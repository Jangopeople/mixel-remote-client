#!/usr/bin/env python3
"""Test real macOS URL activation, customer UI and incoming support IPC.

Targets the exact built app through LaunchServices using synthetic invalid
credentials. Never grants TCC permissions or starts a remote session. A pass
proves launching, the attended guard and relay registration, not screen/input
permissions or video capture on a customer Mac.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import signal
import subprocess
import sys
import tempfile
import time


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mixel_unix_smoke", SCRIPT_DIRECTORY / "test-support-launch-linux.py")
unix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unix)

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
if action == "launch" {
    if arguments.count != 5 { exit(2) }
    let application = URL(fileURLWithPath: arguments[2]).resolvingSymlinksInPath()
    guard let supportURL = URL(string: arguments[3]) else { exit(2) }
    if arguments[4] == "cold" && NSWorkspace.shared.runningApplications.contains(where: {
        $0.bundleURL?.resolvingSymlinksInPath() == application
    }) { emit(["error": "Built application is already running"]); exit(1) }
    let configuration = NSWorkspace.OpenConfiguration()
    configuration.activates = true
    configuration.createsNewApplicationInstance = false
    configuration.allowsRunningApplicationSubstitution = false
    configuration.addsToRecentItems = false
    var finished = false
    var result: [String: Any] = ["error": "App URL activation timed out"]
    NSWorkspace.shared.open([supportURL], withApplicationAt: application, configuration: configuration) { app, error in
        if let app = app, error == nil {
            result = ["pid": app.processIdentifier]
        } else {
            result = ["error": "App URL activation failed"]
        }
        finished = true
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
      "hidden": application.isHidden, "bundlePath": application.bundleURL?.resolvingSymlinksInPath().path ?? ""])
"""


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


def wait_for(description: str, operation, pid: int, timeout: float = 60):
    deadline = time.monotonic() + timeout
    last = "not ready"
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            raise RuntimeError(f"{description}: customer app exited") from None
        try:
            value = operation()
            if value:
                return value
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            last = str(error)
        time.sleep(0.25)
    raise RuntimeError(f"{description}: {last}")


def probe(helper: Path, pid: int, app: Path):
    result = helper_request(helper, "probe", str(pid))
    returned_path = result.get("bundlePath")
    if not isinstance(returned_path, str) or Path(returned_path).resolve() != app.resolve():
        raise RuntimeError("Window probe targets a different application bundle")
    return result


def wait_health(pid: int, scenario: str) -> None:
    def ready():
        result = unix.runtime_health(pid)
        return result if result[0] > 0 and result[1] else None
    state, confirmed = wait_for(scenario + " incoming server", ready, pid)
    print(f"PASS: {scenario} actual incoming IPC proves attended-runtime-v1, branded relay, registered ID, online state={state}, keyConfirmed={str(confirmed).lower()}", flush=True)


