//! One support session: the encrypted link to the technician's viewer, the phone's picture going
//! out and, where the phone allows it, taps and keys coming in. Same wire behaviour as the
//! desktop host (`desktop_app::remote_peer`, host side), without files, clipboard, chat or sound.
use crate::{Event, InputEvent, wire};
use core_protocol::{
    crypto::{Context, ControlChannel, Handshake, Hello, Role},
    input::Input,
    media::H264VideoSender,
    session::Session,
    transport::{
        NativeDataChannel, NativeDataChannelEvent, NativeIceCandidate, NativeIceServer, NativeSessionDescription, NativeTransport,
        local_fingerprint,
    },
};
use ed25519_dalek::{SigningKey, VerifyingKey};
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::sync::mpsc;

const CHANNEL: &str = "mixel-control-v1";
const FEATURES_PREFIX: &str = "a=x-mixel-features:";
const KEYFRAME: &[u8] = b"mixel-video-keyframe-v1";
const IDLE: &[u8] = b"mixel-video-idle-v1";
const POSTED: &[u8] = b"mixel-input-posted-v1";
const UNSUPPORTED: &[u8] = b"mixel-input-unsupported-v1";

pub struct Peer {
    pub servers: Vec<wire::IceServer>,
    pub relay_only: bool,
    /// Session binding from both identity tickets; the session id of the encrypted link.
    pub binding: [u8; 32],
    pub controller: VerifyingKey,
    pub key: SigningKey,
    /// The phone lets the technician operate it (Android with the accessibility service on).
    pub control: bool,
    pub size: (usize, usize),
    pub signals: mpsc::Receiver<wire::Signal>,
    pub out: mpsc::Sender<wire::Signal>,
    pub frames: Arc<tokio::sync::Mutex<mpsc::Receiver<Vec<u8>>>>,
    pub events: Arc<dyn Fn(Event) + Send + Sync>,
    pub stop: Arc<AtomicBool>,
    /// Unix seconds until which the technician's identity is confirmed (extended by renewals).
    pub peer_expiry: Arc<AtomicU64>,
}

fn unix() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|value| value.as_secs()).unwrap_or(0)
}

impl Peer {
    fn stopped(&self) -> bool {
        self.stop.load(Ordering::Acquire) || unix() >= self.peer_expiry.load(Ordering::Acquire)
    }
    async fn signal(&self, signal: wire::Signal) -> Result<(), String> {
        self.out.send(signal).await.map_err(|_| "The connection service link closed".to_owned())
    }
    async fn pump_ice(&self, transport: &mut NativeTransport) -> Result<(), String> {
        while let Ok(candidate) = transport.ice_candidates.try_recv() {
            self.signal(wire::Signal::Ice(serde_json::to_string(&candidate).map_err(|_| "Cannot encode a network candidate")?)).await?;
        }
        Ok(())
    }

    pub async fn run(mut self) -> Result<(), String> {
        // Encrypted relay addresses (turns:) need the system certificate store, which phones do
        // not offer to this code; the plain relay addresses carry the same end-to-end encrypted media.
        let servers: Vec<NativeIceServer> = std::mem::take(&mut self.servers)
            .into_iter()
            .filter_map(|server| {
                let urls: Vec<String> = server.urls.into_iter().filter(|url| !url.starts_with("turns:")).collect();
                (!urls.is_empty()).then_some(NativeIceServer { urls, username: server.username, credential: server.credential })
            })
            .collect();
        let transport = if self.relay_only {
            NativeTransport::relay_only("0.0.0.0:0".into(), servers).await
        } else {
            NativeTransport::new("0.0.0.0:0".into(), servers).await
        };
        let mut transport = transport.map_err(|_| "Cannot create the connection to the technician")?;
        let result = self.session(&mut transport).await;
        let _ = tokio::time::timeout(Duration::from_secs(5), transport.close()).await;
        result
    }

