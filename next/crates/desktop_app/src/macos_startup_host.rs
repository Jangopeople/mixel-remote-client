//! Headless managed host for an approved macOS desktop or login window.
use crate::{device_identity::IdentityState, remote_session::RemoteSession};
use serde::Deserialize;
use signaling_server::auth::{EnrolledPrincipal, Principal};
use std::{
    fs::OpenOptions,
    io::Read,
    os::unix::fs::{MetadataExt, OpenOptionsExt},
    path::Path,
    sync::Arc,
    time::Duration,
};
use tokio::signal::unix::{Signal, SignalKind, signal};
use uuid::Uuid;

const MAX_SETTINGS_BYTES: usize = 2048;
const PRELOGIN_SETTINGS: &str = "/Library/Application Support/Mixel Remote/prelogin.json";
const PRELOGIN_BINARY: &str =
    "/Library/Application Support/Mixel Remote/Host/Mixel-Remote.app/Contents/MacOS/Mixel-Remote";

#[derive(Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Settings {
    version: u16,
    device: Uuid,
    profile: Uuid,
    tenant: Uuid,
    connection_id: u32,
    generation: u64,
    share_main_display: bool,
    control: bool,
}

fn settings(path: &Path, uid: u32) -> Result<Settings, String> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(path)
        .map_err(|_| "Cannot open the approved startup host settings")?;
    let metadata = file
        .metadata()
        .map_err(|_| "Cannot inspect startup settings")?;
    if !metadata.is_file()
        || metadata.uid() != uid
        || metadata.mode() & 0o077 != 0
        || metadata.len() > MAX_SETTINGS_BYTES as u64
    {
        return Err(
            "Startup settings must be a private file owned by the account running the host".into(),
        );
    }
    let mut bytes = Vec::new();
    file.take((MAX_SETTINGS_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| "Cannot read startup host settings")?;
    if bytes.len() > MAX_SETTINGS_BYTES {
        return Err("Startup host settings are too large".into());
    }
    let config: Settings =
        serde_json::from_slice(&bytes).map_err(|_| "Startup host settings are invalid")?;
    if config.version != 1
        || config.device.is_nil()
        || config.profile.is_nil()
        || config.tenant.is_nil()
        || !(100_000_000..=999_999_999).contains(&config.connection_id)
        || !(1..=i64::MAX as u64).contains(&config.generation)
        || !config.share_main_display
    {
        return Err(
            "Startup host settings require an enrolled device and explicit main-display approval"
                .into(),
        );
    }
    Ok(config)
}

fn prelogin_settings(path: &Path) -> Result<Settings, String> {
    let config = settings(path, 0)?;
    if !config.control {
        return Err("Login window access requires explicit keyboard and mouse approval".into());
    }
    Ok(config)
}

fn owns_console(uid: u32) -> bool {
    std::fs::metadata("/dev/console").is_ok_and(|console| console.uid() == uid)
}

fn prelogin_installation() -> Result<(), String> {
    if crate::device_identity::prelogin_bundle().is_none()
        || !std::env::current_exe().is_ok_and(|path| path == Path::new(PRELOGIN_BINARY))
    {
        return Err("Install the dedicated signed login host in its system location".into());
    }
    let executable = Path::new(PRELOGIN_BINARY);
    for path in executable.ancestors() {
        let metadata = path
            .symlink_metadata()
            .map_err(|_| "Cannot inspect login host installation")?;
        let expected_type = if path == executable {
            metadata.is_file()
        } else {
            metadata.is_dir()
        };
        if !expected_type || metadata.uid() != 0 || metadata.mode() & 0o022 != 0 {
            return Err(
                "Login host installation must be owned by root and protected from writes".into(),
            );
        }
    }
    Ok(())
}

