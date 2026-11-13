//! Mac mass rollout: `Mixel-Remote --signin-enroll <deploy token>`.
//!
//! Run as the person signed in on the Mac (the install script `https://remote.mixel.ch/install/macos.sh`
//! runs it for the console user when started as root by `sudo` or an MDM). Without any window or
//! prompt it creates this app's device key in the login Keychain, enrols it for the company with the
//! deploy token, writes the private settings of the headless desktop host (`--startup-host`,
//! `macos_startup_host.rs`) and installs a per-user launch agent that keeps the host running in the
//! person's session (always-on access).
//!
//! macOS rule: sharing the screen and controlling the Mac needs the person's approval once
//! (System Settings › Privacy & Security › Screen Recording and Accessibility; Accessibility can
//! also be pre-approved by an MDM profile). Until then the host waits without asking.
use crate::device_identity::IdentityState;
use security_framework::os::macos::keychain::SecKeychain;
use std::{
    io::Write,
    os::unix::fs::OpenOptionsExt,
    path::{Path, PathBuf},
};

/// The private settings the startup host reads (owned by the person, mode 0600).
#[derive(serde::Serialize)]
struct StartupSettings {
    version: u16,
    device: uuid::Uuid,
    profile: uuid::Uuid,
    tenant: uuid::Uuid,
    connection_id: u32,
    generation: u64,
    share_main_display: bool,
    control: bool,
}

fn home(uid: u32) -> Result<PathBuf, String> {
    // SAFETY: getpwuid returns a pointer to static storage or null; it is read at once, on this thread.
    #[allow(unsafe_code)]
    let directory = unsafe {
        let entry = libc::getpwuid(uid);
        if entry.is_null() || (*entry).pw_dir.is_null() {
            return Err("The home folder of this account is unknown".into());
        }
        std::ffi::CStr::from_ptr((*entry).pw_dir).to_string_lossy().into_owned()
    };
    let path = PathBuf::from(directory);
    if !path.is_absolute() || !path.is_dir() {
        return Err("The home folder of this account is unknown".into());
    }
    Ok(path)
}

/// Launch agent label of this app copy (a lab copy with its own bundle identifier gets its own).
pub fn agent_label(bundle: &str) -> String {
    format!("{bundle}.startup-host")
}

pub fn settings_path(home: &Path, bundle: &str) -> PathBuf {
    home.join("Library/Application Support/Mixel Remote").join(format!("startup-host-{bundle}.json"))
}

pub fn agent_path(home: &Path, bundle: &str) -> PathBuf {
    home.join("Library/LaunchAgents").join(format!("{}.plist", agent_label(bundle)))
}

fn xml(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

/// The per-user launch agent: runs the headless host in the person's graphical session.
pub fn agent_plist(label: &str, program: &Path, settings: &Path) -> String {
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
         <plist version=\"1.0\">\n<dict>\n\t<key>Label</key>\n\t<string>{label}</string>\n\
         \t<key>LimitLoadToSessionType</key>\n\t<array>\n\t\t<string>Aqua</string>\n\t</array>\n\
         \t<key>ProgramArguments</key>\n\t<array>\n\t\t<string>{program}</string>\n\t\t<string>--startup-host</string>\n\t\t<string>{settings}</string>\n\t</array>\n\
         \t<key>RunAtLoad</key>\n\t<true/>\n\t<key>KeepAlive</key>\n\t<dict>\n\t\t<key>SuccessfulExit</key>\n\t\t<false/>\n\t</dict>\n\
         \t<key>ThrottleInterval</key>\n\t<integer>30</integer>\n\t<key>ProcessType</key>\n\t<string>Interactive</string>\n\
         </dict>\n</plist>\n",
        label = xml(label),
        program = xml(&program.display().to_string()),
        settings = xml(&settings.display().to_string()),
    )
}

/// Writes a file atomically with the given mode (no symlinks followed).
fn write_file(path: &Path, bytes: &[u8], mode: u32) -> Result<(), String> {
    let folder = path.parent().ok_or("Invalid path")?;
    std::fs::create_dir_all(folder).map_err(|_| format!("Cannot create {}", folder.display()))?;
    let temporary = path.with_extension("new");
    let _ = std::fs::remove_file(&temporary);
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&temporary)
        .map_err(|_| format!("Cannot write {}", path.display()))?;
    file.write_all(bytes).and_then(|()| file.sync_all()).map_err(|_| format!("Cannot write {}", path.display()))?;
    std::fs::rename(&temporary, path).map_err(|_| format!("Cannot write {}", path.display()))
}

fn computer_name() -> String {
    let name = std::process::Command::new("/usr/sbin/scutil")
        .args(["--get", "ComputerName"])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .unwrap_or_default();
    let name: String = name.chars().filter(|c| !c.is_control()).take(60).collect();
    if name.trim().is_empty() { "Mac".into() } else { name.trim().to_owned() }
}

fn format_id(id: u32) -> String {
    let digits = id.to_string();
    digits.as_bytes().chunks(3).map(|chunk| String::from_utf8_lossy(chunk).into_owned()).collect::<Vec<_>>().join(" ")
}

pub fn valid_token(token: &str) -> bool {
    token.starts_with("mrt_") && (20..=100).contains(&token.len()) && token.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte))
}

