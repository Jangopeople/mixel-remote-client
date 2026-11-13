//! Small settings of this app in `~/.config/ch.mixel.remote/settings.json` (language, the
//! unattended display, this computer's registration, known network addresses for Wake-on-LAN). No
//! secrets: the sign-in and the device key live in the Secret Service, kept by the engine.
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, path::PathBuf};

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    /// `auto`, `en`, `de`, `fr`, `it`.
    pub language: String,
    pub unattended: Option<Unattended>,
    pub this_computer: Option<Registration>,
    /// Remote ID → the network adapter addresses its last session reported.
    pub macs: BTreeMap<u32, Vec<String>>,
    /// The company's own AI server for the session assistant.
    pub ai: Option<AiServer>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct AiServer {
    pub address: String,
    pub model: String,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Unattended {
    pub source: String,
    pub control: bool,
}

/// This computer's registration as the app last saw it (to remove it offline-first).
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Registration {
    pub device: String,
    pub connection_id: u32,
    pub generation: u64,
    pub tenant: Option<String>,
}

fn base(variable: &str, fallback: &str) -> Option<PathBuf> {
    std::env::var_os(variable)
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(fallback)))
}

/// The engine's private folder, the same as the 0.1.x app's (`~/.local/share/ch.mixel.remote.next.qa`),
/// so a removal of this computer queued before the update is still sent.
pub fn data_dir() -> PathBuf {
    base("XDG_DATA_HOME", ".local/share").unwrap_or_else(std::env::temp_dir).join(crate::LEGACY_ID)
}

fn settings_path() -> Option<PathBuf> {
    base("XDG_CONFIG_HOME", ".config").map(|folder| folder.join(crate::APP_ID).join("settings.json"))
}

pub struct Store {
    path: Option<PathBuf>,
    pub settings: Settings,
}

impl Store {
    pub fn open() -> Self {
        let path = settings_path();
        let saved = path.as_ref().and_then(|path| std::fs::read(path).ok()).and_then(|bytes| serde_json::from_slice(&bytes).ok());
        let migrated = saved.is_none();
        let settings = saved.or_else(|| legacy_storage().map(|items| from_legacy(&items))).unwrap_or_default();
        let store = Self { path, settings };
        if migrated && store.settings != Settings::default() {
            store.save();
        }
        store
    }

    /// Kept in memory only (screenshots, tests).
    pub fn memory(settings: Settings) -> Self {
        Self { path: None, settings }
    }

    pub fn save(&self) {
        let Some(path) = &self.path else { return };
        if let Some(folder) = path.parent() {
            let _ = std::fs::create_dir_all(folder);
        }
        if let Ok(bytes) = serde_json::to_vec_pretty(&self.settings) {
            let temporary = path.with_extension("tmp");
            if std::fs::write(&temporary, bytes).is_ok() {
                let _ = std::fs::rename(&temporary, path);
            }
        }
    }

    /// Remembers the addresses of a computer seen in a session (at most 4; at most 500 computers).
    pub fn remember_macs(&mut self, id: u32, macs: &[String]) -> bool {
        let macs: Vec<String> = macs.iter().take(4).cloned().collect();
        if macs.is_empty() || self.settings.macs.get(&id) == Some(&macs) {
            return false;
        }
        if self.settings.macs.len() >= 500 && !self.settings.macs.contains_key(&id) {
            let first = *self.settings.macs.keys().next().expect("non-empty");
            self.settings.macs.remove(&first);
        }
        self.settings.macs.insert(id, macs);
        true
    }
}

/// The 0.1.x app (Tauri) kept these settings in its web view's local storage: an SQLite file with
/// UTF-16 values. Read once, when this app has no settings of its own yet.
fn legacy_storage() -> Option<BTreeMap<String, String>> {
    let file = base("XDG_DATA_HOME", ".local/share")?.join(crate::LEGACY_ID).join("localstorage").join("tauri_localhost_0.localstorage");
    if !file.is_file() {
        return None;
    }
    let connection = rusqlite::Connection::open_with_flags(&file, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY).ok()?;
    let mut statement = connection.prepare("SELECT key, value FROM ItemTable WHERE key LIKE 'mixel.remote.%'").ok()?;
    let rows = statement.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, Vec<u8>>(1)?))).ok()?;
    Some(rows.filter_map(Result::ok).map(|(key, value)| (key, utf16(&value))).collect())
}

