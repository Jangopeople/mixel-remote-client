//! Native pure-Rust WebRTC transport with bounded application event delivery.
//! SDP/ICE must travel through the authenticated, consent-gated signaling path.
//! An open WebRTC transport alone does not authorize OS control.
use sha2::{Digest, Sha256};
use std::{sync::Arc, time::Duration};
use tokio::sync::{mpsc, watch};
use webrtc::{
    data_channel::DataChannel,
    media_stream::track_remote::TrackRemote,
    peer_connection::{
        MediaEngine, PeerConnection, PeerConnectionBuilder, PeerConnectionEventHandler,
        RTCConfigurationBuilder, RTCIceCandidateInit, RTCIceGatheringState, RTCIceServer,
        RTCIceTransportPolicy, RTCPeerConnectionIceEvent, RTCPeerConnectionState,
        RTCSessionDescription, Registry,
    },
};

/// A video track received from the peer.
pub type RemoteTrack = Arc<dyn TrackRemote>;

/// How often the technician's side asks again for one lost packet (the stack's default is without
/// limit: on a link that had just dropped a burst it asked for the same packets every 100 ms).
const NACKS_PER_PACKET: u16 = 3;
/// Packets the host keeps to send again on request. 1024 was about 0.1 s at LAN pace, less than one
/// Mac keyframe (about 850 packets), so a request often came too late to be served and the
/// technician had to ask for a whole new picture. 8192 (about 10 MB) is about a second at LAN pace
/// and several seconds on a slower line.
const REPEAT_BUFFER: u16 = 8192;
/// Packets the technician's side tracks for gaps (the stack's default of 512 lost sight of a gap
/// within one keyframe burst at LAN pace, so it was never asked for).
const RECEIVE_LOG: u16 = 8192;
/// How often the technician's side looks for gaps and asks for them (the stack's default is
/// 100 ms; with 50 ms a lost packet comes back about 50 ms sooner).
const NACK_EVERY: std::time::Duration = std::time::Duration::from_millis(50);

/// The media stack's default set of helpers for every stream, with a lost picture packet sent
/// again when the other side asks (NACK) and asked for at most [`NACKS_PER_PACKET`] times.
///
/// History. Until 2.0.16 the host did not answer these requests: the technician's side took a gap
/// for a damaged picture at once and asked for a complete one, so a packet sent again was never
/// used; and before pacing (14 Nov 2026) every burst lost on a slow link was asked for again and
/// again and sent again unpaced (2 Mbit/s: 460 kB of pictures and 20 MB of repeats in 12 s).
/// Since 2.0.16 the technician's side waits a moment for the missing packet (`PacketReorder`),
/// pictures leave paced, and requests are bounded, so a repeat now saves the picture: on a line
/// with 1 % loss the technician got about one picture a second without it (lab, 17 Nov 2026).
/// Older technicians still ask without limit; each request stops once the packet has arrived.
///
/// Everything else is what the stack's `register_default_interceptors` sets up: the arrival
/// feedback and the reports. (The stack's `NoopInterceptor` is no stand-in for a left-out part:
/// despite its name it ends the inbound control traffic for everything behind it.)
fn interceptors(media_engine: &mut MediaEngine) -> Result<Registry, webrtc::error::Error> {
    use rtc::interceptor::{FlexFec03ReceiveBuilder, FlexFec03SendBuilder, NackGeneratorBuilder, NackResponderBuilder, Slot};
    use rtc::peer_connection::configuration::interceptor_registry::{
        configure_rtcp_reports, configure_simulcast_extension_headers, configure_twcc_receiver_only,
    };
    // Lab builds only: the stack's default set (with repeats), to measure against.
    #[cfg(feature = "qa-session")]
    if std::env::var_os("MIXEL_QA_UNPACED").is_some() || std::env::var_os("MIXEL_QA_REPEATS").is_some() {
        return webrtc::peer_connection::register_default_interceptors(Registry::new(), media_engine);
    }
    configure_simulcast_extension_headers(media_engine)?;
    register_repair_codec(media_engine)?;
    let (media, repair) = repair_block();
    let registry = configure_twcc_receiver_only(Registry::new(), media_engine)?;
    Ok(configure_rtcp_reports(registry)
        .with(Slot::NackGenerator, NackGeneratorBuilder::new().with_size(RECEIVE_LOG).with_interval(NACK_EVERY).with_max_nacks_per_packet(NACKS_PER_PACKET).build())
        .with(Slot::NackResponder, NackResponderBuilder::new().with_size(REPEAT_BUFFER).build())
        .with(Slot::FecEncoder, FlexFec03SendBuilder::new().with_num_media_packets(media).with_num_fec_packets(repair).build())
        .with(Slot::FecDecoder, FlexFec03ReceiveBuilder::new().build()))
}

