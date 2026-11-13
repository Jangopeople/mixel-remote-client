//! Optional "stay signed in on this computer": the account refresh token is
//! kept only in OS-protected storage for the signed-in OS user (Windows DPAPI,
//! Linux Secret Service, macOS Keychain). No plaintext fallback.
use zeroize::Zeroizing;

const MAX_TOKEN: usize = 2048;

fn valid(token: &[u8]) -> bool {
    !token.is_empty() && token.len() <= MAX_TOKEN && token.iter().all(|b| b.is_ascii_graphic())
}

#[cfg(target_os = "windows")]
fn path() -> Result<std::path::PathBuf, String> {
    let identity = crate::device_identity::windows_store_location(None)?;
    Ok(identity
        .parent()
        .ok_or("Session storage folder unavailable")?
        .join("session-v1.dpapi"))
}
#[cfg(target_os = "windows")]
pub fn save(token: &str) -> Result<(), String> {
    if !valid(token.as_bytes()) {
        return Err("Invalid session".into());
    }
    let sealed = crate::device_identity::windows_protect(token.as_bytes())?;
    let path = path()?;
    if let Some(folder) = path.parent() {
        std::fs::create_dir_all(folder).map_err(|_| "Session storage folder unavailable")?;
    }
    let temporary = path.with_extension("tmp");
    std::fs::write(&temporary, sealed.as_slice()).map_err(|_| "Cannot save the session")?;
    std::fs::rename(&temporary, &path).map_err(|_| "Cannot save the session".into())
}
#[cfg(target_os = "windows")]
pub fn load() -> Result<Option<Zeroizing<String>>, String> {
    let sealed = match std::fs::read(path()?) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("Cannot read the saved session".into()),
    };
    let plain = crate::device_identity::windows_unprotect(&sealed)?;
    if !valid(&plain) {
        return Err("Saved session is invalid".into());
    }
    Ok(Some(Zeroizing::new(String::from_utf8_lossy(&plain).into_owned())))
}
#[cfg(target_os = "windows")]
pub fn clear() {
    if let Ok(path) = path() {
        let _ = std::fs::remove_file(path);
    }
}

#[cfg(target_os = "linux")]
const SERVICE: &str = "ch.mixel.remote.next.qa.session.v1";
#[cfg(target_os = "linux")]
fn secret_tool(args: &[&str], input: Option<&[u8]>) -> Result<(bool, Zeroizing<Vec<u8>>), String> {
    use std::io::{Read, Write};
    use std::process::{Command, Stdio};
    let mut child = Command::new("/usr/bin/secret-tool")
        .args(args)
        .env("LC_ALL", "C")
        .stdin(if input.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "Install libsecret-tools to stay signed in")?;
    if let Some(input) = input {
        child
            .stdin
            .take()
            .ok_or("Secret Service input unavailable")?
            .write_all(input)
            .map_err(|_| "Cannot save the session")?;
    }
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|_| "Secret Service unavailable")? {
            break status;
        }
        if std::time::Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Secret Service timed out".into());
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    };
    let mut output = Zeroizing::new(Vec::new());
    child
        .stdout
        .take()
        .ok_or("Secret Service output unavailable")?
        .take(MAX_TOKEN as u64 + 2)
        .read_to_end(&mut output)
        .map_err(|_| "Cannot read the saved session")?;
    Ok((status.success(), output))
}
#[cfg(target_os = "linux")]
pub fn save(token: &str) -> Result<(), String> {
    if !valid(token.as_bytes()) {
        return Err("Invalid session".into());
    }
    let (ok, _) = secret_tool(
        &["store", "--label=Mixel Remote sign-in", "service", SERVICE, "account", "refresh"],
        Some(token.as_bytes()),
    )?;
    if ok { Ok(()) } else { Err("Cannot save the session; unlock the keyring".into()) }
}
#[cfg(target_os = "linux")]
pub fn load() -> Result<Option<Zeroizing<String>>, String> {
    let (ok, mut output) = secret_tool(&["lookup", "service", SERVICE, "account", "refresh"], None)?;
    while output.last().is_some_and(|b| matches!(*b, b'\n' | b'\r')) {
        output.pop();
    }
    if !ok || output.is_empty() {
        return Ok(None);
    }
    if !valid(&output) {
        return Err("Saved session is invalid".into());
    }
    Ok(Some(Zeroizing::new(String::from_utf8_lossy(&output).into_owned())))
}
#[cfg(target_os = "linux")]
pub fn clear() {
    let _ = secret_tool(&["clear", "service", SERVICE, "account", "refresh"], None);
}

