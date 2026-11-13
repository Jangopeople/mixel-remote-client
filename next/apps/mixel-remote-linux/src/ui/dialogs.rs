//! Dialogs: forms with one or two fields (rename, invite, delete with a typed name), confirmations,
//! file panels (the desktop portal where there is one), keyboard shortcuts and About.
use super::widgets::*;
use crate::{app::App, i18n::t};
use adw::prelude::*;
use gtk::glib;
use std::{cell::RefCell, future::Future, pin::Pin, rc::Rc};

pub type Submit = Rc<dyn Fn(Vec<String>) -> Pin<Box<dyn Future<Output = Result<(), String>>>>>;

/// A modal dialog window with a title, content and buttons at the bottom.
pub fn window(parent: &impl IsA<gtk::Window>, title: &str, width: i32) -> (adw::Window, gtk::Box, gtk::Box) {
    let dialog = adw::Window::builder().modal(true).transient_for(parent).title(title).default_width(width).destroy_with_parent(true).build();
    let outer = vbox(0);
    let header = adw::HeaderBar::new();
    header.set_show_end_title_buttons(true);
    header.add_css_class("flat");
    outer.append(&header);
    let body = vbox(14);
    body.set_margin_top(6);
    body.set_margin_bottom(22);
    body.set_margin_start(22);
    body.set_margin_end(22);
    let heading = gtk::Label::builder().label(title).xalign(0.0).wrap(true).accessible_role(gtk::AccessibleRole::Heading).build();
    heading.add_css_class("title-2");
    body.append(&heading);
    let content = vbox(12);
    body.append(&content);
    let actions = hbox(8);
    actions.set_halign(gtk::Align::End);
    actions.set_margin_top(6);
    body.append(&actions);
    outer.append(&body);
    dialog.set_content(Some(&outer));
    let escape = gtk::EventControllerKey::new();
    let closing = dialog.clone();
    escape.connect_key_pressed(move |_, key, _, _| {
        if key == gtk::gdk::Key::Escape {
            closing.close();
            return glib::Propagation::Stop;
        }
        glib::Propagation::Proceed
    });
    dialog.add_controller(escape);
    (dialog, content, actions)
}

/// A form: optional text, labelled fields, Cancel and the confirm button. `submit` gets the values;
/// its error stays in the dialog, success closes it.
#[allow(clippy::too_many_arguments)]
pub fn form(parent: &impl IsA<gtk::Window>, title: &str, text: &str, fields: &[(&str, &str)], confirm: &str, danger: bool, submit: Submit) {
    let (dialog, content, actions) = window(parent, title, 440);
    if !text.is_empty() {
        content.append(&dim(text));
    }
    let entries: Vec<gtk::Entry> = fields
        .iter()
        .map(|(name, value)| {
            let entry = entry(name);
            entry.set_text(value);
            content.append(&field(name, &entry));
            entry
        })
        .collect();
    let error = vbox(0);
    content.append(&error);
    let close = dialog.clone();
    actions.append(&button(&t("Cancel"), move || close.close()));
    let go = {
        let (dialog, error, entries) = (dialog.clone(), error.clone(), entries.clone());
        Rc::new(move || {
            let values: Vec<String> = entries.iter().map(|entry| entry.text().to_string()).collect();
            let future = submit(values);
            let (dialog, error) = (dialog.clone(), error.clone());
            dialog.set_sensitive(false);
            glib::spawn_future_local(async move {
                let result = future.await;
                dialog.set_sensitive(true);
                match result {
                    Ok(()) => dialog.close(),
                    Err(text) => {
                        clear(&error);
                        append_message(&error, &text, true);
                    }
                }
            });
        })
    };
    let confirm_button = {
        let go = go.clone();
        if danger { destructive(confirm, move || go()) } else { primary(confirm, move || go()) }
    };
    actions.append(&confirm_button);
    for entry in &entries {
        let go = go.clone();
        entry.connect_activate(move |_| go());
    }
    dialog.set_default_widget(Some(&confirm_button));
    dialog.present();
    if let Some(first) = entries.first() {
        first.grab_focus();
    }
}

/// Asks before something that cannot be undone; `action` runs on confirm.
pub fn confirm(parent: &impl IsA<gtk::Window>, title: &str, text: &str, confirm: &str, action: impl Fn() + 'static) {
    let (dialog, content, actions) = window(parent, title, 420);
    content.append(&dim(text));
    let close = dialog.clone();
    let cancel = button(&t("Cancel"), move || close.close());
    actions.append(&cancel);
    let close = dialog.clone();
    actions.append(&destructive(confirm, move || {
        close.close();
        action();
    }));
    dialog.present();
    cancel.grab_focus();
}

