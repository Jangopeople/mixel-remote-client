//! Consent-gated encoded-video delivery over native DTLS-SRTP. Capture/encoding
//! belongs to the host adapter; an open transport alone never grants streaming.
use crate::{MAX_VIDEO_ACCESS_UNIT, session::Session, transport::NativeTransport};
use rand_core::{OsRng, RngCore};
use rtc::{
    media::Sample,
    media_stream::MediaStreamTrack,
    rtp_transceiver::rtp_sender::{
        RTCRtpCodec, RTCRtpCodingParameters, RTCRtpEncodingParameters, RtpCodecKind,
    },
};
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
use webrtc::{
    media_stream::track_local::{TrackLocal, static_sample::TrackLocalStaticSample},
    peer_connection::RTCPeerConnectionState,
    rtp_transceiver::RtpSender,
};
#[derive(Debug, thiserror::Error)]
pub enum MediaError {
    #[error("video requires active authenticated consent and a connected transport")]
    Forbidden,
    #[error("invalid video frame or configuration")]
    Invalid,
    #[error("negotiated peer has no compatible H264 payload")]
    Codec,
    #[error(transparent)]
    Transport(#[from] webrtc::error::Error),
}
pub struct H264VideoSender {
    track: Arc<TrackLocalStaticSample>,
    sender: Arc<dyn RtpSender>,
    ssrc: u32,
    state: tokio::sync::watch::Receiver<RTCPeerConnectionState>,
    peer: Arc<dyn webrtc::peer_connection::PeerConnection>,
}
impl H264VideoSender {
    /// Constrained baseline level3.1. Higher display resolutions must be scaled
    /// or negotiate a higher level instead of violating the decoder's SDP limit.
    pub async fn new(
        transport: &NativeTransport,
        width: usize,
        height: usize,
        fps: u32,
    ) -> Result<Self, MediaError> {
        if width == 0
            || height == 0
            || width > 1280
            || height > 720
            || !(1..=30).contains(&fps)
            || width.div_ceil(16) * height.div_ceil(16) * fps as usize > 108000
        {
            return Err(MediaError::Invalid);
        }
        let ssrc = loop {
            let n = OsRng.next_u32();
            if n != 0 {
                break n;
            }
        };
        let codec = RTCRtpCodec {
            mime_type: "video/H264".into(),
            clock_rate: 90000,
            channels: 0,
            sdp_fmtp_line: "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f"
                .into(),
            rtcp_feedback: vec![],
        };
        let track = Arc::new(TrackLocalStaticSample::new(
            Instant::now(),
            MediaStreamTrack::new(
                "mixel-screen".into(),
                format!("mixel-video-{ssrc:08x}"),
                "Mixel Remote desktop".into(),
                RtpCodecKind::Video,
                vec![RTCRtpEncodingParameters {
                    rtp_coding_parameters: RTCRtpCodingParameters {
                        ssrc: Some(ssrc),
                        ..Default::default()
                    },
                    codec,
                    ..Default::default()
                }],
            ),
        )?);
        let sender = transport
            .peer
            .add_track(track.clone() as Arc<dyn TrackLocal>)
            .await?;
        Ok(Self {
            track,
            sender,
            ssrc,
            state: transport.state.clone(),
            peer: transport.peer.clone(),
        })
    }
    pub async fn send(
        &self,
        consent: &Session,
        annex_b: &[u8],
        duration: Duration,
    ) -> Result<(), MediaError> {
        if !consent.can_share_local_screen()
            || *self.state.borrow() != RTCPeerConnectionState::Connected
        {
            return Err(MediaError::Forbidden);
        }
        if annex_b.is_empty()
            || annex_b.len() > MAX_VIDEO_ACCESS_UNIT
            || (!annex_b.starts_with(&[0, 0, 0, 1]) && !annex_b.starts_with(&[0, 0, 1]))
            || duration.is_zero()
            || duration > Duration::from_secs(2)
        {
            return Err(MediaError::Invalid);
        }
        let Some(expected) = consent.fingerprints() else {
            return Err(MediaError::Forbidden);
        };
        let local = self
            .peer
            .local_description()
            .await
            .ok_or(MediaError::Forbidden)?;
        let local =
            crate::transport::local_fingerprint(&local.sdp).map_err(|_| MediaError::Forbidden)?;
        let sctp = self.peer.sctp().await.ok_or(MediaError::Forbidden)?;
        let certificates = sctp.transport().get_remote_certificates().await?;
        let certificate = certificates.first().ok_or(MediaError::Forbidden)?;
        use sha2::Digest;
        let remote: [u8; 32] = sha2::Sha256::digest(certificate).into();
        if expected != (local, remote) {
            return Err(MediaError::Forbidden);
        }
        let parameters = self.sender.get_parameters().await?;
        let payload = parameters
            .rtp_parameters
            .codecs
            .iter()
            .find(|codec| {
                codec.rtp_codec.mime_type.eq_ignore_ascii_case("video/H264")
                    && codec
                        .rtp_codec
                        .sdp_fmtp_line
                        .contains("packetization-mode=1")
            })
            .ok_or(MediaError::Codec)?
            .payload_type;
        self.track
            .sample_writer(self.ssrc, payload)
            .write_sample(&Sample {
                data: bytes::Bytes::copy_from_slice(annex_b),
                duration,
                ..Sample::new(Instant::now())
            })
            .await?;
        Ok(())
    }
}
pub use webrtc::media_stream::track_remote::TrackRemoteEvent;

/// Bounded H264 access-unit assembly. Sequence loss discards predictive frames
/// until an IDR arrives; old/duplicate packets cannot damage a newer frame.
pub struct H264Assembler {
    packet: rtc::rtp::codec::h264::H264Packet,
    timestamp: Option<u32>,
    expected: Option<u16>,
    started: Instant,
    data: zeroize::Zeroizing<Vec<u8>>,
    input_bytes: usize,
    damaged: bool,
    needs_keyframe: bool,
    fragment_open: bool,
    fragment_header: Option<u8>,
}
impl Default for H264Assembler {
    fn default() -> Self {
        Self {
            packet: Default::default(),
            timestamp: None,
            expected: None,
            started: Instant::now(),
            data: Default::default(),
            input_bytes: 0,
            damaged: false,
            needs_keyframe: true,
            fragment_open: false,
            fragment_header: None,
        }
    }
}
impl H264Assembler {
    pub fn needs_keyframe(&self) -> bool {
        self.needs_keyframe
    }
    pub fn push(
        &mut self,
        sequence: u16,
        timestamp: u32,
        marker: bool,
        payload: &[u8],
    ) -> Result<Option<zeroize::Zeroizing<Vec<u8>>>, MediaError> {
        use rtc::rtp::packetizer::Depacketizer;
        use zeroize::Zeroize;
        if payload.is_empty() || payload.len() > 65507 {
            self.reset();
            return Err(MediaError::Invalid);
        }
        let gap = if let Some(expected) = self.expected {
            let delta = sequence.wrapping_sub(expected);
            if delta >= 0x8000 {
                return Ok(None);
            } // stale retransmission or duplicate
            delta != 0
        } else {
            false
        };
        self.expected = Some(sequence.wrapping_add(1));
        if !valid_h264_payload(payload, marker) {
            self.reset();
            return Err(MediaError::Invalid);
        }
        let bytes = bytes::Bytes::copy_from_slice(payload);
        if self.timestamp != Some(timestamp) {
            if self.timestamp.is_some() || gap {
                self.needs_keyframe = true;
            }
            self.data.zeroize();
            self.packet = Default::default();
            self.input_bytes = 0;
            self.timestamp = Some(timestamp);
            self.started = Instant::now();
            self.fragment_open = false;
            self.fragment_header = None;
            self.damaged = !self.packet.is_partition_head(&bytes);
        } else if gap || self.started.elapsed() > Duration::from_secs(2) {
            self.data.zeroize();
            self.packet = Default::default();
            self.damaged = true;
            self.fragment_open = false;
            self.fragment_header = None;
            self.needs_keyframe = true;
        }
        if self.damaged {
            if marker {
                self.timestamp = None;
            }
            return Ok(None);
        }
        self.input_bytes = self
            .input_bytes
            .checked_add(payload.len())
            .ok_or(MediaError::Invalid)?;
        if self.input_bytes > MAX_VIDEO_ACCESS_UNIT {
            self.reset();
            return Err(MediaError::Invalid);
        }
        let nal_type = payload[0] & 0x1f;
        if nal_type == 28 {
            let start = payload[1] & 0x80 != 0;
            if start == self.fragment_open {
                self.reset();
                return Err(MediaError::Invalid);
            }
            let header = (payload[0] & 0xe0) | (payload[1] & 0x1f);
            if !start && self.fragment_header != Some(header) {
                self.reset();
                return Err(MediaError::Invalid);
            }
            self.fragment_open = payload[1] & 0x40 == 0;
            self.fragment_header = self.fragment_open.then_some(header);
        } else if self.fragment_open {
            self.reset();
            return Err(MediaError::Invalid);
        }
        // The upstream depacketizer rejects two-byte single NALs; those are
        // valid for e.g. AUD, so frame them directly after our syntax checks.
        let decoded_result = if (1..=23).contains(&nal_type) {
            let mut nal = Vec::with_capacity(4 + payload.len());
            nal.extend([0, 0, 0, 1]);
            nal.extend_from_slice(payload);
            Ok(bytes::Bytes::from(nal))
        } else {
            self.packet.depacketize(&bytes)
        };
        let decoded = match decoded_result {
            Ok(bytes) => bytes,
            Err(_) => {
                self.reset();
                return Err(MediaError::Invalid);
            }
        };
        if self
            .data
            .len()
            .checked_add(decoded.len())
            .is_none_or(|size| size > MAX_VIDEO_ACCESS_UNIT)
        {
            self.reset();
            return Err(MediaError::Invalid);
        }
        self.data.extend_from_slice(&decoded);
        if !marker {
            return Ok(None);
        }
        self.timestamp = None;
        if self.data.is_empty() {
            self.needs_keyframe = true;
            return Ok(None);
        }
        let keyframe = self
            .data
            .windows(5)
            .any(|window| window[..4] == [0, 0, 0, 1] && window[4] & 0x1f == 5);
        if self.needs_keyframe && !keyframe {
            self.data.zeroize();
            return Ok(None);
        }
        if keyframe {
            self.needs_keyframe = false;
        }
        Ok(Some(std::mem::take(&mut self.data)))
    }
    fn reset(&mut self) {
        *self = Self::default();
    }
}

fn valid_h264_payload(payload: &[u8], marker: bool) -> bool {
    if payload.len() < 2 || payload[0] & 0x80 != 0 {
        return false;
    }
    match payload[0] & 0x1f {
        1..=23 => true,
        24 => {
            let mut position = 1;
            let mut count = 0;
            while position < payload.len() {
                let Some(length) = payload.get(position..position + 2) else {
                    return false;
                };
                let length = u16::from_be_bytes([length[0], length[1]]) as usize;
                position += 2;
                let Some(nal) = payload.get(position..position + length) else {
                    return false;
                };
                if nal.len() < 2 || nal[0] & 0x80 != 0 || !(1..=23).contains(&(nal[0] & 0x1f)) {
                    return false;
                }
                position += length;
                count += 1;
                if count > 1024 {
                    return false;
                }
            }
            count > 0
        }
        28 => {
            payload.len() >= 3
                && payload[1] & 0x20 == 0
                && payload[1] & 0xc0 != 0xc0
                && (1..=23).contains(&(payload[1] & 0x1f))
                && (!marker || payload[1] & 0x40 != 0)
        }
        _ => false,
    }
}
