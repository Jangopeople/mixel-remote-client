//! One compositor-approved display and, optionally, the associated input devices.
use ashpd::desktop::{
    Session,
    remote_desktop::{Axis, DeviceType, KeyState, RemoteDesktop, SelectDevicesOptions},
    screencast::{CursorMode, Screencast, SelectSourcesOptions, SourceType, Stream},
};
use core_protocol::input::Input;
use futures_util::StreamExt;
use gst::prelude::*;
use gstreamer as gst;
use gstreamer_app::AppSink;
use gstreamer_video::{VideoFrameExt, VideoFrameRef, VideoInfo};
use native_os_capture::{
    input::{InjectionError, Injector},
    stream::{CaptureError, CapturedFrame, FrameMailbox},
};
use std::{
    os::fd::{AsRawFd, OwnedFd},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread::JoinHandle,
    time::{Duration, Instant},
};
use zeroize::Zeroizing;

pub const SOURCE: &str = "wayland:portal";

pub fn available() -> bool {
    std::env::var_os("WAYLAND_DISPLAY").is_some_and(|value| !value.is_empty())
        || std::env::var("XDG_SESSION_TYPE")
            .is_ok_and(|value| value.eq_ignore_ascii_case("wayland"))
}

enum PortalKind {
    View(Session<Screencast>),
    Control(RemoteDesktop, Session<RemoteDesktop>),
}

struct PortalSession {
    kind: Arc<PortalKind>,
    runtime: tokio::runtime::Handle,
}

impl Drop for PortalSession {
    fn drop(&mut self) {
        let kind = self.kind.clone();
        self.runtime.spawn(async move {
            let _ = tokio::time::timeout(Duration::from_secs(3), close(&kind)).await;
        });
    }
}

async fn close(kind: &PortalKind) {
    let _ = match kind {
        PortalKind::View(session) => session.close().await,
        PortalKind::Control(_, session) => session.close().await,
    };
}

pub struct Grant {
    session: PortalSession,
    fd: OwnedFd,
    node: u32,
    serial: Option<u64>,
    size: (usize, usize),
    revoked: Arc<AtomicBool>,
    consent: Arc<AtomicBool>,
}

impl Grant {
    pub async fn open(control: bool, consent: Arc<AtomicBool>) -> Result<Arc<Self>, String> {
        if !available() || consent.load(Ordering::Acquire) {
            return Err("The Wayland sharing invitation is unavailable".into());
        }
        let screencast = Screencast::new().await.map_err(portal_error)?;
        let kind = tokio::time::timeout(Duration::from_secs(5), async {
            if control {
                let remote = RemoteDesktop::new().await?;
                let session = remote.create_session(Default::default()).await?;
                Ok(PortalKind::Control(remote, session))
            } else {
                screencast
                    .create_session(Default::default())
                    .await
                    .map(PortalKind::View)
            }
        })
        .await
        .map_err(|_| "The desktop portal did not respond")?
        .map_err(portal_error)?;
        let session = PortalSession {
            kind: Arc::new(kind),
            runtime: tokio::runtime::Handle::current(),
        };
        let setup = async {
            let cursor = if screencast
                .available_cursor_modes()
                .await
                .is_ok_and(|modes| modes.contains(CursorMode::EMBEDDED))
            {
                CursorMode::EMBEDDED
            } else {
                CursorMode::HIDDEN
            };
            let sources = SelectSourcesOptions::default()
                .set_sources(SourceType::MONITOR)
                .set_multiple(false)
                .set_cursor_mode(cursor);
            let (stream, fd) = match session.kind.as_ref() {
                PortalKind::View(portal) => {
                    screencast
                        .select_sources(portal, sources)
                        .await
                        .map_err(portal_error)?
                        .response()
                        .map_err(portal_error)?;
                    let response = screencast
                        .start(portal, None, Default::default())
                        .await
                        .map_err(portal_error)?
                        .response()
                        .map_err(portal_error)?;
                    let stream = single_monitor(response.streams())?;
                    let fd = screencast
                        .open_pipe_wire_remote(portal, Default::default())
                        .await
                        .map_err(portal_error)?;
                    (stream, fd)
                }
                PortalKind::Control(remote, portal) => {
                    let devices = DeviceType::KEYBOARD | DeviceType::POINTER;
                    remote
                        .select_devices(
                            portal,
                            SelectDevicesOptions::default().set_devices(devices),
                        )
                        .await
                        .map_err(portal_error)?
                        .response()
                        .map_err(portal_error)?;
                    screencast
                        .select_sources(portal, sources)
                        .await
                        .map_err(portal_error)?
                        .response()
                        .map_err(portal_error)?;
                    let response = remote
                        .start(portal, None, Default::default())
                        .await
                        .map_err(portal_error)?
                        .response()
                        .map_err(portal_error)?;
                    if !response.devices().contains(devices) {
                        return Err(
                            "The desktop portal did not grant both keyboard and pointer access"
                                .into(),
                        );
                    }
                    let stream = single_monitor(response.streams())?;
                    let fd = screencast
                        .open_pipe_wire_remote(portal, Default::default())
                        .await
                        .map_err(portal_error)?;
                    (stream, fd)
                }
            };
            Ok::<_, String>((stream, fd))
        };
        let (stream, fd) = tokio::select! {
            result = tokio::time::timeout(Duration::from_secs(65), setup) => {
                result.map_err(|_| "The system sharing confirmation expired")??
            }
            () = cancelled(&consent) => return Err("The local invitation was cancelled".into()),
        };
        let (width, height) = stream
            .size()
            .filter(|&(w, h)| (16..=8192).contains(&w) && (16..=8192).contains(&h))
            .ok_or("The compositor did not provide valid display geometry")?;
        let grant = Arc::new(Self {
            session,
            fd,
            node: stream.pipe_wire_node_id(),
            serial: stream.pipewire_serial(),
            size: (width as usize, height as usize),
            revoked: Arc::new(AtomicBool::new(false)),
            consent,
        });
        grant.watch();
        Ok(grant)
    }

