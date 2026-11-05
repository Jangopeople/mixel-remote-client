    pub fn send_key_event(&self, evt: &KeyEvent) {
        // mode: legacy(0), map(1), translate(2), auto(3)

        let mut msg = evt.clone();
        self.swap_modifier_key(&mut msg);
        let mut msg_out = Message::new();
        msg_out.set_key_event(msg);
        self.send(Data::Message(msg_out));
    }

    pub fn send_native_key_event(&self, evt: &KeyEvent, modifiers: Option<Vec<KeyEvent>>) {
        use crate::client::keyboard_state::{KeyboardInput, KeyboardOrigin};
        let mut event = evt.clone();
        self.swap_modifier_key(&mut event);
        let modifiers = modifiers.map(|events| events.into_iter().map(|mut event| {
            self.swap_modifier_key(&mut event);
            event
        }).collect());
        let permitted = self.server_keyboard_enabled.read().unwrap();
        let input = KeyboardInput {
            event,
            origin: KeyboardOrigin::Native,
            epoch: self.keyboard_epoch.load(Ordering::SeqCst),
            permitted: *permitted,
            modifiers,
        };
        self.send(Data::KeyboardInput(input));
    }

    pub fn invalidate_keyboard_input_source(&self) {
        // Hold the same lock used by input enqueue until the barrier is queued.
        // This rotates ownership; it does not alter server permission.
        let _permission = self.server_keyboard_enabled.write().unwrap();
        let epoch = self.keyboard_epoch.fetch_add(1, Ordering::SeqCst).wrapping_add(1);
        self.send(Data::KeyboardSourceChanged(epoch));
    }

    fn send(&self, data: Data) {
        use crate::client::keyboard_state::{KeyboardInput, KeyboardOrigin};
        // Stamp every UI KeyEvent at the common Session enqueue boundary,
        // including legacy input_string/password paths. Denied and old queued
        // events cannot become new input when the grant is renewed.
        let data = match data {
            Data::Message(mut message) if matches!(message.union, Some(message::Union::KeyEvent(_))) => {
                let Some(message::Union::KeyEvent(event)) = message.union.take() else { unreachable!() };
                let permitted = self.server_keyboard_enabled.read().unwrap();
                Data::KeyboardInput(KeyboardInput {
                    event,
                    origin: KeyboardOrigin::Ui,
                    epoch: self.keyboard_epoch.load(Ordering::SeqCst),
                    permitted: *permitted,
                    modifiers: None,
                })
            }
            Data::Message(message) if crate::client::keyboard_state::scoped_pointer_input(&message) => {
                let permitted = self.server_keyboard_enabled.read().unwrap();
                Data::PointerInput((message, self.keyboard_epoch.load(Ordering::SeqCst), *permitted))
            }
            data => data,
        };
        if let Some(sender) = self.sender.read().unwrap().as_ref() {
            sender.send(data).ok();
        }
    }
