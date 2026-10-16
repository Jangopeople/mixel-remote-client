    pub fn process_release_events(keyboard_mode: &str, events: &[Event]) {
        // Only release_remote_keys calls this; it holds NATIVE_INPUT_GATE across
        // suspended check, cache take, target capture and the complete batch.
        #[cfg(all(feature = "cli", not(feature = "flutter")))]
        let _ = (keyboard_mode, events);
        #[cfg(not(any(feature = "flutter", feature = "cli")))]
        if let Some(session) = CUR_SESSION.lock().unwrap().as_ref() {
            process_release_events_with_session(keyboard_mode, events, session);
        }
        #[cfg(feature = "flutter")]
        if let Some(session) = flutter::get_cur_session() {
            process_release_events_with_session(keyboard_mode, events, &session);
        }
    }

    fn process_release_events_with_session<T: InvokeUiSession>(
        _keyboard_mode: &str,
        events: &[Event],
        session: &Session<T>,
    ) {
        // A synthetic Alt Up/Down/Up is one cleanup batch. Bind its target,
        // peer conversion and mode once so a foreground switch cannot split it.
        let keyboard_mode = get_keyboard_mode_enum(&session.get_keyboard_mode());
        let peer = session.peer_platform().to_lowercase();
        for event in events {
            if is_long_press(event) { continue; }
            for key_event in event_to_key_events_with_modifiers(peer.clone(), event, keyboard_mode, None, false) {
                session.send_native_key_event(&key_event, None);
            }
        }
    }
