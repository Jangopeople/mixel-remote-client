//! Settings: stay signed in, start with this computer, language, this computer (remove it), what this
//! desktop allows (X11 or Wayland), sign-in screen access, remote printing, updates, About and legal.
use super::{dialogs, widgets::*};
use crate::{
    app::{App, State},
    format,
    i18n::{self, t, tf},
    logic::Section,
    workspace::{OpenIn, WhenReady},
};
use adw::prelude::*;
use std::{cell::RefCell, rc::Rc};

pub struct SettingsPage {
    pub widget: gtk::ScrolledWindow,
    body: gtk::Box,
    key: RefCell<String>,
    signin_name: gtk::Entry,
    companies: RefCell<Vec<(String, String)>>,
}

fn group(title: &str, description: &str) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::new();
    // Titles and descriptions are Pango markup: "&" must be escaped.
    group.set_title(&gtk::glib::markup_escape_text(title));
    if !description.is_empty() {
        group.set_description(Some(&gtk::glib::markup_escape_text(description)));
    }
    group
}

fn switch_row(app: &Rc<App>, title: &str, subtitle: &str, active: bool, sensitive: bool, action: impl Fn(bool) + 'static) -> adw::ActionRow {
    let row = adw::ActionRow::builder().title(title).build();
    if !subtitle.is_empty() {
        row.set_subtitle(subtitle);
    }
    let switch = gtk::Switch::new();
    switch.set_active(active);
    switch.set_valign(gtk::Align::Center);
    switch.set_sensitive(sensitive);
    let a = app.clone();
    switch.connect_active_notify(move |switch| {
        if !a.rendering.get() {
            action(switch.is_active());
        }
    });
    row.add_suffix(&switch);
    row.set_activatable_widget(Some(&switch));
    row
}

fn info_row(title: &str, value: &str) -> adw::ActionRow {
    let row = adw::ActionRow::builder().title(title).build();
    let label = gtk::Label::new(Some(value));
    label.set_selectable(true);
    label.add_css_class("dim-label");
    row.add_suffix(&label);
    row
}

impl SettingsPage {
    pub fn new(app: &Rc<App>) -> Self {
        let (widget, body) = page(&t("Settings"), "");
        let _ = app;
        Self { widget, body, key: RefCell::new(String::new()), signin_name: entry(&t("Computer name")), companies: RefCell::new(Vec::new()) }
    }

    pub fn render(&self, app: &Rc<App>, state: &State) {
        let settings = &state.settings;
        let key = format!(
            "{:?}",
            ((&settings.startup, &settings.printing, &settings.update, settings.update_checked, settings.removal_pending, settings.screen_access), (&settings.signin, &settings.error, &settings.notice, &settings.removal_result, i18n::choice(), state.signed_in()), (state.staff(), state.remote.host, state.remote.input_access, &state.enrollment.current, &state.enrollment.error, state.enrollment.working), (&state.enrollment.companies, &state.linux), (state.workspace.when_ready, state.workspace.open_in, app.store.borrow().settings.appearance.clone(), state.remote.unattended_enabled))
        );
        if *self.key.borrow() == key {
            return;
        }
        self.key.replace(key);
        keep_focus(&[self.signin_name.clone().upcast()], || self.build(app, state));
    }

