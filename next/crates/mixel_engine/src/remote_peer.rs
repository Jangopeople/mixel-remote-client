//! One real remote peer, driven on an owner thread. ScreenCaptureKit and
//! VideoToolbox objects never migrate between threads or cross webview IPC.
use crate::remote_control::ControlPipe;
use crate::session_platform::{self, ScreenH264Encoder};
use crate::{
    device_identity::IdentityState,
    remote_session::{Command, RemoteSession},
    remote_video::{VideoMailbox, decoded_packet},
};
use core_protocol::{
    crypto::{Context, Handshake, Hello, Role},
    media::{H264Assembler, H264VideoSender, TrackRemoteEvent},
    session::Session,
    transport::{
        NativeDataChannel, NativeDataChannelEvent, NativeIceCandidate, NativeSessionDescription,
        NativeTransport, RemoteTrack, local_fingerprint,
    },
};
use signaling_server::protocol::Signal;
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::sync::mpsc;
use uuid::Uuid;

/// One transport of a session. A session's transport is renewed make-before-break: the running
/// transport keeps sharing until the renewed one takes over (`Config::previous`), so the person
/// at either side sees no gap. Both are confined to the same accepted session and grants.
#[derive(Clone, Default)]
pub struct TransportLink {
    /// Stop this transport: the renewed one took over. The session goes on.
    pub retire: Arc<AtomicBool>,
    /// A renewed transport exists: the end of this one does not end the session.
    pub superseded: Arc<AtomicBool>,
    /// Host: this transport's screen capture is closed (or was never opened).
    pub released: Arc<AtomicBool>,
    /// Technician: the transport is not connected right now (ICE disconnected or failed). When the
    /// connection service then reports the session closed, the computer was lost, not ended: an
    /// unattended session asks again by itself.
    pub lost: Arc<AtomicBool>,
}
impl TransportLink {
    pub fn retire(&self) {
        self.superseded.store(true, Ordering::Release);
        self.retire.store(true, Ordering::Release);
    }
    fn retired(&self) -> bool {
        self.retire.load(Ordering::Acquire)
    }
    /// Host: the transport before stops sharing and closes its capture (a display is captured
    /// once per process); waits for that at most `limit`. `false`: it did not stop in time.
    async fn take_over(&self, limit: Duration) -> bool {
        self.retire();
        let deadline = Instant::now() + limit;
        while !self.released.load(Ordering::Acquire) {
            if Instant::now() >= deadline {
                return false;
            }
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
        true
    }
}
/// Sets a transport's capture as closed when its host media task ends, on every path.
struct Released(Arc<AtomicBool>);
impl Drop for Released {
    fn drop(&mut self) {
        self.0.store(true, Ordering::Release);
    }
}
/// Host: how long a renewed transport waits for the one before to close its capture.
const CAPTURE_HANDOVER: Duration = Duration::from_secs(3);
/// Technician: the transport before stops at the latest this long after the renewed one's
/// picture stream opened, even if no picture came yet.
const PICTURE_HANDOVER: Duration = Duration::from_secs(10);

pub struct Config {
    pub state: RemoteSession,
    pub generation: u64,
    /// The session as the person sees it; it stays the same when its transport is renewed.
    pub session: Uuid,
    /// The connection service's ID of this transport (signalling, end of the transport).
    pub transport: Uuid,
    pub link: TransportLink,
    /// The running transport this one renews: it keeps sharing until this one takes over.
    pub previous: Option<TransportLink>,
    pub nonce: [u8; 32],
    pub host: bool,
    pub login_window: bool,
    pub remote_key: ed25519_dalek::VerifyingKey,
    pub identity: Arc<IdentityState>,
    pub source: String,
    #[cfg(target_os = "linux")]
    pub portal: Option<Arc<crate::wayland_portal::Grant>>,
    pub confirm: bool,
    pub control: bool,
    pub clipboard: bool,
    pub files: bool,
    pub chat: bool,
    pub recording: bool,
    pub consent_id: Uuid,
    pub consent_cancel: Arc<AtomicBool>,
    pub consent_started: Instant,
    pub session_cancel: Arc<AtomicBool>,
    pub account_cancel: Arc<AtomicBool>,
    pub signals: mpsc::Receiver<Signal>,
    pub commands: mpsc::Sender<Command>,
    pub video: Arc<VideoMailbox>,
    /// The technician session this media belongs to (the session id for a host or a colleague).
    pub slot: Uuid,
    pub relay_only: bool,
    pub ice_servers: Vec<core_protocol::transport::NativeIceServer>,
    pub authorization: crate::remote_session::BrokerLease,
    pub peer_expires: Arc<AtomicU64>,
    /// Host: a colleague who joined; sends the first technician's pictures instead of capturing.
    pub mirror: bool,
    /// Technician: a transport that was made ready before Connect (`start_warm`); it takes this session over.
    pub prewarmed: Option<Warm>,
}
fn authorization_live(
    account: &AtomicBool,
    session: &AtomicBool,
    peer: &AtomicU64,
    broker: &crate::remote_session::BrokerLease,
) -> bool {
    if account.load(Ordering::Acquire) || session.load(Ordering::Acquire) {
        return false;
    }
    let live = broker.lock().ok().is_some_and(|lease| {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .is_ok_and(|now| {
                now.as_secs() < lease.0
                    && now.as_secs() < peer.load(Ordering::Acquire)
                    && Instant::now() < lease.1
            })
    });
    if !live {
        // Lab builds only: which authorization ended the session.
        #[cfg(feature = "qa-session")]
        if let Ok(lease) = broker.lock() {
            let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |since| since.as_secs());
            eprintln!("QA authorization ended at clock {}: broker lease {} s left (monotonic {:?}), peer ticket {} s left", crate::remote_control::qa_wall_clock(), lease.0 as i64 - now as i64, lease.1.checked_duration_since(Instant::now()), peer.load(Ordering::Acquire) as i64 - now as i64);
        }
        session.store(true, Ordering::Release);
    }
    live
}
impl Config {
    pub fn stopped(&self) -> bool {
        // Replaced by the renewed transport: this one stops, the session goes on.
        if self.link.retired() {
            return true;
        }
        #[cfg(target_os = "linux")]
        if self.portal.as_ref().is_some_and(|grant| !grant.live()) {
            self.session_cancel.store(true, Ordering::Release);
            return true;
        }
        if self.consent_cancel.load(Ordering::Acquire)
            || (self.login_window && !session_platform::at_login_window())
        {
            self.session_cancel.store(true, Ordering::Release);
            return true;
        }
        !authorization_live(
            &self.account_cancel,
            &self.session_cancel,
            &self.peer_expires,
            &self.authorization,
        )
    }
    pub fn input_authorization(&self) -> Arc<dyn Fn() -> bool + Send + Sync> {
        let account = self.account_cancel.clone();
        let session = self.session_cancel.clone();
        let peer = self.peer_expires.clone();
        let broker = self.authorization.clone();
        let consent = self.consent_cancel.clone();
        let retire = self.link.retire.clone();
        Arc::new(move || {
            if consent.load(Ordering::Acquire) {
                session.store(true, Ordering::Release);
                return false;
            }
            !retire.load(Ordering::Acquire) && authorization_live(&account, &session, &peer, &broker)
        })
    }
    pub fn file_authorization(&self) -> Arc<dyn Fn() -> bool + Send + Sync> {
        let account = self.account_cancel.clone();
        let consent = self.consent_cancel.clone();
        let peer = self.peer_expires.clone();
        let broker = self.authorization.clone();
        let started = self.consent_started;
        // Not bound to this transport: the session's file scope (and its owner thread) outlives a
        // transport renewal, and its end would end the session. Each transport's file network
        // task stops with the transport (`input_authorization`).
        Arc::new(move || {
            if started.elapsed() >= core_protocol::files::LIFETIME {
                consent.store(true, Ordering::Release);
                return false;
            }
            authorization_live(&account, &consent, &peer, &broker)
        })
    }
    async fn signal(&self, signal: Signal) -> Result<(), String> {
        if self.stopped() {
            return Err("Session stopped".into());
        }
        tokio::time::timeout(
            Duration::from_secs(3),
            self.commands.send(Command::Signal {
                session: self.transport,
                signal,
            }),
        )
        .await
        .map_err(|_| "Signaling queue stalled")?
        .map_err(|_| "Connection service closed".to_owned())
    }
}
pub fn start(mut config: Config) -> Result<(), String> {
    // A transport made ready before Connect takes the session over (it has its own thread and
    // runtime, the relay allocation and the offer are in it); one that is gone (it ran out of time or
    // failed) is left out and the session starts the usual way.
    if let Some(warm) = config.prewarmed.take() {
        match warm.adopt(config) {
            Ok(()) => return Ok(()),
            Err(back) => config = *back,
        }
    }
    let state = config.state.clone();
    let cancel = config.account_cancel.clone();
    let mirror = config.mirror;
    let slot = config.slot;
    let renewal = config.previous.is_some();
    state.spawn_media(&cancel, mirror, slot, renewal, move || own_media(config, None, None))
}
/// A renewed transport that fails before it took over (`previous` is still there) does not end the session while
/// the transport before still runs: the session layer goes back to it and asks for the renewal again.
fn old_transport_carries_on(config: &Config, failed: bool) -> bool {
    failed && config.previous.as_ref().is_some_and(|before| !before.released.load(Ordering::Acquire))
}

/// Everything a transport made ready in advance holds: the transport with its relay allocation, the
/// control channel and the offer (candidates in it) made from them.
pub struct WarmParts {
    transport: NativeTransport,
    channel: Arc<dyn NativeDataChannel>,
    offer: String,
}

/// Technician: a transport made ready before Connect. It lives on its own media thread for at most
/// `WARM_LIFETIME`; `adopt` hands the session to that thread, dropping the handle ends it.
pub struct Warm {
    adopt: Option<std::sync::mpsc::Sender<Config>>,
    release: Arc<AtomicBool>,
    started: Instant,
    /// The relay lease it was made with (valid for the session that takes it over too).
    pub lease: signaling_server::turn::IceLease,
}
/// How long a ready transport is kept (the relay allocation lives 10 minutes; the lease longer).
pub const WARM_LIFETIME: Duration = Duration::from_secs(120);
impl Warm {
    /// True while the thread behind it still waits for a session.
    pub fn usable(&self) -> bool {
        !self.release.load(Ordering::Acquire) && self.started.elapsed() < WARM_LIFETIME - Duration::from_secs(5)
    }
    pub fn age(&self) -> Duration {
        self.started.elapsed()
    }
    fn adopt(mut self, config: Config) -> Result<(), Box<Config>> {
        if !self.usable() {
            return Err(Box::new(config));
        }
        match self.adopt.take() {
            Some(sender) => sender.send(config).map_err(|error| Box::new(error.0)),
            None => Err(Box::new(config)),
        }
    }
}
impl Drop for Warm {
    fn drop(&mut self) {
        if self.adopt.is_some() {
            self.release.store(true, Ordering::Release);
        }
    }
}

/// The transport as a session needs it: relay allocation (unless only direct paths are used),
/// control channel, offer. Everything that does not depend on the session's own numbers.
async fn prepare_warm(
    servers: Vec<core_protocol::transport::NativeIceServer>,
    relay_only: bool,
) -> Result<WarmParts, String> {
    let mut transport = create_transport(servers, relay_only).await?;
    transport.receive_video().await.map_err(|error| error.to_string())?;
    let channel = transport
        .peer
        .create_data_channel("mixel-control-v1", None)
        .await
        .map_err(|_| "Cannot create the encrypted control channel")?;
    let offer = transport
        .local_sdp(
            transport
                .peer
                .create_offer(None)
                .await
                .map_err(|_| "Cannot create the connection offer")?,
        )
        .await
        .map_err(|error| error.to_string())?;
    crate::qa_timeline::mark(&format!("ready transport made, own candidates: {}", crate::qa_timeline::candidate_summary(&offer.sdp)));
    Ok(WarmParts { transport, channel, offer: offer.sdp })
}

/// Makes a transport ready on its own thread (the lease comes from the broker without a session).
pub fn start_warm(
    state: &RemoteSession,
    cancel: &AtomicBool,
    servers: Vec<core_protocol::transport::NativeIceServer>,
    relay_only: bool,
    lease: signaling_server::turn::IceLease,
) -> Result<Warm, String> {
    let (adopt, adopted) = std::sync::mpsc::channel::<Config>();
    let release = Arc::new(AtomicBool::new(false));
    let thread_release = release.clone();
    let strict = relay_only_transport(relay_only);
    state.spawn_media(cancel, false, Uuid::new_v4(), false, move || {
        let Ok(runtime) = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build() else {
            thread_release.store(true, Ordering::Release);
            if let Ok(config) = adopted.try_recv() {
                own_media(config, None, None);
            }
            return;
        };
        let Ok(parts) = runtime.block_on(async { tokio::time::timeout(Duration::from_secs(20), prepare_warm(servers, strict)).await }).map_err(|_| ()).and_then(|made| made.map_err(|_| ())) else {
            thread_release.store(true, Ordering::Release);
            if let Ok(config) = adopted.try_recv() {
                own_media(config, Some(runtime), None);
            }
            return;
        };
        let started = Instant::now();
        loop {
            match adopted.recv_timeout(Duration::from_millis(100)) {
                Ok(config) => {
                    own_media(config, Some(runtime), Some(parts));
                    return;
                }
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                    if thread_release.load(Ordering::Acquire) || started.elapsed() >= WARM_LIFETIME {
                        break;
                    }
                }
                Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
            }
        }
        thread_release.store(true, Ordering::Release);
        let _ = runtime.block_on(async { tokio::time::timeout(Duration::from_secs(5), parts.transport.close()).await });
    })?;
    Ok(Warm { adopt: Some(adopt), release, started: Instant::now(), lease })
}

/// The media owner of one session: runs the session's transport to its end and reports the end.
fn own_media(mut config: Config, runtime: Option<tokio::runtime::Runtime>, parts: Option<WarmParts>) {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        match runtime {
            Some(runtime) => Ok(runtime),
            None => tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build().map_err(|_| "Cannot start the media runtime".to_owned()),
        }
        .and_then(|runtime| runtime.block_on(run(&mut config, parts)))
    }))
    .unwrap_or_else(|_| Err("The native media owner failed; this connection was stopped".into()));
    if let Err(error) = &result
        && session_platform::login_window_mode()
    {
        eprintln!("Login window media ended: {error}");
    }
    // Lab builds only: why each session ended, with the time (both sides).
    #[cfg(feature = "qa-session")]
    eprintln!(
        "QA session end {} host={} at {:?}: {result:?}",
        config.transport,
        config.host,
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs()
    );
    // A transport that was replaced ends alone; any other end ends the session.
    if !config.link.superseded.load(Ordering::Acquire) && !old_transport_carries_on(&config, result.is_err()) {
        config.session_cancel.store(true, Ordering::Release);
    }
    config.link.released.store(true, Ordering::Release);
    let mut command = Command::Finished {
        session: config.transport,
        result,
    };
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        match config.commands.try_send(command) {
            Ok(()) => break,
            Err(mpsc::error::TrySendError::Closed(_)) => break,
            Err(mpsc::error::TrySendError::Full(value)) => command = value,
        }
        if Instant::now() >= deadline {
            config.account_cancel.store(true, Ordering::Release);
            break;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
}
/// The native transport: through the relay only (service setting `relay_only`), or direct and relay.
/// The service's configuration says relay only (`relay_only` in the packaged service file):
/// neither side ever publishes its own addresses and every session goes through the relay.
/// The snapshot of 2.0.5-2.0.14 ignored the setting unless MIXEL_RELAY_ONLY=1 was set, which
/// no installation sets: both sides then published their local and public addresses to the
/// other side. There is no switch to turn it off.
async fn create_transport(
    servers: Vec<core_protocol::transport::NativeIceServer>,
    relay_only: bool,
) -> Result<NativeTransport, String> {
    let transport = if relay_only_transport(relay_only) {
        #[cfg(target_os = "windows")]
        if crate::remote_control::lab_flag("MIXEL_TURN_TCP_ONLY") {
            return NativeTransport::relay_stream_only(servers)
                .await
                .map_err(|_| "Cannot create the native WebRTC transport".to_owned());
        }
        NativeTransport::relay_only("0.0.0.0:0".into(), servers).await
    } else {
        NativeTransport::new("0.0.0.0:0".into(), servers).await
    };
    transport.map_err(|_| "Cannot create the native WebRTC transport".to_owned())
}
/// Whether a session's transport may publish only relay candidates (`MIXEL_RELAY_ONLY=1` forces strict relay-only;
/// otherwise direct LAN/STUN UDP candidates are gathered alongside TURN relay candidates for lowest latency).
fn relay_only_transport(configured: bool) -> bool {
    configured && crate::remote_control::lab_flag("MIXEL_RELAY_ONLY")
}

