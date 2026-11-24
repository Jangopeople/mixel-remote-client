//! What the engine reports, read from its JSON (the desktop app's field names).
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// The sign-in state (`account_status`).
#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct AccountStatus {
    /// `signed_out`, `starting`, `waiting_browser`, `verifying`, `mfa_required`, `profile_setup`,
    /// `authenticated`, `error`.
    pub phase: String,
    pub message: String,
    pub account: Option<Account>,
    pub factors: Vec<Factor>,
}

impl AccountStatus {
    pub fn signed_out() -> Self {
        Self { phase: "signed_out".into(), message: "Sign in with your Mixel account".into(), ..Self::default() }
    }
    pub fn signed_in(&self) -> bool {
        self.phase == "authenticated" && self.account.is_some()
    }
    pub fn signing_in(&self) -> bool {
        matches!(self.phase.as_str(), "starting" | "waiting_browser" | "verifying")
    }
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct Account {
    pub remembered: bool,
    pub has_company: bool,
    pub has_personal: bool,
    pub profile: String,
    pub organization: Option<String>,
    pub staff: bool,
    pub personal: bool,
    pub display_name: Option<String>,
    pub companies: Vec<CompanyAdministration>,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct CompanyAdministration {
    pub company_id: String,
    pub company_name: String,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct Factor {
    pub id: String,
    pub factor_type: String,
    pub status: String,
    pub friendly_name: Option<String>,
}

/// The connection to Mixel and the session (`remote_status`).
#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct Refusal {
    pub peer: u32,
    pub message: String,
    pub at: u64,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct RemoteStatus {
    /// Why the last request that never became a session was refused (the status message belongs to the session in front).
    pub last_refusal: Option<Refusal>,
    pub controller_only: bool,
    pub platform: String,
    /// `offline`, `connecting`, `ready`, `outgoing`, `incoming`, `deciding`, `negotiating`, `active`,
    /// `closing`, `error`.
    pub phase: String,
    pub message: String,
    pub host: Option<u32>,
    pub session: Option<String>,
    pub consent: Option<String>,
    pub peer: Option<u32>,
    pub requester: Option<String>,
    pub received: u64,
    pub sharing: bool,
    pub control: bool,
    /// The first picture is up but the control channel is still opening.
    #[serde(default)]
    pub control_pending: bool,
    pub clipboard: bool,
    pub files: bool,
    pub recording_permitted: bool,
    pub recording: Option<RecordingView>,
    pub recording_parts: Vec<String>,
    pub previous_recording: Option<RecordingView>,
    pub input_access: bool,
    pub unattended_enabled: bool,
    pub unattended_pending: Option<String>,
    pub unattended_session: bool,
    /// Unattended was asked for, but that computer is not set up for it: the person there is asked instead.
    pub unattended_refused: bool,
    pub connection_path: Option<String>,
    pub connection_protocol: Option<String>,
    pub rtt_ms: Option<u32>,
    pub bitrate_kbps: Option<u32>,
    pub pairing: Option<PairingPrompt>,
    pub pairing_message: Option<String>,
    pub code_required: bool,
    pub support_code: Option<SupportCode>,
    pub online: Vec<u32>,
    pub displays: u16,
    pub display: u16,
    pub display_all: bool,
    pub print_jobs: Vec<String>,
    pub host_actions: Vec<String>,
    pub signin_id: Option<u32>,
    pub host_macs: Vec<String>,
    /// The technician offers to show their screen in this meeting room: (session, room).
    pub meeting_offer: Option<(String, String)>,
    pub colleagues: Vec<Colleague>,
    pub join_request: Option<JoinRequest>,
    pub invitations: Vec<Invitation>,
    pub invite_message: Option<String>,
    pub joined_session: bool,
    pub switched: bool,
    pub peer_features: Vec<String>,
    /// Every technician session this app holds, oldest first (engines with several sessions). Old
    /// engines send none: `technician_sessions` then reads the single-session fields above.
    pub sessions: Vec<SessionItem>,
    pub max_sessions: Option<usize>,
    /// The connection to the connection service was lost; the session continues and is resumed (engine 2.1.0).
    pub reconnecting: bool,
    /// A session opened with a support password ends by itself after 2 hours (engine 2.1.0).
    pub password_session: Option<PasswordSessionView>,
    /// The session this status describes lost its computer and is asking again (set by `viewed_as`
    /// from `SessionItem::reconnecting`; never sent by the engine at this level).
    #[serde(skip)]
    pub session_reconnecting: bool,
    /// ... and gave up after a minute: "Could not reconnect", with a Retry (set by `viewed_as`).
    #[serde(skip)]
    pub session_reconnect_failed: bool,
}

/// `remote_status.password_session`: when the broker's limit ends the session, once known.
#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct PasswordSessionView {
    pub ends_at: Option<String>,
    pub seconds_left: Option<u64>,
}

/// Picture figures of one session.
#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct SessionQuality {
    pub pictures: u64,
    pub pictures_per_second: f32,
    pub picture_age_ms: Option<u64>,
}

/// One technician session (`remote_status.sessions[]`). `id` is the stable id: it is chosen when the
/// session is requested and never changes, so tabs, windows and every command are bound to it.
/// `session` is the connection service's id of the current transport and changes at every renewal.
#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct SessionItem {
    pub id: String,
    pub session: Option<String>,
    pub peer: Option<u32>,
    pub device: Option<u32>,
    /// `outgoing`, `negotiating`, `active`, `closing`.
    pub phase: String,
    pub message: String,
    pub consent: Option<String>,
    pub received: u64,
    pub control: bool,
    /// The first picture is up but the control channel is still opening.
    #[serde(default)]
    pub control_pending: bool,
    pub clipboard: bool,
    pub files: bool,
    pub recording_permitted: bool,
    pub recording: Option<RecordingView>,
    pub recording_parts: Vec<String>,
    pub displays: u16,
    pub display: u16,
    pub display_all: bool,
    pub print_jobs: Vec<String>,
    pub host_actions: Vec<String>,
    pub signin_id: Option<u32>,
    pub host_macs: Vec<String>,
    pub meeting_offer: Option<(String, String)>,
    pub peer_features: Vec<String>,
    pub joined_session: bool,
    pub switched: bool,
    pub quality: SessionQuality,
    pub started_at: u64,
    /// The host computer is gone and this app asks again for a minute (phase stays `outgoing`).
    pub reconnecting: bool,
    /// The minute is over: "Could not reconnect"; the session stays listed until it is ended or retried.
    pub reconnect_failed: bool,
    /// Opened with a support password: the broker's limit ends it (this session only).
    pub password_session: Option<PasswordSessionView>,
    /// Opened without a person confirming at the other computer (this session only).
    pub unattended_session: bool,
    /// Unattended was asked for, but that computer is not set up for it: the person there is asked instead.
    pub unattended_refused: bool,
    pub connection_path: Option<String>,
    pub connection_protocol: Option<String>,
    pub rtt_ms: Option<u32>,
    pub bitrate_kbps: Option<u32>,
}

