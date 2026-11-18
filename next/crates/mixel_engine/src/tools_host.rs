//! Host side of the remote tools channel: answers the technician's requests (terminal, task manager, script
//! run, quality) after checking, for every single request, that the company policy allows it, the session's
//! consent covers it and the rules of the feature hold. Works on plain records (`mpsc`), so the same code runs
//! over the sealed data channel and in unit tests.
use crate::{
    host_pty::{self, Pty, Spec},
    host_tasks::Tasks,
    tools_policy::Policy,
    tools_proto::{self as proto, Incoming, Reader, ToolResult, err},
};
use serde_json::{Value, json};
use std::{
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU8, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::sync::{mpsc, oneshot};
use uuid::Uuid;

pub type Live = Arc<dyn Fn() -> bool + Send + Sync>;
pub type PolicyFn = Arc<dyn Fn() -> Policy + Send + Sync>;
pub type AuditFn = Arc<dyn Fn(&str, Value) + Send + Sync>;

/// Picture-quality modes (technician's choice, applied by the host's video loop).
pub const QUALITY_AUTO: u8 = 0;
pub const QUALITY_SHARP: u8 = 1;
pub const QUALITY_SMOOTH: u8 = 2;
pub const QUALITY_LOW: u8 = 3;
pub fn quality_name(mode: u8) -> &'static str {
    ["auto", "sharp", "smooth", "low"].get(usize::from(mode)).copied().unwrap_or("auto")
}

/// Switches the host's media loop reads (set by `quality.set`, `pointer.set`, `thumbs.set`).
#[derive(Default)]
pub struct Knobs {
    pub quality: AtomicU8,
    pub pointer: AtomicBool,
    pub thumbs: AtomicBool,
}

/// An approval the person at the computer is asked for (attended elevation, attended script run).
pub struct Pending {
    pub id: Uuid,
    pub kind: &'static str,
    pub who: String,
    pub reason: String,
    pub script_ref: Option<String>,
    pub sha256: Option<String>,
    pub since: u64,
    pub answer: Option<oneshot::Sender<bool>>,
}

/// What the person at the computer can see and answer (read by `remote_tools_status` on the host).
#[derive(Default)]
pub struct HostView {
    pub pending: Vec<Pending>,
    pub terminal: Option<Value>,
    pub tasks: Option<Value>,
    pub script: Option<Value>,
    pub stop: bool,
}
pub type SharedView = Arc<Mutex<HostView>>;

pub struct Grants {
    pub terminal: bool,
    pub tasks: bool,
    pub unattended: bool,
    pub terminal_only: bool,
    pub who: String,
}

pub struct HostConfig {
    pub session: Uuid,
    pub grants: Grants,
    pub policy: PolicyFn,
    pub live: Live,
    pub audit: AuditFn,
    pub view: SharedView,
    pub knobs: Arc<Knobs>,
    /// This computer's device id and Remote ID (what script signatures must name).
    pub identity: Option<(Uuid, u32)>,
    /// Peers that announced script runs (the technician side's features).
    pub peer_scripts: bool,
    /// Tests: pretend to be (or not to be) the privileged service.
    pub force_privileged: Option<bool>,
}
impl HostConfig {
    pub fn privileged(&self) -> bool {
        self.force_privileged.unwrap_or_else(host_pty::privileged)
    }
}

fn now() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}
const APPROVAL: Duration = Duration::from_secs(60);
#[cfg(not(test))]
const LINE_LOST: Duration = Duration::from_secs(10);
#[cfg(test)]
const LINE_LOST: Duration = Duration::from_secs(2);

// ------------------------------------------------------------------ command recording

