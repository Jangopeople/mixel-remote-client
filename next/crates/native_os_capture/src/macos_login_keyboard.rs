use crate::input::InjectionError;
use std::{collections::BTreeSet, ffi::c_void, ptr::NonNull};

unsafe extern "C" {
    fn mixel_login_keyboard_entitled() -> bool;
    fn mixel_login_keyboard_open() -> *mut c_void;
    fn mixel_login_keyboard_report(device: *mut c_void, report: *const u8) -> i32;
    fn mixel_login_keyboard_close(device: *mut c_void);
}

pub fn entitled() -> bool {
    // SAFETY: the shim reads and releases the caller's code-signing information.
    unsafe { mixel_login_keyboard_entitled() }
}

pub struct LoginKeyboard(NonNull<c_void>);
impl LoginKeyboard {
    pub fn open() -> Result<Self, InjectionError> {
        if !entitled() {
            return Err(InjectionError::Native(
                "Apple Virtual HID Device provisioning is required for login-window keyboard input"
                    .into(),
            ));
        }
        // SAFETY: the shim returns either null or an owned, activated keyboard.
        NonNull::new(unsafe { mixel_login_keyboard_open() })
            .map(Self)
            .ok_or_else(|| {
                InjectionError::Native(
                    "macOS refused to create the login-window virtual keyboard".into(),
                )
            })
    }

    pub fn send(&self, held: &BTreeSet<u16>) -> Result<(), InjectionError> {
        let bytes = report(held)?;
        // SAFETY: the owned device is live and bytes contains the complete 29-byte report.
        let result = unsafe { mixel_login_keyboard_report(self.0.as_ptr(), bytes.as_ptr()) };
        if result == 0 {
            Ok(())
        } else {
            Err(InjectionError::Native(format!(
                "macOS rejected the virtual keyboard report (0x{result:08x})"
            )))
        }
    }
}

impl Drop for LoginKeyboard {
    fn drop(&mut self) {
        // SAFETY: this instance owns the device and releases it exactly once.
        unsafe { mixel_login_keyboard_close(self.0.as_ptr()) };
    }
}

fn report(held: &BTreeSet<u16>) -> Result<[u8; 29], InjectionError> {
    let mut bytes = [0; 29];
    for &usage in held {
        match usage {
            0xe0..=0xe7 => bytes[0] |= 1 << (usage - 0xe0),
            0x04..=0xdf => {
                let offset = usize::from(usage - 4);
                bytes[1 + offset / 8] |= 1 << (offset % 8);
            }
            _ => return Err(InjectionError::Unsupported),
        }
    }
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reports_keep_modifiers_separate_and_clear_released_keys() {
        let mut held = BTreeSet::from([4, 42, 0xdf, 0xe1, 0xe7]);
        let bytes = report(&held).unwrap();
        assert_eq!(bytes[0], 0b10000010);
        assert_eq!(bytes[1], 1);
        assert_eq!(bytes[5], 1 << 6);
        assert_eq!(bytes[28], 1 << 3);
        held.clear();
        assert_eq!(report(&held).unwrap(), [0; 29]);
    }
}
