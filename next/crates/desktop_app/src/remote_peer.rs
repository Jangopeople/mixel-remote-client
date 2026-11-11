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
        NativeTransport, local_fingerprint,
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

pub struct Config {
    pub state: RemoteSession,
    pub generation: u64,
    pub session: Uuid,
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
    pub relay_only: bool,
    pub ice_servers: Vec<core_protocol::transport::NativeIceServer>,
    pub authorization: crate::remote_session::BrokerLease,
    pub peer_expires: Arc<AtomicU64>,
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
        session.store(true, Ordering::Release);
    }
    live
}
impl Config {
    pub(crate) fn stopped(&self) -> bool {
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
    pub(crate) fn input_authorization(&self) -> Arc<dyn Fn() -> bool + Send + Sync> {
        let account = self.account_cancel.clone();
        let session = self.session_cancel.clone();
        let peer = self.peer_expires.clone();
        let broker = self.authorization.clone();
        let consent = self.consent_cancel.clone();
        Arc::new(move || {
            if consent.load(Ordering::Acquire) {
                session.store(true, Ordering::Release);
                return false;
            }
            authorization_live(&account, &session, &peer, &broker)
        })
    }
    pub(crate) fn file_authorization(&self) -> Arc<dyn Fn() -> bool + Send + Sync> {
        let account = self.account_cancel.clone();
        let consent = self.consent_cancel.clone();
        let peer = self.peer_expires.clone();
        let broker = self.authorization.clone();
        let started = self.consent_started;
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
                session: self.session,
                signal,
            }),
        )
        .await
        .map_err(|_| "Signaling queue stalled")?
        .map_err(|_| "Connection service closed".to_owned())
    }
}
pub fn start(mut config: Config) -> Result<(), String> {
    let state = config.state.clone();
    let cancel = config.account_cancel.clone();
    state.spawn_media(&cancel, move || {
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()
                .map_err(|_| "Cannot start the media runtime".to_owned())
                .and_then(|runtime| runtime.block_on(run(&mut config)))
        }))
        .unwrap_or_else(|_| {
            Err("The native media owner failed; this connection was stopped".into())
        });
        if let Err(error) = &result
            && session_platform::login_window_mode()
        {
            eprintln!("Login window media ended: {error}");
        }
        config.session_cancel.store(true, Ordering::Release);
        let mut command = Command::Finished {
            session: config.session,
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
    })
}
async fn run(config: &mut Config) -> Result<(), String> {
    if config.stopped() {
        return Ok(());
    }
    let servers = std::mem::take(&mut config.ice_servers);
    let transport = if config.relay_only {
        #[cfg(target_os = "windows")]
        {
            NativeTransport::relay_stream_only(servers).await
        }
        #[cfg(not(target_os = "windows"))]
        {
            NativeTransport::relay_only("0.0.0.0:0".into(), servers).await
        }
    } else {
        NativeTransport::new("0.0.0.0:0".into(), servers).await
    };
    let mut transport = transport.map_err(|_| "Cannot create the native WebRTC transport")?;
    let result = async {
        let (mut consent, channel, sender, size, mut control) =
            negotiate(config, &mut transport).await?;
        let files = if let Some(bulk) = control.bulk.take() {
            Some(
                crate::remote_file_transport::FileTransport::start(config, &mut transport, bulk)
                    .await?,
            )
        } else {
            None
        };
        let chat = control
            .chat
            .take()
            .map(|crypto| crate::remote_chat::ChatTransport::start(config, &mut transport, crypto));
        let result = if config.host {
            host_media(
                config,
                &mut consent,
                &channel,
                sender.ok_or("Host video sender unavailable")?,
                size,
                &mut control,
                &mut transport,
            )
            .await
        } else {
            viewer_media(config, &mut consent, &channel, &mut transport, &mut control).await
        };
        config.session_cancel.store(true, Ordering::Release);
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
    if config.source == "qa-relay-fixture" && session_platform::relay_fixture_enabled() {
        return Ok((640, 420));
    }
    if !config.confirm {
        return Err("Whole-display sharing has not been confirmed".into());
    }
    #[cfg(target_os = "linux")]
    let (width, height) = if config.source == crate::wayland_portal::SOURCE {
        config
            .portal
            .as_ref()
            .filter(|grant| grant.live())
            .ok_or("The Wayland share grant has expired")?
            .size()
    } else {
        session_platform::display_size(&config.source)?
    };
    #[cfg(not(target_os = "linux"))]
    let (width, height) = session_platform::display_size(&config.source)?;
    if width == 0 || height == 0 {
        return Err("The display dimensions are invalid".into());
    }
    let scale = (1280.0 / width as f64).min(720.0 / height as f64).min(1.0);
    let minimum = if cfg!(target_os = "macos") { 2 } else { 16 };
    let width = ((width as f64 * scale) as usize / 2 * 2).max(minimum);
    let height = ((height as f64 * scale) as usize / 2 * 2).max(minimum);
    Ok((width, height))
}
type Negotiated = (
    Session,
    Arc<dyn NativeDataChannel>,
    Option<H264VideoSender>,
    (usize, usize),
    ControlPipe,
);
async fn negotiate(
    config: &mut Config,
    transport: &mut NativeTransport,
) -> Result<Negotiated, String> {
    const RECORDING_ATTRIBUTE: &str = "a=x-mixel-recording:1";
    const INPUT_RESULT_ATTRIBUTE: &str = "a=x-mixel-input-result:1";
    // Controller offer: it can switch between host displays. Host answer: how many, and which is shown.
    const DISPLAYS_ATTRIBUTE: &str = "a=x-mixel-displays:1";
    const DISPLAY_COUNT_PREFIX: &str = "a=x-mixel-display-count:";
    // Controller offer: it understands host actions. Host answer: the actions it offers
    // (comma list) and, if set up, the Remote ID of its sign-in screen access.
    const ACTIONS_ATTRIBUTE: &str = "a=x-mixel-actions:1";
    const HOST_ACTIONS_PREFIX: &str = "a=x-mixel-host-actions:";
    const SIGNIN_ID_PREFIX: &str = "a=x-mixel-signin-id:";
    const MACS_PREFIX: &str = "a=x-mixel-macs:";
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
            H264VideoSender::new(transport, size.0, size.1, 30)
                .await
                .map_err(|error| error.to_string())?,
        )
    } else {
        transport
            .receive_video()
            .await
            .map_err(|error| error.to_string())?;
        None
    };
    let mut channel = if config.host {
        None
    } else {
        Some(
            transport
                .peer
                .create_data_channel("mixel-control-v1", None)
                .await
                .map_err(|_| "Cannot create the encrypted control channel")?,
        )
    };
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
        let proof = config.identity.handshake(
            context.clone(),
            role,
            local_fingerprint(&offer.sdp).map_err(|error| error.to_string())?,
        )?;
        config.signal(Signal::Identity(proof.hello())).await?;
        let offer = format!(
            "{}\r\n{RECORDING_ATTRIBUTE}\r\n{INPUT_RESULT_ATTRIBUTE}\r\n{DISPLAYS_ATTRIBUTE}\r\n{ACTIONS_ATTRIBUTE}\r\n",
            offer.sdp.trim_end()
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
            && let Ok(value) = transport.incoming_channels.try_recv()
        {
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
                return Err(
                    "The peer did not provide the expected ordered reliable control channel".into(),
                );
            }
            channel = Some(value);
        }
        if remote_description && hello.is_some() && channel.is_some() {
            break;
        }
        let signal =
            match tokio::time::timeout(Duration::from_millis(25), config.signals.recv()).await {
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
                if config.control && sdp.lines().any(|line| line == ACTIONS_ATTRIBUTE) {
                    actions = tokio::task::spawn_blocking(crate::host_actions::available)
                        .await
                        .unwrap_or_default();
                }
                if config.control && sdp.lines().any(|line| line == DISPLAYS_ATTRIBUTE) {
                    let source = config.source.clone();
                    displays = tokio::task::spawn_blocking(move || {
                        session_platform::switchable_displays(&source)
                    })
                    .await
                    .unwrap_or_default();
                    displays.truncate(16);
                }
                transport
                    .peer
                    .set_remote_description(
                        NativeSessionDescription::offer(sdp).map_err(|_| "Invalid remote offer")?,
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
                let proof = config.identity.handshake(
                    context.clone(),
                    role,
                    local_fingerprint(&answer.sdp).map_err(|error| error.to_string())?,
                )?;
                config.signal(Signal::Identity(proof.hello())).await?;
                let current = displays.iter().position(|display| *display == config.source);
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
                        NativeSessionDescription::answer(sdp)
                            .map_err(|_| "Invalid remote answer")?,
                    )
                    .await
                    .map_err(|_| "Remote answer was rejected")?;
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
    let channel = channel.ok_or("Control transport is unavailable")?;
    loop {
        if config.stopped() {
            return Err("Session stopped during transport opening".into());
        }
        if Instant::now() >= deadline {
            return Err("Control transport opening timed out".into());
        }
        pump_ice(config, transport).await?;
        match tokio::time::timeout(Duration::from_millis(25), channel.poll()).await {
            Ok(Some(NativeDataChannelEvent::OnOpen)) => break,
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
    let control = handshake
        .ok_or("Local identity proof is unavailable")?
        .finish(
            &hello.ok_or("Remote identity proof is unavailable")?,
            transport
                .observed_fingerprint()
                .await
                .map_err(|_| "The actual remote DTLS certificate is unavailable")?,
        )
        .map_err(|_| "Remote identity or certificate verification failed")?;
    consent
        .authenticated_transport_ready(&control.verified_session(), Instant::now())
        .map_err(|error| error.to_string())?;
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
        },
    )
    .await?;
    if !config.host {
        config
            .state
            .set_displays(
                config.generation,
                config.session,
                if control.allowed { display_offer.0 } else { 0 },
                display_offer.1,
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
    Ok((consent, channel, sender, size, control))
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

struct EncodeRequest {
    frame: Arc<native_os_capture::stream::CapturedFrame>,
    timestamp: u64,
    keyframe: bool,
    reply: tokio::sync::oneshot::Sender<
        Result<(Option<native_os_capture::h264::EncodedH264>, bool), String>,
    >,
}

fn start_encoder(size: (usize, usize)) -> Result<mpsc::Sender<EncodeRequest>, String> {
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
            let mut encoder = ScreenH264Encoder::new(size.0, size.1, 30, 3_000_000);
            while let Some(request) = worker.blocking_recv() {
                let result = match &mut encoder {
                    Ok(encoder) => encoder
                        .encode(&request.frame, request.timestamp, request.keyframe)
                        .map(|encoded| (encoded, encoder.needs_retry())),
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

async fn host_media(
    config: &mut Config,
    consent: &mut Session,
    channel: &Arc<dyn NativeDataChannel>,
    sender: H264VideoSender,
    size: (usize, usize),
    control: &mut ControlPipe,
    transport: &mut NativeTransport,
) -> Result<(), String> {
    if config.stopped() {
        return Ok(());
    }
    let mut capture = session_platform::capture(
        &config.source,
        size,
        #[cfg(target_os = "linux")]
        config.portal.clone(),
    )?;
    let mut shown = config.source.clone();
    let result = async {
        let encoder = start_encoder(size)?;
        let mut retry_encode = false;
        let mut sequence = 0;
        let mut sent = 0u64;
        let started = Instant::now();
        let mut last = Instant::now();
        let mut recent: Option<Arc<native_os_capture::stream::CapturedFrame>> = None;
        while !config.stopped() {
            if !consent.can_share_local_screen() {
                return Err("Screen-sharing authorization expired".into());
            }
            pump_ice(config, transport).await?;
            control.poll(config, channel).await?;
            if let Some(source) = control.take_display_switch() {
                // Same encoder and stream; another display of different size is fitted
                // into the negotiated size (pointer positions are relative, so they stay right).
                // A display can be captured only once per process: close the current capture first.
                let _ = capture.stop();
                capture = match session_platform::capture(
                    &source,
                    size,
                    #[cfg(target_os = "linux")]
                    config.portal.clone(),
                ) {
                    Ok(next) => {
                        shown.clone_from(&source);
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
            let force_keyframe = control.take_keyframe_request() || retry_encode;
            let frame = match session_platform::frames(&capture).next(
                sequence,
                Duration::from_millis(if control.receiving_clipboard() {
                    1
                } else if control.allowed {
                    8
                } else {
                    100
                }),
            ) {
                Ok((next, frame)) => {
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
                    if force_keyframe && let Some(frame) = &recent {
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
            let (reply, mut output) = tokio::sync::oneshot::channel();
            encoder
                .try_send(EncodeRequest {
                    frame,
                    timestamp: started.elapsed().as_micros() as u64,
                    keyframe: force_keyframe || sent.is_multiple_of(60),
                    reply,
                })
                .map_err(|_| "Screen video encoder stopped")?;
            let deadline = Instant::now() + Duration::from_secs(5);
            let (encoded, retry) = loop {
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
            let Some(encoded) = encoded else {
                if sent > 0 && last.elapsed() >= Duration::from_secs(2) {
                    control.video_idle(channel).await?;
                }
                continue;
            };
            if config.stopped() {
                break;
            }
            let duration = last
                .elapsed()
                .clamp(Duration::from_micros(1), Duration::from_secs(2));
            last = Instant::now();
            sender
                .send(consent, &encoded.annex_b, duration)
                .await
                .map_err(|error| error.to_string())?;
            sent += 1;
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
async fn viewer_media(
    config: &mut Config,
    consent: &mut Session,
    channel: &Arc<dyn NativeDataChannel>,
    transport: &mut NativeTransport,
    control: &mut ControlPipe,
) -> Result<(), String> {
    let recording = control
        .recording_allowed
        .then(|| {
            config
                .state
                .recording_hub()
                .attach(config.session, config.input_authorization())
        })
        .transpose()?;
    let track_deadline = Instant::now() + Duration::from_secs(8);
    let track = loop {
        if config.stopped() {
            return Ok(());
        }
        if Instant::now() >= track_deadline {
            return Err("No remote video track arrived".into());
        }
        pump_ice(config, transport).await?;
        control.poll(config, channel).await?;
        control.request_keyframe(channel).await?;
        match tokio::time::timeout(Duration::from_millis(25), transport.incoming_tracks.recv())
            .await
        {
            Ok(Some(track)) => break track,
            Ok(None) => return Err("Remote video transport closed".into()),
            Err(_) => {}
        }
    };
    let mut assembler = H264Assembler::default();
    let mut decoder =
        session_platform::H264Decoder::new(1280, 720).map_err(|error| error.to_string())?;
    let mut previous_rtp: Option<u32> = None;
    let mut timestamp_ticks = 0u64;
    let mut received = 0u64;
    let mut last = Instant::now();
    while !config.stopped() {
        if !consent.can_view() {
            return Err("Remote-viewing authorization expired".into());
        }
        pump_ice(config, transport).await?;
        control.poll(config, channel).await?;
        if assembler.needs_keyframe()
            || recording
                .as_ref()
                .is_some_and(|scope| scope.needs_keyframe())
        {
            control.request_keyframe(channel).await?;
        }
        match tokio::time::timeout(Duration::from_millis(25), track.poll()).await {
            Ok(Some(TrackRemoteEvent::OnRtpPacket(packet))) => {
                if let Some(frame) = assembler
                    .push(
                        packet.header.sequence_number,
                        packet.header.timestamp,
                        packet.header.marker,
                        &packet.payload,
                    )
                    .map_err(|error| error.to_string())?
                {
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
                    let Some(decoded) = decoder
                        .decode(&frame, timestamp)
                        .map_err(|error| error.to_string())?
                    else {
                        control.request_keyframe(channel).await?;
                        continue;
                    };
                    if config.stopped() {
                        return Ok(());
                    }
                    if let Some(recording) = &recording {
                        recording.submit(
                            &frame,
                            timestamp_ticks,
                            (decoded.width() as u16, decoded.height() as u16),
                        );
                    }
                    received += 1;
                    last = Instant::now();
                    config.video.publish(decoded_packet(
                        config.session,
                        received,
                        timestamp,
                        &decoded,
                    )?)?;
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
            _ => {}
        }
        if last.elapsed() > Duration::from_secs(12)
            && control.last_video_idle.elapsed() > Duration::from_secs(12)
        {
            return Err("Remote video delivery timed out".into());
        }
    }
    Ok(())
}

#[cfg(test)]
mod display_fit_tests {
    use super::fit_frame;
    use native_os_capture::stream::CapturedFrame;
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
