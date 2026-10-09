#!/usr/bin/env python3
"""Run the actual generated kernel lease across independent native processes.

Only the fixed endpoint's name/root are isolated in the compiled fixture. Owner
and service probe code comes byte-for-byte from the shipped guard template.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
fn main() {
    match std::env::args().nth(1).as_deref() {
        Some("owner") => {
            assert!(hold_support_invite_attended_lease());
            assert!(hold_support_invite_attended_lease());
            assert!(!support_invite_owner_lease_failed());
            println!("owner-ready");
            use std::io::Write;
            std::io::stdout().flush().unwrap();
            std::thread::sleep(std::time::Duration::from_secs(120));
        }
        Some("probe") => println!("{}", support_invite_requires_click()),
        Some("renew") => {
            renew_support_invite_attended();
            assert!(support_invite_requires_click());
            assert!(!support_invite_owner_lease_failed());
            println!("memory-only");
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
        fixture = directory / "lease.rs"
        fixture.write_text(source + HARNESS, encoding="utf-8")
        binary = directory / ("lease.exe" if os.name == "nt" else "lease")
        subprocess.run(["rustc", "--edition", "2021", "-D", "warnings", str(fixture), "-o", str(binary)], check=True)

        def command(*arguments):
            return subprocess.check_output([str(binary), *arguments], text=True, timeout=10).strip()

        def probe(expected):
            assert command("probe") == str(expected).lower(), "Fresh service process returned wrong attended requirement"

        try:
            probe(False)
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
                probe(True)
                assert not (target / "lease").exists()
                lease_directory.unlink()
                lease_directory.mkdir(mode=0o700)
                (lease_directory / "lease").symlink_to(fixture)
                probe(True)
                (lease_directory / "lease").unlink()
                (lease_directory / "lease").write_text("", encoding="utf-8")
                (lease_directory / "lease").chmod(0o600)
                hard_link = directory / "hard-linked-lease"
                hard_link.hardlink_to(lease_directory / "lease")
                probe(True)
                hard_link.unlink()
                (lease_directory / "lease").unlink()
                lease_directory.rmdir()
                print("PASS: owner0700/file0600; symlink and hard-link endpoints fail closed without traversal", flush=True)
                retry_argument = str(lease_directory)
            else:
                retry_argument = event
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
