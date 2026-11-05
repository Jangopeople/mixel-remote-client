// Mac Flutter GUI launch ownership. No URI is Debug or written to disk.
use super::mixel_support_url::{is_valid_support_url, Handoff};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

pub(crate) const RETRY_BUDGET: Duration = Duration::from_secs(15);
pub(crate) const POLL: Duration = Duration::from_millis(100);

struct Intent { id: u64, url: String, offered: Instant }
// One latest validated support intent plus one coalesced ordinary open.
// An ordinary open must never erase a newer support intent.
pub(crate) struct Pending {
    next: u64,
    support: Option<Intent>,
    ordinary: Option<Intent>,
}
impl Pending {
    pub(crate) fn new() -> Self { Self { next: 0, support: None, ordinary: None } }
    pub(crate) fn offer(&mut self, url: String) -> bool {
        if !url.is_empty() && !is_valid_support_url(&url) { return false; }
        let Some(id) = self.next.checked_add(1) else { return false; };
        self.next = id;
        let intent = Intent { id, url, offered: Instant::now() };
        if intent.url.is_empty() { self.ordinary = Some(intent); }
        else { self.support = Some(intent); }
        true
    }
    pub(crate) fn current(&self) -> Option<(u64, String)> {
        self.support.as_ref().or(self.ordinary.as_ref()).map(|intent| (intent.id, intent.url.clone()))
    }
    pub(crate) fn complete(&mut self, id: u64, outcome: Handoff) {
        if !matches!(outcome, Handoff::Accepted | Handoff::Superseded) { return; }
        if self.support.as_ref().map(|intent| intent.id) == Some(id) {
            self.support = None;
            // A delivered support intent already raises the same GUI.
            self.ordinary = None;
        } else if self.ordinary.as_ref().map(|intent| intent.id) == Some(id) { self.ordinary = None; }
    }
    pub(crate) fn discard(&mut self, id: u64) {
        if self.support.as_ref().map(|intent|intent.id)==Some(id) { self.support=None; }
        if self.ordinary.as_ref().map(|intent|intent.id)==Some(id) { self.ordinary=None; }
    }
    pub(crate) fn expire(&mut self, id: u64, now: Instant) -> bool {
        let expired = |intent: &Intent| intent.id == id && now.saturating_duration_since(intent.offered) >= RETRY_BUDGET;
        if self.support.as_ref().map(expired).unwrap_or(false) { self.support = None; return true; }
        if self.ordinary.as_ref().map(expired).unwrap_or(false) { self.ordinary = None; return true; }
        false
    }
}

pub(crate) fn may_spawn(outcome: Handoff, child_present: bool) -> bool {
    outcome == Handoff::NoReceiver && !child_present
}

