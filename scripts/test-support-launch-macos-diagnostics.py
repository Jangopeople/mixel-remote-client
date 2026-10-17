#!/usr/bin/env python3
"""Execute bounded filesystem and exact launch-helper controls without a GUI.

These are diagnostics/ownership tests. They never launch a customer app, request
TCC access, inspect another process, or qualify screen control or a release.
"""
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import signal
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mixel_macos_launch", ROOT / "scripts/test-support-launch-macos.py")
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)
VERIFY_IPC_OWNER = launch.verify_ipc_owner
PID = 12345
BASE = datetime(2026, 10, 12, 9, 45, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.value = 0.0

    def sleep(self, duration):
        self.value += duration

    def now(self):
        return BASE + timedelta(seconds=self.value)


class SyntheticSmokeCleanupTests(unittest.TestCase):
    def test_exact_fixture_exits_without_querying_unrelated_endpoint(self):
        with patch.object(launch, "probe", return_value={"running": True, "launchTime": 123.0}), \
             patch.object(launch.os, "kill", side_effect=[None, ProcessLookupError()]) as kill, \
             patch.object(launch, "verify_ipc_owner", side_effect=AssertionError("unrelated endpoint")), \
             patch.object(launch.unix, "query", side_effect=AssertionError("live IPC")):
            launch.cleanup_smoke_fixture(Path("helper"), 123, Path("synthetic.app"), 123.0)
            self.assertEqual(kill.call_args_list, [unittest.mock.call(123, signal.SIGTERM), unittest.mock.call(123, 0)])

    def test_changed_birth_and_invalid_identity_refuse_signal(self):
        for birth in (124.0, None):
            with patch.object(launch, "probe", return_value={"running": True, "launchTime": birth}), patch.object(launch.os, "kill") as kill:
                with self.assertRaises(RuntimeError):
                    launch.cleanup_smoke_fixture(Path("helper"), 123, Path("synthetic.app"), 123.0)
                kill.assert_not_called()
        for pid, birth in ((0, 123.0), (True, 123.0), (123, 0), (123, float("nan"))):
            with patch.object(launch, "probe") as probe, patch.object(launch.os, "kill") as kill:
                with self.assertRaises(RuntimeError):
                    launch.cleanup_smoke_fixture(Path("helper"), pid, Path("synthetic.app"), birth)
                probe.assert_not_called(); kill.assert_not_called()

    def test_exit_timeout_is_fatal_without_a_second_signal_or_ipc(self):
        with patch.object(launch, "probe", return_value={"running": True, "launchTime": 123.0}), \
             patch.object(launch.os, "kill") as kill, \
             patch.object(launch.time, "monotonic", side_effect=[0, 0, 6]), \
             patch.object(launch.time, "sleep"), \
             patch.object(launch.unix, "query", side_effect=AssertionError("live IPC")):
            with self.assertRaisesRegex(RuntimeError, "did not exit"):
                launch.cleanup_smoke_fixture(Path("helper"), 123, Path("synthetic.app"), 123.0)
            self.assertEqual(kill.call_args_list, [unittest.mock.call(123, signal.SIGTERM), unittest.mock.call(123, 0)])

    def test_self_test_finally_uses_only_exact_fixture_cleanup(self):
        import ast
        tree=ast.parse((ROOT / "scripts/test-support-launch-macos.py").read_text(encoding="utf-8"))
        body=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=="self_test")
        final=next(node for node in body.body if isinstance(node,ast.Try)).finalbody
        text=ast.unparse(ast.Module(body=final,type_ignores=[]))
        self.assertIn("cleanup_smoke_fixture(helper, pid, app, launch_time)",text)
        self.assertNotIn("endpoint",text);self.assertNotIn("unix.query",text)


class OwnedFixtures(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mxd-", dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = self.root / "Mixel-Remote.app"
        self.binary = self.app / "Contents/MacOS/Mixel-Remote"
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b"source-only synthetic executable, never executed")
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleExecutable": "Mixel-Remote", "CFBundleIdentifier": "ch.mixel.remote",
            "CFBundleURLTypes": [{"CFBundleURLSchemes": ["mixel-remote"]}]}))
        self.home = self.root / "home"
        self.logs = self.home / "Library/Logs/Mixel-Remote"
        self.reports = self.home / "Library/Logs/DiagnosticReports"
        self.reports.mkdir(parents=True)
        self.output = self.root / "evidence"
        self.clock = Clock()
        for target, replacement in (("utc_now", self.clock.now), ("time.monotonic", lambda: self.clock.value),
                                    ("time.sleep", self.clock.sleep)):
            mocked = patch.object(launch, target, replacement) if "." not in target else patch.object(launch.time, target.split(".")[1], replacement)
            mocked.start()
            self.addCleanup(mocked.stop)

        owner = patch.object(launch,"verify_ipc_owner")
        owner.start();self.addCleanup(owner.stop)
        query = patch.object(launch.unix,"query",side_effect=lambda name,args=None: {
            "VideoConnCount":0,"OnlineStatus":[1,True],"Config":["mixel-support-invite-attended",""]}[name])
        query.start();self.addCleanup(query.stop)

    def evidence(self):
        return launch.RuntimeEvidence(self.app, self.output, self.logs, self.reports)

    def failed(self):
        evidence = self.evidence()
        evidence.pid, evidence.launch_time = PID, BASE.timestamp()
        evidence.phase = "warm-visible-window"
        self.clock.value = 10
        evidence.failure(RuntimeError("Warm support URL customer window: customer app exited"))
        return evidence

    def ips(self, **changes):
        report = {"pid": PID, "procPath": str(self.binary), "procName": "Mixel-Remote",
                  "procLaunch": BASE.isoformat(), "captureTime": (BASE + timedelta(seconds=9)).isoformat(),
                  "bundleInfo": {"CFBundleIdentifier": "ch.mixel.remote"},
                  "exception": {"type": "EXC_CRASH", "signal": "SIGABRT"},
                  "termination": {"code": 6, "namespace": "SIGNAL"}, "faultingThread": 0,
                  "threads": [{"triggered": True, "threadState": {"withheldRegister": "private synthetic register"},
                               "frames": [{"imageIndex": 0, "imageOffset": 12, "symbol": "syntheticCrash"}]}],
                  "usedImages": [{"name": "Mixel-Remote", "path": str(self.binary), "arch": "arm64"},
                                 {"name": "foreign.dylib", "path": "/Users/private-other/foreign.dylib"}],
                  "crashReporterKey": "synthetic-private-device-identifier"}
        report.update(changes)
        return (json.dumps({"bug_type": "309"}) + "\n" + json.dumps(report) + "\n").encode()

    def write_report(self, data, name="Mixel-Remote_2026-10-12.ips"):
        path = self.reports / name
        path.write_bytes(data)
        return path

    def endpoint(self, marker=None):
        endpoint = self.root / "ipc-owned" / "ipc"
        endpoint.parent.mkdir(exist_ok=True)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(connection.close)
        connection.bind(str(endpoint))
        endpoint.with_suffix(".pid").write_bytes(marker if marker is not None else str(PID).encode())
        return endpoint