/// Payload type of the picture repair stream (FlexFEC-03). Not used by any codec the stack
/// registers by default.
pub const REPAIR_PAYLOAD_TYPE: u8 = 49;
/// Picture packets protected together, and repair packets sent for each such block.
///
/// Why. A lost picture packet used to be fixed only by asking for it again (NACK): that costs a
/// full round trip, and the technician's side has to hold every later picture until it is back
/// (they depend on it). On the line to Mauritius (about 210 ms) one lost packet in a hundred kept
/// the picture waiting most of the time: in the lab (210 ms, 20 Mbit/s, 1 % loss) the picture was
/// 327 ms late instead of 167 ms and stood still for 13 s of every 45 s. A repair packet after
/// every 8 picture packets rebuilds one lost packet of the block on the spot, without the round
/// trip; the request for it again stays for what the repair cannot cover (two losses in a block).
/// It costs one packet in nine (11 %).
///
/// Apps without it (released before 2.2.13) do not announce the repair stream in their session
/// description, so nothing changes for them: the host only sends repair packets to a technician
/// whose description has them, and an older host simply sends none.
const REPAIR_BLOCK: (u32, u32) = (8, 1);
/// The share of what leaves that is pictures while repair packets go with them: the technician
/// reports picture bytes only, so the host keeps this much of the line for pictures.
pub fn picture_share() -> f64 {
    let (media, repair) = repair_block();
    f64::from(media) / f64::from(media + repair)
}
fn repair_block() -> (u32, u32) {
    #[cfg(feature = "qa-session")]
    if let Some((media, repair)) = std::env::var("MIXEL_QA_FEC").ok().and_then(|value| {
        let (media, repair) = value.split_once(':')?;
        Some((media.parse().ok()?, repair.parse().ok()?))
    }) {
        return (media, repair);
    }
    REPAIR_BLOCK
}
/// The repair stream is a codec of its own in the session description; both sides must list it.
fn register_repair_codec(media_engine: &mut MediaEngine) -> Result<(), webrtc::error::Error> {
    use rtc::rtp_transceiver::rtp_sender::{RTCRtpCodec, RTCRtpCodecParameters, RtpCodecKind};
    media_engine.register_codec(
        RTCRtpCodecParameters {
            rtp_codec: RTCRtpCodec {
                mime_type: rtc::peer_connection::configuration::media_engine::MIME_TYPE_FLEX_FEC03.to_owned(),
                clock_rate: 90000,
                channels: 0,
                sdp_fmtp_line: "repair-window=10000000".to_owned(),
                rtcp_feedback: vec![],
            },
            payload_type: REPAIR_PAYLOAD_TYPE,
            ..Default::default()
        },
        RtpCodecKind::Video,
    )?;
    Ok(())
}

