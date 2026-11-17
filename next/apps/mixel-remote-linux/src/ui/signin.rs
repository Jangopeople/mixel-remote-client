//! The sign-in screen: Continue with Microsoft, Google or Apple (when the sign-in service offers them),
//! Continue with e-mail (the Mixel ID in the browser), "Stay signed in on this computer", the second
//! step (verification code), Quick Support for a person without an account, and the legal links.
//! Shown instead of the console while nobody is signed in.
use super::{account, widgets::*};
use crate::{
    app::{App, State},
    i18n::{engine, t, tf},
};
use adw::prelude::*;
use std::{cell::RefCell, rc::Rc};

pub struct SigninPage {
    pub widget: adw::Leaflet,
    form: gtk::Box,
    key: RefCell<String>,
    code: gtk::Entry,
    stay: gtk::CheckButton,
}

impl SigninPage {
    pub fn new() -> Self {
        let widget = adw::Leaflet::new();
        widget.set_can_navigate_back(false);
        widget.set_transition_type(adw::LeafletTransitionType::Over);
        // The left side: the pitch. It folds away when the window is narrow.
        let hero = vbox(18);
        hero.add_css_class("mixel-signin-hero");
        hero.set_size_request(420, -1);
        hero.set_hexpand(true);
        let brand = hbox(10);
        brand.append(&mark(32));
        let name = gtk::Label::new(Some("Mixel Remote"));
        name.add_css_class("title-4");
        brand.append(&name);
        hero.append(&brand);
        hero.append(&spacer_v());
        let pitch = gtk::Label::builder()
            .label(t("Remote support that feels local."))
            .wrap(true)
            .xalign(0.0)
            .build();
        pitch.add_css_class("title-1");
        hero.append(&pitch);
        let lead = gtk::Label::builder().label(t("Reach every company device in seconds, run several sessions side by side, and file the report without leaving the session.")).wrap(true).xalign(0.0).build();
        hero.append(&lead);
        for text in [
            t("End-to-end encrypted, hosted in Switzerland"),
            t("Several sessions in tabs or windows"),
            t("Session reports linked to Mixel ISM"),
        ] {
            let row = hbox(8);
            row.append(&gtk::Image::from_icon_name("emblem-ok-symbolic"));
            let line = gtk::Label::builder()
                .label(text)
                .wrap(true)
                .xalign(0.0)
                .build();
            row.append(&line);
            hero.append(&row);
        }
        hero.append(&spacer_v());
        hero.append(&caption("Mixel IT and Corporate Services GmbH"));
        widget.append(&hero);
        let form = vbox(10);
        form.set_margin_top(32);
        form.set_margin_bottom(32);
        form.set_margin_start(28);
        form.set_margin_end(28);
        form.set_valign(gtk::Align::Center);
        let clamp = adw::Clamp::builder()
            .maximum_size(400)
            .tightening_threshold(340)
            .child(&form)
            .build();
        let scroller = gtk::ScrolledWindow::builder()
            .hscrollbar_policy(gtk::PolicyType::Never)
            .hexpand(true)
            .child(&clamp)
            .build();
        scroller.set_size_request(340, -1);
        widget.append(&scroller);
        widget.set_visible_child(&scroller);
        let stay = gtk::CheckButton::with_label(&t("Stay signed in on this computer"));
        stay.set_active(true);
        Self {
            widget,
            form,
            key: RefCell::new(String::new()),
            code: entry(&t("Verification code")),
            stay,
        }
    }

    pub fn render(&self, app: &Rc<App>, state: &State) {
        let account = &state.account;
        let key = format!(
            "{:?}",
            (
                &account.phase,
                &account.message,
                &state.sign_in_link,
                &state.providers,
                state.busy,
                &state.error,
                crate::i18n::choice(),
                &account.factors.first().map(|factor| factor.id.clone())
            )
        );
        if *self.key.borrow() == key {
            return;
        }
        self.key.replace(key);
        keep_focus(&[self.code.clone().upcast()], || self.build(app, state));
    }

