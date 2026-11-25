//! In-app updates from the public `remote-updates` folder on store.mixel.ch.
//! Trust does not come from the server: each channel file is signed with the Mixel
//! update key (private key kept offline), the download must match its size and SHA-256,
//! and Windows and macOS installers must also carry Mixel's own code signature.
use base64::Engine as _;
use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    io::Write,
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};

const BASE: &str = "https://store-api.mixel.ch/storage/v1/object/public/remote-updates";
/// Public half of the Mixel Remote update signing key.
const UPDATE_KEY: [u8; 32] = [
    0x54, 0x7e, 0x06, 0xc5, 0x2b, 0xf6, 0xa8, 0xcb, 0x75, 0xb2, 0xd8, 0xd6, 0xeb, 0x2d, 0xab, 0x8a,
    0x95, 0x06, 0x8d, 0xa3, 0x66, 0xab, 0xa4, 0xf7, 0x9a, 0x86, 0x3f, 0x70, 0x5a, 0x93, 0x65, 0x49,
];

/// Where updates come from and which key signs them: the fixed server and key above. Lab builds (feature
/// `qa-session`, never a release build) can point both elsewhere with a file `lab-update.json`
/// (`{"base":"http://host:port/path","key":"<64 hex characters>"}`, named by MIXEL_LAB_UPDATE_FILE or kept in
/// ProgramData\Mixel Remote), to test updates against a lab channel with a lab key.
fn source() -> (String, [u8; 32]) {
    #[cfg(feature = "qa-session")]
    if let Some(lab) = lab_source() {
        return lab;
    }
    (BASE.into(), UPDATE_KEY)
}
#[cfg(feature = "qa-session")]
fn lab_source() -> Option<(String, [u8; 32])> {
    let path = std::env::var_os("MIXEL_LAB_UPDATE_FILE").map(PathBuf::from).or_else(|| {
        std::env::var_os("ProgramData").map(|dir| PathBuf::from(dir).join("Mixel Remote").join("lab-update.json"))
    })?;
    lab_source_from(&std::fs::read_to_string(path).ok()?)
}
#[cfg(feature = "qa-session")]
fn lab_source_from(text: &str) -> Option<(String, [u8; 32])> {
    let value: serde_json::Value = serde_json::from_str(text).ok()?;
    let base = value.get("base")?.as_str()?.trim_end_matches('/').to_owned();
    let hex = value.get("key")?.as_str()?;
    if hex.len() != 64 || !base.starts_with("http") {
        return None;
    }
    let mut key = [0u8; 32];
    for (index, byte) in key.iter_mut().enumerate() {
        *byte = u8::from_str_radix(hex.get(index * 2..index * 2 + 2)?, 16).ok()?;
    }
    Some((base, key))
}
const MAX_INSTALLER: u64 = 300 * 1024 * 1024;
static INSTALLING: AtomicBool = AtomicBool::new(false);

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    manifest: String,
    signature: String,
}
#[derive(Deserialize, Serialize, Clone, Debug, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub product: String,
    pub channel: String,
    pub platform: String,
    pub arch: String,
    pub version: String,
    pub file: String,
    pub size: u64,
    pub sha256: String,
    pub published: u64,
}

pub fn channel() -> String {
    std::env::var("MIXEL_UPDATE_CHANNEL")
        .ok()
        .filter(|value| {
            (1..=20).contains(&value.len())
                && value.bytes().all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
        })
        .unwrap_or_else(|| "stable".into())
}
pub fn target() -> (&'static str, &'static str) {
    if cfg!(target_os = "macos") {
        // macOS ships a universal (arm64 + x86_64) DMG on the macos-arm64 channel.
        ("macos", "arm64")
    } else if cfg!(target_os = "windows") {
        ("windows", if cfg!(target_arch = "aarch64") { "arm64" } else { "x64" })
    } else {
        ("linux", if cfg!(target_arch = "aarch64") { "arm64" } else { "x64" })
    }
}

