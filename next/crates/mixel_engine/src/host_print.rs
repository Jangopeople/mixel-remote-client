//! Remote printing. On a Windows host the printer "Mixel Remote Printer" writes each
//! print job as a PDF into a folder; on macOS and Linux the CUPS printer of the same name
//! sends it to this app (`ipp_printer`). During a session the host sends that PDF to the
//! technician, whose app offers to print it on a local printer or to open it.
use std::path::{Path, PathBuf};

/// Session feature name announced by apps that understand print jobs.
pub const FEATURE: &str = "print";
/// Prefix of the control records that carry a print job from the host.
pub const RECORD: &[u8] = b"mixel-print-v1:";
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub const PRINTER: &str = "Mixel Remote Printer";

/// macOS and Linux: whether the print system (CUPS) is there to add the printer to.
#[cfg(any(target_os = "macos", target_os = "linux"))]
pub fn print_system_available() -> bool {
    std::process::Command::new("lpstat").arg("-r").output().is_ok_and(|output| output.status.success())
}
#[cfg(target_os = "windows")]
pub fn print_system_available() -> bool {
    true
}
const JOB_NAME: &str = "Remote print.pdf";

#[cfg(target_os = "windows")]
fn spool_folder() -> PathBuf {
    PathBuf::from(std::env::var_os("ProgramData").unwrap_or_else(|| "C:\\ProgramData".into()))
        .join("Mixel Remote")
        .join("print")
}

#[cfg(target_os = "windows")]
fn hidden(program: &str) -> std::process::Command {
    use std::os::windows::process::CommandExt;
    let mut command = std::process::Command::new(program);
    command.creation_flags(0x0800_0000);
    command
}

/// Whether the remote printer exists on this computer (Windows hosts only).
pub fn installed() -> bool {
    #[cfg(target_os = "windows")]
    {
        hidden("reg.exe")
            .args(["query", &format!("HKLM\\SYSTEM\\CurrentControlSet\\Control\\Print\\Printers\\{PRINTER}")])
            .output()
            .is_ok_and(|output| output.status.success())
    }
    #[cfg(any(target_os = "macos", target_os = "linux"))]
    {
        crate::ipp_printer::installed()
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        false
    }
}

/// Elevated: `--printer-install` or `--printer-remove`. `None` for other commands.
#[cfg(target_os = "windows")]
pub fn admin(command: &str) -> Option<Result<(), String>> {
    let script = match command {
        "--printer-install" => format!(
            "$ErrorActionPreference='Stop'; $d=Join-Path $env:ProgramData 'Mixel Remote\\print'; \
             New-Item -ItemType Directory -Force $d | Out-Null; \
             icacls $d /grant '*S-1-5-32-545:(OI)(CI)M' | Out-Null; \
             $p=Join-Path $d 'job.pdf'; \
             if(-not (Get-PrinterPort -Name $p -ErrorAction SilentlyContinue)){{ Add-PrinterPort -Name $p }}; \
             if(-not (Get-Printer -Name '{PRINTER}' -ErrorAction SilentlyContinue)){{ Add-Printer -Name '{PRINTER}' -DriverName 'Microsoft Print To PDF' -PortName $p }}"
        ),
        "--printer-remove" => format!(
            "$d=Join-Path $env:ProgramData 'Mixel Remote\\print'; $p=Join-Path $d 'job.pdf'; \
             Remove-Printer -Name '{PRINTER}' -ErrorAction SilentlyContinue; \
             Remove-PrinterPort -Name $p -ErrorAction SilentlyContinue; \
             Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue; \
             if(Get-Printer -Name '{PRINTER}' -ErrorAction SilentlyContinue){{ exit 1 }}"
        ),
        _ => return None,
    };
    Some(
        hidden("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", &script])
            .output()
            .map_err(|_| "Windows PowerShell could not be started".to_owned())
            .and_then(|output| {
                if output.status.success() {
                    Ok(())
                } else {
                    Err("The printer could not be changed".into())
                }
            }),
    )
}

