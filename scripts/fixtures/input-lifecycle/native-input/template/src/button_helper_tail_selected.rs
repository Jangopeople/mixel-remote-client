fn helper_tail() {        // IPC failure/timeout exits need the same owned-only effect cleanup.
        crate::input_service::release_all_owned_mouse_buttons();
        publish_exit();
}
