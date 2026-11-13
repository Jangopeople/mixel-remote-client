//! Linux sign-in screen access. A root systemd service starts a headless host on the sign-in
//! screen (the display manager's greeter) while nobody is signed in on the computer's seat, and
//! stops it as soon as someone signs in (the normal app then takes over). The host uses its own
//! device record, Remote ID and computer-wide key, and only accepts policy-approved unattended
//! sessions, like the Windows sign-in screen host.
//!
//! Only an X11 greeter can be shared: GDM with `WaylandEnable=false`, LightDM, SDDM on X11. A
//! Wayland greeter (the GDM default) lets no other program see or control it; the status then
//! says so and nothing runs.
//!
//! Mass rollout (`--signin-enroll <deploy token>`, run as root by the install script
//! `https://remote.mixel.ch/install/linux.sh`): the computer enrols its computer-wide key with the
//! token and the same service runs **always on**: on the sign-in screen and also in the signed-in
//! person's X11 session (Wayland sessions cannot be shared without a person approving it on screen).
use crate::device_identity::IdentityState;
use serde::{Deserialize, Serialize};
use signaling_server::auth::{EnrolledPrincipal, Principal};
use std::{
    os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use uuid::Uuid;

pub const UNIT: &str = "mixel-remote-signin.service";

fn folder() -> PathBuf {
    PathBuf::from("/var/lib/mixel-remote")
}
pub fn machine_identity_path() -> PathBuf {
    folder().join("machine-identity-v1")
}
fn settings_path() -> PathBuf {
    folder().join("signin-host.json")
}
fn public_key_path() -> PathBuf {
    folder().join("machine-identity.pub")
}
fn public_status_path() -> PathBuf {
    folder().join("signin-host-status.json")
}
fn state_path() -> PathBuf {
    folder().join("signin-host-state")
}
fn program_path() -> PathBuf {
    PathBuf::from("/usr/lib/mixel-remote-signin/mixel-remote")
}
fn unit_path() -> PathBuf {
    PathBuf::from("/etc/systemd/system").join(UNIT)
}

fn log(message: &str) {
    use std::io::Write;
    let path = folder().join("signin-host.log");
    if std::fs::metadata(&path).is_ok_and(|meta| meta.len() > 256 * 1024) {
        let _ = std::fs::rename(&path, folder().join("signin-host.old.log"));
    }
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).mode(0o600).open(path) {
        let seconds = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |time| time.as_secs());
        let _ = writeln!(file, "{seconds} {message}");
    }
}

fn root() -> bool {
    // SAFETY: geteuid only reads the effective user ID of this process.
    #[allow(unsafe_code)]
    unsafe {
        libc::geteuid() == 0
    }
}

#[derive(Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Debug)]
#[serde(deny_unknown_fields)]
pub struct Settings {
    pub version: u16,
    pub device: Uuid,
    pub profile: Uuid,
    pub tenant: Uuid,
    pub connection_id: u32,
    pub generation: u64,
    pub control: bool,
    /// Rolled out with a deploy link: share the signed-in X11 session too, not only the sign-in screen.
    #[serde(default)]
    pub always_on: bool,
}
impl Settings {
    fn valid(&self) -> bool {
        self.version == 1
            && !self.device.is_nil()
            && !self.profile.is_nil()
            && !self.tenant.is_nil()
            && (100_000_000..=999_999_999).contains(&self.connection_id)
            && (1..=i64::MAX as u64).contains(&self.generation)
    }
}
#[derive(Serialize, Deserialize)]
pub struct PublicStatus {
    pub connection_id: u32,
    pub name: String,
}

/// Status the normal app may read: the sign-in screen Remote ID, if installed.
pub fn public_status() -> Option<PublicStatus> {
    serde_json::from_slice(&std::fs::read(public_status_path()).ok()?).ok()
}

/// What the service saw last: `running`, `waiting` (someone is signed in) or `wayland`.
pub fn service_state() -> Option<String> {
    std::fs::read_to_string(state_path()).ok().map(|text| text.trim().to_owned()).filter(|text| text.len() <= 32)
}

/// A technician is connected through the sign-in screen host right now.
pub fn technician_connected() -> bool {
    service_state().as_deref() == Some("active")
}

