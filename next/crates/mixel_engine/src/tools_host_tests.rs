//! Host-side tests of the tools channel over in-memory records (no network): every request, every refusal.
#![allow(unsafe_code)] // signal 0 to test that a shell is gone.
use super::*;
use crate::agent_verify;
use crate::tools_proto::{Incoming, Reader, frame_json, frame_raw, request};
use ed25519_dalek::{Signer, SigningKey};
use signaling_server::protocol::{AgentCommand, CommandSignature, ScriptParam};
use std::sync::atomic::AtomicBool;

pub struct Rig {
    pub to_host: mpsc::Sender<Vec<u8>>,
    pub from_host: mpsc::Receiver<Vec<u8>>,
    reader: Reader,
    pub view: SharedView,
    pub knobs: Arc<Knobs>,
    pub audit: Arc<Mutex<Vec<(String, Value)>>>,
    pub policy: Arc<Mutex<Policy>>,
    pub live: Arc<AtomicBool>,
    pub output: Vec<u8>,
    pub events: Vec<Value>,
    next: u64,
    task: tokio::task::JoinHandle<()>,
}

pub fn all_on() -> Policy {
    Policy { terminal: true, terminal_elevate: true, tasks: true, tasks_unattended: true, scripts: true }
}

pub fn rig(policy: Policy, terminal: bool, tasks: bool, unattended: bool, terminal_only: bool) -> Rig {
    rig_with(policy, terminal, tasks, unattended, terminal_only, false)
}
pub fn rig_with(policy: Policy, terminal: bool, tasks: bool, unattended: bool, terminal_only: bool, privileged: bool) -> Rig {
    let (to_host, host_in) = mpsc::channel(256);
    let (host_out, from_host) = mpsc::channel(256);
    let view: SharedView = Arc::default();
    let knobs = Arc::new(Knobs::default());
    let audit = Arc::new(Mutex::new(Vec::new()));
    let policy = Arc::new(Mutex::new(policy));
    let live = Arc::new(AtomicBool::new(true));
    let cfg = HostConfig {
        session: Uuid::new_v4(),
        grants: Grants { terminal, tasks, unattended, terminal_only, who: "lea".into() },
        policy: {
            let p = policy.clone();
            Arc::new(move || *p.lock().unwrap())
        },
        live: {
            let l = live.clone();
            Arc::new(move || l.load(Ordering::Acquire))
        },
        audit: {
            let a = audit.clone();
            Arc::new(move |kind, data| a.lock().unwrap().push((kind.to_owned(), data)))
        },
        view: view.clone(),
        knobs: knobs.clone(),
        identity: Some((DEVICE.parse().unwrap(), 440_207_203)),
        peer_scripts: true,
        force_privileged: Some(privileged),
    };
    let task = tokio::spawn(run_host(cfg, host_out, host_in));
    Rig { to_host, from_host, reader: Reader::default(), view, knobs, audit, policy, live, output: Vec::new(), events: Vec::new(), next: 1, task }
}

