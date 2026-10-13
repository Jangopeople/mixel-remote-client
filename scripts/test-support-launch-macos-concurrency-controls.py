#!/usr/bin/env python3
"""Run the additive Mac fixture's literal Python bodies with native seams forbidden.

Swift compilation is optional and never executes the resulting binary. Runtime
controls use explicit in-memory AppKit/kernel/socket seams and disposable files.
"""
import argparse
import ast
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / 'scripts/test-support-launch-macos-concurrency.py'
BASE_WORKFLOW_HASH = '08abed8675c6206d56722af3a52b4ea3cc09c13d83838ed6b6443febb4c21018'
ADDITIONS = (
    '            python3 ../scripts/test-support-launch-macos-concurrency.py --app "$APP_PATH" \\\n              --proofs "$TRAY_PROOF_ROOT/macos-${{ matrix.job.arch }}-concurrency"\n',
    '          python3 scripts/test-support-launch-macos-concurrency.py --app "$APP_PATH" \\\n            --proofs "$TRAY_PROOF_ROOT/macos-${EXPECTED_ARCH}-concurrency"\n',
)


def forbidden(*args, **kwargs):
    raise AssertionError('A source-only test attempted a native action')


def load(path=HELPER):
    spec = importlib.util.spec_from_file_location('owned_concurrency_controls', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load()


class RuntimeFixture:
    def __init__(self, directory):
        self.app = directory / 'Spaced ünicode Mixel-Remote.app'
        self.app.mkdir()
        (self.app / 'Contents').mkdir()
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'ch.mixel.remote', 'CFBundleExecutable': 'Mixel-Remote'}))
        for relative in C.tray.CRITICAL:
            path = self.app / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode())
        self.clock = 0.0
        self.facts = {'ownedProcessesStopped': False}
        self.events = []
        self.paired = False
        self.inventory_value = None
        self.owner_value = 202
        self.birth_values = {}
        self.birth_failures = {}
        self.endpoint_failures = {}
        self.signal_failures = {}
        self.auth = False
        self.visible = True
        self.health = True
        self.completions = None
        self.owner_failure = None
        self.primary = None
        self.probe_launch_time = 0  # AppKit GUI launchDate is not its kernel lease.

    def birth(self, pid):
        return ('darwin-gui-birth-v1', pid, os.getuid(),
                str(self.app / 'Contents/MacOS/Mixel-Remote'), 100, 1)

    def gui_birth(self, app, pid, retained=None):
        self.events.append(('birth', pid))
        if pid in self.birth_failures:
            raise self.birth_failures[pid]
        return self.birth_values.get(pid, self.birth(pid))

    def gui_owner(self, app, pid):
        self.events.append(('owner', pid))
        if self.owner_failure:
            raise self.owner_failure
        return self.owner_value() if callable(self.owner_value) else self.owner_value

    def request(self, helper, action, *arguments):
        self.events.append(('request', action))
        if action == 'tray':
            return {'pid': 101, 'bundlePath': str(self.app), 'launchTime': 100.5}
        if action in C.SCENARIOS:
            self.paired = True
            if self.primary:
                raise self.primary
            count = 1 if action == 'batch-two-support' else 2
            return self.completions if self.completions is not None else {'completions': [
                {'pid': 101, 'bundlePath': str(self.app), 'launchTime': 100.5} for _ in range(count)]}
        if action == 'inventory':
            if self.inventory_value is not None and self.paired:
                return {'pids': self.inventory_value}
            return {'pids': [101, 202] if self.paired else [101]}
        raise AssertionError('Unrecognized source-only request')

    def kill(self, pid, kind):
        if kind == 0:
            raise ProcessLookupError()
        self.events.append(('signal', pid, kind))
        if pid in self.signal_failures:
            raise self.signal_failures[pid]

    def endpoint(self, endpoint, pid):
        label = 'url' if endpoint == C.tray.URL_SOCKET else 'incoming'
        self.events.append(('endpoint', label, pid))
        if label in self.endpoint_failures:
            raise self.endpoint_failures[label]

    def wait(self, description, operation, pid):
        value = operation()
        if not value:
            raise RuntimeError('Bounded source-only readiness failure')
        return value

    def sleep(self, seconds):
        self.clock += seconds

    @contextlib.contextmanager
    def seams(self):
        with contextlib.ExitStack() as stack:
            patches = (
                (C.tray, 'isolated_ci', lambda: None),
                (C.tray, 'endpoint_inactive', lambda endpoint: self.events.append(('inactive', str(endpoint)))),
                (C.tray, 'process_path', lambda pid: self.app / 'Contents/MacOS/Mixel-Remote'),
                (C.tray, 'launch_identity', lambda *args: 100.5),
                (C.tray, 'gui_birth', self.gui_birth),
                (C.tray, 'gui_owner', self.gui_owner),
                (C.tray, 'no_connections', lambda *args: not self.auth),
                (C.tray, 'health', lambda *args: self.health),
                (C.tray, 'log_snapshot', lambda *args: {}),
                (C.tray, 'new_tray_marker', lambda *args: True),
                (C.tray, 'wait', self.wait),
                (C.tray, 'cleanup_endpoint', self.endpoint),
                (C.tray, 'os', SimpleNamespace(kill=self.kill, getuid=os.getuid)),
                (C.tray, 'time', SimpleNamespace(monotonic=lambda: self.clock, sleep=self.sleep)),
                (C.desktop, 'helper_request', self.request),
                (C.desktop, 'probe', lambda *args: {'visible': self.visible, 'launchTime': self.probe_launch_time}),
                (C, 'time', SimpleNamespace(monotonic=lambda: self.clock, sleep=self.sleep)),
            )
            for module, name, value in patches:
                stack.enter_context(mock.patch.object(module, name, value))
            yield

    def run(self, scenario='ordinary-and-support'):
        with self.seams():
            C.runtime(self.app, Path('/forbidden-native-helper'), scenario, self.facts)

    def signals(self):
        return [event[1] for event in self.events if event[0] == 'signal']

    def endpoints(self):
        return [event[1] for event in self.events if event[0] == 'endpoint']


class ConcurrencyControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='owned-concurrency-controls-')
        self.directory = Path(self.temporary.name).resolve(strict=True)
        self.f = RuntimeFixture(self.directory)

    def tearDown(self):
        self.temporary.cleanup()

    def test_both_fresh_scenarios_require_stability_and_complete_owned_cleanup(self):
        self.f.run()
        self.assertTrue(self.f.facts['oneRetainedGuiDuringBoundedStability'])
        self.assertEqual(self.f.facts['stabilitySamples'], 21)
        self.assertEqual(self.f.signals(), [202, 101])
        self.assertEqual(self.f.endpoints(), ['url', 'incoming'])
        self.assertTrue(self.f.facts['ownedProcessesStopped'])
        self.assertEqual(self.f.facts['completionCount'], 2)
        # Use a new app resource to avoid reusing a retained scenario.
        nested = self.directory / 'two-support'
        nested.mkdir()
        other = RuntimeFixture(nested)
        other.run('two-support')
        self.assertTrue(other.facts['visibleGuiAndAttendedV2'])

    def test_single_batch_two_urls_retains_one_original_completion_and_gui(self):
        self.f.run('batch-two-support')
        self.assertEqual(self.f.facts['completionCount'], 1)
        self.assertTrue(self.f.facts['completionsMatchOriginalTray'])
        self.assertTrue(self.f.facts['oneRetainedGuiDuringBoundedStability'])
        self.assertEqual(self.f.signals(), [202, 101])
        self.assertEqual(self.f.endpoints(), ['url', 'incoming'])
        self.assertIn('NSWorkspace.shared.open([firstURL, secondURL!]', C.SWIFT_BLOCK)

    def test_batch_completion_cardinality_is_strict_and_cannot_hide_foreign_completion(self):
        good = {'pid': 101, 'bundlePath': str(self.f.app), 'launchTime': 100.5}
        C.completion_matches({'completions': [good]}, self.f.app, 101, 100.5, 1)
        for rows in ([], [good, good], [{**good, 'pid': 303}]):
            with self.assertRaises(RuntimeError):
                C.completion_matches({'completions': rows}, self.f.app, 101, 100.5, 1)

    def test_stable_endpoint_does_not_hide_second_live_same_bundle_gui(self):
        self.f.inventory_value = [101, 202, 303]
        with self.assertRaisesRegex(RuntimeError, 'additional exact-bundle'):
            self.f.run()
        self.assertEqual(self.f.facts['unknownSameBundlePids'], [303])
        self.assertEqual(self.f.signals(), [202, 101])
        self.assertNotIn(303, self.f.signals())
        self.assertFalse(self.f.facts.get('oneRetainedGuiDuringBoundedStability', False))

    def test_changed_url_owner_is_recorded_without_adoption_or_signal(self):
        calls = iter([202, 303])
        self.f.owner_value = lambda: next(calls)
        with self.assertRaisesRegex(RuntimeError, 'URL owner changed'):
            self.f.run()
        self.assertEqual(self.f.facts['unknownUrlOwnerPid'], 303)
        self.assertEqual(self.f.signals(), [202, 101])

    def test_unknown_initial_birth_never_authorizes_unknown_gui_cleanup(self):
        failure = RuntimeError('private application path must not escape')
        self.f.birth_failures[202] = failure
        with self.assertRaises(RuntimeError) as result:
            self.f.run()
        self.assertIs(result.exception, failure)
        self.assertEqual(self.f.facts['observedGuiCandidatePid'], 202)
        self.assertNotIn('guiPid', self.f.facts)
        self.assertEqual(self.f.signals(), [101])

    def test_authenticated_connection_refuses_all_signals_and_endpoint_drains(self):
        self.f.auth = True
        with self.assertRaisesRegex(RuntimeError, 'authenticated'):
            self.f.run()
        self.assertEqual(self.f.signals(), [])
        self.assertEqual(self.f.endpoints(), [])
        self.assertFalse(self.f.facts['ownedProcessesStopped'])

    def test_gui_birth_replacement_refuses_signal_but_attempts_retained_tray(self):
        original = self.f.gui_birth
        calls = 0
        def birth(app, pid, retained=None):
            nonlocal calls
            if pid == 202:
                calls += 1
                if calls > 1:
                    return (*self.f.birth(pid)[:-1], 2)
            return original(app, pid, retained)
        with mock.patch.object(self.f, 'gui_birth', birth):
            with self.assertRaisesRegex(RuntimeError, 'kernel birth changed'):
                self.f.run()
        self.assertEqual(self.f.signals(), [101])
        self.assertEqual(self.f.endpoints(), [])
        self.assertFalse(self.f.facts['ownedProcessesStopped'])

    def test_independent_process_failures_retain_first_error_and_no_endpoint_deletion(self):
        first, second = RuntimeError('first private detail'), OSError('second detail')
        self.f.signal_failures = {202: first, 101: second}
        with self.assertRaises(RuntimeError) as result:
            self.f.run()
        self.assertIs(result.exception, first)
        self.assertEqual(self.f.signals(), [202, 101])
        self.assertEqual(self.f.endpoints(), [])
        self.assertEqual([v['process'] for v in self.f.facts['processCleanupFailures']], ['gui', 'tray'])

    def test_endpoint_failures_attempt_both_and_preserve_first_object(self):
        for failed in (('url',), ('incoming',), ('url', 'incoming')):
            with self.subTest(failed=failed):
                folder = self.directory / ('-'.join(failed))
                folder.mkdir()
                f = RuntimeFixture(folder)
                errors = {name: RuntimeError(name + ' sensitive payload') for name in failed}
                f.endpoint_failures = errors
                with self.assertRaises(RuntimeError) as result:
                    f.run()
                self.assertIs(result.exception, errors[failed[0]])
                self.assertEqual(f.endpoints(), ['url', 'incoming'])
                self.assertTrue(f.facts['ownedProcessesStopped'])
                self.assertNotIn('sensitive', json.dumps(f.facts))

    def test_primary_failure_survives_independent_cleanup_failures(self):
        first = RuntimeError('private URI payload')
        self.f.primary = first
        self.f.endpoint_failures['incoming'] = OSError('private cleanup detail')
        with self.assertRaises(RuntimeError) as result:
            self.f.run()
        self.assertIs(result.exception, first)
        self.assertEqual(self.f.endpoints(), ['url', 'incoming'])
        self.assertNotIn('private', json.dumps(self.f.facts))

    def test_readiness_failures_never_claim_visible_stability(self):
        for attribute in ('visible', 'health'):
            with self.subTest(attribute=attribute):
                folder = self.directory / attribute
                folder.mkdir()
                f = RuntimeFixture(folder)
                setattr(f, attribute, False)
                with self.assertRaises(RuntimeError):
                    f.run()
                self.assertFalse(f.facts.get('visibleGuiAndAttendedV2', False))
                self.assertFalse(f.facts.get('oneRetainedGuiDuringBoundedStability', False))
                self.assertEqual(f.endpoints(), ['url', 'incoming'])

    def test_completion_identity_and_shape_refusals(self):
        good = {'pid': 101, 'bundlePath': str(self.f.app), 'launchTime': 100.5}
        cases = [None, {}, {'completions': []}, {'completions': [good]}]
        for changed in ({'pid': 303}, {'pid': True}, {'bundlePath': '/foreign'},
                        {'launchTime': 0}, {'launchTime': float('nan')}, {'extra': 1}):
            cases.append({'completions': [good, {**good, **changed}]})
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    C.completion_matches(value, self.f.app, 101, 100.5)

    def test_process_inventory_shape_missing_and_duplicate_refusals(self):
        for pids in ([], [101], [101, True], [101, -1], [101, 202, 202], list(range(1, 34)), [101, 202, 303]):
            with self.subTest(pids=pids):
                with mock.patch.object(C.desktop, 'helper_request', return_value={'pids': pids}):
                    with self.assertRaises(RuntimeError):
                        C.inventory(Path('/forbidden'), self.f.app, (101, 202), {})

    def test_sanitized_error_facts_never_contain_raw_details(self):
        payload = C.unix.URI + ' Bearer private-token /private/app'
        for error in (RuntimeError(payload), ValueError(payload), OSError(payload), subprocess.SubprocessError(payload)):
            fact = C.error_fact(error)
            self.assertEqual(set(fact), {'reasonCategory', 'errorType'})
            self.assertNotIn(payload, json.dumps(fact))
            self.assertNotIn('Bearer', json.dumps(fact))

    def test_original_swift_is_byte_exact_after_only_additive_block_inverse(self):
        original = C.tray.tray_swift(C.desktop.SWIFT)
        current = C.concurrency_swift()
        self.assertEqual(current.count(C.SWIFT_BLOCK), 1)
        self.assertEqual(C.inverse_thread_safe_swift(current.replace(C.SWIFT_BLOCK, '', 1)), original)
        schedule = C.SWIFT_BLOCK[C.SWIFT_BLOCK.index('if action == "ordinary-and-support" {'):C.SWIFT_BLOCK.index('let deadline =')]
        self.assertNotIn('RunLoop', schedule)
        self.assertNotIn('sleep', schedule)
        self.assertEqual(schedule.count('NSWorkspace.shared.open'), 5)
        self.assertIn('bundleURL?.resolvingSymlinksInPath() == application', C.SWIFT_BLOCK)
        self.assertNotIn('localizedName', C.SWIFT_BLOCK)

    def test_platform_gate_is_original_and_before_native_or_proof_creation(self):
        with mock.patch.object(C.tray.sys, 'platform', 'not-darwin'):
            with self.assertRaisesRegex(RuntimeError, 'isolated GitHub'):
                C.tray.isolated_ci()
        main = next(v for v in ast.parse(HELPER.read_text()).body if isinstance(v, ast.FunctionDef) and v.name == 'main')
        text = ast.get_source_segment(HELPER.read_text(), main)
        self.assertLess(text.index('tray.isolated_ci()'), text.index('output.mkdir'))
        self.assertLess(text.index('tray.isolated_ci()'), text.index('compile_helper'))

    def test_historical_manifest_all_six_helpers_exact(self):
        table = json.loads(C.safe_source(ROOT / 'scripts/failed-macos-diagnostic-helper-hashes.json'))
        self.assertEqual(len(table), 6)
        for relative, expected in table.items():
            self.assertEqual(hashlib.sha256(C.safe_source(ROOT / relative)).hexdigest(), expected)

    def test_canonical_source_accepts_homogeneous_and_refuses_mixed_bom_or_bare_cr(self):
        self.assertEqual(C.canonical(b'a\nb\n'), C.canonical(b'a\r\nb\r\n'))
        for raw in (b'a\r\nb\n', b'a\rb\r', b'\xef\xbb\xbfa\n'):
            with self.assertRaises(RuntimeError):
                C.canonical(raw)

    def test_source_links_refuse_without_native_import(self):
        ordinary = self.directory / 'source.py'
        ordinary.write_text('source\n')
        linked = self.directory / 'hard.py'
        os.link(ordinary, linked)
        with self.assertRaises(RuntimeError):
            C.safe_source(ordinary)
        symlink = self.directory / 'sym.py'
        symlink.symlink_to(ordinary)
        with self.assertRaises(RuntimeError):
            C.safe_source(symlink)

    def test_full_historical_source_import_accepts_lf_crlf_and_refuses_drift(self):
        for ending in (b'\n', b'\r\n'):
            folder = self.directory / ('LF' if ending == b'\n' else 'CRLF')
            folder.mkdir()
            for path in (ROOT / 'scripts').glob('*'):
                if path.name in {'test-support-launch-macos-concurrency.py', 'failed-macos-diagnostic-helper-hashes.json'} or path.name in [Path(k).name for k in json.loads(C.safe_source(ROOT / 'scripts/failed-macos-diagnostic-helper-hashes.json'))]:
                    data = C.canonical(path.read_bytes()).replace(b'\n', ending)
                    (folder / path.name).write_bytes(data)
            module = load(folder / HELPER.name)
            self.assertEqual(module.concurrency_swift(), C.concurrency_swift())
            changed = folder / 'test-support-launch-macos-tray.py'
            changed.write_bytes(changed.read_bytes() + b'# foreign\n')
            with self.assertRaises(RuntimeError):
                load(folder / HELPER.name)

    def test_proof_root_symlink_and_existing_output_refuse_before_compile_or_runtime(self):
        real = self.directory / 'real-proof-parent'
        real.mkdir()
        linked = self.directory / 'linked-proof-parent'
        linked.symlink_to(real, target_is_directory=True)
        old = real / 'already-owned'
        old.mkdir()
        sentinel = old / 'preserved'
        sentinel.write_bytes(b'exact original receipt')
        for target in (linked / 'new', old):
            with self.subTest(target=target):
                with mock.patch.object(C.tray, 'isolated_ci', lambda: None), mock.patch.object(C, 'compile_helper', forbidden), mock.patch.object(C, 'runtime', forbidden), mock.patch.object(C.sys, 'argv', ['fixture', '--app', str(self.f.app), '--proofs', str(target)]):
                    self.assertEqual(C.main(), 1)
                self.assertEqual(sentinel.read_bytes(), b'exact original receipt')
                self.assertFalse((old / 'concurrency-runtime-safe-facts.json').exists())
        self.assertFalse((real / 'new').exists())

    def test_actual_relative_producer_cli_import_preserves_canonical_source_guard(self):
        carrier = self.directory / 'producer'
        carrier.mkdir()
        scripts = carrier / 'scripts'
        scripts.mkdir()
        table = json.loads(C.safe_source(ROOT / 'scripts/failed-macos-diagnostic-helper-hashes.json'))
        for name in ['failed-macos-diagnostic-helper-hashes.json', HELPER.name, *[Path(v).name for v in table]]:
            shutil.copyfile(ROOT / 'scripts' / name, scripts / name)
        working = carrier / 'rustdesk'
        working.mkdir()
        result = subprocess.run([sys.executable, '-B', '-W', 'error', '../scripts/' + HELPER.name, '--help'], cwd=working, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b'')
        self.assertIn(b'--proofs', result.stdout)
        # Canonicalizing the script root never relaxes individual source link refusal.
        self.assertIn('if path != path.resolve(strict=True):', HELPER.read_text())

    def test_workflow_whole_inverse_has_only_two_ordered_additions(self):
        value = C.canonical((ROOT / '.github/workflows/build.yml').read_bytes()).decode()
        for prefix in ('../scripts/', 'scripts/'):
            for original, flag in (('test-support-launch-macos.py', '--original-desktop'), ('test-support-launch-macos-tray.py', '--original-tray')):
                before = 'python3 ' + prefix + original + ' --app'
                after = 'python3 ' + prefix + HELPER.name + ' ' + flag + ' --app'
                self.assertEqual(value.count(after), 1)
                value = value.replace(after, before, 1)
        for addition in ADDITIONS:
            self.assertEqual(value.count(addition), 1)
            before = value[:value.index(addition)]
            self.assertTrue(before.endswith('-tray"\n'))
            value = value.replace(addition, '', 1)
        self.assertEqual(hashlib.sha256(value.encode()).hexdigest(), BASE_WORKFLOW_HASH)
        self.assertIn('path: runtime-diagnostics/', value)

    def test_preflight_wrapper_and_mandatory_source_command_exact_whole_inverse(self):
        text = C.canonical((ROOT / '.github/workflows/preflight.yml').read_bytes()).decode()
        budget = "  windows-packaging:\n    runs-on: ${{ github.event_name == 'workflow_dispatch' && inputs.windows_source_runner == 'windows-2025' && 'windows-2025' || 'windows-2022' }}\n    timeout-minutes: 120\n"
        previous_budget = budget.replace('    timeout-minutes: 120\n', '    timeout-minutes: 60\n', 1)
        self.assertEqual(text.count(budget), 1)
        text = text.replace(budget, previous_budget, 1)
        old = '          python3 scripts/test-support-launch-macos.py --self-test\n'
        current = '          python3 scripts/test-support-launch-macos-concurrency.py --original-desktop --self-test\n'
        control = '          python3 scripts/test-support-launch-macos-concurrency-controls.py --evidence-dir "$RUNNER_TEMP/mixel-macos-concurrency-source"\n'
        self.assertEqual(text.count(current), 1)
        self.assertEqual(text.count(control), 1)
        text = text.replace(current, old, 1).replace(control, '', 1)
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), 'a2b3ddcf8141d91a6e3f7af26e965642848f9752c49e8db13189e890277555fe')

    def test_release_wrapper_inverse_refuses_partial_duplicate_and_missing_original_calls(self):
        spec = importlib.util.spec_from_file_location('owned_release_projection', ROOT / 'scripts/test-release-pipeline.py')
        release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(release)
        text = (ROOT / '.github/workflows/build.yml').read_text()
        projected = release.original_mac_wrapper_calls(text)
        self.assertNotIn('--original-desktop', projected)
        self.assertNotIn('--original-tray', projected)
        current = 'python3 ../scripts/test-support-launch-macos-concurrency.py --original-desktop --app'
        with self.assertRaises(AssertionError):
            release.original_mac_wrapper_calls(text.replace(current, current + current, 1))
        with self.assertRaises(AssertionError):
            release.original_mac_wrapper_calls(text.replace(current, 'python3 ../scripts/test-support-launch-macos.py --app', 1))
        with self.assertRaises(AssertionError):
            release.original_mac_wrapper_calls(projected.replace('python3 ../scripts/test-support-launch-macos.py --app', '', 1))

    def test_single_callback_projection_whole_inverses_and_drift_refusal(self):
        for original in (C.ORIGINAL_DESKTOP_SWIFT, C.ORIGINAL_TRAY_SWIFT):
            safe = C.thread_safe_swift(original)
            self.assertEqual(C.inverse_thread_safe_swift(safe), original)
            self.assertEqual(safe.count(C.SWIFT_STORAGE), 1)
            self.assertNotIn('var finished', safe)
            self.assertIn('while resultStore.snapshot()[0] == nil', safe)
            with self.assertRaises(RuntimeError):
                C.thread_safe_swift(original + '// foreign')
            with self.assertRaises(RuntimeError):
                C.inverse_thread_safe_swift(safe + '// foreign')

    def test_every_shared_callback_store_access_is_locked_and_snapshots_are_atomic(self):
        self.assertEqual(C.SWIFT_STORAGE.count('lock.lock(); defer { lock.unlock() }'), 2)
        self.assertIn('private var slots:', C.SWIFT_STORAGE)
        self.assertIn('slots[index] == nil', C.SWIFT_STORAGE)
        self.assertIn('let results = MixelCompletionStore(count: expectedResults)', C.SWIFT_BLOCK)
        self.assertNotIn('results[index]', C.SWIFT_BLOCK)
        self.assertNotIn('var results', C.SWIFT_BLOCK)
        self.assertIn('results.snapshot().contains', C.SWIFT_BLOCK)
        self.assertIn('results.snapshot().compactMap', C.SWIFT_BLOCK)

    def test_original_wrappers_preserve_main_argv_compiler_result_and_restore_globals(self):
        for mode, source in (('--original-desktop', C.ORIGINAL_DESKTOP_SWIFT), ('--original-tray', C.ORIGINAL_TRAY_SWIFT)):
            records = []
            def main():
                if mode == '--original-tray':
                    C.desktop.SWIFT = C.tray.tray_swift(C.desktop.SWIFT)
                records.append(tuple(C.sys.argv[1:]))
                C.desktop.compile_helper(self.directory)
                return 73
            original_compiler, original_swift, original_argv = C.desktop.compile_helper, C.desktop.SWIFT, C.sys.argv
            module = C.desktop if mode == '--original-desktop' else C.tray
            with mock.patch.object(C.tray, 'isolated_ci', lambda: None), mock.patch.object(module, 'main', main), mock.patch.object(C, 'compile_source', side_effect=lambda directory, text: records.append(text) or Path('/never-executed')):
                self.assertEqual(C.run_original(mode, ['--app', str(self.f.app), '--unchanged-control']), 73)
            self.assertEqual(records[0], ('--app', str(self.f.app), '--unchanged-control'))
            self.assertEqual(records[1], C.thread_safe_swift(source))
            self.assertIs(C.desktop.compile_helper, original_compiler)
            self.assertEqual(C.desktop.SWIFT, original_swift)
            self.assertIs(C.sys.argv, original_argv)

    def test_original_wrapper_failures_keep_object_and_never_compile_foreign_swift(self):
        failure = RuntimeError('private detail')
        old = C.desktop.SWIFT
        def failing():
            raise failure
        with mock.patch.object(C.tray, 'isolated_ci', lambda: None), mock.patch.object(C.desktop, 'main', failing):
            with self.assertRaises(RuntimeError) as result:
                C.run_original('--original-desktop', ['--self-test'])
            self.assertIs(result.exception, failure)
        def drifted():
            C.desktop.SWIFT += '// foreign'
            C.desktop.compile_helper(self.directory)
        with mock.patch.object(C.tray, 'isolated_ci', lambda: None), mock.patch.object(C.desktop, 'main', drifted), mock.patch.object(C, 'compile_source', forbidden):
            with self.assertRaisesRegex(RuntimeError, 'source changed'):
                C.run_original('--original-desktop', ['--self-test'])
        self.assertEqual(C.desktop.SWIFT, old)
        with self.assertRaises(RuntimeError):
            C.run_original('--unknown-mode', [])

    def test_original_wrapper_platform_fence_precedes_main_and_compile(self):
        with mock.patch.object(C.tray.sys, 'platform', 'not-darwin'), mock.patch.object(C.desktop, 'main', forbidden):
            with self.assertRaisesRegex(RuntimeError, 'isolated GitHub'):
                C.run_original('--original-desktop', ['--self-test'])

    def test_unsynchronized_copy_publish_model_is_detected_by_complete_snapshot_contract(self):
        # Controlled read/modify/publish interleaving, no execution of undefined Swift data races.
        initial = [None, None]
        writer_zero, writer_one = list(initial), list(initial)
        writer_zero[0] = {'pid': 101}
        writer_one[1] = {'pid': 101}
        shared = writer_zero
        shared = writer_one
        with self.assertRaises(AssertionError):
            self.assertEqual(sum(value is not None for value in shared), 2)

    def test_compiler_uses_only_owned_module_cache_and_never_runs_binary(self):
        recorded = []
        with mock.patch.object(C.subprocess, 'run', side_effect=lambda argv, **kw: recorded.append((argv, kw)) or SimpleNamespace(returncode=0, stdout=b"", stderr=b"")):
            C.compile_helper(self.directory)
        self.assertEqual(len(recorded), 1)
        argv, kw = recorded[0]
        self.assertEqual(argv[:3], ['xcrun', 'swiftc', '-module-cache-path'])
        self.assertTrue(Path(argv[3]).is_relative_to(self.directory))
        self.assertEqual(kw['timeout'], 60)
        self.assertIn('-warnings-as-errors', argv)
        self.assertEqual((self.directory / 'compile.stderr').read_bytes(), b'')

