//! Stable QA device identity in OS-protected storage. No plaintext fallback,
//! implicit rotation, synchronization, secret IPC or automatic creation at startup.
use ed25519_dalek::SigningKey;
#[cfg(target_os = "macos")]
use rand_core::OsRng;
#[cfg(target_os = "macos")]
use security_framework::os::macos::keychain::SecKeychain;
use serde::Serialize;
use std::sync::Mutex;
use zeroize::Zeroizing;

#[cfg(target_os = "macos")]
fn service_for_bundle(bundle: Option<&str>) -> Result<&'static str, String> {
    match bundle {
        Some("ch.mixel.remote.next.qa") => Ok("ch.mixel.remote.next.qa.device-identity.v1"),
        Some("ch.mixel.remote.next.qa.controller") => {
            Ok("ch.mixel.remote.next.qa.controller.device-identity.v1")
        }
        Some("ch.mixel.remote.controller") => Ok("ch.mixel.remote.controller.device-identity.v1"),
        Some("ch.mixel.remote") => Ok("ch.mixel.remote.device-identity.v1"),
        _ => Err("Device identity access requires a recognized Mixel Remote app bundle".into()),
    }
}
#[cfg(target_os = "macos")]
pub fn validated_bundle_scope() -> Result<&'static str, String> {
    let bundle = native_os_capture::macos_bundle::identifier();
    if (bundle.as_deref() == Some("ch.mixel.remote.controller"))
        != crate::service_config::controller_only()
    {
        return Err("Controller build and signed bundle identifier do not match".into());
    }
    service_for_bundle(bundle.as_deref())
}
#[cfg(target_os = "macos")]
const ACCOUNT: &str = "ed25519-device";
#[cfg(target_os = "macos")]
pub(crate) const KEYCHAIN_NOT_FOUND: i32 = NOT_FOUND;
const MAGIC: &[u8] = b"MIXEL-IDENTITY\x01";
#[cfg(target_os = "macos")]
const NOT_FOUND: i32 = -25300;
#[cfg(target_os = "macos")]
const DUPLICATE: i32 = -25299;

#[derive(Default)]
pub struct IdentityState(Mutex<Option<SigningKey>>);
#[derive(Serialize)]
pub struct PublicIdentity {
    public_key: String,
    storage: &'static str,
    enrolled: bool,
}
fn decode(bytes: &[u8]) -> Result<SigningKey, String> {
    if bytes.len() != MAGIC.len() + 32 || !bytes.starts_with(MAGIC) {
        return Err("Stored device identity is invalid; it was not overwritten".into());
    }
    let seed = Zeroizing::new(
        <[u8; 32]>::try_from(&bytes[MAGIC.len()..]).map_err(|_| "Invalid identity length")?,
    );
    Ok(SigningKey::from_bytes(&seed))
}
#[cfg(target_os = "macos")]
fn read(keychain: &SecKeychain, service: &str) -> Result<Option<SigningKey>, String> {
    match keychain.find_generic_password(service, ACCOUNT) {
        Ok((password, _item)) => decode(password.as_ref()).map(Some),
        Err(error) if error.code() == NOT_FOUND => Ok(None),
        Err(error) => Err(format!(
            "Keychain access failed (OS status {}); identity was not changed",
            error.code()
        )),
    }
}
#[cfg(target_os = "macos")]
pub fn prelogin_bundle() -> Option<&'static str> {
    match native_os_capture::macos_bundle::identifier().as_deref() {
        Some("ch.mixel.remote.prelogin") => Some("ch.mixel.remote.prelogin"),
        Some("ch.mixel.remote.prelogin.qa") => Some("ch.mixel.remote.prelogin.qa"),
        _ => None,
    }
}

#[cfg(target_os = "macos")]
fn load_or_create(create: bool) -> Result<SigningKey, String> {
    let service = validated_bundle_scope()?;
    if crate::service_config::controller_only() {
        return load_controller_identity(c"ed25519-device", create);
    }
    load_login_identity(service, create)
}

