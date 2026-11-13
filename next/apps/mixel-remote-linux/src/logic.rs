//! The app's decisions without a window: what a status line says, whether a device can be reached
//! and why not, what a company may do, how a click in the picture maps to the remote screen, how
//! input is queued, how a restarted computer is reconnected. Unit-tested; the screens only show it.
use crate::{
    format,
    i18n::{engine, t, tf},
    models::*,
};
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};

/// The sections of the app.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub enum Section {
    #[default]
    Remote,
    Devices,
    Account,
    Reports,
    Settings,
}

impl Section {
    pub const ALL: [Section; 5] = [Section::Remote, Section::Devices, Section::Account, Section::Reports, Section::Settings];
    pub fn title(self) -> String {
        match self {
            Self::Remote => t("Remote control"),
            Self::Devices => t("Devices"),
            Self::Account => t("Account"),
            Self::Reports => t("Session reports"),
            Self::Settings => t("Settings"),
        }
    }
    pub fn icon(self) -> &'static str {
        match self {
            Self::Remote => "network-transmit-receive-symbolic",
            Self::Devices => "computer-symbolic",
            Self::Account => "avatar-default-symbolic",
            Self::Reports => "x-office-spreadsheet-symbolic",
            Self::Settings => "emblem-system-symbolic",
        }
    }
    pub fn id(self) -> &'static str {
        match self {
            Self::Remote => "remote",
            Self::Devices => "devices",
            Self::Account => "account",
            Self::Reports => "reports",
            Self::Settings => "settings",
        }
    }
}

/// The status line of the connection, as the desktop app words it.
pub fn connection_message(signed_in: bool, remote: &RemoteStatus, stopping: bool) -> String {
    if stopping {
        return t("Ending the session…");
    }
    match remote.phase.as_str() {
        "offline" | "" => {
            if remote.controller_only {
                return if signed_in { t("Connect this controller to the Mixel network.") } else { t("Sign in to access managed devices.") };
            }
            if signed_in { t("Make this computer available to start or receive a connection.") } else { t("Sign in to get your connection ID.") }
        }
        "ready" => {
            if remote.message.is_empty() { t("Ready for a connection.") } else { engine(&remote.message) }
        }
        "outgoing" => t("Waiting for the other person to accept your request."),
        "connecting" | "deciding" | "negotiating" => t("Connecting securely…"),
        "active" => {
            if remote.sharing {
                t("Sharing your selected screen. You can end the session at any time.")
            } else if remote.control {
                t("Connected. Keyboard and mouse control is enabled.")
            } else {
                t("Connected in view-only mode.")
            }
        }
        _ => engine(&remote.message),
    }
}

