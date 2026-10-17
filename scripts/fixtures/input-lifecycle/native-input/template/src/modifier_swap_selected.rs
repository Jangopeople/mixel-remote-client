    pub fn swap_modifier_key(&self, msg: &mut KeyEvent) {
        let allow_swap_key = self.get_toggle_option("allow_swap_key".to_string());
        if allow_swap_key {
            if let Some(key_event::Union::ControlKey(ck)) = msg.union {
                let ck = ck.enum_value_or_default();
                let ck = match ck {
                    ControlKey::Control => ControlKey::Meta,
                    ControlKey::Meta => ControlKey::Control,
                    ControlKey::RControl => ControlKey::RWin,
                    ControlKey::RWin => ControlKey::RControl,
                    _ => ck,
                };
                msg.set_control_key(ck);
            }
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

            let code = msg.chr();
            if code != 0 {
                let mut peer = self.peer_platform().to_lowercase();
                peer.retain(|c| !c.is_whitespace());

                let key = match peer.as_str() {
                    "windows" => {
                        let key = rdev::win_key_from_scancode(code);
                        let key = match key {
                            rdev::Key::ControlLeft => rdev::Key::MetaLeft,
                            rdev::Key::MetaLeft => rdev::Key::ControlLeft,
                            rdev::Key::ControlRight => rdev::Key::MetaRight,
                            rdev::Key::MetaRight => rdev::Key::ControlRight,
                            _ => key,
                        };
                        rdev::win_scancode_from_key(key).unwrap_or_default()
                    }
                    "macos" => {
                        let key = rdev::macos_key_from_code(code as _);
                        let key = match key {
                            rdev::Key::ControlLeft => rdev::Key::MetaLeft,
                            rdev::Key::MetaLeft => rdev::Key::ControlLeft,
                            rdev::Key::ControlRight => rdev::Key::MetaRight,
                            rdev::Key::MetaRight => rdev::Key::ControlRight,
                            _ => key,
                        };
                        rdev::macos_keycode_from_key(key).unwrap_or_default() as _
                    }
                    _ => {
                        let key = rdev::linux_key_from_code(code);
                        let key = match key {
                            rdev::Key::ControlLeft => rdev::Key::MetaLeft,
                            rdev::Key::MetaLeft => rdev::Key::ControlLeft,
                            rdev::Key::ControlRight => rdev::Key::MetaRight,
                            rdev::Key::MetaRight => rdev::Key::ControlRight,
                            _ => key,
                        };
                        rdev::linux_keycode_from_key(key).unwrap_or_default()
                    }
                };
                msg.set_chr(key);
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