/// Rebuilds the lines a technician typed from the key stream, to record them (never the secrets typed at
/// password prompts), and keeps the first 4 kB of output of each command.
#[derive(Default)]
pub struct Recorder {
    line: Vec<char>,
    approximate: bool,
    escape: u8,
    hidden: bool,
    tail: Vec<u8>,
    current: Option<Command>,
}
pub struct Command {
    pub line: String,
    pub approximate: bool,
    pub hidden: bool,
    pub at: u64,
    pub head: Vec<u8>,
}
impl Recorder {
    /// Feeds typed bytes; returns the commands completed by this input.
    pub fn typed(&mut self, data: &[u8]) -> Vec<Command> {
        let mut done = Vec::new();
        for ch in String::from_utf8_lossy(data).chars() {
            if self.escape > 0 {
                // ESC [ ... final byte, or ESC x.
                if self.escape == 1 && ch != '[' && ch != 'O' {
                    self.escape = 0;
                } else if self.escape >= 2 && ('@'..='~').contains(&ch) {
                    self.escape = 0;
                } else {
                    self.escape = 2;
                }
                continue;
            }
            match ch {
                '\u{1b}' => {
                    self.escape = 1;
                    self.approximate = true;
                }
                '\r' | '\n' => {
                    let line: String = self.line.drain(..).collect();
                    if !line.trim().is_empty() || self.hidden {
                        if let Some(previous) = self.current.take() {
                            done.push(previous);
                        }
                        self.current = Some(Command {
                            line: if self.hidden { "[hidden input]".into() } else { line.chars().take(1024).collect() },
                            approximate: self.approximate,
                            hidden: self.hidden,
                            at: now(),
                            head: Vec::new(),
                        });
                    }
                    self.approximate = false;
                    self.hidden = false;
                }
                '\u{7f}' | '\u{8}' => {
                    self.line.pop();
                }
                '\u{3}' | '\u{15}' => self.line.clear(),
                '\t' => self.approximate = true,
                c if c.is_control() => {}
                c => {
                    if self.line.is_empty() {
                        let tail = String::from_utf8_lossy(&self.tail).to_lowercase();
                        self.hidden = tail.contains("password") || tail.contains("passphrase") || tail.contains("passcode") || tail.contains("secret:");
                    }
                    self.line.push(c);
                }
            }
        }
        done
    }
    /// Feeds shell output: the head of the current command's output.
    pub fn output(&mut self, data: &[u8]) {
        if let Some(command) = &mut self.current
            && command.head.len() < 4096
        {
            let room = 4096 - command.head.len();
            command.head.extend_from_slice(&data[..data.len().min(room)]);
        }
        self.tail.extend_from_slice(data);
        if self.tail.len() > 96 {
            self.tail.drain(..self.tail.len() - 96);
        }
    }
    pub fn finish(&mut self) -> Option<Command> {
        self.current.take()
    }
}

// ------------------------------------------------------------------ the loop

struct Term {
    pty: Arc<Pty>,
    elevated: bool,
    since: Instant,
    recorder: Recorder,
    id: u32,
}

enum Internal {
    Resp(Value),
    OpenApproved { id: u64, args: OpenArgs },
    TermExit { id: u32, code: Option<i32> },
    Output { id: u32, data: Vec<u8> },
}

#[derive(Clone)]
struct OpenArgs {
    shell: Option<String>,
    elevated: bool,
    reason: Option<String>,
    cols: u16,
    rows: u16,
}

