//! Windows sign-in screen access. A LocalSystem service starts a headless host
//! on the console's sign-in screen only while nobody is signed in, and stops it
//! as soon as a user signs in (the normal app then takes over). The host uses
//! its own device record, Remote ID and computer-wide key, and only accepts
//! policy-approved unattended sessions.
#![allow(unsafe_code)]
use crate::device_identity::IdentityState;
use serde::{Deserialize, Serialize};
use signaling_server::auth::{EnrolledPrincipal, Principal};
use std::{
    ffi::c_void,
    os::windows::ffi::OsStrExt,
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicIsize, Ordering},
    },
    time::Duration,
};
use uuid::Uuid;
use windows::{
    Win32::{
        Foundation::{CloseHandle, HANDLE, NO_ERROR, WAIT_OBJECT_0},
        Security::{
            DuplicateTokenEx, SecurityImpersonation, SetTokenInformation, TOKEN_ACCESS_MASK,
            TOKEN_ADJUST_DEFAULT, TOKEN_ADJUST_SESSIONID, TOKEN_ASSIGN_PRIMARY, TOKEN_DUPLICATE,
            TOKEN_QUERY, TokenPrimary, TokenSessionId,
        },
        System::{
            RemoteDesktop::{
                WTS_CURRENT_SERVER_HANDLE, WTSFreeMemory, WTSGetActiveConsoleSessionId,
                WTSQuerySessionInformationW, WTSUserName,
            },
            Services::{
                RegisterServiceCtrlHandlerExW, SERVICE_ACCEPT_SHUTDOWN, SERVICE_ACCEPT_STOP,
                SERVICE_CONTROL_INTERROGATE, SERVICE_CONTROL_SHUTDOWN, SERVICE_CONTROL_STOP,
                SERVICE_RUNNING, SERVICE_STATUS, SERVICE_STATUS_CURRENT_STATE,
                SERVICE_STATUS_HANDLE, SERVICE_STOP_PENDING, SERVICE_STOPPED, SERVICE_TABLE_ENTRYW,
                SERVICE_WIN32_OWN_PROCESS, SetServiceStatus, StartServiceCtrlDispatcherW,
            },
            Threading::{
                CREATE_NO_WINDOW, CREATE_UNICODE_ENVIRONMENT, CreateProcessAsUserW,
                GetCurrentProcess, GetExitCodeProcess, OpenProcessToken, PROCESS_INFORMATION,
                STARTUPINFOW, TerminateProcess, WaitForSingleObject,
            },
        },
        UI::{
            Shell::{SEE_MASK_NOCLOSEPROCESS, SHELLEXECUTEINFOW, ShellExecuteExW},
            WindowsAndMessaging::SW_HIDE,
        },
    },
    core::{PCWSTR, PWSTR},
};

const SERVICE_NAME: &str = "MixelRemoteSignIn";
const SERVICE_DISPLAY: &str = "Mixel Remote sign-in screen access";

fn wide(text: &str) -> Vec<u16> {
    std::ffi::OsStr::new(text)
        .encode_wide()
        .chain(Some(0))
        .collect()
}

fn folder() -> PathBuf {
    let base = std::env::var_os("ProgramData")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\ProgramData"));
    base.join("Mixel Remote")
}
pub fn machine_identity_path() -> PathBuf {
    folder().join("machine-identity-v1.dpapi")
}
fn settings_path() -> PathBuf {
    folder().join("signin-host.json")
}
fn public_key_path() -> PathBuf {
    folder().join("machine-identity.pub")
}
fn public_status_path() -> PathBuf {
    folder().join("signin-host-status.json")
}

fn log(message: &str) {
    use std::io::Write;
    let path = folder().join("signin-host.log");
    if std::fs::metadata(&path).is_ok_and(|meta| meta.len() > 256 * 1024) {
        let _ = std::fs::rename(&path, folder().join("signin-host.old.log"));
    }
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    {
        let seconds = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |time| time.as_secs());
        let _ = writeln!(file, "{seconds} {message}");
    }
}

fn program_folder() -> PathBuf {
    let base = std::env::var_os("ProgramFiles")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Program Files"));
    base.join("Mixel Remote Sign-in Screen")
}
fn program_path() -> PathBuf {
    program_folder().join("mixel-remote-signin.exe")
}

fn icacls(arguments: &[&std::ffi::OsStr]) -> bool {
    use std::os::windows::process::CommandExt;
    std::process::Command::new("icacls")
        .args(arguments)
        .creation_flags(0x0800_0000)
        .output()
        .is_ok_and(|output| output.status.success())
}

