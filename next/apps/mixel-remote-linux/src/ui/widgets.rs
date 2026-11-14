//! Mixel's building blocks on libadwaita: pages, cards, status badges, the main (Mixel red) button,
//! labelled fields, the Remote ID field and message lines. Everything is a plain GTK widget, so the
//! system's light/dark style, high contrast, scaling and screen readers apply.
use crate::{format, i18n::t, logic::Badge};
use gtk::{glib, pango, prelude::*};

pub fn vbox(spacing: i32) -> gtk::Box {
    gtk::Box::new(gtk::Orientation::Vertical, spacing)
}

pub fn hbox(spacing: i32) -> gtk::Box {
    gtk::Box::new(gtk::Orientation::Horizontal, spacing)
}

/// Empties a container that is built again.
pub fn clear(container: &gtk::Box) {
    release(container);
    while let Some(child) = container.first_child() {
        container.remove(&child);
    }
}

/// Empties a page below its title (the first item stays).
pub fn clear_below_title(body: &gtk::Box) {
    release(body);
    while let Some(child) = body.first_child().and_then(|title| title.next_sibling()) {
        body.remove(&child);
    }
}

/// To be called before widgets inside `container` are taken out of their window: the window lets
/// go of its default button and of the keyboard focus if they are in there.
///
/// GTK up to 4.8 (Debian 12, Ubuntu 22.04) does neither by itself in time:
/// - It knows the default button (the one Return presses) only by address and does not forget it
///   when the button is removed. The removed button is freed, and the window used it after that:
///   when the next default button was set and whenever a widget in the window was hidden
///   ("gtk_widget_queue_draw: assertion 'GTK_IS_WIDGET (widget)' failed", "g_object_notify:
///   assertion 'G_IS_OBJECT (object)' failed").
/// - A removed widget that had the focus (the button just clicked) is kept alive by the window
///   alone until the next frame, when GTK moves the focus on. If the focus then lands on a
///   selectable label, GTK's label code frees the old widget and looks at it once more
///   ("gtk_widget_is_ancestor: assertion 'GTK_IS_WIDGET (widget)' failed").
///
/// Entries the person types into keep their focus through `keep_focus`.
pub fn release(container: &impl IsA<gtk::Widget>) {
    let Some(window) = container.root().and_then(|root| root.downcast::<gtk::Window>().ok()) else { return };
    if window.default_widget().is_some_and(|button| button.is_ancestor(container)) {
        window.set_default_widget(gtk::Widget::NONE);
    }
    if gtk::prelude::GtkWindowExt::focus(&window).is_some_and(|focused| focused.is_ancestor(container)) {
        gtk::prelude::GtkWindowExt::set_focus(&window, gtk::Widget::NONE);
    }
}

/// The widget GTK names by address in a message ("GtkLabel 0x55d0…"): its kind, text and place.
pub fn widget_at(address: &str) -> Option<String> {
    if !gtk::is_initialized_main_thread() {
        return None;
    }
    let mut stack: Vec<gtk::Widget> = gtk::Window::list_toplevels();
    while let Some(widget) = stack.pop() {
        if format!("{:p}", widget.as_ptr()) == address {
            let text = widget.downcast_ref::<gtk::Label>().map(|label| label.text().to_string()).unwrap_or_default();
            let mut place = Vec::new();
            let mut parent = widget.parent();
            while let Some(item) = parent {
                place.push(format!("{}{}", item.type_().name(), item.css_classes().iter().map(|class| format!(".{class}")).collect::<String>()));
                parent = item.parent();
            }
            place.truncate(8);
            return Some(format!(
                "{}{} {text:?} (lines {:?}, visible {}) in {}",
                widget.type_().name(),
                widget.css_classes().iter().map(|class| format!(".{class}")).collect::<String>(),
                widget.downcast_ref::<gtk::Label>().map(|label| (label.wraps(), label.ellipsize(), label.lines(), label.width_chars(), label.max_width_chars())),
                widget.is_visible(),
                place.join(" < ")
            ));
        }
        let mut child = widget.first_child();
        while let Some(item) = child {
            child = item.next_sibling();
            stack.push(item);
        }
    }
    None
}

/// The address in a toolkit message ("… GtkLabel 0x55d0… adjusted size …"), if it names one.
pub fn address_in(message: &str) -> Option<&str> {
    message.split_whitespace().find(|word| word.starts_with("0x") && word.len() > 4 && word[2..].bytes().all(|byte| byte.is_ascii_hexdigit()))
}

