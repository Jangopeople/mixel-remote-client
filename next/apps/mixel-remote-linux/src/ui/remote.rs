//! Remote control: connect to another computer or phone, and allow remote access to this computer
//! (its ID, going online, the one-time password, pairing, unattended access, the hosted session).
use super::widgets::*;
use crate::{
    app::{App, State},
    format,
    i18n::{engine, t, tf},
    logic::{self, Section, SetupPath},
};
use gtk::prelude::*;
use std::{cell::RefCell, rc::Rc};

pub struct RemotePage {
    pub widget: gtk::ScrolledWindow,
    body: gtk::Box,
    key: RefCell<String>,
    partner: gtk::Entry,
    password: gtk::PasswordEntry,
    unattended: gtk::CheckButton,
    phone: gtk::Entry,
    setup: SetupInputs,
}

/// Fields of the set-up card that keep what was typed while the card is built again.
pub struct SetupInputs {
    pub name: gtk::Entry,
    pub search: gtk::Entry,
    pub group: Rc<RefCell<Option<String>>>,
    pub company: Rc<RefCell<Option<String>>>,
}

impl SetupInputs {
    pub fn new(app: &Rc<App>) -> Self {
        let name = entry(&t("Device name"));
        let a = app.clone();
        name.connect_changed(move |entry| a.set_setup_name(&entry.text()));
        Self { name, search: entry(&t("Device name")), group: Rc::new(RefCell::new(None)), company: Rc::new(RefCell::new(None)) }
    }
}

impl RemotePage {
    pub fn new(app: &Rc<App>) -> Self {
        let (widget, body) = page(&t("Remote control"), "");
        let partner = id_entry(&t("Partner connection ID"));
        let password = password("");
        password.update_property(&[gtk::accessible::Property::Label(&t("One-time password, if provided"))]);
        let unattended = gtk::CheckButton::with_label(&t("Connect without confirmation (unattended access)"));
        let pw = password.clone();
        unattended.connect_toggled(move |check| {
            if check.is_active() {
                pw.set_text("");
            }
            pw.set_sensitive(!check.is_active());
        });
        let phone = id_entry(&t("ID from the phone"));
        let page = Self { widget, body, key: RefCell::new(String::new()), partner, password, unattended, phone, setup: SetupInputs::new(app) };
        let a = app.clone();
        let (p, pw, u) = (page.partner.clone(), page.password.clone(), page.unattended.clone());
        let connect = Rc::new(move || connect_to(&a, &p, &pw, &u));
        let go = connect.clone();
        page.partner.connect_activate(move |_| go());
        let go = connect;
        page.password.connect_activate(move |_| go());
        let a = app.clone();
        let phone = page.phone.clone();
        page.phone.connect_activate(move |_| connect_phone(&a, &phone));
        page
    }

    pub fn render(&self, app: &Rc<App>, state: &State) {
        let remote = &state.remote;
        let key = format!(
            "{:?}",
            ((state.connection_message(), state.signed_in(), state.staff(), &remote.phase, remote.host, remote.controller_only), (remote.sharing, remote.control, &remote.support_code, remote.code_required, &remote.pairing, &remote.pairing_message), (&remote.invitations, &remote.colleagues, &remote.join_request, remote.recording_permitted, remote.unattended_enabled, &remote.unattended_pending), (remote.input_access, &remote.meeting_offer, &remote.peer_features, remote.switched, (&state.error, &state.progress, state.busy), (&state.enrollment.needs_setup, &state.enrollment.error, state.enrollment.working, &state.enrollment.records, &state.enrollment.companies, state.setup_path())), ((&state.host.unattended_sources, &state.host.unattended_source, state.host.unattended_confirm, state.host.unattended_control, &state.host.error), (&state.devices.groups, state.company.data.add_blocked(), state.company.data.tenant().map(|tenant| tenant.name.clone())), (&state.session.dismissed_meeting_room, &state.linux)))
        );
        if *self.key.borrow() == key {
            return;
        }
        self.key.replace(key);
        let entries: Vec<gtk::Widget> = vec![self.partner.clone().upcast(), self.password.clone().upcast(), self.phone.clone().upcast(), self.setup.name.clone().upcast(), self.setup.search.clone().upcast()];
        keep_focus(&entries, || self.build(app, state));
    }

