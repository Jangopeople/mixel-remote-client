//! Bounded owned kernel-test child only; no application, service or SAS calls.
#[cfg(not(windows))]
compile_error!("Windows-only component actor");
use std::{ffi::c_void,ptr,time::Duration};
type Handle=*mut c_void;
#[link(name="kernel32")]
extern "system" {
    fn CreateFileW(name:*const u16,access:u32,share:u32,security:Handle,disposition:u32,flags:u32,template:Handle)->Handle;
    fn CloseHandle(handle:Handle)->i32;
    fn OpenEventW(access:u32,inherit:i32,name:*const u16)->Handle;
    fn WaitForSingleObject(handle:Handle,millis:u32)->u32;
}
fn main() {
    let mut args=std::env::args().skip(1);
    match args.next().as_deref() {
        Some("exit-event")=> {
            let name=args.next().expect("owned child release event");assert!(args.next().is_none());
            assert!(name.starts_with("Local\\Mixel-SAS-Component-Exit-"));
            let wide:Vec<u16>=name.encode_utf16().chain(Some(0)).collect();
            let handle=unsafe { OpenEventW(0x00100000,0,wide.as_ptr()) };
            assert!(!handle.is_null() && handle as isize != -1);
            let result=unsafe { WaitForSingleObject(handle,5000) };
            assert_ne!(unsafe { CloseHandle(handle) },0);assert_eq!(result,0,"own parent release deadline");
        },
        Some("pipe")=> {
            let name=args.next().expect("owned component pipe name");assert!(args.next().is_none());
            assert!(name.starts_with("\\\\.\\pipe\\Mixel-SAS-Component-"));
            let wide:Vec<u16>=name.encode_utf16().chain(Some(0)).collect();
            let handle=unsafe { CreateFileW(wide.as_ptr(),0xc0000000,0,ptr::null_mut(),3,0,ptr::null_mut()) };
            assert!(!handle.is_null() && handle as isize != -1,"connect only named own component pipe");
            std::thread::sleep(Duration::from_millis(1000));
            assert_ne!(unsafe { CloseHandle(handle) },0);
        },
        _=>panic!("unsupported component actor route"),
    }
}
