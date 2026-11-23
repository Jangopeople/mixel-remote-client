//! Several technician sessions at once, against the demo engine: one tab per session (bound to the
//! stable id), input / clipboard / toolbar actions only for the session in front, one session ending
//! alone, the typed "too many sessions" error, the single-session fallback and a transport renewal.
//! GLib's main context belongs to one thread, so every scenario runs on one worker thread that owns it.
use crate::{
    app::App,
    i18n,
    mock::{self, MockEngine},
    models::{RemoteStatus, RequestError},
    store::{Settings, Store},
    workspace::Tab,
};
use gtk::glib;
use serde_json::{Value, json};
use std::{
    rc::Rc,
    sync::{Mutex, OnceLock, mpsc},
    time::Duration,
};

const PEERS: [u32; 5] = [731_204_558, 118_406_271, 905_377_412, 264_980_135, 482_000_111];

fn id(n: usize) -> String {
    format!("00000000-0000-4000-8000-00000000000{}", n + 1)
}

/// One `remote_status.sessions[]` item (all fields the engine sends).
fn item(n: usize, phase: &str, transport: &str) -> Value {
    json!({
        "id": id(n), "session": if transport.is_empty() { Value::Null } else { json!(transport) },
        "peer": PEERS[n], "device": PEERS[n], "phase": phase, "message": "",
        "consent": format!("10000000-0000-4000-8000-00000000000{}", n + 1), "received": 40,
        "control": true, "clipboard": true, "files": true, "recording_permitted": false, "recording": null,
        "displays": 1, "display": 0, "display_all": false, "print_jobs": [], "host_actions": ["privacy", "lock"],
        "signin_id": null, "host_macs": [], "meeting_offer": null, "peer_features": ["draw"],
        "joined_session": false, "switched": false,
        "quality": { "pictures": 40, "pictures_per_second": 12.0, "picture_age_ms": 30 }, "started_at": 1_800_000_000 + n
    })
}

fn status_json(items: Vec<Value>) -> Value {
    // The oldest session also fills the single-session fields, as the engine does.
    let first = items.first().cloned();
    let mut value = json!({ "phase": if items.is_empty() { "ready" } else { "active" }, "message": "", "platform": "linux", "host": 482_615_903u32, "input_access": true, "max_sessions": 4, "sessions": items });
    if let Some(first) = first {
        for key in ["session", "peer", "consent", "control", "clipboard", "files", "peer_features", "host_actions"] {
            value[key] = first[key].clone();
        }
        value["session"] = first["id"].clone();
    }
    value
}

fn status(items: Vec<Value>) -> RemoteStatus {
    serde_json::from_value(status_json(items)).expect("status")
}

struct Rig {
    app: Rc<App>,
    engine: Rc<MockEngine>,
}

fn rig() -> Rig {
    i18n::set_choice("en");
    let engine = Rc::new(MockEngine::new());
    engine.no_pictures.set(true);
    mock::signed_in(&engine, false);
    engine.set("quick_phone_claim", json!(false));
    let app = App::new(engine.clone(), Store::memory(Settings::default()));
    app.edit(|state| state.account = serde_json::from_value(json!({ "phase": "authenticated", "message": "", "factors": [], "account": { "remembered": true, "has_company": false, "has_personal": true, "profile": "p", "organization": "o", "staff": false, "personal": true, "display_name": "T", "companies": [] } })).expect("account"));
    Rig { app, engine }
}

type Job = (fn(), mpsc::Sender<Result<(), String>>);

/// Runs a scenario on the worker thread that owns GLib's default main context.
fn on_main(scenario: fn()) {
    static WORKER: OnceLock<Mutex<mpsc::Sender<Job>>> = OnceLock::new();
    let worker = WORKER.get_or_init(|| {
        let (send, receive) = mpsc::channel::<Job>();
        std::thread::spawn(move || {
            let main = glib::MainContext::default();
            let _context = main.acquire().expect("main context");
            while let Ok((scenario, done)) = receive.recv() {
                let result = std::panic::catch_unwind(scenario).map_err(|panic| panic.downcast_ref::<String>().cloned().or_else(|| panic.downcast_ref::<&str>().map(|text| (*text).to_owned())).unwrap_or_else(|| "panic".into()));
                let _ = done.send(result);
            }
        });
        Mutex::new(send)
    });
    let (done, result) = mpsc::channel();
    worker.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).send((scenario, done)).expect("worker");
    if let Err(message) = result.recv().expect("scenario result") {
        panic!("{message}");
    }
}

