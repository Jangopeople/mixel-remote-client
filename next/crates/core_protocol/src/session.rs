//! An attended session never becomes active without a fresh host decision.
use thiserror::Error;
use web_time::{Duration, Instant};

pub const CONSENT_LIFETIME: Duration = Duration::from_secs(90);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    AwaitingConsent,
    Negotiating,
    Active,
    Closed,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum SessionError {
    #[error("session expired or transition forbidden")]
    Forbidden,
}

pub struct Session {
    state: State,
    deadline: Instant,
}

impl Session {
    pub fn attended(now: Instant) -> Self {
        Self {
            state: State::AwaitingConsent,
            deadline: now + CONSENT_LIFETIME,
        }
    }
    pub fn state(&self) -> State {
        self.state
    }
    pub fn decide(&mut self, accept: bool, now: Instant) -> Result<(), SessionError> {
        if self.state != State::AwaitingConsent {
            return Err(SessionError::Forbidden);
        }
        if now >= self.deadline {
            self.state = State::Closed;
            return Err(SessionError::Forbidden);
        }
        self.state = if accept {
            State::Negotiating
        } else {
            State::Closed
        };
        Ok(())
    }
    /// Call only after the authenticated peer handshake and DTLS fingerprints match.
    pub fn authenticated_transport_ready(&mut self) -> Result<(), SessionError> {
        if self.state != State::Negotiating {
            return Err(SessionError::Forbidden);
        }
        self.state = State::Active;
        Ok(())
    }
    pub fn can_control(&self) -> bool {
        self.state == State::Active
    }
    pub fn close(&mut self) {
        self.state = State::Closed;
    }
    pub fn expire(&mut self, now: Instant) -> bool {
        if self.state == State::AwaitingConsent && now >= self.deadline {
            self.close();
            true
        } else {
            false
        }
    }
}