/// Online / offline / problem, for the badge next to this computer's ID.
pub fn connection_badge(remote: &RemoteStatus) -> (Badge, String) {
    if remote.connected() {
        (Badge::Online, t("Online"))
    } else if remote.phase == "error" {
        (Badge::Attention, t("Connection problem"))
    } else {
        (Badge::Offline, t("Offline"))
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Badge {
    Online,
    Offline,
    Unknown,
    Busy,
    Attention,
}

impl Badge {
    pub fn css(self) -> &'static str {
        match self {
            Self::Online => "mixel-online",
            Self::Offline => "mixel-offline",
            Self::Unknown => "mixel-unknown",
            Self::Busy => "mixel-busy",
            Self::Attention => "mixel-attention",
        }
    }
}

// ---- Devices ----

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Presence {
    This,
    Online,
    Offline,
    Checking,
    Unknown,
}

pub fn presence(device: &Device, remote: &RemoteStatus, checked: &HashSet<u32>, server: &HashMap<u32, SaasPresence>) -> Presence {
    if Some(device.connection_id) == remote.host {
        return Presence::This;
    }
    if !remote.connected() {
        return match server.get(&device.connection_id) {
            Some(record) if record.status == "online" => Presence::Online,
            Some(_) => Presence::Offline,
            None => Presence::Unknown,
        };
    }
    if remote.online.contains(&device.connection_id) {
        Presence::Online
    } else if checked.contains(&device.connection_id) {
        Presence::Offline
    } else {
        Presence::Checking
    }
}

pub fn presence_label(presence: Presence, remote: &RemoteStatus) -> String {
    match presence {
        Presence::This => {
            if remote.connected() { t("This computer") } else { t("This computer · not connected") }
        }
        Presence::Online => t("Online"),
        Presence::Offline => t("Offline"),
        Presence::Checking => t("Checking…"),
        Presence::Unknown => t("Status unknown"),
    }
}

pub fn presence_badge(presence: Presence) -> Badge {
    match presence {
        Presence::Online | Presence::This => Badge::Online,
        Presence::Offline => Badge::Offline,
        Presence::Checking => Badge::Busy,
        Presence::Unknown => Badge::Unknown,
    }
}

/// Why a device is (not) reachable, in a short sentence.
pub fn presence_reason(device: &Device, presence: Presence, remote: &RemoteStatus, server: &HashMap<u32, SaasPresence>, now: i64) -> String {
    match presence {
        Presence::This => {
            if remote.connected() { t("You are using this device.") } else { t("Reconnecting automatically.") }
        }
        Presence::Offline => {
            let seen = server.get(&device.connection_id).map(|record| format::last_seen(record.last_seen.as_deref(), now)).unwrap_or_default();
            let prefix = if seen.is_empty() { String::new() } else { format!("{seen}. ") };
            prefix + &t("Turned off, asleep or without internet. It must be online to connect.")
        }
        Presence::Unknown => t("Shown once this computer is connected."),
        Presence::Online => t("Ready. The person there accepts your request, unless unattended access is set up."),
        Presence::Checking => String::new(),
    }
}

/// Why Connect is not possible ("" when it is).
pub fn connect_blocked(device: &Device, presence: Presence, remote: &RemoteStatus, busy: bool, signed_in: bool) -> String {
    if Some(device.connection_id) == remote.host {
        return t("This is the computer you are using.");
    }
    if remote.in_session() {
        return t("End the current session first.");
    }
    if presence == Presence::Offline {
        return t("This device is offline.");
    }
    if busy {
        return t("Please wait…");
    }
    if !signed_in {
        return t("Sign in first.");
    }
    String::new()
}

// ---- Company ----

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Company {
    pub tenants: Vec<Tenant>,
    pub overview: Option<TenantOverview>,
    pub members: Vec<Member>,
    pub invites: Vec<Invite>,
    pub loaded: bool,
}

impl Company {
    pub fn tenant(&self) -> Option<&Tenant> {
        self.tenants.first()
    }
    pub fn role(&self) -> String {
        self.overview.as_ref().and_then(|overview| overview.role.clone()).or_else(|| self.tenant().map(|tenant| tenant.role.clone())).unwrap_or_default()
    }
    pub fn status(&self) -> String {
        self.overview.as_ref().and_then(|overview| overview.status.clone()).or_else(|| self.tenant().map(|tenant| tenant.status.clone())).unwrap_or_default()
    }
    pub fn administers(&self) -> bool {
        matches!(self.role().as_str(), "owner" | "admin")
    }
    pub fn is_owner(&self) -> bool {
        self.role() == "owner"
    }
    /// The company may change things (an active plan, a trial or a payment that is late).
    pub fn writable(&self) -> bool {
        matches!(self.status().as_str(), "active" | "trial" | "past_due")
    }
    pub fn read_only(&self) -> bool {
        self.tenant().is_some() && !self.writable()
    }
    pub fn manages_devices(&self) -> bool {
        self.tenant().is_some() && matches!(self.role().as_str(), "owner" | "admin" | "technician")
    }
    pub fn deploys(&self) -> bool {
        self.tenant().is_some() && self.administers()
    }
    pub fn groups_on(&self) -> bool {
        self.overview.as_ref().and_then(|overview| overview.features.as_ref()).and_then(|features| features.get("groups").copied()).unwrap_or(false)
    }
    /// Devices that may still be added under the plan.
    pub fn room(&self) -> i64 {
        match self.overview.as_ref() {
            Some(TenantOverview { limits: Some(Limits { devices: Some(limit), .. }), usage: Some(usage), .. }) => limit - usage.devices,
            _ => i64::MAX,
        }
    }
    /// Why adding devices is not possible right now ("" when it is).
    pub fn add_blocked(&self) -> String {
        if !self.writable() {
            return t("This company is read-only");
        }
        if self.room() <= 0 {
            return t("Device limit of your plan reached");
        }
        String::new()
    }
    /// "Business trial until 27 Nov 2026" and similar.
    pub fn plan_line(&self) -> String {
        let Some(tenant) = self.tenant() else { return String::new() };
        let overview = self.overview.clone().unwrap_or_default();
        let plan = format::plan(overview.plan.as_deref().unwrap_or(&tenant.plan));
        match self.status().as_str() {
            "trial" => tf("%@ trial until %@", &[&plan, &format::day(overview.trial_ends_at.as_deref().or(tenant.trial_ends_at.as_deref()))]),
            "read_only" | "expired" | "canceled" => tf("%@ · read-only", &[&plan]),
            _ => plan,
        }
    }
    pub fn usage_line(&self) -> String {
        let Some(overview) = self.overview.as_ref() else { return String::new() };
        let Some(usage) = overview.usage.as_ref() else { return String::new() };
        let limits = overview.limits.clone().unwrap_or_default();
        let devices = match limits.devices {
            Some(limit) => tf("%d of %d devices", &[&usage.devices, &limit]),
            None => tf("%d devices", &[&usage.devices]),
        };
        let technicians = match limits.technicians {
            Some(limit) => tf("%d of %d technicians", &[&usage.technicians, &limit]),
            None => tf("%d technicians", &[&usage.technicians]),
        };
        format!("{devices} · {technicians}")
    }
}

pub fn invite_mail_state(invite: &Invite) -> String {
    match invite.mail_state.as_deref() {
        Some("sent" | "delivered") => t("E-mail sent"),
        Some("queued" | "sending") => t("E-mail on its way"),
        Some("failed" | "bounced") => t("E-mail failed"),
        _ => tf("Expires %@", &[&format::day(Some(&invite.expires_at))]),
    }
}

/// What kind of account this is, for the profile card.
pub fn account_kind(account: &Account, company: &Company) -> String {
    if account.staff {
        return t("Mixel team account");
    }
    if account.personal {
        return t("Personal account");
    }
    if let Some(tenant) = company.tenant() {
        return format!("{} · {}", tenant.name, format::role(&tenant.role));
    }
    t("Mixel account")
}

// ---- Setting up this computer ----

/// How this account sets up this computer.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SetupPath {
    Company,
    CompanyMember,
    Team,
    Personal,
    None,
}

