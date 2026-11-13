use super::FileError;
use std::{
    ffi::CString,
    fs::{File, OpenOptions},
    io,
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::fs::{FileExt, MetadataExt, OpenOptionsExt},
    },
    path::Path,
};
use uuid::Uuid;

#[derive(PartialEq, Eq)]
pub(super) struct Stamp {
    device: u64,
    inode: u64,
    pub(super) size: u64,
    modified: i64,
    nanos: i64,
    mode: u32,
}
pub(super) fn stamp(file: &File) -> Result<Stamp, FileError> {
    let m = file.metadata()?;
    Ok(Stamp {
        device: m.dev(),
        inode: m.ino(),
        size: m.len(),
        modified: m.mtime(),
        nanos: m.mtime_nsec(),
        mode: m.mode(),
    })
}
pub(super) fn open_source(path: &Path) -> Result<File, FileError> {
    Ok(OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(path)?)
}
pub(super) fn read_at(file: &File, bytes: &mut [u8], offset: u64) -> io::Result<usize> {
    file.read_at(bytes, offset)
}
pub(super) fn write_all_at(file: &File, bytes: &[u8], offset: u64) -> io::Result<()> {
    file.write_all_at(bytes, offset)
}
pub(super) struct Pending {
    parent: File,
    pub(super) file: File,
    temporary: CString,
    destination: CString,
}
impl Pending {
    pub(super) fn create(path: &Path, name: &str) -> Result<Self, FileError> {
        let parent = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(path.parent().ok_or(FileError::Destination)?)?;
        let destination = CString::new(name).map_err(|_| FileError::Destination)?;
        let temporary = CString::new(format!(".mixel-incoming-{}.part", Uuid::new_v4()))
            .map_err(|_| FileError::Partial)?;
        // SAFETY: retained directory FD, NUL-terminated single path component,
        // initialized output. AT_SYMLINK_NOFOLLOW detects existing links too.
        let mut entry = std::mem::MaybeUninit::<libc::stat>::uninit();
        let exists = unsafe {
            libc::fstatat(
                parent.as_raw_fd(),
                destination.as_ptr(),
                entry.as_mut_ptr(),
                libc::AT_SYMLINK_NOFOLLOW,
            )
        };
        if exists == 0 {
            return Err(FileError::Destination);
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::NotFound {
            return Err(FileError::Storage(error));
        }
        // SAFETY: random single component under the held directory capability.
        // O_EXCL/NOFOLLOW forbids reusing a link or another process's file.
        let fd = unsafe {
            libc::openat(
                parent.as_raw_fd(),
                temporary.as_ptr(),
                libc::O_CREAT | libc::O_EXCL | libc::O_RDWR | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                0o600,
            )
        };
        if fd < 0 {
            return Err(io::Error::last_os_error().into());
        }
        // SAFETY: openat succeeded and ownership of this unique FD moves here.
        let file = unsafe { File::from_raw_fd(fd) };
        Ok(Self {
            parent,
            file,
            temporary,
            destination,
        })
    }
    pub(super) fn abort(&self) -> Result<(), FileError> {
        match self.still_owned() {
            Ok(true) => {}
            Ok(false) => return Err(FileError::Partial),
            Err(FileError::Storage(error)) if error.kind() == io::ErrorKind::NotFound => {
                return Ok(());
            }
            Err(error) => return Err(error),
        }
        // SAFETY: still-owned private entry in the retained directory. No
        // public destination or replacement entry is selected for deletion.
        if unsafe { libc::unlinkat(self.parent.as_raw_fd(), self.temporary.as_ptr(), 0) } != 0 {
            return Err(io::Error::last_os_error().into());
        }
        Ok(())
    }
    pub(super) fn publish(&self) -> Result<(), FileError> {
        // SAFETY: both names are bounded single components in one retained
        // directory. RENAME_EXCL atomically refuses any existing destination.
        #[cfg(any(target_os = "macos", target_os = "ios"))]
        let result = unsafe {
            libc::renameatx_np(
                self.parent.as_raw_fd(),
                self.temporary.as_ptr(),
                self.parent.as_raw_fd(),
                self.destination.as_ptr(),
                libc::RENAME_EXCL,
            )
        };
        #[cfg(target_os = "linux")]
        let result = unsafe {
            libc::renameat2(
                self.parent.as_raw_fd(),
                self.temporary.as_ptr(),
                self.parent.as_raw_fd(),
                self.destination.as_ptr(),
                libc::RENAME_NOREPLACE,
            )
        };
        if result != 0 {
            let error = io::Error::last_os_error();
            return Err(if error.kind() == io::ErrorKind::AlreadyExists {
                FileError::Destination
            } else {
                error.into()
            });
        }
        // Some filesystems do not support directory fsync. Data was synced and
        // the exclusive rename succeeded; never call this an aborted transfer
        // or delete the published document because a metadata flush is denied.
        let _ = self.parent.sync_all();
        Ok(())
    }
    pub(super) fn still_owned(&self) -> Result<bool, FileError> {
        let mut entry = std::mem::MaybeUninit::<libc::stat>::uninit();
        // SAFETY: live directory FD and initialized stat output on success.
        let result = unsafe {
            libc::fstatat(
                self.parent.as_raw_fd(),
                self.temporary.as_ptr(),
                entry.as_mut_ptr(),
                libc::AT_SYMLINK_NOFOLLOW,
            )
        };
        if result != 0 {
            return Err(io::Error::last_os_error().into());
        }
        // SAFETY: fstatat succeeded and initialized this entire stat structure.
        let entry = unsafe { entry.assume_init() };
        let current = self.file.metadata()?;
        #[cfg(any(target_os = "macos", target_os = "ios"))]
        let device = entry.st_dev as u64;
        #[cfg(target_os = "linux")]
        let device = entry.st_dev;
        Ok(device == current.dev()
            && entry.st_ino == current.ino()
            && entry.st_mode & libc::S_IFMT == libc::S_IFREG)
    }
}
