#!/usr/bin/env python3
"""Execute the Mac tray CI gate's real Python bodies with native seams forbidden.

This test never imports or runs the platform gate. Its AST extraction preserves
the current function bodies; OS app launch, sockets, process lookup and signals
are replaced by explicit in-memory fixtures. Only disposable local files exist.
"""
import argparse
import ast
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import signal
import stat
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SOURCE = Path(__file__).with_name("test-support-launch-macos-tray.py")


def forbidden(*args, **kwargs):
    raise AssertionError("A source-only control attempted a native operation")


class Connection:
    def __init__(self, connect_error=None):
        self.connect_error = connect_error
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def settimeout(self, value):
        pass

    def connect(self, value):
        if self.connect_error:
            raise self.connect_error

    def sendall(self, value):
        self.sent.append(value)

    def getsockopt(self, *args):
        return struct.pack("i", 101)


class VirtualPath:
    def __init__(self, name, attributes=None, data=b""):
        self.name = name
        self.attributes = attributes
        self.data = data
        self.deleted = False
        self.pid_file = None

    def __str__(self):
        return self.name

    def lstat(self):
        return self.attributes() if callable(self.attributes) else self.attributes

    def with_suffix(self, suffix):
        return self.pid_file

    def unlink(self, **kwargs):
        self.deleted = True


def attributes(inode=1, mode=stat.S_IFREG | 0o600, size=3):
    return SimpleNamespace(st_dev=1, st_ino=inode, st_mode=mode,
                           st_uid=os.getuid(), st_nlink=1, st_size=size,
                           st_mtime_ns=100)


def identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
            value.st_nlink, value.st_size, value.st_mtime_ns)