#[derive(Debug, thiserror::Error)]
pub enum TransportError {
    #[error("WebRTC: {0}")]
    WebRtc(#[from] webrtc::error::Error),
    #[error("WebRTC gathering timed out or transport ended")]
    Timeout,
    #[error("missing or invalid DTLS certificate fingerprint")]
    Fingerprint,
    #[error("invalid or unavailable relay configuration")]
    Configuration,
    #[error("configured relay transport is unsupported")]
    UnsupportedRelay,
    #[error("TURN stream: {0}")]
    Stream(#[from] std::io::Error),
    #[error("no usable relay candidate was gathered")]
    RelayUnavailable,
}
/// Lab builds only: the time of day in milliseconds, as the engine's lab lines print it.
#[cfg(feature = "qa-session")]
fn qa_clock() -> u128 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |since| since.as_millis() % 100_000_000)
}
struct Handler {
    gathered: watch::Sender<bool>,
    channels: mpsc::Sender<Arc<dyn DataChannel>>,
    ice: mpsc::Sender<RTCIceCandidateInit>,
    state: watch::Sender<RTCPeerConnectionState>,
    tracks: mpsc::Sender<Arc<dyn TrackRemote>>,
}
#[async_trait::async_trait]
impl PeerConnectionEventHandler for Handler {
    async fn on_ice_gathering_state_change(&self, state: RTCIceGatheringState) {
        if state == RTCIceGatheringState::Complete {
            self.gathered.send_replace(true);
        }
    }
    async fn on_ice_candidate(&self, event: RTCPeerConnectionIceEvent) {
        if let Ok(candidate) = event.candidate.to_json() {
            // Non-trickle SDP retains every candidate even if this optional
            // trickle queue fills. Callers can always gather a complete SDP.
            let _ = self.ice.try_send(candidate);
        }
    }
    async fn on_connection_state_change(&self, state: RTCPeerConnectionState) {
        #[cfg(feature = "qa-session")]
        eprintln!("QA transport: connection {state:?} (clock {})", qa_clock());
        self.state.send_replace(state);
    }
    async fn on_ice_connection_state_change(&self, _state: webrtc::peer_connection::RTCIceConnectionState) {
        // Lab builds only: when ICE found its pair (the rest until the open channel is DTLS and SCTP).
        #[cfg(feature = "qa-session")]
        eprintln!("QA transport: ICE {_state:?} (clock {})", qa_clock());
    }
    async fn on_track(&self, track: Arc<dyn TrackRemote>) {
        let _ = self.tracks.try_send(track);
    }
    async fn on_data_channel(&self, channel: Arc<dyn DataChannel>) {
        if self.channels.try_send(channel.clone()).is_err() {
            let _ = channel.close().await;
        }
    }
}
impl NativeTransport {
    /// The peer connection is connected right now (not disconnected, failed or closed).
    pub fn connected(&self) -> bool {
        *self.state.borrow() == RTCPeerConnectionState::Connected
    }
}
pub struct NativeTransport {
    pub peer: Arc<dyn PeerConnection>,
    pub incoming_channels: mpsc::Receiver<Arc<dyn DataChannel>>,
    pub incoming_tracks: mpsc::Receiver<Arc<dyn TrackRemote>>,
    pub ice_candidates: mpsc::Receiver<RTCIceCandidateInit>,
    pub state: watch::Receiver<RTCPeerConnectionState>,
    gathered: watch::Receiver<bool>,
    gather_reset: watch::Sender<bool>,
    relay_only: bool,
    streams: Option<Arc<crate::stream_turn::StreamRuntime>>,
}
/// How long a relay-only session waits before it accepts a working relay (or peer-reflexive) pair.
pub const RELAY_ONLY_ACCEPTANCE_WAIT: Duration = Duration::ZERO;
/// Time between two connectivity checks of a relay-only session (the stack's default is 200 ms). Each
/// side has about six relay candidates, so up to 36 pairs are checked one after the other: 200 ms
/// apiece kept a relay-only session 3.5 s in ICE on a fast link (lab, 17 Nov 2026). The relay is
/// ours and every check goes to it, so a faster pace burdens no third-party NAT.
pub const RELAY_ONLY_CHECK_INTERVAL: Duration = Duration::from_millis(40);

