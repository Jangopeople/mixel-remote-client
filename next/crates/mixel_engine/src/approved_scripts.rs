//! Company-approved IT support script library (Item 11) and local device health/inventory
//! snapshot (Item 12). Only statically allowlisted script IDs may run — never arbitrary shell.
use serde::Serialize;
use std::{
    process::Command,
    time::{Duration, Instant},
};

const MAX_OUTPUT_BYTES: usize = 32 * 1024;
const SCRIPT_TIMEOUT: Duration = Duration::from_secs(15);

#[derive(Debug, Clone, Serialize)]
pub struct ScriptMeta {
    pub id: &'static str,
    pub name: &'static str,
    pub description: &'static str,
    pub category: &'static str,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScriptRunResult {
    pub id: String,
    pub name: String,
    pub exit_code: i32,
    pub output: String,
    pub duration_ms: u64,
}

#[derive(Debug, Clone, Serialize, serde::Deserialize)]
pub struct DeviceHealth {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub os: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub cpu: String,
    #[serde(default)]
    pub ram_gb: u64,
    #[serde(default)]
    pub disk_free_gb: u64,
    #[serde(default)]
    pub disk_total_gb: u64,
    #[serde(default)]
    pub user: String,
    #[serde(default)]
    pub last_boot: String,
    #[serde(default)]
    pub pending_reboot: bool,
}

struct ApprovedScript {
    meta: ScriptMeta,
    #[cfg(target_os = "macos")]
    cmd: &'static str,
    #[cfg(target_os = "linux")]
    cmd: &'static str,
    #[cfg(target_os = "windows")]
    cmd: &'static str,
}

const LIBRARY: &[ApprovedScript] = &[
    ApprovedScript {
        meta: ScriptMeta {
            id: "system_info",
            name: "System & OS summary",
            description: "Show OS version, kernel, hardware model, memory, and uptime.",
            category: "Inventory",
        },
        #[cfg(target_os = "macos")]
        cmd: "sw_vers; uname -m; uptime; sysctl -n hw.model hw.memsize",
        #[cfg(target_os = "linux")]
        cmd: "cat /etc/os-release | head -n 6; uname -a; uptime; free -h",
        #[cfg(target_os = "windows")]
        cmd: "Get-ComputerInfo | Select-Object CsName,OsName,OsVersion,CsModel,CsTotalPhysicalMemory | Format-List",
    },
    ApprovedScript {
        meta: ScriptMeta {
            id: "network_check",
            name: "Network & DNS check",
            description: "Show active IP addresses, default gateway, and DNS resolution to rs.mixel.ch.",
            category: "Network",
        },
        #[cfg(target_os = "macos")]
        cmd: "ifconfig | grep -E '^[a-z]|inet '; route -n get default 2>/dev/null | grep -E 'gateway|interface'; dscacheutil -q host -a name rs.mixel.ch",
        #[cfg(target_os = "linux")]
        cmd: "ip -brief addr; ip route show default; getent hosts rs.mixel.ch",
        #[cfg(target_os = "windows")]
        cmd: "Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*'} | Select-Object InterfaceAlias,IPAddress | Format-Table; Resolve-DnsName rs.mixel.ch -ErrorAction SilentlyContinue | Format-Table",
    },
    ApprovedScript {
        meta: ScriptMeta {
            id: "disk_health",
            name: "Disk space overview",
            description: "Show mounted filesystems and available disk capacity.",
            category: "Health",
        },
        #[cfg(target_os = "macos")]
        cmd: "df -h / /System/Volumes/Data 2>/dev/null || df -h /",
        #[cfg(target_os = "linux")]
        cmd: "df -hT -x tmpfs -x devtmpfs",
        #[cfg(target_os = "windows")]
        cmd: "Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{N='UsedGB';E={[math]::Round($_.Used/1GB,1)}},@{N='FreeGB';E={[math]::Round($_.Free/1GB,1)}} | Format-Table",
    },
    ApprovedScript {
        meta: ScriptMeta {
            id: "top_processes",
            name: "Top CPU & memory processes",
            description: "List the 15 busiest processes by CPU usage.",
            category: "Health",
        },
        #[cfg(target_os = "macos")]
        cmd: "ps -arcwwwxo pid,pcpu,pmem,comm | head -n 16",
        #[cfg(target_os = "linux")]
        cmd: "ps -eo pid,pcpu,pmem,comm --sort=-pcpu | head -n 16",
        #[cfg(target_os = "windows")]
        cmd: "Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 Id,ProcessName,@{N='CPU_s';E={[math]::Round($_.CPU,1)}},@{N='MemMB';E={[math]::Round($_.WorkingSet64/1MB,1)}} | Format-Table",
    },
    ApprovedScript {
        meta: ScriptMeta {
            id: "flush_dns",
            name: "Flush DNS cache",
            description: "Clear the local DNS resolver cache to resolve stale host lookups.",
            category: "Network",
        },
        #[cfg(target_os = "macos")]
        cmd: "dscacheutil -flushcache && echo 'DNS cache flushed.'",
        #[cfg(target_os = "linux")]
        cmd: "resolvectl flush-caches 2>/dev/null && echo 'DNS cache flushed.' || echo 'systemd-resolved not active.'",
        #[cfg(target_os = "windows")]
        cmd: "Clear-DnsClientCache; 'DNS cache flushed.'",
    },
];

