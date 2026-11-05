#!/usr/bin/env python3
"""Exact post-branding Windows compiler integration; default is read-only.

Run after shared bridge input admission, before checking/building the native app.
No authorization, lease, callback, framing or permission behavior is changed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

PLAN = Path(__file__).with_name('windows-compiler-integration-plan.json')
PLAN_SHA = 'f52d8055d1161995b3c7f7b2196c3e9ecfc4363e301e3a5b538f7b6ff838a317'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def logical(raw):
    text = raw.decode('utf-8')
    ending = '\r\n' if '\r\n' in text else '\n'
    normalized = text.replace('\r\n', '\n')
    if '\r' in normalized or (ending == '\r\n' and '\n' in text.replace('\r\n', '')):
        raise ValueError('Mixed compiler input newlines')
    return normalized, ending


def ordinary(path):
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Indirect compiler input ancestry')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Ordinary independent compiler input required')
    return info


def plan():
    ordinary(PLAN)
    raw = PLAN.read_bytes()
    if sha(logical(raw)[0].encode()) != PLAN_SHA:
        raise ValueError('Compiler integration plan drift')
    value = json.loads(raw)
    if value['schema'] != 'mixel-windows-compiler-integration-v1' or len(value['files']) != 10:
        raise ValueError('Compiler integration plan shape drift')
    return value


def transform(row, raw, *, restore=False):
    text, ending = logical(raw)
    current = [name for name in ('before', 'after') if sha(text.encode()) == row[name + 'Sha256']]
    if len(current) != 1:
        raise ValueError('Changed compiler complete source')
    desired = 'before' if restore else 'after'
    if current[0] != desired:
        for pair in reversed(row['changes']) if restore else row['changes']:
            before, after = (pair['after'], pair['before']) if restore else (pair['before'], pair['after'])
            offset = pair['offset']
            if text[offset:offset + len(before)] != before or (before and text.count(before) != 1):
                raise ValueError('Compiler literal coordinate/cardinality drift')
            text = text[:offset] + after + text[offset + len(before):]
    if sha(text.encode()) != row[desired + 'Sha256']:
        raise ValueError('Compiler complete output/inverse drift')
    return text.replace('\n', ending).encode()


def apply(root, *, write=False, restore=False):
    root = Path(root).absolute()
    rows = plan()['files']
    staged = []
    profiles = {'before', 'after'}
    for row in rows:
        path = root / row['path']
        info = ordinary(path)
        raw = path.read_bytes()
        text, ending = logical(raw)
        profiles &= {name for name in ('before', 'after') if sha(text.encode()) == row[name + 'Sha256']}
        staged.append((row, path, info, raw, text, ending))
    if len(profiles) != 1:
        raise ValueError('Changed or mixed Windows compiler input profile')
    current = profiles.pop()
    desired = 'before' if restore else 'after'
    output = []
    for row, path, info, raw, text, ending in staged:
        output.append((path, transform(row, raw, restore=restore), stat.S_IMODE(info.st_mode)))
    # Refuse before any writes if another input changed during validation.
    for row, path, info, raw, text, ending in staged:
        now = ordinary(path)
        if (now.st_dev, now.st_ino, now.st_mode) != (info.st_dev, info.st_ino, info.st_mode) or path.read_bytes() != raw:
            raise ValueError('Compiler input changed during validation')
    if write and current != desired:
        for path, raw, mode in output:
            # Existing private source files retain their owner and DACL.
            with path.open('wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            if stat.S_IMODE(path.stat().st_mode) != mode:
                raise ValueError('Compiler input mode changed')
    return {'profile': current, 'desiredProfile': desired, 'files': len(rows),
            'writes': len(rows) if write and current != desired else 0,
            'scope': 'Windows compiler integration only'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    print(json.dumps(apply(args.root, write=args.apply, restore=args.restore), sort_keys=True))
