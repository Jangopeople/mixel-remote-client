//! Shared native/browser wire primitives. No network or OS permissions are granted here.
pub mod crypto;
pub mod input;
pub mod session;

pub const PROTOCOL_VERSION: u16 = 1;

#[cfg(all(feature = "native-webrtc", not(target_arch = "wasm32")))]
pub mod transport;
