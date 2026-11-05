#![cfg(target_os="macos")]
//! Exact rdev pure conversion modules; physical key reads and ANSI/ISO keyboard
//! layout query are effects mocked at their existing seams. No CG call executes.
use crate::{message_proto::*,rdev::{Event,EventType,Key}};
use std::sync::{Mutex,atomic::{AtomicU16,Ordering}};
#[allow(dead_code)]
pub mod types {include!("rdev_pure/rdev.rs");}
#[allow(dead_code,unreachable_patterns)]
#[path="rdev_pure/keycodes/mod.rs"]
pub mod keycodes;
#[allow(dead_code)]
pub mod macos {pub fn map_keycode(code:crate::KeyCode)->crate::KeyCode {code}}
#[allow(dead_code)]
#[path="rdev_pure/codes_conv.rs"]
pub mod codes_conv;
pub mod rdev {
    pub use super::types::*;
    pub use super::codes_conv::*;
    pub use super::keycodes::windows::{key_from_scancode as win_key_from_scancode,scancode_from_key as win_scancode_from_key};
    pub use super::keycodes::linux::{key_from_code as linux_key_from_code,code_from_key as linux_keycode_from_key};
    pub use super::keycodes::macos::{key_from_code as macos_key_from_code,code_from_key as macos_keycode_from_key};
    pub use super::keycodes::macos::{code_from_key};
    pub use super::keycodes::macos::virtual_keycodes::*;
}
const OS_LOWER_WINDOWS:&str="windows";

const OS_LOWER_MACOS:&str="macos";
const OS_LOWER_ANDROID:&str="android";
static PEER:Mutex<String>=Mutex::new(String::new());
static HELD:AtomicU16=AtomicU16::new(0);
static mut IS_LEFT_OPTION_DOWN:bool=false;
mod enigo {
    #[derive(Clone,Copy)]
    pub enum Key {Shift,RightShift,Control,RightControl,Alt,RightAlt,Meta,RWin,NumLock,CapsLock}
}
fn get_key_state(key:enigo::Key)->bool {HELD.load(Ordering::SeqCst)&(1<<(key as u16))!=0}
fn get_peer_platform()->String {PEER.lock().unwrap().clone()}
mod client {
    use super::*;
    pub fn set_cached(flags:u16) {
        for (key,bit) in [(Key::Alt,1),(Key::ControlLeft,2),(Key::ShiftLeft,4),(Key::MetaLeft,8)] {MODIFIERS_STATE.lock().unwrap().insert(key,flags&bit!=0);}
    }
    #[cfg(feature="original-requirements")]
    pub fn ctrl_alt_del() {}
    #[cfg(feature="original-requirements")]
    pub fn lock_screen() {}
    include!("native_cleanup_client_selected.rs");
    include!("native_ui_selected.rs");
    include!("mouse_physical_selected.rs");
    #[cfg(feature="original-requirements")]
    include!("native_ui_original.rs");
    #[cfg(feature="original-requirements")]
    include!("native_cleanup_client_r5.rs");
    #[cfg(feature="original-requirements")]
    include!("native_cleanup_client_r3.rs");
    #[cfg(feature="original-requirements")]
    pub fn process_event(mode:&str,event:&Event,locks:Option<i32>) {
        process_native_event_with_session(mode,event,locks,captured(),true);
    }
    include!("native_legacy_client_selected.rs");
}
include!("native_conversion_selected.rs");
#[test] fn actual_native_map_snapshot_preserves_all_supported_physical_sides_per_peer() {
    let expected=[("windows",vec![42,54,29,57373,56,57400,57435,57436]),
                  ("linux",vec![50,62,37,105,64,108,133,134]),
                  ("macos",vec![56,60,59,62,58,61,55,54]),
                  ("android",vec![59,60,113,114,57,58,117])];
    HELD.store(0xff,Ordering::SeqCst);
    for (peer,codes) in expected {
        *PEER.lock().unwrap()=peer.into();
        let event=KeyEvent {down:true,mode:KeyboardMode::Map.into(),..Default::default()};
        let events=native_modifier_snapshot(&event,&get_peer_platform()).unwrap();
        assert_eq!(events.iter().map(|e|e.chr()).collect::<Vec<_>>(),codes,"peer {peer}");
        assert!(events.iter().all(|e|e.down&&!e.press&&e.mode.enum_value_or_default()==KeyboardMode::Map));
    }
    HELD.store(0,Ordering::SeqCst);
}
#[test] fn actual_native_translate_snapshot_preserves_pinned_right_option_exclusion() {
    HELD.store(0xff,Ordering::SeqCst);
    for peer in ["windows","linux","macos","android"] {
        *PEER.lock().unwrap()=peer.into();
        let event=KeyEvent {down:true,mode:KeyboardMode::Translate.into(),..Default::default()};
        let events=native_modifier_snapshot(&event,&get_peer_platform()).unwrap();assert_eq!(events.len(),if peer=="android" {6}else{7});
        assert!(events.iter().all(|e|e.down&&!e.press&&e.mode.enum_value_or_default()==KeyboardMode::Translate));
    }
    HELD.store(0,Ordering::SeqCst);
}
#[test] fn actual_native_snapshot_reads_current_physical_state_not_stale_global_cache() {
    *PEER.lock().unwrap()="Windows".into();
    let event=KeyEvent {down:true,mode:KeyboardMode::Map.into(),..Default::default()};
    HELD.store(1<<1,Ordering::SeqCst);let events=native_modifier_snapshot(&event,&get_peer_platform()).unwrap();
    assert_eq!(events.len(),1);assert_eq!(events[0].chr(),54);
    HELD.store(0,Ordering::SeqCst);assert!(native_modifier_snapshot(&event,&get_peer_platform()).unwrap().is_empty());
    let event=KeyEvent {down:false,..event};assert!(native_modifier_snapshot(&event,&get_peer_platform()).is_none());
}

