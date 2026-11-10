//! Bounded clipboard messages inside authenticated ordered control records.
//! No native clipboard access occurs in this module. A complete length/hash
//! checked transaction must commit before the OS clipboard may be replaced.
use sha2::{Digest, Sha256};
use thiserror::Error;
use web_time::{Duration, Instant};
use zeroize::Zeroizing;

pub const PREFIX: &[u8; 4] = b"MCB1";
pub const MAX_TEXT: usize = 1024 * 1024;
pub const CHUNK: usize = 3072;
pub const LIFETIME: Duration = Duration::from_secs(15);
#[derive(Debug, Error, PartialEq, Eq)]
#[error("invalid, expired or incomplete clipboard transaction")]
pub struct ClipboardError;
pub enum Message<'a> {
    Get(u64),
    Begin {
        id: u64,
        length: u32,
        digest: [u8; 32],
    },
    Chunk {
        id: u64,
        offset: u32,
        bytes: &'a [u8],
    },
    Commit(u64),
    Ack(u64),
    Unavailable(u64),
}
impl Message<'_> {
    pub fn encode(&self) -> Result<Zeroizing<Vec<u8>>, ClipboardError> {
        let (kind, id) = match self {
            Self::Get(id) => (0, *id),
            Self::Begin { id, .. } => (1, *id),
            Self::Chunk { id, .. } => (2, *id),
            Self::Commit(id) => (3, *id),
            Self::Ack(id) => (4, *id),
            Self::Unavailable(id) => (5, *id),
        };
        if id == 0 {
            return Err(ClipboardError);
        }
        let mut bytes = Zeroizing::new(Vec::with_capacity(CHUNK + 17));
        bytes.extend(PREFIX);
        bytes.push(kind);
        bytes.extend(id.to_be_bytes());
        match self {
            Self::Begin { length, digest, .. } if *length as usize <= MAX_TEXT => {
                bytes.extend(length.to_be_bytes());
                bytes.extend(digest);
            }
            Self::Chunk {
                offset,
                bytes: data,
                ..
            } if !data.is_empty()
                && data.len() <= CHUNK
                && (*offset as usize)
                    .checked_add(data.len())
                    .is_some_and(|end| end <= MAX_TEXT) =>
            {
                bytes.extend(offset.to_be_bytes());
                bytes.extend(*data);
            }
            Self::Get(_) | Self::Commit(_) | Self::Ack(_) | Self::Unavailable(_) => {}
            _ => return Err(ClipboardError),
        }
        Ok(bytes)
    }
    pub fn decode(bytes: &[u8]) -> Result<Message<'_>, ClipboardError> {
        if bytes.len() < 13 || &bytes[..4] != PREFIX {
            return Err(ClipboardError);
        }
        let id = u64::from_be_bytes(bytes[5..13].try_into().map_err(|_| ClipboardError)?);
        if id == 0 {
            return Err(ClipboardError);
        }
        let number = |start: usize| -> Result<u32, ClipboardError> {
            Ok(u32::from_be_bytes(
                bytes
                    .get(start..start + 4)
                    .ok_or(ClipboardError)?
                    .try_into()
                    .map_err(|_| ClipboardError)?,
            ))
        };
        match bytes[4] {
            0 if bytes.len() == 13 => Ok(Message::Get(id)),
            1 if bytes.len() == 49 && number(13)? as usize <= MAX_TEXT => Ok(Message::Begin {
                id,
                length: number(13)?,
                digest: bytes[17..49].try_into().map_err(|_| ClipboardError)?,
            }),
            2 if bytes.len() > 17
                && bytes.len() <= 17 + CHUNK
                && (number(13)? as usize)
                    .checked_add(bytes.len() - 17)
                    .is_some_and(|end| end <= MAX_TEXT) =>
            {
                Ok(Message::Chunk {
                    id,
                    offset: number(13)?,
                    bytes: &bytes[17..],
                })
            }
            3 if bytes.len() == 13 => Ok(Message::Commit(id)),
            4 if bytes.len() == 13 => Ok(Message::Ack(id)),
            5 if bytes.len() == 13 => Ok(Message::Unavailable(id)),
            _ => Err(ClipboardError),
        }
    }
}
pub fn validate(text: &str) -> Result<(), ClipboardError> {
    if text.len() > MAX_TEXT || text.contains('\0') {
        Err(ClipboardError)
    } else {
        Ok(())
    }
}
pub fn digest(text: &str) -> [u8; 32] {
    Sha256::digest(text.as_bytes()).into()
}
pub struct Receiver {
    id: u64,
    length: usize,
    digest: [u8; 32],
    bytes: Zeroizing<Vec<u8>>,
    deadline: Instant,
}
impl Receiver {
    pub fn begin(message: &Message<'_>, now: Instant) -> Result<Self, ClipboardError> {
        let Message::Begin { id, length, digest } = message else {
            return Err(ClipboardError);
        };
        if *id == 0 || *length as usize > MAX_TEXT {
            return Err(ClipboardError);
        }
        Ok(Self {
            id: *id,
            length: *length as usize,
            digest: *digest,
            bytes: Zeroizing::new(Vec::with_capacity(*length as usize)),
            deadline: now + LIFETIME,
        })
    }
    pub fn push(&mut self, message: &Message<'_>, now: Instant) -> Result<(), ClipboardError> {
        let Message::Chunk { id, offset, bytes } = message else {
            return Err(ClipboardError);
        };
        if now >= self.deadline
            || *id != self.id
            || *offset as usize != self.bytes.len()
            || bytes.is_empty()
            || bytes.len() > CHUNK
            || self.bytes.len() + bytes.len() > self.length
        {
            return Err(ClipboardError);
        }
        self.bytes.extend(*bytes);
        Ok(())
    }
    pub fn commit(mut self, id: u64, now: Instant) -> Result<Zeroizing<String>, ClipboardError> {
        if id != self.id
            || now >= self.deadline
            || self.bytes.len() != self.length
            || <[u8; 32]>::from(Sha256::digest(&self.bytes)) != self.digest
        {
            return Err(ClipboardError);
        }
        let bytes = std::mem::take(&mut *self.bytes);
        match String::from_utf8(bytes) {
            Ok(text) => {
                let text = Zeroizing::new(text);
                validate(&text)?;
                Ok(text)
            }
            Err(error) => {
                let _scrub = Zeroizing::new(error.into_bytes());
                Err(ClipboardError)
            }
        }
    }
    pub fn expired(&self, now: Instant) -> bool {
        now >= self.deadline
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unicode_chunks_commit_only_after_exact_length_and_digest() {
        let text = "Mixel ü 👋\r\n".repeat(2000);
        let now = Instant::now();
        let header = Message::Begin {
            id: 7,
            length: text.len() as u32,
            digest: digest(&text),
        }
        .encode()
        .unwrap();
        let mut receiver = Receiver::begin(&Message::decode(&header).unwrap(), now).unwrap();
        for (index, chunk) in text.as_bytes().chunks(CHUNK).enumerate() {
            let bytes = Message::Chunk {
                id: 7,
                offset: (index * CHUNK) as u32,
                bytes: chunk,
            }
            .encode()
            .unwrap();
            receiver
                .push(&Message::decode(&bytes).unwrap(), now)
                .unwrap();
        }
        assert_eq!(receiver.commit(7, now).unwrap().as_str(), text);
    }
    #[test]
    fn malformed_truncated_reordered_and_expired_transfers_cannot_commit() {
        let now = Instant::now();
        let begin = || Message::Begin {
            id: 1,
            length: 3,
            digest: digest("abc"),
        };
        let mut r = Receiver::begin(&begin(), now).unwrap();
        assert!(
            r.push(
                &Message::Chunk {
                    id: 1,
                    offset: 1,
                    bytes: b"abc"
                },
                now
            )
            .is_err()
        );
        let r = Receiver::begin(&begin(), now).unwrap();
        assert!(r.commit(1, now).is_err());
        let mut r = Receiver::begin(&begin(), now).unwrap();
        r.push(
            &Message::Chunk {
                id: 1,
                offset: 0,
                bytes: b"abd",
            },
            now,
        )
        .unwrap();
        assert!(r.commit(1, now).is_err());
        let mut r = Receiver::begin(&begin(), now).unwrap();
        assert!(
            r.push(
                &Message::Chunk {
                    id: 1,
                    offset: 0,
                    bytes: b"abc"
                },
                now + LIFETIME
            )
            .is_err()
        );
        assert!(Message::Get(0).encode().is_err());
        assert!(
            Message::Begin {
                id: 1,
                length: MAX_TEXT as u32 + 1,
                digest: [0; 32]
            }
            .encode()
            .is_err()
        );
        assert!(Message::decode(b"MCB1").is_err());
        assert!(
            Message::Chunk {
                id: 1,
                offset: 0,
                bytes: &[]
            }
            .encode()
            .is_err()
        );
        assert!(validate("a\0b").is_err());
    }
}