#[allow(unsafe_code)]
fn graphical_security_session() -> bool {
    #[link(name = "Security", kind = "framework")]
    unsafe extern "C" {
        fn SessionGetInfo(session: u32, id: *mut u32, attributes: *mut u32) -> i32;
    }
    // AuthSession.h: callerSecuritySession = -1; sessionHasGraphicAccess = 0x0010.
    let mut id = 0;
    let mut attributes = 0;
    // SAFETY: both out-parameters point to initialized writable storage of the declared type.
    let status = unsafe { SessionGetInfo(u32::MAX, &mut id, &mut attributes) };
    status == 0 && attributes & 0x0010 != 0
}

fn permissions(control: bool) -> bool {
    native_os_capture::macos::screen_access()
        && (!control || native_os_capture::macos_input::MacInjector::post_access())
}

async fn wait_for_prelogin_permissions(
    path: &Path,
    config: &Settings,
    mut term: Signal,
    mut interrupt: Signal,
) -> Result<Option<(Signal, Signal)>, String> {
    let display = native_os_capture::macos::main_display();
    println!(
        "Mixel Remote login host running: uid=0, console owner=0, display={display}, pid={}",
        std::process::id()
    );
    println!(
        "Screen recording preflight: {}",
        native_os_capture::macos::screen_access()
    );
    println!(
        "Input posting preflight: {}",
        native_os_capture::macos_input::MacInjector::post_access()
    );
    if display != 0 {
        let probe = tokio::task::spawn_blocking(move || {
            native_os_capture::macos::probe_login_display(display)
        })
        .await
        .map_err(|_| "Login screen permission probe stopped")?;
        match probe {
            Ok(()) => println!("ScreenCaptureKit enumerated the login display"),
            Err(error) => eprintln!("Login window ScreenCaptureKit: {error}"),
        }
    }
    let keyboard_entitled = native_os_capture::macos_input::MacInjector::login_keyboard_entitled();
    if !keyboard_entitled {
        eprintln!(
            "Login window keyboard blocked: missing Apple entitlement com.apple.developer.hid.virtual.device"
        );
    }
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    loop {
        tokio::select! {
            _ = term.recv() => return Ok(None),
            _ = interrupt.recv() => return Ok(None),
            _ = tick.tick() => {
                if !owns_console(0) || !settings(path, 0).is_ok_and(|current| current == *config) {
                    return Ok(None);
                }
                if keyboard_entitled && native_os_capture::macos::main_display() != 0 && permissions(config.control) {
                    return Ok(Some((term, interrupt)));
                }
            }
        }
    }
}