pub fn setup_path(account: Option<&Account>, company: &Company) -> SetupPath {
    let Some(account) = account else { return SetupPath::None };
    if account.staff {
        return SetupPath::Team;
    }
    if let Some(tenant) = company.tenant() {
        return if matches!(tenant.role.as_str(), "owner" | "admin" | "technician") { SetupPath::Company } else { SetupPath::CompanyMember };
    }
    if account.personal || account.has_personal {
        return SetupPath::Personal;
    }
    SetupPath::None
}

/// The engine says this computer has no registration (yet): show the set-up card instead of an error.
pub fn needs_setup(error: &str) -> bool {
    ["No saved device identity", "not enrolled", "No saved controller identity", "not registered", "Register this computer"].iter().any(|text| error.contains(text))
}

// ---- Unattended access ----

pub fn unattended_text(remote: &RemoteStatus) -> String {
    match remote.unattended_pending.as_deref() {
        Some("enabling") => t("Turning on. Waiting for the connection service."),
        Some(_) => t("Sharing is off. Confirming with the connection service."),
        None if remote.unattended_enabled => t("On. Approved users can connect while this computer stays signed in."),
        None => t("Off. Choose a display and confirm before enabling."),
    }
}

// ---- Session ----

pub fn recording_text(recording: Option<&RecordingView>) -> String {
    match recording.map(|recording| recording.phase.as_str()) {
        Some("waiting_for_video") => t("Waiting for a complete video frame…"),
        Some("recording") => t("Recording video"),
        Some("saving") => t("Saving recording…"),
        Some("saved") => t("Recording saved"),
        Some("saved_partial") => t("The recording stopped early. The playable portion was saved."),
        Some("error") => engine(recording.and_then(|recording| recording.message.as_deref()).unwrap_or_default()),
        _ => String::new(),
    }
}

/// A point in the widget (aspect-fit picture, centred) as a point in the picture (0…1, top left).
/// Outside the picture: `None`, unless `clamp` (while a button is held).
pub fn picture_point(view: (f64, f64), image: (f64, f64), at: (f64, f64), clamp: bool) -> Option<(f64, f64)> {
    let (view_w, view_h) = view;
    let (image_w, image_h) = image;
    if image_w <= 0.0 || image_h <= 0.0 || view_w <= 0.0 || view_h <= 0.0 {
        return None;
    }
    let scale = (view_w / image_w).min(view_h / image_h);
    let (shown_w, shown_h) = (image_w * scale, image_h * scale);
    let x = (at.0 - (view_w - shown_w) / 2.0) / shown_w;
    let y = (at.1 - (view_h - shown_h) / 2.0) / shown_h;
    if !(0.0..=1.0).contains(&x) || !(0.0..=1.0).contains(&y) {
        if !clamp {
            return None;
        }
        return Some((x.clamp(0.0, 1.0), y.clamp(0.0, 1.0)));
    }
    Some((x, y))
}

/// A position (0…1) as the engine expects it (0…65535).
pub fn normalized(value: f64) -> u16 {
    (value.clamp(0.0, 1.0) * 65535.0).round() as u16
}

/// Buttons as the engine numbers them: 1 left, 2 middle, 3 right, 4 back, 5 forward. GTK: 1 left,
/// 2 middle, 3 right, 8 back, 9 forward.
pub fn engine_button(gtk_button: u32) -> Option<u8> {
    match gtk_button {
        1 => Some(1),
        2 => Some(2),
        3 => Some(3),
        8 => Some(4),
        9 => Some(5),
        _ => None,
    }
}

