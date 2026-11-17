//! Several technician sessions at once (Mixel Remote 2.1).
//!
//! A technician holds up to [`MAX_SESSIONS`] sessions, each with its own peer, picture mailbox, input,
//! clipboard, chat, file, drawing and recording state. This module is the registry of those sessions: plain
//! data and rules, no network. `remote_session.rs` keeps the broker connection and the per-session
//! negotiation; it looks sessions up here by id.
//!
//! Two ids name a session:
//! - `id`: stable, chosen when the request is made, never changes. Apps bind a tab or window to it.
//! - `session`: the broker's id of the current transport. It is empty until the broker answers and
//!   changes at every transport renewal.
//!
//! Every session-scoped command accepts either one (`find` matches both), so callers that still pass the
//! `status.session` of a single-session engine keep working.
use crate::remote_session::{ClipboardCommand, InputCommand};
use crate::remote_video::VideoMailbox;
use serde::Serialize;
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::mpsc;
use uuid::Uuid;

/// Sessions a technician may hold at the same time (the connection service allows four per device).
pub const MAX_SESSIONS: usize = 4;

/// Typed refusal of a request over the limit, as the JSON text the engine's other typed errors use:
/// `{"hint":"too_many_sessions","max":4,"message":"..."}`.
pub const TOO_MANY_SESSIONS: &str = "too_many_sessions";
/// Typed refusal of a second session to a device that already has one.
pub const ALREADY_CONNECTED: &str = "already_connected";

/// JSON error text with a machine-readable `hint`.
pub fn typed_error(hint: &str, message: &str) -> String {
    serde_json::json!({ "hint": hint, "max": MAX_SESSIONS, "message": message }).to_string()
}

/// Picture figures of one session, from what the engine receives.
#[derive(Clone, Default, Serialize)]
pub struct Quality {
    pub pictures: u64,
    pub pictures_per_second: f32,
    /// Milliseconds since the last picture arrived (`None`: none yet).
    pub picture_age_ms: Option<u64>,
}

/// What the apps read for one session (`remote_status.sessions[]`).
#[derive(Clone, Serialize)]
pub struct SessionStatus {
    /// Stable id: bind tabs and windows to it.
    pub id: Uuid,
    /// The broker's id of the current transport (`null` until the broker answered).
    pub session: Option<Uuid>,
    /// Remote ID of the device this session views.
    pub peer: u32,
    pub device: u32,
    /// `outgoing`, `negotiating`, `active` or `closing`.
    pub phase: String,
    pub message: String,
    pub consent: Option<Uuid>,
    pub received: u64,
    pub control: bool,
    pub clipboard: bool,
    pub files: bool,
    pub recording_permitted: bool,
    pub recording: Option<crate::remote_recording::View>,
    pub displays: u16,
    pub display: u16,
    pub display_all: bool,
    pub print_jobs: Vec<String>,
    pub host_actions: Vec<String>,
    pub signin_id: Option<u32>,
    pub host_macs: Vec<String>,
    pub meeting_offer: Option<(Uuid, String)>,
    pub peer_features: Vec<String>,
    pub joined_session: bool,
    pub switched: bool,
    pub quality: Quality,
    /// Seconds since the Unix epoch.
    pub started_at: u64,
}

/// One technician session. The fields the commands need are private to the engine.
pub(crate) struct Slot {
    pub view: SessionStatus,
    pub video: Arc<VideoMailbox>,
    pub session_cancel: Option<Arc<AtomicBool>>,
    pub consent_cancel: Option<Arc<AtomicBool>>,
    pub file_permission: Option<bool>,
    pub input: Option<mpsc::Sender<InputCommand>>,
    pub clipboard: Option<mpsc::Sender<ClipboardCommand>>,
    pub drawing: Option<mpsc::Sender<core_protocol::annotation::Message>>,
    pub renew_after: Option<Instant>,
    /// The person ended it (or the registration dropped): no command reaches it any more.
    pub ended: bool,
    last_picture: Option<Instant>,
    rate_at: Instant,
    rate_from: u64,
    rate: f32,
}

