//! Installs the Mac sign-in screen host (`macos_startup_host.rs`, mode `--prelogin-host`).
//!
//! macOS starts programs at the login window only as launchd agents with
//! `LimitLoadToSessionType = LoginWindow` in `/Library/LaunchAgents`; they run as root in the
//! login window's graphical session, which the host needs (a LaunchDaemon has no screen). The
//! host is a separately signed copy of the app (`ch.mixel.remote.prelogin`, shipped inside the
//! app at `Contents/Resources/Prelogin/Mixel-Remote.app`), installed root-owned at the fixed path
//! the host checks. Two administrator steps, each with one macOS administrator prompt:
//! 1. install the host bundle and create the computer-wide key in the System Keychain;
//! 2. after the app enrolled that key for the company: the private settings and the agent.
//!
//! Once per Mac, at the Mac: allow "Mixel Remote" (login host) in Screen Recording when macOS
//! asks at the login window. Typing at the login window also needs Apple's virtual HID
//! keyboard entitlement on the host bundle (`com.apple.developer.hid.virtual.device`).
use std::path::{Path, PathBuf};

pub const AGENT_LABEL: &str = "ch.mixel.remote.prelogin";
pub const AGENT_PATH: &str = "/Library/LaunchAgents/ch.mixel.remote.prelogin.plist";
pub const HOST_FOLDER: &str = "/Library/Application Support/Mixel Remote/Host";
pub const HOST_BINARY: &str = "/Library/Application Support/Mixel Remote/Host/Mixel-Remote.app/Contents/MacOS/Mixel-Remote";
pub const SETTINGS: &str = "/Library/Application Support/Mixel Remote/prelogin.json";
pub const STATUS: &str = "/Library/Application Support/Mixel Remote/prelogin-status.json";

/// The login window agent.
pub fn agent_plist() -> String {
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
         <plist version=\"1.0\">\n<dict>\n\t<key>Label</key>\n\t<string>{AGENT_LABEL}</string>\n\
         \t<key>LimitLoadToSessionType</key>\n\t<array>\n\t\t<string>LoginWindow</string>\n\t</array>\n\
         \t<key>ProgramArguments</key>\n\t<array>\n\t\t<string>{HOST_BINARY}</string>\n\t\t<string>--prelogin-host</string>\n\t</array>\n\
         \t<key>RunAtLoad</key>\n\t<true/>\n\t<key>KeepAlive</key>\n\t<dict>\n\t\t<key>SuccessfulExit</key>\n\t\t<false/>\n\t</dict>\n\
         \t<key>ThrottleInterval</key>\n\t<integer>10</integer>\n\t<key>ProcessType</key>\n\t<string>Interactive</string>\n\
         </dict>\n</plist>\n"
    )
}

/// The host bundle shipped inside this app.
pub fn shipped_bundle() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let contents = exe.parent()?.parent()?;
    let bundle = contents.join("Resources/Prelogin/Mixel-Remote.app");
    bundle.join("Contents/MacOS/Mixel-Remote").is_file().then_some(bundle)
}

/// Apple's virtual HID keyboard entitlement, needed to type at the login window.
pub const KEYBOARD_ENTITLEMENT: &str = "com.apple.developer.hid.virtual.device";

/// Whether a signed host bundle carries the virtual keyboard entitlement (read from its signature).
pub fn bundle_entitled(bundle: &Path) -> bool {
    std::process::Command::new("/usr/bin/codesign")
        .args(["-d", "--entitlements", "-", "--xml"])
        .arg(bundle)
        .output()
        .is_ok_and(|output| output.status.success() && entitlements_grant(&String::from_utf8_lossy(&output.stdout)))
}

/// The entitlements plist grants the virtual keyboard (`<key>…</key><true/>`).
pub fn entitlements_grant(plist: &str) -> bool {
    let compact: String = plist.split_whitespace().collect();
    compact.contains(&format!("<key>{KEYBOARD_ENTITLEMENT}</key><true/>"))
}