/// `--signin-enroll <token>`: enrol this Mac and start the always-on host for this account.
pub fn enroll(token: Option<&str>) -> Result<(), String> {
    let token = token.map(str::trim).filter(|token| valid_token(token)).ok_or("The deploy token is missing or invalid")?;
    // SAFETY: geteuid only reads the effective user ID of this process.
    #[allow(unsafe_code)]
    let uid = unsafe { libc::geteuid() };
    if uid == 0 {
        return Err("Run this step as the person signed in on this Mac (the install script does that)".into());
    }
    let bundle = native_os_capture::macos_bundle::identifier().ok_or("Run Mixel Remote from its app bundle")?;
    crate::device_identity::validated_bundle_scope()?;
    let name = computer_name();
    let identity = IdentityState::default();
    {
        // Headless: a Keychain that would need a prompt fails instead of asking.
        let _no_ui = SecKeychain::disable_user_interaction()
            .map_err(|error| format!("Cannot disable Keychain prompts (OS status {})", error.code()))?;
        identity.open(true).map_err(|error| format!("{error}. Unlock the login Keychain (sign in on the Mac) and run the command again."))?;
    }
    let key = identity.public_key()?;
    let config = crate::service_config::ServiceConfig::load()?;
    let base = config.identity_url.parse().map_err(|_| "Invalid identity service configuration")?;
    let ism = signaling_server::auth::IsmAuth::new(base, config.public_key).map_err(|_| "Invalid identity service configuration")?;
    let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().map_err(|_| "Cannot start the rollout")?;
    let enrolled = runtime
        .block_on(ism.enroll_with_rollout_token(token, &name, "macos", &key))
        .map_err(|_| crate::ROLLOUT_REFUSED)?;
    let home = home(uid)?;
    let settings = settings_path(&home, &bundle);
    let json = serde_json::to_vec(&StartupSettings {
        version: 1,
        device: enrolled.device_id,
        profile: enrolled.profile,
        tenant: enrolled.tenant,
        connection_id: enrolled.connection_id,
        generation: enrolled.generation,
        share_main_display: true,
        control: true,
    })
    .map_err(|_| "Cannot prepare the settings")?;
    write_file(&settings, &json, 0o600)?;
    let program = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
    let label = agent_label(&bundle);
    let agent = agent_path(&home, &bundle);
    write_file(&agent, agent_plist(&label, &program, &settings).as_bytes(), 0o644)?;
    let domain = format!("gui/{uid}");
    let _ = std::process::Command::new("/bin/launchctl").args(["bootout", &format!("{domain}/{label}")]).output();
    let started = std::process::Command::new("/bin/launchctl")
        .args(["bootstrap", &domain, &agent.display().to_string()])
        .output()
        .is_ok_and(|output| output.status.success());
    println!("Mixel Remote: {name} was added with Remote ID {}", format_id(enrolled.connection_id));
    if started {
        println!("Always-on access is set up for this account.");
    } else {
        println!("Always-on access starts the next time this account signs in.");
    }
    println!(
        "Once per Mac: allow Mixel Remote in System Settings > Privacy & Security > Screen Recording and Accessibility (Accessibility can be pre-approved by your MDM)."
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_agent_runs_the_headless_host_in_the_signed_in_session() {
        let plist = agent_plist(
            "ch.mixel.remote.labtest.startup-host",
            Path::new("/Applications/Mixel Remote.app/Contents/MacOS/Mixel-Remote"),
            Path::new("/Users/a&b/Library/Application Support/Mixel Remote/startup-host-ch.mixel.remote.labtest.json"),
        );
        assert!(plist.contains("<string>Aqua</string>"));
        assert!(plist.contains("<string>--startup-host</string>"));
        assert!(plist.contains("<string>/Users/a&amp;b/Library/Application Support/Mixel Remote/startup-host-ch.mixel.remote.labtest.json</string>"));
        assert!(plist.contains("<key>SuccessfulExit</key>\n\t\t<false/>"));
        let home = Path::new("/Users/qa");
        assert_eq!(agent_path(home, "ch.mixel.remote.next.qa"), Path::new("/Users/qa/Library/LaunchAgents/ch.mixel.remote.next.qa.startup-host.plist"));
        assert_eq!(settings_path(home, "x").file_name().unwrap(), "startup-host-x.json");
    }

    #[test]
    fn only_deploy_tokens_are_accepted() {
        assert!(valid_token("mrt_abcdefghijklmnopqrstuvwxyz0123456789-_"));
        assert!(!valid_token("mri_abcdefghijklmnopqrstuvwxyz0123456789"));
        assert!(!valid_token("mrt_short"));
        assert!(!valid_token("mrt_abcdefghijklmnopqrstuvwxyz';touch /tmp/x"));
        assert_eq!(format_id(123_456_789), "123 456 789");
    }

    #[test]
    fn settings_match_what_the_startup_host_accepts() {
        let json = serde_json::to_string(&StartupSettings {
            version: 1,
            device: uuid::Uuid::from_u128(1),
            profile: uuid::Uuid::from_u128(2),
            tenant: uuid::Uuid::from_u128(3),
            connection_id: 485_617_081,
            generation: 1,
            share_main_display: true,
            control: true,
        })
        .unwrap();
        assert!(crate::macos_startup_host::settings_accepted(json.as_bytes()));
    }
}
