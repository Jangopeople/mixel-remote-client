//! Host permission grants and input acknowledgments share the authenticated,
//! directional encrypted channel. Signaling cannot upgrade a view-only session.
use crate::{remote_peer::Config, remote_session::InputCommand};
use core_protocol::{
    crypto::{Context, ControlChannel, Role},
    session::Session,
    transport::{NativeDataChannel, NativeDataChannelEvent},
};
use native_os_capture::{
    input::{InputRouter, InputToken},
};
use crate::session_platform::SessionInjector;
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::mpsc;
use zeroize::Zeroizing;

/// Host: requests for a complete picture closer together than this are one request.
const KEYFRAME_REQUEST_SPACING: Duration = Duration::from_millis(500);
const PERMISSIONS_V3: &[u8] = b"mixel-permissions-v3";
const PERMISSIONS_V4: &[u8] = b"mixel-permissions-v4";
/// Controller to host: show display N (one byte). Sent only to hosts that offered switching.
const DISPLAY_SELECT: &[u8] = b"mixel-display-select-v1";
/// Controller to host: run a one-click action the host offered (name as text).
const HOST_ACTION: &[u8] = b"mixel-action-v1:";
/// Host to controller: one input record was applied, or this host cannot apply that kind of input.
/// 0.1.22 and 2.0 hosts send exactly one of these per input record, in order, without a number.
const INPUT_POSTED: &[u8] = b"mixel-input-posted-v1";
const INPUT_UNSUPPORTED: &[u8] = b"mixel-input-unsupported-v1";
const VIDEO_IDLE: &[u8] = b"mixel-video-idle-v1";
/// Controller: how long a caller waits for the host to acknowledge an input. After that it hears
/// "not confirmed"; the record is still on its way (the channel is reliable and ordered) and its
/// late acknowledgment is matched to it and dropped. A late answer never ends the session.
pub const INPUT_ACK_WAIT: Duration = Duration::from_secs(2);
/// Controller: inputs sent and not yet acknowledged, at most. Further inputs wait in the queue.
const INPUTS_IN_FLIGHT: usize = 64;

type InputReply = tokio::sync::oneshot::Sender<Result<bool, String>>;

/// Controller: the acknowledgments the host still owes, oldest first. Because the host answers
/// every input record exactly once and in order, the next acknowledgment always belongs to the
/// oldest entry here, also when its caller has stopped waiting (the entry then has no reply).
#[derive(Default)]
struct PendingAcks(std::collections::VecDeque<(Instant, Option<InputReply>)>);
impl PendingAcks {
    fn sent(&mut self, deadline: Instant, reply: InputReply) {
        self.0.push_back((deadline, Some(reply)));
    }
    fn full(&self) -> bool {
        self.0.len() >= INPUTS_IN_FLIGHT
    }
    /// Callers whose input is overdue hear "not confirmed"; the input keeps its place.
    fn expire(&mut self, now: Instant) {
        for (deadline, reply) in &mut self.0 {
            if now >= *deadline
                && let Some(reply) = reply.take()
            {
                let _ = reply.send(Err(NOT_CONFIRMED.into()));
            }
        }
    }
    /// The host's next acknowledgment. One nobody is owed is a protocol violation.
    fn acknowledged(&mut self, applied: bool) -> Result<(), String> {
        let (_, reply) = self.0.pop_front().ok_or("Unexpected input acknowledgment")?;
        if let Some(reply) = reply {
            let _ = reply.send(Ok(applied));
        }
        Ok(())
    }
}
/// What a caller hears when the host has not acknowledged its input in time.
pub const NOT_CONFIRMED: &str = "The remote computer did not confirm this input in time";

fn read_permissions(record: &[u8]) -> Result<[bool; 4], String> {
    let (flags, count) = if let Some(flags) = record.strip_prefix(PERMISSIONS_V3) {
        (flags, 3)
    } else if let Some(flags) = record.strip_prefix(PERMISSIONS_V4) {
        (flags, 4)
    } else {
        return Err("Invalid host permission grant".into());
    };
    if flags.len() != count || flags.iter().any(|&flag| flag > 1) {
        return Err("Invalid host permission grant".into());
    }
    let mut permissions = [false; 4];
    for (permission, &flag) in permissions.iter_mut().zip(flags) {
        *permission = flag == 1;
    }
    Ok(permissions)
}

/// Display switching agreed during connection setup. Host: the capture sources a controlling
/// technician may switch between. Controller: how many the host offered and which is shown.
#[derive(Default)]
pub struct DisplayOffer {
    pub sources: Vec<String>,
    pub count: u16,
    pub current: u16,
    /// Host: actions offered to this controller. Controller: actions the host offered.
    pub actions: Vec<String>,
    /// Optional features the app on the other side announced (`a=x-mixel-features:`).
    pub features: Vec<String>,
}

/// Optional features this app announces to the other side of a session.
pub fn own_features() -> Vec<&'static str> {
    let mut features = vec![
        core_protocol::clipboard::FEATURE,
        ALL_MONITORS,
        crate::host_print::FEATURE,
        core_protocol::sound::FEATURE,
        FULL_HD,
        crate::remote_session::JOIN,
        crate::remote_session::SWITCH,
    ];
    // Hosts that show the technician's drawings (not Wayland or iOS).
    features.extend(crate::annotation::features());
    features
}
/// Feature: the controller can show "all monitors as one picture" as a display choice.
pub const ALL_MONITORS: &str = "allmon";
/// Feature: the app shows (and a host may send) pictures up to 1920x1080.
pub const FULL_HD: &str = "hd";

