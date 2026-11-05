//! Actual Windows handle_scale/get_modifier_state with queue/OS state effect seams.
//! Control injection is recorded, never sent to an operating system.
use crate::message_proto::ControlKey;
use protobuf::Enum;
use std::collections::{BTreeSet,HashMap};
use std::sync::{Mutex,atomic::{AtomicI16,Ordering}};
use std::time::Instant;
#[allow(dead_code)]#[derive(Clone,Copy,Debug,PartialEq,Eq,PartialOrd,Ord)]enum Key {Control,RightControl,Shift,RightShift,Alt,RightAlt,Meta,RWin,Other}
#[derive(Clone,Copy,PartialEq,Eq,Hash)]enum RawKey {ScanCode(u32),WinVirtualKeycode(u32),Other}
#[derive(Clone,Copy,PartialEq,Eq,Hash)]enum KeysDown {RdevKey(RawKey),EnigoKey(u64)}
struct Enigo {held:BTreeSet<Key>,query:BTreeSet<Key>,effects:Vec<&'static str>,fail:bool}
impl Enigo {
 fn get_key_state(&mut self,key:Key)->bool {self.query.contains(&key)}
 fn key_down(&mut self,key:Key)->Result<(),()> {self.effects.push("down");if self.fail{return Err(());}self.held.insert(key);Ok(())}
 fn key_up(&mut self,key:Key) {self.effects.push("up");self.held.remove(&key);}
 fn mouse_scroll_y(&mut self,_scale:i32) {self.effects.push("scroll");assert!(self.held.contains(&Key::Control)||self.held.contains(&Key::RightControl)||self.query.contains(&Key::Control)||self.query.contains(&Key::RightControl)||ASYNC_LEFT.load(Ordering::SeqCst)<0||ASYNC_RIGHT.load(Ordering::SeqCst)<0||!KEYS_DOWN.lock().unwrap().is_empty());}
}
static ENIGO:Mutex<Enigo>=Mutex::new(Enigo{held:BTreeSet::new(),query:BTreeSet::new(),effects:Vec::new(),fail:false});
struct Ledger(std::sync::OnceLock<Mutex<HashMap<KeysDown,Instant>>>);
impl Ledger {fn lock(&self)->std::sync::LockResult<std::sync::MutexGuard<'_,HashMap<KeysDown,Instant>>> {self.0.get_or_init(||Mutex::new(HashMap::new())).lock()}}
static KEYS_DOWN:Ledger=Ledger(std::sync::OnceLock::new());
static ASYNC_LEFT:AtomicI16=AtomicI16::new(0);static ASYNC_RIGHT:AtomicI16=AtomicI16::new(0);
mod winapi {pub mod um {#[allow(non_snake_case)]pub mod winuser {pub const VK_LCONTROL:i32=0xA2;pub const VK_RCONTROL:i32=0xA3;pub unsafe fn GetAsyncKeyState(key:i32)->i16 {if key==VK_LCONTROL {super::super::super::ASYNC_LEFT.load(std::sync::atomic::Ordering::SeqCst)}else{super::super::super::ASYNC_RIGHT.load(std::sync::atomic::Ordering::SeqCst)}}}}}
include!("scale_balanced_selected.rs");
#[cfg(feature="original-requirements")]include!("scale_balanced_original.rs");
fn reset() {let mut en=ENIGO.lock().unwrap();en.held.clear();en.query.clear();en.effects.clear();en.fail=false;KEYS_DOWN.lock().unwrap().clear();ASYNC_LEFT.store(0,Ordering::SeqCst);ASYNC_RIGHT.store(0,Ordering::SeqCst);}
fn empty()->bool {ENIGO.lock().unwrap().held.is_empty()}
#[test]fn actual_balanced_receiver_normal_denied_and_network_close_need_no_scale_end() {
 for _close in ["normal close","denied close","network drop"] {reset();handle_scale(9);assert!(empty());assert_eq!(ENIGO.lock().unwrap().effects,["down","scroll","up"]);}
}
#[test]fn actual_balanced_receiver_each_update_and_zero_preserve_foreign_control() {
 for side in [Key::Control,Key::RightControl] {reset();{let mut en=ENIGO.lock().unwrap();en.query.insert(side);en.held.insert(side);}handle_scale(4);handle_scale(-2);handle_scale(0);let en=ENIGO.lock().unwrap();assert_eq!(en.effects,["scroll","scroll"]);assert_eq!(en.held,BTreeSet::from([side]));}
 reset();handle_scale(0);assert!(ENIGO.lock().unwrap().effects.is_empty());
}
#[test]fn actual_balanced_receiver_live_async_control_is_preserved_when_enigo_query_lags() {
 for side in [&ASYNC_LEFT,&ASYNC_RIGHT] {reset();side.store(i16::MIN,Ordering::SeqCst);handle_scale(1);handle_scale(0);assert_eq!(ENIGO.lock().unwrap().effects,["scroll"]);}
}
#[test]fn actual_balanced_receiver_recorded_remote_control_survives_native_query_lag() {
 let records=[KeysDown::EnigoKey(ControlKey::Control.value() as u64),KeysDown::EnigoKey(ControlKey::RControl.value() as u64),KeysDown::RdevKey(RawKey::ScanCode(0x1D)),KeysDown::RdevKey(RawKey::ScanCode(0xE01D)),KeysDown::RdevKey(RawKey::WinVirtualKeycode(0x11)),KeysDown::RdevKey(RawKey::WinVirtualKeycode(0xA2)),KeysDown::RdevKey(RawKey::WinVirtualKeycode(0xA3))];
 for record in records {reset();KEYS_DOWN.lock().unwrap().insert(record,Instant::now());handle_scale(3);handle_scale(0);assert_eq!(ENIGO.lock().unwrap().effects,["scroll"]);assert!(KEYS_DOWN.lock().unwrap().contains_key(&record));}
}
#[test]fn actual_balanced_receiver_noncontrol_records_do_not_suppress_temporary_control() {
 for record in [KeysDown::RdevKey(RawKey::Other),KeysDown::RdevKey(RawKey::ScanCode(30)),KeysDown::RdevKey(RawKey::WinVirtualKeycode(0x41)),KeysDown::EnigoKey(ControlKey::Shift.value() as u64)] {reset();KEYS_DOWN.lock().unwrap().insert(record,Instant::now());handle_scale(1);assert_eq!(ENIGO.lock().unwrap().effects,["down","scroll","up"]);assert!(empty());}
}
#[test]fn actual_balanced_receiver_failed_control_down_never_scrolls_or_releases_foreign_state() {reset();ENIGO.lock().unwrap().fail=true;handle_scale(3);assert_eq!(ENIGO.lock().unwrap().effects,["down"]);assert!(empty());}
#[test]#[cfg(feature="original-requirements")]
fn original_requirement_scale_receiver_close_must_not_retain_temporary_control() {reset();original_handle_scale(3);assert!(empty(),"original receiver waits for Scale0 forever after normal/denied/network close");}
