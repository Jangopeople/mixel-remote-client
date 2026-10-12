#!/usr/bin/env python3
"""Qualify pinned subwindow ownership, actual Swift startup and bounded close paths."""
import argparse
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mixel_window_patch", ROOT / "scripts/patch-support-macos-window.py")
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def checked(args, output, name):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    (output / (name + ".log")).write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    print("PASS: " + name)
    return result.stdout


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def fixture(root, pubspec):
    (root / "flutter").mkdir(parents=True)
    (root / "Cargo.toml").write_text('[package]\nname = "rustdesk"\nversion = "1.4.6"\n')
    (root / "flutter/pubspec.yaml").write_bytes(pubspec)


def refusal(operation):
    try:
        operation()
    except RuntimeError:
        return
    raise AssertionError("Drift or invalid ownership accepted")


def materialization(output, files):
    original = b"name: rustdesk\n" + P.OLD_DEP + b"\n  untouched: ^1.0\n"
    for label, pubspec in (("lf", original), ("crlf", original.replace(b"\n", b"\r\n"))):
        root = output / label
        fixture(root, pubspec)
        with patch.object(P, "fetch_source", return_value=dict(files)) as fetch:
            P.apply(root)
            first = snapshot(root)
            P.apply(root)
            assert snapshot(root) == first and fetch.call_count == 1
        expected = pubspec.replace(P.OLD_DEP.replace(b"\n", b"\r\n" if label == "crlf" else b"\n"),
                                   P.NEW_DEP.replace(b"\n", b"\r\n" if label == "crlf" else b"\n"))
        assert (root / "flutter/pubspec.yaml").read_bytes() == expected
        owned = P.existing_files(root / "flutter" / P.DEPENDENCY)
        assert all(owned[name] == data for name, data in files.items() if name not in P.REPLACEMENTS)
    checks = 0
    for name in P.REPLACEMENTS:
        fixed = P.patch_file(name, files[name])
        assert fixed != files[name] and P.patch_file(name, fixed) == fixed
        assert P.original_file(name, fixed) == files[name]
        for data in (files[name] + b"\n", fixed + b"\n", fixed.replace(b"return nil", b"return nil // drift", 1)):
            if data == fixed:
                data += b"// drift\n"
            refusal(lambda: P.patch_file(name, data))
            checks += 1
        for old, new in P.REPLACEMENTS[name][1]:
            partial = files[name].replace(old.encode(), new.encode(), 1)
            if partial == fixed:
                continue
            refusal(lambda: P.patch_file(name, partial))
            checks += 1
    for label, mutation in (
        ("missing-guard", lambda d: (d / "macos/Classes/FlutterWindow.swift").write_bytes(files["macos/Classes/FlutterWindow.swift"])),
        ("missing-close-ack", lambda d: (d / "macos/Classes/MultiWindowManager.swift").write_bytes(
            (d / "macos/Classes/MultiWindowManager.swift").read_bytes().replace(
                P.REPLACEMENTS["macos/Classes/MultiWindowManager.swift"][1][-1][1].encode(),
                P.REPLACEMENTS["macos/Classes/MultiWindowManager.swift"][1][-1][0].encode(), 1))),
        ("close-timeout-drift", lambda d: (d / "macos/Classes/MultiWindowManager.swift").write_bytes(
            (d / "macos/Classes/MultiWindowManager.swift").read_bytes().replace(
                b"deadline: .now() + 5", b"deadline: .now() + 6", 1))),
        ("extra-file", lambda d: (d / "extra").write_text("extra")),
        ("extra-directory", lambda d: (d / "extra").mkdir()),
        ("symlink", lambda d: (d / "linked").symlink_to(d / "pubspec.yaml")),
        ("hardlink", lambda d: os.link(d / "pubspec.yaml", d / "linked")),
        ("wrong-provenance", lambda d: (d / ".mixel-source.json").write_text("{}")),
        ("other-platform-drift", lambda d: (d / "windows/flutter_multi_window_plugin.cpp").write_text("drift")),
    ):
        root = output / label
        shutil.copytree(output / "lf", root)
        mutation(root / "flutter" / P.DEPENDENCY)
        before = snapshot(root)
        with patch.object(P, "fetch_source", side_effect=AssertionError("Existing drift fetched remote source")):
            refusal(lambda: P.apply(root))
        assert snapshot(root) == before
        checks += 1
    for label, mutation in (
        ("dependency-drift", lambda r: (r / "flutter/pubspec.yaml").write_bytes(original.replace(b"rustdesk_desktop_multi_window", b"changed_plugin"))),
        ("duplicate-dependency", lambda r: (r / "flutter/pubspec.yaml").write_bytes(original + P.OLD_DEP)),
        ("version-drift", lambda r: (r / "Cargo.toml").write_text('[package]\nversion = "1.4.7"\n')),
        ("parent-symlink", lambda r: (r / "flutter/.mixel-dependencies").symlink_to(output / "lf/flutter/.mixel-dependencies", target_is_directory=True)),
        ("pubspec-symlink", lambda r: ((r / "flutter/pubspec.yaml").unlink(), (r / "flutter/pubspec.yaml").symlink_to(output / "lf/flutter/pubspec.yaml"))),
    ):
        root = output / label
        fixture(root, original)
        mutation(root)
        before = snapshot(root)
        with patch.object(P, "fetch_source", side_effect=AssertionError("Source drift fetched remote source")):
            refusal(lambda: P.apply(root))
        assert snapshot(root) == before
        checks += 1
    for label, member_name, member_type in (("parent-alias", "a/../pubspec.yaml", tarfile.REGTYPE),
                                           ("dot-alias", "./pubspec.yaml", tarfile.REGTYPE),
                                           ("archive-link", "pubspec.yaml", tarfile.SYMTYPE)):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            member = tarfile.TarInfo(member_name)
            member.type = member_type
            member.linkname = "outside" if member_type == tarfile.SYMTYPE else ""
            archive.addfile(member, io.BytesIO(b""))
        refusal(lambda: P.source_files(stream.getvalue()))
        checks += 1
    print(f"PASS: LF/CRLF byte preservation, exact 124-file owned inventory, idempotence, unchanged other platforms and {checks} fatal drift/ownership/archive controls")