/// Where one session's connection to its computer stands.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Link {
    Up,
    Reconnecting,
    Failed,
}

impl SessionItem {
    /// A failed reconnect wins over a running one (the engine clears `reconnecting` first anyway).
    pub fn link(&self) -> Link {
        if self.reconnect_failed {
            Link::Failed
        } else if self.reconnecting {
            Link::Reconnecting
        } else {
            Link::Up
        }
    }
}

/// How many technician sessions an engine without `max_sessions` allows.
pub const SINGLE_SESSION: usize = 1;

/// The one-time password of this computer: the engine sends the code (or an object with it).
#[derive(Clone, Debug, PartialEq, Deserialize)]
#[serde(untagged)]
pub enum SupportCode {
    Text(String),
    Object { code: String },
}

impl SupportCode {
    pub fn code(&self) -> &str {
        match self {
            Self::Text(code) | Self::Object { code } => code,
        }
    }
}

impl RemoteStatus {
    pub fn offline() -> Self {
        Self { phase: "offline".into(), platform: "linux".into(), input_access: true, ..Self::default() }
    }
    /// Connected to Mixel (this computer can be reached and can connect).
    pub fn connected(&self) -> bool {
        matches!(self.phase.as_str(), "ready" | "active" | "incoming" | "outgoing" | "deciding" | "negotiating")
    }
    pub fn disconnected(&self) -> bool {
        matches!(self.phase.as_str(), "offline" | "error" | "")
    }
    pub fn in_session(&self) -> bool {
        matches!(self.phase.as_str(), "active" | "incoming" | "outgoing" | "deciding" | "negotiating" | "closing")
    }
    /// This app views (and maybe controls) another computer right now.
    pub fn viewing(&self) -> bool {
        self.phase == "active" && !self.sharing && self.session.is_some()
    }
    pub fn offers(&self, action: &str) -> bool {
        self.host_actions.iter().any(|offered| offered == action)
    }
    /// The technician sessions: the engine's list, or, for an engine that sends none, the one session
    /// the single-session fields describe (its id is then the connection service's session id).
    pub fn technician_sessions(&self) -> Vec<SessionItem> {
        if !self.sessions.is_empty() {
            return self.sessions.clone();
        }
        let Some(peer) = self.peer else { return Vec::new() };
        if self.sharing || !matches!(self.phase.as_str(), "outgoing" | "negotiating" | "active" | "closing") {
            return Vec::new();
        }
        // The request is out but the connection service has not named the session yet (an old engine).
        if self.session.is_none() && self.phase != "outgoing" {
            return Vec::new();
        }
        let session = self.session.clone();
        vec![SessionItem {
            id: session.clone().unwrap_or_default(),
            session,
            peer: Some(peer),
            device: Some(peer),
            phase: self.phase.clone(),
            message: self.message.clone(),
            consent: self.consent.clone(),
            received: self.received,
            control: self.control,
            clipboard: self.clipboard,
            files: self.files,
            recording_permitted: self.recording_permitted,
            recording: self.recording.clone(),
            displays: self.displays,
            display: self.display,
            display_all: self.display_all,
            print_jobs: self.print_jobs.clone(),
            host_actions: self.host_actions.clone(),
            signin_id: self.signin_id,
            host_macs: self.host_macs.clone(),
            meeting_offer: self.meeting_offer.clone(),
            peer_features: self.peer_features.clone(),
            joined_session: self.joined_session,
            switched: self.switched,
            password_session: self.password_session.clone(),
            unattended_session: self.unattended_session,
            unattended_refused: self.unattended_refused,
            connection_path: self.connection_path.clone(),
            connection_protocol: self.connection_protocol.clone(),
            rtt_ms: self.rtt_ms,
            bitrate_kbps: self.bitrate_kbps,
            ..SessionItem::default()
        }]
    }

