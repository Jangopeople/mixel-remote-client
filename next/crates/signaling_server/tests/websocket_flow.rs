//! Real TCP/WebSocket transport with a deterministic identity fixture. This does
//! not claim a live ISM login or a media/desktop session.
use futures_util::{SinkExt, StreamExt};
use signaling_server::{
    auth::{AuthError, Principal},
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
    fn authenticate<'a>(
        &'a self,
        token: &'a str,
    ) -> Pin<Box<dyn Future<Output = Result<Principal, AuthError>> + Send + 'a>> {
        Box::pin(async move {
            let tenant = match token {
                "host" | "viewer" => 1,
                "outsider" => 2,
                _ => return Err(AuthError),
            };
            Ok(Principal {
                user: Uuid::new_v4(),
                tenant: Some(Uuid::from_u128(tenant)),
                staff: false,
                expires_at: SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs()
                    + 600,
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
    let ServerMessage::Ready { host, protocol: 1 } = event(&mut socket).await else {
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
    for peer in [&mut viewer, &mut host] {
        assert!(matches!(
            event(peer).await,
            ServerMessage::Closed {
                reason: CloseReason::Requested,
                ..
            }
        ));
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
