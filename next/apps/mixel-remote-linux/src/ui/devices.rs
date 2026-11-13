//! Devices: every device the account may reach, with its status and the reason when it cannot be
//! reached; for companies also groups, the Manage menu, Add this computer and Add other computers
//! (deploy links), and Wake for computers whose network address is known.
use super::{dialogs, remote::SetupInputs, widgets::*};
use crate::{
    app::{App, State},
    format,
    i18n::{t, tf},
    logic::{self, Presence, Section},
    models::Device,
};
use adw::prelude::*;
use std::{cell::RefCell, rc::Rc};

pub struct DevicesPage {
    pub widget: gtk::Box,
    top: gtk::Box,
    list: gtk::Box,
    key: RefCell<String>,
    search: gtk::Entry,
    setup: Rc<SetupInputs>,
}

impl DevicesPage {
    pub fn new(app: &Rc<App>) -> Self {
        let widget = vbox(0);
        let top = vbox(14);
        top.set_margin_top(24);
        top.set_margin_start(24);
        top.set_margin_end(24);
        top.set_margin_bottom(12);
        let clamp_top = adw::Clamp::builder().maximum_size(980).child(&top).build();
        widget.append(&clamp_top);
        let list = vbox(0);
        let clamp = adw::Clamp::builder().maximum_size(980).child(&list).build();
        let scroller = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).vexpand(true).child(&clamp).build();
        widget.append(&scroller);
        let search = entry(&t("Device name"));
        search.update_property(&[gtk::accessible::Property::Label(&t("Device name"))]);
        let a = app.clone();
        search.connect_activate(move |entry| {
            let text = entry.text().to_string();
            a.state.borrow_mut().devices.search = text;
            a.reload_devices();
        });
        Self { widget, top, list, key: RefCell::new(String::new()), search, setup: Rc::new(SetupInputs::new(app)) }
    }

    pub fn render(&self, app: &Rc<App>, state: &State) {
        let devices = &state.devices;
        let company = &state.company.data;
        let rows: Vec<_> = devices
            .devices
            .iter()
            .map(|device| {
                let presence = state.presence(device);
                (device.clone(), presence, logic::presence_reason(device, presence, &state.remote, &devices.server_presence, 0).len(), app.known_macs(device.connection_id).len())
            })
            .collect();
        let key = format!(
            "{:?}",
            ((state.signed_in(), &rows, devices.loading, &devices.message, &devices.error, &devices.groups), (&devices.group_filter, devices.page, devices.has_next, &devices.deploy_link.is_some(), company.tenant().map(|tenant| (&tenant.name, &tenant.tenant_id)), company.role()), (company.add_blocked(), company.groups_on(), state.registered_here(), (state.remote.phase.as_str(), state.remote.host, state.remote.connected(), state.busy, state.enrollment.needs_setup)))
        );
        if *self.key.borrow() == key {
            return;
        }
        self.key.replace(key);
        keep_focus(&[self.search.clone().upcast()], || {
            self.build_top(app, state);
            self.build_list(app, state);
        });
    }

    fn build_top(&self, app: &Rc<App>, state: &State) {
        clear(&self.top);
        let company = &state.company.data;
        let header = hbox(10);
        let titles = vbox(4);
        titles.set_hexpand(true);
        let title = gtk::Label::builder().label(t("Devices")).xalign(0.0).accessible_role(gtk::AccessibleRole::Heading).build();
        title.add_css_class("title-1");
        titles.append(&title);
        if let Some(tenant) = company.tenant() {
            titles.append(&dim(&tenant.name));
        }
        header.append(&titles);
        if company.tenant().is_some() && state.signed_in() {
            let blocked = company.add_blocked();
            if company.manages_devices() && !state.registered_here() {
                let (a, setup) = (app.clone(), self.setup.clone());
                let add = button(&t("Add this computer"), move || add_this_dialog(&a, &setup));
                add.set_sensitive(blocked.is_empty());
                add.set_tooltip_text((!blocked.is_empty()).then_some(blocked.as_str()));
                header.append(&add);
            }
            if company.deploys() {
                let a = app.clone();
                let add = button(&t("Add other computers"), move || deploy_dialog(&a));
                add.set_sensitive(blocked.is_empty());
                add.set_tooltip_text((!blocked.is_empty()).then_some(blocked.as_str()));
                header.append(&add);
            }
        }
        let a = app.clone();
        let refresh = icon_button("view-refresh-symbolic", &t("Refresh devices"), move || a.reload_devices());
        refresh.set_sensitive(state.signed_in() && !state.devices.loading);
        header.append(&refresh);
        self.top.append(&header);

        let row = hbox(8);
        let search = adopt(&self.search);
        search.set_size_request(280, -1);
        search.set_hexpand(false);
        row.append(&search);
        if company.manages_devices() && company.groups_on() && !state.devices.groups.is_empty() {
            let groups = state.devices.groups.clone();
            let mut names = vec![t("All groups")];
            names.extend(groups.iter().map(|group| group.name.clone()));
            let selected = state.devices.group_filter.as_ref().and_then(|id| groups.iter().position(|group| &group.group_id == id)).map(|index| index + 1).unwrap_or(0);
            let a = app.clone();
            let pick = choice(&names, selected, move |index| {
                if a.rendering.get() {
                    return;
                }
                a.state.borrow_mut().devices.group_filter = if index == 0 { None } else { groups.get(index - 1).map(|group| group.group_id.clone()) };
                a.reload_devices();
            });
            pick.update_property(&[gtk::accessible::Property::Label(&t("Group"))]);
            row.append(&pick);
        }
        let (a, entry) = (app.clone(), self.search.clone());
        let go = button(&t("Search"), move || {
            a.state.borrow_mut().devices.search = entry.text().to_string();
            a.reload_devices();
        });
        go.set_sensitive(state.signed_in() && !state.devices.loading);
        row.append(&go);
        self.top.append(&row);
        if !company.add_blocked().is_empty() && company.manages_devices() {
            let text = if !company.writable() {
                t("This company is read-only. You can still remove devices; adding devices and other changes need an active plan.")
            } else {
                t("Device limit of your plan reached. Remove a device or upgrade the plan to add more.")
            };
            append_message(&self.top, &text, false);
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
            self.top.append(&banner);
        }
        append_message(&self.top, &state.devices.error, true);
        append_message(&self.top, &state.devices.message, false);
    }

    fn build_list(&self, app: &Rc<App>, state: &State) {
        clear(&self.list);
        let devices = &state.devices;
        let company = &state.company.data;
        if devices.devices.is_empty() {
            let title = if devices.loading {
                t("Loading your devices…")
            } else if !state.signed_in() {
                t("Your devices belong here")
            } else if !devices.search.is_empty() {
                t("No matching devices")
            } else if company.tenant().is_some() && devices.group_filter.is_some() {
                t("No devices in this group")
            } else {
                t("No devices to display")
            };
            let detail = if !state.signed_in() {
                t("Sign in to see the devices available to your account.")
            } else if !devices.search.is_empty() {
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
        let list = gtk::ListBox::new();
        list.add_css_class("boxed-list");
        list.set_selection_mode(gtk::SelectionMode::None);
        list.set_margin_start(24);
        list.set_margin_end(24);
        list.set_margin_bottom(12);
        for device in &devices.devices {
            list.append(&device_row(app, state, device));
        }
        self.list.append(&list);
        if devices.page > 0 || devices.has_next {
            let pager = hbox(10);
            pager.set_margin_start(24);
            pager.set_margin_end(24);
            pager.set_margin_bottom(24);
            let a = app.clone();
            let previous = button(&t("Previous"), move || a.devices_page(-1));
            previous.set_sensitive(devices.page > 0 && !devices.loading);
            pager.append(&previous);
            pager.append(&spacer());
            pager.append(&caption(&devices.message));
            pager.append(&spacer());
            let a = app.clone();
            let next = button(&t("Next"), move || a.devices_page(1));
            next.set_sensitive(devices.has_next && !devices.loading);
            pager.append(&next);
            self.list.append(&pager);
        }
    }
}

fn device_row(app: &Rc<App>, state: &State, device: &Device) -> gtk::ListBoxRow {
    let company = &state.company.data;
    let presence = state.presence(device);
    let blocked = logic::connect_blocked(device, presence, &state.remote, state.busy, state.signed_in());
    let row = hbox(14);
    row.add_css_class("mixel-device-row");
    row.set_margin_top(6);
    row.set_margin_bottom(6);
    row.set_margin_start(10);
    row.set_margin_end(10);
    let icon = gtk::Image::from_icon_name(format::os_icon(&device.os));
    icon.set_pixel_size(32);
    if matches!(presence, Presence::Online | Presence::This) {
        icon.add_css_class("mixel-accent");
    } else {
        icon.add_css_class("dim-label");
    }
    row.append(&icon);
    let text = vbox(2);
    text.set_hexpand(true);
    let name = heading(&device.name);
    name.set_wrap(false);
    name.set_ellipsize(gtk::pango::EllipsizeMode::End);
    text.append(&name);
    text.set_size_request(170, -1);
    let mut line = format::os(&device.os);
    if let Some(group) = &device.group_name {
        line.push_str(&format!(" · {group}"));
    }
    line.push_str(&format!(" · {}", format::remote_id(Some(device.connection_id))));
    text.append(&caption(&line));
    row.append(&text);
    let status = vbox(2);
    status.set_halign(gtk::Align::End);
    let badge_row = badge(logic::presence_badge(presence), &logic::presence_label(presence, &state.remote));
    badge_row.set_halign(gtk::Align::End);
    status.append(&badge_row);
    let reason = logic::presence_reason(device, presence, &state.remote, &state.devices.server_presence, format::now());
    if !reason.is_empty() {
        let reason = caption(&reason);
        reason.set_xalign(1.0);
        reason.set_max_width_chars(34);
        reason.set_justify(gtk::Justification::Right);
        status.append(&reason);
    }
    row.append(&status);
    let macs = app.known_macs(device.connection_id);
    if presence == Presence::Offline && !macs.is_empty() {
        let (a, chosen) = (app.clone(), device.clone());
        let wake = button(&t("Wake"), move || a.wake(&chosen, macs.clone()));
        wake.set_valign(gtk::Align::Center);
        wake.set_tooltip_text(Some(&tf("Wake %@ with a network signal", &[&device.name])));
        row.append(&wake);
    }
    if company.manages_devices() {
        let writable = company.writable();
        let mut items: Vec<MenuItem> = Vec::new();
        let (a, chosen) = (app.clone(), device.clone());
        items.push(Some((t("Rename…"), writable, Box::new(move || rename_dialog(&a, &chosen)))));
        if company.groups_on() {
            let (a, chosen) = (app.clone(), device.clone());
            items.push(Some((t("Move to group…"), writable, Box::new(move || move_dialog(&a, &chosen)))));
        }
        items.push(None);
        let (a, chosen) = (app.clone(), device.clone());
        items.push(Some((t("Remove…"), true, Box::new(move || remove_dialog(&a, &chosen)))));
        let manage = menu(&t("Manage"), items);
        manage.set_valign(gtk::Align::Center);
        manage.update_property(&[gtk::accessible::Property::Label(&tf("Manage %@", &[&device.name]))]);
        row.append(&manage);
    }
    let (a, chosen) = (app.clone(), device.clone());
    let connect = primary(&t("Connect"), move || a.connect_device(&chosen));
    connect.set_valign(gtk::Align::Center);
    connect.set_sensitive(blocked.is_empty());
    connect.set_tooltip_text(Some(&if blocked.is_empty() { tf("Connect to %@", &[&device.name]) } else { blocked }));
    connect.update_property(&[gtk::accessible::Property::Label(&tf("Connect to %@", &[&device.name]))]);
    row.append(&connect);
    let item = gtk::ListBoxRow::new();
    item.set_activatable(false);
    item.set_child(Some(&row));
    item
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
