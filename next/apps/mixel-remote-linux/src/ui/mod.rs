//! The window (Pro Console): a title bar with the session tabs (Devices and one tab per session), the
//! console (sidebar and section pages) in the Devices tab, each session in its own tab or popped out
//! into its own window, floating cards for a connection being prepared and for a session that is ready,
//! and the sign-in screen while nobody is signed in. libadwaita's look in Mixel's colours, light or
//! dark as the system or the Appearance setting says. The consent dialog appears over everything.
mod account;
mod cards;
mod consent;
mod devices;
pub mod dialogs;
mod remote;
mod reports;
mod session;
mod sessions;
mod settings;
mod sidebar;
mod signin;
pub mod widgets;

use crate::{
    app::{App, Shell, State},
    i18n::{t, tf},
    logic::Section,
    workspace::{Effect, Tab},
};
use adw::prelude::*;
use gtk::{gdk, gio, glib};
use std::{
    cell::{Cell, RefCell},
    collections::HashMap,
    future::Future,
    pin::Pin,
    rc::Rc,
};
use widgets::*;

const CSS: &str = include_str!("style.css");
const CSS_DARK: &str = include_str!("style-dark.css");

/// One session's tab: its page in the tab bar, the slot the session view sits in, and the note shown
/// instead while the session is in its own window.
struct SessionTab {
    peer: u32,
    page: adw::TabPage,
    slot: gtk::Box,
    popped: gtk::Box,
}