/// Takes a long-lived widget (an entry the person types into) out of its old place.
pub fn adopt(widget: &impl IsA<gtk::Widget>) -> gtk::Widget {
    let widget = widget.clone().upcast::<gtk::Widget>();
    if let Some(parent) = widget.parent() {
        if let Some(parent) = parent.downcast_ref::<gtk::Box>() {
            parent.remove(&widget);
        } else {
            widget.unparent();
        }
    }
    widget
}

pub fn label(text: &str) -> gtk::Label {
    let label = gtk::Label::new(Some(text));
    label.set_wrap(true);
    label.set_wrap_mode(pango::WrapMode::WordChar);
    label.set_xalign(0.0);
    label.set_selectable(false);
    label
}

pub fn dim(text: &str) -> gtk::Label {
    let label = label(text);
    label.add_css_class("dim-label");
    label
}

pub fn caption(text: &str) -> gtk::Label {
    let label = dim(text);
    label.add_css_class("caption");
    label
}

pub fn heading(text: &str) -> gtk::Label {
    let label = gtk::Label::builder().label(text).wrap(true).xalign(0.0).accessible_role(gtk::AccessibleRole::Heading).build();
    label.set_wrap_mode(pango::WrapMode::WordChar);
    label.add_css_class("heading");
    label
}

pub fn selectable(text: &str) -> gtk::Label {
    let label = label(text);
    label.set_selectable(true);
    label
}

pub fn monospace(text: &str) -> gtk::Label {
    let label = selectable(text);
    label.add_css_class("monospace");
    label
}

pub fn button(text: &str, action: impl Fn() + 'static) -> gtk::Button {
    let button = gtk::Button::with_label(text);
    button.connect_clicked(move |_| action());
    button
}

/// The main action of a card: filled Mixel red.
pub fn primary(text: &str, action: impl Fn() + 'static) -> gtk::Button {
    let button = button(text, action);
    button.add_css_class("suggested-action");
    button
}

pub fn destructive(text: &str, action: impl Fn() + 'static) -> gtk::Button {
    let button = button(text, action);
    button.add_css_class("destructive-action");
    button
}

pub fn link(text: &str, action: impl Fn() + 'static) -> gtk::Button {
    let button = button(text, action);
    button.add_css_class("flat");
    button.add_css_class("mixel-link");
    button.set_halign(gtk::Align::Start);
    button
}

/// An icon-only button with a tooltip and the same text for screen readers.
pub fn icon_button(icon: &str, text: &str, action: impl Fn() + 'static) -> gtk::Button {
    let button = gtk::Button::from_icon_name(icon);
    button.set_tooltip_text(Some(text));
    button.update_property(&[gtk::accessible::Property::Label(text)]);
    button.connect_clicked(move |_| action());
    button
}

/// An on/off tool of the session toolbar: icon, tooltip "Name: on", highlighted while on.
pub fn toggle_tool(icon: &str, text: &str, on: bool, action: impl Fn() + 'static) -> gtk::Button {
    let state = if on { t("on") } else { t("off") };
    let tip = format!("{text}: {state}");
    let button = gtk::Button::from_icon_name(icon);
    button.set_tooltip_text(Some(&tip));
    button.update_property(&[gtk::accessible::Property::Label(text), gtk::accessible::Property::Description(&state)]);
    if on {
        button.add_css_class("mixel-on");
    }
    button.connect_clicked(move |_| action());
    button
}

pub fn buttons(items: &[&gtk::Widget]) -> gtk::Box {
    let row = hbox(8);
    for item in items {
        row.append(*item);
    }
    row
}

pub fn spacer() -> gtk::Box {
    let space = hbox(0);
    space.set_hexpand(true);
    space
}

