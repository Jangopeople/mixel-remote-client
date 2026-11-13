//! Mixel Remote in English, German, French or Italian: this computer's language, or the one chosen in
//! Settings. Screen texts are English gettext message ids (`t(…)`); their translations are the
//! catalogs in `po/` (compiled to .mo by build.rs, the same files the package installs under
//! /usr/share/locale). Texts that come from the engine (status lines, error messages) are translated
//! with the desktop app's tables (`engine-texts.json`, generated from `frontends/desktop/i18n-strings.ts`).
//! Device names, chat messages and typed values are never translated.
use std::{
    collections::HashMap,
    fmt::Display,
    sync::{OnceLock, RwLock},
};

pub const SUPPORTED: [&str; 4] = ["en", "de", "fr", "it"];

static CHOICE: RwLock<String> = RwLock::new(String::new());

fn catalogs() -> &'static HashMap<&'static str, gettext::Catalog> {
    static CATALOGS: OnceLock<HashMap<&'static str, gettext::Catalog>> = OnceLock::new();
    CATALOGS.get_or_init(|| {
        let files: [(&str, &[u8]); 3] = [
            ("de", include_bytes!(concat!(env!("OUT_DIR"), "/de.mo"))),
            ("fr", include_bytes!(concat!(env!("OUT_DIR"), "/fr.mo"))),
            ("it", include_bytes!(concat!(env!("OUT_DIR"), "/it.mo"))),
        ];
        files
            .into_iter()
            .filter_map(|(language, bytes)| gettext::Catalog::parse(bytes).ok().map(|catalog| (language, catalog)))
            .collect()
    })
}

struct Table {
    texts: HashMap<String, String>,
    patterns: Vec<(regex::Regex, String)>,
}

fn engine_tables() -> &'static HashMap<String, Table> {
    static TABLES: OnceLock<HashMap<String, Table>> = OnceLock::new();
    TABLES.get_or_init(|| {
        #[derive(serde::Deserialize)]
        struct Raw {
            texts: HashMap<String, String>,
            patterns: Vec<Vec<String>>,
        }
        let raw: HashMap<String, Raw> = serde_json::from_slice(include_bytes!("../data/engine-texts.json")).unwrap_or_default();
        let group = regex::Regex::new(r"\$(\d+)").expect("group pattern");
        raw.into_iter()
            .map(|(language, table)| {
                let patterns = table
                    .patterns
                    .into_iter()
                    .filter(|pair| pair.len() == 2)
                    // JavaScript patterns; the few this engine cannot read stay untranslated.
                    .filter_map(|pair| {
                        let target = group.replace_all(&pair[1], "$${$1}").into_owned();
                        regex::Regex::new(&pair[0]).ok().map(|pattern| (pattern, target))
                    })
                    .collect();
                (language, Table { texts: table.texts, patterns })
            })
            .collect()
    })
}

/// The setting: `auto`, `en`, `de`, `fr` or `it`.
pub fn choice() -> String {
    let chosen = CHOICE.read().map(|value| value.clone()).unwrap_or_default();
    if chosen.is_empty() { "auto".into() } else { chosen }
}

pub fn set_choice(value: &str) {
    if let Ok(mut chosen) = CHOICE.write() {
        *chosen = if value == "auto" || SUPPORTED.contains(&value) { value.to_owned() } else { "auto".into() };
    }
}

/// The language of the system (LANGUAGE, LC_ALL, LC_MESSAGES, LANG), if it is one of ours.
pub fn system_language(read: impl Fn(&str) -> Option<String>) -> &'static str {
    for name in ["LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"] {
        let Some(value) = read(name).filter(|value| !value.is_empty()) else { continue };
        // LANGUAGE may list several ("de:en"); the first we support wins.
        for item in value.split(':') {
            let code = item.get(..2).unwrap_or_default().to_ascii_lowercase();
            if let Some(found) = SUPPORTED.iter().find(|supported| **supported == code) {
                return found;
            }
        }
        if name != "LANGUAGE" {
            return "en";
        }
    }
    "en"
}

