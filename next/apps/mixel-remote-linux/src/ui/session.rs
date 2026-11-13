//! The session on the technician's side: the remote screen (a GtkPicture drawn by GTK's GL renderer),
//! keyboard, mouse and scrolling while control is allowed, the toolbar (only what the other computer
//! offers: displays, privacy screen, block input, pointer, lock, lock when done, Ctrl+Alt+Del,
//! restart, sound, recording, invite a colleague, switch sides, renew, end) and the tools beside it
//! (chat, files, clipboard and typed text, print jobs).
use super::{dialogs, widgets::*};
use crate::{
    app::{App, State, Tool},
    format,
    i18n::{engine, t, tf},
    keymap,
    logic::{self, input},
};
use adw::prelude::*;
use gtk::{gdk, glib};
use std::{
    cell::{Cell, RefCell},
    collections::HashSet,
    rc::Rc,
};

pub struct SessionView {
    pub widget: gtk::Box,
    title: adw::WindowTitle,
    end: gtk::Button,
    toolbar: gtk::Box,
    toolbar_key: RefCell<String>,
    picture: gtk::Picture,
    wait: gtk::Box,
    wait_text: gtk::Label,
    notes: gtk::Box,
    notes_key: RefCell<String>,
    tools: gtk::Revealer,
    tool_box: gtk::Box,
    tool_key: RefCell<String>,
    chat_entry: gtk::Entry,
    text_entry: gtk::Entry,
    ai_address: gtk::Entry,
    ai_question: gtk::Entry,
    ai_answer: gtk::TextView,
    ticket: gtk::Entry,
}

/// What the picture's input handlers keep between events.
#[derive(Default)]
struct Pressed {
    buttons: HashSet<u32>,
    keys: HashSet<u16>,
    wheel: logic::Wheel,
}

impl SessionView {
    pub fn new(app: &Rc<App>) -> Self {
        let widget = vbox(0);
        let header = adw::HeaderBar::new();
        let title = adw::WindowTitle::new("", "");
        header.set_title_widget(Some(&title));
        let a = app.clone();
        let end = destructive(&t("End session"), move || a.end_session());
        end.set_tooltip_text(Some(&format!("{} (Ctrl+.)", t("End session"))));
        header.pack_end(&end);
        let a = app.clone();
        header.pack_end(&icon_button("view-fullscreen-symbolic", &t("Full screen"), move || {
            if let Some(ui) = a.ui.borrow().as_ref() {
                ui.toggle_fullscreen();
            }
        }));
        for (tool, icon, text) in [(Tool::Assistant, "help-browser-symbolic", t("AI assistant")), (Tool::Clipboard, "edit-paste-symbolic", t("Clipboard")), (Tool::Files, "folder-symbolic", t("Files")), (Tool::Chat, "mail-message-new-symbolic", t("Chat"))] {
            let a = app.clone();
            header.pack_end(&icon_button(icon, &text, move || a.set_tool(Some(tool))));
        }
        widget.append(&header);
        let toolbar = hbox(6);
        toolbar.add_css_class("toolbar");
        widget.append(&toolbar);

        let content = hbox(0);
        content.set_vexpand(true);
        let overlay = gtk::Overlay::new();
        overlay.set_hexpand(true);
        overlay.add_css_class("mixel-screen");
        let picture = gtk::Picture::builder().can_shrink(true).keep_aspect_ratio(true).focusable(true).hexpand(true).vexpand(true).accessible_role(gtk::AccessibleRole::Img).build();
        picture.update_property(&[gtk::accessible::Property::Label(&t("Remote screen"))]);
        picture.set_size_request(480, 320);
        overlay.set_child(Some(&picture));
        let wait = vbox(12);
        wait.set_halign(gtk::Align::Center);
        wait.set_valign(gtk::Align::Center);
        let spinner = gtk::Spinner::new();
        spinner.set_size_request(32, 32);
        spinner.start();
        wait.append(&spinner);
        let wait_text = gtk::Label::new(None);
        wait_text.add_css_class("mixel-wait");
        wait.append(&wait_text);
        overlay.add_overlay(&wait);
        let notes = vbox(4);
        notes.set_halign(gtk::Align::Start);
        notes.set_valign(gtk::Align::End);
        notes.set_margin_start(12);
        notes.set_margin_bottom(12);
        notes.set_margin_end(12);
        overlay.add_overlay(&notes);
        content.append(&overlay);
        let tools = gtk::Revealer::builder().transition_type(gtk::RevealerTransitionType::SlideLeft).build();
        let tool_box = vbox(12);
        tool_box.add_css_class("mixel-tools");
        tool_box.set_size_request(320, -1);
        tool_box.set_margin_top(14);
        tool_box.set_margin_bottom(14);
        tool_box.set_margin_start(14);
        tool_box.set_margin_end(14);
        let tool_frame = hbox(0);
        tool_frame.append(&gtk::Separator::new(gtk::Orientation::Vertical));
        let tool_scroll = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).child(&tool_box).build();
        tool_frame.append(&tool_scroll);
        tools.set_child(Some(&tool_frame));
        content.append(&tools);
        widget.append(&content);

