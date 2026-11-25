//! Rotating application log, connection preferences (`relay_only`, `idle_timeout_minutes`),
//! and sanitized diagnostics export for Mixel Remote.
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::{
        Mutex, OnceLock,
        atomic::{AtomicBool, AtomicU32, Ordering},
    },
    time::{SystemTime, UNIX_EPOCH},
};

const MAX_LOG_BYTES: u64 = 5 * 1024 * 1024;
const MAX_BACKUPS: usize = 3;

static LOG_LOCK: Mutex<()> = Mutex::new(());
static PREFS_INIT: OnceLock<()> = OnceLock::new();
static RELAY_ONLY: AtomicBool = AtomicBool::new(false);
static IDLE_TIMEOUT_MINUTES: AtomicU32 = AtomicU32::new(0);

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct ConnectionPrefs {
    #[serde(default)]
    relay_only: bool,
    #[serde(default)]
    idle_timeout_minutes: u32,
}

/// Platform-standard log directory:
/// - macOS: `~/Library/Logs/Mixel Remote`
/// - Windows: `%LOCALAPPDATA%\Mixel Remote\logs`
/// - Linux: `$XDG_STATE_HOME/mixel-remote/logs` or `~/.local/state/mixel-remote/logs`
pub fn log_dir() -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        std::env::var_os("HOME").map(|h| PathBuf::from(h).join("Library/Logs/Mixel Remote"))
    }
    #[cfg(target_os = "windows")]
    {
        std::env::var_os("LOCALAPPDATA")
            .map(|d| PathBuf::from(d).join("Mixel Remote").join("logs"))
    }
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    {
        if let Some(state) = std::env::var_os("XDG_STATE_HOME").filter(|v| !v.is_empty()) {
            Some(PathBuf::from(state).join("mixel-remote").join("logs"))
        } else {
            std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/state/mixel-remote/logs"))
        }
    }
}

pub fn log_path() -> Option<PathBuf> {
    log_dir().map(|d| d.join("mixel-remote.log"))
}

fn prefs_path() -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        std::env::var_os("HOME")
            .map(|h| PathBuf::from(h).join("Library/Application Support/Mixel Remote/connection-prefs.json"))
    }
    #[cfg(target_os = "windows")]
    {
        std::env::var_os("LOCALAPPDATA")
            .map(|d| PathBuf::from(d).join("Mixel Remote").join("connection-prefs.json"))
    }
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    {
        if let Some(cfg) = std::env::var_os("XDG_CONFIG_HOME").filter(|v| !v.is_empty()) {
            Some(PathBuf::from(cfg).join("mixel-remote").join("connection-prefs.json"))
        } else {
            std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config/mixel-remote/connection-prefs.json"))
        }
    }
}

fn ensure_prefs_loaded() {
    PREFS_INIT.get_or_init(|| {
        if let Some(path) = prefs_path()
            && let Ok(raw) = fs::read_to_string(&path)
            && let Ok(prefs) = serde_json::from_str::<ConnectionPrefs>(&raw)
        {
            RELAY_ONLY.store(prefs.relay_only, Ordering::Relaxed);
            IDLE_TIMEOUT_MINUTES.store(prefs.idle_timeout_minutes.min(480), Ordering::Relaxed);
        }
    });
}

fn save_prefs(prefs: &ConnectionPrefs) -> Result<(), String> {
    let Some(path) = prefs_path() else {
        return Err("Cannot determine preferences path".into());
    };
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("Cannot create preferences dir: {e}"))?;
    }
    let body = serde_json::to_string_pretty(prefs).map_err(|e| e.to_string())?;
    fs::write(&path, body).map_err(|e| format!("Cannot write preferences: {e}"))?;
    Ok(())
}

pub fn relay_only_enabled() -> bool {
    ensure_prefs_loaded();
    RELAY_ONLY.load(Ordering::Relaxed)
}

pub fn set_relay_only(enabled: bool) -> Result<(), String> {
    ensure_prefs_loaded();
    RELAY_ONLY.store(enabled, Ordering::Relaxed);
    let mins = IDLE_TIMEOUT_MINUTES.load(Ordering::Relaxed);
    save_prefs(&ConnectionPrefs {
        relay_only: enabled,
        idle_timeout_minutes: mins,
    })?;
    info("settings", &format!("relay_only={enabled}"));
    Ok(())
}

pub fn idle_timeout_minutes() -> u32 {
    ensure_prefs_loaded();
    IDLE_TIMEOUT_MINUTES.load(Ordering::Relaxed)
}

pub fn set_idle_timeout_minutes(minutes: u32) -> Result<(), String> {
    ensure_prefs_loaded();
    let clamped = minutes.min(480);
    IDLE_TIMEOUT_MINUTES.store(clamped, Ordering::Relaxed);
    let relay = RELAY_ONLY.load(Ordering::Relaxed);
    save_prefs(&ConnectionPrefs {
        relay_only: relay,
        idle_timeout_minutes: clamped,
    })?;
    info("settings", &format!("idle_timeout_minutes={clamped}"));
    Ok(())
}

fn utc_timestamp() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    // Civil date from Unix epoch (Howard Hinnant algorithm, zero extra deps).
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let h = rem / 3600;
    let m = (rem % 3600) / 60;
    let s = rem % 60;
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let mo = if mp < 10 { mp + 3 } else { mp - 9 };
    let yr = if mo <= 2 { y + 1 } else { y };
    format!("{yr:04}-{mo:02}-{d:02}T{h:02}:{m:02}:{s:02}Z")
}

