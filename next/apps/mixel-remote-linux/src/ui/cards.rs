//! The two floating cards over the window: the connecting card (four steps, Cancel, "Open in window
//! instead", or the reason it failed) and the "session is ready" notice with Open.
use super::{devices::connect_step_name, widgets::*};
use crate::{
    app::{App, State},
    format,
    i18n::{t, tf},
    workspace::{Attempt, OpenIn, WhenReady},
};
use gtk::prelude::*;
use std::rc::Rc;

fn card_frame() -> gtk::Box {
    let frame = vbox(10);
    frame.add_css_class("mixel-float");
    frame.set_size_request(360, -1);
    frame.set_halign(gtk::Align::End);
    frame
}

pub fn attempt_card(app: &Rc<App>, state: &State, attempt: &Attempt) -> gtk::Box {
    let frame = card_frame();
    let failed = !attempt.failure.is_empty();
    let head = hbox(8);
    head.append(&gtk::Image::from_icon_name(if failed { "dialog-warning-symbolic" } else { "network-transmit-receive-symbolic" }));
    let title = gtk::Label::new(Some(&if failed { t("Could not connect") } else { t("Connecting") }));
    title.add_css_class("heading");
    head.append(&title);
    if !failed {
        let hint = if attempt.in_window || state.workspace.open_in == OpenIn::Window {
            t("it opens in its own window")
        } else if state.workspace.when_ready == WhenReady::Open {
            t("it opens as a tab")
        } else {
            t("keep working, it opens as a tab")
        };
        let hint = dim(&format!("· {hint}"));
        hint.set_wrap(true);
        hint.set_hexpand(true);
        head.append(&hint);
    }
    frame.append(&head);
    let device = hbox(10);
    device.append(&device_tile(format::os_icon(&attempt.os), if failed { "off" } else { "busy" }));
    let names = vbox(0);
    names.set_valign(gtk::Align::Center);
    let name = gtk::Label::new(Some(&attempt.name));
    name.add_css_class("heading");
    name.set_xalign(0.0);
    names.append(&name);
    let id = gtk::Label::new(Some(&format::remote_id(Some(attempt.peer))));
    id.add_css_class("monospace");
    id.add_css_class("dim-label");
    id.set_xalign(0.0);
    names.append(&id);
    device.append(&names);
    frame.append(&device);
    for index in 0..4u8 {
        let row = hbox(10);
        let name = connect_step_name(index, attempt.unattended);
        let done = index < attempt.step;
        let current = index == attempt.step;
        if done {
            let ok = gtk::Image::from_icon_name("emblem-ok-symbolic");
            ok.add_css_class("mixel-step-done");
            row.append(&ok);
        } else if current && !failed {
            let spinner = gtk::Spinner::new();
            spinner.start();
            row.append(&spinner);
        } else if current {
            let bad = gtk::Image::from_icon_name("dialog-error-symbolic");
            bad.add_css_class("mixel-error");
            row.append(&bad);
        } else {
            let todo = gtk::Image::from_icon_name("radio-symbolic");
            todo.add_css_class("mixel-step-todo");
            row.append(&todo);
        }
        let line = gtk::Label::new(Some(&name));
        if !done && !current {
            line.add_css_class("mixel-step-todo");
        }
        row.append(&line);
        let state_word = if done { t("done") } else if current && !failed { t("in progress") } else if current { t("failed") } else { t("waiting") };
        row.update_property(&[gtk::accessible::Property::Label(&format!("{name}: {state_word}"))]);
        frame.append(&row);
    }
    if failed {
        let reason = label(&attempt.failure);
        reason.add_css_class("mixel-error");
        reason.set_max_width_chars(44);
        frame.append(&reason);
    }
    let actions = hbox(8);
    let a = app.clone();
    actions.append(&button(&if failed { t("Close") } else { t("Cancel") }, move || a.cancel_attempt()));
    actions.append(&spacer());
    if failed {
        let (a, peer, unattended) = (app.clone(), attempt.peer, attempt.unattended);
        actions.append(&primary(&t("Try again"), move || a.connect(peer, None, unattended)));
    } else if !attempt.in_window {
        let a = app.clone();
        actions.append(&link(&t("Open in window instead"), move || a.open_attempt_in_window()));
    }
    frame.append(&actions);
    frame.update_property(&[gtk::accessible::Property::Label(&tf("Connecting to %@", &[&attempt.name]))]);
    frame
}

pub fn ready_card(app: &Rc<App>, peer: u32, name: &str, os: &str) -> gtk::Box {
    let frame = card_frame();
    let row = hbox(10);
    row.append(&device_tile(format::os_icon(os), "on"));
    let texts = vbox(0);
    texts.set_hexpand(true);
    texts.set_valign(gtk::Align::Center);
    let title = gtk::Label::new(Some(name));
    title.add_css_class("heading");
    title.set_xalign(0.0);
    texts.append(&title);
    texts.append(&caption(&t("The session is ready.")));
    row.append(&texts);
    let a = app.clone();
    let open = primary(&t("Open"), move || a.show_session(peer));
    open.set_valign(gtk::Align::Center);
    row.append(&open);
    let a = app.clone();
    let dismiss = icon_button("window-close-symbolic", &t("Dismiss"), move || a.dismiss_notice(peer));
    dismiss.add_css_class("flat");
    dismiss.set_valign(gtk::Align::Center);
    row.append(&dismiss);
    frame.append(&row);
    frame.update_property(&[gtk::accessible::Property::Label(&tf("Session with %@ is ready", &[name]))]);
    frame
}