async fn run(config: &mut Config, parts: Option<WarmParts>) -> Result<(), String> {
    if config.stopped() {
        return Ok(());
    }
    let strict_relay = relay_only_transport(config.relay_only);
    let (mut transport, warm) = match parts {
        Some(WarmParts { transport, channel, offer }) => {
            crate::qa_timeline::mark("ready transport taken over");
            (transport, Some((channel, offer)))
        }
        None => {
            let servers = std::mem::take(&mut config.ice_servers);
            let transport = create_transport(servers, config.relay_only).await?;
            crate::qa_timeline::mark(if strict_relay { "transport created (relay only)" } else { "transport created (direct and relay)" });
            (transport, None)
        }
    };
    let result = async {
        let (mut consent, channel, sender, size, limit, mut control, mut early_track, mut early_prep) =
            negotiate(config, &mut transport, warm).await?;
        let files = if let Some(bulk) = control.bulk.take() {
            Some(
                crate::remote_file_transport::FileTransport::start(config, &mut transport, bulk)
                    .await?,
            )
        } else {
            None
        };
        crate::qa_timeline::mark("file channel set up");
        let chat = control
            .chat
            .take()
            .map(|crypto| crate::remote_chat::ChatTransport::start(config, &mut transport, crypto));
        crate::qa_timeline::mark("chat channel set up");
        let result = if config.host && config.mirror {
            drop(early_prep.take());
            mirror_media(config, &mut consent, &channel, sender.ok_or("Host video sender unavailable")?, &mut control, &mut transport).await
        } else if config.host {
            host_media(
                config,
                &mut consent,
                &channel,
                sender.ok_or("Host video sender unavailable")?,
                (size, limit),
                &mut control,
                &mut transport,
                early_prep.take(),
            )
            .await
        } else {
            viewer_media(config, &mut consent, &channel, &mut transport, &mut Ctl::with(&mut control), &mut early_track).await
        };
        if !config.link.superseded.load(Ordering::Acquire) && !old_transport_carries_on(config, result.is_err()) {
            config.session_cancel.store(true, Ordering::Release);
        }
        if let Some(chat) = chat {
            chat.close().await;
        }
        let file_closed = if let Some(files) = files {
            files.close().await
        } else {
            Ok(())
        };
        consent.close();
        let released = control.release();
        let _ = tokio::time::timeout(Duration::from_secs(2), channel.close()).await;
        let result = result.and(file_closed);
        result.and(released)
    }
    .await;
    let closed = tokio::time::timeout(Duration::from_secs(5), transport.close())
        .await
        .map_err(|_| "WebRTC cleanup timed out".to_owned())
        .and_then(|value| value.map_err(|_| "WebRTC cleanup failed".into()));
    result.and(closed)
}
fn source_size(config: &Config) -> Result<(usize, usize), String> {
    source_size_within(config, (1280.0, 720.0))
}
/// The stream size for the shared display, scaled down to fit `limit` (never up).
fn source_size_within(config: &Config, limit: (f64, f64)) -> Result<(usize, usize), String> {
    source_size_of(config, &config.source, limit)
}
/// The stream size for the display `source` (the shared one, or the one the technician switches to).
fn source_size_of(config: &Config, source: &str, limit: (f64, f64)) -> Result<(usize, usize), String> {
    if source == "qa-relay-fixture" && session_platform::relay_fixture_enabled() {
        return Ok((640, 420));
    }
    if !config.confirm {
        return Err("Whole-display sharing has not been confirmed".into());
    }
    #[cfg(target_os = "linux")]
    let (width, height) = if source == crate::wayland_portal::SOURCE {
        config
            .portal
            .as_ref()
            .filter(|grant| grant.live())
            .ok_or("The Wayland share grant has expired")?
            .size()
    } else {
        session_platform::display_size(source)?
    };
    #[cfg(not(target_os = "linux"))]
    let (width, height) = session_platform::display_size(source)?;
    if width == 0 || height == 0 {
        return Err("The display dimensions are invalid".into());
    }
    Ok(stream_size((width, height), limit))
}
/// A display size scaled down (never up) to fit `limit`, even numbers, at least the encoder minimum.
fn stream_size((width, height): (usize, usize), limit: (f64, f64)) -> (usize, usize) {
    let scale = (limit.0 / width as f64).min(limit.1 / height as f64).min(1.0);
    let minimum = if cfg!(target_os = "macos") { 2 } else { 16 };
    (
        ((width as f64 * scale) as usize / 2 * 2).max(minimum),
        ((height as f64 * scale) as usize / 2 * 2).max(minimum),
    )
}
type Negotiated = (
    Session,
    Arc<dyn NativeDataChannel>,
    Option<H264VideoSender>,
    (usize, usize),
    // The largest stream size the technician's app shows (a display switch scales into it).
    (f64, f64),
    ControlPipe,
    // The picture track, when the first picture was taken before the control channel opened.
    Option<RemoteTrack>,
    // Host: the already-opened screen capture and encoder from the early picture, handed over to host_media.
    Option<EarlyPicture>,
);
/// Both sides announce this in the offer/answer when the host may send the first complete picture
/// as soon as the transport is encrypted and the peer's signed identity matches its certificate,
/// without waiting for the control channel to open. Release 2.1.1: on; switched off by `MIXEL_NO_FASTPIC=1`
/// (`MIXEL_NO_EARLYPIC` on the host switches off only the early capture/encode, for measurements).
const FASTPIC_ATTRIBUTE: &str = "a=x-mixel-fastpic:1";
fn fastpic_enabled() -> bool {
    !crate::remote_control::lab_flag("MIXEL_NO_FASTPIC")
}
/// The peer's signed identity against the certificate the encrypted transport actually has. Only
/// a peer that passes this is `authenticated` for the session (pictures are sent to it and shown
/// from it): a different certificate, a wrong signature or an expired consent fail it.
fn verify_peer(
    consent: &mut Session,
    handshake: Option<Handshake>,
    hello: Option<Hello>,
    observed: [u8; 32],
) -> Result<core_protocol::crypto::ControlChannel, String> {
    // Lab only (qa-session builds): the certificate seen on the wire is replaced, to prove live that a peer whose
    // certificate does not match its signed identity gets no picture.
    #[cfg(feature = "qa-session")]
    let observed = if std::env::var_os("MIXEL_QA_WRONG_FINGERPRINT").is_some() { [0xA5; 32] } else { observed };
    let control = handshake
        .ok_or("Local identity proof is unavailable")?
        .finish(&hello.ok_or("Remote identity proof is unavailable")?, observed)
        .map_err(|_| "Remote identity or certificate verification failed")?;
    consent
        .authenticated_transport_ready(&control.verified_session(), Instant::now())
        .map_err(|error| error.to_string())?;
    Ok(control)
}
/// The peer's control channel as it arrives, if it did: it must be the ordered reliable channel.
async fn take_control_channel(
    transport: &mut NativeTransport,
) -> Result<Option<Arc<dyn NativeDataChannel>>, String> {
    let Ok(value) = transport.incoming_channels.try_recv() else {
        return Ok(None);
    };
    let valid = tokio::time::timeout(Duration::from_secs(1), async {
        Ok::<_, core_protocol::transport::TransportError>(
            value.label().await? == "mixel-control-v1"
                && value.ordered().await?
                && value.max_packet_life_time().await?.is_none()
                && value.max_retransmits().await?.is_none()
                && value.protocol().await?.is_empty()
                && !value.negotiated().await?,
        )
    })
    .await
    .map_err(|_| "Control channel configuration inspection timed out")?
    .map_err(|_| "Cannot inspect the control channel configuration")?;
    if !valid {
        let _ = tokio::time::timeout(Duration::from_secs(2), value.close()).await;
        return Err("The peer did not provide the expected ordered reliable control channel".into());
    }
    Ok(Some(value))
}
async fn negotiate(
    config: &mut Config,
    transport: &mut NativeTransport,
    mut warm: Option<(Arc<dyn NativeDataChannel>, String)>,
) -> Result<Negotiated, String> {
    const RECORDING_ATTRIBUTE: &str = "a=x-mixel-recording:1";
    const INPUT_RESULT_ATTRIBUTE: &str = "a=x-mixel-input-result:1";
    // Controller offer: it can switch between host displays. Host answer: how many, and which is shown.
    const DISPLAYS_ATTRIBUTE: &str = "a=x-mixel-displays:1";
    const DISPLAY_COUNT_PREFIX: &str = "a=x-mixel-display-count:";
    // Host answer: the last display in the count is "all monitors as one picture".
    const DISPLAY_ALL_ATTRIBUTE: &str = "a=x-mixel-display-all:1";
    let mut display_all = false;
    // Controller offer: it understands host actions. Host answer: the actions it offers
    // (comma list) and, if set up, the Remote ID of its sign-in screen access.
    const ACTIONS_ATTRIBUTE: &str = "a=x-mixel-actions:1";
    const HOST_ACTIONS_PREFIX: &str = "a=x-mixel-host-actions:";
    const SIGNIN_ID_PREFIX: &str = "a=x-mixel-signin-id:";
    const MACS_PREFIX: &str = "a=x-mixel-macs:";
    // Both directions: optional features this app understands (comma list).
    const FEATURES_PREFIX: &str = "a=x-mixel-features:";
    let own_features = crate::remote_control::own_features().join(",");
    let parse_features = |sdp: &str| -> Vec<String> {
        sdp.lines()
            .find_map(|line| line.strip_prefix(FEATURES_PREFIX))
            .map(|list| {
                list.split(',')
                    .filter(|name| {
                        !name.is_empty()
                            && name.len() <= 16
                            && name.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
                    })
                    .take(16)
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default()
    };
    let mut peer_features: Vec<String> = Vec::new();
    let deadline = Instant::now() + Duration::from_secs(30);
    let local = config.identity.public_key()?;
    let role = if config.host {
        Role::Host
    } else {
        Role::Controller
    };
    let context = Context {
        session_id: config.nonce,
        controller: if config.host {
            config.remote_key
        } else {
            local
        },
        host: if config.host {
            local
        } else {
            config.remote_key
        },
    };
    let mut consent = Session::for_peer(
        Instant::now(),
        context.clone(),
        role,
        config.host && config.control,
    );
    consent
        .decide(true, Instant::now())
        .map_err(|error| error.to_string())?;
    let size = if config.host {
        source_size(config)?
    } else {
        (0, 0)
    };
    let sender = if config.host {
        Some(
            H264VideoSender::new(transport, size.0, size.1, TARGET_FPS)
                .await
                .map_err(|error| error.to_string())?,
        )
    } else {
        if warm.is_none() {
            transport
                .receive_video()
                .await
                .map_err(|error| error.to_string())?;
        }
        None
    };
    let mut channel = if config.host {
        None
    } else if let Some((ready, _)) = &warm {
        Some(ready.clone())
    } else {
        Some(
            transport
                .peer
                .create_data_channel("mixel-control-v1", None)
                .await
                .map_err(|_| "Cannot create the encrypted control channel")?,
        )
    };
    let mut early = false;
    let mut handshake: Option<Handshake> = None;
    let mut hello: Option<Hello> = None;
    let mut remote_description = false;
    let mut peer_supports_recording = false;
    let mut peer_supports_input_result = false;
    let mut displays: Vec<String> = Vec::new();
    let mut display_offer = (0u16, 0u16);
    let mut actions: Vec<String> = Vec::new();
    let mut signin_id: Option<u32> = None;
    let mut host_macs: Vec<String> = Vec::new();
    if !config.host {
        let offer_sdp = if let Some((_, ready)) = warm.take() {
            crate::qa_timeline::mark("offer ready (made before Connect)");
            ready
        } else {
            let offer = transport
                .local_sdp(
                    transport
                        .peer
                        .create_offer(None)
                        .await
                        .map_err(|_| "Cannot create the connection offer")?,
                )
                .await
                .map_err(|error| error.to_string())?;
            crate::qa_timeline::mark(&format!("offer ready, own candidates: {}", crate::qa_timeline::candidate_summary(&offer.sdp)));
            offer.sdp
        };
        let proof = config.identity.handshake(
            context.clone(),
            role,
            local_fingerprint(&offer_sdp).map_err(|error| error.to_string())?,
        )?;
        config.signal(Signal::Identity(proof.hello())).await?;
        let offer = format!(
            "{}\r\n{RECORDING_ATTRIBUTE}\r\n{INPUT_RESULT_ATTRIBUTE}\r\n{DISPLAYS_ATTRIBUTE}\r\n{ACTIONS_ATTRIBUTE}\r\n{FEATURES_PREFIX}{own_features}\r\n{}",
            offer_sdp.trim_end(),
            if fastpic_enabled() { format!("{FASTPIC_ATTRIBUTE}\r\n") } else { String::new() }
        );
        config.signal(Signal::Offer(offer)).await?;
        handshake = Some(proof);
    }
    loop {
        if config.stopped() {
            return Err("Session stopped during negotiation".into());
        }
        if Instant::now() >= deadline {
            return Err("The remote transport negotiation expired".into());
        }
        while let Ok(candidate) = transport.ice_candidates.try_recv() {
            config
                .signal(Signal::Ice(
                    serde_json::to_string(&candidate).map_err(|_| "Cannot encode ICE candidate")?,
                ))
                .await?;
        }
        if channel.is_none()
            && let Some(value) = take_control_channel(transport).await?
        {
            channel = Some(value);
        }
        if remote_description && hello.is_some() && (channel.is_some() || early) {
            break;
        }
        let signal =
            match tokio::time::timeout(Duration::from_millis(5), config.signals.recv()).await {
                Ok(Some(signal)) => signal,
                Ok(None) => return Err("Peer signaling closed".into()),
                Err(_) => continue,
            };
        match signal {
            Signal::Identity(value) => {
                if hello.is_some() || value.role == role {
                    return Err("Unexpected peer identity message".into());
                }
                hello = Some(value);
            }
            Signal::Offer(sdp) if config.host && !remote_description => {
                peer_supports_recording = sdp.lines().any(|line| line == RECORDING_ATTRIBUTE);
                peer_supports_input_result = sdp.lines().any(|line| line == INPUT_RESULT_ATTRIBUTE);
                peer_features = parse_features(&sdp);
                early = fastpic_enabled()
                    && config.host
                    && !config.mirror
                    && config.previous.is_none()
                    && sdp.lines().any(|line| line == FASTPIC_ATTRIBUTE);
                if config.control && sdp.lines().any(|line| line == ACTIONS_ATTRIBUTE) {
                    actions = tokio::task::spawn_blocking(crate::host_actions::available)
                        .await
                        .unwrap_or_default();
                }
                if config.control && sdp.lines().any(|line| line == DISPLAYS_ATTRIBUTE) {
                    let source = config.source.clone();
                    let all = peer_features.iter().any(|name| name == crate::remote_control::ALL_MONITORS);
                    displays = tokio::task::spawn_blocking(move || {
                        session_platform::switchable_displays(&source, all)
                    })
                    .await
                    .unwrap_or_default();
                    displays.truncate(16);
                    crate::qa_timeline::mark(&format!("displays listed: {}", displays.len()));
                }
                transport
                    .peer
                    .set_remote_description(
                        NativeSessionDescription::offer(sdp.clone()).map_err(|_| "Invalid remote offer")?,
                    )
                    .await
                    .map_err(|_| "Remote offer was rejected")?;
                remote_description = true;
                let answer = transport
                    .local_sdp(
                        transport
                            .peer
                            .create_answer(None)
                            .await
                            .map_err(|_| "Cannot create the connection answer")?,
                    )
                    .await
                    .map_err(|error| error.to_string())?;
                crate::qa_timeline::mark(&format!("answer ready, own candidates: {}; the technician's: {}", crate::qa_timeline::candidate_summary(&answer.sdp), crate::qa_timeline::candidate_summary(&sdp)));
                let proof = config.identity.handshake(
                    context.clone(),
                    role,
                    local_fingerprint(&answer.sdp).map_err(|error| error.to_string())?,
                )?;
                config.signal(Signal::Identity(proof.hello())).await?;
                let current = displays
                    .iter()
                    .position(|display| *display == config.source)
                    .or_else(|| (!displays.is_empty()).then_some(0));
                let answer_sdp = match current {
                    Some(current) if displays.len() > 1 => format!(
                        "{}\r\n{DISPLAY_COUNT_PREFIX}{}:{current}\r\n",
                        answer.sdp.trim_end(),
                        displays.len()
                    ),
                    _ => {
                        displays.clear();
                        answer.sdp
                    }
                };
                let mut answer_sdp = answer_sdp;
                if displays.last().is_some_and(|last| last == session_platform::ALL_DISPLAYS) {
                    answer_sdp = format!("{}\r\n{DISPLAY_ALL_ATTRIBUTE}\r\n", answer_sdp.trim_end());
                }
                if !actions.is_empty() {
                    answer_sdp = format!("{}\r\n{HOST_ACTIONS_PREFIX}{}\r\n", answer_sdp.trim_end(), actions.join(","));
                    let macs = tokio::task::spawn_blocking(crate::host_actions::mac_addresses).await.unwrap_or_default();
                    if !macs.is_empty() {
                        answer_sdp = format!("{}\r\n{MACS_PREFIX}{}\r\n", answer_sdp.trim_end(), macs.join(","));
                    }
                    #[cfg(target_os = "windows")]
                    if let Some(status) = crate::windows_signin_host::public_status()
                        .filter(|_| !crate::windows_signin_host::is_signin_host())
                    {
                        answer_sdp = format!("{}\r\n{SIGNIN_ID_PREFIX}{}\r\n", answer_sdp.trim_end(), status.connection_id);
                    }
                    #[cfg(target_os = "macos")]
                    if let Some(status) = crate::macos_prelogin_install::public_status()
                        .filter(|_| !session_platform::login_window_mode())
                    {
                        answer_sdp = format!("{}\r\n{SIGNIN_ID_PREFIX}{}\r\n", answer_sdp.trim_end(), status.connection_id);
                    }
                    #[cfg(target_os = "linux")]
                    if let Some(status) = crate::linux_signin_host::public_status() {
                        answer_sdp = format!("{}\r\n{SIGNIN_ID_PREFIX}{}\r\n", answer_sdp.trim_end(), status.connection_id);
                    }
                }
                if !peer_features.is_empty() {
                    answer_sdp = format!("{}\r\n{FEATURES_PREFIX}{own_features}\r\n", answer_sdp.trim_end());
                }
                if early {
                    answer_sdp = format!("{}\r\n{FASTPIC_ATTRIBUTE}\r\n", answer_sdp.trim_end());
                }
                config.signal(Signal::Answer(answer_sdp)).await?;
                handshake = Some(proof);
            }
            Signal::Answer(sdp) if !config.host && !remote_description => {
                display_offer = sdp
                    .lines()
                    .find_map(|line| line.strip_prefix(DISPLAY_COUNT_PREFIX))
                    .and_then(|value| value.split_once(':'))
                    .and_then(|(count, current)| Some((count.parse::<u16>().ok()?, current.parse::<u16>().ok()?)))
                    .filter(|(count, current)| (2..=16).contains(count) && current < count)
                    .unwrap_or((0, 0));
                peer_features = parse_features(&sdp);
                early = fastpic_enabled() && config.previous.is_none() && sdp.lines().any(|line| line == FASTPIC_ATTRIBUTE);
                display_all = display_offer.0 >= 3 && sdp.lines().any(|line| line == DISPLAY_ALL_ATTRIBUTE);
                actions = sdp
                    .lines()
                    .find_map(|line| line.strip_prefix(HOST_ACTIONS_PREFIX))
                    .map(crate::host_actions::parse)
                    .unwrap_or_default();
                host_macs = sdp
                    .lines()
                    .find_map(|line| line.strip_prefix(MACS_PREFIX))
                    .map(|list| {
                        list.split(',')
                            .filter(|mac| mac.len() == 12 && mac.bytes().all(|byte| byte.is_ascii_hexdigit()))
                            .take(4)
                            .map(str::to_owned)
                            .collect()
                    })
                    .unwrap_or_default();
                signin_id = sdp
                    .lines()
                    .find_map(|line| line.strip_prefix(SIGNIN_ID_PREFIX))
                    .and_then(|value| value.trim().parse::<u32>().ok())
                    .filter(|id| (100_000_000..=999_999_999).contains(id));
                transport
                    .peer
                    .set_remote_description(
                        NativeSessionDescription::answer(sdp.clone())
                            .map_err(|_| "Invalid remote answer")?,
                    )
                    .await
                    .map_err(|_| "Remote answer was rejected")?;
                crate::qa_timeline::mark(&format!("answer applied, the host's candidates: {}", crate::qa_timeline::candidate_summary(&sdp)));
                remote_description = true;
            }
            Signal::Ice(text) => {
                let candidate: NativeIceCandidate =
                    serde_json::from_str(&text).map_err(|_| "Invalid ICE candidate")?;
                transport
                    .peer
                    .add_ice_candidate(candidate)
                    .await
                    .map_err(|_| "Remote ICE candidate was rejected")?;
            }
            _ => return Err("Unexpected peer negotiation transition".into()),
        }
    }
    // Fast first picture: both sides agreed. The transport is encrypted (DTLS) before the control
    // channel is (SCTP and the channel's opening take three more round trips): the peer is checked
    // against its signed identity at once and the host sends the first complete picture, the
    // technician shows it; the control channel opens meanwhile and takes over as before.
    let mut verified: Option<core_protocol::crypto::ControlChannel> = None;
    let mut early_track: Option<RemoteTrack> = None;
    let mut opened = false;
    // Host: the first picture is captured and encoded while DTLS is still opening (nothing is sent before the peer is verified).
    let early_size = if early && config.host {
        let full_hd = peer_features.iter().any(|name| name == crate::remote_control::FULL_HD);
        Some(if full_hd { source_size_within(config, (1920.0, 1080.0))? } else { size })
    } else {
        None
    };
    // `MIXEL_NO_EARLYPIC` (lab): the capture and encoder start only after DTLS is up, as before 4752b70.
    let mut prepared: Option<EarlyPicture> = match early_size {
        Some(early_size) if std::env::var_os("MIXEL_NO_EARLYPIC").is_none() => Some(EarlyPicture::start(config, early_size)),
        _ => None,
    };
    if early {
        let observed = loop {
            if let Some(prep) = prepared.as_mut() {
                prep.advance();
            }
            if config.stopped() {
                return Err("Session stopped during transport opening".into());
            }
            if Instant::now() >= deadline {
                return Err("Control transport opening timed out".into());
            }
            pump_ice(config, transport).await?;
            if channel.is_none() {
                channel = take_control_channel(transport).await?;
            }
            if transport.connected()
                && let Ok(observed) = transport.observed_fingerprint().await
            {
                break observed;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        };
        verified = Some(verify_peer(&mut consent, handshake.take(), hello.take(), observed)?);
        crate::qa_timeline::mark("peer verified on the encrypted transport (identity and certificate)");
        if config.host {
            let early_sender = sender.as_ref().ok_or("Host video sender unavailable")?;
            let mut prep = match prepared.take() {
                Some(prep) => prep,
                None => EarlyPicture::start(config, early_size.ok_or("Early picture not prepared")?),
            };
            host_early_picture(config, transport, &consent, early_sender, &mut channel, &mut prep, deadline).await?;
            prepared = Some(prep);
            opened = true;
        } else if let Some(shown) = channel.clone() {
            let mut before = Ctl::before_open();
            viewer_media(config, &mut consent, &shown, transport, &mut before, &mut early_track).await?;
            opened = before.opened;
        }
    }
    let channel = channel.ok_or("Control transport is unavailable")?;
    loop {
        if opened {
            break;
        }
        if config.stopped() {
            return Err("Session stopped during transport opening".into());
        }
        if Instant::now() >= deadline {
            return Err("Control transport opening timed out".into());
        }
        pump_ice(config, transport).await?;
        match tokio::time::timeout(Duration::from_millis(5), channel.poll()).await {
            Ok(Some(NativeDataChannelEvent::OnOpen)) => {
                crate::qa_timeline::mark("control channel open (ICE and DTLS done)");
                break;
            }
            Ok(
                None
                | Some(
                    NativeDataChannelEvent::OnMessage(_)
                    | NativeDataChannelEvent::OnClose
                    | NativeDataChannelEvent::OnError,
                ),
            ) => return Err("Control transport failed before authentication".into()),
            _ => {}
        }
    }
    if config.stopped() {
        return Err("Session stopped before authentication".into());
    }
    let control = match verified {
        Some(control) => control,
        None => {
            let observed = transport
                .observed_fingerprint()
                .await
                .map_err(|_| "The actual remote DTLS certificate is unavailable")?;
            verify_peer(&mut consent, handshake.take(), hello.take(), observed)?
        }
    };
    let control = ControlPipe::create(
        config,
        control,
        context.clone(),
        &channel,
        peer_supports_recording,
        peer_supports_input_result,
        crate::remote_control::DisplayOffer {
            sources: displays,
            count: display_offer.0,
            current: display_offer.1,
            actions: actions.clone(),
            features: peer_features.clone(),
        },
    )
    .await?;
    crate::qa_timeline::mark("session authenticated (identities and certificates checked)");
    // Both sides: what the other app supports (colleagues joining, switching sides).
    config.state.set_peer_features(config.generation, config.session, peer_features.clone()).await;
    if !config.host {
        config
            .state
            .set_displays(
                config.generation,
                config.session,
                if control.allowed { display_offer.0 } else { 0 },
                display_offer.1,
                display_all,
            )
            .await;
        config
            .state
            .set_host_actions(
                config.generation,
                config.session,
                if control.allowed { actions } else { Vec::new() },
                signin_id,
                host_macs,
            )
            .await;
    }
    if !config.host && control.allowed {
        let mut granted = Session::for_peer(Instant::now(), context, Role::Controller, true);
        granted
            .decide(true, Instant::now())
            .map_err(|error| error.to_string())?;
        granted
            .authenticated_transport_ready(&control.proof(), Instant::now())
            .map_err(|error| error.to_string())?;
        consent = granted;
    }

    config
        .state
        .update_peer(
            config.generation,
            config.session,
            if control.allowed {
                "Accepted · enrolled identity and DTLS verified · keyboard and mouse permitted"
            } else {
                "Accepted · enrolled peer and actual DTLS certificate verified · view only"
            },
            0,
        )
        .await;
    // Full HD when the technician's app can show it; otherwise the classic 1280x720.
    let full_hd = config.host && peer_features.iter().any(|name| name == crate::remote_control::FULL_HD);
    let limit = if full_hd { (1920.0, 1080.0) } else { (1280.0, 720.0) };
    let size = if full_hd { source_size_within(config, limit)? } else { size };
    Ok((consent, channel, sender, size, limit, control, early_track, prepared))
}
/// Host, fast first picture: the peer is verified on the encrypted transport, the control channel is
/// not open yet. Opens the screen, encodes one complete picture and sends it (once more after
/// 1.5 s if the channel is still not open: a packet lost beyond the stack's repeat requests would
/// otherwise leave the technician without a picture until the channel opens), then waits for the
/// channel to open and hands the running capture and encoder directly over to `host_media`.
/// The host's first key picture, made while the transport is still opening: the screen is captured and encoded as soon as the
/// request is accepted and the peer's features are known, so that the picture is ready the moment DTLS is up and the peer
/// verified (nothing is sent before that). Capture, encoder start and the first encode used to begin only after DTLS, which
/// cost 1.2 to 3.5 s on a loaded host (speed pass, 18 Nov 2026).
struct EarlyPicture {
    capture: Option<session_platform::Capture>,
    encoder: Option<mpsc::Sender<EncodeRequest>>,
    encoding: Option<tokio::sync::oneshot::Receiver<Result<EncodeReply, String>>>,
    picture: Option<Vec<u8>>,
    sequence: u64,
    started: Instant,
    size: (usize, usize),
}

impl EarlyPicture {
    fn start(config: &Config, size: (usize, usize)) -> Self {
        let capture = session_platform::capture(
            &config.source,
            size,
            #[cfg(target_os = "linux")]
            config.portal.clone(),
        )
        .ok();
        crate::qa_timeline::mark("early: screen capture opened");
        let encoder = if capture.is_some() {
            start_encoder(size, encoder_bitrate(size, crate::video_pacing::START_RATE)).ok()
        } else {
            None
        };
        Self { capture, encoder, encoding: None, picture: None, sequence: 0, started: Instant::now(), size }
    }

    /// One non-blocking step towards the encoded picture.
    fn advance(&mut self) {
        let size = self.size;
        let (capture, encoder, encoding, picture, sequence, started) =
            (&mut self.capture, &self.encoder, &mut self.encoding, &mut self.picture, &mut self.sequence, self.started);
        if picture.is_none() {
            if let Some(reply) = encoding.as_mut() {
                match reply.try_recv() {
                    Ok(Ok(EncodeReply { picture: Some(encoded), .. })) => {
                        crate::qa_timeline::mark("early: first picture encoded");
                        *encoding = None;
                        *picture = Some(encoded.annex_b.to_vec());
                    }
                    Ok(_) | Err(tokio::sync::oneshot::error::TryRecvError::Closed) => {
                        // Nothing came out (or the encoder stopped): the normal loop makes the picture.
                        *encoding = None;
                        *capture = None;
                    }
                    Err(tokio::sync::oneshot::error::TryRecvError::Empty) => {}
                }
            } else if let (Some(grab), Some(encoder)) = (capture.as_ref(), encoder.as_ref()) {
                match session_platform::frames(grab).next(*sequence, Duration::from_millis(1)) {
                    Ok((next, frame)) => {
                        *sequence = next;
                        let frame = if frame.width() == size.0 && frame.height() == size.1 {
                            frame
                        } else {
                            match fit_frame(&frame, size) {
                                Ok(fitted) => Arc::new(fitted),
                                Err(_) => {
                                    *capture = None;
                                    return;
                                }
                            }
                        };
                        let (reply, output) = tokio::sync::oneshot::channel();
                        if encoder
                            .try_send(EncodeRequest { frame, timestamp: started.elapsed().as_micros() as u64, keyframe: true, reply })
                            .is_ok()
                        {
                            *encoding = Some(output);
                        } else {
                            *capture = None;
                        }
                    }
                    Err(native_os_capture::stream::CaptureError::Timeout) => {}
                    Err(_) => *capture = None,
                }
            }
        }
    }

    fn stop(&mut self) {
        if let Some(mut capture) = self.capture.take() {
            let _ = capture.stop();
        }
    }
}

impl Drop for EarlyPicture {
    /// The peer failed verification (or the session ended) before the picture was sent: the capture does not stay open.
    fn drop(&mut self) {
        self.stop();
    }
}
async fn host_early_picture(
    config: &mut Config,
    transport: &mut NativeTransport,
    consent: &Session,
    sender: &H264VideoSender,
    channel: &mut Option<Arc<dyn NativeDataChannel>>,
    prep: &mut EarlyPicture,
    deadline: Instant,
) -> Result<(), String> {
    let (mut sends, mut sent_at) = (0u32, Instant::now());
    let result = loop {
        if config.stopped() {
            break Ok(());
        }
        if Instant::now() >= deadline {
            break Err("Control transport opening timed out".to_owned());
        }
        if let Err(error) = pump_ice(config, transport).await {
            break Err(error);
        }
        if channel.is_none() {
            match take_control_channel(transport).await {
                Ok(value) => *channel = value,
                Err(error) => break Err(error),
            }
        }
        if let Some(open) = channel.as_ref() {
            match tokio::time::timeout(Duration::from_millis(2), open.poll()).await {
                Ok(Some(NativeDataChannelEvent::OnOpen)) => {
                    crate::qa_timeline::mark("control channel open (ICE and DTLS done)");
                    break Ok(());
                }
                Ok(None | Some(NativeDataChannelEvent::OnMessage(_) | NativeDataChannelEvent::OnClose | NativeDataChannelEvent::OnError)) => {
                    break Err("Control transport failed before authentication".to_owned());
                }
                _ => {}
            }
        } else {
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
        prep.advance();
        if let Some(annex_b) = &prep.picture {
            let due = match sends {
                0 => true,
                1 => sent_at.elapsed() >= Duration::from_millis(1500),
                _ => false,
            };
            if due {
                let packets = match sender.packets(consent, annex_b, Duration::from_millis(33)).await {
                    Ok(packets) => packets,
                    Err(error) => break Err(error.to_string()),
                };
                if sends == 0 {
                    crate::qa_timeline::mark(&format!("early: first picture sent ({} packets)", packets.len()));
                }
                // Send 8 packets per 2 ms burst so the initial keyframe arrives in ~50 ms while still letting control packets interleave.
                let mut failed = None;
                for (index, packet) in packets.into_iter().enumerate() {
                    if let Err(error) = sender.write(consent, packet).await {
                        failed = Some(error.to_string());
                        break;
                    }
                    if index % 8 == 7 {
                        tokio::time::sleep(Duration::from_millis(2)).await;
                    }
                }
                if let Some(error) = failed {
                    break Err(error);
                }
                sends += 1;
                sent_at = Instant::now();
            }
        }
    };
    if result.is_err() {
        prep.stop();
    }
    result
}
async fn pump_ice(config: &mut Config, transport: &mut NativeTransport) -> Result<(), String> {
    while let Ok(candidate) = transport.ice_candidates.try_recv() {
        config
            .signal(Signal::Ice(
                serde_json::to_string(&candidate).map_err(|_| "Cannot encode ICE candidate")?,
            ))
            .await?;
    }
    while let Ok(signal) = config.signals.try_recv() {
        match signal {
            Signal::Ice(text) => {
                let candidate = serde_json::from_str(&text).map_err(|_| "Invalid ICE candidate")?;
                transport
                    .peer
                    .add_ice_candidate(candidate)
                    .await
                    .map_err(|_| "Remote ICE candidate was rejected")?;
            }
            _ => return Err("Unexpected renegotiation".into()),
        }
    }
    Ok(())
}

/// How long the technician's side waits for a picture packet the host sends again on request
/// before it gives the picture up: a request goes out within 100 ms of the gap, so this covers a
/// round trip of about 300 ms (a long relay path) and one more request.
const REPEAT_WAIT: Duration = Duration::from_millis(450);

/// Packets taken from the picture stream before the control channel is served again.
const PACKETS_PER_TURN: u32 = 512;

/// Whether the turn takes another picture packet: up to [`PACKETS_PER_TURN`] of them, and none
/// after the packet that completed a picture. A picture is put on the screen at once, which takes
/// far longer than taking a packet; the control channel (input and its confirmations, clipboard,
/// chat) gets its turn before the next picture's packets, however many are waiting by then.
fn turn_goes_on(taken_this_turn: u32, picture_complete: bool) -> bool {
    taken_this_turn < PACKETS_PER_TURN && !picture_complete
}

/// How long to wait for the next packet of a turn: the first may take a moment to come, the ones
/// after it are only taken if they are already there (no timer, so no timer tick is lost).
fn packet_wait(taken_this_turn: u32) -> Duration {
    Duration::from_millis(if taken_this_turn <= 1 { 4 } else { 0 })
}

#[cfg(test)]
mod packet_turn_tests {
    use super::*;

    /// Release default 2.1.1: the fast first picture (and its early encode) is on; `MIXEL_NO_FASTPIC=1` turns it off.
    #[test]
    fn the_fast_picture_is_on_by_default() {
        assert!(!crate::remote_control::lab_flag_value(None));
        assert!(crate::remote_control::lab_flag_value(Some("1")));
        if std::env::var_os("MIXEL_NO_FASTPIC").is_none() {
            assert!(fastpic_enabled());
        }
    }

    /// A burst of packets that is already waiting is taken in one turn without any timed wait
    /// (a timed wait costs about 15 ms on Windows): the turn's time does not grow with the burst.
    #[tokio::test]
    async fn waiting_packets_are_taken_in_one_turn_without_timed_waits() {
        let (sender, mut waiting) = tokio::sync::mpsc::channel::<u32>(1024);
        for number in 0..400 {
            sender.send(number).await.unwrap();
        }
        let started = Instant::now();
        let (mut taken, mut got) = (0u32, Vec::new());
        while taken < PACKETS_PER_TURN {
            taken += 1;
            match tokio::time::timeout(packet_wait(taken), waiting.recv()).await {
                Ok(Some(number)) => got.push(number),
                Ok(None) => unreachable!("the sender is alive"),
                Err(_) => break,
            }
        }
        assert_eq!(got, (0..400).collect::<Vec<_>>(), "all of the burst, in order");
        assert!(started.elapsed() < Duration::from_millis(200), "took {:?}", started.elapsed());
        assert_eq!(packet_wait(1), Duration::from_millis(4));
        assert!((2..=PACKETS_PER_TURN).all(|turn| packet_wait(turn).is_zero()));
        // An endless stream cannot keep the control channel waiting: a turn is bounded.
        for number in 0..2000 {
            if sender.try_send(number).is_err() {
                break;
            }
        }
        let mut taken = 0u32;
        while taken < PACKETS_PER_TURN {
            taken += 1;
            if tokio::time::timeout(packet_wait(taken), waiting.recv()).await.is_err() {
                break;
            }
        }
        assert_eq!(taken, PACKETS_PER_TURN);
    }

    /// Pictures that keep coming while each one is put on the screen: every picture's packets are
    /// waiting by the time the one before is shown. The control channel still gets its turn
    /// after each picture, not after all of them.
    #[tokio::test]
    async fn the_control_channel_gets_its_turn_after_every_picture() {
        // Ten pictures of three packets each, all waiting already (`true`: completes a picture).
        let (sender, mut waiting) = tokio::sync::mpsc::channel::<bool>(64);
        for _ in 0..10 {
            for last in [false, false, true] {
                sender.send(last).await.unwrap();
            }
        }
        let (mut turns, mut pictures, mut longest_turn) = (0u32, 0u32, 0u32);
        while pictures < 10 {
            turns += 1;
            let (mut taken, mut picture_complete) = (0u32, false);
            while turn_goes_on(taken, picture_complete) {
                taken += 1;
                match tokio::time::timeout(packet_wait(taken), waiting.recv()).await {
                    Ok(Some(last)) => {
                        if last {
                            pictures += 1;
                            picture_complete = true;
                        }
                    }
                    Ok(None) => unreachable!("the sender is alive"),
                    Err(_) => break,
                }
            }
            longest_turn = longest_turn.max(taken);
        }
        assert_eq!((turns, longest_turn), (10, 3), "one picture a turn");
        // A picture of more packets than a turn takes is spread over turns as before.
        assert!(turn_goes_on(PACKETS_PER_TURN - 1, false) && !turn_goes_on(PACKETS_PER_TURN, false));
        assert!(!turn_goes_on(1, true));
    }
}

struct EncodeRequest {
    frame: Arc<native_os_capture::stream::CapturedFrame>,
    timestamp: u64,
    keyframe: bool,
    reply: tokio::sync::oneshot::Sender<Result<EncodeReply, String>>,
}

/// What the encoder made of one picture.
struct EncodeReply {
    /// The picture, if one came out.
    picture: Option<native_os_capture::h264::EncodedH264>,
    /// A complete picture has to be tried again.
    retry: bool,
    /// The encoder left this picture out of the stream: it is given again although the screen has
    /// not changed (see `ScreenH264Encoder::left_out`).
    left_out: bool,
}

/// The encoder's bit rate: what the connection carries at the moment (the pacer's rate, with room
/// for complete pictures and repeats), at most the full rate for the picture size. On a slow or
/// lossy line the encoder used to aim at 3 to 6 Mbit/s whatever the line carried, so the pace held
/// big pictures back and the technician saw a few a second; smaller pictures come more often.
pub(crate) fn encoder_bitrate(size: (usize, usize), pace_bytes_per_second: f64) -> u32 {
    let ceiling: u32 = if size.0 > 1280 || size.1 > 720 { 10_000_000 } else { 4_500_000 };
    let carried = (pace_bytes_per_second * 8.0 * 0.6).clamp(0.0, f64::from(ceiling)) as u32;
    // Steps of 1.5x instead of every value: the encoder cannot change its bit rate while it runs
    // (a new encoder, a complete picture each time), so small moves in the pace must not restart it.
    let rung = ENCODER_LADDER.iter().copied().filter(|rung| *rung <= carried).max().unwrap_or(ENCODER_MIN_BITRATE);
    if carried >= ceiling { ceiling } else { rung.clamp(ENCODER_MIN_BITRATE.min(ceiling), ceiling) }
}
/// The bit rates the encoder is set to (bit/s), 1.5x apart.
const ENCODER_LADDER: [u32; 8] = [400_000, 600_000, 900_000, 1_350_000, 2_000_000, 3_000_000, 4_500_000, 6_750_000];
/// The encoder's next bit rate: down at once when the pace fell to less than two thirds of it, up
/// by half at most when the pace allows 40 % more (the pace rises fast after a few complete
/// pictures arrived, faster than a slow line can follow), otherwise unchanged.
pub(crate) fn next_bitrate(current: u32, wanted: u32) -> u32 {
    if u64::from(wanted) * 3 < u64::from(current) * 2 {
        wanted
    } else if u64::from(wanted) * 10 >= u64::from(current) * 14 {
        wanted.min(current.saturating_add(current / 2))
    } else {
        current
    }
}
/// The lowest bit rate the encoder is set to.
const ENCODER_MIN_BITRATE: u32 = 400_000;
/// The encoder is set up again for a new bit rate at most this often, and only when the rate it
/// would get differs by more than half.
const BITRATE_SETTLE: Duration = Duration::from_millis(2500);

/// Adapts the stream resolution to the link's measured pace and latency (with hysteresis so it
/// never flaps). On slow or high-RTT intercontinental WAN links (< 2.8–3.6 Mbps), downscaling
/// 1080p to 720p cuts pixels, encode CPU, and wire frame size by ~2.3x, lifting FPS 2–3x.
pub(crate) fn adaptive_size(
    full: (usize, usize),
    current: (usize, usize),
    pace_bytes_per_second: f64,
    base_rtt: Duration,
) -> (usize, usize) {
    let long_wan = base_rtt >= Duration::from_millis(120);
    let down_threshold = if long_wan { 450_000.0 } else { 350_000.0 };
    let up_threshold = if long_wan { 700_000.0 } else { 550_000.0 };
    let (long_side, short_side) = (full.0.max(full.1), full.0.min(full.1));
    if long_side <= 1280 && short_side <= 720 {
        return full;
    }
    if current == full && pace_bytes_per_second < down_threshold {
        let scale = (1280.0 / long_side as f64).min(720.0 / short_side as f64);
        let w = (((full.0 as f64 * scale).round() as usize) & !1).max(16);
        let h = (((full.1 as f64 * scale).round() as usize) & !1).max(16);
        (w, h)
    } else if current != full && pace_bytes_per_second >= up_threshold {
        full
    } else {
        current
    }
}

#[cfg(test)]
mod bitrate_tests {
    use super::*;
    #[test]
    fn adaptive_size_scales_down_on_slow_wan_and_recovers_with_hysteresis() {
        let full = (1920, 1080);
        // Fast LAN keeps 1080p.
        assert_eq!(adaptive_size(full, full, 2_000_000.0, Duration::from_millis(5)), full);
        // Slow WAN drops to 720p.
        let down = adaptive_size(full, full, 250_000.0, Duration::from_millis(220));
        assert_eq!(down, (1280, 720));
        // Hysteresis: stays at 720p in the middle band.
        assert_eq!(adaptive_size(full, down, 500_000.0, Duration::from_millis(220)), (1280, 720));
        // Recovers to 1080p when bandwidth rises above the upper threshold.
        assert_eq!(adaptive_size(full, down, 800_000.0, Duration::from_millis(220)), full);
    }
    #[test]
    fn the_bit_rate_follows_the_pace_within_bounds() {
        let full_hd = (1920, 1080);
        assert_eq!(encoder_bitrate(full_hd, 12_000_000.0), 10_000_000, "a fast line: full rate");
        assert_eq!(encoder_bitrate((1280, 720), 12_000_000.0), 4_500_000);
        assert_eq!(encoder_bitrate(full_hd, 500_000.0), 2_000_000, "the start pace (4 Mbit/s): the rung below 2.4 Mbit/s");
        assert_eq!(encoder_bitrate(full_hd, 400_000.0), 1_350_000, "steps of 1.5x, so a small move in the pace does not restart the encoder");
        assert_eq!(encoder_bitrate(full_hd, 125_000.0), 600_000, "the slowest pace (1 Mbit/s)");
        assert_eq!(encoder_bitrate(full_hd, 10_000.0), ENCODER_MIN_BITRATE);
    }
    #[test]
    fn the_bit_rate_falls_at_once_and_rises_by_half_at_most() {
        assert_eq!(next_bitrate(2_400_000, 600_000), 600_000);
        assert_eq!(next_bitrate(600_000, 6_000_000), 900_000);
        assert_eq!(next_bitrate(900_000, 6_000_000), 1_350_000);
        assert_eq!(next_bitrate(2_400_000, 2_000_000), 2_400_000, "small changes are left alone");
        assert_eq!(next_bitrate(2_400_000, 3_000_000), 2_400_000);
    }
}

/// Capture and encoder target FPS: 60 on macOS (VideoToolbox) and Windows (Media Foundation
/// hardware H.264; falls back internally to 30 FPS when software OpenH264 is used), 30 on Linux.
const TARGET_FPS: u32 = if cfg!(any(target_os = "macos", target_os = "windows")) { 60 } else { 30 };

fn start_encoder(size: (usize, usize), bitrate: u32) -> Result<mpsc::Sender<EncodeRequest>, String> {
    // A rotation may briefly overlap the previous encoder's cleanup. Bound
    // owners even if a VideoToolbox driver call never returns.
    static OWNERS: tokio::sync::Semaphore = tokio::sync::Semaphore::const_new(2);
    let permit = OWNERS
        .try_acquire()
        .map_err(|_| "Screen video encoder is busy")?;
    let (requests, mut worker) = mpsc::channel::<EncodeRequest>(1);
    std::thread::Builder::new()
        .name("mixel-screen-encoder".into())
        .spawn(move || {
            let _permit = permit;
            let mut encoder = ScreenH264Encoder::new(size.0, size.1, TARGET_FPS, bitrate);
            while let Some(request) = worker.blocking_recv() {
                let result = match &mut encoder {
                    Ok(encoder) => encoder
                        .encode(&request.frame, request.timestamp, request.keyframe)
                        .map(|picture| EncodeReply { picture, retry: encoder.needs_retry(), left_out: encoder.left_out() }),
                    Err(error) => {
                        let _ = request.reply.send(Err(error.to_string()));
                        break;
                    }
                };
                let _ = request
                    .reply
                    .send(result.map_err(|error| error.to_string()));
            }
        })
        .map_err(|_| "Cannot start the Screen video encoder")?;
    Ok(requests)
}

/// Scales a captured BGRA frame to the stream size (nearest pixel; used after a display switch).
fn fit_frame(
    frame: &native_os_capture::stream::CapturedFrame,
    (width, height): (usize, usize),
) -> Result<native_os_capture::stream::CapturedFrame, String> {
    let (source_width, source_height, stride) = (frame.width(), frame.height(), frame.stride());
    if width == 0 || height == 0 || source_width == 0 || source_height == 0 {
        return Err("The display image is empty".into());
    }
    let pixels = frame.pixels();
    let columns: Vec<usize> = (0..width)
        .map(|x| (x * source_width / width).min(source_width - 1) * 4)
        .collect();
    let mut out = vec![0u8; width * height * 4];
    for (y, row_out) in out.chunks_exact_mut(width * 4).enumerate() {
        let start = (y * source_height / height).min(source_height - 1) * stride;
        let row = pixels
            .get(start..start + source_width * 4)
            .ok_or("The display image layout is invalid")?;
        for (pixel, &column) in row_out.as_chunks_mut::<4>().0.iter_mut().zip(&columns) {
            pixel.copy_from_slice(&row[column..column + 4]);
        }
    }
    native_os_capture::stream::CapturedFrame::bgra(width, height, width * 4, out)
        .map_err(|_| "Cannot scale the display image".into())
}

#[allow(clippy::too_many_arguments)]
async fn host_media(
    config: &mut Config,
    consent: &mut Session,
    channel: &Arc<dyn NativeDataChannel>,
    sender: H264VideoSender,
    (mut size, limit): ((usize, usize), (f64, f64)),
    control: &mut ControlPipe,
    transport: &mut NativeTransport,
    mut early_prep: Option<EarlyPicture>,
) -> Result<(), String> {
    let _released = Released(config.link.released.clone());
    if config.stopped() {
        return Ok(());
    }
    // The technician may already be typing (permissions went out before the file and chat
    // channels were set up): answer that before the capture starts.
    #[cfg(feature = "qa-session")]
    let setup = Instant::now();
    // Lab builds only: a host that is slow to start (as a cold first session can be), to check
    // that the technician's session survives inputs it answers late.
    #[cfg(feature = "qa-session")]
    if let Some(delay) = std::env::var("MIXEL_QA_SLOW_START_MS").ok().and_then(|value| value.parse::<u64>().ok()) {
        tokio::time::sleep(Duration::from_millis(delay.min(10_000))).await;
    }
    control.poll(config, channel).await?;
    // A renewed transport: colleagues who joined keep their view, and the transport before (still
    // sharing until now) stops and closes its capture, then this one opens it. The technician sees
    // the last picture of the one before until the first one of this one.
    let mut continuing = config.previous.is_some().then(crate::session_mirror::publish_continuing);
    if let Some(previous) = config.previous.take() {
        drop(early_prep.take());
        #[cfg(feature = "qa-session")]
        let waited = Instant::now();
        let stopped = previous.take_over(CAPTURE_HANDOVER).await;
        #[cfg(feature = "qa-session")]
        eprintln!("QA renewal: the transport before stopped sharing after {} ms (in time: {stopped}) at clock {}", waited.elapsed().as_millis(), crate::remote_control::qa_wall_clock());
        #[cfg(not(feature = "qa-session"))]
        let _ = stopped;
    }
    let handed_over = early_prep.take().and_then(|mut prep| {
        if prep.size == size && prep.encoding.is_none()
            && let (Some(capture), Some(encoder)) = (prep.capture.take(), prep.encoder.take())
        {
            Some((capture, encoder, prep.sequence, prep.started))
        } else {
            prep.stop();
            None
        }
    });
    let (mut capture, handed_encoder) = match handed_over {
        Some((capture, encoder, seq, started_at)) => (capture, Some((encoder, seq, started_at))),
        None => (
            session_platform::capture(
                &config.source,
                size,
                #[cfg(target_os = "linux")]
                config.portal.clone(),
            )?,
            None,
        ),
    };
    #[cfg(feature = "qa-session")]
    let capture_ready = setup.elapsed();
    crate::qa_timeline::mark("screen capture opened");
    let mut shown = config.source.clone();
    // The technician's pointer is placed on the shared display.
    crate::session_guard::set_shared_source(&shown);
    let result = async {
        let mut full_size = size;
        let mut bitrate = encoder_bitrate(size, crate::video_pacing::START_RATE);
        let (mut encoder, mut sequence, started) = match handed_encoder {
            Some((encoder, seq, started_at)) => (encoder, seq, started_at),
            None => (start_encoder(size, bitrate)?, 0, Instant::now()),
        };
        let mut bitrate_set = Instant::now();
        crate::qa_timeline::mark("encoder started");
        let mut retry_encode = false;
        // The encoder left the last picture out: it is given again although the screen is unchanged.
        let mut encode_again = false;
        let mut sent = 0u64;
        let mut last = Instant::now();
        let mut recent: Option<Arc<native_os_capture::stream::CapturedFrame>> = None;
        // Remote printing: forward jobs printed on "Mixel Remote Printer" during this session.
        // Whether that printer exists is asked in the background (lpstat or the registry can take
        // a while), so input is answered from the start.
        let mut print_installed = control.forwards_print_jobs().then(|| tokio::task::spawn_blocking(crate::host_print::installed));
        let mut print_watch: Option<crate::host_print::Watcher> = None;
        let mut print_checked = Instant::now();
        #[cfg(feature = "qa-session")]
        let mut qa_video = (0u32, Duration::ZERO, Duration::ZERO, Duration::ZERO, 0usize, Instant::now());
        #[cfg(feature = "qa-session")]
        let mut qa_keys = (0u32, 0usize);
        // Colleagues who join this session see these same pictures.
        let mirror = continuing.take().unwrap_or_else(crate::session_mirror::publish);
        // Remote sound: what this computer plays, while the technician wants to hear it.
        let mut sound: Option<crate::remote_sound::Capture> = None;
        let mut sound_failed = false;
        let mut pacer = crate::video_pacing::Pacer::new(Instant::now());
        let mut rate = crate::video_rate::RateControl::new(Instant::now());
        #[cfg(feature = "qa-session")]
        let mut qa_rate_logged = Instant::now();
        #[cfg(feature = "qa-session")]
        let (mut qa_kf, mut qa_skip) = (0u32, 0u32);
        while !config.stopped() {
            if control.sound_wanted() {
                if sound.is_none() && !sound_failed {
                    sound = tokio::task::spawn_blocking(crate::remote_sound::Capture::start).await.ok().flatten();
                    sound_failed = sound.is_none();
                }
                if let Some(capture) = &sound {
                    for _ in 0..8 {
                        let Some(frame) = capture.next() else { break };
                        control.send_sound(channel, &frame).await?;
                    }
                }
            } else if sound.is_some() {
                sound = None;
            }
            if !consent.can_share_local_screen() {
                return Err("Screen-sharing authorization expired".into());
            }
            if print_installed.as_ref().is_some_and(tokio::task::JoinHandle::is_finished)
                && let Some(check) = print_installed.take()
                && check.await.unwrap_or(false)
            {
                print_watch = Some(crate::host_print::Watcher::start());
            }
            if let Some(watch) = &mut print_watch
                && print_checked.elapsed() >= Duration::from_millis(700)
            {
                print_checked = Instant::now();
                if let Some(pdf) = watch.poll() {
                    control.queue_print(pdf);
                }
            }
            pump_ice(config, transport).await?;
            control.poll(config, channel).await?;
            // macOS: a privacy cover switched on is left out of this capture before it shows.
            session_platform::refresh(&mut capture)?;
            // A picture or files arrive in thousands of small records: take them in
            // batches instead of one per captured frame.
            let mut batch = 0;
            while control.receiving_clipboard() && batch < 256 && !config.stopped() {
                control.poll(config, channel).await?;
                batch += 1;
            }
            if let Some(source) = control.take_display_switch() {
                // The stream takes the size of the display switched to (all screens side by side are
                // wider than one), scaled into what the technician's app shows; a new encoder starts
                // with a complete picture and its parameter sets carry the new size.
                // A display can be captured only once per process: close the current capture first.
                let _ = capture.stop();
                let next_size = source_size_of(config, &source, limit).unwrap_or(size);
                #[cfg(all(feature = "qa-session", target_os = "windows"))]
                crate::windows_signin_host::log(&format!("QA display switch to {source}: stream {}x{} -> {}x{} (limit {:?})", size.0, size.1, next_size.0, next_size.1, limit));
                capture = match session_platform::capture(
                    &source,
                    next_size,
                    #[cfg(target_os = "linux")]
                    config.portal.clone(),
                ) {
                    Ok(next) => {
                        shown.clone_from(&source);
                        crate::session_guard::set_shared_source(&shown);
                        full_size = next_size;
                        if next_size != size {
                            size = next_size;
                            encoder = start_encoder(size, bitrate)?;
                        }
                        next
                    }
                    // Keep the session: go back to the display shown before.
                    Err(_) => session_platform::capture(
                        &shown,
                        size,
                        #[cfg(target_os = "linux")]
                        config.portal.clone(),
                    )?,
                };
                sequence = 0;
                recent = None;
                retry_encode = true;
            }
            // What the line delivers (the technician's receive reports): the pace and the encoder
            // follow it, and while more is on its way than the line holds nothing new is encoded
            // (the newest screen is captured when it goes on: stale pictures are never queued).
            for report in control.take_video_reports() {
                rate.report(Instant::now(), report);
            }
            rate.tick(Instant::now());
            pacer.set_cap(rate.limit());
            pacer.set_line_clean(rate.clocked() && rate.queue_delay() < crate::video_rate::QUEUE_LOW);
            #[cfg(feature = "qa-session")]
            if qa_rate_logged.elapsed() >= Duration::from_secs(5) {
                eprintln!("QA line limit {:.0} kB/s, in flight {} kB, base delay {} ms, queue {} ms, pace {:.0} kB/s | delivered {:.0} kB/s, max queue {} ms, cuts {} (soft steps {}), pacer own {:.0} kB/s ceiling {:.0} kB/s losses {}, keyframe requests {}, pictures {}, skipped loops {}", rate.limit() / 1000.0, rate.in_flight() / 1000, rate.base_delay().as_millis(), rate.queue_delay().as_millis(), pacer.rate() / 1000.0, rate.delivered() / 1000.0, rate.max_queue.as_millis(), rate.cuts, rate.softs, pacer.own_rate().0 / 1000.0, pacer.own_rate().1 / 1000.0, pacer.losses, qa_kf, sent, qa_skip);
                rate.max_queue = Duration::ZERO;
                qa_rate_logged = Instant::now();
            }
            if rate.backlogged(Instant::now()) {
                #[cfg(feature = "qa-session")]
                {
                    qa_skip += 1;
                }
                pump_ice(config, transport).await?;
                control.poll(config, channel).await?;
                tokio::time::sleep(Duration::from_millis(5)).await;
                continue;
            }
            let requested = control.take_keyframe_request();
            #[cfg(feature = "qa-session")]
            if requested {
                qa_kf += 1;
            }
            if requested {
                pacer.complete_picture_requested(Instant::now());
            }
            let force_keyframe = requested || mirror.take_keyframe_request() || retry_encode;
            #[cfg(feature = "qa-session")]
            let lap = Instant::now();
            let frame = match session_platform::frames(&capture).next(
                sequence,
                Duration::from_millis(if control.receiving_clipboard() {
                    1
                } else if control.allowed || encode_again {
                    2
                } else {
                    100
                }),
            ) {
                Ok((next, frame)) => {
                    #[cfg(feature = "qa-session")]
                    if sent == 0 && recent.is_none() {
                        crate::qa_timeline::mark("first screen picture captured");
                    }
                    sequence = next;
                    let frame = if frame.width() == size.0 && frame.height() == size.1 {
                        frame
                    } else {
                        Arc::new(fit_frame(&frame, size)?)
                    };
                    recent = Some(frame.clone());
                    frame
                }
                Err(native_os_capture::stream::CaptureError::Timeout) => {
                    // The picture captured last goes to the encoder again although the screen has not
                    // changed: a complete picture is wanted, or the encoder left that picture out
                    // (its rate control drops pictures after large ones). Without the second case
                    // the last change on a screen that then stays still was never sent: after a
                    // moving picture the technician kept seeing an old one until something else
                    // changed (lab, 14 Nov 2026).
                    if (force_keyframe || encode_again) && let Some(frame) = &recent {
                        frame.clone()
                    } else {
                        if sent > 0 && last.elapsed() >= Duration::from_secs(2) {
                            control.video_idle(channel).await?;
                        }
                        continue;
                    }
                }
                Err(error) => return Err(error.to_string()),
            };
            if config.stopped() {
                break;
            }
            #[cfg(feature = "qa-session")]
            let (capture_time, lap) = (lap.elapsed(), Instant::now());
            let (reply, mut output) = tokio::sync::oneshot::channel();
            encoder
                .try_send(EncodeRequest {
                    frame,
                    timestamp: started.elapsed().as_micros() as u64,
                    // One complete picture to start with and one every ~10 s (600 frames on macOS, 300 on Windows/Linux).
                    // Lost packets trigger an immediate explicit keyframe request via `force_keyframe`,
                    // and pictures left out by the encoder are retried via `encode_again`.
                    keyframe: force_keyframe || sent.is_multiple_of(u64::from(TARGET_FPS) * 10),
                    reply,
                })
                .map_err(|_| "Screen video encoder stopped")?;
            let deadline = Instant::now() + Duration::from_secs(5);
            let EncodeReply { picture: encoded, retry, left_out } = loop {
                match tokio::time::timeout(Duration::from_millis(5), &mut output).await {
                    Ok(result) => break result.map_err(|_| "Screen video encoder stopped")??,
                    Err(_) if config.stopped() => return Ok(()),
                    Err(_) if Instant::now() >= deadline => {
                        return Err("Screen video encoder stalled".into());
                    }
                    Err(_) => {
                        pump_ice(config, transport).await?;
                        control.poll(config, channel).await?;
                    }
                }
            };
            retry_encode = retry;
            encode_again = left_out;
            #[cfg(feature = "qa-session")]
            if sent == 0 {
                crate::qa_timeline::mark(if encoded.is_some() { "first picture encoded" } else { "first picture left out by the encoder" });
            }
            let Some(encoded) = encoded else {
                if sent > 0 && last.elapsed() >= Duration::from_secs(2) {
                    control.video_idle(channel).await?;
                }
                continue;
            };
            if config.stopped() {
                break;
            }
            #[cfg(feature = "qa-session")]
            let (encode_time, lap, bytes) = (lap.elapsed(), Instant::now(), encoded.annex_b.len());
            let duration = last
                .elapsed()
                .clamp(Duration::from_micros(1), Duration::from_secs(2));
            last = Instant::now();
            if !send_picture(&sender, &mut pacer, Some(&mut rate), consent, &encoded.annex_b, duration, config, transport, control, channel).await? {
                break;
            }
            // Lab builds only: a picture whose capture, encoding or sending took long (input waits meanwhile).
            #[cfg(feature = "qa-session")]
            if capture_time + encode_time + lap.elapsed() > Duration::from_millis(400) {
                eprintln!("QA slow picture at {} ms: capture {} ms, encode {} ms, send {} ms, {} bytes, pace {:.0} kB/s", setup.elapsed().as_millis(), capture_time.as_millis(), encode_time.as_millis(), lap.elapsed().as_millis(), bytes, pacer.rate() / 1000.0);
            }
            mirror.send(&encoded.annex_b, duration);
            sent += 1;
            // The encoder follows the pace (see `encoder_bitrate` and `adaptive_size`); a new
            // encoder starts with a complete picture.
            if bitrate_set.elapsed() >= BITRATE_SETTLE {
                let next_size = adaptive_size(full_size, size, pacer.rate(), rate.base_delay());
                let wanted = next_bitrate(bitrate, encoder_bitrate(next_size, pacer.rate()));
                if next_size != size || wanted != bitrate {
                    #[cfg(feature = "qa-session")]
                    eprintln!("QA encoder {}x{} -> {}x{}, bit rate {} -> {} kbit/s (pace {:.0} kB/s, base {} ms)", size.0, size.1, next_size.0, next_size.1, bitrate / 1000, wanted / 1000, pacer.rate() / 1000.0, rate.base_delay().as_millis());
                    encoder = start_encoder(next_size, wanted)?;
                    if next_size != size {
                        size = next_size;
                        recent = None;
                    }
                    bitrate = wanted;
                    retry_encode = true;
                }
                bitrate_set = Instant::now();
            }
            #[cfg(feature = "qa-session")]
            if sent == 1 {
                crate::qa_timeline::mark(&format!("first picture sent ({bytes} bytes, capture ready after {} ms)", capture_ready.as_millis()));
            }
            // Lab builds only: how long the host took to start (capture, first picture).
            #[cfg(feature = "qa-session")]
            if sent == 1
                && let Some(path) = std::env::var_os("MIXEL_QA_SETUP_LOG")
            {
                let _ = std::fs::write(path, format!("capture ready after {} ms, first picture sent after {} ms", capture_ready.as_millis(), setup.elapsed().as_millis()));
            }
            // Lab builds only: where the time of each picture goes (capture, encode, send).
            #[cfg(feature = "qa-session")]
            if let Some(path) = std::env::var_os("MIXEL_QA_VIDEO_LOG") {
                qa_video.0 += 1;
                qa_video.1 += capture_time;
                qa_video.2 += encode_time;
                qa_video.3 += lap.elapsed();
                qa_video.4 += bytes;
                qa_keys.0 += u32::from(crate::session_mirror::is_keyframe(&encoded.annex_b));
                qa_keys.1 = qa_keys.1.max(bytes);
                if qa_video.5.elapsed() >= Duration::from_secs(1) {
                    let n = qa_video.0.max(1) as f64;
                    let line = format!(
                        "sent/s={} capture={:.1}ms encode={:.1}ms send={:.1}ms kB={:.0} size={}x{} pace={:.0}kB/s",
                        qa_video.0, qa_video.1.as_secs_f64() * 1000.0 / n, qa_video.2.as_secs_f64() * 1000.0 / n,
                        qa_video.3.as_secs_f64() * 1000.0 / n, qa_video.4 as f64 / n / 1000.0, size.0, size.1, pacer.rate() / 1000.0);
                    // The same once a second as a history: when, how much, how many complete pictures.
                    let mut history = std::path::PathBuf::from(&path).into_os_string();
                    history.push(".history");
                    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(history) {
                        use std::io::Write;
                        let _ = writeln!(file, "{:>6} ms: {line} total={:.0}kB complete={} largest={:.0}kB", setup.elapsed().as_millis(), qa_video.4 as f64 / 1000.0, qa_keys.0, qa_keys.1 as f64 / 1000.0);
                    }
                    let _ = std::fs::write(path, line);
                    qa_video = (0, Duration::ZERO, Duration::ZERO, Duration::ZERO, 0, Instant::now());
                    qa_keys = (0, 0);
                }
            }
            if sent.is_multiple_of(30) {
                config
                    .state
                    .update_peer(
                        config.generation,
                        config.session,
                        if control.allowed {
                            "Sharing selected source · H264 · encrypted keyboard and mouse enabled"
                        } else {
                            "Sharing selected source · H264 · authenticated DTLS-SRTP · view only"
                        },
                        sent,
                    )
                    .await;
            }
        }
        Ok(())
    }
    .await;
    let released = control.release();
    let stopped = capture.stop().map_err(|error| error.to_string());
    result.and(released).and(stopped)
}
/// One picture to the technician at the pace the connection carries (`video_pacing`). While a
/// large picture is held back the session keeps answering: input, the clipboard, chat and the
/// connection itself get their turn between its packets. `false`: the session stopped meanwhile.
#[allow(clippy::too_many_arguments)]
async fn send_picture(
    sender: &H264VideoSender,
    pacer: &mut crate::video_pacing::Pacer,
    mut rate: Option<&mut crate::video_rate::RateControl>,
    consent: &Session,
    annex_b: &[u8],
    duration: Duration,
    config: &mut Config,
    transport: &mut NativeTransport,
    control: &mut ControlPipe,
    channel: &Arc<dyn NativeDataChannel>,
) -> Result<bool, String> {
    // Lab builds only: the old way (the whole picture in one burst), to measure against.
    #[cfg(feature = "qa-session")]
    if std::env::var_os("MIXEL_QA_UNPACED").is_some() {
        sender.send(consent, annex_b, duration).await.map_err(|error| error.to_string())?;
        return Ok(true);
    }
    let packets = sender.packets(consent, annex_b, duration).await.map_err(|error| error.to_string())?;
    let complete = crate::session_mirror::is_keyframe(annex_b);
    let sent = crate::video_pacing::spread(
        pacer,
        packets,
        complete,
        core_protocol::media::PicturePacket::size,
        async |packet| {
            let (seq, size) = (packet.sequence(), packet.size());
            sender.write(consent, packet).await.map_err(|error| error.to_string())?;
            if let Some(rate) = rate.as_deref_mut() {
                rate.sent(Instant::now(), seq, size);
            }
            Ok(())
        },
        async || {
            if config.stopped() {
                return Ok(false);
            }
            pump_ice(config, transport).await?;
            control.poll(config, channel).await?;
            Ok(!config.stopped())
        },
    )
    .await?;
    if sent && complete {
        // Requests for a complete picture that came in while this one was leaving were sent
        // before it could arrive: it answers them. Sending another would double the load on
        // exactly the connections that are slow. (A technician who still needs one asks again
        // within a second.) The snapshot of 2.0.5-2.0.14 dropped this: on a slow link every
        // request then started another complete picture behind the one still on its way.
        control.complete_picture_sent();
    }
    Ok(sent)
}
/// Host side of a colleague who joined: the first technician's pictures, sent on this connection.
async fn mirror_media(
    config: &mut Config,
    consent: &mut Session,
    channel: &Arc<dyn NativeDataChannel>,
    sender: H264VideoSender,
    control: &mut ControlPipe,
    transport: &mut NativeTransport,
) -> Result<(), String> {
    let mut pictures = crate::session_mirror::watch().ok_or("The first technician's session has ended")?;
    let mut sent = 0u64;
    let mut last = Instant::now();
    let mut pacer = crate::video_pacing::Pacer::new(Instant::now());
    let result = async {
        while !config.stopped() {
            if !consent.can_share_local_screen() {
                return Err("Screen-sharing authorization expired".into());
            }
            pump_ice(config, transport).await?;
            control.poll(config, channel).await?;
            if control.take_keyframe_request() {
                pacer.complete_picture_requested(Instant::now());
                pictures.request_keyframe();
            }
            match pictures.next(Duration::from_millis(25)).await {
                crate::session_mirror::Next::Picture(picture) => {
                    if !send_picture(&sender, &mut pacer, None, consent, &picture.annex_b, picture.duration, config, transport, control, channel).await? {
                        break;
                    }
                    sent += 1;
                    last = Instant::now();
                }
                crate::session_mirror::Next::Idle => {
                    if sent > 0 && last.elapsed() >= Duration::from_secs(2) {
                        control.video_idle(channel).await?;
                        last = Instant::now();
                    }
                }
                crate::session_mirror::Next::Ended => return Err("The first technician's session has ended".into()),
            }
        }
        Ok(())
    }
    .await;
    let released = control.release();
    result.and(released)
}
/// Technician: a renewed transport put its first picture on the screen (or waited long enough):
/// the transport before stops now, and the recording, if allowed, goes on from this one.
/// What the technician's picture loop uses of the control channel. Normally the real one; before
/// the channel is open (fast first picture) there is none: the loop only waits for the opening.
struct Ctl<'a> {
    pipe: Option<&'a mut ControlPipe>,
    /// The channel's opening was seen (only without a pipe).
    opened: bool,
}
impl<'a> Ctl<'a> {
    fn with(pipe: &'a mut ControlPipe) -> Self {
        Self { pipe: Some(pipe), opened: false }
    }
    fn before_open() -> Self {
        Self { pipe: None, opened: false }
    }
    fn early(&self) -> bool {
        self.pipe.is_none()
    }
    fn recording_allowed(&self) -> bool {
        self.pipe.as_ref().is_some_and(|pipe| pipe.recording_allowed)
    }
    fn report_clock(&self) -> bool {
        self.pipe.as_ref().is_some_and(|pipe| pipe.report_clock())
    }
    fn video_idle_for(&self) -> Duration {
        self.pipe.as_ref().map_or(Duration::ZERO, |pipe| pipe.last_video_idle.elapsed())
    }
    async fn poll(&mut self, config: &Config, channel: &Arc<dyn NativeDataChannel>) -> Result<(), String> {
        if let Some(pipe) = &mut self.pipe {
            return pipe.poll(config, channel).await;
        }
        match tokio::time::timeout(Duration::from_millis(5), channel.poll()).await {
            Ok(Some(NativeDataChannelEvent::OnOpen)) => {
                crate::qa_timeline::mark("control channel open (ICE and DTLS done)");
                self.opened = true;
            }
            Ok(None | Some(NativeDataChannelEvent::OnMessage(_) | NativeDataChannelEvent::OnClose | NativeDataChannelEvent::OnError)) => {
                return Err("Control transport failed before authentication".into());
            }
            _ => {}
        }
        Ok(())
    }
    async fn send_video_report(&mut self, channel: &Arc<dyn NativeDataChannel>, report: &crate::video_rate::Report) -> Result<(), String> {
        match &mut self.pipe {
            Some(pipe) => pipe.send_video_report(channel, report).await,
            None => Ok(()),
        }
    }
    async fn request_keyframe(&mut self, channel: &Arc<dyn NativeDataChannel>) -> Result<(), String> {
        match &mut self.pipe {
            Some(pipe) => pipe.request_keyframe(channel).await,
            None => Ok(()),
        }
    }
    fn take_sound(&mut self) -> Option<Vec<u8>> {
        self.pipe.as_mut().and_then(|pipe| pipe.take_sound())
    }
    fn take_print_job(&mut self) -> Option<Vec<u8>> {
        self.pipe.as_mut().and_then(|pipe| pipe.take_print_job())
    }
}
fn hand_over(
    config: &mut Config,
    recording_allowed: bool,
    recording: &mut Option<crate::remote_recording::RecordingScope>,
) -> Result<(), String> {
    let Some(previous) = config.previous.take() else {
        return Ok(());
    };
    #[cfg(feature = "qa-session")]
    eprintln!("QA renewal: the renewed transport took over the picture at clock {}", crate::remote_control::qa_wall_clock());
    // The recording goes on from this transport: attach BEFORE the one before is told to stop, so its
    // end cannot finish the running recording first.
    let attached = if recording_allowed && recording.is_none() {
        config
            .state
            .recording_hub()
            .attach_renewal(config.session, config.input_authorization())
            .map(|scope| *recording = Some(scope))
    } else {
        Ok(())
    };
    previous.retire();
    attached
}
async fn viewer_media(
    config: &mut Config,
    consent: &mut Session,
    channel: &Arc<dyn NativeDataChannel>,
    transport: &mut NativeTransport,
    control: &mut Ctl<'_>,
    track_slot: &mut Option<RemoteTrack>,
) -> Result<(), String> {
    // A renewed transport records from the moment it takes over (the one before records until then). The host can
    // close the transport before as soon as its side has switched: that end must not finish a running recording.
    if control.recording_allowed() && config.previous.is_some() {
        config.state.recording_hub().renewal_expected(config.session, PICTURE_HANDOVER + Duration::from_secs(20));
    }
    let mut recording = (control.recording_allowed() && config.previous.is_none())
        .then(|| {
            config
                .state
                .recording_hub()
                .attach(config.session, config.input_authorization())
        })
        .transpose()?;
    // Lab builds only: when the first picture is there, counted from the start of the picture stream.
    #[cfg(feature = "qa-session")]
    let qa_started = Instant::now();
    let track_deadline = Instant::now() + Duration::from_secs(8);
    let track = match track_slot.take() {
        Some(track) => track,
        None => loop {
            if config.stopped() {
                return Ok(());
            }
            // Before the control channel is open (first picture ahead of it) the picture may be
            // late and the channel's opening ends this loop; otherwise the track must come.
            if !control.early() && Instant::now() >= track_deadline {
                return Err("No remote video track arrived".into());
            }
            pump_ice(config, transport).await?;
            control.poll(config, channel).await?;
            if control.opened {
                return Ok(());
            }
            match tokio::time::timeout(Duration::from_millis(10), transport.incoming_tracks.recv())
                .await
            {
                Ok(Some(track)) => {
                    crate::qa_timeline::mark("video track open");
                    break track;
                }
                Ok(None) => return Err("Remote video transport closed".into()),
                Err(_) => {}
            }
        },
    };
    #[cfg(feature = "qa-session")]
    let mut qa_shown = (0u32, Instant::now());
    let mut assembler = H264Assembler::default();
    // A packet lost on the way is asked for again (NACK) and waited for this long before the
    // picture is given up and a complete one is requested.
    let mut reorder = core_protocol::media::PacketReorder::new(REPEAT_WAIT);
    let mut in_order = std::collections::VecDeque::new();
    // What arrives is reported to the host five times a second (`video_rate`).
    let mut report_rx = crate::video_rate::Receiver::new(Instant::now());
    let mut decoder =
        session_platform::H264Decoder::new(1920, 1920).map_err(|error| error.to_string())?;
    let mut previous_rtp: Option<u32> = None;
    let mut timestamp_ticks = 0u64;
    let mut received = 0u64;
    let mut last = Instant::now();
    #[cfg(feature = "qa-session")]
    let mut qa_video = (0u32, Duration::ZERO, Duration::ZERO, Instant::now());
    // Lab builds only: what arrived, for a session that ends without pictures.
    #[cfg(feature = "qa-session")]
    let mut qa_rtp = (0u64, 0u64, 0u64, 0u64, 0u64);
    #[cfg(feature = "qa-session")]
    let (mut qa_expected, mut qa_gaps, mut qa_late, mut qa_keys, mut qa_lost): (Option<u16>, u64, u64, u64, u64) = (None, 0, 0, 0, 0);
    #[cfg(feature = "qa-session")]
    let mut qa_jumps: Vec<String> = Vec::new();
    let (mut window_decodes, mut waiting_for_key) = (
        config.video.wants_compressed() && !recording.as_ref().is_some_and(|scope| scope.is_recording()),
        false,
    );
    let mut seed_keyframe: Option<(u64, Vec<u8>)> = None;
    let mut player: Option<crate::remote_sound::Player> = None;
    let mut player_failed = false;
    let stream_opened = Instant::now();
    while !config.stopped() {
        if !consent.can_view() {
            return Err("Remote-viewing authorization expired".into());
        }
        if config.previous.is_some() && stream_opened.elapsed() >= PICTURE_HANDOVER {
            hand_over(config, control.recording_allowed(), &mut recording)?;
        }
        // Lab builds only: a part of this loop that took long (input and its confirmations wait meanwhile).
        #[cfg(feature = "qa-session")]
        let qa_turn = Instant::now();
        pump_ice(config, transport).await?;
        #[cfg(feature = "qa-session")]
        let qa_ice = qa_turn.elapsed();
        control.poll(config, channel).await?;
        if control.opened {
            *track_slot = Some(track);
            return Ok(());
        }
        config.link.lost.store(
            !transport.connected(),
            Ordering::Release,
        );
        report_rx.clocked(control.report_clock());
        if let Some(report) = report_rx.due(Instant::now()) {
            control.send_video_report(channel, &report).await?;
        }
        #[cfg(feature = "qa-session")]
        if qa_turn.elapsed() > Duration::from_millis(300) {
            eprintln!("QA technician loop at clock {}: signalling {} ms, control channel {} ms", crate::remote_control::qa_wall_clock(), qa_ice.as_millis(), (qa_turn.elapsed() - qa_ice).as_millis());
        }
        #[cfg(feature = "qa-session")]
        let qa_turn = Instant::now();
        while let Some(frame) = control.take_sound() {
            if player.is_none() && !player_failed {
                player = tokio::task::spawn_blocking(crate::remote_sound::Player::start).await.ok().flatten();
                player_failed = player.is_none();
            }
            if let Some(player) = &player {
                player.push(&frame);
                #[cfg(feature = "qa-session")]
                if let Some(path) = std::env::var_os("MIXEL_QA_SOUND_LOG") {
                    let _ = std::fs::write(path, format!("frames={}", player.frames.load(std::sync::atomic::Ordering::Relaxed)));
                }
            }
        }
        if let Some(pdf) = control.take_print_job() {
            match tokio::task::spawn_blocking(move || crate::host_print::store(&pdf)).await {
                Ok(Ok(path)) => config.state.add_print_job(config.generation, config.session, path).await,
                _ => eprintln!("print job could not be stored"),
            }
        }
        if assembler.needs_keyframe()
            || waiting_for_key
            || (received == 0 && last.elapsed() >= Duration::from_secs(1))
            || recording
                .as_ref()
                .is_some_and(|scope| scope.needs_keyframe())
        {
            #[cfg(feature = "qa-session")]
            {
                qa_rtp.3 += 1;
            }
            control.request_keyframe(channel).await?;
        }
        #[cfg(feature = "qa-session")]
        if qa_turn.elapsed() > Duration::from_millis(300) {
            eprintln!("QA technician loop at clock {}: sound, print and the request for a complete picture took {} ms", crate::remote_control::qa_wall_clock(), qa_turn.elapsed().as_millis());
        }
        #[cfg(feature = "qa-session")]
        let qa_turn = Instant::now();
        // Every packet that is already waiting is taken before the control channel gets its turn
        // again. One packet a turn was too little: a turn costs the control channel's short wait,
        // which is a whole timer tick on Windows (about 15 ms), so a Windows technician took about
        // 64 packets a second. A computer that sends more (a Mac being helped, a moving full-HD
        // picture) ran ahead, its keyframes came too late to be put together, and the session
        // ended after 12 s ("Remote video delivery timed out").
        //
        // The turn ends with the picture, though. Putting a picture on the screen (decoding it,
        // handing it over) takes its time, and the packets of the next picture arrive meanwhile:
        // "every packet that is waiting" then never ran out while pictures kept coming, and the
        // control channel waited for seconds (measured in the lab, 14 Nov 2026: turns of 1.4 to
        // 2.4 s over twenty packets; the confirmation of every second input came too late).
        let (mut taken, mut picture_complete) = (0u32, false);
        // Packets held behind a gap the host did not fill in time go on to the assembler now.
        in_order.extend(reorder.expire(Instant::now()));
        while turn_goes_on(taken, picture_complete) {
            taken += 1;
            let event = match in_order.pop_front() {
                Some(packet) => Ok(Some(TrackRemoteEvent::OnRtpPacket(packet))),
                None => match tokio::time::timeout(packet_wait(taken), track.poll()).await {
                    Ok(Some(TrackRemoteEvent::OnRtpPacket(packet))) => {
                        let sequence = packet.header.sequence_number;
                        in_order.extend(reorder.push(sequence, packet, Instant::now()));
                        match in_order.pop_front() {
                            Some(packet) => Ok(Some(TrackRemoteEvent::OnRtpPacket(packet))),
                            // Held behind a gap: the host may still send the missing packet.
                            None => continue,
                        }
                    }
                    other => other,
                },
            };
            match event {
                Ok(Some(TrackRemoteEvent::OnRtpPacket(packet))) => {
                    report_rx.packet(Instant::now(), packet.header.sequence_number, 12 + packet.payload.len());
                    #[cfg(feature = "qa-session")]
                    {
                        qa_rtp.0 += 1;
                        qa_rtp.1 += packet.payload.len() as u64;
                        qa_rtp.4 += u64::from(packet.header.marker);
                        if let Some(expected) = qa_expected {
                            let delta = packet.header.sequence_number.wrapping_sub(expected);
                            if delta >= 0x8000 {
                                qa_late += 1;
                            } else if delta != 0 {
                                qa_gaps += 1;
                                qa_lost += u64::from(delta);
                            }
                            if delta != 0 && qa_jumps.len() < 60 {
                                qa_jumps.push(format!("{}ms #{} expected {expected} got {} ts {} {}b{}", last.elapsed().as_millis(), qa_rtp.0, packet.header.sequence_number, packet.header.timestamp, packet.payload.len(), if packet.header.marker { " end" } else { "" }));
                            }
                        }
                        qa_expected = Some(packet.header.sequence_number.wrapping_add(1));
                        let kind = packet.payload.first().map_or(0, |byte| byte & 0x1f);
                        if kind == 5 || (kind == 28 && packet.payload.get(1).is_some_and(|byte| byte & 0x80 != 0 && byte & 0x1f == 5)) {
                            qa_keys += 1;
                        }
                    }
                    let frame = match assembler.push(
                        packet.header.sequence_number,
                        packet.header.timestamp,
                        packet.header.marker,
                        &packet.payload,
                    ) {
                        Ok(Some(frame)) => frame,
                        Ok(None) => continue,
                        Err(_) => {
                            waiting_for_key = true;
                            control.request_keyframe(channel).await?;
                            continue;
                        }
                    };
                    {
                        picture_complete = true;
                        #[cfg(feature = "qa-session")]
                        {
                            qa_rtp.2 += 1;
                        }
                        let rtp = packet.header.timestamp;
                        if let Some(previous) = previous_rtp {
                            timestamp_ticks = timestamp_ticks
                                .checked_add(u64::from(rtp.wrapping_sub(previous)))
                                .ok_or("Video timestamp exhausted")?;
                        }
                        previous_rtp = Some(rtp);
                        let timestamp = timestamp_ticks
                            .checked_mul(1_000_000)
                            .ok_or("Video timestamp exhausted")?
                            / 90_000;
                        // The window decodes itself when it can (much faster for large pictures);
                        // an active recording needs the decoded picture size, so it temporarily
                        // switches to the native decoder only while recording is actually running.
                        let compressed = config.video.wants_compressed()
                            && !recording.as_ref().is_some_and(|scope| scope.is_recording());
                        if compressed != window_decodes {
                            window_decodes = compressed;
                            waiting_for_key = true;
                            if !compressed {
                                decoder = session_platform::H264Decoder::new(1920, 1920)
                                    .map_err(|error| error.to_string())?;
                            }
                        }
                        if waiting_for_key || compressed {
                            let Ok(unit) = native_os_capture::h264::annex_b_to_avcc(&frame) else {
                                waiting_for_key = true;
                                seed_keyframe = None;
                                control.request_keyframe(channel).await?;
                                continue;
                            };
                            let key = unit.keyframe && unit.sps.is_some() && unit.pps.is_some();
                            if waiting_for_key && !key {
                                if compressed
                                    && let Some((seed_ts, seed_bytes)) = seed_keyframe.take()
                                {
                                    received += 1;
                                    let seed_pkt = crate::remote_video::compressed_packet(
                                        config.session,
                                        received,
                                        seed_ts,
                                        &seed_bytes,
                                        true,
                                    )?
                                    .from_transport(config.transport);
                                    if !config.video.publish_compressed(seed_pkt)? {
                                        control.request_keyframe(channel).await?;
                                        continue;
                                    }
                                } else {
                                    control.request_keyframe(channel).await?;
                                    continue;
                                }
                            }
                            if key {
                                seed_keyframe = None;
                            }
                            waiting_for_key = false;
                            if compressed {
                                received += 1;
                        #[cfg(feature = "qa-session")]
                        {
                            qa_shown.0 += 1;
                            if qa_shown.1.elapsed() >= Duration::from_secs(1) {
                                if let Some(path) = std::env::var_os("MIXEL_QA_VIDEO_LOG") {
                                    let mut history = std::path::PathBuf::from(path).into_os_string();
                                    history.push(".shown");
                                    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(history) {
                                        use std::io::Write;
                                        let _ = writeln!(file, "{:>6} ms: shown/s={}", qa_started.elapsed().as_millis(), qa_shown.0);
                                    }
                                }
                                qa_shown = (0, Instant::now());
                            }
                        }
                                last = Instant::now();
                                #[cfg(feature = "qa-session")]
                                if received == 1 {
                                    eprintln!("QA first picture after {} ms: {} packets, {qa_gaps} gaps ({qa_lost} numbers missing), {} requests for a complete picture", qa_started.elapsed().as_millis(), qa_rtp.0, qa_rtp.3);
                                }
                                let packet = crate::remote_video::compressed_packet(config.session, received, timestamp, &frame, key)?.from_transport(config.transport);
                                if !config.video.publish_compressed(packet)? {
                                    waiting_for_key = true;
                                    control.request_keyframe(channel).await?;
                                }
                                hand_over(config, control.recording_allowed(), &mut recording)?;
                                #[cfg(feature = "qa-session")]
                                if let Some(path) = std::env::var_os("MIXEL_QA_VIDEO_LOG") {
                                    qa_video.0 += 1;
                                    if qa_video.3.elapsed() >= Duration::from_secs(1) {
                                        let fetched = crate::remote_video::QA_FETCHED.swap(0, std::sync::atomic::Ordering::Relaxed);
                                        let _ = std::fs::write(path, format!("window decodes: received/s={} fetched/s={fetched}", qa_video.0));
                                        qa_video = (0, Duration::ZERO, Duration::ZERO, Instant::now());
                                    }
                                }
                                if received.is_multiple_of(30) {
                                    config
                                        .state
                                        .update_peer(config.generation, config.session, "Remote video decoded with hardware acceleration", received)
                                        .await;
                                }
                                continue;
                            }
                        }
                        #[cfg(feature = "qa-session")]
                        let lap = Instant::now();
                        let decoded = match decoder.decode(&frame, timestamp) {
                            Ok(Some(decoded)) => decoded,
                            Ok(None) => {
                                control.request_keyframe(channel).await?;
                                continue;
                            }
                            #[cfg(any(target_os = "macos", target_os = "ios"))]
                            Err(native_os_capture::macos_decoder::DecodeError::Dimensions) => {
                                return Err("decoded dimensions exceed the negotiated limit".into());
                            }
                            #[cfg(not(any(target_os = "macos", target_os = "ios")))]
                            Err(native_os_capture::desktop_h264::DecodeError::Dimensions) => {
                                return Err("decoded dimensions exceed the negotiated limit".into());
                            }
                            Err(_) => {
                                decoder = session_platform::H264Decoder::new(1920, 1920)
                                    .map_err(|error| error.to_string())?;
                                waiting_for_key = true;
                                control.request_keyframe(channel).await?;
                                continue;
                            }
                        };
                        if config.stopped() {
                            return Ok(());
                        }
                        if native_os_capture::h264::annex_b_to_avcc(&frame)
                            .is_ok_and(|u| u.keyframe && u.sps.is_some() && u.pps.is_some())
                        {
                            seed_keyframe = Some((timestamp, frame.to_vec()));
                        } else {
                            seed_keyframe = None;
                        }
                        if let Some(recording) = &recording {
                            recording.submit(
                                &frame,
                                timestamp_ticks,
                                (decoded.width() as u16, decoded.height() as u16),
                            );
                        }
                        received += 1;
                        #[cfg(feature = "qa-session")]
                        {
                            qa_shown.0 += 1;
                            if qa_shown.1.elapsed() >= Duration::from_secs(1) {
                                if let Some(path) = std::env::var_os("MIXEL_QA_VIDEO_LOG") {
                                    let mut history = std::path::PathBuf::from(path).into_os_string();
                                    history.push(".shown");
                                    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(history) {
                                        use std::io::Write;
                                        let _ = writeln!(file, "{:>6} ms: shown/s={}", qa_started.elapsed().as_millis(), qa_shown.0);
                                    }
                                }
                                qa_shown = (0, Instant::now());
                            }
                        }
                        last = Instant::now();
                        #[cfg(feature = "qa-session")]
                        if received == 1 {
                            crate::qa_timeline::mark("first picture decoded");
                            eprintln!("QA first picture after {} ms: {} packets, {qa_gaps} gaps ({qa_lost} numbers missing), {} requests for a complete picture", qa_started.elapsed().as_millis(), qa_rtp.0, qa_rtp.3);
                        }
                        #[cfg(feature = "qa-session")]
                        let (decode_time, lap) = (lap.elapsed(), Instant::now());
                        config.video.publish(decoded_packet(
                            config.session,
                            received,
                            timestamp,
                            &decoded,
                        )?.from_transport(config.transport))?;
                        hand_over(config, control.recording_allowed(), &mut recording)?;
                        // Lab builds only: decode and hand-over time, and how many pictures the window fetched.
                        #[cfg(feature = "qa-session")]
                        if let Some(path) = std::env::var_os("MIXEL_QA_VIDEO_LOG") {
                            qa_video.0 += 1;
                            qa_video.1 += decode_time;
                            qa_video.2 += lap.elapsed();
                            if qa_video.3.elapsed() >= Duration::from_secs(1) {
                                let n = f64::from(qa_video.0.max(1));
                                let fetched = crate::remote_video::QA_FETCHED.swap(0, std::sync::atomic::Ordering::Relaxed);
                                let _ = std::fs::write(path, format!(
                                    "decoded/s={} decode={:.1}ms pack={:.1}ms fetched/s={} size={}x{}",
                                    qa_video.0, qa_video.1.as_secs_f64() * 1000.0 / n, qa_video.2.as_secs_f64() * 1000.0 / n,
                                    fetched, decoded.width(), decoded.height()));
                                qa_video = (0, Duration::ZERO, Duration::ZERO, Instant::now());
                            }
                        }
                        config
                            .state
                            .update_peer(
                                config.generation,
                                config.session,
                                if decoder.hardware_active() {
                                    "Remote video decoded with hardware acceleration"
                                } else {
                                    "Remote video decoded"
                                },
                                received,
                            )
                            .await;
                    }
                }
                Ok(None) => return Err("Remote video transport closed".into()),
                Ok(Some(_)) => {}
                // Nothing more is waiting.
                Err(_) => break,
            }
        }
        #[cfg(feature = "qa-session")]
        if qa_turn.elapsed() > Duration::from_millis(300) {
            eprintln!("QA technician loop at clock {}: taking {taken} picture packets (decoding, handing over) took {} ms", crate::remote_control::qa_wall_clock(), qa_turn.elapsed().as_millis());
        }
        if last.elapsed() > Duration::from_secs(30)
            && control.video_idle_for() > Duration::from_secs(30)
        {
            #[cfg(feature = "qa-session")]
            eprintln!("QA video timed out: {} RTP packets ({} bytes, {} with the end mark), {} pictures assembled, {} decoded, {} keyframe requests; {qa_keys} keyframe slices begun, {qa_gaps} gaps in the numbering ({qa_lost} numbers missing), {qa_late} packets late", qa_rtp.0, qa_rtp.1, qa_rtp.4, qa_rtp.2, received, qa_rtp.3);
            #[cfg(feature = "qa-session")]
            eprintln!("QA video jumps: {}", qa_jumps.join(" | "));
            return Err("Remote video delivery timed out".into());
        }
    }
    #[cfg(feature = "qa-session")]
    eprintln!("QA picture packets: {} gaps filled by a late packet, {} given up", reorder.filled, reorder.given_up);
    #[cfg(feature = "qa-session")]
    eprintln!("QA picture stream ended after {} ms: {received} pictures, {} packets, {qa_gaps} gaps ({qa_lost} numbers missing), {} requests for a complete picture", qa_started.elapsed().as_millis(), qa_rtp.0, qa_rtp.3);
    Ok(())
}

#[cfg(test)]
mod display_fit_tests {
    use super::fit_frame;
    use native_os_capture::stream::CapturedFrame;
    #[test]
    fn all_screens_side_by_side_keep_their_proportions_in_the_stream() {
        let full_hd = (1920.0, 1080.0);
        // One display fits as it is, two side by side are scaled down together (not squeezed).
        assert_eq!(super::stream_size((1920, 1080), full_hd), (1920, 1080));
        assert_eq!(super::stream_size((3840, 1080), full_hd), (1920, 540));
        assert_eq!(super::stream_size((2560, 1440), full_hd), (1920, 1080));
        assert_eq!(super::stream_size((3840, 1080), (1280.0, 720.0)), (1280, 360));
        assert_eq!(super::stream_size((800, 600), full_hd), (800, 600));
    }
    #[test]
    fn a_switched_display_is_fitted_into_the_stream_size() {
        // 4x2 source with padded rows; left half blue, right half red.
        let mut pixels = vec![0u8; 2 * 20];
        for y in 0..2 {
            for x in 0..4 {
                let at = y * 20 + x * 4;
                pixels[at..at + 4].copy_from_slice(if x < 2 { &[255, 0, 0, 255] } else { &[0, 0, 255, 255] });
            }
        }
        let source = CapturedFrame::bgra(4, 2, 20, pixels).unwrap();
        let fitted = fit_frame(&source, (8, 4)).unwrap();
        assert_eq!((fitted.width(), fitted.height(), fitted.stride()), (8, 4, 32));
        let out = fitted.pixels();
        assert_eq!(&out[0..4], &[255, 0, 0, 255]);
        assert_eq!(&out[3 * 32 + 7 * 4..3 * 32 + 8 * 4], &[0, 0, 255, 255]);
        assert!(fit_frame(&source, (0, 4)).is_err());
    }
}

#[cfg(all(test, any(target_os = "macos", target_os = "linux", target_os = "windows")))]
mod renewal_tests {
    use super::*;

    pub(super) fn config(link: TransportLink, session_cancel: Arc<AtomicBool>, commands: mpsc::Sender<Command>) -> Config {
        let (_, signals) = mpsc::channel(1);
        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
        Config {
            state: RemoteSession::default(),
            generation: 0,
            session: Uuid::from_u128(1),
            slot: Uuid::from_u128(1),
            transport: Uuid::from_u128(2),
            link,
            previous: None,
            nonce: [1; 32],
            host: false,
            login_window: false,
            remote_key: ed25519_dalek::SigningKey::from_bytes(&[7; 32]).verifying_key(),
            identity: Arc::new(IdentityState::test_identity(ed25519_dalek::SigningKey::from_bytes(&[8; 32]))),
            source: String::new(),
            #[cfg(target_os = "linux")]
            portal: None,
            confirm: false,
            control: false,
            clipboard: false,
            files: false,
            chat: false,
            recording: false,
            consent_id: Uuid::from_u128(3),
            consent_cancel: Arc::new(AtomicBool::new(false)),
            consent_started: Instant::now(),
            session_cancel,
            account_cancel: Arc::new(AtomicBool::new(false)),
            signals,
            commands,
            video: Arc::new(VideoMailbox::default()),
            relay_only: true,
            ice_servers: Vec::new(),
            authorization: Arc::new(std::sync::Mutex::new((now + 600, Instant::now() + Duration::from_secs(60)))),
            peer_expires: Arc::new(AtomicU64::new(now + 600)),
            mirror: false,
            prewarmed: None,
        }
    }

    #[test]
    fn a_replaced_transport_stops_alone_and_its_grants_end_with_it() {
        let session = Arc::new(AtomicBool::new(false));
        let (commands, _receiver) = mpsc::channel(4);
        let before = config(TransportLink::default(), session.clone(), commands.clone());
        let renewed = config(TransportLink::default(), session.clone(), commands);
        let (input, files) = (before.input_authorization(), before.file_authorization());
        assert!(!before.stopped() && input() && files());
        before.link.retire();
        assert!(before.stopped(), "the replaced transport stops");
        assert!(!input(), "its input, file network and recording grants end with it");
        // The session's file scope is the session's, not the transport's: it goes on.
        assert!(files(), "the file scope outlives the renewal");
        assert!(!session.load(Ordering::Acquire), "the session goes on");
        assert!(!renewed.stopped() && renewed.input_authorization()());
        // Ending the session still stops every transport of it.
        session.store(true, Ordering::Release);
        assert!(renewed.stopped());
    }

    #[tokio::test]
    async fn the_end_of_a_replaced_transport_does_not_end_the_session() {
        let (commands, mut finished) = mpsc::channel(4);
        let session = Arc::new(AtomicBool::new(false));
        let replaced = TransportLink::default();
        replaced.retire();
        start(config(replaced.clone(), session.clone(), commands.clone())).unwrap();
        let Some(Command::Finished { session: transport, result }) = tokio::time::timeout(Duration::from_secs(5), finished.recv()).await.unwrap() else {
            panic!("the transport reports its end")
        };
        assert_eq!(transport, Uuid::from_u128(2), "it reports with the service's transport ID");
        assert!(result.is_ok());
        assert!(!session.load(Ordering::Acquire), "the session goes on");
        assert!(replaced.released.load(Ordering::Acquire));
        // Any other end of a transport ends the session, as before.
        let ending = config(TransportLink::default(), session.clone(), commands);
        ending.consent_cancel.store(true, Ordering::Release);
        start(ending).unwrap();
        assert!(matches!(tokio::time::timeout(Duration::from_secs(5), finished.recv()).await.unwrap(), Some(Command::Finished { .. })));
        assert!(session.load(Ordering::Acquire));
    }

    #[tokio::test]
    async fn the_renewed_host_transport_waits_for_the_capture_of_the_one_before() {
        let before = TransportLink::default();
        let released = before.released.clone();
        let closing = tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(30)).await;
            released.store(true, Ordering::Release);
        });
        let started = Instant::now();
        assert!(before.take_over(Duration::from_secs(2)).await);
        assert!(started.elapsed() >= Duration::from_millis(25));
        assert!(before.retire.load(Ordering::Acquire) && before.superseded.load(Ordering::Acquire));
        closing.await.unwrap();
        // One that does not stop in time is not waited for longer than the limit.
        let stuck = TransportLink::default();
        assert!(!stuck.take_over(Duration::from_millis(40)).await);
        assert!(stuck.retire.load(Ordering::Acquire));
    }
}

