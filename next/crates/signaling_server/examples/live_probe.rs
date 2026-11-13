//! LAB: drives real device sessions against the live identity authority and connection broker
//! (no desktop app): device tickets, WebSocket login with the device key, session requests,
//! consent, limits and removals. Used for release checks of the broker and the policy service.
//!   cargo run -p signaling_server --example live_probe -- plan.json
//! The plan holds short-lived test-account bearers; keep it private and delete it afterwards.
use core_protocol::{delegation::{DelegationProof, DeviceTicket}, device_login::DeviceLoginChallenge};
use ed25519_dalek::SigningKey;
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use signaling_server::protocol::{ClientMessage, ErrorCode, ServerMessage};
use std::{collections::HashMap, time::{Duration, Instant, SystemTime, UNIX_EPOCH}};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;
use uuid::Uuid;

#[derive(Deserialize)]
struct Plan {
    identity: String,
    anon_key: String,
    signaling: String,
    peers: HashMap<String, PeerSpec>,
    steps: Vec<Step>,
}
#[derive(Deserialize, Clone)]
struct PeerSpec {
    token: Option<String>,
    seed: String,
    device: Uuid,
    connection_id: u32,
    generation: u64,
    profile: Uuid,
    tenant: Option<Uuid>,
}
#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
enum Step {
    Connect { peer: String },
    SupportHost { peer: String },
    Request { from: String, to: String, expect: String, name: Option<String>, unattended: Option<bool> },
    Close { peer: String, session: String },
    WaitClosed { peer: String, session: String, within: u64 },
    Rpc { token: String, function: String, body: serde_json::Value, label: String },
    Sleep { seconds: u64 },
    CheckPairing { peer: String },
    Presence { peer: String, hosts: Vec<u32>, label: String },
}

struct Peer {
    tx: mpsc::UnboundedSender<ClientMessage>,
    rx: mpsc::UnboundedReceiver<ServerMessage>,
}

fn unix() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()
}

fn key(seed: &str) -> SigningKey {
    let bytes: Vec<u8> = (0..32).map(|i| u8::from_str_radix(&seed[i * 2..i * 2 + 2], 16).unwrap()).collect();
    SigningKey::from_bytes(&bytes.try_into().unwrap())
}

async fn ticket(plan: &Plan, spec: &PeerSpec) -> Result<DeviceTicket, String> {
    let mut nonce = [0; 32];
    rand_core::RngCore::fill_bytes(&mut rand_core::OsRng, &mut nonce);
    let challenge = DeviceLoginChallenge {
        nonce,
        device: *spec.device.as_bytes(),
        profile: *spec.profile.as_bytes(),
        tenant: spec.tenant.map(|t| *t.as_bytes()),
        connection_id: spec.connection_id,
        generation: spec.generation,
    };
    let signing = key(&spec.seed);
    let http = reqwest::Client::new();
    let response = match &spec.token {
        Some(token) => {
            let proof = DelegationProof::new(challenge, unix(), &signing).map_err(|e| e.to_string())?;
            http.post(format!("{}/v1/device-ticket", plan.identity)).bearer_auth(token).json(&proof).send().await
        }
        None => {
            let proof = DelegationProof::new_unattended_host(challenge, unix(), &signing).map_err(|e| e.to_string())?;
            http.post(format!("{}/v1/startup-host-ticket", plan.identity))
                .json(&serde_json::json!({"proof": proof, "public_key": signing.verifying_key().to_bytes()}))
                .send()
                .await
        }
    }
    .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(format!("ticket refused: HTTP {}", response.status()));
    }
    response.json().await.map_err(|e| e.to_string())
}

