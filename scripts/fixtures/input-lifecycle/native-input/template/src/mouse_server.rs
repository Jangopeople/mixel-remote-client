//! Actual server modifier prefix/button decoder/suffix. Three cfg projections
//! execute on this host; Enigo is an effect recorder, never OS input.
use super::{MouseEvent,ControlKey,input::*};
use protobuf::{Enum,EnumOrUnknown};
use std::collections::{BTreeSet,HashMap,HashSet};
use std::sync::{Mutex,OnceLock};
struct Owners(OnceLock<Mutex<HashMap<i32,HashSet<i32>>>>);
impl Owners {fn lock(&self)->std::sync::LockResult<std::sync::MutexGuard<'_,HashMap<i32,HashSet<i32>>>> {self.0.get_or_init(Default::default).lock()}}
static MOUSE_BUTTON_OWNERS:Owners=Owners(OnceLock::new());
#[derive(Clone,Copy,Debug,PartialEq,Eq,PartialOrd,Ord)]
enum Key {Shift,RightShift,Control,RightControl,Alt,RightAlt,Meta,RWin,CapsLock,NumLock}
#[derive(Clone,Copy,PartialEq,Eq,PartialOrd,Ord)]enum MouseButton {Left,Right,Middle,Back,Forward}
pub(super) struct Enigo {held:BTreeSet<Key>,flags:BTreeSet<Key>,buttons:BTreeSet<MouseButton>,effects:Vec<&'static str>}
impl Enigo {
 fn key_down(&mut self,key:Key)->Result<(),()> {self.held.insert(key);self.effects.push("key_down");Ok(())}
 fn key_up(&mut self,key:Key) {self.held.remove(&key);self.effects.push("key_up");}
 fn reset_flag(&mut self) {self.flags.clear();}
 fn add_flag(&mut self,key:&Key) {self.flags.insert(*key);}
 fn mouse_down(&mut self,button:MouseButton)->Result<(),()> {assert!(self.held.contains(&Key::Shift)||self.flags.contains(&Key::Shift));self.buttons.insert(button);self.effects.push("mouse_down_with_shift");Ok(())}
 fn mouse_up(&mut self,button:MouseButton) {self.buttons.remove(&button);}
}
struct KeyMap;
impl KeyMap {fn get(&self,value:&i32)->Option<&'static Key> {match ControlKey::from_i32(*value)? {ControlKey::Shift=>Some(&Key::Shift),ControlKey::RShift=>Some(&Key::RightShift),ControlKey::Control=>Some(&Key::Control),ControlKey::RControl=>Some(&Key::RightControl),ControlKey::Alt=>Some(&Key::Alt),ControlKey::RAlt=>Some(&Key::RightAlt),ControlKey::Meta=>Some(&Key::Meta),ControlKey::RWin=>Some(&Key::RWin),ControlKey::CapsLock=>Some(&Key::CapsLock),ControlKey::NumLock=>Some(&Key::NumLock),_=>None}}}
static KEY_MAP:KeyMap=KeyMap;
fn get_modifier_state(key:Key,en:&mut Enigo)->bool {en.held.contains(&key)}
fn modifier_sleep() {}
macro_rules! allow_err {($e:expr)=>{let _=$e;}}
include!("mouse_server_selected.rs");
pub(super) fn assert_temporary_shift_click_all_desktop_decoders(mouse:&MouseEvent) {
 for (mac,decoder) in [(false,linux_mouse as fn(&MouseEvent,&mut Enigo)),(false,windows_mouse),(true,macos_mouse)] {
    let mut en=Enigo{held:BTreeSet::new(),flags:BTreeSet::new(),buttons:BTreeSet::new(),effects:Vec::new()};decoder(mouse,&mut en);
    assert!(en.held.is_empty(),"temporary mouse Shift must already be released before later unowned Native Up");
    assert_eq!(en.effects,if mac {vec!["mouse_down_with_shift"]}else{vec!["key_down","mouse_down_with_shift","key_up"]});
    let mut up=mouse.clone();up.mask=(MOUSE_BUTTON_LEFT<<3)|MOUSE_TYPE_UP;decoder(&up,&mut en);assert!(en.held.is_empty());assert!(en.buttons.is_empty());
 }
}
