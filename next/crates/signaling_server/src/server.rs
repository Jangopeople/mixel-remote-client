use crate::{
    auth::{AuthError, EnrolledPrincipal, IsmAuth, Principal},
    protocol::*,
    registry::Registry,
    support_policy::{Grant, SupportPair, SupportPolicy},
};
use axum::{
    Router,
    extract::{
        State, WebSocketUpgrade,
        ws::{Message, WebSocket},
    },
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::get,
};
use futures_util::{SinkExt, StreamExt};
use std::{
    collections::HashSet,
    future::Future,
    pin::Pin,
    sync::{Arc, Mutex},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio::sync::{Semaphore, mpsc, watch};

type AuthFuture<'a, T> = Pin<Box<dyn Future<Output = Result<T, AuthError>> + Send + 'a>>;
pub trait Authenticator: Send + Sync {
    /// Token-only adapters cannot claim enrolled device identities.
    fn authenticate_device<'a>(
        &'a self,
        _token: &'a str,
        _device: uuid::Uuid,
    ) -> AuthFuture<'a, EnrolledPrincipal> {
        Box::pin(async { Err(AuthError) })
    }
    fn authenticate<'a>(
        &'a self,
        token: &'a str,
    ) -> Pin<Box<dyn Future<Output = Result<Principal, AuthError>> + Send + 'a>>;
}
impl Authenticator for IsmAuth {
    fn authenticate_device<'a>(
        &'a self,
        token: &'a str,
        device: uuid::Uuid,
    ) -> AuthFuture<'a, EnrolledPrincipal> {
        Box::pin(self.authenticate_device(token, device))
    }
    fn authenticate<'a>(
        &'a self,
        token: &'a str,
    ) -> Pin<Box<dyn Future<Output = Result<Principal, AuthError>> + Send + 'a>> {
        Box::pin(self.authenticate(token))
    }
}
#[derive(Clone)]
pub struct AppState {
    pub registry: Arc<Mutex<Registry>>,
    auth: Arc<dyn Authenticator>,
    origins: Arc<HashSet<String>>,
    connections: Arc<Semaphore>,
    auth_slots: Arc<Semaphore>,
    turn: Option<Arc<crate::turn::TurnConfig>>,
    policy: Option<Arc<SupportPolicy>>,
    policy_slots: Arc<Semaphore>,
}
impl AppState {
    pub fn with_turn(mut self, config: Option<crate::turn::TurnConfig>) -> Self {
        self.turn = config.map(Arc::new);
        self
    }
    pub fn with_support_policy(mut self, policy: SupportPolicy) -> Self {
        self.policy = Some(Arc::new(policy));
        self
    }

    async fn support_allowed(
        &self,
        pair: &SupportPair,
        unattended: bool,
        accept_session: Option<uuid::Uuid>,
    ) -> Option<Grant> {
        let policy = self.policy.as_ref()?;
        let _slot = self.policy_slots.try_acquire().ok()?;
        policy.allows(pair, unattended, accept_session).await
    }

