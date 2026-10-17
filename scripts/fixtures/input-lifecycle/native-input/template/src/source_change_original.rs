    pub fn change_input_source_r5(session_id: SessionID, input_source: String) {
        let cur_input_source = get_cur_session_input_source();
        if cur_input_source == input_source {
            return;
        }
        if input_source == CONFIG_INPUT_SOURCE_1 {
            #[cfg(target_os = "macos")]
            if !crate::platform::macos::is_can_input_monitoring(false) {
                log::error!("change_input_source, is_can_input_monitoring() false");
                return;
            }
            for session in crate::flutter::sessions::get_sessions() {
                if session.is_default() { session.invalidate_keyboard_input_source(); }
            }
            // It is ok to start grab loop multiple times.
            super::client::start_grab_loop();
            super::IS_RDEV_ENABLED.store(true, super::Ordering::SeqCst);
            crate::flutter_ffi::session_enter_or_leave(session_id, true);
        } else if input_source == CONFIG_INPUT_SOURCE_2 {
            for session in crate::flutter::sessions::get_sessions() {
                if session.is_default() { session.invalidate_keyboard_input_source(); }
            }
            // No need to stop grab loop.
            crate::flutter_ffi::session_enter_or_leave(session_id, false);
            super::IS_RDEV_ENABLED.store(false, super::Ordering::SeqCst);
        }
        set_local_option(CONFIG_OPTION_INPUT_SOURCE.to_string(), input_source);
    }
