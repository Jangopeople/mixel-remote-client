//! Host side of the task manager: processes, services and a system summary, collected with the OS's own
//! tools using fixed command lines (no user text ever reaches a shell). Sampled only while the technician polls.
//! The protected list lives here, on the host: a modified viewer cannot get past it.
use crate::tools_proto::{ToolResult, err};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    process::Command,
    time::{Duration, Instant},
};

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Proc {
    pub pid: u32,
    pub name: String,
    pub user: String,
    /// Total CPU time used so far, seconds.
    pub cpu_seconds: f64,
    /// The OS's own recent CPU percent where it has one (used for the first sample).
    pub os_cpu: f64,
    pub memory: u64,
    pub started: String,
    pub kernel: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Service {
    pub id: String,
    pub name: String,
    pub display: String,
    pub state: &'static str,
    pub start_type: &'static str,
}

// ---------------------------------------------------------------- protection

/// Why a process may not be ended, or None.
pub fn protected_process(proc_: &Proc) -> Option<&'static str> {
    let name = proc_.name.to_lowercase();
    let own = std::process::id();
    if proc_.pid == own {
        return Some("This is Mixel Remote itself");
    }
    if proc_.pid <= 1 || (cfg!(windows) && (proc_.pid == 4 || proc_.pid == 0)) {
        return Some("System process");
    }
    if proc_.kernel {
        return Some("Kernel task");
    }
    if name.contains("mixel remote") || name.contains("mixel-remote") || name.contains("mixelremote") || name.starts_with("mixel_") {
        return Some("Mixel Remote");
    }
    const SYSTEM: &[&str] = &[
        "system", "system idle process", "registry", "memory compression", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
        "services.exe", "lsass.exe", "lsaiso.exe", "fontdrvhost.exe", "dwm.exe", "launchd", "kernel_task", "loginwindow", "windowserver",
        "systemd", "init", "systemd-journald", "systemd-logind", "dbus-daemon", "udevd", "systemd-udevd", "securityd", "opendirectoryd",
    ];
    if SYSTEM.contains(&name.as_str()) {
        return Some("System process");
    }
    const SECURITY: &[&str] = &["msmpeng.exe", "mssense.exe", "nissrv.exe", "securityhealthservice.exe", "securityhealthsystray.exe", "senseir.exe", "endpointsecurityd"];
    if SECURITY.contains(&name.as_str()) {
        return Some("Security software");
    }
    None
}

/// Why a service may not be stopped, or None.
pub fn protected_service(id: &str) -> Option<&'static str> {
    let low = id.to_lowercase();
    if low.contains("mixel") {
        return Some("Mixel Remote");
    }
    const SYSTEM: &[&str] = &[
        "rpcss", "dcomlaunch", "lsm", "samss", "eventlog", "winmgmt", "bfe", "mpssvc", "windefend", "sense", "wscsvc", "trustedinstaller",
        "plugplay", "power", "profsvc", "schedule", "systemd-logind.service", "systemd-journald.service", "dbus.service", "dbus-broker.service",
        "systemd-udevd.service", "systemd-resolved.service", "multi-user.target",
    ];
    if SYSTEM.contains(&low.as_str()) {
        return Some("System service");
    }
    if low.starts_with("com.apple.") {
        return Some("macOS system job");
    }
    None
}

/// Services whose stop can lock the technician out (the app adds a warning).
pub fn lockout_risk(id: &str) -> bool {
    let low = id.to_lowercase();
    ["sshd", "ssh.service", "termservice", "umrdpservice", "sessionenv", "screensharing", "xrdp", "winrm", "networkmanager", "systemd-networkd", "remotedesktop", "com.openssh"]
        .iter()
        .any(|name| low.contains(name))
}

// ---------------------------------------------------------------- parsing helpers (pure, tested)

/// `ps` CPU time: `M:SS.hh`, `H:MM:SS.hh`, `D-HH:MM:SS`; Windows `h:mm:ss`.
pub fn parse_cpu_time(text: &str) -> f64 {
    let (days, rest) = match text.split_once('-') {
        Some((days, rest)) => (days.parse::<f64>().unwrap_or(0.0), rest),
        None => (0.0, text),
    };
    let mut seconds = 0.0;
    for part in rest.split(':') {
        seconds = seconds * 60.0 + part.trim().parse::<f64>().unwrap_or(0.0);
    }
    days * 86400.0 + seconds
}

/// Lines of `ps -axo pid=,user=,time=,rss=,%cpu=,lstart=,comm=` (macOS and other BSD-like systems).
pub fn parse_ps(output: &str) -> Vec<Proc> {
    output
        .lines()
        .filter_map(|line| {
            let mut fields = line.split_whitespace();
            let pid = fields.next()?.parse().ok()?;
            let user = fields.next()?.to_owned();
            let cpu_seconds = parse_cpu_time(fields.next()?);
            let rss: u64 = fields.next()?.parse().ok()?;
            let os_cpu = fields.next()?.parse().ok()?;
            // lstart: "Mon Nov 18 10:00:00 2026"
            let started: Vec<&str> = fields.by_ref().take(5).collect();
            if started.len() != 5 {
                return None;
            }
            let command: Vec<&str> = fields.collect();
            let path = command.join(" ");
            let name = path.rsplit('/').next().unwrap_or(&path).to_owned();
            if name.is_empty() {
                return None;
            }
            Some(Proc { pid, name, user, cpu_seconds, os_cpu, memory: rss * 1024, started: started.join(" "), kernel: false })
        })
        .collect()
}

