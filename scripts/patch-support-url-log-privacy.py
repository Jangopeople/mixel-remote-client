#!/usr/bin/env python3
"""Keep Mac callback logs private and give tray support URLs one delivery owner.

The RustDesk leaf can run on every source platform. The Tao leaf requires an
explicit owned Cargo home: never discover or modify the user's Cargo checkout.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

TAO_COMMIT = '288c219cb0527e509590c2b2d8e7072aa9feb2d3'
TAO_TREE = '7b825f2d863803c13a4cd65d26efadcb0e6c2571'
MARKER = '.mixel-url-log-privacy-owned.json'
SOURCES = {
    'rustdesk': {
        'path': 'src/platform/delegate.rs',
        'original': '1a6f68fc2923fcc78ac89d08f0e672204e5ffe793f19b7b9c7f9edeaa141d83b',
        'before': 'log::debug!("URL received: {}", theurl);',
        'after': 'log::debug!("URL received.");',
    },
    'tao': {
        'path': 'src/platform_impl/macos/app_delegate.rs',
        'original': '24a91ca48fd9508a44ce8c1e0b32b5ac1f83214486e2c8ee61052d929a1db825',
        'before': 'trace!("Get `application:openURLs:` URLs: {:?}", urls);',
        'after': 'trace!("Get `application:openURLs:` URLs received.");',
    },
}
# The old privacy-only profile is accepted solely for exact restoration.
# Apply then starts from the original whole file after the macro's early restore.
PRIVACY_ONLY_RUSTDESK = '44df6e0cffbd3c2a02dd81c4b9867a44875a297fa866b3cb92c86d8bcadccd75'
CALLBACK_ANCHOR = '            std::thread::spawn(move || crate::handle_url_scheme(theurl));'
SUPPORT_OWNER_GUARD = '            // Tao invokes this native callback before emitting Event::Opened.\n            // Its tray route owns validated support intents; do not dispatch twice.\n            #[cfg(feature = "flutter")]\n            if matches!(std::env::args().nth(1).as_deref(), Some("--tray" | "--server"))\n                && crate::server::mixel_support_url::is_valid_support_url(&theurl)\n            {\n                continue;\n            }\n'

# Whole-file corrected fingerprints are independently bound by source controls.
CORRECTED = {
    'rustdesk': '17260041d58c152b8520c6c64a4a6030b9699f7e9ddce22bbf00acaeae2f27a1',
    'tao': '460ab62a629e3fda7f54603d2f6f68524ab5fb9be1092de37d2b9529c214d8b1',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    text = data.decode('utf-8')
    if text.startswith('\ufeff') or '\r' in text.replace('\r\n', ''):
        raise RuntimeError('Invalid source encoding or newline profile')
    profile = '\r\n' if '\r\n' in text else '\n'
    if profile == '\r\n' and '\n' in text.replace('\r\n', ''):
        raise RuntimeError('Mixed source newline profile')
    return text.replace('\r\n', '\n'), profile


def transform(kind, data, restore=False):
    spec = SOURCES[kind]
    text, profile = canonical(data)
    fingerprint = digest(text.encode())
    if fingerprint == spec['original']:
        if text.count(spec['before']) != 1 or spec['after'] in text:
            raise RuntimeError('Original logger ownership mismatch')
        corrected = text.replace(spec['before'], spec['after'])
        if kind == 'rustdesk':
            if corrected.count(CALLBACK_ANCHOR) != 1:
                raise RuntimeError('Original callback ownership mismatch')
            corrected = corrected.replace(CALLBACK_ANCHOR, SUPPORT_OWNER_GUARD + CALLBACK_ANCHOR)
        if digest(corrected.encode()) != CORRECTED[kind]:
            raise RuntimeError('Corrected logger fingerprint mismatch')
        output = text if restore else corrected
    elif fingerprint == CORRECTED[kind]:
        if text.count(spec['after']) != 1 or spec['before'] in text:
            raise RuntimeError('Corrected logger ownership mismatch')
        original = text
        if kind == 'rustdesk':
            if original.count(SUPPORT_OWNER_GUARD + CALLBACK_ANCHOR) != 1:
                raise RuntimeError('Corrected callback ownership mismatch')
            original = original.replace(SUPPORT_OWNER_GUARD + CALLBACK_ANCHOR, CALLBACK_ANCHOR)
        original = original.replace(spec['after'], spec['before'])
        if digest(original.encode()) != spec['original']:
            raise RuntimeError('Logger inverse fingerprint mismatch')
        output = original if restore else text
    elif kind == 'rustdesk' and fingerprint == PRIVACY_ONLY_RUSTDESK:
        if not restore:
            raise RuntimeError('Legacy privacy-only callback requires exact early restoration')
        if text.count(spec['after']) != 1 or spec['before'] in text or SUPPORT_OWNER_GUARD in text:
            raise RuntimeError('Legacy privacy-only callback ownership mismatch')
        original = text.replace(spec['after'], spec['before'])
        if digest(original.encode()) != spec['original']:
            raise RuntimeError('Legacy privacy-only inverse fingerprint mismatch')
        output = original
    else:
        raise RuntimeError('Pinned URL logger source changed: ' + kind)
    return output.replace('\n', profile).encode()


def safe_directory(directory):
    directory = Path(directory).absolute()
    if directory != directory.resolve(strict=True):
        raise RuntimeError('Noncanonical or symlinked source directory')
    for entry in (directory, *directory.parents):
        value = entry.lstat()
        if not stat.S_ISDIR(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('Unsafe source directory')
    return directory


def safe_file(root, relative, limit=1_000_000):
    root = safe_directory(root)
    path = root / relative
    safe_directory(path.parent)
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_size > limit or getattr(value, 'st_file_attributes', 0) & 0x400:
        raise RuntimeError('Unsafe URL logger source file')
    return path, value


def replace_owned(path, data, mode):
    with tempfile.NamedTemporaryFile(prefix='.mixel-url-log-', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.chmod(temporary, stat.S_IMODE(mode))
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def apply(root, kind='rustdesk', restore=False, check=False):
    path, value = safe_file(root, SOURCES[kind]['path'])
    before = path.read_bytes()
    after = transform(kind, before, restore)
    if not check and before != after:
        replace_owned(path, after, value.st_mode)
    return int(not check and before != after)


def marker_data(home):
    return {'schema': 1, 'purpose': 'mixel-pinned-tao-url-log-privacy',
            'home': str(home), 'uid': os.getuid()}


def reserve_cargo_home(home):
    if os.name != 'posix':
        raise RuntimeError('Owned Tao Cargo homes require POSIX')
    home = Path(home).absolute()
    parent = safe_directory(home.parent)
    if home == Path.home() / '.cargo' or home == parent or home.name in ('', '.cargo'):
        raise RuntimeError('Refusing shared Cargo home')
    if home.exists() or home.is_symlink():
        safe_directory(home)
        if (home / MARKER).exists():
            validate_cargo_home(home)
            return home
        if any(home.iterdir()):
            raise RuntimeError('Unowned existing Cargo home')
    else:
        home.mkdir(mode=0o700)
    value = home.lstat()
    if value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) & 0o022:
        raise RuntimeError('Unowned Cargo home permissions')
    marker = home / MARKER
    with marker.open('x', encoding='utf-8', newline='\n') as handle:
        json.dump(marker_data(home), handle, sort_keys=True)
        handle.write('\n')
    marker.chmod(0o600)
    return home


def validate_cargo_home(home):
    if os.name != 'posix':
        raise RuntimeError('Owned Tao Cargo homes require POSIX')
    home = safe_directory(home)
    if home == Path.home() / '.cargo' or home.name == '.cargo':
        raise RuntimeError('Refusing shared Cargo home')
    value = home.lstat()
    if value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) & 0o022:
        raise RuntimeError('Unowned Cargo home permissions')
    path, value = safe_file(home, MARKER)
    if value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) & 0o077:
        raise RuntimeError('Unowned Cargo marker permissions')
    if json.loads(path.read_text()) != marker_data(home):
        raise RuntimeError('Cargo home ownership marker mismatch')
    return home


def git(checkout, *arguments):
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                                  'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES')}
    environment['GIT_OPTIONAL_LOCKS'] = '0'
    return subprocess.check_output(['git', '-C', str(checkout), '--git-dir=.git', '--work-tree=.', *arguments],
                                   stderr=subprocess.PIPE, env=environment)


def owned_git_directory(checkout, home):
    direct = safe_directory(checkout / '.git')
    for option in ('--git-dir', '--git-common-dir'):
        value = Path(git(checkout, 'rev-parse', option).decode().strip())
        value = value if value.is_absolute() else checkout / value
        value = safe_directory(value)
        if not value.is_relative_to(home) or value.stat().st_uid != os.getuid():
            raise RuntimeError('External or unowned Tao Git directory')
        if value != direct:
            raise RuntimeError('Nonstandard Tao common Git directory')
    if Path(git(checkout, 'rev-parse', '--show-toplevel').decode().strip()) != checkout:
        raise RuntimeError('Tao Git worktree identity mismatch')
    pending = [safe_directory(direct / 'objects')]
    visited = set()
    while pending:
        objects = pending.pop()
        if objects in visited:
            continue
        if len(visited) >= 32 or not objects.is_relative_to(home) or objects.stat().st_uid != os.getuid():
            raise RuntimeError('External or unowned Tao object storage')
        visited.add(objects)
        alternate = objects / 'info/alternates'
        if alternate.exists() or alternate.is_symlink():
            path, value = safe_file(home, alternate.relative_to(home), limit=4096)
            if value.st_uid != os.getuid():
                raise RuntimeError('Unowned Tao alternate metadata')
            for line in path.read_text().splitlines():
                value = Path(line)
                value = value if value.is_absolute() else objects / value
                # Resolve only within this explicitly owned home; aliases and
                # external shared object stores never qualify as private input.
                if not value.absolute().is_relative_to(home):
                    raise RuntimeError('External Tao object alternate')
                value = value.resolve(strict=True)
                value = safe_directory(value)
                if not value.is_relative_to(home):
                    raise RuntimeError('External Tao object alternate')
                pending.append(value)


def head_inventory(checkout):
    head = {}
    for record in git(checkout, 'ls-tree', '-r', '-z', 'HEAD').split(b'\0'):
        if not record:
            continue
        identity, name = record.split(b'\t', 1)
        mode, kind, object_id = identity.decode().split()
        name = name.decode()
        if kind != 'blob' or mode not in ('100644', '100755') or name in head:
            raise RuntimeError('Unsafe Tao HEAD inventory')
        head[name] = (mode, object_id)
    index = {}
    for record in git(checkout, 'ls-files', '--stage', '-z').split(b'\0'):
        if not record:
            continue
        identity, name = record.split(b'\t', 1)
        mode, object_id, stage = identity.decode().split()
        name = name.decode()
        if stage != '0' or name in index:
            raise RuntimeError('Unmerged Tao index')
        index[name] = (mode, object_id)
    if not head or index != head:
        raise RuntimeError('Tao index differs from complete pinned HEAD')
    return head


def tao_checkout(home):
    home = validate_cargo_home(home)
    parent = safe_directory(home / 'git/checkouts')
    candidates = []
    for repository in parent.glob('tao-*'):
        safe_directory(repository)
        for checkout in repository.iterdir():
            safe_directory(checkout)
            if (checkout / '.git').exists():
                candidates.append(checkout)
    if len(candidates) != 1:
        raise RuntimeError('Expected exactly one owned Tao checkout')
    checkout = candidates[0]
    owned_git_directory(checkout, home)
    for directory in (checkout, checkout.parent, checkout.parent.parent):
        if directory.stat().st_uid != os.getuid():
            raise RuntimeError('Unowned Tao checkout directory')
    if git(checkout, 'rev-parse', 'HEAD').decode().strip() != TAO_COMMIT or git(checkout, 'rev-parse', 'HEAD^{tree}').decode().strip() != TAO_TREE:
        raise RuntimeError('Pinned Tao Git identity changed')
    target = SOURCES['tao']['path']
    path, _ = safe_file(checkout, target)
    # Every tracked byte is conserved except the exact reversible logger leaf.
    for name, (mode, _object_id) in head_inventory(checkout).items():
        if name.startswith('/') or '..' in Path(name).parts:
            raise RuntimeError('Unsafe Tao tracked source name')
        source, value = safe_file(checkout, name, limit=10_000_000)
        if value.st_uid != os.getuid() or bool(value.st_mode & 0o111) != (mode == '100755'):
            raise RuntimeError('Unowned Tao tracked source or Git mode drift')
        baseline = git(checkout, 'show', 'HEAD:' + name)
        current = source.read_bytes()
        if name == target:
            current = transform('tao', current, restore=True)
        if current != baseline:
            raise RuntimeError('Pinned Tao tracked source changed')
    untracked = set(git(checkout, 'ls-files', '--others', '-z').decode().split('\0')) - {''}
    if untracked - {'.cargo-ok'}:
        raise RuntimeError('Unexpected untracked Tao source')
    if '.cargo-ok' in untracked:
        safe_file(checkout, '.cargo-ok')
    return checkout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--root', type=Path)
    modes.add_argument('--reserve-cargo-home', type=Path)
    modes.add_argument('--cargo-home', type=Path)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.reserve_cargo_home:
        if args.restore or args.check:
            parser.error('Reservation does not accept source actions')
        reserve_cargo_home(args.reserve_cargo_home)
        print('PASS: reserved owned Cargo home for pinned Tao logger privacy')
    else:
        kind = 'tao' if args.cargo_home else 'rustdesk'
        root = tao_checkout(args.cargo_home) if args.cargo_home else args.root
        writes = apply(root, kind, args.restore, args.check)
        print('PASS: exact ' + kind + ' URL logger privacy; writes=' + str(writes))


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, UnicodeError, ValueError, subprocess.SubprocessError):
        raise SystemExit('Refusing unverified URL logger privacy source or ownership')
