//! The Mixel Remote engine for the Windows app (WinUI 3, 2.0 and later): a plain C interface over
//! [`mixel_core_ffi::MixelEngine`], built as `mixel_core.dll` and called from C# with P/Invoke.
//!
//! Why a C interface and not generated C# bindings: the app needs five calls (create, command,
//! picture, input, shutdown) whose arguments and results are already JSON or bytes. A fixed C
//! signature has no generator version to keep in step with UniFFI and is checked by the tests here
//! and by the app's own tests. The Swift apps keep using UniFFI over the same engine object.
//!
//! Rules of the interface:
//! - Every function returns `MIXEL_OK` (0) or `MIXEL_FAILED` (1); on failure `*error`/`*result`
//!   holds an English message for the person (the app translates known texts).
//! - Strings are UTF-8 and NUL-terminated. Strings and byte buffers the engine returns are owned by
//!   the caller and released with [`mixel_string_free`] / [`mixel_bytes_free`].
//! - Calls block the calling thread until the engine answers; the app calls them from worker
//!   threads. Any number of threads may call at once. A panic never crosses the boundary.
//! - Tokens, keys, SDP and ICE never cross into the app, exactly as with the Swift interface.
//!
//! # Safety (all `unsafe` functions)
//! Pointer arguments are either null or valid for the access the function documents: input strings
//! NUL-terminated and alive for the call, output slots writable, handles from [`mixel_engine_new`] and not
//! yet freed, buffers and strings returned by this library released once with the matching free function.
#![allow(unsafe_code)] // A C interface is unsafe by nature; every pointer is checked before use.

use mixel_core_ffi::MixelEngine;
use std::{
    ffi::{CStr, CString, c_char},
    future::Future,
    panic::{AssertUnwindSafe, catch_unwind},
    pin::pin,
    sync::Arc,
    task::{Context, Poll, Wake, Waker},
};

pub const MIXEL_OK: i32 = 0;
pub const MIXEL_FAILED: i32 = 1;
/// Raised whenever a function's signature or meaning changes; the app refuses another value.
pub const ABI_VERSION: u32 = 1;

/// The engine handle the app holds (opaque to C#).
pub struct Engine(Arc<MixelEngine>);

struct ThreadWaker(std::thread::Thread);
impl Wake for ThreadWaker {
    fn wake(self: Arc<Self>) {
        self.0.unpark();
    }
}

/// Runs an engine future on this thread. The engine's own futures only wait for work on the
/// engine's runtime, so a parking executor is all that is needed here.
fn block_on<F: Future>(future: F) -> F::Output {
    let mut future = pin!(future);
    let waker = Waker::from(Arc::new(ThreadWaker(std::thread::current())));
    let mut context = Context::from_waker(&waker);
    loop {
        match future.as_mut().poll(&mut context) {
            Poll::Ready(value) => return value,
            Poll::Pending => std::thread::park(),
        }
    }
}

fn text(pointer: *const c_char) -> Result<String, String> {
    if pointer.is_null() {
        return Err("Missing text argument".into());
    }
    // SAFETY: the caller passes a NUL-terminated string that lives for the whole call.
    unsafe { CStr::from_ptr(pointer) }
        .to_str()
        .map(str::to_owned)
        .map_err(|_| "Text arguments must be UTF-8".into())
}

fn c_string(value: String) -> *mut c_char {
    let clean = if value.contains('\0') { value.replace('\0', " ") } else { value };
    CString::new(clean).map_or(std::ptr::null_mut(), CString::into_raw)
}

/// Writes `value` to `out` when `out` is given.
fn put(out: *mut *mut c_char, value: String) {
    if !out.is_null() {
        // SAFETY: `out` is a valid pointer to a pointer slot supplied by the caller.
        unsafe { *out = c_string(value) };
    }
}

fn engine<'a>(handle: *const Engine) -> Result<&'a MixelEngine, String> {
    if handle.is_null() {
        return Err("The engine is not running".into());
    }
    // SAFETY: a non-null handle comes from `mixel_engine_new` and is not freed while in use.
    Ok(unsafe { &(*handle).0 })
}

/// Runs `work`, turns a panic into a failure and stores the message in `out`.
fn guarded(out: *mut *mut c_char, work: impl FnOnce() -> Result<Option<String>, String>) -> i32 {
    match catch_unwind(AssertUnwindSafe(work)) {
        Ok(Ok(value)) => {
            if let Some(value) = value {
                put(out, value);
            }
            MIXEL_OK
        }
        Ok(Err(message)) => {
            put(out, message);
            MIXEL_FAILED
        }
        Err(_) => {
            put(out, "The engine stopped unexpectedly".into());
            MIXEL_FAILED
        }
    }
}

