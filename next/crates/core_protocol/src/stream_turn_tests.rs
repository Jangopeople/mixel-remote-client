use super::*;
use crate::{
    crypto::{Context as CryptoContext, Handshake, Role},
    input::{self, Input},
    media::{H264Assembler, H264VideoSender, TrackRemoteEvent},
    session::Session,
    transport::{NativeTransport, TransportError, local_fingerprint},
};
use ed25519_dalek::SigningKey;
use rand_core::{OsRng, RngCore};
use std::{any::Any, sync::atomic::AtomicUsize};
use tokio::{
    net::TcpListener,
    sync::{Mutex as AsyncMutex, Semaphore},
    task::JoinSet,
};
use tokio_rustls::{
    TlsAcceptor,
    rustls::{ServerConfig, pki_types::PrivatePkcs8KeyDer},
};
use turn::{
    Error as TurnError,
    auth::{AuthHandler, generate_auth_key},
    relay::relay_static::RelayAddressGeneratorStatic,
    server::{
        Server,
        config::{ConnConfig, ServerConfig as TurnConfig},
    },
};
use turn_util::{Conn, Result as TurnResult, vnet::net::Net};
use webrtc::data_channel::{DataChannel, DataChannelEvent};

type Writer = Arc<AsyncMutex<tokio::io::WriteHalf<BoxStream>>>;
type Writers = Arc<AsyncMutex<HashMap<SocketAddr, Writer>>>;
struct StreamListener {
    address: SocketAddr,
    incoming: AsyncMutex<mpsc::Receiver<Received>>,
    writers: Writers,
    peer: Mutex<Option<SocketAddr>>,
    stop: watch::Sender<bool>,
    worker: Mutex<Option<tokio::task::JoinHandle<()>>>,
    accepted: Arc<AtomicUsize>,
    tls_rejected: Arc<AtomicUsize>,
}
impl StreamListener {
    async fn bind(bind: &str, tls: Option<Arc<ServerConfig>>) -> Arc<Self> {
        let listener = TcpListener::bind(bind).await.unwrap();
        let address = listener.local_addr().unwrap();
        let writers: Writers = Default::default();
        let (incoming, rx) = mpsc::channel(64);
        let (stop, mut stopped) = watch::channel(false);
        let accepted = Arc::new(AtomicUsize::new(0));
        let rejected = Arc::new(AtomicUsize::new(0));
        let (owned_writers, owned_accepted, owned_rejected) =
            (writers.clone(), accepted.clone(), rejected.clone());
        let worker = tokio::spawn(async move {
            let limit = Arc::new(Semaphore::new(32));
            let mut tasks = JoinSet::new();
            loop {
                tokio::select! {
                    _=stopped.changed()=>break,
                    Some(_)=tasks.join_next(),if !tasks.is_empty()=>{},
                    result=listener.accept()=>{
                        let (socket,source)=match result {Ok(value)=>value,Err(_)=>break};
                        let Ok(permit)=limit.clone().try_acquire_owned() else {continue;};
                        owned_accepted.fetch_add(1,Ordering::Relaxed);
                        let tls=tls.clone();let rejected=owned_rejected.clone();let writers=owned_writers.clone();let incoming=incoming.clone();
                        tasks.spawn(async move {
                            let _permit=permit;
                            let stream:BoxStream=if let Some(config)=tls {
                                match tokio::time::timeout(Duration::from_secs(3),TlsAcceptor::from(config).accept(socket)).await {
                                    Ok(Ok(stream))=>Box::new(stream),_=>{rejected.fetch_add(1,Ordering::Relaxed);return;},
                                }
                            } else {Box::new(socket)};
                            let (mut reader,writer)=tokio::io::split(stream);
                            writers.lock().await.insert(source,Arc::new(AsyncMutex::new(writer)));
                            while let Ok(packet)=turn_framing::read_packet(&mut reader).await {
                                if incoming.send(Received {packet,source}).await.is_err() {break;}
                            }
                            writers.lock().await.remove(&source);
                        });
                    }
                }
            }
            tasks.abort_all();
            while tasks.join_next().await.is_some() {}
            owned_writers.lock().await.clear();
        });
        Arc::new(Self {
            address,
            incoming: AsyncMutex::new(rx),
            writers,
            peer: Mutex::new(None),
            stop,
            worker: Mutex::new(Some(worker)),
            accepted,
            tls_rejected: rejected,
        })
    }
}
#[async_trait::async_trait]
impl Conn for StreamListener {
    async fn connect(&self, peer: SocketAddr) -> TurnResult<()> {
        *self
            .peer
            .lock()
            .map_err(|_| io::Error::other("Fixture state failed"))? = Some(peer);
        Ok(())
    }
    async fn recv(&self, buf: &mut [u8]) -> TurnResult<usize> {
        Ok(self.recv_from(buf).await?.0)
    }
    async fn recv_from(&self, buf: &mut [u8]) -> TurnResult<(usize, SocketAddr)> {
        let received = self.incoming.lock().await.recv().await.ok_or_else(|| {
            io::Error::new(io::ErrorKind::UnexpectedEof, "Fixture listener closed")
        })?;
        if received.packet.len() > buf.len() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Fixture receive buffer too small",
            )
            .into());
        }
        buf[..received.packet.len()].copy_from_slice(&received.packet);
        Ok((received.packet.len(), received.source))
    }
    async fn send(&self, buf: &[u8]) -> TurnResult<usize> {
        let peer = self
            .remote_addr()
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotConnected, "Fixture peer is unset"))?;
        self.send_to(buf, peer).await
    }
    async fn send_to(&self, buf: &[u8], peer: SocketAddr) -> TurnResult<usize> {
        let writer = self
            .writers
            .lock()
            .await
            .get(&peer)
            .cloned()
            .ok_or_else(|| {
                io::Error::new(io::ErrorKind::NotConnected, "Fixture stream is closed")
            })?;
        let mut writer = writer.lock().await;
        tokio::time::timeout(
            Duration::from_secs(3),
            turn_framing::write_packet(&mut *writer, buf),
        )
        .await
        .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "Fixture stream stalled"))??;
        Ok(buf.len())
    }
    fn local_addr(&self) -> TurnResult<SocketAddr> {
        Ok(self.address)
    }
    fn remote_addr(&self) -> Option<SocketAddr> {
        self.peer.lock().ok().and_then(|peer| *peer)
    }
    async fn close(&self) -> TurnResult<()> {
        self.stop.send_replace(true);
        let worker = {
            self.worker
                .lock()
                .map_err(|_| io::Error::other("Fixture worker state failed"))?
                .take()
        };
        if let Some(worker) = worker {
            worker.await.map_err(io::Error::other)?;
        }
        self.writers.lock().await.clear();
        Ok(())
    }
    fn as_any(&self) -> &(dyn Any + Send + Sync) {
        self
    }
}
struct Auth(Vec<u8>);
impl AuthHandler for Auth {
    fn auth_handle(
        &self,
        username: &str,
        realm: &str,
        source: SocketAddr,
    ) -> Result<Vec<u8>, TurnError> {
        if username == "mixel-stream" && realm == "mixel-stream-qa" && source.ip().is_loopback() {
            Ok(self.0.clone())
        } else {
            Err(TurnError::ErrNoPermission)
        }
    }
}
fn tls_configs(name: &str, trusted: bool, expired: bool) -> (Arc<ServerConfig>, Arc<ClientConfig>) {
    let mut params = rcgen::CertificateParams::new(vec![name.to_owned()]).unwrap();
    if expired {
        params.not_before = rcgen::date_time_ymd(2000, 1, 1);
        params.not_after = rcgen::date_time_ymd(2001, 1, 1);
    }
    let key = rcgen::KeyPair::generate().unwrap();
    let certificate = params.self_signed(&key).unwrap();
    let mut roots = RootCertStore::empty();
    if trusted {
        roots.add(certificate.der().clone()).unwrap();
    } else {
        let other = rcgen::generate_simple_self_signed(vec!["other.invalid".into()]).unwrap();
        roots.add(other.cert.der().clone()).unwrap();
    }
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let server = ServerConfig::builder_with_provider(provider.clone())
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(
            vec![certificate.der().clone()],
            PrivatePkcs8KeyDer::from(key.serialize_der()).into(),
        )
        .unwrap();
    let client = ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_root_certificates(roots)
        .with_no_client_auth();
    (Arc::new(server), Arc::new(client))
}
struct Fixture {
    server: Server,
    listener: Arc<StreamListener>,
    config: RTCIceServer,
    trust: Option<Arc<ClientConfig>>,
    closed: mpsc::Receiver<turn::allocation::AllocationInfo>,
}
impl Fixture {
    async fn new(tls: bool, name: &str, trusted: bool, expired: bool) -> Self {
        Self::new_on("127.0.0.1:0", tls, name, trusted, expired).await
    }
    async fn new_on(bind: &str, tls: bool, name: &str, trusted: bool, expired: bool) -> Self {
        let (server_tls, trust) = if tls {
            let (server, client) = tls_configs(name, trusted, expired);
            (Some(server), Some(client))
        } else {
            (None, None)
        };
        let listener = StreamListener::bind(bind, server_tls).await;
        let password = format!("{:016x}{:016x}", OsRng.next_u64(), OsRng.next_u64());
        let (tx, closed) = mpsc::channel(8);
        let server = Server::new(TurnConfig {
            conn_configs: vec![ConnConfig {
                conn: listener.clone(),
                relay_addr_generator: Box::new(RelayAddressGeneratorStatic {
                    relay_address: IpAddr::V4(Ipv4Addr::LOCALHOST),
                    address: "127.0.0.1".into(),
                    net: Arc::new(Net::new(None)),
                }),
            }],
            realm: "mixel-stream-qa".into(),
            auth_handler: Arc::new(Auth(generate_auth_key(
                "mixel-stream",
                "mixel-stream-qa",
                &password,
            ))),
            channel_bind_timeout: Duration::from_secs(60),
            alloc_close_notify: Some(tx),
        })
        .await
        .unwrap();
        let config = RTCIceServer {
            urls: vec![format!(
                "{}:{}:{}?transport=tcp",
                if tls { "turns" } else { "turn" },
                if listener.address.is_ipv6() {
                    "[::1]"
                } else {
                    "localhost"
                },
                listener.address.port()
            )],
            username: "mixel-stream".into(),
            credential: password,
        };
        Self {
            server,
            listener,
            config,
            trust,
            closed,
        }
    }
    async fn close(&self) {
        self.server.close().await.unwrap();
        self.listener.close().await.unwrap();
    }
}
async fn opened(channel: &Arc<dyn DataChannel>) {
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            match channel.poll().await {
                Some(DataChannelEvent::OnOpen) => break,
                None | Some(DataChannelEvent::OnClose | DataChannelEvent::OnError) => {
                    panic!("Stream-relay channel closed")
                }
                _ => {}
            }
        }
    })
    .await
    .unwrap();
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
                    panic!("Stream-relay channel closed")
                }
                _ => {}
            }
        }
    })
    .await
    .unwrap()
}
async fn exchange(tls: bool, unresponsive_udp: bool, messages: usize) {
    exchange_on("127.0.0.1:0", tls, unresponsive_udp, messages).await;
}
async fn exchange_on(bind: &str, tls: bool, unresponsive_udp: bool, messages: usize) {
    exchange_endpoints(bind, "127.0.0.1:0", tls, unresponsive_udp, messages).await;
}
async fn exchange_endpoints(
    bind: &str,
    server_bind: &str,
    tls: bool,
    unresponsive_udp: bool,
    messages: usize,
) {
    let name = if server_bind.starts_with('[') {
        "::1"
    } else {
        "localhost"
    };
    let mut fixture = Fixture::new_on(server_bind, tls, name, true, false).await;
    let mut configs = vec![fixture.config.clone()];
    let trap = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
    if unresponsive_udp {
        configs.insert(
            0,
            RTCIceServer {
                urls: vec![format!("turn:{}?transport=udp", trap.local_addr().unwrap())],
                username: "unreachable-fixture".into(),
                credential: "unused-fixture-credential".into(),
            },
        );
    }
    let mut a =
        NativeTransport::configured_tls(bind.into(), configs.clone(), true, fixture.trust.clone())
            .await
            .unwrap();
    let mut b = NativeTransport::configured_tls(bind.into(), configs, true, fixture.trust.clone())
        .await
        .unwrap();
    let video_sender = H264VideoSender::new(&a, 640, 420, 30).await.unwrap();
    let outbound = a
        .peer
        .create_data_channel("mixel-stream-relay", None)
        .await
        .unwrap();
    let offer = a
        .local_sdp(a.peer.create_offer(None).await.unwrap())
        .await
        .unwrap();
    assert!(offer.sdp.contains(" typ relay"));
    assert!(!offer.sdp.contains(" typ host"));
    let af = local_fingerprint(&offer.sdp).unwrap();
    b.peer.set_remote_description(offer).await.unwrap();
    let answer = b
        .local_sdp(b.peer.create_answer(None).await.unwrap())
        .await
        .unwrap();
    assert!(answer.sdp.contains(" typ relay"));
    assert!(!answer.sdp.contains(" typ host"));
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
    let context = CryptoContext {
        session_id: nonce,
        controller: bk.verifying_key(),
        host: ak.verifying_key(),
    };
    let ah = Handshake::new(context.clone(), Role::Host, &ak, af).unwrap();
    let bh = Handshake::new(context.clone(), Role::Controller, &bk, bf).unwrap();
    let ha = ah.hello();
    let hb = bh.hello();
    let mut ac = ah
        .finish(&hb, a.observed_fingerprint().await.unwrap())
        .unwrap();
    let mut bc = bh
        .finish(&ha, b.observed_fingerprint().await.unwrap())
        .unwrap();
    let now = Instant::now();
    let mut consent = Session::for_peer(now, context, Role::Host, false);
    let fixture_video = include_bytes!("../tests/fixtures/generated-colours-640x420.h264");
    assert!(
        video_sender
            .send(&consent, fixture_video, Duration::from_micros(33333))
            .await
            .is_err()
    );
    consent.decide(true, now).unwrap();
    consent
        .authenticated_transport_ready(&ac.verified_session(), Instant::now())
        .unwrap();
    video_sender
        .send(&consent, fixture_video, Duration::from_micros(33333))
        .await
        .unwrap();
    let track = tokio::time::timeout(Duration::from_secs(5), b.incoming_tracks.recv())
        .await
        .unwrap()
        .unwrap();
    let reassembled = tokio::time::timeout(Duration::from_secs(5), async {
        let mut assembler = H264Assembler::default();
        while let Some(event) = track.poll().await {
            if let TrackRemoteEvent::OnRtpPacket(packet) = event {
                eprintln!(
                    "relay RTP seq={} timestamp={} marker={} NAL={} bytes={}",
                    packet.header.sequence_number,
                    packet.header.timestamp,
                    packet.header.marker,
                    packet.payload.first().copied().unwrap_or(0) & 31,
                    packet.payload.len()
                );
                if let Some(frame) = assembler
                    .push(
                        packet.header.sequence_number,
                        packet.header.timestamp,
                        packet.header.marker,
                        &packet.payload,
                    )
                    .unwrap()
                {
                    return frame;
                }
            }
        }
        panic!("SRTP media ended without a complete frame")
    })
    .await
    .unwrap();
    let nal_types = |data: &[u8]| {
        data.windows(5)
            .filter(|w| w[..4] == [0, 0, 0, 1])
            .map(|w| w[4] & 31)
            .collect::<Vec<_>>()
    };
    assert!(
        reassembled.as_slice() == fixture_video.as_slice(),
        "H264 access unit mismatch: received NALs {:?}, expected {:?}, received {} bytes, expected {}",
        nal_types(&reassembled),
        nal_types(fixture_video),
        reassembled.len(),
        fixture_video.len()
    );
    let receiving = inbound.clone();
    let host = tokio::spawn(async move {
        let mut last = Vec::new();
        for index in 0..messages {
            let wire = received(&receiving).await;
            let plain = zeroize::Zeroizing::new(bc.open(&wire).unwrap());
            let expected = format!("Mixel stream {index} Grüezi 中文 😀 {}", "x".repeat(1700));
            assert_eq!(input::decode(&plain).unwrap(), Input::Text(&expected));
            last = wire;
        }
        assert!(bc.open(&last).is_err());
        let ack = bc.seal(b"stream-relay-confirmed").unwrap();
        receiving.send(ack.as_slice().into()).await.unwrap();
    });
    for index in 0..messages {
        let text = zeroize::Zeroizing::new(format!(
            "Mixel stream {index} Grüezi 中文 😀 {}",
            "x".repeat(1700)
        ));
        let plain = zeroize::Zeroizing::new(input::encode(&Input::Text(&text)).unwrap());
        let record = ac.seal(&plain).unwrap();
        outbound.send(record.as_slice().into()).await.unwrap();
    }
    tokio::time::timeout(Duration::from_secs(20), host)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        ac.open(&received(&outbound).await).unwrap(),
        b"stream-relay-confirmed"
    );
    if !unresponsive_udp {
        assert_eq!(a.stream_udp_writes(), Some(0));
        assert_eq!(b.stream_udp_writes(), Some(0));
    }
    a.close().await.unwrap();
    b.close().await.unwrap();
    let mut bytes = 0;
    for _ in 0..2 {
        let info = tokio::time::timeout(Duration::from_secs(3), fixture.closed.recv())
            .await
            .expect("Stream close must release allocation")
            .unwrap();
        assert!(info.relayed_bytes > 0);
        bytes += info.relayed_bytes;
    }
    assert!(fixture.listener.accepted.load(Ordering::Relaxed) >= 2);
    fixture.close().await;
    println!(
        "PASS: actual {} TURN, relay-only ICE/DTLS/SCTP, verified identities, consent-gated byte-exact H264 SRTP, encrypted Unicode/replay denial; {bytes} relayed bytes; both allocations released; UDP-unresponsive={unresponsive_udp}",
        if tls { "TLS" } else { "TCP" }
    );
}
#[tokio::test]
async fn actual_tcp_turn_forwards_encrypted_session_without_udp_downgrade() {
    exchange(false, false, 1).await;
}
#[tokio::test]
async fn loopback_stream_only_transport_never_sends_to_an_advertised_udp_relay() {
    let fixture = Fixture::new(false, "localhost", true, false).await;
    let udp_relay = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
    let mut mixed = fixture.config.clone();
    mixed.urls.insert(
        0,
        format!("turn:{}?transport=udp", udp_relay.local_addr().unwrap()),
    );
    let mut peer = NativeTransport::relay_stream_only(vec![mixed])
        .await
        .unwrap();
    peer.peer
        .create_data_channel("stream-only", None)
        .await
        .unwrap();
    let offer = peer.peer.create_offer(None).await.unwrap();
    assert!(
        peer.local_sdp(offer)
            .await
            .unwrap()
            .sdp
            .contains(" typ relay ")
    );
    assert!(fixture.listener.accepted.load(Ordering::Relaxed) > 0);
    assert_eq!(peer.stream_udp_writes(), Some(0));
    let mut packet = [0; 2048];
    assert_eq!(
        udp_relay.try_recv_from(&mut packet).unwrap_err().kind(),
        io::ErrorKind::WouldBlock
    );
    peer.close().await.unwrap();
    fixture.close().await;
}