def runtime(app: Path, helper: Path) -> None:
    if unix.IPC_PATH.exists() or unix.IPC_PATH.with_suffix(".pid").exists():
        raise RuntimeError("A Mixel Remote IPC endpoint already exists; use a fresh isolated macOS runner")
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if info.get("CFBundleIdentifier") != "ch.mixel.remote":
        raise RuntimeError("Built app has an unexpected bundle identity")
    schemes = [scheme for entry in info.get("CFBundleURLTypes", []) for scheme in entry.get("CFBundleURLSchemes", [])]
    if "mixel-remote" not in schemes:
        raise RuntimeError("Built app is missing the branded support URL handler")
    log_root = Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library/Logs/Mixel-Remote"
    offsets = {path: path.stat().st_size for path in log_root.rglob("*") if path.is_file()} if log_root.exists() else {}
    pid = None
    try:
        launched = helper_request(helper, "launch", str(app), unix.URI, "cold")
        pid = launched.get("pid")
        if type(pid) is not int or pid <= 0:
            raise RuntimeError("macOS cold URL launch returned no application PID")
        wait_for("Cold support URL customer window", lambda: probe(helper, pid, app).get("visible"), pid)
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if not probe(helper, pid, app).get("visible"):
                raise RuntimeError("Cold support URL hid the app after initialisation")
            time.sleep(0.25)
        print("PASS: cold macOS support URL activation opens a stable visible customer window", flush=True)
        wait_health(pid, "Cold support URL launch")
        hidden = helper_request(helper, "hide", str(pid)).get("requested") is True
        if hidden:
            wait_for("Warm support URL hide setup", lambda: probe(helper, pid, app).get("hidden") and not probe(helper, pid, app).get("visible"), pid, timeout=10)
        warm = helper_request(helper, "launch", str(app), unix.URI, "warm")
        if warm.get("pid") != pid:
            raise RuntimeError("macOS warm URL activation created a different customer application")
        wait_for("Warm support URL customer window", lambda: probe(helper, pid, app).get("visible"), pid)
        print("PASS: warm macOS support URL activation " + ("restores the same hidden customer application" if hidden else "reuses the same visible accessory customer application"), flush=True)
        wait_health(pid, "Warm support URL launch")
    finally:
        if pid is not None:
            # Only terminate the exact bundle launched by this test.
            try:
                if probe(helper, pid, app).get("running"):
                    os.kill(pid, signal.SIGTERM)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        try:
                            os.kill(pid, 0)
                        except ProcessLookupError:
                            break
                        time.sleep(0.1)
                    else:
                        os.kill(pid, signal.SIGKILL)
                pid_file = unix.IPC_PATH.with_suffix(".pid")
                if int(pid_file.read_text(encoding="utf-8").strip()) == pid:
                    unix.IPC_PATH.unlink(missing_ok=True)
                    pid_file.unlink(missing_ok=True)
            except (OSError, RuntimeError, ValueError):
                pass
    sensitive = (unix.TOKEN.encode(), unix.API_KEY.encode(), b"mixel-remote://support/?invite=")
    for path in log_root.rglob("*") if log_root.exists() else []:
        if not path.is_file():
            continue
        with path.open("rb") as source:
            old_offset = offsets.get(path, 0)
            if path.stat().st_size >= old_offset:
                source.seek(old_offset)
            if any(value in source.read() for value in sensitive):
                raise RuntimeError("Support invite bearer appeared in macOS app logs")
    print("PASS: synthetic support invite bearer and API key absent from macOS app logs", flush=True)


def self_test(helper: Path, directory: Path) -> None:
    unix.self_test()
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
    try:
        uri = "mixel-smoke-fixture://fixture"
        pid = helper_request(helper, "launch", str(app), uri, "cold")["pid"]
        wait_for("Mac window probe fixture", lambda: probe(helper, pid, app).get("visible"), pid, timeout=15)
        hidden = helper_request(helper, "hide", str(pid)).get("requested") is True
        if hidden:
            wait_for("Mac window probe fixture hide", lambda: probe(helper, pid, app).get("hidden"), pid, timeout=10)
        assert helper_request(helper, "launch", str(app), uri, "warm")["pid"] == pid
        wait_for("Mac window probe fixture warm restore", lambda: probe(helper, pid, app).get("visible"), pid, timeout=15)
        print("PASS: actual macOS accessory fixture cold/warm URL activation and CoreGraphics visibility work without requesting TCC access")
    finally:
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Actual macOS launch verification requires a macOS runner")
    if not args.self_test and (args.app is None or not args.app.is_dir()):
        parser.error("--app must point to the built Mixel-Remote.app")
    try:
        with tempfile.TemporaryDirectory(prefix="mixel-macos-smoke-") as temporary:
            helper = compile_helper(Path(temporary))
            if args.self_test:
                self_test(helper, Path(temporary))
            else:
                runtime(args.app.resolve(), helper)
                print("PASS: actual macOS cold/warm attended-support launch and relay smoke; video/input permissions require customer approval", flush=True)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        detail = str(error).replace(unix.URI, "[support URI]").replace(unix.TOKEN, "[invite]").replace(unix.API_KEY, "[API key]")
        print(f"FAIL: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
