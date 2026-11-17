//! The Devices console (Pro Console): the title with the number of devices, "Connect by ID", the
//! filters (search with "/", Online, Unattended, Update available, the system, sort), and the device
//! table (star, system tile with online dot, name and system, ID, status with the connection progress
//! on the row, last seen, version, Connect / Show and the "…" menu). Companies also get groups, the
//! Add this computer and Add other computers (deploy link) buttons, Wake and the Manage actions.
use super::{dialogs, remote::SetupInputs, widgets::*};
use crate::{
    app::{App, State},
    format,
    i18n::{t, tf},
    logic::{self, Presence, Section},
    models::Device,
    view::{self, Filters, Sort, View},
};
use adw::prelude::*;
use std::{cell::RefCell, rc::Rc};

/// The colours of group dots, in order.
/// (the matching `.mixel-group-N` classes are in style.css).
const GROUP_COLOURS: [&str; 6] = ["#5b5bd6", "#12a594", "#e5870b", "#a855c4", "#d6409f", "#3e8ed0"];

pub struct DevicesPage {
    pub widget: gtk::Box,
    head: gtk::Box,
    list: gtk::Box,
    key: RefCell<String>,
    pub search: gtk::Entry,
    connect_id: gtk::Entry,
    connect_password: gtk::PasswordEntry,
    setup: Rc<SetupInputs>,
    table_head: gtk::Box,
}

/// The table's column widths (the device column takes the rest).
const ID_W: i32 = 108;
const STATUS_W: i32 = 168;
const SEEN_W: i32 = 78;
const VERSION_W: i32 = 88;
const ACTIONS_W: i32 = 132;

