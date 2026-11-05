use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub const MAX_MESSAGE: usize = 65536;
#[derive(Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ClientMessage {
    Authenticate { token: String },
    Renew { token: String },
    Request { host: u32 },
    Decide { session: Uuid, accept: bool },
    Signal { session: Uuid, payload: Signal },
    Close { session: Uuid },
}
#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(
    tag = "kind",
    content = "value",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum Signal {
    Identity(core_protocol::crypto::Hello),
    Offer(String),
    Answer(String),
    Ice(String),
}
impl Signal {
    pub fn valid(&self) -> bool {
        match self {
            Self::Identity(_) => true,
            Self::Offer(s) | Self::Answer(s) => !s.is_empty() && s.len() <= 60000,
            Self::Ice(s) => !s.is_empty() && s.len() <= 4096,
        }
    }
}
#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerMessage {
    Ready {
        host: u32,
        protocol: u16,
    },
    Renewed {
        expires_at: u64,
    },
    Requested {
        session: Uuid,
    },
    Consent {
        session: Uuid,
        requester: Uuid,
        requester_tenant: Option<Uuid>,
    },
    Accepted {
        session: Uuid,
        nonce: [u8; 32],
    },
    Signal {
        session: Uuid,
        payload: Signal,
    },
    Closed {
        session: Uuid,
        reason: CloseReason,
    },
    Error {
        code: ErrorCode,
    },
}
#[derive(Clone, Copy, Deserialize, Serialize, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CloseReason {
    Rejected,
    Disconnected,
    Expired,
    Requested,
}
#[derive(Clone, Copy, Deserialize, Serialize, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    Forbidden,
    Busy,
    Unavailable,
    Invalid,
}
