#!/usr/bin/env python3
"""Execute pinned rdev input loops, Android converters and guarded overrides."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from rust_toolchain import rustc_command

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get("RDREPO", ROOT / "rustdesk"))
spec = importlib.util.spec_from_file_location("mixel_input_patch", ROOT / "scripts/patch-support-input.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--evidence-dir", type=Path)
args = parser.parse_args()
RUSTC = os.environ.get("RUSTC_BIN", "rustc")
if args.evidence_dir:
    args.evidence_dir.mkdir(parents=True, exist_ok=True)


def block(source: str, marker: str) -> str:
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 1
    for end in range(opening + 1, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[start:end + 1]
    raise RuntimeError("Incomplete pinned Rust block: " + marker)


def git_source(dependency: Path, relative: str) -> str:
    return subprocess.check_output(["git", "-C", str(dependency), "show", "HEAD:" + relative]).decode("utf-8")


def snapshot(repo: Path) -> dict[str, bytes]:
    return {str(path.relative_to(repo)): path.read_bytes() for path in repo.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(repo).parts}


def guarded_refusal(repo: Path, operation, expected: str) -> None:
    before = snapshot(repo)
    reject(operation, expected)
    assert snapshot(repo) == before, "Refused input overlay changed another file"


def android_converters(repo: Path, dependency: Path) -> None:
    """Run the real table/macro/converters; only the Mac layout query is mocked."""
    folder = repo / "android-converters"
    tables = folder / "keycodes"
    tables.mkdir(parents=True)
    old = git_source(dependency, patcher.ANDROID_PATH)
    new = patcher.patch_android(old)
    for name in ("windows", "linux", "macos", "macos_virtual_keycodes", "usb_hid"):
        (tables / (name + ".rs")).write_text(git_source(dependency, "src/keycodes/" + name + ".rs"), encoding="utf-8")
    (tables / "original_android.rs").write_text(old, encoding="utf-8")
    (tables / "corrected_android.rs").write_text(new, encoding="utf-8")
    names = ("windows", "linux", "macos_virtual_keycodes", "macos", "usb_hid", "original_android", "corrected_android")
    declarations = "\n".join(("#[cfg(not(corrected_clean))]\n" if name == "original_android" else "") + "pub mod " + name + ";" for name in names)
    declarations += "\n#[cfg(corrected_clean)] pub use corrected_android as original_android;\n"
    (tables / "mod.rs").write_text(declarations, encoding="utf-8")
    enum_source = git_source(dependency, "src/rdev.rs")
    enums = "\n".join("#[derive(Debug, Copy, Clone, PartialEq, Eq)]\n" + block(enum_source, "pub enum " + name + " {") for name in ("Key", "RawKey"))
    units = re.findall(r"^    ([A-Za-z0-9_]+),$", block(enum_source, "pub enum Key {"), flags=re.M)
    assert len(units) > 100 and "BackQuote" in units and "Quote" in units
    converters = git_source(dependency, "src/codes_conv.rs")
    macro = block(converters, "macro_rules! conv_keycodes")
    actual_map = block(git_source(dependency, "src/macos/common.rs"), "pub fn map_keycode(")
    mac_wrapper = block(converters, "fn macos_keycode_from_code_(")
    functions = ("win_scancode_to_android_key_code", "linux_code_to_android_key_code",
                 "macos_code_to_android_key_code", "usb_hid_code_to_android_key_code")
    calls = []
    for name in functions:
        match = re.findall(r"conv_keycodes!\(\s*" + name + r",\s*[^;]*?\);", converters)
        assert len(match) == 1
        calls.append(match[0])
    imports = """
use crate::{Key, KeyCode, map_keycode};
use crate::keycodes::windows::key_from_scancode as win_key_from_scancode;
use crate::keycodes::linux::key_from_code as linux_key_from_code;
use crate::keycodes::macos::key_from_code as macos_key_from_code;
use crate::keycodes::usb_hid::key_from_code as usb_hid_key_from_code;
"""
    graph = ""
    for state in ("original", "corrected"):
        graph += "pub mod " + state + " {\n" + imports
        graph += "use crate::keycodes::" + state + "_android::code_from_key as android_code_from_key;\n"
        graph += macro + "\n" + mac_wrapper + "\n" + "\n".join(calls) + "\n}\n"
    layout = """
