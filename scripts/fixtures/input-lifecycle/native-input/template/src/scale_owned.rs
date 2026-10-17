//! Actual Windows handle_scale body with an Enigo effect recorder and real
//! production protobuf/Session/Remote ownership. No OS input executes.
use crate::{keyboard_state::KeyboardState,message_proto::*};
use std::sync::Mutex;
use protobuf::{Enum,EnumOrUnknown};
use std::collections::BTreeSet;
#[derive(Clone,Copy,PartialEq,Eq,PartialOrd,Ord)]enum Key {Control,RightControl,Shift}
struct Enigo {held:BTreeSet<Key>,scrolls:Vec<i32>}
impl Enigo {fn key_down(&mut self,key:Key)->Result<(),()> {self.held.insert(key);Ok(())}fn key_up(&mut self,key:Key) {self.held.remove(&key);}fn mouse_scroll_y(&mut self,value:i32){self.scrolls.push(value);}}
static ENIGO:Mutex<Enigo>=Mutex::new(Enigo{held:BTreeSet::new(),scrolls:Vec::new()});
static UNICODE_ATTEMPTS:Mutex<Vec<u16>>=Mutex::new(Vec::new());
fn control_key_value_to_key(value:i32)->Option<Key> {match ControlKey::from_i32(value)? {ControlKey::Control=>Some(Key::Control),ControlKey::RControl=>Some(Key::RightControl),ControlKey::Shift=>Some(Key::Shift),_=>None}}
fn sim_rdev_rawkey_position(code:u32,down:bool) {let key=match code {0x1D=>Key::Control,0xE01D=>Key::RightControl,0x2A=>Key::Shift,_=>return};let mut en=ENIGO.lock().unwrap();if down {en.key_down(key).unwrap();}else{en.key_up(key);}}
fn sim_rdev_rawkey_virtual(vk:u32,down:bool) {sim_rdev_rawkey_position(rdev::vk_to_scancode(vk),down);}
mod rdev {
 pub fn simulate_key_unicode(value:u16,_flag:bool)->Result<(),()> {super::UNICODE_ATTEMPTS.lock().unwrap().push(value);Err(())}
 pub fn vk_to_scancode(value:u32)->u32 {match value {0x11|0xA2=>0x1D,0xA3=>0xE01D,_=>0}}
 pub fn simulate_code(_vk:Option<u32>,scan:Option<u32>,down:bool)->Result<(),()> {super::sim_rdev_rawkey_position(scan.unwrap(),down);Ok(())}
}
macro_rules! allow_err {($e:expr)=>{let _=$e;}}
include!("scale_receiver_selected.rs");
fn apply_wire(event:&KeyEvent) {
 match event.mode.enum_value_or_default() {
  KeyboardMode::Map=>map_keyboard_mode(event),
  KeyboardMode::Translate=>match &event.union {Some(key_event::Union::Chr(code))=>translate_process_code(*code,event.down),Some(key_event::Union::Win2winHotkey(code))=>simulate_win2win_hotkey(*code,event.down),_=>{}},
  _=>if let Some(key_event::Union::ControlKey(key))=&event.union {process_control_key(&mut ENIGO.lock().unwrap(),key,event.down);},
 }
}
include!("scale_server_selected.rs");
#[cfg(feature="original-requirements")]
#[allow(dead_code)]
#[path="original_scale_state.rs"]mod original;
pub(crate) fn scale_message(scale:i32)->Message {
    let mut touch=TouchEvent::new();touch.set_scale_update(TouchScaleUpdate{scale,..Default::default()});let mut pointer=PointerDeviceEvent::new();pointer.modifiers=vec![ControlKey::Shift.into()];pointer.set_touch_event(touch);let mut message=Message::new();message.set_pointer_device_event(pointer);message
}
pub(crate) fn scale_value(message:&Message)->Option<i32> {
    if let Some(message::Union::PointerDeviceEvent(pointer))=&message.union {if let Some(pointer_device_event::Union::TouchEvent(touch))=&pointer.union {if let Some(touch_event::Union::ScaleUpdate(scale))=&touch.union {return Some(scale.scale);}}}None
}
fn reset() {let mut en=ENIGO.lock().unwrap();en.held.clear();en.scrolls.clear();UNICODE_ATTEMPTS.lock().unwrap().clear();}
fn apply(message:Message) {handle_scale(scale_value(&message).unwrap());}
fn ctrl()->bool {{let en=ENIGO.lock().unwrap();en.held.contains(&Key::Control)||en.held.contains(&Key::RightControl)}}
#[test]fn actual_scale_denied_end_released_on_real_grant_exactly_once() {
    reset();let mut s=KeyboardState::new(true,0);apply(s.prepare_pointer(scale_message(12),0,true).unwrap());assert!(ctrl());s.set_permission(false);assert!(s.prepare_pointer(scale_message(0),s.epoch(),false).is_none());assert!(s.set_permission(true).is_empty());let end=s.take_scale_release().unwrap();assert_eq!(scale_value(&end),Some(0));if let Some(message::Union::PointerDeviceEvent(pointer))=&end.union {assert!(pointer.modifiers.is_empty());}apply(end);assert!(!ctrl());s.set_permission(true);assert!(s.take_scale_release().is_none());
}
#[test]fn actual_scale_denied_source_change_defers_end_and_preserves_latest_owned_template() {
    reset();let mut s=KeyboardState::new(true,0);apply(s.prepare_pointer(scale_message(3),0,true).unwrap());apply(s.prepare_pointer(scale_message(7),0,true).unwrap());s.set_permission(false);assert!(s.change_source(8).is_empty());assert!(s.take_scale_release().is_none());assert!(ctrl());s.set_permission(true);apply(s.take_scale_release().unwrap());assert!(!ctrl());assert!(s.take_scale_release().is_none());
}
#[test]fn actual_scale_foreign_or_unowned_end_cannot_release_another_connection() {
    reset();let mut a=KeyboardState::new(true,0);let mut b=KeyboardState::new(true,0);apply(a.prepare_pointer(scale_message(5),0,true).unwrap());assert!(b.prepare_pointer(scale_message(0),0,true).is_none());b.change_source(1);assert!(b.take_scale_release().is_none());assert!(ctrl());a.change_source(1);apply(a.take_scale_release().unwrap());assert!(!ctrl());assert!(a.prepare_pointer(scale_message(0),1,true).is_none());
}
#[test]fn actual_scale_stale_or_denied_start_never_creates_owned_cleanup() {
    let mut s=KeyboardState::new(true,0);s.set_permission(false);s.set_permission(true);assert!(s.prepare_pointer(scale_message(12),0,true).is_none());assert!(s.prepare_pointer(scale_message(12),s.epoch(),false).is_none());s.change_source(9);assert!(s.take_scale_release().is_none());
}
#[test]#[cfg(feature="original-requirements")]
fn original_requirement_r9_scale_denied_end_must_not_leave_windows_control_held() {
    reset();let mut s=original::KeyboardState::new(true,0);apply(s.prepare_pointer(scale_message(12),0,true).unwrap());assert!(ctrl());s.set_permission(false);assert!(s.prepare_pointer(scale_message(0),s.epoch(),false).is_none());s.set_permission(true);assert!(!ctrl(),"R9 drops Scale0 and has no owned ScaleEnd on regrant; Windows Control remains held");
}

