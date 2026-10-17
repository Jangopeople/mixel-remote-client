#!/usr/bin/env python3
"""Real Git/source controls for the exact pipeline input copy function.

No compiler, provider query, app launch, native input, or Root source mutation.
"""
import ast
import contextlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().with_name('test-support-flutter-analysis.py')
TREE = ast.parse(SCRIPT.read_text())
FUNCTIONS = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name == 'copy_current_pipeline_inputs']
if len(FUNCTIONS) != 1:
    raise RuntimeError('Expected the exact actual pipeline copy function')


def git(root, *arguments, data=None):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    run = subprocess.run(['git', '-C', str(root), *arguments], input=data,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False)
    if run.returncode:
        raise RuntimeError('Source fixture Git command failed: '+run.stderr.decode('utf-8', errors='replace'))
    return run.stdout


def put(root, name, raw=b'current source\n', mode=0o644):
    path = root/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(mode)
    return path


class SourceControls(unittest.TestCase):
    def setUp(self):
        environment = mock.patch.dict(os.environ, {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull})
        environment.start(); self.addCleanup(environment.stop)
        self.temporary = tempfile.TemporaryDirectory(prefix='mixel-safe-copy-control-')
        self.root = Path(self.temporary.name).resolve()
        self.source, self.output = self.root/'source', self.root/'private-copy'
        self.source.mkdir(); self.output.mkdir()
        self.opaque_entries = []
        git(self.source, 'init', '--quiet')
        (self.source/'scripts').mkdir(); (self.source/'branding').mkdir()
        self.namespace = {'ROOT': self.source, 'Path': Path, 'os': os, 'stat': stat, 'subprocess': subprocess}
        module = ast.Module(body=FUNCTIONS, type_ignores=[])
        exec(compile(module, str(SCRIPT), 'exec'), self.namespace)
        self.copy = self.namespace['copy_current_pipeline_inputs']

    def tearDown(self):
        for path in reversed(self.opaque_entries):
            if path.is_symlink():
                path.unlink()
            else:
                path.rmdir()
        # Windows only: remove our private read-only file attributes before the
        # standard temporary-directory cleanup. No product/source path is used.
        if os.name == 'nt':
            for root in (self.source, self.output):
                for current, directories, files in os.walk(root):
                    directories[:] = [name for name in directories if name.casefold() != 'worktrees']
                    for name in files:
                        path = Path(current)/name
                        if not path.is_symlink():
                            path.chmod(0o666)
        self.temporary.cleanup()

    def opaque_worktrees(self, name):
        # Only create the opaque entry, never a descendant or a real worktree.
        path = self.source/name
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.symlink_to(self.root, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                raise
            path.mkdir()  # empty fallback requires no symlink privilege
        self.opaque_entries.append(path)
        return path

    def track(self, name, raw=b'tracked source\n', mode=0o644):
        path = put(self.source, name, raw, mode)
        git(self.source, 'add', '--', name)
        return path

    @contextlib.contextmanager
    def no_worktree_access(self):
        original_scan, original_lstat, original_read = os.scandir, Path.lstat, Path.read_bytes
        def forbidden(value):
            return not isinstance(value, int) and any(part.casefold() == 'worktrees' for part in Path(value).parts)
        def scan(value):
            self.assertFalse(forbidden(value), 'scandir touched a worktrees path')
            return original_scan(value)
        def lstat(path, *args, **kwargs):
            self.assertFalse(forbidden(path), 'lstat touched a worktrees path')
            return original_lstat(path, *args, **kwargs)
        def read(path):
            self.assertFalse(forbidden(path), 'read touched a worktrees path')
            return original_read(path)
        with mock.patch.object(os, 'scandir', scan), mock.patch.object(Path, 'lstat', lstat), mock.patch.object(Path, 'read_bytes', read):
            yield

    def test_actual_tracked_incoming_nested_ignore_negation_and_local_exclude(self):
        self.track('scripts/tracked.py')
        self.track('branding/tracked.bin', b'\x00\xff\r\n')
        put(self.source, '.gitignore', b'*.tmp\nscripts/generated/*\n!scripts/generated/keep.rs\nscripts/ignored-dir/\n!scripts/ignored-dir/hidden.rs\n')
        put(self.source, '.git/info/exclude', b'scripts/local-excluded.rs\n')
        put(self.source, 'scripts/new.rs')
        put(self.source, 'scripts/generated/drop.rs')
        put(self.source, 'scripts/generated/keep.rs')
        put(self.source, 'scripts/ignored-dir/hidden.rs')
        put(self.source, 'scripts/local-excluded.rs')
        put(self.source, 'branding/ignored.tmp')
        put(self.source, 'scripts/nested/.gitignore', b'private.*\n')
        put(self.source, 'scripts/nested/private.rs')
        put(self.source, 'scripts/nested/public.rs')
        self.copy(self.output)
        actual = {path.relative_to(self.output).as_posix() for path in self.output.rglob('*') if path.is_file()}
        self.assertEqual(actual, {'scripts/tracked.py', 'branding/tracked.bin', 'scripts/new.rs',
            'scripts/generated/keep.rs', 'scripts/nested/.gitignore', 'scripts/nested/public.rs'})
        self.assertEqual((self.output/'branding/tracked.bin').read_bytes(), b'\x00\xff\r\n')

    def test_tracked_ignored_parent_still_copies_and_untracked_parent_prunes(self):
        self.track('scripts/official/current.rs')
        self.track('branding/official.bin')
        put(self.source, 'scripts/official/incoming.rs')
        put(self.source, '.gitignore', b'scripts/official/\nbranding/\n')
        put(self.source, 'branding/incoming.bin')
        self.copy(self.output)
        self.assertTrue((self.output/'scripts/official/current.rs').is_file())
        self.assertTrue((self.output/'branding/official.bin').is_file())
        self.assertFalse((self.output/'scripts/official/incoming.rs').exists())
        self.assertFalse((self.output/'branding/incoming.bin').exists())

    def test_current_bytes_and_actual_readonly_executable_modes_are_preserved(self):
        tracked = self.track('scripts/changed.py', b'index bytes\n')
        tracked.write_bytes(b'current changed bytes\r\n')
        executable = put(self.source, 'scripts/incoming-helper.sh', b'#!/bin/sh\nexit 0\n', 0o755)
        readonly = put(self.source, 'branding/incoming-readonly.bin', b'owned read-only input\n', 0o444)
        self.copy(self.output)
        for source in (tracked, executable, readonly):
            destination = self.output/source.relative_to(self.source)
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), stat.S_IMODE(source.stat().st_mode))

    def test_worktrees_names_are_pruned_before_scandir_lstat_read_or_git_ignore(self):
        self.track('scripts/official.py')
        for name in ('scripts/worktrees', 'scripts/nested/WORKTREES', 'branding/WorkTrees'):
            self.opaque_worktrees(name)
        put(self.source, 'scripts/nested/public.rs')
        original_run, original_output = subprocess.run, subprocess.check_output
        commands, queries = [], []
        def run(argv, **kwargs):
            commands.append(argv)
            if 'check-ignore' in argv:
                values = kwargs['input'].decode().strip('\0').split('\0')
                queries.extend(values)
                self.assertTrue(all(not any(part.casefold() == 'worktrees' for part in Path(value).parts) for value in values))
            return original_run(argv, **kwargs)
        def output(argv, **kwargs):
            commands.append(argv)
            self.assertNotIn('--others', argv)
            self.assertIn('--cached', argv)
            return original_output(argv, **kwargs)
        with self.no_worktree_access(), mock.patch.object(subprocess, 'run', run), mock.patch.object(subprocess, 'check_output', output):
            self.copy(self.output)
        self.assertTrue(commands); self.assertTrue(queries)
        self.assertEqual({path.relative_to(self.output).as_posix() for path in self.output.rglob('*') if path.is_file()},
                         {'scripts/official.py', 'scripts/nested/public.rs'})

    def test_tracked_worktrees_metadata_refuses_before_any_source_access(self):
        blob = git(self.source, 'hash-object', '-w', '--stdin', data=b'Never read from disk.\n').decode().strip()
        git(self.source, 'update-index', '--add', '--cacheinfo', '100644', blob, 'scripts/WorkTrees/forbidden.py')
        self.track('branding/normal.bin')
        with self.no_worktree_access(), self.assertRaisesRegex(RuntimeError, 'Unsafe current fixture input name'):
            self.copy(self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_bounded_git_queries_use_only_safe_names_and_fail_closed(self):
        self.track('scripts/tracked.rs')
        for index in range(260):
            put(self.source, f'scripts/incoming-{index:03}.rs')
        original = subprocess.run
        requests = []
        def run(argv, **kwargs):
            if 'check-ignore' in argv:
                request = kwargs['input']; requests.append(request)
                self.assertLessEqual(len(request), 32768)
                self.assertLessEqual(request.count(b'\0'), 128)
            return original(argv, **kwargs)
        with mock.patch.object(subprocess, 'run', run):
            self.copy(self.output)
        self.assertGreaterEqual(len(requests), 4)
        self.assertEqual(len(list((self.output/'scripts').iterdir())), 261)
        other = self.root/'refused-copy'; other.mkdir()
        def failure(argv, **kwargs):
            if 'check-ignore' in argv:
                return subprocess.CompletedProcess(argv, 128, b'', b'owned source-control refusal')
            return original(argv, **kwargs)
        with mock.patch.object(subprocess, 'run', failure), self.assertRaisesRegex(RuntimeError, 'ignore query failed'):
            self.copy(other)
        self.assertEqual(list(other.iterdir()), [])

    def test_linked_source_parent_is_rejected_before_child_access(self):
        blob = git(self.source, 'hash-object', '-w', '--stdin', data=b'tracked\n').decode().strip()
        git(self.source, 'update-index', '--add', '--cacheinfo', '100644', blob, 'scripts/alias/child.rs')
        forbidden = self.opaque_worktrees('scripts/WORKTREES')
        (self.source/'scripts/alias').symlink_to(forbidden, target_is_directory=True)
        with self.no_worktree_access(), self.assertRaisesRegex(RuntimeError, 'Unowned current fixture incoming input'):
            self.copy(self.output)
        self.assertFalse((self.output/'scripts/alias/child.rs').exists())

    def test_worktrees_root_is_rejected_before_git_or_filesystem_access(self):
        self.namespace['ROOT'] = Path('/unused/WORKTREES/source')
        with mock.patch.object(subprocess, 'check_output', side_effect=AssertionError('Git must not inspect forbidden root')):
            with self.no_worktree_access(), self.assertRaisesRegex(RuntimeError, 'Worktree pipeline roots'):
                self.copy(self.output)

    def test_source_change_during_read_refuses_that_copy(self):
        source = self.track('scripts/changing.rs')
        original = Path.read_bytes
        def changed(path):
            raw = original(path)
            if path == source:
                path.write_bytes(raw+b'changed concurrently\n')
            return raw
        with mock.patch.object(Path, 'read_bytes', changed), self.assertRaisesRegex(RuntimeError, 'changed while reading'):
            self.copy(self.output)
        self.assertFalse((self.output/'scripts/changing.rs').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
