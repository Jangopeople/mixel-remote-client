//! Mixel Remote for Linux: a native GTK 4 + libadwaita app on the shared Rust engine
//! (`mixel_engine`), without a web view and without a foreign-language binding. From 2.0.0 it is the
//! Linux app: same package (`mixel-remote`), same update channel, and it keeps what the 0.1.x app
//! (Tauri) set up on a computer: the device key and remembered sign-in (Secret Service), the
//! always-on / sign-in screen service, the start-up entry and the app's settings.
#[cfg(target_os = "linux")]
mod app;
#[cfg(target_os = "linux")]
mod engine;
#[cfg(target_os = "linux")]
mod format;
#[cfg(target_os = "linux")]
mod i18n;
#[cfg(target_os = "linux")]
mod keymap;
#[cfg(all(target_os = "linux", feature = "qa-session"))]
mod live_check;
#[cfg(target_os = "linux")]
mod logic;
#[cfg(target_os = "linux")]
mod mock;
#[cfg(target_os = "linux")]
mod report_export;
#[cfg(target_os = "linux")]
mod models;
#[cfg(target_os = "linux")]
mod screenshots;
#[cfg(target_os = "linux")]
mod store;
#[cfg(target_os = "linux")]
mod tray;
#[cfg(target_os = "linux")]
mod ui;
#[cfg(target_os = "linux")]
mod video;

/// The application id: D-Bus name, desktop file, icon, settings folder.
#[cfg(target_os = "linux")]
pub const APP_ID: &str = "ch.mixel.remote";
/// The 0.1.x app's identifier: its data folder (a queued removal of this computer) and settings.
#[cfg(target_os = "linux")]
pub const LEGACY_ID: &str = "ch.mixel.remote.next.qa";

#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("Mixel Remote for Linux runs on Linux only.");
    std::process::exit(2);
}

#[cfg(target_os = "linux")]
fn main() {
    use mixel_engine::{deploy_token, linux_guard, linux_signin_host, service_config};
    let args: Vec<String> = std::env::args().collect();
    let first = args.get(1).map(String::as_str);
    let exit = |result: Result<(), String>| -> ! {
        match result {
            Ok(()) => std::process::exit(0),
            Err(error) => {
                eprintln!("{error}");
                std::process::exit(2);
            }
        }
    };
    match first {
        // The release version for install checks and support; no window, no service.
        Some("--version") => {
            println!("Mixel Remote {}", mixel_engine::agent::APP_VERSION);
            return;
        }
        // Packaging check of the embedded public service configuration (no window, no network).
        Some("--validate-embedded-service") => exit(
            service_config::ServiceConfig::load().map(|_| println!("PASS: embedded public service configuration validated")),
        ),
        // Sign-in screen access: the root service, its host and the administrator steps (pkexec).
        Some(command) if command.starts_with("--signin-") => {
            let command = command.to_owned();
            let argument = args.get(2).map(String::as_str);
            exit(match command.as_str() {
                "--signin-service" => linux_signin_host::run_service(),
                "--signin-host" => service_config::require_host().and_then(|()| linux_signin_host::run_host()),
                "--signin-enroll" => deploy_token::read(argument).and_then(|token| linux_signin_host::admin(&command, Some(&token))),
                _ => linux_signin_host::admin(&command, argument),
            })
        }
        // The helper that gives screen and input back if this app ends while they are switched off.
        Some(argument) if argument == linux_guard::RESTORE_ARGUMENT => {
            let app = args.get(2).and_then(|pid| pid.parse::<u32>().ok()).unwrap_or(0);
            if app == 0 || linux_guard::watch_and_restore(app).is_err() {
                std::process::exit(2);
            }
            return;
        }
        // Every screen in light and dark, rendered from demo data (no account, no network).
        Some("--screenshots") => {
            let folder = args.get(2).cloned().unwrap_or_else(|| "screenshots".into());
            let languages = args.get(3).cloned().unwrap_or_else(|| "en".into());
            std::process::exit(screenshots::run(std::path::PathBuf::from(folder), &languages));
        }
        #[cfg(feature = "qa-session")]
        Some("--live-check" | "--live-session" | "--live-host" | "--live-pair" | "--live-colleague" | "--live-watch" | "--check-providers") => std::process::exit(live_check::run(&args[1..])),
        _ => {}
    }
    linux_guard::recover_stale();
    std::process::exit(app::run(args.iter().any(|argument| argument == mixel_engine::autostart::FLAG)));
}
