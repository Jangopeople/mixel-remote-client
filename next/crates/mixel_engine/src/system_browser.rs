//! Open only validated HTTPS destinations using each desktop's URL handler.
pub async fn open(raw: &str) -> Result<(), String> {
    let url = reqwest::Url::parse(raw).map_err(|_| "Invalid browser destination")?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err("The browser destination must use HTTPS without embedded credentials".into());
    }
    let opened = tokio::task::spawn_blocking(move || launch(url.as_str())).await;
    if !matches!(opened, Ok(true)) {
        return Err("The system browser could not be opened".into());
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn launch(url: &str) -> bool {
    std::process::Command::new("/usr/bin/open")
        .arg(url)
        .status()
        .is_ok_and(|status| status.success())
}

#[cfg(target_os = "linux")]
fn launch(url: &str) -> bool {
    gio::AppInfo::launch_default_for_uri(url, None::<&gio::AppLaunchContext>).is_ok()
}

#[cfg(target_os = "windows")]
#[allow(unsafe_code)] // The shell call borrows a terminated UTF-16 URL only.
fn launch(url: &str) -> bool {
    use windows::{
        Win32::UI::{Shell::ShellExecuteW, WindowsAndMessaging::SW_SHOWNORMAL},
        core::{PCWSTR, w},
    };
    let wide: Vec<u16> = url.encode_utf16().chain(std::iter::once(0)).collect();
    // SAFETY: the UTF-16 pointer is valid for the entire call. No shell
    // command line, parameters, or working directory are supplied.
    let result = unsafe {
        ShellExecuteW(
            None,
            w!("open"),
            PCWSTR(wide.as_ptr()),
            PCWSTR::null(),
            PCWSTR::null(),
            SW_SHOWNORMAL,
        )
    };
    result.0 as isize > 32
}
