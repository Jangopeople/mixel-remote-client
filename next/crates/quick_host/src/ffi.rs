//! C interface of the host core, used by the iPhone app (Swift). Texts are UTF-8 with a closing
//! zero; a text returned by this library must be given back to [`mqs_free`].
#![allow(unsafe_code)]
// These functions are called from C/Swift, which has no notion of `unsafe fn`; every pointer use is checked and commented.
#![allow(clippy::not_unsafe_ptr_arg_deref)]
use crate::{Config, Enrollment, Host};
use std::ffi::{CStr, CString, c_char, c_void};

/// Called for every event with its JSON text (valid only during the call) and the caller's own pointer.
pub type Listener = extern "C" fn(event: *const c_char, context: *mut c_void);

struct Context(*mut c_void);
// The app guarantees that its pointer may be used from the host core's threads.
unsafe impl Send for Context {}
unsafe impl Sync for Context {}

fn text(pointer: *const c_char) -> Option<String> {
    if pointer.is_null() {
        return None;
    }
    // Safety: the caller passes a valid zero-terminated text.
    unsafe { CStr::from_ptr(pointer) }.to_str().ok().map(str::to_owned)
}
fn give(text: &str) -> *mut c_char {
    CString::new(text.replace('\0', "")).map(CString::into_raw).unwrap_or(std::ptr::null_mut())
}

/// Creates the host core. `seed` is 32 stored bytes or null on first start. Null on failure.
#[unsafe(no_mangle)]
pub extern "C" fn mqs_create(seed: *const u8, listener: Listener, context: *mut c_void) -> *mut Host {
    // Safety: a non-null `seed` points to 32 readable bytes.
    let seed = (!seed.is_null()).then(|| unsafe { std::ptr::read(seed.cast::<[u8; 32]>()) });
    let context = Context(context);
    let host = Host::new(Config::production(), seed, move |event| {
        let context = &context;
        if let Ok(json) = CString::new(event.json()) {
            listener(json.as_ptr(), context.0);
        }
    });
    host.map(|host| Box::into_raw(Box::new(host))).unwrap_or(std::ptr::null_mut())
}
/// Copies this device's 32-byte key to `out` (store it for the next start).
#[unsafe(no_mangle)]
pub extern "C" fn mqs_seed(host: *const Host, out: *mut u8) {
    // Safety: `host` comes from `mqs_create` and `out` has room for 32 bytes.
    if let Some(host) = unsafe { host.as_ref() } {
        unsafe { std::ptr::copy_nonoverlapping(host.seed().as_ptr(), out, 32) };
    }
}
/// Registers with a support code. Returns JSON: the registration, or `{"error": "..."}`. Blocks.
#[unsafe(no_mangle)]
pub extern "C" fn mqs_enroll(host: *const Host, code: *const c_char, name: *const c_char) -> *mut c_char {
    // Safety: `host` comes from `mqs_create`.
    let Some(host) = (unsafe { host.as_ref() }) else { return std::ptr::null_mut() };
    let result = match (text(code), text(name)) {
        (Some(code), Some(name)) => host.enroll(&code, &name),
        _ => Err("Invalid text".into()),
    };
    give(&match result {
        Ok(enrollment) => serde_json::to_string(&enrollment).unwrap_or_default(),
        Err(error) => serde_json::json!({ "error": error }).to_string(),
    })
}
/// Goes online with a registration (JSON from `mqs_enroll`). Returns null, or an error text.
#[unsafe(no_mangle)]
pub extern "C" fn mqs_start(host: *const Host, enrollment: *const c_char, width: u32, height: u32, control: bool) -> *mut c_char {
    // Safety: `host` comes from `mqs_create`.
    let Some(host) = (unsafe { host.as_ref() }) else { return give("No host") };
    let result = text(enrollment)
        .and_then(|json| serde_json::from_str::<Enrollment>(&json).ok())
        .ok_or_else(|| "Invalid registration".to_owned())
        .and_then(|enrollment| host.start(enrollment, width, height, control));
    match result {
        Ok(()) => std::ptr::null_mut(),
        Err(error) => give(&error),
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn mqs_decide(host: *const Host, accept: bool) {
    // Safety: `host` comes from `mqs_create`.
    if let Some(host) = unsafe { host.as_ref() } {
        host.decide(accept);
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn mqs_end_session(host: *const Host) {
    // Safety: `host` comes from `mqs_create`.
    if let Some(host) = unsafe { host.as_ref() } {
        host.end_session();
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn mqs_stop(host: *const Host) {
    // Safety: `host` comes from `mqs_create`.
    if let Some(host) = unsafe { host.as_ref() } {
        host.stop();
    }
}
/// One encoded picture (H.264 access unit with start codes).
#[unsafe(no_mangle)]
pub extern "C" fn mqs_push_frame(host: *const Host, data: *const u8, length: usize) {
    // Safety: `host` comes from `mqs_create`; `data` points to `length` readable bytes.
    if let Some(host) = unsafe { host.as_ref() }
        && !data.is_null()
    {
        host.push_frame(unsafe { std::slice::from_raw_parts(data, length) });
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn mqs_destroy(host: *mut Host) {
    if !host.is_null() {
        // Safety: `host` comes from `mqs_create` and is not used afterwards.
        let host = unsafe { Box::from_raw(host) };
        host.stop();
        drop(host);
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn mqs_free(text: *mut c_char) {
    if !text.is_null() {
        // Safety: `text` was returned by this library.
        drop(unsafe { CString::from_raw(text) });
    }
}