FOUNDATION_STORAGE_DRIVER = r"""
import Foundation
import Dispatch
STORAGE
let store = MixelCompletionStore(count: 2)
let group = DispatchGroup()
for worker in 0..<32 {
    group.enter()
    DispatchQueue.global().async {
        for index in 0..<2000 {
            let slot = (index + worker) % 2
            store.record(slot, ["pid": 101, "bundlePath": "fixed", "launchTime": 100.5])
            let snapshot = store.snapshot()
            precondition(snapshot.count == 2)
            for value in snapshot.compactMap({ $0 }) {
                precondition(value["pid"] as? Int == 101)
                precondition(value["bundlePath"] as? String == "fixed")
                precondition(value["launchTime"] as? Double == 100.5)
            }
        }
        group.leave()
    }
}
var reads = 0
while group.wait(timeout: .now()) == .timedOut { _ = store.snapshot(); reads += 1 }
let result = store.snapshot()
precondition(result.count == 2 && result.allSatisfy { $0 != nil })
precondition(!store.record(0, ["pid": 999]))
precondition(store.snapshot()[0]?["pid"] as? Int == 101)
precondition(!store.record(2, ["pid": 999]))
let single = MixelCompletionStore(count: 1)
precondition(single.snapshot()[0] == nil)
precondition(single.record(0, ["error": "fixed callback failure"]))
precondition(!single.record(0, ["pid": 999]))
precondition(single.snapshot()[0]?["error"] as? String == "fixed callback failure")
print("PASS: Foundation concurrent callback storage; 64000 writes, immutable first results, atomic snapshots, timeout and error states")
"""


