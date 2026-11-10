//! CoreGraphics input. Desktop construction and every global post preflight OS
//! permission. Own-process QA cannot address another PID or inject globally.
use crate::input::{InjectionError, Injector};
use core_protocol::input::{self, Input};
use objc2_core_foundation::{CFRetained, CGPoint};
use objc2_core_graphics::*;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};

enum Target {
    Desktop(Vec<u32>),
    OwnProcess,
}
pub struct MacInjector {
    source: CFRetained<CGEventSource>,
    target: Target,
    login_keyboard: Option<crate::macos_login_keyboard::LoginKeyboard>,
    keys: BTreeSet<u16>,
    buttons: BTreeSet<u8>,
    clicks: BTreeMap<u8, u8>,
    caps: bool,
    point: CGPoint,
    cancellation: Arc<AtomicBool>,
    authorization: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
}
impl MacInjector {
    pub fn login_keyboard_entitled() -> bool {
        crate::macos_login_keyboard::entitled()
    }

    pub fn post_access() -> bool {
        CGPreflightPostEventAccess()
    }
    /// Call only from an explicit local user's permission action. macOS owns
    /// the approval dialog; this does not grant or bypass the OS permission.
    pub fn request_post_access() -> bool {
        CGRequestPostEventAccess()
    }
    pub fn desktop() -> Result<Self, InjectionError> {
        if !CGPreflightPostEventAccess() {
            return Err(InjectionError::Permission);
        }
        let mut displays = [0; 32];
        let mut count = 0;
        // SAFETY: both output pointers address initialized, correctly sized storage.
        let error = unsafe {
            CGGetActiveDisplayList(displays.len() as u32, displays.as_mut_ptr(), &mut count)
        };
        if error != CGError::Success || count == 0 || count as usize > displays.len() {
            return Err(InjectionError::Native(
                "Active displays are unavailable".into(),
            ));
        }
        let point = CGEvent::location(CGEvent::new(None).as_deref());
        Self::new(Target::Desktop(displays[..count as usize].to_vec()), point)
    }
    /// Limit normalized pointer coordinates to the display the host selected.
    pub fn display(id: u32) -> Result<Self, InjectionError> {
        Self::display_in_session(id, false)
    }

    /// The login window may reject allocating a private per-user event source.
    /// Initialize an explicit source after the LoginWindow AppKit startup.
    pub fn login_display(id: u32) -> Result<Self, InjectionError> {
        Self::display_in_session(id, true)
    }

