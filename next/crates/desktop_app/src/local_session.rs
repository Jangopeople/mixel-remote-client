//! Isolated attended-media QA: two actual UDP/DTLS peers, process-local pinned
//! identities and the app's own fixture. This is never customer enrollment.
use core_protocol::{
    crypto::{Context, Handshake, Role},
    media::{H264Assembler, H264VideoSender, TrackRemoteEvent},
    session::Session,
    transport::{NativeDataChannel, NativeDataChannelEvent, NativeTransport, local_fingerprint},
};
use ed25519_dalek::SigningKey;
use rand_core::{OsRng, RngCore};
use serde::Serialize;
use std::{
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread::JoinHandle,
    time::{Duration, Instant},
};
use zeroize::Zeroizing;

#[derive(Clone, Serialize)]
pub struct Status {
    pub request: u64,
    pub phase: String,
    pub message: String,
    pub received: u64,
}
type VideoReceiver = Arc<Mutex<mpsc::Receiver<Zeroizing<Vec<u8>>>>>;
struct Inner {
    status: Status,
    cancel: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
    deadline: Option<Instant>,
    video: Option<VideoReceiver>,
}
#[derive(Clone)]
pub struct LocalSession(Arc<Mutex<Inner>>);
impl Default for LocalSession {
    fn default() -> Self {
        Self(Arc::new(Mutex::new(Inner {
            status: Status {
                request: 0,
                phase: "ready".into(),
                message: "Local attended-media test is ready".into(),
                received: 0,
            },
            cancel: Arc::new(AtomicBool::new(false)),
            worker: None,
            deadline: None,
            video: None,
        })))
    }
}
impl LocalSession {
    pub fn status(&self) -> Result<Status, String> {
        let mut inner = self.0.lock().map_err(|_| "Session state failed")?;
        if inner.status.phase == "pending" && inner.deadline.is_some_and(|d| Instant::now() >= d) {
            inner.status.phase = "expired".into();
            inner.status.message =
                "Invitation expired; a fresh request and Accept are required".into();
            inner.deadline = None;
        }
        Ok(inner.status.clone())
    }
    pub fn request(&self) -> Result<Status, String> {
        let (worker, status) = {
            let mut inner = self.0.lock().map_err(|_| "Session state failed")?;
            if matches!(
                inner.status.phase.as_str(),
                "pending" | "negotiating" | "active"
            ) {
                return Err("An invitation or session is already open".into());
            }
            inner.cancel.store(true, Ordering::Release);
            inner.video = None;
            inner.status.request = inner
                .status
                .request
                .checked_add(1)
                .ok_or("Request counter exhausted")?;
            inner.status.phase = "pending".into();
            inner.status.message =
                "Waiting for the host's explicit Accept; no screen capture or media has started"
                    .into();
            inner.status.received = 0;
            inner.cancel = Arc::new(AtomicBool::new(false));
            inner.deadline = Some(Instant::now() + core_protocol::session::CONSENT_LIFETIME);
            (inner.worker.take(), inner.status.clone())
        };
        if let Some(worker) = worker {
            worker.join().map_err(|_| "Prior media worker failed")?;
        }
        Ok(status)
    }
    pub fn decide(&self, request: u64, accept: bool) -> Result<(), String> {
        let mut inner = self.0.lock().map_err(|_| "Session state failed")?;
        if inner.status.request != request
            || inner.status.phase != "pending"
            || inner.deadline.is_none_or(|d| Instant::now() >= d)
        {
            return Err("Invitation is no longer pending".into());
        }
        inner.deadline = None;
        if !accept {
            inner.status.phase = "rejected".into();
            inner.status.message = "Host rejected the request; no capture or media started".into();
            return Ok(());
        }
        inner.status.phase = "negotiating".into();
        inner.status.message =
            "Accepted; verifying both identities and actual DTLS certificates".into();
        let (tx, rx) = mpsc::sync_channel(4);
        inner.video = Some(Arc::new(Mutex::new(rx)));
        let cancel = inner.cancel.clone();
        let state = self.clone();
        let worker = std::thread::Builder::new()
            .name("mixel-attended-media".into())
            .spawn(move || {
                let result = tokio::runtime::Builder::new_multi_thread()
                    .worker_threads(2)
                    .enable_all()
                    .build()
                    .map_err(|e| e.to_string())
                    .and_then(|runtime| runtime.block_on(run(state.clone(), request, cancel, tx)));
                if let Err(error) = result {
                    state.update(request, "error", &error);
                } else {
                    state.update(
                        request,
                        "closed",
                        "Session stopped; native capture and both peer transports closed",
                    );
                }
            })
            .map_err(|e| {
                inner.status.phase = "error".into();
                inner.video = None;
                e.to_string()
            })?;
        inner.worker = Some(worker);
        Ok(())
    }
    fn update(&self, request: u64, phase: &str, message: &str) {
        if let Ok(mut inner) = self.0.lock()
            && inner.status.request == request
            && !inner.cancel.load(Ordering::Acquire)
        {
            inner.status.phase = phase.into();
            inner.status.message = message.into();
        }
    }
    pub fn stop(&self) -> Result<(), String> {
        let worker = {
            let mut inner = self.0.lock().map_err(|_| "Session state failed")?;
            inner.cancel.store(true, Ordering::Release);
            inner.video = None;
            inner.status.phase = "closed".into();
            inner.status.message = "Session stopped; retained media cleared".into();
            inner.worker.take()
        };
        if let Some(worker) = worker {
            worker.join().map_err(|_| "Media worker failed")?;
        }
        Ok(())
    }
    pub fn next_video(&self, request: u64) -> Result<Vec<u8>, String> {
        let video = {
            let inner = self.0.lock().map_err(|_| "Session state failed")?;
            if inner.status.request != request || inner.status.phase != "active" {
                return Ok(vec![]);
            }
            inner.video.clone().ok_or("Media receiver unavailable")?
        };
        let packet = match video
            .lock()
            .map_err(|_| "Media receiver failed")?
            .recv_timeout(Duration::from_millis(250))
        {
            Ok(packet) => packet,
            Err(mpsc::RecvTimeoutError::Timeout) => return Ok(vec![]),
            Err(mpsc::RecvTimeoutError::Disconnected) => return Ok(vec![]),
        };
        let inner = self.0.lock().map_err(|_| "Session state failed")?;
        if inner.status.request != request || inner.status.phase != "active" {
            return Ok(vec![]);
        }
        Ok(packet.to_vec())
    }
}
async fn opened(channel: &Arc<dyn NativeDataChannel>) -> Result<(), String> {
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            match channel.poll().await {
                Some(NativeDataChannelEvent::OnOpen) => return Ok(()),
                None => return Err("Control transport closed".into()),
                _ => {}
            }
        }
    })
    .await
    .map_err(|_| "Control transport timed out")?
}
async fn run(
    state: LocalSession,
    request: u64,
    cancel: Arc<AtomicBool>,
    tx: mpsc::SyncSender<Zeroizing<Vec<u8>>>,
) -> Result<(), String> {
    let mut host = NativeTransport::new("127.0.0.1:0".into(), vec![])
        .await
        .map_err(|e| e.to_string())?;
    let mut viewer = match NativeTransport::new("127.0.0.1:0".into(), vec![]).await {
        Ok(peer) => peer,
        Err(error) => {
            let _ = host.close().await;
            return Err(error.to_string());
        }
    };
    let result = connected(&state, request, &cancel, tx, &mut host, &mut viewer).await;
    let host_closed = host.close().await.map_err(|e| e.to_string());
    let viewer_closed = viewer.close().await.map_err(|e| e.to_string());
    result.and(host_closed).and(viewer_closed)
}
async fn connected(
    state: &LocalSession,
    request: u64,
    cancel: &Arc<AtomicBool>,
    tx: mpsc::SyncSender<Zeroizing<Vec<u8>>>,
    host: &mut NativeTransport,
    viewer: &mut NativeTransport,
) -> Result<(), String> {
    let sender = H264VideoSender::new(host, 640, 420, 30)
        .await
        .map_err(|e| e.to_string())?;
    let channel = host
        .peer
        .create_data_channel("mixel-control-v1", None)
        .await
        .map_err(|e| e.to_string())?;
    let host_key = SigningKey::generate(&mut OsRng);
    let controller_key = SigningKey::generate(&mut OsRng);
    let mut session_id = [0; 32];
    OsRng.fill_bytes(&mut session_id);
    let context = Context {
        session_id,
        controller: controller_key.verifying_key(),
        host: host_key.verifying_key(),
    };
    let now = Instant::now();
    let mut consent = Session::for_peer(now, context.clone(), Role::Host, false);
    consent.decide(true, now).map_err(|e| e.to_string())?;
    let offer = host
        .local_sdp(
            host.peer
                .create_offer(None)
                .await
                .map_err(|e| e.to_string())?,
        )
        .await
        .map_err(|e| e.to_string())?;
    let hf = local_fingerprint(&offer.sdp).map_err(|e| e.to_string())?;
    viewer
        .peer
        .set_remote_description(offer)
        .await
        .map_err(|e| e.to_string())?;
    let answer = viewer
        .local_sdp(
            viewer
                .peer
                .create_answer(None)
                .await
                .map_err(|e| e.to_string())?,
        )
        .await
        .map_err(|e| e.to_string())?;
    let vf = local_fingerprint(&answer.sdp).map_err(|e| e.to_string())?;
    host.peer
        .set_remote_description(answer)
        .await
        .map_err(|e| e.to_string())?;
    let remote = tokio::time::timeout(Duration::from_secs(10), viewer.incoming_channels.recv())
        .await
        .map_err(|_| "Incoming channel timed out")?
        .ok_or("Incoming channel closed")?;
    let (a, b) = tokio::join!(opened(&channel), opened(&remote));
    a?;
    b?;
    let hh =
        Handshake::new(context.clone(), Role::Host, &host_key, hf).map_err(|e| e.to_string())?;
    let ch = Handshake::new(context, Role::Controller, &controller_key, vf)
        .map_err(|e| e.to_string())?;
    let hello_h = hh.hello();
    let hello_c = ch.hello();
    let host_control = hh
        .finish(
            &hello_c,
            host.observed_fingerprint()
                .await
                .map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
    let _controller = ch
        .finish(
            &hello_h,
            viewer
                .observed_fingerprint()
                .await
                .map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
    consent
        .authenticated_transport_ready(&host_control.verified_session(), Instant::now())
        .map_err(|e| e.to_string())?;
    if cancel.load(Ordering::Acquire) {
        return Ok(());
    }
    let target = native_os_capture::macos::own_windows()
        .map_err(|e| e.to_string())?
        .into_iter()
        .find(|w| w.title == "Mixel-Remote Capture Fixture")
        .ok_or("Capture fixture is unavailable")?;
    let mut capture =
        native_os_capture::macos::MacStream::start_own_window(target.id, 640, 420, 30)
            .map_err(|e| e.to_string())?;
    let mut encoder =
        native_os_capture::macos_encoder::HardwareH264Encoder::new(640, 420, 30, 2_000_000)
            .map_err(|e| e.to_string())?;
    if !encoder.hardware_active().map_err(|e| e.to_string())? {
        return Err("Hardware H264 is unavailable".into());
    }
    state.update(request, "active", "Accepted · identities and certificates verified · ScreenCaptureKit → hardware H264 → UDP DTLS-SRTP");
    let frames = capture.frames.clone();
    let result = media_loop(
        state,
        request,
        cancel,
        tx,
        MediaPipeline {
            sender: &sender,
            consent: &mut consent,
            frames: &frames,
            encoder: &mut encoder,
            viewer,
        },
    )
    .await;
    consent.close();
    let stopped = capture.stop().map_err(|e| e.to_string());
    result.and(stopped)
}

struct MediaPipeline<'a> {
    sender: &'a H264VideoSender,
    consent: &'a mut Session,
    frames: &'a Arc<native_os_capture::stream::FrameMailbox>,
    encoder: &'a mut native_os_capture::macos_encoder::HardwareH264Encoder,
    viewer: &'a mut NativeTransport,
}
async fn media_loop(
    state: &LocalSession,
    request: u64,
    cancel: &Arc<AtomicBool>,
    tx: mpsc::SyncSender<Zeroizing<Vec<u8>>>,
    pipeline: MediaPipeline<'_>,
) -> Result<(), String> {
    let MediaPipeline {
        sender,
        consent,
        frames,
        encoder,
        viewer,
    } = pipeline;
    let mut sequence = 0;
    let mut assembler = H264Assembler::default();
    let mut track = None;
    let mut received = 0u64;
    let started = Instant::now();
    while !cancel.load(Ordering::Acquire) {
        let next = frames.next(sequence, Duration::from_millis(100));
        match next {
            Ok((next_sequence, frame)) => {
                sequence = next_sequence;
                let encoded = encoder
                    .encode(&frame, started.elapsed().as_micros() as u64, received == 0)
                    .map_err(|e| e.to_string())?;
                sender
                    .send(consent, &encoded.annex_b, Duration::from_micros(33333))
                    .await
                    .map_err(|e| e.to_string())?;
            }
            Err(native_os_capture::stream::CaptureError::Timeout) => {}
            Err(error) => return Err(error.to_string()),
        }
        if track.is_none() {
            track = match tokio::time::timeout(
                Duration::from_millis(100),
                viewer.incoming_tracks.recv(),
            )
            .await
            {
                Ok(Some(track)) => Some(track),
                Ok(None) => return Err("Video transport closed".into()),
                Err(_) => None,
            };
        }
        if let Some(track) = &track {
            loop {
                match tokio::time::timeout(Duration::from_millis(2), track.poll()).await {
                    Ok(Some(TrackRemoteEvent::OnRtpPacket(packet))) => {
                        if let Some(frame) = assembler
                            .push(
                                packet.header.sequence_number,
                                packet.header.timestamp,
                                packet.header.marker,
                                &packet.payload,
                            )
                            .map_err(|e| e.to_string())?
                        {
                            received += 1;
                            let key = frame
                                .windows(5)
                                .any(|w| w[..4] == [0, 0, 0, 1] && w[4] & 0x1f == 5);
                            let mut output = Zeroizing::new(Vec::with_capacity(frame.len() + 17));
                            output.extend(received.to_le_bytes());
                            output.push(u8::from(key));
                            output.extend((received * 33333).to_le_bytes());
                            output.extend_from_slice(&frame);
                            deliver_video(&tx, output, cancel).await?;
                            if let Ok(mut inner) = state.0.lock()
                                && inner.status.request == request
                            {
                                inner.status.received = received;
                            }
                        }
                    }
                    Ok(None) => return Err("Video transport closed".into()),
                    Ok(Some(_)) => {}
                    Err(_) => break,
                }
            }
        }
        if started.elapsed() > Duration::from_secs(5) && received == 0 {
            return Err("No authenticated video arrived".into());
        }
    }
    Ok(())
}

/// Wait with a bounded queue and cancellable backpressure. Holding one encoded
/// access unit pauses the producer; only unencoded raw mailbox frames may skip.
async fn deliver_video(
    tx: &mpsc::SyncSender<Zeroizing<Vec<u8>>>,
    mut packet: Zeroizing<Vec<u8>>,
    cancel: &AtomicBool,
) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if cancel.load(Ordering::Acquire) {
            return Ok(());
        }
        match tx.try_send(packet) {
            Ok(()) => return Ok(()),
            Err(mpsc::TrySendError::Disconnected(_)) => return Err("Viewer closed".into()),
            Err(mpsc::TrySendError::Full(value)) => packet = value,
        }
        if Instant::now() >= deadline {
            return Err("Viewer remained stalled for ten seconds; session stopped without dropping predictive frames".into());
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejected_stale_and_expired_requests_never_start_capture() {
        let state = LocalSession::default();
        let first = state.request().unwrap();
        assert_eq!(first.phase, "pending");
        assert!(state.request().is_err());
        assert!(state.decide(first.request + 1, true).is_err());
        state.decide(first.request, false).unwrap();
        assert_eq!(state.status().unwrap().phase, "rejected");
        assert!(state.0.lock().unwrap().worker.is_none());
        let second = state.request().unwrap();
        assert!(state.decide(first.request, true).is_err());
        state.0.lock().unwrap().deadline = Some(Instant::now() - Duration::from_secs(1));
        assert!(state.decide(second.request, true).is_err());
        assert_eq!(state.status().unwrap().phase, "expired");
        assert!(state.next_video(second.request).unwrap().is_empty());
        state.stop().unwrap();
    }
    #[test]
    fn concurrent_requests_cannot_replace_pending_consent() {
        let state = LocalSession::default();
        let barrier = Arc::new(std::sync::Barrier::new(8));
        let workers: Vec<_> = (0..8)
            .map(|_| {
                let state = state.clone();
                let barrier = barrier.clone();
                std::thread::spawn(move || {
                    barrier.wait();
                    state.request().is_ok()
                })
            })
            .collect();
        let accepted = workers
            .into_iter()
            .filter_map(|t| t.join().unwrap().then_some(()))
            .count();
        assert_eq!(accepted, 1);
        assert_eq!(state.status().unwrap().request, 1);
        state.stop().unwrap();
    }
    #[tokio::test]
    async fn backpressure_preserves_frame_order_and_cancels_without_a_reader() {
        let (tx, rx) = mpsc::sync_channel(1);
        tx.send(Zeroizing::new(vec![1])).unwrap();
        let cancel = Arc::new(AtomicBool::new(false));
        let flag = cancel.clone();
        let producer = tokio::spawn(async move {
            deliver_video(&tx, Zeroizing::new(vec![2]), &flag)
                .await
                .unwrap();
            deliver_video(&tx, Zeroizing::new(vec![3]), &flag)
                .await
                .unwrap();
        });
        tokio::time::sleep(Duration::from_millis(30)).await;
        assert!(!producer.is_finished());
        assert_eq!(&*rx.recv().unwrap(), &[1]);
        tokio::time::sleep(Duration::from_millis(20)).await;
        assert_eq!(&*rx.recv().unwrap(), &[2]);
        cancel.store(true, Ordering::Release);
        tokio::time::timeout(Duration::from_millis(100), producer)
            .await
            .unwrap()
            .unwrap();
        assert!(rx.try_recv().is_err());
    }
}
