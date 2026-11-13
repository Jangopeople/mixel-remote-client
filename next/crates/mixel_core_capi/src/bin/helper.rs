//! `mixel-remote-helper.exe`: the engine program of the Windows app (2.0 and later) for everything the
//! .NET window process cannot be: the administrator steps (sign-in screen and always-on access, remote
//! printer, mass rollout with a deploy token), the SYSTEM service of sign-in screen and always-on access,
//! and the host it starts in the console session. It takes the same arguments as the Tauri app's program
//! did, so the installed service (`MixelRemoteSignIn`) keeps working when an update replaces its program
//! file with this helper (update channel `windows/x64-program`).
#[cfg(target_os = "windows")]
fn main() {
    use mixel_engine::{deploy_token, host_print, service_config, windows_signin_host};
    let mut args = std::env::args().skip(1);
    let Some(command) = args.next() else {
        eprintln!("Mixel Remote helper: started by Mixel Remote; nothing to do.");
        std::process::exit(2);
    };
    let result = match command.as_str() {
        "--version" => {
            println!("Mixel Remote {}", mixel_engine::agent::APP_VERSION);
            Ok(())
        }
        "--validate-embedded-service" => service_config::ServiceConfig::load()
            .map(|_| println!("PASS: embedded public service configuration validated")),
        "--signin-service" => windows_signin_host::run_service(),
        "--signin-host" => service_config::require_host().and_then(|()| windows_signin_host::run_host()),
        "--signin-enroll" => deploy_token::read(args.next().as_deref())
            .and_then(|token| windows_signin_host::admin(&command, Some(&token))),
        other if other.starts_with("--signin-") => windows_signin_host::admin(other, args.next().as_deref()),
        other => host_print::admin(other).unwrap_or_else(|| Err(format!("Unknown command: {other}"))),
    };
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(2);
    }
}

#[cfg(not(target_os = "windows"))]
fn main() {
    eprintln!("The Mixel Remote helper runs on Windows only.");
    std::process::exit(2);
}
