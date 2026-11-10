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
}
impl Backoff {
    pub fn next(&mut self, stable: Duration) -> Option<Duration> {
        if stable >= Duration::from_secs(60) {
            self.failures = 0;
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
            let delay = state.next(Duration::from_secs(2)).unwrap();
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
}
