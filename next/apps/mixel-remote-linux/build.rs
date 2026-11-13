//! Compiles the gettext catalogs (po/*.po) into .mo files in OUT_DIR. The app embeds them (so it
//! works from the build folder, in tests and in screenshots) and the package installs the same files
//! under /usr/share/locale.
use std::{collections::BTreeMap, fs, path::Path};

fn unquote(text: &str) -> String {
    let inner = text.trim().strip_prefix('"').and_then(|rest| rest.strip_suffix('"')).unwrap_or_else(|| panic!("bad po string: {text}"));
    let mut out = String::new();
    let mut chars = inner.chars();
    while let Some(c) = chars.next() {
        if c != '\\' {
            out.push(c);
            continue;
        }
        match chars.next() {
            Some('n') => out.push('\n'),
            Some('t') => out.push('\t'),
            Some('"') => out.push('"'),
            Some('\\') => out.push('\\'),
            other => panic!("bad escape in po: {other:?}"),
        }
    }
    out
}

/// msgid -> msgstr of one .po file (no plurals, no contexts: the app uses neither).
fn parse(text: &str) -> BTreeMap<String, String> {
    let mut entries = BTreeMap::new();
    let (mut id, mut value, mut field) = (None::<String>, None::<String>, 0);
    let mut flush = |id: &mut Option<String>, value: &mut Option<String>| {
        if let (Some(key), Some(text)) = (id.take(), value.take())
            && !text.is_empty()
        {
            entries.insert(key, text);
        }
    };
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some(rest) = line.strip_prefix("msgid ") {
            flush(&mut id, &mut value);
            id = Some(unquote(rest));
            field = 1;
        } else if let Some(rest) = line.strip_prefix("msgstr ") {
            value = Some(unquote(rest));
            field = 2;
        } else if line.starts_with('"') {
            let more = unquote(line);
            match field {
                1 => id.get_or_insert_default().push_str(&more),
                2 => value.get_or_insert_default().push_str(&more),
                _ => panic!("po continuation without a field"),
            }
        } else {
            panic!("unsupported po line: {line}");
        }
    }
    flush(&mut id, &mut value);
    entries
}

/// The GNU .mo format: header, the two string tables, then the strings (no hash table).
fn mo(entries: &BTreeMap<String, String>) -> Vec<u8> {
    let count = entries.len() as u32;
    let header = 28u32;
    let originals = header;
    let translations = originals + count * 8;
    let mut data_offset = translations + count * 8;
    let mut table_a = Vec::new();
    let mut table_b = Vec::new();
    let mut strings = Vec::new();
    for key in entries.keys() {
        table_a.extend((key.len() as u32).to_le_bytes());
        table_a.extend(data_offset.to_le_bytes());
        strings.extend(key.as_bytes());
        strings.push(0);
        data_offset += key.len() as u32 + 1;
    }
    for value in entries.values() {
        table_b.extend((value.len() as u32).to_le_bytes());
        table_b.extend(data_offset.to_le_bytes());
        strings.extend(value.as_bytes());
        strings.push(0);
        data_offset += value.len() as u32 + 1;
    }
    let mut out = Vec::new();
    for word in [0x9504_12de_u32, 0, count, originals, translations, 0, data_offset] {
        out.extend(word.to_le_bytes());
    }
    out.extend(table_a);
    out.extend(table_b);
    out.extend(strings);
    out
}

fn main() {
    let out = std::env::var_os("OUT_DIR").expect("OUT_DIR");
    for language in ["de", "fr", "it"] {
        let path = format!("po/{language}.po");
        println!("cargo:rerun-if-changed={path}");
        let text = fs::read_to_string(&path).unwrap_or_else(|_| panic!("missing {path}"));
        let mut entries = parse(&text);
        // The header entry (msgid "") stays: gettext readers use it for the charset.
        entries.entry(String::new()).or_insert_with(|| "Content-Type: text/plain; charset=UTF-8\n".into());
        fs::write(Path::new(&out).join(format!("{language}.mo")), mo(&entries)).expect("write .mo");
    }
}
