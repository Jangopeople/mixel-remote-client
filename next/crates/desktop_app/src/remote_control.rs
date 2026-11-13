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

const PERMISSIONS_V3: &[u8] = b"mixel-permissions-v3";
const PERMISSIONS_V4: &[u8] = b"mixel-permissions-v4";
/// Controller to host: show display N (one byte). Sent only to hosts that offered switching.
const DISPLAY_SELECT: &[u8] = b"mixel-display-select-v1";
/// Controller to host: run a one-click action the host offered (name as text).
const HOST_ACTION: &[u8] = b"mixel-action-v1:";

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
    vec![
        core_protocol::clipboard::FEATURE,
        ALL_MONITORS,
        crate::host_print::FEATURE,
        core_protocol::sound::FEATURE,
        FULL_HD,
        crate::remote_session::JOIN,
        crate::remote_session::SWITCH,
    ]
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
        })
    }
    /// Host: this session forwards print jobs (the technician's app understands them).
    pub fn forwards_print_jobs(&self) -> bool {
        self.host && self.print_peer
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
    async fn send_action(&mut self, name: &str, channel: &Arc<dyn NativeDataChannel>) -> Result<(), String> {
        let offered = name.split(':').next().unwrap_or(name);
        if self.host || !self.allowed || name.len() > 40 || !self.actions.iter().any(|action| action == offered) {
            return Err("This action is not available on the remote computer".into());
        }
        let mut plain = HOST_ACTION.to_vec();
        plain.extend_from_slice(name.as_bytes());
        let record = self.crypto.seal(&plain).map_err(|error| error.to_string())?;
        tokio::time::timeout(Duration::from_secs(2), channel.send(record.as_slice().into()))
            .await
            .map_err(|_| "The action request timed out")?
            .map_err(|_| "The action request failed".into())
    }
    async fn select_display(
        &mut self,
        index: u16,
        channel: &Arc<dyn NativeDataChannel>,
    ) -> Result<(), String> {
        if self.host || !self.allowed || index >= self.display_count || index > u16::from(u8::MAX) {
            return Err("This display is not available".into());
        }
        let mut plain = DISPLAY_SELECT.to_vec();
        plain.push(index as u8);
        let record = self.crypto.seal(&plain).map_err(|error| error.to_string())?;
        tokio::time::timeout(Duration::from_secs(2), channel.send(record.as_slice().into()))
            .await
            .map_err(|_| "Display selection timed out")?
            .map_err(|_| "Display selection failed")?;
        self.current_display = index;
        Ok(())
    }
    pub fn take_keyframe_request(&mut self) -> bool {
        std::mem::take(&mut self.force_keyframe)
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
        }
        if !self.host
            && let Ok(command) = self.commands.try_recv()
        {
            if Instant::now() >= command.deadline {
                let _ = command.reply.send(Err("The input command expired".into()));
                return Ok(());
            }
            if let crate::remote_session::InputEvent::Action { name } = &command.event {
                let result = self.send_action(name, channel).await;
                let _ = command.reply.send(result.clone().map(|()| true));
                return result;
            }
            if let crate::remote_session::InputEvent::SelectDisplay { index } = command.event {
                let result = self.select_display(index, channel).await;
                let _ = command.reply.send(result.clone().map(|()| true));
                return result;
            }
            let result = tokio::time::timeout(Duration::from_secs(2), async {
                if !self.allowed {
                    return Err("Keyboard control is not permitted".to_owned());
                }
                let mut input = command.event.borrowed();
                if let core_protocol::input::Input::Pointer { display, .. } = &mut input {
                    *display = self.current_display;
                }
                let plain = Zeroizing::new(
                    core_protocol::input::encode(&input).map_err(|error| error.to_string())?,
                );
                let record = self
                    .crypto
                    .seal(&plain)
                    .map_err(|error| error.to_string())?;
                channel
                    .send(record.as_slice().into())
                    .await
                    .map_err(|_| "Input transmission failed")?;
                loop {
                    let record = received_record(channel).await?;
                    let reply = Zeroizing::new(
                        self.crypto
                            .open(&record)
                            .map_err(|error| error.to_string())?,
                    );
                    if reply.as_slice() == b"mixel-video-idle-v1" {
                        self.last_video_idle = Instant::now();
                        continue;
                    }
                    return match reply.as_slice() {
                        b"mixel-input-posted-v1" => Ok(true),
                        b"mixel-input-unsupported-v1" => Ok(false),
                        _ => Err("Invalid input acknowledgment".into()),
                    };
                }
            })
            .await
            .map_err(|_| "Input transmission timed out".to_owned())
            .and_then(|value| value);
            let _ = command.reply.send(result.clone());
            return result.map(|_| ());
        }
        if !self.host
            && let Ok(command) = self.clipboard.commands.try_recv()
        {
            if Instant::now() >= command.deadline {
                let _ = command
                    .reply
                    .send(Err("The clipboard request expired".into()));
                return Ok(());
            }
            let remaining = command
                .deadline
                .saturating_duration_since(Instant::now())
                .min(if self.clipboard.rich {
                    core_protocol::clipboard::RICH_LIFETIME
                } else {
                    core_protocol::clipboard::LIFETIME
                });
            let result = tokio::time::timeout(
                remaining,
                self.clipboard.request(
                    config,
                    &mut self.crypto,
                    channel,
                    command.push,
                    &mut self.last_video_idle,
                ),
            )
            .await
            .map_err(|_| "Clipboard transfer timed out".to_owned())
            .and_then(|value| value);
            match result {
                Ok(result) => {
                    let _ = command.reply.send(result);
                    return Ok(());
                }
                Err(error) => {
                    let _ = command.reply.send(Err(error.clone()));
                    return Err(error);
                }
            }
        }
        let mut record =
            match tokio::time::timeout(Duration::from_millis(1), received_record(channel)).await {
                Ok(value) => value?,
                Err(_) => return Ok(()),
            };
        if !self.host {
            // Print jobs and sound arrive as many small records: take what is there.
            for _ in 0..64 {
                let plain = Zeroizing::new(
                    self.crypto
                        .open(&record)
                        .map_err(|error| error.to_string())?,
                );
                if let Some(rest) = plain.strip_prefix(crate::host_print::RECORD) {
                    self.clipboard.accept_print(rest)?;
                } else if let Some(frame) = plain.strip_prefix(core_protocol::sound::RECORD) {
                    if self.sound_in.len() < 100 {
                        self.sound_in.push_back(frame.to_vec());
                    }
                } else if plain.as_slice() == b"mixel-video-idle-v1" {
                    self.last_video_idle = Instant::now();
                } else {
                    return Err("Unexpected authenticated host control message".into());
                }
                record = match tokio::time::timeout(Duration::from_micros(200), received_record(channel)).await {
                    Ok(value) => value?,
                    Err(_) => break,
                };
            }
            return Ok(());
        }
        if config.stopped() {
            return Err("The session stopped before control processing".into());
        }
        let plain = Zeroizing::new(
            self.crypto
                .open(&record)
                .map_err(|error| error.to_string())?,
        );
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
            if self
                .last_keyframe_request
                .is_none_or(|last| last.elapsed() >= Duration::from_secs(1))
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
pub(crate) async fn received_record(
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
