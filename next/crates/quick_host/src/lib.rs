//! Mixel Quick Support host core.
//!
//! A phone (or any device without a Mixel account) registers itself with a one-time support code,
//! goes online, asks its user when a technician wants to connect, and then shares its screen in
//! one attended session. The app around this core supplies the encoded screen picture
//! ([`Host::push_frame`]) and, where the system allows it, carries out the technician's input
//! ([`Event::Input`]). Nothing here needs a signed-in user and nothing here grants unattended access.
#[cfg(target_os = "android")]
mod android;
pub mod ffi;
mod session;
mod wire;

use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use core_protocol::{
    delegation::{DelegationProof, DeviceTicket, session_binding},
    device_login::DeviceLoginChallenge,
};
use ed25519_dalek::{SigningKey, VerifyingKey};
use futures_util::{SinkExt, StreamExt};
use rand_core::{OsRng, RngCore};
use serde::{Deserialize, Serialize};
use std::{
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::Duration,
};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;
use uuid::Uuid;

/// Where the Mixel services are. [`Config::production`] is what the apps use.
#[derive(Clone, Deserialize)]
pub struct Config {
    pub identity_url: String,
    /// The public (anonymous) key of the account service.
    pub public_key: String,
    pub signaling_url: String,
    pub authority_url: String,
    /// Hex Ed25519 keys of the identity authority; tickets are checked against them.
    pub authority_keys: Vec<String>,
    pub relay_only: bool,
}
impl Config {
    pub fn production() -> Self {
        serde_json::from_str(include_str!("../../../deployment/public-service-config.json")).expect("the bundled service configuration is valid")
    }
    fn roots(&self) -> Result<Vec<VerifyingKey>, String> {
        self.authority_keys
            .iter()
            .map(|text| {
                let bytes: Vec<u8> = (0..text.len() / 2).filter_map(|index| u8::from_str_radix(text.get(index * 2..index * 2 + 2)?, 16).ok()).collect();
                let bytes: [u8; 32] = bytes.try_into().map_err(|_| "Invalid authority key")?;
                VerifyingKey::from_bytes(&bytes).map_err(|_| "Invalid authority key".to_owned())
            })
            .collect()
    }
}

/// The temporary registration a support code gave this device.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Enrollment {
    pub device_id: Uuid,
    pub connection_id: u32,
    pub generation: u64,
    pub profile: Uuid,
    pub tenant: Uuid,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Connecting,
    /// Online; the technician can send a request to this device's Remote ID.
    Ready,
    /// A technician asked; waiting for the person at the device ([`Host::decide`]).
    Asking,
    Negotiating,
    Active,
    Offline,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum InputEvent {
    /// Position on the shared picture, 0..=65535 from left to right and top to bottom.
    Pointer { x: u16, y: u16 },
    /// 1 = main (touch), 2 = secondary (back, on a phone), 3 = middle (home).
    Button { button: u8, down: bool },
    /// 120 = one notch.
    Wheel { horizontal: i16, vertical: i16 },
    /// USB keyboard usage code.
    Key { usage: u16, down: bool },
    Text(String),
    ReleaseAll,
}

#[derive(Clone, Debug)]
pub enum Event {
    Status(Phase, String),
    /// A technician wants to connect. `staff` is true for a Mixel team member.
    Request { connection_id: u32, staff: bool },
    SessionStarted { control: bool },
    SessionEnded(String),
    Input(InputEvent),
    /// The viewer needs a complete picture: make the encoder produce a keyframe.
    KeyframeWanted,
}