    fn build(&self, app: &Rc<App>, state: &State) {
        let body = &self.body;
        // Keep the title; rebuild everything below it.
        while let Some(child) = body.first_child().and_then(|first| first.next_sibling()) {
            body.remove(&child);
        }
        set_subtitle(body, &state.connection_message());
        let remote = &state.remote;
        if !state.signed_in() {
            let (frame, content) = card(&t("Connect with your Mixel account"), Some("avatar-default-symbolic"));
            content.append(&dim(&t("Sign in to get your connection ID.")));
            let a = app.clone();
            let sign_in = primary(&t("Sign in"), move || a.edit(|state| state.section = Section::Account));
            sign_in.set_halign(gtk::Align::Start);
            content.append(&sign_in);
            body.append(&frame);
        }
        if state.enrollment.needs_setup && state.signed_in() {
            body.append(&setup_card(app, state, &self.setup));
        }
        if !remote.invitations.is_empty() {
            body.append(&invitations(app, state));
        }
        if remote.phase == "active" && remote.sharing {
            body.append(&hosted_session(app, state));
        }
        body.append(&self.control_card(app, state));
        body.append(&self.allow_card(app, state));
        if state.staff() {
            body.append(&self.phone_card(app, state));
        }
        if !remote.controller_only && state.signed_in() && (remote.phase == "ready" || remote.unattended_enabled) {
            body.append(&unattended_card(app, state));
        }
        append_message(body, &state.error, true);
    }

    fn control_card(&self, app: &Rc<App>, state: &State) -> gtk::Box {
        let remote = &state.remote;
        let (frame, content) = card(&t("Control a remote device"), Some("video-display-symbolic"));
        content.append(&dim(&t("Enter the 9-digit ID shown on the other computer.")));
        content.append(&field(&t("Partner connection ID"), &adopt(&self.partner)));
        content.append(&field(&t("One-time password, if provided"), &adopt(&self.password)));
        adopt(&self.unattended);
        content.append(&self.unattended);
        let a = app.clone();
        let (p, pw, u) = (self.partner.clone(), self.password.clone(), self.unattended.clone());
        let connect = primary(&t("Connect"), move || connect_to(&a, &p, &pw, &u));
        connect.set_sensitive(!state.busy && state.signed_in() && matches!(remote.phase.as_str(), "ready" | "offline" | "error"));
        let row = buttons(&[connect.upcast_ref()]);
        if remote.phase == "outgoing" {
            let a = app.clone();
            row.append(&button(&t("Cancel"), move || a.end_session()));
        }
        content.append(&row);
        if !state.progress.is_empty() && (state.busy || remote.phase == "outgoing") {
            content.append(&spinner_line(&state.progress));
        }
        frame.set_valign(gtk::Align::Start);
        frame
    }

    fn allow_card(&self, app: &Rc<App>, state: &State) -> gtk::Box {
        let remote = &state.remote;
        let (frame, content) = card(&t("Allow remote access"), Some("system-lock-screen-symbolic"));
        frame.set_valign(gtk::Align::Start);
        if remote.controller_only {
            content.append(&dim(&t("This app controls other devices; it cannot be controlled itself.")));
            return frame;
        }
        content.append(&field(&t("Your connection ID"), &id_text(if state.signed_in() { remote.host } else { None })));
        let (kind, text) = logic::connection_badge(remote);
        let row = hbox(8);
        row.append(&badge(kind, &text));
        row.append(&spacer());
        let a = app.clone();
        let copy = button(&t("Copy ID"), move || a.copy_id());
        copy.set_sensitive(state.signed_in() && remote.host.is_some());
        row.append(&copy);
        content.append(&row);
        if let Some(code) = remote.support_code.as_ref().filter(|_| state.signed_in()) {
            let code_label = monospace(code.code());
            code_label.add_css_class("title-2");
            content.append(&field(&t("One-time password"), &code_label));
            content.append(&caption(&t("Share this password with your technician. It lasts five minutes and allows one request.")));
        }
        let actions = hbox(8);
        if remote.disconnected() {
            let a = app.clone();
            let online = button(&t("Make this computer available"), move || a.go_online());
            online.set_sensitive(!state.busy && state.signed_in());
            actions.append(&online);
        } else {
            let a = app.clone();
            let offline = button(&t("Go offline"), move || a.go_offline());
            offline.set_sensitive(!state.busy);
            actions.append(&offline);
        }
        if state.signed_in() && remote.host.is_some() {
            let a = app.clone();
            if remote.code_required {
                actions.append(&button(&t("Stop requiring a password"), move || a.one_time_password(false)));
            } else {
                let generate = button(&t("Generate one-time password"), move || a.one_time_password(true));
                generate.set_sensitive(remote.phase == "ready");
                actions.append(&generate);
            }
        }
        actions.set_halign(gtk::Align::Start);
        let flow = gtk::FlowBox::new();
        flow.set_selection_mode(gtk::SelectionMode::None);
        let mut child = actions.first_child();
        while let Some(item) = child {
            child = item.next_sibling();
            actions.remove(&item);
            flow.insert(&item, -1);
        }
        content.append(&flow);
        if state.signed_in() && remote.phase == "ready" {
            if let Some(prompt) = &remote.pairing {
                content.append(&label(&tf("Pair this computer with %@ for %@?", &[&prompt.provider_name, &prompt.customer_name])));
                let a = app.clone();
                let confirm = primary(&t("Confirm pairing"), move || a.confirm_pairing());
                confirm.set_halign(gtk::Align::Start);
                content.append(&confirm);
            } else {
                let row = hbox(8);
                let a = app.clone();
                row.append(&link(&t("Check for pairing"), move || a.check_pairing()));
                if let Some(message) = &remote.pairing_message {
                    row.append(&caption(&engine(message)));
                }
                content.append(&row);
            }
        }
        if state.linux.wayland && !remote.controller_only {
            content.append(&caption(&t("This computer uses Wayland: the system asks you to choose the screen each time it is shared. The privacy screen, blocking keyboard and mouse and the technician's pointer are not possible on Wayland; sign in with an Xorg session for them.")));
        }
        frame
    }

