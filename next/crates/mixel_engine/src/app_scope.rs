//! Which program runs the engine's administrator steps and the sign-in screen service.
//!
//! The Tauri app (up to 0.1.x) is one Rust program: it runs its own `--signin-*` and `--printer-*`
//! steps elevated and copies itself into Program Files for the service. From 2.0.0 the Windows window
//! process is .NET (WinUI 3); the same engine steps run in `mixel-remote-helper.exe`, a small Rust
//! program installed next to it. The window process calls [`set_helper_program`] once at start, so
//! elevated steps, the service copy and its outdated check use the helper. Names, folders, the device
//! identity, the service and the printer stay exactly those of the installed app, so an update keeps
//! the computer's enrolment, key and always-on access.
use std::sync::atomic::{AtomicBool, Ordering};

static HELPER_PROGRAM: AtomicBool = AtomicBool::new(false);

/// The engine program next to the Windows app (also the program the always-on service runs).
pub const HELPER: &str = "mixel-remote-helper.exe";

/// Marks this process as the .NET window process: engine programs are the helper next to it.
pub fn set_helper_program() {
    HELPER_PROGRAM.store(true, Ordering::Release);
}

pub fn helper_program() -> bool {
    HELPER_PROGRAM.load(Ordering::Acquire)
}

/// The program for administrator steps and for the copy the sign-in screen service runs: this
/// executable, or in the WinUI app the helper next to it.
pub fn program() -> Result<std::path::PathBuf, String> {
    let exe = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
    if !helper_program() {
        return Ok(exe);
    }
    helper_next_to(&exe)
}

fn helper_next_to(exe: &std::path::Path) -> Result<std::path::PathBuf, String> {
    let helper = exe.parent().ok_or("Cannot locate Mixel Remote")?.join(HELPER);
    if helper.is_file() { Ok(helper) } else { Err("The Mixel Remote helper program is missing; reinstall Mixel Remote".into()) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn helper_is_found_next_to_the_app() {
        let folder = std::env::temp_dir().join(format!("mixel-scope-{}", std::process::id()));
        std::fs::create_dir_all(&folder).unwrap();
        let app = folder.join("mixel-remote.exe");
        assert!(helper_next_to(&app).is_err());
        std::fs::write(folder.join(HELPER), b"x").unwrap();
        assert_eq!(helper_next_to(&app).unwrap(), folder.join(HELPER));
        let _ = std::fs::remove_dir_all(folder);
    }

    #[test]
    fn a_rust_program_runs_its_own_steps() {
        if !helper_program() {
            assert_eq!(program().unwrap(), std::env::current_exe().unwrap());
        }
    }
}
