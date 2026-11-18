//! Real TCP/WebSocket transport with a deterministic identity fixture. This does
//! not claim a live ISM login or a media/desktop session.
use futures_util::{SinkExt, StreamExt};
use signaling_server::{
    auth::{AuthError, EnrolledPrincipal, Principal},
    protocol::*,
    server::{AppState, Authenticator, router},
};
use std::{
    collections::HashSet,
    future::Future,
    pin::Pin,
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::{net::TcpStream, time::timeout};
use tokio_tungstenite::{
    MaybeTlsStream, WebSocketStream,
    tungstenite::{Message, client::IntoClientRequest, http::HeaderValue},
};
use uuid::Uuid;
type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;
struct Fixture;
impl Authenticator for Fixture {
    fn authenticate_device<'a>(
        &'a self,
        token: &'a str,
        device: Uuid,
    ) -> Pin<Box<dyn Future<Output = Result<EnrolledPrincipal, AuthError>> + Send + 'a>> {
        Box::pin(async move {
            if device != Uuid::from_u128(21) || !token.starts_with("host") {
                return Err(AuthError);
            }
            let principal = self.authenticate(token).await?;
            Ok(EnrolledPrincipal {
                principal,
                device,
                connection_id: 123456789,
                generation: 1,
                public_key: ed25519_dalek::SigningKey::from_bytes(&[7; 32]).verifying_key(),
                identity_ticket: None,
            })
        })
    }
    fn authenticate<'a>(
        &'a self,
        token: &'a str,
    ) -> Pin<Box<dyn Future<Output = Result<Principal, AuthError>> + Send + 'a>> {
        Box::pin(async move {
            let tenant = match token {
                "host" | "host-short" | "host-renew" | "host-expired" | "host-slow" | "viewer" => 1,
                "outsider" => 2,
                _ => return Err(AuthError),
            };
            if token == "host-slow" {
                tokio::time::sleep(Duration::from_secs(10)).await;
            }
            Ok(Principal {
                user: Uuid::from_u128(if token.starts_with("host") {
                    10
                } else if token == "viewer" {
                    11
                } else {
                    12
                }),
                tenant: Some(Uuid::from_u128(tenant)),
                staff: false,
                expires_at: SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs()
                    + if token == "host-short" {
                        2
                    } else if token == "host-expired" {
                        0
                    } else {
                        600
                    },
            })
        })
    }
}
async fn event(socket: &mut Socket) -> ServerMessage {
    loop {
        let msg = timeout(Duration::from_secs(3), socket.next())
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        if let Message::Text(text) = msg {
            return serde_json::from_str(&text).unwrap();
        }
    }
}
async fn send(socket: &mut Socket, msg: ClientMessage) {
    socket
        .send(Message::Text(serde_json::to_string(&msg).unwrap().into()))
        .await
        .unwrap();
}
async fn peer(url: &str, token: &str) -> (Socket, u32) {
    let (mut socket, _) = tokio_tungstenite::connect_async(url).await.unwrap();
    send(
        &mut socket,
        ClientMessage::Authenticate {
            token: token.into(),
        },
    )
    .await;
    let ServerMessage::Ready {
        host,
        protocol: core_protocol::PROTOCOL_VERSION,
    } = event(&mut socket).await
    else {
        panic!("ready expected")
    };
    assert!((100_000_000..=999_999_999).contains(&host));
    (socket, host)
}
async fn request(viewer: &mut Socket, host: &mut Socket, host_id: u32) -> Uuid {
    send(viewer, ClientMessage::Request { host: host_id }).await;
    let ServerMessage::Requested { session } = event(viewer).await else {
        panic!("request expected")
    };
    assert!(
        matches!(event(host).await, ServerMessage::Consent { session: id, .. } if id == session)
    );
    session
}
#[tokio::test]
async fn actual_websocket_accept_reject_reconnect_isolation_and_cleanup() {
    let state = AppState::new(
        Arc::new(Fixture),
        HashSet::from(["https://staging.example".into()]),
    );
    let registry = state.registry.clone();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", listener.local_addr().unwrap());
    let task = tokio::spawn(async move {
        axum::serve(listener, router(state)).await.unwrap();
    });
    let (mut host, host_id) = peer(&url, "host").await;
    let (mut viewer, viewer_id) = peer(&url, "viewer").await;
    let (mut outsider, _) = peer(&url, "outsider").await;
    assert_ne!(host_id, viewer_id);
    send(&mut outsider, ClientMessage::Request { host: host_id }).await;
    assert!(matches!(
        event(&mut outsider).await,
        ServerMessage::Error {
            code: ErrorCode::Unavailable
        }
    ));
    let session = request(&mut viewer, &mut host, host_id).await;
    // No SDP before an ordinary host Accept, and viewer cannot accept itself.
    send(
        &mut viewer,
        ClientMessage::Signal {
            session,
            payload: Signal::Offer("offer".into()),
        },
    )
    .await;
    assert!(matches!(
        event(&mut viewer).await,
        ServerMessage::Error {
            code: ErrorCode::Forbidden
        }
    ));
    send(
        &mut viewer,
        ClientMessage::Decide {
            session,
            accept: true,
        },
    )
    .await;
    assert!(matches!(
        event(&mut viewer).await,
        ServerMessage::Error {
            code: ErrorCode::Forbidden
        }
    ));
    send(
        &mut host,
        ClientMessage::Decide {
            session,
            accept: true,
        },
    )
    .await;
    let ServerMessage::Accepted { nonce, .. } = event(&mut viewer).await else {
        panic!("accept expected")
    };
    assert_ne!(nonce, [0; 32]);
    assert!(
        matches!(event(&mut host).await, ServerMessage::Accepted { nonce: n, .. } if n == nonce)
    );
    send(
        &mut outsider,
        ClientMessage::Signal {
            session,
            payload: Signal::Ice("steal".into()),
        },
    )
    .await;
    assert!(matches!(
        event(&mut outsider).await,
        ServerMessage::Error {
            code: ErrorCode::Forbidden
        }
    ));
    send(
        &mut viewer,
        ClientMessage::Signal {
            session,
            payload: Signal::Offer("offer".into()),
        },
    )
    .await;
    assert!(
        matches!(event(&mut host).await, ServerMessage::Signal { payload: Signal::Offer(s), .. } if s == "offer")
    );
    send(
        &mut host,
        ClientMessage::Signal {
            session,
            payload: Signal::Answer("answer".into()),
        },
    )
    .await;
    assert!(
        matches!(event(&mut viewer).await, ServerMessage::Signal { payload: Signal::Answer(s), .. } if s == "answer")
    );
    send(
        &mut viewer,
        ClientMessage::Signal {
            session,
            payload: Signal::Ice("candidate".into()),
        },
    )
    .await;
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::Signal {
            payload: Signal::Ice(_),
            ..
        }
    ));
    send(&mut viewer, ClientMessage::Close { session }).await;
    send(&mut viewer, ClientMessage::Close { session }).await;
    send(&mut host, ClientMessage::Close { session }).await;
    for peer in [&mut viewer, &mut host] {
        assert!(matches!(
            event(peer).await,
            ServerMessage::Closed {
                reason: CloseReason::Requested,
                ..
            }
        ));
    }
    send(&mut outsider, ClientMessage::Close { session }).await;
    assert!(matches!(
        event(&mut outsider).await,
        ServerMessage::Error {
            code: ErrorCode::Forbidden
        }
    ));
    let fresh = request(&mut viewer, &mut host, host_id).await;
    assert_ne!(fresh, session);
    send(
        &mut host,
        ClientMessage::Decide {
            session: fresh,
            accept: false,
        },
    )
    .await;
    for peer in [&mut viewer, &mut host] {
        assert!(matches!(
            event(peer).await,
            ServerMessage::Closed {
                reason: CloseReason::Rejected,
                ..
            }
        ));
    }
    let _ = request(&mut viewer, &mut host, host_id).await;
    host.close(None).await.unwrap();
    assert!(matches!(
        event(&mut viewer).await,
        ServerMessage::Closed {
            reason: CloseReason::Disconnected,
            ..
        }
    ));
    let mut bad_origin = url.clone().into_client_request().unwrap();
    bad_origin
        .headers_mut()
        .insert("origin", HeaderValue::from_static("https://evil.example"));
    assert!(tokio_tungstenite::connect_async(bad_origin).await.is_err());
    let (mut invalid, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
    send(
        &mut invalid,
        ClientMessage::Authenticate {
            token: "invalid".into(),
        },
    )
    .await;
    assert!(!matches!(
        timeout(Duration::from_secs(2), invalid.next()).await,
        Ok(Some(Ok(Message::Text(_))))
    ));
    viewer.close(None).await.unwrap();
    outsider.close(None).await.unwrap();
    timeout(Duration::from_secs(3), async {
        loop {
            if registry.lock().unwrap().counts() == (0, 0) {
                break;
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    task.abort();
    println!(
        "PASS: real WebSocket Accept, pre-consent signaling denial, rejection, fresh consent, tenant isolation, origin denial and disconnect cleanup"
    );
}

#[tokio::test]
async fn consent_gated_signaling_drives_real_native_webrtc_encrypted_control() {
    use core_protocol::{
        crypto::{Context, Handshake, Role},
        input::{self, Input},
        transport::{NativeTransport, local_fingerprint},
    };
    use ed25519_dalek::SigningKey;
    use rand_core::OsRng;
    use webrtc_event_helpers::*;
    let state = AppState::new(Arc::new(Fixture), HashSet::new());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", listener.local_addr().unwrap());
    let task = tokio::spawn(async move {
        axum::serve(listener, router(state)).await.unwrap();
    });
    let (mut host, host_id) = peer(&url, "host").await;
    let (mut viewer, _) = peer(&url, "viewer").await;
    let session = request(&mut viewer, &mut host, host_id).await;
    send(
        &mut host,
        ClientMessage::Decide {
            session,
            accept: true,
        },
    )
    .await;
    let ServerMessage::Accepted { nonce, .. } = event(&mut viewer).await else {
        panic!()
    };
    assert!(
        matches!(event(&mut host).await, ServerMessage::Accepted { nonce: n, .. } if n == nonce)
    );
    // Keys represent independently pinned enrollment fixtures. Neither server
    // messages nor the Hello itself are accepted as an identity trust root.
    let controller_key = SigningKey::generate(&mut OsRng);
    let host_key = SigningKey::generate(&mut OsRng);
    let context = Context {
        session_id: nonce,
        controller: controller_key.verifying_key(),
        host: host_key.verifying_key(),
    };
    let mut a = NativeTransport::new("127.0.0.1:0".into(), vec![])
        .await
        .unwrap();
    let mut b = NativeTransport::new("127.0.0.1:0".into(), vec![])
        .await
        .unwrap();
    let outbound = a
        .peer
        .create_data_channel("mixel-control-v1", None)
        .await
        .unwrap();
    let offer = a
        .local_sdp(a.peer.create_offer(None).await.unwrap())
        .await
        .unwrap();
    let handshake_a = Handshake::new(
        context.clone(),
        Role::Controller,
        &controller_key,
        local_fingerprint(&offer.sdp).unwrap(),
    )
    .unwrap();
    send(
        &mut viewer,
        ClientMessage::Signal {
            session,
            payload: Signal::Identity(handshake_a.hello()),
        },
    )
    .await;
    let ServerMessage::Signal {
        payload: Signal::Identity(hello_a),
        ..
    } = event(&mut host).await
    else {
        panic!()
    };
    send(
        &mut viewer,
        ClientMessage::Signal {
            session,
            payload: Signal::Offer(serde_json::to_string(&offer).unwrap()),
        },
    )
    .await;
    let ServerMessage::Signal {
        payload: Signal::Offer(offer),
        ..
    } = event(&mut host).await
    else {
        panic!()
    };
    b.peer
        .set_remote_description(serde_json::from_str(&offer).unwrap())
        .await
        .unwrap();
    let answer = b
        .local_sdp(b.peer.create_answer(None).await.unwrap())
        .await
        .unwrap();
    let handshake_b = Handshake::new(
        context,
        Role::Host,
        &host_key,
        local_fingerprint(&answer.sdp).unwrap(),
    )
    .unwrap();
    send(
        &mut host,
        ClientMessage::Signal {
            session,
            payload: Signal::Identity(handshake_b.hello()),
        },
    )
    .await;
    let ServerMessage::Signal {
        payload: Signal::Identity(hello_b),
        ..
    } = event(&mut viewer).await
    else {
        panic!()
    };
    send(
        &mut host,
        ClientMessage::Signal {
            session,
            payload: Signal::Answer(serde_json::to_string(&answer).unwrap()),
        },
    )
    .await;
    let ServerMessage::Signal {
        payload: Signal::Answer(answer),
        ..
    } = event(&mut viewer).await
    else {
        panic!()
    };
    a.peer
        .set_remote_description(serde_json::from_str(&answer).unwrap())
        .await
        .unwrap();
    let inbound = timeout(Duration::from_secs(15), b.incoming_channels.recv())
        .await
        .unwrap()
        .unwrap();
    tokio::join!(opened(&outbound), opened(&inbound));
    let mut ac = handshake_a
        .finish(&hello_b, a.observed_fingerprint().await.unwrap())
        .unwrap();
    let mut bc = handshake_b
        .finish(&hello_a, b.observed_fingerprint().await.unwrap())
        .unwrap();
    let input = input::encode(&Input::Text("Accepted Mixel session Grüezi 😀")).unwrap();
    let encrypted = ac.seal(&input).unwrap();
    outbound.send(encrypted.as_slice().into()).await.unwrap();
    let wire = received(&inbound).await;
    let clear = bc.open(&wire).unwrap();
    assert_eq!(
        input::decode(&clear).unwrap(),
        Input::Text("Accepted Mixel session Grüezi 😀")
    );
    let ack = bc.seal(b"host-verified-input").unwrap();
    inbound.send(ack.as_slice().into()).await.unwrap();
    assert_eq!(
        ac.open(&received(&outbound).await).unwrap(),
        b"host-verified-input"
    );
    send(
        &mut host,
        ClientMessage::Signal {
            session,
            payload: Signal::Identity(hello_b),
        },
    )
    .await;
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::Error {
            code: ErrorCode::Forbidden
        }
    ));
    a.close().await.unwrap();
    b.close().await.unwrap();
    send(&mut viewer, ClientMessage::Close { session }).await;
    for socket in [&mut viewer, &mut host] {
        assert!(matches!(event(socket).await, ServerMessage::Closed { .. }));
    }
    let fresh = request(&mut viewer, &mut host, host_id).await;
    assert_ne!(fresh, session);
    send(
        &mut host,
        ClientMessage::Decide {
            session: fresh,
            accept: false,
        },
    )
    .await;
    for socket in [&mut viewer, &mut host] {
        assert!(matches!(
            event(socket).await,
            ServerMessage::Closed {
                reason: CloseReason::Rejected,
                ..
            }
        ));
    }
    viewer.close(None).await.unwrap();
    host.close(None).await.unwrap();
    task.abort();
    println!(
        "PASS: actual WebSocket Accept -> signed identity/SDP -> UDP ICE/DTLS/SCTP -> AES256GCM Unicode input/ack -> disconnect -> fresh consent -> Reject"
    );
}
mod webrtc_event_helpers {
    use std::{sync::Arc, time::Duration};
    // Type inference through the real NativeTransport avoids maintaining a
    // second direct WebRTC dependency/version in the signaling crate.
    pub async fn opened(dc: &Arc<dyn core_protocol::transport::NativeDataChannel>) {
        tokio::time::timeout(Duration::from_secs(15), async {
            loop {
                match dc.poll().await {
                    Some(core_protocol::transport::NativeDataChannelEvent::OnOpen) => break,
                    None => panic!("transport ended"),
                    _ => {}
                }
            }
        })
        .await
        .unwrap();
    }
    pub async fn received(dc: &Arc<dyn core_protocol::transport::NativeDataChannel>) -> Vec<u8> {
        tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                match dc.poll().await {
                    Some(core_protocol::transport::NativeDataChannelEvent::OnMessage(message)) => {
                        assert!(!message.is_string);
                        return message.data.to_vec();
                    }
                    None => panic!("transport ended"),
                    _ => {}
                }
            }
        })
        .await
        .unwrap()
    }
}

