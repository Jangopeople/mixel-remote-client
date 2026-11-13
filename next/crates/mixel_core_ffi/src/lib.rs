//! The Mixel Remote engine for the native apps (Swift on Apple systems, Kotlin on Android).
//!
//! The app talks to one object, [`MixelEngine`]. Commands use the same names and arguments as the
//! desktop app's window commands (`remote_status`, `saas_members`, …): arguments go in as JSON, the
//! result comes back as JSON, an error as its message. The session picture and the input events have
//! their own calls, because they run many times a second.
//!
//! Tokens, private keys, SDP and ICE never cross into Swift: the engine keeps them, exactly as it keeps
//! them away from the desktop app's web view.
#![allow(unsafe_code)] // UniFFI's generated scaffolding (the C interface Swift calls) is unsafe by nature.

/// The command dispatcher lives in the engine, shared with the native Linux app (which calls it directly).
use mixel_engine::native_commands as commands;

use std::sync::Arc;

uniffi::setup_scaffolding!();

/// A failed command; `message` is shown to the person (English; the app translates known texts).
/// Flat: Kotlin gets it as the exception's message (Swift keeps `Failed(message:)`).
#[derive(Debug, uniffi::Error)]
#[uniffi(flat_error)]
pub enum EngineError {
    Failed { message: String },
}
impl std::fmt::Display for EngineError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Failed { message } => formatter.write_str(message),
        }
    }
}
impl std::error::Error for EngineError {}
impl From<String> for EngineError {
    fn from(message: String) -> Self {
        Self::Failed { message }
    }
}

/// Facts about this build, for About and the update check.
#[derive(uniffi::Record)]
pub struct CoreInfo {
    /// The engine's release version (the app's version).
    pub version: String,
    /// The platform the engine runs on: `macos`, `ios` or `android`.
    pub platform: String,
    /// This build can only control other computers (the phone app).
    pub controller_only: bool,
}

/// The app's modes without any window, exactly as the desktop app 0.1.x ran them (launch agents,
/// install scripts and the sign-in screen host call the app's executable with these arguments):
/// `--version`, `--signin-enroll` (mass rollout), `--startup-host` (always-on access),
/// `--prelogin-host`, `--provision-prelogin-identity`, `--validate-prelogin-settings`,
/// `--validate-identity-scope`, `--validate-public-config`.
///
/// `arguments` are the process arguments (including the program). `None`: not a headless mode,
/// start the app. Otherwise the exit code; messages are printed to standard output/error.
#[uniffi::export]
pub fn run_command_line(arguments: Vec<String>) -> Option<i32> {
    let mode = arguments.get(1)?.as_str();
    let result = match mode {
        "--version" => {
            println!("Mixel Remote {}", mixel_engine::agent::APP_VERSION);
            Ok(())
        }
        #[cfg(target_os = "macos")]
        "--signin-enroll" => mixel_engine::deploy_token::read(arguments.get(2).map(String::as_str)).and_then(|token| {
            mixel_engine::service_config::require_host().and_then(|()| mixel_engine::macos_rollout::enroll(&token))
        }),
        #[cfg(target_os = "macos")]
        "--startup-host" | "--prelogin-host" | "--provision-prelogin-identity" | "--validate-prelogin-settings" => {
            mixel_engine::service_config::require_host().and_then(|()| mixel_engine::macos_startup_host::run())
        }
        #[cfg(target_os = "macos")]
        "--validate-identity-scope" => mixel_engine::device_identity::validated_bundle_scope().map(|scope| println!("PASS: {scope}")),
        "--validate-public-config" => {
            use std::io::Read;
            let mut bytes = Vec::new();
            std::io::stdin()
                .take(32769)
                .read_to_end(&mut bytes)
                .map_err(|_| "Cannot read public service configuration".to_owned())
                .and_then(|_| mixel_engine::service_config::ServiceConfig::validate_public_bytes(&bytes))
                .map(|()| println!("PASS: public service configuration and authority keys validated"))
        }
        _ => return None,
    };
    Some(match result {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("{error}");
            2
        }
    })
}