def extract_function(source, anchor):
    start = source.index(anchor)
    first = source.index("{", start)
    depth, end = 1, first + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


SWIFT = r'''
import Foundation
var calls: [String] = []
var invalidEvents = 0
var shouldStart = true
var starts = 0
var shutdowns = 0
struct NSRect { init(x: Int, y: Int, width: Int, height: Int) {} }
struct Style: OptionSet {
 let rawValue: Int
 static let miniaturizable=Style(rawValue:1), closable=Style(rawValue:2), resizable=Style(rawValue:4), titled=Style(rawValue:8), fullSizeContentView=Style(rawValue:16)
}
enum Backing { case buffered }
enum Visibility { case hidden }
class NSWindow {
 var contentViewController: FlutterViewController? { didSet { calls.append("attach-view") } }
 weak var delegate: AnyObject? { didSet { calls.append("delegate") } }
 var isReleasedWhenClosed = true
 var titleVisibility: Visibility = .hidden { didSet { emitLayoutEvent() } }
 var titlebarAppearsTransparent = false { didSet { emitLayoutEvent() } }
 init(contentRect: NSRect, styleMask: Style, backing: Backing, defer: Bool) {}
 func emitLayoutEvent() {
  if delegate != nil && contentViewController?.engine.running != true { invalidEvents += 1 }
 }
}
class FlutterWindowInner: NSWindow {}
class FlutterDartProject { var dartEntrypointArguments: [String] = [] }
class Engine {
 var running = false
 func run(withEntrypoint: String?) -> Bool {
  calls.append("run"); starts += 1
  guard shouldStart else { return false }
  running=true; return true
 }
 func shutDownEngine() { calls.append("shutdown"); shutdowns += 1; running=false }
}
class Registrar {}
class FlutterViewController {
 let engine = Engine()
 init(project: FlutterDartProject) { assert(project.dartEntrypointArguments.first == "multi_window") }
 func registrar(forPlugin: String) -> Registrar { calls.append("registrar"); return Registrar() }
}
typealias FlutterResult = (Any?) -> Void
class FlutterError {
 let code: String
 init(code: String, message: String, details: Any?) { self.code=code }
}
class FlutterMethodCall { let arguments: Any?; init(_ arguments: Any?) { self.arguments=arguments } }
class WindowChannel {
 var methodHandler: (() -> Void)?
 static func register(with: Registrar, windowId: Int64) -> WindowChannel { calls.append("channel"); return WindowChannel() }
}
protocol WindowManagerDelegate: AnyObject {}
class BaseFlutterWindow {
 let windowChannel: WindowChannel
 init(window: NSWindow, channel: WindowChannel) { calls.append("base"); windowChannel=channel }
}
class FlutterWindow: BaseFlutterWindow {
 let windowId: Int64
 let window: NSWindow
 weak var delegate: WindowManagerDelegate?
 __INIT__
}
class MultiWindowManager: WindowManagerDelegate {
 static let shared = MultiWindowManager()
 var id: Int64 = 0
 var windows: [Int64: BaseFlutterWindow] = [:]
 func handleMethodCall() {}
 __CREATE__
}
class FlutterMultiWindowPlugin {
 static var RegisterGeneratedPlugins: ((FlutterViewController) -> Void)? = { _ in calls.append("generated-plugins") }
 static var onWindowCreatedCallback: ((FlutterViewController) -> Void)? = { _ in calls.append("app-plugins") }
 static func registerInternal(with: Registrar) { calls.append("internal-plugin") }
 func create(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
 __HANDLE__
 }
}
let original = CommandLine.arguments.contains("original")
for successful in [true, false, true] {
 calls=[]; invalidEvents=0; shouldStart=successful; starts=0; shutdowns=0
 let manager=MultiWindowManager.shared
 manager.windows.removeAll()
 var replies: [Any?] = []
 FlutterMultiWindowPlugin().create(FlutterMethodCall("{\"peer\":\"test\"}")) { replies.append($0) }
 assert(replies.count == 1)
 if original {
  assert(starts==0 && invalidEvents>0 && replies[0] is Int64 && manager.windows.count==1)
 } else if successful {
  assert(starts==1 && shutdowns==0 && invalidEvents==0 && replies[0] is Int64 && manager.windows.count==1)
  let plugin=calls.firstIndex(of:"app-plugins")!, run=calls.firstIndex(of:"run")!, attach=calls.firstIndex(of:"attach-view")!, delegate=calls.firstIndex(of:"delegate")!
  assert(plugin<run && run<attach && attach<delegate)
 } else {
  assert(starts==1 && shutdowns==1 && invalidEvents==0 && manager.windows.isEmpty)
  assert((replies[0] as? FlutterError)?.code == "window_engine_start_failed")
  assert(!calls.contains("attach-view") && !calls.contains("delegate"))
 }
}
print(original ? "PASS: original actual Swift constructor emits invalid-engine layout events and falsely returns a window on startup failure" : "PASS: corrected actual Swift constructor starts after plugins and before view/delegate; failure shuts down, retains no window and returns exactly one FlutterError; later creation succeeds")
'''


