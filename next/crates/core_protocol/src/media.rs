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
    /// Constrained baseline. The SDP announces level 3.1 (1280x720) for older apps;
    /// apps that both announce the `hd` session feature send up to 1920x1080.
    pub async fn new(
        transport: &NativeTransport,
        width: usize,
        height: usize,
        fps: u32,
    ) -> Result<Self, MediaError> {
        if width == 0
            || height == 0
            // Landscape up to 1920x1088, or the same picture upright (a phone screen).
            || width.max(height) > 1920
            || width.min(height) > 1088
            || !(1..=30).contains(&fps)
            || width.div_ceil(16) * height.div_ceil(16) * fps as usize > 245760
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
    /// The whole picture at once. A long-lived session sends through [`Self::packets`] and
    /// [`Self::write`] instead, so that a large picture can be spread over time.
    pub async fn send(
        &self,
        consent: &Session,
        annex_b: &[u8],
        duration: Duration,
    ) -> Result<(), MediaError> {
        for packet in self.packets(consent, annex_b, duration).await? {
            self.write(consent, packet).await?;
        }
        Ok(())
    }
    /// The picture as numbered RTP packets, after every check [`Self::send`] makes. They have to
    /// be written with [`Self::write`] in this order and all of them: the receiver takes a gap
    /// in the numbering for a loss.
    pub async fn packets(
        &self,
        consent: &Session,
        annex_b: &[u8],
        duration: Duration,
    ) -> Result<Vec<PicturePacket>, MediaError> {
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
        Ok(self
            .track
            .packetize_sample(
                self.ssrc,
                payload,
                &Sample {
                    data: bytes::Bytes::copy_from_slice(annex_b),
                    duration,
                    ..Sample::new(Instant::now())
                },
            )
            .await?
            .into_iter()
            .map(PicturePacket)
            .collect())
    }
    /// Sends one packet of [`Self::packets`]. Consent and the connection are asked again for
    /// every packet: a picture that is spread over time stops the moment sharing does.
    pub async fn write(&self, consent: &Session, packet: PicturePacket) -> Result<(), MediaError> {
        if !consent.can_share_local_screen()
            || *self.state.borrow() != RTCPeerConnectionState::Connected
        {
            return Err(MediaError::Forbidden);
        }
        self.track.write_rtp(packet.0).await?;
        Ok(())
    }
}
/// One RTP packet of a picture, between [`H264VideoSender::packets`] and [`H264VideoSender::write`].
pub struct PicturePacket(rtc::rtp::Packet);
impl PicturePacket {
    /// Bytes on the wire that count for pacing: the RTP header and the payload.
    pub fn size(&self) -> usize {
        12 + self.0.payload.len()
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

/// Puts picture packets back in order and waits a moment for one that is missing, so a packet the
/// host sends again on request (NACK) can still complete its picture. Without it every lost packet
/// made the technician's side drop the picture and wait for a complete one (one to several seconds
/// on a long or lossy line: at 1 % loss a moving full-HD screen arrived at about one picture a
/// second). A gap that is not filled within `wait` is passed on as it is: the assembler then sees
/// the gap and asks for a complete picture, as before.
pub struct PacketReorder<P> {
    expected: Option<u16>,
    held: std::collections::BTreeMap<u16, P>,
    gap_since: Option<std::time::Instant>,
    wait: std::time::Duration,
    /// Gaps closed by a packet that came late (sent again on request, or reordered on the way).
    pub filled: u32,
    /// Gaps given up (the picture is lost; the assembler asks for a complete one).
    pub given_up: u32,
}

/// Packets held behind one gap at most; beyond that the gap is given up at once.
const REORDER_MAX_HELD: usize = 1024;

impl<P> PacketReorder<P> {
    pub fn new(wait: std::time::Duration) -> Self {
        Self { expected: None, held: std::collections::BTreeMap::new(), gap_since: None, wait, filled: 0, given_up: 0 }
    }

    /// One packet arrived; returns the packets that are now in order (possibly none).
    pub fn push(&mut self, sequence: u16, packet: P, now: std::time::Instant) -> Vec<P> {
        let Some(expected) = self.expected else {
            self.expected = Some(sequence.wrapping_add(1));
            return vec![packet];
        };
        let ahead = sequence.wrapping_sub(expected);
        if ahead >= 0x8000 {
            // Older than what was already passed on: a duplicate, or a repeat that came too late.
            return Vec::new();
        }
        if ahead == 0 {
            if !self.held.is_empty() {
                self.filled += 1;
            }
            self.expected = Some(sequence.wrapping_add(1));
            let mut ready = vec![packet];
            self.drain(&mut ready);
            self.gap_since = (!self.held.is_empty()).then_some(now);
            return ready;
        }
        // A key is the distance from `expected`, so the map's order is the packets' order.
        self.held.entry(ahead).or_insert(packet);
        self.gap_since.get_or_insert(now);
        if self.held.len() > REORDER_MAX_HELD {
            return self.give_up();
        }
        Vec::new()
    }

    /// Packets passed on because the gap in front of them was not filled in time.
    pub fn expire(&mut self, now: std::time::Instant) -> Vec<P> {
        match self.gap_since {
            Some(since) if now.saturating_duration_since(since) >= self.wait => self.give_up(),
            _ => Vec::new(),
        }
    }

    /// Whether packets are waiting behind a gap.
    pub fn waiting(&self) -> bool {
        !self.held.is_empty()
    }

    fn drain(&mut self, ready: &mut Vec<P>) {
        // Every held key is a distance from the old `expected`, one less now for each packet passed on.
        let mut shift = 1u16;
        loop {
            match self.held.first_key_value().map(|(key, _)| *key) {
                Some(key) if key == shift => {
                    let (_, packet) = self.held.pop_first().unwrap_or_else(|| unreachable!());
                    ready.push(packet);
                    shift = shift.wrapping_add(1);
                }
                _ => break,
            }
        }
        if let Some(expected) = self.expected {
            let passed = shift.wrapping_sub(1);
            self.expected = Some(expected.wrapping_add(passed));
            if passed > 0 {
                self.held = std::mem::take(&mut self.held).into_iter().map(|(key, packet)| (key - shift, packet)).collect();
            } else {
                self.held = std::mem::take(&mut self.held).into_iter().map(|(key, packet)| (key - 1, packet)).collect();
            }
        }
    }

    fn give_up(&mut self) -> Vec<P> {
        self.given_up += 1;
        let last = self.held.last_key_value().map(|(key, _)| *key);
        if let (Some(expected), Some(last)) = (self.expected, last) {
            self.expected = Some(expected.wrapping_add(last).wrapping_add(1));
        }
        self.gap_since = None;
        std::mem::take(&mut self.held).into_values().collect()
    }
}

#[cfg(test)]
mod reorder_tests {
    use super::PacketReorder;
    use std::time::{Duration, Instant};

    fn feed(reorder: &mut PacketReorder<u16>, sequences: &[u16], now: Instant) -> Vec<u16> {
        sequences.iter().flat_map(|sequence| reorder.push(*sequence, *sequence, now)).collect()
    }

    #[test]
    fn packets_in_order_pass_at_once() {
        let mut reorder = PacketReorder::new(Duration::from_millis(400));
        let now = Instant::now();
        assert_eq!(feed(&mut reorder, &[10, 11, 12, 13], now), [10, 11, 12, 13]);
        assert!(!reorder.waiting());
    }

    #[test]
    fn a_packet_sent_again_completes_the_picture_in_order() {
        let mut reorder = PacketReorder::new(Duration::from_millis(400));
        let now = Instant::now();
        assert_eq!(feed(&mut reorder, &[10, 11, 13, 14, 15], now), [10, 11]);
        assert!(reorder.waiting());
        assert!(reorder.expire(now + Duration::from_millis(100)).is_empty(), "still waiting for 12");
        assert_eq!(reorder.push(12, 12, now + Duration::from_millis(150)), [12, 13, 14, 15]);
        assert!(!reorder.waiting());
        assert_eq!(feed(&mut reorder, &[16, 17], now), [16, 17]);
        assert!(reorder.push(12, 12, now).is_empty(), "a late duplicate is dropped");
    }

    #[test]
    fn two_gaps_are_filled_one_after_the_other() {
        let mut reorder = PacketReorder::new(Duration::from_millis(400));
        let now = Instant::now();
        assert_eq!(feed(&mut reorder, &[1, 3, 5, 6], now), [1]);
        assert_eq!(reorder.push(2, 2, now), [2, 3]);
        assert!(reorder.waiting());
        assert_eq!(reorder.push(4, 4, now), [4, 5, 6]);
        assert_eq!(feed(&mut reorder, &[7], now), [7]);
    }

    #[test]
    fn a_gap_that_is_not_filled_in_time_is_passed_on() {
        let mut reorder = PacketReorder::new(Duration::from_millis(400));
        let now = Instant::now();
        assert_eq!(feed(&mut reorder, &[100, 102, 103], now), [100]);
        assert_eq!(reorder.expire(now + Duration::from_millis(401)), [102, 103]);
        assert!(!reorder.waiting());
        assert_eq!(feed(&mut reorder, &[104, 105], now), [104, 105], "the stream goes on after the gap");
        assert!(reorder.push(101, 101, now).is_empty(), "the lost packet arriving later is dropped");
    }

    #[test]
    fn numbering_wraps_around() {
        let mut reorder = PacketReorder::new(Duration::from_millis(400));
        let now = Instant::now();
        assert_eq!(feed(&mut reorder, &[65534, 65535, 1, 2], now), [65534, 65535]);
        assert_eq!(reorder.push(0, 0, now), [0, 1, 2]);
        assert_eq!(feed(&mut reorder, &[3], now), [3]);
    }
}