/// Sign-in screen access in this app: `Ready` when the shipped host bundle is entitled by Apple,
/// `WaitingForApple` when it is missing or lacks the entitlement (Apple has not granted it yet).
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Availability {
    Ready,
    WaitingForApple,
}

pub fn availability() -> Availability {
    match shipped_bundle() {
        Some(bundle) if bundle_entitled(&bundle) => Availability::Ready,
        _ => Availability::WaitingForApple,
    }
}

fn quote(text: &str) -> String {
    format!("'{}'", text.replace('\'', "'\\''"))
}

/// Step 1 (root): root-owned host bundle at the fixed path, then the System Keychain key.
pub fn install_script(bundle: &Path) -> String {
    let folder = quote(HOST_FOLDER);
    format!(
        "set -eu\numask 022\nmkdir -p {folder}\nrm -rf {folder}/Mixel-Remote.app\n/usr/bin/ditto {source} {folder}/Mixel-Remote.app\n\
         /usr/sbin/chown -R root:wheel '/Library/Application Support/Mixel Remote'\n/bin/chmod -R go-w '/Library/Application Support/Mixel Remote'\n\
         /usr/bin/codesign --verify --strict {folder}/Mixel-Remote.app\n{binary} --provision-prelogin-identity\n",
        source = quote(&bundle.display().to_string()),
        binary = quote(HOST_BINARY),
    )
}

/// Step 2 (root): the private settings (checked by the host itself) and the login window agent.
pub fn settings_script(settings_json: &str, status_json: &str) -> String {
    let settings = quote(SETTINGS);
    format!(
        "set -eu\numask 077\nprintf '%s' {json} > {settings}.new\n/usr/sbin/chown root:wheel {settings}.new\n/bin/chmod 600 {settings}.new\n\
         {binary} --validate-prelogin-settings {settings}.new\n/bin/mv -f {settings}.new {settings}\n\
         umask 022\nprintf '%s' {status} > {status_path}\n/bin/chmod 644 {status_path}\n\
         printf '%s' {plist} > {agent}\n/usr/sbin/chown root:wheel {agent}\n/bin/chmod 644 {agent}\n",
        json = quote(settings_json),
        status = quote(status_json),
        status_path = quote(STATUS),
        binary = quote(HOST_BINARY),
        plist = quote(&agent_plist()),
        agent = quote(AGENT_PATH),
    )
}

/// Removal (root).
pub fn remove_script() -> String {
    format!(
        "/bin/launchctl bootout loginwindow/$(/usr/bin/id -u root)/{AGENT_LABEL} 2>/dev/null || true\nrm -f {agent} {settings} {status}\nrm -rf {folder}\n",
        agent = quote(AGENT_PATH),
        settings = quote(SETTINGS),
        status = quote(STATUS),
        folder = quote(HOST_FOLDER),
    )
}

/// Runs a root script through the macOS administrator prompt; returns its output.
pub fn run_as_administrator(script: &str) -> Result<String, String> {
    let file = tempfile::Builder::new().prefix("mixel-remote-prelogin").suffix(".sh").tempfile().map_err(|_| "Cannot prepare the administrator step")?;
    std::fs::write(file.path(), script).map_err(|_| "Cannot prepare the administrator step")?;
    let path = file.path().display().to_string().replace('\\', "\\\\").replace('"', "\\\"");
    let apple = format!("do shell script \"/bin/sh '{path}'\" with administrator privileges");
    let output = std::process::Command::new("/usr/bin/osascript").args(["-e", &apple]).output().map_err(|_| "The administrator prompt is not available")?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
    } else if String::from_utf8_lossy(&output.stderr).contains("-128") {
        Err("Administrator approval was declined".into())
    } else {
        Err("The administrator step failed".into())
    }
}

