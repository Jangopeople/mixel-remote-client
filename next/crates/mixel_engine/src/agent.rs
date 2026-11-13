//! Device agent: while this computer is registered it reports a small inventory (system,
//! hardware, health) and runs commands that an authorised person queued in the console.
//! Commands run only while unattended access is turned on for this computer, in the
//! context of the process that hosts it (the user's app, or the always-on service host).
use crate::remote_session::{Command, RemoteSession};
use signaling_server::protocol::{AgentCommand, AgentResult, MAX_AGENT_INVENTORY, MAX_AGENT_OUTPUT};
use std::{
    sync::{
        Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::sync::mpsc;

static RESULT: Mutex<Option<AgentResult>> = Mutex::new(None);
static RUNNING: AtomicBool = AtomicBool::new(false);
pub const APP_VERSION: &str = env!("MIXEL_APP_VERSION");

#[cfg(target_os = "windows")]
const INVENTORY_SCRIPT: &str = r#"$ErrorActionPreference='SilentlyContinue'
$o=Get-CimInstance Win32_OperatingSystem; $c=Get-CimInstance Win32_ComputerSystem; $p=Get-CimInstance Win32_Processor | Select-Object -First 1
$d=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($env:SystemDrive)'"
$r=(Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
[ordered]@{name=$env:COMPUTERNAME; os="$($o.Caption) $($o.Version)"; model="$($c.Manufacturer) $($c.Model)"; cpu="$($p.Name)".Trim(); ram_gb=[math]::Round($c.TotalPhysicalMemory/1GB); disk_free_gb=[math]::Round($d.FreeSpace/1GB); disk_total_gb=[math]::Round($d.Size/1GB); user=$c.UserName; last_boot=$o.LastBootUpTime.ToString('yyyy-MM-dd HH:mm'); pending_reboot=[bool]$r} | ConvertTo-Json -Compress"#;
#[cfg(target_os = "linux")]
const INVENTORY_SCRIPT: &str = r#"esc(){ printf '%s' "$1" | tr -d '"\\' | tr -s ' ' | cut -c1-120; }
. /etc/os-release 2>/dev/null
cpu=$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ //')
ram=$(awk '/MemTotal/ {printf "%d", ($2+524288)/1048576}' /proc/meminfo)
set -- $(df -BG --output=avail,size / | tail -1 | tr -d 'G')
model=$(cat /sys/devices/virtual/dmi/id/sys_vendor /sys/devices/virtual/dmi/id/product_name 2>/dev/null | tr '\n' ' ')
reboot=false; [ -f /var/run/reboot-required ] && reboot=true
printf '{"name":"%s","os":"%s","model":"%s","cpu":"%s","ram_gb":%s,"disk_free_gb":%s,"disk_total_gb":%s,"user":"%s","last_boot":"%s","pending_reboot":%s}' "$(esc "$(hostname)")" "$(esc "$PRETTY_NAME")" "$(esc "$model")" "$(esc "$cpu")" "${ram:-0}" "${1:-0}" "${2:-0}" "$(esc "$(id -un)")" "$(uptime -s | cut -c1-16)" "$reboot""#;
/// The phone app collects no inventory (it is a controller only).
#[cfg(target_os = "ios")]
const INVENTORY_SCRIPT: &str = "exit 1";
#[cfg(target_os = "macos")]
const INVENTORY_SCRIPT: &str = r#"esc(){ printf '%s' "$1" | tr -d '"\\' | tr -s ' ' | cut -c1-120; }
ram=$(( $(sysctl -n hw.memsize) / 1073741824 ))
set -- $(df -g / | tail -1 | awk '{print $4, $2}')
boot=$(date -r "$(sysctl -n kern.boottime | sed 's/.*sec = \([0-9]*\).*/\1/')" '+%Y-%m-%d %H:%M')
printf '{"name":"%s","os":"macOS %s","model":"%s","cpu":"%s","ram_gb":%s,"disk_free_gb":%s,"disk_total_gb":%s,"user":"%s","last_boot":"%s","pending_reboot":false}' "$(esc "$(scutil --get ComputerName)")" "$(sw_vers -productVersion)" "$(esc "$(sysctl -n hw.model)")" "$(esc "$(sysctl -n machdep.cpu.brand_string)")" "$ram" "${1:-0}" "${2:-0}" "$(esc "$(id -un)")" "$boot""#;

/// Runs a script with the platform shell; returns (exit code, combined output, bounded).
fn run_script(shell: &str, script: &str, timeout: Duration) -> (i32, String) {
    let Ok(dir) = tempfile::tempdir() else { return (-1, "Cannot prepare the command".into()) };
    let out_path = dir.path().join("out.txt");
    let Ok(out) = std::fs::File::create(&out_path) else { return (-1, "Cannot prepare the command".into()) };
    let Ok(err) = out.try_clone() else { return (-1, "Cannot prepare the command".into()) };
    let mut command;
    if shell == "powershell" {
        let file = dir.path().join("command.ps1");
        // UTF-8 with BOM so Windows PowerShell reads non-ASCII text correctly.
        let mut bytes = vec![0xef, 0xbb, 0xbf];
        bytes.extend_from_slice(b"[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8\r\n");
        bytes.extend_from_slice(script.as_bytes());
        if std::fs::write(&file, bytes).is_err() {
            return (-1, "Cannot prepare the command".into());
        }
        command = std::process::Command::new("powershell");
        command.args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]).arg(&file);
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }
    } else {
        command = std::process::Command::new("/bin/sh");
        command.args(["-c", script]);
    }
    let Ok(mut child) = command.stdin(std::process::Stdio::null()).stdout(out).stderr(err).current_dir(dir.path()).spawn() else {
        return (-1, "The command could not be started".into());
    };
    let deadline = Instant::now() + timeout;
    let code = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status.code().unwrap_or(-1),
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                break -2;
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(200)),
            Err(_) => break -1,
        }
    };
    let bytes = std::fs::read(&out_path).unwrap_or_default();
    let mut text = String::from_utf8_lossy(&bytes).into_owned();
    if code == -2 {
        text.push_str("\n[stopped: time limit reached]");
    }
    if text.len() > MAX_AGENT_OUTPUT - 64 {
        let mut cut = MAX_AGENT_OUTPUT - 64;
        while !text.is_char_boundary(cut) {
            cut -= 1;
        }
        text.truncate(cut);
        text.push_str("\n[output shortened]");
    }
    (code, text)
}

