#!/usr/bin/env python3
"""Source/mock-only observer controls. Native and mutation seams are forbidden."""
import copy
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).with_name("observe-macos-incoming-cleanup.py")
SPEC = importlib.util.spec_from_file_location("mixel_incoming_observer_controls", SOURCE)
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)
UID = 501
NOW = 1_700_000_000
SECRET = "inv_12345678-1234-1234-1234-123456789012?apikey=private-bearer-secret"


def bsd(pid, *, uid=UID, ruid=None, svuid=None, ppid=1, status=2, sec=NOW - 60, usec=12, secret=False):
    fields = [0, status, 0, pid, ppid, uid, 20, uid if ruid is None else ruid,
              20, uid if svuid is None else svuid, 20, 0]
    comm = SECRET.encode()[:16] if secret else b"test"
    name = SECRET.encode()[:32] if secret else b"test"
    return 136, struct.pack("=12I16s32s5Ii2Q", *fields, comm, name, 0, 1, 0, 0, 0, 0, sec, usec), 0


def path_packet(path, count=None):
    raw = os.fsencode(path)
    return len(raw) if count is None else count, (raw + b"\0").ljust(4096, b"\0"), 0


def pid_packet(pids):
    raw = struct.pack("=" + "i" * len(pids), *pids)
    return len(raw), raw.ljust(observer.PID_BYTES, b"\0"), 0


class FakeConnection:
    def __init__(self, backend):
        self.backend = backend
        self.closed = False

    def close(self):
        self.closed = True

    def send(self, *args):
        raise AssertionError("IPC send is forbidden")

    sendall = send
    recv = send