    fn build(&self, app: &Rc<App>, state: &State) {
        let account = &state.account;
        clear(&self.form);
        let tile = mark(44);
        tile.set_halign(gtk::Align::Start);
        self.form.append(&tile);
        let title = gtk::Label::builder()
            .label(t("Sign in to Mixel Remote"))
            .xalign(0.0)
            .wrap(true)
            .accessible_role(gtk::AccessibleRole::Heading)
            .build();
        title.add_css_class("title-1");
        self.form.append(&title);
        match account.phase.as_str() {
            "starting" | "waiting_browser" | "verifying" => {
                self.form.append(&spinner_line(&engine(&account.message)));
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
                    self.form
                        .append(&field(&t("Open this link in your other browser"), &row));
                }
                let a = app.clone();
                let cancel = button(&t("Cancel sign-in"), move || a.cancel_sign_in());
                cancel.set_halign(gtk::Align::Start);
                self.form.append(&cancel);
            }
            "mfa_required" => {
                self.form.append(&dim(&t(
                    "Additional verification is required for this Mixel account",
                )));
                let factor = account.factors.first().cloned();
                if let Some(name) = factor
                    .as_ref()
                    .and_then(|factor| factor.friendly_name.clone())
                {
                    self.form.append(&label(&tf(
                        "Enter the code from the authenticator entry “%@”.",
                        &[&name],
                    )));
                }
                self.form
                    .append(&field(&t("Verification code"), &adopt(&self.code)));
                let row = hbox(8);
                let (a, code) = (app.clone(), self.code.clone());
                let go = primary(&t("Verify"), move || account::verify(&a, &code));
                go.set_sensitive(factor.is_some() && !state.busy);
                row.append(&go);
                let a = app.clone();
                row.append(&button(&t("Cancel sign-in"), move || a.cancel_sign_in()));
                self.form.append(&row);
                let code = self.code.clone();
                gtk::glib::idle_add_local_once(move || {
                    code.grab_focus();
                });
            }
            _ => {
                let lead = dim(&t(
                    "Use your Mixel ID. Your company decides which sign-in methods its members may use.",
                ));
                lead.set_wrap(true);
                self.form.append(&lead);
                if account.phase == "error" {
                    append_message(&self.form, &engine(&account.message), true);
                }
                for (id, name) in [
                    ("azure", "Microsoft"),
                    ("google", "Google"),
                    ("apple", "Apple"),
                ] {
                    if !state.providers.iter().any(|offered| offered == id) {
                        continue;
                    }
                    let (a, stay, provider) = (app.clone(), self.stay.clone(), id.to_owned());
                    let start = button(&tf("Continue with %@", &[&name]), move || {
                        a.state.borrow_mut().signin_stay = Some(stay.is_active());
                        a.sign_in(false, Some(provider.clone()));
                    });
                    start.add_css_class("mixel-sso");
                    start.set_sensitive(!state.busy);
                    self.form.append(&start);
                }
                if !state.providers.is_empty() {
                    let or = dim(&t("or"));
                    or.set_halign(gtk::Align::Center);
                    self.form.append(&or);
                }
                let (a, stay) = (app.clone(), self.stay.clone());
                let email = primary(&t("Continue with e-mail"), move || {
                    a.state.borrow_mut().signin_stay = Some(stay.is_active());
                    a.sign_in(false, None);
                });
                email.add_css_class("mixel-sso");
                email.set_sensitive(!state.busy);
                self.form.append(&email);
                self.form.append(&dim(&t(
                    "Your browser opens; come back here when it says you are signed in.",
                )));
                let a = app.clone();
                let other = link(&t("Use another browser"), move || a.sign_in(true, None));
                other.set_halign(gtk::Align::Start);
                other.set_sensitive(!state.busy);
                self.form.append(&other);
            }
        }
        adopt(&self.stay);
        self.stay.set_margin_top(4);
        self.form.append(&self.stay);

        // Quick Support: a person without an account gets help from a technician.
        let quick = hbox(10);
        quick.add_css_class("mixel-quick");
        quick.set_margin_top(8);
        quick.append(&gtk::Image::from_icon_name(
            "preferences-system-time-symbolic",
        ));
        let texts = vbox(1);
        texts.set_hexpand(true);
        let strong = label(&t("Need help, no account?"));
        strong.add_css_class("heading");
        texts.append(&strong);
        texts.append(&caption(&t("Get a support ID for your technician")));
        quick.append(&texts);
        let a = app.clone();
        quick.append(&button(&t("Quick Support"), move || {
            a.open_uri("https://remote.mixel.ch")
        }));
        self.form.append(&quick);

        // Legal.
        let fine = gtk::FlowBox::new();
        fine.set_selection_mode(gtk::SelectionMode::None);
        fine.set_margin_top(6);
        for (title, page) in [
            (t("Terms of Service"), "terms"),
            (t("Data Processing Agreement"), "dpa"),
            (t("Privacy Policy"), "privacy"),
        ] {
            let a = app.clone();
            fine.insert(&link(&title, move || a.open_legal(page)), -1);
        }
        self.form.append(&fine);
        let accept = caption(&t(
            "By continuing you accept the Terms of Service and the Data Processing Agreement.",
        ));
        accept.set_wrap(true);
        accept.set_xalign(0.0);
        self.form.append(&accept);
    }
}

fn spacer_v() -> gtk::Box {
    let space = vbox(0);
    space.set_vexpand(true);
    space
}