/// The interface version (see [`ABI_VERSION`]).
#[unsafe(no_mangle)]
pub extern "C" fn mixel_core_abi_version() -> u32 {
    ABI_VERSION
}

/// Starts the engine for the Windows app. `data_dir`: the app's private folder (absolute).
/// On success `*out` holds the handle; on failure `*error` the message.
///
/// # Safety
/// Pointers as described in the crate documentation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn mixel_engine_new(data_dir: *const c_char, out: *mut *mut Engine, error: *mut *mut c_char) -> i32 {
    if out.is_null() {
        return MIXEL_FAILED;
    }
    guarded(error, || {
        // Administrator steps and the always-on service run the Rust helper next to the .NET app.
        mixel_engine::app_scope::set_helper_program();
        let engine = MixelEngine::new(text(data_dir)?).map_err(|failure| failure.to_string())?;
        // SAFETY: `out` was checked above.
        unsafe { *out = Box::into_raw(Box::new(Engine(engine))) };
        Ok(None)
    })
}

/// Facts about this build as JSON: `{"version","platform","controller_only","abi"}`.
///
/// # Safety
/// Pointers as described in the crate documentation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn mixel_engine_info(handle: *const Engine, result: *mut *mut c_char) -> i32 {
    guarded(result, || {
        let info = engine(handle)?.info();
        Ok(Some(
            serde_json::json!({
                "version": info.version,
                "platform": info.platform,
                "controller_only": info.controller_only,
                "abi": ABI_VERSION,
            })
            .to_string(),
        ))
    })
}

/// Runs one command (the desktop app's command names); `args` is a JSON object. `*result` holds the
/// JSON result, or the message on failure.
///
/// # Safety
/// Pointers as described in the crate documentation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn mixel_engine_call(
    handle: *const Engine,
    command: *const c_char,
    args: *const c_char,
    result: *mut *mut c_char,
) -> i32 {
    guarded(result, || {
        let engine = engine(handle)?;
        let command = text(command)?;
        let args = if args.is_null() { "{}".to_owned() } else { text(args)? };
        block_on(engine.call(command, args)).map(Some).map_err(|failure| failure.to_string())
    })
}

/// The next picture of a session ("MRF2" RGBA rows, or "MRH1" H.264 when `compressed`); `*len` 0
/// when none is waiting yet. Release `*data` with [`mixel_bytes_free`].
///
/// # Safety
/// Pointers as described in the crate documentation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn mixel_engine_next_video(
    handle: *const Engine,
    session: *const c_char,
    compressed: u8,
    data: *mut *mut u8,
    len: *mut usize,
    error: *mut *mut c_char,
) -> i32 {
    if data.is_null() || len.is_null() {
        return MIXEL_FAILED;
    }
    // SAFETY: both output pointers were checked above.
    unsafe {
        *data = std::ptr::null_mut();
        *len = 0;
    }
    guarded(error, || {
        let engine = engine(handle)?;
        let bytes = block_on(engine.next_video(text(session)?, compressed != 0)).map_err(|failure| failure.to_string())?;
        if !bytes.is_empty() {
            let boxed = bytes.into_boxed_slice();
            let length = boxed.len();
            // SAFETY: both output pointers were checked above; ownership passes to the caller.
            unsafe {
                *len = length;
                *data = Box::into_raw(boxed).cast::<u8>();
            }
        }
        Ok(None)
    })
}

/// One input event for a session (JSON as the desktop app sends it). `*posted` is 1 when the remote
/// computer applied it.
///
/// # Safety
/// Pointers as described in the crate documentation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn mixel_engine_input(
    handle: *const Engine,
    session: *const c_char,
    event: *const c_char,
    posted: *mut u8,
    error: *mut *mut c_char,
) -> i32 {
    guarded(error, || {
        let engine = engine(handle)?;
        let applied = block_on(engine.input(text(session)?, text(event)?)).map_err(|failure| failure.to_string())?;
        if !posted.is_null() {
            // SAFETY: `posted` is a valid byte slot supplied by the caller.
            unsafe { *posted = u8::from(applied) };
        }
        Ok(None)
    })
}