#[tokio::test]
async fn renewal_keeps_real_socket_id_and_consent_but_rejects_account_switch() {
    let state = AppState::new(Arc::new(Fixture), HashSet::new());
    let registry = state.registry.clone();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", listener.local_addr().unwrap());
    let task = tokio::spawn(async move {
        axum::serve(listener, router(state)).await.unwrap();
    });
    let (mut host, id) = peer(&url, "host-short").await;
    let (mut viewer, _) = peer(&url, "viewer").await;
    let session = request(&mut viewer, &mut host, id).await;
    send(
        &mut host,
        ClientMessage::Renew {
            token: "host-renew".into(),
        },
    )
    .await;
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::Renewed { .. }
    ));
    // The initial bearer has definitely expired; renewal retains this exact
    // socket, public host ID and the pending ordinary consent invitation.
    tokio::time::sleep(Duration::from_secs(3)).await;
    send(
        &mut host,
        ClientMessage::Decide {
            session,
            accept: true,
        },
    )
    .await;
    assert!(
        matches!(event(&mut host).await, ServerMessage::Accepted { session: s, .. } if s == session)
    );
    assert!(
        matches!(event(&mut viewer).await, ServerMessage::Accepted { session: s, .. } if s == session)
    );
    send(
        &mut host,
        ClientMessage::Renew {
            token: "host-renew".into(),
        },
    )
    .await;
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::Error {
            code: ErrorCode::Busy
        }
    ));
    host.close(None).await.unwrap();
    assert!(matches!(
        event(&mut viewer).await,
        ServerMessage::Closed {
            reason: CloseReason::Disconnected,
            ..
        }
    ));
    for token in ["viewer", "outsider", "invalid", "host-expired"] {
        let (mut host, id) = peer(&url, "host").await;
        let session = request(&mut viewer, &mut host, id).await;
        send(
            &mut host,
            ClientMessage::Renew {
                token: token.into(),
            },
        )
        .await;
        assert!(
            matches!(event(&mut viewer).await, ServerMessage::Closed { session: s, reason: CloseReason::Disconnected } if s == session)
        );
        for _ in 0..50 {
            if registry.lock().unwrap().counts() == (1, 0) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert_eq!(registry.lock().unwrap().counts(), (1, 0));
    }
    viewer.close(None).await.unwrap();
    task.abort();
    let _ = task.await;
}