#[cfg(test)]
mod program_kind_tests {
    fn pe(machine: u16, subsystem: u16) -> Vec<u8> {
        let mut bytes = vec![0u8; 0x200];
        bytes[..2].copy_from_slice(b"MZ");
        bytes[0x3c..0x40].copy_from_slice(&0x80u32.to_le_bytes());
        bytes[0x80..0x84].copy_from_slice(b"PE\0\0");
        bytes[0x84..0x86].copy_from_slice(&machine.to_le_bytes());
        bytes[0x80 + 24 + 68..0x80 + 24 + 70].copy_from_slice(&subsystem.to_le_bytes());
        bytes
    }

    #[test]
    fn only_an_x64_console_program_may_replace_the_service_program() {
        assert!(super::is_console_program(&pe(0x8664, 3)));
        assert!(!super::is_console_program(&pe(0x8664, 2)), "GUI launcher (the 2.2.9 mistake)");
        assert!(!super::is_console_program(&pe(0xaa64, 3)), "arm64");
        assert!(!super::is_console_program(b"MZ"));
        assert!(!super::is_console_program(b"not a program at all"));
    }
}

/// Checks the signature and content of a channel file for this platform.
#[cfg(test)]
pub fn verify_envelope(bytes: &[u8], channel: &str, key: &VerifyingKey) -> Result<Manifest, String> {
    let (platform, arch) = target();
    verify_envelope_for(bytes, channel, key, platform, arch)
}
fn verify_envelope_for(bytes: &[u8], channel: &str, key: &VerifyingKey, platform: &str, arch: &str) -> Result<Manifest, String> {
    let envelope: Envelope = serde_json::from_slice(bytes).map_err(|_| "The update information is invalid")?;
    let engine = base64::engine::general_purpose::STANDARD;
    let manifest_bytes = engine.decode(envelope.manifest).map_err(|_| "The update information is invalid")?;
    let signature: [u8; 64] = engine
        .decode(envelope.signature)
        .ok()
        .and_then(|value| value.try_into().ok())
        .ok_or("The update information is invalid")?;
    key.verify_strict(&manifest_bytes, &Signature::from_bytes(&signature))
        .map_err(|_| "The update is not signed by Mixel")?;
    let manifest: Manifest = serde_json::from_slice(&manifest_bytes).map_err(|_| "The update information is invalid")?;
    let safe_file = manifest.file.len() <= 200
        && manifest.file.starts_with("files/")
        && !manifest.file.contains("..")
        && manifest.file.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"._-/".contains(&byte));
    if manifest.product != "mixel-remote"
        || manifest.channel != channel
        || manifest.platform != platform
        || manifest.arch != arch
        || !safe_file
        || manifest.size == 0
        || manifest.size > MAX_INSTALLER
        || manifest.sha256.len() != 64
        || !manifest.sha256.bytes().all(|byte| byte.is_ascii_hexdigit())
        || crate::version_parts(&manifest.version).is_none()
    {
        return Err("The update does not belong to this app".into());
    }
    Ok(manifest)
}

fn client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(600))
        .connect_timeout(Duration::from_secs(15))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| "Updates are unavailable".into())
}

/// The newest signed version for this platform and channel.
pub async fn latest() -> Result<Manifest, String> {
    let (platform, arch) = target();
    latest_for(platform, arch).await
}
/// The newest signed version for a given platform (e.g. the Windows installer for a rollout
/// command created on a Mac).
pub async fn latest_for(platform: &str, arch: &str) -> Result<Manifest, String> {
    let channel = channel();
    let (base, update_key) = source();
    let response = client()?
        .get(format!("{base}/{channel}/{platform}-{arch}/latest.json"))
        .timeout(Duration::from_secs(15))
        .send()
        .await
        .and_then(reqwest::Response::error_for_status)
        .map_err(|_| "Update check unavailable")?;
    if response.content_length().is_some_and(|length| length > 8192) {
        return Err("The update information is invalid".into());
    }
    let bytes = response.bytes().await.map_err(|_| "Update check unavailable")?;
    if bytes.len() > 8192 {
        return Err("The update information is invalid".into());
    }
    let key = VerifyingKey::from_bytes(&update_key).map_err(|_| "The update key is invalid")?;
    verify_envelope_for(&bytes, &channel, &key, platform, arch)
}
/// Public download address of a manifest's file.
pub fn file_url(manifest: &Manifest) -> String {
    format!("{}/{}", source().0, manifest.file)
}

