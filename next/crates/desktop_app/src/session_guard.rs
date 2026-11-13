//! Protections a technician with control can switch on for the computer they help:
//! - privacy screen: the monitors go dark for the people at the computer; the technician still
//!   sees the desktop (the cover is left out of screen capture) and clicks pass through it,
//! - block input: the local keyboard and mouse are ignored; the technician's input still works,
//! - lock: lock the computer now, or when the session ends.
//!
//! Everything is switched off when the session ends, however it ends (the `Guard` is dropped).
//! Ctrl+Alt+Del on Windows always gives the person at the computer their input back.
#![allow(unsafe_code)] // window, hook and lock calls of the operating system, each commented

#[cfg(target_os = "windows")]
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

pub const PRIVACY: &str = "privacy";
/// `meet:<room>`: the technician offers to show their own screen in a Mixel Meet room.
pub const MEET: &str = "meet";

/// A Mixel Meet room id: four groups of letters and digits, as the meeting service makes them.
pub fn meeting_room(text: &str) -> bool {
    let parts: Vec<&str> = text.split('-').collect();
    parts.len() == 3 && parts.iter().all(|part| part.len() == 4 && part.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit()))
}
/// `pointer:x,y` / `pointer:off`: a ring the person at the computer sees where the technician points.
pub const POINTER: &str = "pointer";
pub const BLOCK: &str = "block";
pub const LOCK: &str = "lock";
/// `lockend:on` / `lockend:off`: lock the computer when the session ends.
pub const LOCK_AT_END: &str = "lockend";

static LOCK_WHEN_DONE: AtomicBool = AtomicBool::new(false);

/// What this host can offer.
pub fn available() -> Vec<&'static str> {
    let mut list = Vec::new();
    if cfg!(target_os = "windows") {
        list.extend([PRIVACY, BLOCK, POINTER]);
    }
    list.extend([LOCK, LOCK_AT_END, MEET]);
    list
}

/// Runs `privacy:on|off`, `block:on|off`, `lock`, `lockend:on|off`.
pub fn run(name: &str) -> Result<(), String> {
    let (action, value) = name.split_once(':').unwrap_or((name, ""));
    let on = value != "off";
    match action {
        PRIVACY => privacy(on),
        BLOCK => block(on),
        LOCK => lock_now(),
        LOCK_AT_END => {
            LOCK_WHEN_DONE.store(on, Ordering::Release);
            Ok(())
        }
        _ => Err("This action is not available on the remote computer".into()),
    }
}

/// Technicians connected to this computer right now (the first one and colleagues who joined).
static HOLDERS: std::sync::Mutex<usize> = std::sync::Mutex::new(0);

/// Held for the length of a hosted session; when the last technician's session ends, every
/// protection is switched off (a colleague leaving keeps the first technician's settings).
pub struct Guard;
impl Guard {
    pub fn new() -> Self {
        let mut holders = HOLDERS.lock().unwrap_or_else(|error| error.into_inner());
        if *holders == 0 {
            LOCK_WHEN_DONE.store(false, Ordering::Release);
        }
        *holders += 1;
        Guard
    }
}
impl Drop for Guard {
    fn drop(&mut self) {
        let mut holders = HOLDERS.lock().unwrap_or_else(|error| error.into_inner());
        *holders = holders.saturating_sub(1);
        if *holders > 0 {
            return;
        }
        drop(holders);
        pointer(None, 0, 0);
        let _ = privacy(false);
        let _ = block(false);
        if LOCK_WHEN_DONE.swap(false, Ordering::AcqRel) {
            let _ = lock_now();
        }
    }
}

