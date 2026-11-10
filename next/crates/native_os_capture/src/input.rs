//! Per-session ownership of injected key/button state. Disconnect cleanup never
//! emits an up event for another live owner's held input, and failed up events
//! stay pending for retry rather than being silently forgotten.
use core_protocol::{
    input::{self, Input},
    session::Session,
};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum InjectionError {
    #[error("OS input permission is required")]
    Permission,
    #[error("input event is unsupported")]
    Unsupported,
    #[error("OS input failed: {0}")]
    Native(String),
}
pub trait Injector {
    fn inject(&mut self, event: &Input<'_>) -> Result<(), InjectionError>;
}
#[derive(Debug, PartialEq, Eq, thiserror::Error)]
pub enum InputError {
    #[error("session has no active input grant")]
    Forbidden,
    #[error("input session capacity reached")]
    Capacity,
    #[error("invalid input event")]
    Invalid,
    #[error(transparent)]
    Injection(#[from] InjectionError),
    #[error(transparent)]
    Crypto(#[from] core_protocol::crypto::CryptoError),
    #[error("{0} input releases remain pending")]
    Pending(usize),
}
/// A process-local capability. Wire messages cannot construct this token.
#[derive(Clone)]
pub struct InputToken {
    id: Uuid,
}
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Held {
    Key(u16),
    Button(u8),
}
impl Held {
    fn event(self, down: bool) -> Input<'static> {
        match self {
            Self::Key(usage) => Input::Key { usage, down },
            Self::Button(button) => Input::Button { button, down },
        }
    }
}
pub struct InputRouter<T: Injector> {
    injector: T,
    sessions: HashMap<Uuid, Session>,
    owners: BTreeMap<Held, BTreeSet<Uuid>>,
}
impl<T: Injector> InputRouter<T> {
    pub fn new(injector: T) -> Self {
        Self {
            injector,
            sessions: HashMap::new(),
            owners: BTreeMap::new(),
        }
    }
    /// The caller moves an already authenticated, consented session into the
    /// router. View-only/pending/rejected sessions cannot obtain an input token.
    pub fn register(&mut self, session: Session) -> Result<InputToken, InputError> {
        if !session.can_inject_local() {
            return Err(InputError::Forbidden);
        }
        if self.sessions.len() >= 32 {
            return Err(InputError::Capacity);
        }
        let id = Uuid::new_v4();
        self.sessions.insert(id, session);
        Ok(InputToken { id })
    }
    pub fn dispatch(&mut self, token: &InputToken, event: &Input<'_>) -> Result<(), InputError> {
        if !self
            .sessions
            .get(&token.id)
            .is_some_and(Session::can_inject_local)
        {
            return Err(InputError::Forbidden);
        }
        input::validate(event).map_err(|_| InputError::Invalid)?;
        match *event {
            Input::ReleaseAll => self.release_owned(token.id),
            Input::Key { usage, down } => self.change(token.id, Held::Key(usage), down),
            Input::Button { button, down } => self.change(token.id, Held::Button(button), down),
            Input::ButtonClick { button, down, .. } => {
                self.change_event(token.id, Held::Button(button), down, event)
            }
            _ => self.injector.inject(event).map_err(InputError::Injection),
        }
    }
    pub fn dispatch_encrypted(
        &mut self,
        token: &InputToken,
        channel: &mut core_protocol::crypto::ControlChannel,
        record: &[u8],
    ) -> Result<(), InputError> {
        let proof = channel.verified_session();
        if !self
            .sessions
            .get(&token.id)
            .is_some_and(|session| session.can_inject_local() && session.matches_verified(&proof))
        {
            return Err(InputError::Forbidden);
        }
        let bytes = zeroize::Zeroizing::new(channel.open(record)?);
        let event = input::decode(&bytes).map_err(|_| InputError::Invalid)?;
        self.dispatch(token, &event)
    }
    /// A multiplexed, already-decrypted control message must retain the same
    /// opaque peer/session/certificate proof as its registered input grant.
    pub fn dispatch_verified(
        &mut self,
        token: &InputToken,
        proof: &core_protocol::crypto::VerifiedSession,
        event: &Input<'_>,
    ) -> Result<(), InputError> {
        if !self
            .sessions
            .get(&token.id)
            .is_some_and(|session| session.can_inject_local() && session.matches_verified(proof))
        {
            return Err(InputError::Forbidden);
        }
        self.dispatch(token, event)
    }
    fn change(&mut self, id: Uuid, held: Held, down: bool) -> Result<(), InputError> {
        self.change_event(id, held, down, &held.event(down))
    }
    fn change_event(
        &mut self,
        id: Uuid,
        held: Held,
        down: bool,
        event: &Input<'_>,
    ) -> Result<(), InputError> {
        let owners = self.owners.entry(held).or_default();
        if down {
            // Repeated keydown by the same owner preserves typematic delivery.
            // A second owner adopts the already-held OS state without a second
            // synthetic down event that could confuse native modifier state.
            if owners.is_empty() || owners.contains(&id) {
                self.injector.inject(event)?;
            }
            owners.insert(id);
        } else if owners.contains(&id) {
            if owners.len() == 1 {
                self.injector.inject(event)?;
            }
            owners.remove(&id);
        }
        if owners.is_empty() {
            self.owners.remove(&held);
        }
        Ok(())
    }
    fn release_owned(&mut self, id: Uuid) -> Result<(), InputError> {
        let held: Vec<_> = self
            .owners
            .iter()
            .filter(|(_, owners)| owners.contains(&id))
            .map(|(key, _)| *key)
            .collect();
        let mut failures = 0;
        for key in held {
            if self.change(id, key, false).is_err() {
                failures += 1;
            }
        }
        if failures == 0 {
            Ok(())
        } else {
            Err(InputError::Pending(failures))
        }
    }
    pub fn close(&mut self, token: &InputToken) -> Result<(), InputError> {
        if let Some(session) = self.sessions.get_mut(&token.id) {
            session.close();
        }
        let result = self.release_owned(token.id);
        if result.is_ok() {
            self.sessions.remove(&token.id);
        }
        result
    }
    /// Called by the host cleanup timer after transient native injection errors.
    /// Closed sessions remain unauthorized throughout retries.
    pub fn retry_closed(&mut self) -> Result<(), InputError> {
        let closed: Vec<_> = self
            .sessions
            .iter()
            .filter(|(_, s)| !s.can_control())
            .map(|(id, _)| *id)
            .collect();
        let mut failures = 0;
        for id in closed {
            match self.release_owned(id) {
                Ok(()) => {
                    self.sessions.remove(&id);
                }
                Err(InputError::Pending(n)) => failures += n,
                Err(_) => failures += 1,
            }
        }
        if failures == 0 {
            Ok(())
        } else {
            Err(InputError::Pending(failures))
        }
    }
    pub fn held_count(&self) -> usize {
        self.owners
            .values()
            .filter(|owners| !owners.is_empty())
            .count()
    }
    pub fn session_count(&self) -> usize {
        self.sessions.len()
    }
}
impl<T: Injector> Drop for InputRouter<T> {
    fn drop(&mut self) {
        let tokens: Vec<_> = self
            .sessions
            .keys()
            .map(|id| InputToken { id: *id })
            .collect();
        for token in tokens {
            let _ = self.close(&token);
        }
        let _ = self.retry_closed();
    }
}