    fn phone_card(&self, app: &Rc<App>, state: &State) -> gtk::Box {
        let (frame, content) = card(&t("Help someone on their phone"), Some("phone-symbolic"));
        content.append(&dim(&t("The person opens Mixel Quick Support on their phone and tells you the ID it shows.")));
        content.append(&field(&t("ID from the phone"), &adopt(&self.phone)));
        let a = app.clone();
        let phone = self.phone.clone();
        let connect = primary(&t("Connect"), move || connect_phone(&a, &phone));
        connect.set_sensitive(!state.busy && state.signed_in());
        connect.set_halign(gtk::Align::Start);
        content.append(&connect);
        frame
    }
}

fn connect_to(app: &Rc<App>, partner: &gtk::Entry, password: &gtk::PasswordEntry, unattended: &gtk::CheckButton) {
    let Some(id) = format::parse_remote_id(&partner.text()) else {
        app.edit(|state| state.error = t("Enter a nine-digit connection ID"));
        return;
    };
    let supplied = password.text().to_string();
    password.set_text("");
    app.connect(id, (!supplied.is_empty()).then_some(supplied), unattended.is_active());
}

fn connect_phone(app: &Rc<App>, phone: &gtk::Entry) {
    match format::parse_remote_id(&phone.text()) {
        Some(id) => app.connect(id, None, false),
        None => app.edit(|state| state.error = t("Enter a nine-digit connection ID")),
    }
}