        let chat_entry = entry(&t("Chat message"));
        let a = app.clone();
        chat_entry.connect_activate(move |entry| {
            a.send_chat(&entry.text());
            entry.set_text("");
        });
        let text_entry = entry(&t("Text to type on the remote computer"));
        let a = app.clone();
        text_entry.connect_activate(move |entry| {
            a.type_text(&entry.text());
            entry.set_text("");
        });
        let ai_address = entry(&t("Address of your AI server (empty: find it)"));
        let ai_question = entry(&t("Ask about this screen"));
        let a = app.clone();
        ai_question.connect_activate(move |entry| a.ai_ask(entry.text().trim().to_owned(), false));
        let ai_answer = gtk::TextView::builder().wrap_mode(gtk::WrapMode::WordChar).editable(true).top_margin(8).bottom_margin(8).left_margin(8).right_margin(8).height_request(160).build();
        ai_answer.add_css_class("mixel-transfer");
        ai_answer.update_property(&[gtk::accessible::Property::Label(&t("Answer"))]);
        let a = app.clone();
        ai_answer.buffer().connect_changed(move |buffer| {
            if !a.rendering.get() {
                a.set_ai_answer(buffer.text(&buffer.start_iter(), &buffer.end_iter(), false).to_string());
            }
        });
        let ticket = entry(&t("Ticket number"));
        let view = Self {
            ai_address,
            ai_question,
            ai_answer,
            ticket,
            widget,
            title,
            end,
            toolbar,
            toolbar_key: RefCell::new(String::new()),
            picture,
            wait,
            wait_text,
            notes,
            notes_key: RefCell::new(String::new()),
            tools,
            tool_box,
            tool_key: RefCell::new(String::new()),
            chat_entry,
            text_entry,
        };
        view.connect_input(app);
        view
    }

    pub fn set_frame(&self, texture: Option<&gdk::Texture>) {
        self.picture.set_paintable(texture);
    }

    fn connect_input(&self, app: &Rc<App>) {
        let pressed = Rc::new(RefCell::new(Pressed::default()));
        let picture = self.picture.clone();
        // Where the pointer is in the remote picture (0…1), aspect fit; clamped while a button is held.
        let point = {
            let picture = picture.clone();
            move |app: &App, x: f64, y: f64, clamp: bool| -> Option<(f64, f64)> {
                let (w, h) = app.state.borrow().session.size;
                logic::picture_point((f64::from(picture.width()), f64::from(picture.height())), (w as f64, h as f64), (x, y), clamp)
            }
        };
        let point = Rc::new(point);

        let motion = gtk::EventControllerMotion::new();
        let (a, p, held) = (app.clone(), point.clone(), pressed.clone());
        motion.connect_motion(move |_, x, y| {
            let (controllable, pointer_mode) = {
                let state = a.state.borrow();
                (state.controllable(), state.session.pointer_mode && state.remote.viewing())
            };
            if !(controllable || pointer_mode) {
                return;
            }
            if let Some((px, py)) = p(&a, x, y, !held.borrow().buttons.is_empty()) {
                a.move_pointer(px, py);
            }
        });
        picture.add_controller(motion);

        let click = gtk::GestureClick::new();
        click.set_button(0);
        let (a, p, held, focus) = (app.clone(), point.clone(), pressed.clone(), picture.clone());
        click.connect_pressed(move |gesture, presses, x, y| {
            focus.grab_focus();
            let (controllable, pointer_mode) = {
                let state = a.state.borrow();
                (state.controllable(), state.session.pointer_mode)
            };
            if !controllable || pointer_mode {
                return;
            }
            let gtk_button = gesture.current_button();
            let (Some(button), Some((px, py))) = (logic::engine_button(gtk_button), p(&a, x, y, false)) else { return };
            a.move_pointer(px, py);
            held.borrow_mut().buttons.insert(gtk_button);
            a.send_input(input::button(button, true, presses.clamp(1, 3) as u8));
            gesture.set_state(gtk::EventSequenceState::Claimed);
        });
        let (a, p, held) = (app.clone(), point.clone(), pressed.clone());
        click.connect_released(move |gesture, presses, x, y| {
            let gtk_button = gesture.current_button();
            if !held.borrow_mut().buttons.remove(&gtk_button) {
                return;
            }
            if let Some((px, py)) = p(&a, x, y, true) {
                a.move_pointer(px, py);
            }
            if let Some(button) = logic::engine_button(gtk_button) {
                a.send_input(input::button(button, false, presses.clamp(1, 3) as u8));
            }
        });
        picture.add_controller(click);

        let scroll = gtk::EventControllerScroll::new(gtk::EventControllerScrollFlags::BOTH_AXES);
        let (a, held) = (app.clone(), pressed.clone());
        scroll.connect_scroll(move |_, dx, dy| {
            if !a.state.borrow().controllable() {
                return glib::Propagation::Proceed;
            }
            if let Some((horizontal, vertical)) = held.borrow_mut().wheel.add(dx, dy) {
                a.send_input(input::wheel(horizontal, vertical));
            }
            glib::Propagation::Stop
        });
        picture.add_controller(scroll);

        // Keys travel by position (HID usage), so the remote computer's own layout applies.
        // Ctrl+Alt+Escape gives the keyboard back to this computer.
        let keys = gtk::EventControllerKey::new();
        let (a, held, focus) = (app.clone(), pressed.clone(), picture.clone());
        keys.connect_key_pressed(move |_, key, code, modifiers| {
            if !a.state.borrow().controllable() {
                return glib::Propagation::Proceed;
            }
            if key == gdk::Key::Escape && modifiers.contains(gdk::ModifierType::CONTROL_MASK | gdk::ModifierType::ALT_MASK) {
                release_all(&a, &held);
                if let Some(root) = focus.root() {
                    root.set_focus(None::<&gtk::Widget>);
                }
                return glib::Propagation::Stop;
            }
            let Some(usage) = keymap::usage(code) else { return glib::Propagation::Proceed };
            held.borrow_mut().keys.insert(usage);
            a.send_input(input::key(usage, true));
            glib::Propagation::Stop
        });
        let (a, held) = (app.clone(), pressed.clone());
        keys.connect_key_released(move |_, _, code, _| {
            let Some(usage) = keymap::usage(code) else { return };
            if held.borrow_mut().keys.remove(&usage) {
                a.send_input(input::key(usage, false));
            }
        });
        picture.add_controller(keys);

        let focus = gtk::EventControllerFocus::new();
        let (a, held) = (app.clone(), pressed.clone());
        focus.connect_leave(move |_| release_all(&a, &held));
        picture.add_controller(focus);
    }

    pub fn render(&self, app: &Rc<App>, state: &State, ui: &super::Ui) {
        let remote = &state.remote;
        let session = &state.session;
        self.title.set_title(&tf("Computer %@", &[&format::remote_id(remote.peer)]));
        let mut subtitle = if remote.control { t("View and control") } else { t("View only") };
        if session.fps > 0 {
            subtitle.push_str(&format!(" · {} × {} · {} fps", session.size.0, session.size.1, session.fps));
        }
        self.title.set_subtitle(&subtitle);
        self.end.set_sensitive(!session.stopping);
        self.wait.set_visible(!session.has_frame);
        self.wait_text.set_text(&if remote.phase == "outgoing" { t("Waiting for the other person to accept your request.") } else { t("Connecting securely…") });
        self.picture.set_cursor_from_name(Some(if session.pointer_mode { "crosshair" } else { "default" }));
        if session.has_frame && state.controllable() && !self.picture.has_focus() && self.picture.root().and_then(|root| root.focus()).is_none() {
            self.picture.grab_focus();
        }
        self.render_toolbar(app, state);
        self.render_notes(state);
        self.render_tools(app, state);
        let _ = ui;
    }

    fn render_toolbar(&self, app: &Rc<App>, state: &State) {
        let remote = &state.remote;
        let session = &state.session;
        let key = format!(
            "{:?}",
            ((remote.control, remote.displays, remote.display, remote.display_all, &remote.host_actions, &remote.recording), (remote.phase.as_str(), remote.switched, &remote.peer_features, session.privacy, session.block_input, session.lock_when_done), (session.pointer_mode, session.sound, session.restart_armed, session.recording_pending, session.has_frame, state.busy))
        );
        if *self.toolbar_key.borrow() == key {
            return;
        }
        self.toolbar_key.replace(key);
        clear(&self.toolbar);
        if remote.control && remote.displays > 1 {
            let names: Vec<String> = (0..remote.displays)
                .map(|index| if remote.display_all && index == remote.displays - 1 { t("All screens") } else { tf("Display %d", &[&(index + 1)]) })
                .collect();
            let a = app.clone();
            let pick = choice(&names, usize::from(remote.display), move |index| {
                if !a.rendering.get() {
                    a.select_display(index as u16);
                }
            });
            pick.update_property(&[gtk::accessible::Property::Label(&t("Display"))]);
            self.toolbar.append(&pick);
        }
        let busy = state.busy;
        let add = |widget: gtk::Button| {
            widget.add_css_class("flat");
            widget.set_sensitive(!busy && widget.is_sensitive());
            self.toolbar.append(&widget);
        };
        let offers = |name: &str| remote.control && remote.offers(name);
        if offers("privacy") {
            let a = app.clone();
            add(toggle_tool("view-conceal-symbolic", &t("Privacy screen"), session.privacy, move || a.toggle_privacy()));
        }
        if offers("block") {
            let a = app.clone();
            add(toggle_tool("action-unavailable-symbolic", &t("Block input"), session.block_input, move || a.toggle_block_input()));
        }
        if offers("pointer") {
            let a = app.clone();
            add(toggle_tool("find-location-symbolic", &t("Show pointer"), session.pointer_mode, move || a.toggle_pointer()));
        }
        if offers("lock") {
            let a = app.clone();
            add(icon_button("system-lock-screen-symbolic", &t("Lock computer"), move || a.lock_now()));
        }
        if offers("lockend") {
            let a = app.clone();
            add(toggle_tool("appointment-soon-symbolic", &t("Lock when done"), session.lock_when_done, move || a.toggle_lock_when_done()));
        }
        if offers("sas") {
            let a = app.clone();
            add(icon_button("input-keyboard-symbolic", &t("Ctrl+Alt+Del"), move || a.ctrl_alt_del()));
        }
        if offers("restart") {
            let a = app.clone();
            let text = if session.restart_armed { t("Click again to restart") } else { t("Restart computer") };
            let restart = icon_button("system-reboot-symbolic", &text, move || a.restart_computer());
            if session.restart_armed {
                restart.add_css_class("mixel-on");
            }
            add(restart);
        }
        if remote.offers("sound") {
            let a = app.clone();
            add(toggle_tool(if session.sound { "audio-volume-high-symbolic" } else { "audio-volume-muted-symbolic" }, &t("Sound"), session.sound, move || a.toggle_sound()));
        }
        if let Some(recording) = &remote.recording {
            let a = app.clone();
            let running = recording.running();
            let record = icon_button(if running { "media-playback-stop-symbolic" } else { "media-record-symbolic" }, &if running { t("Stop recording") } else { t("Record") }, move || a.toggle_recording());
            record.set_sensitive(!session.recording_pending && recording.phase != "saving" && session.has_frame);
            add(record);
        }
        if remote.phase == "active" && !remote.switched {
            let a = app.clone();
            add(icon_button("contact-new-symbolic", &t("Invite a colleague"), move || invite_dialog(&a)));
        }
        if state.can_switch_sides() {
            let a = app.clone();
            add(icon_button("object-flip-horizontal-symbolic", &t("Switch sides"), move || a.switch_sides()));
        } else if remote.offers("meet") {
            let a = app.clone();
            add(icon_button("video-display-symbolic", &t("Show my screen"), move || a.show_my_screen()));
        }
        if remote.control {
            // Key combinations this computer's desktop would keep for itself.
            let combos: [(String, u16, &[u16]); 6] = [
                (t("Ctrl+Alt+Del"), keymap::DELETE, &[keymap::CONTROL, keymap::ALT]),
                ("Alt+Tab".into(), keymap::TAB, &[keymap::ALT]),
                ("Alt+F4".into(), keymap::F4, &[keymap::ALT]),
                (t("Windows / Super key"), keymap::META, &[]),
                ("Ctrl+Shift+Esc".into(), keymap::ESCAPE, &[keymap::CONTROL, keymap::SHIFT]),
                (t("Print screen"), keymap::PRINT, &[]),
            ];
            let items = combos
                .into_iter()
                .map(|(title, usage, modifiers)| {
                    let a = app.clone();
                    Some((title, true, Box::new(move || a.press(usage, modifiers)) as Box<dyn Fn()>))
                })
                .collect();
            let keys = menu(&t("Keys"), items);
            keys.add_css_class("flat");
            keys.set_tooltip_text(Some(&t("Send a key combination")));
            self.toolbar.append(&keys);
        }
        let a = app.clone();
        add(icon_button("view-refresh-symbolic", &t("Renew connection"), move || a.renew()));
        let status = caption(&logic::recording_text(remote.recording.as_ref()));
        status.set_hexpand(true);
        status.set_xalign(1.0);
        self.toolbar.append(&status);
    }

    fn render_notes(&self, state: &State) {
        let session = &state.session;
        let wayland = state.remote.viewing() && state.remote.offers("wayland");
        let key = format!("{:?}", (&session.notice, &session.meeting_notice, &session.error, &state.error, wayland));
        if *self.notes_key.borrow() == key {
            return;
        }
        self.notes_key.replace(key);
        clear(&self.notes);
        let mut lines: Vec<(String, bool)> = Vec::new();
        if wayland {
            lines.push((t("This Linux computer uses Wayland: the privacy screen, blocking its keyboard and mouse and showing your pointer are not possible there. Ask the person to sign in with “Ubuntu on Xorg” for these."), false));
        }
        for text in [&session.notice, &session.meeting_notice] {
            if !text.is_empty() {
                lines.push((text.clone(), false));
            }
        }
        for text in [&session.error, &state.error] {
            if !text.is_empty() {
                lines.push((text.clone(), true));
            }
        }
        if lines.is_empty() {
            self.notes.set_visible(false);
            return;
        }
        let note = vbox(4);
        note.add_css_class("mixel-overlay-note");
        for (text, error) in lines {
            let line = label(&text);
            line.set_max_width_chars(80);
            if error {
                line.add_css_class("mixel-error");
            }
            note.append(&line);
        }
        self.notes.append(&note);
        self.notes.set_visible(true);
    }

    fn render_tools(&self, app: &Rc<App>, state: &State) {
        let session = &state.session;
        self.tools.set_reveal_child(session.tool.is_some());
        let assistant = &session.assistant;
        let key = format!("{:?}", ((session.tool, &session.chat, &session.files, &session.clipboard_result, state.remote.files, state.remote.clipboard), (state.remote.control, &state.remote.print_jobs, state.staff()), (assistant.connected, &assistant.models, &assistant.model, &assistant.status, assistant.busy, &assistant.ticket_status)));
        // The answer changes while it is typed in: only a new answer from the server is shown again.
        let shown = self.ai_answer.buffer();
        if shown.text(&shown.start_iter(), &shown.end_iter(), false).as_str() != assistant.answer {
            shown.set_text(&assistant.answer);
        }
        if *self.tool_key.borrow() == key {
            return;
        }
        self.tool_key.replace(key);
        keep_focus(&[self.chat_entry.clone().upcast(), self.text_entry.clone().upcast(), self.ai_address.clone().upcast(), self.ai_question.clone().upcast(), self.ticket.clone().upcast(), self.ai_answer.clone().upcast()], || {
            clear(&self.tool_box);
            let Some(tool) = session.tool else { return };
            let title = match tool {
                Tool::Chat => t("Chat"),
                Tool::Files => t("Files"),
                Tool::Clipboard => t("Clipboard"),
                Tool::Assistant => t("AI assistant"),
            };
            let header = hbox(8);
            let heading = heading(&title);
            heading.set_hexpand(true);
            header.append(&heading);
            let a = app.clone();
            let close = icon_button("window-close-symbolic", &t("Close"), move || a.set_tool(None));
            close.add_css_class("flat");
            header.append(&close);
            self.tool_box.append(&header);
            match tool {
                Tool::Chat => self.chat(app, state),
                Tool::Files => self.files(app, state),
                Tool::Clipboard => self.clipboard(app, state),
                Tool::Assistant => self.assistant(app, state),
            }
        });
    }

    fn chat(&self, app: &Rc<App>, state: &State) {
        let messages = vbox(6);
        messages.set_vexpand(true);
        for message in &state.session.chat {
            let bubble = selectable(&message.text);
            bubble.add_css_class("mixel-bubble");
            if message.mine {
                bubble.add_css_class("mine");
                bubble.set_halign(gtk::Align::End);
                bubble.set_margin_start(30);
            } else {
                bubble.set_halign(gtk::Align::Start);
                bubble.set_margin_end(30);
            }
            messages.append(&bubble);
        }
        if state.session.chat.is_empty() {
            messages.append(&caption(&t("Messages appear here.")));
        }
        self.tool_box.append(&messages);
        let row = hbox(8);
        row.append(&adopt(&self.chat_entry));
        let (a, entry) = (app.clone(), self.chat_entry.clone());
        row.append(&button(&t("Send"), move || {
            a.send_chat(&entry.text());
            entry.set_text("");
        }));
        self.tool_box.append(&row);
    }

    fn files(&self, app: &Rc<App>, state: &State) {
        if !state.remote.files {
            self.tool_box.append(&dim(&t("File transfer was not allowed for this session.")));
            return;
        }
        let view = state.session.files.clone();
        let a = app.clone();
        let send = button(&t("Send a file…"), move || a.send_file());
        send.set_sensitive(view.as_ref().is_some_and(|view| view.connected && !view.outgoing.as_ref().is_some_and(|transfer| transfer.active())));
        send.set_halign(gtk::Align::Start);
        self.tool_box.append(&send);
        let Some(view) = view else { return };
        for (transfer, outgoing) in [(view.outgoing.clone(), true), (view.incoming.clone(), false)] {
            let Some(transfer) = transfer else { continue };
            let item = vbox(4);
            item.add_css_class("mixel-transfer");
            let name = label(&format!("{} {}", if outgoing { "↑" } else { "↓" }, transfer.name));
            name.set_ellipsize(gtk::pango::EllipsizeMode::Middle);
            item.append(&name);
            let bar = gtk::ProgressBar::new();
            bar.set_fraction(transfer.progress());
            item.append(&bar);
            let row = hbox(8);
            let size = caption(&format!("{} / {}", format::bytes(transfer.offset), format::bytes(transfer.size)));
            size.set_hexpand(true);
            row.append(&size);
            if matches!(transfer.phase.as_str(), "transferring" | "waiting" | "paused" | "offered") {
                let (a, id) = (app.clone(), transfer.id.clone());
                row.append(&button(&t("Cancel"), move || a.cancel_file(id.clone())));
            }
            item.append(&row);
            if !transfer.message.is_empty() {
                item.append(&caption(&engine(&transfer.message)));
            }
            if !outgoing && transfer.phase == "offered" {
                let row = hbox(8);
                let (a, chosen) = (app.clone(), transfer.clone());
                row.append(&primary(&t("Accept and save as…"), move || a.accept_file(chosen.clone())));
                let (a, id) = (app.clone(), transfer.id.clone());
                row.append(&button(&t("Decline"), move || a.cancel_file(id.clone())));
                item.append(&row);
            }
            self.tool_box.append(&item);
        }
        if !view.message.is_empty() {
            self.tool_box.append(&caption(&engine(&view.message)));
        }
    }

    fn clipboard(&self, app: &Rc<App>, state: &State) {
        if state.remote.clipboard {
            let a = app.clone();
            let push = button(&t("Send my clipboard"), move || a.clipboard(true));
            push.set_halign(gtk::Align::Start);
            self.tool_box.append(&push);
            let a = app.clone();
            let pull = button(&t("Get remote clipboard"), move || a.clipboard(false));
            pull.set_halign(gtk::Align::Start);
            self.tool_box.append(&pull);
            append_message(&self.tool_box, &state.session.clipboard_result, false);
        } else {
            self.tool_box.append(&dim(&t("The clipboard was not allowed for this session.")));
        }
        if state.remote.control {
            self.tool_box.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
            self.tool_box.append(&heading(&t("Send text")));
            self.tool_box.append(&adopt(&self.text_entry));
            let (a, entry) = (app.clone(), self.text_entry.clone());
            let send = button(&t("Send"), move || {
                a.type_text(&entry.text());
                entry.set_text("");
            });
            send.set_halign(gtk::Align::Start);
            self.tool_box.append(&send);
        }
        if !state.remote.print_jobs.is_empty() {
            self.tool_box.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
            self.tool_box.append(&heading(&t("Print jobs")));
            for path in &state.remote.print_jobs {
                let row = hbox(8);
                let name = label(std::path::Path::new(path).file_name().and_then(|name| name.to_str()).unwrap_or(path));
                name.set_hexpand(true);
                name.set_ellipsize(gtk::pango::EllipsizeMode::Middle);
                row.append(&name);
                let (a, chosen) = (app.clone(), path.clone());
                row.append(&button(&t("Print"), move || a.print_job(chosen.clone(), false)));
                let (a, chosen) = (app.clone(), path.clone());
                row.append(&button(&t("Open"), move || a.print_job(chosen.clone(), true)));
                self.tool_box.append(&row);
            }
        }
    }
}

