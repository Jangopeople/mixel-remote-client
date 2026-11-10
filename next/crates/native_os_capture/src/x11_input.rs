//! XTEST input on the same local X11 display selected for screen sharing.
//! Resolve USB positions against the running X server's XKB names.
use crate::{
    input::{InjectionError, Injector},
    x11_capture::{self, Display},
};
use core_protocol::input::{self, Input};
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};
use x11rb::{
    connection::Connection,
    protocol::{
        xkb,
        xproto::{self, ConnectionExt},
        xtest,
    },
    rust_connection::RustConnection,
};

fn native(error: impl std::fmt::Display) -> InjectionError {
    InjectionError::Native(error.to_string())
}

pub struct X11Injector {
    connection: RustConnection,
    display: Display,
    cancellation: Arc<AtomicBool>,
    keycodes: BTreeMap<[u8; 4], u8>,
    keys: BTreeSet<u8>,
    buttons: BTreeSet<u8>,
    scroll: [i32; 2],
}

impl X11Injector {
    pub fn display(
        display: Display,
        cancellation: Arc<AtomicBool>,
    ) -> Result<Self, InjectionError> {
        let connection = x11_capture::connect().map_err(native)?;
        let valid_root = connection
            .setup()
            .roots
            .get(display.screen)
            .is_some_and(|screen| screen.root == display.root);
        if !valid_root {
            return Err(InjectionError::Permission);
        }
        xtest::get_version(&connection, 2, 2)
            .map_err(native)?
            .reply()
            .map_err(native)?;
        if !xkb::use_extension(&connection, 1, 0)
            .map_err(native)?
            .reply()
            .map_err(native)?
            .supported
        {
            return Err(InjectionError::Unsupported);
        }
        let names = xkb::get_names(
            &connection,
            xkb::ID::USE_CORE_KBD.into(),
            xkb::NameDetail::KEY_NAMES,
        )
        .map_err(native)?
        .reply()
        .map_err(native)?;
        let keycodes = names
            .value_list
            .key_names
            .ok_or(InjectionError::Unsupported)?
            .into_iter()
            .zip(names.first_key..=names.max_key_code)
            .filter(|(key, _)| key.name != [0; 4])
            .map(|(key, code)| (key.name, code))
            .collect();
        Ok(Self {
            connection,
            display,
            cancellation,
            keycodes,
            keys: BTreeSet::new(),
            buttons: BTreeSet::new(),
            scroll: [0, 0],
        })
    }

    fn submit(&self, event: u8, detail: u8, x: i16, y: i16) -> Result<(), InjectionError> {
        // XTEST key and button events use the current pointer root. Only
        // motion supplies the selected root and absolute coordinates.
        let root = if event == xproto::MOTION_NOTIFY_EVENT {
            self.display.root
        } else {
            x11rb::NONE
        };
        xtest::fake_input(
            &self.connection,
            event,
            detail,
            x11rb::CURRENT_TIME,
            root,
            x,
            y,
            0,
        )
        .map_err(native)?
        .check()
        .map_err(native)
    }

    fn key(&mut self, key: u8, down: bool) -> Result<(), InjectionError> {
        self.submit(
            if down {
                xproto::KEY_PRESS_EVENT
            } else {
                xproto::KEY_RELEASE_EVENT
            },
            key,
            0,
            0,
        )?;
        if down {
            self.keys.insert(key);
        } else {
            self.keys.remove(&key);
        }
        Ok(())
    }

    fn button(&mut self, button: u8, down: bool) -> Result<(), InjectionError> {
        self.submit(
            if down {
                xproto::BUTTON_PRESS_EVENT
            } else {
                xproto::BUTTON_RELEASE_EVENT
            },
            button,
            0,
            0,
        )?;
        if down {
            self.buttons.insert(button);
        } else {
            self.buttons.remove(&button);
        }
        Ok(())
    }

    fn release(&mut self) -> Result<(), InjectionError> {
        let mut failure = None;
        for key in self.keys.clone() {
            if let Err(error) = self.key(key, false) {
                failure.get_or_insert(error);
            }
        }
        for button in self.buttons.clone() {
            if let Err(error) = self.button(button, false) {
                failure.get_or_insert(error);
            }
        }
        failure.map_or(Ok(()), Err)
    }

    fn wheel(
        &mut self,
        axis: usize,
        delta: i16,
        positive: u8,
        negative: u8,
    ) -> Result<(), InjectionError> {
        self.scroll[axis] = (self.scroll[axis] + i32::from(delta)).clamp(-3_840, 3_840);
        let button = if self.scroll[axis] >= 0 {
            positive
        } else {
            negative
        };
        while self.scroll[axis].abs() >= 120 {
            if self.cancellation.load(Ordering::Acquire) {
                return Err(InjectionError::Permission);
            }
            self.button(button, true)?;
            self.button(button, false)?;
            self.scroll[axis] -= self.scroll[axis].signum() * 120;
        }
        Ok(())
    }
}

