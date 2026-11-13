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

/// Session features of this app: colleagues can join, sides can be switched.
pub const JOIN: &str = signaling_server::protocol::FEATURE_JOIN;
pub const SWITCH: &str = signaling_server::protocol::FEATURE_SWITCH;

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
    /// Remote IDs from the last presence check that are online for this account.
    pub online: Vec<u32>,
    /// Controller: number of host displays it may switch between (0 or 1 = no switching).
    pub displays: u16,
    /// Controller: the host display currently shown.
    pub display: u16,
    /// Controller: the last display is "all monitors as one picture".
    pub display_all: bool,
    /// Controller: print jobs received from the host in this session (PDF file paths).
    pub print_jobs: Vec<String>,
    /// Controller: one-click actions the host offered ("sas", "restart").
    pub host_actions: Vec<String>,
    /// Controller: Remote ID of the host's sign-in screen access, to reconnect after a restart.
    pub signin_id: Option<u32>,
    /// Controller: the host's network adapter addresses, remembered to wake it later.
    pub host_macs: Vec<String>,
    /// Host: the technician offers to show their screen in this Mixel Meet room (session, room id).
    pub meeting_offer: Option<(Uuid, String)>,
    /// Host: colleagues of the technician who joined the running session.
    pub colleagues: Vec<Colleague>,
    /// Host: a colleague of the technician asks to join the running session.
    pub join_request: Option<JoinRequest>,
    /// Technician: colleagues invite this app into their running session.
    pub invitations: Vec<InvitationView>,
    /// Technician: what became of the last invitation this app sent.
    pub invite_message: Option<String>,
    /// Technician: this session was joined on a colleague's invitation.
    pub joined_session: bool,
    /// The sides are switched right now: the computer that was helped views the technician's
    /// (false again after switching back). The viewing side then cannot invite colleagues.
    pub switched: bool,
    /// Optional features the app on the other side announced (`join`, `switch`, …).
    pub peer_features: Vec<String>,
    #[serde(skip)]
    code_expires_at: Option<std::time::Instant>,
}
/// A colleague connected to (or asking for) this computer next to the first technician.
#[derive(Clone, Serialize)]
pub struct Colleague {
    pub session: Uuid,
    pub connection: u32,
    pub control: bool,
    /// `asking`, `connecting` or `active`.
    pub state: &'static str,
}
#[derive(Clone, Serialize)]
pub struct JoinRequest {
    pub session: Uuid,
    pub connection: u32,
    pub inviter_connection: u32,
    pub control: bool,
    pub unattended: bool,
}
#[derive(Clone, Serialize)]
pub struct InvitationView {
    pub invitation: Uuid,
    pub host: u32,
    /// The inviting technician's account.
    pub inviter: Uuid,
    pub inviter_connection: u32,
    pub control: bool,
    pub unattended: bool,
    /// Seconds since the Unix epoch.
    pub expires_at: u64,
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
            online: Vec::new(),
            displays: 0,
            display: 0,
            display_all: false,
            print_jobs: Vec::new(),
            host_actions: Vec::new(),
            meeting_offer: None,
            signin_id: None,
            host_macs: Vec::new(),
            colleagues: Vec::new(),
            join_request: None,
            invitations: Vec::new(),
            invite_message: None,
            joined_session: false,
            switched: false,
            peer_features: Vec::new(),
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
    #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
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
            #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
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
            #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
            Self::Startup { enrolled, .. } => Ok(&enrolled.principal),
        }
    }

    async fn enrollment(
        &self,
        credential: &Option<Credential>,
        identity: &IdentityState,
    ) -> Result<EnrolledPrincipal, String> {
        #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
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
        #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
        None => crate::device_tickets::issue_startup(enrolled, identity).await,
        
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
    Presence(Vec<u32>),
    Agent {
        inventory: Option<String>,
        result: Option<signaling_server::protocol::AgentResult>,
    },
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
    /// Technician: invite a colleague (by account or by the Remote ID of their computer).
    Invite {
        session: Uuid,
        user: Option<Uuid>,
        device: Option<u32>,
        control: bool,
    },
    /// Technician: join a colleague's running session.
    Join(Uuid),
    /// Either side: switch sides in the running session.
    SwitchSides(Uuid),
    /// Host: the person at the computer lets a colleague join (or not).
    DecideJoin {
        session: Uuid,
        accept: bool,
        control: bool,
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
    /// Controller only: show another of the host's displays (never sent as input).
    SelectDisplay { index: u16 },
    /// Controller only: run a host action the host offered ("sas", "restart").
    Action { name: String },
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
            Self::ReleaseAll | Self::SelectDisplay { .. } | Self::Action { .. } => Input::ReleaseAll,
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
/// Media owner threads; `true` marks a colleague's (joined) media.
type MediaWorkers = Arc<std::sync::Mutex<Vec<(bool, std::thread::JoinHandle<()>)>>>;
#[derive(Clone)]
pub struct RemoteSession(
    Arc<Mutex<Inner>>,
    MediaWorkers,
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
    /// Host: records the permissions a peer's session was granted. A colleague's mirrored view (a
    /// second technician) has its own session ID and no state of its own here; it only ever gets
    /// pictures, and recording the grant would belong to the first technician's session (and fail).
    pub(crate) async fn record_grants(
        &self,
        generation: u64,
        session: Uuid,
        mirror: bool,
        files: bool,
        recording: bool,
    ) -> Result<(), String> {
        if mirror {
            return if files || recording {
                Err("A colleague's view cannot transfer files or record".into())
            } else {
                Ok(())
            };
        }
        self.file_grant(generation, session, files).await?;
        self.set_recording_permission(generation, session, recording).await;
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
            // Colleagues' media keeps running: only the first technician's transport is replaced.
            while workers.iter().any(|(colleague, worker)| !colleague && !worker.is_finished()) {
                if std::time::Instant::now() >= deadline {
                    return Err("The previous transport has not finished cleanup".into());
                }
                drop(workers);
                std::thread::sleep(Duration::from_millis(10));
                workers = registry
                    .lock()
                    .map_err(|_| "Media owner registry is unavailable".to_owned())?;
            }
            let mut index = 0;
            while index < workers.len() {
                if !workers[index].0 {
                    workers
                        .swap_remove(index)
                        .1
                        .join()
                        .map_err(|_| "A previous media owner failed".to_owned())?;
                } else {
                    index += 1;
                }
            }
            Ok(())
        })
        .await
        .map_err(|_| "Media cleanup owner failed".to_owned())?
    }
    pub(crate) fn spawn_media(
        &self,
        cancel: &AtomicBool,
        colleague: bool,
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
            if workers[index].1.is_finished() {
                workers
                    .swap_remove(index)
                    .1
                    .join()
                    .map_err(|_| "A prior media owner failed")?;
            } else {
                index += 1;
            }
        }
        let running = workers.iter().filter(|(kind, _)| *kind == colleague).count();
        if running >= if colleague { 4 } else { 2 } {
            return Err("A prior media owner is still stopping".into());
        }
        workers.push((
            colleague,
            std::thread::Builder::new()
                .name(if colleague { "mixel-remote-colleague" } else { "mixel-remote-peer" }.into())
                .spawn(run)
                .map_err(|_| "Cannot start the remote media owner")?,
        ));
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

    #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
    pub async fn connect_startup(
        &self,
        enrolled: EnrolledPrincipal,
        display: u32,
        control: bool,
        identity: Arc<IdentityState>,
    ) -> Result<(), String> {
        crate::service_config::require_host()?;
        #[cfg(target_os = "macos")]
        let source = UnattendedSource {
            source: format!("display:{display}"),
            control,
        };
        #[cfg(target_os = "windows")]
        let source = UnattendedSource {
            source: crate::windows_signin_host::primary_display_source(display)?,
            control,
        };
        // Linux: the sign-in screen's X display (DISPLAY of the host process).
        #[cfg(target_os = "linux")]
        let source = UnattendedSource {
            source: native_os_capture::x11_capture::displays()
                .map_err(|error| error.to_string())?
                .get(display as usize)
                .map(|screen| format!("x11:{}:{}", screen.screen, screen.root))
                .ok_or("The sign-in screen display is unavailable")?,
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
        crate::agent::start(self.clone(), tx.clone());
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
            while workers.iter().any(|(_, worker)| !worker.is_finished()) {
                if std::time::Instant::now() >= deadline {
                    return Err(
                        "A media owner has not finished cleanup; quitting was postponed".into(),
                    );
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            for (_, worker) in workers.drain(..) {
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

    /// Asks the connection service which of these Remote IDs are online for
    /// this account. The answer arrives in `Status::online`.
    pub async fn presence(&self, hosts: Vec<u32>) -> Result<(), String> {
        if hosts.len() > 64 || hosts.iter().any(|id| !(100_000_000..=999_999_999).contains(id)) {
            return Err("Invalid device list".into());
        }
        let inner = self.0.lock().await;
        if !matches!(inner.status.phase.as_str(), "ready" | "active" | "incoming" | "outgoing") {
            return Ok(());
        }
        inner
            .commands
            .as_ref()
            .ok_or("Connect this device first")?
            .try_send(Command::Presence(hosts))
            .map_err(|_| "The connection service is busy".into())
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
    /// Technician: invites a colleague into the running session (account or Remote ID).
    pub async fn invite(&self, session: Uuid, user: Option<Uuid>, device: Option<u32>, control: bool) -> Result<(), String> {
        if user.is_some() == device.is_some() {
            return Err("Choose a colleague or enter the Remote ID of their computer".into());
        }
        if device.is_some_and(|id| !(100_000_000..=999_999_999).contains(&id)) {
            return Err("Enter a nine-digit Remote ID".into());
        }
        let mut inner = self.0.lock().await;
        if inner.status.session != Some(session)
            || inner.status.phase != "active"
            || inner.status.sharing
            || inner.status.joined_session
            || !inner.status.peer_features.iter().any(|name| name == JOIN)
        {
            return Err("Colleagues can be invited into a running session you started".into());
        }
        if inner.status.peer == device {
            return Err("This is the computer you are helping".into());
        }
        if inner.status.switched {
            return Err("Colleagues can be invited after switching back: right now you are viewing the technician's screen".into());
        }
        inner
            .commands
            .as_ref()
            .ok_or("The device is disconnected")?
            .try_send(Command::Invite { session, user, device, control })
            .map_err(|_| "The connection service is busy")?;
        inner.status.invite_message = Some("Sending the invitation".into());
        Ok(())
    }
    /// Technician: joins a colleague's running session from an invitation.
    pub async fn join(&self, invitation: Uuid) -> Result<(), String> {
        let mut inner = self.0.lock().await;
        let now = SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_| "System clock is unavailable")?.as_secs();
        let Some(offer) = inner.status.invitations.iter().find(|offer| offer.invitation == invitation && offer.expires_at > now).cloned() else {
            return Err("This invitation has expired".into());
        };
        if inner.status.phase != "ready" {
            return Err("Finish the current session before joining a colleague".into());
        }
        inner
            .commands
            .as_ref()
            .ok_or("Connect this device first")?
            .try_send(Command::Join(invitation))
            .map_err(|_| "The connection service is busy")?;
        inner.status.invitations.retain(|other| other.invitation != invitation);
        inner.status.phase = "outgoing".into();
        inner.status.peer = Some(offer.host);
        inner.status.message = "Joining your colleague's session".into();
        Ok(())
    }
    /// Removes an invitation without joining.
    pub async fn dismiss_invitation(&self, invitation: Uuid) {
        self.0.lock().await.status.invitations.retain(|offer| offer.invitation != invitation);
    }
    /// Either side: switches sides in the running session.
    pub async fn switch_sides(&self, session: Uuid) -> Result<(), String> {
        let mut inner = self.0.lock().await;
        if inner.status.session != Some(session)
            || inner.status.phase != "active"
            || inner.status.joined_session
            || !inner.status.colleagues.is_empty()
            || !inner.status.peer_features.iter().any(|name| name == SWITCH)
        {
            return Err("Sides can be switched in a running session between two people".into());
        }
        if inner.status.sharing && crate::service_config::controller_only() {
            return Err("This app cannot show its screen".into());
        }
        inner
            .commands
            .as_ref()
            .ok_or("The device is disconnected")?
            .try_send(Command::SwitchSides(session))
            .map_err(|_| "The connection service is busy")?;
        inner.status.message = "Switching sides".into();
        Ok(())
    }
    /// Host: the person at the computer answers a colleague's request to join.
    pub async fn decide_join(&self, session: Uuid, accept: bool, control: bool) -> Result<(), String> {
        crate::service_config::require_host()?;
        let mut inner = self.0.lock().await;
        if inner.status.join_request.as_ref().is_none_or(|request| request.session != session) {
            return Err("This request is no longer pending".into());
        }
        if accept && control && !session_platform::input_access() {
            return Err("Keyboard and mouse control need local input access".into());
        }
        inner
            .commands
            .as_ref()
            .ok_or("The device is disconnected")?
            .try_send(Command::DecideJoin { session, accept, control })
            .map_err(|_| "The connection service is busy")?;
        inner.status.join_request = None;
        Ok(())
    }
    /// Host: ends one colleague's view (the first technician stays).
    pub async fn remove_colleague(&self, session: Uuid) -> Result<(), String> {
        let inner = self.0.lock().await;
        if !inner.status.colleagues.iter().any(|colleague| colleague.session == session) {
            return Err("This colleague has already left".into());
        }
        inner
            .commands
            .as_ref()
            .ok_or("The device is disconnected")?
            .try_send(Command::Close(session))
            .map_err(|_| "The connection service is busy".into())
    }
    pub(crate) async fn set_peer_features(&self, generation: u64, session: Uuid, features: Vec<String>) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation && inner.status.session == Some(session) && !inner.cancel.load(Ordering::Acquire) {
            inner.status.peer_features = features;
        }
    }
    pub async fn input(&self, session: Uuid, event: InputEvent) -> Result<bool, String> {
        core_protocol::input::validate(&event.borrowed()).map_err(|error| error.to_string())?;
        let selected = match event {
            InputEvent::SelectDisplay { index } => Some(index),
            _ => None,
        };
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
        let result = tokio::time::timeout(Duration::from_secs(3), rx)
            .await
            .map_err(|_| "Input acknowledgment timed out")?
            .map_err(|_| "Input transport closed")?;
        if let (Some(index), Ok(true)) = (selected, &result) {
            let mut inner = self.0.lock().await;
            if inner.status.session == Some(session) {
                inner.status.display = index;
            }
        }
        result
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
    pub(crate) async fn add_print_job(&self, generation: u64, session: Uuid, path: String) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation
            && inner.status.session == Some(session)
            && !inner.cancel.load(Ordering::Acquire)
        {
            inner.status.print_jobs.push(path);
        }
    }
    pub(crate) async fn set_displays(&self, generation: u64, session: Uuid, count: u16, current: u16, all: bool) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation
            && inner.status.session == Some(session)
            && !inner.cancel.load(Ordering::Acquire)
        {
            inner.status.displays = count;
            inner.status.display = current;
            inner.status.display_all = all;
        }
    }
    pub(crate) async fn set_host_actions(&self, generation: u64, session: Uuid, actions: Vec<String>, signin_id: Option<u32>, macs: Vec<String>) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation
            && inner.status.session == Some(session)
            && !inner.cancel.load(Ordering::Acquire)
        {
            inner.status.host_actions = actions;
            inner.status.signin_id = signin_id;
            inner.status.host_macs = macs;
        }
    }
    pub(crate) async fn set_meeting_offer(&self, generation: u64, session: Uuid, room: String) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation && inner.status.session == Some(session) && !inner.cancel.load(Ordering::Acquire) {
            inner.status.meeting_offer = Some((session, room));
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
                    deadline: std::time::Instant::now() + Duration::from_secs(125),
                    reply,
                })
                .map_err(|_| "A clipboard transfer is already pending")?;
        }
        tokio::time::timeout(Duration::from_secs(127), wait)
            .await
            .map_err(|_| "Clipboard transfer acknowledgment timed out")?
            .map_err(|_| "The clipboard transport closed")?
    }
    pub async fn next_video(&self, session: Uuid, window_decodes: bool) -> Result<Vec<u8>, String> {
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
        let video = inner.video.as_ref().ok_or("Video is closed")?;
        video.window_decodes(session, window_decodes);
        video.take(session)
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
            inner.status.peer_features.clear();
            if inner.status.session.is_none() {
                inner.status.colleagues.clear();
                inner.status.join_request = None;
                inner.status.joined_session = false;
                inner.status.switched = false;
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
    #[allow(clippy::too_many_arguments)]
    async fn track(
        &self,
        generation: u64,
        session: Uuid,
        cancel: Arc<AtomicBool>,
        consent: Uuid,
        consent_cancel: Arc<AtomicBool>,
        joined: bool,
        reversed: bool,
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
        inner.status.joined_session = joined;
        inner.status.switched = reversed;
        inner.status.peer_features.clear();
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
    /// Signals from a faster peer that arrived while this side still waits for its relay configuration.
    early: Vec<Signal>,
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
    /// Host: a colleague who joined the first technician's session (mirrors its pictures).
    /// Technician: this session was joined on an invitation.
    joined: bool,
    /// The sides were switched: the other side was already verified in the previous session.
    switched: bool,
    /// The sides are the reverse of the session as first requested (flips with every switch).
    reversed: bool,
}
/// What this app remembers about a session whose sides are being switched.
struct SwitchMemory {
    previous: Uuid,
    remote: u32,
    key: ed25519_dalek::VerifyingKey,
    identity: core_protocol::delegation::DeviceTicket,
    /// The grants of the person at this computer from when it was helped (restored on switching back).
    grants: Option<HostGrants>,
    /// The ended session's sides were reversed: the next one has them the other way round.
    reversed: bool,
    at: std::time::Instant,
}
#[derive(Clone)]
struct HostGrants {
    source: String,
    confirm: bool,
    control: bool,
    clipboard: bool,
    files: bool,
    chat: bool,
    recording: bool,
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
/// The other side of a switched session: the same device and account as in the session before.
fn switched_identity(
    ticket: &core_protocol::delegation::DeviceTicket,
    connection: u32,
    known: &core_protocol::delegation::DeviceTicket,
    roots: &[ed25519_dalek::VerifyingKey],
) -> Result<ed25519_dalek::VerifyingKey, String> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "System clock is unavailable")?
        .as_secs();
    let verified = ticket
        .verify(roots, now)
        .map_err(|_| "The remote identity authority proof is invalid or expired")?;
    if ticket.identity.connection_id != connection
        || ticket.unattended_host_only()
        || !same_identity(ticket, known)
    {
        return Err("The other side changed while switching sides".into());
    }
    Ok(verified.key())
}
/// Host: checks a colleague's accepted session like the first technician's, and binds it.
#[allow(clippy::too_many_arguments)]
fn colleague_accepted(
    colleague: &mut Peer,
    session: Uuid,
    nonce: [u8; 32],
    controller: &core_protocol::delegation::DeviceTicket,
    host: &core_protocol::delegation::DeviceTicket,
    own: &core_protocol::delegation::DeviceTicket,
    principal: &Principal,
    roots: &[ed25519_dalek::VerifyingKey],
) -> Result<[u8; 32], String> {
    if colleague.cancel.load(Ordering::Acquire)
        || !colleague.can_negotiate(std::time::Instant::now())
        || nonce == [0; 32]
        || colleague.source.is_empty()
        || colleague.nonce.is_some()
    {
        return Err("The colleague has no current local consent".into());
    }
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "System clock is unavailable")?
        .as_secs();
    host.verify(roots, now).map_err(|_| "The local session identity is invalid or expired")?;
    if !same_identity(host, own) {
        return Err("The colleague's session does not belong to this device".into());
    }
    let key = peer_identity(controller, colleague.remote, principal, true, roots)?;
    if key != colleague.key || colleague.identity.as_ref().is_some_and(|known| !same_identity(known, controller)) {
        return Err("The colleague's identity changed".into());
    }
    colleague.expires.store(controller.expires_at, Ordering::Release);
    colleague.identity = Some(controller.clone());
    Ok(core_protocol::delegation::session_binding(*session.as_bytes(), nonce, controller, host))
}
fn colleague_views(joined: &[Peer]) -> Vec<Colleague> {
    joined
        .iter()
        .map(|peer| Colleague {
            session: peer.id,
            connection: peer.remote,
            control: peer.control,
            state: if peer.local_consent.is_none() {
                "asking"
            } else if peer.signals.is_some() {
                "active"
            } else {
                "connecting"
            },
        })
        .collect()
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
                self.joined && !self.host,
                self.reversed,
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
            #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
            RegistrationAuth::Startup { source, .. } => {
                if !source.capture_allowed() {
                    return Err("The sign-in screen host is missing the required system permissions".into());
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
        // Second technicians and switching sides: announced to the connection service, which
        // forwards invitations and switches only to apps that understand them.
        let features:Vec<String>=if own_ticket.unattended_host_only() || crate::service_config::controller_only() {vec![JOIN.into()]} else {vec![JOIN.into(),SWITCH.into()]};
        send(&mut socket,ClientMessage::Capabilities{features}).await?;
        // An older connection service answers "invalid": then these features are simply not offered.
        let capabilities_sent=std::time::Instant::now();
        let mut peer:Option<Peer>=None;
        // Host: colleagues of the technician who joined (or ask to join) the running session.
        let mut joined:Vec<Peer>=Vec::new();
        // (host, pinned key, unattended, joined on an invitation)
        let mut outgoing:Option<(u32,ed25519_dalek::VerifyingKey,bool,bool)>=None;
        // Invitations received: id, host, unattended, expiry (Unix seconds).
        let mut invitations:Vec<(Uuid,u32,bool,u64)>=Vec::new();
        let mut invite_pending=false;
        let mut switch_pressed:Option<(Uuid,std::time::Instant)>=None;
        let mut switch_memory:Option<SwitchMemory>=None;
    // Presence questions still waiting for their answer, oldest first: an answer speaks only for the IDs asked.
    let mut presence_asked: std::collections::VecDeque<Vec<u32>> = std::collections::VecDeque::new();
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
                    if !joined.is_empty() {
                        // A colleague's request or connection that ran out of time, or whose first
                        // technician is gone, ends here (the connection service ends it as well).
                        let primary_hosting=peer.as_ref().is_some_and(|active|active.host && !active.cancel.load(Ordering::Acquire));
                        let ended:Vec<Uuid>=joined.iter().filter(|colleague|{
                            let expired=colleague.signals.is_none() && ((colleague.local_consent.is_none() && colleague.started_at.elapsed()>=core_protocol::session::CONSENT_LIFETIME) || colleague.accepted_at.is_some_and(|accepted|accepted.elapsed()>=core_protocol::session::NEGOTIATION_LIFETIME));
                            expired || !primary_hosting || (colleague.cancel.load(Ordering::Acquire) && colleague.signals.is_none())
                        }).map(|colleague|colleague.id).collect();
                        for session in ended {
                            if let Some(index)=joined.iter().position(|colleague|colleague.id==session){
                                let mut gone=joined.remove(index);
                                let undecided=gone.local_consent.is_none();
                                if gone.take_close(){send(&mut socket,if undecided{ClientMessage::Decide{session,accept:false}}else{ClientMessage::Close{session}}).await?;}
                            }
                            let views=colleague_views(&joined);
                            state.change(generation,|status|{status.colleagues=views;if status.join_request.as_ref().is_some_and(|request|request.session==session){status.join_request=None;}}).await;
                        }
                    }
                    if invitations.iter().any(|(_,_,_,expires)|*expires<=now){
                        invitations.retain(|(_,_,_,expires)|*expires>now);
                        state.change(generation,|status|status.invitations.retain(|offer|offer.expires_at>now)).await;
                    }
                    if switch_pressed.is_some_and(|(_,at)|at.elapsed()>=Duration::from_secs(30)){switch_pressed=None;}
                    if peer.is_none() && switch_memory.as_ref().is_some_and(|memory|memory.at.elapsed()>=Duration::from_secs(120)){switch_memory=None;}
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
                        send(&mut socket,request).await?;outgoing=Some((host,key,unattended,false));
                        state.change(generation,|status|{status.phase="outgoing".into();status.message="Waiting for the remote host to Accept".into();status.peer=Some(host);status.sharing=false;}).await;
                    },
                    Command::ConfigureUnattended{enabled}=>{
                        send(&mut socket,ClientMessage::ConfigureUnattended{enabled}).await?;
                    },
                    Command::Presence(hosts)=>{
                        if presence_asked.len()>=16{presence_asked.pop_front();}
                        presence_asked.push_back(hosts.clone());
                        send(&mut socket,ClientMessage::Presence{hosts}).await?;
                    },
                    Command::Agent{inventory,result}=>{
                        send(&mut socket,ClientMessage::Agent{inventory,result}).await?;
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
                        if let Some(index)=joined.iter().position(|colleague|colleague.id==session){
                            let mut gone=joined.remove(index);
                            let undecided=gone.local_consent.is_none();
                            if gone.take_close(){send(&mut socket,if undecided{ClientMessage::Decide{session,accept:false}}else{ClientMessage::Close{session}}).await?;}
                            let views=colleague_views(&joined);
                            state.change(generation,|status|status.colleagues=views).await;
                        }
                    },
                    Command::Invite{session,user,device,control}=>{
                        if peer.as_ref().is_some_and(|active|active.id==session && !active.host && !active.joined && active.signals.is_some()){
                            invite_pending=true;
                            send(&mut socket,ClientMessage::Invite{session,user,device,control}).await?;
                        }
                    },
                    Command::Join(invitation)=>{
                        if peer.is_some() || outgoing.is_some(){continue;}
                        let Some((_,host,unattended,_))=invitations.iter().find(|offer|offer.0==invitation).copied() else {continue};
                        invitations.retain(|offer|offer.0!=invitation);
                        let Some(credential) = credential.as_ref() else { return Err("A startup host cannot join sessions".into()); };
                        let key=match credential.ism.peer_key(&credential.token,host).await{Ok(key)=>key,Err(_)=>{state.change(generation,|status|{status.phase="ready".into();status.peer=None;status.message="This account could not verify the computer your colleague is helping.".into();}).await;continue;}};
                        if cancel.load(Ordering::Acquire){break;}
                        send(&mut socket,ClientMessage::JoinSession{invitation}).await?;outgoing=Some((host,key,unattended,true));
                        state.change(generation,|status|{status.phase="outgoing".into();status.message="Waiting for the person at the computer to let you join".into();status.peer=Some(host);status.sharing=false;}).await;
                    },
                    Command::SwitchSides(session)=>{
                        if joined.is_empty() && peer.as_ref().is_some_and(|active|active.id==session && !active.joined && active.signals.is_some() && !active.cancel.load(Ordering::Acquire)){
                            switch_pressed=Some((session,std::time::Instant::now()));
                            send(&mut socket,ClientMessage::SwitchSides{session}).await?;
                        }
                    },
                    Command::DecideJoin{session,accept,control}=>{
                        let Some(index)=joined.iter().position(|colleague|colleague.id==session && colleague.local_consent.is_none()) else {continue};
                        let primary=peer.as_ref().filter(|active|active.host && active.signals.is_some()).map(|active|(active.source.clone(),active.confirm));
                        if accept && let Some((source,confirm))=primary {
                            let colleague=&mut joined[index];
                            colleague.local_consent=Some(true);colleague.accepted_at=Some(std::time::Instant::now());
                            colleague.source=source;colleague.confirm=confirm;colleague.control=control && confirm;
                            send(&mut socket,ClientMessage::Decide{session,accept:true}).await?;
                        } else {
                            let mut gone=joined.remove(index);gone.take_close();
                            send(&mut socket,ClientMessage::Decide{session,accept:false}).await?;
                        }
                        let views=colleague_views(&joined);
                        state.change(generation,|status|status.colleagues=views).await;
                    },
                    Command::Restart(session)=>{
                        if let Some(active)=peer.as_mut().filter(|peer|peer.id==session && !peer.host && peer.signals.is_some() && !peer.cancel.load(Ordering::Acquire) && peer.restart_pending.is_none() && peer.rotated_at.elapsed()>=Duration::from_secs(31)){
                            send(&mut socket,ClientMessage::Restart{session}).await?;active.restart_pending=Some(std::time::Instant::now());
                        }
                    },
                    Command::Signal{session,signal}=>{if peer.as_ref().is_some_and(|peer|peer.id==session && !peer.cancel.load(Ordering::Acquire)) || joined.iter().any(|colleague|colleague.id==session && !colleague.cancel.load(Ordering::Acquire)){send(&mut socket,ClientMessage::Signal{session,payload:signal}).await?;}},
                    Command::Finished{session,result}=>{if let Some(index)=joined.iter().position(|colleague|colleague.id==session){
                        let mut gone=joined.remove(index);
                        if gone.take_close(){send(&mut socket,ClientMessage::Close{session}).await?;}
                        if let Err(error)=&result{eprintln!("colleague session ended: {error}");}
                        let views=colleague_views(&joined);
                        state.change(generation,|status|status.colleagues=views).await;
                        continue;
                    }
                    if peer.as_ref().is_some_and(|peer|peer.id==session && !peer.close_sent){peer=None;send(&mut socket,ClientMessage::Close{session}).await?;state.change(generation,|status|{status.phase="ready".into();status.session=None;status.peer=None;status.requester=None;status.message=result.err().unwrap_or_else(||"Session closed; ready for a new connection".into());}).await;}},
                },
                incoming=event(&mut socket)=>{
                    let message=incoming?;last_activity=std::time::Instant::now();
                    let Some(message)=message else{continue;};
                    // A colleague's session (second technician) is handled on its own: a failure
                    // there ends that colleague's view, never the first technician's session.
                    let colleague_session=match &message {
                        ServerMessage::Accepted{session,..}|ServerMessage::Ice{session,..}|ServerMessage::Signal{session,..}|ServerMessage::PeerRenewed{session,..}|ServerMessage::Closed{session,..}=>Some(*session),
                        ServerMessage::Restarted{previous,..}=>Some(*previous),
                        _=>None,
                    };
                    if let Some(index)=colleague_session.and_then(|session|joined.iter().position(|colleague|colleague.id==session)){
                        // Ok(Some(session)): ask the connection service for the colleague's relay.
                        let outcome:Result<Option<Uuid>,String>=match message {
                            ServerMessage::Closed{..}=>{joined.remove(index);Ok(None)},
                            ServerMessage::Restarted{session,nonce,controller_identity,host_identity,..}|ServerMessage::Accepted{session,nonce,controller_identity:Some(controller_identity),host_identity:Some(host_identity)}=>{
                                let colleague=&mut joined[index];
                                let replaced=colleague.id!=session;
                                if replaced && (colleague.signals.is_none() || colleague.cancel.load(Ordering::Acquire)) {
                                    Err("Unexpected colleague transport replacement".to_owned())
                                } else {
                                    if replaced {
                                        // Transport replacement for the same accepted colleague.
                                        colleague.cancel.store(true,Ordering::Release);
                                        colleague.id=session;colleague.close_sent=false;colleague.cancel=Arc::new(AtomicBool::new(false));colleague.signals=None;colleague.nonce=None;colleague.relay_expires=0;colleague.rotated_at=std::time::Instant::now();colleague.accepted_at=Some(std::time::Instant::now());
                                    }
                                    let accepted=colleague_accepted(colleague,session,nonce,&controller_identity,&host_identity,&own_ticket,&principal,&roots);
                                    accepted.map(|binding|{colleague.nonce=Some(binding);Some(session)})
                                }
                            },
                            ServerMessage::Accepted{..}=>Err("The colleague's session has no signed identities".to_owned()),
                            ServerMessage::Ice{session,lease}=>{
                                let colleague=&mut joined[index];
                                let now=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_|"System clock is unavailable")?.as_secs();
                                match (colleague.nonce.take(),colleague.signals.is_none()) {
                                    (Some(binding),true) if lease.expires_at>now && lease.expires_at<=now.saturating_add(3600) && lease.expires_at<=authorized_until && lease.servers.len()<=8 && lease.servers.iter().all(|server|!server.urls.is_empty() && server.urls.len()<=8 && server.urls.iter().all(|url|url.len()<=1024) && server.username.len()<=256 && server.credential.len()<=1024) => {
                                        colleague.relay_expires=lease.expires_at;
                                        let ice_servers=lease.servers.into_iter().map(|server|core_protocol::transport::NativeIceServer{urls:server.urls,username:server.username,credential:server.credential}).collect();
                                        let (signals_tx,signals_rx)=mpsc::channel(32);
                                        let mut overflow=false;
                                        for early in colleague.early.drain(..){overflow|=signals_tx.try_send(early).is_err();}
                                        colleague.signals=Some(signals_tx);
                                        if overflow {Err("Colleague signaling queue overflowed".to_owned())}
                                        else {remote_peer::start(remote_peer::Config{state:state.clone(),generation,session,nonce:binding,host:true,login_window:session_platform::login_window_mode(),remote_key:colleague.key,identity:identity.clone(),source:colleague.source.clone(),#[cfg(target_os = "linux")] portal:colleague.portal.clone(),confirm:colleague.confirm,control:colleague.control,clipboard:false,files:false,chat:false,recording:false,consent_id:colleague.consent_id,consent_cancel:colleague.consent_cancel.clone(),consent_started:colleague.started_at,session_cancel:colleague.cancel.clone(),account_cancel:cancel.clone(),signals:signals_rx,commands:tx.clone(),video:video.clone(),ice_servers,relay_only:service.relay_only,authorization:broker_lease.clone(),peer_expires:colleague.expires.clone(),mirror:true}).map(|()|None)}
                                    },
                                    _=>Err("Invalid relay configuration for the colleague".to_owned()),
                                }
                            },
                            ServerMessage::Signal{payload,..}=>{
                                let colleague=&mut joined[index];
                                if colleague.cancel.load(Ordering::Acquire){Ok(None)}
                                else{match colleague.signals.as_ref(){
                                    Some(signals)=>signals.try_send(payload).map(|()|None).map_err(|_|"Colleague signaling queue overflowed".to_owned()),
                                    None if colleague.nonce.is_some() && colleague.early.len()<32=>{colleague.early.push(payload);Ok(None)},
                                    None=>Err("Colleague media negotiation is unavailable".to_owned()),
                                }}
                            },
                            ServerMessage::PeerRenewed{identity:renewed,..}=>{
                                let colleague=&mut joined[index];
                                match (peer_identity(&renewed,colleague.remote,&principal,true,&roots),colleague.identity.as_ref()) {
                                    (Ok(key),Some(previous)) if key==colleague.key && same_identity(previous,&renewed) => {
                                        if renewed.issued_at>=previous.issued_at && renewed.expires_at>=previous.expires_at{colleague.expires.store(renewed.expires_at,Ordering::Release);colleague.identity=Some(renewed);}
                                        Ok(None)
                                    },
                                    _=>Err("The colleague's account or device changed".to_owned()),
                                }
                            },
                            _=>Ok(None),
                        };
                        let outcome=match outcome {
                            Ok(Some(session))=>{send(&mut socket,ClientMessage::Ice{session}).await?;Ok(())},
                            Ok(None)=>Ok(()),
                            Err(error)=>Err(error),
                        };
                        if let Err(error)=outcome{
                            if index<joined.len(){
                                let mut gone=joined.remove(index);
                                let session=gone.id;
                                if gone.take_close(){send(&mut socket,ClientMessage::Close{session}).await?;}
                            }
                            eprintln!("colleague session stopped: {error}");
                        }
                        let views=colleague_views(&joined);
                        state.change(generation,|status|status.colleagues=views).await;
                        continue;
                    }
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
                        ServerMessage::Presence{online}=>{
                            // Several parts of the window ask about different devices: keep what the others learned.
                            let asked=presence_asked.pop_front().unwrap_or_default();
                            state.change(generation,|status|{
                                status.online.retain(|id|!asked.contains(id) && !online.contains(id));
                                status.online.extend(online);
                                status.online.sort_unstable();
                                if status.online.len()>256{status.online.clear();}
                            }).await;
                        },
                        ServerMessage::Agent{command}=>{
                            if let Some(command)=command {
                                let unattended=state.status().await.unattended_enabled;
                                crate::agent::accept(command,unattended);
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
                        ServerMessage::Requested{session}=>{let (remote,key,_,joined_request)=outgoing.take().ok_or("Unexpected outgoing invitation")?;peer=Some(Peer{id:session,remote,host:false,key,source:String::new(),#[cfg(target_os = "linux")] portal:None,confirm:false,control:false,clipboard:false,files:false,chat:false,recording:false,close_sent:false,consent_cancel:Arc::new(AtomicBool::new(false)),cancel:Arc::new(AtomicBool::new(false)),signals:None,early:Vec::new(),nonce:None,identity:None,expires:Arc::new(AtomicU64::new(0)),relay_expires:0,restart_pending:None,rotated_at:std::time::Instant::now(),started_at:std::time::Instant::now(),local_consent:None,accepted_at:None,consent_id:Uuid::new_v4(),joined:joined_request,switched:false,reversed:false});peer.as_ref().ok_or("Invitation state unavailable")?.track(state,generation).await?;},
                        ServerMessage::Consent{session,requester_connection,requester,requester_identity,unattended,joining:Some(join),..}=>{
                            // A colleague of the technician asks to join the running session.
                            let primary=peer.as_ref().filter(|active|active.host && active.id==join.session && active.remote==join.inviter_connection && active.signals.is_some() && !active.cancel.load(Ordering::Acquire));
                            let eligible=!crate::service_config::controller_only() && joined.len()<2 && requester_connection!=join.inviter_connection && !joined.iter().any(|colleague|colleague.remote==requester_connection) && (unattended || !own_ticket.unattended_host_only());
                            let checked=match (primary,requester_identity,eligible) {
                                (Some(active),Some(ticket),true) if ticket.identity.profile==*requester.as_bytes() => peer_identity(&ticket,requester_connection,&principal,true,&roots).ok().map(|key|(key,ticket,active.source.clone(),active.confirm,active.control,#[cfg(target_os = "linux")] active.portal.clone())),
                                _=>None,
                            };
                            #[cfg(target_os = "linux")]
                            let Some((key,ticket,source,confirm,primary_control,portal))=checked else {send(&mut socket,ClientMessage::Decide{session,accept:false}).await?;continue;};
                            #[cfg(not(target_os = "linux"))]
                            let Some((key,ticket,source,confirm,primary_control))=checked else {send(&mut socket,ClientMessage::Decide{session,accept:false}).await?;continue;};
                            let expires=Arc::new(AtomicU64::new(ticket.expires_at));
                            let mut colleague=Peer{id:session,remote:requester_connection,host:true,key,source,#[cfg(target_os = "linux")] portal,confirm,control:false,clipboard:false,files:false,chat:false,recording:false,close_sent:false,consent_cancel:Arc::new(AtomicBool::new(false)),cancel:Arc::new(AtomicBool::new(false)),signals:None,early:Vec::new(),nonce:None,identity:Some(ticket),expires,relay_expires:0,restart_pending:None,rotated_at:std::time::Instant::now(),started_at:std::time::Instant::now(),local_consent:None,accepted_at:None,consent_id:Uuid::new_v4(),joined:true,switched:false,reversed:false};
                            if unattended && !unattended_join_allowed(&state.status().await) {
                                send(&mut socket,ClientMessage::Decide{session,accept:false}).await?;continue;
                            }
                            if unattended {
                                // Nobody at the computer: the company's rules for unattended access decide
                                // (checked by the connection service); keyboard and mouse as for the first technician.
                                colleague.local_consent=Some(true);colleague.accepted_at=Some(std::time::Instant::now());colleague.control=join.control && primary_control;
                                send(&mut socket,ClientMessage::Decide{session,accept:true}).await?;
                                joined.push(colleague);
                                let views=colleague_views(&joined);
                                state.change(generation,|status|status.colleagues=views).await;
                            } else {
                                joined.push(colleague);
                                let views=colleague_views(&joined);
                                state.change(generation,|status|{status.colleagues=views;status.join_request=Some(JoinRequest{session,connection:requester_connection,inviter_connection:join.inviter_connection,control:join.control,unattended});}).await;
                            }
                        },
                        ServerMessage::Consent{session,requester_connection,requester_identity,switched:Some(from),..}=>{
                            // The sides of a running session were switched: this computer is to be shown.
                            let memory=switch_memory.take().filter(|memory|memory.previous==from.previous && memory.remote==requester_connection && memory.at.elapsed()<Duration::from_secs(30));
                            let verified=match (&memory,requester_identity) {
                                (Some(memory),Some(ticket)) if !crate::service_config::controller_only() && peer.is_none() && outgoing.is_none() => switched_identity(&ticket,requester_connection,&memory.identity,&roots).ok().filter(|key|*key==memory.key).map(|key|(key,ticket)),
                                _=>None,
                            };
                            let Some((key,ticket))=verified else {send(&mut socket,ClientMessage::Decide{session,accept:false}).await?;continue;};
                            let memory=memory.ok_or("Switch state unavailable")?;
                            let pressed=from.by==enrolled.connection_id && switch_pressed.take().is_some_and(|(previous,_)|previous==from.previous);
                            let expires=Arc::new(AtomicU64::new(ticket.expires_at));
                            peer=Some(Peer{id:session,remote:requester_connection,host:true,key,source:String::new(),#[cfg(target_os = "linux")] portal:None,confirm:false,control:false,clipboard:false,files:false,chat:false,recording:false,close_sent:false,consent_cancel:Arc::new(AtomicBool::new(false)),cancel:Arc::new(AtomicBool::new(false)),signals:None,early:Vec::new(),nonce:None,identity:Some(ticket),expires,relay_expires:0,restart_pending:None,rotated_at:std::time::Instant::now(),started_at:std::time::Instant::now(),local_consent:None,accepted_at:None,consent_id:Uuid::new_v4(),joined:false,switched:true,reversed:!memory.reversed});
                            peer.as_ref().ok_or("Invitation state unavailable")?.track(state,generation).await?;
                            state.change(generation,|status|{status.phase="incoming".into();status.message="The other side asks to switch sides: they would see this screen. Accept or Reject".into();status.peer=Some(requester_connection);status.requester=None;status.sharing=true;status.unattended_session=false;}).await;
                            // Switching back to the computer that was helped restores what its user allowed;
                            // pressing "Switch sides" here is the consent to show this screen (view only).
                            let automatic=match (memory.grants,pressed) {
                                (Some(grants),_)=>Some(Decision{session,accept:true,source:grants.source,confirm:grants.confirm,control:grants.control,clipboard:grants.clipboard,files:grants.files,chat:grants.chat,recording:grants.recording}),
                                (None,true)=>session_platform::main_display_source().map(|source|Decision{session,accept:true,source,confirm:true,control:false,clipboard:false,files:false,chat:false,recording:false}),
                                (None,false)=>None,
                            };
                            if let Some(decision)=automatic && state.decide(decision).await.is_err() {
                                state.change(generation,|status|status.message="Choose what to show, then Accept to switch sides".into()).await;
                            }
                        },
                        ServerMessage::Switched{previous,session,host}=>{
                            // The sides were switched: this app now sees the other computer.
                            let memory=switch_memory.as_ref().filter(|memory|memory.previous==previous && memory.remote==host && memory.at.elapsed()<Duration::from_secs(30));
                            let Some(memory)=memory.filter(|_|peer.is_none() && outgoing.is_none()) else {send(&mut socket,ClientMessage::Close{session}).await?;continue;};
                            switch_pressed=None;
                            let expires=Arc::new(AtomicU64::new(memory.identity.expires_at));
                            peer=Some(Peer{id:session,remote:host,host:false,key:memory.key,source:String::new(),#[cfg(target_os = "linux")] portal:None,confirm:false,control:false,clipboard:false,files:false,chat:false,recording:false,close_sent:false,consent_cancel:Arc::new(AtomicBool::new(false)),cancel:Arc::new(AtomicBool::new(false)),signals:None,early:Vec::new(),nonce:None,identity:Some(memory.identity.clone()),expires,relay_expires:0,restart_pending:None,rotated_at:std::time::Instant::now(),started_at:std::time::Instant::now(),local_consent:None,accepted_at:None,consent_id:Uuid::new_v4(),joined:false,switched:true,reversed:!memory.reversed});
                            peer.as_ref().ok_or("Invitation state unavailable")?.track(state,generation).await?;
                            state.change(generation,|status|{status.phase="outgoing".into();status.message="Switching sides: waiting for the other computer's picture".into();status.peer=Some(host);status.sharing=false;}).await;
                        },
                        ServerMessage::Invited{invitation,host,inviter,inviter_connection,control,unattended,expires_in}=>{
                            let now=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_|"System clock is unavailable")?.as_secs();
                            let expires_at=now+u64::from(expires_in.min(300));
                            if invitations.len()>=4{invitations.remove(0);}
                            invitations.push((invitation,host,unattended,expires_at));
                            state.change(generation,|status|{
                                if status.invitations.len()>=4{status.invitations.remove(0);}
                                status.invitations.push(InvitationView{invitation,host,inviter,inviter_connection,control,unattended,expires_at});
                            }).await;
                        },
                        ServerMessage::InviteStatus{delivered,..}=>{
                            invite_pending=false;
                            state.change(generation,|status|status.invite_message=Some(if delivered==0{"Your colleague is not online in Mixel Remote right now".into()}else{"Invitation sent: your colleague joins when they accept and the person at the computer allows it".into()})).await;
                        },
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
                            peer=Some(Peer{id:session,remote:requester_connection,host:true,key,source:String::new(),#[cfg(target_os = "linux")] portal:None,confirm:false,control:false,clipboard:false,files:false,chat:false,recording:false,close_sent:false,consent_cancel:Arc::new(AtomicBool::new(false)),cancel:Arc::new(AtomicBool::new(false)),signals:None,early:Vec::new(),nonce:None,identity:Some(ticket),expires,relay_expires:0,restart_pending:None,rotated_at:std::time::Instant::now(),started_at:std::time::Instant::now(),local_consent:None,accepted_at:None,consent_id:Uuid::new_v4(),joined:false,switched:false,reversed:false});
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
                            // A device-only host (a phone in Quick Support) may accept an attended request:
                            // the connection service lets it through only after the device opted in, and the
                            // person at the device decides about every session.
                            let (local,remote)=if active.host{(&host,&controller)}else{(&controller,&host)};
                            let now=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_|"System clock is unavailable")?.as_secs();
                            local.verify(&roots,now).map_err(|_|"The local session identity is invalid or expired")?;
                            if !same_identity(local,&own_ticket){return Err("The accepted session does not belong to this account and device".into());}
                            let checked=if active.switched{switched_identity(remote,active.remote,active.identity.as_ref().ok_or("The other side was not verified")?,&roots)?}else{peer_identity(remote,active.remote,&principal,active.host,&roots)?};
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
                            let (signals_tx,signals_rx)=mpsc::channel(32);for early in active.early.drain(..){signals_tx.try_send(early).map_err(|_|"Peer signaling queue overflowed")?;}active.signals=Some(signals_tx);
                            state.finish_previous_media().await?;
                            if active.cancel.load(Ordering::Acquire) || cancel.load(Ordering::Acquire){if active.take_close(){send(&mut socket,ClientMessage::Close{session}).await?;}continue;}
                            remote_peer::start(remote_peer::Config{state:state.clone(),generation,session,nonce,host:active.host,login_window:session_platform::login_window_mode(),remote_key:active.key,identity:identity.clone(),source:active.source.clone(),#[cfg(target_os = "linux")] portal:active.portal.clone(),confirm:active.confirm,control:active.control,clipboard:active.clipboard,files:active.files,chat:active.chat,recording:active.recording,consent_id:active.consent_id,consent_cancel:active.consent_cancel.clone(),consent_started:active.started_at,session_cancel:active.cancel.clone(),account_cancel:cancel.clone(),signals:signals_rx,commands:tx.clone(),video:video.clone(),ice_servers,relay_only:service.relay_only,authorization:broker_lease.clone(),peer_expires:active.expires.clone(),mirror:false})?;
                        },
                        ServerMessage::PeerRenewed{session,identity:renewed}=>{
                            let active=peer.as_mut().filter(|peer|peer.id==session).ok_or("Unexpected peer authorization renewal")?;
                            let previous=active.identity.as_ref().ok_or("Peer identity has not been authenticated")?;
                            let key=if active.switched{switched_identity(&renewed,active.remote,previous,&roots)?}else{peer_identity(&renewed,active.remote,&principal,active.host,&roots)?};
                            if key!=active.key || !same_identity(previous,&renewed){return Err("Peer account, enrollment or authorization changed".into());}
                            if renewed.issued_at>=previous.issued_at && renewed.expires_at>=previous.expires_at{active.expires.store(renewed.expires_at,Ordering::Release);active.identity=Some(renewed);}
                        },
                        ServerMessage::Signal{session,payload}=>{let active=peer.as_ref().filter(|peer|peer.id==session).ok_or("Unexpected peer signaling")?;if active.cancel.load(Ordering::Acquire){continue;}match active.signals.as_ref(){Some(signals)=>signals.try_send(payload).map_err(|_|"Peer signaling queue overflowed")?,None if active.nonce.is_some() && active.early.len()<32=>{/* accepted, relay configuration still on its way: keep for the media task */let active=peer.as_mut().ok_or("Unexpected peer signaling")?;active.early.push(payload);},None=>return Err("Media negotiation is unavailable".into())}},
                        ServerMessage::Closed{session,reason:signaling_server::protocol::CloseReason::Switched} if peer.as_ref().is_some_and(|active|active.id==session && active.identity.is_some())=>{
                            let ended=peer.take().ok_or("Session state unavailable")?;
                            // Grants of the person at this computer survive a switch and its switch back.
                            let grants=if ended.host && !ended.switched {
                                Some(HostGrants{source:ended.source.clone(),confirm:ended.confirm,control:ended.control,clipboard:ended.clipboard,files:ended.files,chat:ended.chat,recording:ended.recording})
                            } else {
                                switch_memory.take().filter(|memory|memory.key==ended.key).and_then(|memory|memory.grants)
                            };
                            switch_memory=Some(SwitchMemory{previous:session,remote:ended.remote,key:ended.key,identity:ended.identity.clone().ok_or("Peer identity unavailable")?,grants,reversed:ended.reversed,at:std::time::Instant::now()});
                            state.change(generation,|status|{status.phase="ready".into();status.message="Switching sides".into();status.session=None;status.peer=Some(ended.remote);status.requester=None;}).await;
                        },
                        ServerMessage::Closed{session,reason}=>{if peer.as_ref().is_some_and(|peer|peer.id==session){peer=None;state.change(generation,|status|{status.phase="ready".into();status.message=format!("Session closed: {reason:?}");status.session=None;status.peer=None;status.requester=None;}).await;}},
                        ServerMessage::Error{code}=>{
                            if outgoing.take().is_some(){state.change(generation,|status|{status.phase="ready".into();status.peer=None;status.message=request_failure_message(code).into();}).await;}
                            else if std::mem::take(&mut invite_pending){
                                state.change(generation,|status|status.invite_message=Some(match code {ErrorCode::Busy=>"No more colleagues can join this session right now",ErrorCode::Unavailable=>"The computer you are helping cannot receive colleagues (it needs a newer Mixel Remote)",_=>"The invitation could not be sent"}.into())).await;
                            }
                            else if switch_pressed.take().is_some(){
                                state.change(generation,|status|status.message=match code {ErrorCode::Busy=>"Sides cannot be switched while a colleague is connected",ErrorCode::Unavailable=>"The other side needs a newer Mixel Remote to switch sides",_=>"Sides could not be switched"}.into()).await;
                            }
                            else if code==ErrorCode::Invalid && capabilities_sent.elapsed()<Duration::from_secs(15) && peer.is_none(){
                                // An older connection service: no colleagues, no switching sides.
                            }
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

/// A colleague's join of an unattended session is accepted without asking only while this computer
/// itself allows unattended access and the running session is an unattended one. The broker's
/// `unattended` flag alone is not enough: it must never turn an attended session (someone at the
/// computer decides) into one a colleague enters silently.
fn unattended_join_allowed(status: &Status) -> bool {
    status.unattended_enabled && status.unattended_session && !status.code_required
}

#[cfg(test)]
mod join_tests {
    use super::*;
    #[test]
    fn an_unattended_join_needs_local_unattended_access_and_an_unattended_session() {
        let mut status = RemoteSession::default().0.try_lock().unwrap().status.clone();
        status.unattended_enabled = true;
        status.unattended_session = true;
        assert!(unattended_join_allowed(&status));
        for change in [
            (|s: &mut Status| s.unattended_session = false) as fn(&mut Status),
            |s: &mut Status| s.unattended_enabled = false,
            |s: &mut Status| s.code_required = true,
        ] {
            let mut refused = status.clone();
            change(&mut refused);
            assert!(!unattended_join_allowed(&refused));
        }
    }
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
            early: Vec::new(),
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
            joined: false,
            switched: false,
            reversed: false,
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
                false,
                false,
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
                false,
                false,
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
                false,
                false,
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
                false,
                false,
            )
            .await
            .unwrap();
        assert!(remote.stop_session(Uuid::new_v4()).await.is_err());
        assert_eq!(remote.0.lock().await.file_permission, Some(true));
        assert!(remote.file_grant(0, new, false).await.is_err());
        assert!(root.load(Ordering::Acquire));
    }
    #[tokio::test]
    async fn the_switched_direction_is_current_and_blocks_invitations_from_the_viewing_side() {
        let remote = RemoteSession::default();
        let (tx, _rx) = mpsc::channel(4);
        let track = |session: Uuid, reversed: bool| {
            let remote = remote.clone();
            async move {
                remote
                    .track(0, session, Arc::new(AtomicBool::new(false)), Uuid::new_v4(), Arc::new(AtomicBool::new(false)), false, reversed)
                    .await
                    .unwrap();
                let mut inner = remote.0.lock().await;
                inner.status.phase = "active".into();
                inner.status.sharing = false;
                inner.status.peer = Some(111_111_111);
                inner.status.peer_features = vec![JOIN.into()];
            }
        };
        remote.0.lock().await.commands = Some(tx);
        // Switched: the computer that was helped views the technician and may not invite.
        let reversed = Uuid::new_v4();
        track(reversed, true).await;
        assert!(remote.status().await.switched);
        let refused = remote.invite(reversed, None, Some(333_333_333), false).await.unwrap_err();
        assert!(refused.contains("switching back"), "{refused}");
        // Switched back: the direction is the original one again and the technician invites.
        let back = Uuid::new_v4();
        track(back, false).await;
        assert!(!remote.status().await.switched, "the label and rules follow the current direction");
        remote.invite(back, None, Some(333_333_333), false).await.unwrap();
    }
    #[tokio::test]
    async fn a_colleague_view_leaves_the_first_technicians_grants_alone() {
        let remote = RemoteSession::default();
        let primary = Uuid::new_v4();
        remote
            .track(0, primary, Arc::new(AtomicBool::new(false)), Uuid::new_v4(), Arc::new(AtomicBool::new(false)), false, false)
            .await
            .unwrap();
        remote.record_grants(0, primary, false, true, false).await.unwrap();
        // The colleague's session is not this app's session: it neither fails nor changes the grants.
        let colleague = Uuid::new_v4();
        remote.record_grants(0, colleague, true, false, false).await.unwrap();
        assert_eq!(remote.0.lock().await.file_permission, Some(true));
        assert!(remote.0.lock().await.status.files);
        assert!(remote.record_grants(0, colleague, true, true, false).await.is_err(), "no files for a colleague's view");
        assert!(remote.record_grants(0, colleague, true, false, true).await.is_err(), "no recording for a colleague's view");
        // Without the mirror flag another session is still refused, as before.
        assert!(remote.record_grants(0, colleague, false, false, false).await.is_err());
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
                false,
                false,
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

    fn ticket(device: u128, profile: u128, tenant: Option<u128>, connection: u32, key: u8, authority: &ed25519_dalek::SigningKey) -> core_protocol::delegation::DeviceTicket {
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
        core_protocol::delegation::DeviceTicket::issue(
            core_protocol::device_login::DeviceLoginChallenge {
                nonce: [1; 32],
                device: *Uuid::from_u128(device).as_bytes(),
                profile: *Uuid::from_u128(profile).as_bytes(),
                tenant: tenant.map(|id| *Uuid::from_u128(id).as_bytes()),
                connection_id: connection,
                generation: 1,
            },
            ed25519_dalek::SigningKey::from_bytes(&[key; 32]).verifying_key(),
            tenant.is_none(),
            now - 5,
            now + 600,
            authority,
        )
        .unwrap()
    }

    #[test]
    fn a_switched_session_accepts_only_the_peer_verified_before() {
        let authority = ed25519_dalek::SigningKey::from_bytes(&[9; 32]);
        let roots = [authority.verifying_key()];
        // A Mixel staff technician and a customer of another company: not allowed to reach each other
        // directly, but the same two devices after a switch.
        let technician = ticket(1, 2, None, 111_111_111, 7, &authority);
        let customer = Principal { user: Uuid::from_u128(5), tenant: Some(Uuid::from_u128(6)), staff: false, expires_at: u64::MAX };
        assert!(peer_identity(&technician, 111_111_111, &customer, true, &roots).is_ok(), "a customer may be helped by staff");
        assert!(peer_identity(&technician, 111_111_111, &customer, false, &roots).is_err(), "but may not open a session to staff on its own");
        let stranger = ticket(3, 4, Some(8), 333_333_333, 8, &authority);
        assert!(peer_identity(&stranger, 333_333_333, &customer, true, &roots).is_err(), "nobody from another company may ask");
        assert_eq!(switched_identity(&technician, 111_111_111, &technician, &roots).unwrap(), ed25519_dalek::SigningKey::from_bytes(&[7; 32]).verifying_key());
        // A different device, another connection ID or a forged authority are refused.
        let other_device = ticket(9, 2, None, 111_111_111, 7, &authority);
        assert!(switched_identity(&other_device, 111_111_111, &technician, &roots).is_err());
        assert!(switched_identity(&technician, 222_222_222, &technician, &roots).is_err());
        let forged = ticket(1, 2, None, 111_111_111, 7, &ed25519_dalek::SigningKey::from_bytes(&[4; 32]));
        assert!(switched_identity(&forged, 111_111_111, &technician, &roots).is_err());
    }

    #[test]
    fn a_colleague_needs_local_consent_and_the_hosts_own_identity() {
        let authority = ed25519_dalek::SigningKey::from_bytes(&[9; 32]);
        let roots = [authority.verifying_key()];
        let own = ticket(10, 11, Some(1), 222_222_222, 3, &authority);
        let principal = Principal { user: Uuid::from_u128(11), tenant: Some(Uuid::from_u128(1)), staff: false, expires_at: u64::MAX };
        let colleague_ticket = ticket(20, 21, Some(1), 123_456_789, 7, &authority);
        let mut colleague = invitation();
        colleague.joined = true;
        colleague.identity = Some(colleague_ticket.clone());
        let session = Uuid::from_u128(1);
        // Not yet allowed by the person at the computer.
        assert!(colleague_accepted(&mut colleague, session, [5; 32], &colleague_ticket, &own, &own, &principal, &roots).is_err());
        colleague.local_consent = Some(true);
        colleague.accepted_at = Some(std::time::Instant::now());
        // The session must name this device as host.
        let someone_else = ticket(30, 31, Some(1), 222_222_222, 4, &authority);
        assert!(colleague_accepted(&mut colleague, session, [5; 32], &colleague_ticket, &someone_else, &own, &principal, &roots).is_err());
        // The colleague's ticket must be the one that asked.
        let impostor = ticket(40, 41, Some(1), 123_456_789, 8, &authority);
        assert!(colleague_accepted(&mut colleague, session, [5; 32], &impostor, &own, &own, &principal, &roots).is_err());
        let binding = colleague_accepted(&mut colleague, session, [5; 32], &colleague_ticket, &own, &own, &principal, &roots).unwrap();
        assert_ne!(binding, [0; 32]);
        assert_eq!(colleague.expires.load(Ordering::Acquire), colleague_ticket.expires_at);
        assert_eq!(colleague_views(&[colleague]).first().map(|view| view.state), Some("connecting"));
    }

}

#[cfg(all(test, any(target_os = "linux", target_os = "windows")))]
#[path = "remote_session_native_test.rs"]
mod remote_session_native_test;
