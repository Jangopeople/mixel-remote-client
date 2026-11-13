//! A demo engine: answers every command from prepared data, for screenshots and tests of the app's
//! state and actions. No account, no network, nothing on this computer is changed.
use crate::engine::{Engine, Info, Reply};
use serde_json::{Value, json};
use std::{
    cell::{Cell, RefCell},
    collections::HashMap,
};

pub struct MockEngine {
    answers: RefCell<HashMap<String, Result<Value, String>>>,
    pub calls: RefCell<Vec<(String, Value)>>,
    sequence: Cell<u64>,
    pub frame: (usize, usize),
}

impl MockEngine {
    pub fn new() -> Self {
        let engine = Self { answers: RefCell::new(HashMap::new()), calls: RefCell::new(Vec::new()), sequence: Cell::new(0), frame: (1280, 800) };
        engine.set("account_status", json!({ "phase": "signed_out", "message": "Sign in with your Mixel account", "account": null, "factors": [] }));
        engine.set("remote_status", json!({ "phase": "offline", "message": "", "platform": "linux", "input_access": true }));
        engine.set("linux_session", json!({ "wayland": false, "x11": true }));
        engine.set("startup_settings", json!({ "remembered": false, "autostart": false, "launched_at_startup": false, "restored": false }));
        engine.set("remote_printing_status", json!({ "supported": true, "installed": false }));
        engine.set("screen_access", json!(true));
        engine.set("device_removal_pending", json!(false));
        engine.set("device_self_remove_flush", Value::Null);
        engine.set("signin_screen_status", json!({ "supported": true, "outdated": false, "technician": false, "connection_id": null, "name": null, "wayland_greeter": false }));
        engine.set("this_computer_name", json!("debian-office"));
        engine.set("update_check", Value::Null);
        engine.fail("saas_my_tenants", "No company");
        engine
    }

    pub fn set(&self, command: &str, value: Value) {
        self.answers.borrow_mut().insert(command.into(), Ok(value));
    }

    pub fn fail(&self, command: &str, error: &str) {
        self.answers.borrow_mut().insert(command.into(), Err(error.into()));
    }

    pub fn called(&self, command: &str) -> Vec<Value> {
        self.calls.borrow().iter().filter(|(name, _)| name == command).map(|(_, args)| args.clone()).collect()
    }
}

impl Engine for MockEngine {
    fn info(&self) -> Info {
        Info { version: mixel_engine::agent::APP_VERSION.into(), platform: "linux".into(), controller_only: false }
    }

    fn call(&self, command: &str, args: Value) -> Reply<Value> {
        self.calls.borrow_mut().push((command.into(), args));
        let answer = self.answers.borrow().get(command).cloned().unwrap_or(Ok(Value::Null));
        Box::pin(async move { answer })
    }

    fn next_video(&self, _session: &str) -> Reply<Vec<u8>> {
        let sequence = self.sequence.get() + 1;
        self.sequence.set(sequence);
        // A still remote screen sends no new pictures: after a few, none (the window settles).
        if sequence > 12 {
            return Box::pin(async {
                gtk::glib::timeout_future(std::time::Duration::from_millis(100)).await;
                Ok(Vec::new())
            });
        }
        let frame = crate::video::sample(self.frame.0, self.frame.1, sequence);
        // About 10 pictures a second, as a calm remote screen.
        Box::pin(async move {
            gtk::glib::timeout_future(std::time::Duration::from_millis(100)).await;
            Ok(frame)
        })
    }

    fn input(&self, _session: &str, event: Value) -> Reply<bool> {
        self.calls.borrow_mut().push(("input".into(), event));
        Box::pin(async { Ok(true) })
    }

    fn shutdown(&self) -> Reply<()> {
        Box::pin(async { Ok(()) })
    }
}

pub const TENANT: &str = "6d9c2d8e-5f86-4f8e-9a77-0e3b1c2d4f10";
pub const SESSION: &str = "1b7e7c0e-3f0a-4b8a-9d6e-6a0a2f1c9e21";
pub const CONSENT: &str = "2c8f8d1f-4a1b-4c9b-8e7f-7b1b3a2d0f32";

