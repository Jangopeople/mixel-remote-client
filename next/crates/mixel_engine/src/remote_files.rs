//! Consent-scoped file owners. Only the native picker supplies paths. Blocking
//! disk I/O stays on a tracked owner, independent of video and input ACKs.
use core_protocol::{
    crypto::{Role, VerifiedSession},
    files::{self, Message, Progress, Rejection, TransferId},
};
use native_os_capture::desktop_files::{IncomingFile, SourceFile};
use serde::Serialize;
use std::{
    collections::{BTreeMap, VecDeque},
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc::{self, SyncSender},
    },
    thread::JoinHandle,
    time::{Duration, Instant},
};
use tokio::sync::{mpsc as async_queue, oneshot};
use uuid::Uuid;
use zeroize::Zeroizing;

type Authorization = Arc<dyn Fn() -> bool + Send + Sync>;
type Reply = oneshot::Sender<Result<(), String>>;
const WINDOW: usize = 8;
const OFFER_LIFETIME: Duration = Duration::from_secs(90);
#[derive(Clone, Serialize)]
pub struct Transfer {
    pub id: Uuid,
    pub name: String,
    pub size: u64,
    pub offset: u64,
    pub phase: String,
    pub message: String,
    #[serde(skip)]
    cancelled: Arc<AtomicBool>,
}
impl Transfer {
    fn new(id: TransferId, name: String, size: u64, phase: &str) -> Self {
        Self {
            id: Uuid::from_bytes(id),
            name,
            size,
            offset: 0,
            phase: phase.into(),
            message: String::new(),
            cancelled: Arc::new(AtomicBool::new(false)),
        }
    }
    fn active(&self) -> bool {
        matches!(
            self.phase.as_str(),
            "offered" | "waiting" | "transferring" | "verifying" | "paused"
        )
    }
}
#[derive(Clone, Default, Serialize)]
pub struct View {
    pub connected: bool,
    pub incoming: Option<Transfer>,
    pub outgoing: Option<Transfer>,
    pub message: String,
}
struct Inner {
    id: Uuid,
    cancel: Arc<AtomicBool>,
    authorization: Authorization,
    binding: ([u8; 32], [u8; 32], bool),
    commands: SyncSender<Command>,
    view: Mutex<View>,
    epoch: AtomicU64,
}
#[derive(Clone)]
pub struct FileScope(Arc<Inner>);
struct Entry {
    scope: FileScope,
    thread: JoinHandle<()>,
}
#[derive(Clone, Default)]
pub struct FileHub(Arc<Mutex<Vec<Entry>>>);
impl Drop for FileHub {
    fn drop(&mut self) {
        if Arc::strong_count(&self.0) == 1 {
            self.revoke();
        }
    }
}
impl FileHub {
    pub fn activate(
        &self,
        id: Uuid,
        cancel: Arc<AtomicBool>,
        authorization: Authorization,
        proof: &VerifiedSession,
    ) -> Result<FileScope, String> {
        if cancel.load(Ordering::Acquire) || !authorization() {
            return Err("File permission has expired".into());
        }
        let identities = proof.identities();
        let binding = (identities.0, identities.1, proof.local_role() == Role::Host);
        let mut entries = self
            .0
            .lock()
            .map_err(|_| "File owner registry is unavailable")?;
        let mut index = 0;
        while index < entries.len() {
            if entries[index].thread.is_finished() {
                entries
                    .swap_remove(index)
                    .thread
                    .join()
                    .map_err(|_| "A file owner failed during cleanup")?;
            } else {
                index += 1;
            }
        }
        if let Some(entry) = entries.iter().find(|entry| entry.scope.0.id == id) {
            if entry.scope.0.binding != binding || entry.scope.0.cancel.load(Ordering::Acquire) {
                return Err("The accepted file peer changed or stopped".into());
            }
            return Ok(entry.scope.clone());
        }
        if entries.len() >= 2 {
            return Err("Previous file cleanup has not completed".into());
        }
        let (tx, rx) = mpsc::sync_channel(32);
        let scope = FileScope(Arc::new(Inner {
            id,
            cancel,
            authorization,
            binding,
            commands: tx,
            view: Mutex::new(View::default()),
            epoch: AtomicU64::new(0),
        }));
        let owner = scope.clone();
        let thread = std::thread::Builder::new()
            .name("mixel-file-owner".into())
            .spawn(move || {
                let mut worker = Worker::new(owner);
                let result =
                    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| worker.run(rx)));
                if result.is_err() {
                    worker.scope.0.cancel.store(true, Ordering::Release);
                    worker
                        .scope
                        .transport_error("The file owner failed and revoked its permissions");
                }
                worker.cleanup_all();
            })
            .map_err(|_| "Cannot start the file owner")?;
        entries.push(Entry {
            scope: scope.clone(),
            thread,
        });
        Ok(scope)
    }
    pub fn get(&self, id: Uuid) -> Result<FileScope, String> {
        self.0
            .lock()
            .map_err(|_| "File owner registry is unavailable")?
            .iter()
            .find(|entry| entry.scope.0.id == id && !entry.thread.is_finished())
            .map(|entry| entry.scope.clone())
            .ok_or("File permission is not active".into())
    }
    pub fn revoke(&self) {
        if let Ok(entries) = self.0.lock() {
            for entry in entries.iter() {
                entry.scope.0.cancel.store(true, Ordering::Release);
            }
        }
    }
    pub async fn finish(&self) -> Result<(), String> {
        self.revoke();
        let hub = self.clone();
        tokio::task::spawn_blocking(move || {
            let deadline = Instant::now() + Duration::from_secs(25);
            loop {
                let mut entries = hub
                    .0
                    .lock()
                    .map_err(|_| "File owner registry is unavailable")?;
                let mut index = 0;
                while index < entries.len() {
                    if entries[index].thread.is_finished() {
                        entries
                            .swap_remove(index)
                            .thread
                            .join()
                            .map_err(|_| "A file owner failed during cleanup")?;
                    } else {
                        index += 1;
                    }
                }
                if entries.is_empty() {
                    return Ok(());
                }
                if Instant::now() >= deadline {
                    return Err("File cleanup is still pending; quitting was postponed".into());
                }
                drop(entries);
                std::thread::sleep(Duration::from_millis(20));
            }
        })
        .await
        .map_err(|_| "File cleanup owner failed".to_owned())?
    }
}
impl FileScope {
    pub fn live(&self) -> bool {
        !self.0.cancel.load(Ordering::Acquire) && (self.0.authorization)()
    }
    fn require_live(&self) -> Result<(), String> {
        if self.live() {
            Ok(())
        } else {
            Err("File permission has expired".into())
        }
    }
    pub fn transport_error(&self, error: &str) {
        self.update(|view| {
            view.connected = false;
            view.message = format!("{error}. Renew the connection to resume file transfer.");
        });
    }
    pub fn view(&self) -> Result<View, String> {
        self.require_live()?;
        Ok(self
            .0
            .view
            .lock()
            .map_err(|_| "File status is unavailable")?
            .clone())
    }
    fn update(&self, update: impl FnOnce(&mut View)) {
        if let Ok(mut view) = self.0.view.lock() {
            update(&mut view);
        }
    }
    fn send(&self, command: Command) -> Result<(), String> {
        self.require_live()?;
        self.0
            .commands
            .try_send(command)
            .map_err(|_| "The file owner is busy or closed".into())
    }
    pub fn can_offer(&self) -> Result<(), String> {
        let view = self.view()?;
        if !view.connected {
            return Err("Wait for the encrypted file channel".into());
        }
        if view.outgoing.as_ref().is_some_and(Transfer::active) {
            return Err("One outgoing file can be transferred at a time".into());
        }
        Ok(())
    }
    pub async fn offer(&self, path: PathBuf) -> Result<(), String> {
        let (tx, rx) = oneshot::channel();
        self.send(Command::Offer { path, reply: tx })?;
        rx.await.map_err(|_| "The file owner closed".to_owned())?
    }
    pub fn pending(&self, id: Uuid) -> Result<String, String> {
        self.require_live()?;
        self.0
            .view
            .lock()
            .map_err(|_| "File status is unavailable")?
            .incoming
            .as_ref()
            .filter(|item| {
                item.id == id && item.phase == "offered" && !item.cancelled.load(Ordering::Acquire)
            })
            .map(|item| item.name.clone())
            .ok_or("This offer is no longer waiting".into())
    }
    pub async fn accept(&self, id: Uuid, path: PathBuf) -> Result<(), String> {
        let (tx, rx) = oneshot::channel();
        self.send(Command::Accept {
            id: *id.as_bytes(),
            path,
            reply: tx,
        })?;
        rx.await.map_err(|_| "The file owner closed".to_owned())?
    }
    pub fn cancel(&self, id: Uuid) -> Result<(), String> {
        self.require_live()?;
        let view = self
            .0
            .view
            .lock()
            .map_err(|_| "File status is unavailable")?;
        let job = [view.incoming.as_ref(), view.outgoing.as_ref()]
            .into_iter()
            .flatten()
            .find(|item| item.id == id && item.active())
            .ok_or("This transfer is already finished")?;
        // Revocation precedes notification, even if the owner is flushing disk.
        job.cancelled.store(true, Ordering::Release);
        drop(view);
        self.send(Command::Cancel(*id.as_bytes()))
    }
    pub fn picker_live(&self, id: Option<Uuid>) -> bool {
        self.live() && id.is_none_or(|id| self.pending(id).is_ok())
    }
    pub fn attach(&self) -> Result<Attachment, String> {
        self.require_live()?;
        let epoch = loop {
            let current = self.0.epoch.load(Ordering::Acquire);
            let next = current
                .checked_add(1)
                .ok_or("File transport generation exhausted")?;
            if self
                .0
                .epoch
                .compare_exchange(current, next, Ordering::AcqRel, Ordering::Acquire)
                .is_ok()
            {
                break next;
            }
        };
        let (tx, rx) = async_queue::channel(16);
        self.send(Command::Attach { epoch, output: tx })?;
        Ok(Attachment {
            scope: self.clone(),
            epoch,
            output: rx,
        })
    }
    fn guard(&self, cancel: Arc<AtomicBool>) -> Authorization {
        let scope = self.clone();
        Arc::new(move || !cancel.load(Ordering::Acquire) && scope.live())
    }
}
pub struct Attachment {
    scope: FileScope,
    epoch: u64,
    pub output: async_queue::Receiver<Zeroizing<Vec<u8>>>,
}
impl Attachment {
    pub fn wire(&self, bytes: Zeroizing<Vec<u8>>) -> Result<(), String> {
        self.scope.send(Command::Wire {
            epoch: self.epoch,
            bytes,
        })
    }
    pub fn current(&self) -> bool {
        self.scope.live() && self.scope.0.epoch.load(Ordering::Acquire) == self.epoch
    }
}
impl Drop for Attachment {
    fn drop(&mut self) {
        // Queue pressure cannot leave a dropped transport reported connected.
        // A stale owner may revoke only its own epoch, never its replacement.
        if let Some(next) = self.epoch.checked_add(1) {
            let _ = self.scope.0.epoch.compare_exchange(
                self.epoch,
                next,
                Ordering::AcqRel,
                Ordering::Acquire,
            );
        } else {
            self.scope.0.cancel.store(true, Ordering::Release);
        }

        let _ = self.scope.0.commands.try_send(Command::Detach(self.epoch));
    }
}
enum Command {
    Attach {
        epoch: u64,
        output: async_queue::Sender<Zeroizing<Vec<u8>>>,
    },
    Detach(u64),
    Wire {
        epoch: u64,
        bytes: Zeroizing<Vec<u8>>,
    },
    Offer {
        path: PathBuf,
        reply: Reply,
    },
    Accept {
        id: TransferId,
        path: PathBuf,
        reply: Reply,
    },
    Cancel(TransferId),
}
struct Sending {
    item: Transfer,
    source: SourceFile,
    progress: Progress,
    checkpoints: BTreeMap<u64, Progress>,
    acked: u64,
    accepted: bool,
    finish: bool,
    started: Instant,
    last: Instant,
}
struct Receiving {
    item: Transfer,
    file: Option<IncomingFile>,
    started: Instant,
    last: Instant,
}
struct Receipt {
    id: TransferId,
    name: String,
    size: u64,
    digest: [u8; 32],
}
struct Worker {
    scope: FileScope,
    epoch: u64,
    output: Option<async_queue::Sender<Zeroizing<Vec<u8>>>>,
    sending: Option<Sending>,
    receiving: Option<Receiving>,
    receipts: VecDeque<Receipt>,
    cleanup: Vec<IncomingFile>,
}
impl Worker {
    fn new(scope: FileScope) -> Self {
        Self {
            scope,
            epoch: 0,
            output: None,
            sending: None,
            receiving: None,
            receipts: VecDeque::new(),
            cleanup: Vec::new(),
        }
    }
    fn run(&mut self, rx: mpsc::Receiver<Command>) {
        while self.scope.live() {
            match rx.recv_timeout(Duration::from_millis(10)) {
                Ok(command) => {
                    if let Err(error) = self.command(command) {
                        self.pause(&error);
                    }
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
                Err(mpsc::RecvTimeoutError::Timeout) => {}
            }
            if self
                .output
                .as_ref()
                .is_some_and(|output| output.is_closed())
                || (self.output.is_some()
                    && self.epoch != self.scope.0.epoch.load(Ordering::Acquire))
            {
                self.pause("File transport is reconnecting");
            }
            self.cancel_jobs();
            self.expire();
            if self.output.is_some()
                && let Err(error) = self.pump()
            {
                let id = self.sending.as_ref().map(|job| *job.item.id.as_bytes());
                self.fail_send(&error);
                if let Some(id) = id {
                    let _ = self.transmit(Message::Cancel { id });
                }
            }
            self.retry_cleanup();
            self.publish_view();
        }
    }
    fn cleanup_all(&mut self) {
        self.scope.0.cancel.store(true, Ordering::Release);
        self.scope.update(|view| {
            view.connected = false;
            view.message = "Session ended; cleaning up unpublished files".into();
        });
        self.output = None;
        if let Some(mut incoming) = self.receiving.take()
            && let Some(mut file) = incoming.file.take()
            && file.abort().is_err()
        {
            self.cleanup.push(file);
        }
        self.sending = None;
        // Keep ownership, and keep Quit waiting, if filesystem permissions or
        // availability prevent removing an unpublished private partial file.
        while !self.cleanup.is_empty() {
            self.retry_cleanup();
            std::thread::sleep(Duration::from_millis(500));
        }
        self.scope.update(|view| {
            view.incoming = None;
            view.outgoing = None;
            view.message = "File permissions and partial files released".into();
        });
    }
    fn transmit(&mut self, message: Message<'_>) -> Result<(), String> {
        if !self.scope.live() {
            return Err("File permission expired".into());
        }
        let bytes = message.encode().map_err(|error| error.to_string())?;
        self.output
            .as_ref()
            .ok_or("File connection is paused")?
            .try_send(bytes)
            .map_err(|_| "File transport is busy or closed".into())
    }
    fn pause(&mut self, error: &str) {
        self.output = None;
        if let Some(job) = &mut self.sending {
            job.accepted = false;
            if job.item.active() {
                job.item.phase = "paused".into();
            }
        }
        if let Some(job) = &mut self.receiving
            && job.file.is_some()
            && job.item.active()
        {
            job.item.phase = "paused".into();
        }
        self.scope.update(|view| {
            view.connected = false;
            view.message =
                format!("{error}. Renew the connection to resume the same accepted transfer.");
        });
    }
    fn publish_view(&self) {
        self.scope.update(|view| {
            view.connected = self.output.is_some();
            if let Some(job) = &self.sending {
                view.outgoing = Some(job.item.clone());
            }
            if let Some(job) = &self.receiving {
                view.incoming = Some(job.item.clone());
            }
        });
    }
    fn command(&mut self, command: Command) -> Result<(), String> {
        match command {
            Command::Attach { epoch, output } => {
                if epoch != self.scope.0.epoch.load(Ordering::Acquire) || epoch <= self.epoch {
                    return Ok(());
                }
                self.epoch = epoch;
                self.output = Some(output);
                self.scope
                    .update(|view| view.message = "Encrypted file channel connected".into());
                let offer = self
                    .sending
                    .as_mut()
                    .filter(|job| job.item.active())
                    .map(|job| {
                        job.accepted = false;
                        job.finish = false;
                        job.item.phase = "waiting".into();
                        job.last = Instant::now();
                        (
                            *job.item.id.as_bytes(),
                            job.item.name.clone(),
                            job.item.size,
                        )
                    });
                if let Some((id, name, size)) = offer {
                    self.transmit(Message::Offer {
                        id,
                        name: &name,
                        size,
                    })?;
                }
            }
            Command::Detach(epoch) => {
                if epoch == self.epoch {
                    self.pause("File transport is reconnecting");
                }
            }
            Command::Wire { epoch, bytes } => {
                if epoch == self.epoch && self.output.is_some() {
                    self.message(Message::decode(&bytes).map_err(|error| error.to_string())?)?;
                }
            }
            Command::Offer { path, reply } => {
                let result = self.offer(path);
                let _ = reply.send(result);
            }
            Command::Accept { id, path, reply } => {
                let result = self.accept(id, path);
                let _ = reply.send(result);
            }
            Command::Cancel(id) => {
                if self
                    .sending
                    .as_ref()
                    .is_some_and(|job| *job.item.id.as_bytes() == id && job.item.active())
                {
                    self.fail_send("Cancelled locally");
                }
                if self
                    .receiving
                    .as_ref()
                    .is_some_and(|job| *job.item.id.as_bytes() == id && job.item.active())
                {
                    self.fail_receive("Declined or cancelled locally");
                }
                if self.output.is_some() {
                    self.transmit(Message::Cancel { id })?;
                }
            }
        }
        Ok(())
    }
    fn offer(&mut self, path: PathBuf) -> Result<(), String> {
        self.scope.require_live()?;
        if self.output.is_none() {
            return Err("Wait for the encrypted file channel".into());
        }
        if self.sending.as_ref().is_some_and(|job| job.item.active()) {
            return Err("One outgoing file can be transferred at a time".into());
        }
        let source =
            SourceFile::open(&path, &self.scope.0.cancel).map_err(|error| error.to_string())?;
        let id = *Uuid::new_v4().as_bytes();
        let item = Transfer::new(id, source.name().into(), source.size(), "waiting");
        let source = source.with_authorization(self.scope.guard(item.cancelled.clone()));
        let progress =
            Progress::new(id, item.size, Instant::now()).map_err(|error| error.to_string())?;
        let mut checkpoints = BTreeMap::new();
        checkpoints.insert(0, progress.clone());
        let name = item.name.clone();
        let size = item.size;
        self.sending = Some(Sending {
            item,
            source,
            progress,
            checkpoints,
            acked: 0,
            accepted: false,
            finish: false,
            started: Instant::now(),
            last: Instant::now(),
        });
        if let Err(error) = self.transmit(Message::Offer {
            id,
            name: &name,
            size,
        }) {
            self.pause(&error);
        }
        Ok(())
    }
    fn accept(&mut self, id: TransferId, path: PathBuf) -> Result<(), String> {
        self.scope.require_live()?;
        if !self.cleanup.is_empty() {
            return Err("Previous partial-file cleanup must finish first".into());
        }
        let job = self
            .receiving
            .as_mut()
            .filter(|job| {
                *job.item.id.as_bytes() == id
                    && job.item.phase == "offered"
                    && !job.item.cancelled.load(Ordering::Acquire)
            })
            .ok_or("This file offer is no longer waiting")?;
        if job.started.elapsed() >= OFFER_LIFETIME {
            return Err("The file offer expired".into());
        }
        if self.output.is_none() {
            return Err("Wait for the encrypted file channel".into());
        }
        let file = IncomingFile::create(
            &path,
            id,
            job.item.size,
            Instant::now(),
            &job.item.cancelled,
        )
        .map_err(|error| error.to_string())?
        .with_authorization(self.scope.guard(job.item.cancelled.clone()));
        if !self.scope.live() || job.item.cancelled.load(Ordering::Acquire) {
            let mut file = file;
            if file.abort().is_err() {
                self.cleanup.push(file);
            }
            return Err("File permission expired during selection".into());
        }
        let prefix_digest = file.prefix_digest();
        job.file = Some(file);
        job.item.phase = "transferring".into();
        job.last = Instant::now();
        self.transmit(Message::Accept {
            id,
            offset: 0,
            prefix_digest,
        })
    }
    fn message(&mut self, message: Message<'_>) -> Result<(), String> {
        let now = Instant::now();
        match message {
            Message::Offer { id, name, size } => {
                if !self.cleanup.is_empty()
                    || self
                        .sending
                        .as_ref()
                        .is_some_and(|job| *job.item.id.as_bytes() == id)
                {
                    return self.transmit(Message::Reject {
                        id,
                        reason: Rejection::Declined,
                    });
                }
                if let Some(receipt) = self.receipts.iter().find(|receipt| receipt.id == id) {
                    if receipt.name != name || receipt.size != size {
                        return Err("Changed completed file offer".into());
                    }
                    return self.transmit(Message::Complete {
                        id,
                        digest: receipt.digest,
                    });
                }
                if let Some(job) = &mut self.receiving
                    && job.item.active()
                {
                    if *job.item.id.as_bytes() != id {
                        return self.transmit(Message::Reject {
                            id,
                            reason: Rejection::Declined,
                        });
                    }
                    if job.item.name != name || job.item.size != size {
                        return Err("Changed resumed file metadata".into());
                    }
                    if let Some(file) = &mut job.file {
                        file.resume(now, &job.item.cancelled)
                            .map_err(|error| error.to_string())?;
                        let offset = file.offset();
                        let prefix_digest = file.prefix_digest();
                        job.item.phase = "transferring".into();
                        job.last = now;
                        return self.transmit(Message::Accept {
                            id,
                            offset,
                            prefix_digest,
                        });
                    }
                    return Ok(());
                }
                self.receiving = Some(Receiving {
                    item: Transfer::new(id, name.into(), size, "offered"),
                    file: None,
                    started: now,
                    last: now,
                });
            }
            Message::Accept {
                id,
                offset,
                prefix_digest,
            } => {
                let job = self
                    .sending
                    .as_mut()
                    .filter(|job| *job.item.id.as_bytes() == id && job.item.active())
                    .ok_or("Unexpected file acceptance")?;
                if job.accepted || offset < job.acked {
                    return Err("Invalid file resume offset".into());
                }
                let mut checkpoint = job
                    .checkpoints
                    .get(&offset)
                    .filter(|progress| progress.prefix_digest() == prefix_digest)
                    .cloned()
                    .ok_or("File resume prefix does not match transmitted bytes")?;
                job.source.unchanged().map_err(|error| error.to_string())?;
                checkpoint.resumed(now).map_err(|error| error.to_string())?;
                job.checkpoints.clear();
                job.checkpoints.insert(offset, checkpoint.clone());
                job.progress = checkpoint;
                job.acked = offset;
                job.accepted = true;
                job.finish = false;
                job.last = now;
                job.item.phase = "transferring".into();
                job.item.offset = offset;
            }
            Message::Data { id, offset, bytes } => {
                let job = self
                    .receiving
                    .as_mut()
                    .filter(|job| *job.item.id.as_bytes() == id && job.item.active())
                    .ok_or("Unexpected incoming file bytes")?;
                let file = job
                    .file
                    .as_mut()
                    .ok_or("File destination has not been accepted locally")?;
                if let Err(error) = file.write_chunk(id, offset, bytes, now, &job.item.cancelled) {
                    let error = error.to_string();
                    self.fail_receive(&error);
                    return self.transmit(Message::Reject {
                        id,
                        reason: Rejection::Storage,
                    });
                }
                let offset = file.offset();
                job.item.offset = offset;
                job.last = now;
                self.transmit(Message::Ack { id, offset })?;
            }
            Message::Ack { id, offset } => {
                let job = self
                    .sending
                    .as_mut()
                    .filter(|job| {
                        *job.item.id.as_bytes() == id && job.item.active() && job.accepted
                    })
                    .ok_or("Unexpected file acknowledgment")?;
                if offset < job.acked || !job.checkpoints.contains_key(&offset) {
                    return Err("File acknowledgment is outside the transmitted window".into());
                }
                job.acked = offset;
                job.item.offset = offset;
                job.last = now;
                job.checkpoints.retain(|&key, _| key >= offset);
            }
            Message::Finish { id, digest } => {
                let job = self
                    .receiving
                    .as_mut()
                    .filter(|job| *job.item.id.as_bytes() == id && job.item.active())
                    .ok_or("Unexpected file completion")?;
                if job.file.is_none() {
                    return Err("A destination has not been accepted locally".into());
                }
                job.item.phase = "verifying".into();
                self.publish_view();
                let job = self.receiving.as_mut().ok_or("File owner disappeared")?;
                let result = job
                    .file
                    .as_mut()
                    .ok_or("File destination disappeared")?
                    .publish(id, digest, now, &job.item.cancelled);
                match result {
                    Ok(()) => {
                        job.item.phase = "complete".into();
                        job.item.offset = job.item.size;
                        job.item.message =
                            "Saved locally; SHA-256 and actual file bytes verified".into();
                        job.file = None;
                        self.receipts.push_back(Receipt {
                            id,
                            name: job.item.name.clone(),
                            size: job.item.size,
                            digest,
                        });
                        while self.receipts.len() > 16 {
                            self.receipts.pop_front();
                        }
                        self.transmit(Message::Complete { id, digest })?;
                    }
                    Err(error) => {
                        let error = error.to_string();
                        self.fail_receive(&error);
                        self.transmit(Message::Reject {
                            id,
                            reason: Rejection::Storage,
                        })?;
                    }
                }
            }
            Message::Complete { id, digest } => {
                let job = self
                    .sending
                    .as_mut()
                    .filter(|job| *job.item.id.as_bytes() == id && job.item.active())
                    .ok_or("Unexpected completed transfer receipt")?;
                if job.progress.offset() != job.item.size || job.progress.prefix_digest() != digest
                {
                    return Err("The received-file digest does not match".into());
                }
                job.source.unchanged().map_err(|error| error.to_string())?;
                job.item.phase = "complete".into();
                job.item.offset = job.item.size;
                job.item.message = "Remote peer confirmed a saved file and matching SHA-256".into();
                job.accepted = false;
            }
            Message::Reject { id, .. } | Message::Cancel { id } => {
                if self
                    .sending
                    .as_ref()
                    .is_some_and(|job| *job.item.id.as_bytes() == id && job.item.active())
                {
                    self.fail_send("Remote peer declined or cancelled this transfer");
                }
                if self
                    .receiving
                    .as_ref()
                    .is_some_and(|job| *job.item.id.as_bytes() == id && job.item.active())
                {
                    self.fail_receive("Remote peer cancelled this transfer");
                }
            }
        }
        Ok(())
    }
    fn pump(&mut self) -> Result<(), String> {
        for _ in 0..WINDOW {
            let Some(job) = self
                .sending
                .as_mut()
                .filter(|job| job.accepted && job.item.active())
            else {
                return Ok(());
            };
            if job.item.cancelled.load(Ordering::Acquire) {
                return Err("Cancelled locally".into());
            }
            let id = *job.item.id.as_bytes();
            let offset = job.progress.offset();
            if offset == job.item.size {
                if !job.finish && job.acked == offset {
                    let digest = job.progress.prefix_digest();
                    job.source.unchanged().map_err(|error| error.to_string())?;
                    job.finish = true;
                    job.item.phase = "verifying".into();
                    job.last = Instant::now();
                    self.transmit(Message::Finish { id, digest })?;
                }
                return Ok(());
            }
            if job.checkpoints.len() > WINDOW {
                return Ok(());
            }
            let bytes = job
                .source
                .read_chunk(offset, &job.item.cancelled)
                .map_err(|error| error.to_string())?;
            job.progress
                .push(id, offset, &bytes, Instant::now())
                .map_err(|error| error.to_string())?;
            job.checkpoints
                .insert(job.progress.offset(), job.progress.clone());
            if let Err(error) = self.transmit(Message::Data {
                id,
                offset,
                bytes: &bytes,
            }) {
                self.pause(&error);
                return Ok(());
            }
        }
        Ok(())
    }
    fn fail_send(&mut self, error: &str) {
        if let Some(job) = &mut self.sending {
            job.item.cancelled.store(true, Ordering::Release);
            job.item.phase = "failed".into();
            job.item.message = error.into();
            job.accepted = false;
        }
    }
    fn fail_receive(&mut self, error: &str) {
        if let Some(job) = &mut self.receiving {
            job.item.cancelled.store(true, Ordering::Release);
            job.item.phase = "failed".into();
            job.item.message = error.into();
            if let Some(mut file) = job.file.take()
                && file.abort().is_err()
            {
                self.cleanup.push(file);
            }
        }
    }
    fn cancel_jobs(&mut self) {
        let send = self
            .sending
            .as_ref()
            .filter(|job| job.item.active() && job.item.cancelled.load(Ordering::Acquire))
            .map(|job| *job.item.id.as_bytes());
        let receive = self
            .receiving
            .as_ref()
            .filter(|job| job.item.active() && job.item.cancelled.load(Ordering::Acquire))
            .map(|job| *job.item.id.as_bytes());
        if let Some(id) = send {
            self.fail_send("Cancelled locally");
            let _ = self.transmit(Message::Cancel { id });
        }
        if let Some(id) = receive {
            self.fail_receive("Declined or cancelled locally");
            let _ = self.transmit(Message::Cancel { id });
        }
    }
    fn expire(&mut self) {
        let send = self
            .sending
            .as_ref()
            .filter(|job| job.item.active())
            .is_some_and(|job| {
                job.started.elapsed() >= files::LIFETIME
                    || (self.output.is_some()
                        && job.last.elapsed()
                            >= if job.finish {
                                Duration::from_secs(1800)
                            } else if job.accepted {
                                files::IDLE
                            } else {
                                OFFER_LIFETIME
                            })
            });
        let receive = self
            .receiving
            .as_ref()
            .filter(|job| job.item.active())
            .is_some_and(|job| {
                job.started.elapsed() >= files::LIFETIME
                    || (job.file.is_none() && job.started.elapsed() >= OFFER_LIFETIME)
                    || (self.output.is_some()
                        && job.file.is_some()
                        && job.last.elapsed() >= files::IDLE)
            });
        if send {
            let id = self.sending.as_ref().map(|job| *job.item.id.as_bytes());
            self.fail_send("The file transfer deadline expired");
            if let Some(id) = id {
                let _ = self.transmit(Message::Cancel { id });
            }
        }
        if receive {
            let id = self.receiving.as_ref().map(|job| *job.item.id.as_bytes());
            self.fail_receive("The file transfer deadline expired");
            if let Some(id) = id {
                let _ = self.transmit(Message::Reject {
                    id,
                    reason: Rejection::Expired,
                });
            }
        }
    }
    fn retry_cleanup(&mut self) {
        let mut index = 0;
        while index < self.cleanup.len() {
            if self.cleanup[index].abort().is_ok() {
                self.cleanup.swap_remove(index);
            } else {
                index += 1;
            }
        }
        if !self.cleanup.is_empty() {
            self.scope.update(|view| {
                view.message =
                    "Private partial-file cleanup is pending; checking local storage again".into()
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use core_protocol::crypto::{Context, Handshake};
    use ed25519_dalek::SigningKey;
    use rand_core::OsRng;
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../../../_scratch")
                .join(format!("file-owner-fixture-{}", Uuid::new_v4()));
            std::fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            std::fs::remove_dir_all(&self.0).unwrap();
        }
    }
    fn proofs() -> (VerifiedSession, VerifiedSession) {
        let controller = SigningKey::generate(&mut OsRng);
        let host = SigningKey::generate(&mut OsRng);
        let context = Context {
            session_id: [45; 32],
            controller: controller.verifying_key(),
            host: host.verifying_key(),
        };
        let ch = Handshake::new(context.clone(), Role::Controller, &controller, [6; 32]).unwrap();
        let hh = Handshake::new(context, Role::Host, &host, [7; 32]).unwrap();
        let cp = ch.hello();
        let hp = hh.hello();
        (
            ch.finish(&hp, [7; 32]).unwrap().verified_session(),
            hh.finish(&cp, [6; 32]).unwrap().verified_session(),
        )
    }
    fn scopes() -> (FileHub, FileScope, FileHub, FileScope) {
        let (c, h) = proofs();
        let cid = Uuid::new_v4();
        let ch = FileHub::default();
        let hh = FileHub::default();
        let cs = ch
            .activate(cid, Arc::new(AtomicBool::new(false)), Arc::new(|| true), &c)
            .unwrap();
        let hs = hh
            .activate(cid, Arc::new(AtomicBool::new(false)), Arc::new(|| true), &h)
            .unwrap();
        (ch, cs, hh, hs)
    }
    async fn packet(attachment: &mut Attachment) -> Zeroizing<Vec<u8>> {
        tokio::time::timeout(Duration::from_secs(5), attachment.output.recv())
            .await
            .unwrap()
            .unwrap()
    }
    async fn ready(scope: &FileScope) {
        tokio::time::timeout(Duration::from_secs(5), async {
            while !scope.view().unwrap().connected {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
    }
    async fn pending(scope: &FileScope, id: Uuid) {
        tokio::time::timeout(Duration::from_secs(5), async {
            while scope.pending(id).is_err() {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
    }
    #[tokio::test]
    async fn actual_file_bytes_resume_from_written_prefix_after_lost_ack_and_ignore_retired_transport()
     {
        let fixture = Fixture::new();
        let source = fixture.0.join("source.bin");
        let destination = fixture.0.join("received.bin");
        let expected: Vec<u8> = (0..41_003).map(|index| (index % 251) as u8).collect();
        std::fs::write(&source, &expected).unwrap();
        let (ch, cs, hh, hs) = scopes();
        let mut ca = cs.attach().unwrap();
        let mut ha = hs.attach().unwrap();
        ready(&cs).await;
        ready(&hs).await;
        cs.offer(source).await.unwrap();
        let offer = packet(&mut ca).await;
        let id = Uuid::from_bytes(Message::decode(&offer).unwrap().id());
        ha.wire(offer).unwrap();
        pending(&hs, id).await;
        assert!(!destination.exists());
        hs.accept(id, destination.clone()).await.unwrap();
        ca.wire(packet(&mut ha).await).unwrap();
        // Two chunks reach disk, only the first ACK reaches the sender.
        ha.wire(packet(&mut ca).await).unwrap();
        let first_ack = packet(&mut ha).await;
        ca.wire(first_ack).unwrap();
        ha.wire(packet(&mut ca).await).unwrap();
        let lost_ack = packet(&mut ha).await;
        assert!(matches!(
            Message::decode(&lost_ack).unwrap(),
            Message::Ack { offset: 6000, .. }
        ));
        // New cryptographic transport keeps this local consent and actual FDs.
        let mut next_c = cs.attach().unwrap();
        let mut next_h = hs.attach().unwrap();
        ca.wire(Message::Cancel { id: *id.as_bytes() }.encode().unwrap())
            .unwrap();
        drop(ca);
        drop(ha);
        let offer = packet(&mut next_c).await;
        next_h.wire(offer).unwrap();
        let resumed = packet(&mut next_h).await;
        assert!(matches!(
            Message::decode(&resumed).unwrap(),
            Message::Accept { offset: 6000, .. }
        ));
        next_c.wire(resumed).unwrap();
        tokio::time::timeout(Duration::from_secs(8), async {
            loop {
                let sent = cs.view().unwrap();
                let received = hs.view().unwrap();
                for transfer in [sent.outgoing.as_ref(), received.incoming.as_ref()].into_iter().flatten() {
                    assert_ne!(transfer.phase, "failed", "{}", transfer.message);
                }
                if sent.outgoing.is_some_and(|job| job.phase == "complete") {
                    break;
                }
                tokio::select! {
                    Some(bytes)=next_c.output.recv()=>next_h.wire(bytes).unwrap(),
                    Some(bytes)=next_h.output.recv()=>next_c.wire(bytes).unwrap(),
                    _=tokio::time::sleep(Duration::from_millis(5))=>{},
                }
            }
        })
        .await
        .unwrap();
        assert_eq!(std::fs::read(&destination).unwrap(), expected);
        assert_eq!(hs.view().unwrap().incoming.unwrap().phase, "complete");
        assert!(std::fs::read_dir(&fixture.0).unwrap().all(|entry| {
            !entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .ends_with(".part")
        }));
        // A lost final receipt is idempotent; it cannot rewrite a saved document.
        let last_c = cs.attach().unwrap();
        let mut last_h = hs.attach().unwrap();
        drop(next_c);
        drop(next_h);
        last_h
            .wire(
                Message::Offer {
                    id: *id.as_bytes(),
                    name: "source.bin",
                    size: expected.len() as u64,
                }
                .encode()
                .unwrap(),
            )
            .unwrap();
        assert!(matches!(
            Message::decode(&packet(&mut last_h).await).unwrap(),
            Message::Complete { .. }
        ));
        assert_eq!(std::fs::read(&destination).unwrap(), expected);
        drop(last_c);
        drop(last_h);
        ch.finish().await.unwrap();
        hh.finish().await.unwrap();
    }
    #[tokio::test]
    async fn rejecting_received_partial_removes_only_private_file_and_retains_existing_documents() {
        let fixture = Fixture::new();
        let source = fixture.0.join("source.dat");
        let destination = fixture.0.join("received.dat");
        let preserved = fixture.0.join("existing.txt");
        std::fs::write(&source, vec![4u8; 20_000]).unwrap();
        std::fs::write(&preserved, b"preserve me").unwrap();
        let (ch, cs, hh, hs) = scopes();
        let mut ca = cs.attach().unwrap();
        let mut ha = hs.attach().unwrap();
        ready(&cs).await;
        ready(&hs).await;
        cs.offer(source).await.unwrap();
        let offer = packet(&mut ca).await;
        let id = Uuid::from_bytes(Message::decode(&offer).unwrap().id());
        ha.wire(offer).unwrap();
        pending(&hs, id).await;
        assert!(hs.accept(id, preserved.clone()).await.is_err());
        assert_eq!(std::fs::read(&preserved).unwrap(), b"preserve me");
        hs.accept(id, destination.clone()).await.unwrap();
        ca.wire(packet(&mut ha).await).unwrap();
        ha.wire(packet(&mut ca).await).unwrap();
        let _ = packet(&mut ha).await;
        hs.cancel(id).unwrap();
        tokio::time::timeout(Duration::from_secs(5), async {
            while hs
                .view()
                .unwrap()
                .incoming
                .as_ref()
                .is_none_or(|job| job.phase != "failed")
            {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
        assert!(!destination.exists());
        drop(ca);
        drop(ha);
        ch.finish().await.unwrap();
        hh.finish().await.unwrap();
        assert_eq!(std::fs::read(&preserved).unwrap(), b"preserve me");
        assert!(std::fs::read_dir(&fixture.0).unwrap().all(|entry| {
            !entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .ends_with(".part")
        }));
    }
    #[tokio::test]
    async fn changed_peer_cannot_reattach_existing_consent_or_adopt_forged_resume_hash() {
        let fixture = Fixture::new();
        let path = fixture.0.join("data.bin");
        std::fs::write(&path, b"Mixel file fixture").unwrap();
        let (ch, cs, hh, hs) = scopes();
        let (_, foreign) = proofs();
        assert!(
            ch.activate(cs.0.id, cs.0.cancel.clone(), Arc::new(|| true), &foreign)
                .is_err()
        );
        let mut attachment = cs.attach().unwrap();
        ready(&cs).await;
        cs.offer(path).await.unwrap();
        let offer = packet(&mut attachment).await;
        let id = Message::decode(&offer).unwrap().id();
        attachment
            .wire(
                Message::Accept {
                    id,
                    offset: 0,
                    prefix_digest: [99; 32],
                }
                .encode()
                .unwrap(),
            )
            .unwrap();
        tokio::time::timeout(Duration::from_secs(5), async {
            while cs.view().unwrap().connected {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
        assert_eq!(cs.view().unwrap().outgoing.unwrap().offset, 0);
        assert!(hs.view().unwrap().incoming.is_none());
        drop(attachment);
        ch.finish().await.unwrap();
        hh.finish().await.unwrap();
    }
}