/// Android: the app's secret storage (an Android Keystore key seals every value). The engine keeps
/// the device key and the remembered sign-in there and nowhere else.
#[cfg(target_os = "android")]
#[uniffi::export(with_foreign)]
pub trait SecureStorage: Send + Sync {
    /// The stored value, or `None` when there is none.
    fn read(&self, name: String) -> Result<Option<Vec<u8>>, StorageError>;
    fn write(&self, name: String, value: Vec<u8>) -> Result<(), StorageError>;
    fn remove(&self, name: String);
}

/// The app's storage could not read or save a secret (`reason` is shown to the person).
#[cfg(target_os = "android")]
#[derive(Debug, uniffi::Error)]
pub enum StorageError {
    Failed { reason: String },
}
#[cfg(target_os = "android")]
impl std::fmt::Display for StorageError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Failed { reason } => formatter.write_str(reason),
        }
    }
}
#[cfg(target_os = "android")]
impl std::error::Error for StorageError {}
#[cfg(target_os = "android")]
impl From<uniffi::UnexpectedUniFFICallbackError> for StorageError {
    fn from(error: uniffi::UnexpectedUniFFICallbackError) -> Self {
        Self::Failed { reason: format!("The secure storage failed: {}", error.reason) }
    }
}

#[cfg(target_os = "android")]
struct AndroidStore(Arc<dyn SecureStorage>);
#[cfg(target_os = "android")]
impl mixel_engine::android_secrets::SecretStore for AndroidStore {
    fn get(&self, name: &str) -> Result<Option<zeroize::Zeroizing<Vec<u8>>>, String> {
        self.0.read(name.to_owned()).map(|value| value.map(zeroize::Zeroizing::new)).map_err(|error| error.to_string())
    }
    fn set(&self, name: &str, value: &[u8]) -> Result<(), String> {
        self.0.write(name.to_owned(), value.to_vec()).map_err(|error| error.to_string())
    }
    fn delete(&self, name: &str) {
        self.0.remove(name.to_owned());
    }
}

/// Android: the phone's clipboard (text), for the clipboard exchange in a session.
#[cfg(target_os = "android")]
#[uniffi::export(with_foreign)]
pub trait PhoneClipboard: Send + Sync {
    fn read_text(&self) -> Option<String>;
    fn write_text(&self, text: String) -> bool;
}

#[cfg(target_os = "android")]
struct AndroidClipboard(Arc<dyn PhoneClipboard>);
#[cfg(target_os = "android")]
impl mixel_engine::android_secrets::PhoneClipboard for AndroidClipboard {
    fn read_text(&self) -> Option<String> {
        self.0.read_text().filter(|text| text.len() <= 1 << 20)
    }
    fn write_text(&self, text: &str) -> bool {
        self.0.write_text(text.to_owned())
    }
}

/// Android: installs the app's secret storage, its clipboard and the phone's trusted certificates.
/// Call once, before the first engine is made.
#[cfg(target_os = "android")]
#[uniffi::export]
pub fn install_android(storage: Arc<dyn SecureStorage>, clipboard: Arc<dyn PhoneClipboard>) {
    mixel_engine::android_secrets::install(Arc::new(AndroidStore(storage)));
    mixel_engine::android_secrets::install_clipboard(Arc::new(AndroidClipboard(clipboard)));
    // The connection service's secure WebSocket checks certificates against the system's trusted
    // roots; on Android they are in this folder (the HTTPS client brings its own roots).
    if std::env::var_os("SSL_CERT_DIR").is_none() {
        // SAFETY: called once by the app before any engine thread exists.
        unsafe { std::env::set_var("SSL_CERT_DIR", "/system/etc/security/cacerts") };
    }
}