    async fn session(&mut self, transport: &mut NativeTransport) -> Result<(), String> {
        let deadline = Instant::now() + Duration::from_secs(30);
        let context = Context { session_id: self.binding, controller: self.controller, host: self.key.verifying_key() };
        let mut consent = Session::for_peer(Instant::now(), context.clone(), Role::Host, self.control);
        consent.decide(true, Instant::now()).map_err(|error| error.to_string())?;
        let sender = H264VideoSender::new(transport, self.size.0, self.size.1, 30).await.map_err(|error| error.to_string())?;

        // The technician's viewer offers; this side answers and proves its identity for the link.
        let mut handshake: Option<Handshake> = None;
        let mut hello: Option<Hello> = None;
        let mut channel: Option<Arc<dyn NativeDataChannel>> = None;
        let mut answered = false;
        let mut input_result = false;
        loop {
            if self.stopped() {
                return Err("The session was stopped while connecting".into());
            }
            if Instant::now() >= deadline {
                return Err("Connecting to the technician took too long".into());
            }
            self.pump_ice(transport).await?;
            if channel.is_none()
                && let Ok(value) = transport.incoming_channels.try_recv()
            {
                let valid = tokio::time::timeout(Duration::from_secs(1), async {
                    Ok::<_, core_protocol::transport::TransportError>(
                        value.label().await? == CHANNEL
                            && value.ordered().await?
                            && value.max_packet_life_time().await?.is_none()
                            && value.max_retransmits().await?.is_none()
                            && value.protocol().await?.is_empty()
                            && !value.negotiated().await?,
                    )
                })
                .await
                .map_err(|_| "The control channel could not be inspected")?
                .map_err(|_| "The control channel could not be inspected")?;
                if !valid {
                    let _ = tokio::time::timeout(Duration::from_secs(2), value.close()).await;
                    return Err("The viewer did not open the expected control channel".into());
                }
                channel = Some(value);
            }
            if answered && hello.is_some() && channel.is_some() {
                break;
            }
            let signal = match tokio::time::timeout(Duration::from_millis(25), self.signals.recv()).await {
                Ok(Some(signal)) => signal,
                Ok(None) => return Err("The connection service link closed".into()),
                Err(_) => continue,
            };
            match signal {
                wire::Signal::Identity(value) => {
                    if hello.is_some() || value.role == Role::Host {
                        return Err("Unexpected identity message".into());
                    }
                    hello = Some(value);
                }
                wire::Signal::Offer(sdp) if !answered => {
                    input_result = sdp.lines().any(|line| line == "a=x-mixel-input-result:1");
                    let features = sdp.lines().any(|line| line.starts_with(FEATURES_PREFIX));
                    transport
                        .peer
                        .set_remote_description(NativeSessionDescription::offer(sdp).map_err(|_| "Invalid offer")?)
                        .await
                        .map_err(|_| "The offer was rejected")?;
                    answered = true;
                    let answer = transport
                        .local_sdp(transport.peer.create_answer(None).await.map_err(|_| "Cannot create the answer")?)
                        .await
                        .map_err(|error| error.to_string())?;
                    let proof = Handshake::new(context.clone(), Role::Host, &self.key, local_fingerprint(&answer.sdp).map_err(|error| error.to_string())?)
                        .map_err(|error| error.to_string())?;
                    self.signal(wire::Signal::Identity(proof.hello())).await?;
                    let mut text = answer.sdp;
                    if features {
                        // Upright phone pictures are larger than 1280x720; the viewer must be told.
                        text = format!("{}\r\n{FEATURES_PREFIX}hd\r\n", text.trim_end());
                    }
                    self.signal(wire::Signal::Answer(text)).await?;
                    handshake = Some(proof);
                }
                wire::Signal::Ice(text) => {
                    let candidate: NativeIceCandidate = serde_json::from_str(&text).map_err(|_| "Invalid network candidate")?;
                    transport.peer.add_ice_candidate(candidate).await.map_err(|_| "A network candidate was rejected")?;
                }
                _ => return Err("Unexpected message while connecting".into()),
            }
        }
        let channel = channel.ok_or("The control channel is missing")?;
        loop {
            if self.stopped() {
                return Err("The session was stopped while connecting".into());
            }
            if Instant::now() >= deadline {
                return Err("Opening the control channel took too long".into());
            }
            self.pump_ice(transport).await?;
            match tokio::time::timeout(Duration::from_millis(25), channel.poll()).await {
                Ok(Some(NativeDataChannelEvent::OnOpen)) => break,
                Ok(None | Some(NativeDataChannelEvent::OnMessage(_) | NativeDataChannelEvent::OnClose | NativeDataChannelEvent::OnError)) => {
                    return Err("The control channel failed before it was confirmed".into());
                }
                _ => {}
            }
        }
        // Both sides prove who they are and that this very link (its certificate) is the one meant.
        let mut control = handshake
            .ok_or("The identity proof is missing")?
            .finish(
                &hello.ok_or("The viewer's identity proof is missing")?,
                transport.observed_fingerprint().await.map_err(|_| "The viewer's certificate is unavailable")?,
            )
            .map_err(|_| "The viewer's identity could not be confirmed")?;
        consent.authenticated_transport_ready(&control.verified_session(), Instant::now()).map_err(|error| error.to_string())?;
        // What the technician may do: operate (or view only); no clipboard, files or recording.
        self.send(&channel, &mut control, &[b"mixel-permissions-v4".as_slice(), &[u8::from(self.control), 0, 0, 0]].concat()).await?;
        (self.events)(Event::SessionStarted { control: self.control });
        let result = self.media(transport, &mut consent, &channel, &mut control, &sender, input_result).await;
        consent.close();
        let _ = tokio::time::timeout(Duration::from_secs(2), channel.close()).await;
        result
    }