impl DevicesPage {
    pub fn new(app: &Rc<App>) -> Self {
        let widget = vbox(0);
        let head = vbox(14);
        head.set_margin_top(20);
        head.set_margin_start(24);
        head.set_margin_end(24);
        head.set_margin_bottom(8);
        widget.append(&head);
        let list = vbox(0);
        let scroller = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).vexpand(true).child(&list).build();
        // The table scrolls sideways when the window is narrow; its header stays above the rows.
        let table_head = vbox(0);
        let table = vbox(0);
        table.append(&table_head);
        table.append(&scroller);
        let sideways = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Automatic).vscrollbar_policy(gtk::PolicyType::Never).vexpand(true).child(&table).build();
        widget.append(&sideways);
        let search = entry(&t("Search name, ID or note"));
        search.set_icon_from_icon_name(gtk::EntryIconPosition::Primary, Some("edit-find-symbolic"));
        search.set_hexpand(false);
        search.set_width_chars(14);
        search.update_property(&[gtk::accessible::Property::Label(&t("Search name, ID or note")), gtk::accessible::Property::KeyShortcuts("/")]);
        // Typing filters the loaded list at once; Enter asks the server (all pages).
        let a = app.clone();
        search.connect_changed(move |entry| {
            if a.rendering.get() {
                return;
            }
            let text = entry.text().to_string();
            a.edit(|state| state.devices.filters.search = text);
        });
        let a = app.clone();
        search.connect_activate(move |entry| {
            let text = entry.text().to_string();
            a.state.borrow_mut().devices.search = text;
            a.reload_devices();
        });
        let connect_id = id_entry(&t("Connect by ID"));
        connect_id.set_placeholder_text(Some("000 000 000"));
        let connect_password = password(&t("One-time password"));
        let (a, id, pw) = (app.clone(), connect_id.clone(), connect_password.clone());
        connect_id.connect_activate(move |_| connect_by_id(&a, &id, &pw));
        let (a, id, pw) = (app.clone(), connect_id.clone(), connect_password.clone());
        connect_password.connect_activate(move |_| connect_by_id(&a, &id, &pw));
        let page = Self { widget, head, list, key: RefCell::new(String::new()), search, connect_id, connect_password, setup: Rc::new(SetupInputs::new(app)), table_head };
        // "/" jumps to the search (only when no text field is being typed in).
        let keys = gtk::EventControllerKey::new();
        let search = page.search.clone();
        keys.connect_key_pressed(move |_, key, _, modifiers| {
            if key == gtk::gdk::Key::slash && !modifiers.intersects(gtk::gdk::ModifierType::CONTROL_MASK | gtk::gdk::ModifierType::ALT_MASK) {
                search.grab_focus();
                return gtk::glib::Propagation::Stop;
            }
            gtk::glib::Propagation::Proceed
        });
        page.widget.add_controller(keys);
        page
    }

    pub fn focus_search(&self) {
        self.search.grab_focus();
    }

    pub fn focus_connect(&self) {
        self.connect_id.grab_focus();
    }

    pub fn render(&self, app: &Rc<App>, state: &State) {
        let devices = &state.devices;
        let company = &state.company.data;
        let rows = app.console_rows(state);
        let key = format!(
            "{:?}",
            (
                (state.signed_in(), &rows, devices.loading, &devices.message, &devices.error, &devices.groups, &devices.filters),
                (devices.page, devices.has_next, devices.deploy_link.is_some(), company.tenant().map(|tenant| (&tenant.name, &tenant.tenant_id)), company.role()),
                (company.add_blocked(), company.groups_on(), state.registered_here(), (state.remote.phase.as_str(), state.remote.host, state.remote.connected(), state.busy, state.enrollment.needs_setup, state.can_connect())),
                (state.workspace.tabs.iter().map(|tab| (tab.peer, tab.state)).collect::<Vec<_>>(), state.workspace.attempt.as_ref().map(|attempt| (attempt.peer, attempt.step, attempt.failure.is_empty())), &devices.server_presence.len()),
            )
        );
        if *self.key.borrow() == key {
            return;
        }
        self.key.replace(key);
        keep_focus(&[self.search.clone().upcast(), self.connect_id.clone().upcast(), self.connect_password.clone().upcast()], || {
            self.build_head(app, state, &rows);
            self.build_list(app, state, &rows);
        });
    }

    fn view_title(state: &State) -> String {
        match &state.devices.filters.view {
            View::All => t("All devices"),
            View::Online => t("Online now"),
            View::Favourites => t("Favourites"),
            View::Recent => t("Recent"),
            View::Group(id) => state.devices.groups.iter().find(|group| &group.group_id == id).map(|group| group.name.clone()).unwrap_or_else(|| t("Group")),
        }
    }

    fn build_head(&self, app: &Rc<App>, state: &State, rows: &[view::Row]) {
        clear(&self.head);
        let company = &state.company.data;
        let filters = &state.devices.filters;
        let shown = view::visible(filters, rows).len();
        let counts = view::counts(rows);

        // Title, count and "Connect by ID".
        let top = hbox(16);
        let titles = vbox(2);
        titles.set_hexpand(true);
        titles.set_valign(gtk::Align::Center);
        let title = gtk::Label::builder().label(Self::view_title(state)).xalign(0.0).accessible_role(gtk::AccessibleRole::Heading).build();
        title.add_css_class("mixel-title");
        titles.append(&title);
        if state.signed_in() {
            let mut line = tf("%d of %d devices · %d online", &[&shown, &counts.all, &counts.online]);
            if let Some(tenant) = company.tenant() {
                line = format!("{} · {line}", tenant.name);
            }
            titles.append(&dim(&line));
        }
        top.append(&titles);
        top.append(&self.connect_box(app, state));
        self.head.append(&top);

        // Filters.
        // One line; it scrolls sideways when the window is narrow.
        let row = hbox(8);
        row.set_valign(gtk::Align::Center);
        let search = adopt(&self.search);
        row.append(&search);
        let chip = |text: &str, on: bool, dot_kind: Option<&str>, change: Box<dyn Fn(&mut Filters)>| {
            let inner = hbox(6);
            if let Some(kind) = dot_kind {
                inner.append(&dot(kind));
            }
            inner.append(&gtk::Label::new(Some(text)));
            let toggle = gtk::ToggleButton::new();
            toggle.set_child(Some(&inner));
            toggle.add_css_class("mixel-chip");
            toggle.set_active(on);
            toggle.update_property(&[gtk::accessible::Property::Label(text)]);
            let a = app.clone();
            toggle.connect_toggled(move |_| {
                if !a.rendering.get() {
                    a.edit(|state| change(&mut state.devices.filters));
                }
            });
            toggle
        };
        row.append(&chip(&t("Online"), filters.online_only, Some("on"), Box::new(|filters| filters.online_only = !filters.online_only)));
        row.append(&chip(&t("Unattended"), filters.unattended_only, None, Box::new(|filters| filters.unattended_only = !filters.unattended_only)));
        row.append(&chip(&t("Update available"), filters.update_only, None, Box::new(|filters| filters.update_only = !filters.update_only)));
        row.append(&gtk::Separator::new(gtk::Orientation::Vertical));
        for (family, name) in [("macos", "macOS"), ("windows", "Windows"), ("linux", "Linux")] {
            row.append(&chip(name, filters.os == Some(family), None, Box::new(move |filters| filters.os = if filters.os == Some(family) { None } else { Some(family) })));
        }
        row.append(&spacer());
        if company.tenant().is_some() && state.signed_in() {
            let blocked = company.add_blocked();
            let mut items: Vec<MenuItem> = Vec::new();
            if company.manages_devices() && !state.registered_here() {
                let (a, setup) = (app.clone(), self.setup.clone());
                items.push(Some((t("Add this computer"), blocked.is_empty(), Box::new(move || add_this_dialog(&a, &setup)))));
            }
            if company.deploys() {
                let a = app.clone();
                items.push(Some((t("Add other computers"), blocked.is_empty(), Box::new(move || deploy_dialog(&a)))));
            }
            if !items.is_empty() {
                let add = menu(&t("Add computer"), items);
                add.set_tooltip_text((!blocked.is_empty()).then_some(blocked.as_str()));
                row.append(&add);
            }
        }
        let a = app.clone();
        let refresh = icon_button("view-refresh-symbolic", &t("Refresh devices"), move || a.reload_devices());
        refresh.set_sensitive(state.signed_in() && !state.devices.loading);
        row.append(&refresh);
        let (a, b) = (app.clone(), app.clone());
        let sort = icon_menu(
            "view-sort-descending-symbolic",
            &if filters.sort == Sort::Status { t("Sorted by status") } else { t("Sorted by name") },
            vec![
                Some((t("Sorted by status"), true, Box::new(move || a.edit(|state| state.devices.filters.sort = Sort::Status)))),
                Some((t("Sorted by name"), true, Box::new(move || b.edit(|state| state.devices.filters.sort = Sort::Name)))),
            ],
        );
        row.append(&sort);
        let filters_scroll = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Automatic).vscrollbar_policy(gtk::PolicyType::Never).propagate_natural_height(true).child(&row).build();
        self.head.append(&filters_scroll);

        if !company.add_blocked().is_empty() && company.manages_devices() {
            let text = if !company.writable() {
                t("This company is read-only. You can still remove devices; adding devices and other changes need an active plan.")
            } else {
                t("Device limit of your plan reached. Remove a device or upgrade the plan to add more.")
            };
            append_message(&self.head, &text, false);
        }
        if state.signed_in() && !state.remote.connected() && state.remote.phase != "closing" && !state.enrollment.needs_setup {
            let banner = hbox(10);
            banner.add_css_class("mixel-warning-box");
            let icon = gtk::Image::from_icon_name("network-offline-symbolic");
            icon.add_css_class("mixel-busy");
            icon.set_valign(gtk::Align::Start);
            banner.append(&icon);
            let text = vbox(3);
            text.set_hexpand(true);
            text.append(&heading(&if state.remote.phase == "error" { t("This computer lost its connection") } else { t("This computer is not connected yet") }));
            text.append(&dim(&t("Mixel Remote reconnects by itself. Device status appears once it is connected; you can still choose Connect.")));
            banner.append(&text);
            let a = app.clone();
            let reconnect = button(&t("Reconnect now"), move || a.go_online());
            reconnect.set_valign(gtk::Align::Center);
            banner.append(&reconnect);
            self.head.append(&banner);
        }
        append_message(&self.head, &state.devices.error, true);
        if state.devices.page > 0 || state.devices.has_next {
            append_message(&self.head, &state.devices.message, false);
        }
        clear(&self.table_head);
        if state.signed_in() && !state.devices.devices.is_empty() {
            self.table_head.append(&self.table_header());
        }
    }

    /// "Connect by ID": the nine digits, Connect, and a password option for hosts that need one.
    fn connect_box(&self, app: &Rc<App>, state: &State) -> gtk::Box {
        let boxed = hbox(6);
        boxed.add_css_class("mixel-connect-box");
        boxed.set_valign(gtk::Align::Center);
        let connect_label = dim(&t("Connect by ID"));
        connect_label.set_wrap(false);
        boxed.append(&connect_label);
        boxed.append(&adopt(&self.connect_id));
        // A one-time password: only for a person who gave it on the phone; the warning says so.
        let pop = gtk::Popover::new();
        let inner = vbox(8);
        inner.set_margin_top(10);
        inner.set_margin_bottom(10);
        inner.set_margin_start(10);
        inner.set_margin_end(10);
        inner.append(&heading(&t("One-time password")));
        let warning = dim(&t("Use a password only if the person at the other computer gave it to you. A session opened with a password needs their confirmation each time and ends when either side ends it."));
        warning.set_wrap(true);
        warning.set_max_width_chars(36);
        inner.append(&warning);
        inner.append(&adopt(&self.connect_password));
        pop.set_child(Some(&inner));
        let more = gtk::MenuButton::builder().icon_name("dialog-password-symbolic").popover(&pop).tooltip_text(t("One-time password")).build();
        more.add_css_class("flat");
        more.update_property(&[gtk::accessible::Property::Label(&t("One-time password"))]);
        boxed.append(&more);
        let (a, id, pw) = (app.clone(), self.connect_id.clone(), self.connect_password.clone());
        let connect = primary(&t("Connect"), move || connect_by_id(&a, &id, &pw));
        connect.set_sensitive(state.can_connect());
        boxed.append(&connect);
        boxed
    }

    fn table_header(&self) -> gtk::Box {
        let header = hbox(12);
        header.set_margin_start(12);
        header.set_margin_end(12);
        let cell = |text: &str, width: Option<i32>, expand: bool| {
            let label = gtk::Label::new(Some(&text.to_uppercase()));
            label.add_css_class("mixel-th");
            label.set_xalign(0.0);
            label.set_hexpand(expand);
            if let Some(width) = width {
                label.set_size_request(width, -1);
            }
            label
        };
        let spacer = hbox(0);
        spacer.set_size_request(34, 1);
        header.append(&spacer);
        let tile = hbox(0);
        tile.set_size_request(38, 1);
        header.append(&tile);
        header.append(&cell(&t("Device"), None, true));
        header.append(&cell(&t("ID"), Some(ID_W), false));
        header.append(&cell(&t("Status"), Some(STATUS_W), false));
        header.append(&cell(&t("Last seen"), Some(SEEN_W), false));
        header.append(&cell(&t("Version"), Some(VERSION_W), false));
        let actions = cell(&t("Actions"), Some(ACTIONS_W), false);
        actions.set_xalign(1.0);
        header.append(&actions);
        header
    }

    fn build_list(&self, app: &Rc<App>, state: &State, rows: &[view::Row]) {
        clear(&self.list);
        let devices = &state.devices;
        let company = &state.company.data;
        let visible = view::visible(&devices.filters, rows);
        if visible.is_empty() {
            let filtered = !devices.devices.is_empty();
            let title = if devices.loading {
                t("Loading your devices…")
            } else if !state.signed_in() {
                t("Your devices belong here")
            } else if filtered || !devices.search.is_empty() {
                t("No matching devices")
            } else if company.tenant().is_some() && devices.group_filter.is_some() {
                t("No devices in this group")
            } else {
                t("No devices to display")
            };
            let detail = if !state.signed_in() {
                t("Sign in to see the devices available to your account.")
            } else if filtered || !devices.search.is_empty() {
                t("Try another device name or clear the search.")
            } else if company.tenant().is_some() {
                if company.manages_devices() { t("Add this computer, or add other computers with a deploy link.") } else { t("Devices you add appear here. Ask an administrator of your company for access to other devices.") }
            } else {
                t("Devices appear here after a Mixel team member enrolls them.")
            };
            let status = adw::StatusPage::builder().icon_name("computer-symbolic").title(title).description(detail).vexpand(true).build();
            if !state.signed_in() {
                let a = app.clone();
                let sign_in = primary(&t("Sign in to Mixel →"), move || a.edit(|state| state.section = Section::Account));
                sign_in.set_halign(gtk::Align::Center);
                status.set_child(Some(&sign_in));
            }
            self.list.append(&status);
            return;
        }
        // Groups: in the list of all devices a company's devices sit under their group's heading.
        let grouped = devices.filters.view == View::All && company.tenant().is_some() && visible.iter().any(|row| row.group.is_some());
        let find = |id: u32| devices.devices.iter().find(|device| device.connection_id == id);
        if grouped {
            let mut order: Vec<Option<String>> = Vec::new();
            for group in &devices.groups {
                if visible.iter().any(|row| row.group.as_deref() == Some(group.group_id.as_str())) {
                    order.push(Some(group.group_id.clone()));
                }
            }
            if visible.iter().any(|row| row.group.is_none()) {
                order.push(None);
            }
            for (index, group) in order.iter().enumerate() {
                let members: Vec<&view::Row> = visible.iter().copied().filter(|row| row.group == *group).collect();
                let name = match group {
                    Some(id) => devices.groups.iter().find(|candidate| &candidate.group_id == id).map(|candidate| candidate.name.clone()).unwrap_or_default(),
                    None => t("No group"),
                };
                let online = members.iter().filter(|row| row.online).count();
                let head = hbox(8);
                head.add_css_class("mixel-group-row");
                let colour = gtk::Box::new(gtk::Orientation::Horizontal, 0);
                colour.add_css_class("mixel-group-dot");
                colour.set_valign(gtk::Align::Center);
                colour.add_css_class(&format!("mixel-group-{}", index % GROUP_COLOURS.len()));
                head.append(&colour);
                head.append(&label(&name));
                head.append(&dim(&tf("%d devices · %d online", &[&members.len(), &online])));
                head.update_property(&[gtk::accessible::Property::Label(&format!("{name}, {}", tf("%d devices · %d online", &[&members.len(), &online])))]);
                self.list.append(&head);
                for row in members {
                    if let Some(device) = find(row.id) {
                        self.list.append(&self.device_row(app, state, device, row));
                    }
                }
            }
        } else {
            for row in visible {
                if let Some(device) = find(row.id) {
                    self.list.append(&self.device_row(app, state, device, row));
                }
            }
        }
        if devices.page > 0 || devices.has_next {
            let pager = hbox(10);
            pager.set_margin_start(24);
            pager.set_margin_end(24);
            pager.set_margin_top(12);
            pager.set_margin_bottom(24);
            let a = app.clone();
            let previous = button(&t("Previous"), move || a.devices_page(-1));
            previous.set_sensitive(devices.page > 0 && !devices.loading);
            pager.append(&previous);
            pager.append(&spacer());
            let a = app.clone();
            let next = button(&t("Next"), move || a.devices_page(1));
            next.set_sensitive(devices.has_next && !devices.loading);
            pager.append(&next);
            self.list.append(&pager);
        }
    }

    fn device_row(&self, app: &Rc<App>, state: &State, device: &Device, summary: &view::Row) -> gtk::Box {
        let company = &state.company.data;
        let presence = state.presence(device);
        let live = state.workspace.state_of(device.connection_id);
        let blocked = logic::connect_blocked(device, presence, &state.remote, state.busy, state.signed_in());
        let meta = state.devices.meta.get(&device.connection_id);
        let row = hbox(12);
        row.add_css_class("mixel-table-row");
        if matches!(live, Some("ready" | "starting")) {
            row.add_css_class("live");
        }
        row.set_margin_start(12);
        row.set_margin_end(12);

        // Star.
        let favourite = summary.favourite;
        let star = icon_button(if favourite { "starred-symbolic" } else { "non-starred-symbolic" }, &if favourite { tf("Remove %@ from favorites", &[&device.name]) } else { tf("Add %@ to favorites", &[&device.name]) }, {
            let (a, id) = (app.clone(), device.device.clone());
            move || a.set_favorite(id.clone(), !favourite)
        });
        star.add_css_class("flat");
        if favourite {
            star.add_css_class("mixel-accent");
        }
        star.set_valign(gtk::Align::Center);
        star.set_sensitive(company.tenant().is_some());
        star.set_size_request(34, -1);
        row.append(&star);

        // System tile and name.
        let kind = match presence {
            Presence::Online | Presence::This => "on",
            Presence::Checking => "busy",
            _ => "off",
        };
        row.append(&device_tile(format::os_icon(&device.os), kind));
        let text = vbox(1);
        text.set_valign(gtk::Align::Center);
        let name = gtk::Label::new(Some(&device.name));
        name.add_css_class("heading");
        name.set_xalign(0.0);
        name.set_ellipsize(gtk::pango::EllipsizeMode::End);
        text.append(&name);
        let mut line = format::os(&device.os);
        if let Some(group) = &device.group_name {
            line.push_str(&format!(" · {group}"));
        }
        if let Some(note) = meta.and_then(|meta| meta.note.as_deref()).filter(|note| !note.is_empty()) {
            line.push_str(&format!(" · {note}"));
        }
        let sub = caption(&line);
        sub.set_ellipsize(gtk::pango::EllipsizeMode::End);
        text.append(&sub);
        text.set_hexpand(true);
        text.set_size_request(100, -1);
        row.append(&text);

        // ID.
        let id = gtk::Label::new(Some(&format::remote_id(Some(device.connection_id))));
        id.add_css_class("monospace");
        id.set_xalign(0.0);
        id.set_selectable(false);
        id.set_size_request(ID_W, -1);
        row.append(&id);

        // Status: the progress of a connection that is being prepared sits on the row.
        let status = hbox(8);
        status.set_valign(gtk::Align::Center);
        status.set_size_request(STATUS_W, -1);
        match live {
            Some("connecting") => {
                let step = state.workspace.attempt.as_ref().map(|attempt| attempt.step).unwrap_or(0);
                let inner = vbox(3);
                let line = hbox(6);
                let spinner = gtk::Spinner::new();
                spinner.start();
                line.append(&spinner);
                line.append(&label(&connect_step_name(step, state.workspace.attempt.as_ref().is_some_and(|attempt| attempt.unattended))));
                inner.append(&line);
                let bar = gtk::ProgressBar::new();
                bar.set_fraction(f64::from(step.min(3) + 1) / 4.0);
                inner.append(&bar);
                inner.set_hexpand(true);
                status.append(&inner);
            }
            Some("ready" | "starting" | "ending") => status.append(&pill_tag("session", &t("In session"))),
            _ => {
                status.append(&badge(logic::presence_badge(presence), &logic::presence_label(presence, &state.remote)));
                if meta.is_some_and(crate::models::AdminDevice::unattended_on) {
                    status.append(&pill_tag("unattended", &t("Unattended")));
                }
            }
        }
        let reason = logic::presence_reason(device, presence, &state.remote, &state.devices.server_presence, format::now());
        status.set_tooltip_text((!reason.is_empty()).then_some(reason.as_str()));
        row.append(&status);

        // Last seen and version.
        let seen = match presence {
            Presence::This | Presence::Online => t("Now"),
            _ if matches!(live, Some("ready" | "starting")) => t("Now"),
            _ => state.devices.server_presence.get(&device.connection_id).map(|record| format::ago(record.last_seen.as_deref(), format::now())).filter(|text| !text.is_empty()).unwrap_or_else(|| "—".into()),
        };
        let seen = label(&seen);
        seen.set_xalign(0.0);
        seen.set_size_request(SEEN_W, -1);
        row.append(&seen);
        let version = hbox(6);
        version.append(&label(meta.and_then(|meta| meta.app_version.as_deref()).unwrap_or("—")));
        if summary.update {
            version.append(&pill_tag("update", &t("Update")));
        }
        version.set_size_request(VERSION_W, -1);
        row.append(&version);

        // Actions.
        let actions = hbox(4);
        actions.set_halign(gtk::Align::End);
        actions.set_valign(gtk::Align::Center);
        actions.set_size_request(ACTIONS_W, -1);
        if matches!(live, Some("ready" | "starting")) {
            let (a, peer) = (app.clone(), device.connection_id);
            let show = button(&t("Show"), move || a.show_session(peer));
            show.set_tooltip_text(Some(&tf("Show the session with %@", &[&device.name])));
            show.update_property(&[gtk::accessible::Property::Label(&tf("Show the session with %@", &[&device.name]))]);
            actions.append(&show);
        } else {
            let (a, chosen) = (app.clone(), device.clone());
            let connect = button(&t("Connect"), move || a.connect_device(&chosen));
            connect.set_sensitive(blocked.is_empty() && live.is_none());
            connect.set_tooltip_text(Some(&if blocked.is_empty() { tf("Connect to %@", &[&device.name]) } else { blocked.clone() }));
            connect.update_property(&[gtk::accessible::Property::Label(&tf("Connect to %@", &[&device.name]))]);
            actions.append(&connect);
        }
        actions.append(&self.row_menu(app, state, device, presence, blocked.is_empty()));
        row.append(&actions);
        row.update_property(&[gtk::accessible::Property::Label(&format!("{}, {}", device.name, logic::presence_label(presence, &state.remote)))]);
        row
    }

    fn row_menu(&self, app: &Rc<App>, state: &State, device: &Device, presence: Presence, can_connect: bool) -> gtk::MenuButton {
        let company = &state.company.data;
        let mut items: Vec<MenuItem> = Vec::new();
        let (a, chosen) = (app.clone(), device.clone());
        items.push(Some((t("Connect without confirmation (unattended access)"), can_connect, Box::new(move || a.connect(chosen.connection_id, None, true)))));
        let (a, chosen) = (app.clone(), device.clone());
        items.push(Some((t("Connect and ask the person to accept"), can_connect, Box::new(move || a.connect(chosen.connection_id, None, false)))));
        let macs = app.known_macs(device.connection_id);
        if presence == Presence::Offline && !macs.is_empty() {
            let (a, chosen) = (app.clone(), device.clone());
            items.push(Some((t("Wake"), true, Box::new(move || a.wake(&chosen, macs.clone())))));
        }
        items.push(None);
        let (a, id) = (app.clone(), device.connection_id);
        items.push(Some((t("Copy ID"), true, Box::new(move || {
            if let Some(shell) = a.shell() {
                shell.copy(&format::remote_id(Some(id)));
                shell.notify(&t("Copied"));
            }
        }))));
        if company.manages_devices() {
            let writable = company.writable();
            let (a, chosen) = (app.clone(), device.clone());
            items.push(Some((t("Rename…"), writable, Box::new(move || rename_dialog(&a, &chosen)))));
            if company.groups_on() {
                let (a, chosen) = (app.clone(), device.clone());
                items.push(Some((t("Move to group…"), writable, Box::new(move || move_dialog(&a, &chosen)))));
            }
            items.push(None);
            let (a, chosen) = (app.clone(), device.clone());
            items.push(Some((t("Remove…"), true, Box::new(move || remove_dialog(&a, &chosen)))));
        }
        icon_menu("view-more-symbolic", &tf("More for %@", &[&device.name]), items)
    }
}

