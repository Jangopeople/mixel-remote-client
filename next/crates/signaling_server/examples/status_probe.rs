//! OPS: the synthetic connection check of the Mixel Remote status monitor (runs on mixel-vps every
//! 5 minutes, see `next/deployment/ops/`). Two probe computers of the dedicated tenant "Mixel Status
//! Probe" sign in with the probe account, get device tickets from the identity authority, log in to
//! the public connection broker, ask for and accept a session, take relay credentials and open a
//! relay-only WebRTC data channel through the TURN server, exchange a ping, and close the session.
//! No customer data is touched.
//!
//!   status_probe run      reads MIXEL_PROBE_* from the environment, prints one JSON line, exit 0/1
//!   status_probe setup    signs in, creates the probe tenant if needed and adds the two probe
//!                         computers; prints the MIXEL_PROBE_A_*/B_* lines to keep (root only)
//!
//! Environment: MIXEL_PROBE_AUTH (https://ism-api.mixel.ch), MIXEL_PROBE_ANON (public anon key),
//! MIXEL_PROBE_EMAIL, MIXEL_PROBE_PASSWORD, MIXEL_PROBE_WS (wss://rs.mixel.ch:9444/v1/ws),
//! MIXEL_PROBE_A_DEVICE/_A_SEED and MIXEL_PROBE_B_DEVICE/_B_SEED (after setup).
use core_protocol::{
    delegation::{DelegationProof, DeviceTicket},
    device_login::DeviceLoginChallenge,
    transport::{NativeDataChannel, NativeDataChannelEvent, NativeIceServer, NativeSessionDescription, NativeTransport},
};
use ed25519_dalek::SigningKey;
use futures_util::{SinkExt, StreamExt};
use rand_core::{OsRng, RngCore};
use signaling_server::{
    auth::IsmAuth,
    protocol::{ClientMessage, ServerMessage, Signal},
};
use std::{
    sync::Arc,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;
use uuid::Uuid;

const TENANT_NAME: &str = "Mixel Status Probe";

fn env(name: &str) -> Result<String, String> {
    std::env::var(name).ok().filter(|v| !v.is_empty()).ok_or(format!("{name} is not set"))
}
fn unix() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
fn key_from_hex(seed: &str) -> Result<SigningKey, String> {
    if seed.len() != 64 {
        return Err("probe seed must be 64 hex characters".into());
    }
    let mut bytes = [0u8; 32];
    for (i, byte) in bytes.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&seed[i * 2..i * 2 + 2], 16).map_err(|_| "invalid probe seed")?;
    }
    Ok(SigningKey::from_bytes(&bytes))
}

struct Config {
    auth: String,
    anon: String,
    email: String,
    password: String,
    ws: String,
}
impl Config {
    fn from_env() -> Result<Self, String> {
        Ok(Self {
            auth: env("MIXEL_PROBE_AUTH")?,
            anon: env("MIXEL_PROBE_ANON")?,
            email: env("MIXEL_PROBE_EMAIL")?,
            password: env("MIXEL_PROBE_PASSWORD")?,
            ws: env("MIXEL_PROBE_WS").unwrap_or_else(|_| "wss://rs.mixel.ch:9444/v1/ws".into()),
        })
    }
    fn ism(&self) -> Result<IsmAuth, String> {
        IsmAuth::new(self.auth.parse().map_err(|_| "invalid MIXEL_PROBE_AUTH")?, self.anon.clone())
            .map_err(|_| "cannot create the ISM client".to_owned())
    }
    async fn sign_in(&self) -> Result<String, String> {
        #[derive(serde::Deserialize)]
        struct Session {
            access_token: String,
        }
        let response = reqwest::Client::new()
            .post(format!("{}/auth/v1/token?grant_type=password", self.auth))
            .header("apikey", &self.anon)
            .timeout(Duration::from_secs(15))
            .json(&serde_json::json!({"email": self.email, "password": self.password}))
            .send()
            .await
            .map_err(|e| format!("sign-in request failed: {e}"))?;
        if !response.status().is_success() {
            return Err(format!("sign-in refused: HTTP {}", response.status()));
        }
        let session: Session = response.json().await.map_err(|_| "sign-in answer invalid")?;
        Ok(session.access_token)
    }
}

