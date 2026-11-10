//! Authenticated ephemeral X25519 and directional AES-256-GCM control records.
//!
//! Expected identity keys MUST come from authenticated device enrollment or an
//! independently verified pairing. Accepting a key supplied by signaling alone
//! would allow a signaling-server MITM. DTLS-SRTP remains the media transport.
use aes_gcm::{
    Aes256Gcm, KeyInit, Nonce,
    aead::{Aead, Payload},
};
use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};
use hkdf::Hkdf;
use rand_core::OsRng;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;
use x25519_dalek::{EphemeralSecret, PublicKey};
use zeroize::Zeroizing;

const MAX_PLAINTEXT: usize = 4096;
const HEADER: usize = 8;
const TAG: usize = 16;
// Conservative per-direction key lifetime; reconnect establishes new ephemeral
// keys long before nonce space exhaustion.
const MAX_RECORDS: u64 = 1 << 32;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    Controller,
    Host,
}
impl Role {
    fn byte(self) -> u8 {
        if self == Self::Controller { 0 } else { 1 }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Hello {
    pub role: Role,
    pub ephemeral: [u8; 32],
    pub dtls_fingerprint: [u8; 32],
    #[serde(with = "signature_wire")]
    pub signature: [u8; 64],
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum CryptoError {
    #[error("peer authentication failed")]
    Authentication,
    #[error("invalid, replayed or reordered control record")]
    Record,
    #[error("control key exhausted; reconnect required")]
    Exhausted,
}

/// Binds both enrolled device identities to one random 256-bit session identifier.
#[derive(Clone, PartialEq, Eq)]
pub struct Context {
    pub session_id: [u8; 32],
    pub controller: VerifyingKey,
    pub host: VerifyingKey,
}
impl Context {
    fn bytes(&self) -> Vec<u8> {
        let mut out = b"Mixel Remote control v1\0".to_vec();
        out.extend(self.session_id);
        out.extend(self.controller.as_bytes());
        out.extend(self.host.as_bytes());
        out
    }
    fn signing_bytes(&self, hello: &Hello) -> Vec<u8> {
        let mut out = self.bytes();
        out.push(hello.role.byte());
        out.extend(hello.ephemeral);
        out.extend(hello.dtls_fingerprint);
        out
    }
}

/// Non-serializable proof created only by a successful signed handshake.
/// It binds the context, endpoint role, ephemeral exchange and both certificates.
#[derive(Clone, PartialEq, Eq)]
pub struct VerifiedSession {
    context: Context,
    role: Role,
    local_fingerprint: [u8; 32],
    peer_fingerprint: [u8; 32],
    binding: [u8; 32],
}
impl VerifiedSession {
    pub(crate) fn matches(&self, context: &Context, role: Role) -> bool {
        self.context == *context && self.role == role
    }
    /// Public, handshake-authenticated enrollment identities; no secret key material.
    pub fn identities(&self) -> ([u8; 32], [u8; 32]) {
        (
            self.context.controller.to_bytes(),
            self.context.host.to_bytes(),
        )
    }
    pub fn local_role(&self) -> Role {
        self.role
    }
    pub fn fingerprints(&self) -> ([u8; 32], [u8; 32]) {
        (self.local_fingerprint, self.peer_fingerprint)
    }
}

pub struct Handshake {
    secret: EphemeralSecret,
    local: Hello,
    context: Context,
}
impl Handshake {
    pub fn new(
        context: Context,
        role: Role,
        identity: &SigningKey,
        fingerprint: [u8; 32],
    ) -> Result<Self, CryptoError> {
        let expected = match role {
            Role::Controller => context.controller,
            Role::Host => context.host,
        };
        if identity.verifying_key() != expected
            || fingerprint == [0; 32]
            || context.session_id == [0; 32]
        {
            return Err(CryptoError::Authentication);
        }
        let secret = EphemeralSecret::random_from_rng(OsRng);
        let mut local = Hello {
            role,
            ephemeral: PublicKey::from(&secret).to_bytes(),
            dtls_fingerprint: fingerprint,
            signature: [0; 64],
        };
        local.signature = identity.sign(&context.signing_bytes(&local)).to_bytes();
        Ok(Self {
            secret,
            local,
            context,
        })
    }
    pub fn hello(&self) -> Hello {
        self.local.clone()
    }
    /// The fingerprint argument is observed from the actual established DTLS peer
    /// certificate, never copied from the received Hello.
    pub fn finish(
        self,
        peer: &Hello,
        observed_fingerprint: [u8; 32],
    ) -> Result<ControlChannel, CryptoError> {
        if peer.role == self.local.role
            || peer.dtls_fingerprint != observed_fingerprint
            || observed_fingerprint == [0; 32]
        {
            return Err(CryptoError::Authentication);
        }
        let identity = match peer.role {
            Role::Controller => self.context.controller,
            Role::Host => self.context.host,
        };
        identity
            .verify_strict(
                &self.context.signing_bytes(peer),
                &Signature::from_bytes(&peer.signature),
            )
            .map_err(|_| CryptoError::Authentication)?;
        let shared = self.secret.diffie_hellman(&PublicKey::from(peer.ephemeral));
        if !shared.was_contributory() {
            return Err(CryptoError::Authentication);
        }
        let (controller, host) = if self.local.role == Role::Controller {
            (&self.local, peer)
        } else {
            (peer, &self.local)
        };
        let mut transcript = self.context.bytes();
        transcript.extend(self.context.signing_bytes(controller));
        transcript.extend(self.context.signing_bytes(host));
        let binding: [u8; 32] = Sha256::digest(&transcript).into();
        let hkdf = Hkdf::<Sha256>::new(Some(&binding), shared.as_bytes());
        let verified = VerifiedSession {
            context: self.context,
            role: self.local.role,
            local_fingerprint: self.local.dtls_fingerprint,
            peer_fingerprint: peer.dtls_fingerprint,
            binding,
        };
        let mut control = ControlChannel::derived(
            &hkdf,
            b"Mixel Remote directional AES256GCM v1",
            binding,
            verified.clone(),
        )?;
        // Separate directional keys, AAD and counters isolate bulk data from
        // keyboard/permission traffic. Retain no exportable shared secret.
        let bulk_binding =
            Sha256::digest([binding.as_slice(), b"Mixel Remote bulk records v1"].concat()).into();
        control.bulk = Some(Box::new(ControlChannel::derived(
            &hkdf,
            b"Mixel Remote bulk directional AES256GCM v1",
            bulk_binding,
            verified.clone(),
        )?));
        let chat_binding =
            Sha256::digest([binding.as_slice(), b"Mixel Remote chat records v1"].concat()).into();
        control.chat = Some(Box::new(ControlChannel::derived(
            &hkdf,
            b"Mixel Remote chat directional AES256GCM v1",
            chat_binding,
            verified,
        )?));
        Ok(control)
    }
}

/// Ordered reliable DataChannel records. A failed authentication never advances
/// the receive sequence. Loss/reorder closes/reconnects the control transport.
pub struct ControlChannel {
    tx: Aes256Gcm,
    rx: Aes256Gcm,
    tx_sequence: u64,
    rx_sequence: u64,
    binding: [u8; 32],
    verified: VerifiedSession,
    bulk: Option<Box<ControlChannel>>,
    chat: Option<Box<ControlChannel>>,
}
fn nonce(sequence: u64) -> [u8; 12] {
    let mut bytes = [0; 12];
    bytes[4..].copy_from_slice(&sequence.to_be_bytes());
    bytes
}
impl ControlChannel {
    fn derived(
        hkdf: &Hkdf<Sha256>,
        label: &[u8],
        binding: [u8; 32],
        verified: VerifiedSession,
    ) -> Result<Self, CryptoError> {
        let mut material = Zeroizing::new([0u8; 64]);
        hkdf.expand(label, material.as_mut())
            .map_err(|_| CryptoError::Authentication)?;
        let first =
            Aes256Gcm::new_from_slice(&material[..32]).map_err(|_| CryptoError::Authentication)?;
        let second =
            Aes256Gcm::new_from_slice(&material[32..]).map_err(|_| CryptoError::Authentication)?;
        let (tx, rx) = if verified.role == Role::Controller {
            (first, second)
        } else {
            (second, first)
        };
        Ok(Self {
            tx,
            rx,
            tx_sequence: 0,
            rx_sequence: 0,
            binding,
            verified,
            bulk: None,
            chat: None,
        })
    }
    /// May be taken exactly once from the authenticated parent. Repeated calls
    /// cannot reset a key's nonce sequence or create a second bulk sender.
    /// Possession does not grant file access; the native host grant still gates
    /// the bulk protocol and every OS operation.
    pub fn take_bulk_channel(&mut self) -> Result<Self, CryptoError> {
        self.bulk
            .take()
            .map(|channel| *channel)
            .ok_or(CryptoError::Exhausted)
    }
    /// Independent keys and counters for the optional in-session chat stream.
    pub fn take_chat_channel(&mut self) -> Result<Self, CryptoError> {
        self.chat
            .take()
            .map(|channel| *channel)
            .ok_or(CryptoError::Exhausted)
    }
    pub fn verified_session(&self) -> VerifiedSession {
        self.verified.clone()
    }
    pub fn seal(&mut self, plaintext: &[u8]) -> Result<Vec<u8>, CryptoError> {
        if plaintext.len() > MAX_PLAINTEXT {
            return Err(CryptoError::Record);
        }
        if self.tx_sequence >= MAX_RECORDS {
            return Err(CryptoError::Exhausted);
        }
        let next = self
            .tx_sequence
            .checked_add(1)
            .ok_or(CryptoError::Exhausted)?;
        let mut record = self.tx_sequence.to_be_bytes().to_vec();
        let mut aad = self.binding.to_vec();
        aad.extend(&record);
        let encrypted = self
            .tx
            .encrypt(
                Nonce::from_slice(&nonce(self.tx_sequence)),
                Payload {
                    msg: plaintext,
                    aad: &aad,
                },
            )
            .map_err(|_| CryptoError::Record)?;
        record.extend(encrypted);
        self.tx_sequence = next;
        Ok(record)
    }
    pub fn open(&mut self, record: &[u8]) -> Result<Vec<u8>, CryptoError> {
        if !(HEADER + TAG..=HEADER + TAG + MAX_PLAINTEXT).contains(&record.len()) {
            return Err(CryptoError::Record);
        }
        let sequence = u64::from_be_bytes(
            record[..HEADER]
                .try_into()
                .map_err(|_| CryptoError::Record)?,
        );
        if sequence != self.rx_sequence {
            return Err(CryptoError::Record);
        }
        if sequence >= MAX_RECORDS {
            return Err(CryptoError::Exhausted);
        }
        let next = sequence.checked_add(1).ok_or(CryptoError::Exhausted)?;
        let mut aad = self.binding.to_vec();
        aad.extend(&record[..HEADER]);
        let plaintext = self
            .rx
            .decrypt(
                Nonce::from_slice(&nonce(sequence)),
                Payload {
                    msg: &record[HEADER..],
                    aad: &aad,
                },
            )
            .map_err(|_| CryptoError::Record)?;
        self.rx_sequence = next;
        Ok(plaintext)
    }
}

// Fixed-size decoding rejects short/long signatures without allocating a Vec.
mod signature_wire {
    use serde::{
        Deserializer, Serializer,
        de::{Error, SeqAccess, Visitor},
        ser::SerializeTuple,
    };
    pub fn serialize<S: Serializer>(bytes: &[u8; 64], serializer: S) -> Result<S::Ok, S::Error> {
        let mut tuple = serializer.serialize_tuple(64)?;
        for byte in bytes {
            tuple.serialize_element(byte)?;
        }
        tuple.end()
    }
    pub fn deserialize<'de, D: Deserializer<'de>>(deserializer: D) -> Result<[u8; 64], D::Error> {
        struct SignatureVisitor;
        impl<'de> Visitor<'de> for SignatureVisitor {
            type Value = [u8; 64];
            fn expecting(&self, fmt: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                fmt.write_str("exactly 64 signature bytes")
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Self::Value, A::Error> {
                let mut bytes = [0; 64];
                for (i, byte) in bytes.iter_mut().enumerate() {
                    *byte = seq
                        .next_element()?
                        .ok_or_else(|| A::Error::invalid_length(i, &self))?;
                }
                if seq.next_element::<u8>()?.is_some() {
                    return Err(A::Error::invalid_length(65, &self));
                }
                Ok(bytes)
            }
        }
        deserializer.deserialize_tuple(64, SignatureVisitor)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn keys() -> (SigningKey, SigningKey, Context) {
        let a = SigningKey::generate(&mut OsRng);
        let b = SigningKey::generate(&mut OsRng);
        let context = Context {
            session_id: [41; 32],
            controller: a.verifying_key(),
            host: b.verifying_key(),
        };
        (a, b, context)
    }
    #[test]
    fn signed_low_order_key_is_refused_and_key_lifetime_is_bounded() {
        let (a, b, context) = keys();
        let ah = Handshake::new(context.clone(), Role::Controller, &a, [1; 32]).unwrap();
        let bh = Handshake::new(context.clone(), Role::Host, &b, [2; 32]).unwrap();
        let mut hello = bh.hello();
        hello.ephemeral = [0; 32];
        hello.signature = b.sign(&context.signing_bytes(&hello)).to_bytes();
        assert!(matches!(
            ah.finish(&hello, [2; 32]),
            Err(CryptoError::Authentication)
        ));
        let ah = Handshake::new(context, Role::Controller, &a, [1; 32]).unwrap();
        let mut channel = ah.finish(&bh.hello(), [2; 32]).unwrap();
        channel.tx_sequence = MAX_RECORDS;
        assert_eq!(channel.seal(b"input"), Err(CryptoError::Exhausted));
        channel.rx_sequence = MAX_RECORDS;
        let mut record = MAX_RECORDS.to_be_bytes().to_vec();
        record.extend([0; 16]);
        assert_eq!(channel.open(&record), Err(CryptoError::Exhausted));
    }
    #[test]
    fn bulk_keys_and_counters_are_isolated_and_cannot_be_forked_twice() {
        let (a, b, context) = keys();
        let ah = Handshake::new(context.clone(), Role::Controller, &a, [1; 32]).unwrap();
        let bh = Handshake::new(context, Role::Host, &b, [2; 32]).unwrap();
        let hello = ah.hello();
        let mut controller = ah.finish(&bh.hello(), [2; 32]).unwrap();
        let mut host = bh.finish(&hello, [1; 32]).unwrap();
        let mut cf = controller.take_bulk_channel().unwrap();
        let mut hf = host.take_bulk_channel().unwrap();
        assert!(controller.take_bulk_channel().is_err());
        assert!(cf.take_bulk_channel().is_err());
        let input = controller.seal(b"same bytes").unwrap();
        let file = cf.seal(b"same bytes").unwrap();
        assert_ne!(input, file);
        assert!(host.open(&file).is_err());
        assert!(hf.open(&input).is_err());
        assert_eq!(host.open(&input).unwrap(), b"same bytes");
        assert_eq!(hf.open(&file).unwrap(), b"same bytes");
        assert!(hf.open(&file).is_err());
        let reply = hf.seal(b"file acknowledgment").unwrap();
        assert_eq!(cf.open(&reply).unwrap(), b"file acknowledgment");
        assert_eq!(
            cf.verified_session().fingerprints(),
            controller.verified_session().fingerprints()
        );
    }
}
