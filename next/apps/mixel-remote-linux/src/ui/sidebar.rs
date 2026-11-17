//! The console's sidebar: the company, the sections (Devices, Sessions, Reports, Settings), the Views
//! (All devices, Online now, Favourites, Recent), the Groups with "+", this computer with its ID and a
//! copy button, and the account row. Every entry is a flat button, so the keyboard reaches all of them.
use super::{devices, widgets::*};
use crate::{
    app::{App, State},
    format,
    i18n::t,
    logic::{self, Section},
    view::{self, View},
};
use adw::prelude::*;
use std::{cell::RefCell, rc::Rc};

pub struct Sidebar {
    pub widget: gtk::Box,
    body: gtk::Box,
    key: RefCell<String>,
}

impl Sidebar {
    pub fn new() -> Self {
        let widget = vbox(0);
        widget.add_css_class("mixel-sidebar");
        widget.set_size_request(250, -1);
        widget.set_hexpand(false);
        let body = vbox(2);
        body.set_margin_top(14);
        body.set_margin_bottom(12);
        body.set_margin_start(10);
        body.set_margin_end(10);
        body.set_vexpand(true);
        widget.append(&body);
        Self {
            widget,
            body,
            key: RefCell::new(String::new()),
        }
    }

    pub fn render(&self, app: &Rc<App>, state: &State) {
        let rows = app.console_rows(state);
        let counts = view::counts(&rows);
        let key = format!(
            "{:?}",
            (
                (
                    state.section,
                    &state.devices.filters.view,
                    counts,
                    state.signed_in(),
                    state
                        .company
                        .data
                        .tenant()
                        .map(|tenant| tenant.name.clone())
                ),
                (
                    state.workspace.tabs.len(),
                    state.remote.host,
                    state.remote.phase.as_str(),
                    state.remote.connected(),
                    &state
                        .account
                        .account
                        .as_ref()
                        .map(|account| account.display_name.clone())
                ),
                (&state.devices.groups, crate::i18n::choice()),
                (
                    state.company.data.manages_devices(),
                    state.company.data.groups_on(),
                    rows.iter()
                        .filter_map(|row| row.group.clone())
                        .collect::<Vec<_>>()
                ),
            )
        );
        if *self.key.borrow() == key {
            return;
        }
        self.key.replace(key);
        clear(&self.body);
        let company = &state.company.data;

        // Brand and company.
        let brand = hbox(10);
        brand.set_margin_start(6);
        brand.set_margin_bottom(14);
        brand.append(&mark(34));
        let names = vbox(0);
        names.set_valign(gtk::Align::Center);
        let name = gtk::Label::new(Some("Mixel Remote"));
        name.add_css_class("heading");
        name.set_xalign(0.0);
        names.append(&name);
        if let Some(tenant) = company.tenant() {
            let company_name = caption(&tenant.name);
            company_name.set_ellipsize(gtk::pango::EllipsizeMode::End);
            names.append(&company_name);
        }
        brand.append(&names);
        self.body.append(&brand);

        // Sections.
        let on_devices = state.section == Section::Devices;
        for section in Section::NAV {
            let count = match section {
                Section::Devices => Some(counts.all),
                Section::Sessions => Some(state.workspace.tabs.len()).filter(|count| *count > 0),
                _ => None,
            };
            let a = app.clone();
            self.body.append(&nav_item(
                section.icon(),
                &section.title(),
                count,
                section == Section::Sessions,
                state.section == section,
                move || a.edit(|state| state.section = section),
            ));
        }

        // Views.
        self.body.append(&section_label(&t("Views")));
        let views: [(&str, String, View, usize); 4] = [
            ("computer-symbolic", t("All devices"), View::All, counts.all),
            (
                "network-transmit-receive-symbolic",
                t("Online now"),
                View::Online,
                counts.online,
            ),
            (
                "starred-symbolic",
                t("Favourites"),
                View::Favourites,
                counts.favourites,
            ),
            (
                "document-open-recent-symbolic",
                t("Recent"),
                View::Recent,
                counts.recent,
            ),
        ];
        for (icon, title, view_kind, count) in views {
            let active = on_devices && state.devices.filters.view == view_kind;
            let a = app.clone();
            self.body.append(&nav_item(
                icon,
                &title,
                Some(count),
                false,
                active,
                move || {
                    let view_kind = view_kind.clone();
                    a.edit(|state| {
                        state.devices.filters.view = view_kind;
                        state.section = Section::Devices;
                    });
                },
            ));
        }

        // Groups.
        if company.tenant().is_some() && (company.groups_on() || !state.devices.groups.is_empty()) {
            let head = hbox(0);
            head.append(&{
                let label = section_label(&t("Groups"));
                label.set_hexpand(true);
                label
            });
            if company.manages_devices() && company.writable() {
                let a = app.clone();
                let add = icon_button("list-add-symbolic", &t("Create group"), move || {
                    devices::create_group_dialog(&a)
                });
                add.add_css_class("flat");
                head.append(&add);
            }
            self.body.append(&head);
            for (index, group) in state.devices.groups.iter().enumerate() {
                let id = group.group_id.clone();
                let active = on_devices && state.devices.filters.view == View::Group(id.clone());
                let count = view::group_count(&rows, &id);
                let row = hbox(10);
                let colour = hbox(0);
                colour.add_css_class("mixel-group-dot");
                colour.add_css_class(&format!("mixel-group-{}", index % 6));
                colour.set_valign(gtk::Align::Center);
                row.append(&colour);
                let name = gtk::Label::new(Some(&group.name));
                name.set_xalign(0.0);
                name.set_hexpand(true);
                name.set_ellipsize(gtk::pango::EllipsizeMode::End);
                row.append(&name);
                let number = gtk::Label::new(Some(&count.to_string()));
                number.add_css_class("mixel-count");
                row.append(&number);
                let button = gtk::Button::new();
                button.set_child(Some(&row));
                button.add_css_class("flat");
                button.add_css_class("mixel-nav");
                if active {
                    button.add_css_class("active");
                }
                button.update_property(&[gtk::accessible::Property::Label(&format!(
                    "{}, {count}",
                    group.name
                ))]);
                let a = app.clone();
                button.connect_clicked(move |_| {
                    let id = id.clone();
                    a.edit(|state| {
                        state.devices.filters.view = View::Group(id);
                        state.section = Section::Devices;
                    });
                });
                self.body.append(&button);
            }
        }

        self.body.append(&spacer_v());
        self.body.append(&this_computer(app, state));
        self.body.append(&account_row(app, state));
    }
}

