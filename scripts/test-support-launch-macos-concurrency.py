#!/usr/bin/env python3
"""Additive packaged Mac two-request ownership fixture on an isolated CI runner.

The original single URL/tray fixture runs first. Three fresh scenarios here ask
LaunchServices for ordinary+support, two support opens in one Swift turn,
and a single activation carrying two support URLs.
They prove bounded GUI/process ownership and readiness, not invite consumption,
customer consent, TCC permissions, remote desktop, or a physical Mac session.
"""
import argparse
import ctypes
import errno
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import plistlib
import pwd
import socket
import stat
import subprocess
import sys
import tempfile
import time
import types

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve(strict=True).parent
MANIFEST_HASH = "c7352ef1d6628035b4c275bed5f16def67fffb13b3002668bf81f837c4f71a45"
LISTENER_OBSERVER_HASH = "7b0397d0630d1b61d81dd922f27f84eed66213b406a4c22fa63406d80e9dddbf"
SCENARIOS = ("ordinary-and-support", "two-support", "batch-two-support")
STABILITY_SECONDS = 5.0
MAX_INVENTORY = 32
INCOMING_SETTLEMENT_SECONDS = 5.0
INCOMING_SETTLEMENT_SAMPLES = 50
INCOMING_SETTLEMENT_CONNECT_SECONDS = 0.2
INCOMING_SETTLEMENT_POLL_SECONDS = 0.1
INCOMING_INITIAL_IDENTITY_SUBPHASES = frozenset((
    "post-connect-lease-guard", "peer-pid-first", "peer-uid-first",
    "peer-pid-comparison", "peer-uid-comparison", "peer-pid-recheck",
    "peer-uid-recheck", "post-peer-lease-guard", "post-close-lease-guard"))
INCOMING_INITIAL_IDENTITY_REASONS = frozenset((
    "unknown-error", "lease-guard-refused", "peer-pid-nonpositive",
    "peer-pid-unavailable", "peer-uid-unavailable", "peer-pid-mismatch",
    "peer-uid-mismatch", "peer-pid-changed", "peer-uid-changed", "deadline"))
INCOMING_INITIAL_IDENTITY_ERROR_TYPES = frozenset(("OSError", "ValueError", "SubprocessError", "RuntimeError"))
INCOMING_INITIAL_PID_COMPARISONS = frozenset(("not-read", "expected", "foreign", "nonpositive", "unavailable"))
INCOMING_INITIAL_UID_COMPARISONS = frozenset(("not-read", "expected", "foreign", "unavailable"))
INCOMING_INITIAL_IDENTITY_ERRNOS = frozenset((
    errno.ENOTCONN, errno.ESRCH, errno.ENOENT, errno.EPERM, errno.EACCES,
    errno.EBADF, errno.EINVAL, errno.EINTR, errno.EAGAIN, errno.EIO,
    errno.ECONNRESET, errno.ECONNREFUSED, errno.ETIMEDOUT, errno.ENOTSOCK,
    errno.ENOTSUP))
ERRORS = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)

# Exact messages from the frozen endpoint/PID reader only; never serialize them.
# The caller cannot distinguish the two same-message socket rechecks.
ENDPOINT_CLEANUP_REASONS = {
    "Pre-existing IPC path is not this runner's socket": ("ipc-path-unsafe", "endpoint-inactivity-check"),
    "A Mixel IPC endpoint is already active; refusing tray fixture": ("ipc-endpoint-active", "endpoint-inactivity-check"),
    "Diagnostic file is not an owned regular file": ("pid-marker-unsafe", "pid-marker-read"),
    "Diagnostic file exceeds its byte bound": ("pid-marker-oversized", "pid-marker-read"),
    "Diagnostic file changed during open": ("pid-marker-open-race", "pid-marker-read"),
    "Diagnostic file changed during read": ("pid-marker-read-race", "pid-marker-read"),
    "Tray fixture cleanup process identity changed": ("pid-marker-identity-changed", "pid-marker-identity-check"),
    "Tray fixture cleanup socket identity changed": ("socket-identity-changed", "socket-identity-check"),
}
ENDPOINT_CLEANUP_MESSAGE_LIMIT = 256


def canonical(raw):
    text = raw.decode("utf-8")
    if text.startswith("\ufeff") or "\r" in text.replace("\r\n", ""):
        raise RuntimeError("Invalid concurrency fixture source encoding")
    ending = "\r\n" if "\r\n" in text else "\n"
    if text != text.replace("\r\n", "\n").replace("\n", ending):
        raise RuntimeError("Mixed concurrency fixture source newlines")
    return text.replace("\r\n", "\n").encode()


def safe_source(path):
    if path != path.resolve(strict=True):
        raise RuntimeError("Concurrency source must be canonical")
    for parent in path.parents:
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise RuntimeError("Concurrency source parent must be a directory")
    value = path.lstat()
    if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1
            or value.st_uid != os.getuid() or value.st_size > 1_000_000):
        raise RuntimeError("Concurrency source must be owned bounded regular source")
    return canonical(path.read_bytes())


