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
struct Trace{installed:bool,elevated:bool,pre_elevate:bool,quick:bool,clear_setup:bool,leases:u32,service:u32,forwarded:Vec<String>}
thread_local!{static TRACE:RefCell<Trace>=RefCell::new(Trace::default());}
fn reset(installed:bool,elevated:bool,pre_elevate:bool){TRACE.with(|t|*t.borrow_mut()=Trace{installed,elevated,pre_elevate,..Trace::default()});}
mod platform{
    pub fn is_installed()->bool{super::TRACE.with(|t|t.borrow().installed)}
    pub fn is_elevated(_:Option<()>)->Option<bool>{Some(super::TRACE.with(|t|t.borrow().elevated))}
}
mod config{pub struct LocalConfig;impl LocalConfig{
    pub fn get_option(key:&str)->String{assert_eq!(key,"pre-elevate-service");super::TRACE.with(|t|if t.borrow().pre_elevate{"Y".into()}else{String::new()})}
}}
mod hbb_common{pub mod password_security{
    // ACTUAL_URI_CLASSIFIER
    pub fn hold_support_invite_attended_lease()->bool{super::super::TRACE.with(|t|t.borrow_mut().leases+=1);true}
}}
mod ipc{pub fn set_config(name:&str,value:String)->Result<(), &'static str>{assert_eq!(name,"mixel-support-invite-attended");assert_eq!(value,"Y");Ok(())}}
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
        TRACE.with(|t|{let t=t.borrow();assert!(t.quick);assert!(t.leases>0);assert_eq!(t.service,1);});
        // The real wrapper launches the ordinary technical payload basename
        // with its explicit flag; the actual core argv parser retains intent.
        reset(false,false,false);assert!(core_dispatch(argv("payload/Mixel-Remote.exe",&["--quick_support"]),false).0);
        TRACE.with(|t|{let t=t.borrow();assert!(t.leases>0);assert_eq!(t.service,1);});
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
            TRACE.with(|t|assert_eq!(t.borrow().service,u32::from(flags == ["--quick_support"])));
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
'''

with tempfile.TemporaryDirectory(prefix="mixel-quick-support-name-") as temporary:
    for variant in ("original", "patched"):
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
        classifier = function((ROOT / "scripts/support-invite-guard.rs").read_text(encoding="utf-8"), "pub fn is_support_invite_arg(value: &str) -> bool")
        test_source = SCAFFOLD.replace("// ACTUAL_URI_CLASSIFIER", classifier).replace("// CORE_DETECTOR", core_detector).replace("// PORTABLE_DETECTOR", portable_detector).replace("// WRAPPER_MAIN", wrapper).replace("// CORE_ARGV_PARSER", argv_parser).replace("// CORE_SELECTOR", selector).replace("// CORE_PORTABLE_SERVICE_GATE", service_gate).replace("// ACTUAL_SOURCE_TESTS", TESTS)
        source = Path(temporary) / (variant + ".rs")
        source.write_text(test_source, encoding="utf-8")
        binary = Path(temporary) / (variant + (".exe" if os.name == "nt" else ""))
        print(variant + " source SHA256: core=" + hashlib.sha256(core.encode()).hexdigest() + " portable=" + hashlib.sha256(portable.encode()).hexdigest(), flush=True)
        command = [rustc, "--edition=2021", "--deny=warnings", "--cfg", 'feature="flutter"']
        if variant == "original":
            command += ["--cfg", "original"]
        subprocess.run(command + ["--test", str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary), "--test-threads=1"], check=True)
print("Result: actual original directory-token/customer-alias failures reproduced; generated synchronized basename/copy detectors, wrapper flag forwarding and native consent/service gates passed", flush=True)