fn lock_now() -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        // SAFETY: no arguments; locks the interactive session this program runs in.
        if unsafe { windows::Win32::System::Shutdown::LockWorkStation() }.is_ok() {
            return Ok(());
        }
        use std::os::windows::process::CommandExt;
        let ok = std::process::Command::new("rundll32.exe")
            .args(["user32.dll,LockWorkStation"])
            .creation_flags(0x0800_0000)
            .status()
            .is_ok_and(|status| status.success());
        return if ok { Ok(()) } else { Err("Windows did not lock the computer".into()) };
    }
    #[cfg(target_os = "macos")]
    {
        // The lock screen call the system's own "Lock Screen" menu item uses.
        // SAFETY: the library and symbol are looked up by name; the function takes no arguments.
        unsafe {
            let library = libc::dlopen(c"/System/Library/PrivateFrameworks/login.framework/Versions/Current/login".as_ptr(), libc::RTLD_LAZY);
            if !library.is_null() {
                let symbol = libc::dlsym(library, c"SACLockScreenImmediate".as_ptr());
                if !symbol.is_null() {
                    let lock: extern "C" fn() -> i32 = std::mem::transmute(symbol);
                    lock();
                    return Ok(());
                }
            }
        }
        return Err("macOS did not lock the computer".into());
    }
    #[cfg(target_os = "linux")]
    {
        let ok = std::process::Command::new("loginctl").arg("lock-session").status().is_ok_and(|status| status.success());
        return if ok { Ok(()) } else { Err("The system did not lock the computer".into()) };
    }
    #[allow(unreachable_code)]
    Err("Locking is not available here".into())
}