fn physical_event(key:Key)->Event {
    let code=rdev::code_from_key(key).unwrap();
    Event {time:std::time::SystemTime::now(),unicode:None,platform_code:code as _,position_code:code as _,event_type:EventType::KeyPress(key),usb_hid:0,extra_data:0}
}
#[test] fn native_legacy_captured_peer_never_recaptures_new_foreground_platform() {
    *PEER.lock().unwrap()="linux".into();HELD.store(0,Ordering::SeqCst);
    let mut template=KeyEvent::new();template.mode=KeyboardMode::Legacy.into();
    let events=legacy_keyboard_mode_for_peer("Windows",&physical_event(Key::Kp1),template.clone(),false);
    assert_eq!(events[0].control_key(),ControlKey::End);
    let events=legacy_keyboard_mode_for_peer("Linux",&physical_event(Key::Kp1),template,false);
    assert_eq!(events[0].control_key(),ControlKey::Numpad1);
}
#[test] fn native_legacy_ignores_cached_released_ui_modifier_but_preserves_real_hold() {
    client::set_cached(4);HELD.store(0,Ordering::SeqCst);
    let template=KeyEvent {mode:KeyboardMode::Legacy.into(),..Default::default()};
    let event=physical_event(Key::KeyA);
    let physical=legacy_keyboard_mode_for_peer("Windows",&event,template.clone(),false);
    assert!(!physical[0].modifiers.contains(&ControlKey::Shift.into()));
    let ui=legacy_keyboard_mode_for_peer("Windows",&event,template.clone(),true);
    assert!(ui[0].modifiers.contains(&ControlKey::Shift.into()),"existing UI cache path conserved");
    HELD.store(1,Ordering::SeqCst);
    let held=legacy_keyboard_mode_for_peer("Windows",&event,template,false);
    assert!(held[0].modifiers.contains(&ControlKey::Shift.into()));
    HELD.store(0,Ordering::SeqCst);client::set_cached(0);
}
#[test] fn native_legacy_ctrl_alt_del_and_lock_return_to_captured_session_path() {
    *PEER.lock().unwrap()="linux".into();HELD.store((1<<2)|(1<<4),Ordering::SeqCst);
    let template=KeyEvent {mode:KeyboardMode::Legacy.into(),..Default::default()};
    let events=legacy_keyboard_mode_for_peer("Windows",&physical_event(Key::Delete),template.clone(),false);
    assert_eq!(events.len(),1);assert_eq!(events[0].control_key(),ControlKey::CtrlAltDel);
    HELD.store(1<<6,Ordering::SeqCst);
    let events=legacy_keyboard_mode_for_peer("Windows",&physical_event(Key::KeyL),template,false);
    assert_eq!(events.len(),1);assert_eq!(events[0].control_key(),ControlKey::LockScreen);
    HELD.store(0,Ordering::SeqCst);
}
#[test] fn native_legacy_snapshot_uses_current_physical_right_side_state() {
    *PEER.lock().unwrap()="Windows".into();HELD.store(1<<7,Ordering::SeqCst);
    let template=KeyEvent {down:true,mode:KeyboardMode::Legacy.into(),..Default::default()};
    let events=native_modifier_snapshot(&template,"Windows").unwrap();
    assert_eq!(events.len(),1);assert_eq!(events[0].control_key(),ControlKey::RWin);
    HELD.store(0,Ordering::SeqCst);
}

