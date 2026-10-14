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
import socket
import stat
import struct
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
        self.capture_failure = None
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

    def capture_lease(self, *args):
        self.events.append(('incoming-lease',))
        if self.capture_failure is not None:
            raise self.capture_failure
        return object()

    def cleanup_incoming(self, app, server_pid, server_birth, gui_pid, birth, lease, facts):
        if lease is None:
            raise RuntimeError('Incoming cleanup lease unavailable')
        self.endpoint(C.unix.IPC_PATH, server_pid)

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
                (C, 'capture_incoming_cleanup_lease', self.capture_lease),
                (C, 'cleanup_incoming_endpoint', self.cleanup_incoming),
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


class IncomingSettlementFixture:
    def __init__(self, app):
        self.app = app
        self.uid = os.getuid()
        self.clock = 0.0
        self.stopped = False
        self.birth_failure = None
        self.connections = []
        self.outcomes = []
        self.first_active = []
        self.after_initial = None
        self.cleanup_error = None
        self.cleanup_calls = []
        self.auth_calls = 0
        self.snapshot_base = {"directory": (1, 10, stat.S_IFDIR | 0o700, self.uid),
            "socket": (1, 11, stat.S_IFSOCK | 0o600, self.uid, 1, 0, 0),
            "marker": (1, 12, stat.S_IFREG | 0o600, self.uid, 1, 3, 0), "markerBytes": b"101"}
        self.facts = {}
        self.endpoint = SimpleNamespace(lstat=lambda: SimpleNamespace(
            st_mode=stat.S_IFSOCK | 0o600, st_uid=self.uid))

    def birth(self, pid):
        return ('darwin-gui-birth-v1', pid, self.uid,
                str(self.app / 'Contents/MacOS/Mixel-Remote'), 100, 1)

    def kernel(self, app, pid, retained=None):
        if self.birth_failure is not None:
            raise self.birth_failure
        return None if self.stopped and retained is not None else self.birth(pid)

    def snapshot(self, deadline=None, allow_missing=False, expected=None, missing=None):
        result = dict(self.snapshot_base)
        for name in ('directory', 'socket', 'marker'):
            if result[name] is None:
                if not allow_missing: raise RuntimeError('Mock leased resource missing')
                if missing is not None: missing[name] = True
            elif missing is not None and missing[name]:
                raise RuntimeError('Mock leased resource reappeared')
            elif expected is not None and result[name] != expected[name]:
                raise RuntimeError('Mock leased identity changed')
        if expected is not None and result['marker'] is not None and result['markerBytes'] != expected['markerBytes']:
            raise RuntimeError('Mock marker bytes changed')
        return result

    def authenticate(self, *args):
        self.auth_calls += 1

    def socket(self, family, kind):
        if (family, kind) != (socket.AF_UNIX, socket.SOCK_STREAM):
            raise AssertionError('Unscoped socket')
        fixture = self
        outcome = self.outcomes.pop(0) if self.outcomes else 'active'
        class Connection:
            def __init__(self):
                self.pid = 999 if outcome == 'foreign' else 101
                self.uid = fixture.uid + 1 if outcome == 'foreign-uid' else fixture.uid
                self.closed = False
                self.timeouts = []
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
            def close(self): self.closed = True
            def settimeout(self, value):
                if not 0 < value <= 0.2: raise AssertionError('Unbounded socket timeout')
                self.timeouts.append(value)
            def connect(self, path):
                if path != str(fixture.endpoint): raise AssertionError('Unexpected socket path')
                fixture.clock += 0.001
                if outcome == 'refused': raise ConnectionRefusedError()
                if outcome == 'missing': raise FileNotFoundError()
                if outcome == 'timeout': raise TimeoutError('private timeout')
                if outcome == 'ambiguous': raise OSError('private metadata uncertainty')
            def getsockopt(self, level, option, size):
                if (level, option, size) != (0, 0x002, 4): raise AssertionError('Unexpected peer query')
                return struct.pack('i', self.pid)
            def sendall(self, *args): raise AssertionError('No settlement IPC payload')
            def recv(self, *args): raise AssertionError('No settlement IPC payload')
        connection = Connection(); self.connections.append(connection)
        return connection

    def final_cleanup(self, endpoint, pid):
        self.cleanup_calls.append((endpoint, pid))
        if self.cleanup_error is not None: raise self.cleanup_error

    def sleep(self, value):
        if not 0 < value <= 0.1: raise AssertionError('Unbounded poll')
        self.clock += value

    @contextlib.contextmanager
    def seams(self):
        original_inactive = C.incoming_endpoint_inactive
        def track_inactive(*args, **kwargs):
            try: return original_inactive(*args, **kwargs)
            except RuntimeError as error:
                if C.endpoint_cleanup_fact(error)['reasonCode'] == 'ipc-endpoint-active':
                    self.first_active.append(error)
                    if self.after_initial is not None: self.after_initial()
                raise
        with contextlib.ExitStack() as stack:
            for target, name, value in (
                (C, 'server_same', self.authenticate), (C, 'incoming_cleanup_snapshot', self.snapshot),
                (C, 'incoming_peer_uid', lambda connection: connection.uid),
                (C, 'incoming_endpoint_inactive', track_inactive),
                (C, 'socket', SimpleNamespace(socket=self.socket, AF_UNIX=socket.AF_UNIX, SOCK_STREAM=socket.SOCK_STREAM)),
                (C, 'time', SimpleNamespace(monotonic=lambda: self.clock, sleep=self.sleep)),
                (C.tray, 'gui_birth', self.kernel),
                (C.tray, 'process_path', lambda pid: self.app / 'Contents/MacOS/Mixel-Remote'),
                (C.tray, 'cleanup_endpoint', self.final_cleanup),
                (C.tray, 'os', SimpleNamespace(getuid=lambda: self.uid, path=SimpleNamespace(lexists=lambda path: True))),
                (C.unix, 'IPC_PATH', self.endpoint),
                (C.os, 'kill', forbidden), (C.os, 'unlink', forbidden), (C.ctypes, 'CDLL', forbidden)):
                stack.enter_context(mock.patch.object(target, name, value))
            yield

    def capture(self):
        with self.seams():
            return C.capture_incoming_cleanup_lease(Path('/source-only-helper'), self.app, 101, 100.5,
                self.birth(101), 202, self.birth(202))

    def cleanup(self, lease):
        self.stopped = True
        with self.seams():
            return C.cleanup_incoming_endpoint(self.app, 101, self.birth(101), 202, self.birth(202), lease, self.facts)


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

    def test_endpoint_diagnostic_allowlist_is_bound_to_frozen_refusal_messages(self):
        def messages(path, names):
            parsed = ast.parse(path.read_text(encoding="utf-8"))
            selected = [node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name in names]
            self.assertEqual({node.name for node in selected}, set(names))
            return {node.args[0].value for function in selected for node in ast.walk(function)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "RuntimeError" and len(node.args) == 1
                    and isinstance(node.args[0], ast.Constant) and type(node.args[0].value) is str}
        actual = messages(ROOT / "scripts/test-support-launch-macos-tray.py", ("endpoint_inactive", "cleanup_endpoint"))
        actual |= messages(ROOT / "scripts/test-support-launch-macos.py", ("bounded_read",))
        self.assertEqual(set(C.ENDPOINT_CLEANUP_REASONS), actual)
        self.assertEqual(len(actual), 8)
        for message in actual:
            result = C.endpoint_cleanup_fact(RuntimeError(message))
            self.assertEqual(result["reasonCategory"], "runtime")
            self.assertEqual(result["errorType"], "RuntimeError")
            self.assertNotEqual(result["reasonCode"], "unclassified-cleanup-error")
            self.assertEqual(set(result), {"reasonCategory", "errorType", "reasonCode", "subphase"})
            self.assertNotIn(message, json.dumps(result))

    def test_successful_runtime_then_incoming_refusal_retains_exact_diagnostic_and_failure(self):
        failure = RuntimeError("Tray fixture cleanup socket identity changed")
        self.f.endpoint_failures["incoming"] = failure
        with self.assertRaises(RuntimeError) as observed:
            self.f.run()
        self.assertIs(observed.exception, failure)
        self.assertTrue(self.f.facts["visibleGuiAndAttendedV2"])
        self.assertTrue(self.f.facts["oneRetainedGuiDuringBoundedStability"])
        self.assertTrue(self.f.facts["ownedProcessesStopped"])
        self.assertNotIn("runtimeFailure", self.f.facts)
        self.assertEqual(self.f.endpoints(), ["url", "incoming"])
        self.assertEqual(self.f.facts["endpointCleanupFailures"], [{
            "endpoint": "incoming", "reasonCategory": "runtime", "errorType": "RuntimeError",
            "reasonCode": "socket-identity-changed", "subphase": "socket-identity-check"}])

    def test_endpoint_diagnostic_unknown_private_and_untrusted_values_never_format(self):
        class PrivateValue:
            def __str__(self):
                raise AssertionError("Unknown exception value must never be formatted")
        class UntrustedRuntimeError(RuntimeError):
            def __str__(self):
                raise AssertionError("Unknown exception subclass must never be formatted")
        class UntrustedString(str):
            def __str__(self):
                raise AssertionError("Unknown string subclass must never be formatted")
        private = "mixel-remote://support/?invite=inv_synthetic-private&apikey=synthetic-private-key /private/customer"
        known = "Tray fixture cleanup socket identity changed"
        cases = [RuntimeError(private), RuntimeError("x" * 4096), RuntimeError(PrivateValue()),
                 RuntimeError(), RuntimeError(known, "private"), UntrustedRuntimeError(known),
                 RuntimeError(UntrustedString(known)),
                 ValueError(known), OSError(private)]
        for error in cases:
            with self.subTest(exception_type=type(error).__name__):
                result = C.endpoint_cleanup_fact(error)
                self.assertEqual(result["reasonCode"], "unclassified-cleanup-error")
                self.assertEqual(result["subphase"], "endpoint-cleanup")
                serialized = json.dumps(result)
                self.assertNotIn("inv_", serialized)
                self.assertNotIn("apikey", serialized)
                self.assertNotIn("/private", serialized)
                self.assertNotIn(known, serialized)

    def test_only_stopped_incoming_active_failure_observes_and_keeps_same_failure(self):
        for label, message, should_observe in (
                ("incoming", "A Mixel IPC endpoint is already active; refusing tray fixture", True),
                ("url", "A Mixel IPC endpoint is already active; refusing tray fixture", False),
                ("incoming", "Pre-existing IPC path is not this runner's socket", False)):
            with self.subTest(label=label, should_observe=should_observe):
                directory = self.directory / (label + ("active" if should_observe else "other"))
                directory.mkdir()
                f = RuntimeFixture(directory)
                failure = RuntimeError(message)
                f.endpoint_failures[label] = failure
                observation = {"scope": "mock-only-readonly-observation"}
                with mock.patch.object(C, "observe_incoming_after_stop", return_value=observation) as observer:
                    with self.assertRaises(RuntimeError) as raised:
                        f.run()
                self.assertIs(raised.exception, failure)
                self.assertTrue(f.facts["ownedProcessesStopped"])
                self.assertEqual(observer.call_count, 1 if should_observe else 0)
                self.assertEqual(f.endpoints(), ["url", "incoming"])
                if should_observe:
                    self.assertIs(f.facts["incomingListenerObservations"], observation)
                    self.assertEqual(observer.call_args.args[0], f.app)
                    self.assertEqual(observer.call_args.args[1], 101)
                    self.assertEqual(observer.call_args.args[3], 202)
                else:
                    self.assertNotIn("incomingListenerObservations", f.facts)

    def test_readonly_incoming_diagnostic_failure_never_replaces_active_cleanup_error(self):
        failure = RuntimeError("A Mixel IPC endpoint is already active; refusing tray fixture")
        for index, diagnostic_error in enumerate((RuntimeError("private observer detail"), KeyboardInterrupt("private interrupt"))):
            directory = self.directory / ("observer-failure-" + str(index))
            directory.mkdir()
            f = RuntimeFixture(directory)
            f.endpoint_failures["incoming"] = failure
            with mock.patch.object(C, "observe_incoming_after_stop", side_effect=diagnostic_error):
                with self.assertRaises(RuntimeError) as raised:
                    f.run()
            self.assertIs(raised.exception, failure)
            self.assertEqual(f.facts["incomingListenerObservationFailure"], "observer-unavailable")
            self.assertNotIn("incomingListenerObservations", f.facts)
            self.assertNotIn("private", json.dumps(f.facts))

    def test_incoming_lease_capture_failure_keeps_independent_owned_process_stops_and_url_drain(self):
        failure = RuntimeError('private incoming lease uncertainty')
        self.f.capture_failure = failure
        with self.assertRaises(RuntimeError) as raised:
            self.f.run()
        self.assertIs(raised.exception, failure)
        self.assertEqual(self.f.signals(), [202, 101])
        self.assertTrue(self.f.facts['ownedProcessesStopped'])
        self.assertEqual(self.f.endpoints(), ['url'])
        self.assertEqual(self.f.facts['incomingEndpointSettlement']['failureCode'], 'lease-unavailable')
        self.assertNotIn('private', json.dumps(self.f.facts))

    def test_incoming_lease_failure_does_not_skip_second_owned_stop_or_replace_primary(self):
        failure = RuntimeError('private lease failure')
        self.f.capture_failure = failure
        self.f.signal_failures[202] = RuntimeError('private GUI stop failure')
        with self.assertRaises(RuntimeError) as raised:
            self.f.run()
        self.assertIs(raised.exception, failure)
        self.assertEqual(self.f.signals(), [202, 101])
        self.assertFalse(self.f.facts['ownedProcessesStopped'])
        self.assertEqual(self.f.endpoints(), [])
        directory = self.directory / 'primary-kept'; directory.mkdir()
        f = RuntimeFixture(directory); primary = RuntimeError('private original runtime failure')
        f.primary = primary; f.capture_failure = failure
        with self.assertRaises(RuntimeError) as raised:
            f.run()
        self.assertIs(raised.exception, primary)
        self.assertEqual(f.signals(), [101])

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
        mac_budget = "  macos-runtime-harness:\n    runs-on: macos-14\n    timeout-minutes: 60\n"
        previous_mac_budget = mac_budget.replace('    timeout-minutes: 60\n', '    timeout-minutes: 30\n', 1)
        self.assertEqual(text.count(mac_budget), 1)
        text = text.replace(mac_budget, previous_mac_budget, 1)
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

    def test_live_incoming_lease_is_captured_before_stop_with_exact_peer_uid_and_kernel_birth(self):
        f = IncomingSettlementFixture(self.f.app); lease = f.capture()
        self.assertTrue(lease['capturedBeforeStop'])
        self.assertEqual(lease['serverBirth'], f.birth(101)); self.assertEqual(lease['guiBirth'], f.birth(202))
        self.assertEqual(lease['snapshot'], f.snapshot_base)
        self.assertGreaterEqual(f.auth_calls, 2)
        self.assertTrue(all(connection.closed for connection in f.connections))
        self.assertEqual(f.cleanup_calls, [])

    def test_live_incoming_lease_rejects_unknown_or_foreign_peer_and_unavailable_birth(self):
        for outcome in ('foreign', 'foreign-uid', 'refused', 'timeout', 'ambiguous'):
            f = IncomingSettlementFixture(self.f.app); f.outcomes = [outcome]
            with self.subTest(outcome=outcome), self.assertRaises((RuntimeError, OSError)):
                f.capture()
            self.assertEqual(f.cleanup_calls, [])
            self.assertTrue(all(connection.closed for connection in f.connections))
        f = IncomingSettlementFixture(self.f.app); f.birth_failure = RuntimeError('private reused birth')
        with self.assertRaises(RuntimeError): f.capture()
        self.assertEqual(f.connections, [])

    def test_incoming_settlement_transient_active_then_refused_runs_original_cleanup_once(self):
        f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.connections.clear()
        f.outcomes = ['active', 'refused']; f.cleanup(lease)
        self.assertEqual(len(f.cleanup_calls), 1)
        self.assertEqual(f.facts['incomingEndpointSettlement']['state'], 'settled')
        self.assertGreater(f.facts['incomingEndpointSettlement']['attempts'], 0)
        self.assertLessEqual(f.facts['incomingEndpointSettlement']['elapsedMs'], 5000)
        self.assertTrue(all(connection.closed for connection in f.connections))
        self.assertNotIn('snapshot', json.dumps(f.facts))

    def test_incoming_settlement_initially_inactive_still_checks_terminal_birth_and_full_lease(self):
        f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.outcomes = ['refused']
        f.cleanup(lease)
        self.assertEqual(f.facts['incomingEndpointSettlement']['state'], 'not-required')
        self.assertEqual(len(f.cleanup_calls), 1)
        for resource in ('directory', 'socket', 'marker', 'markerBytes'):
            f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.outcomes = ['refused']
            value = f.snapshot_base[resource]
            f.snapshot_base[resource] = b'private replacement' if resource == 'markerBytes' else (value[0], value[1] + 1, *value[2:])
            with self.subTest(resource=resource), self.assertRaises(RuntimeError): f.cleanup(lease)
            self.assertEqual(f.cleanup_calls, [])

    def test_unknown_or_foreign_initial_active_probe_is_sticky_before_later_refusal(self):
        for outcome in ('foreign', 'foreign-uid'):
            f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.connections.clear()
            f.outcomes = [outcome, 'refused']
            with self.subTest(outcome=outcome), self.assertRaises(RuntimeError): f.cleanup(lease)
            self.assertEqual(f.cleanup_calls, [])
            self.assertEqual(len(f.connections), 1)
            self.assertEqual(f.facts['incomingEndpointSettlement']['state'], 'failed')

    def test_known_active_then_unknown_foreign_timeout_or_ambiguous_probe_never_recovers(self):
        for outcome in ('foreign', 'foreign-uid', 'timeout', 'ambiguous'):
            f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.connections.clear()
            f.outcomes = ['active', outcome, 'refused']
            with self.subTest(outcome=outcome), self.assertRaises(RuntimeError) as raised: f.cleanup(lease)
            self.assertIs(raised.exception, f.first_active[0])
            self.assertEqual(f.cleanup_calls, [])
            self.assertEqual(f.facts['incomingEndpointSettlement']['state'], 'failed')
            self.assertTrue(all(connection.closed for connection in f.connections))
            self.assertNotIn('private', json.dumps(f.facts))

    def test_persistent_incoming_activity_has_five_second_cap_and_preserves_first_error(self):
        f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.connections.clear()
        with self.assertRaises(RuntimeError) as raised: f.cleanup(lease)
        self.assertIs(raised.exception, f.first_active[0])
        self.assertEqual(f.cleanup_calls, [])
        summary = f.facts['incomingEndpointSettlement']
        self.assertEqual((summary['state'], summary['failureCode']), ('failed', 'active-timeout'))
        self.assertLessEqual(summary['attempts'], 50); self.assertLessEqual(f.clock, 5.01)
        self.assertTrue(all(connection.closed for connection in f.connections))

    def test_post_active_changed_namespace_marker_bytes_or_reused_birth_preserves_original_error(self):
        for change in ('directory', 'socket', 'marker', 'markerBytes', 'birth'):
            f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.outcomes = ['active', 'refused']
            def mutate():
                if change == 'birth': f.birth_failure = RuntimeError('private reused PID')
                elif change == 'markerBytes': f.snapshot_base[change] = b'private replacement'
                else:
                    value = f.snapshot_base[change]; f.snapshot_base[change] = (value[0], value[1] + 1, *value[2:])
            f.after_initial = mutate
            with self.subTest(change=change), self.assertRaises(RuntimeError) as raised: f.cleanup(lease)
            self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(f.cleanup_calls, [])
            self.assertNotIn('private', json.dumps(f.facts))

    def test_settlement_final_frozen_cleanup_failure_keeps_original_active_error(self):
        f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.outcomes = ['active', 'refused']
        f.cleanup_error = RuntimeError('private final guard refusal')
        with self.assertRaises(RuntimeError) as raised: f.cleanup(lease)
        self.assertIs(raised.exception, f.first_active[0])
        self.assertEqual(len(f.cleanup_calls), 1)
        self.assertEqual(f.facts['incomingEndpointSettlement']['failureCode'], 'cleanup-refused')

    def test_settlement_unleased_or_nonterminal_identity_cannot_call_cleanup(self):
        f = IncomingSettlementFixture(self.f.app)
        with self.assertRaises(RuntimeError): f.cleanup(None)
        self.assertEqual(f.cleanup_calls, [])
        self.assertFalse(f.facts['incomingEndpointSettlement']['leaseCapturedBeforeStop'])
        f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
        with f.seams(), mock.patch.object(C.tray, 'gui_birth', return_value=f.birth(101)):
            with self.assertRaises(RuntimeError):
                C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
        self.assertEqual(f.cleanup_calls, []); self.assertEqual(len(f.connections), 1)

    def test_actual_incoming_snapshot_binds_stable_namespace_full_socket_and_owned_bounded_marker(self):
        uid = os.getuid()
        def attributes(mode, ino, size=0):
            return SimpleNamespace(st_dev=1, st_ino=ino, st_mode=mode, st_uid=uid,
                st_nlink=1, st_size=size, st_mtime_ns=10)
        nodes = {'directory': attributes(stat.S_IFDIR | 0o700, 1),
            'socket': attributes(stat.S_IFSOCK | 0o600, 2),
            'marker': attributes(stat.S_IFREG | 0o600, 3, 3)}
        class Resource:
            def __init__(self, name): self.name = name
            def lstat(self):
                if nodes[self.name] is None: raise FileNotFoundError()
                return nodes[self.name]
            @property
            def parent(self): return Resource('directory')
            def with_suffix(self, suffix):
                if suffix != '.pid': raise AssertionError('Unexpected marker path')
                return Resource('marker')
        def read(path, maximum):
            self.assertEqual((path.name, maximum), ('marker', 64))
            return b'101', nodes['marker']
        with mock.patch.object(C.unix, 'IPC_PATH', Resource('socket')), mock.patch.object(C.desktop, 'bounded_read', side_effect=read), mock.patch.object(C.os, 'kill', forbidden), mock.patch.object(C.os, 'unlink', forbidden):
            original = C.incoming_cleanup_snapshot()
            nodes['directory'].st_size += 3; nodes['directory'].st_mtime_ns += 1
            self.assertEqual(C.incoming_cleanup_snapshot(expected=original), original)
            for name, field, value in (('directory', 'st_ino', 999), ('socket', 'st_uid', uid + 1),
                    ('socket', 'st_mode', stat.S_IFREG | 0o600), ('marker', 'st_nlink', 2),
                    ('marker', 'st_size', 65), ('marker', 'st_uid', uid + 1)):
                node = nodes[name]; previous = getattr(node, field); setattr(node, field, value)
                with self.subTest(name=name, field=field), self.assertRaises(RuntimeError):
                    C.incoming_cleanup_snapshot(expected=original)
                setattr(node, field, previous)
            with mock.patch.object(C.desktop, 'bounded_read', return_value=(b'private replacement', nodes['marker'])):
                with self.assertRaises(RuntimeError): C.incoming_cleanup_snapshot(expected=original)
            with mock.patch.object(C.desktop, 'bounded_read', return_value=(b'101', attributes(stat.S_IFREG | 0o600, 77, 3))):
                with self.assertRaises(RuntimeError): C.incoming_cleanup_snapshot(expected=original)
            flags = {'directory': False, 'socket': False, 'marker': False}
            saved = nodes['socket']; nodes['socket'] = None
            with self.assertRaises(RuntimeError): C.incoming_cleanup_snapshot(expected=original)
            absent = C.incoming_cleanup_snapshot(allow_missing=True, expected=original, missing=flags)
            self.assertIsNone(absent['socket']); self.assertTrue(flags['socket'])
            nodes['socket'] = saved
            with self.assertRaises(RuntimeError):
                C.incoming_cleanup_snapshot(allow_missing=True, expected=original, missing=flags)

    def test_terminal_monotonic_absence_follows_original_helper_without_recreating_resources(self):
        f = IncomingSettlementFixture(self.f.app); lease = f.capture()
        f.snapshot_base.update(socket=None, marker=None, markerBytes=None)
        f.outcomes = ['refused']; f.cleanup(lease)
        self.assertEqual(len(f.cleanup_calls), 1)
        self.assertTrue(lease['missing']['socket']); self.assertTrue(lease['missing']['marker'])
        f.snapshot_base['socket'] = lease['snapshot']['socket']
        with self.assertRaises(RuntimeError): f.cleanup(lease)
        self.assertEqual(len(f.cleanup_calls), 1)

    def test_deadline_error_or_unknown_interruption_after_active_keeps_original_exception(self):
        for error in (C.IncomingSettlementDeadline('private deadline'), KeyboardInterrupt('private interrupt')):
            f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.outcomes = ['active', 'refused']
            f.after_initial = lambda: setattr(f, 'birth_failure', error)
            with self.subTest(kind=type(error).__name__), self.assertRaises(RuntimeError) as raised:
                f.cleanup(lease)
            self.assertIs(raised.exception, f.first_active[0])
            self.assertEqual(f.cleanup_calls, []); self.assertNotIn('private', json.dumps(f.facts))

    def test_incoming_original_probe_projection_keeps_frozen_code_globals_and_timeout_clamp(self):
        f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True; f.connections.clear()
        original = C.tray.endpoint_inactive; globals_socket = original.__globals__['socket']
        with f.seams():
            with self.assertRaisesRegex(RuntimeError, '^A Mixel IPC endpoint is already active; refusing tray fixture$'):
                C.incoming_endpoint_inactive(f.clock + 5, lease)
        self.assertIs(C.tray.endpoint_inactive, original)
        self.assertIs(original.__globals__['socket'], globals_socket)
        self.assertTrue(f.connections[0].closed)
        self.assertTrue(all(value <= 0.2 for value in f.connections[0].timeouts))


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