/// The installed copy is older than this app.
pub fn program_outdated() -> bool {
    let Ok(current) = std::env::current_exe().and_then(std::fs::read) else { return false };
    std::fs::read(program_path()).is_ok_and(|installed| installed != current)
}

/// Runs a step as administrator through the system's prompt (pkexec).
pub fn run_elevated(arguments: &str) -> Result<(), String> {
    let program = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
    let status = std::process::Command::new("pkexec")
        .arg(program)
        .args(arguments.split(' ').filter(|part| !part.is_empty()))
        .status()
        .map_err(|_| "The administrator prompt (pkexec) is not available on this computer")?;
    match status.code() {
        Some(0) => Ok(()),
        Some(126 | 127) => Err("Administrator approval was declined".into()),
        _ => Err("The administrator step failed".into()),
    }
}

pub fn provisioned_public_key() -> Result<ed25519_dalek::VerifyingKey, String> {
    let text = std::fs::read_to_string(public_key_path()).map_err(|_| "The sign-in screen key was not created")?;
    let bytes: Vec<u8> = (0..text.trim().len())
        .step_by(2)
        .map(|index| u8::from_str_radix(text.trim().get(index..index + 2).unwrap_or("zz"), 16))
        .collect::<Result<_, _>>()
        .map_err(|_| "The sign-in screen key is invalid")?;
    let bytes: [u8; 32] = bytes.try_into().map_err(|_| "The sign-in screen key is invalid")?;
    ed25519_dalek::VerifyingKey::from_bytes(&bytes).map_err(|_| "The sign-in screen key is invalid".into())
}

/// Writes a file only root can read or change (atomically).
pub fn write_private(path: &Path, bytes: &[u8]) -> Result<(), String> {
    write_mode(path, bytes, 0o600)
}

fn write_mode(path: &Path, bytes: &[u8], mode: u32) -> Result<(), String> {
    use std::io::Write;
    let temporary = path.with_extension("tmp");
    let _ = std::fs::remove_file(&temporary);
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&temporary)
        .map_err(|_| "Cannot write the sign-in screen settings")?;
    file.write_all(bytes).map_err(|_| "Cannot write the sign-in screen settings")?;
    file.sync_all().map_err(|_| "Cannot write the sign-in screen settings")?;
    std::fs::rename(&temporary, path).map_err(|_| "Cannot write the sign-in screen settings".into())
}

/// The data folder belongs to root and nobody else may change it.
fn secure_folder() -> Result<(), String> {
    std::fs::create_dir_all(folder()).map_err(|_| "Cannot create the Mixel Remote data folder")?;
    let metadata = std::fs::symlink_metadata(folder()).map_err(|_| "Cannot inspect the Mixel Remote data folder")?;
    if !metadata.is_dir() || metadata.uid() != 0 {
        return Err("The Mixel Remote data folder is not owned by root".into());
    }
    std::fs::set_permissions(folder(), std::fs::Permissions::from_mode(0o755)).map_err(|_| "Cannot protect the Mixel Remote data folder".into())
}

fn systemctl(arguments: &[&str]) -> bool {
    std::process::Command::new("systemctl").args(arguments).output().is_ok_and(|output| output.status.success())
}

/// The systemd unit of the sign-in screen service.
pub fn unit_text() -> String {
    format!(
        "[Unit]\nDescription=Mixel Remote sign-in screen access\nAfter=display-manager.service network-online.target\nWants=network-online.target\n\n\
         [Service]\nType=simple\nExecStart={} --signin-service\nRestart=always\nRestartSec=5\nNoNewPrivileges=yes\nProtectHome=read-only\nPrivateTmp=yes\n\n\
         [Install]\nWantedBy=graphical.target\n",
        program_path().display()
    )
}

#[derive(Deserialize)]
struct Install {
    settings: Settings,
    name: String,
}

fn decode_hex(hex: &str) -> Result<Vec<u8>, String> {
    if hex.len() > 8192 || !hex.len().is_multiple_of(2) {
        return Err("Invalid settings".into());
    }
    (0..hex.len()).step_by(2).map(|index| u8::from_str_radix(&hex[index..index + 2], 16)).collect::<Result<_, _>>().map_err(|_| "Invalid settings".into())
}

