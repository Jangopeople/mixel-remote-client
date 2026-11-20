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
        let recent = app.recent_connections();
        let key = format!("{:?}{:?}{}", state.workspace, recent, crate::i18n::choice());
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
        if workspace.tabs.is_empty() && recent.is_empty() {
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
        if !recent.is_empty() {
            let heading = gtk::Label::builder().label(t("Recent connections")).xalign(0.0).build();
            heading.add_css_class("title-3");
            heading.set_margin_top(12);
            self.body.append(&heading);
            for conn_id in recent {
                let known = state
                    .devices
                    .devices
                    .iter()
                    .chain(state.devices.company_all.iter())
                    .find(|d| d.connection_id == conn_id);
                let title_text = known
                    .map(|d| d.name.clone())
                    .unwrap_or_else(|| format::remote_id(Some(conn_id)));
                let os_icon = known.map(|d| format::os_icon(&d.os));
                let (frame, content) = card(&title_text, os_icon);
                content.append(&dim(&format::remote_id(Some(conn_id))));
                let a_save = app.clone();
                let save_btn = button(&t("Save device…"), move || {
                    a_save.edit(|st| st.save_for = Some(conn_id));
                });
                let a_conn = app.clone();
                let unattended = known.is_some();
                let conn_btn = primary(&t("Connect"), move || a_conn.connect(conn_id, None, unattended));
                content.append(&buttons(&[save_btn.upcast_ref(), conn_btn.upcast_ref()]));
                self.body.append(&frame);
            }
        }
    }
}