/// Demo data of a signed-in company owner (Acme Treuhand AG) on this computer.
pub fn signed_in(engine: &MockEngine, staff: bool) {
    engine.set(
        "account_status",
        json!({ "phase": "authenticated", "message": "", "factors": [], "account": {
            "remembered": true, "has_company": true, "has_personal": false, "profile": "a1b2c3d4-0000-4000-8000-000000000001",
            "organization": "org", "staff": staff, "personal": false, "display_name": "Anna Keller", "companies": [] } }),
    );
    engine.set("remote_status", json!({ "phase": "ready", "message": "", "platform": "linux", "host": 482_615_903u32, "input_access": true, "online": [731_204_558u32, 118_406_271u32] }));
    engine.set("startup_settings", json!({ "remembered": true, "autostart": true, "launched_at_startup": false, "restored": true }));
    if staff {
        engine.fail("saas_my_tenants", "No company");
        return;
    }
    engine.set("saas_my_tenants", json!([{ "tenant_id": TENANT, "organization_id": "org", "name": "Acme Treuhand AG", "slug": "acme", "role": "owner", "plan": "business", "status": "trial", "trial_ends_at": "2026-11-27T10:00:00Z" }]));
    engine.set(
        "saas_tenant_overview",
        json!({ "role": "owner", "status": "trial", "plan": "business", "trial_ends_at": "2026-11-27T10:00:00Z", "features": { "groups": true }, "limits": { "devices": 2000, "technicians": 15 }, "usage": { "devices": 6, "technicians": 3 } }),
    );
    engine.set(
        "saas_members",
        json!([
            { "user_id": "a1b2c3d4-0000-4000-8000-000000000001", "name": "Anna Keller", "email": "anna.keller@acme-treuhand.ch", "role": "owner", "created_at": "2026-11-01T09:00:00Z" },
            { "user_id": "a1b2c3d4-0000-4000-8000-000000000002", "name": "Marco Rossi", "email": "marco.rossi@acme-treuhand.ch", "role": "technician", "created_at": "2026-11-02T09:00:00Z" },
            { "user_id": "a1b2c3d4-0000-4000-8000-000000000003", "name": "Lea Meier", "email": "lea.meier@acme-treuhand.ch", "role": "member", "created_at": "2026-11-03T09:00:00Z" }
        ]),
    );
    engine.set("saas_invites", json!([{ "id": "inv1", "email": "it@acme-treuhand.ch", "role": "admin", "created_at": "2026-11-12T09:00:00Z", "expires_at": "2026-11-19T09:00:00Z", "mail_state": "delivered" }]));
    engine.set(
        "saas_devices",
        json!([
            { "device_id": "d1", "name": "debian-office", "os": "linux", "group_id": "g1", "group_name": "Office Zürich", "connection_id": 482_615_903u32, "generation": 1 },
            { "device_id": "d2", "name": "Reception PC", "os": "windows", "group_id": "g1", "group_name": "Office Zürich", "connection_id": 731_204_558u32, "generation": 1 },
            { "device_id": "d3", "name": "MacBook Anna", "os": "macos", "group_id": null, "group_name": null, "connection_id": 118_406_271u32, "generation": 1 },
            { "device_id": "d4", "name": "Accounting laptop", "os": "windows", "group_id": "g2", "group_name": "Home office", "connection_id": 905_377_412u32, "generation": 1 },
            { "device_id": "d5", "name": "Warehouse terminal", "os": "linux", "group_id": "g2", "group_name": "Home office", "connection_id": 264_980_135u32, "generation": 1 }
        ]),
    );
    engine.set("saas_favorites", json!([{ "device_id": "d2", "created_at": "2026-11-12T09:00:00Z" }]));
    engine.set("saas_recent_devices", json!([{ "device_id": "d3", "last_connected_at": "2026-11-13T08:40:00Z" }, { "device_id": "d2", "last_connected_at": "2026-11-12T16:10:00Z" }]));
    engine.set(
        "saas_session_report",
        json!([
            { "session_id": "s1", "technician_name": "Marco Rossi", "technician_email": "marco.rossi@acme-treuhand.ch", "device_id": "d2", "device_name": "Reception PC", "connection_id": 731_204_558u32, "started_at": "2026-11-13T08:40:00Z", "ended_at": "2026-11-13T09:02:00Z", "duration_seconds": 1320, "kind": "attended", "colleague": false, "note": "Printer driver reinstalled, test page printed." },
            { "session_id": "s2", "technician_name": "Anna Keller", "technician_email": "anna.keller@acme-treuhand.ch", "device_id": "d4", "device_name": "Accounting laptop", "connection_id": 905_377_412u32, "started_at": "2026-11-12T16:10:00Z", "ended_at": "2026-11-12T17:15:00Z", "duration_seconds": 3900, "kind": "unattended", "colleague": false, "note": null },
            { "session_id": "s3", "technician_name": "Lea Meier", "technician_email": "lea.meier@acme-treuhand.ch", "device_id": "d2", "device_name": "Reception PC", "connection_id": 731_204_558u32, "started_at": "2026-11-11T10:05:00Z", "ended_at": "2026-11-11T10:17:00Z", "duration_seconds": 720, "kind": "attended", "colleague": true, "note": null }
        ]),
    );
    engine.set("saas_groups", json!([{ "group_id": "g1", "name": "Office Zürich", "devices": 2, "technicians": [] }, { "group_id": "g2", "name": "Home office", "devices": 2, "technicians": [] }]));
    engine.set(
        "saas_device_presence",
        json!([
            { "device_id": "d4", "connection_id": 905_377_412u32, "status": "offline", "last_seen": "2026-11-13T08:00:00Z" },
            { "device_id": "d5", "connection_id": 264_980_135u32, "status": "offline", "last_seen": "2026-11-12T09:00:00Z" }
        ]),
    );
}