/// Installs the settings, a root-owned copy of the program and the service, and starts it.
fn install_settings(install: &Install) -> Result<(), String> {
    if !install.settings.valid() || install.name.len() > 200 {
        return Err("Invalid settings".into());
    }
    IdentityState::machine(false)?;
    write_private(&settings_path(), &serde_json::to_vec(&install.settings).map_err(|_| "Invalid settings")?)?;
    write_mode(
        &public_status_path(),
        &serde_json::to_vec(&PublicStatus { connection_id: install.settings.connection_id, name: install.name.clone() }).map_err(|_| "Invalid settings")?,
        0o644,
    )?;
    // The root service runs a copy only root can change, never a file a user could replace.
    let _ = systemctl(&["stop", UNIT]);
    let exe = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
    let program = program_path();
    let parent = program.parent().ok_or("Invalid program folder")?;
    std::fs::create_dir_all(parent).map_err(|_| "Cannot create the sign-in screen program folder")?;
    std::fs::set_permissions(parent, std::fs::Permissions::from_mode(0o755)).map_err(|_| "Cannot protect the sign-in screen program folder")?;
    if exe != program {
        let bytes = std::fs::read(&exe).map_err(|_| "Cannot read Mixel Remote")?;
        write_mode(&program, &bytes, 0o755)?;
    }
    write_mode(&unit_path(), unit_text().as_bytes(), 0o644)?;
    if !systemctl(&["daemon-reload"]) || !systemctl(&["enable", "--now", UNIT]) {
        return Err("The sign-in screen service could not be started".into());
    }
    log(&format!("installed for Remote ID {}", install.settings.connection_id));
    Ok(())
}

/// The computer's name for the device list: the host name, without control characters.
fn computer_name() -> String {
    let raw = std::fs::read_to_string("/proc/sys/kernel/hostname").unwrap_or_default();
    let name: String = raw.trim().chars().filter(|c| !c.is_control()).take(60).collect();
    if name.is_empty() { "Linux computer".into() } else { name }
}

fn format_id(id: u32) -> String {
    let digits = id.to_string();
    digits.as_bytes().chunks(3).map(|chunk| String::from_utf8_lossy(chunk).into_owned()).collect::<Vec<_>>().join(" ")
}

/// Administrator steps (run through `run_elevated`).
pub fn admin(command: &str, argument: Option<&str>) -> Result<(), String> {
    if !root() {
        return Err("This step needs administrator rights".into());
    }
    secure_folder()?;
    match command {
        "--signin-provision" => {
            let identity = IdentityState::machine(true)?;
            let hex: String = identity.public_key()?.as_bytes().iter().map(|byte| format!("{byte:02x}")).collect();
            write_mode(&public_key_path(), hex.as_bytes(), 0o644)
        }
        "--signin-install" => {
            let install: Install = serde_json::from_slice(&decode_hex(argument.ok_or("Missing settings")?)?).map_err(|_| "Invalid settings")?;
            install_settings(&install)
        }
        "--signin-enroll" => {
            // Rollout: enrol this computer with a company deploy token and turn on always-on access.
            let secret = argument.ok_or("Missing deploy token")?.trim().to_owned();
            if !(20..=100).contains(&secret.len()) || !secret.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte)) {
                return Err("The deploy token is invalid".into());
            }
            let name = computer_name();
            let identity = IdentityState::machine(true)?;
            let key = identity.public_key()?;
            let hex: String = key.as_bytes().iter().map(|byte| format!("{byte:02x}")).collect();
            write_mode(&public_key_path(), hex.as_bytes(), 0o644)?;
            let config = crate::service_config::ServiceConfig::load()?;
            let base = config.identity_url.parse().map_err(|_| "Invalid identity service configuration")?;
            let ism = signaling_server::auth::IsmAuth::new(base, config.public_key).map_err(|_| "Invalid identity service configuration")?;
            let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().map_err(|_| "Cannot start the rollout")?;
            let enrolled = runtime
                .block_on(ism.enroll_with_rollout_token(&secret, &name, "linux", &key))
                .map_err(|_| crate::ROLLOUT_REFUSED)?;
            let settings = Settings {
                version: 1,
                device: enrolled.device_id,
                profile: enrolled.profile,
                tenant: enrolled.tenant,
                connection_id: enrolled.connection_id,
                generation: enrolled.generation,
                control: true,
                always_on: true,
            };
            install_settings(&Install { settings, name: name.clone() })?;
            log(&format!("rolled out as Remote ID {}", enrolled.connection_id));
            println!("Mixel Remote: {name} was added with Remote ID {}", format_id(enrolled.connection_id));
            Ok(())
        }
        "--signin-update" => {
            let exe = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
            if exe != program_path() {
                let bytes = std::fs::read(&exe).map_err(|_| "Cannot read Mixel Remote")?;
                write_mode(&program_path(), &bytes, 0o755)?;
            }
            if systemctl(&["restart", UNIT]) { Ok(()) } else { Err("The sign-in screen service could not be restarted".into()) }
        }
        "--signin-remove" => {
            let _ = systemctl(&["disable", "--now", UNIT]);
            for path in [unit_path(), settings_path(), public_status_path(), machine_identity_path(), public_key_path(), state_path(), program_path()] {
                let _ = std::fs::remove_file(path);
            }
            let _ = systemctl(&["daemon-reload"]);
            if unit_path().exists() || settings_path().exists() { Err("Sign-in screen access could not be removed".into()) } else { Ok(()) }
        }
        _ => Err("Unknown sign-in screen step".into()),
    }
}

