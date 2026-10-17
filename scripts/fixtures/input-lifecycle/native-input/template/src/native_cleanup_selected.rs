pub fn release_remote_keys(keyboard_mode: &str) {
    #[cfg(all(feature = "flutter", not(any(target_os = "android", target_os = "ios"))))]
    let _admission = NATIVE_INPUT_GATE.lock().unwrap();
    #[cfg(all(feature = "flutter", not(any(target_os = "android", target_os = "ios"))))]
    if NATIVE_INPUT_SUSPENDED.load(Ordering::SeqCst) { return; }
    let to_release = std::mem::take(&mut *TO_RELEASE.lock().unwrap());
    let mut events = Vec::new();
    for (key, mut event) in to_release.into_iter() {
        event.event_type = EventType::KeyRelease(key);
        events.push(event.clone());
        // If Alt or AltGr is pressed, we need to send another key stoke to release it.
        // Because the controlled side may hold the alt state, if local window is switched by [Alt + Tab].
        if key == Key::Alt || key == Key::AltGr {
            event.event_type = EventType::KeyPress(key);
            events.push(event.clone());
            event.event_type = EventType::KeyRelease(key);
            events.push(event.clone());
        }
    }
    client::process_release_events(keyboard_mode, &events);
}

    fn process_native_event_with_session<T: InvokeUiSession>(
        _keyboard_mode: &str,
        event: &Event,
        lock_modes: Option<i32>,
        session: &Session<T>,
        restore_modifiers: bool,
    ) {
        if is_long_press(&event) { return; }
        let keyboard_mode = get_keyboard_mode_enum(&session.get_keyboard_mode());
        let peer = session.peer_platform().to_lowercase();
        // Bind conversion, held templates and enqueue to the same session even
        // if foreground selection changes during the native callback.
        for key_event in event_to_key_events_with_modifiers(peer.clone(), event, keyboard_mode, lock_modes, false) {
            let modifiers = if restore_modifiers {
                native_modifier_snapshot(&key_event, &peer)
            } else {
                None
            };
            session.send_native_key_event(&key_event, modifiers);
        }
    }