impl NativeTransport {
    /// Both descriptions list the picture repair stream ([`REPAIR_BLOCK`]), so repair packets
    /// travel with the pictures.
    pub async fn repair_stream(&self) -> bool {
        let listed = |description: Option<RTCSessionDescription>| description.is_some_and(|d| d.sdp.contains("flexfec-03"));
        listed(self.peer.local_description().await) && listed(self.peer.remote_description().await)
    }
    pub async fn receive_video(&self) -> Result<(), TransportError> {
        self.peer
            .add_transceiver_from_kind(
                rtc::rtp_transceiver::rtp_sender::RtpCodecKind::Video,
                Some(webrtc::rtp_transceiver::RTCRtpTransceiverInit {
                    direction: webrtc::rtp_transceiver::RTCRtpTransceiverDirection::Recvonly,
                    ..Default::default()
                }),
            )
            .await?;
        Ok(())
    }
    /// ICE servers include dedicated staging STUN/TURN URLs and short-lived
    /// TURN credentials. With no servers, host candidates permit LAN tests.
    pub async fn new(bind: String, servers: Vec<RTCIceServer>) -> Result<Self, TransportError> {
        Self::configured(bind, servers, false).await
    }
    /// Privacy/restrictive-network verification: never publish a host candidate
    /// or quietly fall back to a direct socket when relay gathering fails.
    pub async fn relay_only(
        bind: String,
        servers: Vec<RTCIceServer>,
    ) -> Result<Self, TransportError> {
        Self::configured(bind, servers, true).await
    }
    /// Use outbound TURN streams and a loopback-only WebRTC socket.
    pub async fn relay_stream_only(servers: Vec<RTCIceServer>) -> Result<Self, TransportError> {
        let mut streams = Vec::new();
        for mut server in servers {
            let parsed = server.urls()?;
            server.urls = server
                .urls
                .into_iter()
                .zip(parsed)
                .filter(|(_, url)| {
                    matches!(url.scheme.to_string().as_str(), "turn" | "turns")
                        && url.proto.to_string() == "tcp"
                })
                .map(|(original, _)| original)
                .collect();
            if !server.urls.is_empty() {
                streams.push(server);
            }
        }
        Self::relay_only("127.0.0.1:0".into(), streams).await
    }
    async fn configured(
        bind: String,
        servers: Vec<RTCIceServer>,
        relay_only: bool,
    ) -> Result<Self, TransportError> {
        Self::configured_tls(bind, servers, relay_only, None).await
    }
    pub(crate) async fn configured_tls(
        bind: String,
        servers: Vec<RTCIceServer>,
        relay_only: bool,
        tls: Option<Arc<tokio_rustls::rustls::ClientConfig>>,
    ) -> Result<Self, TransportError> {
        if servers.len() > 8 {
            return Err(TransportError::Configuration);
        }
        let mut has_relay = false;
        for server in &servers {
            if server.urls.is_empty()
                || server.urls.len() > 8
                || server.urls.iter().any(|url| url.len() > 1024)
                || server.username.len() > 256
                || server.credential.len() > 1024
            {
                return Err(TransportError::Configuration);
            }
            for url in server.urls()? {
                let scheme = url.scheme.to_string();
                if scheme == "turns" && url.proto.to_string() != "tcp" {
                    return Err(TransportError::UnsupportedRelay);
                }
                has_relay |= matches!(scheme.as_str(), "turn" | "turns");
            }
        }
        if relay_only && !has_relay {
            return Err(TransportError::Configuration);
        }
        // When direct connections are allowed, also derive a `stun:` URL from any original UDP
        // `turn:` URL (before StreamRuntime rewrites TCP TURN aliases) so ICE gathers `srflx`
        // candidates and can punch direct peer-to-peer UDP on WAN instead of always relaying.
        let mut servers = servers;
        if !relay_only {
            for server in &mut servers {
                let mut stun_urls = Vec::new();
                for raw in &server.urls {
                    if let Some(rest) = raw.strip_prefix("turn:") {
                        let (authority, query) = rest
                            .split_once('?')
                            .map_or((rest, None), |(a, q)| (a, Some(q)));
                        if matches!(query, None | Some("transport=udp")) {
                            let stun = format!("stun:{authority}");
                            if !server.urls.contains(&stun) && !stun_urls.contains(&stun) {
                                stun_urls.push(stun);
                            }
                        }
                    }
                }
                server.urls.extend(stun_urls);
            }
        }
        let (streams, servers) =
            match crate::stream_turn::StreamRuntime::prepare(&bind, servers.clone(), tls)? {
                Some((runtime, rewritten)) => (Some(runtime), rewritten),
                None => (None, servers),
            };
        let (gathered_tx, gathered) = watch::channel(false);
        let (channels, incoming_channels) = mpsc::channel(4);
        let (ice, ice_candidates) = mpsc::channel(128);
        let (tracks, incoming_tracks) = mpsc::channel(4);
        let (state_tx, state) = watch::channel(RTCPeerConnectionState::New);
        let handler = Arc::new(Handler {
            gathered: gathered_tx.clone(),
            channels,
            ice,
            state: state_tx,
            tracks,
        });
        let mut media_engine = MediaEngine::default();
        media_engine.register_default_codecs()?;
        let registry = interceptors(&mut media_engine)?;
        let builder = PeerConnectionBuilder::new()
            .with_media_engine(media_engine)
            .with_interceptor_registry(registry)
            .with_configuration(
                RTCConfigurationBuilder::new()
                    .with_ice_servers(servers)
                    .with_ice_transport_policy(if relay_only {
                        RTCIceTransportPolicy::Relay
                    } else {
                        RTCIceTransportPolicy::All
                    })
                    .build(),
            )
            .with_handler(handler)
            .with_udp_addrs(vec![bind]);
        let builder = if relay_only {
            builder.with_setting_engine(
                webrtc::peer_connection::SettingEngineBuilder::new()
                    .with_multicast_dns_mode(rtc::ice::mdns::MulticastDnsMode::Disabled)
                    // Only relay candidates exist here, so nothing better can turn up: a working
                    // relay pair is taken at once. With the stack's default (wait 2 s before
                    // accepting a relay pair, 1 s for a peer-reflexive one) opening a relay-only
                    // session took about 6 s from offer to the open control channel in the lab
                    // (82cb321, 14 Nov builds) even on a fast link.
                    .with_ice_connection_attempts(Some(RELAY_ONLY_CHECK_INTERVAL), None)
                    .with_relay_acceptance_min_wait(Some(RELAY_ONLY_ACCEPTANCE_WAIT))
                    .with_prflx_acceptance_min_wait(Some(RELAY_ONLY_ACCEPTANCE_WAIT))
                    .build(),
            )
        } else {
            builder.with_setting_engine(
                webrtc::peer_connection::SettingEngineBuilder::new()
                    .with_multicast_dns_mode(rtc::ice::mdns::MulticastDnsMode::Disabled)
                    .with_ice_connection_attempts(Some(RELAY_ONLY_CHECK_INTERVAL), None)
                    // Give direct LAN/STUN UDP hole-punching enough time on intercontinental WAN
                    // (~220 ms RTT) to beat TURN relay while LAN `host` pairs nominate immediately.
                    .with_prflx_acceptance_min_wait(Some(Duration::from_millis(100)))
                    .with_relay_acceptance_min_wait(Some(Duration::from_millis(450)))
                    .build(),
            )
        };
        let builder = if let Some(runtime) = &streams {
            builder.with_runtime(runtime.clone())
        } else {
            builder
        };
        let peer = match builder.build().await {
            Ok(peer) => peer,
            Err(error) => {
                if let Some(runtime) = &streams {
                    let _ = runtime.shutdown().await;
                }
                return Err(error.into());
            }
        };
        Ok(Self {
            peer: Arc::new(peer),
            incoming_channels,
            incoming_tracks,
            ice_candidates,
            state,
            gathered,
            gather_reset: gathered_tx,
            relay_only,
            streams,
        })
    }
    /// Prefer complete non-trickle SDP; a failed ICE server must not hide an
    /// already usable candidate. After the bounded deadline, return available
    /// candidates and continue exposing late candidates through ice_candidates.
    pub async fn local_sdp(
        &mut self,
        description: RTCSessionDescription,
    ) -> Result<RTCSessionDescription, TransportError> {
        self.gather_reset.send_replace(false);
        self.gathered.borrow_and_update();
        self.peer.set_local_description(description).await?;
        let started = tokio::time::Instant::now();
        let deadline = started + Duration::from_secs(3);
        loop {
            if *self.gathered.borrow() || tokio::time::Instant::now() >= deadline {
                break;
            }
            if started.elapsed() >= Duration::from_millis(120)
                && let Some(desc) = self.peer.local_description().await
            {
                let has_relay = desc.sdp.lines().any(|line| {
                    line.starts_with("a=candidate:")
                        && line
                            .split_ascii_whitespace()
                            .collect::<Vec<_>>()
                            .windows(2)
                            .any(|pair| pair == ["typ", "relay"])
                });
                if has_relay {
                    break;
                }
            }
            if let Ok(Err(_)) = tokio::time::timeout(Duration::from_millis(15), self.gathered.changed()).await {
                return Err(TransportError::Timeout);
            }
        }
        let description = self
            .peer
            .local_description()
            .await
            .ok_or(TransportError::Timeout)?;
        let has_candidates = description
            .sdp
            .lines()
            .any(|line| line.starts_with("a=candidate:"));
        if !has_candidates {
            return Err(if self.relay_only {
                TransportError::RelayUnavailable
            } else {
                TransportError::Timeout
            });
        }
        if self.relay_only {
            let candidates: Vec<_> = description
                .sdp
                .lines()
                .filter(|line| line.starts_with("a=candidate:"))
                .collect();
            if candidates.is_empty()
                || candidates.iter().any(|line| {
                    !line
                        .split_ascii_whitespace()
                        .collect::<Vec<_>>()
                        .windows(2)
                        .any(|pair| pair == ["typ", "relay"])
                })
            {
                return Err(TransportError::RelayUnavailable);
            }
        }
        Ok(description)
    }
    /// Whether the pair ICE selected runs through the relay: either side's candidate of the pair is a
    /// relay candidate. `None` while no pair is selected. A session that allows direct connections can
    /// still end up on the relay (both sides behind NAT that keeps hole punching out, a firewall
    /// that drops UDP), so the path is read here and not taken from the relay-only setting.
    pub async fn relayed(&self) -> Option<bool> {
        if self.relay_only {
            return Some(true);
        }
        let sctp = self.peer.sctp().await?;
        let pair = sctp.transport().ice_transport().get_selected_candidate_pair().await.ok()??;
        Some(pair_is_relayed(pair.local().typ, pair.remote().typ))
    }
    /// The latest round trip of the connection's nominated pair (ICE consent checks), `None`
    /// before one was measured. For the technician's perf log; it costs a stats snapshot.
    pub async fn round_trip(&self) -> Option<Duration> {
        let report = self.peer.get_stats(std::time::Instant::now(), rtc::statistics::StatsSelector::None).await;
        report
            .candidate_pairs()
            .filter(|pair| pair.nominated && pair.current_round_trip_time > 0.0)
            .map(|pair| pair.current_round_trip_time)
            .reduce(f64::min)
            .map(Duration::from_secs_f64)
    }
    pub fn ice_gathering_complete(&self) -> bool {
        *self.gathered.borrow()
    }
    #[cfg(test)]
    pub(crate) fn stream_udp_writes(&self) -> Option<u64> {
        self.streams
            .as_ref()
            .map(|runtime| runtime.physical_udp_writes())
    }
    /// Hash the actual peer certificate returned by the completed DTLS stack,
    /// independently of the signaling or signed application Hello.
    pub async fn observed_fingerprint(&self) -> Result<[u8; 32], TransportError> {
        let sctp = self.peer.sctp().await.ok_or(TransportError::Fingerprint)?;
        let certificates = sctp.transport().get_remote_certificates().await?;
        let leaf = certificates
            .first()
            .filter(|c| !c.is_empty())
            .ok_or(TransportError::Fingerprint)?;
        Ok(Sha256::digest(leaf).into())
    }
    /// Transport protocol actually used by the relay/direct path (`"udp"`, `"tcp"`, or `"tls"`).
    pub fn active_protocol(&self) -> &'static str {
        self.streams
            .as_ref()
            .map_or("udp", |runtime| runtime.active_protocol())
    }
    pub async fn close(&self) -> Result<(), TransportError> {
        let peer = self.peer.close().await;
        let streams = if let Some(runtime) = &self.streams {
            runtime.shutdown().await
        } else {
            Ok(())
        };
        peer?;
        streams?;
        Ok(())
    }
}