pub struct ControlPipe {
    crypto: ControlChannel,
    pub bulk: Option<ControlChannel>,
    pub chat: Option<ControlChannel>,
    router: Option<(InputRouter<SessionInjector>, InputToken)>,
    commands: mpsc::Receiver<InputCommand>,
    host: bool,
    pub allowed: bool,
    pub recording_allowed: bool,
    report_unsupported_input: bool,
    last_idle_sent: Instant,
    pub last_video_idle: Instant,
    last_keyframe_request: Option<Instant>,
    force_keyframe: bool,
    clipboard: crate::remote_clipboard::ClipboardPipe,
    /// Host: capture sources a controlling technician may switch between.
    displays: Vec<String>,
    pending_display: Option<usize>,
    /// Host: index of the display being shared (for the technician's pointer).
    shown_display: usize,
    /// The other app understands print jobs.
    print_peer: bool,
    /// The other app understands sound frames; host: the technician wants to hear them.
    sound_peer: bool,
    sound_on: bool,
    /// Host: privacy screen, input block and lock-at-end are undone when the session ends.
    _guard: Option<crate::session_guard::Guard>,
    /// Controller: received sound frames not yet handed to the player.
    sound_in: std::collections::VecDeque<Vec<u8>>,
    /// Host: a print job being sent (transfer id, payload, bytes sent, header sent).
    print_out: Option<(u64, Zeroizing<Vec<u8>>, usize, bool)>,
    print_sequence: u64,
    /// Controller: number of host displays offered, and the one shown.
    display_count: u16,
    current_display: u16,
    /// Host: actions it offered. Controller: actions the host offered.
    actions: Vec<String>,
    /// Host: this technician's drawings on the board (they go when the session ends).
    drawings_in: Option<crate::annotation::Owner>,
    /// Controller: drawing messages to send (only to hosts that announced drawing).
    drawings_out: Option<mpsc::Receiver<core_protocol::annotation::Message>>,
    /// Controller: inputs sent whose acknowledgment has not arrived yet.
    acks: PendingAcks,
}
impl ControlPipe {
    pub async fn create(
        config: &Config,
        mut crypto: ControlChannel,
        context: Context,
        channel: &Arc<dyn NativeDataChannel>,
        peer_supports_recording: bool,
        peer_supports_input_result: bool,
        displays: DisplayOffer,
    ) -> Result<Self, String> {
        let DisplayOffer { sources: displays, count, current, actions, features } = displays;
        let display_offer = (count, current);
        let (input_tx, input_rx) = mpsc::channel(32);
        let (clipboard_tx, clipboard_rx) = mpsc::channel(1);
        let (allowed, clipboard, files, recording, router) = if config.host {
            let router = if config.control {
                if !config.confirm {
                    return Err("Local keyboard-control consent is missing".into());
                }
                let injector = SessionInjector::open(
                    &config.source,
                    config.login_window,
                    !displays.is_empty(),
                    config.session_cancel.clone(),
                    config.input_authorization(),
                    #[cfg(target_os = "linux")] config.portal.clone(),
                )?;
                let mut router = InputRouter::new(injector);
                let mut session = Session::for_peer(Instant::now(), context, Role::Host, true);
                session
                    .decide(true, Instant::now())
                    .map_err(|error| error.to_string())?;
                session
                    .authenticated_transport_ready(&crypto.verified_session(), Instant::now())
                    .map_err(|error| error.to_string())?;
                let token = router
                    .register(session)
                    .map_err(|error| error.to_string())?;
                Some((router, token))
            } else {
                None
            };
            let mut permissions = if peer_supports_recording {
                PERMISSIONS_V4.to_vec()
            } else {
                PERMISSIONS_V3.to_vec()
            };
            permissions.push(u8::from(config.control));
            permissions.push(u8::from(config.clipboard));
            permissions.push(u8::from(config.files));
            let recording = peer_supports_recording && config.recording;
            if peer_supports_recording {
                permissions.push(u8::from(recording));
            }
            let record = crypto
                .seal(&permissions)
                .map_err(|error| error.to_string())?;
            tokio::time::timeout(
                Duration::from_secs(3),
                channel.send(record.as_slice().into()),
            )
            .await
            .map_err(|_| "Session permission transmission timed out")?
            .map_err(|_| "Cannot send authenticated session permissions")?;
            (
                config.control,
                config.clipboard,
                config.files,
                recording,
                router,
            )
        } else {
            let record = tokio::time::timeout(Duration::from_secs(5), received_record(channel))
                .await
                .map_err(|_| "The host did not provide session permissions")??;
            let plain = Zeroizing::new(
                crypto
                    .open(&record)
                    .map_err(|_| "Invalid authenticated session permissions")?,
            );
            let [control, clipboard, files, recording] = read_permissions(&plain)?;
            (control, clipboard, files, recording, None)
        };
        if config.stopped() {
            return Err("The session stopped before permissions were applied".into());
        }
        config
            .state
            .grant(
                config.generation,
                config.session,
                allowed,
                if config.host || !allowed {
                    None
                } else {
                    Some(input_tx)
                },
                clipboard,
                if config.host || !clipboard {
                    None
                } else {
                    Some(clipboard_tx)
                },
            )
            .await;
        config
            .state
            .record_grants(config.generation, config.session, config.mirror, files, recording)
            .await?;
        // Drawing needs only a view of the screen, not control.
        let drawings_out = if !config.host && features.iter().any(|name| name == core_protocol::annotation::FEATURE) {
            let (sender, receiver) = mpsc::channel(64);
            config.state.set_drawing(config.generation, config.session, sender).await;
            Some(receiver)
        } else {
            None
        };
        let drawings_in = (config.host && !crate::annotation::features().is_empty()).then(crate::annotation::Owner::new);
        let bulk = if files {
            Some(
                crypto
                    .take_bulk_channel()
                    .map_err(|error| error.to_string())?,
            )
        } else {
            None
        };
        let chat = if config.host && !config.chat {
            None
        } else {
            Some(
                crypto
                    .take_chat_channel()
                    .map_err(|error| error.to_string())?,
            )
        };
        Ok(Self {
            bulk,
            chat,
            crypto,
            router,
            commands: input_rx,
            host: config.host,
            allowed,
            recording_allowed: recording,
            report_unsupported_input: peer_supports_input_result,
            last_idle_sent: Instant::now(),
            last_video_idle: Instant::now(),
            last_keyframe_request: None,
            force_keyframe: false,
            clipboard: {
                let mut pipe = crate::remote_clipboard::ClipboardPipe::new(clipboard_rx, clipboard);
                pipe.rich = features.iter().any(|name| name == core_protocol::clipboard::FEATURE);
                pipe
            },
            pending_display: None,
            shown_display: 0,
            print_peer: features.iter().any(|name| name == crate::host_print::FEATURE),
            sound_peer: features.iter().any(|name| name == core_protocol::sound::FEATURE),
            sound_on: true,
            _guard: config.host.then(crate::session_guard::Guard::new),
            sound_in: std::collections::VecDeque::new(),
            print_out: None,
            print_sequence: 0,
            current_display: if config.host { 0 } else { display_offer.1 },
            display_count: if config.host { 0 } else { display_offer.0 },
            displays: if config.host && allowed { displays } else { Vec::new() },
            actions: if allowed { actions } else { Vec::new() },
            drawings_in,
            drawings_out,
            acks: PendingAcks::default(),
        })
    }
    /// Host: this session forwards print jobs (the technician's app understands them).
    pub fn forwards_print_jobs(&self) -> bool {
        self.host && self.print_peer
    }
    /// Tests: a controller's pipe with full control on `crypto`, inputs from `commands`.
    #[cfg(test)]
    fn test_controller(crypto: ControlChannel, commands: mpsc::Receiver<InputCommand>, clipboard: mpsc::Receiver<crate::remote_session::ClipboardCommand>) -> Self {
        Self {
            crypto,
            bulk: None,
            chat: None,
            router: None,
            commands,
            host: false,
            allowed: true,
            recording_allowed: false,
            report_unsupported_input: true,
            last_idle_sent: Instant::now(),
            last_video_idle: Instant::now(),
            last_keyframe_request: None,
            force_keyframe: false,
            clipboard: crate::remote_clipboard::ClipboardPipe::new(clipboard, true),
            displays: Vec::new(),
            pending_display: None,
            shown_display: 0,
            print_peer: false,
            sound_peer: true,
            sound_on: true,
            _guard: None,
            sound_in: std::collections::VecDeque::new(),
            print_out: None,
            print_sequence: 0,
            display_count: 1,
            current_display: 0,
            actions: Vec::new(),
            drawings_in: None,
            drawings_out: None,
            acks: PendingAcks::default(),
        }
    }
    /// Host: sends a finished print job to the technician, in small parts between frames.
    pub fn queue_print(&mut self, pdf: Vec<u8>) {
        if self.forwards_print_jobs()
            && self.print_out.is_none()
            && let Ok(payload) = crate::host_print::payload(pdf)
        {
            self.print_sequence += 1;
            self.print_out = Some((self.print_sequence, payload, 0, false));
        }
    }
    async fn pump_print(&mut self, channel: &Arc<dyn NativeDataChannel>) -> Result<(), String> {
        use core_protocol::clipboard::{CHUNK, Message};
        use sha2::Digest;
        let Some((id, payload, offset, begun)) = self.print_out.as_mut() else {
            return Ok(());
        };
        let mut messages: Vec<Zeroizing<Vec<u8>>> = Vec::new();
        if !*begun {
            *begun = true;
            messages.push(
                Message::Begin { id: *id, length: payload.len() as u32, digest: sha2::Sha256::digest(&payload[..]).into() }
                    .encode()
                    .map_err(|error| error.to_string())?,
            );
        }
        for _ in 0..24 {
            if *offset >= payload.len() {
                break;
            }
            let end = (*offset + CHUNK).min(payload.len());
            messages.push(
                Message::Chunk { id: *id, offset: *offset as u32, bytes: &payload[*offset..end] }
                    .encode()
                    .map_err(|error| error.to_string())?,
            );
            *offset = end;
        }
        let finished = *offset >= payload.len();
        if finished {
            messages.push(Message::Commit(*id).encode().map_err(|error| error.to_string())?);
        }
        for message in messages {
            let mut plain = Zeroizing::new(crate::host_print::RECORD.to_vec());
            plain.extend_from_slice(&message);
            let record = self.crypto.seal(&plain).map_err(|error| error.to_string())?;
            tokio::time::timeout(Duration::from_secs(2), channel.send(record.as_slice().into()))
                .await
                .map_err(|_| "Print job transmission timed out")?
                .map_err(|_| "Print job transport closed".to_owned())?;
        }
        if finished {
            self.print_out = None;
        }
        Ok(())
    }
    /// Host: record and send this computer's sound in this session.
    pub fn sound_wanted(&self) -> bool {
        self.host && self.allowed && self.sound_peer && self.sound_on
    }
    /// Host: sends one coded sound frame.
    pub async fn send_sound(&mut self, channel: &Arc<dyn NativeDataChannel>, frame: &[u8]) -> Result<(), String> {
        let mut plain = core_protocol::sound::RECORD.to_vec();
        plain.extend_from_slice(frame);
        let record = self.crypto.seal(&plain).map_err(|error| error.to_string())?;
        tokio::time::timeout(Duration::from_secs(2), channel.send(record.as_slice().into()))
            .await
            .map_err(|_| "Sound transmission timed out")?
            .map_err(|_| "Sound transport closed".to_owned())
    }
    /// Controller: the next received sound frame.
    pub fn take_sound(&mut self) -> Option<Vec<u8>> {
        self.sound_in.pop_front()
    }
    /// Controller: a complete print job received from the host.
    pub fn take_print_job(&mut self) -> Option<Vec<u8>> {
        if self.clipboard.printed.is_empty() { None } else { Some(self.clipboard.printed.remove(0)) }
    }
    /// Host: the next display to capture, after the controller selected one.
    pub fn take_display_switch(&mut self) -> Option<String> {
        let index = self.pending_display.take()?;
        self.displays.get(index).cloned()
    }

