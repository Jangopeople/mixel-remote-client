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
const ENDING: &str = signaling_server::protocol::FEATURE_ENDING;
const POLICY: &str = signaling_server::protocol::FEATURE_POLICY;
const RESUME: &str = signaling_server::protocol::FEATURE_RESUME;
const SIGNED: &str = signaling_server::protocol::FEATURE_SIGNED;
/// `macos-arm64`, `windows-x64`, `linux-x64`: what the Devices list shows next to the version.
fn app_platform() -> String {
    let arch = match std::env::consts::ARCH {
        "aarch64" => "arm64",
        "x86_64" => "x64",
        other => other,
    };
    let text = format!("{}-{arch}", std::env::consts::OS);
    if text.len() <= 32 && text.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-') { text } else { "unknown".into() }
}

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
    /// Technician: every session this app views, oldest first (up to `max_sessions`). The fields above
    /// (`session`, `phase`, `control`, ...) describe the oldest running one, for apps that know only one.
    pub sessions: Vec<crate::remote_sessions::SessionStatus>,
    /// How many sessions this app may hold at once.
    pub max_sessions: usize,
    /// The connection to the connection service was lost and is being restored; the session itself (its
    /// media connection) is kept and resumes (feature `resume`). The window stays open and says so.
    pub reconnecting: bool,
    /// The computer being helped went away and did not come back within a minute ("Could not reconnect").
    pub reconnect_failed: bool,
    /// D2: this session was opened with a support password and ends by itself (2 hours after it connected).
    /// `ends_at` is the broker's countdown once known (5-minute and 1-minute warnings).
    pub password_session: Option<PasswordSessionView>,
    /// D1: the administrator's setting for unattended access on this device (`device_policy`), when known.
    pub unattended_allowed: Option<bool>,
    #[serde(skip)]
    password_ends: Option<std::time::Instant>,
    #[serde(skip)]
    password_flag: bool,
    #[serde(skip)]
    code_expires_at: Option<std::time::Instant>,
}
/// `remote_status.password_session`.
#[derive(Clone, Serialize)]
pub struct PasswordSessionView {
    /// ISO 8601 UTC, when the broker's limit ends the session (None until known).
    pub ends_at: Option<String>,
    pub seconds_left: Option<u64>,
}
/// Seconds since 1970 as `2026-11-17T12:00:00Z`.
pub(crate) fn iso_utc(seconds: u64) -> String {
    let days = (seconds / 86_400) as i64;
    let rest = seconds % 86_400;
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if month <= 2 { year + 1 } else { year };
    format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z", rest / 3600, rest % 3600 / 60, rest % 60)
}
/// "This support session ends in 5 minutes (support password limit). Ask for a new password to continue."
fn ending_message(seconds: u32) -> String {
    let when = if seconds <= 60 { "1 minute".to_owned() } else { format!("{} minutes", seconds.div_ceil(60)) };
    format!("This support session ends in {when} (support password limit). Ask for a new password to continue.")
}
/// What the recording commands need to know about a viewing session.
pub struct RecordingView {
    pub phase: String,
    pub received: u64,
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
            sessions: Vec::new(),
            max_sessions: crate::remote_sessions::MAX_SESSIONS,
            reconnecting: false,
            reconnect_failed: false,
            password_session: None,
            unattended_allowed: None,
            password_ends: None,
            password_flag: false,
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
        #[cfg(any(target_os = "ios", target_os = "android"))]
        None => {
            let _ = (enrolled, identity);
            Err("Sign in again to connect".into())
        }
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
    /// Shows one technician session in the single-session fields.
    fn mirror(&mut self, view: &crate::remote_sessions::SessionStatus) {
        self.phase = view.phase.clone();
        self.message = view.message.clone();
        self.session = view.session;
        self.consent = view.consent;
        self.peer = Some(view.peer);
        self.received = view.received;
        self.sharing = false;
        self.control = view.control;
        self.clipboard = view.clipboard;
        self.files = view.files;
        self.recording_permitted = view.recording_permitted;
        self.recording = view.recording.clone();
        self.displays = view.displays;
        self.display = view.display;
        self.display_all = view.display_all;
        self.print_jobs = view.print_jobs.clone();
        self.host_actions = view.host_actions.clone();
        self.signin_id = view.signin_id;
        self.host_macs = view.host_macs.clone();
        self.meeting_offer = view.meeting_offer.clone();
        self.joined_session = view.joined_session;
        self.switched = view.switched;
        self.peer_features = view.peer_features.clone();
    }
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

pub enum Command {
    /// Technician: ask for a session. The last field is the session's stable id in the registry.
    Request(u32, Option<SupportCode>, bool, Uuid),
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
    /// Technician: ask the device of a session that says "Could not reconnect" again, for another minute.
    Reconnect(Uuid),
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
    /// Technician: join a colleague's running session (invitation, the session's stable id).
    Join(Uuid, Uuid),
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
    pub fn borrowed(&self) -> core_protocol::input::Input<'_> {
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
pub struct InputCommand {
    pub event: InputEvent,
    pub deadline: std::time::Instant,
    pub reply: tokio::sync::oneshot::Sender<Result<bool, String>>,
}
pub struct ClipboardCommand {
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
    /// Controller: drawings for the host's overlay (view-only sessions too).
    drawing: Option<mpsc::Sender<core_protocol::annotation::Message>>,
    renew_after: Option<std::time::Instant>,
    stopping: bool,
    /// Technician sessions (up to four at once), each with its own state.
    slots: crate::remote_sessions::Registry,
}
impl Inner {
    /// A person's request to go online while this device is online or connecting (and not being
    /// disconnected). The headless hosts (always-on, sign-in screen) register once and keep the
    /// refusal, so a second host process is still reported.
    fn already_online(&self, interactive: bool) -> bool {
        interactive && self.commands.is_some() && !self.stopping
    }
}
/// Lab builds only: transport renewals of this process (the session keeps its ID through them).
#[cfg(feature = "qa-session")]
pub static QA_RENEWALS: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
/// Media owner threads; `true` marks a colleague's (joined) media.
type MediaWorkers = Arc<std::sync::Mutex<Vec<(bool, Uuid, std::thread::JoinHandle<()>)>>>;
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
                drawing: None,
                renew_after: None,
                stopping: false,
                slots: Default::default(),
            })),
            Arc::new(std::sync::Mutex::new(Vec::new())),
            crate::remote_chat::ChatHub::default(),
            crate::remote_recording::RecordingHub::default(),
            crate::remote_files::FileHub::default(),
        )
    }
}
impl RemoteSession {
    pub fn recording_hub(&self) -> crate::remote_recording::RecordingHub {
        self.3.clone()
    }
    pub fn chat_hub(&self) -> crate::remote_chat::ChatHub {
        self.2.clone()
    }

