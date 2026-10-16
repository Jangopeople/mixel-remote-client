struct InputMouse {
    msg: MouseEvent,
    conn_id: i32,
    username: String,
    argb: u32,
    simulate: bool,
    show_cursor: bool,
}
enum MessageInput {
    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    Mouse(InputMouse),
    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    Key((KeyEvent, bool)),
    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    ReleaseMouse,
    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    Pointer((PointerDeviceEvent, i32)),
    BlockOn,
    BlockOff,
    #[cfg(all(feature = "flutter", feature = "plugin_framework"))]
    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    BlockOnPlugin(String),
    #[cfg(all(feature = "flutter", feature = "plugin_framework"))]
    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    BlockOffPlugin(String),
}