fn work_dir(version: &str) -> PathBuf {
    #[cfg(target_os = "linux")]
    {
        // Avoid world-writable /tmp on multi-user Linux hosts (prevents TOCTOU swap before pkexec).
        if let Some(runtime) = std::env::var_os("XDG_RUNTIME_DIR")
            && !runtime.is_empty()
        {
            return PathBuf::from(runtime).join("mixel-remote-updates").join(version);
        }
        if let Some(home) = std::env::var_os("HOME")
            && !home.is_empty()
        {
            return PathBuf::from(home).join(".cache").join("mixel-remote-updates").join(version);
        }
    }
    std::env::temp_dir().join("mixel-remote-updates").join(version)
}

async fn download(manifest: &Manifest) -> Result<PathBuf, String> {
    use futures_util::StreamExt;
    let dir = work_dir(&manifest.version);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).map_err(|_| "Cannot prepare the update download")?;
    let name = manifest.file.rsplit('/').next().unwrap_or("update");
    let path = dir.join(name);
    let response = client()?
        .get(format!("{}/{}", source().0, manifest.file))
        .send()
        .await
        .and_then(reqwest::Response::error_for_status)
        .map_err(|_| "The update could not be downloaded")?;
    let mut file = std::fs::File::create(&path).map_err(|_| "Cannot save the update")?;
    let mut hasher = Sha256::new();
    let mut total = 0u64;
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| "The update download was interrupted")?;
        total += chunk.len() as u64;
        if total > manifest.size {
            return Err("The update download is larger than announced".into());
        }
        hasher.update(&chunk);
        file.write_all(&chunk).map_err(|_| "Cannot save the update")?;
    }
    file.sync_all().map_err(|_| "Cannot save the update")?;
    let digest: String = hasher.finalize().iter().map(|byte| format!("{byte:02x}")).collect();
    if total != manifest.size || !digest.eq_ignore_ascii_case(&manifest.sha256) {
        let _ = std::fs::remove_dir_all(&dir);
        return Err("The downloaded update does not match its signature".into());
    }
    Ok(path)
}

#[cfg(not(target_os = "windows"))]
#[cfg_attr(any(target_os = "ios", target_os = "android"), allow(dead_code))]
fn run(program: &str, args: &[&std::ffi::OsStr]) -> Result<String, String> {
    let mut command = std::process::Command::new(program);
    command.args(args);
    let output = command.output().map_err(|_| format!("Cannot run {program}"))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        let detail: String = detail.trim().chars().take(160).collect();
        return Err(format!("{program} failed: {detail}"));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// True for an x64 Windows console program (PE subsystem 3), which is what the always-on service
/// runs (mixel-remote-helper.exe). 2.2.9 published the app's GUI launcher on the program channel; a
/// signed, correctly hashed file of the wrong kind must never replace the service program again.
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub fn is_console_program(bytes: &[u8]) -> bool {
    let read_u16 = |at: usize| bytes.get(at..at + 2).map(|b| u16::from_le_bytes([b[0], b[1]]));
    let read_u32 =
        |at: usize| bytes.get(at..at + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]));
    if bytes.get(..2) != Some(b"MZ") {
        return false;
    }
    let Some(pe) = read_u32(0x3c).map(|offset| offset as usize) else {
        return false;
    };
    if bytes.get(pe..pe + 4) != Some(b"PE\0\0") || read_u16(pe + 4) != Some(0x8664) {
        return false;
    }
    // Optional header follows the 20-byte file header; Subsystem sits at offset 68 in PE32 and PE32+.
    read_u16(pe + 24 + 68) == Some(3)
}

