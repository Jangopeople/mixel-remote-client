async fn close(&mut self) {
        if self.closed {
            return;
        }
        self.closed = true;
        #[cfg(not(any(target_os = "android", target_os = "ios")))]
        self.release_mouse_input();

PUBLISHED.lock().unwrap().push("close");}
