//! The technician's workspace (redesign "Pro Console"): the session tabs, which tab is in front, which
//! sessions are in their own window, the connection being prepared, and the two remembered choices
//! ("when a session is ready" and "open sessions in"). A port of the Mac `WorkspaceModel`, as plain
//! data without GTK: the window asks it what to show, and it answers with `Effect`s (open, close or
//! raise a session window) that the window carries out. Unit-tested here.
//!
//! Sessions are keyed by the other computer's Remote ID, not by the engine's session id: the session id
//! changes when the connection is renewed, the computer does not. Everything is a list so several
//! sessions at once only need `observe` to read more than one session from the engine.
use std::collections::BTreeSet;

/// What happens when a session is ready to be shown.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum WhenReady {
    /// Stay where you are; an "Open" notice appears (the default).
    #[default]
    Stay,
    /// Show the session at once.
    Open,
}

impl WhenReady {
    pub fn id(self) -> &'static str {
        match self {
            Self::Stay => "stay",
            Self::Open => "open",
        }
    }
    pub fn from_id(id: &str) -> Self {
        if id == "open" { Self::Open } else { Self::Stay }
    }
}

/// Where a session opens.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum OpenIn {
    #[default]
    Tab,
    Window,
}

impl OpenIn {
    pub fn id(self) -> &'static str {
        match self {
            Self::Tab => "tab",
            Self::Window => "window",
        }
    }
    pub fn from_id(id: &str) -> Self {
        if id == "window" { Self::Window } else { Self::Tab }
    }
}

/// The tab in the title bar: the Devices console, or one session.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Tab {
    #[default]
    Console,
    Session(u32),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TabState {
    /// Accepted; the first picture is not there yet.
    Starting,
    Ready,
    Ending,
}

/// One session tab.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SessionTab {
    /// The other computer's Remote ID.
    pub peer: u32,
    pub name: String,
    pub os: String,
    pub state: TabState,
}

/// A connection being prepared from the Devices list or "Connect by ID".
#[derive(Clone, Debug, PartialEq)]
pub struct Attempt {
    pub peer: u32,
    pub name: String,
    pub os: String,
    /// Seconds on the app's clock when it started.
    pub started: f64,
    /// The step reached: 0 reaching the device, 1 waiting for approval or unattended access,
    /// 2 encrypting end-to-end, 3 starting video, 4 ready.
    pub step: u8,
    pub unattended: bool,
    pub failure: String,
    /// "Open in window instead" was chosen for this connection.
    pub in_window: bool,
}

/// What the window must do after a change.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Effect {
    OpenWindow(u32),
    CloseWindow(u32),
    FocusWindow(u32),
}

/// What the engine reports, reduced to what the workspace reads.
#[derive(Clone, Debug, Default)]
pub struct Observed {
    pub peer: Option<u32>,
    pub sharing: bool,
    pub has_session: bool,
    pub phase: String,
    /// The first picture of the session is shown.
    pub frame_shown: bool,
    /// This app is in a session (any phase from the request to the end).
    pub in_session: bool,
    /// An app action (the connect steps) is still running.
    pub busy: bool,
    /// The app's error line, already in the person's language.
    pub error: String,
    /// The sentence for "the request was sent and the session is gone again", already translated.
    pub declined: String,
    pub name: String,
    pub os: String,
}

#[derive(Clone, Debug, Default)]
pub struct Workspace {
    pub when_ready: WhenReady,
    pub open_in: OpenIn,
    pub selected: Tab,
    pub tabs: Vec<SessionTab>,
    /// Sessions shown in their own window.
    pub windows: BTreeSet<u32>,
    pub attempt: Option<Attempt>,
    /// Sessions that became ready while the person stayed elsewhere: "SRV-DC-02 is ready · Open".
    pub ready_notices: Vec<u32>,
    attempt_window: BTreeSet<u32>,
}

impl Workspace {
    pub fn new(when_ready: WhenReady, open_in: OpenIn) -> Self {
        Self { when_ready, open_in, ..Self::default() }
    }

