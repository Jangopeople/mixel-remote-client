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
/// Images and small files (only between apps that both announced the `clip2` feature).
pub const MAX_RICH: usize = 32 * 1024 * 1024;
pub const RICH_LIFETIME: Duration = Duration::from_secs(120);
pub const FEATURE: &str = "clip2";
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
    /// Like `Get`, from a controller that also accepts images and files.
    GetRich(u64),
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
            Self::GetRich(id) => (6, *id),
        };
        if id == 0 {
            return Err(ClipboardError);
        }
        let mut bytes = Zeroizing::new(Vec::with_capacity(CHUNK + 17));
        bytes.extend(PREFIX);
        bytes.push(kind);
        bytes.extend(id.to_be_bytes());
        match self {
            Self::Begin { length, digest, .. } if *length as usize <= MAX_RICH => {
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
                    .is_some_and(|end| end <= MAX_RICH) =>
            {
                bytes.extend(offset.to_be_bytes());
                bytes.extend(*data);
            }
            Self::Get(_) | Self::Commit(_) | Self::Ack(_) | Self::Unavailable(_) | Self::GetRich(_) => {}
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
            1 if bytes.len() == 49 && number(13)? as usize <= MAX_RICH => Ok(Message::Begin {
                id,
                length: number(13)?,
                digest: bytes[17..49].try_into().map_err(|_| ClipboardError)?,
            }),
            2 if bytes.len() > 17
                && bytes.len() <= 17 + CHUNK
                && (number(13)? as usize)
                    .checked_add(bytes.len() - 17)
                    .is_some_and(|end| end <= MAX_RICH) =>
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
            6 if bytes.len() == 13 => Ok(Message::GetRich(id)),
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
        Self::begin_limited(message, now, MAX_TEXT, LIFETIME)
    }
    /// A transfer that may also carry an image or files (see [`Content`]).
    pub fn begin_rich(message: &Message<'_>, now: Instant) -> Result<Self, ClipboardError> {
        Self::begin_limited(message, now, MAX_RICH, RICH_LIFETIME)
    }
    fn begin_limited(
        message: &Message<'_>,
        now: Instant,
        limit: usize,
        lifetime: Duration,
    ) -> Result<Self, ClipboardError> {
        let Message::Begin { id, length, digest } = message else {
            return Err(ClipboardError);
        };
        if *id == 0 || *length as usize > limit {
            return Err(ClipboardError);
        }
        Ok(Self {
            id: *id,
            length: *length as usize,
            digest: *digest,
            bytes: Zeroizing::new(Vec::with_capacity((*length as usize).min(MAX_TEXT))),
            deadline: now + lifetime,
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
    /// The complete, length and hash checked payload of a rich transfer.
    pub fn commit_content(mut self, id: u64, now: Instant) -> Result<Content, ClipboardError> {
        if id != self.id
            || now >= self.deadline
            || self.bytes.len() != self.length
            || <[u8; 32]>::from(Sha256::digest(&self.bytes)) != self.digest
        {
            return Err(ClipboardError);
        }
        Content::decode(std::mem::take(&mut *self.bytes))
    }
    pub fn expired(&self, now: Instant) -> bool {
        now >= self.deadline
    }
}

/// What a clipboard transfer carries. Plain text is the payload itself; an image
/// or files start with a zero byte, which plain text never contains.
pub enum Content {
    Text(Zeroizing<String>),
    /// A PNG picture.
    Image(Vec<u8>),
    /// Small files: plain file name and contents.
    Files(Vec<(String, Vec<u8>)>),
}
pub const MAX_FILES: usize = 64;
impl Content {
    pub fn encode(&self) -> Result<Zeroizing<Vec<u8>>, ClipboardError> {
        let bytes = match self {
            Self::Text(text) => {
                validate(text)?;
                text.as_bytes().to_vec()
            }
            Self::Image(png) => {
                let mut bytes = Vec::with_capacity(png.len() + 2);
                bytes.extend([0, 1]);
                bytes.extend(png);
                bytes
            }
            Self::Files(files) => {
                if files.is_empty() || files.len() > MAX_FILES {
                    return Err(ClipboardError);
                }
                let mut bytes = vec![0, 2, files.len() as u8];
                for (name, data) in files {
                    if !file_name_ok(name) || data.len() > MAX_RICH {
                        return Err(ClipboardError);
                    }
                    bytes.extend((name.len() as u16).to_be_bytes());
                    bytes.extend(name.as_bytes());
                    bytes.extend((data.len() as u32).to_be_bytes());
                    bytes.extend(data);
                }
                bytes
            }
        };
        if bytes.is_empty() || bytes.len() > MAX_RICH {
            return Err(ClipboardError);
        }
        Ok(Zeroizing::new(bytes))
    }
    pub fn decode(bytes: Vec<u8>) -> Result<Self, ClipboardError> {
        match bytes.as_slice() {
            [0, 1, png @ ..] if png.len() > 8 => Ok(Self::Image(png.to_vec())),
            [0, 2, count, rest @ ..] if *count as usize >= 1 && *count as usize <= MAX_FILES => {
                let mut rest = rest;
                let mut files = Vec::with_capacity(*count as usize);
                for _ in 0..*count {
                    let (length, tail) = rest.split_at_checked(2).ok_or(ClipboardError)?;
                    let length = u16::from_be_bytes([length[0], length[1]]) as usize;
                    let (name, tail) = tail.split_at_checked(length).ok_or(ClipboardError)?;
                    let name = std::str::from_utf8(name).map_err(|_| ClipboardError)?;
                    let (size, tail) = tail.split_at_checked(4).ok_or(ClipboardError)?;
                    let size = u32::from_be_bytes([size[0], size[1], size[2], size[3]]) as usize;
                    let (data, tail) = tail.split_at_checked(size).ok_or(ClipboardError)?;
                    if !file_name_ok(name) || files.iter().any(|(other, _): &(String, Vec<u8>)| other.eq_ignore_ascii_case(name)) {
                        return Err(ClipboardError);
                    }
                    files.push((name.to_owned(), data.to_vec()));
                    rest = tail;
                }
                if rest.is_empty() { Ok(Self::Files(files)) } else { Err(ClipboardError) }
            }
            [0, ..] | [] => Err(ClipboardError),
            _ => match String::from_utf8(bytes) {
                Ok(text) => {
                    let text = Zeroizing::new(text);
                    if text.len() > MAX_TEXT {
                        return Err(ClipboardError);
                    }
                    validate(&text)?;
                    Ok(Self::Text(text))
                }
                Err(error) => {
                    let _scrub = Zeroizing::new(error.into_bytes());
                    Err(ClipboardError)
                }
            },
        }
    }
}
/// A plain file name that is safe to create inside a folder on every system.
pub fn file_name_ok(name: &str) -> bool {
    const RESERVED: [&str; 22] = [
        "con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8",
        "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
    ];
    let stem = name.split('.').next().unwrap_or("").to_ascii_lowercase();
    !name.is_empty()
        && name.len() <= 200
        && name != "."
        && name != ".."
        && !name.ends_with(['.', ' '])
        && !name.starts_with(' ')
        && !name.chars().any(|c| c.is_control() || "/\\:*?\"<>|".contains(c))
        && !RESERVED.contains(&stem.as_str())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn images_and_files_round_trip_and_bad_names_are_refused() {
        let png = vec![0x89, b'P', b'N', b'G', 1, 2, 3, 4, 5, 6];
        let Content::Image(back) = Content::decode(Content::Image(png.clone()).encode().unwrap().to_vec()).unwrap() else { panic!() };
        assert_eq!(back, png);
        let files = vec![("Bericht ü.pdf".to_owned(), vec![1, 2, 3]), ("b.txt".to_owned(), Vec::new())];
        let Content::Files(back) = Content::decode(Content::Files(files.clone()).encode().unwrap().to_vec()).unwrap() else { panic!() };
        assert_eq!(back, files);
        let Content::Text(text) = Content::decode(b"hello".to_vec()).unwrap() else { panic!() };
        assert_eq!(text.as_str(), "hello");
        for bad in ["../x", "a/b", "a\\b", "con.txt", "x.", " x", "", "a:b", "nul"] {
            assert!(!file_name_ok(bad), "{bad}");
            assert!(Content::Files(vec![(bad.to_owned(), vec![1])]).encode().is_err());
        }
        assert!(Content::decode(vec![0, 2, 1, 0, 1, b'a', 0, 0, 0, 9, 1]).is_err());
        assert!(Content::decode(vec![0, 9, 1]).is_err());
        // Text-only receivers still refuse anything above the text limit.
        let big = Message::Begin { id: 1, length: (MAX_TEXT + 1) as u32, digest: [0; 32] };
        assert!(Receiver::begin(&big, Instant::now()).is_err());
        assert!(Receiver::begin_rich(&big, Instant::now()).is_ok());
    }
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
                length: MAX_RICH as u32 + 1,
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
