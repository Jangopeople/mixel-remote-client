    pub fn get_physical_modifiers_state(alt: bool, ctrl: bool, shift: bool, command: bool) -> (bool, bool, bool, bool) {
        #[cfg(target_os = "windows")]
        unsafe {
            use winapi::um::winuser::{GetAsyncKeyState, VK_LMENU, VK_RMENU, VK_LCONTROL, VK_RCONTROL, VK_LSHIFT, VK_RSHIFT, VK_LWIN, VK_RWIN};
            return (
                alt || GetAsyncKeyState(VK_LMENU) < 0 || GetAsyncKeyState(VK_RMENU) < 0,
                ctrl || GetAsyncKeyState(VK_LCONTROL) < 0 || GetAsyncKeyState(VK_RCONTROL) < 0,
                shift || GetAsyncKeyState(VK_LSHIFT) < 0 || GetAsyncKeyState(VK_RSHIFT) < 0,
                command || GetAsyncKeyState(VK_LWIN) < 0 || GetAsyncKeyState(VK_RWIN) < 0,
            );
        }
        #[cfg(not(any(target_os = "windows", target_os = "android", target_os = "ios")))]
        { return (
            alt || get_key_state(enigo::Key::Alt) || get_key_state(enigo::Key::RightAlt),
            ctrl || get_key_state(enigo::Key::Control) || get_key_state(enigo::Key::RightControl),
            shift || get_key_state(enigo::Key::Shift) || get_key_state(enigo::Key::RightShift),
            command || get_key_state(enigo::Key::Meta) || get_key_state(enigo::Key::RWin),
        ); }
        #[cfg(any(target_os = "android", target_os = "ios"))]
        { (alt, ctrl, shift, command) }
    }