def swift_storage_controls(output):
    directory = output / 'foundation-storage'
    directory.mkdir()
    cache = directory / 'module-cache'
    cache.mkdir()
    source = directory / 'storage.swift'
    source.write_text(FOUNDATION_STORAGE_DRIVER.replace('STORAGE', C.SWIFT_STORAGE), encoding='utf-8', newline='\n')
    binary = directory / 'storage'
    compile_result = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', '-module-cache-path', str(cache), str(source), '-o', str(binary)], capture_output=True, timeout=60)
    (directory / 'compile.stdout').write_bytes(compile_result.stdout)
    (directory / 'compile.stderr').write_bytes(compile_result.stderr)
    if compile_result.returncode:
        raise RuntimeError('Foundation storage compile failed')
    result = subprocess.run([str(binary)], capture_output=True, timeout=30)
    (directory / 'run.stdout').write_bytes(result.stdout)
    (directory / 'run.stderr').write_bytes(result.stderr)
    if result.returncode or b'PASS: Foundation' not in result.stdout:
        raise RuntimeError('Foundation storage controls failed')
    record = {'FoundationOnly': True, 'NativeAppKitInvoked': False, 'concurrentWrites': 64000, 'passed': True, 'tsan': {}}
    # Both sanitizer runs are private Foundation-only child processes.
    for name, storage in [('locked', C.SWIFT_STORAGE), ('unlocked-mutant', C.SWIFT_STORAGE.replace('lock.lock(); defer { lock.unlock() }', ''))]:
        swift = directory / (name + '.swift')
        swift.write_text(FOUNDATION_STORAGE_DRIVER.replace('STORAGE', storage), encoding='utf-8', newline='\n')
        executable = directory / name
        built = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', '-sanitize=thread', '-module-cache-path', str(cache), str(swift), '-o', str(executable)], capture_output=True, timeout=60)
        (directory / (name + '-compile.stdout')).write_bytes(built.stdout)
        (directory / (name + '-compile.stderr')).write_bytes(built.stderr)
        if built.returncode:
            raise RuntimeError('Foundation thread sanitizer compile failed')
        env = {**os.environ, 'TSAN_OPTIONS': 'halt_on_error=1:exitcode=66'}
        import resource
        def no_core():
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        observed = subprocess.run([str(executable)], capture_output=True, env=env, preexec_fn=no_core, timeout=30)
        (directory / (name + '-run.stdout')).write_bytes(observed.stdout)
        (directory / (name + '-run.stderr')).write_bytes(observed.stderr)
        if name == 'locked':
            if observed.returncode or observed.stderr:
                raise RuntimeError('Locked Foundation thread sanitizer control failed')
        elif observed.returncode == 0 or not any(marker in observed.stderr for marker in (b'ThreadSanitizer: data race', b'ThreadSanitizer: Swift access race')):
            raise RuntimeError('Unlocked storage mutation was not discriminated')
        record['tsan'][name] = {'exitCode': observed.returncode, 'raceReported': any(marker in observed.stderr for marker in (b'ThreadSanitizer: data race', b'ThreadSanitizer: Swift access race'))}
    return record



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--compile-swift', action='store_true')
    parser.add_argument('--swift-storage-controls', action='store_true')
    args = parser.parse_args()
    output = args.evidence_dir.absolute()
    if output.parent != output.parent.resolve(strict=True):
        raise RuntimeError('Control output parent must be canonical')
    output.mkdir(mode=0o700, exist_ok=False)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ConcurrencyControls)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    record = {'sourceHelperSha256': hashlib.sha256(HELPER.read_bytes()).hexdigest(),
              'testsRun': result.testsRun, 'passed': result.wasSuccessful(),
              'nativeAppLaunched': False, 'nativeGuiTested': False, 'swiftCompiled': False}
    if result.wasSuccessful() and args.compile_swift:
        directory = output / 'compiled-swift'
        directory.mkdir()
        binary = C.compile_helper(directory)
        record['swiftCompiled'] = True
        record['swiftBinarySha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    if result.wasSuccessful() and args.swift_storage_controls:
        record['swiftStorage'] = swift_storage_controls(output)
    (output / 'source-control-results.json').write_text(json.dumps(record, indent=2) + '\n')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