/// `tasklist /v /fo csv /nh` rows.
pub fn parse_tasklist(output: &str) -> Vec<Proc> {
    output
        .lines()
        .filter_map(|line| {
            let line = line.trim();
            if !line.starts_with('"') {
                return None;
            }
            let cells: Vec<&str> = line.trim_matches('"').split("\",\"").collect();
            if cells.len() < 8 {
                return None;
            }
            let digits: String = cells[4].chars().filter(char::is_ascii_digit).collect();
            Some(Proc {
                pid: cells[1].parse().ok()?,
                name: cells[0].to_owned(),
                user: cells[6].to_owned(),
                cpu_seconds: parse_cpu_time(cells[7]),
                os_cpu: 0.0,
                memory: digits.parse::<u64>().unwrap_or(0) * 1024,
                started: String::new(),
                kernel: false,
            })
        })
        .collect()
}

/// `systemctl list-units --type=service --all --no-legend --plain` merged with `list-unit-files` (name state).
pub fn parse_systemd(units: &str, files: &str) -> Vec<Service> {
    let starts: HashMap<&str, &'static str> = files
        .lines()
        .filter_map(|line| {
            let mut fields = line.split_whitespace();
            let name = fields.next()?;
            let state = fields.next()?;
            Some((name, match state {
                "enabled" | "enabled-runtime" => "auto",
                "disabled" => "disabled",
                "static" | "indirect" | "generated" | "alias" => "manual",
                _ => "other",
            }))
        })
        .collect();
    units
        .lines()
        .filter_map(|line| {
            let line = line.trim_start_matches(['●', '*', ' ']);
            let mut fields = line.split_whitespace();
            let id = fields.next()?;
            if !id.ends_with(".service") {
                return None;
            }
            let _load = fields.next()?;
            let active = fields.next()?;
            let sub = fields.next()?;
            let display: Vec<&str> = fields.collect();
            let state = match (active, sub) {
                ("active", _) => "running",
                ("activating", _) => "starting",
                ("deactivating", _) => "stopping",
                ("inactive" | "failed", _) => "stopped",
                _ => "other",
            };
            Some(Service {
                id: id.to_owned(),
                name: id.trim_end_matches(".service").to_owned(),
                display: display.join(" "),
                state,
                start_type: starts.get(id).copied().unwrap_or("other"),
            })
        })
        .collect()
}

/// `launchctl list`: `PID\tStatus\tLabel`.
pub fn parse_launchctl(output: &str) -> Vec<Service> {
    output
        .lines()
        .skip(1)
        .filter_map(|line| {
            let mut fields = line.split('\t');
            let pid = fields.next()?.trim();
            let _status = fields.next()?;
            let label = fields.next()?.trim();
            if label.is_empty() {
                return None;
            }
            Some(Service {
                id: label.to_owned(),
                name: label.to_owned(),
                display: label.to_owned(),
                state: if pid == "-" { "stopped" } else { "running" },
                start_type: "other",
            })
        })
        .collect()
}

/// Windows services as JSON from `Get-CimInstance Win32_Service` (an array or a single object).
pub fn parse_windows_services(json_text: &str) -> Vec<Service> {
    let Ok(value) = serde_json::from_str::<Value>(json_text) else { return Vec::new() };
    let items = match value {
        Value::Array(items) => items,
        single @ Value::Object(_) => vec![single],
        _ => return Vec::new(),
    };
    items
        .iter()
        .filter_map(|item| {
            let name = item.get("Name")?.as_str()?.to_owned();
            let state = match item.get("State").and_then(Value::as_str).unwrap_or("") {
                "Running" => "running",
                "Stopped" => "stopped",
                "Start Pending" | "Continue Pending" => "starting",
                "Stop Pending" | "Pause Pending" => "stopping",
                _ => "other",
            };
            let start_type = match item.get("StartMode").and_then(Value::as_str).unwrap_or("") {
                "Auto" => "auto",
                "Manual" => "manual",
                "Disabled" => "disabled",
                _ => "other",
            };
            Some(Service {
                id: name.clone(),
                display: item.get("DisplayName").and_then(Value::as_str).unwrap_or(&name).to_owned(),
                name,
                state,
                start_type,
            })
        })
        .collect()
}

/// CPU percent of one core between two samples of cumulative CPU time.
pub fn cpu_percent(before: f64, after: f64, seconds: f64) -> f64 {
    if seconds <= 0.0 || after < before {
        return 0.0;
    }
    (((after - before) / seconds) * 100.0 * 10.0).round() / 10.0
}

