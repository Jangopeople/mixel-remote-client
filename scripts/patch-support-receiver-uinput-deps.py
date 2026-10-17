#!/usr/bin/env python3
"""Exact offline pinned raw-FD adapters, after the complete own-uinput layer.

Only genuine existing File/UnixStream handles are exposed; no effect, backend,
credential, capability, privilege, or dependency edge changes. Inverse this owner
first. A prospective check never creates a dependency or writes any Git metadata.
"""
import argparse
import base64
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import zlib

HERE = Path(__file__).resolve().parent
PLAN_SHA = 'd11f764a8c484b09485aae9cb4caa61a2626cc60432cf6be5615a109104e9b59'
SPEC = importlib.util.spec_from_file_location('mixel_uinput_dependency_files', HERE/'patch-support-receiver-uinput.py')
FILES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FILES)


def oid(kind, raw):
    return hashlib.sha1(kind.encode()+b' '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


def load_plan():
    _, raw, _ = FILES.state(HERE, 'support-receiver-uinput-deps-plan.json')
    if FILES.sha(FILES.canonical(raw)[0]) != PLAN_SHA:
        raise ValueError('uinput dependency plan drift')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate uinput dependency key')
            result[key] = value
        return result
    plan = json.loads(raw, object_pairs_hook=unique)
    if plan['schema'] != 'mixel-uinput-deps-v1' or len(plan['files']) != 2 or len(plan['dependencies']) != 2:
        raise ValueError('uinput dependency plan schema/cardinality changed')
    for row in plan['files']:
        for profile in ('fresh', 'before', 'after'):
            if FILES.sha(row[profile+'Source'].encode()) != row[profile+'Sha256']:
                raise ValueError('uinput dependency root source drift')
    for dep in plan['dependencies']:
        names = set()
        for row in dep['files']:
            if row['path'] in names or row['mode'] not in (0o644, 0o755):
                raise ValueError('uinput dependency inventory drift')
            names.add(row['path'])
            raw = base64.b64decode(row['raw'], validate=True)
            if oid('blob', raw) != row['oid'] or FILES.sha(raw) != row['beforeSha256']:
                raise ValueError('uinput dependency original blob drift')
            if row['path'] == dep['target']:
                raw += dep['addition'].encode()
            if FILES.sha(raw) != row['afterSha256']:
                raise ValueError('uinput dependency prospective blob drift')
        if dep['target'] not in names:
            raise ValueError('missing uinput dependency target')
        for entry in dep['objects']:
            raw = base64.b64decode(entry['raw'], validate=True)
            if entry['kind'] not in ('tree', 'commit') or oid(entry['kind'], raw) != entry['oid']:
                raise ValueError('uinput dependency Git object drift')
        if sum(entry['kind'] == 'commit' and entry['oid'] == dep['pin'] for entry in dep['objects']) != 1:
            raise ValueError('uinput dependency commit identity drift')
    return plan


def git(root, *arguments):
    # Read-only validation never refreshes the index. Ignore external Git config,
    # hooks, credentials, alternate object databases and worktree overrides.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.check_output(['git', '--no-optional-locks', '-c', 'core.hooksPath='+os.devnull,
        '-C', str(root), *arguments], env=env, stderr=subprocess.PIPE, timeout=20)


def directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('unsafe uinput dependency directory')


def config(dep):
    return ('[core]\n\trepositoryformatversion = 0\n\tbare = false\n\tfilemode = '
        +('false' if os.name == 'nt' else 'true')+'\n\tautocrlf = false\n[remote "origin"]\n\turl = '
        +dep['url']+'\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n').encode()


def walk(path, allowed_files, allowed_directories):
    """Check every child name before os.walk can descend into that child."""
    for current, directories, files in os.walk(path, topdown=True, followlinks=False):
        if 'worktrees' in directories or 'worktrees' in files:
            raise ValueError('worktree dependency paths are forbidden')
        for name in directories:
            child = Path(current)/name
            relative = child.relative_to(path).as_posix()
            if relative not in allowed_directories:
                raise ValueError('unexpected uinput dependency directory')
            directory(child)
        for name in files:
            child = Path(current)/name
            relative = child.relative_to(path).as_posix()
            if relative not in allowed_files:
                raise ValueError('unexpected uinput dependency file')
            yield child


def parents(names):
    return {parent.as_posix() for name in names for parent in Path(name).parents if parent != Path('.')}