pub mod input {
    use serde_json::{Value, json};
    pub fn key(usage: u16, down: bool) -> Value {
        json!({ "kind": "key", "usage": usage, "down": down })
    }
    pub fn pointer(x: u16, y: u16) -> Value {
        json!({ "kind": "pointer", "x": x, "y": y })
    }
    pub fn button(button: u8, down: bool, clicks: u8) -> Value {
        json!({ "kind": "button_click", "button": button, "down": down, "clicks": clicks })
    }
    pub fn wheel(horizontal: i16, vertical: i16) -> Value {
        json!({ "kind": "wheel", "horizontal": horizontal, "vertical": vertical })
    }
    pub fn text(text: &str) -> Value {
        json!({ "kind": "text", "text": text })
    }
    pub fn release_all() -> Value {
        json!({ "kind": "release_all" })
    }
    pub fn select_display(index: u16) -> Value {
        json!({ "kind": "select_display", "index": index })
    }
    pub fn action(name: &str) -> Value {
        json!({ "kind": "action", "name": name })
    }
}

/// Input waiting to be sent: pointer moves replace an unsent move; more than 64 waiting events
/// mean the remote computer stopped taking input.
#[derive(Default)]
pub struct InputQueue {
    events: std::collections::VecDeque<Value>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum Queued {
    Ok,
    Stalled,
}

impl InputQueue {
    pub fn push(&mut self, event: Value) -> Queued {
        let pointer = event["kind"] == "pointer";
        if pointer && self.events.back().is_some_and(|last| last["kind"] == "pointer") {
            *self.events.back_mut().expect("last") = event;
            return Queued::Ok;
        }
        if self.events.len() >= 64 {
            self.events.clear();
            return Queued::Stalled;
        }
        self.events.push_back(event);
        Queued::Ok
    }
    pub fn pop(&mut self) -> Option<Value> {
        self.events.pop_front()
    }
    pub fn clear(&mut self) {
        self.events.clear();
    }
    #[cfg(test)]
    pub fn len(&self) -> usize {
        self.events.len()
    }
}

/// Scrolling: fine (touchpad) deltas add up until they make a whole wheel step.
#[derive(Default)]
pub struct Wheel {
    x: f64,
    y: f64,
}

impl Wheel {
    /// GTK deltas (positive = down/right; 1.0 = one wheel notch, touchpads send fractions) →
    /// engine steps (positive = up/left; a notch is three steps, as a browser's line mode).
    pub fn add(&mut self, dx: f64, dy: f64) -> Option<(i16, i16)> {
        let scale = 3.0;
        self.x = (self.x - dx * scale).clamp(-120.0, 120.0);
        self.y = (self.y - dy * scale).clamp(-120.0, 120.0);
        let (horizontal, vertical) = (self.x.trunc() as i16, self.y.trunc() as i16);
        self.x -= f64::from(horizontal);
        self.y -= f64::from(vertical);
        (horizontal != 0 || vertical != 0).then_some((horizontal, vertical))
    }
    pub fn clear(&mut self) {
        *self = Self::default();
    }
}

/// After "Restart computer": wait until the computer went away and came back (or its sign-in screen
/// access is online), then connect again; give up after 10 minutes.
#[derive(Clone, Debug, PartialEq)]
pub struct Reconnect {
    pub host: u32,
    pub signin: Option<u32>,
    pub unattended: bool,
    pub since: f64,
    pub until: f64,
    pub went_offline: bool,
    pub last_try: f64,
    pub last_presence: f64,
    pub session: String,
}

#[derive(Debug, PartialEq)]
pub enum ReconnectStep {
    /// Nothing to do now.
    Wait,
    /// The session is back (or another one runs): stop following.
    Done,
    GaveUp,
    /// Ask the connection service whether these are online.
    Presence(Vec<u32>),
    /// Connect to this ID now.
    Request { host: u32, unattended: bool },
}

impl Reconnect {
    pub fn new(host: u32, signin: Option<u32>, unattended: bool, session: String, now: f64) -> Self {
        Self { host, signin, unattended, since: now, until: now + 600.0, went_offline: false, last_try: f64::MIN, last_presence: f64::MIN, session }
    }

