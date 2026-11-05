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
        RTCPeerConnectionIceEvent, RTCPeerConnectionState, RTCSessionDescription, Registry,
        register_default_interceptors,
    },
};

#[derive(Debug, thiserror::Error)]
pub enum TransportError {
    #[error("WebRTC: {0}")]
    WebRtc(#[from] webrtc::error::Error),
    #[error("WebRTC gathering timed out or transport ended")]
    Timeout,
    #[error("missing or invalid DTLS certificate fingerprint")]
    Fingerprint,
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
}
impl NativeTransport {
    /// ICE servers include dedicated staging STUN/TURN URLs and short-lived
    /// TURN credentials. With no servers, host candidates permit LAN tests.
    pub async fn new(bind: String, servers: Vec<RTCIceServer>) -> Result<Self, TransportError> {
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
        let registry = register_default_interceptors(Registry::new(), &mut media_engine)?;
        let peer = PeerConnectionBuilder::new()
            .with_media_engine(media_engine)
            .with_interceptor_registry(registry)
            .with_configuration(
                RTCConfigurationBuilder::new()
                    .with_ice_servers(servers)
                    .build(),
            )
            .with_handler(handler)
            .with_udp_addrs(vec![bind])
            .build()
            .await?;
        Ok(Self {
            peer: Arc::new(peer),
            incoming_channels,
            incoming_tracks,
            ice_candidates,
            state,
            gathered,
            gather_reset: gathered_tx,
        })
    }
    /// Complete non-trickle SDP avoids races between early candidates and a
    /// remote description. Time bounded even when a STUN/TURN server fails.
    pub async fn local_sdp(
        &mut self,
        description: RTCSessionDescription,
    ) -> Result<RTCSessionDescription, TransportError> {
        self.gather_reset.send_replace(false);
        self.gathered.borrow_and_update();
        self.peer.set_local_description(description).await?;
        tokio::time::timeout(
            Duration::from_secs(10),
            self.gathered.wait_for(|done| *done),
        )
        .await
        .map_err(|_| TransportError::Timeout)?
        .map_err(|_| TransportError::Timeout)?;
        self.peer
            .local_description()
            .await
            .ok_or(TransportError::Timeout)
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
        self.peer.close().await?;
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
