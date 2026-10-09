// Mixel attended support guard. Runtime only: never change saved login preferences.
static MIXEL_SUPPORT_INVITE_UNTIL: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);
static MIXEL_SUPPORT_INVITE_STARTED: std::sync::OnceLock<std::time::Instant> =
    std::sync::OnceLock::new();

pub const SUPPORT_INVITE_ATTESTATION: &str = "attended-runtime-v2";

// The foreground support UI owns this kernel lease. Service IPC heartbeat
// handlers only renew the memory deadline; they must never own this lease.
// A restarted service discovers a live owner before authorizing any session.
mod mixel_support_lease {
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    };

    static OWNER: Mutex<Option<Lease>> = Mutex::new(None);
    static OWNER_FAILED: AtomicBool = AtomicBool::new(false);

    pub(super) fn hold() -> bool {
        let mut owner = OWNER.lock().unwrap_or_else(|error| error.into_inner());
        if let Some(lease) = owner.as_ref() {
            if current(lease).unwrap_or(false) {
                OWNER_FAILED.store(false, Ordering::SeqCst);
                return true;
            }
            // Repair a lease path removed/replaced by temporary-file cleanup.
            *owner = None;
        }
        match create() {
            Ok(lease) => {
                *owner = Some(lease);
                OWNER_FAILED.store(false, Ordering::SeqCst);
                true
            }
            Err(_) => {
                OWNER_FAILED.store(true, Ordering::SeqCst);
                false
            }
        }
    }

    pub(super) fn owner_failed() -> bool {
        OWNER_FAILED.load(Ordering::SeqCst)
    }

    pub(super) fn active() -> bool {
        // Missing kernel owner restores the saved behavior. An endpoint which
        // exists but cannot be safely inspected must never permit auto-login.
        owner_failed() || probe().unwrap_or(true)
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    type Lease = std::fs::File;

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    const PREFIX: &str = "mixel-remote-attended-v2-";
    #[cfg(target_os = "macos")]
    const ROOT: &str = "/private/tmp";
    #[cfg(target_os = "linux")]
    const ROOT: &str = "/tmp";

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    extern "C" {
        fn geteuid() -> u32;
        fn flock(fd: i32, operation: i32) -> i32;
        fn openat(fd: i32, path: *const std::ffi::c_char, flags: i32, ...) -> i32;
    }
    #[cfg(target_os = "macos")]
    const NOFOLLOW: i32 = 0x100;
    #[cfg(target_os = "linux")]
    const NOFOLLOW: i32 = 0x20000;
    #[cfg(target_os = "macos")]
    const DIRECTORY: i32 = 0x100000;
    #[cfg(target_os = "linux")]
    const DIRECTORY: i32 = 0x10000;
    #[cfg(target_os = "macos")]
    const CLOEXEC: i32 = 0x1000000;
    #[cfg(target_os = "linux")]
    const CLOEXEC: i32 = 0x80000;
    #[cfg(target_os = "macos")]
    const CREATE: i32 = 0x200;
    #[cfg(target_os = "linux")]
    const CREATE: i32 = 0x40;

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    fn valid(file: &std::fs::File, uid: u32, directory: bool) -> std::io::Result<()> {
        use std::os::unix::fs::MetadataExt;
        let metadata = file.metadata()?;
        if metadata.uid() != uid
            || metadata.mode() & 0o7777 != if directory { 0o700 } else { 0o600 }
            || if directory {
                !metadata.is_dir()
            } else {
                !metadata.is_file() || metadata.nlink() != 1
            }
        {
            return Err(std::io::Error::new(
                std::io::ErrorKind::PermissionDenied,
                "Unsafe attended lease endpoint",
            ));
        }
        Ok(())
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    fn directory(path: &std::path::Path, uid: u32) -> std::io::Result<std::fs::File> {
        use std::os::unix::fs::OpenOptionsExt;
        let file = std::fs::OpenOptions::new()
            .read(true)
            .custom_flags(NOFOLLOW | DIRECTORY | CLOEXEC)
            .open(path)?;
        valid(&file, uid, true)?;
        Ok(file)
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    fn file(directory: &std::fs::File, uid: u32, create: bool) -> std::io::Result<std::fs::File> {
        use std::os::unix::io::{AsRawFd, FromRawFd};
        // openat pins the validated directory inode. Never follow a symlink,
        // accept a hard link, or traverse a replacement owner directory.
        let flags = NOFOLLOW | CLOEXEC | if create { CREATE | 2 } else { 0 };
        let fd = unsafe {
            openat(
                directory.as_raw_fd(),
                b"lease\0".as_ptr().cast(),
                flags,
                0o600u32,
            )
        };
        if fd < 0 {
            return Err(std::io::Error::last_os_error());
        }
        let file = unsafe { std::fs::File::from_raw_fd(fd) };
        valid(&file, uid, false)?;
        Ok(file)
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    fn create() -> std::io::Result<Lease> {
        use std::os::unix::{fs::DirBuilderExt, io::AsRawFd};
        let uid = unsafe { geteuid() };
        let path = std::path::Path::new(ROOT).join(format!("{PREFIX}{uid}"));
        match std::fs::DirBuilder::new().mode(0o700).create(&path) {
            Ok(()) => (),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(error) => return Err(error),
        }
        let directory = directory(&path, uid)?;
        let lease = file(&directory, uid, true)?;
        if unsafe { flock(lease.as_raw_fd(), 1 | 4) } != 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(lease)
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    fn current(lease: &Lease) -> std::io::Result<bool> {
        use std::os::unix::fs::MetadataExt;
        let uid = unsafe { geteuid() };
        valid(lease, uid, false)?;
        let path = std::path::Path::new(ROOT).join(format!("{PREFIX}{uid}"));
        let directory = directory(&path, uid)?;
        let actual = file(&directory, uid, false)?;
        let held = lease.metadata()?;
        let actual = actual.metadata()?;
        if held.dev() != actual.dev() || held.ino() != actual.ino() {
            return Ok(false);
        }
        // Renew filesystem activity so ordinary /tmp cleanup leaves the live
        // endpoint alone. No preference data or durable access choice is stored.
        lease.set_len(0)?;
        Ok(true)
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    fn probe() -> std::io::Result<bool> {
        use std::os::unix::{ffi::OsStrExt, io::AsRawFd};
        for entry in std::fs::read_dir(ROOT)? {
            let entry = entry?;
            let name = entry.file_name();
            if !name.as_bytes().starts_with(PREFIX.as_bytes()) {
                continue;
            }
            let uid = name
                .to_str()
                .and_then(|name| name.strip_prefix(PREFIX))
                .and_then(|uid| uid.parse::<u32>().ok())
                .ok_or_else(|| {
                    std::io::Error::new(std::io::ErrorKind::InvalidData, "Malformed attended owner")
                })?;
            // A portable user process can only serve its own desktop. The
            // installed root service can serve and inspect every logged-in UID.
            let current_uid = unsafe { geteuid() };
            if current_uid != 0 && uid != current_uid {
                continue;
            }
            let directory = directory(&entry.path(), uid)?;
            let lease = file(&directory, uid, false)?;
            if unsafe { flock(lease.as_raw_fd(), 2 | 4) } != 0 {
                let error = std::io::Error::last_os_error();
                if error.kind() == std::io::ErrorKind::WouldBlock {
                    return Ok(true);
                }
                return Err(error);
            }
            // The temporary exclusive probe lock is released on File drop.
        }
        Ok(false)
    }

    #[cfg(windows)]
    struct Lease(usize);
    #[cfg(windows)]
    impl Drop for Lease {
        fn drop(&mut self) {
            unsafe { CloseHandle(self.0 as _) };
        }
    }
    #[cfg(windows)]
    const EVENT: &str = "Global\\Mixel-Remote-Attended-Runtime-v2";
    #[cfg(windows)]
    #[repr(C)]
    struct SecurityAttributes {
        length: u32,
        descriptor: *mut std::ffi::c_void,
        inherit: i32,
    }
    #[cfg(windows)]
    #[link(name = "kernel32")]
    extern "system" {
        fn CreateEventExW(
            attributes: *const SecurityAttributes,
            name: *const u16,
            flags: u32,
            access: u32,
        ) -> *mut std::ffi::c_void;
        fn OpenEventW(access: u32, inherit: i32, name: *const u16) -> *mut std::ffi::c_void;
        fn CloseHandle(handle: *mut std::ffi::c_void) -> i32;
        fn GetLastError() -> u32;
        fn LocalFree(memory: *mut std::ffi::c_void) -> *mut std::ffi::c_void;
    }
    #[cfg(windows)]
    #[link(name = "advapi32")]
    extern "system" {
        fn ConvertStringSecurityDescriptorToSecurityDescriptorW(
            value: *const u16,
            revision: u32,
            descriptor: *mut *mut std::ffi::c_void,
            size: *mut u32,
        ) -> i32;
    }
    #[cfg(windows)]
    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(Some(0)).collect()
    }

    #[cfg(windows)]
    fn create() -> std::io::Result<Lease> {
        // Every interactive owner can hold SYNCHRONIZE access; only SYSTEM and
        // administrators can mutate its ACL. Event state is irrelevant: its
        // kernel object's existence lasts until the last process handle exits.
        let sddl = wide("D:P(A;;0x00100000;;;WD)(A;;GA;;;SY)(A;;GA;;;BA)");
        let mut descriptor = std::ptr::null_mut();
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl.as_ptr(),
                1,
                &mut descriptor,
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(std::io::Error::last_os_error());
        }
        let attributes = SecurityAttributes {
            length: std::mem::size_of::<SecurityAttributes>() as u32,
            descriptor,
            inherit: 0,
        };
        let handle = unsafe { CreateEventExW(&attributes, wide(EVENT).as_ptr(), 0, 0x00100000) };
        let error = if handle.is_null() {
            Some(std::io::Error::last_os_error())
        } else {
            None
        };
        unsafe {
            LocalFree(descriptor);
        }
        if let Some(error) = error {
            return Err(error);
        }
        Ok(Lease(handle as usize))
    }

    #[cfg(windows)]
    fn current(_lease: &Lease) -> std::io::Result<bool> {
        Ok(true)
    }

    #[cfg(windows)]
    fn probe() -> std::io::Result<bool> {
        let handle = unsafe { OpenEventW(0x00100000, 0, wide(EVENT).as_ptr()) };
        if handle.is_null() {
            return if unsafe { GetLastError() } == 2 {
                Ok(false)
            } else {
                Err(std::io::Error::last_os_error())
            };
        }
        unsafe {
            CloseHandle(handle);
        }
        Ok(true)
    }

    #[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
    type Lease = ();
    #[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
    fn create() -> std::io::Result<Lease> {
        Ok(())
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
    fn current(_lease: &Lease) -> std::io::Result<bool> {
        Ok(true)
    }

    #[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
    fn probe() -> std::io::Result<bool> {
        Ok(false)
    }
}

pub fn hold_support_invite_attended_lease() -> bool {
    renew_support_invite_attended();
    mixel_support_lease::hold()
}

pub fn support_invite_owner_lease_failed() -> bool {
    mixel_support_lease::owner_failed()
}

pub fn support_invite_guard_is_confirmed(value: &str) -> bool {
    value == SUPPORT_INVITE_ATTESTATION
}

pub fn resolve_support_invite_attestation(
    ipc_reachable: bool,
    remote_proof: Option<&str>,
) -> &'static str {
    if !ipc_reachable {
        return "guard-unavailable";
    }
    match remote_proof {
        Some(SUPPORT_INVITE_ATTESTATION) => SUPPORT_INVITE_ATTESTATION,
        Some("") => "guard-unavailable",
        _ => "service-update-required",
    }
}

pub fn is_support_invite_arg(value: &str) -> bool {
    if value == "--support-invite" {
        return true;
    }
    let Some((scheme, remainder)) = value.split_once("://") else {
        return false;
    };
    let Some((authority_path, _query)) = remainder.split_once('?') else {
        return false;
    };
    scheme.eq_ignore_ascii_case("mixel-remote")
        && (authority_path.eq_ignore_ascii_case("support")
            || authority_path.eq_ignore_ascii_case("support/"))
}

pub fn is_mixel_store_package_path(value: &str) -> bool {
    value
        .split(['\\', '/'])
        .any(|component| component.eq_ignore_ascii_case("WindowsApps"))
}

fn support_invite_now_ms() -> u64 {
    // Consent must not disappear early or last indefinitely after the customer
    // corrects their system clock. This deadline is local to this process only.
    MIXEL_SUPPORT_INVITE_STARTED
        .get_or_init(std::time::Instant::now)
        .elapsed()
        .as_millis()
        .min((u64::MAX - 1) as u128) as u64
        + 1
}

fn support_invite_guard_active(now: u64, until: u64) -> bool {
    now != 0 && now < until
}

pub fn renew_support_invite_attended() {
    MIXEL_SUPPORT_INVITE_UNTIL.fetch_max(
        support_invite_now_ms().saturating_add(90_000),
        std::sync::atomic::Ordering::SeqCst,
    );
}

pub fn support_invite_requires_click() -> bool {
    support_invite_guard_active(
        support_invite_now_ms(),
        MIXEL_SUPPORT_INVITE_UNTIL.load(std::sync::atomic::Ordering::SeqCst),
    ) || mixel_support_lease::active()
}

pub fn support_invite_must_wait(required: bool, accepted: bool) -> bool {
    required && !accepted
}

pub fn effective_support_approve_mode(saved: &str, attended: bool) -> String {
    if attended {
        "click".to_owned()
    } else {
        saved.to_owned()
    }
}

#[cfg(test)]
mod mixel_support_invite_tests {
    use super::*;

    #[test]
    fn guard_requires_current_explicit_support_handoff() {
        assert!(!support_invite_guard_active(1_000, 0));
        assert!(!support_invite_guard_active(0, 90_000));
        assert!(support_invite_guard_active(1_000, 91_000));
        assert!(!support_invite_guard_active(91_000, 91_000));
        assert!(!support_invite_guard_active(91_001, 91_000));
    }

    #[test]
    fn support_uri_is_an_incoming_support_launch_not_outbound_connection() {
        assert!(is_support_invite_arg(
            "mixel-remote://support?invite=synthetic&apikey=synthetic"
        ));
        assert!(is_support_invite_arg("--support-invite"));
        assert!(is_support_invite_arg(
            "mixel-remote://support/?invite=synthetic&apikey=synthetic"
        ));
        assert!(is_support_invite_arg(
            "MIXEL-REMOTE://SUPPORT/?invite=synthetic&apikey=synthetic"
        ));
        assert!(!is_support_invite_arg(
            "mixel-remote://support/other?invite=synthetic"
        ));
        assert!(!is_support_invite_arg("mixel-remote://123456"));
        assert!(!is_support_invite_arg(
            "mixel-remote://support.attacker.example?invite=synthetic"
        ));
        assert!(!is_support_invite_arg(
            "mixel-remote://support-something?invite=synthetic"
        ));
        assert!(!is_support_invite_arg("--connect"));
        assert!(!is_support_invite_arg(""));
    }

    #[test]
    fn store_package_path_disables_external_updates_without_changing_choices() {
        assert!(is_mixel_store_package_path(
            r"C:\Program Files\WindowsApps\Mixel_1.5.7_x64\Mixel-Remote.exe"
        ));
        assert!(is_mixel_store_package_path(
            r"C:\PROGRAM FILES\WINDOWSAPPS\Mixel\Mixel-Remote.exe"
        ));
        assert!(!is_mixel_store_package_path(
            r"C:\Program Files\Mixel-Remote\mixel-remote.exe"
        ));
        assert!(!is_mixel_store_package_path(
            r"C:\Users\Example\AppData\Local\mixel-remote\mixel-remote.exe"
        ));
        assert!(!is_mixel_store_package_path(
            r"C:\Users\Example\Downloads\WindowsApps-backup\mixel-remote.exe"
        ));
        assert!(!is_mixel_store_package_path(
            r"C:\Users\WindowsAppsUser\mixel-remote.exe"
        ));
        assert!(is_mixel_store_package_path(
            "C:/Program Files/WindowsApps/Mixel/Mixel-Remote.exe"
        ));
    }

    #[test]
    fn support_handoff_renews_attended_guard() {
        renew_support_invite_attended();
        assert!(support_invite_requires_click());
    }

    #[test]
    fn guard_clock_is_nonzero_monotonic_and_renewals_cannot_shorten_deadline() {
        let before = support_invite_now_ms();
        assert_ne!(before, 0);
        assert!(support_invite_now_ms() >= before);
        renew_support_invite_attended();
        let deadline = MIXEL_SUPPORT_INVITE_UNTIL.load(std::sync::atomic::Ordering::SeqCst);
        renew_support_invite_attended();
        assert!(MIXEL_SUPPORT_INVITE_UNTIL.load(std::sync::atomic::Ordering::SeqCst) >= deadline);
    }

    #[test]
    fn guard_renewal_is_safe_during_concurrent_service_reads() {
        let readers: Vec<_> = (0..8)
            .map(|_| {
                std::thread::spawn(|| {
                    for _ in 0..1_000 {
                        renew_support_invite_attended();
                        assert!(support_invite_requires_click());
                    }
                })
            })
            .collect();
        for reader in readers {
            reader.join().unwrap();
        }
    }

    #[test]
    fn password_recent_session_2fa_and_switch_cannot_replace_customer_accept() {
        // The attended gate deliberately takes no password/trusted-session/2FA
        // input. Only the customer's connection-manager Authorize event releases it.
        assert!(support_invite_must_wait(true, false));
        assert!(!support_invite_must_wait(true, true));
        assert!(!support_invite_must_wait(false, false));
        assert!(!support_invite_must_wait(false, true));
    }

    #[test]
    fn saved_password_mode_shows_customer_accept_during_attended_support() {
        let saved = "password";
        let effective = effective_support_approve_mode(saved, true);
        assert_eq!(effective, "click");
        assert_ne!(effective, "password"); // Existing CM predicate displays Accept.
        assert_eq!(saved, "password"); // No preference mutation.
        assert_eq!(effective_support_approve_mode(saved, false), "password");
        assert_eq!(effective_support_approve_mode("both", true), "click");
        assert_eq!(effective_support_approve_mode("both", false), "both");
    }

    #[test]
    fn legacy_generic_option_echo_cannot_confirm_runtime_guard() {
        assert!(!support_invite_guard_is_confirmed("Y"));
        assert!(!support_invite_guard_is_confirmed(""));
        assert!(!support_invite_guard_is_confirmed("click"));
        assert!(!support_invite_guard_is_confirmed("attended-runtime-v1"));
        assert!(support_invite_guard_is_confirmed("attended-runtime-v2"));
    }

    #[test]
    fn external_old_installed_or_portable_service_cannot_borrow_local_guard() {
        assert_eq!(
            resolve_support_invite_attestation(true, Some("attended-runtime-v1")),
            "service-update-required"
        );
        assert_eq!(
            resolve_support_invite_attestation(true, Some("Y")),
            "service-update-required"
        );
        assert_eq!(
            resolve_support_invite_attestation(true, None),
            "service-update-required"
        );
        assert_eq!(
            resolve_support_invite_attestation(false, None),
            "guard-unavailable"
        );
        assert_eq!(
            resolve_support_invite_attestation(false, Some(SUPPORT_INVITE_ATTESTATION)),
            "guard-unavailable"
        );
        assert_eq!(
            resolve_support_invite_attestation(true, Some(SUPPORT_INVITE_ATTESTATION)),
            SUPPORT_INVITE_ATTESTATION
        );
    }
}
