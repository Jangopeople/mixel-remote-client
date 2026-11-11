//! The connection service's messages, as far as a Quick Support host needs them.
//! Shapes match `signaling_server::protocol` (kept apart so phones do not carry the server code).
use core_protocol::{crypto::Hello, delegation::DeviceTicket, device_login::DeviceLoginChallenge};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Out {
    AuthenticateDevice { token: String, device: Uuid },
    ProveDevice { signature: Vec<u8> },
    Renew { token: String },
    ConfigureSupportHost { enabled: bool },
    Decide { session: Uuid, accept: bool },
    Signal { session: Uuid, payload: Signal },
    Close { session: Uuid },
    Ice { session: Uuid },
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(tag = "kind", content = "value", rename_all = "snake_case")]
pub enum Signal {
    Identity(Hello),
    Offer(String),
    Answer(String),
    Ice(String),
}

#[derive(Clone, Deserialize)]
pub struct IceServer {
    pub urls: Vec<String>,
    pub username: String,
    pub credential: String,
}
#[derive(Clone, Deserialize)]
pub struct IceLease {
    pub expires_at: u64,
    pub servers: Vec<IceServer>,
}

#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum In {
    DeviceChallenge { challenge: DeviceLoginChallenge },
    Ready { host: u32, protocol: u16 },
    Renewed { expires_at: u64 },
    SupportHostStatus { enabled: bool },
    Consent {
        session: Uuid,
        requester_connection: u32,
        requester: Uuid,
        #[serde(default)]
        unattended: bool,
        #[serde(default)]
        requester_identity: Option<DeviceTicket>,
    },
    Accepted {
        session: Uuid,
        nonce: [u8; 32],
        #[serde(default)]
        controller_identity: Option<DeviceTicket>,
        #[serde(default)]
        host_identity: Option<DeviceTicket>,
    },
    Restarted { previous: Uuid, session: Uuid, nonce: [u8; 32], controller_identity: DeviceTicket, host_identity: DeviceTicket },
    PeerRenewed { session: Uuid, identity: DeviceTicket },
    Ice { session: Uuid, lease: IceLease },
    Signal { session: Uuid, payload: Signal },
    Closed { session: Uuid },
    Error {},
    /// Anything a Quick Support host has no use for.
    #[serde(other)]
    Other,
}