def extracted_namespace(source):
    parsed = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    functions = [node for node in parsed.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    expected = {"tray_swift", "isolated_ci", "process_path", "peer_pid", "query",
                "endpoint_inactive", "new_tray_marker", "health", "gui_owner",
                "no_connections", "launch_identity", "terminate_owned", "cleanup_endpoint",
                "runtime", "main"}
    if not expected <= {node.name for node in functions}:
        raise RuntimeError("The actual tray gate function surface changed")
    unix = SimpleNamespace(IPC_PATH=Path("/synthetic-ci/ipc"), PUBLIC_KEY="synthetic-public-key",
                           URI="synthetic-invalid-support-intent", frame=lambda value: value,
                           read_frame=forbidden)
    desktop = SimpleNamespace(unix=unix, probe=forbidden, helper_request=forbidden,
                              compile_helper=forbidden, scoped_files=forbidden,
                              bounded_read=forbidden, file_identity=identity,
                              privacy_issue=lambda _: False, safe_error=lambda _: "fixed failure")
    native_socket = SimpleNamespace(AF_UNIX=1, SOCK_STREAM=1, socket=forbidden,
                                    timeout=TimeoutError)
    native_os = SimpleNamespace(kill=forbidden, getuid=os.getuid, fsdecode=os.fsdecode,
                                environ={}, path=os.path)
    namespace = {"__name__": "source_only_tray_gate", "argparse": argparse,
                 "ctypes": SimpleNamespace(CDLL=forbidden), "datetime": datetime,
                 "timezone": timezone, "hashlib": hashlib, "json": json,
                 "os": native_os, "Path": Path, "plistlib": plistlib,
                 "pwd": SimpleNamespace(getpwuid=forbidden), "signal": signal,
                 "socket": native_socket, "stat": stat, "struct": struct,
                 "subprocess": SimpleNamespace(SubprocessError=subprocess.SubprocessError,
                                                run=forbidden),
                 "sys": SimpleNamespace(platform="source-only", argv=[], stderr=io.StringIO()),
                 "tempfile": tempfile,
                 "time": SimpleNamespace(time=lambda: 200.0, monotonic=forbidden, sleep=forbidden),
                 "desktop": desktop, "unix": unix, "URL_SOCKET": Path("/synthetic-ci/ipc_url"),
                 "TRAY_MARKER": b"Mixel support URL tray route: dispatch",
                 "MAX_LOG_BYTES": 4 * 1024 * 1024,
                 "CRITICAL": ("Contents/MacOS/Mixel-Remote", "Contents/MacOS/service",
                              "Contents/Frameworks/liblibrustdesk.dylib",
                              "Contents/Frameworks/FlutterMacOS.framework/FlutterMacOS")}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


class TrayControls(unittest.TestCase):
    def setUp(self):
        self.n = extracted_namespace(SOURCE)
        self.app = Path("/synthetic-ci/Mixel-Remote.app")
        self.path = self.app / "Contents/MacOS/Mixel-Remote"

    def test_isolated_ci_requires_all_three_conditions(self):
        for platform, actions, runner in (("linux", "true", "macOS"),
                                           ("darwin", "false", "macOS"),
                                           ("darwin", "true", "Linux")):
            self.n["sys"].platform = platform
            self.n["os"].environ = {"GITHUB_ACTIONS": actions, "RUNNER_OS": runner}
            with self.assertRaises(RuntimeError):
                self.n["isolated_ci"]()
        self.n["sys"].platform = "darwin"
        self.n["os"].environ = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "macOS"}
        self.n["isolated_ci"]()

    def test_swift_is_actual_os_url_activation_and_server_start(self):
        parsed = ast.parse(SOURCE.with_name("test-support-launch-macos.py").read_text())
        swift = next(ast.literal_eval(node.value) for node in parsed.body
                     if isinstance(node, ast.Assign) and any(isinstance(name, ast.Name) and name.id == "SWIFT"
                                                           for name in node.targets))
        corrected = self.n["tray_swift"](swift)
        self.assertIn('configuration.arguments = ["--server"]', corrected)
        self.assertIn('if action == "ordinary" || action == "tray" {', corrected)
        self.assertIn('configuration.createsNewApplicationInstance = false', corrected)
        self.assertIn('configuration.allowsRunningApplicationSubstitution = false', corrected)
        self.assertIn('NSWorkspace.shared.open([supportURL], withApplicationAt: application', corrected)
        with self.assertRaises(RuntimeError):
            self.n["tray_swift"](swift.replace('configuration.addsToRecentItems = false', 'changed'))

    def test_query_sends_only_after_kernel_and_executable_identity(self):
        for peer, path in ((102, self.path), (101, Path("/other/Mixel-Remote"))):
            connection = Connection()
            self.n["socket"].socket = lambda *_: connection
            self.n["peer_pid"] = lambda _: peer
            self.n["process_path"] = lambda _: path
            with self.assertRaises(RuntimeError):
                self.n["query"](101, self.app, "VideoConnCount")
            self.assertEqual(connection.sent, [])
        connection = Connection()
        self.n["socket"].socket = lambda *_: connection
        self.n["peer_pid"] = lambda _: 101
        self.n["process_path"] = lambda _: self.path
        self.n["unix"].read_frame = lambda _: b'{"t":"VideoConnCount","c":0}'
        self.assertEqual(self.n["query"](101, self.app, "VideoConnCount"), 0)
        self.assertEqual(len(connection.sent), 1)
        self.n["unix"].read_frame = lambda _: b'{"t":"Other","c":0}'
        with self.assertRaises(RuntimeError):
            self.n["query"](101, self.app, "VideoConnCount")

    def test_kernel_peer_pid_rejects_zero_or_negative(self):
        for pid in (0, -1):
            connection = Connection()
            connection.getsockopt = lambda *_: struct.pack("i", pid)
            with self.assertRaises(RuntimeError):
                self.n["peer_pid"](connection)
        self.assertEqual(self.n["peer_pid"](Connection()), 101)

    def test_connection_count_is_exact_nonnegative_integer(self):
        for value in (False, True, 0.0, "0", None, -1):
            self.n["query"] = lambda *_: value
            with self.assertRaises(RuntimeError):
                self.n["no_connections"](101, self.app)
        for value, expected in ((0, True), (1, False)):
            self.n["query"] = lambda *_: value
            self.assertIs(self.n["no_connections"](101, self.app), expected)

    def test_health_requires_runtime_guard_relay_and_online(self):
        options = {"custom-rendezvous-server": "rs.mixel.ch", "relay-server": "rs.mixel.ch",
                   "key": self.n["unix"].PUBLIC_KEY}
        result = {"VideoConnCount": 0, "Config": ["mixel-support-invite-attended", ""],
                  "OnlineStatus": [1, True], "Options": options}
        self.n["query"] = lambda _pid, _app, kind, *_: result[kind]
        self.assertTrue(self.n["health"](101, self.app, False))
        self.assertFalse(self.n["health"](101, self.app, True))
        result["Config"] = ["mixel-support-invite-attended", "attended-runtime-v2"]
        self.assertTrue(self.n["health"](101, self.app, True))
        for invalid in ([True, True], [1, 1], [0, True]):
            result["OnlineStatus"] = invalid
            if invalid == [0, True]:
                self.assertFalse(self.n["health"](101, self.app, True))
            else:
                with self.assertRaises(RuntimeError):
                    self.n["health"](101, self.app, True)
        result["OnlineStatus"] = [1, True]
        result["Options"] = {**options, "mixel-support-invite-attended": "Y"}
        with self.assertRaises(RuntimeError):
            self.n["health"](101, self.app, True)
        result["Options"] = {**options, "relay-server": "other.example"}
        with self.assertRaises(RuntimeError):
            self.n["health"](101, self.app, True)

    def test_gui_owner_must_be_distinct_and_exact_binary(self):
        self.n["socket"].socket = lambda *_: Connection()
        self.n["process_path"] = lambda _: self.path
        self.n["peer_pid"] = lambda _: 101
        with self.assertRaises(RuntimeError):
            self.n["gui_owner"](self.app, 101)
        self.n["peer_pid"] = lambda _: 102
        self.assertEqual(self.n["gui_owner"](self.app, 101), 102)
        self.n["process_path"] = lambda _: Path("/other/binary")
        with self.assertRaises(RuntimeError):
            self.n["gui_owner"](self.app, 101)

    def test_launch_time_rejects_nonfinite_bool_and_missing(self):
        for value in (None, False, float("nan"), float("inf"), -1, 206):
            self.n["desktop"].probe = lambda *_: {"running": True, "launchTime": value}
            with self.assertRaises(RuntimeError):
                self.n["launch_identity"]("helper", self.app, 101)
        self.n["desktop"].probe = lambda *_: {"running": True, "launchTime": 100.5}
        self.assertEqual(self.n["launch_identity"]("helper", self.app, 101), 100.5)

    def prepare_termination(self):
        ticks = iter(range(100))
        self.n["time"].monotonic = lambda: next(ticks)
        self.n["time"].sleep = lambda _: None
        self.n["process_path"] = lambda _: self.path
        self.n["launch_identity"] = lambda *_: 100
        self.n["no_connections"] = lambda *_: True
        self.n["desktop"].helper_request = lambda *_: {"running": True}
        self.signals = []
        def kill(pid, value):
            if value:
                self.signals.append((pid, value))
            elif any(item[1] == signal.SIGKILL for item in self.signals):
                raise ProcessLookupError
        self.n["os"].kill = kill

    def test_termination_rechecks_identity_and_count_before_each_signal(self):
        for changed in ("launch", "path", "connection"):
            self.prepare_termination()
            def kill(pid, value):
                if value:
                    self.signals.append((pid, value))
                    if changed == "launch":
                        self.n["launch_identity"] = lambda *_: 101
                    elif changed == "path":
                        self.n["process_path"] = lambda _: Path("/replacement")
                    else:
                        self.n["no_connections"] = lambda *_: False
            self.n["os"].kill = kill
            with self.assertRaises(RuntimeError):
                self.n["terminate_owned"]("helper", self.app, 102, 100, 101)
            self.assertEqual(self.signals, [(102, signal.SIGTERM)])

    def test_termination_waits_for_owned_process_exit(self):
        self.prepare_termination()
        self.n["terminate_owned"]("helper", self.app, 102, 100, 101)
        self.assertEqual(self.signals, [(102, signal.SIGTERM), (102, signal.SIGKILL)])
        self.prepare_termination()
        def kill(pid, value):
            if value:
                self.signals.append((pid, value))
            else:
                raise ProcessLookupError
        self.n["os"].kill = kill
        self.n["terminate_owned"]("helper", self.app, 102, 100, 101)
        self.assertEqual(self.signals, [(102, signal.SIGTERM)])

    def test_termination_rejects_authenticated_before_any_signal(self):
        self.prepare_termination()
        self.n["no_connections"] = lambda *_: False
        with self.assertRaises(RuntimeError):
            self.n["terminate_owned"]("helper", self.app, 102, 100, 101)
        self.assertEqual(self.signals, [])

    def test_termination_accepts_native_terminated_state_without_kill(self):
        self.prepare_termination()
        self.n["desktop"].helper_request = lambda *_: {"running": False}
        self.n["terminate_owned"]("helper", self.app, 102, 100, 101)
        self.assertEqual(self.signals, [(102, signal.SIGTERM)])

    def test_termination_fails_if_owned_process_never_exits(self):
        self.prepare_termination()
        self.n["os"].kill = lambda pid, value: self.signals.append((pid, value)) if value else None
        with self.assertRaisesRegex(RuntimeError, "did not exit"):
            self.n["terminate_owned"]("helper", self.app, 102, 100, 101)
        self.assertEqual(self.signals, [(102, signal.SIGTERM), (102, signal.SIGKILL)])

    def prepare_endpoint(self):
        endpoint = VirtualPath("/synthetic-ci/ipc", attributes(mode=stat.S_IFSOCK | 0o600))
        pid_file = VirtualPath("/synthetic-ci/ipc.pid", attributes(inode=2), b"101")
        endpoint.pid_file = pid_file
        self.n["os"].path = SimpleNamespace(lexists=lambda _: True)
        self.n["socket"].socket = lambda *_: Connection(ConnectionRefusedError())
        self.n["desktop"].bounded_read = lambda path, _: (path.data, path.lstat())
        return endpoint, pid_file

    def test_endpoint_cleanup_preserves_other_pid(self):
        endpoint, pid_file = self.prepare_endpoint()
        self.n["cleanup_endpoint"](endpoint, 102)
        self.assertFalse(endpoint.deleted)
        self.assertFalse(pid_file.deleted)

    def test_endpoint_cleanup_rejects_pid_link_or_replacement(self):
        endpoint, pid_file = self.prepare_endpoint()
        original = pid_file.lstat()
        self.n["desktop"].bounded_read = lambda *_: (b"101", original)
        pid_file.attributes = attributes(inode=3)
        with self.assertRaises(RuntimeError):
            self.n["cleanup_endpoint"](endpoint, 101)
        self.assertFalse(endpoint.deleted)
        self.assertFalse(pid_file.deleted)
        endpoint, pid_file = self.prepare_endpoint()
        def reject_link(*_):
            raise RuntimeError("Diagnostic file is not an owned regular file")
        self.n["desktop"].bounded_read = reject_link
        with self.assertRaises(RuntimeError):
            self.n["cleanup_endpoint"](endpoint, 101)
        self.assertFalse(endpoint.deleted)
        self.assertFalse(pid_file.deleted)

    def test_endpoint_cleanup_rejects_socket_replacement(self):
        endpoint, pid_file = self.prepare_endpoint()
        original = endpoint.lstat()
        def read_pid(*_):
            endpoint.attributes = attributes(inode=999, mode=stat.S_IFSOCK | 0o600)
            return b"101", pid_file.lstat()
        self.n["desktop"].bounded_read = read_pid
        with self.assertRaises(RuntimeError):
            self.n["cleanup_endpoint"](endpoint, 101)
        self.assertFalse(endpoint.deleted)
        self.assertFalse(pid_file.deleted)

    def test_endpoint_cleanup_removes_only_matching_owned_inactive_pair(self):
        endpoint, pid_file = self.prepare_endpoint()
        self.n["cleanup_endpoint"](endpoint, 101)
        self.assertTrue(endpoint.deleted)
        self.assertTrue(pid_file.deleted)
        endpoint, pid_file = self.prepare_endpoint()
        self.n["socket"].socket = lambda *_: Connection()
        with self.assertRaises(RuntimeError):
            self.n["cleanup_endpoint"](endpoint, 101)
        self.assertFalse(endpoint.deleted)
        self.assertFalse(pid_file.deleted)

    def test_log_marker_requires_fresh_nonprivate_appended_bytes(self):
        with tempfile.TemporaryDirectory(prefix="mixel-tray-source-log-") as temporary:
            path = Path(temporary) / "owned.log"
            path.write_bytes(self.n["TRAY_MARKER"] + b"\n")
            baseline = {path: path.lstat()}
            self.n["desktop"].scoped_files = lambda *_a, **_k: [path]
            self.n["desktop"].bounded_read = lambda value, _: (value.read_bytes(), value.lstat())
            self.assertFalse(self.n["new_tray_marker"](Path(temporary), baseline))
            path.write_bytes(path.read_bytes() + b"unrelated\n")
            self.assertFalse(self.n["new_tray_marker"](Path(temporary), baseline))
            path.write_bytes(path.read_bytes() + self.n["TRAY_MARKER"] + b"\n")
            self.assertTrue(self.n["new_tray_marker"](Path(temporary), baseline))
            self.n["desktop"].privacy_issue = lambda _: True
            with self.assertRaises(RuntimeError):
                self.n["new_tray_marker"](Path(temporary), baseline)

    def prepare_runtime(self, temporary, activated_time=100):
        app = Path(temporary) / "Mixel-Remote.app"
        (app / "Contents").mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "ch.mixel.remote", "CFBundleExecutable": "Mixel-Remote"}))
        for name in self.n["CRITICAL"]:
            path = app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic fixture")
        self.n["isolated_ci"] = lambda: None
        self.n["endpoint_inactive"] = lambda _: None
        self.n["pwd"].getpwuid = lambda _: SimpleNamespace(pw_dir=temporary)
        self.n["log_snapshot"] = lambda _: {}
        self.n["process_path"] = lambda _: app / "Contents/MacOS/Mixel-Remote"
        self.n["launch_identity"] = lambda _h, _a, pid: 100 if pid == 101 else 102
        self.n["desktop"].helper_request = lambda _h, action, *_: {"pid": 101, "launchTime": 100 if action == "tray" else activated_time}
        self.n["health"] = lambda *_: True
        self.n["new_tray_marker"] = lambda *_: True
        self.n["gui_owner"] = lambda *_: 102
        self.n["desktop"].probe = lambda *_: {"visible": True}
        self.n["wait"] = lambda _d, op, _p: op()
        self.n["no_connections"] = lambda *_: True
        self.terminated = []
        self.n["terminate_owned"] = lambda _h, _a, pid, *_: self.terminated.append(pid)
        self.n["cleanup_endpoint"] = lambda *_: None
        return app

    def test_runtime_requires_os_activation_retained_launch_time(self):
        with tempfile.TemporaryDirectory(prefix="mixel-tray-source-runtime-") as temporary:
            app = self.prepare_runtime(temporary, activated_time=101)
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
                self.n["runtime"](app, "synthetic helper", {})

    def test_runtime_orders_distinct_gui_before_server_cleanup_and_no_accept_claim(self):
        with tempfile.TemporaryDirectory(prefix="mixel-tray-source-runtime-") as temporary:
            app = self.prepare_runtime(temporary)
            facts = {"customerAcceptTested": False, "remoteDesktopConnected": False}
            with contextlib.redirect_stdout(io.StringIO()):
                self.n["runtime"](app, "synthetic helper", facts)
            self.assertEqual(self.terminated, [102, 101])
            self.assertTrue(facts["actualTrayDispatchObserved"])
            self.assertTrue(facts["visibleGuiAndAttendedV2"])
            self.assertTrue(facts["ownedProcessesStopped"])
            self.assertFalse(facts["customerAcceptTested"])
            self.assertFalse(facts["remoteDesktopConnected"])

    def test_existing_output_collision_writes_nothing(self):
        with tempfile.TemporaryDirectory(prefix="mixel-tray-source-main-") as temporary:
            base = Path(temporary).resolve()
            app = base / "app"
            app.mkdir()
            output = base / "existing"
            output.mkdir()
            sentinel = output / "tray-runtime-safe-facts.json"
            sentinel.write_bytes(b"owned before test")
            self.n["isolated_ci"] = lambda: None
            with mock.patch.object(sys, "argv", ["source-only", "--app", str(app), "--proofs", str(output)]):
                self.assertEqual(self.n["main"](), 1)
            self.assertEqual(sentinel.read_bytes(), b"owned before test")

    def test_new_owned_output_records_success_and_redacted_failure(self):
        with tempfile.TemporaryDirectory(prefix="mixel-tray-source-main-") as temporary:
            base = Path(temporary).resolve()
            app = base / "app"
            app.mkdir()
            self.n["isolated_ci"] = lambda: None
            self.n["desktop"].SWIFT = "synthetic helper"
            self.n["tray_swift"] = lambda value: value
            self.n["desktop"].compile_helper = lambda _: Path("/never-executed/helper")
            for passed in (True, False):
                output = base / ("pass" if passed else "fail")
                def runtime(_app, _helper, facts):
                    if not passed:
                        raise RuntimeError("unapproved raw exception must never appear")
                    facts["ownedProcessesStopped"] = True
                self.n["runtime"] = runtime
                with mock.patch.object(sys, "argv", ["source-only", "--app", str(app), "--proofs", str(output)]):
                    self.assertEqual(self.n["main"](), 0 if passed else 1)
                data = (output / "tray-runtime-safe-facts.json").read_text()
                facts = json.loads(data)
                self.assertIs(facts["passed"], passed)
                self.assertFalse(facts["customerAcceptTested"])
                self.assertFalse(facts["remoteDesktopConnected"])
                self.assertNotIn("unapproved raw exception", data)
                if not passed:
                    self.assertEqual(facts["failure"], "fixed failure")



    def prepare_primary_diagnostic_main(self):
        # Extract only the actual bounded formatter; never import the native gate.
        source = SOURCE.with_name("test-support-launch-macos.py")
        parsed = ast.parse(source.read_text(encoding="utf-8"))
        formatter = [node for node in parsed.body
                     if isinstance(node, ast.FunctionDef) and node.name == "safe_error"]
        self.assertEqual(len(formatter), 1)
        private = {"URI": "mixelremote://synthetic-private-invite",
                   "TOKEN": "synthetic-private-invite", "API_KEY": "synthetic-private-key"}
        values = {"unix": SimpleNamespace(**private),
                  "WARM_URI": "mixelremote://synthetic-private-warm",
                  "WARM_TOKEN": "synthetic-private-warm"}
        exec(compile(ast.Module(body=formatter, type_ignores=[]), str(source), "exec"), values)
        self.n["desktop"].safe_error = values["safe_error"]
        self.n["desktop"].SWIFT = "synthetic helper"
        self.n["tray_swift"] = lambda value: value
        self.n["desktop"].compile_helper = lambda _: Path("/never-executed/helper")
        return private

    def prepare_real_active_endpoint_refusal(self, endpoint_inactive, cleanup_endpoint):
        # Initial IPC preflight is inactive; the unchanged real cleanup guard
        # later observes a virtual active owned socket and must refuse removal.
        self.n["unix"].IPC_PATH = VirtualPath("/synthetic-ci/ipc", attributes(mode=stat.S_IFSOCK | 0o600))
        self.n["URL_SOCKET"] = VirtualPath("/synthetic-ci/ipc_url", attributes(mode=stat.S_IFSOCK | 0o600))
        calls = []
        def lexists(path):
            calls.append(str(path))
            return len(calls) > 2
        self.n["os"].path = SimpleNamespace(lexists=lexists)
        self.n["socket"].socket = lambda *_: Connection()
        self.n["endpoint_inactive"] = endpoint_inactive
        self.n["cleanup_endpoint"] = cleanup_endpoint
        return calls

    def assert_no_private_diagnostic(self, private, facts, stderr):
        encoded = json.dumps(facts)
        for value in private.values():
            self.assertNotIn(value, encoded)
            self.assertNotIn(value, stderr)
        self.assertFalse(facts["customerAcceptTested"])
        self.assertFalse(facts["remoteDesktopConnected"])

    def test_primary_failure_and_active_cleanup_refusal_remain_visible_to_main(self):
        phases = (("os-url-activation", None),
                  ("tray-opened-dispatch", "Actual Tao tray Opened dispatch"),
                  ("customer-gui-visible", "Visible customer GUI"),
                  ("attended-service-readiness", "Tray service attended readiness"))
        errors = (RuntimeError, ValueError, OSError, subprocess.SubprocessError)
        for phase, description in phases:
            for error_type in errors:
                with self.subTest(phase=phase, error=error_type.__name__), tempfile.TemporaryDirectory(prefix="mixel-tray-primary-source-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
                    self.n = extracted_namespace(SOURCE)
                    original_endpoint = self.n["endpoint_inactive"]
                    original_cleanup = self.n["cleanup_endpoint"]
                    app = self.prepare_runtime(temporary)
                    private = self.prepare_primary_diagnostic_main()
                    raw = "Synthetic primary " + " ".join(private.values()) + " " + ("x" * 1100)
                    primary = error_type(5, raw) if error_type is OSError else error_type(raw)
                    helper_request = self.n["desktop"].helper_request
                    def request(helper, action, *args):
                        if description is None and action == "launch":
                            raise primary
                        return helper_request(helper, action, *args)
                    self.n["desktop"].helper_request = request
                    def wait(label, operation, pid):
                        if label == description:
                            raise primary
                        return operation()
                    self.n["wait"] = wait
                    calls = self.prepare_real_active_endpoint_refusal(original_endpoint, original_cleanup)
                    output = Path(temporary).resolve() / "owned-proof"
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout), mock.patch.object(sys, "argv", ["source-only", "--app", str(app), "--proofs", str(output)]):
                        result = self.n["main"]()
                    facts = json.loads((output / "tray-runtime-safe-facts.json").read_text())
                    stderr = self.n["sys"].stderr.getvalue()
                    self.assertEqual(result, 1)
                    self.assertFalse(facts["passed"])
                    self.assertEqual(facts["runtimeFailure"]["phase"], phase)
                    expected_primary = self.n["desktop"].safe_error(primary)
                    self.assertEqual(facts["runtimeFailure"]["reason"], expected_primary)
                    self.assertLessEqual(len(expected_primary), 1000)
                    self.assertIn("FAIL: tray runtime (" + phase + "): " + expected_primary, stderr)
                    self.assertEqual(facts["failure"], "A Mixel IPC endpoint is already active; refusing tray fixture")
                    self.assertIn("FAIL: " + facts["failure"], stderr)
                    self.assertEqual(self.terminated, [102, 101])
                    self.assertGreaterEqual(len(calls), 3)
                    self.assertFalse(self.n["URL_SOCKET"].deleted)
                    self.assertFalse(self.n["unix"].IPC_PATH.deleted)
                    self.assertNotIn("confirms attended-runtime-v2", stdout.getvalue())
                    self.assert_no_private_diagnostic(private, facts, stderr)

    def test_primary_exception_identity_is_preserved_when_cleanup_succeeds(self):
        with tempfile.TemporaryDirectory(prefix="mixel-tray-primary-source-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            app = self.prepare_runtime(temporary)
            private = self.prepare_primary_diagnostic_main()
            primary = RuntimeError("Synthetic activation " + private["URI"])
            helper_request = self.n["desktop"].helper_request
            def request(helper, action, *args):
                if action == "launch":
                    raise primary
                return helper_request(helper, action, *args)
            self.n["desktop"].helper_request = request
            facts = {"customerAcceptTested": False, "remoteDesktopConnected": False,
                     "actualTrayDispatchObserved": False, "visibleGuiAndAttendedV2": False}
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError) as raised:
                self.n["runtime"](app, "synthetic helper", facts)
            self.assertIs(raised.exception, primary)
            self.assertEqual(facts["runtimeFailure"], {"phase": "os-url-activation", "reason": "Synthetic activation [support URI]"})
            self.assertTrue(facts["ownedProcessesStopped"])
            self.assertFalse(facts["actualTrayDispatchObserved"])
            self.assertFalse(facts["visibleGuiAndAttendedV2"])
            self.assert_no_private_diagnostic(private, facts, self.n["sys"].stderr.getvalue())

    def test_primary_failure_preserves_authenticated_and_unknown_cleanup_refusals(self):
        for refusal in ("authenticated", "unknown"):
            with self.subTest(refusal=refusal), tempfile.TemporaryDirectory(prefix="mixel-tray-primary-source-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
                self.n = extracted_namespace(SOURCE)
                app = self.prepare_runtime(temporary)
                private = self.prepare_primary_diagnostic_main()
                primary = RuntimeError("Synthetic activation " + private["URI"])
                helper_request = self.n["desktop"].helper_request
                def request(helper, action, *args):
                    if action == "launch":
                        raise primary
                    return helper_request(helper, action, *args)
                self.n["desktop"].helper_request = request
                def no_connections(*args):
                    if refusal == "unknown":
                        raise RuntimeError("Unknown owned IPC " + private["API_KEY"])
                    return False
                self.n["no_connections"] = no_connections
                output = Path(temporary).resolve() / "owned-proof"
                with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(sys, "argv", ["source-only", "--app", str(app), "--proofs", str(output)]):
                    self.assertEqual(self.n["main"](), 1)
                facts = json.loads((output / "tray-runtime-safe-facts.json").read_text())
                stderr = self.n["sys"].stderr.getvalue()
                self.assertFalse(facts["passed"])
                self.assertEqual(facts["runtimeFailure"]["reason"], "Synthetic activation [support URI]")
                self.assertIn(facts["runtimeFailure"]["reason"], stderr)
                expected = "Tray fixture cleanup refused an authenticated connection" if refusal == "authenticated" else "Unknown owned IPC [API key]"
                self.assertEqual(facts["failure"], expected)
                self.assertIn(expected, stderr)
                self.assertEqual(self.terminated, [])
                self.assertFalse(facts["ownedProcessesStopped"])
                self.assert_no_private_diagnostic(private, facts, stderr)

    def test_cleanup_only_refusal_is_fatal_without_inventing_primary_failure(self):
        with tempfile.TemporaryDirectory(prefix="mixel-tray-primary-source-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            original_endpoint = self.n["endpoint_inactive"]
            original_cleanup = self.n["cleanup_endpoint"]
            app = self.prepare_runtime(temporary)
            private = self.prepare_primary_diagnostic_main()
            self.prepare_real_active_endpoint_refusal(original_endpoint, original_cleanup)
            output = Path(temporary).resolve() / "owned-proof"
            with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(sys, "argv", ["source-only", "--app", str(app), "--proofs", str(output)]):
                self.assertEqual(self.n["main"](), 1)
            facts = json.loads((output / "tray-runtime-safe-facts.json").read_text())
            stderr = self.n["sys"].stderr.getvalue()
            self.assertFalse(facts["passed"])
            self.assertNotIn("runtimeFailure", facts)
            self.assertNotIn("FAIL: tray runtime", stderr)
            self.assertEqual(facts["failure"], "A Mixel IPC endpoint is already active; refusing tray fixture")
            self.assertIn(facts["failure"], stderr)
            self.assertFalse(self.n["URL_SOCKET"].deleted)
            self.assertFalse(self.n["unix"].IPC_PATH.deleted)
            self.assert_no_private_diagnostic(private, facts, stderr)

    def test_endpoint_drain_first_failure_attempts_incoming_and_retains_error(self):
        for error_type in (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            with self.subTest(error=error_type.__name__), tempfile.TemporaryDirectory(prefix="mixel-tray-endpoint-drain-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
                self.n = extracted_namespace(SOURCE)
                app = self.prepare_runtime(temporary)
                first = error_type("first endpoint failed")
                calls = []
                def cleanup(endpoint, pid):
                    calls.append((endpoint, pid))
                    if endpoint == self.n["URL_SOCKET"]:
                        raise first
                self.n["cleanup_endpoint"] = cleanup
                facts = {}
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(error_type) as raised:
                    self.n["runtime"](app, "synthetic helper", facts)
                self.assertIs(raised.exception, first)
                self.assertEqual(calls, [(self.n["URL_SOCKET"], 102), (self.n["unix"].IPC_PATH, 101)])
                self.assertEqual(self.terminated, [102, 101])
                self.assertTrue(facts["ownedProcessesStopped"])
                self.assertEqual(facts["endpointCleanupFailures"], [{"endpoint": "url", "reason": "fixed failure"}])

    def test_endpoint_drain_two_failures_retains_first_and_records_both(self):
        errors = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)
        for first_type in errors:
            for second_type in errors:
                with self.subTest(first=first_type.__name__, second=second_type.__name__), tempfile.TemporaryDirectory(prefix="mixel-tray-endpoint-drain-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
                    self.n = extracted_namespace(SOURCE)
                    app = self.prepare_runtime(temporary)
                    first, second = first_type("first endpoint failed"), second_type("second endpoint failed")
                    self.n["desktop"].safe_error = lambda error: "first fixed failure" if error is first else "second fixed failure"
                    calls = []
                    def cleanup(endpoint, pid):
                        calls.append((endpoint, pid))
                        raise first if endpoint == self.n["URL_SOCKET"] else second
                    self.n["cleanup_endpoint"] = cleanup
                    facts = {}
                    with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(first_type) as raised:
                        self.n["runtime"](app, "synthetic helper", facts)
                    self.assertIs(raised.exception, first)
                    self.assertEqual(calls, [(self.n["URL_SOCKET"], 102), (self.n["unix"].IPC_PATH, 101)])
                    self.assertEqual(facts["endpointCleanupFailures"], [{"endpoint": "url", "reason": "first fixed failure"}, {"endpoint": "incoming", "reason": "second fixed failure"}])
                    self.assertNotIn("runtimeFailure", facts)

    def test_endpoint_drain_second_failure_is_fatal(self):
        for error_type in (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            with self.subTest(error=error_type.__name__), tempfile.TemporaryDirectory(prefix="mixel-tray-endpoint-drain-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
                self.n = extracted_namespace(SOURCE)
                app = self.prepare_runtime(temporary)
                second = error_type("second endpoint failed")
                calls = []
                def cleanup(endpoint, pid):
                    calls.append((endpoint, pid))
                    if endpoint == self.n["unix"].IPC_PATH:
                        raise second
                self.n["cleanup_endpoint"] = cleanup
                facts = {}
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(error_type) as raised:
                    self.n["runtime"](app, "synthetic helper", facts)
                self.assertIs(raised.exception, second)
                self.assertEqual(calls, [(self.n["URL_SOCKET"], 102), (self.n["unix"].IPC_PATH, 101)])
                self.assertEqual(facts["endpointCleanupFailures"], [{"endpoint": "incoming", "reason": "fixed failure"}])

    def test_endpoint_drain_success_conserves_order_and_existing_facts(self):
        with tempfile.TemporaryDirectory(prefix="mixel-tray-endpoint-drain-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            app = self.prepare_runtime(temporary)
            calls = []
            self.n["cleanup_endpoint"] = lambda endpoint, pid: calls.append((endpoint, pid))
            facts = {"customerAcceptTested": False, "remoteDesktopConnected": False}
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertIsNone(self.n["runtime"](app, "synthetic helper", facts))
            self.assertEqual(calls, [(self.n["URL_SOCKET"], 102), (self.n["unix"].IPC_PATH, 101)])
            self.assertEqual(self.terminated, [102, 101])
            self.assertEqual(set(facts), {"customerAcceptTested", "remoteDesktopConnected", "criticalHashes", "trayPid", "actualTrayDispatchObserved", "guiPid", "visibleGuiAndAttendedV2", "ownedProcessesStopped"})
            self.assertTrue(facts["ownedProcessesStopped"])
            self.assertNotIn("endpointCleanupFailures", facts)
            self.assertIn("confirms attended-runtime-v2", output.getvalue())

    def test_endpoint_drain_primary_fact_survives_both_failures(self):
        with tempfile.TemporaryDirectory(prefix="mixel-tray-endpoint-drain-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            app = self.prepare_runtime(temporary)
            private = self.prepare_primary_diagnostic_main()
            primary = RuntimeError("Synthetic primary " + private["URI"])
            first = RuntimeError("Synthetic URL cleanup " + private["API_KEY"])
            second = RuntimeError("Synthetic incoming cleanup " + private["TOKEN"])
            self.n["new_tray_marker"] = lambda *_: (_ for _ in ()).throw(primary)
            calls = []
            def cleanup(endpoint, pid):
                calls.append((endpoint, pid))
                raise first if endpoint == self.n["URL_SOCKET"] else second
            self.n["cleanup_endpoint"] = cleanup
            facts = {"customerAcceptTested": False, "remoteDesktopConnected": False}
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError) as raised:
                self.n["runtime"](app, "synthetic helper", facts)
            self.assertIs(raised.exception, first)
            self.assertEqual(facts["runtimeFailure"], {"phase": "tray-opened-dispatch", "reason": "Synthetic primary [support URI]"})
            self.assertEqual(facts["endpointCleanupFailures"], [{"endpoint": "url", "reason": "Synthetic URL cleanup [API key]"}, {"endpoint": "incoming", "reason": "Synthetic incoming cleanup [invite]"}])
            self.assertEqual(len(calls), 2)
            self.assert_no_private_diagnostic(private, facts, self.n["sys"].stderr.getvalue())

    def test_endpoint_drain_does_not_cross_auth_or_termination_refusal(self):
        for refusal in ("authenticated", "unknown", "gui-termination", "server-termination"):
            with self.subTest(refusal=refusal), tempfile.TemporaryDirectory(prefix="mixel-tray-endpoint-drain-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
                self.n = extracted_namespace(SOURCE)
                app = self.prepare_runtime(temporary)
                calls = []
                self.n["cleanup_endpoint"] = lambda *args: calls.append(args)
                sentinel = RuntimeError("owned process refusal")
                if refusal == "authenticated":
                    self.n["no_connections"] = lambda *_: False
                elif refusal == "unknown":
                    self.n["no_connections"] = lambda *_: (_ for _ in ()).throw(sentinel)
                else:
                    def terminate(_h, _a, pid, *_):
                        self.terminated.append(pid)
                        if pid == (102 if refusal == "gui-termination" else 101):
                            raise sentinel
                    self.n["terminate_owned"] = terminate
                facts = {}
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError) as raised:
                    self.n["runtime"](app, "synthetic helper", facts)
                if refusal != "authenticated":
                    self.assertIs(raised.exception, sentinel)
                self.assertEqual(calls, [])
                self.assertNotIn("ownedProcessesStopped", facts)
                self.assertNotIn("endpointCleanupFailures", facts)

    def test_endpoint_drain_real_identity_guards_stop_before_attempts(self):
        for changed in ("path", "launch"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory(prefix="mixel-tray-endpoint-drain-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
                self.n = extracted_namespace(SOURCE)
                terminate = self.n["terminate_owned"]
                app = self.prepare_runtime(temporary)
                self.n["terminate_owned"] = terminate
                cleanup_started = []
                self.n["no_connections"] = lambda *_: cleanup_started.append(True) or True
                if changed == "path":
                    self.n["process_path"] = lambda _: Path("/replacement") if cleanup_started else app / "Contents/MacOS/Mixel-Remote"
                else:
                    self.n["launch_identity"] = lambda _h, _a, pid: 999 if cleanup_started else (100 if pid == 101 else 102)
                calls = []
                self.n["cleanup_endpoint"] = lambda *args: calls.append(args)
                facts = {}
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "cleanup process identity changed"):
                    self.n["runtime"](app, "synthetic helper", facts)
                self.assertEqual(calls, [])
                self.assertNotIn("ownedProcessesStopped", facts)

    def test_endpoint_drain_unexpected_failure_stays_immediate(self):
        with tempfile.TemporaryDirectory(prefix="mixel-tray-endpoint-drain-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            app = self.prepare_runtime(temporary)
            error = AssertionError("unexpected fixture programmer error")
            calls = []
            def cleanup(*args):
                calls.append(args)
                raise error
            self.n["cleanup_endpoint"] = cleanup
            facts = {}
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(AssertionError) as raised:
                self.n["runtime"](app, "synthetic helper", facts)
            self.assertIs(raised.exception, error)
            self.assertEqual(calls, [(self.n["URL_SOCKET"], 102)])
            self.assertNotIn("endpointCleanupFailures", facts)



def main():
    global SOURCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    args = parser.parse_args()
    SOURCE = args.source.resolve(strict=True)
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    print("Source-only Mac tray OS gate SHA256=" + digest, flush=True)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TrayControls)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
