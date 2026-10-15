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


def validate_prior(root, texts, state):
    prior = prior_namespace()
    compiler = prior_compiler(prior)
    # Validate the actual supplied ownership before a copy can conceal it.
    compiler['validate_projection_sources'](root)
    if state == 'final':
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
    states = [state for state, pins in [('fresh', FRESH), ('original', ORIGINAL), ('final', FINAL)]
              if hashes == pins]
    if len(states) != 1:
        raise RuntimeError('Changed or partial URI routing profile')
    state = states[0]
    if state == 'fresh' and not restore:
        raise RuntimeError('URI routing terminal layer requires complete branding')
    planned = []
    for name, (path, text, newline) in texts.items():
        result = text if state == 'fresh' else transform(name, text, restore)
        expected = FRESH[name] if state == 'fresh' else ORIGINAL[name] if restore else FINAL[name]
        if digest(result.encode()) != expected:
            raise RuntimeError('URI routing profile inverse mismatch: ' + name)
        planned.append((path, result.replace('\n', newline).encode()))
    if verify:
        validate_prior(root, texts, state)
    # Recheck targets after the full prior projection, before the first write.
    for name, (path, text, newline) in texts.items():
        if safe_source(root, name) != path or path.read_bytes() != text.replace('\n', newline).encode():
            raise RuntimeError('URI routing source changed during verification')
    writes = 0
    for path, raw in planned:
        if not check and path.read_bytes() != raw:
            path.write_bytes(raw)
            writes += 1
    return {'profile': state, 'writes': writes, 'checkedFiles': len(texts)}


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
               for name, expected in FINAL.items())


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
    originals = {name: safe_source(root, name).read_bytes() for name in ORIGINAL}
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
