#!/usr/bin/env python3
"""Exact terminal support URI dispatch layer; inverse before every prior owner."""
import argparse
import contextlib
import hashlib
import shutil
import stat
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).absolute().parent
PRIOR_NAME = 'patch-support-linux-child-window-close.py'
PRIOR_SHA = '033e578f834769374a3adff50439416c488b382986d44dee957f63fb472ebc3a'
FRESH = {
    'flutter/lib/common.dart': '95ffcc4fdc3d83564d19d5ad7c999adbe65cdc37829cf466d43d5a9684e7f8de',
    'flutter/lib/models/model.dart': 'bf48af559ed23a0a185e164b5efbad5b3aff46eeddd69ab9ad1da32056aac866',
}
ORIGINAL = {
    'flutter/lib/common.dart': '3fe1d6e6ec8e9d8f2ebafc7ad6b175a0d4eb80dd476b687b6aad9fe673a66be8',
    'flutter/lib/models/model.dart': 'bf48af559ed23a0a185e164b5efbad5b3aff46eeddd69ab9ad1da32056aac866',
}
FINAL = {'flutter/lib/common.dart': '8180d31aad9f5464239f049291607456424dbbc14e62b9153f2e2258ec6fc6ff', 'flutter/lib/models/model.dart': '10f981d3a8127faed854ffb1742b011129ff3bd579e8102cb16d542955073f1e'}
CHANGES = {
    'flutter/lib/common.dart': [(
        "  if (uri.scheme.toLowerCase() == 'mixel-remote' && uri.host.toLowerCase() == 'support') {\n",
        "  if (uri.host.toLowerCase() == 'support') {\n"
        "    // The support intent is reserved; another scheme cannot become a remote ID.\n"
        "    if (uri.scheme.toLowerCase() != 'mixel-remote') return null;\n",
    )],
    'flutter/lib/models/model.dart': [(
        "    if (url.startsWith(bind.mainUriPrefixSync()) &&\n",
        "    if (url.toLowerCase().startsWith(bind.mainUriPrefixSync().toLowerCase()) &&\n",
    )],
}