pub fn list_scripts() -> Vec<ScriptMeta> {
    LIBRARY.iter().map(|s| s.meta.clone()).collect()
}

fn exec_bounded(script: &str) -> Result<(i32, String), String> {
    #[cfg(target_os = "windows")]
    let mut child = {
        use std::os::windows::process::CommandExt;
        Command::new("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script])
            .creation_flags(0x0800_0000)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| format!("Cannot start PowerShell: {e}"))?
    };
    #[cfg(not(target_os = "windows"))]
    let mut child = Command::new("/bin/sh")
        .args(["-c", script])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("Cannot start shell: {e}"))?;

    let deadline = Instant::now() + SCRIPT_TIMEOUT;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Ok((-1, "Script timed out after 15 seconds.".into()));
                }
                std::thread::sleep(Duration::from_millis(25));
            }
            Err(e) => return Err(format!("Script wait failed: {e}")),
        }
    }
    let output = child.wait_with_output().map_err(|e| format!("Script output failed: {e}"))?;
    let code = output.status.code().unwrap_or(-1);
    let mut combined = output.stdout;
    if !output.stderr.is_empty() {
        if !combined.is_empty() && !combined.ends_with(b"\n") {
            combined.push(b'\n');
        }
        combined.extend_from_slice(&output.stderr);
    }
    if combined.len() > MAX_OUTPUT_BYTES {
        combined.truncate(MAX_OUTPUT_BYTES);
    }
    Ok((code, String::from_utf8_lossy(&combined).trim().to_owned()))
}

/// Run a strictly allowlisted script by ID and write an audit log entry.
pub fn run_approved_script(id: &str) -> Result<ScriptRunResult, String> {
    let Some(entry) = LIBRARY.iter().find(|s| s.meta.id == id) else {
        crate::app_log::info("script_audit", &format!("refused unknown script id={id}"));
        return Err(format!("Unknown or unapproved script '{id}'"));
    };
    let started = Instant::now();
    let (exit_code, output) = exec_bounded(entry.cmd)?;
    let duration_ms = started.elapsed().as_millis() as u64;
    crate::app_log::info(
        "script_audit",
        &format!("ran id={} exit={} duration_ms={}", entry.meta.id, exit_code, duration_ms),
    );
    Ok(ScriptRunResult {
        id: entry.meta.id.to_owned(),
        name: entry.meta.name.to_owned(),
        exit_code,
        output,
        duration_ms,
    })
}

