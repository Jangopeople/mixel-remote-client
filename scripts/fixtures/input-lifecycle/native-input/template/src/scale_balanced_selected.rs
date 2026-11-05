fn handle_scale(scale: i32) {
    // A scroll update owns Control only for this synchronous operation. It must
    // not rely on a later Scale0, permission grant, or connection Drop.
    if scale == 0 { return; }
    let mut en = ENIGO.lock().unwrap();
    let control_held = get_modifier_state(Key::Control, &mut en)
        || unsafe {
            use winapi::um::winuser::{GetAsyncKeyState, VK_LCONTROL, VK_RCONTROL};
            GetAsyncKeyState(VK_LCONTROL) < 0 || GetAsyncKeyState(VK_RCONTROL) < 0
        }
        || {
            // Injection state can be published to Windows after our synchronous
            // key ledger. Preserve either side already owned by remote input.
            let keys = KEYS_DOWN.lock().unwrap();
            keys.keys().any(|key| match key {
                KeysDown::EnigoKey(value) => *value == ControlKey::Control.value() as u64 || *value == ControlKey::RControl.value() as u64,
                KeysDown::RdevKey(RawKey::ScanCode(code)) => matches!(*code, 0x1D | 0xE01D),
                KeysDown::RdevKey(RawKey::WinVirtualKeycode(code)) => matches!(*code, 0x11 | 0xA2 | 0xA3),
                _ => false,
            })
        };
    if !control_held && en.key_down(Key::Control).is_err() { return; }
    en.mouse_scroll_y(scale);
    if !control_held { en.key_up(Key::Control); }
}
fn get_modifier_state(key: Key, en: &mut Enigo) -> bool {
    // https://github.com/rustdesk/rustdesk/issues/332
    // on Linux, if RightAlt is down, RightAlt status is false, Alt status is true
    // but on Windows, both are true
    let x = en.get_key_state(key.clone());
    match key {
        Key::Shift => x || en.get_key_state(Key::RightShift),
        Key::Control => x || en.get_key_state(Key::RightControl),
        Key::Alt => x || en.get_key_state(Key::RightAlt),
        Key::Meta => x || en.get_key_state(Key::RWin),
        Key::RightShift => x || en.get_key_state(Key::Shift),
        Key::RightControl => x || en.get_key_state(Key::Control),
        Key::RightAlt => x || en.get_key_state(Key::Alt),
        Key::RWin => x || en.get_key_state(Key::Meta),
        _ => x,
    }
}
