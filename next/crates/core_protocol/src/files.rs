//! Borrowed file records for the independently encrypted reliable bulk channel.
//! OS selection, consent, durable writes and no-overwrite publication remain
//! native responsibilities; parsing an Offer never grants filesystem access.
use sha2::{Digest, Sha256};
use thiserror::Error;
use web_time::{Duration, Instant};
use zeroize::Zeroizing;

pub const MAX_FILE_SIZE: u64 = 16 * 1024 * 1024 * 1024;
pub const CHUNK: usize = 3000;
pub const IDLE: Duration = Duration::from_secs(30);
pub const LIFETIME: Duration = Duration::from_secs(8 * 3600);
const PREFIX: &[u8; 4] = b"MFB1";
const HEADER: usize = 21;
pub type TransferId = [u8; 16];
#[derive(Debug, Error, PartialEq, Eq)]
#[error("invalid, expired or inconsistent file transfer")]
pub struct FileError;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Rejection {
    Declined,
    Permission,
    Storage,
    Invalid,
    Expired,
}
impl Rejection {
    fn wire(self) -> u8 {
        match self {
            Self::Declined => 0,
            Self::Permission => 1,
            Self::Storage => 2,
            Self::Invalid => 3,
            Self::Expired => 4,
        }
    }
    fn from_wire(value: u8) -> Result<Self, FileError> {
        match value {
            0 => Ok(Self::Declined),
            1 => Ok(Self::Permission),
            2 => Ok(Self::Storage),
            3 => Ok(Self::Invalid),
            4 => Ok(Self::Expired),
            _ => Err(FileError),
        }
    }
}
pub enum Message<'a> {
    Offer {
        id: TransferId,
        name: &'a str,
        size: u64,
    },
    Accept {
        id: TransferId,
        offset: u64,
        prefix_digest: [u8; 32],
    },
    Data {
        id: TransferId,
        offset: u64,
        bytes: &'a [u8],
    },
    Finish {
        id: TransferId,
        digest: [u8; 32],
    },
    Ack {
        id: TransferId,
        offset: u64,
    },
    Reject {
        id: TransferId,
        reason: Rejection,
    },
    Cancel {
        id: TransferId,
    },
    Complete {
        id: TransferId,
        digest: [u8; 32],
    },
}
pub fn valid_name(name: &str) -> bool {
    if name.is_empty() || name.len()>240 || name=="." || name==".." || name.ends_with(['.',' ']) || name.chars().any(|value|value.is_control() || matches!(value,'/'|'\\'|':'|'\u{200e}'|'\u{200f}'|'\u{202a}'..='\u{202e}'|'\u{2066}'..='\u{2069}')){return false;}
    let stem = name.split('.').next().unwrap_or("").to_ascii_uppercase();
    if matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        || stem
            .strip_prefix("COM")
            .or_else(|| stem.strip_prefix("LPT"))
            .is_some_and(|suffix| {
                matches!(
                    suffix,
                    "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
                )
            })
    {
        return false;
    }
    true
}
impl Message<'_> {
    pub fn id(&self) -> TransferId {
        match self {
            Self::Offer { id, .. }
            | Self::Accept { id, .. }
            | Self::Data { id, .. }
            | Self::Finish { id, .. }
            | Self::Ack { id, .. }
            | Self::Reject { id, .. }
            | Self::Cancel { id }
            | Self::Complete { id, .. } => *id,
        }
    }
    pub fn encode(&self) -> Result<Zeroizing<Vec<u8>>, FileError> {
        let id = self.id();
        if id == [0; 16] {
            return Err(FileError);
        }
        let kind = match self {
            Self::Offer { .. } => 0,
            Self::Accept { .. } => 1,
            Self::Data { .. } => 2,
            Self::Finish { .. } => 3,
            Self::Ack { .. } => 4,
            Self::Reject { .. } => 5,
            Self::Cancel { .. } => 6,
            Self::Complete { .. } => 7,
        };
        let mut bytes = Zeroizing::new(Vec::with_capacity(HEADER + 8 + CHUNK));
        bytes.extend(PREFIX);
        bytes.push(kind);
        bytes.extend(id);
        match self {
            Self::Offer { name, size, .. } if valid_name(name) && *size <= MAX_FILE_SIZE => {
                bytes.extend(size.to_be_bytes());
                bytes.extend((name.len() as u16).to_be_bytes());
                bytes.extend(name.as_bytes());
            }
            Self::Accept {
                offset,
                prefix_digest,
                ..
            } if *offset <= MAX_FILE_SIZE => {
                bytes.extend(offset.to_be_bytes());
                bytes.extend(prefix_digest);
            }
            Self::Data {
                offset,
                bytes: data,
                ..
            } if !data.is_empty()
                && data.len() <= CHUNK
                && offset
                    .checked_add(data.len() as u64)
                    .is_some_and(|end| end <= MAX_FILE_SIZE) =>
            {
                bytes.extend(offset.to_be_bytes());
                bytes.extend(*data);
            }
            Self::Finish { digest, .. } | Self::Complete { digest, .. } => bytes.extend(digest),
            Self::Ack { offset, .. } if *offset <= MAX_FILE_SIZE => {
                bytes.extend(offset.to_be_bytes())
            }
            Self::Reject { reason, .. } => bytes.push(reason.wire()),
            Self::Cancel { .. } => {}
            _ => return Err(FileError),
        }
        Ok(bytes)
    }
    pub fn decode(bytes: &[u8]) -> Result<Message<'_>, FileError> {
        if bytes.len() < HEADER || bytes.len() > HEADER + 8 + CHUNK || &bytes[..4] != PREFIX {
            return Err(FileError);
        }
        let id: TransferId = bytes[5..HEADER].try_into().map_err(|_| FileError)?;
        if id == [0; 16] {
            return Err(FileError);
        }
        let body = &bytes[HEADER..];
        let number = || -> Result<u64, FileError> {
            Ok(u64::from_be_bytes(
                body.get(..8)
                    .ok_or(FileError)?
                    .try_into()
                    .map_err(|_| FileError)?,
            ))
        };
        match bytes[4] {
            0 if body.len() >= 11 => {
                let size = number()?;
                let length =
                    u16::from_be_bytes(body[8..10].try_into().map_err(|_| FileError)?) as usize;
                let name = std::str::from_utf8(&body[10..]).map_err(|_| FileError)?;
                if length != name.len() || !valid_name(name) || size > MAX_FILE_SIZE {
                    return Err(FileError);
                }
                Ok(Message::Offer { id, name, size })
            }
            1 if body.len() == 40 && number()? <= MAX_FILE_SIZE => Ok(Message::Accept {
                id,
                offset: number()?,
                prefix_digest: body[8..].try_into().map_err(|_| FileError)?,
            }),
            2 if body.len() > 8
                && body.len() <= 8 + CHUNK
                && number()?
                    .checked_add((body.len() - 8) as u64)
                    .is_some_and(|end| end <= MAX_FILE_SIZE) =>
            {
                Ok(Message::Data {
                    id,
                    offset: number()?,
                    bytes: &body[8..],
                })
            }
            3 if body.len() == 32 => Ok(Message::Finish {
                id,
                digest: body.try_into().map_err(|_| FileError)?,
            }),
            4 if body.len() == 8 && number()? <= MAX_FILE_SIZE => Ok(Message::Ack {
                id,
                offset: number()?,
            }),
            5 if body.len() == 1 => Ok(Message::Reject {
                id,
                reason: Rejection::from_wire(body[0])?,
            }),
            6 if body.is_empty() => Ok(Message::Cancel { id }),
            7 if body.len() == 32 => Ok(Message::Complete {
                id,
                digest: body.try_into().map_err(|_| FileError)?,
            }),
            _ => Err(FileError),
        }
    }
}
/// Streaming prefix checkpoint. Clone only into the same native, accepted file
/// owner; it contains no permission or path and cannot authorize a new peer.
#[derive(Clone)]
pub struct Progress {
    id: TransferId,
    size: u64,
    offset: u64,
    hash: Sha256,
    started: Instant,
    last: Instant,
}
impl Progress {
    pub fn new(id: TransferId, size: u64, now: Instant) -> Result<Self, FileError> {
        if id == [0; 16] || size > MAX_FILE_SIZE {
            return Err(FileError);
        }
        Ok(Self {
            id,
            size,
            offset: 0,
            hash: Sha256::new(),
            started: now,
            last: now,
        })
    }
    pub fn offset(&self) -> u64 {
        self.offset
    }
    pub fn prefix_digest(&self) -> [u8; 32] {
        self.hash.clone().finalize().into()
    }
    pub fn push(
        &mut self,
        id: TransferId,
        offset: u64,
        bytes: &[u8],
        now: Instant,
    ) -> Result<(), FileError> {
        self.live(now)?;
        let end = offset.checked_add(bytes.len() as u64).ok_or(FileError)?;
        if id != self.id
            || offset != self.offset
            || bytes.is_empty()
            || bytes.len() > CHUNK
            || end > self.size
        {
            return Err(FileError);
        }
        self.hash.update(bytes);
        self.offset = end;
        self.last = now;
        Ok(())
    }
    pub fn complete(
        &self,
        id: TransferId,
        digest: [u8; 32],
        now: Instant,
    ) -> Result<(), FileError> {
        self.live(now)?;
        if id != self.id || self.offset != self.size || self.prefix_digest() != digest {
            return Err(FileError);
        }
        Ok(())
    }
    /// Native code may resume an existing accepted owner after authenticating
    /// replacement transport and the unchanged peer/grant. Absolute lifetime
    /// and accumulated hash are never reset, and no claimed wire offset is used.
    pub fn resumed(&mut self, now: Instant) -> Result<(), FileError> {
        if now.saturating_duration_since(self.started) >= LIFETIME {
            return Err(FileError);
        }
        self.last = now;
        Ok(())
    }
    pub fn live(&self, now: Instant) -> Result<(), FileError> {
        if now.saturating_duration_since(self.started) >= LIFETIME
            || now.saturating_duration_since(self.last) >= IDLE
        {
            Err(FileError)
        } else {
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn portable_names_and_borrowed_records_reject_traversal_oversize_and_trailing_data() {
        for name in [
            "../escape",
            "/root",
            "a\\b",
            "..",
            ".",
            "x:",
            "NUL.txt",
            "COM1",
            "x\u{202e}txt.exe",
            "name ",
            "name.",
            "a\0b",
        ] {
            assert!(!valid_name(name), "{name:?}");
        }
        assert!(valid_name("Mixel ü report.txt"));
        let id = [7; 16];
        let bytes = Message::Offer {
            id,
            name: "report.txt",
            size: 123,
        }
        .encode()
        .unwrap();
        let Message::Offer { name, size, .. } = Message::decode(&bytes).unwrap() else {
            panic!("Missing offer");
        };
        assert_eq!(name, "report.txt");
        assert_eq!(size, 123);
        let mut extra = bytes.to_vec();
        extra.push(0);
        assert!(Message::decode(&extra).is_err());
        assert!(
            Message::Offer {
                id,
                name: "report",
                size: MAX_FILE_SIZE + 1
            }
            .encode()
            .is_err()
        );
        assert!(Message::Cancel { id: [0; 16] }.encode().is_err());
    }
    #[test]
    fn checkpoints_preserve_hash_and_absolute_limit_across_authorized_resume() {
        let now = Instant::now();
        let id = [1; 16];
        let mut progress = Progress::new(id, 6, now).unwrap();
        progress.push(id, 0, b"abc", now).unwrap();
        assert!(progress.push(id, 0, b"abc", now).is_err());
        assert!(
            progress
                .complete(id, Sha256::digest(b"abc").into(), now)
                .is_err()
        );
        let digest = progress.prefix_digest();
        let mut saved = progress.clone();
        saved.resumed(now + Duration::from_secs(60)).unwrap();
        assert_eq!(saved.prefix_digest(), digest);
        assert_eq!(saved.offset(), 3);
        saved
            .push(id, 3, b"def", now + Duration::from_secs(61))
            .unwrap();
        saved
            .complete(
                id,
                Sha256::digest(b"abcdef").into(),
                now + Duration::from_secs(61),
            )
            .unwrap();
        assert!(
            saved
                .complete(id, [0; 32], now + Duration::from_secs(61))
                .is_err()
        );
        assert!(saved.resumed(now + LIFETIME).is_err());
        assert!(progress.push(id, 3, b"def", now + IDLE).is_err());
        let empty = Progress::new(id, 0, now).unwrap();
        empty.complete(id, Sha256::digest([]).into(), now).unwrap();
    }
}
