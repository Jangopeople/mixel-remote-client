#!/usr/bin/env python3
"""Build a separate observation-only GUI-connection child; never qualify consent."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "scripts/support-invite-guard.rs", "scripts/test-support-lease.py",
    "scripts/test-support-launch-windows.ps1", "scripts/test-support-lease-standard-windows.ps1",
    "scripts/build-standard-gui-connection-fixture.py",
    "scripts/observe-standard-gui-connection-windows.ps1",
    "scripts/test-standard-gui-connection.py", "scripts/test-standard-gui-connection-source.ps1",
    "scripts/build-standard-lease-qa-bundle.py",
    ".github/workflows/preflight.yml",
)


def section(text, first, last):
    if text.count(first) != 1 or text.count(last) != 1:
        raise ValueError("Exact normal fixture extraction boundary changed")
    start = text.index(first)
    end = text.index(last, start)
    if end <= start:
        raise ValueError("Exact normal fixture extraction order changed")
    return text[start:end]


def normal_class(text):
    """Keep exact documented launcher/ownership/desktop cleanup bodies, omit diagnostics."""
    full = section(text, "public static class MixelOrdinaryTokenFixture {", "\n'@ }\n")
    head = section(full, "public static class MixelOrdinaryTokenFixture {", "  [StructLayout(LayoutKind.Sequential)] struct UnicodeString")
    native_names = (
        "GetCurrentThreadId", "OpenProcess", "GetExitCodeProcess", "GetProcessId",
        "WaitForSingleObject", "TerminateProcess", "CloseHandle", "OpenProcessToken",
        "GetTokenInformation", "CreateProcessWithLogonW", "GetProcessWindowStation",
        "GetThreadDesktop", "GetUserObjectInformationW", "GetUserObjectSecurity",
        "SetUserObjectSecurity", "DeleteProfileW", "GetUserProfileDirectoryW",
    )
    declarations = []
    for name in native_names:
        matches = re.findall(r'^  \[DllImport[^\n]+\b' + name + r'\([^\n]+$', full, re.M)
        if len(matches) != 1:
            raise ValueError("Exact normal native declaration changed: " + name)
        declarations.append(matches[0] + "\n")
    pieces = (
        section(full, "  static bool ElevatedToken(", "  public static bool Elevated("),
        section(full, "  static string UserObjectName(", "  public static string ThreadDesktopName("),
        section(full, "  static StartupInfo Startup()", "  public static bool ActualOwnedDebuggerAttached("),
        section(full, "  static void StopOwnedProcessHandle(", "  // The MiscInfo stream"),
        section(full, "  public static int StartStandardUser(string executable, string username, string password) {", "  static byte[] Descriptor("),
        section(full, "  static byte[] Descriptor(", "  public static bool IsLeaseName("),
    )
    result = "using System;\nusing System.ComponentModel;\nusing System.Runtime.InteropServices;\nusing System.Security.AccessControl;\nusing System.Security.Principal;\nusing System.Text;\n" + head + "".join(declarations) + "".join(pieces) + CONTEXT + "}\n"
    if any(word in result for word in ("NtQuery", "Debugger", "DuplicateHandle", "ResumeThread", "LogonUser", "CreateProcessWithToken")):
        raise ValueError("Unrelated native diagnosis/context path entered observer")
    return result


CONTEXT = r'''
  public static void AssertOwnedContext(int pid, string expectedSid, uint expectedSession) {
    IntPtr process, token;
    if (!StartedProcesses.TryGetValue(pid, out process)) throw new InvalidOperationException("Context observation does not own the process handle");
    if (!OpenProcessToken(process, 8, out token)) throw new Win32Exception(Marshal.GetLastWin32Error());
    try {
      using (var identity = new WindowsIdentity(token)) {
        if (identity.User.Value != expectedSid || ElevatedToken(token)) throw new InvalidOperationException("Observer child is not the owned non-elevated standard identity");
      }
      IntPtr data = Marshal.AllocHGlobal(4);
      try {
        int required;
        if (!GetTokenInformation(token, 12, data, 4, out required)) throw new Win32Exception(Marshal.GetLastWin32Error());
        if (required != 4 || unchecked((uint)Marshal.ReadInt32(data)) != expectedSession) throw new InvalidOperationException("Observer child is not in the caller token session");
      } finally { Marshal.FreeHGlobal(data); }
    } finally { if (!CloseHandle(token)) throw new Win32Exception(Marshal.GetLastWin32Error()); }
  }
'''

GUI_HELPER = r'''
mod gui_connection {
    use std::ffi::c_void;
    type WindowQuery = unsafe extern "system" fn() -> *mut c_void;
    type ThreadDesktop = unsafe extern "system" fn(u32) -> *mut c_void;
    type ObjectInfo = unsafe extern "system" fn(*mut c_void, i32, *mut c_void, u32, *mut u32) -> i32;
    #[cfg(not(test))]
    #[link(name="kernel32")]
    extern "system" {
        fn LoadLibraryExW(name: *const u16, file: *mut c_void, flags: u32) -> *mut c_void;
        fn GetProcAddress(module: *mut c_void, name: *const u8) -> *mut c_void;
        fn FreeLibrary(module: *mut c_void) -> i32;
        fn GetLastError() -> u32;
        fn GetCurrentThreadId() -> u32;
    }
    #[cfg(test)]
    include!("gui-connection-native-mocks.rs");

    pub(super) fn quote(value: &str) -> String {
        let mut output = String::from("\"");
        for character in value.chars() {
            match character {
                '"' => output.push_str("\\\""), '\\' => output.push_str("\\\\"),
                c if c <= '\u{1f}' => output.push_str(&format!("\\u{:04x}", c as u32)),
                c => output.push(c),
            }
        }
        output.push('"'); output
    }
    pub(super) struct Module { handle: Option<usize> }
    impl Module {
        pub(super) fn load() -> (Self, u32) {
            let name: Vec<u16> = "user32.dll".encode_utf16().chain(Some(0)).collect();
            let handle = unsafe { LoadLibraryExW(name.as_ptr(), std::ptr::null_mut(), 0x800) };
            let error = if handle.is_null() { unsafe { GetLastError() } } else { 0 };
            (Self { handle: (!handle.is_null()).then_some(handle as usize) }, error)
        }
        fn address(&self, name: &[u8]) -> Result<*mut c_void, u32> {
            let handle = self.handle.ok_or(0u32)?;
            let pointer = unsafe { GetProcAddress(handle as _, name.as_ptr()) };
            if pointer.is_null() { Err(unsafe { GetLastError() }) } else { Ok(pointer) }
        }
        pub(super) fn close(&mut self) -> (bool, u32) {
            match self.handle {
                None => (true, 0),
                Some(handle) => {
                    let closed = unsafe { FreeLibrary(handle as _) } != 0;
                    let error = if closed { 0 } else { unsafe { GetLastError() } };
                    if closed { self.handle = None; }
                    (closed, error)
                }
            }
        }
        pub(super) fn loaded(&self) -> bool { self.handle.is_some() }
        pub(super) fn desktop_window(&self) -> (bool, u32, bool) {
            let desktop = match self.address(b"GetDesktopWindow\0") {
                Ok(pointer) => pointer,
                Err(error) => return (false, error, false),
            };
            let query: WindowQuery = unsafe { std::mem::transmute(desktop) };
            // The only non-station/non-desktop USER32 call. No window creation,
            // station assignment, hooks, ACLs or event/guard ownership changes.
            let present = !unsafe { query() }.is_null();
            (true, 0, present)
        }
        pub(super) fn names(&self) -> String {
            let names = (|| -> Result<(String, String), u32> {
                let station: WindowQuery = unsafe { std::mem::transmute(self.address(b"GetProcessWindowStation\0")?) };
                let desktop: ThreadDesktop = unsafe { std::mem::transmute(self.address(b"GetThreadDesktop\0")?) };
                let information: ObjectInfo = unsafe { std::mem::transmute(self.address(b"GetUserObjectInformationW\0")?) };
                // These handles are system-owned, not opened by this observer.
                let station = unsafe { station() };
                let station_error = if station.is_null() { unsafe { GetLastError() } } else { 0 };
                let desktop = unsafe { desktop(GetCurrentThreadId()) };
                let desktop_error = if desktop.is_null() { unsafe { GetLastError() } } else { 0 };
                Ok((name(information, station, station_error), name(information, desktop, desktop_error)))
            })();
            match names {
                Ok((station, desktop)) => format!("\"nameResolveError\":0,\"station\":{station},\"desktop\":{desktop}"),
                Err(error) => format!("\"nameResolveError\":{error},\"station\":null,\"desktop\":null"),
            }
        }
    }
    impl Drop for Module {
        fn drop(&mut self) { let _ = self.close(); }
    }
    fn name(information: ObjectInfo, handle: *mut c_void, handle_error: u32) -> String {
        if handle.is_null() { return format!("{{\"name\":null,\"error\":{handle_error},\"length\":0,\"bounded\":false}}"); }
        let mut buffer = [0u16; 256]; let mut needed = 0u32;
        let success = unsafe { information(handle, 2, buffer.as_mut_ptr().cast(), 512, &mut needed) } != 0;
        let error = if success { 0 } else { unsafe { GetLastError() } };
        let end = buffer.iter().position(|value| *value == 0);
        let value = if success && (2..=512).contains(&needed) && needed % 2 == 0 {
            end.filter(|end| *end > 0 && (*end + 1) * 2 == needed as usize)
                .and_then(|end| String::from_utf16(&buffer[..end]).ok())
        } else { None };
        let bounded = value.is_some();
        let value = value.map(|value| quote(&value)).unwrap_or_else(|| "null".to_owned());
        format!("{{\"name\":{value},\"error\":{error},\"length\":{needed},\"bounded\":{bounded}}}")
    }
}
'''

MAIN = r'''
fn observe(stage: &str) -> String {
    let status = support_invite_lease_status();
    let raw_error = mixel_support_lease::fixture_raw_open_error();
    let proof = support_invite_attestation();
    let required = support_invite_requires_click();
    let failed = support_invite_owner_lease_failed();
    format!("{{\"stage\":{},\"pid\":{},\"sourceStatus\":{},\"rawOpenError\":{},\"attestation\":{},\"requiresClick\":{},\"ownerFailed\":{}}}",
        gui_connection::quote(stage), std::process::id(), gui_connection::quote(&status), raw_error,
        gui_connection::quote(proof), required, failed)
}
fn main() {
    assert!(std::env::args().len() == 1, "GUI connection observation accepts no command-line arguments");
    // Retain compilation coverage only; never execute legacy creation controls.
    let _ = lease_main as fn();
    let directory = std::env::current_exe().unwrap().parent().unwrap().to_owned();
    let mode = std::fs::read_to_string(directory.join("gui-connection-mode.txt")).unwrap();
    assert!(matches!(mode.as_str(), "A-before" | "B" | "A-after"));
    let mut observations = vec![observe("before")];
    let gui = if mode == "B" {
        let (mut module, load_error) = gui_connection::Module::load();
        let loaded = module.loaded();
        observations.push(observe("after-load"));
        let (called, resolve_error, present) = module.desktop_window();
        observations.push(observe("after-call"));
        // Name queries follow the after-call probe, avoiding another before-
        // probe difference. They neither assign nor close system-owned handles.
        let names = module.names();
        let (closed, close_error) = module.close();
        format!("{{\"libraryLoaded\":{loaded},\"loadError\":{load_error},\"callAttempted\":{called},\"resolveError\":{resolve_error},\"desktopWindowPresent\":{present},{names},\"moduleClosed\":{closed},\"closeError\":{close_error}}}")
    } else { "null".to_owned() };
    let record = format!("{{\"schema\":\"mixel-gui-connection-observation-v1\",\"mode\":{},\"pid\":{},\"observations\":[{}],\"gui\":{},\"standardQualified\":false,\"fullEightQualified\":false,\"customerAppQualified\":false,\"finalQualified\":false}}\n",
        gui_connection::quote(&mode), std::process::id(), observations.join(","), gui);
    assert!(record.len() <= 16384);
    std::fs::write(directory.join("gui-connection-result.new"), record).unwrap();
    std::fs::rename(directory.join("gui-connection-result.new"), directory.join("gui-connection-result.json")).unwrap();
    let release = directory.join(format!("fixture-release-{}", std::process::id()));
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
    while !release.is_file() && std::time::Instant::now() < deadline {
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    assert!(release.is_file(), "Owned GUI observation was not acknowledged");
    std::fs::remove_file(release).unwrap();
}
'''


def generated_source(directory, event, original=None):
    if not re.fullmatch(r"Global\\Mixel-Remote-Standard-Lease-[0-9a-f]{32}", event):
        raise ValueError("Requires one isolated Global GUID event")
    spec = importlib.util.spec_from_file_location("gui_lease_exporter", ROOT / "scripts/test-support-lease.py")
    lease = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lease)
    original = original if original is not None else (ROOT / SOURCES[0]).read_text(encoding="utf-8")
    body = lease.standard_windows_fixture_body(original, directory, "mixel-gui-observe-" + event[-32:] + "-", event)
    if body.count(lease.STANDARD_WINDOWS_HARNESS) != 1:
        raise ValueError("Exact generated standard child entry changed")
    return body.replace(lease.STANDARD_WINDOWS_HARNESS, GUI_HELPER + MAIN, 1)


def pe_observation_metadata(data):
    """Bound the existing PE reader and refuse a statically connected GUI child."""
    spec = importlib.util.spec_from_file_location("gui_pe_inventory", ROOT / "scripts/build-standard-lease-qa-bundle.py")
    inventory = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inventory)
    imports = inventory.pe_imports(data)
    header = struct.unpack_from("<I", data, 0x3c)[0] + 24
    subsystem = struct.unpack_from("<H", data, header + 68)[0]
    # The existing strict import reader validates PE32+, all directory/header
    # bounds and section-backed import entries before these bounded reads.
    directory_count = struct.unpack_from("<I", data, header + 108)[0]
    delay_import = struct.unpack_from("<II", data, header + 112 + 13 * 8) if directory_count > 13 else (0, 0)
    if subsystem != 3 or any(delay_import) or not set(imports) <= {"advapi32.dll", "kernel32.dll", "ntdll.dll"}:
        raise ValueError("Observer is not the bounded console fixture with deferred USER32 loading")
    return {"subsystem": subsystem, "imports": imports, "delayImportsAbsent": True, "user32StaticImportAbsent": True}


def prepare(directory, evidence, event, compile_native=True):
    directory, evidence = Path(directory), Path(evidence)
    if not directory.is_dir() or directory.is_symlink() or not evidence.is_dir() or evidence.is_symlink():
        raise ValueError("Requires exact already-created owned directories")
    launch = (ROOT / SOURCES[2]).read_text(encoding="utf-8")
    standard = (ROOT / SOURCES[3]).read_text(encoding="utf-8")
    files = {
        "normal-fixture.cs": normal_class(launch),
        "normal-bootstrap.ps1": section(launch, "        $ownedUser = 'mixelqs'", "        $fixtureStage = 'grant owned standard desktop access'"),
        "normal-result-parser.ps1": section(standard, "function Assert-StandardLeaseSafeResult", "function Read-StandardUnnamedEventResult"),
        "gui-connection.rs": generated_source(directory, event),
    }
    for name, content in files.items():
        path = evidence / name
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(content)
    if not compile_native:
        return files
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_JOB") != "standard-gui-connection-observation" or os.environ.get("MIXEL_GUI_CONNECTION_OBSERVATION") != "1":
        raise ValueError("Native observation compilation requires explicit isolated Windows Actions opt-in")
    bindings = []
    for name in SOURCES:
        actual = (ROOT / name).read_bytes()
        committed = subprocess.check_output(["git", "cat-file", "blob", "HEAD:" + name], cwd=ROOT, stderr=subprocess.PIPE)
        if actual != committed:
            raise ValueError("Observer source differs from committed Git blob: " + name)
        bindings.append({"path": name, "bytes": len(actual), "sha256": hashlib.sha256(actual).hexdigest()})
    rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
    if not rustc:
        raise ValueError("Rust compiler is required")
    command = [rustc, "--edition", "2021", "-D", "warnings", "-C", "target-feature=+crt-static", str(evidence / "gui-connection.rs"), "-o", str(directory / "gui-connection.exe")]
    completed = subprocess.run(command, capture_output=True, timeout=90)
    (evidence / "compile.stdout.log").write_bytes(completed.stdout)
    (evidence / "compile.stderr.log").write_bytes(completed.stderr)
    completed.check_returncode()
    binary = (directory / "gui-connection.exe").read_bytes()
    pe = pe_observation_metadata(binary)
    manifest = {"schema": "mixel-gui-connection-source-v1", "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "event": event,
                "sources": bindings, "generated": {name: hashlib.sha256((evidence / name).read_bytes()).hexdigest() for name in files},
                "binarySha256": hashlib.sha256(binary).hexdigest(), "pe": pe,
                "standardQualified": False, "fullEightQualified": False, "customerAppQualified": False, "finalQualified": False}
    (evidence / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("PASS: separate no-argv observation child compiled; exact committed source binding; no native child executed by builder.")
    return files


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--event", required=True)
    parser.add_argument("--source-only", action="store_true", help="Prepare pure source controls; do not compile or execute a child")
    args = parser.parse_args()
    prepare(args.workspace, args.evidence, args.event, compile_native=not args.source_only)
