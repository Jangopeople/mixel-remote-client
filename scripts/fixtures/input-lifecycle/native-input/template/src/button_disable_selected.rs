fn disable(&mut self,q:BoolOption) {                self.disable_keyboard = q == BoolOption::Yes;
                #[cfg(not(any(target_os = "android", target_os = "ios")))]
                if self.disable_keyboard { self.release_mouse_input(); }
}