/// The name of a connection step (see `workspace::Attempt::step`).
pub fn connect_step_name(step: u8, unattended: bool) -> String {
    match step {
        0 => t("Reaching device"),
        1 => if unattended { t("Checking unattended access") } else { t("Waiting for the person to accept") },
        2 => t("Encrypting end-to-end"),
        _ => t("Starting video"),
    }
}

fn connect_by_id(app: &Rc<App>, id: &gtk::Entry, password: &gtk::PasswordEntry) {
    let Some(peer) = format::parse_remote_id(&id.text()) else {
        app.edit(|state| state.error = t("Enter a nine-digit connection ID"));
        return;
    };
    let supplied = password.text().to_string();
    password.set_text("");
    id.set_text("");
    // With a password the person there confirms it; without one an unattended computer lets us in.
    app.connect(peer, (!supplied.is_empty()).then_some(supplied.clone()), supplied.is_empty());
}

/// Creates a group (the "+" next to Groups).
pub fn create_group_dialog(app: &Rc<App>) {
    let Some(parent) = window(app) else { return };
    let a = app.clone();
    dialogs::form(&parent, &t("Create group"), "", &[(&t("Name"), "")], &t("Create group"), false, Rc::new(move |values| {
        let (a, name) = (a.clone(), values[0].trim().to_owned());
        Box::pin(async move { if name.is_empty() { Ok(()) } else { a.create_group(name).await } })
    }));
}

