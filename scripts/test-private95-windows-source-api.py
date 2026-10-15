#!/usr/bin/env python3
"""Offline probe orchestration controls; mocked owner calls are not native proof."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = load('probe-private95-windows-source-api.py')
W = load('private95_source_probe_workflow_projection.py')


class SourceApiProbe(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix='private95-source-probe-offline-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        repository = root / 'repository'
        (repository / 'scripts').mkdir(parents=True)
        (repository / '.github/workflows').mkdir(parents=True)
        (repository / 'shell-boundary-proof').mkdir()
        runner = root / 'runner'; runner.mkdir()
        for name in P.IMMUTABLE_PINS:
            path = repository / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((HERE.parent / name).read_bytes())
        for name in ('build.yml', 'windows-shell-boundary.yml'):
            (repository / '.github/workflows' / name).write_bytes((HERE.parent / '.github/workflows' / name).read_bytes())
        P.git(repository, 'init', '--quiet')
        P.git(repository, '-c', 'core.autocrlf=false', 'add', 'scripts', '.github/workflows')
        return root, repository, runner

    def test_local_context_refuses_before_git_import_or_writes(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}), \
             patch.object(P.subprocess, 'run', side_effect=AssertionError('Git forbidden')) as run, \
             patch.object(P, 'load_sources', side_effect=AssertionError('Import forbidden')) as imports, \
             patch.object(P.tempfile, 'TemporaryDirectory', side_effect=AssertionError('Prefix forbidden')) as prefix:
            with self.assertRaises(ValueError):
                P.run_probe(HERE.parent)
        run.assert_not_called(); imports.assert_not_called(); prefix.assert_not_called()

    def test_exact_hosted_windows_ref_job_attempt_and_workflow_before_git(self):
        _, repository, runner = self.fixture()
        source = 'a' * 40
        environment = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                       'GITHUB_REPOSITORY': P.REPOSITORY, 'GITHUB_JOB': 'active-bash-boundary',
                       'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_RUN_ATTEMPT': '1',
                       'GITHUB_REF': P.REFERENCE, 'GITHUB_SHA': source, 'GITHUB_WORKFLOW_SHA': source,
                       'GITHUB_WORKFLOW_REF': P.REPOSITORY + '/' + P.WORKFLOW + '@' + P.REFERENCE,
                       'GITHUB_WORKSPACE': str(repository), 'RUNNER_TEMP': str(runner)}
        def invoke(values):
            with patch.object(P, 'os', SimpleNamespace(name='nt', environ=values)), \
                 patch.object(P, 'sys', SimpleNamespace(platform='win32')), \
                 patch.object(P, '__file__', str(repository / 'scripts' / Path(P.__file__).name)), \
                 patch.object(P, 'git', side_effect=[source.encode() + b'\n', b'']) as git:
                result = P.context(repository)
                self.assertEqual(git.call_count, 2)
                return result
        self.assertEqual(invoke(environment), runner)
        for key, value in [('GITHUB_REF', 'refs/heads/main'), ('GITHUB_JOB', 'runtime'),
                           ('GITHUB_RUN_ATTEMPT', '2'), ('GITHUB_EVENT_NAME', 'pull_request'),
                           ('RUNNER_ENVIRONMENT', 'self-hosted'), ('GITHUB_WORKFLOW_SHA', 'b' * 40),
                           ('GITHUB_WORKFLOW_REF', 'wrong'), ('GITHUB_REPOSITORY', 'other/repository')]:
            candidate = dict(environment, **{key: value})
            with self.subTest(key=key), patch.object(P, 'os', SimpleNamespace(name='nt', environ=candidate)), \
                 patch.object(P, 'sys', SimpleNamespace(platform='win32')), \
                 patch.object(P, 'git', side_effect=AssertionError('Git forbidden')) as git:
                with self.assertRaises(ValueError): P.context(repository)
                git.assert_not_called()

    def test_exact_workflow_inverse_preserves_original_matrix_controls_upload_and_budget(self):
        text = (HERE.parent / P.WORKFLOW).read_text()
        original = W.inverse(text)
        self.assertEqual(P.sha(original.encode()), W.ORIGINAL_SHA256)
        self.assertIn('os: [windows-2022, windows-2025]', original)
        self.assertIn('timeout-minutes: 5', original)
        self.assertIn("github.event_name == 'workflow_dispatch' && github.ref == '" + P.REFERENCE + "'", W.ADDITION)
        for mutation in (text.replace(P.REFERENCE, 'refs/heads/main'),
                         text.replace('python -B scripts/probe-', 'python scripts/probe-'),
                         text.replace(W.ADDITION, W.ADDITION * 2),
                         text.replace(W.ADDITION, '') + W.ADDITION,
                         text.replace('timeout-minutes: 5', 'timeout-minutes: 6')):
            with self.subTest(digest=P.sha(mutation.encode())), self.assertRaises(AssertionError):
                W.inverse(mutation)

    def test_both_pins_checked_before_any_module_import(self):
        root, repository, _ = self.fixture()
        names, _ = P.inventory(repository)
        mirror = root / 'mirror'
        P.materialize(repository, mirror, names)
        for name in P.PINS:
            path = mirror / 'scripts' / name
            original = path.read_bytes()
            path.write_bytes(original + b'\n# drift\n')
            with patch.object(P.importlib.util, 'spec_from_file_location', side_effect=AssertionError('Import forbidden')) as imports:
                with self.assertRaises(ValueError): P.load_sources(mirror)
                imports.assert_not_called()
            path.write_bytes(original)

    def test_actual_git_mirror_has_lf_pins_and_preserves_crlf_canonical_bytes(self):
        root, repository, _ = self.fixture()
        for name in P.PINS:
            path = repository / 'scripts' / name
            path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        names, before = P.inventory(repository)
        mirror = root / 'raw Git source préflight 🚀'
        P.materialize(repository, mirror, names)
        for name, expected in P.PINS.items():
            self.assertEqual(P.sha((mirror / 'scripts' / name).read_bytes()), expected)
            self.assertIn(b'\r\n', (repository / 'scripts' / name).read_bytes())
        self.assertEqual(P.inventory(repository), (names, before))
        with self.assertRaises(ValueError): P.materialize(repository, mirror, names)

    def test_nonwindows_native_controls_refuse_before_fixture_creation(self):
        root, _, _ = self.fixture()
        fixture = root / 'absent'
        with patch.object(P, 'os', SimpleNamespace(name='posix')):
            with self.assertRaises(ValueError): P.native_controls(Mock(), Mock(), fixture)
        self.assertFalse(fixture.exists())

    def test_native_call_wiring_exact_bytes_and_file_descriptor_with_test_only_standins(self):
        root, _, _ = self.fixture()
        preparer = SimpleNamespace(owned_read=Mock(return_value=P.PAYLOAD),
                                  windows_owner_matches_current_sid=Mock(return_value=True))
        capture = SimpleNamespace(windows_directory_owner_matches_current_token=Mock(return_value=True))
        fixture = root / 'owned fixture'
        with patch.object(P, 'os', SimpleNamespace(name='nt')), patch.object(P, 'sys', SimpleNamespace(platform='win32')):
            result = P.native_controls(preparer, capture, fixture)
        path, limit = preparer.owned_read.call_args.args
        self.assertEqual(path.read_bytes(), P.PAYLOAD); self.assertEqual(limit, len(P.PAYLOAD))
        self.assertIs(type(preparer.windows_owner_matches_current_sid.call_args.args[0]), int)
        capture.windows_directory_owner_matches_current_token.assert_called_once_with(fixture.resolve())
        self.assertTrue(result['actual_owned_read_exact_bytes'])
        self.assertNotIn(str(root), json.dumps(result))

    def test_unchanged28_child_exact_argv_and_fatal_failed_skipped_or_incomplete_output(self):
        root, repository, _ = self.fixture()
        for index, (code, stderr, accepted) in enumerate((
                (0, b'Ran 28 tests in 1.234s\n\nOK\n', True),
                (1, b'Ran 28 tests in 1.234s\n\nOK\n', False),
                (0, b'Ran 27 tests in 1.234s\n\nOK\n', False),
                (0, b'Ran 28 tests in 1.234s\n\nOK (skipped=1)\n', False),
                (0, b'incomplete', False))):
            with patch.object(P.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, b'opaque', stderr)) as run:
                result = P.source_controls(repository, root / ('child-' + str(index)))
            self.assertIs(result['unchanged_28_source_controls_pass'], accepted)
            self.assertEqual(run.call_args.args[0], [P.sys.executable, '-B', 'scripts/test-private-standard-foreground95.py'])
            self.assertEqual(run.call_args.kwargs['cwd'], repository)
            self.assertEqual(run.call_args.kwargs['timeout'], 150)
            env = run.call_args.kwargs['env']
            self.assertEqual(env['TMPDIR'], env['TEMP']); self.assertEqual(env['TEMP'], env['TMP'])
            self.assertEqual(env['PYTHONDONTWRITEBYTECODE'], '1')
        with patch.object(P.subprocess, 'run', side_effect=subprocess.TimeoutExpired('source-controls', 150)):
            with self.assertRaises(subprocess.TimeoutExpired): P.source_controls(repository, root / 'timeout')

    def test_failed_source_controls_leave_canonical_inventory_exact_and_classification_false(self):
        _, repository, runner = self.fixture()
        before = P.inventory(repository)
        original = P.git
        with patch.object(P, 'context', return_value=runner), \
             patch.object(P, 'native_controls', return_value={'synthetic_offline_wiring_only': True}), \
             patch.object(P, 'source_controls', return_value={'unchanged_28_source_controls_pass': False}), \
             patch.object(P, 'git', wraps=P.git) as git:
            def command(repo, *args, **kwargs):
                if args == ('status', '--porcelain', '--untracked-files=no'): return b''
                return original(repo, *args, **kwargs)
            git.side_effect = command
            record = P.run_probe(repository)
        self.assertFalse(record['completed'])
        self.assertTrue(record['canonical_raw_sources_and_tracked_status_unchanged'])
        self.assertEqual(P.inventory(repository), before)
        self.assertFalse(record['normal_qualified']); self.assertFalse(record['native_application_started'])
        self.assertNotIn(str(repository), json.dumps(record))

    def test_actual_failure_stage_and_bounded_native_codes_are_retained_without_message_or_sid(self):
        _, repository, runner = self.fixture()
        error = OSError(13, 'SECRET SID S-1-5-999 PRIVATE PATH')
        error.winerror = 5
        preparer = SimpleNamespace(owned_read=Mock(return_value=P.PAYLOAD),
                                  windows_owner_matches_current_sid=Mock(side_effect=error))
        capture = SimpleNamespace(windows_directory_owner_matches_current_token=Mock())
        original = P.git
        def git(repo, *args, **kwargs):
            return b'' if args == ('status', '--porcelain', '--untracked-files=no') else original(repo, *args, **kwargs)
        with patch.object(P, 'context', return_value=runner), patch.object(P, 'git', side_effect=git), \
             patch.object(P, 'load_sources', return_value=(preparer, capture)), \
             patch.object(P, 'os', SimpleNamespace(name='nt')), patch.object(P, 'sys', SimpleNamespace(platform='win32')):
            record = P.run_probe(repository)
        self.assertFalse(record['completed']); self.assertEqual(record['stage'], 'native_file_owner')
        self.assertEqual(record['failure_category'], 'os_error')
        self.assertEqual(record['failure_errno'], 13); self.assertEqual(record['failure_winerror'], 5)
        capture.windows_directory_owner_matches_current_token.assert_not_called()
        encoded = json.dumps(record)
        for secret in ('SECRET', 'S-1-5-999', 'PRIVATE PATH', str(repository)):
            self.assertNotIn(secret, encoded)

    def test_failure_sanitizer_never_formats_error_and_rejects_unbounded_or_untyped_codes(self):
        class UnprintableError(Exception):
            errno = 2 ** 40
            winerror = 'SECRET'
            def __str__(self): raise AssertionError('Error formatting forbidden')
            def __repr__(self): raise AssertionError('Error formatting forbidden')
        record = {'stage': 'import'}
        P.failure(record, UnprintableError())
        self.assertEqual(record, {'stage': 'import', 'failure_category': 'unexpected'})
        P.failure(record, subprocess.TimeoutExpired('SECRET PRIVATE PATH', 150))
        self.assertEqual(record, {'stage': 'import', 'failure_category': 'timeout'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