#[cfg(target_os = "windows")]
mod win {
    use super::*;
    use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, RECT, WPARAM};
    use windows::Win32::Graphics::Gdi::{
        BeginPaint, CreateFontW, CreateSolidBrush, DT_CENTER, DT_SINGLELINE, DT_VCENTER, DeleteObject, DrawTextW, EndPaint, FillRect,
        HGDIOBJ, PAINTSTRUCT, SelectObject, SetBkMode, SetTextColor, TRANSPARENT,
    };
    use windows::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows::Win32::System::Threading::GetCurrentThreadId;
    use windows::Win32::UI::WindowsAndMessaging::*;
    use windows::core::{PCWSTR, w};

    /// Thread ids of the running cover and input block (0 = off).
    pub static COVER: Mutex<u32> = Mutex::new(0);
    pub static BLOCKER: Mutex<u32> = Mutex::new(0);

    unsafe extern "system" fn cover_proc(window: HWND, message: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
        match message {
            WM_PAINT => {
                let mut paint = PAINTSTRUCT::default();
                // SAFETY: standard paint cycle on our own window.
                unsafe {
                    let dc = BeginPaint(window, &mut paint);
                    let mut area = RECT::default();
                    let _ = GetClientRect(window, &mut area);
                    let brush = CreateSolidBrush(COLORREF(0x0014_1414));
                    FillRect(dc, &area, brush);
                    let _ = DeleteObject(HGDIOBJ(brush.0));
                    let font = CreateFontW(34, 0, 0, 0, 600, 0, 0, 0, Default::default(), Default::default(), Default::default(), Default::default(), 0, w!("Segoe UI"));
                    let previous = SelectObject(dc, HGDIOBJ(font.0));
                    SetBkMode(dc, TRANSPARENT);
                    SetTextColor(dc, COLORREF(0x00F0_F0F0));
                    let mut text: Vec<u16> = "Mixel Remote · A technician is working on this computer".encode_utf16().collect();
                    DrawTextW(dc, &mut text, &mut area, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
                    SelectObject(dc, previous);
                    let _ = DeleteObject(HGDIOBJ(font.0));
                    let _ = EndPaint(window, &paint);
                }
                LRESULT(0)
            }
            WM_TIMER => {
                // Stay on top of windows that open later.
                // SAFETY: our own window.
                unsafe { let _ = SetWindowPos(window, Some(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE); }
                LRESULT(0)
            }
            // SAFETY: default handling for everything else.
            _ => unsafe { DefWindowProcW(window, message, wparam, lparam) },
        }
    }

    pub fn cover() {
        // SAFETY: window creation and a message loop on this thread only.
        unsafe {
            let instance = GetModuleHandleW(PCWSTR::null()).unwrap_or_default();
            let class = w!("MixelRemotePrivacyCover");
            let description = WNDCLASSW { lpfnWndProc: Some(cover_proc), hInstance: instance.into(), lpszClassName: class, hCursor: LoadCursorW(None, IDC_ARROW).unwrap_or_default(), ..Default::default() };
            RegisterClassW(&description);
            let (x, y) = (GetSystemMetrics(SM_XVIRTUALSCREEN), GetSystemMetrics(SM_YVIRTUALSCREEN));
            let (width, height) = (GetSystemMetrics(SM_CXVIRTUALSCREEN), GetSystemMetrics(SM_CYVIRTUALSCREEN));
            let Ok(window) = CreateWindowExW(
                WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE,
                class, w!("Mixel Remote privacy screen"), WS_POPUP, x, y, width, height, None, None, Some(instance.into()), None,
            ) else { return };
            let _ = SetLayeredWindowAttributes(window, COLORREF(0), 255, LWA_ALPHA);
            // The technician's picture does not contain the cover.
            let _ = SetWindowDisplayAffinity(window, WDA_EXCLUDEFROMCAPTURE);
            let _ = ShowWindow(window, SW_SHOWNOACTIVATE);
            SetTimer(Some(window), 1, 1000, None);
            let mut message = MSG::default();
            while GetMessageW(&mut message, None, 0, 0).as_bool() {
                if message.message == WM_QUIT { break; }
                let _ = TranslateMessage(&message);
                DispatchMessageW(&message);
            }
            let _ = DestroyWindow(window);
        }
    }

    unsafe extern "system" fn keyboard_hook(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
        if code >= 0 {
            // SAFETY: Windows passes a KBDLLHOOKSTRUCT for this hook.
            let event = unsafe { &*(lparam.0 as *const KBDLLHOOKSTRUCT) };
            if event.flags.0 & LLKHF_INJECTED.0 == 0 {
                return LRESULT(1);
            }
        }
        // SAFETY: pass everything else on.
        unsafe { CallNextHookEx(None, code, wparam, lparam) }
    }
    unsafe extern "system" fn mouse_hook(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
        if code >= 0 {
            // SAFETY: Windows passes a MSLLHOOKSTRUCT for this hook.
            let event = unsafe { &*(lparam.0 as *const MSLLHOOKSTRUCT) };
            if event.flags & LLMHF_INJECTED == 0 {
                return LRESULT(1);
            }
        }
        // SAFETY: pass everything else on.
        unsafe { CallNextHookEx(None, code, wparam, lparam) }
    }

    pub fn blocker() {
        // SAFETY: low-level hooks need a message loop on the installing thread.
        unsafe {
            let instance = GetModuleHandleW(PCWSTR::null()).unwrap_or_default();
            let keyboard = SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard_hook), Some(instance.into()), 0).ok();
            let mouse = SetWindowsHookExW(WH_MOUSE_LL, Some(mouse_hook), Some(instance.into()), 0).ok();
            let mut message = MSG::default();
            while GetMessageW(&mut message, None, 0, 0).as_bool() {
                if message.message == WM_QUIT { break; }
            }
            if let Some(hook) = keyboard { let _ = UnhookWindowsHookEx(hook); }
            if let Some(hook) = mouse { let _ = UnhookWindowsHookEx(hook); }
        }
    }

    /// The pointer ring: a small round, click-through window in Mixel red, moved by the technician.
    pub static POINTER_THREAD: Mutex<u32> = Mutex::new(0);
    const MOVE: u32 = WM_APP + 1;
    const SIZE: i32 = 34;

    unsafe extern "system" fn ring_proc(window: HWND, message: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
        if message == WM_PAINT {
            let mut paint = PAINTSTRUCT::default();
            // SAFETY: standard paint cycle on our own window.
            unsafe {
                let dc = BeginPaint(window, &mut paint);
                let mut area = RECT::default();
                let _ = GetClientRect(window, &mut area);
                let back = CreateSolidBrush(COLORREF(0x00FF_00FF));
                FillRect(dc, &area, back);
                let _ = DeleteObject(HGDIOBJ(back.0));
                let red = CreateSolidBrush(COLORREF(0x0034_20BA));
                let old = SelectObject(dc, HGDIOBJ(red.0));
                let _ = windows::Win32::Graphics::Gdi::Ellipse(dc, 1, 1, SIZE - 1, SIZE - 1);
                let white = CreateSolidBrush(COLORREF(0x00FF_FFFF));
                SelectObject(dc, HGDIOBJ(white.0));
                let _ = windows::Win32::Graphics::Gdi::Ellipse(dc, SIZE / 2 - 5, SIZE / 2 - 5, SIZE / 2 + 5, SIZE / 2 + 5);
                SelectObject(dc, old);
                let _ = DeleteObject(HGDIOBJ(red.0));
                let _ = DeleteObject(HGDIOBJ(white.0));
                let _ = EndPaint(window, &paint);
            }
            return LRESULT(0);
        }
        // SAFETY: default handling.
        unsafe { DefWindowProcW(window, message, wparam, lparam) }
    }

    fn ring() {
        // SAFETY: window creation and a message loop on this thread only.
        unsafe {
            let instance = GetModuleHandleW(PCWSTR::null()).unwrap_or_default();
            let class = w!("MixelRemotePointer");
            let description = WNDCLASSW { lpfnWndProc: Some(ring_proc), hInstance: instance.into(), lpszClassName: class, ..Default::default() };
            RegisterClassW(&description);
            let Ok(window) = CreateWindowExW(
                WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE,
                class, w!("Mixel Remote pointer"), WS_POPUP, 0, 0, SIZE, SIZE, None, None, Some(instance.into()), None,
            ) else { return };
            // Magenta is see-through: only the ring shows.
            let _ = SetLayeredWindowAttributes(window, COLORREF(0x00FF_00FF), 0, LWA_COLORKEY);
            let mut message = MSG::default();
            while GetMessageW(&mut message, None, 0, 0).as_bool() {
                match message.message {
                    WM_QUIT => break,
                    MOVE => {
                        let (x, y) = ((message.lParam.0 & 0xffff_ffff) as u32 as i32, (message.lParam.0 >> 32) as i32);
                        let _ = SetWindowPos(window, Some(HWND_TOPMOST), x - SIZE / 2, y - SIZE / 2, SIZE, SIZE, SWP_NOACTIVATE | SWP_SHOWWINDOW);
                    }
                    _ => {
                        let _ = TranslateMessage(&message);
                        DispatchMessageW(&message);
                    }
                }
            }
            let _ = DestroyWindow(window);
        }
    }

    /// Screen rectangle of the shared display: the whole desktop, or the n-th monitor.
    fn display_rect(display: usize, displays: usize) -> (i32, i32, i32, i32) {
        // SAFETY: plain system metrics and monitor enumeration into a local list.
        unsafe {
            let whole = (GetSystemMetrics(SM_XVIRTUALSCREEN), GetSystemMetrics(SM_YVIRTUALSCREEN), GetSystemMetrics(SM_CXVIRTUALSCREEN), GetSystemMetrics(SM_CYVIRTUALSCREEN));
            unsafe extern "system" fn collect(monitor: windows::Win32::Graphics::Gdi::HMONITOR, _: windows::Win32::Graphics::Gdi::HDC, _: *mut RECT, data: LPARAM) -> windows::core::BOOL {
                let mut info = windows::Win32::Graphics::Gdi::MONITORINFO { cbSize: std::mem::size_of::<windows::Win32::Graphics::Gdi::MONITORINFO>() as u32, ..Default::default() };
                // SAFETY: `data` is the Vec passed below; `info` is sized correctly.
                unsafe {
                    if windows::Win32::Graphics::Gdi::GetMonitorInfoW(monitor, &mut info).as_bool() {
                        (*(data.0 as *mut Vec<RECT>)).push(info.rcMonitor);
                    }
                }
                true.into()
            }
            let mut monitors: Vec<RECT> = Vec::new();
            let _ = windows::Win32::Graphics::Gdi::EnumDisplayMonitors(None, None, Some(collect), LPARAM(&mut monitors as *mut Vec<RECT> as isize));
            // One display, or "all screens" (the last choice when there are several): the whole desktop.
            if monitors.len() <= 1 || displays <= 1 || display >= monitors.len() {
                return whole;
            }
            let rect = monitors[display];
            (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        }
    }

    pub fn point(point: Option<(u16, u16)>, display: usize, displays: usize) {
        let Ok(mut running) = POINTER_THREAD.lock() else { return };
        let Some((x, y)) = point else {
            if *running != 0 {
                // SAFETY: ends the ring's message loop.
                unsafe { let _ = PostThreadMessageW(*running, WM_QUIT, WPARAM(0), LPARAM(0)); }
                *running = 0;
            }
            return;
        };
        if *running == 0 {
            let (sender, receiver) = std::sync::mpsc::channel();
            std::thread::spawn(move || {
                // SAFETY: plain query of this thread's id.
                let _ = sender.send(unsafe { GetCurrentThreadId() });
                ring();
            });
            let Ok(id) = receiver.recv_timeout(std::time::Duration::from_secs(2)) else { return };
            *running = id;
            std::thread::sleep(std::time::Duration::from_millis(30));
        }
        let (left, top, width, height) = display_rect(display, displays);
        let px = left + (i64::from(x) * i64::from(width.max(1) - 1) / 65535) as i32;
        let py = top + (i64::from(y) * i64::from(height.max(1) - 1) / 65535) as i32;
        let packed = (i64::from(px as u32)) | (i64::from(py) << 32);
        // SAFETY: posts a move to the ring's own thread.
        unsafe { let _ = PostThreadMessageW(*running, MOVE, WPARAM(0), LPARAM(packed as isize)); }
    }

    /// Starts `body` on its own thread (once) or stops it.
    pub fn toggle(slot: &Mutex<u32>, on: bool, body: fn()) -> Result<(), String> {
        let mut running = slot.lock().map_err(|_| "Protection state unavailable")?;
        if on && *running == 0 {
            let (sender, receiver) = std::sync::mpsc::channel();
            std::thread::spawn(move || {
                // SAFETY: plain query of this thread's id.
                let _ = sender.send(unsafe { GetCurrentThreadId() });
                body();
            });
            *running = receiver.recv_timeout(std::time::Duration::from_secs(2)).map_err(|_| "The protection did not start")?;
        } else if !on && *running != 0 {
            // SAFETY: ends the message loop of the thread started above.
            unsafe { let _ = PostThreadMessageW(*running, WM_QUIT, WPARAM(0), LPARAM(0)); }
            *running = 0;
        }
        Ok(())
    }
}

