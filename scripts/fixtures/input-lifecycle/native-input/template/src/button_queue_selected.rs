    fn track_input_mouse(input: &InputMouse, held: &mut HashSet<(i32, i32)>) {
        let button = input.msg.mask >> 3;
        if !input.simulate || !matches!(button, MOUSE_BUTTON_LEFT | MOUSE_BUTTON_RIGHT | MOUSE_BUTTON_WHEEL | MOUSE_BUTTON_BACK | MOUSE_BUTTON_FORWARD) { return; }
        match input.msg.mask & MOUSE_TYPE_MASK {
            MOUSE_TYPE_DOWN => { held.insert((input.conn_id, button)); }
            MOUSE_TYPE_UP => { held.remove(&(input.conn_id, button)); }
            _ => {}
        }
    }

    fn release_input_mouse(held: &mut HashSet<(i32, i32)>) {
        for (conn, button) in held.drain() {
            let event = MouseEvent { mask: (button << 3) | MOUSE_TYPE_UP, ..Default::default() };
            // Follow the same Mac main queue / Windows portable-service route as
            // the Down. The actual effect sink rejects unowned/foreign Ups.
            handle_mouse(&event, conn, String::new(), 0, true, false);
        }
    }

    fn release_mouse_input(&self) {
        self.tx_input.send(MessageInput::ReleaseMouse).ok();
    }

    fn handle_input(receiver: std_mpsc::Receiver<MessageInput>, tx: Sender) {
        let mut block_input_mode = false;
        let mut held_mouse_buttons = HashSet::new();
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        {
            rdev::set_mouse_extra_info(enigo::ENIGO_INPUT_EXTRA_VALUE);
            rdev::set_keyboard_extra_info(enigo::ENIGO_INPUT_EXTRA_VALUE);
        }
        #[cfg(target_os = "macos")]
        reset_input_ondisconn();
        loop {
            match receiver.recv_timeout(std::time::Duration::from_millis(500)) {
                Ok(v) => match v {
                    MessageInput::Mouse(mouse_input) => {
                        Self::track_input_mouse(&mouse_input, &mut held_mouse_buttons);
                        handle_mouse(
                            &mouse_input.msg,
                            mouse_input.conn_id,
                            mouse_input.username,
                            mouse_input.argb,
                            mouse_input.simulate,
                            mouse_input.show_cursor,
                        );
                    }
                    MessageInput::ReleaseMouse => Self::release_input_mouse(&mut held_mouse_buttons),
                    MessageInput::Key((mut msg, press)) => {
                        // Set the press state to false, use `down` only in `handle_key()`.
                        msg.press = false;
                        if press {
                            msg.down = true;
                        }
                        handle_key(&msg);
                        if press {
                            msg.down = false;
                            handle_key(&msg);
                        }
                    }
                    MessageInput::Pointer((msg, id)) => {
                        handle_pointer(&msg, id);
                    }
                    MessageInput::BlockOn => {
                        let (ok, msg) = crate::platform::block_input(true);
                        if ok {
                            block_input_mode = true;
                        } else {
                            Self::send_block_input_error(
                                &tx,
                                back_notification::BlockInputState::BlkOnFailed,
                                msg,
                            );
                        }
                    }
                    MessageInput::BlockOff => {
                        let (ok, msg) = crate::platform::block_input(false);
                        if ok {
                            block_input_mode = false;
                        } else {
                            Self::send_block_input_error(
                                &tx,
                                back_notification::BlockInputState::BlkOffFailed,
                                msg,
                            );
                        }
                    }
                    #[cfg(all(feature = "flutter", feature = "plugin_framework"))]
                    #[cfg(not(any(target_os = "android", target_os = "ios")))]
                    MessageInput::BlockOnPlugin(_peer) => {
                        let (ok, _msg) = crate::platform::block_input(true);
                        if ok {
                            block_input_mode = true;
                        }
                        let _r = PLUGIN_BLOCK_INPUT_TX_RX
                            .0
                            .lock()
                            .unwrap()
                            .send(block_input_mode);
                    }
                    #[cfg(all(feature = "flutter", feature = "plugin_framework"))]
                    #[cfg(not(any(target_os = "android", target_os = "ios")))]
                    MessageInput::BlockOffPlugin(_peer) => {
                        let (ok, _msg) = crate::platform::block_input(false);
                        if ok {
                            block_input_mode = false;
                        }
                        let _r = PLUGIN_BLOCK_INPUT_TX_RX
                            .0
                            .lock()
                            .unwrap()
                            .send(block_input_mode);
                    }
                },
                Err(err) => {
                    #[cfg(not(any(target_os = "android", target_os = "ios")))]
                    if block_input_mode {
                        let _ = crate::platform::block_input(true);
                    }
                    if std_mpsc::RecvTimeoutError::Disconnected == err {
                        break;
                    }
                }
            }
        }
        #[cfg(target_os = "linux")]
        clear_remapped_keycode();
        Self::release_input_mouse(&mut held_mouse_buttons);
        log::debug!("Input thread exited");
    }