/// A candidate pair runs through the relay when either of its two candidates is a relay candidate.
pub fn pair_is_relayed(
    local: webrtc::peer_connection::RTCIceCandidateType,
    remote: webrtc::peer_connection::RTCIceCandidateType,
) -> bool {
    use webrtc::peer_connection::RTCIceCandidateType::Relay;
    local == Relay || remote == Relay
}

/// SHA-256 local certificate fingerprint from locally generated SDP. All media
/// sections must agree; conflicting, missing or weak fingerprints are rejected.
pub fn local_fingerprint(sdp: &str) -> Result<[u8; 32], TransportError> {
    let mut found = None;
    for line in sdp.lines().filter_map(|s| s.strip_prefix("a=fingerprint:")) {
        let text = line
            .strip_prefix("sha-256 ")
            .ok_or(TransportError::Fingerprint)?;
        let octets: Vec<_> = text.split(':').collect();
        if octets.len() != 32 || octets.iter().any(|s| s.len() != 2) {
            return Err(TransportError::Fingerprint);
        }
        let mut bytes = [0; 32];
        for (out, text) in bytes.iter_mut().zip(octets) {
            *out = u8::from_str_radix(text, 16).map_err(|_| TransportError::Fingerprint)?;
        }
        if bytes == [0; 32] || found.is_some_and(|prior| prior != bytes) {
            return Err(TransportError::Fingerprint);
        }
        found = Some(bytes);
    }
    found.ok_or(TransportError::Fingerprint)
}

