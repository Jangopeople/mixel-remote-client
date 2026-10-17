#!/usr/bin/env python3
"""Compile exact native/portable detectors and their Windows dispatch callers.

Only environment, packaging and service side effects are mocked. The pinned
negative and generated positive selectors, argv parser, wrapper main and
portable-service gate execute as actual Rust 1.75 compatible source.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from rust_toolchain import rustc_command

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", required=True, type=Path)
parser.add_argument("--upstream", required=True, type=Path)
arguments = parser.parse_args()
rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
assert rustc, "Rust compiler required"
upstream_commit = subprocess.check_output(
    ["git", "-C", str(arguments.upstream), "rev-parse", "HEAD"],
    text=True, encoding="utf-8").strip()
assert upstream_commit == "1abc897c451c8b5bbff3792509a7fef9d12f2ce3", "QuickSupport regression must execute the pinned RustDesk 1.4.6 source"


def function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def original(path):
    return subprocess.check_output(
        ["git", "-C", str(arguments.upstream), "show", "HEAD:" + path],
        text=True, encoding="utf-8")


SCAFFOLD = r'''#![allow(dead_code,unused_variables,unused_mut,unused_assignments)]
use std::cell::RefCell;
use std::path::PathBuf;
#[derive(Default)]
struct Trace{installed:bool,elevated:bool,elevation_query_failed:bool,pre_elevate:bool,quick:bool,clear_setup:bool,leases:u32,service:u32,forwarded:Vec<String>,same_window:bool,handoff_failed:bool,window_queries:u32}
thread_local!{static TRACE:RefCell<Trace>=RefCell::new(Trace::default());}
fn reset(installed:bool,elevated:bool,pre_elevate:bool){TRACE.with(|t|*t.borrow_mut()=Trace{installed,elevated,pre_elevate,..Trace::default()});}
mod platform{
    pub fn is_installed()->bool{super::TRACE.with(|t|t.borrow().installed)}
    pub fn is_elevated(_:Option<()>)->Result<bool,&'static str>{super::TRACE.with(|t|{let t=t.borrow();if t.elevation_query_failed{Err("synthetic token query failure")}else{Ok(t.elevated)}})}
}
mod config{pub struct LocalConfig;impl LocalConfig{
    pub fn get_option(key:&str)->String{assert_eq!(key,"pre-elevate-service");super::TRACE.with(|t|if t.borrow().pre_elevate{"Y".into()}else{String::new()})}
}}
mod hbb_common{pub mod password_security{
    // ACTUAL_URI_CLASSIFIER
    pub fn hold_support_invite_attended_lease()->bool{super::super::TRACE.with(|t|t.borrow_mut().leases+=1);true}
}}
mod ipc{pub fn set_config(name:&str,value:String)->Result<(), &'static str>{assert_eq!(name,"mixel-support-invite-attended");assert_eq!(value,"Y");if super::TRACE.with(|t|t.borrow().handoff_failed){Err("no acknowledgement")}else{Ok(())}}}
fn mixel_has_same_executable_main_window()->bool{TRACE.with(|t|{let mut t=t.borrow_mut();t.window_queries+=1;t.same_window})}
mod log{macro_rules! error{($($arg:tt)*)=>{eprintln!($($arg)*)};}pub(crate) use error;}
mod portable_service{pub mod client{
    pub enum StartPara{Direct}
    pub fn set_quick_support(value:bool){super::super::TRACE.with(|t|t.borrow_mut().quick=value);}
    pub fn start_portable_service(_:StartPara)->Result<(), &'static str>{super::super::TRACE.with(|t|t.borrow_mut().service+=1);Ok(())}
}}
#[derive(Default)]struct BinaryReader;
fn setup(_:BinaryReader,_:Option<PathBuf>,clear:bool,_:&Vec<String>,_:&mut bool)->Option<PathBuf>{TRACE.with(|t|t.borrow_mut().clear_setup=clear);Some(PathBuf::from("payload/Mixel-Remote.exe"))}
fn execute(_:PathBuf,args:Vec<String>,_:bool){TRACE.with(|t|t.borrow_mut().forwarded=args);}
// CORE_DETECTOR
mod win{
// PORTABLE_DETECTOR
}
// WRAPPER_MAIN
fn core_dispatch(argv:Vec<String>,click_setup:bool)->(bool,Vec<String>){
// CORE_ARGV_PARSER
// CORE_SELECTOR
// CORE_PORTABLE_SERVICE_GATE
    (_is_quick_support,flutter_args)
}
// ACTUAL_SOURCE_TESTS
'''

TESTS = r'''
fn argv(exe:&str,args:&[&str])->Vec<String>{std::iter::once(exe.to_owned()).chain(args.iter().map(|s|s.to_string())).collect()}
fn legacy_medium_service()->u32{u32::from(cfg!(any(original,warm_original,cold_original)))}
#[cfg(original)]
#[test]
fn actual_original_reproduces_directory_qs_and_missing_customer_alias(){
    let directory=r"C:\ProgramData\mixel-ordinary-qs-123\Mixel-Remote.exe";
    assert!(is_quick_support_exe(directory));assert!(win::is_quick_support_exe(directory));
    reset(false,false,false);assert_eq!(wrapper_dispatch(argv(directory,&[])),vec!["--quick_support"]);
    reset(false,false,false);assert!(core_dispatch(argv(directory,&[]),false).0);
    for alias in ["Mixel-Remote-Support-Windows.exe","Mixel-Remote-Support-Windows (1).exe",
        "Mixel-Remote-Support.exe","Mixel-Remote-Support (1).exe","Mixel-Remote-QS (1).exe"]{
        assert!(!is_quick_support_exe(alias));assert!(!win::is_quick_support_exe(alias));
        reset(false,false,false);assert!(wrapper_dispatch(argv(alias,&[])).is_empty());
        reset(false,false,false);assert!(!core_dispatch(argv(alias,&[]),false).0);
    }
}
#[cfg(not(original))]
#[test]
fn exact_generated_detectors_match_customer_alias_copies_and_legacy_basenames(){
    for name in ["Mixel-Remote-Support-Windows.exe","MIXEL-REMOTE-SUPPORT-WINDOWS.EXE",
        r"C:\Users\Synthetic\Downloads\Mixel-Remote-Support-Windows.exe",
        "/home/synthetic/downloads/Mixel-Remote-Support-Windows.exe",
        "Mixel-Remote-Support-Windows (1).exe","Mixel-Remote-Support-Windows (2).exe",
        "Mixel-Remote-Support-Windows (123).exe","MIXEL-REMOTE-SUPPORT-WINDOWS (9).EXE",
        "Mixel-Remote-Support.exe","MIXEL-REMOTE-SUPPORT.EXE",
        r"C:\Users\Synthetic\Downloads\Mixel-Remote-Support (123).exe",
        "/home/synthetic/downloads/Mixel-Remote-Support.exe",
        "Mixel-Remote-Support (1).exe","MIXEL-REMOTE-SUPPORT (9).EXE",
        "Mixel-Remote-QS (1).exe","MIXEL-REMOTE-QS (123).EXE",
        "Mixel-Remote-QS.exe","Mixel-Remote_qs.exe","Legacy-QS-Custom.exe",
        r"C:\Downloads\Legacy-QS-Custom.exe","/Downloads/Mixel-Remote-QS.exe"]{
        assert!(is_quick_support_exe(name),"core {name}");assert!(win::is_quick_support_exe(name),"wrapper {name}");
        reset(false,false,false);assert_eq!(wrapper_dispatch(argv(name,&[])),vec!["--quick_support"]);
        reset(false,false,false);assert!(core_dispatch(argv(name,&[]),false).0);
        TRACE.with(|t|{let t=t.borrow();assert!(t.quick);assert!(t.leases>0);assert_eq!(t.service,legacy_medium_service());});
        // The real wrapper launches the ordinary technical payload basename
        // with its explicit flag; the actual core argv parser retains intent.
        reset(false,false,false);assert!(core_dispatch(argv("payload/Mixel-Remote.exe",&["--quick_support"]),false).0);
        TRACE.with(|t|{let t=t.borrow();assert!(t.leases>0);assert_eq!(t.service,legacy_medium_service());});
    }
}
#[cfg(not(original))]
#[test]
fn directory_tokens_alias_lookalikes_and_invalid_copy_suffixes_remain_ordinary(){
    for name in ["Mixel-Remote.exe",r"C:\mixel-ordinary-qs-123\Mixel-Remote.exe",
        "/tmp/mixel-ordinary-qs-123/Mixel-Remote.exe",r"C:\folder-qs.exe\Mixel-Remote.exe",
        r"C:\folder_qs.exe\Mixel-Remote.exe",r"C:\Mixel-Remote-Support-Windows.exe\Mixel-Remote.exe",
        "/tmp/Mixel-Remote-Support-Windows (1).exe/Mixel-Remote.exe",
        "Mixel-Remote-Support-Windows.exe.bak","Mixel-Remote-Support-Windows.exe.exe",
        "Other-Mixel-Remote-Support-Windows.exe","Mixel-Remote-Support-Windows-extra.exe",
        "Mixel-Remote-Support-Windows (0).exe","Mixel-Remote-Support-Windows (01).exe",
        "Mixel-Remote-Support-Windows ().exe","Mixel-Remote-Support-Windows (-1).exe",
        "Mixel-Remote-Support-Windows (+1).exe","Mixel-Remote-Support-Windows (1.0).exe",
        "Mixel-Remote-Support-Windows (１).exe","Mixel-Remote-Support-Windows (1) .exe",
        "Mixel-Remote-Support-Windows(1).exe","Mixel-Remote-Support-Windows (1).exe.bak",
        "Mixel-Remote-Support.exe.bak","Mixel-Remote-Support.exe.exe",
        "Other-Mixel-Remote-Support.exe","Mixel-Remote-Support-extra.exe",
        "Mixel-Remote-Support (0).exe","Mixel-Remote-Support (01).exe",
        "Mixel-Remote-Support ().exe","Mixel-Remote-Support (-1).exe",
        "Mixel-Remote-Support (+1).exe","Mixel-Remote-Support (1.0).exe",
        "Mixel-Remote-Support (１).exe","Mixel-Remote-Support (1) .exe",
        "Mixel-Remote-Support(1).exe","Mixel-Remote-Support (1).exe.bak",
        "Mixel-Remote-Support (1) (2).exe", "Mixel-Remote-Support-Windows (1) (2).exe",
        "Mixel-Remote-QS (0).exe","Mixel-Remote-QS (01).exe",
        "Mixel-Remote-QS ().exe","Mixel-Remote-QS (-1).exe","Mixel-Remote-QS (+1).exe",
        "Mixel-Remote-QS (1.0).exe","Mixel-Remote-QS (１).exe","Mixel-Remote-QS (1) .exe",
        "Mixel-Remote-QS(1).exe","Mixel-Remote-QS (1).exe.bak","Mixel-Remote-QS (1) (2).exe",
        r"C:\Mixel-Remote-Support.exe\Mixel-Remote.exe",
        r"C:\mixel-ordinary-qs-123\", "/tmp/mixel-ordinary-qs-123/", ""]{
        assert!(!is_quick_support_exe(name),"core {name}");assert!(!win::is_quick_support_exe(name),"wrapper {name}");
        reset(false,false,false);assert!(wrapper_dispatch(argv(name,&[])).is_empty());
        reset(false,false,false);assert!(!core_dispatch(argv(name,&[]),false).0);
        TRACE.with(|t|{let t=t.borrow();assert!(!t.quick);assert_eq!(t.leases,0);assert_eq!(t.service,0);});
    }
}
#[test]
fn explicit_flags_and_original_argument_install_elevation_gates_remain_intact(){
    for name in ["Mixel-Remote.exe","Mixel-Remote-QS.exe","Mixel-Remote-Support-Windows.exe","Mixel-Remote-Support.exe"]{
        for flags in [&["--quick_support"][..],&["--connect","123456789"][..],&["--install"][..],&["--silent-install"][..],&["--proxy","socks5://127.0.0.1:1080"][..]]{
            reset(false,false,false);assert_eq!(wrapper_dispatch(argv(name,flags)),flags);
            TRACE.with(|t|assert_eq!(t.borrow().clear_setup,flags.contains(&"--silent-install")));
            reset(false,false,false);
            assert_eq!(core_dispatch(argv(name,flags),false).0,flags == ["--quick_support"]);
            TRACE.with(|t|assert_eq!(t.borrow().service,if flags == ["--quick_support"]{legacy_medium_service()}else{0}));
        }
    }
    reset(false,false,false);assert_eq!(wrapper_dispatch(argv(r"C:\mixel-ordinary-qs-123\Mixel-Remote-Install.exe",&[])),["--install"]);
    TRACE.with(|t|assert!(t.borrow().clear_setup));
    reset(true,false,false);assert!(core_dispatch(argv("Mixel-Remote.exe",&["--quick_support"]),false).0);
    TRACE.with(|t|assert_eq!(t.borrow().service,0));
    reset(true,false,false);assert!(!core_dispatch(argv("Mixel-Remote-Support-Windows.exe",&[]),false).0);
    reset(false,true,false);assert!(core_dispatch(argv("Mixel-Remote.exe",&[]),false).0);
    reset(false,false,true);assert!(core_dispatch(argv("Mixel-Remote.exe",&[]),false).0);
    reset(false,true,false);assert!(!core_dispatch(argv("Mixel-Remote.exe",&[]),true).0);
    reset(false,false,false);assert!(core_dispatch(argv("Mixel-Remote.exe",&["--quick_support","--elevate"]),false).0);
    TRACE.with(|t|assert_eq!(t.borrow().service,0));
    reset(false,false,false);assert!(core_dispatch(argv("Mixel-Remote.exe",&["--quick_support","--run-as-system"]),false).0);
    TRACE.with(|t|assert_eq!(t.borrow().service,0));
    reset(false,false,false);assert!(!core_dispatch(argv("Mixel-Remote-Support-Windows.exe",&["--connect","123456789"]),false).0);
}
#[cfg(all(not(original),not(warm_original),not(cold_original)))]
#[test]
fn warm_same_image_acknowledged_window_skips_only_automatic_direct_bootstrap(){
    reset(false,false,false);
    TRACE.with(|t|t.borrow_mut().same_window=true);
    let (quick,flags)=core_dispatch(argv("payload/Mixel-Remote.exe",&["--quick_support"]),false);
    assert!(quick);assert!(flags.contains(&"--mixel-attended".to_owned()));
    TRACE.with(|t|{let t=t.borrow();assert_eq!(t.service,0);assert_eq!(t.window_queries,1);assert!(t.leases>0);assert!(t.quick);});
    reset(false,true,false);
    TRACE.with(|t|{let mut t=t.borrow_mut();t.same_window=true;t.handoff_failed=true;});
    let (_,flags)=core_dispatch(argv("payload/Mixel-Remote.exe",&["--quick_support"]),false);
    assert!(flags.contains(&"--mixel-attended-handoff-unavailable".to_owned()));
    TRACE.with(|t|{let t=t.borrow();assert_eq!(t.service,1);assert_eq!(t.window_queries,0);assert!(t.leases>0);});
    for flags in [&["--quick_support","--elevate"][..],&["--quick_support","--run-as-system"][..]]{
        reset(false,false,false);TRACE.with(|t|t.borrow_mut().same_window=true);
        core_dispatch(argv("payload/Mixel-Remote.exe",flags),false);
        TRACE.with(|t|{let t=t.borrow();assert_eq!(t.service,0);assert_eq!(t.window_queries,0);});
    }
    // Already elevated cold callers retain automatic Direct startup.
    reset(false,true,false);core_dispatch(argv("payload/Mixel-Remote.exe",&["--quick_support"]),false);
    TRACE.with(|t|assert_eq!(t.borrow().service,1));
    reset(false,false,false);TRACE.with(|t|t.borrow_mut().same_window=true);
    assert!(!core_dispatch(argv("payload/Mixel-Remote.exe",&[]),false).0);
    TRACE.with(|t|assert_eq!(t.borrow().window_queries,0));
}
#[cfg(cold_original)]
#[test]
fn actual_previous_cold_gate_requests_privileged_service_from_medium_token(){
    reset(false,false,false);
    core_dispatch(argv("payload/Mixel-Remote.exe",&["--quick_support"]),false);
    TRACE.with(|t|{let t=t.borrow();assert!(t.quick);assert!(t.leases>0);assert_eq!(t.service,1);});
}
#[cfg(all(not(original),not(warm_original),not(cold_original)))]
#[test]
fn cold_attended_gui_never_auto_elevates_medium_or_unqueryable_token(){
    for (exe,flags) in [("mIXEL-rEMOTE-qs.EXE",vec![]),
        ("Mixel-Remote-Support-Windows.exe",vec![]),
        ("payload/Mixel-Remote.exe",vec!["--quick_support"]),
        ("payload/Mixel-Remote.exe",vec!["mixel-remote://support?invite=synthetic&proof=synthetic"])]{
        for pref in [false,true]{
            for failed in [false,true]{
                reset(false,false,pref);TRACE.with(|t|t.borrow_mut().elevation_query_failed=failed);
                let (_,flutter)=core_dispatch(argv(exe,&flags),false);
                TRACE.with(|t|{let t=t.borrow();assert!(t.leases>0);assert_eq!(t.service,0);});
                assert!(flutter.contains(&"--mixel-attended".to_owned()) || !flags.is_empty());
            }
        }
    }
    for pref in [false,true]{
        reset(false,true,pref);core_dispatch(argv("Mixel-Remote-QS.exe",&[]),false);
        TRACE.with(|t|assert_eq!(t.borrow().service,1));
        reset(false,true,pref);TRACE.with(|t|t.borrow_mut().elevation_query_failed=true);
        core_dispatch(argv("Mixel-Remote-QS.exe",&[]),false);
        TRACE.with(|t|assert_eq!(t.borrow().service,0));
    }
    reset(false,false,true);core_dispatch(argv("Mixel-Remote.exe",&[]),false);
    TRACE.with(|t|{let t=t.borrow();assert!(t.quick);assert!(t.leases>0);assert_eq!(t.service,0);});
}
#[cfg(warm_original)]
#[test]
fn actual_previous_service_gate_starts_direct_before_existing_window_dispatch(){
    reset(false,false,false);TRACE.with(|t|t.borrow_mut().same_window=true);
    let (quick,flags)=core_dispatch(argv("payload/Mixel-Remote.exe",&["--quick_support"]),false);
    assert!(quick);assert!(flags.contains(&"--mixel-attended".to_owned()));
    TRACE.with(|t|{let t=t.borrow();assert_eq!(t.service,1);assert_eq!(t.window_queries,0);assert!(t.leases>0);});
}
'''

def run_same_image_window_controls(temporary=None):
    del temporary
    core = (arguments.source / "src/core_main.rs").read_text(encoding="utf-8")
    helper = function(core, "fn mixel_has_same_executable_main_window() -> bool")
    program = r'''#![allow(non_snake_case)]
use std::cell::RefCell;
#[derive(Default)]struct Fake{mode:u8,finds:u32,threads:u32,opens:u32,queries:u32,closes:u32,retained:bool}
thread_local!{static F:RefCell<Fake>=RefCell::new(Fake::default());}
fn reset(mode:u8){F.with(|f|*f.borrow_mut()=Fake{mode,..Fake::default()});}
fn get_app_name()->String{"Mixel-Remote".to_owned()}
mod platform{pub mod windows{pub const FLUTTER_RUNNER_WIN32_WINDOW_CLASS:&str="FLUTTER_RUNNER_WIN32_WINDOW";}}
mod log{macro_rules! error{($($arg:tt)*)=>{eprintln!($($arg)*)};}pub(crate) use error;}
mod winapi{
 pub mod shared{pub mod minwindef{pub const FALSE:i32=0;}}
 pub mod um{
  pub mod winnt{pub const PROCESS_QUERY_LIMITED_INFORMATION:u32=0x1000;}
  pub mod handleapi{pub unsafe fn CloseHandle(h:*mut std::ffi::c_void)->i32{assert_eq!(h as usize,77);super::super::super::F.with(|f|{let mut f=f.borrow_mut();f.closes+=1;if f.mode==7{0}else{f.retained=false;1}})}}
  pub mod processthreadsapi{
   pub unsafe fn GetCurrentProcessId()->u32{std::process::id()}
   pub unsafe fn OpenProcess(access:u32,inherit:i32,pid:u32)->*mut std::ffi::c_void{assert_eq!(access,0x1000);assert_eq!(inherit,0);assert_eq!(pid,54321);super::super::super::F.with(|f|{let mut f=f.borrow_mut();f.opens+=1;if f.mode==3{std::ptr::null_mut()}else{f.retained=true;77usize as *mut _}})}
  }
  pub mod winuser{
   pub unsafe fn FindWindowW(class:*const u16,title:*const u16)->*mut std::ffi::c_void{
    unsafe fn text(p:*const u16)->String{let mut n=0;while *p.add(n)!=0{n+=1;}String::from_utf16(std::slice::from_raw_parts(p,n)).unwrap()}
    assert_eq!(text(class),"FLUTTER_RUNNER_WIN32_WINDOW");assert_eq!(text(title),"Mixel-Remote");
    super::super::super::F.with(|f|{let mut f=f.borrow_mut();f.finds+=1;if f.mode==1 || (f.mode==11 && f.finds==2){std::ptr::null_mut()}else if f.mode==12 && f.finds==2{10usize as *mut _}else{9usize as *mut _}})
   }
   pub unsafe fn GetWindowThreadProcessId(window:*mut std::ffi::c_void,pid:*mut u32)->u32{
    assert_eq!(window as usize,9);super::super::super::F.with(|f|{let mut f=f.borrow_mut();f.threads+=1;if f.mode==2{return 0;}*pid=if f.mode==13 && f.threads==2{999}else if f.mode==14{0}else if f.mode==15{std::process::id()}else{54321};1})
   }
  }
  pub mod winbase{pub unsafe fn QueryFullProcessImageNameW(h:*mut std::ffi::c_void,flags:u32,p:*mut u16,n:*mut u32)->i32{
   assert_eq!(h as usize,77);assert_eq!(flags,0);assert_eq!(*n,32768);
   super::super::super::F.with(|f|{let mut f=f.borrow_mut();f.queries+=1;
    if f.mode==4{return 0;}if f.mode==5{*n=0;return 1;}if f.mode==6{*n=32768;return 1;}if f.mode==16{*n=32769;return 1;}
    let mut image=std::env::current_exe().unwrap().to_str().unwrap().to_owned();
    if f.mode==8{image="C:\\foreign\\Mixel-Remote.exe".to_owned();}
    if f.mode==9{image=image.to_ascii_uppercase();}
    if f.mode==10{image=format!("\\\\?\\{}",image.replace('/',"\\"));}
    let mut units:Vec<u16>=image.encode_utf16().collect();
    if f.mode==17{units=vec![0xd800];}
    for (i,u) in units.iter().enumerate(){*p.add(i)=*u;}
    *n=units.len() as u32;*p.add(units.len())=if f.mode==18{1}else{0};1
   })
  }}
 }
}
// EXACT_GENERATED_HELPER
#[test]fn current_image_requires_owned_closed_query_and_revalidated_window(){for mode in [0,9,10]{reset(mode);assert!(mixel_has_same_executable_main_window());F.with(|f|{let f=f.borrow();assert_eq!((f.opens,f.queries,f.closes),(1,1,1));assert!(!f.retained);assert_eq!((f.finds,f.threads),(2,2));});}}
#[test]fn absent_invalid_self_or_unqueryable_window_never_becomes_warm(){for mode in [1,2,3,14,15]{reset(mode);assert!(!mixel_has_same_executable_main_window());F.with(|f|assert_eq!(f.borrow().closes,0));}}
#[test]fn query_failure_foreign_image_invalid_lengths_and_utf16_close_once(){for mode in [4,5,6,8,16,17,18]{reset(mode);assert!(!mixel_has_same_executable_main_window());F.with(|f|{let f=f.borrow();assert_eq!(f.closes,1);assert!(!f.retained);});}}
#[test]fn failed_close_retains_unresolved_mock_ownership_and_is_not_positive(){reset(7);assert!(!mixel_has_same_executable_main_window());F.with(|f|{let f=f.borrow();assert_eq!(f.closes,1);assert!(f.retained);});}
#[test]fn disappearing_replaced_window_or_reused_pid_never_becomes_warm(){for mode in [11,12,13]{reset(mode);assert!(!mixel_has_same_executable_main_window());F.with(|f|{let f=f.borrow();assert_eq!(f.closes,1);assert!(!f.retained);});}}
'''
    program = program.replace("// EXACT_GENERATED_HELPER", helper)
    with tempfile.TemporaryDirectory(prefix="mixel-warm-qs-window-") as own:
        source=Path(own)/"window.rs";source.write_text(program,encoding="utf-8")
        binary=Path(own)/("window.exe" if os.name=="nt" else "window")
        subprocess.run([rustc,"--edition=2021","--deny=warnings","--test",str(source),"-o",str(binary)],check=True)
        subprocess.run([str(binary),"--test-threads=1"],check=True)
    print("PASS: exact same-image warm window helper; 3 positive and 16 conservative API/path/ownership/race cases; failed close remains unresolved in mock",flush=True)

cold_service_guard = """        // Cold attended support never asks for administrator credentials.
        // A stored pre-elevate-service preference still selects attended mode;
        // elevation requires the existing explicit user action instead.
        && crate::platform::is_elevated(None).unwrap_or(false)
