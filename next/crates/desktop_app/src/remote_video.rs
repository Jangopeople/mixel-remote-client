//! Keep the latest independently decoded image. Superseded raw frames may be
//! discarded; predictive compressed access units must still all reach decoder.
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
}
#[derive(Default)]
pub struct VideoMailbox(Mutex<Slot>);
impl VideoMailbox {
    pub fn activate(&self, session: Uuid) -> Result<(), String> {
        let mut slot = self.0.lock().map_err(|_| "Video state failed")?;
        if slot.closed {
            return Err("Video delivery is closed".into());
        }
        slot.latest = None;
        slot.session = Some(session);
        Ok(())
    }
    pub fn clear(&self) {
        if let Ok(mut slot) = self.0.lock() {
            slot.latest = None;
            slot.session = None;
        }
    }
    pub fn close(&self) {
        if let Ok(mut slot) = self.0.lock() {
            slot.latest = None;
            slot.session = None;
            slot.closed = true;
        }
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
        Ok(slot
            .latest
            .take()
            .map(|mut packet| std::mem::take(&mut *packet.bytes))
            .unwrap_or_default())
    }
}
pub fn decoded_packet(
    session: Uuid,
    sequence: u64,
    timestamp: u64,
    frame: &CapturedFrame,
) -> Result<VideoPacket, String> {
    if frame.width() < 2
        || frame.height() < 2
        || frame.width() > 1280
        || frame.height() > 720
        || sequence == 0
        || frame.pixels().len() > 16_777_216
    {
        return Err("Decoded video exceeds its negotiated bounds".into());
    }
    let mut bytes = Zeroizing::new(Vec::with_capacity(32 + frame.pixels().len()));
    bytes.extend(b"MRF1");
    bytes.extend((frame.width() as u32).to_le_bytes());
    bytes.extend((frame.height() as u32).to_le_bytes());
    bytes.extend((frame.stride() as u32).to_le_bytes());
    bytes.extend(sequence.to_le_bytes());
    bytes.extend(timestamp.to_le_bytes());
    bytes.extend_from_slice(frame.pixels());
    Ok(VideoPacket { session, bytes })
}
