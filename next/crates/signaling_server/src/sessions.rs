//! Session report for Remote SaaS companies. The registry journals when a SaaS session first
//! connects and when it ends for good (`registry::SessionEvent`); every `INTERVAL` this reporter
//! sends the new events to one database function (`public.remote_session_report`, SQL 029) through
//! the ISM API, with the presence token (role `mixel_remote_presence`, which can execute the presence
//! and session functions only). At least every `OPEN_INTERVAL` the report also lists every session
//! running here, so the database closes sessions whose end a broker restart lost. Failed batches
//! are retried in order (bounded); the registry mutex is never held across a network call.
use crate::registry::{Registry, SessionEvent};
use reqwest::{Client, StatusCode, Url, redirect::Policy};
use std::{
    collections::VecDeque,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use uuid::Uuid;
use zeroize::Zeroizing;

pub const INTERVAL: Duration = Duration::from_secs(5);
/// The running-session list goes out at least this often.
pub const OPEN_INTERVAL: Duration = Duration::from_secs(60);
/// Events per request (the database refuses more).
const MAX_BATCH: usize = 1000;
/// Running records per request (the database refuses more; above any broker's session capacity).
const MAX_OPEN: usize = 20_000;
/// Events kept for retries while the database is unreachable; the oldest are dropped beyond.
const MAX_QUEUE: usize = 20_000;
/// Requests per tick, so a long backlog does not delay the next tick much.
const MAX_REQUESTS_PER_TICK: usize = 10;
const PRESENCE_PATH: &str = "/rpc/remote_presence_report";
const SESSION_PATH: &str = "/rpc/remote_session_report";

#[derive(Debug, PartialEq, Eq)]
pub struct InvalidSessionReportConfig;
impl std::fmt::Display for InvalidSessionReportConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("invalid session reporting configuration")
    }
}
impl std::error::Error for InvalidSessionReportConfig {}

#[derive(Debug, PartialEq, Eq)]
enum Outcome {
    Accepted,
    /// The database refused the body (400): retrying the same batch cannot succeed.
    Refused,
    Failed,
}

#[derive(Default)]
struct Queue {
    events: VecDeque<SessionEvent>,
    open_sent: Option<Instant>,
    failing: bool,
    dropped: u64,
}

pub struct SessionReporter {
    client: Client,
    endpoint: Url,
    token: Zeroizing<String>,
    queue: tokio::sync::Mutex<Queue>,
}

impl SessionReporter {
    /// `endpoint`: HTTPS (or loopback HTTP) URL of `rpc/remote_session_report`; `token`: a JWT.
    pub fn new(endpoint: Url, token: String) -> Result<Self, InvalidSessionReportConfig> {
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
            || !endpoint.path().ends_with(SESSION_PATH)
            || !token_ok
        {
            return Err(InvalidSessionReportConfig);
        }
        let client = Client::builder()
            .redirect(Policy::none())
            .timeout(Duration::from_secs(10))
            .build()
            .map_err(|_| InvalidSessionReportConfig)?;
        Ok(Self {
            client,
            endpoint,
            token: Zeroizing::new(token),
            queue: tokio::sync::Mutex::new(Queue::default()),
        })
    }

    /// The session function next to the presence function (`.../rpc/remote_presence_report`).
    pub fn endpoint_for(presence: &str) -> Result<Url, InvalidSessionReportConfig> {
        let mut url: Url = presence.parse().map_err(|_| InvalidSessionReportConfig)?;
        let path = url
            .path()
            .strip_suffix(PRESENCE_PATH)
            .ok_or(InvalidSessionReportConfig)?
            .to_owned();
        url.set_path(&format!("{path}{SESSION_PATH}"));
        Ok(url)
    }