impl Event {
    /// The event as a small JSON text, the form in which the phone apps receive it.
    pub fn json(&self) -> String {
        match self {
            Self::Status(phase, message) => serde_json::json!({ "type": "status", "phase": format!("{phase:?}").to_lowercase(), "message": message }),
            Self::Request { connection_id, staff } => serde_json::json!({ "type": "request", "connection_id": connection_id, "staff": staff }),
            Self::SessionStarted { control } => serde_json::json!({ "type": "started", "control": control }),
            Self::SessionEnded(message) => serde_json::json!({ "type": "ended", "message": message }),
            Self::KeyframeWanted => serde_json::json!({ "type": "keyframe" }),
            Self::Input(InputEvent::Pointer { x, y }) => serde_json::json!({ "type": "pointer", "x": x, "y": y }),
            Self::Input(InputEvent::Button { button, down }) => serde_json::json!({ "type": "button", "button": button, "down": down }),
            Self::Input(InputEvent::Wheel { horizontal, vertical }) => serde_json::json!({ "type": "wheel", "horizontal": horizontal, "vertical": vertical }),
            Self::Input(InputEvent::Key { usage, down }) => serde_json::json!({ "type": "key", "usage": usage, "down": down }),
            Self::Input(InputEvent::Text(text)) => serde_json::json!({ "type": "text", "text": text }),
            Self::Input(InputEvent::ReleaseAll) => serde_json::json!({ "type": "release" }),
        }
        .to_string()
    }
}

enum Command {
    Decide(bool),
    End,
}

struct Inner {
    config: Config,
    key: SigningKey,
    events: Arc<dyn Fn(Event) + Send + Sync>,
    commands: Mutex<Option<mpsc::UnboundedSender<Command>>>,
    frames_in: mpsc::Sender<Vec<u8>>,
    frames_out: Arc<tokio::sync::Mutex<mpsc::Receiver<Vec<u8>>>>,
    streaming: AtomicBool,
    online: AtomicBool,
    http: reqwest::Client,
}

pub struct Host {
    inner: Arc<Inner>,
    runtime: tokio::runtime::Runtime,
}

/// Lab tracing: with MQS_LOG set, the steps of a connection go to the error output.
fn trace(text: &str) {
    static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    if *ON.get_or_init(|| std::env::var_os("MQS_LOG").is_some()) {
        eprintln!("[{}] {text}", unix() % 100_000);
    }
}
fn unix() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|value| value.as_secs()).unwrap_or(0)
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

impl Host {
    /// `seed` is this device's stored key, or `None` on first start (store [`Host::seed`] afterwards).
    pub fn new(config: Config, seed: Option<[u8; 32]>, events: impl Fn(Event) + Send + Sync + 'static) -> Result<Self, String> {
        config.roots()?;
        let key = match seed {
            Some(bytes) => SigningKey::from_bytes(&bytes),
            None => {
                let mut bytes = [0u8; 32];
                OsRng.fill_bytes(&mut bytes);
                SigningKey::from_bytes(&bytes)
            }
        };
        let runtime = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build().map_err(|_| "Cannot start the host core")?;
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(20))
            .redirect(reqwest::redirect::Policy::none())
            .https_only(true)
            .build()
            .map_err(|_| "Cannot start secure connections")?;
        let (frames_in, frames_out) = mpsc::channel(4);
        Ok(Self {
            inner: Arc::new(Inner {
                config,
                key,
                events: Arc::new(events),
                commands: Mutex::new(None),
                frames_in,
                frames_out: Arc::new(tokio::sync::Mutex::new(frames_out)),
                streaming: AtomicBool::new(false),
                online: AtomicBool::new(false),
                http,
            }),
            runtime,
        })
    }
    pub fn seed(&self) -> [u8; 32] {
        self.inner.key.to_bytes()
    }
    /// Registers this device with a support code from the technician. Blocks; call off the main thread.
    pub fn enroll(&self, code: &str, device_name: &str) -> Result<Enrollment, String> {
        self.runtime.block_on(self.inner.enroll(code, device_name))
    }
    /// Goes online with a registration. `width` x `height` is the size of the pictures that
    /// [`Host::push_frame`] will deliver; `control` says whether the technician may operate the device.
    pub fn start(&self, enrollment: Enrollment, width: u32, height: u32, control: bool) -> Result<(), String> {
        if width == 0 || height == 0 || !width.is_multiple_of(2) || !height.is_multiple_of(2) || width.max(height) > 1920 || width.min(height) > 1088 {
            return Err("Unsupported picture size".into());
        }
        if self.inner.online.swap(true, Ordering::AcqRel) {
            return Err("Already online".into());
        }
        let (sender, receiver) = mpsc::unbounded_channel();
        *self.inner.commands.lock().map_err(|_| "Host state unavailable")? = Some(sender);
        let inner = self.inner.clone();
        self.runtime.spawn(async move {
            inner.clone().online(enrollment, (width as usize, height as usize), control, receiver).await;
            inner.online.store(false, Ordering::Release);
            inner.streaming.store(false, Ordering::Release);
            (inner.events)(Event::Status(Phase::Offline, String::new()));
        });
        Ok(())
    }
    /// The answer of the person at the device to [`Event::Request`].
    pub fn decide(&self, accept: bool) {
        self.command(Command::Decide(accept));
    }
    pub fn end_session(&self) {
        self.command(Command::End);
    }
    /// Goes offline and ends a running session.
    pub fn stop(&self) {
        if let Ok(mut commands) = self.inner.commands.lock() {
            *commands = None; // closing the channel stops the online loop
        }
    }
    fn command(&self, command: Command) {
        if let Ok(commands) = self.inner.commands.lock()
            && let Some(sender) = commands.as_ref()
        {
            let _ = sender.send(command);
        }
    }
    /// One encoded picture: an H.264 access unit in Annex-B form (start codes), constrained
    /// baseline, with parameter sets in front of every keyframe. Dropped while no session runs.
    pub fn push_frame(&self, annex_b: &[u8]) {
        if !self.inner.streaming.load(Ordering::Acquire) || annex_b.len() > core_protocol::MAX_VIDEO_ACCESS_UNIT {
            return;
        }
        if self.inner.frames_in.try_send(annex_b.to_vec()).is_err() {
            // The link is slower than the screen: skip this picture and resync with a keyframe.
            (self.inner.events)(Event::KeyframeWanted);
        }
    }
}