NATIVE_FRESH = {'src/mixel_support_url.rs': None, 'src/ipc.rs': '1ba2a64410ab24cdcf8ca67ccfb4564d97d8725653991a501b190510ac9a9953', 'src/common.rs': '1c133828b7fac67c90e9f1293a58f73204a78d2fd25425f2758a1ae217575ac2', 'src/core_main.rs': '3bc241c59d547352e77bfa1b44d6ea0e1200b3dd703ed4a63fbb720b07187512', 'src/server.rs': 'd6460bc6dba17253e6e4ba7947db1dfee9285294a3e3e3bdd71a646dd160663f'}
NATIVE_ORIGINAL = {'src/mixel_support_url.rs': '15263ad5c9823ad0e0b5ae71cb3f2d8e2eec4a4d724f7e92b10446f1f32db1f5', 'src/ipc.rs': '2e69e5eb402628e73ceb36af73adce22ab7d6165533dfee2f38c3db7f5945e6f', 'src/common.rs': 'cd1ca8340689104c621d99becfec33485d66e3afefdd9ab74f901ac24ca40150', 'src/core_main.rs': '8a6541ba344d287a96b917f7bf0eaa8ff3002b5fb5d3aada7562f0c8d8463e1c', 'src/server.rs': 'adfe3dbc3c76a7408e7be47ab0e408bd32960115509e759e5a333d18baf95e6d'}
NATIVE_FINAL = {'src/mixel_support_url.rs': '51794179244b2bfc6055c38edbc2961d6adb2bd6754b3fb855ebee1c5c64fc21', 'src/ipc.rs': '62386d3dbf579e1a72d77100f2bee744395d4c778f2749c97955b5d103a09849', 'src/common.rs': '996c4948ba464f8f6bee036b47454f2c34b285b8f12ed094c679c25f5261b985', 'src/core_main.rs': 'ae088f7f431712968424f0e8d842c00c746c1fd9ab28533b579d4327181ca2ee', 'src/server.rs': '025aadf1e84975be76ae2046a9497a53b9a676af66a8f2d707202340bcb0169d'}
NATIVE_CHANGES = {'src/mixel_support_url.rs': [('pub(crate) fn accepted_ack(', '// Candidate recognition is deliberately independent of validity: malformed\n// support credentials must never reach the legacy unverified URL transport.\npub(crate) fn is_reserved_support_url(raw: &str) -> bool {\n    if let Some((_, rest)) = raw.split_once("://") {\n        let authority = rest.split([\'/\', \'?\', \'#\']).next().unwrap_or("");\n        let host_port = authority.rsplit(\'@\').next().unwrap_or("");\n        let host = host_port.split(\':\').next().unwrap_or("");\n        if let Some(host) = decode_query(host) {\n            let host: String = host.chars().filter(|value| !value.is_control()).collect();\n            if host.eq_ignore_ascii_case("support") { return true; }\n        }\n    }\n    // These names are reserved support credentials, even on an invalid host or\n    // scheme. Legacy remote links use key/password/relay/switch_uuid instead.\n    if let Some((_, query)) = raw.split_once(\'?\') {\n        for pair in query.split([\'&\', \'#\']) {\n            let name = pair.split_once(\'=\').map(|(name, _)| name).unwrap_or(pair);\n            if let Some(name) = decode_query(name) {\n                match name.to_lowercase().as_str() {\n                    "invite" | "apikey" => return true,\n                    _ => {}\n                }\n            }\n        }\n    }\n    false\n}\n\npub(crate) fn accepted_ack(')], 'src/ipc.rs': [('pub async fn send_url_scheme(url: String) -> ResultType<()> {\n', 'pub async fn send_url_scheme(url: String) -> ResultType<()> {\n    #[cfg(all(target_os = "macos", feature = "flutter"))]\n    if crate::server::mixel_support_url::is_reserved_support_url(&url)\n        && !crate::server::mixel_support_url::is_valid_support_url(&url) {\n        bail!("Mixel support URL handoff rejected malformed intent");\n    }\n')], 'src/common.rs': [('pub fn handle_url_scheme(url: String) {\n', 'pub fn handle_url_scheme(url: String) {\n    #[cfg(all(target_os = "macos", feature = "flutter"))]\n    if crate::server::mixel_support_url::is_reserved_support_url(&url)\n        && !crate::server::mixel_support_url::is_valid_support_url(&url) {\n        return;\n    }\n')], 'src/core_main.rs': [('    let _is_mixel_support_invite = args.first()\n', '    #[cfg(all(target_os = "macos", feature = "flutter"))]\n    if args.first().map(|url| crate::server::mixel_support_url::is_reserved_support_url(url)\n        && !crate::server::mixel_support_url::is_valid_support_url(url)).unwrap_or(false) {\n        return None;\n    }\n    let _is_mixel_support_invite = args.first()\n'), ('    #[cfg(target_os = "macos")]\n    {\n        #[cfg(feature = "flutter")]\n        if crate::server::mixel_support_url::is_valid_support_url(&uni_links) {\n', '    #[cfg(target_os = "macos")]\n    {\n        #[cfg(feature = "flutter")]\n        if crate::server::mixel_support_url::is_reserved_support_url(&uni_links)\n            && !crate::server::mixel_support_url::is_valid_support_url(&uni_links) {\n            return None;\n        }\n        #[cfg(feature = "flutter")]\n        if crate::server::mixel_support_url::is_valid_support_url(&uni_links) {\n')], 'src/server.rs': [('                        Data::UrlLink(url) => {\n                            if url.is_empty() {\n', '                        Data::UrlLink(url) => {\n                            if mixel_support_url::is_reserved_support_url(&url)\n                                && !mixel_support_url::is_valid_support_url(&url) {\n                                send_mixel_support_url_ack(&mut conn, mixel_support_url::Delivery::Unavailable).await;\n                                continue;\n                            }\n                            if url.is_empty() {\n')]}

def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(raw):
    text = raw.decode('utf-8', errors='strict')
    logical = text.replace('\r\n', '\n')
    if '\r' in logical or ('\r\n' in text and '\n' in text.replace('\r\n', '')):
        raise RuntimeError('Mixed or unsupported URI routing newlines')
    return logical, '\r\n' if '\r\n' in text else '\n'


def safe_root(root):
    for path in (root, *root.parents):
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise RuntimeError('Unsafe URI routing root')