/// A service id the host may pass to the OS: letters, digits and `._@-:` only.
pub fn valid_service_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 256 && !id.starts_with('-') && id.chars().all(|c| c.is_ascii_alphanumeric() || "._@-: ".contains(c)) && !id.contains("..")
}

fn run(program: &str, args: &[&str]) -> Result<String, String> {
    let mut command = Command::new(program);
    command.args(args).stdin(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let output = command.output().map_err(|_| format!("{program} could not be run"))?;
    Ok(String::from_utf8_lossy(&output.stdout).into_owned() + &String::from_utf8_lossy(&output.stderr).chars().take(0).collect::<String>())
}
fn run_ok(program: &str, args: &[&str]) -> Result<(bool, String), String> {
    let mut command = Command::new(program);
    command.args(args).stdin(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let output = command.output().map_err(|_| format!("{program} could not be run"))?;
    let text = String::from_utf8_lossy(&output.stdout).into_owned() + &String::from_utf8_lossy(&output.stderr);
    Ok((output.status.success(), text))
}

// ---------------------------------------------------------------- collectors

#[cfg(target_os = "linux")]
fn collect_processes() -> Vec<Proc> {
    let mut list = Vec::new();
    let Ok(entries) = std::fs::read_dir("/proc") else { return list };
    let ticks = 100.0;
    let boot = std::fs::read_to_string("/proc/uptime").ok().and_then(|t| t.split_whitespace().next().and_then(|v| v.parse::<f64>().ok())).unwrap_or(0.0);
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
    for entry in entries.flatten() {
        let Some(pid) = entry.file_name().to_str().and_then(|n| n.parse::<u32>().ok()) else { continue };
        let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) else { continue };
        // pid (comm) state ppid ... ; comm may contain spaces and parentheses: split at the last ')'.
        let Some(close) = stat.rfind(')') else { continue };
        let Some(open) = stat.find('(') else { continue };
        let name = stat[open + 1..close].to_owned();
        let fields: Vec<&str> = stat[close + 2..].split_whitespace().collect();
        if fields.len() < 22 {
            continue;
        }
        let utime: f64 = fields[11].parse().unwrap_or(0.0);
        let stime: f64 = fields[12].parse().unwrap_or(0.0);
        let start_ticks: f64 = fields[19].parse().unwrap_or(0.0);
        let rss_pages: u64 = fields[21].parse().unwrap_or(0);
        let uid = std::fs::metadata(format!("/proc/{pid}")).map(|m| std::os::unix::fs::MetadataExt::uid(&m)).unwrap_or(0);
        let kernel = std::fs::read(format!("/proc/{pid}/cmdline")).map(|c| c.is_empty()).unwrap_or(false) && rss_pages == 0;
        let started_epoch = now - boot + start_ticks / ticks;
        list.push(Proc {
            pid,
            name,
            user: user_name(uid),
            cpu_seconds: (utime + stime) / ticks,
            os_cpu: 0.0,
            memory: rss_pages * 4096,
            started: format!("{}", started_epoch as u64),
            kernel,
        });
    }
    list
}
#[cfg(target_os = "linux")]
fn user_name(uid: u32) -> String {
    static NAMES: std::sync::Mutex<Option<HashMap<u32, String>>> = std::sync::Mutex::new(None);
    let mut guard = NAMES.lock().unwrap_or_else(|e| e.into_inner());
    let map = guard.get_or_insert_with(|| {
        std::fs::read_to_string("/etc/passwd")
            .unwrap_or_default()
            .lines()
            .filter_map(|line| {
                let mut f = line.split(':');
                let name = f.next()?;
                f.next()?;
                Some((f.next()?.parse().ok()?, name.to_owned()))
            })
            .collect()
    });
    map.get(&uid).cloned().unwrap_or_else(|| uid.to_string())
}
#[cfg(target_os = "macos")]
fn collect_processes() -> Vec<Proc> {
    run("/bin/ps", &["-axo", "pid=,user=,time=,rss=,%cpu=,lstart=,comm="]).map(|text| parse_ps(&text)).unwrap_or_default()
}
#[cfg(target_os = "windows")]
fn collect_processes() -> Vec<Proc> {
    run("tasklist", &["/v", "/fo", "csv", "/nh"]).map(|text| parse_tasklist(&text)).unwrap_or_default()
}
#[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
fn collect_processes() -> Vec<Proc> {
    Vec::new()
}

#[cfg(target_os = "linux")]
fn collect_services() -> (&'static str, Vec<Service>) {
    let units = run("systemctl", &["list-units", "--type=service", "--all", "--no-legend", "--no-pager", "--plain"]).unwrap_or_default();
    let files = run("systemctl", &["list-unit-files", "--type=service", "--no-legend", "--no-pager"]).unwrap_or_default();
    ("systemd", parse_systemd(&units, &files))
}
#[cfg(target_os = "macos")]
fn collect_services() -> (&'static str, Vec<Service>) {
    ("launchd", run("/bin/launchctl", &["list"]).map(|text| parse_launchctl(&text)).unwrap_or_default())
}
#[cfg(target_os = "windows")]
fn collect_services() -> (&'static str, Vec<Service>) {
    let script = "Get-CimInstance Win32_Service | Select-Object Name,DisplayName,State,StartMode | ConvertTo-Json -Compress";
    let text = run("powershell", &["-NoProfile", "-NonInteractive", "-Command", script]).unwrap_or_default();
    ("windows_services", parse_windows_services(&text))
}
#[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
fn collect_services() -> (&'static str, Vec<Service>) {
    ("none", Vec::new())
}

// ---------------------------------------------------------------- API

#[derive(Default)]
pub struct Tasks {
    previous: Option<(Instant, HashMap<u32, f64>)>,
}

impl Tasks {
    /// A page of processes. CPU is measured against the previous call of this session (dropped after 15 s).
    pub fn processes(&mut self, top: usize, offset: usize, filter: &str, sort: &str) -> ToolResult<Value> {
        if !matches!(sort, "cpu" | "memory" | "name" | "pid") {
            return Err(err("tasks", "bad_argument", "Unknown sort"));
        }
        let list = collect_processes();
        let now = Instant::now();
        let before = self.previous.take().filter(|(at, _)| now.duration_since(*at) < Duration::from_secs(15));
        let mut rows: Vec<(Proc, f64)> = list
            .iter()
            .map(|proc_| {
                let cpu = match &before {
                    Some((at, map)) => map.get(&proc_.pid).map_or(0.0, |old| cpu_percent(*old, proc_.cpu_seconds, now.duration_since(*at).as_secs_f64())),
                    None => proc_.os_cpu,
                };
                (proc_.clone(), cpu)
            })
            .collect();
        self.previous = Some((now, list.iter().map(|p| (p.pid, p.cpu_seconds)).collect()));
        let needle = filter.to_lowercase();
        if !needle.is_empty() {
            rows.retain(|(p, _)| p.name.to_lowercase().contains(&needle) || p.user.to_lowercase().contains(&needle) || p.pid.to_string() == needle);
        }
        match sort {
            "memory" => rows.sort_by(|a, b| b.0.memory.cmp(&a.0.memory)),
            "name" => rows.sort_by_key(|row| row.0.name.to_lowercase()),
            "pid" => rows.sort_by_key(|row| row.0.pid),
            _ => rows.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal)),
        }
        let total = rows.len();
        let items: Vec<Value> = rows
            .into_iter()
            .skip(offset)
            .take(top.clamp(1, 500))
            .map(|(p, cpu)| {
                let why = protected_process(&p);
                json!({"pid": p.pid, "name": p.name, "user": p.user, "cpu": cpu, "memory": p.memory, "started": p.started,
                       "protected": why.is_some(), "protected_reason": why.unwrap_or("")})
            })
            .collect();
        Ok(json!({"total": total, "items": items, "sampled_at": now_secs()}))
    }

    /// Ends one process after the host's own checks.
    pub fn end_process(&mut self, pid: u32, name: &str) -> ToolResult<Value> {
        let list = collect_processes();
        let Some(found) = list.iter().find(|p| p.pid == pid) else {
            return Err(err("tasks", "not_found", "No such process"));
        };
        if let Some(why) = protected_process(found) {
            return Err(err("tasks", "protected", why));
        }
        if !found.name.eq_ignore_ascii_case(name) {
            return Err(err("tasks", "name_mismatch", "The process with this id is not the one chosen"));
        }
        kill_pid(pid)?;
        Ok(json!({"pid": pid, "name": found.name, "user": found.user}))
    }

    pub fn services(&self, filter: &str) -> ToolResult<Value> {
        let (kind, list) = collect_services();
        let needle = filter.to_lowercase();
        let items: Vec<Value> = list
            .into_iter()
            .filter(|s| needle.is_empty() || s.id.to_lowercase().contains(&needle) || s.display.to_lowercase().contains(&needle))
            .map(|s| {
                let why = protected_service(&s.id);
                json!({"id": s.id, "name": s.name, "display": s.display, "state": s.state, "start_type": s.start_type,
                       "protected": why.is_some(), "protected_reason": why.unwrap_or(""), "lockout_risk": lockout_risk(&s.id)})
            })
            .collect();
        Ok(json!({"kind": kind, "items": items}))
    }

    pub fn service_action(&self, id: &str, action: &str) -> ToolResult<Value> {
        if !matches!(action, "start" | "stop" | "restart") || !valid_service_id(id) {
            return Err(err("tasks", "bad_argument", "Unknown service action"));
        }
        let (_, list) = collect_services();
        let Some(service) = list.iter().find(|s| s.id == id) else {
            return Err(err("tasks", "not_found", "No such service"));
        };
        if action != "start"
            && let Some(why) = protected_service(id)
        {
            return Err(err("tasks", "protected", why));
        }
        service_control(id, action)?;
        std::thread::sleep(Duration::from_millis(600));
        let (_, after) = collect_services();
        let state = after.iter().find(|s| s.id == id).map_or(service.state, |s| s.state);
        Ok(json!({"id": id, "state": state}))
    }
}