#[tokio::test]
async fn renewal_cannot_hold_an_expired_session_open_during_identity_lookup() {
    let state = AppState::new(Arc::new(Fixture), HashSet::new());
    let registry = state.registry.clone();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", listener.local_addr().unwrap());
    let task = tokio::spawn(async move {
        axum::serve(listener, router(state)).await.unwrap();
    });
    let (mut host, id) = peer(&url, "host-short").await;
    let (mut viewer, _) = peer(&url, "viewer").await;
    let session = request(&mut viewer, &mut host, id).await;
    let start = std::time::Instant::now();
    send(
        &mut host,
        ClientMessage::Renew {
            token: "host-slow".into(),
        },
    )
    .await;
    assert!(
        matches!(event(&mut viewer).await, ServerMessage::Closed { session: s, reason: CloseReason::Disconnected } if s == session)
    );
    assert!(start.elapsed() < Duration::from_secs(3));
    assert_eq!(registry.lock().unwrap().counts(), (1, 0));
    let (mut expired, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
    send(
        &mut expired,
        ClientMessage::Authenticate {
            token: "host-expired".into(),
        },
    )
    .await;
    // An expired principal is never assigned a Ready ID, even if a pluggable
    // identity adapter mistakenly returns one rather than rejecting it.
    loop {
        match timeout(Duration::from_secs(3), expired.next())
            .await
            .unwrap()
        {
            Some(Ok(Message::Ping(_))) | Some(Ok(Message::Pong(_))) => {}
            Some(Ok(Message::Text(_))) => panic!("expired principal registered"),
            _ => break,
        }
    }
    viewer.close(None).await.unwrap();
    task.abort();
    let _ = task.await;
}

async fn device_challenge(
    url: &str,
) -> (Socket, core_protocol::device_login::DeviceLoginChallenge) {
    let (mut socket, _) = tokio_tungstenite::connect_async(url).await.unwrap();
    send(
        &mut socket,
        ClientMessage::AuthenticateDevice {
            token: "host".into(),
            device: Uuid::from_u128(21),
        },
    )
    .await;
    let ServerMessage::DeviceChallenge { challenge } = event(&mut socket).await else {
        panic!("device challenge expected");
    };
    (socket, challenge)
}
async fn closed_without_ready(socket: &mut Socket) {
    loop {
        match timeout(Duration::from_secs(3), socket.next())
            .await
            .unwrap()
        {
            Some(Ok(Message::Text(_))) => panic!("unverified device received registration"),
            Some(Ok(Message::Ping(_))) | Some(Ok(Message::Pong(_))) => {}
            _ => return,
        }
    }
}
#[tokio::test]
async fn enrolled_socket_requires_fresh_key_proof_and_retains_id_on_reconnect() {
    let state = AppState::new(Arc::new(Fixture), HashSet::new());
    let registry = state.registry.clone();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", listener.local_addr().unwrap());
    let task = tokio::spawn(async move {
        axum::serve(listener, router(state)).await.unwrap();
    });
    let key = ed25519_dalek::SigningKey::from_bytes(&[7; 32]);
    let (mut host, challenge) = device_challenge(&url).await;
    assert_eq!(registry.lock().unwrap().counts(), (0, 0));
    let signature = challenge.sign(&key).unwrap();
    send(
        &mut host,
        ClientMessage::ProveDevice {
            signature: signature.clone(),
        },
    )
    .await;
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::Ready {
            host: 123456789,
            ..
        }
    ));
    let (mut replay, next) = device_challenge(&url).await;
    assert_ne!(next.nonce, challenge.nonce);
    send(&mut replay, ClientMessage::ProveDevice { signature }).await;
    closed_without_ready(&mut replay).await;
    let (mut impostor, next) = device_challenge(&url).await;
    send(
        &mut impostor,
        ClientMessage::ProveDevice {
            signature: next
                .sign(&ed25519_dalek::SigningKey::from_bytes(&[8; 32]))
                .unwrap(),
        },
    )
    .await;
    closed_without_ready(&mut impostor).await;
    let (mut duplicate, next) = device_challenge(&url).await;
    send(
        &mut duplicate,
        ClientMessage::ProveDevice {
            signature: next.sign(&key).unwrap(),
        },
    )
    .await;
    closed_without_ready(&mut duplicate).await;
    assert_eq!(registry.lock().unwrap().counts(), (1, 0));
    host.close(None).await.unwrap();
    for _ in 0..50 {
        if registry.lock().unwrap().counts() == (0, 0) {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    let (mut reconnect, next) = device_challenge(&url).await;
    send(
        &mut reconnect,
        ClientMessage::ProveDevice {
            signature: next.sign(&key).unwrap(),
        },
    )
    .await;
    assert!(matches!(
        event(&mut reconnect).await,
        ServerMessage::Ready {
            host: 123456789,
            ..
        }
    ));
    send(
        &mut reconnect,
        ClientMessage::Renew {
            token: "host-renew".into(),
        },
    )
    .await;
    assert!(matches!(
        event(&mut reconnect).await,
        ServerMessage::Renewed { .. }
    ));
    reconnect.close(None).await.unwrap();
    task.abort();
    let _ = task.await;
}

#[derive(Clone, Copy)]
struct SignedPeer {
    profile: Uuid,
    device: Uuid,
    tenant: Option<Uuid>,
    connection_id: u32,
    generation: u64,
    key_byte: u8,
}

impl SignedPeer {
    fn ticket(
        self,
        signer: &ed25519_dalek::SigningKey,
        unattended_host: bool,
    ) -> core_protocol::delegation::DeviceTicket {
        use core_protocol::{delegation::DeviceTicket, device_login::DeviceLoginChallenge};
        let key = ed25519_dalek::SigningKey::from_bytes(&[self.key_byte; 32]).verifying_key();
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let identity = DeviceLoginChallenge {
            nonce: [1; 32],
            device: *self.device.as_bytes(),
            profile: *self.profile.as_bytes(),
            tenant: self.tenant.map(|id| *id.as_bytes()),
            connection_id: self.connection_id,
            generation: self.generation,
        };
        if unattended_host {
            DeviceTicket::issue_unattended_host(identity, key, now, now + 60, signer)
        } else {
            DeviceTicket::issue(identity, key, self.tenant.is_none(), now, now + 60, signer)
        }
        .unwrap()
    }
}

async fn signed_peer(url: &str, signer: &ed25519_dalek::SigningKey, peer: SignedPeer) -> Socket {
    signed_peer_with_scope(url, signer, peer, false).await
}

async fn signed_peer_with_scope(
    url: &str,
    signer: &ed25519_dalek::SigningKey,
    peer: SignedPeer,
    unattended_host: bool,
) -> Socket {
    let ticket = peer.ticket(signer, unattended_host);
    let key = ed25519_dalek::SigningKey::from_bytes(&[peer.key_byte; 32]);
    connect_signed_ticket(url, &ticket, &key).await
}

async fn connect_signed_ticket(
    url: &str,
    ticket: &core_protocol::delegation::DeviceTicket,
    key: &ed25519_dalek::SigningKey,
) -> Socket {
    use signaling_server::delegated_auth::encode;
    let (mut socket, _) = tokio_tungstenite::connect_async(url).await.unwrap();
    send(
        &mut socket,
        ClientMessage::AuthenticateDevice {
            token: encode(ticket).unwrap(),
            device: Uuid::from_bytes(ticket.identity.device),
        },
    )
    .await;
    let ServerMessage::DeviceChallenge { challenge } = event(&mut socket).await else {
        panic!("challenge expected")
    };
    send(
        &mut socket,
        ClientMessage::ProveDevice {
            signature: challenge.sign(key).unwrap(),
        },
    )
    .await;
    assert!(
        matches!(event(&mut socket).await, ServerMessage::Ready { host, .. } if host == ticket.identity.connection_id)
    );
    socket
}

#[tokio::test]
async fn support_policy_blocks_direct_id_and_revokes_pending_or_active_sessions() {
    use axum::{
        Json, Router,
        extract::State,
        http::{HeaderMap, StatusCode},
        routing::post,
    };
    use ed25519_dalek::SigningKey;
    use signaling_server::{delegated_auth::TicketAuth, support_policy::SupportPolicy};
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

    const TOKEN: &str = "4111441144114411441144114411441144114411441144114411441144114411";
    const VIEWER: u32 = 234_567_890;
    const HOST: u32 = 123_456_789;
    let gate = Arc::new(AtomicBool::new(false));
    let unattended = Arc::new(AtomicBool::new(false));
    let audited = Arc::new(AtomicUsize::new(0));
    let policy = Router::new()
        .route(
            "/api/internal/remote/authorize",
            post(
                |State((gate, unattended, audited)): State<(Arc<AtomicBool>, Arc<AtomicBool>, Arc<AtomicUsize>)>,
                 headers: HeaderMap,
                 Json(pair): Json<serde_json::Value>| async move {
                    assert_eq!(headers["authorization"], format!("Bearer {TOKEN}"));
                    assert_eq!(
                        pair["controller"]["profile"],
                        Uuid::from_u128(11).to_string()
                    );
                    assert_eq!(pair["host"]["device"], Uuid::from_u128(21).to_string());
                    assert_eq!(pair["host"]["generation"], "1");
                    let is_unattended = pair["unattended"] == true;
                    if gate.load(Ordering::SeqCst)
                        && (!is_unattended || unattended.load(Ordering::SeqCst))
                    {
                        if is_unattended && pair["accept_session"].is_string() {
                            audited.fetch_add(1, Ordering::SeqCst);
                        }
                        StatusCode::NO_CONTENT
                    } else {
                        StatusCode::FORBIDDEN
                    }
                },
            ),
        )
        .route("/api/internal/remote/pairing", post(
            |headers: HeaderMap, Json(request): Json<serde_json::Value>| async move {
                assert_eq!(headers["authorization"], format!("Bearer {TOKEN}"));
                assert_eq!(request["binding"]["device"], Uuid::from_u128(21).to_string());
                let id = Uuid::from_u128(993).to_string();
                if request["confirmation"] == id {
                    return (StatusCode::NO_CONTENT, Json(serde_json::Value::Null));
                }
                (StatusCode::OK, Json(serde_json::json!({
                    "request_id": id, "provider_name": "Fixture provider", "customer_name": "Fixture customer",
                })))
            }
        ))
        .with_state((gate.clone(), unattended.clone(), audited.clone()));
    let policy_socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let policy_url = format!(
        "http://{}/api/internal/remote/authorize",
        policy_socket.local_addr().unwrap()
    );
    let policy_task =
        tokio::spawn(async move { axum::serve(policy_socket, policy).await.unwrap() });

    let signer = SigningKey::from_bytes(&[41; 32]);
    let state = AppState::new(
        Arc::new(TicketAuth::new(vec![signer.verifying_key()]).unwrap()),
        HashSet::new(),
    )
    .with_support_policy(SupportPolicy::new(policy_url.parse().unwrap(), TOKEN.into()).unwrap());
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", socket.local_addr().unwrap());
    let broker = tokio::spawn(async move { axum::serve(socket, router(state)).await.unwrap() });

    let mut host = signed_peer(
        &url,
        &signer,
        SignedPeer {
            profile: Uuid::from_u128(10),
            device: Uuid::from_u128(21),
            tenant: Some(Uuid::from_u128(1)),
            connection_id: HOST,
            generation: 1,
            key_byte: 7,
        },
    )
    .await;
    let mut viewer = signed_peer(
        &url,
        &signer,
        SignedPeer {
            profile: Uuid::from_u128(11),
            device: Uuid::from_u128(22),
            tenant: None,
            connection_id: VIEWER,
            generation: 1,
            key_byte: 8,
        },
    )
    .await;
    send(&mut host, ClientMessage::CheckPairing).await;
    let ServerMessage::PairingStatus {
        pending: Some(prompt),
        confirmed: false,
        failed: false,
    } = event(&mut host).await
    else {
        panic!("pairing prompt did not reach the enrolled host");
    };
    assert_eq!(prompt.provider_name, "Fixture provider");
    send(
        &mut host,
        ClientMessage::ConfirmPairing {
            request: prompt.request_id,
        },
    )
    .await;
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::PairingStatus {
            pending: None,
            confirmed: true,
            failed: false
        }
    ));
    send(&mut viewer, ClientMessage::Request { host: HOST }).await;
    assert!(matches!(
        event(&mut viewer).await,
        ServerMessage::Error {
            code: ErrorCode::Unavailable
        }
    ));
    assert!(
        timeout(Duration::from_millis(120), host.next())
            .await
            .is_err()
    );

    gate.store(true, Ordering::SeqCst);
    let first = request(&mut viewer, &mut host, HOST).await;
    gate.store(false, Ordering::SeqCst);
    send(
        &mut host,
        ClientMessage::Decide {
            session: first,
            accept: true,
        },
    )
    .await;
    assert!(
        matches!(event(&mut viewer).await, ServerMessage::Closed { session, .. } if session == first)
    );
    assert!(matches!(event(&mut host).await,
        ServerMessage::Closed { session, .. } if session == first));

    gate.store(true, Ordering::SeqCst);
    let second = request(&mut viewer, &mut host, HOST).await;
    send(
        &mut host,
        ClientMessage::Decide {
            session: second,
            accept: true,
        },
    )
    .await;
    assert!(
        matches!(event(&mut viewer).await, ServerMessage::Accepted { session, .. } if session == second)
    );
    assert!(
        matches!(event(&mut host).await, ServerMessage::Accepted { session, .. } if session == second)
    );
    gate.store(false, Ordering::SeqCst);
    let until = tokio::time::Instant::now() + Duration::from_secs(13);
    loop {
        let Some(Ok(message)) = tokio::time::timeout_at(until, viewer.next()).await.unwrap() else {
            panic!("active socket closed")
        };
        if let Message::Text(raw) = message {
            assert!(
                matches!(serde_json::from_str::<ServerMessage>(&raw).unwrap(),
                ServerMessage::Closed { session, .. } if session == second)
            );
            break;
        }
    }
    assert!(
        matches!(event(&mut host).await, ServerMessage::Closed { session, .. } if session == second)
    );
    gate.store(true, Ordering::SeqCst);
    send(&mut host, ClientMessage::GenerateSupportCode).await;
    let ServerMessage::SupportCode {
        required: true,
        code: Some(code),
        // D2: a 30-minute countdown.
        lifetime_seconds: 1800,
    } = event(&mut host).await
    else {
        panic!("one-time password was not generated");
    };
    let wrong = if code.reveal() == "111111111111" {
        "222222222222"
    } else {
        "111111111111"
    };
    for message in [
        ClientMessage::Request { host: HOST },
        ClientMessage::RequestWithCode {
            host: HOST,
            code: SupportCode::parse(wrong.into()).unwrap(),
        },
    ] {
        send(&mut viewer, message).await;
        assert!(matches!(
            event(&mut viewer).await,
            ServerMessage::Error {
                code: ErrorCode::Unavailable
            }
        ));
        assert!(
            timeout(Duration::from_millis(120), host.next())
                .await
                .is_err()
        );
    }
    let with_code = || ClientMessage::RequestWithCode {
        host: HOST,
        code: code.clone(),
    };
    send(&mut viewer, with_code()).await;
    let ServerMessage::Requested { session } = event(&mut viewer).await else {
        panic!("valid password did not create a request");
    };
    assert!(
        matches!(event(&mut host).await, ServerMessage::Consent { session: invited, unattended: true, .. } if invited == session)
    );
    send(
        &mut viewer,
        ClientMessage::Signal {
            session,
            payload: Signal::Offer("premature".into()),
        },
    )
    .await;
    assert!(matches!(
        event(&mut viewer).await,
        ServerMessage::Error {
            code: ErrorCode::Forbidden
        }
    ));
    send(
        &mut host,
        ClientMessage::Decide {
            session,
            accept: false,
        },
    )
    .await;
    assert!(matches!(
        event(&mut viewer).await,
        ServerMessage::Closed {
            reason: CloseReason::Rejected,
            ..
        }
    ));
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::Closed {
            reason: CloseReason::Rejected,
            ..
        }
    ));
    // The reusable password continues to work for subsequent connections until turned off.
    send(&mut viewer, with_code()).await;
    let ServerMessage::Requested { session: again } = event(&mut viewer).await else {
        panic!("reusable password did not allow a second request");
    };
    assert!(
        matches!(event(&mut host).await, ServerMessage::Consent { session: invited, unattended: true, .. } if invited == again)
    );
    send(&mut viewer, ClientMessage::Close { session: again }).await;
    assert!(
        matches!(event(&mut viewer).await, ServerMessage::Closed { session, .. } if session == again)
    );
    assert!(
        matches!(event(&mut host).await, ServerMessage::Closed { session, .. } if session == again)
    );
    send(&mut host, ClientMessage::ClearSupportCode).await;
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::SupportCode {
            required: false,
            code: None,
            ..
        }
    ));
    let ordinary = request(&mut viewer, &mut host, HOST).await;
    send(&mut viewer, ClientMessage::Close { session: ordinary }).await;
    assert!(
        matches!(event(&mut viewer).await, ServerMessage::Closed { session, .. } if session == ordinary)
    );
    assert!(
        matches!(event(&mut host).await, ServerMessage::Closed { session, .. } if session == ordinary)
    );

    // Neither a server grant alone nor local permission alone admits an unattended request.
    unattended.store(true, Ordering::SeqCst);
    let unattended_request = || ClientMessage::RequestUnattended { host: HOST };
    send(&mut viewer, unattended_request()).await;
    assert!(matches!(
        event(&mut viewer).await,
        ServerMessage::Error {
            code: ErrorCode::Unavailable
        }
    ));
    unattended.store(false, Ordering::SeqCst);
    send(
        &mut host,
        ClientMessage::ConfigureUnattended { enabled: true },
    )
    .await;
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::UnattendedStatus { enabled: true }
    ));
    send(&mut viewer, unattended_request()).await;
    assert!(matches!(
        event(&mut viewer).await,
        ServerMessage::Error {
            code: ErrorCode::Unavailable
        }
    ));
    unattended.store(true, Ordering::SeqCst);
    send(&mut viewer, unattended_request()).await;
    let ServerMessage::Requested { session } = event(&mut viewer).await else {
        panic!("unattended request expected")
    };
    assert!(
        matches!(event(&mut host).await, ServerMessage::Consent { session: invited, unattended: true, .. } if invited == session)
    );
    send(
        &mut host,
        ClientMessage::Decide {
            session,
            accept: true,
        },
    )
    .await;
    assert!(
        matches!(event(&mut viewer).await, ServerMessage::Accepted { session: accepted, .. } if accepted == session)
    );
    assert!(
        matches!(event(&mut host).await, ServerMessage::Accepted { session: accepted, .. } if accepted == session)
    );
    assert_eq!(audited.load(Ordering::SeqCst), 1);
    send(
        &mut host,
        ClientMessage::ConfigureUnattended { enabled: false },
    )
    .await;
    assert!(
        matches!(event(&mut viewer).await, ServerMessage::Closed { session: closed, .. } if closed == session)
    );
    assert!(
        matches!(event(&mut host).await, ServerMessage::Closed { session: closed, .. } if closed == session)
    );
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::UnattendedStatus { enabled: false }
    ));
    policy_task.abort();
    broker.abort();
}

