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

/// Concurrent-session limit of a Remote SaaS tenant (its plan, or 0 while it may hold none).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SessionLimit {
    pub tenant: Uuid,
    pub max_sessions: u32,
}

/// A policy decision that allowed the session.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Grant {
    pub limit: Option<SessionLimit>,
}

impl Grant {
    fn from_tenant_limit(body: &[u8]) -> Option<Self> {
        #[derive(serde::Deserialize)]
        #[serde(deny_unknown_fields)]
        struct TenantLimit {
            tenant: Uuid,
            max_sessions: u32,
        }
        let parsed: TenantLimit = serde_json::from_slice(body).ok()?;
        if parsed.tenant.is_nil() || parsed.max_sessions > 100_000 {
            return None;
        }
        Some(Self {
            limit: Some(SessionLimit {
                tenant: parsed.tenant,
                max_sessions: parsed.max_sessions,
            }),
        })
    }
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

    /// `None`: refused. 204: allowed without a broker-side limit (linked companies, personal
    /// accounts). 200 with `{"tenant": uuid, "max_sessions": n}`: allowed for a Remote SaaS tenant,
    /// which may hold at most `n` sessions at once on this broker.
    pub(crate) async fn allows(
        &self,
        pair: &SupportPair,
        unattended: bool,
        accept_session: Option<Uuid>,
    ) -> Option<Grant> {
        let response = self
            .client
            .post(self.endpoint.clone())
            .bearer_auth(self.token.as_str())
            .json(&serde_json::json!({
                "controller": &pair.controller, "host": &pair.host,
                "unattended": unattended, "accept_session": accept_session,
            }))
            .send()
            .await
            .ok()?;
        match response.status() {
            StatusCode::NO_CONTENT => Some(Grant { limit: None }),
            StatusCode::OK => {
                let mut stream = response.bytes_stream();
                let mut body = Vec::new();
                while let Some(chunk) = stream.next().await {
                    let chunk = chunk.ok()?;
                    if body.len() + chunk.len() > 512 {
                        return None;
                    }
                    body.extend_from_slice(&chunk);
                }
                Grant::from_tenant_limit(&body)
            }
            _ => None,
        }
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
    /// Device agent check-in: stores inventory and the last result, returns the next command.
    pub(crate) async fn agent(
        &self,
        binding: &DeviceBinding,
        inventory: Option<&str>,
        result: Option<&crate::protocol::AgentResult>,
    ) -> Result<Option<crate::protocol::AgentCommand>, AuthError> {
        let mut endpoint = self.endpoint.clone();
        endpoint.set_path("/api/internal/remote/agent");
        let response = self
            .client
            .post(endpoint)
            .bearer_auth(self.token.as_str())
            .json(&serde_json::json!({ "binding": binding, "inventory": inventory, "result": result }))
            .send()
            .await
            .map_err(|_| AuthError)?;
        match response.status() {
            StatusCode::NO_CONTENT => Ok(None),
            StatusCode::OK => {
                let mut stream = response.bytes_stream();
                let mut body = Vec::new();
                while let Some(chunk) = stream.next().await {
                    let chunk = chunk.map_err(|_| AuthError)?;
                    if body.len() + chunk.len() > crate::protocol::MAX_AGENT_SCRIPT + 1024 {
                        return Err(AuthError);
                    }
                    body.extend_from_slice(&chunk);
                }
                let command: crate::protocol::AgentCommand = serde_json::from_slice(&body).map_err(|_| AuthError)?;
                if !matches!(command.shell.as_str(), "powershell" | "sh")
                    || command.script.is_empty()
                    || command.script.len() > crate::protocol::MAX_AGENT_SCRIPT
                    || !(1..=3600).contains(&command.timeout_seconds)
                {
                    return Err(AuthError);
                }
                Ok(Some(command))
            }
            _ => Err(AuthError),
        }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tenant_limit_body_is_strict_and_bounded() {
        let tenant = Uuid::from_u128(7);
        assert_eq!(
            Grant::from_tenant_limit(format!(r#"{{"tenant":"{tenant}","max_sessions":3}}"#).as_bytes()),
            Some(Grant { limit: Some(SessionLimit { tenant, max_sessions: 3 }) })
        );
        for body in [
            "".to_owned(),
            "{}".to_owned(),
            format!(r#"{{"tenant":"{tenant}"}}"#),
            format!(r#"{{"tenant":"{tenant}","max_sessions":-1}}"#),
            format!(r#"{{"tenant":"{tenant}","max_sessions":100001}}"#),
            format!(r#"{{"tenant":"{}","max_sessions":3}}"#, Uuid::nil()),
            format!(r#"{{"tenant":"{tenant}","max_sessions":3,"unlimited":true}}"#),
        ] {
            assert_eq!(Grant::from_tenant_limit(body.as_bytes()), None, "{body}");
        }
    }
}
