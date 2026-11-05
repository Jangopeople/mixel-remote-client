//! Bounded binary input; borrowed text points into the decrypted input buffer.
use serde::{Deserialize, Serialize};
use thiserror::Error;

pub const MAX_INPUT_BYTES: usize = 4096;
pub const MAX_TEXT_BYTES: usize = 2048;

#[derive(Debug, PartialEq, Serialize, Deserialize)]
pub enum Input<'a> {
    Pointer {
        display: u16,
        x: u16,
        y: u16,
    },
    Button {
        button: u8,
        down: bool,
    },
    Wheel {
        horizontal: i16,
        vertical: i16,
    },
    /// USB HID keyboard usage; logical text travels separately.
    Key {
        usage: u16,
        down: bool,
    },
    Text(#[serde(borrow)] &'a str),
    /// Required on focus loss and disconnect to release held keys/buttons.
    ReleaseAll,
}

#[derive(Debug, Error, PartialEq)]
pub enum InputError {
    #[error("invalid, oversized, or noncanonical input packet")]
    Invalid,
}

impl Input<'_> {
    fn valid(&self) -> bool {
        match self {
            Self::Button { button, .. } => (1..=5).contains(button),
            Self::Key { usage, .. } => (4..=0xe7).contains(usage),
            Self::Text(text) => text.len() <= MAX_TEXT_BYTES && !text.contains('\0'),
            _ => true,
        }
    }
}

pub fn validate(input: &Input<'_>) -> Result<(), InputError> {
    if input.valid() {
        Ok(())
    } else {
        Err(InputError::Invalid)
    }
}

pub fn encode(input: &Input<'_>) -> Result<Vec<u8>, InputError> {
    validate(input)?;
    let bytes = bincode::serde::encode_to_vec(
        input,
        bincode::config::standard().with_limit::<MAX_INPUT_BYTES>(),
    )
    .map_err(|_| InputError::Invalid)?;
    if bytes.len() > MAX_INPUT_BYTES {
        return Err(InputError::Invalid);
    }
    Ok(bytes)
}

pub fn decode(bytes: &[u8]) -> Result<Input<'_>, InputError> {
    if bytes.len() > MAX_INPUT_BYTES {
        return Err(InputError::Invalid);
    }
    let (input, consumed): (Input<'_>, usize) = bincode::serde::borrow_decode_from_slice(
        bytes,
        bincode::config::standard().with_limit::<MAX_INPUT_BYTES>(),
    )
    .map_err(|_| InputError::Invalid)?;
    if consumed != bytes.len() || !input.valid() {
        return Err(InputError::Invalid);
    }
    // Reject alternate integer representations so signed transcripts and hashes
    // always have exactly one representation.
    let mut canonical = [0u8; MAX_INPUT_BYTES];
    let length = bincode::serde::encode_into_slice(
        &input,
        &mut canonical,
        bincode::config::standard().with_limit::<MAX_INPUT_BYTES>(),
    )
    .map_err(|_| InputError::Invalid)?;
    if canonical[..length] != *bytes {
        return Err(InputError::Invalid);
    }
    Ok(input)
}
