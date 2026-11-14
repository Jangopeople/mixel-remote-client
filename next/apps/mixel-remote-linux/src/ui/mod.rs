//! The window: a sidebar with the sections and this computer's connection, the section pages, and
//! the session, which takes the whole window while it runs. libadwaita's look in Mixel's colours,
//! light or dark as the system is. The consent dialog appears over everything.
mod account;
mod consent;
mod devices;
pub mod dialogs;
mod remote;
mod reports;
mod session;
mod settings;
pub mod widgets;

use crate::{
    app::{App, Shell, State},
    i18n::{t, tf},
    logic::{self, Section},
};
use adw::prelude::*;
use gtk::{gdk, gio, glib};
use std::{
    cell::{Cell, RefCell},
    future::Future,
    pin::Pin,
    rc::Rc,
};
use widgets::*;

const CSS: &str = include_str!("style.css");
const CSS_DARK: &str = include_str!("style-dark.css");

struct Parts {
    root: gtk::Stack,
    leaflet: adw::Leaflet,
    sidebar: gtk::ListBox,
    footer: gtk::Box,
    footer_key: RefCell<String>,
    title: adw::WindowTitle,
    back: gtk::Button,
    pages: gtk::Stack,
    banner: gtk::Box,
    banner_key: RefCell<String>,
    remote: remote::RemotePage,
    devices: devices::DevicesPage,
    account: account::AccountPage,
    settings: settings::SettingsPage,
    reports: reports::ReportsPage,
    session: session::SessionView,
    section: Cell<Section>,
}

pub struct Ui {
    pub window: adw::ApplicationWindow,
    application: adw::Application,
    toasts: adw::ToastOverlay,
    parts: RefCell<Option<Parts>>,
    consent: consent::Consent,
    tray: RefCell<Option<crate::tray::Tray>>,
    quitting: Cell<bool>,
    fullscreen_session: Cell<bool>,
    session_keys: Cell<bool>,
}

/// The window's own way to ask the person (file panels) and to open links.
struct GtkShell {
    window: adw::ApplicationWindow,
    toasts: adw::ToastOverlay,
}

impl Shell for GtkShell {
    fn open_uri(&self, uri: &str) {
        if !uri.starts_with("https://") {
            return;
        }
        gtk::show_uri(Some(&self.window), uri, gdk::CURRENT_TIME);
    }

    fn choose_file(&self) -> Pin<Box<dyn Future<Output = Option<String>>>> {
        dialogs::choose(&self.window, gtk::FileChooserAction::Open, &t("Choose a file to send"), None)
    }

    fn choose_save(&self, suggested: &str) -> Pin<Box<dyn Future<Output = Option<String>>>> {
        dialogs::choose(&self.window, gtk::FileChooserAction::Save, &t("Save"), Some(suggested))
    }

    fn copy(&self, text: &str) {
        self.window.clipboard().set_text(text);
    }

    fn notify(&self, text: &str) {
        self.toasts.add_toast(adw::Toast::new(text));
    }
}

fn load_css() {
    let Some(display) = gdk::Display::default() else { return };
    let light = gtk::CssProvider::new();
    light.load_from_data(CSS);
    gtk::style_context_add_provider_for_display(&display, &light, gtk::STYLE_PROVIDER_PRIORITY_APPLICATION);
    let dark = gtk::CssProvider::new();
    dark.load_from_data(CSS_DARK);
    let manager = adw::StyleManager::default();
    let apply = move |manager: &adw::StyleManager| {
        if manager.is_dark() {
            gtk::style_context_add_provider_for_display(&display, &dark, gtk::STYLE_PROVIDER_PRIORITY_APPLICATION + 1);
        } else {
            gtk::style_context_remove_provider_for_display(&display, &dark);
        }
    };
    apply(&manager);
    manager.connect_dark_notify(apply);
}

/// Builds the window for `app` (real or demo) and shows it unless `hidden` (started with the computer
/// and a tray is there to bring it back).
pub fn install(app: &Rc<App>, application: &adw::Application, autostart: bool) {
    let ui = Ui::new(app, application);
    app.ui.replace(Some(ui.clone()));
    app.shell.replace(Some(Rc::new(GtkShell { window: ui.window.clone(), toasts: ui.toasts.clone() })));
    ui.install_actions(app);
    if let Some(runtime) = app.engine.runtime() {
        let tray = crate::tray::Tray::start(app, &runtime);
        ui.tray.replace(tray);
    }
    app.render_now();
    let has_tray = ui.tray.borrow().is_some();
    if !(autostart && has_tray) {
        ui.window.present();
    }
    app.watch_updates();
}