    /// The sessions this app views as technician. Today the engine runs one session at a time; when
    /// it reports several this is the only place that reads them.
    pub fn session_tabs(observed: &Observed) -> Vec<SessionTab> {
        let Some(peer) = observed.peer else { return Vec::new() };
        if observed.sharing || !observed.has_session {
            return Vec::new();
        }
        let state = match observed.phase.as_str() {
            "active" if observed.frame_shown => TabState::Ready,
            "active" => TabState::Starting,
            "closing" => TabState::Ending,
            // "outgoing" and "negotiating" are the connecting card; a tab exists once the other side
            // accepted (the picture is coming).
            _ => return Vec::new(),
        };
        vec![SessionTab { peer, name: observed.name.clone(), os: observed.os.clone(), state }]
    }

    /// Called when the remote status or the first picture changes.
    pub fn refresh(&mut self, observed: &Observed) -> Vec<Effect> {
        let mut effects = Vec::new();
        let next = Self::session_tabs(observed);
        let before = std::mem::replace(&mut self.tabs, next.clone());
        // Ended sessions: their tab, window and notice go away; the Devices list comes back.
        for old in &before {
            if next.iter().any(|tab| tab.peer == old.peer) {
                continue;
            }
            if self.windows.remove(&old.peer) {
                effects.push(Effect::CloseWindow(old.peer));
            }
            self.ready_notices.retain(|peer| *peer != old.peer);
            if self.selected == Tab::Session(old.peer) {
                self.selected = Tab::Console;
            }
        }
        self.update_attempt(observed);
        for tab in &next {
            let was_ready = before.iter().any(|old| old.peer == tab.peer && old.state == TabState::Ready);
            if tab.state == TabState::Ready && !was_ready {
                self.became_ready(tab.peer, &mut effects);
            }
        }
        effects
    }

    fn update_attempt(&mut self, observed: &Observed) {
        let Some(mut current) = self.attempt.clone() else { return };
        let matches = observed.peer == Some(current.peer);
        let mut step = current.step;
        if matches {
            match observed.phase.as_str() {
                "outgoing" => step = step.max(1),
                "negotiating" => step = step.max(2),
                "active" => step = step.max(if observed.frame_shown { 4 } else { 3 }),
                _ => {}
            }
        }
        if step == 4 {
            // Ready: the notice (or the session itself) takes over from the card.
            self.attempt = None;
            return;
        }
        let running = matches && observed.in_session;
        if current.failure.is_empty() && !observed.busy && !running {
            if !observed.error.is_empty() {
                current.failure = observed.error.clone();
            } else if step >= 1 {
                // The request was sent and the session is gone again: declined, timed out or refused.
                current.failure = observed.declined.clone();
            }
        }
        current.step = step;
        self.attempt = Some(current);
    }

    fn became_ready(&mut self, peer: u32, effects: &mut Vec<Effect>) {
        let wanted_window = self.attempt_window.remove(&peer);
        if wanted_window {
            self.show_into(peer, Some(true), effects);
        } else if self.when_ready == WhenReady::Open {
            self.show_into(peer, None, effects);
        } else if self.selected != Tab::Session(peer) && !self.windows.contains(&peer) && !self.ready_notices.contains(&peer) {
            self.ready_notices.push(peer);
        }
    }

    // ---- Actions ----

    /// Connect from the Devices list or by ID; the person stays in Devices. `busy`: the previous step
    /// is still running.
    pub fn connect(&mut self, peer: u32, name: &str, os: &str, unattended: bool, started: f64, busy_text: Option<&str>) {
        self.attempt_window.remove(&peer);
        let mut next = Attempt { peer, name: name.to_owned(), os: os.to_owned(), started, step: 0, unattended, failure: String::new(), in_window: false };
        if let Some(text) = busy_text {
            next.failure = text.to_owned();
        }
        self.attempt = Some(next);
    }

    /// "Open in window instead" while connecting.
    pub fn open_attempt_in_window(&mut self) {
        if let Some(current) = self.attempt.as_mut() {
            current.in_window = true;
            self.attempt_window.insert(current.peer);
        }
    }

    /// Cancel while connecting, or close a failed attempt. Returns the Remote ID whose request must be
    /// ended with the engine (when it is in a session).
    pub fn cancel_attempt(&mut self, in_session_with: Option<u32>) -> Option<u32> {
        let current = self.attempt.take()?;
        self.attempt_window.remove(&current.peer);
        (in_session_with == Some(current.peer)).then_some(current.peer)
    }