#[cfg(test)]
mod early_peer_tests {
    /// The early picture is made while the transport opens; with no screen to capture it stays empty, never sends, and stopping twice is harmless.
    #[test]
    fn an_early_picture_without_a_screen_stays_empty_and_stops_cleanly() {
        let mut early = EarlyPicture { capture: None, encoder: None, encoding: None, picture: None, sequence: 0, started: Instant::now(), size: (64, 64) };
        early.advance();
        assert!(early.picture.is_none());
        early.stop();
        early.stop();
    }

    use super::*;
    use ed25519_dalek::SigningKey;

    fn pair() -> (SigningKey, SigningKey, Context) {
        let (technician, host) = (SigningKey::from_bytes(&[7; 32]), SigningKey::from_bytes(&[9; 32]));
        let context = Context { session_id: [41; 32], controller: technician.verifying_key(), host: host.verifying_key() };
        (technician, host, context)
    }
    fn host_consent(context: &Context, decided: bool) -> Session {
        let mut consent = Session::for_peer(Instant::now(), context.clone(), Role::Host, true);
        if decided {
            consent.decide(true, Instant::now()).unwrap();
        }
        consent
    }

    /// The technician's signed identity bound to the certificate the transport really has: the host
    /// may send pictures, before the control channel exists.
    #[test]
    fn a_peer_whose_certificate_matches_its_signed_identity_is_verified_for_the_first_picture() {
        let (technician, host, context) = pair();
        let mut consent = host_consent(&context, true);
        assert!(!consent.can_share_local_screen(), "nothing is sent before the peer is verified");
        let mine = Handshake::new(context.clone(), Role::Host, &host, [2; 32]).unwrap();
        let theirs = Handshake::new(context, Role::Controller, &technician, [1; 32]).unwrap().hello();
        verify_peer(&mut consent, Some(mine), Some(theirs), [1; 32]).unwrap();
        assert!(consent.can_share_local_screen());
    }