    pub fn proof(&self) -> core_protocol::crypto::VerifiedSession {
        self.crypto.verified_session()
    }
    pub fn receiving_clipboard(&self) -> bool {
        self.clipboard.receiving()
    }
    pub fn release(&mut self) -> Result<(), String> {
        if let Some((router, token)) = &mut self.router {
            router.close(token).map_err(|error| error.to_string())?;
        }
        Ok(())
    }
    pub async fn video_idle(&mut self, channel: &Arc<dyn NativeDataChannel>) -> Result<(), String> {
        if !self.host || self.last_idle_sent.elapsed() < Duration::from_secs(2) {
            return Ok(());
        }
        let record = self
            .crypto
            .seal(b"mixel-video-idle-v1")
            .map_err(|error| error.to_string())?;
        tokio::time::timeout(
            Duration::from_secs(2),
            channel.send(record.as_slice().into()),
        )
        .await
        .map_err(|_| "Video idle notification timed out")?
        .map_err(|_| "Video idle notification failed")?;
        self.last_idle_sent = Instant::now();
        Ok(())
    }
    /// Controller: asks the host to run an action it offered. The outer error is a broken
    /// transport (ends the session); the inner one only fails this request.
    async fn send_action(&mut self, name: &str, channel: &Arc<dyn NativeDataChannel>) -> Result<Result<(), String>, String> {
        let offered = name.split(':').next().unwrap_or(name);
        if self.host || !self.allowed || name.len() > 40 || !self.actions.iter().any(|action| action == offered) {
            return Ok(Err("This action is not available on the remote computer".into()));
        }
        let mut plain = HOST_ACTION.to_vec();
        plain.extend_from_slice(name.as_bytes());
        self.send_sealed(&plain, channel, "The action request").await.map(Ok)
    }
    /// Controller: shows another of the host's displays (errors as for `send_action`).
    async fn select_display(
        &mut self,
        index: u16,
        channel: &Arc<dyn NativeDataChannel>,
    ) -> Result<Result<(), String>, String> {
        if self.host || !self.allowed || index >= self.display_count || index > u16::from(u8::MAX) {
            return Ok(Err("This display is not available".into()));
        }
        let mut plain = DISPLAY_SELECT.to_vec();
        plain.push(index as u8);
        self.send_sealed(&plain, channel, "Display selection").await?;
        self.current_display = index;
        Ok(Ok(()))
    }
    /// Seals and sends one record. Sending never waits for the other side (the data channel has
    /// no send limit), so a failure or a stuck send means the transport is gone; and a record that
    /// was sealed but not sent cannot be skipped (records are numbered), so it ends the session.
    async fn send_sealed(&mut self, plain: &[u8], channel: &Arc<dyn NativeDataChannel>, what: &str) -> Result<(), String> {
        let record = self.crypto.seal(plain).map_err(|error| error.to_string())?;
        match tokio::time::timeout(Duration::from_secs(2), channel.send(record.as_slice().into())).await {
            Ok(Ok(_)) => Ok(()),
            Ok(Err(_)) => Err(format!("{what} failed: the connection is closed")),
            Err(_) => Err(format!("{what} timed out: the connection is stuck")),
        }
    }
    /// Controller: sends queued inputs without waiting for their acknowledgments (they are
    /// matched in order as they arrive). Only a broken transport is an error.
    async fn send_inputs(&mut self, channel: &Arc<dyn NativeDataChannel>) -> Result<(), String> {
        for _ in 0..16 {
            if self.acks.full() {
                return Ok(());
            }
            let Ok(command) = self.commands.try_recv() else {
                return Ok(());
            };
            let now = Instant::now();
            if now >= command.deadline {
                let _ = command.reply.send(Err("The input command expired".into()));
                continue;
            }
            match &command.event {
                crate::remote_session::InputEvent::Action { name } => {
                    let name = name.clone();
                    let result = self.send_action(&name, channel).await?;
                    let _ = command.reply.send(result.map(|()| true));
                }
                crate::remote_session::InputEvent::SelectDisplay { index } => {
                    let result = self.select_display(*index, channel).await?;
                    let _ = command.reply.send(result.map(|()| true));
                }
                _ if !self.allowed => {
                    let _ = command.reply.send(Err("Keyboard control is not permitted".into()));
                }
                event => {
                    let mut input = event.borrowed();
                    if let core_protocol::input::Input::Pointer { display, .. } = &mut input {
                        *display = self.current_display;
                    }
                    let plain = match core_protocol::input::encode(&input) {
                        Ok(plain) => Zeroizing::new(plain),
                        Err(error) => {
                            let _ = command.reply.send(Err(error.to_string()));
                            continue;
                        }
                    };
                    self.send_sealed(&plain, channel, "Input transmission").await?;
                    // The caller gives up 3 s after queueing; it hears the outcome before that.
                    let deadline = (now + INPUT_ACK_WAIT).min(command.deadline + Duration::from_millis(500));
                    self.acks.sent(deadline, command.reply);
                }
            }
        }
        Ok(())
    }
    /// Controller: one authenticated record from the host. Returns `true` for a clipboard answer
    /// the clipboard request in progress must handle; everything else is dealt with here: video
    /// idle, sound, print jobs, input acknowledgments (early or late) and late clipboard answers.
    fn take_host_record(&mut self, plain: &[u8]) -> Result<bool, String> {
        if let Some(rest) = plain.strip_prefix(crate::host_print::RECORD) {
            self.clipboard.accept_print(rest);
        } else if let Some(frame) = plain.strip_prefix(core_protocol::sound::RECORD) {
            if self.sound_in.len() < 100 {
                self.sound_in.push_back(frame.to_vec());
            }
        } else if plain == VIDEO_IDLE {
            self.last_video_idle = Instant::now();
        } else if plain == INPUT_POSTED {
            self.acks.acknowledged(true)?;
        } else if plain == INPUT_UNSUPPORTED {
            self.acks.acknowledged(false)?;
        } else if plain.starts_with(core_protocol::clipboard::PREFIX) {
            return Ok(!self.clipboard.late_answer(plain));
        } else {
            return Err("Unexpected authenticated host control message".into());
        }
        Ok(false)
    }
    /// Controller: takes the records the host sent (print jobs and sound arrive as many small
    /// records: up to 64 at a time). Waits at most `wait` for the first one.
    async fn receive_host_records(&mut self, channel: &Arc<dyn NativeDataChannel>, wait: Duration) -> Result<(), String> {
        let mut record = match tokio::time::timeout(wait, received_record(channel)).await {
            Ok(value) => value?,
            Err(_) => return Ok(()),
        };
        for _ in 0..64 {
            let plain = Zeroizing::new(self.crypto.open(&record).map_err(|error| error.to_string())?);
            if self.take_host_record(&plain)? {
                return Err("Unexpected clipboard answer from the host".into());
            }
            record = match tokio::time::timeout(Duration::from_micros(200), received_record(channel)).await {
                Ok(value) => value?,
                Err(_) => break,
            };
        }
        self.acks.expire(Instant::now());
        Ok(())
    }
    /// Controller: one clipboard request. The outer error is a broken transport or a host that
    /// breaks the protocol; a host that answers too late fails only this request (its late
    /// answer is recognised by number and dropped).
    async fn clipboard_request(
        &mut self,
        config: &Config,
        channel: &Arc<dyn NativeDataChannel>,
        push: bool,
        wait: Duration,
    ) -> Result<Result<(), String>, String> {
        let mut request = match self.clipboard.begin(config, &mut self.crypto, channel, push).await? {
            Ok(request) => request,
            Err(error) => return Ok(Err(error)),
        };
        let deadline = Instant::now() + wait;
        loop {
            if config.stopped() {
                return Err("The clipboard transfer stopped".into());
            }
            self.acks.expire(Instant::now());
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                self.clipboard.give_up(&request);
                return Ok(Err("The remote computer did not answer the clipboard request in time".into()));
            }
            let record = match tokio::time::timeout(left.min(Duration::from_millis(250)), received_record(channel)).await {
                Ok(value) => value?,
                Err(_) => continue,
            };
            let plain = Zeroizing::new(self.crypto.open(&record).map_err(|error| error.to_string())?);
            if self.take_host_record(&plain)?
                && let Some(result) = request.accept(&plain, config.stopped())?
            {
                return Ok(result);
            }
        }
    }
    /// Controller: sends the queued drawing messages (no acknowledgment).
    async fn send_drawings(&mut self, channel: &Arc<dyn NativeDataChannel>) -> Result<(), String> {
        for _ in 0..16 {
            let Some(message) = self.drawings_out.as_mut().and_then(|queue| queue.try_recv().ok()) else {
                return Ok(());
            };
            let Ok(plain) = message.record() else { continue };
            let record = self.crypto.seal(&plain).map_err(|error| error.to_string())?;
            tokio::time::timeout(Duration::from_secs(2), channel.send(record.as_slice().into()))
                .await
                .map_err(|_| "Drawing transmission timed out")?
                .map_err(|_| "Drawing transport closed".to_owned())?;
        }
        Ok(())
    }
    /// Host: the next request for a complete picture is taken this long after the last one.
    #[cfg(test)]
    pub(crate) const fn keyframe_request_spacing() -> Duration {
        KEYFRAME_REQUEST_SPACING
    }
    pub fn take_keyframe_request(&mut self) -> bool {
        std::mem::take(&mut self.force_keyframe)
    }
    /// Host: a complete picture has just left. Requests for one that arrived before this moment
    /// are answered by it. (A display switch asked for meanwhile restarts the stream by itself.)
    pub fn complete_picture_sent(&mut self) {
        self.force_keyframe = false;
    }
    pub async fn request_keyframe(
        &mut self,
        channel: &Arc<dyn NativeDataChannel>,
    ) -> Result<(), String> {
        if self.host
            || self
                .last_keyframe_request
                .is_some_and(|last| last.elapsed() < Duration::from_secs(1))
        {
            return Ok(());
        }
        let record = self
            .crypto
            .seal(b"mixel-video-keyframe-v1")
            .map_err(|error| error.to_string())?;
        tokio::time::timeout(
            Duration::from_secs(2),
            channel.send(record.as_slice().into()),
        )
        .await
        .map_err(|_| "Keyframe request timed out")?
        .map_err(|_| "Keyframe request failed")?;
        self.last_keyframe_request = Some(Instant::now());
        Ok(())
    }
    pub async fn poll(
        &mut self,
        config: &Config,
        channel: &Arc<dyn NativeDataChannel>,
    ) -> Result<(), String> {
        if config.stopped() {
            return Ok(());
        }
        self.clipboard.check_deadline()?;
        if self.host {
            self.pump_print(channel).await?;
        } else {
            self.send_drawings(channel).await?;
        }
        if !self.host {
            self.acks.expire(Instant::now());
            self.send_inputs(channel).await?;
            if let Ok(command) = self.clipboard.commands.try_recv() {
                if Instant::now() >= command.deadline {
                    let _ = command.reply.send(Err("The clipboard request expired".into()));
                    return Ok(());
                }
                let wait = command
                    .deadline
                    .saturating_duration_since(Instant::now())
                    .min(if self.clipboard.rich {
                        core_protocol::clipboard::RICH_LIFETIME
                    } else {
                        core_protocol::clipboard::LIFETIME
                    });
                let result = self.clipboard_request(config, channel, command.push, wait).await;
                let _ = command.reply.send(match &result {
                    Ok(result) => result.clone(),
                    Err(error) => Err(error.clone()),
                });
                return result.map(|_| ());
            }
            return self.receive_host_records(channel, Duration::from_millis(1)).await;
        }
        let mut record =
            match tokio::time::timeout(Duration::from_millis(1), received_record(channel)).await {
                Ok(value) => value?,
                Err(_) => return Ok(()),
            };
        if config.stopped() {
            return Err("The session stopped before control processing".into());
        }
        let mut plain = Zeroizing::new(
            self.crypto
                .open(&record)
                .map_err(|error| error.to_string())?,
        );
        // Drawings: in view-only sessions too, and many at a time (they arrive while the
        // technician draws, and a view-only host polls only between pictures).
        let mut drawn = 0;
        while let Some(rest) = plain.strip_prefix(core_protocol::annotation::RECORD) {
            if let Some(owner) = &self.drawings_in {
                owner.receive(rest);
            }
            drawn += 1;
            if drawn >= 64 {
                return Ok(());
            }
            record = match tokio::time::timeout(Duration::from_micros(200), received_record(channel)).await {
                Ok(value) => value?,
                Err(_) => return Ok(()),
            };
            plain = Zeroizing::new(self.crypto.open(&record).map_err(|error| error.to_string())?);
        }
        if plain.starts_with(core_protocol::clipboard::PREFIX) {
            return self
                .clipboard
                .host(config, &mut self.crypto, channel, &plain)
                .await;
        }
        if let Some(rest) = plain.strip_prefix(HOST_ACTION) {
            let name = std::str::from_utf8(rest).map_err(|_| "Invalid host action")?.to_owned();
            let offered = name.split(':').next().unwrap_or(&name).to_owned();
            let switch = [crate::host_actions::WOL, crate::host_actions::SOUND, crate::session_guard::MEET, crate::session_guard::POINTER, crate::session_guard::PRIVACY, crate::session_guard::BLOCK, crate::session_guard::LOCK_AT_END];
            if !self.allowed || name.len() > 40 || !self.actions.contains(&offered) || (name.contains(':') && !switch.contains(&offered.as_str())) {
                return Err("Invalid host action".into());
            }
            if offered == crate::session_guard::MEET {
                let room = name.strip_prefix("meet:").unwrap_or_default();
                if !crate::session_guard::meeting_room(room) {
                    return Err("Invalid host action".into());
                }
                config.state.set_meeting_offer(config.generation, config.session, room.to_owned()).await;
                return Ok(());
            }
            if offered == crate::session_guard::POINTER {
                // `pointer:x,y` (0..65535 on the shared picture) or `pointer:off`.
                let point = name.strip_prefix("pointer:").and_then(|rest| rest.split_once(',')).and_then(|(x, y)| Some((x.parse::<u16>().ok()?, y.parse::<u16>().ok()?)));
                crate::session_guard::pointer(point, self.shown_display, self.displays.len());
                return Ok(());
            }
            if offered == crate::host_actions::SOUND {
                self.sound_on = name != "sound:off";
                return Ok(());
            }
            // Run off the media loop; a restart takes the session down on its own.
            tokio::task::spawn_blocking(move || {
                if let Err(error) = crate::host_actions::run(&name) {
                    eprintln!("host action {name}: {error}");
                }
            });
            return Ok(());
        }
        if let Some(rest) = plain.strip_prefix(DISPLAY_SELECT) {
            return match rest {
                [index] if self.allowed && usize::from(*index) < self.displays.len() => {
                    self.pending_display = Some(usize::from(*index));
                    self.shown_display = usize::from(*index);
                    self.force_keyframe = true;
                    Ok(())
                }
                _ => Err("Invalid display selection".into()),
            };
        }
        if plain.as_slice() == b"mixel-video-keyframe-v1" {
            // The technician's side asks at most once a second. Waiting a full second here as
            // well dropped every request that came a few milliseconds early, so a technician
            // without a picture got one only every other second; half a second still bounds a
            // flood of requests.
            if self
                .last_keyframe_request
                .is_none_or(|last| last.elapsed() >= KEYFRAME_REQUEST_SPACING)
            {
                self.force_keyframe = true;
                self.last_keyframe_request = Some(Instant::now());
            }
            return Ok(());
        }
        if !self.allowed {
            return Err("Remote input is not authorized".into());
        }
        let (router, token) = self
            .router
            .as_mut()
            .ok_or("The input grant is unavailable")?;
        use native_os_capture::input::{InjectionError, InputError};
        let result = router.dispatch_verified(
            token,
            &self.crypto.verified_session(),
            &core_protocol::input::decode(&plain).map_err(|error| error.to_string())?,
        );
        let response = match result {
            Ok(()) => b"mixel-input-posted-v1".as_slice(),
            Err(InputError::Injection(InjectionError::Unsupported)) if self.report_unsupported_input => {
                b"mixel-input-unsupported-v1".as_slice()
            }
            // A screen this host cannot drive (for example a Windows security prompt for a
            // normal app) drops the event but keeps the session.
            Err(InputError::Injection(InjectionError::Native(_))) => {
                if self.report_unsupported_input {
                    b"mixel-input-unsupported-v1".as_slice()
                } else {
                    b"mixel-input-posted-v1".as_slice()
                }
            }
            Err(error) => return Err(error.to_string()),
        };
        let ack = self.crypto.seal(response).map_err(|error| error.to_string())?;
        tokio::time::timeout(Duration::from_secs(2), channel.send(ack.as_slice().into()))
            .await
            .map_err(|_| "Input acknowledgment timed out")?
            .map_err(|_| "Input acknowledgment failed".to_owned())
    }
}
pub async fn received_record(
    channel: &Arc<dyn NativeDataChannel>,
) -> Result<Vec<u8>, String> {
    loop {
        match channel.poll().await {
            Some(NativeDataChannelEvent::OnMessage(message))
                if !message.is_string
                    && message.data.len() <= core_protocol::input::MAX_INPUT_BYTES + 24 =>
            {
                return Ok(message.data.to_vec());
            }
            Some(NativeDataChannelEvent::OnMessage(_)) => {
                return Err("Invalid remote control record".into());
            }
            None | Some(NativeDataChannelEvent::OnClose | NativeDataChannelEvent::OnError) => {
                return Err("Remote control transport closed".into());
            }
            _ => {}
        }
    }
}