/// Elevated: the data folder is owned by Administrators; users may only read it
/// (status and public key). Secret files are re-restricted to SYSTEM and Administrators.
fn secure_folder() -> Result<(), String> {
    let dir = folder();
    std::fs::create_dir_all(&dir).map_err(|_| "Cannot create the Mixel Remote data folder")?;
    let path = dir.as_os_str();
    let owned = icacls(&[
        path,
        "/setowner".as_ref(),
        "*S-1-5-32-544".as_ref(),
        "/T".as_ref(),
        "/C".as_ref(),
    ]);
    let limited = icacls(&[
        path,
        "/inheritance:r".as_ref(),
        "/grant:r".as_ref(),
        "*S-1-5-18:(OI)(CI)F".as_ref(),
        "*S-1-5-32-544:(OI)(CI)F".as_ref(),
        "*S-1-5-32-545:(OI)(CI)RX".as_ref(),
    ]);
    if !owned || !limited {
        return Err("Cannot protect the Mixel Remote data folder".into());
    }
    for secret in [machine_identity_path(), settings_path()] {
        if secret.exists() {
            restrict(&secret)?;
        }
    }
    Ok(())
}

/// Writes a file that only SYSTEM and Administrators can read or change.
pub fn write_private(path: &std::path::Path, bytes: &[u8]) -> Result<(), String> {
    std::fs::create_dir_all(folder()).map_err(|_| "Cannot create the Mixel Remote data folder")?;
    let temporary = path.with_extension("tmp");
    std::fs::write(&temporary, bytes).map_err(|_| "Cannot write the sign-in screen settings")?;
    restrict(&temporary)?;
    std::fs::rename(&temporary, path).map_err(|_| "Cannot write the sign-in screen settings".into())
}
fn restrict(path: &std::path::Path) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    let ok = std::process::Command::new("icacls")
        .arg(path)
        .args([
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:F",
            "*S-1-5-32-544:F",
        ])
        .creation_flags(0x0800_0000)
        .output()
        .is_ok_and(|output| output.status.success());
    if ok {
        Ok(())
    } else {
        Err("Cannot protect the sign-in screen files".into())
    }
}

/// The capture source of a Windows display, by index.
pub fn primary_display_source(index: u32) -> Result<String, String> {
    let displays =
        native_os_capture::windows_capture::displays().map_err(|error| error.to_string())?;
    let display = displays
        .get(index as usize)
        .or(displays.first())
        .ok_or("No display is available")?;
    Ok(format!(
        "dxgi:{}:{}:{}",
        display.adapter, display.output, display.name
    ))
}

#[derive(Serialize, Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct Settings {
    pub version: u16,
    pub device: Uuid,
    pub profile: Uuid,
    pub tenant: Uuid,
    pub connection_id: u32,
    pub generation: u64,
    pub control: bool,
}
impl Settings {
    fn valid(&self) -> bool {
        self.version == 1
            && !self.device.is_nil()
            && !self.profile.is_nil()
            && !self.tenant.is_nil()
            && (100_000_000..=999_999_999).contains(&self.connection_id)
            && (1..=i64::MAX as u64).contains(&self.generation)
    }
}
#[derive(Serialize, Deserialize)]
pub struct PublicStatus {
    pub connection_id: u32,
    pub name: String,
}

/// Status the normal app may read: the sign-in screen Remote ID, if installed.
pub fn public_status() -> Option<PublicStatus> {
    let bytes = std::fs::read(public_status_path()).ok()?;
    serde_json::from_slice(&bytes).ok()
}
/// True when the installed sign-in screen program differs from this app version.
pub fn program_outdated() -> bool {
    let Ok(current) = std::env::current_exe() else {
        return false;
    };
    if current == program_path() {
        return false;
    }
    match (std::fs::read(&current), std::fs::read(program_path())) {
        (Ok(mine), Ok(installed)) => mine != installed,
        _ => false,
    }
}

/// Public key of the computer-wide identity, after provisioning.
pub fn provisioned_public_key() -> Result<ed25519_dalek::VerifyingKey, String> {
    let text = std::fs::read_to_string(public_key_path())
        .map_err(|_| "The sign-in screen identity was not created")?;
    let text = text.trim();
    if text.len() != 64 || !text.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("The sign-in screen identity is invalid".into());
    }
    let mut bytes = [0u8; 32];
    for (index, byte) in bytes.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&text[index * 2..index * 2 + 2], 16)
            .map_err(|_| "The sign-in screen identity is invalid")?;
    }
    ed25519_dalek::VerifyingKey::from_bytes(&bytes)
        .map_err(|_| "The sign-in screen identity is invalid".into())
}