#[cfg(any(target_os = "macos", test))]
pub(crate) mod reservation {
    use std::fs::{File, OpenOptions};
    use std::io;
    use std::os::unix::{fs::{DirBuilderExt, MetadataExt, OpenOptionsExt}, io::{AsRawFd, FromRawFd}};
    use std::path::Path;
    // These APIs/flags are the existing attended lease boundary, in a separate
    // app-owned namespace. No endpoint removal, PID inference or lock-file unlink.
    extern "C" { fn geteuid() -> u32; fn flock(fd: i32, operation: i32) -> i32; fn openat(fd: i32, path: *const std::ffi::c_char, flags: i32, ...) -> i32; fn fcntl(fd: i32, command: i32, ...) -> i32; }
    pub(crate) const INHERITED_FD: &str = "MIXEL_MAC_GUI_RESERVATION_FD";
    pub(crate) const ATTENDED_START: &str = "MIXEL_MAC_GUI_ATTENDED_START";
    pub(crate) const BRIDGE_FD: &str = "MIXEL_MAC_GUI_STARTUP_BRIDGE_FD";
    #[cfg(target_os = "macos")] const NOFOLLOW: i32 = 0x100;
    #[cfg(target_os = "macos")] const DIRECTORY: i32 = 0x100000;
    #[cfg(target_os = "macos")] const CLOEXEC: i32 = 0x1000000;
    #[cfg(target_os = "macos")] const CREATE: i32 = 0x200;
    #[cfg(target_os = "linux")] const NOFOLLOW: i32 = 0x20000;
    #[cfg(target_os = "linux")] const DIRECTORY: i32 = 0x10000;
    #[cfg(target_os = "linux")] const CLOEXEC: i32 = 0x80000;
    #[cfg(target_os = "linux")] const CREATE: i32 = 0x40;
    fn unsafe_endpoint() -> io::Error { io::Error::new(io::ErrorKind::PermissionDenied, "Unsafe Mac GUI launch endpoint") }
    fn valid(file: &File, uid: u32, directory: bool) -> io::Result<()> {
        let metadata = file.metadata()?;
        if metadata.uid() != uid || metadata.mode() & 0o7777 != if directory { 0o700 } else { 0o600 }
            || if directory { !metadata.is_dir() } else { !metadata.is_file() || metadata.nlink() != 1 }
        { return Err(unsafe_endpoint()); }
        Ok(())
    }
    fn open(directory: &File, uid: u32, create: bool) -> io::Result<File> {
        let fd = unsafe { openat(directory.as_raw_fd(), b"launch\0".as_ptr().cast(), NOFOLLOW | CLOEXEC | if create { CREATE | 2 } else { 2 }, 0o600u32) };
        if fd < 0 { return Err(io::Error::last_os_error()); }
        let file = unsafe { File::from_raw_fd(fd) }; valid(&file, uid, false)?; Ok(file)
    }
    pub(crate) struct Reservation { file: File, directory: File, path: std::path::PathBuf, uid: u32 }
    impl Reservation {
        // `root` is fixed /private/tmp in production, explicitly owned in tests.
        pub(crate) fn acquire(root: &Path) -> io::Result<Self> {
            let uid = unsafe { geteuid() };
            let path = root.join(format!("mixel-remote-gui-launch-v1-{uid}"));
            match std::fs::DirBuilder::new().mode(0o700).create(&path) {
                Ok(()) => (), Err(error) if error.kind() == io::ErrorKind::AlreadyExists => (), Err(error) => return Err(error),
            }
            let directory = OpenOptions::new().read(true).custom_flags(NOFOLLOW | DIRECTORY | CLOEXEC).open(&path)?;
            valid(&directory, uid, true)?;
            let file = open(&directory, uid, true)?;
            if unsafe { flock(file.as_raw_fd(), 2 | 4) } != 0 { return Err(io::Error::last_os_error()); }
            let result = Self { file, directory, path, uid };
            if !result.current()? { return Err(unsafe_endpoint()); }
            Ok(result)
        }
        pub(crate) fn current(&self) -> io::Result<bool> {
            valid(&self.directory, self.uid, true)?; valid(&self.file, self.uid, false)?;
            let directory = OpenOptions::new().read(true).custom_flags(NOFOLLOW | DIRECTORY | CLOEXEC).open(&self.path)?;
            valid(&directory, self.uid, true)?;
            let held_directory = self.directory.metadata()?; let actual_directory = directory.metadata()?;
            if held_directory.dev() != actual_directory.dev() || held_directory.ino() != actual_directory.ino() { return Ok(false); }
            let current = open(&self.directory, self.uid, false)?;
            let expected = self.file.metadata()?; let current = current.metadata()?;
            Ok(expected.dev() == current.dev() && expected.ino() == current.ino())
        }
        pub(crate) fn adopt(root: &Path, fd: i32) -> io::Result<Self> {
            if fd < 3 || unsafe { fcntl(fd, 1) } < 0 { return Err(unsafe_endpoint()); }
            // Borrow first. A forged marker must never close a foreign fd.
            let borrowed = std::mem::ManuallyDrop::new(unsafe { File::from_raw_fd(fd) });
            let uid = unsafe { geteuid() };
            valid(&borrowed, uid, false)?;
            let path = root.join(format!("mixel-remote-gui-launch-v1-{uid}"));
            let directory = OpenOptions::new().read(true).custom_flags(NOFOLLOW | DIRECTORY | CLOEXEC).open(&path)?;
            valid(&directory, uid, true)?;
            let current = open(&directory, uid, false)?;
            let held = borrowed.metadata()?; let named = current.metadata()?;
            if held.dev() != named.dev() || held.ino() != named.ino() { return Err(unsafe_endpoint()); }
            if unsafe { flock(fd, 2 | 4) } != 0 { return Err(io::Error::last_os_error()); }
            if unsafe { fcntl(fd, 2, 1) } < 0 { return Err(io::Error::last_os_error()); }
            let result = Self { file: unsafe { File::from_raw_fd(fd) }, directory, path, uid };
            if !result.current()? { return Err(unsafe_endpoint()); }
            Ok(result)
        }
        pub(crate) fn spawn(&self, command: &mut std::process::Command, attended: bool) -> io::Result<std::process::Child> {
            use std::os::unix::process::CommandExt;
            if !self.current()? { return Err(unsafe_endpoint()); }
            let bridge = if attended { Some(hbb_common::password_security::acquire_support_invite_startup_bridge()?) } else { None };
            let bridge_fd = bridge.as_ref().map(|bridge| bridge.raw_fd());
            let fd = self.file.as_raw_fd();
            command.env(INHERITED_FD, fd.to_string());
            if let Some(bridge_fd) = bridge_fd { command.env(BRIDGE_FD, bridge_fd.to_string()); command.env(ATTENDED_START, "1"); }
            else { command.env_remove(BRIDGE_FD); command.env_remove(ATTENDED_START); }
            // Only the post-fork child changes this fd. No allocation, locks or
            // logging in pre_exec, and concurrent unrelated spawns keep CLOEXEC.
            unsafe { command.pre_exec(move || {
                if fcntl(fd, 2, 0) < 0 { return Err(io::Error::last_os_error()); }
                if let Some(bridge_fd) = bridge_fd { if fcntl(bridge_fd, 2, 0) < 0 { return Err(io::Error::last_os_error()); } }
                Ok(())
            }); }
            command.spawn()
        }
    }
    // File drop is the only release. This module never unlocks or removes a
    // reservation while an owned child has not been confirmed exited.
}

