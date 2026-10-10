#!/usr/bin/env python3
"""Execute the actual pinned rdev poll loop and local Cargo override checks."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
from rust_toolchain import rustc_command

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get("RDREPO", ROOT / "rustdesk"))
spec = importlib.util.spec_from_file_location("mixel_input_patch", ROOT / "scripts/patch-support-input.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def function(source: str) -> str:
    start = source.index("fn loop_poll_x_event(")
    opening = source.index("{", start)
    depth = 1
    for end in range(opening + 1, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[start:end + 1]
    raise RuntimeError("Incomplete pinned rdev event loop")


def reject(operation, expected: str) -> None:
    try:
        operation()
    except RuntimeError as error:
        assert expected in str(error), str(error)
    else:
        raise AssertionError("Conflicting input patch unexpectedly accepted")


HARNESS = r'''
extern crate self as log;
#[macro_export] macro_rules! error {($($arg:tt)*)=>{{let _=format!($($arg)*);ERRORS.fetch_add(1,Ordering::SeqCst);}};}
use std::collections::VecDeque;
use std::mem::zeroed;
use std::sync::{Arc,Mutex};
use std::sync::atomic::{AtomicUsize,Ordering};
use std::time::Duration;
static READS:AtomicUsize=AtomicUsize::new(0);
static ERRORS:AtomicUsize=AtomicUsize::new(0);
static POLLS:AtomicUsize=AtomicUsize::new(0);
static mut IS_GRABBING:bool=true;
#[derive(Copy,Clone,PartialEq,Eq)] struct Token(u32);
const GRAB_RECV:Token=Token(0);
struct Event;
impl Event {fn token(&self)->Token {GRAB_RECV}}
struct Events(Vec<Event>);
impl Events {fn with_capacity(_capacity:usize)->Self {Self(Vec::new())}}
impl<'a> IntoIterator for &'a Events {
 type Item=&'a Event;type IntoIter=std::slice::Iter<'a,Event>;
 fn into_iter(self)->Self::IntoIter {self.0.iter()}
}
struct Poll {steps:VecDeque<Result<(),std::io::ErrorKind>>,stop:bool}
impl Poll {fn poll(&mut self,events:&mut Events,_timeout:Option<Duration>)->std::io::Result<()> {
 POLLS.fetch_add(1,Ordering::SeqCst);events.0.clear();
 if self.stop {unsafe {IS_GRABBING=false;}}
 match self.steps.pop_front().expect("unexpected extra poll") {
  Ok(())=>{events.0.push(Event);Ok(())},Err(e)=>Err(e.into())
 }
}}
mod xlib {pub type XEvent=u64;pub enum Display {}}
fn read_x_event(_event:&mut xlib::XEvent,_display:*mut xlib::Display) {
 READS.fetch_add(1,Ordering::SeqCst);unsafe {IS_GRABBING=false;}
}
__ORIGINAL__
__PATCHED__
fn reset() {
 READS.store(0,Ordering::SeqCst);ERRORS.store(0,Ordering::SeqCst);POLLS.store(0,Ordering::SeqCst);
 unsafe {IS_GRABBING=true;}
}
fn poll(steps:Vec<Result<(),std::io::ErrorKind>>,stop:bool)->Poll {Poll {steps:steps.into(),stop}}
#[test] fn actual_original_eintr_loses_pending_keyboard_capture() {
 reset();original_loop_poll_x_event(Arc::new(Mutex::new(1)),poll(vec![Err(std::io::ErrorKind::Interrupted),Ok(())],false));
 assert_eq!(READS.load(Ordering::SeqCst),0);assert_eq!(ERRORS.load(Ordering::SeqCst),1);assert_eq!(POLLS.load(Ordering::SeqCst),1);
}
#[test] fn actual_patched_repeated_interruptions_keep_same_capture_until_key_event() {
 reset();loop_poll_x_event(Arc::new(Mutex::new(1)),poll(vec![Err(std::io::ErrorKind::Interrupted),Err(std::io::ErrorKind::Interrupted),Ok(())],false));
 assert_eq!(READS.load(Ordering::SeqCst),1);assert_eq!(ERRORS.load(Ordering::SeqCst),0);assert_eq!(POLLS.load(Ordering::SeqCst),3);
}
#[test] fn actual_patched_fatal_errors_still_terminate_and_report() {
 for error in [std::io::ErrorKind::PermissionDenied,std::io::ErrorKind::BrokenPipe,std::io::ErrorKind::Other] {
  reset();loop_poll_x_event(Arc::new(Mutex::new(1)),poll(vec![Err(error)],false));
  assert_eq!(READS.load(Ordering::SeqCst),0);assert_eq!(ERRORS.load(Ordering::SeqCst),1);assert_eq!(POLLS.load(Ordering::SeqCst),1);
 }
}
#[test] fn actual_patched_interrupted_stop_exits_without_extra_poll_or_callback() {
 reset();loop_poll_x_event(Arc::new(Mutex::new(1)),poll(vec![Err(std::io::ErrorKind::Interrupted)],true));
 assert_eq!(READS.load(Ordering::SeqCst),0);assert_eq!(ERRORS.load(Ordering::SeqCst),0);assert_eq!(POLLS.load(Ordering::SeqCst),1);
}
'''


with tempfile.TemporaryDirectory(prefix="mixel-input-tests-") as temporary:
    repo = Path(temporary)
    for relative in ("Cargo.toml", "Cargo.lock"):
        text = subprocess.run(["git", "-C", str(UPSTREAM), "show", "HEAD:" + relative],
                              check=True, capture_output=True, text=True, encoding="utf-8").stdout
        (repo / relative).write_text(text, encoding="utf-8")
    original_lock = (repo / "Cargo.lock").read_text(encoding="utf-8")
    patcher.apply(repo)
    dependency = repo / patcher.DEPENDENCY
    files = [repo / "Cargo.toml", repo / "Cargo.lock", dependency / "src/linux/grab.rs"]
    before = [file.read_bytes() for file in files]
    patcher.apply(repo)
    assert [file.read_bytes() for file in files] == before, "input patch must be byte-identical when repeated"
    lock = (repo / "Cargo.lock").read_text(encoding="utf-8")
    expected_source = f'source = "git+{patcher.UPSTREAM_URL}#{patcher.PIN}"\n'
    assert original_lock.count(expected_source) == 1 and lock == original_lock.replace(expected_source, "", 1)
    manifest = (repo / "Cargo.toml").read_text(encoding="utf-8")
    assert manifest.count(patcher.PATCH_TABLE) == 1
    assert f'rdev = {{ git = "{patcher.UPSTREAM_URL}" }}' in manifest
    original = subprocess.run(["git", "-C", str(dependency), "show", "HEAD:src/linux/grab.rs"],
                              check=True, capture_output=True, text=True, encoding="utf-8").stdout
    actual = files[2].read_text(encoding="utf-8")
    assert actual == patcher.patch_grab(original)
    reject(lambda: patcher.patch_grab(original.replace("Failed to poll event", "changed upstream")), "poll loop changed")
    reject(lambda: patcher.patch_lock(original_lock.replace(patcher.PIN, "0" * 40)), "source revision changed")
    reject(lambda: patcher.patch_manifest(manifest.replace(patcher.DEPENDENCY, "wrong-path")), "Conflicting rdev")
    files[2].write_text(actual.replace("Interrupted => continue", "Interrupted => break"), encoding="utf-8")
    reject(lambda: patcher.apply(repo), "unexpected changes")
    files[2].write_text(actual, encoding="utf-8")
    untracked = dependency / "build.rs"
    untracked.write_text('fn main() { panic!("untracked code must not execute"); }', encoding="utf-8")
    reject(lambda: patcher.apply(repo), "untracked files")
    untracked.unlink()
    print("PASS: pinned local override and lock update are idempotent and reject revision, source and manifest drift")
    # Run the generated production loop unchanged. Poll/Xlib boundaries are
    # deterministic here; native signal/capture integration is a separate test.
    harness = HARNESS.replace("__ORIGINAL__", function(original).replace("fn loop_poll_x_event(", "fn original_loop_poll_x_event(", 1))
    harness = harness.replace("__PATCHED__", function(actual))
    source = repo / "input-tests.rs"
    source.write_text(harness, encoding="utf-8")
    binary = repo / ("input-tests.exe" if os.name == "nt" else "input-tests")
    subprocess.run(rustc_command(["rustc", "--edition=2021", "--deny", "warnings", "--test", str(source), "-o", str(binary)]), check=True)
    subprocess.run([str(binary), "--test-threads=1"], check=True)

print("PASS: actual pinned X11 event loop retries interrupted polling without losing keyboard events or changing fatal/stop handling")
