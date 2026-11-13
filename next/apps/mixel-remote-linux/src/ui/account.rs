//! Account: sign in with Mixel ID (system browser, then the second step), the company with plan,
//! usage, members, roles and invitations, renaming, creating a company or a personal space, data
//! export and deleting the company or one's own account.
use super::{dialogs, widgets::*};
use crate::{
    app::{App, State},
    format,
    i18n::{engine, t, tf},
    logic,
    models::Account,
};
use adw::prelude::*;
use std::{cell::RefCell, rc::Rc};

pub struct AccountPage {
    pub widget: gtk::ScrolledWindow,
    body: gtk::Box,
    key: RefCell<String>,
    code: gtk::Entry,
    company_name: gtk::Entry,
    terms: gtk::CheckButton,
    personal_name: gtk::Entry,
}

impl AccountPage {
    pub fn new(app: &Rc<App>) -> Self {
        let (widget, body) = page(&t("Account"), "");
        let code = entry("123456");
        code.set_input_purpose(gtk::InputPurpose::Digits);
        code.set_max_length(6);
        code.set_width_chars(10);
        code.set_hexpand(false);
        code.add_css_class("mixel-id-entry");
        code.update_property(&[gtk::accessible::Property::Label(&t("Verification code"))]);
        let a = app.clone();
        code.connect_activate(move |entry| verify(&a, entry));
        Self {
            widget,
            body,
            key: RefCell::new(String::new()),
            code,
            company_name: entry(&t("Company name")),
            terms: gtk::CheckButton::with_label(&t("I accept the Terms of Service and the Data Processing Agreement")),
            personal_name: entry(&t("Name shown to your own devices")),
        }
    }

    pub fn render(&self, app: &Rc<App>, state: &State) {
        let key = format!(
            "{:?}",
            (&state.account, (&state.sign_in_link, &state.providers), &state.error, state.busy, &state.company.data, &state.company.error, &state.company.notice, state.company.working)
        );
        if *self.key.borrow() == key {
            return;
        }
        self.key.replace(key);
        let entries: Vec<gtk::Widget> = vec![self.code.clone().upcast(), self.company_name.clone().upcast(), self.personal_name.clone().upcast()];
        keep_focus(&entries, || self.build(app, state));
    }

    fn build(&self, app: &Rc<App>, state: &State) {
        let body = &self.body;
        while let Some(child) = body.first_child().and_then(|first| first.next_sibling()) {
            body.remove(&child);
        }
        if let (true, Some(profile)) = (state.signed_in(), state.account.account.as_ref()) {
            body.append(&profile_card(app, state, profile));
            if state.company.data.tenant().is_some() {
                body.append(&company_card(app, state));
            } else if state.company.data.loaded && !profile.staff {
                body.append(&self.create_company_card(app, state, profile));
            }
            body.append(&danger_card(app));
        } else {
            let line = dim(&t("A secure home for your devices and connections."));
            line.set_margin_top(-12);
            body.append(&line);
            body.append(&self.sign_in_card(app, state));
        }
        append_message(body, &state.error, true);
    }

