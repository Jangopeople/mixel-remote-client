#!/usr/bin/env python3
"""Run the additive Mac fixture's literal Python bodies with native seams forbidden.

Swift compilation is optional and never executes the resulting binary. Runtime
controls use explicit in-memory AppKit/kernel/socket seams and disposable files.
"""
import argparse

import ast
import contextlib
import errno
import hashlib
import importlib.util
import json
import os
import re
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

# Load the exact sibling helper for direct, runpy and file-spec entry points.
_input_lifecycle_spec = importlib.util.spec_from_file_location(
    "mixel_input_lifecycle_projection",
    Path(__file__).with_name("input_lifecycle_integration_projection.py"),
)
input_lifecycle = importlib.util.module_from_spec(_input_lifecycle_spec)
_input_lifecycle_spec.loader.exec_module(input_lifecycle)

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
_lifecycle_spec = importlib.util.spec_from_file_location('mixel_lifecycle_workflow_projection', ROOT / 'scripts/file_dialog_workflow_projection.py')
_lifecycle_projection = importlib.util.module_from_spec(_lifecycle_spec)
_lifecycle_spec.loader.exec_module(_lifecycle_projection)
inverse_lifecycle_workflow_checks = _lifecycle_projection.inverse_lifecycle_workflow_checks
HELPER = ROOT / 'scripts/test-support-launch-macos-concurrency.py'
BASE_WORKFLOW_HASH = '08abed8675c6206d56722af3a52b4ea3cc09c13d83838ed6b6443febb4c21018'
ADDITIONS = (
    '            python3 ../scripts/test-support-launch-macos-concurrency.py --app "$APP_PATH" \\\n              --proofs "$TRAY_PROOF_ROOT/macos-${{ matrix.job.arch }}-concurrency"\n',
    '          python3 scripts/test-support-launch-macos-concurrency.py --app "$APP_PATH" \\\n            --proofs "$TRAY_PROOF_ROOT/macos-${EXPECTED_ARCH}-concurrency"\n',
)


def inverse_closed_pid_recheck_source(source):
    """Remove only this exact additive recheck layer before old hash controls."""
    before = '                def pid_recheck_refused():\n                    phase("peer-pid-recheck")\n                    observed = incoming_settlement_call(deadline, tray.peer_pid, self.connection)\n                    refused = observed != first_pid\n'
    after = '                def pid_recheck_refused():\n                    phase("peer-pid-recheck")\n                    try:\n                        observed = incoming_settlement_call(deadline, tray.peer_pid, self.connection)\n                    except OSError as closed_error:\n                        # First PID and UID already matched this retained owner.\n                        # Darwin may close the peer before LOCAL_PEERPID\'s second\n                        # read. Cached UID is rechecked below; this successful\n                        # connect still means active, never endpoint inactivity.\n                        if type(closed_error) is not OSError or incoming_initial_identity_errno(closed_error) != errno.ENOTCONN:\n                            raise\n                        if type(first_pid) is not int or first_pid <= 0 or first_pid != lease["serverPid"] or type(first_uid) is not int or first_uid != lease["uid"]:\n                            raise RuntimeError("Incoming cleanup disconnected peer initial identity refused")\n                        phase("post-peer-lease-guard")\n                        guard()\n                        if any(lease["missing"].values()):\n                            raise RuntimeError("Incoming cleanup disconnected peer resource missing")\n                        self.closed_peer_qualified = True\n                        return False\n                    refused = observed != first_pid\n'
    uid_before = '                    observed = incoming_settlement_call(deadline, incoming_peer_uid, self.connection)\n                    refused = observed != first_uid\n'
    uid_after = '                    observed = incoming_settlement_call(deadline, incoming_peer_uid, self.connection)\n                    if self.closed_peer_qualified and type(observed) is not int:\n                        attribution["peerUidComparison"] = incoming_initial_identity_comparison(observed, lease["uid"])\n                        phase("peer-uid-recheck", "peer-uid-changed")\n                        raise RuntimeError("Incoming cleanup disconnected peer UID changed")\n                    refused = observed != first_uid\n'
    if source.count(after) != 1 or source.count(uid_after) != 1:
        raise AssertionError('Exact closed peer recheck layer changed')
    return source.replace(after, before, 1).replace(uid_after, uid_before, 1)


def linux_close_workflow_projections(workflow):
    """Exact approved adjacent checks and the unchanged surrounding gates."""
    if workflow not in ('build.yml', 'preflight.yml'):
        raise AssertionError('Unknown Linux close workflow projection')
    source = 'rustdesk' if workflow == 'build.yml' else '"$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"'
    dart = 'python3 scripts/test-support-linux-window-close.py --source ' + source + ' --dart "$(command -v dart)"\n'
    native = 'python3 scripts/test-support-linux-child-window-close.py --source ' + source + ' --output "$RUNNER_TEMP/mixel-linux-child-close"\n'
    projections = [(
        '          python3 scripts/test-support-macos-cleanup.py --dart "$(command -v dart)" --flutter "$(command -v flutter)"\n',
        '          ' + dart + '          ' + native,
        '          python3 scripts/test-support-wakelock.py\n',
        2 if workflow == 'build.yml' else 1,
    )]
    if workflow == 'preflight.yml':
        projections.append((
            '      - name: Prove awaited Mac page and terminal cleanup with real Flutter widgets\n'
            '        run: python3 scripts/test-support-macos-cleanup.py --dart "$(command -v dart)" --flutter "$(command -v flutter)"\n',
            '      - name: Prove Linux terminal-close futures and conserved nonfinal window paths\n'
            '        run: ' + dart +
            '      - name: Prove Linux child close ownership and mouse-hook cleanup\n'
            '        run: ' + native,
            '      - name: Prove bounded main-engine Quit and owned resource cleanup\n'
            '        run: python3 scripts/test-support-macos-termination.py --flutter "$(command -v flutter)" --framework "$MIXEL_FLUTTER_FRAMEWORK"\n',
            1,
        ))
    return projections


def inverse_linux_close_workflow_checks(text, workflow):
    """Remove only the exact count and placement of approved regression checks."""
    for before, addition, after, count in linux_close_workflow_projections(workflow):
        current = before + addition + after
        if text.count(addition) != count or text.count(current) != count:
            raise AssertionError('Exact ordered Linux close workflow checks changed: ' + workflow)
        text = text.replace(current, before + after, count)
    return text


def linux_close_bridge_projection():
    before = ('      - name: Generate bridge\n'
              '        working-directory: rustdesk\n'
              '        run: |\n')
    original = ('          RDREPO=. python3 ../scripts/patch-support-macos-texture.py\n'
                '          RDREPO=. python3 ../scripts/patch-support-macos-window.py\n')
    restore = '          python3 ../scripts/patch-support-linux-child-window-close.py --root . --restore\n'
    reapply = '          python3 ../scripts/patch-support-linux-child-window-close.py --root . --after-branding\n'
    after = "          pushd flutter && sed -i -e 's/extended_text: 14.0.0/extended_text: 13.0.0/g' pubspec.yaml && flutter pub get && popd\n"
    return before, restore, original, reapply, after


def inverse_linux_close_bridge_checks(text):
    """Conserve both original bridge hooks while removing their exact brackets."""
    before, restore, original, reapply, after = linux_close_bridge_projection()
    current = before + restore + original + reapply + after
    if any(text.count(value) != 1 for value in (restore, original, reapply, current)):
        raise AssertionError('Exact ordered Linux child close bridge brackets changed')
    return text.replace(current, before + original + after, 1)


def uri_workflow_projections(workflow):
    """Only the reviewed URI checks beside their unchanged Linux regression gates."""
    if workflow not in ('build.yml', 'preflight.yml'):
        raise AssertionError('Unknown URI workflow projection')
    source = 'rustdesk' if workflow == 'build.yml' else '"$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"'
    uri = 'python3 scripts/test-support-uri-routing.py --source ' + source + ' --dart "$(command -v dart)" --output "$RUNNER_TEMP/mixel-uri-routing"\n'
    linux = 'python3 scripts/test-support-linux-window-close.py --source ' + source + ' --dart "$(command -v dart)"\n'
    projections = [(
        '          python3 scripts/test-support-macos-cleanup.py --dart "$(command -v dart)" --flutter "$(command -v flutter)"\n',
        '          ' + uri,
        '          ' + linux,
        2 if workflow == 'build.yml' else 1,
    )]
    if workflow == 'preflight.yml':
        projections.append((
            '      - name: Prove awaited Mac page and terminal cleanup with real Flutter widgets\n'
            '        run: python3 scripts/test-support-macos-cleanup.py --dart "$(command -v dart)" --flutter "$(command -v flutter)"\n',
            '      - name: Prove terminal support URI routing without ID fallthrough\n'
            '        run: ' + uri,
            '      - name: Prove Linux terminal-close futures and conserved nonfinal window paths\n'
            '        run: ' + linux,
            1,
        ))
    return projections