/// Downloads an update and checks size, SHA-256 and Mixel's code signature (Windows).
#[cfg(target_os = "windows")]
pub async fn fetch_verified(manifest: &Manifest) -> Result<PathBuf, String> {
    let path = download(manifest).await?;
    let check = path.clone();
    tokio::task::spawn_blocking(move || verify_signature(&check))
        .await
        .map_err(|_| "Cannot check the update signature")??;
    Ok(path)
}
/// The Authenticode check of [`fetch_verified`], for a copy of an update kept elsewhere.
#[cfg(target_os = "windows")]
pub fn verify_windows_signature(installer: &Path) -> Result<(), String> {
    verify_signature(installer)
}
#[cfg(target_os = "windows")]
fn verify_signature(installer: &Path) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    // Paths travel in environment variables: PowerShell -Command does not pass extra arguments.
    let output = std::process::Command::new("powershell")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$s=Get-AuthenticodeSignature -LiteralPath $env:MIXEL_UPDATE_FILE; '{0}|{1}' -f $s.Status,$s.SignerCertificate.Subject",
        ])
        .env("MIXEL_UPDATE_FILE", installer)
        .creation_flags(0x0800_0000)
        .output()
        .map_err(|_| "Cannot check the update signature")?;
    let checked = String::from_utf8_lossy(&output.stdout);
    let (status, subject) = checked.trim().split_once('|').ok_or("Cannot check the update signature")?;
    if !output.status.success() || status != "Valid" || !subject.contains("O=Mixel International Services SARLS") {
        return Err("The update is not signed by Mixel".into());
    }
    Ok(())
}
#[cfg(target_os = "windows")]
fn install(installer: &Path, _version: &str) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    verify_signature(installer)?;
    let exe = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
    let pid = std::process::id().to_string();
    // Hidden helper: run from TEMP so the install folder is not held open, wait until this app
    // has closed, install silently, and start the new version (or, when the installer failed, the old one again: the
    // app then waits before the next try, so a failing installer never leaves the computer without the app).
    let script = "Set-Location $env:TEMP; \
        for($i=0; $i -lt 40; $i++){ if(-not (Get-Process -Id $env:MIXEL_UPDATE_PID -ErrorAction SilentlyContinue)){ break }; Start-Sleep -Milliseconds 250 }; \
        Stop-Process -Id $env:MIXEL_UPDATE_PID -Force -ErrorAction SilentlyContinue; \
        Stop-Process -Name mixel-remote-helper -Force -ErrorAction SilentlyContinue; \
        Start-Sleep 1; \
        $t=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(); \
        Add-Content -LiteralPath $env:MIXEL_UPDATE_LOG \"installer started $t\"; \
        $p=Start-Process -FilePath $env:MIXEL_UPDATE_FILE -ArgumentList '/S' -Wait -PassThru; \
        $t=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(); \
        Add-Content -LiteralPath $env:MIXEL_UPDATE_LOG \"installer exit $($p.ExitCode) $t\"; \
        Start-Process -FilePath $env:MIXEL_UPDATE_APP; \
        $t=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(); \
        Add-Content -LiteralPath $env:MIXEL_UPDATE_LOG \"app started $t\"";
    let spawn = |flags: u32| {
        std::process::Command::new("powershell")
            .current_dir(std::env::temp_dir())
            .args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script])
            .env("MIXEL_UPDATE_PID", &pid)
            .env("MIXEL_UPDATE_FILE", installer)
            .env("MIXEL_UPDATE_APP", &exe)
            .env("MIXEL_UPDATE_LOG", log_path())
            .env_remove("MIXEL_UPDATE_CHANNEL")
            .creation_flags(flags)
            .spawn()
    };
    // CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, and leave a job (e.g. a scheduled task) if allowed.
    spawn(0x0800_0000 | 0x0000_0200 | 0x0100_0000)
        .or_else(|_| spawn(0x0800_0000 | 0x0000_0200))
        .map_err(|_| "Cannot start the update installer")?;
    Ok(())
}