impl Rig {
    /// Reads one record from the host into `output`; returns a JSON message if it was one.
    pub async fn pump(&mut self, wait: Duration) -> Option<Value> {
        let record = tokio::time::timeout(wait, self.from_host.recv()).await.ok()??;
        match self.reader.push(&record).unwrap() {
            Some(Incoming::Out(bytes)) => self.output.extend(bytes),
            Some(Incoming::Json(value)) => return Some(value),
            _ => {}
        }
        Some(Value::Null)
    }
    pub async fn next_json(&mut self, wait: Duration) -> Option<Value> {
        let end = Instant::now() + wait;
        while Instant::now() < end {
            if let Some(value) = self.pump(Duration::from_millis(100)).await
                && !value.is_null()
            {
                return Some(value);
            }
        }
        None
    }
    pub async fn send(&mut self, op: &str, args: Value) -> u64 {
        let id = self.next;
        self.next += 1;
        for record in frame_json(&request(id, op, args)) {
            self.to_host.send(record).await.unwrap();
        }
        id
    }
    pub async fn call(&mut self, op: &str, args: Value) -> Result<Value, crate::tools_proto::ToolError> {
        let id = self.send(op, args).await;
        self.answer(id, Duration::from_secs(20)).await
    }
    pub async fn answer(&mut self, id: u64, wait: Duration) -> Result<Value, crate::tools_proto::ToolError> {
        let end = Instant::now() + wait;
        while Instant::now() < end {
            let Some(value) = self.next_json(Duration::from_millis(500)).await else { continue };
            if value["t"] == "resp" && value["id"] == id {
                return if value["ok"] == true { Ok(value["data"].clone()) } else { Err(serde_json::from_value(value["error"].clone()).unwrap()) };
            }
            self.events.push(value);
        }
        panic!("no answer to request {id}");
    }
    pub async fn type_text(&mut self, text: &str) {
        for record in frame_raw(crate::tools_proto::KIND_IN, text.as_bytes()) {
            self.to_host.send(record).await.unwrap();
        }
    }
    pub async fn wait_output(&mut self, needle: &str, wait: Duration) -> bool {
        let end = Instant::now() + wait;
        while Instant::now() < end {
            if String::from_utf8_lossy(&self.output).contains(needle) {
                return true;
            }
            if let Some(v) = self.pump(Duration::from_millis(100)).await
                && !v.is_null()
            {
                self.events.push(v);
            }
        }
        String::from_utf8_lossy(&self.output).contains(needle)
    }
    pub fn audit_kinds(&self) -> Vec<String> {
        self.audit.lock().unwrap().iter().map(|(k, _)| k.clone()).collect()
    }
    pub fn set_policy(&self, policy: Policy) {
        *self.policy.lock().unwrap() = policy;
    }
}

const DEVICE: &str = "5ceaf4a7-0000-4000-8000-000000000001";

fn code(result: Result<Value, crate::tools_proto::ToolError>) -> String {
    result.map(|_| "ok".to_owned()).unwrap_or_else(|e| e.code)
}