#[cfg(test)]
mod permission_tests {
    use super::*;

    #[test]
    fn recording_requires_an_explicit_authenticated_flag_and_legacy_grants_stay_compatible() {
        assert_eq!(
            read_permissions(b"mixel-permissions-v3\x01\x00\x01").unwrap(),
            [true, false, true, false]
        );
        assert_eq!(
            read_permissions(b"mixel-permissions-v4\x01\x00\x01\x01").unwrap(),
            [true, false, true, true]
        );
        for bad in [
            b"mixel-permissions-v3\x01\x00\x01\x01".as_slice(),
            b"mixel-permissions-v4\x01\x00\x01".as_slice(),
            b"mixel-permissions-v4\x01\x00\x01\x02".as_slice(),
        ] {
            assert!(read_permissions(bad).is_err());
        }
    }
}

#[cfg(test)]
mod input_ack_tests {
    //! A real WebRTC control channel on this computer: the controller's pipe on one end, a
    //! scripted host (its own half of the encrypted channel) on the other.
    use super::*;
    use crate::remote_session::{ClipboardCommand, InputEvent};
    use core_protocol::{
        crypto::{Handshake, Role},
        transport::{NativeTransport, local_fingerprint},
    };
    use ed25519_dalek::SigningKey;
    use tokio::sync::oneshot;