/// One login session as `loginctl show-session` describes it.
#[derive(Default, Debug, PartialEq, Eq)]
pub struct LoginSession {
    pub class: String,
    pub kind: String,
    pub display: String,
    pub seat: String,
    pub state: String,
    pub remote: bool,
}

/// Parses `loginctl show-session -p Class -p Type -p Display -p Seat -p State -p Remote`.
pub fn parse_session(text: &str) -> LoginSession {
    let mut session = LoginSession::default();
    for line in text.lines() {
        let Some((key, value)) = line.split_once('=') else { continue };
        let value = value.trim().to_owned();
        match key {
            "Class" => session.class = value,
            "Type" => session.kind = value,
            "Display" => session.display = value,
            "Seat" => session.seat = value,
            "State" => session.state = value,
            "Remote" => session.remote = value == "yes",
            _ => {}
        }
    }
    session
}

fn sessions() -> Vec<LoginSession> {
    let Ok(output) = std::process::Command::new("loginctl").args(["list-sessions", "--no-legend"]).output() else { return Vec::new() };
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| line.split_whitespace().next().map(str::to_owned))
        .filter(|id| id.chars().all(|c| c.is_ascii_alphanumeric()) && id.len() <= 16)
        .take(64)
        .filter_map(|id| {
            let output = std::process::Command::new("loginctl")
                .args(["show-session", &id, "-p", "Class", "-p", "Type", "-p", "Display", "-p", "Seat", "-p", "State", "-p", "Remote"])
                .output()
                .ok()?;
            Some(parse_session(&String::from_utf8_lossy(&output.stdout)))
        })
        .collect()
}

#[derive(Debug, PartialEq, Eq)]
pub enum Screen {
    /// The X11 greeter on this display: share it.
    Greeter(String),
    /// The greeter runs on Wayland: nothing can be shared.
    Wayland,
    /// Someone is signed in on the seat (or no greeter yet).
    Waiting,
}

/// What the service should do for these login sessions. `always_on` (rolled out with a deploy
/// link): a signed-in local X11 session is shared too; a Wayland session cannot be.
pub fn decide(sessions: &[LoginSession], always_on: bool) -> Screen {
    let local = |session: &&LoginSession| session.seat == "seat0" && !session.remote;
    let signed_in = |session: &&LoginSession| session.class == "user" && matches!(session.state.as_str(), "active" | "online");
    if let Some(user) = sessions.iter().filter(local).filter(signed_in).find(|session| session.state == "active")
        .or_else(|| sessions.iter().filter(local).find(signed_in))
    {
        return match (always_on, user.kind.as_str()) {
            (true, "x11") if user.display.is_empty() || user.display.starts_with(':') => Screen::Greeter(user.display.clone()),
            (true, "wayland") => Screen::Wayland,
            _ => Screen::Waiting,
        };
    }
    match sessions.iter().filter(local).find(|session| session.class == "greeter") {
        // GDM leaves Display empty for its greeter: the service then finds it from the X server.
        Some(greeter) if greeter.kind == "x11" && (greeter.display.is_empty() || greeter.display.starts_with(':')) => Screen::Greeter(greeter.display.clone()),
        Some(greeter) if greeter.kind == "wayland" => Screen::Wayland,
        _ => Screen::Waiting,
    }
}