/// Runs this executable elevated (UAC prompt) and waits for it.
pub fn run_elevated(arguments: &str) -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
    let file: Vec<u16> = exe.as_os_str().encode_wide().chain(Some(0)).collect();
    let verb = wide("runas");
    let parameters = wide(arguments);
    let mut info = SHELLEXECUTEINFOW {
        cbSize: size_of::<SHELLEXECUTEINFOW>() as u32,
        fMask: SEE_MASK_NOCLOSEPROCESS,
        lpVerb: PCWSTR(verb.as_ptr()),
        lpFile: PCWSTR(file.as_ptr()),
        lpParameters: PCWSTR(parameters.as_ptr()),
        nShow: SW_HIDE.0,
        ..Default::default()
    };
    // SAFETY: every pointer in `info` refers to live, null-terminated buffers above.
    unsafe { ShellExecuteExW(&mut info) }.map_err(|_| "Administrator approval was declined")?;
    if info.hProcess.is_invalid() {
        return Err("Administrator step did not start".into());
    }
    // SAFETY: hProcess is a valid process handle owned by this call.
    let code = unsafe {
        let waited = WaitForSingleObject(info.hProcess, 120_000);
        let mut code = 1u32;
        let _ = GetExitCodeProcess(info.hProcess, &mut code);
        let _ = CloseHandle(info.hProcess);
        if waited != WAIT_OBJECT_0 { 1 } else { code }
    };
    if code == 0 {
        Ok(())
    } else {
        Err("The administrator step failed".into())
    }
}

fn sc(arguments: &[&str]) -> bool {
    use std::os::windows::process::CommandExt;
    std::process::Command::new("sc.exe")
        .args(arguments)
        .creation_flags(0x0800_0000)
        .output()
        .is_ok_and(|output| output.status.success())
}