impl Ui {
    pub fn new(app: &Rc<App>, application: &adw::Application) -> Rc<Self> {
        load_css();
        if let Some(display) = gdk::Display::default() {
            let theme = gtk::IconTheme::for_display(&display);
            theme.add_resource_path("/ch/mixel/remote/icons");
        }
        let window = adw::ApplicationWindow::builder().application(application).title("Mixel Remote").default_width(1120).default_height(740).icon_name(crate::APP_ID).build();
        window.set_size_request(360, 480);
        let toasts = adw::ToastOverlay::new();
        window.set_content(Some(&toasts));
        let ui = Rc::new(Self {
            window,
            application: application.clone(),
            toasts,
            parts: RefCell::new(None),
            consent: consent::Consent::new(app),
            tray: RefCell::new(None),
            quitting: Cell::new(false),
            fullscreen_session: Cell::new(false),
            session_keys: Cell::new(false),
        });
        ui.rebuild(app);
        let weak = Rc::downgrade(&ui);
        let close_app = app.clone();
        ui.window.connect_close_request(move |window| {
            let Some(ui) = weak.upgrade() else { return glib::Propagation::Proceed };
            if ui.quitting.get() {
                return glib::Propagation::Proceed;
            }
            // With a tray icon the app keeps running (this computer stays reachable); without one,
            // closing the window quits.
            if ui.tray.borrow().is_some() {
                window.set_visible(false);
            } else {
                ui.quit(&close_app);
            }
            glib::Propagation::Stop
        });
        ui
    }

    /// Builds every screen again (after a language change, and at the start).
    pub fn rebuild(&self, app: &Rc<App>) {
        let section = self.parts.borrow().as_ref().map(|parts| parts.section.get()).unwrap_or_else(|| app.state.borrow().section);
        let parts = Parts::new(app);
        parts.section.set(section);
        parts.select(section);
        // Every screen is replaced (a new language): the window lets go of what it held in the old ones.
        widgets::release(&self.toasts);
        self.toasts.set_child(Some(&parts.root));
        self.parts.replace(Some(parts));
        self.consent.rebuild(app);
        if let Some(texture) = app.texture.borrow().as_ref() {
            self.set_frame(Some(texture));
        }
    }

    pub fn present(&self) {
        self.window.present();
    }

    pub fn render(&self, app: &Rc<App>, state: &State) {
        if let Some(parts) = self.parts.borrow().as_ref() {
            parts.render(app, state, self);
        }
        self.consent.render(app, state, &self.window);
        if let Some(tray) = self.tray.borrow().as_ref() {
            tray.update(state);
        }
        if let Some(peer) = state.session.note_for {
            app.state.borrow_mut().session.note_for = None;
            let a = app.clone();
            dialogs::form(
                &self.window,
                &t("Session note"),
                &tf("Optional: what was done on %@. It appears in the session report.", &[&crate::format::remote_id(Some(peer))]),
                &[(&t("Note"), "")],
                &t("Save note"),
                false,
                Rc::new(move |values| {
                    let a = a.clone();
                    Box::pin(async move { if values[0].trim().is_empty() { Ok(()) } else { a.session_note(None, Some(peer), values[0].clone()).await } })
                }),
            );
        }
        let viewing = state.remote.session_window();
        if viewing != self.session_keys.get() {
            self.session_keys.set(viewing);
            self.session_shortcuts(viewing);
        }
        if !viewing && self.fullscreen_session.replace(false) {
            self.window.unfullscreen();
        }
    }

    pub fn set_frame(&self, texture: Option<&gdk::Texture>) {
        if let Some(parts) = self.parts.borrow().as_ref() {
            parts.session.set_frame(texture);
        }
    }

    pub fn toggle_fullscreen(&self) {
        let on = !self.window.is_fullscreen();
        self.fullscreen_session.set(on);
        if on { self.window.fullscreen() } else { self.window.unfullscreen() }
    }

