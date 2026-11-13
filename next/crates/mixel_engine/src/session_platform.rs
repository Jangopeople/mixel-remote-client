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

/// Applies capture changes requested elsewhere (macOS: windows left out of the picture).
pub fn refresh(capture: &mut Capture) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    return capture.sync_exclusions().map_err(|error| error.to_string());
    #[allow(unreachable_code)]
    {
        let _ = capture;
        Ok(())
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

/// Full displays a technician with keyboard and mouse control may switch between during a
/// session, in the order the input layer numbers them. Windows only; empty when it does not apply.
pub fn switchable_displays(source: &str, all: bool) -> Vec<String> {
    #[cfg(target_os = "windows")]
    {
        if !is_display(source) {
            return Vec::new();
        }
        let ids: Vec<String> = switch_list(all).iter().map(display_id).collect();
        if ids.len() > 1 && ids.iter().any(|id| id == source) { ids } else { Vec::new() }
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = (source, all);
        Vec::new()
    }
}

/// The main display as a capture source, for showing this screen without a picker (switching
/// sides after pressing "Switch sides" here). `None` where the system asks the user to choose.
pub fn main_display_source() -> Option<String> {
    #[cfg(target_os = "macos")]
    {
        native_os_capture::macos::screen_access()
            .then(|| format!("display:{}", native_os_capture::macos::main_display()))
            .filter(|source| is_display(source))
    }
    #[cfg(target_os = "windows")]
    {
        native_os_capture::windows_capture::displays()
            .ok()?
            .first()
            .map(|display| format!("dxgi:{}:{}:{}", display.adapter, display.output, display.name))
    }
    #[cfg(target_os = "linux")]
    {
        if crate::wayland_portal::available() {
            return None;
        }
        native_os_capture::x11_capture::displays()
            .ok()?
            .first()
            .map(|display| format!("x11:{}:{}", display.screen, display.root))
    }
}

/// The source name of "all monitors as one picture".
pub const ALL_DISPLAYS: &str = "dxgi-all";

#[cfg(target_os = "windows")]
fn display_id(display: &crate::PlatformDisplay) -> String {
    if display.adapter == native_os_capture::windows_capture::ALL_DISPLAYS {
        ALL_DISPLAYS.into()
    } else {
        format!("dxgi:{}:{}:{}", display.adapter, display.output, display.name)
    }
}

/// The displays offered for switching, in order; with `all`, the whole desktop comes last.
#[cfg(target_os = "windows")]
fn switch_list(all: bool) -> Vec<crate::PlatformDisplay> {
    let Ok(mut list) = native_os_capture::windows_capture::displays() else {
        return Vec::new();
    };
    // Lab builds only: offer the one VM display twice, to exercise switching end to end.
    #[cfg(feature = "qa-session")]
    if list.len() == 1 && std::env::var_os("MIXEL_QA_SECOND_DISPLAY").is_some() {
        list.push(list[0].clone());
    }
    if all
        && list.len() > 1
        && let Some(whole) = native_os_capture::windows_capture::all_of(&list)
    {
        list.push(whole);
    }
    list
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
    if source == ALL_DISPLAYS {
        return native_os_capture::windows_capture::displays()
            .ok()
            .and_then(|list| native_os_capture::windows_capture::all_of(&list))
            .ok_or_else(|| "The displays are unavailable".into());
    }
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
        _all_displays: bool,
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
        let platform = if _all_displays {
            PlatformInjector::with_displays(switch_list(true), cancel)
        } else {
            PlatformInjector::display(display, cancel)
        };
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
        // The SYSTEM host follows the desktop on screen (sign-in, lock screen, security prompts).
        #[cfg(target_os = "windows")]
        if crate::windows_signin_host::is_signin_host() {
            native_os_capture::windows_capture::attach_input_desktop();
        }
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

// ---- Rich clipboard: pictures and small files (feature `clip2`) ----
use core_protocol::clipboard::{Content, MAX_FILES, MAX_RICH, file_name_ok};

fn rich_clipboard<T>(
    op: impl FnOnce(&mut arboard::Clipboard) -> Result<T, arboard::Error>,
) -> Result<T, arboard::Error> {
    #[cfg(not(target_os = "macos"))]
    {
        let mut slot = CLIPBOARD.lock().map_err(|_| arboard::Error::ClipboardOccupied)?;
        if slot.is_none() {
            *slot = Some(arboard::Clipboard::new()?);
        }
        op(slot.as_mut().ok_or(arboard::Error::ClipboardOccupied)?)
    }
    #[cfg(target_os = "macos")]
    {
        op(&mut arboard::Clipboard::new()?)
    }
}

/// What the local clipboard holds: files first, then text, then a picture.
/// Without `rich` (older app on the other side) only plain text is read.
pub fn read_content(rich: bool) -> Result<Content, String> {
    if !rich {
        return read_text().map(Content::Text);
    }
    if let Ok(paths) = rich_clipboard(|clipboard| clipboard.get().file_list())
        && !paths.is_empty()
    {
        return read_files(&paths).map(Content::Files);
    }
    if let Ok(text) = read_text()
        && !text.is_empty()
    {
        return Ok(Content::Text(text));
    }
    let picture = rich_clipboard(|clipboard| clipboard.get_image())
        .map_err(|_| "The clipboard holds no text, picture or files")?;
    let (width, height) = (picture.width as u32, picture.height as u32);
    let pixels = image::RgbaImage::from_raw(width, height, picture.bytes.into_owned())
        .ok_or("The clipboard picture could not be read")?;
    let mut png = std::io::Cursor::new(Vec::new());
    pixels
        .write_to(&mut png, image::ImageFormat::Png)
        .map_err(|_| "The clipboard picture could not be read")?;
    let png = png.into_inner();
    if png.len() + 2 > MAX_RICH {
        return Err("The clipboard picture is larger than 32 MB".into());
    }
    Ok(Content::Image(png))
}

fn read_files(paths: &[std::path::PathBuf]) -> Result<Vec<(String, Vec<u8>)>, String> {
    const HINT: &str = "use the Files tab for folders, many files or more than 32 MB";
    if paths.len() > MAX_FILES {
        return Err(format!("The clipboard holds too many files; {HINT}"));
    }
    let mut total = 0usize;
    let mut files = Vec::with_capacity(paths.len());
    for path in paths {
        let metadata = std::fs::metadata(path).map_err(|_| "A copied file can no longer be read")?;
        if !metadata.is_file() {
            return Err(format!("The clipboard holds a folder; {HINT}"));
        }
        total = total.saturating_add(metadata.len() as usize + 300);
        if total > MAX_RICH - 16 {
            return Err(format!("The copied files are too large; {HINT}"));
        }
        let name = path
            .file_name()
            .and_then(|name| name.to_str())
            .filter(|name| file_name_ok(name))
            .ok_or("A copied file has a name that cannot be transferred")?
            .to_owned();
        if files.iter().any(|(other, _): &(String, Vec<u8>)| other.eq_ignore_ascii_case(&name)) {
            return Err("Two copied files have the same name".into());
        }
        let data = std::fs::read(path).map_err(|_| "A copied file can no longer be read")?;
        if data.len() as u64 != metadata.len() {
            return Err("A copied file changed while it was read".into());
        }
        files.push((name, data));
    }
    Ok(files)
}

fn clipboard_folder() -> std::path::PathBuf {
    std::env::temp_dir().join("Mixel Remote Clipboard")
}

/// Removes files received through the clipboard in earlier transfers.
pub fn clear_clipboard_files() {
    let _ = std::fs::remove_dir_all(clipboard_folder());
}

/// Puts received content on the local clipboard. Files are stored in a private
/// temporary folder first, then offered for pasting.
pub fn write_content(content: Content) -> Result<(), String> {
    match content {
        Content::Text(text) => write_text(&text),
        Content::Image(png) => {
            let picture = image::load_from_memory_with_format(&png, image::ImageFormat::Png)
                .map_err(|_| "The received picture is damaged")?
                .into_rgba8();
            let (width, height) = picture.dimensions();
            if width == 0 || height == 0 || u64::from(width) * u64::from(height) > 100_000_000 {
                return Err("The received picture is too large".into());
            }
            rich_clipboard(|clipboard| {
                clipboard.set_image(arboard::ImageData {
                    width: width as usize,
                    height: height as usize,
                    bytes: picture.into_raw().into(),
                })
            })
            .map_err(|_| "The local clipboard did not accept the picture".into())
        }
        Content::Files(files) => {
            clear_clipboard_files();
            let stamp = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|value| value.as_millis())
                .unwrap_or(0);
            let folder = clipboard_folder().join(stamp.to_string());
            std::fs::create_dir_all(&folder).map_err(|_| "Cannot store the received files")?;
            let mut paths = Vec::with_capacity(files.len());
            for (name, data) in files {
                if !file_name_ok(&name) {
                    return Err("A received file has an unsafe name".into());
                }
                let path = folder.join(&name);
                std::fs::write(&path, data).map_err(|_| "Cannot store the received files")?;
                paths.push(path);
            }
            rich_clipboard(|clipboard| clipboard.set().file_list(&paths))
                .map_err(|_| "The local clipboard did not accept the files".into())
        }
    }
}