/// Elevated: `--signin-provision`, `--signin-install <hex json>`, `--signin-remove`.
pub fn admin(command: &str, argument: Option<&str>) -> Result<(), String> {
    secure_folder()?;
    match command {
        "--signin-provision" => {
            let identity = IdentityState::machine(true)?;
            let key = identity.public_key()?;
            let hex: String = key
                .as_bytes()
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect();
            std::fs::write(public_key_path(), hex)
                .map_err(|_| "Cannot publish the sign-in screen key".into())
        }
        "--signin-enroll" => {
            // Rollout: enrol this computer with a company token and turn on always-on access.
            // `main` reads the token from MIXEL_DEPLOY_TOKEN or standard input (deploy_token.rs),
            // never from the command line, where other users could read it.
            let secret = argument.ok_or("Missing rollout token")?.trim().to_owned();
            if !crate::deploy_token::valid(&secret) {
                return Err("The deploy token is invalid".into());
            }
            let name = std::env::var("COMPUTERNAME").unwrap_or_else(|_| "Windows computer".into());
            let name: String = name.chars().filter(|c| !c.is_control()).take(60).collect();
            let identity = IdentityState::machine(true)?;
            let key = identity.public_key()?;
            let hex: String = key.as_bytes().iter().map(|byte| format!("{byte:02x}")).collect();
            let _ = std::fs::write(public_key_path(), hex);
            let config = crate::service_config::ServiceConfig::load()?;
            let base = config.identity_url.parse().map_err(|_| "Invalid identity service configuration")?;
            let ism = signaling_server::auth::IsmAuth::new(base, config.public_key)
                .map_err(|_| "Invalid identity service configuration")?;
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .map_err(|_| "Cannot start the rollout")?;
            let enrolled = runtime
                .block_on(ism.enroll_with_rollout_token(&secret, &name, "windows", &key))
                .map_err(|_| crate::ROLLOUT_REFUSED)?;
            let install = serde_json::json!({
                "settings": Settings {
                    version: 1,
                    device: enrolled.device_id,
                    profile: enrolled.profile,
                    tenant: enrolled.tenant,
                    connection_id: enrolled.connection_id,
                    generation: enrolled.generation,
                    control: true,
                },
                "name": name,
            });
            let payload: String = serde_json::to_vec(&install)
                .map_err(|_| "Cannot prepare the settings")?
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect();
            admin("--signin-install", Some(&payload))?;
            let _ = std::fs::write(folder().join("rollout-result.txt"), format!("Remote ID {}\n", enrolled.connection_id));
            log(&format!("rolled out as Remote ID {}", enrolled.connection_id));
            Ok(())
        }
        "--signin-install" => {
            let hex = argument.ok_or("Missing settings")?;
            if hex.len() > 8192 || hex.len() % 2 != 0 {
                return Err("Invalid settings".into());
            }
            let bytes: Vec<u8> = (0..hex.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&hex[i..i + 2], 16))
                .collect::<Result<_, _>>()
                .map_err(|_| "Invalid settings")?;
            #[derive(Deserialize)]
            struct Install {
                settings: Settings,
                name: String,
            }
            let install: Install =
                serde_json::from_slice(&bytes).map_err(|_| "Invalid settings")?;
            if !install.settings.valid() || install.name.len() > 200 {
                return Err("Invalid settings".into());
            }
            write_private(
                &settings_path(),
                &serde_json::to_vec(&install.settings).map_err(|_| "Invalid settings")?,
            )?;
            std::fs::write(
                public_status_path(),
                serde_json::to_vec(&PublicStatus {
                    connection_id: install.settings.connection_id,
                    name: install.name,
                })
                .map_err(|_| "Invalid settings")?,
            )
            .map_err(|_| "Cannot write status")?;
            // The SYSTEM service runs a copy in Program Files, which normal users cannot
            // change, never the app in the user's own folder.
            let _ = sc(&["stop", SERVICE_NAME]);
            std::thread::sleep(Duration::from_secs(2));
            let exe = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
            std::fs::create_dir_all(program_folder())
                .map_err(|_| "Cannot create the sign-in screen program folder")?;
            if exe != program_path() {
                std::fs::copy(&exe, program_path())
                    .map_err(|_| "Cannot install the sign-in screen program")?;
            }
            let bin = format!("\"{}\" --signin-service", program_path().display());
            if !sc(&[
                "create",
                SERVICE_NAME,
                "binPath=",
                &bin,
                "start=",
                "auto",
                "obj=",
                "LocalSystem",
                "DisplayName=",
                SERVICE_DISPLAY,
            ]) && !sc(&[
                "config",
                SERVICE_NAME,
                "binPath=",
                &bin,
                "start=",
                "auto",
                "obj=",
                "LocalSystem",
                "DisplayName=",
                SERVICE_DISPLAY,
            ]) {
                return Err("Cannot install the sign-in screen service".into());
            }
            allow_service_sas();
            let _ = sc(&[
                "description",
                SERVICE_NAME,
                "Lets approved Mixel Remote technicians reach this computer at the Windows sign-in screen and send Ctrl+Alt+Del.",
            ]);
            let _ = sc(&[
                "failure",
                SERVICE_NAME,
                "reset=",
                "86400",
                "actions=",
                "restart/5000/restart/5000/restart/60000",
            ]);
            if sc(&["start", SERVICE_NAME]) {
                Ok(())
            } else {
                Err("Cannot start the sign-in screen service".into())
            }
        }
        "--signin-update" => {
            if !settings_path().is_file() {
                return Err("Sign-in screen access is not turned on".into());
            }
            let _ = sc(&["stop", SERVICE_NAME]);
            std::thread::sleep(Duration::from_secs(3));
            let exe = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
            std::fs::copy(&exe, program_path())
                .map_err(|_| "Cannot update the sign-in screen program")?;
            if sc(&["start", SERVICE_NAME]) {
                Ok(())
            } else {
                Err("Cannot start the sign-in screen service".into())
            }
        }
        "--signin-remove" => {
            let _ = sc(&["stop", SERVICE_NAME]);
            std::thread::sleep(Duration::from_secs(2));
            let _ = sc(&["delete", SERVICE_NAME]);
            let _ = std::fs::remove_dir_all(program_folder());
            restore_service_sas();
            // The computer-wide key stays, so turning access on again reuses its enrolment.
            for path in [settings_path(), public_status_path()] {
                let _ = std::fs::remove_file(path);
            }
            Ok(())
        }
        _ => Err("Unknown sign-in screen command".into()),
    }
}

// ---------------- Ctrl+Alt+Del ----------------
static SIGNIN_HOST: AtomicBool = AtomicBool::new(false);

fn call_sas() -> Result<(), String> {
    use windows::Win32::System::LibraryLoader::{GetProcAddress, LoadLibraryW};
    // SAFETY: sas.dll exports `VOID SendSAS(BOOL AsUser)`; the pointer is called with that ABI.
    unsafe {
        let library = LoadLibraryW(windows::core::w!("sas.dll")).map_err(|_| "Ctrl+Alt+Del is not available on this computer")?;
        let function = GetProcAddress(library, windows::core::s!("SendSAS")).ok_or("Ctrl+Alt+Del is not available on this computer")?;
        let send: unsafe extern "system" fn(i32) = std::mem::transmute(function);
        send(0);
    }
    Ok(())
}
/// True in the headless sign-in screen host process.
pub fn is_signin_host() -> bool {
    SIGNIN_HOST.load(Ordering::Acquire)
}
/// Whether Ctrl+Alt+Del can be sent: in the sign-in screen host, or through the helper service.
pub fn can_send_sas() -> bool {
    // Only the SYSTEM host can also operate the security screen that Ctrl+Alt+Del opens.
    SIGNIN_HOST.load(Ordering::Acquire)
}
const HELPER_PIPE: &str = r"\\.\pipe\MixelRemoteHelper";

