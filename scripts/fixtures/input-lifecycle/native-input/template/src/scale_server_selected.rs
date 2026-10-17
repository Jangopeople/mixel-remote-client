fn handle_scale(scale: i32) {
    let mut en = ENIGO.lock().unwrap();
    if scale == 0 {
        en.key_up(Key::Control);
    } else {
        if en.key_down(Key::Control).is_ok() {
            en.mouse_scroll_y(scale);
        }
    }
}