    fn sign_in_card(&self, app: &Rc<App>, state: &State) -> gtk::Box {
        let account = &state.account;
        let (frame, content) = card(&t("Connect with your Mixel account"), Some("avatar-default-symbolic"));
        match account.phase.as_str() {
            "starting" | "waiting_browser" | "verifying" => {
                content.append(&spinner_line(&engine(&account.message)));
                if let Some(link) = &state.sign_in_link {
                    let row = hbox(8);
                    let text = monospace(link);
                    text.set_hexpand(true);
                    text.set_ellipsize(gtk::pango::EllipsizeMode::Middle);
                    text.set_wrap(false);
                    row.append(&text);
                    let (a, value) = (app.clone(), link.clone());
                    row.append(&icon_button("edit-copy-symbolic", &t("Copy"), move || {
                        if let Some(shell) = a.shell() {
                            shell.copy(&value);
                            shell.notify(&t("Copied"));
                        }
                    }));
                    content.append(&field(&t("Open this link in your other browser"), &row));
                }
                let a = app.clone();
                let cancel = button(&t("Cancel sign-in"), move || a.cancel_sign_in());
                cancel.set_halign(gtk::Align::Start);
                content.append(&cancel);
            }
            "mfa_required" => {
                content.append(&dim(&t("Additional verification is required for this Mixel account")));
                let factor = account.factors.first().cloned();
                if let Some(name) = factor.as_ref().and_then(|factor| factor.friendly_name.clone()) {
                    content.append(&label(&tf("Enter the code from the authenticator entry “%@”.", &[&name])));
                }
                content.append(&field(&t("Verification code"), &adopt(&self.code)));
                let row = hbox(8);
                let (a, code) = (app.clone(), self.code.clone());
                let go = primary(&t("Verify"), move || verify(&a, &code));
                go.set_sensitive(factor.is_some() && !state.busy);
                row.append(&go);
                let a = app.clone();
                row.append(&button(&t("Cancel sign-in"), move || a.cancel_sign_in()));
                content.append(&row);
                let code = self.code.clone();
                gtk::glib::idle_add_local_once(move || {
                    code.grab_focus();
                });
            }
            _ => {
                content.append(&dim(&t("Sign in with your Mixel ID. Your browser opens; come back here when it says you are signed in.")));
                if account.phase == "error" {
                    append_message(&content, &engine(&account.message), true);
                }
                let row = hbox(8);
                let a = app.clone();
                let sign_in = primary(&t("Sign in with Mixel ID"), move || a.sign_in(false, None));
                sign_in.set_sensitive(!state.busy);
                row.append(&sign_in);
                let a = app.clone();
                let other = button(&t("Use another browser"), move || a.sign_in(true, None));
                other.set_sensitive(!state.busy);
                row.append(&other);
                content.append(&row);
                // Google, Microsoft and Apple, when the sign-in service offers them (same browser flow).
                let outside: Vec<(&str, String)> = [("google", "Google"), ("azure", "Microsoft"), ("apple", "Apple")]
                    .into_iter()
                    .filter(|(id, _)| state.providers.iter().any(|offered| offered == id))
                    .map(|(id, name)| (id, tf("Continue with %@", &[&name])))
                    .collect();
                if !outside.is_empty() {
                    let row = gtk::FlowBox::new();
                    row.set_selection_mode(gtk::SelectionMode::None);
                    row.set_max_children_per_line(3);
                    for (id, title) in outside {
                        let (a, id) = (app.clone(), id.to_owned());
                        let start = button(&title, move || a.sign_in(false, Some(id.clone())));
                        start.set_sensitive(!state.busy);
                        row.insert(&start, -1);
                    }
                    content.append(&row);
                }
                let a = app.clone();
                content.append(&link(&t("Create Mixel ID"), move || a.open_uri("https://auth.mixel.ch/if/flow/mixel-remote-enrollment/")));
            }
        }
        frame
    }

    fn create_company_card(&self, app: &Rc<App>, state: &State, profile: &Account) -> gtk::Box {
        let (frame, content) = card(&t("Create a company"), Some("system-users-symbolic"));
        content.append(&dim(&t("Your company gets a 14-day Business trial, no card needed. You become its owner and can invite your team.")));
        content.append(&field(&t("Company name"), &adopt(&self.company_name)));
        adopt(&self.terms);
        content.append(&self.terms);
        let a = app.clone();
        content.append(&link(&t("Read the Terms of Service, the Data Processing Agreement and the Privacy Policy"), move || a.open_legal("legal")));
        let (a, name, terms) = (app.clone(), self.company_name.clone(), self.terms.clone());
        let create = primary(&t("Create company"), move || {
            let (a, name, accepted) = (a.clone(), name.text().to_string(), terms.is_active());
            a.clone().spawn(async move {
                let _ = a.create_company(name, accepted).await;
            });
        });
        create.set_sensitive(!state.company.working);
        create.set_halign(gtk::Align::Start);
        content.append(&create);
        if !profile.has_personal {
            content.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
            content.append(&heading(&t("Or a personal space for your own devices")));
            let row = hbox(8);
            row.append(&adopt(&self.personal_name));
            let (a, name) = (app.clone(), self.personal_name.clone());
            row.append(&button(&t("Create personal space"), move || {
                let text = name.text().trim().to_owned();
                if text.is_empty() {
                    return;
                }
                let a = a.clone();
                a.clone().spawn(async move {
                    let _ = a.create_personal_space(text).await;
                });
            }));
            content.append(&row);
        }
        append_message(&content, &state.company.error, true);
        frame
    }
}

fn verify(app: &Rc<App>, code: &gtk::Entry) {
    let Some(factor) = app.state.borrow().account.factors.first().map(|factor| factor.id.clone()) else { return };
    let text = code.text().to_string();
    code.set_text("");
    app.verify(factor, &text);
}