/// A session this computer views and controls (a Windows computer with all host actions).
pub fn viewing(engine: &MockEngine) {
    engine.set(
        "remote_status",
        json!({ "phase": "active", "message": "", "platform": "linux", "host": 482_615_903u32, "session": SESSION, "consent": CONSENT, "peer": 731_204_558u32, "received": 120,
            "control": true, "clipboard": true, "files": true, "recording_permitted": true, "recording": { "phase": "ready" }, "input_access": true,
            "displays": 3, "display": 0, "display_all": true, "host_actions": ["privacy", "block", "pointer", "lock", "lockend", "sas", "restart", "sound", "meet"],
            "peer_features": ["join", "switch", "draw"], "host_macs": ["52:54:00:12:34:56"] }),
    );
    engine.set("remote_chat_messages", json!([{ "id": 1, "mine": false, "text": "Hello, the printer in reception does not print." }, { "id": 2, "mine": true, "text": "I'll have a look at the driver now." }]));
    engine.set("remote_file_status", json!({ "connected": true, "message": "", "outgoing": { "id": "f1", "name": "printer-driver-x64.zip", "size": 48_000_000u64, "offset": 31_200_000u64, "phase": "transferring", "message": "" }, "incoming": { "id": "f2", "name": "error-log.txt", "size": 18_400u64, "offset": 0, "phase": "offered", "message": "" } }));
}

/// A technician asks to connect to this computer.
pub fn incoming(engine: &MockEngine) {
    engine.set("remote_status", json!({ "phase": "incoming", "message": "", "platform": "linux", "host": 482_615_903u32, "session": SESSION, "peer": 731_204_558u32, "input_access": true }));
    engine.set("capture_targets", json!([{ "id": "x11:0:1", "name": "X11 screen 0 · 1920 × 1080", "full_display": true }]));
}

/// This computer is shared; a second technician asks to join.
pub fn hosting(engine: &MockEngine) {
    engine.set(
        "remote_status",
        json!({ "phase": "active", "message": "", "platform": "linux", "host": 482_615_903u32, "session": SESSION, "consent": CONSENT, "peer": 731_204_558u32, "sharing": true, "control": true, "recording_permitted": true,
            "input_access": true, "peer_features": ["join", "switch"],
            "colleagues": [{ "session": "c1", "connection": 118_406_271u32, "control": false, "state": "active" }],
            "join_request": { "session": "c2", "connection": 905_377_412u32, "inviter_connection": 731_204_558u32, "control": true, "unattended": false } }),
    );
}