    struct Lab {
        pipe: ControlPipe,
        inputs: mpsc::Sender<InputCommand>,
        _clipboard: mpsc::Sender<ClipboardCommand>,
        controller: Arc<dyn NativeDataChannel>,
        host: Arc<dyn NativeDataChannel>,
        host_crypto: ControlChannel,
        _transports: (NativeTransport, NativeTransport),
    }

    async fn lab() -> Lab {
        let mut controller = NativeTransport::new("127.0.0.1:0".into(), vec![]).await.unwrap();
        let mut host = NativeTransport::new("127.0.0.1:0".into(), vec![]).await.unwrap();
        let controller_channel = controller.peer.create_data_channel("mixel-control-v1", None).await.unwrap();
        let offer = controller.local_sdp(controller.peer.create_offer(None).await.unwrap()).await.unwrap();
        let controller_fp = local_fingerprint(&offer.sdp).unwrap();
        host.peer.set_remote_description(offer).await.unwrap();
        let answer = host.local_sdp(host.peer.create_answer(None).await.unwrap()).await.unwrap();
        let host_fp = local_fingerprint(&answer.sdp).unwrap();
        controller.peer.set_remote_description(answer).await.unwrap();
        let host_channel = tokio::time::timeout(Duration::from_secs(8), host.incoming_channels.recv()).await.unwrap().unwrap();
        for channel in [&host_channel, &controller_channel] {
            assert!(matches!(
                tokio::time::timeout(Duration::from_secs(8), channel.poll()).await,
                Ok(Some(NativeDataChannelEvent::OnOpen))
            ));
        }
        let controller_key = SigningKey::from_bytes(&[11; 32]);
        let host_key = SigningKey::from_bytes(&[12; 32]);
        let context = Context { session_id: [13; 32], controller: controller_key.verifying_key(), host: host_key.verifying_key() };
        let a = Handshake::new(context.clone(), Role::Controller, &controller_key, controller_fp).unwrap();
        let b = Handshake::new(context, Role::Host, &host_key, host_fp).unwrap();
        let (ah, bh) = (a.hello(), b.hello());
        let controller_crypto = a.finish(&bh, controller.observed_fingerprint().await.unwrap()).unwrap();
        let host_crypto = b.finish(&ah, host.observed_fingerprint().await.unwrap()).unwrap();
        let (inputs, input_rx) = mpsc::channel(32);
        let (clipboard, clipboard_rx) = mpsc::channel(1);
        Lab {
            pipe: ControlPipe::test_controller(controller_crypto, input_rx, clipboard_rx),
            inputs,
            _clipboard: clipboard,
            controller: controller_channel,
            host: host_channel,
            host_crypto,
            _transports: (controller, host),
        }
    }