pub async fn run_host(cfg: HostConfig, tx: mpsc::Sender<Vec<u8>>, mut rx: mpsc::Receiver<Vec<u8>>) {
    let cfg = Arc::new(cfg);
    let (internal_tx, mut internal) = mpsc::channel::<Internal>(64);
    let tasks = Arc::new(Mutex::new(Tasks::default()));
    let mut reader = Reader::default();
    let mut term: Option<Term> = None;
    let mut next_term = 1u32;
    let mut last_rx = Instant::now();
    let mut sent_policy = (cfg.policy)();
    send_json(&tx, &grant_message(&cfg, sent_policy)).await;
    let mut ticker = tokio::time::interval(Duration::from_millis(250));
    let mut last_tasks_audit = Instant::now() - Duration::from_secs(120);
    let mut gone = "session_end";
    loop {
        tokio::select! {
            _ = ticker.tick() => {
                if !(cfg.live)() { break; }
                let stop = cfg.view.lock().map(|mut v| std::mem::take(&mut v.stop)).unwrap_or(false);
                if stop && term.is_some() {
                    end_terminal(&cfg, &tx, &mut term, "stopped_by_person").await;
                }
                let policy = (cfg.policy)();
                if policy != sent_policy {
                    sent_policy = policy;
                    send_json(&tx, &grant_message(&cfg, policy)).await;
                    // The grant ended: the terminal ends with it.
                    if term.is_some() && !(policy.terminal) {
                        end_terminal(&cfg, &tx, &mut term, "grant_end").await;
                    }
                }
                if term.as_ref().is_some_and(|_| last_rx.elapsed() > LINE_LOST) {
                    gone = "line_lost";
                    end_terminal(&cfg, &tx, &mut term, "line_lost").await;
                }
            }
            record = rx.recv() => {
                let Some(record) = record else { gone = "channel_closed"; break; };
                last_rx = Instant::now();
                match reader.push(&record) {
                    Ok(Some(Incoming::In(bytes))) => {
                        if let Some(active) = &mut term {
                            let finished = active.recorder.typed(&bytes);
                            for command in finished { audit_command(&cfg, active, command); }
                            let _ = active.pty.write(&bytes);
                        }
                    }
                    Ok(Some(Incoming::Json(value))) => {
                        let kind = value.get("t").and_then(Value::as_str).unwrap_or("");
                        if kind == "req" {
                            let id = value.get("id").and_then(Value::as_u64).unwrap_or(0);
                            let op = value.get("op").and_then(Value::as_str).unwrap_or("").to_owned();
                            let args = value.get("args").cloned().unwrap_or(Value::Null);
                            handle(&cfg, &tx, &internal_tx, &tasks, &mut term, &mut next_term, &mut last_tasks_audit, id, &op, args).await;
                        }
                    }
                    Ok(_) => {}
                    Err(_) => { gone = "bad_record"; break; }
                }
            }
            event = internal.recv() => {
                let Some(event) = event else { break };
                match event {
                    Internal::Resp(value) => send_json(&tx, &value).await,
                    Internal::OpenApproved { id, args } => {
                        let reply = match open_terminal(&cfg, &internal_tx, &mut term, &mut next_term, args, true) {
                            Ok(data) => proto::response_ok(id, data),
                            Err(error) => proto::response_err(id, &error),
                        };
                        send_json(&tx, &reply).await;
                    }
                    Internal::Output { id, data } => {
                        if let Some(active) = &mut term && active.id == id {
                            active.recorder.output(&data);
                            for part in proto::frame_raw(proto::KIND_OUT, &data) {
                                if tx.send(part).await.is_err() { break; }
                            }
                        }
                    }
                    Internal::TermExit { id, code } => {
                        if term.as_ref().is_some_and(|active| active.id == id) {
                            let reason = "exit";
                            finish_terminal(&cfg, &tx, &mut term, code, reason).await;
                        }
                    }
                }
            }
        }
    }
    if term.is_some() {
        end_terminal(&cfg, &tx, &mut term, gone).await;
    }
}

async fn send_json(tx: &mpsc::Sender<Vec<u8>>, value: &Value) {
    for record in proto::frame_json(value) {
        if tx.send(record).await.is_err() {
            return;
        }
    }
}

fn audit_command(cfg: &HostConfig, term: &Term, command: Command) {
    (cfg.audit)(
        "terminal.command",
        json!({"line": command.line, "approximate": command.approximate, "hidden": command.hidden, "run_as": if term.elevated {"elevated"} else {"user"},
               "exit_code": Value::Null, "output_head": String::from_utf8_lossy(&command.head), "at": command.at}),
    );
}