#[derive(serde::Serialize, serde::Deserialize)]
pub struct PublicStatus {
    pub connection_id: u32,
    pub name: String,
}

pub fn public_status() -> Option<PublicStatus> {
    serde_json::from_slice(&std::fs::read(STATUS).ok()?).ok()
}

/// The 32-byte key the provisioning step printed (64 hex digits on the last line).
pub fn parse_key(output: &str) -> Result<ed25519_dalek::VerifyingKey, String> {
    let line = output.lines().last().unwrap_or_default().trim();
    if line.len() != 64 {
        return Err("The sign-in screen key was not created".into());
    }
    let bytes: Vec<u8> = (0..64).step_by(2).map(|index| u8::from_str_radix(&line[index..index + 2], 16)).collect::<Result<_, _>>().map_err(|_| "The sign-in screen key is invalid")?;
    let bytes: [u8; 32] = bytes.try_into().map_err(|_| "The sign-in screen key is invalid")?;
    ed25519_dalek::VerifyingKey::from_bytes(&bytes).map_err(|_| "The sign-in screen key is invalid".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_agent_runs_the_fixed_host_at_the_login_window_only() {
        let plist = agent_plist();
        assert!(plist.contains("<string>LoginWindow</string>"));
        assert!(plist.contains(&format!("<string>{HOST_BINARY}</string>")));
        assert!(plist.contains("<string>--prelogin-host</string>"));
        // The plist is well-formed for macOS.
        let file = tempfile::Builder::new().suffix(".plist").tempfile().unwrap();
        std::fs::write(file.path(), &plist).unwrap();
        let lint = std::process::Command::new("/usr/bin/plutil").arg("-lint").arg(file.path()).output().unwrap();
        assert!(lint.status.success(), "{}", String::from_utf8_lossy(&lint.stdout));
    }

    #[test]
    fn root_scripts_quote_every_value_and_are_valid_shell() {
        let install = install_script(Path::new("/Applications/Mixel Remote's.app/Contents/Resources/Prelogin/Mixel-Remote.app"));
        assert!(install.contains("'/Applications/Mixel Remote'\\''s.app/"));
        assert!(install.contains("--provision-prelogin-identity"));
        let settings = settings_script("{\"version\":1,\"x\":\"it's\"}", "{}");
        assert!(settings.contains("--validate-prelogin-settings"));
        assert!(settings.contains("chmod 600"));
        for script in [install, settings, remove_script()] {
            let file = tempfile::NamedTempFile::new().unwrap();
            std::fs::write(file.path(), &script).unwrap();
            let check = std::process::Command::new("/bin/sh").arg("-n").arg(file.path()).output().unwrap();
            assert!(check.status.success(), "{script}");
        }
    }

    #[test]
    fn only_a_granted_keyboard_entitlement_counts() {
        let granted = "<?xml version=\"1.0\"?><plist version=\"1.0\"><dict>\n\t<key>com.apple.developer.hid.virtual.device</key>\n\t<true/>\n</dict></plist>";
        assert!(entitlements_grant(granted));
        assert!(!entitlements_grant(&granted.replace("<true/>", "<false/>")));
        assert!(!entitlements_grant("<plist><dict><key>com.apple.security.app-sandbox</key><true/></dict></plist>"));
        assert!(!entitlements_grant(""));
        // This test binary is not a host bundle: sign-in screen access waits for Apple.
        assert_eq!(availability(), Availability::WaitingForApple);
    }

    #[test]
    fn the_provisioned_key_is_read_from_the_last_line() {
        let key = ed25519_dalek::SigningKey::from_bytes(&[5; 32]).verifying_key();
        let hex: String = key.as_bytes().iter().map(|byte| format!("{byte:02x}")).collect();
        assert_eq!(parse_key(&format!("Machine identity\n{hex}\n")).unwrap(), key);
        assert!(parse_key("no key").is_err());
    }
}