def load_original():
    manifest = safe_source(SCRIPTS / "failed-macos-diagnostic-helper-hashes.json")
    if hashlib.sha256(manifest).hexdigest() != MANIFEST_HASH:
        raise RuntimeError("Historical Mac helper manifest changed")
    table = json.loads(manifest)
    for relative, expected in table.items():
        if (not relative.startswith("scripts/") or Path(relative).name != relative[8:]
                or not isinstance(expected, str) or len(expected) != 64):
            raise RuntimeError("Historical Mac helper manifest is invalid")
        if hashlib.sha256(safe_source(SCRIPTS / Path(relative).name)).hexdigest() != expected:
            raise RuntimeError("Historical Mac helper source changed")
    spec = importlib.util.spec_from_file_location("mixel_concurrency_original_tray", SCRIPTS / "test-support-launch-macos-tray.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


tray = load_original()
desktop = tray.desktop
unix = tray.unix

ORIGINAL_DESKTOP_SWIFT = desktop.SWIFT
ORIGINAL_TRAY_SWIFT = tray.tray_swift(ORIGINAL_DESKTOP_SWIFT)
SWIFT_STORAGE = '\n// NSWorkspace completions run on a concurrent queue. All shared state is locked.\nfinal class MixelCompletionStore: @unchecked Sendable {\n    private let lock = NSLock()\n    private var slots: [[String: Any]?]\n    init(count: Int) { precondition(count == 1 || count == 2); slots = Array(repeating: nil, count: count) }\n    @discardableResult\n    func record(_ index: Int, _ value: [String: Any]) -> Bool {\n        lock.lock(); defer { lock.unlock() }\n        guard slots.indices.contains(index), slots[index] == nil else { return false }\n        slots[index] = value\n        return true\n    }\n    func snapshot() -> [[String: Any]?] {\n        lock.lock(); defer { lock.unlock() }\n        return slots\n    }\n}\n'
SINGLE_CALLBACK_REPLACEMENTS = (('    var finished = false\n    var result: [String: Any] = ["error": "App URL activation timed out"]\n    let completion: (NSRunningApplication?, Error?) -> Void = { app, error in\n        if let app = app, error == nil {\n            result = ["pid": app.processIdentifier,\n                      "bundlePath": app.bundleURL?.resolvingSymlinksInPath().path ?? "",\n                      "launchTime": app.launchDate?.timeIntervalSince1970 ?? 0]\n        } else {\n            result = ["error": "App URL activation failed"]\n        }\n        finished = true\n    }\n', '    let resultStore = MixelCompletionStore(count: 1)\n    let completion: (NSRunningApplication?, Error?) -> Void = { app, error in\n        let result: [String: Any]\n        if let app = app, error == nil {\n            result = ["pid": app.processIdentifier,\n                      "bundlePath": app.bundleURL?.resolvingSymlinksInPath().path ?? "",\n                      "launchTime": app.launchDate?.timeIntervalSince1970 ?? 0]\n        } else {\n            result = ["error": "App URL activation failed"]\n        }\n        resultStore.record(0, result)\n    }\n'), ('    while !finished && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.05)) }\n    emit(result)\n    exit(result["pid"] == nil ? 1 : 0)', '    while resultStore.snapshot()[0] == nil && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.05)) }\n    let result = resultStore.snapshot()[0] ?? ["error": "App URL activation timed out"]\n    emit(result)\n    exit(result["pid"] == nil ? 1 : 0)'))

SWIFT_BLOCK = r'''
// Additive driver: the exact single-launch callback below is projected to locked storage.
if action == "inventory" {
    if arguments.count != 3 { exit(2) }
    let application = URL(fileURLWithPath: arguments[2]).resolvingSymlinksInPath()
    let running = NSWorkspace.shared.runningApplications
    if running.count > 512 { emit(["error": "Process inventory exceeds bound"]); exit(1) }
    let pids = running.filter { $0.bundleURL?.resolvingSymlinksInPath() == application }
                      .map { $0.processIdentifier }.sorted()
    emit(["pids": pids]); exit(0)
}
if action == "ordinary-and-support" || action == "two-support" || action == "batch-two-support" {
    if arguments.count != (action == "ordinary-and-support" ? 4 : 5) { exit(2) }
    let application = URL(fileURLWithPath: arguments[2]).resolvingSymlinksInPath()
    guard let firstURL = URL(string: arguments[3]) else { exit(2) }
    let secondURL = action != "ordinary-and-support" ? URL(string: arguments[4]) : nil
    if action != "ordinary-and-support" && secondURL == nil { exit(2) }
    let configuration = NSWorkspace.OpenConfiguration()
    configuration.activates = true
    configuration.createsNewApplicationInstance = false
    configuration.allowsRunningApplicationSubstitution = false
    configuration.addsToRecentItems = false
    let expectedResults = action == "batch-two-support" ? 1 : 2
    let results = MixelCompletionStore(count: expectedResults)
    func completion(_ index: Int) -> (NSRunningApplication?, Error?) -> Void {
        return { app, error in
            if let app = app, error == nil {
                results.record(index, ["pid": app.processIdentifier,
                                       "bundlePath": app.bundleURL?.resolvingSymlinksInPath().path ?? "",
                                       "launchTime": app.launchDate?.timeIntervalSince1970 ?? 0])
            } else { results.record(index, ["error": "Concurrent activation failed"]) }
        }
    }
    // The two-request scenarios schedule consecutively in the same Swift turn.
    // The batch scenario submits both URLs in one call; no intermediate wait.
    if action == "ordinary-and-support" {
        NSWorkspace.shared.openApplication(at: application, configuration: configuration, completionHandler: completion(0))
        NSWorkspace.shared.open([firstURL], withApplicationAt: application, configuration: configuration, completionHandler: completion(1))
    } else if action == "batch-two-support" {
        NSWorkspace.shared.open([firstURL, secondURL!], withApplicationAt: application, configuration: configuration, completionHandler: completion(0))
    } else {
        NSWorkspace.shared.open([firstURL], withApplicationAt: application, configuration: configuration, completionHandler: completion(0))
        NSWorkspace.shared.open([secondURL!], withApplicationAt: application, configuration: configuration, completionHandler: completion(1))
    }
    let deadline = Date().addingTimeInterval(30)
    while results.snapshot().contains(where: { $0 == nil }) && Date() < deadline {
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
    }
    let completed = results.snapshot().compactMap { $0 }
    if completed.count != expectedResults || completed.contains(where: { $0["pid"] == nil }) {
        emit(["error": "Concurrent activation did not complete"]); exit(1)
    }
    emit(["completions": completed]); exit(0)
}
'''


def thread_safe_swift(source):
    if source not in (ORIGINAL_DESKTOP_SWIFT, ORIGINAL_TRAY_SWIFT):
        raise RuntimeError("Original single-launch Swift source changed")
    for before, after in SINGLE_CALLBACK_REPLACEMENTS:
        if source.count(before) != 1:
            raise RuntimeError("Original single-launch callback anchor changed")
        source = source.replace(before, after, 1)
    anchor = "import Foundation\n"
    if source.count(anchor) != 1:
        raise RuntimeError("Original Swift Foundation import changed")
    return source.replace(anchor, anchor + SWIFT_STORAGE, 1)


def inverse_thread_safe_swift(source):
    if source.count(SWIFT_STORAGE) != 1:
        raise RuntimeError("Locked Swift storage changed")
    source = source.replace(SWIFT_STORAGE, "", 1)
    for before, after in SINGLE_CALLBACK_REPLACEMENTS:
        if source.count(after) != 1:
            raise RuntimeError("Locked single-launch callback changed")
        source = source.replace(after, before, 1)
    if source not in (ORIGINAL_DESKTOP_SWIFT, ORIGINAL_TRAY_SWIFT):
        raise RuntimeError("Swift projection is outside the original profiles")
    return source


def concurrency_swift():
    original = thread_safe_swift(ORIGINAL_TRAY_SWIFT)
    anchor = "let action = arguments[1]\n"
    if original.count(anchor) != 1:
        raise RuntimeError("Original Mac Swift driver anchor changed")
    return original.replace(anchor, anchor + SWIFT_BLOCK, 1)


def compile_source(directory, contents):
    directory = directory.resolve(strict=True)
    source = directory / "concurrency.swift"
    source.write_text(contents, encoding="utf-8", newline="\n")
    helper = directory / "concurrency"
    cache = directory / "swift-module-cache"
    cache.mkdir(mode=0o700)
    result = subprocess.run(["xcrun", "swiftc", "-module-cache-path", str(cache), "-warnings-as-errors", str(source), "-o", str(helper)],
                            capture_output=True, timeout=60)
    (directory / "compile.stdout").write_bytes(result.stdout)
    (directory / "compile.stderr").write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError("Concurrency Swift driver compilation failed")
    return helper


def compile_helper(directory):
    return compile_source(directory, concurrency_swift())


def run_original(mode, arguments):
    if mode not in ("--original-desktop", "--original-tray"):
        raise RuntimeError("Unknown original Mac wrapper mode")
    tray.isolated_ci()
    original_compiler, original_swift, original_argv = desktop.compile_helper, desktop.SWIFT, sys.argv
    try:
        desktop.compile_helper = lambda directory: compile_source(directory, thread_safe_swift(desktop.SWIFT))
        sys.argv = [str(SCRIPTS / ("test-support-launch-macos.py" if mode == "--original-desktop" else "test-support-launch-macos-tray.py")), *arguments]
        return desktop.main() if mode == "--original-desktop" else tray.main()
    finally:
        desktop.compile_helper, desktop.SWIFT, sys.argv = original_compiler, original_swift, original_argv


def error_fact(error):
    # No raw exceptions, URL, log text, app paths or subprocess payloads.
    category = ("filesystem" if isinstance(error, OSError) else
                "validation" if isinstance(error, ValueError) else
                "subprocess" if isinstance(error, subprocess.SubprocessError) else "runtime")
    kind = {"filesystem": "OSError", "validation": "ValueError",
            "subprocess": "SubprocessError", "runtime": "RuntimeError"}[category]
    return {"reasonCategory": category, "errorType": kind}


def endpoint_cleanup_fact(error):
    reason_code, subphase = "unclassified-cleanup-error", "endpoint-cleanup"
    # Inspect only one bounded plain string from the exact exception class.
    # Unknown objects/subclasses must not invoke __str__ or replace the failure.
    if (type(error) is RuntimeError and len(error.args) == 1
            and type(error.args[0]) is str
            and len(error.args[0]) <= ENDPOINT_CLEANUP_MESSAGE_LIMIT):
        reason_code, subphase = ENDPOINT_CLEANUP_REASONS.get(error.args[0], (reason_code, subphase))
    return {**error_fact(error), "reasonCode": reason_code, "subphase": subphase}


def observe_incoming_after_stop(app, server_pid, server_birth, gui_pid, birth):
    path = SCRIPTS / "observe-macos-incoming-cleanup.py"
    source = safe_source(path)
    if hashlib.sha256(source).hexdigest() != LISTENER_OBSERVER_HASH:
        raise RuntimeError("Mac incoming observer source changed")
    observer = types.ModuleType("mixel_readonly_incoming_cleanup_observer")
    observer.__file__ = str(path)
    exec(compile(source, str(path), "exec"), observer.__dict__)
    facts = observer.observe(app, server_pid, server_birth, gui_pid, birth, tray.peer_pid)
    if observer.validate_facts(facts) is not True:
        raise RuntimeError("Mac incoming observer facts invalid")
    return facts


def completion_matches(value, app, server_pid, launch_time, expected_count=2):
    rows = value.get("completions") if isinstance(value, dict) else None
    if expected_count not in (1, 2) or not isinstance(rows, list) or len(rows) != expected_count:
        raise RuntimeError("Concurrent activation has an invalid completion count")
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"pid", "bundlePath", "launchTime"}
                or type(row["pid"]) is not int or row["pid"] != server_pid
                or type(row["bundlePath"]) is not str or row["bundlePath"] != str(app)
                or type(row["launchTime"]) not in (int, float)
                or not math.isfinite(row["launchTime"]) or row["launchTime"] != launch_time):
            raise RuntimeError("Concurrent activation bypassed original tray identity")


