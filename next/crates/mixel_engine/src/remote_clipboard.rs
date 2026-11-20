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
    /// Controller: the SHA-256 digest of the last clipboard payload synced (pushed or pulled).
    /// When a background pull (`push == false`) receives identical bytes, the local pasteboard is
    /// not rewritten so local copies are never clobbered by an unchanged remote clipboard.
    pub last_digest: Option<[u8; 32]>,
    /// Host: the SHA-256 digest of the last clipboard payload sent to or received from the
    /// controller. When a pull (`Get` / `GetRich`) finds the host clipboard unchanged, the host
    /// replies with a short `Ack(id)` instead of re-sending megabytes of unchanged clipboard data.
    pub last_host_digest: Option<[u8; 32]>,
    /// Host: the controller announced `clip-same` and understands `Ack(id)` as "unchanged".
    pub peer_accepts_unchanged: bool,
}
/// Controller: a clipboard request waiting for the host's answer.
pub struct Request {
    id: u64,
    push: bool,
    rich: bool,
    receiver: Option<Receiver>,
    payload_digest: Option<[u8; 32]>,
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
            last_digest: None,
            last_host_digest: None,
            peer_accepts_unchanged: false,
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
    /// Host: answer a pull with a short `Ack` ("unchanged") instead of the payload. Only for a
    /// controller that announced `clip-same`: older apps end the session on an unexpected Ack.
    pub fn reply_unchanged(&self, digest: &[u8; 32]) -> bool {
        self.peer_accepts_unchanged && self.last_host_digest.as_ref() == Some(digest)
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
                    Ok(text) => {
                        let digest: [u8; 32] = sha2::Sha256::digest(text.as_bytes()).into();
                        if self.reply_unchanged(&digest) {
                            send(crypto, channel, Message::Ack(id)).await
                        } else {
                            let res = send_text(config, crypto, channel, id, &text).await;
                            if res.is_ok() {
                                self.last_host_digest = Some(digest);
                            }
                            res
                        }
                    }
                    Err(_) => send(crypto, channel, Message::Unavailable(id)).await,
                }
            }
            Message::GetRich(id) if self.rich => {
                self.start_request(id)?;
                match read_content(true).and_then(|content| {
                    content.encode().map_err(|error| error.to_string())
                }) {
                    Ok(payload) => {
                        let digest: [u8; 32] = sha2::Sha256::digest(&payload[..]).into();
                        if self.reply_unchanged(&digest) {
                            send(crypto, channel, Message::Ack(id)).await
                        } else {
                            let res = send_payload(config, crypto, channel, id, &payload).await;
                            if res.is_ok() {
                                self.last_host_digest = Some(digest);
                            }
                            res
                        }
                    }
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
                let encoded_digest = content
                    .encode()
                    .ok()
                    .map(|bytes| sha2::Sha256::digest(&bytes[..]).into());
                let reply = if write_content(content).is_ok() {
                    if let Some(digest) = encoded_digest {
                        self.last_host_digest = Some(digest);
                    }
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
        let mut payload_digest = None;
        if push {
            // Read only in direct response to the local Send clipboard action.
            // A local format/permission error must not tear down the session.
            let payload = match read_content(self.rich)
                .and_then(|content| content.encode().map_err(|_| "The clipboard content cannot be transferred".to_owned()))
            {
                Ok(payload) => payload,
                Err(error) => return Ok(Err(error)),
            };
            let digest: [u8; 32] = sha2::Sha256::digest(&payload[..]).into();
            self.last_digest = Some(digest);
            payload_digest = Some(digest);
            send_payload(config, crypto, channel, id, &payload).await?;
        } else if self.rich {
            send(crypto, channel, Message::GetRich(id)).await?;
        } else {
            send(crypto, channel, Message::Get(id)).await?;
        }
        Ok(Ok(Request { id, push, rich: self.rich, receiver: None, payload_digest }))
    }
    /// Tests: a request numbered `id` as if it had been sent.
    #[cfg(test)]
    pub fn test_request(&mut self, id: u64, push: bool) -> Request {
        self.next = id + 1;
        Request { id, push, rich: self.rich, receiver: None, payload_digest: None }
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
        let mut last_digest = None;
        self.accept_deduped(plain, stopped, &mut last_digest)
    }

    /// Like [`accept`], but skips rewriting the local OS clipboard when a pull (`push == false`)
    /// receives the exact same payload digest that was last pushed or pulled (`last_digest`).
    pub fn accept_deduped(
        &mut self,
        plain: &[u8],
        stopped: bool,
        last_digest: &mut Option<[u8; 32]>,
    ) -> Result<Option<Result<(), String>>, String> {
        let (id, push, rich) = (self.id, self.push, self.rich);
        let message = Message::decode(plain).map_err(|error| error.to_string())?;
        match message {
            Message::Ack(reply) if push && reply == id => {
                if let Some(digest) = self.payload_digest {
                    *last_digest = Some(digest);
                }
                Ok(Some(Ok(())))
            }
            Message::Ack(reply) if !push && reply == id && self.receiver.is_none() => {
                Ok(Some(Ok(())))
            }
            Message::Unavailable(reply) if reply == id => Ok(Some(Err(if rich {
                "The remote clipboard is empty, or holds folders or more than 32 MB"
            } else {
                "The remote clipboard is unavailable or exceeds the plain-text limit"
            }
            .into()))),
            Message::Begin { id: reply, digest, .. } if !push && reply == id && self.receiver.is_none() => {
                self.payload_digest = Some(digest);
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
                if let Some(digest) = self.payload_digest
                    && *last_digest == Some(digest)
                {
                    return Ok(Some(Ok(())));
                }
                let result = write_content(content);
                if result.is_ok()
                    && let Some(digest) = self.payload_digest
                {
                    *last_digest = Some(digest);
                }
                Ok(Some(result))
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unchanged_pull_with_matching_last_digest_does_not_touch_local_clipboard() {
        let (_tx, rx) = mpsc::channel(1);
        let mut pipe = ClipboardPipe::new(rx, true);
        let payload = b"same-remote-text";
        let digest: [u8; 32] = sha2::Sha256::digest(payload).into();
        pipe.last_digest = Some(digest);

        let mut request = pipe.test_request(7, false);
        let begin = Message::Begin {
            id: 7,
            length: payload.len() as u32,
            digest,
        }
        .encode()
        .unwrap();
        let chunk = Message::Chunk {
            id: 7,
            offset: 0,
            bytes: payload,
        }
        .encode()
        .unwrap();
        let commit = Message::Commit(7).encode().unwrap();

        assert_eq!(
            request.accept_deduped(&begin, false, &mut pipe.last_digest),
            Ok(None)
        );
        assert_eq!(
            request.accept_deduped(&chunk, false, &mut pipe.last_digest),
            Ok(None)
        );
        assert_eq!(
            request.accept_deduped(&commit, false, &mut pipe.last_digest),
            Ok(Some(Ok(())))
        );
        assert_eq!(pipe.last_digest, Some(digest));
    }

    #[test]
    fn an_unchanged_host_clipboard_answers_a_pull_with_a_short_ack() {
        // The host answers a background pull with Ack(id) when its clipboard did not change; the
        // controller takes that as "nothing new" and leaves its own clipboard alone.
        let (_tx, rx) = mpsc::channel(1);
        let mut pipe = ClipboardPipe::new(rx, true);
        let mut request = pipe.test_request(9, false);
        let ack = Message::Ack(9).encode().unwrap();
        assert_eq!(request.accept_deduped(&ack, false, &mut pipe.last_digest), Ok(Some(Ok(()))));
        assert_eq!(pipe.last_digest, None);
        // An Ack for another request is a protocol error, not silently accepted.
        let mut other = pipe.test_request(10, false);
        let stray = Message::Ack(3).encode().unwrap();
        assert!(other.accept_deduped(&stray, false, &mut pipe.last_digest).is_err());
    }

    #[test]
    fn a_host_never_sends_the_short_unchanged_answer_to_an_older_controller() {
        // 2.1.6 and older controllers end the session on an Ack to a pull (lab, 20 Nov 2026: sessions
        // to a 2.1.7 Mac dropped after a few seconds). They must always get the full clipboard.
        let (_tx, rx) = mpsc::channel(1);
        let mut pipe = ClipboardPipe::new(rx, true);
        let digest: [u8; 32] = sha2::Sha256::digest(b"same").into();
        pipe.last_host_digest = Some(digest);
        assert!(!pipe.reply_unchanged(&digest), "older controller: full payload");
        pipe.peer_accepts_unchanged = true;
        assert!(pipe.reply_unchanged(&digest), "clip-same controller: short Ack");
        let other: [u8; 32] = sha2::Sha256::digest(b"changed").into();
        assert!(!pipe.reply_unchanged(&other), "a changed clipboard is always sent");
    }

    #[test]
    fn this_app_announces_that_it_understands_the_unchanged_answer() {
        assert!(crate::remote_control::own_features().contains(&core_protocol::clipboard::FEATURE_UNCHANGED));
    }
}