    /// One look at the status; returns what to do (several steps may follow on the next looks).
    pub fn step(&mut self, status: &RemoteStatus, busy: bool, now: f64) -> Vec<ReconnectStep> {
        if status.phase == "active" {
            return if status.session.as_deref() != Some(&self.session) { vec![ReconnectStep::Done] } else { vec![ReconnectStep::Wait] };
        }
        if matches!(status.phase.as_str(), "negotiating" | "outgoing" | "deciding") {
            return vec![ReconnectStep::Wait];
        }
        if now > self.until {
            return vec![ReconnectStep::GaveUp];
        }
        let mut steps = Vec::new();
        if now - self.last_presence > 5.0 {
            self.last_presence = now;
            steps.push(ReconnectStep::Presence(std::iter::once(self.host).chain(self.signin).collect()));
        }
        if !status.online.contains(&self.host) {
            self.went_offline = true;
        }
        let elapsed = now - self.since;
        let ready = self.went_offline || elapsed > 120.0;
        let mut target = None;
        if ready {
            if status.online.contains(&self.host) {
                target = Some(self.host);
            } else if let Some(signin) = self.signin.filter(|signin| status.online.contains(signin) && elapsed > 45.0) {
                target = Some(signin);
            } else if self.unattended && elapsed > 60.0 {
                target = Some(self.host);
            }
        }
        if let Some(host) = target
            && status.phase == "ready"
            && !busy
            && now - self.last_try > 15.0
        {
            self.last_try = now;
            steps.push(ReconnectStep::Request { host, unattended: self.unattended || Some(host) == self.signin });
        }
        if steps.is_empty() { vec![ReconnectStep::Wait] } else { steps }
    }
}

/// The meeting the technician offers to show, if it is for this session and not dismissed.
pub fn meeting_offer(remote: &RemoteStatus, dismissed: &str) -> Option<String> {
    let (session, room) = remote.meeting_offer.as_ref()?;
    if remote.phase == "active" && remote.sharing && remote.session.as_deref() == Some(session) && room != dismissed {
        Some(room.clone())
    } else {
        None
    }
}

/// The arguments of a connection request.
pub fn request_args(host: u32, password: Option<&str>, unattended: bool) -> Value {
    json!({ "host": host, "password": password.filter(|value| !value.is_empty()), "unattended": unattended })
}

// ---- Drawing on the remote screen ----

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum DrawTool {
    #[default]
    Pen,
    Highlighter,
    Arrow,
}

impl DrawTool {
    pub const ALL: [DrawTool; 3] = [DrawTool::Pen, DrawTool::Highlighter, DrawTool::Arrow];
    pub fn wire(self) -> &'static str {
        match self {
            Self::Pen => "pen",
            Self::Highlighter => "highlighter",
            Self::Arrow => "arrow",
        }
    }
    pub fn title(self) -> String {
        match self {
            Self::Pen => t("Pen"),
            Self::Highlighter => t("Highlighter"),
            Self::Arrow => t("Arrow"),
        }
    }
}

/// Red, yellow, green, blue, white (the engine's palette).
pub const DRAW_PALETTE: [[u8; 3]; 5] = [[0xE5, 0x32, 0x2D], [0xFF, 0xD4, 0x00], [0x22, 0xB1, 0x4C], [0x1E, 0x7B, 0xFF], [0xFF, 0xFF, 0xFF]];

pub fn draw_color_name(index: u8) -> String {
    match index {
        0 => t("Red"),
        1 => t("Yellow"),
        2 => t("Green"),
        3 => t("Blue"),
        _ => t("White"),
    }
}

/// Line width in pixels of a size step (1..=4) on a picture `height` pixels high (as the engine).
pub fn draw_line_width(tool: DrawTool, width: u8, height: f64) -> f64 {
    let pen = [3.0, 5.0, 8.0, 12.0];
    let highlight = [14.0, 20.0, 28.0, 36.0];
    let step = usize::from(width.clamp(1, 4) - 1);
    let base = if tool == DrawTool::Highlighter { highlight[step] } else { pen[step] };
    (base * height / 1080.0).max(1.0)
}

/// A stroke being drawn here: sent to the other computer in parts of at most 64 points.
#[derive(Clone, Debug, PartialEq)]
pub struct Stroke {
    pub id: u16,
    pub tool: DrawTool,
    pub color: u8,
    pub width: u8,
    pub points: Vec<(u16, u16)>,
    pub sent: usize,
    pub done: bool,
}

impl Stroke {
    pub fn new(id: u16, tool: DrawTool, color: u8, width: u8, start: (u16, u16)) -> Self {
        Self { id, tool, color: color.min(4), width: width.clamp(1, 4), points: vec![start], sent: 0, done: false }
    }
    /// Adds a point (an arrow keeps its start and the newest end only); at most 4096 points.
    pub fn add(&mut self, point: (u16, u16)) {
        if self.tool == DrawTool::Arrow {
            self.points.truncate(1);
            self.sent = 0;
        }
        if self.points.len() < 4096 && self.points.last() != Some(&point) {
            self.points.push(point);
        }
    }
    /// The next message for the other computer, if there is something new (`finish`: the last one).
    pub fn message(&mut self, finish: bool) -> Option<Value> {
        if finish {
            self.done = true;
        }
        let pending = if self.tool == DrawTool::Arrow { self.points.clone() } else { self.points[self.sent..].to_vec() };
        if pending.is_empty() && !finish {
            return None;
        }
        if self.tool != DrawTool::Arrow && pending.len() < 16 && !finish {
            return None;
        }
        let part: Vec<(u16, u16)> = pending.into_iter().take(64).collect();
        if self.tool != DrawTool::Arrow {
            self.sent += part.len();
        }
        let done = self.done && (self.tool == DrawTool::Arrow || self.sent >= self.points.len());
        Some(json!({ "kind": "stroke", "id": self.id, "tool": self.tool.wire(), "color": self.color, "width": self.width, "points": part, "done": done }))
    }
}

// ---- Session reports ----

