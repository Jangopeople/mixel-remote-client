//! Commands of the native Linux app that need this computer itself: its screens (X11 or the Wayland
//! sharing dialog), keyboard and mouse, the clipboard, Wake-on-LAN, the remote printer and access to
//! the sign-in screen. Same names, arguments and results as the desktop app.
use super::{Args, Context, browser, out};
use crate::{host_actions, host_print, ipp_printer, linux_guard, linux_signin_host, self_update, service_config, session_platform, wayland_portal};
use serde_json::json;
use std::sync::{Arc, atomic::AtomicBool};

pub(super) async fn platform(context: &Context, command: &str, args: &Args) -> Result<String, String> {
    match command {
        "capture_targets" => {
            service_config::require_host()?;
            if wayland_portal::available() {
                return out(vec![json!({
                    "id": wayland_portal::SOURCE,
                    "name": "Choose a display in the system sharing dialog",
                    "full_display": true,
                })]);
            }
            let displays = tokio::task::spawn_blocking(native_os_capture::x11_capture::displays)
                .await
                .map_err(|_| "X11 display discovery failed")?
                .map_err(|error| error.to_string())?;
            out(displays
                .into_iter()
                .map(|display| {
                    json!({
                        "id": format!("x11:{}:{}", display.screen, display.root),
                        "name": format!("X11 screen {} · {} × {}", display.screen, display.width, display.height),
                        "full_display": true,
                    })
                })
                .collect::<Vec<_>>())
        }
        "screen_access" | "request_screen_access" => out(session_platform::capture_access()),
        "request_input_access" => out(session_platform::input_access()),
        // What this desktop allows: Wayland asks in its own dialog every time and lets no program
        // darken the screen, block input or draw a pointer for other programs.
        "linux_session" => out(json!({
            "wayland": wayland_portal::available(),
            "x11": linux_guard::x11_available(),
        })),
        "copy_connection_id" => {
            service_config::require_host()?;
            let id = context.remote.status().await.host.ok_or("Make this computer available before copying its connection ID")?;
            out(session_platform::write_text(&id.to_string())?)
        }
        "copy_text" => {
            let text: String = args.get("text")?;
            if text.len() > 8192 {
                return Err("This text is too long to copy".into());
            }
            out(tokio::task::spawn_blocking(move || session_platform::write_text(&text))
                .await
                .map_err(|_| "The clipboard is unavailable")??)
        }
        "wake_on_lan" => {
            let macs: Vec<String> = args.get("macs")?;
            if macs.is_empty() || macs.len() > 4 {
                return Err("No network address is known for this computer yet".into());
            }
            out(tokio::task::spawn_blocking(move || macs.iter().try_for_each(|mac| host_actions::wake(mac)))
                .await
                .map_err(|_| "The wake signal could not be sent")??)
        }
        "remote_printing_status" => {
            let installed = tokio::task::spawn_blocking(host_print::installed).await.unwrap_or(false);
            let supported = tokio::task::spawn_blocking(host_print::print_system_available).await.unwrap_or(false);
            out(json!({ "supported": supported, "installed": installed }))
        }
        "remote_printing_set" => {
            let enabled: bool = args.get("enabled")?;
            out(tokio::task::spawn_blocking(move || ipp_printer::set_installed(enabled))
                .await
                .map_err(|_| "The printer could not be changed")??)
        }
        "print_job" => {
            let path: String = args.get("path")?;
            let open: bool = args.get("open")?;
            out(tokio::task::spawn_blocking(move || {
                if open { host_print::open(&path).map(|()| "opened".to_owned()) } else { host_print::print(&path).map(str::to_owned) }
            })
            .await
            .map_err(|_| "The print job could not be handled")??)
        }
        "signin_screen_status" => {
            let status = linux_signin_host::public_status();
            let outdated = status.is_some() && tokio::task::spawn_blocking(linux_signin_host::program_outdated).await.unwrap_or(false);
            out(json!({
                "supported": true,
                "outdated": outdated,
                "technician": linux_signin_host::technician_connected(),
                "connection_id": status.as_ref().map(|status| status.connection_id),
                "name": status.map(|status| status.name),
                // Only an X11 sign-in screen can be shared; a Wayland one is reported, not offered.
                "wayland_greeter": linux_signin_host::service_state().as_deref() == Some("wayland"),
            }))
        }
        "signin_screen_enable" => signin_enable(context, args).await,
        "signin_screen_update" | "signin_screen_disable" => {
            let step = if command == "signin_screen_update" { "--signin-update" } else { "--signin-remove" };
            tokio::task::spawn_blocking(move || linux_signin_host::run_elevated(step))
                .await
                .map_err(|_| "Administrator step failed")??;
            out(serde_json::Value::Null)
        }
        "update_install" => {
            if cfg!(feature = "qa-session") && std::env::var_os("MIXEL_UPDATE_CHANNEL").is_none() {
                return Err("Lab builds do not update themselves".into());
            }
            let manifest = self_update::latest().await?;
            if !newer(&manifest.version) {
                return Err("This is already the newest version".into());
            }
            // The app quits right after this answer; the updater starts it again.
            out(self_update::download_and_install(manifest).await?)
        }
        "open_browser" => {
            let url: String = args.get("url")?;
            out(browser(&url).await?)
        }
        _ => Err(format!("Unknown command: {command}")),
    }
}