pub use webrtc::data_channel::{
    DataChannel as NativeDataChannel, DataChannelEvent as NativeDataChannelEvent,
};
pub use webrtc::peer_connection::RTCIceServer as NativeIceServer;
pub use webrtc::peer_connection::{
    RTCIceCandidateInit as NativeIceCandidate, RTCSessionDescription as NativeSessionDescription,
};

/// The connection's UDP sockets get a large receive buffer (vendor/webrtc/MIXEL-PATCHES.md).
#[cfg(test)]
mod receive_buffer_tests {
    use std::time::Duration;
    use webrtc::peer_connection::{UDP_RECEIVE_BUFFER, enlarge_receive_buffer};

    /// A burst that is complete before the reader gets to it (as a keyframe on a local network)
    /// is kept: with the system's default buffer Windows kept 64 KiB of it.
    #[test]
    fn a_keyframe_sized_burst_waiting_in_the_socket_is_not_dropped() {
        let receiver = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        let granted = enlarge_receive_buffer(&receiver);
        // Linux doubles the value it reports and caps the request at net.core.rmem_max.
        let room = if cfg!(target_os = "linux") { granted / 2 } else { granted };
        if cfg!(any(target_os = "windows", target_os = "macos")) {
            assert!(room >= UDP_RECEIVE_BUFFER, "the system granted {room} bytes");
        }
        assert!(room >= 64 * 1024, "the system granted {room} bytes");
        // 1200-byte packets worth half the room (the systems count some overhead per packet).
        let count = (room / 2 / 1200).min(1500);
        let sender = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        let packet = [7u8; 1200];
        for _ in 0..count {
            sender.send_to(&packet, receiver.local_addr().unwrap()).unwrap();
        }
        std::thread::sleep(Duration::from_millis(200));
        receiver.set_read_timeout(Some(Duration::from_millis(300))).unwrap();
        let (mut received, mut buffer) = (0, [0u8; 1500]);
        while receiver.recv_from(&mut buffer).is_ok() {
            received += 1;
        }
        assert_eq!(received, count, "of a burst of {count} packets ({} bytes) into {room} bytes", count * 1200);
    }
}

