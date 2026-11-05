#![cfg(feature = "native-webrtc")]
//! A real authenticated TURN server forwards actual ICE/DTLS/SCTP packets.
//! Relay-only SDP and server byte counters exclude a hidden direct fallback.
use core_protocol::{
    crypto::{Context, Handshake, Role},
    input::{self, Input},
    transport::{NativeTransport, TransportError, local_fingerprint},
};
use ed25519_dalek::SigningKey;
use rand_core::{OsRng, RngCore};
use std::{
    net::{IpAddr, Ipv4Addr, SocketAddr},
    sync::Arc,
    time::Duration,
};
use tokio::{net::UdpSocket, sync::mpsc};
use turn::{
    Error,
    auth::{AuthHandler, generate_auth_key},
    relay::relay_static::RelayAddressGeneratorStatic,
    server::{
        Server,
        config::{ConnConfig, ServerConfig},
    },
};
use turn_util::vnet::net::Net;
use webrtc::{
    data_channel::{DataChannel, DataChannelEvent},
    peer_connection::RTCIceServer,
};

const REALM: &str = "mixel-local-qa";
struct Auth {
    key: Vec<u8>,
}
impl AuthHandler for Auth {
    fn auth_handle(
        &self,
        username: &str,
        realm: &str,
        source: SocketAddr,
    ) -> Result<Vec<u8>, Error> {
        if username == "mixel-qa" && realm == REALM && source.ip().is_loopback() {
            Ok(self.key.clone())
        } else {
            Err(Error::ErrNoPermission)
        }
    }
}
async fn server() -> (
    Server,
    RTCIceServer,
    mpsc::Receiver<turn::allocation::AllocationInfo>,
) {
    let conn = Arc::new(UdpSocket::bind("127.0.0.1:0").await.unwrap());
    let address = conn.local_addr().unwrap();
    let password = format!("{:016x}{:016x}", OsRng.next_u64(), OsRng.next_u64());
    let (tx, rx) = mpsc::channel(8);
    let server = Server::new(ServerConfig {
        conn_configs: vec![ConnConfig {
            conn,
            relay_addr_generator: Box::new(RelayAddressGeneratorStatic {
                relay_address: IpAddr::V4(Ipv4Addr::LOCALHOST),
                address: "127.0.0.1".into(),
                net: Arc::new(Net::new(None)),
            }),
        }],
        realm: REALM.into(),
        auth_handler: Arc::new(Auth {
            key: generate_auth_key("mixel-qa", REALM, &password),
        }),
        channel_bind_timeout: Duration::from_secs(60),
        alloc_close_notify: Some(tx),
    })
    .await
    .unwrap();
    (
        server,
        RTCIceServer {
            urls: vec![format!("turn:{address}?transport=udp")],
            username: "mixel-qa".into(),
            credential: password,
        },
        rx,
    )
}
async fn opened(channel: &Arc<dyn DataChannel>) {
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            match channel.poll().await {
                Some(DataChannelEvent::OnOpen) => break,
                None | Some(DataChannelEvent::OnClose | DataChannelEvent::OnError) => {
                    panic!("Relay channel closed before open")
                }
                _ => {}
            }
        }
    })
    .await
    .expect("Relay ICE/DTLS/SCTP must open");
}
async fn received(channel: &Arc<dyn DataChannel>) -> Vec<u8> {
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            match channel.poll().await {
                Some(DataChannelEvent::OnMessage(message)) => {
                    assert!(!message.is_string);
                    return message.data.to_vec();
                }
                None | Some(DataChannelEvent::OnClose | DataChannelEvent::OnError) => {
                    panic!("Relay channel closed before input")
                }
                _ => {}
            }
        }
    })
    .await
    .expect("Encrypted relay input must arrive")
}
fn only_relay(sdp: &str) {
    let candidates: Vec<_> = sdp
        .lines()
        .filter(|line| line.starts_with("a=candidate:"))
        .collect();
    assert!(!candidates.is_empty());
    assert!(candidates.iter().all(|line| line.contains(" typ relay")));
    assert!(
        !candidates
            .iter()
            .any(|line| line.contains(" typ host") || line.contains(" typ srflx"))
    );
}
#[tokio::test]
async fn real_turn_forwards_authenticated_encrypted_input_and_releases_both_allocations() {
    let (server, config, mut closed) = server().await;
    let mut a = NativeTransport::relay_only("127.0.0.1:0".into(), vec![config.clone()])
        .await
        .unwrap();
    let mut b = NativeTransport::relay_only("127.0.0.1:0".into(), vec![config])
        .await
        .unwrap();
    let outbound = a
        .peer
        .create_data_channel("mixel-relay-control", None)
        .await
        .unwrap();
    let offer = a
        .local_sdp(a.peer.create_offer(None).await.unwrap())
        .await
        .unwrap();
    only_relay(&offer.sdp);
    let af = local_fingerprint(&offer.sdp).unwrap();
    b.peer.set_remote_description(offer).await.unwrap();
    let answer = b
        .local_sdp(b.peer.create_answer(None).await.unwrap())
        .await
        .unwrap();
    only_relay(&answer.sdp);
    let bf = local_fingerprint(&answer.sdp).unwrap();
    a.peer.set_remote_description(answer).await.unwrap();
    let inbound = tokio::time::timeout(Duration::from_secs(10), b.incoming_channels.recv())
        .await
        .unwrap()
        .unwrap();
    tokio::join!(opened(&outbound), opened(&inbound));
    let ak = SigningKey::generate(&mut OsRng);
    let bk = SigningKey::generate(&mut OsRng);
    let mut nonce = [0; 32];
    OsRng.fill_bytes(&mut nonce);
    let context = Context {
        session_id: nonce,
        controller: ak.verifying_key(),
        host: bk.verifying_key(),
    };
    let ah = Handshake::new(context.clone(), Role::Controller, &ak, af).unwrap();
    let bh = Handshake::new(context, Role::Host, &bk, bf).unwrap();
    let ha = ah.hello();
    let hb = bh.hello();
    let mut ac = ah
        .finish(&hb, a.observed_fingerprint().await.unwrap())
        .unwrap();
    let mut bc = bh
        .finish(&ha, b.observed_fingerprint().await.unwrap())
        .unwrap();
    let plain =
        zeroize::Zeroizing::new(input::encode(&Input::Text("Mixel TURN Grüezi 中文 😀")).unwrap());
    let record = ac.seal(&plain).unwrap();
    outbound.send(record.as_slice().into()).await.unwrap();
    let wire = received(&inbound).await;
    assert_eq!(wire, record);
    let text = zeroize::Zeroizing::new(bc.open(&wire).unwrap());
    assert_eq!(
        input::decode(&text).unwrap(),
        Input::Text("Mixel TURN Grüezi 中文 😀")
    );
    assert!(bc.open(&wire).is_err());
    let ack = bc.seal(b"relay-confirmed").unwrap();
    inbound.send(ack.as_slice().into()).await.unwrap();
    assert_eq!(
        ac.open(&received(&outbound).await).unwrap(),
        b"relay-confirmed"
    );
    a.close().await.unwrap();
    b.close().await.unwrap();
    let mut relayed_bytes = 0;
    for _ in 0..2 {
        let info = tokio::time::timeout(Duration::from_secs(3), closed.recv())
            .await
            .expect("close must release actual allocation")
            .unwrap();
        assert!(info.relayed_bytes > 0);
        relayed_bytes += info.relayed_bytes;
    }
    server.close().await.unwrap();
    println!(
        "PASS: relay-only actual TURN/UDP + ICE/DTLS/SCTP; signed identities, encrypted Unicode/replay denial; {relayed_bytes} server-counted relay bytes; both allocations released"
    );
}
#[tokio::test]
async fn invalid_relay_authentication_never_publishes_a_direct_candidate() {
    let (server, mut config, _) = server().await;
    config.credential = "incorrect-qa-credential".into();
    let mut peer = NativeTransport::relay_only("127.0.0.1:0".into(), vec![config])
        .await
        .unwrap();
    peer.peer
        .create_data_channel("mixel-denied-relay", None)
        .await
        .unwrap();
    let result = peer
        .local_sdp(peer.peer.create_offer(None).await.unwrap())
        .await;
    assert!(matches!(
        result,
        Err(TransportError::RelayUnavailable | TransportError::Timeout)
    ));
    peer.close().await.unwrap();
    server.close().await.unwrap();
}