    fn display_in_session(id: u32, login_window: bool) -> Result<Self, InjectionError> {
        if !CGPreflightPostEventAccess() {
            return Err(InjectionError::Permission);
        }
        if !CGDisplayIsActive(id) {
            return Err(InjectionError::Unsupported);
        }
        let bounds = CGDisplayBounds(id);
        if !bounds.origin.x.is_finite()
            || !bounds.origin.y.is_finite()
            || !bounds.size.width.is_finite()
            || !bounds.size.height.is_finite()
            || bounds.size.width < 1.0
            || bounds.size.height < 1.0
        {
            return Err(InjectionError::Unsupported);
        }
        // A button/wheel can arrive before the first pointer move. Its initial
        // location must already belong to the host-selected display.
        let point = CGPoint::new(
            bounds.origin.x + bounds.size.width / 2.0,
            bounds.origin.y + bounds.size.height / 2.0,
        );
        let target = Target::Desktop(vec![id]);
        if login_window {
            let source = CGEventSource::new(CGEventSourceStateID::CombinedSessionState)
                .ok_or_else(|| {
                    InjectionError::Native("Cannot create login window input source".into())
                })?;
            let keyboard = crate::macos_login_keyboard::LoginKeyboard::open()?;
            return Ok(Self::with_source(target, point, source, Some(keyboard)));
        }
        Self::new(target, point)
    }
    /// Deliberately limited to this process: useful for the isolated QA fixture.
    /// Pointer/button injection is desktop-only; keyboard/text cannot escape PID.
    pub fn own_process() -> Result<Self, InjectionError> {
        Self::new(Target::OwnProcess, CGPoint::new(0.0, 0.0))
    }
    fn new(target: Target, point: CGPoint) -> Result<Self, InjectionError> {
        let source = CGEventSource::new(CGEventSourceStateID::Private)
            .ok_or_else(|| InjectionError::Native("Cannot create private input source".into()))?;
        Ok(Self::with_source(target, point, source, None))
    }
    fn with_source(
        target: Target,
        point: CGPoint,
        source: CFRetained<CGEventSource>,
        login_keyboard: Option<crate::macos_login_keyboard::LoginKeyboard>,
    ) -> Self {
        Self {
            source,
            target,
            login_keyboard,
            keys: BTreeSet::new(),
            buttons: BTreeSet::new(),
            clicks: BTreeMap::new(),
            caps: false,
            point,
            cancellation: Arc::new(AtomicBool::new(false)),
            authorization: None,
        }
    }
    pub fn with_cancellation(mut self, cancellation: Arc<AtomicBool>) -> Self {
        self.cancellation = cancellation;
        self
    }
    pub fn with_authorization(
        mut self,
        authorization: Arc<dyn Fn() -> bool + Send + Sync>,
    ) -> Self {
        self.authorization = Some(authorization);
        self
    }
    fn live(&self) -> bool {
        !self.cancellation.load(Ordering::Acquire)
            && self.authorization.as_ref().is_none_or(|check| check())
    }
    fn post(&self, event: &CGEvent, release: bool) -> Result<(), InjectionError> {
        if self.login_keyboard.is_some() && !crate::macos::is_login_window_console() {
            return Err(InjectionError::Permission);
        }
        if !release && !self.live() {
            return Err(InjectionError::Native("Input authorization expired".into()));
        }
        // CGEventPostToPid also requires event-post permission on macOS. It is
        // not an exemption for a private source or the application's own PID.
        if !Self::post_access() {
            return Err(InjectionError::Permission);
        }
        if !release && !self.live() {
            return Err(InjectionError::Native("Input authorization expired".into()));
        }
        match self.target {
            Target::Desktop(_) => {
                CGEvent::post(CGEventTapLocation::HIDEventTap, Some(event));
            }
            Target::OwnProcess => CGEvent::post_to_pid(std::process::id() as i32, Some(event)),
        }
        Ok(())
    }
    fn flags(&self) -> CGEventFlags {
        let mut flags = CGEventFlags::empty();
        for key in &self.keys {
            flags |= match key {
                0xe0 | 0xe4 => CGEventFlags::MaskControl,
                0xe1 | 0xe5 => CGEventFlags::MaskShift,
                0xe2 | 0xe6 => CGEventFlags::MaskAlternate,
                0xe3 | 0xe7 => CGEventFlags::MaskCommand,
                _ => CGEventFlags::empty(),
            };
        }
        if self.caps {
            flags |= CGEventFlags::MaskAlphaShift;
        }
        flags
    }
    fn key(&mut self, usage: u16, down: bool) -> Result<(), InjectionError> {
        if let Some(keyboard) = &self.login_keyboard {
            if !crate::macos::is_login_window_console()
                || !Self::post_access()
                || (down && !self.live())
            {
                return Err(InjectionError::Permission);
            }
            let mut next = self.keys.clone();
            if down {
                next.insert(usage);
            } else {
                next.remove(&usage);
            }
            keyboard.send(&next)?;
            self.keys = next;
            return Ok(());
        }

        let code = virtual_key(usage).ok_or(InjectionError::Unsupported)?;
        let event = CGEvent::new_keyboard_event(Some(&self.source), code, down)
            .ok_or_else(|| InjectionError::Native("Cannot create keyboard event".into()))?;
        let was_held = self.keys.contains(&usage);
        let was_caps = self.caps;
        if down {
            self.keys.insert(usage);
        } else {
            self.keys.remove(&usage);
        }
        if usage == 0x39 && down && !was_held {
            self.caps = !self.caps;
        }
        if (0xe0..=0xe7).contains(&usage) || usage == 0x39 {
            CGEvent::set_type(Some(&event), CGEventType::FlagsChanged);
        }
        let mut flags = self.flags();
        if (0x53..=0x63).contains(&usage) {
            flags |= CGEventFlags::MaskNumericPad;
        }
        CGEvent::set_flags(Some(&event), flags);
        CGEvent::set_integer_value_field(
            Some(&event),
            CGEventField::KeyboardEventAutorepeat,
            i64::from(down && was_held),
        );
        if let Err(error) = self.post(&event, !down) {
            if was_held {
                self.keys.insert(usage);
            } else {
                self.keys.remove(&usage);
            }
            self.caps = was_caps;
            return Err(error);
        }
        Ok(())
    }
    fn text(&self, text: &str) -> Result<(), InjectionError> {
        if self.login_keyboard.is_some() {
            return Err(InjectionError::Unsupported);
        }
        // One scalar per paired event keeps surrogate pairs intact, avoids any
        // undocumented maximum event-string length and never uses the clipboard.
        for character in text.chars() {
            if !self.live() {
                return Err(InjectionError::Native("Input session stopped".into()));
            }
            let mut storage = zeroize::Zeroizing::new([0u16; 2]);
            let utf16 = character.encode_utf16(&mut *storage);
            for down in [true, false] {
                if down && !self.live() {
                    return Err(InjectionError::Native("Input authorization expired".into()));
                }
                let event = CGEvent::new_keyboard_event(Some(&self.source), 0, down)
                    .ok_or_else(|| InjectionError::Native("Cannot create Unicode event".into()))?;
                // SAFETY: utf16 remains live, initialized and exactly length units.
                unsafe {
                    CGEvent::keyboard_set_unicode_string(
                        Some(&event),
                        utf16.len() as _,
                        utf16.as_ptr(),
                    );
                }
                CGEvent::set_flags(Some(&event), CGEventFlags::empty());
                self.post(&event, !down)?;
            }
        }
        Ok(())
    }
    fn mouse(
        &self,
        kind: CGEventType,
        button: u8,
        point: CGPoint,
        clicks: u8,
    ) -> Result<(), InjectionError> {
        if !matches!(self.target, Target::Desktop(_)) {
            return Err(InjectionError::Unsupported);
        }
        let event = CGEvent::new_mouse_event(
            Some(&self.source),
            kind,
            point,
            CGMouseButton(u32::from(button - 1)),
        )
        .ok_or_else(|| InjectionError::Native("Cannot create mouse event".into()))?;
        CGEvent::set_flags(Some(&event), self.flags());
        CGEvent::set_integer_value_field(
            Some(&event),
            CGEventField::MouseEventClickState,
            i64::from(clicks),
        );
        self.post(
            &event,
            matches!(
                kind,
                CGEventType::LeftMouseUp | CGEventType::RightMouseUp | CGEventType::OtherMouseUp
            ),
        )
    }
    fn button(&mut self, button: u8, down: bool, clicks: u8) -> Result<(), InjectionError> {
        let kind = match (button, down) {
            (1, true) => CGEventType::LeftMouseDown,
            (1, false) => CGEventType::LeftMouseUp,
            (2, true) => CGEventType::RightMouseDown,
            (2, false) => CGEventType::RightMouseUp,
            (_, true) => CGEventType::OtherMouseDown,
            (_, false) => CGEventType::OtherMouseUp,
        };
        let count = if down {
            clicks
        } else {
            self.clicks.get(&button).copied().unwrap_or(clicks)
        };
        self.mouse(kind, button, self.point, count)?;
        if down {
            self.buttons.insert(button);
            self.clicks.insert(button, clicks);
        } else {
            self.buttons.remove(&button);
            self.clicks.remove(&button);
        }
        Ok(())
    }
    fn release(&mut self) -> Result<(), InjectionError> {
        let keys: Vec<_> = self.keys.iter().copied().collect();
        let buttons: Vec<_> = self.buttons.iter().copied().collect();
        let mut first = None;
        for key in keys {
            if let Err(error) = self.key(key, false) {
                first.get_or_insert(error);
            }
        }
        for button in buttons {
            if let Err(error) = self.inject(&Input::Button {
                button,
                down: false,
            }) {
                first.get_or_insert(error);
            }
        }
        first.map_or(Ok(()), Err)
    }
}
impl Injector for MacInjector {
    fn inject(&mut self, event: &Input<'_>) -> Result<(), InjectionError> {
        input::validate(event).map_err(|_| InjectionError::Unsupported)?;
        if !self.live()
            && !matches!(
                event,
                Input::Key { down: false, .. }
                    | Input::Button { down: false, .. }
                    | Input::ButtonClick { down: false, .. }
                    | Input::ReleaseAll
            )
        {
            return Err(InjectionError::Native("Input session stopped".into()));
        }
        match *event {
            Input::Key { usage, down } => self.key(usage, down),
            Input::Text(text) => self.text(text),
            Input::ReleaseAll => self.release(),
            Input::Pointer { display, x, y } => {
                let Target::Desktop(displays) = &self.target else {
                    return Err(InjectionError::Unsupported);
                };
                let id = *displays
                    .get(usize::from(display))
                    .ok_or(InjectionError::Unsupported)?;
                if !CGDisplayIsActive(id) {
                    return Err(InjectionError::Native("Input display disconnected".into()));
                }
                let bounds = CGDisplayBounds(id);
                if !bounds.size.width.is_finite()
                    || !bounds.size.height.is_finite()
                    || bounds.size.width < 1.0
                    || bounds.size.height < 1.0
                {
                    return Err(InjectionError::Unsupported);
                }
                // The upper endpoint remains inside this display rather than
                // crossing onto its neighbour at normalized coordinate65535.
                let point = CGPoint::new(
                    bounds.origin.x + f64::from(x) / 65535.0 * (bounds.size.width - 1.0),
                    bounds.origin.y + f64::from(y) / 65535.0 * (bounds.size.height - 1.0),
                );
                let (kind, button) = if self.buttons.contains(&1) {
                    (CGEventType::LeftMouseDragged, 1)
                } else if self.buttons.contains(&2) {
                    (CGEventType::RightMouseDragged, 2)
                } else if let Some(button) = self.buttons.first() {
                    (CGEventType::OtherMouseDragged, *button)
                } else {
                    (CGEventType::MouseMoved, 1)
                };
                self.mouse(
                    kind,
                    button,
                    point,
                    self.clicks.get(&button).copied().unwrap_or(0),
                )?;
                self.point = point;
                Ok(())
            }
            Input::Button { button, down } => self.button(
                button,
                down,
                if down {
                    1
                } else {
                    self.clicks.get(&button).copied().unwrap_or(1)
                },
            ),
            Input::ButtonClick {
                button,
                down,
                clicks,
            } => self.button(button, down, clicks),
            Input::Wheel {
                horizontal,
                vertical,
            } => {
                if !matches!(self.target, Target::Desktop(_)) {
                    return Err(InjectionError::Unsupported);
                }
                let event = CGEvent::new_scroll_wheel_event2(
                    Some(&self.source),
                    CGScrollEventUnit::Pixel,
                    2,
                    i32::from(vertical),
                    i32::from(horizontal),
                    0,
                )
                .ok_or_else(|| InjectionError::Native("Cannot create wheel event".into()))?;
                CGEvent::set_flags(Some(&event), self.flags());
                self.post(&event, false)
            }
        }
    }
}
impl Drop for MacInjector {
    fn drop(&mut self) {
        let _ = self.release();
    }
}