impl Slot {
    pub fn cancelled(&self) -> bool {
        self.ended || self.session_cancel.as_ref().is_some_and(|flag| flag.load(Ordering::Acquire))
    }
    /// A session whose commands may reach the peer.
    pub fn live(&self) -> bool {
        self.view.phase == "active" && !self.cancelled()
    }
    /// Stops everything this session holds. The slot stays listed (as `closing`) until the engine removes it.
    pub fn revoke(&mut self, message: &str) {
        self.ended = true;
        if let Some(flag) = &self.session_cancel {
            flag.store(true, Ordering::Release);
        }
        if let Some(flag) = &self.consent_cancel {
            flag.store(true, Ordering::Release);
        }
        self.video.clear();
        self.input = None;
        self.drawing = None;
        self.clipboard = None;
        self.view.control = false;
        self.view.clipboard = false;
        self.view.files = false;
        self.view.recording_permitted = false;
        self.view.phase = "closing".into();
        self.view.message = message.into();
    }
    /// A picture count from the media task: keeps the rate and age figures.
    pub fn pictures(&mut self, received: u64) {
        if received != self.view.received {
            self.last_picture = Some(Instant::now());
        }
        self.view.received = received;
        let elapsed = self.rate_at.elapsed();
        if elapsed >= Duration::from_secs(1) {
            self.rate = received.saturating_sub(self.rate_from) as f32 / elapsed.as_secs_f32();
            self.rate_at = Instant::now();
            self.rate_from = received;
        }
    }
    /// A new transport of the same session: counters start again.
    pub fn restarted(&mut self) {
        self.view.received = 0;
        self.rate_from = 0;
        self.rate_at = Instant::now();
        self.rate = 0.0;
        self.last_picture = None;
    }
    pub fn snapshot(&self) -> SessionStatus {
        let mut view = self.view.clone();
        view.quality = Quality {
            pictures: view.received,
            pictures_per_second: if self.last_picture.is_some_and(|at| at.elapsed() < Duration::from_secs(3)) {
                self.rate
            } else {
                0.0
            },
            picture_age_ms: self.last_picture.map(|at| at.elapsed().as_millis() as u64),
        };
        view
    }
}

/// The technician sessions of one registration, oldest first.
#[derive(Default)]
pub(crate) struct Registry {
    slots: Vec<Slot>,
}

