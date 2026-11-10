//! Device-bound delegation separates ISM credentials from the rendezvous
//! broker. Only the independent identity authority holds the ticket signer.
use crate::{crypto::CryptoError, device_login::DeviceLoginChallenge};
use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const MAX_TICKET_LIFETIME: u64 = 900;
pub const MAX_UNATTENDED_HOST_LIFETIME: u64 = 120;
const INTERACTIVE_VERSION: u16 = 1;
const UNATTENDED_HOST_VERSION: u16 = 2;
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DelegationProof {
    pub challenge: DeviceLoginChallenge,
    pub issued_at: u64,
    pub signature: Vec<u8>,
}
impl DelegationProof {
    fn bytes(&self, startup: bool) -> Vec<u8> {
        let domain = if startup {
            b"Mixel Remote unattended startup proof v1\0".as_slice()
        } else {
            b"Mixel Remote ISM device delegation request v1\0".as_slice()
        };
        let mut bytes = domain.to_vec();
        bytes.extend(self.issued_at.to_be_bytes());
        bytes.extend(self.challenge.bytes());
        bytes
    }
    pub fn new(
        challenge: DeviceLoginChallenge,
        issued_at: u64,
        key: &SigningKey,
    ) -> Result<Self, CryptoError> {
        Self::sign(challenge, issued_at, key, false)
    }

    pub fn new_unattended_host(
        challenge: DeviceLoginChallenge,
        issued_at: u64,
        key: &SigningKey,
    ) -> Result<Self, CryptoError> {
        Self::sign(challenge, issued_at, key, true)
    }

    fn sign(
        challenge: DeviceLoginChallenge,
        issued_at: u64,
        key: &SigningKey,
        startup: bool,
    ) -> Result<Self, CryptoError> {
        if !challenge.valid() {
            return Err(CryptoError::Authentication);
        }
        let mut proof = Self {
            challenge,
            issued_at,
            signature: Vec::new(),
        };
        proof.signature = key.sign(&proof.bytes(startup)).to_bytes().to_vec();
        Ok(proof)
    }
    pub fn verify(&self, key: &VerifyingKey, now: u64) -> Result<(), CryptoError> {
        self.verify_scope(key, now, false)
    }

    pub fn verify_unattended_host(&self, key: &VerifyingKey, now: u64) -> Result<(), CryptoError> {
        self.verify_scope(key, now, true)
    }

