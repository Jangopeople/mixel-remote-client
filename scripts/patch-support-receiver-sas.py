#!/usr/bin/env python3
"""Exact terminal scoped SAS layer, after receiver and native receipt ownership."""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

HERE = Path(__file__).absolute().parent
PLAN = HERE / 'support-receiver-sas-plan.json'
PLAN_SHA = '74d5c1fd6fd2ac343f04317cac8f723a7133e801e83cf93ecb2402c3e2740e9b'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(raw):
    text = raw.decode('utf-8', errors='strict')
    logical = text.replace('\r\n', '\n')
    newline = '\r\n' if '\r\n' in text else '\n'
    if '\r' in logical or logical.replace('\n', newline) != text:
        raise ValueError('Mixed scoped SAS source newlines')
    return logical, newline


def root_path(root):
    root = Path(root).absolute()
    if 'worktrees' in root.parts or root.resolve(strict=True) != root:
        raise ValueError('Ordinary canonical scoped SAS source required')
    for part in (root, *root.parents):
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked scoped SAS source ancestry')
    return root


def read(root, name, absent=False):
    parts = Path(name).parts
    if not parts or Path(name).is_absolute() or any(p in ('.', '..', 'worktrees') for p in parts):
        raise ValueError('Unsafe scoped SAS target name')
    path = root
    for index, part in enumerate(parts):
        path /= part
        try:
            info = path.lstat()
        except FileNotFoundError:
            if absent and index == len(parts) - 1:
                return path, None, None
            raise ValueError('Missing scoped SAS source: ' + name) from None
        if index < len(parts) - 1:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('Linked scoped SAS source parent')
        elif (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
              or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ValueError('Unsafe scoped SAS source: ' + name)
    raw = path.read_bytes()
    after = path.lstat()
    fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode, s.st_nlink)
    if fields(info) != fields(after):
        raise ValueError('Scoped SAS source changed while reading')
    return path, raw, stat.S_IMODE(info.st_mode)