CLOSE_SWIFT = r'''
import Foundation
import Dispatch
var events: [String] = []
var shutdownTimes: [Int64: Double] = [:]
var callbacks: [Int64: FlutterResult] = [:]
var mode: [Int64: String] = [:]
var reenter: ((Int64) -> Void)?
var inReply = false
typealias FlutterResult = (Any?) -> Void
final class Engine {
 let id: Int64
 var alive = true
 init(_ id: Int64) { self.id=id }
 func shutdown() {
  assert(alive && !inReply, "Engine released twice or within platform reply")
  alive=false; events.append("shutdown:"+String(id))
  shutdownTimes[id]=ProcessInfo.processInfo.systemUptime
 }
}
final class WindowChannel {
 let engine: Engine
 init(_ engine: Engine) { self.engine=engine }
 func invokeMethod(fromWindowId: Int64, method: String, arguments: Any?, result: FlutterResult? = nil) {
  assert(fromWindowId==0 && method=="onDestroy")
  let id=engine.id
  events.append("send:"+String(id))
  if let result=result { callbacks[id]=result }
  reenter?(id)
  DispatchQueue.main.async { [weak engine] in
   guard engine?.alive == true else { events.append("lost:"+String(id)); return }
   events.append("handler:"+String(id))
   if mode[id]=="reply" {
    DispatchQueue.main.asyncAfter(deadline: .now()+0.03) { [weak engine] in
     guard engine?.alive == true else { events.append("cleanup-lost:"+String(id)); return }
     events.append("cleanup:"+String(id))
     if let reply=callbacks[id] { inReply=true; reply(nil); inReply=false }
    }
   }
  }
 }
}
final class BaseFlutterWindow {
 let engine: Engine
 lazy var windowChannel=WindowChannel(engine)
 init(_ id: Int64) { engine=Engine(id) }
 deinit { engine.shutdown() }
}
final class MultiWindowManager {
 __FIELDS__
 __CLOSE__
 __FINISH__
 func add(_ id: Int64) { windows[id]=BaseFlutterWindow(id) }
 func state() -> (Int,Int,Int) { __STATE__ }
}
func pump(until condition: () -> Bool, timeout: Double) {
 let deadline=ProcessInfo.processInfo.systemUptime+timeout
 while !condition() && ProcessInfo.processInfo.systemUptime<deadline {
  _=RunLoop.current.run(mode: .default, before: Date(timeIntervalSinceNow:0.01))
 }
 assert(condition(), "Controlled async close did not reach deadline")
}
let original=CommandLine.arguments.contains("original")
let manager=MultiWindowManager()
if original {
 manager.add(1); mode[1]="reply"
 manager.onClose(windowId:1)
 assert(events==["send:1","shutdown:1"])
 pump(until: { events.contains("lost:1") }, timeout:1)
 assert(!events.contains("handler:1") && !events.contains("cleanup:1"))
 print("PASS: original exact native close shuts down before deferred Dart handler/cleanup")
} else {
 manager.add(1); mode[1]="reply"
 reenter = { [weak manager] id in manager?.onClose(windowId:id) }
 manager.onClose(windowId:1)
 reenter = nil
 manager.onClose(windowId:1) // duplicate/reentrant notification must not send twice
 assert(manager.state()==(0,1,1) && !events.contains("shutdown:1"))
 pump(until: { events.contains("shutdown:1") }, timeout:1)
 assert(events==["send:1","handler:1","cleanup:1","shutdown:1"])
 assert(manager.state()==(0,0,0))
 if let reply=callbacks[1] { inReply=true; reply(nil); reply(nil); inReply=false }
 pump(until: { manager.state()==(0,0,0) }, timeout:0.1)

 let started=ProcessInfo.processInfo.systemUptime
 for id: Int64 in [2,3,4] { manager.add(id); mode[id]="hung"; manager.onClose(windowId:id) }
 manager.onClose(windowId:2)
 assert(manager.state()==(0,3,3))
 DispatchQueue.main.asyncAfter(deadline: .now()+4.95) {
  if let reply=callbacks[3] { inReply=true; reply(nil); inReply=false }
 }
 DispatchQueue.main.asyncAfter(deadline: .now()+5.05) {
  if let reply=callbacks[4] { inReply=true; reply(nil); reply(nil); inReply=false }
  events.append("late-replies")
 }
 pump(until: { manager.state()==(0,0,0) && events.contains("late-replies") }, timeout:7)
 for id: Int64 in [1,2,3,4] {
  assert(events.filter { $0=="send:"+String(id) }.count==1)
  assert(events.filter { $0=="shutdown:"+String(id) }.count==1)
 }
 let elapsed=shutdownTimes[2]!-started
 assert(elapsed>=4.9 && elapsed<6.5, "Hung fallback did not use actual five-second main queue deadline")
 assert(shutdownTimes[3]!<shutdownTimes[2]!, "Pre-timeout reply did not win")
 assert(shutdownTimes[4]!>=shutdownTimes[2]!-0.05, "Late reply did not use fallback")
 print("PASS: corrected exact native close retains engine through async handler/reply, defers release outside callback, rejects reentrant close, cancels successful timeout, and releases once for actual 5s hung fallback and both reply/timeout race orders")
 print("OBSERVED: hung fallback seconds="+String(elapsed))
}
print("BOUNDARY: exact extracted Swift methods with asynchronous channel/engine doubles; no packaged Dart or complete remote/file disposal claim")
'''