#[cfg(test)]
mod path_tests {
    use super::pair_is_relayed;
    use webrtc::peer_connection::RTCIceCandidateType::{Host, Prflx, Relay, Srflx};

    #[test]
    fn a_pair_is_relayed_when_either_candidate_is_a_relay_candidate() {
        assert!(!pair_is_relayed(Host, Host));
        assert!(!pair_is_relayed(Srflx, Prflx));
        assert!(pair_is_relayed(Relay, Host), "our side allocated on the relay");
        assert!(pair_is_relayed(Srflx, Relay), "the other side is on the relay");
    }
}

#[cfg(test)]
mod repeat_tests {
    use super::*;

    /// The media stack has the stack's default parts, in the same order: since 2.0.16 with the part
    /// that sends a lost packet again (the asking part is bounded, see `interceptors`).
    #[test]
    fn lost_packets_are_sent_again_on_request_and_the_rest_is_as_before() {
        let names = |registry: Registry| -> Vec<String> { registry.slots().into_iter().map(|(_, name)| name).collect() };
        let mut media_engine = MediaEngine::default();
        let default = names(webrtc::peer_connection::register_default_interceptors(Registry::new(), &mut media_engine).unwrap());
        assert_eq!(default, ["NackResponderInterceptor", "NackGeneratorInterceptor", "TwccReceiverInterceptor", "ReceiverReportInterceptor", "SenderReportInterceptor"]);
        let mut media_engine = MediaEngine::default();
        let ours = names(interceptors(&mut media_engine).unwrap());
        // The default set, including the part that resends, plus the picture repair stream.
        let mut expected: Vec<String> = default.clone();
        for (name, after) in [("FlexFec03SendInterceptor", "NackResponderInterceptor"), ("FlexFec03ReceiveInterceptor", "FlexFec03SendInterceptor")] {
            let at = expected.iter().position(|n| n == after).unwrap() + 1;
            expected.insert(at, name.to_owned());
        }
        assert_eq!(ours, expected);
    }

