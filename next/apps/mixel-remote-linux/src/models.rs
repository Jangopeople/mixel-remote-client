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
pub struct RemoteStatus {
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
    pub clipboard: bool,
    pub files: bool,
    pub recording_permitted: bool,
    pub recording: Option<RecordingView>,
    pub previous_recording: Option<RecordingView>,
    pub input_access: bool,
    pub unattended_enabled: bool,
    pub unattended_pending: Option<String>,
    pub unattended_session: bool,
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
}

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
    /// The session window is shown: the picture, or the wait before it.
    pub fn session_window(&self) -> bool {
        self.viewing() || self.phase == "outgoing" || (self.phase == "negotiating" && !self.sharing && self.session.is_some())
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