class FakeBackend:
    def __init__(self, app):
        self.app = app
        self.clock = 100.0
        self.cost = 0.0
        self.calls = []
        self.records = {10: bsd(10), 20: bsd(20), 30: bsd(30)}
        self.paths = {10: path_packet(app / "Contents/MacOS/Mixel-Remote"),
                      20: path_packet(app / "Contents/MacOS/Mixel-Remote"),
                      30: path_packet(app / "Contents/MacOS/service")}
        self.ids = pid_packet([10, 20, 30])
        self.peer = 30
        self.peer_calls = 0
        self.peer_credentials = (UID, 20)
        self.identity = (stat.S_IFSOCK | 0o777, UID, 1, 42, 0, 1, 1)
        self.connections = []
        self.connect_error = None
        self.timeouts = []
        self.sleep_calls = []

    def now(self):
        return self.clock

    def wall(self):
        return NOW

    def uid(self):
        return UID

    def sleep(self, value):
        self.sleep_calls.append(value)
        self.clock += value

    def _read(self, kind, value):
        self.calls.append((kind, self.clock))
        self.clock += self.cost
        if isinstance(value, list):
            result = value[0]
            if len(value) > 1:
                value.pop(0)
        else:
            result = value
        if isinstance(result, BaseException):
            raise result
        return result

    def bsd(self, pid):
        return self._read("bsd", self.records.get(pid, (0, bytes(136), errno.ESRCH)))

    def path(self, pid):
        return self._read("path", self.paths.get(pid, (0, bytes(4096), errno.ESRCH)))

    def pids(self):
        return self._read("pids", self.ids)

    def socket_identity(self, endpoint):
        if endpoint != Path("/tmp/Mixel-Remote/ipc"):
            raise AssertionError("Unexpected endpoint")
        return self._read("socket_identity", self.identity)

    def connect(self, endpoint, timeout):
        self.timeouts.append(timeout)
        if self.connect_error is not None:
            self._read("connect", self.connect_error)
        self._read("connect", None)
        connection = FakeConnection(self)
        self.connections.append(connection)
        return connection

    def credentials(self, connection):
        return self._read("credentials", self.peer_credentials)

    def peer_pid(self, connection):
        self.peer_calls += 1
        return self._read("peer_pid", self.peer)


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mixel-incoming-observer-source-")
        self.app = Path(self.temporary.name).resolve() / "Mixel-Remote.app"
        (self.app / "Contents/MacOS").mkdir(parents=True)
        (self.app / "Contents/MacOS/Mixel-Remote").write_bytes(b"source-only-main")
        (self.app / "Contents/MacOS/service").write_bytes(b"source-only-service")
        self.backend = FakeBackend(self.app)
        main = str(self.app / "Contents/MacOS/Mixel-Remote")
        self.tray_birth = ("darwin-gui-birth-v1", 10, UID, main, NOW - 60, 12)
        self.gui_birth = ("darwin-gui-birth-v1", 20, UID, main, NOW - 60, 12)
        self.forbidden = []
        for target in ("ctypes.CDLL", "socket.socket", "os.kill", "subprocess.run", "subprocess.Popen", "os.unlink", "pathlib.Path.unlink"):
            patcher = mock.patch(target, side_effect=AssertionError("Native/mutation seam forbidden"))
            self.forbidden.append((patcher, patcher.start()))

    def tearDown(self):
        for patcher, patched in reversed(self.forbidden):
            self.assertEqual(patched.call_count, 0, "Native/mutation seam was reached")
            patcher.stop()
        self.temporary.cleanup()

    def observe(self, **changes):
        arguments = dict(app=self.app, server_pid=10, server_birth=self.tray_birth,
                         gui_pid=20, gui_birth=self.gui_birth,
                         peer_pid_callback=self.backend.peer_pid, backend=self.backend)
        arguments.update(changes)
        result = observer.observe(**arguments)
        self.assertTrue(observer.validate_facts(result))
        self.assertFalse(result["cleanupAuthority"])
        self.assertFalse(result["fdOwnershipEstablished"])
        self.assertFalse(result["recoveryEstablished"])
        return result

    def test_three_bounded_samples_exact_main_service_and_retained_records(self):
        facts = self.observe()
        self.assertEqual(facts["outcome"], "observed")
        self.assertEqual(len(facts["samples"]), 3)
        self.assertEqual(sum(name == "pids" for name, _ in self.backend.calls), 3)
        self.assertEqual(len(self.backend.connections), 3)
        self.assertTrue(all(connection.closed for connection in self.backend.connections))
        self.assertEqual(self.backend.peer_calls, 6)
        self.assertTrue(all(0 < timeout <= 0.2 for timeout in self.backend.timeouts))
        self.assertLessEqual(facts["elapsedMs"], 1500)
        for sample in facts["samples"]:
            self.assertEqual(sample["tray"]["pid"], 10)
            self.assertEqual(sample["gui"]["pid"], 20)
            self.assertIs(sample["tray"]["retainedBirthMatches"], True)
            self.assertIs(sample["gui"]["retainedBirthMatches"], True)
            self.assertEqual([row["executable"] for row in sample["inventory"]["members"]], ["main", "main", "service"])
            self.assertEqual(sample["peer"]["process"]["executable"], "service")

    def test_foreign_uid_peer_is_observed_but_excluded_from_inventory(self):
        self.backend.records[30] = bsd(30, uid=0)
        self.backend.peer_credentials = (0, 0)
        facts = self.observe()
        self.assertEqual(facts["samples"][0]["peer"]["process"]["state"], "foreign_uid")
        self.assertEqual([row["pid"] for row in facts["samples"][0]["inventory"]["members"]], [10, 20])

    def test_real_saved_effective_uid_fields_are_not_merged(self):
        self.backend.records[30] = bsd(30, ruid=0, svuid=0)
        row = self.observe()["samples"][0]["peer"]["process"]
        self.assertEqual((row["uid"], row["euid"], row["ruid"], row["svuid"]), (UID, UID, 0, 0))
        self.assertEqual(row["state"], "foreign_uid")

    def test_missing_retained_pid_requires_two_esrch_records(self):
        self.backend.records.pop(20)
        facts = self.observe()
        self.assertEqual(facts["samples"][0]["gui"]["state"], "missing")
        self.assertIsNone(facts["samples"][0]["gui"]["retainedBirthMatches"])
        self.assertEqual(facts["samples"][0]["inventory"]["state"], "partial")

    def test_missing_then_reappearing_pid_is_changed(self):
        self.backend.records[20] = [(0, bytes(136), errno.ESRCH), bsd(20)]
        self.assertEqual(self.observe()["samples"][0]["gui"]["state"], "changed")

    def test_reused_retained_pid_has_no_matching_lease(self):
        self.backend.records[20] = bsd(20, sec=NOW - 59)
        row = self.observe()["samples"][0]["gui"]
        self.assertEqual(row["state"], "reused")
        self.assertIs(row["retainedBirthMatches"], False)

    def test_retained_executable_mismatch_is_observation_only(self):
        self.backend.paths[20] = path_packet(self.app / "Contents/MacOS/service")
        row = self.observe()["samples"][0]["gui"]
        self.assertEqual(row["state"], "retained_mismatch")
        self.assertIs(row["retainedBirthMatches"], False)

    def test_zombie_status_retains_immutable_birth_without_recovery(self):
        self.backend.records[20] = bsd(20, status=5)
        row = self.observe()["samples"][0]["gui"]
        self.assertEqual(row["status"], 5)
        self.assertIs(row["retainedBirthMatches"], True)

    def test_both_retained_zombies_with_path_esrch_keep_double_kernel_birth(self):
        for pid in (10, 20):
            self.backend.records[pid] = bsd(pid, status=5)
            self.backend.paths[pid] = (0, bytes(4096), errno.ESRCH)
        facts = self.observe()
        for name in ("tray", "gui"):
            row = facts["samples"][0][name]
            self.assertEqual((row["state"], row["status"], row["pathState"]), ("kernel_only", 5, "missing"))
            self.assertEqual((row["uid"], row["startSec"], row["startUsec"]), (UID, NOW - 60, 12))
            self.assertEqual(row["executable"], "unavailable")
            self.assertIs(row["retainedBirthMatches"], True)
        self.assertEqual(facts["outcome"], "partial")

    def test_retained_path_permission_keeps_kernel_birth_without_executable(self):
        self.backend.paths[20] = (0, bytes(4096), errno.EPERM)
        row = self.observe()["samples"][0]["gui"]
        self.assertEqual((row["state"], row["pathState"], row["status"]), ("kernel_only", "permission", 2))
        self.assertEqual(row["startSec"], NOW - 60)
        self.assertIs(row["retainedBirthMatches"], True)

    def test_kernel_only_reused_retained_pid_never_matches_birth(self):
        self.backend.records[20] = bsd(20, status=5, sec=NOW - 59)
        self.backend.paths[20] = (0, bytes(4096), errno.ESRCH)
        row = self.observe()["samples"][0]["gui"]
        self.assertEqual((row["state"], row["pathState"]), ("reused", "missing"))
        self.assertIs(row["retainedBirthMatches"], False)

    def test_kernel_only_bsd_change_has_no_numeric_birth_claim(self):
        self.backend.records[20] = [bsd(20, status=5), bsd(20, status=5, sec=NOW - 59)]
        self.backend.paths[20] = (0, bytes(4096), errno.ESRCH)
        row = self.observe()["samples"][0]["gui"]
        self.assertEqual(row["state"], "changed")
        self.assertIsNone(row["retainedBirthMatches"])
        self.assertIsNone(row["startSec"])

    def test_scheduling_status_change_is_not_birth_change(self):
        self.backend.records[20] = [bsd(20, status=2), bsd(20, status=3)]
        self.assertEqual(self.observe()["samples"][0]["gui"]["state"], "stable")

    def test_double_bsd_identity_change_is_fixed_changed(self):
        self.backend.records[30] = [bsd(30), bsd(30, sec=NOW - 59)]
        row = self.observe()["samples"][0]["peer"]["process"]
        self.assertEqual(row["state"], "changed")
        self.assertIsNone(row["startSec"])

    def test_double_executable_change_is_fixed_changed(self):
        self.backend.paths[30] = [path_packet(self.app / "Contents/MacOS/service"), path_packet("/bin/other")]
        self.assertEqual(self.observe()["samples"][0]["peer"]["process"]["state"], "changed")

    def test_malformed_truncated_bsd(self):
        for packet in ((135, bytes(136), 0), (136, bytes(135), 0), (True, bytes(136), 0), (136, bytearray(136), 0)):
            with self.subTest(packet_type=type(packet[1])):
                self.backend = FakeBackend(self.app)
                self.backend.records[30] = packet
                self.assertEqual(self.observe()["samples"][0]["peer"]["process"]["state"], "malformed")

    def test_invalid_bsd_pid_status_birth_or_ppid(self):
        for packet in (bsd(31), bsd(30, status=0), bsd(30, status=6), bsd(30, sec=NOW + 6), bsd(30, usec=1_000_000), bsd(30, ppid=0xffffffff)):
            with self.subTest(packet=hashlib.sha256(packet[1]).hexdigest()[:8]):
                self.backend = FakeBackend(self.app)
                self.backend.records[30] = packet
                self.assertEqual(self.observe()["samples"][0]["peer"]["process"]["state"], "malformed")

    def test_permission_and_generic_missing_metadata_never_mean_exit(self):
        for result_errno in (errno.EPERM, errno.EACCES, 0):
            with self.subTest(result_errno=result_errno):
                self.backend = FakeBackend(self.app)
                self.backend.records[20] = (0, bytes(136), result_errno)
                self.assertEqual(self.observe()["samples"][0]["gui"]["state"], "unavailable")

    def test_malformed_truncated_or_noncanonical_path(self):
        packets = [(4096, b"a" * 4096, 0), (3, b"abc", 0), (3, b"abc" + bytes(4093), 0),
                   path_packet("/a/../secret"), path_packet("//a/secret"), path_packet("/a\nsecret"),
                   (2, b"/\xff\0" + bytes(4093), 0), path_packet("/valid", count=3)]
        for packet in packets:
            with self.subTest(packet_type=type(packet[1])):
                self.backend = FakeBackend(self.app)
                self.backend.paths[30] = packet
                row = self.observe()["samples"][0]["peer"]["process"]
                self.assertEqual((row["state"], row["pathState"]), ("kernel_only", "malformed"))

    def test_path_unavailable_is_not_canonical_other(self):
        self.backend.paths[30] = (0, bytes(4096), errno.EPERM)
        row = self.observe()["samples"][0]["peer"]["process"]
        self.assertEqual((row["state"], row["executable"], row["pathState"]), ("kernel_only", "unavailable", "permission"))

    def test_canonical_other_path_is_only_enum(self):
        self.backend.paths[30] = path_packet("/private/secret/other")
        facts = self.observe()
        self.assertEqual(facts["samples"][0]["peer"]["process"]["executable"], "other")
        self.assertNotIn("/private/secret", json.dumps(facts))
        self.assertNotIn(30, [row["pid"] for row in facts["samples"][0]["inventory"]["members"]])

    def test_path_count_may_include_only_the_terminator(self):
        raw = os.fsencode(self.app / "Contents/MacOS/service")
        self.backend.paths[30] = path_packet(raw, count=len(raw) + 1)
        self.assertEqual(self.observe()["samples"][0]["peer"]["process"]["state"], "stable")

    def test_missing_peer_pid_and_malformed_callback_results(self):
        for peer in (None, 0, -1, True, observer.MAX_PID + 1, SECRET):
            with self.subTest(peer_type=type(peer)):
                self.backend = FakeBackend(self.app)
                self.backend.peer = peer
                facts = self.observe()
                self.assertEqual(facts["samples"][0]["peer"]["state"], "malformed")
                self.assertNotIn(SECRET, json.dumps(facts))

    def test_peer_pid_change_is_not_listener_ownership(self):
        self.backend.peer = [30, 31]
        self.assertEqual(self.observe()["samples"][0]["peer"]["state"], "changed")

    def test_peer_credentials_change_and_process_uid_disagree(self):
        for credentials in ([(UID, 20), (0, 0)], (0, 0)):
            with self.subTest(credentials_type=type(credentials)):
                self.backend = FakeBackend(self.app)
                self.backend.peer_credentials = credentials
                self.assertEqual(self.observe()["samples"][0]["peer"]["state"], "changed")

    def test_peer_credentials_optional_unavailable_is_explicit(self):
        self.backend.peer_credentials = PermissionError(SECRET)
        facts = self.observe()
        peer = facts["samples"][0]["peer"]
        self.assertEqual((peer["state"], peer["credentialsState"]), ("unavailable", "unavailable"))
        self.assertIsNone(peer["uid"])
        self.assertEqual((facts["outcome"], facts["samples"][0]["state"]), ("partial", "partial"))

    def test_malformed_credentials_have_no_raw_values(self):
        self.backend.peer_credentials = (SECRET, 20)
        facts = self.observe()
        self.assertEqual(facts["samples"][0]["peer"]["credentialsState"], "malformed")
        self.assertNotIn(SECRET, json.dumps(facts))

    def test_socket_identity_change_remains_changed(self):
        self.backend.identity = [self.backend.identity, (stat.S_IFSOCK | 0o777, UID, 1, 43, 0, 1, 1)]
        self.assertEqual(self.observe()["samples"][0]["peer"]["state"], "changed")

    def test_socket_wrong_type_or_uid_never_connects(self):
        for identity in ((stat.S_IFREG | 0o600, UID, 1, 42, 0, 1, 1), (stat.S_IFSOCK | 0o777, 0, 1, 42, 0, 1, 1)):
            with self.subTest(mode=identity[0]):
                self.backend = FakeBackend(self.app)
                self.backend.identity = identity
                facts = self.observe()
                self.assertEqual(facts["samples"][0]["peer"]["reason"], "unsafe_socket")
                self.assertEqual(self.backend.connections, [])

    def test_connection_failure_fixed_categories_and_no_recovery(self):
        for error, reason in ((FileNotFoundError(SECRET), "absent"), (ConnectionRefusedError(SECRET), "refused"), (TimeoutError(SECRET), "timeout"), (PermissionError(SECRET), "permission"), (OSError(SECRET), "failed")):
            with self.subTest(reason=reason):
                self.backend = FakeBackend(self.app)
                self.backend.connect_error = error
                facts = self.observe()
                self.assertEqual(facts["samples"][0]["peer"]["reason"], reason)
                self.assertEqual(facts["outcome"], "partial")
                self.assertNotIn(SECRET, json.dumps(facts))

    def test_no_peer_record_is_inconclusive(self):
        self.backend.records.pop(30)
        peer = self.observe()["samples"][0]["peer"]
        self.assertEqual((peer["state"], peer["process"]["state"]), ("unavailable", "missing"))

    def test_enumeration_full_buffer_is_truncated_not_empty(self):
        self.backend.ids = pid_packet(list(range(1, 4097)))
        row = self.observe()["samples"][0]["inventory"]
        self.assertEqual((row["state"], row["reason"]), ("unavailable", "truncated"))
        self.assertTrue(row["mayBeIncomplete"])
        self.assertIsNone(row["enumerated"])

    def test_enumeration_overflow_and_malformed_buffers(self):
        for packet in ((observer.PID_BYTES + 4, bytes(observer.PID_BYTES), 0), (5, bytes(observer.PID_BYTES), 0), (4, bytes(4), 0), pid_packet([-1]), pid_packet([10, 10]), pid_packet([0, 0])):
            with self.subTest(count=packet[0]):
                self.backend = FakeBackend(self.app)
                self.backend.ids = packet
                row = self.observe()["samples"][0]["inventory"]
                self.assertEqual((row["state"], row["reason"]), ("unavailable", "malformed"))

    def test_enumeration_permission_or_zero_never_false_empty(self):
        for packet in ((-1, bytes(observer.PID_BYTES), errno.EPERM), (0, bytes(observer.PID_BYTES), 0)):
            self.backend = FakeBackend(self.app)
            self.backend.ids = packet
            row = self.observe()["samples"][0]["inventory"]
            self.assertEqual(row["state"], "unavailable")
            self.assertIsNone(row["enumerated"])

    def test_enumeration_inaccessible_member_means_partial(self):
        self.backend.ids = pid_packet([10, 20, 30, 999])
        row = self.observe()["samples"][0]["inventory"]
        self.assertEqual((row["state"], row["reason"]), ("partial", "identity_unavailable"))
        self.assertEqual((row["enumerated"], row["examined"], row["omitted"]), (4, 4, 1))

    def test_enumeration_member_limit_is_partial_at_32(self):
        for pid in range(100, 140):
            self.backend.records[pid] = bsd(pid)
            self.backend.paths[pid] = path_packet(self.app / "Contents/MacOS/service")
        self.backend.ids = pid_packet(list(range(100, 140)))
        row = self.observe()["samples"][0]["inventory"]
        self.assertEqual((row["state"], row["reason"]), ("partial", "member_limit"))
        self.assertEqual(len(row["members"]), 32)

    def test_deadline_no_new_native_calls_after_budget(self):
        self.backend.cost = 0.06
        facts = self.observe()
        self.assertEqual((facts["outcome"], facts["error"]), ("failed", "deadline"))
        self.assertTrue(all(start < 101.5 for _, start in self.backend.calls))
        self.assertLessEqual(sum(name == "pids" for name, _ in self.backend.calls), 3)
        self.assertTrue(all(0 < timeout <= 0.2 for timeout in self.backend.timeouts))

    def test_deadline_during_inventory_preserves_partial_members(self):
        self.backend.cost = 0.03
        self.backend.ids = pid_packet(list(range(10, 100)))
        facts = self.observe()
        self.assertEqual(facts["error"], "deadline")
        row = facts["samples"][0]["inventory"]
        self.assertEqual((row["state"], row["reason"]), ("partial", "deadline"))
        self.assertTrue(row["mayBeIncomplete"])

    def test_raw_secret_in_bsd_names_path_and_exception_is_withheld(self):
        self.backend.records[30] = bsd(30, secret=True)
        self.backend.paths[30] = path_packet("/private/" + SECRET)
        facts = self.observe()
        self.assertNotIn(SECRET, json.dumps(facts))
        self.assertNotIn("/private/", json.dumps(facts))
        self.backend = FakeBackend(self.app)
        self.backend.peer = RuntimeError(SECRET)
        self.assertNotIn(SECRET, json.dumps(self.observe()))

    def test_invalid_inputs_do_not_touch_backend(self):
        invalid = [("server_pid", True), ("gui_pid", 10), ("server_birth", list(self.tray_birth)),
                   ("server_birth", self.tray_birth[:-1] + (1_000_000,)), ("peer_pid_callback", None), ("app", Path("relative"))]
        for name, value in invalid:
            with self.subTest(name=name):
                self.backend = FakeBackend(self.app)
                facts = self.observe(**{name: value})
                self.assertEqual((facts["outcome"], facts["error"]), ("failed", "input"))
                self.assertEqual(self.backend.calls, [])

    def test_expected_main_and_service_must_both_be_canonical_regular_files(self):
        for member in ("Mixel-Remote", "service"):
            alternative = Path(self.temporary.name).resolve() / ("noncanonical-" + member + ".app")
            (alternative / "Contents/MacOS").mkdir(parents=True)
            for name in ("Mixel-Remote", "service"):
                target = alternative / "Contents/MacOS" / name
                if name == member:
                    target.symlink_to(self.app / "Contents/MacOS" / name)
                else:
                    target.write_bytes(b"fixture")
            main = str(alternative / "Contents/MacOS/Mixel-Remote")
            facts = self.observe(app=alternative,
                                 server_birth=self.tray_birth[:3] + (main,) + self.tray_birth[4:],
                                 gui_birth=self.gui_birth[:3] + (main,) + self.gui_birth[4:])
            self.assertEqual(facts["error"], "input")
            self.assertEqual(self.backend.calls, [])

    def test_expected_service_directory_is_not_a_regular_executable(self):
        service = self.app / "Contents/MacOS/service"
        service.rename(service.with_name("service.saved"))
        service.mkdir()
        self.assertEqual(self.observe()["error"], "input")
        self.assertEqual(self.backend.calls, [])

    def test_native_default_is_gated_before_loading(self):
        with mock.patch.object(observer.sys, "platform", "linux"), mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_OS": "macOS"}):
            facts = self.observe(backend=None)
        self.assertEqual(facts["error"], "gate")

    def test_native_gate_requires_both_runner_environment_values(self):
        for values in ({"GITHUB_ACTIONS": "false", "RUNNER_OS": "macOS"}, {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux"}):
            with mock.patch.object(observer.sys, "platform", "darwin"), mock.patch.dict(os.environ, values):
                self.assertEqual(self.observe(backend=None)["error"], "gate")

    def test_backend_start_failure_returns_fixed_schema(self):
        self.backend.now = mock.Mock(side_effect=RuntimeError(SECRET))
        facts = self.observe()
        self.assertEqual((facts["outcome"], facts["error"]), ("failed", "backend"))
        self.assertNotIn(SECRET, json.dumps(facts))

    def test_callback_baseexceptions_cannot_replace_primary_caller_failure(self):
        for error in (SystemExit(SECRET), KeyboardInterrupt(SECRET)):
            self.backend = FakeBackend(self.app)
            self.backend.peer = error
            facts = self.observe()
            self.assertEqual((facts["outcome"], facts["error"]), ("failed", "unexpected"))
            self.assertNotIn(SECRET, json.dumps(facts))
            self.assertTrue(all(connection.closed for connection in self.backend.connections))

    def test_no_native_or_mutation_authority_in_source(self):
        import ast
        tree = ast.parse(SOURCE.read_bytes())
        forbidden = {"send", "sendall", "recv", "kill", "unlink", "remove", "rmdir", "proc_listallpids", "proc_listpidspath", "proc_pidfdinfo"}
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertTrue(calls.isdisjoint(forbidden))
        self.assertNotIn("NSWorkspace", SOURCE.read_text())

    def test_validate_facts_is_pure_and_exact_no_extra_keys(self):
        baseline = self.observe()
        before = len(self.backend.calls)
        self.assertTrue(observer.validate_facts(copy.deepcopy(baseline)))
        self.assertEqual(len(self.backend.calls), before)
        invalid = copy.deepcopy(baseline)
        invalid["secret"] = SECRET
        with self.assertRaisesRegex(ValueError, "^Unsafe Mac incoming observation facts$"):
            observer.validate_facts(invalid)

    def test_validator_rejects_free_strings_bool_numbers_and_authority_claims(self):
        baseline = self.observe()
        mutations = [((), "schema", True), ((), "scope", SECRET), ((), "cleanupAuthority", True),
                     ((), "fdOwnershipEstablished", True), ((), "recoveryEstablished", True),
                     (("samples", 0, "tray"), "pid", True), (("samples", 0, "tray"), "state", SECRET),
                     (("samples", 0, "peer"), "reason", SECRET), (("samples", 0, "peer"), "uid", -1),
                     (("samples", 0, "inventory"), "mayBeIncomplete", 1), (("samples", 0), "elapsedMs", float("nan"))]
        for path, name, value in mutations:
            with self.subTest(name=name):
                invalid = copy.deepcopy(baseline)
                node = invalid
                for key in path:
                    node = node[key]
                node[name] = value
                with self.assertRaises(ValueError):
                    observer.validate_facts(invalid)

    def test_validator_rejects_semantically_false_empty_inventory(self):
        facts = self.observe()
        row = facts["samples"][0]["inventory"]
        row.update(enumerated=None, examined=0, omitted=0, members=[])
        with self.assertRaises(ValueError):
            observer.validate_facts(facts)

    def test_validator_rejects_mismatched_peer_and_unbounded_members(self):
        for change in ("peer", "members"):
            facts = self.observe()
            if change == "peer":
                facts["samples"][0]["peer"]["pid"] = 31
            else:
                members = facts["samples"][0]["inventory"]["members"]
                members.extend([copy.deepcopy(members[0])] * 33)
            with self.assertRaises(ValueError):
                observer.validate_facts(facts)

    def test_validator_connected_peer_requires_credentials_and_matching_uid(self):
        for field, value in (("credentialsState", "unavailable"), ("uid", 0)):
            facts = self.observe()
            peer = facts["samples"][0]["peer"]
            peer[field] = value
            if field == "credentialsState":
                peer["uid"] = peer["gid"] = None
            with self.assertRaises(ValueError):
                observer.validate_facts(facts)

    def test_validator_kernel_only_cannot_claim_an_executable(self):
        self.backend.paths[20] = (0, bytes(4096), errno.ESRCH)
        facts = self.observe()
        facts["samples"][0]["gui"]["executable"] = "main"
        with self.assertRaises(ValueError):
            observer.validate_facts(facts)

    def test_validator_stable_and_retained_birth_match_require_equal_uid_fields(self):
        for kernel_only in (False, True):
            self.backend = FakeBackend(self.app)
            if kernel_only:
                self.backend.paths[20] = (0, bytes(4096), errno.ESRCH)
            facts = self.observe()
            facts["samples"][0]["gui"]["ruid"] = 0
            with self.assertRaises(ValueError):
                observer.validate_facts(facts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