fn profile_card(app: &Rc<App>, state: &State, profile: &Account) -> gtk::Box {
    let (frame, content) = card("", None);
    let row = hbox(14);
    let avatar = adw::Avatar::new(48, profile.display_name.as_deref(), true);
    row.append(&avatar);
    let text = vbox(3);
    text.set_hexpand(true);
    let name = gtk::Label::new(Some(profile.display_name.as_deref().unwrap_or(&t("Signed in"))));
    name.add_css_class("title-3");
    name.set_xalign(0.0);
    text.append(&name);
    text.append(&dim(&logic::account_kind(profile, &state.company.data)));
    if profile.remembered {
        text.append(&caption(&t("Stays signed in on this computer")));
    }
    row.append(&text);
    let a = app.clone();
    let out = button(&t("Sign out"), move || a.sign_out());
    out.set_valign(gtk::Align::Start);
    row.append(&out);
    content.append(&row);
    if profile.has_personal && profile.has_company {
        let names = [t("Company devices"), t("Personal devices")];
        let a = app.clone();
        let pick = choice(&names, usize::from(profile.personal), move |index| {
            if !a.rendering.get() {
                a.set_workspace(index == 1);
            }
        });
        content.append(&field(&t("Devices of"), &pick));
    }
    if !profile.companies.is_empty() {
        content.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
        content.append(&heading(&t("Company administration")));
        for item in &profile.companies {
            let line = hbox(8);
            let name = label(&item.company_name);
            name.set_hexpand(true);
            line.append(&name);
            let (a, id) = (app.clone(), item.company_id.clone());
            line.append(&button(&t("Manage devices and company"), move || a.open_company_settings(id.clone())));
            content.append(&line);
        }
    }
    frame
}

fn company_card(app: &Rc<App>, state: &State) -> gtk::Box {
    let company = &state.company.data;
    let tenant = company.tenant().cloned().unwrap_or_default();
    let (frame, content) = card(&tenant.name, Some("system-users-symbolic"));
    let me = state.account.account.as_ref().map(|account| account.profile.clone()).unwrap_or_default();
    if company.administers() {
        let (a, name) = (app.clone(), tenant.name.clone());
        let rename = button(&t("Rename company"), move || {
            let Some(parent) = a.ui.borrow().as_ref().map(|ui| ui.window.clone()) else { return };
            let a2 = a.clone();
            dialogs::form(&parent, &t("Rename company"), "", &[(&t("Company name"), &name)], &t("Rename"), false, Rc::new(move |values| {
                let a = a2.clone();
                Box::pin(async move { a.rename_company(values[0].trim().to_owned()).await })
            }));
        });
        rename.set_sensitive(!company.read_only());
        rename.set_halign(gtk::Align::Start);
        content.append(&rename);
    }
    let facts = hbox(16);
    facts.append(&dim(&company.plan_line()));
    let usage = company.usage_line();
    if !usage.is_empty() {
        facts.append(&dim(&usage));
    }
    content.append(&facts);
    if company.read_only() {
        append_message(&content, &t("This company is read-only. You can still remove devices; adding devices and other changes need an active plan."), false);
    }
    content.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
    let header = hbox(8);
    let title = heading(&t("Members"));
    title.set_hexpand(true);
    header.append(&title);
    if company.administers() {
        let a = app.clone();
        let invite = button(&t("Invite a person"), move || invite_dialog(&a));
        invite.set_sensitive(!company.read_only());
        header.append(&invite);
    }
    content.append(&header);
    let members = gtk::ListBox::new();
    members.add_css_class("boxed-list");
    members.set_selection_mode(gtk::SelectionMode::None);
    for member in &company.members {
        let row = adw::ActionRow::builder().title(if member.name.is_empty() { &member.email } else { &member.name }).subtitle(&member.email).build();
        if company.administers() && member.user_id != me {
            let mut items: Vec<MenuItem> = Vec::new();
            for role in ["owner", "admin", "technician", "member"] {
                let (a, chosen) = (app.clone(), member.clone());
                items.push(Some((format::role(role), role != member.role, Box::new(move || a.set_member_role(chosen.clone(), role)))));
            }
            items.push(None);
            let (a, chosen) = (app.clone(), member.clone());
            items.push(Some((t("Remove from company"), true, Box::new(move || a.remove_member(chosen.clone())))));
            let role = menu(&format::role(&member.role), items);
            role.set_valign(gtk::Align::Center);
            role.update_property(&[gtk::accessible::Property::Label(&tf("Role of %@", &[&member.email]))]);
            row.add_suffix(&role);
        } else {
            row.add_suffix(&tag(&format::role(&member.role)));
            if member.user_id == me && !company.is_owner() {
                let (a, chosen) = (app.clone(), member.clone());
                let leave = button(&t("Leave"), move || a.remove_member(chosen.clone()));
                leave.set_valign(gtk::Align::Center);
                row.add_suffix(&leave);
            }
        }
        members.append(&row);
    }
    content.append(&members);
    if company.administers() && !company.invites.is_empty() {
        content.append(&heading(&t("Open invitations")));
        let invites = gtk::ListBox::new();
        invites.add_css_class("boxed-list");
        invites.set_selection_mode(gtk::SelectionMode::None);
        for invite in &company.invites {
            let row = adw::ActionRow::builder().title(&invite.email).subtitle(format!("{} · {}", format::role(&invite.role), logic::invite_mail_state(invite))).build();
            let (a, chosen) = (app.clone(), invite.clone());
            let again = button(&t("Send again"), move || a.resend_invite(chosen.clone()));
            again.set_valign(gtk::Align::Center);
            row.add_suffix(&again);
            let (a, chosen) = (app.clone(), invite.clone());
            let revoke = destructive(&t("Revoke"), move || a.revoke_invite(chosen.clone()));
            revoke.set_valign(gtk::Align::Center);
            row.add_suffix(&revoke);
            invites.append(&row);
        }
        content.append(&invites);
    }
    append_message(&content, &state.company.notice, false);
    append_message(&content, &state.company.error, true);
    if company.is_owner() {
        content.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
        content.append(&heading(&t("Data and deletion")));
        let row = hbox(8);
        let a = app.clone();
        row.append(&button(&t("Export data in the portal"), move || a.export_company_data()));
        let a = app.clone();
        row.append(&destructive(&t("Delete company…"), move || {
            let Some(parent) = a.ui.borrow().as_ref().map(|ui| ui.window.clone()) else { return };
            let a2 = a.clone();
            dialogs::form(
                &parent,
                &t("Delete company"),
                &t("Deletes the company at once: devices, invitations, members and their accounts. The subscription ends. Type the company name to confirm."),
                &[(&t("Company name"), "")],
                &t("Delete company"),
                true,
                Rc::new(move |values| {
                    let a = a2.clone();
                    Box::pin(async move { a.delete_company(values[0].clone()).await })
                }),
            );
        }));
        content.append(&row);
    }
    frame
}

