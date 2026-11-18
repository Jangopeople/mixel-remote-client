//! Running a signed library script: in a session (over the tools channel) and for a fleet run (the agent path).
//! The host verifies the signature (pinned console key, this device, this connection id, fresh, never replayed,
//! parameters and script reference covered by a `v2` signature), checks the company policy, asks the person
//! first when the session is attended, and records every run and every refusal.
use crate::{
    agent_verify,
    tools_host::{HostConfig, Pending},
    tools_proto::{ToolResult, err},
};
use serde_json::{Value, json};
use signaling_server::protocol::{AgentCommand, ScriptParam};
use std::{
    process::Command,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::oneshot;
use uuid::Uuid;

/// Each of stdout and stderr is kept up to this many bytes.
pub const STREAM_LIMIT: usize = 8 * 1024;

pub struct Outcome {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
    pub duration_ms: u64,
    pub truncated: bool,
    pub timed_out: bool,
}

fn cut(bytes: &[u8]) -> (String, bool) {
    let mut text = String::from_utf8_lossy(bytes).into_owned();
    if text.len() <= STREAM_LIMIT {
        return (text, false);
    }
    let mut end = STREAM_LIMIT;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    text.truncate(end);
    text.push_str("\n[output shortened]");
    (text, true)
}

/// Environment variable for a parameter; names starting `SECRET_` are masked in records (see `masked`).
pub fn env_name(name: &str) -> String {
    format!("MIXEL_PARAM_{}", name.to_uppercase())
}
/// Parameters for a record: values of `SECRET_*` parameters are replaced.
pub fn masked(params: &[ScriptParam]) -> Value {
    Value::Array(
        params
            .iter()
            .map(|p| json!({"name": p.name, "value": if p.name.to_uppercase().starts_with("SECRET_") { "[masked]" } else { p.value.as_str() }}))
            .collect(),
    )
}

/// Runs the script with the platform shell; stdout and stderr apart; parameters as environment and arguments.
pub fn run_split(shell: &str, script: &str, params: &[ScriptParam], timeout: Duration) -> Outcome {
    let started = Instant::now();
    let fail = |message: &str| Outcome { exit_code: -1, stdout: String::new(), stderr: message.into(), duration_ms: 0, truncated: false, timed_out: false };
    let Ok(dir) = tempfile::tempdir() else { return fail("Cannot prepare the script") };
    let (out_path, err_path) = (dir.path().join("out.txt"), dir.path().join("err.txt"));
    let (Ok(out), Ok(error)) = (std::fs::File::create(&out_path), std::fs::File::create(&err_path)) else { return fail("Cannot prepare the script") };
    let mut command;
    if shell == "powershell" {
        let file = dir.path().join("script.ps1");
        let mut bytes = vec![0xef, 0xbb, 0xbf];
        bytes.extend_from_slice(b"[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8\r\n");
        bytes.extend_from_slice(script.as_bytes());
        if std::fs::write(&file, bytes).is_err() {
            return fail("Cannot prepare the script");
        }
        command = Command::new("powershell");
        command.args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]).arg(&file);
        for param in params {
            command.arg(&param.value);
        }
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }
    } else {
        command = Command::new("/bin/sh");
        command.args(["-c", script, "mixel-script"]);
        for param in params {
            command.arg(&param.value);
        }
    }
    for param in params {
        command.env(env_name(&param.name), &param.value);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // Its own process group: a timeout ends the whole tree.
        command.process_group(0);
    }
    let Ok(mut child) = command.stdin(std::process::Stdio::null()).stdout(out).stderr(error).current_dir(dir.path()).spawn() else {
        return fail("The script could not be started");
    };
    let deadline = started + timeout;
    let (code, timed_out) = loop {
        match child.try_wait() {
            Ok(Some(status)) => break (status.code().unwrap_or(-1), false),
            Ok(None) if Instant::now() >= deadline => {
                #[cfg(unix)]
                // SAFETY: signal to the child's own process group.
                unsafe {
                    libc::kill(-(child.id() as libc::pid_t), libc::SIGKILL);
                }
                let _ = child.kill();
                let _ = child.wait();
                break (-2, true);
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(50)),
            Err(_) => break (-1, false),
        }
    };
    let (stdout, cut_out) = cut(&std::fs::read(&out_path).unwrap_or_default());
    let (stderr, cut_err) = cut(&std::fs::read(&err_path).unwrap_or_default());
    Outcome { exit_code: code, stdout, stderr, duration_ms: started.elapsed().as_millis() as u64, truncated: cut_out || cut_err, timed_out }
}

fn refused(cfg: &HostConfig, command: &AgentCommand, why: &str) -> crate::tools_proto::ToolError {
    (cfg.audit)("script.refused", json!({"reason": why, "sha256": agent_verify::script_sha256(&command.script), "script_ref": command.script_ref}));
    agent_verify::log_line(command.id, command.signed.as_ref().map(|s| s.key_id.as_str()), &format!("refused: {why}"), None, &command.script);
    err("scripts", "refused_signature", why)
}