fn window(app: &Rc<App>) -> Option<adw::ApplicationWindow> {
    app.ui.borrow().as_ref().map(|ui| ui.window.clone())
}

fn rename_dialog(app: &Rc<App>, device: &Device) {
    let Some(parent) = window(app) else { return };
    let (a, id) = (app.clone(), device.device.clone());
    dialogs::form(&parent, &t("Rename device"), "", &[(&t("Device name"), &device.name)], &t("Rename"), false, Rc::new(move |values| {
        let (a, id) = (a.clone(), id.clone());
        Box::pin(async move { a.rename_device(id, values[0].trim().to_owned()).await })
    }));
}

fn remove_dialog(app: &Rc<App>, device: &Device) {
    let Some(parent) = window(app) else { return };
    let (a, id) = (app.clone(), device.device.clone());
    dialogs::confirm(&parent, &t("Remove this device?"), &t("Its running sessions end at once. It can be added again later."), &tf("Remove %@", &[&device.name]), move || {
        let (a, id) = (a.clone(), id.clone());
        a.clone().spawn(async move {
            let _ = a.remove_device(id).await;
        });
    });
}

fn move_dialog(app: &Rc<App>, device: &Device) {
    let Some(parent) = window(app) else { return };
    let (dialog, content, actions) = dialogs::window(&parent, &tf("Move %@ to a group", &[&device.name]), 400);
    let groups = app.state.borrow().devices.groups.clone();
    let mut names = vec![t("No group")];
    names.extend(groups.iter().map(|group| group.name.clone()));
    let selected = device.group_id.as_ref().and_then(|id| groups.iter().position(|group| &group.group_id == id)).map(|index| index + 1).unwrap_or(0);
    let pick = choice(&names, selected, |_| {});
    content.append(&field(&t("Group"), &pick));
    let error = vbox(0);
    if app.state.borrow().company.data.deploys() {
        let row = hbox(8);
        let name = entry(&t("New group"));
        row.append(&name);
        let (a, e) = (app.clone(), error.clone());
        let entry_ref = name.clone();
        row.append(&button(&t("Create group"), move || {
            let text = entry_ref.text().trim().to_owned();
            if text.is_empty() {
                return;
            }
            let (a, e, entry_ref) = (a.clone(), e.clone(), entry_ref.clone());
            a.clone().spawn(async move {
                clear(&e);
                match a.create_group(text).await {
                    Ok(()) => {
                        entry_ref.set_text("");
                        append_message(&e, &t("Group created. Open Move to group again to choose it."), false);
                    }
                    Err(text) => append_message(&e, &text, true),
                }
            });
        }));
        content.append(&row);
    }
    content.append(&error);
    let close = dialog.clone();
    actions.append(&button(&t("Cancel"), move || close.close()));
    let (a, id, d, e) = (app.clone(), device.device.clone(), dialog.clone(), error.clone());
    actions.append(&primary(&t("Move"), move || {
        let index = pick.selected() as usize;
        let group = if index == 0 { None } else { groups.get(index - 1).map(|group| group.group_id.clone()) };
        let (a, id, d, e) = (a.clone(), id.clone(), d.clone(), e.clone());
        a.clone().spawn(async move {
            match a.move_device(id, group).await {
                Ok(()) => d.close(),
                Err(text) => {
                    clear(&e);
                    append_message(&e, &text, true);
                }
            }
        });
    }));
    dialog.present();
}