def inventory(root, dep):
    path = root/dep['path']
    for parent in (path, path.parent):
        directory(parent)
    metadata = path/'.git'
    directory(metadata)
    object_names = {row['oid'] for row in dep['files']+dep['objects']}
    metadata_files = {'HEAD', 'config', 'shallow', 'index'}|{'objects/'+value[:2]+'/'+value[2:] for value in object_names}
    metadata_directories = parents(metadata_files)|{'refs', 'refs/heads'}
    found_metadata = set()
    for item in walk(metadata, metadata_files, metadata_directories):
        name = item.relative_to(metadata).as_posix()
        FILES.state(metadata, name)
        found_metadata.add(name)
    if found_metadata != metadata_files:
        raise ValueError('partial uinput dependency Git metadata')
    # Authenticate local config before invoking Git: no include/filter/helper,
    # alternate object database, replacement ref, worktree or global override.
    if (FILES.state(metadata, 'config')[1] != config(dep)
            or FILES.state(metadata, 'HEAD')[1] != (dep['pin']+'\n').encode()
            or FILES.state(metadata, 'shallow')[1] != (dep['pin']+'\n').encode()):
        raise ValueError('uinput dependency owned Git config changed')
    if git(path, 'rev-parse', 'HEAD').decode().strip() != dep['pin'] or git(path, 'config', '--get', 'remote.origin.url').decode().strip() != dep['url']:
        raise ValueError('uinput dependency identity changed')
    expected_tree = {(row['path'], row['oid'], row['mode']) for row in dep['files']}
    actual_tree = set()
    for entry in git(path, 'ls-tree', '-rz', '--full-tree', 'HEAD').split(b'\0'):
        if not entry:
            continue
        header, name = entry.split(b'\t', 1)
        mode, kind, object_id = header.decode().split()
        if kind != 'blob':
            raise ValueError('unsupported uinput dependency tree entry')
        actual_tree.add((name.decode(), object_id, int(mode, 8)&0o777))
    if actual_tree != expected_tree:
        raise ValueError('uinput dependency tracked tree changed')
    expected = {row['path']: row for row in dep['files']}
    result, found, profiles = {}, set(), {'before', 'after'}
    expected_files = set(expected)
    # The metadata subtree was authenticated separately. Prune it before this
    # source traversal; neither traversal ever visits a worktrees child.
    for item in walk_source(path, expected_files):
        relative = item.relative_to(path)
        name = relative.as_posix()
        if name not in expected:
            raise ValueError('unexpected uinput dependency file')
        _, raw, mode = FILES.state(path, name)
        row = expected[name]
        if mode != row['mode'] and not (os.name == 'nt' and mode == 0o666):
            raise ValueError('uinput dependency file mode changed')
        # Snapshot objects are exact bytes on every platform; the two Rust
        # adapter files also accept a complete canonical CRLF representation.
        logical = FILES.canonical(raw)[0] if name == dep['target'] else raw
        profiles &= {profile for profile in ('before', 'after') if FILES.sha(logical) == row[profile+'Sha256']}
        result[name] = (raw, mode)
        found.add(name)
    if found != set(expected) or len(profiles) != 1:
        raise ValueError('changed/partial uinput dependency source')
    return profiles.pop(), result


def walk_source(path, expected_files):
    allowed_directories = parents(expected_files)
    for current, directories, files in os.walk(path, topdown=True, followlinks=False):
        if 'worktrees' in directories or 'worktrees' in files:
            raise ValueError('worktree dependency paths are forbidden')
        if Path(current) == path:
            directories.remove('.git')
        for name in directories:
            child = Path(current)/name
            if child.relative_to(path).as_posix() not in allowed_directories:
                raise ValueError('unexpected uinput dependency directory')
            directory(child)
        for name in files:
            child = Path(current)/name
            if child.relative_to(path).as_posix() not in expected_files:
                raise ValueError('unexpected uinput dependency file')
            yield child


def inspect(root):
    root = FILES.root_path(root)
    plan = load_plan()
    current, profiles = {}, {'fresh', 'before', 'after'}
    for row in plan['files']:
        value = FILES.state(root, row['name'])
        current[row['name']] = value
        digest = FILES.sha(FILES.canonical(value[1])[0])
        profiles &= {profile for profile in profiles if digest == row[profile+'Sha256']}
    deps = {}
    parent = root/'.mixel-deps'
    if parent.exists() or parent.is_symlink():
        directory(parent)
    for dep in plan['dependencies']:
        path = root/dep['path']
        if not path.exists() and not path.is_symlink():
            deps[dep['name']] = None
            profiles.discard('after')
        else:
            profile, values = inventory(root, dep)
            if profile != 'after':
                # These complete directories are owned by this terminal layer;
                # pre-existing or half-restored clones cannot be adopted.
                raise ValueError('unowned original uinput dependency clone')
            deps[dep['name']] = values
            profiles &= {'after'}
    if any(value is None for value in deps.values()) and any(value is not None for value in deps.values()):
        raise ValueError('partial uinput dependency installation')
    if len(profiles) != 1:
        raise ValueError('changed, partial, or mixed uinput dependency profile')
    return root, plan, profiles.pop(), current, deps