/// "1 h 05 min", "12 min", "45 s".
pub fn duration(seconds: i64) -> String {
    let seconds = seconds.max(0);
    if seconds < 60 {
        return format!("{seconds} s");
    }
    let minutes = seconds / 60;
    if minutes < 60 {
        return format!("{minutes} min");
    }
    format!("{} h {:02} min", minutes / 60, minutes % 60)
}

/// The report as CSV (UTF-8 with BOM, comma separated, quoted as needed; Excel and LibreOffice read it).
pub fn report_csv(rows: &[SessionRow]) -> String {
    fn cell(text: &str) -> String {
        // A leading = + - @ would be read as a formula by spreadsheets: quote it with a tab-free prefix.
        let text = if text.starts_with(['=', '+', '-', '@']) { format!("'{text}") } else { text.to_owned() };
        if text.contains([',', '"', '\n', '\r']) { format!("\"{}\"", text.replace('"', "\"\"")) } else { text }
    }
    let header = [t("Technician"), t("E-mail"), t("Device"), t("Remote ID"), t("Start"), t("End"), t("Duration (seconds)"), t("Type"), t("Joined as colleague"), t("Note"), t("Session ID")];
    let mut out = String::from("\u{feff}");
    out.push_str(&header.iter().map(|title| cell(title)).collect::<Vec<_>>().join(","));
    out.push_str("\r\n");
    for row in rows {
        let line = [
            row.technician_name.clone().unwrap_or_default(),
            row.technician_email.clone().unwrap_or_default(),
            row.device_name.clone(),
            row.connection_id.to_string(),
            row.started_at.clone(),
            row.ended_at.clone().unwrap_or_default(),
            row.duration_seconds.to_string(),
            if row.kind == "unattended" { t("Unattended") } else { t("Attended") },
            if row.colleague { t("Yes") } else { t("No") },
            row.note.clone().unwrap_or_default(),
            row.session_id.clone(),
        ];
        out.push_str(&line.iter().map(|value| cell(value)).collect::<Vec<_>>().join(","));
        out.push_str("\r\n");
    }
    out
}

