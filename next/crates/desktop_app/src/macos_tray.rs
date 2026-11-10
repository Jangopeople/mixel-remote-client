//! Native controls remain available while the support window is hidden. Tray
//! state comes from the native session owner, never a webview success flag.
use crate::{remote_session::RemoteSession, request_quit};
use std::time::Duration;
use tauri::{
    Manager,
    image::Image,
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
};

pub fn install(app: &tauri::AppHandle) -> tauri::Result<()> {
    let status = MenuItem::with_id(
        app,
        "remote-status",
        "Mixel Remote · Offline",
        false,
        None::<&str>,
    )?;
    let show = MenuItem::with_id(app, "remote-show", "Open Mixel Remote", true, None::<&str>)?;
    let hide = MenuItem::with_id(
        app,
        "remote-hide",
        "Hide support window",
        true,
        None::<&str>,
    )?;
    let stop = MenuItem::with_id(app, "remote-stop", "Stop session", false, None::<&str>)?;
    let offline = MenuItem::with_id(app, "remote-offline", "Go offline", false, None::<&str>)?;
    let quit = MenuItem::with_id(app, "remote-quit", "Quit Mixel Remote", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&status, &show, &hide, &stop, &offline, &quit])?;
    let icon = Image::from_bytes(include_bytes!("../../../../branding/icon-32.png"))?;
    TrayIconBuilder::with_id("mixel-remote")
        .icon(icon)
        .icon_as_template(false)
        .tooltip("Mixel Remote · Offline")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "remote-show" => show_window(app),
            "remote-hide" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
            }
            "remote-stop" => {
                let remote = app.state::<RemoteSession>().inner().clone();
                tauri::async_runtime::spawn(async move {
                    let status = remote.status().await;
                    if let Some(consent) = status.consent
                        && let Err(error) = remote.stop_session(consent).await
                    {
                        remote.report_error(error).await;
                    }
                });
            }
            "remote-offline" => {
                let remote = app.state::<RemoteSession>().inner().clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(error) = remote.stop().await {
                        remote.report_error(error).await;
                    }
                });
            }
            "remote-quit" => request_quit(app.clone()),
            _ => {}
        })
        .build(app)?;
    let app = app.clone();
    let remote = app.state::<RemoteSession>().inner().clone();
    tauri::async_runtime::spawn(async move {
        let mut previous = String::new();
        let mut last_phase = String::new();
        loop {
            tokio::time::sleep(Duration::from_millis(300)).await;
            let current = remote.status().await;
            let text = match current.phase.as_str() {
                "active" if current.sharing => "Sharing active",
                "active" => "Remote session active",
                "incoming" => "Incoming request",
                "ready" => "Ready",
                "connecting" => "Connecting",
                "error" => "Attention required",
                "offline" => "Offline",
                _ => "Session pending",
            };
            let detail = format!(
                "Mixel Remote · {text}{}",
                current
                    .host
                    .map(|id| format!(" · {id}"))
                    .unwrap_or_default()
            );
            if detail != previous {
                let _ = status.set_text(&detail);
                let _ = stop.set_enabled(current.session.is_some() && current.phase != "closing");
                let _ = offline.set_enabled(current.phase != "offline");
                if let Some(tray) = app.tray_by_id("mixel-remote") {
                    let _ = tray.set_tooltip(Some(&detail));
                    let _ = tray.set_title(if current.phase == "active" && current.sharing {
                        Some("Sharing")
                    } else if current.phase == "incoming" {
                        Some("Request")
                    } else {
                        None
                    });
                }
                previous = detail;
            }
            if current.phase == "incoming" && last_phase != "incoming" {
                show_window(&app);
            }
            last_phase = current.phase;
        }
    });
    Ok(())
}
fn show_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}
