//! In-session chat has independent keys and lives only as long as its transport.
use crate::remote_peer::Config;
use core_protocol::{
    crypto::ControlChannel,
    transport::{NativeDataChannel, NativeDataChannelEvent, NativeTransport, TransportError},
};
use serde::Serialize;
use std::{
    collections::VecDeque,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::sync::{mpsc, oneshot};
use uuid::Uuid;
use zeroize::Zeroizing;

const LABEL: &str = "mixel-chat-v1";
const GRANT: &[u8] = b"MCH1:grant";
const READY: &[u8] = b"MCH1:ready";
const TEXT: &[u8] = b"MCH1:text:";
const MAX_TEXT: usize = 2048;
const MAX_HISTORY: usize = 80;

type Authorization = Arc<dyn Fn() -> bool + Send + Sync>;

#[derive(Clone, Serialize)]
pub struct ChatMessage {
    pub id: u64,
    pub mine: bool,
    pub text: String,
}

struct Outgoing {
    text: String,
    expires: Instant,
    reply: oneshot::Sender<Result<(), String>>,
}

struct Conversation {
    session: Uuid,
    live: Authorization,
    output: mpsc::Sender<Outgoing>,
    messages: VecDeque<ChatMessage>,
    next_id: u64,
}

#[derive(Clone, Default)]
pub struct ChatHub(Arc<Mutex<Option<Conversation>>>);

impl ChatHub {
    fn attach(
        &self,
        session: Uuid,
        live: Authorization,
        output: mpsc::Sender<Outgoing>,
    ) -> Result<Attachment, String> {
        let mut state = self.0.lock().map_err(|_| "Chat state is unavailable")?;
        if !live() || state.as_ref().is_some_and(|current| (current.live)()) {
            return Err("A chat transport is already active or this session has ended".into());
        }
        *state = Some(Conversation {
            session,
            live,
            output,
            messages: VecDeque::new(),
            next_id: 1,
        });
        Ok(Attachment {
            hub: self.clone(),
            session,
        })
    }

    pub fn messages(&self, session: Uuid) -> Option<Vec<ChatMessage>> {
        let state = self.0.lock().ok()?;
        let conversation = state
            .as_ref()
            .filter(|current| current.session == session && (current.live)())?;
        Some(conversation.messages.iter().cloned().collect())
    }

    pub async fn send(&self, session: Uuid, text: String) -> Result<(), String> {
        validate(&text)?;
        let (reply, received) = oneshot::channel();
        {
            let state = self.0.lock().map_err(|_| "Chat state is unavailable")?;
            let conversation = state
                .as_ref()
                .filter(|current| current.session == session && (current.live)())
                .ok_or("Chat is unavailable for this session")?;
            conversation
                .output
                .try_send(Outgoing {
                    text,
                    expires: Instant::now() + Duration::from_secs(3),
                    reply,
                })
                .map_err(|_| "Chat is busy or has closed")?;
        }
        tokio::time::timeout(Duration::from_secs(4), received)
            .await
            .map_err(|_| "The chat message timed out")?
            .map_err(|_| "The chat connection closed")?
    }
}

struct Attachment {
    hub: ChatHub,
    session: Uuid,
}

impl Attachment {
    fn append(&self, mine: bool, text: String) -> Result<(), String> {
        let mut state = self.hub.0.lock().map_err(|_| "Chat state is unavailable")?;
        let conversation = state
            .as_mut()
            .filter(|current| current.session == self.session && (current.live)())
            .ok_or("The chat session ended")?;
        if conversation.messages.len() == MAX_HISTORY {
            conversation.messages.pop_front();
        }
        let id = conversation.next_id;
        conversation.next_id = id.checked_add(1).ok_or("The chat history is full")?;
        conversation
            .messages
            .push_back(ChatMessage { id, mine, text });
        Ok(())
    }
}

impl Drop for Attachment {
    fn drop(&mut self) {
        if let Ok(mut state) = self.hub.0.lock()
            && state
                .as_ref()
                .is_some_and(|current| current.session == self.session)
        {
            *state = None;
        }
    }
}

fn validate(text: &str) -> Result<(), String> {
    if text.trim().is_empty()
        || text.len() > MAX_TEXT
        || text
            .chars()
            .any(|c| c.is_control() && !matches!(c, '\n' | '\t'))
    {
        return Err("Enter a plain-text message of up to 2,048 bytes".into());
    }
    Ok(())
}

pub struct ChatTransport(tokio::task::JoinHandle<()>);

impl ChatTransport {
    pub fn start(config: &Config, transport: &mut NativeTransport, crypto: ControlChannel) -> Self {
        let peer = transport.peer.clone();
        let host = config.host;
        let incoming = (!host).then(|| {
            let (_, unused) = mpsc::channel(1);
            std::mem::replace(&mut transport.incoming_channels, unused)
        });
        let hub = config.state.chat_hub();
        let session = config.session;
        let live = config.input_authorization();
        Self(tokio::spawn(async move {
            let channel = tokio::time::timeout(Duration::from_secs(8), async {
                if let Some(mut incoming) = incoming {
                    incoming.recv().await
                } else {
                    peer.create_data_channel(LABEL, None).await.ok()
                }
            })
            .await
            .ok()
            .flatten();
            if let Some(channel) = channel {
                let _ = run(&channel, crypto, hub, session, host, live).await;
                let _ = tokio::time::timeout(Duration::from_secs(1), channel.close()).await;
            }
        }))
    }

    pub async fn close(self) {
        self.0.abort();
        let _ = self.0.await;
    }
}

async fn send_record(
    channel: &Arc<dyn NativeDataChannel>,
    crypto: &mut ControlChannel,
    data: &[u8],
    deadline: Instant,
) -> Result<(), String> {
    let record = crypto
        .seal(data)
        .map_err(|_| "Cannot protect the chat message")?;
    tokio::time::timeout_at(deadline.into(), channel.send(record.as_slice().into()))
        .await
        .map_err(|_| "Chat transmission timed out")?
        .map_err(|_| "Chat transmission failed".into())
}

fn open_record(crypto: &mut ControlChannel, data: &[u8]) -> Result<Zeroizing<Vec<u8>>, String> {
    if data.len() > MAX_TEXT + 128 {
        return Err("Oversized chat record".into());
    }
    crypto
        .open(data)
        .map(Zeroizing::new)
        .map_err(|_| "Invalid chat record".into())
}

async fn handshake(
    channel: &Arc<dyn NativeDataChannel>,
    crypto: &mut ControlChannel,
    host: bool,
    live: &Authorization,
) -> Result<(), String> {
    let valid: Result<bool, TransportError> = async {
        Ok(channel.label().await? == LABEL
            && channel.ordered().await?
            && channel.max_packet_life_time().await?.is_none()
            && channel.max_retransmits().await?.is_none()
            && channel.protocol().await?.is_empty()
            && !channel.negotiated().await?)
    }
    .await;
    if !live() || !matches!(valid, Ok(true)) {
        return Err("Chat is unavailable".into());
    }
    loop {
        match channel.poll().await {
            Some(NativeDataChannelEvent::OnOpen) => break,
            None
            | Some(
                NativeDataChannelEvent::OnMessage(_)
                | NativeDataChannelEvent::OnClose
                | NativeDataChannelEvent::OnError,
            ) => return Err("Chat channel did not open".into()),
            Some(_) => {}
        }
    }
    if !live() {
        return Err("The chat session ended".into());
    }
    if host {
        send_record(
            channel,
            crypto,
            GRANT,
            Instant::now() + Duration::from_secs(2),
        )
        .await?;
    }
    loop {
        match channel.poll().await {
            Some(NativeDataChannelEvent::OnMessage(message)) => {
                let expected = if host { READY } else { GRANT };
                if !live() || open_record(crypto, &message.data)?.as_slice() != expected {
                    return Err("Chat permission was not authenticated".into());
                }
                if !host {
                    send_record(
                        channel,
                        crypto,
                        READY,
                        Instant::now() + Duration::from_secs(2),
                    )
                    .await?;
                }
                return Ok(());
            }
            None | Some(NativeDataChannelEvent::OnClose | NativeDataChannelEvent::OnError) => {
                return Err("Chat permission exchange closed".into());
            }
            Some(_) => {}
        }
    }
}

async fn run(
    channel: &Arc<dyn NativeDataChannel>,
    mut crypto: ControlChannel,
    hub: ChatHub,
    session: Uuid,
    host: bool,
    live: Authorization,
) -> Result<(), String> {
    tokio::time::timeout(
        Duration::from_secs(6),
        handshake(channel, &mut crypto, host, &live),
    )
    .await
    .map_err(|_| "Chat permission exchange timed out")??;
    let (output, mut pending) = mpsc::channel::<Outgoing>(4);
    let attachment = hub.attach(session, live.clone(), output)?;
    let mut window = Instant::now();
    let mut received = 0;
    while live() {
        tokio::select! {
            _ = tokio::time::sleep(Duration::from_millis(50)) => {},
            next = pending.recv() => {
                let Some(message) = next else { break; };
                if !live() || Instant::now() >= message.expires || message.reply.is_closed() {
                    let _ = message.reply.send(Err("The chat message expired".into()));
                    continue;
                }
                let mut plain = Zeroizing::new(TEXT.to_vec());
                plain.extend_from_slice(message.text.as_bytes());
                let result = send_record(channel, &mut crypto, &plain, message.expires).await
                    .and_then(|()| attachment.append(true, message.text));
                let _ = message.reply.send(result.clone());
                result?;
            }
            event = channel.poll() => match event {
                Some(NativeDataChannelEvent::OnMessage(record)) => {
                    if !live() { break; }
                    if window.elapsed() >= Duration::from_secs(1) {
                        received = 0;
                        window = Instant::now();
                    }
                    received += 1;
                    if received > 16 {
                        return Err("The peer exceeded the chat rate limit".into());
                    }
                    let plain = open_record(&mut crypto, &record.data)?;
                    let text = plain.strip_prefix(TEXT).and_then(|bytes| std::str::from_utf8(bytes).ok())
                        .ok_or("Invalid chat message")?;
                    validate(text)?;
                    attachment.append(false, text.to_owned())?;
                }
                None | Some(NativeDataChannelEvent::OnClose | NativeDataChannelEvent::OnError) => break,
                Some(_) => {}
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use core_protocol::{
        crypto::{Context, Handshake, Role},
        transport::local_fingerprint,
    };
    use ed25519_dalek::SigningKey;
    use std::sync::atomic::{AtomicBool, Ordering};

    async fn wait_for(hub: &ChatHub, session: Uuid, count: usize) -> Vec<ChatMessage> {
        tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                if let Some(messages) = hub.messages(session)
                    && messages.len() >= count
                {
                    return messages;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("the native chat stream must deliver its authenticated messages")
    }

    #[tokio::test]
    async fn native_webrtc_chat_is_bidirectional_and_stops_with_session() {
        let mut controller = NativeTransport::new("127.0.0.1:0".into(), vec![])
            .await
            .unwrap();
        let mut host = NativeTransport::new("127.0.0.1:0".into(), vec![])
            .await
            .unwrap();
        let controller_control = controller
            .peer
            .create_data_channel("mixel-control-v1", None)
            .await
            .unwrap();
        let offer = controller
            .local_sdp(controller.peer.create_offer(None).await.unwrap())
            .await
            .unwrap();
        let controller_fp = local_fingerprint(&offer.sdp).unwrap();
        host.peer.set_remote_description(offer).await.unwrap();
        let answer = host
            .local_sdp(host.peer.create_answer(None).await.unwrap())
            .await
            .unwrap();
        let host_fp = local_fingerprint(&answer.sdp).unwrap();
        controller
            .peer
            .set_remote_description(answer)
            .await
            .unwrap();
        let host_control =
            tokio::time::timeout(Duration::from_secs(8), host.incoming_channels.recv())
                .await
                .unwrap()
                .unwrap();
        for channel in [&host_control, &controller_control] {
            assert!(matches!(
                tokio::time::timeout(Duration::from_secs(8), channel.poll()).await,
                Ok(Some(NativeDataChannelEvent::OnOpen))
            ));
        }
        let controller_key = SigningKey::from_bytes(&[5; 32]);
        let host_key = SigningKey::from_bytes(&[6; 32]);
        let context = Context {
            session_id: [7; 32],
            controller: controller_key.verifying_key(),
            host: host_key.verifying_key(),
        };
        let a = Handshake::new(
            context.clone(),
            Role::Controller,
            &controller_key,
            controller_fp,
        )
        .unwrap();
        let b = Handshake::new(context, Role::Host, &host_key, host_fp).unwrap();
        let ah = a.hello();
        let bh = b.hello();
        let mut ac = a
            .finish(&bh, controller.observed_fingerprint().await.unwrap())
            .unwrap();
        let mut bc = b
            .finish(&ah, host.observed_fingerprint().await.unwrap())
            .unwrap();
        let ca = ac.take_chat_channel().unwrap();
        let cb = bc.take_chat_channel().unwrap();
        assert!(ac.take_chat_channel().is_err());

        let controller_hub = ChatHub::default();
        let host_hub = ChatHub::default();
        let session = Uuid::from_u128(1);
        assert!(
            controller_hub
                .send(session, "Before permission".into())
                .await
                .is_err()
        );
        assert!(host_hub.messages(session).is_none());
        let hb = host.peer.create_data_channel(LABEL, None).await.unwrap();
        let ha = tokio::time::timeout(Duration::from_secs(8), controller.incoming_channels.recv())
            .await
            .unwrap()
            .unwrap();
        let live = Arc::new(AtomicBool::new(true));
        let state = live.clone();
        let permit: Authorization = Arc::new(move || state.load(Ordering::Acquire));
        let client_task = tokio::spawn({
            let hub = controller_hub.clone();
            let permit = permit.clone();
            async move { run(&ha, ca, hub, session, false, permit).await }
        });
        let host_task = tokio::spawn({
            let hub = host_hub.clone();
            async move { run(&hb, cb, hub, session, true, permit).await }
        });
        assert!(wait_for(&controller_hub, session, 0).await.is_empty());
        assert!(wait_for(&host_hub, session, 0).await.is_empty());
        assert!(controller_hub.send(session, "☃".repeat(800)).await.is_err());
        assert!(
            controller_hub
                .send(Uuid::from_u128(2), "Old session".into())
                .await
                .is_err()
        );
        controller_hub
            .send(session, "Grüezi <b>Host</b> 👋".into())
            .await
            .unwrap();
        host_hub
            .send(session, "こんにちは Controller".into())
            .await
            .unwrap();
        let host_messages = wait_for(&host_hub, session, 2).await;
        let controller_messages = wait_for(&controller_hub, session, 2).await;
        assert_eq!(
            host_messages.iter().find(|m| !m.mine).unwrap().text,
            "Grüezi <b>Host</b> 👋"
        );
        assert_eq!(
            controller_messages.iter().find(|m| !m.mine).unwrap().text,
            "こんにちは Controller"
        );
        live.store(false, Ordering::Release);
        assert!(controller_hub.messages(session).is_none());
        assert!(host_hub.send(session, "After Stop".into()).await.is_err());
        tokio::time::timeout(Duration::from_secs(2), async {
            client_task.await.unwrap().unwrap();
            host_task.await.unwrap().unwrap();
        })
        .await
        .unwrap();

        // Chat cleanup cannot consume or invalidate a control record.
        let record = ac.seal(b"control channel remains available").unwrap();
        controller_control
            .send(record.as_slice().into())
            .await
            .unwrap();
        let control = tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                match host_control.poll().await {
                    Some(NativeDataChannelEvent::OnMessage(record)) => break record,
                    None
                    | Some(NativeDataChannelEvent::OnClose | NativeDataChannelEvent::OnError) => {
                        panic!("control channel closed during chat cleanup")
                    }
                    Some(_) => {}
                }
            }
        })
        .await
        .unwrap();
        assert_eq!(
            bc.open(&control.data).unwrap(),
            b"control channel remains available"
        );
        controller.close().await.unwrap();
        host.close().await.unwrap();
        println!(
            "PASS: real WebRTC chat delivered Unicode in both directions; consent, limits, Stop, and control isolation verified"
        );
    }
}