/// A scrollable page with a large title and an optional subtitle; returns the page and its body.
pub fn page(title: &str, subtitle: &str) -> (gtk::ScrolledWindow, gtk::Box) {
    let body = vbox(18);
    body.set_margin_top(24);
    body.set_margin_bottom(24);
    body.set_margin_start(24);
    body.set_margin_end(24);
    let heading = gtk::Label::builder().label(title).xalign(0.0).wrap(true).accessible_role(gtk::AccessibleRole::Heading).build();
    heading.add_css_class("title-1");
    // The title and the line under it are one item of the page (6 px apart; the page's items are
    // 18 px apart). A negative margin on the line did the same, but GTK does not allow one and
    // says so every time the page is measured ("GtkLabel … adjusted size … must not decrease").
    let head = vbox(6);
    head.append(&heading);
    body.append(&head);
    set_subtitle(&body, subtitle);
    let clamp = adw::Clamp::new();
    clamp.set_maximum_size(980);
    clamp.set_tightening_threshold(700);
    clamp.set_child(Some(&body));
    let scroller = gtk::ScrolledWindow::new();
    scroller.set_hscrollbar_policy(gtk::PolicyType::Never);
    scroller.set_vexpand(true);
    scroller.set_child(Some(&clamp));
    (scroller, body)
}

/// The dim line under the title of a [`page`] (none when `text` is empty). The page's first item
/// holds the title and this line.
pub fn set_subtitle(body: &gtk::Box, text: &str) {
    let Some(head) = body.first_child().and_then(|head| head.downcast::<gtk::Box>().ok()) else { return };
    while let Some(line) = head.first_child().and_then(|title| title.next_sibling()) {
        head.remove(&line);
    }
    if !text.is_empty() {
        head.append(&dim(text));
    }
}

/// A rounded panel (libadwaita's card) with a title and optional icon; returns frame and content.
pub fn card(title: &str, icon: Option<&str>) -> (gtk::Box, gtk::Box) {
    let frame = vbox(12);
    frame.add_css_class("card");
    frame.add_css_class("mixel-card");
    let content = vbox(12);
    content.set_margin_top(18);
    content.set_margin_bottom(18);
    content.set_margin_start(18);
    content.set_margin_end(18);
    if !title.is_empty() {
        let header = hbox(10);
        if let Some(icon) = icon {
            let image = gtk::Image::from_icon_name(icon);
            image.add_css_class("mixel-accent");
            header.append(&image);
        }
        header.append(&heading(title));
        content.append(&header);
    }
    frame.append(&content);
    (frame, content)
}

/// A small uppercase label above a field (Mixel's forms).
pub fn field(title: &str, widget: &impl IsA<gtk::Widget>) -> gtk::Box {
    let column = vbox(5);
    let caption = gtk::Label::new(Some(&title.to_uppercase()));
    caption.add_css_class("caption-heading");
    caption.add_css_class("dim-label");
    caption.set_xalign(0.0);
    column.append(&caption);
    column.append(widget);
    if let Some(widget) = widget.dynamic_cast_ref::<gtk::Widget>() {
        widget.update_relation(&[gtk::accessible::Relation::LabelledBy(&[caption.upcast_ref()])]);
    }
    column
}

pub fn entry(placeholder: &str) -> gtk::Entry {
    let entry = gtk::Entry::new();
    entry.set_placeholder_text(Some(placeholder));
    entry.set_hexpand(true);
    entry
}

pub fn password(placeholder: &str) -> gtk::PasswordEntry {
    let entry = gtk::PasswordEntry::new();
    entry.set_show_peek_icon(true);
    entry.set_hexpand(true);
    entry.set_property("placeholder-text", placeholder);
    entry
}

/// The 9-digit ID field: groups digits as they are typed.
pub fn id_entry(accessible: &str) -> gtk::Entry {
    let entry = entry("123 456 789");
    entry.add_css_class("mixel-id-entry");
    entry.set_input_purpose(gtk::InputPurpose::Digits);
    entry.set_max_length(11);
    entry.update_property(&[gtk::accessible::Property::Label(accessible)]);
    entry.connect_changed(|entry| {
        let text = entry.text();
        let grouped = format::group_digits(&text);
        if grouped != text.as_str() {
            let entry = entry.clone();
            glib::idle_add_local_once(move || {
                entry.set_text(&grouped);
                entry.set_position(-1);
            });
        }
    });
    entry
}

/// A Remote ID in large digits.
pub fn id_text(id: Option<u32>) -> gtk::Label {
    let text = format::remote_id(id);
    let label = gtk::Label::new(Some(&text));
    label.add_css_class("mixel-id");
    label.set_selectable(true);
    label.set_xalign(0.0);
    label.update_property(&[gtk::accessible::Property::Label(&format!("{} {}", t("Connection ID"), text))]);
    label
}