struct Asked {
    session: Uuid,
    controller: VerifyingKey,
    expires_at: u64,
}
struct Running {
    session: Uuid,
    controller: VerifyingKey,
    to_peer: Option<mpsc::Sender<wire::Signal>>,
    buffered: Vec<wire::Signal>,
    binding: [u8; 32],
    stop: Arc<AtomicBool>,
    peer_expiry: Arc<AtomicU64>,
    task: Option<tokio::task::JoinHandle<Result<(), String>>>,
}

impl Inner {
    fn status(&self, phase: Phase, message: &str) {
        (self.events)(Event::Status(phase, message.to_owned()));
    }

    async fn enroll(&self, code: &str, device_name: &str) -> Result<Enrollment, String> {
        let response = self
            .http
            .post(format!("{}/rest/v1/rpc/remote_enroll_with_support_code", self.config.identity_url))
            .header("apikey", &self.config.public_key)
            .bearer_auth(&self.config.public_key)
            .header("accept", "application/vnd.pgrst.object+json")
            .json(&serde_json::json!({ "code": code, "device_name": device_name, "key_material": format!("\\x{}", hex(self.key.verifying_key().as_bytes())) }))
            .send()
            .await
            .map_err(|_| "The Mixel service cannot be reached")?;
        if !response.status().is_success() {
            return Err(if response.status().as_u16() == 403 { "This support code is not valid or has expired".into() } else { "The support code was not accepted".to_owned() });
        }
        response.json().await.map_err(|_| "The Mixel service answered unexpectedly".into())
    }

    /// A short-lived identity ticket for this device alone (no user): proof with the device key,
    /// checked by the identity authority against the registration.
    async fn ticket(&self, enrollment: &Enrollment) -> Result<DeviceTicket, String> {
        let mut nonce = [0u8; 32];
        OsRng.fill_bytes(&mut nonce);
        let challenge = DeviceLoginChallenge {
            nonce,
            device: *enrollment.device_id.as_bytes(),
            profile: *enrollment.profile.as_bytes(),
            tenant: Some(*enrollment.tenant.as_bytes()),
            connection_id: enrollment.connection_id,
            generation: enrollment.generation,
        };
        let proof = DelegationProof::new_unattended_host(challenge.clone(), unix(), &self.key).map_err(|error| error.to_string())?;
        let response = self
            .http
            .post(format!("{}/v1/startup-host-ticket", self.config.authority_url))
            .json(&serde_json::json!({ "proof": proof, "public_key": self.key.verifying_key().to_bytes() }))
            .send()
            .await
            .map_err(|_| "The identity service cannot be reached")?;
        if !response.status().is_success() {
            return Err(format!("The identity service did not confirm this device ({})", response.status().as_u16()));
        }
        let bytes = response.bytes().await.map_err(|_| "The identity service answer was interrupted")?;
        if bytes.len() > 8192 {
            return Err("The identity service answer is too large".into());
        }
        let ticket: DeviceTicket = serde_json::from_slice(&bytes).map_err(|_| "Invalid identity ticket")?;
        let verified = ticket.verify(&self.config.roots()?, unix()).map_err(|_| "The identity ticket is not signed by Mixel")?;
        if verified.key() != self.key.verifying_key() || ticket.identity != challenge || !ticket.unattended_host_only() || ticket.staff {
            return Err("The identity ticket does not match this device".into());
        }
        Ok(ticket)
    }