    /// Quit: ends sessions and goes offline first (at most 15 s), then closes.
    pub fn quit(&self, app: &Rc<App>) {
        if self.quitting.replace(true) {
            return;
        }
        let app = app.clone();
        let application = self.application.clone();
        let window = self.window.clone();
        glib::spawn_future_local(async move {
            app.shutdown().await;
            window.close();
            application.quit();
        });
    }

    /// Closes the window only (screenshots).
    pub fn quit_now_window(&self) {
        self.quitting.set(true);
        self.window.destroy();
    }

    /// While a remote screen is controlled, the app's own shortcuts (Ctrl+R, Ctrl+1 …) go to the
    /// remote computer; only Ctrl+Q, Ctrl+. (end) and F11 stay here.
    fn session_shortcuts(&self, viewing: bool) {
        let application = &self.application;
        let mut local: Vec<(String, Vec<String>)> = vec![
            ("app.go-online".into(), vec!["<Control><Shift>o".into()]),
            ("app.go-offline".into(), vec!["<Control><Alt>o".into()]),
            ("app.copy-id".into(), vec!["<Control><Shift>c".into()]),
            ("app.refresh".into(), vec!["<Control>r".into(), "F5".into()]),
            ("app.preferences".into(), vec!["<Control>comma".into()]),
            ("app.help".into(), vec!["F1".into()]),
            ("app.shortcuts".into(), vec!["<Control>question".into()]),
        ];
        for (index, section) in Section::ALL.iter().enumerate() {
            local.push((format!("app.section-{}", section.id()), vec![format!("<Control>{}", index + 1)]));
        }
        for (action, accels) in local {
            let accels: Vec<&str> = if viewing { Vec::new() } else { accels.iter().map(String::as_str).collect() };
            application.set_accels_for_action(&action, &accels);
        }
    }

    /// Quit at once (the updater starts the new version).
    pub fn quit_now(&self) {
        self.quitting.set(true);
        self.window.close();
        self.application.quit();
    }

    fn install_actions(self: &Rc<Self>, app: &Rc<App>) {
        let application = &self.application;
        let add = |name: &str, accels: &[&str], action: Rc<dyn Fn()>| {
            let entry = gio::SimpleAction::new(name, None);
            entry.connect_activate(move |_, _| action());
            application.add_action(&entry);
            if !accels.is_empty() {
                application.set_accels_for_action(&format!("app.{name}"), accels);
            }
        };
        let (a, u) = (app.clone(), Rc::downgrade(self));
        add("quit", &["<Control>q"], Rc::new(move || if let Some(ui) = u.upgrade() { ui.quit(&a) }));
        let a = app.clone();
        add("go-online", &["<Control><Shift>o"], Rc::new(move || a.go_online()));
        let a = app.clone();
        add("go-offline", &["<Control><Alt>o"], Rc::new(move || a.go_offline()));
        let a = app.clone();
        add("copy-id", &["<Control><Shift>c"], Rc::new(move || a.copy_id()));
        let a = app.clone();
        add("end-session", &["<Control>period"], Rc::new(move || a.end_session()));
        let u = Rc::downgrade(self);
        add("fullscreen", &["F11"], Rc::new(move || if let Some(ui) = u.upgrade() { ui.toggle_fullscreen() }));
        let a = app.clone();
        add("refresh", &["<Control>r", "F5"], Rc::new(move || a.reload_devices()));
        for (index, section) in Section::ALL.iter().enumerate() {
            let (a, section) = (app.clone(), *section);
            let accel = format!("<Control>{}", index + 1);
            add(&format!("section-{}", section.id()), &[accel.as_str()], Rc::new(move || a.edit(|state| state.section = section)));
        }
        let a = app.clone();
        add("preferences", &["<Control>comma"], Rc::new(move || a.edit(|state| state.section = Section::Settings)));
        let a = app.clone();
        add("help", &["F1"], Rc::new(move || a.open_uri("https://remote.mixel.ch")));
        let u = Rc::downgrade(self);
        add("shortcuts", &["<Control>question"], Rc::new(move || if let Some(ui) = u.upgrade() { dialogs::shortcuts(&ui.window) }));
        let (a, u) = (app.clone(), Rc::downgrade(self));
        add("about", &[], Rc::new(move || if let Some(ui) = u.upgrade() { dialogs::about(&ui.window, &a) }));
        let u = Rc::downgrade(self);
        add("show", &[], Rc::new(move || if let Some(ui) = u.upgrade() { ui.present() }));
    }
}

