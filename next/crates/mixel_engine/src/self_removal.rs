//! Removing this computer from Mixel Remote: the request is signed with this computer's own
//! device key, saved privately and sent; offline it stays queued and is sent later.
use signaling_server::auth::saas::{SaasError, SelfRemoval};

/// "removed": done (or already removed); "queued": saved and sent when Mixel Remote is reachable;
/// "stale": the registration changed since (nothing was removed).
#[derive(serde::Serialize, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum SelfRemovalStatus {
    Removed,
    Queued,
    Stale,
}

/// What to do with a saved request after one delivery attempt.
#[derive(Debug, PartialEq, Eq)]
pub enum Delivery {
    /// A definite answer: the saved request is deleted.
    Done(Result<SelfRemovalStatus, String>),
    /// No definite answer (offline, server trouble, rate limit): kept and sent again later.
    Keep,
}

pub fn delivery(outcome: Result<bool, SaasError>) -> Delivery {
    match outcome {
        Ok(true) => Delivery::Done(Ok(SelfRemovalStatus::Removed)),
        Ok(false) => Delivery::Done(Ok(SelfRemovalStatus::Stale)),
        // 408 timeout, 425 too early, 429 rate limited: the request was not judged; try again.
        Err(error) if matches!(error.status, 0 | 408 | 425 | 429) || error.status >= 500 => Delivery::Keep,
        Err(error) => Delivery::Done(Err(error.message)),
    }
}

/// Saves the request so that a crash never leaves a half-written file behind (which would be
/// discarded as unreadable) and, on Unix, readable by this account only.
pub fn save_pending(path: &std::path::Path, json: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    let temporary = path.with_extension("json.tmp");
    let _ = std::fs::remove_file(&temporary);
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
    let written = options.open(&temporary).and_then(|mut file| {
        file.write_all(json)?;
        file.sync_all()
    });
    match written.and_then(|()| std::fs::rename(&temporary, path)) {
        Ok(()) => Ok(()),
        Err(error) => {
            let _ = std::fs::remove_file(&temporary);
            Err(error)
        }
    }
}

/// Sends a saved request. Network trouble keeps it for later; a definite answer clears it.
pub async fn deliver(path: &std::path::Path, request: &SelfRemoval) -> Result<SelfRemovalStatus, String> {
    let config = crate::service_config::ServiceConfig::load()?;
    let base = config.identity_url.parse().map_err(|_| "Invalid identity service configuration")?;
    let ism = signaling_server::auth::IsmAuth::new(base, config.public_key)
        .map_err(|_| "Invalid identity service configuration")?;
    match delivery(ism.device_self_remove(request).await) {
        Delivery::Keep => Ok(SelfRemovalStatus::Queued),
        Delivery::Done(result) => {
            let _ = std::fs::remove_file(path);
            result
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use uuid::Uuid;

    fn refused(status: u16) -> Result<bool, SaasError> {
        Err(SaasError { status, code: None, message: format!("HTTP {status}") })
    }

    #[test]
    fn only_definite_answers_clear_a_saved_self_removal() {
        assert_eq!(delivery(Ok(true)), Delivery::Done(Ok(SelfRemovalStatus::Removed)));
        assert_eq!(delivery(Ok(false)), Delivery::Done(Ok(SelfRemovalStatus::Stale)));
        // Offline, server trouble, timeouts and rate limits: kept and sent again later.
        for status in [0, 408, 425, 429, 500, 502, 503] {
            assert_eq!(delivery(refused(status)), Delivery::Keep, "{status}");
        }
        // The database refused it (bad signature, too old): deleted, the person sees why.
        assert_eq!(delivery(refused(400)), Delivery::Done(Err("HTTP 400".into())));
        assert_eq!(delivery(refused(403)), Delivery::Done(Err("HTTP 403".into())));
    }

    #[test]
    fn a_saved_self_removal_replaces_atomically_and_is_private() {
        let directory = std::env::temp_dir().join(format!("mixel-self-removal-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&directory).unwrap();
        let path = directory.join("pending-self-removal.json");
        save_pending(&path, b"{\"first\":1}").unwrap();
        save_pending(&path, b"{\"second\":2}").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"{\"second\":2}");
        // No temporary file is left next to it.
        assert_eq!(std::fs::read_dir(&directory).unwrap().count(), 1);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        }
        std::fs::remove_dir_all(&directory).unwrap();
    }
}
