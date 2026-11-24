//! Session-scoped, short-lived coturn REST credentials. The shared secret is
//! server-only. No relay allocations or credentials are issued before Accept.
//! Format: https://github.com/coturn/coturn/blob/master/README.turnserver
use base64::{Engine, engine::general_purpose::STANDARD};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha1::Sha1;
use std::{fmt, sync::Arc};
use uuid::Uuid;
use zeroize::Zeroizing;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IceServer {
    pub urls: Vec<String>,
    pub username: String,
    pub credential: String,
}
impl fmt::Debug for IceServer {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("IceServer")
            .field("urls", &self.urls)
            .finish_non_exhaustive()
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IceLease {
    pub expires_at: u64,
    pub servers: Vec<IceServer>,
}
#[derive(Clone)]
pub struct RelayPool {
    pub urls: Vec<String>,
    pub secret: Arc<Zeroizing<String>>,
}

#[derive(Clone)]
pub enum TurnConfig {
    Relay {
        pools: Vec<RelayPool>,
        ttl: u64,
    },
    LanOnly,
}
impl TurnConfig {
    pub fn from_env() -> Result<Option<Self>, String> {
        let urls = std::env::var("MIXEL_TURN_URLS").ok();
        let secret = std::env::var("MIXEL_TURN_SHARED_SECRET")
            .ok()
            .map(Zeroizing::new);
        let lan = std::env::var("MIXEL_ALLOW_LAN_ONLY").is_ok_and(|value| value == "1");
        match (urls, secret, lan) {
            (None, None, false) => Ok(None),
            (None, None, true) => Ok(Some(Self::LanOnly)),
            (Some(urls), Some(secret), false) => {
                let urls: Vec<_> = urls.split(',').map(str::trim).filter(|s| !s.is_empty()).map(str::to_owned).collect();
                let ttl = std::env::var("MIXEL_TURN_CREDENTIAL_TTL")
                    .unwrap_or_else(|_| "600".into())
                    .parse::<u64>()
                    .map_err(|_| "Invalid relay credential lifetime")?;
                let mut config = Self::relay(urls, secret, ttl)?;
                // Optional R-1 backup relay host in a second location (its own URLs and shared secret).
                let backup_urls = std::env::var("MIXEL_TURN_BACKUP_URLS").ok().filter(|s| !s.trim().is_empty());
                let backup_secret = std::env::var("MIXEL_TURN_BACKUP_SHARED_SECRET")
                    .ok()
                    .filter(|s| !s.is_empty())
                    .map(Zeroizing::new);
                if let (Some(b_urls), Some(b_secret)) = (backup_urls, backup_secret) {
                    let b_urls: Vec<_> = b_urls.split(',').map(str::trim).filter(|s| !s.is_empty()).map(str::to_owned).collect();
                    config = config.with_backup(b_urls, b_secret)?;
                }
                Ok(Some(config))
            }
            _ => Err(
                "Configure both relay URLs and shared secret, or explicit isolated LAN-only mode"
                    .into(),
            ),
        }
    }
    fn validate_pool(urls: &[String], secret: &Zeroizing<String>) -> Result<(), String> {
        if urls.is_empty()
            || urls.len() > 8
            || urls.iter().any(|url| !valid_url(url))
            || secret.len() < 32
            || secret.len() > 4096
            || secret.bytes().any(|byte| byte.is_ascii_control())
        {
            return Err("Invalid relay URLs, secret or credential lifetime".into());
        }
        Ok(())
    }
    pub fn relay(urls: Vec<String>, secret: Zeroizing<String>, ttl: u64) -> Result<Self, String> {
        Self::validate_pool(&urls, &secret)?;
        if !(60..=3600).contains(&ttl) {
            return Err("Invalid relay URLs, secret or credential lifetime".into());
        }
        Ok(Self::Relay {
            pools: vec![RelayPool {
                urls,
                secret: Arc::new(secret),
            }],
            ttl,
        })
    }
    pub fn with_backup(mut self, urls: Vec<String>, secret: Zeroizing<String>) -> Result<Self, String> {
        Self::validate_pool(&urls, &secret)?;
        if let Self::Relay { ref mut pools, .. } = self {
            if pools.len() >= 8 {
                return Err("Too many relay pools configured".into());
            }
            pools.push(RelayPool {
                urls,
                secret: Arc::new(secret),
            });
        }
        Ok(self)
    }
    pub fn issue(
        &self,
        session: Uuid,
        host: u32,
        authorization_expiry: u64,
        now: u64,
    ) -> Result<IceLease, String> {
        if session.is_nil()
            || !(100_000_000..=999_999_999).contains(&host)
            || authorization_expiry.saturating_sub(now) < 30
        {
            return Err("Current session authorization is required for relay credentials".into());
        }
        match self {
            Self::LanOnly => Ok(IceLease {
                expires_at: authorization_expiry.min(now.saturating_add(600)),
                servers: Vec::new(),
            }),
            Self::Relay { pools, ttl } => {
                let expires_at = authorization_expiry.min(now.saturating_add(*ttl));
                let username = format!("{expires_at}:mixel-{session}-{host}");
                let mut servers = Vec::with_capacity(pools.len());
                for pool in pools {
                    let mut mac = Hmac::<Sha1>::new_from_slice(pool.secret.as_bytes())
                        .map_err(|_| "Relay signer unavailable")?;
                    mac.update(username.as_bytes());
                    let credential = STANDARD.encode(mac.finalize().into_bytes());
                    servers.push(IceServer {
                        urls: pool.urls.clone(),
                        username: username.clone(),
                        credential,
                    });
                }
                Ok(IceLease {
                    expires_at,
                    servers,
                })
            }
        }
    }
}
fn valid_url(value: &str) -> bool {
    if value.len() > 1024
        || !value.is_ascii()
        || value
            .bytes()
            .any(|byte| byte.is_ascii_control() || byte.is_ascii_whitespace())
    {
        return false;
    }
    let (secure, rest) = if let Some(rest) = value.strip_prefix("turns:") {
        (true, rest)
    } else if let Some(rest) = value.strip_prefix("turn:") {
        (false, rest)
    } else {
        return false;
    };
    let (authority, query) = rest
        .split_once('?')
        .map_or((rest, None), |(authority, query)| (authority, Some(query)));
    if authority.is_empty()
        || authority.contains(['/', '@', '#', '%', '\\'])
        || !matches!(query, None | Some("transport=udp" | "transport=tcp"))
        || (secure && query == Some("transport=udp"))
    {
        return false;
    }
    let Ok(url) = reqwest::Url::parse(&format!("https://{authority}")) else {
        return false;
    };
    url.host_str().is_some()
        && url.port() != Some(0)
        && url.username().is_empty()
        && url.password().is_none()
        && url.path() == "/"
        && url.query().is_none()
        && url.fragment().is_none()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn issue_returns_primary_and_backup_relay_servers_with_distinct_credentials() {
        let primary_secret = Zeroizing::new("a".repeat(40));
        let backup_secret = Zeroizing::new("b".repeat(40));
        let config = TurnConfig::relay(
            vec!["turn:rs1.mixel.ch:3478?transport=udp".into(), "turns:rs1.mixel.ch:5350?transport=tcp".into()],
            primary_secret,
            600,
        )
        .unwrap()
        .with_backup(
            vec!["turn:rs2.mixel.ch:3478?transport=udp".into(), "turns:rs2.mixel.ch:5350?transport=tcp".into()],
            backup_secret,
        )
        .unwrap();
        let lease = config.issue(Uuid::new_v4(), 482_913_557, 2_000_000_000, 1_999_999_000).unwrap();
        assert_eq!(lease.servers.len(), 2);
        assert_eq!(lease.servers[0].urls[0], "turn:rs1.mixel.ch:3478?transport=udp");
        assert_eq!(lease.servers[1].urls[0], "turn:rs2.mixel.ch:3478?transport=udp");
        assert_ne!(lease.servers[0].credential, lease.servers[1].credential);
    }
}