fn now_secs() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

#[cfg(unix)]
fn kill_pid(pid: u32) -> ToolResult<()> {
    // SAFETY: a plain signal to a pid the caller validated against the process list just before.
    let result = unsafe { libc::kill(pid as libc::pid_t, libc::SIGTERM) };
    if result == 0 {
        // Give it a moment, then force it if it ignored the request.
        std::thread::sleep(Duration::from_millis(1200));
        // SAFETY: signal 0 only tests existence; SIGKILL to the same validated pid.
        unsafe {
            if libc::kill(pid as libc::pid_t, 0) == 0 {
                libc::kill(pid as libc::pid_t, libc::SIGKILL);
            }
        }
        return Ok(());
    }
    match std::io::Error::last_os_error().raw_os_error() {
        Some(libc::EPERM) => Err(err("tasks", "needs_elevation", "This process belongs to another user")),
        _ => Err(err("tasks", "not_found", "No such process")),
    }
}
#[cfg(windows)]
fn kill_pid(pid: u32) -> ToolResult<()> {
    let (ok, text) = run_ok("taskkill", &["/PID", &pid.to_string(), "/F"]).map_err(|m| err("tasks", "not_available", &m))?;
    if ok {
        Ok(())
    } else if text.to_lowercase().contains("access is denied") {
        Err(err("tasks", "needs_elevation", "Access is denied"))
    } else {
        Err(err("tasks", "not_found", "No such process"))
    }
}
#[cfg(not(any(unix, windows)))]
fn kill_pid(_: u32) -> ToolResult<()> {
    Err(err("tasks", "not_available", "Not available on this computer"))
}

