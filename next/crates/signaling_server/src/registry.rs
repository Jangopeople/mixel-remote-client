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
    collections::{HashMap, VecDeque},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio::sync::{mpsc, watch};
use uuid::Uuid;

/// Connections and sessions a registry holds without `with_capacity` (tests; the broker sets
/// `MIXEL_MAX_CONNECTIONS`, see `limits.rs`).
const DEFAULT_CAPACITY: usize = 1000;
const MAX_PER_PEER: usize = 64;
const MAX_PRESENCE: usize = 64;
/// Second (and third) technicians in one running session.
const MAX_JOINED: usize = 2;
const MAX_INVITATIONS: usize = 256;
const INVITATION_SECONDS: u16 = 120;
/// Session events waiting for the reporter (`sessions.rs`); the oldest are dropped beyond this.
pub const MAX_SESSION_EVENTS: usize = 10_000;

/// A Remote SaaS session for the company's session report (SQL 029, `remote_session_report`).
/// `start` once the session first connects, `end` when it ends for good (a transport restart is
/// neither). `technician` is the profile authorized to help (also when the sides are switched),
/// `device` the helped computer. Times are Unix seconds.
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SessionEvent {
    Start {
        id: Uuid,
        tenant: Uuid,
        technician: Uuid,
        controller_device: Uuid,
        controller_connection: u32,
        device: Uuid,
        connection: u32,
        unattended: bool,
        colleague: bool,
        at: u64,
    },
    End {
        id: Uuid,
        at: u64,
    },
}

/// Seconds from `now` to `until` for the wire (0 when past, at most `u16::MAX - 1`, never the old
/// "until turned off" value).
fn remaining_seconds(until: Instant, now: Instant) -> u16 {
    u16::try_from(until.saturating_duration_since(now).as_secs()).unwrap_or(u16::MAX - 1).min(u16::MAX - 1)
}

fn unix_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |time| time.as_secs())
}