/// A coloured dot with a word (online, offline, …).
pub fn badge(kind: Badge, text: &str) -> gtk::Box {
    let row = hbox(6);
    let dot = gtk::Label::new(Some("●"));
    dot.add_css_class(kind.css());
    row.append(&dot);
    let word = gtk::Label::new(Some(text));
    word.add_css_class("dim-label");
    word.set_xalign(0.0);
    row.append(&word);
    row
}

/// An error (Mixel red) or notice line; `None` when empty.
pub fn message(text: &str, error: bool) -> Option<gtk::Box> {
    if text.trim().is_empty() {
        return None;
    }
    let row = gtk::Box::builder().orientation(gtk::Orientation::Horizontal).spacing(8).accessible_role(if error { gtk::AccessibleRole::Alert } else { gtk::AccessibleRole::Group }).build();
    let icon = gtk::Image::from_icon_name(if error { "dialog-warning-symbolic" } else { "dialog-information-symbolic" });
    icon.set_valign(gtk::Align::Start);
    row.append(&icon);
    let line = label(text);
    line.set_selectable(true);
    line.set_hexpand(true);
    row.append(&line);
    row.add_css_class(if error { "mixel-error" } else { "mixel-notice" });
    Some(row)
}

pub fn append_message(container: &gtk::Box, text: &str, error: bool) {
    if let Some(line) = message(text, error) {
        container.append(&line);
    }
}

pub fn spinner_line(text: &str) -> gtk::Box {
    let row = hbox(8);
    let spinner = gtk::Spinner::new();
    spinner.start();
    row.append(&spinner);
    row.append(&dim(text));
    row
}

pub fn check(text: &str, active: bool, action: impl Fn(bool) + 'static) -> gtk::CheckButton {
    let check = gtk::CheckButton::with_label(text);
    check.set_active(active);
    if let Some(child) = check.last_child().and_then(|child| child.downcast::<gtk::Label>().ok()) {
        child.set_wrap(true);
        child.set_xalign(0.0);
    }
    check.connect_toggled(move |check| action(check.is_active()));
    check
}

/// A drop-down of texts; `selected` is an index.
pub fn choice(items: &[String], selected: usize, action: impl Fn(usize) + 'static) -> gtk::DropDown {
    let strings: Vec<&str> = items.iter().map(String::as_str).collect();
    let dropdown = gtk::DropDown::from_strings(&strings);
    dropdown.set_selected(selected.min(items.len().saturating_sub(1)) as u32);
    dropdown.connect_selected_notify(move |dropdown| action(dropdown.selected() as usize));
    dropdown
}

/// A menu button with a list of plain actions (`None` = separator).
/// One entry of [`menu`]: title, enabled, action; `None` is a separator.
pub type MenuItem = Option<(String, bool, Box<dyn Fn()>)>;

pub fn menu(text: &str, items: Vec<MenuItem>) -> gtk::MenuButton {
    let button = gtk::MenuButton::new();
    button.set_label(text);
    let popover = gtk::Popover::new();
    let list = vbox(2);
    list.set_margin_top(6);
    list.set_margin_bottom(6);
    list.set_margin_start(6);
    list.set_margin_end(6);
    for item in items {
        match item {
            None => list.append(&gtk::Separator::new(gtk::Orientation::Horizontal)),
            Some((title, enabled, action)) => {
                let entry = gtk::Button::with_label(&title);
                entry.add_css_class("flat");
                if let Some(label) = entry.child().and_then(|child| child.downcast::<gtk::Label>().ok()) {
                    label.set_xalign(0.0);
                }
                entry.set_sensitive(enabled);
                let popover = popover.clone();
                entry.connect_clicked(move |_| {
                    popover.popdown();
                    action();
                });
                list.append(&entry);
            }
        }
    }
    popover.set_child(Some(&list));
    button.set_popover(Some(&popover));
    button
}

/// The Mixel mark on its tile.
pub fn mark(size: i32) -> gtk::Picture {
    let texture = gtk::gdk::Texture::from_bytes(&glib::Bytes::from_static(include_bytes!("../../data/mixel-mark.png"))).ok();
    let picture = gtk::Picture::new();
    picture.set_paintable(texture.as_ref());
    picture.set_size_request(size, size);
    picture.set_can_shrink(true);
    picture.add_css_class("mixel-mark");
    picture.set_valign(gtk::Align::Center);
    picture.set_halign(gtk::Align::Center);
    picture
}

