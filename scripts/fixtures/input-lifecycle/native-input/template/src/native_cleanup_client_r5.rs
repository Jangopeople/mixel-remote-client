    pub fn process_release_events_r5(keyboard_mode: &str, events: &[Event]) {
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