def inventory(helper, app, allowed, facts):
    value = desktop.helper_request(helper, "inventory", str(app))
    pids = value.get("pids") if isinstance(value, dict) else None
    if (not isinstance(pids, list) or len(pids) > MAX_INVENTORY
            or any(type(pid) is not int or pid <= 0 for pid in pids)
            or len(set(pids)) != len(pids)):
        raise RuntimeError("Exact-bundle process inventory is invalid")
    unknown = sorted(set(pids) - set(allowed))
    facts["inventorySamples"] = facts.get("inventorySamples", 0) + 1
    if unknown:
        facts["unknownSameBundlePids"] = unknown
        raise RuntimeError("An additional exact-bundle process appeared")
    if not set(allowed).issubset(pids):
        raise RuntimeError("A retained exact-bundle process disappeared")


def server_same(helper, app, server_pid, launch_time, birth):
    if (tray.launch_identity(helper, app, server_pid) != launch_time
            or tray.gui_birth(app, server_pid) != birth):
        raise RuntimeError("Original tray identity changed")
    if not tray.no_connections(server_pid, app):
        raise RuntimeError("Concurrency fixture has an authenticated connection")


def observe(helper, app, server_pid, launch_time, server_birth, gui_pid, birth, facts):
    server_same(helper, app, server_pid, launch_time, server_birth)
    owner = tray.gui_owner(app, server_pid)
    if owner != gui_pid:
        facts["unknownUrlOwnerPid"] = owner
        raise RuntimeError("Retained GUI URL owner changed")
    if tray.gui_birth(app, gui_pid) != birth:
        raise RuntimeError("Retained GUI kernel birth changed")
    inventory(helper, app, (server_pid, gui_pid), facts)
    visible = desktop.probe(helper, gui_pid, app).get("visible")
    return visible is True and tray.health(server_pid, app, True)


class IncomingSettlementDeadline(RuntimeError):
    pass


def incoming_settlement_call(deadline, operation, *arguments, **keywords):
    before = time.monotonic()
    if not math.isfinite(before) or before >= deadline:
        raise IncomingSettlementDeadline("Incoming cleanup settlement deadline reached")
    try:
        result = operation(*arguments, **keywords)
    except BaseException as error:
        after = time.monotonic()
        if not math.isfinite(after) or after < before or after >= deadline:
            raise IncomingSettlementDeadline("Incoming cleanup settlement deadline reached") from error
        raise
    after = time.monotonic()
    if not math.isfinite(after) or after < before or after >= deadline:
        raise IncomingSettlementDeadline("Incoming cleanup settlement deadline reached")
    return result


def incoming_cleanup_call(deadline, operation, *arguments, **keywords):
    return operation(*arguments, **keywords) if deadline is None else incoming_settlement_call(deadline, operation, *arguments, **keywords)


def incoming_peer_uid(connection):
    # Exact readonly getpeereid primitive used by the producer's GUI verifier.
    library = ctypes.CDLL(None, use_errno=True)
    library.getpeereid.argtypes = (ctypes.c_int, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32))
    library.getpeereid.restype = ctypes.c_int
    uid, gid = ctypes.c_uint32(0xffffffff), ctypes.c_uint32(0xffffffff)
    ctypes.set_errno(0)
    if library.getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        error = RuntimeError("Incoming cleanup peer credentials unavailable")
        try:
            number = ctypes.get_errno()
        except BaseException:
            number = None
        error._mixel_incoming_errno = number if type(number) is int and number in INCOMING_INITIAL_IDENTITY_ERRNOS else None
        raise error
    return uid.value


def incoming_cleanup_inputs(app, server_pid, server_birth, gui_pid, birth):
    uid = os.getuid()
    if (type(uid) is not int or not 0 < uid <= 0xffffffff
            or type(server_pid) is not int or not 0 < server_pid <= 0x7fffffff
            or type(gui_pid) is not int or not 0 < gui_pid <= 0x7fffffff or server_pid == gui_pid):
        raise RuntimeError("Incoming cleanup retained identity invalid")
    expected = str(app / "Contents/MacOS/Mixel-Remote")
    for pid, retained in ((server_pid, server_birth), (gui_pid, birth)):
        if (type(retained) is not tuple or len(retained) != 6
                or retained[0] != "darwin-gui-birth-v1" or type(retained[1]) is not int or retained[1] != pid
                or type(retained[2]) is not int or retained[2] != uid
                or type(retained[3]) is not str or retained[3] != expected
                or type(retained[4]) is not int or retained[4] <= 0
                or type(retained[5]) is not int or not 0 <= retained[5] < 1_000_000):
            raise RuntimeError("Incoming cleanup retained identity invalid")
    return uid