impl Injector for X11Injector {
    fn inject(&mut self, event: &Input<'_>) -> Result<(), InjectionError> {
        input::validate(event).map_err(|_| InjectionError::Unsupported)?;
        if self.cancellation.load(Ordering::Acquire)
            && !matches!(
                event,
                Input::ReleaseAll
                    | Input::Key { down: false, .. }
                    | Input::Button { down: false, .. }
                    | Input::ButtonClick { down: false, .. }
            )
        {
            return Err(InjectionError::Permission);
        }
        match *event {
            Input::Key { usage, down } => {
                let code = physical_name(usage)
                    .and_then(|name| self.keycodes.get(name))
                    .copied()
                    .ok_or(InjectionError::Unsupported)?;
                self.key(code, down)
            }
            Input::Pointer { display: 0, x, y } => {
                let geometry = self
                    .connection
                    .get_geometry(self.display.root)
                    .map_err(native)?
                    .reply()
                    .map_err(native)?;
                let scaled = |value, size: u16| {
                    i16::try_from(u32::from(value) * u32::from(size.saturating_sub(1)) / 65535)
                        .map_err(|_| InjectionError::Unsupported)
                };
                self.submit(
                    xproto::MOTION_NOTIFY_EVENT,
                    0,
                    scaled(x, geometry.width)?,
                    scaled(y, geometry.height)?,
                )
            }
            Input::Button { button, down } | Input::ButtonClick { button, down, .. } => {
                let mapped = [1, 3, 2, 8, 9][usize::from(button - 1)];
                self.button(mapped, down)
            }
            Input::Wheel {
                horizontal,
                vertical,
            } => {
                self.wheel(0, horizontal, 7, 6)?;
                self.wheel(1, vertical, 4, 5)
            }
            Input::ReleaseAll => self.release(),
            Input::Pointer { .. } | Input::Text(_) => Err(InjectionError::Unsupported),
        }
    }
}

impl Drop for X11Injector {
    fn drop(&mut self) {
        let _ = self.release();
    }
}

fn physical_name(usage: u16) -> Option<&'static [u8; 4]> {
    let name = match usage {
        0x04..=0x1d => [
            b"AC01", b"AB05", b"AB03", b"AC03", b"AD03", b"AC04", b"AC05", b"AC06", b"AD08",
            b"AC07", b"AC08", b"AC09", b"AB07", b"AB06", b"AD09", b"AD10", b"AD01", b"AD04",
            b"AC02", b"AD05", b"AD07", b"AB04", b"AD02", b"AB02", b"AD06", b"AB01",
        ][usize::from(usage - 0x04)],
        0x1e..=0x27 => [
            b"AE01", b"AE02", b"AE03", b"AE04", b"AE05", b"AE06", b"AE07", b"AE08", b"AE09",
            b"AE10",
        ][usize::from(usage - 0x1e)],
        0x28 => b"RTRN",
        0x29 => b"ESC\0",
        0x2a => b"BKSP",
        0x2b => b"TAB\0",
        0x2c => b"SPCE",
        0x2d => b"AE11",
        0x2e => b"AE12",
        0x2f => b"AD11",
        0x30 => b"AD12",
        0x31 => b"BKSL",
        0x32 => b"BKSL",
        0x33 => b"AC10",
        0x34 => b"AC11",
        0x35 => b"TLDE",
        0x36 => b"AB08",
        0x37 => b"AB09",
        0x38 => b"AB10",
        0x39 => b"CAPS",
        0x3a..=0x45 => [
            b"FK01", b"FK02", b"FK03", b"FK04", b"FK05", b"FK06", b"FK07", b"FK08", b"FK09",
            b"FK10", b"FK11", b"FK12",
        ][usize::from(usage - 0x3a)],
        0x46 => b"PRSC",
        0x47 => b"SCLK",
        0x48 => b"PAUS",
        0x49 => b"INS\0",
        0x4a => b"HOME",
        0x4b => b"PGUP",
        0x4c => b"DELE",
        0x4d => b"END\0",
        0x4e => b"PGDN",
        0x4f => b"RGHT",
        0x50 => b"LEFT",
        0x51 => b"DOWN",
        0x52 => b"UP\0\0",
        0x53 => b"NMLK",
        0x54 => b"KPDV",
        0x55 => b"KPMU",
        0x56 => b"KPSU",
        0x57 => b"KPAD",
        0x58 => b"KPEN",
        0x59..=0x62 => [
            b"KP1\0", b"KP2\0", b"KP3\0", b"KP4\0", b"KP5\0", b"KP6\0", b"KP7\0", b"KP8\0",
            b"KP9\0", b"KP0\0",
        ][usize::from(usage - 0x59)],
        0x63 => b"KPDL",
        0x64 => b"LSGT",
        0x65 => b"MENU",
        0xe0..=0xe7 => [
            b"LCTL", b"LFSH", b"LALT", b"LWIN", b"RCTL", b"RTSH", b"RALT", b"RWIN",
        ][usize::from(usage - 0xe0)],
        _ => return None,
    };
    Some(name)
}
