"""Extract complete production Session and Remote component bodies."""


def extract(out, inputs, method):
    def write(name, value):
        (out / 'harness/src' / name).write_bytes(value.encode('utf-8'))

    ui = inputs['src/ui_session_interface.rs'].decode('utf-8')
    methods = [method(ui, signature) for signature in ['    pub fn send_key_event(', '    pub fn send_native_key_event(', '    pub fn invalidate_keyboard_input_source(', '    fn send(&self, data: Data)']]
    write('session_methods.rs', '\n\n'.join(methods) + '\n')
    io = inputs['src/client/io_loop.rs'].decode('utf-8')
    first = io.index('            Data::KeyboardSourceChanged(epoch) => {')
    last = io.index('            Data::Message(msg) => {', first)
    arms = io[first:last]
    write('remote_input_arms.rs', arms)
    first = io.index('                                // The IO loop is the sole owner')
    last = io.index('                                #[cfg(feature = "flutter")]', first)
    permission = io[first:last]
    write('remote_permission_body.rs', permission)
    # The match arms must be composed into a method to compile as Rust. Only
    # the transport and executor seam outside the verbatim arms is test code.
    code = '''    fn process(&mut self, data:Data, peer:&mut Peer)->bool {
        run(async {
            match data {
''' + arms + '''                Data::Message(message) => { if peer.send(&message).await.is_err() { return false; } },
            }
            true
        })
    }
    fn permission(&mut self, enabled:bool, peer:&mut Peer)->bool {
        struct Permission {enabled:bool}
        let p=Permission {enabled};
        run(async {
''' + permission + '''            true
        })
    }
'''
    write('remote_methods.rs', code)
    template = (out / 'harness/src/session_component.template.rs').read_text(encoding='utf-8')
    assert template.count('include!("session_methods.rs");') == 1
    assert template.count('include!("remote_methods.rs");') == 1
    template = template.replace('include!("session_methods.rs");', '\n'.join(methods))
    template = template.replace('include!("remote_methods.rs");', code)
    write('session_component.rs', template)
    return ['session_methods.rs', 'remote_input_arms.rs', 'remote_permission_body.rs', 'remote_methods.rs', 'session_component.rs']
