//! The status icon (StatusNotifierItem, where the desktop shows one: KDE, Xfce, Cinnamon, GNOME with
//! the AppIndicator extension, Ubuntu): the connection and ID, the main actions, and the window back.
//! Without a status icon host the app has none, and closing the window quits.
use crate::{
    app::{App, State},
    format,
    i18n::{t, tf},
};
use std::{cell::RefCell, rc::Rc};

#[derive(Clone, Copy, Debug)]
enum Command {
    Open,
    CopyId,
    Online,
    Offline,
    End,
    Quit,
}

#[derive(Clone, Default, PartialEq)]
struct View {
    status: String,
    session: String,
    signed_in: bool,
    has_id: bool,
    disconnected: bool,
    in_session: bool,
    texts: [String; 7],
}

struct Item {
    view: View,
    send: async_channel::Sender<Command>,
}

impl Item {
    fn command(&self, command: Command) {
        let _ = self.send.try_send(command);
    }
}

impl ksni::Tray for Item {
    fn id(&self) -> String {
        crate::APP_ID.into()
    }
    fn title(&self) -> String {
        "Mixel Remote".into()
    }
    fn icon_name(&self) -> String {
        crate::APP_ID.into()
    }
    fn tool_tip(&self) -> ksni::ToolTip {
        ksni::ToolTip { title: "Mixel Remote".into(), description: self.view.status.clone(), ..Default::default() }
    }
    fn activate(&mut self, _x: i32, _y: i32) {
        self.command(Command::Open);
    }
    fn menu(&self) -> Vec<ksni::MenuItem<Self>> {
        use ksni::menu::StandardItem;
        let view = &self.view;
        let item = |label: &str, enabled: bool, command: Command| -> ksni::MenuItem<Self> {
            StandardItem { label: label.into(), enabled, activate: Box::new(move |tray: &mut Self| tray.command(command)), ..Default::default() }.into()
        };
        let mut items = vec![StandardItem { label: view.status.clone(), enabled: false, ..Default::default() }.into()];
        if view.in_session {
            items.push(StandardItem { label: view.session.clone(), enabled: false, ..Default::default() }.into());
            items.push(item(&view.texts[4], true, Command::End));
        }
        items.push(ksni::MenuItem::Separator);
        items.push(item(&view.texts[0], true, Command::Open));
        if view.signed_in && view.has_id {
            items.push(item(&view.texts[1], true, Command::CopyId));
        }
        if view.signed_in {
            if view.disconnected {
                items.push(item(&view.texts[2], true, Command::Online));
            } else if !view.in_session {
                items.push(item(&view.texts[3], true, Command::Offline));
            }
        } else {
            items.push(item(&view.texts[6], true, Command::Open));
        }
        items.push(ksni::MenuItem::Separator);
        items.push(item(&view.texts[5], true, Command::Quit));
        items
    }
}

pub struct Tray {
    handle: ksni::Handle<Item>,
    runtime: tokio::runtime::Handle,
    shown: RefCell<View>,
}

fn view(state: &State) -> View {
    View {
        status: if state.remote.connected() { tf("Online · ID %@", &[&format::remote_id(state.remote.host)]) } else { t("Offline") },
        session: state.connection_message(),
        signed_in: state.signed_in(),
        has_id: state.remote.host.is_some(),
        disconnected: state.remote.disconnected(),
        in_session: state.remote.in_session(),
        texts: [t("Open Mixel Remote"), t("Copy ID"), t("Make this computer available"), t("Go offline"), t("End session"), t("Quit Mixel Remote"), t("Sign in…")],
    }
}

impl Tray {
    /// The status icon, if the desktop shows status icons.
    pub fn start(app: &Rc<App>, runtime: &tokio::runtime::Handle) -> Option<Self> {
        use ksni::TrayMethods;
        let (send, receive) = async_channel::unbounded();
        let first = view(&app.state.borrow());
        let item = Item { view: first.clone(), send };
        let spawned = runtime.block_on(async { tokio::time::timeout(std::time::Duration::from_secs(3), item.spawn()).await });
        let handle = match spawned {
            Ok(Ok(handle)) => handle,
            _ => return None,
        };
        let a = app.clone();
        gtk::glib::spawn_future_local(async move {
            while let Ok(command) = receive.recv().await {
                match command {
                    Command::Open => {
                        if let Some(ui) = a.ui.borrow().as_ref() {
                            ui.present();
                        }
                    }
                    Command::CopyId => a.copy_id(),
                    Command::Online => a.go_online(),
                    Command::Offline => a.go_offline(),
                    Command::End => a.end_session(),
                    Command::Quit => {
                        let ui = a.ui.borrow().clone();
                        if let Some(ui) = ui {
                            ui.quit(&a);
                        }
                    }
                }
            }
        });
        Some(Self { handle, runtime: runtime.clone(), shown: RefCell::new(first) })
    }

    pub fn update(&self, state: &State) {
        let next = view(state);
        if *self.shown.borrow() == next {
            return;
        }
        self.shown.replace(next.clone());
        let handle = self.handle.clone();
        self.runtime.spawn(async move {
            handle.update(move |item| item.view = next).await;
        });
    }
}
