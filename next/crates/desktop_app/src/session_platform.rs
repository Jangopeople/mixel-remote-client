//! Session-only platform boundary. The broker and authenticated grants never
//! call capture or input before the local host has accepted a selected display.
#[cfg(any(target_os = "windows", target_os = "linux"))]
pub use native_os_capture::desktop_h264::{H264Decoder, ScreenH264Encoder};
use native_os_capture::input::{InjectionError, Injector};
#[cfg(target_os = "macos")]
pub use native_os_capture::{macos_decoder::H264Decoder, macos_screen_encoder::ScreenH264Encoder};
use std::sync::{Arc, atomic::AtomicBool};
#[cfg(target_os = "macos")]
pub type Capture = native_os_capture::macos::MacStream;
#[cfg(target_os = "windows")]
pub type Capture = crate::Worker;
#[cfg(target_os = "linux")]
pub enum Capture {
    X11(crate::Worker),
    Wayland(crate::wayland_portal::Capture),
}

#[cfg(target_os = "linux")]
impl Capture {
    pub fn stop(&mut self) -> Result<(), String> {
        match self {
            Self::X11(capture) => capture.stop(),
            Self::Wayland(capture) => capture.stop(),
        }
    }
}

pub fn frames(capture: &Capture) -> &native_os_capture::stream::FrameMailbox {
    #[cfg(target_os = "linux")]
    {
        match capture {
            Capture::X11(capture) => &capture.frames,
            Capture::Wayland(capture) => &capture.frames,
        }
    }
    #[cfg(not(target_os = "linux"))]
    {
        &capture.frames
    }
}

pub fn relay_fixture_enabled() -> bool {
    #[cfg(target_os = "macos")]
    {
        crate::service_config::relay_fixture_enabled()
    }
    #[cfg(not(target_os = "macos"))]
    {
        false
    }
}

pub fn login_window_mode() -> bool {
    #[cfg(target_os = "macos")]
    {
        crate::device_identity::prelogin_bundle().is_some()
    }
    #[cfg(not(target_os = "macos"))]
    {
        false
    }
}

pub fn at_login_window() -> bool {
    #[cfg(target_os = "macos")]
    {
        native_os_capture::macos::is_login_window_console()
    }
    #[cfg(not(target_os = "macos"))]
    {
        false
    }
}

pub fn capture_access() -> bool {
    #[cfg(target_os = "macos")]
    {
        native_os_capture::macos::screen_access()
    }
    #[cfg(target_os = "windows")]
    {
        true
    }
    #[cfg(target_os = "linux")]
    {
        crate::wayland_portal::available() || native_os_capture::x11_capture::session_available()
    }
}

pub fn input_access() -> bool {
    #[cfg(target_os = "macos")]
    {
        native_os_capture::macos_input::MacInjector::post_access()
    }
    #[cfg(not(target_os = "macos"))]
    {
        capture_access()
    }
}

pub fn is_display(source: &str) -> bool {
    #[cfg(target_os = "macos")]
    {
        source
            .strip_prefix("display:")
            .is_some_and(|id| id.parse::<u32>().is_ok())
    }
    #[cfg(target_os = "windows")]
    {
        source.strip_prefix("dxgi:").is_some_and(|id| {
            let mut fields = id.splitn(3, ':');
            fields
                .next()
                .is_some_and(|part| part.parse::<u32>().is_ok())
                && fields
                    .next()
                    .is_some_and(|part| part.parse::<u32>().is_ok())
                && fields
                    .next()
                    .is_some_and(|part| !part.is_empty() && part.len() <= 128)
        })
    }
    #[cfg(target_os = "linux")]
    {
        if source == crate::wayland_portal::SOURCE {
            return crate::wayland_portal::available();
        }
        source
            .strip_prefix("x11:")
            .and_then(|id| id.split_once(':'))
            .is_some_and(|(screen, root)| {
                screen.parse::<usize>().is_ok() && root.parse::<u32>().is_ok()
            })
    }
}

#[cfg(target_os = "macos")]
fn selected(source: &str) -> Result<native_os_capture::macos::Display, String> {
    let id: u32 = source
        .strip_prefix("display:")
        .and_then(|value| value.parse().ok())
        .ok_or("The selected display is invalid")?;
    native_os_capture::macos::displays()
        .map_err(|error| error.to_string())?
        .into_iter()
        .find(|display| display.id == id)
        .ok_or_else(|| "The selected display is unavailable".into())
}

#[cfg(target_os = "windows")]
pub fn selected(source: &str) -> Result<crate::PlatformDisplay, String> {
    native_os_capture::windows_capture::displays()
        .map_err(|error| error.to_string())?
        .into_iter()
        .find(|display| {
            format!(
                "dxgi:{}:{}:{}",
                display.adapter, display.output, display.name
            ) == source
        })
        .ok_or_else(|| "The selected display is unavailable".into())
}

#[cfg(target_os = "linux")]
pub fn selected(source: &str) -> Result<crate::PlatformDisplay, String> {
    native_os_capture::x11_capture::displays()
        .map_err(|error| error.to_string())?
        .into_iter()
        .find(|display| format!("x11:{}:{}", display.screen, display.root) == source)
        .ok_or_else(|| "The selected X11 screen is unavailable".into())
}

pub fn display_size(source: &str) -> Result<(usize, usize), String> {
    let display = selected(source)?;
    #[cfg(target_os = "macos")]
    {
        Ok((display.width, display.height))
    }
    #[cfg(any(target_os = "windows", target_os = "linux"))]
    {
        Ok((display.width as usize, display.height as usize))
    }
}

