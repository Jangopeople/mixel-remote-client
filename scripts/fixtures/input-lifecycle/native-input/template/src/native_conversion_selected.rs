pub fn is_numpad_rdev_key(key: &rdev::Key) -> bool {
    matches!(
        key,
        Key::Kp0
            | Key::Kp1
            | Key::Kp2
            | Key::Kp3
            | Key::Kp4
            | Key::Kp5
            | Key::Kp6
            | Key::Kp7
            | Key::Kp8
            | Key::Kp9
            | Key::KpMinus
            | Key::KpMultiply
            | Key::KpDivide
            | Key::KpPlus
            | Key::KpDecimal
    )
}

fn is_numpad_key(event: &Event) -> bool {
    matches!(event.event_type, EventType::KeyPress(key) | EventType::KeyRelease(key) if is_numpad_rdev_key(&key))
}

pub fn map_keyboard_mode(_peer: &str, event: &Event, key_event: KeyEvent) -> Vec<KeyEvent> {
    _map_keyboard_mode(_peer, event, key_event)
        .map(|e| vec![e])
        .unwrap_or_default()
}

fn _map_keyboard_mode(_peer: &str, event: &Event, mut key_event: KeyEvent) -> Option<KeyEvent> {
    match event.event_type {
        EventType::KeyPress(..) => {
            key_event.down = true;
        }
        EventType::KeyRelease(..) => {
            key_event.down = false;
        }
        _ => return None,
    };

    #[cfg(target_os = "windows")]
    let keycode = match _peer {
        OS_LOWER_WINDOWS => {
            // https://github.com/rustdesk/rustdesk/issues/1371
            // Filter scancodes that are greater than 255 and the height word is not 0xE0.
            if event.position_code > 255 && (event.position_code >> 8) != 0xE0 {
                return None;
            }
            event.position_code
        }
        OS_LOWER_MACOS => {
            if hbb_common::config::LocalConfig::get_kb_layout_type() == "ISO" {
                rdev::win_scancode_to_macos_iso_code(event.position_code)?
            } else {
                rdev::win_scancode_to_macos_code(event.position_code)?
            }
        }
        OS_LOWER_ANDROID => rdev::win_scancode_to_android_key_code(event.position_code)?,
        _ => rdev::win_scancode_to_linux_code(event.position_code)?,
    };
    #[cfg(target_os = "macos")]
    let keycode = match _peer {
        OS_LOWER_WINDOWS => rdev::macos_code_to_win_scancode(event.platform_code as _)?,
        OS_LOWER_MACOS => event.platform_code as _,
        OS_LOWER_ANDROID => rdev::macos_code_to_android_key_code(event.platform_code as _)?,
        _ => rdev::macos_code_to_linux_code(event.platform_code as _)?,
    };
    #[cfg(target_os = "linux")]
    let keycode = match _peer {
        OS_LOWER_WINDOWS => rdev::linux_code_to_win_scancode(event.position_code as _)?,
        OS_LOWER_MACOS => {
            if hbb_common::config::LocalConfig::get_kb_layout_type() == "ISO" {
                rdev::linux_code_to_macos_iso_code(event.position_code as _)?
            } else {
                rdev::linux_code_to_macos_code(event.position_code as _)?
            }
        }
        OS_LOWER_ANDROID => rdev::linux_code_to_android_key_code(event.position_code as _)?,
        _ => event.position_code as _,
    };
    #[cfg(any(target_os = "android", target_os = "ios"))]
    let keycode = match _peer {
        OS_LOWER_WINDOWS => rdev::usb_hid_code_to_win_scancode(event.usb_hid as _)?,
        OS_LOWER_LINUX => rdev::usb_hid_code_to_linux_code(event.usb_hid as _)?,
        OS_LOWER_MACOS => {
            if hbb_common::config::LocalConfig::get_kb_layout_type() == "ISO" {
                rdev::usb_hid_code_to_macos_iso_code(event.usb_hid as _)?
            } else {
                rdev::usb_hid_code_to_macos_code(event.usb_hid as _)?
            }
        }
        OS_LOWER_ANDROID => rdev::usb_hid_code_to_android_key_code(event.usb_hid as _)?,
        _ => event.usb_hid as _,
    };
    key_event.set_chr(keycode as _);
    Some(key_event)
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
            #[cfg(target_os = "windows")]
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

pub fn translate_keyboard_mode(peer: &str, event: &Event, key_event: KeyEvent) -> Vec<KeyEvent> {
    let mut events: Vec<KeyEvent> = Vec::new();

    if let Some(unicode_info) = &event.unicode {
        if unicode_info.is_dead {
            #[cfg(target_os = "macos")]
            if peer != OS_LOWER_MACOS && unsafe { IS_LEFT_OPTION_DOWN } {
                // try clear dead key state
                // rdev::clear_dead_key_state();
            } else {
                return events;
            }
            #[cfg(not(target_os = "macos"))]
            return events;
        }
    }

    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    if is_numpad_key(&event) {
        events.append(&mut map_keyboard_mode(peer, event, key_event));
        return events;
    }

    #[cfg(target_os = "macos")]
    // ignore right option key
    if event.platform_code == rdev::kVK_RightOption as u32 {
        return events;
    }

    #[cfg(any(target_os = "linux", target_os = "windows"))]
    if is_altgr(event) {
        return events;
    }

    #[cfg(target_os = "windows")]
    if event.position_code == 0x021D {
        return events;
    }

    #[cfg(target_os = "windows")]
    if peer == OS_LOWER_WINDOWS && matches!(event.event_type,
        EventType::KeyPress(key) | EventType::KeyRelease(key) if is_modifier(&key)) {
        // ToUnicode has no text for modifier Down, while the hotkey Up path
        // emits VK. Keep the same scan identity for both edges and remapping.
        return map_keyboard_mode(peer, event, key_event);
    }

    #[cfg(target_os = "windows")]
    try_fill_win2win_hotkey(peer, event, &key_event, &mut events);

    #[cfg(any(target_os = "linux", target_os = "windows"))]
    if events.is_empty() && is_press(event) {
        try_fill_unicode(peer, event, &key_event, &mut events);
    }

    // If AltGr is down, no need to send events other than unicode.
    #[cfg(target_os = "windows")]
    unsafe {
        if IS_0X021D_DOWN {
            return events;
        }
    }

    #[cfg(target_os = "macos")]
    if !unsafe { IS_LEFT_OPTION_DOWN } {
        try_fill_unicode(peer, event, &key_event, &mut events);
    }

    if events.is_empty() {
        events.append(&mut map_keyboard_mode(peer, event, key_event));
    }
    events
}

pub fn convert_numpad_keys(key: Key) -> Key {
    if get_key_state(enigo::Key::NumLock) {
        return key;
    }
    match key {
        Key::Kp0 => Key::Insert,
        Key::KpDecimal => Key::Delete,
        Key::Kp1 => Key::End,
        Key::Kp2 => Key::DownArrow,
        Key::Kp3 => Key::PageDown,
        Key::Kp4 => Key::LeftArrow,
        Key::Kp5 => Key::Clear,
        Key::Kp6 => Key::RightArrow,
        Key::Kp7 => Key::Home,
        Key::Kp8 => Key::UpArrow,
        Key::Kp9 => Key::PageUp,
        _ => key,
    }
}

pub fn legacy_keyboard_mode_for_peer(peer: &str, event: &Event, mut key_event: KeyEvent, use_cached_modifiers: bool) -> Vec<KeyEvent> {
    let mut events = Vec::new();
    // legacy mode(0): Generate characters locally, look for keycode on other side.
    let (mut key, down_or_up) = match event.event_type {
        EventType::KeyPress(key) => (key, true),
        EventType::KeyRelease(key) => (key, false),
        _ => {
            return events;
        }
    };

    let is_win = peer.eq_ignore_ascii_case(OS_LOWER_WINDOWS);
    if is_win {
        key = convert_numpad_keys(key);
    }

    let alt = get_key_state(enigo::Key::Alt)
        || (!use_cached_modifiers && get_key_state(enigo::Key::RightAlt));
    #[cfg(windows)]
    let ctrl = {
        let mut tmp = get_key_state(enigo::Key::Control) || get_key_state(enigo::Key::RightControl);
        unsafe {
            if IS_ALT_GR {
                if alt || key == Key::AltGr {
                    if tmp {
                        tmp = false;
                    }
                } else {
                    IS_ALT_GR = false;
                }
            }
        }
        tmp
    };
    #[cfg(not(windows))]
    let ctrl = get_key_state(enigo::Key::Control) || get_key_state(enigo::Key::RightControl);
    let shift = get_key_state(enigo::Key::Shift) || get_key_state(enigo::Key::RightShift);
    #[cfg(windows)]
    let command = crate::platform::windows::get_win_key_state();
    #[cfg(not(windows))]
    let command = get_key_state(enigo::Key::Meta)
        || (!use_cached_modifiers && get_key_state(enigo::Key::RWin));
    let control_key = match key {
        Key::Alt => Some(ControlKey::Alt),
        Key::AltGr => Some(ControlKey::RAlt),
        Key::Backspace => Some(ControlKey::Backspace),
        Key::ControlLeft => {
            // when pressing AltGr, an extra VK_LCONTROL with a special
            // scancode with bit 9 set is sent, let's ignore this.
            #[cfg(windows)]
            if (event.position_code >> 8) == 0xE0 {
                unsafe {
                    IS_ALT_GR = true;
                }
                return events;
            }
            Some(ControlKey::Control)
        }
        Key::ControlRight => Some(ControlKey::RControl),
        Key::DownArrow => Some(ControlKey::DownArrow),
        Key::Escape => Some(ControlKey::Escape),
        Key::F1 => Some(ControlKey::F1),
        Key::F10 => Some(ControlKey::F10),
        Key::F11 => Some(ControlKey::F11),
        Key::F12 => Some(ControlKey::F12),
        Key::F2 => Some(ControlKey::F2),
        Key::F3 => Some(ControlKey::F3),
        Key::F4 => Some(ControlKey::F4),
        Key::F5 => Some(ControlKey::F5),
        Key::F6 => Some(ControlKey::F6),
        Key::F7 => Some(ControlKey::F7),
        Key::F8 => Some(ControlKey::F8),
        Key::F9 => Some(ControlKey::F9),
        Key::LeftArrow => Some(ControlKey::LeftArrow),
        Key::MetaLeft => Some(ControlKey::Meta),
        Key::MetaRight => Some(ControlKey::RWin),
        Key::Return => Some(ControlKey::Return),
        Key::RightArrow => Some(ControlKey::RightArrow),
        Key::ShiftLeft => Some(ControlKey::Shift),
        Key::ShiftRight => Some(ControlKey::RShift),
        Key::Space => Some(ControlKey::Space),
        Key::Tab => Some(ControlKey::Tab),
        Key::UpArrow => Some(ControlKey::UpArrow),
        Key::Delete => {
            if is_win && ctrl && alt {
                let mut command = KeyEvent::new();
                command.set_control_key(ControlKey::CtrlAltDel);
                command.down = true;
                command.mode = KeyboardMode::Legacy.into();
                events.push(command);
                return events;
            }
            Some(ControlKey::Delete)
        }
        Key::Apps => Some(ControlKey::Apps),
        Key::Cancel => Some(ControlKey::Cancel),
        Key::Clear => Some(ControlKey::Clear),
        Key::Kana => Some(ControlKey::Kana),
        Key::Hangul => Some(ControlKey::Hangul),
        Key::Junja => Some(ControlKey::Junja),
        Key::Final => Some(ControlKey::Final),
        Key::Hanja => Some(ControlKey::Hanja),
        Key::Hanji => Some(ControlKey::Hanja),
        Key::Lang2 => Some(ControlKey::Convert),
        Key::Print => Some(ControlKey::Print),
        Key::Select => Some(ControlKey::Select),
        Key::Execute => Some(ControlKey::Execute),
        Key::PrintScreen => Some(ControlKey::Snapshot),
        Key::Help => Some(ControlKey::Help),
        Key::Sleep => Some(ControlKey::Sleep),
        Key::Separator => Some(ControlKey::Separator),
        Key::KpReturn => Some(ControlKey::NumpadEnter),
        Key::Kp0 => Some(ControlKey::Numpad0),
        Key::Kp1 => Some(ControlKey::Numpad1),
        Key::Kp2 => Some(ControlKey::Numpad2),
        Key::Kp3 => Some(ControlKey::Numpad3),
        Key::Kp4 => Some(ControlKey::Numpad4),
        Key::Kp5 => Some(ControlKey::Numpad5),
        Key::Kp6 => Some(ControlKey::Numpad6),
        Key::Kp7 => Some(ControlKey::Numpad7),
        Key::Kp8 => Some(ControlKey::Numpad8),
        Key::Kp9 => Some(ControlKey::Numpad9),
        Key::KpDivide => Some(ControlKey::Divide),
        Key::KpMultiply => Some(ControlKey::Multiply),
        Key::KpDecimal => Some(ControlKey::Decimal),
        Key::KpMinus => Some(ControlKey::Subtract),
        Key::KpPlus => Some(ControlKey::Add),
        Key::CapsLock | Key::NumLock | Key::ScrollLock => {
            return events;
        }
        Key::Home => Some(ControlKey::Home),
        Key::End => Some(ControlKey::End),
        Key::Insert => Some(ControlKey::Insert),
        Key::PageUp => Some(ControlKey::PageUp),
        Key::PageDown => Some(ControlKey::PageDown),
        Key::Pause => Some(ControlKey::Pause),
        _ => None,
    };
    if let Some(k) = control_key {
        key_event.set_control_key(k);
    } else {
        let name = event
            .unicode
            .as_ref()
            .and_then(|unicode| unicode.name.clone());
        let mut chr = match &name {
            Some(ref s) => {
                if s.len() <= 2 {
                    // exclude chinese characters
                    s.chars().next().unwrap_or('\0')
                } else {
                    '\0'
                }
            }
            _ => '\0',
        };
        if chr == '·' {
            // special for Chinese
            chr = '`';
        }
        if chr == '\0' {
            chr = match key {
                Key::Num1 => '1',
                Key::Num2 => '2',
                Key::Num3 => '3',
                Key::Num4 => '4',
                Key::Num5 => '5',
                Key::Num6 => '6',
                Key::Num7 => '7',
                Key::Num8 => '8',
                Key::Num9 => '9',
                Key::Num0 => '0',
                Key::KeyA => 'a',
                Key::KeyB => 'b',
                Key::KeyC => 'c',
                Key::KeyD => 'd',
                Key::KeyE => 'e',
                Key::KeyF => 'f',
                Key::KeyG => 'g',
                Key::KeyH => 'h',
                Key::KeyI => 'i',
                Key::KeyJ => 'j',
                Key::KeyK => 'k',
                Key::KeyL => 'l',
                Key::KeyM => 'm',
                Key::KeyN => 'n',
                Key::KeyO => 'o',
                Key::KeyP => 'p',
                Key::KeyQ => 'q',
                Key::KeyR => 'r',
                Key::KeyS => 's',
                Key::KeyT => 't',
                Key::KeyU => 'u',
                Key::KeyV => 'v',
                Key::KeyW => 'w',
                Key::KeyX => 'x',
                Key::KeyY => 'y',
                Key::KeyZ => 'z',
                Key::Comma => ',',
                Key::Dot => '.',
                Key::SemiColon => ';',
                Key::Quote => '\'',
                Key::LeftBracket => '[',
                Key::RightBracket => ']',
                Key::Slash => '/',
                Key::BackSlash => '\\',
                Key::Minus => '-',
                Key::Equal => '=',
                Key::BackQuote => '`',
                _ => '\0',
            }
        }
        if chr != '\0' {
            if chr == 'l' && is_win && command {
                events.push(client::event_lock_screen());
                return events;
            }
            key_event.set_chr(chr as _);
        } else {
            log::error!("Unknown key {:?}", &event);
            return events;
        }
    }
    let (alt, ctrl, shift, command) = if use_cached_modifiers {
        client::get_modifiers_state(alt, ctrl, shift, command)
    } else {
        (alt, ctrl, shift, command)
    };
    client::legacy_modifiers(&mut key_event, alt, ctrl, shift, command);

    if down_or_up == true {
        key_event.down = true;
    }
    events.push(key_event);
    events
}

fn native_modifier_snapshot(key_event: &KeyEvent, peer_platform: &str) -> Option<Vec<KeyEvent>> {
    if !key_event.down && !key_event.press && !matches!(key_event.union,
        Some(key_event::Union::Seq(_)) | Some(key_event::Union::Unicode(_))) {
        return None;
    }
    let mode = key_event.mode.enum_value_or_default();
    let mut peer = peer_platform.to_lowercase();
    peer.retain(|c| !c.is_whitespace());
    let keys = [
        (Key::ShiftLeft, enigo::Key::Shift),
        (Key::ShiftRight, enigo::Key::RightShift),
        (Key::ControlLeft, enigo::Key::Control),
        (Key::ControlRight, enigo::Key::RightControl),
        (Key::Alt, enigo::Key::Alt),
        (Key::AltGr, enigo::Key::RightAlt),
        (Key::MetaLeft, enigo::Key::Meta),
        (Key::MetaRight, enigo::Key::RWin),
    ];
    let mut modifiers = Vec::new();
    for (key, state_key) in keys {
        if !get_key_state(state_key) { continue; }
        #[cfg(target_os = "windows")]
        let platform_code = rdev::win_code_from_key(key).unwrap_or(0);
        #[cfg(target_os = "windows")]
        let position_code = rdev::win_scancode_from_key(key).unwrap_or(0);
        #[cfg(not(target_os = "windows"))]
        let position_code = rdev::code_from_key(key).unwrap_or(0) as u32;
        #[cfg(not(target_os = "windows"))]
        let platform_code = position_code;
        let event = Event {
            time: std::time::SystemTime::now(),
            unicode: None,
            platform_code,
            position_code: position_code as _,
            event_type: EventType::KeyPress(key),
            usb_hid: 0,
            #[cfg(any(target_os = "windows", target_os = "macos"))]
            extra_data: 0,
        };
        let template = KeyEvent { mode: mode.into(), ..Default::default() };
        let events = match mode {
            KeyboardMode::Map => map_keyboard_mode(&peer, &event, template),
            KeyboardMode::Translate => translate_keyboard_mode(&peer, &event, template),
            _ => legacy_keyboard_mode_for_peer(&peer, &event, template, false),
        };
        modifiers.extend(events);
    }
    Some(modifiers)
}
