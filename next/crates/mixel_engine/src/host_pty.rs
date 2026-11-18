//! Host side of the remote terminal: a pseudo terminal with a shell (forkpty-style on macOS and Linux,
//! ConPTY on Windows). The shell runs as the logged-on user; "elevated" (SYSTEM / root) exists only where
//! the host engine itself is the privileged service (`privileged()`).
#![allow(unsafe_code)] // libc / Win32 calls for the pseudo terminal, each commented.
use crate::tools_proto::{ToolError, ToolResult, err};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
    mpsc,
};

/// A shell the host offers: the name the technician chooses and the absolute path the host resolved itself.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Shell {
    pub name: &'static str,
    pub path: std::path::PathBuf,
}

/// What a terminal is started with.
pub struct Spec {
    pub shell: Shell,
    pub elevated: bool,
    pub cols: u16,
    pub rows: u16,
}

/// The user a non-elevated terminal runs as when the host engine is the privileged service.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ConsoleUser {
    pub name: String,
    pub uid: u32,
    pub gid: u32,
    pub home: std::path::PathBuf,
}

pub struct Pty {
    inner: imp::Inner,
    exit: Arc<Mutex<Option<i32>>>,
    killed: AtomicBool,
}

pub fn clamp_size(cols: u16, rows: u16) -> (u16, u16) {
    (cols.clamp(10, 500), rows.clamp(2, 200))
}

impl Pty {
    /// Starts the shell. The output arrives on the returned bounded channel (the reader blocks when it is full:
    /// back-pressure reaches the shell); the channel disconnects when the shell has exited.
    pub fn spawn(spec: &Spec, user: Option<&ConsoleUser>) -> ToolResult<(Pty, mpsc::Receiver<Vec<u8>>)> {
        let (cols, rows) = clamp_size(spec.cols, spec.rows);
        let exit = Arc::new(Mutex::new(None));
        let (tx, rx) = mpsc::sync_channel(32);
        let inner = imp::spawn(spec, user, cols, rows, tx, exit.clone())
            .map_err(|message| err("terminal", "not_available", &message))?;
        Ok((Pty { inner, exit, killed: AtomicBool::new(false) }, rx))
    }
    pub fn write(&self, data: &[u8]) -> ToolResult<()> {
        imp::write(&self.inner, data).map_err(|_| err("terminal", "not_open", "The shell is gone"))
    }
    pub fn resize(&self, cols: u16, rows: u16) {
        let (cols, rows) = clamp_size(cols, rows);
        imp::resize(&self.inner, cols, rows);
    }
    /// Ends the shell and everything it started.
    pub fn kill(&self) {
        if !self.killed.swap(true, Ordering::AcqRel) {
            imp::kill(&self.inner);
        }
    }
    pub fn exit_code(&self) -> Option<i32> {
        self.exit.lock().ok().and_then(|code| *code)
    }
    pub fn pid(&self) -> u32 {
        imp::pid(&self.inner)
    }
}
impl Drop for Pty {
    fn drop(&mut self) {
        self.kill();
    }
}

/// Whether this process can start a shell as SYSTEM / root (it is the always-on service, or root).
pub fn privileged() -> bool {
    imp::privileged()
}
/// The shells this computer offers, the first being the default.
pub fn shells() -> Vec<Shell> {
    imp::shells()
}
/// The person logged on at the console, if any (needed when `privileged()` to run as them).
pub fn console_user() -> Option<ConsoleUser> {
    imp::console_user()
}

/// Checks a requested shell name against the list.
pub fn pick_shell(name: Option<&str>) -> ToolResult<Shell> {
    let list = shells();
    match name {
        None => list.into_iter().next().ok_or_else(|| err("terminal", "not_available", "No shell is available")),
        Some(name) => list
            .into_iter()
            .find(|shell| shell.name == name)
            .ok_or_else(|| ToolError::new("terminal", "bad_shell", "This shell is not offered on this computer")),
    }
}

#[cfg(unix)]
mod imp {
    use super::*;
    use std::{
        fs::File,
        io::{Read, Write},
        os::{
            fd::{AsRawFd, FromRawFd, OwnedFd},
            unix::process::CommandExt,
        },
        process::{Command, Stdio},
    };

