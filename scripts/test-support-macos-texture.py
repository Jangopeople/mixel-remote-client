#!/usr/bin/env python3
"""Run pinned renderer lifecycle, frame-lock, async Dart and materialization controls."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mixel_texture_patch", ROOT / "scripts/patch-support-macos-texture.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
FILES = ("Cargo.toml", "src/flutter.rs", "src/flutter_ffi.rs", "flutter/lib/web/bridge.dart", "flutter/lib/models/desktop_render_texture.dart", "flutter/pubspec.yaml")


def run(args, output, name):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    (output / name).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"{name} failed ({result.returncode}): {result.stdout}{result.stderr}")
    return result.stdout


def original_source(repo):
    return {name: subprocess.check_output(["git", "-C", str(repo), "show", "HEAD:" + name]) for name in FILES}


def write_repo(repo, sources):
    for name, data in sources.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def snapshot(repo):
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob("*") if p.is_file()}


def materialization_controls(output, originals, files):
    root = output / "materialization"
    root.mkdir()
    write_repo(root, originals)
    with patch.object(P, "fetch_source", return_value=dict(files)) as fetch:
        P.apply(root)
        assert fetch.call_count == 1
        first = snapshot(root)
        P.apply(root)
        assert fetch.call_count == 1 and snapshot(root) == first
    target = root / "flutter" / P.DEPENDENCY
    assert len(P.existing_files(target)) == 98
    checks = 0
    for name in ("src/flutter.rs", "src/flutter_ffi.rs", "flutter/lib/web/bridge.dart", "flutter/lib/models/desktop_render_texture.dart", "flutter/pubspec.yaml"):
        owned = output / f"drift-{checks}"
        write_repo(owned, originals)
        text = (owned / name).read_text()
        if name.endswith("pubspec.yaml"):
            text = text.replace(P.PIN, "0" * 40)
        elif name.endswith("desktop_render_texture.dart"):
            text = text.replace("_textureKey = -1", "_textureKey = -2", 1)
        else:
            text = text.replace("ptr", "different_ptr", 1 if name.endswith("bridge.dart") else 0)
            # Mutate the actual registration block, rather than unrelated source.
            start = "  void sessionRegisterPixelbufferTexture(" if name.endswith("bridge.dart") else ("    fn register_pixelbuffer_texture(" if name.endswith("flutter.rs") else "pub fn session_register_pixelbuffer_texture(")
            a = text.index(start)
            text = text[:a] + text[a:].replace("ptr", "different_ptr", 1)
        (owned / name).write_text(text)
        before = snapshot(owned)
        with patch.object(P, "fetch_source", side_effect=AssertionError("drift fetched")):
            try:
                P.apply(owned)
            except RuntimeError:
                pass
            else:
                raise AssertionError("drift accepted")
        assert snapshot(owned) == before
        checks += 1
    for label, mutation in (
        ("extra-file", lambda d: (d / "unexpected").write_text("unexpected")),
        ("extra-dir", lambda d: (d / "extra").mkdir()),
        ("symlink", lambda d: (d / "linked").symlink_to(d / "pubspec.yaml")),
        ("wrong-content", lambda d: (d / "pubspec.yaml").write_text("wrong")),
        ("frame-drift", lambda d: (d / P.TEXT).write_text((d / P.TEXT).read_text().replace("memcpy(", "changed_copy(", 1))),
        ("old-frame-copy", lambda d: (d / P.TEXT).write_text(P.unpatch_frame((d / P.TEXT).read_text()))),
    ):
        owned = output / label
        shutil.copytree(root, owned)
        mutation(owned / "flutter" / P.DEPENDENCY)
        with patch.object(P, "fetch_source", side_effect=AssertionError("existing content fetched")):
            try:
                P.apply(owned)
            except RuntimeError:
                pass
            else:
                raise AssertionError(label + " accepted")
        checks += 1
    for label in ("destination-symlink", "parent-symlink"):
        owned = output / label
        write_repo(owned, originals)
        parent = owned / "flutter/.mixel-dependencies"
        if label == "parent-symlink":
            parent.symlink_to(target.parent, target_is_directory=True)
        else:
            parent.mkdir()
            (parent / "texture_rgba_renderer").symlink_to(target, target_is_directory=True)
        with patch.object(P, "fetch_source", side_effect=AssertionError("symlink fetched")):
            try:
                P.apply(owned)
            except RuntimeError:
                pass
            else:
                raise AssertionError(label + " accepted")
        checks += 1
    for label in ("callback-drift", "source-symlink", "source-hardlink"):
        owned = output / label
        write_repo(owned, originals)
        source = owned / "src/flutter.rs"
        if label == "callback-drift":
            text = source.read_text()
            a = text.index(P.CALLBACK_START)
            source.write_text(text[:a] + text[a:].replace("write().unwrap()", "read().unwrap()", 1))
        else:
            retained = owned / "retained-source"
            source.rename(retained)
            if label == "source-symlink":
                source.symlink_to(retained)
            else:
                os.link(retained, source)
        before = snapshot(owned)
        with patch.object(P, "fetch_source", side_effect=AssertionError("unsafe source fetched")):
            try:
                P.apply(owned)
            except RuntimeError:
                pass
            else:
                raise AssertionError(label + " accepted")
        assert snapshot(owned) == before
        checks += 1
    owned = output / "fetch-failure"
    write_repo(owned, originals)
    with patch.object(P, "fetch_source", side_effect=RuntimeError("actual bounded failure")):
        try:
            P.apply(owned)
        except RuntimeError:
            pass
        else:
            raise AssertionError("fetch failure accepted")
    assert snapshot(owned) == originals and not (owned / "flutter/.mixel-dependencies").exists()
    with patch.object(P.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "git")) as call, patch.object(P.time, "sleep"):
        try:
            P.fetch_source()
        except RuntimeError:
            pass
        else:
            raise AssertionError("failed network silently accepted")
        assert call.call_count == 3
    print(f"PASS: pinned98 source inventory/owned materialization/idempotence; {checks + 2} drift/path/fetch refusal controls before source writes")
    return root


SWIFT_STUBS = r'''
import Foundation
import CoreVideo
public protocol FlutterTexture { func copyPixelBuffer() -> Unmanaged<CVPixelBuffer>? }
public protocol FlutterTextureRegistry {
    func register(_ texture: FlutterTexture) -> Int64
    func unregisterTexture(_ id: Int64)
    func textureFrameAvailable(_ id: Int64)
}
public protocol FlutterPlugin {}
public typealias FlutterResult = (Any?) -> Void
public class FlutterMethodCall {
    let method: String; let arguments: Any?
    init(_ method: String, _ arguments: Any?) { self.method = method; self.arguments = arguments }
}
public class FlutterStandardTypedData { let data: Data; init(_ data: Data) { self.data = data } }
public class FlutterMethodChannel { init(name: String, binaryMessenger: Any) {} }
public class FlutterPluginRegistrar {
    public var messenger: Any { return 0 }
    public var textures: FlutterTextureRegistry { fatalError("not used") }
    public func addMethodCallDelegate(_ instance: FlutterPlugin, channel: FlutterMethodChannel) {}
}
let FlutterMethodNotImplemented = "unused"
final class WeakTexture { weak var value: TextRgba?; init(_ value: TextRgba) { self.value = value } }
final class Registry: FlutterTextureRegistry {
    var refs: [WeakTexture] = []; var unregistered: [Int64] = []; var frames = 0; var failRegister = false
    func register(_ texture: FlutterTexture) -> Int64 {
        refs.append(WeakTexture(texture as! TextRgba)); return failRegister ? 0 : Int64(refs.count)
    }
    func unregisterTexture(_ id: Int64) {
        precondition(refs[Int(id) - 1].value != nil, "local close owner must survive unregister")
        unregistered.append(id)
    }
    func textureFrameAvailable(_ id: Int64) { frames += 1 }
}
func call(_ plugin: TextureRgbaRendererPlugin, _ method: String, _ key: Int64) -> Any? {
    var value: Any?; plugin.handle(FlutterMethodCall(method, ["key": key])) { value = $0 }; return value
}
func exercise(_ plugin: TextureRgbaRendererPlugin, _ key: Int64) {
    precondition((call(plugin, "createTexture", key) as? Int64) != -1)
    let ptr = call(plugin, "getTexturePtr", key) as! UInt
    let texture = Unmanaged<TextRgba>.fromOpaque(UnsafeRawPointer(bitPattern: ptr)!).takeUnretainedValue()
    let bytes = [UInt8](repeating: 17, count: 64 * 16)
    let accepted = bytes.withUnsafeBufferPointer {
        texture.markFrameAvaliableRaw(buffer: $0.baseAddress!, len: $0.count, width: 16, height: 16, stride_align: 64)
    }
    precondition(accepted, "real CoreVideo frame must be accepted")
    precondition((call(plugin, "closeTexture", key) as? Bool) == true)
}
func verifyRows(_ texture: TextRgba, _ width: Int, _ height: Int, _ alignment: Int) -> Bool {
    let stride = (width * 4 + alignment - 1) & ~(alignment - 1)
    var bytes = [UInt8](repeating: 0, count: stride * height)
    for row in 0..<height { for column in 0..<(width * 4) { bytes[row * stride + column] = UInt8((row * 29 + column) % 251) } }
    let accepted = bytes.withUnsafeBufferPointer { texture.markFrameAvaliableRaw(buffer: $0.baseAddress!, len: $0.count, width: width, height: height, stride_align: alignment) }
    precondition(accepted)
    let pixel = texture.copyPixelBuffer()!.takeRetainedValue()
    precondition(CVPixelBufferLockBaseAddress(pixel, []) == kCVReturnSuccess)
    defer { CVPixelBufferUnlockBaseAddress(pixel, []) }
    let destination = CVPixelBufferGetBaseAddress(pixel)!.assumingMemoryBound(to: UInt8.self)
    let destinationStride = CVPixelBufferGetBytesPerRow(pixel)
    for row in 0..<height { for column in 0..<(width * 4) {
        if destination[row * destinationStride + column] != bytes[row * stride + column] { return false }
    } }
    for row in 0..<height { for column in (width * 4)..<destinationStride {
        if destination[row * destinationStride + column] != 0 { return false }
    } }
    return true
}
'''


def swift_controls(output, files):
    if sys.platform != "darwin":
        print("SOURCE SCOPE: actual CoreVideo controls require macOS; portable source/Rust/Dart controls continue")
        return
    text = files["macos/Classes/TextRgba.swift"].decode()
    original = files[P.PLUGIN].decode()
    assert text.count("import FlutterMacOS\n") == original.count("import FlutterMacOS\n") == 1
    old_tail = r'''
let registry = Registry(); var plugin: TextureRgbaRendererPlugin? = TextureRgbaRendererPlugin()
plugin!.setTextureRegistry(registry: registry)
for key in Int64(1)...Int64(3) { exercise(plugin!, key) }
precondition(registry.refs.filter { $0.value != nil }.count == 3)
var buffers = 0
for ref in registry.refs { if ref.value?.copyPixelBuffer()?.takeRetainedValue() != nil { buffers += 1 } }
precondition(buffers == 3 && registry.frames == 3 && registry.unregistered.count == 3)
for key in Int64(1)...Int64(3) { precondition((call(plugin!, "getTexturePtr", key) as? UInt) != 0) }
registry.failRegister = true
precondition((call(plugin!, "createTexture", 9) as? Int64) == 0)
precondition((call(plugin!, "getTexturePtr", 9) as? UInt) != 0)
plugin = nil; precondition(registry.refs.allSatisfy { $0.value == nil })
print("PASS: original pinned close retains3 objects and3 real CoreVideo buffers until plugin teardown")
print("PASS: original pinned registration failure0 still publishes a native pointer")
let rowTexture = TextRgba.new(registry: Registry())
precondition(!verifyRows(rowTexture, 17, 3, 1))
precondition(verifyRows(rowTexture, 17, 3, 64))
print("PASS: exact original packed align1 multi-display rows are corrupted; align64 control preserves every pixel")
'''
    new_tail = r'''
let registry = Registry(); let plugin = TextureRgbaRendererPlugin(); plugin.setTextureRegistry(registry: registry)
for key in Int64(1)...Int64(3) { exercise(plugin, key) }
precondition(registry.refs.allSatisfy { $0.value == nil })
precondition(registry.frames == 3 && registry.unregistered.count == 3)
for key in Int64(1)...Int64(3) {
    precondition((call(plugin, "getTexturePtr", key) as? Int) == 0)
    precondition((call(plugin, "closeTexture", key) as? Bool) == false)
}
exercise(plugin, 1)
precondition(registry.refs.allSatisfy { $0.value == nil } && registry.unregistered.count == 4)
precondition((call(plugin, "closeTexture", 999) as? Bool) == false)
registry.failRegister = true
precondition((call(plugin, "createTexture", 5) as? Int64) == 0)
precondition((call(plugin, "closeTexture", 5) as? Bool) == false)
precondition(registry.refs.allSatisfy { $0.value == nil } && registry.unregistered.count == 4)
precondition((call(plugin, "getTexturePtr", 5) as? Int) == 0)
let noRegistry = TextureRgbaRendererPlugin()
precondition((call(noRegistry, "createTexture", 6) as? Int64) == -1)
precondition((call(noRegistry, "closeTexture", 6) as? Bool) == false)
precondition((call(noRegistry, "getTexturePtr", 6) as? Int) == 0)
print("PASS: corrected pinned CoreVideo lifetime: distinct keys release immediately; closed pointers absent; repeat close, same-key recreate, unknown and failed registry safe; strong local owner survives unregister")
let rowTexture = TextRgba.new(registry: Registry())
var rows = 0
for width in [1,2,15,16,17,31,32,63,64,65,127,128,129,1919,1920,1921,3840] {
    for height in [1,2,7] { for alignment in [1,64] {
        precondition(verifyRows(rowTexture, width, height, alignment)); rows += 1
    } }
}
precondition(!rowTexture.markFrameAvaliable(data: Data(), width: 1, height: 1, stride_align: 1))
precondition(verifyRows(rowTexture, 17, 3, 1))
print("PASS: corrected real CoreVideo \(rows) packed/padded row cases preserve every visible pixel and zero destination padding; empty Data refused then valid frame recovers")
'''
    for name, source, tail in (("original", original, old_tail), ("fixed", P.patch_plugin(original), new_tail)):
        frame_source = P.patch_frame(text) if name == "fixed" else text
        prefix = SWIFT_STUBS + frame_source.replace("import FlutterMacOS\n", "", 1)
        fixture = output / (name + "-texture-lifetime.swift")
        fixture.write_text(prefix + source.replace("import FlutterMacOS\n", "", 1) + tail)
        for architecture, target, launcher in (("arm64", "arm64-apple-macosx12.3", []),
                                                ("x86_64", "x86_64-apple-macosx10.14", ["arch", "-x86_64"])):
            binary = output / (name + "-texture-lifetime-" + architecture)
            run(["swiftc", "-O", "-target", target, str(fixture), "-o", str(binary)], output, name + "-" + architecture + "-swift-compile.log")
            print(run([*launcher, str(binary)], output, name + "-" + architecture + "-swift-output.log").strip())
    swift_failure_controls(output, text, original)


CV_FAILURE_PROVIDER = r'''
enum Fault: CaseIterable { case none, createError, createNil, lockError, baseNil, shortRow, shortCapacity, strideOverflow, unlockError }
var fault = Fault.none
var allocations = 0
func testedCreate(_ allocator: CFAllocator?, _ width: Int, _ height: Int, _ format: OSType, _ attributes: CFDictionary?, _ output: UnsafeMutablePointer<CVPixelBuffer?>) -> CVReturn {
    allocations += 1
    if fault == .createError { output.pointee = nil; return kCVReturnError }
    if fault == .createNil { output.pointee = nil; return kCVReturnSuccess }
    return CVPixelBufferCreate(allocator, width, height, format, attributes, output)
}
func testedLock(_ buffer: CVPixelBuffer, _ flags: CVPixelBufferLockFlags) -> CVReturn {
    if fault == .lockError { return kCVReturnError }
    return CVPixelBufferLockBaseAddress(buffer, flags)
}
func testedUnlock(_ buffer: CVPixelBuffer, _ flags: CVPixelBufferLockFlags) -> CVReturn {
    let result = CVPixelBufferUnlockBaseAddress(buffer, flags)
    return fault == .unlockError ? kCVReturnError : result
}
func testedBase(_ buffer: CVPixelBuffer) -> UnsafeMutableRawPointer? { return fault == .baseNil ? nil : CVPixelBufferGetBaseAddress(buffer) }
func testedRow(_ buffer: CVPixelBuffer) -> Int { return fault == .shortRow ? 1 : (fault == .strideOverflow ? Int.max : CVPixelBufferGetBytesPerRow(buffer)) }
func testedCapacity(_ buffer: CVPixelBuffer) -> Int { return fault == .shortCapacity ? 0 : CVPixelBufferGetDataSize(buffer) }
'''
CV_FAILURE_TEST = r'''
let registry=Registry();let texture=TextRgba.new(registry:registry)
precondition(verifyRows(texture,17,3,1))
let bytes=[UInt8](repeating:41,count:204)
for failure in Fault.allCases where failure != .none {
    let previous=texture.copyPixelBuffer()!.takeRetainedValue();let frames=registry.frames
    fault=failure
    let accepted=bytes.withUnsafeBufferPointer { texture.markFrameAvaliableRaw(buffer:$0.baseAddress!,len:$0.count,width:17,height:3,stride_align:1) }
    precondition(!accepted && registry.frames==frames)
    precondition(previous === texture.copyPixelBuffer()!.takeRetainedValue())
    fault = .none;precondition(verifyRows(texture,17,3,1))
}
let shapes:[(Int,Int,Int,Int)]=[(0,3,1,204),(-1,3,1,204),(17,0,1,204),(17,-1,1,204),(17,3,0,204),(17,3,-1,204),(17,3,3,204),(Int.max,3,1,204),(Int.max/4,1,8,204),(1,1,Int.max,204),(17,Int.max,1,204),(17,3,1,203),(17,3,1,205),(17,3,1,-1),(1,1,1<<62,4)]
for (width,height,alignment,length) in shapes {
    let before=allocations;let previous=texture.copyPixelBuffer()!.takeRetainedValue();let frames=registry.frames
    let accepted=bytes.withUnsafeBufferPointer { texture.markFrameAvaliableRaw(buffer:$0.baseAddress!,len:length,width:width,height:height,stride_align:alignment) }
    precondition(!accepted && allocations==before && registry.frames==frames)
    precondition(previous === texture.copyPixelBuffer()!.takeRetainedValue())
}
precondition(!texture.markFrameAvaliable(data:Data(),width:1,height:1,stride_align:1))
precondition(verifyRows(texture,17,3,1))
print("PASS: actual fixed frame body: eight controlled API failure cases and15 malformed/overflow input cases preserve prior frame/no notification; empty Data refused; every valid retry succeeds (API failures injected, not observed host OS failures)")
'''


def swift_failure_controls(output, text, plugin):
    frame = P.patch_frame(text)
    for original, controlled in (("CVPixelBufferCreate", "testedCreate"),
                                  ("CVPixelBufferLockBaseAddress", "testedLock"),
                                  ("CVPixelBufferUnlockBaseAddress", "testedUnlock"),
                                  ("CVPixelBufferGetBaseAddress", "testedBase"),
                                  ("CVPixelBufferGetBytesPerRow", "testedRow"),
                                  ("CVPixelBufferGetDataSize", "testedCapacity")):
        frame = frame.replace(original, controlled)
    fixture = output / "actual-frame-failure-controls.swift"
    fixture.write_text(SWIFT_STUBS + CV_FAILURE_PROVIDER + frame.replace("import FlutterMacOS\n", "", 1)
                       + P.patch_plugin(plugin).replace("import FlutterMacOS\n", "", 1) + CV_FAILURE_TEST)
    for architecture, target, launcher in (("arm64", "arm64-apple-macosx12.3", []),
                                          ("x86_64", "x86_64-apple-macosx10.14", ["arch", "-x86_64"])):
        binary = output / ("actual-frame-failures-" + architecture)
        run(["swiftc", "-O", "-target", target, str(fixture), "-o", str(binary)], output, architecture + "-frame-failures-compile.log")
        print(run([*launcher, str(binary)], output, architecture + "-frame-failures-output.log").strip())


RUST_PREFIX = r'''
extern crate self as log;
#[macro_export] macro_rules! error { ($($x:tt)*) => {{}} }
use std::{collections::HashMap,sync::{Arc,RwLock,Mutex,Condvar,atomic::{AtomicUsize,Ordering}},thread,time::Duration};
type TextureRgbaPtr = usize;
#[derive(PartialEq)] enum RenderType { PixelBuffer }
struct DisplaySessionInfo { texture_rgba_ptr: usize, size: (usize,usize), notify_render_type: Option<RenderType>, #[cfg(feature="vram")] gpu_output_ptr: usize }
mod scrap { pub struct ImageRgb { pub raw: Vec<u8>, pub w: usize, pub h: usize } impl ImageRgb { pub fn align(&self)->usize {64} } }
struct VideoRenderer { map_display_sessions: RwLock<HashMap<usize,DisplaySessionInfo>>, is_support_multi_ui_session: bool, on_rgba_func: Option<unsafe extern "C" fn(usize,*const u8,i32,i32,i32,i32)> }
static CALLS: AtomicUsize = AtomicUsize::new(0);
static GATE: (Mutex<(bool,bool)>,Condvar) = (Mutex::new((false,false)),Condvar::new());
unsafe extern "C" fn frame(_:usize,_:*const u8,_:i32,_:i32,_:i32,_:i32) {
 CALLS.fetch_add(1,Ordering::SeqCst); let mut g=GATE.0.lock().unwrap();g.0=true;GATE.1.notify_all();while !g.1 {g=GATE.1.wait(g).unwrap();}
}
fn info(ptr:usize)->DisplaySessionInfo { DisplaySessionInfo { texture_rgba_ptr:ptr,size:(1,1),notify_render_type:None,#[cfg(feature="vram")] gpu_output_ptr:0 } }
'''
RUST_TEST = r'''
fn main() {
 let renderer=Arc::new(VideoRenderer {map_display_sessions:RwLock::new(HashMap::from([(0,info(111))])),is_support_multi_ui_session:true,on_rgba_func:Some(frame)});
 assert!(!renderer.unregister_pixelbuffer_texture_if_current(0,0));
 assert!(!renderer.unregister_pixelbuffer_texture_if_current(9,111));
 assert!(!renderer.unregister_pixelbuffer_texture_if_current(0,222));
 assert_eq!(renderer.map_display_sessions.read().unwrap()[&0].texture_rgba_ptr,111);
 let r=renderer.clone();let callback=thread::spawn(move||r.on_rgba(0,&scrap::ImageRgb{raw:vec![0;64],w:1,h:1}));
 {let mut g=GATE.0.lock().unwrap();while !g.0 {g=GATE.1.wait(g).unwrap();}}
 let (tx,rx)=std::sync::mpsc::channel();let r=renderer.clone();let removal=thread::spawn(move||tx.send(r.unregister_pixelbuffer_texture_if_current(0,111)).unwrap());
 assert!(matches!(rx.recv_timeout(Duration::from_millis(100)),Err(std::sync::mpsc::RecvTimeoutError::Timeout)));
 {let mut g=GATE.0.lock().unwrap();g.1=true;GATE.1.notify_all();}
 assert!(callback.join().unwrap());assert!(rx.recv_timeout(Duration::from_secs(2)).unwrap());removal.join().unwrap();
 assert!(!renderer.on_rgba(0,&scrap::ImageRgb{raw:vec![0;64],w:1,h:1}));assert_eq!(CALLS.load(Ordering::SeqCst),1);
 assert!(!renderer.unregister_pixelbuffer_texture_if_current(0,111));
 renderer.map_display_sessions.write().unwrap().insert(0,info(222));
 assert!(!renderer.unregister_pixelbuffer_texture_if_current(0,111));assert_eq!(renderer.map_display_sessions.read().unwrap()[&0].texture_rgba_ptr,222);
 assert!(renderer.unregister_pixelbuffer_texture_if_current(0,222));
 #[cfg(feature="vram")] {let mut x=info(333);x.gpu_output_ptr=444;renderer.map_display_sessions.write().unwrap().insert(0,x);assert!(renderer.unregister_pixelbuffer_texture_if_current(0,333));let r=renderer.map_display_sessions.read().unwrap();assert_eq!(r[&0].texture_rgba_ptr,0);assert_eq!(r[&0].gpu_output_ptr,444);}
 println!("PASS: actual source frame callback lock excludes retirement; empty/zero/wrong-pointer/repeat/handoff replacement and GPU-preservation controls");
}
'''


def rust_controls(output, generated):
    text = (generated / "src/flutter.rs").read_text()
    start = text.index(P.CALLBACK_START)
    end = text.index(P.CALLBACK_END, start)
    callback = text[start:end]
    method = P.METHOD.replace('    #[cfg(target_os = "macos")]\n', "")
    fixture = output / "actual-frame-lock.rs"
    fixture.write_text(RUST_PREFIX + "impl VideoRenderer {" + method + callback + "}\n" + RUST_TEST)
    rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
    if not rustc:
        raise RuntimeError("Rust compiler required for actual frame-lock controls")
    for mode, flags in (("normal", []), ("vram", ["--cfg", 'feature="vram"'])):
        binary = output / ("actual-frame-lock-" + mode + (".exe" if os.name == "nt" else ""))
        run([rustc, "--edition=2021", *flags, str(fixture), "-o", str(binary)], output, mode + "-rust-compile.log")
        print(run([str(binary)], output, mode + "-rust-output.log").strip())


DART_STUBS = r'''
import 'dart:async';
bool isMacOS = true;
void debugPrint(String s) {}
class SessionID { final String value; SessionID(this.value); }
class TextureModel { final List<int> ids=[];void setRgbaTextureId({required int display,required int id}) {ids.add(id);} }
class FFI { final id='test'; final textureModel=TextureModel(); }
class Binding {
 int key=0;int? native;int retire=0;int registrations=0;bool failRetire=false;
 int getNextTextureKey()=>++key;
 bool sessionUnregisterPixelbufferTextureIfCurrent({required SessionID sessionId,required int display,required int ptr}) {
  retire++;if(failRetire)throw StateError('unknown retirement');if(ptr==0||native!=ptr)return false;native=null;return true;
 }
}
final bind=Binding();
class Platform {
 void registerPixelbufferTexture(SessionID s,int d,int ptr) {bind.registrations++;bind.native=ptr==0?null:ptr;}
}
final platformFFI=Platform();
class TextureRgbaRenderer {
 final Map<int,int> pointers={}; final List<int> closed=[];
 Completer<int>? createPending;Completer<int>? ptrPending;Completer<bool>? closePending;
 int? failedId;int? fixedPtr;
 Future<int> createTexture(int key) async {
  if(createPending!=null) {await createPending!.future;}
  pointers[key]=fixedPtr??key+100;return failedId??key;
 }
 Future<int> getTexturePtr(int key) async {if(ptrPending!=null)return ptrPending!.future;return pointers[key]??0;}
 Future<bool> closeTexture(int key) async {closed.add(key);if(closePending!=null)await closePending!.future;return pointers.remove(key)!=null;}
}
void check(bool value,String message) {if(!value)throw StateError(message);}
Future<void> flush() async {for(var i=0;i<8;i++){await Future<void>.delayed(Duration.zero);}}
'''
DART_TEST = r'''
Future<void> main() async {
 final ffi=FFI();final session=SessionID('same-session');
 var texture=_PixelbufferTexture();texture.create(0,session,ffi);await flush();check(bind.native!=null,'created pointer registered');
 await texture.destroy(false,ffi);check(bind.native==null&&texture.textureRenderer.pointers.isEmpty,'migration retires original');
 final retire=bind.retire;await texture.destroy(false,ffi);check(bind.retire==retire,'repeat close no second retirement');
 texture=_PixelbufferTexture();texture.create(0,session,ffi);await flush();bind.native=999;
 await texture.destroy(false,ffi);check(bind.native==999,'same-session newer pointer survives');
 texture=_PixelbufferTexture();texture.textureRenderer.createPending=Completer<int>();texture.create(0,session,ffi);final lateKey=texture._textureKey;
 await texture.destroy(false,ffi);final registrations=bind.registrations;texture.textureRenderer.createPending!.complete(1);await flush();
 check(!texture.textureRenderer.pointers.containsKey(lateKey)&&bind.registrations==registrations,'late create closes exact old generation without registration');
 texture=_PixelbufferTexture();texture.textureRenderer.ptrPending=Completer<int>();texture.create(0,session,ffi);await flush();
 await texture.destroy(false,ffi);final before=bind.registrations;texture.textureRenderer.ptrPending!.complete(777);await flush();
 check(bind.registrations==before&&texture.textureRenderer.pointers.isEmpty,'late getPtr cannot re-register');
 texture=_PixelbufferTexture();texture.create(0,session,ffi);await flush();final first=texture._textureKey;
 texture.textureRenderer.closePending=Completer<bool>();final closing=texture.destroy(false,ffi);texture.create(0,session,ffi);await flush();final second=texture._textureKey;
 check(second!=first&&bind.native==second+100,'new generation registers independently');
 texture.textureRenderer.closePending!.complete(true);await closing;await flush();
 check(texture._textureKey==second&&bind.native==second+100,'old finally preserves new generation');texture.textureRenderer.closePending=null;await texture.destroy(false,ffi);
 check(bind.native==null&&texture.textureRenderer.pointers.isEmpty,'new generation can close');
 for(final failedId in [0,-1]) {
  texture=_PixelbufferTexture();texture.textureRenderer.failedId=failedId;final failed=bind.registrations;final published=ffi.textureModel.ids.length;texture.create(0,session,ffi);await flush();
  check(bind.registrations==failed&&ffi.textureModel.ids.length==published&&texture.textureRenderer.pointers.isEmpty,'failed registry must not publish UI or native texture');
  await texture.destroy(false,ffi);check(texture.textureRenderer.pointers.isEmpty,'failed registry disposal safe');
 }
 texture=_PixelbufferTexture();texture.create(0,session,ffi);await flush();final failedKey=texture._textureKey;final retained=bind.native;bind.failRetire=true;
 var refused=false;try{await texture.destroy(false,ffi);}on StateError{refused=true;}
 check(refused&&texture.textureRenderer.pointers.containsKey(failedKey)&&bind.native==retained,'unknown retirement cannot free native pointer');
 bind.failRetire=false;await texture.destroy(false,ffi);check(bind.native==null&&texture.textureRenderer.pointers.isEmpty,'known retry retires safely');
 texture=_PixelbufferTexture();texture.textureRenderer.fixedPtr=321;texture.create(0,session,ffi);await flush();await texture.destroy(false,ffi);
 final reused=_PixelbufferTexture();reused.textureRenderer.fixedPtr=321;reused.create(0,session,ffi);await flush();final reusedRetire=bind.retire;
 await texture.destroy(false,ffi);check(bind.native==321&&bind.retire==reusedRetire,'old repeated dispose cannot retire reused address');await reused.destroy(false,ffi);
 isMacOS=false;texture=_PixelbufferTexture();texture.create(0,session,ffi);await flush();final nonMac=bind.native;final nonMacRetire=bind.retire;
 await texture.destroy(false,ffi);check(bind.native==nonMac&&bind.retire==nonMacRetire,'non-Mac migration behavior preserved');
 texture=_PixelbufferTexture();texture.create(0,session,ffi);await flush();await texture.destroy(true,ffi);check(bind.native==null&&bind.retire==nonMacRetire,'non-Mac ordinary zero-pointer retirement preserved');
 print('PASS: actual generated Dart class: migration, matching/newer pointers, repeated disposal, late create/getPtr, same-session new generation, failed registry and unchanged non-Mac paths');
}
'''


def dart_controls(output, generated, originals):
    text = (generated / "flutter/lib/models/desktop_render_texture.dart").read_text()
    a = text.index("class _PixelbufferTexture {")
    b = text.index("\nclass _GpuTexture {", a)
    fixture = output / "actual-pixelbuffer-lifecycle.dart"
    fixture.write_text(DART_STUBS + text[a:b] + DART_TEST)
    dart = os.environ.get("DART_BIN") or shutil.which("dart")
    if not dart:
        raise RuntimeError("Dart SDK required for async texture controls")
    print(run([dart, "analyze", str(fixture)], output, "dart-analyze.log").strip())
    print(run([dart, str(fixture)], output, "dart-lifecycle-output.log").strip())
    original = originals["flutter/lib/models/desktop_render_texture.dart"].decode()
    a = original.index("class _PixelbufferTexture {")
    b = original.index("\nclass _GpuTexture {", a)
    negative = output / "original-zero-registration.dart"
    negative.write_text(DART_STUBS + original[a:b] + r'''
Future<void> main() async {
 final ffi=FFI();final texture=_PixelbufferTexture();texture.textureRenderer.failedId=0;
 texture.create(0,SessionID('failed'),ffi);await flush();
 check(ffi.textureModel.ids.contains(0)&&bind.registrations==1&&bind.native!=null,'original exact0 failure publishes UI and registers pointer');
 print('PASS: exact original Dart registration failure0 publishes UI and native pointer; regression reproduced');
}
''')
    print(run([dart, str(negative)], output, "original-dart-zero-output.log").strip())


def workflow_controls(output, originals, files):
    workflow = (ROOT / ".github/workflows/build.yml").read_text()
    hook = "          RDREPO=. python3 ../scripts/patch-support-macos-texture.py\n"
    assert workflow.count(hook) == 1
    committed = subprocess.check_output(["git", "-C", str(ROOT), "show", "HEAD:.github/workflows/build.yml"]).decode()
    assert workflow.replace(hook, "", 1) == committed.replace(hook, "", 1), "No other build job/guard may change for this integration"
    match = re.search(r"      - name: Generate bridge\n        working-directory: rustdesk\n        run: \|\n((?:          .*\n)+)", workflow)
    assert match
    script = textwrap.dedent(match[1])
    assert script.index("patch-support-macos-texture.py") < script.index("flutter pub get") < script.index("flutter_rust_bridge_codegen")
    owner = output / "actual bridge étape"
    repo = owner / "rustdesk"
    write_repo(repo, originals)
    (repo / "flutter/macos/Runner").mkdir(parents=True)
    (repo / "flutter/ios/Runner").mkdir(parents=True)
    (owner / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/patch-support-macos-texture.py", owner / "scripts/patch-support-macos-texture.py")
    # Deterministically replay the real bounded98-member source archive in the
    # actual helper child; Flutter/compiler stand-ins assert the input ordering.
    archive = owner / "pinned-renderer.tar"
    with tarfile.open(archive, "w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    binary = owner / "bin"
    binary.mkdir()
    python = binary / "python3"
    python.write_text('#!' + sys.executable + '\n' + r'''
import importlib.util,os
from pathlib import Path
s=importlib.util.spec_from_file_location('actual_patch','../scripts/patch-support-macos-texture.py')
p=importlib.util.module_from_spec(s);s.loader.exec_module(p)
p.fetch_source=lambda:p.source_files(Path('../pinned-renderer.tar').read_bytes())
p.apply(Path(os.environ['RDREPO']))
''')
    flutter = binary / "flutter"
    flutter.write_text('#!' + sys.executable + '\n' + r'''
from pathlib import Path
assert 'session_unregister_pixelbuffer_texture_if_current' in Path('../src/flutter_ffi.rs').read_text()
assert 'path: .mixel-dependencies/texture_rgba_renderer' in Path('pubspec.yaml').read_text()
print('PASS: actual bridge step patched source before Flutter resolution')
''')
    codegen = binary / "flutter_rust_bridge_codegen"
    codegen.write_text('#!' + sys.executable + '\n' + r'''
from pathlib import Path
assert 'session_unregister_pixelbuffer_texture_if_current' in Path('src/flutter_ffi.rs').read_text()
assert 'bool sessionUnregisterPixelbufferTextureIfCurrent(' in Path('flutter/lib/web/bridge.dart').read_text()
Path('flutter/macos/Runner/bridge_generated.h').write_text('ordered bridge fixture')
print('PASS: actual bridge step reaches codegen with new retirement API')
''')
    for executable in (python, flutter, codegen):
        executable.chmod(0o755)
    script = script.replace("~/.cargo/bin/flutter_rust_bridge_codegen", shlex.quote(codegen.as_posix()))
    bash = shutil.which("bash")
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.is_file():
            bash = str(candidate)
    assert bash
    environment = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"])
    result = subprocess.run([bash, "-e", "-o", "pipefail", "-c", script], cwd=repo, env=environment, capture_output=True, text=True, timeout=120)
    (output / "actual-bridge-step-output.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    # Removing the exact hook makes the same downstream dependency/codegen
    # checks fail on pristine input; a static name/count alone cannot pass.
    negative = owner / "without-hook/rustdesk"
    write_repo(negative, originals)
    result = subprocess.run([bash, "-e", "-o", "pipefail", "-c", script.replace("RDREPO=. python3 ../scripts/patch-support-macos-texture.py\n", "", 1)], cwd=negative, env=environment, capture_output=True, text=True, timeout=30)
    (output / "bridge-without-hook-negative.log").write_text(result.stdout + result.stderr)
    assert result.returncode != 0 and "AssertionError" in result.stderr
    (output / "workflow-source-proof.json").write_text(json.dumps({"result":"passed", "workflow_sha256":P.sha(workflow.encode()), "head_workflow_sha256":P.sha(committed.encode()), "only_change":"single existing bridge-step pre-generation hook", "all_other_build_jobs_and_guards_byte_identical":True, "actual_source_step_executed":True, "compiler_and_Flutter_tools":"ordering stand-ins; actual pinned bridge generation is a separate required build check"}, indent=2) + "\n")
    print("PASS: actual bridge-step hook precedes dependency/codegen input; removing hook rejects pristine input; all other build jobs/guards unchanged")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--native-only", action="store_true", help="Mac source job without Dart; async Dart controls run in complete source jobs")
    args = parser.parse_args()
    repo = Path(os.environ.get("RDREPO", ROOT / "rustdesk")).resolve()
    originals = original_source(repo)
    target = repo / "flutter" / P.DEPENDENCY
    if target.exists():
        files = P.existing_files(target)
        files[P.PLUGIN] = P.unpatch_plugin(files[P.PLUGIN].decode()).encode()
        files[P.TEXT] = P.unpatch_frame(files[P.TEXT].decode()).encode()
    else:
        files = P.fetch_source()
    with tempfile.TemporaryDirectory(prefix="mixel-mac-texture-controls-") as temporary:
        output = args.output.resolve() if args.output else Path(temporary)
        if args.output:
            output.mkdir(parents=True, exist_ok=False)
        generated = materialization_controls(output, originals, files)
        workflow_controls(output, originals, files)
        swift_controls(output, files)
        rust_controls(output, generated)
        if args.native_only:
            assert sys.platform == "darwin", "native-only mode requires actual macOS"
            print("SOURCE SCOPE: async Dart controls are required in complete source jobs; this standalone Mac job has native-only scope")
        else:
            dart_controls(output, generated, originals)
        (output / "source-proof.json").write_text(json.dumps({"result": "passed", "dependency_pin": P.PIN, "dependency_tree": P.TREE, "original_inventory_sha256": P.INVENTORY_SHA, "macOS_CoreVideo_executed": sys.platform == "darwin", "dart_async_executed": not args.native_only, "source_sha256": {name: hashlib.sha256((generated / name).read_bytes()).hexdigest() for name in FILES}, "scope": "source-extracted lifecycle/frame-lock/owned dependency" + (" and async Dart" if not args.native_only else "; async Dart skipped in native-only mode") + " controls; no application, UI, TCC, native authorization or remote session qualification"}, indent=2) + "\n")
        print("PASS: pinned Mac closed-texture correction " + ("native-only" if args.native_only else "source/native lifecycle and async handoff") + " controls; existing frame callback ABI unchanged")


if __name__ == "__main__":
    main()