/// USB keyboard-page usage -> Apple virtual key. Unassigned/consumer usages do
/// not silently become another key. Text follows the separate Unicode path.
fn virtual_key(usage: u16) -> Option<u16> {
    const LETTERS: [u16; 26] = [
        0, 11, 8, 2, 14, 3, 5, 4, 34, 38, 40, 37, 46, 45, 31, 35, 12, 15, 1, 17, 32, 9, 13, 7, 16,
        6,
    ];
    const DIGITS: [u16; 10] = [18, 19, 20, 21, 23, 22, 26, 28, 25, 29];
    if (4..=29).contains(&usage) {
        return Some(LETTERS[usize::from(usage - 4)]);
    }
    if (30..=39).contains(&usage) {
        return Some(DIGITS[usize::from(usage - 30)]);
    }
    Some(match usage {
        0x28 => 36,
        0x29 => 53,
        0x2a => 51,
        0x2b => 48,
        0x2c => 49,
        0x2d => 27,
        0x2e => 24,
        0x2f => 33,
        0x30 => 30,
        0x31 => 42,
        0x32 => 42,
        0x33 => 41,
        0x34 => 39,
        0x35 => 50,
        0x36 => 43,
        0x37 => 47,
        0x38 => 44,
        0x39 => 57,
        0x3a => 122,
        0x3b => 120,
        0x3c => 99,
        0x3d => 118,
        0x3e => 96,
        0x3f => 97,
        0x40 => 98,
        0x41 => 100,
        0x42 => 101,
        0x43 => 109,
        0x44 => 103,
        0x45 => 111,
        0x46 => 105,
        0x47 => 107,
        0x48 => 113,
        0x49 => 114,
        0x4a => 115,
        0x4b => 116,
        0x4c => 117,
        0x4d => 119,
        0x4e => 121,
        0x4f => 124,
        0x50 => 123,
        0x51 => 125,
        0x52 => 126,
        0x53 => 71,
        0x54 => 75,
        0x55 => 67,
        0x56 => 78,
        0x57 => 69,
        0x58 => 76,
        0x59 => 83,
        0x5a => 84,
        0x5b => 85,
        0x5c => 86,
        0x5d => 87,
        0x5e => 88,
        0x5f => 89,
        0x60 => 91,
        0x61 => 92,
        0x62 => 82,
        0x63 => 65,
        0x64 => 10,
        0x65 => 110,
        0x67 => 81,
        0x68 => 105,
        0x69 => 107,
        0x6a => 113,
        0x6b => 106,
        0x6c => 64,
        0x6d => 79,
        0x6e => 80,
        0x6f => 90,
        0x87 => 94,
        0x89 => 93,
        0x90 => 104,
        0x91 => 102,
        0xe0 => 59,
        0xe1 => 56,
        0xe2 => 58,
        0xe3 => 55,
        0xe4 => 62,
        0xe5 => 60,
        0xe6 => 61,
        0xe7 => 54,
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn physical_keys_and_both_sides_of_modifiers_map_without_layout_text_guessing() {
        assert_eq!(virtual_key(4), Some(0));
        assert_eq!(virtual_key(29), Some(6));
        assert_eq!(virtual_key(0x64), Some(10));
        assert_eq!(virtual_key(0x58), Some(76));
        assert_eq!(virtual_key(0xe1), Some(56));
        assert_eq!(virtual_key(0xe5), Some(60));
        assert_eq!(virtual_key(0), None);
        assert_eq!(virtual_key(0x86), None);
        assert_eq!(virtual_key(0x100), None);
    }
    #[test]
    fn own_process_target_cannot_inject_pointer_buttons_or_wheel() {
        let mut injector = MacInjector::own_process().unwrap();
        for event in [
            Input::Pointer {
                display: 0,
                x: 0,
                y: 0,
            },
            Input::Button {
                button: 1,
                down: true,
            },
            Input::Wheel {
                horizontal: 0,
                vertical: 1,
            },
        ] {
            assert_eq!(injector.inject(&event), Err(InjectionError::Unsupported));
        }
        assert!(injector.keys.is_empty());
        assert!(injector.buttons.is_empty());
    }
}
