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
pub enum TurnConfig {
    Relay {
        urls: Vec<String>,
        secret: Arc<Zeroizing<String>>,
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
                let urls: Vec<_> = urls.split(',').map(str::trim).map(str::to_owned).collect();
                let ttl = std::env::var("MIXEL_TURN_CREDENTIAL_TTL")
                    .unwrap_or_else(|_| "600".into())
                    .parse::<u64>()
                    .map_err(|_| "Invalid relay credential lifetime")?;
                Self::relay(urls, secret, ttl).map(Some)
            }
            _ => Err(
                "Configure both relay URLs and shared secret, or explicit isolated LAN-only mode"
                    .into(),
            ),
        }
    }
    pub fn relay(urls: Vec<String>, secret: Zeroizing<String>, ttl: u64) -> Result<Self, String> {
        if urls.is_empty()
            || urls.len() > 8
            || urls.iter().any(|url| !valid_url(url))
            || secret.len() < 32
            || secret.len() > 4096
            || secret.bytes().any(|byte| byte.is_ascii_control())
            || !(60..=3600).contains(&ttl)
        {
            return Err("Invalid relay URLs, secret or credential lifetime".into());
        }
        Ok(Self::Relay {
            urls,
            secret: Arc::new(secret),
            ttl,
        })
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
            Self::Relay { urls, secret, ttl } => {
                let expires_at = authorization_expiry.min(now.saturating_add(*ttl));
                let username = format!("{expires_at}:mixel-{session}-{host}");
                let mut mac = Hmac::<Sha1>::new_from_slice(secret.as_bytes())
                    .map_err(|_| "Relay signer unavailable")?;
                mac.update(username.as_bytes());
                let credential = STANDARD.encode(mac.finalize().into_bytes());
                Ok(IceLease {
                    expires_at,
                    servers: vec![IceServer {
                        urls: urls.clone(),
                        username,
                        credential,
                    }],
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