/// Keeps the keyboard focus (and cursor) in long-lived entries when a screen is built again.
pub fn keep_focus(entries: &[gtk::Widget], rebuild: impl FnOnce()) {
    let focused = entries.iter().find(|entry| entry.has_focus() || entry.first_child().is_some_and(|child| child.has_focus())).cloned();
    let position = focused.as_ref().and_then(|entry| entry.downcast_ref::<gtk::Editable>().map(|editable| editable.position()));
    rebuild();
    if let Some(entry) = focused {
        entry.grab_focus();
        if let (Some(editable), Some(position)) = (entry.downcast_ref::<gtk::Editable>(), position) {
            editable.set_position(position);
        }
    }
}

/// A short dim word that never wraps (row suffixes, values).
pub fn tag(text: &str) -> gtk::Label {
    let label = gtk::Label::new(Some(text));
    label.add_css_class("dim-label");
    label.set_valign(gtk::Align::Center);
    label
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Needs a display: `xvfb-run -a cargo test -p mixel_remote_linux -- --ignored toolkit`.
    /// (The same is checked on every screen of the app by `mixel-remote --screenshots`.)
    #[test]
    #[ignore = "needs a display (xvfb-run)"]
    fn toolkit_stays_silent_when_content_with_the_default_button_is_built_again() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        static MESSAGES: AtomicUsize = AtomicUsize::new(0);
        gtk::init().expect("a display");
        glib::log_set_writer_func(|level, fields| {
            if matches!(level, glib::LogLevel::Critical | glib::LogLevel::Warning) {
                MESSAGES.fetch_add(1, Ordering::Relaxed);
            }
            glib::log_writer_default(level, fields)
        });
        let window = gtk::Window::new();
        let body = vbox(0);
        window.set_child(Some(&body));
        let other = label("another widget of the window");
        // The buttons of earlier rounds are kept alive here, so that this test never makes GTK
        // touch freed memory: what is checked is that the window has forgotten the button.
        let mut earlier: Vec<gtk::Button> = Vec::new();
        for round in 0..3 {
            clear(&body);
            assert!(window.default_widget().is_none(), "round {round}: the window still names the removed button as its default");
            let actions = hbox(0);
            let allow = button("Allow", || {});
            actions.append(&allow);
            body.append(&other);
            body.append(&actions);
            window.set_default_widget(Some(&allow));
            assert!(window.default_widget().is_some_and(|button| button == allow), "round {round}");
            // Hiding any widget makes the window look at its default button too.
            other.set_visible(false);
            other.set_visible(true);
            earlier.push(allow);
        }
        // A default button outside the emptied container stays the default.
        let outside = button("Close", || {});
        let frame = vbox(0);
        window.set_child(Some(&frame));
        frame.append(&body);
        frame.append(&outside);
        window.set_default_widget(Some(&outside));
        clear(&body);
        assert!(window.default_widget().is_some_and(|button| button == outside));
        // The keyboard focus: a focused button that is removed (the one just clicked) is let go
        // of first, so that GTK does not move the focus on from a widget that is no longer there.
        let clicked = button("Sign in", || {});
        body.append(&clicked);
        window.present();
        assert!(clicked.grab_focus());
        assert!(gtk::prelude::GtkWindowExt::focus(&window).is_some_and(|focused| focused == clicked));
        let page = vbox(0);
        frame.append(&page);
        page.append(&label("title"));
        let below = button("Below the title", || {});
        page.append(&below);
        clear(&body);
        assert!(gtk::prelude::GtkWindowExt::focus(&window).is_none(), "the window still holds the removed widget as its focus");
        assert!(below.grab_focus());
        clear_below_title(&page);
        assert!(gtk::prelude::GtkWindowExt::focus(&window).is_none());
        assert!(page.first_child().is_some() && page.first_child().and_then(|title| title.next_sibling()).is_none(), "the title stays");
        // Focus outside the emptied container is kept.
        assert!(outside.grab_focus());
        clear(&body);
        assert!(gtk::prelude::GtkWindowExt::focus(&window).is_some_and(|focused| focused == outside));
        // And a container that is in no window is simply emptied.
        let loose = vbox(0);
        loose.append(&button("x", || {}));
        clear(&loose);
        assert!(loose.first_child().is_none());
        window.destroy();
        assert_eq!(MESSAGES.load(Ordering::Relaxed), 0, "GTK warnings or critical messages");
    }
}
