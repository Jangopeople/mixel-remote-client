//! DXGI Desktop Duplication. Construct and poll on one capture-owner thread,
//! only after the host grants sharing of the selected display. No global capture
//! starts at module load. Dirty-frame transport and session policy live above it.
use crate::stream::{CaptureError, CapturedFrame};
#[path = "windows_gdi_capture.rs"]
mod gdi;
use std::time::Duration;
use windows::{
    Win32::{
        Foundation::HMODULE,
        Graphics::{
            Direct3D::D3D_DRIVER_TYPE_UNKNOWN,
            Direct3D11::*,
            Dxgi::{Common::*, *},
        },
    },
    core::Interface,
};
use zeroize::Zeroizing;
/// Keep the screen on while the calling thread runs (`on`), or let Windows turn it off again.
///
/// Windows turns the display off after its idle timeout (10 minutes on the HP laptop's mains
/// plan) when nobody touches that computer, and a technician who only watches sends no input.
/// With the display off the compositor stops and desktop duplication returns no new pictures:
/// the technician sees the last picture for minutes or hours while the host reports an unchanged
/// screen. TeamViewer holds the display on for its sessions; this does the same for the capture
/// owner thread. The request belongs to the thread and ends with it.
pub fn keep_display_on(on: bool) -> bool {
    use windows::Win32::System::Power::{
        ES_CONTINUOUS, ES_DISPLAY_REQUIRED, ES_SYSTEM_REQUIRED, SetThreadExecutionState,
    };
    let flags = if on {
        ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED
    } else {
        ES_CONTINUOUS
    };
    // SAFETY: a plain flag call without pointers.
    unsafe { SetThreadExecutionState(flags) }.0 != 0
}

/// Turn on a display that is already off: a mouse move of zero counts as user input (the pointer
/// stays where it is). Holding the display on does not switch on one that went dark before.
pub fn wake_display() -> bool {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        INPUT, INPUT_0, INPUT_MOUSE, MOUSEEVENTF_MOVE, MOUSEINPUT, SendInput,
    };
    let _ = attach_input_desktop();
    let input = INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx: 0,
                dy: 0,
                mouseData: 0,
                dwFlags: MOUSEEVENTF_MOVE,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    };
    // SAFETY: one initialized INPUT of the exact SDK layout, copied by Windows during the call.
    unsafe { SendInput(&[input], std::mem::size_of::<INPUT>() as i32) == 1 }
}