    /// From `MIXEL_PRESENCE_URL` and `MIXEL_PRESENCE_TOKEN` (same API and token as presence);
    /// `None` when both are unset.
    pub fn from_env() -> Result<Option<Self>, InvalidSessionReportConfig> {
        match (
            std::env::var("MIXEL_PRESENCE_URL"),
            std::env::var("MIXEL_PRESENCE_TOKEN"),
        ) {
            (Err(_), Err(_)) => Ok(None),
            (Ok(url), Ok(token)) => Self::new(Self::endpoint_for(&url)?, token).map(Some),
            _ => Err(InvalidSessionReportConfig),
        }
    }

    pub fn body(events: &[SessionEvent], open: Option<&[Uuid]>) -> serde_json::Value {
        let mut body = serde_json::json!({ "events": &events[..events.len().min(MAX_BATCH)] });
        if let Some(open) = open {
            body["open"] = serde_json::json!(&open[..open.len().min(MAX_OPEN)]);
        }
        body
    }

    async fn post(&self, body: &serde_json::Value) -> Outcome {
        match self
            .client
            .post(self.endpoint.clone())
            .bearer_auth(self.token.as_str())
            .header("apikey", self.token.as_str())
            .json(body)
            .send()
            .await
        {
            Ok(response) if response.status().is_success() => Outcome::Accepted,
            Ok(response) if response.status() == StatusCode::BAD_REQUEST => Outcome::Refused,
            _ => Outcome::Failed,
        }
    }

    /// Takes the registry's new events (and, when due, its running records) and sends what is
    /// queued. `true` when everything queued was delivered.
    pub async fn run_once(&self, registry: &Mutex<Registry>, now: Instant) -> bool {
        let mut queue = self.queue.lock().await;
        let open_due = queue
            .open_sent
            .is_none_or(|sent| now.duration_since(sent) >= OPEN_INTERVAL);
        // One registry lock: the running list matches exactly the events taken so far.
        let (events, open) = match registry.lock() {
            Ok(mut registry) => (
                registry.take_session_events(),
                open_due.then(|| registry.running_records()),
            ),
            Err(_) => return false,
        };
        queue.events.extend(events);
        let overflow = queue.events.len().saturating_sub(MAX_QUEUE);
        if overflow > 0 {
            queue.events.drain(..overflow);
            queue.dropped += overflow as u64;
            eprintln!(
                "session report backlog full: {} oldest session events dropped",
                queue.dropped
            );
        }
        let mut open = open;
        for _ in 0..MAX_REQUESTS_PER_TICK {
            let count = queue.events.len().min(MAX_BATCH);
            // The running list only goes with the last batch, after every earlier event.
            let with_open = if count == queue.events.len() { open.take() } else { None };
            if count == 0 && with_open.is_none() {
                break;
            }
            let batch: Vec<SessionEvent> = queue.events.iter().take(count).cloned().collect();
            let body = Self::body(&batch, with_open.as_deref());
            match self.post(&body).await {
                outcome @ (Outcome::Accepted | Outcome::Refused) => {
                    if outcome == Outcome::Refused {
                        eprintln!("session report refused by the database: {count} events dropped");
                    }
                    queue.events.drain(..count);
                    if with_open.is_some() && outcome == Outcome::Accepted {
                        queue.open_sent = Some(now);
                    }
                    if queue.failing {
                        eprintln!("session report accepted again");
                        queue.failing = false;
                    }
                }
                Outcome::Failed => {
                    if !queue.failing {
                        eprintln!("session report failed; {} events kept for retry", queue.events.len());
                        queue.failing = true;
                    }
                    return false;
                }
            }
        }
        queue.events.is_empty()
    }