fn input(state:&mut KeyboardState,event:KeyEvent,origin:crate::keyboard_state::KeyboardOrigin)->Vec<KeyEvent> {let epoch=state.epoch();state.prepare_input(crate::keyboard_state::KeyboardInput{event,origin,epoch,permitted:true,modifiers:None})}
fn template(mode:KeyboardMode,code:u32,right:bool)->KeyEvent {let mut event=KeyEvent{down:true,mode:mode.into(),..Default::default()};if mode==KeyboardMode::Legacy||mode==KeyboardMode::Auto {event.set_control_key(if right {ControlKey::RControl}else{ControlKey::Control});}else{event.set_chr(code);}event.modifiers=vec![ControlKey::Alt.into()];event}
pub(crate) fn check_owned_control_after_normal_scale(mut event:KeyEvent,origin:crate::keyboard_state::KeyboardOrigin) {
    reset();let mut s=KeyboardState::new(true,0);s.set_windows_peer(true);
    let mut shift=KeyEvent{down:true,mode:KeyboardMode::Legacy.into(),..Default::default()};shift.set_control_key(ControlKey::Shift);for e in input(&mut s,shift,origin){apply_wire(&e);}
    for e in input(&mut s,event.clone(),origin){apply_wire(&e);}assert!(ctrl());let text_before=UNICODE_ATTEMPTS.lock().unwrap().len();
    apply(s.prepare_pointer(scale_message(9),0,true).unwrap());apply(s.prepare_pointer(scale_message(0),0,true).unwrap());
    let restore=s.take_scale_controls();assert_eq!(restore.len(),1);assert_eq!(restore[0].mode.enum_value_or_default(),KeyboardMode::Map);assert!(restore[0].modifiers.is_empty());assert!(restore[0].down&&!restore[0].press);
    for e in restore {apply_wire(&e);}assert!(ctrl());assert!(ENIGO.lock().unwrap().held.contains(&Key::Shift));assert_eq!(UNICODE_ATTEMPTS.lock().unwrap().len(),text_before,"ScaleEnd replay cannot type Unicode");
    event.down=false;let foreign=if origin==crate::keyboard_state::KeyboardOrigin::Ui {crate::keyboard_state::KeyboardOrigin::Native}else{crate::keyboard_state::KeyboardOrigin::Ui};assert!(input(&mut s,event.clone(),foreign).is_empty(),"replay must not transfer owner");
    for e in input(&mut s,event,origin){apply_wire(&e);}assert!(!ctrl());assert!(ENIGO.lock().unwrap().held.contains(&Key::Shift));
}
#[test]fn actual_normal_scale_end_restores_only_own_ui_and_native_windows_controls_without_text_or_side_loss() {
    use crate::keyboard_state::KeyboardOrigin::*;
    for origin in [Ui,Native] {for mode in [KeyboardMode::Legacy,KeyboardMode::Auto,KeyboardMode::Map,KeyboardMode::Translate] {for right in [false,true] {check_owned_control_after_normal_scale(template(mode,if right {0xE01D}else{0x1D},right),origin);}}}
    for origin in [Ui,Native] {for vk in [0x11,0xA2,0xA3] {let event=template(KeyboardMode::Translate,(vk<<16)|0x61,vk==0xA3);check_owned_control_after_normal_scale(event,origin);let mut hotkey=template(KeyboardMode::Translate,0,false);hotkey.set_win2win_hotkey((vk<<16)|0x62);check_owned_control_after_normal_scale(hotkey,origin);}}
}
#[test]fn actual_scale_end_never_replays_noncontrols_nonwindows_or_foreign_holds() {
    use crate::keyboard_state::KeyboardOrigin::Ui;
    for windows in [true,false] {let mut s=KeyboardState::new(true,0);s.set_windows_peer(windows);let mut events=vec![template(KeyboardMode::Map,30,false),template(KeyboardMode::Legacy,0,false),template(KeyboardMode::Map,0xE05B,false)];events[1].set_control_key(ControlKey::Meta);
      for event in events {input(&mut s,event,Ui);}s.prepare_pointer(scale_message(3),0,true);s.prepare_pointer(scale_message(0),0,true);assert!(s.take_scale_controls().is_empty());}
    let mut s=KeyboardState::new(true,0);s.set_windows_peer(false);input(&mut s,template(KeyboardMode::Map,0x1D,false),Ui);s.prepare_pointer(scale_message(3),0,true);s.prepare_pointer(scale_message(0),0,true);assert!(s.take_scale_controls().is_empty());
}
#[test]fn actual_scale_cleanup_terminates_control_without_replay_and_normal_end_preserves_both_sides() {
 use crate::keyboard_state::KeyboardOrigin::Ui;
 let mut s=KeyboardState::new(true,0);s.set_windows_peer(true);for code in [0x1D,0xE01D] {input(&mut s,template(KeyboardMode::Map,code,false),Ui);}s.prepare_pointer(scale_message(5),0,true);s.prepare_pointer(scale_message(0),0,true);assert_eq!(s.take_scale_controls().len(),2);
 s.prepare_pointer(scale_message(8),0,true);s.set_permission(false);assert!(s.take_scale_controls().is_empty());assert_eq!(s.set_permission(true).len(),2);assert!(s.take_scale_release().is_some());assert!(s.take_scale_controls().is_empty());
}
#[test]#[cfg(feature="original-requirements")]
fn original_requirement_r9_normal_scale_end_must_preserve_same_remote_ui_control_before_next_key() {
    reset();let mut s=original::KeyboardState::new(true,0);let event=template(KeyboardMode::Map,0x1D,false);for e in s.prepare_input(original::KeyboardInput{event,origin:original::KeyboardOrigin::Ui,epoch:0,permitted:true,modifiers:None}) {apply_wire(&e);}apply(s.prepare_pointer(scale_message(6),0,true).unwrap());apply(s.prepare_pointer(scale_message(0),0,true).unwrap());assert!(ctrl(),"R9 next Ui key has no owned Control replay after Scale0");
}