/// Sends Ctrl+Alt+Del (the secure attention sequence) on this computer. Windows accepts it
/// only from the service process, so the SYSTEM host asks the service over a pipe that only
/// SYSTEM may open.
pub fn send_sas() -> Result<(), String> {
    use std::io::Write;
    if !SIGNIN_HOST.load(Ordering::Acquire) {
        return Err("Ctrl+Alt+Del needs always-on access for this computer".into());
    }
    let mut last = None;
    for _ in 0..20 {
        match std::fs::OpenOptions::new().write(true).open(HELPER_PIPE) {
            Ok(mut pipe) => return pipe.write_all(b"sas\n").map_err(|_| "Ctrl+Alt+Del could not be sent".into()),
            Err(error) => last = Some(error),
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    log(&format!("Ctrl+Alt+Del: helper unavailable ({last:?})"));
    Err("Ctrl+Alt+Del could not be sent".into())
}

/// Service thread: sends Ctrl+Alt+Del when the SYSTEM host asks (pipe open to SYSTEM only).
fn helper_pipe() {
    use tokio::io::AsyncReadExt;
    use windows::Win32::Security::{
        Authorization::{ConvertStringSecurityDescriptorToSecurityDescriptorW, SDDL_REVISION_1},
        PSECURITY_DESCRIPTOR, SECURITY_ATTRIBUTES,
    };
    let Ok(runtime) = tokio::runtime::Builder::new_current_thread().enable_all().build() else {
        log("helper pipe: no runtime");
        return;
    };
    let mut descriptor = PSECURITY_DESCRIPTOR::default();
    let sddl = wide("D:P(A;;GA;;;SY)");
    // SAFETY: sddl is a live null-terminated buffer; the descriptor lives for the thread.
    if unsafe { ConvertStringSecurityDescriptorToSecurityDescriptorW(PCWSTR(sddl.as_ptr()), SDDL_REVISION_1, &mut descriptor, None) }.is_err() {
        log("helper pipe: no security descriptor");
        return;
    }
    let mut attributes = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor.0,
        bInheritHandle: false.into(),
    };
    runtime.block_on(async move {
        let mut first = true;
        while !STOP.load(Ordering::Acquire) {
            let mut options = tokio::net::windows::named_pipe::ServerOptions::new();
            options.first_pipe_instance(first).access_outbound(false);
            // SAFETY: attributes points at a valid SECURITY_ATTRIBUTES for the whole loop.
            let server = unsafe { options.create_with_security_attributes_raw(HELPER_PIPE, (&raw mut attributes).cast()) };
            let Ok(mut server) = server else {
                log("helper pipe: cannot create");
                tokio::time::sleep(Duration::from_secs(5)).await;
                continue;
            };
            first = false;
            // Wait as long as needed: the pipe stays available between requests.
            if server.connect().await.is_err() {
                continue;
            }
            let mut buffer = [0u8; 16];
            let read = tokio::time::timeout(Duration::from_secs(2), server.read(&mut buffer)).await;
            if let Ok(Ok(count)) = read
                && &buffer[..count] == b"sas\n"
            {
                match call_sas() {
                    Ok(()) => log("Ctrl+Alt+Del sent"),
                    Err(error) => log(&error),
                }
            }
        }
    });
}

/// Notice for the signed-in user while a technician is connected through this host
/// (read by the Mixel Remote app, which shows a banner).
fn set_session_notice(active: bool) {
    let path = folder().join("session-active.json");
    if active {
        let seconds = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |time| time.as_secs());
        let _ = std::fs::write(path, format!("{{\"since\":{seconds}}}"));
    } else {
        let _ = std::fs::remove_file(path);
    }
}
/// For the app: whether a technician is connected through the unattended host now.
pub fn technician_connected() -> bool {
    folder().join("session-active.json").is_file()
}