fn spacer_v() -> gtk::Box {
    let space = vbox(0);
    space.set_vexpand(true);
    space
}

fn section_label(text: &str) -> gtk::Label {
    let label = gtk::Label::new(Some(&text.to_uppercase()));
    label.add_css_class("mixel-section");
    label.set_xalign(0.0);
    label.set_margin_top(16);
    label.set_margin_bottom(4);
    label.set_margin_start(10);
    label.update_property(&[gtk::accessible::Property::Label(text)]);
    label
}

/// "This computer · online" with its ID and a copy button; a click on the title opens its page.
fn this_computer(app: &Rc<App>, state: &State) -> gtk::Box {
    let (kind, text) = logic::connection_badge(&state.remote);
    let card = vbox(4);
    card.add_css_class("mixel-this");
    card.set_margin_bottom(10);
    let title = hbox(6);
    title.append(&dot(match kind {
        logic::Badge::Online => "on",
        logic::Badge::Busy => "busy",
        _ => "off",
    }));
    let words = if state.signed_in() {
        format!("{} · {}", t("This computer"), text.to_lowercase())
    } else {
        t("This computer")
    };
    let line = gtk::Label::new(Some(&words));
    line.set_xalign(0.0);
    line.add_css_class("caption");
    line.set_ellipsize(gtk::pango::EllipsizeMode::End);
    title.append(&line);
    let open = gtk::Button::new();
    open.set_child(Some(&title));
    open.add_css_class("flat");
    open.set_halign(gtk::Align::Start);
    open.set_tooltip_text(Some(&t("Open this computer's page")));
    open.update_property(&[gtk::accessible::Property::Label(&format!(
        "{words}. {}",
        t("Open this computer's page")
    ))]);
    let a = app.clone();
    open.connect_clicked(move |_| a.edit(|state| state.section = Section::Computer));
    card.append(&open);
    let id_row = hbox(6);
    id_row.set_margin_start(6);
    match state.remote.host.filter(|_| state.signed_in()) {
        Some(host) => {
            let id = id_text(Some(host));
            id.add_css_class("mixel-id-small");
            id.set_hexpand(true);
            id_row.append(&id);
            let a = app.clone();
            id_row.append(&icon_button(
                "edit-copy-symbolic",
                &t("Copy ID"),
                move || a.copy_id(),
            ));
        }
        None if !state.signed_in() => {
            let a = app.clone();
            id_row.append(&link(&t("Sign in to get your connection ID."), move || {
                a.edit(|state| state.section = Section::Account)
            }));
        }
        None => id_row.append(&dim(&format::remote_id(None))),
    }
    card.append(&id_row);
    card
}

/// The signed-in person: initials, name, "Account"; a click opens the account.
fn account_row(app: &Rc<App>, state: &State) -> gtk::Button {
    let row = hbox(10);
    let name = state
        .account
        .account
        .as_ref()
        .and_then(|account| account.display_name.clone())
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| t("Account"));
    let initials: String = name
        .split_whitespace()
        .filter_map(|word| word.chars().next())
        .take(2)
        .collect::<String>()
        .to_uppercase();
    let avatar = gtk::Label::new(Some(if state.signed_in() {
        initials.as_str()
    } else {
        "?"
    }));
    avatar.add_css_class("mixel-avatar");
    avatar.set_size_request(32, 32);
    row.append(&avatar);
    let texts = vbox(0);
    texts.set_valign(gtk::Align::Center);
    let title = gtk::Label::new(Some(&if state.signed_in() {
        name.clone()
    } else {
        t("Sign in")
    }));
    title.add_css_class("heading");
    title.set_xalign(0.0);
    title.set_ellipsize(gtk::pango::EllipsizeMode::End);
    texts.append(&title);
    texts.append(&caption(&if state.signed_in() {
        t("Account")
    } else {
        t("Not signed in")
    }));
    row.append(&texts);
    let button = gtk::Button::new();
    button.set_child(Some(&row));
    button.add_css_class("flat");
    button.update_property(&[gtk::accessible::Property::Label(&format!(
        "{}: {}",
        t("Account"),
        if state.signed_in() {
            name
        } else {
            t("Sign in")
        }
    ))]);
    let a = app.clone();
    button.connect_clicked(move |_| a.edit(|state| state.section = Section::Account));
    button
}
