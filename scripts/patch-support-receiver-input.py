#!/usr/bin/env python3
"""Exact reversible receiver input-capability layer, after the native receipt adapter."""
import argparse
import contextlib
import importlib.util
import shutil
import tempfile
import hashlib
import json
import stat
from pathlib import Path

HERE = Path(__file__).absolute().parent
PLAN = HERE / 'support-receiver-input-plan.json'
PLAN_SHA = "0c52a74161368b6083bc30ed5bc009293be99ffacc383ff6db4b96938d336513"
LEAVES = {'src/server/mixel_receiver_input.rs': 'support-receiver-input.rs',
          'src/server/mixel_receiver_input_windows.rs': 'support-receiver-input-windows.rs'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(raw):
    text = raw.decode('utf-8')
    logical = text.replace('\r\n', '\n')
    if '\r' in logical or ('\r\n' in text and '\n' in text.replace('\r\n', '')):
        raise RuntimeError('Mixed receiver input newlines')
    return logical, '\r\n' if '\r\n' in text else '\n'


def ordinary(path, directory=False):
    info = path.lstat()
    if ((not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))
            or stat.S_ISLNK(info.st_mode) or (not directory and info.st_nlink != 1)
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise RuntimeError('Unsafe receiver source: ' + str(path))
    return info


def target(root, name, absent=False):
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise RuntimeError('Invalid receiver source name')
    path = root / relative
    for parent in (root, *path.parents):
        ordinary(parent, True)
    if not absent or path.exists() or path.is_symlink():
        ordinary(path)
    return path


def load_plan():
    ordinary(PLAN)
    raw = PLAN.read_bytes()
    if digest(canonical(raw)[0].encode()) != PLAN_SHA:
        raise RuntimeError('Receiver pinned plan drift')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise RuntimeError('Duplicate receiver plan key')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    if value['schema'] != 'mixel-receiver-input-v1':
        raise RuntimeError('Unexpected receiver plan')
    for name, template in LEAVES.items():
        source = HERE / template
        ordinary(source)
        if digest(canonical(source.read_bytes())[0].encode()) != value['templates'][name]:
            raise RuntimeError('Receiver template drift: ' + template)
    return value


def inspect(root, plan):
    root = Path(root).absolute()
    originals, logical, hashes = {}, {}, {}
    for name in plan['profiles']['fresh']:
        path = target(root, name, name in LEAVES)
        raw = path.read_bytes() if path.exists() else None
        originals[name] = raw
        if raw is None:
            hashes[name] = None
        else:
            text, newline = canonical(raw)
            logical[name] = (text, newline)
            hashes[name] = digest(text.encode())
    profiles = [key for key, profile in plan['profiles'].items() if hashes == profile]
    if len(profiles) != 1:
        raise RuntimeError('Changed, partial, or mixed receiver input source')
    profile = profiles[0]
    return root, profile, originals, logical


def is_current(root):
    # Changed/mixed source is an error, never interpreted as an absent layer.
    return inspect(root, load_plan())[1] == 'final'


@contextlib.contextmanager
def previous(root):
    """Expose the exact own-layer inverse privately, retaining supplied sources."""
    plan = load_plan()
    root, profile, originals, _logical = inspect(root, plan)
    # Reuse the pinned established projection filesystem policy, without
    # accepting/validating old source profiles through new terminal layers.
    path = HERE / 'patch-support-input-lifecycle.py'
    spec = importlib.util.spec_from_file_location('mixel_receiver_projection_policy', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    engine = module.namespace()
    prior = engine['prior_namespace']()
    compiler = prior['prior_compiler'](prior['prior_namespace']())
    compiler['validate_projection_sources'](root)
    before = engine['source_inventory'](root, compiler)
    try:
        with tempfile.TemporaryDirectory(prefix='mixel-receiver-prior-') as temporary:
            stage = Path(temporary).resolve() / 'source'
            shutil.copytree(root, stage, symlinks=True,
                ignore=lambda directory, names: compiler['projection_ignored'](root, directory, names) | {'worktrees'})
            apply(stage, restore=True)
            if inspect(root, plan)[1:3] != (profile, originals):
                raise RuntimeError('Receiver source changed during private inverse')
            yield stage
    finally:
        if engine['source_inventory'](root, compiler) != before:
            raise RuntimeError('Receiver inverse changed supplied source')


def apply(root, restore=False, check=False):
    plan = load_plan()
    root, profile, originals, logical = inspect(root, plan)
    hashes = plan['profiles'][profile]
    if profile == 'fresh':
        if not restore:
            raise RuntimeError('Receiver input layer requires complete prior branding')
        return {'profile': profile, 'writes': 0, 'files': len(hashes)}
    destination = 'original' if restore else 'final'
    planned = {}
    for name in hashes:
        if name in LEAVES:
            result = None if restore else canonical((HERE / LEAVES[name]).read_bytes())[0].encode()
        else:
            text, newline = logical[name]
            if profile != destination:
                changes = list(reversed(plan['inverse'][name])) if restore else plan['changes'][name]
                for old, new in changes:
                    if text.count(old) != 1:
                        raise RuntimeError('Receiver exact source anchor drift: ' + name)
                    text = text.replace(old, new, 1)
            if digest(text.encode()) != plan['profiles'][destination][name]:
                raise RuntimeError('Receiver whole-source inverse mismatch: ' + name)
            result = text.replace('\n', newline).encode()
        if (None if result is None else digest(canonical(result)[0].encode())) != plan['profiles'][destination][name]:
            raise RuntimeError('Receiver final output mismatch: ' + name)
        planned[name] = result
    # No writes precede complete ownership/profile/template and target rechecks.
    for name, original in originals.items():
        path = target(root, name, name in LEAVES)
        if (path.read_bytes() if path.exists() else None) != original:
            raise RuntimeError('Receiver source changed during verification')
    writes = sum(originals[name] != raw for name, raw in planned.items())
    if not check:
        for name, raw in planned.items():
            if raw == originals[name]:
                continue
            path = root / name
            if raw is None:
                path.unlink()
            else:
                path.write_bytes(raw)
                if name in LEAVES:
                    path.chmod(0o644)
    return {'profile': profile, 'writes': writes, 'files': len(hashes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(apply(args.root, args.restore, args.check), sort_keys=True))


if __name__ == '__main__':
    main()
