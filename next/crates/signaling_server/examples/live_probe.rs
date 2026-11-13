//! LAB: drives real device sessions against the live identity authority and connection broker
//! (no desktop app): device tickets, WebSocket login with the device key, session requests,
//! consent, limits and removals, second technicians (invite, join, removal by the host) and
//! switching sides. Used for release checks of the broker and the policy service.
//!   cargo run -p signaling_server --example live_probe -- plan.json
//! The plan holds short-lived test-account bearers; keep it private and delete it afterwards.
use core_protocol::{delegation::{DelegationProof, DeviceTicket}, device_login::DeviceLoginChallenge};
use ed25519_dalek::SigningKey;
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use signaling_server::protocol::{ClientMessage, CloseReason, ErrorCode, FEATURE_JOIN, FEATURE_SWITCH, ServerMessage, Signal};
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
    /// `features`: announce `join` and `switch` after login, like the current apps.
    Connect { peer: String, features: Option<bool> },
    SupportHost { peer: String },
    /// `run`: also exchange offer and answer, so the session is running (needed to invite or switch).
    Request { from: String, to: String, expect: String, name: Option<String>, unattended: Option<bool>, run: Option<bool> },
    Close { peer: String, session: String },
    WaitClosed { peer: String, session: String, within: u64, reason: Option<String> },
    /// The technician of running session `session` invites a colleague by account (`user`) or by
    /// the Remote ID of the peer `device`; `to` must receive it (or `expect` names the refusal).
    Invite { session: String, user: Option<Uuid>, device: Option<String>, to: Option<String>, control: bool, expect: Option<String>, name: String },
    /// `peer` joins with invitation `invitation`; the host answers (`accept`) and the join runs.
    JoinSession { peer: String, invitation: String, accept: bool, expect: String, name: String },
    /// `by` (either side of running session `session`) switches sides; `expect`: "switched" or a refusal.
    /// The new session (sides swapped, accepted by the former technician) is stored as `name`.
    SwitchSides { by: String, session: String, expect: String, name: Option<String> },
    Rpc { token: String, function: String, body: serde_json::Value, label: String },
    Sleep { seconds: u64 },
    CheckPairing { peer: String },
    Presence { peer: String, hosts: Vec<u32>, label: String },
}

struct Peer {
    tx: mpsc::UnboundedSender<ClientMessage>,
    rx: mpsc::UnboundedReceiver<ServerMessage>,
}

fn code_name(code: ErrorCode) -> &'static str {
    match code {
        ErrorCode::Busy => "busy",
        ErrorCode::Unavailable => "unavailable",
        ErrorCode::Forbidden => "forbidden",
        ErrorCode::Invalid => "invalid",
    }
}

/// Host accepts `session`, both see `accepted`, then offer and answer run it (phase connected).
async fn run_session(controller: &mut Peer, host: &mut Peer, session: Uuid) -> Result<(), String> {
    host.tx.send(ClientMessage::Decide { session, accept: true }).map_err(|_| "host closed")?;
    for (side, peer) in [("controller", &mut *controller), ("host", &mut *host)] {
        wait(peer, Duration::from_secs(15), |m| match m {
            ServerMessage::Accepted { session: s, .. } if *s == session => Some(Ok(())),
            ServerMessage::Closed { session: s, reason } if *s == session => Some(Err(format!("{side}: closed ({reason:?})"))),
            _ => None,
        })
        .await
        .ok_or(format!("{side}: not accepted"))??;
    }
    controller.tx.send(ClientMessage::Signal { session, payload: Signal::Offer("v=0 lab probe".into()) }).map_err(|_| "controller closed")?;
    wait(host, Duration::from_secs(10), |m| matches!(m, ServerMessage::Signal { session: s, payload: Signal::Offer(_) } if *s == session).then_some(()))
        .await
        .ok_or("host got no offer")?;
    host.tx.send(ClientMessage::Signal { session, payload: Signal::Answer("v=0 lab probe".into()) }).map_err(|_| "host closed")?;
    wait(controller, Duration::from_secs(10), |m| matches!(m, ServerMessage::Signal { session: s, payload: Signal::Answer(_) } if *s == session).then_some(()))
        .await
        .ok_or("controller got no answer")?;
    Ok(())
}

