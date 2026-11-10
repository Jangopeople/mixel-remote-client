//! Plain text only. Callers must establish a live clipboard grant and an
//! explicit transfer request; there is no background pasteboard watcher.
use objc2_app_kit::NSPasteboard;
use objc2_foundation::{NSString, ns_string};
use zeroize::Zeroizing;

pub fn read_text() -> Result<Zeroizing<String>, String> {
    let board = NSPasteboard::generalPasteboard();
    let change = board.changeCount();
    let value = board
        .stringForType(ns_string!("public.utf8-plain-text"))
        .ok_or("The clipboard has no available plain text")?;
    if value.length() > core_protocol::clipboard::MAX_TEXT {
        return Err("Clipboard text exceeds the 1 MiB transfer limit".into());
    }
    let text = Zeroizing::new(value.to_string());
    core_protocol::clipboard::validate(&text)
        .map_err(|_| "Clipboard text exceeds the limit or contains unsupported NUL characters")?;
    if board.changeCount() != change {
        return Err("The clipboard changed while being read; try again".into());
    }
    Ok(text)
}
pub fn write_text(text: &str) -> Result<(), String> {
    core_protocol::clipboard::validate(text).map_err(|_| "Invalid clipboard text")?;
    let value = NSString::from_str(text);
    let board = NSPasteboard::generalPasteboard();
    board.clearContents();
    if !board.setString_forType(&value, ns_string!("public.utf8-plain-text")) {
        return Err("macOS did not accept the clipboard text".into());
    }
    Ok(())
}
