//! D4: a remote command is run only when the console signed it (contract 4.6 of
//! `docs/mixel-remote-device-admin-api.md`). This module checks the signature against the pinned key,
//! that the command is for this computer, that it is fresh and was never run before, and keeps the
//! on-device log (one line per command: time, id, key or "unsigned", accepted/refused and why, exit
//! code, SHA-256 of the script; never the script).
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use sha2::{Digest, Sha256};
use signaling_server::protocol::AgentCommand;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use uuid::Uuid;

/// Pinned console keys: (key id, raw Ed25519 public key as hex). The private key never leaves mixel-vps.
pub const PINNED: &[(&str, &str)] = &[("58b522e9976086ec", "10896242e49de888c657bd4f634b87abfb64e3cd13ba55c93822f60c0c8fc2a1")];
/// Tests: keys used instead of the pinned console key.
#[cfg(test)]
pub static TEST_PINNED: Mutex<Option<Vec<(String, VerifyingKey)>>> = Mutex::new(None);
/// Ids and nonces of run commands are kept this long.
const KEEP_SECONDS: u64 = 30 * 24 * 3600;
/// A command may be this much older than "now" than its `issued_at` (clock difference).
const SKEW_SECONDS: u64 = 300;
const LOG_LIMIT: u64 = 1024 * 1024;

#[derive(Debug, PartialEq, Eq)]
pub enum Refusal {
    Malformed,
    UnknownKey,
    BadSignature,
    WrongDevice,
    NotYetValid,
    Expired,
    Replayed,
    WrongShell,
}

impl Refusal {
    pub fn text(&self) -> &'static str {
        match self {
            Refusal::Malformed => "signature is malformed",
            Refusal::UnknownKey => "signing key is not pinned",
            Refusal::BadSignature => "signature does not verify",
            Refusal::WrongDevice => "command is for another computer",
            Refusal::NotYetValid => "command is not valid yet",
            Refusal::Expired => "command expired",
            Refusal::Replayed => "command id or nonce was already used",
            Refusal::WrongShell => "shell is not this platform's shell",
        }
    }
}

pub fn from_hex(text: &str) -> Option<Vec<u8>> {
    if !text.len().is_multiple_of(2) || !text.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    (0..text.len()).step_by(2).map(|i| u8::from_str_radix(&text[i..i + 2], 16).ok()).collect()
}

pub fn to_hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

pub fn script_sha256(script: &str) -> String {
    to_hex(&Sha256::digest(script.as_bytes()))
}

/// Parameter names a library script may take: `[A-Za-z_][A-Za-z0-9_]{0,31}`; at most 16, values up to 1024 bytes.
pub fn valid_params(params: &[signaling_server::protocol::ScriptParam]) -> bool {
    params.len() <= 16
        && params.iter().all(|param| {
            let mut chars = param.name.chars();
            chars.next().is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
                && param.name.len() <= 32
                && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
                && param.value.len() <= 1024
                && !param.value.contains('\0')
        })
}

/// SHA-256 of the compact JSON array of `[name, value]` pairs (empty list: `[]`).
pub fn params_sha256(params: &[signaling_server::protocol::ScriptParam]) -> String {
    let pairs: Vec<[&str; 2]> = params.iter().map(|param| [param.name.as_str(), param.value.as_str()]).collect();
    to_hex(&Sha256::digest(serde_json::to_string(&pairs).unwrap_or_default().as_bytes()))
}

/// The signed message (UTF-8, `\n` separated, no trailing newline). `v1` (scheme absent) is the ad-hoc console
/// command and carries no parameters; `v2` (library scripts) also covers the parameters and the script reference.
pub fn signed_message(command: &AgentCommand) -> Option<String> {
    let signed = command.signed.as_ref()?;
    let id = command.id.hyphenated().to_string().to_lowercase();
    let device = signed.device.hyphenated().to_string().to_lowercase();
    match signed.scheme.as_deref() {
        None if command.params.is_empty() && command.script_ref.is_none() => Some(format!(
            "mixel-remote/agent-command/v1\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}",
            id, device, signed.connection_id, command.shell, command.timeout_seconds, script_sha256(&command.script), signed.issued_at, signed.expires_at, signed.nonce,
        )),
        Some("v2") if valid_params(&command.params) => Some(format!(
            "mixel-remote/script-run/v2\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}",
            id, device, signed.connection_id, command.shell, command.timeout_seconds, script_sha256(&command.script), params_sha256(&command.params),
            command.script_ref.as_deref().unwrap_or(""), signed.issued_at, signed.expires_at, signed.nonce,
        )),
        _ => None,
    }
}

