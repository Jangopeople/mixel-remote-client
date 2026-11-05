pub fn translate_keyboard_mode_r9(peer: &str, event: &Event, key_event: KeyEvent) -> Vec<KeyEvent> {
    let mut events: Vec<KeyEvent> = Vec::new();

    if let Some(unicode_info) = &event.unicode {
        if unicode_info.is_dead {
            #[cfg(any())]
            if peer != OS_LOWER_MACOS && unsafe { IS_LEFT_OPTION_DOWN } {
                // try clear dead key state
                // rdev::clear_dead_key_state();
            } else {
                return events;
            }
            #[cfg(all())]
            return events;
        }
    }

    #[cfg(all())]
    if is_numpad_key(&event) {
        events.append(&mut map_keyboard_mode(peer, event, key_event));
        return events;
    }

    #[cfg(any())]
    // ignore right option key
    if event.platform_code == rdev::kVK_RightOption as u32 {
        return events;
    }

    #[cfg(all())]
    if is_altgr(event) {
        return events;
    }

    #[cfg(all())]
    if event.position_code == 0x021D {
        return events;
    }

    #[cfg(all())]
    try_fill_win2win_hotkey(peer, event, &key_event, &mut events);

    #[cfg(all())]
    if events.is_empty() && is_press(event) {
        try_fill_unicode(peer, event, &key_event, &mut events);
    }

    // If AltGr is down, no need to send events other than unicode.
    #[cfg(all())]
    unsafe {
        if IS_0X021D_DOWN {
            return events;
        }
    }

    #[cfg(any())]
    if !unsafe { IS_LEFT_OPTION_DOWN } {
        try_fill_unicode(peer, event, &key_event, &mut events);
    }

    if events.is_empty() {
        events.append(&mut map_keyboard_mode(peer, event, key_event));
    }
    events
}
