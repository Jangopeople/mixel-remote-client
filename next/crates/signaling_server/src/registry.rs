//! Bounded registry. All mutation occurs under one short mutex; no network await
//! occurs while held. Connection generations prevent stale disconnects deleting
//! a replacement. Slow peers are disconnected, never given unbounded queues.
use crate::{auth::Principal, protocol::*};
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
struct Peer {
    generation: Uuid,
    principal: Principal,
    tx: mpsc::Sender<ServerMessage>,
    stop: watch::Sender<bool>,
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
    phase: Phase,
    deadline: Instant,
    identity_from_controller: bool,
    identity_from_host: bool,
}
#[derive(Default)]
pub struct Registry {
    peers: HashMap<u32, Peer>,
    sessions: HashMap<Uuid, Session>,
}
impl Registry {
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
        let generation = Uuid::new_v4();
        self.peers.insert(
            host,
            Peer {
                generation,
                principal,
                tx,
                stop,
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
    fn close(&mut self, session: Uuid, reason: CloseReason) {
        if let Some(s) = self.sessions.remove(&session) {
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
            self.close(s, CloseReason::Disconnected);
        }
    }
    pub fn expire(&mut self, now: Instant) {
        let expired: Vec<_> = self
            .sessions
            .iter()
            .filter(|(_, s)| now >= s.deadline)
            .map(|(id, _)| *id)
            .collect();
        for s in expired {
            self.close(s, CloseReason::Expired);
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
        match message {
            ClientMessage::Authenticate { .. } | ClientMessage::Renew { .. } => {
                Err(ErrorCode::Invalid)
            }
            ClientMessage::Request { host } => {
                let requester = &self.peers[&from].principal;
                let target = self.peers.get(&host).ok_or(ErrorCode::Unavailable)?;
                if from == host || !requester.can_request(&target.principal) {
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
                let requester = requester.clone();
                let session = Uuid::new_v4();
                self.sessions.insert(
                    session,
                    Session {
                        controller: from,
                        host,
                        phase: Phase::Consent,
                        deadline: now + Duration::from_secs(90),
                        identity_from_controller: false,
                        identity_from_host: false,
                    },
                );
                self.send(from, ServerMessage::Requested { session });
                self.send(
                    host,
                    ServerMessage::Consent {
                        session,
                        requester: requester.user,
                        requester_tenant: requester.tenant,
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
                    self.close(session, CloseReason::Rejected);
                    return Ok(());
                }
                s.phase = Phase::Offer;
                s.deadline = now + Duration::from_secs(30);
                let (a, b) = (s.controller, s.host);
                let mut nonce = [0; 32];
                OsRng.fill_bytes(&mut nonce);
                for id in [a, b] {
                    self.send(id, ServerMessage::Accepted { session, nonce });
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
                        s.deadline = now + Duration::from_secs(8 * 3600);
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
                self.close(session, CloseReason::Requested);
                Ok(())
            }
        }
    }
    pub fn shutdown(&mut self) {
        let sessions: Vec<_> = self.sessions.keys().copied().collect();
        for id in sessions {
            self.close(id, CloseReason::Disconnected);
        }
        for peer in self.peers.values() {
            let _ = peer.stop.send(true);
        }
        self.peers.clear();
    }
    pub fn counts(&self) -> (usize, usize) {
        (self.peers.len(), self.sessions.len())
    }
}
