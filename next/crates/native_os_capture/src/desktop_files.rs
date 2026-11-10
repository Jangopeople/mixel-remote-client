//! Capability-owned files. Paths come only from a local OS picker; a remote
//! filename never opens a source or selects a destination. Partial files stay
//! unpublished, and publication cannot replace an existing directory entry.
use core_protocol::files::{self, Progress, TransferId};
use std::{
    fs::File,
    io,
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Instant,
};
#[cfg(test)]
use uuid::Uuid;
use zeroize::Zeroizing;

#[cfg_attr(target_os = "windows", path = "windows_pending_file.rs")]
#[cfg_attr(not(target_os = "windows"), path = "unix_pending_file.rs")]
mod platform;
use platform::{Pending, Stamp, open_source, read_at, stamp, write_all_at};

#[derive(Debug, thiserror::Error)]
pub enum FileError {
    #[error("The file operation was cancelled")]
    Cancelled,
    #[error("Choose a regular file with a supported name, up to 16 GiB")]
    Source,
    #[error("The selected file changed during transfer")]
    Changed,
    #[error("Choose a new destination filename; existing files are preserved")]
    Destination,
    #[error("The private partial file changed or is no longer available")]
    Partial,
    #[error(transparent)]
    Protocol(#[from] files::FileError),
    #[error("Local file storage failed: {0}")]
    Storage(#[from] io::Error),
}
fn live(cancelled: &AtomicBool) -> Result<(), FileError> {
    if cancelled.load(Ordering::Acquire) {
        Err(FileError::Cancelled)
    } else {
        Ok(())
    }
}
pub struct SourceFile {
    file: File,
    initial: Stamp,
    name: String,
    size: u64,
    authorization: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
}
impl SourceFile {
    pub fn with_authorization(
        mut self,
        authorization: Arc<dyn Fn() -> bool + Send + Sync>,
    ) -> Self {
        self.authorization = Some(authorization);
        self
    }
    fn live(&self, cancelled: &AtomicBool) -> Result<(), FileError> {
        live(cancelled)?;
        if self
            .authorization
            .as_ref()
            .is_some_and(|allowed| !allowed())
        {
            Err(FileError::Cancelled)
        } else {
            Ok(())
        }
    }
    pub fn name(&self) -> &str {
        &self.name
    }
    pub fn size(&self) -> u64 {
        self.size
    }
    pub fn open(path: &Path, cancelled: &AtomicBool) -> Result<Self, FileError> {
        live(cancelled)?;
        let name = path
            .file_name()
            .and_then(|value| value.to_str())
            .filter(|value| files::valid_name(value))
            .ok_or(FileError::Source)?
            .to_owned();
        let file = open_source(path)?;
        if !file.metadata()?.is_file() {
            return Err(FileError::Source);
        }
        let initial = stamp(&file)?;
        if initial.size > files::MAX_FILE_SIZE {
            return Err(FileError::Source);
        }
        live(cancelled)?;
        let size = initial.size;
        Ok(Self {
            file,
            initial,
            name,
            size,
            authorization: None,
        })
    }
    pub fn unchanged(&self) -> Result<(), FileError> {
        if stamp(&self.file)? == self.initial {
            Ok(())
        } else {
            Err(FileError::Changed)
        }
    }
    pub fn read_chunk(
        &self,
        offset: u64,
        cancelled: &AtomicBool,
    ) -> Result<Zeroizing<Vec<u8>>, FileError> {
        self.live(cancelled)?;
        self.unchanged()?;
        if offset > self.size {
            return Err(FileError::Source);
        }
        let count = (self.size - offset).min(files::CHUNK as u64) as usize;
        let mut bytes = Zeroizing::new(vec![0; count]);
        let mut read = 0;
        while read < count {
            self.live(cancelled)?;
            let next = read_at(&self.file, &mut bytes[read..], offset + read as u64)?;
            if next == 0 {
                return Err(FileError::Changed);
            }
            read += next;
        }
        self.unchanged()?;
        self.live(cancelled)?;
        Ok(bytes)
    }
}
pub struct IncomingFile {
    pending: Pending,
    progress: Progress,
    id: TransferId,
    failed: bool,
    published: bool,
    removed: bool,
    authorization: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
}
impl IncomingFile {
    pub fn with_authorization(
        mut self,
        authorization: Arc<dyn Fn() -> bool + Send + Sync>,
    ) -> Self {
        self.authorization = Some(authorization);
        self
    }
    fn live(&self, cancelled: &AtomicBool) -> Result<(), FileError> {
        live(cancelled)?;
        if self
            .authorization
            .as_ref()
            .is_some_and(|allowed| !allowed())
        {
            Err(FileError::Cancelled)
        } else {
            Ok(())
        }
    }
    pub fn create(
        path: &Path,
        id: TransferId,
        size: u64,
        now: Instant,
        cancelled: &AtomicBool,
    ) -> Result<Self, FileError> {
        live(cancelled)?;
        let name = path
            .file_name()
            .and_then(|value| value.to_str())
            .filter(|value| files::valid_name(value))
            .ok_or(FileError::Destination)?;
        let progress = Progress::new(id, size, now)?;
        let pending = Pending::create(path, name)?;
        let result = Self {
            pending,
            progress,
            id,
            failed: false,
            published: false,
            removed: false,
            authorization: None,
        };
        // Return the capability even if cancellation raced creation, so its
        // owner can explicitly retain/retry a failed private-file cleanup.
        Ok(result)
    }
    pub fn offset(&self) -> u64 {
        self.progress.offset()
    }
    /// Explicit cleanup reports storage failures so its native owner can retain
    /// the capability and retry, rather than silently claiming cancellation.
    /// Published user documents are never removed by transfer cleanup.
    pub fn abort(&mut self) -> Result<(), FileError> {
        if self.published || self.removed {
            return Ok(());
        }
        self.failed = true;
        self.pending.abort()?;
        self.removed = true;
        Ok(())
    }
    pub fn prefix_digest(&self) -> [u8; 32] {
        self.progress.prefix_digest()
    }
    pub fn resume(&mut self, now: Instant, cancelled: &AtomicBool) -> Result<(), FileError> {
        self.live(cancelled)?;
        if self.failed || self.published || !self.pending.still_owned()? {
            return Err(FileError::Partial);
        }
        self.progress.resumed(now)?;
        Ok(())
    }
    pub fn write_chunk(
        &mut self,
        id: TransferId,
        offset: u64,
        bytes: &[u8],
        now: Instant,
        cancelled: &AtomicBool,
    ) -> Result<(), FileError> {
        self.live(cancelled)?;
        if self.failed || self.published {
            return Err(FileError::Partial);
        }
        self.progress.push(id, offset, bytes, now)?;
        if let Err(error) = write_all_at(&self.pending.file, bytes, offset) {
            self.failed = true;
            return Err(error.into());
        }
        self.live(cancelled)?;
        Ok(())
    }
    pub fn publish(
        &mut self,
        id: TransferId,
        digest: [u8; 32],
        now: Instant,
        cancelled: &AtomicBool,
    ) -> Result<(), FileError> {
        self.live(cancelled)?;
        if self.failed || self.published || id != self.id || !self.pending.still_owned()? {
            return Err(FileError::Partial);
        }
        self.progress.complete(id, digest, now)?;
        self.pending.file.sync_all()?;
        self.verify_storage(digest, cancelled)?;
        self.live(cancelled)?;
        self.pending.publish()?;
        self.published = true;
        Ok(())
    }
    fn verify_storage(&self, digest: [u8; 32], cancelled: &AtomicBool) -> Result<(), FileError> {
        let size = self.progress.offset();
        if self.pending.file.metadata()?.len() != size {
            return Err(FileError::Partial);
        }
        let mut checked = Progress::new(self.id, size, Instant::now())?;
        let mut buffer = Zeroizing::new(vec![0u8; files::CHUNK]);
        while checked.offset() < size {
            self.live(cancelled)?;
            let offset = checked.offset();
            let count = (size - offset).min(files::CHUNK as u64) as usize;
            let mut position = 0;
            while position < count {
                let read = read_at(
                    &self.pending.file,
                    &mut buffer[position..count],
                    offset + position as u64,
                )?;
                if read == 0 {
                    return Err(FileError::Partial);
                }
                position += read;
            }
            self.live(cancelled)?;
            checked.push(self.id, offset, &buffer[..count], Instant::now())?;
        }
        if self.pending.file.metadata()?.len() != size || !self.pending.still_owned()? {
            return Err(FileError::Partial);
        }
        checked.complete(self.id, digest, Instant::now())?;
        Ok(())
    }
}
impl Drop for IncomingFile {
    fn drop(&mut self) {
        let _ = self.abort();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Fixture(std::path::PathBuf);
    impl Fixture {
        fn new() -> Self {
            let root = Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../../_scratch")
                .join(format!("native-file-fixture-{}", Uuid::new_v4()));
            let mut builder = std::fs::DirBuilder::new();
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                builder.mode(0o700);
            }
            builder.recursive(true).create(&root).unwrap();
            Self(root)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            std::fs::remove_dir_all(&self.0).unwrap();
        }
    }
    fn digest(bytes: &[u8]) -> [u8; 32] {
        let mut progress = Progress::new([1; 16], bytes.len() as u64, Instant::now()).unwrap();
        for (index, chunk) in bytes.chunks(files::CHUNK).enumerate() {
            progress
                .push(
                    [1; 16],
                    (index * files::CHUNK) as u64,
                    chunk,
                    Instant::now(),
                )
                .unwrap();
        }
        progress.prefix_digest()
    }
    #[test]
    fn receives_actual_bytes_without_overwrite_and_preserves_published_documents_on_cleanup() {
        let root = Fixture::new();
        let destination = root.0.join("report.txt");
        let cancel = AtomicBool::new(false);
        let now = Instant::now();
        let mut incoming = IncomingFile::create(&destination, [1; 16], 3, now, &cancel).unwrap();
        incoming
            .write_chunk([1; 16], 0, b"abc", now, &cancel)
            .unwrap();
        incoming
            .publish([1; 16], digest(b"abc"), now, &cancel)
            .unwrap();
        incoming.abort().unwrap();
        drop(incoming);
        assert_eq!(std::fs::read(&destination).unwrap(), b"abc");
        assert!(matches!(
            IncomingFile::create(&destination, [2; 16], 0, now, &cancel),
            Err(FileError::Destination)
        ));
        let raced = root.0.join("new.txt");
        let mut incoming = IncomingFile::create(&raced, [1; 16], 3, now, &cancel).unwrap();
        incoming
            .write_chunk([1; 16], 0, b"abc", now, &cancel)
            .unwrap();
        std::fs::write(&raced, b"existing document").unwrap();
        assert!(matches!(
            incoming.publish([1; 16], digest(b"abc"), now, &cancel),
            Err(FileError::Destination)
        ));
        incoming.abort().unwrap();
        drop(incoming);
        assert_eq!(std::fs::read(raced).unwrap(), b"existing document");
        assert_eq!(std::fs::read_dir(&root.0).unwrap().count(), 2);
    }
    #[test]
    fn cancelled_corrupt_and_changed_files_never_publish_or_silently_change_source() {
        let root = Fixture::new();
        let cancel = AtomicBool::new(false);
        let now = Instant::now();
        let destination = root.0.join("copy.txt");
        let mut incoming = IncomingFile::create(&destination, [1; 16], 3, now, &cancel).unwrap();
        incoming
            .write_chunk([1; 16], 0, b"abc", now, &cancel)
            .unwrap();
        write_all_at(&incoming.pending.file, b"bad", 0).unwrap();
        assert!(
            incoming
                .publish([1; 16], digest(b"abc"), now, &cancel)
                .is_err()
        );
        incoming.abort().unwrap();
        drop(incoming);
        assert!(!destination.exists());
        let mut incoming = IncomingFile::create(&destination, [1; 16], 3, now, &cancel).unwrap();
        cancel.store(true, Ordering::Release);
        assert!(matches!(
            incoming.write_chunk([1; 16], 0, b"abc", now, &cancel),
            Err(FileError::Cancelled)
        ));
        incoming.abort().unwrap();
        drop(incoming);
        assert!(!destination.exists());
        cancel.store(false, Ordering::Release);
        let source = root.0.join("source.txt");
        std::fs::write(&source, b"original").unwrap();
        let selected = SourceFile::open(&source, &cancel).unwrap();
        assert_eq!(
            selected.read_chunk(0, &cancel).unwrap().as_slice(),
            b"original"
        );
        std::fs::write(&source, b"changed size").unwrap();
        assert!(matches!(
            selected.read_chunk(0, &cancel),
            Err(FileError::Changed)
        ));
        #[cfg(unix)]
        {
            let link = root.0.join("alias.txt");
            std::os::unix::fs::symlink(&source, &link).unwrap();
            assert!(SourceFile::open(&link, &cancel).is_err());
        }
    }
}