/// Set up this computer once: the path depends on the account (company, Mixel team, personal).
pub fn setup_card(app: &Rc<App>, state: &State, inputs: &SetupInputs) -> gtk::Box {
    let (frame, content) = card(&t("Set up this computer"), Some("security-high-symbolic"));
    let enrollment = &state.enrollment;
    if inputs.name.text().is_empty() && !enrollment.name.is_empty() {
        inputs.name.set_text(&enrollment.name);
    }
    match state.setup_path() {
        SetupPath::Company => {
            let company = state.company.data.tenant().map(|tenant| tenant.name.clone()).unwrap_or_default();
            content.append(&dim(&tf("Add this computer to %@ so you can connect from it and to it.", &[&company])));
            content.append(&field(&t("Name of this computer"), &adopt(&inputs.name)));
            let groups = &state.devices.groups;
            if !groups.is_empty() {
                let mut names = vec![t("No group")];
                names.extend(groups.iter().map(|group| group.name.clone()));
                let chosen = inputs.group.borrow().clone();
                let selected = chosen.and_then(|id| groups.iter().position(|group| group.group_id == id)).map(|index| index + 1).unwrap_or(0);
                let ids: Vec<String> = groups.iter().map(|group| group.group_id.clone()).collect();
                let target = inputs.group.clone();
                let pick = choice(&names, selected, move |index| {
                    target.replace(if index == 0 { None } else { ids.get(index - 1).cloned() });
                });
                content.append(&field(&t("Group"), &pick));
            }
            let blocked = state.company.data.add_blocked();
            let a = app.clone();
            let group = inputs.group.clone();
            let add = primary(&t("Add this computer"), move || a.add_to_company(group.borrow().clone()));
            add.set_sensitive(!enrollment.working && blocked.is_empty());
            add.set_halign(gtk::Align::Start);
            content.append(&add);
            append_message(&content, &blocked, false);
        }
        SetupPath::CompanyMember => content.append(&dim(&t("Devices you add appear here. Ask an administrator of your company for access to other devices."))),
        SetupPath::Personal => {
            content.append(&dim(&t("Register this computer with your personal account.")));
            content.append(&field(&t("Device name"), &adopt(&inputs.name)));
            let a = app.clone();
            let register = primary(&t("Register this computer"), move || a.register_personal());
            register.set_sensitive(!enrollment.working);
            register.set_halign(gtk::Align::Start);
            content.append(&register);
        }
        SetupPath::Team => {
            content.append(&dim(&t("Enroll this computer against its existing ISM record, or add it to a company.")));
            let row = hbox(8);
            row.append(&adopt(&inputs.search));
            let a = app.clone();
            let search = inputs.search.clone();
            row.append(&button(&t("Find device"), move || a.load_team_records(search.text().to_string())));
            content.append(&row);
            for record in &enrollment.records {
                let line = hbox(10);
                line.append(&gtk::Image::from_icon_name(format::os_icon(&record.os)));
                let name = label(&record.name);
                name.set_hexpand(true);
                line.append(&name);
                let a = app.clone();
                let chosen = record.clone();
                let enroll = button(&t("Enroll this computer"), move || a.enroll_record(chosen.clone()));
                enroll.set_sensitive(!enrollment.working);
                line.append(&enroll);
                content.append(&line);
            }
            if !enrollment.companies.is_empty() {
                content.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
                content.append(&heading(&t("This computer is not in the list? Add it")));
                let names: Vec<String> = enrollment.companies.iter().map(|company| company.client_name.clone()).collect();
                let ids: Vec<String> = enrollment.companies.iter().map(|company| company.client_id.clone()).collect();
                let current = inputs.company.borrow().clone().and_then(|id| ids.iter().position(|item| *item == id)).unwrap_or(0);
                inputs.company.replace(ids.get(current).cloned());
                let target = inputs.company.clone();
                let pick = choice(&names, current, move |index| {
                    target.replace(ids.get(index).cloned());
                });
                content.append(&field(&t("Company"), &pick));
                content.append(&field(&t("Computer name"), &adopt(&inputs.name)));
                let a = app.clone();
                let company = inputs.company.clone();
                let add = button(&t("Add and enroll this computer"), move || {
                    if let Some(company) = company.borrow().clone() {
                        a.add_and_enroll(company);
                    }
                });
                add.set_sensitive(!enrollment.working && inputs.company.borrow().is_some());
                add.set_halign(gtk::Align::Start);
                content.append(&add);
            }
        }
        SetupPath::None => {
            content.append(&dim(&t("Create a company to manage your devices and team, or a personal space for your own devices.")));
            let a = app.clone();
            let create = primary(&t("Create a company"), move || a.edit(|state| state.section = Section::Account));
            create.set_halign(gtk::Align::Start);
            content.append(&create);
        }
    }
    if enrollment.working {
        content.append(&spinner_line(&t("Please wait…")));
    }
    append_message(&content, &enrollment.error, true);
    frame
}

fn invitations(app: &Rc<App>, state: &State) -> gtk::Box {
    let (frame, content) = card(&t("A colleague invites you to a session"), Some("system-users-symbolic"));
    for invitation in &state.remote.invitations {
        let row = hbox(10);
        let text = vbox(2);
        text.append(&heading(&tf("Computer %@", &[&format::remote_id(Some(invitation.host))])));
        text.append(&caption(&if invitation.control { t("View and control") } else { t("View only") }));
        text.set_hexpand(true);
        row.append(&text);
        let (a, chosen) = (app.clone(), invitation.clone());
        row.append(&button(&t("Not now"), move || a.dismiss_invitation(chosen.clone())));
        let (a, chosen) = (app.clone(), invitation.clone());
        row.append(&primary(&t("Join"), move || a.join_invitation(chosen.clone())));
        content.append(&row);
    }
    frame
}