    pub fn size(&self) -> (usize, usize) {
        self.size
    }

    pub fn live(&self) -> bool {
        !self.revoked.load(Ordering::Acquire) && !self.consent.load(Ordering::Acquire)
    }

    fn watch(&self) {
        let kind = self.session.kind.clone();
        let revoked = self.revoked.clone();
        let consent = self.consent.clone();
        self.session.runtime.spawn(async move {
            async fn wait<T: ashpd::desktop::SessionPortal>(
                session: &Session<T>,
                consent: &AtomicBool,
            ) {
                tokio::select! {
                    _ = async {
                        if let Ok(events) = session.receive_closed().await {
                            let mut events = std::pin::pin!(events);
                            let _ = events.next().await;
                        }
                    } => {}
                    () = cancelled(consent) => {
                        let _ = tokio::time::timeout(Duration::from_secs(3), session.close()).await;
                    }
                }
            }
            match kind.as_ref() {
                PortalKind::View(portal) => wait(portal, &consent).await,
                PortalKind::Control(_, portal) => wait(portal, &consent).await,
            }
            revoked.store(true, Ordering::Release);
        });
    }
}

fn single_monitor(streams: &[Stream]) -> Result<Stream, String> {
    match streams {
        [stream]
            if stream.pipe_wire_node_id() != 0
                && stream
                    .source_type()
                    .is_none_or(|kind| kind == SourceType::MONITOR) =>
        {
            Ok(stream.clone())
        }
        _ => Err("The desktop portal did not grant a single display".into()),
    }
}

fn portal_error(error: ashpd::Error) -> String {
    format!("The system sharing request could not be completed ({error})")
}

async fn cancelled(flag: &AtomicBool) {
    while !flag.load(Ordering::Acquire) {
        tokio::time::sleep(Duration::from_millis(75)).await;
    }
}

pub struct Capture {
    pub frames: Arc<FrameMailbox>,
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}

impl Capture {
    pub fn start(grant: Arc<Grant>, size: (usize, usize)) -> Result<Self, String> {
        if !grant.live() {
            return Err("The Wayland screen-sharing permission has expired".into());
        }
        let frames = Arc::new(FrameMailbox::default());
        let stop = Arc::new(AtomicBool::new(false));
        let worker = {
            let frames = frames.clone();
            let stop = stop.clone();
            std::thread::Builder::new()
                .name("mixel-wayland-capture".into())
                .spawn(move || {
                    let reason = capture_loop(&grant, size, &stop, &frames)
                        .err()
                        .unwrap_or(CaptureError::Stopped);
                    frames.close(reason);
                })
                .map_err(|_| "Cannot start the Wayland capture worker")?
        };
        Ok(Self {
            frames,
            stop,
            worker: Some(worker),
        })
    }

