//! `--screenshots <folder> [en,de,…]`: every screen of the real app (the same widgets, the same
//! state and actions) from demo data, in light and dark, rendered by GTK's own renderer into PNG
//! files. Needs a display (Xvfb is enough); no account, no network.
use crate::{
    app::{App, Tool},
    i18n,
    logic::Section,
    mock::{self, MockEngine},
    store::{Settings, Store},
};
use adw::prelude::*;
use gtk::{gio, glib, graphene};
use serde_json::json;
use std::{cell::Cell, path::PathBuf, rc::Rc, time::Duration};

const SCENES: &[&str] = &[
    "signed-out",
    "sign-in-browser",
    "second-step",
    "remote-control",
    "set-up-this-computer",
    "phone-help",
    "unattended",
    "devices",
    "deploy-link",
    "account-company",
    "create-company",
    "settings",
    "consent",
    "hosted-session",
    "session",
    "session-chat",
    "session-files",
    "session-clipboard",
    "session-draw",
    "reports",
    "invite-colleague",
    "wayland",
];

fn engine_for(scene: &str) -> MockEngine {
    let engine = MockEngine::new();
    match scene {
        "signed-out" => {}
        "sign-in-browser" => engine.set("account_status", json!({ "phase": "waiting_browser", "message": "Finish signing in in your browser", "factors": [] })),
        "second-step" => engine.set("account_status", json!({ "phase": "mfa_required", "message": "", "factors": [{ "id": "f0000000-0000-4000-8000-000000000001", "factor_type": "totp", "status": "verified", "friendly_name": "Mixel ID" }] })),
        "phone-help" => mock::signed_in(&engine, true),
        "create-company" => {
            mock::signed_in(&engine, false);
            engine.fail("saas_my_tenants", "No company");
            engine.set("account_status", json!({ "phase": "authenticated", "message": "", "factors": [], "account": { "remembered": false, "has_company": false, "has_personal": false, "profile": "p", "staff": false, "personal": false, "display_name": "Lukas Brunner", "companies": [] } }));
        }
        "unattended" => {
            mock::signed_in(&engine, false);
            engine.set("capture_targets", json!([{ "id": "x11:0:1", "name": "X11 screen 0 · 1920 × 1080", "full_display": true }]));
        }
        "consent" => {
            mock::signed_in(&engine, false);
            mock::incoming(&engine);
        }
        "hosted-session" => {
            mock::signed_in(&engine, false);
            mock::hosting(&engine);
        }
        "session" | "session-chat" | "session-files" | "session-clipboard" | "session-draw" | "invite-colleague" => {
            mock::signed_in(&engine, false);
            mock::viewing(&engine);
        }
        "wayland" => {
            mock::signed_in(&engine, false);
            engine.set("linux_session", json!({ "wayland": true, "x11": false }));
            engine.set("signin_screen_status", json!({ "supported": true, "outdated": false, "technician": false, "connection_id": 615_882_034u32, "name": "debian-office", "wayland_greeter": true }));
        }
        _ => mock::signed_in(&engine, false),
    }
    engine
}

fn section_for(scene: &str) -> Section {
    match scene {
        "sign-in-browser" | "second-step" | "account-company" | "create-company" => Section::Account,
        "devices" | "deploy-link" => Section::Devices,
        "settings" | "wayland" => Section::Settings,
        "reports" => Section::Reports,
        _ => Section::Remote,
    }
}

/// Renders a widget (a whole window) with its own renderer into a PNG file.
pub fn save(widget: &impl IsA<gtk::Widget>, path: &std::path::Path) -> bool {
    let (width, height) = (widget.width(), widget.height());
    if width <= 0 || height <= 0 {
        eprintln!("screenshot: the window has no size yet");
        return false;
    }
    let paintable = gtk::WidgetPaintable::new(Some(widget));
    let snapshot = gtk::Snapshot::new();
    paintable.snapshot(snapshot.upcast_ref::<gtk::gdk::Snapshot>(), f64::from(width), f64::from(height));
    let Some(node) = snapshot.to_node() else {
        eprintln!("screenshot: nothing was drawn");
        return false;
    };
    let Some(native) = widget.native() else { return false };
    let Some(renderer) = native.renderer() else {
        eprintln!("screenshot: no renderer");
        return false;
    };
    let texture = renderer.render_texture(&node, Some(&graphene::Rect::new(0.0, 0.0, width as f32, height as f32)));
    match texture.save_to_png(path) {
        Ok(()) => true,
        Err(error) => {
            eprintln!("screenshot: {error}");
            false
        }
    }
}

async fn settle(milliseconds: u64) {
    glib::timeout_future(Duration::from_millis(milliseconds)).await;
}