WINDOWS_SDK_SOURCE_DIAGNOSTICS_UPLOAD = '      - name: Preserve Windows SDK source diagnostics\n        if: always()\n        uses: actions/upload-artifact@v6\n        with:\n          name: windows-sdk-source-diagnostics\n          path: |\n            ${{ runner.temp }}/mixel-sdk-source/*.log\n            ${{ runner.temp }}/mixel-sdk-source/*.timing.json\n          if-no-files-found: ignore\n          retention-days: 14\n'
WINDOWS_SDK_SOURCE_DIAGNOSTICS_CONTEXTS = {'build.yml': ('windows', '      - name: Preflight generated support source and reporter\n        id: preflight\n        continue-on-error: ${{ needs.config.outputs.collect_windows_preflight_failure == \'true\' }}\n        shell: bash\n        env:\n          MIXEL_HOSTED_OWNED_QA_NAMESPACE: "1"\n          VCPKG_ROOT: ${{ github.workspace }}/vcpkg\n          RDREPO: ./rustdesk\n          MIXEL_SDK_FULL_APP_PREFLIGHT: "true"\n        run: bash scripts/test-windows-generated-source.sh\n', '\n      - name: Build (cargo + flutter via build.py)\n'), 'preflight.yml': ('windows-packaging', '      - name: Exercise complete Windows generated source, keep-awake and native branding\n        shell: bash\n        env:\n          MIXEL_HOSTED_OWNED_QA_NAMESPACE: "1"\n          RDREPO: ./rustdesk\n          AOM_INCLUDE_DIR: ${{ github.workspace }}/native-aom-headers\n        run: bash scripts/test-windows-generated-source.sh\n', '      - uses: actions/upload-artifact@v6\n')}

def inverse_windows_sdk_source_diagnostics(text, workflow):
    if workflow not in WINDOWS_SDK_SOURCE_DIAGNOSTICS_CONTEXTS:
        raise AssertionError('Unknown Windows SDK diagnostics workflow')
    job, before, after = WINDOWS_SDK_SOURCE_DIAGNOSTICS_CONTEXTS[workflow]
    marker = '\n  ' + job + ':\n'
    if text.count(marker) != 1 or text.count(WINDOWS_SDK_SOURCE_DIAGNOSTICS_UPLOAD) != 1:
        raise AssertionError('Changed, missing or duplicate Windows SDK diagnostics upload')
    start = text.index(marker)
    tail = text[start + len(marker):]
    match = re.search(r'\n  [A-Za-z][A-Za-z0-9_-]*:\n', tail)
    segment = text[start:] if match is None else text[start:start + len(marker) + match.start()]
    exact = before + WINDOWS_SDK_SOURCE_DIAGNOSTICS_UPLOAD + after
    if segment.count(exact) != 1:
        raise AssertionError('Changed or moved Windows SDK diagnostics upload context')
    return text.replace(exact, before + after, 1)

def inverse_uri_workflow_checks(text, workflow):
    if workflow == 'preflight.yml':
        text = inverse_uri_preflight_budget(text)
    for before, addition, after, count in uri_workflow_projections(workflow):
        current = before + addition + after
        if text.count(addition) != count or text.count(current) != count:
            raise AssertionError('Exact URI workflow check count/order/placement changed: ' + workflow)
        text = text.replace(current, before + after, count)
    return text


def inverse_windows_producer_budget(text):
    before = ("  windows:\n    needs: [config, bridge]\n"
              "    if: ${{ needs.config.outputs.windows == 'true' }}\n    runs-on: windows-2022\n")
    current = '    timeout-minutes: 240\n'
    after = "    env:\n      PYTHONUTF8: '1'\n"
    exact = before + current + after
    if text.count(before) != 1 or text.count(current) != 1 or text.count(exact) != 1:
        raise AssertionError('Exact Windows producer outer 240-minute budget context changed')
    return text.replace(exact, before + '    timeout-minutes: 180\n' + after, 1)


def windows_preflight_budget_projection():
    before = "  windows-packaging:\n    runs-on: ${{ github.event_name == 'workflow_dispatch' && inputs.windows_source_runner == 'windows-2025' && 'windows-2025' || 'windows-2022' }}\n"
    current = '    timeout-minutes: 240\n'
    after = "    env:\n      PYTHONUTF8: '1'\n    steps:\n      - uses: actions/checkout@v5\n"
    return before, current, after


def inverse_windows_preflight_budget(text):
    before, current, after = windows_preflight_budget_projection()
    approved = before + current + after
    if text.count(approved) != 1 or text.count(before) != 1 or text.count(current) != 1:
        raise AssertionError('Exact Windows preflight 240-minute source-job budget context changed')
    return text.replace(approved, before + '    timeout-minutes: 120\n' + after, 1)


def uri_preflight_budget_projection():
    before = '  upstream-source:\n    runs-on: ubuntu-22.04\n'
    current = '    timeout-minutes: 45\n'
    after = '    steps:\n      - uses: actions/checkout@v5\n'
    return before, current, after


def uri_mac_preflight_budget_projection():
    before = '  macos-runtime-harness:\n    runs-on: macos-14\n'
    current = '    timeout-minutes: 90\n'
    after = '    steps:\n      - uses: actions/checkout@v5\n'
    return before, current, after


def inverse_uri_preflight_budget(text):
    for projection, historical in ((uri_preflight_budget_projection(), '20'),
                                   (uri_mac_preflight_budget_projection(), '60')):
        before, current, after = projection
        approved = before + current + after
        if text.count(approved) != 1 or text.count(before) != 1:
            raise AssertionError('Exact upstream/Mac URI source-job budget context changed')
        text = text.replace(approved, before + '    timeout-minutes: ' + historical + '\n' + after, 1)
    return text


def uri_bridge_projection():
    before, native_restore, original, native_after, after = linux_close_bridge_projection()
    restore = '          python3 ../scripts/patch-support-uri-routing.py --root . --restore\n'
    reapply = '          python3 ../scripts/patch-support-uri-routing.py --root . --after-branding\n'
    return before, restore, native_restore + original + native_after, reapply, after