extern crate self as core_graphics;
pub mod event { pub type CGKeyCode = u16; }
#[cfg(target_os="macos")] pub type KeyCode = u16;
#[cfg(not(target_os="macos"))] pub type KeyCode = u32;
pub mod rdev { pub type KeyCode = crate::KeyCode; __ENUMS__ }
pub use rdev::Key;
pub mod keycodes;
use keycodes::macos_virtual_keycodes::*;
use std::sync::atomic::{AtomicBool, Ordering};
static ISO: AtomicBool = AtomicBool::new(false);
// Mock only the public native layout-query boundary. The production layout
// map and every Android converter below are extracted without body changes.
const ISO_LAYOUT: u32 = 1230196512;
#[allow(non_upper_case_globals)] const kKeyboardISO: u32 = ISO_LAYOUT;
fn kb_get_layout_type() -> u32 { if ISO.load(Ordering::SeqCst) { ISO_LAYOUT } else { 0 } }
#[allow(non_upper_case_globals)]
__MAP__
__GRAPH__
fn main() {
    if cfg!(corrected_clean) {
        assert_eq!(keycodes::corrected_android::code_from_key(Key::BackQuote), Some(68));
        assert_eq!(keycodes::corrected_android::code_from_key(Key::Quote), Some(75));
        for iso in [false, true] {
            ISO.store(iso, Ordering::SeqCst);
            __CLEAN_CASES__
        }
        println!("PASS: corrected actual four converters compile with warnings denied and produce distinct Android grave68/apostrophe75 in ANSI/ISO layout-query controls");
        return;
    }
    if std::env::args().nth(1).as_deref()==Some("original-official") {
        assert_eq!(keycodes::original_android::code_from_key(Key::BackQuote), Some(68), "Android KEYCODE_GRAVE is 68");
        return;
    }
    assert_eq!(keycodes::original_android::code_from_key(Key::BackQuote), Some(75));
    assert_eq!(keycodes::original_android::code_from_key(Key::Quote), Some(75));
    assert_eq!(keycodes::original_android::key_from_code(75), Key::BackQuote);
    assert_eq!(keycodes::original_android::key_from_code(68), Key::Unknown(68));
    assert_eq!(keycodes::corrected_android::code_from_key(Key::BackQuote), Some(68));
    assert_eq!(keycodes::corrected_android::code_from_key(Key::Quote), Some(75));
    assert_eq!(keycodes::corrected_android::key_from_code(68), Key::BackQuote);
    assert_eq!(keycodes::corrected_android::key_from_code(75), Key::Quote);
    for key in [__KEYS__, Key::Unknown(u32::MAX), Key::RawKey(rdev::RawKey::ScanCode(0))] {
        if key != Key::BackQuote {
            assert_eq!(keycodes::original_android::code_from_key(key), keycodes::corrected_android::code_from_key(key), "changed other key: {:?}", key);
        }
    }
    for code in 0..=65535 {
        let old = keycodes::original_android::key_from_code(code);
        let new = keycodes::corrected_android::key_from_code(code);
        if code != 68 && code != 75 { assert_eq!(old, new); }
        // The upstream numeric round-trip test passes despite its collision;
        // the official semantic assertions above expose the lost grave key.
        assert_eq!(keycodes::original_android::code_from_key(old), Some(code));
        assert_eq!(keycodes::corrected_android::code_from_key(new), Some(code));
    }
    assert_eq!(keycodes::corrected_android::key_from_code(u32::MAX), Key::Unknown(u32::MAX));
    for iso in [false, true] {
        ISO.store(iso, Ordering::SeqCst);
        __CASES__
    }
    println!("PASS: actual Android table and Windows/Linux/Mac/USB converters; official grave68/apostrophe75; all other enum mappings and 65536 codes conserved, both Mac ANSI/ISO layout-query controls");
}
"""
    cases = ""
    clean_cases = ""
    for name, decoder, grave, quote in (
            (functions[0], "keycodes::windows::key_from_scancode", "0x29", "0x28"),
            (functions[1], "keycodes::linux::key_from_code", "49", "48"),
            (functions[2], "|code| keycodes::macos::key_from_code(map_keycode(code))", "if iso { 10 } else { 50 }", "39"),
            (functions[3], "keycodes::usb_hid::key_from_code", "0x35", "0x34")):
        direct = f"assert_eq!(corrected::{name}({grave}), Some(68));\nassert_eq!(corrected::{name}({quote}), Some(75));\n"
        cases += direct
        clean_cases += direct
        cases += f"for code in (0..=65535).chain([65536, u32::MAX]) {{\nlet key=({decoder})(code as _);\n"
        cases += f"let old=original::{name}(code); let new=corrected::{name}(code);\n"
        cases += "if key==Key::BackQuote { assert_eq!(old,Some(75)); assert_eq!(new,Some(68)); } else { assert_eq!(old,new); }\n}\n"
    harness = layout.replace("__ENUMS__", enums).replace("__MAP__", actual_map).replace("__GRAPH__", graph)
    harness = harness.replace("__KEYS__", ", ".join("Key::" + name for name in units)).replace("__CASES__", cases).replace("__CLEAN_CASES__", clean_cases)
    source = folder / "android-converters.rs"
    source.write_text(harness, encoding="utf-8")
    binary = folder / ("android-converters.exe" if os.name == "nt" else "android-converters")
    command = [RUSTC, "--edition=2021", str(source), "-o", str(binary)]
    denied = subprocess.run(command + ["--deny", "warnings"], capture_output=True, text=True, encoding="utf-8")
    assert denied.returncode != 0 and "error: unreachable pattern" in denied.stderr and "original_android.rs" in denied.stderr
    (folder / "original-warnings-denied.stderr").write_text(denied.stderr, encoding="utf-8")
    # The original table's duplicate 75 is an explicit negative control, not
    # suppressed. Its diagnostic compile keeps and binds that exact warning;
    # the corrected-only graph must compile clean with all warnings denied.
    compiled = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    assert compiled.stderr.count("warning: unreachable pattern") == 1 and "warning: 1 warning emitted" in compiled.stderr
    (folder / "original-diagnostic-compile.stderr").write_text(compiled.stderr, encoding="utf-8")
    original = subprocess.run([str(binary), "original-official"], capture_output=True, text=True, encoding="utf-8")
    assert original.returncode != 0 and "Android KEYCODE_GRAVE is 68" in original.stderr and "75" in original.stderr
    (folder / "original-official.stderr").write_text(original.stderr, encoding="utf-8")
    corrected = subprocess.run([str(binary)], check=True, capture_output=True, text=True, encoding="utf-8")
    (folder / "corrected.stdout").write_text(corrected.stdout, encoding="utf-8")
    clean_binary = folder / ("corrected-clean.exe" if os.name == "nt" else "corrected-clean")
    clean = subprocess.run([RUSTC, "--edition=2021", "--deny", "warnings", "--cfg", "corrected_clean", str(source), "-o", str(clean_binary)], check=True, capture_output=True, text=True, encoding="utf-8")
    assert clean.stdout == clean.stderr == ""
    clean_run = subprocess.run([str(clean_binary)], check=True, capture_output=True, text=True, encoding="utf-8")
    (folder / "corrected-clean.stdout").write_text(clean_run.stdout, encoding="utf-8")
    (folder / "source-bindings.json").write_text(json.dumps({
        "dependency_revision": patcher.PIN, "original_android_sha256": hashlib.sha256(old.encode()).hexdigest(),
        "corrected_android_sha256": hashlib.sha256(new.encode()).hexdigest(),
        "original_official_assertion_exit": original.returncode, "corrected_exit": corrected.returncode,
        "original_duplicate_table_warnings_denied_exit": denied.returncode,
        "corrected_warnings_denied_exit": clean.returncode, "corrected_clean_run_exit": clean_run.returncode,
        "native_layout_query_mocked": True, "native_capture_or_layout_runtime_claimed": False,
        "extracted_converter_names": functions, "unit_enum_mappings": len(units),
        "official_grave": 68, "official_apostrophe": 75,
        "source_sha256": {name: hashlib.sha256(git_source(dependency, name).encode()).hexdigest() for name in
                          ("src/codes_conv.rs", "src/rdev.rs", "src/macos/common.rs")}
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(corrected.stdout.strip())
    if args.evidence_dir:
        shutil.copytree(folder, args.evidence_dir / "android-converters")


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



EVENT_STATE_HARNESS = r'''
#![allow(non_snake_case)]
use std::{sync::{Mutex,mpsc::{channel,Sender}}, time::SystemTime};
#[derive(Debug,Copy,Clone,PartialEq,Eq)] enum Key {A,At,Colon,Plus,Shift,Control,Numpad,AltGr,Dead,Delete,Unknown}
#[derive(Debug,Copy,Clone,PartialEq,Eq)] enum EventType {KeyPress(Key),KeyRelease(Key),Other}
#[derive(Debug,Clone,PartialEq,Eq)] struct UnicodeInfo {name:Option<String>,is_dead:bool,unicode:Vec<u16>}
#[derive(Debug,Clone)] struct Event {event_type:EventType,time:SystemTime,unicode:Option<UnicodeInfo>,platform_code:u32,position_code:u32,usb_hid:u32}
enum GrabEvent {KeyEvent(Event)}
const KEYPRESS_EVENT:i32=2;
static GRAB_KEY_EVENT_SENDER:Mutex<Option<Sender<GrabEvent>>>=Mutex::new(None);
static mut KEYBOARD:Option<Keyboard>=None;
static CURRENT:Mutex<u32>=Mutex::new(0);
static DECODED:Mutex<Vec<(u32,u32)>>=Mutex::new(Vec::new());
static mut CURRENT_QUERIES:u32=0;
fn key_from_code(code:u32)->Key {match code {38=>Key::A,11=>Key::At,47=>Key::Colon,21=>Key::Plus,50=>Key::Shift,37=>Key::Control,90=>Key::Numpad,108=>Key::AltGr,49=>Key::Dead,119=>Key::Delete,_=>Key::Unknown}}
fn code_from_key(key:Key)->Option<u32> {match key {Key::A=>Some(38),Key::At=>Some(11),Key::Colon=>Some(47),Key::Plus=>Some(21),Key::Shift=>Some(50),Key::Control=>Some(37),Key::Numpad=>Some(90),Key::AltGr=>Some(108),Key::Dead=>Some(49),Key::Delete=>Some(119),Key::Unknown=>None}}
#[derive(Default)] struct Keyboard {keysym:u32}
trait KeyboardState {fn add(&mut self,event:&EventType)->Option<UnicodeInfo>;}
impl Keyboard {
 unsafe fn get_current_modifiers(&mut self)->Option<u32> {CURRENT_QUERIES+=1;Some(*CURRENT.lock().unwrap())}
 unsafe fn unicode_from_code(&mut self,code:u32,state:u32)->Option<UnicodeInfo> {
  DECODED.lock().unwrap().push((code,state));self.keysym=code+state;
  let shift=state&1!=0;let caps=state&2!=0;let altgr=state&128!=0;let num=state&16!=0;
  let value=match code {38=>if shift^caps {"A"} else {"a"},11=>if altgr {"€"} else if shift {"@"} else {"2"},47=>if shift {":"} else {";"},21=>if shift {"+"} else {"="},90=>if num {"0"} else {"insert"},49=>return Some(UnicodeInfo{name:None,is_dead:true,unicode:vec![]}),119=>"\u{7f}",_=>return None};
  Some(UnicodeInfo{name:Some(value.to_owned()),is_dead:false,unicode:vec![]})
 }
 fn keysym(&self)->u32 {self.keysym}
}
mod xlib {
 use std::sync::Mutex;
 pub enum Display {}
 #[derive(Copy,Clone)]#[repr(C)]pub struct XKeyEvent {pub type_:i32,pub keycode:u32,pub state:u32}
 #[derive(Copy,Clone)]#[repr(C)]pub union XEvent {pub key:XKeyEvent,pub type_:i32}
 pub static QUEUE:Mutex<Vec<XKeyEvent>>=Mutex::new(Vec::new());
 pub unsafe fn XPending(_: *mut Display)->i32 {QUEUE.lock().unwrap().len() as i32}
 pub unsafe fn XNextEvent(_: *mut Display,event: &mut XEvent)->i32 {let key=QUEUE.lock().unwrap().remove(0);*event=XEvent{key};0}
}
__ADD__
__ISCONTROL__
__CONVERT__
__READ__
fn queued(codes:&[u32],state:u32,current:u32,press:bool)->Vec<Event> {
 unsafe{KEYBOARD=Some(Keyboard::default());}*CURRENT.lock().unwrap()=current;
 let (tx,rx)=channel();*GRAB_KEY_EVENT_SENDER.lock().unwrap()=Some(tx);
 *xlib::QUEUE.lock().unwrap()=codes.iter().map(|code|xlib::XKeyEvent{type_:if press {2} else {3},keycode:*code,state}).collect();
 let mut event=xlib::XEvent{type_:0};read_x_event(&mut event,std::ptr::null_mut());
 *GRAB_KEY_EVENT_SENDER.lock().unwrap()=None;
 rx.try_iter().map(|e|{let GrabEvent::KeyEvent(event)=e;event}).collect()
}
fn text(events:Vec<Event>)->String {events.into_iter().filter_map(|e|e.unicode.and_then(|u|u.name)).collect()}
fn main() {
 let sample=queued(&[38],0,0,true).remove(0);
 assert_eq!(sample.event_type,EventType::KeyPress(Key::A));assert!(sample.time<=SystemTime::now());assert_eq!(sample.platform_code,38);assert_eq!(sample.position_code,38);assert_eq!(sample.usb_hid,0);
 let shifted=text(queued(&[38,11,47,21],1,0,true));
 #[cfg(original)] {assert_eq!(shifted,"a2;=");println!("ORIGINAL GAP: historical Shift=1/current=0 yielded a2;= instead of A@:+");}
 #[cfg(corrected)] {
  assert_eq!(shifted,"A@:+");assert_eq!(text(queued(&[38,11,47,21],0,1,true)),"a2;=");
  for state in 0..=255 {
   DECODED.lock().unwrap().clear();let events=queued(&[38,11,47,21,90,49],state,state^255,true);
   assert_eq!(*DECODED.lock().unwrap(),vec![(38,state&0xFFFB),(11,state&0xFFFB),(47,state&0xFFFB),(21,state&0xFFFB),(90,state&0xFFFB),(49,state&0xFFFB)]);
   assert_eq!(events.len(),6);assert_eq!(events.iter().map(|e|e.position_code).collect::<Vec<_>>(),vec![38,11,47,21,90,49]);
   assert_eq!(events[0].event_type,EventType::KeyPress(Key::A));assert_eq!(events[0].usb_hid,0);assert!(events[0].time<=SystemTime::now());
   assert_eq!(events[0].platform_code,38+(state&0xFFFB));assert!(events[5].unicode.as_ref().unwrap().is_dead);
  }
  // Both the character press and later Shift release are already queued;
  // the server's current mask has advanced to zero before the grab loop runs.
  unsafe{KEYBOARD=Some(Keyboard::default());}*CURRENT.lock().unwrap()=0;
  let (tx,rx)=channel();*GRAB_KEY_EVENT_SENDER.lock().unwrap()=Some(tx);
  *xlib::QUEUE.lock().unwrap()=vec![xlib::XKeyEvent{type_:2,keycode:38,state:1},xlib::XKeyEvent{type_:3,keycode:50,state:1}];
  let mut x=xlib::XEvent{type_:0};read_x_event(&mut x,std::ptr::null_mut());*GRAB_KEY_EVENT_SENDER.lock().unwrap()=None;
  let events:Vec<Event>=rx.try_iter().map(|g|{let GrabEvent::KeyEvent(e)=g;e}).collect();
  assert_eq!(events[0].unicode.as_ref().unwrap().name.as_deref(),Some("A"));assert_eq!(events[1].event_type,EventType::KeyRelease(Key::Shift));assert!(events[1].unicode.is_none());
  for state in [256,512,1024,2048,4096,65535,65536,u32::MAX] {
   DECODED.lock().unwrap().clear();queued(&[38],state,0,true);assert_eq!(*DECODED.lock().unwrap(),vec![(38,state&0xFFFB)]);
  }
  // A non-key event remains the original no-Unicode release classification.
  unsafe{KEYBOARD=Some(Keyboard::default());}DECODED.lock().unwrap().clear();
  let (tx,rx)=channel();*GRAB_KEY_EVENT_SENDER.lock().unwrap()=Some(tx);
  *xlib::QUEUE.lock().unwrap()=vec![xlib::XKeyEvent{type_:99,keycode:999,state:u32::MAX}];
  read_x_event(&mut x,std::ptr::null_mut());*GRAB_KEY_EVENT_SENDER.lock().unwrap()=None;
  let GrabEvent::KeyEvent(e)=rx.try_recv().unwrap();assert_eq!(e.event_type,EventType::KeyRelease(Key::Unknown));assert!(e.unicode.is_none());assert!(DECODED.lock().unwrap().is_empty());
  assert_eq!(text(queued(&[38],2,0,true)),"A");assert_eq!(text(queued(&[38],3,0,true)),"a");
  assert_eq!(text(queued(&[11],128,0,true)),"€");assert_eq!(text(queued(&[90],16,0,true)),"0");
  DECODED.lock().unwrap().clear();let released=queued(&[38,50,37,108],1,255,false);assert!(released.iter().all(|e|e.unicode.is_none()));assert_eq!(released[0].event_type,EventType::KeyRelease(Key::A));assert!(DECODED.lock().unwrap().is_empty());
  assert!(queued(&[999],1,0,true)[0].unicode.is_none());assert!(queued(&[119],1,0,true)[0].unicode.is_none());
  unsafe{KEYBOARD=None;assert!(convert_event(38,true,1).unicode.is_none());assert_eq!(convert_event(38,true,1).platform_code,0);}
  assert_eq!(unsafe{CURRENT_QUERIES},0);println!("CORRECTED PASS: exact queued functions decode A@:+;256 state masks/Control/Caps/AltGr/NumLock/dead/release/unknown/no keyboard controls; no current-state query");
 }
 // Exercise unchanged other-event/release KeyboardState callers.
 let mut k=Keyboard::default();assert!(k.add(&EventType::Other).is_none());assert!(k.add(&EventType::KeyRelease(Key::A)).is_none());
}
'''


def queued_modifier_controls(repo: Path, dependency: Path) -> None:
    original = git_source(dependency, "src/linux/grab.rs")
    corrected = patcher.patch_grab(original)
    keyboard = git_source(dependency, "src/linux/keyboard.rs")
    lint_help = subprocess.run([RUSTC, "-W", "help"], check=True, capture_output=True,
                               text=True, encoding="utf-8", timeout=30)
    has_static_mut_lint = re.search(r"(?m)^\s+static-mut-refs\s+", lint_help.stdout) is not None
    # The literal inherited static access is exercised only by this sequential,
    # single-threaded source fixture. Keep that code unchanged; allow its known
    # lint on this one function when the compiler recognizes it, never in product
    # source or as a crate/global warning policy.
    fixture_attribute = "#[allow(static_mut_refs)]\n" if has_static_mut_lint else ""
    for mode, source in (("original", original), ("corrected", corrected)):
        harness = EVENT_STATE_HARNESS.replace("__ADD__", block(keyboard, "impl KeyboardState for Keyboard {"))
        for marker, name in (("__ISCONTROL__", "fn is_control("), ("__CONVERT__", "fn convert_event("), ("__READ__", "fn read_x_event(")):
            harness = harness.replace(marker, block(source, name))
        target = repo / ("queued-modifiers-" + mode + ".rs")
        literal_harness = harness
        conversion = block(source, "fn convert_event(")
        assert harness.count(conversion) == 1
        if has_static_mut_lint:
            unannotated = repo / ("queued-modifiers-" + mode + "-unannotated.rs")
            unannotated.write_text(harness, encoding="utf-8")
            old_compilation = subprocess.run([RUSTC, "--edition=2021", "-D", "warnings", "--cfg", mode,
                                             str(unannotated), "-o", str(unannotated.with_suffix(".exe" if os.name == "nt" else ""))],
                                            capture_output=True, text=True, encoding="utf-8", timeout=90)
            assert old_compilation.returncode != 0 and "static_mut_refs" in old_compilation.stderr, old_compilation.stderr
            if args.evidence_dir:
                shutil.copyfile(unannotated, args.evidence_dir / unannotated.name)
                (args.evidence_dir / ("queued-modifiers-" + mode + "-unannotated-compile.log")).write_text(
                    old_compilation.stdout + old_compilation.stderr, encoding="utf-8")
        harness = harness.replace(conversion, fixture_attribute + conversion, 1)
        assert harness.replace(fixture_attribute + conversion, conversion, 1) == literal_harness
        target.write_text(harness, encoding="utf-8")
        binary = target.with_suffix(".exe" if os.name == "nt" else "")
        compilation = subprocess.run([RUSTC, "--edition=2021", "-D", "warnings", "--cfg", mode, str(target), "-o", str(binary)],
                                     capture_output=True, text=True, encoding="utf-8", timeout=90)
        assert compilation.returncode == 0, compilation.stderr
        warning_target = repo / ("queued-modifiers-" + mode + "-unrelated-warning.rs")
        warning_target.write_text(harness + "\nfn unrelated_unused_fixture_function() {}\n", encoding="utf-8")
        warning_compilation = subprocess.run([RUSTC, "--edition=2021", "-D", "warnings", "--cfg", mode,
                                             str(warning_target), "-o", str(warning_target.with_suffix(".exe" if os.name == "nt" else ""))],
                                            capture_output=True, text=True, encoding="utf-8", timeout=90)
        assert warning_compilation.returncode != 0 and "unrelated_unused_fixture_function" in warning_compilation.stderr, warning_compilation.stderr
        if args.evidence_dir:
            shutil.copyfile(warning_target, args.evidence_dir / warning_target.name)
            (args.evidence_dir / ("queued-modifiers-" + mode + "-unrelated-warning-compile.log")).write_text(
                warning_compilation.stdout + warning_compilation.stderr, encoding="utf-8")
        executed = subprocess.run([str(binary)], capture_output=True, text=True, encoding="utf-8", timeout=15)
        assert executed.returncode == 0, executed.stderr
        print(executed.stdout.strip())
        if args.evidence_dir:
            shutil.copyfile(target, args.evidence_dir / target.name)
            (args.evidence_dir / ("queued-modifiers-" + mode + "-compile.log")).write_text(compilation.stdout + compilation.stderr, encoding="utf-8")
            (args.evidence_dir / ("queued-modifiers-" + mode + "-run.log")).write_text(executed.stdout + executed.stderr, encoding="utf-8")

with tempfile.TemporaryDirectory(prefix="mixel-input-tests-") as temporary:
    repo = Path(temporary)
    for relative in ("Cargo.toml", "Cargo.lock"):
        text = subprocess.run(["git", "-C", str(UPSTREAM), "show", "HEAD:" + relative],
                              check=True, capture_output=True, text=True, encoding="utf-8").stdout
        (repo / relative).write_text(text, encoding="utf-8")
    original_lock = (repo / "Cargo.lock").read_text(encoding="utf-8")
    patcher.apply(repo)
    dependency = repo / patcher.DEPENDENCY
    files = [repo / "Cargo.toml", repo / "Cargo.lock", dependency / "src/linux/grab.rs", dependency / patcher.ANDROID_PATH]
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
    assert patcher.patch_grab(patcher.patch_grab_legacy(original)) == actual
    assert patcher.patch_grab(actual) == actual
    for old, new in (("state & 0xFFFB", "state & 0xFFFF"), ("x_event.key.state", "0")):
        reject(lambda: patcher.patch_grab(actual.replace(old, new)), "queued modifier source changed")
    partial = patcher.patch_grab_legacy(original).replace("        let event = convert_event(keycode, is_press);",
                                                        "        let state = unsafe { x_event.key.state };\n        let event = convert_event(keycode, is_press, state);")
    reject(lambda: patcher.patch_grab(partial), "Mixed rdev queued modifier correction")
    for invalid in (partial, actual.replace("state & 0xFFFB", "state & 0xFFFF")):
        files[2].write_text(invalid, encoding="utf-8")
        guarded_refusal(repo, lambda: patcher.apply(repo), "unexpected changes")
    files[2].write_text(actual, encoding="utf-8")


    android_original = git_source(dependency, patcher.ANDROID_PATH)
    assert hashlib.sha256(android_original.encode()).hexdigest() == patcher.ANDROID_SHA256
    android_corrected = patcher.patch_android(android_original)
    assert android_corrected == android_original.replace("    BackQuote, 75,\n", "    BackQuote, 68,\n", 1)
    android_initial = files[3].read_bytes()
    android_initial_newline = b"\r\n" if b"\r\n" in android_initial else b"\n"
    assert android_initial == android_corrected.encode().replace(b"\n", android_initial_newline)
    assert patcher.patch_android(android_corrected) == android_corrected
    for newline in ("\n", "\r\n"):
        assert patcher.android_bytes(android_original.replace("\n", newline).encode()) == android_corrected.replace("\n", newline).encode()
        assert patcher.android_bytes(android_corrected.replace("\n", newline).encode()) == android_corrected.replace("\n", newline).encode()
    for malformed in (android_original.replace("    Quote, 75,", "    Quote, 76,"),
                      android_original.replace("    BackQuote, 75,", "    BackQuote, 67,"),
                      android_original + "// unrelated table change\n"):
        reject(lambda: patcher.patch_android(malformed), "mapping table changed")
    reject(lambda: patcher.android_bytes(android_original.replace("\n", "\r\n").encode() + b"// mixed\n"), "Mixed or unsupported")
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
    # Reject malformed/missing/linked Android source before a pending Linux,
    # manifest or lock correction can write any valid path.
    valid = files[3].read_bytes()
    files[2].write_text(original, encoding="utf-8")
    (repo / "Cargo.toml").write_text(manifest.replace(patcher.PATCH_TABLE, ""), encoding="utf-8")
    (repo / "Cargo.lock").write_text(original_lock, encoding="utf-8")
    for changed in (android_original.replace("    Quote, 75,", "    Quote, 76,").encode(),
                    android_original.replace("\n", "\r\n").encode() + b"// mixed\n"):
        files[3].write_bytes(changed)
        guarded_refusal(repo, lambda: patcher.apply(repo), "mapping table changed" if b"Quote, 76" in changed else "Mixed or unsupported")
    files[3].unlink()
    guarded_refusal(repo, lambda: patcher.apply(repo), "Missing pinned Android mapping source")
    linked = repo / "outside-android.rs"
    linked.write_bytes(valid)
    os.link(linked, files[3])
    guarded_refusal(repo, lambda: patcher.apply(repo), "owned ordinary file")
    assert linked.read_bytes() == valid
    files[3].unlink()
    if os.name != "nt":
        files[3].symlink_to(linked)
        guarded_refusal(repo, lambda: patcher.apply(repo), "owned ordinary file")
        assert linked.read_bytes() == valid
        files[3].unlink()
    linked.unlink()
    files[3].write_bytes(valid)
    patcher.apply(repo)
    assert files[2].read_text(encoding="utf-8") == actual
    android_converters(repo, dependency)
    queued_modifier_controls(repo, dependency)
    mac_spec = importlib.util.spec_from_file_location("mixel_mac_input_patch", ROOT / "scripts/patch-support-macos-rdev.py")
    mac = importlib.util.module_from_spec(mac_spec)
    mac_spec.loader.exec_module(mac)
    profile_reports = []
    for profile in ("lf", "crlf"):
        state = repo / ("profile-" + profile)
        state.mkdir()
        for name in ("Cargo.toml", "Cargo.lock"):
            shutil.copyfile(repo / name, state / name)
        clone = state / patcher.DEPENDENCY
        clone.parent.mkdir()
        subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(dependency), str(clone)], check=True)
        subprocess.run(["git", "-C", str(clone), "remote", "set-url", "origin", patcher.UPSTREAM_URL], check=True)
        subprocess.run(["git", "-C", str(clone), "config", "core.autocrlf", "true" if profile == "crlf" else "false"], check=True)
        subprocess.run(["git", "-C", str(clone), "config", "core.eol", profile], check=True)
        paths = subprocess.check_output(["git", "-C", str(clone), "ls-files", "-z"]).decode().split("\0")[:-1]
        assert len(paths) == 53
        for name in paths:
            (clone / name).unlink()
        subprocess.run(["git", "-C", str(clone), "checkout", "HEAD", "--", "."], check=True)
        newline = "\r\n" if profile == "crlf" else "\n"
        for name in paths:
            assert (clone / name).read_bytes() == git_source(clone, name).replace("\n", newline).encode(), name
        patcher.apply(state)
        android = clone / patcher.ANDROID_PATH
        assert android.read_bytes() == android_corrected.replace("\n", newline).encode()
        clean = snapshot(state)
        patcher.apply(state)
        assert snapshot(state) == clean
        mac.apply(state)
        native = snapshot(state)
        mac.apply(state, check=True)
        assert snapshot(state) == native
        linux = clone / "src/linux/grab.rs"
        fixed_linux = linux.read_bytes()
        linux.write_bytes(patcher.patch_grab_legacy(original).replace("\n", newline).encode())
        legacy_linux = snapshot(state)
        mac.apply(state, restore=True, check=True)
        assert snapshot(state) == legacy_linux
        guarded_refusal(state, lambda: mac.apply(state, check=True), "Pinned Linux input overlay is required")
        mac.apply(state, restore=True)
        assert linux.read_bytes() == patcher.patch_grab_legacy(original).replace("\n", newline).encode()
        patcher.apply(state)
        assert snapshot(state) == clean
        mac.apply(state)
        assert snapshot(state) == native
        # A partial snapshot correction must refuse before any Mac write.
        linux.write_bytes(patcher.patch_grab_legacy(original).replace("kbd.add(&event_type)", "kbd.add(&event_type) /* drift */").replace("\n", newline).encode())
        guarded_refusal(state, lambda: mac.apply(state, restore=True), "Pinned Linux input overlay is required")
        linux.write_bytes(fixed_linux)
        assert snapshot(state) == native
        # Exact old-table migration is restore-only, not a broad input waiver.
        android.write_bytes(android_original.replace("\n", newline).encode())
        legacy = snapshot(state)
        mac.apply(state, restore=True, check=True)
        assert snapshot(state) == legacy
        guarded_refusal(state, lambda: mac.apply(state, check=True), "Pinned Android input overlay is required")
        mac.apply(state, restore=True)
        assert android.read_bytes() == android_original.replace("\n", newline).encode()
        patcher.apply(state)
        assert snapshot(state) == clean
        mac.apply(state)
        android.write_bytes(android_corrected.replace("    Quote, 75,", "    Quote, 76,").replace("\n", newline).encode())
        guarded_refusal(state, lambda: mac.apply(state, restore=True, check=True), "Pinned Android input overlay is required")
        android.write_bytes(android_corrected.replace("\n", newline).encode())
        mac.apply(state, restore=True)
        assert snapshot(state) == clean, "Mac migration/restoration changed input or other runtime source"
        profile_reports.append({"profile": profile, "tracked_paths": len(paths), "idempotent": True,
                                "exact_legacy_restore_only": True, "drift_refusal_zero_write": True,
                                "old_android_sha256": hashlib.sha256(android_original.encode()).hexdigest(),
                                "corrected_android_sha256": hashlib.sha256(android_corrected.encode()).hexdigest()})
    if args.evidence_dir:
        (args.evidence_dir / "guarded-profiles.json").write_text(json.dumps(profile_reports, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("PASS: full-table pin; LF/CRLF owned 53-file profiles; missing, linked, mixed and unrelated table refusals precede all writes; exact old-table Mac restore migration preserves other source bytes")
    # Run the generated production loop unchanged. Poll/Xlib boundaries are
    # deterministic here; native signal/capture integration is a separate test.
    harness = HARNESS.replace("__ORIGINAL__", function(original).replace("fn loop_poll_x_event(", "fn original_loop_poll_x_event(", 1))
    harness = harness.replace("__PATCHED__", function(actual))
    source = repo / "input-tests.rs"
    source.write_text(harness, encoding="utf-8")
    binary = repo / ("input-tests.exe" if os.name == "nt" else "input-tests")
    subprocess.run(rustc_command([RUSTC, "--edition=2021", "--deny", "warnings", "--test", str(source), "-o", str(binary)]), check=True)
    subprocess.run([str(binary), "--test-threads=1"], check=True)

print("PASS: actual pinned X11 event loop retries interrupted polling without losing keyboard events or changing fatal/stop handling")
