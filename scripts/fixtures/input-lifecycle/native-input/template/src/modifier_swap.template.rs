//! Actual Session modifier mapping methods; toggle and peer reads are the only
//! Session facades. Native key-code tables, protobuf and wire ledger are real.
use crate::{message_proto::*,keyboard_state::*,rdev};
use std::collections::BTreeSet;
struct Session {toggle:bool,peer:&'static str}
impl Session {
    fn get_toggle_option(&self,key:String)->bool {assert_eq!(key,"allow_swap_key");self.toggle}
    fn peer_platform(&self)->String {self.peer.into()}
    include!("modifier_swap_selected.rs");
    #[cfg(feature="original-requirements")]
    include!("modifier_swap_original.rs");
}
fn wire(state:&mut KeyboardState,event:KeyEvent)->Vec<KeyEvent> {
    state.prepare_input(KeyboardInput {event,origin:KeyboardOrigin::Native,epoch:0,permitted:true,modifiers:None})
}
fn key(chr:u32,down:bool)->KeyEvent {let mut event=KeyEvent{down,mode:KeyboardMode::Map.into(),..Default::default()};event.set_chr(chr);event}
fn swapped_sequence(original:bool) {
    for (peer,control,meta) in [("windows",[29,57373],[57435,57436]),("linux",[37,105],[133,134]),("macos",[59,62],[55,54])] {
        for (source,target) in [(control,meta),(meta,control)] {
            let session=Session{toggle:true,peer};let mut state=KeyboardState::new(true,0);let mut held=BTreeSet::new();
            for (code,down) in [(source[0],true),(source[1],true),(source[0],false),(source[1],false)] {
                let mut event=key(code,down);
                if original {
                    #[cfg(feature="original-requirements")]
                    session.swap_modifier_key_original(&mut event);
                } else {session.swap_modifier_key(&mut event);}
                for e in wire(&mut state,event) {if e.down {held.insert(e.chr());}else{held.remove(&e.chr());}}
                if code==source[0]&&!down {assert!(held.contains(&target[1]),"{peer}: first physical Up must preserve opposite held side");}
            }
            assert!(held.is_empty());
        }
    }
}
#[test] fn actual_swap_preserves_dual_physical_side_wire_ownership_for_all_desktop_map_peers() {swapped_sequence(false);}
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_swap_must_not_collapse_two_physical_modifier_holds() {swapped_sequence(true);}
#[test] fn actual_control_key_and_mouse_modifier_swaps_preserve_both_sides() {
    let s=Session{toggle:true,peer:"windows"};
    for (source,target) in [(ControlKey::Control,ControlKey::Meta),(ControlKey::RControl,ControlKey::RWin),(ControlKey::Meta,ControlKey::Control),(ControlKey::RWin,ControlKey::RControl)] {
        let mut event=KeyEvent{mode:KeyboardMode::Legacy.into(),..Default::default()};event.set_control_key(source);event.modifiers=vec![source.into()];
        s.swap_modifier_key(&mut event);assert_eq!(event.control_key(),target);assert_eq!(event.modifiers,vec![target.into()]);
    }
    for (source,target) in [([ControlKey::Control,ControlKey::RControl],[ControlKey::Meta,ControlKey::RWin]),([ControlKey::Meta,ControlKey::RWin],[ControlKey::Control,ControlKey::RControl])] {
        let mut state=KeyboardState::new(true,0);let mut held=Vec::new();
        for (index,down) in [(0,true),(1,true),(0,false),(1,false)] {
            let mut event=KeyEvent{down,mode:KeyboardMode::Legacy.into(),..Default::default()};event.set_control_key(source[index]);s.swap_modifier_key(&mut event);
            for e in wire(&mut state,event) {if e.down {held.push(e.control_key());}else{held.retain(|k|*k!=e.control_key());}}
            if index==0&&!down {assert_eq!(held,vec![target[1]]);}
        }
        assert!(held.is_empty());
    }
    let mut mouse=MouseEvent {modifiers:vec![ControlKey::Control.into(),ControlKey::RControl.into(),ControlKey::Meta.into(),ControlKey::RWin.into()],..Default::default()};
    s.swap_modifier_mouse(&mut mouse);assert_eq!(mouse.modifiers,vec![ControlKey::Meta.into(),ControlKey::RWin.into(),ControlKey::Control.into(),ControlKey::RControl.into()]);
}
#[test] fn actual_swap_disabled_toggle_preserves_key_and_mouse_defaults() {
    let s=Session{toggle:false,peer:"windows"};let mut event=key(57373,true);event.modifiers=vec![ControlKey::RControl.into()];let before=event.clone();s.swap_modifier_key(&mut event);assert_eq!(event,before);
    let mut mouse=MouseEvent {modifiers:vec![ControlKey::RControl.into(),ControlKey::RWin.into()],..Default::default()};let before=mouse.clone();s.swap_modifier_mouse(&mut mouse);assert_eq!(mouse,before);
}
