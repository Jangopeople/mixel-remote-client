//! Validated BGRA frames and damage tracking shared by native capture adapters.
//! This module does not itself acquire OS screen or input permissions.
pub mod damage;

#[cfg(target_os = "macos")]
#[allow(unsafe_code)] // Audited native FFI is isolated in this module.
pub mod macos;
pub mod stream;

pub mod input;

pub mod h264;
#[cfg(any(target_os = "windows", target_os = "linux", test))]
pub mod desktop_h264;
#[cfg(target_os = "macos")]
pub mod macos_bundle;
#[cfg(any(target_os = "macos", target_os = "ios"))]
#[allow(unsafe_code)] // Owner-thread VideoToolbox decoding and read-locked output copies.
pub mod macos_decoder;
#[cfg(any(target_os = "macos", target_os = "ios"))]
#[allow(unsafe_code)] // Read-locked CoreVideo pixel copies.
mod apple_pixels;
#[cfg(target_os = "macos")]
#[allow(unsafe_code)] // VideoToolbox FFI and pixel-buffer lifetimes are isolated here.
pub mod macos_encoder;
#[cfg(target_os = "macos")]
pub mod macos_screen_encoder;

#[cfg(any(target_os = "macos", test))]
mod h264_low_delay;

#[cfg(target_os = "macos")]
#[allow(unsafe_code)] // Bounded CoreGraphics output buffers and Unicode event data.
pub mod macos_input;
#[cfg(target_os = "macos")]
#[allow(unsafe_code)] // Owns the Apple virtual keyboard and its bounded HID reports.
mod macos_login_keyboard;

#[cfg(target_os = "macos")]
#[allow(unsafe_code)] // Objective-C shim: cover windows, event tap, pointer ring, sound capture.
pub mod macos_guard;
#[cfg(target_os = "macos")]
pub mod macos_clipboard;
#[cfg(target_os = "macos")]
#[allow(unsafe_code)] // Main-thread panel cancellation and retained NSURL filesystem bytes.
pub mod macos_file_picker;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux", target_os = "ios"))]
#[allow(unsafe_code)] // Retained directory capabilities and exclusive native publication.
pub mod desktop_files;

#[cfg(target_os = "windows")]
#[allow(unsafe_code)] // DXGI/D3D resource acquisition, map/unmap and bounded pixel copies.
pub mod windows_capture;

#[cfg(target_os = "windows")]
#[allow(unsafe_code)] // Initialized SendInput unions and current desktop metric calls.
pub mod windows_input;

#[cfg(target_os = "linux")]
pub mod x11_capture;
#[cfg(target_os = "linux")]
pub mod x11_input;

#[cfg(target_os = "macos")]
#[allow(unsafe_code)]
pub mod macos_controller_keychain;