#[cfg(feature="original-requirements")]
include!("native_legacy_original.rs");
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_legacy_peer_must_stay_bound_to_captured_session() {
    *PEER.lock().unwrap()="Linux".into();HELD.store(0,Ordering::SeqCst);
    let template=KeyEvent {mode:KeyboardMode::Legacy.into(),..Default::default()};
    let events=legacy_keyboard_mode_original(&physical_event(Key::Kp1),template);
    assert_eq!(events[0].control_key(),ControlKey::End,"captured target was Windows");
}
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_legacy_released_ui_cache_must_not_reapply_modifier() {
    *PEER.lock().unwrap()="Windows".into();HELD.store(0,Ordering::SeqCst);client::set_cached(4);
    let template=KeyEvent {mode:KeyboardMode::Legacy.into(),..Default::default()};
    let events=legacy_keyboard_mode_original(&physical_event(Key::KeyA),template);
    assert!(!events[0].modifiers.contains(&ControlKey::Shift.into()));
}

trait InvokeUiSession {}
impl InvokeUiSession for () {}
struct Session<T> {id:usize,mouse_outputs:Mutex<Vec<MouseEvent>>,outputs:Mutex<Vec<(KeyEvent,Option<Vec<KeyEvent>>)>>,marker:std::marker::PhantomData<T>}
impl<T> Session<T> {
    fn get_toggle_option(&self,_key:String)->bool {false}
    fn get_scroll_xy(&self,xy:(i32,i32))->(i32,i32) {xy}
    pub fn send_mouse(
        &self,
        mut mask: i32,
        x: i32,
        y: i32,
        alt: bool,
        ctrl: bool,
        shift: bool,
        command: bool,
    ) {
        #[allow(unused_mut)]
        let mut command = command;
        #[cfg(windows)]
        {
            if !command && crate::platform::windows::get_win_key_state() {
                command = true;
            }
        }

        // Compute event type once using MOUSE_TYPE_MASK for reuse
        let event_type = mask & MOUSE_TYPE_MASK;
        let (x, y) = if event_type == MOUSE_TYPE_WHEEL || event_type == MOUSE_TYPE_TRACKPAD {
            self.get_scroll_xy((x, y))
        } else {
            (x, y)
        };

        // Read current physical state; a different session's cached modifier
        // can remain held while its permission-revoked Up is deferred.
        let (alt, ctrl, shift, command) =
            keyboard::client::get_physical_modifiers_state(alt, ctrl, shift, command);
        let is_left = (mask & (MOUSE_BUTTON_LEFT << 3)) > 0;
        let is_right = (mask & (MOUSE_BUTTON_RIGHT << 3)) > 0;
        if is_left ^ is_right {
            let swap_lr = self.get_toggle_option("swap-left-right-mouse".to_string());
            if swap_lr {
                if is_left {
                    mask = (mask & (!(MOUSE_BUTTON_LEFT << 3))) | (MOUSE_BUTTON_RIGHT << 3);
                } else {
                    mask = (mask & (!(MOUSE_BUTTON_RIGHT << 3))) | (MOUSE_BUTTON_LEFT << 3);
                }
            }
        }

        send_mouse(mask, x, y, alt, ctrl, shift, command, self);
        // on macos, ctrl + left button down = right button down, up won't emit, so we need to
        // emit up myself if peer is not macos
        // to-do: how about ctrl + left from win to macos
        if cfg!(target_os = "macos") {
            let buttons = mask >> 3;
            if buttons == MOUSE_BUTTON_LEFT
                && event_type == MOUSE_TYPE_DOWN
                && ctrl
                && self.peer_platform() != "Mac OS"
            {
                self.send_mouse(
                    (MOUSE_BUTTON_LEFT << 3 | MOUSE_TYPE_UP) as _,
                    x,
                    y,
                    alt,
                    ctrl,
                    shift,
                    command,
                );
            }
        }
    }
    fn swap_modifier_mouse(&self, msg: &mut hbb_common::protos::message::MouseEvent) {
        let allow_swap_key = self.get_toggle_option("allow_swap_key".to_string());
        if allow_swap_key {
            msg.modifiers = msg
                .modifiers
                .iter()
                .map(|ck| {
                    let ck = ck.enum_value_or_default();
                    let ck = match ck {
                        ControlKey::Control => ControlKey::Meta,
                        ControlKey::Meta => ControlKey::Control,
                        ControlKey::RControl => ControlKey::RWin,
                        ControlKey::RWin => ControlKey::RControl,
                        _ => ck,
                    };
                    hbb_common::protobuf::EnumOrUnknown::new(ck)
                })
                .collect();
        };
    }

