//! User-bound DPAPI ciphertext, atomically persisted without overwriting another
//! process's identity. No machine-wide protection, UI prompt or plaintext fallback.
use super::{MAGIC, decode};
use ed25519_dalek::SigningKey;
use rand_core::OsRng;
use std::{
    ffi::OsString,
    fs::File,
    io::{Read, Write},
    os::windows::ffi::OsStringExt,
    path::{Path, PathBuf},
};
use windows::{
    Win32::{
        Foundation::{HLOCAL, LocalFree},
        Security::Cryptography::{
            CRYPT_INTEGER_BLOB, CRYPTPROTECT_LOCAL_MACHINE, CRYPTPROTECT_UI_FORBIDDEN,
            CryptProtectData, CryptUnprotectData,
        },
        System::Com::CoTaskMemFree,
        UI::Shell::{FOLDERID_LocalAppData, KF_FLAG_DEFAULT, SHGetKnownFolderPath},
    },
    core::PCWSTR,
};
use zeroize::{Zeroize, Zeroizing};
const MAX_RECORD: usize = 4096;
struct Blob(CRYPT_INTEGER_BLOB);
impl Drop for Blob {
    fn drop(&mut self) {
        if !self.0.pbData.is_null() {
            // SAFETY: CryptProtect/UnprotectData allocate cbData initialized bytes
            // with LocalAlloc on success. This guard owns that output exactly once.
            unsafe {
                std::slice::from_raw_parts_mut(self.0.pbData, self.0.cbData as usize).zeroize();
                let _ = LocalFree(Some(HLOCAL(self.0.pbData.cast())));
            }
        }
    }
}
fn crypt(bytes: &[u8], protect: bool) -> Result<Zeroizing<Vec<u8>>, String> {
    crypt_with(bytes, protect, CRYPTPROTECT_UI_FORBIDDEN)
}
fn crypt_with(
    bytes: &[u8],
    protect: bool,
    flags: u32,
) -> Result<Zeroizing<Vec<u8>>, String> {
    if bytes.is_empty() || bytes.len() > MAX_RECORD {
        return Err("Invalid encrypted identity size".into());
    }
    let input = CRYPT_INTEGER_BLOB {
        cbData: bytes.len() as u32,
        pbData: bytes.as_ptr().cast_mut(),
    };
    let mut output = CRYPT_INTEGER_BLOB::default();
    // SAFETY: input and output remain live for the synchronous call; no optional
    // pointers are supplied. Input data is not modified by the Windows APIs.
    let result = unsafe {
        if protect {
            CryptProtectData(
                &input,
                PCWSTR::null(),
                None,
                None,
                None,
                flags,
                &mut output,
            )
        } else {
            CryptUnprotectData(
                &input,
                None,
                None,
                None,
                None,
                flags,
                &mut output,
            )
        }
    };
    result.map_err(|error| {
        format!(
            "Windows user DPAPI failed ({}); identity was not replaced",
            error.code()
        )
    })?;
    let output = Blob(output);
    if output.0.pbData.is_null() || output.0.cbData == 0 || output.0.cbData as usize > MAX_RECORD {
        return Err("Windows returned an invalid identity record".into());
    }
    // SAFETY: the live Blob owns cbData initialized bytes returned by Windows.
    Ok(Zeroizing::new(
        unsafe { std::slice::from_raw_parts(output.0.pbData, output.0.cbData as usize) }.to_vec(),
    ))
}
pub fn location(profile: Option<uuid::Uuid>) -> Result<PathBuf, String> {
    // SAFETY: known-folder API allocates a null-terminated UTF16 path; its memory
    // is copied losslessly before release with the matching COM allocator.
    let folder = unsafe { SHGetKnownFolderPath(&FOLDERID_LocalAppData, KF_FLAG_DEFAULT, None) }
        .map_err(|error| format!("Local application data unavailable ({})", error.code()))?;
    let path = PathBuf::from(OsString::from_wide(unsafe { folder.as_wide() }));
    unsafe {
        CoTaskMemFree(Some(folder.0.cast()));
    }
    let directory = path.join("Mixel-Remote-Next-QA");
    Ok(match profile {
        Some(profile) => directory.join(format!("personal-{profile}-v1.dpapi")),
        None => directory.join("device-identity-v1.dpapi"),
    })
}
fn read(path: &Path) -> Result<Option<SigningKey>, String> {
    let file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("Cannot read stored device identity".into()),
    };
    if !file
        .metadata()
        .map_err(|_| "Cannot inspect identity record")?
        .is_file()
    {
        return Err("Identity record is not a file".into());
    }
    let mut ciphertext = Zeroizing::new(Vec::new());
    file.take((MAX_RECORD + 1) as u64)
        .read_to_end(&mut ciphertext)
        .map_err(|_| "Cannot read identity record")?;
    let plaintext = crypt(&ciphertext, false)?;
    decode(&plaintext).map(Some)
}
pub(super) fn load_or_create(
    profile: Option<uuid::Uuid>,
    create: bool,
) -> Result<SigningKey, String> {
    let path = location(profile)?;
    if let Some(key) = read(&path)? {
        return Ok(key);
    }
    if !create {
        return Err(
            "No saved device identity. Choose Create device identity to generate one locally."
                .into(),
        );
    }
    let key = SigningKey::generate(&mut OsRng);
    let seed = Zeroizing::new(key.to_bytes());
    let mut record = Zeroizing::new(MAGIC.to_vec());
    record.extend_from_slice(seed.as_ref());
    let ciphertext = crypt(&record, true)?;
    let directory = path.parent().ok_or("Identity directory unavailable")?;
    std::fs::create_dir_all(directory).map_err(|_| "Cannot create identity directory")?;
    struct Pending(PathBuf);
    impl Drop for Pending {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }
    use rand_core::RngCore;
    let mut nonce = [0; 16];
    OsRng.fill_bytes(&mut nonce);
    let suffix: String = nonce.iter().map(|byte| format!("{byte:02x}")).collect();
    let pending = Pending(directory.join(format!(".identity-{suffix}.tmp")));
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&pending.0)
        .map_err(|_| "Cannot prepare encrypted identity")?;
    file.write_all(&ciphertext)
        .map_err(|_| "Cannot write encrypted identity")?;
    file.sync_all()
        .map_err(|_| "Cannot flush encrypted identity")?;
    drop(file);
    // Atomic exclusive publication on the local NTFS volume: readers never see
    // a partial identity and a concurrent creator's record is not overwritten.
    match std::fs::hard_link(&pending.0, &path) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(_) => {
            return Err(
                "Cannot persist encrypted identity; existing records were not overwritten".into(),
            );
        }
    }
    read(&path)?.ok_or_else(|| "Saved identity unavailable after persistence".into())
}

/// DPAPI-protect a small secret for the current Windows user (session store).
pub fn protect(bytes: &[u8]) -> Result<Zeroizing<Vec<u8>>, String> {
    crypt(bytes, true)
}
/// Reverse of [`protect`] for the same Windows user.
pub fn unprotect(bytes: &[u8]) -> Result<Zeroizing<Vec<u8>>, String> {
    crypt(bytes, false)
}

/// Machine-scope DPAPI for the sign-in screen host (readable by SYSTEM; the
/// file itself is restricted to SYSTEM and Administrators).
pub fn machine_protect(bytes: &[u8]) -> Result<Zeroizing<Vec<u8>>, String> {
    crypt_with(bytes, true, CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE)
}
pub fn machine_unprotect(bytes: &[u8]) -> Result<Zeroizing<Vec<u8>>, String> {
    crypt_with(bytes, false, CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE)
}
