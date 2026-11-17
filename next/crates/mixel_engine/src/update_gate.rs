//! When may an update be installed? Never while a session runs on this computer (as technician or as
//! the computer being helped); only after the last session has ended plus a short idle time.
//!
//! The decision is a pure function of "is a session running", "when did the last one end" and the idle
//! time, so it is tested without a session. The running engine feeds it from the status the apps poll
//! (`observe_phase`), which also publishes a small marker file that other programs on the same computer
//! can read (the always-on service on Windows must not replace itself while the app has a session open).
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// How long the computer must stay without a session before an update installs by itself.
pub const IDLE_AFTER_SESSION: Duration = Duration::from_secs(120);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    /// No session now and none for the idle time: install.
    Install,
    /// A session is running: wait for it.
    WaitForSession,
    /// The last session ended recently: wait this many more seconds.
    WaitIdle { seconds_left: u64 },
}

impl Verdict {
    pub fn installs(self) -> bool {
        self == Verdict::Install
    }
    /// The update is ready but held back because of a session.
    pub fn waits(self) -> bool {
        !self.installs()
    }
    pub fn seconds_left(self) -> Option<u64> {
        match self {
            Verdict::WaitIdle { seconds_left } => Some(seconds_left),
            _ => None,
        }
    }
}

/// A phase in which a session (or its set-up, tear-down or reconnection) is in progress. `connecting` is the
/// registration with the connection service and `ready` the idle state: neither is a session.
pub fn phase_busy(phase: &str) -> bool {
    matches!(phase, "incoming" | "outgoing" | "negotiating" | "active" | "closing" | "reconnecting")
}

#[derive(Default)]
pub struct Gate {
    /// The last instant a session was seen running.
    last_busy: Option<Instant>,
}

impl Gate {
    pub const fn new() -> Self {
        Self { last_busy: None }
    }

    /// Records what the engine sees now.
    pub fn observe(&mut self, busy: bool, now: Instant) {
        if busy {
            self.last_busy = Some(now);
        }
    }

    /// `busy_now`: whether a session runs at this moment (from the same status, or another program's marker).
    /// `force`: an administrator chose to install anyway.
    pub fn verdict(&self, busy_now: bool, now: Instant, idle: Duration, force: bool) -> Verdict {
        if force {
            return Verdict::Install;
        }
        if busy_now {
            return Verdict::WaitForSession;
        }
        match self.last_busy {
            Some(last) => {
                let since = now.saturating_duration_since(last);
                if since >= idle {
                    Verdict::Install
                } else {
                    Verdict::WaitIdle { seconds_left: (idle - since).as_secs().max(1) }
                }
            }
            None => Verdict::Install,
        }
    }
}

static GATE: Mutex<Gate> = Mutex::new(Gate::new());

/// Called with the engine's phase whenever it is read (apps poll it; the always-on service reads it every 5 s).
pub fn observe_phase(phase: &str) {
    let busy = phase_busy(phase);
    if let Ok(mut gate) = GATE.lock() {
        gate.observe(busy, Instant::now());
    }
    marker::publish(busy);
}

/// The verdict for the engine's current phase, also counting sessions other programs of this computer report.
pub fn verdict(phase: &str, force: bool) -> Verdict {
    let busy = phase_busy(phase) || marker::others_busy();
    let now = Instant::now();
    match GATE.lock() {
        Ok(mut gate) => {
            gate.observe(phase_busy(phase), now);
            gate.verdict(busy, now, IDLE_AFTER_SESSION, force)
        }
        Err(_) => Verdict::WaitForSession,
    }
}

/// A heartbeat file `session-active.json` in the program's own folder while a session runs. Another program
/// of the same computer (the Windows always-on service) reads it before it replaces itself.
pub mod marker {
    use std::path::PathBuf;
    use std::sync::Mutex;
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

    /// A marker older than this is a program that died while a session was open.
    pub const FRESH: Duration = Duration::from_secs(90);
    const REWRITE_EVERY: Duration = Duration::from_secs(20);

    static LAST: Mutex<Option<(bool, Instant)>> = Mutex::new(None);

    pub fn own_path() -> Option<PathBuf> {
        #[cfg(windows)]
        {
            std::env::var_os("LOCALAPPDATA").map(|base| PathBuf::from(base).join("Mixel Remote").join("session-active.json"))
        }
        #[cfg(not(windows))]
        {
            None
        }
    }

    pub fn is_fresh(modified: SystemTime, now: SystemTime) -> bool {
        now.duration_since(modified).map_or(true, |age| age <= FRESH)
    }