    fn build(&self, app: &Rc<App>, state: &State) {
        let body = &self.body;
        clear_below_title(body);
        let settings = &state.settings;
        let startup = settings.startup.clone().unwrap_or_default();

        // Sessions: what happens when a session is ready, and where sessions open.
        let sessions = group(&t("Sessions"), "");
        let when_names = [t("Stay in Devices and show “Open”"), t("Switch to the session")];
        let when_model = gtk::StringList::new(&when_names.iter().map(String::as_str).collect::<Vec<_>>());
        let when = adw::ComboRow::builder().title(t("When a session is ready")).model(&when_model).build();
        when.set_selected(if state.workspace.when_ready == WhenReady::Open { 1 } else { 0 });
        let a = app.clone();
        when.connect_selected_notify(move |row| {
            if !a.rendering.get() {
                a.set_when_ready(if row.selected() == 1 { WhenReady::Open } else { WhenReady::Stay });
            }
        });
        sessions.add(&when);
        let in_names = [t("A tab in this window"), t("Its own window")];
        let in_model = gtk::StringList::new(&in_names.iter().map(String::as_str).collect::<Vec<_>>());
        let open_in = adw::ComboRow::builder().title(t("Open sessions in")).model(&in_model).build();
        open_in.set_selected(if state.workspace.open_in == OpenIn::Window { 1 } else { 0 });
        let a = app.clone();
        open_in.connect_selected_notify(move |row| {
            if !a.rendering.get() {
                a.set_open_in(if row.selected() == 1 { OpenIn::Window } else { OpenIn::Tab });
            }
        });
        sessions.add(&open_in);
        body.append(&sessions);

        // Unattended access of this computer (the details are on its own page).
        let unattended = group(&t("Unattended access"), &t("Lets technicians of your company connect to this computer without someone confirming each time."));
        let row = adw::ActionRow::builder().title(t("This computer")).subtitle(if state.remote.unattended_enabled { t("On") } else { t("Off") }).build();
        let a = app.clone();
        let open = button(&t("Configure…"), move || a.edit(|state| state.section = Section::Computer));
        open.set_valign(gtk::Align::Center);
        row.add_suffix(&open);
        unattended.add(&row);
        body.append(&unattended);

        let general = group(&t("General"), "");
        let appearance_names = [t("System"), t("Light"), t("Dark")];
        let appearance_ids = ["system", "light", "dark"];
        let appearance_model = gtk::StringList::new(&appearance_names.iter().map(String::as_str).collect::<Vec<_>>());
        let appearance = adw::ComboRow::builder().title(t("Appearance")).model(&appearance_model).build();
        let current = app.store.borrow().settings.appearance.clone();
        appearance.set_selected(appearance_ids.iter().position(|id| *id == current).unwrap_or(0) as u32);
        let a = app.clone();
        appearance.connect_selected_notify(move |row| {
            if !a.rendering.get() {
                a.set_appearance(appearance_ids[row.selected() as usize % appearance_ids.len()]);
            }
        });
        general.add(&appearance);
        let a = app.clone();
        general.add(&switch_row(app, &t("Stay signed in on this computer"), "", startup.remembered, state.signed_in(), move |on| a.set_stay_signed_in(on)));
        let a = app.clone();
        general.add(&switch_row(app, &t("Start Mixel Remote when this computer starts"), "", startup.autostart, true, move |on| a.set_autostart(on)));
        let choices = ["auto", "en", "de", "fr", "it"];
        let names = [t("Automatic (this computer’s language)"), "English".into(), "Deutsch".into(), "Français".into(), "Italiano".into()];
        let model = gtk::StringList::new(&names.iter().map(String::as_str).collect::<Vec<_>>());
        let language = adw::ComboRow::builder().title(t("Language")).model(&model).build();
        language.set_selected(choices.iter().position(|choice| *choice == i18n::choice()).unwrap_or(0) as u32);
        let a = app.clone();
        language.connect_selected_notify(move |row| {
            if a.rendering.get() {
                return;
            }
            let value = choices[row.selected() as usize % choices.len()];
            let a = a.clone();
            // After the current signal: the screens are built again with the new language.
            gtk::glib::idle_add_local_once(move || a.set_language(value));
        });
        general.add(&language);

        let this = group(&t("This computer"), "");
        this.add(&info_row(&t("Connection ID"), &format::remote_id(state.remote.host.or(state.enrollment.current.as_ref().map(|current| current.connection_id)))));
        let removal = vbox(8);
        if settings.removal_pending {
            append_message(&removal, &t("This computer is being removed from Mixel Remote. It is removed as soon as it is online."), false);
        } else if state.enrollment.current.is_some() || state.remote.host.is_some() {
            let a = app.clone();
            let remove = destructive(&t("Remove this computer from Mixel Remote"), move || remove_dialog(&a));
            remove.set_sensitive(!state.enrollment.working);
            remove.set_halign(gtk::Align::Start);
            removal.append(&remove);
        } else {
            removal.append(&dim(&t("This computer is not set up yet.")));
        }
        append_message(&removal, &settings.removal_result, false);
        append_message(&removal, &state.enrollment.error, true);
        removal.set_margin_top(8);
        this.add(&removal);
        body.append(&this);

        // What this Linux desktop allows.
        let desktop = group(&t("Sharing this screen"), &if state.linux.wayland {
            t("This computer uses Wayland. Each time a technician connects, the system asks which screen to share. Wayland lets no program darken the screen, block keyboard and mouse or show another person's pointer, and the sign-in screen cannot be shared. Sign in with an Xorg session (for example “Ubuntu on Xorg”) for these.")
        } else if state.linux.x11 {
            t("This computer uses X11: screen, keyboard and mouse can be shared, and the privacy screen, input blocking and the technician's pointer work.")
        } else {
            t("No desktop session was found for sharing this screen.")
        });
        desktop.add(&info_row(&t("Screen sharing"), &if settings.screen_access { t("Allowed") } else { t("Not available") }));
        desktop.add(&info_row(&t("Keyboard and mouse"), &if state.remote.input_access { t("Allowed") } else { t("Not available") }));
        body.append(&desktop);

        if let Some(signin) = &settings.signin {
            body.append(&self.signin_group(app, state, signin));
        }

        if let Some(printing) = settings.printing.as_ref().filter(|printing| printing.supported) {
            let print = group(&t("Remote printing"), &t("Adds the printer “Mixel Remote Printer” to this computer. What is printed on it during a session arrives at the technician, who prints it on a printer there. Needs administrator approval on this computer."));
            let a = app.clone();
            print.add(&switch_row(app, &t("Remote printing"), "", printing.installed, true, move |on| a.set_printing(on)));
            body.append(&print);
        }

        let updates = group(&t("Updates"), "");

        updates.add(&info_row(&t("Version"), &app.info.version));
        let row = adw::ActionRow::builder().title(t("Check for updates")).build();
        if let Some(update) = &settings.update {
            let mut subtitle = tf("Version %@ is available.", &[&update.version]);
            if update.waits_for_session {
                subtitle.push_str(" ");
                subtitle.push_str(&t("Update ready, installs after this session"));
            }
            row.set_subtitle(&subtitle);
            let a = app.clone();
            let install = if update.installable { primary(&t("Update now"), move || a.install_update()) } else { button(&t("Download"), move || a.open_uri("https://store.mixel.ch/app/mixel-remote")) };
            install.set_valign(gtk::Align::Center);
            row.add_suffix(&install);
        } else if settings.update_checked {
            row.set_subtitle(&t("This is the newest version."));
        }
        let a = app.clone();
        let check = button(&t("Check now"), move || a.check_for_update());
        check.set_valign(gtk::Align::Center);
        row.add_suffix(&check);
        updates.add(&row);
        body.append(&updates);
        body.append(&general);

        // Account and security.
        let security = group(&t("Account & security"), "");
        let account_row = adw::ActionRow::builder().title(t("Account")).subtitle(t("Profile, company, members and second step")).build();
        let a = app.clone();
        let open_account = button(&t("Open account"), move || a.edit(|state| state.section = Section::Account));
        open_account.set_valign(gtk::Align::Center);
        account_row.add_suffix(&open_account);
        security.add(&account_row);
        let sign_out_row = adw::ActionRow::builder().title(t("Sign out")).subtitle(t("This computer goes offline and is no longer reachable until you sign in again.")).build();
        let a = app.clone();
        let sign_out = destructive(&t("Sign out"), move || a.sign_out());
        sign_out.set_valign(gtk::Align::Center);
        sign_out.set_sensitive(state.signed_in());
        sign_out_row.add_suffix(&sign_out);
        security.add(&sign_out_row);
        body.append(&security);

        let about = group(&t("About and legal"), "");
        let block = vbox(10);
        let head = hbox(12);
        head.append(&mark(44));
        let text = vbox(2);
        let name = gtk::Label::new(Some("Mixel Remote"));
        name.add_css_class("title-3");
        name.set_xalign(0.0);
        text.append(&name);
        text.append(&dim(&tf("Version %@ for Linux", &[&app.info.version])));
        head.append(&text);
        block.append(&head);
        block.append(&caption("© Mixel IT and Corporate Services"));
        let links = gtk::FlowBox::new();
        links.set_selection_mode(gtk::SelectionMode::None);
        for (title, page) in [(t("Terms of Service"), "terms"), (t("Privacy Policy"), "privacy"), (t("Data Processing Agreement"), "dpa"), (t("Sub-processors"), "subprocessors")] {
            let a = app.clone();
            links.insert(&link(&title, move || a.open_legal(page)), -1);
        }
        block.append(&links);
        block.set_margin_top(6);
        about.add(&block);
        body.append(&about);
        append_message(body, &settings.notice, false);
        append_message(body, &settings.error, true);
    }

