
pub(crate) fn windows_swap_fixture(event:&mut KeyEvent,enabled:bool) {Session{toggle:enabled,peer:"windows"}.swap_modifier_key(event);}
#[test]fn actual_postswap_right_and_left_meta_controls_remain_owned_after_scale_end() {
    for key in [rdev::Key::MetaLeft,rdev::Key::MetaRight,rdev::Key::ControlLeft,rdev::Key::ControlRight] {
        let mut event=KeyEvent{down:true,mode:KeyboardMode::Map.into(),..Default::default()};event.set_chr(rdev::win_scancode_from_key(key).unwrap());windows_swap_fixture(&mut event,true);
        if matches!(key,rdev::Key::MetaLeft|rdev::Key::MetaRight) {crate::scale_owned::check_owned_control_after_normal_scale(event,KeyboardOrigin::Ui);} else {assert!(matches!(event.chr(),0xE05B|0xE05C));}
    }
}
