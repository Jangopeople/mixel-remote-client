//! Native authenticated signaling. No bearer, private key, SDP or ICE enters
//! the webview. One attended connection is owned by this desktop window.
use crate::session_platform;
use crate::remote_recovery::{Backoff, Failure};
use crate::remote_video::VideoMailbox;
use crate::{
    device_identity::IdentityState,
    account_auth::{Credential, AccountAuth},
    remote_peer,
};
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use signaling_server::auth::{EnrolledPrincipal, Principal};
use signaling_server::protocol::{
    ClientMessage, ErrorCode, MAX_MESSAGE, PairingPrompt, ServerMessage, Signal, SupportCode,
};
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::{
    sync::{Mutex, mpsc},
    task::JoinHandle,
};
use tokio_tungstenite::{
    connect_async_with_config,
    tungstenite::{Message, protocol::WebSocketConfig},
};
use uuid::Uuid;
use zeroize::Zeroize;

#[derive(Clone, Serialize)]
pub struct Status {
    pub controller_only: bool,
    pub platform: &'static str,
    pub phase: String,
    pub message: String,
    pub host: Option<u32>,
    pub session: Option<Uuid>,
    pub consent: Option<Uuid>,
    pub peer: Option<u32>,
    pub requester: Option<Uuid>,
    pub received: u64,
    pub sharing: bool,
    pub control: bool,
    pub clipboard: bool,
    pub files: bool,
    pub recording_permitted: bool,
    pub recording: Option<crate::remote_recording::View>,
    pub previous_recording: Option<crate::remote_recording::View>,
    pub input_access: bool,
    pub unattended_enabled: bool,
    unattended_pending: Option<UnattendedChange>,
    pub unattended_session: bool,
    #[serde(skip)]
    unattended: Option<UnattendedSource>,
    pub pairing: Option<PairingPrompt>,
    pub pairing_message: Option<String>,
    pub code_required: bool,
    pub support_code: Option<SupportCode>,
    #[serde(skip)]
    code_expires_at: Option<std::time::Instant>,
}
impl Default for Status {
    fn default() -> Self {
        Self {
            controller_only: crate::service_config::controller_only(),
            platform: std::env::consts::OS,
            phase: "offline".into(),
            message: if crate::service_config::controller_only() {
                "Sign in to connect to managed devices"
            } else {
                "Sign in and register this enrolled device to receive connections"
            }
            .into(),
            host: None,
            session: None,
            consent: None,
            peer: None,
            requester: None,
            received: 0,
            sharing: false,
            control: false,
            clipboard: false,
            files: false,
            recording_permitted: false,
            recording: None,
            previous_recording: None,
            input_access: false,
            unattended_enabled: false,
            unattended_pending: None,
            unattended_session: false,
            unattended: None,
            pairing: None,
            pairing_message: None,
            code_required: false,
            support_code: None,
            code_expires_at: None,
        }
    }
}
#[derive(Clone)]
struct UnattendedSource {
    source: String,
    control: bool,
}

impl UnattendedSource {
    fn capture_allowed(&self) -> bool {
        if self.source == "qa-relay-fixture" {
            return !self.control && session_platform::relay_fixture_enabled();
        }
        session_platform::capture_access()
            && (!self.control || session_platform::input_access())
    }
}

#[derive(Clone)]
enum RegistrationAuth {
    Interactive(AccountAuth),
    #[cfg(target_os = "macos")]
    Startup {
        enrolled: Arc<EnrolledPrincipal>,
        source: UnattendedSource,
    },
}

impl RegistrationAuth {
    async fn credential(
        &self,
        cancel: Option<&Arc<AtomicBool>>,
    ) -> Result<Option<Credential>, String> {
        match self {
            Self::Interactive(auth) => auth.credential(cancel).await.map(Some),
            #[cfg(target_os = "macos")]
            Self::Startup { .. } => Ok(None),
        }
    }

    fn principal<'a>(
        &'a self,
        credential: &'a Option<Credential>,
    ) -> Result<&'a Principal, String> {
        match self {
            Self::Interactive(_) => credential
                .as_ref()
                .map(|credential| &credential.principal)
                .ok_or_else(|| "Interactive account authorization is unavailable".into()),
            #[cfg(target_os = "macos")]
            Self::Startup { enrolled, .. } => Ok(&enrolled.principal),
        }
    }

    async fn enrollment(
        &self,
        credential: &Option<Credential>,
        identity: &IdentityState,
    ) -> Result<EnrolledPrincipal, String> {
        #[cfg(target_os = "macos")]
        if let Self::Startup { enrolled, .. } = self {
            return Ok(enrolled.as_ref().clone());
        }
        let credential = credential
            .as_ref()
            .ok_or("Interactive account authorization is unavailable")?;
        let device = credential
            .ism
            .device_for_key(&credential.token, &identity.public_key()?)
            .await
            .map_err(|_| {
                if credential.principal.is_personal() {
                    "Register this computer in Device settings first"
                } else {
                    "This computer is not registered for the current account"
                }
            })?;
        credential
            .ism
            .authenticate_device(&credential.token, device)
            .await
            .map_err(|_| "This device is not enrolled for the current account".into())
    }
}

async fn registration_ticket(
    credential: &Option<Credential>,
    enrolled: &EnrolledPrincipal,
    identity: &IdentityState,
) -> Result<core_protocol::delegation::DeviceTicket, Failure> {
    match credential {
        Some(credential) => crate::device_tickets::issue(credential, enrolled, identity)
            .await
            .map_err(Into::into),
        #[cfg(target_os = "macos")]
        None => crate::device_tickets::issue_startup(enrolled, identity).await,
        #[cfg(not(target_os = "macos"))]
        None => Err("Interactive account authorization is unavailable".into()),
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum UnattendedChange {
    Enabling,
    DisablingAfterEnable,
    Disabling,
}

impl Status {
    fn acknowledge_unattended(&mut self, enabled: bool) {
        use UnattendedChange::*;
        match self.unattended_pending.take() {
            Some(Enabling) if enabled && self.unattended.is_some() => {
                self.unattended_enabled = true;
            }
            Some(DisablingAfterEnable) => {
                // Consume the enable reply, even if the broker rejected the enable.
                // The following disable reply is still in flight.
                self.unattended_pending = Some(Disabling);
            }
            _ => {
                self.unattended = None;
                self.unattended_enabled = false;
            }
        }
    }
}

pub(crate) enum Command {
    Request(u32, Option<SupportCode>, bool),
    ConfigureUnattended {
        enabled: bool,
    },
    Pairing(Option<Uuid>),
    SupportCode {
        required: bool,
    },
    Decide {
        session: Uuid,
        accept: bool,
        source: String,
        #[cfg(target_os = "linux")]
        portal: Option<Arc<crate::wayland_portal::Grant>>,
        confirm: bool,
        control: bool,
        clipboard: bool,
        files: bool,
        chat: bool,
        recording: bool,
    },
    Close(Uuid),
    Restart(Uuid),
    Signal {
        session: Uuid,
        signal: Signal,
    },
    Finished {
        session: Uuid,
        result: Result<(), String>,
    },
}
#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum InputEvent {
    Text { text: String },
    Key { usage: u16, down: bool },
    Pointer { x: u16, y: u16 },
    Button { button: u8, down: bool },
    ButtonClick { button: u8, down: bool, clicks: u8 },
    Wheel { horizontal: i16, vertical: i16 },
    ReleaseAll,
}
impl InputEvent {
    pub(crate) fn borrowed(&self) -> core_protocol::input::Input<'_> {
        use core_protocol::input::Input;
        match self {
            Self::Text { text } => Input::Text(text),
            Self::Key { usage, down } => Input::Key {
                usage: *usage,
                down: *down,
            },
            Self::Pointer { x, y } => Input::Pointer {
                display: 0,
                x: *x,
                y: *y,
            },
            Self::Button { button, down } => Input::Button {
                button: *button,
                down: *down,
            },
            Self::ButtonClick {
                button,
                down,
                clicks,
            } => Input::ButtonClick {
                button: *button,
                down: *down,
                clicks: *clicks,
            },
            Self::Wheel {
                horizontal,
                vertical,
            } => Input::Wheel {
                horizontal: *horizontal,
                vertical: *vertical,
            },
            Self::ReleaseAll => Input::ReleaseAll,
        }
    }
}
impl Drop for InputEvent {
    fn drop(&mut self) {
        if let Self::Text { text } = self {
            text.zeroize();
        }
    }
}
pub(crate) struct InputCommand {
    pub event: InputEvent,
    pub deadline: std::time::Instant,
    pub reply: tokio::sync::oneshot::Sender<Result<bool, String>>,
}
pub(crate) struct ClipboardCommand {
    pub push: bool,
    pub deadline: std::time::Instant,
    pub reply: tokio::sync::oneshot::Sender<Result<(), String>>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Decision {
    pub session: Uuid,
    pub accept: bool,
    pub source: String,
    pub confirm: bool,
    pub control: bool,
    pub clipboard: bool,
    pub files: bool,
    #[serde(default)]
    pub chat: bool,
    #[serde(default)]
    pub recording: bool,
}
struct Inner {
    generation: u64,
    status: Status,
    cancel: Arc<AtomicBool>,
    commands: Option<mpsc::Sender<Command>>,
    task: Option<JoinHandle<()>>,
    video: Option<Arc<VideoMailbox>>,
    session_cancel: Option<Arc<AtomicBool>>,
    consent_cancel: Option<Arc<AtomicBool>>,
    file_permission: Option<bool>,
    input: Option<mpsc::Sender<InputCommand>>,
    clipboard: Option<mpsc::Sender<ClipboardCommand>>,
    renew_after: Option<std::time::Instant>,
    stopping: bool,
}
#[derive(Clone)]
pub struct RemoteSession(
    Arc<Mutex<Inner>>,
    Arc<std::sync::Mutex<Vec<std::thread::JoinHandle<()>>>>,
    crate::remote_chat::ChatHub,
    crate::remote_recording::RecordingHub,
    crate::remote_files::FileHub,
);
impl Default for RemoteSession {
    fn default() -> Self {
        Self(
            Arc::new(Mutex::new(Inner {
                generation: 0,
                status: Status::default(),
                cancel: Arc::new(AtomicBool::new(false)),
                commands: None,
                task: None,
                video: None,
                session_cancel: None,
                consent_cancel: None,
                file_permission: None,
                input: None,
                clipboard: None,
                renew_after: None,
                stopping: false,
            })),
            Arc::new(std::sync::Mutex::new(Vec::new())),
            crate::remote_chat::ChatHub::default(),
            crate::remote_recording::RecordingHub::default(),
            crate::remote_files::FileHub::default(),
        )
    }
}
impl RemoteSession {
    pub(crate) fn recording_hub(&self) -> crate::remote_recording::RecordingHub {
        self.3.clone()
    }
    pub(crate) fn chat_hub(&self) -> crate::remote_chat::ChatHub {
        self.2.clone()
    }

