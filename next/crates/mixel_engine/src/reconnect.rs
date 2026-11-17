//! The technician's window stays open when the computer being helped goes away (update install, crash, service
//! restart): the same device is asked again with quick retries for up to a minute. Only access that needs nobody's
//! answer is retried: an unattended session or a support password that is still valid. An attended session asks the
//! person at the computer again, which this never does by itself.
use signaling_server::protocol::SupportCode;
use std::time::{Duration, Instant};

/// How long the same device is asked again.
pub const WINDOW: Duration = Duration::from_secs(60);
/// Pause between two asks.
pub const EVERY: Duration = Duration::from_secs(2);

#[derive(Clone)]
pub struct Access {
    pub host: u32,
    pub unattended: bool,
    pub password: Option<SupportCode>,
}

impl Access {
    /// Whether this access can be asked for again without anybody at the other computer answering.
    pub fn repeatable(&self) -> bool {
        self.unattended || self.password.is_some()
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Step {
    /// Nothing to do yet (an ask is in flight or the pause is not over).
    Wait,
    /// Ask the device again.
    Ask,
    /// A minute passed: "Could not reconnect".
    GiveUp,
}

pub struct Retry {
    access: Access,
    started: Instant,
    last_ask: Option<Instant>,
}

impl Retry {
    /// `None` when the session cannot be repeated without an answer from the person at the computer.
    pub fn start(access: Access, now: Instant) -> Option<Self> {
        access.repeatable().then_some(Self { access, started: now, last_ask: None })
    }
    pub fn access(&self) -> &Access {
        &self.access
    }
    pub fn step(&mut self, now: Instant, ask_in_flight: bool) -> Step {
        if now.saturating_duration_since(self.started) >= WINDOW {
            return Step::GiveUp;
        }
        if ask_in_flight || self.last_ask.is_some_and(|at| now.saturating_duration_since(at) < EVERY) {
            return Step::Wait;
        }
        self.last_ask = Some(now);
        Step::Ask
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unattended() -> Access {
        Access { host: 440_207_203, unattended: true, password: None }
    }

    #[test]
    fn an_attended_session_is_never_asked_again_by_itself() {
        let attended = Access { host: 1, unattended: false, password: None };
        assert!(Retry::start(attended, Instant::now()).is_none());
        let with_password = Access { host: 1, unattended: false, password: SupportCode::parse("0123456789ab".into()) };
        assert!(Retry::start(with_password, Instant::now()).is_some());
    }

    #[test]
    fn it_asks_at_once_then_every_two_seconds_and_waits_while_an_ask_is_open() {
        let start = Instant::now();
        let mut retry = Retry::start(unattended(), start).unwrap();
        assert_eq!(retry.step(start, false), Step::Ask);
        assert_eq!(retry.step(start + Duration::from_millis(500), false), Step::Wait);
        assert_eq!(retry.step(start + Duration::from_secs(2), true), Step::Wait, "an ask is still open");
        assert_eq!(retry.step(start + Duration::from_secs(2), false), Step::Ask);
        assert_eq!(retry.step(start + Duration::from_secs(5), false), Step::Ask);
    }

    #[test]
    fn after_a_minute_it_gives_up_and_says_so() {
        let start = Instant::now();
        let mut retry = Retry::start(unattended(), start).unwrap();
        assert_eq!(retry.step(start + Duration::from_secs(59), false), Step::Ask);
        assert_eq!(retry.step(start + Duration::from_secs(60), false), Step::GiveUp);
        assert_eq!(retry.step(start + Duration::from_secs(90), true), Step::GiveUp);
    }
}