#[cfg(all(target_os = "macos", feature = "flutter"))]
mod runtime {
    use super::*;
    use reservation::Reservation;
    struct State { pending: Pending, worker: bool, child: Option<std::process::Child>, reservation: Option<Reservation>, child_intent: Option<u64> }
    fn global() -> &'static Mutex<State> {
        static STATE: OnceLock<Mutex<State>> = OnceLock::new();
        STATE.get_or_init(|| Mutex::new(State { pending: Pending::new(), worker: false, child: None, reservation: None, child_intent: None }))
    }
    fn fixed_status(status: &'static str) { hbb_common::log::info!("Mixel Mac GUI launch: {}", status); }
    fn send(url: String) -> Handoff { crate::ipc::send_mixel_gui_intent(url) }
    fn acquire() -> std::io::Result<Reservation> { Reservation::acquire(std::path::Path::new("/private/tmp")) }
    fn direct() -> &'static OnceLock<Reservation> { static DIRECT: OnceLock<Reservation> = OnceLock::new(); &DIRECT }
    fn direct_gate() -> &'static Mutex<()> { static GATE: Mutex<()> = Mutex::new(()); &GATE }
    pub(crate) fn adopt_inherited() -> Result<bool, ()> {
        // The environment marker and raw descriptor transfer are one atomic
        // operation; losing a concurrent OnceLock race must never close it.
        let _gate = direct_gate().lock().unwrap_or_else(|error| error.into_inner());
        let attended = std::env::var_os(reservation::ATTENDED_START);
        std::env::remove_var(reservation::ATTENDED_START);
        let marker = std::env::var_os(reservation::INHERITED_FD);
        std::env::remove_var(reservation::INHERITED_FD);
        let bridge_marker = std::env::var_os(reservation::BRIDGE_FD);
        std::env::remove_var(reservation::BRIDGE_FD);
        if attended.as_ref().map(|value| value != "1").unwrap_or(false)
            || (attended.is_some() && marker.is_none()) || (attended.is_some() != bridge_marker.is_some()) {
            fixed_status("inherited-attended-invalid"); return Err(());
        }
        let Some(marker) = marker else { return Ok(false); };
        if direct().get().is_some() { fixed_status("inherited-reservation-duplicate"); return Err(()); }
        let Some(text) = marker.to_str() else { fixed_status("inherited-reservation-invalid"); return Err(()); };
        if text.is_empty() || text.len() > 10 || !text.bytes().all(|value| value.is_ascii_digit()) {
            fixed_status("inherited-reservation-invalid"); return Err(());
        }
        let owned = text.parse::<i32>().ok().and_then(|fd| Reservation::adopt(std::path::Path::new("/private/tmp"), fd).ok());
        let Some(owned) = owned else { fixed_status("inherited-reservation-unsafe"); return Err(()); };
        // This exact exec-only marker transfers a raw fd with no Rust owner.
        // direct_gate takes/removes it once before the foreground owner exists.
        let bridge = if let Some(marker) = bridge_marker {
            let Some(text) = marker.to_str() else { fixed_status("inherited-bridge-invalid"); return Err(()); };
            if text.is_empty() || text.len() > 10 || !text.bytes().all(|value| value.is_ascii_digit()) { fixed_status("inherited-bridge-invalid"); return Err(()); }
            let Some(fd) = text.parse::<i32>().ok() else { fixed_status("inherited-bridge-invalid"); return Err(()); };
            match unsafe { hbb_common::password_security::adopt_support_invite_startup_bridge(fd) } {
                Ok(bridge) => Some(bridge), Err(_) => { fixed_status("inherited-bridge-unsafe"); return Err(()); }
            }
        } else { None };
        if attended.is_some() && !hbb_common::password_security::hold_support_invite_attended_lease() {
            fixed_status("inherited-attended-lease-unavailable"); return Err(());
        }
        // Foreground OWNER now overlaps and owns protection; release only bridge.
        drop(bridge);
        if direct().set(owned).is_err() { fixed_status("inherited-reservation-duplicate"); return Err(()); }
        Ok(true)
    }
    pub(crate) fn submit(url: String) {
        let mut state = global().lock().unwrap_or_else(|error| error.into_inner());
        if !state.pending.offer(url) { fixed_status("invalid-intent"); return; }
        if state.worker { return; }
        state.worker = true;
        drop(state);
        if std::thread::Builder::new().name("mixel-gui-launch".to_owned()).spawn(|| {
            if std::panic::catch_unwind(worker).is_err() {
                global().lock().unwrap_or_else(|error| error.into_inner()).worker = false;
                fixed_status("worker-failed-owned-resources-pending-retained");
            }
        }).is_err() {
            global().lock().unwrap_or_else(|error| error.into_inner()).worker = false;
            fixed_status("worker-unavailable-pending-retained");
        }
    }
    fn worker() {
        let mut bridge = None;
        let mut unknown_reported = false;
        loop {
            let mut state = global().lock().unwrap_or_else(|error| error.into_inner());
            if let Some(owned) = state.child.as_mut() {
                match owned.try_wait() {
                    Ok(Some(_)) => { state.child = None; state.reservation = None; if let Some(id)=state.child_intent.take() {state.pending.discard(id);} fixed_status("owned-child-exited"); }
                    Ok(None) => (),
                    Err(_) => { if !unknown_reported { fixed_status("owned-child-state-unknown"); unknown_reported = true; } }
                }
            }
            let child_present = state.child.is_some();
            drop(state);
            let current = global().lock().unwrap_or_else(|error| error.into_inner()).pending.current();
            let Some((id, url)) = current else {
                let mut state = global().lock().unwrap_or_else(|error| error.into_inner());
                if state.pending.current().is_none() {
                    // Atomically release only startup protection. A newer valid
                    // offer cannot appear between the check and bridge drop.
                    bridge = None;
                    if child_present { drop(state); std::thread::sleep(POLL); continue; }
                    state.worker = false; return;
                }
                continue;
            };
            {
                let mut state=global().lock().unwrap_or_else(|error|error.into_inner());
                if state.pending.expire(id,Instant::now()) {
                    if !state.pending.current().map(|(_,url)|!url.is_empty()).unwrap_or(false) {bridge=None;}
                    fixed_status("intent-expired"); continue;
                }
            }
            // The persistent tray/server/CM parent never becomes foreground OWNER
            // or renews memory. Keep one scoped bridge across bounded retries and
            // newer support generations; no scheduler gap between drop/reacquire.
            if url.is_empty() {
                let state=global().lock().unwrap_or_else(|error|error.into_inner());
                if !state.pending.current().map(|(_,url)|!url.is_empty()).unwrap_or(false) {bridge=None;}
            }
            else if bridge.is_none() {
                match hbb_common::password_security::acquire_support_invite_startup_bridge() {
                    Ok(owned) => bridge = Some(owned),
                    Err(_) => { fixed_status("attended-bridge-unavailable"); std::thread::sleep(POLL); continue; }
                }
            }
            if bridge.as_ref().map(|owned|!owned.current().unwrap_or(false)).unwrap_or(false) {
                let mut state=global().lock().unwrap_or_else(|error|error.into_inner());state.pending.discard(id);
                if !state.pending.current().map(|(_,url)|!url.is_empty()).unwrap_or(false) {bridge=None;}
                drop(state);fixed_status("attended-bridge-unsafe"); std::thread::sleep(POLL); continue;
            }
            if global().lock().unwrap_or_else(|error| error.into_inner()).pending.expire(id, Instant::now()) { fixed_status("intent-expired"); continue; }
            if global().lock().unwrap_or_else(|error| error.into_inner()).pending.current().map(|(next,_)| next) != Some(id) { continue; }
            let outcome = send(url);
            if matches!(outcome, Handoff::Accepted | Handoff::Superseded) {
                {
                    let mut state=global().lock().unwrap_or_else(|error|error.into_inner());state.pending.complete(id,outcome);
                    if !state.pending.current().map(|(_,url)|!url.is_empty()).unwrap_or(false) {bridge=None;}
                }
                fixed_status(if outcome == Handoff::Accepted { "accepted" } else { "superseded" });
                continue;
            }
            if global().lock().unwrap_or_else(|error| error.into_inner()).pending.expire(id, Instant::now()) { fixed_status("intent-expired"); continue; }
            if may_spawn(outcome, child_present) {
                match acquire() {
                    Ok(owned) => {
                        // Recheck the original transport under the reservation.
                        // A GUI may have finished starting while acquire waited.
                        let current = global().lock().unwrap_or_else(|error| error.into_inner()).pending.current();
                        if let Some((current_id, current_url)) = current {
                            if current_id != id { continue; }
                            let support_start = !current_url.is_empty();
                            let fresh = send(current_url);
                            if global().lock().unwrap_or_else(|error| error.into_inner()).pending.expire(current_id, Instant::now()) { fixed_status("intent-expired"); continue; }
                            if global().lock().unwrap_or_else(|error| error.into_inner()).pending.current().map(|(next,_)| next) != Some(current_id) { continue; }
                            if fresh == Handoff::NoReceiver && owned.current().unwrap_or(false) {
                                let command = std::env::current_exe().map(std::process::Command::new);
                                match command.and_then(|mut command| owned.spawn(&mut command, support_start)) {
                                    Ok(launched) => {
                                        let mut state = global().lock().unwrap_or_else(|error| error.into_inner());
                                        state.child = Some(launched); state.reservation = Some(owned); state.child_intent = Some(current_id);
                                        fixed_status("owned-child-starting");
                                    }
                                    Err(_) => { let mut state=global().lock().unwrap_or_else(|error|error.into_inner());state.pending.discard(current_id);if !state.pending.current().map(|(_,url)|!url.is_empty()).unwrap_or(false) {bridge=None;}fixed_status("spawn-failed-intent-refused"); },
                                }
                            } else {
                                global().lock().unwrap_or_else(|error| error.into_inner()).pending.complete(current_id, fresh);
                            }
                        }
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => (),
                    Err(_) => { fixed_status("reservation-unsafe-pending-retained"); }
                }
            }
            // The owned child and fd stay alive after delivery/startup timeout.
            // Pending storage remains one newest intent, retried while it starts.
            std::thread::sleep(POLL);
        }
    }
    pub(crate) fn cold_route(url: String) -> bool {
        let deadline = Instant::now() + RETRY_BUDGET;
        if !url.is_empty() && !is_valid_support_url(&url) { fixed_status("invalid-intent"); return false; }
        let _bridge = if url.is_empty() { None } else {
            match hbb_common::password_security::acquire_support_invite_startup_bridge() {
                Ok(bridge) => Some(bridge), Err(_) => { fixed_status("attended-bridge-unavailable"); return false; }
            }
        };
        if direct().get().is_some() { return url.is_empty(); }
        loop {
            if Instant::now() >= deadline { fixed_status("existing-receiver-not-accepted"); return false; }
            match send(url.clone()) {
                Handoff::Accepted => return false,
                Handoff::Superseded => { fixed_status("superseded"); return false; }
                Handoff::NoReceiver => {
                    if Instant::now() >= deadline { fixed_status("intent-expired"); return false; }
                    match acquire() {
                    Ok(owned) => {
                        let fresh = send(url.clone());
                        if Instant::now() >= deadline { fixed_status("intent-expired"); return false; }
                        if fresh == Handoff::NoReceiver && owned.current().unwrap_or(false) {
                            let _gate = direct_gate().lock().unwrap_or_else(|error| error.into_inner());
                            if Instant::now() >= deadline { fixed_status("intent-expired"); return false; }
                            if direct().get().is_some() { fixed_status("direct-reservation-unavailable"); return false; }
                            if !url.is_empty() && !hbb_common::password_security::hold_support_invite_attended_lease() { fixed_status("foreground-lease-unavailable"); return false; }
                            if direct().set(owned).is_ok() { return true; }
                            fixed_status("direct-reservation-unavailable"); return false;
                        }
                        if matches!(fresh, Handoff::Accepted | Handoff::Superseded) { return false; }
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => (),
                    Err(_) => { fixed_status("reservation-unsafe"); return false; }
                    }
                },
                Handoff::ExistingNeedsRetry => (),
            }
            if Instant::now() >= deadline { fixed_status("existing-receiver-not-accepted"); return false; }
            std::thread::sleep(POLL);
        }
    }
}
#[cfg(all(target_os = "macos", feature = "flutter"))]
pub(crate) use runtime::{adopt_inherited, cold_route, submit};

// Validate the connected kernel peer before any support bearer is serialized.
// This is intentionally separate from generic IPC and its legacy permissions.
#[cfg(target_os = "macos")]
pub(crate) mod peer {
    use std::{ffi::c_void, io, os::unix::{ffi::OsStrExt, io::RawFd}};
    extern "C" {
        fn geteuid() -> u32;
        fn getpeereid(fd: i32, uid: *mut u32, gid: *mut u32) -> i32;
        fn getsockopt(fd: i32, level: i32, name: i32, value: *mut c_void, length: *mut u32) -> i32;
    }
    #[link(name = "proc")]
    extern "C" { fn proc_pidpath(pid: i32, buffer: *mut c_void, length: u32) -> i32; }
    fn identity(fd: RawFd) -> io::Result<(u32, i32)> {
        let mut uid = u32::MAX; let mut gid = u32::MAX;
        if unsafe { getpeereid(fd, &mut uid, &mut gid) } != 0 { return Err(io::Error::last_os_error()); }
        let mut pid = 0i32; let mut length = std::mem::size_of::<i32>() as u32;
        if unsafe { getsockopt(fd, 0, 2, (&mut pid as *mut i32).cast(), &mut length) } != 0 { return Err(io::Error::last_os_error()); }
        if length != std::mem::size_of::<i32>() as u32 || pid <= 0 { return Err(io::ErrorKind::PermissionDenied.into()); }
        Ok((uid, pid))
    }
    fn image(pid: i32) -> io::Result<Vec<u8>> {
        let mut buffer = [0u8; 4096];
        let count = unsafe { proc_pidpath(pid, buffer.as_mut_ptr().cast(), buffer.len() as u32) };
        if count <= 0 || count as usize >= buffer.len() { return Err(io::ErrorKind::PermissionDenied.into()); }
        let Some(end) = buffer.iter().position(|value| *value == 0) else { return Err(io::ErrorKind::PermissionDenied.into()); };
        if end == 0 || buffer[0] != b'/' { return Err(io::ErrorKind::PermissionDenied.into()); }
        Ok(buffer[..end].to_vec())
    }
    pub(crate) fn verify(fd: RawFd) -> io::Result<()> {
        let before = identity(fd)?;
        if before.0 != unsafe { geteuid() } { return Err(io::ErrorKind::PermissionDenied.into()); }
        let expected = std::fs::canonicalize(std::env::current_exe()?)?;
        let first = image(before.1)?;
        if first != expected.as_os_str().as_bytes() || identity(fd)? != before || image(before.1)? != first {
            return Err(io::ErrorKind::PermissionDenied.into());
        }
        Ok(())
    }
}