/// Attach the calling thread to the desktop that currently receives input,
/// so a new duplication follows the user's desktop after a lock or a secure
/// prompt. Returns false when that desktop is not accessible (still secure).
pub fn attach_input_desktop() -> bool {
    use std::cell::RefCell;
    use windows::Win32::System::StationsAndDesktops::{
        CloseDesktop, DESKTOP_ACCESS_FLAGS, DESKTOP_CONTROL_FLAGS, GetUserObjectInformationW, HDESK,
        OpenInputDesktop, SetThreadDesktop, UOI_NAME,
    };
    thread_local! {
        // The desktop this thread is attached to, by name; switched only when it changes.
        static CURRENT: RefCell<Option<(String, HDESK)>> = const { RefCell::new(None) };
    }
    fn name(desktop: HDESK) -> Option<String> {
        let mut buffer = [0u16; 128];
        let mut needed = 0u32;
        // SAFETY: the buffer and length describe valid writable memory.
        unsafe {
            GetUserObjectInformationW(
                windows::Win32::Foundation::HANDLE(desktop.0),
                UOI_NAME,
                Some(buffer.as_mut_ptr().cast()),
                (buffer.len() * 2) as u32,
                Some(&mut needed),
            )
            .ok()?;
        }
        let length = buffer.iter().position(|&unit| unit == 0).unwrap_or(buffer.len());
        Some(String::from_utf16_lossy(&buffer[..length]))
    }
    // SAFETY: handles are closed exactly once; the attached one stays open while in use,
    // as SetThreadDesktop requires. MAXIMUM_ALLOWED includes the input (journal) right
    // that SendInput needs on secure desktops for the SYSTEM host.
    unsafe {
        let Ok(desktop) = OpenInputDesktop(DESKTOP_CONTROL_FLAGS(0), false, DESKTOP_ACCESS_FLAGS(0x0200_0000)) else {
            return false;
        };
        let Some(shown) = name(desktop) else {
            let _ = CloseDesktop(desktop);
            return false;
        };
        CURRENT.with(|current| {
            let mut current = current.borrow_mut();
            if current.as_ref().is_some_and(|(attached, _)| *attached == shown) {
                let _ = CloseDesktop(desktop);
                return true;
            }
            if SetThreadDesktop(desktop).is_err() {
                let _ = CloseDesktop(desktop);
                return false;
            }
            if let Some((_, previous)) = current.replace((shown, desktop)) {
                let _ = CloseDesktop(previous);
            }
            true
        })
    }
}
/// The name of the desktop that receives input ("Default", "Winlogon" for the lock or sign-in
/// screen and secure prompts), or "unreadable" when this process may not open it. For the logs.
pub fn input_desktop_name() -> String {
    use windows::Win32::System::StationsAndDesktops::{
        CloseDesktop, DESKTOP_ACCESS_FLAGS, DESKTOP_CONTROL_FLAGS, GetUserObjectInformationW,
        OpenInputDesktop, UOI_NAME,
    };
    // SAFETY: the handle is closed once; the buffer and length describe writable memory.
    unsafe {
        let Ok(desktop) = OpenInputDesktop(DESKTOP_CONTROL_FLAGS(0), false, DESKTOP_ACCESS_FLAGS(0x0001)) else {
            return "unreadable".into();
        };
        let mut buffer = [0u16; 128];
        let mut needed = 0u32;
        let named = GetUserObjectInformationW(
            windows::Win32::Foundation::HANDLE(desktop.0),
            UOI_NAME,
            Some(buffer.as_mut_ptr().cast()),
            (buffer.len() * 2) as u32,
            Some(&mut needed),
        )
        .is_ok();
        let _ = CloseDesktop(desktop);
        if !named {
            return "unnamed".into();
        }
        let length = buffer.iter().position(|&unit| unit == 0).unwrap_or(buffer.len());
        String::from_utf16_lossy(&buffer[..length])
    }
}
fn native(error: windows::core::Error) -> CaptureError {
    // Desktop switches (lock screen, secure prompts) can surface from any
    // duplication call, not only AcquireNextFrame. Report them as a target
    // change so the owner can wait for the same display to come back.
    // E_ACCESSDENIED: the thread is not on the desktop that is shown (for example a secure
    // desktop that this process may not read); the owner reattaches and tries again.
    if error.code() == DXGI_ERROR_ACCESS_LOST || error.code().0 as u32 == 0x8007_0005 {
        return CaptureError::Target;
    }
    CaptureError::Native(format!("Windows desktop capture failed ({})", error.code()))
}
#[derive(Clone, Debug)]
pub struct Display {
    pub adapter: u32,
    pub output: u32,
    pub name: String,
    pub left: i32,
    pub top: i32,
    pub width: u32,
    pub height: u32,
}
pub fn displays() -> Result<Vec<Display>, CaptureError> {
    // SAFETY: DXGI returns reference-counted interfaces and initialized records.
    let factory: IDXGIFactory1 = unsafe { CreateDXGIFactory1() }.map_err(native)?;
    let mut displays = Vec::new();
    for adapter_index in 0..32 {
        let adapter = match unsafe { factory.EnumAdapters1(adapter_index) } {
            Ok(adapter) => adapter,
            Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => break,
            Err(error) => return Err(native(error)),
        };
        for output_index in 0..32 {
            let output = match unsafe { adapter.EnumOutputs(output_index) } {
                Ok(output) => output,
                Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => break,
                Err(error) => return Err(native(error)),
            };
            let description = unsafe { output.GetDesc() }.map_err(native)?;
            if !description.AttachedToDesktop.as_bool() {
                continue;
            }
            let width = i64::from(description.DesktopCoordinates.right)
                - i64::from(description.DesktopCoordinates.left);
            let height = i64::from(description.DesktopCoordinates.bottom)
                - i64::from(description.DesktopCoordinates.top);
            if !(1..=8192).contains(&width) || !(1..=8192).contains(&height) {
                continue;
            }
            let end = description
                .DeviceName
                .iter()
                .position(|unit| *unit == 0)
                .unwrap_or(description.DeviceName.len());
            displays.push(Display {
                adapter: adapter_index,
                output: output_index,
                name: String::from_utf16_lossy(&description.DeviceName[..end]),
                left: description.DesktopCoordinates.left,
                top: description.DesktopCoordinates.top,
                width: width as u32,
                height: height as u32,
            });
        }
    }
    Ok(displays)
}
/// The display that now stands for `selected`: the same one when it is still there, otherwise the
/// only display left. A PC without a monitor of its own (the HP laptop in Mauritius, lid shut)
/// shows its desktop on TeamViewer's virtual monitor while a TeamViewer session runs
/// (\\.\DISPLAY23, 1920x1080) and on Windows' own stand-in display otherwise (\\.\DISPLAY1,
/// 1280x720): when TeamViewer ends, the display that was shared is gone and the picture froze.
/// With one display left there is only one desktop to show, so the session goes on with it;
/// with several, nothing is chosen for the person (the session waits, as before).
pub fn follow<'a>(selected: &Display, current: &'a [Display]) -> Option<&'a Display> {
    current
        .iter()
        .find(|screen| screen.name == selected.name && screen.adapter == selected.adapter && screen.output == selected.output)
        .or_else(|| current.iter().find(|screen| screen.name == selected.name))
        .or_else(|| (current.len() == 1).then(|| &current[0]))
}
/// Marks the display record that stands for the whole desktop across all monitors.
pub const ALL_DISPLAYS: u32 = u32::MAX;
/// The rectangle around all `displays`, as one capture and input target.
pub fn all_of(displays: &[Display]) -> Option<Display> {
    let left = displays.iter().map(|display| i64::from(display.left)).min()?;
    let top = displays.iter().map(|display| i64::from(display.top)).min()?;
    let right = displays.iter().map(|display| i64::from(display.left) + i64::from(display.width)).max()?;
    let bottom = displays.iter().map(|display| i64::from(display.top) + i64::from(display.height)).max()?;
    let (width, height) = (right - left, bottom - top);
    if !(2..=32768).contains(&width) || !(2..=16384).contains(&height) || width * height > 8192 * 8192 {
        return None;
    }
    Some(Display {
        adapter: ALL_DISPLAYS,
        output: 0,
        name: "all".into(),
        left: left as i32,
        top: top as i32,
        width: width as u32,
        height: height as u32,
    })
}
struct Acquired(Option<IDXGIOutputDuplication>);
impl Acquired {
    fn release(mut self) -> Result<(), CaptureError> {
        if let Some(duplication) = self.0.take() {
            unsafe { duplication.ReleaseFrame() }.map_err(native)?;
        }
        Ok(())
    }
}
impl Drop for Acquired {
    fn drop(&mut self) {
        if let Some(duplication) = self.0.take() {
            let _ = unsafe { duplication.ReleaseFrame() };
        }
    }
}
struct Mapped<'a> {
    context: &'a ID3D11DeviceContext,
    texture: &'a ID3D11Texture2D,
}
impl Drop for Mapped<'_> {
    fn drop(&mut self) {
        unsafe {
            self.context.Unmap(self.texture, 0);
        }
    }
}
/// The shortest time between two pictures when only GDI can copy the screen.
const GDI_ONLY_INTERVAL: Duration = Duration::from_millis(33);
/// Without pictures for this long, whether the display is still there is checked (this often).
const DISPLAY_CHECK: Duration = Duration::from_secs(2);
/// GDI-only capture asks for desktop duplication again this often.
const DUPLICATION_RETRY: Duration = Duration::from_secs(2);
/// Desktop duplication silent for this long: a GDI copy (this often) checks whether the screen
/// really is unchanged.
const STALE_PROBE: Duration = Duration::from_secs(1);