    pub fn dismiss_notice(&mut self, peer: u32) {
        self.ready_notices.retain(|noticed| *noticed != peer);
    }

    /// Shows a session: in its window if it has one, else as the chosen default (tab or own window).
    pub fn show(&mut self, peer: u32, in_window: Option<bool>) -> Vec<Effect> {
        let mut effects = Vec::new();
        self.show_into(peer, in_window, &mut effects);
        effects
    }

    fn show_into(&mut self, peer: u32, in_window: Option<bool>, effects: &mut Vec<Effect>) {
        if !self.has_tab(peer) {
            return;
        }
        self.ready_notices.retain(|noticed| *noticed != peer);
        if self.windows.contains(&peer) {
            effects.push(Effect::FocusWindow(peer));
            return;
        }
        if in_window.unwrap_or(self.open_in == OpenIn::Window) {
            self.pop_out_into(peer, effects);
        } else {
            self.selected = Tab::Session(peer);
        }
    }

    /// Moves the session from its tab into its own window.
    pub fn pop_out(&mut self, peer: u32) -> Vec<Effect> {
        let mut effects = Vec::new();
        if self.has_tab(peer) {
            self.pop_out_into(peer, &mut effects);
        }
        effects
    }

    fn pop_out_into(&mut self, peer: u32, effects: &mut Vec<Effect>) {
        self.ready_notices.retain(|noticed| *noticed != peer);
        if self.selected == Tab::Session(peer) {
            self.selected = Tab::Console;
        }
        effects.push(if self.windows.insert(peer) { Effect::OpenWindow(peer) } else { Effect::FocusWindow(peer) });
    }

    /// Puts the session's window back into its tab (also when its window is closed by the person:
    /// then `close_window` is false, the window is gone already).
    pub fn dock(&mut self, peer: u32, select: bool, close_window: bool) -> Vec<Effect> {
        let mut effects = Vec::new();
        if !self.windows.remove(&peer) {
            return effects;
        }
        if close_window {
            effects.push(Effect::CloseWindow(peer));
        }
        if select && self.has_tab(peer) {
            self.selected = Tab::Session(peer);
        }
        effects
    }

    /// The session window was closed by the person: it goes back into its tab.
    pub fn window_closed(&mut self, peer: u32) {
        self.dock(peer, true, false);
    }

    pub fn has_tab(&self, peer: u32) -> bool {
        self.tabs.iter().any(|tab| tab.peer == peer)
    }
    pub fn tab(&self, peer: u32) -> Option<&SessionTab> {
        self.tabs.iter().find(|tab| tab.peer == peer)
    }
    pub fn is_in_window(&self, peer: u32) -> bool {
        self.windows.contains(&peer)
    }

    /// `connecting` or a tab state of the device with this Remote ID, for the table row.
    pub fn state_of(&self, peer: u32) -> Option<&'static str> {
        if let Some(tab) = self.tab(peer) {
            return Some(match tab.state {
                TabState::Starting => "starting",
                TabState::Ready => "ready",
                TabState::Ending => "ending",
            });
        }
        self.attempt.as_ref().filter(|attempt| attempt.peer == peer && attempt.failure.is_empty()).map(|_| "connecting")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PEER: u32 = 731_204_118;

    fn viewing(frame: bool) -> Observed {
        Observed { peer: Some(PEER), has_session: true, phase: "active".into(), frame_shown: frame, in_session: true, name: "Reception PC".into(), os: "windows".into(), ..Observed::default() }
    }
    fn ended() -> Observed {
        Observed { phase: "ready".into(), ..Observed::default() }
    }
    fn ready(when: WhenReady, open_in: OpenIn) -> (Workspace, Vec<Effect>) {
        let mut ws = Workspace::new(when, open_in);
        let effects = ws.refresh(&viewing(true));
        (ws, effects)
    }

