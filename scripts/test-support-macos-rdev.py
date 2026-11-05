#!/usr/bin/env python3
"""Execute pinned Mac rdev ABI, event ownership, panic and source drift controls."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


P = module("patch-support-macos-rdev")
I = module("patch-support-input")

C_TRAMPOLINE = r'''
#include <ApplicationServices/ApplicationServices.h>
#include <stdint.h>
_Static_assert(sizeof(CGEventType) == sizeof(uint32_t), "CGEventType ABI");
CGEventRef mixel_invoke(CGEventTapCallBack callback, uint32_t kind, CGEventRef event) {
    /* The callback never inspects this nonnull opaque cookie, and it is
       never passed to a CoreGraphics API. Only event is a native CG object. */
    unsigned char proxy_cookie = 0;
    return callback((CGEventTapProxy)&proxy_cookie, (CGEventType)kind, event, NULL);
}
CGEventRef mixel_create(uint32_t kind, int64_t keycode) {
    CGEventRef event = CGEventCreate(NULL);
    if (event) {
        CGEventSetType(event, (CGEventType)kind);
        CGEventSetIntegerValueField(event, kCGKeyboardEventKeycode, keycode);
        CGEventSetIntegerValueField(event, kCGEventSourceUserData, 0x5155495353);
        CGEventSetFlags(event, kCGEventFlagMaskControl);
    }
    return event;
}
CFIndex mixel_count(CGEventRef event) { return CFGetRetainCount(event); }
void mixel_release(CGEventRef event) { CFRelease(event); }
'''

# These exports are appended only to the isolated test copy. Production helper
# output contains neither an export nor a test feature or callback installer.
GRAB_EXPORT = r'''
pub unsafe fn mixel_test_set_grab(callback: Box<dyn FnMut(Event) -> Option<Event>>) {
    GLOBAL_CALLBACK = Some(callback);
}
pub unsafe fn mixel_test_grab() -> QCallback { raw_callback }
'''
LISTEN_EXPORT = r'''
pub unsafe fn mixel_test_set_listen(callback: Box<dyn FnMut(Event)>) {
    GLOBAL_CALLBACK = Some(callback);
}
pub unsafe fn mixel_test_listen() -> QCallback { raw_callback }
'''
LIB_EXPORT = r'''
#[cfg(target_os = "macos")]
pub use crate::macos::{mixel_test_grab, mixel_test_set_grab};
#[cfg(target_os = "macos")]
pub use crate::macos::{mixel_test_listen, mixel_test_set_listen};
'''
MOD_EXPORT = r'''
pub use self::grab::{mixel_test_grab, mixel_test_set_grab};
pub use self::listen::{mixel_test_listen, mixel_test_set_listen};
'''

RUST_MAIN = r'''
use core_graphics::sys::CGEventRef;
use foreign_types::ForeignType;
use core_graphics::event::{CGEvent, CGEventFlags, CGEventType, EventField};
use std::sync::{Arc, Mutex};
type Callback = unsafe extern "C" fn(*mut std::ffi::c_void, u32, CGEventRef, *mut std::ffi::c_void) -> CGEventRef;
extern "C" {
    fn mixel_invoke(callback: Callback, kind: u32, event: CGEventRef) -> CGEventRef;
    fn mixel_create(kind: u32, code: i64) -> CGEventRef;
    fn mixel_count(event: CGEventRef) -> isize;
    fn mixel_release(event: CGEventRef);
}
struct DropPanics;
impl Drop for DropPanics { fn drop(&mut self) { panic!("panic payload must never drop across C"); } }
unsafe fn invoke(callback: Callback, kind: u32, event: CGEventRef) {
    let count = mixel_count(event);
    assert_eq!(mixel_invoke(callback, kind, event), event, "callback must return same native event");
    assert_eq!(mixel_count(event), count, "borrowed event must not retain or release");
}
fn main() {
    // No event posting, event-tap installation, permission prompt or UI action.
    std::panic::set_hook(Box::new(|_| {}));
    if std::env::args().any(|arg| arg == "--abort-profile") {
        unsafe {
            rdev::mixel_test_set_grab(Box::new(|_| panic!("release abort-policy witness")));
            let event = mixel_create(11, 50);
            assert!(!event.is_null());
            mixel_invoke(rdev::mixel_test_grab(), 11, event);
        }
        panic!("abort-profile callback unexpectedly recovered");
    }
    let seen = Arc::new(Mutex::new(Vec::new()));
    unsafe {
        let grab: Callback = rdev::mixel_test_grab();
        let listen: Callback = rdev::mixel_test_listen();
        let sink = seen.clone();
        rdev::mixel_test_set_grab(Box::new(move |event| { sink.lock().unwrap().push(event); None }));
        let event = mixel_create(11, 50); // ISO/Swiss physical code; key-up avoids layout IO.
        assert!(!event.is_null());
        invoke(grab, 11, event);
        let facade = std::mem::ManuallyDrop::new(CGEvent::from_ptr(event));
        assert_eq!(facade.get_type() as u32, CGEventType::Null as u32);
        let captured = seen.lock().unwrap();
        assert_eq!(captured.len(), 1);
        assert_eq!(captured[0].platform_code, 50);
        assert_eq!(captured[0].extra_data, 0x5155495353);
        assert_eq!(captured[0].event_type, rdev::EventType::KeyRelease(rdev::key_from_code(50)));
        drop(captured);
        mixel_release(event);
        println!("PASS: C Apple-header grab filter preserves physical code/data, borrowed retain count and same pointer");

        let sink = seen.clone();
        rdev::mixel_test_set_grab(Box::new(move |event| { sink.lock().unwrap().push(event.clone()); Some(event) }));
        let sink = seen.clone();
        rdev::mixel_test_set_listen(Box::new(move |event| { sink.lock().unwrap().push(event); }));
        for callback in [grab, listen] {
            for code in [0, 6, 10, 42, 50, 56, 60, 58, 61, 55, 54, 59, 62, 123, 124] {
                let event = mixel_create(11, code);
                let before = seen.lock().unwrap().len();
                invoke(callback, 11, event);
                assert_eq!(seen.lock().unwrap().len(), before + 1);
                let captured = seen.lock().unwrap().last().unwrap().clone();
                assert_eq!(captured.platform_code as i64, code);
                assert_eq!(captured.event_type, rdev::EventType::KeyRelease(rdev::key_from_code(code as _)));
                assert_eq!(captured.extra_data, 0x5155495353);
                let facade = std::mem::ManuallyDrop::new(CGEvent::from_ptr(event));
                assert_eq!(facade.get_type() as u32, 11);
                assert_eq!(facade.get_integer_value_field(EventField::KEYBOARD_EVENT_KEYCODE), code);
                assert_eq!(facade.get_flags(), CGEventFlags::CGEventFlagControl);
                mixel_release(event);
            }
            let before = seen.lock().unwrap().len();
            for kind in [8, 9, 13, 21, 28, 0xffff_fffd, 0xffff_fffe, 0xffff_ffff] {
                // Real native allocation even for unknown event notifications.
                let event = mixel_create(11, 50);
                invoke(callback, kind, event);
                assert_eq!(seen.lock().unwrap().len(), before);
                mixel_release(event);
                assert!(mixel_invoke(callback, kind, std::ptr::null_mut()).is_null());
            }
            assert!(mixel_invoke(callback, 11, std::ptr::null_mut()).is_null());
            for (kind, code, flags) in [(10, 0, CGEventFlags::CGEventFlagControl),
                                         (12, 56, CGEventFlags::CGEventFlagShift),
                                         (12, 56, CGEventFlags::CGEventFlagNull)] {
                let event = mixel_create(kind, code);
                let facade = std::mem::ManuallyDrop::new(CGEvent::from_ptr(event));
                facade.set_flags(flags);
                let before = seen.lock().unwrap().len();
                invoke(callback, kind, event);
                assert_eq!(seen.lock().unwrap().len(), before + 1);
                let captured = seen.lock().unwrap().last().unwrap().clone();
                let expected = if flags == CGEventFlags::CGEventFlagNull {
                    rdev::EventType::KeyRelease(rdev::key_from_code(code as _))
                } else { rdev::EventType::KeyPress(rdev::key_from_code(code as _)) };
                assert_eq!(captured.event_type, expected);
                assert_eq!(facade.get_flags(), flags);
                assert_eq!(captured.extra_data, 0x5155495353);
                mixel_release(event);
            }
            let event = mixel_create(22, 0);
            let facade = std::mem::ManuallyDrop::new(CGEvent::from_ptr(event));
            facade.set_integer_value_field(EventField::SCROLL_WHEEL_EVENT_POINT_DELTA_AXIS_1, -17);
            facade.set_integer_value_field(EventField::SCROLL_WHEEL_EVENT_POINT_DELTA_AXIS_2, 29);
            invoke(callback, 22, event);
            assert_eq!(seen.lock().unwrap().last().unwrap().event_type,
                       rdev::EventType::Wheel { delta_x: 29, delta_y: -17 });
            mixel_release(event);
        }
        println!("PASS: both actual callbacks preserve key/modifier data and safely pass null, unknown and disabled notifications");

        if std::env::args().any(|arg| arg == "--release-normal") {
            println!("PASS: unchanged panic=abort release profile preserves normal C callback ABI and borrowed event ownership");
            return;
        }

        for payload in [false, true] {
            rdev::mixel_test_set_grab(Box::new(move |_| {
                if payload { std::panic::panic_any(DropPanics); }
                panic!("client callback panic");
            }));
            let event = mixel_create(11, 50);
            invoke(grab, 11, event);
            let facade = std::mem::ManuallyDrop::new(CGEvent::from_ptr(event));
            assert_eq!(facade.get_type() as u32, 11);
            let sink = seen.clone();
            rdev::mixel_test_set_grab(Box::new(move |event| { sink.lock().unwrap().push(event.clone()); Some(event) }));
            let before = seen.lock().unwrap().len();
            invoke(grab, 11, event);
            assert_eq!(seen.lock().unwrap().len(), before + 1, "panic must not poison keyboard mutex");
            mixel_release(event);
        }
        rdev::mixel_test_set_listen(Box::new(|_| panic!("listener panic")));
        let event = mixel_create(11, 50);
        invoke(listen, 11, event);
        let sink = seen.clone();
        rdev::mixel_test_set_listen(Box::new(move |event| { sink.lock().unwrap().push(event); }));
        let before = seen.lock().unwrap().len();
        invoke(listen, 11, event);
        assert_eq!(seen.lock().unwrap().len(), before + 1);
        mixel_release(event);
        println!("PASS: real C invocation contains grab/listen/payload-Drop panics without native ownership changes or poisoned next callback");
    }
}
'''


def run(arguments, *, cwd=None, env=None, expected=0, name=None):
    result = subprocess.run([str(a) for a in arguments], cwd=cwd, env=env, capture_output=True,
                            encoding="utf-8", errors="strict", timeout=600)
    if name:
        (EVIDENCE / (name + ".stdout.log")).write_text(result.stdout, encoding="utf-8")
        (EVIDENCE / (name + ".stderr.log")).write_text(result.stderr, encoding="utf-8")
        (EVIDENCE / (name + ".invocation.json")).write_text(json.dumps({"argv": [str(a) for a in arguments], "cwd": str(cwd), "exit": result.returncode}, indent=2) + "\n", encoding="utf-8")
    if result.returncode != expected:
        raise AssertionError(f"{arguments[0]} exited {result.returncode}:\n{result.stdout[-6000:]}\n{result.stderr[-6000:]}")
    return result


def bytes_snapshot(repo):
    return {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*")
            if p.is_file() and not p.is_symlink() and ".git" not in p.relative_to(repo).parts}


def lock_graph(text):
    graph = {}
    for match in P.entries(text):
        entry = match.group()
        fields = {name: re.search(r'^' + name + r' = "([^"]+)"$', entry, re.M).group(1)
                  for name in ("name", "version")}
        for name in ("source", "checksum"):
            value = re.search(r'^' + name + r' = "([^"]+)"$', entry, re.M)
            fields[name] = value.group(1) if value else None
        fields["dependencies"] = re.findall(r'^ "([^"]+)",$', entry, re.M)
        graph[(fields["name"], fields["version"], fields["source"])] = fields
    return graph


def resolved_edges(record, graph):
    edges = set()
    for edge in record["dependencies"]:
        parts = edge.split(" ", 2)
        found = [key for key in graph if key[0] == parts[0] and
                 (len(parts) == 1 or key[1] == parts[1]) and
                 (len(parts) < 3 or key[2] == parts[2].strip("()"))]
        assert len(found) == 1, "Ambiguous dependency in locked fixture graph: " + edge
        edges.add(found[0])
    return edges


def compare_graph(old_text, new_text):
    old, new = lock_graph(old_text), lock_graph(new_text)
    omitted = []
    for key, record in new.items():
        if key[0] == "mixel-rdev-ffi-harness": continue
        assert key in old and old[key]["checksum"] == record["checksum"], "Cargo changed pinned package identity/checksum"
        old_edges, new_edges = resolved_edges(old[key], old), resolved_edges(record, new)
        assert new_edges <= old_edges, "Cargo added an unpinned dependency edge"
        for missing in old_edges - new_edges:
            omitted.append([key[0], key[1], missing[0], missing[1]])
    # Full-app feature unification also enables these two optional edges.
    # They are not used by standalone rdev. Record their omission explicitly;
    # the production app lock is untouched except for the one new Mac edge.
    assert sorted(omitted) == [["bitflags", "2.9.1", "serde", "1.0.228"],
                               ["objc", "0.2.7", "objc_exception", "0.1.2"]], omitted
    return {"pinned_reachable_packages": len(new) - 1,
            "package_versions_sources_checksums_preserved": True,
            "standalone_optional_feature_edge_omissions": sorted(omitted),
            "production_lock_only_change": "rdev -> existing foreign-types 0.3.2",
            "full_app_feature_equivalence_claimed": False}


def fixture(destination):
    destination.mkdir()
    for relative in ("Cargo.toml", "Cargo.lock"):
        (destination / relative).write_bytes(P.git(UPSTREAM, "show", "HEAD:" + relative))
    dependency = destination / P.DEPENDENCY
    dependency.parent.mkdir()
    # Clone into our own checkout; never write to Cargo's shared Git cache.
    run(["git", "clone", "--quiet", "--no-hardlinks", str(DEPENDENCY), str(dependency)])
    run(["git", "-C", dependency, "remote", "set-url", "origin", P.UPSTREAM])
    I.apply(destination)
    return destination


class SourceControls(unittest.TestCase):
    def test_absent_fresh_dependency_is_read_only_and_missing_owned_dependency_is_fatal(self):
        with tempfile.TemporaryDirectory(prefix="rdev-absent-") as temporary:
            fresh = Path(temporary) / "fresh"
            fresh.mkdir()
            for name in ("Cargo.toml", "Cargo.lock"):
                (fresh / name).write_bytes(P.git(UPSTREAM, "show", "HEAD:" + name))
            before = bytes_snapshot(fresh)
            for check in (False, True):
                P.apply(fresh, restore=True, check=check)
                self.assertEqual(bytes_snapshot(fresh), before)
            for manifest_owned, lock_owned in ((True, True), (True, False), (False, True)):
                with self.subTest(manifest_owned=manifest_owned, lock_owned=lock_owned):
                    (fresh / "Cargo.toml").write_bytes(before["Cargo.toml"] + (I.PATCH_TABLE.encode() if manifest_owned else b""))
                    (fresh / "Cargo.lock").write_bytes(I.patch_lock(before["Cargo.lock"].decode()).encode() if lock_owned else before["Cargo.lock"])
                    missing = bytes_snapshot(fresh)
                    for check in (False, True):
                        with self.assertRaisesRegex(RuntimeError, "Missing owned rdev dependency checkout"):
                            P.apply(fresh, restore=True, check=check)
                        self.assertEqual(bytes_snapshot(fresh), missing)

    def test_full_inventory_idempotence_restore_and_exact_lock_edge(self):
        for newline in ("\n", "\r\n"):
            with tempfile.TemporaryDirectory(prefix="rdev-state-") as temporary:
                repo = fixture(Path(temporary) / "app")
                if newline == "\r\n":
                    for path in repo.rglob("*"):
                        if path.is_file() and ".git" not in path.relative_to(repo).parts:
                            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
                before = bytes_snapshot(repo)
                for restore in (False, True):
                    P.apply(repo, restore=restore, check=True)
                    self.assertEqual(bytes_snapshot(repo), before)
                P.apply(repo)
                after = bytes_snapshot(repo)
                changed = {key for key in before if before[key] != after[key]}
                self.assertEqual(changed, {"Cargo.lock", *(P.DEPENDENCY + "/" + name for name in P.FILES)})
                self.assertEqual(after["Cargo.lock"], before["Cargo.lock"].replace(b' "epoll",' + newline.encode(), b' "epoll",' + newline.encode() + P.EDGE.replace("\n", newline).encode()))
                for restore in (False, True):
                    P.apply(repo, restore=restore, check=True)
                    self.assertEqual(bytes_snapshot(repo), after)
                P.apply(repo)
                self.assertEqual(bytes_snapshot(repo), after)
                P.apply(repo, restore=True)
                self.assertEqual(bytes_snapshot(repo), before)
                # Earlier input patch sees its exact owned baseline again.
                if newline == "\n":
                    I.apply(repo)
                    P.apply(repo)
                    self.assertEqual(bytes_snapshot(repo), after)

    def test_drift_mixed_symlink_untracked_revision_origin_refused_before_writes(self):
        for case in ("source", "unrelated", "lock", "manifest", "mixed", "symlink", "hardlink", "untracked", "staged-build", "origin", "revision", "newline"):
            with self.subTest(case=case), tempfile.TemporaryDirectory(prefix="rdev-refusal-") as temporary:
                repo = fixture(Path(temporary) / "app")
                dependency = repo / P.DEPENDENCY
                target = dependency / "src/macos/grab.rs"
                if case == "source": target.write_bytes(target.read_bytes() + b"// drift\n")
                elif case == "unrelated": (dependency / "src/macos/keyboard.rs").write_text("drift", encoding="utf-8")
                elif case == "lock":
                    lock = (repo / "Cargo.lock").read_text(encoding="utf-8")
                    entry = next(m.group() for m in P.entries(lock) if '\nname = "rdev"\n' in m.group())
                    (repo / "Cargo.lock").write_text(lock.replace(entry, entry.replace(' "dispatch",', ' "different",', 1), 1), encoding="utf-8")
                elif case == "manifest": (repo / "Cargo.toml").write_text("conflicting override", encoding="utf-8")
                elif case == "mixed": target.write_text(P.patch("src/macos/grab.rs", target.read_text(encoding="utf-8")), encoding="utf-8")
                elif case == "symlink": target.unlink(); target.symlink_to(repo / "Cargo.toml")
                elif case == "hardlink": os.link(target, repo.parent / "outside-hardlink.rs")
                elif case == "untracked": (dependency / "build.rs").write_text("fn main() {}", encoding="utf-8")
                elif case == "staged-build":
                    (dependency / "build.rs").write_text("fn main() {}", encoding="utf-8")
                    run(["git", "-C", dependency, "add", "build.rs"])
                elif case == "origin": run(["git", "-C", dependency, "remote", "set-url", "origin", "https://example.invalid/rdev"])
                elif case == "revision": run(["git", "-C", dependency, "update-ref", "HEAD", "0" * 40])
                elif case == "newline": target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n", 1))
                before = bytes_snapshot(repo)
                for restore in (False, True):
                    with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
                        P.apply(repo, restore=restore, check=True)
                    self.assertEqual(bytes_snapshot(repo), before)
                with self.assertRaises((RuntimeError, subprocess.CalledProcessError)): P.apply(repo)
                self.assertEqual(bytes_snapshot(repo), before)

    def test_conversion_masks_keyboard_layout_and_permissions_remain_byte_identical(self):
        dependency = DEPENDENCY
        for name in P.FILES:
            original = P.git(dependency, "show", "HEAD:" + name).decode()
            patched = P.patch(name, original)
            if name.endswith("common.rs"):
                boundary = '#[cfg(target_os = "macos")]\n#[inline]\nfn kb_get_layout_type()'
                self.assertEqual(original[original.index(boundary):], patched[patched.index(boundary):])
                start, end = original.index("pub static mut LAST_FLAGS"), original.index('#[cfg(target_os = "macos")]\n#[link')
                self.assertIn(original[start:end], patched)
            elif name.endswith(("grab.rs", "listen.rs")):
                boundary = "static mut CUR_LOOP" if name.endswith("grab.rs") else "pub fn listen<T>"
                self.assertEqual(original[original.index(boundary):], patched[patched.index(boundary):])
                self.assertNotIn("allow(improper_ctypes", patched)
            else:
                self.assertEqual(patched.replace(P.MAC_DEPENDENCY, ""), original)
        print("PASS: exact conversion, ISO/Swiss mapping, masks, permissions and event-tap/runloop lifecycle preserved")


def native_controls():
    if sys.platform != "darwin": raise RuntimeError("Native ABI/CF ownership controls require macOS")
    repo = fixture(EVIDENCE / "native-app")
    before = bytes_snapshot(repo)
    pinned_dir = EVIDENCE / "pinned-upstream-rdev"
    for row in P.git(repo / P.DEPENDENCY, "ls-tree", "-r", "HEAD").decode().splitlines():
        relative = row.split("\t", 1)[1]
        path = pinned_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(P.git(repo / P.DEPENDENCY, "show", "HEAD:" + relative))
    (EVIDENCE / "pinned-rdev-inventory.txt").write_bytes(P.git(repo / P.DEPENDENCY, "ls-tree", "-r", "HEAD"))
    original_dir = EVIDENCE / "original-owned-input"
    for relative, data in before.items():
        path = original_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    P.apply(repo)
    production = bytes_snapshot(repo)
    (EVIDENCE / "production-bindings.json").write_text(json.dumps({key: hashlib.sha256(value).hexdigest() for key, value in production.items()}, indent=2) + "\n", encoding="utf-8")
    dependency = EVIDENCE / "native-rdev"
    shutil.copytree(repo / P.DEPENDENCY, dependency, ignore=shutil.ignore_patterns(".git"))
    for relative, export in (("src/macos/grab.rs", GRAB_EXPORT), ("src/macos/listen.rs", LISTEN_EXPORT), ("src/lib.rs", LIB_EXPORT), ("src/macos/mod.rs", MOD_EXPORT)):
        path = dependency / relative
        path.write_text(path.read_text(encoding="utf-8") + export, encoding="utf-8")
    harness = EVIDENCE / "native-harness"
    harness.mkdir()
    (harness / "src").mkdir()
    (harness / "src/main.rs").write_text(RUST_MAIN, encoding="utf-8")
    (harness / "Cargo.toml").write_text('[package]\nname="mixel-rdev-ffi-harness"\nversion="0.0.0"\nedition="2021"\n[workspace]\n[dependencies]\nrdev={path="../native-rdev"}\ncore-graphics={version="=0.22.3",features=["highsierra"]}\nforeign-types="=0.3.2"\n', encoding="utf-8")
    app_manifest = (repo / "Cargo.toml").read_text(encoding="utf-8")
    release = app_manifest[app_manifest.index("[profile.release]"):].split("\n[", 1)[0]
    assert re.search(r"^panic = 'abort'$", release, re.M) and "lto = true" in release and "codegen-units = 1" in release
    with (harness / "Cargo.toml").open("a", encoding="utf-8") as stream:
        stream.write('\n[profile.release]\nlto=true\ncodegen-units=1\npanic="abort"\n')
    (EVIDENCE / "production-release-profile.json").write_text(json.dumps({"original_exact_profile": release,
        "source_manifest_sha256": hashlib.sha256((repo / "Cargo.toml").read_bytes()).hexdigest(),
        "production_profile_changed": False, "panic_recovery_control_profile": "unwind only",
        "production_callback_panic_recovery_claimed": False}, indent=2) + "\n", encoding="utf-8")
    (harness / "Cargo.lock").write_bytes((repo / "Cargo.lock").read_bytes())
    (harness / "trampoline.c").write_text(C_TRAMPOLINE, encoding="utf-8")
    sdk = run(["xcrun", "--sdk", "macosx", "--show-sdk-path"]).stdout.strip()
    toolchain = os.environ.get("MIXEL_RDEV_RUST_TOOLCHAIN", "1.81")
    for arch, target in (("arm64", "aarch64-apple-darwin"), ("x86_64", "x86_64-apple-darwin")):
        minimum = "12.3" if arch == "arm64" else "10.14"
        object_file = harness / (arch + ".o")
        run(["xcrun", "clang", "-arch", arch, "-isysroot", sdk, "-mmacosx-version-min=" + minimum, "-std=c11", "-Wall", "-Wextra", "-Werror", "-c", harness / "trampoline.c", "-o", object_file], name=arch + "-c-header-abi")
        env = dict(os.environ, CARGO_TARGET_DIR=str(EVIDENCE / "cargo-target"), RUSTFLAGS=f"-D improper_ctypes_definitions -D improper_ctypes -C link-arg={object_file} -C link-arg=-framework -C link-arg=ApplicationServices", MACOSX_DEPLOYMENT_TARGET=minimum)
        run(["cargo", "+" + toolchain, "build", "--manifest-path", harness / "Cargo.toml", "--target", target], env=env, name=arch + "-full-rdev-build")
        binary = EVIDENCE / "cargo-target" / target / "debug/mixel-rdev-ffi-harness"
        result = run([binary], name=arch + "-real-c-callback")
        self_contained = "event passed through"
        assert result.stderr.count(self_contained) == 3, result.stderr
        print(result.stdout, end="")
        # Actual pinned foreign-types wrapper must fail a by-value C ABI gate.
        deps = EVIDENCE / "cargo-target" / target / "debug/deps"
        core = list(deps.glob("libcore_graphics-*.rlib"))
        assert len(core) == 1
        witness = harness / "original-owned-wrapper.rs"
        witness.write_text('use core_graphics::event::CGEvent;\n#[deny(improper_ctypes_definitions)]\npub unsafe extern "C" fn original(event: CGEvent) -> CGEvent { event }\n', encoding="utf-8")
        refusal = run(["rustc", "+" + toolchain, "--edition=2021", "--crate-type=lib", "--target", target, "--emit=metadata", "--extern", "core_graphics=" + str(core[0]), "-L", "dependency=" + str(deps), witness, "-o", harness / (arch + "-refused.rmeta")], expected=1, name=arch + "-original-owned-abi-refusal")
        assert "not FFI-safe" in refusal.stderr and "CGEvent" in refusal.stderr
        print(f"PASS: {arch} original owned-wrapper C ABI refused; complete corrected pinned dependency and real Apple C callback passed")
        run(["cargo", "+" + toolchain, "build", "--release", "--manifest-path", harness / "Cargo.toml", "--target", target], env=env, name=arch + "-release-abort-rdev-build")
        release_binary = EVIDENCE / "cargo-target" / target / "release/mixel-rdev-ffi-harness"
        normal = run([release_binary, "--release-normal"], name=arch + "-release-normal-c-callback")
        assert "unchanged panic=abort" in normal.stdout
        minimum_output = run(["xcrun", "vtool", "-show-build", release_binary], name=arch + "-release-mach-o-minimum").stdout
        assert "minos " + minimum in minimum_output, minimum_output
        run([release_binary, "--abort-profile"], expected=-6, name=arch + "-release-panic-aborts")
        print(f"PASS: {arch} exact abort-profile normal ABI/ownership passed; deliberate callback panic SIGABRT preserved, recovery not claimed")
    # Cargo may add only the harness package; every pre-existing reachable
    # locked package retains its exact version/source/checksum/dependency set.
    graph = compare_graph((repo / "Cargo.lock").read_text(encoding="utf-8"), (harness / "Cargo.lock").read_text(encoding="utf-8"))
    (EVIDENCE / "standalone-locked-graph-comparison.json").write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
    print("PASS: both native architecture builds preserve pinned package identities/checksums and record standalone optional-feature projection")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--repo", type=Path, default=Path(os.environ.get("RDREPO", ROOT / "rustdesk")))
    parser.add_argument("--dependency", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    UPSTREAM = args.repo
    DEPENDENCY = args.dependency or UPSTREAM / P.DEPENDENCY
    temporary = tempfile.TemporaryDirectory(prefix="mixel-mac-rdev-controls-") if not args.evidence_dir else None
    EVIDENCE = args.evidence_dir.resolve() if args.evidence_dir else Path(temporary.name).resolve()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SourceControls)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful(): sys.exit(1)
    if args.native: native_controls()
    if temporary: temporary.cleanup()
