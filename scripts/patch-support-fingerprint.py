#!/usr/bin/env python3
"""Replace the exact pinned fingerprint cache's unsynchronized mutable globals."""
import argparse
import hashlib
from pathlib import Path
import stat

TARGET = 'libs/hbb_common/src/fingerprint.rs'
BEFORE_SHA = '62d46014c029e7869d8b470e33a3ca51db4a8e1267676632052bf729bf7dc164'
OLD_GLOBALS = '''static mut FINGERPRINTING_INFO: Option<FingerprintingInfo> = None;
static INIT: Once = Once::new();
static mut CACHED_FINGERPRINTS: Option<HashMap<String, Vec<u8>>> = None;'''
NEW_GLOBALS = '''static FINGERPRINTING_INFO: OnceLock<FingerprintingInfo> = OnceLock::new();
static CACHED_FINGERPRINTS: OnceLock<Mutex<HashMap<Vec<String>, Vec<u8>>>> = OnceLock::new();'''
OLD_INFO = '''pub fn get_fingerprinting_info() -> FingerprintingInfo {
    unsafe {
        INIT.call_once(|| {
            FINGERPRINTING_INFO = Some(FingerprintingInfo::new());
            CACHED_FINGERPRINTS = Some(HashMap::new());
        });
        #[allow(static_mut_refs)]
        FINGERPRINTING_INFO.clone().unwrap_or_default()
    }
}'''
NEW_INFO = '''pub fn get_fingerprinting_info() -> FingerprintingInfo {
    FINGERPRINTING_INFO
        .get_or_init(FingerprintingInfo::new)
        .clone()
}'''
OLD_CACHE = '''    unsafe {
        #[allow(static_mut_refs)]
        if let Some(cache) = &mut CACHED_FINGERPRINTS {
            if let Some(fingerprint) = cache.get(&cache_key) {
                return fingerprint.clone();
            }

            let fingerprint = calculate_fingerprint(&parameters);
            cache.insert(cache_key, fingerprint.clone());
            fingerprint
        } else {
            calculate_fingerprint(&parameters)
        }
    }'''
NEW_CACHE = '''    let cache = CACHED_FINGERPRINTS.get_or_init(|| Mutex::new(HashMap::new()));
    if let Some(fingerprint) = cache.lock().unwrap().get(&cache_key) {
        return fingerprint.clone();
    }

    // Hardware information and digest calculation do not run under the cache lock.
    let fingerprint = calculate_fingerprint(&parameters);
    cache
        .lock()
        .unwrap()
        .entry(cache_key)
        .or_insert(fingerprint)
        .clone()'''
REPLACEMENTS = [
    ('use std::sync::Once;', 'use std::sync::{Mutex, OnceLock};'),
    (OLD_GLOBALS, NEW_GLOBALS),
    (OLD_INFO, NEW_INFO),
    ('let cache_key = parameters.join("");', 'let cache_key = parameters.clone();'),
    (OLD_CACHE, NEW_CACHE),
]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def normalize(raw):
    if b'\r' in raw:
        if b'\r' in raw.replace(b'\r\n', b'') or b'\n' in raw.replace(b'\r\n', b''):
            raise RuntimeError('Mixed fingerprint source newlines')
        return raw.replace(b'\r\n', b'\n'), b'\r\n'
    return raw, b'\n'


def transform(raw):
    if digest(raw) != BEFORE_SHA:
        raise RuntimeError('Pinned fingerprint source changed')
    text = raw.decode('utf-8')
    for old, new in REPLACEMENTS:
        if text.count(old) != 1:
            raise RuntimeError('Fingerprint source anchor changed')
        text = text.replace(old, new, 1)
    return text.encode('utf-8')


def inverse(raw):
    text = raw.decode('utf-8')
    for old, new in reversed(REPLACEMENTS):
        if text.count(new) != 1:
            raise RuntimeError('Corrected fingerprint source anchor changed')
        text = text.replace(new, old, 1)
    original = text.encode('utf-8')
    if digest(original) != BEFORE_SHA or transform(original) != raw:
        raise RuntimeError('Corrected fingerprint source changed')
    return original


def safe_target(root):
    root = Path(root).absolute()
    target = root / TARGET
    for parent in list(reversed(target.parents)):
        entry = parent.lstat()
        if not stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or getattr(entry, "st_file_attributes", 0) & 0x400:
            raise RuntimeError('Unsafe fingerprint source parent')
    entry = target.lstat()
    if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1 or getattr(entry, "st_file_attributes", 0) & 0x400:
        raise RuntimeError('Unsafe fingerprint source file')
    return target


def apply(root, check=False, restore=False):
    target = safe_target(root)
    raw = target.read_bytes()
    canonical, newline = normalize(raw)
    if digest(canonical) == BEFORE_SHA:
        original, corrected = canonical, transform(canonical)
    else:
        original = inverse(canonical)
        corrected = canonical
    desired = original if restore else corrected
    desired = desired.replace(b'\n', newline)
    if not check and raw != desired:
        target.write_bytes(desired)
    return digest(desired)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    apply(args.root, args.check, args.restore)
    print('PASS pinned fingerprint cache source')


if __name__ == '__main__':
    main()