    /// Up to this many technician sessions at once.
    pub fn session_limit(&self) -> usize {
        self.max_sessions.filter(|max| *max > 0).unwrap_or(SINGLE_SESSION)
    }

    /// Another computer may be requested: this computer is ready (or offline, it goes online first), or
    /// it holds technician sessions and fewer than the limit. Not gated on `phase == "ready"`, which
    /// stops being true while a session runs.
    pub fn can_request(&self) -> bool {
        if matches!(self.phase.as_str(), "ready" | "offline" | "error") {
            return true;
        }
        let held = self.technician_sessions();
        !held.is_empty() && held.len() < self.session_limit() && !self.sharing
    }

    /// The status as one session sees it: the single-session fields (`phase`, `session`, `peer`,
    /// `control` …) describe `item`, the rest (this computer, `sessions`) stays. `session` is the
    /// stable id, so every command that reads it carries the id that survives a transport renewal.
    pub fn viewed_as(&self, item: &SessionItem) -> Self {
        let mut view = self.clone();
        view.sessions = self.technician_sessions();
        view.max_sessions = Some(self.session_limit());
        view.sharing = false;
        view.phase = item.phase.clone();
        view.message = item.message.clone();
        view.session = Some(item.id.clone());
        view.consent = item.consent.clone();
        view.peer = item.peer;
        view.received = item.received;
        view.control = item.control;
        view.control_pending = item.control_pending;
        view.clipboard = item.clipboard;
        view.files = item.files;
        view.recording_permitted = item.recording_permitted;
        view.recording = item.recording.clone();
        view.displays = item.displays;
        view.display = item.display;
        view.display_all = item.display_all;
        view.print_jobs = item.print_jobs.clone();
        view.host_actions = item.host_actions.clone();
        view.signin_id = item.signin_id;
        view.host_macs = item.host_macs.clone();
        view.meeting_offer = item.meeting_offer.clone();
        view.peer_features = item.peer_features.clone();
        view.joined_session = item.joined_session;
        view.switched = item.switched;
        // These belong to the session, not to this computer: another session's limit or reconnect
        // must not show here.
        view.password_session = item.password_session.clone();
        view.unattended_session = item.unattended_session;
        view.unattended_refused = item.unattended_refused;
        view.connection_path = item.connection_path.clone().or(view.connection_path);
        view.connection_protocol = item.connection_protocol.clone().or(view.connection_protocol);
        view.rtt_ms = item.rtt_ms.or(view.rtt_ms);
        view.bitrate_kbps = item.bitrate_kbps.or(view.bitrate_kbps);
        view.session_reconnecting = item.reconnecting && !item.reconnect_failed;
        view.session_reconnect_failed = item.reconnect_failed;
        view
    }
}

