#!/usr/bin/env python3
"""Run the actual generated kernel lease across independent native processes.

The fixed endpoint's name/root are isolated in the compiled fixture. Owner and
service probe code comes byte-for-byte from the shipped guard template; a test
bridge exposes creation errors. Windows uses an allocator that clears last-error
on cleanup to exercise the API's required immediate error capture.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_BRIDGE = r'''
    // Exact prior Windows probe body from application source 11176863fa3e23c6ce113f8392eb591786a5cd4c.
    // Only its name/visibility differ; endpoint isolation is shared with the corrected code.
    #[cfg(windows)]
    pub(super) fn prior_windows_probe() -> std::io::Result<bool> {
        let handle = unsafe { OpenEventW(0x00100000, 0, wide(EVENT).as_ptr()) };
        if handle.is_null() {
            return if unsafe { GetLastError() } == 2 {
                Ok(false)
            } else {
                Err(std::io::Error::last_os_error())
            };
        }
        unsafe {
            CloseHandle(handle);
        }
        Ok(true)
    }

    #[cfg(windows)]
    pub(super) fn fixture_create_failure_error() -> i32 {
        match create() {
            Err(error) => error.raw_os_error().unwrap(),
            Ok(_) => 0,
        }
    }

    #[cfg(windows)]
    pub(super) fn fixture_raw_open_error() -> u32 {
        let name = wide(EVENT);
        let handle = unsafe { OpenEventW(0x00100000, 0, name.as_ptr()) };
        if handle.is_null() {
            return unsafe { GetLastError() };
        }
        unsafe { CloseHandle(handle); }
        0
    }
'''
HARNESS = r'''
#[cfg(windows)]
struct LastErrorClearingAllocator;
#[cfg(windows)]
unsafe impl std::alloc::GlobalAlloc for LastErrorClearingAllocator {
    unsafe fn alloc(&self, layout: std::alloc::Layout) -> *mut u8 {
        std::alloc::System.alloc(layout)
    }
    unsafe fn dealloc(&self, pointer: *mut u8, layout: std::alloc::Layout) {
        std::alloc::System.dealloc(pointer, layout);
        #[link(name="kernel32")]
        extern "system" { fn SetLastError(error:u32); }
        SetLastError(0);
    }
}
#[cfg(windows)]
#[global_allocator]
static CLEARING_ALLOCATOR: LastErrorClearingAllocator = LastErrorClearingAllocator;

fn main() {
    match std::env::args().nth(1).as_deref() {
        Some("owner") => {
            assert!(hold_support_invite_attended_lease());
            assert!(hold_support_invite_attended_lease());
            assert!(!support_invite_owner_lease_failed());
            assert_eq!(support_invite_attestation(), SUPPORT_INVITE_ATTESTATION);
            assert!(support_invite_lease_status().ends_with("memoryActive=true"));
            println!("owner-ready");
            use std::io::Write;
            std::io::stdout().flush().unwrap();
            std::thread::sleep(std::time::Duration::from_secs(120));
        }
        Some("probe") => println!("{}", support_invite_requires_click()),
        Some("raw-open-error") => {
            #[cfg(windows)] println!("{}", mixel_support_lease::fixture_raw_open_error());
        }
        Some("attestation") => println!("{}", support_invite_attestation()),
        Some("status") => {
            let status = support_invite_lease_status();
            assert_eq!(status, support_invite_lease_status());
            if status.contains(";kernel=absent;") {
                assert!(!support_invite_requires_click());
                assert_eq!(support_invite_attestation(), "");
            }
            println!("{}", status);
        }
        Some("renew-attestation") => {
            renew_support_invite_attended();
            assert!(support_invite_requires_click());
            println!("{}", support_invite_attestation());
        }
        Some("renew") => {
            renew_support_invite_attended();
            assert!(support_invite_requires_click());
            assert!(!support_invite_owner_lease_failed());
            assert_eq!(support_invite_attestation(), SUPPORT_INVITE_ATTESTATION);
            println!("memory-only");
        }
        Some("prior-probe-error-capture") => {
            #[cfg(windows)] {
                assert!(mixel_support_lease::prior_windows_probe().unwrap_or(true));
                assert_eq!(mixel_support_lease::prior_windows_probe().unwrap_err().raw_os_error(), Some(0));
                assert!(!support_invite_requires_click());
                assert_eq!(support_invite_attestation(), "");
            }
            println!("prior-probe-unknown-reproduced-corrected-safely-absent");
        }
        Some("wrong-object") => {
            #[cfg(windows)] {
                use std::ffi::c_void;
                #[link(name="kernel32")]
                extern "system" {
                    fn CreateMutexW(attributes: *const c_void, owner:i32, name:*const u16)->*mut c_void;
                }
                let name: Vec<u16> = std::env::args().nth(2).unwrap().encode_utf16().chain(Some(0)).collect();
                let handle=unsafe {CreateMutexW(std::ptr::null(),0,name.as_ptr())};
                assert!(!handle.is_null());
                assert_eq!(mixel_support_lease::fixture_create_failure_error(),6);
            }
            println!("wrong-object-ready");
            use std::io::Write;
            std::io::stdout().flush().unwrap();
            std::thread::sleep(std::time::Duration::from_secs(120));
        }
        Some("exclusive-retry") => {
            #[cfg(unix)] {
                use std::os::unix::{fs::{DirBuilderExt,OpenOptionsExt},io::AsRawFd};
                extern "C" { fn flock(fd:i32, operation:i32)->i32; }
                let path=std::path::PathBuf::from(std::env::args().nth(2).unwrap());
                std::fs::DirBuilder::new().mode(0o700).create(&path).unwrap();
                let exclusive=std::fs::OpenOptions::new().create(true).read(true).write(true).mode(0o600).open(path.join("lease")).unwrap();
                assert_eq!(unsafe {flock(exclusive.as_raw_fd(),2|4)},0);
                assert!(!hold_support_invite_attended_lease());
                assert!(support_invite_owner_lease_failed());
                assert_eq!(unsafe {flock(exclusive.as_raw_fd(),8)},0);
                assert!(hold_support_invite_attended_lease());
                assert!(!support_invite_owner_lease_failed());
            }
            println!("exclusive-lock-bounded-retry");
        }
        Some("repair") => {
            #[cfg(unix)] {
                let path=std::path::PathBuf::from(std::env::args().nth(2).unwrap());
                assert!(hold_support_invite_attended_lease());
                std::fs::remove_file(path.join("lease")).unwrap();
                std::fs::remove_dir(&path).unwrap();
                assert!(hold_support_invite_attended_lease());
                assert!(!support_invite_owner_lease_failed());
            }
            println!("owner-ready");
            use std::io::Write;
            std::io::stdout().flush().unwrap();
            std::thread::sleep(std::time::Duration::from_secs(120));
        }
        Some("retry") => {
            #[cfg(unix)] {
                use std::os::unix::fs::PermissionsExt;
                let path = std::path::PathBuf::from(std::env::args().nth(2).unwrap());
                std::fs::create_dir(&path).unwrap();
                std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o777)).unwrap();
                assert!(!hold_support_invite_attended_lease());
                assert!(support_invite_owner_lease_failed());
                assert!(support_invite_lease_status().contains("ownerFailed=true;"));
                assert!(support_invite_lease_status().ends_with("memoryActive=true"));
                assert!(support_invite_requires_click());
                assert_eq!(support_invite_attestation(), "guard-unavailable");
                std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
                assert!(hold_support_invite_attended_lease());
                assert!(!support_invite_owner_lease_failed());
                assert!(support_invite_lease_status().starts_with("ownerFailed=false;ownerError=none;kernel=active;"));
            }
            #[cfg(windows)] {
                use std::ffi::c_void;
                #[link(name="kernel32")]
                extern "system" {
                    fn CreateMutexW(attributes: *const c_void, owner: i32, name: *const u16) -> *mut c_void;
                    fn CloseHandle(handle: *mut c_void) -> i32;
                }
                let name: Vec<u16> = std::env::args().nth(2).unwrap().encode_utf16().chain(Some(0)).collect();
                let mutex = unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
                assert!(!mutex.is_null());
                assert!(!hold_support_invite_attended_lease());
                assert!(support_invite_owner_lease_failed());
                assert!(support_invite_lease_status().starts_with("ownerFailed=true;ownerError=6;kernel=unavailable;probeError=6;"));
                assert!(support_invite_lease_status().ends_with("memoryActive=true"));
                assert!(support_invite_requires_click());
                assert_eq!(support_invite_attestation(), "guard-unavailable");
                unsafe { CloseHandle(mutex); }
                assert!(hold_support_invite_attended_lease());
                assert!(!support_invite_owner_lease_failed());
                assert!(support_invite_lease_status().starts_with("ownerFailed=false;ownerError=none;kernel=active;"));
            }
            println!("owner-retry-clears-failure");
        }
        _ => panic!("unknown isolated lease test command"),
    }
}
'''


STANDARD_WINDOWS_HARNESS = r'''
fn main() {
    // Keep the existing native negative-control branches compiled with -D warnings.
    if std::env::args().nth(1).is_some() {
        lease_main();
        return;
    }
    #[cfg(windows)] {
        let directory = std::env::current_exe().unwrap().parent().unwrap().to_owned();
        let mode = std::fs::read_to_string(directory.join("fixture-mode.txt")).unwrap();
        match mode.as_str() {
            "probe" => {}
            "owner" => { assert!(hold_support_invite_attended_lease()); }
            "failed-owner" => { assert!(!hold_support_invite_attended_lease()); }
            _ => panic!("Unknown owned standard-account functional control"),
        }
        let status = support_invite_lease_status();
        let raw_error = mixel_support_lease::fixture_raw_open_error();
        let proof = support_invite_attestation();
        let required = support_invite_requires_click();
        let failed = support_invite_owner_lease_failed();
        assert!(matches!(proof, "" | "attended-runtime-v2" | "guard-unavailable"));
        let result = format!(
            "{{\"pid\":{},\"sourceStatus\":\"{}\",\"rawOpenError\":{},\"attestation\":\"{}\",\"requiresClick\":{},\"ownerFailed\":{}}}\n",
            std::process::id(), status, raw_error, proof, required, failed);
        std::fs::write(directory.join("fixture-result.new"), result).unwrap();
        std::fs::rename(directory.join("fixture-result.new"), directory.join("fixture-result.json")).unwrap();
        if mode == "owner" {
            std::thread::sleep(std::time::Duration::from_secs(120));
        } else {
            // Retain the actual token until the parent has verified this exact
            // PID/result. A fixed sleep would race a heavily loaded CI runner.
            let release = directory.join(format!("fixture-release-{}", std::process::id()));
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
            while !release.is_file() && std::time::Instant::now() < deadline {
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            assert!(release.is_file(), "Owned standard-account result was not acknowledged");
            std::fs::remove_file(release).unwrap();
        }
    }
    #[cfg(not(windows))]
    panic!("This standard-account functional control requires actual Windows");
}
'''


def isolated_source(original, directory, prefix, event):
    source, count = re.subn(r'const ROOT: &str = "(?:/tmp|/private/tmp)";',
                           lambda _: "const ROOT: &str = " + json.dumps(str(directory)) + ";", original)
    assert count == 2, "Native lease root isolation marker changed"
    source, count = re.subn(r'const PREFIX: &str = "mixel-remote-attended-v[0-9]+-";',
                           lambda _: "const PREFIX: &str = " + json.dumps(prefix) + ";", source)
    assert count == 1, "Native owner directory marker changed"
    source, count = re.subn(r'const EVENT: &str = "Global\\\\Mixel-Remote-Attended-Runtime-v[0-9]+";',
                           lambda _: "const EVENT: &str = " + json.dumps(event) + ";", source)
    assert count == 1, "Windows native event isolation marker changed"
    bridge_marker = "\n}\n\npub fn hold_support_invite_attended_lease()"
    assert source.count(bridge_marker) == 1, "Native lease fixture bridge boundary changed"
    return source.replace(bridge_marker, WINDOWS_BRIDGE + bridge_marker, 1)


def export_standard_windows_fixture(directory, event):
    assert os.name == "nt", "Standard-account fixture compilation requires actual Windows"
    assert re.fullmatch(r"Global\\Mixel-Remote-Standard-Lease-[0-9a-f]{32}", event), "Expected an isolated GUID event name"
    directory = directory.resolve()
    assert directory.is_dir() and not directory.is_symlink(), "Expected an owned existing fixture directory"
    original = (ROOT / "scripts/support-invite-guard.rs").read_text(encoding="utf-8")
    assert "pub fn support_invite_lease_status() -> String" in original, "Actual read-only lease classification helper is missing"
    source = isolated_source(original, directory, "mixel-standard-lease-" + uuid.uuid4().hex + "-", event)
    fixture = directory / "lease-standard.rs"
    fixture.write_text(source + HARNESS.replace("fn main() {", "fn lease_main() {", 1) + STANDARD_WINDOWS_HARNESS, encoding="utf-8")
    binary = directory / "lease-standard.exe"
    rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
    assert rustc, "Native standard-account lease control requires rustc"
    subprocess.run([rustc, "--edition", "2021", "-D", "warnings", str(fixture), "-o", str(binary)], check=True, timeout=90)
    print("PASS: actual source-extracted standard-account lease control compiles with no Rust warnings", flush=True)


def main():
    original = (ROOT / "scripts/support-invite-guard.rs").read_text(encoding="utf-8")
    owners = []
    with tempfile.TemporaryDirectory(prefix="mixel-kernel-lease-test-") as temporary:
        directory = Path(temporary)
        prefix = "mixel-lease-test-" + uuid.uuid4().hex + "-"
        event = "Global\\Mixel-Remote-Test-Lease-" + uuid.uuid4().hex
        source = isolated_source(original, directory, prefix, event)
        fixture = directory / "lease.rs"
        fixture.write_text(source + HARNESS, encoding="utf-8")
        binary = directory / ("lease.exe" if os.name == "nt" else "lease")
        rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
        assert rustc, "Native lease tests require rustc"
        subprocess.run([rustc, "--edition", "2021", "-D", "warnings", str(fixture), "-o", str(binary)], check=True)

        def command(*arguments):
            return subprocess.check_output([str(binary), *arguments], text=True, timeout=10).strip()

        def probe(expected, attestation=None):
            assert command("probe") == str(expected).lower(), "Fresh service process returned wrong attended requirement"
            if attestation is None:
                attestation = "attended-runtime-v2" if expected else ""
            assert command("attestation") == attestation, "Fresh probe must not attest readiness from unknown ownership"
            status = command("status")
            fields = dict(part.split("=", 1) for part in status.split(";"))
            assert fields.keys() == {"ownerFailed", "ownerError", "kernel", "probeError", "memoryActive"}, "Native status contract changed"
            assert fields["ownerFailed"] == "false" and fields["ownerError"] == "none" and fields["memoryActive"] == "false", "Read-only fresh status created owner/memory state"
            kernel = "unavailable" if attestation == "guard-unavailable" else "active" if expected else "absent"
            assert fields["kernel"] == kernel, "Read-only native status omitted the actual kernel state"
            if kernel == "unavailable":
                assert re.fullmatch(r"-?[0-9]+", fields["probeError"]), "Unavailable native status omitted its numeric error"
            else:
                assert fields["probeError"] == "none", "Known native state reported a probe error"

        try:
            probe(False)
            if os.name == "nt":
                assert command("prior-probe-error-capture") == "prior-probe-unknown-reproduced-corrected-safely-absent"
                print("PASS: actual prior Windows temporary-name probe loses absent error during allocator cleanup; corrected immediate capture safely returns absent", flush=True)
            assert command("renew") == "memory-only"
            probe(False)
            print("PASS: IPC memory renewal never creates an owner lease", flush=True)
            for _ in range(2):
                owner = subprocess.Popen([str(binary), "owner"], stdout=subprocess.PIPE, text=True)
                owners.append(owner)
                assert owner.stdout.readline().strip() == "owner-ready", "Foreground owner could not acquire its kernel lease"
            for _ in range(3):
                probe(True)
            print("PASS: three fresh service processes observe two live UI owners", flush=True)
            owners[0].kill()
            owners[0].wait(timeout=10)
            probe(True)
            print("PASS: first owner crash preserves the second owner's consent requirement", flush=True)
            owners[1].kill()
            owners[1].wait(timeout=10)
            probe(False)
            print("PASS: final owner process exit releases kernel lease and restores saved behavior", flush=True)
            if os.name != "nt":
                lease_directory = directory / (prefix + str(os.geteuid()))
                assert lease_directory.stat().st_mode & 0o7777 == 0o700
                assert (lease_directory / "lease").stat().st_mode & 0o7777 == 0o600
                (lease_directory / "lease").unlink()
                lease_directory.rmdir()
                # Never follow another user's replacement endpoint/symlinks.
                target = directory / "unrelated"
                target.mkdir()
                lease_directory.symlink_to(target, target_is_directory=True)
                probe(True, "guard-unavailable")
                assert command("renew-attestation") == "guard-unavailable"
                assert not (target / "lease").exists()
                lease_directory.unlink()
                lease_directory.mkdir(mode=0o700)
                (lease_directory / "lease").symlink_to(fixture)
                probe(True, "guard-unavailable")
                assert command("renew-attestation") == "guard-unavailable"
                (lease_directory / "lease").unlink()
                (lease_directory / "lease").write_text("", encoding="utf-8")
                (lease_directory / "lease").chmod(0o600)
                hard_link = directory / "hard-linked-lease"
                hard_link.hardlink_to(lease_directory / "lease")
                probe(True, "guard-unavailable")
                assert command("renew-attestation") == "guard-unavailable"
                hard_link.unlink()
                (lease_directory / "lease").unlink()
                lease_directory.rmdir()
                print("PASS: owner0700/file0600; symlink and hard-link endpoints fail closed without traversal", flush=True)
                retry_argument = str(lease_directory)
            else:
                retry_argument = event
                wrong = subprocess.Popen([str(binary), "wrong-object", event], stdout=subprocess.PIPE, text=True)
                owners.append(wrong)
                assert wrong.stdout.readline().strip() == "wrong-object-ready"
                probe(True, "guard-unavailable")
                assert command("renew-attestation") == "guard-unavailable"
                wrong.kill()
                wrong.wait(timeout=10)
                probe(False)
                print("PASS: actual Windows wrong-object creation retains error6 despite allocator cleanup; fresh non-owner probe and renewed memory deny v2 readiness while authorization remains fail closed", flush=True)
            assert command("retry", retry_argument) == "owner-retry-clears-failure"
            probe(False)
            print("PASS: failed owner acquisition cannot attest ready; successful retry clears failure", flush=True)
            if os.name != "nt":
                (lease_directory / "lease").unlink()
                lease_directory.rmdir()
                assert command("exclusive-retry", str(lease_directory)) == "exclusive-lock-bounded-retry"
                probe(False)
                print("PASS: exclusive lease contention cannot block startup; retry succeeds after release", flush=True)
                repair = subprocess.Popen([str(binary), "repair", str(lease_directory)], stdout=subprocess.PIPE, text=True)
                owners.append(repair)
                assert repair.stdout.readline().strip() == "owner-ready"
                probe(True)
                repair.kill()
                repair.wait(timeout=10)
                probe(False)
                print("PASS: owner renewal repairs removed lease path and fresh service observes ownership", flush=True)
                if os.geteuid() != 0:
                    other = directory / (prefix + str(os.geteuid() + 1))
                    other.mkdir(mode=0o700)
                    probe(False)
                    other.rmdir()
                    print("PASS: portable service ignores other UIDs' protected inactive endpoints", flush=True)
        finally:
            for owner in owners:
                if owner.poll() is None:
                    owner.kill()
                    owner.wait(timeout=10)
    if os.name == "nt":
        pwsh = shutil.which("pwsh")
        assert pwsh, "Actual Windows standard-account lease controls require PowerShell7"
        subprocess.run([pwsh, "-NoProfile", "-File", str(ROOT / "scripts/test-support-lease-standard-windows.ps1")], check=True, timeout=180)
    print("Result: native cross-process attended lease lifecycle passed", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-standard-windows-fixture", type=Path)
    parser.add_argument("--event")
    args = parser.parse_args()
    if args.export_standard_windows_fixture is not None:
        export_standard_windows_fixture(args.export_standard_windows_fixture, args.event or "")
    else:
        assert args.event is None, "An isolated event requires explicit fixture export mode"
        main()
