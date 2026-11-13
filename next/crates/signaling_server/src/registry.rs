//! Bounded registry. All mutation occurs under one short mutex; no network await
//! occurs while held. Connection generations prevent stale disconnects deleting
//! a replacement. Slow peers are disconnected, never given unbounded queues.
use crate::{
    auth::Principal,
    protocol::*,
    support_policy::{DeviceBinding, SessionLimit, SupportPair},
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
/// Second (and third) technicians in one running session.
const MAX_JOINED: usize = 2;
const MAX_INVITATIONS: usize = 256;
const INVITATION_SECONDS: u16 = 120;
struct Peer {
    generation: Uuid,
    principal: Principal,
    tx: mpsc::Sender<ServerMessage>,
    stop: watch::Sender<bool>,
    ice_next: Instant,
    identity: Option<core_protocol::delegation::DeviceTicket>,
    code_gate: Option<CodeGate>,
    unattended_enabled: bool,
    /// A device-only host that opted into attended requests (and out of unattended ones).
    attended_support: bool,
    /// Broker features the app announced (`join`, `switch`).
    features: Vec<String>,
}
impl Peer {
    fn unattended_host_only(&self) -> bool {
        self.identity
            .as_ref()
            .is_some_and(|ticket| ticket.unattended_host_only())
    }

    fn supports(&self, feature: &str) -> bool {
        self.features.iter().any(|name| name == feature)
    }

    fn can_request(&self, target: &Self, unattended: bool) -> bool {
        !self.unattended_host_only()
            && (unattended || !target.unattended_host_only() || target.attended_support)
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
    /// The Remote SaaS tenant whose concurrent-session limit this session counts against.
    limit_tenant: Option<Uuid>,
    /// Sessions of one support case share a group: the first technician's session and the
    /// sessions of colleagues who joined it. Stable across transport restarts.
    group: Uuid,
    /// A colleague who joined the group's running session (`Some(control asked)`).
    joined: Option<bool>,
    /// The sides were switched: the technician who is authorized to help is `host` here and
    /// the computer they help is `controller`. Policy checks use the authorized direction.
    reversed: bool,
}
impl Session {
    /// (technician, helped computer) as authorized by the support policy.
    fn authorized(&self) -> (u32, u32) {
        if self.reversed { (self.host, self.controller) } else { (self.controller, self.host) }
    }
}
struct Invitation {
    group: Uuid,
    host: u32,
    inviter: u32,
    recipients: Vec<u32>,
    control: bool,
    unattended: bool,
    expires_at: Instant,
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
    invitations: HashMap<Uuid, Invitation>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct PolicyCheck {
    pub pair: SupportPair,
    pub controller: (u32, Uuid),
    pub host: (u32, Uuid),
    pub unattended: bool,
}

impl Registry {
    /// Connection IDs of the enrolled devices connected right now (a verified device ticket whose
    /// account sign-in has not expired). Used for the "online / last seen" record; never sent to peers.
    pub fn online_devices(&self, unix: u64) -> Vec<u32> {
        let mut online: Vec<u32> = self
            .peers
            .iter()
            .filter(|(_, peer)| peer.identity.is_some() && peer.principal.expires_at > unix)
            .map(|(id, _)| *id)
            .collect();
        online.sort_unstable();
        online
    }

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
            ClientMessage::JoinSession { invitation } => {
                let invitation = self.invitations.get(invitation)?;
                if !invitation.recipients.contains(&from) {
                    return None;
                }
                (from, invitation.host, invitation.unattended)
            }
            ClientMessage::Decide {
                session,
                accept: true,
            } => {
                let current = self.sessions.get(session)?;
                if current.host != from || !matches!(current.phase, Phase::Consent) {
                    return None;
                }
                let (technician, helped) = current.authorized();
                (technician, helped, current.unattended)
            }
            ClientMessage::Restart { session } => {
                let current = self.sessions.get(session)?;
                if current.controller != from || !matches!(current.phase, Phase::Connected) {
                    return None;
                }
                let (technician, helped) = current.authorized();
                (technician, helped, current.unattended)
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
            .filter(|(_, session)| session.authorized().0 == controller)
            .map(|(id, session)| {
                (
                    *id,
                    self.policy_check(controller, session.authorized().1, session.unattended, unix),
                )
            })
            .collect()
    }
    pub(crate) fn policy_revoked(&mut self, session: Uuid, controller: (u32, Uuid), now: Instant) {
        if self
            .sessions
            .get(&session)
            .is_some_and(|room| room.authorized().0 == controller.0)
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
                attended_support: false,
                features: Vec::new(),
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
            // When the first technician's session ends, the colleagues who joined it end too.
            if s.joined.is_none() {
                let joined: Vec<Uuid> = self
                    .sessions
                    .iter()
                    .filter(|(_, room)| room.group == s.group && room.joined.is_some())
                    .map(|(id, _)| *id)
                    .collect();
                for room in joined {
                    self.close(room, CloseReason::Requested, now);
                }
                self.invitations.retain(|_, invitation| invitation.group != s.group);
            }
        }
    }
    /// The running first-technician session of a group, if any.
    fn primary(&self, group: Uuid) -> Option<(Uuid, &Session)> {
        self.sessions
            .iter()
            .find(|(_, room)| room.group == group && room.joined.is_none())
            .map(|(id, room)| (*id, room))
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
        self.invitations.retain(|_, invitation| now < invitation.expires_at);
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
        self.handle_limited(from, generation, message, now, None)
    }
    /// Sessions of one Remote SaaS tenant on this broker, in any phase (pending consent included).
    pub fn tenant_sessions(&self, tenant: Uuid) -> usize {
        self.sessions
            .values()
            .filter(|session| session.limit_tenant == Some(tenant))
            .count()
    }
    /// Like `handle`; a new session request under `limit` is refused with `Busy` once the tenant
    /// holds `max_sessions` sessions, and the new session counts against that tenant.
    pub fn handle_limited(
        &mut self,
        from: u32,
        generation: Uuid,
        message: ClientMessage,
        now: Instant,
        limit: Option<SessionLimit>,
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
                let limit_tenant = current.limit_tenant;
                let (group, joined, reversed) = (current.group, current.joined, current.reversed);
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
                        limit_tenant,
                        group,
                        joined,
                        reversed,
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
                peer.unattended_enabled = enabled && peer.code_gate.is_none() && !peer.attended_support;
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
            ClientMessage::ConfigureSupportHost { enabled } => {
                // Only for device-only hosts, and never together with unattended access: such a
                // host is reachable by attended requests alone, so someone at the device decides.
                let Some(peer) = self
                    .peers
                    .get_mut(&from)
                    .filter(|peer| peer.unattended_host_only() && !peer.unattended_enabled)
                else {
                    return Err(ErrorCode::Forbidden);
                };
                peer.attended_support = enabled;
                if !enabled {
                    let rooms: Vec<_> = self
                        .sessions
                        .iter()
                        .filter_map(|(id, room)| (room.host == from && !room.unattended).then_some(*id))
                        .collect();
                    for room in rooms {
                        self.close(room, CloseReason::Requested, now);
                    }
                }
                self.send(from, ServerMessage::SupportHostStatus { enabled });
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
                if let Some(limit) = limit
                    && self.tenant_sessions(limit.tenant) >= limit.max_sessions as usize
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
                        limit_tenant: limit.map(|limit| limit.tenant),
                        group: session,
                        joined: None,
                        reversed: false,
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
                        joining: None,
                        switched: None,
                    },
                );
                Ok(())
            }
            ClientMessage::Capabilities { features } => {
                if features.len() > 16 || features.iter().any(|name| name.is_empty() || name.len() > 16 || !name.bytes().all(|byte| byte.is_ascii_lowercase())) {
                    return Err(ErrorCode::Invalid);
                }
                let peer = self.peers.get_mut(&from).ok_or(ErrorCode::Forbidden)?;
                peer.features = features;
                Ok(())
            }
            ClientMessage::Invite { session, user, device, control } => {
                let s = self.sessions.get(&session).ok_or(ErrorCode::Forbidden)?;
                if from != s.controller || s.joined.is_some() || s.reversed || !matches!(s.phase, Phase::Connected) {
                    return Err(ErrorCode::Forbidden);
                }
                if user.is_some() == device.is_some() {
                    return Err(ErrorCode::Invalid);
                }
                let (group, host, unattended) = (s.group, s.host, s.unattended);
                let target = self.peers.get(&host).ok_or(ErrorCode::Unavailable)?;
                if !target.supports(FEATURE_JOIN) {
                    return Err(ErrorCode::Unavailable);
                }
                let joined = self.sessions.values().filter(|room| room.group == group && room.joined.is_some()).count();
                if joined >= MAX_JOINED
                    || self.invitations.len() >= MAX_INVITATIONS
                    || self.invitations.values().filter(|invitation| invitation.group == group).count() >= 4
                {
                    return Err(ErrorCode::Busy);
                }
                let inviter = self.peers[&from].principal.user;
                // Only colleagues who may reach this computer themselves (same company, or staff).
                let mut recipients: Vec<u32> = self
                    .peers
                    .iter()
                    .filter(|(id, peer)| {
                        **id != from
                            && **id != host
                            && user.is_none_or(|user| peer.principal.user == user)
                            && device.is_none_or(|device| **id == device)
                            && peer.identity.is_some()
                            && peer.supports(FEATURE_JOIN)
                            && peer.can_request(target, unattended)
                            && (!unattended || target.unattended_enabled)
                    })
                    .map(|(id, _)| *id)
                    .take(8)
                    .collect();
                recipients.sort_unstable();
                let invitation = Uuid::new_v4();
                if !recipients.is_empty() {
                    for recipient in &recipients {
                        self.send(
                            *recipient,
                            ServerMessage::Invited {
                                invitation,
                                host,
                                inviter,
                                inviter_connection: from,
                                control,
                                unattended,
                                expires_in: INVITATION_SECONDS,
                            },
                        );
                    }
                    self.invitations.insert(
                        invitation,
                        Invitation {
                            group,
                            host,
                            inviter: from,
                            recipients: recipients.clone(),
                            control,
                            unattended,
                            expires_at: now + Duration::from_secs(u64::from(INVITATION_SECONDS)),
                        },
                    );
                }
                self.send(from, ServerMessage::InviteStatus { invitation, delivered: recipients.len() as u16 });
                Ok(())
            }
            ClientMessage::JoinSession { invitation: id } => {
                let invitation = self.invitations.get(&id).ok_or(ErrorCode::Unavailable)?;
                if !invitation.recipients.contains(&from) {
                    return Err(ErrorCode::Forbidden);
                }
                let (group, host, inviter_connection, control, unattended) =
                    (invitation.group, invitation.host, invitation.inviter, invitation.control, invitation.unattended);
                let Some((primary, room)) = self.primary(group) else {
                    self.invitations.remove(&id);
                    return Err(ErrorCode::Unavailable);
                };
                if !matches!(room.phase, Phase::Connected) || room.controller != inviter_connection || room.host != host {
                    self.invitations.remove(&id);
                    return Err(ErrorCode::Unavailable);
                }
                let requester = &self.peers[&from];
                let target = self.peers.get(&host).ok_or(ErrorCode::Unavailable)?;
                if !requester.can_request(target, unattended) || (unattended && !target.unattended_enabled) {
                    return Err(ErrorCode::Unavailable);
                }
                if self.sessions.values().any(|room| room.group == group && room.controller == from) {
                    return Err(ErrorCode::Busy);
                }
                if self.sessions.len() >= MAX_SESSIONS
                    || self.sessions.values().filter(|room| room.group == group && room.joined.is_some()).count() >= MAX_JOINED
                    || [from, host].iter().any(|peer| {
                        self.sessions.values().filter(|s| s.host == *peer || s.controller == *peer).count() >= MAX_PER_PEER
                    })
                {
                    return Err(ErrorCode::Busy);
                }
                if let Some(limit) = limit
                    && self.tenant_sessions(limit.tenant) >= limit.max_sessions as usize
                {
                    return Err(ErrorCode::Busy);
                }
                let inviter = self.peers.get(&inviter_connection).map(|peer| peer.principal.user).ok_or(ErrorCode::Unavailable)?;
                self.invitations.remove(&id);
                let requester = self.peers[&from].principal.clone();
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
                        limit_tenant: limit.map(|limit| limit.tenant),
                        group,
                        joined: Some(control),
                        reversed: false,
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
                        requester_identity: self.peers.get(&from).and_then(|peer| peer.identity.clone()),
                        joining: Some(Joining { session: primary, inviter, inviter_connection, control }),
                        switched: None,
                    },
                );
                Ok(())
            }
            ClientMessage::SwitchSides { session } => {
                let s = self.sessions.get(&session).ok_or(ErrorCode::Forbidden)?;
                if (from != s.controller && from != s.host) || s.joined.is_some() || !matches!(s.phase, Phase::Connected) {
                    return Err(ErrorCode::Forbidden);
                }
                let (controller, host, group) = (s.controller, s.host, s.group);
                if ![controller, host].iter().all(|id| self.peers.get(id).is_some_and(|peer| peer.supports(FEATURE_SWITCH) && peer.identity.is_some())) {
                    return Err(ErrorCode::Unavailable);
                }
                // Colleagues watching would lose their picture: switch only in a one-to-one session.
                if self.sessions.values().any(|room| room.group == group && room.joined.is_some()) {
                    return Err(ErrorCode::Busy);
                }
                let (reversed, limit_tenant) = (!s.reversed, s.limit_tenant);
                let requester = self.peers[&host].principal.clone();
                let replacement = Uuid::new_v4();
                // The old session ends first (its pictures stop), then the switched one asks.
                self.close(session, CloseReason::Switched, now);
                self.sessions.insert(
                    replacement,
                    Session {
                        controller: host,
                        host: controller,
                        unattended: false,
                        phase: Phase::Consent,
                        deadline: now + Duration::from_secs(90),
                        identity_from_controller: false,
                        identity_from_host: false,
                        active_until: None,
                        restart_after: now,
                        limit_tenant,
                        group: replacement,
                        joined: None,
                        reversed,
                    },
                );
                self.send(host, ServerMessage::Switched { previous: session, session: replacement, host: controller });
                self.send(
                    controller,
                    ServerMessage::Consent {
                        session: replacement,
                        requester_connection: host,
                        requester: requester.user,
                        requester_tenant: requester.tenant,
                        unattended: false,
                        requester_identity: self.peers.get(&host).and_then(|peer| peer.identity.clone()),
                        joining: None,
                        switched: Some(SwitchedFrom { previous: session, by: from }),
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
    /// A device-only host as the phone Quick Support registers: no user, device ticket only.
    fn device_only_host(registry: &mut Registry, id: u32, profile: u128) -> (Uuid, mpsc::Receiver<ServerMessage>) {
        let (tx, rx) = mpsc::channel(64);
        let (stop, _) = watch::channel(false);
        let principal = Principal { user: Uuid::from_u128(profile), tenant: Some(Uuid::from_u128(1)), staff: false, expires_at: 220 };
        let (_, generation) = registry.register_with_id(id, principal, tx, stop).unwrap();
        let ticket = core_protocol::delegation::DeviceTicket::issue_unattended_host(
            core_protocol::device_login::DeviceLoginChallenge {
                nonce: [2; 32],
                device: *Uuid::from_u128(profile + 100).as_bytes(),
                profile: *Uuid::from_u128(profile).as_bytes(),
                tenant: Some(*Uuid::from_u128(1).as_bytes()),
                connection_id: id,
                generation: 1,
            },
            ed25519_dalek::SigningKey::from_bytes(&[8; 32]).verifying_key(),
            100,
            220,
            &ed25519_dalek::SigningKey::from_bytes(&[9; 32]),
        )
        .unwrap();
        registry.bind_identity(id, generation, ticket).unwrap();
        (generation, rx)
    }
    #[test]
    fn online_devices_lists_only_verified_unexpired_devices() {
        let mut registry = Registry::default();
        peer(&mut registry, 234_567_890, 11);
        peer(&mut registry, 123_456_789, 10);
        // A connection without a device ticket (not yet proven) is not counted.
        let (tx, _rx) = mpsc::channel(4);
        let (stop, _) = watch::channel(false);
        let principal = Principal { user: Uuid::from_u128(12), tenant: None, staff: false, expires_at: 900 };
        registry.register_with_id(345_678_901, principal, tx, stop).unwrap();
        assert_eq!(registry.online_devices(100), vec![123_456_789, 234_567_890]);
        // Expired sign-ins are not online.
        assert!(registry.online_devices(900).is_empty());
    }
    #[test]
    fn a_device_only_host_gets_attended_requests_only_after_it_opts_in() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (controller, mut controller_events) = peer(&mut registry, 123_456_789, 10);
        let (phone, mut phone_events) = device_only_host(&mut registry, 234_567_890, 11);
        while phone_events.try_recv().is_ok() {}
        // Before opting in, nobody reaches it with an attended request and it is not shown as online.
        assert_eq!(
            registry.handle(123_456_789, controller, ClientMessage::Request { host: 234_567_890 }, now),
            Err(ErrorCode::Unavailable)
        );
        registry.handle(123_456_789, controller, ClientMessage::Presence { hosts: vec![234_567_890] }, now).unwrap();
        while let Ok(event) = controller_events.try_recv() {
            if let ServerMessage::Presence { online } = event { assert!(online.is_empty()); }
        }
        // An ordinary host (with a signed-in user) cannot use the switch.
        assert_eq!(
            registry.handle(123_456_789, controller, ClientMessage::ConfigureSupportHost { enabled: true }, now),
            Err(ErrorCode::Forbidden)
        );
        registry.handle(234_567_890, phone, ClientMessage::ConfigureSupportHost { enabled: true }, now).unwrap();
        assert!(matches!(phone_events.try_recv().unwrap(), ServerMessage::SupportHostStatus { enabled: true }));
        // Opted in: unattended access stays off, whatever the host asks for.
        registry.handle(234_567_890, phone, ClientMessage::ConfigureUnattended { enabled: true }, now).unwrap();
        assert!(matches!(phone_events.try_recv().unwrap(), ServerMessage::UnattendedStatus { enabled: false }));
        assert_eq!(
            registry.handle(123_456_789, controller, ClientMessage::RequestUnattended { host: 234_567_890 }, now),
            Err(ErrorCode::Unavailable)
        );
        registry.handle(123_456_789, controller, ClientMessage::Presence { hosts: vec![234_567_890] }, now).unwrap();
        let mut seen = false;
        while let Ok(event) = controller_events.try_recv() {
            if let ServerMessage::Presence { online } = event { assert_eq!(online, vec![234_567_890]); seen = true; }
        }
        assert!(seen);
        // The attended request arrives as a question to the person at the device.
        registry.handle(123_456_789, controller, ClientMessage::Request { host: 234_567_890 }, now).unwrap();
        let ServerMessage::Consent { unattended, requester_connection, .. } = phone_events.try_recv().unwrap() else {
            panic!("missing consent");
        };
        assert!(!unattended);
        assert_eq!(requester_connection, 123_456_789);
        // The phone itself can never ask to control anything.
        assert_eq!(
            registry.handle(234_567_890, phone, ClientMessage::Request { host: 123_456_789 }, now),
            Err(ErrorCode::Unavailable)
        );
        // Opting out ends what was started this way.
        registry.handle(234_567_890, phone, ClientMessage::ConfigureSupportHost { enabled: false }, now).unwrap();
        assert_eq!(registry.counts().1, 0);
    }
    #[test]
    fn a_saas_tenant_holds_at_most_its_plan_sessions_at_once() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let tenant = Uuid::from_u128(77);
        let limit = Some(SessionLimit { tenant, max_sessions: 2 });
        let (controller, _c) = peer(&mut registry, 123_456_789, 10);
        let (other, _o) = peer(&mut registry, 123_456_790, 12);
        let (_h1, _e1) = peer(&mut registry, 234_567_890, 11);
        let (h2, _e2) = peer(&mut registry, 234_567_891, 13);
        let (_h3, _e3) = peer(&mut registry, 234_567_892, 14);
        registry
            .handle_limited(123_456_789, controller, ClientMessage::Request { host: 234_567_890 }, now, limit)
            .unwrap();
        registry
            .handle_limited(123_456_790, other, ClientMessage::Request { host: 234_567_891 }, now, limit)
            .unwrap();
        assert_eq!(registry.tenant_sessions(tenant), 2);
        // A third session of the same tenant waits until one ends, whoever asks.
        assert_eq!(
            registry.handle_limited(123_456_789, controller, ClientMessage::Request { host: 234_567_892 }, now, limit),
            Err(ErrorCode::Busy)
        );
        // Another tenant's limit and unlimited (linked company) requests are not affected.
        registry
            .handle_limited(
                123_456_789,
                controller,
                ClientMessage::Request { host: 234_567_892 },
                now,
                Some(SessionLimit { tenant: Uuid::from_u128(78), max_sessions: 1 }),
            )
            .unwrap();
        assert_eq!(registry.tenant_sessions(tenant), 2);
        // A plan with no sessions (read-only) refuses at once.
        assert_eq!(
            registry.handle_limited(
                123_456_790,
                other,
                ClientMessage::Request { host: 234_567_890 },
                now,
                Some(SessionLimit { tenant: Uuid::from_u128(79), max_sessions: 0 }),
            ),
            Err(ErrorCode::Busy)
        );
        // Ending a session frees its place.
        registry.disconnect(234_567_891, h2);
        assert_eq!(registry.tenant_sessions(tenant), 1);
        registry
            .handle_limited(123_456_789, controller, ClientMessage::Request { host: 234_567_890 }, now, limit)
            .unwrap();
        assert_eq!(registry.tenant_sessions(tenant), 2);
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

    /// Drains every queued event of one peer.
    fn drain(events: &mut mpsc::Receiver<ServerMessage>) -> Vec<ServerMessage> {
        let mut all = Vec::new();
        while let Ok(event) = events.try_recv() {
            all.push(event);
        }
        all
    }
    /// Takes a requested session through consent, identity, offer and answer.
    fn connect(registry: &mut Registry, now: Instant, controller: (u32, Uuid), host: (u32, Uuid), session: Uuid) {
        registry.handle(host.0, host.1, ClientMessage::Decide { session, accept: true }, now).unwrap();
        registry.handle(controller.0, controller.1, ClientMessage::Signal { session, payload: Signal::Offer("v=0".into()) }, now).unwrap();
        registry.handle(host.0, host.1, ClientMessage::Signal { session, payload: Signal::Answer("v=0".into()) }, now).unwrap();
    }
    fn features(registry: &mut Registry, id: u32, generation: Uuid) {
        registry
            .handle(id, generation, ClientMessage::Capabilities { features: vec![FEATURE_JOIN.into(), FEATURE_SWITCH.into()] }, Instant::now())
            .unwrap();
    }

    #[test]
    fn a_colleague_joins_a_running_session_and_leaves_with_it() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (technician, mut technician_events) = peer(&mut registry, 111_111_111, 10);
        let (host, mut host_events) = peer(&mut registry, 222_222_222, 11);
        let (colleague, mut colleague_events) = peer(&mut registry, 333_333_333, 12);
        let (stranger, mut stranger_events) = peer(&mut registry, 444_444_444, 13);
        for (id, generation) in [(111_111_111, technician), (222_222_222, host), (333_333_333, colleague)] {
            features(&mut registry, id, generation);
        }
        drain(&mut stranger_events);
        registry.handle(111_111_111, technician, ClientMessage::Request { host: 222_222_222 }, now).unwrap();
        let Some(ServerMessage::Requested { session }) = drain(&mut technician_events).pop() else { panic!() };
        // Inviting is only possible in a running session.
        assert_eq!(
            registry.handle(111_111_111, technician, ClientMessage::Invite { session, user: Some(Uuid::from_u128(12)), device: None, control: true }, now),
            Err(ErrorCode::Forbidden)
        );
        connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), session);
        drain(&mut technician_events);
        drain(&mut host_events);
        // The person helped cannot invite; exactly one target kind is required.
        assert_eq!(
            registry.handle(222_222_222, host, ClientMessage::Invite { session, user: Some(Uuid::from_u128(12)), device: None, control: true }, now),
            Err(ErrorCode::Forbidden)
        );
        assert_eq!(
            registry.handle(111_111_111, technician, ClientMessage::Invite { session, user: Some(Uuid::from_u128(12)), device: Some(333_333_333), control: true }, now),
            Err(ErrorCode::Invalid)
        );
        // A colleague whose app does not understand invitations is not reached.
        registry.handle(111_111_111, technician, ClientMessage::Invite { session, user: Some(Uuid::from_u128(13)), device: None, control: false }, now).unwrap();
        assert!(matches!(drain(&mut technician_events).as_slice(), [ServerMessage::InviteStatus { delivered: 0, .. }]));
        assert!(drain(&mut stranger_events).is_empty());
        registry.handle(111_111_111, technician, ClientMessage::Invite { session, user: Some(Uuid::from_u128(12)), device: None, control: true }, now).unwrap();
        assert!(matches!(drain(&mut technician_events).as_slice(), [ServerMessage::InviteStatus { delivered: 1, .. }]));
        let Some(ServerMessage::Invited { invitation, host: invited_host, control: true, inviter, .. }) = drain(&mut colleague_events).pop() else { panic!() };
        assert_eq!((invited_host, inviter), (222_222_222, Uuid::from_u128(10)));
        // Only the invited colleague can use the invitation.
        assert_eq!(registry.handle(444_444_444, stranger, ClientMessage::JoinSession { invitation }, now), Err(ErrorCode::Forbidden));
        registry.handle(333_333_333, colleague, ClientMessage::JoinSession { invitation }, now).unwrap();
        let Some(ServerMessage::Requested { session: joined }) = drain(&mut colleague_events).pop() else { panic!() };
        let Some(ServerMessage::Consent { session: asked, requester_connection: 333_333_333, joining: Some(joining), switched: None, .. }) = drain(&mut host_events).pop() else { panic!() };
        assert_eq!((asked, joining.session, joining.inviter, joining.control), (joined, session, Uuid::from_u128(10), true));
        // An invitation is used once.
        assert_eq!(registry.handle(333_333_333, colleague, ClientMessage::JoinSession { invitation }, now), Err(ErrorCode::Unavailable));
        connect(&mut registry, now, (333_333_333, colleague), (222_222_222, host), joined);
        assert_eq!(registry.counts().1, 2);
        // Sides cannot be switched while a colleague watches.
        assert_eq!(registry.handle(111_111_111, technician, ClientMessage::SwitchSides { session }, now), Err(ErrorCode::Busy));
        // The colleague leaving keeps the first session; the first session ending ends the joined one.
        drain(&mut colleague_events);
        registry.handle(111_111_111, technician, ClientMessage::Close { session }, now).unwrap();
        assert!(drain(&mut colleague_events).iter().any(|event| matches!(event, ServerMessage::Closed { session: closed, .. } if *closed == joined)));
        assert_eq!(registry.counts().1, 0);
    }

    #[test]
    fn a_joined_session_survives_a_transport_restart_of_the_first_session() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (technician, mut technician_events) = peer(&mut registry, 111_111_111, 10);
        let (host, mut host_events) = peer(&mut registry, 222_222_222, 11);
        let (colleague, mut colleague_events) = peer(&mut registry, 333_333_333, 12);
        for (id, generation) in [(111_111_111, technician), (222_222_222, host), (333_333_333, colleague)] {
            features(&mut registry, id, generation);
        }
        registry.handle(111_111_111, technician, ClientMessage::Request { host: 222_222_222 }, now).unwrap();
        let Some(ServerMessage::Requested { session }) = drain(&mut technician_events).pop() else { panic!() };
        connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), session);
        registry.handle(111_111_111, technician, ClientMessage::Invite { session, user: None, device: Some(333_333_333), control: false }, now).unwrap();
        let Some(ServerMessage::Invited { invitation, control: false, .. }) = drain(&mut colleague_events).pop() else { panic!() };
        registry.handle(333_333_333, colleague, ClientMessage::JoinSession { invitation }, now).unwrap();
        let Some(ServerMessage::Requested { session: joined }) = drain(&mut colleague_events).pop() else { panic!() };
        drain(&mut host_events);
        connect(&mut registry, now, (333_333_333, colleague), (222_222_222, host), joined);
        let later = now + Duration::from_secs(31);
        registry.handle(111_111_111, technician, ClientMessage::Restart { session }, later).unwrap();
        let Some(ServerMessage::Restarted { session: replacement, .. }) = drain(&mut technician_events).pop() else { panic!() };
        assert_eq!(registry.counts().1, 2);
        // The replacement is still the group's first session: ending it ends the colleague's too.
        registry.handle(222_222_222, host, ClientMessage::Close { session: replacement }, later).unwrap();
        assert_eq!(registry.counts().1, 0);
        // Invitations for a finished session are gone.
        assert_eq!(registry.invitations.len(), 0);
    }

    #[test]
    fn switching_sides_reverses_the_session_and_keeps_the_authorized_direction() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (technician, mut technician_events) = peer(&mut registry, 111_111_111, 10);
        let (host, mut host_events) = peer(&mut registry, 222_222_222, 11);
        registry.handle(111_111_111, technician, ClientMessage::Request { host: 222_222_222 }, now).unwrap();
        let Some(ServerMessage::Requested { session }) = drain(&mut technician_events).pop() else { panic!() };
        // Not before the session runs, and only between apps that understand it.
        assert_eq!(registry.handle(111_111_111, technician, ClientMessage::SwitchSides { session }, now), Err(ErrorCode::Forbidden));
        connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), session);
        assert_eq!(registry.handle(111_111_111, technician, ClientMessage::SwitchSides { session }, now), Err(ErrorCode::Unavailable));
        features(&mut registry, 111_111_111, technician);
        features(&mut registry, 222_222_222, host);
        drain(&mut technician_events);
        drain(&mut host_events);
        registry.handle(111_111_111, technician, ClientMessage::SwitchSides { session }, now).unwrap();
        // The helped computer now views; the technician's computer is asked (it pressed the button).
        let host_side = drain(&mut host_events);
        assert!(matches!(host_side.first(), Some(ServerMessage::Closed { reason: CloseReason::Switched, .. })));
        let Some(ServerMessage::Switched { previous, session: reversed, host: 111_111_111 }) = host_side.last().cloned() else { panic!() };
        assert_eq!(previous, session);
        let technician_side = drain(&mut technician_events);
        let Some(ServerMessage::Consent { session: asked, requester_connection: 222_222_222, switched: Some(from), joining: None, .. }) = technician_side.last().cloned() else { panic!() };
        assert_eq!((asked, from.previous, from.by), (reversed, session, 111_111_111));
        // The policy is asked about the technician helping the computer, never the other way round.
        let check = registry
            .policy_for_message(111_111_111, technician, &ClientMessage::Decide { session: reversed, accept: true }, 100)
            .unwrap();
        assert_eq!((check.controller.0, check.host.0), (111_111_111, 222_222_222));
        assert!(registry.controlled_policy_checks(111_111_111, technician, 100).iter().any(|(id, check)| *id == reversed && check.is_some()));
        assert!(registry.controlled_policy_checks(222_222_222, host, 100).is_empty());
        // A policy revocation for the technician ends the switched session too.
        registry.policy_revoked(reversed, (111_111_111, technician), now);
        assert_eq!(registry.counts().1, 0);
    }

    #[test]
    fn capabilities_are_bounded() {
        let mut registry = Registry::default();
        let (generation, _events) = peer(&mut registry, 111_111_111, 10);
        for bad in [vec!["".to_owned()], vec!["Join".to_owned()], vec!["a".repeat(17)], vec!["x".to_owned(); 17]] {
            assert_eq!(registry.handle(111_111_111, generation, ClientMessage::Capabilities { features: bad }, Instant::now()), Err(ErrorCode::Invalid));
        }
    }
}