    /// A peer with another certificate than the one in its signed identity (a man in the middle, or a
    /// replayed identity) gets no picture.
    #[test]
    fn a_peer_with_another_dtls_certificate_gets_no_picture() {
        let (technician, host, context) = pair();
        let mut consent = host_consent(&context, true);
        let mine = Handshake::new(context.clone(), Role::Host, &host, [2; 32]).unwrap();
        let theirs = Handshake::new(context, Role::Controller, &technician, [1; 32]).unwrap().hello();
        assert!(verify_peer(&mut consent, Some(mine), Some(theirs), [5; 32]).is_err());
        assert!(!consent.can_share_local_screen());
        assert!(!consent.can_view());
    }

    /// A signature by another identity than the enrolled technician gets no picture either, and a
    /// peer without any identity proof gets none.
    #[test]
    fn an_unenrolled_identity_or_a_missing_proof_gets_no_picture() {
        let (_, host, context) = pair();
        let stranger = SigningKey::from_bytes(&[3; 32]);
        let mut consent = host_consent(&context, true);
        let mine = Handshake::new(context.clone(), Role::Host, &host, [2; 32]).unwrap();
        let mut forged = Handshake::new(Context { controller: stranger.verifying_key(), ..context.clone() }, Role::Controller, &stranger, [1; 32]).unwrap().hello();
        forged.dtls_fingerprint = [1; 32];
        assert!(verify_peer(&mut consent, Some(mine), Some(forged), [1; 32]).is_err());
        assert!(!consent.can_share_local_screen());
        let mine = Handshake::new(context, Role::Host, &host, [2; 32]).unwrap();
        assert!(verify_peer(&mut consent, Some(mine), None, [1; 32]).is_err());
        assert!(!consent.can_share_local_screen());
    }