/// One engine per app. It owns the account, the connection to Mixel and the sessions.
#[derive(uniffi::Object)]
pub struct MixelEngine {
    runtime: tokio::runtime::Runtime,
    context: commands::Context,
}

#[uniffi::export]
impl MixelEngine {
    /// `data_dir`: the app's private folder (Application Support), for the queued removal of this
    /// computer and similar small files.
    #[uniffi::constructor]
    pub fn new(data_dir: String) -> Result<Arc<Self>, EngineError> {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(4)
            .thread_name("mixel-engine")
            .enable_all()
            .build()
            .map_err(|_| EngineError::from("The engine could not start".to_owned()))?;
        let context = commands::Context::new(std::path::PathBuf::from(data_dir))?;
        Ok(Arc::new(Self { runtime, context }))
    }

    pub fn info(&self) -> CoreInfo {
        CoreInfo {
            version: mixel_engine::agent::APP_VERSION.to_owned(),
            platform: commands::PLATFORM.to_owned(),
            controller_only: mixel_engine::service_config::controller_only(),
        }
    }

    /// Runs one command. `args` is a JSON object (`{}` without arguments); the result is JSON.
    pub async fn call(&self, command: String, args: String) -> Result<String, EngineError> {
        let context = self.context.clone();
        self.runtime
            .spawn(async move { commands::dispatch(&context, &command, &args).await })
            .await
            .map_err(|_| EngineError::from("The engine stopped this command".to_owned()))?
            .map_err(EngineError::from)
    }

    /// The next picture of a session: "MRF2" (RGBA rows) or empty when none is waiting yet.
    /// `compressed`: the app decodes H.264 itself ("MRH1" packets).
    pub async fn next_video(&self, session: String, compressed: bool) -> Result<Vec<u8>, EngineError> {
        let remote = self.context.remote.clone();
        let session = commands::uuid(&session)?;
        self.runtime
            .spawn(async move { remote.next_video(session, compressed).await })
            .await
            .map_err(|_| EngineError::from("The engine stopped the picture".to_owned()))?
            .map_err(EngineError::from)
    }

    /// One input event for a session (`{"kind":"key","usage":4,"down":true}` and so on, as the
    /// desktop app sends them). `false`: the remote computer could not apply it.
    pub async fn input(&self, session: String, event: String) -> Result<bool, EngineError> {
        let remote = self.context.remote.clone();
        let session = commands::uuid(&session)?;
        let event: mixel_engine::remote_session::InputEvent =
            serde_json::from_str(&event).map_err(|_| EngineError::from("Invalid input event".to_owned()))?;
        self.runtime
            .spawn(async move { remote.input(session, event).await })
            .await
            .map_err(|_| EngineError::from("The engine stopped the input".to_owned()))?
            .map_err(EngineError::from)
    }

    /// Before the app quits: ends sessions, goes offline and forgets the sign-in in memory (a
    /// remembered sign-in stays in the Keychain).
    pub async fn shutdown(&self) -> Result<(), EngineError> {
        let context = self.context.clone();
        self.runtime
            .spawn(async move { context.shutdown().await })
            .await
            .map_err(|_| EngineError::from("The engine could not stop cleanly".to_owned()))?
            .map_err(EngineError::from)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_headless_modes_run_without_a_window() {
        let args = |list: &[&str]| list.iter().map(|item| (*item).to_owned()).collect::<Vec<_>>();
        assert_eq!(run_command_line(args(&["app", "--version"])), Some(0));
        assert_eq!(run_command_line(args(&["app"])), None);
        assert_eq!(run_command_line(args(&["app", "--autostart"])), None);
        assert_eq!(run_command_line(args(&["app", "-NSDocumentRevisionsDebugMode", "YES"])), None);
        // A deploy token is never accepted as an argument.
        #[cfg(target_os = "macos")]
        assert_eq!(run_command_line(args(&["app", "--signin-enroll", "mrt_abcdefghijklmnopqrstuv"])), Some(2));
    }
}