    /// Reports every `INTERVAL` until the process ends (and enables the registry's journal).
    pub fn spawn(self: Arc<Self>, registry: Arc<Mutex<Registry>>) -> tokio::task::JoinHandle<()> {
        if let Ok(mut registry) = registry.lock() {
            registry.enable_session_journal();
        }
        tokio::spawn(async move {
            let mut ticker = tokio::time::interval(INTERVAL);
            ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
            loop {
                ticker.tick().await;
                self.run_once(&registry, Instant::now()).await;
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const TOKEN: &str = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoieCJ9.c2lnbmF0dXJl";

    #[test]
    fn the_endpoint_sits_next_to_the_presence_function() {
        assert_eq!(
            SessionReporter::endpoint_for("https://ism-api.mixel.ch/rest/v1/rpc/remote_presence_report").unwrap().as_str(),
            "https://ism-api.mixel.ch/rest/v1/rpc/remote_session_report"
        );
        assert_eq!(
            SessionReporter::endpoint_for("http://127.0.0.1:3000/rpc/remote_presence_report").unwrap().as_str(),
            "http://127.0.0.1:3000/rpc/remote_session_report"
        );
        assert!(SessionReporter::endpoint_for("https://ism-api.mixel.ch/rest/v1/rpc/other").is_err());
        assert!(SessionReporter::endpoint_for("not a url").is_err());
        let ok = |url: &str| SessionReporter::new(url.parse().unwrap(), TOKEN.into()).is_ok();
        assert!(ok("https://ism-api.mixel.ch/rest/v1/rpc/remote_session_report"));
        assert!(ok("http://localhost:3000/rpc/remote_session_report"));
        assert!(!ok("http://ism-api.mixel.ch/rest/v1/rpc/remote_session_report"));
        assert!(!ok("https://ism-api.mixel.ch/rest/v1/rpc/remote_presence_report"));
        assert!(!ok("https://user:pw@ism-api.mixel.ch/rest/v1/rpc/remote_session_report"));
        assert!(!ok("https://ism-api.mixel.ch/rest/v1/rpc/remote_session_report#x"));
        let url: Url = "https://ism-api.mixel.ch/rest/v1/rpc/remote_session_report".parse().unwrap();
        assert!(SessionReporter::new(url, "not a jwt".into()).is_err());
    }

    #[test]
    fn body_has_the_database_shape() {
        let start = SessionEvent::Start {
            id: Uuid::from_u128(1),
            tenant: Uuid::from_u128(2),
            technician: Uuid::from_u128(3),
            controller_device: Uuid::from_u128(4),
            controller_connection: 111_111_111,
            device: Uuid::from_u128(5),
            connection: 222_222_222,
            unattended: true,
            colleague: false,
            at: 1_800_000_000,
        };
        let end = SessionEvent::End { id: Uuid::from_u128(1), at: 1_800_000_100 };
        assert_eq!(
            SessionReporter::body(&[start, end], Some(&[Uuid::from_u128(9)])),
            serde_json::json!({
                "events": [
                    {"type": "start", "id": Uuid::from_u128(1), "tenant": Uuid::from_u128(2),
                     "technician": Uuid::from_u128(3), "controller_device": Uuid::from_u128(4),
                     "controller_connection": 111_111_111, "device": Uuid::from_u128(5),
                     "connection": 222_222_222, "unattended": true, "colleague": false, "at": 1_800_000_000u64},
                    {"type": "end", "id": Uuid::from_u128(1), "at": 1_800_000_100u64}
                ],
                "open": [Uuid::from_u128(9)]
            })
        );
        assert_eq!(SessionReporter::body(&[], None), serde_json::json!({ "events": [] }));
    }

    /// Answers each request with the next status and hands back the bodies.
    async fn database(statuses: Vec<u16>) -> (Url, tokio::sync::mpsc::UnboundedReceiver<(String, serde_json::Value)>) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
        tokio::spawn(async move {
            for status in statuses {
                let (mut socket, _) = listener.accept().await.unwrap();
                let mut buffer = Vec::new();
                let mut chunk = vec![0u8; 65536];
                let (head, body) = loop {
                    let read = socket.read(&mut chunk).await.unwrap();
                    assert!(read > 0, "connection closed early");
                    buffer.extend_from_slice(&chunk[..read]);
                    let text = String::from_utf8_lossy(&buffer).to_string();
                    if let Some(split) = text.find("\r\n\r\n") {
                        let head = text[..split].to_string();
                        let length: usize = head
                            .lines()
                            .find_map(|line| line.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse().unwrap()))
                            .unwrap_or(0);
                        if buffer.len() >= split + 4 + length {
                            break (head, buffer[split + 4..split + 4 + length].to_vec());
                        }
                    }
                };
                socket
                    .write_all(format!("HTTP/1.1 {status} X\r\ncontent-length: 0\r\nconnection: close\r\n\r\n").as_bytes())
                    .await
                    .unwrap();
                tx.send((head, serde_json::from_slice(&body).unwrap())).unwrap();
            }
        });
        (format!("http://127.0.0.1:{port}/rpc/remote_session_report").parse().unwrap(), rx)
    }

    fn end(n: u128) -> SessionEvent {
        SessionEvent::End { id: Uuid::from_u128(n), at: 1_800_000_000 }
    }

    #[tokio::test]
    async fn retries_in_order_sends_the_running_list_every_minute_and_drops_refused_batches() {
        let (url, mut bodies) = database(vec![503, 204, 204, 400, 204]).await;
        let reporter = SessionReporter::new(url, TOKEN.into()).unwrap();
        let registry = Mutex::new(Registry::default());
        registry.lock().unwrap().enable_session_journal();
        let now = Instant::now();
        registry.lock().unwrap().journal_for_test(end(1));
        // The database is down: kept.
        assert!(!reporter.run_once(&registry, now).await);
        let (head, body) = bodies.recv().await.unwrap();
        assert!(head.starts_with("POST /rpc/remote_session_report"));
        assert!(head.to_ascii_lowercase().contains(&format!("authorization: bearer {TOKEN}").to_ascii_lowercase()));
        assert_eq!(body, serde_json::json!({ "events": [end(1)], "open": [] }));
        // Back: the kept event first, then the new one, with the running list (not sent yet).
        registry.lock().unwrap().journal_for_test(end(2));
        assert!(reporter.run_once(&registry, now).await);
        assert_eq!(bodies.recv().await.unwrap().1, serde_json::json!({ "events": [end(1), end(2)], "open": [] }));
        // Within the minute: events only.
        registry.lock().unwrap().journal_for_test(end(3));
        assert!(reporter.run_once(&registry, now + Duration::from_secs(5)).await);
        assert_eq!(bodies.recv().await.unwrap().1, serde_json::json!({ "events": [end(3)] }));
        // Nothing new within the minute: no request at all.
        assert!(reporter.run_once(&registry, now + Duration::from_secs(10)).await);
        // A body the database refuses is dropped, not retried forever.
        registry.lock().unwrap().journal_for_test(end(4));
        assert!(reporter.run_once(&registry, now + Duration::from_secs(15)).await);
        assert_eq!(bodies.recv().await.unwrap().1, serde_json::json!({ "events": [end(4)] }));
        // A minute after the last running list: the list again, even without events.
        assert!(reporter.run_once(&registry, now + Duration::from_secs(61)).await);
        assert_eq!(bodies.recv().await.unwrap().1, serde_json::json!({ "events": [], "open": [] }));
        assert!(bodies.try_recv().is_err());
    }

    #[tokio::test]
    async fn a_backlog_goes_in_batches_with_the_running_list_last() {
        let (url, mut bodies) = database(vec![204, 204]).await;
        let reporter = SessionReporter::new(url, TOKEN.into()).unwrap();
        let registry = Mutex::new(Registry::default());
        registry.lock().unwrap().enable_session_journal();
        for n in 0..1500 {
            registry.lock().unwrap().journal_for_test(end(n));
        }
        assert!(reporter.run_once(&registry, Instant::now()).await);
        let first = bodies.recv().await.unwrap().1;
        assert_eq!(first["events"].as_array().unwrap().len(), 1000);
        assert_eq!(first["events"][0], serde_json::json!(end(0)));
        assert!(first.get("open").is_none());
        let second = bodies.recv().await.unwrap().1;
        assert_eq!(second["events"].as_array().unwrap().len(), 500);
        assert_eq!(second["events"][0], serde_json::json!(end(1000)));
        assert_eq!(second["open"], serde_json::json!([]));
    }
}
