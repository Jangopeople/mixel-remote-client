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
        ("macos", "arm64")
    } else if cfg!(target_os = "windows") {
        ("windows", "x64")
    } else {
        ("linux", "x64")
    }
}

/// Checks the signature and content of a channel file for this platform.
pub fn verify_envelope(bytes: &[u8], channel: &str, key: &VerifyingKey) -> Result<Manifest, String> {
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
    let (platform, arch) = target();
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
    let channel = channel();
    let (platform, arch) = target();
    let response = client()?
        .get(format!("{BASE}/{channel}/{platform}-{arch}/latest.json"))
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
    let key = VerifyingKey::from_bytes(&UPDATE_KEY).map_err(|_| "The update key is invalid")?;
    verify_envelope(&bytes, &channel, &key)
}

fn work_dir(version: &str) -> PathBuf {
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
        .get(format!("{BASE}/{}", manifest.file))
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

#[cfg(target_os = "windows")]
fn install(installer: &Path, _version: &str) -> Result<(), String> {
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
    let exe = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
    // Hidden helper: wait until this app has closed, install silently, start the new version.
    let script = "Start-Sleep 2; Add-Content -LiteralPath $env:MIXEL_UPDATE_LOG \"installer started\"; \
        $p=Start-Process -FilePath $env:MIXEL_UPDATE_FILE -ArgumentList '/S' -Wait -PassThru; \
        Add-Content -LiteralPath $env:MIXEL_UPDATE_LOG \"installer exit $($p.ExitCode)\"; \
        if($p.ExitCode -eq 0){ Start-Process -FilePath $env:MIXEL_UPDATE_APP }";
    let spawn = |flags: u32| {
        std::process::Command::new("powershell")
            .args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script])
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

#[cfg(target_os = "macos")]
fn install(image: &Path, version: &str) -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
    let bundle = exe
        .ancestors()
        .nth(3)
        .filter(|path| path.extension().is_some_and(|ext| ext == "app"))
        .ok_or("Mixel Remote is not running from an app bundle")?
        .to_path_buf();
    let parent = bundle.parent().ok_or("Cannot locate the app folder")?.to_path_buf();
    let identifier = native_os_capture::macos_bundle::identifier().ok_or("Cannot read this app's identity")?;
    let probe = parent.join(".mixel-remote-update-probe");
    std::fs::write(&probe, b"").map_err(|_| "The app folder is not writable; install the update from the download")?;
    let _ = std::fs::remove_file(&probe);
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
        let _ = std::fs::remove_dir_all(&staged);
        let _ = std::fs::remove_dir_all(&old);
        run("/usr/bin/ditto", &[source.as_os_str(), staged.as_os_str()])?;
        std::fs::rename(&bundle, &old).map_err(|_| "Cannot replace the app")?;
        if std::fs::rename(&staged, &bundle).is_err() {
            let _ = std::fs::rename(&old, &bundle);
            return Err("Cannot replace the app".into());
        }
        let _ = std::fs::remove_dir_all(&old);
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

#[cfg(target_os = "linux")]
fn install(package: &Path, _version: &str) -> Result<(), String> {
    // Read the program path first: once dpkg replaces the file, it reads as "… (deleted)".
    let exe = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
    let exe = PathBuf::from(exe.to_string_lossy().trim_end_matches(" (deleted)"));
    // Installing a system package needs the administrator password once (polkit prompt).
    run("pkexec", &["/usr/bin/dpkg".as_ref(), "-i".as_ref(), package.as_os_str()])
        .map_err(|_| "The update was not installed (administrator approval is needed)")?;
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
fn log_path() -> PathBuf {
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
    #[test]
    fn the_embedded_key_is_valid() {
        assert!(VerifyingKey::from_bytes(&UPDATE_KEY).is_ok());
    }
}