pub fn capture(
    source: &str,
    _size: (usize, usize),
    #[cfg(target_os = "linux")] portal: Option<Arc<crate::wayland_portal::Grant>>,
) -> Result<Capture, String> {
    #[cfg(target_os = "macos")]
    {
        let (width, height) = _size;
        let stream = if source == "qa-relay-fixture" && relay_fixture_enabled() {
            let window = native_os_capture::macos::own_windows()
                .map_err(|error| error.to_string())?
                .into_iter()
                .find(|window| window.title == "Mixel-Remote Capture Fixture")
                .ok_or("The QA test window is unavailable")?;
            Capture::start_own_window(window.id, width, height, 30)
        } else {
            Capture::start(selected(source)?.id, width, height, 30)
        };
        stream.map_err(|error| error.to_string())
    }
    #[cfg(target_os = "windows")]
    {
        Capture::start(selected(source)?)
    }
    #[cfg(target_os = "linux")]
    {
        if source == crate::wayland_portal::SOURCE {
            return crate::wayland_portal::Capture::start(
                portal.ok_or("A compositor sharing grant is required")?,
                _size,
            )
            .map(Capture::Wayland);
        }
        crate::Worker::start(selected(source)?).map(Capture::X11)
    }
}

#[cfg(target_os = "macos")]
type PlatformInjector = native_os_capture::macos_input::MacInjector;
#[cfg(target_os = "windows")]
type PlatformInjector = native_os_capture::windows_input::WindowsInjector;
#[cfg(target_os = "linux")]
enum PlatformInjector {
    X11(Box<native_os_capture::x11_input::X11Injector>),
    Wayland(crate::wayland_portal::PortalInjector),
}
#[cfg(target_os = "linux")]
impl Injector for PlatformInjector {
    fn inject(&mut self, event: &core_protocol::input::Input<'_>) -> Result<(), InjectionError> {
        match self {
            Self::X11(injector) => injector.inject(event),
            Self::Wayland(injector) => injector.inject(event),
        }
    }
}

pub struct SessionInjector {
    platform: PlatformInjector,
    authorized: Arc<dyn Fn() -> bool + Send + Sync>,
}

impl SessionInjector {
    pub fn open(
        source: &str,
        _login_window: bool,
        cancel: Arc<AtomicBool>,
        authorized: Arc<dyn Fn() -> bool + Send + Sync>,
        #[cfg(target_os = "linux")] portal: Option<Arc<crate::wayland_portal::Grant>>,
    ) -> Result<Self, String> {
        #[cfg(not(target_os = "linux"))]
        let display = selected(source)?;
        #[cfg(target_os = "macos")]
        let platform = if _login_window {
            PlatformInjector::login_display(display.id)
        } else {
            PlatformInjector::display(display.id)
        }
        .map(|injector| {
            injector
                .with_cancellation(cancel)
                .with_authorization(authorized.clone())
        });
        #[cfg(target_os = "windows")]
        let platform = PlatformInjector::display(display, cancel);
        #[cfg(target_os = "linux")]
        let platform = if source == crate::wayland_portal::SOURCE {
            let grant = portal.ok_or("A compositor control grant is required")?;
            Ok(PlatformInjector::Wayland(
                crate::wayland_portal::PortalInjector::new(grant, authorized.clone())?,
            ))
        } else {
            native_os_capture::x11_input::X11Injector::display(selected(source)?, cancel)
                .map(Box::new)
                .map(PlatformInjector::X11)
        };
        Ok(Self {
            platform: platform.map_err(|error| error.to_string())?,
            authorized,
        })
    }
}

impl Injector for SessionInjector {
    fn inject(&mut self, event: &core_protocol::input::Input<'_>) -> Result<(), InjectionError> {
        use core_protocol::input::Input;
        if !matches!(
            event,
            Input::Key { down: false, .. }
                | Input::Button { down: false, .. }
                | Input::ButtonClick { down: false, .. }
                | Input::ReleaseAll
        ) && !(self.authorized)()
        {
            return Err(InjectionError::Permission);
        }
        self.platform.inject(event)
    }
}

#[cfg(target_os = "macos")]
pub use native_os_capture::macos_clipboard::{read_text, write_text};

#[cfg(not(target_os = "macos"))]
static CLIPBOARD: std::sync::Mutex<Option<arboard::Clipboard>> = std::sync::Mutex::new(None);

#[cfg(not(target_os = "macos"))]
fn with_clipboard<T>(
    op: impl FnOnce(&mut arboard::Clipboard) -> Result<T, arboard::Error>,
) -> Result<T, String> {
    let mut slot = CLIPBOARD
        .lock()
        .map_err(|_| "The local clipboard is unavailable")?;
    if slot.is_none() {
        *slot = Some(arboard::Clipboard::new().map_err(|_| "The local clipboard is unavailable")?);
    }
    op(slot.as_mut().ok_or("The local clipboard is unavailable")?)
        .map_err(|_| "The local clipboard could not complete this operation".into())
}

#[cfg(not(target_os = "macos"))]
pub fn read_text() -> Result<zeroize::Zeroizing<String>, String> {
    let text = zeroize::Zeroizing::new(with_clipboard(arboard::Clipboard::get_text)?);
    if text.len() > 1024 * 1024 || text.contains('\0') {
        return Err("The clipboard does not contain supported plain text".into());
    }
    Ok(text)
}

#[cfg(not(target_os = "macos"))]
pub fn write_text(text: &str) -> Result<(), String> {
    if text.len() > 1024 * 1024 || text.contains('\0') {
        return Err("The clipboard does not contain supported plain text".into());
    }
    with_clipboard(|clipboard| clipboard.set_text(text))
}

#[cfg(not(target_os = "macos"))]
pub fn close_clipboard() {
    if let Ok(mut slot) = CLIPBOARD.lock() {
        slot.take();
    }
}