    impl Lab {
        /// Queues an input as the app does (the caller gives up after 2 s in the queue).
        fn input(&self, usage: u16) -> oneshot::Receiver<Result<bool, String>> {
            let (reply, wait) = oneshot::channel();
            self.inputs
                .try_send(InputCommand { event: InputEvent::Key { usage, down: false }, deadline: Instant::now() + Duration::from_secs(2), reply })
                .unwrap();
            wait
        }
        /// What the controller's media loop does between pictures (no clipboard request).
        async fn step(&mut self) -> Result<(), String> {
            self.pipe.acks.expire(Instant::now());
            self.pipe.send_inputs(&self.controller).await?;
            self.pipe.receive_host_records(&self.controller, Duration::from_millis(5)).await
        }
        /// Steps until `reply` is answered; every step must keep the session.
        async fn until(&mut self, reply: &mut oneshot::Receiver<Result<bool, String>>, limit: Duration) -> Result<bool, String> {
            let end = Instant::now() + limit;
            loop {
                self.step().await.expect("a slow or late acknowledgment must not end the session");
                if let Ok(result) = reply.try_recv() {
                    return result;
                }
                assert!(Instant::now() < end, "no answer for the input");
            }
        }
        /// The scripted host: the next input record (decrypted and decoded).
        async fn host_reads_input(&mut self) -> u16 {
            let record = tokio::time::timeout(Duration::from_secs(5), received_record(&self.host)).await.unwrap().unwrap();
            let plain = self.host_crypto.open(&record).unwrap();
            match core_protocol::input::decode(&plain).unwrap() {
                core_protocol::input::Input::Key { usage, .. } => usage,
                other => panic!("unexpected input {other:?}"),
            }
        }
        async fn host_sends(&mut self, plain: &[u8]) {
            let record = self.host_crypto.seal(plain).unwrap();
            self.host.send(record.as_slice().into()).await.unwrap();
        }
    }