/// `script.run` on the host: verify, policy, ask, run, record.
pub async fn host_run(cfg: &Arc<HostConfig>, args: Value) -> ToolResult<Value> {
    let policy = (cfg.policy)();
    if !policy.scripts {
        return Err(err("scripts", "script_policy", "Running scripts is not allowed by company policy"));
    }
    if !cfg.peer_scripts {
        return Err(err("scripts", "peer_too_old", "The technician's app cannot run scripts"));
    }
    let command: AgentCommand = serde_json::from_value(args.get("command").cloned().unwrap_or(Value::Null)).map_err(|_| err("scripts", "bad_argument", "The command is not a signed script"))?;
    if command.script.len() > signaling_server::protocol::MAX_AGENT_SCRIPT {
        return Err(err("scripts", "bad_argument", "The script is too long"));
    }
    let Some((device, connection)) = cfg.identity else {
        return Err(err("scripts", "not_available", "This computer's identity is not ready"));
    };
    if command.signed.is_none() {
        return Err(refused(cfg, &command, "commands must be signed"));
    }
    if !agent_verify::valid_params(&command.params) {
        return Err(refused(cfg, &command, "parameters are not valid"));
    }
    let key = match agent_verify::check_and_record(&command, device, connection, true) {
        Ok(key) => key,
        Err(refusal) => return Err(refused(cfg, &command, refusal.text())),
    };
    let sha = agent_verify::script_sha256(&command.script);
    if !cfg.grants.unattended {
        let (answer, decided) = oneshot::channel();
        let pending = Uuid::new_v4();
        if let Ok(mut view) = cfg.view.lock() {
            view.pending.push(Pending {
                id: pending,
                kind: "script",
                who: cfg.grants.who.clone(),
                reason: String::new(),
                script_ref: command.script_ref.clone(),
                sha256: Some(sha.clone()),
                since: now(),
                answer: Some(answer),
            });
        }
        let accepted = matches!(tokio::time::timeout(Duration::from_secs(60), decided).await, Ok(Ok(true)));
        if let Ok(mut view) = cfg.view.lock() {
            view.pending.retain(|p| p.id != pending);
        }
        if !accepted {
            (cfg.audit)("script.run", json!({"script_ref": command.script_ref, "sha256": sha, "who": cfg.grants.who, "result": "refused", "params": masked(&command.params)}));
            return Err(err("scripts", "elevation_refused", "The person at the computer did not allow the script"));
        }
    }
    agent_verify::log_line(command.id, key.as_deref(), "accepted (session)", None, &command.script);
    let (shell, script, params, timeout) = (command.shell.clone(), command.script.clone(), command.params.clone(), Duration::from_secs(u64::from(command.timeout_seconds.clamp(1, 3600))));
    let outcome = tokio::task::spawn_blocking(move || run_split(&shell, &script, &params, timeout)).await.map_err(|_| err("scripts", "busy", "The script runner stopped"))?;
    agent_verify::log_line(command.id, key.as_deref(), "finished (session)", Some(outcome.exit_code), &command.script);
    let result = if outcome.timed_out { "timed_out" } else if outcome.exit_code == 0 { "ok" } else { "failed" };
    (cfg.audit)("script.run", json!({"script_ref": command.script_ref, "sha256": sha, "device": device, "who": cfg.grants.who, "exit_code": outcome.exit_code,
        "duration_ms": outcome.duration_ms, "result": result, "params": masked(&command.params)}));
    if let Ok(mut view) = cfg.view.lock() {
        view.script = Some(json!({"script_ref": command.script_ref, "result": result, "at": now()}));
    }
    Ok(json!({"id": command.id, "exit_code": outcome.exit_code, "stdout": outcome.stdout, "stderr": outcome.stderr, "duration_ms": outcome.duration_ms,
        "truncated": outcome.truncated, "timed_out": outcome.timed_out, "key_id": key}))
}

fn now() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    fn p(name: &str, value: &str) -> ScriptParam {
        ScriptParam { name: name.into(), value: value.into() }
    }

    #[test]
    fn stdout_stderr_exit_code_and_parameters_are_returned_apart() {
        let out = run_split("sh", "echo out-$MIXEL_PARAM_NAME-$1; echo problem >&2; exit 3", &[p("name", "Lea")], Duration::from_secs(10));
        assert_eq!(out.exit_code, 3);
        assert_eq!(out.stdout.trim(), "out-Lea-Lea");
        assert_eq!(out.stderr.trim(), "problem");
        assert!(!out.timed_out && !out.truncated && out.duration_ms < 5000);
    }
    #[test]
    fn a_script_over_its_time_limit_is_stopped_and_reported() {
        let out = run_split("sh", "sleep 30 & sleep 30", &[], Duration::from_secs(1));
        assert!(out.timed_out && out.exit_code == -2);
        assert!(out.duration_ms < 5000);
    }
    #[test]
    fn long_output_is_cut_and_says_so() {
        let out = run_split("sh", "yes aaaaaaaaaa | head -c 30000", &[], Duration::from_secs(10));
        assert!(out.truncated && out.stdout.ends_with("[output shortened]") && out.stdout.len() < STREAM_LIMIT + 40);
    }
    #[test]
    fn secret_parameters_are_masked_in_records() {
        let masked = masked(&[p("SECRET_token", "abc"), p("name", "Lea")]);
        assert_eq!(masked[0]["value"], "[masked]");
        assert_eq!(masked[1]["value"], "Lea");
    }
}
