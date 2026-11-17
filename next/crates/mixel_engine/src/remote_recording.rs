//! A session grant owns at most one local, video-only MP4 writer at a time.
//! The peer thread only queues accepted frames. A separate worker owns disk I/O.
use native_os_capture::h264::{AvccAccessUnit, annex_b_to_avcc};
use serde::Serialize;
use std::{
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc::{Receiver, SyncSender, TrySendError, sync_channel},
    },
    thread::JoinHandle,
    time::{Duration, Instant},
};
use tempfile::NamedTempFile;
use uuid::Uuid;
use zeroize::Zeroizing;

type Authorization = Arc<dyn Fn() -> bool + Send + Sync>;
const TIMESCALE: u32 = 90_000;
const MAX_TICKS: u64 = TIMESCALE as u64 * 60 * 60 * 4;
const MAX_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const MAX_SAMPLES: u32 = 250_000;

#[derive(Clone, Serialize)]
#[serde(tag = "phase", content = "message", rename_all = "snake_case")]
pub enum View {
    Ready,
    WaitingForVideo,
    Recording,
    Saving,
    Saved,
    SavedPartial,
    Error(String),
}

struct Frame {
    annex_b: Zeroizing<Vec<u8>>,
    ticks: u64,
    size: (u16, u16),
    arrived: Instant,
    /// The scope (transport) the picture came from; a change of it is a renewal or reconnect: the
    /// recording goes on and the timeline continues by the wall clock.
    segment: u64,
}

struct Job {
    output: Option<SyncSender<Frame>>,
    view: Arc<Mutex<View>>,
    partial: Arc<AtomicBool>,
    stopped: Arc<Mutex<Option<Instant>>>,
    /// Set when the stream under a running recording was replaced (renewal) or its picture size changed:
    /// the next picture sent is a key picture, the writer skips anything before it.
    awaiting_key: Arc<AtomicBool>,
    /// The files of this recording, in order: the chosen name, then `<name>-part2.mp4`, ...
    parts: Arc<Mutex<Vec<PathBuf>>>,
}

impl Job {
    fn finish(&mut self) {
        let Some(output) = self.output.take() else {
            return;
        };
        if let Ok(mut stopped) = self.stopped.lock() {
            *stopped = Some(Instant::now());
        }
        if let Ok(mut view) = self.view.lock()
            && matches!(*view, View::WaitingForVideo | View::Recording)
        {
            *view = View::Saving;
        }
        drop(output);
    }
}

struct Grant {
    session: Uuid,
    /// The scope that holds this grant. A session keeps its ID when its transport is renewed:
    /// the scope of the transport before must not finish or feed the renewed one's grant.
    scope: u64,
    live: Authorization,
    job: Option<Job>,
}

#[derive(Clone, Default)]
pub struct RecordingHub {
    grant: Arc<Mutex<Vec<Grant>>>,
    workers: Arc<Mutex<Vec<JoinHandle<()>>>>,
}

pub struct RecordingScope {
    pub hub: RecordingHub,
    pub session: Uuid,
    scope: u64,
}
static NEXT_SCOPE: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

impl Drop for RecordingScope {
    fn drop(&mut self) {
        if let Ok(mut entry) = self.hub.grant.lock()
            && let Some(grant) = entry.iter_mut().find(|grant| grant.scope == self.scope)
            && let Some(job) = grant.job.as_mut()
        {
            job.finish();
        }
    }
}

