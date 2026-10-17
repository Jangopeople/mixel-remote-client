    pub fn send_mouse(
        &self,
        mut mask: i32,
        x: i32,
        y: i32,
        alt: bool,
        ctrl: bool,
        shift: bool,
        command: bool,
    ) {
        #[allow(unused_mut)]
        let mut command = command;
        #[cfg(windows)]
        {
            if !command && crate::platform::windows::get_win_key_state() {
                command = true;
            }
        }

        // Compute event type once using MOUSE_TYPE_MASK for reuse
        let event_type = mask & MOUSE_TYPE_MASK;
        let (x, y) = if event_type == MOUSE_TYPE_WHEEL || event_type == MOUSE_TYPE_TRACKPAD {
            self.get_scroll_xy((x, y))
        } else {
            (x, y)
        };

        // Read current physical state; a different session's cached modifier
        // can remain held while its permission-revoked Up is deferred.
        let (alt, ctrl, shift, command) =
            keyboard::client::get_physical_modifiers_state(alt, ctrl, shift, command);
        let is_left = (mask & (MOUSE_BUTTON_LEFT << 3)) > 0;
        let is_right = (mask & (MOUSE_BUTTON_RIGHT << 3)) > 0;
        if is_left ^ is_right {
            let swap_lr = self.get_toggle_option("swap-left-right-mouse".to_string());
            if swap_lr {
                if is_left {
                    mask = (mask & (!(MOUSE_BUTTON_LEFT << 3))) | (MOUSE_BUTTON_RIGHT << 3);
                } else {
                    mask = (mask & (!(MOUSE_BUTTON_RIGHT << 3))) | (MOUSE_BUTTON_LEFT << 3);
                }
            }
        }

        send_mouse(mask, x, y, alt, ctrl, shift, command, self);
        // on macos, ctrl + left button down = right button down, up won't emit, so we need to
        // emit up myself if peer is not macos
        // to-do: how about ctrl + left from win to macos
        if cfg!(target_os = "macos") {
            let buttons = mask >> 3;
            if buttons == MOUSE_BUTTON_LEFT
                && event_type == MOUSE_TYPE_DOWN
                && ctrl
                && self.peer_platform() != "Mac OS"
            {
                self.send_mouse(
                    (MOUSE_BUTTON_LEFT << 3 | MOUSE_TYPE_UP) as _,
                    x,
                    y,
                    alt,
                    ctrl,
                    shift,
                    command,
                );
            }
        }
    }
    fn swap_modifier_mouse(&self, msg: &mut hbb_common::protos::message::MouseEvent) {
        let allow_swap_key = self.get_toggle_option("allow_swap_key".to_string());
        if allow_swap_key {
            msg.modifiers = msg
                .modifiers
                .iter()
                .map(|ck| {
                    let ck = ck.enum_value_or_default();
                    let ck = match ck {
                        ControlKey::Control => ControlKey::Meta,
                        ControlKey::Meta => ControlKey::Control,
                        ControlKey::RControl => ControlKey::RWin,
                        ControlKey::RWin => ControlKey::RControl,
                        _ => ck,
                    };
                    hbb_common::protobuf::EnumOrUnknown::new(ck)
                })
                .collect();
        };
    }
