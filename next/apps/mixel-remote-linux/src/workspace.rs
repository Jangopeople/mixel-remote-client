//! The technician's workspace (redesign "Pro Console"): the session tabs, which tab is in front, which
//! sessions are in their own window, the connection being prepared, and the two remembered choices
//! ("when a session is ready" and "open sessions in"). A port of the Mac `WorkspaceModel`, as plain
//! data without GTK: the window asks it what to show, and it answers with `Effect`s (open, close or
//! raise a session window) that the window carries out. Unit-tested here.
//!
//! Tabs and windows are keyed by the other computer's Remote ID (one session per device, so it is unique)
//! and carry the session's stable id (`SessionTab::id`), which every engine command uses. A transport
//! renewal changes neither, so the tab stays. One tab (or window) per session the engine reports, up to
//! the engine's limit; each ends alone.
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
        if id == "window" {
            Self::Window
        } else {
            Self::Tab
        }
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
    /// The other computer is gone and the engine asks again (about a minute); the tab stays.
    Reconnecting,
    /// It did not come back: the tab stays with a Retry until the session is ended.
    ReconnectFailed,
}

/// One session tab.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SessionTab {
    /// The session's stable id (the key of every engine command for it).
    pub id: String,
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
    Open(u32),
    Close(u32),
    Focus(u32),
}

/// One technician session as the workspace reads it.
#[derive(Clone, Debug, Default)]
pub struct ObservedSession {
    /// The stable id.
    pub id: String,
    pub peer: u32,
    pub phase: String,
    /// The first picture of this session is shown.
    pub frame_shown: bool,
    pub name: String,
    pub os: String,
    pub reconnecting: bool,
    pub reconnect_failed: bool,
}

/// What the engine reports, reduced to what the workspace reads.
#[derive(Clone, Debug, Default)]
pub struct Observed {
    /// Every technician session of this app, oldest first (any phase from the request to the end).
    pub sessions: Vec<ObservedSession>,
    /// An app action (the connect steps) is still running.
    pub busy: bool,
    /// The app's error line, already in the person's language.
    pub error: String,
    /// The sentence for "the request was sent and the session is gone again", already translated.
    pub declined: String,
}

impl Observed {
    pub fn session_of(&self, peer: u32) -> Option<&ObservedSession> {
        self.sessions.iter().find(|session| session.peer == peer)
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
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
        Self {
            when_ready,
            open_in,
            ..Self::default()
        }
    }

