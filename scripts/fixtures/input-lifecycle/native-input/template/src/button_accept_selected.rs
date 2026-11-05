    fn peer_keyboard_enabled(&self) -> bool {
        self.keyboard && !self.disable_keyboard
    }
    fn input_mouse(
        &self,
        msg: MouseEvent,
        conn_id: i32,
        username: String,
        argb: u32,
        simulate: bool,
        show_cursor: bool,
    ) {
        self.tx_input
            .send(MessageInput::Mouse(InputMouse {
                msg,
                conn_id,
                username,
                argb,
                simulate,
                show_cursor,
            }))
            .ok();
    }