pub fn pinned_keys() -> Vec<(String, VerifyingKey)> {
    #[cfg(test)]
    if let Some(keys) = TEST_PINNED.lock().ok().and_then(|keys| keys.clone()) {
        return keys;
    }
    PINNED
        .iter()
        .filter_map(|(id, key)| {
            let bytes: [u8; 32] = from_hex(key)?.try_into().ok()?;
            Some(((*id).to_owned(), VerifyingKey::from_bytes(&bytes).ok()?))
        })
        .collect()
}

/// Ids and nonces already run, with the time they were seen.
#[derive(Default)]
pub struct Seen {
    entries: HashMap<String, u64>,
}

impl Seen {
    pub fn load(path: &Path) -> Self {
        let mut seen = Self::default();
        if let Ok(text) = std::fs::read_to_string(path) {
            for line in text.lines() {
                if let Some((at, key)) = line.split_once(' ')
                    && let Ok(at) = at.parse()
                {
                    seen.entries.insert(key.to_owned(), at);
                }
            }
        }
        seen
    }
    pub fn contains(&self, id: &str, nonce: &str) -> bool {
        self.entries.contains_key(id) || self.entries.contains_key(nonce)
    }
    pub fn insert(&mut self, id: &str, nonce: &str, now: u64) {
        self.entries.retain(|_, at| now.saturating_sub(*at) < KEEP_SECONDS);
        self.entries.insert(id.to_owned(), now);
        self.entries.insert(nonce.to_owned(), now);
    }
    pub fn save(&self, path: &Path) {
        let text: String = self.entries.iter().map(|(key, at)| format!("{at} {key}\n")).collect();
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let _ = std::fs::write(path, text);
    }
}

/// What the command must match: this computer, now, this platform's shell.
pub struct Own<'a> {
    pub device: Uuid,
    pub connection_id: u32,
    pub now: u64,
    pub shell: &'a str,
}

/// Checks a signed command. Returns the key id when it may run. Does not record it as used: the caller
/// calls `Seen::insert` once it accepts.
pub fn verify(command: &AgentCommand, own: &Own, keys: &[(String, VerifyingKey)], seen: &Seen) -> Result<String, Refusal> {
    let signed = command.signed.as_ref().ok_or(Refusal::Malformed)?;
    if !signed.well_formed() {
        return Err(Refusal::Malformed);
    }
    let key = keys.iter().find(|(id, _)| *id == signed.key_id).map(|(_, key)| key).ok_or(Refusal::UnknownKey)?;
    let message = signed_message(command).ok_or(Refusal::Malformed)?;
    let bytes: [u8; 64] = from_hex(&signed.signature).and_then(|b| b.try_into().ok()).ok_or(Refusal::Malformed)?;
    key.verify(message.as_bytes(), &Signature::from_bytes(&bytes)).map_err(|_| Refusal::BadSignature)?;
    if signed.device != own.device || signed.connection_id != own.connection_id {
        return Err(Refusal::WrongDevice);
    }
    if own.now + SKEW_SECONDS < signed.issued_at {
        return Err(Refusal::NotYetValid);
    }
    if own.now >= signed.expires_at {
        return Err(Refusal::Expired);
    }
    if command.shell != own.shell {
        return Err(Refusal::WrongShell);
    }
    if seen.contains(&command.id.hyphenated().to_string().to_lowercase(), &signed.nonce) {
        return Err(Refusal::Replayed);
    }
    Ok(signed.key_id.clone())
}

static SEEN: Mutex<Option<Seen>> = Mutex::new(None);

fn folder() -> Option<PathBuf> {
    crate::remote_session::app_folder()
}

