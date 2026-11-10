//! Real coturn interoperability. Missing fixture configuration is a failure,
//! never a skipped/pass test. The fixture is restricted to local loopback.
use crate::{
    crypto::{Context, Handshake, Role},
    input::{self, Input},
    media::{H264Assembler, H264VideoSender, TrackRemoteEvent},
    session::Session,
    transport::{
        NativeDataChannel, NativeDataChannelEvent, NativeIceServer, NativeTransport,
        local_fingerprint,
    },
};
use ed25519_dalek::SigningKey;
use rand_core::{OsRng, RngCore};
use serde::Deserialize;
use std::{
    path::PathBuf,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio_rustls::rustls::{self, RootCertStore, pki_types::CertificateDer};
use zeroize::Zeroizing;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Fixture {
    port: u16,
    tls_port: u16,
    username: String,
    password: String,
    certificate: String,
}
fn config() -> Fixture {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../_scratch")
        .canonicalize()
        .unwrap();
    let path = PathBuf::from(
        std::env::var("MIXEL_LOCAL_COTURN_FIXTURE")
            .expect("Run the owned local coturn fixture script"),
    )
    .canonicalize()
    .unwrap();
    assert!(path.starts_with(&root));
    assert!(std::fs::metadata(&path).unwrap().len() <= 8192);
    let data = Zeroizing::new(std::fs::read(path).unwrap());
    let value: Fixture = serde_json::from_slice(&data).unwrap();
    assert!(
        value.port > 0
            && value.tls_port > 0
            && value.username.len() <= 256
            && value.password.len() <= 1024
    );
    let certificate = PathBuf::from(&value.certificate).canonicalize().unwrap();
    assert!(certificate.starts_with(root));
    value
}
async fn opened(channel: &Arc<dyn NativeDataChannel>) {
    tokio::time::timeout(Duration::from_secs(15), async {
        loop {
            match channel.poll().await {
                Some(NativeDataChannelEvent::OnOpen) => return,
                None | Some(NativeDataChannelEvent::OnClose | NativeDataChannelEvent::OnError) => {
                    panic!("Coturn channel failed before opening")
                }
                _ => {}
            }
        }
    })
    .await
    .expect("Coturn DTLS/SCTP opening timed out");
}
async fn received(channel: &Arc<dyn NativeDataChannel>) -> Vec<u8> {
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            match channel.poll().await {
                Some(NativeDataChannelEvent::OnMessage(message)) => {
                    assert!(!message.is_string);
                    return message.data.to_vec();
                }
                None | Some(NativeDataChannelEvent::OnClose | NativeDataChannelEvent::OnError) => {
                    panic!("Coturn channel closed before delivery")
                }
                _ => {}
            }
        }
    })
    .await
    .expect("Coturn delivery timed out")
}
fn relay_only(sdp: &str) {
    let candidates: Vec<_> = sdp
        .lines()
        .filter(|line| line.starts_with("a=candidate:"))
        .collect();
    assert!(!candidates.is_empty());
    assert!(candidates.iter().all(|line| line.contains(" typ relay")));
    assert!(!sdp.lines().any(|line| line.starts_with("a=candidate:")
        && (line.contains(" typ host") || line.contains(" typ srflx"))));
}
async fn exchange(kind: &str) {
    let fixture = config();
    let tls = kind == "tls";
    let mut roots = RootCertStore::empty();
    roots
        .add(CertificateDer::from(
            std::fs::read(&fixture.certificate).unwrap(),
        ))
        .unwrap();
    let trust = Arc::new(
        rustls::ClientConfig::builder_with_provider(Arc::new(
            rustls::crypto::ring::default_provider(),
        ))
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_root_certificates(roots)
        .with_no_client_auth(),
    );
    let server = NativeIceServer {
        urls: vec![if tls {
            format!("turns:127.0.0.1:{}?transport=tcp", fixture.tls_port)
        } else {
            format!("turn:127.0.0.1:{}?transport={kind}", fixture.port)
        }],
        username: fixture.username,
        credential: fixture.password,
    };
    let mut controller = NativeTransport::configured_tls(
        "127.0.0.1:0".into(),
        vec![server.clone()],
        true,
        if tls { Some(trust.clone()) } else { None },
    )
    .await
    .unwrap();
    let mut host = NativeTransport::configured_tls(
        "127.0.0.1:0".into(),
        vec![server],
        true,
        if tls { Some(trust) } else { None },
    )
    .await
    .unwrap();
    controller.receive_video().await.unwrap();
    let video = H264VideoSender::new(&host, 640, 420, 30).await.unwrap();
    let outbound = controller
        .peer
        .create_data_channel("mixel-control-v1", None)
        .await
        .unwrap();
    let offer = controller
        .local_sdp(controller.peer.create_offer(None).await.unwrap())
        .await
        .expect("Coturn must gather the controller's relay candidate");
    relay_only(&offer.sdp);
    let cf = local_fingerprint(&offer.sdp).unwrap();
    host.peer.set_remote_description(offer).await.unwrap();
    let answer = host
        .local_sdp(host.peer.create_answer(None).await.unwrap())
        .await
        .expect("Coturn must gather the host's relay candidate");
    relay_only(&answer.sdp);
    let hf = local_fingerprint(&answer.sdp).unwrap();
    controller
        .peer
        .set_remote_description(answer)
        .await
        .unwrap();
    let inbound = tokio::time::timeout(Duration::from_secs(15), host.incoming_channels.recv())
        .await
        .unwrap()
        .unwrap();
    tokio::join!(opened(&outbound), opened(&inbound));
    let ck = SigningKey::generate(&mut OsRng);
    let hk = SigningKey::generate(&mut OsRng);
    let mut nonce = [0; 32];
    OsRng.fill_bytes(&mut nonce);
    let context = Context {
        session_id: nonce,
        controller: ck.verifying_key(),
        host: hk.verifying_key(),
    };
    let ch = Handshake::new(context.clone(), Role::Controller, &ck, cf).unwrap();
    let hh = Handshake::new(context.clone(), Role::Host, &hk, hf).unwrap();
    let chello = ch.hello();
    let hhello = hh.hello();
    let mut cc = ch
        .finish(&hhello, controller.observed_fingerprint().await.unwrap())
        .unwrap();
    let mut hc = hh
        .finish(&chello, host.observed_fingerprint().await.unwrap())
        .unwrap();
    let mut consent = Session::for_peer(Instant::now(), context, Role::Host, false);
    let sample = include_bytes!("../tests/fixtures/generated-colours-640x420.h264");
    assert!(
        video
            .send(&consent, sample, Duration::from_micros(33333))
            .await
            .is_err()
    );
    consent.decide(true, Instant::now()).unwrap();
    consent
        .authenticated_transport_ready(&hc.verified_session(), Instant::now())
        .unwrap();
    video
        .send(&consent, sample, Duration::from_micros(33333))
        .await
        .unwrap();
    let track = tokio::time::timeout(Duration::from_secs(5), controller.incoming_tracks.recv())
        .await
        .unwrap()
        .unwrap();
    let assembled = tokio::time::timeout(Duration::from_secs(5), async {
        let mut assembler = H264Assembler::default();
        while let Some(event) = track.poll().await {
            if let TrackRemoteEvent::OnRtpPacket(packet) = event
                && let Some(bytes) = assembler
                    .push(
                        packet.header.sequence_number,
                        packet.header.timestamp,
                        packet.header.marker,
                        &packet.payload,
                    )
                    .unwrap()
            {
                return bytes;
            }
        }
        panic!("Coturn video track ended");
    })
    .await
    .unwrap();
    assert_eq!(assembled.as_slice(), sample);
    let input = Zeroizing::new(input::encode(&Input::Text("Mixel coturn Grüezi 中文 😀")).unwrap());
    let record = cc.seal(&input).unwrap();
    outbound.send(record.as_slice().into()).await.unwrap();
    let wire = received(&inbound).await;
    let plain = Zeroizing::new(hc.open(&wire).unwrap());
    assert_eq!(
        input::decode(&plain).unwrap(),
        Input::Text("Mixel coturn Grüezi 中文 😀")
    );
    assert!(hc.open(&wire).is_err());
    let ack = hc.seal(b"coturn-delivery-confirmed").unwrap();
    inbound.send(ack.as_slice().into()).await.unwrap();
    assert_eq!(
        cc.open(&received(&outbound).await).unwrap(),
        b"coturn-delivery-confirmed"
    );
    // Independently encrypted file traffic uses a second real reliable channel.
    let bulk_out = controller
        .peer
        .create_data_channel("mixel-files-v1", None)
        .await
        .unwrap();
    let bulk_in = tokio::time::timeout(Duration::from_secs(5), host.incoming_channels.recv())
        .await
        .unwrap()
        .unwrap();
    tokio::join!(opened(&bulk_out), opened(&bulk_in));
    let mut cb = cc.take_bulk_channel().unwrap();
    let mut hb = hc.take_bulk_channel().unwrap();
    let bytes = Zeroizing::new(vec![47; 3000]);
    let message = crate::files::Message::Data {
        id: [9; 16],
        offset: 0,
        bytes: &bytes,
    }
    .encode()
    .unwrap();
    let encrypted = cb.seal(&message).unwrap();
    bulk_out.send(encrypted.as_slice().into()).await.unwrap();
    let received = received(&bulk_in).await;
    let decrypted = Zeroizing::new(hb.open(&received).unwrap());
    assert_eq!(decrypted.as_slice(), message.as_slice());
    assert!(hc.open(&received).is_err());
    if kind != "udp" {
        assert_eq!(controller.stream_udp_writes(), Some(0));
        assert_eq!(host.stream_udp_writes(), Some(0));
    }
    host.close().await.unwrap();
    controller.close().await.unwrap();
    println!(
        "PASS: real coturn {kind}; relay-only ICE; actual pinned DTLS; consent-gated byte-exact H264/SRTP; encrypted Unicode/ACK/replay denial; independent file DataChannel"
    );
}
#[tokio::test]
async fn actual_coturn_udp() {
    exchange("udp").await;
}
#[tokio::test]
async fn actual_coturn_tcp() {
    exchange("tcp").await;
}
#[tokio::test]
async fn actual_coturn_tls_with_explicit_fixture_ca() {
    exchange("tls").await;
}
