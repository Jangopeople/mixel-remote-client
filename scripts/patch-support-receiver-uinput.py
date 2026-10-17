#!/usr/bin/env python3
"""Strict final own-uinput sink owner. Apply after native receipts and receiver.

previous() inverses only this owner into a private ordinary copy. It never calls
or weakens any earlier source owner. Inverse order is uinput -> receiver -> native.
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

HERE = Path(__file__).resolve().parent
PLAN = HERE / 'support-receiver-uinput-plan.json'
PLAN_SHA = 'e7ade1c324daebc658cb3925f64433d3471308d6f724c61c0c6f5dca4d51ee7d'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(raw):
    logical = raw.replace(b'\r\n', b'\n')
    if b'\r' in logical or (b'\r\n' in raw and logical.replace(b'\n', b'\r\n') != raw):
        raise ValueError('mixed/bare-CR uinput source')
    logical.decode('utf-8')
    return logical, b'\r\n' if b'\r\n' in raw else b'\n'


def root_path(root):
    root = Path(root).absolute()
    for parent in reversed((root, *root.parents)):
        if 'worktrees' in parent.parts or not stat.S_ISDIR(parent.lstat().st_mode) or getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('ordinary non-worktree source required')
    return root


def state(root, name, absent=False):
    relative = Path(name)
    if (relative.is_absolute() or not relative.parts
            or any(part in ('.', '..', 'worktrees') for part in relative.parts)):
        raise ValueError('unsafe uinput source name')
    path = root
    for index, part in enumerate(relative.parts):
        path /= part
        try:
            info = path.lstat()
        except FileNotFoundError:
            if absent and index == len(relative.parts)-1:
                return path, None, None
            raise ValueError('missing uinput source: ' + name) from None
        if index < len(relative.parts)-1:
            if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('linked/non-directory uinput parent')
        elif (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or getattr(info, 'st_file_attributes', 0) & 0x400
                or not 0 < info.st_size <= 2_000_000):
            raise ValueError('unsafe uinput source file: ' + name)
    raw = path.read_bytes()
    after = path.lstat()
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                              value.st_mtime_ns, value.st_mode, value.st_nlink)
    if identity(info) != identity(after):
        raise ValueError('uinput source changed during read')
    return path, raw, stat.S_IMODE(info.st_mode)


def transform(raw, row, restore=False):
    logical, newline = canonical(raw)
    source, destination = ('after', 'before') if restore else ('before', 'after')
    if sha(logical) != row[source+'Sha256']:
        raise ValueError('uinput whole-source profile changed: '+row['name'])
    for hunk in reversed(row['changes']) if restore else row['changes']:
        before, after = ((hunk['after'], hunk['before']) if restore else
                         (hunk['before'], hunk['after']))
        before, after = before.encode(), after.encode()
        if not before or logical.count(before) != 1:
            raise ValueError('uinput exact replacement cardinality changed')
        logical = logical.replace(before, after, 1)
    if sha(logical) != row[destination+'Sha256']:
        raise ValueError('uinput prospective whole-source mismatch')
    return logical.replace(b'\n', newline)


def load_plan():
    _, raw, _ = state(HERE, PLAN.name)
    logical, _ = canonical(raw)
    if sha(logical) != PLAN_SHA:
        raise ValueError('uinput pinned plan drift')
    def unique(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ValueError('duplicate uinput plan key')
            output[key] = value
        return output
    plan = json.loads(logical, object_pairs_hook=unique)
    if plan['schema'] != 'mixel-receiver-uinput-plan-v1' or len(plan['files']) != 5 or len(plan['owned']) != 3:
        raise ValueError('uinput plan schema/cardinality changed')
    names = [row['name'] for row in plan['files']+plan['owned']]
    if len(names) != len(set(names)):
        raise ValueError('duplicate uinput source owner')
    for row in plan['files']:
        if (row['freshSource'] is None) != (row['freshSha256'] is None) or (row['freshSource'] is not None and sha(row['freshSource'].encode()) != row['freshSha256']):
            raise ValueError('uinput fresh fixture drift')
        if sha(row['beforeSource'].encode()) != row['beforeSha256']:
            raise ValueError('uinput predecessor fixture changed')
        transform(row['beforeSource'].encode(), row)
    templates = {}
    for row in plan['owned']:
        _, raw, mode = state(HERE, row['template'])
        logical, _ = canonical(raw)
        if sha(logical) != row['sha256'] or row['mode'] != 0o644:
            raise ValueError('uinput terminal template drift')
        templates[row['name']] = logical
    return plan, templates


def inspect(root):
    root = root_path(root)
    plan, templates = load_plan()
    current, profiles = {}, {'fresh', 'before', 'after'}
    for row in plan['files']:
        value = state(root, row['name'], absent=row['freshSha256'] is None)
        current[row['name']] = value
        if value[1] is not None and row['inheritedMode'] is not None and not (value[2] == row['inheritedMode'] or os.name == 'nt' and value[2] == 0o666):
            raise ValueError('inherited source owner mode changed')
        digest = None if value[1] is None else sha(canonical(value[1])[0])
        profiles &= {profile for profile in ('fresh', 'before', 'after')
                     if digest == row[profile+'Sha256']}
    for row in plan['owned']:
        value = state(root, row['name'], absent=True)
        current[row['name']] = value
        if value[1] is None:
            profiles.discard('after')
        elif (sha(canonical(value[1])[0]) == row['sha256']
              and (value[2] == row['mode'] or os.name == 'nt' and value[2] == 0o666)):
            profiles &= {'after'}
        else:
            raise ValueError('changed owned uinput leaf')
    if len(profiles) != 1:
        raise ValueError('changed, partial, or mixed uinput profile')
    return root, plan, templates, profiles.pop(), current


def is_current(root):
    return inspect(root)[3] == 'after'


def recheck(root, plan, current):
    for row in plan['files']+plan['owned']:
        if state(root, row['name'], absent=True)[1:] != current[row['name']][1:]:
            raise ValueError('uinput destination changed before mutation')


def atomic(path, raw, mode):
    descriptor, temporary = tempfile.mkstemp(prefix='.mixel-receiver-uinput-', dir=path.parent)
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
    root, plan, templates, profile, current = inspect(root)
    if profile == 'fresh':
        if not restore:
            raise ValueError('uinput owner requires full native+receiver branding')
        return {'profile': profile, 'writes': 0, 'files': len(current)}
    desired = 'before' if restore else 'after'
    if profile == desired:
        return {'profile': profile, 'writes': 0, 'files': len(current)}
    output = {row['name']: transform(current[row['name']][1], row, restore)
              for row in plan['files']}
    for row in plan['owned']:
        newline = canonical(current[row['newlineFrom']][1])[1]
        output[row['name']] = None if restore else templates[row['name']].replace(b'\n', newline)
    recheck(root, plan, current)
    if check:
        return {'profile': profile, 'plannedProfile': desired, 'writes': 0,
                'plannedWrites': len(output), 'files': len(current)}
    for name, raw in output.items():
        if state(root, name, absent=True)[1:] != current[name][1:]:
            raise ValueError('uinput destination changed during commit')
        path, original, mode = current[name]
        if raw is None:
            path.unlink()
        else:
            atomic(path, raw, 0o644 if mode is None else mode)
    if inspect(root)[3] != desired:
        raise ValueError('uinput commit did not produce whole profile')
    return {'profile': desired, 'writes': len(output), 'files': len(current)}


@contextlib.contextmanager
def previous(root):
    root, plan, _, profile, current = inspect(root)
    def ignored(directory, names):
        relative = Path(directory).relative_to(root).as_posix()
        ignored_names = {'worktrees'} & set(names)
        if relative == '.':
            ignored_names.update(set(names) & {'build', 'target', '.dart_tool', '__pycache__'})
        elif relative == 'flutter':
            ignored_names.update(set(names) & {'build', '.dart_tool'})
        elif relative in ('flutter/macos/Flutter', 'flutter/windows/flutter', 'flutter/linux/flutter'):
            ignored_names.update(set(names) & {'ephemeral'})
        return ignored_names
    with tempfile.TemporaryDirectory(prefix='mixel-uinput-prior-') as temporary:
        stage = Path(temporary).resolve(strict=True) / 'source'
        shutil.copytree(root, stage, symlinks=True, ignore=ignored)
        if profile == 'after':
            apply(stage, restore=True)
        recheck(root, plan, current)
        try:
            yield stage
        finally:
            recheck(root, plan, current)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(apply(args.root, args.restore, args.check), sort_keys=True))


if __name__ == '__main__':
    main()