struct Peer {
    tx: mpsc::UnboundedSender<ClientMessage>,
    rx: mpsc::UnboundedReceiver<ServerMessage>,
    connection_id: u32,
}

async fn ticket(config: &Config, ism: &IsmAuth, token: &str, device: Uuid, seed: &SigningKey) -> Result<(DeviceTicket, u32), String> {
    let enrolled = ism.authenticate_device(token, device).await.map_err(|_| "probe computer registration not readable")?;
    let mut nonce = [0; 32];
    OsRng.fill_bytes(&mut nonce);
    let challenge = DeviceLoginChallenge {
        nonce,
        device: *device.as_bytes(),
        profile: *enrolled.principal.user.as_bytes(),
        tenant: enrolled.principal.tenant.map(|t| *t.as_bytes()),
        connection_id: enrolled.connection_id,
        generation: enrolled.generation,
    };
    let proof = DelegationProof::new(challenge, unix(), seed).map_err(|e| e.to_string())?;
    let response = reqwest::Client::new()
        .post(format!("{}/v1/device-ticket", config.auth))
        .bearer_auth(token)
        .timeout(Duration::from_secs(20))
        .json(&proof)
        .send()
        .await
        .map_err(|e| format!("device ticket request failed: {e}"))?;
    if !response.status().is_success() {
        return Err(format!("device ticket refused: HTTP {}", response.status()));
    }
    Ok((response.json().await.map_err(|_| "device ticket invalid")?, enrolled.connection_id))
}

async fn connect(config: &Config, ticket: DeviceTicket, connection_id: u32, device: Uuid, seed: SigningKey) -> Result<Peer, String> {
    let (mut socket, _) = tokio::time::timeout(Duration::from_secs(15), tokio_tungstenite::connect_async(config.ws.as_str()))
        .await
        .map_err(|_| "broker connect timeout")?
        .map_err(|e| format!("broker connect failed: {e}"))?;
    let text = |m: &ClientMessage| Message::Text(serde_json::to_string(m).unwrap_or_default().into());
    let token = signaling_server::delegated_auth::encode(&ticket).map_err(|_| "ticket encode")?;
    socket.send(text(&ClientMessage::AuthenticateDevice { token, device })).await.map_err(|e| e.to_string())?;
    loop {
        let next = tokio::time::timeout(Duration::from_secs(15), socket.next()).await.map_err(|_| "broker login timeout")?;
        let Some(Ok(message)) = next else { return Err("broker closed the connection during login".into()) };
        let Message::Text(body) = message else { continue };
        match serde_json::from_str::<ServerMessage>(&body).map_err(|e| e.to_string())? {
            ServerMessage::DeviceChallenge { challenge } => {
                let signature = challenge.sign(&seed).map_err(|e| e.to_string())?;
                socket.send(text(&ClientMessage::ProveDevice { signature })).await.map_err(|e| e.to_string())?;
            }
            ServerMessage::Ready { host, .. } if host == connection_id => break,
            other => return Err(format!("unexpected login answer {other:?}")),
        }
    }
    let (out_tx, mut out_rx) = mpsc::unbounded_channel::<ClientMessage>();
    let (in_tx, in_rx) = mpsc::unbounded_channel::<ServerMessage>();
    tokio::spawn(async move {
        loop {
            tokio::select! {
                outgoing = out_rx.recv() => {
                    let Some(m) = outgoing else { break };
                    if socket.send(text(&m)).await.is_err() { break; }
                }
                incoming = socket.next() => match incoming {
                    Some(Ok(Message::Text(body))) => {
                        if let Ok(m) = serde_json::from_str::<ServerMessage>(&body) { let _ = in_tx.send(m); }
                    }
                    Some(Ok(_)) => {}
                    _ => break,
                },
            }
        }
        let _ = socket.close(None).await;
    });
    Ok(Peer { tx: out_tx, rx: in_rx, connection_id })
}