    pub fn stop(&mut self) -> Result<(), String> {
        self.stop.store(true, Ordering::Release);
        self.frames.close(CaptureError::Stopped);
        if let Some(worker) = self.worker.take() {
            let deadline = Instant::now() + Duration::from_secs(3);
            while !worker.is_finished() {
                if Instant::now() >= deadline {
                    return Err("The Wayland capture worker did not stop promptly".into());
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            worker
                .join()
                .map_err(|_| "The Wayland capture worker failed")?;
        }
        Ok(())
    }
}

impl Drop for Capture {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

fn capture_loop(
    grant: &Grant,
    (width, height): (usize, usize),
    stop: &AtomicBool,
    frames: &FrameMailbox,
) -> Result<(), CaptureError> {
    let failure = |message: &str| CaptureError::Native(message.into());
    gst::init().map_err(|_| failure("GStreamer is unavailable"))?;
    let target = grant.serial.map_or_else(
        || format!("path={}", grant.node),
        |serial| format!("target-object={serial}"),
    );
    let description = format!(
        "pipewiresrc fd={} {target} keepalive-time=1000 ! queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream ! videoconvert ! videoscale ! video/x-raw,format=BGRA,width={width},height={height} ! appsink name=screen max-buffers=1 drop=true sync=false",
        grant.fd.as_raw_fd(),
    );
    // The PipeWire plugin duplicates this FD for its connection. The grant
    // stays alive until the graph has returned to Null.
    let pipeline = gst::parse::launch(&description)
        .map_err(|_| failure("Install the GStreamer PipeWire and base video plugins"))?
        .downcast::<gst::Pipeline>()
        .map_err(|_| failure("Cannot create the Wayland capture pipeline"))?;
    struct Active(gst::Pipeline);
    impl Drop for Active {
        fn drop(&mut self) {
            let _ = self.0.set_state(gst::State::Null);
        }
    }
    let active = Active(pipeline);
    let sink = active
        .0
        .by_name("screen")
        .and_then(|element| element.downcast::<AppSink>().ok())
        .ok_or_else(|| failure("Cannot create the Wayland frame sink"))?;
    active
        .0
        .set_state(gst::State::Playing)
        .map_err(|_| failure("The PipeWire display stream could not start"))?;
    let startup = Instant::now();
    let mut delivered = false;
    while !stop.load(Ordering::Acquire) && grant.live() {
        let sample = sink.try_pull_sample(gst::ClockTime::from_mseconds(100));
        if !grant.live() {
            return Err(CaptureError::Permission);
        }
        if let Some(sample) = sample {
            let info = VideoInfo::from_caps(sample.caps().ok_or(CaptureError::Target)?)
                .map_err(|_| CaptureError::Target)?;
            if info.width() as usize != width || info.height() as usize != height {
                return Err(CaptureError::Target);
            }
            let frame = VideoFrameRef::from_buffer_ref_readable(
                sample.buffer().ok_or(CaptureError::Target)?,
                &info,
            )
            .map_err(|_| CaptureError::Target)?;
            let plane = frame.plane_data(0).map_err(|_| CaptureError::Target)?;
            let stride =
                usize::try_from(frame.plane_stride()[0]).map_err(|_| CaptureError::Target)?;
            let row = width * 4;
            if stride < row || plane.len() < stride * (height - 1) + row {
                return Err(CaptureError::Target);
            }
            let mut pixels = Zeroizing::new(Vec::with_capacity(row * height));
            for y in 0..height {
                pixels.extend_from_slice(&plane[y * stride..y * stride + row]);
            }
            let frame = CapturedFrame::bgra(width, height, row, std::mem::take(&mut *pixels))
                .map_err(|_| CaptureError::Target)?;
            frames.publish(frame);
            delivered = true;
        } else if sink.is_eos() || !delivered && startup.elapsed() >= Duration::from_secs(8) {
            return Err(failure(
                "The PipeWire display stream delivered no usable frames",
            ));
        }
    }
    Ok(())
}

pub struct PortalInjector {
    grant: Arc<Grant>,
    authorized: Arc<dyn Fn() -> bool + Send + Sync>,
    scroll: [i32; 2],
}

impl PortalInjector {
    pub fn new(
        grant: Arc<Grant>,
        authorized: Arc<dyn Fn() -> bool + Send + Sync>,
    ) -> Result<Self, String> {
        if !grant.live() || !matches!(grant.session.kind.as_ref(), PortalKind::Control(..)) {
            return Err("The compositor did not authorize remote control".into());
        }
        Ok(Self {
            grant,
            authorized,
            scroll: [0; 2],
        })
    }
}

impl Injector for PortalInjector {
    fn inject(&mut self, event: &Input<'_>) -> Result<(), InjectionError> {
        enum Action {
            Key(i32, bool),
            Button(i32, bool),
            Motion(f64, f64),
            Scroll(i32, i32),
        }
        let cleanup = matches!(
            event,
            Input::Key { down: false, .. }
                | Input::Button { down: false, .. }
                | Input::ButtonClick { down: false, .. }
                | Input::ReleaseAll
        );
        if !cleanup && (!self.grant.live() || !(self.authorized)()) {
            return Err(InjectionError::Permission);
        }
        // The compositor releases virtual input devices when it closes a
        // portal session. No other session is allowed to clean up this one's keys.
        if cleanup && !self.grant.live() {
            return Ok(());
        }
        let action = match *event {
            Input::Key { usage, down } => {
                Action::Key(evdev(usage).ok_or(InjectionError::Unsupported)?, down)
            }
            Input::Button { button, down } | Input::ButtonClick { button, down, .. } => {
                Action::Button(
                    *[272, 273, 274, 275, 276]
                        .get(usize::from(button.saturating_sub(1)))
                        .ok_or(InjectionError::Unsupported)?,
                    down,
                )
            }
            Input::Pointer { display: 0, x, y } => {
                let (width, height) = self.grant.size();
                Action::Motion(
                    f64::from(x) * (width - 1) as f64 / 65535.0,
                    f64::from(y) * (height - 1) as f64 / 65535.0,
                )
            }
            Input::Wheel {
                horizontal,
                vertical,
            } => {
                self.scroll[0] += i32::from(horizontal);
                self.scroll[1] -= i32::from(vertical);
                let action = Action::Scroll(self.scroll[0] / 120, self.scroll[1] / 120);
                self.scroll = [self.scroll[0] % 120, self.scroll[1] % 120];
                action
            }
            Input::ReleaseAll => return Ok(()),
            _ => return Err(InjectionError::Unsupported),
        };
        let (reply, wait) = std::sync::mpsc::sync_channel(1);
        let grant = self.grant.clone();
        let authorized = self.authorized.clone();
        let task = self.grant.session.runtime.spawn(async move {
            let result = async {
                if !cleanup && (!grant.live() || !authorized()) {
                    return Err(InjectionError::Permission);
                }
                let PortalKind::Control(remote, portal) = grant.session.kind.as_ref() else {
                    return Err(InjectionError::Permission);
                };
                let state = |down| {
                    if down {
                        KeyState::Pressed
                    } else {
                        KeyState::Released
                    }
                };
                let result = match action {
                    Action::Key(code, down) => {
                        remote
                            .notify_keyboard_keycode(portal, code, state(down), Default::default())
                            .await
                    }
                    Action::Button(button, down) => {
                        remote
                            .notify_pointer_button(portal, button, state(down), Default::default())
                            .await
                    }
                    Action::Motion(x, y) => {
                        remote
                            .notify_pointer_motion_absolute(
                                portal,
                                grant.node,
                                x,
                                y,
                                Default::default(),
                            )
                            .await
                    }
                    Action::Scroll(x, y) => {
                        if x != 0 {
                            remote
                                .notify_pointer_axis_discrete(
                                    portal,
                                    Axis::Horizontal,
                                    x,
                                    Default::default(),
                                )
                                .await
                                .map_err(|_| InjectionError::Permission)?;
                        }
                        if y != 0 {
                            remote
                                .notify_pointer_axis_discrete(
                                    portal,
                                    Axis::Vertical,
                                    y,
                                    Default::default(),
                                )
                                .await
                        } else {
                            Ok(())
                        }
                    }
                };
                result.map_err(|_| InjectionError::Permission)
            }
            .await;
            let _ = reply.send(result);
        });
        wait.recv_timeout(Duration::from_millis(700))
            .unwrap_or_else(|_| {
                task.abort();
                Err(InjectionError::Native(
                    "The desktop portal did not acknowledge input".into(),
                ))
            })
    }
}

fn evdev(usage: u16) -> Option<i32> {
    const LETTERS: [i32; 26] = [
        30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50, 49, 24, 25, 16, 19, 31, 20, 22, 47, 17,
        45, 21, 44,
    ];
    Some(match usage {
        4..=29 => LETTERS[usize::from(usage - 4)],
        30..=39 => i32::from(usage - 28),
        0x28..=0x39 => [
            28, 1, 14, 15, 57, 12, 13, 26, 27, 43, 43, 39, 40, 41, 51, 52, 53, 58,
        ][usize::from(usage - 0x28)],
        0x3a..=0x43 => i32::from(usage - 0x3a) + 59,
        0x44 => 87,
        0x45 => 88,
        0x46..=0x64 => [
            99, 70, 119, 110, 102, 104, 111, 107, 109, 106, 105, 108, 103, 69, 98, 55, 74, 78, 96,
            79, 80, 81, 75, 76, 77, 71, 72, 73, 82, 83, 86,
        ][usize::from(usage - 0x46)],
        0x65 => 127,
        0xe0..=0xe7 => [29, 42, 56, 125, 97, 54, 100, 126][usize::from(usage - 0xe0)],
        _ => return None,
    })
}
