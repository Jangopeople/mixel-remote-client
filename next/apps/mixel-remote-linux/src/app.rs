//! The app's state and actions: the account, the connection to Mixel, devices, company, settings,
//! setting up this computer, this computer as host, and the session. Reads the engine a few times a
//! second while something happens, rarely otherwise; the screens (`ui`) only show the state and call
//! these actions. Everything runs on the GTK main thread; engine calls run on the engine's runtime.
use crate::{
    engine::{self, Engine, Info},
    format,
    i18n::{self, t, tf},
    logic::{self, Company, Presence, Reconnect, ReconnectStep, Section, SetupPath, input},
    models::*,
    store::{Registration, Store, Unattended},
    video,
};
use gtk::{gdk, glib, prelude::*};
use serde::de::DeserializeOwned;
use serde_json::{Value, json};
use std::{
    cell::{Cell, RefCell},
    collections::{HashMap, HashSet},
    future::Future,
    rc::Rc,
    time::Duration,
};

/// Seconds since the app started (monotonic).
pub fn clock() -> f64 {
    use std::sync::OnceLock;
    static START: OnceLock<std::time::Instant> = OnceLock::new();
    START.get_or_init(std::time::Instant::now).elapsed().as_secs_f64()
}

#[derive(Clone, Debug, Default)]
pub struct DevicesState {
    pub devices: Vec<Device>,
    pub loading: bool,
    pub search: String,
    pub group_filter: Option<String>,
    pub groups: Vec<SaasGroup>,
    pub page: usize,
    pub has_next: bool,
    pub message: String,
    pub error: String,
    pub server_presence: HashMap<u32, SaasPresence>,
    pub deploy_link: Option<DeployLinkCreated>,
    pub checked: HashSet<u32>,
    cursors: Vec<Option<u32>>,
    next_cursor: Option<u32>,
    company_all: Vec<Device>,
    last_presence: f64,
}

#[derive(Clone, Debug, Default)]
pub struct CompanyState {
    pub data: Company,
    pub error: String,
    pub notice: String,
    pub working: bool,
}

#[derive(Clone, Debug, Default)]
pub struct SettingsState {
    pub startup: Option<StartupSettings>,
    pub printing: Option<PrintingStatus>,
    pub update: Option<UpdateInfo>,
    pub update_checked: bool,
    pub removal_pending: bool,
    pub screen_access: bool,
    pub signin: Option<SignInScreen>,
    pub error: String,
    pub notice: String,
    pub removal_result: String,
}

#[derive(Clone, Debug, Default)]
pub struct EnrollmentState {
    pub needs_setup: bool,
    pub current: Option<Registration>,
    pub name: String,
    pub error: String,
    pub working: bool,
    pub companies: Vec<EnrollmentCompany>,
    pub records: Vec<InventoryDevice>,
}

#[derive(Clone, Debug, Default)]
pub struct HostState {
    pub sources: Vec<CaptureTarget>,
    pub source: String,
    pub confirm_display: bool,
    pub allow_control: bool,
    pub allow_clipboard: bool,
    pub allow_files: bool,
    pub allow_chat: bool,
    pub allow_recording: bool,
    pub screen_access: bool,
    pub error: String,
    pub unattended_sources: Vec<CaptureTarget>,
    pub unattended_source: String,
    pub unattended_confirm: bool,
    pub unattended_control: bool,
    restored: bool,
}