/// Elevated: allow services to send Ctrl+Alt+Del (policy SoftwareSASGeneration = 1) if unset.
fn allow_service_sas() {
    let key = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System";
    let present = std::process::Command::new("reg")
        .args(["query", key, "/v", "SoftwareSASGeneration"])
        .output()
        .is_ok_and(|output| output.status.success());
    if !present
        && std::process::Command::new("reg")
            .args(["add", key, "/v", "SoftwareSASGeneration", "/t", "REG_DWORD", "/d", "1", "/f"])
            .output()
            .is_ok_and(|output| output.status.success())
    {
        let _ = std::fs::write(folder().join("sas-policy-set"), b"1");
    }
}
fn restore_service_sas() {
    if std::fs::remove_file(folder().join("sas-policy-set")).is_ok() {
        let _ = std::process::Command::new("reg")
            .args(["delete", r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System", "/v", "SoftwareSASGeneration", "/f"])
            .output();
    }
}

// ---------------- service ----------------
static STOP: AtomicBool = AtomicBool::new(false);
static STATUS: AtomicIsize = AtomicIsize::new(0);

fn report(state: SERVICE_STATUS_CURRENT_STATE) {
    let handle = SERVICE_STATUS_HANDLE(STATUS.load(Ordering::Acquire) as *mut c_void);
    let status = SERVICE_STATUS {
        dwServiceType: SERVICE_WIN32_OWN_PROCESS,
        dwCurrentState: state,
        dwControlsAccepted: if state == SERVICE_RUNNING {
            SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN
        } else {
            0
        },
        dwWin32ExitCode: NO_ERROR.0,
        dwWaitHint: 5000,
        ..Default::default()
    };
    // SAFETY: the handle came from RegisterServiceCtrlHandlerExW for this service.
    unsafe {
        let _ = SetServiceStatus(handle, &status);
    }
}
unsafe extern "system" fn control(
    code: u32,
    _kind: u32,
    _data: *mut c_void,
    _context: *mut c_void,
) -> u32 {
    if code == SERVICE_CONTROL_STOP || code == SERVICE_CONTROL_SHUTDOWN {
        STOP.store(true, Ordering::Release);
        report(SERVICE_STOP_PENDING);
    } else if code != SERVICE_CONTROL_INTERROGATE {
        return 120; // ERROR_CALL_NOT_IMPLEMENTED
    }
    NO_ERROR.0
}
unsafe extern "system" fn service_main(_argc: u32, _argv: *mut PWSTR) {
    let name = wide(SERVICE_NAME);
    // SAFETY: name is a live null-terminated buffer; `control` matches the handler signature.
    let Ok(handle) =
        (unsafe { RegisterServiceCtrlHandlerExW(PCWSTR(name.as_ptr()), Some(control), None) })
    else {
        return;
    };
    STATUS.store(handle.0 as isize, Ordering::Release);
    report(SERVICE_RUNNING);
    log("service started");
    std::thread::spawn(helper_pipe);
    supervise();
    log("service stopped");
    report(SERVICE_STOPPED);
}
pub fn run_service() -> Result<(), String> {
    let mut name = wide(SERVICE_NAME);
    let table = [
        SERVICE_TABLE_ENTRYW {
            lpServiceName: PWSTR(name.as_mut_ptr()),
            lpServiceProc: Some(service_main),
        },
        SERVICE_TABLE_ENTRYW::default(),
    ];
    // SAFETY: the table is null-terminated and outlives the dispatcher call.
    unsafe { StartServiceCtrlDispatcherW(table.as_ptr()) }
        .map_err(|_| "Run this as the Windows service".into())
}

fn signed_in_user(session: u32) -> Option<String> {
    let mut buffer = PWSTR::null();
    let mut bytes = 0u32;
    // SAFETY: Windows allocates the buffer; it is freed with WTSFreeMemory below.
    unsafe {
        WTSQuerySessionInformationW(
            Some(WTS_CURRENT_SERVER_HANDLE),
            session,
            WTSUserName,
            &mut buffer,
            &mut bytes,
        )
        .ok()?;
        let name = buffer.to_string().unwrap_or_default();
        WTSFreeMemory(buffer.0.cast());
        Some(name)
    }
}

fn spawn_host(session: u32) -> Result<HANDLE, String> {
    let exe = std::env::current_exe().map_err(|_| "Cannot locate Mixel Remote")?;
    let mut command: Vec<u16> = wide(&format!("\"{}\" --signin-host", exe.display()));
    let mut desktop = wide(r"winsta0\Winlogon");
    // SAFETY: all handles are owned here and closed on every path; buffers live for the call.
    unsafe {
        let mut token = HANDLE::default();
        OpenProcessToken(
            GetCurrentProcess(),
            TOKEN_DUPLICATE
                | TOKEN_QUERY
                | TOKEN_ASSIGN_PRIMARY
                | TOKEN_ADJUST_SESSIONID
                | TOKEN_ADJUST_DEFAULT,
            &mut token,
        )
        .map_err(|_| "Cannot open the service token")?;
        let mut primary = HANDLE::default();
        let duplicated = DuplicateTokenEx(
            token,
            TOKEN_ACCESS_MASK(0x0200_0000),
            None,
            SecurityImpersonation,
            TokenPrimary,
            &mut primary,
        );
        let _ = CloseHandle(token);
        duplicated.map_err(|_| "Cannot prepare the host token")?;
        let session_value = session;
        if SetTokenInformation(
            primary,
            TokenSessionId,
            (&raw const session_value).cast(),
            size_of::<u32>() as u32,
        )
        .is_err()
        {
            let _ = CloseHandle(primary);
            return Err("Cannot move the host to the sign-in screen".into());
        }
        let startup = STARTUPINFOW {
            cb: size_of::<STARTUPINFOW>() as u32,
            lpDesktop: PWSTR(desktop.as_mut_ptr()),
            ..Default::default()
        };
        let mut process = PROCESS_INFORMATION::default();
        let created = CreateProcessAsUserW(
            Some(primary),
            PCWSTR::null(),
            Some(PWSTR(command.as_mut_ptr())),
            None,
            None,
            false,
            CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
            None,
            PCWSTR::null(),
            &startup,
            &mut process,
        );
        let _ = CloseHandle(primary);
        created.map_err(|_| "Cannot start the sign-in screen host")?;
        let _ = CloseHandle(process.hThread);
        Ok(process.hProcess)
    }
}

fn running(process: HANDLE) -> bool {
    // SAFETY: process is a live handle owned by the supervisor.
    unsafe { WaitForSingleObject(process, 0) != WAIT_OBJECT_0 }
}
fn stop_host(process: HANDLE) {
    // SAFETY: process is a live handle owned by the supervisor and closed here.
    unsafe {
        if running(process) {
            let _ = TerminateProcess(process, 0);
            let _ = WaitForSingleObject(process, 5000);
        }
        let _ = CloseHandle(process);
    }
}

fn supervise() {
    let mut host: Option<(HANDLE, u32)> = None;
    let mut last = String::new();
    while !STOP.load(Ordering::Acquire) {
        // SAFETY: no arguments; returns 0xFFFFFFFF when no console session is attached.
        let session = unsafe { WTSGetActiveConsoleSessionId() };
        let user = if session == u32::MAX {
            None
        } else {
            signed_in_user(session)
        };
        // Always reachable: the host follows the active console session (sign-in screen,
        // desktop, lock screen and security prompts alike).
        let wanted = session != u32::MAX && settings_path().is_file();
        let state = format!(
            "session {session} user {} wanted {wanted}",
            if user.as_deref().is_some_and(|name| !name.is_empty()) {
                "present"
            } else {
                "none"
            }
        );
        if state != last {
            log(&state);
            last = state;
        }
        if let Some((process, at)) = host
            && (!wanted || at != session || !running(process))
        {
            stop_host(process);
            host = None;
            log("host stopped");
        }
        if wanted && host.is_none() {
            match spawn_host(session) {
                Ok(process) => {
                    host = Some((process, session));
                    log("host started on the sign-in screen");
                }
                Err(error) => log(&error),
            }
        }
        std::thread::sleep(Duration::from_secs(2));
    }
    if let Some((process, _)) = host {
        stop_host(process);
    }
}

/// The always-on host keeps itself current: when no session is open and a newer signed
/// version is published, it installs it and replaces the service program.
async fn update_self() {
    if cfg!(feature = "qa-session") && std::env::var_os("MIXEL_UPDATE_CHANNEL").is_none() {
        return;
    }
    // The signed program file itself (not the installer) is published as windows/x64-program.
    let Ok(manifest) = crate::self_update::latest_for("windows", "x64-program").await else { return };
    let newer = matches!(
        (crate::version_parts(crate::agent::APP_VERSION), crate::version_parts(&manifest.version)),
        (Some(current), Some(next)) if next > current
    );
    if !newer {
        return;
    }
    log(&format!("updating always-on access {} -> {}", crate::agent::APP_VERSION, manifest.version));
    let installer = match crate::self_update::fetch_verified(&manifest).await {
        Ok(path) => path,
        Err(error) => {
            log(&format!("update not installed: {error}"));
            return;
        }
    };
    use std::os::windows::process::CommandExt;
    // Detached helper (survives the service stop): swap the service program, start it again.
    let script = "sc.exe stop MixelRemoteSignIn | Out-Null; Start-Sleep 5; \
        Copy-Item -LiteralPath $env:MIXEL_UPDATE_FILE -Destination $env:MIXEL_UPDATE_DEST -Force; \
        if($?){ Add-Content -LiteralPath $env:MIXEL_UPDATE_LOG 'always-on program replaced' }; sc.exe start MixelRemoteSignIn | Out-Null";
    let spawned = std::process::Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script])
        .env("MIXEL_UPDATE_FILE", &installer)
        .env("MIXEL_UPDATE_DEST", program_path())
        .env("MIXEL_UPDATE_LOG", folder().join("signin-host.log"))
        .creation_flags(0x0800_0000 | 0x0000_0200)
        .spawn();
    if spawned.is_err() {
        log("update helper could not start");
    }
}

