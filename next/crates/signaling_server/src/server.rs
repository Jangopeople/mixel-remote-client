use crate::{
    auth::{AuthError, IsmAuth, Principal},
    protocol::*,
    registry::Registry,
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

pub trait Authenticator: Send + Sync {
    fn authenticate<'a>(
        &'a self,
        token: &'a str,
    ) -> Pin<Box<dyn Future<Output = Result<Principal, AuthError>> + Send + 'a>>;
}
impl Authenticator for IsmAuth {
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
}
impl AppState {
    pub fn new(auth: Arc<dyn Authenticator>, origins: HashSet<String>) -> Self {
        Self {
            registry: Arc::new(Mutex::new(Registry::default())),
            auth,
            origins: Arc::new(origins),
            connections: Arc::new(Semaphore::new(1000)),
            auth_slots: Arc::new(Semaphore::new(32)),
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
    let Ok(ClientMessage::Authenticate { token }) = serde_json::from_str(&raw) else {
        return;
    };
    drop(raw);
    let Ok(auth_slot) = state.auth_slots.clone().try_acquire_owned() else {
        return;
    };
    let result =
        tokio::time::timeout(Duration::from_secs(13), state.auth.authenticate(&token)).await;
    drop(auth_slot);
    // Never log a bearer or retain it after identity resolution.
    drop(token);
    let Ok(Ok(principal)) = result else {
        return;
    };
    let mut expires_at = principal.expires_at;
    let unix = unix_now();
    if expires_at <= unix {
        return;
    }
    let (tx, mut rx) = mpsc::channel(64);
    let (stop, mut stopped) = watch::channel(false);
    let registered = match state.registry.lock() {
        Ok(mut r) => r.register(principal, tx, stop),
        Err(_) => return,
    };
    let Ok((id, generation)) = registered else {
        return;
    };
    let start = Instant::now();
    let mut authorized_at = start;
    let mut last_renewal = None;
    let mut last_activity = start;
    let mut last_ping = start;
    let mut rate_start = start;
    let mut rate_count = 0u32;
    let mut tick = tokio::time::interval(Duration::from_secs(1));
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
                            let result = tokio::time::timeout(remaining.min(Duration::from_secs(13)), state.auth.authenticate(&token)).await;
                            drop(token);
                            drop(slot);
                            let Ok(Ok(principal)) = result else { break; };
                            let unix = unix_now();
                            if principal.expires_at <= unix || !authorization_live(expires_at, authorized_at, Instant::now(), unix) { break; }
                            let next_expiry = principal.expires_at;
                            let renewed = match state.registry.lock() {
                                Ok(mut r) => r.renew(id, generation, principal).is_ok(),
                                Err(_) => false,
                            };
                            if !renewed { break; }
                            expires_at = next_expiry;
                            authorized_at = Instant::now();
                            if !send(&mut socket, &ServerMessage::Renewed { expires_at }).await { break; }
                            continue;
                        }
                        let result = match parsed {
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