def native_controls(output, files, framework):
    compiler = shutil.which("swiftc")
    assert compiler and sys.platform == "darwin", "Native Mac Swift compiler required"
    for label in ("original", "corrected"):
        sources = {name: (P.patch_file(name, data) if label == "corrected" and name in P.REPLACEMENTS else data)
                   for name, data in files.items()}
        window = sources["macos/Classes/FlutterWindow.swift"].decode()
        manager = sources["macos/Classes/MultiWindowManager.swift"].decode()
        plugin = sources["macos/Classes/FlutterMultiWindowPlugin.swift"].decode()
        initializer = extract_function(window, "  init?(id:" if label == "corrected" else "  init(id:")
        create = extract_function(manager, "  func create(arguments:")
        handle = plugin.split('    case "createWindow":\n', 1)[1].split('    case "show":', 1)[0]
        source = SWIFT.replace("__INIT__", initializer).replace("__CREATE__", create).replace("__HANDLE__", handle)
        path = output / (label + "-actual-startup.swift")
        path.write_text(source)
        binary = output / (label + "-actual-startup")
        checked([compiler, "-warnings-as-errors", str(path), "-o", str(binary)], output, label + "-swift-compile")
        text = checked([str(binary), label], output, label + "-actual-startup")
        assert "PASS: " + label + " actual Swift constructor" in text
        declarations = "\n".join(line for line in manager.splitlines()
                                 if line.startswith("  private var ") and any(
                                     field in line for field in (" windows:", " closingWindows:", " closeTimeouts:")))
        on_close = extract_function(manager, "  func onClose(windowId: Int64) {")
        finish = extract_function(manager, "  private func finishClosingWindow(") if label == "corrected" else ""
        state = "return (windows.count,closingWindows.count,closeTimeouts.count)" if label == "corrected" else "return (windows.count,0,0)"
        close_source = CLOSE_SWIFT.replace("__FIELDS__", declarations).replace("__CLOSE__", on_close).replace(
            "__FINISH__", finish).replace("__STATE__", state)
        close_path = output / (label + "-actual-close.swift")
        close_path.write_text(close_source)
        close_binary = output / (label + "-actual-close")
        checked([compiler, "-warnings-as-errors", str(close_path), "-o", str(close_binary)], output, label + "-close-swift-compile")
        close_text = checked([str(close_binary), label], output, label + "-actual-close")
        assert "PASS: " + label + " exact native close" in close_text
    if framework:
        framework = framework.resolve()
        assert framework.name == "FlutterMacOS.framework" and framework.is_dir(), "Actual FlutterMacOS framework required"
        module = output / "actual-plugin"
        module.mkdir()
        paths = []
        for name in sorted(files):
            if name.startswith("macos/Classes/") and name.endswith(".swift"):
                path = module / Path(name).name
                path.write_bytes(P.patch_file(name, files[name]) if name in P.REPLACEMENTS else files[name])
                paths.append(str(path))
        assert len(paths) == 4
        sdk = subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip()
        for arch, minimum in (("arm64", "12.3"), ("x86_64", "10.14")):
            checked([compiler, "-typecheck", "-warnings-as-errors", "-sdk", sdk,
                     "-target", arch + "-apple-macos" + minimum, "-F", str(framework.parent)] + paths,
                    output, "actual-pinned-framework-" + arch + "-complete-plugin-typecheck")
        print("PASS: all four complete Swift plugin sources typecheck against actual pinned Flutter framework on arm64 and x86_64")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--native", action="store_true", default=sys.platform == "darwin")
    parser.add_argument("--flutter-framework", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="mixel-mac-window-test-") as temporary:
        output = args.evidence_dir.resolve() if args.evidence_dir else Path(temporary)
        output.mkdir(parents=True, exist_ok=True)
        files = P.fetch_source()
        assert P.inventory(files) == P.INVENTORY_SHA
        materialization(output, files)
        if args.native or args.flutter_framework:
            native_controls(output, files, args.flutter_framework)
    print("PASS: pinned Mac subwindow startup, bounded close acknowledgement and ownership regression qualification")


if __name__ == "__main__":
    main()