    #[tokio::test]
    async fn a_slow_acknowledgment_fails_only_that_input_and_the_session_goes_on() {
        let mut lab = lab().await;
        // The host is still starting (as right after connecting) and answers after 2.6 s.
        let mut first = lab.input(4);
        lab.step().await.unwrap();
        assert_eq!(lab.host_reads_input().await, 4);
        let started = Instant::now();
        assert_eq!(lab.until(&mut first, Duration::from_secs(4)).await, Err(NOT_CONFIRMED.into()));
        assert!(started.elapsed() >= Duration::from_millis(1900));
        tokio::time::sleep(Duration::from_millis(600)).await;
        lab.host_sends(INPUT_POSTED).await;
        // The late answer is taken quietly; the next input is answered normally.
        for _ in 0..20 {
            lab.step().await.unwrap();
        }
        assert!(lab.pipe.acks.0.is_empty());
        let mut second = lab.input(5);
        lab.step().await.unwrap();
        assert_eq!(lab.host_reads_input().await, 5);
        lab.host_sends(VIDEO_IDLE).await;
        lab.host_sends(INPUT_POSTED).await;
        assert_eq!(lab.until(&mut second, Duration::from_secs(2)).await, Ok(true));
    }

    #[tokio::test]
    async fn a_late_acknowledgment_is_never_taken_for_the_next_input() {
        let mut lab = lab().await;
        let mut first = lab.input(4);
        lab.step().await.unwrap();
        assert_eq!(lab.host_reads_input().await, 4);
        assert_eq!(lab.until(&mut first, Duration::from_secs(4)).await, Err(NOT_CONFIRMED.into()));
        // The second input is sent while the first one's answer is still owed.
        let mut second = lab.input(5);
        lab.step().await.unwrap();
        assert_eq!(lab.host_reads_input().await, 5);
        assert!(second.try_recv().is_err(), "nothing answered yet");
        // Host: the first was applied (late), the second is a kind it cannot apply. Sound in between.
        lab.host_sends(INPUT_POSTED).await;
        lab.host_sends(b"mixel-sound-v1:frame").await;
        lab.host_sends(INPUT_UNSUPPORTED).await;
        assert_eq!(lab.until(&mut second, Duration::from_secs(2)).await, Ok(false));
        assert_eq!(lab.pipe.take_sound().as_deref(), Some(b"frame".as_slice()));
        assert!(lab.pipe.acks.0.is_empty());
    }