/// Collect a structured local device health & inventory snapshot.
pub fn collect_local_health() -> Result<DeviceHealth, String> {
    #[cfg(target_os = "macos")]
    let script = r#"esc(){ printf '%s' "$1" | tr -d '"\\' | tr -s ' ' | cut -c1-120; }
ram=$(( $(sysctl -n hw.memsize) / 1073741824 ))
set -- $(df -g / | tail -1 | awk '{print $4, $2}')
boot=$(date -r "$(sysctl -n kern.boottime | sed 's/.*sec = \([0-9]*\).*/\1/')" '+%Y-%m-%d %H:%M')
printf '{"name":"%s","os":"macOS %s","model":"%s","cpu":"%s","ram_gb":%s,"disk_free_gb":%s,"disk_total_gb":%s,"user":"%s","last_boot":"%s","pending_reboot":false}' "$(esc "$(scutil --get ComputerName 2>/dev/null || hostname)")" "$(sw_vers -productVersion)" "$(esc "$(sysctl -n hw.model)")" "$(esc "$(sysctl -n machdep.cpu.brand_string)")" "$ram" "${1:-0}" "${2:-0}" "$(esc "$(id -un)")" "$boot""#;
    #[cfg(target_os = "linux")]
    let script = r#"esc(){ printf '%s' "$1" | tr -d '"\\' | tr -s ' ' | cut -c1-120; }
. /etc/os-release 2>/dev/null
cpu=$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ //')
ram=$(awk '/MemTotal/ {printf "%d", ($2+524288)/1048576}' /proc/meminfo)
set -- $(df -BG --output=avail,size / | tail -1 | tr -d 'G')
model=$(cat /sys/devices/virtual/dmi/id/sys_vendor /sys/devices/virtual/dmi/id/product_name 2>/dev/null | tr '\n' ' ')
reboot=false; [ -f /var/run/reboot-required ] && reboot=true
printf '{"name":"%s","os":"%s","model":"%s","cpu":"%s","ram_gb":%s,"disk_free_gb":%s,"disk_total_gb":%s,"user":"%s","last_boot":"%s","pending_reboot":%s}' "$(esc "$(hostname)")" "$(esc "$PRETTY_NAME")" "$(esc "$model")" "$(esc "$cpu")" "${ram:-0}" "${1:-0}" "${2:-0}" "$(esc "$(id -un)")" "$(uptime -s | cut -c1-16)" "$reboot""#;
    #[cfg(target_os = "windows")]
    let script = r#"$ErrorActionPreference='SilentlyContinue'
$o=Get-CimInstance Win32_OperatingSystem; $c=Get-CimInstance Win32_ComputerSystem; $p=Get-CimInstance Win32_Processor | Select-Object -First 1
$d=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($env:SystemDrive)'"
$r=(Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
[ordered]@{name=$env:COMPUTERNAME; os="$($o.Caption) $($o.Version)"; model="$($c.Manufacturer) $($c.Model)"; cpu="$($p.Name)".Trim(); ram_gb=[math]::Round($c.TotalPhysicalMemory/1GB); disk_free_gb=[math]::Round($d.FreeSpace/1GB); disk_total_gb=[math]::Round($d.Size/1GB); user=$c.UserName; last_boot=$o.LastBootUpTime.ToString('yyyy-MM-dd HH:mm'); pending_reboot=[bool]$r} | ConvertTo-Json -Compress"#;
    #[cfg(any(target_os = "ios", target_os = "android"))]
    let script = "exit 1";

    let (code, output) = exec_bounded(script)?;
    if code != 0 || output.is_empty() {
        return Err("Unable to collect device health snapshot".into());
    }
    serde_json::from_str::<DeviceHealth>(&output).map_err(|e| format!("Invalid health JSON: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refuses_unapproved_script_ids() {
        let err = run_approved_script("rm -rf /").unwrap_err();
        assert!(err.contains("Unknown or unapproved"), "{err}");
    }

    #[test]
    fn runs_system_info_and_collects_health() {
        assert!(list_scripts().len() >= 5);
        let res = run_approved_script("system_info").unwrap();
        assert_eq!(res.exit_code, 0);
        assert!(!res.output.is_empty());
        let health = collect_local_health().unwrap();
        assert!(!health.os.is_empty());
        assert!(health.ram_gb > 0);
    }
}
