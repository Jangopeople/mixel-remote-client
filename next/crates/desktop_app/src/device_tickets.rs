//! The full ISM bearer goes only to its trusted identity authority. The broker
//! receives a short-lived device-bound ticket with no ISM API privileges.
use crate::{
    device_identity::IdentityState, account_auth::Credential, service_config::ServiceConfig,
    remote_recovery::Failure,
};
use core_protocol::{delegation::DeviceTicket, device_login::DeviceLoginChallenge};
use futures_util::StreamExt;
use rand_core::{OsRng, RngCore};
use signaling_server::auth::EnrolledPrincipal;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use zeroize::Zeroizing;

pub async fn issue(
    credential: &Credential,
    enrolled: &EnrolledPrincipal,
    identity: &IdentityState,
) -> Result<DeviceTicket, String> {
    issue_scoped(Some(credential), enrolled, identity)
        .await
        .map_err(|error| error.message)
}

#[cfg(target_os = "macos")]
pub async fn issue_startup(
    enrolled: &EnrolledPrincipal,
    identity: &IdentityState,
) -> Result<DeviceTicket, Failure> {
    issue_scoped(None, enrolled, identity).await
}

async fn issue_scoped(
    credential: Option<&Credential>,
    enrolled: &EnrolledPrincipal,
    identity: &IdentityState,
) -> Result<DeviceTicket, Failure> {
    let config = ServiceConfig::load()?;
    let now = unix_now()?;
    let mut nonce = [0; 32];
    OsRng
        .try_fill_bytes(&mut nonce)
        .map_err(|_| "Secure device randomness is unavailable")?;
    let challenge = DeviceLoginChallenge {
        nonce,
        device: *enrolled.device.as_bytes(),
        profile: *enrolled.principal.user.as_bytes(),
        tenant: enrolled.principal.tenant.map(|value| *value.as_bytes()),
        connection_id: enrolled.connection_id,
        generation: enrolled.generation,
    };
    let key = identity.public_key()?;
    let base: reqwest::Url = config
        .authority_url
        .parse()
        .map_err(|_| "Invalid identity authority configuration")?;
    let http = reqwest::Client::builder()
        .timeout(Duration::from_secs(20))
        .redirect(reqwest::redirect::Policy::none());
    #[cfg(target_os = "macos")]
    let http = if credential.is_none()
        && matches!(
            native_os_capture::macos_bundle::identifier().as_deref(),
            Some("ch.mixel.remote.next.qa" | "ch.mixel.remote.prelogin.qa")
        )
        && let Some(pem) = option_env!("MIXEL_STARTUP_LAB_CA_PEM")
    {
        http.add_root_certificate(
            reqwest::Certificate::from_pem(pem.as_bytes())
                .map_err(|_| "Invalid signed QA lab certificate")?,
        )
    } else { http };
    let http = http
        .build()
        .map_err(|_| "Cannot initialize secure device authorization")?;
    let endpoint = |path| {
        base.join(path)
            .map_err(|_| "Invalid device authorization endpoint")
    };
    let request = if let Some(credential) = credential {
        let proof = identity.delegate(challenge.clone(), now)?;
        http.post(endpoint("/v1/device-ticket")?)
            .bearer_auth(credential.token.as_str())
            .json(&proof)
    } else {
        #[cfg(not(target_os = "macos"))]
        return Err("Interactive account authorization is unavailable".into());
        #[cfg(target_os = "macos")]
        let proof = identity.delegate_startup(challenge.clone(), now)?;
        #[cfg(target_os = "macos")]
        http.post(endpoint("/v1/startup-host-ticket")?)
            .json(&serde_json::json!({"proof":proof,"public_key":key.to_bytes()}))
    };
    let response = request.send().await.map_err(|error| {
        if credential.is_none() && crate::remote_recovery::retryable_request(&error) {
            Failure::network("The device identity authority is temporarily unavailable")
        } else {
            "The device identity authority is unavailable".into()
        }
    })?;
    if credential.is_none() && matches!(response.status().as_u16(), 502..=504) {
        return Err(Failure::network(
            "The device identity authority is temporarily unavailable",
        ));
    }
    if !response.status().is_success() {
        return Err("The identity authority did not authorize this account and device".into());
    }
    let mut bytes = Zeroizing::new(Vec::new());
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| "Device authorization response interrupted")?;
        if bytes.len() + chunk.len() > 8192 {
            return Err("Device authorization response exceeds its limit".into());
        }
        bytes.extend_from_slice(&chunk);
    }
    let ticket: DeviceTicket =
        serde_json::from_slice(&bytes).map_err(|_| "Invalid signed device authorization")?;
    let verified = ticket
        .verify(&config.roots()?, unix_now()?)
        .map_err(|_| "The device authority signature or lifetime is invalid")?;
    if verified.key() != key
        || ticket.identity != challenge
        || ticket.unattended_host_only() != credential.is_none()
        || ticket.staff != enrolled.principal.staff
        || credential.is_some_and(|value| ticket.expires_at > value.principal.expires_at)
    {
        return Err("The signed device identity does not match this account and enrollment".into());
    }
    Ok(ticket)
}

fn unix_now() -> Result<u64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|time| time.as_secs())
        .map_err(|_| "System clock is unavailable".into())
}