    #[cfg(feature="original-requirements")]
    pub fn send_mouse_r8(
        &self,
        mut mask: i32,
        x: i32,
        y: i32,
        alt: bool,
        ctrl: bool,
        shift: bool,
        command: bool,
    ) {
        #[allow(unused_mut)]
        let mut command = command;
        #[cfg(windows)]
        {
            if !command && crate::platform::windows::get_win_key_state() {
                command = true;
            }
        }

        // Compute event type once using MOUSE_TYPE_MASK for reuse
        let event_type = mask & MOUSE_TYPE_MASK;
        let (x, y) = if event_type == MOUSE_TYPE_WHEEL || event_type == MOUSE_TYPE_TRACKPAD {
            self.get_scroll_xy((x, y))
        } else {
            (x, y)
        };

        // #[cfg(not(any(target_os = "android", target_os = "ios")))]
        let (alt, ctrl, shift, command) =
            keyboard::client::get_modifiers_state(alt, ctrl, shift, command);
        let is_left = (mask & (MOUSE_BUTTON_LEFT << 3)) > 0;
        let is_right = (mask & (MOUSE_BUTTON_RIGHT << 3)) > 0;
        if is_left ^ is_right {
            let swap_lr = self.get_toggle_option("swap-left-right-mouse".to_string());
            if swap_lr {
                if is_left {
                    mask = (mask & (!(MOUSE_BUTTON_LEFT << 3))) | (MOUSE_BUTTON_RIGHT << 3);
                } else {
                    mask = (mask & (!(MOUSE_BUTTON_RIGHT << 3))) | (MOUSE_BUTTON_LEFT << 3);
                }
            }
        }

        send_mouse(mask, x, y, alt, ctrl, shift, command, self);
        // on macos, ctrl + left button down = right button down, up won't emit, so we need to
        // emit up myself if peer is not macos
        // to-do: how about ctrl + left from win to macos
        if cfg!(target_os = "macos") {
            let buttons = mask >> 3;
            if buttons == MOUSE_BUTTON_LEFT
                && event_type == MOUSE_TYPE_DOWN
                && ctrl
                && self.peer_platform() != "Mac OS"
            {
                self.send_mouse(
                    (MOUSE_BUTTON_LEFT << 3 | MOUSE_TYPE_UP) as _,
                    x,
                    y,
                    alt,
                    ctrl,
                    shift,
                    command,
                );
            }
        }
    }
    fn get_keyboard_mode(&self)->String {MODE_READS.fetch_add(1,Ordering::SeqCst);if self.id==0 {"map"}else{"legacy"}.into()}
    fn peer_platform(&self)->String {PEER_READS.fetch_add(1,Ordering::SeqCst);if self.id==0 {"Windows"}else{"Linux"}.into()}
    fn send_key_event(&self,event:&KeyEvent) {self.outputs.lock().unwrap().push((event.clone(),None));}
    fn send_native_key_event(&self,event:&KeyEvent,modifiers:Option<Vec<KeyEvent>>) {self.outputs.lock().unwrap().push((event.clone(),modifiers));if event.down && event.chr()==56 && FLIP_ON_DOWN.load(Ordering::SeqCst) {SELECTED.store(1,Ordering::SeqCst);}}
}
fn captured()->&'static Session<()> {
    static SESSION:std::sync::OnceLock<Session<()>>=std::sync::OnceLock::new();
    SESSION.get_or_init(||Session{id:0,mouse_outputs:Mutex::new(Vec::new()),outputs:Mutex::new(Vec::new()),marker:std::marker::PhantomData})
}
static NATIVE_INPUT_GATE:Mutex<()>=Mutex::new(());
static NATIVE_INPUT_SUSPENDED:std::sync::atomic::AtomicBool=std::sync::atomic::AtomicBool::new(false);
static SELECTED:std::sync::atomic::AtomicUsize=std::sync::atomic::AtomicUsize::new(0);
static SELECTOR_READS:std::sync::atomic::AtomicUsize=std::sync::atomic::AtomicUsize::new(0);
static MODE_READS:std::sync::atomic::AtomicUsize=std::sync::atomic::AtomicUsize::new(0);
static PEER_READS:std::sync::atomic::AtomicUsize=std::sync::atomic::AtomicUsize::new(0);
static FLIP_ON_DOWN:std::sync::atomic::AtomicBool=std::sync::atomic::AtomicBool::new(false);
fn other()->&'static Session<()> {
    static SESSION:std::sync::OnceLock<Session<()>>=std::sync::OnceLock::new();
    SESSION.get_or_init(||Session{id:1,mouse_outputs:Mutex::new(Vec::new()),outputs:Mutex::new(Vec::new()),marker:std::marker::PhantomData})
}
mod flutter {
    pub fn get_cur_session()->Option<&'static super::Session<()>> {
        super::SELECTOR_READS.fetch_add(1,super::Ordering::SeqCst);
        Some(if super::SELECTED.load(super::Ordering::SeqCst)==0 {super::captured()}else{super::other()})
    }
}
// Mock-only lazy mutex adapter supports shipping Rust1.75 (OnceLock:1.70).
struct ReleaseMap;
impl ReleaseMap {
    fn lock(&self)->std::sync::LockResult<std::sync::MutexGuard<'static,std::collections::HashMap<Key,Event>>> {
        static VALUE:std::sync::OnceLock<Mutex<std::collections::HashMap<Key,Event>>>=std::sync::OnceLock::new();
        VALUE.get_or_init(||Mutex::new(std::collections::HashMap::new())).lock()
    }
}
static TO_RELEASE:ReleaseMap=ReleaseMap;
fn get_keyboard_mode_enum(value:&str)->KeyboardMode {match value {"map"=>KeyboardMode::Map,"translate"=>KeyboardMode::Translate,_=>KeyboardMode::Legacy}}
include!("native_cache_selected.rs");
struct ModifierMap;
impl ModifierMap {
    fn lock(&self)->std::sync::LockResult<std::sync::MutexGuard<'static,std::collections::HashMap<Key,bool>>> {
        static VALUE:std::sync::OnceLock<Mutex<std::collections::HashMap<Key,bool>>>=std::sync::OnceLock::new();
        VALUE.get_or_init(||Mutex::new([Key::ShiftLeft,Key::ShiftRight,Key::ControlLeft,Key::ControlRight,Key::Alt,Key::AltGr,Key::MetaLeft,Key::MetaRight].map(|key|(key,false)).into_iter().collect())).lock()
    }
}
static MODIFIERS_STATE:ModifierMap=ModifierMap;
pub(crate) fn clear_source_fixture() {clear_input_source_state();}
include!("native_cleanup_selected.rs");
#[cfg(feature="original-requirements")]
include!("native_cleanup_r2.rs");
#[cfg(feature="original-requirements")]
include!("native_cleanup_r3.rs");
fn cleanup_reentry_case(corrected:bool)->(crate::keyboard_state::KeyboardState,std::collections::BTreeSet<u32>) {
    use crate::keyboard_state::*;
    let mut state=KeyboardState::new(true,0);let mut remote=std::collections::BTreeSet::new();
    for code in [42,56] {
        let mut event=KeyEvent{down:true,mode:KeyboardMode::Map.into(),..Default::default()};event.set_chr(code);
        let input=KeyboardInput{event,origin:KeyboardOrigin::Native,epoch:0,permitted:true,modifiers:None};
        for event in state.prepare_input(input) {remote.insert(event.chr());}
    }
    for event in state.change_source(1) {remote.remove(&event.chr());}
    clear_input_source_state();TO_RELEASE.lock().unwrap().insert(Key::Alt,physical_event(Key::Alt));
    SELECTED.store(0,Ordering::SeqCst);FLIP_ON_DOWN.store(false,Ordering::SeqCst);captured().outputs.lock().unwrap().clear();HELD.store((1<<0)|(1<<4),Ordering::SeqCst);
    if corrected {release_remote_keys("map");}
    #[cfg(feature="original-requirements")]
    if !corrected {release_remote_keys_r2("map");}
    assert_eq!(captured().outputs.lock().unwrap().len(),3,"cleanup control must exercise actual Alt Up/Down/Up");
    for (event,modifiers) in captured().outputs.lock().unwrap().drain(..) {
        let input=KeyboardInput{event,origin:KeyboardOrigin::Native,epoch:1,permitted:true,modifiers};
        for event in state.prepare_input(input) {if event.down {remote.insert(event.chr());}else{remote.remove(&event.chr());}}
    }
    HELD.store(0,Ordering::SeqCst);
    let mut local_up=KeyEvent{mode:KeyboardMode::Map.into(),..Default::default()};local_up.set_chr(42);
    for event in state.prepare_input(KeyboardInput{event:local_up,origin:KeyboardOrigin::Ui,epoch:1,permitted:true,modifiers:None}) {if !event.down {remote.remove(&event.chr());}}
    (state,remote)
}
#[test] fn actual_source_leave_alt_cleanup_cannot_restore_another_modifier_after_barrier() {
    let (_,remote)=cleanup_reentry_case(true);assert!(remote.is_empty(),"Shift must not reappear during synthetic Alt cleanup");
}
#[test] fn actual_synthetic_cleanup_preserves_next_real_native_modifier_restoration() {
    use crate::keyboard_state::*;
    let (mut state,remote)=cleanup_reentry_case(true);assert!(remote.is_empty());
    HELD.store(1,Ordering::SeqCst);captured().outputs.lock().unwrap().clear();
    process_native_event_with_session("map",&physical_event(Key::KeyA),None,captured(),true);
    let mut wire=Vec::new();
    for (event,modifiers) in captured().outputs.lock().unwrap().drain(..) {
        wire.extend(state.prepare_input(KeyboardInput{event,origin:KeyboardOrigin::Native,epoch:1,permitted:true,modifiers}));
    }
    HELD.store(0,Ordering::SeqCst);assert_eq!(wire.iter().map(|e|e.chr()).collect::<Vec<_>>(),[42,30]);
}
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_r2_source_leave_alt_cleanup_must_not_repress_shift() {
    let (_,remote)=cleanup_reentry_case(false);assert!(remote.is_empty(),"R2 synthetic Alt Down restores Shift after source barrier");
}

