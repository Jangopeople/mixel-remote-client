#!/usr/bin/env python3
"""Apply the exact terminal native acceptance correction after full branding."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

PLAN = Path(__file__).with_name('support-receiver-native-enigo-plan.json')
PLAN_SHA256 = '50231377c56e69ab25aa9756ef4068fffbdd66560d77e4c6e9908b8e9700e3b5'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def normalize(data):
    if b'\r' in data:
        if data.replace(b'\r\n', b'\n').find(b'\r') >= 0 or b'\n' in data.replace(b'\r\n', b''):
            raise ValueError('mixed or bare-CR native acceptance source')
        return data.replace(b'\r\n', b'\n'), b'\r\n'
    return data, b'\n'


def plan():
    value = PLAN.lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise ValueError('native acceptance plan is not a regular owned file')
    data = PLAN.read_bytes()
    if digest(data) != PLAN_SHA256:
        raise ValueError('native acceptance plan changed')
    result = json.loads(data)
    if result['schema'] != 'mixel-receiver-native-enigo-plan-v1':
        raise ValueError('unsupported native acceptance plan')
    return result['files']


def regular(root, name):
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('unsafe native acceptance path')
    path = root
    for index, part in enumerate(relative.parts):
        path = path / part
        value = path.lstat()
        if index != len(relative.parts) - 1:
            if not stat.S_ISDIR(value.st_mode):
                raise ValueError('native acceptance parent is not a directory: ' + name)
        elif not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
            raise ValueError('native acceptance source is not an unlinked regular file: ' + name)
    return path, path.read_bytes(), stat.S_IMODE(value.st_mode)


def transform(data, row, restore=False):
    normalized, newline = normalize(data)
    initial = row['afterSha256' if restore else 'beforeSha256']
    final = row['beforeSha256' if restore else 'afterSha256']
    if digest(normalized) != initial:
        raise ValueError('native acceptance source profile changed: ' + row['name'])
    text = normalized.decode('utf-8')
    for change in reversed(row['changes']) if restore else row['changes']:
        before, after = (change['after'], change['before']) if restore else (change['before'], change['after'])
        if text.count(before) != 1:
            raise ValueError('native acceptance replacement cardinality changed: ' + row['name'])
        text = text.replace(before, after, 1)
    output = text.encode()
    if digest(output) != final:
        raise ValueError('native acceptance output profile changed: ' + row['name'])
    return output.replace(b'\n', newline)


def apply(root, restore=False, check=False):
    root = Path(root).absolute()
    if 'worktrees' in root.parts:
        raise ValueError('worktree roots are forbidden')
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError('native acceptance root must be an ordinary directory')
    rows = plan()
    actual = [regular(root, row['name']) for row in rows]
    profiles = []
    for row, (_, data, _) in zip(rows, actual):
        normalized, _ = normalize(data)
        value = digest(normalized)
        if value == row['beforeSha256']: profiles.append('before')
        elif value == row['afterSha256']: profiles.append('after')
        else: raise ValueError('unapproved native acceptance source: ' + row['name'])
    if len(set(profiles)) != 1:
        raise ValueError('partial native acceptance profile')
    desired = 'before' if restore else 'after'
    if profiles[0] == desired:
        return
    if check:
        raise ValueError('required native acceptance profile is not present')
    output = [transform(data, row, restore) for row, (_, data, _) in zip(rows, actual)]
    # Every profile and every replacement is validated before the first write.
    for path, data, mode in actual:
        if regular(root, path.relative_to(root).as_posix())[1:] != (data, mode):
            raise ValueError('native acceptance source changed before writing')
    for (path, _, mode), data in zip(actual, output):
        descriptor, temporary = tempfile.mkstemp(prefix='.mixel-native-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    apply(args.root, args.restore, args.check)
    print('PASS: exact terminal native acceptance profile' + (' restored' if args.restore else ' validated'))


if __name__ == '__main__':
    main()
