pub fn release_remote_keys_r3(keyboard_mode: &str) {
    // todo!: client quit suddenly, how to release keys?
    let to_release = TO_RELEASE.lock().unwrap().clone();
    TO_RELEASE.lock().unwrap().clear();
    for (key, mut event) in to_release.into_iter() {
        event.event_type = EventType::KeyRelease(key);
        client::process_release_event_r3(keyboard_mode, &event, None);
        // If Alt or AltGr is pressed, we need to send another key stoke to release it.
        // Because the controlled side may hold the alt state, if local window is switched by [Alt + Tab].
        if key == Key::Alt || key == Key::AltGr {
            event.event_type = EventType::KeyPress(key);
            client::process_release_event_r3(keyboard_mode, &event, None);
            event.event_type = EventType::KeyRelease(key);
            client::process_release_event_r3(keyboard_mode, &event, None);
        }
    }
}