#[cfg(target_os = "macos")]
fn load_login_identity(service: &str, create: bool) -> Result<SigningKey, String> {
    let keychain = SecKeychain::default()
        .map_err(|error| format!("Default Keychain unavailable (OS status {})", error.code()))?;
    let loaded =
        match read(&keychain, service)? {
            Some(key) => Some(key),
            None if !create => return Err(
                "No saved device identity. Choose Create device identity to generate one locally."
                    .into(),
            ),
            None => {
                let key = SigningKey::generate(&mut OsRng);
                let seed = Zeroizing::new(key.to_bytes());
                let mut record = Zeroizing::new(Vec::with_capacity(MAGIC.len() + seed.len()));
                record.extend_from_slice(MAGIC);
                record.extend_from_slice(seed.as_ref());
                match keychain.add_generic_password(service, ACCOUNT, &record) {
                    Ok(()) => {}
                    Err(error) if error.code() == DUPLICATE => {}
                    Err(error) => {
                        return Err(format!(
                            "Cannot save identity (OS status {}); no plaintext fallback was used",
                            error.code()
                        ));
                    }
                }
                // Read back the actual winning record, including a concurrent
                // creator's value. Never publish an identity that wasn't saved.
                Some(read(&keychain, service)?.ok_or("Saved identity could not be read back")?)
            }
        };
    loaded.ok_or_else(|| "Device identity unavailable".into())
}
#[cfg(target_os = "macos")]
fn load_controller_identity(account: &std::ffi::CStr, create: bool) -> Result<SigningKey, String> {
    use native_os_capture::macos_controller_keychain as keychain;
    let mut record = Zeroizing::new([0_u8; MAGIC.len() + 32]);
    match keychain::read(account, &mut record) {
        Ok(()) => return decode(record.as_ref()),
        Err(NOT_FOUND) if create => {}
        Err(NOT_FOUND) => {
            return Err("No saved controller identity. Enroll this controller first.".into());
        }
        Err(status) => {
            return Err(format!(
                "Controller Keychain read failed (OS status {status}); identity was not changed"
            ));
        }
    }
    let key = SigningKey::generate(&mut OsRng);
    let seed = Zeroizing::new(key.to_bytes());
    record[..MAGIC.len()].copy_from_slice(MAGIC);
    record[MAGIC.len()..].copy_from_slice(seed.as_ref());
    match keychain::create(account, &record) {
        Ok(()) | Err(DUPLICATE) => {}
        Err(status) => {
            return Err(format!(
                "Controller Keychain could not save identity (OS status {status})"
            ));
        }
    }
    keychain::read(account, &mut record).map_err(|status| {
        format!("Controller Keychain could not read back identity (OS status {status})")
    })?;
    decode(record.as_ref())
}
#[cfg(target_os = "windows")]
#[allow(unsafe_code)]
#[path = "windows_identity_store.rs"]
mod windows_store;
#[cfg(target_os = "windows")]
fn load_or_create(create: bool) -> Result<SigningKey, String> {
    windows_store::load_or_create(None, create)
}
#[cfg(target_os = "windows")]
pub(crate) use windows_store::{location as windows_store_location, protect as windows_protect, unprotect as windows_unprotect};
#[cfg(target_os = "linux")]
#[allow(unsafe_code)]
#[path = "linux_identity_store.rs"]
mod linux_store;
#[cfg(target_os = "linux")]
fn load_or_create(create: bool) -> Result<SigningKey, String> {
    linux_store::load_or_create(None, create)
}
impl IdentityState {
    #[cfg(all(test, any(target_os = "linux", target_os = "windows")))]
    pub(crate) fn test_identity(key: SigningKey) -> Self {
        Self(Mutex::new(Some(key)))
    }

    #[cfg(any(target_os = "windows", target_os = "linux"))]
    pub fn personal(profile: uuid::Uuid, create: bool) -> Result<Self, String> {
        #[cfg(target_os = "windows")]
        let key = windows_store::load_or_create(Some(profile), create)?;
        #[cfg(target_os = "linux")]
        let key = linux_store::load_or_create(Some(profile), create)?;
        Ok(Self(Mutex::new(Some(key))))
    }

    #[cfg(target_os = "macos")]
    pub fn personal(profile: uuid::Uuid, create: bool) -> Result<Self, String> {
        let service = validated_bundle_scope()?;
        let key = if crate::service_config::controller_only() {
            let account = std::ffi::CString::new(format!("remote-personal-{profile}"))
                .map_err(|_| "Invalid Remote profile")?;
            load_controller_identity(&account, create)?
        } else {
            load_login_identity(&format!("{service}.personal.{profile}"), create)?
        };
        Ok(Self(Mutex::new(Some(key))))
    }

