//! Lightweight host cursor-shape detection for native remote cursor synchronization.
//! Returns one of a fixed set of semantic cursor names (`"arrow"`, `"ibeam"`, `"pointing"`,
//! `"resize_ew"`, `"resize_ns"`, `"resize_nwse"`, `"resize_nesw"`, `"crosshair"`,
//! `"not_allowed"`, `"wait"`, `"grab"`).

pub const KNOWN_SHAPES: [&str; 11] = [
    "arrow",
    "ibeam",
    "pointing",
    "resize_ew",
    "resize_ns",
    "resize_nwse",
    "resize_nesw",
    "crosshair",
    "not_allowed",
    "wait",
    "grab",
];

pub fn normalize(name: &str) -> Option<&'static str> {
    KNOWN_SHAPES.iter().copied().find(|&k| k == name)
}

#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn mixel_current_cursor_kind() -> i32;
}

/// Returns the current system cursor shape on this host.
pub fn current_cursor_shape() -> &'static str {
    #[cfg(target_os = "macos")]
    {
        // SAFETY: C shim reads an atomic cache and schedules a non-blocking main-queue refresh.
        let code = unsafe { mixel_current_cursor_kind() };
        match code {
            1 => "ibeam",
            2 => "pointing",
            3 => "resize_ew",
            4 => "resize_ns",
            5 => "crosshair",
            6 => "not_allowed",
            7 => "grab",
            _ => "arrow",
        }
    }
    #[cfg(target_os = "windows")]
    {
        windows_cursor_shape()
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        "arrow"
    }
}

#[cfg(target_os = "windows")]
fn windows_cursor_shape() -> &'static str {
    use windows::Win32::UI::WindowsAndMessaging::{
        CURSORINFO, GetCursorInfo, HCURSOR, IDC_APPSTARTING, IDC_CROSS, IDC_HAND, IDC_IBEAM,
        IDC_NO, IDC_SIZEALL, IDC_SIZENESW, IDC_SIZENS, IDC_SIZENWSE, IDC_SIZEWE, IDC_WAIT,
        LoadCursorW,
    };

    // SAFETY: `CURSORINFO` is zero-initialized with `cbSize` set to its exact byte size.
    let mut info = CURSORINFO {
        cbSize: std::mem::size_of::<CURSORINFO>() as u32,
        ..Default::default()
    };
    let ok = unsafe { GetCursorInfo(&mut info) }.is_ok();
    if !ok || info.hCursor.0.is_null() {
        return "arrow";
    }
    let current = info.hCursor;
    let matches_idc = |idc| -> bool {
        // SAFETY: `LoadCursorW(None, idc)` returns a shared system cursor handle that must not be destroyed.
        unsafe { LoadCursorW(None, idc) }.is_ok_and(|h: HCURSOR| h == current)
    };
    if matches_idc(IDC_IBEAM) {
        "ibeam"
    } else if matches_idc(IDC_HAND) {
        "pointing"
    } else if matches_idc(IDC_SIZEWE) {
        "resize_ew"
    } else if matches_idc(IDC_SIZENS) {
        "resize_ns"
    } else if matches_idc(IDC_SIZENWSE) {
        "resize_nwse"
    } else if matches_idc(IDC_SIZENESW) {
        "resize_nesw"
    } else if matches_idc(IDC_CROSS) {
        "crosshair"
    } else if matches_idc(IDC_NO) {
        "not_allowed"
    } else if matches_idc(IDC_WAIT) || matches_idc(IDC_APPSTARTING) {
        "wait"
    } else if matches_idc(IDC_SIZEALL) {
        "grab"
    } else {
        "arrow"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_accepts_only_known_cursor_shapes() {
        assert_eq!(normalize("ibeam"), Some("ibeam"));
        assert_eq!(normalize("pointing"), Some("pointing"));
        assert_eq!(normalize("arrow"), Some("arrow"));
        assert_eq!(normalize("bogus"), None);
    }
}