def incoming_cleanup_snapshot(deadline=None, allow_missing=False, expected=None, missing=None):
    endpoint = unix.IPC_PATH
    directory = endpoint.parent
    marker = endpoint.with_suffix(".pid")

    missing = {name: False for name in ("directory", "socket", "marker")} if missing is None else missing

    def absent(name):
        if not allow_missing:
            raise RuntimeError("Incoming cleanup leased resource missing")
        missing[name] = True
        return None

    def attributes(name, path):
        try:
            value = incoming_cleanup_call(deadline, path.lstat)
        except FileNotFoundError:
            return absent(name)
        if missing[name]:
            raise RuntimeError("Incoming cleanup leased resource reappeared")
        return value

    def directory_identity():
        value = attributes("directory", directory)
        if value is None:
            return None
        if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.getuid():
            raise RuntimeError("Incoming cleanup namespace identity refused")
        identity = (value.st_dev, value.st_ino, value.st_mode, value.st_uid)
        if expected is not None and identity != expected["directory"]:
            raise RuntimeError("Incoming cleanup namespace identity changed")
        return identity

    def socket_identity():
        value = attributes("socket", endpoint)
        if value is None:
            return None
        if not stat.S_ISSOCK(value.st_mode) or value.st_uid != os.getuid():
            raise RuntimeError("Incoming cleanup socket identity refused")
        identity = desktop.file_identity(value)
        if expected is not None and identity != expected["socket"]:
            raise RuntimeError("Incoming cleanup socket identity changed")
        return identity

    def marker_identity():
        value = attributes("marker", marker)
        if value is None:
            return None, None
        if not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid() or value.st_nlink != 1 or value.st_size > 64:
            raise RuntimeError("Incoming cleanup PID marker identity refused")
        identity = desktop.file_identity(value)
        if expected is not None and identity != expected["marker"]:
            raise RuntimeError("Incoming cleanup PID marker identity changed")
        try:
            data, read_attributes = incoming_cleanup_call(deadline, desktop.bounded_read, marker, 64)
        except FileNotFoundError:
            return absent("marker"), None
        if (type(data) is not bytes or len(data) > 64 or desktop.file_identity(read_attributes) != identity
                or (expected is not None and data != expected["markerBytes"])):
            raise RuntimeError("Incoming cleanup PID marker identity changed")
        after = attributes("marker", marker)
        if after is None:
            return None, None
        if desktop.file_identity(after) != identity:
            raise RuntimeError("Incoming cleanup PID marker identity changed")
        return identity, data

    first_directory = directory_identity()
    first_socket = socket_identity()
    first_marker, first_data = marker_identity()
    final_marker, final_data = marker_identity()
    final_socket = socket_identity()
    final_directory = directory_identity()
    for previous, current in ((first_directory, final_directory), (first_socket, final_socket), (first_marker, final_marker)):
        if current is not None and current != previous:
            raise RuntimeError("Incoming cleanup leased resource changed")
    if final_marker is not None and final_data != first_data:
        raise RuntimeError("Incoming cleanup PID marker identity changed")
    if final_directory is None and (final_socket is not None or final_marker is not None):
        raise RuntimeError("Incoming cleanup namespace absence is ambiguous")
    return {"directory": final_directory, "socket": final_socket,
            "marker": final_marker, "markerBytes": final_data}


def incoming_cleanup_lease_guard(app, server_pid, server_birth, gui_pid, birth, lease, terminal=False, deadline=None):
    uid = incoming_cleanup_inputs(app, server_pid, server_birth, gui_pid, birth)
    if (type(lease) is not dict or set(lease) != {"schema", "app", "uid", "serverPid", "serverBirth", "guiPid", "guiBirth", "capturedBeforeStop", "snapshot", "missing"}
            or type(lease["schema"]) is not int or lease["schema"] != 1
            or lease["app"] != app or type(lease["uid"]) is not int or lease["uid"] != uid
            or type(lease["serverPid"]) is not int or lease["serverPid"] != server_pid
            or lease["serverBirth"] != server_birth or type(lease["guiPid"]) is not int or lease["guiPid"] != gui_pid
            or lease["guiBirth"] != birth or lease["capturedBeforeStop"] is not True
            or type(lease["snapshot"]) is not dict or set(lease["snapshot"]) != {"directory", "socket", "marker", "markerBytes"}
            or type(lease["snapshot"]["markerBytes"]) is not bytes
            or lease["snapshot"]["markerBytes"] != str(server_pid).encode("ascii")
            or type(lease["missing"]) is not dict or set(lease["missing"]) != {"directory", "socket", "marker"}
            or any(type(value) is not bool for value in lease["missing"].values())
            or (not terminal and any(lease["missing"].values()))):
        raise RuntimeError("Incoming cleanup lease identity refused")
    captured = lease["snapshot"]
    for name, length in (("directory", 4), ("socket", 7), ("marker", 7)):
        value = captured[name]
        if type(value) is not tuple or len(value) != length or any(type(field) is not int or field < 0 for field in value):
            raise RuntimeError("Incoming cleanup captured identity invalid")
        if value[3] != uid:
            raise RuntimeError("Incoming cleanup captured UID refused")
    if (not stat.S_ISDIR(captured["directory"][2]) or not stat.S_ISSOCK(captured["socket"][2])
            or not stat.S_ISREG(captured["marker"][2]) or captured["marker"][4] != 1
            or captured["marker"][5] != len(captured["markerBytes"])):
        raise RuntimeError("Incoming cleanup captured identity invalid")

    def snapshot_guard(value):
        if type(value) is not dict or set(value) != {"directory", "socket", "marker", "markerBytes"}:
            raise RuntimeError("Incoming cleanup observed identity invalid")
        for name in ("directory", "socket", "marker"):
            if value[name] is None:
                if not terminal:
                    raise RuntimeError("Incoming cleanup leased resource missing")
                lease["missing"][name] = True
            elif lease["missing"][name] or value[name] != captured[name]:
                raise RuntimeError("Incoming cleanup leased resource changed or reappeared")
        if ((value["marker"] is None and value["markerBytes"] is not None)
                or (value["marker"] is not None and value["markerBytes"] != captured["markerBytes"])
                or (value["directory"] is None and (value["socket"] is not None or value["marker"] is not None))):
            raise RuntimeError("Incoming cleanup observed identity invalid")

    if terminal:
        for pid, retained in ((server_pid, server_birth), (gui_pid, birth)):
            if incoming_cleanup_call(deadline, tray.gui_birth, app, pid, retained) is not None:
                raise RuntimeError("Incoming cleanup retained identity changed")
    before = incoming_cleanup_snapshot(deadline, terminal, lease["snapshot"], lease["missing"])
    snapshot_guard(before)
    for pid, retained in ((server_pid, server_birth), (gui_pid, birth)):
        observed = incoming_cleanup_call(deadline, tray.gui_birth, app, pid, retained) if terminal else incoming_cleanup_call(deadline, tray.gui_birth, app, pid)
        if (terminal and observed is not None) or (not terminal and observed != retained):
            raise RuntimeError("Incoming cleanup retained identity changed")
    after = incoming_cleanup_snapshot(deadline, terminal, lease["snapshot"], lease["missing"])
    snapshot_guard(after)
    for name in ("directory", "socket", "marker"):
        if after[name] is not None and after[name] != before[name]:
            raise RuntimeError("Incoming cleanup lease identity changed")
    return True