fn foreground_switch_cleanup(corrected:bool)->Vec<(u32,bool)> {
    SELECTED.store(0,Ordering::SeqCst);SELECTOR_READS.store(0,Ordering::SeqCst);MODE_READS.store(0,Ordering::SeqCst);PEER_READS.store(0,Ordering::SeqCst);FLIP_ON_DOWN.store(true,Ordering::SeqCst);
    captured().outputs.lock().unwrap().clear();other().outputs.lock().unwrap().clear();HELD.store(0,Ordering::SeqCst);
    TO_RELEASE.lock().unwrap().clear();TO_RELEASE.lock().unwrap().insert(Key::Alt,physical_event(Key::Alt));
    if corrected {release_remote_keys("map");}
    #[cfg(feature="original-requirements")]
    if !corrected {release_remote_keys_r3("map");}
    FLIP_ON_DOWN.store(false,Ordering::SeqCst);
    let sent=captured().outputs.lock().unwrap().drain(..).map(|(event,modifiers)|{assert!(modifiers.is_none());(event.chr(),event.down)}).collect();
    SELECTED.store(0,Ordering::SeqCst);sent
}
#[test] fn actual_public_cleanup_batch_keeps_same_target_peer_mode_across_foreground_switch() {
    let sent=foreground_switch_cleanup(true);assert_eq!(sent,[(56,false),(56,true),(56,false)]);
    assert!(other().outputs.lock().unwrap().is_empty(),"cleanup must not enter another session");
    assert_eq!(SELECTOR_READS.load(Ordering::SeqCst),1);assert_eq!(MODE_READS.load(Ordering::SeqCst),1);assert_eq!(PEER_READS.load(Ordering::SeqCst),1);
}
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_r3_cleanup_must_not_split_across_foreground_sessions() {
    let sent=foreground_switch_cleanup(false);assert_eq!(sent,[(56,false),(56,true),(56,false)],"R3 forced Alt Down in A has its Up rerouted to B");
}

