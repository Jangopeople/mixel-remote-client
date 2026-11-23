//! Pictures on their way to the window. Decoded pictures: only the latest is kept,
//! older ones may be dropped. Compressed pictures (when the window decodes them
//! itself): all are kept in order, because each one builds on the previous.
use native_os_capture::stream::CapturedFrame;
use std::sync::Mutex;
use uuid::Uuid;
use zeroize::Zeroizing;

pub struct VideoPacket {
    pub session: Uuid,
    /// The transport that delivered the picture (the same session keeps its ID when its
    /// transport is renewed).
    pub transport: Uuid,
    pub bytes: Zeroizing<Vec<u8>>,
}
impl VideoPacket {
    pub fn from_transport(mut self, transport: Uuid) -> Self {
        self.transport = transport;
        self
    }
    /// The sequence number every packet carries at bytes 16..24 ("MRF2" and "MRH1").
    fn raw_sequence(&self) -> Option<u64> {
        let field = self.bytes.get(16..24)?;
        Some(u64::from_le_bytes(field.try_into().ok()?))
    }
    fn renumber(&mut self, offset: u64) -> Option<u64> {
        let field = self.bytes.get_mut(16..24)?;
        let sequence = u64::from_le_bytes(field.try_into().ok()?).checked_add(offset)?;
        field.copy_from_slice(&sequence.to_le_bytes());
        Some(sequence)
    }
}
#[derive(Default)]
struct Slot {
    session: Option<Uuid>,
    /// The transport whose pictures are shown; the first one to deliver for the session.
    publisher: Option<Uuid>,
    /// A renewed transport of the same session that takes over with its first picture.
    successor: Option<Uuid>,
    /// Added to the successor's sequence numbers: the window sees one stream whose numbers grow.
    offset: u64,
    last_sequence: u64,
    latest: Option<VideoPacket>,
    closed: bool,
    /// The window asked for compressed pictures (it has its own video decoder).
    compressed: bool,
    queue: std::collections::VecDeque<VideoPacket>,
    queued_bytes: usize,
    /// Lab builds only: pictures handed to the window per second and the longest gap between two
    /// of them, across transport renewals (the window sees one stream).
    #[cfg(feature = "qa-session")]
    qa: QaGaps,
}
#[cfg(feature = "qa-session")]
#[derive(Default)]
struct QaGaps {
    last: Option<std::time::Instant>,
    second: Option<std::time::Instant>,
    count: u32,
    longest: u128,
}
#[cfg(feature = "qa-session")]
impl QaGaps {
    fn path() -> Option<std::ffi::OsString> {
        let mut path = std::env::var_os("MIXEL_QA_VIDEO_LOG")?;
        path.push(".gaps");
        Some(path)
    }
    /// One line per second (wall clock in ms): pictures handed over and the longest gap before one.
    fn shown(&mut self) {
        let now = std::time::Instant::now();
        if let Some(last) = self.last {
            self.longest = self.longest.max(now.duration_since(last).as_millis());
        }
        self.last = Some(now);
        self.count += 1;
        let second = *self.second.get_or_insert(now);
        if now.duration_since(second) >= std::time::Duration::from_secs(1) {
            Self::line(&format!("shown/s={} maxgap={}ms", self.count, self.longest));
            (self.second, self.count, self.longest) = (Some(now), 0, 0);
        }
    }
    pub(crate) fn line(text: &str) {
        let Some(path) = Self::path() else { return };
        let clock = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |since| since.as_millis());
        if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
            use std::io::Write;
            let _ = writeln!(file, "{clock} {text}");
        }
    }
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
        (slot.publisher, slot.successor, slot.offset, slot.last_sequence) = (None, None, 0, 0);
        #[cfg(feature = "qa-session")]
        QaGaps::line(&format!("activate {session}"));
        Ok(())
    }
    pub fn clear(&self) {
        if let Ok(mut slot) = self.0.lock() {
            slot.latest = None;
            slot.queue.clear();
            slot.queued_bytes = 0;
            slot.compressed = false;
            slot.session = None;
            (slot.publisher, slot.successor) = (None, None);
        }
    }
    pub fn close(&self) {
        if let Ok(mut slot) = self.0.lock() {
            slot.latest = None;
            slot.queue.clear();
            slot.queued_bytes = 0;
            slot.session = None;
            (slot.publisher, slot.successor) = (None, None);
            slot.closed = true;
        }
    }
    /// The session's transport is being renewed: the running transport keeps delivering until the
    /// renewed one delivers its first picture; from then on only the renewed one is shown.
    pub fn expect_successor(&self, session: Uuid, transport: Uuid) -> Result<(), String> {
        let mut slot = self.0.lock().map_err(|_| "Video state failed")?;
        if slot.closed || slot.session != Some(session) {
            return Err("This video session has stopped".into());
        }
        slot.successor = Some(transport);
        #[cfg(feature = "qa-session")]
        QaGaps::line(&format!("renewal begins: transport {transport}"));
        Ok(())
    }
    /// Admits a picture of the shown session from its current transport or from the renewed
    /// one (which takes over), numbered on from the pictures before.
    fn admit(slot: &mut Slot, packet: &mut VideoPacket) -> Result<(), String> {
        if slot.closed || slot.session != Some(packet.session) {
            return Err("This video session has stopped".into());
        }
        match slot.publisher {
            Some(current) if current == packet.transport => {}
            _ if slot.successor == Some(packet.transport) => {
                slot.publisher = slot.successor.take();
                slot.offset = slot.last_sequence;
                #[cfg(feature = "qa-session")]
                QaGaps::line(&format!("handover: transport {}", packet.transport));
            }
            None if packet.transport == packet.session => slot.publisher = Some(packet.transport),
            _ => return Err("This video session has stopped".into()),
        }
        // Early-video -> main-video handover (or decoder switch) runs on the same transport with a
        // fresh 1-based counter; bump `offset` so sequence numbers delivered to the UI stay strictly
        // increasing instead of tripping out-of-order validation and closing the session.
        if packet
            .raw_sequence()
            .is_some_and(|raw| raw.saturating_add(slot.offset) <= slot.last_sequence)
        {
            slot.offset = slot.last_sequence;
        }
        slot.last_sequence = packet.renumber(slot.offset).ok_or("Video sequence exhausted")?;
        Ok(())
    }
    /// The window says, with every fetch, whether it decodes compressed pictures itself.
    pub fn window_decodes(&self, session: Uuid, compressed: bool) {
        if let Ok(mut slot) = self.0.lock()
            && slot.session == Some(session)
            && slot.compressed != compressed
        {
            slot.compressed = compressed;
            if !compressed {
                slot.latest = None;
            }
            slot.queue.clear();
            slot.queued_bytes = 0;
        }
    }
    pub fn wants_compressed(&self) -> bool {
        self.0.lock().is_ok_and(|slot| slot.compressed)
    }
    /// Queues a compressed picture. `false`: the window fell behind, the queue was
    /// emptied and the stream has to restart with a complete picture.
    pub fn publish_compressed(&self, mut packet: VideoPacket) -> Result<bool, String> {
        let mut slot = self.0.lock().map_err(|_| "Video state failed")?;
        Self::admit(&mut slot, &mut packet)?;
        if !slot.compressed {
            return Ok(true);
        }
        if slot.queue.len() >= MAX_QUEUE || slot.queued_bytes + packet.bytes.len() > MAX_QUEUE_BYTES {
            slot.queue.clear();
            slot.queued_bytes = 0;
            return Ok(false);
        }
        // The first compressed picture supersedes the early/raw fallback. Otherwise
        // draining the compressed queue repaints an older screen (and possibly its old size).
        slot.latest = None;
        slot.queued_bytes += packet.bytes.len();
        slot.queue.push_back(packet);
        #[cfg(feature = "qa-session")]
        slot.qa.shown();
        Ok(true)
    }
    pub fn publish(&self, mut packet: VideoPacket) -> Result<(), String> {
        let mut slot = self.0.lock().map_err(|_| "Video state failed")?;
        Self::admit(&mut slot, &mut packet)?;
        slot.latest = Some(packet);
        #[cfg(feature = "qa-session")]
        slot.qa.shown();
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
    Ok(VideoPacket { session, transport: session, bytes })
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
        // Landscape up to 1920x1088, or the same picture upright (a phone screen).
        || frame.width().max(frame.height()) > 1920
        || frame.width().min(frame.height()) > 1088
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
        for pixel in bytes[start..].as_chunks_mut::<4>().0 {
            pixel.swap(0, 2);
            pixel[3] = 255;
        }
    }
    Ok(VideoPacket { session, transport: session, bytes })
}