fn service_control(id: &str, action: &str) -> ToolResult<()> {
    #[cfg(target_os = "linux")]
    let result = run_ok("systemctl", &[action, "--no-pager", id]);
    #[cfg(target_os = "macos")]
    let result = {
        // SAFETY: getuid has no preconditions.
        let uid = unsafe { libc::getuid() };
        let domain = if uid == 0 { "system".to_owned() } else { format!("gui/{uid}") };
        let target = format!("{domain}/{id}");
        match action {
            "start" => run_ok("/bin/launchctl", &["kickstart", &target]),
            "stop" => run_ok("/bin/launchctl", &["kill", "SIGTERM", &target]),
            _ => run_ok("/bin/launchctl", &["kickstart", "-k", &target]),
        }
    };
    #[cfg(target_os = "windows")]
    let result = match action {
        "start" => run_ok("sc.exe", &["start", id]),
        "stop" => run_ok("sc.exe", &["stop", id]),
        _ => {
            let _ = run_ok("sc.exe", &["stop", id]);
            std::thread::sleep(Duration::from_millis(1500));
            run_ok("sc.exe", &["start", id])
        }
    };
    #[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
    let result: Result<(bool, String), String> = Err("unsupported".into());
    let (ok, text) = result.map_err(|m| err("tasks", "not_available", &m))?;
    if ok {
        return Ok(());
    }
    let low = text.to_lowercase();
    if low.contains("access denied") || low.contains("access is denied") || low.contains("permission") || low.contains("authentication") || low.contains("not privileged") || low.contains("operation not permitted") {
        Err(err("tasks", "needs_elevation", "This needs administrator rights"))
    } else {
        Err(err("tasks", "not_available", "The service did not change state"))
    }
}

// ---------------------------------------------------------------- summary

pub fn summary() -> ToolResult<Value> {
    let hostname = run("hostname", &[]).unwrap_or_default().trim().to_owned();
    let (os, os_version) = os_info();
    let cores = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1);
    let (mem_total, mem_used) = memory();
    Ok(json!({
        "os": os, "os_version": os_version, "hostname": hostname, "uptime_s": uptime(),
        "cpu": {"model": cpu_model(), "cores": cores, "percent": cpu_total()},
        "memory": {"total": mem_total, "used": mem_used},
        "disks": disks(), "users": users(),
    }))
}

#[cfg(target_os = "linux")]
fn os_info() -> (String, String) {
    let text = std::fs::read_to_string("/etc/os-release").unwrap_or_default();
    let get = |key: &str| text.lines().find_map(|l| l.strip_prefix(key)).map(|v| v.trim_matches('"').to_owned()).unwrap_or_default();
    (get("NAME="), get("VERSION_ID="))
}
#[cfg(target_os = "macos")]
fn os_info() -> (String, String) {
    ("macOS".into(), run("/usr/bin/sw_vers", &["-productVersion"]).unwrap_or_default().trim().to_owned())
}
#[cfg(target_os = "windows")]
fn os_info() -> (String, String) {
    let text = run("cmd", &["/c", "ver"]).unwrap_or_default();
    ("Windows".into(), text.trim().to_owned())
}
#[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
fn os_info() -> (String, String) {
    (String::new(), String::new())
}