fn danger_card(app: &Rc<App>) -> gtk::Box {
    let (frame, content) = card(&t("Delete my account"), Some("user-trash-symbolic"));
    content.append(&dim(&t("Deletes your Mixel Remote account and signs you out on every device. Company data stays with the company.")));
    let a = app.clone();
    let delete = destructive(&t("Delete my account…"), move || {
        let Some(parent) = a.ui.borrow().as_ref().map(|ui| ui.window.clone()) else { return };
        let a2 = a.clone();
        dialogs::form(&parent, &t("Delete my account"), &t("Type your e-mail address to confirm. This cannot be undone."), &[(&t("E-mail"), "")], &t("Delete my account"), true, Rc::new(move |values| {
            let a = a2.clone();
            Box::pin(async move { a.delete_account(values[0].trim().to_owned()).await })
        }));
    });
    delete.set_halign(gtk::Align::Start);
    content.append(&delete);
    frame
}

fn invite_dialog(app: &Rc<App>) {
    let Some(parent) = app.ui.borrow().as_ref().map(|ui| ui.window.clone()) else { return };
    let (dialog, content, actions) = dialogs::window(&parent, &t("Invite a person"), 440);
    content.append(&dim(&t("Mixel Remote e-mails the invitation link. Administrators and technicians count against the technicians of your plan.")));
    let email = entry("name@company.ch");
    email.set_input_purpose(gtk::InputPurpose::Email);
    content.append(&field(&t("E-mail"), &email));
    let roles = ["admin", "technician", "member"];
    let names: Vec<String> = roles.iter().map(|role| format::role(role)).collect();
    let pick = choice(&names, 1, |_| {});
    content.append(&field(&t("Role"), &pick));
    let error = vbox(0);
    content.append(&error);
    let close = dialog.clone();
    actions.append(&button(&t("Cancel"), move || close.close()));
    let (a, d, e, mail, picked) = (app.clone(), dialog.clone(), error.clone(), email.clone(), pick.clone());
    let send = Rc::new(move || {
        let role = roles[picked.selected() as usize % roles.len()].to_owned();
        let (a, d, e, address) = (a.clone(), d.clone(), e.clone(), mail.text().to_string());
        a.clone().spawn(async move {
            match a.invite_member(address, role).await {
                Ok(()) => d.close(),
                Err(text) => {
                    clear(&e);
                    append_message(&e, &text, true);
                }
            }
        });
    });
    let go = send.clone();
    actions.append(&primary(&t("Send invitation"), move || go()));
    email.connect_activate(move |_| send());
    dialog.present();
    email.grab_focus();
}