#[cfg(test)]
mod handover_tests {
    use super::*;
    fn picture(session: Uuid, transport: Uuid, sequence: u64) -> VideoPacket {
        compressed_packet(session, sequence, sequence * 3000, &[0, 0, 0, 1, 0x65, 1], sequence == 1)
            .unwrap()
            .from_transport(transport)
    }
    fn sequence(bytes: &[u8]) -> u64 {
        u64::from_le_bytes(bytes[16..24].try_into().unwrap())
    }

    #[test]
    fn a_renewed_transport_takes_over_with_its_first_picture_and_numbers_go_on() {
        let mailbox = VideoMailbox::default();
        let session = Uuid::from_u128(1);
        let renewed = Uuid::from_u128(2);
        mailbox.activate(session).unwrap();
        for number in 1..=5 {
            mailbox.publish(picture(session, session, number)).unwrap();
        }
        assert_eq!(sequence(&mailbox.take(session).unwrap()), 5);
        mailbox.expect_successor(session, renewed).unwrap();
        // The running transport keeps the picture going while the renewed one connects.
        mailbox.publish(picture(session, session, 6)).unwrap();
        assert_eq!(sequence(&mailbox.take(session).unwrap()), 6);
        // The renewed transport's first picture takes over, numbered on from the last one shown.
        mailbox.publish(picture(session, renewed, 1)).unwrap();
        assert_eq!(sequence(&mailbox.take(session).unwrap()), 7);
        mailbox.publish(picture(session, renewed, 2)).unwrap();
        assert_eq!(sequence(&mailbox.take(session).unwrap()), 8);
        // From then on the old transport is refused (it stops itself on that error).
        assert!(mailbox.publish(picture(session, session, 7)).is_err());
        assert!(mailbox.take(session).unwrap().is_empty());
    }