    /// A request that was refused (or never accepted) gets no picture although the certificate fits.
    #[test]
    fn a_request_that_was_not_accepted_gets_no_picture() {
        let (technician, host, context) = pair();
        let mut consent = host_consent(&context, false);
        let mine = Handshake::new(context.clone(), Role::Host, &host, [2; 32]).unwrap();
        let theirs = Handshake::new(context, Role::Controller, &technician, [1; 32]).unwrap().hello();
        assert!(verify_peer(&mut consent, Some(mine), Some(theirs), [1; 32]).is_err());
        assert!(!consent.can_share_local_screen());
    }

    /// An old peer announces nothing: today's order (the control channel first) stays.
    #[test]
    fn the_fast_picture_needs_the_attribute_from_the_peer() {
        let offer = "v=0\r\na=x-mixel-features:hd\r\n";
        assert!(!offer.lines().any(|line| line == FASTPIC_ATTRIBUTE));
        let offer = format!("{offer}{FASTPIC_ATTRIBUTE}\r\n");
        assert!(offer.lines().any(|line| line == FASTPIC_ATTRIBUTE));
    }
}

#[cfg(test)]
mod warm_tests {
    use super::*;

    fn ready(age: Duration) -> (Warm, std::sync::mpsc::Receiver<Config>) {
        let (adopt, adopted) = std::sync::mpsc::channel();
        let warm = Warm {
            adopt: Some(adopt),
            release: Arc::new(AtomicBool::new(false)),
            started: Instant::now() - age,
            lease: signaling_server::turn::IceLease { expires_at: 0, servers: Vec::new() },
        };
        (warm, adopted)
    }
    fn session_config() -> Config {
        let (commands, _) = mpsc::channel(4);
        renewal_tests::config(TransportLink::default(), Arc::new(AtomicBool::new(false)), commands)
    }