impl HostState {
    /// A technician asks to connect and the person here decides.
    pub fn selected(&self) -> Option<&CaptureTarget> {
        self.sources.iter().find(|source| source.id == self.source)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tool {
    Chat,
    Files,
    Clipboard,
    Assistant,
}

/// The session assistant on the company's own AI server, and the ISM ticket note (Mixel team).
#[derive(Clone, Debug, Default)]
pub struct AssistantState {
    pub connected: bool,
    pub address: String,
    pub model: String,
    pub models: Vec<String>,
    pub status: String,
    pub busy: bool,
    pub answer: String,
    pub ticket_status: String,
    log: Vec<String>,
    session: Option<String>,
    started: f64,
}

#[derive(Clone, Debug, Default)]
pub struct SessionState {
    pub size: (usize, usize),
    pub fps: usize,
    pub has_frame: bool,
    pub stopping: bool,
    pub error: String,
    pub notice: String,
    pub privacy: bool,
    pub block_input: bool,
    pub lock_when_done: bool,
    pub pointer_mode: bool,
    pub sound: bool,
    pub restart_armed: bool,
    pub chat: Vec<ChatMessage>,
    pub files: Option<FileView>,
    pub clipboard_result: String,
    pub recording_pending: bool,
    pub meeting_notice: String,
    pub dismissed_meeting_room: String,
    pub tool: Option<Tool>,
    pub invite_link: Option<MeetingRoom>,
    pub last_request_unattended: bool,
    pub assistant: AssistantState,
    viewing_session: Option<String>,
    toggle_session: Option<String>,
    stopped_consent: Option<String>,
    reconnect: Option<Reconnect>,
    tools_running: bool,
}

#[derive(Clone, Debug, Default)]
pub struct State {
    pub section: Section,
    pub account: AccountStatus,
    pub remote: RemoteStatus,
    pub error: String,
    pub progress: String,
    pub busy: bool,
    pub sign_in_link: Option<String>,
    /// Outside sign-ins the sign-in service offers (`keycloak` = Mixel ID, `google`, `azure`, `apple`).
    pub providers: Vec<String>,
    pub linux: LinuxSession,
    pub devices: DevicesState,
    pub company: CompanyState,
    pub settings: SettingsState,
    pub enrollment: EnrollmentState,
    pub host: HostState,
    pub session: SessionState,
    went_offline: bool,
    auto_tries: usize,
    next_auto_try: f64,
    last_account_read: f64,
    account_key: String,
}

impl State {
    pub fn signed_in(&self) -> bool {
        self.account.signed_in()
    }
    pub fn staff(&self) -> bool {
        self.account.account.as_ref().is_some_and(|account| account.staff)
    }
    pub fn asking(&self) -> bool {
        self.remote.phase == "incoming" && !self.remote.unattended_session && !self.remote.controller_only
    }
    pub fn can_accept(&self) -> bool {
        if !self.asking() || self.busy {
            return false;
        }
        match self.host.selected() {
            Some(selected) => !selected.full_display || self.host.confirm_display,
            None => false,
        }
    }
    pub fn control_available(&self) -> bool {
        self.asking() && self.host.selected().is_some_and(|source| source.full_display) && self.remote.input_access
    }
    /// The picture is shown and keyboard and mouse go to the other computer.
    pub fn controllable(&self) -> bool {
        !self.session.stopping && self.remote.viewing() && self.remote.control && self.session.has_frame && !self.locally_stopped()
    }
    fn locally_stopped(&self) -> bool {
        self.remote.consent.is_some() && self.remote.consent == self.session.stopped_consent
    }
    pub fn can_switch_sides(&self) -> bool {
        self.remote.phase == "active" && self.remote.peer_features.iter().any(|feature| feature == "switch")
    }
    pub fn connection_message(&self) -> String {
        logic::connection_message(self.signed_in(), &self.remote, self.session.stopping)
    }
    pub fn setup_path(&self) -> SetupPath {
        logic::setup_path(self.account.account.as_ref(), &self.company.data)
    }
    pub fn presence(&self, device: &Device) -> Presence {
        logic::presence(device, &self.remote, &self.devices.checked, &self.devices.server_presence)
    }
    /// This computer is already one of the listed devices.
    pub fn registered_here(&self) -> bool {
        self.remote.host.is_some_and(|host| host > 0) || self.enrollment.current.is_some()
    }
}

/// What the screens offer the app to ask the person (file panels, links).
pub trait Shell {
    fn open_uri(&self, uri: &str);
    fn choose_file(&self) -> std::pin::Pin<Box<dyn Future<Output = Option<String>>>>;
    fn choose_save(&self, suggested: &str) -> std::pin::Pin<Box<dyn Future<Output = Option<String>>>>;
    fn copy(&self, text: &str);
    fn notify(&self, text: &str);
}

pub struct App {
    pub engine: Rc<dyn Engine>,
    pub info: Info,
    pub state: RefCell<State>,
    pub store: RefCell<Store>,
    /// The current picture of the session.
    pub texture: RefCell<Option<gdk::Texture>>,
    pub ui: RefCell<Option<Rc<crate::ui::Ui>>>,
    pub shell: RefCell<Option<Rc<dyn Shell>>>,
    /// True while the screens set widget values (their signal handlers then do nothing).
    pub rendering: Cell<bool>,
    render_pending: Cell<bool>,
    queue: RefCell<logic::InputQueue>,
    sending: Cell<bool>,
    video_generation: Cell<u64>,
    shown: RefCell<Vec<f64>>,
    started: Cell<bool>,
}

type Work = std::pin::Pin<Box<dyn Future<Output = Result<(), String>>>>;

impl App {
    pub fn new(engine: Rc<dyn Engine>, store: Store) -> Rc<Self> {
        let info = engine.info();
        i18n::set_choice(&store.settings.language);
        let mut state = State { account: AccountStatus::signed_out(), remote: RemoteStatus::offline(), ..State::default() };
        state.enrollment.current = store.settings.this_computer.clone();
        state.session.sound = true;
        Rc::new(Self {
            engine,
            info,
            state: RefCell::new(state),
            store: RefCell::new(store),
            texture: RefCell::new(None),
            ui: RefCell::new(None),
            shell: RefCell::new(None),
            rendering: Cell::new(false),
            render_pending: Cell::new(false),
            queue: RefCell::new(logic::InputQueue::default()),
            sending: Cell::new(false),
            video_generation: Cell::new(0),
            shown: RefCell::new(Vec::new()),
            started: Cell::new(false),
        })
    }

    // ---- Plumbing ----

    pub async fn run<T: DeserializeOwned>(&self, command: &str, args: Value) -> Result<T, String> {
        engine::run(self.engine.as_ref(), command, args).await
    }
    pub async fn perform(&self, command: &str, args: Value) -> Result<(), String> {
        self.engine.call(command, args).await.map(|_| ())
    }
    pub fn shell(&self) -> Option<Rc<dyn Shell>> {
        self.shell.borrow().clone()
    }
    pub fn open_uri(&self, uri: &str) {
        if let Some(shell) = self.shell() {
            shell.open_uri(uri);
        }
    }

    /// The screens show the state again (once per main-loop turn, however often this is called).
    pub fn changed(self: &Rc<Self>) {
        if self.render_pending.replace(true) {
            return;
        }
        let app = self.clone();
        glib::idle_add_local_once(move || {
            app.render_pending.set(false);
            app.render_now();
        });
    }

    pub fn render_now(self: &Rc<Self>) {
        let ui = self.ui.borrow().clone();
        if let Some(ui) = ui {
            let state = self.state.borrow().clone();
            self.rendering.set(true);
            ui.render(self, &state);
            self.rendering.set(false);
        }
    }

    pub fn edit(self: &Rc<Self>, change: impl FnOnce(&mut State)) {
        change(&mut self.state.borrow_mut());
        self.changed();
    }

    pub fn spawn(self: &Rc<Self>, future: impl Future<Output = ()> + 'static) {
        glib::spawn_future_local(future);
    }

    /// Runs an action; its failure shows in `error`. The engine is read again afterwards.
    pub fn act(self: &Rc<Self>, work: impl FnOnce(Rc<App>) -> Work) {
        if self.state.borrow().busy {
            return;
        }
        self.edit(|state| {
            state.busy = true;
            state.error.clear();
        });
        let app = self.clone();
        let future = work(app.clone());
        self.spawn(async move {
            let result = future.await;
            app.edit(|state| {
                state.busy = false;
                if let Err(error) = result {
                    state.error = i18n::engine(&error);
                }
            });
            app.refresh_remote().await;
        });
    }

    // ---- The loop ----

    /// Starts reading the engine. Idempotent.
    pub fn start(self: &Rc<Self>) {
        if self.started.replace(true) {
            return;
        }
        let app = self.clone();
        self.spawn(async move {
            if let Ok(linux) = app.run::<LinuxSession>("linux_session", json!({})).await {
                app.edit(|state| state.linux = linux);
            }
            if let Ok(providers) = app.run::<Vec<String>>("sign_in_providers", json!({})).await {
                app.edit(|state| state.providers = providers);
            }
            app.refresh_account().await;
            app.flush_pending_removal().await;
            let saved = app.store.borrow().settings.ai.clone().unwrap_or_default();
            app.ai_connect(saved.address, saved.model, false);
            loop {
                app.tick().await;
                let fast = {
                    let state = app.state.borrow();
                    state.remote.in_session() || matches!(state.remote.phase.as_str(), "connecting" | "outgoing") || state.account.signing_in()
                };
                glib::timeout_future(Duration::from_millis(if fast { 250 } else { 1000 })).await;
            }
        });
    }

    async fn tick(self: &Rc<Self>) {
        let (every, last) = {
            let state = self.state.borrow();
            (if state.account.signing_in() || state.account.phase == "mfa_required" { 1.0 } else { 10.0 }, state.last_account_read)
        };
        if clock() - last >= every || last == 0.0 {
            self.refresh_account().await;
        }
        self.refresh_remote().await;
    }

    pub async fn refresh_account(self: &Rc<Self>) {
        self.state.borrow_mut().last_account_read = clock().max(f64::MIN_POSITIVE);
        let Ok(next) = self.run::<AccountStatus>("account_status", json!({})).await else { return };
        let key = if next.signed_in() {
            let account = next.account.clone().unwrap_or_default();
            format!("{}|{}|{}", account.profile, account.organization.unwrap_or_default(), account.personal)
        } else {
            String::new()
        };
        let changed = {
            let mut state = self.state.borrow_mut();
            state.account = next;
            if key != state.account_key {
                state.account_key = key;
                state.sign_in_link = None;
                true
            } else {
                false
            }
        };
        self.changed();
        if changed {
            self.account_changed();
        }
    }

    pub async fn refresh_remote(self: &Rc<Self>) {
        let Ok(next) = self.run::<RemoteStatus>("remote_status", json!({})).await else { return };
        let previous = {
            let mut state = self.state.borrow_mut();
            let previous = std::mem::replace(&mut state.remote, next.clone());
            if next.phase == "ready" {
                state.auto_tries = 0;
                state.next_auto_try = 0.0;
            }
            previous
        };
        if previous != next {
            self.changed();
        }
        self.session_status_changed(&previous, &next);
        self.host_status_changed(&previous, &next);
        self.devices_status_changed(&previous, &next);
        if !next.host_macs.is_empty()
            && let Some(peer) = next.peer
            && next.viewing()
        {
            let mut store = self.store.borrow_mut();
            if store.remember_macs(peer, &next.host_macs) {
                store.save();
            }
        }
        self.go_online_if_needed().await;
    }

    /// A set-up computer goes online by itself once someone is signed in, so the person who needs
    /// help never has to look for a button. Not again after "Go offline", with a growing pause.
    async fn go_online_if_needed(self: &Rc<Self>) {
        {
            let mut state = self.state.borrow_mut();
            if !state.signed_in() || state.went_offline || !state.remote.disconnected() || state.busy || clock() < state.next_auto_try || state.enrollment.needs_setup {
                return;
            }
            const PAUSES: [f64; 5] = [0.0, 5.0, 15.0, 30.0, 60.0];
            state.auto_tries += 1;
            state.next_auto_try = clock() + PAUSES[state.auto_tries.min(PAUSES.len() - 1)];
        }
        if let Err(error) = self.perform("remote_register", json!({})).await {
            self.register_failed(&error);
        }
    }

    fn account_changed(self: &Rc<Self>) {
        self.edit(|state| {
            state.error.clear();
            state.devices = DevicesState::default();
            state.company = CompanyState::default();
            state.enrollment.needs_setup = false;
            state.enrollment.error.clear();
            state.enrollment.companies.clear();
            state.enrollment.records.clear();
        });
        if self.state.borrow().signed_in() {
            let app = self.clone();
            self.spawn(async move {
                app.load_company().await;
                app.check_enrollment().await;
                app.load_devices(0).await;
                app.load_settings().await;
            });
        }
    }

    // ---- Sign-in ----

    /// Sign-in in the system browser; `provider`: Mixel ID (None), `google`, `azure` or `apple`.
    pub fn sign_in(self: &Rc<Self>, show_link: bool, provider: Option<String>) {
        self.act(move |app| {
            Box::pin(async move {
                let url: String = app.run("account_sign_in", json!({ "open_browser": !show_link, "provider": provider })).await?;
                if show_link {
                    app.edit(|state| state.sign_in_link = Some(url));
                }
                app.refresh_account().await;
                Ok(())
            })
        });
    }

    pub fn cancel_sign_in(self: &Rc<Self>) {
        self.act(|app| {
            Box::pin(async move {
                app.perform("account_cancel", json!({})).await?;
                app.refresh_account().await;
                Ok(())
            })
        });
    }

    pub fn verify(self: &Rc<Self>, factor: String, code: &str) {
        let code: String = code.chars().filter(char::is_ascii_digit).collect();
        if code.len() != 6 {
            self.edit(|state| state.error = t("Enter the 6-digit code"));
            return;
        }
        self.act(move |app| {
            Box::pin(async move {
                app.perform("account_verify", json!({ "factor": factor, "code": code })).await?;
                app.refresh_account().await;
                Ok(())
            })
        });
    }

    pub fn sign_out(self: &Rc<Self>) {
        self.act(|app| {
            Box::pin(async move {
                let _ = app.perform("remote_disconnect", json!({})).await;
                app.perform("set_stay_signed_in", json!({ "enabled": false })).await?;
                app.perform("account_cancel", json!({})).await?;
                app.refresh_account().await;
                Ok(())
            })
        });
    }

    pub fn set_workspace(self: &Rc<Self>, personal: bool) {
        self.act(move |app| {
            Box::pin(async move {
                if !app.state.borrow().remote.disconnected() {
                    app.perform("remote_disconnect", json!({})).await?;
                }
                app.perform("account_workspace", json!({ "personal": personal })).await?;
                app.refresh_account().await;
                Ok(())
            })
        });
    }

    pub fn open_company_settings(self: &Rc<Self>, company: String) {
        self.act(move |app| {
            Box::pin(async move {
                let url: String = app.run("company_settings_url", json!({ "company_id": company })).await?;
                app.open_uri(&url);
                Ok(())
            })
        });
    }

    // ---- The connection ----

    pub fn go_online(self: &Rc<Self>) {
        self.edit(|state| {
            state.went_offline = false;
            state.auto_tries = 0;
            state.next_auto_try = 0.0;
        });
        self.act(|app| {
            Box::pin(async move {
                if let Err(error) = app.perform("remote_register", json!({})).await {
                    app.register_failed(&error);
                    if !app.state.borrow().enrollment.needs_setup {
                        return Err(error);
                    }
                }
                Ok(())
            })
        });
    }

    pub fn go_offline(self: &Rc<Self>) {
        self.edit(|state| state.went_offline = true);
        self.clear_video();
        self.act(|app| Box::pin(async move { app.perform("remote_disconnect", json!({})).await }));
    }

    /// Connects to a Remote ID: brings this computer online first if needed; for a phone with Mixel
    /// Quick Support it claims the phone and waits until it is online, so one click is enough.
    pub fn connect(self: &Rc<Self>, id: u32, password: Option<String>, unattended: bool) {
        if self.state.borrow().busy {
            self.edit(|state| state.progress = t("Please wait a moment, the previous step is still running."));
            return;
        }
        self.edit(|state| {
            state.busy = true;
            state.error.clear();
            state.section = Section::Remote;
        });
        let app = self.clone();
        self.spawn(async move {
            let result = app.connect_steps(id, password, unattended).await;
            app.edit(|state| {
                state.busy = false;
                if let Err(error) = result {
                    state.error = i18n::engine(&error);
                    state.progress.clear();
                }
            });
            app.refresh_remote().await;
        });
    }

    async fn status(&self) -> Option<RemoteStatus> {
        self.run("remote_status", json!({})).await.ok()
    }

    async fn wait_until(self: &Rc<Self>, seconds: u32, until: impl Fn(&RemoteStatus) -> bool, ask: Option<Vec<u32>>) -> bool {
        for round in 0..seconds {
            if let Some(status) = self.status().await
                && until(&status)
            {
                return true;
            }
            if let Some(hosts) = &ask
                && round % 2 == 0
            {
                let _ = self.perform("remote_presence", json!({ "hosts": hosts })).await;
            }
            glib::timeout_future(Duration::from_secs(1)).await;
        }
        false
    }

    async fn connect_steps(self: &Rc<Self>, id: u32, password: Option<String>, unattended: bool) -> Result<(), String> {
        self.edit(|state| state.went_offline = false);
        if self.status().await.is_some_and(|status| status.disconnected()) {
            self.edit(|state| state.progress = t("Getting this computer ready…"));
            self.perform("remote_register", json!({})).await?;
            if !self.wait_until(20, |status| status.phase == "ready", None).await {
                return Err("This computer could not go online. Check the internet connection and try again.".into());
            }
        }
        self.edit(|state| state.progress = t("Looking up this ID…"));
        let phone: bool = match self.run("quick_phone_claim", json!({ "host": id })).await {
            Ok(phone) => phone,
            Err(error) if ["MFA", "staff", "42501"].iter().any(|word| error.contains(word)) => {
                return Err("Connecting to phones needs your Mixel team account with the second confirmation step. Sign in again under Account, then choose Connect.".into());
            }
            Err(error) => return Err(format!("This phone could not be prepared for the session: {error}")),
        };
        if phone {
            self.edit(|state| state.progress = t("Phone found. Waiting for it to come online… On an iPhone the person taps “Start sharing my screen”."));
            if !self.wait_until(120, |status| status.online.contains(&id), Some(vec![id])).await {
                return Err("The phone did not come online. Ask the person to keep Mixel Quick Support open, then choose Connect again.".into());
            }
            self.edit(|state| state.progress = t("Connecting… The person allows the session on their phone."));
        }
        if let Some(status) = self.status().await
            && status.phase != "ready"
        {
            return Err(if matches!(status.phase.as_str(), "active" | "incoming" | "outgoing") {
                "End the current session first, then connect again.".into()
            } else {
                "This computer is not ready to connect yet. Please try again in a moment.".into()
            });
        }
        let unattended = unattended && !phone;
        self.perform("remote_request", logic::request_args(id, password.as_deref(), unattended)).await?;
        self.edit(|state| {
            state.session.last_request_unattended = unattended;
            state.progress = t("Waiting for the other person to accept your request.");
        });
        Ok(())
    }

    /// Before the app quits: end sessions, go offline (at most 15 s).
    pub async fn shutdown(self: &Rc<Self>) {
        self.clear_video();
        let engine = self.engine.clone();
        let stop = engine.shutdown();
        let _ = futures_timeout(stop, Duration::from_secs(15)).await;
        // As the desktop app on exit: no clipboard, files or print jobs of a session stay behind.
        mixel_engine::session_platform::close_clipboard();
        mixel_engine::session_platform::clear_clipboard_files();
        mixel_engine::host_print::clear_received();
    }

    // ---- Devices ----

    pub async fn load_devices(self: &Rc<Self>, wanted: usize) {
        if !self.state.borrow().signed_in() {
            self.edit(|state| state.devices = DevicesState::default());
            return;
        }
        self.edit(|state| {
            state.devices.loading = true;
            state.devices.error.clear();
        });
        let result = self.load_devices_page(wanted).await;
        self.edit(|state| {
            state.devices.loading = false;
            match result {
                Ok(()) => {
                    state.devices.message = if state.devices.devices.is_empty() { String::new() } else { tf("Page %d · %d devices.", &[&(state.devices.page + 1), &state.devices.devices.len()]) };
                }
                Err(error) => state.devices.error = i18n::engine(&error),
            }
        });
        self.check_presence(true).await;
    }

    async fn load_devices_page(self: &Rc<Self>, wanted: usize) -> Result<(), String> {
        const PAGE: usize = 50;
        let (tenant, search, group, manages, groups_on, need_all) = {
            let state = self.state.borrow();
            let company = &state.company.data;
            (company.tenant().map(|tenant| tenant.tenant_id.clone()), state.devices.search.clone(), state.devices.group_filter.clone(), company.manages_devices(), company.groups_on(), wanted == 0 || state.devices.company_all.is_empty())
        };
        if let Some(tenant) = tenant {
            if need_all {
                let search = if search.is_empty() { None } else { Some(search) };
                let rows: Vec<SaasDevice> = self.run("saas_devices", json!({ "tenant": tenant, "group": group, "search": search })).await?;
                let all: Vec<Device> = rows.iter().filter_map(SaasDevice::device).collect();
                let groups: Vec<SaasGroup> = if manages && groups_on { self.run("saas_groups", json!({ "tenant": tenant })).await.unwrap_or_default() } else { Vec::new() };
                let presence: Vec<SaasPresence> = self.run("saas_device_presence", json!({ "tenant": tenant })).await.unwrap_or_default();
                self.edit(|state| {
                    state.devices.company_all = all;
                    state.devices.groups = groups;
                    state.devices.server_presence = presence.into_iter().filter_map(|row| row.connection_id.map(|id| (id, row))).collect();
                });
            }
            self.edit(|state| {
                let all = &state.devices.company_all;
                let page = wanted.min(all.len().saturating_sub(1) / PAGE);
                state.devices.page = page;
                state.devices.devices = all.iter().skip(page * PAGE).take(PAGE).cloned().collect();
                state.devices.has_next = (page + 1) * PAGE < all.len();
            });
        } else {
            let cursor = {
                let mut state = self.state.borrow_mut();
                if wanted == 0 {
                    state.devices.cursors = vec![None];
                }
                if wanted < state.devices.cursors.len() { state.devices.cursors[wanted] } else { state.devices.next_cursor }
            };
            let result: DevicePage = self.run("remote_devices", json!({ "after": cursor, "search": search })).await?;
            self.edit(|state| {
                state.devices.devices = result.devices;
                state.devices.page = wanted;
                state.devices.next_cursor = result.next;
                state.devices.has_next = result.next.is_some();
                if state.devices.cursors.len() <= wanted + 1 {
                    state.devices.cursors.push(result.next);
                } else {
                    state.devices.cursors[wanted + 1] = result.next;
                }
            });
        }
        Ok(())
    }

    pub fn devices_page(self: &Rc<Self>, delta: isize) {
        let page = self.state.borrow().devices.page as isize + delta;
        let app = self.clone();
        self.spawn(async move { app.load_devices(page.max(0) as usize).await });
    }

    pub fn reload_devices(self: &Rc<Self>) {
        let app = self.clone();
        self.spawn(async move { app.load_devices(0).await });
    }

    /// Asks the connection service which listed devices are online (every 15 s while connected).
    async fn check_presence(self: &Rc<Self>, force: bool) {
        let hosts: Vec<u32> = {
            let mut state = self.state.borrow_mut();
            if !state.remote.connected() || state.devices.devices.is_empty() || (!force && clock() - state.devices.last_presence < 15.0) {
                return;
            }
            state.devices.last_presence = clock();
            let own = state.remote.host;
            state.devices.devices.iter().map(|device| device.connection_id).filter(|id| Some(*id) != own).take(100).collect()
        };
        if hosts.is_empty() {
            return;
        }
        let _ = self.perform("remote_presence", json!({ "hosts": hosts })).await;
        // The answer arrives in remote_status.online; the next read shows it.
        glib::timeout_future(Duration::from_millis(600)).await;
        self.edit(|state| state.devices.checked.extend(hosts));
    }

    fn devices_status_changed(self: &Rc<Self>, previous: &RemoteStatus, next: &RemoteStatus) {
        if next.connected() && !previous.connected() {
            self.state.borrow_mut().devices.last_presence = f64::MIN;
        }
        if next.connected() {
            let app = self.clone();
            self.spawn(async move { app.check_presence(false).await });
        }
    }

    pub fn connect_device(self: &Rc<Self>, device: &Device) {
        let name = device.name.clone();
        self.edit(|state| state.devices.message = tf("Connecting to %@…", &[&name]));
        self.connect(device.connection_id, None, false);
    }

    /// A company change, then the list again; `false` with the reason in `devices.error`.
    async fn device_change(self: &Rc<Self>, work: Work) -> bool {
        self.edit(|state| state.devices.error.clear());
        match work.await {
            Ok(()) => {
                self.load_devices(0).await;
                true
            }
            Err(error) => {
                self.edit(|state| state.devices.error = i18n::engine(&error));
                false
            }
        }
    }

    fn tenant_id(&self) -> Option<String> {
        self.state.borrow().company.data.tenant().map(|tenant| tenant.tenant_id.clone())
    }

    pub async fn rename_device(self: &Rc<Self>, device: String, name: String) -> Result<(), String> {
        let tenant = self.tenant_id().ok_or("No company")?;
        let app = self.clone();
        self.result_of(self.device_change(Box::pin(async move { app.perform("saas_rename_device", json!({ "tenant": tenant, "device": device, "name": name })).await })).await, |state| state.devices.error.clone())
    }

    pub async fn move_device(self: &Rc<Self>, device: String, group: Option<String>) -> Result<(), String> {
        let tenant = self.tenant_id().ok_or("No company")?;
        let app = self.clone();
        self.result_of(self.device_change(Box::pin(async move { app.perform("saas_move_device", json!({ "tenant": tenant, "device": device, "group": group })).await })).await, |state| state.devices.error.clone())
    }

    pub async fn remove_device(self: &Rc<Self>, device: String) -> Result<(), String> {
        let tenant = self.tenant_id().ok_or("No company")?;
        let app = self.clone();
        self.result_of(self.device_change(Box::pin(async move { app.perform("saas_remove_device", json!({ "tenant": tenant, "device": device })).await })).await, |state| state.devices.error.clone())
    }

    pub async fn create_group(self: &Rc<Self>, name: String) -> Result<(), String> {
        let tenant = self.tenant_id().ok_or("No company")?;
        let app = self.clone();
        self.result_of(self.device_change(Box::pin(async move { app.perform("saas_create_group", json!({ "tenant": tenant, "name": name })).await })).await, |state| state.devices.error.clone())
    }

    /// "Add other computers": a deploy link with the command for each system (shown once).
    pub async fn create_deploy_link(self: &Rc<Self>, label: String, days: u32, computers: u32, group: Option<String>) -> Result<(), String> {
        let tenant = self.tenant_id().ok_or("No company")?;
        let app = self.clone();
        self.result_of(
            self.device_change(Box::pin(async move {
                let link: DeployLinkCreated = app.run("saas_create_deploy_link", json!({ "tenant": tenant, "label": label, "days": days, "computers": computers, "group": group })).await?;
                app.edit(|state| state.devices.deploy_link = Some(link));
                Ok(())
            }))
            .await,
            |state| state.devices.error.clone(),
        )
    }

    /// "Add this computer": this computer's device key becomes one of the company's devices.
    pub async fn add_this_computer(self: &Rc<Self>, name: String, group: Option<String>) -> Result<(), String> {
        let tenant = self.tenant_id().ok_or("No company")?;
        let was_online = !self.state.borrow().remote.disconnected();
        let app = self.clone();
        let tenant_for = tenant.clone();
        let done = self
            .device_change(Box::pin(async move {
                if was_online {
                    app.perform("remote_disconnect", json!({})).await?;
                }
                let enrolled: SaasEnrollment = app.run("saas_add_this_device", json!({ "tenant": tenant_for, "name": name, "group": group })).await?;
                app.remember_registration(Registration { device: enrolled.device_id, connection_id: enrolled.connection_id, generation: enrolled.generation, tenant: Some(tenant_for) });
                Ok(())
            }))
            .await;
        if done {
            self.go_online();
            self.load_overview().await;
        }
        self.result_of(done, |state| state.devices.error.clone())
    }

    pub fn wake(self: &Rc<Self>, device: &Device, macs: Vec<String>) {
        let name = device.name.clone();
        self.act(move |app| {
            Box::pin(async move {
                app.perform("wake_on_lan", json!({ "macs": macs })).await?;
                app.edit(|state| state.devices.message = tf("Wake signal sent to %@. It shows Online when it has started.", &[&name]));
                Ok(())
            })
        });
    }

    pub fn known_macs(&self, id: u32) -> Vec<String> {
        self.store.borrow().settings.macs.get(&id).cloned().unwrap_or_default()
    }

    fn result_of(&self, ok: bool, error: impl FnOnce(&State) -> String) -> Result<(), String> {
        if ok { Ok(()) } else { Err(error(&self.state.borrow())) }
    }

    // ---- Company ----

    pub async fn load_company(self: &Rc<Self>) {
        if !self.state.borrow().signed_in() {
            self.edit(|state| state.company = CompanyState::default());
            return;
        }
        match self.run::<Vec<Tenant>>("saas_my_tenants", json!({})).await {
            Ok(tenants) => {
                self.edit(|state| state.company.data.tenants = tenants);
                self.load_overview().await;
                self.load_people().await;
            }
            // Mixel team accounts, ISM companies and personal spaces have no Remote company.
            Err(_) => self.edit(|state| state.company.data.tenants.clear()),
        }
        self.edit(|state| state.company.data.loaded = true);
    }

    async fn load_overview(self: &Rc<Self>) {
        let Some(tenant) = self.tenant_id() else {
            self.edit(|state| state.company.data.overview = None);
            return;
        };
        let overview = self.run::<TenantOverview>("saas_tenant_overview", json!({ "tenant": tenant })).await.ok();
        self.edit(|state| state.company.data.overview = overview);
    }

    async fn load_people(self: &Rc<Self>) {
        let Some(tenant) = self.tenant_id() else { return };
        let members: Vec<Member> = self.run("saas_members", json!({ "tenant": tenant })).await.unwrap_or_default();
        let administers = self.state.borrow().company.data.administers();
        let invites: Vec<Invite> = if administers { self.run("saas_invites", json!({ "tenant": tenant })).await.unwrap_or_default() } else { Vec::new() };
        self.edit(|state| {
            state.company.data.members = members;
            state.company.data.invites = invites;
        });
    }

    /// A company change; afterwards overview and people again. The error also stays in `company.error`.
    async fn company_change(self: &Rc<Self>, work: Work) -> Result<(), String> {
        self.edit(|state| {
            state.company.error.clear();
            state.company.notice.clear();
            state.company.working = true;
        });
        let result = work.await;
        self.load_overview().await;
        self.load_people().await;
        self.edit(|state| {
            state.company.working = false;
            if let Err(error) = &result {
                state.company.error = i18n::engine(error);
            }
        });
        result.map_err(|error| i18n::engine(&error))
    }

    pub async fn invite_member(self: &Rc<Self>, email: String, role: String) -> Result<(), String> {
        let tenant = self.tenant_id().ok_or("No company")?;
        let email = email.trim().to_owned();
        if !email.contains('@') || email.len() > 254 {
            let error = t("Enter an e-mail address");
            self.edit(|state| state.company.error = error.clone());
            return Err(error);
        }
        let app = self.clone();
        self.company_change(Box::pin(async move {
            let _: String = app.run("saas_invite_member", json!({ "tenant": tenant, "email": email, "role": role, "locale": i18n::language() })).await?;
            app.edit(|state| state.company.notice = tf("Invitation sent to %@.", &[&email]));
            Ok(())
        }))
        .await
    }

    pub fn resend_invite(self: &Rc<Self>, invite: Invite) {
        let Some(tenant) = self.tenant_id() else { return };
        let app = self.clone();
        self.spawn(async move {
            let inner = app.clone();
            let _ = app
                .company_change(Box::pin(async move {
                    inner.perform("saas_resend_invite", json!({ "tenant": tenant, "invite": invite.id, "locale": i18n::language() })).await?;
                    inner.edit(|state| state.company.notice = tf("Invitation sent again to %@.", &[&invite.email]));
                    Ok(())
                }))
                .await;
        });
    }

    pub fn revoke_invite(self: &Rc<Self>, invite: Invite) {
        let Some(tenant) = self.tenant_id() else { return };
        let app = self.clone();
        self.spawn(async move {
            let inner = app.clone();
            let _ = app.company_change(Box::pin(async move { inner.perform("saas_revoke_invite", json!({ "tenant": tenant, "invite": invite.id })).await })).await;
        });
    }

    pub fn set_member_role(self: &Rc<Self>, member: Member, role: &'static str) {
        let Some(tenant) = self.tenant_id() else { return };
        let app = self.clone();
        self.spawn(async move {
            let inner = app.clone();
            let _ = app.company_change(Box::pin(async move { inner.perform("saas_set_member_role", json!({ "tenant": tenant, "member": member.user_id, "role": role })).await })).await;
        });
    }

    /// Removes a member; for oneself: leaves the company.
    pub fn remove_member(self: &Rc<Self>, member: Member) {
        let Some(tenant) = self.tenant_id() else { return };
        let app = self.clone();
        self.spawn(async move {
            let inner = app.clone();
            let user = member.user_id.clone();
            let _ = app.company_change(Box::pin(async move { inner.perform("saas_remove_member", json!({ "tenant": tenant, "member": user })).await })).await;
            let me = app.state.borrow().account.account.as_ref().map(|account| account.profile.clone());
            if me.as_deref() == Some(member.user_id.as_str()) {
                app.refresh_account().await;
                app.load_company().await;
            }
        });
    }

    pub async fn rename_company(self: &Rc<Self>, name: String) -> Result<(), String> {
        let tenant = self.tenant_id().ok_or("No company")?;
        let app = self.clone();
        let result = self.company_change(Box::pin(async move { app.perform("saas_rename_tenant", json!({ "tenant": tenant, "name": name })).await })).await;
        if result.is_ok() {
            self.load_company().await;
        }
        result
    }

    /// Creates a company with a 14-day Business trial; this account becomes its owner.
    pub async fn create_company(self: &Rc<Self>, name: String, accepted_terms: bool) -> Result<(), String> {
        let name = name.trim().to_owned();
        let refusal = if !accepted_terms {
            Some(t("Accept the Terms of Service and the Data Processing Agreement to create a company."))
        } else if name.is_empty() {
            Some(t("Enter a company name"))
        } else {
            None
        };
        if let Some(error) = refusal {
            self.edit(|state| state.company.error = error.clone());
            return Err(error);
        }
        let app = self.clone();
        let result = self
            .company_change(Box::pin(async move {
                if !app.state.borrow().remote.disconnected() {
                    app.perform("remote_disconnect", json!({})).await?;
                }
                app.perform("saas_signup", json!({ "company": name, "terms": true })).await
            }))
            .await;
        if result.is_ok() {
            self.refresh_account().await;
            self.load_company().await;
        }
        result
    }

    pub async fn delete_company(self: &Rc<Self>, confirm_name: String) -> Result<(), String> {
        let tenant = self.tenant_id().ok_or("No company")?;
        let app = self.clone();
        let result = self.company_change(Box::pin(async move { app.perform("saas_delete_company", json!({ "tenant": tenant, "name": confirm_name })).await })).await;
        if result.is_ok() {
            self.refresh_account().await;
            self.load_company().await;
        }
        result
    }

    pub async fn delete_account(self: &Rc<Self>, confirm_email: String) -> Result<(), String> {
        let app = self.clone();
        let result = self.company_change(Box::pin(async move { app.perform("saas_delete_my_account", json!({ "email": confirm_email })).await })).await;
        if result.is_ok() {
            self.sign_out();
        }
        result
    }

    pub async fn create_personal_space(self: &Rc<Self>, name: String) -> Result<(), String> {
        let app = self.clone();
        let result = self
            .company_change(Box::pin(async move {
                if !app.state.borrow().remote.disconnected() {
                    app.perform("remote_disconnect", json!({})).await?;
                }
                app.perform("account_create_personal", json!({ "name": name })).await
            }))
            .await;
        if result.is_ok() {
            self.refresh_account().await;
        }
        result
    }

    pub fn export_company_data(self: &Rc<Self>) {
        self.act(|app| {
            Box::pin(async move {
                let url: String = app.run("legal_url", json!({ "page": "company-data", "lang": i18n::language() })).await?;
                app.open_uri(&url);
                Ok(())
            })
        });
    }

    // ---- Settings ----

    pub async fn load_settings(self: &Rc<Self>) {
        let startup = self.run::<StartupSettings>("startup_settings", json!({})).await.ok();
        let removal_pending = self.run::<bool>("device_removal_pending", json!({})).await.unwrap_or(false);
        let printing = self.run::<PrintingStatus>("remote_printing_status", json!({})).await.ok();
        let screen_access = self.run::<bool>("screen_access", json!({})).await.unwrap_or(false);
        let signin = self.run::<SignInScreen>("signin_screen_status", json!({})).await.ok();
        self.edit(|state| {
            state.settings.startup = startup;
            state.settings.removal_pending = removal_pending;
            state.settings.printing = printing;
            state.settings.screen_access = screen_access;
            state.settings.signin = signin;
        });
    }

    /// Sends a removal of this computer that was saved while offline.
    async fn flush_pending_removal(self: &Rc<Self>) {
        if let Ok(Some(status)) = self.run::<Option<String>>("device_self_remove_flush", json!({})).await
            && status != "queued"
        {
            self.forget_registration();
        }
        let pending = self.run::<bool>("device_removal_pending", json!({})).await.unwrap_or(false);
        self.edit(|state| state.settings.removal_pending = pending);
    }

    fn settings_change(self: &Rc<Self>, work: impl FnOnce(Rc<App>) -> Work) {
        self.edit(|state| {
            state.settings.error.clear();
            state.settings.notice.clear();
        });
        let app = self.clone();
        let future = work(app.clone());
        self.spawn(async move {
            if let Err(error) = future.await {
                app.edit(|state| state.settings.error = i18n::engine(&error));
            }
            app.load_settings().await;
        });
    }

    pub fn set_stay_signed_in(self: &Rc<Self>, enabled: bool) {
        self.settings_change(move |app| Box::pin(async move { app.perform("set_stay_signed_in", json!({ "enabled": enabled })).await }));
    }

    pub fn set_autostart(self: &Rc<Self>, enabled: bool) {
        self.settings_change(move |app| {
            Box::pin(async move {
                if enabled {
                    app.perform("set_stay_signed_in", json!({ "enabled": true })).await?;
                }
                app.perform("set_autostart", json!({ "enabled": enabled })).await
            })
        });
    }

    pub fn set_printing(self: &Rc<Self>, enabled: bool) {
        self.settings_change(move |app| Box::pin(async move { app.perform("remote_printing_set", json!({ "enabled": enabled })).await }));
    }

    pub fn set_language(self: &Rc<Self>, value: &str) {
        i18n::set_choice(value);
        {
            let mut store = self.store.borrow_mut();
            store.settings.language = value.to_owned();
            store.save();
        }
        // Every text is read when a screen is built: build them again.
        if let Some(ui) = self.ui.borrow().clone() {
            ui.rebuild(self);
        }
        self.changed();
    }

    pub fn check_for_update(self: &Rc<Self>) {
        self.settings_change(|app| {
            Box::pin(async move {
                let update: Option<UpdateInfo> = app.run("update_check", json!({})).await?;
                app.edit(|state| {
                    state.settings.update = update;
                    state.settings.update_checked = true;
                });
                Ok(())
            })
        });
    }

    /// Looks for an update now and then (start, every 6 hours); it is shown, never installed unasked.
    pub fn watch_updates(self: &Rc<Self>) {
        let app = self.clone();
        self.spawn(async move {
            loop {
                if let Ok(update) = app.run::<Option<UpdateInfo>>("update_check", json!({})).await {
                    app.edit(|state| {
                        state.settings.update = update;
                        state.settings.update_checked = true;
                    });
                }
                glib::timeout_future(Duration::from_secs(6 * 3600)).await;
            }
        });
    }

    /// Downloads, checks and installs the signed update (one administrator prompt), then the app
    /// starts again by itself.
    pub fn install_update(self: &Rc<Self>) {
        let version = self.state.borrow().settings.update.as_ref().map(|update| update.version.clone()).unwrap_or_default();
        self.edit(|state| state.settings.notice = tf("Installing Mixel Remote %@… The app restarts by itself.", &[&version]));
        let app = self.clone();
        self.spawn(async move {
            match app.perform("update_install", json!({})).await {
                Ok(()) => {
                    glib::timeout_future(Duration::from_millis(300)).await;
                    if let Some(ui) = app.ui.borrow().clone() {
                        ui.quit_now();
                    }
                }
                Err(error) => app.edit(|state| {
                    state.settings.notice.clear();
                    state.settings.error = tf("The update could not be installed: %@", &[&i18n::engine(&error)]);
                }),
            }
        });
    }

    pub fn open_legal(self: &Rc<Self>, page: &'static str) {
        self.settings_change(move |app| {
            Box::pin(async move {
                let url: String = app.run("legal_url", json!({ "page": page, "lang": i18n::language() })).await?;
                app.open_uri(&url);
                Ok(())
            })
        });
    }

    pub fn enable_signin_screen(self: &Rc<Self>, company: String, name: String) {
        self.settings_change(move |app| {
            Box::pin(async move {
                let id: u32 = app.run("signin_screen_enable", json!({ "company": company, "name": name })).await?;
                app.edit(|state| state.settings.notice = tf("Sign-in screen access is on (Remote ID %@).", &[&format::remote_id(Some(id))]));
                Ok(())
            })
        });
    }

    pub fn change_signin_screen(self: &Rc<Self>, update: bool) {
        self.settings_change(move |app| Box::pin(async move { app.perform(if update { "signin_screen_update" } else { "signin_screen_disable" }, json!({})).await }));
    }

    // ---- Setting up this computer ----

    async fn check_enrollment(self: &Rc<Self>) {
        if self.state.borrow().enrollment.name.is_empty() {
            let name: String = self.run("this_computer_name", json!({ "device_name": glib::host_name().to_string() })).await.unwrap_or_default();
            self.edit(|state| state.enrollment.name = name);
        }
        if self.state.borrow().staff()
            && let Ok(Some(enrolled)) = self.run::<Option<CurrentEnrollment>>("enrollment_current", json!({})).await
        {
            let generation = self.state.borrow().enrollment.current.as_ref().map(|current| current.generation).unwrap_or(0);
            self.remember_registration(Registration { device: enrolled.device, connection_id: enrolled.connection_id, generation, tenant: None });
        }
    }

    fn register_failed(self: &Rc<Self>, error: &str) {
        if logic::needs_setup(error) {
            self.edit(|state| state.enrollment.needs_setup = true);
        } else {
            let error = i18n::engine(error);
            self.edit(|state| state.error = error);
        }
    }

    fn remember_registration(self: &Rc<Self>, registration: Registration) {
        {
            let mut store = self.store.borrow_mut();
            store.settings.this_computer = Some(registration.clone());
            store.save();
        }
        self.edit(|state| {
            state.enrollment.current = Some(registration);
            state.enrollment.needs_setup = false;
        });
    }

    fn forget_registration(self: &Rc<Self>) {
        {
            let mut store = self.store.borrow_mut();
            store.settings.this_computer = None;
            store.save();
        }
        self.edit(|state| state.enrollment.current = None);
    }

    fn enrollment_run(self: &Rc<Self>, work: impl FnOnce(Rc<App>) -> Work) {
        if self.state.borrow().enrollment.working {
            return;
        }
        self.edit(|state| {
            state.enrollment.working = true;
            state.enrollment.error.clear();
        });
        let app = self.clone();
        let future = work(app.clone());
        self.spawn(async move {
            let result = future.await;
            app.edit(|state| {
                state.enrollment.working = false;
                if let Err(error) = result {
                    state.enrollment.error = i18n::engine(&error);
                }
            });
        });
    }

    pub fn set_setup_name(self: &Rc<Self>, name: &str) {
        self.state.borrow_mut().enrollment.name = name.to_owned();
    }

    pub fn add_to_company(self: &Rc<Self>, group: Option<String>) {
        let name = self.state.borrow().enrollment.name.trim().to_owned();
        if name.is_empty() || name.chars().count() > 60 {
            self.edit(|state| state.enrollment.error = t("Enter a computer name of up to 60 characters"));
            return;
        }
        self.enrollment_run(move |app| Box::pin(async move { app.add_this_computer(name, group).await }));
    }

    pub fn register_personal(self: &Rc<Self>) {
        let name = self.state.borrow().enrollment.name.trim().to_owned();
        if name.is_empty() {
            self.edit(|state| state.enrollment.error = t("Enter a device name"));
            return;
        }
        self.enrollment_run(move |app| {
            Box::pin(async move {
                if !app.state.borrow().remote.disconnected() {
                    app.perform("remote_disconnect", json!({})).await?;
                }
                let id: u32 = app.run("register_personal_device", json!({ "name": name })).await?;
                app.remember_registration(Registration { device: String::new(), connection_id: id, generation: 0, tenant: None });
                app.go_online();
                Ok(())
            })
        });
    }

    pub fn load_team_records(self: &Rc<Self>, search: String) {
        self.enrollment_run(move |app| {
            Box::pin(async move {
                let companies: Vec<EnrollmentCompany> = app.run("enrollment_companies", json!({})).await?;
                let page: InventoryPage = app.run("enrollment_devices", json!({ "after": null, "search": search })).await?;
                app.edit(|state| {
                    state.enrollment.companies = companies;
                    state.enrollment.records = page.devices;
                });
                Ok(())
            })
        });
    }

    /// Mixel team: enrol this computer against an existing record.
    pub fn enroll_record(self: &Rc<Self>, record: InventoryDevice) {
        self.enrollment_run(move |app| {
            Box::pin(async move {
                if !app.state.borrow().remote.disconnected() {
                    app.perform("remote_disconnect", json!({})).await?;
                }
                let id: u32 = app.run("enroll_this_device", json!({ "device": record.id, "confirmed": true })).await?;
                app.remember_registration(Registration { device: record.id, connection_id: id, generation: 0, tenant: None });
                app.go_online();
                Ok(())
            })
        });
    }

    /// Mixel team: add a new record for this computer in a company, then enrol it.
    pub fn add_and_enroll(self: &Rc<Self>, company: String) {
        let name = self.state.borrow().enrollment.name.trim().to_owned();
        if name.is_empty() {
            self.edit(|state| state.enrollment.error = t("Enter a device name"));
            return;
        }
        self.enrollment_run(move |app| {
            Box::pin(async move {
                if !app.state.borrow().remote.disconnected() {
                    app.perform("remote_disconnect", json!({})).await?;
                }
                let device: String = app.run("add_this_device", json!({ "company": company, "name": name })).await?;
                let id: u32 = app.run("enroll_this_device", json!({ "device": device, "confirmed": true })).await?;
                app.remember_registration(Registration { device, connection_id: id, generation: 0, tenant: None });
                app.go_online();
                Ok(())
            })
        });
    }

    /// Removes this computer from Mixel Remote (signed with its own key; queued while offline).
    pub async fn remove_this_computer(self: &Rc<Self>) -> Option<String> {
        self.edit(|state| {
            state.enrollment.error.clear();
            state.enrollment.working = true;
            state.went_offline = true;
        });
        let result: Result<String, String> = async {
            if !self.state.borrow().remote.disconnected() {
                self.perform("remote_disconnect", json!({})).await?;
            }
            let saved = self
                .state
                .borrow()
                .enrollment
                .current
                .clone()
                .filter(|current| !current.device.is_empty() && current.generation > 0)
                .map(|current| json!({ "device": current.device, "connection_id": current.connection_id, "generation": current.generation }));
            self.run("device_self_remove", json!({ "registration": saved, "confirmed": true })).await
        }
        .await;
        self.edit(|state| {
            state.enrollment.working = false;
            state.went_offline = false;
        });
        match result {
            Ok(status) => {
                if status != "stale" {
                    self.forget_registration();
                    self.edit(|state| state.enrollment.needs_setup = true);
                }
                self.load_devices(0).await;
                self.load_settings().await;
                Some(status)
            }
            Err(error) => {
                self.edit(|state| state.enrollment.error = i18n::engine(&error));
                None
            }
        }
    }

    // ---- This computer as host ----

    fn host_status_changed(self: &Rc<Self>, previous: &RemoteStatus, next: &RemoteStatus) {
        if next.phase == "incoming" && previous.phase != "incoming" && !next.controller_only {
            self.edit(|state| {
                let host = &mut state.host;
                host.allow_chat = false;
                host.allow_recording = false;
                host.allow_files = false;
                host.allow_clipboard = false;
                host.error.clear();
            });
            let app = self.clone();
            self.spawn(async move { app.load_sources().await });
        }
        if next.phase == "ready" && previous.phase != "ready" {
            let app = self.clone();
            self.spawn(async move { app.restore_unattended().await });
        }
    }

    pub async fn load_sources(self: &Rc<Self>) {
        let access = self.run::<bool>("screen_access", json!({})).await.unwrap_or(true);
        match self.run::<Vec<CaptureTarget>>("capture_targets", json!({})).await {
            Ok(targets) => self.edit(|state| {
                let host = &mut state.host;
                host.screen_access = access;
                host.sources = targets.into_iter().filter(|target| target.full_display).collect();
                if !host.sources.iter().any(|source| source.id == host.source) {
                    host.source = host.sources.first().map(|source| source.id.clone()).unwrap_or_default();
                }
                // One screen: it is the one to share. Keyboard and mouse are pre-selected when they can
                // be allowed; the person still decides with Allow.
                host.confirm_display = host.sources.len() == 1;
                host.allow_control = host.confirm_display && state.remote.input_access;
            }),
            Err(error) => self.edit(|state| {
                state.host.screen_access = access;
                state.host.error = i18n::engine(&error);
            }),
        }
    }

    pub fn host_edit(self: &Rc<Self>, change: impl FnOnce(&mut HostState)) {
        if self.rendering.get() {
            return;
        }
        change(&mut self.state.borrow_mut().host);
        self.changed();
    }

    pub fn decide(self: &Rc<Self>, accept: bool) {
        let decision = {
            let state = self.state.borrow();
            let Some(session) = state.remote.session.clone() else { return };
            let host = &state.host;
            Decision {
                session,
                accept,
                source: host.source.clone(),
                confirm: host.confirm_display,
                control: accept && host.allow_control && state.control_available(),
                clipboard: accept && host.allow_clipboard,
                files: accept && host.allow_files,
                chat: accept && host.allow_chat,
                recording: accept && host.allow_recording,
            }
        };
        self.act(move |app| Box::pin(async move { app.perform("remote_decide", json!({ "decision": decision })).await }));
    }

    pub fn decide_join(self: &Rc<Self>, accept: bool, control: bool) {
        let Some(request) = self.state.borrow().remote.join_request.clone() else { return };
        self.act(move |app| Box::pin(async move { app.perform("remote_decide_join", json!({ "session": request.session, "accept": accept, "control": control })).await }));
    }

    pub fn remove_colleague(self: &Rc<Self>, colleague: Colleague) {
        self.act(move |app| Box::pin(async move { app.perform("remote_remove_colleague", json!({ "session": colleague.session })).await }));
    }

    pub fn open_meeting_offer(self: &Rc<Self>) {
        let room = {
            let state = self.state.borrow();
            logic::meeting_offer(&state.remote, &state.session.dismissed_meeting_room)
        };
        let Some(room) = room else { return };
        self.edit(|state| state.session.dismissed_meeting_room = room.clone());
        self.act(move |app| {
            Box::pin(async move {
                let link: String = app.run("meeting_link", json!({ "link": format!("https://rs.mixel.ch/meet/#{room}") })).await?;
                app.open_uri(&link);
                Ok(())
            })
        });
    }

    pub fn dismiss_meeting_offer(self: &Rc<Self>) {
        let room = self.state.borrow().remote.meeting_offer.as_ref().map(|(_, room)| room.clone()).unwrap_or_default();
        self.edit(|state| state.session.dismissed_meeting_room = room);
    }

    pub fn load_unattended_sources(self: &Rc<Self>) {
        self.act(|app| {
            Box::pin(async move {
                let targets: Vec<CaptureTarget> = app.run("capture_targets", json!({})).await?;
                let targets: Vec<CaptureTarget> = targets.into_iter().filter(|target| target.full_display).collect();
                let empty = targets.is_empty();
                app.edit(|state| {
                    state.host.unattended_source = targets.first().map(|target| target.id.clone()).unwrap_or_default();
                    state.host.unattended_sources = targets;
                    state.host.unattended_confirm = false;
                });
                if empty { Err("An available display is required before enabling unattended sharing.".into()) } else { Ok(()) }
            })
        });
    }

    pub fn enable_unattended(self: &Rc<Self>) {
        let (source, control) = {
            let state = self.state.borrow();
            if state.host.unattended_source.is_empty() || !state.host.unattended_confirm {
                drop(state);
                self.edit(|state| state.host.error = t("Choose a display and confirm it before enabling unattended sharing."));
                return;
            }
            (state.host.unattended_source.clone(), state.host.unattended_control && state.remote.input_access)
        };
        self.edit(|state| state.host.unattended_confirm = false);
        {
            let mut store = self.store.borrow_mut();
            store.settings.unattended = Some(Unattended { source: source.clone(), control });
            store.save();
        }
        self.act(move |app| Box::pin(async move { app.perform("remote_unattended", json!({ "source": source, "control": control })).await }));
    }

    pub fn disable_unattended(self: &Rc<Self>) {
        {
            let mut store = self.store.borrow_mut();
            store.settings.unattended = None;
            store.save();
        }
        self.act(|app| Box::pin(async move { app.perform("remote_unattended", json!({ "source": null, "control": false })).await }));
    }

    /// After a start with a remembered sign-in, unattended sharing comes back as the owner left it.
    async fn restore_unattended(self: &Rc<Self>) {
        let saved = self.store.borrow().settings.unattended.clone();
        let skip = {
            let state = self.state.borrow();
            state.host.restored || state.remote.unattended_enabled || state.remote.unattended_pending.is_some()
        };
        let Some(saved) = saved.filter(|_| !skip) else { return };
        let Ok(startup) = self.run::<StartupSettings>("startup_settings", json!({})).await else { return };
        if !(startup.restored || startup.launched_at_startup) {
            return;
        }
        let Ok(targets) = self.run::<Vec<CaptureTarget>>("capture_targets", json!({})).await else { return };
        if !targets.iter().any(|target| target.id == saved.source && target.full_display) {
            return;
        }
        self.state.borrow_mut().host.restored = true;
        let _ = self.perform("remote_unattended", json!({ "source": saved.source, "control": saved.control })).await;
    }

    pub fn one_time_password(self: &Rc<Self>, required: bool) {
        self.act(move |app| Box::pin(async move { app.perform("remote_support_code", json!({ "required": required })).await }));
    }

    pub fn check_pairing(self: &Rc<Self>) {
        self.act(|app| Box::pin(async move { app.perform("remote_pairing", json!({ "confirmation": null })).await }));
    }

    pub fn confirm_pairing(self: &Rc<Self>) {
        let Some(pairing) = self.state.borrow().remote.pairing.clone() else { return };
        self.act(move |app| Box::pin(async move { app.perform("remote_pairing", json!({ "confirmation": pairing.request_id })).await }));
    }

    pub fn copy_id(self: &Rc<Self>) {
        let host = self.state.borrow().remote.host;
        if let (Some(host), Some(shell)) = (host, self.shell()) {
            shell.copy(&host.to_string());
            shell.notify(&t("Connection ID copied"));
        }
    }

    // ---- The session (technician side) ----

    fn session_status_changed(self: &Rc<Self>, previous: &RemoteStatus, next: &RemoteStatus) {
        let _ = previous;
        {
            let mut state = self.state.borrow_mut();
            if next.session != state.session.toggle_session {
                let session = &mut state.session;
                session.toggle_session = next.session.clone();
                session.privacy = false;
                session.block_input = false;
                session.lock_when_done = false;
                session.pointer_mode = false;
                session.sound = true;
                session.chat.clear();
                session.files = None;
                session.clipboard_result.clear();
                session.invite_link = None;
            }
            if next.viewing() && state.session.assistant.session != next.session {
                let assistant = &mut state.session.assistant;
                assistant.session = next.session.clone();
                assistant.started = clock();
                assistant.log.clear();
                assistant.answer.clear();
                assistant.ticket_status.clear();
            }
            if !next.in_session() {
                state.session.stopped_consent = None;
            }
        }
        let (start_video, stop_video) = {
            let state = self.state.borrow();
            let viewing = next.viewing() && !state.session.stopping && !state.locally_stopped();
            let start = viewing && next.session != state.session.viewing_session;
            let stop = !viewing && state.session.viewing_session.is_some();
            (start.then(|| next.session.clone()).flatten(), stop)
        };
        if let Some(session) = start_video {
            self.start_video(session);
        } else if stop_video {
            self.clear_video();
        }
        let tools = next.phase == "active" || matches!(next.phase.as_str(), "incoming" | "outgoing");
        if tools && !self.state.borrow().session.tools_running {
            self.start_tools();
        } else if !tools {
            self.state.borrow_mut().session.tools_running = false;
        }
        self.continue_reconnect(next);
        if next.phase == "active" {
            let progress_empty = self.state.borrow().progress.is_empty();
            if !progress_empty {
                self.edit(|state| state.progress.clear());
            }
        }
    }

    /// Stops the picture and forgets it.
    pub fn clear_video(self: &Rc<Self>) {
        self.video_generation.set(self.video_generation.get() + 1);
        self.texture.replace(None);
        self.queue.borrow_mut().clear();
        self.shown.borrow_mut().clear();
        self.edit(|state| {
            state.session.viewing_session = None;
            state.session.has_frame = false;
            state.session.size = (0, 0);
            state.session.fps = 0;
        });
        if let Some(ui) = self.ui.borrow().clone() {
            ui.set_frame(None);
        }
    }

    fn start_video(self: &Rc<Self>, session: String) {
        self.clear_video();
        self.edit(|state| state.session.viewing_session = Some(session.clone()));
        let generation = self.video_generation.get();
        let app = self.clone();
        self.spawn(async move {
            let mut sequence = 0;
            loop {
                if app.video_generation.get() != generation {
                    return;
                }
                let data = app.engine.next_video(&session).await;
                if app.video_generation.get() != generation {
                    return;
                }
                match data {
                    Ok(data) if data.is_empty() => glib::timeout_future(Duration::from_millis(16)).await,
                    Ok(data) => match video::decode(data, sequence) {
                        Ok(frame) => {
                            sequence = frame.sequence;
                            app.show_frame(frame);
                        }
                        Err(video::Problem::Compressed) => {}
                        Err(video::Problem::Invalid) => {
                            app.edit(|state| state.session.error = t("Invalid native video frame"));
                            let _ = app.perform("remote_close", json!({ "session": session })).await;
                            app.clear_video();
                            return;
                        }
                    },
                    Err(error) => {
                        app.edit(|state| state.session.error = i18n::engine(&error));
                        let _ = app.perform("remote_close", json!({ "session": session })).await;
                        app.clear_video();
                        return;
                    }
                }
            }
        });
    }

    pub fn show_frame(self: &Rc<Self>, frame: video::Frame) {
        let format = match frame.layout {
            video::Layout::Rgba => gdk::MemoryFormat::R8g8b8a8,
            video::Layout::Bgra => gdk::MemoryFormat::B8g8r8a8,
        };
        let bytes = glib::Bytes::from_owned(frame.pixels);
        let texture: gdk::Texture = gdk::MemoryTexture::new(frame.width as i32, frame.height as i32, format, &bytes, frame.stride).upcast();
        self.texture.replace(Some(texture.clone()));
        if let Some(ui) = self.ui.borrow().clone() {
            ui.set_frame(Some(&texture));
        }
        let now = clock();
        let fps = {
            let mut shown = self.shown.borrow_mut();
            shown.push(now);
            shown.retain(|time| now - time <= 1.0);
            shown.len()
        };
        let changed = {
            let state = self.state.borrow();
            state.session.fps != fps || state.session.size != (frame.width, frame.height) || !state.session.has_frame
        };
        if changed {
            self.edit(|state| {
                state.session.fps = fps;
                state.session.size = (frame.width, frame.height);
                state.session.has_frame = true;
            });
        }
    }

    /// Queues an input event and sends it; pointer moves replace the previous unsent move.
    pub fn send_input(self: &Rc<Self>, event: Value) {
        let (session, controllable) = {
            let state = self.state.borrow();
            (state.remote.session.clone(), state.controllable())
        };
        let Some(session) = session else { return };
        if event["kind"] != "release_all" && !controllable {
            return;
        }
        if self.queue.borrow_mut().push(event) == logic::Queued::Stalled {
            self.edit(|state| state.session.error = t("Input delivery stalled; the session has been stopped"));
            let app = self.clone();
            self.spawn(async move {
                let _ = app.perform("remote_close", json!({ "session": session })).await;
            });
            return;
        }
        if self.sending.replace(true) {
            return;
        }
        let app = self.clone();
        self.spawn(async move {
            loop {
                let Some(event) = app.queue.borrow_mut().pop() else { break };
                if app.state.borrow().remote.session.as_deref() != Some(session.as_str()) {
                    app.queue.borrow_mut().clear();
                    break;
                }
                let text = event["kind"] == "text";
                match app.engine.input(&session, event).await {
                    Ok(false) if text => app.edit(|state| state.session.error = t("The remote computer could not type this text.")),
                    Ok(_) => {}
                    Err(error) => {
                        app.queue.borrow_mut().clear();
                        app.edit(|state| state.session.error = i18n::engine(&error));
                        break;
                    }
                }
            }
            app.sending.set(false);
        });
    }

    /// A pointer position in the picture (0…1).
    pub fn move_pointer(self: &Rc<Self>, x: f64, y: f64) {
        let (x, y) = (logic::normalized(x), logic::normalized(y));
        if self.state.borrow().session.pointer_mode {
            self.session_action(&format!("pointer:{x},{y}"), true);
        } else {
            self.send_input(input::pointer(x, y));
        }
    }

    pub fn type_text(self: &Rc<Self>, text: &str) {
        if !text.is_empty() {
            self.send_input(input::text(text));
        }
    }

    /// Presses and releases a key, with modifiers held around it (Ctrl+C and similar).
    pub fn press(self: &Rc<Self>, usage: u16, modifiers: &[u16]) {
        for modifier in modifiers {
            self.send_input(input::key(*modifier, true));
        }
        self.send_input(input::key(usage, true));
        self.send_input(input::key(usage, false));
        for modifier in modifiers.iter().rev() {
            self.send_input(input::key(*modifier, false));
        }
    }

    pub fn session_action(self: &Rc<Self>, name: &str, quiet: bool) {
        let Some(session) = self.state.borrow().remote.session.clone() else { return };
        let app = self.clone();
        let name = name.to_owned();
        self.spawn(async move {
            if let Err(error) = app.engine.input(&session, input::action(&name)).await
                && !quiet
            {
                app.edit(|state| state.session.error = i18n::engine(&error));
            }
        });
    }

    pub fn toggle_privacy(self: &Rc<Self>) {
        let on = !self.state.borrow().session.privacy;
        self.edit(|state| state.session.privacy = on);
        self.session_action(if on { "privacy:on" } else { "privacy:off" }, false);
    }
    pub fn toggle_block_input(self: &Rc<Self>) {
        let on = !self.state.borrow().session.block_input;
        self.edit(|state| state.session.block_input = on);
        self.session_action(if on { "block:on" } else { "block:off" }, false);
    }
    pub fn toggle_lock_when_done(self: &Rc<Self>) {
        let on = !self.state.borrow().session.lock_when_done;
        self.edit(|state| state.session.lock_when_done = on);
        self.session_action(if on { "lockend:on" } else { "lockend:off" }, false);
    }
    pub fn toggle_pointer(self: &Rc<Self>) {
        let on = !self.state.borrow().session.pointer_mode;
        self.edit(|state| state.session.pointer_mode = on);
        if !on {
            self.session_action("pointer:off", true);
        }
    }
    pub fn toggle_sound(self: &Rc<Self>) {
        let on = !self.state.borrow().session.sound;
        self.edit(|state| state.session.sound = on);
        self.session_action(if on { "sound:on" } else { "sound:off" }, true);
    }
    pub fn lock_now(self: &Rc<Self>) {
        self.session_action("lock", false);
    }
    pub fn ctrl_alt_del(self: &Rc<Self>) {
        self.session_action("sas", false);
    }
    pub fn select_display(self: &Rc<Self>, index: u16) {
        let Some(session) = self.state.borrow().remote.session.clone() else { return };
        let app = self.clone();
        self.spawn(async move {
            let _ = app.engine.input(&session, input::select_display(index)).await;
        });
    }

    /// The first click arms, the second (within 5 s) restarts the remote computer; the app reconnects
    /// when it is back (up to 10 minutes).
    pub fn restart_computer(self: &Rc<Self>) {
        let (session, peer, signin, unattended, armed) = {
            let state = self.state.borrow();
            (state.remote.session.clone(), state.remote.peer, state.remote.signin_id, state.session.last_request_unattended || state.remote.unattended_session, state.session.restart_armed)
        };
        let (Some(session), Some(peer)) = (session, peer) else { return };
        if !armed {
            self.edit(|state| state.session.restart_armed = true);
            let app = self.clone();
            self.spawn(async move {
                glib::timeout_future(Duration::from_secs(5)).await;
                app.edit(|state| state.session.restart_armed = false);
            });
            return;
        }
        self.edit(|state| {
            state.session.restart_armed = false;
            state.session.reconnect = Some(Reconnect::new(peer, signin, unattended, session, clock()));
        });
        self.session_action("restart", false);
    }

    fn continue_reconnect(self: &Rc<Self>, status: &RemoteStatus) {
        let busy = self.state.borrow().busy;
        let steps = {
            let mut state = self.state.borrow_mut();
            let Some(plan) = state.session.reconnect.as_mut() else { return };
            plan.step(status, busy, clock())
        };
        for step in steps {
            match step {
                ReconnectStep::Wait => {
                    if status.phase != "active" {
                        let notice = t("The remote computer is restarting. Mixel Remote reconnects when it is back…");
                        if self.state.borrow().session.notice != notice {
                            self.edit(|state| state.session.notice = notice);
                        }
                    }
                }
                ReconnectStep::Done => self.edit(|state| {
                    state.session.reconnect = None;
                    state.session.notice.clear();
                }),
                ReconnectStep::GaveUp => self.edit(|state| {
                    state.session.reconnect = None;
                    state.session.notice.clear();
                    state.error = t("The remote computer did not come back within 10 minutes.");
                }),
                ReconnectStep::Presence(hosts) => {
                    let app = self.clone();
                    self.spawn(async move {
                        let _ = app.perform("remote_presence", json!({ "hosts": hosts })).await;
                    });
                    self.edit(|state| state.session.notice = t("The remote computer is restarting. Mixel Remote reconnects when it is back…"));
                }
                ReconnectStep::Request { host, unattended } => {
                    self.act(move |app| Box::pin(async move { app.perform("remote_request", logic::request_args(host, None, unattended)).await }));
                }
            }
        }
    }

    /// Renew the connection (new keys, same session).
    pub fn renew(self: &Rc<Self>) {
        let Some(session) = self.state.borrow().remote.session.clone() else { return };
        self.act(move |app| Box::pin(async move { app.perform("remote_restart", json!({ "session": session })).await }));
    }

    /// Ends the session for both sides.
    pub fn end_session(self: &Rc<Self>) {
        let (consent, stopping) = {
            let state = self.state.borrow();
            (state.remote.consent.clone(), state.session.stopping)
        };
        let Some(consent) = consent else { return };
        if stopping {
            return;
        }
        self.edit(|state| {
            state.session.stopped_consent = Some(consent.clone());
            state.session.stopping = true;
            state.session.error.clear();
        });
        self.clear_video();
        let app = self.clone();
        self.spawn(async move {
            let result = app.perform("remote_stop_session", json!({ "consent": consent })).await;
            app.edit(|state| {
                state.session.stopping = false;
                if let Err(error) = result {
                    state.session.error = format!("{} {}", t("Could not confirm that the session ended. Try End session again."), i18n::engine(&error));
                }
            });
            app.refresh_remote().await;
        });
    }

    pub fn clipboard(self: &Rc<Self>, push: bool) {
        let Some(session) = self.state.borrow().remote.session.clone() else { return };
        self.edit(|state| state.session.clipboard_result = t("Transferring…"));
        let app = self.clone();
        self.spawn(async move {
            let result = app.perform("remote_clipboard", json!({ "session": session, "push": push })).await;
            app.edit(|state| {
                state.session.clipboard_result = match result {
                    Ok(()) if push => t("Remote clipboard updated."),
                    Ok(()) => t("Your clipboard was updated."),
                    Err(error) => i18n::engine(&error),
                };
            });
        });
    }

    fn start_tools(self: &Rc<Self>) {
        self.state.borrow_mut().session.tools_running = true;
        let app = self.clone();
        self.spawn(async move {
            while app.state.borrow().session.tools_running {
                app.read_tools().await;
                glib::timeout_future(Duration::from_millis(700)).await;
            }
        });
    }

    async fn read_tools(self: &Rc<Self>) {
        let status = self.state.borrow().remote.clone();
        if let Some(session) = status.session.clone()
            && status.phase == "active"
            && let Ok(Some(messages)) = self.run::<Option<Vec<ChatMessage>>>("remote_chat_messages", json!({ "session": session })).await
            && messages != self.state.borrow().session.chat
        {
            self.edit(|state| state.session.chat = messages);
        }
        if let Some(consent) = status.consent.clone()
            && status.files
            && status.phase == "active"
        {
            let view = self.run::<FileView>("remote_file_status", json!({ "consent": consent })).await.ok();
            if view != self.state.borrow().session.files {
                self.edit(|state| state.session.files = view);
            }
        } else if self.state.borrow().session.files.is_some() {
            self.edit(|state| state.session.files = None);
        }
    }

    pub fn send_chat(self: &Rc<Self>, text: &str) {
        let text = text.trim().to_owned();
        let Some(session) = self.state.borrow().remote.session.clone() else { return };
        if text.is_empty() {
            return;
        }
        let app = self.clone();
        self.spawn(async move {
            match app.perform("remote_chat_send", json!({ "session": session, "text": text })).await {
                Ok(()) => app.read_tools().await,
                Err(error) => app.edit(|state| state.session.error = i18n::engine(&error)),
            }
        });
    }

    pub fn send_file(self: &Rc<Self>) {
        let Some(consent) = self.state.borrow().remote.consent.clone() else { return };
        let Some(shell) = self.shell() else { return };
        let app = self.clone();
        self.spawn(async move {
            let Some(path) = shell.choose_file().await else { return };
            match app.perform("remote_file_send", json!({ "consent": consent, "path": path })).await {
                Ok(()) => app.read_tools().await,
                Err(error) => app.edit(|state| state.session.error = i18n::engine(&error)),
            }
        });
    }

    pub fn accept_file(self: &Rc<Self>, transfer: Transfer) {
        let Some(consent) = self.state.borrow().remote.consent.clone() else { return };
        let Some(shell) = self.shell() else { return };
        let app = self.clone();
        self.spawn(async move {
            let name: String = app.run("remote_file_pending", json!({ "consent": consent, "id": transfer.id })).await.unwrap_or(transfer.name.clone());
            let Some(path) = shell.choose_save(&name).await else { return };
            match app.perform("remote_file_accept", json!({ "consent": consent, "id": transfer.id, "path": path })).await {
                Ok(()) => app.read_tools().await,
                Err(error) => app.edit(|state| state.session.error = i18n::engine(&error)),
            }
        });
    }

    pub fn cancel_file(self: &Rc<Self>, id: String) {
        let Some(consent) = self.state.borrow().remote.consent.clone() else { return };
        let app = self.clone();
        self.spawn(async move {
            let _ = app.perform("remote_file_cancel", json!({ "consent": consent, "id": id })).await;
            app.read_tools().await;
        });
    }

    pub fn toggle_recording(self: &Rc<Self>) {
        let (session, running) = {
            let state = self.state.borrow();
            (state.remote.session.clone(), state.remote.recording.as_ref().is_some_and(RecordingView::running))
        };
        let Some(session) = session else { return };
        let app = self.clone();
        self.spawn(async move {
            app.edit(|state| state.session.recording_pending = true);
            let result = if running {
                app.perform("remote_record_stop", json!({ "session": session })).await
            } else {
                match app.run::<bool>("remote_record_may_start", json!({ "session": session })).await {
                    Ok(true) => match app.shell() {
                        Some(shell) => match shell.choose_save("Mixel Remote recording.mp4").await {
                            Some(path) => app.perform("remote_record_start", json!({ "session": session, "path": path })).await,
                            None => Ok(()),
                        },
                        None => Ok(()),
                    },
                    _ => Err("Start a permitted recording after the remote screen appears".into()),
                }
            };
            app.edit(|state| {
                state.session.recording_pending = false;
                if let Err(error) = result {
                    state.session.error = i18n::engine(&error);
                }
            });
            app.refresh_remote().await;
        });
    }

    pub fn invite_colleague(self: &Rc<Self>, device: u32, control: bool) {
        let Some(session) = self.state.borrow().remote.session.clone() else { return };
        self.act(move |app| Box::pin(async move { app.perform("remote_invite", json!({ "session": session, "user": null, "device": device, "control": control })).await }));
    }

    pub fn join_invitation(self: &Rc<Self>, invitation: Invitation) {
        self.act(move |app| Box::pin(async move { app.perform("remote_join", json!({ "invitation": invitation.invitation })).await }));
    }

    pub fn dismiss_invitation(self: &Rc<Self>, invitation: Invitation) {
        self.act(move |app| Box::pin(async move { app.perform("remote_dismiss_invitation", json!({ "invitation": invitation.invitation })).await }));
    }

    pub fn switch_sides(self: &Rc<Self>) {
        let Some(session) = self.state.borrow().remote.session.clone() else { return };
        self.act(move |app| Box::pin(async move { app.perform("remote_switch_sides", json!({ "session": session })).await }));
    }

    /// Show my screen (older computers without switching sides): a Mixel Meet room the other side is
    /// asked to watch.
    pub fn show_my_screen(self: &Rc<Self>) {
        let Some(session) = self.state.borrow().remote.session.clone() else { return };
        self.act(move |app| {
            Box::pin(async move {
                let room: MeetingRoom = app.run("meeting_create", json!({})).await?;
                app.open_uri(&room.host);
                app.engine.input(&session, input::action(&format!("meet:{}", room.room))).await?;
                app.edit(|state| state.session.meeting_notice = t("Your meeting opens in the browser. Choose “Share screen” there; the other person is asked to watch."));
                Ok(())
            })
        });
    }

    /// A meeting link for a colleague (when the other computer cannot take a second technician).
    pub fn meeting_link(self: &Rc<Self>) {
        let app = self.clone();
        self.spawn(async move {
            match app.run::<MeetingRoom>("meeting_create", json!({})).await {
                Ok(room) => {
                    app.open_uri(&room.host);
                    app.edit(|state| state.session.invite_link = Some(room));
                }
                Err(error) => app.edit(|state| state.session.error = i18n::engine(&error)),
            }
        });
    }

    pub fn print_job(self: &Rc<Self>, path: String, open: bool) {
        self.act(move |app| Box::pin(async move { app.perform("print_job", json!({ "path": path, "open": open })).await }));
    }

    /// Finds or checks the company's AI server (empty address: this computer and the usual server).
    pub fn ai_connect(self: &Rc<Self>, address: String, model: String, announce: bool) {
        if announce {
            self.edit(|state| state.session.assistant.status = t("Connecting…"));
        }
        let app = self.clone();
        self.spawn(async move {
            #[derive(serde::Deserialize)]
            struct Server {
                address: String,
                models: Vec<String>,
                model: String,
            }
            match app.run::<Server>("ai_connect", json!({ "address": address, "model": model })).await {
                Ok(server) => {
                    {
                        let mut store = app.store.borrow_mut();
                        store.settings.ai = Some(crate::store::AiServer { address: server.address.clone(), model: server.model.clone() });
                        store.save();
                    }
                    app.edit(|state| {
                        let assistant = &mut state.session.assistant;
                        assistant.connected = true;
                        assistant.address = server.address;
                        assistant.models = server.models;
                        assistant.model = server.model;
                        if announce {
                            assistant.status = t("Connected to your AI server.");
                        }
                    });
                }
                Err(error) => app.edit(|state| {
                    state.session.assistant.connected = false;
                    if announce {
                        state.session.assistant.status = i18n::engine(&error);
                    }
                }),
            }
        });
    }

    /// Asks about the screen the technician sees (`note`: writes the ticket note of the session).
    pub fn ai_ask(self: &Rc<Self>, typed: String, note: bool) {
        let (address, model, peer, log, started, busy) = {
            let state = self.state.borrow();
            let assistant = &state.session.assistant;
            (assistant.address.clone(), assistant.model.clone(), state.remote.peer, assistant.log.clone(), assistant.started, assistant.busy)
        };
        if busy || address.is_empty() {
            return;
        }
        let language = match i18n::language() {
            "de" => "German",
            "fr" => "French",
            "it" => "Italian",
            _ => "English",
        };
        let image = self.texture.borrow().as_ref().map(|texture| {
            use base64_encode as encode;
            encode(&texture.save_to_png_bytes())
        });
        let (system, prompt) = if note {
            let minutes = ((clock() - started) / 60.0).round().max(1.0) as u64;
            (
                format!("You write short, factual service-desk notes for an IT provider. Use only the facts given and what the final screenshot shows; never invent work. Format: one summary line, then three short sections for what was done, what is still open, and the time spent, with the section labels in the answer language. Write \"–\" for an empty section. No Markdown. Answer in {language}."),
                format!(
                    "Remote support session with computer ID {}, about {minutes} minutes so far.\nTechnician's keywords: {}\nAssistant exchanges during the session:\n{}\nWrite the ticket note.",
                    peer.map(|peer| peer.to_string()).unwrap_or_else(|| "unknown".into()),
                    if typed.is_empty() { "(none)" } else { &typed },
                    if log.is_empty() { "(none)".to_owned() } else { log.join("\n---\n") }
                ),
            )
        } else {
            (
                format!("You are an IT support assistant helping a technician during a remote support session. The image is the remote computer's screen (a Mixel Remote window on it only means the session is running; ignore it). Be concrete and brief. No Markdown. Quote on-screen text exactly when you rely on it; say so if you cannot read something. Answer in {language}."),
                if typed.is_empty() { "Explain briefly what is shown on this screen, name any error or warning you can read, and suggest the next two or three steps for the technician.".to_owned() } else { typed.clone() },
            )
        };
        self.edit(|state| {
            state.session.assistant.busy = true;
            state.session.assistant.answer = if note { t("Writing the note…") } else { t("Looking at the screen…") };
        });
        let app = self.clone();
        self.spawn(async move {
            let result = app.run::<String>("ai_ask", json!({ "address": address, "model": model, "system": system, "prompt": prompt, "image": image })).await;
            app.edit(|state| {
                let assistant = &mut state.session.assistant;
                assistant.busy = false;
                match result {
                    Ok(answer) => {
                        let answer = plain(&answer);
                        if !note {
                            assistant.log.push(format!("Question: {}\nAssistant: {answer}", if typed.is_empty() { "(explain the screen)" } else { &typed }));
                            if assistant.log.len() > 12 {
                                assistant.log.remove(0);
                            }
                        }
                        assistant.answer = answer;
                    }
                    Err(error) => assistant.answer = i18n::engine(&error),
                }
            });
        });
    }

    /// Mixel team: the note becomes an internal note on an ISM ticket.
    pub fn ticket_note(self: &Rc<Self>, ticket: String, note: String) {
        let ticket = ticket.trim().to_owned();
        let note = note.trim().chars().take(7800).collect::<String>();
        if ticket.len() < 3 {
            self.edit(|state| state.session.assistant.ticket_status = t("Enter the ticket number."));
            return;
        }
        if note.is_empty() {
            self.edit(|state| state.session.assistant.ticket_status = t("Write the note first, or let the AI write it."));
            return;
        }
        self.edit(|state| state.session.assistant.ticket_status = t("Saving…"));
        let peer = self.state.borrow().remote.peer;
        let app = self.clone();
        self.spawn(async move {
            let result = app.run::<String>("ticket_note", json!({ "ticket": ticket, "note": note, "remote_id": peer })).await;
            app.edit(|state| {
                state.session.assistant.ticket_status = match result {
                    Ok(saved) => tf("Saved as internal note in %@", &[&saved]),
                    Err(error) => i18n::engine(&error),
                };
            });
        });
    }

    pub fn set_ai_answer(self: &Rc<Self>, text: String) {
        self.state.borrow_mut().session.assistant.answer = text;
    }

    pub fn set_tool(self: &Rc<Self>, tool: Option<Tool>) {
        self.edit(|state| state.session.tool = if state.session.tool == tool { None } else { tool });
    }
}

/// Plain text for display: no Markdown emphasis or heading marks.
fn plain(answer: &str) -> String {
    answer
        .lines()
        .map(|line| {
            let line = line.trim_start_matches('#').trim_start();
            let line = if let Some(rest) = line.strip_prefix("* ") { format!("• {rest}") } else { line.to_owned() };
            line.replace("**", "")
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn base64_encode(bytes: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let value = (u32::from(chunk[0]) << 16) | (u32::from(*chunk.get(1).unwrap_or(&0)) << 8) | u32::from(*chunk.get(2).unwrap_or(&0));
        for index in 0..4 {
            if index <= chunk.len() {
                out.push(TABLE[((value >> (18 - 6 * index)) & 63) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

/// A future with a time limit (the engine's stop must not hold the app open for ever).
async fn futures_timeout<T>(future: impl Future<Output = T>, limit: Duration) -> Option<T> {
    use std::pin::pin;
    let timer = glib::timeout_future(limit);
    let mut future = pin!(future);
    let mut timer = pin!(timer);
    std::future::poll_fn(move |context| {
        if let std::task::Poll::Ready(value) = future.as_mut().poll(context) {
            return std::task::Poll::Ready(Some(value));
        }
        if timer.as_mut().poll(context).is_ready() {
            return std::task::Poll::Ready(None);
        }
        std::task::Poll::Pending
    })
    .await
}

/// Starts the GTK app. `autostart`: started with the computer (stays in the tray when it can).
pub fn run(autostart: bool) -> i32 {
    use adw::prelude::*;
    let application = adw::Application::builder().application_id(crate::APP_ID).build();
    let app: Rc<RefCell<Option<Rc<App>>>> = Rc::new(RefCell::new(None));
    let holder = app.clone();
    application.connect_activate(move |application| {
        if let Some(existing) = holder.borrow().as_ref() {
            if let Some(ui) = existing.ui.borrow().as_ref() {
                ui.present();
            }
            return;
        }
        let engine: Rc<dyn Engine> = match crate::engine::RealEngine::new(crate::store::data_dir()) {
            Ok(engine) => Rc::new(engine),
            Err(error) => {
                eprintln!("{error}");
                application.quit();
                return;
            }
        };
        let created = App::new(engine, Store::open());
        crate::ui::install(&created, application, autostart);
        created.start();
        holder.replace(Some(created));
    });
    let empty: [&str; 0] = [];
    if application.run_with_args(&empty) == glib::ExitCode::SUCCESS { 0 } else { 1 }
}

#[cfg(test)]
mod tests {
    #[test]
    fn base64_and_plain_answers() {
        assert_eq!(super::base64_encode(b"Mixel"), "TWl4ZWw=");
        assert_eq!(super::base64_encode(b"ab"), "YWI=");
        assert_eq!(super::base64_encode(b"abc"), "YWJj");
        assert_eq!(super::plain("## Step\n* **Restart** the spooler"), "Step\n• Restart the spooler");
    }
}
