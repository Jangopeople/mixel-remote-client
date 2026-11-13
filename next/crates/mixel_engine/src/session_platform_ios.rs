//! iPhone and iPad: the controller side only. The phone shows and controls other computers; it is
//! never captured or controlled itself (Mixel Quick Support shares a phone screen), so the host
//! parts of the desktop platform boundary have no implementation here and refuse.
use native_os_capture::input::{InjectionError, Injector};
pub use native_os_capture::macos_decoder::H264Decoder;
use std::sync::{Arc, atomic::AtomicBool};

const CONTROLLER_ONLY: &str = "This iPhone or iPad controls other computers; it cannot be shared or controlled itself.";

/// No capture exists on the phone.
pub enum Capture {}
impl Capture {
    pub fn stop(&mut self) -> Result<(), String> {
        match *self {}
    }
}

pub fn refresh(capture: &mut Capture) -> Result<(), String> {
    match *capture {}
}
pub fn frames(capture: &Capture) -> &native_os_capture::stream::FrameMailbox {
    match *capture {}
}
pub fn relay_fixture_enabled() -> bool {
    false
}
pub fn login_window_mode() -> bool {
    false
}
pub fn at_login_window() -> bool {
    false
}
pub fn capture_access() -> bool {
    false
}
pub fn input_access() -> bool {
    false
}
pub fn is_display(_source: &str) -> bool {
    false
}
pub fn switchable_displays(_source: &str, _all: bool) -> Vec<String> {
    Vec::new()
}
pub fn main_display_source() -> Option<String> {
    None
}
pub const ALL_DISPLAYS: &str = "dxgi-all";
pub fn display_size(_source: &str) -> Result<(usize, usize), String> {
    Err(CONTROLLER_ONLY.into())
}
pub fn capture(_source: &str, _size: (usize, usize)) -> Result<Capture, String> {
    Err(CONTROLLER_ONLY.into())
}

/// The phone never encodes its own screen in a session (switching sides is refused before).
pub struct ScreenH264Encoder;
impl ScreenH264Encoder {
    pub fn new(_width: usize, _height: usize, _fps: u32, _bitrate: u32) -> Result<Self, String> {
        Err(CONTROLLER_ONLY.into())
    }
    pub fn needs_retry(&self) -> bool {
        false
    }
    pub fn encode(
        &mut self,
        _frame: &native_os_capture::stream::CapturedFrame,
        _timestamp: u64,
        _keyframe: bool,
    ) -> Result<Option<native_os_capture::h264::EncodedH264>, String> {
        Err(CONTROLLER_ONLY.into())
    }
}

pub struct SessionInjector;
impl SessionInjector {
    pub fn open(
        _source: &str,
        _login_window: bool,
        _all_displays: bool,
        _cancel: Arc<AtomicBool>,
        _authorized: Arc<dyn Fn() -> bool + Send + Sync>,
    ) -> Result<Self, String> {
        Err(CONTROLLER_ONLY.into())
    }
}
impl Injector for SessionInjector {
    fn inject(&mut self, _event: &core_protocol::input::Input<'_>) -> Result<(), InjectionError> {
        Err(InjectionError::Permission)
    }
}

// The phone app does not exchange clipboards (a desktop feature).
const NO_CLIPBOARD: &str = "The clipboard is available between computers only.";
pub fn read_text() -> Result<zeroize::Zeroizing<String>, String> {
    Err(NO_CLIPBOARD.into())
}
pub fn write_text(_text: &str) -> Result<(), String> {
    Err(NO_CLIPBOARD.into())
}
pub fn close_clipboard() {}
pub fn read_content(_rich: bool) -> Result<core_protocol::clipboard::Content, String> {
    Err(NO_CLIPBOARD.into())
}
pub fn write_content(_content: core_protocol::clipboard::Content) -> Result<(), String> {
    Err(NO_CLIPBOARD.into())
}
pub fn clear_clipboard_files() {}