def unique(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError('Duplicate scoped SAS plan key')
        value[name] = item
    return value


def plan():
    _, raw, _ = read(HERE, PLAN.name)
    logical, _ = canonical(raw)
    if sha(logical.encode()) != PLAN_SHA:
        raise ValueError('Scoped SAS plan pin drift')
    value = json.loads(logical, object_pairs_hook=unique)
    if value['schema'] != 'mixel-receiver-scoped-sas-v1' or len(value['files']) != 4:
        raise ValueError('Scoped SAS plan shape changed')
    if value.get('readOnlyProfiles') != ['fresh', 'prior', 'native', 'preUinput']:
        raise ValueError('Scoped SAS complete prefix profiles changed')
    names = [row['name'] for row in value['files']] + [value['owned']['name']]
    if len(set(names)) != 5:
        raise ValueError('Duplicate scoped SAS source owner')
    _, template, _ = read(HERE, value['owned']['template'])
    template = canonical(template)[0].encode()
    if sha(template) != value['owned']['sha256']:
        raise ValueError('Scoped SAS template drift')
    return value, template


def inspect(root):
    root = root_path(root)
    value, template = plan()
    current = {}
    profiles = {'fresh', 'prior', 'native', 'preUinput', 'before', 'after'}
    for row in value['files']:
        item = read(root, row['name'], row['freshSha256'] is None)
        current[row['name']] = item
        if item[1] is not None and item[2] != (0o666 if os.name == 'nt' else row['mode']):
            raise ValueError('Scoped SAS inherited source mode changed')
        digest = None if item[1] is None else sha(canonical(item[1])[0].encode())
        profiles &= {profile for profile in profiles if digest == row[profile + 'Sha256']}
    owned = value['owned']
    item = read(root, owned['name'], True)
    current[owned['name']] = item
    if item[1] is None:
        profiles.discard('after')
    else:
        mode = 0o666 if os.name == 'nt' else owned['mode']
        if sha(canonical(item[1])[0].encode()) != owned['sha256'] or item[2] != mode:
            raise ValueError('Scoped SAS owned module changed')
        profiles &= {'after'}
    if len(profiles) != 1:
        raise ValueError('Changed, mixed or partial scoped SAS source')
    return root, value, template, profiles.pop(), current


def transform(raw, row, restore=False):
    text, newline = canonical(raw)
    before, after = ('afterSha256', 'beforeSha256') if restore else ('beforeSha256', 'afterSha256')
    if sha(text.encode()) != row[before]:
        raise ValueError('Scoped SAS whole input profile changed')
    pairs = reversed(row['changes']) if restore else row['changes']
    for pair in pairs:
        old, new = (pair['after'], pair['before']) if restore else (pair['before'], pair['after'])
        if not old or not new or text.count(old) != 1:
            raise ValueError('Scoped SAS exact hunk cardinality changed')
        text = text.replace(old, new, 1)
    if sha(text.encode()) != row[after]:
        raise ValueError('Scoped SAS whole output profile changed')
    return text.replace('\n', newline).encode()


def recheck(root, value, current):
    for name, old in current.items():
        if read(root, name, old[1] is None)[1:] != old[1:]:
            raise ValueError('Scoped SAS destination changed before mutation')


def atomic(path, raw, mode):
    fd, temporary = tempfile.mkstemp(prefix='.mixel-scoped-sas-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(root, restore=False, check=False):
    root, value, template, profile, current = inspect(root)
    if profile in ('fresh', 'prior', 'native', 'preUinput'):
        if not restore:
            raise ValueError('Scoped SAS requires complete predecessor branding')
        return {'profile': profile, 'writes': 0, 'files': len(current)}
    wanted = 'before' if restore else 'after'
    if profile == wanted:
        return {'profile': profile, 'writes': 0, 'files': len(current)}
    output = {row['name']: transform(current[row['name']][1], row, restore) for row in value['files']}
    owner = value['owned']
    newline = canonical(current['src/ipc.rs'][1])[1]
    output[owner['name']] = None if restore else template.decode().replace('\n', newline).encode()
    recheck(root, value, current)
    if check:
        return {'profile': profile, 'plannedProfile': wanted, 'writes': 0, 'plannedWrites': len(output)}
    for name, raw in output.items():
        _, old, mode = current[name]
        if read(root, name, old is None)[1:] != (old, mode):
            raise ValueError('Scoped SAS target changed during commit')
        if raw is None:
            (root / name).unlink()
        else:
            atomic(root / name, raw, owner['mode'] if mode is None else mode)
    if inspect(root)[3] != wanted:
        raise ValueError('Scoped SAS did not produce complete output')
    return {'profile': wanted, 'writes': len(output), 'files': len(current)}


def is_current(root):
    return inspect(root)[3] == 'after'


@contextlib.contextmanager
def previous(root):
    root, value, _, profile, current = inspect(root)
    path = HERE / 'patch-support-input-lifecycle.py'
    spec = importlib.util.spec_from_file_location('mixel_sas_projection_policy', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    engine = module.namespace(); prior = engine['prior_namespace']()
    compiler = prior['prior_compiler'](prior['prior_namespace']())
    compiler['validate_projection_sources'](root)
    before = engine['source_inventory'](root, compiler)
    try:
        with tempfile.TemporaryDirectory(prefix='mixel-scoped-sas-prior-') as temporary:
            stage = Path(temporary).resolve() / 'source'
            shutil.copytree(root, stage, symlinks=True,
                ignore=lambda directory, names: compiler['projection_ignored'](root, directory, names) | {'worktrees'})
            if profile == 'after':
                apply(stage, restore=True)
            recheck(root, value, current)
            yield stage
    finally:
        if engine['source_inventory'](root, compiler) != before:
            raise ValueError('Scoped SAS inverse changed supplied source')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(apply(args.root, args.restore, args.check), sort_keys=True))


if __name__ == '__main__':
    main()
