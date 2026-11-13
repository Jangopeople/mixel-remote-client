//! Session-scoped explicit text transfers. The webview receives only completion
//! status; clipboard bytes remain in native memory and encrypted data records.
use crate::{remote_peer::Config, remote_session::ClipboardCommand};
use core_protocol::{
    clipboard::{self, Content, Message, Receiver},
    crypto::ControlChannel,
    transport::NativeDataChannel,
};
use crate::session_platform::{read_content, read_text, write_content};
use sha2::Digest;
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::mpsc;

pub struct ClipboardPipe {
    pub commands: mpsc::Receiver<ClipboardCommand>,
    pub allowed: bool,
    next: u64,
    last_remote: u64,
    receiver: Option<Receiver>,
    /// Both apps announced `clip2`: pictures and small files may be transferred too.
    pub rich: bool,
    /// Controller: a print job the host is sending, and the complete ones not yet shown.
    print_in: Option<Receiver>,
    pub printed: Vec<Vec<u8>>,
    /// Host: the transfer being received (its number), and one that expired unfinished: its
    /// remaining parts are dropped and its commit is answered "unavailable".
    receiving_id: u64,
    expired_id: Option<u64>,
    /// Controller: requests up to this number were given up; their late answers are dropped.
    abandoned: u64,
}
/// Controller: a clipboard request waiting for the host's answer.
pub struct Request {
    id: u64,
    push: bool,
    rich: bool,
    receiver: Option<Receiver>,
}
/// The request number a clipboard message belongs to.
fn message_id(message: &Message<'_>) -> u64 {
    match message {
        Message::Get(id) | Message::GetRich(id) | Message::Commit(id) | Message::Ack(id) | Message::Unavailable(id) => *id,
        Message::Begin { id, .. } | Message::Chunk { id, .. } => *id,
    }
}
impl ClipboardPipe {
    pub fn new(commands: mpsc::Receiver<ClipboardCommand>, allowed: bool) -> Self {
        Self {
            commands,
            allowed,
            next: 1,
            last_remote: 0,
            receiver: None,
            rich: false,
            print_in: None,
            printed: Vec::new(),
            receiving_id: 0,
            expired_id: None,
            abandoned: 0,
        }
    }
    /// Host: a transfer from the technician that did not finish in time is dropped (the session
    /// stays); the technician hears "unavailable" when its commit arrives.
    pub fn check_deadline(&mut self) -> Result<(), String> {
        if self
            .receiver
            .as_ref()
            .is_some_and(|value| value.expired(Instant::now()))
        {
            self.receiver = None;
            self.expired_id = Some(self.receiving_id);
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
            Message::GetRich(id) if self.rich => {
                self.start_request(id)?;
                match read_content(true).and_then(|content| {
                    content.encode().map_err(|error| error.to_string())
                }) {
                    Ok(payload) => send_payload(config, crypto, channel, id, &payload).await,
                    Err(_) => send(crypto, channel, Message::Unavailable(id)).await,
                }
            }
            Message::Begin { id, .. } => {
                self.start_request(id)?;
                self.receiving_id = id;
                self.expired_id = None;
                self.receiver = Some(
                    if self.rich {
                        Receiver::begin_rich(&message, Instant::now())
                    } else {
                        Receiver::begin(&message, Instant::now())
                    }
                    .map_err(|error| error.to_string())?,
                );
                Ok(())
            }
            Message::Chunk { id, .. } if self.receiver.is_none() && self.expired_id == Some(id) => Ok(()),
            Message::Commit(id) if self.receiver.is_none() && self.expired_id == Some(id) => {
                self.expired_id = None;
                send(crypto, channel, Message::Unavailable(id)).await
            }
            Message::Chunk { .. } => self
                .receiver
                .as_mut()
                .ok_or("Unexpected clipboard chunk")?
                .push(&message, Instant::now())
                .map_err(|error| error.to_string()),
            Message::Commit(id) => {
                let receiver = self.receiver.take().ok_or("No clipboard transfer is pending")?;
                let content = if self.rich {
                    receiver.commit_content(id, Instant::now())
                } else {
                    receiver.commit(id, Instant::now()).map(Content::Text)
                }
                .map_err(|error| error.to_string())?;
                if config.stopped() {
                    return Err("The session stopped before clipboard replacement".into());
                }
                let reply = if write_content(content).is_ok() {
                    Message::Ack(id)
                } else {
                    Message::Unavailable(id)
                };
                send(crypto, channel, reply).await
            }
            _ => Err("Unexpected remote clipboard message".into()),
        }
    }
    /// Controller: one record of a print job the host sends on its own. A job that cannot be
    /// taken (damaged, too slow, too many waiting) is dropped with its remaining parts; the
    /// session goes on.
    pub fn accept_print(&mut self, bytes: &[u8]) {
        if let Err(error) = self.take_print(bytes) {
            eprintln!("print job dropped: {error}");
            self.print_in = None;
        }
    }
    fn take_print(&mut self, bytes: &[u8]) -> Result<(), String> {
        let message = Message::decode(bytes).map_err(|error| error.to_string())?;
        match message {
            Message::Begin { .. } => {
                self.print_in = Some(
                    Receiver::begin_rich(&message, Instant::now()).map_err(|error| error.to_string())?,
                );
            }
            // The rest of a job that was dropped.
            Message::Chunk { .. } | Message::Commit(_) if self.print_in.is_none() => {}
            Message::Chunk { .. } => self
                .print_in
                .as_mut()
                .ok_or("Unexpected print job data")?
                .push(&message, Instant::now())
                .map_err(|error| error.to_string())?,
            Message::Commit(id) => {
                let content = self
                    .print_in
                    .take()
                    .ok_or("No print job is pending")?
                    .commit_content(id, Instant::now())
                    .map_err(|error| error.to_string())?;
                let Content::Files(mut files) = content else {
                    return Err("Invalid print job".into());
                };
                if files.len() != 1 || self.printed.len() >= 8 {
                    return Err("Invalid print job".into());
                }
                self.printed.push(files.remove(0).1);
            }
            _ => return Err("Invalid print job message".into()),
        }
        Ok(())
    }
    fn start_request(&mut self, id: u64) -> Result<(), String> {
        if self.receiver.is_some() || id <= self.last_remote {
            return Err("A clipboard request is duplicated or overlapping".into());
        }
        self.last_remote = id;
        Ok(())
    }
    /// Controller: sends a clipboard request (the whole payload for a push). The outer error is
    /// a broken transport; the inner one fails only this request (nothing was sent).
    pub async fn begin(
        &mut self,
        config: &Config,
        crypto: &mut ControlChannel,
        channel: &Arc<dyn NativeDataChannel>,
        push: bool,
    ) -> Result<Result<Request, String>, String> {
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
            let payload = match read_content(self.rich)
                .and_then(|content| content.encode().map_err(|_| "The clipboard content cannot be transferred".to_owned()))
            {
                Ok(payload) => payload,
                Err(error) => return Ok(Err(error)),
            };
            send_payload(config, crypto, channel, id, &payload).await?;
        } else if self.rich {
            send(crypto, channel, Message::GetRich(id)).await?;
        } else {
            send(crypto, channel, Message::Get(id)).await?;
        }
        Ok(Ok(Request { id, push, rich: self.rich, receiver: None }))
    }
    /// Tests: a request numbered `id` as if it had been sent.
    #[cfg(test)]
    pub fn test_request(&mut self, id: u64, push: bool) -> Request {
        self.next = id + 1;
        Request { id, push, rich: self.rich, receiver: None }
    }
    /// Controller: the host did not answer `request` in time; its answer may still come.
    pub fn give_up(&mut self, request: &Request) {
        self.abandoned = self.abandoned.max(request.id);
    }
    /// Controller: a clipboard message answering a request that was given up.
    pub fn late_answer(&self, plain: &[u8]) -> bool {
        Message::decode(plain).is_ok_and(|message| message_id(&message) <= self.abandoned)
    }
}
impl Request {
    /// Controller: one clipboard message from the host for this request. `Some` when the request
    /// is complete (with its outcome), `None` while more parts are to come. An error is a host
    /// breaking the protocol.
    pub fn accept(&mut self, plain: &[u8], stopped: bool) -> Result<Option<Result<(), String>>, String> {
        let (id, push, rich) = (self.id, self.push, self.rich);
        let message = Message::decode(plain).map_err(|error| error.to_string())?;
        match message {
            Message::Ack(reply) if push && reply == id => Ok(Some(Ok(()))),
            Message::Unavailable(reply) if reply == id => Ok(Some(Err(if rich {
                "The remote clipboard is empty, or holds folders or more than 32 MB"
            } else {
                "The remote clipboard is unavailable or exceeds the plain-text limit"
            }
            .into()))),
            Message::Begin { id: reply, .. } if !push && reply == id && self.receiver.is_none() => {
                self.receiver = Some(
                    if rich {
                        Receiver::begin_rich(&message, Instant::now())
                    } else {
                        Receiver::begin(&message, Instant::now())
                    }
                    .map_err(|error| error.to_string())?,
                );
                Ok(None)
            }
            Message::Chunk { id: reply, .. } if !push && reply == id => {
                self.receiver
                    .as_mut()
                    .ok_or("Clipboard response has no header")?
                    .push(&message, Instant::now())
                    .map_err(|error| error.to_string())?;
                Ok(None)
            }
            Message::Commit(reply) if !push && reply == id => {
                let receiver = self.receiver.take().ok_or("Clipboard response has no transaction")?;
                let content = if rich {
                    receiver.commit_content(id, Instant::now())
                } else {
                    receiver.commit(id, Instant::now()).map(Content::Text)
                }
                .map_err(|error| error.to_string())?;
                if stopped {
                    return Err("The session stopped before clipboard replacement".into());
                }
                Ok(Some(write_content(content)))
            }
            _ => Err("Invalid clipboard response".into()),
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
    clipboard::validate(text).map_err(|error| error.to_string())?;
    send_payload(config, crypto, channel, id, text.as_bytes()).await
}
async fn send_payload(
    config: &Config,
    crypto: &mut ControlChannel,
    channel: &Arc<dyn NativeDataChannel>,
    id: u64,
    payload: &[u8],
) -> Result<(), String> {
    if config.stopped() {
        return Err("Clipboard authorization expired before transmission".into());
    }
    let lifetime = if payload.len() > clipboard::MAX_TEXT || payload.first() == Some(&0) {
        clipboard::RICH_LIFETIME
    } else {
        clipboard::LIFETIME
    };
    let deadline = Instant::now() + lifetime;
    send(
        crypto,
        channel,
        Message::Begin {
            id,
            length: payload.len() as u32,
            digest: sha2::Sha256::digest(payload).into(),
        },
    )
    .await?;
    for (index, bytes) in payload.chunks(clipboard::CHUNK).enumerate() {
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
