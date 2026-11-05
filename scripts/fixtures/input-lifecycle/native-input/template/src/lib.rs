extern crate self as hbb_common;
include!(concat!(env!("OUT_DIR"), "/protos/mod.rs"));
pub use message as message_proto;
#[path = "../../candidate/src/client/keyboard_state.rs"]
pub mod keyboard_state;

#[cfg(test)]
mod tests {
    use super::{keyboard_state::*, message_proto::*};
    use std::collections::BTreeSet;

    fn key(code: u32, down: bool) -> KeyEvent {
        let mut event = KeyEvent { down, mode: KeyboardMode::Map.into(), ..Default::default() };
        event.set_chr(code); event
    }
    fn input(state: &KeyboardState, origin: KeyboardOrigin, event: KeyEvent) -> KeyboardInput {
        KeyboardInput { event, origin, epoch: state.epoch(), permitted: true, modifiers: None }
    }
    fn forward(state: &mut KeyboardState, origin: KeyboardOrigin, event: KeyEvent) -> Vec<KeyEvent> {
        let event = input(state, origin, event); state.prepare_input(event)
    }
    fn codes(events: &[KeyEvent]) -> Vec<(u32,bool)> {
        events.iter().map(|event| (event.chr(),event.down)).collect()
    }
    fn hold(state: &mut KeyboardState, origin: KeyboardOrigin, code: u32) {
        assert_eq!(codes(&forward(state,origin,key(code,true))),[(code,true)]);
    }
    fn cycle(state: &mut KeyboardState) -> Vec<KeyEvent> {
        assert!(state.set_permission(false).is_empty()); state.set_permission(true)
    }
    fn native(state: &mut KeyboardState, event: KeyEvent, modifiers: Vec<KeyEvent>) -> Vec<KeyEvent> {
        let mut event = input(state,KeyboardOrigin::Native,event);
        event.modifiers=Some(modifiers); state.prepare_input(event)
    }
    #[test] fn revoke_local_up_source_switch_regain_no_dart_boundary_cleans_before_native_key() {
        let mut s=KeyboardState::new(true,0); let mut remote=BTreeSet::new();
        for event in forward(&mut s,KeyboardOrigin::Ui,key(42,true)) { remote.insert(event.chr()); }
        s.set_permission(false);
        // Source2 may retain its physical up until a future Dart boundary.
        let mut denied=input(&s,KeyboardOrigin::Ui,key(42,false)); denied.permitted=false;
        assert!(s.prepare_input(denied).is_empty());
        let mut wire=s.set_permission(true);
        wire.extend(native(&mut s,key(30,true),vec![]));
        assert_eq!(codes(&wire),[(42,false),(30,true)]);
        for event in wire { if event.down { remote.insert(event.chr()); } else { remote.remove(&event.chr()); } }
        assert!(!remote.contains(&42)); assert!(remote.contains(&30));
    }
    #[test] fn old_ui_up_cannot_release_fresh_native_same_wire_key() {
        let mut s=KeyboardState::new(true,0);hold(&mut s,KeyboardOrigin::Ui,42);cycle(&mut s);
        assert_eq!(codes(&native(&mut s,key(30,true),vec![key(42,true)])),[(42,true),(30,true)]);
        assert!(forward(&mut s,KeyboardOrigin::Ui,key(42,false)).is_empty());
        assert_eq!(codes(&forward(&mut s,KeyboardOrigin::Native,key(42,false))),[(42,false)]);
    }
    #[test] fn new_origin_takes_wire_key_old_origin_up_cannot_release_it() {
        let mut s=KeyboardState::new(true,0);hold(&mut s,KeyboardOrigin::Ui,42);hold(&mut s,KeyboardOrigin::Native,42);
        assert!(forward(&mut s,KeyboardOrigin::Ui,key(42,false)).is_empty());
        assert_eq!(codes(&forward(&mut s,KeyboardOrigin::Native,key(42,false))),[(42,false)]);
    }
    #[test] fn new_native_owner_up_releases_without_waiting_for_inactive_ui_source() {
        let mut s=KeyboardState::new(true,0);hold(&mut s,KeyboardOrigin::Ui,42);hold(&mut s,KeyboardOrigin::Native,42);
        assert_eq!(codes(&forward(&mut s,KeyboardOrigin::Native,key(42,false))),[(42,false)]);
        assert!(forward(&mut s,KeyboardOrigin::Ui,key(42,false)).is_empty());assert!(cycle(&mut s).is_empty());
    }
    #[test] fn new_ui_owner_up_releases_without_waiting_for_inactive_native_source() {
        let mut s=KeyboardState::new(true,0);hold(&mut s,KeyboardOrigin::Native,42);hold(&mut s,KeyboardOrigin::Ui,42);
        assert_eq!(codes(&forward(&mut s,KeyboardOrigin::Ui,key(42,false))),[(42,false)]);
        assert!(forward(&mut s,KeyboardOrigin::Native,key(42,false)).is_empty());assert!(cycle(&mut s).is_empty());
    }
    #[test] fn reverse_origin_release_also_preserves_other_owner() {
        let mut s=KeyboardState::new(true,0);hold(&mut s,KeyboardOrigin::Native,42);hold(&mut s,KeyboardOrigin::Ui,42);
        assert!(forward(&mut s,KeyboardOrigin::Native,key(42,false)).is_empty());
        assert_eq!(codes(&forward(&mut s,KeyboardOrigin::Ui,key(42,false))),[(42,false)]);
    }
    #[test] fn repeats_do_not_increase_owner_depth() {
        let mut s=KeyboardState::new(true,0);for _ in 0..100 {hold(&mut s,KeyboardOrigin::Ui,42);}
        assert_eq!(codes(&forward(&mut s,KeyboardOrigin::Ui,key(42,false))),[(42,false)]);
        assert!(forward(&mut s,KeyboardOrigin::Ui,key(42,false)).is_empty());
    }
    #[test] fn no_key_down_or_text_is_forwarded_while_denied() {
        let mut s=KeyboardState::new(true,0);s.set_permission(false);
        for origin in [KeyboardOrigin::Ui,KeyboardOrigin::Native] {
            assert!(forward(&mut s,origin,key(30,true)).is_empty());
            let mut text=KeyEvent::new();text.set_seq("synthetic text".into());
            assert!(forward(&mut s,origin,text).is_empty());
        }
        assert!(s.set_permission(true).is_empty());
    }
    #[test] fn denied_queued_down_cannot_replay_after_regain() {
        let mut s=KeyboardState::new(true,0);s.set_permission(false);
        let mut queued=input(&s,KeyboardOrigin::Native,key(30,true));queued.permitted=false;
        s.set_permission(true);assert!(s.prepare_input(queued).is_empty());
    }
    #[test] fn pre_revoke_queued_down_cannot_replay_after_regain() {
        let mut s=KeyboardState::new(true,0);let queued=input(&s,KeyboardOrigin::Ui,key(30,true));
        cycle(&mut s);assert!(s.prepare_input(queued).is_empty());
    }
    #[test] fn pre_revoke_snapshot_cannot_repress_released_modifier() {
        let mut s=KeyboardState::new(true,0);let mut queued=input(&s,KeyboardOrigin::Native,key(30,true));
        queued.modifiers=Some(vec![key(42,true)]);cycle(&mut s);
        assert!(s.prepare_input(queued).is_empty());
        assert_eq!(codes(&native(&mut s,key(31,true),vec![])),[(31,true)]);
    }
    #[test] fn genuinely_held_left_and_right_modifiers_restore_before_first_native_action() {
        let mut s=KeyboardState::new(true,0);hold(&mut s,KeyboardOrigin::Ui,42);hold(&mut s,KeyboardOrigin::Ui,54);
        assert_eq!(codes(&cycle(&mut s)),[(54,false),(42,false)]);
        assert_eq!(codes(&native(&mut s,key(30,true),vec![key(42,true),key(54,true)])),[(42,true),(54,true),(30,true)]);
    }
    #[test] fn physically_released_modifiers_are_not_recreated() {
        let mut s=KeyboardState::new(true,0);hold(&mut s,KeyboardOrigin::Ui,42);cycle(&mut s);
        assert_eq!(codes(&native(&mut s,key(30,true),vec![])),[(30,true)]);
    }
    #[test] fn native_up_does_not_restore_unrelated_bare_modifiers() {
        let mut s=KeyboardState::new(true,0);cycle(&mut s);
        assert!(forward(&mut s,KeyboardOrigin::Native,key(42,false)).is_empty());
        assert_eq!(codes(&native(&mut s,key(30,true),vec![key(54,true)])),[(54,true),(30,true)]);
    }
    #[test] fn restoration_occurs_once_not_before_every_native_repeat() {
        let mut s=KeyboardState::new(true,0);cycle(&mut s);
        assert_eq!(codes(&native(&mut s,key(30,true),vec![key(42,true)])),[(42,true),(30,true)]);
        assert_eq!(codes(&native(&mut s,key(30,true),vec![key(42,true)])),[(30,true)]);
    }
    #[test] fn wrong_session_snapshot_and_release_cannot_touch_another_session() {
        let mut a=KeyboardState::new(true,0);let mut b=KeyboardState::new(true,0);
        hold(&mut a,KeyboardOrigin::Ui,42);hold(&mut b,KeyboardOrigin::Native,54);
        assert_eq!(codes(&cycle(&mut a)),[(42,false)]);
        assert!(forward(&mut b,KeyboardOrigin::Ui,key(42,false)).is_empty());
        assert_eq!(codes(&cycle(&mut b)),[(54,false)]);
    }
    #[test] fn no_global_release_is_generated_for_unowned_keys() {
        let mut s=KeyboardState::new(true,0);assert!(cycle(&mut s).is_empty());
        for code in 0..255 { assert!(forward(&mut s,KeyboardOrigin::Ui,key(code,false)).is_empty()); }
    }
    #[test] fn retained_release_uses_exact_pre_revoke_wire_remap() {
        let mut s=KeyboardState::new(true,0);let mut mapped=key(125,true);mapped.modifiers.push(ControlKey::Shift.into());
        forward(&mut s,KeyboardOrigin::Ui,mapped);let releases=cycle(&mut s);
        assert_eq!(codes(&releases),[(125,false)]);assert!(releases[0].modifiers.is_empty());
    }
    #[test] fn distinct_keyboard_modes_have_independent_exact_release_templates() {
        let mut s=KeyboardState::new(true,0);
        let map=key(42,true);let mut legacy=KeyEvent {down:true,mode:KeyboardMode::Legacy.into(),..Default::default()};
        legacy.set_control_key(ControlKey::Shift);
        forward(&mut s,KeyboardOrigin::Ui,map);forward(&mut s,KeyboardOrigin::Ui,legacy);
        let releases=cycle(&mut s);assert_eq!(releases.len(),2);
        assert_eq!(releases[0].control_key(),ControlKey::Shift);assert_eq!(releases[1].chr(),42);
    }
    #[test] fn translate_vk_down_and_up_match_despite_text_low_word() {
        let mut s=KeyboardState::new(true,0);let mut event=KeyEvent {down:true,mode:KeyboardMode::Translate.into(),..Default::default()};
        event.set_win2win_hotkey(0x00410061);forward(&mut s,KeyboardOrigin::Native,event.clone());
        event.down=false;event.set_win2win_hotkey(0x00410000);
        assert_eq!(forward(&mut s,KeyboardOrigin::Native,event).len(),1);assert!(cycle(&mut s).is_empty());
    }
    #[test] fn translate_vk_suspended_release_strips_text_but_preserves_physical_vk() {
        let mut s=KeyboardState::new(true,0);let mut event=KeyEvent {down:true,mode:KeyboardMode::Translate.into(),..Default::default()};
        event.set_win2win_hotkey(0x00410061);forward(&mut s,KeyboardOrigin::Native,event);
        let events=cycle(&mut s);assert_eq!(events[0].win2win_hotkey(),0x00410000);assert!(!events[0].down);
    }
    #[test] fn atomic_press_text_unicode_are_never_replayed_as_held_keys() {
        let mut s=KeyboardState::new(true,0);let mut press=key(30,true);press.press=true;
        forward(&mut s,KeyboardOrigin::Ui,press);
        let mut seq=KeyEvent {down:true,..Default::default()};seq.set_seq("synthetic".into());forward(&mut s,KeyboardOrigin::Ui,seq);
        let mut uni=KeyEvent {down:true,..Default::default()};uni.set_unicode(0x20ac);forward(&mut s,KeyboardOrigin::Ui,uni);
        assert!(cycle(&mut s).is_empty());
    }
    #[test] fn disconnect_destroys_only_its_ledger_no_cross_connection_replay() {
        let mut a=KeyboardState::new(true,0);hold(&mut a,KeyboardOrigin::Ui,42);a.set_permission(false);drop(a);
        let mut fresh=KeyboardState::new(true,1);assert!(fresh.set_permission(true).is_empty());
        assert_eq!(codes(&native(&mut fresh,key(30,true),vec![])),[(30,true)]);
    }
    #[test] fn repeated_denial_does_not_rotate_epoch_or_lose_releases() {
        let mut s=KeyboardState::new(true,9);hold(&mut s,KeyboardOrigin::Native,42);s.set_permission(false);
        assert_eq!(s.epoch(),10);for _ in 0..20 {assert!(s.set_permission(false).is_empty());}
        assert_eq!(s.epoch(),10);assert_eq!(codes(&s.set_permission(true)),[(42,false)]);
        assert!(s.set_permission(true).is_empty());
    }
    #[test] fn epoch_wrap_is_defined_and_stale_neighbor_still_rejected() {
        let mut s=KeyboardState::new(true,usize::MAX);let queued=input(&s,KeyboardOrigin::Native,key(30,true));
        cycle(&mut s);assert_eq!(s.epoch(),0);assert!(s.prepare_input(queued).is_empty());
    }
    #[test] fn permitted_source_handoff_releases_then_restores_real_native_modifiers() {
        let mut s=KeyboardState::new(true,0);hold(&mut s,KeyboardOrigin::Ui,42);
        assert_eq!(codes(&s.change_source(1)),[(42,false)]);
        assert_eq!(codes(&native(&mut s,key(30,true),vec![key(42,true)])),[(42,true),(30,true)]);
    }
    #[test] fn denied_source_handoff_defers_exact_owned_releases_until_real_grant() {
        let mut s=KeyboardState::new(true,0);hold(&mut s,KeyboardOrigin::Ui,42);s.set_permission(false);
        assert!(s.change_source(2).is_empty());assert!(!s.permits(2,true));
        assert_eq!(codes(&s.set_permission(true)),[(42,false)]);
        assert_eq!(codes(&native(&mut s,key(30,true),vec![])),[(30,true)]);
    }
    #[test] fn source_handoff_releases_snapshot_only_native_hold_absent_from_to_release() {
        let mut s=KeyboardState::new(true,0);cycle(&mut s);
        native(&mut s,key(30,true),vec![key(42,true)]);forward(&mut s,KeyboardOrigin::Native,key(30,false));
        assert_eq!(codes(&s.change_source(2)),[(42,false)]);
        assert!(forward(&mut s,KeyboardOrigin::Native,key(42,false)).is_empty());
    }
    #[test] fn source_handoff_rejects_old_epoch_events_without_toggling_permission() {
        let mut s=KeyboardState::new(true,0);let queued=input(&s,KeyboardOrigin::Ui,key(42,true));
        s.change_source(1);assert!(s.permits(1,true));assert!(s.prepare_input(queued).is_empty());
    }
    #[test] fn superseded_queued_source_barrier_cannot_roll_back_current_permission_epoch() {
        let mut s=KeyboardState::new(true,0);hold(&mut s,KeyboardOrigin::Ui,42);s.set_permission(false);s.adopt_epoch(2);
        let queued_source_epoch=1;let shared_epoch=2;
        if queued_source_epoch==shared_epoch {s.change_source(queued_source_epoch);}
        assert_eq!(s.epoch(),2);assert!(!s.permits(2,true));assert_eq!(codes(&s.set_permission(true)),[(42,false)]);
    }
    #[test] fn rapid_source_changes_accept_only_current_shared_barrier_and_release_once() {
        let mut s=KeyboardState::new(true,0);hold(&mut s,KeyboardOrigin::Ui,42);let mut events=Vec::new();
        for queued in [1,2] {if queued==2 {events.extend(s.change_source(queued));}}
        assert_eq!(codes(&events),[(42,false)]);assert_eq!(s.epoch(),2);
    }
    #[test] fn renewed_grant_then_already_queued_same_epoch_source_barrier_is_safe() {
        let mut s=KeyboardState::new(true,0);hold(&mut s,KeyboardOrigin::Ui,42);s.set_permission(false);
        assert_eq!(codes(&s.set_permission(true)),[(42,false)]);s.adopt_epoch(2);
        assert!(s.change_source(2).is_empty());assert_eq!(codes(&native(&mut s,key(30,true),vec![key(42,true)])),[(42,true),(30,true)]);
    }
    #[test] fn source_handoff_never_releases_foreign_session_keys_or_own_mouse_drag() {
        let mut a=KeyboardState::new(true,0);let mut b=KeyboardState::new(true,0);hold(&mut b,KeyboardOrigin::Native,54);
        a.prepare_pointer(mouse(1,1),0,true);assert!(a.change_source(1).is_empty());assert!(a.take_button_releases().is_empty());
        assert!(a.prepare_pointer(mouse(1,2),1,true).is_some());assert_eq!(codes(&cycle(&mut b)),[(54,false)]);
    }
    fn mouse(button:i32,kind:i32)->Message {
        let mut message=Message::new();message.set_mouse_event(MouseEvent {mask:(button<<3)|kind,x:731,y:514,modifiers:vec![ControlKey::Shift.into()],..Default::default()});message
    }
    #[test] fn only_exact_owned_mouse_buttons_release_on_grant_without_cursor_warp() {
        let mut s=KeyboardState::new(true,0);
        for button in [1,2,4,8,16] {assert!(s.prepare_pointer(mouse(button,1),0,true).is_some());}
        cycle(&mut s);let releases=s.take_button_releases();
        assert_eq!(releases.iter().map(|e|e.mask>>3).collect::<Vec<_>>(),[16,8,4,2,1]);
        assert!(releases.iter().all(|e|e.mask&7==2&&e.x==0&&e.y==0&&e.modifiers.is_empty()));
        assert!(s.take_button_releases().is_empty());
    }
    #[test] fn mouse_repeat_down_does_not_create_extra_release_ownership() {
        let mut s=KeyboardState::new(true,0);for _ in 0..10 {s.prepare_pointer(mouse(1,1),0,true);}
        assert!(s.prepare_pointer(mouse(1,2),0,true).is_some());cycle(&mut s);assert!(s.take_button_releases().is_empty());
    }
    #[test] fn mouse_denied_up_is_retained_for_owned_regrant_cleanup() {
        let mut s=KeyboardState::new(true,0);s.prepare_pointer(mouse(1,1),0,true);s.set_permission(false);
        let epoch=s.epoch();assert!(s.prepare_pointer(mouse(1,2),epoch,false).is_none());
        s.set_permission(true);assert_eq!(s.take_button_releases().len(),1);
        assert!(s.prepare_pointer(mouse(1,2),epoch,true).is_none());
    }
    #[test] fn stale_and_denied_mouse_click_cannot_replay_after_grant() {
        let mut s=KeyboardState::new(true,0);cycle(&mut s);
        assert!(s.prepare_pointer(mouse(1,1),0,true).is_none());
        assert!(s.prepare_pointer(mouse(1,1),s.epoch(),false).is_none());
        cycle(&mut s);assert!(s.take_button_releases().is_empty());
    }
    #[test] fn foreign_session_mouse_up_does_not_release_another_session_button() {
        let mut a=KeyboardState::new(true,0);let mut b=KeyboardState::new(true,0);
        a.prepare_pointer(mouse(1,1),0,true);assert!(b.prepare_pointer(mouse(1,2),0,true).is_none());
        cycle(&mut b);assert!(b.take_button_releases().is_empty());cycle(&mut a);assert_eq!(a.take_button_releases().len(),1);
    }
    #[test] fn wheel_motion_and_touch_have_no_synthetic_button_cleanup() {
        let mut s=KeyboardState::new(true,0);s.prepare_pointer(mouse(0,3),0,true);s.prepare_pointer(mouse(0,0),0,true);
        let mut message=Message::new();message.set_pointer_device_event(PointerDeviceEvent::new());
        assert!(s.prepare_pointer(message,0,true).is_some());cycle(&mut s);assert!(s.take_button_releases().is_empty());
    }
    #[test] fn command_control_keys_never_become_owned_replayed_cleanup() {
        let mut s=KeyboardState::new(true,0);
        for key in [ControlKey::CtrlAltDel,ControlKey::LockScreen] {
            let mut event=KeyEvent {down:true,mode:KeyboardMode::Legacy.into(),..Default::default()};event.set_control_key(key);
            assert_eq!(forward(&mut s,KeyboardOrigin::Native,event).len(),1);
        }
        assert!(cycle(&mut s).is_empty());assert!(s.change_source(2).is_empty());
    }
    #[test] fn failed_restore_write_caller_never_publishes_grant() {
        let mut s=KeyboardState::new(true,0);hold(&mut s,KeyboardOrigin::Ui,42);s.set_permission(false);
        let mut published=false;
        let write_result: Result<(),&str>=s.set_permission(true).into_iter().try_for_each(|_|Err("mock transport closed"));
        if write_result.is_ok() { published=true; }
        assert!(!published);
    }
}
#[cfg(test)]
mod admission;
#[cfg(test)]
pub(crate) use admission::keyboard;
#[cfg(all(test,target_os="macos"))]
mod native_conversion;
#[cfg(all(test,target_os="macos"))]
pub(crate) use native_conversion::{rdev,keycodes,macos};
#[cfg(all(test,target_os="macos"))]
pub(crate) use rdev::{Key,KeyCode};

