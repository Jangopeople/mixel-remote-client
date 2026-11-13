//! Session reports (SQL 029) in the desktop app: saves a CSV or PDF the web UI built
//! (`report-export.ts`) where the person chooses, and opens the portal's billing page for a plan
//! with reports. No session scope is involved, unlike the session file transfer.
use std::path::PathBuf;

/// A generous limit for 5000 report rows.
const MAX_BYTES: usize = 20 * 1024 * 1024;

#[cfg(any(target_os = "windows", target_os = "linux"))]
static PICKER: tokio::sync::Semaphore = tokio::sync::Semaphore::const_new(1);

/// The suggested file name and content must be a CSV or PDF report the app produced.
fn check(name: &str, content: &str) -> Result<&'static str, String> {
    let kind = if name.ends_with(".pdf") {
        "pdf"
    } else if name.ends_with(".csv") {
        "csv"
    } else {
        return Err("Only CSV and PDF reports can be saved".into());
    };
    if name.len() > 160
        || name.starts_with('.')
        || !name.chars().all(|value| value.is_ascii_alphanumeric() || matches!(value, '-' | '_' | '.'))
    {
        return Err("The proposed file name is not supported".into());
    }
    if content.is_empty() || content.len() > MAX_BYTES {
        return Err("The report is empty or too large".into());
    }
    // The PDF writer emits ASCII only; anything else is not a report of this app.
    if kind == "pdf" && (!content.starts_with("%PDF-1.4\n") || !content.is_ascii()) {
        return Err("The report is not a valid PDF".into());
    }
    Ok(kind)
}

fn write(path: PathBuf, content: &str) -> Result<String, String> {
    std::fs::write(&path, content.as_bytes()).map_err(|error| format!("The report could not be saved: {error}"))?;
    Ok(path.display().to_string())
}

/// Asks where to save `name` and writes `content` (UTF-8; the PDF is ASCII). Returns the path, or
/// nothing when the person cancelled.
#[tauri::command]
pub async fn save_report_file(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    name: String,
    content: String,
) -> Result<Option<String>, String> {
    crate::main_window(&window)?;
    let kind = check(&name, &content)?;
    #[cfg(target_os = "macos")]
    let path = {
        let _ = kind;
        let suggestion = name.clone();
        crate::macos_picker::select_scoped(app, || true, move || {
            native_os_capture::macos_file_picker::FilePicker::save_export(&suggestion)
        })
        .await?
    };
    #[cfg(not(target_os = "macos"))]
    let path = {
        let _ = app;
        let _permit = PICKER.try_acquire().map_err(|_| "Finish the current file selection first")?;
        let (label, extension) = if kind == "pdf" { ("PDF document", "pdf") } else { ("CSV table", "csv") };
        rfd::AsyncFileDialog::new()
            .set_parent(&window)
            .set_title("Save the session report")
            .set_file_name(&name)
            .add_filter(label, &[extension])
            .save_file()
            .await
            .map(|file| file.path().to_owned())
    };
    match path {
        Some(path) => write(path, &content).map(Some),
        None => Ok(None),
    }
}

/// Plan & billing on remote.mixel.ch (in the app's language), in the system browser.
#[tauri::command]
pub async fn open_billing_page(window: tauri::WebviewWindow, lang: Option<String>) -> Result<(), String> {
    crate::main_window(&window)?;
    let lang = lang.filter(|value| ["en", "de", "fr", "it"].contains(&value.as_str())).unwrap_or_else(|| "en".into());
    crate::system_browser::open(&format!("https://remote.mixel.ch/app/billing?lang={lang}")).await
}

#[cfg(test)]
mod tests {
    use super::check;

    #[test]
    fn only_csv_and_pdf_reports_of_the_app_are_saved() {
        assert_eq!(check("mixel-remote-acme-sessions-2026-10-14-2026-11-13.csv", "\u{feff}a,b\r\n"), Ok("csv"));
        assert_eq!(check("mixel-remote-acme-sessions-2026-10-14-2026-11-13.pdf", "%PDF-1.4\n%%EOF\n"), Ok("pdf"));
        assert!(check("report.exe", "x").is_err());
        assert!(check("../report.csv", "x").is_err());
        assert!(check(".csv", "x").is_err());
        assert!(check("report.csv", "").is_err());
        assert!(check("report.pdf", "not a pdf").is_err());
        assert!(check("report.pdf", "%PDF-1.4\n\u{e9}").is_err());
    }
}