async fn connect(plan: &Plan, spec: &PeerSpec) -> Result<Peer, String> {
    let ticket = ticket(plan, spec).await?;
    let (mut socket, _) = tokio_tungstenite::connect_async(plan.signaling.as_str()).await.map_err(|e| e.to_string())?;
    let send = |m: &ClientMessage| Message::Text(serde_json::to_string(m).unwrap().into());
    socket
        .send(send(&ClientMessage::AuthenticateDevice {
            token: signaling_server::delegated_auth::encode(&ticket).map_err(|_| "encode")?,
            device: spec.device,
        }))
        .await
        .map_err(|e| e.to_string())?;
    let mut ready = false;
    while let Some(Ok(message)) = tokio::time::timeout(Duration::from_secs(20), socket.next()).await.map_err(|_| "login timeout")? {
        let Message::Text(text) = message else { continue };
        match serde_json::from_str::<ServerMessage>(&text).map_err(|e| e.to_string())? {
            ServerMessage::DeviceChallenge { challenge } => {
                let signature = challenge.sign(&key(&spec.seed)).map_err(|e| e.to_string())?;
                socket.send(send(&ClientMessage::ProveDevice { signature })).await.map_err(|e| e.to_string())?;
            }
            ServerMessage::Ready { host, .. } => {
                if host != spec.connection_id {
                    return Err(format!("ready with another ID {host}"));
                }
                ready = true;
                break;
            }
            other => return Err(format!("unexpected {other:?}")),
        }
    }
    if !ready {
        return Err("connection closed during login (broker refused the device)".into());
    }
    let (out_tx, mut out_rx) = mpsc::unbounded_channel::<ClientMessage>();
    let (in_tx, in_rx) = mpsc::unbounded_channel::<ServerMessage>();
    tokio::spawn(async move {
        let mut keepalive = tokio::time::interval(Duration::from_secs(20));
        loop {
            tokio::select! {
                outgoing = out_rx.recv() => {
                    let Some(m) = outgoing else { break };
                    if socket.send(send(&m)).await.is_err() { break; }
                }
                _ = keepalive.tick() => {
                    if socket.send(Message::Ping(Vec::new().into())).await.is_err() { break; }
                }
                incoming = socket.next() => {
                    match incoming {
                        Some(Ok(Message::Text(text))) => {
                            if let Ok(m) = serde_json::from_str::<ServerMessage>(&text) { let _ = in_tx.send(m); }
                        }
                        Some(Ok(_)) => {}
                        _ => break,
                    }
                }
            }
        }
    });
    Ok(Peer { tx: out_tx, rx: in_rx })
}

/// Next message for which `pick` returns Some, within `within`.
async fn wait<T>(peer: &mut Peer, within: Duration, mut pick: impl FnMut(&ServerMessage) -> Option<T>) -> Option<T> {
    let deadline = Instant::now() + within;
    loop {
        let left = deadline.checked_duration_since(Instant::now())?;
        match tokio::time::timeout(left, peer.rx.recv()).await {
            Ok(Some(message)) => {
                if let Some(value) = pick(&message) {
                    return Some(value);
                }
            }
            _ => return None,
        }
    }
}