/// How the last picture was copied (for the perf log): 0 none yet, 1 desktop duplication, 2 GDI.
static LAST_METHOD: std::sync::atomic::AtomicU8 = std::sync::atomic::AtomicU8::new(0);

/// Why the screen copy could not be opened last time (for the app log): which step refused and
/// with what code. A session that only ever waits for the desktop to come back (after a
/// TeamViewer session the HP sent one picture and then nothing, 26 Nov 2026) said nothing in the
/// field logs before.
static OPEN_FAILURE: std::sync::Mutex<String> = std::sync::Mutex::new(String::new());
fn open_failed(step: &str, detail: impl std::fmt::Display) {
    if let Ok(mut last) = OPEN_FAILURE.lock() {
        *last = format!("{step}: {detail}");
    }
}
/// What refused the last open of the screen copy ("" when none did).
pub fn last_open_failure() -> String {
    OPEN_FAILURE.lock().map(|last| last.clone()).unwrap_or_default()
}

/// How the last picture was copied: "dxgi" (desktop duplication), "gdi" (slow full-screen copy).
pub fn capture_method() -> &'static str {
    match LAST_METHOD.load(std::sync::atomic::Ordering::Relaxed) {
        1 => "dxgi",
        2 => "gdi",
        _ => "none",
    }
}

