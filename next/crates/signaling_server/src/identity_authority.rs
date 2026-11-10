//! Run as a separate trusted process. The connection broker receives no ISM
//! bearer and must have no access to this service's private signing-key mount.
use crate::{auth::IsmAuth, support_policy::SupportPolicy};
use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use core_protocol::delegation::{
    DelegationProof, DeviceTicket, MAX_TICKET_LIFETIME, MAX_UNATTENDED_HOST_LIFETIME,
};
use ed25519_dalek::{SigningKey, VerifyingKey};
use serde::Deserialize;
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio::sync::Semaphore;
use uuid::Uuid;

#[derive(Clone)]
pub struct Authority {
    auth: Arc<IsmAuth>,
    signer: Arc<SigningKey>,
    slots: Arc<Semaphore>,
    used: ReplayCache,
    startup_policy: Option<SupportPolicy>,
}
type ReplayCache = Arc<Mutex<HashMap<([u8; 16], [u8; 32]), Instant>>>;
impl Authority {
    pub fn new(auth: IsmAuth, signer: SigningKey) -> Self {
        Self {
            auth: Arc::new(auth),
            signer: Arc::new(signer),
            slots: Arc::new(Semaphore::new(32)),
            used: Arc::new(Mutex::new(HashMap::new())),
            startup_policy: None,
        }
    }

    pub fn with_startup_policy(mut self, policy: SupportPolicy) -> Self {
        self.startup_policy = Some(policy);
        self
    }

    fn consume_proof(&self, proof: &DelegationProof) -> Result<(), StatusCode> {
        let mut used = self
            .used
            .lock()
            .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?;
        let time = Instant::now();
        used.retain(|_, expiry| time < *expiry);
        let id = (proof.challenge.device, proof.challenge.nonce);
        if used.contains_key(&id) {
            return Err(StatusCode::CONFLICT);
        }
        if used.len() >= 4096 {
            return Err(StatusCode::SERVICE_UNAVAILABLE);
        }
        used.insert(id, time + Duration::from_secs(40));
        Ok(())
    }
}
pub fn router(authority: Authority) -> Router {
    Router::new()
        .route("/healthz", get(|| async { "ok\n" }))
        .route("/v1/device-ticket", post(issue))
        .route("/v1/startup-host-ticket", post(issue_startup_host))
        .layer(DefaultBodyLimit::max(8192))
        .with_state(authority)
}
fn unix() -> Result<u64, StatusCode> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs())
        .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)
}
async fn issue(
    State(authority): State<Authority>,
    headers: HeaderMap,
    Json(proof): Json<DelegationProof>,
) -> Response {
    ticket_response(issue_verified(&authority, &headers, proof).await)
}

