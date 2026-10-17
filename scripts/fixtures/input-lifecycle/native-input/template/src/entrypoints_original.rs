#[allow(unused_imports)]
use super::{sessions,SessionID,HashMap,SyncReturn};
use super::input::*;
use super::{change_input_source,try_sync_peer_option};
pub fn session_handle_flutter_key_event(
    session_id: SessionID,
    character: String,
    usb_hid: i32,
    lock_modes: i32,
    down_or_up: bool,
) {
    if let Some(session) = sessions::get_session_by_session_id(&session_id) {
        let keyboard_mode = session.get_keyboard_mode();
        session.handle_flutter_key_event(
            &keyboard_mode,
            &character,
            usb_hid,
            lock_modes,
            down_or_up,
        );
    }
}

pub fn session_handle_flutter_raw_key_event(
    session_id: SessionID,
    name: String,
    platform_code: i32,
    position_code: i32,
    lock_modes: i32,
    down_or_up: bool,
) {
    if let Some(session) = sessions::get_session_by_session_id(&session_id) {
        let keyboard_mode = session.get_keyboard_mode();
        session.handle_flutter_raw_key_event(
            &keyboard_mode,
            &name,
            platform_code,
            position_code,
            lock_modes,
            down_or_up,
        );
    }
}

pub fn session_input_key(
    session_id: SessionID,
    name: String,
    down: bool,
    press: bool,
    alt: bool,
    ctrl: bool,
    shift: bool,
    command: bool,
) {
    if let Some(session) = sessions::get_session_by_session_id(&session_id) {
        // #[cfg(any(target_os = "android", target_os = "ios"))]
        session.input_key(&name, down, press, alt, ctrl, shift, command);
    }
}

pub fn session_input_string(session_id: SessionID, value: String) {
    if let Some(session) = sessions::get_session_by_session_id(&session_id) {
        // #[cfg(any(target_os = "android", target_os = "ios"))]
        session.input_string(&value);
    }
}

pub fn session_send_mouse(session_id: SessionID, msg: String) {
    if let Ok(m) = serde_json::from_str::<HashMap<String, String>>(&msg) {
        // Relative mouse mode marker validation (Flutter-only).
        // This only validates and filters markers; the server tracks per-connection
        // relative-movement processing state but not mode activation/deactivation.
        // See doc comment above for the message contract.
        if let Some(v) = m.get("relative_mouse_mode") {
            let active = matches!(v.as_str(), "1" | "Y" | "on");

            // Disable marker: local-only, never forwarded to the server.
            // The server does not track mode deactivation; it simply stops receiving
            // relative move events when the client exits relative mouse mode.
            if !active {
                #[cfg(not(any(target_os = "android", target_os = "ios")))]
                crate::keyboard::set_relative_mouse_mode_state(false);
                return;
            }

            // Enable marker: validate BEFORE setting state to avoid desync.
            // This ensures we only mark as active if the marker will actually be forwarded.

            // Enable marker is allowed to go through only if it's a safe no-op relative move.
            // This avoids accidentally moving the remote cursor (e.g. if type/x/y are missing).
            let msg_type = m.get("type").map(|t| t.as_str());
            if msg_type != Some("move_relative") {
                log::warn!(
                    "relative_mouse_mode activation marker has invalid type: {:?}, expected 'move_relative'. Dropping.",
                    msg_type
                );
                return;
            }
            let x_marker = m
                .get("x")
                .map(|x| x.parse::<i32>().unwrap_or(0))
                .unwrap_or(0);
            let y_marker = m
                .get("y")
                .map(|y| y.parse::<i32>().unwrap_or(0))
                .unwrap_or(0);
            if x_marker != 0 || y_marker != 0 {
                log::warn!(
                    "relative_mouse_mode activation marker has non-zero coordinates: x={}, y={}. Dropping.",
                    x_marker, y_marker
                );
                return;
            }

            // Guard against unexpected fields that could turn this no-op into a real event.
            if m.contains_key("buttons")
                || m.contains_key("alt")
                || m.contains_key("ctrl")
                || m.contains_key("shift")
                || m.contains_key("command")
            {
                log::warn!(
                    "relative_mouse_mode activation marker contains unexpected fields (buttons/alt/ctrl/shift/command). Dropping."
                );
                return;
            }

            // All validation passed - marker will be forwarded as a no-op relative move.
            #[cfg(not(any(target_os = "android", target_os = "ios")))]
            crate::keyboard::set_relative_mouse_mode_state(true);
        }

        let alt = m.get("alt").is_some();
        let ctrl = m.get("ctrl").is_some();
        let shift = m.get("shift").is_some();
        let command = m.get("command").is_some();
        let x = m
            .get("x")
            .map(|x| x.parse::<i32>().unwrap_or(0))
            .unwrap_or(0);
        let y = m
            .get("y")
            .map(|x| x.parse::<i32>().unwrap_or(0))
            .unwrap_or(0);
        let mut mask = 0;
        if let Some(_type) = m.get("type") {
            mask = match _type.as_str() {
                "down" => MOUSE_TYPE_DOWN,
                "up" => MOUSE_TYPE_UP,
                "wheel" => MOUSE_TYPE_WHEEL,
                "trackpad" => MOUSE_TYPE_TRACKPAD,
                "move_relative" => MOUSE_TYPE_MOVE_RELATIVE,
                _ => 0,
            };
        }
        if let Some(buttons) = m.get("buttons") {
            mask |= match buttons.as_str() {
                "left" => MOUSE_BUTTON_LEFT,
                "right" => MOUSE_BUTTON_RIGHT,
                "wheel" => MOUSE_BUTTON_WHEEL,
                "back" => MOUSE_BUTTON_BACK,
                "forward" => MOUSE_BUTTON_FORWARD,
                _ => 0,
            } << 3;
        }
        if let Some(session) = sessions::get_session_by_session_id(&session_id) {
            session.send_mouse(mask, x, y, alt, ctrl, shift, command);
        }
    }
}

pub fn session_send_pointer(session_id: SessionID, msg: String) {
    super::flutter::session_send_pointer(session_id, msg);
}

pub fn main_set_input_source(session_id: SessionID, value: String) {
    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    {
        change_input_source(session_id, value);
        if let Some(session) = sessions::get_session_by_session_id(&session_id) {
            try_sync_peer_option(&session, &session_id, "input_source", None);
        }
    }
}