/// Always-on access (rollout) as recorded in the installed settings.
fn always_on() -> bool {
    std::fs::read(settings_path())
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Settings>(&bytes).ok())
        .is_some_and(|settings| settings.always_on)
}

/// X authority files the running X servers were started with (`-auth <file>`).
fn authority_files() -> Vec<PathBuf> {
    let mut found = Vec::new();
    let Ok(entries) = std::fs::read_dir("/proc") else { return found };
    for entry in entries.flatten() {
        if !entry.file_name().to_string_lossy().chars().all(|c| c.is_ascii_digit()) {
            continue;
        }
        let Ok(command) = std::fs::read(entry.path().join("cmdline")) else { continue };
        let arguments: Vec<String> = command.split(|byte| *byte == 0).map(|part| String::from_utf8_lossy(part).into_owned()).collect();
        let Some(program) = arguments.first() else { continue };
        if !(program.ends_with("Xorg") || program.ends_with("/X") || program == "X") {
            continue;
        }
        if let Some(index) = arguments.iter().position(|argument| argument == "-auth")
            && let Some(file) = arguments.get(index + 1)
        {
            found.push(PathBuf::from(file));
        }
    }
    found.sort();
    found.dedup();
    found
}

/// The display of a running X server whose `-auth` file is `authority`, read from the
/// environment of a program on it (GDM starts its X server with `-displayfd`, so no
/// command line names the display).
fn display_of(authorities: &[PathBuf]) -> Option<String> {
    let entries = std::fs::read_dir("/proc").ok()?;
    for entry in entries.flatten() {
        if !entry.file_name().to_string_lossy().chars().all(|c| c.is_ascii_digit()) {
            continue;
        }
        let Ok(environment) = std::fs::read(entry.path().join("environ")) else { continue };
        let (mut display, mut authority) = (None, None);
        for variable in environment.split(|byte| *byte == 0) {
            let variable = String::from_utf8_lossy(variable);
            if let Some(value) = variable.strip_prefix("DISPLAY=") {
                display = Some(value.to_owned());
            } else if let Some(value) = variable.strip_prefix("XAUTHORITY=") {
                authority = Some(PathBuf::from(value));
            }
        }
        if let (Some(display), Some(authority)) = (display, authority)
            && display.starts_with(':')
            && display.len() <= 8
            && authorities.contains(&authority)
        {
            return Some(display);
        }
    }
    None
}

fn write_state(state: &str) {
    let _ = write_mode(&state_path(), state.as_bytes(), 0o644);
}

/// The root service: runs the host while the X11 greeter is shown, stops it when someone signs in.
pub fn run_service() -> Result<(), String> {
    if !root() {
        return Err("The sign-in screen service runs as root".into());
    }
    let program = program_path();
    let mut child: Option<(std::process::Child, String)> = None;
    let mut last = String::new();
    let mut tick = 0u32;
    let mut everywhere = always_on();
    loop {
        // The settings change only through the administrator steps; re-read them now and then.
        tick = tick.wrapping_add(1);
        if tick.is_multiple_of(15) {
            everywhere = always_on();
        }
        let screen = decide(&sessions(), everywhere);
        let wanted = match &screen {
            Screen::Greeter(display) if display.is_empty() => display_of(&authority_files()),
            Screen::Greeter(display) => Some(display.clone()),
            _ => None,
        };
        if let Some((running, display)) = child.as_mut() {
            let ended = running.try_wait().ok().flatten().is_some();
            if ended || wanted.as_ref() != Some(display) {
                let _ = running.kill();
                let _ = running.wait();
                child = None;
                if !ended {
                    log("the screen changed (someone signed in or out): host stopped");
                }
                std::thread::sleep(Duration::from_secs(if ended { 5 } else { 1 }));
            }
        }
        if child.is_none()
            && let Some(display) = wanted
        {
            let candidates: Vec<String> = authority_files().iter().map(|path| path.display().to_string()).collect();
            match std::process::Command::new(&program)
                .arg("--signin-host")
                .env_clear()
                .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
                .env("DISPLAY", &display)
                .env("MIXEL_XAUTH_CANDIDATES", candidates.join("\n"))
                .stdin(std::process::Stdio::null())
                .spawn()
            {
                Ok(started) => {
                    log(&format!("sign-in screen host started on {display}"));
                    child = Some((started, display));
                }
                Err(_) => log("the sign-in screen host could not be started"),
            }
        }
        let state = match (&screen, &child) {
            (Screen::Greeter(_), Some(_)) => {
                if technician_connected() { "active" } else { "running" }
            }
            (Screen::Wayland, _) => "wayland",
            _ => "waiting",
        };
        if state != last && state != "active" {
            write_state(state);
            if state == "wayland" {
                log("the screen uses Wayland: it cannot be shared without approval on screen (set WaylandEnable=false for GDM, or use an X11 session)");
            }
            last = state.to_owned();
        }
        std::thread::sleep(Duration::from_secs(2));
    }
}