    pub fn new(auth: Arc<dyn Authenticator>, origins: HashSet<String>) -> Self {
        Self {
            registry: Arc::new(Mutex::new(Registry::default())),
            auth,
            origins: Arc::new(origins),
            connections: Arc::new(Semaphore::new(1000)),
            auth_slots: Arc::new(Semaphore::new(32)),
            turn: None,
            policy: None,
            policy_slots: Arc::new(Semaphore::new(64)),
        }
    }
}
pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/healthz", get(|| async { "ok\n" }))
        .route("/v1/ws", get(upgrade))
        .with_state(state)
}
async fn upgrade(
    State(state): State<AppState>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Response {
    if let Some(origin) = headers.get("origin")
        && !origin.to_str().is_ok_and(|o| state.origins.contains(o))
    {
        return StatusCode::FORBIDDEN.into_response();
    }
    let Ok(permit) = state.connections.clone().try_acquire_owned() else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    ws.max_message_size(MAX_MESSAGE)
        .max_frame_size(MAX_MESSAGE)
        .on_upgrade(move |socket| async move {
            let _permit = permit;
            connection(socket, state).await;
        })
}
async fn send(socket: &mut WebSocket, event: &ServerMessage) -> bool {
    let Ok(json) = serde_json::to_string(event) else {
        return false;
    };
    matches!(
        tokio::time::timeout(
            Duration::from_secs(2),
            socket.send(Message::Text(json.into()))
        )
        .await,
        Ok(Ok(()))
    )
}
fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(u64::MAX, |d| d.as_secs())
}
fn authorization_live(expires_at: u64, authorized_at: Instant, now: Instant, unix: u64) -> bool {
    unix < expires_at && now.duration_since(authorized_at) < Duration::from_secs(900)
}
async fn connection(mut socket: WebSocket, state: AppState) {
    let initial = tokio::time::timeout(Duration::from_secs(5), socket.next()).await;
    let Ok(Some(Ok(Message::Text(raw)))) = initial else {
        return;
    };
    let (token, device) = match serde_json::from_str(&raw) {
        Ok(ClientMessage::Authenticate { token }) => (token, None),
        Ok(ClientMessage::AuthenticateDevice { token, device }) => (token, Some(device)),
        _ => return,
    };
    drop(raw);
    let Ok(auth_slot) = state.auth_slots.clone().try_acquire_owned() else {
        return;
    };
    let result = tokio::time::timeout(Duration::from_secs(17), async {
        if let Some(device) = device {
            let enrolled = state.auth.authenticate_device(&token, device).await?;
            Ok::<_, AuthError>((enrolled.principal.clone(), Some(enrolled)))
        } else {
            Ok((state.auth.authenticate(&token).await?, None))
        }
    })
    .await;
    drop(auth_slot);
    // Never log a bearer or retain it after identity resolution.
    drop(token);
    let Ok(Ok((principal, enrolled))) = result else {
        return;
    };
    if state.policy.is_none()
        && enrolled
            .as_ref()
            .and_then(|device| device.identity_ticket.as_ref())
            .is_some_and(|ticket| ticket.unattended_host_only())
    {
        return;
    }
    if let Some(enrolled) = &enrolled {
        use rand_core::RngCore;
        let mut nonce = [0; 32];
        rand_core::OsRng.fill_bytes(&mut nonce);
        let challenge = core_protocol::device_login::DeviceLoginChallenge {
            nonce,
            device: *enrolled.device.as_bytes(),
            profile: *principal.user.as_bytes(),
            tenant: principal.tenant.map(|id| *id.as_bytes()),
            connection_id: enrolled.connection_id,
            generation: enrolled.generation,
        };
        if !send(
            &mut socket,
            &ServerMessage::DeviceChallenge {
                challenge: challenge.clone(),
            },
        )
        .await
        {
            return;
        }
        let proof = tokio::time::timeout(Duration::from_secs(5), socket.next()).await;
        let Ok(Some(Ok(Message::Text(raw)))) = proof else {
            return;
        };
        let Ok(ClientMessage::ProveDevice { signature }) = serde_json::from_str(&raw) else {
            return;
        };
        if challenge.verify(&enrolled.public_key, &signature).is_err() {
            return;
        }
    }
    let mut expires_at = principal.expires_at;
    let unix = unix_now();
    if expires_at <= unix {
        return;
    }
    let (tx, mut rx) = mpsc::channel(64);
    let (stop, mut stopped) = watch::channel(false);
    let registered = match state.registry.lock() {
        Ok(mut r) => match &enrolled {
            Some(device) => r
                .register_with_id(device.connection_id, principal, tx, stop)
                .and_then(|(id, generation)| {
                    if let Some(ticket) = device.identity_ticket.clone()
                        && let Err(error) = r.bind_identity(id, generation, ticket)
                    {
                        r.disconnect(id, generation);
                        return Err(error);
                    }
                    Ok((id, generation))
                }),
            None => r.register(principal, tx, stop),
        },
        Err(_) => return,
    };
    let Ok((id, generation)) = registered else {
        return;
    };
    let start = Instant::now();
    let mut authorized_at = start;
    let mut last_agent: Option<Instant> = None;
    let mut last_renewal = None;
    let mut last_activity = start;
    let mut last_ping = start;
    let mut rate_start = start;
    let mut rate_count = 0u32;
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    let mut policy_tick = tokio::time::interval(Duration::from_secs(10));
    policy_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    loop {
        tokio::select! {
            _ = stopped.changed() => break,
            _ = tick.tick() => {
                let now = Instant::now();
                let unix = unix_now();
                if !authorization_live(expires_at, authorized_at, now, unix) || now.duration_since(last_activity) >= Duration::from_secs(75) { break; }
                match state.registry.lock() { Ok(mut r) => r.expire(now), Err(_) => break }
                if now.duration_since(last_ping) >= Duration::from_secs(20) {
                    if !matches!(tokio::time::timeout(Duration::from_secs(2), socket.send(Message::Ping(Vec::new().into()))).await, Ok(Ok(()))) { break; }
                    last_ping = now;
                }
            }
            _ = policy_tick.tick(), if state.policy.is_some() => {
                let checks = match state.registry.lock() {
                    Ok(registry) => registry.controlled_policy_checks(id, generation, unix_now()),
                    Err(_) => break,
                };
                let denied = futures_util::future::join_all(checks.into_iter().map(|(session, check)| {
                    let state = &state;
                    async move {
                        let allowed = match check {
                            Some(check) => state.support_allowed(&check.pair, check.unattended, None).await.is_some(),
                            None => false,
                        };
                        (!allowed).then_some(session)
                    }
                })).await;
                if let Ok(mut registry) = state.registry.lock() {
                    for session in denied.into_iter().flatten() {
                        registry.policy_revoked(session, (id, generation), Instant::now());
                    }
                } else { break; }
            }
            event = rx.recv() => {
                let Some(event) = event else { break; };
                if !send(&mut socket, &event).await { break; }
            }
            message = socket.next() => {
                let Some(Ok(message)) = message else { break; };
                let now = Instant::now();
                // Do not wait for the next housekeeping tick: an inbound
                // request arriving at expiry must never create new consent.
                if !authorization_live(expires_at, authorized_at, now, unix_now()) { break; }
                last_activity = now;
                if now.duration_since(rate_start) >= Duration::from_secs(1) { rate_start = now; rate_count = 0; }
                rate_count += 1;
                if rate_count > 60 { break; }
                match message {
                    Message::Close(_) => break,
                    Message::Ping(_) | Message::Pong(_) => {},
                    Message::Text(text) => {
                        let parsed = serde_json::from_str(&text);
                        drop(text);
                        if let Ok(ClientMessage::Renew { token }) = parsed {
                            // Authenticate outside the registry lock. A failed renewal
                            // closes the original connection and its sessions as well.
                            if last_renewal.is_some_and(|last: Instant| now.duration_since(last) < Duration::from_secs(30)) {
                                if !send(&mut socket, &ServerMessage::Error { code: ErrorCode::Busy }).await { break; }
                                continue;
                            }
                            last_renewal = Some(now);
                            let unix = unix_now();
                            let remaining = Duration::from_secs(expires_at.saturating_sub(unix))
                                .min(Duration::from_secs(900).saturating_sub(now.duration_since(authorized_at)));
                            if remaining.is_zero() { break; }
                            let Ok(slot) = state.auth_slots.clone().try_acquire_owned() else { break; };
                            let authentication_budget = Duration::from_secs(if enrolled.is_some() {17} else {13});
                            let result = tokio::time::timeout(remaining.min(authentication_budget), async {
                                if let Some(previous) = &enrolled {
                                    let current = state.auth.authenticate_device(&token, previous.device).await?;
                                    if current.device != previous.device || current.connection_id != previous.connection_id
                                        || current.generation != previous.generation || current.public_key != previous.public_key
                                        || current.identity_ticket.as_ref().map(|ticket| ticket.version)
                                            != previous.identity_ticket.as_ref().map(|ticket| ticket.version) {
                                        return Err(AuthError);
                                    }
                                    Ok((current.principal,current.identity_ticket))
                                } else { state.auth.authenticate(&token).await.map(|principal|(principal,None)) }
                            }).await;
                            drop(token);
                            drop(slot);
                            let Ok(Ok((principal,identity))) = result else { break; };
                            let unix = unix_now();
                            if principal.expires_at <= unix || !authorization_live(expires_at, authorized_at, Instant::now(), unix) { break; }
                            let next_expiry = principal.expires_at;
                            let renewed = match state.registry.lock() {
                                Ok(mut r) => r.renew(id, generation, principal).and_then(|()|match identity{Some(identity)=>r.bind_identity(id,generation,identity),None=>Ok(())}).is_ok(),
                                Err(_) => false,
                            };
                            if !renewed { break; }
                            expires_at = next_expiry;
                            authorized_at = Instant::now();
                            if !send(&mut socket, &ServerMessage::Renewed { expires_at }).await { break; }
                            continue;
                        }
                        if let Ok(ClientMessage::Agent { inventory, result }) = &parsed {
                            // At most one check-in every 15 s per connection; oversized reports are refused.
                            let allowed = last_agent.is_none_or(|at: Instant| at.elapsed() >= Duration::from_secs(15))
                                && inventory.as_ref().is_none_or(|text| text.len() <= crate::protocol::MAX_AGENT_INVENTORY)
                                && result.as_ref().is_none_or(|result| result.output.len() <= crate::protocol::MAX_AGENT_OUTPUT);
                            if !allowed {
                                if !send(&mut socket, &ServerMessage::Agent { command: None }).await { break; }
                                continue;
                            }
                            last_agent = Some(Instant::now());
                            let binding = match state.registry.lock() {
                                Ok(registry) => registry.agent_binding(id, generation, unix_now()),
                                Err(_) => break,
                            };
                            let command = match (state.policy.as_ref(), binding.as_ref(), state.policy_slots.try_acquire()) {
                                (Some(policy), Some(binding), Ok(_slot)) => policy.agent(binding, inventory.as_deref(), result.as_ref()).await.unwrap_or(None),
                                _ => None,
                            };
                            if !send(&mut socket, &ServerMessage::Agent { command }).await { break; }
                            continue;
                        }
                        if let Ok(message @ (ClientMessage::CheckPairing | ClientMessage::ConfirmPairing { .. })) = parsed {
                            let binding = match state.registry.lock() {
                                Ok(registry) => registry.device_binding(id, generation, unix_now()),
                                Err(_) => break,
                            };
                            let confirmation = match message {
                                ClientMessage::ConfirmPairing { request } => Some(request),
                                _ => None,
                            };
                            let outcome = match (state.policy.as_ref(), binding.as_ref(), state.policy_slots.try_acquire()) {
                                (Some(policy), Some(binding), Ok(_slot)) => policy.pairing(binding, confirmation).await,
                                _ => Err(AuthError),
                            };
                            let event = match outcome {
                                Ok(pending) => ServerMessage::PairingStatus { pending, confirmed: confirmation.is_some(), failed: false },
                                Err(_) => ServerMessage::PairingStatus { pending: None, confirmed: false, failed: true },
                            };
                            if !send(&mut socket, &event).await { break; }
                            continue;
                        }
                        if state.policy.is_none()
                            && matches!(parsed, Ok(ClientMessage::RequestUnattended { .. }
                                | ClientMessage::ConfigureUnattended { .. }))
                        {
                            if !send(&mut socket, &ServerMessage::Error { code: ErrorCode::Unavailable }).await { break; }
                            continue;
                        }
                        if state.policy.is_some()
                            && let Ok(message @ (ClientMessage::Request { .. }
                                | ClientMessage::RequestUnattended { .. }
                                | ClientMessage::RequestWithCode { .. }
                                | ClientMessage::Decide { accept: true, .. }
                                | ClientMessage::Restart { .. })) = parsed
                        {
                            let check = match state.registry.lock() {
                                Ok(registry) => registry.policy_for_message(id, generation, &message, unix_now()),
                                Err(_) => break,
                            };
                            let grant = match &check {
                                Some(check) => {
                                    let audit = match &message {
                                        ClientMessage::Decide { session, accept: true } if check.unattended => Some(*session),
                                        _ => None,
                                    };
                                    state.support_allowed(&check.pair, check.unattended, audit).await
                                },
                                None => None,
                            };
                            let now = Instant::now();
                            let unix = unix_now();
                            if !authorization_live(expires_at, authorized_at, now, unix) { break; }
                            let result = match state.registry.lock() {
                                Ok(mut registry) => {
                                    if let Some(grant) = grant.filter(|_| registry.policy_for_message(id, generation, &message, unix) == check) {
                                        // A SaaS tenant's plan limits its sessions at once.
                                        registry.handle_limited(id, generation, message, now, grant.limit)
                                    } else {
                                        if let Some(check) = check
                                            && let ClientMessage::Decide { session, .. } | ClientMessage::Restart { session } = message
                                        {
                                            registry.policy_revoked(session, check.controller, now);
                                            Ok(())
                                        } else {
                                            Err(ErrorCode::Unavailable)
                                        }
                                    }
                                }
                                Err(_) => break,
                            };
                            if let Err(code) = result
                                && !send(&mut socket, &ServerMessage::Error { code }).await { break; }
                            continue;
                        }
                        let result = match parsed {
                            Ok(ClientMessage::Ice{session})=>{
                                let permission=if enrolled.is_some(){match state.registry.lock(){Ok(mut registry)=>registry.ice_permission(id,generation,session,now,unix_now()),Err(_)=>break}}else{Err(ErrorCode::Forbidden)};
                                let response=permission.and_then(|expiry|state.turn.as_ref().ok_or(ErrorCode::Unavailable)?.issue(session,id,expiry.min(unix_now().saturating_add(Duration::from_secs(900).saturating_sub(now.duration_since(authorized_at)).as_secs())),unix_now()).map_err(|_|ErrorCode::Forbidden));
                                match response{Ok(lease)=>{if !send(&mut socket,&ServerMessage::Ice{session,lease}).await{break;}Ok(())},Err(code)=>Err(code)}
                            },
                            Ok(message) => match state.registry.lock() { Ok(mut r) => r.handle(id, generation, message, now), Err(_) => break },
                            Err(_) => Err(ErrorCode::Invalid),
                        };
                        if let Err(code) = result
                            && !send(&mut socket, &ServerMessage::Error { code }).await { break; }
                    }
                    Message::Binary(_) => break,
                }
            }
        }
    }
    if let Ok(mut registry) = state.registry.lock() {
        registry.disconnect(id, generation);
    }
    let _ = tokio::time::timeout(Duration::from_secs(1), socket.close()).await;
}

#[cfg(test)]
mod lease_tests {
    use super::*;
    #[test]
    fn authorization_rejects_exact_bearer_and_monotonic_boundaries() {
        let start = Instant::now();
        assert!(authorization_live(
            2000,
            start,
            start + Duration::from_secs(899),
            1999
        ));
        assert!(!authorization_live(2000, start, start, 2000));
        assert!(!authorization_live(2000, start, start, 2001));
        // Moving the wall clock backwards cannot reset the monotonic limit.
        assert!(!authorization_live(
            2000,
            start,
            start + Duration::from_secs(900),
            1000
        ));
        assert!(!authorization_live(
            2000,
            start,
            start + Duration::from_secs(901),
            1000
        ));
    }
}
