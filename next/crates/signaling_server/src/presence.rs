//! Online / last-seen record for the Remote portal. Every `INTERVAL` the broker sends the
//! connection IDs of the enrolled devices connected right now to one database function
//! (`public.remote_presence_report`, SQL 025) through the ISM API, with a token whose role can
//! execute that function only. The database keeps `last_seen` per connection ID; tenant members
//! read it through `remote_saas_device_presence`, which applies their access rights. Presence
//! between peers in a session is unchanged (the broker's live `presence` message).
use crate::registry::Registry;
use reqwest::{Client, Url, redirect::Policy};
use std::{
    sync::{Arc, Mutex},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use zeroize::Zeroizing;

pub const INTERVAL: Duration = Duration::from_secs(30);
/// More IDs than any broker holds (`MAX_PEERS`); the database refuses larger reports.
const MAX_REPORT: usize = 20_000;

#[derive(Debug, PartialEq, Eq)]
pub struct InvalidPresenceConfig;
impl std::fmt::Display for InvalidPresenceConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("invalid presence reporting configuration")
    }
}
impl std::error::Error for InvalidPresenceConfig {}

pub struct PresenceReporter {
    client: Client,
    endpoint: Url,
    token: Zeroizing<String>,
}

impl PresenceReporter {
    /// `endpoint`: HTTPS (or loopback HTTP) URL of `rpc/remote_presence_report`; `token`: a JWT.
    pub fn new(endpoint: Url, token: String) -> Result<Self, InvalidPresenceConfig> {
        let loopback = endpoint
            .host_str()
            .is_some_and(|host| host == "127.0.0.1" || host == "localhost");
        let token_ok = (20..=4096).contains(&token.len())
            && token.split('.').count() == 3
            && token
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'));
        if !(endpoint.scheme() == "https" || (endpoint.scheme() == "http" && loopback))
            || !endpoint.username().is_empty()
            || endpoint.password().is_some()
            || endpoint.query().is_some()
            || endpoint.fragment().is_some()
            || !endpoint.path().ends_with("/rpc/remote_presence_report")
            || !token_ok
        {
            return Err(InvalidPresenceConfig);
        }
        let client = Client::builder()
            .redirect(Policy::none())
            .timeout(Duration::from_secs(10))
            .build()
            .map_err(|_| InvalidPresenceConfig)?;
        Ok(Self {
            client,
            endpoint,
            token: Zeroizing::new(token),
        })
    }

    /// From `MIXEL_PRESENCE_URL` and `MIXEL_PRESENCE_TOKEN`; `None` when both are unset.
    pub fn from_env() -> Result<Option<Self>, InvalidPresenceConfig> {
        match (
            std::env::var("MIXEL_PRESENCE_URL"),
            std::env::var("MIXEL_PRESENCE_TOKEN"),
        ) {
            (Err(_), Err(_)) => Ok(None),
            (Ok(url), Ok(token)) => {
                Self::new(url.parse().map_err(|_| InvalidPresenceConfig)?, token).map(Some)
            }
            _ => Err(InvalidPresenceConfig),
        }
    }

    pub fn body(online: &[u32]) -> serde_json::Value {
        serde_json::json!({ "online": &online[..online.len().min(MAX_REPORT)] })
    }

    /// One report; `true` when the database accepted it.
    pub async fn report(&self, online: &[u32]) -> bool {
        self.client
            .post(self.endpoint.clone())
            .bearer_auth(self.token.as_str())
            .header("apikey", self.token.as_str())
            .json(&Self::body(online))
            .send()
            .await
            .is_ok_and(|response| response.status().is_success())
    }

    /// Reports every `INTERVAL` until the process ends. Failures are logged once per change.
    pub fn spawn(self, registry: Arc<Mutex<Registry>>) -> tokio::task::JoinHandle<()> {
        tokio::spawn(async move {
            let mut ticker = tokio::time::interval(INTERVAL);
            ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
            let mut failing = false;
            loop {
                ticker.tick().await;
                let unix = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map_or(0, |time| time.as_secs());
                let online = match registry.lock() {
                    Ok(registry) => registry.online_devices(unix),
                    Err(_) => return,
                };
                let ok = self.report(&online).await;
                if ok == failing {
                    eprintln!(
                        "presence report {}",
                        if ok { "accepted again" } else { "failed" }
                    );
                    failing = !ok;
                }
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const TOKEN: &str = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoieCJ9.c2lnbmF0dXJl";

    #[test]
    fn accepts_only_the_presence_function_over_https_or_loopback() {
        let ok = |url: &str| PresenceReporter::new(url.parse().unwrap(), TOKEN.into()).is_ok();
        assert!(ok(
            "https://ism-api.mixel.ch/rest/v1/rpc/remote_presence_report"
        ));
        assert!(ok("http://127.0.0.1:3000/rpc/remote_presence_report"));
        assert!(!ok(
            "http://ism-api.mixel.ch/rest/v1/rpc/remote_presence_report"
        ));
        assert!(!ok("https://ism-api.mixel.ch/rest/v1/rpc/other"));
        assert!(!ok(
            "https://user:pw@ism-api.mixel.ch/rest/v1/rpc/remote_presence_report"
        ));
        assert!(!ok(
            "https://ism-api.mixel.ch/rest/v1/rpc/remote_presence_report?x=1"
        ));
        let url: Url = "https://ism-api.mixel.ch/rest/v1/rpc/remote_presence_report"
            .parse()
            .unwrap();
        assert!(PresenceReporter::new(url.clone(), "not a jwt".into()).is_err());
        assert!(PresenceReporter::new(url, "a.b".into()).is_err());
    }

    #[test]
    fn body_lists_the_online_ids() {
        assert_eq!(
            PresenceReporter::body(&[123_456_789, 234_567_890]),
            serde_json::json!({ "online": [123_456_789, 234_567_890] })
        );
        assert_eq!(
            PresenceReporter::body(&[]),
            serde_json::json!({ "online": [] })
        );
    }

    #[tokio::test]
    async fn reports_to_the_endpoint_with_the_token() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut buffer = vec![0u8; 4096];
            let mut read = 0;
            loop {
                read += socket.read(&mut buffer[read..]).await.unwrap();
                let text = String::from_utf8_lossy(&buffer[..read]).to_string();
                if text.contains("\r\n\r\n") && text.ends_with('}') {
                    break;
                }
            }
            socket
                .write_all(b"HTTP/1.1 204 No Content\r\ncontent-length: 0\r\n\r\n")
                .await
                .unwrap();
            String::from_utf8_lossy(&buffer[..read]).to_string()
        });
        let reporter = PresenceReporter::new(
            format!("http://127.0.0.1:{port}/rpc/remote_presence_report")
                .parse()
                .unwrap(),
            TOKEN.into(),
        )
        .unwrap();
        assert!(reporter.report(&[123_456_789]).await);
        let request = server.await.unwrap();
        assert!(request.starts_with("POST /rpc/remote_presence_report"));
        assert!(
            request
                .to_ascii_lowercase()
                .contains(&format!("authorization: bearer {TOKEN}").to_ascii_lowercase())
        );
        assert!(request.ends_with("{\"online\":[123456789]}"));
    }
}