/// The engine's typed error of a refused request (`{"hint":"too_many_sessions","max":4,"message":…}`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RequestError {
    TooManySessions(usize),
    AlreadyConnected,
}

impl RequestError {
    /// The engine's own error text for a refused request, as the app builds it when it knows in advance.
    pub fn too_many(max: usize) -> String {
        serde_json::json!({ "hint": "too_many_sessions", "max": max, "message": format!("You already have {max} sessions open. End one to start another.") }).to_string()
    }

    pub fn parse(error: &str) -> Option<Self> {
        let value: serde_json::Value = serde_json::from_str(error.trim()).ok()?;
        match value["hint"].as_str()? {
            "too_many_sessions" => Some(Self::TooManySessions(value["max"].as_u64().map_or(4, |max| max as usize))),
            "already_connected" => Some(Self::AlreadyConnected),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct PairingPrompt {
    pub request_id: String,
    pub provider_name: String,
    pub customer_name: String,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct Colleague {
    pub session: String,
    pub connection: u32,
    pub control: bool,
    pub state: String,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct JoinRequest {
    pub session: String,
    pub connection: u32,
    pub inviter_connection: u32,
    pub control: bool,
    pub unattended: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct Invitation {
    pub invitation: String,
    pub host: u32,
    pub inviter: String,
    pub inviter_connection: u32,
    pub control: bool,
    pub unattended: bool,
    pub expires_at: u64,
}

/// `ready`, `waiting_for_video`, `recording`, `saving`, `saved`, `saved_partial`, `error`.
#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct RecordingView {
    pub phase: String,
    pub message: Option<String>,
}

impl RecordingView {
    pub fn running(&self) -> bool {
        matches!(self.phase.as_str(), "recording" | "waiting_for_video")
    }
}

/// A screen this computer can share (`capture_targets`).
#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct CaptureTarget {
    pub id: String,
    pub name: String,
    pub full_display: bool,
}

/// The answer to a connection request (`remote_decide`).
#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub struct Decision {
    pub session: String,
    pub accept: bool,
    pub source: String,
    pub confirm: bool,
    pub control: bool,
    pub clipboard: bool,
    pub files: bool,
    pub chat: bool,
    pub recording: bool,
}

/// A device of the account's directory (`remote_devices`) or of a company (`saas_devices`).
#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct Device {
    pub device: String,
    pub connection_id: u32,
    pub name: String,
    pub os: String,
    pub group_id: Option<String>,
    pub group_name: Option<String>,
    pub generation: Option<u64>,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct DevicePage {
    pub devices: Vec<Device>,
    pub next: Option<u32>,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct SaasDevice {
    pub device_id: String,
    pub name: String,
    pub os: String,
    pub group_id: Option<String>,
    pub group_name: Option<String>,
    pub connection_id: Option<u32>,
    pub generation: Option<u64>,
    pub revoked: Option<bool>,
}

impl SaasDevice {
    pub fn device(&self) -> Option<Device> {
        let connection_id = self.connection_id?;
        if self.revoked == Some(true) {
            return None;
        }
        Some(Device {
            device: self.device_id.clone(),
            connection_id,
            name: self.name.clone(),
            os: self.os.clone(),
            group_id: self.group_id.clone(),
            group_name: self.group_name.clone(),
            generation: self.generation,
        })
    }
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct SaasPresence {
    pub device_id: String,
    pub connection_id: Option<u32>,
    pub status: String,
    pub last_seen: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct SaasGroup {
    pub group_id: String,
    pub name: String,
    pub devices: i64,
    pub technicians: Vec<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct Tenant {
    pub tenant_id: String,
    pub organization_id: String,
    pub name: String,
    pub slug: String,
    pub role: String,
    pub plan: String,
    pub status: String,
    pub trial_ends_at: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct TenantOverview {
    pub role: Option<String>,
    pub status: Option<String>,
    pub plan: Option<String>,
    pub trial_ends_at: Option<String>,
    pub features: Option<HashMap<String, bool>>,
    pub limits: Option<Limits>,
    pub usage: Option<Usage>,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct Limits {
    pub devices: Option<i64>,
    pub technicians: Option<i64>,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct Usage {
    pub devices: i64,
    pub technicians: i64,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct Member {
    pub user_id: String,
    pub name: String,
    pub email: String,
    pub role: String,
    pub created_at: String,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct Invite {
    pub id: String,
    pub email: String,
    pub role: String,
    pub created_at: String,
    pub expires_at: String,
    pub mail_state: Option<String>,
    pub mail_sent_at: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct DeployLinkCreated {
    pub token: String,
    pub windows_command: String,
    pub macos_command: String,
    pub linux_command: String,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct SaasEnrollment {
    pub device_id: String,
    pub connection_id: u32,
    pub generation: u64,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct ChatMessage {
    pub id: u64,
    pub mine: bool,
    pub text: String,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct FileView {
    pub connected: bool,
    pub incoming: Option<Transfer>,
    pub outgoing: Option<Transfer>,
    pub message: String,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct Transfer {
    pub id: String,
    pub name: String,
    pub size: u64,
    pub offset: u64,
    pub phase: String,
    pub message: String,
}

impl Transfer {
    pub fn progress(&self) -> f64 {
        if self.size == 0 { 0.0 } else { (self.offset as f64 / self.size as f64).min(1.0) }
    }
    pub fn active(&self) -> bool {
        matches!(self.phase.as_str(), "offered" | "waiting" | "transferring" | "verifying" | "paused")
    }
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct MeetingRoom {
    pub room: String,
    pub invite: String,
    pub host: String,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct PrintingStatus {
    pub supported: bool,
    pub installed: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct StartupSettings {
    pub remembered: bool,
    pub autostart: bool,
    pub launched_at_startup: bool,
    pub restored: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct UpdateInfo {
    pub version: String,
    /// A signed package is on the update channel and can be installed in the app.
    pub installable: bool,
    pub silent: bool,
    /// Installing asks for the administrator password (always on Linux).
    pub needs_password: bool,
    /// A session is running (or ended a moment ago): the update is ready and installs after it.
    pub waits_for_session: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct SignInScreen {
    pub supported: bool,
    pub outdated: bool,
    pub technician: bool,
    pub connection_id: Option<u32>,
    pub name: Option<String>,
    pub wayland_greeter: bool,
    pub managed_elsewhere: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct LinuxSession {
    pub wayland: bool,
    pub x11: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct EnrollmentCompany {
    pub client_id: String,
    pub client_name: String,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct InventoryDevice {
    pub id: String,
    pub name: String,
    pub os: String,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct InventoryPage {
    pub devices: Vec<InventoryDevice>,
    pub next: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct CurrentEnrollment {
    pub device: String,
    pub connection_id: u32,
    pub name: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn engine_status_reads_with_missing_and_extra_fields() {
        let status: RemoteStatus = serde_json::from_str(
            r#"{"phase":"active","session":"7c1d","sharing":false,"control":true,"host_actions":["privacy","sas"],"meeting_offer":["7c1d","room1"],"support_code":"123456","future_field":1}"#,
        )
        .unwrap();
        assert!(status.viewing());
        assert!(status.connected());
        assert!(status.offers("sas") && !status.offers("restart"));
        assert_eq!(status.meeting_offer, Some(("7c1d".into(), "room1".into())));
        assert_eq!(status.support_code.as_ref().map(SupportCode::code), Some("123456"));
        let account: AccountStatus = serde_json::from_str(r#"{"phase":"authenticated","message":"","account":{"profile":"p","staff":true}}"#).unwrap();
        assert!(account.signed_in());
        assert!(account.account.unwrap().staff);
    }

    #[test]
    fn admin_rows_read_the_engine_answer_and_unattended_follows_the_setting() {
        let rows: Vec<AdminDevice> = serde_json::from_str(
            r#"[{"device_id":"d1","connection_id":440207203,"name":"A","os":"macos","favorite":true,"unattended_setting":"default","unattended_default":false,"unattended_allowed":true,"status":"online","app_version":"2.0.19","note":"Desk 3","can_admin":true,"last_seen":"2026-11-17T09:12:30Z","owner_name":null,"future":1},
                {"connection_id":268455901,"unattended_setting":"default","unattended_default":true,"unattended_allowed":true},
                {"connection_id":731204118,"unattended_setting":"on","unattended_allowed":true},
                {"connection_id":123456789,"unattended_setting":"off","unattended_default":true,"unattended_allowed":false},
                {"connection_id":111222333}]"#,
        )
        .unwrap();
        assert_eq!((rows[0].app_version.as_deref(), rows[0].note.as_deref(), rows[0].can_admin), (Some("2.0.19"), Some("Desk 3"), true));
        assert_eq!(rows.iter().map(AdminDevice::unattended_on).collect::<Vec<_>>(), [false, true, true, false, false]);
    }

    #[test]
    fn revoked_or_unenrolled_company_devices_are_not_listed() {
        let row = |connection: Option<u32>, revoked: Option<bool>| SaasDevice { device_id: "d".into(), connection_id: connection, revoked, ..Default::default() };
        assert!(row(Some(123_456_789), None).device().is_some());
        assert!(row(Some(123_456_789), Some(true)).device().is_none());
        assert!(row(None, None).device().is_none());
    }

    #[test]
    fn decisions_serialize_with_the_engine_field_names() {
        let decision = Decision { session: "s".into(), accept: true, source: "x11:0:1".into(), confirm: true, ..Default::default() };
        let json = serde_json::to_value(&decision).unwrap();
        assert_eq!(json["source"], "x11:0:1");
        assert_eq!(json["recording"], false);
    }
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct SaasFavorite {
    pub device_id: String,
    pub created_at: String,
}

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct SaasRecentDevice {
    pub device_id: String,
    pub last_connected_at: String,
}

/// One session of the session report (`saas_session_report`).
#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct SessionRow {
    pub session_id: String,
    pub technician_id: Option<String>,
    pub technician_name: Option<String>,
    pub technician_email: Option<String>,
    pub device_id: String,
    pub device_name: String,
    pub connection_id: u32,
    pub started_at: String,
    pub ended_at: Option<String>,
    pub duration_seconds: i64,
    /// `attended` or `unattended`.
    pub kind: String,
    pub colleague: bool,
    pub note: Option<String>,
}

/// What the device administration API (`remote_admin_devices`, docs/mixel-remote-device-admin-api.md)
/// reports about a device beyond the list: the app version and the unattended-access setting. Read in
/// one place (`App::load_device_meta`); until the engine has the command the table shows no badges.
#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct AdminDevice {
    pub device_id: Option<String>,
    pub connection_id: u32,
    pub app_version: Option<String>,
    pub unattended_allowed: Option<bool>,
    pub unattended_default: Option<bool>,
    pub unattended_setting: Option<String>,
    pub note: Option<String>,
    pub group_name: Option<String>,
    pub company_name: Option<String>,
    pub can_admin: bool,
    pub last_seen: Option<String>,
    pub updated_at: Option<String>,
}

impl AdminDevice {
    /// The device accepts unattended connections: switched on, or on by the company default and not switched
    /// off. `unattended_allowed` alone only says the broker does not refuse it (true for every device that
    /// is not set to "off"), so it is not the badge.
    pub fn unattended_on(&self) -> bool {
        match self.unattended_setting.as_deref() {
            Some("on") => self.unattended_allowed != Some(false),
            Some("off") | None => false,
            Some(_) => self.unattended_default == Some(true) && self.unattended_allowed != Some(false),
        }
    }
}

/// One row of `remote_admin_unattended_grants`: a per-user grant, or automatic access from the company setting.
#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
#[serde(default)]
pub struct UnattendedGrant {
    pub user_id: String,
    pub name: Option<String>,
    pub role: Option<String>,
    /// `company_setting` for automatic access (read-only); `grant` (or absent) for a per-user grant.
    pub source: Option<String>,
}

/// One person in the "Who may connect" list.
#[derive(Clone, Debug, PartialEq)]
pub struct GrantRow {
    pub user_id: String,
    pub name: String,
    pub email: String,
    pub role: String,
    pub granted: bool,
    pub automatic: bool,
}