/// The language shown now.
pub fn language() -> &'static str {
    let chosen = choice();
    if let Some(found) = SUPPORTED.iter().find(|supported| **supported == chosen) {
        return found;
    }
    system_language(|name| std::env::var(name).ok())
}

/// A screen text in the shown language (English when there is no translation).
pub fn t(key: &str) -> String {
    translate_screen(key, language())
}

pub fn translate_screen(key: &str, language: &str) -> String {
    if language == "en" {
        return key.to_owned();
    }
    if let Some(catalog) = catalogs().get(language) {
        let text = catalog.gettext(key);
        if text != key {
            return text.to_owned();
        }
    }
    translate_engine(key, language)
}

/// A screen text with `%@` (text) and `%d` (number) placeholders filled in order.
pub fn tf(key: &str, values: &[&dyn Display]) -> String {
    fill(&t(key), values)
}

pub fn fill(template: &str, values: &[&dyn Display]) -> String {
    let mut out = String::with_capacity(template.len() + 16);
    let mut values = values.iter();
    let mut chars = template.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '%' {
            match chars.peek() {
                Some('@' | 'd') => {
                    chars.next();
                    if let Some(value) = values.next() {
                        out.push_str(&value.to_string());
                    }
                    continue;
                }
                Some('%') => {
                    chars.next();
                    out.push('%');
                    continue;
                }
                _ => {}
            }
        }
        out.push(c);
    }
    out
}

/// An engine text (status line, error message) in the shown language; unknown texts stay English.
pub fn engine(text: &str) -> String {
    translate_engine(text, language())
}

pub fn translate_engine(text: &str, language: &str) -> String {
    let core = text.trim();
    if language == "en" || core.is_empty() || core.chars().count() > 600 {
        return text.to_owned();
    }
    let Some(table) = engine_tables().get(language) else { return text.to_owned() };
    if let Some(hit) = table.texts.get(core) {
        return text.replacen(core, hit, 1);
    }
    for (pattern, target) in &table.patterns {
        if pattern.is_match(core) {
            let replaced = pattern.replace(core, target.as_str());
            return text.replacen(core, &replaced, 1);
        }
    }
    text.to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn placeholders_are_filled_in_order() {
        assert_eq!(fill("%d of %d devices", &[&3, &10]), "3 of 10 devices");
        assert_eq!(fill("Connect to %@", &[&"Office PC"]), "Connect to Office PC");
        assert_eq!(fill("100%% sure %@", &[&"yes"]), "100% sure yes");
        assert_eq!(fill("missing %@ value", &[]), "missing  value");
    }

    #[test]
    fn system_language_is_read_from_the_usual_variables() {
        let env = |pairs: &'static [(&'static str, &'static str)]| move |name: &str| pairs.iter().find(|(key, _)| *key == name).map(|(_, value)| value.to_string());
        assert_eq!(system_language(env(&[("LANG", "de_CH.UTF-8")])), "de");
        assert_eq!(system_language(env(&[("LANGUAGE", "it:en"), ("LANG", "de_CH.UTF-8")])), "it");
        assert_eq!(system_language(env(&[("LANGUAGE", "es:fr"), ("LANG", "C.UTF-8")])), "fr");
        assert_eq!(system_language(env(&[("LC_ALL", "C"), ("LANG", "fr_FR.UTF-8")])), "en");
        assert_eq!(system_language(env(&[])), "en");
    }

    #[test]
    fn every_language_has_a_catalog_with_the_main_texts() {
        for language in ["de", "fr", "it"] {
            assert!(catalogs().contains_key(language), "{language} catalog");
            assert_ne!(translate_screen("Remote control", language), "Remote control", "{language}");
            assert_ne!(translate_screen("End session", language), "End session", "{language}");
        }
        assert_eq!(translate_screen("Remote control", "en"), "Remote control");
    }

    #[test]
    fn engine_messages_are_translated_with_the_desktop_tables() {
        let german = translate_engine("Go offline before removing this computer", "de");
        assert_ne!(german, "Go offline before removing this computer");
        assert_eq!(translate_engine("Some unknown engine text 4711", "de"), "Some unknown engine text 4711");
        assert_eq!(translate_engine("Go offline before removing this computer", "en"), "Go offline before removing this computer");
    }
}