async fn wait<T>(peer: &mut Peer, within: Duration, what: &str, mut pick: impl FnMut(&ServerMessage) -> Option<T>) -> Result<T, String> {
    let deadline = Instant::now() + within;
    loop {
        let left = deadline.checked_duration_since(Instant::now()).ok_or(format!("timeout waiting for {what}"))?;
        match tokio::time::timeout(left, peer.rx.recv()).await {
            Ok(Some(ServerMessage::Error { code })) => return Err(format!("broker error {code:?} waiting for {what}")),
            Ok(Some(message)) => {
                if let Some(value) = pick(&message) {
                    return Ok(value);
                }
            }
            Ok(None) => return Err(format!("broker connection closed waiting for {what}")),
            Err(_) => return Err(format!("timeout waiting for {what}")),
        }
    }
}

fn relay_servers(lease: &signaling_server::turn::IceLease) -> Vec<NativeIceServer> {
    // Plain UDP relay addresses: the probe proves the relay path, TLS of the relay port is checked separately.
    lease
        .servers
        .iter()
        .filter_map(|server| {
            let urls: Vec<String> = server.urls.iter().filter(|u| u.starts_with("turn:")).cloned().collect();
            (!urls.is_empty()).then(|| NativeIceServer { urls, username: server.username.clone(), credential: server.credential.clone() })
        })
        .collect()
}

async fn channel_open(channel: &Arc<dyn NativeDataChannel>) -> Result<(), String> {
    tokio::time::timeout(Duration::from_secs(15), async {
        loop {
            match channel.poll().await {
                Some(NativeDataChannelEvent::OnOpen) => return Ok(()),
                None | Some(NativeDataChannelEvent::OnClose | NativeDataChannelEvent::OnError) => return Err("relay channel closed before open".to_owned()),
                _ => {}
            }
        }
    })
    .await
    .map_err(|_| "relay channel did not open (TURN/ICE/DTLS)".to_owned())?
}
async fn channel_message(channel: &Arc<dyn NativeDataChannel>) -> Result<Vec<u8>, String> {
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            match channel.poll().await {
                Some(NativeDataChannelEvent::OnMessage(message)) => return Ok(message.data.to_vec()),
                None | Some(NativeDataChannelEvent::OnClose | NativeDataChannelEvent::OnError) => return Err("relay channel closed".to_owned()),
                _ => {}
            }
        }
    })
    .await
    .map_err(|_| "no data through the relay".to_owned())?
}

struct Steps {
    started: Instant,
    done: Vec<serde_json::Value>,
}
impl Steps {
    fn mark(&mut self, name: &str) {
        self.done.push(serde_json::json!({"step": name, "ms": self.started.elapsed().as_millis() as u64}));
    }
}