/// Before the app quits: ends sessions and goes offline. The handle stays valid until freed.
///
/// # Safety
/// Pointers as described in the crate documentation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn mixel_engine_shutdown(handle: *const Engine, error: *mut *mut c_char) -> i32 {
    guarded(error, || {
        block_on(engine(handle)?.shutdown()).map_err(|failure| failure.to_string())?;
        Ok(None)
    })
}

/// Releases the engine. No call may use the handle afterwards.
///
/// # Safety
/// Pointers as described in the crate documentation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn mixel_engine_free(handle: *mut Engine) {
    if !handle.is_null() {
        // SAFETY: the handle came from `mixel_engine_new` and is released exactly once.
        let _ = catch_unwind(AssertUnwindSafe(|| drop(unsafe { Box::from_raw(handle) })));
    }
}

/// Releases a string returned by the engine.
///
/// # Safety
/// Pointers as described in the crate documentation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn mixel_string_free(value: *mut c_char) {
    if !value.is_null() {
        // SAFETY: the string came from `CString::into_raw` in this library.
        drop(unsafe { CString::from_raw(value) });
    }
}

/// Releases a picture returned by [`mixel_engine_next_video`].
///
/// # Safety
/// Pointers as described in the crate documentation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn mixel_bytes_free(data: *mut u8, len: usize) {
    if !data.is_null() {
        // SAFETY: the buffer came from `Box<[u8]>::into_raw` with exactly this length.
        drop(unsafe { Box::from_raw(std::ptr::slice_from_raw_parts_mut(data, len)) });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // The tests call the C interface the way the app does; each wrapper only adds the `unsafe` the
    // signature asks for (the pointers below are valid as documented).
    fn mixel_engine_new_safe(dir: *const c_char, out: *mut *mut Engine, error: *mut *mut c_char) -> i32 {
        unsafe { mixel_engine_new(dir, out, error) }
    }
    fn mixel_engine_info_safe(handle: *const Engine, result: *mut *mut c_char) -> i32 {
        unsafe { mixel_engine_info(handle, result) }
    }
    fn mixel_engine_call_safe(handle: *const Engine, command: *const c_char, args: *const c_char, result: *mut *mut c_char) -> i32 {
        unsafe { mixel_engine_call(handle, command, args, result) }
    }
    fn mixel_engine_next_video_safe(handle: *const Engine, session: *const c_char, compressed: u8, data: *mut *mut u8, len: *mut usize, error: *mut *mut c_char) -> i32 {
        unsafe { mixel_engine_next_video(handle, session, compressed, data, len, error) }
    }
    fn mixel_engine_input_safe(handle: *const Engine, session: *const c_char, event: *const c_char, posted: *mut u8, error: *mut *mut c_char) -> i32 {
        unsafe { mixel_engine_input(handle, session, event, posted, error) }
    }
    fn mixel_engine_shutdown_safe(handle: *const Engine, error: *mut *mut c_char) -> i32 {
        unsafe { mixel_engine_shutdown(handle, error) }
    }
    fn mixel_engine_free_safe(handle: *mut Engine) {
        unsafe { mixel_engine_free(handle) }
    }
    fn mixel_string_free_safe(value: *mut c_char) {
        unsafe { mixel_string_free(value) }
    }
    fn mixel_bytes_free_safe(data: *mut u8, len: usize) {
        unsafe { mixel_bytes_free(data, len) }
    }

    fn take(pointer: *mut c_char) -> String {
        assert!(!pointer.is_null());
        // SAFETY: the pointer came from this library.
        let value = unsafe { CStr::from_ptr(pointer) }.to_str().unwrap().to_owned();
        mixel_string_free_safe(pointer);
        value
    }

    fn start() -> *mut Engine {
        let folder = std::env::temp_dir().join(format!("mixel-capi-test-{}", std::process::id()));
        let folder = CString::new(folder.to_str().unwrap()).unwrap();
        let mut handle = std::ptr::null_mut();
        let mut error = std::ptr::null_mut();
        assert_eq!(mixel_engine_new_safe(folder.as_ptr(), &mut handle, &mut error), MIXEL_OK);
        assert!(error.is_null() && !handle.is_null());
        handle
    }

    fn call(handle: *mut Engine, command: &str, args: &str) -> (i32, String) {
        let command = CString::new(command).unwrap();
        let args = CString::new(args).unwrap();
        let mut result = std::ptr::null_mut();
        let code = mixel_engine_call_safe(handle, command.as_ptr(), args.as_ptr(), &mut result);
        (code, take(result))
    }

    #[test]
    fn engine_starts_in_helper_mode_and_answers_commands() {
        let handle = start();
        assert!(mixel_engine::app_scope::helper_program());
        assert_eq!(mixel_core_abi_version(), ABI_VERSION);
        let mut info = std::ptr::null_mut();
        assert_eq!(mixel_engine_info_safe(handle, &mut info), MIXEL_OK);
        let info: serde_json::Value = serde_json::from_str(&take(info)).unwrap();
        assert_eq!(info["abi"], ABI_VERSION);
        let (code, status) = call(handle, "remote_status", "{}");
        assert_eq!(code, MIXEL_OK);
        let status: serde_json::Value = serde_json::from_str(&status).unwrap();
        assert_eq!(status["phase"], "offline");
        let (code, message) = call(handle, "no_such_command", "{}");
        assert_eq!(code, MIXEL_FAILED);
        assert!(message.contains("Unknown command"));
        let (code, message) = call(handle, "remote_request", "{}");
        assert_eq!((code, message.as_str()), (MIXEL_FAILED, "Missing or invalid argument: host"));
        // Called from many threads at once.
        let shared = handle as usize;
        let workers: Vec<_> = (0..8)
            .map(|_| std::thread::spawn(move || call(shared as *mut Engine, "device_removal_pending", "{}")))
            .collect();
        for worker in workers {
            assert_eq!(worker.join().unwrap(), (MIXEL_OK, "false".to_owned()));
        }
        let mut error = std::ptr::null_mut();
        assert_eq!(mixel_engine_shutdown_safe(handle, &mut error), MIXEL_OK);
        mixel_engine_free_safe(handle);
    }

    #[test]
    fn bad_pointers_and_texts_fail_without_crashing() {
        let mut result = std::ptr::null_mut();
        assert_eq!(mixel_engine_call_safe(std::ptr::null(), std::ptr::null(), std::ptr::null(), &mut result), MIXEL_FAILED);
        assert_eq!(take(result), "The engine is not running");
        let handle = start();
        let bad = [0xffu8, 0xfe, 0];
        let mut result = std::ptr::null_mut();
        assert_eq!(mixel_engine_call_safe(handle, bad.as_ptr().cast(), std::ptr::null(), &mut result), MIXEL_FAILED);
        assert_eq!(take(result), "Text arguments must be UTF-8");
        let session = CString::new("not-a-uuid").unwrap();
        let (mut data, mut len, mut error) = (std::ptr::null_mut(), 0usize, std::ptr::null_mut());
        assert_eq!(mixel_engine_next_video_safe(handle, session.as_ptr(), 0, &mut data, &mut len, &mut error), MIXEL_FAILED);
        assert!(data.is_null() && len == 0);
        assert_eq!(take(error), "Invalid identifier");
        let event = CString::new("{\"kind\":\"nope\"}").unwrap();
        let session = CString::new("3b241101-e2bb-4255-8caf-4136c566a962").unwrap();
        let mut error = std::ptr::null_mut();
        assert_eq!(mixel_engine_input_safe(handle, session.as_ptr(), event.as_ptr(), std::ptr::null_mut(), &mut error), MIXEL_FAILED);
        assert_eq!(take(error), "Invalid input event");
        mixel_string_free_safe(std::ptr::null_mut());
        mixel_bytes_free_safe(std::ptr::null_mut(), 0);
        mixel_engine_free_safe(std::ptr::null_mut());
        mixel_engine_free_safe(handle);
    }

    #[test]
    fn block_on_waits_for_other_threads() {
        let (sender, receiver) = std::sync::mpsc::channel::<u32>();
        let value = std::sync::Arc::new(std::sync::Mutex::new(None::<u32>));
        struct Later(std::sync::Arc<std::sync::Mutex<Option<u32>>>, Option<std::sync::mpsc::Sender<u32>>);
        impl Future for Later {
            type Output = u32;
            fn poll(mut self: std::pin::Pin<&mut Self>, context: &mut Context<'_>) -> Poll<u32> {
                if let Some(found) = *self.0.lock().unwrap() {
                    return Poll::Ready(found);
                }
                if let Some(sender) = self.1.take() {
                    let (slot, waker) = (self.0.clone(), context.waker().clone());
                    std::thread::spawn(move || {
                        std::thread::sleep(std::time::Duration::from_millis(30));
                        *slot.lock().unwrap() = Some(7);
                        let _ = sender.send(7);
                        waker.wake();
                    });
                }
                Poll::Pending
            }
        }
        assert_eq!(block_on(Later(value, Some(sender))), 7);
        assert_eq!(receiver.recv().unwrap(), 7);
    }
}