/// Verifies a command against this computer and records it as used. `Ok(key id)` for a signed one,
/// `Ok(None)` for an unsigned one the phase allows.
pub fn check_and_record(command: &AgentCommand, device: Uuid, connection_id: u32, require_signed: bool) -> Result<Option<String>, Refusal> {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |time| time.as_secs());
    if command.signed.is_none() {
        return if require_signed { Err(Refusal::Malformed) } else { Ok(None) };
    }
    let shell = if cfg!(target_os = "windows") { "powershell" } else { "sh" };
    let own = Own { device, connection_id, now, shell };
    let path = folder().map(|folder| folder.join("agent-seen.txt"));
    let mut guard = SEEN.lock().map_err(|_| Refusal::Malformed)?;
    let seen = guard.get_or_insert_with(|| path.as_deref().map(Seen::load).unwrap_or_default());
    let key = verify(command, &own, &pinned_keys(), seen)?;
    if let Some(signed) = &command.signed {
        seen.insert(&command.id.hyphenated().to_string().to_lowercase(), &signed.nonce, now);
    }
    if let Some(path) = &path {
        seen.save(path);
    }
    Ok(Some(key))
}

/// One log line. `commands.log` in the app's folder, 1 MB, rotated once.
pub fn log_line(id: Uuid, key: Option<&str>, outcome: &str, exit_code: Option<i32>, script: &str) {
    let Some(folder) = folder() else { return };
    write_log(&folder.join("commands.log"), &format_line(id, key, outcome, exit_code, script));
}

pub fn format_line(id: Uuid, key: Option<&str>, outcome: &str, exit_code: Option<i32>, script: &str) -> String {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |time| time.as_secs());
    format!(
        "{now} id={} key={} {outcome} exit={} sha256={}\n",
        id.hyphenated(),
        key.unwrap_or("unsigned"),
        exit_code.map_or_else(|| "-".to_owned(), |code| code.to_string()),
        script_sha256(script),
    )
}