pub struct WindowsCapture {
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    duplication: Option<IDXGIOutputDuplication>,
    output: IDXGIOutput1,
    display: Display,
    fallback: Option<gdi::GdiCapture>,
    received_visible_frame: bool,
    /// GDI-only capture (no desktop duplication): the last picture, to tell an unchanged screen.
    last_gdi_only: Option<Vec<u8>>,
    /// GDI-only capture: when the last copy was taken (copies are at most [`GDI_ONLY_INTERVAL`]
    /// apart) and when desktop duplication was last tried again.
    gdi_last: Option<std::time::Instant>,
    duplication_tried: std::time::Instant,
    /// When desktop duplication last gave a picture, and when it was last checked that the
    /// display is still there (a removed display gives no error, only no pictures).
    dxgi_last: std::time::Instant,
    present_checked: std::time::Instant,
    /// Desktop duplication silent: the last GDI probe picture and when it was taken. A display
    /// without a monitor (the HP in Mauritius with its lid closed, once TeamViewer's virtual
    /// monitor is gone) is granted duplication but never gives a picture, while GDI sees the
    /// screen change: the session showed one picture and then nothing (26 Nov 2026).
    stale_probe: Option<Vec<u8>>,
    stale_probed: std::time::Instant,
    /// Duplication was found silent on a changing screen: GDI from now on for this display.
    duplication_stale: bool,
    staging: Option<(u32, u32, ID3D11Texture2D)>,
}
impl WindowsCapture {
    pub fn open(display: &Display) -> Result<Self, CaptureError> {
        if display.adapter == ALL_DISPLAYS {
            // All monitors as one picture: copied from the desktop itself. The
            // first monitor's duplication only keeps this type uniform; it is not read.
            let list = displays()?;
            let all = all_of(&list).ok_or(CaptureError::Target)?;
            if (all.left, all.top, all.width, all.height)
                != (display.left, display.top, display.width, display.height)
            {
                return Err(CaptureError::Target);
            }
            let mut capture = Self::open(list.first().ok_or(CaptureError::Target)?)?;
            capture.fallback = Some(gdi::GdiCapture::open(&all)?);
            capture.display = all;
            capture.received_visible_frame = true;
            return Ok(capture);
        }
        let noted = |step: &'static str| move |error: windows::core::Error| {
            open_failed(step, error.code());
            native(error)
        };
        let factory: IDXGIFactory1 = unsafe { CreateDXGIFactory1() }.map_err(noted("factory"))?;
        let adapter = unsafe { factory.EnumAdapters1(display.adapter) }.map_err(noted("adapter"))?;
        let output = unsafe { adapter.EnumOutputs(display.output) }.map_err(noted("output"))?;
        let description = unsafe { output.GetDesc() }.map_err(noted("output description"))?;
        if !description.AttachedToDesktop.as_bool() {
            open_failed("output", "not attached to the desktop");
            return Err(CaptureError::Target);
        }
        // Re-resolve the selected display. A topology change must require a new
        // selection rather than silently sharing a different monitor.
        let end = description
            .DeviceName
            .iter()
            .position(|unit| *unit == 0)
            .unwrap_or(description.DeviceName.len());
        let shown_name = String::from_utf16_lossy(&description.DeviceName[..end]);
        if shown_name != display.name {
            open_failed("output", format_args!("is now {shown_name}, was {}", display.name));
            return Err(CaptureError::Target);
        }
        let adapter: IDXGIAdapter = adapter.cast().map_err(native)?;
        let mut device = None;
        let mut context = None;
        // SAFETY: output pointers address live initialized Options. An explicit
        // adapter requires UNKNOWN driver type and no software module.
        unsafe {
            D3D11CreateDevice(
                &adapter,
                D3D_DRIVER_TYPE_UNKNOWN,
                HMODULE::default(),
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                None,
                D3D11_SDK_VERSION,
                Some(&mut device),
                None,
                Some(&mut context),
            )
        }
        .map_err(noted("device"))?;
        let device =
            device.ok_or_else(|| CaptureError::Native("Windows returned no D3D device".into()))?;
        let context = context
            .ok_or_else(|| CaptureError::Native("Windows returned no D3D context".into()))?;
        let output: IDXGIOutput1 = output.cast().map_err(native)?;
        match unsafe { output.DuplicateOutput(&device) } {
            Ok(duplication) => Ok(Self {
                device,
                context,
                duplication: Some(duplication),
                output,
                display: display.clone(),
                fallback: None,
                received_visible_frame: false,
                last_gdi_only: None,
                gdi_last: None,
                duplication_tried: std::time::Instant::now(),
                dxgi_last: std::time::Instant::now(),
                present_checked: std::time::Instant::now(),
                stale_probe: None,
                stale_probed: std::time::Instant::now(),
                duplication_stale: false,
                staging: None,
            }),
            Err(error) => {
                open_failed("duplication", error.code());
                if gdi::default_input_desktop()
                    && let Ok(fallback) = gdi::GdiCapture::open(display)
                {
                    return Ok(Self {
                        device,
                        context,
                        duplication: None,
                        output,
                        display: display.clone(),
                        fallback: Some(fallback),
                        received_visible_frame: false,
                        last_gdi_only: None,
                        gdi_last: None,
                        duplication_tried: std::time::Instant::now(),
                        dxgi_last: std::time::Instant::now(),
                        present_checked: std::time::Instant::now(),
                        stale_probe: None,
                        stale_probed: std::time::Instant::now(),
                        duplication_stale: false,
                        staging: None,
                    });
                }
                Err(native(error))
            }
        }
    }
    /// Windows refused desktop duplication: every picture is a slow GDI copy of the screen.
    pub fn gdi_only(&self) -> bool {
        self.duplication.is_none() && self.display.adapter != ALL_DISPLAYS
    }
    /// A timeout is a normal unchanged-screen result. Desktop switches, lock
    /// screens and display removal fail closed; the caller must reselect/reopen.
    pub fn next_frame(&mut self, timeout: Duration) -> Result<Option<CapturedFrame>, CaptureError> {
        if self.display.adapter == ALL_DISPLAYS {
            std::thread::sleep(timeout.clamp(Duration::from_millis(1), Duration::from_millis(50)));
            let all = all_of(&displays()?).ok_or(CaptureError::Target)?;
            if (all.left, all.top, all.width, all.height)
                != (self.display.left, self.display.top, self.display.width, self.display.height)
            {
                return Err(CaptureError::Target);
            }
            return self
                .fallback
                .as_mut()
                .ok_or(CaptureError::Stopped)?
                .frame(&self.display)
                .map(Some);
        }
        if self.duplication.is_none() && !self.duplication_stale && self.duplication_tried.elapsed() >= DUPLICATION_RETRY {
            // Windows refused desktop duplication when the capture opened (the HP in Mauritius
            // after a TeamViewer session, 26 Nov 2026: every picture of every session was a
            // GDI copy, 5-6 a second). Ask again now and then: once it is granted the pictures
            // come from the graphics card again.
            self.duplication_tried = std::time::Instant::now();
            if let Ok(duplication) = unsafe { self.output.DuplicateOutput(&self.device) } {
                self.duplication = Some(duplication);
                self.fallback = None;
                self.last_gdi_only = None;
                self.received_visible_frame = false;
                open_failed("duplication", "granted again");
            }
        }
        let Some(duplication) = self.duplication.clone() else {
            // Windows refused desktop duplication for this display, so every picture is a GDI copy
            // of the whole screen (slow, and done on this thread). At most about 30 a second, and an
            // unchanged screen is no new picture: the snapshot of 2.0.5-2.0.14 copied and encoded the
            // whole screen every 8 ms whether anything changed or not. The wait counts from the last
            // copy: 2.2.13 slept the caller's whole timeout (100 ms) before every copy, so on top of
            // the copy itself no more than 5-6 pictures a second left the HP in Mauritius.
            let next = self.gdi_last.map_or(std::time::Instant::now(), |last| last + GDI_ONLY_INTERVAL);
            let wait = next.saturating_duration_since(std::time::Instant::now());
            if wait > timeout {
                std::thread::sleep(timeout);
                return Ok(None);
            }
            std::thread::sleep(wait);
            self.gdi_last = Some(std::time::Instant::now());
            let frame = self.fallback_frame()?;
            if self.last_gdi_only.as_deref() == Some(frame.pixels()) {
                return Ok(None);
            }
            self.last_gdi_only = Some(frame.pixels().to_vec());
            return Ok(Some(frame));
        };
        let mut info = DXGI_OUTDUPL_FRAME_INFO::default();
        let mut resource = None;
        let milliseconds = timeout
            .as_millis()
            .clamp(1, if !self.received_visible_frame { 25 } else { 50 })
            as u32;
        match unsafe {
            duplication
                .AcquireNextFrame(milliseconds, &mut info, &mut resource)
        } {
            Ok(()) => {
                LAST_METHOD.store(1, std::sync::atomic::Ordering::Relaxed);
                self.dxgi_last = std::time::Instant::now();
                self.stale_probe = None;
            }
            Err(error) if error.code() == DXGI_ERROR_WAIT_TIMEOUT => {
                // No picture for a while: a display that was removed (TeamViewer's virtual monitor
                // when its session ends) says nothing, it only stops giving pictures. Check that
                // it is still there now and then, and let the owner reopen when it is not.
                if self.dxgi_last.elapsed() >= DISPLAY_CHECK && self.present_checked.elapsed() >= DISPLAY_CHECK {
                    self.present_checked = std::time::Instant::now();
                    let still = displays().is_ok_and(|current| {
                        current.iter().any(|screen| {
                            screen.name == self.display.name
                                && (screen.left, screen.top, screen.width, screen.height)
                                    == (self.display.left, self.display.top, self.display.width, self.display.height)
                        })
                    });
                    if !still {
                        open_failed("output", format_args!("display {} is gone or changed", self.display.name));
                        return Err(CaptureError::Target);
                    }
                }
                if self.received_visible_frame
                    && self.dxgi_last.elapsed() >= STALE_PROBE
                    && self.stale_probed.elapsed() >= STALE_PROBE
                    && gdi::default_input_desktop()
                {
                    self.stale_probed = std::time::Instant::now();
                    if let Ok(probe) = gdi::GdiCapture::open(&self.display) {
                        self.fallback = Some(probe);
                        let frame = self.fallback_frame();
                        let frame = match frame {
                            Ok(frame) => frame,
                            Err(error) => {
                                self.fallback = None;
                                return Err(error);
                            }
                        };
                        let changed = self.stale_probe.as_deref().is_some_and(|last| last != frame.pixels());
                        if changed {
                            open_failed("duplication", "gives no pictures while the screen changes; copying by GDI");
                            self.duplication = None;
                            self.duplication_stale = true;
                            self.stale_probe = None;
                            self.gdi_last = Some(std::time::Instant::now());
                            self.last_gdi_only = Some(frame.pixels().to_vec());
                            return Ok(Some(frame));
                        }
                        self.stale_probe = Some(frame.pixels().to_vec());
                        self.fallback = None;
                    }
                    return Ok(None);
                }
                if !self.received_visible_frame
                    && self.fallback.is_none()
                    && gdi::default_input_desktop()
                {
                    self.fallback = gdi::GdiCapture::open(&self.display).ok();
                }
                return if self.fallback.is_some() {
                    let frame = self.fallback_frame()?;
                    self.received_visible_frame = true;
                    self.fallback = None;
                    Ok(Some(frame))
                } else {
                    Ok(None)
                };
            }
            Err(error) if error.code() == DXGI_ERROR_ACCESS_LOST => {
                return Err(CaptureError::Target);
            }
            Err(error) => return Err(native(error)),
        }
        let acquired = Acquired(Some(duplication.clone()));
        let resource = resource
            .ok_or_else(|| CaptureError::Native("Windows returned no desktop resource".into()))?;
        let texture: ID3D11Texture2D = resource.cast().map_err(native)?;
        let mut description = D3D11_TEXTURE2D_DESC::default();
        unsafe {
            texture.GetDesc(&mut description);
        }
        let width = description.Width as usize;
        let height = description.Height as usize;
        if width == 0
            || height == 0
            || width > 8192
            || height > 8192
            || description.Format != DXGI_FORMAT_B8G8R8A8_UNORM
            || description.SampleDesc.Count != 1
        {
            return Err(CaptureError::Native(
                "Unsupported desktop texture layout".into(),
            ));
        }
        let stride = width
            .checked_mul(4)
            .ok_or_else(|| CaptureError::Native("Desktop row overflow".into()))?;
        let length = stride
            .checked_mul(height)
            .filter(|bytes| *bytes <= 256 * 1024 * 1024)
            .ok_or_else(|| CaptureError::Native("Desktop frame exceeds limit".into()))?;
        if !self
            .staging
            .as_ref()
            .is_some_and(|(w, h, _)| *w == description.Width && *h == description.Height)
        {
            description.Usage = D3D11_USAGE_STAGING;
            description.BindFlags = 0;
            description.CPUAccessFlags = D3D11_CPU_ACCESS_READ.0 as u32;
            description.MiscFlags = 0;
            description.MipLevels = 1;
            description.ArraySize = 1;
            let mut staging = None;
            unsafe {
                self.device
                    .CreateTexture2D(&description, None, Some(&mut staging))
            }
            .map_err(native)?;
            self.staging = Some((
                description.Width,
                description.Height,
                staging.ok_or_else(|| {
                    CaptureError::Native("Windows returned no staging texture".into())
                })?,
            ));
        }
        let staging = &self.staging.as_ref().ok_or(CaptureError::Stopped)?.2;
        unsafe {
            self.context.CopyResource(staging, &texture);
        }
        let mut mapping = D3D11_MAPPED_SUBRESOURCE::default();
        unsafe {
            self.context
                .Map(staging, 0, D3D11_MAP_READ, 0, Some(&mut mapping))
        }
        .map_err(native)?;
        let mapped = Mapped {
            context: &self.context,
            texture: staging,
        };
        let pitch = mapping.RowPitch as usize;
        if mapping.pData.is_null()
            || pitch < stride
            || pitch
                .checked_mul(height)
                .is_none_or(|bytes| bytes > 256 * 1024 * 1024)
        {
            return Err(CaptureError::Native("Invalid mapped desktop layout".into()));
        }
        let mut pixels = Zeroizing::new(vec![0; length]);
        if pitch == stride {
            // Fast path: tightly packed DXGI staging texture — single contiguous copy.
            let source = unsafe { std::slice::from_raw_parts(mapping.pData.cast::<u8>(), length) };
            pixels.copy_from_slice(source);
        } else {
            for row in 0..height {
                // SAFETY: Map guarantees RowPitch rows for this texture. Bounds and
                // products were checked above; copy active pixels only, never padding.
                let source = unsafe {
                    std::slice::from_raw_parts(mapping.pData.cast::<u8>().add(row * pitch), stride)
                };
                pixels[row * stride..(row + 1) * stride].copy_from_slice(source);
            }
        }
        drop(mapped);
        let black = (!self.received_visible_frame || self.fallback.is_some())
            && pixels
                .as_chunks::<4>()
                .0
                .iter()
                .all(|pixel| pixel[..3] == [0, 0, 0]);
        if info.ProtectedContentMaskedOut.as_bool() {
            self.fallback = None;
        } else if black && !self.received_visible_frame {
            if self.fallback.is_none() && gdi::default_input_desktop() {
                self.fallback = gdi::GdiCapture::open(&self.display).ok();
            }
            if self.fallback.is_some() {
                acquired.release()?;
                return self.fallback_frame().map(Some);
            }
        } else if !black {
            self.received_visible_frame = true;
            self.fallback = None;
        }
        let rotation = unsafe { duplication.GetDesc() }.Rotation;
        let (out_width, out_height, rotated) = if rotation == DXGI_MODE_ROTATION_IDENTITY
            || rotation == DXGI_MODE_ROTATION_UNSPECIFIED
        {
            (width, height, std::mem::take(&mut *pixels))
        } else {
            rotate(width, height, &pixels, rotation)?
        };
        acquired.release()?;
        CapturedFrame::bgra(out_width, out_height, out_width * 4, rotated)
            .map(Some)
            .map_err(|error| CaptureError::Native(error.to_string()))
    }
    fn fallback_frame(&mut self) -> Result<CapturedFrame, CaptureError> {
        LAST_METHOD.store(2, std::sync::atomic::Ordering::Relaxed);
        if !gdi::default_input_desktop() {
            return Err(CaptureError::Target);
        }
        let desc = unsafe { self.output.GetDesc() }.map_err(native)?;
        let bounds = desc.DesktopCoordinates;
        if !desc.AttachedToDesktop.as_bool()
            || bounds.left != self.display.left
            || bounds.top != self.display.top
            || i64::from(bounds.right) - i64::from(bounds.left) != i64::from(self.display.width)
            || i64::from(bounds.bottom) - i64::from(bounds.top) != i64::from(self.display.height)
        {
            return Err(CaptureError::Target);
        }
        self.fallback
            .as_mut()
            .ok_or(CaptureError::Stopped)?
            .frame(&self.display)
    }
}
fn rotate(
    width: usize,
    height: usize,
    pixels: &[u8],
    rotation: DXGI_MODE_ROTATION,
) -> Result<(usize, usize, Vec<u8>), CaptureError> {
    let quarter =
        rotation == DXGI_MODE_ROTATION_ROTATE90 || rotation == DXGI_MODE_ROTATION_ROTATE270;
    let (out_width, out_height) = if quarter {
        (height, width)
    } else {
        (width, height)
    };
    if rotation != DXGI_MODE_ROTATION_IDENTITY
        && rotation != DXGI_MODE_ROTATION_UNSPECIFIED
        && !quarter
        && rotation != DXGI_MODE_ROTATION_ROTATE180
    {
        return Err(CaptureError::Native("Unsupported display rotation".into()));
    }
    let mut output = vec![0; pixels.len()];
    for y in 0..height {
        for x in 0..width {
            let (dx, dy) = if rotation == DXGI_MODE_ROTATION_ROTATE90 {
                (height - 1 - y, x)
            } else if rotation == DXGI_MODE_ROTATION_ROTATE180 {
                (width - 1 - x, height - 1 - y)
            } else if rotation == DXGI_MODE_ROTATION_ROTATE270 {
                (y, width - 1 - x)
            } else {
                (x, y)
            };
            output[(dy * out_width + dx) * 4..(dy * out_width + dx) * 4 + 4]
                .copy_from_slice(&pixels[(y * width + x) * 4..(y * width + x) * 4 + 4]);
        }
    }
    Ok((out_width, out_height, output))
}