def safe_source(root, name):
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise RuntimeError('Unsafe URI routing source name')
    path = root / relative
    for parent in path.parents:
        safe_root(parent)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or not 0 < info.st_size <= 1_000_000
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise RuntimeError('Unsafe URI routing source: ' + name)
    return path


def prior_namespace():
    path = safe_source(SCRIPTS, PRIOR_NAME)
    text, _ = canonical(path.read_bytes())
    if digest(text.encode()) != PRIOR_SHA:
        raise RuntimeError('Changed URI routing prior verifier')
    namespace = {'__name__': 'mixel_uri_routing_prior', '__file__': str(path)}
    exec(compile(text, str(path), 'exec'), namespace)
    return namespace


def prior_compiler(prior):
    return prior['prior_namespace']()['prior_namespace']()['prior']('patch-support-compiler-cleanup.py')


def transform(name, text, restore=False):
    if name in NATIVE_ORIGINAL:
        return transform_native(name, text, restore)
    before, after = (FINAL[name], ORIGINAL[name]) if restore else (ORIGINAL[name], FINAL[name])
    if digest(text.encode()) == after:
        return text
    if digest(text.encode()) != before:
        raise RuntimeError('Changed pinned URI routing source: ' + name)
    pairs = list(reversed(CHANGES[name])) if restore else CHANGES[name]
    for old, new in pairs:
        if restore:
            old, new = new, old
        if text.count(old) != 1:
            raise RuntimeError('Changed exact URI routing literal: ' + name)
        text = text.replace(old, new, 1)
    if digest(text.encode()) != after:
        raise RuntimeError('URI routing whole-source inverse mismatch: ' + name)
    return text



def transform_native(name, text, restore=False):
    before, after = (NATIVE_FINAL[name], NATIVE_ORIGINAL[name]) if restore else (NATIVE_ORIGINAL[name], NATIVE_FINAL[name])
    if digest(text.encode()) == after:
        return text
    if digest(text.encode()) != before:
        raise RuntimeError('Changed pinned native URI routing source: ' + name)
    pairs = list(reversed(NATIVE_CHANGES[name])) if restore else NATIVE_CHANGES[name]
    for old, new in pairs:
        if restore:
            old, new = new, old
        if text.count(old) != 1:
            raise RuntimeError('Changed exact native URI routing literal: ' + name)
        text = text.replace(old, new, 1)
    if digest(text.encode()) != after:
        raise RuntimeError('Native URI routing whole-source inverse mismatch: ' + name)
    return text


def native_inventory(root):
    texts, hashes = {}, {}
    for name in NATIVE_ORIGINAL:
        path = root / name
        if not path.exists() and not path.is_symlink():
            hashes[name] = None
            continue
        path = safe_source(root, name)
        text, newline = canonical(path.read_bytes())
        texts[name] = (path, text, newline)
        hashes[name] = digest(text.encode())
    matches = [state for state, pins in [('fresh', NATIVE_FRESH), ('original', NATIVE_ORIGINAL), ('final', NATIVE_FINAL)] if hashes == pins]
    if len(matches) != 1:
        raise RuntimeError('Changed or partial native URI routing profile')
    return matches[0], texts


def validate_prior(root, texts, state):
    prior = prior_namespace()
    compiler = prior_compiler(prior)
    # Validate the actual supplied ownership before a copy can conceal it.
    compiler['validate_projection_sources'](root)
    if state in ('final', 'previous-final'):
        with tempfile.TemporaryDirectory(prefix='mixel-uri-routing-prior-') as temporary:
            stage = Path(temporary).resolve() / 'source'
            shutil.copytree(root, stage, symlinks=True,
                            ignore=lambda directory, names: compiler['projection_ignored'](root, directory, names))
            for name, (_, text, newline) in texts.items():
                (stage / name).write_bytes(transform(name, text, True).replace('\n', newline).encode())
            prior['apply'](stage, check=True, restore=True, verify=True)
    else:
        prior['apply'](root, check=True, restore=True, verify=True)
    return compiler


