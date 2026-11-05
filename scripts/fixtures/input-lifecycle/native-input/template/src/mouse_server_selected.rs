pub fn linux_mouse(evt:&MouseEvent,mut en:&mut Enigo) {let conn=0;let evt_type=evt.mask & MOUSE_TYPE_MASK;let buttons=evt.mask>>3;
    #[cfg(all())]
    let mut to_release = Vec::new();
    if evt_type == MOUSE_TYPE_DOWN {
        fix_modifiers(&evt.modifiers[..], &mut en, 0);
        #[cfg(any())]
        en.reset_flag();
        for ref ck in evt.modifiers.iter() {
            if let Some(key) = KEY_MAP.get(&ck.value()) {
                #[cfg(any())]
                en.add_flag(key);
                #[cfg(all())]
                if key != &Key::CapsLock && key != &Key::NumLock {
                    if !get_modifier_state(key.clone(), &mut en) {
                        en.key_down(key.clone()).ok();
                        #[cfg(any())]
                        modifier_sleep();
                        to_release.push(key);
                    }
                }
            }
        }
    }
    match evt_type {
        MOUSE_TYPE_DOWN => owned_mouse_down(&mut en, conn, buttons),
        MOUSE_TYPE_UP => owned_mouse_up(&mut en, conn, buttons),
        _=>{}
    }
    #[cfg(all())]
    for key in to_release {
        en.key_up(key.clone());
    }
}
pub fn windows_mouse(evt:&MouseEvent,mut en:&mut Enigo) {let conn=0;let evt_type=evt.mask & MOUSE_TYPE_MASK;let buttons=evt.mask>>3;
    #[cfg(all())]
    let mut to_release = Vec::new();
    if evt_type == MOUSE_TYPE_DOWN {
        fix_modifiers(&evt.modifiers[..], &mut en, 0);
        #[cfg(any())]
        en.reset_flag();
        for ref ck in evt.modifiers.iter() {
            if let Some(key) = KEY_MAP.get(&ck.value()) {
                #[cfg(any())]
                en.add_flag(key);
                #[cfg(all())]
                if key != &Key::CapsLock && key != &Key::NumLock {
                    if !get_modifier_state(key.clone(), &mut en) {
                        en.key_down(key.clone()).ok();
                        #[cfg(all())]
                        modifier_sleep();
                        to_release.push(key);
                    }
                }
            }
        }
    }
    match evt_type {
        MOUSE_TYPE_DOWN => owned_mouse_down(&mut en, conn, buttons),
        MOUSE_TYPE_UP => owned_mouse_up(&mut en, conn, buttons),
        _=>{}
    }
    #[cfg(all())]
    for key in to_release {
        en.key_up(key.clone());
    }
}
pub fn macos_mouse(evt:&MouseEvent,mut en:&mut Enigo) {let conn=0;let evt_type=evt.mask & MOUSE_TYPE_MASK;let buttons=evt.mask>>3;
    #[cfg(any())]
    let mut to_release = Vec::new();
    if evt_type == MOUSE_TYPE_DOWN {
        fix_modifiers(&evt.modifiers[..], &mut en, 0);
        #[cfg(all())]
        en.reset_flag();
        for ref ck in evt.modifiers.iter() {
            if let Some(key) = KEY_MAP.get(&ck.value()) {
                #[cfg(all())]
                en.add_flag(key);
                #[cfg(any())]
                if key != &Key::CapsLock && key != &Key::NumLock {
                    if !get_modifier_state(key.clone(), &mut en) {
                        en.key_down(key.clone()).ok();
                        #[cfg(any())]
                        modifier_sleep();
                        to_release.push(key);
                    }
                }
            }
        }
    }
    match evt_type {
        MOUSE_TYPE_DOWN => owned_mouse_down(&mut en, conn, buttons),
        MOUSE_TYPE_UP => owned_mouse_up(&mut en, conn, buttons),
        _=>{}
    }
    #[cfg(any())]
    for key in to_release {
        en.key_up(key.clone());
    }
}

fn fix_modifier(
    modifiers: &[EnumOrUnknown<ControlKey>],
    key0: ControlKey,
    key1: Key,
    en: &mut Enigo,
) {
    if get_modifier_state(key1, en) && !modifiers.contains(&EnumOrUnknown::new(key0)) {
        #[cfg(windows)]
        if key0 == ControlKey::Control && get_modifier_state(Key::Alt, en) {
            // AltGr case
            return;
        }
        en.key_up(key1);
        log::debug!("Fixed {:?}", key1);
    }
}
fn fix_modifiers(modifiers: &[EnumOrUnknown<ControlKey>], en: &mut Enigo, ck: i32) {
    if ck != ControlKey::Shift.value() {
        fix_modifier(modifiers, ControlKey::Shift, Key::Shift, en);
    }
    if ck != ControlKey::RShift.value() {
        fix_modifier(modifiers, ControlKey::Shift, Key::RightShift, en);
    }
    if ck != ControlKey::Alt.value() {
        fix_modifier(modifiers, ControlKey::Alt, Key::Alt, en);
    }
    if ck != ControlKey::RAlt.value() {
        fix_modifier(modifiers, ControlKey::Alt, Key::RightAlt, en);
    }
    if ck != ControlKey::Control.value() {
        fix_modifier(modifiers, ControlKey::Control, Key::Control, en);
    }
    if ck != ControlKey::RControl.value() {
        fix_modifier(modifiers, ControlKey::Control, Key::RightControl, en);
    }
    if ck != ControlKey::Meta.value() {
        fix_modifier(modifiers, ControlKey::Meta, Key::Meta, en);
    }
    if ck != ControlKey::RWin.value() {
        fix_modifier(modifiers, ControlKey::Meta, Key::RWin, en);
    }
}

fn mouse_button_from_mask(button: i32) -> Option<MouseButton> {
    match button {
        MOUSE_BUTTON_LEFT => Some(MouseButton::Left),
        MOUSE_BUTTON_RIGHT => Some(MouseButton::Right),
        MOUSE_BUTTON_WHEEL => Some(MouseButton::Middle),
        MOUSE_BUTTON_BACK => Some(MouseButton::Back),
        MOUSE_BUTTON_FORWARD => Some(MouseButton::Forward),
        _ => None,
    }
}
fn owned_mouse_down(en: &mut Enigo, conn: i32, button: i32) {
    let Some(mouse_button) = mouse_button_from_mask(button) else { return; };
    let result = en.mouse_down(mouse_button);
    if result.is_ok() {
        MOUSE_BUTTON_OWNERS.lock().unwrap().entry(button).or_default().insert(conn);
    }
    allow_err!(result);
}
fn owned_mouse_up(en: &mut Enigo, conn: i32, button: i32) {
    let Some(mouse_button) = mouse_button_from_mask(button) else { return; };
    let release = {
        let mut owners = MOUSE_BUTTON_OWNERS.lock().unwrap();
        let Some(connections) = owners.get_mut(&button) else { return; };
        if !connections.remove(&conn) { return; }
        if connections.is_empty() { owners.remove(&button); true } else { false }
    };
    if release { en.mouse_up(mouse_button); }
}
