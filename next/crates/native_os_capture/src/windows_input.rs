//! Win32 SendInput with no elevation fallback. Use behind an active host input
//! grant; UIPI/secure-desktop failures remain errors rather than bypasses.
use crate::{
    input::{InjectionError, Injector},
    windows_capture::{self, Display},
};
use core_protocol::input::{self, Input};
use std::{
    collections::BTreeSet,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};
use windows::Win32::UI::{
    Input::KeyboardAndMouse::*,
    WindowsAndMessaging::{
        GetSystemMetrics, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN,
        SM_YVIRTUALSCREEN,
    },
};
use zeroize::Zeroizing;
fn submit(events: &[INPUT]) -> Result<(), InjectionError> {
    // SAFETY: the initialized slice remains live for the call and its element
    // size is the exact SDK INPUT layout. Windows copies the events synchronously.
    let accepted = unsafe { SendInput(events, std::mem::size_of::<INPUT>() as i32) };
    if accepted as usize != events.len() {
        return Err(InjectionError::Native(format!(
            "Windows submitted {accepted}/{} events; host security may require local approval",
            events.len()
        )));
    }
    Ok(())
}
fn keyboard(scan: u16, vk: VIRTUAL_KEY, flags: KEYBD_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: vk,
                wScan: scan,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}
fn mouse(dx: i32, dy: i32, data: u32, flags: MOUSE_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx,
                dy,
                mouseData: data,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}