    pub async fn require_chat_session(&self, session: Uuid) -> Result<(), String> {
        let inner = self.0.lock().await;
        if inner.status.session != Some(session)
            || inner.status.phase != "active"
            || inner.cancel.load(Ordering::Acquire)
            || inner
                .session_cancel
                .as_ref()
                .is_none_or(|flag| flag.load(Ordering::Acquire))
        {
            return Err("Chat requires a current, accepted session".into());
        }
        Ok(())
    }

    pub(crate) fn file_hub(&self) -> crate::remote_files::FileHub {
        self.4.clone()
    }
    pub(crate) async fn file_grant(
        &self,
        generation: u64,
        session: Uuid,
        allowed: bool,
    ) -> Result<(), String> {
        let mut inner = self.0.lock().await;
        if inner.generation != generation
            || inner.status.session != Some(session)
            || inner.cancel.load(Ordering::Acquire)
            || inner.status.phase == "closing"
        {
            return Err("The accepted file scope stopped".into());
        }
        if inner
            .file_permission
            .is_some_and(|previous| previous != allowed)
        {
            if let Some(cancel) = &inner.consent_cancel {
                cancel.store(true, Ordering::Release);
            }
            if let Some(cancel) = &inner.session_cancel {
                cancel.store(true, Ordering::Release);
            }
            return Err("The host file grant changed; a fresh local Accept is required".into());
        }
        inner.file_permission = Some(allowed);
        inner.status.files = allowed;
        Ok(())
    }
    pub async fn file_scope(&self, consent: Uuid) -> Result<crate::remote_files::FileScope, String> {
        let inner = self.0.lock().await;
        if inner.status.consent != Some(consent)
            || !inner.status.files
            || !matches!(inner.status.phase.as_str(), "active" | "negotiating")
            || inner.cancel.load(Ordering::Acquire)
        {
            return Err("The host has not granted active file transfer permission".into());
        }
        let scope = self.4.get(consent)?;
        if !scope.live() {
            return Err("File transfer permission has expired".into());
        }
        Ok(scope)
    }
    async fn finish_previous_media(&self) -> Result<(), String> {
        let registry = self.1.clone();
        tokio::task::spawn_blocking(move || {
            let mut workers = registry
                .lock()
                .map_err(|_| "Media owner registry is unavailable".to_owned())?;
            let deadline = std::time::Instant::now() + Duration::from_secs(25);
            while workers.iter().any(|worker| !worker.is_finished()) {
                if std::time::Instant::now() >= deadline {
                    return Err("The previous transport has not finished cleanup".into());
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            for worker in workers.drain(..) {
                worker
                    .join()
                    .map_err(|_| "A previous media owner failed".to_owned())?;
            }
            Ok(())
        })
        .await
        .map_err(|_| "Media cleanup owner failed".to_owned())?
    }
    pub(crate) fn spawn_media(
        &self,
        cancel: &AtomicBool,
        run: impl FnOnce() + Send + 'static,
    ) -> Result<(), String> {
        let mut workers = self
            .1
            .lock()
            .map_err(|_| "Media owner registry unavailable")?;
        if cancel.load(Ordering::Acquire) {
            return Err("The connection was cancelled".into());
        }
        let mut index = 0;
        while index < workers.len() {
            if workers[index].is_finished() {
                workers
                    .swap_remove(index)
                    .join()
                    .map_err(|_| "A prior media owner failed")?;
            } else {
                index += 1;
            }
        }
        if workers.len() >= 2 {
            return Err("A prior media owner is still stopping".into());
        }
        workers.push(
            std::thread::Builder::new()
                .name("mixel-remote-peer".into())
                .spawn(run)
                .map_err(|_| "Cannot start the remote media owner")?,
        );
        Ok(())
    }
    #[cfg(target_os = "macos")]
    pub async fn report_error(&self, message: String) {
        let mut inner = self.0.lock().await;
        inner.status.phase = "error".into();
        inner.status.message = message;
    }
    pub async fn status(&self) -> Status {
        let inner = self.0.lock().await;
        let mut status = inner.status.clone();
        if status.phase == "active" && !status.sharing {
            status.recording = status.session.and_then(|session| self.recording_hub().view(session));
        }
        if status.session.is_none() {
            status.previous_recording = self.recording_hub().last_view();
        }
        if status
            .code_expires_at
            .is_some_and(|expiry| std::time::Instant::now() >= expiry)
        {
            status.support_code = None;
        }
        if (inner.cancel.load(Ordering::Acquire)
            || inner
                .consent_cancel
                .as_ref()
                .is_some_and(|flag| flag.load(Ordering::Acquire))
            || inner
                .session_cancel
                .as_ref()
                .is_some_and(|flag| flag.load(Ordering::Acquire)))
            && matches!(status.phase.as_str(), "active" | "negotiating")
        {
            status.phase = "closing".into();
            status.message = "The session stopped; cleaning up the connection".into();
            status.control = false;
            status.clipboard = false;
            status.files = false;
            status.recording_permitted = false;
            status.recording = None;
            if let Some(video) = &inner.video {
                video.clear();
            }
        }
        drop(inner);
        status.input_access = !crate::service_config::controller_only()
            && session_platform::input_access();
        status
    }
    pub async fn connect(&self, auth: AccountAuth, identity: Arc<IdentityState>) -> Result<(), String> {
        self.connect_registration(RegistrationAuth::Interactive(auth), identity)
            .await
    }

    #[cfg(target_os = "macos")]
    pub async fn connect_startup(
        &self,
        enrolled: EnrolledPrincipal,
        display: u32,
        control: bool,
        identity: Arc<IdentityState>,
    ) -> Result<(), String> {
        crate::service_config::require_host()?;
        let source = UnattendedSource {
            source: format!("display:{display}"),
            control,
        };
        self.connect_registration(
            RegistrationAuth::Startup {
                enrolled: Arc::new(enrolled),
                source,
            },
            identity,
        )
        .await
    }

    async fn connect_registration(
        &self,
        auth: RegistrationAuth,
        identity: Arc<IdentityState>,
    ) -> Result<(), String> {
        identity.public_key()?;
        let config = crate::service_config::ServiceConfig::load()?;
        let url: reqwest::Url = config
            .signaling_url
            .parse()
            .map_err(|_| "Invalid connection-service configuration")?;
        let mut inner = self.0.lock().await;
        if inner.commands.is_some() || inner.stopping {
            return Err("This device is already registered or connecting".into());
        }
        inner.generation = inner
            .generation
            .checked_add(1)
            .ok_or("Connection generation exhausted")?;
        let generation = inner.generation;
        let cancel = Arc::new(AtomicBool::new(false));
        let credential = auth.credential(Some(&cancel)).await?;
        let (tx, rx) = mpsc::channel(32);
        let video = Arc::new(VideoMailbox::default());
        inner.cancel = cancel.clone();
        inner.commands = Some(tx.clone());
        inner.video = Some(video.clone());
        inner.status = Status {
            phase: "connecting".into(),
            message: "Verifying enrolled identity and connecting securely".into(),
            ..Status::default()
        };
        let state = self.clone();
        inner.task = Some(tokio::spawn(async move {
            let result = supervise(
                &state,
                generation,
                auth,
                identity,
                url,
                credential,
                cancel.clone(),
                tx,
                rx,
                video,
            )
            .await;
            cancel.store(true, Ordering::Release);
            let mut inner = state.0.lock().await;
            if inner.generation == generation {
                inner.commands = None;
                if let Some(video) = inner.video.take() {
                    video.close();
                }
                inner.input = None;
                inner.clipboard = None;
                if let Some(flag) = inner.session_cancel.take() {
                    flag.store(true, Ordering::Release);
                }
                inner.status = Status {
                    phase: if result.is_ok() { "offline" } else { "error" }.into(),
                    message: result
                        .err()
                        .unwrap_or_else(|| "Disconnected; remote media has stopped".into()),
                    ..Status::default()
                };
            }
        }));
        Ok(())
    }
    pub async fn stop(&self) -> Result<(), String> {
        let mut inner = self.0.lock().await;
        if inner.stopping {
            return Err("Session cleanup is already running".into());
        }
        inner.cancel.store(true, Ordering::Release);
        self.4.revoke();
        inner.generation = inner
            .generation
            .checked_add(1)
            .ok_or("Connection generation exhausted")?;
        inner.stopping = true;
        let generation = inner.generation;
        inner.commands = None;
        if let Some(video) = inner.video.take() {
            video.close();
        }
        inner.input = None;
        inner.clipboard = None;
        if let Some(flag) = inner.session_cancel.take() {
            flag.store(true, Ordering::Release);
        }
        // Revoke media first. The signaling task can then be aborted safely;
        // native resources remain owned by the separately tracked media thread.
        let task = inner.task.take();
        inner.status = Status::default();
        inner.status.message = "Disconnected; sharing and retained video revoked".into();
        drop(inner);
        if let Some(mut task) = task
            && tokio::time::timeout(Duration::from_secs(3), &mut task)
                .await
                .is_err()
        {
            task.abort();
            let _ = task.await;
        }
        let registry = self.1.clone();
        let result = tokio::task::spawn_blocking(move || {
            let mut workers = registry
                .lock()
                .map_err(|_| "Media owner registry unavailable".to_owned())?;
            let deadline = std::time::Instant::now() + Duration::from_secs(25);
            while workers.iter().any(|worker| !worker.is_finished()) {
                if std::time::Instant::now() >= deadline {
                    return Err(
                        "A media owner has not finished cleanup; quitting was postponed".into(),
                    );
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            for worker in workers.drain(..) {
                worker
                    .join()
                    .map_err(|_| "A media owner failed during cleanup".to_owned())?;
            }
            Ok(())
        })
        .await
        .map_err(|_| "Media cleanup worker failed".to_owned())
        .and_then(|value| value);
        let result = result.and(self.4.finish().await);
        let result = result.and(self.recording_hub().finish().await);
        let mut inner = self.0.lock().await;
        if inner.generation == generation {
            inner.stopping = false;
            if let Err(error) = &result {
                inner.status.phase = "error".into();
                inner.status.message = error.clone();
            }
        }
        result
    }
    pub async fn configure_unattended(
        &self,
        source: Option<String>,
        control: bool,
    ) -> Result<(), String> {
        crate::service_config::require_host()?;
        #[cfg(target_os = "linux")]
        if source.as_deref() == Some(crate::wayland_portal::SOURCE) {
            return Err("Wayland requires local confirmation for each sharing session".into());
        }
        let source = source.map(|source| UnattendedSource { source, control });
        if let Some(config) = &source {
            if config.source == "qa-relay-fixture" {
                if !config.capture_allowed() {
                    return Err("The view-only QA test window is unavailable".into());
                }
            } else {
                if !config.capture_allowed() {
                    return Err("The required screen sharing or input permission is unavailable".into());
                }
                let target = config.source.clone();
                let exists = tokio::task::spawn_blocking(move || session_platform::display_size(&target).is_ok())
                    .await
                    .map_err(|_| "Cannot verify the selected display")?;
                if !exists {
                    return Err("Select an available display first".into());
                }
            }
        }

        let mut inner = self.0.lock().await;
        if let Some(config) = source {
            if inner.status.phase != "ready" || inner.status.code_required {
                return Err("Make this computer available and turn off password mode before enabling unattended sharing".into());
            }
            if inner.status.unattended_pending.is_some() || inner.status.unattended.is_some() {
                return Err(
                    "Turn off unattended sharing and wait for confirmation before enabling it"
                        .into(),
                );
            }
            inner
                .commands
                .as_ref()
                .ok_or("Connect this device first")?
                .try_send(Command::ConfigureUnattended { enabled: true })
                .map_err(|_| "The connection service is busy")?;
            inner.status.unattended = Some(config);
            inner.status.unattended_pending = Some(UnattendedChange::Enabling);
            return Ok(());
        }

        if inner.status.unattended.take().is_none() {
            return Ok(());
        }
        inner.status.unattended_enabled = false;
        if inner.status.unattended_session {
            if let Some(cancel) = &inner.consent_cancel {
                cancel.store(true, Ordering::Release);
            }
            if let Some(cancel) = &inner.session_cancel {
                cancel.store(true, Ordering::Release);
            }
            if let Some(video) = &inner.video {
                video.clear();
            }
            inner.status.phase = "closing".into();
        }
        if !inner.commands.as_ref().is_some_and(|commands| {
            commands
                .try_send(Command::ConfigureUnattended { enabled: false })
                .is_ok()
        }) {
            // Closing the registration removes the broker's unattended state
            // even when its local command queue cannot take another message.
            inner.status.unattended_pending = None;
            inner.status.phase = "closing".into();
            inner.cancel.store(true, Ordering::Release);
            return Err("Unattended sharing stopped. The broker queue was busy, so this computer is going offline.".into());
        }
        inner.status.unattended_pending = Some(match inner.status.unattended_pending {
            Some(UnattendedChange::Enabling) => UnattendedChange::DisablingAfterEnable,
            _ => UnattendedChange::Disabling,
        });
        Ok(())
    }

    pub async fn pairing(&self, confirmation: Option<Uuid>) -> Result<(), String> {
        crate::service_config::require_host()?;
        let mut inner = self.0.lock().await;
        if inner.status.phase != "ready" {
            return Err("Make this computer available to check device pairing".into());
        }
        if confirmation.is_some()
            && inner
                .status
                .pairing
                .as_ref()
                .map(|prompt| prompt.request_id)
                != confirmation
        {
            return Err("Check for a current pairing request before confirming".into());
        }
        inner
            .commands
            .as_ref()
            .ok_or("Connect this device first")?
            .try_send(Command::Pairing(confirmation))
            .map_err(|_| "The connection service is busy")?;
        inner.status.pairing = None;
        inner.status.pairing_message = Some("Checking device pairing…".into());
        Ok(())
    }

    pub async fn support_code(&self, required: bool) -> Result<(), String> {
        crate::service_config::require_host()?;
        let mut inner = self.0.lock().await;
        if inner.status.phase != "ready"
            || inner.status.unattended.is_some()
            || inner.status.unattended_pending.is_some()
        {
            return Err("Make this computer available and turn off unattended sharing before changing its one-time password".into());
        }
        inner
            .commands
            .as_ref()
            .ok_or("Connect this device first")?
            .try_send(Command::SupportCode { required })
            .map_err(|_| "The connection service is busy")?;
        inner.status.support_code = None;
        if required {
            inner.status.code_required = true;
        }
        Ok(())
    }

    pub async fn request(
        &self,
        host: u32,
        password: Option<String>,
        unattended: bool,
    ) -> Result<(), String> {
        let code = password
            .map(|value| {
                SupportCode::parse(value).ok_or("Enter the 12-character one-time password")
            })
            .transpose()?;
        if unattended && code.is_some() {
            return Err("Use either a one-time password or unattended access".into());
        }
        if !(100_000_000..=999_999_999).contains(&host) {
            return Err("Enter a nine-digit Mixel connection ID".into());
        }
        let mut inner = self.0.lock().await;
        if inner.status.phase != "ready" || inner.status.host == Some(host) {
            return Err(
                "The device must be Ready and the destination must be another device".into(),
            );
        }
        inner
            .commands
            .as_ref()
            .ok_or("Connect this device first")?
            .try_send(Command::Request(host, code, unattended))
            .map_err(|_| "The connection service is busy")?;
        inner.status.phase = "outgoing".into();
        inner.status.peer = Some(host);
        inner.status.message = "Checking the remote device identity".into();
        Ok(())
    }
    pub async fn decide(&self, decision: Decision) -> Result<(), String> {
        crate::service_config::require_host()?;
        let Decision {
            session,
            accept,
            source,
            confirm,
            control,
            clipboard,
            files,
            chat,
            recording,
        } = decision;
        let mut inner = self.0.lock().await;
        if inner.status.phase != "incoming" || inner.status.session != Some(session) {
            return Err("This invitation is no longer pending".into());
        }
        if accept
            && inner.status.unattended_session
            && (!inner.status.unattended_enabled
                || !inner.status.unattended.as_ref().is_some_and(|config| {
                    source == config.source
                        && control == config.control
                        && !clipboard
                        && !files
                        && !chat
                        && !recording
                }))
        {
            return Err("Unattended sharing permission changed".into());
        }
        let relay_fixture =
            source == "qa-relay-fixture" && session_platform::relay_fixture_enabled();
        if accept && relay_fixture && (control || clipboard || files) {
            return Err(
                "The QA test window does not grant keyboard, clipboard or file access".into(),
            );
        }
        if accept
            && !relay_fixture
            && !(confirm
                && session_platform::is_display(&source))
        {
            return Err("Select and confirm the source to share before Accept".into());
        }
        if accept
            && control
            && (!confirm
                || !session_platform::is_display(&source)
                || !session_platform::input_access())
        {
            return Err("Keyboard and mouse control require a selected display and local input access".into());
        }
        #[cfg(target_os = "linux")]
        let portal = if accept && source == crate::wayland_portal::SOURCE {
            static PICKER: tokio::sync::Semaphore = tokio::sync::Semaphore::const_new(1);
            let _permit = PICKER.try_acquire().map_err(|_| "Finish the current system sharing dialog first")?;
            let consent = inner.consent_cancel.clone().ok_or("The invitation is no longer pending")?;
            drop(inner);
            let grant = crate::wayland_portal::Grant::open(control, consent.clone()).await?;
            inner = self.0.lock().await;
            if inner.status.phase != "incoming" || inner.status.session != Some(session)
                || !inner.consent_cancel.as_ref().is_some_and(|current| Arc::ptr_eq(current, &consent))
                || !grant.live()
            {
                return Err("The invitation expired while the system sharing dialog was open".into());
            }
            Some(grant)
        } else {
            None
        };
        if !accept {
            if let Some(cancel) = &inner.consent_cancel {
                cancel.store(true, Ordering::Release);
            }
            if let Some(cancel) = &inner.session_cancel {
                cancel.store(true, Ordering::Release);
            }
            inner.status.phase = "closing".into();
            inner.status.message = "Rejected locally; notifying the connection service".into();
        }
        inner
            .commands
            .as_ref()
            .ok_or("The device is disconnected")?
            .try_send(Command::Decide {
                session,
                accept,
                source,
                #[cfg(target_os = "linux")]
                portal,
                confirm,
                control,
                clipboard,
                files,
                chat,
                recording,
            })
            .map_err(|_| {
                if accept {
                    "The connection service is busy"
                } else {
                    "Invitation rejected locally; the connection service is busy"
                }
            })?;
        inner.status.phase = if accept { "deciding" } else { "closing" }.into();
        inner.status.message = if accept {
            "Sending acceptance for this invitation"
        } else {
            "Rejecting invitation"
        }
        .into();
        Ok(())
    }
    pub async fn close(&self, session: Uuid) -> Result<(), String> {
        let mut inner = self.0.lock().await;
        Self::close_locked(&mut inner, session)
    }
    pub async fn stop_session(&self, consent: Uuid) -> Result<(), String> {
        let mut inner = self.0.lock().await;
        if inner.status.consent != Some(consent) {
            return Err("This accepted connection has already closed".into());
        }
        let session = inner
            .status
            .session
            .ok_or("This connection has already closed")?;
        Self::close_locked(&mut inner, session)
    }
    fn close_locked(inner: &mut Inner, session: Uuid) -> Result<(), String> {
        if inner.status.session != Some(session) {
            return Err("This session has already closed".into());
        }
        if let Some(cancel) = &inner.session_cancel {
            cancel.store(true, Ordering::Release);
        }
        if let Some(cancel) = &inner.consent_cancel {
            cancel.store(true, Ordering::Release);
        }
        inner.status.phase = "closing".into();
        inner.status.message = "Stopping the session; video delivery revoked".into();
        if let Some(video) = &inner.video {
            video.clear();
        }
        inner.input = None;
        inner.clipboard = None;
        inner.status.control = false;
        inner.status.clipboard = false;
        inner.status.files = false;
        inner
            .commands
            .as_ref()
            .ok_or("Sharing stopped locally; the connection service is disconnected")?
            .try_send(Command::Close(session))
            .map_err(|_| "Sharing stopped locally; the connection service is busy")?;
        Ok(())
    }
    pub async fn restart(&self, session: Uuid) -> Result<(), String> {
        let mut inner = self.0.lock().await;
        if inner.status.session != Some(session)
            || inner.status.phase != "active"
            || inner.status.sharing
            || inner.cancel.load(Ordering::Acquire)
        {
            return Err("Only an active remote viewer can renew this connection".into());
        }
        if inner
            .renew_after
            .is_some_and(|deadline| std::time::Instant::now() < deadline)
        {
            return Err("Wait 30 seconds between connection renewals".into());
        }
        inner
            .commands
            .as_ref()
            .ok_or("The device is disconnected")?
            .try_send(Command::Restart(session))
            .map_err(|_| "The connection service is busy")?;
        inner.status.message = "Renewing this accepted connection".into();
        Ok(())
    }
    pub async fn input(&self, session: Uuid, event: InputEvent) -> Result<bool, String> {
        core_protocol::input::validate(&event.borrowed()).map_err(|error| error.to_string())?;
        let (tx, rx) = tokio::sync::oneshot::channel();
        {
            let inner = self.0.lock().await;
            if inner.status.session != Some(session)
                || inner.status.phase != "active"
                || !inner.status.control
                || inner.status.sharing
                || inner.cancel.load(Ordering::Acquire)
                || inner
                    .session_cancel
                    .as_ref()
                    .is_some_and(|flag| flag.load(Ordering::Acquire))
            {
                return Ok(false);
            }
            inner
                .input
                .as_ref()
                .ok_or("The input transport is closed")?
                .try_send(InputCommand {
                    event,
                    deadline: std::time::Instant::now() + Duration::from_secs(2),
                    reply: tx,
                })
                .map_err(|_| "The input transport is busy")?;
        }
        tokio::time::timeout(Duration::from_secs(3), rx)
            .await
            .map_err(|_| "Input acknowledgment timed out")?
            .map_err(|_| "Input transport closed")?
    }
    pub(crate) async fn grant(
        &self,
        generation: u64,
        session: Uuid,
        control: bool,
        input: Option<mpsc::Sender<InputCommand>>,
        clipboard: bool,
        clipboard_tx: Option<mpsc::Sender<ClipboardCommand>>,
    ) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation
            && inner.status.session == Some(session)
            && !inner.cancel.load(Ordering::Acquire)
            && inner.status.phase != "closing"
        {
            inner.status.control = control;
            inner.input = input;
            inner.status.clipboard = clipboard;
            inner.clipboard = clipboard_tx;
        }
    }
    pub(crate) async fn set_recording_permission(
        &self,
        generation: u64,
        session: Uuid,
        allowed: bool,
    ) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation
            && inner.status.session == Some(session)
            && !inner.cancel.load(Ordering::Acquire)
            && inner.status.phase != "closing"
        {
            inner.status.recording_permitted = allowed;
        }
    }
    pub async fn clipboard(&self, session: Uuid, push: bool) -> Result<(), String> {
        let release = {
            let inner = self.0.lock().await;
            inner.status.session == Some(session) && inner.status.control && !inner.status.sharing
        };
        if release {
            self.input(session, InputEvent::ReleaseAll).await?;
        }
        let (reply, wait) = tokio::sync::oneshot::channel();
        {
            let inner = self.0.lock().await;
            if inner.status.session != Some(session)
                || inner.status.phase != "active"
                || !inner.status.clipboard
                || inner.status.sharing
                || inner.cancel.load(Ordering::Acquire)
                || inner
                    .session_cancel
                    .as_ref()
                    .is_some_and(|flag| flag.load(Ordering::Acquire))
            {
                return Err("The host has not granted active clipboard access".into());
            }
            inner
                .clipboard
                .as_ref()
                .ok_or("Clipboard transport is closed")?
                .try_send(ClipboardCommand {
                    push,
                    deadline: std::time::Instant::now() + Duration::from_secs(20),
                    reply,
                })
                .map_err(|_| "A clipboard transfer is already pending")?;
        }
        tokio::time::timeout(Duration::from_secs(22), wait)
            .await
            .map_err(|_| "Clipboard transfer acknowledgment timed out")?
            .map_err(|_| "The clipboard transport closed")?
    }
    pub async fn next_video(&self, session: Uuid) -> Result<Vec<u8>, String> {
        let inner = self.0.lock().await;
        if inner.cancel.load(Ordering::Acquire)
            || inner
                .session_cancel
                .as_ref()
                .is_some_and(|flag| flag.load(Ordering::Acquire))
        {
            if let Some(video) = &inner.video {
                video.clear();
            }
            return Ok(Vec::new());
        }
        if inner.status.session != Some(session) || inner.status.phase != "active" {
            return Ok(Vec::new());
        }
        inner.video.as_ref().ok_or("Video is closed")?.take(session)
    }

    async fn change(&self, generation: u64, update: impl FnOnce(&mut Status)) {
        let mut inner = self.0.lock().await;
        if inner.generation != generation || inner.cancel.load(Ordering::Acquire) {
            return;
        }
        let previous = inner.status.session;
        update(&mut inner.status);
        if previous != inner.status.session {
            inner.input = None;
            inner.clipboard = None;
            inner.status.control = false;
            inner.status.clipboard = false;
            inner.status.files = false;
            if let Some(cancel) = inner.session_cancel.take() {
                cancel.store(true, Ordering::Release);
            }
            if let Some(video) = &inner.video {
                video.clear();
            }
            if inner.status.session.is_none() {
                inner.status.unattended_session = false;
                if let Some(cancel) = inner.consent_cancel.take() {
                    cancel.store(true, Ordering::Release);
                }
                inner.file_permission = None;
                inner.status.consent = None;
                inner.status.sharing = false;
            }
        }
    }
    async fn track(
        &self,
        generation: u64,
        session: Uuid,
        cancel: Arc<AtomicBool>,
        consent: Uuid,
        consent_cancel: Arc<AtomicBool>,
    ) -> Result<(), String> {
        let mut inner = self.0.lock().await;
        if inner.generation != generation || inner.cancel.load(Ordering::Acquire) {
            cancel.store(true, Ordering::Release);
            return Err("The connection was cancelled".into());
        }
        if let Some(previous) = inner.session_cancel.replace(cancel) {
            previous.store(true, Ordering::Release);
        }
        if inner.status.consent != Some(consent) {
            inner.file_permission = None;
            if let Some(previous) = inner.consent_cancel.replace(consent_cancel) {
                previous.store(true, Ordering::Release);
            }
        }
        inner.status.session = Some(session);
        inner.status.consent = Some(consent);
        inner.renew_after = Some(std::time::Instant::now() + Duration::from_secs(31));
        inner.input = None;
        inner.clipboard = None;
        inner.status.control = false;
        inner.status.clipboard = false;
        inner.status.files = false;
        inner.status.received = 0;
        if let Some(video) = &inner.video {
            video.activate(session)?;
        }
        Ok(())
    }
    pub(crate) async fn update_peer(
        &self,
        generation: u64,
        session: Uuid,
        message: &str,
        received: u64,
    ) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation
            && inner.status.session == Some(session)
            && !inner.cancel.load(Ordering::Acquire)
            && inner.status.phase != "closing"
        {
            inner.status.phase = "active".into();
            inner.status.message = message.into();
            inner.status.received = received;
        }
    }
}
struct Peer {
    id: Uuid,
    remote: u32,
    host: bool,
    unattended: bool,
    key: ed25519_dalek::VerifyingKey,
    source: String,
    #[cfg(target_os = "linux")]
    portal: Option<Arc<crate::wayland_portal::Grant>>,
    confirm: bool,
    control: bool,
    clipboard: bool,
    files: bool,
    chat: bool,
    recording: bool,
    close_sent: bool,
    consent_cancel: Arc<AtomicBool>,
    cancel: Arc<AtomicBool>,
    signals: Option<mpsc::Sender<Signal>>,
    nonce: Option<[u8; 32]>,
    identity: Option<core_protocol::delegation::DeviceTicket>,
    expires: Arc<AtomicU64>,
    relay_expires: u64,
    restart_pending: Option<std::time::Instant>,
    rotated_at: std::time::Instant,
    started_at: std::time::Instant,
    local_consent: Option<bool>,
    accepted_at: Option<std::time::Instant>,
    consent_id: Uuid,
}
fn request_failure_message(code: ErrorCode) -> &'static str {
    match code {
        ErrorCode::Forbidden => {
            "Connection request denied. Check the device’s company access and one-time password, then try again."
        }
        ErrorCode::Unavailable => {
            "The remote device is not available to you. Ask the other person to make their computer available. If it is online, your account may not be assigned to that device’s company: check Manage devices and company."
        }
        ErrorCode::Busy => {
            "The remote device is busy. Wait for its current request or session to finish, then try again."
        }
        ErrorCode::Invalid => {
            "The connection request was invalid. Check the ID and one-time password, then try again."
        }
    }
}
fn same_identity(
    left: &core_protocol::delegation::DeviceTicket,
    right: &core_protocol::delegation::DeviceTicket,
) -> bool {
    left.identity.device == right.identity.device
        && left.version == right.version
        && left.identity.profile == right.identity.profile
        && left.identity.tenant == right.identity.tenant
        && left.identity.connection_id == right.identity.connection_id
        && left.identity.generation == right.identity.generation
        && left.public_key == right.public_key
        && left.staff == right.staff
}
fn peer_identity(
    ticket: &core_protocol::delegation::DeviceTicket,
    connection: u32,
    own: &signaling_server::auth::Principal,
    host: bool,
    roots: &[ed25519_dalek::VerifyingKey],
) -> Result<ed25519_dalek::VerifyingKey, String> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "System clock is unavailable")?
        .as_secs();
    let verified = ticket
        .verify(roots, now)
        .map_err(|_| "The remote identity authority proof is invalid or expired")?;
    if ticket.identity.connection_id != connection {
        return Err("The verified remote identity does not match this connection ID".into());
    }
    if host && ticket.unattended_host_only() {
        return Err("An unattended host cannot request a connection".into());
    }
    let same_tenant =
        own.tenant.is_some() && own.tenant.map(|id| *id.as_bytes()) == ticket.identity.tenant;
    if (host && !ticket.staff && !same_tenant) || (!host && !own.staff && !same_tenant) {
        return Err("This peer is not authorized to request this connection".into());
    }
    Ok(verified.key())
}
impl Drop for Peer {
    fn drop(&mut self) {
        self.cancel.store(true, Ordering::Release);
        self.consent_cancel.store(true, Ordering::Release);
    }
}
impl Peer {
    fn take_close(&mut self) -> bool {
        self.cancel.store(true, Ordering::Release);
        !std::mem::replace(&mut self.close_sent, true)
    }