    fn verify_scope(&self, key: &VerifyingKey, now: u64, startup: bool) -> Result<(), CryptoError> {
        if !self.challenge.valid()
            || self.issued_at > now.saturating_add(5)
            || now.saturating_sub(self.issued_at) > 30
            || key.is_weak()
        {
            return Err(CryptoError::Authentication);
        }
        let signature =
            Signature::from_slice(&self.signature).map_err(|_| CryptoError::Authentication)?;
        key.verify_strict(&self.bytes(startup), &signature)
            .map_err(|_| CryptoError::Authentication)
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeviceTicket {
    pub version: u16,
    pub identity: DeviceLoginChallenge,
    pub public_key: [u8; 32],
    pub staff: bool,
    pub issued_at: u64,
    pub expires_at: u64,
    pub signature: Vec<u8>,
}
impl DeviceTicket {
    pub fn unattended_host_only(&self) -> bool {
        self.version == UNATTENDED_HOST_VERSION
    }

    pub fn fingerprint(&self) -> [u8; 32] {
        let mut hash = Sha256::new();
        hash.update(self.bytes());
        hash.update(&self.signature);
        hash.finalize().into()
    }
    fn bytes(&self) -> Vec<u8> {
        let mut bytes = b"Mixel Remote delegated broker and peer identity v1\0".to_vec();
        bytes.extend(self.version.to_be_bytes());
        bytes.extend(self.identity.bytes());
        bytes.extend(self.public_key);
        bytes.push(u8::from(self.staff));
        bytes.extend(self.issued_at.to_be_bytes());
        bytes.extend(self.expires_at.to_be_bytes());
        bytes
    }
    pub fn issue(
        identity: DeviceLoginChallenge,
        key: VerifyingKey,
        staff: bool,
        issued_at: u64,
        expires_at: u64,
        authority: &SigningKey,
    ) -> Result<Self, CryptoError> {
        Self::issue_version(
            INTERACTIVE_VERSION,
            identity,
            key,
            staff,
            issued_at,
            expires_at,
            authority,
        )
    }

    pub fn issue_unattended_host(
        identity: DeviceLoginChallenge,
        key: VerifyingKey,
        issued_at: u64,
        expires_at: u64,
        authority: &SigningKey,
    ) -> Result<Self, CryptoError> {
        Self::issue_version(
            UNATTENDED_HOST_VERSION,
            identity,
            key,
            false,
            issued_at,
            expires_at,
            authority,
        )
    }

    fn issue_version(
        version: u16,
        identity: DeviceLoginChallenge,
        key: VerifyingKey,
        staff: bool,
        issued_at: u64,
        expires_at: u64,
        authority: &SigningKey,
    ) -> Result<Self, CryptoError> {
        let mut ticket = Self {
            version,
            identity,
            public_key: key.to_bytes(),
            staff,
            issued_at,
            expires_at,
            signature: Vec::new(),
        };
        ticket.validate(issued_at)?;
        ticket.signature = authority.sign(&ticket.bytes()).to_bytes().to_vec();
        Ok(ticket)
    }
    fn validate(&self, now: u64) -> Result<VerifyingKey, CryptoError> {
        let max_lifetime = match self.version {
            INTERACTIVE_VERSION => MAX_TICKET_LIFETIME,
            UNATTENDED_HOST_VERSION if !self.staff => MAX_UNATTENDED_HOST_LIFETIME,
            _ => return Err(CryptoError::Authentication),
        };
        if !self.identity.valid()
            || self.staff != self.identity.tenant.is_none()
            || self.issued_at > now.saturating_add(5)
            || self.expires_at <= now
            || self.expires_at <= self.issued_at
            || self.expires_at.saturating_sub(self.issued_at) > max_lifetime
        {
            return Err(CryptoError::Authentication);
        }
        let key =
            VerifyingKey::from_bytes(&self.public_key).map_err(|_| CryptoError::Authentication)?;
        if key.is_weak() {
            return Err(CryptoError::Authentication);
        }
        Ok(key)
    }
    pub fn verify(
        &self,
        authorities: &[VerifyingKey],
        now: u64,
    ) -> Result<VerifiedIdentity, CryptoError> {
        let key = self.validate(now)?;
        if authorities.is_empty() || authorities.len() > 4 {
            return Err(CryptoError::Authentication);
        }
        let signature =
            Signature::from_slice(&self.signature).map_err(|_| CryptoError::Authentication)?;
        let bytes = self.bytes();
        if !authorities.iter().any(|authority| {
            !authority.is_weak() && authority.verify_strict(&bytes, &signature).is_ok()
        }) {
            return Err(CryptoError::Authentication);
        }
        Ok(VerifiedIdentity {
            ticket: self.clone(),
            key,
        })
    }
}
pub fn session_binding(
    room: [u8; 16],
    nonce: [u8; 32],
    controller: &DeviceTicket,
    host: &DeviceTicket,
) -> [u8; 32] {
    let mut hash = Sha256::new();
    hash.update(b"Mixel Remote identity-bound attended session v1\0");
    hash.update(room);
    hash.update(nonce);
    hash.update(controller.fingerprint());
    hash.update(host.fingerprint());
    hash.finalize().into()
}
/// Opaque proof: parsing an identity claim cannot construct this type.
pub struct VerifiedIdentity {
    ticket: DeviceTicket,
    key: VerifyingKey,
}
impl VerifiedIdentity {
    pub fn claims(&self) -> &DeviceTicket {
        &self.ticket
    }
    pub fn key(&self) -> VerifyingKey {
        self.key
    }
    pub fn can_request(&self, target: &Self) -> bool {
        !self.ticket.unattended_host_only()
            && (self.ticket.staff
                || (self.ticket.identity.tenant.is_some()
                    && self.ticket.identity.tenant == target.ticket.identity.tenant))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn identity(tenant: Option<[u8; 16]>) -> DeviceLoginChallenge {
        DeviceLoginChallenge {
            nonce: [1; 32],
            device: [2; 16],
            profile: [3; 16],
            tenant,
            connection_id: 123_456_789,
            generation: 1,
        }
    }
    #[test]
    fn unattended_host_ticket_is_tenant_bound_short_lived_and_cannot_request() {
        let authority = SigningKey::from_bytes(&[9; 32]);
        let device = SigningKey::from_bytes(&[7; 32]).verifying_key();
        let roots = [authority.verifying_key()];
        let customer = identity(Some([4; 16]));
        let startup =
            DeviceTicket::issue_unattended_host(customer.clone(), device, 100, 220, &authority)
                .unwrap();
        let interactive =
            DeviceTicket::issue(customer.clone(), device, false, 100, 900, &authority)
                .unwrap()
                .verify(&roots, 100)
                .unwrap();
        let verified = startup.verify(&roots, 100).unwrap();
        assert!(verified.claims().unattended_host_only());
        assert!(!verified.can_request(&interactive));
        assert!(interactive.can_request(&verified));
        assert!(startup.verify(&roots, 220).is_err());
        assert!(
            DeviceTicket::issue_unattended_host(customer, device, 100, 221, &authority).is_err()
        );
        assert!(
            DeviceTicket::issue_unattended_host(identity(None), device, 100, 220, &authority)
                .is_err()
        );
        let mut changed = startup;
        changed.version = INTERACTIVE_VERSION;
        assert!(changed.verify(&roots, 100).is_err());
    }
    #[test]
    fn authority_signature_binds_all_claims_and_expiry() {
        let signer = SigningKey::from_bytes(&[9; 32]);
        let device = SigningKey::from_bytes(&[7; 32]);
        let roots = [signer.verifying_key()];
        let ticket = DeviceTicket::issue(
            identity(Some([4; 16])),
            device.verifying_key(),
            false,
            100,
            900,
            &signer,
        )
        .unwrap();
        assert_eq!(
            ticket.verify(&roots, 100).unwrap().key(),
            device.verifying_key()
        );
        assert!(ticket.verify(&roots, 900).is_err());
        assert!(ticket.verify(&roots, 94).is_err());
        assert!(ticket.verify(&[device.verifying_key()], 100).is_err());
        let mut variants = Vec::new();
        let mut v = ticket.clone();
        v.identity.nonce[0] ^= 1;
        variants.push(v);
        let mut v = ticket.clone();
        v.identity.device[0] ^= 1;
        variants.push(v);
        let mut v = ticket.clone();
        v.identity.profile[0] ^= 1;
        variants.push(v);
        let mut v = ticket.clone();
        v.identity.tenant = Some([5; 16]);
        variants.push(v);
        let mut v = ticket.clone();
        v.identity.connection_id += 1;
        variants.push(v);
        let mut v = ticket.clone();
        v.identity.generation += 1;
        variants.push(v);
        let mut v = ticket.clone();
        v.public_key = signer.verifying_key().to_bytes();
        variants.push(v);
        let mut v = ticket.clone();
        v.staff = true;
        v.identity.tenant = None;
        variants.push(v);
        let mut v = ticket.clone();
        v.expires_at -= 1;
        variants.push(v);
        let mut v = ticket.clone();
        v.issued_at += 1;
        variants.push(v);
        let mut v = ticket.clone();
        v.version += 1;
        variants.push(v);
        for v in variants {
            assert!(v.verify(&roots, 100).is_err());
        }
        assert!(
            DeviceTicket::issue(
                identity(Some([4; 16])),
                device.verifying_key(),
                true,
                100,
                900,
                &signer
            )
            .is_err()
        );
        assert!(
            DeviceTicket::issue(
                identity(None),
                device.verifying_key(),
                true,
                100,
                1001,
                &signer
            )
            .is_err()
        );
    }
    #[test]
    fn separate_delegation_proof_cannot_be_reused_for_device_login() {
        let key = SigningKey::from_bytes(&[7; 32]);
        let proof = DelegationProof::new(identity(Some([4; 16])), 100, &key).unwrap();
        proof.verify(&key.verifying_key(), 130).unwrap();
        assert!(proof.verify(&key.verifying_key(), 131).is_err());
        assert!(proof.verify(&key.verifying_key(), 94).is_err());
        assert!(
            proof
                .challenge
                .verify(&key.verifying_key(), &proof.signature)
                .is_err()
        );
        let mut changed = proof.clone();
        changed.issued_at += 1;
        assert!(changed.verify(&key.verifying_key(), 100).is_err());
        let login = proof.challenge.sign(&key).unwrap();
        let mut changed = proof;
        changed.signature = login;
        assert!(changed.verify(&key.verifying_key(), 100).is_err());
    }
    #[test]
    fn tenant_authorization_and_transcript_are_independent_of_broker() {
        let signer = SigningKey::from_bytes(&[9; 32]);
        let device = SigningKey::from_bytes(&[7; 32]);
        let roots = [signer.verifying_key()];
        let make = |tenant, staff| {
            DeviceTicket::issue(
                identity(tenant),
                device.verifying_key(),
                staff,
                100,
                900,
                &signer,
            )
            .unwrap()
        };
        let customer = make(Some([4; 16]), false);
        let other = make(Some([5; 16]), false);
        let staff = make(None, true);
        let cv = customer.verify(&roots, 100).unwrap();
        let ov = other.verify(&roots, 100).unwrap();
        let sv = staff.verify(&roots, 100).unwrap();
        assert!(cv.can_request(&cv));
        assert!(!cv.can_request(&ov));
        assert!(!cv.can_request(&sv));
        assert!(sv.can_request(&ov));
        let binding = session_binding([6; 16], [7; 32], &staff, &customer);
        assert_ne!(
            binding,
            session_binding([8; 16], [7; 32], &staff, &customer)
        );
        assert_ne!(
            binding,
            session_binding([6; 16], [8; 32], &staff, &customer)
        );
        assert_ne!(
            binding,
            session_binding([6; 16], [7; 32], &customer, &staff)
        );
        assert_ne!(binding, session_binding([6; 16], [7; 32], &staff, &other));
    }
}