pub struct WindowsInjector {
    displays: Vec<Display>,
    cancellation: Arc<AtomicBool>,
    keys: BTreeSet<u16>,
    buttons: BTreeSet<u8>,
}
impl WindowsInjector {
    pub fn desktop(cancellation: Arc<AtomicBool>) -> Result<Self, InjectionError> {
        let displays = windows_capture::displays()
            .map_err(|error| InjectionError::Native(error.to_string()))?;
        if displays.is_empty() {
            return Err(InjectionError::Native(
                "No active display is available for input".into(),
            ));
        }
        Ok(Self {
            displays,
            cancellation,
            keys: BTreeSet::new(),
            buttons: BTreeSet::new(),
        })
    }
    /// Pointer targets in the order the controller numbers them; an entry made by
    /// `windows_capture::all_of` stands for the whole desktop.
    pub fn with_displays(displays: Vec<Display>, cancellation: Arc<AtomicBool>) -> Result<Self, InjectionError> {
        if displays.is_empty() {
            return Err(InjectionError::Native(
                "No active display is available for input".into(),
            ));
        }
        Ok(Self {
            displays,
            cancellation,
            keys: BTreeSet::new(),
            buttons: BTreeSet::new(),
        })
    }
    pub fn display(display: Display, cancellation: Arc<AtomicBool>) -> Result<Self, InjectionError> {
        if display.width < 2 || display.height < 2 {
            return Err(InjectionError::Unsupported);
        }
        Ok(Self {
            displays: vec![display],
            cancellation,
            keys: BTreeSet::new(),
            buttons: BTreeSet::new(),
        })
    }
    fn key(&mut self, usage: u16, down: bool) -> Result<(), InjectionError> {
        let (scan, extended, vk) = scan_code(usage).ok_or(InjectionError::Unsupported)?;
        let mut flags = if vk == VIRTUAL_KEY(0) {
            KEYEVENTF_SCANCODE
        } else {
            KEYBD_EVENT_FLAGS(0)
        };
        if extended {
            flags |= KEYEVENTF_EXTENDEDKEY;
        }
        if !down {
            flags |= KEYEVENTF_KEYUP;
        }
        submit(&[keyboard(scan, vk, flags)])?;
        if down {
            self.keys.insert(usage);
        } else {
            self.keys.remove(&usage);
        }
        Ok(())
    }
    fn text(&self, text: &str) -> Result<(), InjectionError> {
        for character in text.chars() {
            if self.cancellation.load(Ordering::Acquire) {
                return Err(InjectionError::Native("Input session stopped".into()));
            }
            let mut storage = Zeroizing::new([0u16; 2]);
            for unit in character.encode_utf16(&mut *storage) {
                let down = keyboard(*unit, VIRTUAL_KEY(0), KEYEVENTF_UNICODE);
                let up = keyboard(*unit, VIRTUAL_KEY(0), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP);
                if let Err(error) = submit(&[down, up]) {
                    let _ = submit(&[up]);
                    return Err(error);
                }
            }
        }
        Ok(())
    }
    fn button(&mut self, button: u8, down: bool) -> Result<(), InjectionError> {
        let (flags, data) = match (button, down) {
            (1, true) => (MOUSEEVENTF_LEFTDOWN, 0),
            (1, false) => (MOUSEEVENTF_LEFTUP, 0),
            (2, true) => (MOUSEEVENTF_RIGHTDOWN, 0),
            (2, false) => (MOUSEEVENTF_RIGHTUP, 0),
            (3, true) => (MOUSEEVENTF_MIDDLEDOWN, 0),
            (3, false) => (MOUSEEVENTF_MIDDLEUP, 0),
            (4 | 5, true) => (MOUSEEVENTF_XDOWN, u32::from(button - 3)),
            (4 | 5, false) => (MOUSEEVENTF_XUP, u32::from(button - 3)),
            _ => return Err(InjectionError::Unsupported),
        };
        submit(&[mouse(0, 0, data, flags)])?;
        if down {
            self.buttons.insert(button);
        } else {
            self.buttons.remove(&button);
        }
        Ok(())
    }
    fn release(&mut self) -> Result<(), InjectionError> {
        let mut first = None;
        for key in self.keys.clone() {
            if let Err(error) = self.key(key, false) {
                first.get_or_insert(error);
            }
        }
        for button in self.buttons.clone() {
            if let Err(error) = self.button(button, false) {
                first.get_or_insert(error);
            }
        }
        first.map_or(Ok(()), Err)
    }
}
impl Injector for WindowsInjector {
    fn inject(&mut self, event: &Input<'_>) -> Result<(), InjectionError> {
        input::validate(event).map_err(|_| InjectionError::Unsupported)?;
        if self.cancellation.load(Ordering::Acquire)
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
            Input::Text(text) => self.text(text),
            Input::Key { usage, down } => self.key(usage, down),
            Input::Button { button, down } | Input::ButtonClick { button, down, .. } => {
                self.button(button, down)
            }
            Input::ReleaseAll => self.release(),
            Input::Wheel {
                horizontal,
                vertical,
            } => {
                if vertical != 0 {
                    submit(&[mouse(0, 0, i32::from(vertical) as u32, MOUSEEVENTF_WHEEL)])?;
                }
                if horizontal != 0 {
                    submit(&[mouse(
                        0,
                        0,
                        i32::from(horizontal) as u32,
                        MOUSEEVENTF_HWHEEL,
                    )])?;
                }
                Ok(())
            }
            Input::Pointer { display, x, y } => {
                let selected = self
                    .displays
                    .get(usize::from(display))
                    .ok_or(InjectionError::Unsupported)?;
                let current = windows_capture::displays()
                    .map_err(|error| InjectionError::Native(error.to_string()))?;
                let whole = if selected.adapter == windows_capture::ALL_DISPLAYS {
                    windows_capture::all_of(&current)
                } else {
                    None
                };
                let active = whole
                    .as_ref()
                    .or_else(|| {
                        current.iter().find(|screen| {
                            screen.name == selected.name
                                && screen.adapter == selected.adapter
                                && screen.output == selected.output
                        })
                    })
                    .ok_or_else(|| {
                        InjectionError::Native(
                            "Input display changed; reconnect to select displays again".into(),
                        )
                    })?;
                let left = unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) };
                let top = unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) };
                let width = unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) };
                let height = unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) };
                if width < 2 || height < 2 {
                    return Err(InjectionError::Native(
                        "Virtual desktop dimensions are unavailable".into(),
                    ));
                }
                let px = i64::from(active.left)
                    + i64::from(x) * i64::from(active.width.saturating_sub(1)) / 65535;
                let py = i64::from(active.top)
                    + i64::from(y) * i64::from(active.height.saturating_sub(1)) / 65535;
                let dx = (px - i64::from(left)) * 65535 / i64::from(width - 1);
                let dy = (py - i64::from(top)) * 65535 / i64::from(height - 1);
                if !(0..=65535).contains(&dx) || !(0..=65535).contains(&dy) {
                    return Err(InjectionError::Native(
                        "Selected display is outside the current virtual desktop".into(),
                    ));
                }
                submit(&[mouse(
                    dx as i32,
                    dy as i32,
                    0,
                    MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                )])
            }
        }
    }
}
impl Drop for WindowsInjector {
    fn drop(&mut self) {
        let _ = self.release();
    }
}
fn scan_code(usage: u16) -> Option<(u16, bool, VIRTUAL_KEY)> {
    const LETTERS: [u16; 26] = [
        0x1e, 0x30, 0x2e, 0x20, 0x12, 0x21, 0x22, 0x23, 0x17, 0x24, 0x25, 0x26, 0x32, 0x31, 0x18,
        0x19, 0x10, 0x13, 0x1f, 0x14, 0x16, 0x2f, 0x11, 0x2d, 0x15, 0x2c,
    ];
    let (scan, extended) = match usage {
        4..=29 => (LETTERS[usize::from(usage - 4)], false),
        30..=38 => (usage - 28, false),
        39 => (0x0b, false),
        0x28 => (0x1c, false),
        0x29 => (0x01, false),
        0x2a => (0x0e, false),
        0x2b => (0x0f, false),
        0x2c => (0x39, false),
        0x2d => (0x0c, false),
        0x2e => (0x0d, false),
        0x2f => (0x1a, false),
        0x30 => (0x1b, false),
        0x31 | 0x32 => (0x2b, false),
        0x33 => (0x27, false),
        0x34 => (0x28, false),
        0x35 => (0x29, false),
        0x36 => (0x33, false),
        0x37 => (0x34, false),
        0x38 => (0x35, false),
        0x39 => (0x3a, false),
        0x3a..=0x43 => (usage + 1, false),
        0x44 => (0x57, false),
        0x45 => (0x58, false),
        0x46 => (0x37, true),
        0x47 => (0x46, false),
        0x48 => return Some((0, false, VK_PAUSE)),
        0x49 => (0x52, true),
        0x4a => (0x47, true),
        0x4b => (0x49, true),
        0x4c => (0x53, true),
        0x4d => (0x4f, true),
        0x4e => (0x51, true),
        0x4f => (0x4d, true),
        0x50 => (0x4b, true),
        0x51 => (0x50, true),
        0x52 => (0x48, true),
        0x53 => (0x45, true),
        0x54 => (0x35, true),
        0x55 => (0x37, false),
        0x56 => (0x4a, false),
        0x57 => (0x4e, false),
        0x58 => (0x1c, true),
        0x59 => (0x4f, false),
        0x5a => (0x50, false),
        0x5b => (0x51, false),
        0x5c => (0x4b, false),
        0x5d => (0x4c, false),
        0x5e => (0x4d, false),
        0x5f => (0x47, false),
        0x60 => (0x48, false),
        0x61 => (0x49, false),
        0x62 => (0x52, false),
        0x63 => (0x53, false),
        0x64 => (0x56, false),
        0x65 => (0x5d, true),
        0x68..=0x73 | 0x7f..=0x81 => {
            let vk = if usage <= 0x73 {
                u32::from(VK_F13.0) + u32::from(usage - 0x68)
            } else {
                match usage {
                    0x7f => u32::from(VK_VOLUME_MUTE.0),
                    0x80 => u32::from(VK_VOLUME_UP.0),
                    _ => u32::from(VK_VOLUME_DOWN.0),
                }
            };
            let mapped = unsafe { MapVirtualKeyW(vk, MAPVK_VK_TO_VSC_EX) };
            if mapped == 0 {
                return None;
            }
            (mapped as u16 & 0xff, mapped & 0xff00 == 0xe000)
        }
        0xe0 => (0x1d, false),
        0xe1 => (0x2a, false),
        0xe2 => (0x38, false),
        0xe3 => (0x5b, true),
        0xe4 => (0x1d, true),
        0xe5 => (0x36, false),
        0xe6 => (0x38, true),
        0xe7 => (0x5c, true),
        _ => return None,
    };
    Some((scan, extended, VIRTUAL_KEY(0)))
}