/// A file panel: a file to open, or where to save (with a suggested name). `None`: cancelled.
pub fn choose(parent: &impl IsA<gtk::Window>, action: gtk::FileChooserAction, title: &str, suggested: Option<&str>) -> Pin<Box<dyn Future<Output = Option<String>>>> {
    let (sender, receiver) = async_channel::bounded::<Option<String>>(1);
    let accept = if action == gtk::FileChooserAction::Save { t("Save") } else { t("Open") };
    let chooser = gtk::FileChooserNative::new(Some(title), Some(parent), action, Some(&accept), Some(&t("Cancel")));
    chooser.set_modal(true);
    if let Some(name) = suggested {
        chooser.set_current_name(name);
    }
    let keep: Rc<RefCell<Option<gtk::FileChooserNative>>> = Rc::new(RefCell::new(Some(chooser.clone())));
    chooser.connect_response(move |chooser, response| {
        let path = (response == gtk::ResponseType::Accept).then(|| chooser.file().and_then(|file| file.path())).flatten();
        let _ = sender.try_send(path.and_then(|path| path.to_str().map(str::to_owned)));
        keep.replace(None);
    });
    chooser.show();
    Box::pin(async move { receiver.recv().await.ok().flatten() })
}

const SHORTCUTS: &str = r#"<?xml version="1.0" encoding="UTF-8"?>
<interface>
  <object class="GtkShortcutsWindow" id="shortcuts">
    <property name="modal">1</property>
    <child>
      <object class="GtkShortcutsSection">
        <property name="section-name">main</property>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">@APP@</property>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;1</property><property name="title">@REMOTE@</property></object></child>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;2</property><property name="title">@DEVICES@</property></object></child>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;3</property><property name="title">@ACCOUNT@</property></object></child>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;4 &lt;Control&gt;comma</property><property name="title">@SETTINGS@</property></object></child>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;r</property><property name="title">@REFRESH@</property></object></child>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">F1</property><property name="title">@HELP@</property></object></child>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;q</property><property name="title">@QUIT@</property></object></child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">@CONNECTION@</property>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;&lt;Shift&gt;o</property><property name="title">@ONLINE@</property></object></child>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;&lt;Alt&gt;o</property><property name="title">@OFFLINE@</property></object></child>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;&lt;Shift&gt;c</property><property name="title">@COPY@</property></object></child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">@SESSION@</property>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;period</property><property name="title">@END@</property></object></child>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">F11</property><property name="title">@FULL@</property></object></child>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;&lt;Alt&gt;Delete</property><property name="title">@SAS@</property></object></child>
            <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;&lt;Alt&gt;Escape</property><property name="title">@RELEASE@</property></object></child>
          </object>
        </child>
      </object>
    </child>
  </object>
</interface>"#;

fn xml(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

pub fn shortcuts(parent: &adw::ApplicationWindow) {
    let mut text = SHORTCUTS.to_owned();
    for (key, value) in [
        ("@APP@", "Mixel Remote".to_owned()),
        ("@REMOTE@", t("Remote control")),
        ("@DEVICES@", t("Devices")),
        ("@ACCOUNT@", t("Account")),
        ("@SETTINGS@", t("Settings")),
        ("@REFRESH@", t("Refresh devices")),
        ("@HELP@", t("Mixel Remote Help")),
        ("@QUIT@", t("Quit Mixel Remote")),
        ("@CONNECTION@", t("Connection")),
        ("@ONLINE@", t("Make this computer available")),
        ("@OFFLINE@", t("Go offline")),
        ("@COPY@", t("Copy ID")),
        ("@SESSION@", t("Session")),
        ("@END@", t("End session")),
        ("@FULL@", t("Full screen")),
        ("@SAS@", t("Ctrl+Alt+Del on the remote computer")),
        ("@RELEASE@", t("Keyboard back to this computer")),
    ] {
        text = text.replace(key, &xml(&value));
    }
    let builder = gtk::Builder::from_string(&text);
    if let Some(window) = builder.object::<gtk::ShortcutsWindow>("shortcuts") {
        window.set_transient_for(Some(parent));
        window.present();
    }
}

pub fn about(parent: &adw::ApplicationWindow, app: &Rc<App>) {
    let dialog = gtk::AboutDialog::builder()
        .transient_for(parent)
        .modal(true)
        .program_name("Mixel Remote")
        .version(&app.info.version)
        .comments(t("Remote support and access for your devices."))
        .website("https://remote.mixel.ch")
        .website_label("remote.mixel.ch")
        .copyright("© Mixel IT and Corporate Services")
        .logo_icon_name(crate::APP_ID)
        .build();
    dialog.present();
}