macro_rules! scenarios {
    ($(fn $name:ident() $body:block)*) => {
        $(
            #[test]
            fn $name() {
                fn scenario() $body
                on_main(scenario);
            }
        )*
    };
}

fn pump() {
    let context = glib::MainContext::default();
    while context.iteration(false) {}
}

fn pump_until(what: &str, done: impl Fn() -> bool) {
    for _ in 0..300 {
        pump();
        if done() {
            return;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    panic!("timed out waiting for: {what}");
}

impl Rig {
    /// The engine reports these sessions (now and at every later read), as the app takes them in.
    fn report(&self, items: Vec<Value>) {
        self.engine.set("remote_status", status_json(items.clone()));
        self.app.take_status(status(items));
    }

    /// The engine reports these sessions; every one has its first picture and may be controlled.
    fn sessions(&self, items: Vec<Value>) {
        self.report(items);
        let ids: Vec<String> = self.app.state.borrow().raw.sessions.iter().map(|item| item.id.clone()).collect();
        self.app.edit(|state| {
            for id in &ids {
                state.session_state_mut(id).has_frame = true;
            }
        });
    }
}

fn tab_ids(app: &App) -> Vec<String> {
    app.state.borrow().workspace.tabs.iter().map(|tab| tab.id.clone()).collect()
}

fn tab_peers(app: &App) -> Vec<u32> {
    app.state.borrow().workspace.tabs.iter().map(|tab| tab.peer).collect()
}

fn closes(engine: &MockEngine) -> Vec<String> {
    engine.called("remote_close").iter().map(|args| args["session"].as_str().unwrap_or_default().to_owned()).collect()
}

/// A session whose computer is gone: phase `outgoing`, and the top-level single-session fields are empty.
fn gone(n: usize, failed: bool) -> Value {
    let mut value = item(n, "outgoing", "");
    value["reconnecting"] = json!(!failed);
    value["reconnect_failed"] = json!(failed);
    value["message"] = json!(if failed { "Could not reconnect" } else { "Reconnecting..." });
    value
}

fn report_with_empty_top_level(rig: &Rig, items: Vec<Value>) {
    let mut value = status_json(items);
    for key in ["session", "peer", "consent"] {
        value[key] = Value::Null;
    }
    for key in ["control", "clipboard", "files"] {
        value[key] = json!(false);
    }
    value["phase"] = json!("ready");
    rig.engine.set("remote_status", value.clone());
    rig.app.take_status(serde_json::from_value(value).expect("status"));
}

scenarios! {
    fn startup_loads_devices_before_company_administration() {
        let engine = Rc::new(MockEngine::new());
        mock::signed_in(&engine, false);
        let app = App::new(engine.clone(), Store::memory(Settings::default()));
        glib::MainContext::default().block_on(app.refresh_account());
        pump_until("startup administration", || !engine.called("saas_members").is_empty());
        let calls = engine.calls.borrow();
        let devices = calls.iter().position(|(name, _)| name == "saas_devices").expect("devices loaded");
        let members = calls.iter().position(|(name, _)| name == "saas_members").unwrap();
        assert!(devices < members, "administration requests must not delay the launch device list");
        drop(calls);
        let started = std::time::Instant::now();
        glib::MainContext::default().block_on(app.load_devices(0));
        assert!(started.elapsed() < Duration::from_millis(300), "initial presence must not impose the background 600 ms grace period");
    }

fn two_to_four_sessions_give_two_to_four_tabs_bound_to_their_stable_ids() {
    for count in 2..=4 {
        let rig = rig();
        rig.sessions((0..count).map(|n| item(n, "active", &format!("t{n}"))).collect());
        assert_eq!(tab_ids(&rig.app), (0..count).map(id).collect::<Vec<_>>(), "{count} sessions");
        assert_eq!(tab_peers(&rig.app), PEERS[..count].to_vec());
        let state = rig.app.state.borrow();
        assert_eq!(state.raw.max_sessions, Some(4));
        assert!(state.can_connect() == (count < 4), "Connect stays on while fewer than four are open ({count})");
    }
}

fn a_picture_is_fetched_for_every_session_by_its_own_id() {
    let rig = rig();
    rig.report((0..3).map(|n| item(n, "active", &format!("t{n}"))).collect());
    pump_until("a picture request per session", || (0..3).all(|n| rig.engine.videos.borrow().contains(&id(n))));
    assert!(rig.engine.videos.borrow().iter().all(|asked| (0..3).any(|n| *asked == id(n))), "only stable ids");
}

fn input_clipboard_and_toolbar_go_only_to_the_session_in_front() {
    let rig = rig();
    rig.sessions((0..3).map(|n| item(n, "active", &format!("t{n}"))).collect());
    let app = &rig.app;
    // Nothing is in front (Devices): nothing is sent anywhere.
    assert_eq!(app.state.borrow().focus, None);
    app.send_input(json!({ "kind": "key", "usage": 4, "down": true }));
    app.session_action("lock", false);
    app.toggle_privacy();
    app.clipboard(true);
    app.send_chat("hello");
    pump();
    assert!(rig.engine.inputs.borrow().is_empty(), "no input without a session in front");
    assert!(rig.engine.called("remote_clipboard").is_empty());
    assert!(rig.engine.called("remote_chat_send").is_empty());

    // Session 2 in front: its input, its toolbar, its clipboard, its chat.
    app.focus_session(Some(id(1)));
    assert_eq!(app.state.borrow().remote.session.as_deref(), Some(id(1).as_str()), "the status the screens read is the stable id");
    assert_eq!(app.state.borrow().remote.peer, Some(PEERS[1]));
    app.send_input(json!({ "kind": "key", "usage": 4, "down": true }));
    app.session_action("lock", false);
    app.clipboard(true);
    app.send_chat("hello");
    pump_until("the events", || rig.engine.inputs_of(&id(1)).len() == 2 && !rig.engine.called("remote_chat_send").is_empty());
    assert_eq!(rig.engine.inputs_of(&id(0)).len(), 0);
    assert_eq!(rig.engine.inputs_of(&id(2)).len(), 0);
    assert_eq!(rig.engine.called("remote_clipboard"), vec![json!({ "session": id(1), "push": true })]);
    assert_eq!(rig.engine.called("remote_chat_send")[0]["session"], json!(id(1)));

    // Another session in front: only it gets the next ones.
    app.focus_session(Some(id(2)));
    app.send_input(json!({ "kind": "key", "usage": 5, "down": true }));
    app.toggle_privacy();
    app.clipboard(false);
    pump_until("the events of the third", || rig.engine.inputs_of(&id(2)).len() == 2);
    assert_eq!(rig.engine.inputs_of(&id(1)).len(), 2, "the second got nothing more");
    assert_eq!(rig.engine.inputs_of(&id(0)).len(), 0);
    assert_eq!(rig.engine.called("remote_clipboard").len(), 2);
    assert_eq!(rig.engine.called("remote_clipboard")[1]["session"], json!(id(2)));

    // A view that is not in front cannot send, whatever it tries.
    let before = rig.engine.inputs.borrow().len();
    app.send_input_to(&id(0), json!({ "kind": "key", "usage": 6, "down": true }));
    app.send_input_to(&id(1), json!({ "kind": "key", "usage": 6, "down": true }));
    pump();
    assert_eq!(rig.engine.inputs.borrow().len(), before);
    // Letting go of held keys is always allowed (a view that lost the front does it).
    app.send_input_to(&id(1), json!({ "kind": "release_all" }));
    pump_until("the release", || rig.engine.inputs.borrow().len() == before + 1);
    assert_eq!(rig.engine.inputs.borrow().last().map(|(session, _)| session.clone()), Some(id(1)));
}

fn a_session_without_control_or_picture_gets_no_input_even_in_front() {
    let rig = rig();
    let mut view_only = item(0, "active", "t0");
    view_only["control"] = json!(false);
    rig.sessions(vec![view_only, item(1, "active", "t1")]);
    rig.app.focus_session(Some(id(0)));
    rig.app.send_input(json!({ "kind": "key", "usage": 4, "down": true }));
    rig.app.focus_session(Some(id(1)));
    rig.app.edit(|state| state.session.has_frame = false);
    rig.app.send_input(json!({ "kind": "key", "usage": 4, "down": true }));
    pump();
    assert!(rig.engine.inputs.borrow().is_empty());
}

fn closing_one_session_ends_only_that_session() {
    let rig = rig();
    rig.sessions((0..3).map(|n| item(n, "active", &format!("t{n}"))).collect());
    rig.app.end_session_peer(PEERS[1]);
    pump_until("the close", || !closes(&rig.engine).is_empty());
    assert_eq!(closes(&rig.engine), vec![id(1)], "only the second session was closed, by its stable id");
    assert!(rig.engine.called("remote_stop_session").is_empty());
    // The tab's close button works on a tab that is not in front, too.
    rig.app.focus_session(Some(id(0)));
    rig.app.end_session_peer(PEERS[2]);
    pump_until("the second close", || closes(&rig.engine).len() == 2);
    assert_eq!(closes(&rig.engine), vec![id(1), id(2)]);
    // The first one was never touched, and its input still works.
    rig.app.send_input(json!({ "kind": "key", "usage": 4, "down": true }));
    pump_until("input of the first", || rig.engine.inputs_of(&id(0)).len() == 1);
}

fn a_reconnecting_session_keeps_its_tab_and_can_be_ended_by_its_stable_id() {
    let rig = rig();
    rig.sessions((0..2).map(|n| item(n, "active", &format!("t{n}"))).collect());
    rig.app.select_tab(Tab::Session(PEERS[1]));
    // The second computer is gone: its session stays listed, the top-level session is empty.
    report_with_empty_top_level(&rig, vec![item(0, "active", "t0"), gone(1, false)]);
    assert_eq!(tab_ids(&rig.app), vec![id(0), id(1)], "the tab stays");
    {
        let state = rig.app.state.borrow();
        assert_eq!(state.workspace.tab(PEERS[1]).map(|tab| tab.state), Some(crate::workspace::TabState::Reconnecting));
        assert_eq!(state.workspace.selected, Tab::Session(PEERS[1]), "and stays selected");
        assert!(state.raw.session.is_none());
        let view = state.viewed_as(&id(1)).expect("view");
        assert!(view.remote.session_reconnecting && !view.remote.session_reconnect_failed);
        assert_eq!(crate::logic::session_wait_text(&view.remote), "Reconnecting...");
    }
    // End works without a top-level session, by the stable id, and only ends that one.
    rig.app.end_session_id(&id(1));
    pump_until("the close", || !closes(&rig.engine).is_empty());
    assert_eq!(closes(&rig.engine), vec![id(1)]);
}

fn a_failed_reconnect_stays_listed_and_retry_asks_again_by_stable_id() {
    let rig = rig();
    rig.sessions(vec![item(0, "active", "t0")]);
    report_with_empty_top_level(&rig, vec![gone(0, true)]);
    assert_eq!(tab_ids(&rig.app), vec![id(0)]);
    assert_eq!(rig.app.state.borrow().workspace.tab(PEERS[0]).map(|tab| tab.state), Some(crate::workspace::TabState::ReconnectFailed));
    assert_eq!(crate::logic::session_wait_text(&rig.app.state.borrow().viewed_as(&id(0)).expect("view").remote), "Could not reconnect");
    rig.app.reconnect_session(&id(0));
    pump_until("remote_reconnect", || !rig.engine.called("remote_reconnect").is_empty());
    assert_eq!(rig.engine.called("remote_reconnect"), vec![json!({ "session": id(0) })]);
    // Retry means nothing for a session that is connected or retrying already.
    report_with_empty_top_level(&rig, vec![gone(0, false)]);
    rig.app.reconnect_session(&id(0));
    pump();
    assert_eq!(rig.engine.called("remote_reconnect").len(), 1);
    // The computer came back: the same session is active again, in the same tab, and is not "new".
    rig.sessions(vec![item(0, "active", "t1")]);
    assert_eq!(tab_ids(&rig.app), vec![id(0)]);
    assert!(rig.app.state.borrow().workspace.ready_notices.is_empty());
    // An ended session can be closed while it is failed, too.
    report_with_empty_top_level(&rig, vec![gone(0, true)]);
    rig.app.end_session_id(&id(0));
    pump_until("the close", || !closes(&rig.engine).is_empty());
    assert_eq!(closes(&rig.engine), vec![id(0)]);
}

fn a_typed_sign_in_address_is_remembered_and_offered_to_the_sign_in_page() {
    let rig = rig();
    assert_eq!(rig.app.store.borrow().settings.last_signin_email, "", "nothing remembered yet: the field starts empty");
    // A bad address starts nothing and says so.
    rig.app.sign_in_with_email(false, "not an address");
    pump();
    assert!(rig.engine.called("account_sign_in").is_empty());
    assert_eq!(rig.app.state.borrow().error, "Enter an e-mail address");
    // Any typed address is accepted, trimmed, remembered as the last used one and passed on.
    rig.app.sign_in_with_email(false, "  tech@kunde-ag.example ");
    pump_until("account_sign_in", || !rig.engine.called("account_sign_in").is_empty());
    assert_eq!(rig.engine.called("account_sign_in")[0]["login_hint"], "tech@kunde-ag.example");
    assert_eq!(rig.app.store.borrow().settings.last_signin_email, "tech@kunde-ag.example");
    // An empty field lets the browser ask; the last used address stays.
    rig.app.sign_in_with_email(false, "");
    pump_until("second account_sign_in", || rig.engine.called("account_sign_in").len() == 2);
    assert!(rig.engine.called("account_sign_in")[1]["login_hint"].is_null());
    assert_eq!(rig.app.store.borrow().settings.last_signin_email, "tech@kunde-ag.example");
}

fn the_password_limit_warns_only_in_the_session_it_belongs_to() {
    let rig = rig();
    let mut first = item(0, "active", "t0");
    first["password_session"] = json!({ "ends_at": "2026-11-17T12:00:00Z", "seconds_left": 200 });
    let mut second = item(1, "active", "t1");
    second["unattended_session"] = json!(true);
    rig.sessions(vec![first, second]);
    let state = rig.app.state.borrow();
    let warn = |n: usize| crate::logic::session_warnings(&state.viewed_as(&id(n)).expect("view").remote);
    assert!(warn(0)[0].contains("4 minutes"));
    assert!(warn(1).is_empty());
    assert!(state.viewed_as(&id(1)).expect("view").remote.unattended_session);
    assert!(!state.viewed_as(&id(0)).expect("view").remote.unattended_session);
}

fn a_session_ending_removes_only_its_tab_and_keeps_the_others_state() {
    let rig = rig();
    rig.sessions((0..3).map(|n| item(n, "active", &format!("t{n}"))).collect());
    rig.app.focus_session(Some(id(2)));
    rig.app.edit(|state| state.session.privacy = true);
    rig.app.select_tab(Tab::Session(PEERS[2]));
    // The second one ends on the host's side.
    rig.report(vec![item(0, "active", "t0"), item(2, "active", "t2")]);
    assert_eq!(tab_ids(&rig.app), vec![id(0), id(2)]);
    {
        let state = rig.app.state.borrow();
        assert_eq!(state.focus.as_deref(), Some(id(2).as_str()), "the one in front stays in front");
        assert!(state.session.privacy, "and keeps its own state");
        assert!(state.session_state(&id(1)).is_none());
        assert_eq!(state.workspace.selected, Tab::Session(PEERS[2]));
    }
    // The one in front ends: Devices comes back and nothing is in front.
    rig.report(vec![item(0, "active", "t0")]);
    assert_eq!(tab_ids(&rig.app), vec![id(0)]);
    assert_eq!(rig.app.state.borrow().focus, None);
    assert_eq!(rig.app.state.borrow().workspace.selected, Tab::Console);
    rig.app.send_input(json!({ "kind": "key", "usage": 4, "down": true }));
    pump();
    assert!(rig.engine.inputs.borrow().is_empty());
}

fn a_fifth_request_shows_the_typed_error_as_a_friendly_sentence() {
    let rig = rig();
    rig.engine.set("remote_status", serde_json::to_value(json!({ "phase": "active", "max_sessions": 4, "sessions": (0..4).map(|n| item(n, "active", "t")).collect::<Vec<_>>() })).expect("json"));
    // The app knows it is full: nothing is sent to the engine.
    rig.app.connect(PEERS[4], None, false);
    pump_until("the refusal", || !rig.app.state.borrow().busy && !rig.app.state.borrow().error.is_empty());
    assert_eq!(rig.app.state.borrow().error, "You already have 4 computers open. End one before connecting to another.");
    assert!(rig.engine.called("remote_request").is_empty());

    // The engine refuses (its typed error): the same sentence.
    let typed = json!({ "hint": "too_many_sessions", "max": 4, "message": "You already have 4 sessions open. End one to start another." }).to_string();
    rig.engine.set("remote_status", serde_json::to_value(json!({ "phase": "active", "max_sessions": 4, "sessions": (0..3).map(|n| item(n, "active", "t")).collect::<Vec<_>>() })).expect("json"));
    rig.engine.fail("remote_request", &typed);
    rig.app.edit(|state| state.error.clear());
    rig.app.connect(PEERS[4], None, false);
    pump_until("the typed error", || !rig.app.state.borrow().busy && !rig.app.state.borrow().error.is_empty());
    assert_eq!(rig.engine.called("remote_request").len(), 1);
    assert_eq!(rig.app.state.borrow().error, "You already have 4 computers open. End one before connecting to another.");
    assert!(!rig.app.state.borrow().error.contains('{'), "no JSON on screen");
}

fn a_request_with_room_left_is_sent_and_the_answer_is_the_new_stable_id() {
    let rig = rig();
    rig.engine.set("remote_status", serde_json::to_value(json!({ "phase": "active", "max_sessions": 4, "sessions": (0..2).map(|n| item(n, "active", "t")).collect::<Vec<_>>() })).expect("json"));
    rig.engine.set("remote_request", json!(id(2)));
    // Not gated on phase == "ready": two sessions run and a third may be asked for.
    rig.report((0..2).map(|n| item(n, "active", "t")).collect());
    assert!(rig.app.state.borrow().can_connect());
    rig.app.connect(PEERS[2], Some("ABCDEFGHJKLM".into()), false);
    pump_until("the request", || !rig.app.state.borrow().busy && !rig.engine.called("remote_request").is_empty());
    assert_eq!(rig.engine.called("remote_request")[0]["host"], json!(PEERS[2]));
    assert!(rig.app.state.borrow().error.is_empty(), "{}", rig.app.state.borrow().error);
    assert!(rig.app.state.borrow().password_peers.contains(&PEERS[2]), "the one-time password warning follows the device");
}

fn an_engine_without_sessions_falls_back_to_the_single_session_fields() {
    let rig = rig();
    mock::viewing(&rig.engine);
    let old: RemoteStatus = serde_json::from_value(rig.engine.answer("remote_status")).expect("status");
    assert!(old.sessions.is_empty() && old.max_sessions.is_none());
    rig.app.take_status(old);
    assert_eq!(tab_peers(&rig.app), vec![731_204_558]);
    assert_eq!(tab_ids(&rig.app), vec![mock::SESSION.to_owned()], "the id is the connection service's session id");
    let state = rig.app.state.borrow();
    assert_eq!(state.raw.sessions.len(), 1);
    assert_eq!(state.raw.session_limit(), 1);
    assert!(!state.can_connect(), "one session at a time with such an engine");
    drop(state);
    rig.app.edit(|state| state.session_state_mut(mock::SESSION).has_frame = true);
    rig.app.focus_session(Some(mock::SESSION.to_owned()));
    rig.app.send_input(json!({ "kind": "key", "usage": 4, "down": true }));
    pump_until("input", || rig.engine.inputs_of(mock::SESSION).len() == 1);
}

fn a_transport_renewal_keeps_the_tab_the_id_and_the_state() {
    let rig = rig();
    rig.sessions(vec![item(0, "active", "transport-1"), item(1, "active", "transport-a")]);
    rig.app.focus_session(Some(id(0)));
    rig.app.select_tab(Tab::Session(PEERS[0]));
    let tabs = tab_ids(&rig.app);
    // About every 8.5 minutes the connection service names a new transport; the stable id stays.
    rig.report(vec![item(0, "active", "transport-2"), item(1, "active", "transport-a")]);
    assert_eq!(tab_ids(&rig.app), tabs);
    let state = rig.app.state.borrow();
    assert_eq!(state.focus.as_deref(), Some(id(0).as_str()));
    assert_eq!(state.workspace.selected, Tab::Session(PEERS[0]));
    assert_eq!(state.remote.session.as_deref(), Some(id(0).as_str()), "commands keep using the stable id");
    assert_eq!(state.session_item(&id(0)).and_then(|item| item.session.clone()).as_deref(), Some("transport-2"));
    drop(state);
    // The picture loop did not restart, and input still goes to the same id.
    rig.app.edit(|state| state.session.has_frame = true);
    rig.app.send_input(json!({ "kind": "key", "usage": 4, "down": true }));
    pump_until("input after the renewal", || rig.engine.inputs_of(&id(0)).len() == 1);
    assert_eq!(rig.engine.videos.borrow().iter().filter(|asked| **asked == id(0)).count().min(1), 1);
}

fn the_typed_error_reads_as_a_sentence_in_every_language() {
    let typed = json!({ "hint": "too_many_sessions", "max": 4, "message": "x" }).to_string();
    assert_eq!(RequestError::parse(&typed), Some(RequestError::TooManySessions(4)));
    assert_eq!(RequestError::parse(&RequestError::too_many(4)), Some(RequestError::TooManySessions(4)));
    assert_eq!(RequestError::parse(&json!({ "hint": "already_connected", "max": 4, "message": "x" }).to_string()), Some(RequestError::AlreadyConnected));
    assert_eq!(RequestError::parse("Enter a nine-digit Mixel connection ID"), None);
    assert_eq!(RequestError::parse("{\"hint\":\"other\"}"), None);
    for (language, expected) in [("en", "You already have 4 computers open"), ("de", "Sie haben bereits 4 Computer geöffnet"), ("fr", "Vous avez déjà 4 ordinateurs ouverts"), ("it", "Ha già 4 computer aperti")] {
        i18n::set_choice(language);
        let text = i18n::engine(&typed);
        assert!(text.starts_with(expected), "{language}: {text}");
        assert!(!text.contains('{') && !text.contains("%d"));
    }
    i18n::set_choice("en");
}

fn a_session_nobody_looks_at_stops_fetching_pictures_after_its_first() {
    let rig = rig();
    rig.sessions((0..2).map(|n| item(n, "active", &format!("t{n}"))).collect());
    rig.app.select_tab(Tab::Session(PEERS[0]));
    let asked = |n: usize| rig.engine.videos.borrow().iter().filter(|asked| **asked == id(n)).count();
    pump_until("pictures of the one in front", || asked(0) >= 3);
    std::thread::sleep(Duration::from_millis(300));
    pump();
    let hidden = asked(1);
    std::thread::sleep(Duration::from_millis(500));
    pump();
    assert_eq!(asked(1), hidden, "the hidden session is not fetched while it has a picture");
    assert!(asked(0) > hidden || asked(0) >= 3);
    // Its tab comes to the front: fetching goes on.
    rig.app.select_tab(Tab::Session(PEERS[1]));
    pump_until("fetching goes on", || asked(1) > hidden);
}
}