async fn scene(application: &adw::Application, folder: &std::path::Path, name: &str, dark: bool, language: &str) -> bool {
    i18n::set_choice(language);
    adw::StyleManager::default().set_color_scheme(if dark { adw::ColorScheme::ForceDark } else { adw::ColorScheme::ForceLight });
    let engine = Rc::new(engine_for(name));
    let app = App::new(engine.clone(), Store::memory(Settings { language: language.into(), ..Settings::default() }));
    crate::ui::install(&app, application, false);
    // The same reads the running app does after a start, once.
    if let Ok(linux) = app.run("linux_session", json!({})).await {
        app.edit(|state| state.linux = linux);
    }
    app.refresh_account().await;
    settle(50).await;
    app.load_company().await;
    app.load_devices(0).await;
    app.load_settings().await;
    app.refresh_remote().await;
    let section = section_for(name);
    app.edit(|state| state.section = section);
    match name {
        "sign-in-browser" => app.edit(|state| state.sign_in_link = Some("https://auth.mixel.ch/application/o/authorize/?client_id=mixel-remote&code_challenge=…".into())),
        "set-up-this-computer" => app.edit(|state| state.enrollment.needs_setup = true),
        "unattended" => {
            app.load_unattended_sources();
            settle(100).await;
            app.host_edit(|host| host.unattended_control = true);
        }
        "remote-control" => app.edit(|state| state.remote.support_code = Some(crate::models::SupportCode::Text("482915".into()))),
        "session-chat" => app.set_tool(Some(Tool::Chat)),
        "session-draw" => app.edit(|state| {
            state.session.drawing = true;
            state.session.draw_tool = crate::logic::DrawTool::Arrow;
        }),
        "session-files" => app.set_tool(Some(Tool::Files)),
        "session-clipboard" => {
            app.set_tool(Some(Tool::Clipboard));
            app.edit(|state| state.remote.print_jobs = vec!["/run/user/1000/mixel-remote/invoice-2026-117.pdf".into()]);
        }
        "session" => app.edit(|state| {
            state.session.privacy = true;
            state.session.notice = String::new();
        }),
        "settings" | "wayland" => app.edit(|state| {
            state.settings.update_checked = true;
        }),
        _ => {}
    }
    // Pictures arrive, tools are read, presence is checked.
    settle(900).await;
    app.render_now();
    settle(300).await;
    let window = app.ui.borrow().as_ref().map(|ui| ui.window.clone()).expect("window");
    let suffix = if dark { "dark" } else { "light" };
    let prefix = if language == "en" { String::new() } else { format!("{language}-") };
    let mut ok = true;
    // Dialogs of the scene are their own windows: open them, then save both.
    let dialog: Option<gtk::Window> = match name {
        "deploy-link" => {
            app.edit(|state| state.devices.deploy_link = None);
            if let Some(button) = find_button(&window, &i18n::t("Add other computers")) {
                button.emit_clicked();
            }
            settle(300).await;
            top_dialog(application, &window)
        }
        "invite-colleague" => {
            if let Some(button) = find_button(&window, &i18n::t("Invite a colleague")) {
                button.emit_clicked();
            }
            settle(300).await;
            top_dialog(application, &window)
        }
        "consent" => top_dialog(application, &window),
        _ => None,
    };
    let file = folder.join(format!("{prefix}{name}-{suffix}.png"));
    let mut saved = save(&window, &file);
    for _ in 0..5 {
        if saved {
            break;
        }
        settle(400).await;
        saved = save(&window, &file);
    }
    ok &= saved;
    if let Some(dialog) = dialog {
        settle(200).await;
        ok &= save(&dialog, &folder.join(format!("{prefix}{name}-dialog-{suffix}.png")));
        dialog.destroy();
    }
    if let Some(ui) = app.ui.borrow().as_ref() {
        ui.quit_now_window();
    }
    app.clear_video();
    let _ = engine.called("remote_status");
    ok
}

fn find_button(root: &impl IsA<gtk::Widget>, label: &str) -> Option<gtk::Button> {
    let mut stack = vec![root.clone().upcast::<gtk::Widget>()];
    while let Some(widget) = stack.pop() {
        if let Some(button) = widget.downcast_ref::<gtk::Button>()
            && (button.label().as_deref() == Some(label) || button.tooltip_text().as_deref() == Some(label))
            && button.is_mapped()
        {
            return Some(button.clone());
        }
        let mut child = widget.first_child();
        while let Some(item) = child {
            child = item.next_sibling();
            stack.push(item);
        }
    }
    None
}

fn top_dialog(application: &adw::Application, main: &adw::ApplicationWindow) -> Option<gtk::Window> {
    let _ = application;
    gtk::Window::list_toplevels()
        .into_iter()
        .filter_map(|widget| widget.downcast::<gtk::Window>().ok())
        .find(|window| window.is_visible() && window.transient_for().is_some_and(|parent| parent == *main.upcast_ref::<gtk::Window>()))
}

pub fn run(folder: PathBuf, languages: &str) -> i32 {
    let _ = std::fs::create_dir_all(&folder);
    let application = adw::Application::builder().application_id(format!("{}.Screenshots", crate::APP_ID)).flags(gio::ApplicationFlags::NON_UNIQUE).build();
    let failures = Rc::new(Cell::new(0));
    let languages: Vec<String> = languages.split(',').map(|item| item.trim().to_owned()).filter(|item| !item.is_empty()).collect();
    let count = failures.clone();
    application.connect_activate(move |application| {
        let hold = application.hold();
        let (application, folder, languages, count) = (application.clone(), folder.clone(), languages.clone(), count.clone());
        glib::spawn_future_local(async move {
            for language in &languages {
                // Other languages: the main screens; English: all of them.
                let scenes: Vec<&str> = if language == "en" { SCENES.to_vec() } else { vec!["remote-control", "devices", "session", "consent", "settings"] };
                for name in scenes {
                    for dark in [false, true] {
                        if !scene(&application, &folder, name, dark, language).await {
                            eprintln!("FAIL screenshot {language} {name} dark={dark}");
                            count.set(count.get() + 1);
                        } else {
                            println!("saved {language} {name} {}", if dark { "dark" } else { "light" });
                        }
                    }
                }
            }
            drop(hold);
            application.quit();
        });
    });
    let empty: [&str; 0] = [];
    application.run_with_args(&empty);
    i32::from(failures.get() > 0)
}