def is_current(root):
    return inspect(root)[2] == 'after'


def recheck(root, current, deps):
    _root, _plan, _profile, actual, installed = inspect(root)
    if {name: value[1:] for name, value in actual.items()} != {name: value[1:] for name, value in current.items()} or installed != deps:
        raise ValueError('uinput dependency destination changed before mutation')


def snapshot(path, dep):
    path.mkdir()
    metadata = path/'.git'
    (metadata/'objects').mkdir(parents=True)
    (metadata/'refs/heads').mkdir(parents=True)
    (metadata/'HEAD').write_text(dep['pin']+'\n')
    (metadata/'shallow').write_text(dep['pin']+'\n')
    (metadata/'config').write_bytes(config(dep))
    def object_file(kind, raw, expected):
        if oid(kind, raw) != expected:
            raise ValueError('uinput dependency snapshot object mismatch')
        destination = metadata/'objects'/expected[:2]/expected[2:]
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(zlib.compress(kind.encode()+b' '+str(len(raw)).encode()+b'\0'+raw))
    for row in dep['files']:
        raw = base64.b64decode(row['raw'], validate=True)
        object_file('blob', raw, row['oid'])
        destination = path/row['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw+dep['addition'].encode() if row['path'] == dep['target'] else raw)
        destination.chmod(row['mode'])
    for row in dep['objects']:
        object_file(row['kind'], base64.b64decode(row['raw'], validate=True), row['oid'])
    git(path, 'read-tree', 'HEAD')
    return path


def apply(root, restore=False, check=False):
    root, plan, profile, current, deps = inspect(root)
    desired = 'before' if restore else 'after'
    if profile == 'fresh':
        if not restore:
            raise ValueError('uinput dependency adapters require complete branding')
        return {'profile': profile, 'writes': 0}
    if profile == desired:
        return {'profile': profile, 'writes': 0}
    if not restore and not FILES.is_current(root):
        raise ValueError('uinput dependency adapters require complete own-uinput source')
    output = {}
    for row in plan['files']:
        newline = FILES.canonical(current[row['name']][1])[1]
        output[row['name']] = row[desired+'Source'].encode().replace(b'\n', newline)
    recheck(root, current, deps)
    if check:
        return {'profile': profile, 'plannedProfile': desired, 'writes': 0,
                'plannedRootWrites': 2, 'plannedDependencies': 2}
    if restore:
        # Remove only these fully authenticated terminal-owned snapshots. The
        # private inverse leaves every supplied dependency and its .git intact.
        for dep in plan['dependencies']:
            if inventory(root, dep)[1] != deps[dep['name']]:
                raise ValueError('uinput dependency changed during inverse')
            shutil.rmtree(root/dep['path'])
    else:
        parent = root/'.mixel-deps'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.mixel-uinput-dependency-', dir=parent) as temporary:
            staging = Path(temporary).resolve(strict=True)
            for dep in plan['dependencies']:
                snapshot(staging/dep['name'], dep)
                surrogate = dict(dep, path=dep['name'])
                if inventory(staging, surrogate)[0] != 'after':
                    raise ValueError('uinput dependency prospective snapshot mismatch')
            recheck(root, current, deps)
            for dep in plan['dependencies']:
                (staging/dep['name']).rename(root/dep['path'])
    for name, raw in output.items():
        if FILES.state(root, name)[1:] != current[name][1:]:
            raise ValueError('uinput dependency root changed during commit')
        FILES.atomic(current[name][0], raw, current[name][2])
    if inspect(root)[2] != desired:
        raise ValueError('uinput dependency commit did not produce complete profile')
    return {'profile': desired, 'writes': 2, 'dependencies': 2}


@contextlib.contextmanager
def previous(root):
    root, _plan, profile, current, deps = inspect(root)
    def ignored(directory_name, names):
        relative = Path(directory_name).relative_to(root).as_posix()
        result = {'worktrees'}&set(names)
        if relative == '.':
            result |= {'build', 'target', '.dart_tool', '__pycache__'}&set(names)
        elif relative == 'flutter':
            result |= {'build', '.dart_tool'}&set(names)
        elif relative in ('flutter/macos/Flutter', 'flutter/windows/flutter', 'flutter/linux/flutter'):
            result |= {'ephemeral'}&set(names)
        return result
    with tempfile.TemporaryDirectory(prefix='mixel-uinput-deps-prior-') as temporary:
        stage = Path(temporary).resolve(strict=True)/'source'
        shutil.copytree(root, stage, symlinks=True, ignore=ignored)
        if profile == 'after':
            apply(stage, restore=True)
        recheck(root, current, deps)
        try:
            yield stage
        finally:
            recheck(root, current, deps)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(apply(args.root, args.restore, args.check), sort_keys=True))


if __name__ == '__main__':
    main()
