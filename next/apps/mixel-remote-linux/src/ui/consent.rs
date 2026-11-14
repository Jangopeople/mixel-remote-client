//! The person at this computer decides about a connection request: which screen, and what the
//! technician may do (keyboard and mouse, clipboard, files, chat, recording). Nothing is allowed
//! before Allow. Shown over everything, also when the window was hidden in the tray.
use super::widgets::*;
use crate::{
    app::{App, State},
    format,
    i18n::{engine, t, tf},
};
use adw::prelude::*;
use std::{cell::RefCell, rc::Rc};

pub struct Consent {
    window: RefCell<Option<adw::Window>>,
    body: gtk::Box,
    key: RefCell<String>,
}

impl Consent {
    pub fn new(_app: &Rc<App>) -> Self {
        let body = vbox(16);
        body.set_margin_top(6);
        body.set_margin_bottom(24);
        body.set_margin_start(24);
        body.set_margin_end(24);
        Self { window: RefCell::new(None), body, key: RefCell::new(String::new()) }
    }

    pub fn rebuild(&self, _app: &Rc<App>) {
        self.key.replace(String::new());
    }

    pub fn render(&self, app: &Rc<App>, state: &State, parent: &adw::ApplicationWindow) {
        if !state.asking() {
            if let Some(window) = self.window.take() {
                window.destroy();
            }
            return;
        }
        if self.window.borrow().is_none() {
            let window = adw::Window::builder().modal(true).transient_for(parent).title(t("Someone wants to connect")).default_width(500).deletable(false).build();
            let outer = vbox(0);
            let header = adw::HeaderBar::new();
            header.add_css_class("flat");
            header.set_show_end_title_buttons(false);
            outer.append(&header);
            if let Some(parent) = self.body.parent().and_then(|old| old.downcast::<gtk::Box>().ok()) {
                parent.remove(&self.body);
            }
            outer.append(&self.body);
            window.set_content(Some(&outer));
            parent.present();
            window.present();
            self.window.replace(Some(window));
            self.key.replace(String::new());
        }
        let host = &state.host;
        let key = format!("{:?}", (host, &state.remote.peer, state.remote.input_access, &state.error, state.busy, &state.linux, state.can_accept(), state.control_available()));
        if *self.key.borrow() == key {
            return;
        }
        self.key.replace(key);
        self.build(app, state);
    }

    fn build(&self, app: &Rc<App>, state: &State) {
        let body = &self.body;
        // `clear` tells the dialog first that its default button (Allow) goes away with the old content.
        clear(body);
        let host = &state.host;
        let head = hbox(12);
        head.append(&mark(40));
        let text = vbox(2);
        let title = gtk::Label::builder().label(t("Someone wants to connect")).xalign(0.0).wrap(true).accessible_role(gtk::AccessibleRole::Heading).build();
        title.add_css_class("title-2");
        text.append(&title);
        text.append(&dim(&tf("Device %@ requests to connect. Its enrolled identity has been verified with Mixel.", &[&format::remote_id(state.remote.peer)])));
        head.append(&text);
        body.append(&head);
        if !host.screen_access || host.sources.is_empty() {
            append_message(body, &t("No screen of this computer can be shared right now. Sign in to a desktop session (X11, or Wayland with the system's sharing dialog) and try again."), false);
        } else {
            let names: Vec<String> = host.sources.iter().map(|source| engine(&source.name)).collect();
            let ids: Vec<String> = host.sources.iter().map(|source| source.id.clone()).collect();
            let selected = ids.iter().position(|id| *id == host.source).unwrap_or(0);
            let a = app.clone();
            body.append(&field(&t("Screen to share"), &choice(&names, selected, move |index| {
                let id = ids.get(index).cloned().unwrap_or_default();
                a.host_edit(|host| host.source = id);
            })));
            if host.selected().is_some_and(|source| source.full_display) {
                let a = app.clone();
                body.append(&check(&t("I confirm sharing this whole display"), host.confirm_display, move |on| a.host_edit(|host| host.confirm_display = on)));
            }
            if state.linux.wayland {
                body.append(&caption(&t("Wayland: after Allow, the system asks you once more which screen to share.")));
            }
        }
        let options = vbox(8);
        let a = app.clone();
        let control = check(&t("Keyboard & mouse"), host.allow_control, move |on| a.host_edit(|host| host.allow_control = on));
        control.set_sensitive(state.control_available());
        options.append(&control);
        if !state.remote.input_access {
            options.append(&caption(&t("Keyboard and mouse cannot be shared in this desktop session.")));
        }
        for (text, value, set) in [
            (t("Clipboard"), host.allow_clipboard, (|host: &mut crate::app::HostState, on| host.allow_clipboard = on) as fn(&mut crate::app::HostState, bool)),
            (t("Files"), host.allow_files, |host, on| host.allow_files = on),
            (t("Chat"), host.allow_chat, |host, on| host.allow_chat = on),
            (t("Allow recording of this session"), host.allow_recording, |host, on| host.allow_recording = on),
        ] {
            let a = app.clone();
            options.append(&check(&text, value, move |on| a.host_edit(|host| set(host, on))));
        }
        body.append(&options);
        append_message(body, &host.error, true);
        append_message(body, &state.error, true);
        let actions = hbox(8);
        actions.set_halign(gtk::Align::End);
        let a = app.clone();
        let decline = button(&t("Decline"), move || a.decide(false));
        actions.append(&decline);
        let a = app.clone();
        let allow = primary(&if host.allow_control && state.control_available() { t("Allow with control") } else { t("Allow view only") }, move || a.decide(true));
        allow.set_sensitive(state.can_accept());
        actions.append(&allow);
        body.append(&actions);
        if let Some(window) = self.window.borrow().as_ref() {
            window.set_default_widget(Some(&allow));
        }
    }
}
