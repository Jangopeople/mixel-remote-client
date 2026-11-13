//! What the app needs from the Mixel Remote engine. The real engine (`mixel_engine`, on its own
//! tokio runtime) and the demo engine (`mock`, for screenshots and tests) both provide it, so every
//! screen runs against either. Commands use the desktop app's names and arguments; arguments and
//! results are JSON. The picture and the input have their own calls.
use serde::de::DeserializeOwned;
use serde_json::Value;
use std::{future::Future, pin::Pin};

pub type Reply<T> = Pin<Box<dyn Future<Output = Result<T, String>>>>;

#[derive(Clone, Debug, PartialEq)]
pub struct Info {
    pub version: String,
    pub platform: String,
    pub controller_only: bool,
}

pub trait Engine {
    fn info(&self) -> Info;
    /// Runs one command; `args` is a JSON object, the result is JSON. Errors are the engine's
    /// (English) message.
    fn call(&self, command: &str, args: Value) -> Reply<Value>;
    /// The next session picture ("MRF2"), empty when none is waiting.
    fn next_video(&self, session: &str) -> Reply<Vec<u8>>;
    /// One input event; `false`: the remote computer could not apply it.
    fn input(&self, session: &str, event: Value) -> Reply<bool>;
    /// Before the app quits: ends sessions, goes offline.
    fn shutdown(&self) -> Reply<()>;
    /// The runtime for helpers that need tokio (the tray).
    fn runtime(&self) -> Option<tokio::runtime::Handle> {
        None
    }
}

/// Runs a command and reads its result as `T`.
pub async fn run<T: DeserializeOwned>(engine: &dyn Engine, command: &str, args: Value) -> Result<T, String> {
    let value = engine.call(command, args).await?;
    serde_json::from_value(value).map_err(|_| format!("The app could not read the engine's answer to {command}"))
}

/// The engine of this computer.
pub struct RealEngine {
    runtime: tokio::runtime::Runtime,
    context: mixel_engine::native_commands::Context,
}

impl RealEngine {
    /// `data_dir`: this app's private folder (queued removal of this computer and similar).
    pub fn new(data_dir: std::path::PathBuf) -> Result<Self, String> {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(4)
            .thread_name("mixel-engine")
            .enable_all()
            .build()
            .map_err(|_| "The engine could not start".to_owned())?;
        let context = mixel_engine::native_commands::Context::new(data_dir)?;
        Ok(Self { runtime, context })
    }
}

impl Engine for RealEngine {
    fn info(&self) -> Info {
        Info {
            version: mixel_engine::agent::APP_VERSION.to_owned(),
            platform: mixel_engine::native_commands::PLATFORM.to_owned(),
            controller_only: mixel_engine::service_config::controller_only(),
        }
    }

    fn call(&self, command: &str, args: Value) -> Reply<Value> {
        let context = self.context.clone();
        let name = command.to_owned();
        let raw = args.to_string();
        let handle = self.runtime.spawn(async move { mixel_engine::native_commands::dispatch(&context, &name, &raw).await });
        let command = command.to_owned();
        Box::pin(async move {
            let text = handle.await.map_err(|_| "The engine stopped this command".to_owned())??;
            serde_json::from_str(&text).map_err(|_| format!("The app could not read the engine's answer to {command}"))
        })
    }

    fn next_video(&self, session: &str) -> Reply<Vec<u8>> {
        let remote = self.context.remote.clone();
        let session = mixel_engine::native_commands::uuid(session);
        let handle = self.runtime.spawn(async move { remote.next_video(session?, false).await });
        Box::pin(async move { handle.await.map_err(|_| "The engine stopped the picture".to_owned())? })
    }

    fn input(&self, session: &str, event: Value) -> Reply<bool> {
        let remote = self.context.remote.clone();
        let session = mixel_engine::native_commands::uuid(session);
        let handle = self.runtime.spawn(async move {
            let event: mixel_engine::remote_session::InputEvent = serde_json::from_value(event).map_err(|_| "Invalid input event".to_owned())?;
            remote.input(session?, event).await
        });
        Box::pin(async move { handle.await.map_err(|_| "The engine stopped the input".to_owned())? })
    }

    fn shutdown(&self) -> Reply<()> {
        let context = self.context.clone();
        let handle = self.runtime.spawn(async move { context.shutdown().await });
        Box::pin(async move { handle.await.map_err(|_| "The engine could not stop cleanly".to_owned())? })
    }

    fn runtime(&self) -> Option<tokio::runtime::Handle> {
        Some(self.runtime.handle().clone())
    }
}
