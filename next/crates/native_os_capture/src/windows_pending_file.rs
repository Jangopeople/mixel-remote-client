use super::FileError;
use std::{
    fs::{File, OpenOptions},
    io,
    os::windows::{
        fs::{FileExt, MetadataExt, OpenOptionsExt},
        io::AsRawHandle,
    },
    path::Path,
};
use uuid::Uuid;
use windows::Win32::{
    Foundation::{ERROR_ALREADY_EXISTS, ERROR_FILE_EXISTS, GENERIC_READ, GENERIC_WRITE, HANDLE},
    Storage::FileSystem::{
        BY_HANDLE_FILE_INFORMATION, DELETE, FILE_ATTRIBUTE_REPARSE_POINT, FILE_DISPOSITION_INFO,
        FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_NAME_NORMALIZED,
        FILE_RENAME_INFO, FILE_SHARE_READ, FILE_SHARE_WRITE, FileDispositionInfo, FileRenameInfo,
        GetFileInformationByHandle, GetFinalPathNameByHandleW, SetFileInformationByHandle,
    },
};

fn handle(file: &File) -> HANDLE {
    HANDLE(file.as_raw_handle())
}

fn info(file: &File) -> Result<BY_HANDLE_FILE_INFORMATION, FileError> {
    let mut value = BY_HANDLE_FILE_INFORMATION::default();
    // SAFETY: the File owns a live handle and the Windows structure is writable.
    unsafe { GetFileInformationByHandle(handle(file), &mut value) }
        .map_err(|_| io::Error::last_os_error())?;
    Ok(value)
}

#[derive(PartialEq, Eq)]
pub(super) struct Stamp {
    index: (u32, u32, u32),
    pub(super) size: u64,
    modified: u64,
    attributes: u32,
}

pub(super) fn stamp(file: &File) -> Result<Stamp, FileError> {
    let value = info(file)?;
    Ok(Stamp {
        index: (
            value.dwVolumeSerialNumber,
            value.nFileIndexHigh,
            value.nFileIndexLow,
        ),
        size: (u64::from(value.nFileSizeHigh) << 32) | u64::from(value.nFileSizeLow),
        modified: (u64::from(value.ftLastWriteTime.dwHighDateTime) << 32)
            | u64::from(value.ftLastWriteTime.dwLowDateTime),
        attributes: value.dwFileAttributes,
    })
}

pub(super) fn open_source(path: &Path) -> Result<File, FileError> {
    let file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ.0 | FILE_SHARE_WRITE.0)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT.0)
        .open(path)?;
    if file.metadata()?.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT.0 != 0 {
        return Err(FileError::Source);
    }
    Ok(file)
}

pub(super) fn read_at(file: &File, bytes: &mut [u8], offset: u64) -> io::Result<usize> {
    file.seek_read(bytes, offset)
}

pub(super) fn write_all_at(file: &File, mut bytes: &[u8], mut offset: u64) -> io::Result<()> {
    while !bytes.is_empty() {
        let count = file.seek_write(bytes, offset)?;
        if count == 0 {
            return Err(io::ErrorKind::WriteZero.into());
        }
        bytes = &bytes[count..];
        offset += count as u64;
    }
    Ok(())
}

pub(super) struct Pending {
    pub(super) file: File,
    parent: File,
    destination: Vec<u16>,
}

impl Pending {
    pub(super) fn create(path: &Path, name: &str) -> Result<Self, FileError> {
        if name.contains(['<', '>', '"', '|', '?', '*']) {
            return Err(FileError::Destination);
        }
        let parent_path = path.parent().ok_or(FileError::Destination)?;
        let parent = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ.0 | FILE_SHARE_WRITE.0)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS.0 | FILE_FLAG_OPEN_REPARSE_POINT.0)
            .open(parent_path)?;
        let metadata = parent.metadata()?;
        if !metadata.is_dir() || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT.0 != 0 {
            return Err(FileError::Destination);
        }
        match std::fs::symlink_metadata(path) {
            Ok(_) => return Err(FileError::Destination),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        let temporary = parent_path.join(format!(".mixel-incoming-{}.part", Uuid::new_v4()));
        // Retain the exclusive write/delete handle. Cleanup and publication
        // act on this handle, never a pathname that could have been swapped.
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .access_mode(GENERIC_READ.0 | GENERIC_WRITE.0 | DELETE.0)
            .share_mode(0)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT.0)
            .open(temporary)?;
        Ok(Self {
            file,
            parent,
            destination: name.encode_utf16().collect(),
        })
    }

    pub(super) fn still_owned(&self) -> Result<bool, FileError> {
        let value = info(&self.file)?;
        Ok(value.nNumberOfLinks == 1
            && value.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT.0 == 0)
    }

    pub(super) fn abort(&self) -> Result<(), FileError> {
        let disposition = FILE_DISPOSITION_INFO { DeleteFile: true };
        // SAFETY: Windows reads the initialized disposition during this call;
        // DELETE access belongs to the retained staging file only.
        unsafe {
            SetFileInformationByHandle(
                handle(&self.file),
                FileDispositionInfo,
                (&raw const disposition).cast(),
                size_of::<FILE_DISPOSITION_INFO>() as u32,
            )
        }
        .map_err(|_| FileError::Storage(io::Error::last_os_error()))
    }

    pub(super) fn publish(&self) -> Result<(), FileError> {
        let name_offset = std::mem::offset_of!(FILE_RENAME_INFO, FileName);
        // Windows resolves FILE_RENAME_INFO destinations as a DOS path.
        // Obtain that path from the still-held non-delete-sharing directory.
        let mut destination = vec![0u16; 32_768];
        // SAFETY: the parent owns a live directory handle and the buffer is writable.
        let length = unsafe {
            GetFinalPathNameByHandleW(handle(&self.parent), &mut destination, FILE_NAME_NORMALIZED)
        } as usize;
        if length == 0 {
            return Err(FileError::Storage(io::Error::last_os_error()));
        }
        if length >= destination.len().saturating_sub(self.destination.len() + 2) {
            return Err(FileError::Destination);
        }
        destination.truncate(length);
        if destination.last() != Some(&(b'\\' as u16)) {
            destination.push(b'\\' as u16);
        }
        destination.extend_from_slice(&self.destination);
        let name_bytes = destination.len() * size_of::<u16>();
        let bytes =
            (name_offset + name_bytes + size_of::<u16>()).max(size_of::<FILE_RENAME_INFO>());
        let mut aligned = vec![0usize; bytes.div_ceil(size_of::<usize>())];
        let rename = aligned.as_mut_ptr().cast::<FILE_RENAME_INFO>();
        // SAFETY: buffer is aligned for FILE_RENAME_INFO and sized for its
        // header, inline UTF-16 name and terminator. Both handles stay live.
        let result = unsafe {
            (*rename).FileNameLength = name_bytes as u32;
            std::ptr::copy_nonoverlapping(
                destination.as_ptr(),
                rename.cast::<u8>().add(name_offset).cast::<u16>(),
                destination.len(),
            );
            SetFileInformationByHandle(
                handle(&self.file),
                FileRenameInfo,
                rename.cast(),
                bytes as u32,
            )
        };
        if let Err(error) = result {
            let code = error.code();
            if code == ERROR_FILE_EXISTS.to_hresult() || code == ERROR_ALREADY_EXISTS.to_hresult() {
                return Err(FileError::Destination);
            }
            return Err(FileError::Storage(io::Error::last_os_error()));
        }
        Ok(())
    }
}