    fn can_negotiate(&self, now: std::time::Instant) -> bool {
        !self.cancel.load(Ordering::Acquire)
            && (!self.host || (self.local_consent == Some(true) && self.accepted_at.is_some()))
            && self.accepted_at.is_none_or(|accepted| {
                now.saturating_duration_since(accepted)
                    < core_protocol::session::NEGOTIATION_LIFETIME
            })
    }
    async fn track(&self, state: &RemoteSession, generation: u64) -> Result<(), String> {
        state
            .track(
                generation,
                self.id,
                self.cancel.clone(),
                self.consent_id,
                self.consent_cancel.clone(),
            )
            .await
    }
}
type Socket =
    tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;
struct LeaseTask(JoinHandle<()>);
impl Drop for LeaseTask {
    fn drop(&mut self) {
        self.0.abort();
    }
}
pub(crate) type BrokerLease = Arc<std::sync::Mutex<(u64, std::time::Instant)>>;
fn watch_lease(
    state: RemoteSession,
    generation: u64,
    cancel: Arc<AtomicBool>,
    lease: BrokerLease,
) -> LeaseTask {
    LeaseTask(tokio::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_millis(100)).await;
            if cancel.load(Ordering::Acquire) {
                return;
            }
            let live = lease.lock().ok().is_some_and(|value| {
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .is_ok_and(|now| now.as_secs() < value.0 && std::time::Instant::now() < value.1)
            });
            if !live {
                cancel.store(true, Ordering::Release);
                let mut inner = state.0.lock().await;
                if inner.generation == generation {
                    if let Some(flag) = &inner.session_cancel {
                        flag.store(true, Ordering::Release);
                    }
                    inner.input = None;
                    inner.clipboard = None;
                    if let Some(video) = inner.video.take() {
                        video.close();
                    }
                    inner.status.control = false;
                    inner.status.clipboard = false;
                    inner.status.files = false;
                    inner.status.phase = "error".into();
                    inner.status.message =
                        "Connection authorization expired; sharing and input have stopped".into();
                }
                return;
            }
        }
    }))
}
async fn send(socket: &mut Socket, message: ClientMessage) -> Result<(), Failure> {
    let text = serde_json::to_string(&message).map_err(|_| "Cannot encode connection message")?;
    tokio::time::timeout(
        Duration::from_secs(5),
        socket.send(Message::Text(text.into())),
    )
    .await
    .map_err(|_| Failure::network("Connection service write timed out"))?
    .map_err(crate::remote_recovery::socket)
}
async fn event(socket: &mut Socket) -> Result<Option<ServerMessage>, Failure> {
    match socket.next().await {
        Some(Ok(Message::Text(text))) if text.len() <= MAX_MESSAGE => serde_json::from_str(&text)
            .map(Some)
            .map_err(|_| "Invalid connection-service response".into()),
        Some(Ok(Message::Ping(value))) => {
            tokio::time::timeout(Duration::from_secs(5), socket.send(Message::Pong(value)))
                .await
                .map_err(|_| Failure::network("Connection-service heartbeat reply timed out"))?
                .map_err(crate::remote_recovery::socket)?;
            Ok(None)
        }
        Some(Ok(Message::Pong(_))) => Ok(None),
        Some(Ok(Message::Close(frame))) => {
            Err(crate::remote_recovery::closed(frame.map(|frame| frame.code)))
        }
        Some(Err(error)) => Err(crate::remote_recovery::socket(error)),
        None => Err(Failure::network("The connection service disconnected")),
        _ => Err("The connection service sent an invalid message".into()),
    }
}
struct AttemptGuard(Arc<AtomicBool>);
impl Drop for AttemptGuard {
    fn drop(&mut self) {
        self.0.store(true, Ordering::Release);
    }
}
async fn cancelled(cancel: &AtomicBool) {
    while !cancel.load(Ordering::Acquire) {
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}
#[allow(clippy::too_many_arguments)]
async fn supervise(
    state: &RemoteSession,
    generation: u64,
    auth: RegistrationAuth,
    identity: Arc<IdentityState>,
    url: reqwest::Url,
    initial: Option<Credential>,
    cancel: Arc<AtomicBool>,
    tx: mpsc::Sender<Command>,
    mut commands: mpsc::Receiver<Command>,
    video: Arc<VideoMailbox>,
) -> Result<(), String> {
    let expected = auth.principal(&initial)?.clone();
    drop(initial);
    let mut backoff = Backoff::default();
    loop {
        if cancel.load(Ordering::Acquire) {
            return Ok(());
        }
        let attempt = Arc::new(AtomicBool::new(false));
        let guard = AttemptGuard(attempt.clone());
        let credential = auth.credential(Some(&attempt)).await?;
        let principal = auth.principal(&credential)?;
        if principal.user != expected.user
            || principal.tenant != expected.tenant
            || principal.staff != expected.staff
        {
            return Err("Account authorization changed; register again".into());
        }
        let result = tokio::select! {
            _=cancelled(&cancel)=>Ok(()),
            result=run(state,generation,auth.clone(),identity.clone(),url.clone(),credential,attempt,tx.clone(),&mut commands,video.clone())=>result,
        };
        drop(guard);
        let error = match result {
            Ok(()) => return Ok(()),
            Err(error) => error,
        };
        if cancel.load(Ordering::Acquire) {
            return Ok(());
        }
        if !error.retryable {
            return Err(error.message);
        }
        if state.0.lock().await.status.code_required {
            return Err(
                "Connection lost. Register this computer again to issue a new one-time password".into(),
            );
        }
        let delay=backoff.next(error.registered_for).ok_or_else(||format!("{}; automatic reconnect attempts exhausted. Register this computer again when the network is available.",error.message))?;
        state
            .change(generation, |status| {
                status.phase = "connecting".into();
                status.session = None;
                status.peer = None;
                status.requester = None;
                status.host = None;
                status.unattended = None;
                status.unattended_enabled = false;
                status.unattended_pending = None;
                status.pairing = None;
                status.pairing_message = None;
                status.message = format!(
                    "Connection lost; sharing stopped. Reconnecting registration ({}/6)…",
                    backoff.attempt()
                );
            })
            .await;
        // Never carry an invitation, plaintext frame, input queue or file grant
        // into the new broker registration. A fresh session needs fresh Accept.
        state.finish_previous_media().await?;
        state.4.finish().await?;
        while commands.try_recv().is_ok() {}
        tokio::select! {_ = cancelled(&cancel)=>return Ok(()),_ = tokio::time::sleep(delay)=>{}}
    }
}
#[allow(clippy::too_many_arguments)]
async fn run(
    state: &RemoteSession,
    generation: u64,
    auth: RegistrationAuth,
    identity: Arc<IdentityState>,
    url: reqwest::Url,
    initial: Option<Credential>,
    cancel: Arc<AtomicBool>,
    tx: mpsc::Sender<Command>,
    commands: &mut mpsc::Receiver<Command>,
    video: Arc<VideoMailbox>,
) -> Result<(), Failure> {
    let enrolled = auth.enrollment(&initial, &identity).await?;
    let device = enrolled.device;
    let principal = auth.principal(&initial)?.clone();
    if enrolled.public_key != identity.public_key()? {
        return Err("The saved device key does not match its enrollment".into());
    }
    let service = crate::service_config::ServiceConfig::load()?;
    let roots = service.roots()?;
    let mut own_ticket = registration_ticket(&initial, &enrolled, &identity).await?;
    if cancel.load(Ordering::Acquire) {
        return Err("The device registration was cancelled".into());
    }
    let config = WebSocketConfig::default()
        .max_message_size(Some(MAX_MESSAGE))
        .max_frame_size(Some(MAX_MESSAGE));
    let (mut socket, _) = tokio::time::timeout(
        Duration::from_secs(12),
        connect_async_with_config(url.as_str(), Some(config), false),
    )
    .await
    .map_err(|_| Failure::network("Secure connection timed out"))?
    .map_err(crate::remote_recovery::socket)?;
    let broker_lease = Arc::new(std::sync::Mutex::new((
        own_ticket.expires_at,
        std::time::Instant::now() + Duration::from_secs(899),
    )));
    let _lease_task = watch_lease(
        state.clone(),
        generation,
        cancel.clone(),
        broker_lease.clone(),
    );
    let mut ready_at = None;
    let result:Result<(),Failure>=async {
        send(&mut socket,ClientMessage::AuthenticateDevice{token:signaling_server::delegated_auth::encode(&own_ticket).map_err(|_|"Cannot encode delegated device authorization")?,device}).await?;
        let challenge=tokio::time::timeout(Duration::from_secs(20),event(&mut socket)).await.map_err(|_|"Device authorization timed out")??;
        let Some(ServerMessage::DeviceChallenge{challenge})=challenge else{return Err("Device challenge was not received".into());};
        if challenge.device!=*device.as_bytes() || challenge.profile!=*principal.user.as_bytes() || challenge.tenant!=principal.tenant.map(|id|*id.as_bytes()) || challenge.connection_id!=enrolled.connection_id || challenge.generation!=enrolled.generation || cancel.load(Ordering::Acquire) {return Err("The device authorization challenge does not match this account".into());}
        send(&mut socket,ClientMessage::ProveDevice{signature:identity.prove(challenge)?}).await?;
        let ready=tokio::time::timeout(Duration::from_secs(6),event(&mut socket)).await.map_err(|_|"Device registration timed out")??;
        if !matches!(ready,Some(ServerMessage::Ready{host,protocol:core_protocol::PROTOCOL_VERSION}) if host==enrolled.connection_id) {return Err("The connection service did not verify this device ID".into());}
        ready_at=Some(std::time::Instant::now());
        match &auth {
            #[cfg(target_os = "macos")]
            RegistrationAuth::Startup { source, .. } => {
                if !source.capture_allowed() {
                    return Err("Startup host requires the configured macOS sharing permissions".into());
                }
                state.change(generation, |status| {
                    status.phase = "ready".into();
                    status.message = "Verifying unattended startup sharing".into();
                    status.host = Some(enrolled.connection_id);
                    status.unattended = Some(source.clone());
                    status.unattended_pending = Some(UnattendedChange::Enabling);
                }).await;
                send(&mut socket, ClientMessage::ConfigureUnattended { enabled: true }).await?;
            }
            _ => {
                state.change(generation, |status| {
                    status.phase = "ready".into();
                    status.message = "Ready for an attended connection".into();
                    status.host = Some(enrolled.connection_id);
                }).await;
            }
        }
        let mut credential=initial;
        let mut authorized_until=own_ticket.expires_at;
        let mut pending_renewal:Option<std::time::Instant>=None;
        let mut last_renewed=std::time::Instant::now();
        let mut last_delegation_attempt=std::time::Instant::now();
        let mut peer:Option<Peer>=None;
        let mut outgoing:Option<(u32,ed25519_dalek::VerifyingKey,bool)>=None;
        let mut tick=tokio::time::interval(Duration::from_millis(250));
        let mut last_activity=std::time::Instant::now();
        let mut heartbeat=std::time::Instant::now();
        loop {
            tokio::select! {
                _=tick.tick()=>{
                    if cancel.load(Ordering::Acquire){return Err("Registration authorization was revoked or expired".into());}
                    let now=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_|"System clock is unavailable")?.as_secs();
                    if now>=authorized_until || pending_renewal.is_some_and(|deadline|std::time::Instant::now()>=deadline) {return Err("Device authorization expired; reconnect after signing in".into());}
                    if last_activity.elapsed()>Duration::from_secs(65){return Err(Failure::network("Connection-service heartbeat expired"));}
                    if let Some(active)=peer.as_mut(){
                        if !active.close_sent && active.cancel.load(Ordering::Acquire) && active.signals.is_none(){
                            let session=active.id;let reject=active.host && active.local_consent.is_none();peer=None;
                            state.change(generation,|status|{status.phase="ready".into();status.session=None;status.peer=None;status.requester=None;status.message="Invitation stopped locally; ready for a new connection".into();}).await;
                            send(&mut socket,if reject{ClientMessage::Decide{session,accept:false}}else{ClientMessage::Close{session}}).await?;continue;
                        }
                        let invitation_expired=!active.close_sent && active.signals.is_none() && ((active.local_consent.is_none() && active.accepted_at.is_none() && active.started_at.elapsed()>=core_protocol::session::CONSENT_LIFETIME) || active.accepted_at.is_some_and(|accepted|accepted.elapsed()>=core_protocol::session::NEGOTIATION_LIFETIME));
                        if invitation_expired{
                            let session=active.id;active.cancel.store(true,Ordering::Release);send(&mut socket,ClientMessage::Close{session}).await?;peer=None;
                            state.change(generation,|status|{status.phase="ready".into();status.session=None;status.peer=None;status.requester=None;status.message="The local invitation or acceptance deadline expired; request a fresh connection".into();}).await;
                            continue;
                        }
                        if active.started_at.elapsed()>=Duration::from_secs(8*3600) || active.restart_pending.is_some_and(|start|start.elapsed()>=Duration::from_secs(20)){return Err("The accepted connection lifetime or renewal deadline expired".into());}
                        if !active.host && active.signals.is_some() && !active.cancel.load(Ordering::Acquire) && active.restart_pending.is_none() && active.rotated_at.elapsed()>=Duration::from_secs(31) && (active.relay_expires.saturating_sub(now)<=90 || active.rotated_at.elapsed()>=Duration::from_secs(720)) && state.status().await.phase=="active"{
                            send(&mut socket,ClientMessage::Restart{session:active.id}).await?;active.restart_pending=Some(std::time::Instant::now());
                            state.change(generation,|status|status.message="Renewing relay credentials and transport keys for the accepted peer".into()).await;
                        }
                    }
                    if heartbeat.elapsed()>Duration::from_secs(20){tokio::time::timeout(Duration::from_secs(5),socket.send(Message::Ping(Vec::new().into()))).await.map_err(|_|Failure::network("Heartbeat write timed out"))?.map_err(crate::remote_recovery::socket)?;heartbeat=std::time::Instant::now();}
                    let fresh=auth.credential(None).await?;
                    let fresh_principal = auth.principal(&fresh)?;
                    if fresh_principal.user!=principal.user || fresh_principal.tenant!=principal.tenant || fresh_principal.staff!=principal.staff {return Err("Account authorization changed".into());}
                    let token_changed = fresh.as_ref().zip(credential.as_ref()).is_some_and(|(new, old)| new.token.as_str()!=old.token.as_str());
                    if pending_renewal.is_none() && last_delegation_attempt.elapsed()>=Duration::from_secs(31) && (token_changed || last_renewed.elapsed()>=Duration::from_secs(720) || authorized_until.saturating_sub(now)<=120) {
                        last_delegation_attempt=std::time::Instant::now();
                        let renewed=registration_ticket(&fresh,&enrolled,&identity).await?;
                        if !same_identity(&own_ticket,&renewed){return Err("Device identity changed during renewal".into());}
                        if cancel.load(Ordering::Acquire){break;}
                        // A ticket capped by an expiring ISM token cannot gain
                        // lifetime until native account refresh succeeds.
                        if renewed.expires_at<=authorized_until{credential=fresh;continue;}
                        send(&mut socket,ClientMessage::Renew{token:signaling_server::delegated_auth::encode(&renewed).map_err(|_|"Cannot encode delegated device renewal")?}).await?;
                        own_ticket=renewed;pending_renewal=Some(std::time::Instant::now()+Duration::from_secs(20));credential=fresh;
                    }
                },
                Some(command)=commands.recv()=>match command {
                    Command::Request(host,code,unattended)=>{
                        if peer.is_some() || outgoing.is_some(){continue;}
                        let Some(credential) = credential.as_ref() else { return Err("A startup host cannot request sessions".into()); };
                        let key=match credential.ism.peer_key(&credential.token,host).await{Ok(key)=>key,Err(_)=>{state.change(generation,|status|{status.phase="ready".into();status.peer=None;status.message="This account could not verify that device ID. Check that it is enrolled in Mixel Remote and that your company has access.".into();}).await;continue;}};
                        if cancel.load(Ordering::Acquire){break;}
                        let request=match code {
                            Some(code)=>ClientMessage::RequestWithCode{host,code},
                            None if unattended=>ClientMessage::RequestUnattended{host},
                            None=>ClientMessage::Request{host},
                        };
                        send(&mut socket,request).await?;outgoing=Some((host,key,unattended));
                        state.change(generation,|status|{status.phase="outgoing".into();status.message="Waiting for the remote host to Accept".into();status.peer=Some(host);status.sharing=false;}).await;
                    },
                    Command::ConfigureUnattended{enabled}=>{
                        send(&mut socket,ClientMessage::ConfigureUnattended{enabled}).await?;
                    },
                    Command::Pairing(confirmation)=>{
                        let message=match confirmation {
                            Some(request)=>ClientMessage::ConfirmPairing{request},
                            None=>ClientMessage::CheckPairing,
                        };
                        send(&mut socket,message).await?;
                    },
                    Command::SupportCode{required}=>{
                        let message=if required{ClientMessage::GenerateSupportCode}else{ClientMessage::ClearSupportCode};
                        send(&mut socket,message).await?;
                    },
                    Command::Decide{session,accept,source,#[cfg(target_os = "linux")] portal,confirm,control,clipboard,files,chat,recording}=>{
                        let Some(active)=peer.as_mut().filter(|peer|peer.id==session && peer.host && peer.signals.is_none()) else{continue;};
                        if (accept && active.cancel.load(Ordering::Acquire)) || active.local_consent.is_some() || active.started_at.elapsed()>=core_protocol::session::CONSENT_LIFETIME{return Err("This local invitation is no longer eligible for consent".into());}
                        active.local_consent=Some(accept);
                        #[cfg(target_os = "linux")]
                        { active.portal=portal; }
                        if accept{active.accepted_at=Some(std::time::Instant::now());active.source=source;active.confirm=confirm;active.control=control;active.clipboard=clipboard;active.files=files;active.chat=chat;active.recording=recording;}
                        else{active.cancel.store(true,Ordering::Release);active.source.clear();active.confirm=false;active.control=false;active.clipboard=false;active.files=false;active.chat=false;active.recording=false;active.consent_cancel.store(true,Ordering::Release);}
                        send(&mut socket,ClientMessage::Decide{session,accept}).await?;
                        if !accept{
                            peer=None;
                            state.change(generation,|status|{status.phase="ready".into();status.session=None;status.peer=None;status.requester=None;status.message="Invitation rejected locally; ready for a new connection".into();}).await;
                            continue;
                        }
                        state.change(generation,|status|{status.phase=if accept{"negotiating"}else{"closing"}.into();status.message=if accept{"Accepted; authenticating the remote transport"}else{"Rejecting invitation"}.into();}).await;
                    },
                    Command::Close(session)=>{
                        if let Some(active)=peer.as_mut().filter(|peer|peer.id==session) && active.take_close(){
                            send(&mut socket,ClientMessage::Close{session}).await?;
                        }
                    },
                    Command::Restart(session)=>{
                        if let Some(active)=peer.as_mut().filter(|peer|peer.id==session && !peer.host && peer.signals.is_some() && !peer.cancel.load(Ordering::Acquire) && peer.restart_pending.is_none() && peer.rotated_at.elapsed()>=Duration::from_secs(31)){
                            send(&mut socket,ClientMessage::Restart{session}).await?;active.restart_pending=Some(std::time::Instant::now());
                        }
                    },
                    Command::Signal{session,signal}=>{if peer.as_ref().is_some_and(|peer|peer.id==session && !peer.cancel.load(Ordering::Acquire)){send(&mut socket,ClientMessage::Signal{session,payload:signal}).await?;}},
                    Command::Finished{session,result}=>{if peer.as_ref().is_some_and(|peer|peer.id==session && !peer.close_sent){peer=None;send(&mut socket,ClientMessage::Close{session}).await?;state.change(generation,|status|{status.phase="ready".into();status.session=None;status.peer=None;status.requester=None;status.message=result.err().unwrap_or_else(||"Session closed; ready for a new connection".into());}).await;}},
                },
                incoming=event(&mut socket)=>{
                    let message=incoming?;last_activity=std::time::Instant::now();
                    let Some(message)=message else{continue;};
                    let message=if let ServerMessage::Restarted{previous,session,nonce,controller_identity,host_identity}=message{
                        let Some(active)=peer.as_mut().filter(|peer|peer.id==previous && peer.signals.is_some() && !peer.cancel.load(Ordering::Acquire)) else{send(&mut socket,ClientMessage::Close{session}).await?;continue;};
                        let status=state.status().await;
                        if status.phase!="active" || active.started_at.elapsed()>=Duration::from_secs(8*3600){send(&mut socket,ClientMessage::Close{session}).await?;continue;}
                        // A transport replacement is confined to the already
                        // accepted device/account/source and existing grants.
                        // Common Accepted validation below pins fresh tickets.
                        active.cancel.store(true,Ordering::Release);
                        active.id=session;active.close_sent=false;active.cancel=Arc::new(AtomicBool::new(false));active.signals=None;active.nonce=None;active.relay_expires=0;active.restart_pending=None;active.rotated_at=std::time::Instant::now();active.accepted_at=Some(std::time::Instant::now());
                        active.track(state,generation).await?;
                        state.change(generation,|status|{status.phase="negotiating".into();status.message="Replacing transport for the same accepted peer; old sharing has stopped".into();}).await;
                        ServerMessage::Accepted{session,nonce,controller_identity:Some(controller_identity),host_identity:Some(host_identity)}
                    }else{message};
                    match message {
                        ServerMessage::Renewed{expires_at}=>{if pending_renewal.take().is_none() || expires_at!=own_ticket.expires_at{return Err("Unexpected device authorization renewal".into());}authorized_until=expires_at;last_renewed=std::time::Instant::now();*broker_lease.lock().map_err(|_|"Connection lease state failed")?=(expires_at,std::time::Instant::now()+Duration::from_secs(899));},
                        ServerMessage::UnattendedStatus{enabled}=>{
                            state.change(generation, |status| {
                                status.acknowledge_unattended(enabled);
                                if own_ticket.unattended_host_only() && enabled {
                                    status.message = "Ready for policy-approved unattended access".into();
                                }
                            }).await;
                            if own_ticket.unattended_host_only() && !enabled {
                                return Err("The broker refused unattended startup sharing".into());
                            }
                        },
                        ServerMessage::PairingStatus{pending,confirmed,failed}=>{
                            state.change(generation,|status|{
                                status.pairing_message=if failed {Some("Device pairing is unavailable. Ask the company owner to resend the request.".into())}
                                    else if confirmed {Some("This computer is paired. Its connection ID stays the same.".into())}
                                    else if pending.is_none() {Some("No current pairing request.".into())} else {None};
                                status.pairing=pending;
                            }).await;
                        },
                        ServerMessage::SupportCode{required,code,lifetime_seconds}=>{
                            state.change(generation,|status|{
                                status.code_required=required;
                                status.code_expires_at=code.as_ref().map(|_|std::time::Instant::now()+Duration::from_secs(u64::from(lifetime_seconds)));
                                status.support_code=code;
                            }).await;
                        },
                        ServerMessage::Requested{session}=>{let (remote,key,unattended)=outgoing.take().ok_or("Unexpected outgoing invitation")?;peer=Some(Peer{id:session,remote,host:false,unattended,key,source:String::new(),#[cfg(target_os = "linux")] portal:None,confirm:false,control:false,clipboard:false,files:false,chat:false,recording:false,close_sent:false,consent_cancel:Arc::new(AtomicBool::new(false)),cancel:Arc::new(AtomicBool::new(false)),signals:None,nonce:None,identity:None,expires:Arc::new(AtomicU64::new(0)),relay_expires:0,restart_pending:None,rotated_at:std::time::Instant::now(),started_at:std::time::Instant::now(),local_consent:None,accepted_at:None,consent_id:Uuid::new_v4()});peer.as_ref().ok_or("Invitation state unavailable")?.track(state,generation).await?;},
                        ServerMessage::Consent{session,requester_connection,requester,requester_identity,unattended,..}=>{
                            if crate::service_config::controller_only() || peer.is_some() || outgoing.is_some(){send(&mut socket,ClientMessage::Decide{session,accept:false}).await?;continue;}
                            let ticket=requester_identity.ok_or("The invitation has no signed requester identity")?;
                            let key=peer_identity(&ticket,requester_connection,&principal,true,&roots)?;
                            if own_ticket.unattended_host_only() && !unattended {
                                send(&mut socket, ClientMessage::Decide { session, accept: false }).await?;
                                continue;
                            }
                            if ticket.identity.profile!=*requester.as_bytes(){return Err("The requester account does not match its signed identity".into());}
                            let auto=if unattended {
                                let status=state.status().await;
                                let Some(config)=status.unattended.filter(|config|status.unattended_enabled
                                    && config.capture_allowed()
                                    && !status.code_required)
                                else { send(&mut socket,ClientMessage::Decide{session,accept:false}).await?;continue; };
                                Some(config)
                            }else{None};
                            let expires=Arc::new(AtomicU64::new(ticket.expires_at));
                            peer=Some(Peer{id:session,remote:requester_connection,host:true,unattended,key,source:String::new(),#[cfg(target_os = "linux")] portal:None,confirm:false,control:false,clipboard:false,files:false,chat:false,recording:false,close_sent:false,consent_cancel:Arc::new(AtomicBool::new(false)),cancel:Arc::new(AtomicBool::new(false)),signals:None,nonce:None,identity:Some(ticket),expires,relay_expires:0,restart_pending:None,rotated_at:std::time::Instant::now(),started_at:std::time::Instant::now(),local_consent:None,accepted_at:None,consent_id:Uuid::new_v4()});
                            peer.as_ref().ok_or("Invitation state unavailable")?.track(state,generation).await?;
                            state.change(generation,|status|{status.phase="incoming".into();status.message="Incoming request: choose a source and Accept or Reject".into();status.peer=Some(requester_connection);status.requester=Some(requester);status.sharing=true;status.unattended_session=unattended;}).await;
                            if let Some(config)=auto {
                                let decision=Decision{session,accept:true,source:config.source,confirm:true,
                                    control:config.control,clipboard:false,files:false,chat:false,recording:false};
                                if state.decide(decision).await.is_err() {
                                    send(&mut socket,ClientMessage::Decide{session,accept:false}).await?;
                                }
                            }
                        },
                        ServerMessage::Accepted{session,nonce,controller_identity,host_identity}=>{
                            let active=peer.as_mut().filter(|peer|peer.id==session && peer.signals.is_none()).ok_or("Unexpected accepted invitation")?;
                            if active.cancel.load(Ordering::Acquire){if active.take_close(){send(&mut socket,ClientMessage::Close{session}).await?;}continue;}
                            if !active.can_negotiate(std::time::Instant::now()){return Err("A broker response cannot grant or extend local sharing consent".into());}
                            active.accepted_at.get_or_insert_with(std::time::Instant::now);
                            if nonce==[0;32] || (active.host && active.source.is_empty()){return Err("Missing local sharing consent".into());}
                            let controller=controller_identity.ok_or("The accepted session has no signed controller identity")?;
                            let host=host_identity.ok_or("The accepted session has no signed host identity")?;
                            if host.unattended_host_only() && !active.unattended {
                                return Err("The verified host accepts only unattended connections".into());
                            }
                            let (local,remote)=if active.host{(&host,&controller)}else{(&controller,&host)};
                            let now=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_|"System clock is unavailable")?.as_secs();
                            local.verify(&roots,now).map_err(|_|"The local session identity is invalid or expired")?;
                            if !same_identity(local,&own_ticket){return Err("The accepted session does not belong to this account and device".into());}
                            let checked=peer_identity(remote,active.remote,&principal,active.host,&roots)?;
                            if checked!=active.key || active.identity.as_ref().is_some_and(|known|!same_identity(known,remote)){return Err("Peer identity changed; request a fresh connection".into());}
                            active.expires.store(remote.expires_at,Ordering::Release);
                            active.identity=Some(remote.clone());
                            if active.cancel.load(Ordering::Acquire) || cancel.load(Ordering::Acquire){if active.take_close(){send(&mut socket,ClientMessage::Close{session}).await?;}continue;}
                            auth.credential(Some(&active.cancel)).await?;
                            let binding=core_protocol::delegation::session_binding(*session.as_bytes(),nonce,&controller,&host);
                            if active.nonce.replace(binding).is_some(){return Err("Duplicate accepted invitation".into());}
                            state.change(generation,|status|{status.phase="negotiating".into();status.message="Accepted; obtaining authenticated short-lived relay configuration".into();}).await;
                            send(&mut socket,ClientMessage::Ice{session}).await?;
                        },
                        ServerMessage::Ice{session,lease}=>{
                            let active=peer.as_mut().filter(|peer|peer.id==session && peer.signals.is_none()).ok_or("Unexpected relay configuration")?;
                            let nonce=active.nonce.take().ok_or("Missing accepted session binding")?;
                            let now=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_|"System clock is unavailable")?.as_secs();
                            if lease.expires_at<=now || lease.expires_at>now.saturating_add(3600) || lease.expires_at>authorized_until || lease.servers.len()>8{return Err("Relay configuration has invalid bounds or expiry".into());}
                            active.relay_expires=lease.expires_at;
                            let mut ice_servers=Vec::new();
                            for server in lease.servers{
                                if server.urls.is_empty() || server.urls.len()>8 || server.urls.iter().any(|url|url.len()>1024) || server.username.len()>256 || server.credential.len()>1024{return Err("Invalid relay configuration bounds".into());}
                                ice_servers.push(core_protocol::transport::NativeIceServer{urls:server.urls,username:server.username,credential:server.credential});
                            }
                            let (signals_tx,signals_rx)=mpsc::channel(32);active.signals=Some(signals_tx);
                            state.finish_previous_media().await?;
                            if active.cancel.load(Ordering::Acquire) || cancel.load(Ordering::Acquire){if active.take_close(){send(&mut socket,ClientMessage::Close{session}).await?;}continue;}
                            remote_peer::start(remote_peer::Config{state:state.clone(),generation,session,nonce,host:active.host,login_window:session_platform::login_window_mode(),remote_key:active.key,identity:identity.clone(),source:active.source.clone(),#[cfg(target_os = "linux")] portal:active.portal.clone(),confirm:active.confirm,control:active.control,clipboard:active.clipboard,files:active.files,chat:active.chat,recording:active.recording,consent_id:active.consent_id,consent_cancel:active.consent_cancel.clone(),consent_started:active.started_at,session_cancel:active.cancel.clone(),account_cancel:cancel.clone(),signals:signals_rx,commands:tx.clone(),video:video.clone(),ice_servers,relay_only:service.relay_only,authorization:broker_lease.clone(),peer_expires:active.expires.clone()})?;
                        },
                        ServerMessage::PeerRenewed{session,identity:renewed}=>{
                            let active=peer.as_mut().filter(|peer|peer.id==session).ok_or("Unexpected peer authorization renewal")?;
                            let key=peer_identity(&renewed,active.remote,&principal,active.host,&roots)?;
                            let previous=active.identity.as_ref().ok_or("Peer identity has not been authenticated")?;
                            if key!=active.key || !same_identity(previous,&renewed){return Err("Peer account, enrollment or authorization changed".into());}
                            if renewed.issued_at>=previous.issued_at && renewed.expires_at>=previous.expires_at{active.expires.store(renewed.expires_at,Ordering::Release);active.identity=Some(renewed);}
                        },
                        ServerMessage::Signal{session,payload}=>{let active=peer.as_ref().filter(|peer|peer.id==session).ok_or("Unexpected peer signaling")?;if active.cancel.load(Ordering::Acquire){continue;}active.signals.as_ref().ok_or("Media negotiation is unavailable")?.try_send(payload).map_err(|_|"Peer signaling queue overflowed")?;},
                        ServerMessage::Closed{session,reason}=>{if peer.as_ref().is_some_and(|peer|peer.id==session){peer=None;state.change(generation,|status|{status.phase="ready".into();status.message=format!("Session closed: {reason:?}");status.session=None;status.peer=None;status.requester=None;}).await;}},
                        ServerMessage::Error{code}=>{
                            if outgoing.take().is_some(){state.change(generation,|status|{status.phase="ready".into();status.peer=None;status.message=request_failure_message(code).into();}).await;}
                            else if peer.as_ref().is_some_and(|peer|peer.nonce.is_some()) && pending_renewal.is_none(){
                                let session=peer.take().ok_or("Session state unavailable")?.id;
                                send(&mut socket,ClientMessage::Close{session}).await?;
                                state.change(generation,|status|{status.phase="ready".into();status.peer=None;status.session=None;status.requester=None;status.message="The relay service is unavailable; screen sharing has not started".into();}).await;
                            }else{return Err(format!("Connection service rejected an operation: {code:?}").into());}
                        },
                        _=>return Err("Unexpected connection-service state transition".into()),
                    }
                },
            }
        }
        Ok(())
    }.await;
    let result = result.map_err(|mut error| {
        error.registered_for = ready_at
            .map(|ready| ready.elapsed())
            .unwrap_or(Duration::ZERO);
        error
    });
    cancel.store(true, Ordering::Release);
    let _ = tokio::time::timeout(Duration::from_secs(2), socket.close(None)).await;
    result
}

#[cfg(test)]
mod consent_tests {
    use super::*;
    #[cfg(feature = "testflight-controller")]
    #[tokio::test]
    async fn controller_refuses_hosting_even_if_invoked_without_the_webview() {
        let remote = RemoteSession::default();
        let error = "controller cannot host connections";
        assert!(remote.status().await.controller_only);
        assert!(
            remote
                .configure_unattended(Some("display:1".into()), true)
                .await
                .unwrap_err()
                .contains(error)
        );
        assert!(remote.pairing(None).await.unwrap_err().contains(error));
        assert!(remote.support_code(true).await.unwrap_err().contains(error));
        assert!(
            remote
                .decide(Decision {
                    session: Uuid::new_v4(),
                    accept: true,
                    source: "display:1".into(),
                    confirm: true,
                    control: true,
                    clipboard: true,
                    files: true,
                    chat: true,
                    recording: true,
                })
                .await
                .unwrap_err()
                .contains(error)
        );
        assert!(!remote.status().await.input_access);
    }

    #[cfg(not(feature = "testflight-controller"))]
    #[tokio::test]
    async fn disabling_unattended_during_enable_ignores_both_possible_enable_replies() {
        for enable_reply in [true, false] {
            let remote = RemoteSession::default();
            let (commands, mut receiver) = mpsc::channel(2);
            commands
                .try_send(Command::ConfigureUnattended { enabled: true })
                .unwrap();
            {
                let mut inner = remote.0.lock().await;
                inner.commands = Some(commands);
                inner.status.unattended = Some(UnattendedSource {
                    source: "display:1".into(),
                    control: false,
                });
                inner.status.unattended_pending = Some(UnattendedChange::Enabling);
            }
            remote.configure_unattended(None, false).await.unwrap();
            assert!(matches!(
                receiver.try_recv().unwrap(),
                Command::ConfigureUnattended { enabled: true }
            ));
            assert!(matches!(
                receiver.try_recv().unwrap(),
                Command::ConfigureUnattended { enabled: false }
            ));
            let mut inner = remote.0.lock().await;
            inner.status.acknowledge_unattended(enable_reply);
            assert_eq!(
                inner.status.unattended_pending,
                Some(UnattendedChange::Disabling)
            );
            assert!(!inner.status.unattended_enabled);
            assert!(inner.status.unattended.is_none());
            inner.status.acknowledge_unattended(false);
            assert!(inner.status.unattended_pending.is_none());
            assert!(!inner.status.unattended_enabled);
        }
    }

    #[cfg(not(feature = "testflight-controller"))]
    #[tokio::test]
    async fn disabling_unattended_with_full_queue_stops_media_and_registration() {
        let remote = RemoteSession::default();
        let (commands, _receiver) = mpsc::channel(1);
        commands.try_send(Command::Pairing(None)).unwrap();
        let consent = Arc::new(AtomicBool::new(false));
        let session = Arc::new(AtomicBool::new(false));
        {
            let mut inner = remote.0.lock().await;
            inner.cancel = Arc::new(AtomicBool::new(false));
            inner.commands = Some(commands);
            inner.consent_cancel = Some(consent.clone());
            inner.session_cancel = Some(session.clone());
            inner.status.unattended = Some(UnattendedSource {
                source: "display:1".into(),
                control: true,
            });
            inner.status.unattended_enabled = true;
            inner.status.unattended_session = true;
            inner.status.phase = "active".into();
        }
        assert!(remote.configure_unattended(None, false).await.is_err());
        let inner = remote.0.lock().await;
        assert_eq!(inner.status.phase, "closing");
        assert!(inner.status.unattended.is_none());
        assert!(!inner.status.unattended_enabled);
        assert!(consent.load(Ordering::Acquire));
        assert!(session.load(Ordering::Acquire));
        assert!(inner.cancel.load(Ordering::Acquire));
    }

    #[test]
    fn local_stop_sends_close_at_most_once() {
        let mut peer = invitation();
        assert!(peer.take_close());
        assert!(peer.cancel.load(Ordering::Acquire));
        assert!(!peer.take_close());
    }
    fn invitation() -> Peer {
        let now = std::time::Instant::now();
        Peer {
            id: Uuid::from_u128(1),
            remote: 123456789,
            host: true,
            unattended: false,
            key: ed25519_dalek::SigningKey::from_bytes(&[7; 32]).verifying_key(),
            source: "display:1".into(),
            #[cfg(target_os = "linux")]
            portal: None,
            confirm: true,
            control: false,
            clipboard: false,
            files: false,
            chat: false,
            recording: false,
            close_sent: false,
            consent_cancel: Arc::new(AtomicBool::new(false)),
            cancel: Arc::new(AtomicBool::new(false)),
            signals: None,
            nonce: None,
            identity: None,
            expires: Arc::new(AtomicU64::new(900)),
            relay_expires: 0,
            restart_pending: None,
            rotated_at: now,
            started_at: now,
            local_consent: None,
            accepted_at: None,
            consent_id: Uuid::from_u128(2),
        }
    }
    #[test]
    fn selected_source_and_broker_acceptance_cannot_replace_local_accept_or_extend_it() {
        let mut peer = invitation();
        let now = std::time::Instant::now();
        assert!(!peer.can_negotiate(now));
        peer.local_consent = Some(false);
        assert!(!peer.can_negotiate(now));
        peer.local_consent = Some(true);
        assert!(!peer.can_negotiate(now));
        peer.accepted_at = Some(now);
        assert!(peer.can_negotiate(now));
        assert!(!peer.can_negotiate(now + core_protocol::session::NEGOTIATION_LIFETIME));
        peer.cancel.store(true, Ordering::Release);
        assert!(!peer.can_negotiate(now));
    }
    #[tokio::test]
    async fn stop_targets_same_local_consent_after_room_rotation_even_with_full_notification_queue()
    {
        let remote = RemoteSession::default();
        let old = Arc::new(AtomicBool::new(false));
        let fresh = Arc::new(AtomicBool::new(false));
        let scope = Uuid::from_u128(11);
        let original = Uuid::from_u128(12);
        let replacement = Uuid::from_u128(13);
        remote
            .track(
                0,
                original,
                old.clone(),
                scope,
                Arc::new(AtomicBool::new(false)),
            )
            .await
            .unwrap();
        remote
            .track(
                0,
                replacement,
                fresh.clone(),
                scope,
                Arc::new(AtomicBool::new(false)),
            )
            .await
            .unwrap();
        let (commands, _receiver) = mpsc::channel(1);
        commands
            .try_send(Command::Request(123456789, None, false))
            .unwrap();
        {
            let mut inner = remote.0.lock().await;
            inner.commands = Some(commands);
            inner.status.phase = "active".into();
            inner.status.control = true;
            inner.status.clipboard = true;
        }
        assert!(remote.close(original).await.is_err());
        assert!(!fresh.load(Ordering::Acquire));
        assert!(remote.stop_session(Uuid::from_u128(99)).await.is_err());
        assert!(!fresh.load(Ordering::Acquire));
        assert!(remote.stop_session(scope).await.is_err());
        assert!(fresh.load(Ordering::Acquire));
        assert!(old.load(Ordering::Acquire));
        let inner = remote.0.lock().await;
        assert_eq!(inner.status.phase, "closing");
        assert!(!inner.status.control);
        assert!(!inner.status.clipboard);
    }
    #[tokio::test]
    async fn rotating_transport_cannot_change_file_grant_or_clear_it_with_a_stale_stop() {
        let remote = RemoteSession::default();
        let scope = Uuid::new_v4();
        let old = Uuid::new_v4();
        let new = Uuid::new_v4();
        let root = Arc::new(AtomicBool::new(false));
        remote
            .track(
                0,
                old,
                Arc::new(AtomicBool::new(false)),
                scope,
                root.clone(),
            )
            .await
            .unwrap();
        remote.file_grant(0, old, true).await.unwrap();
        remote
            .track(
                0,
                new,
                Arc::new(AtomicBool::new(false)),
                scope,
                root.clone(),
            )
            .await
            .unwrap();
        assert!(remote.stop_session(Uuid::new_v4()).await.is_err());
        assert_eq!(remote.0.lock().await.file_permission, Some(true));
        assert!(remote.file_grant(0, new, false).await.is_err());
        assert!(root.load(Ordering::Acquire));
    }
    #[tokio::test]
    async fn queued_input_after_session_cancellation_is_discarded() {
        let remote = RemoteSession::default();
        let session = Uuid::new_v4();
        let (input, mut receiver) = mpsc::channel(1);
        {
            let mut inner = remote.0.lock().await;
            inner.status.phase = "active".into();
            inner.status.session = Some(session);
            inner.status.control = true;
            inner.session_cancel = Some(Arc::new(AtomicBool::new(true)));
            inner.input = Some(input);
        }
        assert!(!remote.input(session, InputEvent::ReleaseAll).await.unwrap());
        assert!(matches!(receiver.try_recv(), Err(mpsc::error::TryRecvError::Empty)));
    }

    #[tokio::test]
    async fn relay_fixture_cannot_be_accepted_outside_the_signed_qa_host() {
        let remote = RemoteSession::default();
        assert!(
            remote
                .configure_unattended(Some("qa-relay-fixture".into()), false)
                .await
                .is_err()
        );
        let room = Uuid::from_u128(30);
        let (commands, mut receiver) = mpsc::channel(1);
        {
            let mut inner = remote.0.lock().await;
            inner.commands = Some(commands);
            inner.status.phase = "incoming".into();
            inner.status.session = Some(room);
        }
        let result = remote
            .decide(Decision {
                session: room,
                accept: true,
                source: "qa-relay-fixture".into(),
                confirm: true,
                control: false,
                clipboard: false,
                files: false,
                chat: false,
                recording: false,
            })
            .await;
        assert!(result.is_err());
        assert!(matches!(
            receiver.try_recv(),
            Err(mpsc::error::TryRecvError::Empty)
        ));
    }
    #[cfg(not(feature = "testflight-controller"))]
    #[tokio::test]
    async fn reject_revokes_locally_before_busy_broker_can_acknowledge() {
        let remote = RemoteSession::default();
        let cancelled = Arc::new(AtomicBool::new(false));
        let room = Uuid::from_u128(22);
        remote
            .track(
                0,
                room,
                cancelled.clone(),
                Uuid::from_u128(23),
                Arc::new(AtomicBool::new(false)),
            )
            .await
            .unwrap();
        let (commands, _receiver) = mpsc::channel(1);
        commands
            .try_send(Command::Request(123456789, None, false))
            .unwrap();
        {
            let mut inner = remote.0.lock().await;
            inner.commands = Some(commands);
            inner.status.phase = "incoming".into();
        }
        let result = remote
            .decide(Decision {
                session: room,
                accept: false,
                source: "display:1".into(),
                confirm: true,
                control: true,
                clipboard: true,
                files: true,
                chat: false,
                recording: false,
            })
            .await;
        assert!(result.is_err());
        assert!(cancelled.load(Ordering::Acquire));
        assert_eq!(remote.0.lock().await.status.phase, "closing");
    }
}

#[cfg(all(test, any(target_os = "linux", target_os = "windows")))]
#[path = "remote_session_native_test.rs"]
mod remote_session_native_test;