class BoundedFileTests(OwnedFixtures):
    def test_actual_regular_bytes_and_digest_are_bound(self):
        data, attributes = launch.bounded_read(self.binary, 1024)
        self.assertEqual(data, self.binary.read_bytes())
        self.assertEqual(attributes.st_size, len(data))
        self.assertEqual(launch.bounded_digest(self.binary, 1024)["sha256"], hashlib.sha256(data).hexdigest())

    def test_symlink_hardlink_directory_and_size_are_refused(self):
        link = self.root / "linked"
        link.symlink_to(self.binary)
        hard = self.root / "hardlinked"
        os.link(self.binary, hard)
        for path, limit in ((link, 1024), (hard, 1024), (self.app, 1024), (self.binary, 1)):
            with self.subTest(path=path.name, limit=limit), self.assertRaises(RuntimeError):
                launch.bounded_read(path, limit)

    def test_foreign_owner_and_changed_open_are_refused(self):
        with patch.object(launch.os, "getuid", return_value=os.getuid() + 1000), self.assertRaises(RuntimeError):
            launch.bounded_read(self.binary, 1024)
        actual = launch.file_identity
        count = 0
        def changed(value):
            nonlocal count
            count += 1
            result = actual(value)
            return result + ("changed",) if count == 1 else result
        with patch.object(launch, "file_identity", changed), self.assertRaisesRegex(RuntimeError, "changed"):
            launch.bounded_digest(self.binary, 1024)

    def test_existing_output_or_symlink_parent_cannot_be_overwritten(self):
        self.output.mkdir()
        (self.output / "keep").write_bytes(b"untouched")
        with self.assertRaises(FileExistsError):
            self.evidence()
        self.assertEqual((self.output / "keep").read_bytes(), b"untouched")
        link = self.root / "parent-link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "canonical"):
            launch.RuntimeEvidence(self.app, link / "new", self.logs, self.reports)

    def test_log_directory_symlink_depth_and_entry_bounds(self):
        self.logs.mkdir()
        (self.logs / "foreign").symlink_to(self.reports, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symbolic"):
            self.evidence()
        (self.logs / "foreign").unlink()
        with patch.object(launch, "MAX_FILES", 1):
            (self.logs / "one").write_bytes(b"one")
            (self.logs / "two").write_bytes(b"two")
            with self.assertRaisesRegex(RuntimeError, "entry bound"):
                self.evidence()

    def test_archive_exact_path_hash_and_symlink_refusal(self):
        archive = self.root / "signed-stapled-diagnostic-app.zip"
        archive.write_bytes(b"synthetic archive, never launched")
        evidence = launch.RuntimeEvidence(self.app, self.output, self.logs, self.reports, archive)
        self.assertEqual(evidence.app_archive["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        archive.unlink()
        archive.symlink_to(self.binary)
        with self.assertRaises(RuntimeError):
            launch.bounded_digest(archive, 1024)


class CrashReportTests(OwnedFixtures):
    def test_matching_ips_preserves_original_identity_hash_and_safe_frames(self):
        evidence = self.failed()
        raw = self.ips()
        self.write_report(raw)
        evidence.collect_reports()
        evidence.collect_reports()
        self.assertEqual(len(evidence.reports), 1)
        record = evidence.reports[0]
        self.assertEqual(record["sha256"], hashlib.sha256(raw).hexdigest())
        safe = json.loads((self.output / record["retained_file"]).read_bytes())
        self.assertEqual(safe["original_identity"]["pid"], PID)
        self.assertEqual(safe["original_identity"]["procPath"], str(self.binary))
        self.assertEqual(safe["original_identity"]["procLaunch"], BASE.isoformat())
        self.assertFalse(safe["original_bytes_preserved"])
        exported = json.dumps(safe)
        for forbidden in ("private-other", "private-device", "withheldRegister", "private synthetic register"):
            self.assertNotIn(forbidden, exported)
        self.assertEqual(safe["details"]["crashedThreads"][0]["frames"][0]["symbol"], "syntheticCrash")

    def test_old_reports_and_unrelated_names_are_not_read(self):
        self.write_report(self.ips())
        evidence = self.failed()
        other = self.write_report(b"unrelated private bytes", "OtherApplication_2026.ips")
        with patch.object(launch, "bounded_read", side_effect=AssertionError("No old/foreign bytes may be read")):
            evidence.collect_reports()
        self.assertEqual(evidence.reports, [])
        self.assertTrue(other.exists())

    def test_pid_bundle_path_name_time_and_launch_mismatches_are_refused(self):
        evidence = self.failed()
        cases = ({"pid": PID + 1}, {"pid": True}, {"procPath": str(self.binary) + ".other"},
                 {"procPath": str(self.binary.parent) + "/./Mixel-Remote"}, {"procName": "other"},
                 {"bundleInfo": {"CFBundleIdentifier": "other.app"}},
                 {"captureTime": (BASE - timedelta(seconds=10)).isoformat()},
                 {"captureTime": (BASE + timedelta(seconds=100)).isoformat()},
                 {"procLaunch": (BASE - timedelta(minutes=5)).isoformat()}, {"procLaunch": None},
                 {"captureTime": "malformed"}, {"captureTime": "2026-10-12T09:45:09"})
        for index, change in enumerate(cases):
            self.write_report(self.ips(**change), f"Mixel-Remote_case-{index}.ips")
        evidence.collect_reports()
        self.assertEqual(evidence.reports, [])
        self.assertEqual(len(evidence.errors), len(cases))
        self.assertEqual(list(self.output.iterdir()), [])

    def test_malformed_duplicate_metadata_oversize_and_report_symlink_refused(self):
        evidence = self.failed()
        self.write_report(b"not JSON", "Mixel-Remote_invalid.ips")
        self.write_report(self.ips().replace(b'"bug_type": "309"', b'"bug_type": "211"'), "Mixel-Remote_wrong-type.ips")
        self.write_report(self.ips().replace(b'"pid": 12345', b'"pid": 12345, "pid": 12345'), "Mixel-Remote_duplicate.ips")
        self.write_report(b"x" * (launch.MAX_REPORT + 1), "Mixel-Remote_oversized.ips")
        (self.reports / "Mixel-Remote_link.ips").symlink_to(self.binary)
        evidence.collect_reports()
        self.assertEqual(evidence.reports, [])
        self.assertEqual(len(evidence.errors), 5)

    def test_matching_legacy_crash_records_launch_time_absence_honestly(self):
        evidence = self.failed()
        data = (f"Process: Mixel-Remote [{PID}]\nPath: {self.binary}\nIdentifier: ch.mixel.remote\n"
                "Date/Time: 2026-10-12 09:45:09.000 +0000\nException Type: EXC_CRASH\n"
                "Private unrelated text: omitted\n").encode()
        self.write_report(data, "Mixel-Remote_legacy.crash")
        evidence.collect_reports()
        self.assertEqual(len(evidence.reports), 1)
        self.assertIsNone(evidence.reports[0]["identity"]["procLaunch"])
        self.assertFalse(evidence.reports[0]["launch_time_recorded"])
        retained = (self.output / evidence.reports[0]["retained_file"]).read_text()
        self.assertNotIn("Private unrelated text", retained)
        self.assertIn("EXC_CRASH", retained)

    def test_credential_report_withheld_without_exporting_private_values(self):
        evidence = self.failed()
        self.write_report(self.ips(privateMessage="Authorization: Bearer synthetic-private-value"))
        evidence.collect_reports()
        self.assertEqual(evidence.reports[0]["withheld"], "bearer-or-credential-pattern")
        self.assertEqual(evidence.reports[0]["identity"]["pid"], PID)
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertNotIn("synthetic-private-value", json.dumps(evidence.finish()))

    def test_total_report_bound_retains_only_hash_and_identity(self):
        evidence = self.failed()
        self.write_report(self.ips())
        with patch.object(launch, "MAX_DIAGNOSTIC_BYTES", 1):
            evidence.collect_reports()
        self.assertEqual(evidence.reports[0]["withheld"], "total-byte-bound")
        self.assertEqual(list(self.output.iterdir()), [])


class LogAndTimelineTests(OwnedFixtures):
    def test_only_actual_new_log_delta_exported_and_hashed(self):
        self.logs.mkdir()
        path = self.logs / "Mixel-Remote.log"
        old = (launch.unix.TOKEN + " existing old bytes\n").encode()
        path.write_bytes(old)
        evidence = self.failed()
        delta = b"synthetic new app failure line\n"
        with path.open("ab") as destination:
            destination.write(delta)
        evidence.collect_logs()
        record = evidence.logs[0]
        self.assertEqual(record["offset"], len(old))
        self.assertEqual(record["sha256"], hashlib.sha256(delta).hexdigest())
        self.assertEqual((self.output / record["retained_file"]).read_bytes(), delta)
        self.assertTrue(evidence.privacy_ok)

    def test_invite_key_uri_and_bearer_are_withheld(self):
        evidence = self.failed()
        self.logs.mkdir()
        values = (launch.unix.TOKEN, launch.unix.API_KEY, launch.unix.URI,
                  "Authorization: Bearer synthetic-private", "password=synthetic-private",
                  '{"api_key": "synthetic-private"}', '{"password":"synthetic-private"}')
        # Credential-shaped fixtures belong in memory. Exercise the real owned
        # bounded read with same-sized zero-filled files, then supply each
        # public invalid payload only at that read boundary. The real collector
        # and privacy filter below are unchanged.
        payloads = {}
        for index, value in enumerate(values):
            path = self.logs / f"owned-{index}.log"
            payloads[path] = value.encode("utf-8")
            path.write_bytes(bytes(len(payloads[path])))
        bounded_read = launch.bounded_read
        reads = []

        def read_synthetic_log(path, limit):
            data, attributes = bounded_read(path, limit)
            self.assertEqual(data, bytes(len(payloads[path])))
            reads.append(path)
            return payloads[path], attributes

        with patch.object(launch, "bounded_read", side_effect=read_synthetic_log):
            evidence.collect_logs()
        self.assertEqual(reads, sorted(payloads))
        self.assertTrue(all(path.read_bytes() == bytes(len(payload)) for path, payload in payloads.items()))
        self.assertFalse(evidence.privacy_ok)
        self.assertEqual(len(evidence.logs), len(values))
        self.assertTrue(all("withheld" in item for item in evidence.logs))
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertNotIn("synthetic-private", json.dumps(evidence.finish()))

    def test_changed_and_truncated_logs_start_at_zero_not_stale_offsets(self):
        self.logs.mkdir()
        path = self.logs / "owned.log"
        path.write_bytes(b"original longer log\n")
        evidence = self.failed()
        path.write_bytes(b"new\n")
        evidence.collect_logs()
        self.assertEqual(evidence.logs[0]["offset"], 0)
        self.assertEqual((self.output / evidence.logs[0]["retained_file"]).read_bytes(), b"new\n")

    def test_oversized_or_linked_app_logs_fail_closed(self):
        evidence = self.failed()
        self.logs.mkdir()
        path = self.logs / "owned.log"
        path.write_bytes(b"oversized")
        with patch.object(launch, "MAX_LOG", 1), self.assertRaises(RuntimeError):
            evidence.collect_logs()
        path.unlink()
        path.symlink_to(self.binary)
        with self.assertRaises(RuntimeError):
            evidence.collect_logs()

    def test_monotonic_phase_pid_timeline_and_empty_report_are_explicit(self):
        evidence = self.failed()
        evidence.mark("owned-pid-exited")
        result = evidence.finish()
        self.assertEqual(result["owned_pid"], PID)
        self.assertTrue(result["report_absent"])
        self.assertEqual(result["outcome"], "failure")
        self.assertEqual(result["events"][-1]["phase"], "warm-visible-window")
        self.assertEqual(result["events"][-1]["elapsed_seconds"], 10)
        self.assertFalse(result["customer_app_qualified"])
        self.assertFalse(result["screen_control_qualified"])
        self.assertFalse(result["publication"])
        self.assertEqual(result["executable_sha256"], hashlib.sha256(self.binary.read_bytes()).hexdigest())


class OwnedCleanupTests(OwnedFixtures):
    def test_real_owned_socket_and_exact_pid_removed(self):
        endpoint = self.endpoint()
        self.assertEqual(launch.cleanup_endpoint(endpoint, PID), "removed-owned-endpoint")
        self.assertFalse(endpoint.exists())
        self.assertFalse(endpoint.with_suffix(".pid").exists())

    def test_foreign_pid_noncanonical_marker_and_regular_endpoint_preserved(self):
        endpoint = self.endpoint()
        marker = endpoint.with_suffix(".pid")
        for value in (str(PID + 1).encode(), b"012345", b"12345 extra", b" 12345 ", b"12345\n\n"):
            marker.write_bytes(value)
            with self.assertRaises(RuntimeError):
                launch.cleanup_endpoint(endpoint, PID)
            self.assertTrue(endpoint.exists())
            self.assertEqual(marker.read_bytes(), value)
        marker.write_bytes(str(PID).encode())
        endpoint.unlink()
        endpoint.write_bytes(b"foreign regular file")
        with self.assertRaisesRegex(RuntimeError, "owned socket"):
            launch.cleanup_endpoint(endpoint, PID)
        self.assertEqual(endpoint.read_bytes(), b"foreign regular file")

    def test_symlink_and_hardlink_markers_do_not_delete_any_target(self):
        endpoint = self.endpoint()
        marker = endpoint.with_suffix(".pid")
        marker.unlink()
        foreign = self.root / "foreign-marker"
        foreign.write_bytes(str(PID).encode())
        marker.symlink_to(foreign)
        with self.assertRaises(RuntimeError):
            launch.cleanup_endpoint(endpoint, PID)
        marker.unlink()
        os.link(foreign, marker)
        with self.assertRaises(RuntimeError):
            launch.cleanup_endpoint(endpoint, PID)
        self.assertTrue(endpoint.exists())
        self.assertEqual(foreign.read_bytes(), str(PID).encode())

    def test_pid_marker_changed_after_read_is_preserved(self):
        endpoint = self.endpoint()
        original = launch.bounded_read
        def changed(path, bound):
            result = original(path, bound)
            path.write_bytes(str(PID + 1).encode())
            return result
        with patch.object(launch, "bounded_read", changed), self.assertRaisesRegex(RuntimeError, "changed"):
            launch.cleanup_endpoint(endpoint, PID)
        self.assertTrue(endpoint.exists())
        self.assertEqual(endpoint.with_suffix(".pid").read_text(), str(PID + 1))

    def test_already_exited_pid_cleans_owned_endpoint_without_failed_window_probe(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        with patch.object(launch.os, "kill", side_effect=ProcessLookupError), patch.object(evidence, "window", side_effect=AssertionError("Exited PID must not require NSRunningApplication")):
            result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
        self.assertEqual(result, {"process": "already-exited", "endpoint": "removed-owned-endpoint"})

    def test_active_wrong_bundle_or_launch_refuses_signal_and_unlink(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        for window in (RuntimeError("Window probe targets a different application bundle"),
                       {"running": True, "bundlePath": str(self.app), "launchTime": BASE.timestamp() + 999}):
            with self.subTest(window=window), patch.object(launch.os, "kill") as killed:
                argument = {"side_effect": window} if isinstance(window, Exception) else {"return_value": window}
                with patch.object(evidence, "window", **argument):
                    result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
                self.assertIn("process_error", result)
                self.assertEqual(killed.call_args_list[0].args, (PID, 0))
                self.assertEqual(len(killed.call_args_list), 1)
                self.assertTrue(endpoint.exists())

    def test_kernel_peer_pid_uses_exact_endpoint_and_rejects_different_owner(self):
        from unittest.mock import MagicMock
        endpoint = self.root / 'owned-private-synthetic-ipc'
        for observed in (PID, PID + 1):
            with self.subTest(observed=observed):
                connection = MagicMock()
                connection.__enter__.return_value = connection
                connection.getsockopt.return_value = launch.struct.pack('i', observed)
                with patch.object(launch.socket, 'socket', return_value=connection):
                    if observed == PID:
                        VERIFY_IPC_OWNER(PID, endpoint)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'different application process'):
                            VERIFY_IPC_OWNER(PID, endpoint)
                connection.connect.assert_called_once_with(str(endpoint))
                connection.getsockopt.assert_called_once_with(0, 0x002, 4)
                connection.__exit__.assert_called_once()

    def test_foreign_ipc_owner_blocks_signal_and_retains_endpoint(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        with patch.object(launch.os, 'kill') as killed, patch.object(evidence, 'window', return_value={'running':True, 'bundlePath':str(self.app), 'launchTime':BASE.timestamp()}), patch.object(launch, 'verify_ipc_owner', side_effect=RuntimeError('Actual incoming IPC belongs to a different application process')):
            result = launch.cleanup_runtime(evidence, self.root/'unused-helper', endpoint)
        self.assertEqual([item.args for item in killed.call_args_list], [(PID,0)])
        self.assertIn('process_error', result)
        self.assertEqual(result['endpoint'], 'not-touched')
        self.assertTrue(endpoint.exists())

    def test_authenticated_session_blocks_signal_and_retains_endpoint(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        with patch.object(launch.os, 'kill') as killed, patch.object(evidence, 'window', return_value={'running':True, 'bundlePath':str(self.app), 'launchTime':BASE.timestamp()}), patch.object(launch.unix,'query',return_value=1):
            result = launch.cleanup_runtime(evidence, self.root/'unused-helper', endpoint)
        self.assertEqual([item.args for item in killed.call_args_list], [(PID,0)])
        self.assertIn('process_error', result)
        self.assertEqual(result['endpoint'], 'not-touched')
        self.assertTrue(endpoint.exists())

    def test_verified_sigterm_exit_then_endpoint_cleanup(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        calls = []
        def kill(pid, action):
            calls.append((pid, action))
            if len(calls) >= 3:
                raise ProcessLookupError
        with patch.object(launch.os, "kill", kill), patch.object(evidence, "window", return_value={"running": True, "bundlePath": str(self.app), "launchTime": BASE.timestamp()}):
            result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
        self.assertEqual(calls, [(PID, 0), (PID, signal.SIGTERM), (PID, 0), (PID, 0)])
        self.assertEqual(result["process"], "terminated-owned-application")
        self.assertEqual(result["endpoint"], "removed-owned-endpoint")

    def test_failed_signal_retains_endpoint_and_error(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        with patch.object(launch.os, "kill", side_effect=[None, PermissionError(1, "synthetic")]), patch.object(evidence, "window", return_value={"running": True, "bundlePath": str(self.app), "launchTime": BASE.timestamp()}):
            result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
        self.assertIn("process_error", result)
        self.assertEqual(result["endpoint"], "not-touched")
        self.assertTrue(endpoint.exists())

    def test_exited_then_reused_pid_cannot_delete_owned_old_endpoint(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        with patch.object(launch.os, "kill", side_effect=[ProcessLookupError, None]), patch.object(evidence, "window", side_effect=AssertionError("No foreign probe")):
            result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
        self.assertIn("endpoint_error", result)
        self.assertEqual(result["endpoint"], "not-touched")
        self.assertTrue(endpoint.exists())

    def test_sigkill_checks_same_launch_and_confirms_exit_before_unlink(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        killed = []
        gone = False
        def kill(pid, action):
            nonlocal gone
            killed.append((pid, action))
            if action == signal.SIGKILL:
                gone = True
            elif gone:
                raise ProcessLookupError
        with patch.object(launch.os, "kill", kill), patch.object(evidence, "window", return_value={"running": True, "bundlePath": str(self.app), "launchTime": BASE.timestamp()}) as checked:
            result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
        self.assertGreater(checked.call_count, 2)
        self.assertIn((PID, signal.SIGTERM), killed)
        self.assertIn((PID, signal.SIGKILL), killed)
        self.assertEqual(result["endpoint"], "removed-owned-endpoint")

    def test_sigkill_refuses_changed_launch_after_timeout(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        with patch.object(launch.os, "kill") as killed, patch.object(evidence, "window", side_effect=lambda *args, **kwargs: {"running": True, "bundlePath": str(self.app), "launchTime": BASE.timestamp() + (1 if self.clock.value >= 15 else 0)}):
            result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
        self.assertIn("process_error", result)
        self.assertNotIn((PID, signal.SIGKILL), [call.args for call in killed.call_args_list])
        self.assertTrue(endpoint.exists())

    def test_invalid_owned_identity_refuses_all_process_and_ipc_operations(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        for pid, birth in ((True, BASE.timestamp()), (0, BASE.timestamp()), (PID, False),
                           (PID, 0), (PID, float("nan")), (PID, float("inf"))):
            evidence.pid, evidence.launch_time = pid, birth
            with self.subTest(pid=pid, birth=birth), patch.object(launch.os, "kill") as killed, \
                 patch.object(evidence, "window") as window, patch.object(launch.unix, "query") as query:
                result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
                killed.assert_not_called(); window.assert_not_called(); query.assert_not_called()
                self.assertIn("process_error", result)
                self.assertTrue(endpoint.exists())

    def test_only_exact_integer_zero_is_a_connection_count_proof(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        window = {"running": True, "bundlePath": str(self.app), "launchTime": BASE.timestamp()}
        for count in (False, True, 0.0, -1, 1, "0", None, float("nan")):
            with self.subTest(count=count), patch.object(launch.os, "kill") as killed, \
                 patch.object(evidence, "window", return_value=window), patch.object(launch.unix, "query", return_value=count):
                result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
                self.assertEqual([call.args for call in killed.call_args_list], [(PID, 0)])
                self.assertIn("process_error", result)
                self.assertEqual(result["endpoint"], "not-touched")
                self.assertTrue(endpoint.exists())

    def test_missing_ipc_cannot_authorize_a_signal(self):
        evidence = self.failed()
        window = {"running": True, "bundlePath": str(self.app), "launchTime": BASE.timestamp()}
        with patch.object(launch.os, "kill") as killed, patch.object(evidence, "window", return_value=window), \
             patch.object(launch.unix, "query") as query:
            result = launch.cleanup_runtime(evidence, self.root / "unused-helper", self.root / "absent-ipc")
        self.assertEqual([call.args for call in killed.call_args_list], [(PID, 0)])
        query.assert_not_called()
        self.assertIn("process_error", result)

    def test_new_connection_after_term_refuses_kill_and_retains_endpoint(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        window = {"running": True, "bundlePath": str(self.app), "launchTime": BASE.timestamp()}
        with patch.object(launch.os, "kill") as killed, patch.object(evidence, "window", return_value=window), \
             patch.object(launch.unix, "query", side_effect=[0, 1]) as query, \
             patch.object(launch, "verify_ipc_owner") as owner:
            result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
        signals = [call.args for call in killed.call_args_list if call.args[1] != 0]
        self.assertEqual(signals, [(PID, signal.SIGTERM)])
        self.assertEqual(query.call_count, 2)
        self.assertEqual(owner.call_args_list, [unittest.mock.call(PID, endpoint)] * 2)
        self.assertIn("process_error", result)
        self.assertTrue(endpoint.exists())

    def test_replaced_kernel_owner_after_term_refuses_kill(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        window = {"running": True, "bundlePath": str(self.app), "launchTime": BASE.timestamp()}
        with patch.object(launch.os, "kill") as killed, patch.object(evidence, "window", return_value=window), \
             patch.object(launch, "verify_ipc_owner", side_effect=[None, RuntimeError("different application process")]) as owner, \
             patch.object(launch.unix, "query", return_value=0) as query:
            result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
        self.assertEqual([call.args for call in killed.call_args_list if call.args[1] != 0], [(PID, signal.SIGTERM)])
        self.assertEqual(owner.call_count, 2)
        self.assertEqual(query.call_count, 1, "Foreign IPC owner must not be queried")
        self.assertIn("process_error", result)
        self.assertTrue(endpoint.exists())

    def test_changed_bundle_or_invalid_running_observation_after_term_refuses_kill(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        owned = {"running": True, "bundlePath": str(self.app), "launchTime": BASE.timestamp()}
        for changed in ({**owned, "bundlePath": str(self.root / "foreign.app")},
                        {**owned, "running": 1}, {**owned, "launchTime": False},
                        {**owned, "launchTime": float("nan")}):
            with self.subTest(changed=changed), patch.object(launch.os, "kill") as killed, \
                 patch.object(evidence, "window", side_effect=[owned, changed]):
                result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
                self.assertEqual([call.args for call in killed.call_args_list if call.args[1] != 0], [(PID, signal.SIGTERM)])
                self.assertIn("process_error", result)
                self.assertTrue(endpoint.exists())

    def test_native_ended_zombie_needs_no_kill_and_cleans_only_inactive_owned_socket(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        owned = {"running": True, "bundlePath": str(self.app), "launchTime": BASE.timestamp()}
        ended = {"running": False, "bundlePath": ""}
        with patch.object(launch.os, "kill") as killed, \
             patch.object(evidence, "window", side_effect=[owned, ended, ended]) as checked:
            result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
        self.assertEqual([call.args for call in killed.call_args_list if call.args[1] != 0], [(PID, signal.SIGTERM)])
        self.assertEqual(checked.call_count, 3, "Native-ended identity must be rechecked before deletion")
        self.assertEqual(result, {"process": "terminated-owned-application", "endpoint": "removed-owned-endpoint"})
        self.assertEqual(self.clock.value, 10, "Native-ended proof avoids zombie timeout")
        self.assertFalse(endpoint.exists())

    def test_native_ended_with_live_listener_or_reused_launch_preserves_endpoint(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        endpoint.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(endpoint)); listener.listen(1)
        ended = {"running": False, "bundlePath": ""}
        with patch.object(launch.os, "kill") as killed, patch.object(evidence, "window", return_value=ended):
            result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
        self.assertFalse(any(call.args[1] != 0 for call in killed.call_args_list))
        self.assertIn("endpoint_error", result)
        self.assertTrue(endpoint.exists())
        with patch.object(launch.os, "kill") as killed, patch.object(evidence, "window", side_effect=[
                ended, {"running": True, "bundlePath": str(self.app), "launchTime": BASE.timestamp() + 1}]):
            result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
        self.assertFalse(any(call.args[1] != 0 for call in killed.call_args_list))
        self.assertIn("endpoint_error", result)
        self.assertTrue(endpoint.exists())

    def test_never_exiting_owned_process_is_bounded_and_does_not_unlink(self):
        evidence = self.failed()
        endpoint = self.endpoint()
        owned = {"running": True, "bundlePath": str(self.app), "launchTime": BASE.timestamp()}
        with patch.object(launch.os, "kill") as killed, patch.object(evidence, "window", return_value=owned), \
             patch.object(launch, "verify_ipc_owner") as owner, patch.object(launch.unix, "query", return_value=0) as query:
            result = launch.cleanup_runtime(evidence, self.root / "unused-helper", endpoint)
        self.assertEqual([call.args for call in killed.call_args_list if call.args[1] != 0], [(PID, signal.SIGTERM), (PID, signal.SIGKILL)])
        self.assertEqual(owner.call_count, 2); self.assertEqual(query.call_count, 2)
        self.assertLess(self.clock.value, 20.3)
        self.assertIn("did not exit", result["process_error"])
        self.assertTrue(endpoint.exists())

    def test_native_ended_observation_is_allowed_only_for_cleanup(self):
        evidence = self.failed()
        ended = {"running": False, "bundlePath": ""}
        with patch.object(evidence, "request", return_value=ended):
            with self.assertRaisesRegex(RuntimeError, "different application bundle"):
                evidence.window(self.root / "unused-helper")
            self.assertEqual(evidence.window(self.root / "unused-helper", allow_terminated=True), ended)


class ExactRuntimeTests(OwnedFixtures):
    def exercise_warm_exit(self, log_delta=b"synthetic app exit evidence\n"):
        endpoint = self.root / "runtime-ipc" / "ipc"
        endpoint.parent.mkdir()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(connection.close)
        stage = {"warm": False}
        def request(helper, action, *arguments):
            if action in ("ordinary","launch"):
                if action == "ordinary":
                    connection.bind(str(endpoint))
                    endpoint.with_suffix(".pid").write_text(str(PID))
                    self.logs.mkdir()
                    (self.logs / "owned.log").write_bytes(log_delta)
                else:
                    stage["warm"] = True
                return {"pid": PID, "bundlePath": str(self.app), "launchTime": BASE.timestamp()}
            if action == "hide":
                return {"requested": False}
            return {"running": True, "visible": True, "hidden": False, "bundlePath": str(self.app), "launchTime": BASE.timestamp()}
        def kill(pid, action):
            self.assertEqual(action, 0, "Already exited app must never be signalled")
            if stage["warm"]:
                raise ProcessLookupError
        with patch.object(launch.unix, "IPC_PATH", endpoint), patch.object(launch.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(self.home))), patch.object(launch, "helper_request", request), patch.object(launch.os, "kill", kill), patch.object(launch.unix, "runtime_health", return_value=(1, True)):
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "Warm support URL customer window: customer app exited"):
                launch.runtime(self.app, self.root / "unused-helper", self.output)
        result = json.loads((self.output / "runtime.json").read_bytes())
        return result

    def test_warm_exit_stays_fatal_records_phase_and_independently_cleans(self):
        result = self.exercise_warm_exit()
        self.assertEqual(result["outcome"], "failure")
        self.assertEqual(next(item for item in result["events"] if item["event"] == "test-failure")["phase"], "fresh-warm-url-launch")
        self.assertEqual(result["cleanup"]["process"], "already-exited")
        self.assertEqual(result["cleanup"]["endpoint"], "removed-owned-endpoint")
        self.assertEqual((self.output / result["app_log_deltas"][0]["retained_file"]).read_bytes(), b"synthetic app exit evidence\n")
        self.assertTrue(result["report_absent"])
        self.assertEqual(self.clock.value, 3, "Bounded three-second diagnostic wait only")

    def test_failure_path_privacy_scan_still_runs_and_preserves_first_failure(self):
        result = self.exercise_warm_exit((launch.unix.TOKEN + " private app delta\n").encode())
        self.assertFalse(result["privacy_check_passed"])
        self.assertEqual(result["app_log_deltas"][0]["withheld"], "bearer-or-credential-pattern")
        self.assertEqual(result["cleanup"]["endpoint"], "removed-owned-endpoint")
        self.assertNotIn(launch.unix.TOKEN, json.dumps(result))
        self.assertEqual([item.name for item in self.output.iterdir()], ["runtime.json"])
        failures = [item for item in result["events"] if item["event"] == "test-failure"]
        self.assertEqual(len(failures), 1)
        self.assertIn("customer app exited", failures[0]["reason"])

    def test_complete_success_path_preserves_both_health_checks_and_owned_cleanup(self):
        endpoint = self.root / "runtime-ipc" / "ipc"
        endpoint.parent.mkdir()
        sockets=[]
        stopped=set()
        launches=[]
        active_pid=PID
        def request(helper, action, *arguments):
            nonlocal active_pid
            if action in ("ordinary","launch"):
                launches.append((action,arguments))
                if action=="ordinary" or arguments[-1]=="cold":
                    active_pid=PID if action=="ordinary" else PID+1
                    connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
                    sockets.append(connection);self.addCleanup(connection.close)
                    connection.bind(str(endpoint));endpoint.with_suffix(".pid").write_text(str(active_pid))
                return {"pid":active_pid,"bundlePath":str(self.app),"launchTime":BASE.timestamp()+active_pid-PID}
            if action=="hide":return {"requested":False}
            return {"running":True,"visible":True,"hidden":False,"bundlePath":str(self.app),"launchTime":BASE.timestamp()+active_pid-PID}
        def kill(pid, action):
            if action==signal.SIGTERM:stopped.add(pid)
            elif pid in stopped:raise ProcessLookupError
        captured = io.StringIO()
        with patch.object(launch.unix, "IPC_PATH", endpoint), patch.object(launch.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(self.home))), patch.object(launch, "helper_request", request), patch.object(launch.os, "kill", kill), patch.object(launch.unix, "runtime_health", return_value=(1, True)) as health, redirect_stdout(captured):
            launch.runtime(self.app, self.root / "unused-helper", self.output)
        result = json.loads((self.output / "runtime.json").read_bytes())
        self.assertEqual([item[0] for item in launches],["ordinary","launch","launch"])
        self.assertEqual(launches[1][1][-2:],(launch.WARM_URI,"warm"))
        self.assertEqual(launches[2][1][-2:],(launch.unix.URI,"cold"))
        self.assertNotEqual(launch.WARM_TOKEN,launch.unix.TOKEN)
        self.assertEqual(stopped,{PID,PID+1})
        self.assertEqual(health.call_count, 2)
        self.assertEqual([call.args for call in health.call_args_list],[(PID,),(PID+1,)])
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["cleanup"]["endpoint"], "removed-owned-endpoint")
        self.assertFalse(result["customer_app_qualified"])
        self.assertIn("fresh warm support intent changes the same ordinary application", captured.getvalue())
        self.assertEqual(self.clock.value, 5)

    def test_wait_deadline_and_process_absence_remain_fatal(self):
        with patch.object(launch.os, "kill", return_value=None):
            with self.assertRaises(RuntimeError):
                launch.wait_for("actual unchanged window deadline", lambda: False, PID, timeout=1)
        self.assertEqual(self.clock.value, 1)
        with patch.object(launch.os, "kill", side_effect=ProcessLookupError):
            with self.assertRaisesRegex(RuntimeError, "customer app exited"):
                launch.wait_for("actual window", lambda: True, PID)


class WorkflowScopeTests(unittest.TestCase):
    def test_signed_stapled_zip_before_smoke_and_failure_only_separate_artifact(self):
        source = (ROOT / ".github/workflows/build.yml").read_text()
        package = source.split("      - name: Package + sign + notarize (.app then .dmg)\n", 1)[1].split("  # WINDOWS", 1)[0]
        self.assertIn("        id: macos_package\n", package)
        markers = ['codesign --verify --deep --strict --verbose=2 "$APP_PATH"',
                   'xcrun stapler staple "$APP_PATH"', 'xcrun stapler validate "$APP_PATH"',
                   'spctl --assess --type execute --verbose=2 "$APP_PATH"',
                   'ditto -c -k --keepParent "$APP_PATH" "$DIAGNOSTIC_APP_ZIP"',
                   "python3 ../scripts/test-support-launch-macos-diagnostics.py",
                   'python3 ../scripts/test-support-launch-macos-concurrency.py --original-desktop --app "$APP_PATH" \\\n'
                   '              --diagnostics "$DIAGNOSTICS/runtime" --diagnostic-app-zip "$DIAGNOSTIC_APP_ZIP"',
                   'python3 ../scripts/test-support-launch-macos-concurrency.py --original-tray --app "$APP_PATH" \\\n'
                   '              --proofs "$TRAY_PROOF_ROOT/macos-${{ matrix.job.arch }}-tray"',
                   'python3 ../scripts/test-support-launch-macos-concurrency.py --app "$APP_PATH" \\\n'
                   '              --proofs "$TRAY_PROOF_ROOT/macos-${{ matrix.job.arch }}-concurrency"',
                   "# 3) Package stapled .app into DMG"]
        for marker in markers:
            self.assertEqual(package.count(marker), 1, f"Expected one exact mandatory macOS package gate: {marker}")
        positions = [package.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("if: ${{ always() && steps.macos_package.outcome == 'failure' }}", package)
        self.assertIn("name: macos-launch-failure-diagnostic-${{ matrix.job.arch }}", package)
        self.assertIn("path: macos-launch-failure/", package)
        self.assertNotIn("continue-on-error", package)
        self.assertNotIn('DIAGNOSTICS="../dist', package)
        self.assertIn('rm -f "$APP_ZIP"', package)
        ordinary = package.split("name: macos-${{ matrix.job.arch }}", 1)[1]
        self.assertIn("path: dist/", ordinary)
        self.assertNotIn("always()", ordinary)


if __name__ == "__main__":
    unittest.main(verbosity=2)