def apply(root, check=False, restore=False, verify=True):
    root = Path(root).absolute()
    safe_root(root)
    texts, hashes = {}, {}
    for name in ORIGINAL:
        path = safe_source(root, name)
        text, newline = canonical(path.read_bytes())
        texts[name] = (path, text, newline)
        hashes[name] = digest(text.encode())
    native_state, native_texts = native_inventory(root)
    states = [state for state, pins in [('fresh', FRESH), ('original', ORIGINAL), ('final', FINAL)]
              if hashes == pins]
    if len(states) != 1:
        raise RuntimeError('Changed or partial URI routing profile')
    state = states[0]
    if state == 'final' and native_state == 'original':
        state = 'previous-final'
    elif native_state != state:
        raise RuntimeError('Mixed Dart/native URI routing profile')
    texts.update(native_texts)
    if state == 'previous-final' and not restore:
        raise RuntimeError('Historical native URI routing profile is restore-only')
    if state == 'fresh' and not restore:
        raise RuntimeError('URI routing terminal layer requires complete branding')
    planned = []
    for name, (path, text, newline) in texts.items():
        result = text if state == 'fresh' else transform(name, text, restore)
        original_pins, final_pins, fresh_pins = ((ORIGINAL, FINAL, FRESH) if name in ORIGINAL
                                                   else (NATIVE_ORIGINAL, NATIVE_FINAL, NATIVE_FRESH))
        expected = fresh_pins[name] if state == 'fresh' else original_pins[name] if restore else final_pins[name]
        if digest(result.encode()) != expected:
            raise RuntimeError('URI routing profile inverse mismatch: ' + name)
        planned.append((path, result.replace('\n', newline).encode()))
    if verify:
        validate_prior(root, texts, state)
    # Recheck targets after the full prior projection, before the first write.
    for name, (path, text, newline) in texts.items():
        if safe_source(root, name) != path or path.read_bytes() != text.replace('\n', newline).encode():
            raise RuntimeError('URI routing source changed during verification')
    writes = native_writes = 0
    for path, raw in planned:
        if not check and path.read_bytes() != raw:
            path.write_bytes(raw)
            if path.relative_to(root).as_posix() in ORIGINAL:
                writes += 1
            else:
                native_writes += 1
    return {'profile': state, 'writes': writes, 'nativeWrites': native_writes, 'checkedFiles': len(texts)}


def has_layer(root):
    root = Path(root).absolute()
    safe_root(root)
    # Every earlier owner leaves this complete upstream file byte-exact. Check
    # it before presence detection, so corrupted partial layers cannot bypass
    # the strict inverse through an older owner's conditional projection.
    model = 'flutter/lib/models/model.dart'
    model_hash = digest(canonical(safe_source(root, model).read_bytes())[0].encode())
    if model_hash not in (ORIGINAL[model], FINAL[model]):
        raise RuntimeError('Changed URI routing model presence source')
    return any(digest(canonical(safe_source(root, name).read_bytes())[0].encode()) == expected
               for name, expected in FINAL.items()) or any(
                   (root / name).is_file() and digest(canonical(safe_source(root, name).read_bytes())[0].encode()) == expected
                   for name, expected in NATIVE_FINAL.items())


@contextlib.contextmanager
def previous(root):
    """Read-only exact prior-owner projection for existing source tests."""
    root = Path(root).absolute()
    apply(root, check=True, restore=True)
    if not has_layer(root):
        yield root
        return
    compiler = prior_compiler(prior_namespace())
    compiler['validate_projection_sources'](root)
    originals = {name: safe_source(root, name).read_bytes() for name in (*ORIGINAL, *NATIVE_ORIGINAL)}
    with tempfile.TemporaryDirectory(prefix='mixel-uri-routing-inverse-') as temporary:
        stage = Path(temporary).resolve() / 'source'
        shutil.copytree(root, stage, symlinks=True,
                        ignore=lambda directory, names: compiler['projection_ignored'](root, directory, names))
        apply(stage, restore=True, verify=False)
        try:
            yield stage
        finally:
            if any(safe_source(root, name).read_bytes() != raw for name, raw in originals.items()):
                raise RuntimeError('URI routing projection changed supplied source')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--before-branding', action='store_true')
    parser.add_argument('--after-branding', action='store_true')
    args = parser.parse_args()
    if args.before_branding and not (args.restore and args.check):
        parser.error('--before-branding requires --restore --check')
    if args.after_branding and args.restore:
        parser.error('--after-branding cannot restore')
    print(apply(args.root, args.check, args.restore))


if __name__ == '__main__':
    main()