impl RecordingHub {
    pub fn attach(&self, session: Uuid, live: Authorization) -> Result<RecordingScope, String> {
        let mut entry = self.grant.lock().map_err(|_| "Recording is unavailable")?;
        // One grant per session. An ended one stays while its recording still shows a result.
        entry.retain(|grant| (grant.live)() || grant.job.is_some());
        while entry.len() >= 8 {
            entry.remove(0);
        }
        if !live() || entry.iter().any(|grant| grant.session == session && (grant.live)()) {
            return Err("This recording permission is no longer available".into());
        }
        // A recording of the grant before (a transport of this session that was just replaced) is
        // finished, never dropped unfinished.
        // A recording that is still running goes on under the renewed transport (same file, or the next
        // part when the picture size changes); a finished one is dropped from the list as before.
        let mut carried = None;
        for before in entry.iter_mut().filter(|grant| grant.session == session) {
            if let Some(job) = before.job.take() {
                if job.output.is_some() {
                    job.awaiting_key.store(true, Ordering::Release);
                    carried = Some(job);
                }
            }
        }
        entry.retain(|grant| grant.session != session);
        let scope = NEXT_SCOPE.fetch_add(1, Ordering::Relaxed);
        entry.push(Grant {
            session,
            scope,
            live,
            job: carried,
        });
        Ok(RecordingScope {
            hub: self.clone(),
            session,
            scope,
        })
    }

    pub fn view(&self, session: Uuid) -> Option<View> {
        let entry = self.grant.lock().ok()?;
        let grant = entry
            .iter()
            .find(|grant| grant.session == session && (grant.live)())?;
        grant.job.as_ref().map_or(Some(View::Ready), |job| {
            job.view.lock().ok().map(|view| view.clone())
        })
    }

    pub fn may_start(&self, session: Uuid) -> bool {
        matches!(
            self.view(session),
            Some(View::Ready | View::Saved | View::SavedPartial | View::Error(_))
        )
    }

    pub fn start(&self, session: Uuid, destination: PathBuf) -> Result<(), String> {
        if destination
            .try_exists()
            .map_err(|_| "Could not check that recording filename")?
        {
            return Err("Choose a new filename for the recording".into());
        }
        let mut workers = self
            .workers
            .lock()
            .map_err(|_| "Recording workers are unavailable")?;
        workers.retain(|worker| !worker.is_finished());
        if workers.len() >= 4 {
            return Err("Wait for a previous recording to finish saving".into());
        }
        let mut entry = self.grant.lock().map_err(|_| "Recording is unavailable")?;
        let grant = entry
            .iter_mut()
            .find(|grant| grant.session == session && (grant.live)())
            .ok_or("This recording permission is no longer available")?;
        if grant.job.as_ref().is_some_and(|job| {
            !job.view.lock().is_ok_and(|view| {
                matches!(*view, View::Saved | View::SavedPartial | View::Error(_))
            })
        }) {
            return Err("A recording is already running or saving".into());
        }
        let (output, input) = sync_channel(8);
        let view = Arc::new(Mutex::new(View::WaitingForVideo));
        let result = view.clone();
        let partial = Arc::new(AtomicBool::new(false));
        let worker_partial = partial.clone();
        let stopped = Arc::new(Mutex::new(None));
        let worker_stopped = stopped.clone();
        let awaiting_key = Arc::new(AtomicBool::new(false));
        let worker_key = awaiting_key.clone();
        let parts = Arc::new(Mutex::new(Vec::new()));
        let worker_parts = parts.clone();
        let worker = std::thread::Builder::new()
            .name("mixel-session-recording".into())
            .spawn(move || {
                let next = match save(&destination, input, &result, &worker_stopped, &worker_key, &worker_parts) {
                    Ok(limited) if limited || worker_partial.load(Ordering::Acquire) => {
                        View::SavedPartial
                    }
                    Ok(_) => View::Saved,
                    Err(error) => View::Error(error),
                };
                if let Ok(mut state) = result.lock() {
                    *state = next;
                }
            })
            .map_err(|_| "The recording writer could not start")?;
        workers.push(worker);
        grant.job = Some(Job {
            output: Some(output),
            view,
            partial,
            stopped,
            awaiting_key,
            parts,
        });
        Ok(())
    }

    pub fn stop(&self, session: Uuid) -> Result<(), String> {
        let mut entry = self.grant.lock().map_err(|_| "Recording is unavailable")?;
        let job = entry
            .iter_mut()
            .find(|grant| grant.session == session)
            .and_then(|grant| grant.job.as_mut())
            .ok_or("There is no recording to stop")?;
        job.finish();
        Ok(())
    }

