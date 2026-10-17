    pub fn process_release_event_r3(keyboard_mode: &str, event: &Event, lock_modes: Option<i32>) {
        #[cfg(all(feature = "cli", not(feature = "flutter")))]
        let _ = (keyboard_mode, event, lock_modes);
        #[cfg(not(any(feature = "flutter", feature = "cli")))]
        if let Some(session) = CUR_SESSION.lock().unwrap().as_ref() {
            process_native_event_with_session(keyboard_mode, event, lock_modes, session, false);
        }
        #[cfg(feature = "flutter")]
        if let Some(session) = flutter::get_cur_session() {
            process_native_event_with_session(keyboard_mode, event, lock_modes, &session, false);
        }
    }