    #[test]
    fn ready_session_becomes_a_tab_and_stays_in_devices_with_a_notice() {
        let (ws, effects) = ready(WhenReady::Stay, OpenIn::Tab);
        assert_eq!(ws.tabs.iter().map(|tab| tab.peer).collect::<Vec<_>>(), vec![PEER]);
        assert_eq!(ws.tabs[0].state, TabState::Ready);
        assert_eq!(ws.selected, Tab::Console, "Connect must not jump away from Devices");
        assert_eq!(ws.ready_notices, vec![PEER]);
        assert!(effects.is_empty());
    }

    #[test]
    fn switch_to_session_setting_opens_the_tab() {
        let (ws, _) = ready(WhenReady::Open, OpenIn::Tab);
        assert_eq!(ws.selected, Tab::Session(PEER));
        assert!(ws.ready_notices.is_empty());
    }

    #[test]
    fn a_session_without_picture_is_starting_and_has_no_notice_yet() {
        let mut ws = Workspace::new(WhenReady::Stay, OpenIn::Tab);
        ws.refresh(&viewing(false));
        assert_eq!(ws.tabs[0].state, TabState::Starting);
        assert!(ws.ready_notices.is_empty());
        ws.refresh(&viewing(true));
        assert_eq!(ws.ready_notices, vec![PEER]);
        // The same state again does not add a second notice.
        ws.refresh(&viewing(true));
        assert_eq!(ws.ready_notices, vec![PEER]);
    }

    #[test]
    fn connecting_and_hosting_have_no_tab() {
        let mut observed = viewing(true);
        observed.phase = "outgoing".into();
        assert!(Workspace::session_tabs(&observed).is_empty());
        observed.phase = "negotiating".into();
        assert!(Workspace::session_tabs(&observed).is_empty());
        observed.phase = "active".into();
        observed.sharing = true;
        assert!(Workspace::session_tabs(&observed).is_empty(), "this computer is being viewed");
        observed.sharing = false;
        observed.phase = "closing".into();
        assert_eq!(Workspace::session_tabs(&observed)[0].state, TabState::Ending);
    }

    #[test]
    fn pop_out_and_dock() {
        let (mut ws, _) = ready(WhenReady::Open, OpenIn::Tab);
        assert_eq!(ws.pop_out(PEER), vec![Effect::OpenWindow(PEER)]);
        assert!(ws.is_in_window(PEER));
        assert_eq!(ws.selected, Tab::Console);
        assert_eq!(ws.dock(PEER, true, true), vec![Effect::CloseWindow(PEER)]);
        assert!(!ws.is_in_window(PEER));
        assert_eq!(ws.selected, Tab::Session(PEER));
        // Docking something that is not in a window does nothing.
        assert!(ws.dock(PEER, true, true).is_empty());
    }

    #[test]
    fn closing_the_window_docks_it() {
        let (mut ws, _) = ready(WhenReady::Stay, OpenIn::Window);
        assert_eq!(ws.show(PEER, None), vec![Effect::OpenWindow(PEER)], "default own window");
        assert!(ws.is_in_window(PEER));
        ws.window_closed(PEER);
        assert!(!ws.is_in_window(PEER));
        assert_eq!(ws.selected, Tab::Session(PEER));
    }

    #[test]
    fn showing_a_session_that_has_a_window_raises_it() {
        let (mut ws, _) = ready(WhenReady::Stay, OpenIn::Tab);
        ws.pop_out(PEER);
        assert_eq!(ws.show(PEER, None), vec![Effect::FocusWindow(PEER)]);
        assert_eq!(ws.pop_out(PEER), vec![Effect::FocusWindow(PEER)]);
    }

    #[test]
    fn session_end_removes_tab_window_and_notice() {
        let (mut ws, _) = ready(WhenReady::Open, OpenIn::Tab);
        ws.pop_out(PEER);
        let effects = ws.refresh(&ended());
        assert!(ws.tabs.is_empty());
        assert!(!ws.is_in_window(PEER));
        assert_eq!(ws.selected, Tab::Console);
        assert_eq!(effects, vec![Effect::CloseWindow(PEER)]);

        let (mut ws, _) = ready(WhenReady::Stay, OpenIn::Tab);
        assert_eq!(ws.ready_notices, vec![PEER]);
        ws.refresh(&ended());
        assert!(ws.ready_notices.is_empty());
    }

