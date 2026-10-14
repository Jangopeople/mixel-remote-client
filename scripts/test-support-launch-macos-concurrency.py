#!/usr/bin/env python3
"""Additive packaged Mac two-request ownership fixture on an isolated CI runner.

The original single URL/tray fixture runs first. Three fresh scenarios here ask
LaunchServices for ordinary+support, two support opens in one Swift turn,
and a single activation carrying two support URLs.
They prove bounded GUI/process ownership and readiness, not invite consumption,
customer consent, TCC permissions, remote desktop, or a physical Mac session.
"""
import argparse
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
LISTENER_OBSERVER_HASH = "747f515ddaa36d40a5676ed5fd4fa1afb0851dcf27898aa6a5875428080ad61c"
SCENARIOS = ("ordinary-and-support", "two-support", "batch-two-support")
STABILITY_SECONDS = 5.0
MAX_INVENTORY = 32
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


def cleanup(helper, app, server_pid, launch_time, server_birth, gui_pid, birth, facts):
    if server_pid is None or launch_time is None or server_birth is None:
        return None
    try:
        server_same(helper, app, server_pid, launch_time, server_birth)
    except ERRORS as error:
        facts["processCleanupFailures"] = [{"process": "tray", **error_fact(error)}]
        return error
    first = None
    retained = [("gui", gui_pid, birth)] if gui_pid is not None and birth is not None else []
    retained.append(("tray", server_pid, server_birth))
    for label, pid, retained_birth in retained:
        try:
            tray.terminate_owned(helper, app, pid, retained_birth, server_pid)
        except ERRORS as error:
            if first is None:
                first = error
            facts.setdefault("processCleanupFailures", []).append({"process": label, **error_fact(error)})
    if first is not None:
        return first
    facts["ownedProcessesStopped"] = True
    for label, endpoint, pid in (("url", tray.URL_SOCKET, gui_pid), ("incoming", unix.IPC_PATH, server_pid)):
        try:
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