/// iPhone and iPad update through the App Store or TestFlight.
#[cfg(target_os = "ios")]
fn install(_package: &Path, _version: &str) -> Result<(), String> {
    Err("Mixel Remote for iPhone and iPad updates through the App Store or TestFlight".into())
}
/// Android phones and tablets update through Google Play (or the store.mixel.ch test download).
#[cfg(target_os = "android")]
fn install(_package: &Path, _version: &str) -> Result<(), String> {
    Err("Mixel Remote for Android updates through Google Play or store.mixel.ch".into())
}
#[cfg(target_os = "macos")]
fn install(image: &Path, version: &str) -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
    let mut bundle = exe
        .ancestors()
        .nth(3)
        .filter(|path| path.extension().is_some_and(|ext| ext == "app"))
        .ok_or("Mixel Remote is not running from an app bundle")?
        .to_path_buf();
    let bundle_str = bundle.to_string_lossy();
    if bundle.starts_with("/Volumes/") || bundle_str.contains("/AppTranslocation/") {
        bundle = PathBuf::from("/Applications/Mixel Remote.app");
    }
    let parent = bundle.parent().ok_or("Cannot locate the app folder")?.to_path_buf();
    let identifier = native_os_capture::macos_bundle::identifier().ok_or("Cannot read this app's identity")?;
    let mount = image.with_extension("mount");
    let _ = std::fs::create_dir_all(&mount);
    run(
        "/usr/bin/hdiutil",
        &["attach".as_ref(), "-nobrowse".as_ref(), "-readonly".as_ref(), "-noautoopen".as_ref(), "-mountpoint".as_ref(), mount.as_os_str(), image.as_os_str()],
    )?;
    let result = (|| -> Result<(), String> {
        let source = std::fs::read_dir(&mount)
            .map_err(|_| "Cannot read the update image")?
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .find(|path| path.extension().is_some_and(|ext| ext == "app"))
            .ok_or("The update image holds no app")?;
        let requirement = format!(
            "=anchor apple generic and certificate leaf[subject.OU] = \"5277F8NDH4\" and identifier \"{identifier}\""
        );
        run(
            "/usr/bin/codesign",
            &["--verify".as_ref(), "--deep".as_ref(), "--strict".as_ref(), "-R".as_ref(), requirement.as_ref(), source.as_os_str()],
        )
        .map_err(|_| "The update is not signed by Mixel")?;
        let plist = source.join("Contents/Info.plist");
        let found = run(
            "/usr/libexec/PlistBuddy",
            &["-c".as_ref(), "Print :CFBundleShortVersionString".as_ref(), plist.as_os_str()],
        )?;
        if found.trim() != version {
            return Err("The update image has another version".into());
        }
        let name = bundle.file_name().ok_or("Cannot locate the app")?.to_string_lossy().into_owned();
        let staged = parent.join(format!(".{name}.update"));
        let old = parent.join(format!(".{name}.previous"));
        let direct = (|| -> Result<(), String> {
            let _ = std::fs::remove_dir_all(&staged);
            let _ = std::fs::remove_dir_all(&old);
            run("/usr/bin/ditto", &[source.as_os_str(), staged.as_os_str()])?;
            let had_existing = bundle.exists();
            if had_existing {
                std::fs::rename(&bundle, &old).map_err(|_| "Cannot replace the app")?;
            }
            if std::fs::rename(&staged, &bundle).is_err() {
                if had_existing {
                    let _ = std::fs::rename(&old, &bundle);
                }
                return Err("Cannot replace the app".into());
            }
            let _ = std::fs::remove_dir_all(&old);
            Ok(())
        })();
        if direct.is_err() {
            let _ = std::fs::remove_dir_all(&staged);
            let uid = run("/usr/bin/id", &["-u".as_ref()]).unwrap_or_else(|_| "0".into());
            let gid = run("/usr/bin/id", &["-g".as_ref()]).unwrap_or_else(|_| "0".into());
            let sh_cmd = admin_install_command(&source, &staged, &old, &bundle, &requirement, uid.trim(), gid.trim());
            let osa_script = format!(
                "do shell script \"{}\" with administrator privileges",
                sh_cmd.replace('\\', "\\\\").replace('"', "\\\"")
            );
            run("/usr/bin/osascript", &["-e".as_ref(), osa_script.as_ref()])
                .map_err(|_| "The update could not be installed (administrator approval is needed)".to_string())?;
        }
        Ok(())
    })();
    let _ = run("/usr/bin/hdiutil", &["detach".as_ref(), "-force".as_ref(), mount.as_os_str()]);
    result?;
    std::process::Command::new("/bin/sh")
        .args(["-c", "sleep 1; exec /usr/bin/open -n \"$0\""])
        .arg(&bundle)
        .spawn()
        .map_err(|_| "Updated; start Mixel Remote again")?;
    Ok(())
}