/// Two distinct peers of the map at once.
fn pair<'a>(peers: &'a mut HashMap<String, Peer>, a: &str, b: &str) -> Result<(&'a mut Peer, &'a mut Peer), String> {
    if a == b {
        return Err(format!("{a} on both sides"));
    }
    let [first, second] = peers.get_disjoint_mut([a, b]);
    Ok((first.ok_or(format!("{a} not connected"))?, second.ok_or(format!("{b} not connected"))?))
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
    // Session name -> (controller peer, host peer).
    let mut sides: HashMap<String, (String, String)> = HashMap::new();
    let mut invitations: HashMap<String, (Uuid, String)> = HashMap::new();
    let mut failures = 0;
    for step in &plan.steps {
        let outcome: Result<String, String> = async {
            match step {
                Step::Connect { peer, features } => {
                    let spec = &plan.peers[peer];
                    let connected = connect(&plan, spec).await?;
                    if features.unwrap_or(false) {
                        connected.tx.send(ClientMessage::Capabilities { features: vec![FEATURE_JOIN.into(), FEATURE_SWITCH.into()] }).map_err(|_| "closed")?;
                    }
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
                Step::Request { from, to, expect, name, unattended, run } => {
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
                            let got = code_name(code);
                            return if got == expect { Ok(format!("{from} -> {to}: refused ({got}) as expected")) } else { Err(format!("{from} -> {to}: {got}, expected {expect}")) };
                        }
                        Ok(session) => session,
                    };
                    if !unattended {
                        let host = peers.get_mut(to).ok_or("host not connected")?;
                        wait(host, Duration::from_secs(10), |m| matches!(m, ServerMessage::Consent { session: s, .. } if *s == session).then_some(()))
                            .await
                            .ok_or("host got no consent question")?;
                        if run.unwrap_or(false) {
                            let (controller, host) = pair(&mut peers, from, to)?;
                            run_session(controller, host, session).await?;
                            if let Some(name) = name {
                                sessions.insert(name.clone(), session);
                                sides.insert(name.clone(), (from.clone(), to.clone()));
                            }
                            return if expect == "accepted" { Ok(format!("{from} -> {to}: accepted and running")) } else { Err(format!("{from} -> {to}: running, expected {expect}")) };
                        }
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
                    if let Some(name) = name {
                        sessions.insert(name.clone(), session);
                        sides.insert(name.clone(), (from.clone(), to.clone()));
                    }
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
                Step::WaitClosed { peer, session, within, reason } => {
                    let id = sessions[session];
                    let started = Instant::now();
                    let p = peers.get_mut(peer).ok_or("not connected")?;
                    let got = wait(p, Duration::from_secs(*within), |m| match m {
                        ServerMessage::Closed { session: s, reason } if *s == id => Some(*reason),
                        _ => None,
                    })
                    .await
                    .ok_or(format!("{session} still open after {within} s"))?;
                    let got = serde_json::to_value(got).map_err(|e| e.to_string())?.as_str().unwrap_or_default().to_owned();
                    if reason.as_ref().is_some_and(|wanted| *wanted != got) {
                        return Err(format!("{session} closed for {peer} ({got}), expected {}", reason.as_deref().unwrap_or_default()));
                    }
                    Ok(format!("{session} ended for {peer} ({got}) after {:.1} s", started.elapsed().as_secs_f32()))
                }
                Step::Invite { session, user, device, to, control, expect, name } => {
                    let id = *sessions.get(session).ok_or("unknown session")?;
                    let (technician, host) = sides.get(session).cloned().ok_or("unknown session")?;
                    let device_id = device.as_ref().map(|peer| plan.peers[peer].connection_id);
                    let p = peers.get_mut(&technician).ok_or("technician not connected")?;
                    p.tx.send(ClientMessage::Invite { session: id, user: *user, device: device_id, control: *control }).map_err(|_| "closed")?;
                    let status = wait(p, Duration::from_secs(10), |m| match m {
                        ServerMessage::InviteStatus { invitation, delivered } => Some(Ok((*invitation, *delivered))),
                        ServerMessage::Error { code } => Some(Err(code_name(*code))),
                        _ => None,
                    })
                    .await
                    .ok_or("no answer to the invitation")?;
                    let (invitation, delivered) = match (status, expect) {
                        (Err(got), Some(wanted)) if got == wanted => return Ok(format!("{technician} invite refused ({got}) as expected")),
                        (Err(got), _) => return Err(format!("{technician} invite refused ({got})")),
                        (Ok((_, delivered)), Some(wanted)) if wanted == "undelivered" && delivered == 0 => {
                            return Ok(format!("{technician} invite reached nobody, as expected"));
                        }
                        (Ok((_, delivered)), Some(wanted)) => return Err(format!("{technician} invite delivered to {delivered}, expected {wanted}")),
                        (Ok(value), None) => value,
                    };
                    let recipient = to.as_ref().ok_or("`to` missing")?;
                    let host_id = plan.peers[&host].connection_id;
                    let technician_id = plan.peers[&technician].connection_id;
                    let r = peers.get_mut(recipient).ok_or("colleague not connected")?;
                    let offer = wait(r, Duration::from_secs(10), |m| match m {
                        ServerMessage::Invited { invitation: i, host, inviter_connection, control: c, expires_in, .. } if *i == invitation => {
                            Some((*host, *inviter_connection, *c, *expires_in))
                        }
                        _ => None,
                    })
                    .await
                    .ok_or(format!("{recipient} got no invitation"))?;
                    if (offer.0, offer.1, offer.2) != (host_id, technician_id, *control) {
                        return Err(format!("{recipient}: invitation for host {} from {} control {}, expected {host_id} from {technician_id} control {control}", offer.0, offer.1, offer.2));
                    }
                    invitations.insert(name.clone(), (invitation, session.clone()));
                    Ok(format!("{technician} invited {recipient} ({}): delivered to {delivered} app(s), valid {} s", if *control { "view and control" } else { "view" }, offer.3))
                }
                Step::JoinSession { peer, invitation, accept, expect, name } => {
                    let (invitation, primary_name) = invitations.get(invitation).cloned().ok_or("unknown invitation")?;
                    let primary = sessions[&primary_name];
                    let (technician, host) = sides[&primary_name].clone();
                    let technician_id = plan.peers[&technician].connection_id;
                    let p = peers.get_mut(peer).ok_or("colleague not connected")?;
                    p.tx.send(ClientMessage::JoinSession { invitation }).map_err(|_| "closed")?;
                    let first = wait(p, Duration::from_secs(10), |m| match m {
                        ServerMessage::Requested { session } => Some(Ok(*session)),
                        ServerMessage::Error { code } => Some(Err(code_name(*code))),
                        _ => None,
                    })
                    .await
                    .ok_or("no answer to the join")?;
                    let joined = match first {
                        Err(got) => return if got == expect { Ok(format!("{peer} join refused ({got}) as expected")) } else { Err(format!("{peer} join refused ({got}), expected {expect}")) },
                        Ok(session) => session,
                    };
                    let colleague_id = plan.peers[peer].connection_id;
                    let h = peers.get_mut(&host).ok_or("host not connected")?;
                    let asked = wait(h, Duration::from_secs(10), |m| match m {
                        ServerMessage::Consent { session, requester_connection, joining: Some(joining), requester_identity, .. } if *session == joined => {
                            Some((*requester_connection, joining.session, joining.inviter_connection, requester_identity.is_some()))
                        }
                        _ => None,
                    })
                    .await
                    .ok_or("host got no join question")?;
                    if asked != (colleague_id, primary, technician_id, true) {
                        return Err(format!("join question {asked:?}, expected colleague {colleague_id} in {primary} invited by {technician_id} with identity"));
                    }
                    if !*accept {
                        h.tx.send(ClientMessage::Decide { session: joined, accept: false }).map_err(|_| "closed")?;
                        let p = peers.get_mut(peer).ok_or("colleague gone")?;
                        let reason = wait(p, Duration::from_secs(10), |m| match m {
                            ServerMessage::Closed { session, reason } if *session == joined => Some(*reason),
                            _ => None,
                        })
                        .await
                        .ok_or("colleague not told")?;
                        return if expect == "rejected" && reason == CloseReason::Rejected { Ok(format!("host declined {peer}")) } else { Err(format!("declined join ended {reason:?}, expected {expect}")) };
                    }
                    let (colleague, host_peer) = pair(&mut peers, peer, &host)?;
                    run_session(colleague, host_peer, joined).await?;
                    sessions.insert(name.clone(), joined);
                    sides.insert(name.clone(), (peer.clone(), host.clone()));
                    if expect == "accepted" { Ok(format!("{peer} joined {primary_name} next to {technician}, host allowed, running")) } else { Err(format!("{peer} joined, expected {expect}")) }
                }
                Step::SwitchSides { by, session, expect, name } => {
                    let id = *sessions.get(session).ok_or("unknown session")?;
                    let (controller, host) = sides.get(session).cloned().ok_or("unknown session")?;
                    let p = peers.get_mut(by).ok_or("not connected")?;
                    p.tx.send(ClientMessage::SwitchSides { session: id }).map_err(|_| "closed")?;
                    let first = wait(p, Duration::from_secs(10), |m| match m {
                        ServerMessage::Closed { session: s, reason } if *s == id => Some(Ok(*reason)),
                        ServerMessage::Error { code } => Some(Err(code_name(*code))),
                        _ => None,
                    })
                    .await
                    .ok_or("no answer to the switch")?;
                    match first {
                        Err(got) => return if got == expect { Ok(format!("{by}: switch refused ({got}) as expected")) } else { Err(format!("{by}: switch refused ({got}), expected {expect}")) },
                        Ok(CloseReason::Switched) => {}
                        Ok(other) => return Err(format!("{session} closed ({other:?}) instead of switching")),
                    }
                    if expect != "switched" {
                        return Err(format!("{by}: switched, expected {expect}"));
                    }
                    // The former technician's app is asked (it is now shown); the former host views.
                    let by_id = plan.peers[by].connection_id;
                    let old_host_id = plan.peers[&host].connection_id;
                    let old_controller_id = plan.peers[&controller].connection_id;
                    let c = peers.get_mut(&controller).ok_or("technician gone")?;
                    if controller != *by {
                        wait(c, Duration::from_secs(10), |m| matches!(m, ServerMessage::Closed { session: s, reason: CloseReason::Switched } if *s == id).then_some(()))
                            .await
                            .ok_or("technician not told about the switch")?;
                    }
                    let (reversed, switched_by) = wait(c, Duration::from_secs(10), |m| match m {
                        ServerMessage::Consent { session, requester_connection, switched: Some(from), .. } if from.previous == id && *requester_connection == old_host_id => {
                            Some((*session, from.by))
                        }
                        _ => None,
                    })
                    .await
                    .ok_or("former technician got no switched question")?;
                    if switched_by != by_id {
                        return Err(format!("switch attributed to {switched_by}, expected {by_id}"));
                    }
                    let h = peers.get_mut(&host).ok_or("host gone")?;
                    if host != *by {
                        wait(h, Duration::from_secs(10), |m| matches!(m, ServerMessage::Closed { session: s, reason: CloseReason::Switched } if *s == id).then_some(()))
                            .await
                            .ok_or("host not told about the switch")?;
                    }
                    wait(h, Duration::from_secs(10), |m| {
                        matches!(m, ServerMessage::Switched { previous, session, host } if *previous == id && *session == reversed && *host == old_controller_id).then_some(())
                    })
                    .await
                    .ok_or("former host got no switched session")?;
                    let (viewer, shown) = pair(&mut peers, &host, &controller)?;
                    run_session(viewer, shown, reversed).await?;
                    if let Some(name) = name {
                        sessions.insert(name.clone(), reversed);
                        sides.insert(name.clone(), (host.clone(), controller.clone()));
                    }
                    Ok(format!("{by} switched sides: {host} now views {controller} ({controller} accepted), running"))
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