#[tokio::test]
async fn actual_tls_turn_verifies_certificate_and_forwards_encrypted_session() {
    exchange(true, false, 1).await;
}
#[tokio::test]
async fn tls_turn_remains_usable_when_another_configured_udp_relay_never_answers() {
    exchange(true, true, 1).await;
}
#[tokio::test]
async fn tls_rejects_untrusted_wrong_name_and_expired_certificates_without_udp_fallback() {
    for (name, trusted, expired, custom_roots) in [
        ("localhost", false, false, true),
        ("wrong.invalid", true, false, true),
        ("localhost", true, true, true),
        ("localhost", true, false, false),
    ] {
        let fixture = Fixture::new(true, name, trusted, expired).await;
        let mut peer = NativeTransport::configured_tls(
            "127.0.0.1:0".into(),
            vec![fixture.config.clone()],
            true,
            if custom_roots {
                fixture.trust.clone()
            } else {
                None
            },
        )
        .await
        .unwrap();
        peer.peer
            .create_data_channel("mixel-denied-tls", None)
            .await
            .unwrap();
        let result = peer
            .local_sdp(peer.peer.create_offer(None).await.unwrap())
            .await;
        assert!(matches!(
            result,
            Err(TransportError::RelayUnavailable | TransportError::Timeout)
        ));
        assert_eq!(peer.stream_udp_writes(), Some(0));
        assert!(fixture.listener.tls_rejected.load(Ordering::Relaxed) > 0);
        peer.close().await.unwrap();
        fixture.close().await;
    }
}