#[test] fn actual_suspended_cleanup_cannot_steal_fresh_ui_alt_after_owned_barrier() {
    use crate::keyboard_state::*;
    let mut state=KeyboardState::new(true,0);state.change_source(1);
    let mut down=KeyEvent{down:true,mode:KeyboardMode::Map.into(),..Default::default()};down.set_chr(56);
    let events=state.prepare_input(KeyboardInput{event:down,origin:KeyboardOrigin::Ui,epoch:1,permitted:true,modifiers:None});assert_eq!(events.len(),1);
    captured().outputs.lock().unwrap().clear();SELECTOR_READS.store(0,Ordering::SeqCst);NATIVE_INPUT_SUSPENDED.store(true,Ordering::SeqCst);
    TO_RELEASE.lock().unwrap().insert(Key::Alt,physical_event(Key::Alt));release_remote_keys("map");
    NATIVE_INPUT_SUSPENDED.store(false,Ordering::SeqCst);
    assert!(captured().outputs.lock().unwrap().is_empty());assert_eq!(SELECTOR_READS.load(Ordering::SeqCst),0);assert!(TO_RELEASE.lock().unwrap().contains_key(&Key::Alt));
    TO_RELEASE.lock().unwrap().clear();
    let mut up=KeyEvent{mode:KeyboardMode::Map.into(),..Default::default()};up.set_chr(56);
    let events=state.prepare_input(KeyboardInput{event:up,origin:KeyboardOrigin::Ui,epoch:1,permitted:true,modifiers:None});assert_eq!(events.len(),1);assert!(!events[0].down);
}

#[test] #[cfg(feature="original-requirements")]
fn original_requirement_r5_obsolete_cleanup_must_not_steal_new_ui_alt() {
    use crate::keyboard_state::*;
    let mut state=KeyboardState::new(true,0);state.change_source(1);
    let mut down=KeyEvent{down:true,mode:KeyboardMode::Map.into(),..Default::default()};down.set_chr(56);
    state.prepare_input(KeyboardInput{event:down,origin:KeyboardOrigin::Ui,epoch:1,permitted:true,modifiers:None});
    SELECTED.store(0,Ordering::SeqCst);captured().outputs.lock().unwrap().clear();NATIVE_INPUT_SUSPENDED.store(true,Ordering::SeqCst);
    let mut up=physical_event(Key::Alt);up.event_type=EventType::KeyRelease(Key::Alt);
    client::process_release_events_r5("map",&[up.clone(),physical_event(Key::Alt),up]);NATIVE_INPUT_SUSPENDED.store(false,Ordering::SeqCst);
    let output=captured().outputs.lock().unwrap().drain(..).collect::<Vec<_>>();
    for (event,modifiers) in output {state.prepare_input(KeyboardInput{event,origin:KeyboardOrigin::Native,epoch:1,permitted:true,modifiers});}
    let mut up=KeyEvent{mode:KeyboardMode::Map.into(),..Default::default()};up.set_chr(56);
    let events=state.prepare_input(KeyboardInput{event:up,origin:KeyboardOrigin::Ui,epoch:1,permitted:true,modifiers:None});
    assert_eq!(events.len(),1,"R5 synthetic Alt batch already stole and released the new UI-owned hold");
}