def capture_incoming_cleanup_lease(helper, app, server_pid, launch_time, server_birth, gui_pid, birth):
    uid = incoming_cleanup_inputs(app, server_pid, server_birth, gui_pid, birth)
    server_same(helper, app, server_pid, launch_time, server_birth)
    if tray.gui_birth(app, gui_pid) != birth:
        raise RuntimeError("Incoming cleanup GUI birth changed")
    snapshot = incoming_cleanup_snapshot()
    if snapshot["markerBytes"] != str(server_pid).encode("ascii"):
        raise RuntimeError("Incoming cleanup PID marker does not match owned tray")
    lease = {"schema": 1, "app": app, "uid": uid, "serverPid": server_pid,
             "serverBirth": server_birth, "guiPid": gui_pid, "guiBirth": birth,
             "capturedBeforeStop": True, "snapshot": snapshot,
             "missing": {"directory": False, "socket": False, "marker": False}}
    incoming_cleanup_lease_guard(app, server_pid, server_birth, gui_pid, birth, lease)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(INCOMING_SETTLEMENT_CONNECT_SECONDS)
        connection.connect(str(unix.IPC_PATH))
        peer = tray.peer_pid(connection)
        peer_uid = incoming_peer_uid(connection)
        if (type(peer) is not int or peer != server_pid or type(peer_uid) is not int or peer_uid != uid
                or tray.process_path(server_pid) != app / "Contents/MacOS/Mixel-Remote"):
            raise RuntimeError("Incoming cleanup live peer identity refused")
        incoming_cleanup_lease_guard(app, server_pid, server_birth, gui_pid, birth, lease)
        server_same(helper, app, server_pid, launch_time, server_birth)
        if tray.peer_pid(connection) != peer or incoming_peer_uid(connection) != peer_uid:
            raise RuntimeError("Incoming cleanup live peer identity changed")
    finally:
        connection.close()
    server_same(helper, app, server_pid, launch_time, server_birth)
    incoming_cleanup_lease_guard(app, server_pid, server_birth, gui_pid, birth, lease)
    return lease


def incoming_initial_identity_errno(error):
    # Only fixed numeric errno values are retained; exception text is never read.
    try:
        number = error.errno if isinstance(error, OSError) else (
            error.__dict__.get("_mixel_incoming_errno") if type(error) is RuntimeError else None)
    except BaseException:
        return None
    return number if type(number) is int and number in INCOMING_INITIAL_IDENTITY_ERRNOS else None


def incoming_initial_identity_comparison(value, expected, pid=False):
    if type(value) is not int:
        return "unavailable"
    if pid and value <= 0:
        return "nonpositive"
    return "expected" if value == expected else "foreign"


def incoming_initial_identity_failure_fact(error):
    # Validate the private attachment before copying it into public safe facts.
    try:
        if type(error) is not RuntimeError:
            return None
        value = error.__dict__.get("_mixel_incoming_initial_identity_failure")
        keys = {"subphase", "reason", "errorType", "errno", "peerPidComparison", "peerUidComparison"}
        if (type(value) is not dict or set(value) != keys
                or type(value["subphase"]) is not str or value["subphase"] not in INCOMING_INITIAL_IDENTITY_SUBPHASES
                or type(value["reason"]) is not str or value["reason"] not in INCOMING_INITIAL_IDENTITY_REASONS
                or type(value["errorType"]) is not str or value["errorType"] not in INCOMING_INITIAL_IDENTITY_ERROR_TYPES
                or (value["errno"] is not None and (type(value["errno"]) is not int or value["errno"] not in INCOMING_INITIAL_IDENTITY_ERRNOS))
                or type(value["peerPidComparison"]) is not str or value["peerPidComparison"] not in INCOMING_INITIAL_PID_COMPARISONS
                or type(value["peerUidComparison"]) is not str or value["peerUidComparison"] not in INCOMING_INITIAL_UID_COMPARISONS):
            return None
        return dict(value)
    except BaseException:
        return None


