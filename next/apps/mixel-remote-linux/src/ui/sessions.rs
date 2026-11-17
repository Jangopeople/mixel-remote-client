//! Sessions: the sessions of this app as technician (one today, a list when the engine runs several),
//! with Open, "Open in its own window" / "Move back into a tab" and End session.
use super::widgets::*;
use crate::{
    app::{App, State},
    format,
    i18n::{t, tf},
    logic::Section,
    workspace::TabState,
};
use adw::prelude::*;
use std::{cell::RefCell, rc::Rc};

pub struct SessionsPage {
    pub widget: gtk::ScrolledWindow,
    body: gtk::Box,
    key: RefCell<String>,
}

impl SessionsPage {
    pub fn new() -> Self {
        let (widget, body) = page(&t("Sessions"), "");
        Self {
            widget,
            body,
            key: RefCell::new(String::new()),
        }
    }

    pub fn render(&self, app: &Rc<App>, state: &State) {
        let key = format!("{:?}{}", state.workspace, crate::i18n::choice());
        if *self.key.borrow() == key {
            return;
        }
        self.key.replace(key);
        clear_below_title(&self.body);
        let workspace = &state.workspace;
        set_subtitle(
            &self.body,
            &if workspace.tabs.is_empty() {
                String::new()
            } else {
                tf("%d sessions", &[&workspace.tabs.len()])
            },
        );
        if workspace.tabs.is_empty() {
            let status = adw::StatusPage::builder()
                .icon_name("view-dual-symbolic")
                .title(t("No sessions yet"))
                .description(t("Connect to a device under Devices; its session appears here and as a tab in the title bar."))
                .build();
            let a = app.clone();
            let go = primary(&t("Go to Devices"), move || {
                a.edit(|state| state.section = Section::Devices)
            });
            go.set_halign(gtk::Align::Center);
            status.set_child(Some(&go));
            self.body.append(&status);
            return;
        }
        for tab in &workspace.tabs {
            let peer = tab.peer;
            let in_window = workspace.is_in_window(peer);
            let (frame, content) = card(&tab.name, Some(crate::format::os_icon(&tab.os)));
            let line = hbox(10);
            let state_text = match tab.state {
                TabState::Starting => t("Starting video"),
                TabState::Ready => t("In session"),
                TabState::Ending => t("Ending the session…"),
                TabState::Reconnecting => t("Reconnecting..."),
                TabState::ReconnectFailed => t("Could not reconnect"),
            };
            line.append(&badge(crate::logic::Badge::Online, &state_text));
            line.append(&dim(&format::remote_id(Some(peer))));
            line.append(&dim(&if in_window {
                t("In its own window")
            } else {
                t("In a tab")
            }));
            content.append(&line);
            let a = app.clone();
            let open = primary(&t("Open"), move || a.show_session(peer));
            let (b, c) = (app.clone(), app.clone());
            let move_button = if in_window {
                button(&t("Move back into a tab"), move || b.dock(peer, true))
            } else {
                button(&t("Open in its own window"), move || b.pop_out(peer))
            };
            let end = destructive(&t("End session"), move || c.end_session_peer(peer));
            content.append(&buttons(&[
                open.upcast_ref(),
                move_button.upcast_ref(),
                end.upcast_ref(),
            ]));
            self.body.append(&frame);
        }
    }
}