/// Host side: notices a finished print job in the spool folder.
#[derive(Default)]
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub struct Watcher {
    seen: Option<(u64, std::time::SystemTime, std::time::Instant)>,
}
impl Watcher {
    /// Starts watching; a job left over from before the session is discarded.
    pub fn start() -> Self {
        #[cfg(target_os = "windows")]
        let _ = std::fs::remove_file(spool_folder().join("job.pdf"));
        #[cfg(any(target_os = "macos", target_os = "linux"))]
        {
            crate::ipp_printer::ensure_running();
            crate::ipp_printer::clear();
        }
        Self::default()
    }
    /// A complete job, once its file has stopped changing for a moment.
    pub fn poll(&mut self) -> Option<Vec<u8>> {
        #[cfg(target_os = "windows")]
        {
            let path = spool_folder().join("job.pdf");
            let Ok(metadata) = std::fs::metadata(&path) else {
                self.seen = None;
                return None;
            };
            let state = (metadata.len(), metadata.modified().ok()?);
            match self.seen {
                Some((length, modified, since)) if (length, modified) == state => {
                    if since.elapsed() < std::time::Duration::from_millis(1500) || length < 64 {
                        return None;
                    }
                }
                _ => {
                    self.seen = Some((state.0, state.1, std::time::Instant::now()));
                    return None;
                }
            }
            // Still being written by the print system: try again on the next poll.
            let data = std::fs::read(&path).ok()?;
            if data.len() as u64 != state.0 {
                return None;
            }
            self.seen = None;
            let _ = std::fs::remove_file(&path);
            (data.starts_with(b"%PDF") && data.len() <= core_protocol::clipboard::MAX_RICH - 4096).then_some(data)
        }
        #[cfg(any(target_os = "macos", target_os = "linux"))]
        {
            crate::ipp_printer::take()
        }
        #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
        {
            None
        }
    }
}

/// The transfer payload of one print job.
pub fn payload(pdf: Vec<u8>) -> Result<zeroize::Zeroizing<Vec<u8>>, String> {
    core_protocol::clipboard::Content::Files(vec![(JOB_NAME.into(), pdf)])
        .encode()
        .map_err(|_| "The print job cannot be transferred".into())
}

fn received_folder() -> PathBuf {
    std::env::temp_dir().join("Mixel Remote Print")
}

/// Controller side: keeps a received job as a PDF file and returns its path.
pub fn store(pdf: &[u8]) -> Result<String, String> {
    if !pdf.starts_with(b"%PDF") {
        return Err("The received print job is not a PDF".into());
    }
    let folder = received_folder();
    std::fs::create_dir_all(&folder).map_err(|_| "Cannot store the print job")?;
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_millis())
        .unwrap_or(0);
    let path = folder.join(format!("Remote print {stamp}.pdf"));
    std::fs::write(&path, pdf).map_err(|_| "Cannot store the print job")?;
    path.to_str().map(str::to_owned).ok_or_else(|| "Cannot store the print job".into())
}

/// Removes print jobs received in earlier sessions.
#[cfg_attr(target_os = "macos", allow(dead_code))]
pub fn clear_received() {
    let _ = std::fs::remove_dir_all(received_folder());
}

fn received(path: &str) -> Result<PathBuf, String> {
    let path = Path::new(path);
    let inside = path.parent() == Some(received_folder().as_path())
        && path.extension().is_some_and(|extension| extension == "pdf")
        && path.is_file();
    if inside { Ok(path.to_owned()) } else { Err("This print job is no longer available".into()) }
}

/// Opens a received job in the default PDF program.
pub fn open(path: &str) -> Result<(), String> {
    let path = received(path)?;
    #[cfg(target_os = "windows")]
    let started = hidden("explorer.exe").arg(&path).spawn();
    #[cfg(target_os = "macos")]
    let started = std::process::Command::new("/usr/bin/open").arg(&path).spawn();
    #[cfg(target_os = "linux")]
    let started = std::process::Command::new("xdg-open").arg(&path).spawn();
    #[cfg(target_os = "ios")]
    let started: std::io::Result<std::process::Child> = { let _ = &path; Err(std::io::Error::other("unsupported")) };
    started.map(|_| ()).map_err(|_| "The print job could not be opened".into())
}

/// Prints a received job on this computer's default printer. On Windows this needs a
/// PDF program that can print directly; otherwise the job is opened instead.
pub fn print(path: &str) -> Result<&'static str, String> {
    let file = received(path)?;
    #[cfg(target_os = "windows")]
    {
        let quoted = file.to_string_lossy().replace('\'', "''");
        let done = hidden("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-Command", &format!("Start-Process -FilePath '{quoted}' -Verb Print -ErrorAction Stop")])
            .output()
            .is_ok_and(|output| output.status.success());
        if done {
            return Ok("printed");
        }
        open(path)?;
        Ok("opened")
    }
    #[cfg(not(target_os = "windows"))]
    {
        let done = std::process::Command::new("lp")
            .arg(&file)
            .output()
            .is_ok_and(|output| output.status.success());
        if done {
            return Ok("printed");
        }
        open(path)?;
        Ok("opened")
    }
}
