async fn permission(&mut self,enabled:bool) {let conn=self;                                conn.keyboard = enabled;
                                #[cfg(not(any(target_os = "android", target_os = "ios")))]
                                if !enabled { conn.release_mouse_input(); }
                                conn.send_permission(Permission::Keyboard, enabled).await;
}