    /// The tabs of the sessions this app views as technician: one per session the engine reports.
    pub fn session_tabs(observed: &Observed) -> Vec<SessionTab> {
        observed
            .sessions
            .iter()
            .filter_map(|session| {
                let state = match session.phase.as_str() {
                    // The computer is gone: the session stays listed with phase "outgoing".
                    "closing" => TabState::Ending,
                    _ if session.reconnect_failed => TabState::ReconnectFailed,
                    _ if session.reconnecting => TabState::Reconnecting,
                    "active" if session.frame_shown => TabState::Ready,
                    "active" => TabState::Starting,
                    // "outgoing" and "negotiating" are the connecting card; a tab exists once the other
                    // side accepted (the picture is coming).
                    _ => return None,
                };
                Some(SessionTab { id: session.id.clone(), peer: session.peer, name: session.name.clone(), os: session.os.clone(), state })
            })
            .collect()
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
                effects.push(Effect::Close(old.peer));
            }
            self.ready_notices.retain(|peer| *peer != old.peer);
            if self.selected == Tab::Session(old.peer) {
                self.selected = Tab::Console;
            }
        }
        self.update_attempt(observed);
        for tab in &next {
            // A session that lost its computer and got it back was ready before: it is not "new".
            let was_ready = before.iter().any(|old| {
                old.peer == tab.peer && matches!(old.state, TabState::Ready | TabState::Reconnecting | TabState::ReconnectFailed)
            });
            if tab.state == TabState::Ready && !was_ready {
                self.became_ready(tab.peer, &mut effects);
            }
        }
        effects
    }

    fn update_attempt(&mut self, observed: &Observed) {
        let Some(mut current) = self.attempt.clone() else {
            return;
        };
        let session = observed.session_of(current.peer);
        let mut step = current.step;
        if let Some(session) = session {
            match session.phase.as_str() {
                "outgoing" => step = step.max(1),
                "negotiating" => step = step.max(2),
                "active" => step = step.max(if session.frame_shown { 4 } else { 3 }),
                _ => {}
            }
        }
        if step == 4 {
            // Ready: the notice (or the session itself) takes over from the card.
            self.attempt = None;
            return;
        }
        let running = session.is_some();
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
        } else if self.selected != Tab::Session(peer)
            && !self.windows.contains(&peer)
            && !self.ready_notices.contains(&peer)
        {
            self.ready_notices.push(peer);
        }
    }

    // ---- Actions ----

    /// Connect from the Devices list or by ID; the person stays in Devices. `busy`: the previous step
    /// is still running.
    pub fn connect(
        &mut self,
        peer: u32,
        name: &str,
        os: &str,
        unattended: bool,
        started: f64,
        busy_text: Option<&str>,
    ) {
        self.attempt_window.remove(&peer);
        let mut next = Attempt {
            peer,
            name: name.to_owned(),
            os: os.to_owned(),
            started,
            step: 0,
            unattended,
            failure: String::new(),
            in_window: false,
        };
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
            effects.push(Effect::Focus(peer));
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
        effects.push(if self.windows.insert(peer) {
            Effect::Open(peer)
        } else {
            Effect::Focus(peer)
        });
    }

    /// Puts the session's window back into its tab (also when its window is closed by the person:
    /// then `close_window` is false, the window is gone already).
    pub fn dock(&mut self, peer: u32, select: bool, close_window: bool) -> Vec<Effect> {
        let mut effects = Vec::new();
        if !self.windows.remove(&peer) {
            return effects;
        }
        if close_window {
            effects.push(Effect::Close(peer));
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

    /// The stable id of the session shown for this Remote ID.
    pub fn id_of(&self, peer: u32) -> Option<&str> {
        self.tab(peer).map(|tab| tab.id.as_str())
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
                TabState::Reconnecting => "reconnecting",
                TabState::ReconnectFailed => "reconnect_failed",
            });
        }
        self.attempt
            .as_ref()
            .filter(|attempt| attempt.peer == peer && attempt.failure.is_empty())
            .map(|_| "connecting")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PEER: u32 = 731_204_118;
    const OTHER: u32 = 205_118_340;

    fn session(id: &str, peer: u32, phase: &str, frame: bool) -> ObservedSession {
        ObservedSession { id: id.into(), peer, phase: phase.into(), frame_shown: frame, name: format!("PC {peer}"), os: "windows".into(), ..ObservedSession::default() }
    }
    fn observe(sessions: Vec<ObservedSession>) -> Observed {
        Observed { sessions, ..Observed::default() }
    }
    fn viewing(frame: bool) -> Observed {
        observe(vec![session("s-1", PEER, "active", frame)])
    }
    fn ended() -> Observed {
        Observed::default()
    }
    fn ready(when: WhenReady, open_in: OpenIn) -> (Workspace, Vec<Effect>) {
        let mut ws = Workspace::new(when, open_in);
        let effects = ws.refresh(&viewing(true));
        (ws, effects)
    }
    fn many(count: u32) -> Observed {
        observe((0..count).map(|n| session(&format!("s-{n}"), PEER + n, "active", true)).collect())
    }

    #[test]
    fn ready_session_becomes_a_tab_and_stays_in_devices_with_a_notice() {
        let (ws, effects) = ready(WhenReady::Stay, OpenIn::Tab);
        assert_eq!(ws.tabs.iter().map(|tab| tab.peer).collect::<Vec<_>>(), vec![PEER]);
        assert_eq!(ws.tabs[0].state, TabState::Ready);
        assert_eq!(ws.tabs[0].id, "s-1", "the tab is bound to the stable id");
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
    fn connecting_has_no_tab_and_a_closing_session_is_ending() {
        for phase in ["outgoing", "negotiating"] {
            assert!(Workspace::session_tabs(&observe(vec![session("s-1", PEER, phase, true)])).is_empty());
        }
        let tabs = Workspace::session_tabs(&observe(vec![session("s-1", PEER, "closing", true)]));
        assert_eq!(tabs[0].state, TabState::Ending);
    }

    #[test]
    fn two_to_four_sessions_give_two_to_four_tabs() {
        for count in 2..=4u32 {
            let mut ws = Workspace::new(WhenReady::Stay, OpenIn::Tab);
            ws.refresh(&many(count));
            assert_eq!(ws.tabs.len(), count as usize);
            let ids: Vec<&str> = ws.tabs.iter().map(|tab| tab.id.as_str()).collect();
            assert_eq!(ids, (0..count).map(|n| format!("s-{n}")).collect::<Vec<_>>());
            assert_eq!(ws.ready_notices.len(), count as usize, "each ready session has its own notice");
        }
    }

    #[test]
    fn a_session_that_connects_does_not_disturb_the_others() {
        let mut ws = Workspace::new(WhenReady::Stay, OpenIn::Tab);
        ws.refresh(&viewing(true));
        ws.selected = Tab::Session(PEER);
        let mut next = viewing(true);
        next.sessions.push(session("s-2", OTHER, "outgoing", false));
        ws.refresh(&next);
        assert_eq!(ws.tabs.len(), 1, "the second one is still connecting");
        assert_eq!(ws.selected, Tab::Session(PEER));
        next.sessions[1].phase = "active".into();
        next.sessions[1].frame_shown = true;
        ws.refresh(&next);
        assert_eq!(ws.tabs.len(), 2);
        assert_eq!(ws.selected, Tab::Session(PEER), "the new tab does not take the focus away");
        assert!(ws.ready_notices.contains(&OTHER), "the new session announces itself with a notice");
    }

    #[test]
    fn one_session_ending_removes_only_its_tab_window_and_notice() {
        let mut ws = Workspace::new(WhenReady::Open, OpenIn::Tab);
        ws.refresh(&many(3));
        ws.pop_out(PEER + 1);
        ws.selected = Tab::Session(PEER + 1);
        ws.selected = Tab::Session(PEER + 2);
        ws.pop_out(PEER + 2);
        let mut next = many(3);
        next.sessions.remove(1);
        let effects = ws.refresh(&next);
        assert_eq!(ws.tabs.iter().map(|tab| tab.peer).collect::<Vec<_>>(), vec![PEER, PEER + 2]);
        assert_eq!(effects, vec![Effect::Close(PEER + 1)]);
        assert!(ws.is_in_window(PEER + 2), "the other window stays");
        assert!(!ws.is_in_window(PEER + 1));
        // The one in front ends: back to Devices.
        ws.selected = Tab::Session(PEER);
        let mut last = many(3);
        last.sessions.remove(0);
        last.sessions.remove(0);
        ws.refresh(&last);
        assert_eq!(ws.selected, Tab::Console);
        assert_eq!(ws.tabs.len(), 1);
    }

    #[test]
    fn a_transport_renewal_keeps_the_tab_the_window_and_the_selection() {
        let (mut ws, _) = ready(WhenReady::Open, OpenIn::Tab);
        ws.pop_out(PEER);
        let before = ws.clone();
        // Same stable id, new transport: the engine reports the same item (only its `session` changed).
        ws.refresh(&viewing(true));
        assert_eq!(ws, before);
        assert!(ws.is_in_window(PEER));
        // An old engine renews the only id it has: the tab (keyed by the device) stays, with the new id.
        ws.refresh(&observe(vec![session("s-renewed", PEER, "active", true)]));
        assert_eq!(ws.tabs.len(), 1);
        assert_eq!(ws.tabs[0].id, "s-renewed");
        assert!(ws.is_in_window(PEER));
    }

    #[test]
    fn pop_out_and_dock() {
        let (mut ws, _) = ready(WhenReady::Open, OpenIn::Tab);
        assert_eq!(ws.pop_out(PEER), vec![Effect::Open(PEER)]);
        assert!(ws.is_in_window(PEER));
        assert_eq!(ws.selected, Tab::Console);
        assert_eq!(ws.dock(PEER, true, true), vec![Effect::Close(PEER)]);
        assert!(!ws.is_in_window(PEER));
        assert_eq!(ws.selected, Tab::Session(PEER));
        // Docking something that is not in a window does nothing.
        assert!(ws.dock(PEER, true, true).is_empty());
    }

    #[test]
    fn a_session_that_lost_its_computer_keeps_its_tab_window_and_selection() {
        let (mut ws, _) = ready(WhenReady::Open, OpenIn::Window);
        assert!(ws.is_in_window(PEER));
        let lost = |reconnecting: bool, failed: bool| {
            observe(vec![ObservedSession { reconnecting, reconnect_failed: failed, ..session("s-1", PEER, "outgoing", true) }])
        };
        let effects = ws.refresh(&lost(true, false));
        assert!(effects.is_empty(), "nothing closes while it retries");
        assert_eq!(ws.tab(PEER).map(|tab| tab.state), Some(TabState::Reconnecting));
        assert!(ws.is_in_window(PEER));
        assert_eq!(ws.state_of(PEER), Some("reconnecting"));
        ws.refresh(&lost(false, true));
        assert_eq!(ws.tab(PEER).map(|tab| tab.state), Some(TabState::ReconnectFailed));
        assert!(ws.is_in_window(PEER));
        // It comes back (Retry worked): same tab and window, no "ready" notice or second window.
        let effects = ws.refresh(&viewing(true));
        assert!(effects.is_empty());
        assert!(ws.ready_notices.is_empty() && ws.is_in_window(PEER));
        assert_eq!(ws.tab(PEER).map(|tab| tab.state), Some(TabState::Ready));
        // Ending it (End works in every one of these states) removes tab and window.
        ws.refresh(&lost(true, false));
        assert_eq!(ws.refresh(&ended()), vec![Effect::Close(PEER)]);
        assert!(ws.tabs.is_empty());
    }

    #[test]
    fn a_window_that_is_gone_already_docks_without_closing_anything() {
        let (mut ws, _) = ready(WhenReady::Stay, OpenIn::Window);
        assert_eq!(ws.show(PEER, None), vec![Effect::Open(PEER)], "default own window");
        assert!(ws.is_in_window(PEER));
        ws.window_closed(PEER);
        assert!(!ws.is_in_window(PEER));
        assert_eq!(ws.selected, Tab::Session(PEER));
    }

    #[test]
    fn showing_a_session_that_has_a_window_raises_it() {
        let (mut ws, _) = ready(WhenReady::Stay, OpenIn::Tab);
        ws.pop_out(PEER);
        assert_eq!(ws.show(PEER, None), vec![Effect::Focus(PEER)]);
        assert_eq!(ws.pop_out(PEER), vec![Effect::Focus(PEER)]);
    }

    #[test]
    fn session_end_removes_tab_window_and_notice() {
        let (mut ws, _) = ready(WhenReady::Open, OpenIn::Tab);
        ws.pop_out(PEER);
        let effects = ws.refresh(&ended());
        assert!(ws.tabs.is_empty());
        assert!(!ws.is_in_window(PEER));
        assert_eq!(ws.selected, Tab::Console);
        assert_eq!(effects, vec![Effect::Close(PEER)]);

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
        ws.refresh(&observe(vec![session("s-1", PEER, "outgoing", false)]));
        let effects = ws.refresh(&viewing(true));
        assert_eq!(effects, vec![Effect::Open(PEER)]);
        assert!(ws.attempt.is_none(), "the card goes away when the session is ready");
        assert!(ws.ready_notices.is_empty());
    }

    #[test]
    fn attempt_steps_follow_the_phase_and_never_go_back() {
        let mut ws = Workspace::new(WhenReady::Stay, OpenIn::Tab);
        ws.connect(PEER, "Reception PC", "windows", false, 1.0, None);
        let mut observed = observe(vec![session("s-1", PEER, "outgoing", false)]);
        observed.busy = true;
        ws.refresh(&observed);
        assert_eq!(ws.attempt.as_ref().unwrap().step, 1);
        observed.sessions[0].phase = "negotiating".into();
        ws.refresh(&observed);
        assert_eq!(ws.attempt.as_ref().unwrap().step, 2);
        observed.sessions[0].phase = "outgoing".into();
        ws.refresh(&observed);
        assert_eq!(ws.attempt.as_ref().unwrap().step, 2);
        observed.sessions[0].phase = "active".into();
        ws.refresh(&observed);
        assert_eq!(ws.attempt.as_ref().unwrap().step, 3);
        assert_eq!(ws.state_of(PEER), Some("starting"));
    }

    #[test]
    fn the_attempt_follows_its_own_device_among_several_sessions() {
        let mut ws = Workspace::new(WhenReady::Stay, OpenIn::Tab);
        ws.refresh(&viewing(true));
        ws.connect(OTHER, "Server", "linux", false, 1.0, None);
        let mut observed = viewing(true);
        observed.sessions.push(session("s-2", OTHER, "negotiating", false));
        ws.refresh(&observed);
        assert_eq!(ws.attempt.as_ref().unwrap().step, 2, "the first computer's session does not advance it");
        assert_eq!(ws.tabs.len(), 1);
    }

    #[test]
    fn a_failed_attempt_keeps_its_reason_until_closed() {
        let mut ws = Workspace::new(WhenReady::Stay, OpenIn::Tab);
        ws.connect(PEER, "Reception PC", "windows", false, 1.0, None);
        // The connect steps stopped with an error.
        let observed = Observed { error: "Could not reach".into(), ..Observed::default() };
        ws.refresh(&observed);
        assert_eq!(ws.attempt.as_ref().unwrap().failure, "Could not reach");
        assert_eq!(ws.state_of(PEER), None);
        assert_eq!(ws.cancel_attempt(None), None);
        assert!(ws.attempt.is_none());

        // Declined after the request was sent.
        ws.connect(PEER, "Reception PC", "windows", false, 1.0, None);
        ws.refresh(&observe(vec![session("s-1", PEER, "outgoing", false)]));
        ws.refresh(&Observed { declined: "Declined".into(), ..Observed::default() });
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
