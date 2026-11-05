//! Validated BGRA frames and damage tracking shared by native capture adapters.
//! This module does not itself acquire OS screen or input permissions.
pub mod damage;

#[cfg(target_os = "macos")]
#[allow(unsafe_code)] // Audited native FFI is isolated in this module.
pub mod macos;
pub mod stream;

pub mod input;