#[cfg(target_os = "macos")]
fn service() -> Result<String, String> {
    Ok(format!("{}.session.v1", crate::device_identity::validated_bundle_scope()?))
}
#[cfg(target_os = "macos")]
pub fn save(token: &str) -> Result<(), String> {
    use security_framework::os::macos::keychain::SecKeychain;
    if !valid(token.as_bytes()) {
        return Err("Invalid session".into());
    }
    let keychain = SecKeychain::default().map_err(|_| "Keychain unavailable")?;
    let service = service()?;
    if let Ok((_, item)) = keychain.find_generic_password(&service, "refresh") {
        item.delete();
    }
    keychain
        .add_generic_password(&service, "refresh", token.as_bytes())
        .map_err(|_| "Cannot save the session".into())
}
#[cfg(target_os = "macos")]
pub fn load() -> Result<Option<Zeroizing<String>>, String> {
    use security_framework::os::macos::keychain::SecKeychain;
    let keychain = SecKeychain::default().map_err(|_| "Keychain unavailable")?;
    match keychain.find_generic_password(&service()?, "refresh") {
        Ok((password, _)) if valid(password.as_ref()) => Ok(Some(Zeroizing::new(
            String::from_utf8_lossy(password.as_ref()).into_owned(),
        ))),
        Ok(_) => Err("Saved session is invalid".into()),
        Err(error) if error.code() == crate::device_identity::KEYCHAIN_NOT_FOUND => Ok(None),
        Err(_) => Err("Keychain access failed".into()),
    }
}
#[cfg(target_os = "macos")]
pub fn clear() {
    use security_framework::os::macos::keychain::SecKeychain;
    if let (Ok(keychain), Ok(service)) = (SecKeychain::default(), service())
        && let Ok((_, item)) = keychain.find_generic_password(&service, "refresh")
    {
        item.delete();
    }
}

/// iPhone and iPad: the refresh token in the app's own Keychain (this device only).
#[cfg(target_os = "ios")]
const IOS_SERVICE: &str = "ch.mixel.remote.controller.session.v1";
#[cfg(target_os = "ios")]
pub fn save(token: &str) -> Result<(), String> {
    if !valid(token.as_bytes()) {
        return Err("Invalid session".into());
    }
    security_framework::passwords::set_generic_password(IOS_SERVICE, "refresh", token.as_bytes())
        .map_err(|_| "Cannot save the session".into())
}
#[cfg(target_os = "ios")]
pub fn load() -> Result<Option<Zeroizing<String>>, String> {
    match security_framework::passwords::get_generic_password(IOS_SERVICE, "refresh") {
        Ok(password) if valid(&password) => Ok(Some(Zeroizing::new(String::from_utf8_lossy(&password).into_owned()))),
        Ok(_) => Err("Saved session is invalid".into()),
        Err(error) if error.code() == crate::device_identity::KEYCHAIN_NOT_FOUND => Ok(None),
        Err(_) => Err("Keychain access failed".into()),
    }
}
#[cfg(target_os = "ios")]
pub fn clear() {
    let _ = security_framework::passwords::delete_generic_password(IOS_SERVICE, "refresh");
}

/// Android: the refresh token in the app's Keystore-sealed storage (this device only).
#[cfg(target_os = "android")]
pub fn save(token: &str) -> Result<(), String> {
    if !valid(token.as_bytes()) {
        return Err("Invalid session".into());
    }
    crate::android_secrets::set("session-refresh", token.as_bytes()).map_err(|_| "Cannot save the session".into())
}
#[cfg(target_os = "android")]
pub fn load() -> Result<Option<Zeroizing<String>>, String> {
    match crate::android_secrets::get("session-refresh") {
        Ok(Some(token)) if valid(&token) => Ok(Some(Zeroizing::new(String::from_utf8_lossy(&token).into_owned()))),
        Ok(Some(_)) => Err("Saved session is invalid".into()),
        Ok(None) => Ok(None),
        Err(_) => Err("The secure storage could not be read".into()),
    }
}
#[cfg(target_os = "android")]
pub fn clear() {
    crate::android_secrets::delete("session-refresh");
}