    fn encode(ticket: &DeviceTicket) -> Result<String, String> {
        Ok(URL_SAFE_NO_PAD.encode(serde_json::to_vec(ticket).map_err(|_| "Cannot encode the identity ticket")?))
    }

    /// The technician's identity: signed by Mixel, for the connection that asked, allowed to ask.
    fn technician(&self, ticket: &DeviceTicket, connection: u32, enrollment: &Enrollment) -> Result<VerifyingKey, String> {
        let verified = ticket.verify(&self.config.roots()?, unix()).map_err(|_| "The technician's identity is not confirmed by Mixel")?;
        if ticket.identity.connection_id != connection || ticket.unattended_host_only() {
            return Err("The technician's identity does not match the request".into());
        }
        if !ticket.staff && ticket.identity.tenant != Some(*enrollment.tenant.as_bytes()) {
            return Err("This person may not connect to this device".into());
        }
        Ok(verified.key())
    }

    async fn online(self: Arc<Self>, enrollment: Enrollment, size: (usize, usize), control: bool, mut commands: mpsc::UnboundedReceiver<Command>) {
        let mut wait = 1u64;
        loop {
            self.status(Phase::Connecting, "");
            match self.clone().connected(&enrollment, size, control, &mut commands).await {
                Ok(()) => return, // stopped on purpose
                Err(error) => {
                    self.streaming.store(false, Ordering::Release);
                    self.status(Phase::Connecting, &error);
                }
            }
            // Try again after 1, 2, 4 … 30 seconds, unless the app stops the host meanwhile.
            let pause = tokio::time::sleep(Duration::from_secs(wait));
            tokio::pin!(pause);
            loop {
                tokio::select! {
                    _ = &mut pause => break,
                    command = commands.recv() => if command.is_none() { return },
                }
            }
            wait = (wait * 2).min(30);
        }
    }