    pub struct Inner {
        master: File,
        pid: u32,
    }

    pub fn privileged() -> bool {
        // SAFETY: geteuid has no preconditions.
        unsafe { libc::geteuid() == 0 }
    }

    pub fn shells() -> Vec<Shell> {
        let candidates: &[(&'static str, &[&str])] = if cfg!(target_os = "macos") {
            &[("zsh", &["/bin/zsh"]), ("bash", &["/bin/bash"]), ("sh", &["/bin/sh"])]
        } else {
            &[
                ("bash", &["/bin/bash", "/usr/bin/bash"]),
                ("sh", &["/bin/sh", "/usr/bin/sh"]),
                ("zsh", &["/bin/zsh", "/usr/bin/zsh"]),
                ("fish", &["/usr/bin/fish", "/bin/fish"]),
            ]
        };
        candidates
            .iter()
            .filter_map(|(name, paths)| {
                paths
                    .iter()
                    .map(std::path::PathBuf::from)
                    .find(|path| path.is_file())
                    .map(|path| Shell { name, path })
            })
            .collect()
    }

    fn passwd(uid: u32) -> Option<ConsoleUser> {
        let mut buffer = vec![0u8; 8192];
        // SAFETY: zeroed passwd is a valid out-parameter; getpwuid_r fills it using `buffer`, which outlives
        // the strings read below.
        unsafe {
            let mut pwd: libc::passwd = std::mem::zeroed();
            let mut result: *mut libc::passwd = std::ptr::null_mut();
            if libc::getpwuid_r(uid, &mut pwd, buffer.as_mut_ptr().cast(), buffer.len(), &mut result) != 0 || result.is_null() {
                return None;
            }
            let name = std::ffi::CStr::from_ptr(pwd.pw_name).to_string_lossy().into_owned();
            let home = std::ffi::CStr::from_ptr(pwd.pw_dir).to_string_lossy().into_owned();
            Some(ConsoleUser { name, uid, gid: pwd.pw_gid as u32, home: home.into() })
        }
    }

    pub fn console_user() -> Option<ConsoleUser> {
        if !privileged() {
            // Not the service: the shell runs as the user of this process.
            // SAFETY: getuid has no preconditions.
            return passwd(unsafe { libc::getuid() });
        }
        let uid = console_uid()?;
        if uid == 0 { None } else { passwd(uid) }
    }

    #[cfg(target_os = "macos")]
    fn console_uid() -> Option<u32> {
        use std::os::unix::fs::MetadataExt;
        std::fs::metadata("/dev/console").ok().map(|meta| meta.uid())
    }
    #[cfg(not(target_os = "macos"))]
    fn console_uid() -> Option<u32> {
        let list = Command::new("loginctl").args(["list-sessions", "--no-legend"]).output().ok()?;
        for line in String::from_utf8_lossy(&list.stdout).lines() {
            let Some(id) = line.split_whitespace().next() else { continue };
            if !id.bytes().all(|b| b.is_ascii_alphanumeric()) {
                continue;
            }
            let shown = Command::new("loginctl")
                .args(["show-session", id, "-p", "Active", "-p", "Class", "-p", "User"])
                .output()
                .ok()?;
            let text = String::from_utf8_lossy(&shown.stdout).into_owned();
            let value = |key: &str| text.lines().find_map(|line| line.strip_prefix(key)).map(str::to_owned);
            if value("Active=").as_deref() == Some("yes")
                && value("Class=").as_deref() == Some("user")
                && let Some(uid) = value("User=").and_then(|text| text.parse::<u32>().ok())
                && uid >= 1
            {
                return Some(uid);
            }
        }
        None
    }

    pub fn spawn(
        spec: &Spec,
        user: Option<&ConsoleUser>,
        cols: u16,
        rows: u16,
        tx: mpsc::SyncSender<Vec<u8>>,
        exit: Arc<Mutex<Option<i32>>>,
    ) -> Result<Inner, String> {
        let mut master: libc::c_int = -1;
        let mut slave: libc::c_int = -1;
        let mut size = libc::winsize { ws_row: rows, ws_col: cols, ws_xpixel: 0, ws_ypixel: 0 };
        // SAFETY: valid out-pointers; the winsize is initialised; no name or termios is passed.
        let opened = unsafe { libc::openpty(&mut master, &mut slave, std::ptr::null_mut(), std::ptr::null_mut(), &mut size) };
        if opened != 0 {
            return Err("Cannot open a terminal".into());
        }
        // SAFETY: both fds were just created by openpty and are owned here.
        let (master, slave) = unsafe { (OwnedFd::from_raw_fd(master), OwnedFd::from_raw_fd(slave)) };
        let mut command = Command::new(&spec.shell.path);
        match spec.shell.name {
            "bash" | "zsh" | "sh" | "fish" => {
                command.arg("-i");
            }
            _ => {}
        }
        command.env_clear();
        command.env("TERM", "xterm-256color").env("LANG", std::env::var("LANG").unwrap_or_else(|_| "en_US.UTF-8".into()));
        command.env("PATH", "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin");
        let real = privileged();
        let account = if spec.elevated && real { passwd(0) } else { user.cloned() };
        if let Some(account) = &account {
            command.env("HOME", &account.home).env("USER", &account.name).env("LOGNAME", &account.name);
            command.env("SHELL", &spec.shell.path);
            command.current_dir(&account.home);
            if real && account.uid != 0 {
                command.uid(account.uid).gid(account.gid);
            }
        }
        let slave_file = File::from(slave.try_clone().map_err(|_| "Cannot prepare the terminal")?);
        command
            .stdin(Stdio::from(slave_file.try_clone().map_err(|_| "Cannot prepare the terminal")?))
            .stdout(Stdio::from(slave_file.try_clone().map_err(|_| "Cannot prepare the terminal")?))
            .stderr(Stdio::from(slave_file));
        // SAFETY: runs between fork and exec; only async-signal-safe calls (setsid, ioctl).
        unsafe {
            command.pre_exec(|| {
                libc::setsid();
                libc::ioctl(0, libc::TIOCSCTTY as _, 0);
                Ok(())
            });
        }
        let mut child = command.spawn().map_err(|_| "The shell could not be started".to_owned())?;
        drop(slave);
        let pid = child.id();
        let master = File::from(master);
        let mut reader = master.try_clone().map_err(|_| "Cannot prepare the terminal")?;
        std::thread::spawn(move || {
            let mut buffer = [0u8; 3000];
            loop {
                match reader.read(&mut buffer) {
                    Ok(0) | Err(_) => break,
                    Ok(count) => {
                        if tx.send(buffer[..count].to_vec()).is_err() {
                            break;
                        }
                    }
                }
            }
            // `tx` drops here: the receiver sees the end of the output.
        });
        std::thread::spawn(move || {
            let status = child.wait().ok();
            let code = status.and_then(|status| {
                use std::os::unix::process::ExitStatusExt;
                status.code().or_else(|| status.signal().map(|signal| 128 + signal))
            });
            if let Ok(mut slot) = exit.lock() {
                *slot = Some(code.unwrap_or(-1));
            }
        });
        Ok(Inner { master, pid })
    }

    pub fn write(inner: &Inner, data: &[u8]) -> std::io::Result<()> {
        (&inner.master).write_all(data)
    }
    pub fn resize(inner: &Inner, cols: u16, rows: u16) {
        let size = libc::winsize { ws_row: rows, ws_col: cols, ws_xpixel: 0, ws_ypixel: 0 };
        // SAFETY: valid master fd and an initialised winsize.
        unsafe { libc::ioctl(inner.master.as_raw_fd(), libc::TIOCSWINSZ as _, &size) };
    }
    pub fn kill(inner: &Inner) {
        let pid = inner.pid as libc::pid_t;
        // The shell is a session leader (setsid): its process group id is its pid. HUP first, then KILL.
        // SAFETY: plain signal calls on our own child's group.
        unsafe {
            libc::kill(-pid, libc::SIGHUP);
            libc::kill(-pid, libc::SIGCONT);
        }
        let group = pid;
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(700));
            // SAFETY: as above; harmless if the group is gone (the pid could be reused only after `wait`,
            // which the waiting thread does right after exit; the delay is short).
            unsafe { libc::kill(-group, libc::SIGKILL) };
        });
    }
    pub fn pid(inner: &Inner) -> u32 {
        inner.pid
    }
}