#[cfg(target_os = "linux")]
fn uptime() -> u64 {
    std::fs::read_to_string("/proc/uptime").ok().and_then(|t| t.split_whitespace().next().and_then(|v| v.parse::<f64>().ok())).map_or(0, |v| v as u64)
}
#[cfg(target_os = "macos")]
fn uptime() -> u64 {
    // kern.boottime: `{ sec = 1789000000, usec = 0 } Tue ...`
    let text = run("/usr/sbin/sysctl", &["-n", "kern.boottime"]).unwrap_or_default();
    let boot = text.split("sec = ").nth(1).and_then(|r| r.split(',').next()).and_then(|v| v.trim().parse::<u64>().ok()).unwrap_or(0);
    now_secs().saturating_sub(boot)
}
#[cfg(target_os = "windows")]
fn uptime() -> u64 {
    // SAFETY: GetTickCount64 has no preconditions.
    unsafe { windows::Win32::System::SystemInformation::GetTickCount64() / 1000 }
}
#[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
fn uptime() -> u64 {
    0
}

#[cfg(target_os = "linux")]
fn cpu_model() -> String {
    std::fs::read_to_string("/proc/cpuinfo").unwrap_or_default().lines().find_map(|l| l.strip_prefix("model name")).and_then(|l| l.split(':').nth(1)).map(|v| v.trim().to_owned()).unwrap_or_default()
}
#[cfg(target_os = "macos")]
fn cpu_model() -> String {
    run("/usr/sbin/sysctl", &["-n", "machdep.cpu.brand_string"]).unwrap_or_default().trim().to_owned()
}
#[cfg(target_os = "windows")]
fn cpu_model() -> String {
    std::env::var("PROCESSOR_IDENTIFIER").unwrap_or_default()
}
#[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
fn cpu_model() -> String {
    String::new()
}

/// Whole-machine CPU percent (0-100): two samples 250 ms apart.
#[cfg(target_os = "linux")]
fn cpu_total() -> f64 {
    let read = || {
        let text = std::fs::read_to_string("/proc/stat").ok()?;
        let line = text.lines().next()?;
        let v: Vec<f64> = line.split_whitespace().skip(1).filter_map(|x| x.parse().ok()).collect();
        let idle = v.get(3)? + v.get(4).copied().unwrap_or(0.0);
        Some((v.iter().sum::<f64>(), idle))
    };
    let (Some(a), None) = (read(), ()) else { return 0.0 };
    std::thread::sleep(Duration::from_millis(250));
    let Some(b) = read() else { return 0.0 };
    let total = b.0 - a.0;
    if total <= 0.0 { 0.0 } else { (((total - (b.1 - a.1)) / total) * 1000.0).round() / 10.0 }
}
#[cfg(target_os = "macos")]
fn cpu_total() -> f64 {
    let text = run("/bin/ps", &["-A", "-o", "%cpu="]).unwrap_or_default();
    let sum: f64 = text.lines().filter_map(|l| l.trim().parse::<f64>().ok()).sum();
    let cores = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1) as f64;
    ((sum / cores).min(100.0) * 10.0).round() / 10.0
}
#[cfg(target_os = "windows")]
fn cpu_total() -> f64 {
    let text = run("powershell", &["-NoProfile", "-NonInteractive", "-Command", "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average"]).unwrap_or_default();
    text.trim().parse().unwrap_or(0.0)
}
#[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
fn cpu_total() -> f64 {
    0.0
}

#[cfg(target_os = "linux")]
fn memory() -> (u64, u64) {
    let text = std::fs::read_to_string("/proc/meminfo").unwrap_or_default();
    let kb = |key: &str| text.lines().find_map(|l| l.strip_prefix(key)).and_then(|l| l.split_whitespace().next()).and_then(|v| v.parse::<u64>().ok()).unwrap_or(0) * 1024;
    let total = kb("MemTotal:");
    (total, total.saturating_sub(kb("MemAvailable:")))
}
#[cfg(target_os = "macos")]
fn memory() -> (u64, u64) {
    let total = run("/usr/sbin/sysctl", &["-n", "hw.memsize"]).unwrap_or_default().trim().parse().unwrap_or(0);
    let text = run("/usr/bin/vm_stat", &[]).unwrap_or_default();
    let page: u64 = text.split("page size of ").nth(1).and_then(|r| r.split_whitespace().next()).and_then(|v| v.parse().ok()).unwrap_or(16384);
    let pages = |key: &str| text.lines().find_map(|l| l.strip_prefix(key)).and_then(|l| l.trim().trim_end_matches('.').parse::<u64>().ok()).unwrap_or(0);
    let available = (pages("Pages free:") + pages("Pages inactive:") + pages("Pages speculative:")) * page;
    (total, total.saturating_sub(available))
}
#[cfg(target_os = "windows")]
fn memory() -> (u64, u64) {
    let text = run("powershell", &["-NoProfile", "-NonInteractive", "-Command", "$o=Get-CimInstance Win32_OperatingSystem; \"$($o.TotalVisibleMemorySize) $($o.FreePhysicalMemory)\""]).unwrap_or_default();
    let mut v = text.split_whitespace().filter_map(|x| x.parse::<u64>().ok());
    let (total, free) = (v.next().unwrap_or(0) * 1024, v.next().unwrap_or(0) * 1024);
    (total, total.saturating_sub(free))
}
#[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
fn memory() -> (u64, u64) {
    (0, 0)
}

