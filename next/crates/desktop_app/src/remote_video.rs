//! Pictures on their way to the window. Decoded pictures: only the latest is kept,
//! older ones may be dropped. Compressed pictures (when the window decodes them
//! itself): all are kept in order, because each one builds on the previous.
use native_os_capture::stream::CapturedFrame;
use std::sync::Mutex;
use uuid::Uuid;
use zeroize::Zeroizing;

pub struct VideoPacket {
    pub session: Uuid,
    pub bytes: Zeroizing<Vec<u8>>,
}
#[derive(Default)]
struct Slot {
    session: Option<Uuid>,
    latest: Option<VideoPacket>,
    closed: bool,
    /// The window asked for compressed pictures (it has its own video decoder).
    compressed: bool,
    queue: std::collections::VecDeque<VideoPacket>,
    queued_bytes: usize,
}
const MAX_QUEUE: usize = 90;
const MAX_QUEUE_BYTES: usize = 12 * 1024 * 1024;
#[derive(Default)]
pub struct VideoMailbox(Mutex<Slot>);
impl VideoMailbox {
    pub fn activate(&self, session: Uuid) -> Result<(), String> {
        let mut slot = self.0.lock().map_err(|_| "Video state failed")?;
        if slot.closed {
            return Err("Video delivery is closed".into());
        }
        slot.latest = None;
        slot.queue.clear();
        slot.queued_bytes = 0;
        slot.compressed = false;
        slot.session = Some(session);
        Ok(())
    }
    pub fn clear(&self) {
        if let Ok(mut slot) = self.0.lock() {
            slot.latest = None;
            slot.queue.clear();
            slot.queued_bytes = 0;
            slot.compressed = false;
            slot.session = None;
        }
    }
    pub fn close(&self) {
        if let Ok(mut slot) = self.0.lock() {
            slot.latest = None;
            slot.queue.clear();
            slot.queued_bytes = 0;
            slot.session = None;
            slot.closed = true;
        }
    }
    /// The window says, with every fetch, whether it decodes compressed pictures itself.
    pub fn window_decodes(&self, session: Uuid, compressed: bool) {
        if let Ok(mut slot) = self.0.lock()
            && slot.session == Some(session)
            && slot.compressed != compressed
        {
            slot.compressed = compressed;
            slot.latest = None;
            slot.queue.clear();
            slot.queued_bytes = 0;
        }
    }
    pub fn wants_compressed(&self) -> bool {
        self.0.lock().is_ok_and(|slot| slot.compressed)
    }
    /// Queues a compressed picture. `false`: the window fell behind, the queue was
    /// emptied and the stream has to restart with a complete picture.
    pub fn publish_compressed(&self, packet: VideoPacket) -> Result<bool, String> {
        let mut slot = self.0.lock().map_err(|_| "Video state failed")?;
        if slot.closed || slot.session != Some(packet.session) {
            return Err("This video session has stopped".into());
        }
        if !slot.compressed {
            return Ok(true);
        }
        if slot.queue.len() >= MAX_QUEUE || slot.queued_bytes + packet.bytes.len() > MAX_QUEUE_BYTES {
            slot.queue.clear();
            slot.queued_bytes = 0;
            return Ok(false);
        }
        slot.queued_bytes += packet.bytes.len();
        slot.queue.push_back(packet);
        Ok(true)
    }
    pub fn publish(&self, packet: VideoPacket) -> Result<(), String> {
        let mut slot = self.0.lock().map_err(|_| "Video state failed")?;
        if slot.closed || slot.session != Some(packet.session) {
            return Err("This video session has stopped".into());
        }
        slot.latest = Some(packet);
        Ok(())
    }
    pub fn take(&self, session: Uuid) -> Result<Vec<u8>, String> {
        let mut slot = self.0.lock().map_err(|_| "Video state failed")?;
        if slot.closed || slot.session != Some(session) {
            return Ok(Vec::new());
        }
        if let Some(mut packet) = slot.queue.pop_front() {
            slot.queued_bytes = slot.queued_bytes.saturating_sub(packet.bytes.len());
            return Ok(std::mem::take(&mut *packet.bytes));
        }
        Ok(slot
            .latest
            .take()
            .map(|mut packet| std::mem::take(&mut *packet.bytes))
            .unwrap_or_default())
    }
}
/// A compressed picture for the window's own decoder: "MRH1", flags (1 = complete
/// picture), two unused fields, sequence, timestamp, then the H.264 access unit.
pub fn compressed_packet(
    session: Uuid,
    sequence: u64,
    timestamp: u64,
    annex_b: &[u8],
    keyframe: bool,
) -> Result<VideoPacket, String> {
    if annex_b.is_empty() || annex_b.len() > 4 * 1024 * 1024 || sequence == 0 {
        return Err("Compressed video exceeds its bounds".into());
    }
    let mut bytes = Zeroizing::new(Vec::with_capacity(32 + annex_b.len()));
    bytes.extend(b"MRH1");
    bytes.extend(u32::from(keyframe).to_le_bytes());
    bytes.extend([0u8; 8]);
    bytes.extend(sequence.to_le_bytes());
    bytes.extend(timestamp.to_le_bytes());
    bytes.extend_from_slice(annex_b);
    Ok(VideoPacket { session, bytes })
}
/// Lab builds only: pictures the window fetched since the last log line.
#[cfg(feature = "qa-session")]
pub static QA_FETCHED: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
pub fn decoded_packet(
    session: Uuid,
    sequence: u64,
    timestamp: u64,
    frame: &CapturedFrame,
) -> Result<VideoPacket, String> {
    if frame.width() < 2
        || frame.height() < 2
        || frame.width() > 1920
        || frame.height() > 1088
        || sequence == 0
        || frame.pixels().len() > 16_777_216
    {
        return Err("Decoded video exceeds its negotiated bounds".into());
    }
    let mut bytes = Zeroizing::new(Vec::with_capacity(32 + frame.pixels().len()));
    // "MRF2": tightly packed RGBA rows, ready to draw; the window does no per-pixel work.
    bytes.extend(b"MRF2");
    bytes.extend((frame.width() as u32).to_le_bytes());
    bytes.extend((frame.height() as u32).to_le_bytes());
    bytes.extend((frame.width() as u32 * 4).to_le_bytes());
    bytes.extend(sequence.to_le_bytes());
    bytes.extend(timestamp.to_le_bytes());
    let row = frame.width() * 4;
    for line in frame.pixels().chunks(frame.stride()).take(frame.height()) {
        let start = bytes.len();
        bytes.extend_from_slice(line.get(..row).ok_or("Decoded video row is incomplete")?);
        for pixel in bytes[start..].chunks_exact_mut(4) {
            pixel.swap(0, 2);
            pixel[3] = 255;
        }
    }
    Ok(VideoPacket { session, bytes })
}
