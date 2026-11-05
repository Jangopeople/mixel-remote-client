#!/usr/bin/env python3
"""Run the production Mac input-source adapter against deterministic OS outcomes.

This checks cold initialization, retained state and failure handling. It does not
post OS input; the signed app must separately pass the real customer session.
"""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('native_patch', ROOT / 'patch-support-receiver-native.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
plan, _ = patch.plan()
row = next(row for row in plan['files'] if row['name'] == 'src/server/input_service.rs')
source = patch.transform(row['beforeSource'].encode(), row).decode()
start = source.index('fn simulate_native_result(event_type: &EventType) -> Result<(), String> {')
body = source.index('{', start)
depth = 1
end = body + 1
while depth:
    depth += (source[end] == '{') - (source[end] == '}')
    end += 1
production = source[start:end]

fixture = r'''
#![allow(static_mut_refs, non_upper_case_globals)]
use std::sync::{Mutex, atomic::{AtomicBool, AtomicUsize, Ordering}};
static VIRTUAL_INPUT_MTX: Mutex<()> = Mutex::new(());
static mut VIRTUAL_INPUT_STATE: Option<VirtualInputState> = None;
static CREATES: AtomicUsize = AtomicUsize::new(0);
static POSTS: AtomicUsize = AtomicUsize::new(0);
static CREATE_FAIL: AtomicBool = AtomicBool::new(false);
static POST_FAIL: AtomicBool = AtomicBool::new(false);
mod rdev { pub const kVK_CapsLock: u32 = 57; }
#[derive(Clone, Copy)] enum RawKey { MacVirtualKeycode(u32) }
#[derive(Clone, Copy)] enum RdevKey { RawKey(RawKey) }
enum EventType { KeyPress(RdevKey), KeyRelease(RdevKey), Other }
struct VirtualInputState { capslock_down: bool }
impl VirtualInputState {
    #[allow(dead_code)] // The pre-fix production adapter never calls its factory.
    fn new() -> Option<Self> {
        CREATES.fetch_add(1, Ordering::SeqCst);
        if CREATE_FAIL.load(Ordering::SeqCst) { None }
        else { Some(Self { capslock_down: false }) }
    }
    fn simulate(&self, _: &EventType) -> Result<(), &'static str> {
        if POST_FAIL.load(Ordering::SeqCst) { Err("dispatch refused") }
        else { POSTS.fetch_add(1, Ordering::SeqCst); Ok(()) }
    }
}
fn reset() {
    unsafe { VIRTUAL_INPUT_STATE = None; }
    CREATES.store(0, Ordering::SeqCst); POSTS.store(0, Ordering::SeqCst);
    CREATE_FAIL.store(false, Ordering::SeqCst); POST_FAIL.store(false, Ordering::SeqCst);
}
fn down() -> EventType { EventType::KeyPress(RdevKey::RawKey(RawKey::MacVirtualKeycode(57))) }
fn up() -> EventType { EventType::KeyRelease(RdevKey::RawKey(RawKey::MacVirtualKeycode(57))) }
#[test] fn cold_first_key_constructs_source_and_dispatches() {
    reset();
    assert_eq!(simulate_native_result(&down()), Ok(()));
    assert_eq!(CREATES.load(Ordering::SeqCst), 1);
    assert_eq!(POSTS.load(Ordering::SeqCst), 1);
    unsafe { assert!(VIRTUAL_INPUT_STATE.as_ref().unwrap().capslock_down); }
}
#[test] fn existing_source_and_held_state_survive_other_input() {
    reset();
    unsafe { VIRTUAL_INPUT_STATE = Some(VirtualInputState { capslock_down: true }); }
    assert_eq!(simulate_native_result(&EventType::Other), Ok(()));
    unsafe { assert!(VIRTUAL_INPUT_STATE.as_ref().unwrap().capslock_down); }
    assert_eq!(simulate_native_result(&up()), Ok(()));
    unsafe { assert!(!VIRTUAL_INPUT_STATE.as_ref().unwrap().capslock_down); }
    assert_eq!(CREATES.load(Ordering::SeqCst), 0);
    assert_eq!(POSTS.load(Ordering::SeqCst), 2);
}
#[test] fn failed_creation_posts_nothing_and_next_key_retries() {
    reset(); CREATE_FAIL.store(true, Ordering::SeqCst);
    assert_eq!(simulate_native_result(&down()), Err("CGEvent input source unavailable".into()));
    assert_eq!(POSTS.load(Ordering::SeqCst), 0);
    unsafe { assert!(VIRTUAL_INPUT_STATE.is_none()); }
    CREATE_FAIL.store(false, Ordering::SeqCst);
    assert_eq!(simulate_native_result(&down()), Ok(()));
    assert_eq!(CREATES.load(Ordering::SeqCst), 2);
    assert_eq!(POSTS.load(Ordering::SeqCst), 1);
}
#[test] fn failed_dispatch_preserves_source_and_does_not_claim_key_state() {
    reset();
    unsafe { VIRTUAL_INPUT_STATE = Some(VirtualInputState { capslock_down: false }); }
    POST_FAIL.store(true, Ordering::SeqCst);
    assert_eq!(simulate_native_result(&down()), Err("dispatch refused".into()));
    unsafe { assert!(!VIRTUAL_INPUT_STATE.as_ref().unwrap().capslock_down); }
    assert_eq!(CREATES.load(Ordering::SeqCst), 0);
    assert_eq!(POSTS.load(Ordering::SeqCst), 0);
}
'''

compiler = shutil.which('rustc')
if not compiler:
    raise SystemExit('rustc is required: production adapter regression was not run')
with tempfile.TemporaryDirectory(prefix='mixel-mac-input-source-') as directory:
    root = Path(directory)
    (root / 'adapter.rs').write_text(fixture + '\n' + production + '\n')
    subprocess.run([compiler, '--edition=2021', '--test', '-Dwarnings', str(root / 'adapter.rs'), '-o', str(root / 'adapter')], check=True)
    subprocess.run([str(root / 'adapter'), '--test-threads=1'], check=True)
