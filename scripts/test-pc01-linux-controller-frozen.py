#!/usr/bin/env python3
"""Run the unchanged frozen31 inert controls in an owned historical source view.

The active fourteen workflows must first satisfy the manual-only policy. Only
the strictly projected controller workflow differs in the private view. The
old controller, old test, and each old declared dependency retain their exact
frozen bytes. No runtime actor, provider, compiler, native API, or Git command
is run; GitHub environment values are never fabricated or changed.
"""
import ast
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).absolute().parents[1]
FROZEN = {
    'scripts/pc01-linux-controller.py': '8df417fabfaf368216e2e74aceccedde5b68cb3f94ee664d42a2d728bd39aa50',
    '.github/workflows/pc01-linux-controller.yml': '2007f8deca8f2a60a0acde3a1f9fb8d816a85942891c6231c57f3a8202304980',
    'scripts/test-pc01-linux-controller.py': '33add732600dc7d292c39177933ab8d1ba612f74ab4b69144489508a4122d31b',
}
SPEC = importlib.util.spec_from_file_location(
    'mixel_frozen_controller_manual_policy', ROOT / 'scripts/manual_workflow_policy_projection.py')
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


def source_bytes(root, name):
    relative = Path(name)
    if (relative.is_absolute() or '..' in relative.parts or '\\' in name
            or any(part.casefold() == 'worktrees' for part in relative.parts)):
        raise ValueError('Unsafe frozen control source name')
    path = root
    for part in relative.parts[:-1]:
        path /= part
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Unsafe frozen control source ancestry')
    path = root / relative
    before = path.lstat()
    raw = POLICY.regular(path)
    after = path.lstat()
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                             info.st_mode, info.st_nlink)
    if identity(before) != identity(after) or len(raw) > 10_000_000:
        raise ValueError('Frozen control source changed while reading')
    return raw


def controls(root=ROOT):
    root = Path(root).absolute()
    POLICY.verify_active(root)
    physical, historical = {}, {}
    for name, expected in FROZEN.items():
        physical[name] = source_bytes(root, name)
        raw = physical[name]
        if name.startswith('.github/workflows/'):
            raw = POLICY.project_to_historical(raw, name)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Changed frozen controller/test/workflow source: ' + name)
        historical[name] = raw
    tree = ast.parse(historical['scripts/pc01-linux-controller.py'])
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'SOURCE_PINS'
                           for target in node.targets)]
    if len(assignments) != 1:
        raise ValueError('Frozen controller dependency inventory changed')
    pins = ast.literal_eval(assignments[0].value)
    for name, expected in pins.items():
        raw = source_bytes(root, name)
        if not name.startswith('scripts/') or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Changed frozen controller declared dependency: ' + name)
        physical[name] = historical[name] = raw
    scratch = root / '_scratch'
    scratch.mkdir(exist_ok=True)
    proof = scratch / 'manual-workflow-policy'
    for directory in (scratch, proof):
        if directory == proof:
            directory.mkdir(exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Unsafe owned frozen-control scratch directory')
    with tempfile.TemporaryDirectory(dir=proof, prefix='frozen-controls-') as temporary:
        stage = Path(temporary)
        for name, raw in historical.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        private_temp = stage / '_temp'
        private_temp.mkdir()
        env = os.environ.copy()
        env.update(TMPDIR=str(private_temp), MIXEL_CONTROLLER_SOURCE_ROOT=str(stage))
        try:
            result = subprocess.run([sys.executable, '-I', str(stage / 'scripts/test-pc01-linux-controller.py')],
                                    env=env, cwd=stage, check=False)
        finally:
            for name, raw in physical.items():
                if source_bytes(root, name) != raw:
                    raise ValueError('Frozen controls modified supplied source: ' + name)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(controls())
