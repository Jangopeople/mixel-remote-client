//! Secret Service storage via the distribution's libsecret helper. Private keys
//! travel only over inherited pipes, never command arguments or plaintext files.
//! A private advisory lock serializes competing Mixel creation requests.
use super::{MAGIC, decode};
use ed25519_dalek::SigningKey;
use rand_core::OsRng;
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
    path::PathBuf,
    process::{Command, Stdio},
    time::{Duration, Instant},
};
use zeroize::Zeroizing;
const HELPER: &str = "/usr/bin/secret-tool";
const SERVICE: &str = "ch.mixel.remote.next.qa.device-identity.v1";
const ACCOUNT: &str = "ed25519-device";
fn ownership_lock() -> Result<File, String> {
    // Effective UID identifies the current OS account, without shell commands.
    let uid = unsafe { libc::geteuid() };
    let parent = match std::env::var_os("XDG_STATE_HOME") {
        Some(path) if PathBuf::from(&path).is_absolute() => PathBuf::from(path),
        _ => {
            let home = std::env::var_os("HOME").ok_or("Home directory unavailable")?;
            let home = PathBuf::from(home);
            if !home.is_absolute() {
                return Err("Home directory must be absolute".into());
            }
            home.join(".local/state")
        }
    };
    std::fs::create_dir_all(&parent).map_err(|_| "Cannot prepare identity state directory")?;
    let parent_metadata =
        std::fs::symlink_metadata(&parent).map_err(|_| "Cannot inspect identity state parent")?;
    if !parent_metadata.is_dir()
        || parent_metadata.uid() != uid
        || parent_metadata.permissions().mode() & 0o022 != 0
    {
        return Err(
            "Identity state parent must be owned by this user and not writable by other users"
                .into(),
        );
    }
    let directory = parent.join("mixel-remote-next-qa");
    use std::os::unix::fs::DirBuilderExt;
    match std::fs::DirBuilder::new().mode(0o700).create(&directory) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(_) => return Err("Cannot create private identity state directory".into()),
    }
    let metadata =
        std::fs::symlink_metadata(&directory).map_err(|_| "Cannot inspect identity directory")?;
    if !metadata.is_dir() || metadata.uid() != uid || metadata.permissions().mode() & 0o077 != 0 {
        return Err("Identity directory ownership or permissions are unsafe".into());
    }
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(directory.join("identity.lock"))
        .map_err(|_| "Cannot open private identity lock")?;
    let metadata = lock
        .metadata()
        .map_err(|_| "Cannot inspect identity lock")?;
    if !metadata.is_file()
        || metadata.uid() != uid
        || metadata.nlink() != 1
        || metadata.permissions().mode() & 0o077 != 0
    {
        return Err("Identity lock ownership or permissions are unsafe".into());
    }
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        if lock.try_lock().is_ok() {
            return Ok(lock);
        }
        if Instant::now() >= deadline {
            return Err("Another identity operation is busy or file locking is unavailable".into());
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}
fn helper(account: &str, store: Option<&[u8]>) -> Result<Option<Zeroizing<Vec<u8>>>, String> {
    let mut command = Command::new(HELPER);
    if store.is_some() {
        command.args(["store", "--label=Mixel Remote QA device identity"]);
    } else {
        command.arg("lookup");
    }
    command
        .args(["service", SERVICE, "account", account])
        .env("LC_ALL", "C")
        .stdin(if store.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child=command.spawn().map_err(|_|"Install the distribution libsecret-tools package to use protected device identity storage")?;
    if let Some(secret) = store {
        let written = child
            .stdin
            .take()
            .ok_or("Secret Service input pipe unavailable")
            .and_then(|mut pipe| {
                pipe.write_all(secret)
                    .map_err(|_| "Cannot submit identity to Secret Service")
            });
        if let Err(error) = written {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error.into());
        }
    }
    let deadline = Instant::now() + Duration::from_secs(30);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {}
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("Cannot observe Secret Service operation".into());
            }
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(
                "Secret Service operation timed out; no plaintext fallback was used".into(),
            );
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    let mut output = Zeroizing::new(Vec::new());
    child
        .stdout
        .take()
        .ok_or("Secret Service output unavailable")?
        .take(4097)
        .read_to_end(&mut output)
        .map_err(|_| "Cannot read Secret Service response")?;
    let mut errors = Zeroizing::new(Vec::new());
    child
        .stderr
        .take()
        .ok_or("Secret Service error channel unavailable")?
        .take(4097)
        .read_to_end(&mut errors)
        .map_err(|_| "Cannot read Secret Service status")?;
    if output.len() > 4096 || errors.len() > 4096 {
        return Err("Secret Service response exceeds limit".into());
    }
    if status.success() {
        return Ok(Some(output));
    }
    // libsecret lookup reports no match with exit 1 and no diagnostic. A locked
    // or unavailable service is an error, never permission to replace an identity.
    if store.is_none() && status.code() == Some(1) && errors.is_empty() && output.is_empty() {
        return Ok(None);
    }
    Err(
        "Secret Service access failed; unlock the OS keyring locally. Identity was not replaced"
            .into(),
    )
}
fn read(account: &str) -> Result<Option<SigningKey>, String> {
    let Some(mut encoded) = helper(account, None)? else {
        return Ok(None);
    };
    while encoded
        .last()
        .is_some_and(|byte| matches!(*byte, b'\n' | b'\r'))
    {
        encoded.pop();
    }
    if encoded.len() != (MAGIC.len() + 32) * 2 || !encoded.iter().all(u8::is_ascii_hexdigit) {
        return Err("Stored device identity is invalid; it was not overwritten".into());
    }
    let mut bytes = Zeroizing::new(Vec::with_capacity(encoded.len() / 2));
    for pair in encoded.as_chunks::<2>().0 {
        let text = std::str::from_utf8(pair).map_err(|_| "Invalid identity encoding")?;
        bytes.push(u8::from_str_radix(text, 16).map_err(|_| "Invalid identity encoding")?);
    }
    decode(&bytes).map(Some)
}
pub(super) fn load_or_create(
    profile: Option<uuid::Uuid>,
    create: bool,
) -> Result<SigningKey, String> {
    let _lock = ownership_lock()?;
    let scoped_account = profile.map(|profile| format!("remote-personal-{profile}"));
    let account = scoped_account.as_deref().unwrap_or(ACCOUNT);
    if let Some(key) = read(account)? {
        return Ok(key);
    }
    if !create {
        return Err(
            "No saved device identity. Choose Create device identity to generate one locally."
                .into(),
        );
    }
    let key = SigningKey::generate(&mut OsRng);
    let seed = Zeroizing::new(key.to_bytes());
    let mut record = Zeroizing::new(MAGIC.to_vec());
    record.extend_from_slice(seed.as_ref());
    let mut encoded = Zeroizing::new(Vec::with_capacity(record.len() * 2 + 1));
    const HEX: &[u8] = b"0123456789abcdef";
    for byte in record.iter() {
        encoded.push(HEX[usize::from(byte >> 4)]);
        encoded.push(HEX[usize::from(byte & 15)]);
    }
    encoded.push(b'\n');
    helper(account, Some(&encoded))?;
    read(account)?.ok_or_else(|| "Saved identity could not be read back".into())
}