/// "Add this computer" from the device list: the set-up card in a dialog.
fn add_this_dialog(app: &Rc<App>, setup: &Rc<SetupInputs>) {
    let Some(parent) = window(app) else { return };
    let (dialog, content, actions) = dialogs::window(&parent, &t("Add this computer"), 480);
    let holder = vbox(0);
    content.append(&holder);
    let render = {
        let (a, setup, holder) = (app.clone(), setup.clone(), holder.clone());
        move || {
            clear(&holder);
            let state = a.state.borrow().clone();
            holder.append(&super::remote::setup_card(&a, &state, &setup));
        }
    };
    render();
    let close = dialog.clone();
    actions.append(&button(&t("Close"), move || close.close()));
    // Follow the set-up while the dialog is open; close it once this computer is registered.
    let (a, d) = (app.clone(), dialog.clone());
    gtk::glib::timeout_add_local(std::time::Duration::from_millis(700), move || {
        if !d.is_visible() {
            return gtk::glib::ControlFlow::Break;
        }
        if a.state.borrow().registered_here() && !a.state.borrow().enrollment.working {
            d.close();
            return gtk::glib::ControlFlow::Break;
        }
        render();
        gtk::glib::ControlFlow::Continue
    });
    dialog.present();
}

/// "Add other computers": a deploy link and the one-line command for each system (shown once).
fn deploy_dialog(app: &Rc<App>) {
    let Some(parent) = window(app) else { return };
    app.edit(|state| state.devices.deploy_link = None);
    let (dialog, content, actions) = dialogs::window(&parent, &t("Add other computers"), 560);
    let name = entry(&t("Name"));
    content.append(&field(&t("Name"), &name));
    let computers = gtk::SpinButton::with_range(1.0, 2000.0, 1.0);
    computers.set_value(50.0);
    content.append(&field(&t("Computers"), &computers));
    let days = gtk::SpinButton::with_range(1.0, 365.0, 1.0);
    days.set_value(30.0);
    content.append(&field(&t("Valid for (days)"), &days));
    let (groups, groups_on) = {
        let state = app.state.borrow();
        (state.devices.groups.clone(), state.company.data.groups_on())
    };
    let pick = (groups_on && !groups.is_empty()).then(|| {
        let mut names = vec![t("No group")];
        names.extend(groups.iter().map(|group| group.name.clone()));
        let pick = choice(&names, 0, |_| {});
        content.append(&field(&t("Group"), &pick));
        pick
    });
    let error = vbox(0);
    content.append(&error);
    let close = dialog.clone();
    let cancel = button(&t("Cancel"), move || close.close());
    actions.append(&cancel);
    let (a, c, act, e) = (app.clone(), content.clone(), actions.clone(), error.clone());
    let create = primary(&t("Create deploy link"), move || {
        let label = name.text().trim().to_owned();
        let label = if label.is_empty() { t("Rollout") } else { label };
        let group = pick.as_ref().map(|pick| pick.selected() as usize).filter(|index| *index > 0).and_then(|index| groups.get(index - 1).map(|group| group.group_id.clone()));
        let (count, valid) = (computers.value() as u32, days.value() as u32);
        let (a, c, act, e) = (a.clone(), c.clone(), act.clone(), e.clone());
        a.clone().spawn(async move {
            clear(&e);
            match a.create_deploy_link(label, valid, count, group).await {
                Ok(()) => {
                    let Some(link) = a.state.borrow().devices.deploy_link.clone() else { return };
                    clear(&c);
                    c.append(&dim(&t("Run the command on each computer (as administrator, or with your deployment tool). The link is shown only now.")));
                    for (title, command) in [(t("Windows (PowerShell, as administrator)"), link.windows_command.clone()), ("macOS".to_owned(), link.macos_command.clone()), ("Linux".to_owned(), link.linux_command.clone())] {
                        c.append(&command_box(&a, &title, &command));
                    }
                    c.append(&command_box(&a, &t("Deploy token"), &link.token));
                    clear(&act);
                    if let Some(window) = c.root().and_downcast::<gtk::Window>() {
                        act.append(&primary(&t("Done"), move || window.close()));
                    }
                }
                Err(text) => append_message(&e, &text, true),
            }
        });
    });
    actions.append(&create);
    dialog.present();
}

fn command_box(app: &Rc<App>, title: &str, command: &str) -> gtk::Box {
    let row = hbox(8);
    let text = monospace(command);
    text.set_hexpand(true);
    text.add_css_class("mixel-transfer");
    row.append(&text);
    let (a, value) = (app.clone(), command.to_owned());
    row.append(&icon_button("edit-copy-symbolic", &t("Copy command"), move || {
        if let Some(shell) = a.shell() {
            shell.copy(&value);
            shell.notify(&t("Copied"));
        }
    }));
    field(title, &row)
}