async fn run(steps: &mut Steps) -> Result<(), String> {
    let config = Config::from_env()?;
    let ism = config.ism()?;
    let a_device: Uuid = env("MIXEL_PROBE_A_DEVICE")?.parse().map_err(|_| "invalid A device")?;
    let b_device: Uuid = env("MIXEL_PROBE_B_DEVICE")?.parse().map_err(|_| "invalid B device")?;
    let a_key = key_from_hex(&env("MIXEL_PROBE_A_SEED")?)?;
    let b_key = key_from_hex(&env("MIXEL_PROBE_B_SEED")?)?;

    let token = config.sign_in().await?;
    steps.mark("sign_in");
    let (a_ticket, a_id) = ticket(&config, &ism, &token, a_device, &a_key).await?;
    let (b_ticket, b_id) = ticket(&config, &ism, &token, b_device, &b_key).await?;
    steps.mark("device_tickets");
    let mut controller = connect(&config, a_ticket, a_id, a_device, a_key).await?;
    let mut host = connect(&config, b_ticket, b_id, b_device, b_key).await?;
    steps.mark("broker_login");

    controller.tx.send(ClientMessage::Request { host: host.connection_id }).map_err(|_| "controller closed")?;
    let session = wait(&mut controller, Duration::from_secs(15), "session request", |m| match m {
        ServerMessage::Requested { session } => Some(*session),
        _ => None,
    })
    .await?;
    wait(&mut host, Duration::from_secs(15), "consent question", |m| matches!(m, ServerMessage::Consent { session: s, .. } if *s == session).then_some(())).await?;
    host.tx.send(ClientMessage::Decide { session, accept: true }).map_err(|_| "host closed")?;
    wait(&mut controller, Duration::from_secs(15), "accept", |m| match m {
        ServerMessage::Accepted { session: s, .. } if *s == session => Some(Ok(())),
        ServerMessage::Closed { session: s, reason } if *s == session => Some(Err(format!("session closed: {reason:?}"))),
        _ => None,
    })
    .await??;
    steps.mark("session_accepted");

    let result = relay(&mut controller, &mut host, session, steps).await;
    let _ = controller.tx.send(ClientMessage::Close { session });
    let closed = wait(&mut controller, Duration::from_secs(10), "close", |m| matches!(m, ServerMessage::Closed { session: s, .. } if *s == session).then_some(())).await;
    result?;
    closed?;
    steps.mark("session_closed");
    Ok(())
}

async fn relay(controller: &mut Peer, host: &mut Peer, session: Uuid, steps: &mut Steps) -> Result<(), String> {
    let lease = |peer: &mut Peer| {
        let _ = peer.tx.send(ClientMessage::Ice { session });
        session
    };
    lease(controller);
    let a_lease = wait(controller, Duration::from_secs(10), "relay credentials", |m| match m {
        ServerMessage::Ice { session: s, lease } if *s == session => Some(lease.clone()),
        _ => None,
    })
    .await?;
    lease(host);
    let b_lease = wait(host, Duration::from_secs(10), "relay credentials", |m| match m {
        ServerMessage::Ice { session: s, lease } if *s == session => Some(lease.clone()),
        _ => None,
    })
    .await?;
    let (a_servers, b_servers) = (relay_servers(&a_lease), relay_servers(&b_lease));
    if a_servers.is_empty() || b_servers.is_empty() {
        return Err("the broker issued no relay address".into());
    }
    steps.mark("relay_credentials");

    let mut a = NativeTransport::relay_only("0.0.0.0:0".into(), a_servers).await.map_err(|e| format!("transport: {e}"))?;
    let mut b = NativeTransport::relay_only("0.0.0.0:0".into(), b_servers).await.map_err(|e| format!("transport: {e}"))?;
    let outcome = async {
        let outbound = a.peer.create_data_channel("mixel-status-probe", None).await.map_err(|e| e.to_string())?;
        let offer = a.local_sdp(a.peer.create_offer(None).await.map_err(|e| e.to_string())?).await.map_err(|e| format!("relay allocation (controller): {e}"))?;
        controller.tx.send(ClientMessage::Signal { session, payload: Signal::Offer(offer.sdp) }).map_err(|_| "controller closed")?;
        let sdp = wait(host, Duration::from_secs(10), "offer through the broker", |m| match m {
            ServerMessage::Signal { session: s, payload: Signal::Offer(sdp) } if *s == session => Some(sdp.clone()),
            _ => None,
        })
        .await?;
        b.peer.set_remote_description(NativeSessionDescription::offer(sdp).map_err(|_| "invalid offer")?).await.map_err(|e| e.to_string())?;
        let answer = b.local_sdp(b.peer.create_answer(None).await.map_err(|e| e.to_string())?).await.map_err(|e| format!("relay allocation (host): {e}"))?;
        host.tx.send(ClientMessage::Signal { session, payload: Signal::Answer(answer.sdp) }).map_err(|_| "host closed")?;
        let sdp = wait(controller, Duration::from_secs(10), "answer through the broker", |m| match m {
            ServerMessage::Signal { session: s, payload: Signal::Answer(sdp) } if *s == session => Some(sdp.clone()),
            _ => None,
        })
        .await?;
        a.peer.set_remote_description(NativeSessionDescription::answer(sdp).map_err(|_| "invalid answer")?).await.map_err(|e| e.to_string())?;
        steps.mark("signaling");
        let inbound = tokio::time::timeout(Duration::from_secs(15), b.incoming_channels.recv())
            .await
            .map_err(|_| "relay channel did not arrive (TURN/ICE/DTLS)")?
            .ok_or("relay channel missing")?;
        let (left, right) = tokio::join!(channel_open(&outbound), channel_open(&inbound));
        left?;
        right?;
        let mut ping = [0u8; 16];
        OsRng.fill_bytes(&mut ping);
        outbound.send(ping.as_slice().into()).await.map_err(|e| e.to_string())?;
        if channel_message(&inbound).await? != ping {
            return Err("relay delivered different data".into());
        }
        inbound.send(b"mixel-status-pong".as_slice().into()).await.map_err(|e| e.to_string())?;
        if channel_message(&outbound).await? != b"mixel-status-pong" {
            return Err("relay delivered a different answer".into());
        }
        steps.mark("relay_data");
        Ok::<(), String>(())
    }
    .await;
    let _ = a.close().await;
    let _ = b.close().await;
    outcome
}