#[tokio::test]
async fn tls_turn_preserves_order_and_encryption_across_a_large_fragmented_burst() {
    exchange(true, false, 256).await;
}
#[test]
fn output_queue_has_backpressure_and_failure_is_not_a_udp_send() {
    let (stop, _) = watch::channel(false);
    let (finished, _) = watch::channel(false);
    let pipe = Pipe {
        route: Route {
            alias: "192.0.2.1:40000".parse().unwrap(),
            endpoint: "127.0.0.1:1".into(),
            name: "localhost".into(),
            tls: None,
        },
        out: Mutex::new(Outbox {
            packets: VecDeque::new(),
            error: None,
            closed: false,
        }),
        ready: Notify::new(),
        writable: AtomicWaker::new(),
        stop,
        finished,
    };
    let mut packet = vec![0; 20];
    packet[1] = 1;
    packet[4..8].copy_from_slice(&[0x21, 0x12, 0xa4, 0x42]);
    let mut context = std::task::Context::from_waker(futures_util::task::noop_waker_ref());
    for _ in 0..OUT_PACKETS {
        assert!(matches!(
            pipe.enqueue(&mut context, &packet),
            Poll::Ready(Ok(20))
        ));
    }
    assert!(pipe.enqueue(&mut context, &packet).is_pending());
    assert!(pipe.pop().unwrap().is_some());
    assert!(matches!(
        pipe.enqueue(&mut context, &packet),
        Poll::Ready(Ok(20))
    ));
    pipe.finish(Some("TLS certificate rejected".into()));
    assert!(matches!(
        pipe.enqueue(&mut context, &packet),
        Poll::Ready(Err(_))
    ));
    assert!(pipe.out.lock().unwrap().packets.is_empty());
}

#[tokio::test]
async fn tls_turn_can_relay_ipv4_media_from_an_ipv6_local_socket() {
    exchange_on("[::1]:0", true, false, 1).await;
}

#[tokio::test]
async fn tls_turn_accepts_a_verified_ipv6_literal_server_name() {
    exchange_endpoints("127.0.0.1:0", "[::1]:0", true, false, 1).await;
}