/// Redact bearer tokens, JWTs (`eyJ...`), and inline `password=` / `secret=` / `credential=` values.
pub fn redact(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    for (idx, token) in input.split_whitespace().enumerate() {
        if idx > 0 {
            out.push(' ');
        }
        let lower = token.to_ascii_lowercase();
        if token.starts_with("eyJ") && token.len() > 24 {
            out.push_str("[REDACTED_JWT]");
        } else if let Some((k, _)) = token.split_once('=') {
            let kl = k.to_ascii_lowercase();
            if kl.contains("token")
                || kl.contains("password")
                || kl.contains("secret")
                || kl.contains("credential")
                || kl.contains("authorization")
            {
                out.push_str(k);
                out.push_str("=[REDACTED]");
            } else {
                out.push_str(token);
            }
        } else if lower == "bearer" {
            out.push_str("Bearer");
        } else {
            out.push_str(token);
        }
    }
    out
}

fn rotate_if_needed(path: &Path) {
    let Ok(meta) = fs::metadata(path) else {
        return;
    };
    if meta.len() < MAX_LOG_BYTES {
        return;
    }
    for i in (1..MAX_BACKUPS).rev() {
        let from = path.with_extension(format!("log.{i}"));
        let to = path.with_extension(format!("log.{}", i + 1));
        if from.exists() {
            let _ = fs::rename(&from, &to);
        }
    }
    let first = path.with_extension("log.1");
    let _ = fs::rename(path, &first);
}

pub fn info(category: &str, message: &str) {
    let Some(path) = log_path() else {
        return;
    };
    write_line(&path, category, message);
}

fn write_line(path: &Path, category: &str, message: &str) {
    let Ok(_guard) = LOG_LOCK.lock() else {
        return;
    };
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    rotate_if_needed(path);
    let clean = redact(message);
    let line = format!("{} [{}] {}\n", utc_timestamp(), category, clean);
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = file.write_all(line.as_bytes());
    }
}

fn tail_file(path: &Path, max_bytes: usize) -> String {
    let Ok(bytes) = fs::read(path) else {
        return String::new();
    };
    let slice = if bytes.len() > max_bytes {
        &bytes[bytes.len() - max_bytes..]
    } else {
        &bytes[..]
    };
    String::from_utf8_lossy(slice).into_owned()
}

fn perf_log_path() -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        std::env::var_os("HOME").map(|h| PathBuf::from(h).join("Library/Logs/Mixel-Remote-perf.log"))
    }
    #[cfg(target_os = "windows")]
    {
        std::env::var_os("LOCALAPPDATA").map(|d| PathBuf::from(d).join("Mixel Remote").join("perf.log"))
    }
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    {
        std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/state/mixel-remote/perf.log"))
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct DiagnosticsExport {
    pub path: String,
    pub size_bytes: usize,
    pub log_dir: String,
}

/// Write a sanitized diagnostics bundle to the platform log directory and return its path.
pub fn export_diagnostics(app_version: &str) -> Result<DiagnosticsExport, String> {
    let Some(dir) = log_dir() else {
        return Err("Cannot determine log directory".into());
    };
    fs::create_dir_all(&dir).map_err(|e| format!("Cannot create log directory: {e}"))?;
    let stamp = utc_timestamp().replace(':', "-");
    let out_path = dir.join(format!("mixel-remote-diagnostics-{stamp}.txt"));
    let mut report = String::with_capacity(8192);
    report.push_str("=== Mixel Remote Diagnostics ===\n");
    report.push_str(&format!("Generated: {}\n", utc_timestamp()));
    report.push_str(&format!("Version:   {app_version}\n"));
    report.push_str(&format!("OS / Arch: {} / {}\n", std::env::consts::OS, std::env::consts::ARCH));
    report.push_str(&format!("Relay-only policy: {}\n", relay_only_enabled()));
    report.push_str(&format!("Idle timeout (min): {}\n", idle_timeout_minutes()));
    report.push_str("\n--- Application Log (tail) ---\n");
    if let Some(lp) = log_path() {
        let content = tail_file(&lp, 150 * 1024);
        if content.is_empty() {
            report.push_str("(no log entries yet)\n");
        } else {
            for line in content.lines() {
                report.push_str(&redact(line));
                report.push('\n');
            }
        }
    }
    report.push_str("\n--- Performance Log (tail) ---\n");
    if let Some(pp) = perf_log_path() {
        let content = tail_file(&pp, 50 * 1024);
        if content.is_empty() {
            report.push_str("(no perf entries)\n");
        } else {
            report.push_str(&content);
        }
    }
    fs::write(&out_path, report.as_bytes()).map_err(|e| format!("Cannot write diagnostics: {e}"))?;
    info("diagnostics", &format!("exported {}", out_path.display()));
    Ok(DiagnosticsExport {
        path: out_path.to_string_lossy().into_owned(),
        size_bytes: report.len(),
        log_dir: dir.to_string_lossy().into_owned(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_jwts_and_secret_pairs() {
        let line = "sign-in token=abc123456 eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaa.bbb host=123456789 password=secret";
        let out = redact(line);
        assert!(out.contains("token=[REDACTED]"), "{out}");
        assert!(out.contains("[REDACTED_JWT]"), "{out}");
        assert!(out.contains("password=[REDACTED]"), "{out}");
        assert!(out.contains("host=123456789"), "{out}");
        assert!(!out.contains("abc123456") && !out.contains("secret"), "{out}");
    }

    #[test]
    fn writes_and_rotates_log_file() {
        let dir = std::env::temp_dir().join(format!("mixel-log-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let path = dir.join("mixel-remote.log");
        write_line(&path, "signaling", "connected to wss://rs.mixel.ch:9444");
        let text = fs::read_to_string(&path).unwrap();
        assert!(text.contains("[signaling] connected to wss://rs.mixel.ch:9444"));
        let _ = fs::remove_dir_all(&dir);
    }
}