/// Shows the technician's pointer at `point` (0..65535 on display `display` of `displays`), or hides it.
pub fn pointer(point: Option<(u16, u16)>, display: usize, displays: usize) {
    #[cfg(target_os = "windows")]
    win::point(point, display, displays);
    #[cfg(not(target_os = "windows"))]
    let _ = (point, display, displays);
}

fn privacy(on: bool) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    return win::toggle(&win::COVER, on, win::cover);
    #[allow(unreachable_code)]
    if on { Err("The privacy screen is available for Windows computers".into()) } else { Ok(()) }
}

fn block(on: bool) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    return win::toggle(&win::BLOCKER, on, win::blocker);
    #[allow(unreachable_code)]
    if on { Err("Blocking the keyboard and mouse is available for Windows computers".into()) } else { Ok(()) }
}

#[cfg(test)]
mod tests {
    #[test]
    fn unknown_actions_are_refused_and_off_is_always_safe() {
        assert!(super::run("format").is_err());
        assert!(super::run("privacy:off").is_ok());
        assert!(super::run("block:off").is_ok());
        assert!(super::run("lockend:on").is_ok());
        assert!(super::run("lockend:off").is_ok());
        assert!(super::available().contains(&super::LOCK));
        // Only room ids the meeting service makes are accepted from the technician.
        assert!(super::meeting_room("pu5f-9qwq-m767"));
        for bad in ["", "pu5f-9qwq", "PU5F-9QWQ-M767", "pu5f-9qwq-m767.key", "../x-yyyy-zzzz", "pu5f-9qwq-m76"] {
            assert!(!super::meeting_room(bad), "{bad}");
        }
    }
}