def incoming_endpoint_inactive(deadline, lease):
    # Run identical frozen inactivity code with a private timeout-only socket
    # view. Neither frozen function bytes nor its module globals are mutated.
    function = tray.endpoint_inactive
    if type(function) is not types.FunctionType:
        raise RuntimeError("Frozen incoming inactivity helper changed")
    sticky = {"code": "none", "identity": None}
    attribution = {"subphase": "post-connect-lease-guard", "reason": "unknown-error",
                   "peerPidComparison": "not-read", "peerUidComparison": "not-read"}

    def phase(subphase, reason="unknown-error"):
        attribution["subphase"], attribution["reason"] = subphase, reason

    def remember(error):
        code = "active-timeout" if isinstance(error, IncomingSettlementDeadline) else "identity-refused"
        previous = sticky["code"]
        if previous != "identity-refused":
            sticky["code"] = code
        if sticky["identity"] is not None and (previous == "identity-refused" or code != "identity-refused"):
            return
        reason = "deadline" if isinstance(error, IncomingSettlementDeadline) else attribution["reason"]
        pid_comparison = attribution["peerPidComparison"]
        uid_comparison = attribution["peerUidComparison"]
        subphase = attribution["subphase"]
        if reason == "unknown-error":
            if subphase in ("post-connect-lease-guard", "post-peer-lease-guard", "post-close-lease-guard"):
                reason = "lease-guard-refused"
            elif subphase in ("peer-pid-first", "peer-pid-recheck"):
                # This is the sole frozen literal for a nonpositive LOCAL_PEERPID.
                # No additional getsockopt call is made to classify the failure.
                nonpositive = (type(error) is RuntimeError and len(error.args) == 1
                    and type(error.args[0]) is str and error.args[0] == "Fixture IPC has no kernel process owner")
                reason = "peer-pid-nonpositive" if nonpositive else "peer-pid-unavailable"
                pid_comparison = "nonpositive" if nonpositive else "unavailable"
            elif subphase in ("peer-uid-first", "peer-uid-recheck"):
                reason, uid_comparison = "peer-uid-unavailable", "unavailable"
        sticky["identity"] = {"subphase": subphase, "reason": reason,
            "errorType": error_fact(error)["errorType"], "errno": incoming_initial_identity_errno(error),
            "peerPidComparison": pid_comparison, "peerUidComparison": uid_comparison}

    def guard():
        return incoming_cleanup_lease_guard(lease["app"], lease["serverPid"], lease["serverBirth"], lease["guiPid"], lease["guiBirth"], lease, terminal=True, deadline=deadline)

    class BoundedSocket:
        def __init__(self, family, kind):
            if (family, kind) != (socket.AF_UNIX, socket.SOCK_STREAM):
                raise RuntimeError("Incoming cleanup socket scope refused")
            guard()
            self.closed_peer_qualified = False
            self.connection = incoming_settlement_call(deadline, socket.socket, family, kind)

        def __enter__(self):
            return self

        def __exit__(self, *arguments):
            try:
                self.connection.close()
            except BaseException as error:
                phase("post-close-lease-guard")
                remember(error)
            try:
                phase("post-close-lease-guard")
                guard()
                if self.closed_peer_qualified and any(lease["missing"].values()):
                    raise RuntimeError("Incoming cleanup disconnected peer resource missing")
            except BaseException as error:
                remember(error)

        def settimeout(self, value):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IncomingSettlementDeadline("Incoming cleanup settlement deadline reached")
            incoming_settlement_call(deadline, self.connection.settimeout, min(float(value), INCOMING_SETTLEMENT_CONNECT_SECONDS, remaining))

        def connect(self, path):
            if path != str(unix.IPC_PATH):
                raise RuntimeError("Incoming cleanup socket scope refused")
            self.settimeout(INCOMING_SETTLEMENT_CONNECT_SECONDS)
            result = incoming_settlement_call(deadline, self.connection.connect, path)
            try:
                phase("post-connect-lease-guard")
                guard()
                phase("peer-pid-first")
                try:
                    first_pid = incoming_settlement_call(deadline, tray.peer_pid, self.connection)
                except OSError as first_error:
                    # Darwin retains cached credentials on a connected client
                    # after its peer closes, but LOCAL_PEERPID then raises
                    # ENOTCONN. This permits only a separate bounded probe;
                    # neither this connection nor its cached UID is inactivity.
                    if type(first_error) is not OSError or incoming_initial_identity_errno(first_error) != errno.ENOTCONN:
                        raise
                    attribution["peerPidComparison"] = "unavailable"

                    def closed_peer_guard():
                        phase("post-peer-lease-guard")
                        guard()
                        if any(lease["missing"].values()):
                            raise RuntimeError("Incoming cleanup disconnected peer resource missing")

                    closed_peer_guard()
                    phase("peer-uid-first")
                    closed_uid = incoming_settlement_call(deadline, incoming_peer_uid, self.connection)
                    attribution["peerUidComparison"] = incoming_initial_identity_comparison(closed_uid, lease["uid"])
                    if type(closed_uid) is not int or closed_uid != lease["uid"]:
                        phase("peer-uid-comparison", "peer-uid-mismatch" if type(closed_uid) is int else "peer-uid-unavailable")
                        raise RuntimeError("Incoming cleanup disconnected peer UID refused")
                    closed_peer_guard()
                    phase("peer-pid-recheck")
                    try:
                        observed_pid = incoming_settlement_call(deadline, tray.peer_pid, self.connection)
                    except OSError as second_error:
                        if type(second_error) is not OSError or incoming_initial_identity_errno(second_error) != errno.ENOTCONN:
                            raise
                    else:
                        attribution["peerPidComparison"] = incoming_initial_identity_comparison(observed_pid, lease["serverPid"], pid=True)
                        phase("peer-pid-recheck", "peer-pid-changed")
                        raise RuntimeError("Incoming cleanup disconnected peer PID became available")
                    closed_peer_guard()
                    phase("peer-uid-recheck")
                    observed_uid = incoming_settlement_call(deadline, incoming_peer_uid, self.connection)
                    attribution["peerUidComparison"] = incoming_initial_identity_comparison(observed_uid, lease["uid"])
                    if type(observed_uid) is not int or observed_uid != closed_uid:
                        phase("peer-uid-recheck", "peer-uid-changed")
                        raise RuntimeError("Incoming cleanup disconnected peer UID changed")
                    closed_peer_guard()
                    # Return the actual successful connect to the frozen
                    # helper, which still creates its original active error.
                    self.closed_peer_qualified = True
                    return result
                attribution["peerPidComparison"] = incoming_initial_identity_comparison(first_pid, lease["serverPid"], pid=True)
                phase("peer-uid-first")
                first_uid = incoming_settlement_call(deadline, incoming_peer_uid, self.connection)
                attribution["peerUidComparison"] = incoming_initial_identity_comparison(first_uid, lease["uid"])

                def pid_refused():
                    phase("peer-pid-comparison")
                    comparison = incoming_initial_identity_comparison(first_pid, lease["serverPid"], pid=True)
                    attribution["peerPidComparison"] = comparison
                    attribution["reason"] = ("peer-pid-nonpositive" if comparison == "nonpositive" else
                        "peer-pid-unavailable" if comparison == "unavailable" else "peer-pid-mismatch")
                    return type(first_pid) is not int or first_pid != lease["serverPid"]

                def uid_refused():
                    phase("peer-uid-comparison")
                    comparison = incoming_initial_identity_comparison(first_uid, lease["uid"])
                    attribution["peerUidComparison"] = comparison
                    attribution["reason"] = "peer-uid-unavailable" if comparison == "unavailable" else "peer-uid-mismatch"
                    return type(first_uid) is not int or first_uid != lease["uid"]

                def pid_recheck_refused():
                    phase("peer-pid-recheck")
                    try:
                        observed = incoming_settlement_call(deadline, tray.peer_pid, self.connection)
                    except OSError as closed_error:
                        # First PID and UID already matched this retained owner.
                        # Darwin may close the peer before LOCAL_PEERPID's second
                        # read. Cached UID is rechecked below; this successful
                        # connect still means active, never endpoint inactivity.
                        if type(closed_error) is not OSError or incoming_initial_identity_errno(closed_error) != errno.ENOTCONN:
                            raise
                        if type(first_pid) is not int or first_pid <= 0 or first_pid != lease["serverPid"] or type(first_uid) is not int or first_uid != lease["uid"]:
                            raise RuntimeError("Incoming cleanup disconnected peer initial identity refused")
                        phase("post-peer-lease-guard")
                        guard()
                        if any(lease["missing"].values()):
                            raise RuntimeError("Incoming cleanup disconnected peer resource missing")
                        self.closed_peer_qualified = True
                        return False
                    refused = observed != first_pid
                    attribution["peerPidComparison"] = incoming_initial_identity_comparison(observed, lease["serverPid"], pid=True)
                    if refused:
                        attribution["reason"] = "peer-pid-changed"
                    return refused

                def uid_recheck_refused():
                    phase("peer-uid-recheck")
                    observed = incoming_settlement_call(deadline, incoming_peer_uid, self.connection)
                    if self.closed_peer_qualified and type(observed) is not int:
                        attribution["peerUidComparison"] = incoming_initial_identity_comparison(observed, lease["uid"])
                        phase("peer-uid-recheck", "peer-uid-changed")
                        raise RuntimeError("Incoming cleanup disconnected peer UID changed")
                    refused = observed != first_uid
                    attribution["peerUidComparison"] = incoming_initial_identity_comparison(observed, lease["uid"])
                    if refused:
                        attribution["reason"] = "peer-uid-changed"
                    return refused

                # Preserve the original condition's read and short-circuit order.
                if pid_refused() or uid_refused() or pid_recheck_refused() or uid_recheck_refused():
                    raise RuntimeError("Incoming cleanup initial active peer identity refused")
                phase("post-peer-lease-guard")
                guard()
            except BaseException as error:
                # Let identical frozen code create its exact active exception.
                # This private fixed refusal prevents any later recovery route.
                remember(error)
            return result

    view = types.SimpleNamespace(socket=BoundedSocket, AF_UNIX=socket.AF_UNIX, SOCK_STREAM=socket.SOCK_STREAM)
    projected = types.FunctionType(function.__code__, {**function.__globals__, "socket": view}, function.__name__, function.__defaults__, function.__closure__)
    try:
        result = incoming_settlement_call(deadline, projected, unix.IPC_PATH)
    except BaseException as error:
        original = error.__cause__ if isinstance(error, IncomingSettlementDeadline) else error
        if sticky["code"] != "none" and type(original) is RuntimeError:
            original._mixel_incoming_refusal_code = sticky["code"]
            original._mixel_incoming_initial_identity_failure = dict(sticky["identity"])
        raise
    if sticky["code"] != "none":
        error = RuntimeError("Incoming cleanup initial identity refused")
        error._mixel_incoming_refusal_code = sticky["code"]
        error._mixel_incoming_initial_identity_failure = dict(sticky["identity"])
        raise error
    return result


