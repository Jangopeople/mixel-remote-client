//! Add-only data protection Keychain access for the sandboxed controller.
use std::ffi::{CStr, c_char};

const RECORD_SIZE: usize = 47;

unsafe extern "C" {
    fn mixel_controller_keychain_read(account: *const c_char, output: *mut u8, size: isize) -> i32;
    fn mixel_controller_keychain_create(
        account: *const c_char,
        record: *const u8,
        size: isize,
    ) -> i32;
}

pub fn read(account: &CStr, output: &mut [u8; RECORD_SIZE]) -> Result<(), i32> {
    // The C bridge only writes a matching record into this live fixed-size buffer.
    let status = unsafe {
        mixel_controller_keychain_read(account.as_ptr(), output.as_mut_ptr(), RECORD_SIZE as isize)
    };
    if status == 0 { Ok(()) } else { Err(status) }
}

pub fn create(account: &CStr, record: &[u8; RECORD_SIZE]) -> Result<(), i32> {
    // The C bridge uses the buffer synchronously during SecItemAdd and never retains it.
    let status = unsafe {
        mixel_controller_keychain_create(account.as_ptr(), record.as_ptr(), RECORD_SIZE as isize)
    };
    if status == 0 { Ok(()) } else { Err(status) }
}
