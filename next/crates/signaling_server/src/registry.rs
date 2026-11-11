//! Bounded registry. All mutation occurs under one short mutex; no network await
//! occurs while held. Connection generations prevent stale disconnects deleting
//! a replacement. Slow peers are disconnected, never given unbounded queues.
use crate::{
    auth::Principal,
    protocol::*,
    support_policy::{DeviceBinding, SupportPair},
};
use core_protocol::PROTOCOL_VERSION;
use rand_core::{OsRng, RngCore};
use std::{
    collections::HashMap,
    time::{Duration, Instant},
};
use tokio::sync::{mpsc, watch};
use uuid::Uuid;

const MAX_PEERS: usize = 1000;
const MAX_SESSIONS: usize = 1000;
const MAX_PER_PEER: usize = 4;
const MAX_PRESENCE: usize = 64;
struct Peer {
    generation: Uuid,
    principal: Principal,
    tx: mpsc::Sender<ServerMessage>,
    stop: watch::Sender<bool>,
    ice_next: Instant,
    identity: Option<core_protocol::delegation::DeviceTicket>,
    code_gate: Option<CodeGate>,
    unattended_enabled: bool,
}
impl Peer {
    fn unattended_host_only(&self) -> bool {
        self.identity
            .as_ref()
            .is_some_and(|ticket| ticket.unattended_host_only())
    }

    fn can_request(&self, target: &Self, unattended: bool) -> bool {
        !self.unattended_host_only()
            && (unattended || !target.unattended_host_only())
            && self.principal.can_request(&target.principal)
    }
}
const CODE_LIFETIME_SECONDS: u16 = 300;
const CODE_ATTEMPTS: u8 = 5;

struct CodeGate {
    code: Option<SupportCode>,
    expires_at: Instant,
    attempts_left: u8,
}

#[derive(Clone, Copy)]
enum Phase {
    Consent,
    Offer,
    Answer,
    Connected,
}
struct Session {
    controller: u32,
    host: u32,
    unattended: bool,
    phase: Phase,
    deadline: Instant,
    identity_from_controller: bool,
    identity_from_host: bool,
    active_until: Option<Instant>,
    restart_after: Instant,
}
struct Retired {
    controller: u32,
    host: u32,
    deadline: Instant,
}
#[derive(Default)]
pub struct Registry {
    peers: HashMap<u32, Peer>,
    sessions: HashMap<Uuid, Session>,
    retired: HashMap<Uuid, Retired>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct PolicyCheck {
    pub pair: SupportPair,
    pub controller: (u32, Uuid),
    pub host: (u32, Uuid),
    pub unattended: bool,
}

impl Registry {
    pub(crate) fn device_binding(
        &self,
        id: u32,
        generation: Uuid,
        unix: u64,
    ) -> Option<DeviceBinding> {
        let peer = self.peers.get(&id)?;
        if peer.generation != generation
            || peer.principal.expires_at <= unix
            || peer.unattended_host_only()
        {
            return None;
        }
        Some(peer.identity.as_ref()?.into())
    }

    /// The verified device behind a connection, including unattended-only hosts (for the agent).
    pub(crate) fn agent_binding(&self, id: u32, generation: Uuid, unix: u64) -> Option<DeviceBinding> {
        let peer = self.peers.get(&id)?;
        if peer.generation != generation || peer.principal.expires_at <= unix {
            return None;
        }
        Some(peer.identity.as_ref()?.into())
    }