/// Bounded queue of session events; nothing is kept while reporting is off.
#[derive(Default)]
struct SessionJournal {
    enabled: bool,
    events: VecDeque<SessionEvent>,
    dropped: u64,
}
impl SessionJournal {
    fn push(&mut self, event: SessionEvent) {
        if !self.enabled {
            return;
        }
        if self.events.len() >= MAX_SESSION_EVENTS {
            self.events.pop_front();
            self.dropped += 1;
            if self.dropped == 1 || self.dropped.is_multiple_of(1000) {
                eprintln!(
                    "session report queue full: {} oldest session events dropped",
                    self.dropped
                );
            }
        }
        self.events.push_back(event);
    }
}
/// A session may ask for relay credentials once per this interval.
const ICE_SESSION_INTERVAL: Duration = Duration::from_secs(5);
/// One connection may be issued at most `ICE_BURST_MAX` relay credentials per `ICE_BURST_WINDOW`
/// (the plans allow at most 10 sessions at once; a few reconnects fit).
const ICE_BURST_MAX: usize = 24;
const ICE_BURST_WINDOW: Duration = Duration::from_secs(60);
/// Limits relay (TURN) credentials of one connection: once per session per 5 s, and a burst cap across
/// all its sessions.
#[derive(Default)]
struct IceLimiter {
    next: HashMap<Uuid, Instant>,
    issued: VecDeque<Instant>,
}
impl IceLimiter {
    /// True (and recorded) when `session` may be issued credentials at `now`.
    fn allow(&mut self, session: Uuid, now: Instant) -> bool {
        self.next.retain(|_, next| *next > now);
        self.issued.retain(|at| now.duration_since(*at) < ICE_BURST_WINDOW);
        if self.next.contains_key(&session) || self.issued.len() >= ICE_BURST_MAX {
            return false;
        }
        self.next.insert(session, now + ICE_SESSION_INTERVAL);
        self.issued.push_back(now);
        true
    }
}
/// Leases for a transport that waits for a request: one every 10 s, at most 8 in 10 minutes per connection.
#[derive(Default)]
struct WarmLimiter {
    issued: VecDeque<Instant>,
}
impl WarmLimiter {
    fn allow(&mut self, now: Instant) -> bool {
        self.issued.retain(|at| now.duration_since(*at) < Duration::from_secs(600));
        if self.issued.back().is_some_and(|last| now.duration_since(*last) < Duration::from_secs(10)) || self.issued.len() >= 8 {
            return false;
        }
        self.issued.push_back(now);
        true
    }
}
struct Peer {
    generation: Uuid,
    principal: Principal,
    tx: mpsc::Sender<ServerMessage>,
    stop: watch::Sender<bool>,
    ice: IceLimiter,
    warm: WarmLimiter,
    identity: Option<core_protocol::delegation::DeviceTicket>,
    code_gate: Option<CodeGate>,
    unattended_enabled: bool,
    /// A device-only host that opted into attended requests (and out of unattended ones).
    attended_support: bool,
    /// Broker features the app announced (`join`, `switch`).
    features: Vec<String>,
    /// App version and platform the app reported (`app_version`), for the Devices list.
    app_version: Option<(String, String)>,
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
/// D2: a support password starts sessions for 30 minutes after it was made. The apps get the
/// remaining time as `lifetime_seconds` (a countdown; 2.0.15 and older showed one already).
pub const CODE_KEEP: Duration = Duration::from_secs(30 * 60);
/// D2: a session started with a support password lasts at most this long once connected (also across
/// transport renewals, switching sides and joined colleagues). Unattended sessions are not limited.
pub const PASSWORD_SESSION: Duration = Duration::from_secs(2 * 60 * 60);
/// Longest any other session lasts before the apps have to ask again.
const SESSION_MAX: Duration = Duration::from_secs(8 * 3600);
/// `session_ending` warnings before the end of a password session (feature `ending`).
const ENDING_WARNINGS: [Duration; 2] = [Duration::from_secs(300), Duration::from_secs(60)];
/// After a broker restart, a session waits this long for both sides to `resume`.
pub const RESUME_WINDOW: Duration = Duration::from_secs(90);
/// A handover file older than this is ignored (the sessions it lists are long gone).
pub const HANDOVER_MAX_AGE_SECONDS: u64 = 120;
const CODE_ATTEMPTS: u8 = 5;

#[derive(Clone)]
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
    /// Session report record: stable across transport restarts, new for a switch of sides.
    record: Uuid,
    /// The record's start was journaled (so its end is journaled exactly once).
    recorded: bool,
    /// D2: started with a support password (limited to `PASSWORD_SESSION` once connected).
    password: bool,
    /// D2: the end already fixed for this support case (set when a switch of sides or a joined
    /// colleague continues a password session that has already connected).
    password_until: Option<Instant>,
    /// `session_ending` warnings already sent (index into `ENDING_WARNINGS`).
    warned: usize,
    /// Restored from the previous broker and waiting for both sides to `resume`.
    parked: Option<Parked>,
    /// A renewal under way (feature `renewagain`): the session it replaced, back in place if the renewed transport
    /// fails (`ClientMessage::RenewalFailed`).
    renewed_from: Option<Box<(Uuid, Session)>>,
}
#[derive(Clone, Copy, Debug)]
struct Parked {
    controller: HandoverSide,
    host: HandoverSide,
    controller_back: bool,
    host_back: bool,
}
/// One side of a session in the handover file: the connection and the signed identity behind it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HandoverSide {
    pub connection: u32,
    pub profile: Uuid,
    pub device: Uuid,
}
/// A running session saved at shutdown for the next broker (no secrets: no passwords, no tokens).
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HandoverSession {
    pub id: Uuid,
    pub controller: HandoverSide,
    pub host: HandoverSide,
    pub unattended: bool,
    pub password: bool,
    /// Seconds the session may still run (its `active_until`).
    pub remaining: u64,
    pub limit_tenant: Option<Uuid>,
    pub group: Uuid,
    pub joined: Option<bool>,
    pub reversed: bool,
    pub record: Uuid,
    pub recorded: bool,
    pub warned: usize,
}
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Handover {
    pub version: u8,
    pub saved_at: u64,
    pub sessions: Vec<HandoverSession>,
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
pub struct Registry {
    peers: HashMap<u32, Peer>,
    sessions: HashMap<Uuid, Session>,
    retired: HashMap<Uuid, Retired>,
    invitations: HashMap<Uuid, Invitation>,
    /// Support passwords that survive a reconnect of the same connection ID, until turned off.
    support_codes: HashMap<u32, CodeGate>,
    /// Most connections (peers) and most sessions at once, broker-wide.
    max_peers: usize,
    max_sessions: usize,
    journal: SessionJournal,
    /// Set only while `handle_limited_attended` runs: a plain request is handled as attended
    /// (the person at the host decides) although the host has unattended access on.
    force_attended: bool,
    /// Relay credentials before consent, only for apps that announced `early_ice` (`MIXEL_EARLY_ICE=1`).
    early_ice: bool,
    /// Relay leases without a session for apps that announced `prewarm` (`MIXEL_PREWARM=1`).
    prewarm: bool,
}
impl Default for Registry {
    fn default() -> Self {
        Self::with_capacity(DEFAULT_CAPACITY)
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct PolicyCheck {
    pub pair: SupportPair,
    pub controller: (u32, Uuid),
    pub host: (u32, Uuid),
    pub unattended: bool,
}

impl Registry {
    /// Starts keeping session events for the session report (the reporter calls this).
    pub fn enable_session_journal(&mut self) {
        self.journal.enabled = true;
    }

    #[cfg(test)]
    pub(crate) fn journal_for_test(&mut self, event: SessionEvent) {
        self.journal.push(event);
    }

    /// The session events since the last call, oldest first.
    pub fn take_session_events(&mut self) -> Vec<SessionEvent> {
        self.journal.events.drain(..).collect()
    }

    /// Record ids of the reported sessions running right now (each started, not yet ended).
    pub fn running_records(&self) -> Vec<Uuid> {
        let mut records: Vec<Uuid> = self
            .sessions
            .values()
            .filter(|session| session.recorded)
            .map(|session| session.record)
            .collect();
        records.sort_unstable();
        records.dedup();
        records
    }

    /// Journals the start of a SaaS session that has just connected for the first time.
    fn record_start(&mut self, session: Uuid) {
        if !self.journal.enabled {
            return;
        }
        let Some(room) = self.sessions.get(&session) else {
            return;
        };
        let Some(tenant) = room.limit_tenant else {
            return;
        };
        if room.recorded {
            return;
        }
        let (technician, helped) = room.authorized();
        let identity = |id: u32| {
            self.peers
                .get(&id)
                .and_then(|peer| peer.identity.as_ref())
                .map(|ticket| {
                    (
                        Uuid::from_bytes(ticket.identity.profile),
                        Uuid::from_bytes(ticket.identity.device),
                    )
                })
        };
        let (Some((profile, controller_device)), Some((_, device))) =
            (identity(technician), identity(helped))
        else {
            return;
        };
        let event = SessionEvent::Start {
            id: room.record,
            tenant,
            technician: profile,
            controller_device,
            controller_connection: technician,
            device,
            connection: helped,
            unattended: room.unattended,
            colleague: room.joined.is_some(),
            at: unix_seconds(),
        };
        if let Some(room) = self.sessions.get_mut(&session) {
            room.recorded = true;
        }
        self.journal.push(event);
    }

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
            || (unattended && !target.unattended_enabled)
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
            ClientMessage::Request { host } => {
                let unattended = self.peers.get(host).is_some_and(|peer| peer.unattended_enabled);
                (from, *host, unattended)
            }
            ClientMessage::RequestWithCode { host, .. } => {
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
            .filter(|(_, session)| session.authorized().0 == controller && session.parked.is_none())
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
    /// Switch for relay credentials while the session still waits for consent.
    pub fn set_early_ice(&mut self, enabled: bool) {
        self.early_ice = enabled;
    }
    /// Switch for relay leases without a session (a transport kept ready while the Devices list is shown).
    pub fn set_prewarm(&mut self, enabled: bool) {
        self.prewarm = enabled;
    }
    /// A relay lease for a transport that waits for a request: the gate, the announcement, a live
    /// authenticated and enrolled connection (checked by the caller), its own limiter and the sign-in
    /// expiry bound. No session, no peer and no address is involved.
    pub fn warm_permission(&mut self, id: u32, generation: Uuid, now: Instant, unix: u64) -> Result<u64, ErrorCode> {
        let peer = self.peers.get_mut(&id).ok_or(ErrorCode::Forbidden)?;
        if !self.prewarm
            || peer.generation != generation
            || !peer.features.iter().any(|name| name == crate::protocol::FEATURE_PREWARM)
            || peer.principal.expires_at.saturating_sub(unix) < 30
        {
            return Err(ErrorCode::Forbidden);
        }
        if !peer.warm.allow(now) {
            return Err(ErrorCode::Busy);
        }
        Ok(peer.principal.expires_at)
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
        if active.host != id && active.controller != id {
            return Err(ErrorCode::Forbidden);
        }
        let peer = self.peers.get_mut(&id).ok_or(ErrorCode::Forbidden)?;
        // Before consent only the two sides of a request that exists, authenticated, enrolled and
        // authorized (a refused request is gone), and only apps that announced `early_ice`.
        if matches!(active.phase, Phase::Consent)
            && !(self.early_ice && peer.features.iter().any(|name| name == crate::protocol::FEATURE_EARLY_ICE))
        {
            return Err(ErrorCode::Forbidden);
        }
        if peer.generation != generation || peer.principal.expires_at.saturating_sub(unix) < 30 {
            return Err(ErrorCode::Forbidden);
        }
        // Per session, so a technician can start several sessions back to back; a client asking
        // again for the same session within 5 s, or too many times overall, is still refused.
        if !peer.ice.allow(session, now) {
            return Err(ErrorCode::Busy);
        }
        Ok(peer.principal.expires_at)
    }
    /// A registry for at most `connections` peers and as many sessions at once (`limits.rs`).
    pub fn with_capacity(connections: usize) -> Self {
        Self {
            peers: HashMap::new(),
            sessions: HashMap::new(),
            retired: HashMap::new(),
            invitations: HashMap::new(),
            support_codes: HashMap::new(),
            max_peers: connections,
            max_sessions: connections,
            journal: SessionJournal::default(),
            force_attended: false,
            early_ice: false,
            prewarm: false,
        }
    }
    /// Changes the limits; connections and sessions already open stay.
    pub fn set_capacity(&mut self, connections: usize) {
        self.max_peers = connections;
        self.max_sessions = connections;
    }
    pub fn register(
        &mut self,
        principal: Principal,
        tx: mpsc::Sender<ServerMessage>,
        stop: watch::Sender<bool>,
    ) -> Result<(u32, Uuid), ErrorCode> {
        if self.peers.len() >= self.max_peers {
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
            || self.peers.len() >= self.max_peers
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
                ice: IceLimiter::default(),
                warm: WarmLimiter::default(),
                identity: None,
                code_gate: None,
                unattended_enabled: false,
                attended_support: false,
                features: Vec::new(),
                app_version: None,
            },
        );
        self.send(
            host,
            ServerMessage::Ready {
                host,
                protocol: PROTOCOL_VERSION,
            },
        );
        if let Some(gate) = self
            .support_codes
            .get(&host)
            .filter(|gate| Instant::now() < gate.expires_at && gate.code.is_some())
            .cloned()
        {
            let code = gate.code.clone();
            let remaining = remaining_seconds(gate.expires_at, Instant::now());
            if let Some(peer) = self.peers.get_mut(&host) {
                peer.code_gate = Some(gate);
            }
            self.send(
                host,
                ServerMessage::SupportCode {
                    required: true,
                    code,
                    lifetime_seconds: remaining,
                },
            );
        }
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
            if s.recorded {
                self.journal.push(SessionEvent::End {
                    id: s.record,
                    at: unix_seconds(),
                });
            }
            if self.retired.len() < self.max_sessions * 2 {
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
        self.support_codes
            .retain(|_, gate| now < gate.expires_at && gate.code.is_some());
        let expired: Vec<_> = self
            .sessions
            .iter()
            .filter(|(_, s)| now >= s.deadline)
            .map(|(id, _)| *id)
            .collect();
        for s in expired {
            self.close(s, CloseReason::Expired, now);
        }
        // D2: a support password that is over no longer starts sessions; the host's app is told.
        let lapsed: Vec<u32> = self
            .peers
            .iter()
            .filter(|(_, peer)| peer.code_gate.as_ref().is_some_and(|gate| gate.code.is_some() && now >= gate.expires_at))
            .map(|(id, _)| *id)
            .collect();
        for id in lapsed {
            if let Some(gate) = self.peers.get_mut(&id).and_then(|peer| peer.code_gate.as_mut()) {
                gate.code = None;
            }
            self.send(id, ServerMessage::SupportCode { required: true, code: None, lifetime_seconds: 0 });
        }
        // D2: warn both sides of a password session before its end (apps that announced `ending`).
        let mut warnings = Vec::new();
        for (id, room) in self.sessions.iter_mut() {
            let (true, Phase::Connected, Some(until), None) = (room.password, room.phase, room.active_until, room.parked) else {
                continue;
            };
            let left = until.saturating_duration_since(now);
            let mut due = None;
            while room.warned < ENDING_WARNINGS.len() && left <= ENDING_WARNINGS[room.warned] {
                room.warned += 1;
                due = Some(left);
            }
            if let Some(left) = due {
                warnings.push((*id, room.controller, room.host, left));
            }
        }
        for (session, controller, host, left) in warnings {
            for side in [controller, host] {
                if self.peers.get(&side).is_some_and(|peer| peer.supports(FEATURE_ENDING)) {
                    self.send(
                        side,
                        ServerMessage::SessionEnding {
                            session,
                            seconds: u32::try_from(left.as_secs()).unwrap_or(u32::MAX),
                            reason: EndingReason::SupportPasswordLimit,
                        },
                    );
                }
            }
        }
    }

    /// The app announced this broker feature.
    pub fn supports(&self, id: u32, generation: Uuid, feature: &str) -> bool {
        self.peers.get(&id).is_some_and(|peer| peer.generation == generation && peer.supports(feature))
    }

    /// App versions of the enrolled devices connected right now (for the presence report).
    pub fn device_versions(&self, unix: u64) -> Vec<(u32, String, String)> {
        let mut versions: Vec<(u32, String, String)> = self
            .peers
            .iter()
            .filter(|(_, peer)| peer.identity.is_some() && peer.principal.expires_at > unix)
            .filter_map(|(id, peer)| peer.app_version.clone().map(|(version, platform)| (*id, version, platform)))
            .collect();
        versions.sort_unstable();
        versions
    }

    /// The running sessions to hand to the next broker: connected, both sides with a signed identity.
    pub fn export_handover(&self, now: Instant, unix: u64) -> Handover {
        let side = |id: u32| {
            self.peers.get(&id).and_then(|peer| peer.identity.as_ref()).map(|ticket| HandoverSide {
                connection: id,
                profile: Uuid::from_bytes(ticket.identity.profile),
                device: Uuid::from_bytes(ticket.identity.device),
            })
        };
        let mut sessions: Vec<HandoverSession> = self
            .sessions
            .iter()
            .filter(|(_, room)| matches!(room.phase, Phase::Connected))
            .filter_map(|(id, room)| {
                let (controller, host) = match room.parked {
                    Some(parked) => (parked.controller, parked.host),
                    None => (side(room.controller)?, side(room.host)?),
                };
                let until = room.active_until?;
                (until > now).then(|| HandoverSession {
                    id: *id,
                    controller,
                    host,
                    unattended: room.unattended,
                    password: room.password,
                    remaining: until.saturating_duration_since(now).as_secs(),
                    limit_tenant: room.limit_tenant,
                    group: room.group,
                    joined: room.joined,
                    reversed: room.reversed,
                    record: room.record,
                    recorded: room.recorded,
                    warned: room.warned,
                })
            })
            .collect();
        sessions.sort_by_key(|session| session.id);
        Handover { version: 1, saved_at: unix, sessions }
    }

    /// Restores the sessions of the previous broker. Each waits `RESUME_WINDOW` for both sides to come
    /// back with the same connection, account and device and to send `resume`; otherwise it ends.
    /// Returns how many were restored.
    pub fn import_handover(&mut self, handover: Handover, now: Instant, unix: u64) -> usize {
        if handover.version != 1 || unix.saturating_sub(handover.saved_at) > HANDOVER_MAX_AGE_SECONDS || handover.saved_at > unix + 5 {
            return 0;
        }
        let elapsed = Duration::from_secs(unix.saturating_sub(handover.saved_at));
        let mut restored = 0;
        for session in handover.sessions {
            if self.sessions.len() >= self.max_sessions
                || self.sessions.contains_key(&session.id)
                || session.controller.connection == session.host.connection
                || session.warned > ENDING_WARNINGS.len()
            {
                continue;
            }
            let Some(left) = Duration::from_secs(session.remaining).checked_sub(elapsed).filter(|left| !left.is_zero()) else {
                continue;
            };
            let until = now + left.min(SESSION_MAX);
            self.sessions.insert(
                session.id,
                Session {
                    controller: session.controller.connection,
                    host: session.host.connection,
                    unattended: session.unattended,
                    phase: Phase::Connected,
                    deadline: (now + RESUME_WINDOW).min(until),
                    identity_from_controller: true,
                    identity_from_host: true,
                    active_until: Some(until),
                    restart_after: now,
                    limit_tenant: session.limit_tenant,
                    group: session.group,
                    joined: session.joined,
                    reversed: session.reversed,
                    record: session.record,
                    recorded: session.recorded,
                    password: session.password,
                    password_until: None,
                    warned: session.warned,
                    parked: Some(Parked {
                        controller: session.controller,
                        host: session.host,
                        controller_back: false,
                        host_back: false,
                    }),
                    renewed_from: None,
                },
            );
            restored += 1;
        }
        restored
    }

    /// Stops the broker for a restart: the connected sessions go to the handover (no `closed` is sent
    /// for them and their report stays open); every other session ends; every connection is told to stop.
    pub fn shutdown_for_handover(&mut self, now: Instant, unix: u64) -> Handover {
        let handover = self.export_handover(now, unix);
        for session in &handover.sessions {
            self.sessions.remove(&session.id);
        }
        self.shutdown();
        handover
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
    /// As [`Self::handle_limited`], for a plain `Request` the policy service allowed only as
    /// attended support: the host is asked for consent even when it has unattended access on.
    pub fn handle_limited_attended(
        &mut self,
        from: u32,
        generation: Uuid,
        message: ClientMessage,
        now: Instant,
        limit: Option<SessionLimit>,
    ) -> Result<(), ErrorCode> {
        if !matches!(message, ClientMessage::Request { .. }) {
            return Err(ErrorCode::Invalid);
        }
        self.force_attended = true;
        let result = self.handle_limited(from, generation, message, now, limit);
        self.force_attended = false;
        result
    }
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
        | ClientMessage::RenewalFailed { session }
        | ClientMessage::Restart { session } = &message
            && self
                .retired
                .get(session)
                .is_some_and(|old| from == old.controller || from == old.host)
        {
            return Ok(());
        }
        // A session restored after a broker restart carries nothing until both sides resumed it.
        if let ClientMessage::Signal { session, .. }
        | ClientMessage::Restart { session }
        | ClientMessage::Invite { session, .. }
        | ClientMessage::SwitchSides { session }
        | ClientMessage::Decide { session, .. } = &message
            && self.sessions.get(session).is_some_and(|room| room.parked.is_some())
        {
            return Err(ErrorCode::Forbidden);
        }
        match message {
            ClientMessage::Resume { session } => {
                let identity = self
                    .peers
                    .get(&from)
                    .and_then(|peer| peer.identity.as_ref())
                    .map(|ticket| (Uuid::from_bytes(ticket.identity.profile), Uuid::from_bytes(ticket.identity.device)))
                    .ok_or(ErrorCode::Forbidden)?;
                let room = self.sessions.get_mut(&session).ok_or(ErrorCode::Unavailable)?;
                let Some(parked) = room.parked.as_mut() else {
                    // Already running here (both sides resumed, or the other side resumed it for us).
                    return if room.controller == from || room.host == from { Ok(()) } else { Err(ErrorCode::Unavailable) };
                };
                let matches = |side: &HandoverSide| side.connection == from && (side.profile, side.device) == identity;
                if matches(&parked.controller) {
                    parked.controller_back = true;
                } else if matches(&parked.host) {
                    parked.host_back = true;
                } else {
                    return Err(ErrorCode::Unavailable);
                }
                if parked.controller_back && parked.host_back {
                    room.parked = None;
                    room.deadline = room.active_until.unwrap_or(now);
                    room.restart_after = now;
                }
                self.send(from, ServerMessage::Resumed { session });
                Ok(())
            }
            ClientMessage::AppVersion { version, platform } => {
                if !valid_app_version(&version, &platform) {
                    return Err(ErrorCode::Invalid);
                }
                let peer = self.peers.get_mut(&from).filter(|peer| peer.identity.is_some()).ok_or(ErrorCode::Forbidden)?;
                peer.app_version = Some((version, platform));
                Ok(())
            }
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
                    || self.retired.len() >= self.max_sessions * 2
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
                // A transport restart continues the same reported session.
                let (record, recorded) = (current.record, current.recorded);
                let (password, password_until, warned) = (current.password, current.password_until, current.warned);
                if current.parked.is_some() {
                    return Err(ErrorCode::Forbidden);
                }
                let until = current.active_until.ok_or(ErrorCode::Forbidden)?;
                if now >= until {
                    return Err(ErrorCode::Forbidden);
                }
                let replacement = Uuid::new_v4();
                let mut nonce = [0; 32];
                OsRng.fill_bytes(&mut nonce);
                let before = self.sessions.remove(&session);
                let renew_again = [controller, host]
                    .iter()
                    .all(|id| self.peers.get(id).is_some_and(|peer| peer.supports(crate::protocol::FEATURE_RENEW_AGAIN)));
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
                        record,
                        recorded,
                        password,
                        password_until,
                        warned,
                        parked: None,
                        renewed_from: before.filter(|_| renew_again).map(|mut before| {
                            before.renewed_from = None;
                            Box::new((session, before))
                        }),
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
            ClientMessage::RenewalFailed { session } => {
                // Only the replacement of a running session, reported by one of its sides. In any phase: the attempt often fails AFTER the
                // offer and answer went through (the new transport itself does not open), and a refusal here ended the session (lab, 18 Nov).
                let current = self.sessions.get(&session).ok_or(ErrorCode::Forbidden)?;
                if (from != current.controller && from != current.host) || current.renewed_from.is_none() {
                    return Err(ErrorCode::Forbidden);
                }
                let (controller, host) = (current.controller, current.host);
                let mut failed = self.sessions.remove(&session).ok_or(ErrorCode::Forbidden)?;
                let (before_id, mut before) = *failed.renewed_from.take().ok_or(ErrorCode::Forbidden)?;
                // The session before comes back as it was, and can be restarted again after a short pause.
                self.retired.remove(&before_id);
                before.restart_after = now + Duration::from_secs(5);
                self.sessions.insert(before_id, before);
                self.retired.insert(session, Retired { controller, host, deadline: now + Duration::from_secs(60) });
                let other = if from == controller { host } else { controller };
                self.send(other, ServerMessage::Closed { session, reason: CloseReason::Requested });
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
                peer.unattended_enabled = enabled && !peer.attended_support;
                let enabled = peer.unattended_enabled;
                if !enabled {
                    let rooms: Vec<_> = self
                        .sessions
                        .iter()
                        .filter_map(|(id, room)| {
                            (room.authorized().1 == from && room.unattended).then_some(*id)
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
                        .filter_map(|(id, room)| (room.authorized().1 == from && !room.unattended).then_some(*id))
                        .collect();
                    for room in rooms {
                        self.close(room, CloseReason::Requested, now);
                    }
                }
                self.send(from, ServerMessage::SupportHostStatus { enabled });
                Ok(())
            }
            ClientMessage::GenerateSupportCode | ClientMessage::ClearSupportCode => {
                let required = matches!(message, ClientMessage::GenerateSupportCode);
                let code = required.then(SupportCode::generate);
                let gate = code.clone().map(|code| CodeGate {
                    code: Some(code),
                    expires_at: now + CODE_KEEP,
                    attempts_left: CODE_ATTEMPTS,
                });
                {
                    let Some(peer) = self
                        .peers
                        .get_mut(&from)
                        .filter(|peer| peer.identity.is_some() && !peer.unattended_host_only())
                    else {
                        return Err(ErrorCode::Forbidden);
                    };
                    peer.code_gate = gate.clone();
                }
                match gate {
                    Some(gate) => {
                        self.support_codes.insert(from, gate);
                    }
                    None => {
                        self.support_codes.remove(&from);
                    }
                }
                self.send(
                    from,
                    ServerMessage::SupportCode {
                        required,
                        code,
                        lifetime_seconds: if required { remaining_seconds(now + CODE_KEEP, now) } else { 0 },
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
                let offered = match &message {
                    ClientMessage::RequestWithCode { code, .. } => Some(code),
                    _ => None,
                };
                let requester = &self.peers[&from];
                let target = self.peers.get(&host).ok_or(ErrorCode::Unavailable)?;
                // A plain request to a host with unattended access on is unattended when the
                // policy service allows that controller unattended access to this computer; when
                // it allows only attended support, the server handles it as attended
                // (`handle_limited_attended`) and the person at the host decides.
                let unattended = matches!(message, ClientMessage::RequestUnattended { .. })
                    || (offered.is_none() && target.unattended_enabled && !self.force_attended);
                if from == host
                    || !requester.can_request(target, unattended)
                    || (unattended && !target.unattended_enabled)
                {
                    return Err(ErrorCode::Unavailable);
                }
                if self.sessions.len() >= self.max_sessions
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
                let mut remembered = None;
                let mut rejected = false;
                {
                    let target = self.peers.get_mut(&host).ok_or(ErrorCode::Unavailable)?;
                    match (target.code_gate.as_mut(), offered) {
                        (None, None) => {}
                        (_, None) if unattended => {}
                        (Some(gate), Some(offered))
                            if now < gate.expires_at && gate.code.is_some() =>
                        {
                            if !gate.code.as_ref().is_some_and(|code| code.matches(offered)) {
                                gate.attempts_left = gate.attempts_left.saturating_sub(1);
                                rejected = true;
                                if gate.attempts_left == 0 {
                                    gate.code = None;
                                } else {
                                    remembered = Some(gate.clone());
                                }
                            } else {
                                // The same password starts sessions until its 30 minutes are over (D2).
                                gate.attempts_left = CODE_ATTEMPTS;
                                remembered = Some(gate.clone());
                            }
                        }
                        _ => return Err(ErrorCode::Unavailable),
                    }
                }
                if rejected && remembered.is_none() {
                    self.support_codes.remove(&host);
                    self.send(
                        host,
                        ServerMessage::SupportCode {
                            required: true,
                            code: None,
                            lifetime_seconds: 0,
                        },
                    );
                    return Err(ErrorCode::Unavailable);
                }
                if let Some(gate) = remembered {
                    self.support_codes.insert(host, gate);
                }
                if rejected {
                    return Err(ErrorCode::Unavailable);
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
                        record: Uuid::new_v4(),
                        recorded: false,
                        password: offered.is_some(),
                        password_until: None,
                        warned: 0,
                        parked: None,
                        renewed_from: None,
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
                        unattended: unattended || offered.is_some(),
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
                let (primary_password, primary_until) = (room.password, room.active_until);
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
                if self.sessions.len() >= self.max_sessions
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
                        record: Uuid::new_v4(),
                        recorded: false,
                        // A colleague of a password session ends with it (D2).
                        password: primary_password,
                        password_until: if primary_password { primary_until } else { None },
                        warned: 0,
                        parked: None,
                        renewed_from: None,
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
                // An unattended session stays unattended on both sides of the switch: the policy
                // keeps checking the unattended rules and the helped computer can still end it by
                // turning unattended access off.
                let (reversed, limit_tenant, unattended) = (!s.reversed, s.limit_tenant, s.unattended);
                // D2: switching sides does not extend a password session.
                let (password, password_until, warned) =
                    (s.password, if s.password { s.active_until } else { None }, s.warned);
                let requester = self.peers[&host].principal.clone();
                let replacement = Uuid::new_v4();
                // The old session ends first (its pictures stop), then the switched one asks.
                self.close(session, CloseReason::Switched, now);
                self.sessions.insert(
                    replacement,
                    Session {
                        controller: host,
                        host: controller,
                        unattended,
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
                        // Switched sides: a new session in the report.
                        record: Uuid::new_v4(),
                        recorded: false,
                        password,
                        password_until,
                        warned,
                        parked: None,
                        renewed_from: None,
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
                let mut connected = false;
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
                        let limit = if s.password {
                            s.password_until.unwrap_or(now + PASSWORD_SESSION)
                        } else {
                            now + SESSION_MAX
                        };
                        let until = *s.active_until.get_or_insert(limit);
                        s.deadline = until;
                        connected = true;
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
                if connected {
                    self.record_start(session);
                }
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
    fn relay_credentials_are_limited_per_session_with_a_burst_cap_per_connection() {
        let mut limiter = IceLimiter::default();
        let start = Instant::now();
        let sessions: Vec<Uuid> = (1..=30).map(Uuid::from_u128).collect();
        // Different sessions back to back are fine, up to the cap.
        for session in &sessions[..ICE_BURST_MAX] {
            assert!(limiter.allow(*session, start));
        }
        assert!(!limiter.allow(sessions[ICE_BURST_MAX], start), "the burst cap holds across sessions");
        // The same session is refused for 5 s, then allowed again; the cap frees after its window.
        let later = start + ICE_BURST_WINDOW + Duration::from_secs(1);
        assert!(limiter.allow(sessions[0], later));
        assert!(!limiter.allow(sessions[0], later + Duration::from_secs(4)));
        assert!(limiter.allow(sessions[0], later + Duration::from_secs(6)));
        assert!(limiter.next.len() <= 2, "expired entries are dropped");
    }
    #[test]
    fn the_connection_capacity_is_a_setting() {
        let principal = || Principal { user: Uuid::from_u128(12), tenant: None, staff: false, expires_at: 900 };
        let mut registry = Registry::with_capacity(2);
        let mut keep = Vec::new();
        let mut add = |registry: &mut Registry, id: u32| {
            let (tx, rx) = mpsc::channel(4);
            let (stop, stop_rx) = watch::channel(false);
            keep.push((rx, stop_rx));
            registry.register_with_id(id, principal(), tx, stop)
        };
        assert!(add(&mut registry, 100_000_001).is_ok());
        assert!(add(&mut registry, 100_000_002).is_ok());
        assert_eq!(add(&mut registry, 100_000_003).unwrap_err(), ErrorCode::Busy);
        registry.set_capacity(3);
        assert!(add(&mut registry, 100_000_003).is_ok(), "raising the limit keeps the open ones and admits more");
        assert_eq!(Registry::default().max_peers, DEFAULT_CAPACITY);
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
    fn five_wrong_support_passwords_lock_the_code_even_for_the_right_one() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (controller, mut controller_events) = peer(&mut registry, 123_456_789, 10);
        let (host, mut host_events) = peer(&mut registry, 234_567_890, 11);
        let _ = controller_events.try_recv().unwrap();
        let _ = host_events.try_recv().unwrap();
        registry.handle(234_567_890, host, ClientMessage::GenerateSupportCode, now).unwrap();
        let ServerMessage::SupportCode { code: Some(code), .. } = host_events.try_recv().unwrap() else { panic!("missing code") };
        let ask = |registry: &mut Registry, code: crate::protocol::SupportCode| {
            registry.handle(123_456_789, controller, ClientMessage::RequestWithCode { host: 234_567_890, code }, now)
        };
        for _ in 0..CODE_ATTEMPTS {
            let wrong = crate::protocol::SupportCode::parse("ABCDEF012345".into()).unwrap();
            assert!(ask(&mut registry, wrong).is_err(), "a wrong password is refused");
        }
        assert_eq!(ask(&mut registry, code.clone()), Err(ErrorCode::Unavailable), "locked: the right password no longer works either");
    }

    #[test]
    fn support_password_starts_sessions_for_thirty_minutes_then_needs_a_new_one() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (controller, mut controller_events) = peer(&mut registry, 123_456_789, 10);
        let (host, mut host_events) = peer(&mut registry, 234_567_890, 11);
        let (_, mut other_events) = peer(&mut registry, 345_678_901, 12);
        let _ = controller_events.try_recv().unwrap();
        let _ = host_events.try_recv().unwrap();
        let _ = other_events.try_recv().unwrap();
        registry
            .handle(234_567_890, host, ClientMessage::GenerateSupportCode, now)
            .unwrap();
        let ServerMessage::SupportCode {
            code: Some(code),
            lifetime_seconds,
            ..
        } = host_events.try_recv().unwrap()
        else {
            panic!("missing code");
        };
        // D2: a countdown of 30 minutes, never "until turned off".
        assert_eq!(lifetime_seconds, 1800);
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
        let request = || ClientMessage::RequestWithCode {
            host: 234_567_890,
            code: code.clone(),
        };
        registry
            .handle(123_456_789, controller, request(), now)
            .unwrap();
        let ServerMessage::Requested { session } = controller_events.try_recv().unwrap() else {
            panic!("missing first request");
        };
        assert!(matches!(host_events.try_recv().unwrap(), ServerMessage::Consent { .. }));
        registry
            .handle(123_456_789, controller, ClientMessage::Close { session }, now)
            .unwrap();
        let _ = controller_events.try_recv().unwrap();
        let _ = host_events.try_recv().unwrap();
        // The old five-minute deadline no longer consumes the password.
        registry
            .handle(
                123_456_789,
                controller,
                request(),
                now + Duration::from_secs(300),
            )
            .unwrap();
        let ServerMessage::Requested { session: again } = controller_events.try_recv().unwrap()
        else {
            panic!("missing second request");
        };
        assert_ne!(session, again);
        registry
            .handle(
                123_456_789,
                controller,
                ClientMessage::Close { session: again },
                now,
            )
            .unwrap();
        let generation = registry.peers.get(&234_567_890).unwrap().generation;
        registry.disconnect(234_567_890, generation);
        let (host, mut host_events) = peer(&mut registry, 234_567_890, 11);
        assert!(matches!(host_events.try_recv().unwrap(), ServerMessage::Ready { .. }));
        let ServerMessage::SupportCode {
            code: Some(restored),
            ..
        } = host_events.try_recv().unwrap()
        else {
            panic!("password was not restored");
        };
        assert_eq!(restored.reveal(), code.reveal());
        registry
            .handle(123_456_789, controller, request(), now + Duration::from_secs(300))
            .unwrap();
        // After 30 minutes the same password starts nothing, and the host's app is told.
        drain(&mut host_events);
        assert_eq!(
            registry.handle(123_456_789, controller, request(), now + CODE_KEEP + Duration::from_secs(1)),
            Err(ErrorCode::Unavailable)
        );
        assert!(drain(&mut host_events).iter().any(|event| matches!(
            event,
            ServerMessage::SupportCode { required: true, code: None, lifetime_seconds: 0 }
        )));
        registry
            .handle(234_567_890, host, ClientMessage::ClearSupportCode, now)
            .unwrap();
        assert_eq!(
            registry.handle(123_456_789, controller, request(), now),
            Err(ErrorCode::Unavailable)
        );
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

    fn announce_early_ice(registry: &mut Registry, id: u32, generation: Uuid) {
        registry
            .handle(id, generation, ClientMessage::Capabilities { features: vec![FEATURE_EARLY_ICE.into()] }, Instant::now())
            .unwrap();
    }

    #[test]
    fn relay_credentials_before_consent_need_the_gate_and_the_announcement_and_a_live_request() {
        let now = Instant::now();
        let mut registry = Registry::default();
        let (technician, mut technician_events) = peer(&mut registry, 111_111_111, 10);
        let (host, mut host_events) = peer(&mut registry, 222_222_222, 11);
        let (stranger, _stranger_events) = peer(&mut registry, 444_444_444, 13);
        registry.handle(111_111_111, technician, ClientMessage::Request { host: 222_222_222 }, now).unwrap();
        let Some(ServerMessage::Requested { session }) = drain(&mut technician_events).pop() else { panic!() };
        drain(&mut host_events);
        // Gate off (production today): nothing before consent, as before.
        assert_eq!(registry.ice_permission(111_111_111, technician, session, now, 100), Err(ErrorCode::Forbidden));
        registry.set_early_ice(true);
        // Gate on, but the app did not announce the feature: still nothing.
        assert_eq!(registry.ice_permission(111_111_111, technician, session, now, 100), Err(ErrorCode::Forbidden));
        announce_early_ice(&mut registry, 111_111_111, technician);
        announce_early_ice(&mut registry, 222_222_222, host);
        announce_early_ice(&mut registry, 444_444_444, stranger);
        // Both sides of the live request may ask; a stranger and an unknown session may not.
        assert!(registry.ice_permission(111_111_111, technician, session, now, 100).is_ok());
        assert!(registry.ice_permission(222_222_222, host, session, now, 100).is_ok());
        assert_eq!(registry.ice_permission(444_444_444, stranger, session, now, 100), Err(ErrorCode::Forbidden));
        assert_eq!(registry.ice_permission(111_111_111, technician, Uuid::from_u128(99), now, 100), Err(ErrorCode::Forbidden));
        // Abuse limit kept: the same session again within 5 s is Busy.
        assert_eq!(registry.ice_permission(111_111_111, technician, session, now, 100), Err(ErrorCode::Busy));
    }

    #[test]
    fn a_relay_lease_without_a_session_needs_the_gate_the_announcement_and_stays_limited() {
        let now = Instant::now();
        let mut registry = Registry::default();
        let (technician, _events) = peer(&mut registry, 111_111_111, 10);
        // Gate off (production today): nothing.
        assert_eq!(registry.warm_permission(111_111_111, technician, now, 100), Err(ErrorCode::Forbidden));
        registry.set_prewarm(true);
        // Gate on, feature not announced: nothing.
        assert_eq!(registry.warm_permission(111_111_111, technician, now, 100), Err(ErrorCode::Forbidden));
        registry
            .handle(111_111_111, technician, ClientMessage::Capabilities { features: vec![FEATURE_PREWARM.into()] }, now)
            .unwrap();
        // A wrong connection generation and an unknown connection get nothing.
        assert_eq!(registry.warm_permission(111_111_111, Uuid::from_u128(5), now, 100), Err(ErrorCode::Forbidden));
        assert_eq!(registry.warm_permission(333_333_333, technician, now, 100), Err(ErrorCode::Forbidden));
        // The announced, signed-in connection gets one, then must wait 10 s, then at most 8 in 10 minutes.
        assert!(registry.warm_permission(111_111_111, technician, now, 100).is_ok());
        assert_eq!(registry.warm_permission(111_111_111, technician, now + Duration::from_secs(5), 100), Err(ErrorCode::Busy));
        let mut granted = 1;
        for step in 1..20u64 {
            if registry.warm_permission(111_111_111, technician, now + Duration::from_secs(11 * step), 100).is_ok() {
                granted += 1;
            }
        }
        assert_eq!(granted, 8, "at most 8 leases in 10 minutes");
        // Its sign-in about to expire: nothing.
        let later = now + Duration::from_secs(2000);
        assert_eq!(registry.warm_permission(111_111_111, technician, later, u64::MAX / 2), Err(ErrorCode::Forbidden));
    }

    #[test]
    fn a_refused_request_gets_no_relay_credentials() {
        let now = Instant::now();
        let mut registry = Registry::default();
        registry.set_early_ice(true);
        let (technician, mut technician_events) = peer(&mut registry, 111_111_111, 10);
        let (host, mut host_events) = peer(&mut registry, 222_222_222, 11);
        announce_early_ice(&mut registry, 111_111_111, technician);
        announce_early_ice(&mut registry, 222_222_222, host);
        registry.handle(111_111_111, technician, ClientMessage::Request { host: 222_222_222 }, now).unwrap();
        let Some(ServerMessage::Requested { session }) = drain(&mut technician_events).pop() else { panic!() };
        drain(&mut host_events);
        registry.handle(222_222_222, host, ClientMessage::Decide { session, accept: false }, now).unwrap();
        assert_eq!(registry.ice_permission(111_111_111, technician, session, now, 100), Err(ErrorCode::Forbidden));
        assert_eq!(registry.ice_permission(222_222_222, host, session, now, 100), Err(ErrorCode::Forbidden));
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

    type Renewed = (Registry, (Uuid, Uuid), (u32, Uuid), (u32, Uuid), mpsc::Receiver<ServerMessage>, mpsc::Receiver<ServerMessage>, Instant);
    fn renewal_after_a_failed_attempt(announce: bool) -> Renewed {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (technician, mut technician_events) = peer(&mut registry, 111_111_111, 10);
        let (host, mut host_events) = peer(&mut registry, 222_222_222, 11);
        for (id, generation) in [(111_111_111, technician), (222_222_222, host)] {
            let features = if announce { vec![crate::protocol::FEATURE_RENEW_AGAIN.into()] } else { vec![] };
            registry.handle(id, generation, ClientMessage::Capabilities { features }, now).unwrap();
        }
        registry.handle(111_111_111, technician, ClientMessage::Request { host: 222_222_222 }, now).unwrap();
        let Some(ServerMessage::Requested { session }) = drain(&mut technician_events).pop() else { panic!() };
        connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), session);
        drain(&mut host_events);
        let later = now + Duration::from_secs(31);
        registry.handle(111_111_111, technician, ClientMessage::Restart { session }, later).unwrap();
        let Some(ServerMessage::Restarted { session: replacement, .. }) = drain(&mut technician_events).pop() else { panic!() };
        drain(&mut host_events);
        (registry, (session, replacement), (111_111_111, technician), (222_222_222, host), technician_events, host_events, later)
    }

    #[test]
    fn a_failed_renewal_attempt_returns_the_session_to_the_transport_before_and_it_restarts_again() {
        let (mut registry, (session, replacement), technician, host, mut technician_events, mut host_events, later) = renewal_after_a_failed_attempt(true);
        // The host's side of the new transport failed.
        registry.handle(host.0, host.1, ClientMessage::RenewalFailed { session: replacement }, later).unwrap();
        assert!(drain(&mut technician_events).iter().any(|event| matches!(event, ServerMessage::Closed { session: closed, .. } if *closed == replacement)));
        assert!(drain(&mut host_events).is_empty(), "the reporter is not told");
        assert_eq!(registry.counts().1, 1);
        // A second report (the other side) is ignored, not an error.
        registry.handle(technician.0, technician.1, ClientMessage::RenewalFailed { session: replacement }, later).unwrap();
        // Too early: the pause; later the restart of the transport before is answered as usual.
        assert!(registry.handle(technician.0, technician.1, ClientMessage::Restart { session }, later + Duration::from_secs(1)).is_err());
        let again = later + Duration::from_secs(6);
        registry.handle(technician.0, technician.1, ClientMessage::Restart { session }, again).unwrap();
        let Some(ServerMessage::Restarted { session: next, previous, .. }) = drain(&mut technician_events).pop() else { panic!() };
        assert_eq!(previous, session);
        assert_ne!(next, replacement);
        assert_eq!(registry.counts().1, 1);
        // The new attempt can fail and be reported again.
        registry.handle(technician.0, technician.1, ClientMessage::RenewalFailed { session: next }, again).unwrap();
        assert_eq!(registry.counts().1, 1);
    }

    #[test]
    fn a_failed_renewal_is_taken_back_after_the_offer_and_answer_went_through() {
        let (mut registry, (session, replacement), technician, host, mut technician_events, _, later) = renewal_after_a_failed_attempt(true);
        // The attempt got as far as the answer (the new transport itself did not open).
        registry.sessions.get_mut(&replacement).unwrap().phase = Phase::Connected;
        registry.handle(host.0, host.1, ClientMessage::RenewalFailed { session: replacement }, later).unwrap();
        assert!(drain(&mut technician_events).iter().any(|event| matches!(event, ServerMessage::Closed { session: closed, .. } if *closed == replacement)));
        let again = later + Duration::from_secs(6);
        registry.handle(technician.0, technician.1, ClientMessage::Restart { session }, again).unwrap();
        assert_eq!(registry.counts().1, 1);
    }

    #[test]
    fn a_failed_renewal_attempt_is_not_undone_for_apps_that_did_not_announce_it() {
        let (mut registry, (session, replacement), technician, host, _, _, later) = renewal_after_a_failed_attempt(false);
        assert!(registry.handle(host.0, host.1, ClientMessage::RenewalFailed { session: replacement }, later).is_err());
        // The old way: closing the attempt ends the session at the service, and a restart of the old one is refused.
        registry.handle(host.0, host.1, ClientMessage::Close { session: replacement }, later).unwrap();
        assert_eq!(registry.counts().1, 0);
        let _ = registry.handle(technician.0, technician.1, ClientMessage::Restart { session }, later + Duration::from_secs(10));
        assert_eq!(registry.counts().1, 0, "nothing to restart");
    }

    #[test]
    fn only_a_negotiating_replacement_of_the_own_session_can_be_reported_failed() {
        let (mut registry, (session, replacement), _technician, host, _, _, later) = renewal_after_a_failed_attempt(true);
        let (stranger, _) = peer(&mut registry, 345_678_901, 12);
        assert!(registry.handle(345_678_901, stranger, ClientMessage::RenewalFailed { session: replacement }, later).is_err());
        // The transport before is retired and no longer the session: reporting it fails nothing.
        assert!(registry.handle(host.0, host.1, ClientMessage::RenewalFailed { session }, later).is_ok());
        assert_eq!(registry.counts().1, 1);
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

    /// Switching sides in an unattended session (nobody at the helped computer) must not turn it
    /// into an "attended" one: the switched sessions keep the unattended rules (policy checks and
    /// the helped computer turning unattended access off ends them).
    #[test]
    fn switching_sides_keeps_an_unattended_session_unattended() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (technician, mut technician_events) = peer(&mut registry, 111_111_111, 10);
        let (host, mut host_events) = peer(&mut registry, 222_222_222, 11);
        features(&mut registry, 111_111_111, technician);
        features(&mut registry, 222_222_222, host);
        registry.handle(222_222_222, host, ClientMessage::ConfigureUnattended { enabled: true }, now).unwrap();
        registry.handle(111_111_111, technician, ClientMessage::RequestUnattended { host: 222_222_222 }, now).unwrap();
        let Some(ServerMessage::Requested { session }) = drain(&mut technician_events).pop() else { panic!() };
        connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), session);
        drain(&mut host_events);
        // Switch: the helped computer views the technician's screen.
        registry.handle(111_111_111, technician, ClientMessage::SwitchSides { session }, now).unwrap();
        let Some(ServerMessage::Switched { session: reversed, .. }) = drain(&mut host_events).pop() else { panic!() };
        let check = registry
            .policy_for_message(111_111_111, technician, &ClientMessage::Decide { session: reversed, accept: true }, 100)
            .unwrap();
        assert!(check.unattended, "the switched session is still checked under the unattended rules");
        connect(&mut registry, now, (222_222_222, host), (111_111_111, technician), reversed);
        drain(&mut technician_events);
        // Switch back: the helped computer is shown again (its app restores the earlier grants).
        registry.handle(222_222_222, host, ClientMessage::SwitchSides { session: reversed }, now).unwrap();
        let Some(ServerMessage::Switched { session: back, .. }) = drain(&mut technician_events).pop() else { panic!() };
        let check = registry
            .policy_for_message(222_222_222, host, &ClientMessage::Decide { session: back, accept: true }, 100)
            .unwrap();
        assert!(check.unattended, "switching back restores an unattended session, not an attended one");
        connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), back);
        assert!(registry.controlled_policy_checks(111_111_111, technician, 100).iter().all(|(_, check)| check.as_ref().is_some_and(|check| check.unattended)));
        // Turning unattended access off at the helped computer ends it.
        registry.handle(222_222_222, host, ClientMessage::ConfigureUnattended { enabled: false }, now).unwrap();
        assert_eq!(registry.counts().1, 0);

        // Also while the sides are switched.
        registry.handle(222_222_222, host, ClientMessage::ConfigureUnattended { enabled: true }, now).unwrap();
        registry.handle(111_111_111, technician, ClientMessage::RequestUnattended { host: 222_222_222 }, now).unwrap();
        let Some(ServerMessage::Requested { session }) = drain(&mut technician_events).pop() else { panic!() };
        connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), session);
        drain(&mut host_events);
        registry.handle(111_111_111, technician, ClientMessage::SwitchSides { session }, now).unwrap();
        let Some(ServerMessage::Switched { session: reversed, .. }) = drain(&mut host_events).pop() else { panic!() };
        connect(&mut registry, now, (222_222_222, host), (111_111_111, technician), reversed);
        registry.handle(222_222_222, host, ClientMessage::ConfigureUnattended { enabled: false }, now).unwrap();
        assert_eq!(registry.counts().1, 0);
    }

    /// A registry that keeps session events, with a SaaS technician (profile 10, device 110) and
    /// a helped computer (profile 11, device 111).
    fn reporting() -> (Registry, Uuid, Uuid, Vec<mpsc::Receiver<ServerMessage>>) {
        let mut registry = Registry::default();
        registry.enable_session_journal();
        let (technician, t) = peer(&mut registry, 111_111_111, 10);
        let (host, h) = peer(&mut registry, 222_222_222, 11);
        (registry, technician, host, vec![t, h])
    }
    const SAAS: Option<SessionLimit> = Some(SessionLimit { tenant: Uuid::from_u128(77), max_sessions: 10 });
    fn requested(registry: &mut Registry, from: (u32, Uuid), host: u32, unattended: bool, limit: Option<SessionLimit>) -> Uuid {
        let message = if unattended { ClientMessage::RequestUnattended { host } } else { ClientMessage::Request { host } };
        registry.handle_limited(from.0, from.1, message, Instant::now(), limit).unwrap();
        *registry
            .sessions
            .iter()
            .find(|(_, room)| room.controller == from.0 && room.host == host && matches!(room.phase, Phase::Consent))
            .unwrap()
            .0
    }
    fn starts(events: &[SessionEvent]) -> Vec<&SessionEvent> {
        events.iter().filter(|event| matches!(event, SessionEvent::Start { .. })).collect()
    }
    fn ends(events: &[SessionEvent]) -> Vec<Uuid> {
        events.iter().filter_map(|event| match event { SessionEvent::End { id, .. } => Some(*id), _ => None }).collect()
    }
    fn record_of(event: &SessionEvent) -> Uuid {
        match event { SessionEvent::Start { id, .. } | SessionEvent::End { id, .. } => *id }
    }

    #[test]
    fn a_saas_session_is_recorded_once_connected_and_ended_once() {
        let (mut registry, technician, host, _events) = reporting();
        let now = Instant::now();
        // A linked-company session (no tenant limit) is not recorded.
        let session = requested(&mut registry, (111_111_111, technician), 222_222_222, false, None);
        connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), session);
        assert!(registry.running_records().is_empty());
        registry.handle(111_111_111, technician, ClientMessage::Close { session }, now).unwrap();
        assert!(registry.take_session_events().is_empty());
        // A SaaS session: nothing while asking, offering and answering...
        let session = requested(&mut registry, (111_111_111, technician), 222_222_222, false, SAAS);
        registry.handle(222_222_222, host, ClientMessage::Decide { session, accept: true }, now).unwrap();
        registry.handle(111_111_111, technician, ClientMessage::Signal { session, payload: Signal::Offer("v=0".into()) }, now).unwrap();
        assert!(registry.take_session_events().is_empty());
        // ...the start when the host's answer connects it.
        registry.handle(222_222_222, host, ClientMessage::Signal { session, payload: Signal::Answer("v=0".into()) }, now).unwrap();
        let events = registry.take_session_events();
        let [SessionEvent::Start { id, tenant, technician: profile, controller_device, controller_connection, device, connection, unattended, colleague, at }] = events.as_slice() else {
            panic!("{events:?}")
        };
        assert_eq!(
            (*tenant, *profile, *controller_device, *controller_connection, *device, *connection, *unattended, *colleague),
            (Uuid::from_u128(77), Uuid::from_u128(10), Uuid::from_u128(110), 111_111_111, Uuid::from_u128(111), 222_222_222, false, false)
        );
        assert!(*at > 1_700_000_000);
        assert_eq!(registry.running_records(), vec![*id]);
        // Closing it from both sides ends it once.
        registry.handle(222_222_222, host, ClientMessage::Close { session }, now).unwrap();
        registry.handle(111_111_111, technician, ClientMessage::Close { session }, now).unwrap();
        assert_eq!(ends(&registry.take_session_events()), vec![*id]);
        assert!(registry.running_records().is_empty());
        // A session refused at the computer was never connected: no record at all.
        let session = requested(&mut registry, (111_111_111, technician), 222_222_222, false, SAAS);
        registry.handle(222_222_222, host, ClientMessage::Decide { session, accept: false }, now).unwrap();
        assert!(registry.take_session_events().is_empty());
    }

    #[test]
    fn a_transport_restart_keeps_the_record() {
        let (mut registry, technician, host, _events) = reporting();
        let now = Instant::now();
        let session = requested(&mut registry, (111_111_111, technician), 222_222_222, false, SAAS);
        connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), session);
        let started = registry.take_session_events();
        let record = record_of(&started[0]);
        let later = now + Duration::from_secs(31);
        registry.handle(111_111_111, technician, ClientMessage::Restart { session }, later).unwrap();
        let replacement = *registry.sessions.keys().next().unwrap();
        assert_ne!(replacement, session);
        registry.handle(111_111_111, technician, ClientMessage::Signal { session: replacement, payload: Signal::Offer("v=0".into()) }, later).unwrap();
        registry.handle(222_222_222, host, ClientMessage::Signal { session: replacement, payload: Signal::Answer("v=0".into()) }, later).unwrap();
        assert!(registry.take_session_events().is_empty(), "no new start, no end");
        assert_eq!(registry.running_records(), vec![record]);
        registry.handle(222_222_222, host, ClientMessage::Close { session: replacement }, later).unwrap();
        assert_eq!(ends(&registry.take_session_events()), vec![record]);
    }

    #[test]
    fn switching_sides_records_a_new_session_for_the_same_technician() {
        let (mut registry, technician, host, _events) = reporting();
        let now = Instant::now();
        features(&mut registry, 111_111_111, technician);
        features(&mut registry, 222_222_222, host);
        let session = requested(&mut registry, (111_111_111, technician), 222_222_222, false, SAAS);
        connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), session);
        let first = record_of(&registry.take_session_events()[0]);
        registry.handle(111_111_111, technician, ClientMessage::SwitchSides { session }, now).unwrap();
        assert_eq!(ends(&registry.take_session_events()), vec![first]);
        let reversed = *registry.sessions.keys().next().unwrap();
        // The helped computer now views; the technician's computer answers.
        connect(&mut registry, now, (222_222_222, host), (111_111_111, technician), reversed);
        let events = registry.take_session_events();
        let [SessionEvent::Start { id, technician: profile, controller_device, controller_connection, device, connection, .. }] = events.as_slice() else {
            panic!("{events:?}")
        };
        assert_ne!(*id, first);
        assert_eq!(
            (*profile, *controller_device, *controller_connection, *device, *connection),
            (Uuid::from_u128(10), Uuid::from_u128(110), 111_111_111, Uuid::from_u128(111), 222_222_222),
            "the authorized technician and the helped computer, not the switched roles"
        );
        registry.handle(111_111_111, technician, ClientMessage::Close { session: reversed }, now).unwrap();
        assert_eq!(ends(&registry.take_session_events()), vec![*id]);
    }

    #[test]
    fn a_colleague_is_recorded_as_colleague_and_ends_with_the_first_session() {
        let (mut registry, technician, host, mut events) = reporting();
        let now = Instant::now();
        let (colleague, colleague_events) = peer(&mut registry, 333_333_333, 12);
        events.push(colleague_events);
        for (id, generation) in [(111_111_111, technician), (222_222_222, host), (333_333_333, colleague)] {
            features(&mut registry, id, generation);
        }
        let session = requested(&mut registry, (111_111_111, technician), 222_222_222, false, SAAS);
        connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), session);
        registry.handle(111_111_111, technician, ClientMessage::Invite { session, user: None, device: Some(333_333_333), control: false }, now).unwrap();
        let Some(ServerMessage::Invited { invitation, .. }) = drain(&mut events[2]).pop() else { panic!() };
        registry.handle_limited(333_333_333, colleague, ClientMessage::JoinSession { invitation }, now, SAAS).unwrap();
        let joined = *registry.sessions.iter().find(|(_, room)| room.joined.is_some()).unwrap().0;
        connect(&mut registry, now, (333_333_333, colleague), (222_222_222, host), joined);
        let recorded = registry.take_session_events();
        let starts = starts(&recorded);
        assert_eq!(starts.len(), 2);
        let SessionEvent::Start { technician: profile, colleague: true, device, .. } = starts[1] else { panic!("{recorded:?}") };
        assert_eq!((*profile, *device), (Uuid::from_u128(12), Uuid::from_u128(111)));
        assert!(matches!(starts[0], SessionEvent::Start { colleague: false, .. }));
        assert_eq!(registry.running_records().len(), 2);
        registry.handle(111_111_111, technician, ClientMessage::Close { session }, now).unwrap();
        let mut ended = ends(&registry.take_session_events());
        ended.sort_unstable();
        let mut wanted: Vec<Uuid> = recorded.iter().map(record_of).collect();
        wanted.sort_unstable();
        assert_eq!(ended, wanted);
    }

    #[test]
    fn every_way_a_session_ends_journals_exactly_one_end() {
        type Ender = fn(&mut Registry, Uuid, Uuid, Uuid, Instant);
        let enders: [(&str, Ender); 6] = [
            ("controller disconnects", |registry, technician, _, _, _| registry.disconnect(111_111_111, technician)),
            ("host disconnects", |registry, _, host, _, _| registry.disconnect(222_222_222, host)),
            ("deadline", |registry, _, _, _, now| registry.expire(now + Duration::from_secs(9 * 3600))),
            ("policy revoked", |registry, technician, _, session, now| registry.policy_revoked(session, (111_111_111, technician), now)),
            ("shutdown", |registry, _, _, _, _| registry.shutdown()),
            ("unattended turned off", |registry, _, host, _, now| {
                registry.handle(222_222_222, host, ClientMessage::ConfigureUnattended { enabled: false }, now).unwrap();
            }),
        ];
        for (what, end) in enders {
            let (mut registry, technician, host, _events) = reporting();
            let now = Instant::now();
            registry.handle(222_222_222, host, ClientMessage::ConfigureUnattended { enabled: true }, now).unwrap();
            let session = requested(&mut registry, (111_111_111, technician), 222_222_222, true, SAAS);
            connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), session);
            let started = registry.take_session_events();
            assert!(matches!(started.as_slice(), [SessionEvent::Start { unattended: true, .. }]), "{what}");
            end(&mut registry, technician, host, session, now);
            assert_eq!(ends(&registry.take_session_events()), vec![record_of(&started[0])], "{what}");
            // Nothing more afterwards (late close, expiry of retired rooms).
            registry.expire(now + Duration::from_secs(10 * 3600));
            assert!(registry.take_session_events().is_empty(), "{what}");
            assert!(registry.running_records().is_empty(), "{what}");
        }
    }

    #[test]
    fn the_journal_is_off_until_enabled_and_bounded() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (technician, _t) = peer(&mut registry, 111_111_111, 10);
        let (host, _h) = peer(&mut registry, 222_222_222, 11);
        let session = requested(&mut registry, (111_111_111, technician), 222_222_222, false, SAAS);
        connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), session);
        assert!(registry.take_session_events().is_empty() && registry.running_records().is_empty());
        registry.enable_session_journal();
        for n in 0..(MAX_SESSION_EVENTS as u128 + 5) {
            registry.journal.push(SessionEvent::End { id: Uuid::from_u128(n), at: 1 });
        }
        let events = registry.take_session_events();
        assert_eq!(events.len(), MAX_SESSION_EVENTS);
        assert_eq!(record_of(&events[0]), Uuid::from_u128(5), "the oldest are dropped");
        assert_eq!(registry.journal.dropped, 5);
    }

    #[test]
    fn capabilities_are_bounded() {
        let mut registry = Registry::default();
        let (generation, _events) = peer(&mut registry, 111_111_111, 10);
        for bad in [vec!["".to_owned()], vec!["Join".to_owned()], vec!["a".repeat(17)], vec!["x".to_owned(); 17]] {
            assert_eq!(registry.handle(111_111_111, generation, ClientMessage::Capabilities { features: bad }, Instant::now()), Err(ErrorCode::Invalid));
        }
    }

    fn connected_with_password(registry: &mut Registry, now: Instant, controller: (u32, Uuid), host: (u32, Uuid)) -> Uuid {
        registry.handle(host.0, host.1, ClientMessage::GenerateSupportCode, now).unwrap();
        let code = registry.peers[&host.0].code_gate.as_ref().unwrap().code.clone().unwrap();
        registry.handle(controller.0, controller.1, ClientMessage::RequestWithCode { host: host.0, code }, now).unwrap();
        let session = *registry.sessions.keys().next().unwrap();
        connect(registry, now, controller, host, session);
        session
    }

    #[test]
    fn a_password_session_is_warned_and_ends_after_two_hours() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (technician, mut technician_events) = peer(&mut registry, 111_111_111, 10);
        let (host, mut host_events) = peer(&mut registry, 222_222_222, 11);
        // Only the technician's app understands `session_ending`.
        registry.handle(111_111_111, technician, ClientMessage::Capabilities { features: vec![FEATURE_ENDING.into()] }, now).unwrap();
        let session = connected_with_password(&mut registry, now, (111_111_111, technician), (222_222_222, host));
        drain(&mut technician_events);
        drain(&mut host_events);
        // A transport renewal keeps the limit.
        registry.handle(111_111_111, technician, ClientMessage::Restart { session }, now + Duration::from_secs(40)).unwrap();
        let session = *registry.sessions.keys().next().unwrap();
        connect_renewed(&mut registry, now + Duration::from_secs(41), (111_111_111, technician), (222_222_222, host), session);
        drain(&mut technician_events);
        drain(&mut host_events);
        registry.expire(now + PASSWORD_SESSION - Duration::from_secs(301));
        assert!(drain(&mut technician_events).is_empty());
        registry.expire(now + PASSWORD_SESSION - Duration::from_secs(300));
        assert!(matches!(drain(&mut technician_events).as_slice(),
            [ServerMessage::SessionEnding { session: warned, seconds: 300, reason: EndingReason::SupportPasswordLimit }] if *warned == session));
        assert!(
            drain(&mut host_events).iter().all(|event| !matches!(event, ServerMessage::SessionEnding { .. })),
            "an app without `ending` gets no new message type"
        );
        registry.expire(now + PASSWORD_SESSION - Duration::from_secs(200));
        assert!(drain(&mut technician_events).is_empty(), "warned once per step");
        registry.expire(now + PASSWORD_SESSION - Duration::from_secs(60));
        assert!(matches!(drain(&mut technician_events).as_slice(), [ServerMessage::SessionEnding { seconds: 60, .. }]));
        registry.expire(now + PASSWORD_SESSION);
        assert!(matches!(drain(&mut technician_events).as_slice(),
            [ServerMessage::Closed { session: closed, reason: CloseReason::Expired }] if *closed == session));
        assert!(matches!(drain(&mut host_events).as_slice(), [ServerMessage::Closed { reason: CloseReason::Expired, .. }]));
        assert_eq!(registry.counts(), (2, 0));
    }

    fn connect_renewed(registry: &mut Registry, now: Instant, controller: (u32, Uuid), host: (u32, Uuid), session: Uuid) {
        registry.handle(controller.0, controller.1, ClientMessage::Signal { session, payload: Signal::Offer("v=0".into()) }, now).unwrap();
        registry.handle(host.0, host.1, ClientMessage::Signal { session, payload: Signal::Answer("v=0".into()) }, now).unwrap();
    }

    #[test]
    fn sessions_without_a_password_are_not_limited_to_two_hours() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (technician, mut technician_events) = peer(&mut registry, 111_111_111, 10);
        let (host, _host_events) = peer(&mut registry, 222_222_222, 11);
        registry.handle(111_111_111, technician, ClientMessage::Capabilities { features: vec![FEATURE_ENDING.into()] }, now).unwrap();
        registry.handle(222_222_222, host, ClientMessage::ConfigureUnattended { enabled: true }, now).unwrap();
        registry.handle(111_111_111, technician, ClientMessage::RequestUnattended { host: 222_222_222 }, now).unwrap();
        let session = *registry.sessions.keys().next().unwrap();
        connect(&mut registry, now, (111_111_111, technician), (222_222_222, host), session);
        drain(&mut technician_events);
        registry.expire(now + PASSWORD_SESSION + Duration::from_secs(60));
        assert!(drain(&mut technician_events).is_empty());
        assert_eq!(registry.counts(), (2, 1), "an unattended session keeps running after two hours");
    }

    #[test]
    fn a_switch_of_sides_does_not_extend_a_password_session() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (technician, _technician_events) = peer(&mut registry, 111_111_111, 10);
        let (host, _host_events) = peer(&mut registry, 222_222_222, 11);
        for (id, generation) in [(111_111_111, technician), (222_222_222, host)] {
            features(&mut registry, id, generation);
        }
        let session = connected_with_password(&mut registry, now, (111_111_111, technician), (222_222_222, host));
        let later = now + Duration::from_secs(3600);
        registry.handle(111_111_111, technician, ClientMessage::SwitchSides { session }, later).unwrap();
        let switched = *registry.sessions.keys().next().unwrap();
        // The switched session runs the other way: the old host is the controller now.
        connect(&mut registry, later, (222_222_222, host), (111_111_111, technician), switched);
        assert_eq!(registry.sessions[&switched].active_until, Some(now + PASSWORD_SESSION));
    }

    #[test]
    fn running_sessions_are_handed_to_the_next_broker_and_resume_with_the_same_identities() {
        let mut old = Registry::default();
        let now = Instant::now();
        let (technician, _a) = peer(&mut old, 111_111_111, 10);
        let (host, _b) = peer(&mut old, 222_222_222, 11);
        let (third, _c) = peer(&mut old, 333_333_333, 12);
        let session = connected_with_password(&mut old, now, (111_111_111, technician), (222_222_222, host));
        // A session still asking for consent is not handed over.
        old.handle(333_333_333, third, ClientMessage::Request { host: 111_111_111 }, now).unwrap();
        let handover = old.shutdown_for_handover(now, 1_000_000);
        assert_eq!(handover.sessions.len(), 1);
        assert_eq!(handover.sessions[0].id, session, "the asking session stays behind");
        assert!(handover.sessions[0].password);
        assert_eq!(old.counts(), (0, 0));
        let text = serde_json::to_string(&handover).unwrap();
        assert!(!text.contains("code") && !text.contains("token"), "no secrets in the handover: {text}");

        // Too old, or from the future: ignored.
        let mut stale = Registry::default();
        assert_eq!(stale.import_handover(handover.clone(), now, 1_000_000 + HANDOVER_MAX_AGE_SECONDS + 1), 0);
        assert_eq!(stale.import_handover(handover.clone(), now, 999_000), 0);

        let mut next = Registry::default();
        let later = now + Duration::from_secs(3);
        assert_eq!(next.import_handover(handover, later, 1_000_003), 1);
        let (technician, mut technician_events) = peer(&mut next, 111_111_111, 10);
        let (host, mut host_events) = peer(&mut next, 222_222_222, 11);
        // Another account on the technician's ID cannot take the session over.
        let (impostor, mut impostor_events) = peer(&mut next, 444_444_444, 10);
        assert_eq!(next.handle(444_444_444, impostor, ClientMessage::Resume { session }, later), Err(ErrorCode::Unavailable));
        assert!(drain(&mut impostor_events).iter().all(|event| !matches!(event, ServerMessage::Resumed { .. })));
        // Nothing passes until both sides are back.
        drain(&mut technician_events);
        drain(&mut host_events);
        next.handle(111_111_111, technician, ClientMessage::Resume { session }, later).unwrap();
        assert!(matches!(drain(&mut technician_events).as_slice(), [ServerMessage::Resumed { session: back }] if *back == session));
        assert_eq!(
            next.handle(111_111_111, technician, ClientMessage::Signal { session, payload: Signal::Ice("candidate".into()) }, later),
            Err(ErrorCode::Forbidden)
        );
        assert!(next.controlled_policy_checks(111_111_111, technician, 100).is_empty(), "no policy re-check while parked");
        next.handle(222_222_222, host, ClientMessage::Resume { session }, later).unwrap();
        next.handle(111_111_111, technician, ClientMessage::Signal { session, payload: Signal::Ice("candidate".into()) }, later).unwrap();
        assert!(drain(&mut host_events).iter().any(|event| matches!(event, ServerMessage::Signal { .. })));
        assert_eq!(next.controlled_policy_checks(111_111_111, technician, 100).len(), 1);
        // The D2 limit carried over: still two hours from the original connect.
        next.expire(later + PASSWORD_SESSION);
        assert_eq!(next.counts().1, 0);
    }

    #[test]
    fn a_handed_over_session_ends_when_a_side_does_not_resume() {
        let mut old = Registry::default();
        let now = Instant::now();
        let (technician, _a) = peer(&mut old, 111_111_111, 10);
        let (host, _b) = peer(&mut old, 222_222_222, 11);
        let session = *{
            old.handle(111_111_111, technician, ClientMessage::Request { host: 222_222_222 }, now).unwrap();
            old.sessions.keys().next().unwrap()
        };
        connect(&mut old, now, (111_111_111, technician), (222_222_222, host), session);
        let handover = old.shutdown_for_handover(now, 2_000_000);
        let mut next = Registry::default();
        assert_eq!(next.import_handover(handover, now, 2_000_000), 1);
        let (technician, mut technician_events) = peer(&mut next, 111_111_111, 10);
        next.handle(111_111_111, technician, ClientMessage::Resume { session }, now).unwrap();
        drain(&mut technician_events);
        next.expire(now + RESUME_WINDOW);
        assert!(drain(&mut technician_events).iter().any(|event| matches!(event, ServerMessage::Closed { session: closed, .. } if *closed == session)));
        assert_eq!(next.counts(), (1, 0));
        assert_eq!(next.handle(111_111_111, technician, ClientMessage::Resume { session }, now), Err(ErrorCode::Unavailable));
    }

    #[test]
    fn app_versions_are_validated_and_listed_for_presence() {
        let mut registry = Registry::default();
        let now = Instant::now();
        let (host, _events) = peer(&mut registry, 222_222_222, 11);
        for (version, platform) in [("2.0.19; rm", "macos"), ("2", "macos"), ("2.0.19", "MacOS"), ("2.0.19", ""), ("1.2.3.4.5", "x")] {
            assert_eq!(
                registry.handle(222_222_222, host, ClientMessage::AppVersion { version: version.into(), platform: platform.into() }, now),
                Err(ErrorCode::Invalid),
                "{version} {platform}"
            );
        }
        registry.handle(222_222_222, host, ClientMessage::AppVersion { version: "2.0.19".into(), platform: "macos-arm64".into() }, now).unwrap();
        assert_eq!(registry.device_versions(100), vec![(222_222_222, "2.0.19".into(), "macos-arm64".into())]);
        assert!(registry.device_versions(900).is_empty(), "expired sign-ins are not listed");
    }
}