    #[tokio::test]
    async fn inputs_are_sent_without_waiting_and_answered_in_order() {
        let mut lab = lab().await;
        let mut replies: Vec<_> = (0..5).map(|usage| lab.input(10 + usage)).collect();
        lab.step().await.unwrap();
        for usage in 0..5 {
            assert_eq!(lab.host_reads_input().await, 10 + usage);
        }
        for applied in [true, false, true, true, false] {
            lab.host_sends(if applied { INPUT_POSTED } else { INPUT_UNSUPPORTED }).await;
        }
        let mut answers = Vec::new();
        for reply in &mut replies {
            answers.push(lab.until(reply, Duration::from_secs(2)).await);
        }
        assert_eq!(answers, [Ok(true), Ok(false), Ok(true), Ok(true), Ok(false)]);
    }

    #[tokio::test]
    async fn a_dead_transport_still_ends_the_session() {
        let mut lab = lab().await;
        let _waiting = lab.input(4);
        lab.step().await.unwrap();
        assert_eq!(lab.host_reads_input().await, 4);
        lab.host.close().await.unwrap();
        let end = Instant::now() + Duration::from_secs(10);
        let error = loop {
            match lab.step().await {
                Err(error) => break error,
                Ok(()) => assert!(Instant::now() < end, "a closed transport must end the session"),
            }
        };
        assert_eq!(error, "Remote control transport closed");
    }

    #[tokio::test]
    async fn an_acknowledgment_nobody_is_owed_and_unknown_records_are_still_refused() {
        let mut lab = lab().await;
        lab.host_sends(INPUT_POSTED).await;
        let end = Instant::now() + Duration::from_secs(5);
        let error = loop {
            match lab.step().await {
                Err(error) => break error,
                Ok(()) => assert!(Instant::now() < end),
            }
        };
        assert_eq!(error, "Unexpected input acknowledgment");
    }

    #[test]
    fn late_clipboard_answers_are_dropped_by_number_and_current_ones_handed_on() {
        use core_protocol::clipboard::Message;
        let (_inputs, input_rx) = mpsc::channel(1);
        let (_clipboard, clipboard_rx) = mpsc::channel(1);
        let key = SigningKey::from_bytes(&[21; 32]);
        let peer = SigningKey::from_bytes(&[22; 32]);
        let context = Context { session_id: [23; 32], controller: key.verifying_key(), host: peer.verifying_key() };
        let a = Handshake::new(context.clone(), Role::Controller, &key, [1; 32]).unwrap();
        let b = Handshake::new(context, Role::Host, &peer, [2; 32]).unwrap();
        let crypto = a.finish(&b.hello(), [2; 32]).unwrap();
        let mut pipe = ControlPipe::test_controller(crypto, input_rx, clipboard_rx);
        let mut request = pipe.clipboard.test_request(3, true);
        pipe.clipboard.give_up(&request);
        for (name, late) in [("ack 2", Message::Ack(2)), ("ack 3", Message::Ack(3)), ("unavailable 3", Message::Unavailable(3)), ("commit 1", Message::Commit(1))] {
            assert_eq!(pipe.take_host_record(&late.encode().unwrap()), Ok(false), "{name} is late");
        }
        let current = Message::Ack(4).encode().unwrap();
        assert_eq!(pipe.take_host_record(&current), Ok(true));
        request = pipe.clipboard.test_request(4, true);
        assert_eq!(request.accept(&current, false), Ok(Some(Ok(()))));
        assert!(pipe.take_host_record(b"something else").is_err());
        // A damaged print job is dropped, with its remaining parts, and the session goes on.
        let mut print = crate::host_print::RECORD.to_vec();
        print.extend_from_slice(&Message::Chunk { id: 9, offset: 0, bytes: b"x" }.encode().unwrap());
        assert_eq!(pipe.take_host_record(&print), Ok(false));
        assert!(pipe.take_print_job().is_none());
    }
}