/// Sign-in screen access (X11 greeter): a computer-wide key, its enrolment and the system service.
/// Two administrator prompts. Never replaces an installation of the current Mixel Remote app.
async fn signin_enable(context: &Context, args: &Args) -> Result<String, String> {
    let company: uuid::Uuid = args.get("company")?;
    let name: String = args.get("name")?;
    let cancelled = Arc::new(AtomicBool::new(false));
    let credential = context.auth.credential(Some(&cancelled)).await?;
    if !credential.principal.staff {
        return Err("Sign-in screen access requires an MFA-verified Mixel team account".into());
    }
    let name = name.trim().to_owned();
    if name.is_empty() || name.chars().count() > 60 || name.chars().any(char::is_control) {
        return Err("Enter a computer name of up to 60 characters".into());
    }
    tokio::task::spawn_blocking(|| linux_signin_host::run_elevated("--signin-provision"))
        .await
        .map_err(|_| "Administrator step failed")??;
    let key = linux_signin_host::provisioned_public_key()?;
    let ism = &credential.ism;
    let token = &credential.token;
    let device = match ism.enrollment_of_key(token, &key).await.map_err(|_| "The enrolment status is unavailable")? {
        Some(current) => current.device,
        None => {
            let device = ism
                .add_device(token, company, &format!("{name} (sign-in screen)"), "linux")
                .await
                .map_err(|_| "The sign-in screen record could not be added for this company")?;
            ism.enroll(token, device, key, &cancelled).await.map_err(|_| "The sign-in screen could not be enrolled")?;
            device
        }
    };
    let enrolled = ism.authenticate_device(token, device).await.map_err(|_| "The sign-in screen enrolment could not be confirmed")?;
    if enrolled.public_key != key {
        return Err("The sign-in screen enrolment does not match this computer".into());
    }
    let tenant = ism.device_tenant(token, device).await.map_err(|_| "The company of this computer is unavailable")?;
    let settings = linux_signin_host::Settings {
        version: 1,
        device,
        profile: enrolled.principal.user,
        tenant,
        connection_id: enrolled.connection_id,
        generation: enrolled.generation,
        control: true,
        always_on: false,
    };
    let payload = serde_json::to_vec(&json!({ "settings": settings, "name": name })).map_err(|_| "Cannot prepare the sign-in screen settings")?;
    let hex: String = payload.iter().map(|byte| format!("{byte:02x}")).collect();
    tokio::task::spawn_blocking(move || linux_signin_host::run_elevated(&format!("--signin-install {hex}")))
        .await
        .map_err(|_| "Administrator step failed")??;
    out(enrolled.connection_id)
}

fn newer(version: &str) -> bool {
    matches!((crate::version_parts(crate::agent::APP_VERSION), crate::version_parts(version)), (Some(current), Some(next)) if next > current)
}

/// A newer signed release on the update channel (installable in the app with one administrator
/// prompt); else the store listing (download only). Lab builds never update unless a test channel is set.
pub(super) async fn update_check(context: &Context) -> Result<String, String> {
    if cfg!(feature = "qa-session") && std::env::var_os("MIXEL_UPDATE_CHANNEL").is_none() {
        return out(serde_json::Value::Null);
    }
    if let Ok(manifest) = self_update::latest().await {
        if !newer(&manifest.version) {
            return out(serde_json::Value::Null);
        }
        let idle = matches!(context.remote.status().await.phase.as_str(), "offline" | "error" | "ready");
        let account = context.auth.status().await;
        let keeps_sign_in = account.phase != "authenticated" || account.account.as_ref().is_some_and(|account| account.remembered);
        return out(json!({
            "version": manifest.version,
            "installable": true,
            "silent": idle && keeps_sign_in && !self_update::NEEDS_PASSWORD,
            "needs_password": self_update::NEEDS_PASSWORD,
        }));
    }
    #[derive(serde::Deserialize)]
    struct Row {
        version: String,
    }
    let url = "https://store-api.mixel.ch/rest/v1/store_app_releases?select=version&app_slug=eq.mixel-remote&is_current=eq.true&platform=eq.linux&variant=eq.managed&limit=5";
    let rows: Vec<Row> = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| "Update check unavailable")?
        .get(url)
        .send()
        .await
        .and_then(reqwest::Response::error_for_status)
        .map_err(|_| "Update check unavailable")?
        .json()
        .await
        .map_err(|_| "Update check unavailable")?;
    out(rows
        .into_iter()
        .filter(|row| row.version.len() <= 40 && newer(&row.version))
        .filter_map(|row| crate::version_parts(&row.version).map(|parts| (parts, row.version)))
        .max_by(|a, b| a.0.cmp(&b.0))
        .map(|(_, version)| json!({ "version": version, "installable": false, "silent": false, "needs_password": false })))
}
