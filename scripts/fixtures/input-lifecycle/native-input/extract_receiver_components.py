"""Production-only projections added after the original portable gate.

Historical negative snippets remain immutable template inputs. The functions
here always read the just-branded app source pinned by the caller.
"""
from pathlib import Path


def extract(out: Path, inputs: dict, method, function):
    def text(name):
        return inputs[name].decode('utf-8')

    def write(name, value):
        (out / 'harness/src' / name).write_bytes(value.encode('utf-8'))
        names.append(name)

    names = []
    kb = text('src/keyboard.rs')
    ui = text('src/ui_session_interface.rs')
    client = text('src/client.rs')
    common = text('src/common.rs')
    server = text('src/server/input_service.rs')
    mouse_ui = method(ui, '    pub fn send_mouse(') + '\n' + method(ui, '    fn swap_modifier_mouse(') + '\n'
    # These methods are in the real Session impl, embedded in the test seam.
    # Replace its frozen selected body explicitly rather than leaving a copy.
    native = (out / 'harness/src/native_conversion.rs').read_text(encoding='utf-8')
    previous = (out / 'harness/src/mouse_ui_selected.rs').read_text(encoding='utf-8')
    assert native.count(previous) == 1, 'mouse Session fixture anchor drift'
    write('native_conversion.rs', native.replace(previous, mouse_ui))
    write('mouse_ui_selected.rs', mouse_ui)
    write('mouse_wire_selected.rs', method(client, 'pub fn send_mouse(') + '\n')
    physical = method(kb, '    pub fn get_physical_modifiers_state(')
    write('mouse_physical_selected.rs', physical + '\n')
    write('mouse_constants_selected.rs', method(common, 'pub mod input {') + '\n')

    # Project the actual three desktop receiver cfg branches independently;
    # branch statement bodies are unchanged and OS effects are test recorders.
    a = server.index('    #[cfg(not(target_os = "macos"))]\n    let mut to_release = Vec::new();', server.index('pub fn handle_mouse_simulation_'))
    b = server.index('    match evt_type {', a)
    prefix = server[a:b]
    a2 = server.index('        MOUSE_TYPE_DOWN =>', b)
    b2 = server.index('        MOUSE_TYPE_WHEEL | MOUSE_TYPE_TRACKPAD => {', a2)
    arms = server[a2:b2]
    a3 = server.index('    #[cfg(not(target_os = "macos"))]\n    for key in to_release {', b2)
    suffix = server[a3:server.index('\n}', a3)]
    code = ''
    for platform in ['linux', 'windows', 'macos']:
        body = prefix + '    match evt_type {\n' + arms + '        _=>{}\n    }\n' + suffix
        for tag, enabled in [('target_os = "macos"', platform == 'macos'), ('not(target_os = "macos")', platform != 'macos'), ('windows', platform == 'windows')]:
            body = body.replace('#[cfg(' + tag + ')]', '#[cfg(' + ('all()' if enabled else 'any()') + ')]')
        code += 'pub fn ' + platform + '_mouse(evt:&MouseEvent,mut en:&mut Enigo) {let conn=0;let evt_type=evt.mask & MOUSE_TYPE_MASK;let buttons=evt.mask>>3;\n' + body + '\n}\n'
    code += '\n' + method(server, 'fn fix_modifier(') + '\n' + method(server, 'fn fix_modifiers(') + '\n'
    code += '\n' + '\n'.join(method(server, signature) for signature in ['fn mouse_button_from_mask(', 'fn owned_mouse_down(', 'fn owned_mouse_up(']) + '\n'
    write('mouse_server_selected.rs', code)
    write('scale_balanced_selected.rs', method(server, 'fn handle_scale(') + '\n' + method(server, 'fn get_modifier_state(') + '\n')
    windows_physical = physical
    for tag, enabled in [('target_os = "windows"', True), ('not(any(target_os = "windows", target_os = "android", target_os = "ios"))', False), ('any(target_os = "android", target_os = "ios")', False)]:
        windows_physical = windows_physical.replace('#[cfg(' + tag + ')]', '#[cfg(' + ('all()' if enabled else 'any()') + ')]')
    write('mouse_windows_physical_selected.rs', windows_physical + '\n')

    def windows_projection(value):
        choices = {'target_os = "windows"': True, 'target_os = "macos"': False, 'target_os = "linux"': False, 'not(target_os = "macos")': True, 'any(target_os = "linux", target_os = "windows")': True, 'any(target_os = "android", target_os = "ios")': False, 'not(any(target_os = "android", target_os = "ios"))': True, 'not(any(target_os = "ios"))': True}
        for tag, enabled in choices.items():
            value = value.replace('#[cfg(' + tag + ')]', '#[cfg(' + ('all()' if enabled else 'any()') + ')]')
        return value

    selected_names = ['map_keyboard_mode', 'try_fill_unicode', 'try_fill_win2win_hotkey', 'is_hot_key_modifiers_down', 'is_altgr', 'is_press', 'is_modifier', 'translate_keyboard_mode']
    map_fn = function(kb, '_map_keyboard_mode')
    map_prefix = map_fn[map_fn.index('{') + 1:map_fn.index('    #[cfg(target_os = "windows")]')]
    arm = method(map_fn, '        OS_LOWER_WINDOWS => {')
    map_body = arm[arm.index('{') + 1:-1]
    postfix = map_fn[map_fn.index('    key_event.set_chr(keycode as _);'):]
    map_only = 'fn _map_keyboard_mode(_peer:&str,event:&Event,mut key_event:KeyEvent)->Option<KeyEvent> {assert_eq!(_peer,OS_LOWER_WINDOWS);' + map_prefix + '\nlet keycode={' + map_body + '};\n' + postfix
    write('windows_modifiers_selected.rs', '\n\n'.join(windows_projection(function(kb, name)) for name in selected_names) + '\n\n' + map_only + '\n')

    def receiver_projection(value):
        for tag, enabled in [('windows', True), ('target_os = "macos"', False), ('target_os = "linux"', False)]:
            value = value.replace('#[cfg(' + tag + ')]', '#[cfg(' + ('all()' if enabled else 'any()') + ')]')
        return value

    write('scale_receiver_selected.rs', '\n\n'.join(receiver_projection(function(server, name)) for name in ['map_keyboard_mode', 'translate_process_code', 'simulate_win2win_hotkey', 'process_control_key']) + '\n')
    conn = text('src/server/connection.rs')
    portable = text('src/server/portable_service.rs')
    write('button_sink_selected.rs', '\n\n'.join(method(server, signature) for signature in ['fn mouse_button_from_mask(', 'fn owned_mouse_down(', 'fn owned_mouse_up(', 'pub fn release_all_owned_mouse_buttons(']) + '\n')
    write('button_queue_selected.rs', '\n\n'.join(method(conn, signature) for signature in ['    fn track_input_mouse(', '    fn release_input_mouse(', '    fn release_mouse_input(', '    fn handle_input(']) + '\n')
    write('button_message_selected.rs', method(conn, 'struct InputMouse {') + '\n' + method(conn, 'enum MessageInput {') + '\n')
    write('button_accept_selected.rs', method(conn, '    fn peer_keyboard_enabled(') + '\n' + method(conn, '    fn input_mouse(') + '\n')
    close = method(conn, '    async fn on_close(')
    close_prefix = close[close.index('{') + 1:close.index('        // If voice A,B')]
    write('button_close_selected.rs', 'async fn close(&mut self) {' + close_prefix + '\nPUBLISHED.lock().unwrap().push("close");}\n')
    a = conn.index('                                conn.keyboard = enabled;')
    b = conn.index('                                if let Some(s) = conn.server.upgrade()', a)
    write('button_permission_selected.rs', 'async fn permission(&mut self,enabled:bool) {let conn=self;' + conn[a:b] + '}\n')
    a = conn.index('                self.disable_keyboard = q == BoolOption::Yes;')
    b = conn.index('                if let Some(s) = self.server.upgrade()', a)
    write('button_disable_selected.rs', 'fn disable(&mut self,q:BoolOption) {' + conn[a:b] + '}\n')
    branch = method(portable, '                                    ConnCount(Some(n)) => {')
    branch = branch[branch.index('{') + 1:-1]
    branch = branch.replace('stream.send(&Data::DataPortableService(WillClose)).await.ok();', 'publish_will_close();').replace('break;', 'return;')
    write('button_helper_zero_selected.rs', 'fn helper_zero(n:usize) {' + branch + '}\n')
    a = portable.index('        // IPC failure/timeout exits need')
    b = portable.index('\n    }', a)
    tail = portable[a:b].replace('*EXIT.lock().unwrap() = true;', 'publish_exit();')
    write('button_helper_tail_selected.rs', 'fn helper_tail() {' + tail + '\n}\n')
    buttons = (out / 'harness/src/mouse_buttons.template.rs').read_text(encoding='utf-8')
    for name in ['button_queue_selected.rs', 'button_close_selected.rs', 'button_permission_selected.rs', 'button_disable_selected.rs', 'button_accept_selected.rs', 'button_queue_original.rs']:
        anchor = 'include!("' + name + '");'
        assert buttons.count(anchor) == 1, 'button fixture anchor drift: ' + name
        buttons = buttons.replace(anchor, (out / 'harness/src' / name).read_text(encoding='utf-8'))
    write('mouse_buttons.rs', buttons)
    # The exact historical Scale receiver is retained as a backward-compatibility
    # test, separately from the newly extracted current balanced receiver above.
    return names
