//! Android: the app hands the engine its secret storage (an Android Keystore key that never leaves
//! the device's secure hardware seals every value; the sealed values live in the app's private,
//! never-backed-up folder). The engine keeps only the device key and the remembered sign-in there.
//! There is no plaintext fallback: without the app's storage nothing is saved. The app also lends the
//! engine its clipboard (text), for the clipboard exchange with the remote computer.
use std::sync::{Arc, OnceLock};
use zeroize::Zeroizing;

/// What the Android app provides (implemented in Kotlin through `mixel_core_ffi`).
pub trait SecretStore: Send + Sync {
    /// The stored value, `Ok(None)` when there is none; `Err` when the storage could not be read.
    fn get(&self, name: &str) -> Result<Option<Zeroizing<Vec<u8>>>, String>;
    fn set(&self, name: &str, value: &[u8]) -> Result<(), String>;
    fn delete(&self, name: &str);
}

static STORE: OnceLock<Arc<dyn SecretStore>> = OnceLock::new();

/// Installs the app's storage once (later calls keep the first one).
pub fn install(store: Arc<dyn SecretStore>) {
    let _ = STORE.set(store);
}

fn store() -> Result<&'static Arc<dyn SecretStore>, String> {
    STORE.get().ok_or_else(|| "The secure storage of this phone is unavailable".to_owned())
}

/// Names are fixed by the engine: letters, digits, '-', '.' and '_' only.
fn checked(name: &str) -> Result<&str, String> {
    if name.is_empty()
        || name.len() > 96
        || !name.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_'))
    {
        return Err("Invalid secure storage name".into());
    }
    Ok(name)
}

pub fn get(name: &str) -> Result<Option<Zeroizing<Vec<u8>>>, String> {
    store()?.get(checked(name)?)
}
pub fn set(name: &str, value: &[u8]) -> Result<(), String> {
    if value.len() > 8192 {
        return Err("The secret is too large".into());
    }
    store()?.set(checked(name)?, value)
}
pub fn delete(name: &str) {
    if let (Ok(store), Ok(name)) = (store(), checked(name)) {
        store.delete(name);
    }
}

/// The phone's clipboard (text only), provided by the app.
pub trait PhoneClipboard: Send + Sync {
    fn read_text(&self) -> Option<String>;
    fn write_text(&self, text: &str) -> bool;
}

static CLIPBOARD: OnceLock<Arc<dyn PhoneClipboard>> = OnceLock::new();

pub fn install_clipboard(clipboard: Arc<dyn PhoneClipboard>) {
    let _ = CLIPBOARD.set(clipboard);
}

const NO_CLIPBOARD: &str = "The clipboard of this phone is unavailable.";
pub fn clipboard_text() -> Result<Zeroizing<String>, String> {
    CLIPBOARD.get().ok_or(NO_CLIPBOARD)?.read_text().map(Zeroizing::new).ok_or_else(|| "The clipboard of this phone holds no text.".into())
}
pub fn set_clipboard_text(text: &str) -> Result<(), String> {
    if CLIPBOARD.get().ok_or(NO_CLIPBOARD)?.write_text(text) { Ok(()) } else { Err(NO_CLIPBOARD.into()) }
}

#[cfg(test)]
mod tests {
    #[test]
    fn names_are_restricted() {
        assert!(super::checked("ed25519-device").is_ok());
        assert!(super::checked("remote-personal-0f0e0d0c-0b0a-4908-8706-050403020100").is_ok());
        assert!(super::checked("../escape").is_err());
        assert!(super::checked("").is_err());
        assert!(super::checked("a b").is_err());
    }
}