    /// Writes (or removes) this program's marker; rewrites a running one every 20 seconds.
    pub fn publish(busy: bool) {
        let Some(path) = own_path() else { return };
        let Ok(mut last) = LAST.lock() else { return };
        let now = Instant::now();
        match (*last, busy) {
            (Some((true, at)), true) if now.duration_since(at) < REWRITE_EVERY => return,
            (Some((false, _)) | None, false) => {
                if last.is_none() {
                    let _ = std::fs::remove_file(&path);
                }
                *last = Some((false, now));
                return;
            }
            _ => {}
        }
        if busy {
            let seconds = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |time| time.as_secs());
            if let Some(parent) = path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let _ = std::fs::write(&path, format!("{{\"at\":{seconds}}}"));
        } else {
            let _ = std::fs::remove_file(&path);
        }
        *last = Some((busy, now));
    }

    /// Whether another program of this computer reports a running session (Windows: the app of any signed-in user,
    /// read by the always-on service; elsewhere nothing: the app is the only program with sessions).
    pub fn others_busy() -> bool {
        #[cfg(windows)]
        {
            let own = own_path();
            let drive = std::env::var_os("SystemDrive").map_or_else(|| "C:".to_owned(), |value| value.to_string_lossy().into_owned());
            let Ok(users) = std::fs::read_dir(format!("{drive}\\Users")) else { return false };
            let now = SystemTime::now();
            users
                .flatten()
                .map(|user| user.path().join("AppData").join("Local").join("Mixel Remote").join("session-active.json"))
                .filter(|path| Some(path) != own.as_ref())
                .any(|path| std::fs::metadata(&path).and_then(|meta| meta.modified()).is_ok_and(|modified| is_fresh(modified, now)))
        }
        #[cfg(not(windows))]
        {
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::SystemTime;

    fn secs(n: u64) -> Duration {
        Duration::from_secs(n)
    }

    #[test]
    fn busy_phases_are_sessions_and_idle_ones_are_not() {
        for phase in ["incoming", "outgoing", "negotiating", "active", "closing", "reconnecting"] {
            assert!(phase_busy(phase), "{phase}");
        }
        for phase in ["offline", "connecting", "ready", "error", ""] {
            assert!(!phase_busy(phase), "{phase}");
        }
    }

    #[test]
    fn nothing_seen_yet_installs() {
        let gate = Gate::new();
        assert_eq!(gate.verdict(false, Instant::now(), secs(120), false), Verdict::Install);
    }

    #[test]
    fn a_running_session_holds_the_update_back() {
        let start = Instant::now();
        let mut gate = Gate::new();
        gate.observe(true, start);
        assert_eq!(gate.verdict(true, start + secs(3600), secs(120), false), Verdict::WaitForSession);
        assert!(gate.verdict(true, start, secs(120), false).waits());
    }

    #[test]
    fn after_the_last_session_it_waits_the_idle_time_then_installs() {
        let start = Instant::now();
        let mut gate = Gate::new();
        gate.observe(true, start);
        // The session ended; the last time it was seen running was `start`.
        assert_eq!(gate.verdict(false, start + secs(1), secs(120), false), Verdict::WaitIdle { seconds_left: 119 });
        assert_eq!(gate.verdict(false, start + secs(119), secs(120), false).seconds_left(), Some(1));
        assert_eq!(gate.verdict(false, start + secs(120), secs(120), false), Verdict::Install);
        assert_eq!(gate.verdict(false, start + secs(900), secs(120), false), Verdict::Install);
    }

    #[test]
    fn a_new_session_restarts_the_idle_time() {
        let start = Instant::now();
        let mut gate = Gate::new();
        gate.observe(true, start);
        gate.observe(false, start + secs(100));
        gate.observe(true, start + secs(110));
        assert_eq!(gate.verdict(false, start + secs(200), secs(120), false), Verdict::WaitIdle { seconds_left: 30 });
    }

    #[test]
    fn an_administrator_can_force_it() {
        let start = Instant::now();
        let mut gate = Gate::new();
        gate.observe(true, start);
        assert_eq!(gate.verdict(true, start, secs(120), true), Verdict::Install);
    }

    #[test]
    fn a_marker_of_a_dead_program_expires() {
        let now = SystemTime::now();
        assert!(marker::is_fresh(now - secs(30), now));
        assert!(!marker::is_fresh(now - secs(300), now));
        assert!(marker::is_fresh(now + secs(5), now), "a clock step must not hide a running session");
    }

    #[test]
    fn the_global_gate_follows_the_phase() {
        // Uses the process-wide gate: only monotonic facts are asserted.
        observe_phase("active");
        assert!(verdict("active", false).waits());
        assert!(verdict("active", true).installs());
        observe_phase("ready");
        assert!(verdict("ready", false).waits(), "idle time after the session is still running");
    }
}