/// While this computer is shared: who is connected, a colleague asking to join, a meeting offer,
/// switching sides and ending.
fn hosted_session(app: &Rc<App>, state: &State) -> gtk::Box {
    let remote = &state.remote;
    let (frame, content) = card(&t("A technician is connected to this computer."), Some("network-wireless-signal-excellent-symbolic"));
    content.append(&dim(&if remote.control { t("They can see your screen and use keyboard and mouse.") } else { t("They can see your screen.") }));
    if remote.recording_permitted {
        content.append(&label(&t("You allowed the other person to save a video of this session.")));
    }
    // A colleague still asking to join is shown with the question below, not as connected.
    for colleague in remote.colleagues.iter().filter(|colleague| colleague.state != "asking") {
        let row = hbox(10);
        let name = label(&tf("Colleague %@", &[&format::remote_id(Some(colleague.connection))]));
        row.append(&name);
        let what = caption(&if colleague.control { t("View and control") } else { t("View only") });
        what.set_hexpand(true);
        row.append(&what);
        let (a, chosen) = (app.clone(), colleague.clone());
        row.append(&button(&t("Remove"), move || a.remove_colleague(chosen.clone())));
        content.append(&row);
    }
    if let Some(request) = &remote.join_request {
        content.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
        content.append(&heading(&t("A second technician wants to join")));
        content.append(&dim(&tf("Technician %@ was invited by the technician who is connected.", &[&format::remote_id(Some(request.connection))])));
        let row = hbox(8);
        let a = app.clone();
        row.append(&button(&t("Decline"), move || a.decide_join(false, false)));
        let a = app.clone();
        row.append(&button(&t("Allow viewing"), move || a.decide_join(true, false)));
        if request.control {
            let a = app.clone();
            row.append(&primary(&t("Allow viewing and control"), move || a.decide_join(true, true)));
        }
        content.append(&row);
    }
    if logic::meeting_offer(remote, &state.session.dismissed_meeting_room).is_some() {
        content.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
        content.append(&heading(&t("The technician wants to show you their screen.")));
        let row = hbox(8);
        let a = app.clone();
        row.append(&button(&t("Not now"), move || a.dismiss_meeting_offer()));
        let a = app.clone();
        row.append(&primary(&t("Show it"), move || a.open_meeting_offer()));
        content.append(&row);
    }
    let row = hbox(8);
    if state.can_switch_sides() && !remote.switched {
        let a = app.clone();
        row.append(&button(&t("Switch sides"), move || a.switch_sides()));
    }
    row.append(&spacer());
    let a = app.clone();
    row.append(&destructive(&t("End session"), move || a.end_session()));
    content.append(&row);
    frame
}

fn unattended_card(app: &Rc<App>, state: &State) -> gtk::Box {
    let remote = &state.remote;
    let host = &state.host;
    let (frame, content) = card(&t("Unattended sharing"), Some("preferences-system-time-symbolic"));
    content.append(&dim(&logic::unattended_text(remote)));
    if remote.unattended_enabled || remote.unattended_pending.as_deref() == Some("enabling") {
        let a = app.clone();
        let off = button(&t("Turn off unattended sharing"), move || a.disable_unattended());
        off.set_halign(gtk::Align::Start);
        content.append(&off);
    } else if host.unattended_sources.is_empty() {
        let a = app.clone();
        let choose = button(&t("Choose a display"), move || a.load_unattended_sources());
        choose.set_sensitive(!state.busy);
        choose.set_halign(gtk::Align::Start);
        content.append(&choose);
    } else {
        let names: Vec<String> = host.unattended_sources.iter().map(|source| engine(&source.name)).collect();
        let ids: Vec<String> = host.unattended_sources.iter().map(|source| source.id.clone()).collect();
        let selected = ids.iter().position(|id| *id == host.unattended_source).unwrap_or(0);
        let a = app.clone();
        content.append(&field(&t("Display"), &choice(&names, selected, move |index| {
            let id = ids.get(index).cloned().unwrap_or_default();
            a.host_edit(|host| host.unattended_source = id);
        })));
        let a = app.clone();
        let control = check(&t("Allow keyboard and mouse control"), host.unattended_control, move |on| a.host_edit(|host| host.unattended_control = on));
        control.set_sensitive(remote.input_access);
        content.append(&control);
        let a = app.clone();
        content.append(&check(&t("I confirm that approved users may see this display without asking."), host.unattended_confirm, move |on| a.host_edit(|host| host.unattended_confirm = on)));
        let a = app.clone();
        let enable = primary(&t("Enable unattended sharing"), move || a.enable_unattended());
        enable.set_sensitive(!state.busy && host.unattended_confirm && !remote.code_required);
        enable.set_halign(gtk::Align::Start);
        content.append(&enable);
        if state.linux.wayland {
            content.append(&caption(&t("Wayland requires local confirmation for each sharing session.")));
        }
    }
    append_message(&content, &host.error, true);
    frame
}
