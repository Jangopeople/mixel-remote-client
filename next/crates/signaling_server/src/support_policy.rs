use crate::{auth::AuthError, protocol::PairingPrompt};
use core_protocol::{delegation::DeviceTicket, device_login::DeviceLoginChallenge};
use futures_util::StreamExt;
use reqwest::{Client, StatusCode, Url, redirect::Policy};
use serde::Serialize;
use std::time::Duration;
use uuid::Uuid;
use zeroize::Zeroizing;

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub(crate) struct DeviceBinding {
    profile: Uuid,
    device: Uuid,
    generation: String,
    connection_id: u32,
    public_key: String,
}

impl From<&DeviceTicket> for DeviceBinding {
    fn from(ticket: &DeviceTicket) -> Self {
        Self::from_challenge(&ticket.identity, &ticket.public_key)
    }
}

impl DeviceBinding {
    fn from_challenge(identity: &DeviceLoginChallenge, key: &[u8; 32]) -> Self {
        Self {
            profile: Uuid::from_bytes(identity.profile),
            device: Uuid::from_bytes(identity.device),
            generation: identity.generation.to_string(),
            connection_id: identity.connection_id,
            public_key: key.iter().map(|byte| format!("{byte:02x}")).collect(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub(crate) struct SupportPair {
    pub controller: DeviceBinding,
    pub host: DeviceBinding,
}

#[derive(Clone)]
pub struct SupportPolicy {
    client: Client,
    endpoint: Url,
    token: Zeroizing<String>,
}

impl SupportPolicy {
    pub fn new(endpoint: Url, token: String) -> Result<Self, AuthError> {
        Self::with_endpoint(endpoint, token, "/api/internal/remote/authorize")
    }

    pub fn for_startup_host(endpoint: Url, token: String) -> Result<Self, AuthError> {
        Self::with_endpoint(endpoint, token, "/api/internal/remote/startup-host")
    }

    fn with_endpoint(endpoint: Url, token: String, path: &str) -> Result<Self, AuthError> {
        let loopback = endpoint.host_str().is_some_and(|host| {
            host.parse::<std::net::Ipv4Addr>()
                .is_ok_and(|ip| ip.is_loopback())
        });
        if (endpoint.scheme() != "https" && !(endpoint.scheme() == "http" && loopback))
            || !endpoint.has_host()
            || !endpoint.username().is_empty()
            || endpoint.password().is_some()
            || endpoint.query().is_some()
            || endpoint.fragment().is_some()
            || endpoint.path() != path
            || token.len() != 64
            || !token.bytes().all(|byte| byte.is_ascii_hexdigit())
        {
            return Err(AuthError);
        }
        let client = Client::builder()
            .redirect(Policy::none())
            .timeout(Duration::from_secs(2))
            .build()
            .map_err(|_| AuthError)?;
        Ok(Self {
            client,
            endpoint,
            token: Zeroizing::new(token),
        })
    }

    pub(crate) async fn allows(
        &self,
        pair: &SupportPair,
        unattended: bool,
        accept_session: Option<Uuid>,
    ) -> bool {
        self.client
            .post(self.endpoint.clone())
            .bearer_auth(self.token.as_str())
            .json(&serde_json::json!({
                "controller": &pair.controller, "host": &pair.host,
                "unattended": unattended, "accept_session": accept_session,
            }))
            .send()
            .await
            .is_ok_and(|response| response.status() == StatusCode::NO_CONTENT)
    }
    pub(crate) async fn allows_startup_host(
        &self,
        identity: &DeviceLoginChallenge,
        key: &[u8; 32],
    ) -> bool {
        let Some(tenant) = identity.tenant.map(Uuid::from_bytes) else {
            return false;
        };
        self.client
            .post(self.endpoint.clone())
            .bearer_auth(self.token.as_str())
            .json(&serde_json::json!({
                "binding": DeviceBinding::from_challenge(identity, key),
                "tenant": tenant,
            }))
            .send()
            .await
            .is_ok_and(|response| response.status() == StatusCode::NO_CONTENT)
    }
    pub(crate) async fn pairing(
        &self,
        binding: &DeviceBinding,
        confirmation: Option<Uuid>,
    ) -> Result<Option<PairingPrompt>, AuthError> {
        let mut endpoint = self.endpoint.clone();
        endpoint.set_path("/api/internal/remote/pairing");
        let response = self
            .client
            .post(endpoint)
            .bearer_auth(self.token.as_str())
            .json(&serde_json::json!({ "binding": binding, "confirmation": confirmation }))
            .send()
            .await
            .map_err(|_| AuthError)?;
        match response.status() {
            StatusCode::NO_CONTENT => Ok(None),
            StatusCode::OK if confirmation.is_none() => {
                let mut stream = response.bytes_stream();
                let mut body = Vec::new();
                while let Some(chunk) = stream.next().await {
                    let chunk = chunk.map_err(|_| AuthError)?;
                    if body.len() + chunk.len() > 2048 {
                        return Err(AuthError);
                    }
                    body.extend_from_slice(&chunk);
                }
                serde_json::from_slice(&body)
                    .map(Some)
                    .map_err(|_| AuthError)
            }
            _ => Err(AuthError),
        }
    }
}
