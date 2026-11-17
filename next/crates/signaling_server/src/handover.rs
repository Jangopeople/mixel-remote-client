//! Running sessions across a broker restart (contract 4.4). At shutdown the broker writes the
//! connected sessions (`registry::Handover`: connection IDs, account and device IDs, limits; no
//! passwords, tokens or keys) to `MIXEL_HANDOVER_FILE`; the next broker reads the file once at start,
//! deletes it and keeps each session for `RESUME_WINDOW` until both sides send `resume`.
use crate::registry::{Handover, Registry};
use std::{
    io::Write,
    path::Path,
    sync::{Arc, Mutex},
    time::{Instant, SystemTime, UNIX_EPOCH},
};

/// Largest handover file read (far above any broker's sessions).
const MAX_FILE: u64 = 16 * 1024 * 1024;

fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |time| time.as_secs())
}

/// Saves the running sessions and stops every connection. Returns how many sessions were saved.
/// When the file cannot be written the sessions simply end (as without a handover).
pub fn save(path: &Path, registry: &Arc<Mutex<Registry>>) -> usize {
    let Ok(mut registry) = registry.lock() else {
        return 0;
    };
    let handover = registry.shutdown_for_handover(Instant::now(), unix_now());
    drop(registry);
    let count = handover.sessions.len();
    let Ok(json) = serde_json::to_vec(&handover) else {
        return 0;
    };
    let temporary = path.with_extension("tmp");
    let written = (|| -> std::io::Result<()> {
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temporary)?;
        file.write_all(&json)?;
        file.sync_all()?;
        std::fs::rename(&temporary, path)
    })();
    if let Err(error) = written {
        eprintln!(
            "handover: could not save the running sessions: {}",
            error.kind()
        );
        let _ = std::fs::remove_file(&temporary);
        return 0;
    }
    count
}

/// Restores the sessions of the previous broker (once: the file is deleted). Returns how many.
pub fn load(path: &Path, registry: &Arc<Mutex<Registry>>) -> usize {
    let Ok(metadata) = std::fs::metadata(path) else {
        return 0;
    };
    let contents = if metadata.is_file() && metadata.len() <= MAX_FILE {
        std::fs::read(path).ok()
    } else {
        None
    };
    let _ = std::fs::remove_file(path);
    let Some(handover) = contents.and_then(|bytes| serde_json::from_slice::<Handover>(&bytes).ok())
    else {
        return 0;
    };
    match registry.lock() {
        Ok(mut registry) => registry.import_handover(handover, Instant::now(), unix_now()),
        Err(_) => 0,
    }
}