impl SessionView {
    fn assistant(&self, app: &Rc<App>, state: &State) {
        let assistant = &state.session.assistant;
        self.tool_box.append(&dim(&t("Your company's own AI server explains the screen, suggests steps and writes the ticket note. The picture is sent only to that server.")));
        if self.ai_address.text().is_empty() && !assistant.address.is_empty() {
            self.ai_address.set_text(&assistant.address);
        }
        let row = hbox(8);
        row.append(&adopt(&self.ai_address));
        let (a, address) = (app.clone(), self.ai_address.clone());
        let model = assistant.model.clone();
        row.append(&button(&t("Connect"), move || a.ai_connect(address.text().trim().to_owned(), model.clone(), true)));
        self.tool_box.append(&row);
        if assistant.models.len() > 1 {
            let models = assistant.models.clone();
            let selected = models.iter().position(|model| *model == assistant.model).unwrap_or(0);
            let (a, address) = (app.clone(), assistant.address.clone());
            let names = models.clone();
            let pick = choice(&names, selected, move |index| {
                if !a.rendering.get() {
                    a.ai_connect(address.clone(), models.get(index).cloned().unwrap_or_default(), false);
                }
            });
            self.tool_box.append(&field(&t("Model"), &pick));
        }
        append_message(&self.tool_box, &assistant.status, false);
        self.tool_box.append(&adopt(&self.ai_question));
        self.ai_question.set_sensitive(assistant.connected);
        let row = hbox(8);
        let (a, question) = (app.clone(), self.ai_question.clone());
        let ask = primary(&t("Ask"), move || a.ai_ask(question.text().trim().to_owned(), false));
        ask.set_sensitive(assistant.connected && !assistant.busy);
        row.append(&ask);
        let (a, question) = (app.clone(), self.ai_question.clone());
        let note = button(&t("Write the ticket note"), move || a.ai_ask(question.text().trim().to_owned(), true));
        note.set_sensitive(assistant.connected && !assistant.busy);
        row.append(&note);
        self.tool_box.append(&row);
        self.tool_box.append(&adopt(&self.ai_answer));
        let (a, answer) = (app.clone(), self.ai_answer.clone());
        let copy = button(&t("Copy"), move || {
            let buffer = answer.buffer();
            if let Some(shell) = a.shell() {
                shell.copy(&buffer.text(&buffer.start_iter(), &buffer.end_iter(), false));
                shell.notify(&t("Copied"));
            }
        });
        copy.set_halign(gtk::Align::Start);
        self.tool_box.append(&copy);
        if state.staff() {
            self.tool_box.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
            self.tool_box.append(&heading(&t("ISM ticket")));
            let row = hbox(8);
            row.append(&adopt(&self.ticket));
            let (a, ticket, answer) = (app.clone(), self.ticket.clone(), self.ai_answer.clone());
            row.append(&button(&t("Save in ticket"), move || {
                let buffer = answer.buffer();
                a.ticket_note(ticket.text().to_string(), buffer.text(&buffer.start_iter(), &buffer.end_iter(), false).to_string());
            }));
            self.tool_box.append(&row);
            append_message(&self.tool_box, &assistant.ticket_status, false);
        }
    }
}

