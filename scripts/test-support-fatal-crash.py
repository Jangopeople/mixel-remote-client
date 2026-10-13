#!/usr/bin/env python3
"""Run exact-source guards and isolated fatal-crash children; never launch the app."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('fatal_crash_patch', HERE/'patch-support-fatal-crash.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

C = r'''
#define _DEFAULT_SOURCE 1
#include <errno.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <unistd.h>
int owned_disable_core(void) {
 struct rlimit limit = {0, 0}; return setrlimit(RLIMIT_CORE, &limit);
}
int owned_protection_fault(void) {
 long length = sysconf(_SC_PAGESIZE);
 if (length <= 0) return 11;
 void *page = mmap(NULL, (size_t)length, PROT_READ | PROT_WRITE,
                  MAP_PRIVATE | MAP_ANON, -1, 0);
 if (page == MAP_FAILED) return 12;
 if (mprotect(page, (size_t)length, PROT_NONE) != 0) {
  munmap(page, (size_t)length); return 13;
 }
 // Only this newly allocated child's page is accessed. No unrelated memory is used.
 volatile unsigned char value = *(volatile unsigned char *)page;
 (void)value;
 munmap(page, (size_t)length);
 return 14;
}
'''

MODEL = r'''
extern crate self as log;
#[macro_export] macro_rules! info { ($($arg:tt)*)=>{{let _=format_args!($($arg)*);}} }
#[macro_export] macro_rules! error { ($($arg:tt)*)=>{{let _=format_args!($($arg)*);}} }
mod config {
 pub struct Config;
 impl Config {pub fn set_option(_k:String,_v:String) {panic!("GPU mutation outside no-symbol control")}}
}
mod backtrace {
 pub struct Frame; pub struct Symbol;
 impl Symbol {pub fn name(&self)->Option<&'static str> {None}}
 pub fn trace<F:FnMut(&Frame)->bool>(mut callback:F) {callback(&Frame);}
 pub fn resolve_frame<F:FnMut(&Symbol)>(_frame:&Frame,mut callback:F) {callback(&Symbol);}
}
mod libc {
 pub const SIGSEGV:i32=11;
 extern "C" {pub fn signal(sig:i32, handler:usize)->usize;pub fn raise(sig:i32)->i32;pub fn close(fd:i32)->i32;}
}
#[allow(dead_code)] mod platform;
mod input_service {
 use std::sync::Mutex;
 #[derive(Copy,Clone)] pub enum Key {Shift,Control,Alt,Meta,RightShift,RightControl,RightAlt,RWin}
 pub struct Enigo {pub releases:usize,pub clears:usize}
 impl Enigo {pub fn tfc_clear_remapped(&mut self){self.clears+=1;} pub fn key_up(&mut self,_key:Key){self.releases+=1;}}
 pub static ENIGO:Mutex<Enigo>=Mutex::new(Enigo{releases:0,clears:0});
 fn get_modifier_state(_key:Key,_en:&mut Enigo)->bool {true}
 __CLEAR__
 __RELEASE__
}
__CALLBACK__
extern "C" {fn owned_disable_core()->i32;fn owned_protection_fault()->i32;}
#[no_mangle]
pub extern "C" fn owned_model_run(argument:*const std::os::raw::c_char)->i32 {
 assert!(!argument.is_null());
 let mode=unsafe{std::ffi::CStr::from_ptr(argument)}.to_str().unwrap();
 if mode=="normal" {
  breakdown_callback();
  let en=input_service::ENIGO.lock().unwrap();
  assert_eq!(en.releases,8);
  assert_eq!(en.clears,if cfg!(target_os="linux"){1}else{0});
  println!("PASS normal callback outside signal releases modifiers");return 0;
 }
 unsafe{assert_eq!(owned_disable_core(),0);}
 __REGISTER__
 let _held=if mode.contains("held") {Some(input_service::ENIGO.lock().unwrap())} else {None};
 println!("owned fatal child ready {}",mode);
 if mode.contains("closed") {unsafe{assert_eq!(libc::close(2),0);}}
 if mode.contains("fault") {
  let error=unsafe{owned_protection_fault()};
  panic!("owned protection fault returned: {}",error);
 }
 unsafe{assert_eq!(libc::raise(libc::SIGSEGV),0);}
 panic!("fatal signal returned");
}
'''


def command(args, folder, label, expected=0, timeout=60):
    completed = subprocess.run([str(x) for x in args], capture_output=True, timeout=timeout)
    (folder/(label+'.stdout')).write_bytes(completed.stdout)
    (folder/(label+'.stderr')).write_bytes(completed.stderr)
    if completed.returncode != expected:
        raise RuntimeError(label+' returned '+str(completed.returncode)+'\n'+completed.stderr.decode('utf-8',errors='replace'))
    return completed


def block(text, marker):
    start = text.index(marker); opening = text.index('{', start); depth = 1
    for index in range(opening+1, len(text)):
        depth += (text[index]=='{') - (text[index]=='}')
        if depth == 0:
            return text[start:index+1]
    raise RuntimeError('Incomplete original normal-cleanup function')


def snapshot(root):
    result = {}
    for path in root.rglob('*'):
        info = path.lstat(); name = str(path.relative_to(root))
        if stat.S_ISLNK(info.st_mode): result[name] = ('symlink',os.readlink(path))
        elif stat.S_ISREG(info.st_mode): result[name] = ('file',path.read_bytes())
        elif stat.S_ISDIR(info.st_mode): result[name] = ('directory',)
        else: result[name] = ('other',info.st_mode)
    return result


def write_pair(root, pair, newline):
    for name,data in zip(P.FILES,pair):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data.replace(b'\n',newline))


def refuse(root, operation):
    before = snapshot(root)
    try: operation()
    except (RuntimeError, FileNotFoundError): pass
    else: raise AssertionError('Invalid fatal-crash source accepted')
    assert snapshot(root)==before, 'Invalid source operation wrote before pair validation'


def source_controls(folder, profiles):
    count=0
    for index, pair in enumerate(profiles):
        corrected=P.project(*pair)
        for newline in (b'\n',b'\r\n'):
            root=folder/('roundtrip-'+str(index)+'-'+str(len(newline)));write_pair(root,pair,newline)
            baseline=snapshot(root)
            P.apply(root,check=True);P.apply(root,restore=True,check=True);assert snapshot(root)==baseline
            P.apply(root);first=snapshot(root);P.apply(root);assert snapshot(root)==first
            P.apply(root,restore=True);assert snapshot(root)==baseline
            assert P.project(*corrected,restore=True)==pair
            for state,data in enumerate((pair,corrected)):
                for target in range(2):
                    for mutation in ('append','mixed','missing','hardlink','symlink','fifo'):
                        if mutation=='fifo' and not hasattr(os,'mkfifo'):continue
                        root=folder/('invalid-'+str(count));write_pair(root,data,newline);path=root/P.FILES[target]
                        if mutation=='append':path.write_bytes(path.read_bytes()+b'// unexpected\n')
                        elif mutation=='mixed':path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n',1) if newline==b'\n' else path.read_bytes().replace(b'\r\n',b'\n',1))
                        elif mutation=='missing':path.unlink()
                        elif mutation=='hardlink':os.link(path,root/'extra-link')
                        elif mutation=='symlink':path.rename(root/'actual');path.symlink_to(root/'actual')
                        else:path.unlink();os.mkfifo(path)
                        for restore,check in ((False,False),(True,False),(False,True),(True,True)):
                            refuse(root,lambda r=restore,c=check:P.apply(root,restore=r,check=c));count+=1
            # Reject a mismatched profile and a partial installation as one pair.
            for mixed in ((pair[0],corrected[1]),(corrected[0],pair[1])):
                root=folder/('partial-'+str(count));write_pair(root,mixed,newline)
                for restore,check in ((False,False),(True,False),(False,True),(True,True)):
                    refuse(root,lambda r=restore,c=check:P.apply(root,restore=r,check=c));count+=1
    root=folder/'reparse-model';write_pair(root,profiles[0],b'\n');original_lstat=Path.lstat
    for target in (root,root/'libs/hbb_common/src',root/P.FILES[0],root/P.FILES[1]):
        def modeled(path,*args,**kwargs):
            value=original_lstat(path,*args,**kwargs)
            if path==target:
                return SimpleNamespace(st_mode=value.st_mode,st_nlink=value.st_nlink,st_size=value.st_size,st_file_attributes=0x400)
            return value
        before=snapshot(root)
        with patch.object(Path,'lstat',modeled):
            for restore,check in ((False,False),(True,False),(False,True),(True,True)):
                try:P.apply(root,restore=restore,check=check)
                except RuntimeError:count+=1
                else:raise AssertionError('Windows reparse attribute accepted')
        assert snapshot(root)==before
    # External alias inputs stay strict; only internally created test paths are resolved.
    alias=folder/'source-alias';alias.symlink_to(root,target_is_directory=True)
    for restore,check in ((False,False),(True,False),(False,True),(True,True)):
        refuse(folder,lambda:P.apply(alias,restore=restore,check=check));count+=1
    print('PASS exact LF/CRLF pair roundtrips, idempotence and '+str(count)+' prewrite source/ownership refusals')
    return count


def metadata(folder, rustc, linux_sysroot):
    root=folder/'metadata';root.mkdir()
    (root/'lib.rs').write_bytes(P.CORRECTED_PLATFORM)
    for name in ('linux','macos','windows'):(root/(name+'.rs')).write_text('pub fn preserved_platform_module() {}\n')
    args=[rustc,'--edition=2021','--crate-type=lib','--emit=metadata','-Dwarnings','-Cdebug-assertions=no',root/'lib.rs']
    command(args+['-o',root/'host.rmeta'],folder,'metadata-host')
    targets=['host']
    # Cross-targets are explicit available vendor stds, never fabricated target cfg.
    sysroot=Path(subprocess.check_output([str(rustc),'--print','sysroot']).decode().strip())
    windows='x86_64-pc-windows-msvc'
    if (sysroot/'lib/rustlib'/windows/'lib').is_dir():
        command(args+['--target',windows,'-o',root/'windows.rmeta'],folder,'metadata-windows');targets.append(windows)
    if linux_sysroot:
        command(args+['--sysroot',linux_sysroot,'--target','x86_64-unknown-linux-gnu','-o',root/'linux.rmeta'],folder,'metadata-linux');targets.append('x86_64-unknown-linux-gnu')
    return targets


def run_child(binary,mode,folder,expected=None,timeout=False,full=False):
    reader=writer=None;process=None;capacity=None;started=time.monotonic()
    try:
        if full:
            reader,writer=os.pipe();os.set_blocking(writer,False);capacity=0
            while True:
                try:capacity+=os.write(writer,b'x'*4096)
                except BlockingIOError:break
            assert capacity>0;os.set_blocking(writer,True)
        process=subprocess.Popen([str(binary),mode],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=writer if full else subprocess.PIPE)
        try:
            stdout,stderr=process.communicate(timeout=1.5)
            assert not timeout and process.returncode in (expected if isinstance(expected,tuple) else (expected,)),(mode,process.returncode,stderr)
            expired=False
        except subprocess.TimeoutExpired:
            assert timeout,mode
            process.terminate()
            try:stdout,stderr=process.communicate(timeout=1.5)
            except subprocess.TimeoutExpired:process.kill();stdout,stderr=process.communicate(timeout=1.5)
            expired=True
        assert process.poll() is not None
        if mode!='normal':assert ('owned fatal child ready '+mode).encode() in stdout,(mode,stdout)
        else:assert b'PASS normal callback' in stdout
        (folder/(mode+'.stdout')).write_bytes(stdout)
        (folder/(mode+'.stderr')).write_bytes(stderr or b'')
        return {'mode':mode,'exit':process.returncode,'timedOut':expired,'fullBlockingStderrPipeBytes':capacity,'seconds':time.monotonic()-started,'ownedChildReaped':True}
    finally:
        if process is not None and process.poll() is None:
            process.kill();process.communicate(timeout=3)
        for fd in (reader,writer):
            if fd is not None:os.close(fd)


def native_controls(folder,source,rustc,original_platform):
    if os.name!='posix':
        return {'executed':False,'reason':'POSIX isolated signal/guard-page child is not a Windows native exception test'}
    platform=(source/'src/platform/mod.rs').read_text()
    inputs=(source/'src/server/input_service.rs').read_text()
    callback=block(platform,'pub fn breakdown_callback()')
    clear=block(inputs,'pub fn clear_remapped_keycode()')
    release=block(inputs,'pub fn release_device_modifiers()')
    records=[]
    for state,actual in (('original',original_platform),('corrected',P.CORRECTED_PLATFORM)):
        root=folder/state;root.mkdir()
        (root/'platform.rs').write_bytes(actual)
        sub=root/'platform';sub.mkdir()
        (sub/'linux.rs').write_text('pub fn system_message(_a:&str,_b:&str,_c:bool)->Result<(),()>{panic!("No GPU branch in model")}\n')
        for name in ('macos','windows'):(sub/(name+'.rs')).write_text('')
        model=MODEL.replace('__CLEAR__',clear).replace('__RELEASE__',release).replace('__CALLBACK__',callback)
        model=model.replace('__REGISTER__','platform::register_breakdown_handler(breakdown_callback);' if state=='original' else '')
        # Model-only imports do not alter the extracted production platform body.
        # Rust1.75 predates the original source's static_mut_refs allowance;
        # only this historical isolated module permits an unknown lint.
        # Corrected production metadata remains under unmodified -Dwarnings.
        model='#![allow(dead_code)]\n'+model
        (root/'main.rs').write_text(model)
        (root/'owned-fault.c').write_text(C)
        command([os.environ.get('CC','cc'),'-std=c11','-fPIC','-Wall','-Wextra','-Werror','-c',root/'owned-fault.c','-o',root/'owned-fault.o'],root,'c-compile')
        # Original uses crate-local stand-ins for unrelated logging/backtrace/config;
        # the handler/register/callback/lock bodies and real signal ABI are exact.
        original_import='#![allow(unknown_lints)]\nuse crate::{libc, backtrace};\n' if state=='original' else ''
        (root/'platform.rs').write_bytes(original_import.encode()+actual)
        library='libowned_model.dylib' if sys.platform=='darwin' else 'libowned_model.so'
        command([rustc,'--edition=2021','--crate-type=cdylib','-Dwarnings','-Cdebug-assertions=no',root/'main.rs','-Clink-arg='+str(root/'owned-fault.o'),'-o',root/library],root,'rust-compile')
        (root/'host.c').write_text('extern int owned_model_run(const char *);\nint main(int argc,char **argv){if(argc!=2)return 99;return owned_model_run(argv[1]);}\n')
        command([os.environ.get('CC','cc'),'-Wall','-Wextra','-Werror',root/'host.c','-L'+str(root),'-lowned_model','-Wl,-rpath,'+str(root),'-o',root/'child'],root,'c-host-compile')
        records.append(run_child(root/'child','normal',root,expected=0))
        if state=='original':
            records.append(run_child(root/'child','original-unlocked',root,expected=0))
            records.append(run_child(root/'child','original-held',root,timeout=True))
        else:
            for mode,full in (('corrected-held',False),('corrected-closed',False),('corrected-held-full',True),('corrected-held-fault',False),('corrected-held-fault-full',True)):
                expected=(-signal.SIGSEGV,-signal.SIGBUS) if 'fault' in mode else -signal.SIGSEGV
                records.append(run_child(root/'child',mode,root,expected=expected,full=full))
    result={'executed':True,'scope':'Owned isolated C-host/Rust-cdylib POSIX children matching embedded-library startup; original literal signal/lock functions with explicit no-symbol stand-ins; corrected native disposition; owned C mmap/mprotect fault page; normal callback outside signal; no app/UI/GPU mutation','records':records,'windowsNativeExceptionExecuted':False,'automaticCrashTimeGpuRetryRemoved':True}
    (folder/'native-results.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS '+str(len(records))+' actual owned signal/normal/guard-page children; original lock blocks, corrected fatal paths terminate with held lock/full pipe, all children/fds drained')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--upstream-repo',type=Path)
    parser.add_argument('--evidence-dir',type=Path)
    parser.add_argument('--linux-sysroot',type=Path)
    args=parser.parse_args()
    # Validate supplied source before compiler/output/network work.
    input_paths=[P.safe_target(args.source,name) for name in P.FILES]
    raw=[path.read_bytes() for path in input_paths]
    normalized=[P.canonical(value)[0] for value in raw]
    supplied=P.project(*normalized,restore=True)
    upstream=args.upstream_repo or Path(os.environ.get('RDREPO',args.source))
    pristine=(subprocess.check_output(['git','-C',str(upstream),'show',P.UPSTREAM+':'+P.FILES[0]]),
              subprocess.check_output(['git','-C',str(upstream/'libs/hbb_common'),'show',P.HBB+':src/platform/mod.rs']))
    P.project(*pristine)
    profiles=[pristine]
    if supplied!=pristine:profiles.append(supplied)
    # Composed profile is source-bound when provided, rather than a fabricated cfg.
    rustc=Path(os.environ.get('RUSTC_BIN',shutil.which('rustc') or 'rustc')).absolute()
    version=subprocess.check_output([str(rustc),'--version']).decode().strip()
    if not (version.startswith('rustc 1.75.0 ') or version.startswith('rustc 1.81.0 ')):
        raise RuntimeError('Expected actual pinned Rust1.75.0 or1.81.0')
    folder=args.evidence_dir or Path(tempfile.mkdtemp(prefix='mixel-fatal-crash-controls-')).resolve()/'controls'
    folder.mkdir(parents=True,exist_ok=False);folder=folder.resolve()
    count=source_controls(folder,profiles)
    targets=metadata(folder,rustc,args.linux_sysroot)
    native=native_controls(folder,args.source,rustc,supplied[1])
    assert [path.read_bytes() for path in input_paths]==raw,'Input source changed during controls'
    (folder/'qualification.json').write_text(json.dumps({'rustc':version,'sourceProfiles':len(profiles),'refusals':count,'metadataTargets':targets,'native':native,'inputBytesUnchanged':True,'scope':'Source/isolated owned children; application and packaged native acceptance remain separate'},indent=2)+'\n')
    print('PASS fatal-crash source qualification; native runtime disposition preserved, normal cleanup/manual GPU settings unchanged; packaged app acceptance remains separate')


if __name__=='__main__':main()
