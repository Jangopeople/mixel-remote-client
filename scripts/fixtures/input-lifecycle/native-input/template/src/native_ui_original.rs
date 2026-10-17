    pub fn process_event_with_session_r6<T: InvokeUiSession>(
        keyboard_mode: &str,
        event: &Event,
        lock_modes: Option<i32>,
        session: &Session<T>,
    ) {
        let keyboard_mode = get_keyboard_mode_enum(keyboard_mode);
        if is_long_press(&event) {
            return;
        }
        let peer = session.peer_platform().to_lowercase();
        for key_event in event_to_key_events(peer, &event, keyboard_mode, lock_modes) {
            session.send_key_event(&key_event);
        }
    }
