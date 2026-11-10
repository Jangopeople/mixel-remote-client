//! Local AppKit consent only. The native desktop coordinator owns the panel on
//! its main thread and cancels it when its session/account grant is revoked.
//! Neither selected paths nor Objective-C pointers are webview IPC values.
use objc2::{MainThreadMarker, rc::Retained};
use objc2_app_kit::{NSModalResponseCancel, NSModalResponseOK, NSOpenPanel, NSSavePanel};
use objc2_foundation::NSString;
use std::{
    ffi::{CStr, OsStr},
    os::unix::ffi::OsStrExt,
    path::PathBuf,
};

#[derive(Clone)]
enum Panel {
    Open(Retained<NSOpenPanel>),
    DestinationFolder(Retained<NSOpenPanel>, String),
    Save(Retained<NSSavePanel>),
}
#[derive(Clone)]
pub struct FilePicker(Panel);
impl FilePicker {
    pub fn open() -> Result<Self, String> {
        let main =
            MainThreadMarker::new().ok_or("File selection must run on the Mac main thread")?;
        let panel = NSOpenPanel::openPanel(main);
        panel.setCanChooseFiles(true);
        panel.setCanChooseDirectories(false);
        panel.setAllowsMultipleSelection(false);
        panel.setResolvesAliases(false);
        panel.setTreatsFilePackagesAsDirectories(true);
        panel.setTitle(Some(&NSString::from_str("Send a file with Mixel Remote")));
        panel.setMessage(Some(&NSString::from_str(
            "Choose the file you want to send to the connected person.",
        )));
        Ok(Self(Panel::Open(panel)))
    }
    pub fn save(suggestion: &str) -> Result<Self, String> {
        Self::save_named(
            suggestion,
            "Receive a file with Mixel Remote",
            "Choose a new filename. Existing files are preserved.",
        )
    }
    pub fn save_recording() -> Result<Self, String> {
        Self::save_named(
            "Mixel Remote recording.mp4",
            "Save the Mixel Remote session recording",
            "Choose a new MP4 filename for the recording.",
        )
    }
    pub fn receive_into_folder(filename: &str) -> Result<Self, String> {
        Self::destination_folder(filename, "Choose where to save the received file")
    }
    pub fn record_into_folder(filename: &str) -> Result<Self, String> {
        Self::destination_folder(filename, "Choose where to save the session recording")
    }
    fn destination_folder(filename: &str, title: &str) -> Result<Self, String> {
        if !core_protocol::files::valid_name(filename) {
            return Err("The proposed filename is not supported".into());
        }
        let main =
            MainThreadMarker::new().ok_or("Folder selection must run on the Mac main thread")?;
        let panel = NSOpenPanel::openPanel(main);
        panel.setCanChooseFiles(false);
        panel.setCanChooseDirectories(true);
        panel.setCanCreateDirectories(true);
        panel.setAllowsMultipleSelection(false);
        panel.setResolvesAliases(false);
        panel.setTreatsFilePackagesAsDirectories(false);
        panel.setTitle(Some(&NSString::from_str(title)));
        panel.setMessage(Some(&NSString::from_str(&format!(
            "Save “{filename}” in the selected folder. Existing files are preserved."
        ))));
        Ok(Self(Panel::DestinationFolder(panel, filename.to_owned())))
    }
    fn save_named(suggestion: &str, title: &str, message: &str) -> Result<Self, String> {
        if !core_protocol::files::valid_name(suggestion) {
            return Err("The offered filename is not supported".into());
        }
        let main =
            MainThreadMarker::new().ok_or("File selection must run on the Mac main thread")?;
        let panel = NSSavePanel::savePanel(main);
        panel.setCanCreateDirectories(true);
        panel.setTreatsFilePackagesAsDirectories(true);
        panel.setTitle(Some(&NSString::from_str(title)));
        panel.setMessage(Some(&NSString::from_str(message)));
        panel.setNameFieldStringValue(&NSString::from_str(suggestion));
        Ok(Self(Panel::Save(panel)))
    }
    fn panel(&self) -> &NSSavePanel {
        match &self.0 {
            Panel::Open(panel) | Panel::DestinationFolder(panel, _) => panel,
            Panel::Save(panel) => panel,
        }
    }
    pub fn run(&self) -> Result<Option<PathBuf>, String> {
        let response = self.panel().runModal();
        if response == NSModalResponseCancel {
            return Ok(None);
        }
        if response != NSModalResponseOK {
            return Err("macOS could not complete file selection".into());
        }
        let url = self
            .panel()
            .URL()
            .filter(|url| url.isFileURL())
            .ok_or("Choose a local filesystem destination")?;
        let pointer = url.fileSystemRepresentation();
        // SAFETY: NSURL documents a NUL-terminated filesystem representation;
        // the retained file URL stays alive until its bytes are copied. Keep
        // original filesystem bytes instead of lossy Unicode path conversion.
        let bytes = unsafe { CStr::from_ptr(pointer.as_ptr()) }.to_bytes();
        if bytes.len() > 16384 {
            return Err("The selected path is too long".into());
        }
        let path = PathBuf::from(OsStr::from_bytes(bytes));
        if !path.is_absolute() {
            return Err("macOS returned an invalid file selection".into());
        }
        Ok(Some(match &self.0 {
            Panel::DestinationFolder(_, filename) => path.join(filename),
            _ => path,
        }))
    }
    pub fn cancel(&self) {
        // SAFETY: nil is a valid programmatic action sender. The retained panel
        // is main-thread-only, so neither it nor this call can migrate workers.
        unsafe {
            self.panel().cancel(None);
        }
    }
}
