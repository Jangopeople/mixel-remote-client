fn map_keyboard_mode(evt: &KeyEvent) {
    #[cfg(all())]
    crate::platform::windows::try_change_desktop();

    // Wayland
    #[cfg(any())]
    if !crate::platform::linux::is_x11() {
        wayland_send_raw_key(evt.chr() as u16, evt.down);
        return;
    }

    sim_rdev_rawkey_position(evt.chr() as _, evt.down);
}

fn translate_process_code(code: u32, down: bool) {
    crate::platform::windows::try_change_desktop();
    match code >> 16 {
        0 => sim_rdev_rawkey_position(code as _, down),
        vk_code => sim_rdev_rawkey_virtual(vk_code, down),
    };
}

fn simulate_win2win_hotkey(code: u32, down: bool) {
    let unicode: u16 = (code & 0x0000FFFF) as u16;
    if down {
        if rdev::simulate_key_unicode(unicode, false).is_ok() {
            return;
        }
    }

    let keycode: u16 = ((code >> 16) & 0x0000FFFF) as u16;
    let scan = rdev::vk_to_scancode(keycode as _);
    allow_err!(rdev::simulate_code(None, Some(scan), down));
}

fn process_control_key(en: &mut Enigo, ck: &EnumOrUnknown<ControlKey>, down: bool) {
    if let Some(key) = control_key_value_to_key(ck.value()) {
        if down {
            en.key_down(key).ok();
        } else {
            en.key_up(key);
        }
    }
}