    /// The files of the session's current or last recording (more than one when the picture size changed).
    pub fn parts(&self, session: Uuid) -> Vec<String> {
        let Ok(entry) = self.grant.lock() else { return Vec::new() };
        entry
            .iter()
            .find(|grant| grant.session == session)
            .and_then(|grant| grant.job.as_ref())
            .and_then(|job| job.parts.lock().ok().map(|parts| parts.iter().map(|path| path.to_string_lossy().into_owned()).collect()))
            .unwrap_or_default()
    }

    pub fn last_view(&self) -> Option<View> {
        let entry = self.grant.lock().ok()?;
        // The most recent recording of any session.
        let grant = entry.iter().rev().find(|grant| grant.job.is_some())?;
        let view = grant.job.as_ref()?.view.lock().ok()?.clone();
        (!matches!(view, View::Ready)).then_some(view)
    }

    pub async fn finish(&self) -> Result<(), String> {
        let workers = self.workers.clone();
        tokio::task::spawn_blocking(move || {
            let mut workers = workers
                .lock()
                .map_err(|_| "Recording workers are unavailable")?;
            let deadline = Instant::now() + Duration::from_secs(20);
            while workers.iter().any(|worker| !worker.is_finished()) {
                if Instant::now() >= deadline {
                    return Err("A recording is still saving".into());
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            for worker in workers.drain(..) {
                worker.join().map_err(|_| "A recording writer failed")?;
            }
            Ok(())
        })
        .await
        .map_err(|_| "Recording cleanup failed")?
    }
}

impl RecordingScope {
    pub fn needs_keyframe(&self) -> bool {
        self.hub.grant.lock().is_ok_and(|entry| entry.iter().any(|grant| grant.scope == self.scope))
            && (matches!(self.hub.view(self.session), Some(View::WaitingForVideo))
                || self.hub.grant.lock().is_ok_and(|entry| {
                    entry.iter().any(|grant| {
                        grant.scope == self.scope
                            && grant.job.as_ref().is_some_and(|job| job.output.is_some() && job.awaiting_key.load(Ordering::Acquire))
                    })
                }))
    }
    pub fn submit(&self, annex_b: &[u8], ticks: u64, size: (u16, u16)) {
        let Ok(mut entry) = self.hub.grant.lock() else {
            return;
        };
        let Some(grant) = entry.iter_mut().find(|grant| grant.scope == self.scope) else {
            return;
        };
        let live = (grant.live)();
        let Some(job) = grant.job.as_mut() else {
            return;
        };
        let Some(output) = &job.output else { return };
        if !live {
            job.finish();
            return;
        }
        let queued = output.try_send(Frame {
            annex_b: Zeroizing::new(annex_b.to_vec()),
            ticks,
            size,
            arrived: Instant::now(),
            segment: self.scope,
        });
        if matches!(queued, Err(TrySendError::Full(_))) {
            job.partial.store(true, Ordering::Release);
        }
        if queued.is_err() {
            // Finalize the already-accepted prefix if disk falls behind or consent ends.
            job.finish();
        }
    }
}

/// How one file of a recording ended.
enum PartEnd {
    /// The recording is over (stopped, consent ended, or a size/time limit was reached).
    Done { limited: bool },
    /// The picture size or the video parameters changed: the recording goes on in the next file,
    /// starting with this picture.
    Next(Frame),
}

/// `<folder>/<name>.mp4` for part 1, `<folder>/<name>-part2.mp4`, ... for the following ones.
fn part_path(destination: &Path, number: u32) -> PathBuf {
    if number <= 1 {
        return destination.to_path_buf();
    }
    let stem = destination.file_stem().map(|stem| stem.to_string_lossy().into_owned()).unwrap_or_default();
    destination.with_file_name(format!("{stem}-part{number}.mp4"))
}

fn save(
    destination: &Path,
    input: Receiver<Frame>,
    view: &Mutex<View>,
    stopped: &Mutex<Option<Instant>>,
    awaiting_key: &AtomicBool,
    parts: &Mutex<Vec<PathBuf>>,
) -> Result<bool, String> {
    let mut number = 1;
    let mut carried: Option<Frame> = None;
    let mut any_limit = false;
    loop {
        let path = part_path(destination, number);
        match save_part(&path, &input, view, stopped, awaiting_key, carried.take())? {
            PartEnd::Done { limited } => {
                any_limit |= limited;
                if let Ok(mut list) = parts.lock() {
                    list.push(path);
                }
                return Ok(any_limit);
            }
            PartEnd::Next(frame) => {
                if let Ok(mut list) = parts.lock() {
                    list.push(path);
                }
                // The next file starts with the first key picture of the new size.
                awaiting_key.store(true, Ordering::Release);
                carried = Some(frame);
                number += 1;
            }
        }
    }
}

fn save_part(
    destination: &Path,
    input: &Receiver<Frame>,
    view: &Mutex<View>,
    stopped: &Mutex<Option<Instant>>,
    awaiting_key: &AtomicBool,
    carried: Option<Frame>,
) -> Result<PartEnd, String> {
    let parent = destination.parent().ok_or("Choose a recording filename")?;
    if !destination.is_absolute()
        || !destination
            .extension()
            .is_some_and(|ext| ext.eq_ignore_ascii_case("mp4"))
    {
        return Err("Choose a filename ending in .mp4".into());
    }
    let mut candidate = carried;
    let (first, first_frame, first_segment) = loop {
        let frame = match candidate.take() {
            Some(frame) => frame,
            None => input
                .recv()
                .map_err(|_| "Recording ended before a complete video frame arrived")?,
        };
        let access =
            annex_b_to_avcc(&frame.annex_b).map_err(|_| "The recording received invalid video")?;
        if access.keyframe
            && let (Some(sps), Some(pps)) = (access.sps, access.pps)
            && sps.len() >= 4
        {
            break (
                (
                    frame.ticks,
                    frame.size,
                    sps.to_vec(),
                    pps.to_vec(),
                    frame.arrived,
                ),
                access.bytes,
                frame.segment,
            );
        }
    };
    awaiting_key.store(false, Ordering::Release);
    let (start, size, sps, pps, mut last_arrived) = first;
    let file = tempfile::Builder::new()
        .prefix(".mixel-recording-")
        .suffix(".part")
        .tempfile_in(parent)
        .map_err(|_| "Could not create a private recording in that folder")?;
    let track = mp4::TrackConfig {
        timescale: TIMESCALE,
        ..mp4::AvcConfig {
            width: size.0,
            height: size.1,
            seq_param_set: sps.clone(),
            pic_param_set: pps.clone(),
        }
        .into()
    };
    let config = mp4::Mp4Config {
        major_brand: "isom".parse().expect("valid fixed MP4 brand"),
        minor_version: 512,
        compatible_brands: ["isom", "iso2", "avc1", "mp41"]
            .map(|brand| brand.parse().expect("valid fixed MP4 brand"))
            .to_vec(),
        timescale: 1_000,
    };
    let mut writer = mp4::Mp4Writer::write_start(file, &config)
        .map_err(|_| "Could not initialize the MP4 recording")?;
    writer
        .add_track(&track)
        .map_err(|_| "Could not create the MP4 video track")?;
    if let Ok(mut state) = view.lock()
        && matches!(*state, View::WaitingForVideo)
    {
        *state = View::Recording;
    }
    let mut pending = mp4::Mp4Sample {
        start_time: 0,
        duration: 0,
        rendering_offset: 0,
        is_sync: true,
        bytes: first_frame.to_vec().into(),
    };
    let mut length = pending.bytes.len() as u64;
    let mut samples = 1;
    let mut last_duration = TIMESCALE / 30;
    let mut limited = false;
    let mut next_part = None;
    // The picture clock of one transport; a renewed transport has its own, so the timeline of the
    // file continues by the wall clock at the change (`base` is where the segment starts in the file).
    let (mut segment, mut segment_start, mut base) = (first_segment, start, 0u64);
    // After a renewal, pictures before the first key picture of the new transport are skipped (below).
    for frame in input {
        let access =
            annex_b_to_avcc(&frame.annex_b).map_err(|_| "The recording received invalid video")?;
        if frame.segment != segment {
            if !access.keyframe {
                continue;
            }
            let gap = frame.arrived.saturating_duration_since(last_arrived).as_millis() * u128::from(TIMESCALE / 1000);
            let gap = gap.clamp(u128::from(TIMESCALE / 30), u128::from(MAX_TICKS)) as u64;
            base = pending.start_time + gap;
            segment = frame.segment;
            segment_start = frame.ticks;
            awaiting_key.store(false, Ordering::Release);
        }
        if frame.size != size || changed_parameters(&access, &sps, &pps) {
            next_part = Some(frame);
            break;
        }
        let elapsed = base + frame.ticks.saturating_sub(segment_start);
        if elapsed <= pending.start_time {
            continue;
        }
        if elapsed >= MAX_TICKS || samples >= MAX_SAMPLES {
            limited = true;
            break;
        }
        length += access.bytes.len() as u64;
        if length > MAX_BYTES {
            limited = true;
            break;
        }
        last_duration = u32::try_from(elapsed - pending.start_time)
            .map_err(|_| "Invalid recording timestamp")?;
        pending.duration = last_duration;
        writer
            .write_sample(1, &pending)
            .map_err(|_| "Could not write the recording video")?;
        pending = mp4::Mp4Sample {
            start_time: elapsed,
            duration: 0,
            rendering_offset: 0,
            is_sync: access.keyframe,
            bytes: access.bytes.to_vec().into(),
        };
        last_arrived = frame.arrived;
        samples += 1;
    }
    let tail = if limited || next_part.is_some() {
        u64::from(last_duration.clamp(1, TIMESCALE))
    } else {
        let ended = stopped
            .lock()
            .ok()
            .and_then(|value| *value)
            .unwrap_or(last_arrived);
        let wall_ticks = ended.saturating_duration_since(last_arrived).as_millis()
            * u128::from(TIMESCALE / 1000);
        wall_ticks.clamp(u128::from(TIMESCALE / 30), u128::from(MAX_TICKS)) as u64
    };
    pending.duration = tail.min(MAX_TICKS - pending.start_time).max(1) as u32;
    writer
        .write_sample(1, &pending)
        .and_then(|_| writer.write_end())
        .map_err(|_| "Could not finish the MP4 recording")?;
    let file: NamedTempFile = writer.into_writer();
    file.as_file()
        .sync_all()
        .map_err(|_| "Could not flush the recording to disk")?;
    file.persist_noclobber(destination)
        .map_err(|_| "Could not save the recording. Choose a new filename in a writable folder.")?;
    Ok(match next_part {
        Some(frame) => PartEnd::Next(frame),
        None => PartEnd::Done { limited },
    })
}

fn changed_parameters(access: &AvccAccessUnit<'_>, sps: &[u8], pps: &[u8]) -> bool {
    access.sps.is_some_and(|value| value != sps) || access.pps.is_some_and(|value| value != pps)
}

#[cfg(test)]
mod renewal_tests {
    use super::*;

    fn key(sps: u8) -> Vec<u8> {
        vec![0, 0, 0, 1, 0x67, sps, 1, 2, 3, 0, 0, 0, 1, 0x68, 4, 5, 0, 0, 0, 1, 0x65, 9, 9, 9]
    }
    fn delta() -> Vec<u8> {
        vec![0, 0, 0, 1, 0x41, 7, 7, 7]
    }
    fn samples(path: &std::path::Path) -> (u32, u16, u16) {
        let file = std::fs::File::open(path).unwrap();
        let length = file.metadata().unwrap().len();
        let movie = mp4::Mp4Reader::read_header(std::io::BufReader::new(file), length).unwrap();
        let track = &movie.tracks()[&1];
        (track.sample_count(), track.width(), track.height())
    }

    /// A session keeps its ID when its transport is renewed. The scope of the transport before
    /// may neither finish nor feed the renewed transport's grant; a RUNNING recording goes on under
    /// the renewed transport (same file), skipping pictures until its first key picture.
    #[tokio::test]
    async fn a_running_recording_goes_on_across_a_renewal_in_the_same_file() {
        let directory = tempfile::tempdir().unwrap();
        let hub = RecordingHub::default();
        let session = Uuid::new_v4();
        let old_live = Arc::new(AtomicBool::new(true));
        let allowed = old_live.clone();
        let old = hub.attach(session, Arc::new(move || allowed.load(Ordering::Acquire))).unwrap();
        assert!(hub.attach(session, Arc::new(|| true)).is_err(), "no second grant while the first is live");
        let file = directory.path().join("long.mp4");
        hub.start(session, file.clone()).unwrap();
        assert!(old.needs_keyframe());
        old.submit(&key(1), 0, (2, 2));
        old.submit(&delta(), 3000, (2, 2));
        old.submit(&delta(), 6000, (2, 2));
        std::thread::sleep(Duration::from_millis(50));
        // The old transport was replaced: its authorization ends, the renewed one attaches.
        old_live.store(false, Ordering::Release);
        let renewed = hub.attach(session, Arc::new(|| true)).unwrap();
        assert!(matches!(hub.view(session), Some(View::Recording)), "the recording is still running");
        assert!(!old.needs_keyframe(), "the old scope no longer asks for pictures");
        assert!(renewed.needs_keyframe(), "the renewed transport must send a key picture first");
        old.submit(&delta(), 9000, (2, 2));
        drop(old);
        assert!(matches!(hub.view(session), Some(View::Recording)), "the old scope's end leaves the recording running");
        renewed.submit(&delta(), 50, (2, 2)); // not usable before a key picture
        assert!(renewed.needs_keyframe());
        std::thread::sleep(Duration::from_millis(50));
        renewed.submit(&key(1), 100, (2, 2));
        renewed.submit(&delta(), 3100, (2, 2));
        std::thread::sleep(Duration::from_millis(50));
        assert!(!renewed.needs_keyframe());
        hub.stop(session).unwrap();
        hub.finish().await.unwrap();
        assert!(matches!(hub.last_view(), Some(View::Saved)));
        assert_eq!(samples(&file), (5, 2, 2), "3 pictures before, 2 after the renewal, nothing lost or doubled");
        assert_eq!(hub.parts(session), vec![file.to_string_lossy().into_owned()]);
        drop(renewed);
    }

    /// A change of the picture size (a display switch) starts the next part, named clearly; the
    /// recording itself goes on.
    #[tokio::test]
    async fn a_size_change_starts_the_next_part_automatically() {
        let directory = tempfile::tempdir().unwrap();
        let hub = RecordingHub::default();
        let session = Uuid::new_v4();
        let scope = hub.attach(session, Arc::new(|| true)).unwrap();
        let file = directory.path().join("screen.mp4");
        hub.start(session, file.clone()).unwrap();
        scope.submit(&key(1), 0, (2, 2));
        scope.submit(&delta(), 3000, (2, 2));
        scope.submit(&delta(), 6000, (4, 4)); // size changed, not a key picture: waits
        std::thread::sleep(Duration::from_millis(50));
        scope.submit(&key(2), 9000, (4, 4));
        scope.submit(&delta(), 12000, (4, 4));
        std::thread::sleep(Duration::from_millis(50));
        hub.stop(session).unwrap();
        hub.finish().await.unwrap();
        let second = directory.path().join("screen-part2.mp4");
        assert_eq!(samples(&file), (2, 2, 2));
        assert_eq!(samples(&second), (2, 4, 4));
        assert_eq!(hub.parts(session).len(), 2);
        assert!(matches!(hub.last_view(), Some(View::Saved)));
        drop(scope);
    }
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::*;
    use native_os_capture::{macos_encoder::HardwareH264Encoder, stream::CapturedFrame};
    use std::{fs, io::BufReader};

    #[tokio::test]
    async fn records_native_h264_and_finishes_on_revocation_without_overwriting() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("session.mp4");
        let live = Arc::new(AtomicBool::new(true));
        let session = Uuid::new_v4();
        let hub = RecordingHub::default();
        assert!(hub.start(session, path.clone()).is_err());
        let allowed = live.clone();
        let scope = hub
            .attach(session, Arc::new(move || allowed.load(Ordering::Acquire)))
            .unwrap();
        hub.start(session, path.clone()).unwrap();
        assert!(!path.exists());
        let mut encoder = HardwareH264Encoder::new(320, 240, 30, 1_000_000).unwrap();
        let mut pictures = Vec::new();
        for index in 0..4 {
            let captured =
                CapturedFrame::bgra(320, 240, 1280, vec![45 + index as u8 * 30; 320 * 240 * 4])
                    .unwrap();
            let frame = encoder
                .encode(&captured, index * 33_333, index == 0)
                .unwrap();
            let sample = annex_b_to_avcc(&frame.annex_b).unwrap();
            if index > 0 {
                scope.submit(&frame.annex_b, index * 3_000, (320, 240));
                pictures.push(sample.bytes.to_vec());
            }
        }
        // The writer started after the initial IDR and must wait for another.
        let first = CapturedFrame::bgra(320, 240, 1280, vec![180; 320 * 240 * 4]).unwrap();
        let key = encoder.encode(&first, 133_333, true).unwrap();
        let sample = annex_b_to_avcc(&key.annex_b).unwrap();
        assert!(sample.keyframe && sample.sps.is_some() && sample.pps.is_some());
        pictures.clear();
        pictures.push(sample.bytes.to_vec());
        scope.submit(&key.annex_b, 12_000, (320, 240));
        let second = encoder.encode(&first, 166_666, false).unwrap();
        let sample = annex_b_to_avcc(&second.annex_b).unwrap();
        pictures.push(sample.bytes.to_vec());
        scope.submit(&second.annex_b, 15_000, (320, 240));
        live.store(false, Ordering::Release);
        scope.submit(&second.annex_b, 18_000, (320, 240));
        drop(scope);
        assert!(hub.view(session).is_none());
        hub.finish().await.unwrap();
        assert!(matches!(hub.last_view(), Some(View::Saved)));

        let file = fs::File::open(&path).unwrap();
        let length = file.metadata().unwrap().len();
        let mut movie = mp4::Mp4Reader::read_header(BufReader::new(file), length).unwrap();
        let video = &movie.tracks()[&1];
        assert_eq!((video.width(), video.height()), (320, 240));
        assert_eq!(video.sample_count(), 2);
        for (index, expected) in pictures.iter().enumerate() {
            let sample = movie.read_sample(1, index as u32 + 1).unwrap().unwrap();
            assert_eq!(sample.bytes.as_ref(), expected);
            assert_eq!(sample.is_sync, index == 0);
        }
        let bytes = fs::read(&path).unwrap();
        let replacement = Uuid::new_v4();
        let _fresh = hub.attach(replacement, Arc::new(|| true)).unwrap();
        assert!(hub.start(session, path.clone()).is_err());
        assert!(hub.start(replacement, path.clone()).is_err());
        assert_eq!(fs::read(&path).unwrap(), bytes);
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
        if let Some(proof) = std::env::var_os("MIXEL_RECORDING_TEST_OUTPUT") {
            use std::io::Write;
            let mut output = fs::OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(proof)
                .unwrap();
            output.write_all(&bytes).unwrap();
        }
        println!(
            "PASS: native H.264 MP4 retained two exact samples, excluded the revoked frame, and preserved the existing destination"
        );
    }
}