async fn wait_pending(r: &Rig) -> Pending {
    loop {
        if let Some(p) = r.view.lock().unwrap().pending.pop() {
            return p;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

#[tokio::test]
async fn grant_message_reports_each_feature_state() {
    let mut r = rig(Policy::default(), true, true, false, false);
    let grant = r.next_json(Duration::from_secs(3)).await.unwrap();
    assert_eq!(grant["t"], "grant");
    assert_eq!(grant["features"]["terminal"]["state"], "policy_off");
    assert_eq!(grant["features"]["tasks"]["state"], "policy_off");
    assert_eq!(grant["features"]["quality"]["state"], "ready");
    r.set_policy(Policy { terminal: true, tasks: true, ..Default::default() });
    let grant = r.next_json(Duration::from_secs(3)).await.unwrap();
    assert_eq!(grant["features"]["terminal"]["state"], "ready");
    assert_eq!(grant["features"]["terminal_elevate"]["state"], "policy_off");
    let mut r = rig(all_on(), false, false, true, true);
    let grant = r.next_json(Duration::from_secs(3)).await.unwrap();
    assert_eq!(grant["features"]["terminal"]["state"], "not_consented");
    assert_eq!(grant["features"]["quality"]["state"], "not_available", "terminal only: no screen");
    assert_eq!(grant["terminal_only"], true);
}

#[tokio::test]
async fn terminal_needs_policy_and_consent_then_works_and_is_audited() {
    let mut r = rig(Policy::default(), true, false, false, false);
    assert_eq!(code(r.call("term.open", json!({"cols": 80, "rows": 24})).await), "policy_off");
    r.set_policy(all_on());
    let mut no_consent = rig(all_on(), false, false, false, false);
    assert_eq!(code(no_consent.call("term.open", json!({})).await), "not_consented");
    assert_eq!(code(r.call("term.open", json!({"shell": "../../evil"})).await), "bad_shell");
    assert_eq!(code(r.call("term.resize", json!({"cols": 90, "rows": 30})).await), "not_open");
    let opened = r.call("term.open", json!({"shell": "sh", "cols": 80, "rows": 24})).await.unwrap();
    assert_eq!(opened["run_as"], "user");
    assert_eq!(code(r.call("term.open", json!({})).await), "already_open");
    r.type_text("echo hello-$((6*7))\n").await;
    assert!(r.wait_output("hello-42", Duration::from_secs(8)).await, "{}", String::from_utf8_lossy(&r.output));
    r.call("term.resize", json!({"cols": 100, "rows": 30})).await.unwrap();
    r.type_text("stty size\n").await;
    assert!(r.wait_output("30 100", Duration::from_secs(8)).await);
    r.type_text("exit 5\n").await;
    let exit = loop {
        let value = r.next_json(Duration::from_secs(8)).await.expect("term_exit");
        if value["t"] == "term_exit" {
            break value;
        }
    };
    assert_eq!(exit["exit_code"], 5);
    let kinds = r.audit_kinds();
    assert!(kinds.contains(&"terminal.shell".into()) && kinds.contains(&"terminal.exit".into()));
    let commands: Vec<String> = r.audit.lock().unwrap().iter().filter(|(k, _)| k == "terminal.command").map(|(_, d)| d["line"].as_str().unwrap().to_owned()).collect();
    assert!(commands.contains(&"echo hello-$((6*7))".to_owned()) && commands.contains(&"stty size".to_owned()), "{commands:?}");
    let first = r.audit.lock().unwrap().iter().find(|(k, d)| k == "terminal.command" && d["line"].as_str().unwrap().starts_with("echo hello")).unwrap().1.clone();
    assert!(first["output_head"].as_str().unwrap().contains("hello-42"));
    // A new terminal can be opened once the first is gone.
    assert!(r.call("term.open", json!({"shell": "sh"})).await.is_ok());
}

#[tokio::test]
async fn elevation_needs_policy_a_privileged_host_a_reason_and_the_person() {
    // Switch off: refused.
    let mut r = rig(Policy { terminal: true, ..Default::default() }, true, false, false, false);
    let ask = json!({"run_as": "elevated", "reason": "fix the printer queue", "shell": "sh"});
    assert_eq!(code(r.call("term.open", ask.clone()).await), "elevation_policy");
    r.set_policy(all_on());
    assert_eq!(code(r.call("term.open", ask.clone()).await), "elevation_not_available", "this host is not the privileged service");
    let mut r = rig_with(all_on(), true, false, false, false, true);
    assert_eq!(code(r.call("term.open", json!({"run_as": "elevated", "reason": "short"})).await), "reason_required");
    assert_eq!(code(r.call("term.open", json!({"run_as": "elevated"})).await), "reason_required");
    // Attended: the person is asked; a refusal ends it.
    let id = r.send("term.open", ask.clone()).await;
    let pending = wait_pending(&r).await;
    assert_eq!((pending.kind, pending.reason.as_str()), ("elevate", "fix the printer queue"));
    pending.answer.unwrap().send(false).unwrap();
    assert_eq!(r.answer(id, Duration::from_secs(5)).await.unwrap_err().code, "elevation_refused");
    // Attended and accepted.
    let id = r.send("term.open", ask.clone()).await;
    wait_pending(&r).await.answer.unwrap().send(true).unwrap();
    let opened = r.answer(id, Duration::from_secs(5)).await.unwrap();
    assert_eq!(opened["run_as"], "elevated");
    let elevate = r.audit.lock().unwrap().iter().filter(|(k, _)| k == "terminal.elevate").map(|(_, d)| d.clone()).collect::<Vec<_>>();
    assert_eq!(elevate.len(), 2);
    assert_eq!(elevate[0]["result"], "refused");
    assert_eq!(elevate[1]["accepted_by"], "person");
    assert_eq!(elevate[1]["reason"], "fix the printer queue");
    assert!(r.view.lock().unwrap().terminal.as_ref().unwrap()["elevated"] == true, "the person sees the elevated terminal");
    // Unattended: no prompt, the reason is still required.
    let mut u = rig_with(all_on(), true, false, true, false, true);
    assert_eq!(code(u.call("term.open", json!({"run_as": "elevated"})).await), "reason_required");
    assert!(u.call("term.open", ask).await.is_ok());
}

#[tokio::test]
async fn the_terminal_dies_with_the_line_the_channel_the_session_and_the_grant() {
    for how in ["line", "channel", "session", "grant", "stop"] {
        let mut r = rig(all_on(), true, false, false, false);
        r.call("term.open", json!({"shell": "sh"})).await.unwrap();
        r.type_text("echo pid-$$\n").await;
        assert!(r.wait_output("\npid-", Duration::from_secs(8)).await);
        tokio::time::sleep(Duration::from_millis(200)).await;
        let _ = r.pump(Duration::from_millis(100)).await;
        let text = String::from_utf8_lossy(&r.output).into_owned();
        let pid: i32 = text.split("pid-").last().unwrap().trim().lines().next().unwrap().trim().parse().unwrap();
        match how {
            // No record at all from the technician: LINE_LOST (2 s in tests) ends it.
            "line" => {}
            "channel" => {
                let (dead, _) = mpsc::channel(1);
                r.to_host = dead;
            }
            "session" => r.live.store(false, Ordering::Release),
            "grant" => r.set_policy(Policy { tasks: true, ..Default::default() }),
            _ => r.view.lock().unwrap().stop = true,
        }
        let end = Instant::now() + Duration::from_secs(10);
        loop {
            // SAFETY: signal 0 only tests whether the shell still exists.
            let alive = unsafe { libc::kill(pid, 0) } == 0;
            if !alive {
                break;
            }
            assert!(Instant::now() < end, "{how}: the shell must be gone within the limit");
            let _ = r.pump(Duration::from_millis(100)).await;
        }
        let end = Instant::now() + Duration::from_secs(5);
        while !r.audit_kinds().contains(&"terminal.exit".to_owned()) && Instant::now() < end {
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        assert!(r.audit_kinds().contains(&"terminal.exit".to_owned()), "{how}: the end is recorded");
        let _ = tokio::time::timeout(Duration::from_secs(5), r.task).await;
    }
}

#[tokio::test]
async fn terminal_output_arrives_in_order_and_secrets_are_not_recorded() {
    let mut r = rig(all_on(), true, false, false, false);
    r.call("term.open", json!({"shell": "sh"})).await.unwrap();
    r.type_text("i=0; while [ $i -lt 3000 ]; do echo line-$i; i=$((i+1)); done; echo finished-all\n").await;
    assert!(r.wait_output("\r\nfinished-all\r\n", Duration::from_secs(30)).await);
    let text = String::from_utf8_lossy(&r.output).into_owned();
    let mut at = text.find("line-0\r\n").unwrap();
    for n in 1..3000 {
        let next = text[at..].find(&format!("line-{n}\r\n")).unwrap_or_else(|| panic!("line {n} missing or out of order"));
        at += next;
    }
    // A password at a prompt is not recorded.
    r.type_text("printf 'Password: '; read -r x\n").await;
    assert!(r.wait_output("Password: ", Duration::from_secs(8)).await);
    r.type_text("hunter2-secret\n").await;
    r.type_text("echo after\n").await;
    assert!(r.wait_output("after\r\n", Duration::from_secs(8)).await);
    r.call("term.close", json!({})).await.unwrap();
    let all = serde_json::to_string(&*r.audit.lock().unwrap()).unwrap();
    // The shell echoes what is typed at a prompt that does not hide it, so the secret may be in the output
    // head of the previous command, but never as a recorded command line.
    let lines: Vec<String> = r.audit.lock().unwrap().iter().filter(|(k, _)| k == "terminal.command").map(|(_, d)| d["line"].as_str().unwrap().to_owned()).collect();
    assert!(lines.contains(&"[hidden input]".to_owned()) && !lines.iter().any(|l| l.contains("hunter2")), "{all}");
}

#[tokio::test]
async fn tasks_follow_policy_consent_unattended_rules_and_the_protected_list() {
    let mut r = rig(Policy::default(), false, true, false, false);
    assert_eq!(code(r.call("tasks.processes", json!({})).await), "policy_off");
    let mut n = rig(all_on(), false, false, false, false);
    assert_eq!(code(n.call("tasks.summary", json!({})).await), "not_consented");
    // Attended: lists and changes work; the person sees a notice.
    let mut a = rig(Policy { tasks: true, ..Default::default() }, false, true, false, false);
    let list = a.call("tasks.processes", json!({"top": 5, "sort": "memory"})).await.unwrap();
    assert!(list["items"].as_array().unwrap().len() <= 5 && list["total"].as_u64().unwrap() > 3);
    let summary = a.call("tasks.summary", json!({})).await.unwrap();
    assert!(summary["memory"]["total"].as_u64().unwrap() > 0);
    assert!(a.call("tasks.services", json!({})).await.is_ok());
    let own = std::process::id();
    assert_eq!(code(a.call("tasks.end", json!({"pid": own, "name": "x"})).await), "protected");
    assert_eq!(code(a.call("tasks.end", json!({"pid": 1, "name": "launchd"})).await), "protected");
    assert_eq!(code(a.call("tasks.end", json!({"name": "x"})).await), "bad_argument");
    let mut child = std::process::Command::new("sleep").arg("300").spawn().unwrap();
    assert_eq!(code(a.call("tasks.end", json!({"pid": child.id(), "name": "other"})).await), "name_mismatch");
    let done = a.call("tasks.end", json!({"pid": child.id(), "name": "sleep"})).await.unwrap();
    assert_eq!(done["name"], "sleep");
    child.wait().unwrap();
    assert!(a.view.lock().unwrap().tasks.as_ref().unwrap()["last_action"].as_str().unwrap().contains("sleep"));
    let ended: Vec<Value> = a.audit.lock().unwrap().iter().filter(|(k, _)| k == "tasks.end_process").map(|(_, d)| d.clone()).collect();
    assert!(ended.iter().any(|d| d["result"] == "ok" && d["name"] == "sleep"));
    assert!(ended.iter().any(|d| d["result"] == "protected"));
    // Unattended without the switch: look only. With it: a reason is required.
    let mut u = rig(Policy { tasks: true, ..Default::default() }, false, true, true, false);
    assert!(u.call("tasks.processes", json!({"top": 3})).await.is_ok(), "look-only still works");
    assert_eq!(code(u.call("tasks.end", json!({"pid": 999_999, "name": "x", "reason": "cleaning up now"})).await), "unattended_policy");
    assert_eq!(code(u.call("tasks.service", json!({"id": "x.service", "action": "stop"})).await), "unattended_policy");
    u.set_policy(all_on());
    assert_eq!(code(u.call("tasks.end", json!({"pid": 999_999, "name": "x"})).await), "reason_required");
    assert_eq!(code(u.call("tasks.end", json!({"pid": 999_999, "name": "x", "reason": "cleaning up now"})).await), "not_found");
    let mut sleeper = std::process::Command::new("sleep").arg("300").spawn().unwrap();
    assert!(u.call("tasks.end", json!({"pid": sleeper.id(), "name": "sleep", "reason": "stuck job, cleaning up"})).await.is_ok());
    sleeper.wait().unwrap();
    assert_eq!(code(u.call("tasks.service", json!({"id": "bad;id", "action": "stop", "reason": "long enough reason"})).await), "bad_argument");
    assert_eq!(code(u.call("tasks.service", json!({"id": "no-such-svc-xyz", "action": "stop", "reason": "long enough reason"})).await), "not_found");
    assert_eq!(code(u.call("tasks.end", json!({"elevated": true, "pid": 999_999, "name": "x", "reason": "long enough reason"})).await), "elevation_not_available");
}

#[tokio::test]
async fn quality_pointer_and_thumbnails_are_set_without_a_restart_and_not_on_terminal_only() {
    let mut r = rig(Policy::default(), false, false, false, false);
    assert_eq!(r.call("quality.set", json!({"mode": "low"})).await.unwrap()["mode"], "low");
    assert_eq!(r.knobs.quality.load(Ordering::Acquire), QUALITY_LOW);
    r.call("quality.set", json!({"mode": "sharp"})).await.unwrap();
    assert_eq!(r.knobs.quality.load(Ordering::Acquire), QUALITY_SHARP);
    assert_eq!(code(r.call("quality.set", json!({"mode": "ultra"})).await), "bad_argument");
    r.call("pointer.set", json!({"show": true})).await.unwrap();
    assert!(r.knobs.pointer.load(Ordering::Acquire));
    r.call("thumbs.set", json!({"on": true})).await.unwrap();
    assert!(r.knobs.thumbs.load(Ordering::Acquire));
    assert!(r.audit_kinds().contains(&"quality.set".to_owned()));
    let mut t = rig(Policy::default(), true, false, false, true);
    assert_eq!(code(t.call("quality.set", json!({"mode": "low"})).await), "terminal_only_no_screen");
    assert_eq!(code(t.call("bogus.op", json!({})).await), "bad_argument");
}

// ------------------------------------------------------------------ scripts

fn key() -> SigningKey {
    SigningKey::from_bytes(&[9u8; 32])
}
fn install_test_key() {
    *agent_verify::TEST_PINNED.lock().unwrap() = Some(vec![("fe812c12f3ab4ce6".to_owned(), key().verifying_key())]);
}
fn signed(script: &str, params: Vec<ScriptParam>, v2: bool, device: &str, conn: u32, issued: i64) -> AgentCommand {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs() as i64;
    let mut command = AgentCommand {
        id: Uuid::new_v4(),
        shell: "sh".into(),
        script: script.into(),
        timeout_seconds: 10,
        params,
        script_ref: v2.then(|| "flush-dns@3".to_owned()),
        signed: Some(CommandSignature {
            key_id: "fe812c12f3ab4ce6".into(),
            device: device.parse().unwrap(),
            connection_id: conn,
            issued_at: (now + issued) as u64,
            expires_at: (now + issued + 300) as u64,
            nonce: Uuid::new_v4().simple().to_string() + &Uuid::new_v4().simple().to_string(),
            signature: "0".repeat(128),
            scheme: v2.then(|| "v2".to_owned()),
        }),
    };
    let message = agent_verify::signed_message(&command).unwrap_or_default();
    command.signed.as_mut().unwrap().signature = agent_verify::to_hex(&key().sign(message.as_bytes()).to_bytes());
    command
}

#[tokio::test]
async fn signed_scripts_run_and_every_bad_signature_is_refused_and_logged() {
    install_test_key();
    let mut r = rig(all_on(), false, false, true, false);
    let good = signed("echo ran-$MIXEL_PARAM_WHO; echo oops >&2; exit 4", vec![ScriptParam { name: "who".into(), value: "lea".into() }], true, DEVICE, 440_207_203, 0);
    let result = r.call("script.run", json!({"command": good})).await.unwrap();
    assert_eq!((result["exit_code"].as_i64(), result["stdout"].as_str().unwrap().trim(), result["stderr"].as_str().unwrap().trim()), (Some(4), "ran-lea", "oops"));
    assert_eq!(result["timed_out"], false);
    let run = r.audit.lock().unwrap().iter().find(|(k, _)| k == "script.run").unwrap().1.clone();
    assert_eq!((run["result"].as_str().unwrap(), run["script_ref"].as_str().unwrap()), ("failed", "flush-dns@3"));
    assert_eq!(run["sha256"].as_str().unwrap(), agent_verify::script_sha256("echo ran-$MIXEL_PARAM_WHO; echo oops >&2; exit 4"));
    // Replay of the same command: refused.
    assert_eq!(code(r.call("script.run", json!({"command": good})).await), "refused_signature");
    // Wrong device, wrong connection id, old, tampered script, tampered parameters, unsigned, bad signature.
    let wrong_device = signed("echo x", vec![], false, "5ceaf4a7-0000-4000-8000-0000000000ff", 440_207_203, 0);
    let wrong_conn = signed("echo x", vec![], false, DEVICE, 440_207_204, 0);
    let old = signed("echo x", vec![], false, DEVICE, 440_207_203, -4000);
    let mut tampered = signed("echo x", vec![], false, DEVICE, 440_207_203, 0);
    tampered.script = "echo evil".into();
    let mut params_changed = signed("echo x", vec![ScriptParam { name: "a".into(), value: "1".into() }], true, DEVICE, 440_207_203, 0);
    params_changed.params[0].value = "2".into();
    let mut unsigned = signed("echo x", vec![], false, DEVICE, 440_207_203, 0);
    unsigned.signed = None;
    let mut bad_sig = signed("echo x", vec![], false, DEVICE, 440_207_203, 0);
    bad_sig.signed.as_mut().unwrap().signature = "ab".repeat(64);
    let mut params_without_v2 = signed("echo x", vec![], false, DEVICE, 440_207_203, 0);
    params_without_v2.params = vec![ScriptParam { name: "a".into(), value: "1".into() }];
    let bad_name = signed("echo x", vec![ScriptParam { name: "1bad name".into(), value: "1".into() }], true, DEVICE, 440_207_203, 0);
    let cases = [
        ("wrong device", wrong_device),
        ("wrong connection", wrong_conn),
        ("old", old),
        ("tampered script", tampered),
        ("tampered params", params_changed),
        ("unsigned", unsigned),
        ("bad signature", bad_sig),
        ("params without v2", params_without_v2),
        ("bad param name", bad_name),
    ];
    let count = cases.len() + 1;
    for (name, command) in cases {
        assert_eq!(code(r.call("script.run", json!({"command": command})).await), "refused_signature", "{name}");
    }
    let refused = r.audit_kinds().iter().filter(|k| *k == "script.refused").count();
    assert_eq!(refused, count, "each refusal is on record (the replay too)");
    // Policy off.
    r.set_policy(Policy { scripts: false, ..all_on() });
    assert_eq!(code(r.call("script.run", json!({"command": signed("echo x", vec![], false, DEVICE, 440_207_203, 0)})).await), "script_policy");
    r.set_policy(all_on());
    assert_eq!(code(r.call("script.run", json!({"command": "nonsense"})).await), "bad_argument");
}

#[tokio::test]
async fn an_attended_script_run_asks_the_person_first() {
    install_test_key();
    let mut r = rig(all_on(), false, false, false, false);
    let command = signed("echo attended-ok", vec![], true, DEVICE, 440_207_203, 0);
    let id = r.send("script.run", json!({"command": command})).await;
    let pending = wait_pending(&r).await;
    assert_eq!((pending.kind, pending.script_ref.as_deref()), ("script", Some("flush-dns@3")));
    pending.answer.unwrap().send(true).unwrap();
    let result = r.answer(id, Duration::from_secs(10)).await.unwrap();
    assert_eq!(result["stdout"].as_str().unwrap().trim(), "attended-ok");
    let command = signed("echo no", vec![], true, DEVICE, 440_207_203, 0);
    let id = r.send("script.run", json!({"command": command})).await;
    wait_pending(&r).await.answer.unwrap().send(false).unwrap();
    assert_eq!(r.answer(id, Duration::from_secs(10)).await.unwrap_err().code, "elevation_refused");
}
