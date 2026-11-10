use serde::{Deserialize, Serialize};
use uuid::Uuid;
use zeroize::Zeroize;

#[derive(Clone, Serialize, Deserialize)]
#[serde(transparent)]
pub struct SupportCode(String);

impl SupportCode {
    pub fn parse(value: String) -> Option<Self> {
        Self(value).valid()
    }
    pub(crate) fn generate() -> Self {
        use rand_core::{OsRng, RngCore};
        let mut bytes = [0u8; 6];
        OsRng.fill_bytes(&mut bytes);
        let result = Self(bytes.iter().map(|byte| format!("{byte:02X}")).collect());
        bytes.zeroize();
        result
    }
    fn valid(self) -> Option<Self> {
        (self.0.len() == 12 && self.0.bytes().all(|byte| byte.is_ascii_hexdigit())).then_some(self)
    }
    pub fn reveal(&self) -> &str {
        &self.0
    }
    pub(crate) fn matches(&self, offered: &Self) -> bool {
        use hmac::{Hmac, Mac};
        use sha1::Sha1;

        if offered.0.len() != self.0.len() {
            return false;
        }
        let mac = |value: &str| {
            let mut result =
                Hmac::<Sha1>::new_from_slice(value.as_bytes()).expect("HMAC accepts any key size");
            result.update(b"Mixel Remote one-time support");
            result
        };
        let mut normalized = offered.0.to_ascii_uppercase();
        let matched = mac(&normalized)
            .verify_slice(&mac(&self.0).finalize().into_bytes())
            .is_ok();
        normalized.zeroize();
        matched
    }
}

impl std::fmt::Debug for SupportCode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("[redacted]")
    }
}

impl Drop for SupportCode {
    fn drop(&mut self) {
        self.0.zeroize();
    }
}

pub const MAX_MESSAGE: usize = 65536;
#[derive(Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ClientMessage {
    Authenticate { token: String },
    AuthenticateDevice { token: String, device: Uuid },
    ProveDevice { signature: Vec<u8> },
    Renew { token: String },
    Request { host: u32 },
    RequestWithCode { host: u32, code: SupportCode },
    RequestUnattended { host: u32 },
    ConfigureUnattended { enabled: bool },
    GenerateSupportCode,
    ClearSupportCode,
    CheckPairing,
    ConfirmPairing { request: Uuid },
    Decide { session: Uuid, accept: bool },
    Signal { session: Uuid, payload: Signal },
    Close { session: Uuid },
    Ice { session: Uuid },
    Restart { session: Uuid },
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
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PairingPrompt {
    pub request_id: Uuid,
    pub provider_name: String,
    pub customer_name: String,
}

#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerMessage {
    Ice {
        session: Uuid,
        lease: crate::turn::IceLease,
    },
    DeviceChallenge {
        challenge: core_protocol::device_login::DeviceLoginChallenge,
    },
    Ready {
        host: u32,
        protocol: u16,
    },
    Renewed {
        expires_at: u64,
    },
    SupportCode {
        required: bool,
        code: Option<SupportCode>,
        lifetime_seconds: u16,
    },
    PairingStatus {
        pending: Option<PairingPrompt>,
        confirmed: bool,
        failed: bool,
    },
    UnattendedStatus {
        enabled: bool,
    },
    Requested {
        session: Uuid,
    },
    Consent {
        session: Uuid,
        requester_connection: u32,
        requester: Uuid,
        requester_tenant: Option<Uuid>,
        #[serde(default)]
        unattended: bool,
        #[serde(default)]
        requester_identity: Option<core_protocol::delegation::DeviceTicket>,
    },
    Accepted {
        session: Uuid,
        nonce: [u8; 32],
        #[serde(default)]
        controller_identity: Option<core_protocol::delegation::DeviceTicket>,
        #[serde(default)]
        host_identity: Option<core_protocol::delegation::DeviceTicket>,
    },
    PeerRenewed {
        session: Uuid,
        identity: core_protocol::delegation::DeviceTicket,
    },
    Restarted {
        previous: Uuid,
        session: Uuid,
        nonce: [u8; 32],
        controller_identity: core_protocol::delegation::DeviceTicket,
        host_identity: core_protocol::delegation::DeviceTicket,
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
