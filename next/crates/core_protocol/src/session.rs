//! Fresh attended permission is bound to the verified peer/session/certificates.
use crate::crypto::{Context, Role, VerifiedSession};
use thiserror::Error;
use web_time::{Duration, Instant};
pub const CONSENT_LIFETIME: Duration = Duration::from_secs(90);
pub const NEGOTIATION_LIFETIME: Duration = Duration::from_secs(30);
pub const ACTIVE_LIFETIME: Duration = Duration::from_secs(15 * 60);
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    AwaitingConsent,
    Negotiating,
    Active,
    Closed,
}
#[derive(Debug, Error, PartialEq, Eq)]
pub enum SessionError {
    #[error("session expired, peer mismatched or transition forbidden")]
    Forbidden,
}
pub struct Session {
    state: State,
    deadline: Instant,
    control_allowed: bool,
    expected: Option<(Context, Role)>,
    verified: Option<VerifiedSession>,
}
impl Session {
    /// Unbound consent state, useful for invitation expiry. It cannot activate.
    pub fn attended(now: Instant) -> Self {
        Self {
            state: State::AwaitingConsent,
            deadline: now + CONSENT_LIFETIME,
            control_allowed: true,
            expected: None,
            verified: None,
        }
    }
    pub fn attended_view_only(now: Instant) -> Self {
        let mut session = Self::attended(now);
        session.control_allowed = false;
        session
    }
    /// Context identities must be pinned by enrollment or verified pairing.
    pub fn for_peer(now: Instant, context: Context, role: Role, control_allowed: bool) -> Self {
        let mut session = Self::attended(now);
        session.expected = Some((context, role));
        session.control_allowed = control_allowed;
        session
    }
    pub fn state(&self) -> State {
        self.state
    }
    pub fn decide(&mut self, accept: bool, now: Instant) -> Result<(), SessionError> {
        if self.state != State::AwaitingConsent {
            return Err(SessionError::Forbidden);
        }
        if now >= self.deadline {
            self.close();
            return Err(SessionError::Forbidden);
        }
        self.state = if accept {
            State::Negotiating
        } else {
            State::Closed
        };
        self.deadline = now + NEGOTIATION_LIFETIME;
        Ok(())
    }
    pub fn authenticated_transport_ready(
        &mut self,
        proof: &VerifiedSession,
        now: Instant,
    ) -> Result<(), SessionError> {
        if self.state != State::Negotiating {
            return Err(SessionError::Forbidden);
        }
        if now >= self.deadline
            || !self
                .expected
                .as_ref()
                .is_some_and(|(context, role)| proof.matches(context, *role))
        {
            self.close();
            return Err(SessionError::Forbidden);
        }
        self.verified = Some(proof.clone());
        self.state = State::Active;
        self.deadline = now + ACTIVE_LIFETIME;
        Ok(())
    }
    pub fn can_view_at(&self, now: Instant) -> bool {
        self.state == State::Active && self.verified.is_some() && now < self.deadline
    }
    pub fn can_view(&self) -> bool {
        self.can_view_at(Instant::now())
    }
    pub fn can_control(&self) -> bool {
        self.can_view() && self.control_allowed
    }
    pub fn can_share_local_screen(&self) -> bool {
        self.can_view()
            && self
                .verified
                .as_ref()
                .is_some_and(|p| p.local_role() == Role::Host)
    }
    pub fn can_inject_local(&self) -> bool {
        self.can_control()
            && self
                .verified
                .as_ref()
                .is_some_and(|p| p.local_role() == Role::Host)
    }
    pub fn fingerprints(&self) -> Option<([u8; 32], [u8; 32])> {
        self.can_view()
            .then(|| self.verified.as_ref().map(VerifiedSession::fingerprints))
            .flatten()
    }
    pub fn matches_verified(&self, proof: &VerifiedSession) -> bool {
        self.can_view() && self.verified.as_ref() == Some(proof)
    }
    pub fn close(&mut self) {
        self.state = State::Closed;
        self.verified = None;
    }
    pub fn expire(&mut self, now: Instant) -> bool {
        if self.state != State::Closed && now >= self.deadline {
            self.close();
            true
        } else {
            false
        }
    }
}