    #[test]
    fn only_the_announced_transport_may_take_over_and_compressed_pictures_follow_the_same_rule() {
        let mailbox = VideoMailbox::default();
        let session = Uuid::from_u128(1);
        let stranger = Uuid::from_u128(9);
        mailbox.activate(session).unwrap();
        // Nothing but the session's own first transport delivers before a renewal is announced.
        assert!(mailbox.publish(picture(session, stranger, 1)).is_err());
        mailbox.window_decodes(session, true);
        assert!(mailbox.publish_compressed(picture(session, session, 1)).unwrap());
        assert!(mailbox.publish_compressed(picture(session, stranger, 1)).is_err());
        let renewed = Uuid::from_u128(2);
        mailbox.expect_successor(session, renewed).unwrap();
        assert!(mailbox.publish_compressed(picture(session, stranger, 1)).is_err());
        assert!(mailbox.publish_compressed(picture(session, renewed, 1)).unwrap());
        assert_eq!(sequence(&mailbox.take(session).unwrap()), 1);
        assert_eq!(sequence(&mailbox.take(session).unwrap()), 2);
        // Another session's pictures and renewals are refused.
        let other = Uuid::from_u128(3);
        assert!(mailbox.expect_successor(other, stranger).is_err());
        assert!(mailbox.publish(picture(other, other, 1)).is_err());
    }

    #[test]
    fn compressed_handover_does_not_replay_an_older_raw_picture() {
        let mailbox = VideoMailbox::default();
        let session = Uuid::from_u128(1);
        mailbox.activate(session).unwrap();
        // The early/raw decoder has a picture while the window enables its own decoder.
        mailbox.publish(picture(session, session, 1)).unwrap();
        mailbox.window_decodes(session, true);
        // Keep the early picture available until compressed video actually arrives.
        assert!(mailbox.0.lock().unwrap().latest.is_some());
        assert!(mailbox.publish_compressed(picture(session, session, 2)).unwrap());
        assert_eq!(sequence(&mailbox.take(session).unwrap()), 2);
        assert!(mailbox.take(session).unwrap().is_empty(), "never paint the stale raw picture after the new compressed one");
    }

    #[test]
    fn a_new_session_starts_numbering_and_ownership_afresh() {
        let mailbox = VideoMailbox::default();
        let (first, renewed, next) = (Uuid::from_u128(1), Uuid::from_u128(2), Uuid::from_u128(3));
        mailbox.activate(first).unwrap();
        mailbox.publish(picture(first, first, 4)).unwrap();
        mailbox.expect_successor(first, renewed).unwrap();
        mailbox.activate(next).unwrap();
        assert!(mailbox.publish(picture(next, renewed, 1)).is_err(), "a renewal of the session before does not carry over");
        mailbox.publish(picture(next, next, 1)).unwrap();
        assert_eq!(sequence(&mailbox.take(next).unwrap()), 1);
        mailbox.clear();
        assert!(mailbox.expect_successor(next, renewed).is_err());
    }

    #[test]
    fn same_transport_counter_restart_keeps_sequences_monotonic() {
        let mailbox = VideoMailbox::default();
        let session = Uuid::from_u128(42);
        mailbox.activate(session).unwrap();
        // Early-video loop delivers frames 1, 2 on the session's transport.
        mailbox.publish(picture(session, session, 1)).unwrap();
        assert_eq!(sequence(&mailbox.take(session).unwrap()), 1);
        mailbox.publish(picture(session, session, 2)).unwrap();
        assert_eq!(sequence(&mailbox.take(session).unwrap()), 2);
        // Main video loop starts on the same transport with a fresh 1-based counter.
        mailbox.publish(picture(session, session, 1)).unwrap();
        assert_eq!(sequence(&mailbox.take(session).unwrap()), 3);
        mailbox.publish(picture(session, session, 2)).unwrap();
        assert_eq!(sequence(&mailbox.take(session).unwrap()), 4);
        mailbox.publish(picture(session, session, 3)).unwrap();
        assert_eq!(sequence(&mailbox.take(session).unwrap()), 5);
    }
}