    #[cfg(target_os = "macos")]
    pub fn open_prelogin(&self, provision: bool) -> Result<(), String> {
        let bundle =
            prelogin_bundle().ok_or("A dedicated Mixel Remote login host bundle is required")?;
        let service = format!("{bundle}.machine-identity.v1");
        let _no_ui = SecKeychain::disable_user_interaction()
            .map_err(|e| format!("Cannot disable Keychain prompts (OS status {})", e.code()))?;
        let keychain = SecKeychain::open("/Library/Keychains/System.keychain")
            .map_err(|e| format!("Cannot open System Keychain (OS status {})", e.code()))?;
        let mut state = self
            .0
            .lock()
            .map_err(|_| "Device identity state unavailable")?;
        if state.is_none() {
            if provision && read(&keychain, &service)?.is_none() {
                let key = SigningKey::generate(&mut OsRng);
                let seed = Zeroizing::new(key.to_bytes());
                let mut record = Zeroizing::new(Vec::with_capacity(MAGIC.len() + seed.len()));
                record.extend_from_slice(MAGIC);
                record.extend_from_slice(seed.as_ref());
                match keychain.add_generic_password(&service, ACCOUNT, &record) {
                    Ok(()) => {}
                    Err(error) if error.code() == DUPLICATE => {}
                    Err(error) => {
                        return Err(format!(
                            "Cannot create the machine identity in System Keychain (OS status {})",
                            error.code()
                        ));
                    }
                }
            }
            *state = Some(
                read(&keychain, &service)?
                    .ok_or("Provision and enroll the machine identity first")?,
            );
        }
        Ok(())
    }