def incoming_settlement_elapsed(start):
    elapsed = time.monotonic() - start
    return min(0x7fffffff, max(0, int(elapsed * 1000))) if math.isfinite(elapsed) else 0


def incoming_settlement_summary(facts, state, captured, start, attempts, code):
    facts["incomingEndpointSettlement"] = {"state": state, "leaseCapturedBeforeStop": captured,
        "maximumMs": 5000, "attempts": attempts, "elapsedMs": incoming_settlement_elapsed(start), "failureCode": code}


def cleanup_incoming_endpoint(app, server_pid, server_birth, gui_pid, birth, lease, facts):
    start = time.monotonic()
    if lease is None:
        incoming_settlement_summary(facts, "failed", False, start, 0, "lease-unavailable")
        raise RuntimeError("Incoming cleanup lease unavailable")
    deadline = start + INCOMING_SETTLEMENT_SECONDS
    first_active = None
    attempts = 0
    stage = "identity-refused"
    try:
        incoming_cleanup_lease_guard(app, server_pid, server_birth, gui_pid, birth, lease, terminal=True, deadline=deadline)
        stage = "probe-ambiguous"
        try:
            incoming_endpoint_inactive(deadline, lease)
        except RuntimeError as error:
            original = error.__cause__ if isinstance(error, IncomingSettlementDeadline) else error
            refusal = getattr(original, "_mixel_incoming_refusal_code", "none")
            if refusal in ("identity-refused", "active-timeout"):
                stage = refusal
            if endpoint_cleanup_fact(original)["reasonCode"] != "ipc-endpoint-active":
                raise
            first_active = original
            initial_failure = incoming_initial_identity_failure_fact(original)
            if refusal in ("identity-refused", "active-timeout") and initial_failure is not None:
                facts["incomingInitialIdentityFailure"] = initial_failure
            if refusal == "identity-refused":
                raise original
            if refusal == "active-timeout":
                raise IncomingSettlementDeadline("Incoming cleanup settlement deadline reached") from original
            if original is not error:
                raise
        if first_active is None:
            stage = "identity-refused"
            incoming_cleanup_lease_guard(app, server_pid, server_birth, gui_pid, birth, lease, terminal=True, deadline=deadline)
            stage = "cleanup-refused"
            incoming_settlement_call(deadline, tray.cleanup_endpoint, unix.IPC_PATH, server_pid)
            incoming_cleanup_lease_guard(app, server_pid, server_birth, gui_pid, birth, lease, terminal=True, deadline=deadline)
            incoming_settlement_summary(facts, "not-required", True, start, 0, "none")
            return
        for attempts in range(1, INCOMING_SETTLEMENT_SAMPLES + 1):
            stage = "identity-refused"
            incoming_cleanup_lease_guard(app, server_pid, server_birth, gui_pid, birth, lease, terminal=True, deadline=deadline)
            stage = "probe-ambiguous"
            connection = incoming_settlement_call(deadline, socket.socket, socket.AF_UNIX, socket.SOCK_STREAM)
            inactive = False
            try:
                remaining = deadline - time.monotonic()
                incoming_settlement_call(deadline, connection.settimeout, min(INCOMING_SETTLEMENT_CONNECT_SECONDS, remaining))
                try:
                    incoming_settlement_call(deadline, connection.connect, str(unix.IPC_PATH))
                except (ConnectionRefusedError, FileNotFoundError):
                    inactive = True
                if not inactive:
                    stage = "identity-refused"
                    peer = incoming_settlement_call(deadline, tray.peer_pid, connection)
                    peer_uid = incoming_settlement_call(deadline, incoming_peer_uid, connection)
                    if type(peer) is not int or peer != server_pid or type(peer_uid) is not int or peer_uid != lease["uid"]:
                        raise RuntimeError("Incoming cleanup active peer identity refused")
                    if (incoming_settlement_call(deadline, tray.peer_pid, connection) != peer
                            or incoming_settlement_call(deadline, incoming_peer_uid, connection) != peer_uid):
                        raise RuntimeError("Incoming cleanup active peer identity changed")
            finally:
                connection.close()
            stage = "identity-refused"
            incoming_cleanup_lease_guard(app, server_pid, server_birth, gui_pid, birth, lease, terminal=True, deadline=deadline)
            if inactive:
                incoming_cleanup_lease_guard(app, server_pid, server_birth, gui_pid, birth, lease, terminal=True, deadline=deadline)
                stage = "cleanup-refused"
                incoming_settlement_call(deadline, tray.cleanup_endpoint, unix.IPC_PATH, server_pid)
                incoming_cleanup_lease_guard(app, server_pid, server_birth, gui_pid, birth, lease, terminal=True, deadline=deadline)
                incoming_settlement_summary(facts, "settled", True, start, attempts, "none")
                return
            remaining = deadline - time.monotonic()
            stage = "active-timeout"
            if attempts == INCOMING_SETTLEMENT_SAMPLES or remaining <= 0:
                raise IncomingSettlementDeadline("Incoming cleanup settlement deadline reached")
            incoming_settlement_call(deadline, time.sleep, min(INCOMING_SETTLEMENT_POLL_SECONDS, remaining))
    except BaseException as error:
        code = "active-timeout" if isinstance(error, IncomingSettlementDeadline) else stage
        incoming_settlement_summary(facts, "failed", True, start, attempts, code)
        if first_active is not None:
            raise first_active
        raise