impl Registry {
    pub fn len(&self) -> usize {
        self.slots.len()
    }
    /// Starts a session for a request to `device`. Refuses a fifth session, and a second one to the same
    /// device, with a typed error.
    pub fn add(&mut self, device: u32) -> Result<Uuid, String> {
        if self.slots.len() >= MAX_SESSIONS {
            return Err(typed_error(
                TOO_MANY_SESSIONS,
                "You already have 4 sessions open. End one to start another.",
            ));
        }
        if self.slots.iter().any(|slot| slot.view.peer == device) {
            return Err(typed_error(ALREADY_CONNECTED, "You already have a session with this device."));
        }
        let id = Uuid::new_v4();
        self.slots.push(Slot {
            view: SessionStatus {
                id,
                session: None,
                peer: device,
                device,
                phase: "outgoing".into(),
                message: "Checking the remote device identity".into(),
                consent: None,
                received: 0,
                control: false,
                clipboard: false,
                files: false,
                recording_permitted: false,
                recording: None,
                displays: 0,
                display: 0,
                display_all: false,
                print_jobs: Vec::new(),
                host_actions: Vec::new(),
                signin_id: None,
                host_macs: Vec::new(),
                meeting_offer: None,
                peer_features: Vec::new(),
                joined_session: false,
                switched: false,
                quality: Quality::default(),
                started_at: SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |now| now.as_secs()),
            },
            video: Arc::new(VideoMailbox::default()),
            session_cancel: None,
            consent_cancel: None,
            file_permission: None,
            input: None,
            clipboard: None,
            drawing: None,
            renew_after: None,
            ended: false,
            last_picture: None,
            rate_at: Instant::now(),
            rate_from: 0,
            rate: 0.0,
        });
        Ok(id)
    }
    /// By stable id or by the broker's current session id.
    pub fn find(&self, key: Uuid) -> Option<&Slot> {
        self.slots.iter().find(|slot| slot.view.id == key || slot.view.session == Some(key))
    }
    pub fn find_mut(&mut self, key: Uuid) -> Option<&mut Slot> {
        self.slots.iter_mut().find(|slot| slot.view.id == key || slot.view.session == Some(key))
    }
    pub fn by_consent(&self, consent: Uuid) -> Option<&Slot> {
        self.slots.iter().find(|slot| slot.view.consent == Some(consent))
    }
    pub fn contains(&self, key: Uuid) -> bool {
        self.find(key).is_some()
    }
    /// The stable id for any key.
    pub fn stable(&self, key: Uuid) -> Option<Uuid> {
        self.find(key).map(|slot| slot.view.id)
    }
    pub fn remove(&mut self, key: Uuid) -> Option<Slot> {
        let index = self.slots.iter().position(|slot| slot.view.id == key || slot.view.session == Some(key))?;
        Some(self.slots.remove(index))
    }
    pub fn iter(&self) -> impl Iterator<Item = &Slot> {
        self.slots.iter()
    }
    /// Puts a drained session back (it is carried over).
    pub fn restore(&mut self, slot: Slot) {
        self.slots.push(slot);
    }
    pub fn drain(&mut self) -> Vec<Slot> {
        std::mem::take(&mut self.slots)
    }
    #[cfg(test)]
    pub fn views(&self) -> Vec<SessionStatus> {
        self.slots.iter().map(Slot::snapshot).collect()
    }
    /// The session old single-session callers see in the top-level status: the oldest one that runs,
    /// else the oldest.
    pub fn primary(&self) -> Option<&Slot> {
        self.slots.iter().find(|slot| slot.view.phase == "active").or_else(|| self.slots.first())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn four_sessions_fit_and_the_fifth_is_refused_with_a_typed_error() {
        let mut registry = Registry::default();
        let ids: Vec<Uuid> = (0..4).map(|n| registry.add(100_000_001 + n).unwrap()).collect();
        assert_eq!(registry.len(), 4);
        let error = registry.add(100_000_009).unwrap_err();
        let json: serde_json::Value = serde_json::from_str(&error).unwrap();
        assert_eq!(json["hint"], TOO_MANY_SESSIONS);
        assert_eq!(json["max"], 4);
        // Ending one makes room again.
        assert!(registry.remove(ids[1]).is_some());
        assert!(registry.add(100_000_009).is_ok());
    }

    #[test]
    fn a_device_gets_one_session_at_a_time() {
        let mut registry = Registry::default();
        registry.add(123_456_789).unwrap();
        let error = registry.add(123_456_789).unwrap_err();
        assert!(error.contains(ALREADY_CONNECTED));
        assert_eq!(registry.len(), 1);
    }

    #[test]
    fn a_session_is_found_by_its_stable_id_and_by_the_current_broker_id() {
        let mut registry = Registry::default();
        let id = registry.add(111_111_111).unwrap();
        let other = registry.add(222_222_222).unwrap();
        let first = Uuid::new_v4();
        registry.find_mut(id).unwrap().view.session = Some(first);
        assert_eq!(registry.stable(first), Some(id));
        assert_eq!(registry.stable(id), Some(id));
        // A transport renewal changes the broker id; the stable id and the other session stay.
        let renewed = Uuid::new_v4();
        registry.find_mut(first).unwrap().view.session = Some(renewed);
        assert!(registry.find(first).is_none());
        assert_eq!(registry.stable(renewed), Some(id));
        assert_eq!(registry.stable(other), Some(other));
        assert!(registry.find(Uuid::new_v4()).is_none());
    }

    #[test]
    fn ending_one_session_leaves_the_others_untouched() {
        let mut registry = Registry::default();
        let a = registry.add(111_111_111).unwrap();
        let b = registry.add(222_222_222).unwrap();
        let c = registry.add(333_333_333).unwrap();
        for id in [a, b, c] {
            let slot = registry.find_mut(id).unwrap();
            slot.session_cancel = Some(Arc::new(AtomicBool::new(false)));
            slot.view.phase = "active".into();
            slot.view.control = true;
        }
        registry.find_mut(b).unwrap().revoke("Session ended");
        assert!(registry.find(b).unwrap().cancelled());
        assert_eq!(registry.find(b).unwrap().view.phase, "closing");
        assert!(!registry.find(b).unwrap().view.control);
        for id in [a, c] {
            let slot = registry.find(id).unwrap();
            assert!(slot.live(), "the other sessions keep running");
            assert!(slot.view.control);
        }
        registry.remove(b);
        assert_eq!(registry.views().iter().map(|view| view.id).collect::<Vec<_>>(), vec![a, c]);
    }

    #[test]
    fn the_top_level_status_follows_the_oldest_running_session() {
        let mut registry = Registry::default();
        assert!(registry.primary().is_none());
        let a = registry.add(111_111_111).unwrap();
        let b = registry.add(222_222_222).unwrap();
        assert_eq!(registry.primary().unwrap().view.id, a, "none runs yet: the oldest");
        registry.find_mut(b).unwrap().view.phase = "active".into();
        assert_eq!(registry.primary().unwrap().view.id, b);
        registry.find_mut(a).unwrap().view.phase = "active".into();
        assert_eq!(registry.primary().unwrap().view.id, a);
    }

    #[test]
    fn picture_figures_count_per_session() {
        let mut registry = Registry::default();
        let a = registry.add(111_111_111).unwrap();
        let b = registry.add(222_222_222).unwrap();
        assert!(registry.find(a).unwrap().snapshot().quality.picture_age_ms.is_none());
        registry.find_mut(a).unwrap().pictures(30);
        let view = registry.find(a).unwrap().snapshot();
        assert_eq!(view.quality.pictures, 30);
        assert!(view.quality.picture_age_ms.is_some());
        assert_eq!(registry.find(b).unwrap().snapshot().quality.pictures, 0);
        registry.find_mut(a).unwrap().restarted();
        assert_eq!(registry.find(a).unwrap().snapshot().quality.pictures, 0);
    }
}