/// Ends the shell now and reports why.
async fn end_terminal(cfg: &Arc<HostConfig>, tx: &mpsc::Sender<Vec<u8>>, term: &mut Option<Term>, reason: &str) {
    if let Some(active) = term.as_ref() {
        active.pty.kill();
        // Wait briefly for the exit code so the record has it.
        let end = Instant::now() + Duration::from_millis(1500);
        while active.pty.exit_code().is_none() && Instant::now() < end {
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
    }
    let code = term.as_ref().and_then(|active| active.pty.exit_code());
    finish_terminal(cfg, tx, term, code, reason).await;
}

async fn finish_terminal(cfg: &Arc<HostConfig>, tx: &mpsc::Sender<Vec<u8>>, term: &mut Option<Term>, code: Option<i32>, reason: &str) {
    let Some(mut active) = term.take() else { return };
    if let Some(command) = active.recorder.finish() {
        audit_command(cfg, &active, command);
    }
    (cfg.audit)("terminal.exit", json!({"exit_code": code, "duration_s": active.since.elapsed().as_secs(), "reason": reason}));
    if let Ok(mut view) = cfg.view.lock() {
        view.terminal = None;
    }
    send_json(tx, &json!({"t": "term_exit", "terminal": active.id, "exit_code": code, "reason": reason})).await;
}

fn state_of(ok: bool, off: &'static str) -> Value {
    json!({"state": if ok { "ready" } else { off }, "reason": ""})
}

/// The announcement of what works, per feature (contract section 6, `F`).
fn grant_message(cfg: &HostConfig, policy: Policy) -> Value {
    let shells = host_pty::shells();
    let term_state = if !policy.terminal {
        "policy_off"
    } else if !cfg.grants.terminal {
        "not_consented"
    } else if shells.is_empty() {
        "not_available"
    } else {
        "ready"
    };
    let elevate = if !policy.terminal_elevate {
        "policy_off"
    } else if !cfg.privileged() {
        "not_available"
    } else if term_state != "ready" {
        term_state
    } else {
        "ready"
    };
    let tasks = if !policy.tasks {
        "policy_off"
    } else if !cfg.grants.tasks {
        "not_consented"
    } else {
        "ready"
    };
    let change = if tasks != "ready" {
        tasks
    } else if cfg.grants.unattended && !policy.tasks_unattended {
        "unattended_policy"
    } else {
        "ready"
    };
    let screen = !cfg.grants.terminal_only;
    json!({
        "t": "grant", "v": 1,
        "features": {
            "terminal": {"state": term_state, "reason": ""},
            "terminal_elevate": {"state": elevate, "reason": ""},
            "tasks": {"state": tasks, "reason": ""},
            "tasks_change": {"state": change, "reason": ""},
            "quality": state_of(screen, "not_available"),
            "thumbs": state_of(screen, "not_available"),
            "scripts": state_of(policy.scripts && cfg.peer_scripts, if cfg.peer_scripts { "policy_off" } else { "needs_2_2" }),
        },
        "shells": shells.iter().map(|s| s.name).collect::<Vec<_>>(),
        "unattended": cfg.grants.unattended, "terminal_only": cfg.grants.terminal_only,
    })
}

fn need_terminal(cfg: &HostConfig) -> ToolResult<()> {
    let policy = (cfg.policy)();
    if !policy.terminal {
        return Err(err("terminal", "policy_off", "The remote terminal is not allowed by company policy"));
    }
    if !cfg.grants.terminal {
        return Err(err("terminal", "not_consented", "The person at the computer did not allow a terminal"));
    }
    Ok(())
}
fn need_tasks(cfg: &HostConfig) -> ToolResult<()> {
    let policy = (cfg.policy)();
    if !policy.tasks {
        return Err(err("tasks", "policy_off", "The task manager is not allowed by company policy"));
    }
    if !cfg.grants.tasks {
        return Err(err("tasks", "not_consented", "The person at the computer did not allow the task manager"));
    }
    Ok(())
}

fn arg_str(args: &Value, name: &str) -> Option<String> {
    args.get(name).and_then(Value::as_str).map(str::to_owned)
}
fn arg_u64(args: &Value, name: &str) -> Option<u64> {
    args.get(name).and_then(Value::as_u64)
}

#[allow(clippy::too_many_arguments)]
async fn handle(
    cfg: &Arc<HostConfig>,
    tx: &mpsc::Sender<Vec<u8>>,
    internal: &mpsc::Sender<Internal>,
    tasks: &Arc<Mutex<Tasks>>,
    term: &mut Option<Term>,
    next_term: &mut u32,
    last_tasks_audit: &mut Instant,
    id: u64,
    op: &str,
    args: Value,
) {
    let reply = |result: ToolResult<Value>| match result {
        Ok(data) => proto::response_ok(id, data),
        Err(error) => proto::response_err(id, &error),
    };
    match op {
        "ping" => send_json(tx, &proto::response_ok(id, json!({}))).await,
        "term.open" => {
            let open = OpenArgs {
                shell: arg_str(&args, "shell"),
                elevated: args.get("run_as").and_then(Value::as_str) == Some("elevated"),
                reason: arg_str(&args, "reason"),
                cols: arg_u64(&args, "cols").unwrap_or(80).min(500) as u16,
                rows: arg_u64(&args, "rows").unwrap_or(24).min(200) as u16,
            };
            let result = (|| {
                need_terminal(cfg)?;
                if term.is_some() {
                    return Err(err("terminal", "already_open", "A terminal is already open in this session"));
                }
                if open.elevated {
                    check_elevation(cfg, &open)?;
                    if !cfg.grants.unattended {
                        // Attended: the person decides first. Answered later through `Internal::OpenApproved`.
                        return Ok(None);
                    }
                }
                open_terminal(cfg, internal, term, next_term, open.clone(), open.elevated).map(Some)
            })();
            match result {
                Ok(Some(data)) => send_json(tx, &proto::response_ok(id, data)).await,
                Ok(None) => ask_person(cfg, internal.clone(), id, open),
                Err(error) => send_json(tx, &proto::response_err(id, &error)).await,
            }
        }
        "term.resize" => {
            let result = term.as_ref().ok_or_else(|| err("terminal", "not_open", "No terminal is open")).map(|active| {
                active.pty.resize(arg_u64(&args, "cols").unwrap_or(80).min(500) as u16, arg_u64(&args, "rows").unwrap_or(24).min(200) as u16);
                json!({})
            });
            send_json(tx, &reply(result)).await;
        }
        "term.close" => {
            if term.is_none() {
                send_json(tx, &reply(Err(err("terminal", "not_open", "No terminal is open")))).await;
            } else {
                end_terminal(cfg, tx, term, "closed").await;
                send_json(tx, &proto::response_ok(id, json!({}))).await;
            }
        }
        "tasks.summary" | "tasks.processes" | "tasks.end" | "tasks.services" | "tasks.service" => {
            let result = tasks_request(cfg, tasks, last_tasks_audit, op, &args).await;
            send_json(tx, &reply(result)).await;
        }
        "quality.set" | "pointer.set" | "thumbs.set" => {
            let result = (|| {
                if cfg.grants.terminal_only {
                    return Err(err("quality", "terminal_only_no_screen", "This session shares no screen"));
                }
                match op {
                    "quality.set" => {
                        let mode = match args.get("mode").and_then(Value::as_str) {
                            Some("auto") => QUALITY_AUTO,
                            Some("sharp") => QUALITY_SHARP,
                            Some("smooth") => QUALITY_SMOOTH,
                            Some("low") => QUALITY_LOW,
                            _ => return Err(err("quality", "bad_argument", "Unknown quality mode")),
                        };
                        cfg.knobs.quality.store(mode, Ordering::Release);
                        (cfg.audit)("quality.set", json!({"mode": quality_name(mode)}));
                        Ok(json!({"mode": quality_name(mode)}))
                    }
                    "pointer.set" => {
                        let show = args.get("show").and_then(Value::as_bool).unwrap_or(false);
                        cfg.knobs.pointer.store(show, Ordering::Release);
                        Ok(json!({"show": show}))
                    }
                    _ => {
                        let on = args.get("on").and_then(Value::as_bool).unwrap_or(false);
                        cfg.knobs.thumbs.store(on, Ordering::Release);
                        Ok(json!({"on": on}))
                    }
                }
            })();
            send_json(tx, &reply(result)).await;
        }
        "script.run" => {
            let cfg = cfg.clone();
            let internal = internal.clone();
            tokio::spawn(async move {
                let value = match crate::tools_scripts::host_run(&cfg, args).await {
                    Ok(data) => proto::response_ok(id, data),
                    Err(error) => proto::response_err(id, &error),
                };
                let _ = internal.send(Internal::Resp(value)).await;
            });
        }
        _ => send_json(tx, &reply(Err(err("terminal", "bad_argument", "Unknown request")))).await,
    }
}

fn check_elevation(cfg: &HostConfig, open: &OpenArgs) -> ToolResult<()> {
    let policy = (cfg.policy)();
    if !policy.terminal_elevate {
        return Err(err("terminal", "elevation_policy", "An elevated terminal is not allowed by company policy"));
    }
    if !cfg.privileged() {
        return Err(err("terminal", "elevation_not_available", "An elevated terminal is not available on this computer"));
    }
    if !proto::valid_reason(open.reason.as_deref()) {
        return Err(err("terminal", "reason_required", "Give a reason of at least 8 characters"));
    }
    Ok(())
}

fn ask_person(cfg: &Arc<HostConfig>, internal: mpsc::Sender<Internal>, id: u64, open: OpenArgs) {
    let (answer, decided) = oneshot::channel();
    let pending_id = Uuid::new_v4();
    if let Ok(mut view) = cfg.view.lock() {
        view.pending.push(Pending {
            id: pending_id,
            kind: "elevate",
            who: cfg.grants.who.clone(),
            reason: open.reason.clone().unwrap_or_default(),
            script_ref: None,
            sha256: None,
            since: now(),
            answer: Some(answer),
        });
    }
    let cfg = cfg.clone();
    tokio::spawn(async move {
        let accepted = matches!(tokio::time::timeout(APPROVAL, decided).await, Ok(Ok(true)));
        if let Ok(mut view) = cfg.view.lock() {
            view.pending.retain(|p| p.id != pending_id);
        }
        if accepted {
            let _ = internal.send(Internal::OpenApproved { id, args: open }).await;
        } else {
            (cfg.audit)("terminal.elevate", json!({"reason": open.reason, "who": cfg.grants.who, "accepted_by": "person", "result": "refused"}));
            let error = err("terminal", "elevation_refused", "The person at the computer did not allow it");
            let _ = internal.send(Internal::Resp(proto::response_err(id, &error))).await;
        }
    });
}

fn open_terminal(
    cfg: &Arc<HostConfig>,
    internal: &mpsc::Sender<Internal>,
    term: &mut Option<Term>,
    next_term: &mut u32,
    open: OpenArgs,
    after_approval: bool,
) -> ToolResult<Value> {
    // Re-checked here too: the approval can arrive after the policy changed.
    need_terminal(cfg)?;
    if term.is_some() {
        return Err(err("terminal", "already_open", "A terminal is already open in this session"));
    }
    if open.elevated {
        check_elevation(cfg, &open)?;
    }
    let shell = host_pty::pick_shell(open.shell.as_deref())?;
    let user = if open.elevated {
        None
    } else {
        let user = host_pty::console_user();
        if user.is_none() {
            return Err(err("terminal", "no_user_session", "Nobody is logged on to run the terminal as"));
        }
        user
    };
    let (pty, output) = Pty::spawn(&Spec { shell: shell.clone(), elevated: open.elevated, cols: open.cols, rows: open.rows }, user.as_ref())?;
    let pty = Arc::new(pty);
    let id = *next_term;
    *next_term += 1;
    let name = user.as_ref().map_or("root".to_owned(), |u| u.name.clone());
    let reason = open.reason.clone().unwrap_or_default();
    let started = now();
    // Output bridge: the blocking reader hands chunks over; a full queue stops the shell (back-pressure).
    let sender = internal.clone();
    let waiter = pty.clone();
    std::thread::spawn(move || {
        while let Ok(data) = output.recv() {
            if sender.blocking_send(Internal::Output { id, data }).is_err() {
                return;
            }
        }
        let end = Instant::now() + Duration::from_secs(3);
        while waiter.exit_code().is_none() && Instant::now() < end {
            std::thread::sleep(Duration::from_millis(20));
        }
        let _ = sender.blocking_send(Internal::TermExit { id, code: waiter.exit_code() });
    });
    if open.elevated {
        (cfg.audit)("terminal.elevate", json!({"reason": reason, "who": cfg.grants.who, "accepted_by": if after_approval { "person" } else { "policy" }, "result": "ok"}));
    }
    (cfg.audit)("terminal.shell", json!({"shell": shell.name, "run_as": if open.elevated {"elevated"} else {"user"}, "user": name, "cols": open.cols, "rows": open.rows}));
    if let Ok(mut view) = cfg.view.lock() {
        view.terminal = Some(json!({"open": true, "elevated": open.elevated, "shell": shell.name, "reason": reason, "since": started, "who": cfg.grants.who}));
    }
    *term = Some(Term { pty, elevated: open.elevated, since: Instant::now(), recorder: Recorder::default(), id });
    Ok(json!({"terminal": id, "shell": shell.name, "run_as": if open.elevated {"elevated"} else {"user"}, "user": name, "started_at": started,
              "shells": host_pty::shells().iter().map(|s| s.name).collect::<Vec<_>>()}))
}

async fn tasks_request(cfg: &Arc<HostConfig>, tasks: &Arc<Mutex<Tasks>>, last_audit: &mut Instant, op: &str, args: &Value) -> ToolResult<Value> {
    need_tasks(cfg)?;
    let change = matches!(op, "tasks.end") || (op == "tasks.service" && matches!(args.get("action").and_then(Value::as_str), Some("start" | "stop" | "restart")));
    let elevated = args.get("elevated").and_then(Value::as_bool).unwrap_or(false);
    let reason = arg_str(args, "reason");
    if change {
        let policy = (cfg.policy)();
        if cfg.grants.unattended && !policy.tasks_unattended {
            return Err(err("tasks", "unattended_policy", "Changes when nobody is at the computer are not allowed by company policy"));
        }
        if elevated && !cfg.privileged() {
            return Err(err("tasks", "elevation_not_available", "Acting as administrator is not available on this computer"));
        }
        // Without a person at the computer, and whenever the host acts with full rights, a reason is on record.
        if (cfg.grants.unattended || elevated || cfg.privileged()) && !proto::valid_reason(reason.as_deref()) {
            return Err(err("tasks", "reason_required", "Give a reason of at least 8 characters"));
        }
    } else if last_audit.elapsed() > Duration::from_secs(60) {
        *last_audit = Instant::now();
        (cfg.audit)("tasks.view", json!({"what": op.trim_start_matches("tasks.")}));
    }
    let tasks = tasks.clone();
    let op = op.to_owned();
    let args = args.clone();
    let cfg2 = cfg.clone();
    let outcome = tokio::task::spawn_blocking(move || {
        let mut guard = tasks.lock().map_err(|_| err("tasks", "busy", "Busy"))?;
        match op.as_str() {
            "tasks.summary" => crate::host_tasks::summary(),
            "tasks.processes" => guard.processes(
                arg_u64(&args, "top").unwrap_or(50) as usize,
                arg_u64(&args, "offset").unwrap_or(0) as usize,
                &arg_str(&args, "filter").unwrap_or_default(),
                &arg_str(&args, "sort").unwrap_or_else(|| "cpu".into()),
            ),
            "tasks.end" => {
                let pid = arg_u64(&args, "pid").and_then(|p| u32::try_from(p).ok()).ok_or_else(|| err("tasks", "bad_argument", "Missing pid"))?;
                let name = arg_str(&args, "name").ok_or_else(|| err("tasks", "bad_argument", "Missing name"))?;
                let result = guard.end_process(pid, &name);
                let fields = |result: &str, user: &str| json!({"pid": pid, "name": name, "user": user, "reason": arg_str(&args, "reason"), "result": result, "elevated": elevated_flag(&args)});
                match &result {
                    Ok(done) => (cfg2.audit)("tasks.end_process", fields("ok", done["user"].as_str().unwrap_or(""))),
                    Err(error) => (cfg2.audit)("tasks.end_process", fields(&error.code, "")),
                }
                result
            }
            "tasks.services" => guard.services(&arg_str(&args, "filter").unwrap_or_default()),
            _ => {
                let id = arg_str(&args, "id").unwrap_or_default();
                let action = arg_str(&args, "action").unwrap_or_default();
                let result = guard.service_action(&id, &action);
                let code = result.as_ref().map_or_else(|e| e.code.clone(), |_| "ok".into());
                (cfg2.audit)("tasks.service", json!({"id": id, "action": action, "reason": arg_str(&args, "reason"), "result": code, "elevated": elevated_flag(&args)}));
                result
            }
        }
    })
    .await
    .map_err(|_| err("tasks", "busy", "The task manager stopped"))??;
    if change && let Ok(mut view) = cfg.view.lock() {
        view.tasks = Some(json!({"last_action": op_label(&outcome), "who": cfg.grants.who, "at": now()}));
    }
    Ok(outcome)
}
fn elevated_flag(args: &Value) -> bool {
    args.get("elevated").and_then(Value::as_bool).unwrap_or(false)
}
fn op_label(outcome: &Value) -> String {
    if outcome.get("pid").is_some() { format!("ended {}", outcome["name"].as_str().unwrap_or("a process")) } else { format!("changed service {}", outcome["id"].as_str().unwrap_or("")) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn typed_lines_are_rebuilt_and_secrets_at_prompts_are_not_recorded() {
        let mut rec = Recorder::default();
        assert!(rec.typed(b"ls -la").is_empty());
        assert!(rec.typed(b"\n").is_empty(), "a command completes when the next one starts");
        rec.output(b"total 8\n");
        let done = rec.typed(b"echo xx\x7f\x7fhi\r");
        assert_eq!(done.len(), 1);
        assert_eq!(done[0].line, "ls -la");
        assert_eq!(done[0].head, b"total 8\n");
        let last = rec.finish().unwrap();
        assert_eq!(last.line, "echo hi");
        // A password typed after a prompt is never recorded.
        rec.output(b"[sudo] password for lea: ");
        rec.typed(b"hunter2\r");
        let secret = rec.finish().unwrap();
        assert!(secret.hidden && secret.line == "[hidden input]" && !secret.line.contains("hunter2"));
        // History keys mark the line approximate.
        let mut rec = Recorder::default();
        rec.typed(b"\x1b[A\r");
        rec.typed(b"x\r");
        let _ = rec;
        let mut rec = Recorder::default();
        rec.typed(b"\x1b[Aok\r");
        assert!(rec.finish().unwrap().approximate);
    }
    #[test]
    fn output_head_is_capped_at_4_kb() {
        let mut rec = Recorder::default();
        rec.typed(b"yes\r");
        for _ in 0..10 {
            rec.output(&[b'y'; 1000]);
        }
        assert_eq!(rec.finish().unwrap().head.len(), 4096);
    }
}

#[cfg(test)]
#[path = "tools_host_tests.rs"]
mod tools_host_tests;
