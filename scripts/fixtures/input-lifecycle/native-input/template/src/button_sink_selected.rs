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

pub fn release_all_owned_mouse_buttons() {
    let mut en = ENIGO.lock().unwrap();
    let buttons = {
        let mut owners = MOUSE_BUTTON_OWNERS.lock().unwrap();
        let buttons: Vec<i32> = owners.keys().copied().collect();
        owners.clear();
        buttons
    };
    for button in buttons {
        if let Some(mouse_button) = mouse_button_from_mask(button) { en.mouse_up(mouse_button); }
    }
}
