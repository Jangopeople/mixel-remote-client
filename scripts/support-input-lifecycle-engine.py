#!/usr/bin/env python3
"""Strict whole-tree terminal source patching; no app or provider execution."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import unicodedata

SCRIPTS = Path(__file__).absolute().parent
PRIOR_NAME = 'patch-support-uri-routing.py'
PRIOR_SHA = '874a1543113472448d28935526003a9d383b1ae4592594301d01ba5f5ca921c4'
SCHEMA = 'mixel-terminal-source-plan-v1'
PROFILES = ('fresh', 'original', 'final')
MAX_SOURCE = 1_000_000


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(raw):
    logical = raw.replace(b'\r\n', b'\n')
    newline = b'\r\n' if b'\r\n' in raw else b'\n'
    if b'\r' in logical or logical.replace(b'\n', newline) != raw:
        raise RuntimeError('Mixed or unsupported terminal source newlines')
    try:
        logical.decode('utf-8', errors='strict')
    except UnicodeDecodeError as error:
        raise RuntimeError('Terminal source is not UTF-8') from error
    return logical, newline


def safe_name(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or any(ord(character) < 32 or ord(character) == 127 for character in name)):
        raise RuntimeError('Unsafe terminal source name')
    path = PurePosixPath(name)
    reserved = {'con', 'prn', 'aux', 'nul', *('com' + str(i) for i in range(1, 10)),
                *('lpt' + str(i) for i in range(1, 10))}
    if path.is_absolute() or any(part in ('', '.', '..', '.git')
            or part.endswith((' ', '.')) or part.split('.')[0].casefold() in reserved
            for part in name.split('/')):
        raise RuntimeError('Unsafe terminal source name')
    return Path(*path.parts)


def safe_root(root):
    root = Path(root).absolute()
    if '..' in root.parts:
        raise RuntimeError('Unsafe terminal root traversal')
    for path in [*reversed(root.parents), root]:
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise RuntimeError('Unsafe terminal source parent')
    return root


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns)


def source_path(root, name):
    path = safe_root(root) / safe_name(name)
    safe_root(path.parent)
    return path


def read_source(root, name, absent=False):
    path = source_path(root, name)
    try:
        before = path.lstat()
    except FileNotFoundError:
        if absent:
            return {'path': path, 'raw': None, 'stamp': None, 'newline': None, 'sha256': None}
        raise RuntimeError('Missing terminal source: ' + name)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or not 0 < before.st_size <= MAX_SOURCE
            or getattr(before, 'st_file_attributes', 0) & 0x400):
        raise RuntimeError('Terminal source must be a single-link regular file: ' + name)
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        if identity(os.fstat(stream.fileno())) != identity(before):
            raise RuntimeError('Terminal source changed before read')
        raw = stream.read(MAX_SOURCE + 1)
        if identity(os.fstat(stream.fileno())) != identity(before):
            raise RuntimeError('Terminal source changed during read')
    if len(raw) > MAX_SOURCE or identity(path.lstat()) != identity(before):
        raise RuntimeError('Terminal source changed after read')
    logical, newline = canonical(raw)
    return {'path': path, 'raw': raw, 'stamp': identity(before),
            'newline': newline, 'sha256': digest(logical)}


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError('Duplicate terminal plan JSON key')
        result[key] = value
    return result


def validate_plan(plan):
    if set(plan) != {'schema', 'files'} or plan['schema'] != SCHEMA:
        raise RuntimeError('Changed terminal plan schema')
    rows = plan['files']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 64:
        raise RuntimeError('Invalid terminal plan file count')
    names = []
    for row in rows:
        if set(row) != {'name', 'profiles', 'changes', 'newOwned', 'mode', 'newlineFrom'}:
            raise RuntimeError('Changed terminal file plan schema')
        name = row['name']; safe_name(name); names.append(name)
        pins = row['profiles']
        if (not isinstance(pins, dict) or set(pins) != set(PROFILES)
                or any(value is not None and (not isinstance(value, str) or
                    re.fullmatch('[0-9a-f]{64}', value) is None) for value in pins.values())):
            raise RuntimeError('Invalid terminal whole-source profiles')
        if type(row['newOwned']) is not bool or type(row['mode']) is not int:
            raise RuntimeError('Invalid terminal ownership or mode')
        if row['newOwned']:
            if (pins['fresh'] is not None or pins['original'] is not None or pins['final'] is None
                    or not name.startswith('src/') or not name.endswith('.rs')
                    or row['mode'] not in (0o600, 0o644)
                    or not isinstance(row['newlineFrom'], str)):
                raise RuntimeError('Invalid new owned Rust source plan')
            safe_name(row['newlineFrom'])
        elif (any(value is None for value in pins.values()) or row['mode'] != 0
                or row['newlineFrom'] is not None or pins['original'] == pins['final']):
            raise RuntimeError('Invalid existing terminal source plan')
        if not isinstance(row['changes'], list) or not row['changes']:
            raise RuntimeError('Missing exact terminal changes')
        for change in row['changes']:
            if set(change) != {'before', 'after', 'beforeSha256', 'afterSha256'}:
                raise RuntimeError('Changed terminal chunk schema')
            for side in ('before', 'after'):
                value = change[side]
                if (not isinstance(value, str) or '\r' in value
                        or len(value.encode()) > MAX_SOURCE
                        or digest(value.encode()) != change[side + 'Sha256']):
                    raise RuntimeError('Changed terminal replacement chunk')
            if not row['newOwned'] and (not change['before'] or not change['after']):
                raise RuntimeError('Empty existing terminal replacement')
        if row['newOwned']:
            if (len(row['changes']) != 1 or row['changes'][0]['before'] != ''
                    or not row['changes'][0]['after']
                    or digest(row['changes'][0]['after'].encode()) != pins['final']):
                raise RuntimeError('Invalid complete owned source content')
    aliases = {unicodedata.normalize('NFC', name).casefold() for name in names}
    if names != sorted(set(names)) or len(aliases) != len(names):
        raise RuntimeError('Terminal file plans must be unique and sorted')
    by_name = {row['name']: row for row in rows}
    for row in rows:
        if row['newOwned'] and (row['newlineFrom'] not in by_name
                or by_name[row['newlineFrom']]['newOwned']):
            raise RuntimeError('New source newline owner must be a pinned existing target')
    return plan


def load_plan(path, expected_sha):
    path = Path(path).absolute()
    state = read_source(path.parent, path.name)
    logical, _ = canonical(state['raw'])
    if not isinstance(expected_sha, str) or digest(logical) != expected_sha:
        raise RuntimeError('Changed pinned terminal source plan')
    plan = json.loads(logical, object_pairs_hook=strict_object,
        parse_constant=lambda value: (_ for _ in ()).throw(RuntimeError('Nonfinite terminal plan JSON')))
    return validate_plan(plan)


def inspect(root, plan):
    validate_plan(plan)
    states = {row['name']: read_source(root, row['name'], absent=row['newOwned'])
              for row in plan['files']}
    candidates = [profile for profile in PROFILES if all(
        states[row['name']]['sha256'] == row['profiles'][profile] for row in plan['files'])]
    if not candidates:
        raise RuntimeError('Changed or partial terminal whole-tree profile')
    # Two labels can be genuinely byte-identical. Prefer composed original;
    # never infer fresh/original by a single target shared by both profiles.
    profile = 'original' if 'original' in candidates else candidates[0]
    return profile, states


def changed_content(row, raw, restore):
    pins = row['profiles']
    if row['newOwned']:
        if restore:
            return None
        return row['changes'][0]['after'].encode()
    logical, newline = canonical(raw)
    expected, wanted = (pins['final'], pins['original']) if restore else (pins['original'], pins['final'])
    if digest(logical) == wanted:
        return raw
    if digest(logical) != expected:
        raise RuntimeError('Changed terminal transformation source')
    changes = reversed(row['changes']) if restore else row['changes']
    for change in changes:
        before, after = (change['after'], change['before']) if restore else (change['before'], change['after'])
        before, after = before.encode(), after.encode()
        if logical.count(before) != 1:
            raise RuntimeError('Changed exact terminal source replacement')
        logical = logical.replace(before, after, 1)
    if digest(logical) != wanted:
        raise RuntimeError('Terminal whole-source inverse mismatch')
    return logical.replace(b'\n', newline)


def projected(plan, profile, states, restore):
    if profile == 'fresh':
        if not restore:
            raise RuntimeError('Terminal final layer requires complete branding')
        return {name: state['raw'] for name, state in states.items()}
    output = {}
    for row in plan['files']:
        state = states[row['name']]
        if profile == ('original' if restore else 'final'):
            output[row['name']] = state['raw']
        else:
            raw = changed_content(row, state['raw'], restore)
            if raw is not None and row['newOwned']:
                raw = raw.replace(b'\n', states[row['newlineFrom']]['newline'])
            output[row['name']] = raw
    return output


def prior_namespace():
    source = read_source(SCRIPTS, PRIOR_NAME)
    if source['sha256'] != PRIOR_SHA:
        raise RuntimeError('Changed terminal prior verifier')
    namespace = {'__name__': 'mixel_terminal_prior', '__file__': str(source['path'])}
    exec(compile(canonical(source['raw'])[0].decode(), str(source['path']), 'exec'), namespace)
    return namespace


def source_inventory(root, compiler):
    result = {}
    for directory, children, files in os.walk(root, followlinks=False):
        ignored = compiler['projection_ignored'](root, directory, children) | {'.git'}
        children[:] = [name for name in children if name not in ignored]
        for name in files:
            if name == '.git': continue
            path = Path(directory) / name; info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                raise RuntimeError('Unsafe terminal prior source inventory')
            state = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(131072), b''): state.update(chunk)
            if identity(path.lstat()) != identity(info):
                raise RuntimeError('Changed terminal prior source inventory')
            result[path.relative_to(root).as_posix()] = (identity(info), state.hexdigest())
    return result


def write_private(stage, output):
    for name, raw in output.items():
        path = source_path(stage, name)
        if raw is None:
            if path.exists() or path.is_symlink(): path.unlink()
        else:
            path.write_bytes(raw)


@contextlib.contextmanager
def private_inverse(root, plan, profile, states):
    prior = prior_namespace()
    compiler = prior['prior_compiler'](prior['prior_namespace']())
    compiler['validate_projection_sources'](root)
    before = source_inventory(root, compiler)
    inverse = projected(plan, profile, states, restore=True)
    try:
        with tempfile.TemporaryDirectory(prefix='mixel-terminal-prior-') as temporary:
            stage = Path(temporary).resolve() / 'source'
            shutil.copytree(root, stage, symlinks=True,
                ignore=lambda directory, names: compiler['projection_ignored'](root, directory, names))
            write_private(stage, inverse)
            prior['apply'](stage, check=True, restore=True, verify=True)
            yield stage
    finally:
        if source_inventory(root, compiler) != before:
            raise RuntimeError('Terminal private prior proof changed supplied source')


def validate_prior(root, plan, profile, states):
    with private_inverse(root, plan, profile, states):
        pass


def recheck(root, plan, profile, states):
    current_profile, current = inspect(root, plan)
    if current_profile != profile or any(current[name] != state for name, state in states.items()):
        raise RuntimeError('Terminal source changed after complete prior check')


def commit_one(root, row, state, raw):
    path = source_path(root, row['name'])
    now = read_source(root, row['name'], absent=row['newOwned'])
    if now != state:
        raise RuntimeError('Terminal source changed before write')
    if raw == state['raw']: return False
    if raw is None:
        # This branch is possible only for a fully pinned, present, new-owned
        # final Rust module. Unknown bytes/types/links were rejected above.
        if not row['newOwned'] or state['sha256'] != row['profiles']['final']:
            raise RuntimeError('Refusing unowned terminal source removal')
        path.unlink()
        return True
    flags = os.O_WRONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    creating = state['raw'] is None
    if creating:
        if not row['newOwned']: raise RuntimeError('Refusing unowned terminal source creation')
        flags |= os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, row['mode'] if creating else 0o600)
    try:
        info = os.fstat(descriptor)
        if creating:
            if info.st_nlink != 1 or not stat.S_ISREG(info.st_mode) or identity(path.lstat()) != identity(info):
                raise RuntimeError('Terminal created source ownership changed')
        elif identity(info) != state['stamp'] or identity(path.lstat()) != state['stamp']:
            raise RuntimeError('Terminal source changed before bound write')
        offset = 0
        while offset < len(raw):
            count = os.write(descriptor, raw[offset:])
            if count <= 0: raise RuntimeError('Incomplete terminal source write')
            offset += count
        os.ftruncate(descriptor, len(raw))
    finally:
        os.close(descriptor)
    return True


def apply(root, plan, check=False, restore=False):
    root = safe_root(root)
    profile, states = inspect(root, plan)
    output = projected(plan, profile, states, restore)
    validate_prior(root, plan, profile, states)
    # Every target, including absent owned modules, is rechecked before the
    # first supplied-tree write. Per-target bound checks repeat at commit.
    recheck(root, plan, profile, states)
    writes = 0
    if not check:
        for row in plan['files']:
            writes += int(commit_one(root, row, states[row['name']], output[row['name']]))
    return {'profile': profile, 'plannedProfile': 'fresh' if profile == 'fresh' else
            'original' if restore else 'final', 'writes': writes, 'checkedFiles': len(states)}


@contextlib.contextmanager
def previous(root, plan):
    root = safe_root(root)
    profile, states = inspect(root, plan)
    with private_inverse(root, plan, profile, states) as stage:
        recheck(root, plan, profile, states)
        yield stage
        recheck(root, plan, profile, states)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--before-branding', action='store_true')
    parser.add_argument('--after-branding', action='store_true')
    args = parser.parse_args()
    if args.before_branding and not (args.restore and args.check):
        parser.error('--before-branding requires --restore --check')
    if args.after_branding and args.restore:
        parser.error('--after-branding cannot restore')
    if args.before_branding and args.after_branding:
        parser.error('Branding phases are exclusive')
    plan = load_plan(args.plan, args.plan_sha256)
    print(json.dumps(apply(args.root, plan, args.check, args.restore), sort_keys=True))


if __name__ == '__main__':
    main()