    fn signin_group(&self, app: &Rc<App>, state: &State, signin: &crate::models::SignInScreen) -> adw::PreferencesGroup {
        let group = group(&t("Sign-in screen access"), &t("Technicians of your company can reach this computer at its sign-in screen and while nobody is signed in (X11 sign-in screens only). Needs administrator approval on this computer."));
        let content = vbox(10);
        content.set_margin_top(8);
        if let Some(id) = signin.connection_id {
            let name = signin.name.clone().unwrap_or_default();
            if signin.wayland_greeter {
                content.append(&label(&tf("Always-on access is installed (Remote ID %@), but this computer’s sign-in screen uses Wayland, which cannot be shared. Switch the sign-in screen to Xorg (GDM: WaylandEnable=false) to use it.", &[&format::remote_id(Some(id))])));
            } else {
                content.append(&label(&tf("On for %@ (Remote ID %@).", &[&name, &format::remote_id(Some(id))])));
            }
            if signin.technician {
                content.append(&caption(&t("A technician is connected through it right now.")));
            }
            let row = hbox(8);
            if signin.outdated {
                let a = app.clone();
                row.append(&primary(&t("Update sign-in screen access"), move || a.change_signin_screen(true)));
            }
            let a = app.clone();
            row.append(&destructive(&t("Turn off sign-in screen access"), move || a.change_signin_screen(false)));
            content.append(&row);
        } else if state.staff() {
            if state.enrollment.companies.is_empty() {
                let a = app.clone();
                let load = button(&t("Set up sign-in screen access…"), move || a.load_team_records(String::new()));
                load.set_halign(gtk::Align::Start);
                content.append(&load);
            } else {
                let companies: Vec<(String, String)> = state.enrollment.companies.iter().map(|company| (company.client_id.clone(), company.client_name.clone())).collect();
                self.companies.replace(companies.clone());
                let names: Vec<String> = companies.iter().map(|(_, name)| name.clone()).collect();
                let pick = choice(&names, 0, |_| {});
                content.append(&field(&t("Company"), &pick));
                if self.signin_name.text().is_empty() {
                    self.signin_name.set_text(&state.enrollment.name);
                }
                content.append(&field(&t("Computer name"), &adopt(&self.signin_name)));
                let (a, name) = (app.clone(), self.signin_name.clone());
                let enable = primary(&t("Turn on sign-in screen access"), move || {
                    if let Some((company, _)) = companies.get(pick.selected() as usize) {
                        a.enable_signin_screen(company.clone(), name.text().trim().to_owned());
                    }
                });
                enable.set_halign(gtk::Align::Start);
                content.append(&enable);
            }
        } else {
            content.append(&dim(&t("Computers of a company get sign-in screen access with the company's deploy link (Devices › Add other computers).")));
        }
        group.add(&content);
        group
    }
}

fn remove_dialog(app: &Rc<App>) {
    let Some(parent) = app.ui.borrow().as_ref().map(|ui| ui.window.clone()) else { return };
    let a = app.clone();
    dialogs::confirm(&parent, &t("Remove this computer from Mixel Remote?"), &t("It can no longer be reached or connect until it is added again."), &t("Remove this computer"), move || {
        let a = a.clone();
        a.clone().spawn(async move {
            let result = match a.remove_this_computer().await.as_deref() {
                Some("removed") => t("This computer was removed from Mixel Remote."),
                Some("queued") => t("Saved. This computer is removed as soon as Mixel Remote can be reached."),
                Some("stale") => t("This computer's registration changed since; nothing was removed."),
                _ => String::new(),
            };
            a.edit(|state| state.settings.removal_result = result);
        });
    });
}