#[tokio::main]
async fn main() {
    let path = std::env::args().nth(1).expect("plan.json");
    let plan: Plan = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    let mut peers: HashMap<String, Peer> = HashMap::new();
    let mut sessions: HashMap<String, Uuid> = HashMap::new();
    let mut failures = 0;
    for step in &plan.steps {
        let outcome: Result<String, String> = async {
            match step {
                Step::Connect { peer } => {
                    let spec = &plan.peers[peer];
                    let connected = connect(&plan, spec).await?;
                    peers.insert(peer.clone(), connected);
                    Ok(format!("{peer} online as {}", spec.connection_id))
                }
                Step::SupportHost { peer } => {
                    let p = peers.get_mut(peer).ok_or("not connected")?;
                    p.tx.send(ClientMessage::ConfigureSupportHost { enabled: true }).map_err(|_| "closed")?;
                    wait(p, Duration::from_secs(5), |m| matches!(m, ServerMessage::SupportHostStatus { enabled: true }).then_some(()))
                        .await
                        .ok_or("support host not confirmed")?;
                    Ok(format!("{peer} accepts attended requests"))
                }
                Step::Request { from, to, expect, name, unattended } => {
                    let host_id = plan.peers[to].connection_id;
                    let unattended = unattended.unwrap_or(false);
                    let controller = peers.get_mut(from).ok_or("controller not connected")?;
                    controller
                        .tx
                        .send(if unattended { ClientMessage::RequestUnattended { host: host_id } } else { ClientMessage::Request { host: host_id } })
                        .map_err(|_| "closed")?;
                    let first = wait(controller, Duration::from_secs(10), |m| match m {
                        ServerMessage::Requested { session } => Some(Ok(*session)),
                        ServerMessage::Error { code } => Some(Err(*code)),
                        _ => None,
                    })
                    .await
                    .ok_or("no answer to the request")?;
                    let session = match first {
                        Err(code) => {
                            let got = match code { ErrorCode::Busy => "busy", ErrorCode::Unavailable => "unavailable", ErrorCode::Forbidden => "forbidden", ErrorCode::Invalid => "invalid" };
                            return if got == expect { Ok(format!("{from} -> {to}: refused ({got}) as expected")) } else { Err(format!("{from} -> {to}: {got}, expected {expect}")) };
                        }
                        Ok(session) => session,
                    };
                    if !unattended {
                        let host = peers.get_mut(to).ok_or("host not connected")?;
                        wait(host, Duration::from_secs(10), |m| matches!(m, ServerMessage::Consent { session: s, .. } if *s == session).then_some(()))
                            .await
                            .ok_or("host got no consent question")?;
                        host.tx.send(ClientMessage::Decide { session, accept: true }).map_err(|_| "closed")?;
                    }
                    let controller = peers.get_mut(from).ok_or("controller gone")?;
                    let result = wait(controller, Duration::from_secs(15), |m| match m {
                        ServerMessage::Accepted { session: s, .. } if *s == session => Some("accepted"),
                        ServerMessage::Closed { session: s, .. } if *s == session => Some("closed"),
                        _ => None,
                    })
                    .await
                    .unwrap_or("timeout");
                    if let Some(name) = name { sessions.insert(name.clone(), session); }
                    if result == expect { Ok(format!("{from} -> {to}: {result}")) } else { Err(format!("{from} -> {to}: {result}, expected {expect}")) }
                }
                Step::Close { peer, session } => {
                    let id = sessions[session];
                    let p = peers.get_mut(peer).ok_or("not connected")?;
                    p.tx.send(ClientMessage::Close { session: id }).map_err(|_| "closed")?;
                    wait(p, Duration::from_secs(5), |m| matches!(m, ServerMessage::Closed { session: s, .. } if *s == id).then_some(()))
                        .await
                        .ok_or("close not confirmed")?;
                    Ok(format!("{session} closed"))
                }
                Step::WaitClosed { peer, session, within } => {
                    let id = sessions[session];
                    let started = Instant::now();
                    let p = peers.get_mut(peer).ok_or("not connected")?;
                    wait(p, Duration::from_secs(*within), |m| matches!(m, ServerMessage::Closed { session: s, .. } if *s == id).then_some(()))
                        .await
                        .ok_or(format!("{session} still open after {within} s"))?;
                    Ok(format!("{session} ended by the broker after {:.1} s", started.elapsed().as_secs_f32()))
                }
                Step::Rpc { token, function, body, label } => {
                    let response = reqwest::Client::new()
                        .post(format!("{}/rest/v1/rpc/{function}", plan.identity))
                        .header("apikey", &plan.anon_key)
                        .bearer_auth(token)
                        .json(body)
                        .send()
                        .await
                        .map_err(|e| e.to_string())?;
                    let status = response.status();
                    let text = response.text().await.unwrap_or_default();
                    if status.is_success() { Ok(format!("{label}: {}", &text[..text.len().min(80)])) } else { Err(format!("{label}: HTTP {status} {}", &text[..text.len().min(200)])) }
                }
                Step::CheckPairing { peer } => {
                    let p = peers.get_mut(peer).ok_or("not connected")?;
                    p.tx.send(ClientMessage::CheckPairing).map_err(|_| "closed")?;
                    let (pending, failed) = wait(p, Duration::from_secs(10), |m| match m {
                        ServerMessage::PairingStatus { pending, failed, .. } => Some((pending.is_some(), *failed)),
                        _ => None,
                    })
                    .await
                    .ok_or("no pairing status")?;
                    if failed { Err(format!("{peer}: pairing check failed")) } else { Ok(format!("{peer}: pairing check ok (pending: {pending})")) }
                }
                Step::Presence { peer, hosts, label } => {
                    let p = peers.get_mut(peer).ok_or("not connected")?;
                    p.tx.send(ClientMessage::Presence { hosts: hosts.clone() }).map_err(|_| "closed")?;
                    let online = wait(p, Duration::from_secs(10), |m| match m {
                        ServerMessage::Presence { online } => Some(online.clone()),
                        _ => None,
                    })
                    .await
                    .ok_or("no presence answer")?;
                    Ok(format!("{label}: {} of {} online {online:?}", online.len(), hosts.len()))
                }
                Step::Sleep { seconds } => {
                    tokio::time::sleep(Duration::from_secs(*seconds)).await;
                    Ok(format!("waited {seconds} s"))
                }
            }
        }
        .await;
        match outcome {
            Ok(text) => println!("PASS: {text}"),
            Err(text) => {
                failures += 1;
                println!("FAIL: {text}");
            }
        }
    }
    println!("{} steps, {failures} failed", plan.steps.len());
    std::process::exit(i32::from(failures > 0));
}