async fn setup() -> Result<(), String> {
    let config = Config::from_env()?;
    let ism = config.ism()?;
    let token = config.sign_in().await?;
    let tenants = ism.saas_my_tenants(&token).await.map_err(|e| format!("tenants: {:?}", e.message))?;
    let tenant = match tenants.iter().find(|t| t.name == TENANT_NAME) {
        Some(t) => t.tenant_id,
        None if tenants.is_empty() => ism.saas_signup(&token, TENANT_NAME, true).await.map_err(|e| format!("signup: {:?}", e.message))?.tenant_id,
        None => return Err("the probe account already belongs to another company".into()),
    };
    println!("# tenant {tenant}");
    for (label, name) in [("A", "Status probe controller"), ("B", "Status probe host")] {
        let mut seed = [0u8; 32];
        OsRng.fill_bytes(&mut seed);
        let key = SigningKey::from_bytes(&seed);
        let added = ism
            .saas_add_this_device(&token, tenant, name, "linux", &key.verifying_key(), None)
            .await
            .map_err(|e| format!("add device: {:?}", e.message))?;
        println!("MIXEL_PROBE_{label}_DEVICE={}", added.device_id);
        println!("MIXEL_PROBE_{label}_SEED={}", hex(&seed));
    }
    Ok(())
}

#[tokio::main]
async fn main() {
    let mode = std::env::args().nth(1).unwrap_or_else(|| "run".into());
    if mode == "setup" {
        if let Err(error) = setup().await {
            eprintln!("setup failed: {error}");
            std::process::exit(1);
        }
        return;
    }
    let mut steps = Steps { started: Instant::now(), done: Vec::new() };
    let result = tokio::time::timeout(Duration::from_secs(100), run(&mut steps)).await.unwrap_or_else(|_| Err("probe took longer than 100 s".into()));
    let ok = result.is_ok();
    println!(
        "{}",
        serde_json::json!({
            "ok": ok,
            "error": result.err(),
            "ms": steps.started.elapsed().as_millis() as u64,
            "steps": steps.done,
            "at": unix(),
        })
    );
    std::process::exit(i32::from(!ok));
}
