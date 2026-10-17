    pub fn process_event_with_session<T: InvokeUiSession>(
        keyboard_mode: &str,
        event: &Event,
        lock_modes: Option<i32>,
        session: &Session<T>,
    ) {
        #[cfg(all(feature = "flutter", not(any(target_os = "android", target_os = "ios"))))]
        let _admission = NATIVE_INPUT_GATE.lock().unwrap();
        let keyboard_mode = get_keyboard_mode_enum(keyboard_mode);
        let peer = session.peer_platform().to_lowercase();
        // UI repeats and ownership belong to this Session's wire ledger. A
        // different window's shared modifier cache must not suppress this Down
        // or add a released modifier to a Legacy UI event.
        for key_event in event_to_key_events_with_modifiers(peer, event, keyboard_mode, lock_modes, false) {
            session.send_key_event(&key_event);
        }
    }
