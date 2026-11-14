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

/// The media stack's default set of helpers for every stream, without one of them: the one that
/// sends a lost packet again when the other side asks for it (NACK responder).
///
/// The side that shows the pictures takes a gap in the packet numbering for a damaged picture at
/// once and asks for a complete one; a packet that arrives later with an old number is dropped
/// (`H264Assembler`). So a packet sent again was never used, by any version. It only cost: on a
/// link that had just dropped part of a burst, every missing packet was asked for again and again
/// and sent again each time, unpaced. Measured on a 2 Mbit/s link: about 460 kB of pictures in
/// twelve seconds and 20 MB of repeats on top, which left no room for the next complete picture;
/// the session ended with "Remote video delivery timed out".
///
/// Everything else is what the stack's `register_default_interceptors` sets up, in its order:
/// the part that asks for repeats, the arrival feedback and the reports. The asking part stays so
/// that nothing else about a session changes: the session description is the same, and a
/// published host still answers it as before. (The stack's `NoopInterceptor` is no stand-in for
/// the left-out part: despite its name it ends the inbound control traffic for everything behind
/// it.)
fn interceptors(media_engine: &mut MediaEngine) -> Result<Registry, webrtc::error::Error> {
    use rtc::interceptor::{NackGeneratorBuilder, Slot};
    use rtc::peer_connection::configuration::interceptor_registry::{
        configure_rtcp_reports, configure_simulcast_extension_headers, configure_twcc_receiver_only,
    };
    // Lab builds only: the stack's default set (with repeats), to measure against.
    #[cfg(feature = "qa-session")]
    if std::env::var_os("MIXEL_QA_UNPACED").is_some() || std::env::var_os("MIXEL_QA_REPEATS").is_some() {
        return webrtc::peer_connection::register_default_interceptors(Registry::new(), media_engine);
    }
    configure_simulcast_extension_headers(media_engine)?;
    let registry = configure_twcc_receiver_only(Registry::new(), media_engine)?;
    Ok(configure_rtcp_reports(registry).with(Slot::NackGenerator, NackGeneratorBuilder::new().build()))
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
        self.state.send_replace(state);
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
impl NativeTransport {
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
                    .build(),
            )
        } else {
            builder
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
        let gathering =
            tokio::time::timeout(Duration::from_secs(3), self.gathered.wait_for(|done| *done))
                .await;
        if matches!(gathering, Ok(Err(_))) {
            return Err(TransportError::Timeout);
        }
        drop(gathering);
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
mod repeat_tests {
    use super::*;

    /// The media stack is set up as the published apps have it, without the part that sends a
    /// lost packet again. The part that asks for repeats, the arrival feedback and the reports are
    /// there as before, in the same order.
    #[test]
    fn lost_packets_are_not_sent_again_and_the_rest_is_as_before() {
        let names = |registry: Registry| -> Vec<String> { registry.slots().into_iter().map(|(_, name)| name).collect() };
        let mut media_engine = MediaEngine::default();
        let default = names(webrtc::peer_connection::register_default_interceptors(Registry::new(), &mut media_engine).unwrap());
        assert_eq!(default, ["NackResponderInterceptor", "NackGeneratorInterceptor", "TwccReceiverInterceptor", "ReceiverReportInterceptor", "SenderReportInterceptor"]);
        let mut media_engine = MediaEngine::default();
        let ours = names(interceptors(&mut media_engine).unwrap());
        assert_eq!(ours, default[1..], "the default set without its first part, the one that resends");
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
