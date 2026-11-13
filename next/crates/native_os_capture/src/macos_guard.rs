//! Privacy screen, input block, pointer ring and sound for a Mac being helped (see
//! `macos_guard.m`). The cover is left out of the technician's picture: its windows are
//! excluded from every running capture before the cover becomes visible.
use std::{
    ffi::{CString, c_void},
    sync::mpsc,
    time::{Duration, Instant},
};

unsafe extern "C" {
    static MIXEL_INPUT_MARK: i64;
    fn mixel_privacy_create(text: *const std::ffi::c_char, ids: *mut u32, capacity: i32) -> i32;
    fn mixel_privacy_reveal();
    fn mixel_privacy_close();
    fn mixel_pointer_move(x: f64, y: f64);
    fn mixel_pointer_hide();
    fn mixel_accessibility_trusted() -> bool;
    fn mixel_block_start() -> i32;
    fn mixel_block_stop();
    fn mixel_block_active() -> bool;
    fn mixel_audio_start(callback: extern "C" fn(*mut c_void, *const f32, usize), context: *mut c_void, error: *mut i32) -> *mut c_void;
    fn mixel_audio_stop(handle: *mut c_void);
    fn mixel_lock_available() -> bool;
}

/// The value Mixel Remote puts into `kCGEventSourceUserData` of the events it posts.
pub fn input_mark() -> i64 {
    // SAFETY: a constant defined in the shim.
    unsafe { MIXEL_INPUT_MARK }
}

/// Covers every display. The windows are excluded from capture first; only then they show.
pub fn privacy_on(text: &str) -> Result<(), String> {
    let text = CString::new(text).map_err(|_| "Invalid privacy text")?;
    let mut ids = [0u32; 16];
    // SAFETY: the buffer holds 16 entries; the shim writes at most `capacity`.
    let count = unsafe { mixel_privacy_create(text.as_ptr(), ids.as_mut_ptr(), ids.len() as i32) };
    if count <= 0 {
        return Err("macOS did not open the privacy screen".into());
    }
    let ids = ids[..(count as usize).min(ids.len())].to_vec();
    let version = crate::macos::set_excluded_windows(ids);
    // Wait until each running capture has left the cover out (normally a few milliseconds).
    let deadline = Instant::now() + Duration::from_millis(1500);
    while !crate::macos::exclusions_applied(version) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    // SAFETY: no arguments; acts on the windows created above.
    unsafe { mixel_privacy_reveal() };
    Ok(())
}

pub fn privacy_off() {
    // SAFETY: no arguments; closes the cover windows if there are any.
    unsafe { mixel_privacy_close() };
    crate::macos::set_excluded_windows(Vec::new());
}

/// Shows the ring at a point in global display coordinates (top left of the main display = 0,0).
pub fn pointer_at(x: f64, y: f64) {
    if x.is_finite() && y.is_finite() {
        // SAFETY: plain values; the shim moves its own window on the main thread.
        unsafe { mixel_pointer_move(x, y) };
    }
}

pub fn pointer_hide() {
    // SAFETY: no arguments.
    unsafe { mixel_pointer_hide() };
}

/// Whether macOS allows this app to filter keyboard and mouse events (Accessibility).
pub fn accessibility_trusted() -> bool {
    // SAFETY: no arguments; reads the permission state.
    unsafe { mixel_accessibility_trusted() }
}

#[derive(Debug, PartialEq, Eq)]
pub enum BlockError {
    Accessibility,
    Refused,
}

/// Drops keyboard and mouse events that Mixel Remote did not post.
pub fn block_on() -> Result<(), BlockError> {
    // SAFETY: no arguments; the shim owns the event tap and its thread.
    match unsafe { mixel_block_start() } {
        0 => Ok(()),
        1 => Err(BlockError::Accessibility),
        _ => Err(BlockError::Refused),
    }
}

pub fn block_off() {
    // SAFETY: no arguments.
    unsafe { mixel_block_stop() };
}

/// False after the person at the Mac pressed Control+Option+Command+Delete.
pub fn block_active() -> bool {
    // SAFETY: no arguments.
    unsafe { mixel_block_active() }
}

/// Whether the system lock call can be found (it is looked up, not called).
pub fn lock_available() -> bool {
    // SAFETY: no arguments; dlopen/dlsym lookups only.
    unsafe { mixel_lock_available() }
}

#[derive(Debug, PartialEq, Eq)]
pub enum SoundError {
    Permission,
    Unavailable,
    TooOld,
}

/// What the Mac plays, as interleaved stereo samples at 48 kHz.
pub struct Sound {
    handle: *mut c_void,
    context: *mut mpsc::SyncSender<Vec<f32>>,
    pub samples: mpsc::Receiver<Vec<f32>>,
}
// SAFETY: the handle and context are only used to stop the capture, once, in Drop.
unsafe impl Send for Sound {}

extern "C" fn deliver(context: *mut c_void, stereo: *const f32, frames: usize) {
    if context.is_null() || stereo.is_null() || frames == 0 {
        return;
    }
    // SAFETY: the context is the sender boxed in `Sound::start`, alive until the capture stopped;
    // the shim passes `frames * 2` samples.
    let (sender, samples) = unsafe { (&*(context as *const mpsc::SyncSender<Vec<f32>>), std::slice::from_raw_parts(stereo, frames * 2)) };
    // A full queue means the session is busy: drop rather than lag.
    let _ = sender.try_send(samples.to_vec());
}

impl Sound {
    pub fn start() -> Result<Self, SoundError> {
        let (sender, samples) = mpsc::sync_channel(64);
        let context = Box::into_raw(Box::new(sender));
        let mut error = 0;
        // SAFETY: `deliver` matches the callback type; the context stays alive until Drop.
        let handle = unsafe { mixel_audio_start(deliver, context.cast(), &mut error) };
        if handle.is_null() {
            // SAFETY: the capture did not start, so nothing else holds the context.
            drop(unsafe { Box::from_raw(context) });
            return Err(match error {
                1 => SoundError::Permission,
                3 => SoundError::TooOld,
                _ => SoundError::Unavailable,
            });
        }
        Ok(Self { handle, context, samples })
    }
}

impl Drop for Sound {
    fn drop(&mut self) {
        // SAFETY: stops the capture and waits for its last callback before the context is freed.
        unsafe {
            mixel_audio_stop(self.handle);
            drop(Box::from_raw(self.context));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_input_mark_and_lock_call_are_present() {
        assert_eq!(input_mark(), 0x004D_5852_4D54);
        assert!(lock_available(), "SACLockScreenImmediate is found in login.framework");
    }

    #[test]
    fn delivered_sound_is_copied_and_bounded() {
        let (sender, samples) = mpsc::sync_channel::<Vec<f32>>(1);
        let context = Box::into_raw(Box::new(sender));
        let stereo = [0.25f32, -0.25, 0.5, -0.5];
        deliver(context.cast(), stereo.as_ptr(), 2);
        // A full queue drops instead of blocking the sound thread.
        deliver(context.cast(), stereo.as_ptr(), 2);
        deliver(std::ptr::null_mut(), stereo.as_ptr(), 2);
        assert_eq!(samples.try_recv().unwrap(), stereo.to_vec());
        assert!(samples.try_recv().is_err());
        // SAFETY: created above, not used afterwards.
        drop(unsafe { Box::from_raw(context) });
    }

    #[test]
    fn without_screen_recording_permission_sound_does_not_start() {
        // Test binaries are never allowed to record the screen; the check must not prompt.
        if crate::macos::screen_access() {
            return;
        }
        assert_eq!(Sound::start().err(), Some(SoundError::Permission));
    }
}