"""
with tempfile.TemporaryDirectory(prefix="mixel-quick-support-name-") as temporary:
    for variant in ("original", "warm_original", "cold_original", "patched"):
        core = original("src/core_main.rs") if variant == "original" else (arguments.source / "src/core_main.rs").read_text(encoding="utf-8")
        portable = original("libs/portable/src/main.rs") if variant == "original" else (arguments.source / "libs/portable/src/main.rs").read_text(encoding="utf-8")
        core_detector = function(core, "fn is_quick_support_exe(exe: &str) -> bool")
        portable_detector = function(portable, "pub(super) fn is_quick_support_exe(exe: &str) -> bool")
        assert re.sub(r"\s+", "", core_detector) == re.sub(r"\s+", "", portable_detector.replace("pub(super) ", "")), "Native and wrapper executable detectors must stay identical"
        wrapper = function(portable, "fn main()")
        wrapper = wrapper.replace("fn main()", "fn wrapper_dispatch(argv:Vec<String>)->Vec<String>")
        wrapper = wrapper.replace("std::env::args()", "argv")
        wrapper = wrapper.replace('    #[cfg(not(windows))]\n    let quick_support = false;\n', "")
        wrapper = wrapper.replace('    #[cfg(windows)]\n', "")
        wrapper = wrapper[:-1] + "    TRACE.with(|t|t.borrow().forwarded.clone())\n}"
        start = core.index("    let mut args = Vec::new();")
        argv_parser = core[start:core.index('    #[cfg(any(target_os = "linux", target_os = "windows"))]', start)].replace("std::env::args()", "argv")
        start = core.index("        _is_quick_support |= !crate::platform::is_installed()")
        selector = core[start:core.index("    }\n    let mut log_name", start)]
        start = core.index("    if !crate::platform::is_installed()\n")
        service_gate = function(core, core[start:core.index(" {", start)])
        if variant != "original":
            assert service_gate.count(cold_service_guard) == 1
        if variant in ("warm_original", "cold_original"):
            service_gate = service_gate.replace(cold_service_guard, "", 1)
        if variant == "warm_original":
            assert service_gate.count("        && !(_mixel_attended_handoff_confirmed && mixel_has_same_executable_main_window())\n") == 1
            service_gate = service_gate.replace("        && !(_mixel_attended_handoff_confirmed && mixel_has_same_executable_main_window())\n", "", 1)
            previous_core = original("src/core_main.rs")
            previous_start = previous_core.index("    if !crate::platform::is_installed()\n")
            previous_gate = function(previous_core, previous_core[previous_start:previous_core.index(" {", previous_start)])
            # The only prior branding delta in this exact block adds the support
            # URI condition. Removing it proves the whole old gate is pinned.
            assert service_gate.replace("(args.is_empty() || _is_mixel_support_invite)", "args.is_empty()", 1) == previous_gate
        classifier = function((ROOT / "scripts/support-invite-guard.rs").read_text(encoding="utf-8"), "pub fn is_support_invite_arg(value: &str) -> bool")
        test_source = SCAFFOLD.replace("// ACTUAL_URI_CLASSIFIER", classifier).replace("// CORE_DETECTOR", core_detector).replace("// PORTABLE_DETECTOR", portable_detector).replace("// WRAPPER_MAIN", wrapper).replace("// CORE_ARGV_PARSER", argv_parser).replace("// CORE_SELECTOR", selector).replace("// CORE_PORTABLE_SERVICE_GATE", service_gate).replace("// ACTUAL_SOURCE_TESTS", TESTS)
        source = Path(temporary) / (variant + ".rs")
        source.write_text(test_source, encoding="utf-8")
        binary = Path(temporary) / (variant + (".exe" if os.name == "nt" else ""))
        print(variant + " source SHA256: core=" + hashlib.sha256(core.encode()).hexdigest() + " portable=" + hashlib.sha256(portable.encode()).hexdigest(), flush=True)
        command = rustc_command([rustc, "--edition=2021", "--deny=warnings", "--cfg", 'feature="flutter"'])
        if variant == "original":
            command += ["--cfg", "original"]
        if variant == "warm_original":
            command += ["--cfg", "warm_original"]
        if variant == "cold_original":
            command += ["--cfg", "cold_original"]
        subprocess.run(command + ["--test", str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary), "--test-threads=1"], check=True)
run_same_image_window_controls(temporary=None)
print("Result: actual original directory-token/customer-alias failures reproduced; generated synchronized basename/copy detectors, wrapper flag forwarding and native consent/service gates passed", flush=True)