#[tokio::test]
async fn startup_identity_can_only_receive_policy_approved_unattended_sessions() {
    use axum::{Json, Router, http::StatusCode, routing::post};
    use signaling_server::{
        delegated_auth::{TicketAuth, encode},
        support_policy::SupportPolicy,
    };
    use std::sync::atomic::{AtomicBool, Ordering};

    let signer = ed25519_dalek::SigningKey::from_bytes(&[42; 32]);
    let broker_state = || {
        AppState::new(
            Arc::new(TicketAuth::new(vec![signer.verifying_key()]).unwrap()),
            HashSet::new(),
        )
    };
    let host_identity = SignedPeer {
        profile: Uuid::from_u128(10),
        device: Uuid::from_u128(21),
        tenant: Some(Uuid::from_u128(1)),
        connection_id: 123_456_789,
        generation: 1,
        key_byte: 7,
    };
    let viewer_identity = SignedPeer {
        profile: Uuid::from_u128(11),
        device: Uuid::from_u128(22),
        connection_id: 234_567_890,
        key_byte: 8,
        ..host_identity
    };
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", socket.local_addr().unwrap());
    let unconfigured = tokio::spawn(axum::serve(socket, router(broker_state())).into_future());
    let (mut rejected, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
    send(
        &mut rejected,
        ClientMessage::AuthenticateDevice {
            token: encode(&host_identity.ticket(&signer, true)).unwrap(),
            device: host_identity.device,
        },
    )
    .await;
    closed_without_ready(&mut rejected).await;
    unconfigured.abort();

    let grant = Arc::new(AtomicBool::new(false));
    let policy_grant = grant.clone();
    let policy = Router::new().route(
        "/api/internal/remote/authorize",
        post(move |Json(pair): Json<serde_json::Value>| {
            let grant = policy_grant.clone();
            async move {
                assert_eq!(
                    pair["controller"]["profile"],
                    viewer_identity.profile.to_string()
                );
                assert_eq!(pair["host"]["device"], host_identity.device.to_string());
                assert_eq!(pair["unattended"], true);
                if grant.load(Ordering::SeqCst) {
                    StatusCode::NO_CONTENT
                } else {
                    StatusCode::FORBIDDEN
                }
            }
        }),
    );
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let policy_url = format!(
        "http://{}/api/internal/remote/authorize",
        socket.local_addr().unwrap()
    );
    let policy_task = tokio::spawn(axum::serve(socket, policy).into_future());
    let state = broker_state().with_support_policy(
        SupportPolicy::new(policy_url.parse().unwrap(), "e".repeat(64)).unwrap(),
    );
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", socket.local_addr().unwrap());
    let broker = tokio::spawn(axum::serve(socket, router(state)).into_future());
    let mut host = signed_peer_with_scope(&url, &signer, host_identity, true).await;
    let mut viewer = signed_peer(&url, &signer, viewer_identity).await;
    send(&mut host, ClientMessage::CheckPairing).await;
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::PairingStatus { failed: true, .. }
    ));
    send(&mut host, ClientMessage::GenerateSupportCode).await;
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::Error {
            code: ErrorCode::Forbidden
        }
    ));
    send(
        &mut host,
        ClientMessage::RequestUnattended {
            host: viewer_identity.connection_id,
        },
    )
    .await;
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::Error {
            code: ErrorCode::Unavailable
        }
    ));
    send(
        &mut host,
        ClientMessage::ConfigureUnattended { enabled: true },
    )
    .await;
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::UnattendedStatus { enabled: true }
    ));
    send(
        &mut viewer,
        ClientMessage::Request {
            host: host_identity.connection_id,
        },
    )
    .await;
    assert!(matches!(
        event(&mut viewer).await,
        ServerMessage::Error {
            code: ErrorCode::Unavailable
        }
    ));

    let request = || ClientMessage::RequestUnattended {
        host: host_identity.connection_id,
    };
    send(&mut viewer, request()).await;
    assert!(matches!(
        event(&mut viewer).await,
        ServerMessage::Error {
            code: ErrorCode::Unavailable
        }
    ));
    grant.store(true, Ordering::SeqCst);
    send(&mut viewer, request()).await;
    let ServerMessage::Requested { session } = event(&mut viewer).await else {
        panic!("approved unattended request expected");
    };
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::Consent {
            unattended: true,
            ..
        }
    ));
    send(
        &mut host,
        ClientMessage::Decide {
            session,
            accept: true,
        },
    )
    .await;
    assert!(
        matches!(event(&mut viewer).await, ServerMessage::Accepted { host_identity: Some(ticket), .. } if ticket.unattended_host_only())
    );
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::Accepted { .. }
    ));
    send(
        &mut host,
        ClientMessage::Renew {
            token: encode(&host_identity.ticket(&signer, false)).unwrap(),
        },
    )
    .await;
    closed_without_ready(&mut host).await;
    assert!(
        matches!(event(&mut viewer).await, ServerMessage::Closed { session: ended, .. } if ended == session)
    );
    broker.abort();
    policy_task.abort();
}

#[tokio::test]
#[ignore = "run by the Cockpit two-database browser fixture"]
async fn cockpit_ism_policy_service_on_real_broker() {
    use ed25519_dalek::SigningKey;
    use signaling_server::{delegated_auth::TicketAuth, support_policy::SupportPolicy};
    let env = |name| std::env::var(name).unwrap();
    let uuid = |name| env(name).parse::<Uuid>().unwrap();
    let signer = SigningKey::from_bytes(&[41; 32]);
    let state = AppState::new(
        Arc::new(TicketAuth::new(vec![signer.verifying_key()]).unwrap()),
        HashSet::new(),
    )
    .with_support_policy(
        SupportPolicy::new(
            env("QA_POLICY_URL").parse().unwrap(),
            env("QA_POLICY_TOKEN"),
        )
        .unwrap(),
    );
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", socket.local_addr().unwrap());
    let broker = tokio::spawn(async move { axum::serve(socket, router(state)).await.unwrap() });
    let mut host = signed_peer(
        &url,
        &signer,
        SignedPeer {
            profile: uuid("QA_HOST_PROFILE"),
            device: uuid("QA_HOST_DEVICE"),
            tenant: Some(uuid("QA_ISM_ORG")),
            connection_id: 222_222_222,
            generation: 1,
            key_byte: 7,
        },
    )
    .await;
    let mut controller = signed_peer(
        &url,
        &signer,
        SignedPeer {
            profile: uuid("QA_ACTOR_PROFILE"),
            device: uuid("QA_ACTOR_DEVICE"),
            tenant: None,
            connection_id: 111_111_111,
            generation: 1,
            key_byte: 8,
        },
    )
    .await;
    match env("QA_POLICY_SCENARIO").as_str() {
        "pair" => {
            send(&mut host, ClientMessage::CheckPairing).await;
            let ServerMessage::PairingStatus {
                pending: Some(prompt),
                failed: false,
                ..
            } = event(&mut host).await
            else {
                panic!("pending pairing did not reach the enrolled device");
            };
            assert_eq!(prompt.customer_name, "Customer Apple");
            send(
                &mut host,
                ClientMessage::ConfirmPairing {
                    request: prompt.request_id,
                },
            )
            .await;
            assert!(matches!(
                event(&mut host).await,
                ServerMessage::PairingStatus {
                    confirmed: true,
                    failed: false,
                    ..
                }
            ));
        }
        "allow" => {
            let session = request(&mut controller, &mut host, 222_222_222).await;
            send(
                &mut host,
                ClientMessage::Decide {
                    session,
                    accept: true,
                },
            )
            .await;
            assert!(
                matches!(event(&mut controller).await, ServerMessage::Accepted { session: actual, .. } if actual == session)
            );
            assert!(
                matches!(event(&mut host).await, ServerMessage::Accepted { session: actual, .. } if actual == session)
            );
        }
        "deny" => {
            send(
                &mut controller,
                ClientMessage::Request { host: 222_222_222 },
            )
            .await;
            assert!(matches!(
                event(&mut controller).await,
                ServerMessage::Error {
                    code: ErrorCode::Unavailable
                }
            ));
            assert!(
                timeout(Duration::from_millis(150), host.next())
                    .await
                    .is_err()
            );
        }
        _ => panic!("unknown policy scenario"),
    }
    broker.abort();
}

#[tokio::test]
#[ignore = "run by the Cockpit two-database browser fixture"]
async fn cockpit_startup_issuer_and_broker_use_the_current_enrollment_and_grant() {
    use core_protocol::{
        delegation::{DelegationProof, DeviceTicket},
        device_login::DeviceLoginChallenge,
    };
    use ed25519_dalek::SigningKey;
    use signaling_server::{
        auth::IsmAuth,
        delegated_auth::TicketAuth,
        identity_authority::{Authority, router as identity_router},
        support_policy::SupportPolicy,
    };

    let env = |name| std::env::var(name).unwrap();
    let uuid = |name| env(name).parse::<Uuid>().unwrap();
    let signer = SigningKey::from_bytes(&[41; 32]);
    let roots = [signer.verifying_key()];
    let device = SigningKey::from_bytes(&[9; 32]);
    let tenant = uuid("QA_ISM_ORG");
    let identity = DeviceLoginChallenge {
        nonce: [23; 32],
        device: *uuid("QA_HOST_DEVICE").as_bytes(),
        profile: *uuid("QA_HOST_PROFILE").as_bytes(),
        tenant: Some(*tenant.as_bytes()),
        connection_id: 222_222_222,
        generation: 2,
    };
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let proof = DelegationProof::new_unattended_host(identity, now, &device).unwrap();
    let authority = Authority::new(
        IsmAuth::new(
            "https://identity.example.test".parse().unwrap(),
            "public".into(),
        )
        .unwrap(),
        signer.clone(),
    )
    .with_startup_policy(
        SupportPolicy::for_startup_host(
            env("QA_STARTUP_URL").parse().unwrap(),
            env("QA_STARTUP_TOKEN"),
        )
        .unwrap(),
    );
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!(
        "http://{}/v1/startup-host-ticket",
        socket.local_addr().unwrap()
    );
    let issuer = tokio::spawn(axum::serve(socket, identity_router(authority)).into_future());
    let response = reqwest::Client::new()
        .post(url)
        .json(&serde_json::json!({
            "proof":proof, "public_key":device.verifying_key().to_bytes(),
        }))
        .send()
        .await
        .unwrap();
    if env("QA_POLICY_SCENARIO") == "startup-deny" {
        assert_eq!(response.status(), reqwest::StatusCode::FORBIDDEN);
        issuer.abort();
        return;
    }
    assert_eq!(response.status(), reqwest::StatusCode::OK);
    let ticket: DeviceTicket = response.json().await.unwrap();
    assert!(
        ticket
            .verify(&roots, now)
            .unwrap()
            .claims()
            .unattended_host_only()
    );

    let state = AppState::new(
        Arc::new(TicketAuth::new(roots.to_vec()).unwrap()),
        HashSet::new(),
    )
    .with_support_policy(
        SupportPolicy::new(
            env("QA_POLICY_URL").parse().unwrap(),
            env("QA_POLICY_TOKEN"),
        )
        .unwrap(),
    );
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", socket.local_addr().unwrap());
    let broker = tokio::spawn(axum::serve(socket, router(state)).into_future());
    let mut host = connect_signed_ticket(&url, &ticket, &device).await;
    let mut viewer = signed_peer(
        &url,
        &signer,
        SignedPeer {
            profile: uuid("QA_ACTOR_PROFILE"),
            device: uuid("QA_ACTOR_DEVICE"),
            tenant: None,
            connection_id: 111_111_111,
            generation: 1,
            key_byte: 8,
        },
    )
    .await;
    send(
        &mut host,
        ClientMessage::ConfigureUnattended { enabled: true },
    )
    .await;
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::UnattendedStatus { enabled: true }
    ));
    send(
        &mut viewer,
        ClientMessage::RequestUnattended {
            host: ticket.identity.connection_id,
        },
    )
    .await;
    let ServerMessage::Requested { session } = event(&mut viewer).await else {
        panic!("expected a policy-approved unattended session");
    };
    assert!(matches!(
        event(&mut host).await,
        ServerMessage::Consent {
            unattended: true,
            ..
        }
    ));
    send(
        &mut host,
        ClientMessage::Decide {
            session,
            accept: true,
        },
    )
    .await;
    assert!(
        matches!(event(&mut viewer).await, ServerMessage::Accepted { session: id, .. } if id == session)
    );
    assert!(
        matches!(event(&mut host).await, ServerMessage::Accepted { session: id, .. } if id == session)
    );
    issuer.abort();
    broker.abort();
}

#[tokio::test]
#[ignore = "requires the isolated macOS guest and its loopback broker"]
async fn signed_macos_startup_agent_accepts_after_reboot() {
    use ed25519_dalek::SigningKey;

    let address: std::net::SocketAddr = std::env::var("MIXEL_STARTUP_TEST_BROKER")
        .unwrap()
        .parse()
        .unwrap();
    assert!(address.ip().is_loopback());
    let path = std::env::var_os("MIXEL_STARTUP_TEST_SIGNER").unwrap();
    let raw = zeroize::Zeroizing::new(std::fs::read_to_string(path).unwrap());
    let raw = raw.trim();
    assert_eq!(raw.len(), 64);
    assert!(raw.bytes().all(|byte| byte.is_ascii_hexdigit()));
    let mut seed = zeroize::Zeroizing::new([0u8; 32]);
    for (index, byte) in seed.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&raw[index * 2..index * 2 + 2], 16).unwrap();
    }
    let signer = SigningKey::from_bytes(&seed);
    let mut viewer = signed_peer(
        &format!("ws://{address}/v1/ws"),
        &signer,
        SignedPeer {
            profile: Uuid::from_u128(31),
            device: Uuid::from_u128(32),
            tenant: None,
            connection_id: 365_800_092,
            generation: 1,
            key_byte: 17,
        },
    )
    .await;
    let host = 365_800_091;
    send(&mut viewer, ClientMessage::Request { host }).await;
    assert!(matches!(
        event(&mut viewer).await,
        ServerMessage::Error {
            code: ErrorCode::Unavailable
        }
    ));
    send(&mut viewer, ClientMessage::RequestUnattended { host }).await;
    let ServerMessage::Requested { session } = event(&mut viewer).await else {
        panic!("startup host must receive the approved unattended request");
    };
    let ServerMessage::Accepted {
        session: accepted,
        nonce,
        controller_identity: Some(controller),
        host_identity: Some(ticket),
        ..
    } = event(&mut viewer).await
    else {
        panic!("signed headless Mac must accept the unattended request");
    };
    assert_eq!(accepted, session);
    assert_eq!(ticket.identity.connection_id, host);
    assert!(ticket.unattended_host_only());
    println!("Signed macOS startup host accepted unattended session {session}");
    if let Some(directory) = std::env::var_os("MIXEL_STARTUP_TEST_MEDIA_DIR") {
        let binding = core_protocol::delegation::session_binding(
            *session.as_bytes(),
            nonce,
            &controller,
            &ticket,
        );
        let host_key = ticket
            .verify(
                &[signer.verifying_key()],
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs(),
            )
            .unwrap()
            .key();
        verify_loginwindow_media(&mut viewer, session, binding, host_key, directory.into()).await;
    }
    send(&mut viewer, ClientMessage::Close { session }).await;
    loop {
        if matches!(event(&mut viewer).await, ServerMessage::Closed { session: closed, .. } if closed == session)
        {
            break;
        }
    }
    viewer.close(None).await.unwrap();
}