fn ticket_response(result: Result<DeviceTicket, StatusCode>) -> Response {
    match result {
        Ok(ticket) => ([("cache-control", "no-store")], Json(ticket)).into_response(),
        Err(code) => code.into_response(),
    }
}
async fn issue_verified(
    authority: &Authority,
    headers: &HeaderMap,
    proof: DelegationProof,
) -> Result<DeviceTicket, StatusCode> {
    // This native endpoint has no cookie authentication or cross-origin web
    // permission. A future browser adapter must use an exact origin policy.
    if headers.contains_key("origin") {
        return Err(StatusCode::FORBIDDEN);
    }
    let bearer = headers
        .get("authorization")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .filter(|value| !value.is_empty() && value.len() <= 16000)
        .ok_or(StatusCode::UNAUTHORIZED)?;
    let _slot = authority
        .slots
        .clone()
        .try_acquire_owned()
        .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?;
    let device = Uuid::from_bytes(proof.challenge.device);
    if device.is_nil() {
        return Err(StatusCode::BAD_REQUEST);
    }
    let enrolled = tokio::time::timeout(
        Duration::from_secs(17),
        authority.auth.authenticate_device(bearer, device),
    )
    .await
    .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?
    .map_err(|_| StatusCode::FORBIDDEN)?;
    let now = unix()?;
    let identity = &proof.challenge;
    if identity.profile != *enrolled.principal.user.as_bytes()
        || identity.tenant != enrolled.principal.tenant.map(|value| *value.as_bytes())
        || identity.connection_id != enrolled.connection_id
        || identity.generation != enrolled.generation
        || enrolled.principal.expires_at.saturating_sub(now) < 30
    {
        return Err(StatusCode::FORBIDDEN);
    }
    proof
        .verify(&enrolled.public_key, now)
        .map_err(|_| StatusCode::FORBIDDEN)?;
    authority.consume_proof(&proof)?;
    DeviceTicket::issue(
        proof.challenge,
        enrolled.public_key,
        enrolled.principal.staff,
        now,
        enrolled
            .principal
            .expires_at
            .min(now.saturating_add(MAX_TICKET_LIFETIME)),
        &authority.signer,
    )
    .map_err(|_| StatusCode::FORBIDDEN)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StartupHostRequest {
    proof: DelegationProof,
    public_key: [u8; 32],
}

async fn issue_startup_host(
    State(authority): State<Authority>,
    headers: HeaderMap,
    Json(request): Json<StartupHostRequest>,
) -> Response {
    ticket_response(startup_host_verified(&authority, &headers, request).await)
}

async fn startup_host_verified(
    authority: &Authority,
    headers: &HeaderMap,
    request: StartupHostRequest,
) -> Result<DeviceTicket, StatusCode> {
    let policy = authority
        .startup_policy
        .as_ref()
        .ok_or(StatusCode::SERVICE_UNAVAILABLE)?;
    if headers.contains_key("origin")
        || headers.contains_key("authorization")
        || request.proof.challenge.tenant.is_none()
    {
        return Err(StatusCode::FORBIDDEN);
    }
    let _slot = authority
        .slots
        .try_acquire()
        .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?;
    let key = VerifyingKey::from_bytes(&request.public_key).map_err(|_| StatusCode::FORBIDDEN)?;
    request
        .proof
        .verify_unattended_host(&key, unix()?)
        .map_err(|_| StatusCode::FORBIDDEN)?;
    if !policy
        .allows_startup_host(&request.proof.challenge, &request.public_key)
        .await
    {
        return Err(StatusCode::FORBIDDEN);
    }
    let now = unix()?;
    request
        .proof
        .verify_unattended_host(&key, now)
        .map_err(|_| StatusCode::FORBIDDEN)?;
    authority.consume_proof(&request.proof)?;
    DeviceTicket::issue_unattended_host(
        request.proof.challenge,
        key,
        now,
        now.saturating_add(MAX_UNATTENDED_HOST_LIFETIME),
        &authority.signer,
    )
    .map_err(|_| StatusCode::FORBIDDEN)
}

#[cfg(test)]
mod tests {
    use super::*;
    use core_protocol::device_login::DeviceLoginChallenge;
    use std::sync::atomic::{AtomicBool, Ordering};

    #[tokio::test]
    async fn startup_issuer_checks_device_proof_policy_and_replay_over_http() {
        let signer = SigningKey::from_bytes(&[9; 32]);
        let roots = [signer.verifying_key()];
        let device = SigningKey::from_bytes(&[7; 32]);
        let key = device.verifying_key().to_bytes();
        let claim = DeviceLoginChallenge {
            nonce: [1; 32],
            device: [2; 16],
            profile: [3; 16],
            tenant: Some([4; 16]),
            connection_id: 123_456_789,
            generation: 1,
        };
        let allowed = Arc::new(AtomicBool::new(false));
        let gate = allowed.clone();
        let token = "a".repeat(64);
        let expected_token = format!("Bearer {token}");
        let policy = Router::new().route(
            "/api/internal/remote/startup-host",
            post(
                move |headers: HeaderMap, Json(body): Json<serde_json::Value>| {
                    let gate = gate.clone();
                    let expected_token = expected_token.clone();
                    async move {
                        assert_eq!(headers["authorization"], expected_token);
                        assert_eq!(body["tenant"], Uuid::from_bytes([4; 16]).to_string());
                        assert_eq!(
                            body["binding"]["device"],
                            Uuid::from_bytes([2; 16]).to_string()
                        );
                        assert_eq!(body["binding"]["generation"], "1");
                        assert_eq!(
                            body["binding"]["public_key"],
                            key.iter()
                                .map(|value| format!("{value:02x}"))
                                .collect::<String>()
                        );
                        if gate.load(Ordering::Acquire) {
                            StatusCode::NO_CONTENT
                        } else {
                            StatusCode::FORBIDDEN
                        }
                    }
                },
            ),
        );
        let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let policy_url = format!(
            "http://{}/api/internal/remote/startup-host",
            socket.local_addr().unwrap()
        );
        let policy_task = tokio::spawn(axum::serve(socket, policy).into_future());
        let auth = IsmAuth::new(
            "https://identity.example.test".parse().unwrap(),
            "public".into(),
        )
        .unwrap();
        let policy = SupportPolicy::for_startup_host(policy_url.parse().unwrap(), token).unwrap();
        let issuer = Authority::new(auth, signer).with_startup_policy(policy);
        let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!(
            "http://{}/v1/startup-host-ticket",
            socket.local_addr().unwrap()
        );
        let issuer_task = tokio::spawn(axum::serve(socket, router(issuer)).into_future());
        let client = reqwest::Client::new();
        let proof =
            DelegationProof::new_unattended_host(claim.clone(), unix().unwrap(), &device).unwrap();
        let body = serde_json::json!({"proof":proof,"public_key":key});
        assert_eq!(
            client.post(&url).json(&body).send().await.unwrap().status(),
            StatusCode::FORBIDDEN
        );
        allowed.store(true, Ordering::Release);
        let ordinary = DelegationProof::new(claim, unix().unwrap(), &device).unwrap();
        let ordinary = serde_json::json!({"proof":ordinary,"public_key":key});
        assert_eq!(
            client
                .post(&url)
                .json(&ordinary)
                .send()
                .await
                .unwrap()
                .status(),
            StatusCode::FORBIDDEN
        );
        assert_eq!(
            client
                .post(&url)
                .header("origin", "https://example.test")
                .json(&body)
                .send()
                .await
                .unwrap()
                .status(),
            StatusCode::FORBIDDEN
        );
        let response = client.post(&url).json(&body).send().await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()["cache-control"], "no-store");
        let ticket: DeviceTicket = response.json().await.unwrap();
        assert!(
            ticket
                .verify(&roots, unix().unwrap())
                .unwrap()
                .claims()
                .unattended_host_only()
        );
        assert_eq!(
            ticket.expires_at - ticket.issued_at,
            MAX_UNATTENDED_HOST_LIFETIME
        );
        assert_eq!(
            client.post(&url).json(&body).send().await.unwrap().status(),
            StatusCode::CONFLICT
        );
        allowed.store(false, Ordering::Release);
        let mut changed = proof.challenge;
        changed.nonce = [8; 32];
        let fresh =
            DelegationProof::new_unattended_host(changed, unix().unwrap(), &device).unwrap();
        let body = serde_json::json!({"proof":fresh,"public_key":key});
        assert_eq!(
            client.post(&url).json(&body).send().await.unwrap().status(),
            StatusCode::FORBIDDEN
        );
        issuer_task.abort();
        policy_task.abort();
    }
}