impl Parts {
    fn new(app: &Rc<App>) -> Self {
        // Sidebar: brand, sections, this computer's connection.
        let sidebar_box = vbox(0);
        sidebar_box.add_css_class("mixel-sidebar");
        sidebar_box.set_size_request(232, -1);
        // Its natural width, not half of the window (stops expansion from inside the sidebar).
        sidebar_box.set_hexpand(false);
        let sidebar_header = adw::HeaderBar::new();
        sidebar_header.set_show_end_title_buttons(false);
        let brand = hbox(8);
        brand.append(&mark(26));
        let name = gtk::Label::new(None);
        name.set_markup("<b>Mixel</b> Remote");
        name.add_css_class("title-4");
        brand.append(&name);
        brand.update_property(&[gtk::accessible::Property::Label("Mixel Remote")]);
        sidebar_header.set_title_widget(Some(&brand));
        let menu = gio::Menu::new();
        menu.append(Some(&t("Keyboard shortcuts")), Some("app.shortcuts"));
        menu.append(Some(&t("Mixel Remote Help")), Some("app.help"));
        menu.append(Some(&t("About Mixel Remote")), Some("app.about"));
        menu.append(Some(&t("Quit Mixel Remote")), Some("app.quit"));
        let menu_button = gtk::MenuButton::builder().icon_name("open-menu-symbolic").menu_model(&menu).tooltip_text(t("Main menu")).build();
        sidebar_header.pack_end(&menu_button);
        sidebar_box.append(&sidebar_header);
        let sidebar = gtk::ListBox::new();
        sidebar.add_css_class("navigation-sidebar");
        sidebar.set_vexpand(true);
        for section in Section::ALL {
            let row = hbox(12);
            row.set_margin_top(8);
            row.set_margin_bottom(8);
            row.set_margin_start(6);
            row.append(&gtk::Image::from_icon_name(section.icon()));
            row.append(&gtk::Label::new(Some(&section.title())));
            let item = gtk::ListBoxRow::new();
            item.set_child(Some(&row));
            item.update_property(&[gtk::accessible::Property::Label(&section.title())]);
            sidebar.append(&item);
        }
        sidebar_box.append(&sidebar);
        sidebar_box.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
        let footer = vbox(4);
        footer.set_margin_top(12);
        footer.set_margin_bottom(14);
        footer.set_margin_start(14);
        footer.set_margin_end(14);
        sidebar_box.append(&footer);

        // Content: header, update banner, pages.
        let content = vbox(0);
        content.set_hexpand(true);
        let header = adw::HeaderBar::new();
        let title = adw::WindowTitle::new("", "");
        header.set_title_widget(Some(&title));
        let back = gtk::Button::from_icon_name("go-previous-symbolic");
        back.set_tooltip_text(Some(&t("Sections")));
        back.update_property(&[gtk::accessible::Property::Label(&t("Sections"))]);
        header.pack_start(&back);
        content.append(&header);
        let banner = hbox(10);
        banner.add_css_class("mixel-banner");
        banner.set_visible(false);
        content.append(&banner);
        let pages = gtk::Stack::new();
        pages.set_transition_type(gtk::StackTransitionType::Crossfade);
        pages.set_vexpand(true);
        let remote = remote::RemotePage::new(app);
        let devices = devices::DevicesPage::new(app);
        let account = account::AccountPage::new(app);
        let settings = settings::SettingsPage::new(app);
        let reports = reports::ReportsPage::new(app);
        pages.add_named(&remote.widget, Some("remote"));
        pages.add_named(&devices.widget, Some("devices"));
        pages.add_named(&account.widget, Some("account"));
        pages.add_named(&reports.widget, Some("reports"));
        pages.add_named(&settings.widget, Some("settings"));
        content.append(&pages);

        // The sidebar keeps its own width (a scrolled window reports its minimum width as natural).
        let sidebar_pane = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).vscrollbar_policy(gtk::PolicyType::Automatic).min_content_width(240).propagate_natural_width(false).child(&sidebar_box).build();
        let leaflet = adw::Leaflet::new();
        leaflet.set_can_navigate_back(true);
        leaflet.append(&sidebar_pane);
        let separator = gtk::Separator::new(gtk::Orientation::Vertical);
        leaflet.append(&separator).set_navigatable(false);
        leaflet.append(&content);
        leaflet.set_visible_child(&content);
        let fold_back = back.clone();
        leaflet.connect_folded_notify(move |leaflet| fold_back.set_visible(leaflet.is_folded()));
        back.set_visible(leaflet.is_folded());
        let to_sidebar = (leaflet.clone(), sidebar_pane.clone());
        back.connect_clicked(move |_| to_sidebar.0.set_visible_child(&to_sidebar.1));