    fn policy_check(
        &self,
        controller: u32,
        host: u32,
        unattended: bool,
        unix: u64,
    ) -> Option<PolicyCheck> {
        let source = self.peers.get(&controller)?;
        let target = self.peers.get(&host)?;
        if controller == host
            || source.principal.expires_at <= unix
            || target.principal.expires_at <= unix
            || !source.can_request(target, unattended)
            || (unattended && (!target.unattended_enabled || target.code_gate.is_some()))
        {
            return None;
        }
        Some(PolicyCheck {
            pair: SupportPair {
                controller: source.identity.as_ref()?.into(),
                host: target.identity.as_ref()?.into(),
            },
            controller: (controller, source.generation),
            host: (host, target.generation),
            unattended,
        })
    }
    pub(crate) fn policy_for_message(
        &self,
        from: u32,
        generation: Uuid,
        message: &ClientMessage,
        unix: u64,
    ) -> Option<PolicyCheck> {
        let (controller, host, unattended) = match message {
            ClientMessage::Request { host } | ClientMessage::RequestWithCode { host, .. } => {
                (from, *host, false)
            }
            ClientMessage::RequestUnattended { host } => (from, *host, true),
            ClientMessage::Decide {
                session,
                accept: true,
            } => {
                let current = self.sessions.get(session)?;
                if current.host != from || !matches!(current.phase, Phase::Consent) {
                    return None;
                }
                (current.controller, current.host, current.unattended)
            }
            ClientMessage::Restart { session } => {
                let current = self.sessions.get(session)?;
                if current.controller != from || !matches!(current.phase, Phase::Connected) {
                    return None;
                }
                (current.controller, current.host, current.unattended)
            }
            _ => return None,
        };
        let check = self.policy_check(controller, host, unattended, unix)?;
        (check.controller == (from, generation) || check.host == (from, generation))
            .then_some(check)
    }
    pub(crate) fn controlled_policy_checks(
        &self,
        controller: u32,
        generation: Uuid,
        unix: u64,
    ) -> Vec<(Uuid, Option<PolicyCheck>)> {
        if !self
            .peers
            .get(&controller)
            .is_some_and(|peer| peer.generation == generation)
        {
            return Vec::new();
        }
        self.sessions
            .iter()
            .filter(|(_, session)| session.controller == controller)
            .map(|(id, session)| {
                (
                    *id,
                    self.policy_check(controller, session.host, session.unattended, unix),
                )
            })
            .collect()
    }
    pub(crate) fn policy_revoked(&mut self, session: Uuid, controller: (u32, Uuid), now: Instant) {
        if self
            .sessions
            .get(&session)
            .is_some_and(|room| room.controller == controller.0)
            && self
                .peers
                .get(&controller.0)
                .is_some_and(|peer| peer.generation == controller.1)
        {
            self.close(session, CloseReason::Expired, now);
        }
    }
    pub fn bind_identity(
        &mut self,
        id: u32,
        generation: Uuid,
        identity: core_protocol::delegation::DeviceTicket,
    ) -> Result<(), ErrorCode> {
        let peer = self.peers.get_mut(&id).ok_or(ErrorCode::Forbidden)?;
        if peer.generation != generation
            || identity.identity.connection_id != id
            || identity.identity.profile != *peer.principal.user.as_bytes()
            || identity.identity.tenant != peer.principal.tenant.map(|value| *value.as_bytes())
            || identity.staff != peer.principal.staff
            || identity.expires_at != peer.principal.expires_at
            || peer
                .identity
                .as_ref()
                .is_some_and(|previous| previous.version != identity.version)
        {
            return Err(ErrorCode::Forbidden);
        }
        peer.identity = Some(identity.clone());
        let affected: Vec<_> = self
            .sessions
            .iter()
            .filter(|(_, session)| {
                !matches!(session.phase, Phase::Consent)
                    && (session.controller == id || session.host == id)
            })
            .map(|(room, session)| {
                (
                    *room,
                    if session.controller == id {
                        session.host
                    } else {
                        session.controller
                    },
                )
            })
            .collect();
        for (session, target) in affected {
            self.send(
                target,
                ServerMessage::PeerRenewed {
                    session,
                    identity: identity.clone(),
                },
            );
        }
        Ok(())
    }
    pub fn ice_permission(
        &mut self,
        id: u32,
        generation: Uuid,
        session: Uuid,
        now: Instant,
        unix: u64,
    ) -> Result<u64, ErrorCode> {
        self.expire(now);
        let active = self.sessions.get(&session).ok_or(ErrorCode::Forbidden)?;
        if (active.host != id && active.controller != id) || matches!(active.phase, Phase::Consent)
        {
            return Err(ErrorCode::Forbidden);
        }
        let peer = self.peers.get_mut(&id).ok_or(ErrorCode::Forbidden)?;
        if peer.generation != generation || peer.principal.expires_at.saturating_sub(unix) < 30 {
            return Err(ErrorCode::Forbidden);
        }
        if now < peer.ice_next {
            return Err(ErrorCode::Busy);
        }
        peer.ice_next = now + Duration::from_secs(5);
        Ok(peer.principal.expires_at)
    }
    pub fn register(
        &mut self,
        principal: Principal,
        tx: mpsc::Sender<ServerMessage>,
        stop: watch::Sender<bool>,
    ) -> Result<(u32, Uuid), ErrorCode> {
        if self.peers.len() >= MAX_PEERS {
            return Err(ErrorCode::Busy);
        }
        let host = (0..128)
            .find_map(|_| {
                // Rejection sampling avoids modulo bias in public nine-digit IDs.
                let n = OsRng.next_u32();
                if n >= 3_600_000_000 {
                    return None;
                }
                let id = 100_000_000 + n % 900_000_000;
                (!self.peers.contains_key(&id)).then_some(id)
            })
            .ok_or(ErrorCode::Busy)?;
        self.register_with_id(host, principal, tx, stop)
    }
    pub fn register_with_id(
        &mut self,
        host: u32,
        principal: Principal,
        tx: mpsc::Sender<ServerMessage>,
        stop: watch::Sender<bool>,
    ) -> Result<(u32, Uuid), ErrorCode> {
        if !(100_000_000..=999_999_999).contains(&host)
            || self.peers.contains_key(&host)
            || self.peers.len() >= MAX_PEERS
        {
            return Err(ErrorCode::Busy);
        }
        let generation = Uuid::new_v4();
        self.peers.insert(
            host,
            Peer {
                generation,
                principal,
                tx,
                stop,
                ice_next: Instant::now(),
                identity: None,
                code_gate: None,
                unattended_enabled: false,
            },
        );
        self.send(
            host,
            ServerMessage::Ready {
                host,
                protocol: PROTOCOL_VERSION,
            },
        );
        Ok((host, generation))
    }
    /// Renewal cannot move a live connection to a different account, tenant or
    /// privilege level. Existing consent remains scoped to the original peers.
    pub fn renew(
        &mut self,
        id: u32,
        generation: Uuid,
        principal: Principal,
    ) -> Result<(), ErrorCode> {
        let peer = self.peers.get_mut(&id).ok_or(ErrorCode::Forbidden)?;
        if peer.generation != generation
            || peer.principal.user != principal.user
            || peer.principal.tenant != principal.tenant
            || peer.principal.staff != principal.staff
        {
            return Err(ErrorCode::Forbidden);
        }
        peer.principal = principal;
        Ok(())
    }
    fn send(&self, id: u32, message: ServerMessage) {
        if let Some(peer) = self.peers.get(&id)
            && peer.tx.try_send(message).is_err()
        {
            let _ = peer.stop.send(true);
        }
    }
    fn close(&mut self, session: Uuid, reason: CloseReason, now: Instant) {
        if let Some(s) = self.sessions.remove(&session) {
            if self.retired.len() < MAX_SESSIONS * 2 {
                self.retired.insert(
                    session,
                    Retired {
                        controller: s.controller,
                        host: s.host,
                        deadline: now + Duration::from_secs(60),
                    },
                );
            }
            for id in [s.controller, s.host] {
                self.send(id, ServerMessage::Closed { session, reason });
            }
        }
    }
    pub fn disconnect(&mut self, id: u32, generation: Uuid) {
        if !self
            .peers
            .get(&id)
            .is_some_and(|p| p.generation == generation)
        {
            return;
        }
        self.peers.remove(&id);
        let sessions: Vec<_> = self
            .sessions
            .iter()
            .filter(|(_, s)| s.controller == id || s.host == id)
            .map(|(id, _)| *id)
            .collect();
        for s in sessions {
            self.close(s, CloseReason::Disconnected, Instant::now());
        }
    }
    pub fn expire(&mut self, now: Instant) {
        self.retired.retain(|_, session| now < session.deadline);
        let expired: Vec<_> = self
            .sessions
            .iter()
            .filter(|(_, s)| now >= s.deadline)
            .map(|(id, _)| *id)
            .collect();
        for s in expired {
            self.close(s, CloseReason::Expired, now);
        }
    }
    pub fn handle(
        &mut self,
        from: u32,
        generation: Uuid,
        message: ClientMessage,
        now: Instant,
    ) -> Result<(), ErrorCode> {
        if !self
            .peers
            .get(&from)
            .is_some_and(|p| p.generation == generation)
        {
            return Err(ErrorCode::Forbidden);
        }
        self.expire(now);
        // Old media owners may finish sending already queued ICE/Close records
        // when a session ends or both endpoints replace a transport. Discard only participant
        // traffic for that exact retired room; never route it into the new one.
        if let ClientMessage::Signal { session, .. }
        | ClientMessage::Close { session }
        | ClientMessage::Restart { session } = &message
            && self
                .retired
                .get(session)
                .is_some_and(|old| from == old.controller || from == old.host)
        {
            return Ok(());
        }
        match message {
            ClientMessage::Authenticate { .. }
            | ClientMessage::AuthenticateDevice { .. }
            | ClientMessage::ProveDevice { .. }
            | ClientMessage::Renew { .. } => Err(ErrorCode::Invalid),
            ClientMessage::Ice { .. }
            | ClientMessage::Agent { .. }
            | ClientMessage::CheckPairing
            | ClientMessage::ConfirmPairing { .. } => Err(ErrorCode::Invalid),
            ClientMessage::Restart { session } => {
                let current = self.sessions.get(&session).ok_or(ErrorCode::Forbidden)?;
                if from != current.controller
                    || !matches!(current.phase, Phase::Connected)
                    || now < current.restart_after
                    || self.retired.len() >= MAX_SESSIONS * 2
                {
                    return Err(ErrorCode::Forbidden);
                }
                let controller = current.controller;
                let host = current.host;
                let authority = |id| {
                    self.peers
                        .get(&id)
                        .and_then(|peer| peer.identity.clone())
                        .ok_or(ErrorCode::Forbidden)
                };
                let controller_identity = authority(controller)?;
                let host_identity = authority(host)?;
                let unattended = current.unattended;
                let until = current.active_until.ok_or(ErrorCode::Forbidden)?;
                if now >= until {
                    return Err(ErrorCode::Forbidden);
                }
                let replacement = Uuid::new_v4();
                let mut nonce = [0; 32];
                OsRng.fill_bytes(&mut nonce);
                self.sessions.remove(&session);
                self.sessions.insert(
                    replacement,
                    Session {
                        controller,
                        host,
                        unattended,
                        phase: Phase::Offer,
                        deadline: (now + Duration::from_secs(60)).min(until),
                        identity_from_controller: false,
                        identity_from_host: false,
                        active_until: Some(until),
                        restart_after: now + Duration::from_secs(30),
                    },
                );
                self.retired.insert(
                    session,
                    Retired {
                        controller,
                        host,
                        deadline: now + Duration::from_secs(60),
                    },
                );
                for id in [controller, host] {
                    self.send(
                        id,
                        ServerMessage::Restarted {
                            previous: session,
                            session: replacement,
                            nonce,
                            controller_identity: controller_identity.clone(),
                            host_identity: host_identity.clone(),
                        },
                    );
                }
                Ok(())
            }
            ClientMessage::ConfigureUnattended { enabled } => {
                let Some(peer) = self
                    .peers
                    .get_mut(&from)
                    .filter(|peer| peer.identity.is_some())
                else {
                    return Err(ErrorCode::Forbidden);
                };
                peer.unattended_enabled = enabled && peer.code_gate.is_none();
                let enabled = peer.unattended_enabled;
                if !enabled {
                    let rooms: Vec<_> = self
                        .sessions
                        .iter()
                        .filter_map(|(id, room)| {
                            (room.host == from && room.unattended).then_some(*id)
                        })
                        .collect();
                    for room in rooms {
                        self.close(room, CloseReason::Requested, now);
                    }
                }
                self.send(from, ServerMessage::UnattendedStatus { enabled });
                Ok(())
            }
            ClientMessage::GenerateSupportCode | ClientMessage::ClearSupportCode => {
                let Some(peer) = self
                    .peers
                    .get_mut(&from)
                    .filter(|peer| peer.identity.is_some() && !peer.unattended_host_only())
                else {
                    return Err(ErrorCode::Forbidden);
                };
                let required = matches!(message, ClientMessage::GenerateSupportCode);
                if required && peer.unattended_enabled {
                    return Err(ErrorCode::Busy);
                }
                let code = required.then(SupportCode::generate);
                peer.code_gate = code.clone().map(|code| CodeGate {
                    code: Some(code),
                    expires_at: now + Duration::from_secs(u64::from(CODE_LIFETIME_SECONDS)),
                    attempts_left: CODE_ATTEMPTS,
                });
                self.send(
                    from,
                    ServerMessage::SupportCode {
                        required,
                        code,
                        lifetime_seconds: if required { CODE_LIFETIME_SECONDS } else { 0 },
                    },
                );
                Ok(())
            }
            ClientMessage::Presence { hosts } => {
                if hosts.len() > MAX_PRESENCE {
                    return Err(ErrorCode::Invalid);
                }
                let requester = &self.peers[&from];
                if requester.identity.is_none() {
                    return Err(ErrorCode::Forbidden);
                }
                // Only peers this caller may request are reported, so presence
                // never reveals devices outside the caller's own scope.
                let mut online: Vec<u32> = hosts
                    .into_iter()
                    .filter(|host| {
                        *host != from
                            && self.peers.get(host).is_some_and(|target| {
                                requester.can_request(target, false)
                                    || (target.unattended_enabled && requester.can_request(target, true))
                            })
                    })
                    .collect();
                online.sort_unstable();
                online.dedup();
                self.send(from, ServerMessage::Presence { online });
                Ok(())
            }
            ClientMessage::Request { host }
            | ClientMessage::RequestWithCode { host, .. }
            | ClientMessage::RequestUnattended { host } => {
                let unattended = matches!(message, ClientMessage::RequestUnattended { .. });
                let offered = match &message {
                    ClientMessage::RequestWithCode { code, .. } => Some(code),
                    _ => None,
                };
                let requester = &self.peers[&from];
                let target = self.peers.get(&host).ok_or(ErrorCode::Unavailable)?;
                if from == host
                    || !requester.can_request(target, unattended)
                    || (unattended && !target.unattended_enabled)
                {
                    return Err(ErrorCode::Unavailable);
                }
                if self.sessions.len() >= MAX_SESSIONS
                    || [from, host].iter().any(|peer| {
                        self.sessions
                            .values()
                            .filter(|s| s.host == *peer || s.controller == *peer)
                            .count()
                            >= MAX_PER_PEER
                    })
                {
                    return Err(ErrorCode::Busy);
                }
                let requester = requester.principal.clone();
                let target = self.peers.get_mut(&host).ok_or(ErrorCode::Unavailable)?;
                match (target.code_gate.as_mut(), offered) {
                    (None, None) => {}
                    (Some(gate), Some(offered)) if now < gate.expires_at && gate.code.is_some() => {
                        if !gate.code.as_ref().is_some_and(|code| code.matches(offered)) {
                            gate.attempts_left = gate.attempts_left.saturating_sub(1);
                            if gate.attempts_left == 0 {
                                gate.code = None;
                                self.send(
                                    host,
                                    ServerMessage::SupportCode {
                                        required: true,
                                        code: None,
                                        lifetime_seconds: 0,
                                    },
                                );
                            }
                            return Err(ErrorCode::Unavailable);
                        }
                        gate.code = None;
                        self.send(
                            host,
                            ServerMessage::SupportCode {
                                required: true,
                                code: None,
                                lifetime_seconds: 0,
                            },
                        );
                    }
                    _ => return Err(ErrorCode::Unavailable),
                }
                let session = Uuid::new_v4();
                self.sessions.insert(
                    session,
                    Session {
                        controller: from,
                        host,
                        unattended,
                        phase: Phase::Consent,
                        deadline: now + Duration::from_secs(90),
                        identity_from_controller: false,
                        identity_from_host: false,
                        active_until: None,
                        restart_after: now,
                    },
                );
                self.send(from, ServerMessage::Requested { session });
                self.send(
                    host,
                    ServerMessage::Consent {
                        session,
                        requester_connection: from,
                        requester: requester.user,
                        requester_tenant: requester.tenant,
                        unattended,
                        requester_identity: self
                            .peers
                            .get(&from)
                            .and_then(|peer| peer.identity.clone()),
                    },
                );
                Ok(())
            }
            ClientMessage::Decide { session, accept } => {
                let s = self
                    .sessions
                    .get_mut(&session)
                    .ok_or(ErrorCode::Forbidden)?;
                if from != s.host || !matches!(s.phase, Phase::Consent) {
                    return Err(ErrorCode::Forbidden);
                }
                if !accept {
                    self.close(session, CloseReason::Rejected, now);
                    return Ok(());
                }
                s.phase = Phase::Offer;
                s.deadline = now + Duration::from_secs(30);
                let (a, b) = (s.controller, s.host);
                let mut nonce = [0; 32];
                OsRng.fill_bytes(&mut nonce);
                for id in [a, b] {
                    self.send(
                        id,
                        ServerMessage::Accepted {
                            session,
                            nonce,
                            controller_identity: self
                                .peers
                                .get(&a)
                                .and_then(|peer| peer.identity.clone()),
                            host_identity: self
                                .peers
                                .get(&b)
                                .and_then(|peer| peer.identity.clone()),
                        },
                    );
                }
                Ok(())
            }
            ClientMessage::Signal { session, payload } => {
                if !payload.valid() {
                    return Err(ErrorCode::Invalid);
                }
                let s = self
                    .sessions
                    .get_mut(&session)
                    .ok_or(ErrorCode::Forbidden)?;
                if from != s.controller && from != s.host {
                    return Err(ErrorCode::Forbidden);
                }
                match (&payload, s.phase) {
                    (Signal::Identity(_), Phase::Offer | Phase::Answer | Phase::Connected) => {
                        let delivered = if from == s.controller {
                            &mut s.identity_from_controller
                        } else {
                            &mut s.identity_from_host
                        };
                        if *delivered {
                            return Err(ErrorCode::Forbidden);
                        }
                        *delivered = true;
                    }

                    (Signal::Offer(_), Phase::Offer) if from == s.controller => {
                        s.phase = Phase::Answer;
                    }
                    (Signal::Answer(_), Phase::Answer) if from == s.host => {
                        s.phase = Phase::Connected;
                        let until = *s
                            .active_until
                            .get_or_insert(now + Duration::from_secs(8 * 3600));
                        s.deadline = until;
                    }
                    (Signal::Ice(_), Phase::Answer | Phase::Connected) => {}
                    _ => return Err(ErrorCode::Forbidden),
                }
                let to = if from == s.controller {
                    s.host
                } else {
                    s.controller
                };
                self.send(to, ServerMessage::Signal { session, payload });
                Ok(())
            }
            ClientMessage::Close { session } => {
                let s = self.sessions.get(&session).ok_or(ErrorCode::Forbidden)?;
                if from != s.controller && from != s.host {
                    return Err(ErrorCode::Forbidden);
                }
                self.close(session, CloseReason::Requested, now);
                Ok(())
            }
        }
    }
    pub fn shutdown(&mut self) {
        let now = Instant::now();
        let sessions: Vec<_> = self.sessions.keys().copied().collect();
        for id in sessions {
            self.close(id, CloseReason::Disconnected, now);
        }
        for peer in self.peers.values() {
            let _ = peer.stop.send(true);
        }
        self.peers.clear();
        self.retired.clear();
    }
    pub fn counts(&self) -> (usize, usize) {
        (self.peers.len(), self.sessions.len())
    }
}

#[cfg(test)]
mod rotation_tests {
    use super::*;
    fn peer(
        registry: &mut Registry,
        id: u32,
        profile: u128,
    ) -> (Uuid, mpsc::Receiver<ServerMessage>) {
        let (tx, rx) = mpsc::channel(64);
        let (stop, _) = watch::channel(false);
        let principal = Principal {
            user: Uuid::from_u128(profile),
            tenant: Some(Uuid::from_u128(1)),
            staff: false,
            expires_at: 900,
        };
        let (_, generation) = registry.register_with_id(id, principal, tx, stop).unwrap();
        let ticket = core_protocol::delegation::DeviceTicket::issue(
            core_protocol::device_login::DeviceLoginChallenge {
                nonce: [1; 32],
                device: *Uuid::from_u128(profile + 100).as_bytes(),
                profile: *Uuid::from_u128(profile).as_bytes(),
                tenant: Some(*Uuid::from_u128(1).as_bytes()),
                connection_id: id,
                generation: 1,
            },
            ed25519_dalek::SigningKey::from_bytes(&[7; 32]).verifying_key(),
            false,
            100,
            900,
            &ed25519_dalek::SigningKey::from_bytes(&[9; 32]),
        )
        .unwrap();
        registry.bind_identity(id, generation, ticket).unwrap();
        (generation, rx)
    }
    #[test]
    fn presence_reports_only_online_peers_in_the_callers_scope() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (controller, mut events) = peer(&mut registry, 123_456_789, 10);
        let (_host, _) = peer(&mut registry, 234_567_890, 11);
        let (tx, _other_rx) = mpsc::channel(64);
        let (stop, _) = watch::channel(false);
        registry
            .register_with_id(
                345_678_901,
                Principal {
                    user: Uuid::from_u128(20),
                    tenant: Some(Uuid::from_u128(2)),
                    staff: false,
                    expires_at: 900,
                },
                tx,
                stop,
            )
            .unwrap();
        while events.try_recv().is_ok() {}
        registry
            .handle(
                123_456_789,
                controller,
                ClientMessage::Presence {
                    hosts: vec![234_567_890, 345_678_901, 456_789_012, 123_456_789],
                },
                now,
            )
            .unwrap();
        let ServerMessage::Presence { online } = events.try_recv().unwrap() else {
            panic!("missing presence");
        };
        assert_eq!(online, vec![234_567_890]);
        assert_eq!(
            registry.handle(
                123_456_789,
                controller,
                ClientMessage::Presence {
                    hosts: (100_000_000..100_000_065).collect(),
                },
                now,
            ),
            Err(ErrorCode::Invalid)
        );
    }
    #[test]
    fn one_time_password_is_bound_to_one_host_and_expires_at_its_deadline() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (controller, _) = peer(&mut registry, 123_456_789, 10);
        let (host, mut host_events) = peer(&mut registry, 234_567_890, 11);
        let (_, mut other_events) = peer(&mut registry, 345_678_901, 12);
        let _ = host_events.try_recv().unwrap();
        let _ = other_events.try_recv().unwrap();
        registry
            .handle(234_567_890, host, ClientMessage::GenerateSupportCode, now)
            .unwrap();
        let ServerMessage::SupportCode {
            code: Some(code), ..
        } = host_events.try_recv().unwrap()
        else {
            panic!("missing code");
        };
        assert_eq!(
            registry.handle(
                123_456_789,
                controller,
                ClientMessage::RequestWithCode {
                    host: 345_678_901,
                    code: code.clone()
                },
                now
            ),
            Err(ErrorCode::Unavailable)
        );
        assert!(other_events.try_recv().is_err());
        assert_eq!(
            registry.handle(
                123_456_789,
                controller,
                ClientMessage::RequestWithCode {
                    host: 234_567_890,
                    code
                },
                now + Duration::from_secs(u64::from(CODE_LIFETIME_SECONDS))
            ),
            Err(ErrorCode::Unavailable)
        );
        assert!(host_events.try_recv().is_err());
    }

    #[test]
    fn both_participants_can_stop_without_rejecting_late_cleanup() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (viewer, mut viewer_events) = peer(&mut registry, 123_456_789, 10);
        let (host, mut host_events) = peer(&mut registry, 234_567_890, 11);
        let (outsider, _) = peer(&mut registry, 345_678_901, 12);
        let _ = viewer_events.try_recv().unwrap();
        let _ = host_events.try_recv().unwrap();
        let request = || ClientMessage::Request { host: 234_567_890 };
        registry
            .handle(123_456_789, viewer, request(), now)
            .unwrap();
        let ServerMessage::Requested { session } = viewer_events.try_recv().unwrap() else {
            panic!("Missing session request");
        };
        let _ = host_events.try_recv().unwrap();
        let close = || ClientMessage::Close { session };
        registry.handle(123_456_789, viewer, close(), now).unwrap();
        registry.handle(123_456_789, viewer, close(), now).unwrap();
        registry.handle(234_567_890, host, close(), now).unwrap();
        assert_eq!(
            registry.handle(345_678_901, outsider, close(), now),
            Err(ErrorCode::Forbidden)
        );
        for events in [&mut viewer_events, &mut host_events] {
            assert!(
                matches!(events.try_recv(), Ok(ServerMessage::Closed { session: closed, .. }) if closed == session)
            );
            assert!(events.try_recv().is_err());
        }
        registry
            .handle(123_456_789, viewer, request(), now)
            .unwrap();
        let ServerMessage::Requested { session: next } = viewer_events.try_recv().unwrap() else {
            panic!("Missing fresh session request");
        };
        assert_ne!(session, next);
        assert_eq!(registry.counts(), (3, 1));
        assert_eq!(
            registry.handle(123_456_789, viewer, close(), now + Duration::from_secs(61)),
            Err(ErrorCode::Forbidden)
        );
        assert_eq!(registry.counts(), (3, 1));
    }

    #[test]
    fn replacement_keeps_original_consent_deadline_and_discards_retired_owner_messages() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (viewer, mut vrx) = peer(&mut registry, 123_456_789, 10);
        let (host, mut hrx) = peer(&mut registry, 234_567_890, 11);
        let (outsider, _) = peer(&mut registry, 345_678_901, 12);
        let _ = vrx.try_recv().unwrap();
        let _ = hrx.try_recv().unwrap();
        registry
            .handle(
                123_456_789,
                viewer,
                ClientMessage::Request { host: 234_567_890 },
                now,
            )
            .unwrap();
        let ServerMessage::Requested { session } = vrx.try_recv().unwrap() else {
            panic!("Missing invitation");
        };
        let _ = hrx.try_recv().unwrap();
        assert!(
            registry
                .handle(123_456_789, viewer, ClientMessage::Restart { session }, now)
                .is_err()
        );
        registry
            .handle(
                234_567_890,
                host,
                ClientMessage::Decide {
                    session,
                    accept: true,
                },
                now,
            )
            .unwrap();
        let _ = vrx.try_recv().unwrap();
        let _ = hrx.try_recv().unwrap();
        registry
            .handle(
                123_456_789,
                viewer,
                ClientMessage::Signal {
                    session,
                    payload: Signal::Offer("fixture-offer".into()),
                },
                now,
            )
            .unwrap();
        let _ = hrx.try_recv().unwrap();
        registry
            .handle(
                234_567_890,
                host,
                ClientMessage::Signal {
                    session,
                    payload: Signal::Answer("fixture-answer".into()),
                },
                now,
            )
            .unwrap();
        let _ = vrx.try_recv().unwrap();
        let deadline = registry.sessions[&session].active_until.unwrap();
        assert!(
            registry
                .handle(234_567_890, host, ClientMessage::Restart { session }, now)
                .is_err()
        );
        registry
            .handle(123_456_789, viewer, ClientMessage::Restart { session }, now)
            .unwrap();
        let ServerMessage::Restarted {
            previous,
            session: replacement,
            nonce,
            controller_identity,
            host_identity,
        } = vrx.try_recv().unwrap()
        else {
            panic!("Missing replacement");
        };
        assert_eq!(previous, session);
        assert_ne!(replacement, session);
        assert_ne!(nonce, [0; 32]);
        assert_eq!(controller_identity.identity.connection_id, 123_456_789);
        assert_eq!(host_identity.identity.connection_id, 234_567_890);
        let _ = hrx.try_recv().unwrap();
        assert_eq!(registry.counts(), (3, 1));
        registry
            .handle(123_456_789, viewer, ClientMessage::Close { session }, now)
            .unwrap();
        registry
            .handle(
                234_567_890,
                host,
                ClientMessage::Signal {
                    session,
                    payload: Signal::Ice("old-candidate".into()),
                },
                now,
            )
            .unwrap();
        assert!(
            registry
                .handle(345_678_901, outsider, ClientMessage::Close { session }, now)
                .is_err()
        );
        assert!(vrx.try_recv().is_err());
        assert!(hrx.try_recv().is_err());
        assert_eq!(registry.counts(), (3, 1));
        registry
            .handle(
                123_456_789,
                viewer,
                ClientMessage::Signal {
                    session: replacement,
                    payload: Signal::Offer("fresh-offer".into()),
                },
                now,
            )
            .unwrap();
        let _ = hrx.try_recv().unwrap();
        registry
            .handle(
                234_567_890,
                host,
                ClientMessage::Signal {
                    session: replacement,
                    payload: Signal::Answer("fresh-answer".into()),
                },
                now + Duration::from_secs(1),
            )
            .unwrap();
        let _ = vrx.try_recv().unwrap();
        assert_eq!(registry.sessions[&replacement].active_until, Some(deadline));
        assert_eq!(registry.sessions[&replacement].deadline, deadline);
        assert!(
            registry
                .handle(
                    123_456_789,
                    viewer,
                    ClientMessage::Restart {
                        session: replacement
                    },
                    now + Duration::from_secs(2)
                )
                .is_err()
        );
        registry.expire(deadline);
        assert_eq!(registry.counts(), (3, 0));
    }
}