pub fn write_log(path: &Path, line: &str) {
    use std::io::Write;
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if std::fs::metadata(path).is_ok_and(|meta| meta.len() >= LOG_LIMIT) {
        let _ = std::fs::rename(path, path.with_extension("log.1"));
    }
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = file.write_all(line.as_bytes());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};
    use signaling_server::protocol::CommandSignature;

    const DEVICE: &str = "5ceaf4a7-0000-4000-8000-000000000001";
    const VECTOR_SIGNATURE: &str = "943d07309e829a2715c51afc3fde3e3bf836fb7a695e701d6d812476779d32ed4442821e195630f8843afb523d85ae14c342df7cf95dd996c4f629a078ea4d05";

    fn test_key() -> SigningKey {
        SigningKey::from_bytes(&[7u8; 32])
    }
    fn keys() -> Vec<(String, VerifyingKey)> {
        vec![("fe812c12f3ab4ce6".to_owned(), test_key().verifying_key())]
    }
    fn command(signature: &str) -> AgentCommand {
        AgentCommand {
            id: "6f0c4b8e-1d2a-4c3b-9e8f-0a1b2c3d4e5f".parse().unwrap(),
            shell: "sh".into(),
            script: "uname -a\n".into(),
            timeout_seconds: 300,
            params: vec![],
            script_ref: None,
            signed: Some(CommandSignature {
                key_id: "fe812c12f3ab4ce6".into(),
                device: DEVICE.parse().unwrap(),
                connection_id: 440_207_203,
                issued_at: 1_794_900_000,
                expires_at: 1_794_986_400,
                nonce: "ab".repeat(32),
                signature: signature.into(),
                scheme: None,
            }),
        }
    }
    fn own(now: u64) -> Own<'static> {
        Own { device: DEVICE.parse().unwrap(), connection_id: 440_207_203, now, shell: "sh" }
    }

    #[test]
    fn the_contract_test_vector_signs_and_verifies() {
        let message = signed_message(&command(VECTOR_SIGNATURE)).unwrap();
        assert_eq!(to_hex(&test_key().sign(message.as_bytes()).to_bytes()), VECTOR_SIGNATURE);
        assert_eq!(verify(&command(VECTOR_SIGNATURE), &own(1_794_900_100), &keys(), &Seen::default()), Ok("fe812c12f3ab4ce6".to_owned()));
    }

    #[test]
    fn the_pinned_key_is_valid_and_its_id_is_the_hash_prefix() {
        let keys = pinned_keys();
        assert_eq!(keys.len(), PINNED.len());
        let raw = from_hex(PINNED[0].1).unwrap();
        assert_eq!(&to_hex(&Sha256::digest(&raw))[..16], PINNED[0].0);
    }

    #[test]
    fn every_change_to_the_command_or_the_circumstances_is_refused() {
        let good = command(VECTOR_SIGNATURE);
        let at = 1_794_900_100;
        let check = |c: &AgentCommand, o: &Own, s: &Seen| verify(c, o, &keys(), s);
        let mut changed = good.clone();
        changed.script = "rm -rf /\n".into();
        assert_eq!(check(&changed, &own(at), &Seen::default()), Err(Refusal::BadSignature));
        let mut changed = good.clone();
        changed.timeout_seconds = 301;
        assert_eq!(check(&changed, &own(at), &Seen::default()), Err(Refusal::BadSignature));
        let mut changed = good.clone();
        changed.signed.as_mut().unwrap().key_id = "0000000000000000".into();
        assert_eq!(check(&changed, &own(at), &Seen::default()), Err(Refusal::UnknownKey));
        let mut changed = good.clone();
        changed.signed.as_mut().unwrap().signature = "00".repeat(64);
        assert_eq!(check(&changed, &own(at), &Seen::default()), Err(Refusal::BadSignature));
        let mut changed = good.clone();
        changed.signed.as_mut().unwrap().signature = "zz".into();
        assert_eq!(check(&changed, &own(at), &Seen::default()), Err(Refusal::Malformed));
        let mut other = own(at);
        other.connection_id = 440_207_204;
        assert_eq!(check(&good, &other, &Seen::default()), Err(Refusal::WrongDevice));
        let mut other = own(at);
        other.device = Uuid::new_v4();
        assert_eq!(check(&good, &other, &Seen::default()), Err(Refusal::WrongDevice));
        assert_eq!(check(&good, &own(1_794_900_000 - 301), &Seen::default()), Err(Refusal::NotYetValid));
        assert!(check(&good, &own(1_794_900_000 - 300), &Seen::default()).is_ok(), "5 minutes of clock difference are allowed");
        assert_eq!(check(&good, &own(1_794_986_400), &Seen::default()), Err(Refusal::Expired));
        let mut other = own(at);
        other.shell = "powershell";
        assert_eq!(check(&good, &other, &Seen::default()), Err(Refusal::WrongShell));
    }

    #[test]
    fn a_command_runs_once() {
        let good = command(VECTOR_SIGNATURE);
        let mut seen = Seen::default();
        seen.insert(&good.id.hyphenated().to_string(), &"ab".repeat(32), 1_794_900_100);
        assert_eq!(verify(&good, &own(1_794_900_200), &keys(), &seen), Err(Refusal::Replayed));
        // Only the nonce known: still a replay.
        let mut seen = Seen::default();
        seen.insert("other", &"ab".repeat(32), 1_794_900_100);
        assert_eq!(verify(&good, &own(1_794_900_200), &keys(), &seen), Err(Refusal::Replayed));
    }

    #[test]
    fn what_was_seen_is_kept_on_disk_for_thirty_days() {
        let dir = std::env::temp_dir().join(format!("mixel-agent-seen-{}", std::process::id()));
        let path = dir.join("agent-seen.txt");
        let mut seen = Seen::default();
        seen.insert("id-1", "nonce-1", 1_000);
        seen.save(&path);
        let loaded = Seen::load(&path);
        assert!(loaded.contains("id-1", "x") && loaded.contains("y", "nonce-1"));
        let mut loaded = loaded;
        loaded.insert("id-2", "nonce-2", 1_000 + KEEP_SECONDS + 1);
        assert!(!loaded.contains("id-1", "nonce-1"), "older than 30 days is dropped");
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn the_log_line_has_the_hash_and_never_the_script() {
        let id = Uuid::nil();
        let line = format_line(id, Some("58b522e9976086ec"), "accepted", Some(0), "secret-script");
        assert!(line.contains("key=58b522e9976086ec") && line.contains("exit=0") && line.contains(&script_sha256("secret-script")));
        assert!(!line.contains("secret-script"));
        assert!(format_line(id, None, "refused: x", None, "s").contains("key=unsigned"));
    }

    #[test]
    fn the_log_rotates_once_at_one_megabyte() {
        let dir = std::env::temp_dir().join(format!("mixel-agent-log-{}", std::process::id()));
        let path = dir.join("commands.log");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&path, vec![b'x'; LOG_LIMIT as usize]).unwrap();
        write_log(&path, "new\n");
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "new\n");
        assert_eq!(std::fs::metadata(path.with_extension("log.1")).unwrap().len(), LOG_LIMIT);
        let _ = std::fs::remove_dir_all(dir);
    }
}
