//! Dedicated reliable channel, independent counters and bounded queues. A
//! replacement transport can resume only the same locally accepted file scope.
use crate::{
    remote_files::{Attachment, FileScope},
    remote_peer::Config,
};
use core_protocol::{
    crypto::ControlChannel,
    transport::{NativeDataChannel, NativeDataChannelEvent, NativeTransport, TransportError},
};
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
use zeroize::Zeroizing;

pub struct FileTransport {
    task: tokio::task::JoinHandle<()>,
    cancel: Arc<std::sync::atomic::AtomicBool>,
}
impl FileTransport {
    pub async fn start(
        config: &Config,
        transport: &mut NativeTransport,
        crypto: ControlChannel,
    ) -> Result<Self, String> {
        let scope = config.state.file_hub().activate(
            config.consent_id,
            config.consent_cancel.clone(),
            config.file_authorization(),
            &crypto.verified_session(),
        )?;
        let channel = if config.host {
            let deadline = Instant::now() + Duration::from_secs(8);
            loop {
                if config.stopped() {
                    return Err("File negotiation was cancelled".into());
                }
                if Instant::now() >= deadline {
                    return Err("The file channel did not arrive".into());
                }
                match tokio::time::timeout(
                    Duration::from_millis(25),
                    transport.incoming_channels.recv(),
                )
                .await
                {
                    Ok(Some(channel)) => break channel,
                    Ok(None) => return Err("The file transport closed".into()),
                    Err(_) => {}
                }
            }
        } else {
            transport
                .peer
                .create_data_channel("mixel-files-v1", None)
                .await
                .map_err(|_| "Cannot create the encrypted file channel")?
        };
        let valid = tokio::time::timeout(Duration::from_secs(1), async {
            Ok::<_, TransportError>(
                channel.label().await? == "mixel-files-v1"
                    && channel.ordered().await?
                    && channel.max_packet_life_time().await?.is_none()
                    && channel.max_retransmits().await?.is_none()
                    && channel.protocol().await?.is_empty()
                    && !channel.negotiated().await?,
            )
        })
        .await
        .map_err(|_| "File channel inspection timed out")?
        .map_err(|_| "Cannot inspect the file channel")?;
        if !valid {
            let _ = tokio::time::timeout(Duration::from_secs(2), channel.close()).await;
            return Err("Unexpected file channel configuration".into());
        }
        let stopped = config.input_authorization();
        let cancel = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let task_cancel = cancel.clone();
        let task = tokio::spawn(async move {
            let result = run(&scope, &channel, crypto, || {
                !stopped() || task_cancel.load(std::sync::atomic::Ordering::Acquire)
            })
            .await;
            let _ = tokio::time::timeout(Duration::from_secs(2), channel.close()).await;
            if let Err(error) = result {
                scope.transport_error(&error);
            }
        });
        Ok(Self { task, cancel })
    }
    pub async fn close(mut self) -> Result<(), String> {
        self.cancel
            .store(true, std::sync::atomic::Ordering::Release);
        match tokio::time::timeout(Duration::from_secs(5), &mut self.task).await {
            Ok(Ok(())) => Ok(()),
            Ok(Err(_)) => Err("The file network owner failed".into()),
            Err(_) => {
                self.task.abort();
                let _ = self.task.await;
                Err("The file network owner did not stop in time".into())
            }
        }
    }
}
async fn run(
    scope: &FileScope,
    channel: &Arc<dyn NativeDataChannel>,
    mut crypto: ControlChannel,
    stopped: impl Fn() -> bool,
) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(8);
    loop {
        if stopped() {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err("File transport opening timed out".into());
        }
        match tokio::time::timeout(Duration::from_millis(25), channel.poll()).await {
            Ok(Some(NativeDataChannelEvent::OnOpen)) => break,
            Ok(
                None
                | Some(
                    NativeDataChannelEvent::OnClose
                    | NativeDataChannelEvent::OnError
                    | NativeDataChannelEvent::OnMessage(_),
                ),
            ) => return Err("File transport failed before opening".into()),
            Err(_) | Ok(Some(_)) => {}
        }
    }
    if stopped() {
        return Ok(());
    }
    let mut attachment: Attachment = scope.attach()?;
    loop {
        if stopped() || !attachment.current() {
            return Ok(());
        }
        tokio::select! {
            biased;
            _=tokio::time::sleep(Duration::from_millis(25))=>{},
            next=attachment.output.recv()=>{
                let Some(plain)=next else{return Ok(());};
                if stopped()||!attachment.current(){return Ok(());}
                let record=crypto.seal(&plain).map_err(|_|"Cannot encrypt the file record")?;
                tokio::time::timeout(Duration::from_secs(3),channel.send(record.as_slice().into())).await.map_err(|_|"File transmission timed out")?.map_err(|_|"File transmission failed")?;
            },
            event=channel.poll()=>match event{
                Some(NativeDataChannelEvent::OnMessage(record))=>{
                    if record.data.len()>8192{return Err("Oversized encrypted file record".into());}
                    let plain=Zeroizing::new(crypto.open(&record.data).map_err(|_|"File record authentication failed")?);
                    if stopped()||!attachment.current(){return Ok(());}
                    attachment.wire(plain)?;
                },
                Some(NativeDataChannelEvent::OnOpen)=>return Err("Unexpected file channel reopening".into()),
                None|Some(NativeDataChannelEvent::OnClose|NativeDataChannelEvent::OnError)=>return Err("File transport disconnected".into()),
                Some(_)=>{},
            },
        }
    }
}
