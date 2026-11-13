//! "Start Mixel Remote with this computer": a per-user login item that runs
//! the app with `--autostart` (Windows Run key, Linux XDG autostart entry,
//! macOS LaunchAgent). Nothing runs for other users or before sign-in.
pub const FLAG: &str = "--autostart";

fn executable() -> Result<String, String> {
    let path = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
    let text = path.to_str().ok_or("Unsupported install path")?.to_owned();
    if text.chars().any(|c| c.is_control() || c == '"') {
        return Err("Unsupported install path".into());
    }
    Ok(text)
}

#[cfg(target_os = "windows")]
const RUN_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run";
#[cfg(target_os = "windows")]
const RUN_VALUE: &str = "Mixel Remote";

#[cfg(target_os = "windows")]
pub fn enabled() -> bool {
    use std::os::windows::process::CommandExt;
    std::process::Command::new("reg")
        .args(["query", RUN_KEY, "/v", RUN_VALUE])
        .creation_flags(0x0800_0000)
        .output()
        .is_ok_and(|output| output.status.success())
}
#[cfg(target_os = "windows")]
pub fn set(enable: bool) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    let mut command = std::process::Command::new("reg");
    if enable {
        let value = format!("\"{}\" {FLAG}", executable()?);
        command.args(["add", RUN_KEY, "/v", RUN_VALUE, "/t", "REG_SZ", "/d", &value, "/f"]);
    } else {
        if !enabled() {
            return Ok(());
        }
        command.args(["delete", RUN_KEY, "/v", RUN_VALUE, "/f"]);
    }
    let ok = command
        .creation_flags(0x0800_0000)
        .output()
        .is_ok_and(|output| output.status.success());
    if ok { Ok(()) } else { Err("Cannot change the startup setting".into()) }
}

#[cfg(target_os = "linux")]
fn entry() -> Result<std::path::PathBuf, String> {
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|home| std::path::PathBuf::from(home).join(".config")))
        .ok_or("Home folder unavailable")?;
    Ok(base.join("autostart").join("mixel-remote.desktop"))
}
#[cfg(target_os = "linux")]
pub fn enabled() -> bool {
    entry().is_ok_and(|path| path.is_file())
}
#[cfg(target_os = "linux")]
pub fn set(enable: bool) -> Result<(), String> {
    let path = entry()?;
    if !enable {
        let _ = std::fs::remove_file(&path);
        return Ok(());
    }
    if let Some(folder) = path.parent() {
        std::fs::create_dir_all(folder).map_err(|_| "Cannot change the startup setting")?;
    }
    let text = format!(
        "[Desktop Entry]\nType=Application\nName=Mixel Remote\nExec=\"{}\" {FLAG}\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n",
        executable()?
    );
    std::fs::write(&path, text).map_err(|_| "Cannot change the startup setting".into())
}

/// The launch agent of this app: the native preview has its own, next to the current app's.
#[cfg(target_os = "macos")]
fn label() -> &'static str {
    match native_os_capture::macos_bundle::identifier().as_deref() {
        Some("ch.mixel.remote.native") => "ch.mixel.remote.native.autostart",
        _ => "ch.mixel.remote.autostart",
    }
}
#[cfg(target_os = "macos")]
fn agent() -> Result<std::path::PathBuf, String> {
    let home = std::env::var_os("HOME").ok_or("Home folder unavailable")?;
    Ok(std::path::PathBuf::from(home).join(format!("Library/LaunchAgents/{}.plist", label())))
}
#[cfg(target_os = "macos")]
pub fn enabled() -> bool {
    agent().is_ok_and(|path| path.is_file())
}
#[cfg(target_os = "macos")]
pub fn set(enable: bool) -> Result<(), String> {
    let path = agent()?;
    if !enable {
        let _ = std::fs::remove_file(&path);
        return Ok(());
    }
    if let Some(folder) = path.parent() {
        std::fs::create_dir_all(folder).map_err(|_| "Cannot change the startup setting")?;
    }
    let exe = executable()?;
    if exe.contains('<') || exe.contains('&') {
        return Err("Unsupported install path".into());
    }
    let label = label();
    let text = format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\"><dict><key>Label</key><string>{label}</string><key>ProgramArguments</key><array><string>{exe}</string><string>{FLAG}</string></array><key>RunAtLoad</key><true/></dict></plist>\n"
    );
    std::fs::write(&path, text).map_err(|_| "Cannot change the startup setting".into())
}

pub fn launched_at_startup() -> bool {
    std::env::args().any(|argument| argument == FLAG)
}