async fn verify_loginwindow_media(
    viewer: &mut Socket,
    session: Uuid,
    binding: [u8; 32],
    host: ed25519_dalek::VerifyingKey,
    directory: std::path::PathBuf,
) {
    use core_protocol::{
        crypto::{Context, Handshake, Role},
        input::{self, Input},
        media::{H264Assembler, TrackRemoteEvent},
        transport::{
            NativeIceCandidate, NativeSessionDescription, NativeTransport, local_fingerprint,
        },
    };
    use webrtc_event_helpers::{opened, received};

    let address: std::net::IpAddr = std::env::var("MIXEL_STARTUP_TEST_MEDIA_BIND")
        .unwrap()
        .parse()
        .unwrap();
    let mut transport = NativeTransport::new(format!("{address}:0"), vec![])
        .await
        .unwrap();
    transport.receive_video().await.unwrap();
    let channel = transport
        .peer
        .create_data_channel("mixel-control-v1", None)
        .await
        .unwrap();
    let offer = transport
        .local_sdp(transport.peer.create_offer(None).await.unwrap())
        .await
        .unwrap();
    let key = ed25519_dalek::SigningKey::from_bytes(&[17; 32]);
    let proof = Handshake::new(
        Context {
            session_id: binding,
            controller: key.verifying_key(),
            host,
        },
        Role::Controller,
        &key,
        local_fingerprint(&offer.sdp).unwrap(),
    )
    .unwrap();
    for payload in [Signal::Identity(proof.hello()), Signal::Offer(offer.sdp)] {
        send(viewer, ClientMessage::Signal { session, payload }).await;
    }
    let mut host_hello = None;
    let mut candidates = Vec::new();
    loop {
        match event(viewer).await {
            ServerMessage::Signal {
                session: active,
                payload,
            } if active == session => match payload {
                Signal::Identity(hello) => host_hello = Some(hello),
                Signal::Answer(sdp) => {
                    transport
                        .peer
                        .set_remote_description(NativeSessionDescription::answer(sdp).unwrap())
                        .await
                        .unwrap();
                    break;
                }
                Signal::Ice(candidate) => {
                    candidates.push(serde_json::from_str::<NativeIceCandidate>(&candidate).unwrap())
                }
                Signal::Offer(_) => panic!("unexpected host offer"),
            },
            ServerMessage::Closed { reason, .. } => {
                panic!("native host closed before video: {reason:?}")
            }
            other => panic!("unexpected negotiation message: {other:?}"),
        }
    }
    for candidate in candidates {
        transport.peer.add_ice_candidate(candidate).await.unwrap();
    }
    opened(&channel).await;
    let mut crypto = proof
        .finish(
            &host_hello.unwrap(),
            transport.observed_fingerprint().await.unwrap(),
        )
        .unwrap();
    println!("Data channel opened on the controller");
    let permissions = timeout(Duration::from_secs(12), async {
        loop {
            tokio::select! {
                frame = channel.poll() => match frame {
                    Some(core_protocol::transport::NativeDataChannelEvent::OnMessage(message)) => break message.data,
                    _ => panic!("host control stream ended before its grant"),
                },
                message = viewer.next() => if let Message::Text(text) = message.unwrap().unwrap() {
                    match serde_json::from_str::<ServerMessage>(&text).unwrap() {
                        ServerMessage::Signal { payload: Signal::Ice(candidate), .. } => {
                            transport.peer.add_ice_candidate(serde_json::from_str(&candidate).unwrap()).await.unwrap();
                        }
                        ServerMessage::PeerRenewed { .. } => {}
                        other => panic!("host before control grant: {other:?}"),
                    }
                }
            }
        }
    }).await.expect("no host control grant");
    assert_eq!(
        crypto.open(&permissions).unwrap(),
        b"mixel-permissions-v3\x01\x00\x00"
    );
    println!("Verified DTLS certificate, device identity and encrypted keyboard/mouse grant");

    let track = timeout(Duration::from_secs(10), transport.incoming_tracks.recv())
        .await
        .unwrap()
        .unwrap();
    std::fs::create_dir_all(&directory).unwrap();
    let mut assembler = H264Assembler::default();
    macro_rules! snapshot {
        ($name:literal) => {{
            let frame = timeout(Duration::from_secs(8), async {
                loop {
                    let TrackRemoteEvent::OnRtpPacket(packet) =
                        track.poll().await.expect("video track closed")
                    else {
                        continue;
                    };
                    if let Some(unit) = assembler
                        .push(
                            packet.header.sequence_number,
                            packet.header.timestamp,
                            packet.header.marker,
                            &packet.payload,
                        )
                        .unwrap()
                        && unit
                            .windows(5)
                            .any(|nal| nal[..4] == [0, 0, 0, 1] && nal[4] & 31 == 5)
                    {
                        break unit;
                    }
                }
            })
            .await
            .expect("no login-window keyframe");
            println!(
                "Received H.264 login-window frame {} ({} bytes)",
                $name,
                frame.len()
            );
            std::fs::write(directory.join(concat!($name, ".h264")), &*frame).unwrap();
        }};
    }
    macro_rules! send_record {
        ($record:expr) => {{
            let sealed = crypto.seal($record).unwrap();
            channel.send(sealed.as_slice().into()).await.unwrap();
        }};
    }
    macro_rules! send_input {
        ($input:expr) => {{
            send_record!(&input::encode(&$input).unwrap());
            loop {
                let record = received(&channel).await;
                match crypto.open(&record).unwrap().as_slice() {
                    b"mixel-input-posted-v1" => break,
                    b"mixel-video-idle-v1" => continue,
                    other => panic!("unexpected control record: {other:?}"),
                }
            }
            println!("Native host acknowledged {:?}", $input);
        }};
    }
    snapshot!("01-before");
    send_input!(Input::Pointer {
        display: 0,
        x: 32_768,
        y: 58_700
    });
    send_input!(Input::Button {
        button: 1,
        down: true
    });
    send_input!(Input::Button {
        button: 1,
        down: false
    });
    tokio::time::sleep(Duration::from_millis(250)).await;
    send_record!(b"mixel-video-keyframe-v1");
    snapshot!("02-mouse");
    // The synthetic key must not submit the login form.
    if std::env::var_os("MIXEL_STARTUP_TEST_UNICODE").is_some() {
        send_input!(Input::Text("a"));
    } else {
        send_input!(Input::Key {
            usage: 4,
            down: true
        });
        send_input!(Input::Key {
            usage: 4,
            down: false
        });
    }
    tokio::time::sleep(Duration::from_millis(1100)).await;
    send_record!(b"mixel-video-keyframe-v1");
    snapshot!("03-key");
    if std::env::var_os("MIXEL_STARTUP_TEST_OBSERVE_KEY").is_some() {
        println!("Observing the synthetic key at the guest login window before clearing it");
        tokio::time::sleep(Duration::from_secs(15)).await;
    }
    send_input!(Input::Key {
        usage: 42,
        down: true
    });
    send_input!(Input::Key {
        usage: 42,
        down: false
    });
    tokio::time::sleep(Duration::from_millis(1100)).await;
    send_record!(b"mixel-video-keyframe-v1");
    snapshot!("04-cleared");
    transport.close().await.unwrap();
}

/// Remote SaaS tenants: the policy answers 200 with the tenant's concurrent-session limit, and
/// the broker refuses further session requests of that tenant until one ends.
#[tokio::test]
async fn saas_policy_limit_caps_concurrent_sessions_of_a_tenant() {
    use axum::{Json, Router, http::StatusCode, routing::post};
    use ed25519_dalek::SigningKey;
    use signaling_server::{delegated_auth::TicketAuth, support_policy::SupportPolicy};

    const TOKEN: &str = "5111441144114411441144114411441144114411441144114411441144114411";
    let tenant = Uuid::from_u128(500);
    let policy = Router::new().route(
        "/api/internal/remote/authorize",
        post(move |Json(_pair): Json<serde_json::Value>| async move {
            (StatusCode::OK, Json(serde_json::json!({"tenant": tenant, "max_sessions": 1})))
        }),
    );
    let policy_socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let policy_url = format!("http://{}/api/internal/remote/authorize", policy_socket.local_addr().unwrap());
    let policy_task = tokio::spawn(async move { axum::serve(policy_socket, policy).await.unwrap() });
    let signer = SigningKey::from_bytes(&[51; 32]);
    let state = AppState::new(Arc::new(TicketAuth::new(vec![signer.verifying_key()]).unwrap()), HashSet::new())
        .with_support_policy(SupportPolicy::new(policy_url.parse().unwrap(), TOKEN.into()).unwrap());
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", socket.local_addr().unwrap());
    let broker = tokio::spawn(async move { axum::serve(socket, router(state)).await.unwrap() });
    let org = Some(Uuid::from_u128(600));
    let peer = |profile: u128, device: u128, connection_id: u32, key_byte: u8| SignedPeer {
        profile: Uuid::from_u128(profile),
        device: Uuid::from_u128(device),
        tenant: org,
        connection_id,
        generation: 1,
        key_byte,
    };
    let mut first_host = signed_peer(&url, &signer, peer(10, 21, 123_456_701, 1)).await;
    let mut second_host = signed_peer(&url, &signer, peer(10, 22, 123_456_702, 2)).await;
    let mut viewer = signed_peer(&url, &signer, peer(11, 23, 123_456_703, 3)).await;
    let first = request(&mut viewer, &mut first_host, 123_456_701).await;
    send(&mut viewer, ClientMessage::Request { host: 123_456_702 }).await;
    assert!(matches!(event(&mut viewer).await, ServerMessage::Error { code: ErrorCode::Busy }));
    assert!(timeout(Duration::from_millis(150), second_host.next()).await.is_err());
    send(&mut viewer, ClientMessage::Close { session: first }).await;
    assert!(matches!(event(&mut viewer).await, ServerMessage::Closed { session, .. } if session == first));
    let second = request(&mut viewer, &mut second_host, 123_456_702).await;
    assert_ne!(first, second);
    broker.abort();
    policy_task.abort();
}