// ---------------- host ----------------
pub fn run_host() -> Result<(), String> {
    SIGNIN_HOST.store(true, Ordering::Release);
    let bytes = std::fs::read(settings_path())
        .map_err(|_| "The sign-in screen settings are not readable")?;
    let settings: Settings =
        serde_json::from_slice(&bytes).map_err(|_| "The sign-in screen settings are invalid")?;
    if !settings.valid() {
        return Err("The sign-in screen settings are invalid".into());
    }
    let identity = Arc::new(IdentityState::machine(false)?);
    let enrolled = EnrolledPrincipal {
        principal: Principal {
            user: settings.profile,
            tenant: Some(settings.tenant),
            staff: false,
            expires_at: 0,
        },
        device: settings.device,
        connection_id: settings.connection_id,
        generation: settings.generation,
        public_key: identity.public_key()?,
        identity_ticket: None,
    };
    native_os_capture::windows_capture::attach_input_desktop();
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .map_err(|_| "Cannot start the host runtime")?;
    runtime.block_on(async move {
        let remote = crate::remote_session::RemoteSession::default();
        let mut backoff = 5u64;
        loop {
            match remote
                .connect_startup(enrolled.clone(), 0, settings.control, identity.clone())
                .await
            {
                Ok(()) => {
                    log("sign-in screen host connecting");
                    let mut phase = String::new();
                    let mut next_update: Option<std::time::Instant> = None;
                    loop {
                        tokio::time::sleep(Duration::from_secs(5)).await;
                        let status = remote.status().await;
                        if status.phase != phase {
                            log(&format!("host {}: {}", status.phase, status.message));
                            set_session_notice(status.phase == "active");
                            phase.clone_from(&status.phase);
                        }
                        if matches!(status.phase.as_str(), "offline" | "error") {
                            set_session_notice(false);
                            break;
                        }
                        // Idle: look for a newer version two minutes after start, then every six hours.
                        if status.phase == "ready" && next_update.is_none_or(|at: std::time::Instant| std::time::Instant::now() >= at) {
                            let first = next_update.is_none();
                            next_update = Some(std::time::Instant::now() + Duration::from_secs(if first { 120 } else { 6 * 3600 }));
                            if !first {
                                update_self().await;
                            }
                        }
                    }
                }
                Err(error) => log(&format!("host registration failed: {error}")),
            }
            tokio::time::sleep(Duration::from_secs(backoff)).await;
            backoff = (backoff * 2).min(60);
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn settings() -> Settings {
        Settings {
            version: 1,
            device: Uuid::from_u128(1),
            profile: Uuid::from_u128(2),
            tenant: Uuid::from_u128(3),
            connection_id: 485_617_081,
            generation: 1,
            control: true,
        }
    }
    #[test]
    fn settings_reject_missing_ids_and_out_of_range_values() {
        assert!(settings().valid());
        for broken in [
            Settings {
                version: 2,
                ..settings()
            },
            Settings {
                device: Uuid::nil(),
                ..settings()
            },
            Settings {
                tenant: Uuid::nil(),
                ..settings()
            },
            Settings {
                connection_id: 99_999_999,
                ..settings()
            },
            Settings {
                generation: 0,
                ..settings()
            },
        ] {
            assert!(!broken.valid());
        }
        let text = r#"{"version":1,"device":"00000000-0000-0000-0000-000000000001","profile":"00000000-0000-0000-0000-000000000002","tenant":"00000000-0000-0000-0000-000000000003","connection_id":485617081,"generation":1,"control":true,"extra":1}"#;
        assert!(serde_json::from_str::<Settings>(text).is_err());
    }
    #[test]
    fn service_program_lives_in_program_files() {
        assert!(program_path().starts_with(program_folder()));
        assert!(
            program_folder()
                .to_string_lossy()
                .to_lowercase()
                .contains("program files")
        );
    }
}
