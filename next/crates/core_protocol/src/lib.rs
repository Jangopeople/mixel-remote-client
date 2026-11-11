//! Shared native/browser wire primitives. No network or OS permissions are granted here.
pub mod clipboard;
pub mod crypto;
pub mod delegation;
pub mod device_login;
pub mod files;
pub mod input;
pub mod session;
pub mod sound;

// Version 2 requires scoped identity tickets and independent encrypted host
// grants. A version-1 peer must fail before attempting the new consent flow.
pub const PROTOCOL_VERSION: u16 = 2;

#[cfg(all(feature = "native-webrtc", not(target_arch = "wasm32")))]
pub mod transport;

pub const MAX_VIDEO_ACCESS_UNIT: usize = 8 * 1024 * 1024;
#[cfg(all(feature = "native-webrtc", not(target_arch = "wasm32")))]
pub mod media;

#[cfg(feature = "native-webrtc")]
pub mod turn_framing;

#[cfg(feature = "native-webrtc")]
mod stream_turn;

// Enabled only by the owned local coturn fixture runner, never by release apps.
#[cfg(all(test, feature = "external-turn-qa", not(target_arch = "wasm32")))]
mod coturn_fixture_tests;