/// Second technician and switching sides over real WebSockets, signed device tickets and the
/// support policy: the colleague is checked like a direct request, and the switched session is
/// checked as the technician helping the computer (never the computer controlling staff).
#[tokio::test]
async fn colleague_joins_and_sides_switch_with_the_support_policy_on_a_real_broker() {
    use axum::{Json, Router, extract::State, http::StatusCode, routing::post};
    use ed25519_dalek::SigningKey;
    use signaling_server::{delegated_auth::TicketAuth, support_policy::SupportPolicy};
    use std::sync::Mutex;

    const TOKEN: &str = "4111441144114411441144114411441144114411441144114411441144114411";
    const HOST: u32 = 123_456_789;
    const TECHNICIAN: u32 = 234_567_890;
    const COLLEAGUE: u32 = 345_678_901;
    type Asked = Arc<Mutex<Vec<(String, String)>>>;
    let asked: Asked = Arc::default();
    let policy = Router::new()
        .route(
            "/api/internal/remote/authorize",
            post(|State(asked): State<Asked>, Json(pair): Json<serde_json::Value>| async move {
                let controller = pair["controller"]["profile"].as_str().unwrap_or_default().to_owned();
                let host = pair["host"]["device"].as_str().unwrap_or_default().to_owned();
                asked.lock().unwrap().push((controller.clone(), host.clone()));
                // Technicians 11 and 12 may help device 21; nobody may "help" a technician's device.
                let allowed = [Uuid::from_u128(11).to_string(), Uuid::from_u128(12).to_string()].contains(&controller)
                    && host == Uuid::from_u128(21).to_string();
                if allowed { StatusCode::NO_CONTENT } else { StatusCode::FORBIDDEN }
            }),
        )
        .with_state(asked.clone());
    let policy_socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let policy_url = format!("http://{}/api/internal/remote/authorize", policy_socket.local_addr().unwrap());
    let policy_task = tokio::spawn(async move { axum::serve(policy_socket, policy).await.unwrap() });
    let signer = SigningKey::from_bytes(&[41; 32]);
    let state = AppState::new(Arc::new(TicketAuth::new(vec![signer.verifying_key()]).unwrap()), HashSet::new())
        .with_support_policy(SupportPolicy::new(policy_url.parse().unwrap(), TOKEN.into()).unwrap());
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", socket.local_addr().unwrap());
    let broker = tokio::spawn(async move { axum::serve(socket, router(state)).await.unwrap() });
    let peer = |profile: u128, device: u128, tenant: Option<Uuid>, connection_id: u32, key_byte: u8| SignedPeer {
        profile: Uuid::from_u128(profile),
        device: Uuid::from_u128(device),
        tenant,
        connection_id,
        generation: 1,
        key_byte,
    };
    let mut host = signed_peer(&url, &signer, peer(10, 21, Some(Uuid::from_u128(1)), HOST, 7)).await;
    let mut technician = signed_peer(&url, &signer, peer(11, 22, None, TECHNICIAN, 8)).await;
    let mut colleague = signed_peer(&url, &signer, peer(12, 23, None, COLLEAGUE, 9)).await;
    for socket in [&mut host, &mut technician, &mut colleague] {
        send(socket, ClientMessage::Capabilities { features: vec![FEATURE_JOIN.into(), FEATURE_SWITCH.into()] }).await;
    }
    // Takes a requested session to the running phase.
    async fn run(controller: &mut Socket, host: &mut Socket, session: Uuid) {
        send(host, ClientMessage::Decide { session, accept: true }).await;
        assert!(matches!(event(controller).await, ServerMessage::Accepted { session: s, .. } if s == session));
        assert!(matches!(event(host).await, ServerMessage::Accepted { session: s, .. } if s == session));
        send(controller, ClientMessage::Signal { session, payload: Signal::Offer("v=0".into()) }).await;
        assert!(matches!(event(host).await, ServerMessage::Signal { .. }));
        send(host, ClientMessage::Signal { session, payload: Signal::Answer("v=0".into()) }).await;
        assert!(matches!(event(controller).await, ServerMessage::Signal { .. }));
    }
    send(&mut technician, ClientMessage::Request { host: HOST }).await;
    let ServerMessage::Requested { session } = event(&mut technician).await else { panic!("requested") };
    assert!(matches!(event(&mut host).await, ServerMessage::Consent { joining: None, switched: None, .. }));
    run(&mut technician, &mut host, session).await;

    // Invite the colleague by account; the colleague joins after the person at the computer agrees.
    send(&mut technician, ClientMessage::Invite { session, user: Some(Uuid::from_u128(12)), device: None, control: true }).await;
    assert!(matches!(event(&mut technician).await, ServerMessage::InviteStatus { delivered: 1, .. }));
    let ServerMessage::Invited { invitation, host: HOST, control: true, .. } = event(&mut colleague).await else { panic!("invited") };
    send(&mut colleague, ClientMessage::JoinSession { invitation }).await;
    let ServerMessage::Requested { session: joined } = event(&mut colleague).await else { panic!("joined request") };
    let ServerMessage::Consent { session: asked_session, requester_connection: COLLEAGUE, joining: Some(joining), requester_identity: Some(_), .. } = event(&mut host).await else { panic!("colleague consent") };
    assert_eq!((asked_session, joining.session, joining.inviter_connection), (joined, session, TECHNICIAN));
    run(&mut colleague, &mut host, joined).await;
    // While the colleague watches, the sides stay; the person at the computer removes the colleague.
    send(&mut technician, ClientMessage::SwitchSides { session }).await;
    assert!(matches!(event(&mut technician).await, ServerMessage::Error { code: ErrorCode::Busy }));
    send(&mut host, ClientMessage::Close { session: joined }).await;
    assert!(matches!(event(&mut colleague).await, ServerMessage::Closed { reason: CloseReason::Requested, .. }));
    assert!(matches!(event(&mut host).await, ServerMessage::Closed { session: s, .. } if s == joined));

    // Switch sides: the computer that was helped now sees the technician's screen.
    send(&mut technician, ClientMessage::SwitchSides { session }).await;
    assert!(matches!(event(&mut technician).await, ServerMessage::Closed { reason: CloseReason::Switched, .. }));
    let ServerMessage::Consent { session: reversed, requester_connection: HOST, switched: Some(from), .. } = event(&mut technician).await else { panic!("switched consent") };
    assert_eq!((from.previous, from.by), (session, TECHNICIAN));
    assert!(matches!(event(&mut host).await, ServerMessage::Closed { reason: CloseReason::Switched, .. }));
    assert!(matches!(event(&mut host).await, ServerMessage::Switched { previous, session: s, host: TECHNICIAN } if previous == session && s == reversed));
    asked.lock().unwrap().clear();
    run(&mut host, &mut technician, reversed).await;
    // The policy was asked about the technician helping device 21, not the reverse.
    assert_eq!(asked.lock().unwrap().as_slice(), [(Uuid::from_u128(11).to_string(), Uuid::from_u128(21).to_string())]);
    // And back again.
    send(&mut host, ClientMessage::SwitchSides { session: reversed }).await;
    assert!(matches!(event(&mut host).await, ServerMessage::Closed { reason: CloseReason::Switched, .. }));
    let ServerMessage::Consent { session: back, switched: Some(from), .. } = event(&mut host).await else { panic!("switch back consent") };
    assert_eq!(from.by, HOST);
    assert!(matches!(event(&mut technician).await, ServerMessage::Closed { reason: CloseReason::Switched, .. }));
    assert!(matches!(event(&mut technician).await, ServerMessage::Switched { host: HOST, .. }));
    run(&mut technician, &mut host, back).await;
    send(&mut technician, ClientMessage::Close { session: back }).await;
    assert!(matches!(event(&mut host).await, ServerMessage::Closed { reason: CloseReason::Requested, .. }));
    broker.abort();
    policy_task.abort();
}