#[cfg(unix)]
fn disks() -> Vec<Value> {
    let text = run("df", &["-k", "-P"]).unwrap_or_default();
    parse_df(&text)
}
#[cfg(windows)]
fn disks() -> Vec<Value> {
    let text = run("powershell", &["-NoProfile", "-NonInteractive", "-Command", "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object { \"$($_.DeviceID) $($_.Size) $($_.FreeSpace)\" }"]).unwrap_or_default();
    text.lines()
        .filter_map(|l| {
            let mut f = l.split_whitespace();
            Some(json!({"mount": f.next()?, "total": f.next()?.parse::<u64>().ok()?, "free": f.next()?.parse::<u64>().ok()?}))
        })
        .collect()
}
#[cfg(not(any(unix, windows)))]
fn disks() -> Vec<Value> {
    Vec::new()
}

/// `df -k -P` rows of real devices (`/dev/...`), sizes in bytes.
pub fn parse_df(text: &str) -> Vec<Value> {
    let mut seen = std::collections::HashSet::new();
    text.lines()
        .skip(1)
        .filter_map(|line| {
            let f: Vec<&str> = line.split_whitespace().collect();
            if f.len() < 6 || !f[0].starts_with("/dev/") || !seen.insert(f[0].to_owned()) {
                return None;
            }
            let mount = f[5..].join(" ");
            if mount.starts_with("/System/Volumes/") && mount != "/System/Volumes/Data" {
                return None;
            }
            Some(json!({"mount": mount, "total": f[1].parse::<u64>().ok()? * 1024, "free": f[3].parse::<u64>().ok()? * 1024}))
        })
        .collect()
}

