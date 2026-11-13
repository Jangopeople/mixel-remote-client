//! Mixel Remote engine: everything the desktop app does without a window toolkit
//! (accounts, devices, sessions, files, chat, recording, the host side). The Tauri app and
//! the native apps (through `mixel_core_ffi`) both drive it.
/// The commands of the native apps (same names and arguments as the desktop app's window commands).
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod native_commands;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
pub mod session_platform;
/// iPhone, iPad and Android phones and tablets: controller only.
#[cfg(any(target_os = "ios", target_os = "android"))]
#[path = "session_platform_ios.rs"]
pub mod session_platform;
#[cfg(target_os = "linux")]
pub mod wayland_portal;
#[cfg(target_os = "linux")]
pub mod linux_guard;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod account_auth;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod device_identity;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod session_store;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod host_actions;
pub mod session_guard;
pub mod session_mirror;
pub mod host_print;
#[cfg(any(target_os = "macos", target_os = "linux"))]
pub mod ipp_printer;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod agent;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod ai_assistant;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod self_update;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
pub mod autostart;
#[cfg(target_os = "macos")]
pub mod local_session;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod remote_chat;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod remote_clipboard;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod remote_control;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod remote_file_transport;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod remote_files;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod remote_peer;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod remote_recording;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod remote_recovery;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod remote_session;
pub mod remote_sound;
#[cfg(target_os = "macos")]
pub mod macos_startup_host;
#[cfg(target_os = "macos")]
pub mod macos_rollout;
#[cfg(target_os = "macos")]
pub mod macos_prelogin_install;
#[cfg(target_os = "windows")]
pub mod windows_signin_host;
#[cfg(target_os = "linux")]
pub mod linux_signin_host;
/// `--signin-enroll` reads the deploy token from MIXEL_DEPLOY_TOKEN or standard input.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod deploy_token;
#[cfg(target_os = "macos")]
pub mod macos_support;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod device_tickets;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod remote_video;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios", target_os = "android"))]
pub mod service_config;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
pub mod system_browser;
/// Android: secrets (device key, remembered sign-in) in the app's Keystore-sealed storage.
#[cfg(target_os = "android")]
pub mod android_secrets;

/// Screen-capture worker shared by the windowed apps and the session host.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
pub mod capture_worker;
/// Mass rollout commands (Windows PowerShell, Mac and Linux install scripts).
pub mod rollout;
/// Fixed addresses of the legal pages.
pub mod legal;
/// Offline-first removal of this computer, signed with its own device key.
pub mod self_removal;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
pub use capture_worker::Worker;
#[cfg(any(target_os = "windows", target_os = "linux"))]
pub use capture_worker::PlatformDisplay;

/// Release version parts ("0.1.22" -> [0, 1, 22]); `None` for anything else.
pub fn version_parts(version: &str) -> Option<Vec<u64>> {
    let core = version.split(['-', '+']).next()?;
    let parts: Option<Vec<u64>> = core.split('.').map(|part| part.parse().ok()).collect();
    parts.filter(|parts| (1..=4).contains(&parts.len()))
}

/// What a refused rollout enrolment says (the service does not tell the device why).
pub const ROLLOUT_REFUSED: &str = "The deploy link was refused or the service could not be reached. Check that the link is not expired, revoked or used up and that the company is within its device limit, then run the command again.";

#[cfg(test)]
mod update_version_tests {
    #[test]
    fn compares_release_versions_numerically() {
        assert!(crate::version_parts("0.1.10") > crate::version_parts("0.1.9"));
        assert_eq!(crate::version_parts("1.4.6-support.elevate.1"), Some(vec![1, 4, 6]));
        assert_eq!(crate::version_parts("abc"), None);
    }
}

