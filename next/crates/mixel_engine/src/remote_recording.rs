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
}

struct Job {
    output: Option<SyncSender<Frame>>,
    view: Arc<Mutex<View>>,
    partial: Arc<AtomicBool>,
    stopped: Arc<Mutex<Option<Instant>>>,
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
    live: Authorization,
    job: Option<Job>,
}

#[derive(Clone, Default)]
pub struct RecordingHub {
    grant: Arc<Mutex<Option<Grant>>>,
    workers: Arc<Mutex<Vec<JoinHandle<()>>>>,
}

pub struct RecordingScope {
    pub hub: RecordingHub,
    pub session: Uuid,
}

impl Drop for RecordingScope {
    fn drop(&mut self) {
        if let Ok(mut entry) = self.hub.grant.lock()
            && let Some(grant) = entry.as_mut().filter(|grant| grant.session == self.session)
            && let Some(job) = grant.job.as_mut()
        {
            job.finish();
        }
    }
}

impl RecordingHub {
    pub fn attach(&self, session: Uuid, live: Authorization) -> Result<RecordingScope, String> {
        let mut entry = self.grant.lock().map_err(|_| "Recording is unavailable")?;
        if !live() || entry.as_ref().is_some_and(|grant| (grant.live)()) {
            return Err("This recording permission is no longer available".into());
        }
        *entry = Some(Grant {
            session,
            live,
            job: None,
        });
        Ok(RecordingScope {
            hub: self.clone(),
            session,
        })
    }

    pub fn view(&self, session: Uuid) -> Option<View> {
        let entry = self.grant.lock().ok()?;
        let grant = entry
            .as_ref()
            .filter(|grant| grant.session == session && (grant.live)())?;
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
        if !workers.is_empty() {
            return Err("Wait for the previous recording to finish saving".into());
        }
        let mut entry = self.grant.lock().map_err(|_| "Recording is unavailable")?;
        let grant = entry
            .as_mut()
            .filter(|grant| grant.session == session && (grant.live)())
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
        let worker = std::thread::Builder::new()
            .name("mixel-session-recording".into())
            .spawn(move || {
                let next = match save(&destination, input, &result, &worker_stopped) {
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
        });
        Ok(())
    }

    pub fn stop(&self, session: Uuid) -> Result<(), String> {
        let mut entry = self.grant.lock().map_err(|_| "Recording is unavailable")?;
        let job = entry
            .as_mut()
            .filter(|grant| grant.session == session)
            .and_then(|grant| grant.job.as_mut())
            .ok_or("There is no recording to stop")?;
        job.finish();
        Ok(())
    }

    pub fn last_view(&self) -> Option<View> {
        let entry = self.grant.lock().ok()?;
        let grant = entry.as_ref()?;
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
        matches!(self.hub.view(self.session), Some(View::WaitingForVideo))
    }
    pub fn submit(&self, annex_b: &[u8], ticks: u64, size: (u16, u16)) {
        let Ok(mut entry) = self.hub.grant.lock() else {
            return;
        };
        let Some(grant) = entry.as_mut().filter(|grant| grant.session == self.session) else {
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

fn save(
    destination: &Path,
    input: Receiver<Frame>,
    view: &Mutex<View>,
    stopped: &Mutex<Option<Instant>>,
) -> Result<bool, String> {
    let parent = destination.parent().ok_or("Choose a recording filename")?;
    if !destination.is_absolute()
        || !destination
            .extension()
            .is_some_and(|ext| ext.eq_ignore_ascii_case("mp4"))
    {
        return Err("Choose a filename ending in .mp4".into());
    }
    let (first, first_frame) = loop {
        let frame = input
            .recv()
            .map_err(|_| "Recording ended before a complete video frame arrived")?;
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
            );
        }
    };
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
    for frame in input {
        let elapsed = frame
            .ticks
            .checked_sub(start)
            .filter(|&ticks| ticks > pending.start_time);
        let Some(elapsed) = elapsed else { continue };
        if elapsed >= MAX_TICKS || samples >= MAX_SAMPLES || frame.size != size {
            limited = true;
            break;
        }
        let access =
            annex_b_to_avcc(&frame.annex_b).map_err(|_| "The recording received invalid video")?;
        if changed_parameters(&access, &sps, &pps) {
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
    let tail = if limited {
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
    Ok(limited)
}

fn changed_parameters(access: &AvccAccessUnit<'_>, sps: &[u8], pps: &[u8]) -> bool {
    access.sps.is_some_and(|value| value != sps) || access.pps.is_some_and(|value| value != pps)
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