    /// A ready transport hands the session to its own thread; the session's configuration arrives there intact.
    #[test]
    fn a_ready_transport_takes_the_session_over() {
        let (warm, adopted) = ready(Duration::from_secs(3));
        assert!(warm.usable());
        assert!(warm.adopt(session_config()).is_ok());
        assert!(adopted.try_recv().is_ok());
    }

    /// One that ran out of time, was released or whose thread is gone is left out: the session starts the usual way
    /// and gets its configuration back.
    #[test]
    fn a_ready_transport_that_ran_out_or_is_gone_gives_the_session_back() {
        let (old, _kept) = ready(WARM_LIFETIME);
        assert!(!old.usable());
        assert!(old.adopt(session_config()).is_err());
        let (gone, adopted) = ready(Duration::from_secs(3));
        drop(adopted);
        assert!(gone.adopt(session_config()).is_err());
        let (released, _kept) = ready(Duration::from_secs(3));
        released.release.store(true, Ordering::Release);
        assert!(released.adopt(session_config()).is_err());
    }

    /// Leaving the Devices list (dropping the handle) releases the transport; a taken-over one is not released.
    #[test]
    fn dropping_the_handle_releases_the_transport_but_not_after_a_take_over() {
        let (warm, _kept) = ready(Duration::from_secs(1));
        let release = warm.release.clone();
        drop(warm);
        assert!(release.load(Ordering::Acquire));
        let (warm, _kept) = ready(Duration::from_secs(1));
        let release = warm.release.clone();
        assert!(warm.adopt(session_config()).is_ok());
        assert!(!release.load(Ordering::Acquire));
    }
}