    #[test]
    fn open_in_window_instead_wins_over_the_settings() {
        let mut ws = Workspace::new(WhenReady::Stay, OpenIn::Tab);
        ws.connect(PEER, "Reception PC", "windows", true, 0.0, None);
        ws.open_attempt_in_window();
        assert!(ws.attempt.as_ref().unwrap().in_window);
        let mut observed = viewing(true);
        observed.phase = "outgoing".into();
        ws.refresh(&observed);
        let effects = ws.refresh(&viewing(true));
        assert_eq!(effects, vec![Effect::OpenWindow(PEER)]);
        assert!(ws.attempt.is_none(), "the card goes away when the session is ready");
        assert!(ws.ready_notices.is_empty());
    }

    #[test]
    fn attempt_steps_follow_the_phase_and_never_go_back() {
        let mut ws = Workspace::new(WhenReady::Stay, OpenIn::Tab);
        ws.connect(PEER, "Reception PC", "windows", false, 1.0, None);
        let mut observed = Observed { peer: Some(PEER), has_session: true, in_session: true, phase: "outgoing".into(), busy: true, ..Observed::default() };
        ws.refresh(&observed);
        assert_eq!(ws.attempt.as_ref().unwrap().step, 1);
        observed.phase = "negotiating".into();
        ws.refresh(&observed);
        assert_eq!(ws.attempt.as_ref().unwrap().step, 2);
        observed.phase = "outgoing".into();
        ws.refresh(&observed);
        assert_eq!(ws.attempt.as_ref().unwrap().step, 2);
        observed.phase = "active".into();
        ws.refresh(&observed);
        assert_eq!(ws.attempt.as_ref().unwrap().step, 3);
        assert_eq!(ws.state_of(PEER), Some("starting"));
    }

    #[test]
    fn a_failed_attempt_keeps_its_reason_until_closed() {
        let mut ws = Workspace::new(WhenReady::Stay, OpenIn::Tab);
        ws.connect(PEER, "Reception PC", "windows", false, 1.0, None);
        // The connect steps stopped with an error.
        let observed = Observed { phase: "ready".into(), error: "Could not reach".into(), ..Observed::default() };
        ws.refresh(&observed);
        assert_eq!(ws.attempt.as_ref().unwrap().failure, "Could not reach");
        assert_eq!(ws.state_of(PEER), None);
        assert_eq!(ws.cancel_attempt(None), None);
        assert!(ws.attempt.is_none());

        // Declined after the request was sent.
        ws.connect(PEER, "Reception PC", "windows", false, 1.0, None);
        let mut observed = Observed { peer: Some(PEER), has_session: true, in_session: true, phase: "outgoing".into(), ..Observed::default() };
        ws.refresh(&observed);
        observed = Observed { phase: "ready".into(), declined: "Declined".into(), ..Observed::default() };
        ws.refresh(&observed);
        assert_eq!(ws.attempt.as_ref().unwrap().failure, "Declined");
    }

    #[test]
    fn a_busy_app_refuses_a_second_attempt_and_cancel_ends_a_running_request() {
        let mut ws = Workspace::new(WhenReady::Stay, OpenIn::Tab);
        ws.connect(PEER, "x", "linux", false, 0.0, Some("Please wait"));
        assert_eq!(ws.attempt.as_ref().unwrap().failure, "Please wait");
        ws.connect(PEER, "x", "linux", false, 0.0, None);
        assert_eq!(ws.cancel_attempt(Some(PEER)), Some(PEER), "the running request must be ended");
        ws.connect(PEER, "x", "linux", false, 0.0, None);
        assert_eq!(ws.cancel_attempt(Some(1)), None);
    }

    #[test]
    fn choices_are_remembered_as_ids() {
        for when in [WhenReady::Stay, WhenReady::Open] {
            assert_eq!(WhenReady::from_id(when.id()), when);
        }
        for place in [OpenIn::Tab, OpenIn::Window] {
            assert_eq!(OpenIn::from_id(place.id()), place);
        }
        assert_eq!(WhenReady::from_id("garbage"), WhenReady::Stay);
        assert_eq!(OpenIn::from_id(""), OpenIn::Tab);
    }
}