struct Parts {
    root: gtk::Stack,
    signin: signin::SigninPage,
    chrome: gtk::Box,
    leaflet: adw::Leaflet,
    sidebar: sidebar::Sidebar,
    title: adw::WindowTitle,
    back: gtk::Button,
    tabs: adw::TabView,
    console_page: adw::TabPage,
    session_tabs: Rc<RefCell<Vec<SessionTab>>>,
    forced_close: Rc<Cell<bool>>,
    pages: gtk::Stack,
    banner: gtk::Box,
    banner_key: RefCell<String>,
    floating: gtk::Box,
    floating_key: RefCell<String>,
    computer: remote::RemotePage,
    devices: devices::DevicesPage,
    sessions: sessions::SessionsPage,
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
    popouts: RefCell<HashMap<u32, adw::Window>>,
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
    apply_appearance(&app.store.borrow().settings.appearance);
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
            popouts: RefCell::new(HashMap::new()),
        });
        ui.rebuild(app);
        // Full screen shows the session alone: no title bar, tabs or sidebar.
        let weak = Rc::downgrade(&ui);
        ui.window.connect_fullscreened_notify(move |window| {
            if let Some(ui) = weak.upgrade()
                && let Some(parts) = ui.parts.borrow().as_ref()
            {
                parts.chrome.set_visible(!window.is_fullscreen());
                parts.sidebar_visible(!window.is_fullscreen());
            }
        });
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
        // Session windows hold the old session view: they go back into tabs of the new screens.
        let windows: Vec<adw::Window> = self.popouts.borrow_mut().drain().map(|(_, window)| window).collect();
        if !windows.is_empty() {
            app.state.borrow_mut().workspace.windows.clear();
            for window in windows {
                window.destroy();
            }
        }
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
        // The app's own shortcuts go to the remote computer while its screen is in front.
        let viewing = state.remote.viewing() && (state.workspace.is_in_window(state.remote.peer.unwrap_or(0)) || matches!(state.workspace.selected, Tab::Session(_)));
        if viewing != self.session_keys.get() {
            self.session_keys.set(viewing);
            self.session_shortcuts(viewing);
        }
        if !state.remote.viewing() && self.fullscreen_session.replace(false) {
            self.window.unfullscreen();
        }
    }

    pub fn set_frame(&self, texture: Option<&gdk::Texture>) {
        if let Some(parts) = self.parts.borrow().as_ref() {
            parts.session.set_frame(texture);
        }
    }

    /// Full screen for the session in front: its own window when popped out, else this window.
    pub fn toggle_fullscreen(&self) {
        if let Some(window) = self.popouts.borrow().values().find(|window| window.is_active()).cloned() {
            if window.is_fullscreen() { window.unfullscreen() } else { window.fullscreen() }
            return;
        }
        let on = !self.window.is_fullscreen();
        self.fullscreen_session.set(on);
        if on { self.window.fullscreen() } else { self.window.unfullscreen() }
    }

    /// The Connect-by-ID field of the Devices page (the "+" tab).
    pub fn focus_connect(&self) {
        if let Some(parts) = self.parts.borrow().as_ref() {
            parts.devices.focus_connect();
        }
    }

    pub fn focus_search(&self) {
        if let Some(parts) = self.parts.borrow().as_ref() {
            parts.devices.focus_search();
        }
    }

    /// Carries out what the workspace asked for: open, raise or close a session window.
    pub fn apply(self: &Rc<Self>, app: &Rc<App>, effect: Effect) {
        match effect {
            Effect::OpenWindow(peer) => self.open_popout(app, peer),
            Effect::FocusWindow(peer) => {
                if let Some(window) = self.popouts.borrow().get(&peer) {
                    window.present();
                }
            }
            Effect::CloseWindow(peer) => {
                let window = self.popouts.borrow_mut().remove(&peer);
                if let Some(window) = window {
                    // The session view goes back to its tab (the next render puts it there).
                    if let Some(parts) = self.parts.borrow().as_ref() {
                        adopt(&parts.session.widget);
                    }
                    window.destroy();
                }
            }
        }
    }

    fn open_popout(self: &Rc<Self>, app: &Rc<App>, peer: u32) {
        if let Some(window) = self.popouts.borrow().get(&peer) {
            window.present();
            return;
        }
        let name = app.state.borrow().workspace.tab(peer).map(|tab| tab.name.clone()).unwrap_or_default();
        let window = adw::Window::builder().application(&self.application).title(&name).default_width(1100).default_height(720).build();
        window.set_icon_name(Some(crate::APP_ID));
        let content = vbox(0);
        let header = adw::HeaderBar::new();
        let title = adw::WindowTitle::new(&name, &tf("Session with %@", &[&crate::format::remote_id(Some(peer))]));
        header.set_title_widget(Some(&title));
        let a = app.clone();
        let back = gtk::Button::with_label(&t("Back into a tab"));
        back.set_tooltip_text(Some(&t("Move this session back into a tab of the main window")));
        back.connect_clicked(move |_| a.dock(peer, true));
        header.pack_start(&back);
        content.append(&header);
        if let Some(parts) = self.parts.borrow().as_ref() {
            let widget = adopt(&parts.session.widget);
            widget.set_vexpand(true);
            content.append(&widget);
        }
        window.set_content(Some(&content));
        let (a, weak) = (app.clone(), Rc::downgrade(self));
        window.connect_close_request(move |_| {
            // Closing the window puts the session back into its tab; the session goes on.
            if let Some(ui) = weak.upgrade() {
                ui.popouts.borrow_mut().remove(&peer);
            }
            let a = a.clone();
            glib::idle_add_local_once(move || a.dock(peer, false));
            glib::Propagation::Proceed
        });
        self.popouts.borrow_mut().insert(peer, window.clone());
        window.present();
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
        for (index, section) in Section::NAV.iter().enumerate() {
            local.push((format!("app.section-{}", section.id()), vec![format!("<Control>{}", index + 1)]));
        }
        local.push(("app.search".into(), vec!["<Control>f".into()]));
        local.push(("app.new-tab".into(), vec!["<Control>t".into()]));
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
        for (index, section) in Section::NAV.iter().enumerate() {
            let (a, section) = (app.clone(), *section);
            let accel = format!("<Control>{}", index + 1);
            add(&format!("section-{}", section.id()), &[accel.as_str()], Rc::new(move || a.edit(|state| {
                state.section = section;
                state.workspace.selected = Tab::Console;
            })));
        }
        let (a, u) = (app.clone(), Rc::downgrade(self));
        add("search", &["<Control>f"], Rc::new(move || {
            a.edit(|state| {
                state.section = Section::Devices;
                state.workspace.selected = Tab::Console;
            });
            if let Some(ui) = u.upgrade() {
                ui.focus_search();
            }
        }));
        let (a, u) = (app.clone(), Rc::downgrade(self));
        add("new-tab", &["<Control>t"], Rc::new(move || {
            a.edit(|state| {
                state.section = Section::Devices;
                state.workspace.selected = Tab::Console;
            });
            if let Some(ui) = u.upgrade() {
                ui.focus_connect();
            }
        }));
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

/// The "Invite a colleague" dialog of the session (also used by the screenshots).
pub fn invite_colleague_dialog(app: &Rc<App>) {
    session::invite_dialog(app);
}

/// `system`, `light` or `dark` (empty is `system`).
pub fn apply_appearance(value: &str) {
    adw::StyleManager::default().set_color_scheme(match value {
        "light" => adw::ColorScheme::ForceLight,
        "dark" => adw::ColorScheme::ForceDark,
        _ => adw::ColorScheme::Default,
    });
}

fn os_icon_of(os: &str) -> gio::ThemedIcon {
    gio::ThemedIcon::new(crate::format::os_icon(os))
}

impl Parts {
    fn new(app: &Rc<App>) -> Self {
        // The title bar: back (narrow window), the connection line, the main menu.
        let header = adw::HeaderBar::new();
        let title = adw::WindowTitle::new("Mixel Remote", "");
        header.set_title_widget(Some(&title));
        let back = gtk::Button::from_icon_name("go-previous-symbolic");
        back.set_tooltip_text(Some(&t("Sections")));
        back.update_property(&[gtk::accessible::Property::Label(&t("Sections"))]);
        header.pack_start(&back);
        let menu = gio::Menu::new();
        menu.append(Some(&t("Keyboard shortcuts")), Some("app.shortcuts"));
        menu.append(Some(&t("Mixel Remote Help")), Some("app.help"));
        menu.append(Some(&t("About Mixel Remote")), Some("app.about"));
        menu.append(Some(&t("Quit Mixel Remote")), Some("app.quit"));
        let menu_button = gtk::MenuButton::builder().icon_name("open-menu-symbolic").menu_model(&menu).tooltip_text(t("Main menu")).build();
        menu_button.update_property(&[gtk::accessible::Property::Label(&t("Main menu"))]);
        header.pack_end(&menu_button);

        // The tabs: Devices, one per session, and "+".
        let tabs = adw::TabView::new();
        tabs.set_vexpand(true);
        let tab_bar = adw::TabBar::new();
        tab_bar.set_view(Some(&tabs));
        tab_bar.set_autohide(false);
        tab_bar.set_expand_tabs(false);
        let plus = gtk::Button::from_icon_name("list-add-symbolic");
        plus.add_css_class("flat");
        plus.set_tooltip_text(Some(&t("Connect by ID (Ctrl+T)")));
        plus.update_property(&[gtk::accessible::Property::Label(&t("New connection"))]);
        let a = app.clone();
        plus.connect_clicked(move |_| {
            a.edit(|state| {
                state.section = Section::Devices;
                state.workspace.selected = Tab::Console;
            });
            let ui = a.ui.borrow().clone();
            if let Some(ui) = ui {
                ui.focus_connect();
            }
        });
        tab_bar.set_end_action_widget(Some(&plus));

        // The console: sidebar and the section pages.
        let sidebar = sidebar::Sidebar::new();
        let content = vbox(0);
        content.set_hexpand(true);
        let banner = hbox(10);
        banner.add_css_class("mixel-banner");
        banner.set_visible(false);
        content.append(&banner);
        let pages = gtk::Stack::new();
        pages.set_transition_type(gtk::StackTransitionType::Crossfade);
        pages.set_vexpand(true);
        let computer = remote::RemotePage::new(app);
        let devices = devices::DevicesPage::new(app);
        let sessions = sessions::SessionsPage::new();
        let account = account::AccountPage::new(app);
        let settings = settings::SettingsPage::new(app);
        let reports = reports::ReportsPage::new(app);
        pages.add_named(&devices.widget, Some(Section::Devices.id()));
        pages.add_named(&sessions.widget, Some(Section::Sessions.id()));
        pages.add_named(&reports.widget, Some(Section::Reports.id()));
        pages.add_named(&settings.widget, Some(Section::Settings.id()));
        pages.add_named(&computer.widget, Some(Section::Computer.id()));
        pages.add_named(&account.widget, Some(Section::Account.id()));
        content.append(&pages);
        let sidebar_pane = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).vscrollbar_policy(gtk::PolicyType::Automatic).min_content_width(250).propagate_natural_width(false).child(&sidebar.widget).build();
        let leaflet = adw::Leaflet::new();
        leaflet.set_can_navigate_back(true);
        leaflet.append(&sidebar_pane);
        leaflet.append(&gtk::Separator::new(gtk::Orientation::Vertical)).set_navigatable(false);
        leaflet.append(&content);
        leaflet.set_visible_child(&content);
        let fold_back = back.clone();
        leaflet.connect_folded_notify(move |leaflet| fold_back.set_visible(leaflet.is_folded()));
        back.set_visible(leaflet.is_folded());
        let to_sidebar = (leaflet.clone(), sidebar_pane.clone());
        back.connect_clicked(move |_| to_sidebar.0.set_visible_child(&to_sidebar.1));
        // Choosing a section on a narrow window shows the page.
        let console_page = tabs.append(&leaflet);
        console_page.set_title(&t("Devices"));
        console_page.set_icon(Some(&gio::ThemedIcon::new("computer-symbolic")));
        console_page.set_tooltip(&t("Devices"));

        let session = session::SessionView::new(app);
        let session_tabs: Rc<RefCell<Vec<SessionTab>>> = Rc::new(RefCell::new(Vec::new()));
        let forced_close = Rc::new(Cell::new(false));
        {
            let (a, list) = (app.clone(), session_tabs.clone());
            tabs.connect_selected_page_notify(move |view| {
                if a.rendering.get() {
                    return;
                }
                let Some(page) = view.selected_page() else { return };
                let tab = list.borrow().iter().find(|entry| entry.page == page).map(|entry| Tab::Session(entry.peer)).unwrap_or(Tab::Console);
                a.select_tab(tab);
            });
            let (a, list, forced) = (app.clone(), session_tabs.clone(), forced_close.clone());
            // The close button of a session tab ends the session; the Devices tab cannot be closed.
            // (A tab the app removes itself, because its session ended, closes without asking.)
            tabs.connect_close_page(move |view, page| {
                if forced.get() {
                    view.close_page_finish(page, true);
                    return glib::Propagation::Stop;
                }
                if list.borrow().iter().any(|entry| entry.page == *page) {
                    a.end_session();
                }
                view.close_page_finish(page, false);
                glib::Propagation::Stop
            });
        }

        // Floating cards over the window: connecting, and "session is ready".
        let overlay = gtk::Overlay::new();
        overlay.set_child(Some(&tabs));
        let floating = vbox(10);
        floating.set_halign(gtk::Align::End);
        floating.set_valign(gtk::Align::End);
        floating.set_margin_end(18);
        floating.set_margin_bottom(18);
        floating.set_visible(false);
        overlay.add_overlay(&floating);

        let chrome = vbox(0);
        chrome.append(&header);
        chrome.append(&tab_bar);
        let shell = vbox(0);
        shell.append(&chrome);
        shell.append(&overlay);

        let signin = signin::SigninPage::new();
        let root = gtk::Stack::new();
        root.set_transition_type(gtk::StackTransitionType::Crossfade);
        root.add_named(&shell, Some("shell"));
        let signin_shell = vbox(0);
        let signin_header = adw::HeaderBar::new();
        signin_header.set_title_widget(Some(&adw::WindowTitle::new("Mixel Remote", "")));
        signin_header.add_css_class("flat");
        signin_header.pack_end(&gtk::MenuButton::builder().icon_name("open-menu-symbolic").menu_model(&menu).tooltip_text(t("Main menu")).build());
        signin_shell.append(&signin_header);
        signin.widget.set_vexpand(true);
        signin_shell.append(&signin.widget);
        root.add_named(&signin_shell, Some("signin"));
        Self {
            root,
            signin,
            chrome,
            leaflet,
            sidebar,
            title,
            back,
            tabs,
            console_page,
            session_tabs,
            forced_close,
            pages,
            banner,
            banner_key: RefCell::new(String::new()),
            floating,
            floating_key: RefCell::new(String::new()),
            computer,
            devices,
            sessions,
            account,
            settings,
            reports,
            session,
            section: Cell::new(Section::Devices),
        }
    }

    fn sidebar_visible(&self, visible: bool) {
        if let Some(pane) = self.sidebar.widget.parent().and_then(|viewport| viewport.parent()) {
            pane.set_visible(visible);
        }
    }

    fn select(&self, section: Section) {
        self.pages.set_visible_child_name(section.id());
    }

    fn render(&self, app: &Rc<App>, state: &State, ui: &Ui) {
        if !state.signed_in() {
            self.root.set_visible_child_name("signin");
            self.signin.render(app, state);
            return;
        }
        self.root.set_visible_child_name("shell");
        if self.section.get() != state.section {
            self.section.set(state.section);
        }
        self.select(state.section);
        self.title.set_subtitle(&state.connection_message());
        self.sync_tabs(app, state);
        self.sidebar.render(app, state);
        self.render_banner(app, state);
        self.render_floating(app, state);
        match state.section {
            Section::Devices => self.devices.render(app, state),
            Section::Sessions => self.sessions.render(app, state),
            Section::Reports => self.reports.render(app, state),
            Section::Settings => self.settings.render(app, state),
            Section::Computer => self.computer.render(app, state),
            Section::Account => self.account.render(app, state),
        }
        if state.remote.viewing() || !state.workspace.tabs.is_empty() {
            self.session.render(app, state, ui);
        }
        let _ = (&self.leaflet, &self.back);
    }

    /// Brings the tab bar in line with the workspace: one tab per session, the selected one in front,
    /// the session view in its tab (or a note while it is in its own window).
    fn sync_tabs(&self, app: &Rc<App>, state: &State) {
        let workspace = &state.workspace;
        let mut list = self.session_tabs.borrow_mut();
        // Ended sessions lose their tab.
        let gone: Vec<usize> = list.iter().enumerate().filter(|(_, entry)| !workspace.has_tab(entry.peer)).map(|(index, _)| index).collect();
        for index in gone.into_iter().rev() {
            let entry = list.remove(index);
            if entry.slot.first_child().is_some() {
                adopt(&self.session.widget);
            }
            self.forced_close.set(true);
            self.tabs.close_page(&entry.page);
            self.forced_close.set(false);
        }
        for tab in &workspace.tabs {
            if !list.iter().any(|entry| entry.peer == tab.peer) {
                let holder = vbox(0);
                let slot = vbox(0);
                slot.set_vexpand(true);
                let popped = vbox(10);
                popped.set_valign(gtk::Align::Center);
                popped.set_halign(gtk::Align::Center);
                popped.set_vexpand(true);
                popped.set_visible(false);
                holder.append(&slot);
                holder.append(&popped);
                let page = self.tabs.append(&holder);
                list.push(SessionTab { peer: tab.peer, page, slot, popped });
            }
        }
        for tab in &workspace.tabs {
            let Some(entry) = list.iter().find(|entry| entry.peer == tab.peer) else { continue };
            entry.page.set_title(&tab.name);
            entry.page.set_icon(Some(&os_icon_of(&tab.os)));
            entry.page.set_tooltip(&tf("Session with %@", &[&tab.name]));
            entry.page.set_loading(tab.state == crate::workspace::TabState::Starting);
            let in_window = workspace.is_in_window(tab.peer);
            entry.slot.set_visible(!in_window);
            entry.popped.set_visible(in_window);
            clear(&entry.popped);
            if in_window {
                entry.popped.append(&heading(&tf("%@ is in its own window", &[&tab.name])));
                let (a, peer) = (app.clone(), tab.peer);
                entry.popped.append(&button(&t("Show the window"), move || a.show_session(peer)));
                let (a, peer) = (app.clone(), tab.peer);
                entry.popped.append(&primary(&t("Move back into a tab"), move || a.dock(peer, true)));
            } else if self.session.widget.parent().as_ref() != Some(entry.slot.upcast_ref::<gtk::Widget>()) {
                adopt(&self.session.widget);
                entry.slot.append(&self.session.widget);
            }
        }
        let wanted = match workspace.selected {
            Tab::Session(peer) => list.iter().find(|entry| entry.peer == peer).map(|entry| entry.page.clone()),
            Tab::Console => None,
        }
        .unwrap_or_else(|| self.console_page.clone());
        if self.tabs.selected_page().as_ref() != Some(&wanted) {
            self.tabs.set_selected_page(&wanted);
        }
    }

    fn render_floating(&self, app: &Rc<App>, state: &State) {
        let workspace = &state.workspace;
        let notices: Vec<(u32, String, String)> = workspace.ready_notices.iter().filter_map(|peer| workspace.tab(*peer).map(|tab| (*peer, tab.name.clone(), tab.os.clone()))).collect();
        let key = format!("{:?}{:?}{}", workspace.attempt, notices, crate::i18n::choice());
        if *self.floating_key.borrow() == key {
            return;
        }
        self.floating_key.replace(key);
        clear(&self.floating);
        if let Some(attempt) = &workspace.attempt {
            self.floating.append(&cards::attempt_card(app, state, attempt));
        }
        for (peer, name, os) in notices {
            self.floating.append(&cards::ready_card(app, peer, &name, &os));
        }
        self.floating.set_visible(self.floating.first_child().is_some());
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
