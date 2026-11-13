//! LAB ONLY: the Linux always-on host of the desktop app without its window, for live checks of
//! the controller apps on an isolated test computer (an Xvfb display works). Run as root:
//!
//! ```text
//! # in a private mount namespace, so the VM's own /var/lib/mixel-remote (if any) is never touched:
//! unshare --mount sh -c 'mount --bind <own folder> /var/lib/mixel-remote &&
//!   MIXEL_DEPLOY_TOKEN=mrt_… lab_linux_host --lab-enroll &&          # key + settings only, no service
//!   DISPLAY=:99 lab_linux_host --signin-host'                         # the host on an isolated display
//! ```
//! Build with `MIXEL_DESKTOP_SERVICE_CONFIG=deployment/public-service-config.json` (the public service).

#[cfg(target_os = "linux")]
fn main() {
    use mixel_engine::linux_signin_host as host;
    let mut args = std::env::args().skip(1);
    let result = match args.next().as_deref() {
        Some("--signin-service") => host::run_service(),
        Some("--signin-host") => mixel_engine::service_config::require_host().and_then(|()| host::run_host()),
        Some("--signin-enroll") => std::env::var("MIXEL_DEPLOY_TOKEN")
            .map_err(|_| "Set MIXEL_DEPLOY_TOKEN".to_owned())
            .and_then(|token| host::admin("--signin-enroll", Some(&token))),
        Some("--signin-remove") => host::admin("--signin-remove", None),
        Some("--lab-enroll") => enroll(),
        _ => Err("Use --signin-enroll, --signin-host, --signin-service or --signin-remove".into()),
    };
    if let Err(error) = result {
        eprintln!("lab_linux_host: {error}");
        std::process::exit(1);
    }
}

/// Enrols with a deploy token and writes the host settings, without installing or starting a service.
#[cfg(target_os = "linux")]
fn enroll() -> Result<(), String> {
    use mixel_engine::{device_identity::IdentityState, linux_signin_host as host};
    let token = std::env::var("MIXEL_DEPLOY_TOKEN").map_err(|_| "Set MIXEL_DEPLOY_TOKEN")?;
    let identity = IdentityState::machine(true)?;
    let key = identity.public_key()?;
    let config = mixel_engine::service_config::ServiceConfig::load()?;
    let ism = signaling_server::auth::IsmAuth::new(config.identity_url.parse().map_err(|_| "Invalid identity URL")?, config.public_key)
        .map_err(|_| "Invalid identity service configuration")?;
    let name = std::fs::read_to_string("/etc/hostname").unwrap_or_default().trim().to_owned();
    let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().map_err(|_| "No runtime")?;
    let enrolled = runtime
        .block_on(ism.enroll_with_rollout_token(token.trim(), &format!("{name} (lab)"), "linux", &key))
        .map_err(|_| mixel_engine::ROLLOUT_REFUSED.to_owned())?;
    let settings = host::Settings {
        version: 1,
        device: enrolled.device_id,
        profile: enrolled.profile,
        tenant: enrolled.tenant,
        connection_id: enrolled.connection_id,
        generation: enrolled.generation,
        control: true,
        always_on: true,
    };
    let bytes = serde_json::to_vec(&settings).map_err(|_| "Invalid settings")?;
    host::write_private(&host::machine_identity_path().with_file_name("signin-host.json"), &bytes)?;
    println!("lab host enrolled with Remote ID {}", enrolled.connection_id);
    Ok(())
}

#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("lab_linux_host runs on Linux only");
}