def inverse_uri_bridge_checks(text):
    before, restore, original, reapply, after = uri_bridge_projection()
    current = before + restore + original + reapply + after
    lines = (restore + original + reapply).splitlines(keepends=True)
    if text.count(current) != 1 or any(text.count(line) != 1 for line in lines):
        raise AssertionError('Exact six-command URI/native/original bridge sequence changed')
    return text.replace(current, before + original + after, 1)


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

    def test_lifecycle_workflow_inverse_refuses_drift_missing_duplicate_moved_or_wrong_job(self):
        for workflow in ('build.yml', 'preflight.yml'):
            original = C.canonical((ROOT / '.github/workflows' / workflow).read_bytes()).decode()
            projected = inverse_lifecycle_workflow_checks(original, workflow)
            self.assertNotIn('file-dialog-lifecycle', projected)
            for job, before, addition, after in _lifecycle_projection.lifecycle_workflow_projections(workflow):
                exact = before + addition + after
                changed = (addition.replace('--source ', '--source foreign ', 1)
                           if '--source ' in addition else addition.replace('retention-days: 14', 'retention-days: 13', 1))
                mutants = {
                    'missing': original.replace(addition, '', 1),
                    'duplicate': original.replace(addition, addition + addition, 1),
                    'changed': original.replace(addition, changed, 1),
                    'reordered': original.replace(exact, addition + before + after, 1),
                    'moved': original.replace(addition, '', 1) + addition,
                    'preceding-gate': original.replace(exact, before + '# foreign\n' + addition + after, 1),
                    'following-gate': original.replace(exact, before + addition + '# foreign\n' + after, 1),
                }
                for mutation, text in mutants.items():
                    with self.subTest(workflow=workflow, job=job, mutation=mutation):
                        with self.assertRaises(AssertionError):
                            inverse_lifecycle_workflow_checks(text, workflow)
        with self.assertRaises(AssertionError):
            inverse_lifecycle_workflow_checks('', 'foreign.yml')

    def test_windows_producer_budget_has_only_one_exact_outer_job_change(self):
        current = input_lifecycle.inverse_blocks(_lifecycle_projection.inverse_sdk_compatibility_retention(C.canonical((ROOT / '.github/workflows/build.yml').read_bytes()).decode(), 'build.yml').encode(), '.github/workflows/build.yml').decode()
        budget_only = inverse_windows_producer_budget(current)
        early = _lifecycle_projection.early_windows_lifecycle()
        self.assertEqual(budget_only.count(early), 1)
        original = budget_only.replace(early, '', 1)
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(),
                         'cfabeb31aef95f1c8a20df5946caf9ea83f9b95c37ca851a0f5c709a0645cdf8')
        self.assertEqual(current.replace('    timeout-minutes: 240\n', '    timeout-minutes: 180\n', 1), budget_only)
        self.assertEqual(current.count('    timeout-minutes: 180\n'), 2)
        self.assertEqual(hashlib.sha256(C.canonical((ROOT / 'scripts/test-support-launch-windows.ps1').read_bytes())).hexdigest(),
                         'c1f3e99ebda0a146bce9b54f215feeebd0c6d19bec19f2a280259d311983c44b')
        for mutation in (current.replace('timeout-minutes: 240', 'timeout-minutes: 239'),
                         current.replace('    timeout-minutes: 240\n', ''),
                         current.replace('    timeout-minutes: 240\n', '    timeout-minutes: 240\n' * 2),
                         current.replace('    runs-on: windows-2022\n    timeout-minutes: 240\n',
                                         '    timeout-minutes: 240\n    runs-on: windows-2022\n'),
                         current.replace('    timeout-minutes: 180\n', '    timeout-minutes: 240\n', 1)):
            with self.subTest(digest=hashlib.sha256(mutation.encode()).hexdigest()), self.assertRaises(AssertionError):
                inverse_windows_producer_budget(mutation)

    def test_early_windows_lifecycle_is_fatal_native_dart_after_clone_before_composition(self):
        early = _lifecycle_projection.early_windows_lifecycle()
        for workflow, job, clone, composition, late in (
                ('build.yml', 'windows', 'Fetch upstream RustDesk', 'Apply Mixel-Remote branding',
                 'Preflight generated support source and reporter'),
                ('preflight.yml', 'windows-packaging', 'Fetch pinned complete Windows source',
                 'Fetch exact pinned native AV1 API headers without codec compilation',
                 'Exercise complete Windows generated source, keep-awake and native branding')):
            text = C.canonical((ROOT / '.github/workflows' / workflow).read_bytes()).decode()
            section = re.search(r'^  ' + re.escape(job) + r':\n(.*?)(?=^  [A-Za-z][A-Za-z0-9_-]*:|\Z)',
                                text, re.M | re.S).group(1)
            self.assertEqual(section.count(early), 1)
            self.assertLess(section.index('name: ' + clone), section.index(early))
            self.assertLess(section.index(early), section.index('name: ' + composition))
            self.assertLess(section.index('name: ' + composition), section.index('name: ' + late))
            control = early.split('      - name: Preserve early Windows', 1)[0]
            self.assertNotIn('        if:', control); self.assertNotIn('continue-on-error:', control)
            self.assertIn('        shell: pwsh\n', control)
            self.assertIn('--source rustdesk --dart $env:DART_BIN --output $early\n', control)
            self.assertIn("if ($LASTEXITCODE -ne 0) { throw 'Early actual file-dialog lifecycle controls failed.' }", control)
            self.assertIn("'mixel-file-dialog-lifecycle-early'", control)
            self.assertNotIn('command -v', control)
        suite = ast.parse((ROOT / 'scripts/test-support-file-dialog-lifecycle.py').read_text(encoding='utf-8'))
        tests = next(node for node in suite.body if isinstance(node, ast.ClassDef) and node.name == 'LifecycleTests')
        self.assertEqual(len([node for node in tests.body if isinstance(node, ast.FunctionDef)
                              and node.name.startswith('test_')]), 9)
        dispatcher = (ROOT / 'scripts/test-windows-generated-source.sh').read_text(encoding='utf-8')
        self.assertEqual(dispatcher.count('python scripts/test-support-file-dialog-lifecycle.py --source '), 1)

    def test_workflow_whole_inverse_has_only_two_ordered_additions(self):
        value = input_lifecycle.inverse_blocks(C.canonical((ROOT / '.github/workflows/build.yml').read_bytes()), '.github/workflows/build.yml').decode()
        value = inverse_windows_producer_budget(value)
        value = inverse_lifecycle_workflow_checks(value, 'build.yml')
        value = inverse_windows_sdk_source_diagnostics(value, 'build.yml')
        value = inverse_uri_workflow_checks(value, 'build.yml')
        value = inverse_uri_bridge_checks(value)
        value = inverse_linux_close_workflow_checks(value, 'build.yml')
        value = inverse_linux_close_bridge_checks(value)
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
        text = inverse_lifecycle_workflow_checks(text, 'preflight.yml')
        text = inverse_windows_preflight_budget(text)
        text = inverse_windows_sdk_source_diagnostics(text, 'preflight.yml')
        text = inverse_uri_workflow_checks(text, 'preflight.yml')
        text = inverse_linux_close_workflow_checks(text, 'preflight.yml')
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

    def test_linux_close_workflow_projection_refuses_changed_missing_duplicate_reordered_or_moved_checks(self):
        for workflow in ('build.yml', 'preflight.yml'):
            text = C.canonical((ROOT / '.github/workflows' / workflow).read_bytes()).decode()
            text = inverse_uri_workflow_checks(text, workflow)
            projected = inverse_linux_close_workflow_checks(text, workflow)
            self.assertNotIn('test-support-linux-window-close.py', projected)
            self.assertNotIn('test-support-linux-child-window-close.py', projected)
            for before, addition, after, count in linux_close_workflow_projections(workflow):
                lines = addition.splitlines(keepends=True)
                first = ''.join(lines[:len(lines) // 2])
                second = ''.join(lines[len(lines) // 2:])
                mutations = {
                    'missing': '', 'missing-first': second, 'missing-second': first,
                    'duplicate': addition + addition, 'duplicate-first': first + addition,
                    'changed': addition.replace('--source ', '--source changed ', 1),
                    'reordered': second + first,
                }
                for kind, changed in mutations.items():
                    with self.subTest(workflow=workflow, addition=addition, mutation=kind):
                        with self.assertRaises(AssertionError):
                            inverse_linux_close_workflow_checks(text.replace(addition, changed, 1), workflow)
                moved = text.replace(addition, '', 1) + addition
                for kind, changed in (
                        ('moved', moved),
                        ('preceding-gate', text.replace(before + addition + after, before.rstrip('\n') + ' # changed\n' + addition + after, 1)),
                        ('following-gate', text.replace(before + addition + after, before + addition + after.rstrip('\n') + ' # changed\n', 1))):
                    with self.subTest(workflow=workflow, addition=addition, mutation=kind):
                        with self.assertRaises(AssertionError):
                            inverse_linux_close_workflow_checks(changed, workflow)
        with self.assertRaises(AssertionError):
            inverse_linux_close_workflow_checks('', 'foreign.yml')

    def test_linux_close_bridge_projection_refuses_changed_missing_duplicate_reordered_or_moved_brackets(self):
        text = input_lifecycle.inverse_blocks(C.canonical((ROOT / '.github/workflows/build.yml').read_bytes()), '.github/workflows/build.yml').decode()
        text = inverse_uri_bridge_checks(text)
        before, restore, original, reapply, after = linux_close_bridge_projection()
        current = restore + original + reapply
        projected = inverse_linux_close_bridge_checks(text)
        self.assertEqual(projected.count(before + original + after), 1)
        self.assertNotIn(restore, projected)
        self.assertNotIn(reapply, projected)
        lines = current.splitlines(keepends=True)
        mutations = {
            'missing-restore': original + reapply,
            'missing-reapply': restore + original,
            'missing-original': restore + reapply,
            'duplicate-restore': restore + current,
            'duplicate-reapply': current + reapply,
            'duplicate-original': restore + original + original + reapply,
            'changed-restore': current.replace('--restore', '--restore changed', 1),
            'changed-reapply': current.replace('--after-branding', '--after-branding changed', 1),
            'changed-texture': current.replace('RDREPO=. ', 'RDREPO=changed ', 1),
            'changed-window': current.replace('patch-support-macos-window.py', 'foreign-window.py', 1),
        }
        for index in range(len(lines) - 1):
            swapped = lines[:]
            swapped[index:index + 2] = reversed(swapped[index:index + 2])
            mutations['reordered-' + str(index)] = ''.join(swapped)
        for kind, changed in mutations.items():
            with self.subTest(mutation=kind), self.assertRaises(AssertionError):
                inverse_linux_close_bridge_checks(text.replace(current, changed, 1))
        for kind, changed in (
                ('moved', text.replace(current, original, 1) + restore + reapply),
                ('preceding-gate', text.replace(before + current, before + '# changed\n' + current, 1)),
                ('following-gate', text.replace(current + after, current + '# changed\n' + after, 1))):
            with self.subTest(mutation=kind), self.assertRaises(AssertionError):
                inverse_linux_close_bridge_checks(changed)

    def test_windows_preflight_budget_exact_inverse_refuses_drift_before_commands(self):
        path = ROOT / '.github/workflows/preflight.yml'
        raw = path.read_bytes()
        text = C.canonical(raw).decode()
        before, current, after = windows_preflight_budget_projection()
        approved = before + current + after
        forbidden = AssertionError('Budget source projection attempted a command')
        with mock.patch.object(subprocess, 'run', side_effect=forbidden), mock.patch.object(subprocess, 'Popen', side_effect=forbidden):
            projected = inverse_windows_preflight_budget(text)
            self.assertEqual(projected, text.replace(current, '    timeout-minutes: 120\n', 1))
            for changed in (
                    text.replace(approved, before + after, 1),
                    text.replace(current, current + current, 1),
                    text.replace(approved, approved + approved, 1),
                    text.replace(current, current.replace('240', '241'), 1),
                    text.replace(current, current.replace('240', '180'), 1),
                    text.replace(current, current.replace('240', '120'), 1),
                    text.replace(approved, before + after, 1) + current,
                    text.replace(approved, before + '# disjoint\n' + current + after, 1),
                    text.replace(before, before.replace('windows-packaging:', 'foreign-job:'), 1)):
                with self.assertRaises(AssertionError):
                    inverse_windows_preflight_budget(changed)
        self.assertEqual(path.read_bytes(), raw)

    def test_uri_workflow_projection_refuses_drift_missing_duplicate_or_disjoint_checks_before_commands(self):
        for workflow in ('build.yml', 'preflight.yml'):
            path = ROOT / '.github/workflows' / workflow
            raw = path.read_bytes()
            text = C.canonical(raw).decode()
            with mock.patch.object(subprocess, 'run', forbidden), mock.patch.object(subprocess, 'Popen', forbidden):
                projected = inverse_uri_workflow_checks(text, workflow)
                self.assertNotIn('scripts/test-support-uri-routing.py', projected)
                for before, addition, after, count in uri_workflow_projections(workflow):
                    for kind, changed in (
                            ('missing', text.replace(addition, '', 1)),
                            ('duplicate', text.replace(addition, addition + addition, 1)),
                            ('argument-drift', text.replace(addition, addition.replace('--source ', '--source foreign ', 1), 1)),
                            ('disjoint', text.replace(before + addition + after, before + '# disjoint\n' + addition + after, 1)),
                            ('reordered', text.replace(addition + after, after + addition, 1)),
                            ('moved', text.replace(addition, '', 1) + addition)):
                        with self.subTest(workflow=workflow, mutation=kind), self.assertRaises(AssertionError):
                            inverse_uri_workflow_checks(changed, workflow)
            if workflow == 'preflight.yml':
                for before, current, after in (uri_preflight_budget_projection(), uri_mac_preflight_budget_projection()):
                    for changed in (text.replace(before + current + after, before + after, 1),
                                    text.replace(before + current + after, before + current + current + after, 1),
                                    text.replace(before + current + after, before + current.replace(': ', ': 1', 1) + after, 1),
                                    text.replace(before + current + after, before + after, 1) + current,
                                    text.replace(before + current + after, before + '# disjoint\n' + current + after, 1)):
                        with self.assertRaises(AssertionError):
                            inverse_uri_workflow_checks(changed, workflow)
            self.assertEqual(path.read_bytes(), raw)
        with self.assertRaises(AssertionError):
            inverse_uri_workflow_checks('', 'foreign.yml')

    def test_uri_bridge_projection_refuses_changed_or_disjoint_six_command_sequence_before_commands(self):
        path = ROOT / '.github/workflows/build.yml'
        raw = path.read_bytes()
        text = input_lifecycle.inverse_blocks(C.canonical(raw), '.github/workflows/build.yml').decode()
        before, restore, original, reapply, after = uri_bridge_projection()
        current = restore + original + reapply
        lines = current.splitlines(keepends=True)
        with mock.patch.object(subprocess, 'run', forbidden), mock.patch.object(subprocess, 'Popen', forbidden):
            projected = inverse_uri_bridge_checks(text)
            self.assertEqual(projected.count(before + original + after), 1)
            for index, line in enumerate(lines):
                for kind, changed in (
                        ('missing', text.replace(line, '', 1)),
                        ('duplicate', text.replace(line, line + line, 1)),
                        ('argument-drift', text.replace(line, line.rstrip('\n') + ' --extra\n', 1))):
                    with self.subTest(index=index, mutation=kind), self.assertRaises(AssertionError):
                        inverse_uri_bridge_checks(changed)
            for index in range(len(lines) - 1):
                swapped = lines[:]
                swapped[index:index + 2] = reversed(swapped[index:index + 2])
                with self.subTest(swap=index), self.assertRaises(AssertionError):
                    inverse_uri_bridge_checks(text.replace(current, ''.join(swapped), 1))
            for changed in (text.replace(current, '', 1) + current,
                            text.replace(before + current, before + '# disjoint\n' + current, 1),
                            text.replace(current + after, current + '# disjoint\n' + after, 1)):
                with self.assertRaises(AssertionError):
                    inverse_uri_bridge_checks(changed)
        self.assertEqual(path.read_bytes(), raw)

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


    def test_initial_identity_attribution_covers_all_nine_subphases_without_recovery(self):
        cases = (
            ('post-connect-lease-guard', 'guard', 3, 'lease-guard-refused'),
            ('peer-pid-first', 'pid-error', None, 'peer-pid-unavailable'),
            ('peer-uid-first', 'uid-error', None, 'peer-uid-unavailable'),
            ('peer-pid-comparison', 'pid-foreign', None, 'peer-pid-mismatch'),
            ('peer-uid-comparison', 'uid-foreign', None, 'peer-uid-mismatch'),
            ('peer-pid-recheck', 'pid-changed', None, 'peer-pid-changed'),
            ('peer-uid-recheck', 'uid-changed', None, 'peer-uid-changed'),
            ('post-peer-lease-guard', 'guard', 4, 'lease-guard-refused'),
            ('post-close-lease-guard', 'guard', 5, 'lease-guard-refused'))
        for subphase, kind, guard_at, reason in cases:
            with self.subTest(subphase=subphase):
                f = IncomingSettlementFixture(self.f.app); lease = f.capture()
                f.stopped = True; f.connections.clear(); f.outcomes = ['active', 'refused']
                original_guard = C.incoming_cleanup_lease_guard; guard_calls = 0
                def guard(*args, **kwargs):
                    nonlocal guard_calls
                    guard_calls += 1
                    if guard_calls == guard_at: raise RuntimeError('private guard detail')
                    return original_guard(*args, **kwargs)
                with f.seams(), contextlib.ExitStack() as stack:
                    if kind == 'guard': stack.enter_context(mock.patch.object(C, 'incoming_cleanup_lease_guard', guard))
                    elif kind.startswith('pid'):
                        values = {'pid-error': [OSError(errno.EPERM, 'private API detail')],
                                  'pid-foreign': [999], 'pid-changed': [101, 999]}[kind]
                        stack.enter_context(mock.patch.object(C.tray, 'peer_pid', side_effect=values))
                    else:
                        values = {'uid-error': [OSError(errno.ENOTCONN, 'private API detail')],
                                  'uid-foreign': [f.uid + 1], 'uid-changed': [f.uid, f.uid + 1]}[kind]
                        stack.enter_context(mock.patch.object(C, 'incoming_peer_uid', side_effect=values))
                    with self.assertRaises(RuntimeError) as raised:
                        C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
                self.assertIs(raised.exception, f.first_active[0])
                self.assertEqual(raised.exception.args, ('A Mixel IPC endpoint is already active; refusing tray fixture',))
                identity = f.facts['incomingInitialIdentityFailure']
                self.assertEqual((identity['subphase'], identity['reason']), (subphase, reason))
                self.assertEqual(f.facts['incomingEndpointSettlement']['attempts'], 0)
                self.assertEqual(len(f.connections), 1); self.assertTrue(f.connections[0].closed)
                self.assertEqual(f.cleanup_calls, []); self.assertNotIn('private', json.dumps(f.facts))

    def test_initial_peer_pid_closed_nonpositive_and_invalid_api_results_stay_fatal(self):
        for value, comparison, reason in ((0, 'nonpositive', 'peer-pid-nonpositive'),
                (True, 'unavailable', 'peer-pid-unavailable'), (999, 'foreign', 'peer-pid-mismatch')):
            with self.subTest(value=value):
                f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
                f.connections.clear(); f.outcomes = ['active', 'refused']
                with f.seams(), mock.patch.object(C.tray, 'peer_pid', return_value=value):
                    with self.assertRaises(RuntimeError) as raised:
                        C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
                self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(f.cleanup_calls, [])
                identity = f.facts['incomingInitialIdentityFailure']
                self.assertEqual((identity['peerPidComparison'], identity['reason']), (comparison, reason))
        f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
        original_peer = C.tray.peer_pid
        def zero_peer(connection):
            with mock.patch.object(connection, 'getsockopt', return_value=struct.pack('i', 0)):
                return original_peer(connection)
        with f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=zero_peer):
            with self.assertRaises(RuntimeError) as raised:
                C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
        self.assertIs(raised.exception, f.first_active[0])
        self.assertEqual(f.facts['incomingInitialIdentityFailure']['subphase'], 'peer-pid-first')
        self.assertEqual(f.facts['incomingInitialIdentityFailure']['peerUidComparison'], 'not-read')

    def test_initial_diagnostic_errno_or_ctypes_metadata_failure_preserves_original_error(self):
        class PrivateOSError(OSError):
            @property
            def errno(self): raise ValueError('private metadata')
            def __str__(self): raise AssertionError('Exception text read')
        f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
        f.connections.clear(); f.outcomes = ['active', 'refused']
        with f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=PrivateOSError()):
            with self.assertRaises(RuntimeError) as raised:
                C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
        self.assertIs(raised.exception, f.first_active[0])
        self.assertIsNone(f.facts['incomingInitialIdentityFailure']['errno'])
        self.assertEqual(f.facts['incomingInitialIdentityFailure']['errorType'], 'OSError')
        original_uid = C.incoming_peer_uid
        function = mock.Mock(return_value=-1)
        with mock.patch.object(C.ctypes, 'CDLL', return_value=SimpleNamespace(getpeereid=function)), \
                mock.patch.object(C.ctypes, 'get_errno', side_effect=ValueError('private errno metadata')):
            with self.assertRaises(RuntimeError) as uid_error:
                original_uid(SimpleNamespace(fileno=lambda: 7))
        self.assertEqual(uid_error.exception.args, ('Incoming cleanup peer credentials unavailable',))
        self.assertIsNone(C.incoming_initial_identity_errno(uid_error.exception))
        self.assertEqual(function.call_count, 1)

    def test_initial_identity_extraction_is_closed_and_cannot_export_raw_metadata(self):
        valid = {'subphase': 'peer-pid-first', 'reason': 'peer-pid-unavailable', 'errorType': 'OSError',
            'errno': errno.ENOTCONN, 'peerPidComparison': 'unavailable', 'peerUidComparison': 'not-read'}
        error = RuntimeError('private original text'); error._mixel_incoming_initial_identity_failure = valid
        self.assertEqual(C.incoming_initial_identity_failure_fact(error), valid)
        for key, value in (('subphase', 'private-stage'), ('reason', 'private-token'), ('errorType', []),
                ('errno', True), ('errno', 0x7fffffff), ('peerPidComparison', 101), ('peerUidComparison', '501'),
                ('rawPath', '/private/secret')):
            with self.subTest(key=key):
                error._mixel_incoming_initial_identity_failure = {**valid, key: value}
                self.assertIsNone(C.incoming_initial_identity_failure_fact(error))
        class DictTrap(dict):
            def __iter__(self): raise AssertionError('private dict iteration')
        error._mixel_incoming_initial_identity_failure = DictTrap(valid)
        self.assertIsNone(C.incoming_initial_identity_failure_fact(error))
        self.assertIsNone(C.incoming_initial_identity_failure_fact(SimpleNamespace()))
        self.assertIsNone(C.incoming_initial_identity_errno(OSError(0x7fffffff, 'private token')))

    def test_initial_attribution_is_absent_on_success_or_later_retry_refusal(self):
        for outcomes in (['refused'], ['active', 'refused'], ['active', 'foreign', 'refused']):
            f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.outcomes = list(outcomes)
            if 'foreign' in outcomes:
                with self.assertRaises(RuntimeError) as raised: f.cleanup(lease)
                self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(f.cleanup_calls, [])
            else: f.cleanup(lease)
            self.assertNotIn('incomingInitialIdentityFailure', f.facts)


    def test_closed_initial_peer_exact_signature_only_enters_fresh_refused_settlement(self):
        f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
        f.connections.clear(); f.outcomes = ['active', 'refused']; events = []
        def peer(connection):
            events.append(('pid', id(connection)))
            raise OSError(errno.ENOTCONN, 'private closed peer')
        def uid(connection):
            events.append(('uid', id(connection)))
            return f.uid
        with f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=peer), \
                mock.patch.object(C, 'incoming_peer_uid', side_effect=uid):
            C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
        self.assertEqual([event[0] for event in events], ['pid', 'uid', 'pid', 'uid'])
        self.assertEqual(len({event[1] for event in events}), 1)
        self.assertEqual(len(f.connections), 2); self.assertTrue(all(c.closed for c in f.connections))
        self.assertEqual(len(f.first_active), 1); self.assertEqual(len(f.cleanup_calls), 1)
        self.assertEqual((f.facts['incomingEndpointSettlement']['state'], f.facts['incomingEndpointSettlement']['attempts']), ('settled', 1))
        self.assertNotIn('incomingInitialIdentityFailure', f.facts)
        self.assertNotIn('private', json.dumps(f.facts))

    def test_closed_initial_peer_other_errno_subclass_or_unknown_errno_stays_first_fatal(self):
        class ErrorSubclass(OSError): pass
        class HiddenErrno(OSError):
            @property
            def errno(self): raise ValueError('private errno accessor')
        errors = [OSError(number, 'private different error') for number in
            sorted(C.INCOMING_INITIAL_IDENTITY_ERRNOS - {errno.ENOTCONN})]
        errors += [ErrorSubclass(errno.ENOTCONN, 'private subclass'), HiddenErrno(), OSError(), OSError(True, 'private bool')]
        for error in errors:
            with self.subTest(error=type(error).__name__, errno=getattr(error, 'args', ())):
                f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
                f.connections.clear(); f.outcomes = ['active', 'refused']
                with f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=error), \
                        mock.patch.object(C, 'incoming_peer_uid', side_effect=AssertionError('UID must not be read')):
                    with self.assertRaises(RuntimeError) as raised:
                        C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
                self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(f.cleanup_calls, [])
                self.assertEqual(len(f.connections), 1)
                self.assertEqual(f.facts['incomingInitialIdentityFailure']['subphase'], 'peer-pid-first')

    def test_closed_initial_peer_repeat_must_remain_exact_native_notconnected(self):
        class ErrorSubclass(OSError): pass
        for result in (101, 999, 0, True, None, OSError(errno.EPERM, 'private'),
                       ErrorSubclass(errno.ENOTCONN, 'private subclass'), RuntimeError('private')):
            with self.subTest(result=type(result).__name__):
                f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
                f.connections.clear(); f.outcomes = ['active', 'refused']
                with f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=[OSError(errno.ENOTCONN, 'private'), result]):
                    with self.assertRaises(RuntimeError) as raised:
                        C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
                self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(f.cleanup_calls, [])
                self.assertEqual(len(f.connections), 1)
                self.assertEqual(f.facts['incomingInitialIdentityFailure']['subphase'], 'peer-pid-recheck')

    def test_closed_initial_peer_uid_twice_must_match_exact_owned_uid(self):
        for values in ([True], [None], [0], [os.getuid()+1], [OSError(errno.ENOTCONN, 'private')],
                       [os.getuid(), True], [os.getuid(), None], [os.getuid(), os.getuid()+1],
                       [os.getuid(), OSError(errno.ENOTCONN, 'private')]):
            with self.subTest(values=[type(v).__name__ for v in values]):
                f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
                f.connections.clear(); f.outcomes = ['active', 'refused']
                with f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=OSError(errno.ENOTCONN, 'private')), \
                        mock.patch.object(C, 'incoming_peer_uid', side_effect=values):
                    with self.assertRaises(RuntimeError) as raised:
                        C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
                self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(f.cleanup_calls, [])
                self.assertEqual(len(f.connections), 1); self.assertNotIn('private', json.dumps(f.facts))

    def test_closed_initial_peer_full_lease_and_terminal_birth_guard_every_read(self):
        for step in ('first-uid', 'second-pid', 'second-uid', 'close'):
            for resource in ('directory', 'socket', 'marker', 'markerBytes', 'birth', 'missing'):
                with self.subTest(step=step, resource=resource):
                    f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
                    f.connections.clear(); f.outcomes = ['active', 'refused']; pid_reads = 0; uid_reads = 0
                    def mutate():
                        if resource == 'birth': f.birth_failure = RuntimeError('private reused birth')
                        elif resource == 'markerBytes': f.snapshot_base[resource] = b'private replacement'
                        elif resource == 'missing': f.snapshot_base.update(socket=None, marker=None, markerBytes=None)
                        else:
                            value = f.snapshot_base[resource]; f.snapshot_base[resource] = (value[0], value[1]+1, *value[2:])
                    def peer(connection):
                        nonlocal pid_reads
                        pid_reads += 1
                        if step == 'second-pid' and pid_reads == 2: mutate()
                        raise OSError(errno.ENOTCONN, 'private')
                    def uid(connection):
                        nonlocal uid_reads
                        uid_reads += 1
                        if (step == 'first-uid' and uid_reads == 1) or (step == 'second-uid' and uid_reads == 2): mutate()
                        if step == 'close' and uid_reads == 2:
                            original_close = connection.close
                            def close():
                                original_close(); mutate()
                            connection.close = close
                        return f.uid
                    with f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=peer), \
                            mock.patch.object(C, 'incoming_peer_uid', side_effect=uid):
                        with self.assertRaises(RuntimeError) as raised:
                            C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
                    self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(f.cleanup_calls, [])
                    self.assertEqual(len(f.connections), 1); self.assertTrue(f.connections[0].closed)

    def test_closed_initial_peer_close_error_is_fatal_original_active_and_no_retry(self):
        f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
        f.connections.clear(); f.outcomes = ['active', 'refused']
        def peer(connection): raise OSError(errno.ENOTCONN, 'private')
        def uid(connection):
            def close():
                connection.closed = True
                raise OSError(errno.EIO, 'private close failure')
            connection.close = close
            return f.uid
        with f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=peer), \
                mock.patch.object(C, 'incoming_peer_uid', side_effect=uid):
            with self.assertRaises(RuntimeError) as raised:
                C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
        self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(f.cleanup_calls, [])
        self.assertEqual(len(f.connections), 1); self.assertTrue(f.connections[0].closed)

    def test_closed_initial_peer_deadline_remains_fatal_original_active(self):
        for step in ('first-uid', 'second-pid', 'second-uid'):
            with self.subTest(step=step):
                f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
                f.connections.clear(); f.outcomes = ['active', 'refused']; pid_reads = 0; uid_reads = 0
                def peer(connection):
                    nonlocal pid_reads
                    pid_reads += 1
                    if step == 'second-pid' and pid_reads == 2: f.clock = 6.0
                    raise OSError(errno.ENOTCONN, 'private')
                def uid(connection):
                    nonlocal uid_reads
                    uid_reads += 1
                    if (step == 'first-uid' and uid_reads == 1) or (step == 'second-uid' and uid_reads == 2): f.clock = 6.0
                    return f.uid
                original_errors = []
                with f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=peer), \
                        mock.patch.object(C, 'incoming_peer_uid', side_effect=uid):
                    bounded_probe = C.incoming_endpoint_inactive
                    def record_original(*arguments):
                        try: return bounded_probe(*arguments)
                        except C.IncomingSettlementDeadline as error:
                            original_errors.append(error.__cause__)
                            raise
                    with mock.patch.object(C, 'incoming_endpoint_inactive', side_effect=record_original), self.assertRaises(RuntimeError) as raised:
                        C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
                self.assertIs(raised.exception, original_errors[0]); self.assertEqual(f.cleanup_calls, [])
                self.assertEqual(raised.exception.args, ('A Mixel IPC endpoint is already active; refusing tray fixture',))
                self.assertEqual(len(f.connections), 1)

    def test_closed_initial_peer_later_unknown_peer_or_error_never_recovers(self):
        for outcome in ('foreign', 'foreign-uid', 'timeout', 'ambiguous'):
            f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
            f.connections.clear(); f.outcomes = ['active', outcome, 'refused']; reads = 0
            original_peer = C.tray.peer_pid
            def peer(connection):
                nonlocal reads
                reads += 1
                if reads <= 2: raise OSError(errno.ENOTCONN, 'private')
                return original_peer(connection)
            with self.subTest(outcome=outcome), f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=peer):
                with self.assertRaises(RuntimeError) as raised:
                    C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
            self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(f.cleanup_calls, [])
            self.assertEqual(len(f.connections), 2)

    def test_closed_initial_peer_fresh_monotonic_missing_still_uses_original_cleanup(self):
        f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
        f.connections.clear(); f.outcomes = ['active', 'missing']
        f.after_initial = lambda: f.snapshot_base.update(socket=None, marker=None, markerBytes=None)
        with f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=OSError(errno.ENOTCONN, 'private')):
            C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
        self.assertEqual(len(f.cleanup_calls), 1)
        self.assertEqual((f.facts['incomingEndpointSettlement']['state'], f.facts['incomingEndpointSettlement']['attempts']), ('settled', 1))
        self.assertTrue(lease['missing']['socket']); self.assertTrue(lease['missing']['marker'])
        self.assertTrue(all(c.closed for c in f.connections))

    def test_closed_initial_peer_persistent_active_or_final_cleanup_refusal_preserves_original(self):
        for final_error in (None, RuntimeError('private final cleanup')):
            f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
            f.connections.clear(); f.outcomes = ['active', 'refused'] if final_error else ['active']
            f.cleanup_error = final_error; reads = 0; original_peer = C.tray.peer_pid
            def peer(connection):
                nonlocal reads
                reads += 1
                if reads <= 2: raise OSError(errno.ENOTCONN, 'private')
                return original_peer(connection)
            with self.subTest(final_error=bool(final_error)), f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=peer):
                with self.assertRaises(RuntimeError) as raised:
                    C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
            self.assertIs(raised.exception, f.first_active[0])
            self.assertEqual(len(f.cleanup_calls), 1 if final_error else 0)
            self.assertEqual(f.facts['incomingEndpointSettlement']['failureCode'], 'cleanup-refused' if final_error else 'active-timeout')
            self.assertLessEqual(f.facts['incomingEndpointSettlement']['attempts'], 50)
            self.assertLessEqual(f.clock, 5.01)

    def test_exact_historical_caller_inverse_preserves_original_first_notconnected_failure(self):
        source = HELPER.read_text(encoding='utf-8')
        source = inverse_closed_pid_recheck_source(source)
        start = source.index('                phase("peer-pid-first")\n                try:\n')
        end = source.index('                attribution["peerPidComparison"] = incoming_initial_identity_comparison(first_pid,', start)
        source = source[:start] + '                phase("peer-pid-first")\n                first_pid = incoming_settlement_call(deadline, tray.peer_pid, self.connection)\n' + source[end:]
        source = source.replace('            self.closed_peer_qualified = False\n', '', 1)
        start = source.index('        def __exit__(self, *arguments):\n', source.index('def incoming_endpoint_inactive('))
        end = source.index('        def settimeout(self, value):\n', start)
        original_exit = '        def __exit__(self, *arguments):\n            self.connection.close()\n            try:\n                phase("post-close-lease-guard")\n                guard()\n            except BaseException as error:\n                remember(error)\n\n'
        source = source[:start] + original_exit + source[end:]
        source = source.replace('                except (ConnectionRefusedError, FileNotFoundError):\n                    inactive = True\n', '                except ConnectionRefusedError:\n                    inactive = True\n', 1)
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), '8cb84580e1f330a6649a014f29f80769b0c5059081445c15bc07ccedbf319e9f')
        node = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == 'incoming_endpoint_inactive')
        namespace = {}; exec(compile(ast.Module(body=[node], type_ignores=[]), '<exact-historical-caller>', 'exec'), namespace)
        historical = type(C.incoming_endpoint_inactive)(namespace['incoming_endpoint_inactive'].__code__, C.__dict__)
        f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
        f.connections.clear(); f.outcomes = ['active', 'refused']
        with mock.patch.object(C, 'incoming_endpoint_inactive', historical), f.seams(), \
                mock.patch.object(C.tray, 'peer_pid', side_effect=OSError(errno.ENOTCONN, 'private historical')):
            with self.assertRaises(RuntimeError) as raised:
                C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
        self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(f.cleanup_calls, [])
        self.assertEqual(len(f.connections), 1)
        self.assertEqual(f.facts['incomingInitialIdentityFailure'], {'subphase':'peer-pid-first',
            'reason':'peer-pid-unavailable', 'errorType':'OSError', 'errno':errno.ENOTCONN,
            'peerPidComparison':'unavailable', 'peerUidComparison':'not-read'})

    def test_exact_recheck_inverse_recovers_current_708_helper_bytes(self):
        source = inverse_closed_pid_recheck_source(HELPER.read_text(encoding='utf-8'))
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), '9fb58a9c5333df460cc4a676dc1499ee08af4af78edca762d0e20c59f0d03fc0')

    def test_exact_recheck_inverse_rejects_missing_duplicate_and_drifted_layers(self):
        source = HELPER.read_text(encoding='utf-8')
        for label, altered in (
                ('missing-pid', source.replace('                def pid_recheck_refused():', '                def changed_recheck():', 1)),
                ('duplicate-pid', source + source[source.index('                def pid_recheck_refused():'):source.index('                def uid_recheck_refused():')]),
                ('missing-uid', source.replace('                    if self.closed_peer_qualified and type(observed) is not int:', '                    if self.closed_peer_qualified and False:', 1)),
                ('duplicate-uid', source + '                    observed = incoming_settlement_call(deadline, incoming_peer_uid, self.connection)\n                    if self.closed_peer_qualified and type(observed) is not int:\n                        attribution["peerUidComparison"] = incoming_initial_identity_comparison(observed, lease["uid"])\n                        phase("peer-uid-recheck", "peer-uid-changed")\n                        raise RuntimeError("Incoming cleanup disconnected peer UID changed")\n                    refused = observed != first_uid\n'),
                ('changed-errno', source.replace('if type(closed_error) is not OSError or incoming_initial_identity_errno(closed_error) != errno.ENOTCONN:', 'if type(closed_error) is not OSError or incoming_initial_identity_errno(closed_error) != errno.ECONNRESET:', 1))):
            with self.subTest(malformed=label):
                with self.assertRaisesRegex(AssertionError, '^Exact closed peer recheck layer changed$'):
                    inverse_closed_pid_recheck_source(altered)

    def test_closed_pid_recheck_owned_identity_requires_separate_fresh_inactivity(self):
        for outcome in ('refused', 'missing'):
            with self.subTest(outcome=outcome):
                f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
                f.connections.clear(); f.outcomes = ['active', outcome]; reads = []; uid_reads = []
                def peer(connection):
                    reads.append(id(connection))
                    if len(reads) == 2: raise OSError(errno.ENOTCONN, 'private closed recheck')
                    return 101
                def uid(connection):
                    uid_reads.append(id(connection)); return f.uid
                with f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=peer), mock.patch.object(C, 'incoming_peer_uid', side_effect=uid):
                    C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
                self.assertEqual(len(reads), 2); self.assertEqual(len(uid_reads), 2)
                self.assertEqual(len(set(reads + uid_reads)), 1)
                self.assertEqual(len(f.connections), 2); self.assertTrue(all(c.closed for c in f.connections))
                self.assertEqual(len(f.first_active), 1); self.assertEqual(len(f.cleanup_calls), 1)
                self.assertEqual((f.facts['incomingEndpointSettlement']['state'], f.facts['incomingEndpointSettlement']['attempts']), ('settled', 1))
                self.assertNotIn('incomingInitialIdentityFailure', f.facts)
                self.assertNotIn('private', json.dumps(f.facts))

    def test_closed_pid_recheck_other_errors_remain_first_fatal_without_retry(self):
        class ErrorSubclass(OSError): pass
        class HiddenErrno(OSError):
            @property
            def errno(self): raise ValueError('private errno')
        errors = [OSError(number, 'private') for number in sorted(C.INCOMING_INITIAL_IDENTITY_ERRNOS - {errno.ENOTCONN})]
        errors += [ErrorSubclass(errno.ENOTCONN, 'private'), HiddenErrno(), OSError(), OSError(True, 'private'), RuntimeError('private')]
        for error in errors:
            f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
            f.connections.clear(); f.outcomes = ['active', 'refused']; reads = 0
            def peer(connection):
                nonlocal reads
                reads += 1
                if reads == 2: raise error
                return 101
            with self.subTest(error=type(error).__name__), f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=peer):
                with self.assertRaises(RuntimeError) as raised:
                    C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
            self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(f.cleanup_calls, [])
            self.assertEqual(len(f.connections), 1); self.assertTrue(f.connections[0].closed)
            self.assertEqual(f.facts['incomingInitialIdentityFailure']['subphase'], 'peer-pid-recheck')

    def test_closed_pid_recheck_cached_uid_must_remain_exact_integer_owned_uid(self):
        for value in (True, None, 0, os.getuid()+1, float(os.getuid()), OSError(errno.ENOTCONN, 'private'), RuntimeError('private')):
            f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
            f.connections.clear(); f.outcomes = ['active', 'refused']; reads = 0
            def peer(connection):
                nonlocal reads
                reads += 1
                if reads == 2: raise OSError(errno.ENOTCONN, 'private')
                return 101
            with self.subTest(uid=type(value).__name__), f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=peer), mock.patch.object(C, 'incoming_peer_uid', side_effect=[f.uid, value]):
                with self.assertRaises(RuntimeError) as raised:
                    C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
            self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(f.cleanup_calls, [])
            self.assertEqual(len(f.connections), 1); self.assertTrue(f.connections[0].closed)

    def test_closed_pid_recheck_cannot_bypass_invalid_or_foreign_first_identity(self):
        for pid, uid in ((999, os.getuid()), (0, os.getuid()), (True, os.getuid()), (None, os.getuid()), (101, os.getuid()+1), (101, True), (101, None)):
            f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
            f.connections.clear(); f.outcomes = ['active', 'refused']; reads = 0
            def peer(connection):
                nonlocal reads
                reads += 1
                if reads == 2: raise OSError(errno.ENOTCONN, 'private')
                return pid
            with self.subTest(pid=pid, uid=uid), f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=peer), mock.patch.object(C, 'incoming_peer_uid', return_value=uid):
                with self.assertRaises(RuntimeError) as raised:
                    C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
            self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(reads, 1)
            self.assertEqual(f.cleanup_calls, []); self.assertEqual(len(f.connections), 1)

    def test_closed_pid_recheck_every_lease_resource_and_birth_stays_guarded(self):
        for step in ('pid-recheck', 'uid-recheck', 'close'):
            for resource in ('directory', 'socket', 'marker', 'markerBytes', 'birth', 'missing'):
                f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
                f.connections.clear(); f.outcomes = ['active', 'refused']; pid_reads = 0; uid_reads = 0
                def mutate():
                    if resource == 'birth': f.birth_failure = RuntimeError('private reused PID')
                    elif resource == 'markerBytes': f.snapshot_base[resource] = b'private replacement'
                    elif resource == 'missing': f.snapshot_base.update(socket=None, marker=None, markerBytes=None)
                    else:
                        value=f.snapshot_base[resource]; f.snapshot_base[resource]=(value[0],value[1]+1,*value[2:])
                def peer(connection):
                    nonlocal pid_reads
                    pid_reads += 1
                    if pid_reads == 2:
                        if step == 'pid-recheck': mutate()
                        raise OSError(errno.ENOTCONN, 'private')
                    return 101
                def uid(connection):
                    nonlocal uid_reads
                    uid_reads += 1
                    if uid_reads == 2:
                        if step == 'uid-recheck': mutate()
                        if step == 'close':
                            original_close=connection.close
                            def close(): original_close(); mutate()
                            connection.close=close
                    return f.uid
                with self.subTest(step=step, resource=resource), f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=peer), mock.patch.object(C, 'incoming_peer_uid', side_effect=uid):
                    with self.assertRaises(RuntimeError) as raised:
                        C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
                self.assertIs(raised.exception, f.first_active[0]); self.assertEqual(f.cleanup_calls, [])
                self.assertEqual(len(f.connections), 1); self.assertTrue(f.connections[0].closed)

    def test_closed_pid_recheck_deadline_and_close_errors_never_authorize_cleanup(self):
        for step in ('pid-recheck', 'uid-recheck', 'close-error'):
            f = IncomingSettlementFixture(self.f.app); lease = f.capture(); f.stopped = True
            f.connections.clear(); f.outcomes = ['active', 'refused']; reads = 0
            def peer(connection):
                nonlocal reads
                reads += 1
                if reads == 2:
                    if step == 'pid-recheck': f.clock=6.0
                    if step == 'close-error':
                        def close(): connection.closed=True; raise OSError(errno.EIO, 'private close')
                        connection.close=close
                    raise OSError(errno.ENOTCONN, 'private')
                return 101
            uid_reads = 0
            def uid(connection):
                nonlocal uid_reads
                uid_reads += 1
                if uid_reads==2 and step=='uid-recheck': f.clock=6.0
                return f.uid
            with self.subTest(step=step), f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=peer), mock.patch.object(C, 'incoming_peer_uid', side_effect=uid):
                with self.assertRaises(RuntimeError):
                    C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
            self.assertEqual(f.cleanup_calls, []); self.assertEqual(len(f.connections), 1)
            self.assertTrue(f.connections[0].closed)
            self.assertEqual(f.facts['incomingEndpointSettlement']['failureCode'], 'identity-refused' if step=='close-error' else 'active-timeout')

    def test_closed_pid_recheck_later_foreign_listener_or_cleanup_refusal_preserves_first_error(self):
        for outcome in ('foreign', 'foreign-uid', 'timeout', 'ambiguous', 'persistent', 'cleanup-refused'):
            f=IncomingSettlementFixture(self.f.app); lease=f.capture(); f.stopped=True; f.connections.clear()
            f.outcomes=['active'] if outcome=='persistent' else ['active', 'refused' if outcome=='cleanup-refused' else outcome, 'refused']
            if outcome=='cleanup-refused': f.cleanup_error=RuntimeError('private final cleanup')
            reads=0; original_peer=C.tray.peer_pid
            def peer(connection):
                nonlocal reads
                reads+=1
                if reads==2: raise OSError(errno.ENOTCONN, 'private')
                return original_peer(connection)
            with self.subTest(outcome=outcome), f.seams(), mock.patch.object(C.tray, 'peer_pid', side_effect=peer):
                with self.assertRaises(RuntimeError) as raised:
                    C.cleanup_incoming_endpoint(f.app, 101, f.birth(101), 202, f.birth(202), lease, f.facts)
            self.assertIs(raised.exception, f.first_active[0])
            self.assertEqual(len(f.cleanup_calls), 1 if outcome=='cleanup-refused' else 0)
            self.assertLessEqual(f.facts['incomingEndpointSettlement']['attempts'], 50); self.assertLessEqual(f.clock, 5.01)
            self.assertTrue(all(c.closed for c in f.connections))

    def test_closed_pid_recheck_positive_integer_first_identity_is_required(self):
        for pid, uid in ((101.0, os.getuid()), (-101, os.getuid()), (0, os.getuid()), (True, os.getuid()), (101, float(os.getuid())), (101, True)):
            f=IncomingSettlementFixture(self.f.app); lease=f.capture(); f.stopped=True; f.connections.clear(); f.outcomes=['active','refused']
            reads=0
            def peer(connection):
                nonlocal reads
                reads+=1
                if reads==2: raise OSError(errno.ENOTCONN, 'private')
                return pid
            with self.subTest(pid=type(pid).__name__,uid=type(uid).__name__), f.seams(), mock.patch.object(C.tray,'peer_pid',side_effect=peer), mock.patch.object(C,'incoming_peer_uid',return_value=uid):
                with self.assertRaises(RuntimeError) as raised:
                    C.cleanup_incoming_endpoint(f.app,101,f.birth(101),202,f.birth(202),lease,f.facts)
            self.assertIs(raised.exception,f.first_active[0]); self.assertEqual(f.cleanup_calls,[])
            self.assertEqual(reads,1); self.assertEqual(len(f.connections),1)

    def test_added_logical_source_views_reject_mixed_newlines_before_strict_guards(self):
        sources = {name: C.canonical((ROOT / name).read_bytes()) for name in (
            '.github/workflows/build.yml', '.github/workflows/preflight.yml',
            'scripts/test-support-launch-windows.ps1')}
        cases = (
            ('test_workflow_whole_inverse_has_only_two_ordered_additions', '.github/workflows/build.yml'),
            ('test_linux_close_bridge_projection_refuses_changed_missing_duplicate_reordered_or_moved_brackets', '.github/workflows/build.yml'),
            ('test_uri_bridge_projection_refuses_changed_or_disjoint_six_command_sequence_before_commands', '.github/workflows/build.yml'),
            ('test_windows_preflight_budget_exact_inverse_refuses_drift_before_commands', '.github/workflows/preflight.yml'),
            ('test_windows_producer_budget_has_only_one_exact_outer_job_change', 'scripts/test-support-launch-windows.ps1'),
        )
        for index, (method, relative) in enumerate(cases):
            carrier = self.directory / ('mixed-source-view-' + str(index))
            for name, logical in sources.items():
                path = carrier / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(logical)
            path = carrier / relative
            malformed = sources[relative].replace(b'\n', b'\r\n') + b'# bare LF is not the physical CRLF profile\n'
            path.write_bytes(malformed)
            with self.subTest(method=method), mock.patch.dict(globals(), {'ROOT': carrier}), \
                    mock.patch.object(subprocess, 'run', forbidden), mock.patch.object(subprocess, 'Popen', forbidden):
                with self.assertRaisesRegex(RuntimeError, '^Mixed concurrency fixture source newlines$'):
                    getattr(self, method)()
            self.assertEqual(path.read_bytes(), malformed)

    @unittest.skipUnless(sys.platform == 'darwin', 'Owned Darwin socket API regression')
    def test_native_owned_closed_connection_pid_notconnected_uid_stays_cached(self):
        # These sockets are all created by this Python process in its disposable
        # owned test directory. No app/other-process socket or IPC payload is used.
        native_directory = tempfile.TemporaryDirectory(prefix='mxl-peer-', dir='/tmp')
        directory = Path(native_directory.name).resolve(strict=True)
        endpoint = directory / 'owned.sock'
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        accepted = None
        try:
            listener.settimeout(1); client.settimeout(1)
            listener.bind(str(endpoint)); listener.listen(1)
            client.connect(str(endpoint)); accepted, _ = listener.accept()
            self.assertEqual(C.tray.peer_pid(client), os.getpid())
            self.assertEqual(C.incoming_peer_uid(client), os.getuid())
            accepted.close(); accepted = None; listener.close()
            for _ in range(2):
                with self.assertRaises(OSError) as raised: C.tray.peer_pid(client)
                self.assertIs(type(raised.exception), OSError)
                self.assertIs(type(raised.exception.errno), int)
                self.assertEqual(raised.exception.errno, errno.ENOTCONN)
                self.assertEqual(C.incoming_peer_uid(client), os.getuid())
        finally:
            if accepted is not None: accepted.close()
            client.close(); listener.close()
        attributes = endpoint.lstat()
        self.assertTrue(stat.S_ISSOCK(attributes.st_mode)); self.assertEqual(attributes.st_uid, os.getuid())
        endpoint.unlink(); native_directory.cleanup()


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
