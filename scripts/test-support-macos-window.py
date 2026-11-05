#!/usr/bin/env python3
"""Qualify pinned Mac frame readiness, startup, lifecycle and bounded close paths."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from unittest.mock import patch
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mixel_window_patch", ROOT / "scripts/patch-support-macos-window.py")
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)

ENGINE = "a18df97ca57a249df5d8d68cd0820600223ce262"
ENGINE_FILES = {
    "Source/FlutterEngine.mm": "1e59b42da1a82999b5d902ad94f4036265ce3f1dea9ccaae04fee1e89d3a5d5c",
    "Source/FlutterAppLifecycleDelegate.mm": "f04648c8a439c7cc1cad3106ac5100f2b6e0ec5c0747d6fa20b4e87568666a42",
    "Headers/FlutterEngine.h": "96d684b07db41db3912cb3f1d917da856726f0b5b70237a4ae743327c9998710",
    "Headers/FlutterAppLifecycleDelegate.h": "a2af9087215714bda2d9720a1fa765db8c1caf44f79f6da9b36fa8567190ace7",
    "Source/FlutterViewController.mm": "3ed9dfe183995bda1ec85bdabe7eb55939951486a97ab39503f28e6a009e5845",
}

FRAME_ENGINE_FILES = {
    "shell/common/engine.cc": "533337a3f83729b8d7ff925dd35e1af6369af93f9050cb62defb849064f617cb",
    "shell/common/animator.cc": "8454701fbc99fb3ac4d34a7774c54b14faebc9d16a515e6d83eb141c7460f7f0",
    "shell/common/vsync_waiter.cc": "46c8896cb4b28fc954d47246b4a1da88ac1419113d45718fa779ec0f249e2002",
}

SDK_PROFILES = {
    "3.24.5": {"engine": ENGINE, "native": ENGINE_FILES, "frame": FRAME_ENGINE_FILES},
    "3.27.4": {
        "engine": "82bd5b7209295a5b7ff8cae0df96e7870171e3a5",
        "native": dict(ENGINE_FILES, **{
            "Source/FlutterEngine.mm": "76c759afa2356594198986da8a35a3dd2eea8255936f8666d1f9e82494a9d7dc",
            "Source/FlutterViewController.mm": "346a89442eb07dbe7e63a83890fa9507131f5a6c35c5f207859e3d72dfe40a4e",
            "Headers/FlutterEngine.h": "87a325c6c9089f2c4fec8215a1ec717a7fc0fd4d7d2c5f2a82fe16b890c3bae7",
        }),
        "frame": dict(FRAME_ENGINE_FILES, **{
            "shell/common/engine.cc": "76204b363afd759104b763a9ff5aea959faf6da121ff165eb39c87326935230e",
        }),
    },
}


def framework_profile(framework):
    engine_header = P.sha((framework / "Headers/FlutterEngine.h").read_bytes())
    lifecycle_header = P.sha((framework / "Headers/FlutterAppLifecycleDelegate.h").read_bytes())
    matched = [version for version, profile in SDK_PROFILES.items()
               if engine_header == profile["native"]["Headers/FlutterEngine.h"] and
               lifecycle_header == profile["native"]["Headers/FlutterAppLifecycleDelegate.h"]]
    assert len(matched) == 1, "Actual Flutter SDK public header drift"
    return matched[0]


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


def windows_resource_controls(files):
    original = files["windows/icon.rc"]
    corrected = P.patch_file("windows/icon.rc", original)
    text = corrected.decode("utf-8")
    expected = {
        "CompanyName": "Mixel IT and Corporate Services GmbH",
        "FileDescription": "Mixel Remote",
        "InternalName": "desktop_multi_window_plugin",
        "LegalCopyright": "Copyright © 2026 Mixel IT and Corporate Services GmbH. All rights reserved.",
        "OriginalFilename": "desktop_multi_window_plugin.dll",
        "ProductName": "Mixel Remote",
    }
    values = dict(re.findall(r'^\s*VALUE "([^"]+)", "([^"]*)"', text, re.M))
    assert all(values.get(key) == value for key, value in expected.items())
    assert '#define VERSION_AS_NUMBER 1,4,6,0' in text
    assert '#define VERSION_AS_STRING "1.4.6"' in text
    assert ' FILETYPE VFT_DLL' in text and ' FILETYPE VFT_APP' not in text
    assert not re.search(r'^\s*[^/\s]+\s+ICON\s', text, re.M)
    assert not re.search(r'com\.carriez|Purslane|RustDesk|flutter_hbb', text, re.I)
    # The actual plugin asks the process executable for its icon, so its own
    # upstream app icon can be omitted without changing window icon loading.
    window = files["windows/flutter_window.cc"].decode("utf-8")
    assert 'window_class.hInstance = GetModuleHandle(nullptr);' in window
    assert 'LoadIcon(window_class.hInstance, MAKEINTRESOURCE(IDI_APP_ICON));' in window
    assert '"icon.rc"' in files["windows/CMakeLists.txt"].decode("utf-8")
    assert P.original_file("windows/icon.rc", corrected) == original
    assert P.patch_file("windows/icon.rc", corrected) == corrected
    assert b'com.carriez' in original and b'VFT_APP' in original
    print("PASS: original plugin publisher leak reproduced; corrected exact DLL identity/version/type and omitted unused executable-owned icon; complete resource inverse/idempotence")


def materialization(output, files):
    windows_resource_controls(files)
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
        ("missing-occlusion-sync", lambda d: (d / "macos/Classes/FlutterWindow.swift").write_bytes(
            (d / "macos/Classes/FlutterWindow.swift").read_bytes().replace(
                b"engine.handleDidChangeOcclusionState(", b"engine.removedOcclusionSync(", 1))),
        ("missing-view-readiness", lambda d: (d / "macos/Classes/FlutterWindow.swift").write_bytes(
            (d / "macos/Classes/FlutterWindow.swift").read_bytes().replace(
                b"    _ = flutterViewController.view\n", b"", 1))),
        ("missing-active-sync", lambda d: (d / "macos/Classes/FlutterWindow.swift").write_bytes(
            (d / "macos/Classes/FlutterWindow.swift").read_bytes().replace(
                b"engine.handleWillBecomeActive(", b"engine.removedActiveSync(", 1))),
        ("missing-inactive-sync", lambda d: (d / "macos/Classes/FlutterWindow.swift").write_bytes(
            (d / "macos/Classes/FlutterWindow.swift").read_bytes().replace(
                b"engine.handleWillResignActive(", b"engine.removedInactiveSync(", 1))),
        ("assumed-active-state", lambda d: (d / "macos/Classes/FlutterWindow.swift").write_bytes(
            (d / "macos/Classes/FlutterWindow.swift").read_bytes().replace(
                b"let applicationIsActive = application.isActive", b"let applicationIsActive = true", 1))),
        ("extra-file", lambda d: (d / "extra").write_text("extra")),
        ("extra-directory", lambda d: (d / "extra").mkdir()),
        ("symlink", lambda d: (d / "linked").symlink_to(d / "pubspec.yaml")),
        ("hardlink", lambda d: os.link(d / "pubspec.yaml", d / "linked")),
        ("wrong-provenance", lambda d: (d / ".mixel-source.json").write_text("{}")),
        ("other-platform-drift", lambda d: (d / "windows/flutter_multi_window_plugin.cpp").write_text("drift")),
        ("old-Mac-only-owned-profile", lambda d: (d / "windows/icon.rc").write_bytes(files["windows/icon.rc"])),
        ("restored-upstream-plugin-icon", lambda d: (d / "windows/icon.rc").write_bytes(
            (d / "windows/icon.rc").read_bytes().replace(
                P.REPLACEMENTS["windows/icon.rc"][1][0][1].encode(),
                P.REPLACEMENTS["windows/icon.rc"][1][0][0].encode(), 1))),
        ("plugin-resource-drift", lambda d: (d / "windows/icon.rc").write_bytes(
            (d / "windows/icon.rc").read_bytes().replace(b"Mixel Remote", b"Changed Remote", 1))),
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
    print(f"PASS: LF/CRLF byte preservation, exact 124-file owned inventory, idempotence, unchanged unpatched sources and {checks} fatal drift/ownership/archive controls")


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
var invalidFrameRequests = 0
struct NSRect { init(x: Int, y: Int, width: Int, height: Int) {} }
struct Style: OptionSet {
 let rawValue: Int
 static let miniaturizable=Style(rawValue:1), closable=Style(rawValue:2), resizable=Style(rawValue:4), titled=Style(rawValue:8), fullSizeContentView=Style(rawValue:16)
}
enum Backing { case buffered }
enum Visibility { case hidden }
struct Occlusion: OptionSet {
 let rawValue: Int
 static let visible = Occlusion(rawValue: 1)
}
class NSApplication {
 static let shared = NSApplication()
 static let didChangeOcclusionStateNotification = Notification.Name("occlusion")
 static let willBecomeActiveNotification = Notification.Name("active")
 static let willResignActiveNotification = Notification.Name("inactive")
 var isActive = true
 var occlusionState: Occlusion = .visible
}
class NSWindow {
 var contentViewController: FlutterViewController? { didSet { calls.append("attach-view"); _ = contentViewController?.view } }
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
 var viewReady = false
 var active = false
 var visible = false
 var lifecycleStates: [String] = []
 func run(withEntrypoint: String?) -> Bool {
  calls.append("run"); starts += 1
  guard shouldStart else { return false }
  if !viewReady { invalidFrameRequests += 1 }
  running=true; return true
 }
 func shutDownEngine() { calls.append("shutdown"); shutdowns += 1; running=false }
 func emitLifecycle(_ state: String) { assert(running); lifecycleStates.append(state) }
 func handleDidChangeOcclusionState(_ note: Notification) {
   assert(note.name == NSApplication.didChangeOcclusionStateNotification && note.object as? NSApplication === NSApplication.shared)
   calls.append("lifecycle-occlusion")
   self.visible = NSApplication.shared.occlusionState.contains(.visible)
   self.emitLifecycle(self.visible ? (self.active ? "resumed" : "inactive") : "hidden")
 }
 func handleWillBecomeActive(_ note: Notification) {
   assert(note.name == NSApplication.willBecomeActiveNotification && note.object as? NSApplication === NSApplication.shared)
   calls.append("lifecycle-active"); self.active=true
   self.emitLifecycle(self.visible ? "resumed" : "hidden")
 }
 func handleWillResignActive(_ note: Notification) {
   assert(note.name == NSApplication.willResignActiveNotification && note.object as? NSApplication === NSApplication.shared)
   calls.append("lifecycle-inactive"); self.active=false
   self.emitLifecycle(self.visible ? "inactive" : "hidden")
 }
}
class Registrar {}
class FlutterViewController {
 let engine = Engine()
 private var loaded = false
 var view: Int {
   if !loaded { loaded=true; calls.append("load-view"); engine.viewReady=true }
   return 0
 }
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
let viewAfterRun = CommandLine.arguments.contains("view-after-run")
for visible in [true, false] {
for active in [true, false] {
for successful in [true, false, true] {
 calls=[]; invalidEvents=0; shouldStart=successful; starts=0; shutdowns=0; invalidFrameRequests=0
 NSApplication.shared.isActive=active
 NSApplication.shared.occlusionState=visible ? .visible : []
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
  let occlusion=calls.firstIndex(of:"lifecycle-occlusion")!, activity=calls.firstIndex(of:active ? "lifecycle-active" : "lifecycle-inactive")!
  assert(plugin<run && run<occlusion && occlusion<activity && activity<attach && attach<delegate)
  let load=calls.firstIndex(of:"load-view")!
  assert(viewAfterRun ? (run<load && invalidFrameRequests==1) : (plugin<load && load<run && invalidFrameRequests==0))
  let engine=(manager.windows.values.first as! FlutterWindow).window.contentViewController!.engine
  assert(engine.visible==visible && engine.active==active)
  assert(engine.lifecycleStates.last==(visible ? (active ? "resumed" : "inactive") : "hidden"))
 } else {
  assert(starts==1 && shutdowns==1 && invalidEvents==0 && manager.windows.isEmpty)
  assert((replies[0] as? FlutterError)?.code == "window_engine_start_failed")
  assert(!calls.contains("attach-view") && !calls.contains("delegate"))
  assert(invalidFrameRequests==0)
  assert(!calls.contains(where: { $0.hasPrefix("lifecycle-") }))
 }
 if successful {
  let engine=(manager.windows.values.first as! FlutterWindow).window.contentViewController!.engine
  if original {
   assert(engine.lifecycleStates.isEmpty)
   assert(engine.run(withEntrypoint:nil))
   engine.handleWillBecomeActive(Notification(name:NSApplication.willBecomeActiveNotification,object:NSApplication.shared))
   assert(engine.lifecycleStates.last=="hidden")
   // A real later occlusion callback repairs the original visibility cache.
   engine.handleDidChangeOcclusionState(Notification(name:NSApplication.didChangeOcclusionStateNotification,object:NSApplication.shared))
  }
  NSApplication.shared.isActive=false
  engine.handleWillResignActive(Notification(name:NSApplication.willResignActiveNotification,object:NSApplication.shared))
  assert(engine.lifecycleStates.last==(visible ? "inactive" : "hidden"))
  NSApplication.shared.isActive=true
  engine.handleWillBecomeActive(Notification(name:NSApplication.willBecomeActiveNotification,object:NSApplication.shared))
  assert(engine.lifecycleStates.last==(visible ? "resumed" : "hidden"))
  NSApplication.shared.occlusionState=[]
  engine.handleDidChangeOcclusionState(Notification(name:NSApplication.didChangeOcclusionStateNotification,object:NSApplication.shared))
  assert(engine.lifecycleStates.last=="hidden")
  NSApplication.shared.occlusionState=visible ? .visible : []
  engine.handleDidChangeOcclusionState(Notification(name:NSApplication.didChangeOcclusionStateNotification,object:NSApplication.shared))
  assert(engine.lifecycleStates.last==(visible ? "resumed" : "hidden"))
 }
}
}
}
print(original ? "PASS: original actual Swift constructor emits invalid-engine layout events and falsely returns a window on startup failure" : "PASS: corrected actual Swift constructor starts after plugins and before view/delegate; failure shuts down, retains no window and returns exactly one FlutterError; later creation succeeds")
print(original ? "PASS: original late-engine lifecycle is unseeded; focus notification reports hidden while app is actually visible until an occlusion callback" : "PASS: corrected actual constructor synchronizes visible/hidden and active/inactive snapshots only after successful start; later focus and occlusion callbacks preserve legitimate states, with no callback on failed startup")
if !original {
 print(viewAfterRun ? "PASS: previous actual constructor can request its initial frame before the native view waiter is ready" : "PASS: corrected actual constructor loads the public view after plugins and before run; repeated view access reuses readiness and failed startup emits no frame")
}
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
 manager.add(2); mode[2]="hung"; manager.onClose(windowId:2)
 manager.onClose(windowId:2)
 assert(manager.state()==(0,1,1))
 pump(until: { manager.state()==(0,0,0) }, timeout:20)
 for id: Int64 in [1,2] {
  assert(events.filter { $0=="send:"+String(id) }.count==1)
  assert(events.filter { $0=="shutdown:"+String(id) }.count==1)
 }
 let elapsed=shutdownTimes[2]!-started
 assert(elapsed>=4.9 && elapsed<20, "Hung fallback did not reach its actual five-second main queue deadline")
 print("PASS: corrected exact native close retains engine through async handler/reply, defers release outside callback, rejects reentrant close, cancels successful timeout, and releases once for actual 5s hung fallback")
 print("OBSERVED: hung fallback seconds="+String(elapsed))
}
print("BOUNDARY: exact extracted Swift methods with asynchronous channel/engine doubles; no packaged Dart or complete remote/file disposal claim")
'''


CLOSE_RACE_SWIFT = r'''
import Foundation
typealias FlutterResult = (Any?) -> Void
var events: [String] = []
var callbacks: [Int64: FlutterResult] = [:]
var inReply = false
func debugPrint(_ value: Any) {}

// Execute the exact extracted production methods with explicit delivery order.
// A nominal 4.95s callback may run after a queued 5s timer on a loaded host;
// delivery of a reply also queues its release for a later main-queue turn.
final class DispatchQueue {
 static let main = DispatchQueue()
 var pending: [() -> Void] = []
 var timers: [DispatchWorkItem] = []
 func async(execute: @escaping () -> Void) { pending.append(execute) }
 func asyncAfter(deadline: DispatchTime, execute: DispatchWorkItem) {
  let remaining = Double(deadline.uptimeNanoseconds - DispatchTime.now().uptimeNanoseconds) / 1e9
  assert(remaining > 4.9 && remaining <= 5, "Exact production deadline changed")
  timers.append(execute)
 }
 func runMainTurn() { assert(!inReply); let action=pending.removeFirst(); action() }
 func runAll() { while !pending.isEmpty { runMainTurn() } }
 func fireTimer() { assert(!inReply && timers.count==1); if !timers[0].isCancelled { timers[0].perform() } }
 func reset() { pending.removeAll(); timers.removeAll() }
}
final class Engine {
 let id: Int64
 init(_ id: Int64) { self.id=id }
 func shutdown() { assert(!inReply, "Engine shutdown inside platform reply"); events.append("shutdown:"+String(id)) }
}
final class WindowChannel {
 let engine: Engine
 init(_ engine: Engine) { self.engine=engine }
 func invokeMethod(fromWindowId: Int64, method: String, arguments: Any?, result: FlutterResult?=nil) {
  assert(fromWindowId==0 && method=="onDestroy")
  events.append("send:"+String(engine.id)); callbacks[engine.id]=result
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
func deliverReply(_ id: Int64) {
 inReply=true; callbacks[id]!(nil); inReply=false
}
for order in ["reply-release-first", "timeout-first", "reply-delivered-timeout-before-release"] {
 events=[]; callbacks=[:]; DispatchQueue.main.reset()
 let manager=MultiWindowManager(); let id: Int64=1
 manager.add(id); manager.onClose(windowId:id); manager.onClose(windowId:id)
 assert(manager.state()==(0,1,1) && events==["send:1"])
 if order=="reply-release-first" {
  deliverReply(id)
  assert(manager.state()==(0,1,1) && events==["send:1"])
  DispatchQueue.main.runMainTurn(); DispatchQueue.main.fireTimer()
 } else if order=="timeout-first" {
  DispatchQueue.main.fireTimer(); deliverReply(id)
 } else {
  deliverReply(id); DispatchQueue.main.fireTimer()
 }
 deliverReply(id); deliverReply(id); manager.onClose(windowId:id)
 DispatchQueue.main.runAll(); DispatchQueue.main.fireTimer()
 assert(manager.state()==(0,0,0) && events==["send:1","shutdown:1"])
 assert(DispatchQueue.main.timers[0].isCancelled)
 print("PASS: exact native close queue order "+order+" releases once outside reply, cancels timeout and rejects duplicate/late replies")
}
print("BOUNDARY: exact extracted Swift methods with controlled queue delivery; actual five-second fallback is exercised separately")
'''


ENGINE_OBJC = r'''
#import <Foundation/Foundation.h>
#include <cassert>
#include <iostream>
using NSApplicationOcclusionState=NSUInteger;
static const NSApplicationOcclusionState NSApplicationOcclusionStateVisible=1;
namespace flutter { enum class AppLifecycleState { kHidden,kResumed,kInactive }; }
@interface NSApplication:NSObject
@property(nonatomic) NSApplicationOcclusionState occlusionState;
+ (instancetype)sharedApplication;
@end
@implementation NSApplication
+ (instancetype)sharedApplication { static NSApplication* instance; if(!instance) instance=[NSApplication new]; return instance; }
@end
@interface EngineControl:NSObject {
  BOOL _active;
  BOOL _visible;
}
@property(nonatomic) flutter::AppLifecycleState last;
- (void)setApplicationState:(flutter::AppLifecycleState)state;
- (void)handleWillBecomeActive:(NSNotification*)notification;
- (void)handleWillResignActive:(NSNotification*)notification;
- (void)handleDidChangeOcclusionState:(NSNotification*)notification;
@end
@implementation EngineControl
- (instancetype)init { if(self=[super init]) { __INITIAL_STATE__ } return self; }
- (void)setApplicationState:(flutter::AppLifecycleState)state { self.last=state; }
__HANDLERS__
@end
int main() { @autoreleasepool {
  NSApplication* app=[NSApplication sharedApplication];
  NSNotification* note=[NSNotification notificationWithName:@"source-control-only" object:app];
  app.occlusionState=NSApplicationOcclusionStateVisible;
  EngineControl* original=[EngineControl new];
  [original handleWillBecomeActive:note];assert(original.last==flutter::AppLifecycleState::kHidden);
  [original handleWillResignActive:note];assert(original.last==flutter::AppLifecycleState::kHidden);
  [original handleWillBecomeActive:note];assert(original.last==flutter::AppLifecycleState::kHidden);
  std::cout<<"PASS: exact pinned engine initializes visibility false; late activate/resign callbacks report hidden despite an actually visible observed app.\n";
  for (bool visible : {false,true}) {
    for (bool active : {false,true}) {
      app.occlusionState=visible ? NSApplicationOcclusionStateVisible : 0;
      EngineControl* corrected=[EngineControl new];
      [corrected handleDidChangeOcclusionState:note];
      if(active) [corrected handleWillBecomeActive:note];
      else [corrected handleWillResignActive:note];
      using State=flutter::AppLifecycleState;
      assert(corrected.last==(visible ? (active ? State::kResumed : State::kInactive) : State::kHidden));
      [corrected handleWillResignActive:note];
      assert(corrected.last==(visible ? State::kInactive : State::kHidden));
      [corrected handleWillBecomeActive:note];
      assert(corrected.last==(visible ? State::kResumed : State::kHidden));
      app.occlusionState=0;[corrected handleDidChangeOcclusionState:note];
      assert(corrected.last==State::kHidden);
      [corrected handleWillBecomeActive:note];assert(corrected.last==State::kHidden);
      app.occlusionState=NSApplicationOcclusionStateVisible;
      [corrected handleDidChangeOcclusionState:note];assert(corrected.last==State::kResumed);
    }
  }
  std::cout<<"PASS: exact pinned handlers synchronize all four actual visibility/activity states and preserve future focus/hide/restore callbacks without forced frames or geometry.\n";
  std::cout<<"BOUNDARY: exact original engine handlers with observed-application doubles; no live viewer cause or packaged painting repair claim.\n";
} }
'''



FRAME_OBJC = r'''
#import <Foundation/Foundation.h>
#import <dispatch/dispatch.h>
#include <cassert>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <vector>
#include <iostream>
#define TRACE_EVENT0(...) ((void)0)
#define TRACE_EVENT_INSTANT0(...) ((void)0)
#define TRACE_EVENT_ASYNC_BEGIN0(...) ((void)0)
#define TRACE_EVENT0_WITH_FLOW_IDS(...) ((void)0)
#define TRACE_FLOW_BEGIN(category, name, id) ((void)(category), (void)(name), (void)(id))
#define TRACE_FLOW_END(category, name, id) ((void)(category), (void)(name), (void)(id))
#define FML_TRACE_EVENT_WITH_FLOW_IDS(category, name, ...) ((void)(category), (void)(name))
#define FML_DCHECK(value) assert(value)
namespace fml {
using closure=std::function<void()>;
struct TimePoint { long value; static TimePoint Now(){return{1};} bool operator>=(TimePoint rhs)const{return value>=rhs.value;} };
using TaskQueueId=int;
namespace tracing { inline uint64_t TraceNonce(){return 1;} }
}
struct FrameTimingsRecorder {
 void RecordVsync(fml::TimePoint, fml::TimePoint) {}
};
struct TaskRunner {
 std::deque<fml::closure> tasks;
 void PostTask(fml::closure task){tasks.push_back(std::move(task));}
 int GetTaskQueueId(){return 1;}
 void Drain(){while(!tasks.empty()){auto task=std::move(tasks.front());tasks.pop_front();task();}}
};
struct TaskRunners { TaskRunner* runner; TaskRunner* GetUITaskRunner() const {return runner;} };
static constexpr const char* kVsyncFlowName="VsyncFlow";
static constexpr const char* kVsyncTraceName="VsyncProcessCallback";
class VsyncWaiter {
public:
 using Callback=std::function<void(std::unique_ptr<FrameTimingsRecorder>)>;
 TaskRunners task_runners_;
 std::mutex callback_mutex_;
 Callback callback_;
 std::map<uintptr_t,fml::closure> secondary_callbacks_;
 std::function<void()> native_request;
 void AsyncWaitForVsync(const Callback& callback);
 void FireCallback(fml::TimePoint frame_start_time, fml::TimePoint frame_target_time, bool pause_secondary_tasks=true);
 void AwaitVSync(){native_request();}
 void PauseDartEventLoopTasks(){}
 static void ResumeDartEventLoopTasks(int){}
};
__WAIT_ASYNC__
__FIRE_CALLBACK__
class Semaphore {
 dispatch_semaphore_t value_=dispatch_semaphore_create(1);
public:
 ~Semaphore(){dispatch_semaphore_signal(value_);}
 bool TryWait(){return dispatch_semaphore_wait(value_,DISPATCH_TIME_NOW)==0;}
 void Signal(){dispatch_semaphore_signal(value_);}
};
class Animator {
public:
 TaskRunners task_runners_;
 std::shared_ptr<VsyncWaiter> waiter_;
 Semaphore pending_frame_semaphore_;
 struct WeakHandle { std::shared_ptr<int> flag; Animator* self; explicit operator bool() const{return bool(flag);} Animator* operator->() const{return self;} };
 struct WeakFactory { Animator* self; std::shared_ptr<int> flag=std::make_shared<int>(1); WeakHandle GetWeakPtr(){return{flag,self};} } weak_factory_{this};
 bool regenerate_layer_trees_=false,frame_scheduled_=false,has_rendered_=false;
 int frame_request_number_=0,dart_frame_deadline_=0,frames=0;
 struct Delegate {void OnAnimatorNotifyIdle(int){}} delegate_;
 void RequestFrame(bool regenerate_layer_trees=true);
 void AwaitVSync();
 bool CanReuseLastLayerTrees(){return !regenerate_layer_trees_;}
 // Controlled consuming callback; actual source releases this same semaphore in BeginFrame.
 void BeginFrame(std::unique_ptr<FrameTimingsRecorder>){frames++;frame_scheduled_=false;regenerate_layer_trees_=false;pending_frame_semaphore_.Signal();}
 void DrawLastLayerTrees(std::unique_ptr<FrameTimingsRecorder>){pending_frame_semaphore_.Signal();}
 void EndFrame(){}
};
__REQUEST_FRAME__
__AWAIT_VSYNC__
struct DisplayData {};
struct RuntimeController {void SetDisplays(const std::vector<DisplayData>&){}};
class Engine {
public:
 Animator* animator_;
 RuntimeController* runtime_controller_;
 void ScheduleFrame(bool regenerate_layer_trees=true);
 void SetDisplays(const std::vector<DisplayData>& displays);
};
__ENGINE_SCHEDULE__
__ENGINE_DISPLAYS__
static VsyncWaiter* currentWaiter;
static int requests,deliveries;
static const int64_t kFlutterImplicitViewId=0;
@interface FlutterVSyncWaiter:NSObject
- (void)waitForVSync:(uintptr_t)baton;
@end
@implementation FlutterVSyncWaiter
- (void)waitForVSync:(uintptr_t)baton {
 assert(baton==1);deliveries++;
 currentWaiter->FireCallback({1},{1});
}
@end
@interface FlutterEngine:NSObject {
 @public NSMutableDictionary<NSNumber*,FlutterVSyncWaiter*>* _vsyncWaiters;
}
- (void)onVSync:(uintptr_t)baton;
- (void)installViewWaiter;
@end
@implementation FlutterEngine
- (instancetype)init {self=[super init];if(self){_vsyncWaiters=[NSMutableDictionary dictionary];}return self;}
- (void)installViewWaiter {assert(_vsyncWaiters.count==0);_vsyncWaiters[@(kFlutterImplicitViewId)]=[[FlutterVSyncWaiter alloc]init];}
__NATIVE_VSYNC__
@end
int main(){@autoreleasepool {
 for(bool readyBeforeRun:{false,true}) {
  TaskRunner queue; auto waiter=std::make_shared<VsyncWaiter>();waiter->task_runners_={&queue};currentWaiter=waiter.get();
  FlutterEngine* engine=[[FlutterEngine alloc]init];requests=deliveries=0;
  waiter->native_request=[&]{requests++;[engine onVSync:1];};
  Animator animator;animator.task_runners_={&queue};animator.waiter_=waiter;
  RuntimeController runtime;
  Engine cppEngine{&animator,&runtime};
  if(readyBeforeRun){[engine installViewWaiter];}
  // A permitted UI-thread interleaving during native run before NSWindow attachment.
  cppEngine.SetDisplays({});queue.Drain();
  if(!readyBeforeRun){[engine installViewWaiter];}
  for(int event=0;event<10;event++){animator.RequestFrame();queue.Drain();}
  if(readyBeforeRun){
   assert(requests==11 && deliveries==11 && animator.frames==11 && !waiter->callback_);
   std::cout<<"PASS: waiter-ready-before-run delivers every requested frame, leaving no retained callback.\n";
  }else{
   assert(requests==1 && deliveries==0 && animator.frames==0 && waiter->callback_);
   std::cout<<"PASS: original view-after-run ordering loses first native baton; ten later frame requests cannot recover the retained pending frame.\n";
  }
 }
 std::cout<<"BOUNDARY: seven exact pinned native/engine/waiter/Animator methods with controlled UI scheduling and frame consumer; no live black-window cause claim.\n";
}}
'''

def engine_controls(output, version="3.24.5"):
    profile = SDK_PROFILES[version]
    engine = profile["engine"]
    official = output / "official-engine"
    official.mkdir()
    bindings = {"flutter_version": version, "engine_revision": engine, "files": {}}
    sources = {}
    for relative, digest in profile["native"].items():
        url = ("https://raw.githubusercontent.com/flutter/engine/" + engine +
               "/shell/platform/darwin/macos/framework/" + relative)
        with urlopen(url, timeout=30) as response:
            data = response.read(2 * 1024 * 1024)
        assert P.sha(data) == digest, "Pinned public Flutter engine source drift: " + relative
        (official / Path(relative).name).write_bytes(data)
        sources[relative] = data.decode()
        bindings["files"][relative] = {"url": url, "sha256": digest, "bytes": len(data)}
    version_url = "https://raw.githubusercontent.com/flutter/flutter/" + version + "/bin/internal/engine.version"
    with urlopen(version_url, timeout=30) as response:
        engine_version = response.read(128)
    assert engine_version == (engine + "\n").encode(), "Pinned Flutter version no longer binds the expected engine"
    (official / "engine.version").write_bytes(engine_version)
    bindings["engine_version_url"] = version_url
    source = sources["Source/FlutterEngine.mm"]
    initial = "_active = NO;\n  _visible = NO;"
    assert source.count(initial) == 1
    handlers = [extract_function(source, "- (void)" + name + ":(NSNotification*)notification {")
                for name in ("handleWillBecomeActive", "handleWillResignActive", "handleDidChangeOcclusionState")]
    bindings["extracted_methods"] = {handler.split(":", 1)[0]: P.sha(handler.encode()) for handler in handlers}
    registrar = extract_function(sources["Source/FlutterAppLifecycleDelegate.mm"],
                                 "- (void)addDelegate:(NSObject<FlutterAppLifecycleDelegate>*)delegate {")
    assert "[_delegates addPointer:" in registrar and "handle" not in registrar
    bindings["registrar_add_delegate_sha256"] = P.sha(registrar.encode())
    (output / "engine-source-bindings.json").write_text(json.dumps(bindings, indent=2) + "\n")
    path = output / "actual-engine-lifecycle.mm"
    path.write_text(ENGINE_OBJC.replace("__INITIAL_STATE__", initial).replace("__HANDLERS__", "\n".join(handlers)))
    binary = output / "actual-engine-lifecycle"
    checked(["xcrun", "clang++", "-std=c++17", "-fobjc-arc", "-Wall", "-Wextra", "-Werror",
             "-framework", "Foundation", str(path), "-o", str(binary)], output, "actual-engine-lifecycle-compile")
    text = checked([str(binary)], output, "actual-engine-lifecycle")
    assert "PASS: exact pinned engine initializes visibility false" in text
    assert "PASS: exact pinned handlers synchronize all four" in text
    frame_readiness_controls(output, sources["Source/FlutterEngine.mm"], sources["Source/FlutterViewController.mm"], version)


def frame_readiness_controls(output, native_engine, view_controller, version="3.24.5"):
    profile = SDK_PROFILES[version]
    engine = profile["engine"]
    sources = {}
    bindings = {"flutter_version": version, "engine_revision": engine, "files": {}}
    for relative, digest in profile["frame"].items():
        url = "https://raw.githubusercontent.com/flutter/engine/" + engine + "/" + relative
        with urlopen(url, timeout=30) as response:
            data = response.read(2 * 1024 * 1024)
        assert P.sha(data) == digest, "Pinned Flutter frame source drift: " + relative
        (output / "official-engine" / Path(relative).name).write_bytes(data)
        sources[relative] = data.decode()
        bindings["files"][relative] = {"url": url, "sha256": digest, "bytes": len(data)}
    view_loaded = extract_function(view_controller, "- (void)viewDidLoad {")
    install_waiter = extract_function(native_engine, "- (void)viewControllerViewDidLoad:(FlutterViewController*)viewController {")
    run = extract_function(native_engine, "- (BOOL)runWithEntrypoint:(NSString*)entrypoint {")
    assert "[_engine viewControllerViewDidLoad:self];" in view_loaded
    assert "[_vsyncWaiters setObject:waiter forKey:@(viewController.viewIdentifier)];" in install_waiter
    assert run.index("_embedderAPI.RunInitialized(_engine)") < run.index("[self updateDisplayConfig];")
    assert "pending_frame_semaphore_.Signal();" in extract_function(sources["shell/common/animator.cc"], "void Animator::BeginFrame(")
    methods = {
        "__WAIT_ASYNC__": extract_function(sources["shell/common/vsync_waiter.cc"], "void VsyncWaiter::AsyncWaitForVsync("),
        "__FIRE_CALLBACK__": extract_function(sources["shell/common/vsync_waiter.cc"], "void VsyncWaiter::FireCallback("),
        "__REQUEST_FRAME__": extract_function(sources["shell/common/animator.cc"], "void Animator::RequestFrame("),
        "__AWAIT_VSYNC__": extract_function(sources["shell/common/animator.cc"], "void Animator::AwaitVSync()"),
        "__NATIVE_VSYNC__": extract_function(native_engine, "- (void)onVSync:(uintptr_t)baton {"),
        "__ENGINE_SCHEDULE__": extract_function(sources["shell/common/engine.cc"], "void Engine::ScheduleFrame("),
        "__ENGINE_DISPLAYS__": extract_function(sources["shell/common/engine.cc"], "void Engine::SetDisplays("),
    }
    source = FRAME_OBJC
    for marker, method in methods.items():
        assert source.count(marker) == 1
        source = source.replace(marker, method)
    bindings["extracted_methods"] = {marker: P.sha(method.encode()) for marker, method in methods.items()}
    bindings["readiness_methods"] = {"viewDidLoad": P.sha(view_loaded.encode()), "install_waiter": P.sha(install_waiter.encode()), "run": P.sha(run.encode())}
    (output / "frame-source-bindings.json").write_text(json.dumps(bindings, indent=2) + "\n")
    path = output / "actual-frame-readiness.mm"
    path.write_text(source)
    binary = output / "actual-frame-readiness"
    checked(["xcrun", "clang++", "-std=c++17", "-fobjc-arc", "-Wall", "-Wextra", "-Werror",
             "-framework", "Foundation", str(path), "-o", str(binary)], output, "actual-frame-readiness-compile")
    text = checked([str(binary)], output, "actual-frame-readiness")
    assert "PASS: original view-after-run ordering loses first native baton" in text
    assert "PASS: waiter-ready-before-run delivers every requested frame" in text


def native_controls(output, files, framework):
    compiler = shutil.which("swiftc")
    assert compiler and sys.platform == "darwin", "Native Mac Swift compiler required"
    version = framework_profile(framework.resolve()) if framework else "3.24.5"
    engine_controls(output, version)
    ready_window = P.patch_file("macos/Classes/FlutterWindow.swift", files["macos/Classes/FlutterWindow.swift"]).decode()
    ready_init = extract_function(ready_window, "  init?(id:")
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
        if label == "corrected":
            before_ready_init = ready_init.replace("    _ = flutterViewController.view\n", "", 1)
            before_ready = SWIFT.replace("__INIT__", before_ready_init).replace("__CREATE__", create).replace("__HANDLE__", handle)
            before_ready_path = output / "previous-actual-view-after-run.swift"
            before_ready_path.write_text(before_ready)
            before_ready_binary = output / "previous-actual-view-after-run"
            checked([compiler, "-warnings-as-errors", str(before_ready_path), "-o", str(before_ready_binary)], output, "previous-view-after-run-compile")
            before_ready_text = checked([str(before_ready_binary), "view-after-run"], output, "previous-actual-view-after-run")
            assert "PASS: previous actual constructor can request its initial frame before the native view waiter is ready" in before_ready_text
            assert "PASS: corrected actual constructor loads the public view after plugins and before run" in text
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
        if label == "corrected":
            races = CLOSE_RACE_SWIFT.replace("__FIELDS__", declarations).replace("__CLOSE__", on_close).replace(
                "__FINISH__", finish).replace("__STATE__", state)
            race_path = output / "actual-close-queue-orders.swift"
            race_path.write_text(races)
            race_binary = output / "actual-close-queue-orders"
            checked([compiler, "-warnings-as-errors", str(race_path), "-o", str(race_binary)], output, "actual-close-queue-orders-compile")
            race_text = checked([str(race_binary)], output, "actual-close-queue-orders")
            for order in ("reply-release-first", "timeout-first", "reply-delivered-timeout-before-release"):
                assert "PASS: exact native close queue order " + order in race_text
    if framework:
        framework = framework.resolve()
        assert framework.name == "FlutterMacOS.framework" and framework.is_dir(), "Actual FlutterMacOS framework required"
        assert framework_profile(framework) == version
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
        print("PASS: all four complete Swift plugin sources typecheck against actual Flutter" + version + " framework on arm64 and x86_64")


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
    print("PASS: pinned Mac frame readiness, subwindow startup, bounded close acknowledgement and ownership regression qualification")


if __name__ == "__main__":
    main()