        let session = session::SessionView::new(app);
        let root = gtk::Stack::new();
        root.set_transition_type(gtk::StackTransitionType::Crossfade);
        root.add_named(&leaflet, Some("main"));
        root.add_named(&session.widget, Some("session"));

        let select_app = app.clone();
        let select_leaflet = leaflet.clone();
        let select_content = content.clone();
        sidebar.connect_row_activated(move |_, row| {
            let index = row.index().max(0) as usize;
            if let Some(section) = Section::ALL.get(index) {
                let section = *section;
                select_app.edit(|state| state.section = section);
                select_leaflet.set_visible_child(&select_content);
            }
        });
        Self {
            root,
            leaflet,
            sidebar,
            footer,
            footer_key: RefCell::new(String::new()),
            title,
            back,
            pages,
            banner,
            banner_key: RefCell::new(String::new()),
            remote,
            devices,
            account,
            settings,
            reports,
            session,
            section: Cell::new(Section::Remote),
        }
    }

    fn select(&self, section: Section) {
        self.pages.set_visible_child_name(section.id());
        if let Some(row) = self.sidebar.row_at_index(Section::ALL.iter().position(|item| *item == section).unwrap_or(0) as i32) {
            self.sidebar.select_row(Some(&row));
        }
        self.title.set_title(&section.title());
    }

    fn render(&self, app: &Rc<App>, state: &State, ui: &Ui) {
        if self.section.get() != state.section {
            self.section.set(state.section);
            self.select(state.section);
        }
        let viewing = state.remote.session_window();
        self.root.set_visible_child_name(if viewing { "session" } else { "main" });
        self.title.set_subtitle(&if state.section == Section::Remote { String::new() } else { state.connection_message() });
        self.render_footer(app, state);
        self.render_banner(app, state);
        match state.section {
            Section::Remote => self.remote.render(app, state),
            Section::Devices => self.devices.render(app, state),
            Section::Account => self.account.render(app, state),
            Section::Reports => self.reports.render(app, state),
            Section::Settings => self.settings.render(app, state),
        }
        if viewing {
            self.session.render(app, state, ui);
        }
        let _ = &self.leaflet;
        let _ = &self.back;
    }

    fn render_footer(&self, app: &Rc<App>, state: &State) {
        let (kind, text) = logic::connection_badge(&state.remote);
        let key = format!("{kind:?}{text}{:?}{}", state.remote.host, state.signed_in());
        if *self.footer_key.borrow() == key {
            return;
        }
        self.footer_key.replace(key);
        clear(&self.footer);
        self.footer.append(&badge(kind, &text));
        if state.signed_in()
            && let Some(host) = state.remote.host
        {
            let id = id_text(Some(host));
            id.add_css_class("mixel-id-small");
            self.footer.append(&id);
        } else if !state.signed_in() {
            let a = app.clone();
            self.footer.append(&link(&t("Sign in"), move || a.edit(|state| state.section = Section::Account)));
        }
    }

    fn render_banner(&self, app: &Rc<App>, state: &State) {
        let update = state.settings.update.as_ref().filter(|update| !update.version.is_empty());
        let key = format!("{:?}{}", update.map(|update| (&update.version, update.installable)), state.settings.notice);
        if *self.banner_key.borrow() == key {
            return;
        }
        self.banner_key.replace(key);
        clear(&self.banner);
        let Some(update) = update else {
            self.banner.set_visible(false);
            return;
        };
        self.banner.set_visible(true);
        let text = if state.settings.notice.is_empty() { tf("Mixel Remote %@ is ready.", &[&update.version]) } else { state.settings.notice.clone() };
        let line = label(&text);
        line.set_hexpand(true);
        self.banner.append(&line);
        let a = app.clone();
        if update.installable {
            self.banner.append(&primary(&t("Update now"), move || a.install_update()));
        } else {
            self.banner.append(&button(&t("Download"), move || a.open_uri("https://store.mixel.ch/app/mixel-remote")));
        }
    }
}
