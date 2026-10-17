#!/usr/bin/env python3
"""Strict terminal successful-native-receipt layer; run after complete branding.

Apply receiver-generation only after this layer; inverse generation -> native ->
prior input. previous() copies an ordinary source tree privately and inverses
ONLY this layer. It does not import/call prior or later terminal generators.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

SCRIPTS = Path(__file__).resolve().parent
PLAN = SCRIPTS / 'support-receiver-native-plan.json'
PLAN_SHA = 'b495a3eefdb2d3a6d5d1b00bf66dcd81b442072f40c14a9e49dd59036303a7a0'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(raw):
    if b'\r' not in raw:
        return raw, b'\n'
    normalized = raw.replace(b'\r\n', b'\n')
    if b'\r' in normalized or normalized.replace(b'\n', b'\r\n') != raw:
        raise ValueError('mixed or bare-CR native source')
    return normalized, b'\r\n'


def root_path(root):
    root = Path(root).absolute()
    if 'worktrees' in root.parts:
        raise ValueError('worktree roots are forbidden')
    root = root.resolve(strict=True)
    if 'worktrees' in root.parts or not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError('an ordinary native source directory is required')
    return root


def state(root, name, absent=False):
    parts = Path(name).parts
    if not parts or Path(name).is_absolute() or any(p in ('.', '..', 'worktrees') for p in parts):
        raise ValueError('unsafe native source path')
    path = root
    for index, part in enumerate(parts):
        path /= part
        try:
            info = path.lstat()
        except FileNotFoundError:
            if absent and index == len(parts) - 1:
                return path, None, None
            raise ValueError('missing native source: ' + name) from None
        if index < len(parts) - 1:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('linked/non-directory native source parent')
        elif (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
              or getattr(info, 'st_file_attributes', 0) & 0x400
              or not 0 < info.st_size <= 2_000_000):
            raise ValueError('unsafe native source file: ' + name)
    raw = path.read_bytes()
    after = path.lstat()
    before_id = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode, info.st_nlink)
    after_id = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode, after.st_nlink)
    if before_id != after_id:
        raise ValueError('native source changed while reading')
    return path, raw, stat.S_IMODE(info.st_mode)


def plan():
    _, raw, _ = state(SCRIPTS, PLAN.name)
    logical, _ = canonical(raw)
    if sha(logical) != PLAN_SHA:
        raise ValueError('native receipt plan changed')
    value = json.loads(logical)
    if value['schema'] != 'mixel-receiver-native-plan-v1' or len(value['files']) != 8:
        raise ValueError('native receipt plan schema/cardinality changed')
    names = [row['name'] for row in value['files']]
    if len(set(names)) != 8 or len(value['owned']) != 2 or any(row['name'] in names for row in value['owned']):
        raise ValueError('duplicate native source owner')
    for row in value['files']:
        original = row['beforeSource'].encode()
        if sha(row['freshSource'].encode()) != row['freshSha256']:
            raise ValueError('native fresh source fixture changed')
        if sha(original) != row['beforeSha256']:
            raise ValueError('native source fixture changed')
        transform(original, row)
    modules = {}
    for owner in value['owned']:
        _, raw, _ = state(SCRIPTS, owner['template'])
        logical, _ = canonical(raw)
        if sha(logical) != owner['sha256']:
            raise ValueError('native receipt module changed')
        modules[owner['name']] = logical
    return value, modules


def transform(raw, row, restore=False):
    logical, newline = canonical(raw)
    source = 'afterSha256' if restore else 'beforeSha256'
    target = 'beforeSha256' if restore else 'afterSha256'
    if sha(logical) != row[source]:
        raise ValueError('native whole-source profile changed: ' + row['name'])
    text = logical.decode()
    for hunk in reversed(row['changes']) if restore else row['changes']:
        before, after = (hunk['after'], hunk['before']) if restore else (hunk['before'], hunk['after'])
        if not before or text.count(before) != 1:
            raise ValueError('native exact replacement cardinality changed')
        text = text.replace(before, after, 1)
    result = text.encode()
    if sha(result) != row[target]:
        raise ValueError('native output whole-source profile changed')
    return result.replace(b'\n', newline)


def inspect(root):
    root = root_path(root)
    value, module = plan()
    current = {}
    profiles = {"fresh", "before", "after"}
    for row in value['files']:
        item = state(root, row['name'])
        current[row['name']] = item
        digest = sha(canonical(item[1])[0])
        allowed = {profile for profile in ('fresh', 'before', 'after') if digest == row[profile + 'Sha256']}
        profiles &= allowed
        if not profiles:
            raise ValueError('unapproved or partial native source: ' + row['name'])
    absent_owners = False
    present_owners = False
    for owner in value['owned']:
        item = state(root, owner['name'], absent=True)
        current[owner['name']] = item
        if item[1] is None:
            absent_owners = True
            profiles.discard('after')
        elif sha(canonical(item[1])[0]) == owner['sha256'] and item[2] == owner['mode']:
            present_owners = True
            profiles &= {'after'}
        else:
            raise ValueError('unapproved owned native module')
    if absent_owners and present_owners:
        raise ValueError('partial native source profile without modules')
    if len(profiles) != 1:
        raise ValueError('partial native source profile')
    return root, value, module, profiles.pop(), current


def is_current(root):
    return inspect(root)[3] == 'after'


def recheck(root, value, current):
    for row in value['files'] + value['owned']:
        if state(root, row['name'], absent=row in value['owned'])[1:] != current[row['name']][1:]:
            raise ValueError('native source changed before mutation')


def atomic(path, raw, mode):
    descriptor, temporary = tempfile.mkstemp(prefix='.mixel-receiver-native-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(root, restore=False, check=False):
    root, value, module, profile, current = inspect(root)
    if profile == 'fresh':
        if restore:
            return {'profile': 'fresh', 'writes': 0, 'files': 10}
        raise ValueError('native receipts require complete branding first')
    desired = 'before' if restore else 'after'
    if profile == desired:
        return {'profile': profile, 'writes': 0, 'files': 10}
    if check:
        raise ValueError('required native receipt source profile is absent')
    output = {row['name']: transform(current[row['name']][1], row, restore) for row in value['files']}
    owned_names = {row['name'] for row in value['owned']}
    for owner in value['owned']:
        newline = canonical(current[owner['newlineFrom']][1])[1]
        output[owner['name']] = None if restore else module[owner['name']].replace(b'\n', newline)
    recheck(root, value, current)
    writes = 0
    for name, raw in output.items():
        path, original, mode = current[name]
        # Repeat each destination guard at commit; never follow a new link.
        if state(root, name, absent=name in owned_names)[1:] != (original, mode):
            raise ValueError('native destination changed during commit')
        if raw is None:
            path.unlink()
        else:
            atomic(path, raw, 0o644 if mode is None else mode)
        writes += 1
    if inspect(root)[3] != desired:
        raise ValueError('native commit did not produce complete profile')
    return {'profile': desired, 'writes': writes, 'files': 10}


@contextlib.contextmanager
def previous(root):
    root, value, _, profile, current = inspect(root)
    # Copy only source; retain symlinks as symlinks and never follow excluded
    # repository/build/cache directories. This layer validates only its 10 owners.
    ignored = {'.git', 'target', 'build', '.dart_tool', 'worktrees', '__pycache__'}
    with tempfile.TemporaryDirectory(prefix='mixel-native-prior-') as temporary:
        stage = Path(temporary) / 'source'
        shutil.copytree(root, stage, symlinks=True, ignore=lambda _path, names: [name for name in names if name in ignored])
        if profile == 'after':
            apply(stage, restore=True)
        recheck(root, value, current)
        try:
            yield stage
        finally:
            recheck(root, value, current)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(apply(args.root, args.restore, args.check), sort_keys=True))


if __name__ == '__main__':
    main()
