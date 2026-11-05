//! Actual Windows Translate/hotkey code and receiver-specific Map arm projected
//! into an OS-free component; physical hook state/layout calls are mocked seams.
use super::{Event,EventType,Key,KeyEvent,KeyboardMode,OS_LOWER_WINDOWS,is_numpad_key};
const OS_LOWER_LINUX:&str="linux";
static mut IS_0X021D_DOWN:bool=false;
fn get_unicode_from_vk(_vk:u32)->Option<u16> {None}
fn get_char_from_vk(_vk:u32)->Option<char> {None}
mod rdev {pub use super::super::rdev::*;pub fn get_modifier(key:Key)->bool {matches!(key,Key::ControlLeft)}}
include!("windows_modifiers_selected.rs");
#[cfg(feature="original-requirements")]
include!("windows_modifiers_original.rs");
fn event(key:Key,down:bool)->Event {
    let vk=super::keycodes::windows::code_from_key(key).unwrap();let scan=super::keycodes::windows::scancode_from_key(key).unwrap();
    Event{time:std::time::SystemTime::now(),unicode:None,platform_code:vk,position_code:scan,event_type:if down {EventType::KeyPress(key)}else{EventType::KeyRelease(key)},usb_hid:0,extra_data:0}
}
fn pair(original:bool) {
 use crate::keyboard_state::*;
 for key in [Key::ControlLeft,Key::ControlRight,Key::MetaLeft,Key::MetaRight,Key::ShiftLeft,Key::ShiftRight,Key::Alt,Key::AltGr] {
  for swap in [false,true] {
   let mut s=KeyboardState::new(true,0);
   for down in [true,false] {
    let template=KeyEvent{mode:KeyboardMode::Translate.into(),..Default::default()};
    let ev=event(key,down);
    let mut converted=if original {
      #[cfg(feature="original-requirements")] {translate_keyboard_mode_r9("windows",&ev,template)}
      #[cfg(not(feature="original-requirements"))] {let _=template;Vec::new()}
    }else{translate_keyboard_mode("windows",&ev,template)};
    assert_eq!(converted.len(),1);
    let mut wire=converted.remove(0);crate::modifier_swap::windows_swap_fixture(&mut wire,swap);
    let sent=s.prepare_input(KeyboardInput{event:wire,origin:KeyboardOrigin::Native,epoch:0,permitted:true,modifiers:None});assert_eq!(sent.len(),1,"{key:?}: Down/Up must share physical ownership, swap={swap}");
   }
   s.set_permission(false);assert!(s.set_permission(true).is_empty());
  }
 }
}
#[test] fn actual_windows_modifier_down_none_unicode_and_up_prior_hook_state_keep_scan_identity() {pair(false);}
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_r9_windows_modifier_up_vk_alias_must_balance_scan_down() {pair(true);}