#[cfg(all(test,target_os="macos"))]
pub(crate) use keycodes::macos_virtual_keycodes::CGKeyCode;

#[cfg(test)]
pub(crate) mod client {
    pub(crate) use crate::keyboard_state;
    #[derive(Clone)]
    pub enum Data {
        Message(crate::message_proto::Message),
        KeyboardInput(keyboard_state::KeyboardInput),
        PointerInput((crate::message_proto::Message,usize,bool)),
        KeyboardSourceChanged(usize),
    }
}
#[cfg(test)]
mod session_component;

#[cfg(test)]
pub fn is_control_key(evt:&message_proto::KeyEvent,key:&message_proto::ControlKey)->bool {
    matches!(&evt.union,Some(message_proto::key_event::Union::ControlKey(value)) if value.enum_value_or_default()==*key)
}
#[cfg(test)]
mod source_component;
#[cfg(test)]
mod flutter {
    pub(crate) fn get_cur_session()->Option<crate::source_component::SourceSession> {sessions::get_sessions().first().cloned()}
    pub mod sessions {
        pub(crate) fn get_sessions()->Vec<crate::source_component::SourceSession> {crate::source_component::get_sessions()}
    }
}
#[cfg(test)]
mod flutter_ffi {
    pub fn session_enter_or_leave(id:usize,enter:bool) {crate::source_component::enter_leave(id,enter);}
}
#[cfg(test)]
mod platform {
    pub fn block_input(_on:bool)->(bool,String) {(true,String::new())}
    pub mod windows {pub fn try_change_desktop() {}}
    #[cfg(target_os="macos")]
    pub mod macos {
        pub fn is_can_input_monitoring(_request:bool)->bool {crate::source_component::MONITOR_ALLOWED.load(std::sync::atomic::Ordering::SeqCst)}
    }
}

#[cfg(all(test,feature="original-requirements"))]
mod original_transport;

pub use protobuf;
pub mod protos {pub use crate::message_proto as message;}
#[cfg(all(test,target_os="macos"))]
mod modifier_swap;

#[cfg(test)]
mod scale_owned;

#[cfg(test)]mod scale_balanced;
#[cfg(test)]mod mouse_windows_physical;

#[cfg(test)]mod mouse_buttons;

#[cfg(test)]pub mod input_service {pub fn release_all_owned_mouse_buttons(){crate::mouse_buttons::release_all_owned_mouse_buttons();}}
