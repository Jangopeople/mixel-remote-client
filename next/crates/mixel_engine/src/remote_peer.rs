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
    /// Host: a colleague who joined; sends the first technician's pictures instead of capturing.
    pub mirror: bool,
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
    pub fn stopped(&self) -> bool {
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
        Arc::new(move || {
            if consent.load(Ordering::Acquire) {
                session.store(true, Ordering::Release);
                return false;
            }
            authorization_live(&account, &session, &peer, &broker)
        })
    }
    pub fn file_authorization(&self) -> Arc<dyn Fn() -> bool + Send + Sync> {
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
    let mirror = config.mirror;
    state.spawn_media(&cancel, mirror, move || {
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
        // Lab builds only: why each session ended, with the time (both sides).
        #[cfg(feature = "qa-session")]
        eprintln!(
            "QA session end {} host={} at {:?}: {result:?}",
            config.session,
            config.host,
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs()
        );
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
        let result = if config.host && config.mirror {
            mirror_media(config, &mut consent, &channel, sender.ok_or("Host video sender unavailable")?, &mut control, &mut transport).await
        } else if config.host {
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
    source_size_within(config, (1280.0, 720.0))
}
/// The stream size for the shared display, scaled down to fit `limit` (never up).
fn source_size_within(config: &Config, limit: (f64, f64)) -> Result<(usize, usize), String> {
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
    let scale = (limit.0 / width as f64).min(limit.1 / height as f64).min(1.0);
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
                    .filter(|name| !name.is_empty() && name.len() <= 16 && name.bytes().all(|byte| byte.is_ascii_alphanumeric()))
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
            "{}\r\n{RECORDING_ATTRIBUTE}\r\n{INPUT_RESULT_ATTRIBUTE}\r\n{DISPLAYS_ATTRIBUTE}\r\n{ACTIONS_ATTRIBUTE}\r\n{FEATURES_PREFIX}{own_features}\r\n",
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
                peer_features = parse_features(&sdp);
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
                }
                if !peer_features.is_empty() {
                    answer_sdp = format!("{}\r\n{FEATURES_PREFIX}{own_features}\r\n", answer_sdp.trim_end());
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
            features: peer_features.clone(),
        },
    )
    .await?;
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
    let size = if config.host && peer_features.iter().any(|name| name == crate::remote_control::FULL_HD) {
        source_size_within(config, (1920.0, 1080.0))?
    } else {
        size
    };
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
    Duration::from_millis(if taken_this_turn <= 1 { 25 } else { 0 })
}

#[cfg(test)]
mod packet_turn_tests {
    use super::*;

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
        assert_eq!(packet_wait(1), Duration::from_millis(25));
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
            let bitrate = if size.0 > 1280 || size.1 > 720 { 6_000_000 } else { 3_000_000 };
            let mut encoder = ScreenH264Encoder::new(size.0, size.1, 30, bitrate);
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
    let mut capture = session_platform::capture(
        &config.source,
        size,
        #[cfg(target_os = "linux")]
        config.portal.clone(),
    )?;
    #[cfg(feature = "qa-session")]
    let capture_ready = setup.elapsed();
    let mut shown = config.source.clone();
    // The technician's pointer is placed on the shared display.
    crate::session_guard::set_shared_source(&shown);
    let result = async {
        let encoder = start_encoder(size)?;
        let mut retry_encode = false;
        let mut sequence = 0;
        let mut sent = 0u64;
        let started = Instant::now();
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
        let mirror = crate::session_mirror::publish();
        // Remote sound: what this computer plays, while the technician wants to hear it.
        let mut sound: Option<crate::remote_sound::Capture> = None;
        let mut sound_failed = false;
        let mut pacer = crate::video_pacing::Pacer::new(Instant::now());
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
                        crate::session_guard::set_shared_source(&shown);
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
            let requested = control.take_keyframe_request();
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
            #[cfg(feature = "qa-session")]
            let (capture_time, lap) = (lap.elapsed(), Instant::now());
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
            #[cfg(feature = "qa-session")]
            let (encode_time, lap, bytes) = (lap.elapsed(), Instant::now(), encoded.annex_b.len());
            let duration = last
                .elapsed()
                .clamp(Duration::from_micros(1), Duration::from_secs(2));
            last = Instant::now();
            if !send_picture(&sender, &mut pacer, consent, &encoded.annex_b, duration, config, transport, control, channel).await? {
                break;
            }
            // Lab builds only: a picture whose capture, encoding or sending took long (input waits meanwhile).
            #[cfg(feature = "qa-session")]
            if capture_time + encode_time + lap.elapsed() > Duration::from_millis(400) {
                eprintln!("QA slow picture at {} ms: capture {} ms, encode {} ms, send {} ms, {} bytes, pace {:.0} kB/s", setup.elapsed().as_millis(), capture_time.as_millis(), encode_time.as_millis(), lap.elapsed().as_millis(), bytes, pacer.rate() / 1000.0);
            }
            mirror.send(&encoded.annex_b, duration);
            sent += 1;
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
        async |packet| sender.write(consent, packet).await.map_err(|error| error.to_string()),
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
        // exactly the connections that are slow. (A technician who still needs one asks again.)
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
                    if !send_picture(&sender, &mut pacer, consent, &picture.annex_b, picture.duration, config, transport, control, channel).await? {
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
    // Lab builds only: when the first picture is there, counted from the start of the picture stream.
    #[cfg(feature = "qa-session")]
    let qa_started = Instant::now();
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
    let (mut window_decodes, mut waiting_for_key) = (false, false);
    let mut player: Option<crate::remote_sound::Player> = None;
    let mut player_failed = false;
    while !config.stopped() {
        if !consent.can_view() {
            return Err("Remote-viewing authorization expired".into());
        }
        // Lab builds only: a part of this loop that took long (input and its confirmations wait meanwhile).
        #[cfg(feature = "qa-session")]
        let qa_turn = Instant::now();
        pump_ice(config, transport).await?;
        #[cfg(feature = "qa-session")]
        let qa_ice = qa_turn.elapsed();
        control.poll(config, channel).await?;
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
        while turn_goes_on(taken, picture_complete) {
            taken += 1;
            match tokio::time::timeout(packet_wait(taken), track.poll()).await {
                Ok(Some(TrackRemoteEvent::OnRtpPacket(packet))) => {
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
                    if let Some(frame) = assembler
                        .push(
                            packet.header.sequence_number,
                            packet.header.timestamp,
                            packet.header.marker,
                            &packet.payload,
                        )
                        .map_err(|error| error.to_string())?
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
                        // a recording needs the picture size, so it keeps the native decoder.
                        let compressed = config.video.wants_compressed() && recording.is_none();
                        if compressed != window_decodes {
                            window_decodes = compressed;
                            waiting_for_key = true;
                            if !compressed {
                                decoder = session_platform::H264Decoder::new(1920, 1920)
                                    .map_err(|error| error.to_string())?;
                            }
                            control.request_keyframe(channel).await?;
                        }
                        if waiting_for_key || compressed {
                            let key = native_os_capture::h264::annex_b_to_avcc(&frame)
                                .map_err(|_| "Invalid remote video")?
                                .keyframe;
                            if waiting_for_key && !key {
                                control.request_keyframe(channel).await?;
                                continue;
                            }
                            waiting_for_key = false;
                            if compressed {
                                received += 1;
                                last = Instant::now();
                                #[cfg(feature = "qa-session")]
                                if received == 1 {
                                    eprintln!("QA first picture after {} ms: {} packets, {qa_gaps} gaps ({qa_lost} numbers missing), {} requests for a complete picture", qa_started.elapsed().as_millis(), qa_rtp.0, qa_rtp.3);
                                }
                                let packet = crate::remote_video::compressed_packet(config.session, received, timestamp, &frame, key)?;
                                if !config.video.publish_compressed(packet)? {
                                    waiting_for_key = true;
                                    control.request_keyframe(channel).await?;
                                }
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
                        #[cfg(feature = "qa-session")]
                        if received == 1 {
                            eprintln!("QA first picture after {} ms: {} packets, {qa_gaps} gaps ({qa_lost} numbers missing), {} requests for a complete picture", qa_started.elapsed().as_millis(), qa_rtp.0, qa_rtp.3);
                        }
                        #[cfg(feature = "qa-session")]
                        let (decode_time, lap) = (lap.elapsed(), Instant::now());
                        config.video.publish(decoded_packet(
                            config.session,
                            received,
                            timestamp,
                            &decoded,
                        )?)?;
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
        if last.elapsed() > Duration::from_secs(12)
            && control.last_video_idle.elapsed() > Duration::from_secs(12)
        {
            #[cfg(feature = "qa-session")]
            eprintln!("QA video timed out: {} RTP packets ({} bytes, {} with the end mark), {} pictures assembled, {} decoded, {} keyframe requests; {qa_keys} keyframe slices begun, {qa_gaps} gaps in the numbering ({qa_lost} numbers missing), {qa_late} packets late", qa_rtp.0, qa_rtp.1, qa_rtp.4, qa_rtp.2, received, qa_rtp.3);
            #[cfg(feature = "qa-session")]
            eprintln!("QA video jumps: {}", qa_jumps.join(" | "));
            return Err("Remote video delivery timed out".into());
        }
    }
    #[cfg(feature = "qa-session")]
    eprintln!("QA picture stream ended after {} ms: {received} pictures, {} packets, {qa_gaps} gaps ({qa_lost} numbers missing), {} requests for a complete picture", qa_started.elapsed().as_millis(), qa_rtp.0, qa_rtp.3);
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
