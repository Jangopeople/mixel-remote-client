//! One local modal picker. A dropped IPC waiter does not release the busy
//! permit while AppKit is still showing its panel; revocation cancels that panel.
use crate::remote_files::FileScope;
use native_os_capture::macos_file_picker::FilePicker;
use std::{
    cell::RefCell,
    path::PathBuf,
    sync::Arc,
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};
use uuid::Uuid;
static BUSY: AtomicBool = AtomicBool::new(false);
thread_local! {static PANEL:RefCell<Option<(Uuid,FilePicker)>>=const{RefCell::new(None)};}
struct Permit;
impl Drop for Permit {
    fn drop(&mut self) {
        BUSY.store(false, Ordering::Release);
    }
}
fn cancel(app: &tauri::AppHandle, id: Uuid) {
    let _ = app.run_on_main_thread(move || {
        let panel = PANEL.with(|slot| {
            slot.borrow()
                .as_ref()
                .filter(|(current, _)| *current == id)
                .map(|(_, panel)| panel.clone())
        });
        if let Some(panel) = panel {
            panel.cancel();
        }
    });
}
struct Waiter {
    app: tauri::AppHandle,
    id: Uuid,
    watch: tokio::task::JoinHandle<()>,
}
impl Drop for Waiter {
    fn drop(&mut self) {
        self.watch.abort();
        cancel(&self.app, self.id);
    }
}
pub async fn select(
    app: tauri::AppHandle,
    scope: FileScope,
    offer: Option<Uuid>,
) -> Result<Option<PathBuf>, String> {
    let watched = scope.clone();
    select_scoped(
        app,
        move || watched.picker_live(offer),
        move || match offer {
            Some(offer) => {
                let filename = scope.pending(offer)?;
                if crate::service_config::controller_only() {
                    // The sandbox grants only the selected URL. An explicit folder
                    // selection permits the secure sibling staging and final rename.
                    FilePicker::receive_into_folder(&filename)
                } else {
                    FilePicker::save(&filename)
                }
            }
            None => FilePicker::open(),
        },
    )
    .await
}

pub async fn select_scoped(
    app: tauri::AppHandle,
    live: impl Fn() -> bool + Send + Sync + 'static,
    prepare: impl FnOnce() -> Result<FilePicker, String> + Send + 'static,
) -> Result<Option<PathBuf>, String> {
    let live = Arc::new(live);
    if !live() {
        return Err("This session permission has expired".into());
    }
    if BUSY
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return Err("Finish the current file selection first".into());
    }
    let permit = Permit;
    let id = Uuid::new_v4();
    let (tx, rx) = tokio::sync::oneshot::channel();
    let watch_app = app.clone();
    let watch_live = live.clone();
    let watch = tokio::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_millis(100)).await;
            if !watch_live() {
                cancel(&watch_app, id);
                return;
            }
        }
    });
    let _waiter = Waiter {
        app: app.clone(),
        id,
        watch,
    };
    app.run_on_main_thread(move || {
        let _permit = permit;
        let result = (|| {
            if !live() {
                return Err("Session permission expired before selection".into());
            }
            let picker = prepare()?;
            PANEL.with(|slot| *slot.borrow_mut() = Some((id, picker.clone())));
            let result = if live() {
                picker.run()
            } else {
                Err("Session permission expired".into())
            };
            PANEL.with(|slot| {
                let mut current = slot.borrow_mut();
                if current.as_ref().is_some_and(|(value, _)| *value == id) {
                    *current = None;
                }
            });
            let path = result?;
            if !live() {
                return Err("Session permission expired during selection".into());
            }
            Ok(path)
        })();
        let _ = tx.send(result);
    })
    .map_err(|_| "Cannot show the Mac file picker")?;
    rx.await
        .map_err(|_| "The Mac file picker closed unexpectedly".to_owned())?
}