    pub fn delegate(
        &self,
        challenge: core_protocol::device_login::DeviceLoginChallenge,
        issued_at: u64,
    ) -> Result<core_protocol::delegation::DelegationProof, String> {
        let state = self
            .0
            .lock()
            .map_err(|_| "Device identity state unavailable")?;
        core_protocol::delegation::DelegationProof::new(
            challenge,
            issued_at,
            state
                .as_ref()
                .ok_or("Load the saved device identity first")?,
        )
        .map_err(|error| error.to_string())
    }
    #[cfg(target_os = "macos")]
    pub fn delegate_startup(
        &self,
        challenge: core_protocol::device_login::DeviceLoginChallenge,
        issued_at: u64,
    ) -> Result<core_protocol::delegation::DelegationProof, String> {
        let state = self
            .0
            .lock()
            .map_err(|_| "Device identity state unavailable")?;
        core_protocol::delegation::DelegationProof::new_unattended_host(
            challenge,
            issued_at,
            state
                .as_ref()
                .ok_or("Load the saved device identity first")?,
        )
        .map_err(|error| error.to_string())
    }
    pub fn handshake(
        &self,
        context: core_protocol::crypto::Context,
        role: core_protocol::crypto::Role,
        fingerprint: [u8; 32],
    ) -> Result<core_protocol::crypto::Handshake, String> {
        let state = self
            .0
            .lock()
            .map_err(|_| "Device identity state unavailable")?;
        core_protocol::crypto::Handshake::new(
            context,
            role,
            state
                .as_ref()
                .ok_or("Load the saved device identity first")?,
            fingerprint,
        )
        .map_err(|error| error.to_string())
    }
    pub fn public_key(&self) -> Result<ed25519_dalek::VerifyingKey, String> {
        self.0
            .lock()
            .map_err(|_| "Device identity state unavailable")?
            .as_ref()
            .map(SigningKey::verifying_key)
            .ok_or_else(|| "Load the saved device identity first".into())
    }
    pub fn prove(
        &self,
        challenge: core_protocol::device_login::DeviceLoginChallenge,
    ) -> Result<Vec<u8>, String> {
        let state = self
            .0
            .lock()
            .map_err(|_| "Device identity state unavailable")?;
        let key = state
            .as_ref()
            .ok_or("Load the saved identity before enrollment login")?;
        challenge.sign(key).map_err(|error| error.to_string())
    }
    /// Creation must originate in the main window's explicit local action. A
    /// duplicate from another process is re-read, never replaced by our seed.
    pub fn open(&self, create: bool) -> Result<PublicIdentity, String> {
        let mut state = self
            .0
            .lock()
            .map_err(|_| "Device identity state unavailable")?;
        if state.is_none() {
            *state = Some(load_or_create(create)?);
        }
        let key = state.as_ref().ok_or("Device identity is unavailable")?;
        let public_key = key
            .verifying_key()
            .to_bytes()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        Ok(PublicIdentity {
            public_key,
            storage: if cfg!(target_os = "macos") {
                "macOS Keychain"
            } else if cfg!(target_os = "windows") {
                "Windows user DPAPI"
            } else {
                "Linux Secret Service"
            },
            enrolled: false,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(any(target_os = "windows", target_os = "linux"))]
    fn assert_profile_keys_persist(first: uuid::Uuid, second: uuid::Uuid) {
        assert!(IdentityState::personal(first, false).is_err());
        assert!(IdentityState::personal(second, false).is_err());
        let first_key = IdentityState::personal(first, true)
            .unwrap()
            .public_key()
            .unwrap();
        let second_key = IdentityState::personal(second, true)
            .unwrap()
            .public_key()
            .unwrap();
        assert_ne!(first_key, second_key);
        assert_eq!(
            IdentityState::personal(first, false)
                .unwrap()
                .public_key()
                .unwrap(),
            first_key
        );
        assert_eq!(
            IdentityState::personal(second, true)
                .unwrap()
                .public_key()
                .unwrap(),
            second_key
        );
    }

    #[cfg(target_os = "windows")]
    #[test]
    #[ignore = "Run with a loaded Windows user profile and a password-backed logon"]
    fn personal_device_keys_persist_in_user_dpapi() {
        struct Profile(uuid::Uuid);
        impl Drop for Profile {
            fn drop(&mut self) {
                if let Ok(path) = windows_store::location(Some(self.0)) {
                    let _ = std::fs::remove_file(path);
                }
            }
        }
        let first = Profile(uuid::Uuid::new_v4());
        let second = Profile(uuid::Uuid::new_v4());
        assert_profile_keys_persist(first.0, second.0);
    }

    #[cfg(target_os = "linux")]
    #[test]
    #[ignore = "Run with a disposable unlocked Secret Service session"]
    fn personal_device_keys_persist_in_secret_service() {
        assert_profile_keys_persist(uuid::Uuid::new_v4(), uuid::Uuid::new_v4());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn qa_controller_cannot_reuse_or_rotate_the_primary_or_production_device_identity() {
        let primary = service_for_bundle(Some("ch.mixel.remote.next.qa")).unwrap();
        let controller = service_for_bundle(Some("ch.mixel.remote.next.qa.controller")).unwrap();
        let production = service_for_bundle(Some("ch.mixel.remote")).unwrap();
        assert_ne!(primary, controller);
        assert_ne!(primary, production);
        assert_ne!(controller, production);
        for bundle in [
            None,
            Some("ch.mixel.remote.next.qa.controller.evil"),
            Some("controller"),
        ] {
            assert!(service_for_bundle(bundle).is_err());
        }
    }
    #[test]
    fn corrupted_stored_identity_fails_closed_instead_of_rotating_trust() {
        let mut valid = Zeroizing::new(MAGIC.to_vec());
        valid.extend_from_slice(&[42; 32]);
        let public = decode(&valid).unwrap().verifying_key();
        assert_eq!(public, SigningKey::from_bytes(&[42; 32]).verifying_key());
        for length in 0..valid.len() {
            assert!(decode(&valid[..length]).is_err());
        }
        let mut trailing = Zeroizing::new(valid.to_vec());
        trailing.push(0);
        assert!(decode(&trailing).is_err());
        let mut wrong_version = Zeroizing::new(valid.to_vec());
        wrong_version[MAGIC.len() - 1] = 2;
        assert!(decode(&wrong_version).is_err());
        let original = valid.to_vec();
        assert!(decode(&wrong_version).is_err());
        assert_eq!(valid.as_slice(), original.as_slice());
    }
}