#[cfg(unix)]
fn users() -> Vec<Value> {
    let text = run("who", &[]).unwrap_or_default();
    text.lines().filter_map(|l| {
        let mut f = l.split_whitespace();
        Some(json!({"name": f.next()?, "session": f.next()?}))
    }).collect()
}
#[cfg(windows)]
fn users() -> Vec<Value> {
    let text = run("query", &["user"]).unwrap_or_default();
    text.lines().skip(1).filter_map(|l| {
        let mut f = l.trim_start_matches('>').split_whitespace();
        Some(json!({"name": f.next()?, "session": f.next().unwrap_or("")}))
    }).collect()
}
#[cfg(not(any(unix, windows)))]
fn users() -> Vec<Value> {
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cpu_times_parse_in_every_format() {
        assert_eq!(parse_cpu_time("0:01.50"), 1.5);
        assert_eq!(parse_cpu_time("1:02:03"), 3723.0);
        assert_eq!(parse_cpu_time("2-00:00:10"), 172_810.0);
        assert_eq!(cpu_percent(1.0, 1.5, 1.0), 50.0);
        assert_eq!(cpu_percent(2.0, 1.0, 1.0), 0.0);
    }

    #[test]
    fn ps_output_is_parsed() {
        let text = "  1 root 0:10.00 1234 0.5 Mon Nov 17 10:00:00 2026 /sbin/launchd\n 501 mike 1:00:00 20480 12.5 Tue Nov 18 09:30:11 2026 /Applications/Some App/Contents/MacOS/Some App\n";
        let list = parse_ps(text);
        assert_eq!(list.len(), 2);
        assert_eq!((list[0].pid, list[0].name.as_str(), list[0].memory), (1, "launchd", 1234 * 1024));
        assert_eq!(list[1].name, "Some App");
        assert_eq!(list[1].cpu_seconds, 3600.0);
        assert_eq!(list[1].started, "Tue Nov 18 09:30:11 2026");
    }

    #[test]
    fn tasklist_csv_is_parsed() {
        let text = "\"notepad.exe\",\"4242\",\"Console\",\"1\",\"12,345 K\",\"Running\",\"PC\\\\lea\",\"0:00:05\",\"Untitled - Notepad\"\r\n";
        let list = parse_tasklist(text);
        assert_eq!(list.len(), 1);
        assert_eq!((list[0].pid, list[0].memory, list[0].cpu_seconds), (4242, 12345 * 1024, 5.0));
        assert_eq!(list[0].user, "PC\\\\lea");
    }

    #[test]
    fn service_lists_are_parsed() {
        let units = "ssh.service loaded active running OpenBSD Secure Shell server\ncron.service loaded inactive dead Regular background program\n● bad.service loaded failed failed Broken\nfoo.socket loaded active listening x\n";
        let files = "ssh.service enabled enabled\ncron.service disabled enabled\nbad.service static -\n";
        let list = parse_systemd(units, files);
        assert_eq!(list.len(), 3);
        assert_eq!((list[0].state, list[0].start_type), ("running", "auto"));
        assert_eq!((list[1].state, list[1].start_type), ("stopped", "disabled"));
        assert_eq!((list[2].state, list[2].start_type), ("stopped", "manual"));
        let mac = parse_launchctl("PID\tStatus\tLabel\n123\t0\tch.mixel.a\n-\t0\tcom.apple.b\n");
        assert_eq!((mac.len(), mac[0].state, mac[1].state), (2, "running", "stopped"));
        let win = parse_windows_services("[{\"Name\":\"Spooler\",\"DisplayName\":\"Print Spooler\",\"State\":\"Running\",\"StartMode\":\"Auto\"}]");
        assert_eq!((win[0].state, win[0].start_type, win[0].display.as_str()), ("running", "auto", "Print Spooler"));
        assert_eq!(parse_windows_services("{\"Name\":\"X\",\"State\":\"Stopped\",\"StartMode\":\"Disabled\"}").len(), 1);
    }

    #[test]
    fn protected_lists_hold_on_the_host() {
        let p = |pid: u32, name: &str| Proc { pid, name: name.into(), ..Default::default() };
        assert!(protected_process(&p(std::process::id(), "anything")).is_some());
        assert!(protected_process(&p(1, "launchd")).is_some());
        assert!(protected_process(&p(500, "LSASS.exe")).is_some());
        assert!(protected_process(&p(500, "MsMpEng.exe")).is_some());
        assert!(protected_process(&p(500, "Mixel Remote")).is_some());
        assert!(protected_process(&Proc { kernel: true, pid: 77, name: "kworker".into(), ..Default::default() }).is_some());
        assert!(protected_process(&p(500, "notepad.exe")).is_none());
        assert!(protected_service("MixelRemoteService").is_some());
        assert!(protected_service("WinDefend").is_some());
        assert!(protected_service("com.apple.x").is_some());
        assert!(protected_service("Spooler").is_none());
        assert!(lockout_risk("sshd.service") && lockout_risk("TermService") && !lockout_risk("cron.service"));
    }

    #[test]
    fn service_ids_are_validated() {
        assert!(valid_service_id("ssh.service") && valid_service_id("ch.mixel.agent") && valid_service_id("user@1000.service"));
        for bad in ["", "-rf", "a;b", "a b$c", "../x", "a|b", "a\nb"] {
            assert!(!valid_service_id(bad), "{bad}");
        }
    }

    #[test]
    fn df_rows_are_parsed() {
        let text = "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/disk3s1 976490576 500 100000000 5% /System/Volumes/Data\n/dev/disk3s1s1 976490576 500 100000000 5% /\ndevfs 1 1 0 100% /dev\n";
        let rows = parse_df(text);
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["free"], 100_000_000u64 * 1024);
    }

    #[test]
    fn this_computers_lists_and_summary_work() {
        let mut tasks = Tasks::default();
        let first = tasks.processes(5, 0, "", "cpu").unwrap();
        assert!(first["total"].as_u64().unwrap() > 3, "{first}");
        assert!(first["items"].as_array().unwrap().len() <= 5);
        // Our own process is listed, protected, and cannot be ended even when asked directly.
        let own = tasks.processes(500, 0, &std::process::id().to_string(), "pid").unwrap();
        assert_eq!(own["items"][0]["protected"], true, "{own}");
        let refused = tasks.end_process(std::process::id(), "whatever").unwrap_err();
        assert_eq!(refused.code, "protected");
        assert_eq!(tasks.end_process(4_000_000, "x").unwrap_err().code, "not_found");
        assert!(tasks.processes(5, 0, "", "bogus").is_err());
        let summary = summary().unwrap();
        assert!(summary["memory"]["total"].as_u64().unwrap() > 0 && summary["cpu"]["cores"].as_u64().unwrap() >= 1, "{summary}");
        assert!(!summary["disks"].as_array().unwrap().is_empty(), "{summary}");
        let services = tasks.services("").unwrap();
        assert!(services["kind"].is_string());
        assert_eq!(tasks.service_action("bad;id", "stop").unwrap_err().code, "bad_argument");
        assert_eq!(tasks.service_action("ok.id", "explode").unwrap_err().code, "bad_argument");
        assert_eq!(tasks.service_action("no-such-service-xyz", "stop").unwrap_err().code, "not_found");
    }

    #[cfg(unix)]
    #[test]
    fn a_spawned_process_can_be_ended_with_the_right_name_only() {
        let mut child = Command::new("sleep").arg("300").spawn().unwrap();
        let pid = child.id();
        let mut tasks = Tasks::default();
        assert_eq!(tasks.end_process(pid, "not-sleep").unwrap_err().code, "name_mismatch");
        assert!(child.try_wait().unwrap().is_none());
        let done = tasks.end_process(pid, "SLEEP").unwrap();
        assert_eq!(done["pid"], pid);
        assert!(child.wait().is_ok());
    }
}
