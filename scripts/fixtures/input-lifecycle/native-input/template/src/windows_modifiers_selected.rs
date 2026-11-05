pub fn map_keyboard_mode(_peer: &str, event: &Event, key_event: KeyEvent) -> Vec<KeyEvent> {
    _map_keyboard_mode(_peer, event, key_event)
        .map(|e| vec![e])
        .unwrap_or_default()
}

fn try_fill_unicode(_peer: &str, event: &Event, key_event: &KeyEvent, events: &mut Vec<KeyEvent>) {
    match &event.unicode {
        Some(unicode_info) => {
            if let Some(name) = &unicode_info.name {
                if name.len() > 0 {
                    let mut evt = key_event.clone();
                    evt.set_seq(name.to_string());
                    evt.down = true;
                    events.push(evt);
                }
            }
        }
        None =>
        {
            #[cfg(all())]
            if _peer == OS_LOWER_LINUX {
                if is_hot_key_modifiers_down() && unsafe { !IS_0X021D_DOWN } {
                    if let Some(chr) = get_char_from_vk(event.platform_code as u32) {
                        let mut evt = key_event.clone();
                        evt.set_seq(chr.to_string());
                        evt.down = true;
                        events.push(evt);
                    }
                }
            }
        }
    }
}

fn try_fill_win2win_hotkey(
    peer: &str,
    event: &Event,
    key_event: &KeyEvent,
    events: &mut Vec<KeyEvent>,
) {
    if peer == OS_LOWER_WINDOWS && is_hot_key_modifiers_down() && unsafe { !IS_0X021D_DOWN } {
        let mut down = false;
        let win2win_hotkey = match event.event_type {
            EventType::KeyPress(..) => {
                down = true;
                if let Some(unicode) = get_unicode_from_vk(event.platform_code as u32) {
                    Some((unicode as u32 & 0x0000FFFF) | (event.platform_code << 16))
                } else {
                    None
                }
            }
            EventType::KeyRelease(..) => Some(event.platform_code << 16),
            _ => None,
        };
        if let Some(code) = win2win_hotkey {
            let mut evt = key_event.clone();
            evt.set_win2win_hotkey(code);
            evt.down = down;
            events.push(evt);
        }
    }
}

fn is_hot_key_modifiers_down() -> bool {
    if rdev::get_modifier(Key::ControlLeft) || rdev::get_modifier(Key::ControlRight) {
        return true;
    }
    if rdev::get_modifier(Key::Alt) || rdev::get_modifier(Key::AltGr) {
        return true;
    }
    if rdev::get_modifier(Key::MetaLeft) || rdev::get_modifier(Key::MetaRight) {
        return true;
    }
    return false;
}

fn is_altgr(event: &Event) -> bool {
    #[cfg(any())]
    if event.platform_code == 0xFE03 {
        true
    } else {
        false
    }

    #[cfg(all())]
    if unsafe { IS_0X021D_DOWN } && event.position_code == 0xE038 {
        true
    } else {
        false
    }
}

fn is_press(event: &Event) -> bool {
    matches!(event.event_type, EventType::KeyPress(_))
}

pub fn is_modifier(key: &rdev::Key) -> bool {
    matches!(
        key,
        Key::ShiftLeft
            | Key::ShiftRight
            | Key::ControlLeft
            | Key::ControlRight
            | Key::MetaLeft
            | Key::MetaRight
            | Key::Alt
            | Key::AltGr
    )
}

pub fn translate_keyboard_mode(peer: &str, event: &Event, key_event: KeyEvent) -> Vec<KeyEvent> {
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
    if peer == OS_LOWER_WINDOWS && matches!(event.event_type,
        EventType::KeyPress(key) | EventType::KeyRelease(key) if is_modifier(&key)) {
        // ToUnicode has no text for modifier Down, while the hotkey Up path
        // emits VK. Keep the same scan identity for both edges and remapping.
        return map_keyboard_mode(peer, event, key_event);
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

fn _map_keyboard_mode(_peer:&str,event:&Event,mut key_event:KeyEvent)->Option<KeyEvent> {assert_eq!(_peer,OS_LOWER_WINDOWS);
    match event.event_type {
        EventType::KeyPress(..) => {
            key_event.down = true;
        }
        EventType::KeyRelease(..) => {
            key_event.down = false;
        }
        _ => return None,
    };


let keycode={
            // https://github.com/rustdesk/rustdesk/issues/1371
            // Filter scancodes that are greater than 255 and the height word is not 0xE0.
            if event.position_code > 255 && (event.position_code >> 8) != 0xE0 {
                return None;
            }
            event.position_code
        };
    key_event.set_chr(keycode as _);
    Some(key_event)
}