    /// One technician session as the apps see it (by stable id or current session id).
    pub async fn session_view(&self, key: Uuid) -> Option<crate::remote_sessions::SessionStatus> {
        let inner = self.0.lock().await;
        inner.slots.find(key).map(|slot| slot.snapshot())
    }
    /// For recording: the id the recording hub knows (the current session id) and whether this viewing
    /// session is active and has shown a picture (`phase`, pictures received).
    pub async fn recording_view(&self, key: Uuid) -> (Uuid, Option<RecordingView>) {
        {
            let inner = self.0.lock().await;
            if let Some(slot) = inner.slots.find(key) {
                return (slot.view.session.unwrap_or(key), Some(RecordingView { phase: slot.view.phase.clone(), received: slot.view.received }));
            }
        }
        let status = self.status().await;
        if status.session != Some(key) || status.sharing {
            return (key, None);
        }
        (key, Some(RecordingView { phase: status.phase, received: status.received }))
    }
    /// The stable id of a technician session (any id of it in), if it exists.
    async fn slot_id(&self, key: Uuid) -> Option<Uuid> {
        self.0.lock().await.slots.stable(key)
    }
    pub async fn require_chat_session(&self, session: Uuid) -> Result<(), String> {
        let inner = self.0.lock().await;
        if let Some(slot) = inner.slots.find(session) {
            if inner.cancel.load(Ordering::Acquire) || slot.view.phase != "active" || slot.session_cancel.as_ref().is_none_or(|flag| flag.load(Ordering::Acquire)) {
                return Err("Chat requires a current, accepted session".into());
            }
            return Ok(());
        }
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

    pub fn file_hub(&self) -> crate::remote_files::FileHub {
        self.4.clone()
    }
    pub async fn file_grant(
        &self,
        generation: u64,
        session: Uuid,
        allowed: bool,
    ) -> Result<(), String> {
        let mut inner = self.0.lock().await;
        let account_gone = inner.generation != generation || inner.cancel.load(Ordering::Acquire);
        if let Some(slot) = inner.slots.find_mut(session) {
            if account_gone || slot.view.phase == "closing" || slot.ended {
                return Err("The accepted file scope stopped".into());
            }
            if slot.file_permission.is_some_and(|previous| previous != allowed) {
                slot.revoke("The host file grant changed; a fresh local Accept is required");
                return Err("The host file grant changed; a fresh local Accept is required".into());
            }
            slot.file_permission = Some(allowed);
            slot.view.files = allowed;
            return Ok(());
        }
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
    pub async fn record_grants(
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
        if let Some(slot) = inner.slots.by_consent(consent) {
            if !slot.view.files || !matches!(slot.view.phase.as_str(), "active" | "negotiating") || inner.cancel.load(Ordering::Acquire) || slot.cancelled() {
                return Err("The host has not granted active file transfer permission".into());
            }
            let scope = self.4.get(consent)?;
            if !scope.live() {
                return Err("File transfer permission has expired".into());
            }
            return Ok(scope);
        }
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
    /// Waits for the media owners of the technician's previous transport of one session (`Some(slot)`)
    /// or of every technician session (`None`). Other sessions' media keeps running.
    async fn finish_previous_media(&self, slot: Option<Uuid>) -> Result<(), String> {
        let registry = self.1.clone();
        tokio::task::spawn_blocking(move || {
            let mut workers = registry
                .lock()
                .map_err(|_| "Media owner registry is unavailable".to_owned())?;
            let deadline = std::time::Instant::now() + Duration::from_secs(25);
            let mine = |colleague: bool, key: Uuid| !colleague && slot.is_none_or(|slot| slot == key);
            // Colleagues' media keeps running: only the technician's own transport is replaced.
            while workers.iter().any(|(colleague, key, worker)| mine(*colleague, *key) && !worker.is_finished()) {
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
                if mine(workers[index].0, workers[index].1) {
                    workers
                        .swap_remove(index)
                        .2
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
    /// Starts a media owner thread. `slot` names the technician session it belongs to (the session id for
    /// a host or a colleague), so a session's transport is replaced without waiting for the others.
    pub fn spawn_media(
        &self,
        cancel: &AtomicBool,
        colleague: bool,
        slot: Uuid,
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
            if workers[index].2.is_finished() {
                workers
                    .swap_remove(index)
                    .2
                    .join()
                    .map_err(|_| "A prior media owner failed")?;
            } else {
                index += 1;
            }
        }
        if !colleague && workers.iter().any(|(kind, key, _)| !*kind && *key == slot) {
            return Err("A prior media owner is still stopping".into());
        }
        let running = workers.iter().filter(|(kind, _, _)| *kind == colleague).count();
        // Four sessions, each possibly still cleaning up its replaced transport.
        if running >= if colleague { 4 } else { 2 * crate::remote_sessions::MAX_SESSIONS } {
            return Err("A prior media owner is still stopping".into());
        }
        workers.push((
            colleague,
            slot,
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
        let mut inner = self.0.lock().await;
        if !crate::update_gate::phase_busy(&inner.status.phase) {
            // Nothing running: the countdown of an ended password session must not reach the next one.
            inner.status.password_flag = false;
            inner.status.password_ends = None;
        }
        let mut status = inner.status.clone();
        if status.phase == "active" && !status.sharing {
            status.recording = status.session.and_then(|session| self.recording_hub().view(session));
        }
        // Technician sessions: each one's own state; a stopped session shows as closing.
        let account_stopped = inner.cancel.load(Ordering::Acquire);
        status.sessions = inner
            .slots
            .iter()
            .map(|slot| {
                let mut view = slot.snapshot();
                if view.phase == "active" {
                    view.recording = view.session.and_then(|session| self.recording_hub().view(session));
                }
                view.password_session = if view.password_flag || view.password_ends.is_some() {
                    let left = view.password_ends.map(|end| end.saturating_duration_since(std::time::Instant::now()).as_secs());
                    let unix = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |time| time.as_secs());
                    Some(PasswordSessionView { ends_at: left.map(|left| iso_utc(unix + left)), seconds_left: left })
                } else {
                    None
                };
                if (account_stopped || slot.cancelled()) && matches!(view.phase.as_str(), "active" | "negotiating") {
                    view.phase = "closing".into();
                    view.message = "The session stopped; cleaning up the connection".into();
                    view.control = false;
                    view.clipboard = false;
                    view.files = false;
                    view.recording_permitted = false;
                    view.recording = None;
                    slot.video.clear();
                }
                view
            })
            .collect();
        if status.session.is_none() && status.phase == "ready" {
            let primary = inner.slots.primary().map(|slot| slot.view.id);
            if let Some(view) = primary.and_then(|id| status.sessions.iter().find(|view| view.id == id)).cloned() {
                status.mirror(&view);
            }
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
        crate::update_gate::observe_phase(&status.phase);
        if crate::update_gate::phase_busy(&status.phase) && (status.password_flag || status.password_ends.is_some()) {
            let now = std::time::Instant::now();
            let left = status.password_ends.map(|end| end.saturating_duration_since(now).as_secs());
            let unix = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |time| time.as_secs());
            status.password_session = Some(PasswordSessionView { ends_at: left.map(|left| iso_utc(unix + left)), seconds_left: left });
        } else {
            status.password_session = None;
        }
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
        let mut inner = self.0.lock().await;
        // The apps ask to go online from two places: by themselves once a set-up computer is signed
        // in, and again right after "Add this computer" or the Go online button. Whichever comes
        // second finds the device online or on its way: that is what was asked for, not a failure
        // (it used to be reported as "already registered or connecting" although all was well).
        if inner.already_online(matches!(auth, RegistrationAuth::Interactive(_))) {
            return Ok(());
        }
        if inner.commands.is_some() || inner.stopping {
            return Err("This device is already registered or connecting".into());
        }
        identity.public_key()?;
        let config = crate::service_config::ServiceConfig::load()?;
        let url: reqwest::Url = config
            .signaling_url
            .parse()
            .map_err(|_| "Invalid connection-service configuration")?;
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
        // Unattended access is on by default, unless the owner turned it off on this computer:
        // that choice is kept across restarts (2.0.5-2.0.14 switched it back on at every start).
        let default_unattended = if !crate::service_config::controller_only() && !unattended_choice::turned_off() {
            session_platform::main_display_source()
                .map(|source| UnattendedSource {
                    source,
                    control: session_platform::input_access(),
                })
                .filter(UnattendedSource::capture_allowed)
        } else {
            None
        };
        inner.status = Status {
            phase: "connecting".into(),
            message: "Verifying enrolled identity and connecting securely".into(),
            unattended: default_unattended,
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
                inner.drawing = None;
                inner.clipboard = None;
                if let Some(flag) = inner.session_cancel.take() {
                    flag.store(true, Ordering::Release);
                }
                Self::end_all_slots(&mut inner, "Disconnected; remote media has stopped");
                let kept_unattended = inner.status.unattended.clone();
                let kept_code = (inner.status.code_required, inner.status.support_code.clone());
                inner.status = Status {
                    phase: if result.is_ok() { "offline" } else { "error" }.into(),
                    message: result
                        .err()
                        .unwrap_or_else(|| "Disconnected; remote media has stopped".into()),
                    unattended: kept_unattended,
                    code_required: kept_code.0,
                    support_code: kept_code.1,
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
        inner.drawing = None;
        inner.clipboard = None;
        if let Some(flag) = inner.session_cancel.take() {
            flag.store(true, Ordering::Release);
        }
        Self::end_all_slots(&mut inner, "Disconnected; sharing and retained video revoked");
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
            while workers.iter().any(|(_, _, worker)| !worker.is_finished()) {
                if std::time::Instant::now() >= deadline {
                    return Err(
                        "A media owner has not finished cleanup; quitting was postponed".into(),
                    );
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            for (_, _, worker) in workers.drain(..) {
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
            if inner.status.phase != "ready" {
                return Err("Make this computer available before enabling unattended sharing".into());
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
            unattended_choice::remember(false);
            inner.status.unattended = Some(config);
            inner.status.unattended_pending = Some(UnattendedChange::Enabling);
            return Ok(());
        }

        unattended_choice::remember(true);
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
        if inner.status.phase != "ready" {
            return Err("Make this computer available before changing its one-time password".into());
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

    /// Starts a technician session to `host`; the answer is the session's stable id (it also shows in
    /// `status.sessions` at once). Up to four run side by side; a fifth fails with the typed error
    /// `too_many_sessions`.
    pub async fn request(
        &self,
        host: u32,
        password: Option<String>,
        unattended: bool,
    ) -> Result<Uuid, String> {
        let code = password
            .map(|value| {
                SupportCode::parse(value).ok_or("Enter the 12-character one-time password")
            })
            .transpose()?;
        let unattended = unattended && code.is_none();
        if !(100_000_000..=999_999_999).contains(&host) {
            return Err("Enter a nine-digit Mixel connection ID".into());
        }
        let mut inner = self.0.lock().await;
        if inner.status.phase != "ready" || inner.status.host == Some(host) {
            return Err(
                "The device must be Ready and the destination must be another device".into(),
            );
        }
        let commands = inner.commands.clone().ok_or("Connect this device first")?;
        let slot = inner.slots.add(host)?;
        if commands.try_send(Command::Request(host, code, unattended, slot)).is_err() {
            inner.slots.remove(slot);
            return Err("The connection service is busy".into());
        }
        Ok(slot)
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
            && ((!(inner.status.unattended_enabled || inner.status.code_required))
                || inner.status.unattended.as_ref().is_some_and(|config| {
                    source != config.source
                        || (!control && config.control)
                        || (control && !config.control && !session_platform::input_access())
                })
                || (session_platform::login_window_mode()
                    && (clipboard || files || chat || recording)))
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
    /// Ends one session. For a technician session `session` may be its stable id or its current id;
    /// the other sessions are not touched.
    pub async fn close(&self, session: Uuid) -> Result<(), String> {
        let mut inner = self.0.lock().await;
        if inner.slots.contains(session) {
            return Self::close_slot(&mut inner, session);
        }
        Self::close_locked(&mut inner, session)
    }
    fn close_slot(inner: &mut Inner, key: Uuid) -> Result<(), String> {
        let slot = inner.slots.find_mut(key).ok_or("This session has already closed")?;
        if slot.ended {
            return Err("This session has already closed".into());
        }
        slot.revoke("Stopping the session; video delivery revoked");
        let target = slot.view.session.unwrap_or(slot.view.id);
        let commands = inner.commands.as_ref().ok_or("Sharing stopped locally; the connection service is disconnected")?;
        commands
            .try_send(Command::Close(target))
            .map_err(|_| "Sharing stopped locally; the connection service is busy".into())
    }
    /// A session that says "Could not reconnect": ask its computer again for another minute.
    pub async fn reconnect(&self, session: Uuid) -> Result<(), String> {
        let mut inner = self.0.lock().await;
        let slot = inner.slots.find_mut(session).ok_or("This session has already closed")?;
        if slot.ended || !slot.view.reconnect_failed {
            return Err("This session is not waiting to be asked again".into());
        }
        let commands = inner.commands.as_ref().ok_or("The connection service is disconnected")?;
        commands
            .try_send(Command::Reconnect(session))
            .map_err(|_| "The connection service is busy; try again".into())
    }
    pub async fn stop_session(&self, consent: Uuid) -> Result<(), String> {
        let mut inner = self.0.lock().await;
        if let Some(id) = inner.slots.by_consent(consent).map(|slot| slot.view.id) {
            return Self::close_slot(&mut inner, id);
        }
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
        inner.drawing = None;
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
        if let Some(slot) = inner.slots.find_mut(session) {
            let Some(current) = slot.view.session.filter(|_| slot.live()) else {
                return Err("Only an active remote viewer can renew this connection".into());
            };
            if slot.renew_after.is_some_and(|deadline| std::time::Instant::now() < deadline) {
                return Err("Wait 30 seconds between connection renewals".into());
            }
            slot.view.message = "Renewing this accepted connection".into();
            return inner
                .commands
                .as_ref()
                .ok_or("The device is disconnected")?
                .try_send(Command::Restart(current))
                .map_err(|_| "The connection service is busy".into());
        }
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
        if let Some(slot) = inner.slots.find(session) {
            if !slot.live() || slot.view.joined_session || !slot.view.peer_features.iter().any(|name| name == JOIN) {
                return Err("Colleagues can be invited into a running session you started".into());
            }
            if device == Some(slot.view.peer) {
                return Err("This is the computer you are helping".into());
            }
            if slot.view.switched {
                return Err("Colleagues can be invited after switching back: right now you are viewing the technician's screen".into());
            }
            let current = slot.view.session.ok_or("Colleagues can be invited into a running session you started")?;
            inner
                .commands
                .as_ref()
                .ok_or("The device is disconnected")?
                .try_send(Command::Invite { session: current, user, device, control })
                .map_err(|_| "The connection service is busy")?;
            inner.status.invite_message = Some("Sending the invitation".into());
            return Ok(());
        }
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
        let commands = inner.commands.clone().ok_or("Connect this device first")?;
        let slot = inner.slots.add(offer.host)?;
        if commands.try_send(Command::Join(invitation, slot)).is_err() {
            inner.slots.remove(slot);
            return Err("The connection service is busy".into());
        }
        inner.status.invitations.retain(|other| other.invitation != invitation);
        if let Some(slot) = inner.slots.find_mut(slot) {
            slot.view.joined_session = true;
            slot.view.message = "Joining your colleague's session".into();
        }
        Ok(())
    }
    /// Removes an invitation without joining.
    pub async fn dismiss_invitation(&self, invitation: Uuid) {
        self.0.lock().await.status.invitations.retain(|offer| offer.invitation != invitation);
    }
    /// Either side: switches sides in the running session.
    pub async fn switch_sides(&self, session: Uuid) -> Result<(), String> {
        let mut inner = self.0.lock().await;
        if let Some(slot) = inner.slots.find(session) {
            // Switching moves this computer into the helped side: only with one session open.
            if inner.slots.len() != 1 {
                return Err("Close the other sessions before switching sides".into());
            }
            if !slot.live() || slot.view.joined_session || !slot.view.peer_features.iter().any(|name| name == SWITCH) {
                return Err("Sides can be switched in a running session between two people".into());
            }
            let current = slot.view.session.ok_or("Sides can be switched in a running session between two people")?;
            let commands = inner.commands.as_ref().ok_or("The device is disconnected")?;
            commands.try_send(Command::SwitchSides(current)).map_err(|_| "The connection service is busy")?;
            if let Some(slot) = inner.slots.find_mut(session) {
                slot.view.message = "Switching sides".into();
            }
            return Ok(());
        }
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
    pub async fn set_peer_features(&self, generation: u64, session: Uuid, features: Vec<String>) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation && !inner.cancel.load(Ordering::Acquire)
            && let Some(slot) = inner.slots.find_mut(session)
        {
            if !slot.ended {
                slot.view.peer_features = features;
            }
            return;
        }
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
        let fast_ack = !cfg!(test)
            && matches!(
                event,
                InputEvent::Pointer { .. }
                    | InputEvent::Wheel { .. }
                    | InputEvent::ButtonClick { .. }
                    | InputEvent::Key { .. }
            );
        let (tx, rx) = tokio::sync::oneshot::channel();
        {
            let inner = self.0.lock().await;
            if let Some(slot) = inner.slots.find(session) {
                if !slot.live() || !slot.view.control || inner.cancel.load(Ordering::Acquire) {
                    return Ok(false);
                }
                match slot
                    .input
                    .as_ref()
                    .ok_or("The input transport is closed")?
                    .try_send(InputCommand {
                        event,
                        deadline: std::time::Instant::now() + Duration::from_secs(2),
                        reply: tx,
                    }) {
                    Ok(()) => {}
                    // The transport before a renewal ended ahead of the renewed one: not delivered.
                    Err(mpsc::error::TrySendError::Closed(_)) => return Ok(false),
                    Err(mpsc::error::TrySendError::Full(_)) => return Err("The input transport is busy".into()),
                }
            } else {
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
                match inner
                    .input
                    .as_ref()
                    .ok_or("The input transport is closed")?
                    .try_send(InputCommand {
                        event,
                        deadline: std::time::Instant::now() + Duration::from_secs(2),
                        reply: tx,
                    }) {
                    Ok(()) => {}
                    // The transport before a renewal ended ahead of the renewed one (a host of an
                    // older version stops at once): not delivered, as while the session negotiates.
                    Err(mpsc::error::TrySendError::Closed(_)) => return Ok(false),
                    Err(mpsc::error::TrySendError::Full(_)) => return Err("The input transport is busy".into()),
                }
            }
        }
        if fast_ack {
            return Ok(true);
        }
        let result = tokio::time::timeout(Duration::from_secs(3), rx)
            .await
            .map_err(|_| "Input acknowledgment timed out")?
            .map_err(|_| "Input transport closed")?;
        if let (Some(index), Ok(true)) = (selected, &result) {
            let mut inner = self.0.lock().await;
            if let Some(slot) = inner.slots.find_mut(session) {
                slot.view.display = index;
            } else if inner.status.session == Some(session) {
                inner.status.display = index;
            }
        }
        result
    }
    /// Controller: where drawings for this session go (the host announced drawing).
    pub async fn set_drawing(&self, generation: u64, session: Uuid, sender: mpsc::Sender<core_protocol::annotation::Message>) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation && !inner.cancel.load(Ordering::Acquire)
            && let Some(slot) = inner.slots.find_mut(session)
        {
            if !slot.cancelled() && slot.view.phase != "closing" {
                slot.drawing = Some(sender);
            }
            return;
        }
        if inner.generation == generation
            && inner.status.session == Some(session)
            && !inner.cancel.load(Ordering::Acquire)
            && inner.status.phase != "closing"
        {
            inner.drawing = Some(sender);
        }
    }
    /// Draws on the remote screen: `true` when queued, `false` when this session is not active
    /// here as the viewing side or the remote computer cannot show drawings. Needs no control.
    pub async fn draw(&self, session: Uuid, message: core_protocol::annotation::Message) -> Result<bool, String> {
        message.validate().map_err(|error| error.to_string())?;
        let inner = self.0.lock().await;
        let sender = if let Some(slot) = inner.slots.find(session) {
            if !slot.live() || inner.cancel.load(Ordering::Acquire) {
                return Ok(false);
            }
            slot.drawing.as_ref()
        } else {
            if inner.status.session != Some(session)
                || inner.status.phase != "active"
                || inner.status.sharing
                || inner.cancel.load(Ordering::Acquire)
                || inner
                    .session_cancel
                    .as_ref()
                    .is_some_and(|flag| flag.load(Ordering::Acquire))
            {
                return Ok(false);
            }
            inner.drawing.as_ref()
        };
        let Some(sender) = sender else { return Ok(false) };
        match sender.try_send(message) {
            Ok(()) => Ok(true),
            Err(mpsc::error::TrySendError::Closed(_)) => Ok(false),
            Err(mpsc::error::TrySendError::Full(_)) => Err("The drawing transport is busy".into()),
        }
    }
    pub async fn grant(
        &self,
        generation: u64,
        session: Uuid,
        control: bool,
        input: Option<mpsc::Sender<InputCommand>>,
        clipboard: bool,
        clipboard_tx: Option<mpsc::Sender<ClipboardCommand>>,
    ) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation && !inner.cancel.load(Ordering::Acquire)
            && let Some(slot) = inner.slots.find_mut(session)
        {
            if !slot.cancelled() && slot.view.phase != "closing" {
                slot.view.control = control;
                slot.input = input;
                slot.view.clipboard = clipboard;
                slot.clipboard = clipboard_tx;
            }
            return;
        }
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
    pub async fn add_print_job(&self, generation: u64, session: Uuid, path: String) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation && !inner.cancel.load(Ordering::Acquire)
            && let Some(slot) = inner.slots.find_mut(session)
        {
            slot.view.print_jobs.push(path);
            return;
        }
        if inner.generation == generation
            && inner.status.session == Some(session)
            && !inner.cancel.load(Ordering::Acquire)
        {
            inner.status.print_jobs.push(path);
        }
    }
    pub async fn set_displays(&self, generation: u64, session: Uuid, count: u16, current: u16, all: bool) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation && !inner.cancel.load(Ordering::Acquire)
            && let Some(slot) = inner.slots.find_mut(session)
        {
            slot.view.displays = count;
            slot.view.display = current;
            slot.view.display_all = all;
            return;
        }
        if inner.generation == generation
            && inner.status.session == Some(session)
            && !inner.cancel.load(Ordering::Acquire)
        {
            inner.status.displays = count;
            inner.status.display = current;
            inner.status.display_all = all;
        }
    }
    pub async fn set_host_actions(&self, generation: u64, session: Uuid, actions: Vec<String>, signin_id: Option<u32>, macs: Vec<String>) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation && !inner.cancel.load(Ordering::Acquire)
            && let Some(slot) = inner.slots.find_mut(session)
        {
            slot.view.host_actions = actions;
            slot.view.signin_id = signin_id;
            slot.view.host_macs = macs;
            return;
        }
        if inner.generation == generation
            && inner.status.session == Some(session)
            && !inner.cancel.load(Ordering::Acquire)
        {
            inner.status.host_actions = actions;
            inner.status.signin_id = signin_id;
            inner.status.host_macs = macs;
        }
    }
    pub async fn set_meeting_offer(&self, generation: u64, session: Uuid, room: String) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation && !inner.cancel.load(Ordering::Acquire)
            && let Some(slot) = inner.slots.find_mut(session)
        {
            slot.view.meeting_offer = Some((session, room));
            return;
        }
        if inner.generation == generation && inner.status.session == Some(session) && !inner.cancel.load(Ordering::Acquire) {
            inner.status.meeting_offer = Some((session, room));
        }
    }
    pub async fn set_recording_permission(
        &self,
        generation: u64,
        session: Uuid,
        allowed: bool,
    ) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation && !inner.cancel.load(Ordering::Acquire)
            && let Some(slot) = inner.slots.find_mut(session)
        {
            if !slot.cancelled() && slot.view.phase != "closing" {
                slot.view.recording_permitted = allowed;
            }
            return;
        }
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
            if let Some(slot) = inner.slots.find(session) {
                slot.view.control
            } else {
                inner.status.session == Some(session) && inner.status.control && !inner.status.sharing
            }
        };
        if release {
            // Held keys are let go first. If the host has not confirmed that yet, the release is
            // still on its way and arrives before the clipboard (same ordered channel), so only a
            // closed connection stops the clipboard here.
            if let Err(error) = self.input(session, InputEvent::ReleaseAll).await
                && error != crate::remote_control::NOT_CONFIRMED
            {
                return Err(error);
            }
        }
        let (reply, wait) = tokio::sync::oneshot::channel();
        {
            let inner = self.0.lock().await;
            let channel = if let Some(slot) = inner.slots.find(session) {
                if !slot.live() || !slot.view.clipboard || inner.cancel.load(Ordering::Acquire) {
                    return Err("The host has not granted active clipboard access".into());
                }
                slot.clipboard.as_ref()
            } else {
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
                inner.clipboard.as_ref()
            };
            channel
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
        if let Some(slot) = inner.slots.find(session) {
            // This session's own mailbox: a picture of another session never arrives here.
            if inner.cancel.load(Ordering::Acquire) || slot.cancelled() {
                slot.video.clear();
                return Ok(Vec::new());
            }
            let Some(current) = slot.view.session.filter(|_| slot.view.phase == "active") else {
                return Ok(Vec::new());
            };
            slot.video.window_decodes(current, window_decodes);
            return slot.video.take(current);
        }
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
            inner.drawing = None;
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
        inner.drawing = None;
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
    /// A technician session got a broker session (the broker answered the request, or replaced the
    /// transport). `key` is the stable id or the id the slot has now.
    #[allow(clippy::too_many_arguments)]
    async fn slot_track(
        &self,
        generation: u64,
        key: Uuid,
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
        let Some(slot) = inner.slots.find_mut(key) else {
            cancel.store(true, Ordering::Release);
            return Err("This session has already closed".into());
        };
        if slot.ended {
            cancel.store(true, Ordering::Release);
            return Err("This session has already closed".into());
        }
        if let Some(previous) = slot.session_cancel.replace(cancel) {
            previous.store(true, Ordering::Release);
        }
        if slot.view.consent != Some(consent) {
            slot.file_permission = None;
            if let Some(previous) = slot.consent_cancel.replace(consent_cancel) {
                previous.store(true, Ordering::Release);
            }
        }
        slot.view.session = Some(session);
        slot.view.consent = Some(consent);
        slot.view.joined_session = joined;
        slot.view.switched = reversed;
        slot.view.peer_features.clear();
        slot.renew_after = Some(std::time::Instant::now() + Duration::from_secs(31));
        slot.input = None;
        slot.drawing = None;
        slot.clipboard = None;
        slot.view.control = false;
        slot.view.clipboard = false;
        slot.view.files = false;
        slot.restarted();
        slot.video.activate(session)?;
        Ok(())
    }
    /// Changes what one technician session shows (phase, message).
    async fn slot_change(&self, generation: u64, key: Uuid, update: impl FnOnce(&mut crate::remote_sessions::SessionStatus)) {
        let mut inner = self.0.lock().await;
        if inner.generation != generation || inner.cancel.load(Ordering::Acquire) {
            return;
        }
        if let Some(slot) = inner.slots.find_mut(key) {
            update(&mut slot.view);
        }
    }
    /// The technician session exists and was not ended by the person.
    async fn slot_open(&self, key: Uuid) -> bool {
        self.0.lock().await.slots.find(key).is_some_and(|slot| !slot.ended)
    }
    /// The picture mailbox of a technician session.
    async fn slot_video(&self, key: Uuid) -> Option<Arc<VideoMailbox>> {
        self.0.lock().await.slots.find(key).map(|slot| slot.video.clone())
    }
    /// Starts a technician session the registration loop begins itself (sides switched).
    async fn slot_begin(&self, generation: u64, device: u32) -> Result<Uuid, String> {
        let mut inner = self.0.lock().await;
        if inner.generation != generation || inner.cancel.load(Ordering::Acquire) {
            return Err("The connection was cancelled".into());
        }
        inner.slots.add(device)
    }
    /// The state of a technician session for the registration loop: `(phase, ended)`.
    async fn slot_active(&self, key: Uuid) -> bool {
        self.0.lock().await.slots.find(key).is_some_and(|slot| slot.live())
    }
    /// A technician session is over (it ended, failed or was refused): its state goes, the others stay.
    /// `message` is what a single-session caller reads from the top-level status afterwards.
    /// The stable id and the device of a technician session (any id of it in).
    async fn slot_device(&self, key: Uuid) -> Option<(Uuid, u32)> {
        self.0.lock().await.slots.find(key).map(|slot| (slot.view.id, slot.view.peer))
    }
    async fn slot_end(&self, generation: u64, key: Uuid, message: String) {
        let mut inner = self.0.lock().await;
        if inner.generation != generation {
            return;
        }
        if let Some(mut slot) = inner.slots.remove(key) {
            slot.revoke(&message);
        }
        if inner.status.phase == "ready" && inner.status.session.is_none() {
            inner.status.message = message;
        }
    }
    /// Every technician session ends (the registration is lost or stopped).
    fn end_all_slots(inner: &mut Inner, message: &str) {
        Self::end_slots_except(inner, &[], message);
    }
    /// Every technician session ends except those in `keep` (carried over to the next registration).
    fn end_slots_except(inner: &mut Inner, keep: &[Uuid], message: &str) {
        for mut slot in inner.slots.drain() {
            if keep.iter().any(|id| slot.view.id == *id || slot.view.session == Some(*id)) {
                inner.slots.restore(slot);
            } else {
                slot.revoke(message);
            }
        }
    }
    pub async fn update_peer(
        &self,
        generation: u64,
        session: Uuid,
        message: &str,
        received: u64,
    ) {
        let mut inner = self.0.lock().await;
        if inner.generation == generation && !inner.cancel.load(Ordering::Acquire)
            && let Some(slot) = inner.slots.find_mut(session)
        {
            if !slot.cancelled() && slot.view.phase != "closing" {
                slot.view.phase = "active".into();
                slot.view.message = message.into();
                slot.pictures(received);
            }
            return;
        }
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
    /// The connection service's ID of the current transport.
    id: Uuid,
    /// The session as the person sees it (the first transport's ID); it stays when the
    /// transport is renewed, so the window, chat and tools carry on.
    ui: Uuid,
    /// The current transport (its media task's flags).
    link: remote_peer::TransportLink,
    /// A renewal under way: the transport before, which keeps sharing until the new one takes over.
    handover: Option<remote_peer::TransportLink>,
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
    /// The connection service renewed this session's transport as `session`. Make before break:
    /// the running transport keeps sharing (the technician keeps seeing its pictures) until the
    /// renewed one takes over, and the person sees the same session (`ui`) with the same
    /// consent, grants and stop flags: both transports stop with the session.
    fn begin_renewal(&mut self, session: Uuid) {
        let before = std::mem::take(&mut self.link);
        before.superseded.store(true, Ordering::Release);
        // A renewal whose transport never started: the one sharing stays the one before.
        if self.handover.is_none() {
            self.handover = Some(before);
        }
        self.id = session;
        self.close_sent = false;
        self.signals = None;
        self.early.clear();
        self.nonce = None;
        self.relay_expires = 0;
        self.restart_pending = None;
        self.rotated_at = std::time::Instant::now();
        self.accepted_at = Some(std::time::Instant::now());
    }
    /// A renewal under way: the session stays "active" for the window while the new transport
    /// negotiates (its picture, tools and input go on over the transport before).
    fn renewing(&self) -> bool {
        self.handover.is_some()
    }
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
    /// Records this peer's session in the engine state. A technician peer (not the host) has its own
    /// technician session: `key` is the stable id of it, or the id it had before a transport replacement.
    async fn track(&self, state: &RemoteSession, generation: u64, key: Uuid) -> Result<(), String> {
        if !self.host {
            return state
                .slot_track(
                    generation,
                    key,
                    self.id,
                    self.cancel.clone(),
                    self.consent_id,
                    self.consent_cancel.clone(),
                    self.joined,
                    self.reversed,
                )
                .await;
        }
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
/// A request sent to the connection service that it has not answered yet (answers come in order).
/// A technician session whose computer went away: the same device is asked again for a minute.
struct Reconnect {
    host: u32,
    /// The session's stable id in the registry (its tab stays while it asks again).
    slot: Uuid,
    retry: crate::reconnect::Retry,
    gave_up: bool,
}
/// Starts asking the device of a technician session again, for a minute: when the computer went away, or when the
/// person pressed Retry after "Could not reconnect". Nothing happens for access that needs somebody's answer.
async fn begin_reconnect(
    state: &RemoteSession,
    generation: u64,
    retries: &mut Vec<Reconnect>,
    last_access: &std::collections::HashMap<u32, crate::reconnect::Access>,
    key: Uuid,
) -> bool {
    let Some((slot, host)) = state.slot_device(key).await else { return false };
    if retries.iter().any(|active| active.host == host) {
        return false;
    }
    let Some(retry) = last_access.get(&host).cloned().and_then(|access| crate::reconnect::Retry::start(access, std::time::Instant::now())) else {
        return false;
    };
    retries.push(Reconnect { host, slot, retry, gave_up: false });
    state
        .slot_change(generation, slot, |view| {
            view.phase = "outgoing".into();
            view.reconnecting = true;
            view.reconnect_failed = false;
            view.message = "Reconnecting… the computer went away; asking again".into();
        })
        .await;
    state.change(generation, |status| {
        status.reconnecting = true;
        status.reconnect_failed = false;
    }).await;
    true
}
struct Out {
    /// The technician session's stable id in the registry.
    slot: Uuid,
    host: u32,
    key: ed25519_dalek::VerifyingKey,
    unattended: bool,
    /// Joined on a colleague's invitation.
    joined: bool,
    /// The technician ended it before the broker answered: it is closed as soon as it is known.
    cancelled: bool,
}
/// The peer with this session id: the hosted one, or one of the technician's sessions.
fn find_peer<'a>(peer: &'a mut Option<Peer>, ctrl: &'a mut [Peer], id: Uuid) -> Option<&'a mut Peer> {
    if peer.as_ref().is_some_and(|active| active.id == id || active.ui == id) {
        return peer.as_mut();
    }
    ctrl.iter_mut().find(|active| active.id == id || active.ui == id)
}
/// Sessions waiting to be resumed: the transport id and when it was asked, oldest first.
type Resuming = Vec<(Uuid, std::time::Instant)>;
/// The transport id of the session (hosted or technician's) that `session` names, if it is waiting to be resumed.
fn resuming_id(resuming: &Resuming, peer: &Option<Peer>, ctrl: &[Peer], session: Uuid) -> Option<Uuid> {
    let id = peer.iter().chain(ctrl.iter()).find(|active| active.id == session || active.ui == session)?.id;
    resuming.iter().any(|(waiting, _)| *waiting == id).then_some(id)
}
/// The session a command is about, if it is about one.
fn command_session(command: &Command) -> Option<Uuid> {
    match command {
        Command::Close(session) | Command::Reconnect(session) | Command::Restart(session) | Command::SwitchSides(session) => Some(*session),
        Command::Signal { session, .. } | Command::Finished { session, .. } | Command::Decide { session, .. }
        | Command::Invite { session, .. } | Command::DecideJoin { session, .. } => Some(*session),
        _ => None,
    }
}
/// A session being resumed after the connection service restarted could not be: it ends here, alone. The other
/// sessions that are being resumed, and those that were never lost, are not touched.
#[allow(clippy::too_many_arguments)]
async fn end_resumed(
    state: &RemoteSession,
    generation: u64,
    peer: &mut Option<Peer>,
    ctrl: &mut Vec<Peer>,
    resuming: &mut Resuming,
    end_when_resumed: &mut Vec<Uuid>,
    id: Uuid,
    message: &str,
) {
    resuming.retain(|(waiting, _)| *waiting != id);
    end_when_resumed.retain(|waiting| *waiting != id);
    if peer.as_ref().is_some_and(|active| active.id == id) {
        *peer = None;
        state
            .change(generation, |status| {
                status.phase = "ready".into();
                status.session = None;
                status.peer = None;
                status.requester = None;
                status.message = message.into();
            })
            .await;
    } else if let Some(index) = ctrl.iter().position(|active| active.id == id) {
        let over = ctrl.remove(index);
        state.slot_end(generation, over.ui, message.into()).await;
    }
    let still = !resuming.is_empty();
    state.change(generation, |status| status.reconnecting = still).await;
}
/// A session this registration ended a moment ago: the connection service may still send a message for it.
fn recently_gone(gone: &std::collections::VecDeque<(Uuid, std::time::Instant)>, id: Uuid) -> bool {
    gone.iter().any(|(session, at)| *session == id && at.elapsed() < Duration::from_secs(60))
}
fn remember_gone(gone: &mut std::collections::VecDeque<(Uuid, std::time::Instant)>, id: Uuid) {
    gone.retain(|(_, at)| at.elapsed() < Duration::from_secs(60));
    if gone.len() >= 32 {
        gone.pop_front();
    }
    gone.push_back((id, std::time::Instant::now()));
}
type Socket =
    tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;
struct LeaseTask(JoinHandle<()>);
impl Drop for LeaseTask {
    fn drop(&mut self) {
        self.0.abort();
    }
}
pub type BrokerLease = Arc<std::sync::Mutex<(u64, std::time::Instant)>>;
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
                // End this registration so supervise can reconnect with a fresh ticket.
                // Do not freeze the UI in "error": that is what left Windows stuck offline.
                cancel.store(true, Ordering::Release);
                let mut inner = state.0.lock().await;
                if inner.generation == generation {
                    if let Some(flag) = &inner.session_cancel {
                        flag.store(true, Ordering::Release);
                    }
                    if let Some(video) = &inner.video {
                        video.clear();
                    }
                    RemoteSession::end_all_slots(&mut inner, "Connection authorization expired; reconnecting…");
                    if matches!(inner.status.phase.as_str(), "active" | "negotiating" | "incoming" | "outgoing") {
                        inner.status.phase = "connecting".into();
                        inner.status.message =
                            "Connection authorization expired; reconnecting…".into();
                    }
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
struct AttemptGuard(Arc<AtomicBool>, bool);
impl AttemptGuard {
    fn disarm(mut self) -> Self {
        self.1 = true;
        self
    }
}
impl Drop for AttemptGuard {
    fn drop(&mut self) {
        if !self.1 {
            self.0.store(true, Ordering::Release);
        }
    }
}
/// How long a session whose connection to the connection service was lost waits to be resumed.
const RESUME_WINDOW: Duration = Duration::from_secs(30);
/// A session carried from one registration to the next while the connection service restarts.
struct Carry {
    /// Every session whose media connection was up (the hosted one, or the technician's, each on its own).
    peers: Vec<Peer>,
    attempt: Arc<AtomicBool>,
    lease: BrokerLease,
    since: std::time::Instant,
}
impl Peer {
    /// Whether the session's media connection is established and nothing about it is in flight: only such a
    /// session can be resumed after the connection to the connection service was lost.
    fn resumable(&self) -> bool {
        !self.cancel.load(Ordering::Acquire)
            && self.signals.is_some()
            && self.identity.is_some()
            && self.nonce.is_none()
            && self.restart_pending.is_none()
            && !self.close_sent
            && self.local_consent != Some(false)
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
    let mut ever_ready = false;
    // A session whose media connection survives a lost connection to the connection service (resume).
    let mut keep: Option<Carry> = None;
    loop {
        if cancel.load(Ordering::Acquire) {
            return Ok(());
        }
        // The kept session's media is bound to the registration's flag: the next registration continues it.
        let attempt = keep.as_ref().map_or_else(|| Arc::new(AtomicBool::new(false)), |kept| kept.attempt.clone());
        let guard = AttemptGuard(attempt.clone(), false);
        crate::qa_timeline::begin("registration: starting");
        let result = match auth.credential(Some(&attempt)).await {
            Ok(credential) => {
                crate::qa_timeline::mark("registration: sign-in credential ready");
                let principal = auth.principal(&credential)?;
                if principal.user != expected.user
                    || principal.tenant != expected.tenant
                    || principal.staff != expected.staff
                {
                    return Err("Account authorization changed; register again".into());
                }
                tokio::select! {
                    _=cancelled(&cancel)=>Ok(()),
                    result=run(state,generation,auth.clone(),identity.clone(),url.clone(),credential,attempt,tx.clone(),&mut commands,video.clone(),&mut keep)=>result,
                }
            }
            Err(message) if ever_ready => Err(Failure::network(&message)),
            Err(message) => return Err(message),
        };
        // A session kept for resuming must not cancel the registration flag its media listens to.
        let guard = if keep.is_some() { guard.disarm() } else { guard };
        drop(guard);
        let mut error = match result {
            Ok(()) => return Ok(()),
            Err(error) => error,
        };
        // Lab builds only: why the registration ended (a running session ends with it).
        #[cfg(feature = "qa-session")]
        eprintln!("QA registration ended at clock {}: {}", crate::remote_control::qa_wall_clock(), error.message);
        if !error.registered_for.is_zero() {
            ever_ready = true;
        }
        if ever_ready && !error.message.contains("Account authorization changed") && !error.message.contains("does not match its enrollment") {
            error.retryable = true;
        }
        if cancel.load(Ordering::Acquire) {
            return Ok(());
        }
        if !error.retryable {
            return Err(error.message);
        }
        if keep.as_ref().is_some_and(|kept| kept.since.elapsed() >= RESUME_WINDOW) {
            keep = None;
        }
        let delay=backoff.next(error.registered_for).ok_or_else(||format!("{}; automatic reconnect attempts exhausted. Register this computer again when the network is available.",error.message))?;
        {
            let carried: Vec<Uuid> = keep.as_ref().map(|kept| kept.peers.iter().filter(|peer| !peer.host).map(|peer| peer.ui).collect()).unwrap_or_default();
            let mut inner = state.0.lock().await;
            if inner.generation == generation {
                RemoteSession::end_slots_except(&mut inner, &carried, "Connection lost; sharing stopped");
            }
        }
        if keep.is_some() {
            // The session stays open: only the connection to the connection service is restored.
            state
                .change(generation, |status| {
                    status.reconnecting = true;
                    status.message = "Reconnecting… the session stays open".into();
                })
                .await;
            tokio::select! {_ = cancelled(&cancel)=>return Ok(()),_ = tokio::time::sleep(delay.min(Duration::from_secs(2)))=>{}}
            continue;
        }
        state
            .change(generation, |status| {
                status.reconnecting = false;
                status.password_flag = false;
                status.password_ends = None;
                status.phase = "connecting".into();
                status.session = None;
                status.peer = None;
                status.requester = None;
                status.host = None;
                // Keep the display the owner chose. The new registration turns it back on.
                if status.unattended.is_some() {
                    status.unattended_enabled = false;
                    status.unattended_pending = Some(UnattendedChange::Enabling);
                }
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
        state.finish_previous_media(None).await?;
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
    keep: &mut Option<Carry>,
) -> Result<(), Failure> {
    // A session carried over from the registration that was lost: it resumes once this one is ready.
    let carried = keep.take();
    let carried_since = carried.as_ref().map(|kept| kept.since);
    let mut peer: Option<Peer> = None;
    // The technician's sessions (up to four at once), each with its own peer. `peer` is the hosted one.
    let mut ctrl: Vec<Peer> = Vec::new();
    let mut resuming: Resuming = Vec::new();
    let mut end_when_resumed: Vec<Uuid> = Vec::new();
    let mut carried_lease: Option<BrokerLease> = None;
    if let Some(kept) = carried {
        carried_lease = Some(kept.lease);
        // Each kept session is the hosted one or one of the technician's; every one is resumed on its own.
        for kept in kept.peers { if kept.host { peer = Some(kept); } else { ctrl.push(kept); } }
    }
    let enrolled = auth.enrollment(&initial, &identity).await?;
    crate::qa_timeline::mark("registration: device enrollment ready");
    let device = enrolled.device;
    let principal = auth.principal(&initial)?.clone();
    if enrolled.public_key != identity.public_key()? {
        return Err("The saved device key does not match its enrollment".into());
    }
    let service = crate::service_config::ServiceConfig::load()?;
    let roots = service.roots()?;
    let mut own_ticket = registration_ticket(&initial, &enrolled, &identity).await?;
    crate::qa_timeline::mark("registration: device ticket ready");
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
    crate::qa_timeline::mark("registration: connection service socket open");
    let broker_lease = carried_lease.unwrap_or_else(|| Arc::new(std::sync::Mutex::new((0, std::time::Instant::now()))));
    if let Ok(mut lease) = broker_lease.lock() {
        *lease = (own_ticket.expires_at, std::time::Instant::now() + Duration::from_secs(899));
    }
    let _lease_task = watch_lease(
        state.clone(),
        generation,
        cancel.clone(),
        broker_lease.clone(),
    );
    let mut ready_at = None;
    let result:Result<(),Failure>=async {
        send(&mut socket,ClientMessage::AuthenticateDevice{token:signaling_server::delegated_auth::encode(&own_ticket).map_err(|_|"Cannot encode delegated device authorization")?,device}).await?;
        let challenge=tokio::time::timeout(Duration::from_secs(20),event(&mut socket)).await.map_err(|_|Failure::network("Device authorization timed out"))??;
        let Some(ServerMessage::DeviceChallenge{challenge})=challenge else{return Err(Failure::network("Device challenge was not received"));};
        if challenge.device!=*device.as_bytes() || challenge.profile!=*principal.user.as_bytes() || challenge.tenant!=principal.tenant.map(|id|*id.as_bytes()) || challenge.connection_id!=enrolled.connection_id || challenge.generation!=enrolled.generation || cancel.load(Ordering::Acquire) {return Err("The device authorization challenge does not match this account".into());}
        send(&mut socket,ClientMessage::ProveDevice{signature:identity.prove(challenge)?}).await?;
        let ready=tokio::time::timeout(Duration::from_secs(6),event(&mut socket)).await.map_err(|_|Failure::network("Device registration timed out"))??;
        if !matches!(ready,Some(ServerMessage::Ready{host,protocol:core_protocol::PROTOCOL_VERSION}) if host==enrolled.connection_id) {return Err(Failure::network("The connection service did not verify this device ID"));}
        ready_at=Some(std::time::Instant::now());
        crate::qa_timeline::mark("registration: READY (reachable)");
        match &auth {
            #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
            RegistrationAuth::Startup { source, .. } => {
                if !source.capture_allowed() {
                    return Err("The sign-in screen host is missing the required system permissions".into());
                }
                let carried_session = peer.is_some() || !ctrl.is_empty();
                state.change(generation, |status| {
                    if carried_session {
                        status.host = Some(enrolled.connection_id);
                        return;
                    }
                    status.phase = "ready".into();
                    status.message = "Verifying unattended startup sharing".into();
                    status.host = Some(enrolled.connection_id);
                    status.unattended = Some(source.clone());
                    status.unattended_pending = Some(UnattendedChange::Enabling);
                }).await;
                send(&mut socket, ClientMessage::ConfigureUnattended { enabled: true }).await?;
            }
            _ => {
                let carried_session = peer.is_some() || !ctrl.is_empty();
                state.change(generation, |status| {
                    if carried_session {
                        status.host = Some(enrolled.connection_id);
                        return;
                    }
                    status.phase = "ready".into();
                    status.host = Some(enrolled.connection_id);
                    if status.unattended.is_some() {
                        status.unattended_enabled = false;
                        status.unattended_pending = Some(UnattendedChange::Enabling);
                        status.message = "Verifying unattended startup sharing".into();
                    } else {
                        status.message = "Ready for an attended connection".into();
                    }
                }).await;
                if state.0.lock().await.status.unattended.is_some() {
                    send(&mut socket, ClientMessage::ConfigureUnattended { enabled: true }).await?;
                }
            }
        }
        let mut credential=initial;
        let mut authorized_until=own_ticket.expires_at;
        let mut pending_renewal:Option<std::time::Instant>=None;
        let mut last_renewed=std::time::Instant::now();
        let mut last_delegation_attempt=std::time::Instant::now();
        // Second technicians and switching sides: announced to the connection service, which
        // forwards invitations and switches only to apps that understand them.
        let mut features:Vec<String>=if own_ticket.unattended_host_only() || crate::service_config::controller_only() {vec![JOIN.into()]} else {vec![JOIN.into(),SWITCH.into()]};
        // Password-session warnings, the administrator's device policy, resuming a session after the connection
        // service restarted, and signed remote commands (verified in `agent_verify`).
        features.extend([ENDING.into(),POLICY.into(),RESUME.into(),SIGNED.into()]);
        send(&mut socket,ClientMessage::Capabilities{features}).await?;
        send(&mut socket,ClientMessage::AppVersion{version:crate::agent::APP_VERSION.into(),platform:app_platform()}).await?;
        // An older connection service answers "invalid": then these features are simply not offered.
        let capabilities_sent=std::time::Instant::now();
        // The computer being helped went away: ask the same device again for a minute (reconnect.rs).
        // Per device (a technician has one session per device): how it was asked for, and its asking again.
        let mut last_access:std::collections::HashMap<u32,crate::reconnect::Access>=std::collections::HashMap::new();
        let mut retries:Vec<Reconnect>=Vec::new();
        // The media connections of the running sessions are still up: ask the connection service to continue
        // each of them (answers come in the order asked; one that fails ends alone).
        let kept_ids:Vec<Uuid>=peer.iter().chain(ctrl.iter()).map(|kept|kept.id).collect();
        for session in kept_ids {
            send(&mut socket,ClientMessage::Resume{session}).await?;
            resuming.push((session,std::time::Instant::now()));
        }
        // Host: colleagues of the technician who joined (or ask to join) the running session.
        let mut joined:Vec<Peer>=Vec::new();
        // (host, pinned key, unattended, joined on an invitation)
        let mut outgoing:std::collections::VecDeque<Out>=std::collections::VecDeque::new();
        // Sessions ended a moment ago: late messages of the connection service for them are ignored.
        let mut recent:std::collections::VecDeque<(Uuid,std::time::Instant)>=std::collections::VecDeque::new();
        let mut peer_keys: std::collections::HashMap<u32, (ed25519_dalek::VerifyingKey, std::time::Instant)> = std::collections::HashMap::new();
        // Invitations received: id, host, unattended, expiry (Unix seconds).
        let mut invitations:Vec<(Uuid,u32,bool,u64)>=Vec::new();
        let mut invite_pending=false;
        let mut switch_pressed:Option<(Uuid,std::time::Instant)>=None;
        // When this computer last answered or gave up a request the connection service may have
        // ended at the same moment (a colleague's request at its 90 s limit): the service then
        // refuses that late answer, which must not end this connection and the running session.
        let mut late_answer:Option<std::time::Instant>=None;
        let mut switch_memory:Option<SwitchMemory>=None;
    // Presence questions still waiting for their answer, oldest first: an answer speaks only for the IDs asked.
    let mut presence_asked: std::collections::VecDeque<Vec<u32>> = std::collections::VecDeque::new();
        let mut tick=tokio::time::interval(Duration::from_millis(250));
        let mut last_activity=std::time::Instant::now();
        let mut heartbeat=std::time::Instant::now();
        loop {
            tokio::select! {
                _=tick.tick()=>{
                    if cancel.load(Ordering::Acquire){return Err(Failure::network("Registration authorization expired; reconnecting"));}
                    let now=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_|"System clock is unavailable")?.as_secs();
                    if now>=authorized_until || pending_renewal.is_some_and(|deadline|std::time::Instant::now()>=deadline) {return Err(Failure::network("Device authorization expired; reconnecting"));}
                    if last_activity.elapsed()>Duration::from_secs(150){return Err(Failure::network("Connection-service heartbeat expired"));}
                    let mut finished_retry:Vec<usize>=Vec::new();
                    for (position,active) in retries.iter_mut().enumerate(){
                        if ctrl.iter().any(|session|session.remote==active.host && session.signals.is_some()){
                            finished_retry.push(position);
                            continue;
                        }
                        let asked=outgoing.iter().any(|out|out.host==active.host);
                        match active.retry.step(std::time::Instant::now(),asked){
                            crate::reconnect::Step::Ask=>{
                                let access=active.retry.access().clone();
                                let _=tx.try_send(Command::Request(access.host,access.password,access.unattended,active.slot));
                            },
                            crate::reconnect::Step::GiveUp=>{finished_retry.push(position);active.gave_up=true;},
                            crate::reconnect::Step::Wait=>{},
                        }
                    }
                    for position in finished_retry.into_iter().rev(){
                        let done=retries.remove(position);
                        let failed=done.gave_up;
                        state.slot_change(generation,done.slot,|view|{view.reconnecting=false;view.reconnect_failed=failed;if failed{view.phase="outgoing".into();view.message="Could not reconnect".into();}}).await;
                        let waiting=!retries.is_empty()||!resuming.is_empty();
                        let alone=ctrl.is_empty()&&peer.is_none();
                        state.change(generation,|status|{
                            status.reconnecting=waiting;
                            if failed{status.reconnect_failed=true;if alone&&!waiting{status.phase="ready".into();status.session=None;status.peer=None;status.message="Could not reconnect".into();}}
                            else{status.reconnect_failed=false;}
                        }).await;
                    }
                    let overdue:Vec<Uuid>=resuming.iter().filter(|(_,since)|since.elapsed()>=Duration::from_secs(20)).map(|(id,_)|*id).collect();
                    for id in overdue{
                        // The connection service did not take this session back in time: it ends here, alone.
                        end_resumed(state,generation,&mut peer,&mut ctrl,&mut resuming,&mut end_when_resumed,id,"The session ended while the connection service restarted").await;
                    }
                    if let Some(active)=peer.as_mut(){
                        if !active.close_sent && active.cancel.load(Ordering::Acquire) && active.signals.is_none(){
                            let session=active.id;let reject=active.host && active.local_consent.is_none();peer=None;
                            state.change(generation,|status|{status.phase="ready".into();status.session=None;status.peer=None;status.requester=None;status.message="Invitation stopped locally; ready for a new connection".into();}).await;
                            late_answer=Some(std::time::Instant::now());
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
                    if !ctrl.is_empty() {
                        // Each technician session is looked after on its own: one that expires or fails ends
                        // alone, the others keep running.
                        let mut ended:Vec<(Uuid,String)>=Vec::new();
                        let mut renew:Vec<Uuid>=Vec::new();
                        for active in ctrl.iter() {
                            if active.close_sent || resuming.iter().any(|(id,_)|*id==active.id) {continue;}
                            if active.cancel.load(Ordering::Acquire) && active.signals.is_none() {ended.push((active.id,"Invitation stopped locally; ready for a new connection".into()));continue;}
                            let invitation_expired=active.signals.is_none() && ((active.local_consent.is_none() && active.accepted_at.is_none() && active.started_at.elapsed()>=core_protocol::session::CONSENT_LIFETIME) || active.accepted_at.is_some_and(|accepted|accepted.elapsed()>=core_protocol::session::NEGOTIATION_LIFETIME));
                            if invitation_expired {ended.push((active.id,"The local invitation or acceptance deadline expired; request a fresh connection".into()));continue;}
                            if active.started_at.elapsed()>=Duration::from_secs(8*3600) || active.restart_pending.is_some_and(|start|start.elapsed()>=Duration::from_secs(20)) {ended.push((active.id,"The accepted connection lifetime or renewal deadline expired".into()));continue;}
                            if active.signals.is_some() && !active.cancel.load(Ordering::Acquire) && active.restart_pending.is_none() && active.rotated_at.elapsed()>=Duration::from_secs(31) && (active.relay_expires.saturating_sub(now)<=90 || active.rotated_at.elapsed()>=Duration::from_secs(720)) {renew.push(active.id);}
                        }
                        for session in renew {
                            if state.slot_active(session).await && let Some(active)=ctrl.iter_mut().find(|active|active.id==session) {
                                send(&mut socket,ClientMessage::Restart{session}).await?;active.restart_pending=Some(std::time::Instant::now());
                                state.slot_change(generation,session,|view|view.message="Renewing relay credentials and transport keys for the accepted peer".into()).await;
                            }
                        }
                        for (session,message) in ended {
                            if let Some(index)=ctrl.iter().position(|active|active.id==session) {
                                let mut over=ctrl.remove(index);
                                remember_gone(&mut recent,session);
                                if over.take_close() {late_answer=Some(std::time::Instant::now());send(&mut socket,ClientMessage::Close{session}).await?;}
                                state.slot_end(generation,session,message).await;
                            }
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
                                if gone.take_close(){late_answer=Some(std::time::Instant::now());send(&mut socket,if undecided{ClientMessage::Decide{session,accept:false}}else{ClientMessage::Close{session}}).await?;}
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
                    if peer.is_none() && ctrl.is_empty() && switch_memory.as_ref().is_some_and(|memory|memory.at.elapsed()>=Duration::from_secs(120)){switch_memory=None;}
                    if heartbeat.elapsed()>Duration::from_secs(20){tokio::time::timeout(Duration::from_secs(5),socket.send(Message::Ping(Vec::new().into()))).await.map_err(|_|Failure::network("Heartbeat write timed out"))?.map_err(crate::remote_recovery::socket)?;heartbeat=std::time::Instant::now();}
                    let fresh=match auth.credential(None).await {
                        Ok(fresh)=>fresh,
                        Err(_)=>return Err(Failure::network("Sign-in is renewing; reconnecting registration")),
                    };
                    let fresh_principal = match auth.principal(&fresh) {
                        Ok(value)=>value,
                        Err(_)=>return Err(Failure::network("Sign-in is renewing; reconnecting registration")),
                    };
                    if fresh_principal.user!=principal.user || fresh_principal.tenant!=principal.tenant || fresh_principal.staff!=principal.staff {return Err(Failure::network("Account authorization changed; reconnecting registration"));}
                    let token_changed = fresh.as_ref().zip(credential.as_ref()).is_some_and(|(new, old)| new.token.as_str()!=old.token.as_str());
                    if pending_renewal.is_none() && last_delegation_attempt.elapsed()>=Duration::from_secs(31) && (token_changed || last_renewed.elapsed()>=Duration::from_secs(600) || authorized_until.saturating_sub(now)<=180) {
                        last_delegation_attempt=std::time::Instant::now();
                        let Ok(renewed)=registration_ticket(&fresh,&enrolled,&identity).await else {
                            continue;
                        };
                        if !same_identity(&own_ticket,&renewed){return Err("Device identity changed during renewal".into());}
                        if cancel.load(Ordering::Acquire){break;}
                        // A ticket capped by an expiring ISM token cannot gain
                        // lifetime until native account refresh succeeds, unless the
                        // 15-minute monotonic broker lease needs a refresh.
                        if renewed.expires_at<=authorized_until && last_renewed.elapsed()<Duration::from_secs(600) {credential=fresh;continue;}
                        send(&mut socket,ClientMessage::Renew{token:signaling_server::delegated_auth::encode(&renewed).map_err(|_|"Cannot encode delegated device renewal")?}).await?;
                        own_ticket=renewed;pending_renewal=Some(std::time::Instant::now()+Duration::from_secs(20));credential=fresh;
                    }
                },
                Some(command)=commands.recv()=>{
                    // Lab builds only: the session commands (no signalling, no contents).
                    #[cfg(feature = "qa-session")]
                    match &command{Command::Close(session)=>eprintln!("QA command close {session} at clock {}",crate::remote_control::qa_wall_clock()),Command::Restart(session)=>eprintln!("QA command restart {session} at clock {}",crate::remote_control::qa_wall_clock()),Command::Finished{session,result}=>eprintln!("QA command finished {session} {:?} at clock {}",result.as_ref().err(),crate::remote_control::qa_wall_clock()),_=>{}}
                    match command {
                    // While a session is being resumed only ending it counts; everything else about it is dropped.
                    // Commands about other sessions go on.
                    Command::Close(session) if resuming_id(&resuming,&peer,&ctrl,session).is_some()=>{
                        if let Some(id)=resuming_id(&resuming,&peer,&ctrl,session){
                            if let Some(active)=find_peer(&mut peer,&mut ctrl,session){active.take_close();}
                            end_when_resumed.push(id);
                            state.change(generation,|status|{status.message="Ending the session…".into();}).await;
                        }
                    },
                    Command::Finished{session,..} if resuming_id(&resuming,&peer,&ctrl,session).is_some()=>{
                        if let Some(id)=resuming_id(&resuming,&peer,&ctrl,session){
                            end_resumed(state,generation,&mut peer,&mut ctrl,&mut resuming,&mut end_when_resumed,id,"The session ended while the connection service restarted").await;
                        }
                    },
                    ref other if command_session(other).is_some_and(|session|resuming_id(&resuming,&peer,&ctrl,session).is_some())=>{},
                    Command::Request(host,code,unattended,slot)=>{
                        // This computer is hosting a session: it does not start one of its own.
                        if peer.is_some(){state.slot_end(generation,slot,"This computer is being helped right now; finish that first".into()).await;continue;}
                        if ctrl.len()+outgoing.len()>=crate::remote_sessions::MAX_SESSIONS{state.slot_end(generation,slot,"You already have 4 sessions open. End one to start another.".into()).await;continue;}
                        if !state.slot_open(slot).await{continue;}
                        crate::qa_timeline::begin(if unattended {"connect asked (unattended)"} else if code.is_some() {"connect asked (password)"} else {"connect asked (attended)"});
                        let Some(credential) = credential.as_ref() else { return Err("A startup host cannot request sessions".into()); };
                        let cached = peer_keys.get(&host).and_then(|(key, at)| (at.elapsed() < Duration::from_secs(300)).then_some(*key));
                        let qa_cached = cached.is_some();
                        let key=if let Some(key)=cached{key}else{match credential.ism.peer_key_scoped(&credential.token,credential.principal.is_personal(),host).await{Ok(key)=>{peer_keys.insert(host,(key,std::time::Instant::now()));key},Err(_)=>{state.slot_end(generation,slot,"This account could not verify that device ID. Check that it is enrolled in Mixel Remote and that your company has access.".into()).await;continue;}}};
                        crate::qa_timeline::mark(if qa_cached {"device key from the cache"} else {"device key fetched from the account service"});
                        if cancel.load(Ordering::Acquire){break;}
                        if !state.slot_open(slot).await{continue;}
                        let with_password=code.is_some();
                        last_access.insert(host,crate::reconnect::Access{host,unattended,password:code.clone()});
                        let request=match code {
                            Some(code)=>ClientMessage::RequestWithCode{host,code},
                            None if unattended=>ClientMessage::RequestUnattended{host},
                            None=>ClientMessage::Request{host},
                        };
                        send(&mut socket,request).await?;outgoing.push_back(Out{slot,host,key,unattended,joined:false,cancelled:false});crate::qa_timeline::mark("request sent to the connection service");
                        state.slot_change(generation,slot,|view|{view.phase="outgoing".into();view.message="Waiting for the remote host to Accept".into();}).await;
                        state.change(generation,|status|{status.password_flag=with_password;status.password_ends=None;}).await;
                        state.slot_change(generation,slot,|view|{view.password_flag=with_password;view.password_ends=None;view.password_session=None;view.unattended_session=unattended;}).await;
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
                    Command::Reconnect(session)=>{
                        begin_reconnect(state,generation,&mut retries,&last_access,session).await;
                    },
                    Command::Close(session)=>{
                        // The person ended it (or stopped the retries): nothing is asked again for this session.
                        if let Some((slot,host))=state.slot_device(session).await{
                            last_access.remove(&host);
                            let had_retry=retries.iter().position(|active|active.slot==slot).map(|index|retries.remove(index)).is_some();
                            let failed=state.0.lock().await.slots.find(slot).is_some_and(|found|found.view.reconnect_failed);
                            let in_peers=ctrl.iter().any(|active|active.id==session || active.ui==session);
                            if had_retry || (failed && !in_peers){
                                for out in outgoing.iter_mut().filter(|out|out.host==host){out.cancelled=true;}
                                state.slot_end(generation,slot,"Session ended".into()).await;
                                let waiting=!retries.is_empty()||!resuming.is_empty();
                                let alone=ctrl.is_empty()&&peer.is_none();
                                state.change(generation,|status|{status.reconnecting=waiting;status.reconnect_failed=false;if alone&&!waiting{status.phase="ready".into();status.session=None;status.peer=None;status.message="Session ended".into();}}).await;
                            }
                        }
                        // The window names the session as it sees it; the service knows its current transport.
                        if let Some(active)=peer.as_mut().filter(|peer|peer.id==session || peer.ui==session) && active.take_close(){
                            send(&mut socket,ClientMessage::Close{session:active.id}).await?;
                        }
                        // A technician session: the connection service is told, the session is removed when it answers.
                        if let Some(active)=ctrl.iter_mut().find(|peer|peer.id==session || peer.ui==session) && active.take_close(){
                            send(&mut socket,ClientMessage::Close{session:active.id}).await?;
                        }
                        // Not answered yet: closed as soon as the connection service names it.
                        if let Some(waiting)=outgoing.iter_mut().find(|waiting|waiting.slot==session){waiting.cancelled=true;}
                        if let Some(index)=joined.iter().position(|colleague|colleague.id==session){
                            let mut gone=joined.remove(index);
                            let undecided=gone.local_consent.is_none();
                            if gone.take_close(){late_answer=Some(std::time::Instant::now());send(&mut socket,if undecided{ClientMessage::Decide{session,accept:false}}else{ClientMessage::Close{session}}).await?;}
                            let views=colleague_views(&joined);
                            state.change(generation,|status|status.colleagues=views).await;
                        }
                    },
                    Command::Invite{session,user,device,control}=>{
                        if let Some(active)=ctrl.iter().find(|active|(active.id==session || active.ui==session) && !active.joined && active.signals.is_some()){
                            invite_pending=true;
                            send(&mut socket,ClientMessage::Invite{session:active.id,user,device,control}).await?;
                        }
                    },
                    Command::Join(invitation,slot)=>{
                        if peer.is_some() || !ctrl.is_empty() || !outgoing.is_empty(){state.slot_end(generation,slot,"Finish the current session before joining a colleague".into()).await;continue;}
                        let Some((_,host,unattended,_))=invitations.iter().find(|offer|offer.0==invitation).copied() else {state.slot_end(generation,slot,"This invitation has expired".into()).await;continue};
                        invitations.retain(|offer|offer.0!=invitation);
                        let Some(credential) = credential.as_ref() else { return Err("A startup host cannot join sessions".into()); };
                        let key=match credential.ism.peer_key_scoped(&credential.token,credential.principal.is_personal(),host).await{Ok(key)=>{peer_keys.insert(host,(key,std::time::Instant::now()));key},Err(_)=>{state.slot_end(generation,slot,"This account could not verify the computer your colleague is helping.".into()).await;continue;}};
                        if cancel.load(Ordering::Acquire){break;}
                        send(&mut socket,ClientMessage::JoinSession{invitation}).await?;outgoing.push_back(Out{slot,host,key,unattended,joined:true,cancelled:false});
                        state.slot_change(generation,slot,|view|{view.phase="outgoing".into();view.message="Waiting for the person at the computer to let you join".into();}).await;
                    },
                    Command::SwitchSides(session)=>{
                        if let Some(active)=ctrl.iter().find(|active|joined.is_empty() && ctrl.len()==1 && (active.id==session || active.ui==session) && !active.joined && active.signals.is_some() && !active.cancel.load(Ordering::Acquire)){
                            let session=active.id;
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
                            late_answer=Some(std::time::Instant::now());
                            send(&mut socket,ClientMessage::Decide{session,accept:true}).await?;
                        } else {
                            let mut gone=joined.remove(index);gone.take_close();
                            late_answer=Some(std::time::Instant::now());
                            send(&mut socket,ClientMessage::Decide{session,accept:false}).await?;
                        }
                        let views=colleague_views(&joined);
                        state.change(generation,|status|status.colleagues=views).await;
                    },
                    Command::Restart(session)=>{
                        if let Some(active)=ctrl.iter_mut().find(|peer|(peer.id==session || peer.ui==session) && peer.signals.is_some() && !peer.cancel.load(Ordering::Acquire) && peer.restart_pending.is_none() && peer.rotated_at.elapsed()>=Duration::from_secs(31)){
                            send(&mut socket,ClientMessage::Restart{session:active.id}).await?;active.restart_pending=Some(std::time::Instant::now());
                        }
                    },
                    Command::Signal{session,signal}=>{if peer.as_ref().is_some_and(|peer|peer.id==session && !peer.cancel.load(Ordering::Acquire)) || ctrl.iter().any(|peer|peer.id==session && !peer.cancel.load(Ordering::Acquire)) || joined.iter().any(|colleague|colleague.id==session && !colleague.cancel.load(Ordering::Acquire)){send(&mut socket,ClientMessage::Signal{session,payload:signal}).await?;}},
                    Command::Finished{session,result}=>{if let Some(index)=joined.iter().position(|colleague|colleague.id==session){
                        let mut gone=joined.remove(index);
                        if gone.take_close(){send(&mut socket,ClientMessage::Close{session}).await?;}
                        if let Err(error)=&result{eprintln!("colleague session ended: {error}");}
                        let views=colleague_views(&joined);
                        state.change(generation,|status|status.colleagues=views).await;
                        continue;
                    }
                    if let Some(index)=ctrl.iter().position(|active|(active.id==session || active.ui==session) && !active.close_sent){
                        // This technician session's media ended (the peer left or failed): only this one ends.
                        let again=result.is_err() && !ctrl[index].joined && ctrl[index].signals.is_some() && !retries.iter().any(|active|active.host==ctrl[index].remote) && last_access.get(&ctrl[index].remote).is_some_and(crate::reconnect::Access::repeatable);
                        let mut over=ctrl.remove(index);over.take_close();remember_gone(&mut recent,over.id);remember_gone(&mut recent,session);
                        send(&mut socket,ClientMessage::Close{session:over.id}).await?;
                        let asking_again=again && begin_reconnect(state,generation,&mut retries,&last_access,over.ui).await;
                        if !asking_again {
                            state.slot_end(generation,over.ui,result.err().unwrap_or_else(||"Session closed; ready for a new connection".into())).await;
                        }
                        continue;
                    }
                    if peer.as_ref().is_some_and(|peer|peer.id==session && !peer.close_sent){peer=None;send(&mut socket,ClientMessage::Close{session}).await?;state.change(generation,|status|{status.phase="ready".into();status.session=None;status.peer=None;status.requester=None;status.message=result.err().unwrap_or_else(||"Session closed; ready for a new connection".into());}).await;}},
                }},
                incoming=event(&mut socket)=>{
                    let message=incoming?;last_activity=std::time::Instant::now();
                    let Some(message)=message else{continue;};
                    // Lab builds only: what the connection service sent (kind only, never contents).
                    #[cfg(feature = "qa-session")]
                    if !matches!(message,ServerMessage::Signal{..}|ServerMessage::Presence{..}){
                        let detail=match &message{ServerMessage::Closed{reason,..}=>format!(" {reason:?}"),ServerMessage::Error{code}=>format!(" {code:?}"),_=>String::new()};
                        eprintln!("QA broker sent {}{detail} at clock {}",match &message{ServerMessage::Ice{..}=>"Ice",ServerMessage::DeviceChallenge{..}=>"DeviceChallenge",ServerMessage::Ready{..}=>"Ready",ServerMessage::Renewed{..}=>"Renewed",ServerMessage::SupportCode{..}=>"SupportCode",ServerMessage::PairingStatus{..}=>"PairingStatus",ServerMessage::UnattendedStatus{..}=>"UnattendedStatus",ServerMessage::SupportHostStatus{..}=>"SupportHostStatus",ServerMessage::Presence{..}=>"Presence",ServerMessage::Agent{..}=>"Agent",ServerMessage::Requested{..}=>"Requested",ServerMessage::Invited{..}=>"Invited",ServerMessage::InviteStatus{..}=>"InviteStatus",ServerMessage::Switched{..}=>"Switched",ServerMessage::Consent{..}=>"Consent",ServerMessage::Accepted{..}=>"Accepted",ServerMessage::PeerRenewed{..}=>"PeerRenewed",ServerMessage::Restarted{..}=>"Restarted",ServerMessage::Signal{..}=>"Signal",ServerMessage::Closed{..}=>"Closed",ServerMessage::Error{..}=>"Error",_=>"other"},crate::remote_control::qa_wall_clock());
                    }
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
                                        else {remote_peer::start(remote_peer::Config{state:state.clone(),generation,session,slot:session,transport:session,link:Default::default(),previous:None,nonce:binding,host:true,login_window:session_platform::login_window_mode(),remote_key:colleague.key,identity:identity.clone(),source:colleague.source.clone(),#[cfg(target_os = "linux")] portal:colleague.portal.clone(),confirm:colleague.confirm,control:colleague.control,clipboard:false,files:false,chat:false,recording:false,consent_id:colleague.consent_id,consent_cancel:colleague.consent_cancel.clone(),consent_started:colleague.started_at,session_cancel:colleague.cancel.clone(),account_cancel:cancel.clone(),signals:signals_rx,commands:tx.clone(),video:video.clone(),ice_servers,relay_only:service.relay_only,authorization:broker_lease.clone(),peer_expires:colleague.expires.clone(),mirror:true}).map(|()|None)}
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
                        let Some(active)=find_peer(&mut peer,&mut ctrl,previous).filter(|peer|peer.signals.is_some() && !peer.cancel.load(Ordering::Acquire)) else{send(&mut socket,ClientMessage::Close{session}).await?;continue;};
                        let running=if active.host{state.status().await.phase=="active"}else{state.slot_active(previous).await};
                        if !running || active.started_at.elapsed()>=Duration::from_secs(8*3600){send(&mut socket,ClientMessage::Close{session}).await?;continue;}
                        // A transport replacement is confined to the already
                        // accepted device/account/source and existing grants.
                        // Common Accepted validation below pins fresh tickets.
                        active.begin_renewal(session);
                        if !active.host{match state.slot_video(active.ui).await{Some(own)=>own.expect_successor(active.ui,session)?,None=>return Err("This session has already closed".into())}}
                        #[cfg(feature = "qa-session")]
                        {QA_RENEWALS.fetch_add(1,Ordering::Relaxed);eprintln!("QA renewal: transport {previous} renewed as {session} at clock {}",crate::remote_control::qa_wall_clock());}
                        let (host_side,ui)=(active.host,active.ui);
                        if host_side{state.change(generation,|status|status.message="Renewing the connection keys; the session goes on".into()).await;}
                        else{state.slot_change(generation,ui,|view|view.message="Renewing the connection keys; the session goes on".into()).await;}
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
                                crate::agent::accept(command,unattended,device,enrolled.connection_id);
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
                                // u16::MAX means the password stays until the owner turns it off.
                                status.code_expires_at=if lifetime_seconds==u16::MAX {None} else {code.as_ref().map(|_|std::time::Instant::now()+Duration::from_secs(u64::from(lifetime_seconds)))};
                                status.support_code=code;
                            }).await;
                        },
                        ServerMessage::Requested{session}=>{
                            let waiting=outgoing.pop_front().ok_or("Unexpected outgoing invitation")?;
                            if waiting.cancelled || !state.slot_open(waiting.slot).await {
                                // The technician ended the request before the connection service named it.
                                remember_gone(&mut recent,session);late_answer=Some(std::time::Instant::now());
                                send(&mut socket,ClientMessage::Close{session}).await?;
                                state.slot_end(generation,waiting.slot,"Request cancelled; ready for a new connection".into()).await;
                                continue;
                            }
                            let Out{slot,host:remote,key,joined:joined_request,..}=waiting;
                            let started=Peer{id:session,ui:session,link:Default::default(),handover:None,remote,host:false,key,source:String::new(),#[cfg(target_os = "linux")] portal:None,confirm:false,control:false,clipboard:false,files:false,chat:false,recording:false,close_sent:false,consent_cancel:Arc::new(AtomicBool::new(false)),cancel:Arc::new(AtomicBool::new(false)),signals:None,early:Vec::new(),nonce:None,identity:None,expires:Arc::new(AtomicU64::new(0)),relay_expires:0,restart_pending:None,rotated_at:std::time::Instant::now(),started_at:std::time::Instant::now(),local_consent:None,accepted_at:None,consent_id:Uuid::new_v4(),joined:joined_request,switched:false,reversed:false};
                            if started.track(state,generation,slot).await.is_err() {
                                // Ended meanwhile: close it at the connection service, the others go on.
                                remember_gone(&mut recent,session);
                                send(&mut socket,ClientMessage::Close{session}).await?;
                                continue;
                            }
                            ctrl.push(started);
                        },
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
                            let mut colleague=Peer{id:session,ui:session,link:Default::default(),handover:None,remote:requester_connection,host:true,key,source,#[cfg(target_os = "linux")] portal,confirm,control:false,clipboard:false,files:false,chat:false,recording:false,close_sent:false,consent_cancel:Arc::new(AtomicBool::new(false)),cancel:Arc::new(AtomicBool::new(false)),signals:None,early:Vec::new(),nonce:None,identity:Some(ticket),expires,relay_expires:0,restart_pending:None,rotated_at:std::time::Instant::now(),started_at:std::time::Instant::now(),local_consent:None,accepted_at:None,consent_id:Uuid::new_v4(),joined:true,switched:false,reversed:false};
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
                                (Some(memory),Some(ticket)) if !crate::service_config::controller_only() && peer.is_none() && ctrl.is_empty() && outgoing.is_empty() => switched_identity(&ticket,requester_connection,&memory.identity,&roots).ok().filter(|key|*key==memory.key).map(|key|(key,ticket)),
                                _=>None,
                            };
                            let Some((key,ticket))=verified else {send(&mut socket,ClientMessage::Decide{session,accept:false}).await?;continue;};
                            let memory=memory.ok_or("Switch state unavailable")?;
                            let pressed=from.by==enrolled.connection_id && switch_pressed.take().is_some_and(|(previous,_)|previous==from.previous);
                            let expires=Arc::new(AtomicU64::new(ticket.expires_at));
                            peer=Some(Peer{id:session,ui:session,link:Default::default(),handover:None,remote:requester_connection,host:true,key,source:String::new(),#[cfg(target_os = "linux")] portal:None,confirm:false,control:false,clipboard:false,files:false,chat:false,recording:false,close_sent:false,consent_cancel:Arc::new(AtomicBool::new(false)),cancel:Arc::new(AtomicBool::new(false)),signals:None,early:Vec::new(),nonce:None,identity:Some(ticket),expires,relay_expires:0,restart_pending:None,rotated_at:std::time::Instant::now(),started_at:std::time::Instant::now(),local_consent:None,accepted_at:None,consent_id:Uuid::new_v4(),joined:false,switched:true,reversed:!memory.reversed});
                            peer.as_ref().ok_or("Invitation state unavailable")?.track(state,generation,session).await?;
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
                            let Some(memory)=memory.filter(|_|peer.is_none() && ctrl.is_empty() && outgoing.is_empty()) else {send(&mut socket,ClientMessage::Close{session}).await?;continue;};
                            switch_pressed=None;
                            let expires=Arc::new(AtomicU64::new(memory.identity.expires_at));
                            let slot=state.slot_begin(generation,host).await?;
                            ctrl.push(Peer{id:session,ui:session,link:Default::default(),handover:None,remote:host,host:false,key:memory.key,source:String::new(),#[cfg(target_os = "linux")] portal:None,confirm:false,control:false,clipboard:false,files:false,chat:false,recording:false,close_sent:false,consent_cancel:Arc::new(AtomicBool::new(false)),cancel:Arc::new(AtomicBool::new(false)),signals:None,early:Vec::new(),nonce:None,identity:Some(memory.identity.clone()),expires,relay_expires:0,restart_pending:None,rotated_at:std::time::Instant::now(),started_at:std::time::Instant::now(),local_consent:None,accepted_at:None,consent_id:Uuid::new_v4(),joined:false,switched:true,reversed:!memory.reversed});
                            ctrl.last().ok_or("Invitation state unavailable")?.track(state,generation,slot).await?;
                            state.slot_change(generation,slot,|view|{view.phase="outgoing".into();view.message="Switching sides: waiting for the other computer's picture".into();}).await;
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
                            crate::qa_timeline::begin(if unattended {"request came in (unattended)"} else {"request came in"});
                            if crate::service_config::controller_only() || peer.is_some() || !ctrl.is_empty() || !outgoing.is_empty(){send(&mut socket,ClientMessage::Decide{session,accept:false}).await?;continue;}
                            let ticket=requester_identity.ok_or("The invitation has no signed requester identity")?;
                            let key=peer_identity(&ticket,requester_connection,&principal,true,&roots)?;
                            if own_ticket.unattended_host_only() && !unattended {
                                send(&mut socket, ClientMessage::Decide { session, accept: false }).await?;
                                continue;
                            }
                            if ticket.identity.profile!=*requester.as_bytes(){return Err("The requester account does not match its signed identity".into());}
                            let status=state.status().await;
                            // Only the connection service says a request is unattended: it does so for a
                            // controller the policy service allows unattended access to this computer, or
                            // for a correct support password. A plain request it handles as attended is
                            // asked about here, also when unattended access is on (2.0.5-2.0.14 accepted
                            // every request without asking as soon as unattended access was on).
                            let is_unattended=unattended;
                            let auto=if is_unattended {
                                let Some(config)=automatic_source(&status, || {
                                    session_platform::main_display_source().map(|source| UnattendedSource {
                                        source,
                                        control: session_platform::input_access(),
                                    })
                                }) else { send(&mut socket,ClientMessage::Decide{session,accept:false}).await?;continue; };
                                Some(config)
                            }else{None};
                            let expires=Arc::new(AtomicU64::new(ticket.expires_at));
                            peer=Some(Peer{id:session,ui:session,link:Default::default(),handover:None,remote:requester_connection,host:true,key,source:String::new(),#[cfg(target_os = "linux")] portal:None,confirm:false,control:false,clipboard:false,files:false,chat:false,recording:false,close_sent:false,consent_cancel:Arc::new(AtomicBool::new(false)),cancel:Arc::new(AtomicBool::new(false)),signals:None,early:Vec::new(),nonce:None,identity:Some(ticket),expires,relay_expires:0,restart_pending:None,rotated_at:std::time::Instant::now(),started_at:std::time::Instant::now(),local_consent:None,accepted_at:None,consent_id:Uuid::new_v4(),joined:false,switched:false,reversed:false});
                            peer.as_ref().ok_or("Invitation state unavailable")?.track(state,generation,session).await?;
                            state.change(generation,|status|{status.phase="incoming".into();status.message="Incoming request: choose a source and Accept or Reject".into();status.peer=Some(requester_connection);status.requester=Some(requester);status.sharing=true;status.unattended_session=is_unattended;}).await;
                            if let Some(config)=auto {
                                let full=!session_platform::login_window_mode();
                                // The owner's choice for unattended sessions: view only stays view only.
                                let control=unattended_control(&config, session_platform::input_access());
                                let decision=Decision{session,accept:true,source:config.source,confirm:true,
                                    control,clipboard:full,files:full,chat:full,recording:full};
                                if state.decide(decision).await.is_err() {
                                    send(&mut socket,ClientMessage::Decide{session,accept:false}).await?;
                                }
                            }
                        },
                        ServerMessage::Accepted{session,nonce,controller_identity,host_identity}=>{
                            crate::qa_timeline::mark("accepted (connection service)");
                            let Some(active)=find_peer(&mut peer,&mut ctrl,session).filter(|peer|peer.signals.is_none()) else {if recently_gone(&recent,session){continue;}return Err("Unexpected accepted invitation".into());};
                            if active.cancel.load(Ordering::Acquire){if active.take_close(){send(&mut socket,ClientMessage::Close{session}).await?;}continue;}
                            if !active.can_negotiate(std::time::Instant::now()){return Err("A broker response cannot grant or extend local sharing consent".into());}
                            active.accepted_at.get_or_insert_with(std::time::Instant::now);
                            let host_role=active.host;
                            let active_ui=active.ui;
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
                            if checked!=active.key && !active.host && !active.switched
                                && let Some(credential) = credential.as_ref()
                                && let Ok(fresh_key) = credential.ism.peer_key_scoped(&credential.token, credential.principal.is_personal(), active.remote).await
                                && fresh_key == checked
                            {
                                peer_keys.insert(active.remote, (fresh_key, std::time::Instant::now()));
                                active.key = fresh_key;
                            }
                            if checked!=active.key || active.identity.as_ref().is_some_and(|known|!same_identity(known,remote)){return Err("Peer identity changed; request a fresh connection".into());}
                            active.expires.store(remote.expires_at,Ordering::Release);
                            active.identity=Some(remote.clone());
                            if active.cancel.load(Ordering::Acquire) || cancel.load(Ordering::Acquire){if active.take_close(){send(&mut socket,ClientMessage::Close{session}).await?;}continue;}
                            auth.credential(Some(&active.cancel)).await?;
                            let binding=core_protocol::delegation::session_binding(*session.as_bytes(),nonce,&controller,&host);
                            if active.nonce.replace(binding).is_some(){return Err("Duplicate accepted invitation".into());}
                            // A renewal: the session stays active (the transport before still shares), so the
                            // window keeps its picture and tools and input goes on.
                            if !active.renewing(){
                                if host_role{state.change(generation,|status|{status.phase="negotiating".into();status.message="Accepted; obtaining authenticated short-lived relay configuration".into();}).await;}
                                else{state.slot_change(generation,active_ui,|view|{view.phase="negotiating".into();view.message="Accepted; obtaining authenticated short-lived relay configuration".into();}).await;}
                            }
                            send(&mut socket,ClientMessage::Ice{session}).await?;
                        },
                        ServerMessage::Ice{session,lease}=>{
                            let Some(active)=find_peer(&mut peer,&mut ctrl,session).filter(|peer|peer.signals.is_none()) else {if recently_gone(&recent,session){continue;}return Err("Unexpected relay configuration".into());};
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
                            // A renewal: the transport before goes on until this one takes over. Only this session's
                            // previous transport is waited for: the other sessions keep running.
                            let slot=if active.host{None}else{state.slot_id(active.ui).await};
                            if active.handover.is_none(){state.finish_previous_media(slot).await?;}
                            if active.cancel.load(Ordering::Acquire) || cancel.load(Ordering::Acquire){if active.take_close(){send(&mut socket,ClientMessage::Close{session}).await?;}continue;}
                            let session_video=if active.host{video.clone()}else{match state.slot_video(active.ui).await{Some(own)=>own,None=>{if active.take_close(){send(&mut socket,ClientMessage::Close{session}).await?;}continue;}}};
                            remote_peer::start(remote_peer::Config{state:state.clone(),generation,session:active.ui,slot:slot.unwrap_or(active.ui),transport:session,link:active.link.clone(),previous:active.handover.take(),nonce,host:active.host,login_window:session_platform::login_window_mode(),remote_key:active.key,identity:identity.clone(),source:active.source.clone(),#[cfg(target_os = "linux")] portal:active.portal.clone(),confirm:active.confirm,control:active.control,clipboard:active.clipboard,files:active.files,chat:active.chat,recording:active.recording,consent_id:active.consent_id,consent_cancel:active.consent_cancel.clone(),consent_started:active.started_at,session_cancel:active.cancel.clone(),account_cancel:cancel.clone(),signals:signals_rx,commands:tx.clone(),video:session_video,ice_servers,relay_only:service.relay_only,authorization:broker_lease.clone(),peer_expires:active.expires.clone(),mirror:false})?;
                        },
                        ServerMessage::PeerRenewed{session,identity:renewed}=>{
                            let Some(active)=find_peer(&mut peer,&mut ctrl,session) else {if recently_gone(&recent,session){continue;}return Err("Unexpected peer authorization renewal".into());};
                            let previous=active.identity.as_ref().ok_or("Peer identity has not been authenticated")?;
                            let key=if active.switched{switched_identity(&renewed,active.remote,previous,&roots)?}else{peer_identity(&renewed,active.remote,&principal,active.host,&roots)?};
                            if key!=active.key || !same_identity(previous,&renewed){return Err("Peer account, enrollment or authorization changed".into());}
                            if renewed.issued_at>=previous.issued_at && renewed.expires_at>=previous.expires_at{active.expires.store(renewed.expires_at,Ordering::Release);active.identity=Some(renewed);}
                        },
                        ServerMessage::Signal{session,payload}=>{let Some(active)=find_peer(&mut peer,&mut ctrl,session) else {if recently_gone(&recent,session){continue;}return Err("Unexpected peer signaling".into());};if active.cancel.load(Ordering::Acquire){continue;}match active.signals.as_ref(){Some(signals)=>signals.try_send(payload).map_err(|_|"Peer signaling queue overflowed")?,None if active.nonce.is_some() && active.early.len()<32=>{/* accepted, relay configuration still on its way: keep for the media task */active.early.push(payload);},None=>return Err("Media negotiation is unavailable".into())}},
                        ServerMessage::Closed{session,reason:signaling_server::protocol::CloseReason::Switched} if peer.as_ref().is_some_and(|active|active.id==session && active.identity.is_some()) || ctrl.iter().any(|active|active.id==session && active.identity.is_some())=>{
                            let ended=match peer.as_ref().is_some_and(|active|active.id==session){true=>peer.take().ok_or("Session state unavailable")?,false=>{let index=ctrl.iter().position(|active|active.id==session).ok_or("Session state unavailable")?;ctrl.remove(index)}};
                            // Grants of the person at this computer survive a switch and its switch back.
                            let grants=if ended.host && !ended.switched {
                                Some(HostGrants{source:ended.source.clone(),confirm:ended.confirm,control:ended.control,clipboard:ended.clipboard,files:ended.files,chat:ended.chat,recording:ended.recording})
                            } else {
                                switch_memory.take().filter(|memory|memory.key==ended.key).and_then(|memory|memory.grants)
                            };
                            switch_memory=Some(SwitchMemory{previous:session,remote:ended.remote,key:ended.key,identity:ended.identity.clone().ok_or("Peer identity unavailable")?,grants,reversed:ended.reversed,at:std::time::Instant::now()});
                            if ended.host{state.change(generation,|status|{status.phase="ready".into();status.message="Switching sides".into();status.session=None;status.peer=Some(ended.remote);status.requester=None;}).await;}
                            else{state.slot_end(generation,ended.ui,"Switching sides".into()).await;}
                        },
                        ServerMessage::Closed{session,reason:signaling_server::protocol::CloseReason::Disconnected} if ctrl.iter().any(|active|(active.id==session || active.ui==session) && !active.joined && active.signals.is_some() && !retries.iter().any(|again|again.host==active.remote) && last_access.get(&active.remote).is_some_and(crate::reconnect::Access::repeatable))=>{
                            // The computer this session views went away: only this session asks again.
                            if let Some(index)=ctrl.iter().position(|active|active.id==session || active.ui==session){
                                let over=ctrl.remove(index);remember_gone(&mut recent,over.id);
                                begin_reconnect(state,generation,&mut retries,&last_access,over.ui).await;
                            }
                        },
                        ServerMessage::Closed{session,reason}=>{
                            let text=if reason==signaling_server::protocol::CloseReason::Expired{"The support session reached its 2-hour limit.".to_owned()}else{format!("Session closed: {reason:?}")};
                            if peer.as_ref().is_some_and(|peer|peer.id==session || peer.ui==session){peer=None;state.change(generation,|status|{status.phase="ready".into();status.message=text;status.session=None;status.peer=None;status.requester=None;}).await;}
                            else if let Some(index)=ctrl.iter().position(|active|active.id==session || active.ui==session){let over=ctrl.remove(index);remember_gone(&mut recent,over.id);state.slot_end(generation,over.ui,text).await;}
                        },
                        ServerMessage::Error{code} if !resuming.is_empty()=>{
                            // Not known any more (older connection service, or the 90 seconds passed): the session that was
                            // asked first and not yet answered ends here, alone (answers come in the order asked).
                            let _=code;
                            let first=resuming[0].0;
                            end_resumed(state,generation,&mut peer,&mut ctrl,&mut resuming,&mut end_when_resumed,first,"The session ended while the connection service restarted").await;
                        },
                        ServerMessage::Error{code}=>{
                            if let Some(Out{slot,host,key,unattended:was_unattended,joined,cancelled})=outgoing.pop_front(){
                                // A computer with unattended access accepts this. One without it
                                // still gets the usual Accept prompt, instead of a hard failure.
                                if was_unattended && !joined && !cancelled && code==ErrorCode::Unavailable {
                                    crate::qa_timeline::mark("unattended request refused (Unavailable): asking again as an attended request");
                                    send(&mut socket,ClientMessage::Request{host}).await?;
                                    outgoing.push_front(Out{slot,host,key,unattended:false,joined:false,cancelled:false});
                                    state.slot_change(generation,slot,|view|{view.phase="outgoing".into();view.unattended_session=false;view.message="Waiting for the remote host to Accept".into();}).await;
                                } else if retries.iter().any(|again|again.host==host) {
                                    // A retry of a lost session: the next ask follows, the window stays as it is.
                                } else {
                                    state.slot_end(generation,slot,request_failure_message(code).into()).await;
                                }
                            }
                            else if std::mem::take(&mut invite_pending){
                                state.change(generation,|status|status.invite_message=Some(match code {ErrorCode::Busy=>"No more colleagues can join this session right now",ErrorCode::Unavailable=>"The computer you are helping cannot receive colleagues (it needs a newer Mixel Remote)",_=>"The invitation could not be sent"}.into())).await;
                            }
                            else if switch_pressed.take().is_some(){
                                state.change(generation,|status|status.message=match code {ErrorCode::Busy=>"Sides cannot be switched while a colleague is connected",ErrorCode::Unavailable=>"The other side needs a newer Mixel Remote to switch sides",_=>"Sides could not be switched"}.into()).await;
                            }
                            else if code==ErrorCode::Forbidden && late_answer.take().is_some_and(|at|at.elapsed()<Duration::from_secs(15)){
                                // The request had already ended at the connection service; nothing to undo.
                            }
                            else if code==ErrorCode::Invalid && capabilities_sent.elapsed()<Duration::from_secs(15) && peer.is_none() && ctrl.is_empty(){
                                // An older connection service: no colleagues, no switching sides.
                            }
                            else if peer.as_ref().is_some_and(|peer|peer.nonce.is_some()) && pending_renewal.is_none(){
                                let session=peer.take().ok_or("Session state unavailable")?.id;
                                send(&mut socket,ClientMessage::Close{session}).await?;
                                state.change(generation,|status|{status.phase="ready".into();status.peer=None;status.session=None;status.requester=None;status.message="The relay service is unavailable; screen sharing has not started".into();}).await;
                            }
                            else if pending_renewal.is_none() && let Some(index)=ctrl.iter().position(|active|active.nonce.is_some()){
                                // The relay service refused one technician session: only that one ends.
                                let mut over=ctrl.remove(index);over.take_close();let session=over.id;remember_gone(&mut recent,session);
                                send(&mut socket,ClientMessage::Close{session}).await?;
                                state.slot_end(generation,session,"The relay service is unavailable; screen sharing has not started".into()).await;
                            }else if pending_renewal.take().is_some(){
                                return Err(Failure::network("Device authorization renewal was refused; reconnecting"));
                            }else{return Err(format!("Connection service rejected an operation: {code:?}").into());}
                        },
                        ServerMessage::SessionEnding{session,seconds,..}=>{
                            // D2: a session opened with a support password ends 2 hours after it connected. The warning
                            // belongs to that session: a technician's other sessions do not show it.
                            let ends=std::time::Instant::now()+Duration::from_secs(u64::from(seconds));
                            let technician=ctrl.iter().position(|active|active.id==session || active.ui==session);
                            if let Some(index)=technician{
                                state.slot_change(generation,ctrl[index].ui,|view|{view.password_ends=Some(ends);view.message=ending_message(seconds);}).await;
                            }
                            // The single-session fields follow the hosted session, or the oldest technician session.
                            if technician.is_none_or(|index|index==0){
                                state.change(generation,|status|{status.password_ends=Some(ends);status.message=ending_message(seconds);}).await;
                            }
                        },
                        ServerMessage::DevicePolicy{unattended_allowed,..}=>{
                            state.change(generation,|status|status.unattended_allowed=Some(unattended_allowed)).await;
                        },
                        ServerMessage::Resumed{session} if resuming_id(&resuming,&peer,&ctrl,session).is_some()=>{
                            let id=resuming_id(&resuming,&peer,&ctrl,session).unwrap_or(session);
                            resuming.retain(|(waiting,_)|*waiting!=id);
                            let technician=ctrl.iter().find(|active|active.id==id).map(|active|active.ui);
                            let still=!resuming.is_empty()||!retries.is_empty();
                            if end_when_resumed.contains(&id) {
                                end_when_resumed.retain(|waiting|*waiting!=id);
                                if let Some(ui)=technician{
                                    if let Some(index)=ctrl.iter().position(|active|active.ui==ui){let over=ctrl.remove(index);remember_gone(&mut recent,over.id);}
                                    send(&mut socket,ClientMessage::Close{session:id}).await?;
                                    state.change(generation,|status|status.reconnecting=still).await;
                                    state.slot_end(generation,ui,"Session ended".into()).await;
                                } else {
                                    peer=None;
                                    send(&mut socket,ClientMessage::Close{session:id}).await?;
                                    state.change(generation,|status|{status.reconnecting=still;status.phase="ready".into();status.session=None;status.peer=None;status.requester=None;status.message="Session ended".into();}).await;
                                }
                            } else {
                                state.change(generation,|status|{status.reconnecting=still;status.message="Reconnected; the session continues".into();}).await;
                                if let Some(ui)=technician{state.slot_change(generation,ui,|view|{view.reconnecting=false;view.message="Reconnected; the session continues".into();}).await;}
                            }
                        },
                        ServerMessage::Resumed{..}=>{},
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
    // The connection to the connection service is lost but sessions' media connections are up: keep each of them
    // for the next registration, which asks the connection service to resume every one on its own (nothing is
    // cancelled). A session that cannot be resumed ends alone; the others still go on.
    if let Err(failure) = &result
        && failure.retryable
        && ready_at.is_some()
        && !cancel.load(Ordering::Acquire)
        && (peer.as_ref().is_some_and(Peer::resumable) || ctrl.iter().any(Peer::resumable))
    {
        let mut kept: Vec<Peer> = Vec::new();
        if let Some(hosted) = peer.take().filter(Peer::resumable) {
            kept.push(hosted);
        }
        for session in ctrl.drain(..) {
            if session.resumable() { kept.push(session); }
        }
        *keep = Some(Carry { peers: kept, attempt: cancel.clone(), lease: broker_lease.clone(), since: carried_since.unwrap_or_else(std::time::Instant::now) });
        let _ = tokio::time::timeout(Duration::from_secs(2), socket.close(None)).await;
        return result;
    }
    cancel.store(true, Ordering::Release);
    let _ = tokio::time::timeout(Duration::from_secs(2), socket.close(None)).await;
    result
}

/// A colleague's join of an unattended session is accepted without asking only while this computer
/// itself allows unattended access and the running session is an unattended one. The broker's
/// `unattended` flag alone is not enough: it must never turn an attended session (someone at the
/// computer decides) into one a colleague enters silently.
fn unattended_join_allowed(status: &Status) -> bool {
    status.unattended_enabled && status.unattended_session
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
        ] {
            let mut refused = status.clone();
            change(&mut refused);
            assert!(!unattended_join_allowed(&refused));
        }
    }
}

#[cfg(test)]
mod registration_tests {
    use super::*;

    fn go_online(remote: &RemoteSession) -> impl Future<Output = Result<(), String>> + '_ {
        remote.connect(AccountAuth::default(), Arc::new(IdentityState::default()))
    }

    /// "Add this computer" and the app's own going online both ask to register. The second request
    /// must not be reported as an error ("This device is already registered or connecting") while
    /// the computer is online: it changes nothing and succeeds.
    #[tokio::test]
    async fn going_online_again_while_online_or_connecting_succeeds_and_changes_nothing() {
        let remote = RemoteSession::default();
        let (commands, _kept) = mpsc::channel(1);
        {
            let mut inner = remote.0.lock().await;
            inner.commands = Some(commands);
            inner.generation = 7;
            inner.status.phase = "ready".into();
            inner.status.message = "Online".into();
        }
        assert_eq!(go_online(&remote).await, Ok(()));
        assert_eq!(go_online(&remote).await, Ok(()));
        let inner = remote.0.lock().await;
        assert_eq!(inner.generation, 7, "no second registration was started");
        assert!(inner.commands.is_some() && inner.task.is_none());
        assert_eq!((inner.status.phase.as_str(), inner.status.message.as_str()), ("ready", "Online"));
    }

    /// A device that is offline still goes through the real checks (here: no device identity).
    #[tokio::test]
    async fn going_online_while_offline_still_registers() {
        let remote = RemoteSession::default();
        assert_eq!(go_online(&remote).await.unwrap_err(), "Load the saved device identity first");
        assert!(remote.0.lock().await.commands.is_none());
    }

    /// While a disconnect is still cleaning up, and for the headless hosts, the refusal stays.
    #[tokio::test]
    async fn going_online_during_cleanup_and_a_second_headless_host_are_still_refused() {
        let remote = RemoteSession::default();
        let (commands, _kept) = mpsc::channel(1);
        {
            let mut inner = remote.0.lock().await;
            inner.commands = Some(commands);
            assert!(inner.already_online(true));
            assert!(!inner.already_online(false), "a second always-on host is refused");
            inner.stopping = true;
            assert!(!inner.already_online(true));
        }
        assert_eq!(go_online(&remote).await.unwrap_err(), "This device is already registered or connecting");
        remote.0.lock().await.commands = None;
        assert_eq!(go_online(&remote).await.unwrap_err(), "This device is already registered or connecting");
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
    #[cfg_attr(any(target_os = "ios", target_os = "android"), ignore = "host side only: phones are controllers")]
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
    #[cfg_attr(any(target_os = "ios", target_os = "android"), ignore = "host side only: phones are controllers")]
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
            ui: Uuid::from_u128(1),
            link: Default::default(),
            handover: None,
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
    fn a_renewal_keeps_the_running_transport_and_the_session_until_the_new_one_takes_over() {
        let mut peer = invitation();
        peer.host = false;
        let (ui, cancel, consent) = (peer.ui, peer.cancel.clone(), peer.consent_cancel.clone());
        let running = peer.link.clone();
        let renewed = Uuid::from_u128(3);
        peer.begin_renewal(renewed);
        // The service's ID moves on; the session as the person sees it, its consent and its stop
        // flags stay, and nothing is stopped yet.
        assert_eq!((peer.id, peer.ui), (renewed, ui));
        assert!(Arc::ptr_eq(&peer.cancel, &cancel) && Arc::ptr_eq(&peer.consent_cancel, &consent));
        assert!(!cancel.load(Ordering::Acquire) && !consent.load(Ordering::Acquire));
        let before = peer.handover.as_ref().expect("the running transport is handed over");
        assert!(Arc::ptr_eq(&before.retire, &running.retire));
        assert!(running.superseded.load(Ordering::Acquire), "its end no longer ends the session");
        assert!(!running.retire.load(Ordering::Acquire), "it keeps sharing until the new one takes over");
        assert!(!Arc::ptr_eq(&peer.link.retire, &running.retire), "the renewed transport has its own flags");
        assert!(peer.signals.is_none() && peer.nonce.is_none() && peer.can_negotiate(std::time::Instant::now()));
        assert!(peer.renewing(), "the window keeps seeing an active session");
        // A second renewal before the first one's transport started: the one sharing stays.
        let unstarted = peer.link.clone();
        peer.begin_renewal(Uuid::from_u128(4));
        assert!(Arc::ptr_eq(&peer.handover.as_ref().unwrap().retire, &running.retire));
        assert!(unstarted.superseded.load(Ordering::Acquire));
        // Ending the session stops both transports (they share the session's stop flag).
        drop(peer);
        assert!(cancel.load(Ordering::Acquire) && consent.load(Ordering::Acquire));
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
            .try_send(Command::Request(123456789, None, false, Uuid::new_v4()))
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
    async fn input_while_a_renewed_transport_takes_over_is_not_delivered_rather_than_failing() {
        let remote = RemoteSession::default();
        let session = Uuid::new_v4();
        let (input, receiver) = mpsc::channel(1);
        {
            let mut inner = remote.0.lock().await;
            inner.status.phase = "active".into();
            inner.status.session = Some(session);
            inner.status.control = true;
            inner.session_cancel = Some(Arc::new(AtomicBool::new(false)));
            inner.input = Some(input);
        }
        // The transport before ended ahead of the renewed one (an older host stops at once).
        drop(receiver);
        assert_eq!(remote.input(session, InputEvent::ReleaseAll).await, Ok(false));
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
    #[cfg_attr(any(target_os = "ios", target_os = "android"), ignore = "host side only: phones are controllers")]
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
            .try_send(Command::Request(123456789, None, false, Uuid::new_v4()))
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

    #[cfg(not(feature = "testflight-controller"))]
    #[tokio::test]
    #[cfg_attr(any(target_os = "ios", target_os = "android"), ignore = "host side only: phones are controllers")]
    async fn unattended_session_auto_accepts_with_full_tools_and_control() {
        let Some(source) = session_platform::main_display_source() else {
            return;
        };
        let remote = RemoteSession::default();
        let id = Uuid::new_v4();
        let (commands, mut receiver) = mpsc::channel(4);
        remote
            .track(
                0,
                id,
                Arc::new(AtomicBool::new(false)),
                Uuid::new_v4(),
                Arc::new(AtomicBool::new(false)),
                false,
                false,
            )
            .await
            .unwrap();
        {
            let mut inner = remote.0.lock().await;
            inner.commands = Some(commands);
            inner.status.phase = "incoming".into();
            inner.status.session = Some(id);
            inner.status.unattended_enabled = true;
            inner.status.unattended_session = true;
            inner.status.unattended = Some(UnattendedSource {
                source: source.clone(),
                control: false,
            });
        }
        let control = session_platform::input_access();
        remote
            .decide(Decision {
                session: id,
                accept: true,
                source: source.clone(),
                confirm: true,
                control,
                clipboard: true,
                files: true,
                chat: true,
                recording: true,
            })
            .await
            .unwrap();
        assert!(matches!(
            receiver.recv().await,
            Some(Command::Decide {
                accept: true,
                clipboard: true,
                files: true,
                chat: true,
                recording: true,
                ..
            })
        ));
    }

}

#[cfg(all(test, any(target_os = "macos", target_os = "linux", target_os = "windows")))]
#[path = "remote_session_native_test.rs"]
mod remote_session_native_test;

/// What an unattended request (the connection service said so: an allowed controller or a correct
/// support password) is accepted with, without asking: the source the owner chose, else the main
/// display; nothing when neither unattended access nor a support password is on here.
fn automatic_source(status: &Status, main_display: impl FnOnce() -> Option<UnattendedSource>) -> Option<UnattendedSource> {
    if !(status.unattended_enabled || status.code_required) {
        return None;
    }
    status
        .unattended
        .clone()
        .filter(UnattendedSource::capture_allowed)
        .or_else(|| main_display().filter(UnattendedSource::capture_allowed))
}

/// Keyboard and mouse in an unattended session: only when the owner allowed control for it and
/// this computer can apply input (2.0.5-2.0.14 gave control whenever input was possible).
fn unattended_control(config: &UnattendedSource, input_access: bool) -> bool {
    config.control && config.source != "qa-relay-fixture" && input_access
}

#[cfg(test)]
mod automatic_tests {
    use super::*;

    fn source(control: bool) -> UnattendedSource {
        UnattendedSource { source: "qa-relay-fixture".into(), control }
    }

    #[test]
    fn nothing_is_accepted_without_asking_unless_unattended_or_a_password_is_on() {
        let status = Status::default();
        assert!(automatic_source(&status, || Some(source(true))).is_none());
        let status = Status { unattended: Some(source(true)), ..Status::default() };
        assert!(automatic_source(&status, || Some(source(true))).is_none(), "a source alone is not consent");
    }

    #[test]
    fn view_only_unattended_access_never_gives_control() {
        let display = UnattendedSource { source: "display:1".into(), control: false };
        assert!(!unattended_control(&display, true));
        let display = UnattendedSource { source: "display:1".into(), control: true };
        assert!(unattended_control(&display, true));
        assert!(!unattended_control(&display, false));
    }
}

/// The owner's choice about unattended access on this computer, kept across restarts: a file
/// `unattended-off` in the app's folder for the OS user while it is turned off.
/// The app's own data folder (device key store location, command log, seen ids).
pub(crate) fn app_folder() -> Option<std::path::PathBuf> {
    unattended_choice::folder()
}

mod unattended_choice {
    use std::path::{Path, PathBuf};

    pub(super) fn folder() -> Option<PathBuf> {
        // Unit tests never touch the real app's folder of the person running them.
        #[cfg(test)]
        {
            Some(std::env::temp_dir().join(format!("mixel-unattended-tests-{}", std::process::id())))
        }
        #[cfg(all(not(test), target_os = "windows"))]
        {
            crate::device_identity::windows_store_location(None)
                .ok()
                .and_then(|path| path.parent().map(Path::to_path_buf))
        }
        #[cfg(all(not(test), target_os = "macos"))]
        {
            std::env::var_os("HOME").map(|home| PathBuf::from(home).join("Library/Application Support/Mixel Remote"))
        }
        #[cfg(all(not(test), not(any(target_os = "windows", target_os = "macos"))))]
        {
            std::env::var_os("XDG_CONFIG_HOME")
                .map(PathBuf::from)
                .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".config")))
                .map(|base| base.join("mixel-remote"))
        }
    }

    pub(super) fn turned_off() -> bool {
        folder().is_some_and(|folder| turned_off_in(&folder))
    }

    pub(super) fn remember(off: bool) {
        if let Some(folder) = folder() {
            remember_in(&folder, off);
        }
    }

    fn marker(folder: &Path) -> PathBuf {
        folder.join("unattended-off")
    }

    pub(super) fn turned_off_in(folder: &Path) -> bool {
        marker(folder).is_file()
    }

    pub(super) fn remember_in(folder: &Path, off: bool) {
        if off {
            let _ = std::fs::create_dir_all(folder);
            let _ = std::fs::write(marker(folder), b"1");
        } else {
            let _ = std::fs::remove_file(marker(folder));
        }
    }

    #[cfg(test)]
    mod tests {
        #[test]
        fn turning_unattended_access_off_is_kept_until_it_is_turned_on() {
            let folder = std::env::temp_dir().join(format!("mixel-unattended-choice-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&folder);
            assert!(!super::turned_off_in(&folder), "on by default");
            super::remember_in(&folder, true);
            assert!(super::turned_off_in(&folder), "off is kept (a restart reads it again)");
            super::remember_in(&folder, true);
            assert!(super::turned_off_in(&folder));
            super::remember_in(&folder, false);
            assert!(!super::turned_off_in(&folder), "on again");
            let _ = std::fs::remove_dir_all(&folder);
        }
    }
}

/// Several technician sessions at once: the state machine the registration loop drives, run here with
/// the loop's own calls (the broker side is covered by `signaling_server/tests/websocket_flow.rs` and
/// the lab run). Four sessions, each with its own pictures, input, clipboard and drawing, started, ended
/// and lost in mixed order.
#[cfg(test)]
mod multi_session_tests {
    use super::*;

    struct Open {
        id: Uuid,
        session: Uuid,
        cancel: Arc<AtomicBool>,
        /// Key usages the host side of this session received.
        seen: mpsc::UnboundedReceiver<u16>,
        draw: mpsc::Receiver<core_protocol::annotation::Message>,
    }

    async fn registered() -> (RemoteSession, mpsc::Receiver<Command>) {
        let remote = RemoteSession::default();
        let (tx, rx) = mpsc::channel(64);
        {
            let mut inner = remote.0.lock().await;
            inner.commands = Some(tx);
            inner.status.phase = "ready".into();
            inner.status.host = Some(111_111_111);
        }
        (remote, rx)
    }

    /// What the loop does when the broker answers and the media task grants the session.
    async fn open(remote: &RemoteSession, commands: &mut mpsc::Receiver<Command>, device: u32) -> Open {
        let id = remote.request(device, None, false).await.unwrap();
        let Some(Command::Request(host, _, _, slot)) = commands.recv().await else { panic!("request expected") };
        assert_eq!((host, slot), (device, id));
        let session = Uuid::new_v4();
        let cancel = Arc::new(AtomicBool::new(false));
        remote
            .slot_track(0, id, session, cancel.clone(), Uuid::new_v4(), Arc::new(AtomicBool::new(false)), false, false)
            .await
            .unwrap();
        let (input_tx, mut input) = mpsc::channel::<InputCommand>(8);
        let (seen_tx, seen) = mpsc::unbounded_channel();
        // The host side of the session: confirms every input and reports what it was.
        tokio::spawn(async move {
            while let Some(command) = input.recv().await {
                if let InputEvent::Key { usage, .. } = command.event {
                    let _ = seen_tx.send(usage);
                }
                let _ = command.reply.send(Ok(true));
            }
        });
        let (clip_tx, _clip) = mpsc::channel(1);
        let (draw_tx, draw) = mpsc::channel(8);
        remote.grant(0, session, true, Some(input_tx), true, Some(clip_tx)).await;
        remote.set_drawing(0, session, draw_tx).await;
        remote.update_peer(0, session, "Remote screen shared", 1).await;
        Open { id, session, cancel, seen, draw }
    }

    fn key(usage: u16) -> InputEvent {
        InputEvent::Key { usage, down: true }
    }

    fn lands(open: &mut Open, usage: u16) -> bool {
        open.seen.try_recv() == Ok(usage)
    }

    #[tokio::test]
    async fn four_sessions_run_side_by_side_and_a_fifth_fails_with_a_typed_error() {
        let (remote, mut commands) = registered().await;
        let mut sessions = Vec::new();
        for n in 0..4 {
            sessions.push(open(&remote, &mut commands, 200_000_001 + n).await);
        }
        let status = remote.status().await;
        assert_eq!(status.sessions.len(), 4);
        assert_eq!(status.max_sessions, 4);
        assert!(status.sessions.iter().all(|view| view.phase == "active" && view.control));
        let error = remote.request(200_000_099, None, false).await.unwrap_err();
        let json: serde_json::Value = serde_json::from_str(&error).unwrap();
        assert_eq!(json["hint"], "too_many_sessions");
        assert_eq!(remote.status().await.sessions.len(), 4, "the refused request left nothing behind");
        assert!(commands.try_recv().is_err(), "and sent nothing to the broker");
        // The same device twice is refused as well.
        let again = remote.request(200_000_001, None, false).await.unwrap_err();
        assert!(again.contains("already_connected") || again.contains("too_many_sessions"));
        drop(sessions);
    }

    #[tokio::test]
    async fn input_clipboard_drawing_and_pictures_stay_with_their_own_session() {
        let (remote, mut commands) = registered().await;
        let mut sessions = Vec::new();
        for n in 0..4 {
            sessions.push(open(&remote, &mut commands, 200_000_001 + n).await);
        }
        // Input goes to the session named, by stable id or by the broker's current id, and to no other.
        for index in 0..4 {
            let usage = 4 + index as u16;
            let named = if index % 2 == 0 { sessions[index].id } else { sessions[index].session };
            assert!(remote.input(named, key(usage)).await.unwrap());
            for (other, session) in sessions.iter_mut().enumerate() {
                assert_eq!(lands(session, usage), other == index, "key {usage} for session {index}, seen by {other}");
            }
        }
        // Drawings the same way.
        let clear = core_protocol::annotation::Message::Clear;
        assert!(remote.draw(sessions[2].id, clear).await.unwrap());
        assert!(sessions[2].draw.try_recv().is_ok());
        for other in [0, 1, 3] {
            assert!(sessions[other].draw.try_recv().is_err());
        }
        // Each session has its own picture mailbox: a picture of one never shows in another. (The
        // window asks for compressed pictures: each is kept, in order, for that session only.)
        for session in sessions.iter() {
            assert!(remote.next_video(session.id, true).await.unwrap().is_empty());
        }
        for (index, session) in sessions.iter().enumerate() {
            let video = remote.slot_video(session.id).await.unwrap();
            let packet = crate::remote_video::compressed_packet(session.session, 1, 10, &[index as u8 + 1; 8], true).unwrap();
            assert!(video.publish_compressed(packet).unwrap());
        }
        for index in [3, 1, 0, 2] {
            let picture = remote.next_video(sessions[index].id, true).await.unwrap();
            assert_eq!(&picture[picture.len() - 8..], &[index as u8 + 1; 8], "session {index} gets its own picture");
            assert!(remote.next_video(sessions[index].session, true).await.unwrap().is_empty(), "and only once");
        }
        // Picture figures per session.
        remote.update_peer(0, sessions[1].session, "Remote screen shared", 40).await;
        let status = remote.status().await;
        assert_eq!(status.sessions[1].quality.pictures, 40);
        assert_eq!(status.sessions[0].quality.pictures, 1);
    }

    #[tokio::test]
    async fn sessions_end_and_get_lost_in_mixed_order_without_touching_the_others() {
        let (remote, mut commands) = registered().await;
        let mut sessions = Vec::new();
        for n in 0..4 {
            sessions.push(open(&remote, &mut commands, 200_000_001 + n).await);
        }
        // The technician closes the third tab: only that session stops.
        remote.close(sessions[2].id).await.unwrap();
        assert!(matches!(commands.recv().await, Some(Command::Close(id)) if id == sessions[2].session));
        assert!(sessions[2].cancel.load(Ordering::Acquire));
        assert!(!remote.input(sessions[2].id, key(9)).await.unwrap(), "no input reaches a closing session");
        let status = remote.status().await;
        let phases: Vec<&str> = status.sessions.iter().map(|view| view.phase.as_str()).collect();
        assert_eq!(phases, ["active", "active", "closing", "active"]);
        for other in [0, 1, 3] {
            assert!(!sessions[other].cancel.load(Ordering::Acquire));
            assert!(remote.input(sessions[other].id, key(10)).await.unwrap());
            assert!(lands(&mut sessions[other], 10));
        }
        // The broker confirms: the session leaves the list, the others stay.
        remote.slot_end(0, sessions[2].session, "Session closed".into()).await;
        assert_eq!(remote.status().await.sessions.len(), 3);
        // One is lost (its peer stopped): again only that one.
        remote.slot_end(0, sessions[0].session, "The other computer left".into()).await;
        let status = remote.status().await;
        assert_eq!(status.sessions.iter().map(|view| view.id).collect::<Vec<_>>(), vec![sessions[1].id, sessions[3].id]);
        assert!(status.sessions.iter().all(|view| view.phase == "active" && view.control));
        assert!(remote.input(sessions[1].id, key(11)).await.unwrap());
        assert!(lands(&mut sessions[1], 11));
        // Places free up: a new session can start while the two others run.
        let fresh = open(&remote, &mut commands, 200_000_077).await;
        assert_eq!(remote.status().await.sessions.len(), 3);
        // End the rest in yet another order; the last one ends with the registration.
        remote.close(sessions[3].id).await.unwrap();
        assert!(matches!(commands.recv().await, Some(Command::Close(_))));
        assert!(remote.input(fresh.id, key(12)).await.unwrap());
        assert!(!remote.input(sessions[3].id, key(12)).await.unwrap());
        {
            let mut inner = remote.0.lock().await;
            RemoteSession::end_all_slots(&mut inner, "Connection lost; sharing stopped");
        }
        let status = remote.status().await;
        assert!(status.sessions.is_empty());
        assert!(fresh.cancel.load(Ordering::Acquire) && sessions[1].cancel.load(Ordering::Acquire));
    }

    #[tokio::test]
    async fn a_session_that_is_closed_before_the_broker_answers_never_starts() {
        let (remote, mut commands) = registered().await;
        let id = remote.request(200_000_001, None, false).await.unwrap();
        let Some(Command::Request(..)) = commands.recv().await else { panic!("request expected") };
        remote.close(id).await.unwrap();
        // The loop is told by the id it knows; the broker names the session later.
        assert!(matches!(commands.recv().await, Some(Command::Close(target)) if target == id));
        let late = remote
            .slot_track(0, id, Uuid::new_v4(), Arc::new(AtomicBool::new(false)), Uuid::new_v4(), Arc::new(AtomicBool::new(false)), false, false)
            .await;
        assert!(late.is_err(), "a closed request cannot become a session");
    }

    #[tokio::test]
    async fn a_transport_renewal_keeps_the_stable_id_and_the_other_sessions() {
        let (remote, mut commands) = registered().await;
        let mut first = open(&remote, &mut commands, 200_000_001).await;
        let mut second = open(&remote, &mut commands, 200_000_002).await;
        // The broker replaced the first session's transport: a new session id, the same tab.
        let renewed = Uuid::new_v4();
        remote
            .slot_track(0, first.session, renewed, Arc::new(AtomicBool::new(false)), Uuid::new_v4(), Arc::new(AtomicBool::new(false)), false, false)
            .await
            .unwrap();
        let status = remote.status().await;
        assert_eq!(status.sessions[0].id, first.id);
        assert_eq!(status.sessions[0].session, Some(renewed));
        assert!(first.cancel.load(Ordering::Acquire), "the old transport is stopped");
        assert_eq!(status.sessions[1].session, Some(second.session));
        assert!(!second.cancel.load(Ordering::Acquire), "the other session is not touched");
        assert!(remote.input(second.id, key(5)).await.unwrap());
        assert!(lands(&mut second, 5));
        assert!(!remote.input(first.id, key(5)).await.unwrap(), "until the new transport grants input");
        first.session = renewed;
    }

    #[tokio::test]
    async fn the_single_session_form_still_works() {
        let (remote, mut commands) = registered().await;
        let mut only = open(&remote, &mut commands, 200_000_001).await;
        let status = remote.status().await;
        // What an app that knows one session reads: the top-level fields describe it.
        assert_eq!(status.phase, "active");
        assert_eq!(status.session, Some(only.session));
        assert_eq!(status.peer, Some(200_000_001));
        assert!(status.control && status.clipboard);
        assert!(remote.input(status.session.unwrap(), key(7)).await.unwrap());
        assert!(lands(&mut only, 7));
        remote.close(status.session.unwrap()).await.unwrap();
        assert!(matches!(commands.recv().await, Some(Command::Close(id)) if id == only.session));
    }
}

#[cfg(test)]
mod countdown_tests {
    use super::*;

    #[test]
    fn the_end_time_is_written_as_iso_8601_utc() {
        assert_eq!(iso_utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(iso_utc(1_794_900_000), "2026-11-17T07:20:00Z");
        assert_eq!(iso_utc(1_794_986_400), "2026-11-18T07:20:00Z");
        assert_eq!(iso_utc(951_868_799), "2000-02-29T23:59:59Z", "leap day");
    }

    #[test]
    fn the_warnings_say_five_minutes_and_one_minute() {
        assert!(ending_message(300).starts_with("This support session ends in 5 minutes (support password limit)."));
        assert!(ending_message(60).starts_with("This support session ends in 1 minute (support password limit)."));
        assert!(ending_message(61).contains("2 minutes"));
        assert!(ending_message(300).ends_with("Ask for a new password to continue."));
    }

    #[test]
    fn the_platform_is_a_short_lowercase_word_the_broker_accepts() {
        let platform = app_platform();
        assert!(signaling_server::protocol::valid_app_version(crate::agent::APP_VERSION, &platform), "{platform}");
    }
}

/// Recovery with several technician sessions: each session resumes and asks again on its own.
#[cfg(test)]
mod per_session_recovery_tests {
    use super::*;
    use crate::reconnect::Access;

    fn technician(session: Uuid, remote: u32) -> Peer {
        let now = std::time::Instant::now();
        Peer {
            id: session,
            ui: session,
            link: Default::default(),
            handover: None,
            remote,
            host: false,
            key: ed25519_dalek::SigningKey::from_bytes(&[7; 32]).verifying_key(),
            source: String::new(),
            #[cfg(target_os = "linux")]
            portal: None,
            confirm: false,
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
            consent_id: Uuid::new_v4(),
            joined: false,
            switched: false,
            reversed: false,
        }
    }

    /// A registered technician with one open session per device: (state, stable ids, transport ids).
    async fn open(devices: &[u32]) -> (RemoteSession, Vec<Uuid>, Vec<Uuid>) {
        let remote = RemoteSession::default();
        let (tx, mut commands) = mpsc::channel(64);
        {
            let mut inner = remote.0.lock().await;
            inner.commands = Some(tx);
            inner.status.phase = "ready".into();
            inner.status.host = Some(111_111_111);
        }
        let (mut stable, mut transports) = (Vec::new(), Vec::new());
        for device in devices {
            let id = remote.request(*device, None, true).await.unwrap();
            let _ = commands.recv().await;
            let session = Uuid::new_v4();
            remote
                .slot_track(0, id, session, Arc::new(AtomicBool::new(false)), Uuid::new_v4(), Arc::new(AtomicBool::new(false)), false, false)
                .await
                .unwrap();
            stable.push(id);
            transports.push(session);
        }
        (remote, stable, transports)
    }

    fn view(status: &Status, id: Uuid) -> crate::remote_sessions::SessionStatus {
        status.sessions.iter().find(|view| view.id == id).cloned().expect("session listed")
    }

    #[tokio::test]
    async fn losing_the_computer_of_one_session_asks_again_for_that_session_only() {
        let (remote, stable, transports) = open(&[300_000_001, 300_000_002]).await;
        let access = |host| Access { host, unattended: true, password: None };
        let last: std::collections::HashMap<u32, Access> = [(300_000_001, access(300_000_001)), (300_000_002, access(300_000_002))].into();
        let mut retries = Vec::new();
        assert!(begin_reconnect(&remote, 0, &mut retries, &last, transports[0]).await);
        assert_eq!(retries.len(), 1);
        assert_eq!(retries[0].slot, stable[0]);
        let status = remote.status().await;
        let first = view(&status, stable[0]);
        assert!(first.reconnecting && !first.reconnect_failed);
        assert_eq!(first.phase, "outgoing");
        assert!(first.message.contains("Reconnecting"));
        let second = view(&status, stable[1]);
        assert!(!second.reconnecting && !second.reconnect_failed, "the other session is not touched");
        assert!(!begin_reconnect(&remote, 0, &mut retries, &last, transports[0]).await, "one retry per device");
        assert_eq!(retries.len(), 1);
        // Both can ask again at the same time, each on its own clock.
        assert!(begin_reconnect(&remote, 0, &mut retries, &last, transports[1]).await);
        assert_eq!(retries.len(), 2);
    }

    #[tokio::test]
    async fn an_attended_session_is_not_asked_again_and_an_unknown_session_neither() {
        let (remote, _, transports) = open(&[300_000_011]).await;
        let attended: std::collections::HashMap<u32, Access> =
            [(300_000_011, Access { host: 300_000_011, unattended: false, password: None })].into();
        let mut retries = Vec::new();
        assert!(!begin_reconnect(&remote, 0, &mut retries, &attended, transports[0]).await);
        assert!(!begin_reconnect(&remote, 0, &mut retries, &attended, Uuid::new_v4()).await);
        assert!(retries.is_empty());
        assert!(!remote.status().await.sessions[0].reconnecting);
    }

    #[tokio::test]
    async fn a_session_that_cannot_be_resumed_ends_alone() {
        let (remote, stable, transports) = open(&[300_000_021, 300_000_022, 300_000_023]).await;
        let mut ctrl: Vec<Peer> = (0..3).map(|n| technician(transports[n], 300_000_021 + n as u32)).collect();
        let mut peer: Option<Peer> = None;
        let mut resuming: Resuming = transports.iter().map(|id| (*id, std::time::Instant::now())).collect();
        let mut end_when_resumed: Vec<Uuid> = vec![transports[0]];
        end_resumed(&remote, 0, &mut peer, &mut ctrl, &mut resuming, &mut end_when_resumed, transports[1], "The session ended while the connection service restarted").await;
        assert_eq!(ctrl.iter().map(|p| p.id).collect::<Vec<_>>(), vec![transports[0], transports[2]]);
        assert_eq!(resuming.iter().map(|(id, _)| *id).collect::<Vec<_>>(), vec![transports[0], transports[2]]);
        assert_eq!(end_when_resumed, vec![transports[0]], "the others keep their own wish");
        let ids: Vec<Uuid> = remote.status().await.sessions.iter().map(|view| view.id).collect();
        assert!(ids.contains(&stable[0]) && ids.contains(&stable[2]) && !ids.contains(&stable[1]));
        // Naming a session by its stable id or its transport id finds the same waiting entry.
        assert_eq!(resuming_id(&resuming, &peer, &ctrl, transports[2]), Some(transports[2]));
        assert_eq!(resuming_id(&resuming, &peer, &ctrl, transports[1]), None);
    }

    #[tokio::test]
    async fn the_password_countdown_and_the_unattended_flag_belong_to_one_session() {
        let (remote, stable, _) = open(&[300_000_031, 300_000_032]).await;
        {
            let mut inner = remote.0.lock().await;
            let first = inner.slots.find_mut(stable[0]).unwrap();
            first.view.password_flag = true;
            first.view.password_ends = Some(std::time::Instant::now() + Duration::from_secs(250));
            inner.slots.find_mut(stable[1]).unwrap().view.unattended_session = true;
        }
        let status = remote.status().await;
        let (first, second) = (view(&status, stable[0]), view(&status, stable[1]));
        let left = first.password_session.as_ref().and_then(|session| session.seconds_left).expect("countdown on its own session");
        assert!((248..=250).contains(&left), "{left}");
        assert!(first.password_session.as_ref().unwrap().ends_at.is_some());
        assert!(!first.unattended_session);
        assert!(second.password_session.is_none(), "the other session shows no warning");
        assert!(second.unattended_session);
        let json = serde_json::to_value(&status).unwrap();
        assert!(json["sessions"][0]["password_session"]["seconds_left"].is_u64());
        assert!(json["sessions"][1]["password_session"].is_null());
        assert_eq!(json["sessions"][1]["unattended_session"], true);
        assert!(json["sessions"][0].get("password_flag").is_none(), "internal fields stay internal");
    }

    #[test]
    fn only_commands_about_a_resuming_session_wait() {
        let session = Uuid::new_v4();
        assert_eq!(command_session(&Command::Close(session)), Some(session));
        assert_eq!(command_session(&Command::Reconnect(session)), Some(session));
        assert_eq!(command_session(&Command::Request(1, None, false, session)), None, "a new session is not held back");
        assert_eq!(command_session(&Command::Presence(vec![1])), None);
    }
}