#[test] fn actual_source_boundary_clears_old_cache_before_new_ui_modifier_without_erasing_fresh_state() {
    clear_input_source_state();let event=physical_event(Key::ShiftLeft);update_modifiers_state(&event);assert!(is_long_press(&event));
    crate::source_component::perform_boundary_for_native_fixture();assert!(!is_long_press(&event));
    captured().outputs.lock().unwrap().clear();client::process_event_with_session("map",&event,None,captured());assert_eq!(captured().outputs.lock().unwrap().len(),1);assert!(is_long_press(&event));
    NATIVE_INPUT_SUSPENDED.store(true,Ordering::SeqCst);release_remote_keys("map");NATIVE_INPUT_SUSPENDED.store(false,Ordering::SeqCst);
    assert!(is_long_press(&event));assert!(TO_RELEASE.lock().unwrap().contains_key(&Key::ShiftLeft));clear_input_source_state();
}
fn cross_session_ui_modifier(original:bool,bare_legacy:bool)->Vec<KeyEvent> {
    clear_input_source_state();HELD.store(0,Ordering::SeqCst);captured().outputs.lock().unwrap().clear();other().outputs.lock().unwrap().clear();
    client::process_event_with_session("map",&physical_event(Key::ShiftLeft),None,captured());assert!(is_long_press(&physical_event(Key::ShiftLeft)));
    // A's actual wire owner is revoked; its local Up stays deferred before the
    // Rust UI entrypoint, leaving the original shared cache held.
    let mut a=crate::keyboard_state::KeyboardState::new(true,0);
    let down=captured().outputs.lock().unwrap()[0].0.clone();
    assert_eq!(a.prepare_input(crate::keyboard_state::KeyboardInput{event:down.clone(),origin:crate::keyboard_state::KeyboardOrigin::Ui,epoch:0,permitted:true,modifiers:None}).len(),1);
    assert!(a.set_permission(false).is_empty());let mut up=down;up.down=false;
    assert!(a.prepare_input(crate::keyboard_state::KeyboardInput{event:up,origin:crate::keyboard_state::KeyboardOrigin::Ui,epoch:a.epoch(),permitted:false,modifiers:None}).is_empty());
    let event=physical_event(if bare_legacy {Key::KeyB}else{Key::ShiftLeft});let mode=if bare_legacy {"legacy"}else{"map"};
    if !bare_legacy {HELD.store(1,Ordering::SeqCst);}
    if original {
        #[cfg(feature="original-requirements")]client::process_event_with_session_r6(mode,&event,None,other());
    } else {client::process_event_with_session(mode,&event,None,other());}
    HELD.store(0,Ordering::SeqCst);let events=other().outputs.lock().unwrap().drain(..).map(|(event,_)|event).collect();clear_input_source_state();events
}
#[test] fn actual_ui_b_restores_held_shift_even_when_permission_revoked_a_keeps_global_cache() {
    let events=cross_session_ui_modifier(false,false);assert_eq!(events.len(),1);assert!(events[0].down);
    let mut b=crate::keyboard_state::KeyboardState::new(true,0);
    assert_eq!(b.prepare_input(crate::keyboard_state::KeyboardInput{event:events[0].clone(),origin:crate::keyboard_state::KeyboardOrigin::Ui,epoch:0,permitted:true,modifiers:None}).len(),1);
}
#[test] fn actual_ui_b_legacy_bare_key_does_not_inherit_a_released_shift_cache() {
    let events=cross_session_ui_modifier(false,true);assert_eq!(events.len(),1);assert!(!events[0].modifiers.contains(&ControlKey::Shift.into()));
}
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_r6_ui_b_owned_shift_down_must_not_be_suppressed_by_a_cache() {assert_eq!(cross_session_ui_modifier(true,false).len(),1);}
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_r6_ui_b_bare_key_must_not_inherit_a_cached_shift() {let events=cross_session_ui_modifier(true,true);assert_eq!(events.len(),1);assert!(!events[0].modifiers.contains(&ControlKey::Shift.into()));}
#[test] fn actual_existing_conversion_wrapper_keeps_normal_map_control() {
    clear_input_source_state();let events=event_to_key_events("windows".into(),&physical_event(Key::KeyA),KeyboardMode::Map,None);assert_eq!(events.len(),1);assert_eq!(events[0].chr(),30);clear_input_source_state();
}