    async fn send(&self, channel: &Arc<dyn NativeDataChannel>, control: &mut ControlChannel, plain: &[u8]) -> Result<(), String> {
        let record = control.seal(plain).map_err(|error| error.to_string())?;
        tokio::time::timeout(Duration::from_secs(3), channel.send(record.as_slice().into()))
            .await
            .map_err(|_| "Sending to the viewer timed out".to_owned())?
            .map_err(|_| "Sending to the viewer failed".to_owned())
    }

    async fn media(
        &mut self,
        transport: &mut NativeTransport,
        consent: &mut Session,
        channel: &Arc<dyn NativeDataChannel>,
        control: &mut ControlChannel,
        sender: &H264VideoSender,
        input_result: bool,
    ) -> Result<(), String> {
        let frames = self.frames.clone();
        let mut frames = frames.lock().await;
        while frames.try_recv().is_ok() {} // nothing older than this session
        (self.events)(Event::KeyframeWanted);
        let mut last = Instant::now();
        let mut last_idle = Instant::now();
        let mut last_keyframe = Instant::now() - Duration::from_secs(2);
        let mut sent = 0u64;
        let mut tick = tokio::time::interval(Duration::from_millis(100));
        loop {
            if self.stopped() {
                return Ok(());
            }
            if !consent.can_share_local_screen() {
                return Err("The permission to share the screen has ended".into());
            }
            self.pump_ice(transport).await?;
            tokio::select! {
                frame = frames.recv() => {
                    let frame = frame.ok_or("The screen picture stopped")?;
                    let duration = last.elapsed().clamp(Duration::from_micros(1), Duration::from_secs(2));
                    last = Instant::now();
                    sender.send(consent, &frame, duration).await.map_err(|error| error.to_string())?;
                    sent += 1;
                }
                event = channel.poll() => match event {
                    Some(NativeDataChannelEvent::OnMessage(message)) => {
                        if message.is_string || message.data.len() > core_protocol::input::MAX_INPUT_BYTES + 24 {
                            return Err("The viewer sent something unexpected".into());
                        }
                        let plain = zeroize::Zeroizing::new(control.open(&message.data).map_err(|error| error.to_string())?);
                        if plain.as_slice() == KEYFRAME {
                            if last_keyframe.elapsed() >= Duration::from_secs(1) {
                                last_keyframe = Instant::now();
                                (self.events)(Event::KeyframeWanted);
                            }
                            continue;
                        }
                        // Everything else is an input event; a phone offers no other functions.
                        if !self.control || !consent.can_inject_local() {
                            return Err("Operating this device is not allowed".into());
                        }
                        let reply = match core_protocol::input::decode(&plain).map_err(|error| error.to_string())? {
                            Input::Pointer { x, y, .. } => { (self.events)(Event::Input(InputEvent::Pointer { x, y })); POSTED }
                            Input::Button { button, down } => { (self.events)(Event::Input(InputEvent::Button { button, down })); POSTED }
                            Input::ButtonClick { button, down, .. } => { (self.events)(Event::Input(InputEvent::Button { button, down })); POSTED }
                            Input::Wheel { horizontal, vertical } => { (self.events)(Event::Input(InputEvent::Wheel { horizontal, vertical })); POSTED }
                            Input::Key { usage, down } => { (self.events)(Event::Input(InputEvent::Key { usage, down })); POSTED }
                            Input::Text(text) => { (self.events)(Event::Input(InputEvent::Text(text.to_owned()))); POSTED }
                            Input::ReleaseAll => { (self.events)(Event::Input(InputEvent::ReleaseAll)); POSTED }
                            #[allow(unreachable_patterns)]
                            _ => if input_result { UNSUPPORTED } else { POSTED },
                        };
                        self.send(channel, control, reply).await?;
                    }
                    None | Some(NativeDataChannelEvent::OnClose | NativeDataChannelEvent::OnError) => return Ok(()),
                    _ => {}
                },
                signal = self.signals.recv() => match signal {
                    Some(wire::Signal::Ice(text)) => {
                        if let Ok(candidate) = serde_json::from_str::<NativeIceCandidate>(&text) {
                            let _ = transport.peer.add_ice_candidate(candidate).await;
                        }
                    }
                    Some(_) => {}
                    None => return Ok(()),
                },
                _ = tick.tick() => {
                    // A still screen sends no pictures; tell the viewer the session is alive.
                    if sent > 0 && last.elapsed() >= Duration::from_secs(2) && last_idle.elapsed() >= Duration::from_secs(2) {
                        last_idle = Instant::now();
                        self.send(channel, control, IDLE).await?;
                    }
                }
            }
        }
    }
}