fn utf16(bytes: &[u8]) -> String {
    let units: Vec<u16> = bytes.as_chunks::<2>().0.iter().map(|pair| u16::from_le_bytes(*pair)).collect();
    String::from_utf16_lossy(&units)
}

fn from_legacy(items: &BTreeMap<String, String>) -> Settings {
    let json = |key: &str| items.get(key).and_then(|text| serde_json::from_str::<serde_json::Value>(text).ok());
    let mut settings = Settings::default();
    if let Some(language) = items.get("mixel.remote.language.v1").filter(|value| ["en", "de", "fr", "it"].contains(&value.as_str())) {
        settings.language = language.clone();
    }
    if let Some(value) = json("mixel.remote.unattended.v1")
        && let Some(source) = value["source"].as_str()
    {
        settings.unattended = Some(Unattended { source: source.to_owned(), control: value["control"].as_bool().unwrap_or(false) });
    }
    if let Some(value) = json("mixel.remote.this-computer.v1") {
        settings.this_computer = serde_json::from_value(value).ok();
    }
    if let Some(value) = json("mixel.remote.ai.v1")
        && let (Some(address), Some(model)) = (value["address"].as_str(), value["model"].as_str())
    {
        settings.ai = Some(AiServer { address: address.to_owned(), model: model.to_owned() });
    }
    if let Some(serde_json::Value::Object(known)) = json("mixel.remote.macs.v1") {
        for (id, macs) in known {
            if let (Ok(id), Some(list)) = (id.parse::<u32>(), macs.as_array()) {
                let macs: Vec<String> = list.iter().filter_map(|mac| mac.as_str().map(str::to_owned)).take(4).collect();
                if !macs.is_empty() {
                    settings.macs.insert(id, macs);
                }
            }
        }
    }
    settings
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn settings_round_trip_and_mac_memory() {
        let mut store = Store::memory(Settings::default());
        assert!(store.remember_macs(123_456_789, &["aa:bb:cc:dd:ee:ff".into()]));
        assert!(!store.remember_macs(123_456_789, &["aa:bb:cc:dd:ee:ff".into()]), "unchanged");
        assert!(!store.remember_macs(222_222_222, &[]));
        let text = serde_json::to_string(&store.settings).unwrap();
        let back: Settings = serde_json::from_str(&text).unwrap();
        assert_eq!(back, store.settings);
        let legacy: BTreeMap<String, String> = [
            ("mixel.remote.language.v1".to_owned(), "fr".to_owned()),
            ("mixel.remote.unattended.v1".to_owned(), r#"{"source":"x11:0:1","control":true}"#.to_owned()),
            ("mixel.remote.this-computer.v1".to_owned(), r#"{"device":"d","connection_id":482615903,"generation":3,"name":"PC","tenant":"t"}"#.to_owned()),
            ("mixel.remote.macs.v1".to_owned(), r#"{"731204558":["52:54:00:12:34:56"],"bad":["x"]}"#.to_owned()),
        ]
        .into();
        let migrated = from_legacy(&legacy);
        assert_eq!(migrated.language, "fr");
        assert_eq!(migrated.unattended, Some(Unattended { source: "x11:0:1".into(), control: true }));
        assert_eq!(migrated.this_computer.as_ref().map(|current| (current.connection_id, current.generation)), Some((482_615_903, 3)));
        assert_eq!(migrated.macs.get(&731_204_558).map(Vec::len), Some(1));
        assert_eq!(utf16(&[0x64, 0, 0x65, 0]), "de");
        let old: Settings = serde_json::from_str(r#"{"language":"de","unknown":true}"#).unwrap();
        assert_eq!(old.language, "de");
        assert!(old.this_computer.is_none());
    }
}