/// The headless host on the greeter's X display (started by the service, as root).
pub fn run_host() -> Result<(), String> {
    if !root() {
        return Err("The sign-in screen host runs as root".into());
    }
    // Before any thread exists: find the X authority file that opens the greeter's display.
    let display = std::env::var("DISPLAY").map_err(|_| "No sign-in screen display")?;
    let candidates = std::env::var("MIXEL_XAUTH_CANDIDATES").unwrap_or_default();
    let mut opened = false;
    for candidate in candidates.lines().filter(|line| line.starts_with('/')) {
        // SAFETY: this process is still single-threaded here; nothing reads the environment concurrently.
        #[allow(unsafe_code)]
        unsafe {
            std::env::set_var("XAUTHORITY", candidate);
        }
        if x11rb::connect(Some(&display)).is_ok() {
            opened = true;
            break;
        }
    }
    if !opened && x11rb::connect(Some(&display)).is_err() {
        return Err("The sign-in screen display cannot be opened".into());
    }
    let bytes = std::fs::read(settings_path()).map_err(|_| "The sign-in screen settings are not readable")?;
    let settings: Settings = serde_json::from_slice(&bytes).map_err(|_| "The sign-in screen settings are invalid")?;
    if !settings.valid() {
        return Err("The sign-in screen settings are invalid".into());
    }
    let identity = Arc::new(IdentityState::machine(false)?);
    let enrolled = EnrolledPrincipal {
        principal: Principal { user: settings.profile, tenant: Some(settings.tenant), staff: false, expires_at: 0 },
        device: settings.device,
        connection_id: settings.connection_id,
        generation: settings.generation,
        public_key: identity.public_key()?,
        identity_ticket: None,
    };
    let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build().map_err(|_| "Cannot start the host runtime")?;
    runtime.block_on(async move {
        let remote = crate::remote_session::RemoteSession::default();
        let mut backoff = 5u64;
        loop {
            match remote.connect_startup(enrolled.clone(), 0, settings.control, identity.clone()).await {
                Ok(()) => {
                    log("sign-in screen host connecting");
                    let mut phase = String::new();
                    loop {
                        tokio::time::sleep(Duration::from_secs(3)).await;
                        let status = remote.status().await;
                        if status.phase != phase {
                            log(&format!("host {}: {}", status.phase, status.message));
                            write_state(if status.phase == "active" { "active" } else { "running" });
                            phase.clone_from(&status.phase);
                        }
                        if matches!(status.phase.as_str(), "offline" | "error") {
                            break;
                        }
                    }
                }
                Err(error) => log(&format!("host registration failed: {error}")),
            }
            tokio::time::sleep(Duration::from_secs(backoff)).await;
            backoff = (backoff * 2).min(60);
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn session(class: &str, kind: &str, display: &str, state: &str) -> LoginSession {
        LoginSession { class: class.into(), kind: kind.into(), display: display.into(), seat: "seat0".into(), state: state.into(), remote: false }
    }

    #[test]
    fn the_host_runs_only_on_an_x11_greeter_while_nobody_is_signed_in() {
        assert_eq!(decide(&[session("greeter", "x11", ":0", "online")], false), Screen::Greeter(":0".into()));
        // Someone signed in on the seat: the normal app takes over.
        assert_eq!(decide(&[session("greeter", "x11", ":0", "online"), session("user", "x11", ":1", "active")], false), Screen::Waiting);
        // A remote (ssh) login does not count, a Wayland greeter cannot be shared.
        let mut ssh = session("user", "tty", "", "active");
        ssh.remote = true;
        assert_eq!(decide(&[session("greeter", "x11", ":0", "online"), ssh], false), Screen::Greeter(":0".into()));
        assert_eq!(decide(&[session("greeter", "wayland", "", "online")], false), Screen::Wayland);
        assert_eq!(decide(&[], false), Screen::Waiting);
        // GDM: an X11 greeter without a Display entry is still shared (the display is looked up).
        assert_eq!(decide(&[session("greeter", "x11", "", "active")], false), Screen::Greeter(String::new()));
        let mut other_seat = session("greeter", "x11", ":2", "online");
        other_seat.seat = "seat1".into();
        assert_eq!(decide(&[other_seat], false), Screen::Waiting);
    }

    #[test]
    fn a_rolled_out_computer_is_shared_always_in_x11_sessions_too() {
        // Signed in on X11: the host follows the person's display.
        assert_eq!(decide(&[session("greeter", "x11", ":0", "online"), session("user", "x11", ":1", "active")], true), Screen::Greeter(":1".into()));
        assert_eq!(decide(&[session("user", "x11", "", "active")], true), Screen::Greeter(String::new()));
        // The active session wins over a second, background one.
        assert_eq!(decide(&[session("user", "wayland", "", "online"), session("user", "x11", ":2", "active")], true), Screen::Greeter(":2".into()));
        // Wayland sessions cannot be shared without approval on screen.
        assert_eq!(decide(&[session("user", "wayland", "", "active")], true), Screen::Wayland);
        // Nobody signed in: the sign-in screen, as before.
        assert_eq!(decide(&[session("greeter", "x11", ":0", "online")], true), Screen::Greeter(":0".into()));
        // ssh logins never count.
        let mut ssh = session("user", "tty", "", "active");
        ssh.remote = true;
        assert_eq!(decide(&[session("greeter", "x11", "", "online"), ssh], true), Screen::Greeter(String::new()));
    }

    #[test]
    fn loginctl_output_is_parsed() {
        let parsed = parse_session("Class=greeter\nType=x11\nDisplay=:0\nSeat=seat0\nState=online\nRemote=no\nUnknown=1\n");
        assert_eq!(parsed, session("greeter", "x11", ":0", "online"));
    }

    #[test]
    fn settings_are_strict_and_the_service_runs_a_root_owned_copy() {
        let good = Settings { version: 1, device: Uuid::from_u128(1), profile: Uuid::from_u128(2), tenant: Uuid::from_u128(3), connection_id: 485_617_081, generation: 1, control: true, always_on: false };
        assert!(good.valid());
        assert!(!Settings { connection_id: 99_999_999, ..good }.valid());
        assert!(!Settings { tenant: Uuid::nil(), ..good }.valid());
        assert!(serde_json::from_str::<Settings>(r#"{"version":1,"device":"00000000-0000-0000-0000-000000000001","profile":"00000000-0000-0000-0000-000000000002","tenant":"00000000-0000-0000-0000-000000000003","connection_id":485617081,"generation":1,"control":true,"extra":1}"#).is_err());
        // Settings written before rollout existed stay valid (always_on defaults to off).
        let old: Settings = serde_json::from_str(r#"{"version":1,"device":"00000000-0000-0000-0000-000000000001","profile":"00000000-0000-0000-0000-000000000002","tenant":"00000000-0000-0000-0000-000000000003","connection_id":485617081,"generation":1,"control":true}"#).unwrap();
        assert!(!old.always_on);
        assert_eq!(format_id(485_617_081), "485 617 081");
        let unit = unit_text();
        assert!(unit.contains("ExecStart=/usr/lib/mixel-remote-signin/mixel-remote --signin-service"));
        assert!(unit.contains("Restart=always"));
        assert!(decode_hex("0g").is_err());
        assert_eq!(decode_hex("7b7d").unwrap(), b"{}");
    }
}