    /// Repair packets are listed only where both sides know them: a technician app from before
    /// 2.2.13 (no repair stream in its description) gets the pictures exactly as before.
    #[tokio::test]
    async fn the_repair_stream_is_negotiated_only_with_apps_that_know_it() {
        let host = NativeTransport::new("127.0.0.1:0".into(), vec![]).await.unwrap();
        let _pictures = crate::media::H264VideoSender::new(&host, 640, 420, 30).await.unwrap();
        let offer = host.peer.create_offer(None).await.unwrap();
        assert!(offer.sdp.contains("flexfec-03"), "{}", offer.sdp);
        // An app before 2.2.13: the stack's own codecs, no repair stream.
        let mut old_engine = MediaEngine::default();
        old_engine.register_default_codecs().unwrap();
        struct Quiet;
        #[async_trait::async_trait]
        impl PeerConnectionEventHandler for Quiet {}
        let old = webrtc::peer_connection::PeerConnectionBuilder::new().with_media_engine(old_engine).with_handler(Arc::new(Quiet)).with_udp_addrs(vec!["127.0.0.1:0".to_owned()]).build().await.unwrap();
        old.add_transceiver_from_kind(rtc::rtp_transceiver::rtp_sender::RtpCodecKind::Video, Some(webrtc::rtp_transceiver::RTCRtpTransceiverInit { direction: webrtc::rtp_transceiver::RTCRtpTransceiverDirection::Recvonly, ..Default::default() })).await.unwrap();
        old.set_remote_description(offer.clone()).await.unwrap();
        let answer = old.create_answer(None).await.unwrap();
        assert!(answer.sdp.contains("H264/90000") && !answer.sdp.contains("flexfec-03"), "{}", answer.sdp);
        // A current technician lists it too.
        let technician = NativeTransport::new("127.0.0.1:0".into(), vec![]).await.unwrap();
        technician.receive_video().await.unwrap();
        technician.peer.set_remote_description(offer).await.unwrap();
        assert!(technician.peer.create_answer(None).await.unwrap().sdp.contains("flexfec-03"));
        host.close().await.unwrap();
        technician.close().await.unwrap();
        let _ = old.close().await;
    }

    /// The session description is the one the published apps offer and answer (the list of
    /// picture formats names NACK as before), so both directions still agree on the picture format.
    #[tokio::test]
    async fn the_session_description_is_unchanged() {
        let host = NativeTransport::new("127.0.0.1:0".into(), vec![]).await.unwrap();
        let _pictures = crate::media::H264VideoSender::new(&host, 640, 420, 30).await.unwrap();
        let offer = host.peer.create_offer(None).await.unwrap();
        assert!(offer.sdp.contains("H264/90000") && offer.sdp.contains(" nack") && offer.sdp.contains("transport-cc"), "{}", offer.sdp);
        let technician = NativeTransport::new("127.0.0.1:0".into(), vec![]).await.unwrap();
        technician.receive_video().await.unwrap();
        technician.peer.set_remote_description(offer).await.unwrap();
        assert!(technician.peer.create_answer(None).await.unwrap().sdp.contains("H264/90000"));
        host.close().await.unwrap();
        technician.close().await.unwrap();
    }
}