/// Inventory as JSON text, or None when it cannot be collected.
fn inventory() -> Option<String> {
    let shell = if cfg!(target_os = "windows") { "powershell" } else { "sh" };
    let (code, text) = run_script(shell, INVENTORY_SCRIPT, Duration::from_secs(40));
    if code != 0 {
        return None;
    }
    let mut value: serde_json::Value = serde_json::from_str(text.trim()).ok()?;
    let object = value.as_object_mut()?;
    object.insert("app_version".into(), APP_VERSION.into());
    object.insert("always_on".into(), cfg!(target_os = "windows").then(crate::agent::always_on).unwrap_or(false).into());
    let text = serde_json::to_string(&value).ok()?;
    (text.len() <= MAX_AGENT_INVENTORY).then_some(text)
}
#[cfg(target_os = "windows")]
fn always_on() -> bool {
    crate::windows_signin_host::is_signin_host()
}
#[cfg(not(target_os = "windows"))]
fn always_on() -> bool {
    false
}

/// A command arrived from the console (through the broker). Runs it unless one is running or
/// unattended access is off here; the result goes out with the next check-in.
pub fn accept(command: AgentCommand, unattended: bool) {
    if !unattended {
        if let Ok(mut slot) = RESULT.lock() {
            *slot = Some(AgentResult { id: command.id, exit_code: -3, output: "Refused: unattended access is turned off on this computer.".into() });
        }
        return;
    }
    if RUNNING.swap(true, Ordering::AcqRel) {
        return;
    }
    std::thread::spawn(move || {
        let (exit_code, output) = run_script(&command.shell, &command.script, Duration::from_secs(u64::from(command.timeout_seconds)));
        if let Ok(mut slot) = RESULT.lock() {
            *slot = Some(AgentResult { id: command.id, exit_code, output });
        }
        RUNNING.store(false, Ordering::Release);
    });
}

/// Check-in loop for one registration: every 30 s while registered; inventory every 15 minutes.
pub fn start(state: RemoteSession, commands: mpsc::Sender<Command>) {
    tokio::spawn(async move {
        let mut last_inventory: Option<Instant> = None;
        tokio::time::sleep(Duration::from_secs(20)).await;
        loop {
            if commands.is_closed() {
                break;
            }
            let phase = state.status().await.phase;
            if matches!(phase.as_str(), "ready" | "active" | "incoming" | "deciding" | "negotiating") {
                let due = last_inventory.is_none_or(|at| at.elapsed() >= Duration::from_secs(900));
                let report = if due { tokio::task::spawn_blocking(inventory).await.ok().flatten() } else { None };
                if report.is_some() {
                    last_inventory = Some(Instant::now());
                }
                let result = RESULT.lock().ok().and_then(|mut slot| slot.take());
                if commands.send(Command::Agent { inventory: report, result }).await.is_err() {
                    break;
                }
            }
            tokio::time::sleep(Duration::from_secs(30)).await;
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn scripts_return_their_exit_code_and_output_and_stop_at_the_time_limit() {
        if cfg!(target_os = "windows") {
            let (code, text) = run_script("powershell", "Write-Output 'hello'; exit 3", Duration::from_secs(30));
            assert_eq!((code, text.trim()), (3, "hello"));
        } else {
            let (code, text) = run_script("sh", "echo hello; echo problem >&2; exit 3", Duration::from_secs(10));
            assert_eq!(code, 3);
            assert!(text.contains("hello") && text.contains("problem"));
            let (code, text) = run_script("sh", "sleep 5", Duration::from_secs(1));
            assert_eq!(code, -2);
            assert!(text.contains("time limit"));
        }
    }
    #[test]
    fn inventory_is_bounded_json_with_the_app_version() {
        let text = inventory().expect("inventory");
        let value: serde_json::Value = serde_json::from_str(&text).unwrap();
        assert!(value["name"].as_str().is_some_and(|name| !name.is_empty()));
        assert_eq!(value["app_version"], APP_VERSION);
        assert!(text.len() <= MAX_AGENT_INVENTORY);
    }
}
