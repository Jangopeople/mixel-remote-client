    fn process(&mut self, data:Data, peer:&mut Peer)->bool {
        run(async {
            match data {
            Data::KeyboardSourceChanged(epoch) => {
                // A later source change or permission revoke supersedes this
                // queued barrier; only the exact current epoch can resume.
                if epoch == self.handler.keyboard_epoch.load(Ordering::SeqCst) {
                    let releases = self.keyboard_state.change_source(epoch);
                    if let Some(message) = self.keyboard_state.take_scale_release() {
                        if peer.send(&message).await.is_err() { return false; }
                    }
                    for event in releases {
                        let mut message = Message::new();
                        message.set_key_event(event);
                        if peer.send(&message).await.is_err() {
                            return false;
                        }
                    }
                }
            }
            Data::KeyboardInput(input) => {
                for event in self.keyboard_state.prepare_input(input) {
                    let mut message = Message::new();
                    message.set_key_event(event);
                    if peer.send(&message).await.is_err() {
                        return false;
                    }
                }
            }
            Data::PointerInput((message, epoch, permitted)) => {
                self.keyboard_state.set_windows_peer(self.handler.peer_platform().eq_ignore_ascii_case("Windows"));
                if let Some(message) = self.keyboard_state.prepare_pointer(message, epoch, permitted) {
                    if peer.send(&message).await.is_err() { return false; }
                    for event in self.keyboard_state.take_scale_controls() {
                        let mut message = Message::new();
                        message.set_key_event(event);
                        if peer.send(&message).await.is_err() { return false; }
                    }
                }
            }
                Data::Message(message) => { if peer.send(&message).await.is_err() { return false; } },
            }
            true
        })
    }
    fn permission(&mut self, enabled:bool, peer:&mut Peer)->bool {
        struct Permission {enabled:bool}
        let p=Permission {enabled};
        run(async {
                                // The IO loop is the sole owner of outgoing key
                                // order. Release this connection's old holds before
                                // publishing a renewed grant to either input source.
                                let releases = self.keyboard_state.set_permission(p.enabled);
                                if !p.enabled {
                                    let mut enabled = self.handler.server_keyboard_enabled.write().unwrap();
                                    if *enabled {
                                        self.handler.keyboard_epoch.fetch_add(1, Ordering::SeqCst);
                                    }
                                    self.keyboard_state.adopt_epoch(self.handler.keyboard_epoch.load(Ordering::SeqCst));
                                    *enabled = false;
                                }
                                if let Some(message) = self.keyboard_state.take_scale_release() {
                                    if peer.send(&message).await.is_err() { return false; }
                                }
                                for event in self.keyboard_state.take_button_releases() {
                                    let mut message = Message::new();
                                    message.set_mouse_event(event);
                                    if peer.send(&message).await.is_err() {
                                        return false;
                                    }
                                }
                                for event in releases {
                                    let mut message = Message::new();
                                    message.set_key_event(event);
                                    if peer.send(&message).await.is_err() {
                                        return false;
                                    }
                                }
                                if p.enabled {
                                    let mut enabled = self.handler.server_keyboard_enabled.write().unwrap();
                                    self.keyboard_state.adopt_epoch(self.handler.keyboard_epoch.load(Ordering::SeqCst));
                                    *enabled = true;
                                }
            true
        })
    }
