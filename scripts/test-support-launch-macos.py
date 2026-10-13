#!/usr/bin/env python3
"""Test real macOS URL activation, customer UI and incoming support IPC.

Targets the exact built app through LaunchServices using synthetic invalid
credentials. Never grants TCC permissions or starts a remote session. A pass
proves launching, the attended guard and relay registration, not screen/input
permissions or video capture on a customer Mac.
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
import re
import signal
import socket
import struct
import stat
import subprocess
import sys
import tempfile
import time


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mixel_unix_smoke", SCRIPT_DIRECTORY / "test-support-launch-linux.py")
unix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unix)
WARM_TOKEN = "inv_00000000-0000-0000-0000-000000000003"
WARM_URI = unix.URI.replace(unix.TOKEN, WARM_TOKEN)

SWIFT = r"""
import AppKit
import CoreGraphics
import Foundation

func emit(_ result: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}

let arguments = CommandLine.arguments
if arguments.count < 3 { exit(2) }
let action = arguments[1]
if action == "launch" || action == "ordinary" {
    if arguments.count != (action == "launch" ? 5 : 3) { exit(2) }
    let application = URL(fileURLWithPath: arguments[2]).resolvingSymlinksInPath()
    if (action == "ordinary" || arguments[4] == "cold") && NSWorkspace.shared.runningApplications.contains(where: {
        $0.bundleURL?.resolvingSymlinksInPath() == application
    }) { emit(["error": "Built application is already running"]); exit(1) }
    let configuration = NSWorkspace.OpenConfiguration()
    configuration.activates = true
    configuration.createsNewApplicationInstance = false
    configuration.allowsRunningApplicationSubstitution = false
    configuration.addsToRecentItems = false
    var finished = false
    var result: [String: Any] = ["error": "App URL activation timed out"]
    let completion: (NSRunningApplication?, Error?) -> Void = { app, error in
        if let app = app, error == nil {
            result = ["pid": app.processIdentifier,
                      "bundlePath": app.bundleURL?.resolvingSymlinksInPath().path ?? "",
                      "launchTime": app.launchDate?.timeIntervalSince1970 ?? 0]
        } else {
            result = ["error": "App URL activation failed"]
        }
        finished = true
    }
    if action == "ordinary" {
        NSWorkspace.shared.openApplication(at: application, configuration: configuration, completionHandler: completion)
    } else {
        guard let supportURL = URL(string: arguments[3]) else { exit(2) }
        NSWorkspace.shared.open([supportURL], withApplicationAt: application, configuration: configuration, completionHandler: completion)
    }
    let deadline = Date().addingTimeInterval(30)
    while !finished && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.05)) }
    emit(result)
    exit(result["pid"] == nil ? 1 : 0)
}
guard let pid = Int32(arguments[2]), let application = NSRunningApplication(processIdentifier: pid) else {
    emit(["running": false, "visible": false, "hidden": false, "bundlePath": ""]); exit(0)
}
if action == "hide" {
    emit(["requested": application.hide()]); exit(0)
}
if action != "probe" { exit(2) }
let windows = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]]) ?? []
let visible = windows.contains { window in
    guard (window[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
          (window[kCGWindowLayer as String] as? NSNumber)?.intValue == 0,
          (window[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 0 > 0,
          let bounds = window[kCGWindowBounds as String] as? [String: Any],
          let width = bounds["Width"] as? NSNumber, let height = bounds["Height"] as? NSNumber
    else { return false }
    return width.doubleValue >= 400 && height.doubleValue >= 250
}
// Window owner/layer/bounds suffice; no window contents, titles, Accessibility
// scripting, screenshots or screen-recording permission are needed.
emit(["running": !application.isTerminated, "visible": visible && !application.isHidden,
      "hidden": application.isHidden, "bundlePath": application.bundleURL?.resolvingSymlinksInPath().path ?? "",
      "launchTime": application.launchDate?.timeIntervalSince1970 ?? 0])
"""


MAX_REPORT = 4 * 1024 * 1024
MAX_LOG = 4 * 1024 * 1024
MAX_FILES = 64
MAX_DIAGNOSTIC_BYTES = 12 * 1024 * 1024


def utc_now():
    return datetime.now(timezone.utc)


def strict_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate diagnostic JSON key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite diagnostic JSON number")))


def bounded_read(path: Path, limit: int):
    """Read only this regular, same-user, single-link file; never follow a link."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1:
        raise RuntimeError("Diagnostic file is not an owned regular file")
    if before.st_size > limit:
        raise RuntimeError("Diagnostic file exceeds its byte bound")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        opened = os.fstat(source.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError("Diagnostic file changed during open")
        data = source.read(limit + 1)
        after = os.fstat(source.fileno())
    if len(data) > limit or file_identity(after) != file_identity(before):
        raise RuntimeError("Diagnostic file changed during read")
    return data, before


def file_identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
            value.st_nlink, value.st_size, value.st_mtime_ns)


def bounded_digest(path: Path, limit: int):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1 or before.st_size > limit:
        raise RuntimeError("Diagnostic archive is not an owned bounded regular file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    digest = hashlib.sha256()
    count = 0
    with os.fdopen(descriptor, "rb") as source:
        if file_identity(os.fstat(source.fileno())) != file_identity(before):
            raise RuntimeError("Diagnostic archive changed during open")
        for block in iter(lambda: source.read(1024 * 1024), b""):
            count += len(block)
            if count > limit:
                raise RuntimeError("Diagnostic archive grew beyond its byte bound")
            digest.update(block)
        if file_identity(os.fstat(source.fileno())) != file_identity(before):
            raise RuntimeError("Diagnostic archive changed during read")
    return {"name": path.name, "bytes": before.st_size, "sha256": digest.hexdigest()}


def scoped_files(root: Path, *, recursive: bool):
    if not os.path.lexists(root):
        return []
    pending = [(root, 0)]
    result = []
    inspected = 0
    while pending:
        directory, depth = pending.pop()
        attributes = directory.lstat()
        if directory.resolve() != directory or not stat.S_ISDIR(attributes.st_mode) or attributes.st_uid != os.getuid():
            raise RuntimeError("Diagnostic directory is not an owned directory")
        for path in sorted(directory.iterdir()):
            inspected += 1
            if inspected > MAX_FILES:
                raise RuntimeError("Diagnostic directory exceeds its entry bound")
            item = path.lstat()
            if stat.S_ISLNK(item.st_mode):
                raise RuntimeError("Diagnostic directory contains a symbolic link")
            if stat.S_ISDIR(item.st_mode) and recursive:
                if depth >= 3:
                    raise RuntimeError("Diagnostic directory exceeds its depth bound")
                pending.append((path, depth + 1))
            elif stat.S_ISREG(item.st_mode):
                result.append(path)
    return result


def privacy_issue(data: bytes):
    sensitive = (unix.TOKEN.encode(), WARM_TOKEN.encode(), unix.API_KEY.encode(), b"mixel-remote://support/?invite=")
    if any(value in data for value in sensitive):
        return True
    return bool(re.search(rb"(?i)bearer\s+\S+|inv_[0-9a-f-]{36}|(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|authorization)[\"']?\s*[=:]\s*[\"']?[^\s\"',}]+", data))


def report_time(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Missing crash report timestamp")
    normalized = re.sub(r" (?=[+-][0-9]{2}:?[0-9]{2}$)", "", value.strip()).replace("Z", "+00:00")
    result = datetime.fromisoformat(normalized)
    if result.tzinfo is None:
        raise ValueError("Crash report timestamp has no timezone")
    return result.astimezone(timezone.utc)


def crash_identity(data: bytes, suffix: str):
    text = data.decode("utf-8", errors="strict")
    if suffix == ".ips":
        header, body = text.split("\n", 1)
        metadata, report = strict_json(header), strict_json(body)
        if not isinstance(metadata, dict) or str(metadata.get("bug_type")) != "309" or not isinstance(report, dict):
            raise ValueError("Not an application crash report")
        identity = {key: report.get(key) for key in ("pid", "procPath", "procLaunch", "captureTime", "procName")}
        identity["bundleIdentifier"] = report.get("bundleInfo", {}).get("CFBundleIdentifier") if isinstance(report.get("bundleInfo"), dict) else None
        return identity, report
    if suffix != ".crash":
        raise ValueError("Unexpected crash report format")
    fields = {}
    for line in text.splitlines():
        match = re.fullmatch(r"(Process|Path|Identifier|Date/Time|Launch Time):\s+(.+)", line)
        if match:
            if match[1] in fields:
                raise ValueError("Duplicate crash report identity")
            fields[match[1]] = match[2]
    process = re.fullmatch(r"(.+) \[([1-9][0-9]*)\]", fields.get("Process", ""))
    if not process:
        raise ValueError("Missing crash report process identity")
    identity = {"pid": int(process[2]), "procName": process[1], "procPath": fields.get("Path"),
                "bundleIdentifier": fields.get("Identifier"), "procLaunch": fields.get("Launch Time"),
                "captureTime": fields.get("Date/Time")}
    # Legacy text reports often omit launch time. Preserve that absence, never
    # invent it; their newly-created file, exact PID/path and capture time gate
    # still apply. Only bounded termination lines are exported from text reports.
    report = {"legacyTermination": [line for line in text.splitlines() if re.match(
        r"^(Exception Type|Exception Codes|Termination Reason|Crashed Thread):", line)][:8]}
    return identity, report


def safe_crash_details(report, app: Path):
    """Export crash mechanics, excluding registers, environment and foreign paths."""
    result = {key: report[key] for key in ("faultingThread", "legacyTermination") if key in report}
    for key, allowed in (("exception", ("type", "signal", "codes", "rawCodes")),
                         ("termination", ("flags", "code", "namespace", "indicator", "byProc", "byPid"))):
        if isinstance(report.get(key), dict):
            result[key] = {name: report[key][name] for name in allowed if name in report[key]}
    threads = report.get("threads", [])
    if isinstance(threads, list) and len(threads) <= 512:
        selected = [index for index, thread in enumerate(threads) if isinstance(thread, dict) and thread.get("triggered") is True]
        if type(report.get("faultingThread")) is int and 0 <= report["faultingThread"] < len(threads):
            selected.append(report["faultingThread"])
        result["crashedThreads"] = []
        for index in sorted(set(selected))[:4]:
            frames = threads[index].get("frames", []) if isinstance(threads[index], dict) else []
            if not isinstance(frames, list) or len(frames) > 512:
                continue
            result["crashedThreads"].append({"index": index, "frames": [
                {key: frame[key] for key in ("imageOffset", "imageIndex", "symbol", "symbolLocation") if key in frame}
                for frame in frames if isinstance(frame, dict)]})
    images = report.get("usedImages", [])
    if isinstance(images, list) and len(images) <= 1024:
        result["usedImages"] = []
        for index, image in enumerate(images):
            if not isinstance(image, dict):
                continue
            item = {key: image[key] for key in ("base", "size", "arch", "uuid", "name") if key in image}
            item["index"] = index
            path = image.get("path")
            if isinstance(path, str) and (path.startswith(str(app) + "/") or path.startswith(("/System/Library/", "/usr/lib/"))):
                item["path"] = path
            elif path is not None:
                item["path_withheld"] = True
            result["usedImages"].append(item)
    return result


class RuntimeEvidence:
    def __init__(self, app: Path, output: Path | None, log_root: Path, reports_root: Path, app_archive: Path | None = None):
        self.app, self.output, self.log_root, self.reports_root = app.resolve(), output, log_root, reports_root
        self.started, self.clock = utc_now(), time.monotonic()
        self.phase, self.pid, self.launch_time = "initialization", None, None
        self.events, self.reports, self.logs, self.errors = [], [], [], []
        self.seen_reports = set()
        self.failed_at = None
        self.total_bytes = 0
        self.privacy_ok = True
        self.cleanup = {}
        info = plistlib.loads((self.app / "Contents/Info.plist").read_bytes())
        executable = info.get("CFBundleExecutable")
        if not isinstance(executable, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", executable):
            raise RuntimeError("Built app has an invalid executable identity")
        self.executable = executable
        self.executable_path = self.app / "Contents/MacOS" / executable
        executable_data, _ = bounded_read(self.executable_path, 128 * 1024 * 1024)
        self.executable_sha256 = hashlib.sha256(executable_data).hexdigest()
        self.app_archive = None
        if output is not None:
            if output.parent.resolve() != output.parent or not output.parent.is_dir():
                raise RuntimeError("Diagnostic output parent must be a canonical existing directory")
        if app_archive is not None:
            if output is None or app_archive.parent != output.parent or app_archive.name != "signed-stapled-diagnostic-app.zip":
                raise RuntimeError("Diagnostic app archive has an unexpected owned path")
            self.app_archive = bounded_digest(app_archive, 512 * 1024 * 1024)
        self.offsets = {path: path.lstat() for path in scoped_files(log_root, recursive=True)}
        # No old or unrelated report bytes are read during this snapshot.
        self.old_reports = {path: file_identity(path.lstat()) for path in self.report_candidates()}
        if output is not None:
            output.mkdir(exist_ok=False)
        self.mark("test-start")

    def report_candidates(self):
        if not os.path.lexists(self.reports_root):
            return []
        attributes = self.reports_root.lstat()
        if self.reports_root.resolve() != self.reports_root or not stat.S_ISDIR(attributes.st_mode) or attributes.st_uid != os.getuid():
            raise RuntimeError("Crash report root is not an owned directory")
        # A runner may contain unrelated system reports; inspect only matching
        # executable filenames, not unrelated file metadata or contents.
        candidates = [path for path in self.reports_root.iterdir() if path.suffix in (".ips", ".crash")
                      and path.name.startswith((self.executable + "_", self.executable + "-"))]
        if len(candidates) > MAX_FILES:
            raise RuntimeError("Owned crash report candidates exceed their bound")
        return sorted(candidates)

    def mark(self, event, **fields):
        if len(self.events) >= 4096:
            raise RuntimeError("Runtime observation timeline exceeds its bound")
        self.events.append({"event": event, "phase": self.phase, "pid": self.pid,
                            "elapsed_seconds": round(time.monotonic() - self.clock, 6),
                            "utc": utc_now().isoformat(), **fields})

    def request(self, helper, action, *arguments):
        self.mark("launchservices-request" if action == "launch" else action + "-request")
        try:
            result = helper_request(helper, action, *arguments)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            self.mark(action + "-error", error_type=type(error).__name__, reason=safe_error(error))
            raise
        allowed = {key: result[key] for key in ("pid", "running", "visible", "hidden", "requested") if key in result and type(result[key]) in (int, bool)}
        if "bundlePath" in result:
            allowed["bundlePath"] = result["bundlePath"] if result["bundlePath"] in (str(self.app), "") else "different-bundle"
        if "launchTime" in result:
            value = result["launchTime"]
            allowed["launchTime"] = value if type(value) in (int, float) and math.isfinite(value) else "invalid"
        self.mark("launchservices-response" if action == "launch" else action + "-response", response=allowed)
        return result

    def window(self, helper, *, allow_terminated=False):
        result = self.request(helper, "probe", str(self.pid))
        # The native helper has no bundle when NSRunningApplication has ended.
        # Only cleanup may use that observation; normal readiness stays strict.
        if allow_terminated and result.get("running") is False and result.get("bundlePath") == "":
            return result
        if result.get("bundlePath") != str(self.app):
            raise RuntimeError("Window probe targets a different application bundle")
        return result

    def failure(self, error):
        self.failed_at = utc_now()
        self.mark("test-failure", error_type=type(error).__name__, reason=safe_error(error))

    def collect_reports(self):
        if self.pid is None or self.failed_at is None:
            return
        for path in self.report_candidates():
            record = {"source_format": path.suffix}
            try:
                attributes = path.lstat()
                if self.old_reports.get(path) == file_identity(attributes):
                    continue
                item_identity = (path, file_identity(attributes))
                if item_identity in self.seen_reports:
                    continue
                self.seen_reports.add(item_identity)
                data, attributes = bounded_read(path, MAX_REPORT)
                record.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                identity, body = crash_identity(data, path.suffix)
                if (type(identity["pid"]) is not int or identity["pid"] != self.pid
                        or identity["procPath"] != str(self.executable_path)
                        or identity["procName"] != self.executable or identity["bundleIdentifier"] != "ch.mixel.remote"):
                    raise ValueError("Crash report does not match the owned process and bundle")
                captured = report_time(identity["captureTime"])
                if not self.started.timestamp() - 1 <= captured.timestamp() <= self.failed_at.timestamp() + 1:
                    raise ValueError("Crash report is outside the owned test interval")
                if identity["procLaunch"] is not None:
                    launched = report_time(identity["procLaunch"])
                    if not self.started.timestamp() - 1 <= launched.timestamp() <= captured.timestamp():
                        raise ValueError("Crash report launch is outside the owned test interval")
                    if self.launch_time is not None and abs(launched.timestamp() - self.launch_time) > 2:
                        raise ValueError("Crash report launch does not match the owned application")
                elif path.suffix == ".ips":
                    raise ValueError("JSON crash report is missing launch identity")
                safe = {"original_identity": identity, "details": safe_crash_details(body, self.app),
                        "original_bytes_preserved": False, "format": "privacy-filtered-crash-report"}
                encoded = (json.dumps(safe, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
                record["identity"] = identity
                record["launch_time_recorded"] = identity["procLaunch"] is not None
                record["association"] = "pid-path-launch-capture-time" if identity["procLaunch"] is not None else "pid-path-capture-time-new-report-without-launch-field"
                if privacy_issue(data) or privacy_issue(encoded):
                    record["withheld"] = "bearer-or-credential-pattern"
                elif len(encoded) + self.total_bytes > MAX_DIAGNOSTIC_BYTES:
                    record["withheld"] = "total-byte-bound"
                else:
                    if self.output is not None:
                        name = f"owned-crash-{len(self.reports) + 1}.json"
                        with (self.output / name).open("xb") as destination:
                            destination.write(encoded)
                        record["retained_file"] = name
                        record["retained_sha256"] = hashlib.sha256(encoded).hexdigest()
                    self.total_bytes += len(encoded)
                self.reports.append(record)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                self.errors.append({"kind": "crash-report-refused", "reason": safe_error(error)})

    def collect_logs(self):
        for path in scoped_files(self.log_root, recursive=True):
            data, attributes = bounded_read(path, MAX_LOG)
            old = self.offsets.get(path)
            offset = old.st_size if old is not None and (old.st_dev, old.st_ino) == (attributes.st_dev, attributes.st_ino) and old.st_size <= len(data) else 0
            delta = data[offset:]
            if not delta:
                continue
            record = {"relative_path": str(path.relative_to(self.log_root)), "offset": offset,
                      "bytes": len(delta), "sha256": hashlib.sha256(delta).hexdigest()}
            if privacy_issue(delta):
                self.privacy_ok = False
                record["withheld"] = "bearer-or-credential-pattern"
            elif len(delta) + self.total_bytes > MAX_DIAGNOSTIC_BYTES:
                raise RuntimeError("Owned app log deltas exceed the total byte bound")
            elif self.failed_at is not None and self.output is not None:
                delta.decode("utf-8", errors="strict")
                name = f"owned-app-log-{len(self.logs) + 1}.log"
                with (self.output / name).open("xb") as destination:
                    destination.write(delta)
                record["retained_file"] = name
                self.total_bytes += len(delta)
            self.logs.append(record)

    def finish(self):
        source, run = os.environ.get("GITHUB_SHA", ""), os.environ.get("GITHUB_RUN_ID", "")
        result = {"schema": 1, "scope": "macos-launch-smoke-diagnostic-not-customer-release",
                  "bundle_path": str(self.app), "executable_path": str(self.executable_path),
                  "executable_sha256": self.executable_sha256, "diagnostic_app_archive": self.app_archive,
                  "owned_pid": self.pid, "owned_launch_time": self.launch_time,
                  "started_utc": self.started.isoformat(), "failed_utc": self.failed_at.isoformat() if self.failed_at else None,
                  "outcome": "failure" if self.failed_at else "success", "events": self.events,
                  "reports": self.reports, "report_absent": not self.reports, "app_log_deltas": self.logs,
                  "privacy_check_passed": self.privacy_ok, "collection_errors": self.errors, "cleanup": self.cleanup,
                  "runner_context": {"source_sha": source if re.fullmatch(r"[0-9a-f]{40}", source) else None,
                                     "run_id": int(run) if re.fullmatch(r"[1-9][0-9]{0,19}", run) else None},
                  "customer_app_qualified": False, "screen_control_qualified": False, "publication": False}
        if self.output is not None:
            with (self.output / "runtime.json").open("x", encoding="utf-8") as destination:
                json.dump(result, destination, sort_keys=True, indent=2, allow_nan=False)
                destination.write("\n")
        return result


def safe_error(error):
    if isinstance(error, OSError):
        return f"Diagnostic filesystem operation failed (errno={error.errno})"
    if isinstance(error, UnicodeError):
        return "Diagnostic text is not valid UTF-8"
    if isinstance(error, ValueError):
        return "Diagnostic value failed validation"
    return str(error).replace(unix.URI, "[support URI]").replace(WARM_URI, "[support URI]").replace(unix.TOKEN, "[invite]").replace(WARM_TOKEN, "[invite]").replace(unix.API_KEY, "[API key]")[:1000]


def cleanup_endpoint(endpoint: Path, pid: int):
    """Delete only same-user socket + canonical PID marker for this owned PID."""
    marker = endpoint.with_suffix(".pid")
    if not os.path.lexists(marker):
        if os.path.lexists(endpoint):
            raise RuntimeError("IPC endpoint has no owned PID marker")
        return "absent"
    parent = endpoint.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid():
        raise RuntimeError("IPC parent is not an owned directory")
    data, marker_stat = bounded_read(marker, 64)
    if data.strip() != str(pid).encode() or not re.fullmatch(rb"[1-9][0-9]*\n?", data):
        raise RuntimeError("IPC PID marker does not belong to the launched process")
    endpoint_stat = None
    if os.path.lexists(endpoint):
        endpoint_stat = endpoint.lstat()
        if not stat.S_ISSOCK(endpoint_stat.st_mode) or endpoint_stat.st_uid != os.getuid() or endpoint_stat.st_nlink != 1:
            raise RuntimeError("IPC endpoint is not an owned socket")
    if file_identity(marker.lstat()) != file_identity(marker_stat):
        raise RuntimeError("IPC PID marker changed before cleanup")
    if endpoint_stat is not None:
        if file_identity(endpoint.lstat()) != file_identity(endpoint_stat):
            raise RuntimeError("IPC endpoint changed before cleanup")
        endpoint.unlink()
    if file_identity(marker.lstat()) != file_identity(marker_stat):
        raise RuntimeError("IPC PID marker changed during cleanup")
    marker.unlink()
    return "removed-owned-endpoint"


def cleanup_runtime(evidence, helper, endpoint):
    pid = evidence.pid
    if pid is None:
        return {"process": "not-launched", "endpoint": "not-touched"}
    result = {"process": "unverified", "endpoint": "not-touched"}
    exited = False
    native_ended = False

    def owned_window():
        current = evidence.window(helper, allow_terminated=True)
        if current.get("running") is False and current.get("bundlePath") == "":
            return False
        launched = current.get("launchTime")
        if (current.get("bundlePath") != str(evidence.app)
                or type(launched) not in (int, float) or not math.isfinite(launched)
                or launched != evidence.launch_time):
            raise RuntimeError("Cleanup cannot verify the owned process bundle and launch identity")
        if type(current.get("running")) is not bool:
            raise RuntimeError("Cleanup native running observation is invalid")
        return current["running"]

    def before_signal():
        if not owned_window():
            return False
        # No signal is allowed without a fresh kernel owner and exact integer
        # count. bool False and floats are not authenticated zero-count proofs.
        if not os.path.lexists(endpoint):
            raise RuntimeError("Cleanup cannot verify the incoming IPC owner")
        verify_ipc_owner(pid, endpoint)
        count = unix.query("VideoConnCount")
        if type(count) is not int or count != 0:
            raise RuntimeError("Isolated fixture has an unverified or authenticated remote session")
        return True

    def wait_exit():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True, False
            # NSRunningApplication can end while kill(0) still sees a zombie.
            # Recognizing that is observation only, never permission to signal.
            if not owned_window():
                return True, True
            time.sleep(0.1)
        return False, False

    try:
        if (type(pid) is not int or pid <= 0
                or type(evidence.launch_time) not in (int, float)
                or not math.isfinite(evidence.launch_time) or evidence.launch_time <= 0):
            raise RuntimeError("Cleanup has no valid owned process identity")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            exited = True
            result["process"] = "already-exited"
        if not exited:
            if not before_signal():
                exited, native_ended = True, True
                result["process"] = "already-terminated"
            else:
                os.kill(pid, signal.SIGTERM)
                exited, native_ended = wait_exit()
                if not exited:
                    # TERM may have changed the connection count, IPC owner or
                    # process launch. Recheck every boundary before escalation.
                    if not before_signal():
                        exited, native_ended = True, True
                    else:
                        os.kill(pid, signal.SIGKILL)
                        exited, native_ended = wait_exit()
                if not exited:
                    raise RuntimeError("Owned application did not exit during bounded cleanup")
                result["process"] = "terminated-owned-application"
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        result["process_error"] = safe_error(error)
    if exited:
        try:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                if not native_ended or owned_window():
                    raise RuntimeError("Cleanup PID is active again; IPC files were not touched")
                # Retained zombie PIDs must have a repeated native-ended proof
                # and an inactive socket. An active/replaced listener is never
                # eligible, even when its marker still contains the owned PID.
                if os.path.lexists(endpoint):
                    endpoint_identity = file_identity(endpoint.lstat())
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                        connection.settimeout(2)
                        try:
                            connection.connect(str(endpoint))
                        except (FileNotFoundError, ConnectionRefusedError):
                            pass
                        else:
                            raise RuntimeError("Cleanup native-ended process still has an active IPC listener")
                    if not os.path.lexists(endpoint) or file_identity(endpoint.lstat()) != endpoint_identity:
                        raise RuntimeError("IPC endpoint changed during native-ended cleanup")
            result["endpoint"] = cleanup_endpoint(endpoint, pid)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            result["endpoint_error"] = safe_error(error)
    return result


def compile_helper(directory: Path) -> Path:
    source = directory / "window-probe.swift"
    source.write_text(SWIFT, encoding="utf-8")
    helper = directory / "window-probe"
    compiled = subprocess.run(["xcrun", "swiftc", str(source), "-o", str(helper)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if compiled.returncode:
        raise RuntimeError("macOS window probe compilation failed: " + compiled.stderr[-2000:])
    return helper


def helper_request(helper: Path, *arguments: str):
    result = subprocess.run([str(helper), *arguments], capture_output=True, text=True, encoding="utf-8", timeout=40)
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("macOS window probe returned an invalid response") from None
    if result.returncode or not isinstance(value, dict) or "error" in value:
        raise RuntimeError("macOS application URL activation or window probe failed")
    return value


def wait_for(description: str, operation, pid: int, timeout: float = 60, evidence=None):
    deadline = time.monotonic() + timeout
    last = "not ready"
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            if evidence is not None:
                evidence.mark("owned-pid-exited")
            raise RuntimeError(f"{description}: customer app exited") from None
        try:
            value = operation()
            if value:
                return value
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            last = str(error)
            if evidence is not None:
                evidence.mark("wait-observation-error", error_type=type(error).__name__, reason=safe_error(error))
        time.sleep(0.25)
    if evidence is not None:
        evidence.mark("wait-deadline", timeout_seconds=timeout)
    raise RuntimeError(f"{description}: {last}")


def probe(helper: Path, pid: int, app: Path):
    result = helper_request(helper, "probe", str(pid))
    returned_path = result.get("bundlePath")
    if not isinstance(returned_path, str) or Path(returned_path).resolve() != app.resolve():
        raise RuntimeError("Window probe targets a different application bundle")
    return result


def wait_health(pid: int, scenario: str, evidence=None) -> None:
    def ready():
        verify_ipc_owner(pid)
        result = unix.runtime_health(pid)
        if evidence is not None:
            evidence.mark("incoming-health-response", online=result[0], key_confirmed=result[1])
        return result if result[0] > 0 and result[1] else None
    state, confirmed = wait_for(scenario + " incoming server", ready, pid, evidence=evidence)
    print(f"PASS: {scenario} actual incoming IPC proves attended-runtime-v2, branded relay, registered ID, online state={state}, keyConfirmed={str(confirmed).lower()}", flush=True)


def verify_ipc_owner(pid: int, endpoint: Path = unix.IPC_PATH) -> None:
    # The two launch agents can share a pathname while racing the .pid file.
    # Darwin's kernel peer PID identifies the server actually receiving IPC.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(endpoint))
        # SDK sys/un.h: SOL_LOCAL=0 and LOCAL_PEERPID=0x002.
        actual = struct.unpack("i", connection.getsockopt(0, 0x002, 4))[0]
    if actual != pid:
        raise RuntimeError("Actual incoming IPC belongs to a different application process")


def runtime(app: Path, helper: Path, diagnostics: Path | None = None, app_archive: Path | None = None) -> None:
    if os.path.lexists(unix.IPC_PATH) or os.path.lexists(unix.IPC_PATH.with_suffix(".pid")):
        raise RuntimeError("A Mixel Remote IPC endpoint already exists; use a fresh isolated macOS runner")
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if info.get("CFBundleIdentifier") != "ch.mixel.remote":
        raise RuntimeError("Built app has an unexpected bundle identity")
    schemes = [scheme for entry in info.get("CFBundleURLTypes", []) for scheme in entry.get("CFBundleURLSchemes", [])]
    if "mixel-remote" not in schemes:
        raise RuntimeError("Built app is missing the branded support URL handler")
    log_root = Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library/Logs/Mixel-Remote"
    reports_root = log_root.parent / "DiagnosticReports"
    evidence = RuntimeEvidence(app, diagnostics, log_root, reports_root, app_archive)
    pid = None
    primary_error = None
    try:
        evidence.phase = "ordinary-gui-launch"
        ordinary = evidence.request(helper, "ordinary", str(app))
        pid = ordinary.get("pid")
        if type(pid) is not int or pid <= 0:
            raise RuntimeError("macOS ordinary GUI launch returned no application PID")
        evidence.pid = pid
        launched_time = ordinary.get("launchTime")
        if type(launched_time) in (int, float) and math.isfinite(launched_time) and launched_time > 0:
            evidence.launch_time = launched_time
        evidence.mark("owned-ordinary-application-launched")
        wait_for("Ordinary customer window", lambda: evidence.window(helper).get("visible"), pid, evidence=evidence)
        def ordinary_ready():
            verify_ipc_owner(pid)
            if int(unix.IPC_PATH.with_suffix(".pid").read_text().strip()) != pid:
                raise RuntimeError("Ordinary GUI IPC PID file does not identify its owner")
            if unix.query("Config", ["mixel-support-invite-attended", None]) != ["mixel-support-invite-attended", ""]:
                raise RuntimeError("Ordinary GUI already has an attended guard; fresh warm intent cannot be proven")
            if unix.query("VideoConnCount") != 0:
                raise RuntimeError("Ordinary GUI unexpectedly has an authenticated remote session")
            state = unix.query("OnlineStatus")
            return isinstance(state, list) and len(state) == 2 and state[0] > 0 and state[1] is True
        evidence.phase = "ordinary-unarmed-health"
        wait_for("Ordinary unarmed incoming server", ordinary_ready, pid, evidence=evidence)
        print("PASS: ordinary macOS GUI starts registered with no attended guard and no authenticated remote session", flush=True)
        evidence.phase = "fresh-warm-hide-setup"
        hidden = evidence.request(helper, "hide", str(pid)).get("requested") is True
        if hidden:
            wait_for("Warm support URL hide setup", lambda: evidence.window(helper).get("hidden") and not evidence.window(helper).get("visible"), pid, timeout=10, evidence=evidence)
        evidence.phase = "fresh-warm-url-launch"
        warm = evidence.request(helper, "launch", str(app), WARM_URI, "warm")
        if warm.get("pid") != pid or warm.get("launchTime") != evidence.launch_time:
            raise RuntimeError("macOS warm URL activation created a different customer application")
        wait_for("Warm support URL customer window", lambda: evidence.window(helper).get("visible"), pid, evidence=evidence)
        wait_health(pid, "Fresh ordinary-to-support warm URL launch", evidence)
        print("PASS: fresh warm support intent changes the same ordinary application from empty guard to attended-runtime-v2", flush=True)
        evidence.phase = "ordinary-to-warm-owned-cleanup"
        warm_cleanup = cleanup_runtime(evidence, helper, unix.IPC_PATH)
        evidence.mark("warm-cleanup-result", result=warm_cleanup)
        if any(key.endswith("_error") for key in warm_cleanup) or warm_cleanup.get("process") not in ("already-exited", "already-terminated", "terminated-owned-application"):
            raise RuntimeError("Warm application cleanup failed; distinct cold launch prohibited")
        evidence.pid = pid = None
        evidence.launch_time = None
        evidence.phase = "cold-url-launch"
        launched = evidence.request(helper, "launch", str(app), unix.URI, "cold")
        pid = launched.get("pid")
        if type(pid) is not int or pid <= 0:
            raise RuntimeError("macOS cold URL launch returned no application PID")
        evidence.pid = pid
        launched_time = launched.get("launchTime")
        if type(launched_time) in (int, float) and math.isfinite(launched_time) and launched_time > 0:
            evidence.launch_time = launched_time
        evidence.mark("owned-application-launched")
        evidence.phase = "cold-visible-window"
        wait_for("Cold support URL customer window", lambda: evidence.window(helper).get("visible"), pid, evidence=evidence)
        evidence.phase = "cold-stable-window"
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if not evidence.window(helper).get("visible"):
                raise RuntimeError("Cold support URL hid the app after initialisation")
            time.sleep(0.25)
        print("PASS: cold macOS support URL activation opens a stable visible customer window", flush=True)
        evidence.phase = "cold-incoming-health"
        wait_health(pid, "Cold support URL launch", evidence)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        primary_error = error
        evidence.failure(error)
        # A crash report can arrive after the process exit. This diagnostic-only
        # wait does not extend any window/health deadline or convert a failure.
        deadline = time.monotonic() + 3
        while True:
            try:
                evidence.collect_reports()
            except (OSError, ValueError, RuntimeError) as collection_error:
                evidence.errors.append({"kind": "crash-collection-failed", "reason": safe_error(collection_error)})
                break
            if evidence.reports or time.monotonic() >= deadline:
                break
            time.sleep(0.25)
    finally:
        evidence.phase = "owned-cleanup"
        evidence.cleanup = cleanup_runtime(evidence, helper, unix.IPC_PATH)
        evidence.mark("cleanup-result", result=evidence.cleanup)
        try:
            evidence.collect_logs()
            if not evidence.privacy_ok:
                raise RuntimeError("Support invite bearer or credential appeared in macOS app logs")
        except (OSError, ValueError, RuntimeError) as error:
            evidence.privacy_ok = False
            evidence.errors.append({"kind": "app-log-collection-failed", "reason": safe_error(error)})
            if primary_error is None:
                primary_error = error
                evidence.failure(error)
        if any(key.endswith("_error") for key in evidence.cleanup) and primary_error is None:
            primary_error = RuntimeError("Owned macOS runtime cleanup could not be completed")
            evidence.failure(primary_error)
        evidence.finish()
    if primary_error is not None:
        raise primary_error
    print("PASS: synthetic support invite bearer and API key absent from macOS app logs", flush=True)


def cleanup_smoke_fixture(helper: Path, pid: int, app: Path, launch_time) -> None:
    """Stop only the exact synthetic NSApplication created by self_test."""
    if type(pid) is not int or pid <= 0 or type(launch_time) not in (int, float) or not math.isfinite(launch_time) or launch_time <= 0:
        raise RuntimeError("Synthetic smoke cleanup lacks a valid launch identity")
    current = probe(helper, pid, app)
    if current.get("launchTime") != launch_time or current.get("running") is not True:
        raise RuntimeError("Synthetic smoke cleanup cannot verify the exact live launch")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise RuntimeError("Synthetic smoke application did not exit during bounded cleanup")


def self_test(helper: Path, directory: Path) -> None:
    unix.self_test()
    # A stale PID sidecar must not substitute for the kernel socket owner.
    endpoint = directory / "owner-identity.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(endpoint))
        listener.listen(4)
        verify_ipc_owner(os.getpid(), endpoint)
        try:
            verify_ipc_owner(os.getpid() + 1, endpoint)
            raise AssertionError("A different actual IPC owner was accepted")
        except RuntimeError:
            pass
    print("PASS: macOS kernel IPC peer ownership rejects stale sidecar attribution")
    response = helper_request(helper, "probe", str(os.getpid()))
    assert response.get("visible") is False, "A CLI process must not have an app window"
    app = (directory / "Smoke Fixture.app").resolve()
    binary = app / "Contents/MacOS/fixture"
    binary.parent.mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleExecutable": "fixture", "CFBundleIdentifier": "ch.mixel.runtime-smoke-fixture",
        "CFBundleName": "Mixel Runtime Smoke Fixture", "CFBundlePackageType": "APPL",
        "LSUIElement": True,
        "CFBundleURLTypes": [{"CFBundleURLSchemes": ["mixel-smoke-fixture"]}],
    }))
    source = directory / "fixture.swift"
    source.write_text(r'''
import AppKit
final class Delegate: NSObject, NSApplicationDelegate {
    let window = NSWindow(contentRect: NSRect(x: 100, y: 100, width: 800, height: 500),
        styleMask: [.titled, .closable], backing: .buffered, defer: false)
    func show() {
        window.title = "Mixel Runtime Smoke Fixture"
        NSApplication.shared.unhide(nil)
        window.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }
    func applicationDidFinishLaunching(_ notification: Notification) { show() }
    func application(_ application: NSApplication, open urls: [URL]) { show() }
}
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let delegate = Delegate()
application.delegate = delegate
application.run()
''', encoding="utf-8")
    compiled = subprocess.run(["xcrun", "swiftc", str(source), "-o", str(binary)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if compiled.returncode:
        raise RuntimeError("macOS smoke fixture compilation failed: " + compiled.stderr[-2000:])
    pid = None
    launch_time = None
    try:
        uri = "mixel-smoke-fixture://ordinary-to-warm"
        launched = helper_request(helper, "ordinary", str(app))
        pid, launch_time = launched["pid"], launched.get("launchTime")
        wait_for("Mac window probe fixture", lambda: probe(helper, pid, app).get("visible"), pid, timeout=15)
        hidden = helper_request(helper, "hide", str(pid)).get("requested") is True
        if hidden:
            wait_for("Mac window probe fixture hide", lambda: probe(helper, pid, app).get("hidden"), pid, timeout=10)
        assert helper_request(helper, "launch", str(app), uri, "warm")["pid"] == pid
        wait_for("Mac window probe fixture warm restore", lambda: probe(helper, pid, app).get("visible"), pid, timeout=15)
        print("PASS: actual macOS accessory fixture ordinary-to-warm URL activation and CoreGraphics visibility work without requesting TCC access")
    finally:
        if pid is not None:
            cleanup_smoke_fixture(helper, pid, app, launch_time)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--diagnostics", type=Path, help="New owned directory for bounded launch failure evidence (not a customer installer)")
    parser.add_argument("--diagnostic-app-zip", type=Path, help="Already signed/stapled diagnostic app ZIP beside --diagnostics")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Actual macOS launch verification requires a macOS runner")
    if not args.self_test and (args.app is None or not args.app.is_dir()):
        parser.error("--app must point to the built Mixel-Remote.app")
    if args.diagnostic_app_zip and (args.self_test or args.diagnostics is None):
        parser.error("--diagnostic-app-zip requires --app and --diagnostics")
    try:
        with tempfile.TemporaryDirectory(prefix="mixel-macos-smoke-") as temporary:
            helper = compile_helper(Path(temporary))
            if args.self_test:
                self_test(helper, Path(temporary))
            else:
                runtime(args.app.resolve(), helper, args.diagnostics.resolve() if args.diagnostics else None,
                        args.diagnostic_app_zip.parent.resolve() / args.diagnostic_app_zip.name if args.diagnostic_app_zip else None)
                print("PASS: actual macOS cold/warm attended-support launch and relay smoke; video/input permissions require customer approval", flush=True)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        detail = safe_error(error)
        print(f"FAIL: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
