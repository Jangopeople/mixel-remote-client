//! Remote sound. A Windows host records what its speakers play and sends it as
//! small frames in the control channel; the technician's app plays them.
use core_protocol::sound::{self, FRAME_SAMPLES, RATE, Resampler, State};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use std::{
    collections::VecDeque,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc,
    },
    time::Duration,
};

/// Host: the system sound of this computer as coded frames.
pub struct Capture {
    frames: mpsc::Receiver<Vec<u8>>,
    stop: Arc<AtomicBool>,
}
impl Capture {
    /// Starts recording what this computer plays (Windows). `None` when it has no speakers.
    pub fn start() -> Option<Self> {
        if !cfg!(target_os = "windows") {
            return None;
        }
        let (sender, frames) = mpsc::sync_channel::<Vec<u8>>(50);
        let (ready_tx, ready_rx) = mpsc::channel::<bool>();
        let stop = Arc::new(AtomicBool::new(false));
        let stopped = stop.clone();
        std::thread::spawn(move || {
            let stream = (|| {
                // On Windows, recording from an output device records what it plays.
                let device = cpal::default_host().default_output_device()?;
                let config = device.default_output_config().ok()?;
                let mut resampler = Resampler::new(config.channels(), config.sample_rate().0)?;
                let mut pending: Vec<i16> = Vec::with_capacity(FRAME_SAMPLES * 4);
                let mut state = State::default();
                let mut consume = move |input: &[f32]| {
                    resampler.push(input, &mut pending);
                    while pending.len() >= FRAME_SAMPLES {
                        let samples: Vec<i16> = pending.drain(..FRAME_SAMPLES).collect();
                        if !sound::silent(&samples)
                            && let Some(frame) = sound::encode(&mut state, &samples)
                        {
                            // A full queue means the session is busy: drop rather than lag.
                            let _ = sender.try_send(frame);
                        }
                    }
                };
                let failed = |_| {};
                let stream = match config.sample_format() {
                    cpal::SampleFormat::F32 => device.build_input_stream(
                        &config.into(),
                        move |data: &[f32], _: &cpal::InputCallbackInfo| consume(data),
                        failed,
                        None,
                    ),
                    cpal::SampleFormat::I16 => device.build_input_stream(
                        &config.into(),
                        move |data: &[i16], _: &cpal::InputCallbackInfo| {
                            let converted: Vec<f32> = data.iter().map(|sample| f32::from(*sample) / 32768.0).collect();
                            consume(&converted);
                        },
                        failed,
                        None,
                    ),
                    _ => return None,
                }
                .ok()?;
                stream.play().ok()?;
                Some(stream)
            })();
            let _ = ready_tx.send(stream.is_some());
            while stream.is_some() && !stopped.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(100));
            }
        });
        ready_rx
            .recv_timeout(Duration::from_secs(5))
            .ok()
            .filter(|ready| *ready)
            .map(|_| Self { frames, stop })
    }
    pub fn next(&self) -> Option<Vec<u8>> {
        self.frames.try_recv().ok()
    }
}
impl Drop for Capture {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
    }
}

/// Technician: plays the frames received from the host on the default speakers.
pub struct Player {
    queue: Arc<Mutex<VecDeque<i16>>>,
    stop: Arc<AtomicBool>,
    pub frames: Arc<AtomicU64>,
}
impl Player {
    pub fn start() -> Option<Self> {
        let queue = Arc::new(Mutex::new(VecDeque::<i16>::with_capacity(RATE as usize)));
        let (ready_tx, ready_rx) = mpsc::channel::<bool>();
        let stop = Arc::new(AtomicBool::new(false));
        let (source, stopped) = (queue.clone(), stop.clone());
        std::thread::spawn(move || {
            let stream = (|| {
                let device = cpal::default_host().default_output_device()?;
                let config = device.default_output_config().ok()?;
                let channels = usize::from(config.channels());
                let step = f64::from(RATE) / f64::from(config.sample_rate().0);
                let (mut position, mut current, mut started) = (1.0f64, 0.0f32, false);
                let mut next = move || -> f32 {
                    while position >= 1.0 {
                        position -= 1.0;
                        let mut queue = source.lock().unwrap_or_else(|error| error.into_inner());
                        // Start after a short buffer, so small network pauses are not heard.
                        if !started && queue.len() < FRAME_SAMPLES * 3 {
                            current = 0.0;
                            continue;
                        }
                        match queue.pop_front() {
                            Some(sample) => {
                                started = true;
                                current = f32::from(sample) / 32768.0;
                            }
                            None => {
                                started = false;
                                current = 0.0;
                            }
                        }
                    }
                    position += step;
                    current
                };
                let failed = |_| {};
                let stream = match config.sample_format() {
                    cpal::SampleFormat::F32 => device.build_output_stream(
                        &config.into(),
                        move |data: &mut [f32], _: &cpal::OutputCallbackInfo| {
                            for frame in data.chunks_mut(channels) {
                                let value = next();
                                frame.fill(value);
                            }
                        },
                        failed,
                        None,
                    ),
                    cpal::SampleFormat::I16 => device.build_output_stream(
                        &config.into(),
                        move |data: &mut [i16], _: &cpal::OutputCallbackInfo| {
                            for frame in data.chunks_mut(channels) {
                                let value = (next() * 32767.0) as i16;
                                frame.fill(value);
                            }
                        },
                        failed,
                        None,
                    ),
                    _ => return None,
                }
                .ok()?;
                stream.play().ok()?;
                Some(stream)
            })();
            let _ = ready_tx.send(stream.is_some());
            while stream.is_some() && !stopped.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(100));
            }
        });
        ready_rx
            .recv_timeout(Duration::from_secs(5))
            .ok()
            .filter(|ready| *ready)
            .map(|_| Self { queue, stop, frames: Arc::new(AtomicU64::new(0)) })
    }
    /// Queues one received frame; at most about one second waits to be played.
    pub fn push(&self, frame: &[u8]) {
        let Some(samples) = sound::decode(frame) else {
            return;
        };
        self.frames.fetch_add(1, Ordering::Relaxed);
        let mut queue = self.queue.lock().unwrap_or_else(|error| error.into_inner());
        if queue.len() > RATE as usize {
            queue.clear();
        }
        queue.extend(samples);
    }
}
impl Drop for Player {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
    }
}