#[cfg(not(unix))]
mod imp {
    use super::*;
    pub struct Inner;
    pub fn privileged() -> bool {
        false
    }
    pub fn shells() -> Vec<Shell> {
        Vec::new()
    }
    pub fn console_user() -> Option<ConsoleUser> {
        None
    }
    pub fn spawn(_: &Spec, _: Option<&ConsoleUser>, _: u16, _: u16, _: mpsc::SyncSender<Vec<u8>>, _: Arc<Mutex<Option<i32>>>) -> Result<Inner, String> {
        Err("Terminal is not available on this platform yet".into())
    }
    pub fn write(_: &Inner, _: &[u8]) -> std::io::Result<()> {
        Err(std::io::ErrorKind::Unsupported.into())
    }
    pub fn resize(_: &Inner, _: u16, _: u16) {}
    pub fn kill(_: &Inner) {}
    pub fn pid(_: &Inner) -> u32 {
        0
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::time::{Duration, Instant};

    fn collect(rx: &mpsc::Receiver<Vec<u8>>, until: &str, wait: Duration) -> String {
        let end = Instant::now() + wait;
        let mut text = String::new();
        while Instant::now() < end {
            if let Ok(chunk) = rx.recv_timeout(Duration::from_millis(50)) {
                text.push_str(&String::from_utf8_lossy(&chunk));
                if text.contains(until) {
                    break;
                }
            }
        }
        text
    }
    fn sh() -> Shell {
        pick_shell(Some("sh")).unwrap()
    }

    #[test]
    fn a_shell_echoes_input_resizes_and_reports_its_exit_code() {
        let user = console_user();
        let (pty, rx) = Pty::spawn(&Spec { shell: sh(), elevated: false, cols: 80, rows: 24 }, user.as_ref()).unwrap();
        pty.write(b"echo hello-$((20+22))\n").unwrap();
        let text = collect(&rx, "hello-42", Duration::from_secs(5));
        assert!(text.contains("hello-42"), "{text}");
        pty.resize(100, 30);
        pty.write(b"stty size\n").unwrap();
        let text = collect(&rx, "30 100", Duration::from_secs(5));
        assert!(text.contains("30 100"), "{text}");
        pty.write(b"exit 7\n").unwrap();
        let end = Instant::now() + Duration::from_secs(5);
        while pty.exit_code().is_none() && Instant::now() < end {
            std::thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(pty.exit_code(), Some(7));
    }

    #[test]
    fn kill_ends_the_whole_process_tree_and_the_output_channel() {
        let user = console_user();
        let (pty, rx) = Pty::spawn(&Spec { shell: sh(), elevated: false, cols: 80, rows: 24 }, user.as_ref()).unwrap();
        pty.write(b"sleep 300 &\n").unwrap();
        pty.write(b"echo started-$$\n").unwrap();
        let text = collect(&rx, "started-", Duration::from_secs(5));
        assert!(text.contains("started-"));
        let pid = pty.pid();
        pty.kill();
        let end = Instant::now() + Duration::from_secs(6);
        while pty.exit_code().is_none() && Instant::now() < end {
            std::thread::sleep(Duration::from_millis(50));
        }
        assert!(pty.exit_code().is_some(), "the shell must end");
        // The background sleep belonged to the shell's process group: nothing of it remains.
        std::thread::sleep(Duration::from_millis(1200));
        // SAFETY: signal 0 only tests for existence of the group.
        let alive = unsafe { libc::kill(-(pid as libc::pid_t), 0) } == 0;
        assert!(!alive, "no process of the terminal's group may remain");
        drop(rx);
    }

    #[test]
    fn shell_names_are_checked_against_the_host_list() {
        assert!(pick_shell(Some("../../bin/evil")).is_err());
        assert_eq!(pick_shell(Some("nope")).unwrap_err().code, "bad_shell");
        assert!(pick_shell(None).is_ok());
        assert_eq!(clamp_size(1, 9999), (10, 200));
    }

    #[test]
    fn elevation_is_offered_only_to_a_root_host() {
        assert_eq!(privileged(), unsafe { libc::geteuid() } == 0);
    }
}