#[allow(dead_code)] mod constants {include!("mouse_constants_selected.rs");}
use constants::input;
use input::*;
trait Interface {fn swap_modifier_mouse(&self,event:&mut MouseEvent);fn send(&self,data:crate::client::Data);}
impl<T> Interface for Session<T> {
    fn swap_modifier_mouse(&self,event:&mut MouseEvent) {Session::swap_modifier_mouse(self,event);}
    fn send(&self,data:crate::client::Data) {if let crate::client::Data::Message(msg)=data {if let Some(message::Union::MouseEvent(event))=msg.union {self.mouse_outputs.lock().unwrap().push(event);}}}
}
mod mouse_wire {use super::*;use crate::client::Data;include!("mouse_wire_selected.rs");}
use mouse_wire::send_mouse;

mod keyboard {pub(super) use super::client;}
#[path="mouse_server.rs"] mod mouse_server;
fn actual_mouse_after_denied_other_session(original:bool)->MouseEvent {
    clear_input_source_state();HELD.store(0,Ordering::SeqCst);client::process_event_with_session("map",&physical_event(Key::ShiftLeft),None,captured());assert!(is_long_press(&physical_event(Key::ShiftLeft)));
    let mut a=crate::keyboard_state::KeyboardState::new(true,0);let down=captured().outputs.lock().unwrap().last().unwrap().0.clone();a.prepare_input(crate::keyboard_state::KeyboardInput{event:down,origin:crate::keyboard_state::KeyboardOrigin::Ui,epoch:0,permitted:true,modifiers:None});a.set_permission(false);
    other().mouse_outputs.lock().unwrap().clear();
    if original {#[cfg(feature="original-requirements")]other().send_mouse_r8(MOUSE_BUTTON_LEFT<<3|MOUSE_TYPE_DOWN,10,20,false,false,false,false);}
    else {other().send_mouse(MOUSE_BUTTON_LEFT<<3|MOUSE_TYPE_DOWN,10,20,false,false,false,false);}
    let mouse=other().mouse_outputs.lock().unwrap().pop().unwrap();clear_input_source_state();mouse
}
#[test] fn actual_session_mouse_b_does_not_inherit_denied_a_cached_shift() {assert!(!actual_mouse_after_denied_other_session(false).modifiers.contains(&ControlKey::Shift.into()));}
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_r8_mouse_b_must_not_inherit_denied_a_cached_shift() {assert!(!actual_mouse_after_denied_other_session(true).modifiers.contains(&ControlKey::Shift.into()));}
#[test] fn actual_physical_mouse_fallback_preserves_each_modifier_side_and_caller_flags() {
    for (bit,want) in [(0,(false,false,true,false)),(1,(false,false,true,false)),(2,(false,true,false,false)),(3,(false,true,false,false)),(4,(true,false,false,false)),(5,(true,false,false,false)),(6,(false,false,false,true)),(7,(false,false,false,true))] {HELD.store(1<<bit,Ordering::SeqCst);assert_eq!(client::get_physical_modifiers_state(false,false,false,false),want);}
    HELD.store(0,Ordering::SeqCst);assert_eq!(client::get_physical_modifiers_state(true,true,true,true),(true,true,true,true));
}
#[test] fn actual_native_regrant_first_mouse_click_uses_temporary_server_shift_and_no_stuck_up() {
    use crate::keyboard_state::*;clear_input_source_state();captured().outputs.lock().unwrap().clear();captured().mouse_outputs.lock().unwrap().clear();HELD.store(1,Ordering::SeqCst);
    process_native_event_with_session("map",&physical_event(Key::ShiftLeft),None,captured(),true);
    let (down,modifiers)=captured().outputs.lock().unwrap().pop().unwrap();let mut state=KeyboardState::new(true,0);assert_eq!(state.prepare_input(KeyboardInput{event:down,origin:KeyboardOrigin::Native,epoch:0,permitted:true,modifiers}).len(),1);
    state.set_permission(false);let cleanup=state.set_permission(true);assert_eq!(cleanup.len(),1);assert!(!cleanup[0].down);
    captured().send_mouse(MOUSE_BUTTON_LEFT<<3|MOUSE_TYPE_DOWN,11,12,false,false,false,false);let mouse=captured().mouse_outputs.lock().unwrap().pop().unwrap();assert!(mouse.modifiers.contains(&ControlKey::Shift.into()));
    let mut message=Message::new();message.set_mouse_event(mouse.clone());assert!(state.prepare_pointer(message,state.epoch(),true).is_some());
    mouse_server::assert_temporary_shift_click_all_desktop_decoders(&mouse);
    HELD.store(0,Ordering::SeqCst);let mut up=physical_event(Key::ShiftLeft);up.event_type=EventType::KeyRelease(Key::ShiftLeft);process_native_event_with_session("map",&up,None,captured(),true);
    let (event,modifiers)=captured().outputs.lock().unwrap().pop().unwrap();let epoch=state.epoch();assert!(state.prepare_input(KeyboardInput{event,origin:KeyboardOrigin::Native,epoch,permitted:true,modifiers}).is_empty());clear_input_source_state();
}

#[path="windows_modifiers.rs"] mod windows_modifiers;