#[cfg(target_os = "macos")]
fn sh_quote(path: &Path) -> String {
    format!("'{}'", path.to_string_lossy().replace('\'', "'\\''"))
}

/// The administrator step of a Mac update: the app is copied out of the image into a staging
/// folder, and that copy (not the image the user's own processes could swap meanwhile) is checked
/// against Mixel's signing requirement as root before it replaces the installed app.
#[cfg(target_os = "macos")]
fn admin_install_command(source: &Path, staged: &Path, old: &Path, bundle: &Path, requirement: &str, uid: &str, gid: &str) -> String {
    format!(
        "/bin/rm -rf {staged} {old} && /usr/bin/ditto {source} {staged} && /usr/bin/codesign --verify --deep --strict -R {requirement} {staged} && (if [ -e {bundle} ]; then /bin/mv {bundle} {old}; fi) && /bin/mv {staged} {bundle} && /usr/sbin/chown -R {uid}:{gid} {bundle} && /bin/rm -rf {old}",
        source = sh_quote(source),
        staged = sh_quote(staged),
        old = sh_quote(old),
        bundle = sh_quote(bundle),
        requirement = sh_quote(Path::new(requirement)),
    )
}

#[cfg(all(test, target_os = "macos"))]
mod admin_install_tests {
    #[test]
    fn the_root_step_checks_the_signature_of_its_own_copy_before_installing_it() {
        use std::path::Path;
        let command = super::admin_install_command(
            Path::new("/tmp/x.mount/Mixel Remote.app"),
            Path::new("/Applications/.Mixel Remote.app.update"),
            Path::new("/Applications/.Mixel Remote.app.previous"),
            Path::new("/Applications/Mixel Remote.app"),
            "=anchor apple generic and identifier \"ch.mixel.remote\"",
            "501",
            "20",
        );
        let copy = command.find("/usr/bin/ditto").unwrap();
        let check = command.find("/usr/bin/codesign --verify").unwrap();
        let install = command.find("/bin/mv '/Applications/.Mixel Remote.app.update'").unwrap();
        assert!(copy < check && check < install, "{command}");
        assert!(command[check..install].contains("'/Applications/.Mixel Remote.app.update'"), "the copy is what is checked: {command}");
    }
}

#[cfg(target_os = "linux")]
fn install(package: &Path, _version: &str) -> Result<(), String> {
    // Read the program path first: once dpkg replaces the file, it reads as "… (deleted)".
    let exe = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
    let exe = PathBuf::from(exe.to_string_lossy().trim_end_matches(" (deleted)"));
    // Installing a system package needs the administrator password once (polkit prompt). apt
    // also installs what a new version depends on (dpkg alone would leave it unconfigured).
    let installed = if Path::new("/usr/bin/apt-get").exists() {
        run("pkexec", &["/usr/bin/apt-get".as_ref(), "install".as_ref(), "-y".as_ref(), "-q".as_ref(), package.as_os_str()])
    } else {
        run("pkexec", &["/usr/bin/dpkg".as_ref(), "-i".as_ref(), package.as_os_str()])
    };
    installed.map_err(|_| "The update was not installed (administrator approval is needed)")?;
    std::process::Command::new("/bin/sh")
        .args(["-c", "sleep 1; exec \"$0\""])
        .arg(exe)
        .spawn()
        .map_err(|_| "Updated; start Mixel Remote again")?;
    Ok(())
}

/// Whether installing needs a password prompt on this platform.
pub const NEEDS_PASSWORD: bool = cfg!(target_os = "linux");

