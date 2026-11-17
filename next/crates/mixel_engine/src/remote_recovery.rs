//! Retry only temporary transport failures. Identity, TLS, protocol and local
//! consent failures never become an automatic retry or a resumed input grant.
use rand_core::{OsRng, RngCore};
use std::time::Duration;
use tokio_tungstenite::tungstenite::{
    Error, error::ProtocolError, protocol::frame::coding::CloseCode,
};
#[derive(Debug)]
pub struct Failure {
    pub message: String,
    pub retryable: bool,
    pub registered_for: Duration,
}
impl Failure {
    pub fn network(message: &str) -> Self {
        Self {
            message: message.into(),
            retryable: true,
            registered_for: Duration::ZERO,
        }
    }
}
impl From<String> for Failure {
    fn from(message: String) -> Self {
        Self {
            message,
            retryable: false,
            registered_for: Duration::ZERO,
        }
    }
}
impl From<&str> for Failure {
    fn from(message: &str) -> Self {
        message.to_owned().into()
    }
}
pub fn retryable_request(error: &reqwest::Error) -> bool {
    if error.is_timeout() {
        return true;
    }
    let mut cause = std::error::Error::source(error);
    while let Some(error) = cause {
        if let Some(io) = error.downcast_ref::<std::io::Error>() {
            use std::io::ErrorKind::*;
            if matches!(
                io.kind(),
                ConnectionRefused
                    | ConnectionReset
                    | ConnectionAborted
                    | NotConnected
                    | NetworkDown
                    | NetworkUnreachable
                    | HostUnreachable
                    | TimedOut
            ) {
                return true;
            }
        }
        cause = error.source();
    }
    false
}

pub fn socket(error: Error) -> Failure {
    match error {
        Error::Io(_)
        | Error::ConnectionClosed
        | Error::AlreadyClosed
        | Error::Protocol(ProtocolError::ResetWithoutClosingHandshake) => {
            Failure::network("The connection service disconnected")
        }
        Error::Http(response) if matches!(response.status().as_u16(), 502..=504) => {
            Failure::network("The connection service is temporarily unavailable")
        }
        Error::Tls(_) => "The connection service TLS certificate could not be verified".into(),
        _ => "The connection service rejected the secure transport or sent invalid data".into(),
    }
}
pub fn closed(code: Option<CloseCode>) -> Failure {
    if code.is_none_or(|code| {
        matches!(
            code,
            CloseCode::Away | CloseCode::Restart | CloseCode::Again
        )
    }) {
        Failure::network("The connection service ended this transport")
    } else {
        "The connection service closed this registration; sign in and register again".into()
    }
}
#[derive(Default)]
pub struct Backoff {
    failures: u8,
    /// Registrations in a row that reached "ready" but ended within a few seconds.
    short: u8,
}
/// A registration that stayed ready this long counts as working: the back-off starts again at its minimum.
const WORKED: Duration = Duration::from_secs(5);
impl Backoff {
    /// `stable`: how long the ended registration was ready (zero when it never was).
    pub fn next(&mut self, stable: Duration) -> Option<Duration> {
        if stable >= WORKED {
            // Any registration that worked resets the back-off (it used to need a full minute, so a
            // restart after a long wait sat at the 60 s ceiling).
            self.failures = 0;
            self.short = 0;
        } else if !stable.is_zero() {
            // Ready, then gone within seconds (a restart, a closing connection service): the first time
            // register again at once, then back off as before.
            self.short = self.short.saturating_add(1);
            if self.short == 1 {
                self.failures = 0;
                return Some(Duration::from_millis(100 + u64::from(OsRng.next_u32() % 301)));
            }
        }
        // Quick retries first, then every minute for as long as the network or
        // service is down: an unattended computer must come back on its own.
        let seconds = [1, 2, 4, 8, 16, 30]
            .get(usize::from(self.failures))
            .copied()
            .unwrap_or(60);
        self.failures = self.failures.saturating_add(1);
        Some(
            Duration::from_secs(seconds) + Duration::from_millis(u64::from(OsRng.next_u32() % 501)),
        )
    }
    pub fn attempt(&self) -> u8 {
        self.failures
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_network_loss_and_temporary_server_shutdown_can_retry() {
        assert!(
            socket(Error::Io(std::io::Error::from(
                std::io::ErrorKind::ConnectionReset
            )))
            .retryable
        );
        assert!(socket(Error::Protocol(ProtocolError::ResetWithoutClosingHandshake)).retryable);
        assert!(!socket(Error::Protocol(ProtocolError::UnmaskedFrameFromClient)).retryable);
        assert!(!closed(Some(CloseCode::Policy)).retryable);
        assert!(!closed(Some(CloseCode::Normal)).retryable);
        assert!(closed(Some(CloseCode::Restart)).retryable);
        assert!(closed(None).retryable);
        assert!(!Failure::from("Enrollment or local consent changed").retryable);
    }
    #[test]
    fn continuous_failure_retries_back_off_to_one_minute_and_never_give_up() {
        let mut state = Backoff::default();
        for minimum in [1, 2, 4, 8, 16, 30] {
            let delay = state.next(Duration::ZERO).unwrap();
            assert!(
                delay >= Duration::from_secs(minimum)
                    && delay <= Duration::from_secs(minimum) + Duration::from_millis(500)
            );
        }
        for _ in 0..300 {
            let delay = state.next(Duration::ZERO).unwrap();
            assert!(delay >= Duration::from_secs(60) && delay <= Duration::from_millis(60_500));
        }
        assert!(state.next(Duration::from_secs(61)).is_some());
        assert_eq!(state.attempt(), 1);
    }

    #[test]
    fn a_registration_that_worked_resets_the_back_off_to_its_minimum() {
        let mut state = Backoff::default();
        for _ in 0..20 {
            state.next(Duration::ZERO);
        }
        let delay = state.next(Duration::from_secs(8)).unwrap();
        assert!(delay <= Duration::from_millis(1500), "{delay:?}: back at the minimum after a registration that worked");
    }

    #[test]
    fn a_registration_that_ends_within_seconds_is_retried_at_once_only_once() {
        let mut state = Backoff::default();
        for _ in 0..20 {
            state.next(Duration::ZERO);
        }
        // Ready, then gone after 2 s (right after ConfigureUnattended): immediate retry, not 60 s.
        assert!(state.next(Duration::from_secs(2)).unwrap() <= Duration::from_millis(500));
        // Again within seconds: normal back-off from the start, never a tight loop.
        let second = state.next(Duration::from_secs(2)).unwrap();
        assert!(second >= Duration::from_secs(1));
        let third = state.next(Duration::from_secs(2)).unwrap();
        assert!(third >= Duration::from_secs(2));
        // After a registration that held, the quick retry is available again.
        state.next(Duration::from_secs(70));
        assert!(state.next(Duration::from_secs(2)).unwrap() <= Duration::from_millis(500));
    }
}
