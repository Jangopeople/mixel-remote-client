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