/// A typed report day (YYYY-MM-DD) as the start of that day, or None when empty/invalid.
pub fn report_day(text: &str) -> Option<String> {
    let text = text.trim();
    (text.len() == 10 && format::parse_time(&format!("{text}T00:00:00Z")).is_some()).then(|| text.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn remote(phase: &str) -> RemoteStatus {
        RemoteStatus { phase: phase.into(), ..RemoteStatus::offline() }
    }

    fn device(id: u32) -> Device {
        Device { device: format!("d{id}"), connection_id: id, name: format!("PC {id}"), os: "windows".into(), ..Default::default() }
    }

    #[test]
    fn status_lines_follow_the_phase() {
        crate::i18n::set_choice("en");
        assert_eq!(connection_message(false, &remote("offline"), false), "Sign in to get your connection ID.");
        assert_eq!(connection_message(true, &remote("offline"), false), "Make this computer available to start or receive a connection.");
        assert_eq!(connection_message(true, &remote("ready"), false), "Ready for a connection.");
        let mut active = remote("active");
        active.control = true;
        assert_eq!(connection_message(true, &active, false), "Connected. Keyboard and mouse control is enabled.");
        active.sharing = true;
        assert!(connection_message(true, &active, false).starts_with("Sharing your selected screen"));
        assert_eq!(connection_message(true, &active, true), "Ending the session…");
        assert_eq!(connection_badge(&remote("error")).0, Badge::Attention);
        assert_eq!(connection_badge(&remote("ready")).0, Badge::Online);
    }

    #[test]
    fn device_presence_and_reasons() {
        crate::i18n::set_choice("en");
        let mut status = remote("ready");
        status.host = Some(111_111_111);
        status.online = vec![222_222_222];
        let checked: HashSet<u32> = [222_222_222, 333_333_333].into();
        let none = HashMap::new();
        assert_eq!(presence(&device(111_111_111), &status, &checked, &none), Presence::This);
        assert_eq!(presence(&device(222_222_222), &status, &checked, &none), Presence::Online);
        assert_eq!(presence(&device(333_333_333), &status, &checked, &none), Presence::Offline);
        assert_eq!(presence(&device(444_444_444), &status, &checked, &none), Presence::Checking);
        // Not connected: the connection service's last record decides.
        let offline = remote("offline");
        let server: HashMap<u32, SaasPresence> =
            [(222_222_222, SaasPresence { status: "online".into(), ..Default::default() }), (333_333_333, SaasPresence { status: "offline".into(), last_seen: Some("2026-11-13T09:55:00Z".into()), ..Default::default() })].into();
        assert_eq!(presence(&device(222_222_222), &offline, &checked, &server), Presence::Online);
        assert_eq!(presence(&device(333_333_333), &offline, &checked, &server), Presence::Offline);
        assert_eq!(presence(&device(444_444_444), &offline, &checked, &server), Presence::Unknown);
        let now = format::parse_time("2026-11-13T10:00:00Z").unwrap();
        let reason = presence_reason(&device(333_333_333), Presence::Offline, &offline, &server, now);
        assert_eq!(reason, "Last seen 5 minutes ago. Turned off, asleep or without internet. It must be online to connect.");
        assert_eq!(connect_blocked(&device(333_333_333), Presence::Offline, &status, false, true), "This device is offline.");
        assert_eq!(connect_blocked(&device(111_111_111), Presence::This, &status, false, true), "This is the computer you are using.");
        assert_eq!(connect_blocked(&device(222_222_222), Presence::Online, &status, false, true), "");
        assert_eq!(connect_blocked(&device(222_222_222), Presence::Online, &remote("active"), false, true), "End the current session first.");
    }

    #[test]
    fn company_rights_plan_and_usage() {
        crate::i18n::set_choice("en");
        let tenant = Tenant { tenant_id: "t".into(), name: "Acme".into(), role: "technician".into(), plan: "business".into(), status: "trial".into(), trial_ends_at: Some("2026-11-27T10:00:00Z".into()), ..Default::default() };
        let mut company = Company { tenants: vec![tenant], ..Default::default() };
        assert!(company.manages_devices() && !company.deploys() && !company.administers());
        assert_eq!(company.plan_line(), "Business trial until 27 Nov 2026");
        company.overview = Some(TenantOverview {
            role: Some("owner".into()),
            status: Some("active".into()),
            plan: Some("team".into()),
            limits: Some(Limits { devices: Some(10), technicians: Some(5) }),
            usage: Some(Usage { devices: 10, technicians: 2 }),
            features: Some([("groups".to_owned(), true)].into()),
            ..Default::default()
        });
        assert!(company.is_owner() && company.deploys() && company.groups_on());
        assert_eq!(company.plan_line(), "Team");
        assert_eq!(company.usage_line(), "10 of 10 devices · 2 of 5 technicians");
        assert_eq!(company.add_blocked(), "Device limit of your plan reached");
        company.overview.as_mut().unwrap().status = Some("read_only".into());
        assert!(company.read_only());
        assert_eq!(company.add_blocked(), "This company is read-only");
        assert_eq!(company.plan_line(), "Team · read-only");
    }

    #[test]
    fn setup_path_depends_on_the_account() {
        let company = Company::default();
        let staff = Account { staff: true, ..Default::default() };
        let personal = Account { has_personal: true, ..Default::default() };
        assert_eq!(setup_path(Some(&staff), &company), SetupPath::Team);
        assert_eq!(setup_path(Some(&personal), &company), SetupPath::Personal);
        assert_eq!(setup_path(Some(&Account::default()), &company), SetupPath::None);
        assert_eq!(setup_path(None, &company), SetupPath::None);
        let member = Company { tenants: vec![Tenant { role: "member".into(), ..Default::default() }], ..Default::default() };
        assert_eq!(setup_path(Some(&Account::default()), &member), SetupPath::CompanyMember);
        assert!(needs_setup("No saved device identity. Choose Create device identity to generate one locally."));
        assert!(!needs_setup("The connection service cannot be reached"));
    }

    #[test]
    fn clicks_map_through_the_aspect_fit_picture() {
        // A 1600 × 900 picture in a 1000 × 1000 view: shown 1000 × 562.5, centred vertically.
        let view = (1000.0, 1000.0);
        let image = (1600.0, 900.0);
        let (x, y) = picture_point(view, image, (500.0, 500.0), false).unwrap();
        assert!((x - 0.5).abs() < 1e-9 && (y - 0.5).abs() < 1e-9);
        assert_eq!(picture_point(view, image, (500.0, 100.0), false), None, "above the picture");
        assert_eq!(picture_point(view, image, (500.0, 100.0), true), Some((0.5, 0.0)));
        assert_eq!(normalized(0.0), 0);
        assert_eq!(normalized(1.0), 65535);
        assert_eq!(normalized(2.0), 65535);
        assert_eq!(engine_button(3), Some(3));
        assert_eq!(engine_button(8), Some(4));
        assert_eq!(engine_button(7), None);
    }

    #[test]
    fn input_queue_coalesces_moves_and_detects_stalls() {
        let mut queue = InputQueue::default();
        queue.push(input::pointer(1, 1));
        queue.push(input::pointer(2, 2));
        assert_eq!(queue.len(), 1);
        assert_eq!(queue.pop().unwrap()["x"], 2);
        for _ in 0..64 {
            assert_eq!(queue.push(input::key(4, true)), Queued::Ok);
        }
        assert_eq!(queue.push(input::key(4, false)), Queued::Stalled);
        assert_eq!(queue.len(), 0);
        let mut wheel = Wheel::default();
        assert_eq!(wheel.add(0.0, 1.0), Some((0, -3)));
        assert_eq!(wheel.add(0.0, -0.1), None, "fine deltas add up");
        assert_eq!(wheel.add(0.0, -0.1), None);
        assert_eq!(wheel.add(0.0, -0.2), Some((0, 1)));
        assert_eq!(wheel.add(-1.0, 0.0), Some((3, 0)));
    }

    #[test]
    fn restart_reconnects_when_the_computer_is_back() {
        let mut plan = Reconnect::new(222_222_222, Some(999_999_999), false, "s1".into(), 0.0);
        let mut status = remote("ready");
        status.online = vec![222_222_222];
        // Still online right after the restart click: only ask for presence.
        assert_eq!(plan.step(&status, false, 1.0), vec![ReconnectStep::Presence(vec![222_222_222, 999_999_999])]);
        status.online.clear();
        assert_eq!(plan.step(&status, false, 30.0), vec![ReconnectStep::Presence(vec![222_222_222, 999_999_999])]);
        assert!(plan.went_offline);
        // The sign-in screen access is online after 45 s: connect there (unattended).
        status.online = vec![999_999_999];
        assert_eq!(plan.step(&status, false, 50.0), vec![ReconnectStep::Presence(vec![222_222_222, 999_999_999]), ReconnectStep::Request { host: 999_999_999, unattended: true }]);
        // Not again within 15 s.
        assert_eq!(plan.step(&status, false, 56.0), vec![ReconnectStep::Presence(vec![222_222_222, 999_999_999])]);
        status.online = vec![222_222_222];
        assert_eq!(plan.step(&status, true, 70.0).last(), Some(&ReconnectStep::Presence(vec![222_222_222, 999_999_999])), "busy: no request");
        assert_eq!(plan.step(&status, false, 72.0).last(), Some(&ReconnectStep::Request { host: 222_222_222, unattended: false }));
        let mut back = remote("active");
        back.session = Some("s2".into());
        assert_eq!(plan.step(&back, false, 80.0), vec![ReconnectStep::Done]);
        assert_eq!(plan.step(&status, false, 700.0), vec![ReconnectStep::GaveUp]);
    }

    #[test]
    fn meeting_offers_and_requests() {
        let mut status = remote("active");
        status.sharing = true;
        status.session = Some("s".into());
        status.meeting_offer = Some(("s".into(), "room".into()));
        assert_eq!(meeting_offer(&status, ""), Some("room".into()));
        assert_eq!(meeting_offer(&status, "room"), None);
        status.session = Some("other".into());
        assert_eq!(meeting_offer(&status, ""), None);
        assert_eq!(request_args(123_456_789, Some(""), true), json!({"host": 123_456_789, "password": null, "unattended": true}));
        assert_eq!(request_args(123_456_789, Some("abc"), false)["password"], "abc");
    }

    #[test]
    fn strokes_are_sent_in_parts_and_arrows_whole() {
        let mut stroke = Stroke::new(7, DrawTool::Pen, 9, 0, (10, 10));
        assert_eq!((stroke.color, stroke.width), (4, 1), "clamped to the palette and sizes");
        for x in 0..20 {
            stroke.add((20 + x, 20));
        }
        let part = stroke.message(false).unwrap();
        assert_eq!(part["points"].as_array().unwrap().len(), 21);
        assert_eq!(part["done"], false);
        assert!(stroke.message(false).is_none(), "nothing new");
        stroke.add((99, 99));
        let last = stroke.message(true).unwrap();
        assert_eq!(last["points"].as_array().unwrap().len(), 1);
        assert_eq!(last["done"], true);
        let mut arrow = Stroke::new(1, DrawTool::Arrow, 0, 2, (0, 0));
        arrow.add((5, 5));
        arrow.add((9, 9));
        assert_eq!(arrow.message(true).unwrap()["points"], json!([[0, 0], [9, 9]]));
        assert!(draw_line_width(DrawTool::Highlighter, 4, 1080.0) > draw_line_width(DrawTool::Pen, 4, 1080.0));
    }

    #[test]
    fn report_csv_and_durations() {
        crate::i18n::set_choice("en");
        assert_eq!(duration(45), "45 s");
        assert_eq!(duration(720), "12 min");
        assert_eq!(duration(3900), "1 h 05 min");
        let row = SessionRow { technician_name: Some("Anna, K.".into()), device_name: "=cmd".into(), connection_id: 123_456_789, kind: "unattended".into(), note: Some("said \"hi\"".into()), ..Default::default() };
        let csv = report_csv(&[row]);
        assert!(csv.starts_with('\u{feff}'));
        assert!(csv.contains("\"Anna, K.\""));
        assert!(csv.contains("'=cmd"));
        assert!(csv.contains("\"said \"\"hi\"\"\""));
        assert!(csv.contains("Unattended"));
        assert_eq!(report_day("2026-11-01"), Some("2026-11-01".into()));
        assert_eq!(report_day("01.11.2026"), None);
    }
}