def cleanup(helper, app, server_pid, launch_time, server_birth, gui_pid, birth, facts):
    if server_pid is None or launch_time is None or server_birth is None:
        return None
    try:
        server_same(helper, app, server_pid, launch_time, server_birth)
    except ERRORS as error:
        facts["processCleanupFailures"] = [{"process": "tray", **error_fact(error)}]
        return error
    first = None
    lease = None
    try:
        lease = capture_incoming_cleanup_lease(helper, app, server_pid, launch_time, server_birth, gui_pid, birth)
    except BaseException as error:
        first = error
        incoming_settlement_summary(facts, "failed", False, time.monotonic(), 0, "lease-unavailable")
    stop_failure = None
    retained = [("gui", gui_pid, birth)] if gui_pid is not None and birth is not None else []
    retained.append(("tray", server_pid, server_birth))
    for label, pid, retained_birth in retained:
        try:
            tray.terminate_owned(helper, app, pid, retained_birth, server_pid)
        except ERRORS as error:
            if first is None:
                first = error
            if stop_failure is None:
                stop_failure = error
            facts.setdefault("processCleanupFailures", []).append({"process": label, **error_fact(error)})
    if stop_failure is not None:
        if lease is not None:
            incoming_settlement_summary(facts, "failed", True, time.monotonic(), 0, "identity-refused")
        return first
    facts["ownedProcessesStopped"] = True
    for label, endpoint, pid in (("url", tray.URL_SOCKET, gui_pid), ("incoming", unix.IPC_PATH, server_pid)):
        try:
            if label == "incoming":
                cleanup_incoming_endpoint(app, server_pid, server_birth, gui_pid, birth, lease, facts)
            else:
                tray.cleanup_endpoint(endpoint, pid)
        except ERRORS as error:
            if first is None:
                first = error
            failure = endpoint_cleanup_fact(error)
            facts.setdefault("endpointCleanupFailures", []).append({"endpoint": label, **failure})
            if (label == "incoming" and failure["reasonCode"] == "ipc-endpoint-active"
                    and facts["ownedProcessesStopped"] is True):
                try:
                    facts["incomingListenerObservations"] = observe_incoming_after_stop(
                        app, server_pid, server_birth, gui_pid, birth)
                except BaseException:
                    # Observation has no cleanup authority and cannot replace
                    # this exact original endpoint-cleanup failure.
                    facts["incomingListenerObservationFailure"] = "observer-unavailable"
    return first


def runtime(app, helper, scenario, facts):
    if scenario not in SCENARIOS:
        raise RuntimeError("Unknown concurrency scenario")
    tray.isolated_ci()
    tray.endpoint_inactive(unix.IPC_PATH)
    tray.endpoint_inactive(tray.URL_SOCKET)
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if info.get("CFBundleIdentifier") != "ch.mixel.remote" or info.get("CFBundleExecutable") != "Mixel-Remote":
        raise RuntimeError("Concurrency bundle identity is invalid")
    facts["criticalHashes"] = {name: hashlib.sha256((app / name).read_bytes()).hexdigest() for name in tray.CRITICAL}
    root = Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library/Logs/Mixel-Remote"
    baseline = tray.log_snapshot(root)
    server_pid = gui_pid = None
    server_launch = server_birth = birth = None
    phase = "tray-launch"
    primary = None
    try:
        launched = desktop.helper_request(helper, "tray", str(app))
        server_pid = launched.get("pid")
        if type(server_pid) is int and server_pid > 0:
            facts["observedTrayCandidatePid"] = server_pid
        if type(server_pid) is not int or server_pid <= 0 or tray.process_path(server_pid) != app / "Contents/MacOS/Mixel-Remote":
            raise RuntimeError("Concurrency tray executable is invalid")
        server_launch = tray.launch_identity(helper, app, server_pid)
        if launched.get("launchTime") != server_launch:
            raise RuntimeError("Original tray launch identity changed")
        server_birth = tray.gui_birth(app, server_pid)
        facts["trayPid"] = server_pid
        phase = "unarmed-tray-readiness"
        tray.wait("Unarmed concurrent tray", lambda: tray.health(server_pid, app, False), server_pid)
        inventory(helper, app, (server_pid,), facts)
        phase = "same-turn-activation"
        arguments = (str(app), unix.URI) if scenario == "ordinary-and-support" else (str(app), unix.URI, desktop.WARM_URI)
        activated = desktop.helper_request(helper, scenario, *arguments)
        expected_count = 1 if scenario == "batch-two-support" else 2
        completion_matches(activated, app, server_pid, server_launch, expected_count)
        server_same(helper, app, server_pid, server_launch, server_birth)
        facts["completionCount"] = expected_count
        facts["completionsMatchOriginalTray"] = True
        phase = "tray-dispatch"
        tray.wait("Concurrent Tao dispatch", lambda: tray.new_tray_marker(root, baseline), server_pid)
        facts["actualTrayDispatchObserved"] = True
        phase = "initial-gui-owner"
        candidate_pid = tray.wait("Initial concurrent GUI", lambda: tray.gui_owner(app, server_pid), server_pid)
        facts["observedGuiCandidatePid"] = candidate_pid
        phase = "initial-gui-birth"
        candidate_birth = tray.gui_birth(app, candidate_pid)
        gui_pid, birth = candidate_pid, candidate_birth
        facts["guiPid"] = gui_pid
        phase = "visible-v2-owned-gui"
        tray.wait("Concurrent visible guarded GUI", lambda: observe(helper, app, server_pid, server_launch, server_birth, gui_pid, birth, facts), server_pid)
        phase = "bounded-stability"
        deadline = time.monotonic() + STABILITY_SECONDS
        while True:
            if not observe(helper, app, server_pid, server_launch, server_birth, gui_pid, birth, facts):
                raise RuntimeError("Concurrent GUI readiness did not remain stable")
            facts["stabilitySamples"] = facts.get("stabilitySamples", 0) + 1
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        facts["visibleGuiAndAttendedV2"] = True
        facts["oneRetainedGuiDuringBoundedStability"] = True
        facts["stabilitySecondsRequired"] = STABILITY_SECONDS
    except ERRORS as error:
        primary = error
        facts["runtimeFailure"] = {"phase": phase, **error_fact(error)}
    finally:
        cleanup_error = cleanup(helper, app, server_pid, server_launch, server_birth, gui_pid, birth, facts)
    if primary is not None:
        raise primary
    if cleanup_error is not None:
        raise cleanup_error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    args = parser.parse_args()
    output_owned = False
    facts = {"observedUtc": datetime.now(timezone.utc).isoformat(), "scenarios": [],
             "syntheticInvite": True, "customerAcceptTested": False,
             "remoteDesktopConnected": False, "invitePayloadDeliveryTested": False,
             "coldDirectLaunchConcurrencyTested": False, "passed": False}
    try:
        tray.isolated_ci()
        app = args.app.resolve(strict=True)
        output = args.proofs.absolute()
        parent = output.parent
        if parent != parent.resolve(strict=True) or not parent.is_dir() or parent.stat().st_uid != os.getuid():
            raise RuntimeError("Concurrency proof parent must be canonical and owned")
        output.mkdir(mode=0o700, exist_ok=False)
        output_owned = True
        with tempfile.TemporaryDirectory(prefix="mixel-macos-concurrency-") as temporary:
            helper = compile_helper(Path(temporary).resolve(strict=True))
            for scenario in SCENARIOS:
                record = {"scenario": scenario, "passed": False, "ownedProcessesStopped": False}
                facts["scenarios"].append(record)
                runtime(app, helper, scenario, record)
                record["passed"] = True
                print("PASS: packaged Mac concurrency " + scenario + " retains one kernel-owned visible GUI and original guarded incoming service", flush=True)
        facts["passed"] = True
        return 0
    except ERRORS as error:
        facts["failure"] = error_fact(error)
        print("FAIL: packaged Mac concurrency verification", file=sys.stderr, flush=True)
        return 1
    finally:
        if output_owned:
            (output / "concurrency-runtime-safe-facts.json").write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--original-desktop", "--original-tray"):
        raise SystemExit(run_original(sys.argv[1], sys.argv[2:]))
    raise SystemExit(main())