fn release_all(app: &Rc<App>, held: &Rc<RefCell<Pressed>>) {
    let mut held = held.borrow_mut();
    if held.buttons.is_empty() && held.keys.is_empty() {
        return;
    }
    held.buttons.clear();
    held.keys.clear();
    held.wheel.clear();
    drop(held);
    app.send_input(input::release_all());
}

/// Invite a colleague into this session: by the Remote ID of their computer, or with a meeting link.
fn invite_dialog(app: &Rc<App>) {
    let Some(parent) = app.ui.borrow().as_ref().map(|ui| ui.window.clone()) else { return };
    let (dialog, content, actions) = dialogs::window(&parent, &t("Invite a colleague"), 460);
    let native = app.state.borrow().remote.peer_features.iter().any(|feature| feature == "join");
    let device = id_entry(&t("Or the Remote ID of their computer"));
    let control = gtk::CheckButton::with_label(&t("Allow viewing and control"));
    let status = vbox(6);
    if native {
        content.append(&dim(&t("Your colleague gets the invitation in Mixel Remote and joins this session. The person at the computer is asked first, unless unattended access is set up.")));
        content.append(&field(&t("Or the Remote ID of their computer"), &device));
        content.append(&control);
    } else {
        content.append(&dim(&t("The other computer cannot take a second technician directly. Use a meeting link instead.")));
    }
    content.append(&status);
    // Follow the invitation's answer and the meeting link while the dialog is open.
    let shown = Rc::new(Cell::new(String::new()));
    let (a, s, d, last) = (app.clone(), status.clone(), dialog.clone(), shown.clone());
    glib::timeout_add_local(std::time::Duration::from_millis(500), move || {
        if !d.is_visible() {
            return glib::ControlFlow::Break;
        }
        let state = a.state.borrow();
        let key = format!("{:?}", (&state.remote.invite_message, &state.session.invite_link, &state.session.error));
        if last.replace(key.clone()) != key {
            clear(&s);
            if let Some(message) = &state.remote.invite_message {
                append_message(&s, &engine(message), false);
            }
            if let Some(link) = &state.session.invite_link {
                let row = hbox(8);
                let text = monospace(&link.invite);
                text.set_hexpand(true);
                row.append(&text);
                let (a2, value) = (a.clone(), link.invite.clone());
                row.append(&icon_button("edit-copy-symbolic", &t("Copy"), move || {
                    if let Some(shell) = a2.shell() {
                        shell.copy(&value);
                        shell.notify(&t("Copied"));
                    }
                }));
                s.append(&field(&t("Meeting link"), &row));
            }
            append_message(&s, &state.session.error, true);
        }
        glib::ControlFlow::Continue
    });
    let a = app.clone();
    let meeting = link(&t("Use a meeting link instead"), move || a.meeting_link());
    actions.append(&meeting);
    actions.append(&spacer());
    let close = dialog.clone();
    actions.append(&button(&t("Close"), move || close.close()));
    if native {
        let (a, device, control) = (app.clone(), device.clone(), control.clone());
        actions.append(&primary(&t("Send invitation"), move || match format::parse_remote_id(&device.text()) {
            Some(id) => a.invite_colleague(id, control.is_active()),
            None => a.edit(|state| state.session.error = t("Enter a nine-digit connection ID")),
        }));
    }
    actions.set_halign(gtk::Align::Fill);
    dialog.present();
}
