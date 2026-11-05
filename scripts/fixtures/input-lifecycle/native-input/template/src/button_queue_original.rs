    fn handle_input_original(receiver: std_mpsc::Receiver<MessageInput>, tx: Sender) {
        let mut block_input_mode = false;
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
                    MessageInput::ReleaseMouse => {},
                    MessageInput::Mouse(mouse_input) => {
                        handle_mouse(
                            &mouse_input.msg,
                            mouse_input.conn_id,
                            mouse_input.username,
                            mouse_input.argb,
                            mouse_input.simulate,
                            mouse_input.show_cursor,
                        );
                    }
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
        log::debug!("Input thread exited");
    }