/// The broker-wide connection limit (`MIXEL_MAX_CONNECTIONS`) is a setting: beyond it a new
/// WebSocket gets 503, and a closed connection frees its place again.
#[tokio::test]
async fn the_connection_limit_is_configurable_and_frees_closed_places() {
    let state = AppState::new(Arc::new(Fixture), HashSet::new()).with_max_connections(2);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", listener.local_addr().unwrap());
    let task = tokio::spawn(async move {
        axum::serve(listener, router(state)).await.unwrap();
    });
    let (first, _) = peer(&url, "host").await;
    let (_second, _) = peer(&url, "viewer").await;
    let refused = tokio_tungstenite::connect_async(&url).await.unwrap_err();
    assert!(
        matches!(&refused, tokio_tungstenite::tungstenite::Error::Http(response) if response.status() == 503),
        "{refused:?}"
    );
    drop(first);
    // The place is free once the broker has seen the close.
    let mut again = None;
    for _ in 0..50 {
        if let Ok((socket, _)) = tokio_tungstenite::connect_async(&url).await {
            again = Some(socket);
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    assert!(again.is_some(), "a closed connection frees its place");
    task.abort();
}

/// Who gets onto a host with unattended access on (and a support password set) without the
/// person at the host being asked. Only controllers the policy service allows unattended access
/// get in without a question; controllers it allows only attended support are asked about; anyone
/// it refuses (another company, an unrelated account) and anyone not signed in gets nothing, with
/// or without the password.
#[tokio::test]
async fn unattended_access_only_for_controllers_the_policy_allows() {
    use axum::{Json, Router, http::StatusCode, routing::post};
    use ed25519_dalek::SigningKey;
    use signaling_server::{delegated_auth::TicketAuth, support_policy::SupportPolicy};

    const TOKEN: &str = "5222552255225522552255225522552255225522552255225522552255225522";
    const HOST: u32 = 123_456_789;
    const UNATTENDED_TECHNICIAN: u32 = 200_000_001;
    const ATTENDED_TECHNICIAN: u32 = 200_000_002;
    const STRANGER: u32 = 200_000_003;
    // The policy service of the fixture: profile 12 may help unattended, profile 11 only attended,
    // profile 13 (another company) not at all.
    let policy = Router::new().route(
        "/api/internal/remote/authorize",
        post(|Json(pair): Json<serde_json::Value>| async move {
            let controller = pair["controller"]["profile"].as_str().unwrap_or_default().to_owned();
            let unattended = pair["unattended"] == true;
            let allowed = if controller == Uuid::from_u128(12).to_string() {
                true
            } else if controller == Uuid::from_u128(11).to_string() {
                !unattended
            } else {
                false
            };
            if allowed { StatusCode::NO_CONTENT } else { StatusCode::FORBIDDEN }
        }),
    );
    let policy_socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let policy_url = format!("http://{}/api/internal/remote/authorize", policy_socket.local_addr().unwrap());
    let policy_task = tokio::spawn(async move { axum::serve(policy_socket, policy).await.unwrap() });
    let signer = SigningKey::from_bytes(&[52; 32]);
    let state = AppState::new(Arc::new(TicketAuth::new(vec![signer.verifying_key()]).unwrap()), HashSet::new())
        .with_support_policy(SupportPolicy::new(policy_url.parse().unwrap(), TOKEN.into()).unwrap());
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", socket.local_addr().unwrap());
    let broker = tokio::spawn(async move { axum::serve(socket, router(state)).await.unwrap() });
    let peer = |profile: u128, device: u128, tenant: u128, connection_id: u32, key_byte: u8| SignedPeer {
        profile: Uuid::from_u128(profile),
        device: Uuid::from_u128(device),
        tenant: Some(Uuid::from_u128(tenant)),
        connection_id,
        generation: 1,
        key_byte,
    };
    let mut host = signed_peer(&url, &signer, peer(10, 20, 1, HOST, 60)).await;
    let mut attended = signed_peer(&url, &signer, peer(11, 21, 1, ATTENDED_TECHNICIAN, 61)).await;
    let mut unattended = signed_peer(&url, &signer, peer(12, 22, 1, UNATTENDED_TECHNICIAN, 62)).await;
    let mut stranger = signed_peer(&url, &signer, peer(13, 23, 9, STRANGER, 63)).await;

    send(&mut host, ClientMessage::ConfigureUnattended { enabled: true }).await;
    assert!(matches!(event(&mut host).await, ServerMessage::UnattendedStatus { enabled: true }));

    // Refused by the policy: nothing reaches the host, whatever is asked.
    for message in [ClientMessage::Request { host: HOST }, ClientMessage::RequestUnattended { host: HOST }] {
        send(&mut stranger, message).await;
        assert!(matches!(event(&mut stranger).await, ServerMessage::Error { code: ErrorCode::Unavailable }));
        assert!(quiet(&mut host).await, "a refused controller reached the host");
    }

    // Not signed in at all: no session can be asked for.
    let (mut anonymous, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
    send(&mut anonymous, ClientMessage::Request { host: HOST }).await;
    let answer = timeout(Duration::from_secs(2), anonymous.next()).await;
    if let Ok(Some(Ok(Message::Text(raw)))) = answer {
        assert!(!matches!(serde_json::from_str::<ServerMessage>(&raw), Ok(ServerMessage::Requested { .. })));
    }
    assert!(quiet(&mut host).await, "an anonymous client reached the host");

    // Allowed attended support only: the host is asked (no unattended consent), and an explicit
    // unattended request is refused.
    send(&mut attended, ClientMessage::RequestUnattended { host: HOST }).await;
    assert!(matches!(event(&mut attended).await, ServerMessage::Error { code: ErrorCode::Unavailable }));
    assert!(quiet(&mut host).await);
    send(&mut attended, ClientMessage::Request { host: HOST }).await;
    let ServerMessage::Requested { session: asked } = event(&mut attended).await else {
        panic!("an attended technician's request was refused instead of being asked about");
    };
    assert!(matches!(event(&mut host).await,
        ServerMessage::Consent { session, unattended: false, requester_connection: ATTENDED_TECHNICIAN, .. } if session == asked));
    send(&mut host, ClientMessage::Decide { session: asked, accept: false }).await;
    while let Ok(Some(Ok(_))) = timeout(Duration::from_millis(150), attended.next()).await {}
    while let Ok(Some(Ok(_))) = timeout(Duration::from_millis(150), host.next()).await {}

    // Allowed unattended access: the plain request is unattended (no question at the host).
    send(&mut unattended, ClientMessage::Request { host: HOST }).await;
    let ServerMessage::Requested { session: silent } = event(&mut unattended).await else {
        panic!("an unattended technician's request was refused");
    };
    assert!(matches!(event(&mut host).await,
        ServerMessage::Consent { session, unattended: true, requester_connection: UNATTENDED_TECHNICIAN, .. } if session == silent));
    send(&mut host, ClientMessage::Decide { session: silent, accept: false }).await;
    while let Ok(Some(Ok(_))) = timeout(Duration::from_millis(150), unattended.next()).await {}
    while let Ok(Some(Ok(_))) = timeout(Duration::from_millis(150), host.next()).await {}

    // With a support password set: the right password does not let a refused controller in, and a
    // controller the policy allows attended support needs it (then, by the owner's choice, it is
    // not asked about).
    send(&mut host, ClientMessage::GenerateSupportCode).await;
    let ServerMessage::SupportCode { code: Some(code), .. } = event(&mut host).await else {
        panic!("support password was not generated");
    };
    let password = || ClientMessage::RequestWithCode { host: HOST, code: SupportCode::parse(code.reveal().into()).unwrap() };
    send(&mut stranger, password()).await;
    assert!(matches!(event(&mut stranger).await, ServerMessage::Error { code: ErrorCode::Unavailable }));
    assert!(quiet(&mut host).await, "a refused controller got in with the password");
    send(&mut attended, ClientMessage::Request { host: HOST }).await;
    assert!(matches!(event(&mut attended).await, ServerMessage::Error { code: ErrorCode::Unavailable }));
    assert!(quiet(&mut host).await, "the password was skipped");
    send(&mut attended, password()).await;
    let ServerMessage::Requested { session: with_password } = event(&mut attended).await else {
        panic!("the password was refused");
    };
    assert!(matches!(event(&mut host).await,
        ServerMessage::Consent { session, unattended: true, requester_connection: ATTENDED_TECHNICIAN, .. } if session == with_password));

    policy_task.abort();
    broker.abort();
}

/// Nothing arrives on the socket for a moment.
async fn quiet(socket: &mut Socket) -> bool {
    timeout(Duration::from_millis(150), socket.next()).await.is_err()
}

/// Server additions of 17 Nov 2026 on a real broker: new message types and fields reach only apps
/// that announced them (2.0.14-2.0.18 parse every server message strictly), the device policy (D1),
/// signed commands (D4), the policy re-check riding out a restart of the policy service but not a
/// refusal, and the 1012 close code on a broker restart.
#[tokio::test]
async fn server_additions_reach_only_apps_that_announced_them_and_old_apps_keep_working() {
    use axum::{Json, Router, extract::State, http::StatusCode, routing::post};
    use ed25519_dalek::SigningKey;
    use signaling_server::{delegated_auth::TicketAuth, support_policy::SupportPolicy};
    use std::sync::atomic::{AtomicU8, Ordering};

    const TOKEN: &str = "6333663366336633663366336633663366336633663366336633663366336633";
    const OLD_HOST: u32 = 123_456_789;
    const NEW_HOST: u32 = 123_456_790;
    const VIEWER: u32 = 200_000_011;
    // 0: allow, 1: unreachable (503), 2: refuse (403).
    let mode = Arc::new(AtomicU8::new(0));
    let policy = Router::new()
        .route(
            "/api/internal/remote/authorize",
            post(|State(mode): State<Arc<AtomicU8>>| async move {
                match mode.load(Ordering::SeqCst) {
                    0 => StatusCode::NO_CONTENT,
                    1 => StatusCode::SERVICE_UNAVAILABLE,
                    _ => StatusCode::FORBIDDEN,
                }
            }),
        )
        .route(
            "/api/internal/remote/device-policy",
            post(|| async { Json(serde_json::json!({ "unattended_default": true, "unattended_allowed": false })) }),
        )
        .route(
            "/api/internal/remote/agent",
            post(|Json(request): Json<serde_json::Value>| async move {
                let binding = &request["binding"];
                Json(serde_json::json!({
                    "id": Uuid::from_u128(4242), "shell": "sh", "script": "uname -a", "timeout_seconds": 300,
                    "signed": {
                        "key_id": "58b522e9976086ec", "device": binding["device"], "connection_id": binding["connection_id"],
                        "issued_at": 1_794_900_000u64, "expires_at": 1_794_986_400u64,
                        "nonce": "ab".repeat(32), "signature": "cd".repeat(64),
                    }
                }))
            }),
        )
        .with_state(mode.clone());
    let policy_socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let policy_url = format!("http://{}/api/internal/remote/authorize", policy_socket.local_addr().unwrap());
    let policy_task = tokio::spawn(async move { axum::serve(policy_socket, policy).await.unwrap() });
    let signer = SigningKey::from_bytes(&[63; 32]);
    let state = AppState::new(Arc::new(TicketAuth::new(vec![signer.verifying_key()]).unwrap()), HashSet::new())
        .with_support_policy(SupportPolicy::new(policy_url.parse().unwrap(), TOKEN.into()).unwrap());
    let registry = state.registry.clone();
    let shutdown = state.clone();
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", socket.local_addr().unwrap());
    let broker = tokio::spawn(async move { axum::serve(socket, router(state)).await.unwrap() });
    let peer = |profile: u128, device: u128, connection_id: u32, key_byte: u8| SignedPeer {
        profile: Uuid::from_u128(profile),
        device: Uuid::from_u128(device),
        tenant: Some(Uuid::from_u128(1)),
        connection_id,
        generation: 1,
        key_byte,
    };
    // A 2.0.18 app announces join and switch only; a new app also the new features.
    let mut old_host = signed_peer(&url, &signer, peer(10, 20, OLD_HOST, 70)).await;
    let mut new_host = signed_peer(&url, &signer, peer(14, 24, NEW_HOST, 74)).await;
    let mut viewer = signed_peer(&url, &signer, peer(11, 21, VIEWER, 71)).await;
    send(&mut old_host, ClientMessage::Capabilities { features: vec![FEATURE_JOIN.into(), FEATURE_SWITCH.into()] }).await;
    send(&mut new_host, ClientMessage::Capabilities {
        features: [FEATURE_JOIN, FEATURE_SWITCH, FEATURE_ENDING, FEATURE_POLICY, FEATURE_RESUME, FEATURE_SIGNED].map(String::from).to_vec(),
    })
    .await;
    assert!(matches!(event(&mut new_host).await,
        ServerMessage::DevicePolicy { unattended_default: true, unattended_allowed: false, require_signed_commands: Some(false) }));
    assert!(quiet(&mut old_host).await, "an old app got a device policy");

    // App version: accepted from any app, reported with presence.
    send(&mut new_host, ClientMessage::AppVersion { version: "2.0.19".into(), platform: "linux-x64".into() }).await;
    send(&mut old_host, ClientMessage::AppVersion { version: "not a version".into(), platform: "x".into() }).await;
    assert!(matches!(event(&mut old_host).await, ServerMessage::Error { code: ErrorCode::Invalid }));
    tokio::time::sleep(Duration::from_millis(100)).await;
    let unix = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
    assert_eq!(registry.lock().unwrap().device_versions(unix), vec![(NEW_HOST, "2.0.19".into(), "linux-x64".into())]);

    // D4: the signature goes to the app that checks it; the old app gets the old shape, byte for byte.
    async fn raw_agent(socket: &mut Socket) -> String {
        send(socket, ClientMessage::Agent { inventory: None, result: None }).await;
        loop {
            let message = timeout(Duration::from_secs(3), socket.next()).await.unwrap().unwrap().unwrap();
            if let Message::Text(text) = message && text.contains("\"agent\"") {
                return text.to_string();
            }
        }
    }
    let old_agent = raw_agent(&mut old_host).await;
    assert!(old_agent.contains("uname -a") && !old_agent.contains("signed"), "{old_agent}");
    let new_agent = raw_agent(&mut new_host).await;
    let ServerMessage::Agent { command: Some(AgentCommand { signed: Some(signed), .. }) } = serde_json::from_str(&new_agent).unwrap() else {
        panic!("{new_agent}");
    };
    assert_eq!((signed.device, signed.connection_id), (Uuid::from_u128(24), NEW_HOST));

    // A running session rides out a policy service that does not answer, and ends on a refusal.
    send(&mut viewer, ClientMessage::Request { host: OLD_HOST }).await;
    let ServerMessage::Requested { session } = event(&mut viewer).await else { panic!("request refused") };
    assert!(matches!(event(&mut old_host).await, ServerMessage::Consent { session: asked, .. } if asked == session));
    send(&mut old_host, ClientMessage::Decide { session, accept: true }).await;
    assert!(matches!(event(&mut viewer).await, ServerMessage::Accepted { .. }));
    assert!(matches!(event(&mut old_host).await, ServerMessage::Accepted { .. }));
    send(&mut viewer, ClientMessage::Signal { session, payload: Signal::Offer("v=0".into()) }).await;
    assert!(matches!(event(&mut old_host).await, ServerMessage::Signal { .. }));
    send(&mut old_host, ClientMessage::Signal { session, payload: Signal::Answer("v=0".into()) }).await;
    assert!(matches!(event(&mut viewer).await, ServerMessage::Signal { .. }));
    mode.store(1, Ordering::SeqCst);
    // Two re-checks (every 10 s) without an answer: the session stays.
    let until = tokio::time::Instant::now() + Duration::from_secs(22);
    while tokio::time::Instant::now() < until {
        if let Ok(Some(Ok(Message::Text(raw)))) = tokio::time::timeout_at(until, viewer.next()).await {
            panic!("the session ended while the policy service did not answer: {raw}");
        }
    }
    mode.store(2, Ordering::SeqCst);
    let until = tokio::time::Instant::now() + Duration::from_secs(13);
    loop {
        let Ok(Some(Ok(message))) = tokio::time::timeout_at(until, viewer.next()).await else {
            panic!("a refused session was not ended");
        };
        if let Message::Text(raw) = message {
            assert!(matches!(serde_json::from_str::<ServerMessage>(&raw).unwrap(), ServerMessage::Closed { session: closed, .. } if closed == session));
            break;
        }
    }

    // A broker restart closes every connection with 1012, which the apps treat as temporary.
    shutdown.begin_shutdown();
    registry.lock().unwrap().shutdown();
    for socket in [&mut old_host, &mut new_host, &mut viewer] {
        let code = loop {
            match timeout(Duration::from_secs(3), socket.next()).await.unwrap() {
                Some(Ok(Message::Close(frame))) => break frame.map(|frame| u16::from(frame.code)),
                Some(Ok(_)) => continue,
                other => panic!("no close frame: {other:?}"),
            }
        };
        assert_eq!(code, Some(1012));
    }
    policy_task.abort();
    broker.abort();
}

/// Several sessions at once (Mixel Remote 2.1): one technician holds four sessions to four hosts on a
/// real broker, each with its own signaling; a fifth is refused; sessions end in mixed order, from either
/// side, without touching the others; a freed place can be used again.
#[tokio::test]
async fn one_technician_holds_four_sessions_that_end_in_any_order() {
    let state = AppState::new(
        Arc::new(Fixture),
        HashSet::from(["https://staging.example".into()]),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", listener.local_addr().unwrap());
    let task = tokio::spawn(async move {
        axum::serve(listener, router(state)).await.unwrap();
    });
    let (mut viewer, _) = peer(&url, "viewer").await;
    let mut hosts = Vec::new();
    for _ in 0..5 {
        hosts.push(peer(&url, "host").await);
    }
    let mut fifth = hosts.pop().unwrap();
    // Four requests from the same technician, each accepted by its own host.
    let mut sessions = Vec::new();
    for (host, id) in hosts.iter_mut() {
        let session = request(&mut viewer, host, *id).await;
        send(host, ClientMessage::Decide { session, accept: true }).await;
        let ServerMessage::Accepted { nonce, .. } = event(&mut viewer).await else {
            panic!("accept expected")
        };
        assert_ne!(nonce, [0; 32]);
        assert!(matches!(event(host).await, ServerMessage::Accepted { session: id, .. } if id == session));
        // Offer and answer, per session.
        send(&mut viewer, ClientMessage::Signal { session, payload: Signal::Offer(format!("offer {id}")) }).await;
        assert!(matches!(event(host).await, ServerMessage::Signal { payload: Signal::Offer(_), session: s, .. } if s == session));
        send(host, ClientMessage::Signal { session, payload: Signal::Answer(format!("answer {id}")) }).await;
        assert!(matches!(event(&mut viewer).await, ServerMessage::Signal { payload: Signal::Answer(_), session: s, .. } if s == session));
        sessions.push(session);
    }
    assert_eq!(sessions.len(), 4);
    let distinct: HashSet<_> = sessions.iter().collect();
    assert_eq!(distinct.len(), 4, "four independent sessions");
    // A fifth session of the same technician is refused with a typed answer, not dropped.
    send(&mut viewer, ClientMessage::Request { host: fifth.1 }).await;
    assert!(matches!(
        event(&mut viewer).await,
        ServerMessage::Error { code: ErrorCode::Busy }
    ));
    assert!(quiet(&mut fifth.0).await, "the refused host was not bothered");
    // Signaling is per session: a message of session 2 reaches host 2 and nobody else.
    send(
        &mut viewer,
        ClientMessage::Signal { session: sessions[1], payload: Signal::Ice("two".into()) },
    )
    .await;
    assert!(matches!(
        event(&mut hosts[1].0).await,
        ServerMessage::Signal { session, .. } if session == sessions[1]
    ));
    for index in [0, 2, 3] {
        assert!(quiet(&mut hosts[index].0).await, "host {index} heard nothing of session 2");
    }
    // End session 3 from the technician, session 1 from its host, in mixed order.
    send(&mut viewer, ClientMessage::Close { session: sessions[2] }).await;
    assert!(matches!(event(&mut viewer).await, ServerMessage::Closed { session, .. } if session == sessions[2]));
    assert!(matches!(event(&mut hosts[2].0).await, ServerMessage::Closed { session, .. } if session == sessions[2]));
    send(&mut hosts[0].0, ClientMessage::Close { session: sessions[0] }).await;
    assert!(matches!(event(&mut hosts[0].0).await, ServerMessage::Closed { session, .. } if session == sessions[0]));
    assert!(matches!(event(&mut viewer).await, ServerMessage::Closed { session, .. } if session == sessions[0]));
    // The two others still carry signaling, both ways.
    for index in [1, 3] {
        send(
            &mut viewer,
            ClientMessage::Signal { session: sessions[index], payload: Signal::Ice(format!("alive {index}")) },
        )
        .await;
        assert!(matches!(
            event(&mut hosts[index].0).await,
            ServerMessage::Signal { session, .. } if session == sessions[index]
        ));
        send(
            &mut hosts[index].0,
            ClientMessage::Signal { session: sessions[index], payload: Signal::Ice(format!("back {index}")) },
        )
        .await;
        assert!(matches!(
            event(&mut viewer).await,
            ServerMessage::Signal { session, .. } if session == sessions[index]
        ));
    }
    // Two places are free again: the fifth host can be asked now.
    let again = request(&mut viewer, &mut fifth.0, fifth.1).await;
    send(&mut fifth.0, ClientMessage::Decide { session: again, accept: true }).await;
    assert!(matches!(event(&mut viewer).await, ServerMessage::Accepted { .. }));
    assert!(matches!(event(&mut fifth.0).await, ServerMessage::Accepted { .. }));
    send(&mut viewer, ClientMessage::Signal { session: again, payload: Signal::Offer("offer".into()) }).await;
    assert!(matches!(event(&mut fifth.0).await, ServerMessage::Signal { payload: Signal::Offer(_), .. }));
    send(&mut fifth.0, ClientMessage::Signal { session: again, payload: Signal::Answer("answer".into()) }).await;
    assert!(matches!(event(&mut viewer).await, ServerMessage::Signal { payload: Signal::Answer(_), .. }));
    // End the rest in the order 4, 2, new: each closes alone.
    for (order, session) in [(3, sessions[3]), (1, sessions[1])] {
        send(&mut viewer, ClientMessage::Close { session }).await;
        assert!(matches!(event(&mut viewer).await, ServerMessage::Closed { session: id, .. } if id == session));
        assert!(matches!(event(&mut hosts[order].0).await, ServerMessage::Closed { session: id, .. } if id == session));
    }
    send(
        &mut viewer,
        ClientMessage::Signal { session: again, payload: Signal::Ice("last".into()) },
    )
    .await;
    assert!(matches!(event(&mut fifth.0).await, ServerMessage::Signal { session, .. } if session == again));
    task.abort();
}

/// Relay credentials are limited per session, not per computer (Mixel Remote 2.1): one technician starts
/// four sessions to four hosts within a second and every session gets its credentials; asking again for
/// the same session at once is still refused, and so is a connection asking far too often overall.
#[tokio::test]
async fn relay_credentials_are_limited_per_session_not_per_computer() {
    use ed25519_dalek::SigningKey;
    use signaling_server::{delegated_auth::TicketAuth, turn::TurnConfig};

    let signer = SigningKey::from_bytes(&[81; 32]);
    let state = AppState::new(Arc::new(TicketAuth::new(vec![signer.verifying_key()]).unwrap()), HashSet::new())
        .with_turn(Some(TurnConfig::LanOnly));
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", socket.local_addr().unwrap());
    let broker = tokio::spawn(async move { axum::serve(socket, router(state)).await.unwrap() });
    let identity = |profile: u128, device: u128, connection_id: u32, key_byte: u8| SignedPeer {
        profile: Uuid::from_u128(profile),
        device: Uuid::from_u128(device),
        tenant: None,
        connection_id,
        generation: 1,
        key_byte,
    };
    let mut viewer = signed_peer(&url, &signer, identity(11, 30, 234_567_890, 90)).await;
    let mut hosts = Vec::new();
    for index in 0..4u32 {
        let host = signed_peer(&url, &signer, identity(10, 40 + index as u128, 123_456_800 + index, 91 + index as u8)).await;
        hosts.push((host, 123_456_800 + index));
    }
    let mut sessions = Vec::new();
    for (host, id) in hosts.iter_mut() {
        let session = request(&mut viewer, host, *id).await;
        send(host, ClientMessage::Decide { session, accept: true }).await;
        assert!(matches!(event(&mut viewer).await, ServerMessage::Accepted { .. }));
        assert!(matches!(event(host).await, ServerMessage::Accepted { .. }));
        sessions.push(session);
    }
    // All four ask back to back: every one is answered with its own credentials.
    let started = std::time::Instant::now();
    for session in &sessions {
        send(&mut viewer, ClientMessage::Ice { session: *session }).await;
        assert!(
            matches!(event(&mut viewer).await, ServerMessage::Ice { session: answered, .. } if answered == *session),
            "session {session} got no relay credentials"
        );
    }
    assert!(started.elapsed() < Duration::from_secs(2), "the four asks were not back to back");
    // The same session again within 5 s: refused. The other sessions are not affected by it.
    send(&mut viewer, ClientMessage::Ice { session: sessions[0] }).await;
    assert!(matches!(event(&mut viewer).await, ServerMessage::Error { code: ErrorCode::Busy }));
    // A host asking for a session of its own is limited by that session too.
    send(&mut hosts[1].0, ClientMessage::Ice { session: sessions[1] }).await;
    assert!(matches!(event(&mut hosts[1].0).await, ServerMessage::Ice { .. }));
    send(&mut hosts[1].0, ClientMessage::Ice { session: sessions[1] }).await;
    assert!(matches!(event(&mut hosts[1].0).await, ServerMessage::Error { code: ErrorCode::Busy }));
    // Hammering one session never gets a second lease.
    for _ in 0..30 {
        send(&mut viewer, ClientMessage::Ice { session: sessions[2] }).await;
        assert!(matches!(event(&mut viewer).await, ServerMessage::Error { code: ErrorCode::Busy }));
    }
    // After 5 s the session may ask again (a normal renewal).
    tokio::time::sleep(Duration::from_millis(5_200)).await;
    send(&mut viewer, ClientMessage::Ice { session: sessions[0] }).await;
    assert!(matches!(event(&mut viewer).await, ServerMessage::Ice { .. }));
    broker.abort();
}

/// Early relay credentials on a real broker: only with the gate on and the feature announced, to both sides of
/// a live request; a refused request and an app that did not announce get nothing.
#[tokio::test]
async fn relay_credentials_before_consent_only_with_the_gate_and_the_announcement() {
    use ed25519_dalek::SigningKey;
    use signaling_server::{delegated_auth::TicketAuth, turn::TurnConfig};

    let signer = SigningKey::from_bytes(&[82; 32]);
    let state = AppState::new(Arc::new(TicketAuth::new(vec![signer.verifying_key()]).unwrap()), HashSet::new())
        .with_turn(Some(TurnConfig::LanOnly))
        .with_early_ice(true);
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", socket.local_addr().unwrap());
    let broker = tokio::spawn(async move { axum::serve(socket, router(state)).await.unwrap() });
    let identity = |profile: u128, device: u128, connection_id: u32, key_byte: u8| SignedPeer {
        profile: Uuid::from_u128(profile),
        device: Uuid::from_u128(device),
        tenant: None,
        connection_id,
        generation: 1,
        key_byte,
    };
    let mut viewer = signed_peer(&url, &signer, identity(11, 30, 234_567_890, 90)).await;
    let mut host = signed_peer(&url, &signer, identity(10, 40, 123_456_800, 91)).await;
    let mut plain = signed_peer(&url, &signer, identity(12, 50, 345_678_901, 92)).await;
    // An app that did not announce the feature is not told about it and gets nothing before consent.
    send(&mut plain, ClientMessage::Capabilities { features: vec!["join".into()] }).await;
    assert!(quiet(&mut plain).await);
    let session = request(&mut viewer, &mut host, 123_456_800).await;
    send(&mut viewer, ClientMessage::Ice { session }).await;
    assert!(matches!(event(&mut viewer).await, ServerMessage::Error { code: ErrorCode::Forbidden }));
    // Announcing the feature: the broker says it is on, and both sides of the live request get a lease.
    for socket in [&mut viewer, &mut host] {
        send(socket, ClientMessage::Capabilities { features: vec!["earlyice".into()] }).await;
        assert!(matches!(event(socket).await, ServerMessage::EarlyIce));
    }
    send(&mut viewer, ClientMessage::Ice { session }).await;
    assert!(matches!(event(&mut viewer).await, ServerMessage::Ice { session: answered, .. } if answered == session));
    send(&mut host, ClientMessage::Ice { session }).await;
    assert!(matches!(event(&mut host).await, ServerMessage::Ice { session: answered, .. } if answered == session));
    // A stranger that announced the feature gets nothing for somebody else's session.
    send(&mut plain, ClientMessage::Capabilities { features: vec!["earlyice".into()] }).await;
    assert!(matches!(event(&mut plain).await, ServerMessage::EarlyIce));
    send(&mut plain, ClientMessage::Ice { session }).await;
    assert!(matches!(event(&mut plain).await, ServerMessage::Error { code: ErrorCode::Forbidden }));
    // A refused request: no lease for the new session either.
    let refused = request(&mut viewer, &mut host, 123_456_800).await;
    send(&mut host, ClientMessage::Decide { session: refused, accept: false }).await;
    let _ = event(&mut viewer).await;
    send(&mut viewer, ClientMessage::Ice { session: refused }).await;
    assert!(matches!(event(&mut viewer).await, ServerMessage::Error { code: ErrorCode::Forbidden }));
    broker.abort();
}

/// D4 phase 3: with MIXEL_AGENT_REQUIRE_SIGNED on, `device_policy` carries `require_signed_commands: true`,
/// but only for apps that announced `signed`; an app that announced only `policy` gets the old shape.
#[tokio::test]
async fn device_policy_tells_signing_apps_to_refuse_unsigned_commands_when_the_broker_requires_it() {
    use axum::{Json, Router, http::StatusCode, routing::post};
    use ed25519_dalek::SigningKey;
    use signaling_server::{delegated_auth::TicketAuth, support_policy::SupportPolicy};

    const TOKEN: &str = "7444744474447444744474447444744474447444744474447444744474447444";
    let policy = Router::new()
        .route("/api/internal/remote/authorize", post(|| async { StatusCode::NO_CONTENT }))
        .route(
            "/api/internal/remote/device-policy",
            post(|| async { Json(serde_json::json!({ "unattended_default": false, "unattended_allowed": true })) }),
        );
    let policy_socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let policy_url = format!("http://{}/api/internal/remote/authorize", policy_socket.local_addr().unwrap());
    let policy_task = tokio::spawn(async move { axum::serve(policy_socket, policy).await.unwrap() });
    let signer = SigningKey::from_bytes(&[82; 32]);
    let state = AppState::new(Arc::new(TicketAuth::new(vec![signer.verifying_key()]).unwrap()), HashSet::new())
        .with_support_policy(SupportPolicy::new(policy_url.parse().unwrap(), TOKEN.into()).unwrap())
        .with_signed_commands_required(true);
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/v1/ws", socket.local_addr().unwrap());
    let broker = tokio::spawn(async move { axum::serve(socket, router(state)).await.unwrap() });
    let identity = |profile: u128, device: u128, connection_id: u32, key_byte: u8| SignedPeer {
        profile: Uuid::from_u128(profile),
        device: Uuid::from_u128(device),
        tenant: Some(Uuid::from_u128(1)),
        connection_id,
        generation: 1,
        key_byte,
    };
    let mut signing_host = signed_peer(&url, &signer, identity(10, 20, 123_456_810, 61)).await;
    let mut policy_only_host = signed_peer(&url, &signer, identity(11, 21, 123_456_811, 62)).await;
    send(&mut signing_host, ClientMessage::Capabilities { features: [FEATURE_POLICY, FEATURE_SIGNED].map(String::from).to_vec() }).await;
    send(&mut policy_only_host, ClientMessage::Capabilities { features: vec![FEATURE_POLICY.to_string()] }).await;
    assert!(matches!(event(&mut signing_host).await,
        ServerMessage::DevicePolicy { unattended_default: false, unattended_allowed: true, require_signed_commands: Some(true) }));
    let text = loop {
        let message = timeout(Duration::from_secs(3), policy_only_host.next()).await.unwrap().unwrap().unwrap();
        if let Message::Text(text) = message { break text.to_string(); }
    };
    assert!(text.contains("device_policy") && !text.contains("require_signed"), "{text}");
    policy_task.abort();
    broker.abort();
}