/// Downloads, verifies and installs `manifest`; on success the caller exits the app.
pub async fn download_and_install(manifest: Manifest) -> Result<(), String> {
    if INSTALLING.swap(true, Ordering::AcqRel) {
        return Err("An update is already being installed".into());
    }
    log(&format!("installing {} from channel {}", manifest.version, manifest.channel));
    let result = async {
        let path = download(&manifest).await?;
        let version = manifest.version.clone();
        tokio::task::spawn_blocking(move || install(&path, &version))
            .await
            .map_err(|_| "The update installer failed")?
    }
    .await;
    log(&match &result {
        Ok(()) => format!("installed {}", manifest.version),
        Err(error) => format!("update to {} failed: {error}", manifest.version),
    });
    if result.is_err() {
        INSTALLING.store(false, Ordering::Release);
    }
    result
}

/// Short update history for support: <temp>/mixel-remote-updates/update.log.
pub fn log_path() -> PathBuf {
    std::env::temp_dir().join("mixel-remote-updates").join("update.log")
}
fn log(message: &str) {
    let path = log_path();
    let _ = std::fs::create_dir_all(path.parent().unwrap_or(Path::new(".")));
    if std::fs::metadata(&path).is_ok_and(|meta| meta.len() > 64 * 1024) {
        let _ = std::fs::remove_file(&path);
    }
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let seconds = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |time| time.as_secs());
        let _ = writeln!(file, "{seconds} {message}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};
    fn envelope(key: &SigningKey, manifest: &str) -> Vec<u8> {
        let engine = base64::engine::general_purpose::STANDARD;
        serde_json::to_vec(&serde_json::json!({
            "manifest": engine.encode(manifest),
            "signature": engine.encode(key.sign(manifest.as_bytes()).to_bytes()),
        }))
        .unwrap()
    }
    fn manifest(version: &str, file: &str) -> String {
        let (platform, arch) = target();
        format!(r#"{{"product":"mixel-remote","channel":"stable","platform":"{platform}","arch":"{arch}","version":"{version}","file":"{file}","size":10,"sha256":"{}","published":1}}"#, "a".repeat(64))
    }
    #[test]
    fn only_a_signed_manifest_for_this_app_and_platform_is_accepted() {
        let key = SigningKey::from_bytes(&[7; 32]);
        let public = key.verifying_key();
        let good = manifest("0.2.0", "files/0.2.0/x/Setup.exe");
        assert_eq!(verify_envelope(&envelope(&key, &good), "stable", &public).unwrap().version, "0.2.0");
        let other = SigningKey::from_bytes(&[8; 32]);
        assert!(verify_envelope(&envelope(&other, &good), "stable", &public).is_err());
        assert!(verify_envelope(&envelope(&key, &good), "lab", &public).is_err());
        assert!(verify_envelope(&envelope(&key, &manifest("0.2.0", "files/../x")), "stable", &public).is_err());
        assert!(verify_envelope(&envelope(&key, &good.replace("windows", "beos").replace("macos", "beos").replace("linux", "beos")), "stable", &public).is_err());
        let mut tampered = envelope(&key, &good);
        let at = tampered.len() / 2;
        tampered[at] ^= 1;
        assert!(verify_envelope(&tampered, "stable", &public).is_err());
    }
    /// The lab override does not exist in a release build (the functions are compiled only with `qa-session`), so no file or
    /// variable can redirect the update server or key; this checks the result and that the source text keeps it that way.
    #[cfg(not(feature = "qa-session"))]
    #[test]
    fn release_builds_ignore_the_lab_override() {
        let (base, key) = source();
        assert_eq!(base, BASE);
        assert_eq!(key, UPDATE_KEY);
    }
    #[cfg(feature = "qa-session")]
    #[test]
    fn lab_builds_read_the_lab_override() {
        let (base, key) = lab_source_from(&format!(r#"{{"base":"http://10.0.0.1:8080/u/","key":"{}"}}"#, "ab".repeat(32))).unwrap();
        assert_eq!(base, "http://10.0.0.1:8080/u");
        assert_eq!(key, [0xab; 32]);
        assert!(lab_source_from(r#"{"base":"ftp://x","key":"00"}"#).is_none());
        assert!(lab_source_from(&format!(r#"{{"base":"file:///x","key":"{}"}}"#, "ab".repeat(32))).is_none());
    }
    #[test]
    fn the_embedded_key_is_valid() {
        assert!(VerifyingKey::from_bytes(&UPDATE_KEY).is_ok());
    }
}
