#!/usr/bin/env python3
"""Run the actual generated kernel lease across independent native processes.

The fixed endpoint's name/root are isolated in the compiled fixture. Owner and
service probe code comes byte-for-byte from the shipped guard template; a test
bridge exposes creation errors. Windows uses an allocator that clears last-error
on cleanup to exercise the API's required immediate error capture.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from rust_toolchain import rustc_command

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
            println!("owner-ready");
            use std::io::Write;
            std::io::stdout().flush().unwrap();
            std::thread::sleep(std::time::Duration::from_secs(120));
        }
        Some("probe") => println!("{}", support_invite_requires_click()),
        Some("attestation") => println!("{}", support_invite_attestation()),
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
                assert!(support_invite_requires_click());
                assert_eq!(support_invite_attestation(), "guard-unavailable");
                std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
                assert!(hold_support_invite_attended_lease());
                assert!(!support_invite_owner_lease_failed());
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
                assert!(support_invite_requires_click());
                assert_eq!(support_invite_attestation(), "guard-unavailable");
                unsafe { CloseHandle(mutex); }
                assert!(hold_support_invite_attended_lease());
                assert!(!support_invite_owner_lease_failed());
            }
            println!("owner-retry-clears-failure");
        }
        _ => panic!("unknown isolated lease test command"),
    }
}
'''


def main():
    original = (ROOT / "scripts/support-invite-guard.rs").read_text(encoding="utf-8")
    owners = []
    with tempfile.TemporaryDirectory(prefix="mixel-kernel-lease-test-") as temporary:
        directory = Path(temporary)
        prefix = "mixel-lease-test-" + uuid.uuid4().hex + "-"
        event = "Global\\Mixel-Remote-Test-Lease-" + uuid.uuid4().hex
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
        source = source.replace(bridge_marker, WINDOWS_BRIDGE + bridge_marker, 1)
        fixture = directory / "lease.rs"
        fixture.write_text(source + HARNESS, encoding="utf-8")
        binary = directory / ("lease.exe" if os.name == "nt" else "lease")
        rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
        assert rustc, "Native lease tests require rustc"
        subprocess.run(rustc_command([rustc, "--edition", "2021", "-D", "warnings", str(fixture), "-o", str(binary)]), check=True)

        def command(*arguments):
            return subprocess.check_output([str(binary), *arguments], text=True, timeout=10).strip()

        def probe(expected, attestation=None):
            assert command("probe") == str(expected).lower(), "Fresh service process returned wrong attended requirement"
            if attestation is None:
                attestation = "attended-runtime-v2" if expected else ""
            assert command("attestation") == attestation, "Fresh probe must not attest readiness from unknown ownership"

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
    print("Result: native cross-process attended lease lifecycle passed", flush=True)


if __name__ == "__main__":
    main()
