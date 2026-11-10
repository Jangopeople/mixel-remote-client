//! Domain-separated device login proof. The verifier must resolve the public key
//! from authenticated enrollment, issue a fresh nonce, enforce a short deadline,
//! and consume each challenge once. This signature alone grants no session consent.
use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeviceLoginChallenge {
    pub nonce: [u8; 32],
    pub device: [u8; 16],
    pub profile: [u8; 16],
    pub tenant: Option<[u8; 16]>,
    pub connection_id: u32,
    pub generation: u64,
}
impl DeviceLoginChallenge {
    pub(crate) fn bytes(&self) -> Vec<u8> {
        let mut bytes = b"Mixel Remote enrolled device login v1\0".to_vec();
        bytes.extend(self.nonce);
        bytes.extend(self.device);
        bytes.extend(self.profile);
        bytes.push(u8::from(self.tenant.is_some()));
        bytes.extend(self.tenant.unwrap_or_default());
        bytes.extend(self.connection_id.to_be_bytes());
        bytes.extend(self.generation.to_be_bytes());
        bytes
    }
    pub fn valid(&self) -> bool {
        self.nonce != [0; 32]
            && self.device != [0; 16]
            && self.profile != [0; 16]
            && self.tenant != Some([0; 16])
            && (100_000_000..=999_999_999).contains(&self.connection_id)
            && self.generation > 0
    }
    pub fn sign(&self, key: &SigningKey) -> Result<Vec<u8>, crate::crypto::CryptoError> {
        if !self.valid() {
            return Err(crate::crypto::CryptoError::Authentication);
        }
        Ok(key.sign(&self.bytes()).to_bytes().to_vec())
    }
    pub fn verify(
        &self,
        key: &VerifyingKey,
        signature: &[u8],
    ) -> Result<(), crate::crypto::CryptoError> {
        if !self.valid() || key.is_weak() {
            return Err(crate::crypto::CryptoError::Authentication);
        }
        let signature = Signature::from_slice(signature)
            .map_err(|_| crate::crypto::CryptoError::Authentication)?;
        key.verify_strict(&self.bytes(), &signature)
            .map_err(|_| crate::crypto::CryptoError::Authentication)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn proof_binds_every_enrollment_and_login_field() {
        let key = SigningKey::from_bytes(&[7; 32]);
        let challenge = DeviceLoginChallenge {
            nonce: [1; 32],
            device: [2; 16],
            profile: [3; 16],
            tenant: Some([4; 16]),
            connection_id: 123456789,
            generation: 1,
        };
        let signature = challenge.sign(&key).unwrap();
        challenge.verify(&key.verifying_key(), &signature).unwrap();
        let mut variants = Vec::new();
        let mut v = challenge.clone();
        v.nonce[0] ^= 1;
        variants.push(v);
        let mut v = challenge.clone();
        v.device[0] ^= 1;
        variants.push(v);
        let mut v = challenge.clone();
        v.profile[0] ^= 1;
        variants.push(v);
        let mut v = challenge.clone();
        v.tenant = None;
        variants.push(v);
        let mut v = challenge.clone();
        v.connection_id += 1;
        variants.push(v);
        let mut v = challenge.clone();
        v.generation += 1;
        variants.push(v);
        for variant in variants {
            assert!(variant.verify(&key.verifying_key(), &signature).is_err());
        }
        assert!(
            challenge
                .verify(
                    &SigningKey::from_bytes(&[8; 32]).verifying_key(),
                    &signature
                )
                .is_err()
        );
        assert!(
            challenge
                .verify(&key.verifying_key(), &signature[..63])
                .is_err()
        );
    }
}
