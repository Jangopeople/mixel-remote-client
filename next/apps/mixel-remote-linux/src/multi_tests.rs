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

scenarios! {
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
