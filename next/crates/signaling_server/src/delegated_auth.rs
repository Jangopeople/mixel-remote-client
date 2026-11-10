//! Production rendezvous accepts only independently signed device tickets,
//! never the ISM administrator bearer capable of changing enrollment keys.
use crate::{
    auth::{AuthError, EnrolledPrincipal, Principal},
    server::Authenticator,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use core_protocol::delegation::{DeviceTicket, VerifiedIdentity};
use ed25519_dalek::VerifyingKey;
use std::{
    future::Future,
    pin::Pin,
    time::{SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

pub fn encode(ticket: &DeviceTicket) -> Result<String, AuthError> {
    let bytes = serde_json::to_vec(ticket).map_err(|_| AuthError)?;
    if bytes.len() > 4096 {
        return Err(AuthError);
    }
    Ok(URL_SAFE_NO_PAD.encode(bytes))
}
pub fn verify(
    encoded: &str,
    roots: &[VerifyingKey],
    now: u64,
) -> Result<VerifiedIdentity, AuthError> {
    if encoded.is_empty() || encoded.len() > 8192 {
        return Err(AuthError);
    }
    let bytes = URL_SAFE_NO_PAD.decode(encoded).map_err(|_| AuthError)?;
    if bytes.len() > 4096 || URL_SAFE_NO_PAD.encode(&bytes) != encoded {
        return Err(AuthError);
    }
    let ticket: DeviceTicket = serde_json::from_slice(&bytes).map_err(|_| AuthError)?;
    ticket.verify(roots, now).map_err(|_| AuthError)
}
pub fn public_keys(encoded: &str) -> Result<Vec<VerifyingKey>, AuthError> {
    let mut roots = Vec::new();
    for value in encoded.split(',').map(str::trim) {
        if value.len() != 64
            || !value.bytes().all(|byte| byte.is_ascii_hexdigit())
            || roots.len() >= 4
        {
            return Err(AuthError);
        }
        let mut bytes = [0; 32];
        for (index, byte) in bytes.iter_mut().enumerate() {
            *byte =
                u8::from_str_radix(&value[index * 2..index * 2 + 2], 16).map_err(|_| AuthError)?;
        }
        let key = VerifyingKey::from_bytes(&bytes).map_err(|_| AuthError)?;
        if key.is_weak() || roots.contains(&key) {
            return Err(AuthError);
        }
        roots.push(key);
    }
    if roots.is_empty() {
        return Err(AuthError);
    }
    Ok(roots)
}
#[derive(Clone)]
pub struct TicketAuth {
    roots: Vec<VerifyingKey>,
}
impl TicketAuth {
    pub fn new(roots: Vec<VerifyingKey>) -> Result<Self, AuthError> {
        if roots.is_empty() || roots.len() > 4 || roots.iter().any(VerifyingKey::is_weak) {
            return Err(AuthError);
        }
        Ok(Self { roots })
    }
}
impl Authenticator for TicketAuth {
    fn authenticate<'a>(
        &'a self,
        _token: &'a str,
    ) -> Pin<Box<dyn Future<Output = Result<Principal, AuthError>> + Send + 'a>> {
        Box::pin(async { Err(AuthError) })
    }
    fn authenticate_device<'a>(
        &'a self,
        token: &'a str,
        device: Uuid,
    ) -> Pin<Box<dyn Future<Output = Result<EnrolledPrincipal, AuthError>> + Send + 'a>> {
        Box::pin(async move {
            let now = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(|_| AuthError)?
                .as_secs();
            let verified = verify(token, &self.roots, now)?;
            let ticket = verified.claims();
            if ticket.identity.device != *device.as_bytes() {
                return Err(AuthError);
            }
            Ok(EnrolledPrincipal {
                principal: Principal {
                    user: Uuid::from_bytes(ticket.identity.profile),
                    tenant: ticket.identity.tenant.map(Uuid::from_bytes),
                    staff: ticket.staff,
                    expires_at: ticket.expires_at,
                },
                device,
                connection_id: ticket.identity.connection_id,
                generation: ticket.identity.generation,
                public_key: verified.key(),
                identity_ticket: Some(ticket.clone()),
            })
        })
    }
}
