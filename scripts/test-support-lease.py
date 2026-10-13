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
import struct
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

# Used only by the exported standard-account source fixture. Observe the actual
# child's calling thread immediately before its unchanged event API call. These
# TOKEN_QUERY handles never impersonate, revert, assign or modify any token.
STANDARD_THREAD_BRIDGE = r'''
    #[cfg(windows)]
    mod fixture_thread_tokens {
        use std::ffi::c_void;
        type Handle = *mut c_void;
        #[repr(C)]
        #[derive(Clone, Copy, Default, PartialEq, Eq)]
        struct Luid { low: u32, high: i32 }
        #[repr(C)]
        #[derive(Default)]
        struct Statistics {
            token_id: Luid, authentication_id: Luid, expires: i64,
            token_type: u32, impersonation_level: u32, dynamic_charged: u32,
            dynamic_available: u32, groups: u32, privileges: u32, modified_id: Luid,
        }
        #[link(name="kernel32")]
        extern "system" {
            fn GetCurrentProcess() -> Handle;
            fn GetCurrentThread() -> Handle;
            fn GetCurrentThreadId() -> u32;
            fn GetLastError() -> u32;
            fn CloseHandle(handle: Handle) -> i32;
        }
        #[link(name="advapi32")]
        extern "system" {
            fn OpenProcessToken(process: Handle, access: u32, token: *mut Handle) -> i32;
            fn OpenThreadToken(thread: Handle, access: u32, open_as_self: i32, token: *mut Handle) -> i32;
            fn GetTokenInformation(token: Handle, kind: u32, data: *mut c_void, size: u32, returned: *mut u32) -> i32;
        }
        struct Facts { status: &'static str, error: u32, statistics: Statistics }
        impl Facts {
            fn empty(status: &'static str, error: u32) -> Self {
                Self { status, error, statistics: Statistics::default() }
            }
        }
        fn statistics(token: Handle) -> Facts {
            let mut facts=Facts::empty("available",0);
            let mut returned=0;
            let size=std::mem::size_of::<Statistics>() as u32;
            assert_eq!(size,56,"Exact Win32 TOKEN_STATISTICS layout changed");
            let success=unsafe { GetTokenInformation(token,10,(&mut facts.statistics as *mut Statistics).cast(),size,&mut returned) };
            let error=if success==0 { unsafe { GetLastError() } } else { 0 };
            if success==0 { return Facts::empty("statistics-error",error); }
            if returned!=size { return Facts::empty("statistics-size",0); }
            facts
        }
        fn close(token: Handle) -> (&'static str,u32) {
            if token.is_null() { return ("not-opened",0); }
            let success=unsafe { CloseHandle(token) };
            let error=if success==0 { unsafe { GetLastError() } } else { 0 };
            (if success==0 { "failed" } else { "closed" },error)
        }
        static RECORDS: std::sync::Mutex<Vec<String>>=std::sync::Mutex::new(Vec::new());
        static ENABLED: std::sync::atomic::AtomicBool=std::sync::atomic::AtomicBool::new(false);
        pub(super) fn enable(directory: &std::path::Path) {
            let request=directory.join("fixture-thread-token.request");
            let enabled=request.is_file();
            if enabled { assert_eq!(std::fs::read_to_string(request).unwrap(),"observe","Invalid owned calling-thread observation request"); }
            ENABLED.store(enabled,std::sync::atomic::Ordering::SeqCst);
        }
        pub(super) fn observe(phase: &'static str) {
            if !ENABLED.load(std::sync::atomic::Ordering::SeqCst) { return; }
            assert!(matches!(phase,"CreateEventExW" | "CreateEventExW-unnamed" | "OpenEventW"));
            let tid=unsafe { GetCurrentThreadId() };
            assert_ne!(tid,0);
            let mut thread=std::ptr::null_mut();
            // OpenAsSelf chooses the access check for this read-only token
            // query. It does not change the thread's effective security token.
            let thread_open=unsafe { OpenThreadToken(GetCurrentThread(),8,1,&mut thread) };
            let thread_error=if thread_open==0 { unsafe { GetLastError() } } else { 0 };
            let thread_facts=if thread_open!=0 { statistics(thread) }
                else { Facts::empty(if thread_error==1008 { "absent" } else { "open-error" },thread_error) };
            let mut primary=std::ptr::null_mut();
            let primary_open=unsafe { OpenProcessToken(GetCurrentProcess(),8,&mut primary) };
            let primary_error=if primary_open==0 { unsafe { GetLastError() } } else { 0 };
            let primary_facts=if primary_open!=0 { statistics(primary) } else { Facts::empty("open-error",primary_error) };
            let same_logon=if thread_facts.status=="available" && primary_facts.status=="available" {
                if thread_facts.statistics.authentication_id==primary_facts.statistics.authentication_id { "true" } else { "false" }
            } else { "null" };
            let (thread_close,thread_close_error)=close(if thread_open!=0 { thread } else { std::ptr::null_mut() });
            let (primary_close,primary_close_error)=close(if primary_open!=0 { primary } else { std::ptr::null_mut() });
            // No SID, token ID, authentication LUID, credentials or arguments
            // leave this helper. Equality alone compares existing identities.
            let record=format!(concat!(
                "{{\"phase\":\"{}\",\"tid\":{},\"threadStatus\":\"{}\",\"threadError\":{},",
                "\"threadType\":{},\"threadImpersonationLevel\":{},\"primaryStatus\":\"{}\",\"primaryError\":{},",
                "\"primaryType\":{},\"threadLogonMatchesPrimary\":{},\"threadClose\":\"{}\",\"threadCloseError\":{},",
                "\"primaryClose\":\"{}\",\"primaryCloseError\":{}}}"),
                phase,tid,thread_facts.status,thread_facts.error,thread_facts.statistics.token_type,
                thread_facts.statistics.impersonation_level,primary_facts.status,primary_facts.error,
                primary_facts.statistics.token_type,same_logon,thread_close,thread_close_error,primary_close,primary_close_error);
            let mut records=RECORDS.lock().unwrap();
            assert!(records.len()<32,"Read-only thread-token observation exceeds its call bound");
            records.push(record);
        }
        pub(super) fn flush(directory: &std::path::Path, unnamed: bool) {
            if !ENABLED.load(std::sync::atomic::Ordering::SeqCst) { return; }
            let records=RECORDS.lock().unwrap();
            assert!(!records.is_empty(),"No calling-thread event observation recorded");
            let unnamed_calls=records.iter().filter(|record| record.contains("\"phase\":\"CreateEventExW-unnamed\"")).count();
            assert_eq!(unnamed_calls,if unnamed { 1 } else { 0 },"Calling-thread publication stage does not match its event calls");
            let result=format!("{{\"schema\":1,\"pid\":{},\"records\":[{}]}}\n",std::process::id(),records.join(","));
            assert!(result.len()<=32768,"Read-only token result exceeds its size bound");
            let filename=if unnamed { "fixture-thread-token-unnamed.json" } else { "fixture-thread-token-initial.json" };
            // Each phase is written once and closed before its separate result
            // readiness file is published. The parent cannot read partial
            // bytes or hold a delete-denying reader across a later replacement.
            // create_new also refuses accidental repeat publication.
            let mut file=std::fs::OpenOptions::new().write(true).create_new(true).open(directory.join(filename)).unwrap();
            use std::io::Write;
            file.write_all(result.as_bytes()).unwrap();
        }
    }
    #[cfg(windows)]
    pub(super) fn fixture_enable_thread_tokens(directory: &std::path::Path) {
        fixture_thread_tokens::enable(directory);
    }
    #[cfg(windows)]
    pub(super) fn fixture_flush_thread_tokens(directory: &std::path::Path, unnamed: bool) {
        fixture_thread_tokens::flush(directory,unnamed);
    }
'''
# Exported source-fixture diagnostic only. NULL selects this calling child's
# immediate job, even when jobs are nested (documented QueryInformationJobObject
# contract). Raw DWORD flags do not establish an event/namespace failure cause.
STANDARD_JOB_BRIDGE = r'''
    #[cfg(windows)]
    mod fixture_job_limits {
        use std::ffi::c_void;
        type Handle = *mut c_void;
        #[repr(C)]
        struct BasicLimit {
            process_time: i64, job_time: i64, flags: u32,
            minimum_working_set: usize, maximum_working_set: usize,
            active_process_limit: u32, affinity: usize,
            priority_class: u32, scheduling_class: u32,
        }
        #[repr(C)]
        struct ExtendedLimit {
            basic: BasicLimit, io: [u64; 6], process_memory: usize,
            job_memory: usize, peak_process_memory: usize, peak_job_memory: usize,
        }
        #[link(name="kernel32")]
        extern "system" {
            fn GetCurrentProcess() -> Handle;
            fn GetLastError() -> u32;
            fn IsProcessInJob(process: Handle, job: Handle, present: *mut i32) -> i32;
            fn QueryInformationJobObject(job: Handle, kind: i32, data: *mut c_void,
                size: u32, returned: *mut u32) -> i32;
        }
        fn query(kind: i32, data: *mut c_void, size: u32) -> (&'static str, u32, u32) {
            let mut returned=0;
            let success=unsafe { QueryInformationJobObject(std::ptr::null_mut(),kind,data,size,&mut returned) };
            let error=if success==0 { unsafe { GetLastError() } } else { 0 };
            (if success==0 { "error" } else if returned!=size { "size" } else { "available" },error,returned)
        }
        pub(super) fn observe(directory: &std::path::Path) {
            let request=directory.join("fixture-job-limits.request");
            if !request.is_file() { return; }
            assert_eq!(std::fs::read_to_string(request).unwrap(),"observe","Invalid owned job observation request");
            // All bytes, including ABI padding, start at zero. This exported
            // fixture is x64 only; no guessed x86 layout reaches a Win32 call.
            assert_eq!(std::mem::size_of::<usize>(),8,"Job diagnostic requires exact x64 ABI");
            assert_eq!(std::mem::size_of::<BasicLimit>(),64);
            assert_eq!(std::mem::size_of::<ExtendedLimit>(),144);
            let mut extended: ExtendedLimit=unsafe { std::mem::zeroed() };
            unsafe { std::ptr::write_bytes(&mut extended as *mut ExtendedLimit,0,1); }
            assert_eq!(std::ptr::addr_of!(extended.basic.flags) as usize - std::ptr::addr_of!(extended) as usize,16);
            let mut ui=0u32;
            let mut present=0;
            let member_success=unsafe { IsProcessInJob(GetCurrentProcess(),std::ptr::null_mut(),&mut present) };
            let member_error=if member_success==0 { unsafe { GetLastError() } } else { 0 };
            let in_job=member_success!=0 && present!=0;
            let unused=if member_success==0 { "membership-error" } else { "not-in-job" };
            let ui_result=if in_job { query(4,(&mut ui as *mut u32).cast(),4) } else { (unused,0,0) };
            // Attempt both independent read-only classes even if UI fails.
            let extended_result=if in_job { query(9,(&mut extended as *mut ExtendedLimit).cast(),144) } else { (unused,0,0) };
            let ui_flags=if ui_result.0=="available" { ui.to_string() } else { "null".to_owned() };
            let extended_flags=if extended_result.0=="available" { extended.basic.flags.to_string() } else { "null".to_owned() };
            let result=format!(concat!(
                "{{\"schema\":1,\"pid\":{},\"inAnyJob\":{},\"membershipStatus\":\"{}\",\"membershipError\":{},",
                "\"uiStatus\":\"{}\",\"uiError\":{},\"uiReturned\":{},\"uiFlags\":{},",
                "\"extendedStatus\":\"{}\",\"extendedError\":{},\"extendedReturned\":{},\"extendedFlags\":{}}}\n"),
                std::process::id(),if member_success==0 { "null" } else if in_job { "true" } else { "false" },
                if member_success==0 { "error" } else { "available" },member_error,
                ui_result.0,ui_result.1,ui_result.2,ui_flags,
                extended_result.0,extended_result.1,extended_result.2,extended_flags);
            assert!(result.len()<=4096,"Read-only job result exceeds its bound");
            use std::io::Write;
            let mut file=std::fs::OpenOptions::new().write(true).create_new(true)
                .open(directory.join("fixture-job-limits.json")).unwrap();
            file.write_all(result.as_bytes()).unwrap();
        }
    }
    #[cfg(windows)]
    pub(super) fn fixture_observe_job_limits(directory: &std::path::Path) {
        fixture_job_limits::observe(directory);
    }
'''
# Opt-in exported source fixture only: existing explicit object directories,
# independent QUERY/TRAVERSE rights. These facts do not identify the directory
# selected internally by the unchanged Win32 event API.
STANDARD_DIRECTORY_BRIDGE = r'''
    #[cfg(windows)]
    mod fixture_directory_access {
        use std::ffi::c_void;
        type Handle = *mut c_void;
        #[repr(C)]
        struct UnicodeString { length: u16, maximum_length: u16, buffer: *mut u16 }
        #[repr(C)]
        struct ObjectAttributes {
            length: u32, root: Handle, name: *mut UnicodeString, attributes: u32,
            security: *mut c_void, quality: *mut c_void,
        }
        type OpenDirectory = unsafe extern "system" fn(*mut Handle,u32,*mut ObjectAttributes)->i32;
        #[link(name="kernel32")]
        extern "system" {
            fn GetCurrentProcessId() -> u32;
            fn ProcessIdToSessionId(pid: u32, session: *mut u32) -> i32;
            fn LoadLibraryExW(name: *const u16, file: Handle, flags: u32) -> Handle;
            fn GetProcAddress(module: Handle, name: *const u8) -> *mut c_void;
            fn FreeLibrary(module: Handle) -> i32;
            fn CloseHandle(handle: Handle) -> i32;
            fn GetLastError() -> u32;
        }
        fn layout_valid() -> bool {
            let mut name: UnicodeString=unsafe { std::mem::zeroed() };
            let object: ObjectAttributes=unsafe { std::mem::zeroed() };
            let n=std::ptr::addr_of_mut!(name) as usize;
            let o=std::ptr::addr_of!(object) as usize;
            std::mem::size_of::<usize>()==8 && std::mem::size_of::<UnicodeString>()==16 &&
            std::mem::size_of::<ObjectAttributes>()==48 &&
            std::ptr::addr_of!(name.length) as usize-n==0 &&
            std::ptr::addr_of!(name.maximum_length) as usize-n==2 &&
            std::ptr::addr_of!(name.buffer) as usize-n==8 &&
            std::ptr::addr_of!(object.length) as usize-o==0 &&
            std::ptr::addr_of!(object.root) as usize-o==8 &&
            std::ptr::addr_of!(object.name) as usize-o==16 &&
            std::ptr::addr_of!(object.attributes) as usize-o==24 &&
            std::ptr::addr_of!(object.security) as usize-o==32 &&
            std::ptr::addr_of!(object.quality) as usize-o==40
        }
        fn attempt(open: OpenDirectory, directory: &str, path: &str, access: u32) -> String {
            assert!(matches!(directory,"global" | "session") && matches!(access,1 | 2 | 4));
            // Both names are derived solely from constants and this child's
            // actual DWORD session. Absolute names require RootDirectory=NULL.
            let mut wide: Vec<u16>=path.encode_utf16().chain(Some(0)).collect();
            assert!(wide.len()<128 && wide.len()>1);
            let mut name: UnicodeString=unsafe { std::mem::zeroed() };
            let mut object: ObjectAttributes=unsafe { std::mem::zeroed() };
            unsafe {
                std::ptr::write_bytes(&mut name as *mut UnicodeString,0,1);
                std::ptr::write_bytes(&mut object as *mut ObjectAttributes,0,1);
            }
            name.length=((wide.len()-1)*2) as u16;
            name.maximum_length=(wide.len()*2) as u16;
            name.buffer=wide.as_mut_ptr();
            object.length=48;object.name=&mut name;object.attributes=0x40;
            let mut handle=std::ptr::null_mut();
            let nt_status=unsafe { open(&mut handle,access,&mut object) };
            // NtOpenDirectoryObject documents STATUS_SUCCESS (zero) or an
            // error NTSTATUS. Never substitute last-error or guess ownership
            // from an output written on an unsuccessful/unknown status.
            let returned=!handle.is_null();
            let (status,close_status,close_error)=if nt_status==0 && returned {
                let success=unsafe { CloseHandle(handle) };
                let error=if success==0 { unsafe { GetLastError() } } else { 0 };
                ("opened",if success==0 { "failed" } else { "closed" },error)
            } else {
                (if nt_status>=0 || returned { "contract-error" } else { "nt-result" },"not-opened",0)
            };
            format!(concat!(
                "{{\"directory\":\"{}\",\"path\":\"{}\",\"access\":{},\"ntStatus\":{},",
                "\"status\":\"{}\",\"handleReturned\":{},\"closeStatus\":\"{}\",\"closeError\":{}}}"),
                directory,path.replace('\\',"\\\\"),access,nt_status,status,returned,close_status,close_error)
        }
        pub(super) fn observe(directory: &std::path::Path) {
            let request=directory.join("fixture-directory-access.request");
            if !request.is_file() { return; }
            assert_eq!(std::fs::read_to_string(request).unwrap(),"observe","Invalid owned explicit-directory observation request");
            let mut records=Vec::new();
            let mut session=0u32;
            let mut session_status="unqueried";let mut session_error=0;
            let mut loader_status="layout-error";let mut loader_error=0;
            let mut unload_status="not-loaded";let mut unload_error=0;
            if layout_valid() {
                let session_success=unsafe { ProcessIdToSessionId(GetCurrentProcessId(),&mut session) };
                session_error=if session_success==0 { unsafe { GetLastError() } } else { 0 };
                session_status=if session_success==0 { "error" } else { "available" };
                let dll: Vec<u16>="ntdll.dll".encode_utf16().chain(Some(0)).collect();
                // The returned LoadLibraryEx reference is owned. System32 is
                // the sole search directory, with no legacy-path fallback.
                let module=unsafe { LoadLibraryExW(dll.as_ptr(),std::ptr::null_mut(),0x800) };
                loader_error=if module.is_null() { unsafe { GetLastError() } } else { 0 };
                if module.is_null() { loader_status="load-error"; }
                else {
                    let export=unsafe { GetProcAddress(module,b"NtOpenDirectoryObject\0".as_ptr()) };
                    loader_error=if export.is_null() { unsafe { GetLastError() } } else { 0 };
                    if export.is_null() { loader_status="export-error"; }
                    else {
                        loader_status="available";
                        let open: OpenDirectory=unsafe { std::mem::transmute(export) };
                        for access in [1,2] {
                            records.push(attempt(open,"global",r"\BaseNamedObjects",access));
                        }
                        if session_status=="available" {
                            let path=format!(r"\Sessions\{}\BaseNamedObjects",session);
                            for access in [1,2] { records.push(attempt(open,"session",&path,access)); }
                        }
                        // Request name-creation access separately on these
                        // existing directories. This only opens handles; no
                        // directory or event object is created or modified.
                        records.push(attempt(open,"global",r"\BaseNamedObjects",4));
                        if session_status=="available" {
                            let path=format!(r"\Sessions\{}\BaseNamedObjects",session);
                            records.push(attempt(open,"session",&path,4));
                        }
                    }
                    // Keep the owned module alive until every export call and
                    // owned directory close has finished. Never CloseHandle it.
                    let success=unsafe { FreeLibrary(module) };
                    unload_error=if success==0 { unsafe { GetLastError() } } else { 0 };
                    unload_status=if success==0 { "failed" } else { "closed" };
                }
            }
            let result=format!(concat!(
                "{{\"schema\":1,\"pid\":{},\"scope\":\"ExplicitDirectoryAccessOnly\",\"route\":\"ActualEventRouteUnmeasured\",",
                "\"nameCreationAccessMeasured\":{},\"objectsCreated\":false,",
                "\"sessionStatus\":\"{}\",\"sessionError\":{},\"sessionId\":{},\"loaderStatus\":\"{}\",\"loaderError\":{},",
                "\"unloadStatus\":\"{}\",\"unloadError\":{},\"records\":[{}]}}\n"),
                std::process::id(),loader_status=="available",session_status,session_error,
                if session_status=="available" { session.to_string() } else { "null".to_owned() },
                loader_status,loader_error,unload_status,unload_error,records.join(","));
            assert!(result.len()<=4096 && records.len()<=6,"Explicit-directory result exceeds its bound");
            use std::io::Write;
            let mut file=std::fs::OpenOptions::new().write(true).create_new(true)
                .open(directory.join("fixture-directory-access.json")).unwrap();
            file.write_all(result.as_bytes()).unwrap();
        }
    }
    #[cfg(windows)]
    pub(super) fn fixture_observe_directory_access(directory: &std::path::Path) {
        fixture_directory_access::observe(directory);
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
        mixel_support_lease::fixture_observe_directory_access(&directory);
        mixel_support_lease::fixture_observe_job_limits(&directory);
        mixel_support_lease::fixture_enable_thread_tokens(&directory);
        let mode = std::fs::read_to_string(directory.join("fixture-mode.txt")).unwrap();
        match mode.as_str() {
            "probe" => {}
            // The parent strictly validates success; retain real create-error
            // classification when an experimental standard-token owner fails.
            "owner" => { let _ = hold_support_invite_attended_lease(); }
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
        mixel_support_lease::fixture_flush_thread_tokens(&directory,false);
        std::fs::rename(directory.join("fixture-result.new"), directory.join("fixture-result.json")).unwrap();
        // This test-only request is absent from the strict normal baseline.
        // The result never acquires/renews an attended lease or exports ready.
        let unnamed_request = directory.join("fixture-unnamed.request");
        if unnamed_request.is_file() {
            assert_eq!(mode, "probe", "Unnamed observation must not run as a lease owner");
            assert_eq!(std::fs::read_to_string(&unnamed_request).unwrap(), "observe");
            let (phase, created, create_error, closed, close_error) = mixel_support_lease::fixture_unnamed_create();
            let after_status = support_invite_lease_status();
            let after_error = mixel_support_lease::fixture_raw_open_error();
            let after_proof = support_invite_attestation();
            let after_required = support_invite_requires_click();
            let unnamed_result = format!(
                "{{\"pid\":{},\"phase\":\"{}\",\"created\":{},\"createError\":{},\"closed\":{},\"closeError\":{},\"sourceStatusAfter\":\"{}\",\"rawOpenErrorAfter\":{},\"attestationAfter\":\"{}\",\"requiresClickAfter\":{}}}\n",
                std::process::id(), phase, created, create_error, closed, close_error, after_status, after_error, after_proof, after_required);
            std::fs::write(directory.join("fixture-unnamed-result.new"), unnamed_result).unwrap();
            mixel_support_lease::fixture_flush_thread_tokens(&directory,true);
            std::fs::rename(directory.join("fixture-unnamed-result.new"), directory.join("fixture-unnamed-result.json")).unwrap();
        }
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



def standard_windows_harness(event):
    # The diagnostic is nested in Standard main so replacing the whole main
    # conserves GUI output and does not export unused diagnostic functions.
    child = (ROOT / "scripts/test-support-nt-event-child.rs").read_text(encoding="utf-8")
    marker = '        std::fs::rename(directory.join("fixture-result.new"), directory.join("fixture-result.json")).unwrap();\n'
    assert STANDARD_WINDOWS_HARNESS.count(marker) == 1, "Exact original Standard result publication boundary changed"
    diagnostic = ('        mod fixture_nt_event {\n' + child + '\n        }\n'
                  '        if mode == \"nt-world-probe\" { fixture_nt_event::observe_native(&directory, ' + json.dumps(event) + ').unwrap(); }\n')
    return STANDARD_WINDOWS_HARNESS.replace(marker, marker + diagnostic, 1).replace('"probe" => {}', '"probe" | "nt-world-probe" => {}', 1)


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


def standard_unnamed_bridge(original):
    # Copy the current Windows create implementation; no alternate SDDL,
    # SECURITY_ATTRIBUTES layout, privilege, event access or owner state.
    first = "    #[cfg(windows)]\n    fn create() -> std::io::Result<Lease> {"
    last = "\n    #[cfg(windows)]\n    fn current(_lease: &Lease) -> std::io::Result<bool> {"
    assert original.count(first) == 1 and original.count(last) == 1, "Exact Windows create extraction boundary changed"
    start = original.index(first)
    end = original.index(last, start)
    body = original[start:end]
    replacements = (
        ("fn create() -> std::io::Result<Lease>",
         "pub(super) fn fixture_unnamed_create() -> (&'static str, bool, u32, bool, u32)"),
        ("return Err(std::io::Error::last_os_error());",
         'return ("sddl", false, unsafe { GetLastError() }, false, 0);'),
        ("        let name = wide(EVENT);\n        let handle = unsafe { CreateEventExW(&attributes, name.as_ptr(), 0, 0x00100000) };",
         "        let handle = unsafe { CreateEventExW(&attributes, std::ptr::null(), 0, 0x00100000) };"),
        ("        if let Some(error) = error {\n            return Err(std::io::Error::from_raw_os_error(error as i32));\n        }\n        Ok(Lease(handle as usize))",
         '        if let Some(error) = error {\n            return ("create", false, error, false, 0);\n        }\n'
         '        // Only a non-null handle returned by this exact call is closed.\n'
         '        let closed = unsafe { CloseHandle(handle) } != 0;\n'
         '        let close_error = if closed { 0 } else { unsafe { GetLastError() } };\n'
         '        ("close", true, 0, closed, close_error)'),
    )
    for old, new in replacements:
        assert body.count(old) == 1, "Exact Windows create discriminator boundary changed"
        body = body.replace(old, new, 1)
    return body


def standard_windows_fixture_body(original, directory, prefix, event):
    source = isolated_source(original, directory, prefix, event)
    marker = "\n}\n\npub fn hold_support_invite_attended_lease()"
    assert source.count(marker) == 1, "Standard Windows bridge extraction boundary changed"
    source = source.replace(marker, "\n" + standard_unnamed_bridge(original) + STANDARD_THREAD_BRIDGE + STANDARD_JOB_BRIDGE + STANDARD_DIRECTORY_BRIDGE + marker, 1)
    # Instrument only this test source, before (never between the API call and
    # immediate last-error capture). Product code and event call arguments stay
    # unchanged. The intentionally prior-probe negative stays uninstrumented.
    calls = (
        ('        let handle = unsafe { CreateEventExW(&attributes, name.as_ptr(), 0, 0x00100000) };', "CreateEventExW", 1),
        ('        let handle = unsafe { CreateEventExW(&attributes, std::ptr::null(), 0, 0x00100000) };', "CreateEventExW-unnamed", 1),
        ('        let handle = unsafe { OpenEventW(0x00100000, 0, name.as_ptr()) };', "OpenEventW", 2),
    )
    for call, phase, count in calls:
        assert source.count(call)==count, "Exact standard fixture event observation boundary changed"
        source=source.replace(call, f'        fixture_thread_tokens::observe("{phase}");\n' + call)
    return source + HARNESS.replace("fn main() {", "fn lease_main() {", 1) + standard_windows_harness(event)


def read_pe_subsystem(binary):
    assert 128 <= binary.stat().st_size <= 16 * 1024 * 1024, "Actual Windows PE executable exceeds its size bounds"
    data = binary.read_bytes()
    assert 128 <= len(data) <= 16 * 1024 * 1024 and data[:2] == b"MZ", "Expected bounded actual Windows PE executable"
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    assert 64 <= pe <= len(data) - 96 and data[pe:pe + 4] == b"PE\0\0", "Actual PE header is missing or outside its bounds"
    machine, optional_size = struct.unpack_from("<H", data, pe + 4)[0], struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    assert machine == 0x8664 and optional_size >= 70 and optional + optional_size <= len(data), "Expected bounded x64 PE optional header"
    assert struct.unpack_from("<H", data, optional)[0] == 0x20b, "Expected actual PE32+ executable"
    return struct.unpack_from("<H", data, optional + 68)[0]


def export_standard_windows_fixture(directory, event, subsystem_pair=False):
    assert os.name == "nt", "Standard-account fixture compilation requires actual Windows"
    assert re.fullmatch(r"(?:(?:Global|Local)\\)?Mixel-Remote-Standard-Lease-[0-9a-f]{32}", event), "Expected an isolated GUID event name"
    directory = directory.resolve()
    assert directory.is_dir() and not directory.is_symlink(), "Expected an owned existing fixture directory"
    original = (ROOT / "scripts/support-invite-guard.rs").read_text(encoding="utf-8")
    assert "pub fn support_invite_lease_status() -> String" in original, "Actual read-only lease classification helper is missing"
    body = standard_windows_fixture_body(original, directory, "mixel-standard-lease-" + uuid.uuid4().hex + "-", event)
    fixture = directory / "lease-standard.rs"
    fixture.write_text(body, encoding="utf-8", newline="\n")
    binary = directory / "lease-standard.exe"
    rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
    assert rustc, "Native standard-account lease control requires rustc"
    subprocess.run([rustc, "--edition", "2021", "-D", "warnings", str(fixture), "-o", str(binary)], check=True, timeout=90)
    assert read_pe_subsystem(binary) == 3, "Actual console source control must link PE subsystem3"
    print("PASS: actual source-extracted standard-account lease control compiles with no Rust warnings", flush=True)
    if subsystem_pair:
        header = '#![windows_subsystem = "windows"]\n'
        gui_fixture = directory / "lease-standard-gui.rs"
        gui_fixture.write_text(header + body, encoding="utf-8", newline="\n")
        assert gui_fixture.read_bytes() == header.encode("ascii") + fixture.read_bytes(), "Console/Windows subsystem pair differs beyond its single crate header"
        gui_binary = directory / "lease-standard-gui.exe"
        subprocess.run([rustc, "--edition", "2021", "-D", "warnings", str(gui_fixture), "-o", str(gui_binary)], check=True, timeout=90)
        assert read_pe_subsystem(gui_binary) == 2, "Actual Windows source control must link PE subsystem2"
        print("PASS: actual console/Windows source pair differs only by the crate subsystem header; linked x64 PE metadata is console3/windows2 with no Rust warnings", flush=True)


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
                try:
                    if owner.poll() is None:
                        owner.kill()
                        owner.wait(timeout=10)
                finally:
                    if owner.stdout is not None:
                        owner.stdout.close()
    if os.name == "nt":
        pwsh = shutil.which("pwsh")
        assert pwsh, "Actual Windows standard-account lease controls require PowerShell7"
        hosted_request = os.environ.get("MIXEL_HOSTED_OWNED_QA_NAMESPACE", "")
        if hosted_request not in ("", "1"):
            raise RuntimeError("Invalid hosted owned QA namespace request")
        driver = ("invoke-hosted-standard-qa-windows.ps1" if hosted_request == "1"
                  else "test-support-lease-standard-windows.ps1")
        subprocess.run([pwsh, "-NoProfile", "-File", str(ROOT / "scripts" / driver)], check=True, timeout=180)
    print("Result: native cross-process attended lease lifecycle passed", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-standard-windows-fixture", type=Path)
    parser.add_argument("--event")
    parser.add_argument("--export-standard-windows-subsystem-pair", action="store_true")
    args = parser.parse_args()
    if args.export_standard_windows_fixture is not None:
        export_standard_windows_fixture(args.export_standard_windows_fixture, args.event or "", args.export_standard_windows_subsystem_pair)
    else:
        assert args.event is None, "An isolated event requires explicit fixture export mode"
        assert not args.export_standard_windows_subsystem_pair, "A subsystem pair requires explicit fixture export mode"
        main()