    async fn connected(self: Arc<Self>, enrollment: &Enrollment, size: (usize, usize), control: bool, commands: &mut mpsc::UnboundedReceiver<Command>) -> Result<(), String> {
        let mut ticket = self.ticket(enrollment).await?;
        let (socket, _) = tokio::time::timeout(Duration::from_secs(15), tokio_tungstenite::connect_async(self.config.signaling_url.as_str()))
            .await
            .map_err(|_| "The connection service did not answer")?
            .map_err(|_| "The connection service cannot be reached")?;
        let (mut write, mut read) = socket.split();
        macro_rules! say {
            ($message:expr) => {
                write.send(Message::Text(serde_json::to_string(&$message).map_err(|_| "Cannot encode a message")?.into())).await.map_err(|_| "The connection service link closed".to_owned())?
            };
        }
        async fn next(read: &mut (impl StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>> + Unpin), seconds: u64) -> Result<wire::In, String> {
            loop {
                let message = tokio::time::timeout(Duration::from_secs(seconds), read.next())
                    .await
                    .map_err(|_| "The connection service did not answer")?
                    .ok_or("The connection service link closed")?
                    .map_err(|_| "The connection service link failed")?;
                if let Message::Text(text) = message {
                    return serde_json::from_str(text.as_str()).map_err(|_| "Unexpected message from the connection service".to_owned());
                }
            }
        }
        say!(wire::Out::AuthenticateDevice { token: Self::encode(&ticket)?, device: enrollment.device_id });
        let wire::In::DeviceChallenge { challenge } = next(&mut read, 10).await? else {
            return Err("The connection service refused this device".into());
        };
        if challenge.device != ticket.identity.device
            || challenge.profile != ticket.identity.profile
            || challenge.tenant != ticket.identity.tenant
            || challenge.connection_id != ticket.identity.connection_id
            || challenge.generation != ticket.identity.generation
        {
            return Err("The connection service asked for another device".into());
        }
        say!(wire::Out::ProveDevice { signature: challenge.sign(&self.key).map_err(|error| error.to_string())? });
        match next(&mut read, 10).await? {
            wire::In::Ready { host, protocol } if host == enrollment.connection_id && protocol == core_protocol::PROTOCOL_VERSION => {}
            _ => return Err("The connection service did not accept this device".into()),
        }
        // Attended requests only: the person at this device decides about every session.
        say!(wire::Out::ConfigureSupportHost { enabled: true });
        match next(&mut read, 10).await? {
            wire::In::SupportHostStatus { enabled: true } => {}
            _ => return Err("The connection service does not offer Quick Support for this device".into()),
        }
        self.status(Phase::Ready, "");

        let (from_peer, mut peer_signals) = mpsc::channel::<wire::Signal>(64);
        let mut asked: Option<Asked> = None;
        let mut running: Option<Running> = None;
        let mut renew = tokio::time::interval_at(tokio::time::Instant::now() + Duration::from_secs(31), Duration::from_secs(31));
        let mut ping = tokio::time::interval_at(tokio::time::Instant::now() + Duration::from_secs(20), Duration::from_secs(20));
        loop {
            // A finished session task (ended by either side, or failed) closes the session.
            if let Some(run) = running.as_mut()
                && run.task.as_ref().is_some_and(tokio::task::JoinHandle::is_finished)
            {
                let outcome = match run.task.take() {
                    Some(task) => task.await.unwrap_or_else(|_| Err("The session stopped unexpectedly".into())),
                    None => Ok(()),
                };
                trace(&format!("session task finished: {outcome:?}"));
                say!(wire::Out::Close { session: run.session });
                running = None;
                self.streaming.store(false, Ordering::Release);
                (self.events)(Event::SessionEnded(outcome.err().unwrap_or_default()));
                self.status(Phase::Ready, "");
            }
            tokio::select! {
                message = read.next() => {
                    let message = message.ok_or("The connection service link closed")?.map_err(|_| "The connection service link failed")?;
                    let Message::Text(text) = message else { continue };
                    let Ok(message) = serde_json::from_str::<wire::In>(text.as_str()) else { trace(&format!("unreadable message: {}", text.chars().take(160).collect::<String>())); continue };
                    if !matches!(message, wire::In::Signal { .. } | wire::In::Renewed { .. } | wire::In::Other) { trace(&format!("message: {}", text.chars().take(90).collect::<String>())); }
                    match message {
                        wire::In::Consent { session, requester_connection, requester, unattended, requester_identity } => {
                            let checked = requester_identity
                                .as_ref()
                                .filter(|identity| !unattended && identity.identity.profile == *requester.as_bytes())
                                .ok_or_else(|| "The request carries no confirmed identity".to_owned())
                                .and_then(|identity| self.technician(identity, requester_connection, enrollment).map(|key| (key, identity.expires_at, identity.staff)));
                            match checked {
                                Ok((controller, expires_at, staff)) if asked.is_none() && running.is_none() => {
                                    asked = Some(Asked { session, controller, expires_at });
                                    self.status(Phase::Asking, "");
                                    (self.events)(Event::Request { connection_id: requester_connection, staff });
                                }
                                _ => say!(wire::Out::Decide { session, accept: false }),
                            }
                        }
                        wire::In::Accepted { session, nonce, controller_identity, host_identity } => {
                            let Some(pending) = asked.take().filter(|pending| pending.session == session) else { continue };
                            let binding = self.binding(session, nonce, controller_identity.as_ref(), host_identity.as_ref(), &ticket, pending.controller, enrollment);
                            match binding {
                                Ok(binding) => {
                                    running = Some(Running { session, controller: pending.controller, to_peer: None, buffered: Vec::new(), binding, stop: Arc::new(AtomicBool::new(false)), peer_expiry: Arc::new(AtomicU64::new(pending.expires_at)), task: None });
                                    self.status(Phase::Negotiating, "");
                                    say!(wire::Out::Ice { session });
                                }
                                Err(error) => {
                                    say!(wire::Out::Close { session });
                                    (self.events)(Event::SessionEnded(error));
                                    self.status(Phase::Ready, "");
                                }
                            }
                        }
                        wire::In::Ice { session, lease } => {
                            let Some(run) = running.as_mut().filter(|run| run.session == session && run.task.is_none()) else { continue };
                            let now = unix();
                            if lease.expires_at <= now || lease.expires_at > now + 3600 || lease.servers.len() > 8 || lease.servers.iter().any(|server| server.urls.len() > 8) {
                                say!(wire::Out::Close { session });
                                running = None;
                                (self.events)(Event::SessionEnded("The relay offer was not valid".into()));
                                self.status(Phase::Ready, "");
                                continue;
                            }
                            let (to_peer, signals) = mpsc::channel(64);
                            for signal in run.buffered.drain(..) {
                                let _ = to_peer.try_send(signal);
                            }
                            run.to_peer = Some(to_peer);
                            self.streaming.store(true, Ordering::Release);
                            let peer = session::Peer {
                                servers: lease.servers,
                                relay_only: self.config.relay_only,
                                binding: run.binding,
                                controller: run.controller,
                                key: self.key.clone(),
                                control,
                                size,
                                signals,
                                out: from_peer.clone(),
                                frames: self.frames_out.clone(),
                                events: self.events.clone(),
                                stop: run.stop.clone(),
                                peer_expiry: run.peer_expiry.clone(),
                            };
                            let inner = self.clone();
                            run.task = Some(tokio::spawn(async move {
                                let result = peer.run().await;
                                inner.streaming.store(false, Ordering::Release);
                                result
                            }));
                        }
                        wire::In::Signal { session, payload } => {
                            if let Some(run) = running.as_mut().filter(|run| run.session == session) {
                                match &run.to_peer {
                                    Some(to_peer) => { let _ = to_peer.try_send(payload); }
                                    None if run.buffered.len() < 32 => run.buffered.push(payload),
                                    None => {}
                                }
                            }
                        }
                        wire::In::PeerRenewed { session, identity } => {
                            if let Some(run) = running.as_ref().filter(|run| run.session == session)
                                && let Ok(key) = self.technician(&identity, identity.identity.connection_id, enrollment)
                                && key == run.controller
                                && identity.expires_at >= run.peer_expiry.load(Ordering::Acquire)
                            {
                                run.peer_expiry.store(identity.expires_at, Ordering::Release);
                            }
                        }
                        wire::In::Restarted { previous, session, nonce, controller_identity, host_identity } => {
                            // The viewer renews the session (at the latest every 15 minutes): same people, new link.
                            let Some(mut run) = running.take().filter(|run| run.session == previous) else { continue };
                            run.stop.store(true, Ordering::Release);
                            if let Some(task) = run.task.take() {
                                let ended = tokio::time::timeout(Duration::from_secs(8), task).await;
                                trace(&format!("old link closed for renewal: {ended:?}"));
                            }
                            match self.binding(session, nonce, Some(&controller_identity), Some(&host_identity), &ticket, run.controller, enrollment) {
                                Ok(binding) => {
                                    running = Some(Running { session, controller: run.controller, to_peer: None, buffered: Vec::new(), binding, stop: Arc::new(AtomicBool::new(false)), peer_expiry: Arc::new(AtomicU64::new(controller_identity.expires_at)), task: None });
                                    say!(wire::Out::Ice { session });
                                }
                                Err(error) => {
                                    say!(wire::Out::Close { session });
                                    (self.events)(Event::SessionEnded(error));
                                    self.status(Phase::Ready, "");
                                }
                            }
                        }
                        wire::In::Closed { session, .. } => {
                            if asked.as_ref().is_some_and(|pending| pending.session == session) {
                                asked = None;
                                self.status(Phase::Ready, "");
                            }
                            if let Some(mut run) = running.take_if(|run| run.session == session) {
                                run.stop.store(true, Ordering::Release);
                                if let Some(task) = run.task.take() {
                                    let _ = tokio::time::timeout(Duration::from_secs(8), task).await;
                                }
                                self.streaming.store(false, Ordering::Release);
                                (self.events)(Event::SessionEnded(String::new()));
                                self.status(Phase::Ready, "");
                            }
                        }
                        wire::In::Renewed { expires_at } => {
                            if expires_at != ticket.expires_at {
                                return Err("The connection service confirmed another identity".into());
                            }
                        }
                        wire::In::Error { .. } => {
                            if let Some(run) = running.as_ref().filter(|run| run.task.is_none()) {
                                say!(wire::Out::Close { session: run.session });
                                running = None;
                                self.status(Phase::Ready, "");
                            }
                        }
                        _ => {}
                    }
                }
                signal = peer_signals.recv() => {
                    if let (Some(signal), Some(run)) = (signal, running.as_ref()) {
                        say!(wire::Out::Signal { session: run.session, payload: signal });
                    }
                }
                command = commands.recv() => match command {
                    Some(Command::Decide(accept)) => {
                        if let Some(pending) = asked.as_ref() {
                            say!(wire::Out::Decide { session: pending.session, accept });
                            if !accept {
                                asked = None;
                                self.status(Phase::Ready, "");
                            }
                        }
                    }
                    Some(Command::End) => {
                        if let Some(mut run) = running.take() {
                            run.stop.store(true, Ordering::Release);
                            say!(wire::Out::Close { session: run.session });
                            if let Some(task) = run.task.take() {
                                let _ = tokio::time::timeout(Duration::from_secs(8), task).await;
                            }
                            self.streaming.store(false, Ordering::Release);
                            (self.events)(Event::SessionEnded(String::new()));
                            self.status(Phase::Ready, "");
                        }
                    }
                    None => {
                        // Stopped by the app: end what runs and leave.
                        if let Some(mut run) = running.take() {
                            run.stop.store(true, Ordering::Release);
                            let _ = write.send(Message::Text(serde_json::to_string(&wire::Out::Close { session: run.session }).unwrap_or_default().into())).await;
                            if let Some(task) = run.task.take() {
                                let _ = tokio::time::timeout(Duration::from_secs(8), task).await;
                            }
                        }
                        let _ = write.close().await;
                        return Ok(());
                    }
                },
                _ = renew.tick() => {
                    // The identity ticket lives two minutes; renew it while online.
                    match self.ticket(enrollment).await {
                        Ok(fresh) => {
                            say!(wire::Out::Renew { token: Self::encode(&fresh)? });
                            ticket = fresh;
                        }
                        Err(error) if unix() >= ticket.expires_at => return Err(error),
                        Err(_) => {}
                    }
                }
                _ = ping.tick() => {
                    write.send(Message::Ping(Vec::new().into())).await.map_err(|_| "The connection service link closed".to_owned())?;
                }
                // Wake up regularly so a finished session task is noticed without any message.
                () = tokio::time::sleep(Duration::from_millis(500)), if running.as_ref().is_some_and(|run| run.task.is_some()) => {}
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn binding(&self, session: Uuid, nonce: [u8; 32], controller: Option<&DeviceTicket>, host: Option<&DeviceTicket>, own: &DeviceTicket, expected: VerifyingKey, enrollment: &Enrollment) -> Result<[u8; 32], String> {
        let (controller, host) = controller.zip(host).ok_or("The session carries no confirmed identities")?;
        if nonce == [0; 32] {
            return Err("The session is not valid".into());
        }
        host.verify(&self.config.roots()?, unix()).map_err(|_| "This device's identity is not confirmed")?;
        let same = host.identity.device == own.identity.device
            && host.version == own.version
            && host.identity.profile == own.identity.profile
            && host.identity.tenant == own.identity.tenant
            && host.identity.connection_id == own.identity.connection_id
            && host.identity.generation == own.identity.generation
            && host.public_key == own.public_key
            && host.staff == own.staff;
        if !same {
            return Err("The session names another device".into());
        }
        if self.technician(controller, controller.identity.connection_id, enrollment)? != expected {
            return Err("The session names another technician".into());
        }
        Ok(session_binding(*session.as_bytes(), nonce, controller, host))
    }
}
