#!/usr/bin/env python3
"""Compile the exact Windows lease FFI against allocator last-error clobber.

The original functions are preserved from source874b516. Windows executes the
real native event/mutex/error APIs; other hosts only qualify Rust drop ordering
with explicitly simulated native results. Neither mode starts the support app.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from rust_toolchain import rustc_command

ROOT = Path(__file__).resolve().parents[1]
LEGACY_SHA = "44538df170f74fff41fd904bdb5615f5f411561c6f493478474668f56704d67a"


def function(text, marker):
    start = text.index("    fn ", text.index(marker))
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


SCAFFOLD = r'''#![allow(non_snake_case)]
use std::alloc::{GlobalAlloc,Layout,System};
use std::sync::atomic::{AtomicBool,AtomicU32,Ordering};
static ARMED:AtomicBool=AtomicBool::new(false);
static DROPS:AtomicU32=AtomicU32::new(0);
struct ErrorClobberAllocator;
unsafe impl GlobalAlloc for ErrorClobberAllocator {
 unsafe fn alloc(&self,layout:Layout)->*mut u8 { System.alloc(layout) }
 unsafe fn dealloc(&self,pointer:*mut u8,layout:Layout){
  System.dealloc(pointer,layout);
  if ARMED.load(Ordering::SeqCst){DROPS.fetch_add(1,Ordering::SeqCst);native::SetLastError(5);}
 }
}
#[global_allocator]static ALLOCATOR:ErrorClobberAllocator=ErrorClobberAllocator;
#[repr(C)]struct SecurityAttributes{length:u32,descriptor:*mut std::ffi::c_void,inherit:i32}
struct Lease(usize);
impl Drop for Lease { fn drop(&mut self){unsafe{CloseHandle(self.0 as _)};} }
fn wide(value:&str)->Vec<u16>{value.encode_utf16().chain(Some(0)).collect()}
const EVENT:&str="EVENT_NAME";
#[cfg(windows)]mod native{
 use super::SecurityAttributes;
 use std::ffi::c_void;
 #[link(name="kernel32")]extern "system"{
  pub fn OpenEventW(access:u32,inherit:i32,name:*const u16)->*mut c_void;
  pub fn CreateEventExW(attributes:*const SecurityAttributes,name:*const u16,flags:u32,access:u32)->*mut c_void;
  pub fn CreateMutexW(attributes:*const c_void,owner:i32,name:*const u16)->*mut c_void;
  pub fn CloseHandle(handle:*mut c_void)->i32;
  pub fn GetLastError()->u32;
  pub fn SetLastError(error:u32);
  pub fn LocalFree(pointer:*mut c_void)->*mut c_void;
 }
 #[link(name="advapi32")]extern "system"{
  pub fn ConvertStringSecurityDescriptorToSecurityDescriptorW(value:*const u16,revision:u32,descriptor:*mut *mut c_void,size:*mut u32)->i32;
 }
}
#[cfg(not(windows))]mod native{
 use super::{AtomicU32,Ordering,SecurityAttributes};
 use std::ffi::c_void;
 static KIND:AtomicU32=AtomicU32::new(0);
 static ERROR:AtomicU32=AtomicU32::new(0);
 pub unsafe fn OpenEventW(_access:u32,_inherit:i32,_name:*const u16)->*mut c_void{
  match KIND.load(Ordering::SeqCst){1=>3usize as _,0=>{SetLastError(2);std::ptr::null_mut()},_=>{SetLastError(6);std::ptr::null_mut()}}
 }
 pub unsafe fn CreateEventExW(_attributes:*const SecurityAttributes,_name:*const u16,_flags:u32,_access:u32)->*mut c_void{
  if KIND.load(Ordering::SeqCst)!=0{SetLastError(6);std::ptr::null_mut()}else{KIND.store(1,Ordering::SeqCst);1usize as _}
 }
 pub unsafe fn CreateMutexW(_attributes:*const c_void,_owner:i32,_name:*const u16)->*mut c_void{KIND.store(2,Ordering::SeqCst);2usize as _}
 pub unsafe fn CloseHandle(handle:*mut c_void)->i32{if handle as usize!=3{KIND.store(0,Ordering::SeqCst);}1}
 pub unsafe fn GetLastError()->u32{ERROR.load(Ordering::SeqCst)}
 pub unsafe fn SetLastError(value:u32){ERROR.store(value,Ordering::SeqCst);}
 pub unsafe fn LocalFree(_pointer:*mut c_void)->*mut c_void{SetLastError(87);std::ptr::null_mut()}
 pub unsafe fn ConvertStringSecurityDescriptorToSecurityDescriptorW(_value:*const u16,_revision:u32,descriptor:*mut *mut c_void,_size:*mut u32)->i32{*descriptor=4usize as _;1}
}
use native::*;
FUNCTIONS
fn assert_missing_control(){
 let result=probe();
 let error=result.as_ref().err().and_then(|error|error.raw_os_error());
 println!("CONTROL: VARIANT default allocator missing probe: success_false={}, native_error={:?}",matches!(result,Ok(false)),error);
 MISSING_ASSERT
}
fn main(){
 // Keep this backing allocation alive across each immediate native capture.
 let name=wide(EVENT);
 let absent=unsafe{OpenEventW(0x100000,0,name.as_ptr())};
 let missing_error=unsafe{GetLastError()};
 assert!(absent.is_null());assert_eq!(missing_error,2);
 assert_missing_control();
 ARMED.store(true,Ordering::SeqCst);
 let clobbered_probe=probe();
 ARMED.store(false,Ordering::SeqCst);
 assert!(DROPS.load(Ordering::SeqCst)>0);
 PROBE_ASSERT
 let event=create().unwrap();assert!(matches!(probe(),Ok(true)));drop(event);
 let absent=unsafe{OpenEventW(0x100000,0,name.as_ptr())};
 let missing_error=unsafe{GetLastError()};
 assert!(absent.is_null());assert_eq!(missing_error,2,"final owned handle must release event");
 assert_missing_control();
 // A native mutex with the exact event name produces ERROR_INVALID_HANDLE.
 let mutex=unsafe{CreateMutexW(std::ptr::null(),0,name.as_ptr())};assert!(!mutex.is_null());
 assert!(probe().is_err(),"non-missing native error must remain fail-closed");
 ARMED.store(true,Ordering::SeqCst);
 let failure=create();
 ARMED.store(false,Ordering::SeqCst);
 let create_error=match failure{Err(error)=>error,Ok(_)=>panic!("event/mutex collision unexpectedly succeeded")};
 CREATE_ASSERT
 unsafe{assert_ne!(CloseHandle(mutex),0);}
 assert_missing_control();
 println!("PASS: VARIANT native missing/live/type-collision controls and allocator-clobber capture");
}
'''


def main():
    legacy = (ROOT / "scripts/fixtures/windows-lease-last-error-original.rs").read_text(encoding="utf-8")
    assert hashlib.sha256(legacy.encode()).hexdigest() == LEGACY_SHA, "Original874b516 negative fixture changed"
    source = (ROOT / "scripts/support-invite-guard.rs").read_text(encoding="utf-8")
    current = "\n\n".join(function(source, "    #[cfg(windows)]\n    fn " + name + "()")
                           for name in ("create", "probe")) + "\n"
    assert "wide(EVENT).as_ptr()" not in current
    event = "Global\\Mixel-Lease-Error-Test-" + uuid.uuid4().hex
    scope = "actual Win32" if os.name == "nt" else "simulated Win32; actual Rust drop ordering"
    with tempfile.TemporaryDirectory(prefix="mixel-lease-error-") as temporary:
        for variant, functions in (("original", legacy), ("corrected", current)):
            probe_assert = ("assert!(clobbered_probe.is_err());#[cfg(windows)]assert_eq!(clobbered_probe.unwrap_err().raw_os_error(),Some(5));" if variant == "original" else
                            "assert!(matches!(clobbered_probe,Ok(false)));" )
            missing_assert = ("assert!(!matches!(result,Ok(true)));" if variant == "original" else
                              "assert!(matches!(result,Ok(false)),\"corrected default allocator missing probe must be false\");")
            create_assert = ("#[cfg(windows)]assert_eq!(create_error.raw_os_error(),Some(5));" if variant == "original" else
                             "assert_eq!(create_error.raw_os_error(),Some(6));")
            # The simulated original io::last_os_error reads the host OS error,
            # not our Win32 API emulator; only Windows attributes its exact5.
            if variant == "original":
                create_assert += "#[cfg(not(windows))]assert!(create_error.raw_os_error().is_some());"
            text = SCAFFOLD.replace("EVENT_NAME", event.replace("\\", "\\\\"))
            text = text.replace("FUNCTIONS", functions).replace("PROBE_ASSERT", probe_assert)
            text = text.replace("MISSING_ASSERT", missing_assert)
            text = text.replace("CREATE_ASSERT", create_assert).replace("VARIANT", variant)
            path = Path(temporary) / (variant + ".rs")
            path.write_text(text, encoding="utf-8", newline="\n")
            binary = path.with_suffix(".exe" if os.name == "nt" else ".bin")
            subprocess.run(rustc_command(["rustc", "--edition=2021", "--deny=warnings", str(path), "-o", str(binary)]), check=True)
            subprocess.run([str(binary)], check=True, timeout=15)
    print("PASS: exact original-negative/corrected-positive Windows lease error ordering; " + scope)
    print("Scope: injected allocator clobber regression; default signed-app runtime cause remains separately tested")


if __name__ == "__main__":
    main()