pub fn run() -> Result<(), String> {
    if crate::service_config::has_service_overrides() {
        return Err("Startup host requires its signed bundle configuration".into());
    }
    let mut args = std::env::args_os().skip(1);
    let mode = args.next().ok_or("Startup host mode is missing")?;
    let prelogin = mode == "--prelogin-host";
    let provision = mode == "--provision-prelogin-identity";
    let validate = mode == "--validate-prelogin-settings";
    // SAFETY: geteuid only reads the effective user ID of this process.
    #[allow(unsafe_code)]
    let uid = unsafe { libc::geteuid() };
    if (uid == 0) != (prelogin || provision || validate) {
        return Err(
            "Login window mode requires root; desktop mode requires the graphical user".into(),
        );
    }
    if validate {
        let path = args
            .next()
            .ok_or("Specify the private login host settings path")?;
        if args.next().is_some() {
            return Err("Login host validation accepts exactly one settings path".into());
        }
        prelogin_settings(Path::new(&path))?;
        return Ok(());
    }
    let identity = Arc::new(IdentityState::default());
    if prelogin || provision {
        prelogin_installation()?;
        if args.next().is_some() {
            return Err("Login window mode uses its fixed system settings location".into());
        }
        if provision {
            identity.open_prelogin(true)?;
            let key = identity
                .public_key()?
                .to_bytes()
                .map(|byte| format!("{byte:02x}"))
                .join("");
            println!("{key}");
            return Ok(());
        }
        if !graphical_security_session() {
            return Err("Login window mode requires a graphical security session".into());
        }
    }
    let path_arg = if prelogin {
        PRELOGIN_SETTINGS.into()
    } else {
        let path = args
            .next()
            .ok_or("Specify the private startup host settings path")?;
        if args.next().is_some() {
            return Err("Startup host accepts exactly one settings path".into());
        }
        path
    };
    let path = Path::new(&path_arg);
    let config = if prelogin {
        prelogin_settings(path)?
    } else {
        settings(path, uid)?
    };
    if !owns_console(uid) {
        return Err("Startup host requires its approved account to own the console".into());
    }
    if prelogin {
        identity.open_prelogin(false)?;
        println!("Machine identity loaded from System Keychain");
    } else {
        if native_os_capture::macos::main_display() == 0 || !permissions(config.control) {
            return Err("Startup host requires an active graphical session and approved macOS sharing permissions".into());
        }
        identity.open(false)?;
    }
    let enrolled = EnrolledPrincipal {
        principal: Principal {
            user: config.profile,
            tenant: Some(config.tenant),
            staff: false,
            expires_at: 0,
        },
        device: config.device,
        connection_id: config.connection_id,
        generation: config.generation,
        public_key: identity.public_key()?,
        identity_ticket: None,
    };
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .map_err(|_| "Cannot start the managed host runtime")?;
    let serve = || {
        runtime.block_on(async {
            let term =
                signal(SignalKind::terminate()).map_err(|_| "Cannot watch host termination")?;
            let interrupt =
                signal(SignalKind::interrupt()).map_err(|_| "Cannot watch host interruption")?;
            let (mut term, mut interrupt) = if prelogin {
                match wait_for_prelogin_permissions(path, &config, term, interrupt).await? {
                    Some(signals) => signals,
                    None => return Ok(()),
                }
            } else {
                (term, interrupt)
            };
            let display = native_os_capture::macos::main_display();
            let remote = RemoteSession::default();
            remote
                .connect_startup(enrolled, display, config.control, identity)
                .await?;
            let mut tick = tokio::time::interval(Duration::from_millis(500));
            let mut ready = false;
            loop {
                tokio::select! {
                    _ = term.recv() => return remote.stop().await,
                    _ = interrupt.recv() => return remote.stop().await,
                    _ = tick.tick() => {
                        if !owns_console(uid)
                            || native_os_capture::macos::main_display() != display
                            || !permissions(config.control)
                            || !settings(path, uid).is_ok_and(|current| current == config)
                        {
                            return remote.stop().await;
                        }
                        let status = remote.status().await;
                        if status.unattended_enabled && !ready {
                            println!("Ready for policy-approved unattended access");
                        }
                        ready = status.unattended_enabled;
                        if matches!(status.phase.as_str(), "error" | "offline") {
                            remote.stop().await?;
                            return Err(status.message);
                        }
                    }
                }
            }
        })
    };
    if prelogin {
        native_os_capture::macos::run_login_window(serve)?
    } else {
        serve()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{PermissionsExt, symlink};

    #[test]
    fn startup_configuration_is_private_explicit_and_refuses_symlinks() {
        let file = tempfile::NamedTempFile::new().unwrap();
        let uid = file.as_file().metadata().unwrap().uid();
        let value = serde_json::json!({
            "version":1,"device":Uuid::from_u128(1),"profile":Uuid::from_u128(2),
            "tenant":Uuid::from_u128(3),"connection_id":123456789,"generation":1,
            "share_main_display":true,"control":false,
        });
        std::fs::write(file.path(), serde_json::to_vec(&value).unwrap()).unwrap();
        assert!(settings(file.path(), uid).is_ok());
        assert!(settings(file.path(), uid ^ 1).is_err());
        let directory = tempfile::tempdir().unwrap();
        let link = directory.path().join("startup.json");
        symlink(file.path(), &link).unwrap();
        assert!(settings(&link, uid).is_err());
        std::fs::set_permissions(file.path(), std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(settings(file.path(), uid).is_err());
        std::fs::set_permissions(file.path(), std::fs::Permissions::from_mode(0o600)).unwrap();
        let mut value = value;
        value["share_main_display"] = false.into();
        std::fs::write(file.path(), serde_json::to_vec(&value).unwrap()).unwrap();
        assert!(settings(file.path(), uid).is_err());
    }
}
