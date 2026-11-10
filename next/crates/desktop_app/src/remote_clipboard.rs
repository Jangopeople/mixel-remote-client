//! Session-scoped explicit text transfers. The webview receives only completion
//! status; clipboard bytes remain in native memory and encrypted data records.
use crate::{remote_peer::Config, remote_session::ClipboardCommand};
use core_protocol::{
    clipboard::{self, Message, Receiver},
    crypto::ControlChannel,
    transport::NativeDataChannel,
};
use crate::session_platform::{read_text, write_text};
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::mpsc;
use zeroize::Zeroizing;

pub struct ClipboardPipe {
    pub commands: mpsc::Receiver<ClipboardCommand>,
    pub allowed: bool,
    next: u64,
    last_remote: u64,
    receiver: Option<Receiver>,
}
impl ClipboardPipe {
    pub fn new(commands: mpsc::Receiver<ClipboardCommand>, allowed: bool) -> Self {
        Self {
            commands,
            allowed,
            next: 1,
            last_remote: 0,
            receiver: None,
        }
    }
    pub fn check_deadline(&mut self) -> Result<(), String> {
        if self
            .receiver
            .as_ref()
            .is_some_and(|value| value.expired(Instant::now()))
        {
            self.receiver = None;
            return Err("The clipboard transaction expired".into());
        }
        Ok(())
    }
    pub fn receiving(&self) -> bool {
        self.receiver.is_some()
    }
    pub async fn host(
        &mut self,
        config: &Config,
        crypto: &mut ControlChannel,
        channel: &Arc<dyn NativeDataChannel>,
        bytes: &[u8],
    ) -> Result<(), String> {
        if !config.host || !self.allowed || config.stopped() {
            return Err("Remote clipboard access is not authorized".into());
        }
        self.check_deadline()?;
        let message = Message::decode(bytes).map_err(|error| error.to_string())?;
        match message {
            Message::Get(id) => {
                self.start_request(id)?;
                match read_text() {
                    Ok(text) => send_text(config, crypto, channel, id, &text).await,
                    Err(_) => send(crypto, channel, Message::Unavailable(id)).await,
                }
            }
            Message::Begin { id, .. } => {
                self.start_request(id)?;
                self.receiver = Some(
                    Receiver::begin(&message, Instant::now()).map_err(|error| error.to_string())?,
                );
                Ok(())
            }
            Message::Chunk { .. } => self
                .receiver
                .as_mut()
                .ok_or("Unexpected clipboard chunk")?
                .push(&message, Instant::now())
                .map_err(|error| error.to_string()),
            Message::Commit(id) => {
                let text = self
                    .receiver
                    .take()
                    .ok_or("No clipboard transfer is pending")?
                    .commit(id, Instant::now())
                    .map_err(|error| error.to_string())?;
                if config.stopped() {
                    return Err("The session stopped before clipboard replacement".into());
                }
                let reply = if write_text(&text).is_ok() {
                    Message::Ack(id)
                } else {
                    Message::Unavailable(id)
                };
                send(crypto, channel, reply).await
            }
            _ => Err("Unexpected remote clipboard message".into()),
        }
    }
    fn start_request(&mut self, id: u64) -> Result<(), String> {
        if self.receiver.is_some() || id <= self.last_remote {
            return Err("A clipboard request is duplicated or overlapping".into());
        }
        self.last_remote = id;
        Ok(())
    }
    pub async fn request(
        &mut self,
        config: &Config,
        crypto: &mut ControlChannel,
        channel: &Arc<dyn NativeDataChannel>,
        push: bool,
        last_video_idle: &mut Instant,
    ) -> Result<Result<(), String>, String> {
        if config.host || !self.allowed || config.stopped() {
            return Ok(Err("The host has not granted clipboard access".into()));
        }
        let id = self.next;
        self.next = self
            .next
            .checked_add(1)
            .ok_or("Clipboard request sequence exhausted")?;
        if push {
            // Read only in direct response to the local Send clipboard action.
            // A local format/permission error must not tear down the session.
            let text = match read_text() {
                Ok(text) => text,
                Err(error) => return Ok(Err(error)),
            };
            send_text(config, crypto, channel, id, &text).await?;
        } else {
            send(crypto, channel, Message::Get(id)).await?;
        }
        let mut receiver: Option<Receiver> = None;
        loop {
            if config.stopped() {
                return Err("The clipboard transfer stopped".into());
            }
            let record = super::remote_control::received_record(channel).await?;
            let plain = Zeroizing::new(crypto.open(&record).map_err(|error| error.to_string())?);
            if plain.as_slice() == b"mixel-video-idle-v1" {
                *last_video_idle = Instant::now();
                continue;
            }
            let message = Message::decode(&plain).map_err(|error| error.to_string())?;
            match message {
                Message::Ack(reply) if push && reply == id => return Ok(Ok(())),
                Message::Unavailable(reply) if reply == id => {
                    return Ok(Err(
                        "The remote clipboard is unavailable or exceeds the plain-text limit"
                            .into(),
                    ));
                }
                Message::Begin { id: reply, .. } if !push && reply == id && receiver.is_none() => {
                    receiver = Some(
                        Receiver::begin(&message, Instant::now())
                            .map_err(|error| error.to_string())?,
                    )
                }
                Message::Chunk { .. } if !push => receiver
                    .as_mut()
                    .ok_or("Clipboard response has no header")?
                    .push(&message, Instant::now())
                    .map_err(|error| error.to_string())?,
                Message::Commit(reply) if !push && reply == id => {
                    let text = receiver
                        .take()
                        .ok_or("Clipboard response has no transaction")?
                        .commit(id, Instant::now())
                        .map_err(|error| error.to_string())?;
                    if config.stopped() {
                        return Err("The session stopped before clipboard replacement".into());
                    }
                    return Ok(write_text(&text));
                }
                _ => return Err("Invalid clipboard response".into()),
            }
        }
    }
}
pub async fn send(
    crypto: &mut ControlChannel,
    channel: &Arc<dyn NativeDataChannel>,
    message: Message<'_>,
) -> Result<(), String> {
    let plain = message.encode().map_err(|error| error.to_string())?;
    let record = crypto.seal(&plain).map_err(|error| error.to_string())?;
    tokio::time::timeout(
        Duration::from_secs(2),
        channel.send(record.as_slice().into()),
    )
    .await
    .map_err(|_| "Clipboard transmission timed out")?
    .map_err(|_| "Clipboard transport closed".to_owned())
}
async fn send_text(
    config: &Config,
    crypto: &mut ControlChannel,
    channel: &Arc<dyn NativeDataChannel>,
    id: u64,
    text: &str,
) -> Result<(), String> {
    if config.stopped() {
        return Err("Clipboard authorization expired before transmission".into());
    }
    clipboard::validate(text).map_err(|error| error.to_string())?;
    let deadline = Instant::now() + clipboard::LIFETIME;
    send(
        crypto,
        channel,
        Message::Begin {
            id,
            length: text.len() as u32,
            digest: clipboard::digest(text),
        },
    )
    .await?;
    for (index, bytes) in text.as_bytes().chunks(clipboard::CHUNK).enumerate() {
        if config.stopped() || Instant::now() >= deadline {
            return Err("The clipboard transfer stopped or expired".into());
        }
        send(
            crypto,
            channel,
            Message::Chunk {
                id,
                offset: (index * clipboard::CHUNK) as u32,
                bytes,
            },
        )
        .await?;
    }
    if config.stopped() {
        return Err("The clipboard transfer stopped".into());
    }
    send(crypto, channel, Message::Commit(id)).await
}
