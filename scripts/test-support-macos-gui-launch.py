#!/usr/bin/env python3
"""Compile exact Mac launch/typed transport code; owned children only, no app/URI."""
import argparse, ast, hashlib, importlib.util, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from rust_toolchain import rustc_command
ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'scripts'
UPSTREAM=Path(os.environ.get('RDREPO', ROOT/'rustdesk'))

def original(path):
    return subprocess.check_output(['git','-C',str(UPSTREAM),'show','HEAD:'+path]).decode()

def run(cmd, **kw):
    result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,**kw)
    print(result.stdout.decode('utf-8','replace'),end='')
    if result.returncode: raise SystemExit(result.returncode)
    return result

def source_guards(layer, source, out):
    """Actual full input, exact profile inversion and prewrite refusal controls."""
    compiler=layer.prior('patch-support-compiler-cleanup.py')
    def snapshot(root):
        result={}
        for parent, dirs, files in os.walk(root, followlinks=False):
            dirs[:]=[name for name in dirs if name!='.git']
            for name in files:
                path=Path(parent)/name
                result[path.relative_to(root).as_posix()]=('link:'+os.readlink(path) if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest())
        return result
    before=snapshot(source)
    stage=out/'owned-full-source'
    shutil.copytree(source,stage,symlinks=True,ignore=lambda directory,names:compiler['projection_ignored'](source,directory,names))
    # Preserve exact nested Git metadata because prior native owners require it.
    result=layer.apply(stage,check=True,restore=True,verify=True)
    assert result['writes']==0
    if result['profile'] in ('final','previous-final','legacy'):layer.apply(stage,restore=True)
    original=snapshot(stage)
    assert layer.apply(stage,verify=True)['writes']==len(layer.ORIGINAL)+1
    final=snapshot(stage)
    assert layer.apply(stage,verify=True)['writes']==0 and snapshot(stage)==final
    assert layer.apply(stage,restore=True,verify=True)['writes']==len(layer.ORIGINAL)+1 and snapshot(stage)==original
    refusals=0
    for ending in ('\n','\r\n'):
        for name in layer.ORIGINAL:
            path=stage/name;raw=path.read_bytes();logical,_=compiler['canonical'](raw);path.write_bytes(logical.replace('\n',ending).encode())
        profile=snapshot(stage)
        layer.apply(stage,verify=True);layer.apply(stage,restore=True,verify=True)
        assert snapshot(stage)==profile
        for name in layer.ORIGINAL:
            path=stage/name;saved=path.read_bytes()
            for raw in (saved+b'// owned hash drift'+ending.encode(),saved.replace(b'\n',b'\r\n',1) if ending=='\n' else saved.replace(b'\r\n',b'\n',1)):
                path.write_bytes(raw);poison=snapshot(stage)
                try:layer.apply(stage,verify=True)
                except (RuntimeError,SystemExit):pass
                else:raise AssertionError('Changed or mixed launch profile accepted: '+name)
                assert snapshot(stage)==poison;refusals+=1
                path.write_bytes(saved)
        # Complete planned transformations must be checked before any write.
        original_transform=layer.transform
        last=list(layer.ORIGINAL)[-1]
        def late_failure(name,text,restore=False):
            if name==last:raise RuntimeError('owned final transform refusal')
            return original_transform(name,text,restore)
        layer.transform=late_failure
        poison=snapshot(stage)
        try:layer.apply(stage)
        except RuntimeError:pass
        else:raise AssertionError('Late transform failure accepted')
        assert snapshot(stage)==poison;refusals+=1;layer.transform=original_transform
    # A disjoint unsafe source must refuse before a regularizing copy.
    target=stage/'src/ipc.rs';saved=target.read_bytes();target.unlink();os.symlink(stage/'src/core_main.rs',target)
    poison=snapshot(stage)
    try:layer.apply(stage,verify=True)
    except (RuntimeError,SystemExit):pass
    else:raise AssertionError('Source symlink accepted')
    assert snapshot(stage)==poison;refusals+=1;target.unlink();target.write_bytes(saved)
    os.link(target,stage/'owned-hardlink')
    poison=snapshot(stage)
    try:layer.apply(stage,verify=True)
    except (RuntimeError,SystemExit):pass
    else:raise AssertionError('Source hardlink accepted')
    assert snapshot(stage)==poison;refusals+=1;(stage/'owned-hardlink').unlink()
    assert snapshot(source)==before,'Read-only complete input changed'
    (out/'source-refusals.json').write_bytes((json.dumps({'whole_profiles':2,'zero_write_refusals':refusals,'supplied_input_unchanged':True},sort_keys=True)+'\n').encode())
    print('PASS exact launch whole-source LF/CRLF apply/inverse; '+str(refusals)+' prewrite refusals; supplied input unchanged')


def portable_fixture(fixture, launch, output):
    # Compile the exact platform-independent Pending, Queue and typed sender.
    # Native reservation/peer controls are separate Mac-only owned children.
    launch=launch[:launch.index('#[cfg(any(target_os = "macos", test))]')].replace('use std::sync::{Mutex, OnceLock};\n','')
    (output/'launch-fixture.rs').write_bytes(launch.encode())
    def block(text,start):
        begin=text.index(start);opening=text.index('{',begin);depth=1;end=opening+1
        while depth:
            depth+=(text[end]=='{')-(text[end]=='}');end+=1
        return text[begin:end]
    fixture=fixture.replace(block(fixture,'pub mod password_security'), '')
    fixture=fixture.replace(block(fixture,'pub mod log'), '')
    fixture=fixture.replace(block(fixture,'mod ipc'), '')
    fixture=fixture.replace(block(fixture,'fn test_command()'), '')
    for name in ('fn phase_pause','fn release_phase','fn wait_phase','fn fixture_poll','fn worker_gate_marker'):
        fixture=fixture.replace(block(fixture,name), '')
    kept=('typed_transport_only_known_absence_authorizes_spawn','latest_intent_and_ordinary_open_cannot_resurrect_superseded_or_erase_latest','queue_contract_and_cold_accepted_route_preserved','generation_ttl_discards_only_same_stale_intent')
    import re
    for name in re.findall(r'#\[test\]fn (\w+)\(',fixture):
        if name not in kept:fixture=fixture.replace(block(fixture,'#[test]fn '+name), '')
    fixture=fixture.replace(' *MODE.lock().unwrap()="accepted";assert!(!server::mixel_gui_launch::cold_route(FIRST.into()));assert!(!server::mixel_gui_launch::cold_route("invalid".into()));password_security::actual_parent_absent();',' assert_eq!(server::mixel_gui_launch::POLL.as_millis(),100);')
    fixture='\n'.join(line for line in fixture.splitlines() if not any(line.startswith('static '+name) for name in ('SPAWNS','CHILD_READY','DELIVERED','LEASE_CALLS','LEASE_OK','PANICKED','EXPECT_ORDINARY','LEASE_DELAY','LEASE_BLOCK','LEASE_EVENT','BRIDGE_PHASE','PHASE_READY','PHASE_RELEASE','PHASE_EVENT')))+'\n'
    return fixture

SCOPED_GUARD_DRIVER = '\nuse std::os::unix::{fs::MetadataExt,io::{AsRawFd,FromRawFd},process::CommandExt};\nextern "C" {fn fcntl(fd:i32,command:i32,...) ->i32; fn getdtablesize()->i32; fn geteuid()->u32;}\nfn active()->bool {mixel_support_lease::observation().2.unwrap()}\nfn pristine_memory_owner(){assert_eq!(MIXEL_SUPPORT_INVITE_UNTIL.load(std::sync::atomic::Ordering::SeqCst),0);assert!(!support_invite_owner_lease_failed());assert_eq!(mixel_support_lease::fixture_owner_facts(),(false,0));}\nfn wait(p:&std::path::Path){let end=std::time::Instant::now()+std::time::Duration::from_secs(3);while !p.exists(){assert!(std::time::Instant::now()<end,"owned marker timeout");std::thread::sleep(std::time::Duration::from_millis(5));}}\nfn lease_metadata()->std::fs::Metadata {\n let root=std::path::Path::new(env!("MIXEL_AUDIT_ROOT"));let entries=std::fs::read_dir(root).unwrap().map(|p|p.unwrap().path()).filter(|p|p.is_dir()&&p.file_name().unwrap().to_string_lossy().starts_with("mixel-remote-attended-v2-")).collect::<Vec<_>>();assert_eq!(entries.len(),1);let meta=std::fs::symlink_metadata(entries[0].join("lease")).unwrap();assert!(meta.is_file());assert_eq!(meta.uid(),unsafe{geteuid()});assert_eq!(meta.mode()&0o777,0o600);assert_eq!(meta.nlink(),1);meta\n}\nfn lease_fds(expected:&std::fs::Metadata)->Vec<i32> {\n let limit=unsafe{getdtablesize()};assert!(limit>0,"OS descriptor bound unavailable");let mut matches=Vec::new();\n for fd in 0..limit {\n  if unsafe{fcntl(fd,1)}<0 {assert_eq!(std::io::Error::last_os_error().raw_os_error(),Some(9),"descriptor scan error");continue;}\n  let borrowed=std::mem::ManuallyDrop::new(unsafe{std::fs::File::from_raw_fd(fd)});let meta=borrowed.metadata().expect("open descriptor metadata scan failed");\n  if meta.dev()==expected.dev()&&meta.ino()==expected.ino(){assert_eq!(meta.uid(),expected.uid());assert_eq!(meta.mode(),expected.mode());assert_eq!(meta.nlink(),expected.nlink());matches.push(fd);}\n }matches\n}\nfn foreground_fd()->i32 {let matches=lease_fds(&lease_metadata());assert_eq!(matches.len(),1);matches[0]}\nfn drain_closed_bridge(fd:i32,expected:&std::fs::Metadata){\n assert!(unsafe{fcntl(fd,1)}<0,"parent scoped descriptor still open");assert_eq!(std::io::Error::last_os_error().raw_os_error(),Some(9));assert!(lease_fds(expected).is_empty(),"parent retains owned lease descriptor");pristine_memory_owner();\n let started=std::time::Instant::now();let end=started+std::time::Duration::from_secs(3);let mut active_samples=0usize;\n loop {assert!(std::time::Instant::now()<end,"failed-exec owned endpoint did not become absent");if !active(){break;}active_samples=active_samples.checked_add(1).unwrap();pristine_memory_owner();std::thread::yield_now();}\n assert!(!support_invite_requires_click());pristine_memory_owner();println!("parentDescriptorClosed=true;parentOwnedLeaseDescriptors=0;scanCoversCapturedDescriptor=true;failedExecActiveSamples={active_samples};drainElapsedMicros={};boundedFinalKernelAbsent=true",started.elapsed().as_micros());\n}\nstruct OwnedChild(std::process::Child);impl Drop for OwnedChild{fn drop(&mut self){let _=self.0.kill();let _=self.0.wait();}}\nfn main(){\n let mode=std::env::args().nth(1).unwrap();let signals=std::path::PathBuf::from(std::env::var_os("MIXEL_SIGNAL_ROOT").unwrap());\n if mode=="probe" {println!("kernelActive={}",active());return;}\n if mode=="scope" {pristine_memory_owner();assert!(!active());{let b=acquire_support_invite_startup_bridge().unwrap();assert!(b.current().unwrap());assert!(active());pristine_memory_owner();}assert!(!active());assert!(!support_invite_requires_click());pristine_memory_owner();println!("scopedAcquireDropAbsent=true;foregroundAndMemoryUnchanged=true");return;}\n if mode=="foreground-survives" {assert!(hold_support_invite_attended_lease());MIXEL_SUPPORT_INVITE_UNTIL.store(0,std::sync::atomic::Ordering::SeqCst);{let b=acquire_support_invite_startup_bridge().unwrap();assert!(b.current().unwrap());}assert!(active());assert!(support_invite_requires_click());println!("foregroundSurvivesScopedDrop=true");return;}\n if mode=="foreground-alias" {assert!(hold_support_invite_attended_lease());let fd=foreground_fd();match unsafe { adopt_support_invite_startup_bridge(fd) }{Ok(b)=>drop(b),Err(_)=>()}assert!(unsafe{fcntl(fd,1)}>=0,"safe adoption closed foreground OWNER descriptor");println!("foregroundAliasRefusedWithoutClose=true");return;}\n if mode=="foreign-fd" {let p=signals.join("foreign");std::fs::write(&p,b"foreign-live").unwrap();let f=std::fs::File::open(&p).unwrap();let fd=f.as_raw_fd();assert!(unsafe { adopt_support_invite_startup_bridge(fd) }.is_err());assert!(unsafe{fcntl(fd,1)}>=0);assert_eq!(f.metadata().unwrap().len(),12);for bad in [-1,0,1,2,2147483647]{assert!(unsafe { adopt_support_invite_startup_bridge(bad) }.is_err());}pristine_memory_owner();assert!(!active());println!("foreignFdAndInvalidNumbersRefusedWithoutClose=true");return;}\n if mode=="spawn-failed" {pristine_memory_owner();let fd;let expected;{let b=acquire_support_invite_startup_bridge().unwrap();fd=b.raw_fd();expected=lease_metadata();assert_eq!(lease_fds(&expected),vec![fd]);assert!(std::process::Command::new(signals.join("nonexistent-owned-child")).spawn().is_err());}drain_closed_bridge(fd,&expected);pristine_memory_owner();println!("failedSpawnScopedDropAbsent=true");return;}\n if mode=="high-fd-scan"||mode=="drain-leaked-fd-negative" {pristine_memory_owner();let b=acquire_support_invite_startup_bridge().unwrap();let fd=b.raw_fd();let expected=lease_metadata();assert_eq!(lease_fds(&expected),vec![fd]);let extra=unsafe{fcntl(fd,0,256)};assert!(extra>=256);let duplicate=unsafe{std::fs::File::from_raw_fd(extra)};let matches=lease_fds(&expected);assert_eq!(matches.len(),2);assert!(matches.contains(&fd)&&matches.contains(&extra));drop(b);if mode=="drain-leaked-fd-negative"{drain_closed_bridge(fd,&expected);panic!("leaked descriptor accepted");}assert_eq!(lease_fds(&expected),vec![extra]);drop(duplicate);drain_closed_bridge(fd,&expected);println!("fullOsDescriptorBoundCoversHighOwnedDuplicate=true");return;}\n if mode=="child" {std::fs::write(signals.join("exec-started"),b"started").unwrap();wait(&signals.join("allow-adopt"));let fd=std::env::var("MIXEL_BRIDGE_FD").unwrap().parse().unwrap();let b=unsafe { adopt_support_invite_startup_bridge(fd) }.unwrap();assert!(b.current().unwrap());std::fs::write(signals.join("bridge-ready"),b"ready").unwrap();wait(&signals.join("foreground"));assert!(hold_support_invite_attended_lease());drop(b);assert!(active());std::fs::write(signals.join("foreground-ready"),b"ready").unwrap();wait(&signals.join("exit"));return;}\n if mode=="handoff"||mode=="parent-exit" {let b=acquire_support_invite_startup_bridge().unwrap();let fd=b.raw_fd();let mut command=std::process::Command::new(std::env::current_exe().unwrap());command.arg("child").env("MIXEL_BRIDGE_FD",fd.to_string()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());unsafe{command.pre_exec(move||{if fcntl(fd,2,0)<0{Err(std::io::Error::last_os_error())}else{Ok(())}});}let mut child=OwnedChild(command.spawn().unwrap());std::fs::write(signals.join("child-pid"),child.0.id().to_string()).unwrap();wait(&signals.join("exec-started"));\n  if mode=="parent-exit" {std::mem::forget(child);println!("parentExitsBeforeChildAdoption=true");return;}\n  std::fs::write(signals.join("allow-adopt"),b"go").unwrap();wait(&signals.join("bridge-ready"));drop(b);assert!(active());std::fs::write(signals.join("foreground"),b"go").unwrap();wait(&signals.join("foreground-ready"));assert!(active());std::fs::write(signals.join("exit"),b"go").unwrap();assert!(child.0.wait().unwrap().success());assert!(!active());pristine_memory_owner();println!("scopedParentToInheritedToForegroundToExitNoGap=true");return;\n }\n panic!("unknown mode");\n}\n'

def real_guard_controls(layer,out):
    # Exact committed guard plus only the new Mac scoped API; production asset
    # and all foreground/heartbeat/Windows bodies remain unchanged.
    original=(SCRIPTS/'support-invite-guard.rs').read_bytes().replace(b'\r\n',b'\n').decode()
    text=original
    for before,after in layer.CHANGES['libs/hbb_common/src/password_security.rs']:
        assert text.count(before)==1;text=text.replace(before,after,1)
    text=text.split('#[cfg(test)]\nmod mixel_support_invite_tests',1)[0]
    assert text.count('mod mixel_support_lease {')==1
    text=text.replace('mod mixel_support_lease {','mod mixel_support_lease {\n    pub(super) fn fixture_owner_facts()->(bool,i32) { (OWNER.lock().unwrap().is_some(), OWNER_ERROR.load(std::sync::atomic::Ordering::SeqCst)) }',1)
    assert text.count('const ROOT: &str = "/private/tmp";')==1
    root=out/'guard-api-endpoints';root.mkdir(mode=0o700)
    text=text.replace('const ROOT: &str = "/private/tmp";', 'const ROOT: &str = '+json.dumps(str(root))+';')
    source=out/'guard-api-lifecycle.rs';source.write_bytes(('#![allow(dead_code)]\n'+text+SCOPED_GUARD_DRIVER).encode())
    binary=out/'guard-api-lifecycle'
    run(rustc_command([os.environ.get('RUSTC_BIN','rustc'),'--edition=2021','--deny=warnings',str(source),'-o',str(binary)]),env={**os.environ,'MIXEL_AUDIT_ROOT':str(root)})
    # The inherited transfer API cannot be called through safe Rust.
    negative=out/'guard-unsafe-contract-negative.rs';negative.write_bytes(('#![allow(dead_code)]\n'+text+'fn main(){let _=adopt_support_invite_startup_bridge(3);}\n').encode())
    rejected=subprocess.run(rustc_command([os.environ.get('RUSTC_BIN','rustc'),'--edition=2021','--deny=warnings',str(negative),'-o',str(out/'guard-unsafe-contract-negative')]),capture_output=True)
    (out/'guard-unsafe-contract-negative.log').write_bytes(rejected.stdout+rejected.stderr)
    assert rejected.returncode and b'E0133' in rejected.stderr
    records=[]
    def invoke(mode,signals=None):
        signals=signals or out/('guard-signals-'+str(len(records))+'-'+mode);signals.mkdir(mode=0o700,exist_ok=True)
        result=run([str(binary),mode],env={**os.environ,'MIXEL_SIGNAL_ROOT':str(signals)},timeout=8)
        (out/('guard-native-'+str(len(records))+'-'+mode+'.log')).write_bytes(result.stdout)
        records.append({'mode':mode,'exit':result.returncode,'safe_output':result.stdout.decode()});return signals
    for mode in ('scope','probe','foreground-survives','probe','foreign-fd','spawn-failed','high-fd-scan','foreground-alias','probe','probe','handoff','probe'):invoke(mode)
    negative_signals=out/'guard-signals-owned-leak-negative';negative_signals.mkdir(mode=0o700)
    leaked=subprocess.run([str(binary),'drain-leaked-fd-negative'],capture_output=True,env={**os.environ,'MIXEL_SIGNAL_ROOT':str(negative_signals)},timeout=8)
    (out/'guard-owned-descriptor-leak-negative.log').write_bytes(leaked.stdout+leaked.stderr)
    assert leaked.returncode and b'parent retains owned lease descriptor' in leaked.stderr
    signals=invoke('parent-exit');assert (signals/'child-pid').exists()
    try:
        invoke('probe',signals);assert records[-1]['safe_output']=='kernelActive=true\n'
        (signals/'allow-adopt').write_bytes(b'go')
        deadline=__import__('time').monotonic()+3
        while not (signals/'bridge-ready').exists():assert __import__('time').monotonic()<deadline;__import__('time').sleep(.005)
        (signals/'foreground').write_bytes(b'go')
        while not (signals/'foreground-ready').exists():assert __import__('time').monotonic()<deadline;__import__('time').sleep(.005)
        invoke('probe',signals);assert records[-1]['safe_output']=='kernelActive=true\n'
    finally:(signals/'exit').write_bytes(b'go')
    deadline=__import__('time').monotonic()+5
    while True:
        result=subprocess.run([str(binary),'probe'],capture_output=True,env={**os.environ,'MIXEL_SIGNAL_ROOT':str(signals)},timeout=3)
        if result.stdout==b'kernelActive=false\n':break
        assert __import__('time').monotonic()<deadline;__import__('time').sleep(.01)
    records.append({'mode':'parent-exit-child-exit-probe','exit':result.returncode,'safe_output':result.stdout.decode()})
    (out/'guard-native-lifecycle.json').write_bytes((json.dumps(records,sort_keys=True,indent=2)+'\n').encode())
    print('PASS '+str(len(records))+' literal realguard native lifecycle commands + unsafe-transfer compiler refusal; no OWNER/memory/error writes, child overlaps through actual parent exit')

def checked_launch_asset(layer, raw):
    # Match the unchanged production reader: homogeneous LF/CRLF canonical
    # content only; malformed/mixed/drift resource bytes still refuse.
    logical,_=layer.prior('patch-support-compiler-cleanup.py')['canonical'](raw)
    canonical=logical.encode()
    if hashlib.sha256(canonical).hexdigest()!=layer.ASSET_SHA:
        raise RuntimeError('Changed launch source resource')
    return canonical

def launch_asset_controls(layer, raw):
    canonical=checked_launch_asset(layer,raw)
    assert b'\r' not in canonical
    for ending in (b'\n',b'\r\n'):
        profile=canonical.replace(b'\n',ending)
        assert checked_launch_asset(layer,profile)==canonical
        poisoned=(profile+b'// owned resource drift'+ending,
                  profile.replace(ending,b'\r',1),
                  profile.replace(ending,b'\r\n' if ending==b'\n' else b'\n',1))
        for invalid in poisoned:
            try:checked_launch_asset(layer,invalid)
            except (RuntimeError,UnicodeError,SystemExit):pass
            else:raise AssertionError('Changed or malformed launch asset accepted')
    print('PASS exact launch resource canonical LF/CRLF positives and6 malformed/mixed/hash-drift refusals')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--source',type=Path);parser.add_argument('--source-only',action='store_true');args=parser.parse_args();args.output=args.output.resolve()
    args.output.mkdir(parents=True,exist_ok=False)
    p=args.output/'source';p.mkdir()
    names=('src/server.rs','src/ipc.rs','src/flutter.rs','src/ui_interface.rs','src/common.rs','src/core_main.rs','flutter/lib/common.dart','flutter/lib/models/native_model.dart','flutter/lib/desktop/pages/desktop_home_page.dart')
    spec=importlib.util.spec_from_file_location("mixel_final_launch",SCRIPTS/'patch-support-macos-gui-launch.py');layer=importlib.util.module_from_spec(spec);spec.loader.exec_module(layer)
    names=tuple(layer.ORIGINAL)
    if args.source:
        source_guards(layer,args.source.resolve(),args.output)
        # Historical final-L input is restore-only. The exact read-only
        # projection supplies the unchanged prior profile before new generation.
        with layer.previous(args.source.resolve()) as previous:
            for name in names:
                raw=(previous/name).read_bytes();logical=raw.replace(b"\r\n",b"\n").decode()
                dst=p/name;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(layer.transform(name,logical).encode())
        first={name:hashlib.sha256((p/name).read_bytes()).hexdigest() for name in names}
        assert first==layer.FINAL
        generated=(p/'src/ipc.rs').read_text()
    else:
        (p/'src').mkdir()
        (p/'src/mixel_support_url.rs').write_bytes(layer.CHANGES['src/mixel_support_url.rs'][0][1].encode())
        generated='\n'.join(after for _,after in layer.CHANGES['src/ipc.rs'])
        first={'src/mixel_support_url.rs':hashlib.sha256((p/'src/mixel_support_url.rs').read_bytes()).hexdigest()}
        assert first['src/mixel_support_url.rs']==layer.FINAL['src/mixel_support_url.rs']
    resource=(SCRIPTS/'support-macos-gui-launch.rs').read_bytes()
    launch_asset_controls(layer,resource)
    (p/'src/mixel_gui_launch.rs').write_bytes(checked_launch_asset(layer,resource))
    first['src/mixel_gui_launch.rs']=hashlib.sha256((p/'src/mixel_gui_launch.rs').read_bytes()).hexdigest()
    assert first['src/mixel_gui_launch.rs']==layer.ASSET_SHA
    start=generated.index('async fn send_mixel_gui_intent_('); end=generated.index('#[cfg(all(target_os = "macos", feature = "flutter"))]\nasync fn connect_mixel_gui_peer()',start)
    transport=generated[start:end]
    guard=(SCRIPTS/'support-invite-guard.rs').read_bytes().replace(b'\r\n',b'\n').decode()
    assert hashlib.sha256(guard.encode()).hexdigest()=='4b48b7995b36cc9662958ef8753be70ef0b6cf35626b7264f0151bc553293f29'
    for before,after in layer.CHANGES['libs/hbb_common/src/password_security.rs']:
        assert guard.count(before)==1;guard=guard.replace(before,after,1)
    guard=guard.split('#[cfg(test)]\nmod mixel_support_invite_tests',1)[0]
    assert guard.count('const ROOT: &str = "/private/tmp";')==1
    owned_guard_root=args.output/'real-attended-namespace';owned_guard_root.mkdir(mode=0o700)
    guard=guard.replace('const ROOT: &str = "/private/tmp";', 'const ROOT: &str = '+json.dumps(str(owned_guard_root))+';')
    # Read-only fixture observation of actual OWNER, not a replacement boolean.
    hook='    static OWNER: Mutex<Option<Lease>> = Mutex::new(None);'
    assert guard.count(hook)==1
    guard=guard.replace(hook,hook+'\n    pub(super) fn fixture_owner_held()->bool { OWNER.lock().unwrap_or_else(|error|error.into_inner()).is_some() }')
    guard+='\npub fn fixture_owner_held()->bool { mixel_support_lease::fixture_owner_held() }\n'
    guard_path=args.output/'literal-real-guard.rs';guard_path.write_bytes(guard.encode())
    fixture=r'''
extern crate self as hbb_common;
#[macro_export] macro_rules! fixed_info { ($($value:tt)*) => {{ let _ = format_args!($($value)*); }}; }
pub mod log { pub(crate) use crate::fixed_info as info; }
pub mod password_security {
 #[allow(dead_code)] mod real { include!("REAL_GUARD"); }
 pub use real::SupportInviteStartupBridge;
 pub fn hold_support_invite_attended_lease()->bool {super::LEASE_CALLS.fetch_add(1,std::sync::atomic::Ordering::SeqCst);real::hold_support_invite_attended_lease()}
 pub fn acquire_support_invite_startup_bridge()->std::io::Result<SupportInviteStartupBridge> {
  super::LEASE_CALLS.fetch_add(1,std::sync::atomic::Ordering::SeqCst);let mut blocked=super::LEASE_BLOCK.lock().unwrap();while *blocked {blocked=super::LEASE_EVENT.wait(blocked).unwrap();}drop(blocked);
  std::thread::sleep(std::time::Duration::from_millis(super::LEASE_DELAY.load(std::sync::atomic::Ordering::SeqCst)));
  if !super::LEASE_OK.load(std::sync::atomic::Ordering::SeqCst){return Err(std::io::Error::new(std::io::ErrorKind::PermissionDenied,"synthetic bridge refusal"))}
  real::acquire_support_invite_startup_bridge()
 }
 pub unsafe fn adopt_support_invite_startup_bridge(fd:i32)->std::io::Result<SupportInviteStartupBridge> {unsafe{real::adopt_support_invite_startup_bridge(fd)}}
 pub fn actual_kernel_active()->bool {real::support_invite_lease_status().contains("kernel=active")}
 pub fn actual_parent_not_owner(){let status=real::support_invite_lease_status();assert!(!real::fixture_owner_held());assert!(status.contains("ownerFailed=false"));assert!(status.contains("memoryActive=false"));}
 pub fn actual_parent_absent(){actual_parent_not_owner();let status=real::support_invite_lease_status();assert!(status.contains("kernel=absent"),"{status}");assert!(!real::support_invite_requires_click());}
}
use std::sync::{Arc,Mutex};
use std::future::Future;
use std::task::{Context,Poll,Wake,Waker};
mod server { #[path="QUEUE"] pub(crate) mod mixel_support_url; #[path="LAUNCH"] pub(crate) mod mixel_gui_launch; }
type Handoff = server::mixel_support_url::Handoff;
use server::mixel_gui_launch::{Pending,may_spawn};
#[derive(Clone)] enum Data { UrlLink(String),Config((String,Option<String>)), Other }
type Error=Box<dyn std::error::Error + Send + Sync>;
type ResultType<T>=Result<T,Error>;
static MODE:Mutex<&str>=Mutex::new("accepted");
static SPAWNS:Mutex<usize>=Mutex::new(0);
static CHILD_READY:Mutex<bool>=Mutex::new(false);
static DELIVERED:Mutex<Vec<String>>=Mutex::new(Vec::new());
static EXPECT_ORDINARY:std::sync::atomic::AtomicBool=std::sync::atomic::AtomicBool::new(false);
static LEASE_DELAY:std::sync::atomic::AtomicU64=std::sync::atomic::AtomicU64::new(0);
static LEASE_BLOCK:std::sync::Mutex<bool>=std::sync::Mutex::new(false);
static LEASE_EVENT:std::sync::Condvar=std::sync::Condvar::new();
static LEASE_CALLS:std::sync::atomic::AtomicUsize=std::sync::atomic::AtomicUsize::new(0);
static LEASE_OK:std::sync::atomic::AtomicBool=std::sync::atomic::AtomicBool::new(true);
static PANICKED:std::sync::atomic::AtomicBool=std::sync::atomic::AtomicBool::new(false);
static BRIDGE_PHASE:std::sync::atomic::AtomicUsize=std::sync::atomic::AtomicUsize::new(0);
static PHASE_READY:std::sync::atomic::AtomicUsize=std::sync::atomic::AtomicUsize::new(0);
static PHASE_RELEASE:Mutex<usize>=Mutex::new(0);
static PHASE_EVENT:std::sync::Condvar=std::sync::Condvar::new();
fn phase_pause(code:usize){PHASE_READY.store(code,std::sync::atomic::Ordering::SeqCst);let mut release=PHASE_RELEASE.lock().unwrap();while *release!=code{release=PHASE_EVENT.wait(release).unwrap();}}
fn release_phase(){*PHASE_RELEASE.lock().unwrap()=PHASE_READY.load(std::sync::atomic::Ordering::SeqCst);PHASE_EVENT.notify_all();}
fn wait_phase(code:usize){let end=std::time::Instant::now()+std::time::Duration::from_secs(3);while PHASE_READY.load(std::sync::atomic::Ordering::SeqCst)!=code{assert!(std::time::Instant::now()<end);std::thread::sleep(std::time::Duration::from_millis(5));}}
fn fixture_poll(){if BRIDGE_PHASE.load(std::sync::atomic::Ordering::SeqCst)==1{phase_pause(1);}std::thread::sleep(std::time::Duration::from_millis(100));}

struct Connection;
async fn connect_mixel_gui_peer()->Result<Connection,Handoff> { let (ms,postfix)=(1000,"_url");
 assert_eq!((ms,postfix),(1000,"_url"));
 match *MODE.lock().unwrap() {
  "not-found"=>Err(Handoff::NoReceiver),
  "refused"=>Err(Handoff::NoReceiver),
  "permission"=>Err(Handoff::ExistingNeedsRetry),
  "unknown"|"peer-not-found"|"peer-refused"=>Err(Handoff::ExistingNeedsRetry),
  _=>Ok(Connection),
 }
}
impl Connection {
 async fn send(&mut self,data:&Data)->ResultType<()> { let Data::UrlLink(url)=data else { panic!("wrong frame") };assert!(url.is_empty()||server::mixel_support_url::is_valid_support_url(url)); if *MODE.lock().unwrap()=="send-failed" {Err("send-failed".into())} else {Ok(())} }
 async fn next_timeout(&mut self,ms:u64)->ResultType<Option<Data>> {
  assert_eq!(ms,2000);
  let m=*MODE.lock().unwrap();if m=="reply-error" {return Err("reply-error".into())}
  Ok(match m { "missing"=>None,"wrong-type"=>Some(Data::Other),"wrong-name"=>Some(Data::Config(("wrong".into(),Some("accepted".into())))),"wrong-value"=>Some(Data::Config(("mixel-support-url-ack-v1".into(),Some("unknown".into())))),"superseded"=>Some(Data::Config(("mixel-support-url-ack-v1".into(),Some("superseded".into())))),"rejected"|"busy"|"expired"=>Some(Data::Config(("mixel-support-url-ack-v1".into(),Some("unavailable".into())))),_=>Some(Data::Config(("mixel-support-url-ack-v1".into(),Some("accepted".into())))) })
 }
}
async fn timeout<T>(ms:u64,future:impl Future<Output=T>)->Result<T,Error> {assert_eq!(ms,4000);if *MODE.lock().unwrap()=="timeout" {Err("timeout".into())}else{Ok(future.await)}}
TRANSPORT
struct Noop;impl Wake for Noop {fn wake(self:Arc<Self>) {}}
fn poll<T>(future:impl Future<Output=T>)->T {let w=Waker::from(Arc::new(Noop));let mut c=Context::from_waker(&w);let mut f=Box::pin(future);loop{if let Poll::Ready(v)=f.as_mut().poll(&mut c){return v}}}
const FIRST:&str="mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000001&apikey=synthetic-first-key-000000000000";
const SECOND:&str="mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000002&apikey=synthetic-second-key-000000000000";
mod ipc {
 pub(crate) fn send_mixel_gui_intent(url:String)->super::Handoff {
  if let Some(root)=std::env::var_os("MIXEL_GATE_CHILD_ROOT") { if std::path::Path::new(&root).join("listener-ready").exists() { return super::Handoff::Accepted; } }
  let phase=super::BRIDGE_PHASE.load(std::sync::atomic::Ordering::SeqCst);
  if phase==2 && url==super::FIRST {super::phase_pause(2);}
  if phase==3 && url==super::SECOND {super::phase_pause(3);}
  let mode=*super::MODE.lock().unwrap();
  if mode=="panic-after-spawn" && *super::SPAWNS.lock().unwrap()>0 {super::PANICKED.store(true,std::sync::atomic::Ordering::SeqCst);panic!("owned test panic after child retained")}
  if *super::CHILD_READY.lock().unwrap(){super::DELIVERED.lock().unwrap().push(url);return super::Handoff::Accepted}
  super::poll(super::send_mixel_gui_intent_(url))
 }
}
fn test_command()->std::io::Result<std::process::Command> {
 *SPAWNS.lock().unwrap()+=1;
 if BRIDGE_PHASE.load(std::sync::atomic::Ordering::SeqCst)==4 {phase_pause(4);return Ok(std::process::Command::new(std::path::PathBuf::from(std::env::var_os("MIXEL_TEST_ROOT").unwrap()).join("nonexistent-owned-child")));}
 let mut command=std::process::Command::new(std::env::current_exe()?);
 command.args(["--exact","owned_sleep_child","--nocapture"]).env("MIXEL_CHILD_SLEEP","1").env("MIXEL_EXPECT_ATTENDED",if EXPECT_ORDINARY.load(std::sync::atomic::Ordering::SeqCst){"0"}else{"1"});Ok(command)
}
fn worker_gate_marker(path:&std::path::Path,uid:u32)->std::io::Result<bool>{
 use std::os::unix::fs::MetadataExt;
 match std::fs::symlink_metadata(path){
  Ok(meta) if meta.is_dir()&&meta.uid()==uid&&meta.mode()&0o7777==0o700=>Ok(true),
  Ok(_)=>Err(std::io::ErrorKind::PermissionDenied.into()),
  Err(error) if error.kind()==std::io::ErrorKind::NotFound=>Ok(false),
  Err(error)=>Err(error),
 }
}
#[test]fn owned_sleep_child(){if std::env::var_os("MIXEL_CHILD_SLEEP").is_some(){
 assert!(server::mixel_gui_launch::adopt_inherited().unwrap());assert_eq!(LEASE_CALLS.load(std::sync::atomic::Ordering::SeqCst),usize::from(std::env::var("MIXEL_EXPECT_ATTENDED").ok().as_deref()==Some("1")));
 if let Some(marker)=std::env::var_os("MIXEL_WORKER_CHILD_GATE"){
  use std::os::unix::fs::{DirBuilderExt,MetadataExt};assert_eq!(marker,"1");
  let root=std::path::PathBuf::from(std::env::var_os("MIXEL_TEST_ROOT").unwrap());let uid=std::fs::symlink_metadata(&root).unwrap().uid();let gates=root.join("worker-child-gates");assert!(worker_gate_marker(&gates,uid).unwrap());
  std::fs::DirBuilder::new().mode(0o700).create(gates.join("adopted")).unwrap();
  let deadline=std::time::Instant::now()+std::time::Duration::from_secs(2);
  while !worker_gate_marker(&gates.join("release"),uid).unwrap(){assert!(std::time::Instant::now()<deadline,"owned worker child release marker timeout");std::thread::sleep(std::time::Duration::from_millis(5));}
  assert!(std::time::Instant::now()<deadline,"owned worker child release marker arrived after deadline");
 }else{std::thread::sleep(std::time::Duration::from_millis(700));}
}}
#[test]fn typed_transport_only_known_absence_authorizes_spawn() {
 for mode in ["not-found","refused"] {*MODE.lock().unwrap()=mode;assert_eq!(poll(send_mixel_gui_intent_(FIRST.into())),Handoff::NoReceiver)}
 for mode in ["permission","unknown","peer-not-found","peer-refused","send-failed","reply-error","missing","wrong-type","wrong-name","wrong-value","rejected","busy","expired","timeout"] {*MODE.lock().unwrap()=mode;let result=poll(send_mixel_gui_intent_(FIRST.into()));assert_eq!(result,Handoff::ExistingNeedsRetry);assert!(!may_spawn(result,false))}
 *MODE.lock().unwrap()="superseded";assert_eq!(poll(send_mixel_gui_intent_(FIRST.into())),Handoff::Superseded);
 *MODE.lock().unwrap()="accepted";assert_eq!(poll(send_mixel_gui_intent_(FIRST.into())),Handoff::Accepted);
 for kind in [std::io::ErrorKind::PermissionDenied,std::io::ErrorKind::TimedOut,std::io::ErrorKind::WouldBlock,std::io::ErrorKind::InvalidData]{assert!(!server::mixel_support_url::transport_absent(Some(kind)))}
 assert!(!server::mixel_support_url::transport_absent(None));assert!(!may_spawn(Handoff::NoReceiver,true));
}
#[test]fn latest_intent_and_ordinary_open_cannot_resurrect_superseded_or_erase_latest(){
 let mut p=Pending::new();assert!(p.offer(FIRST.into()));let first=p.current().unwrap().0;
 assert!(p.offer(String::new()));assert!(p.offer(SECOND.into()));let second=p.current().unwrap().0;
 p.complete(first,Handoff::Accepted);assert_eq!(p.current().unwrap().1,SECOND);
 p.complete(first,Handoff::Superseded);assert_eq!(p.current().unwrap().1,SECOND);
 p.complete(second,Handoff::ExistingNeedsRetry);assert_eq!(p.current().unwrap().1,SECOND);
 assert!(!p.offer("mixel-remote://support/?invalid".into()));assert_eq!(p.current().unwrap().1,SECOND);
 p.complete(second,Handoff::Accepted);assert!(p.current().is_none());assert!(p.offer(String::new()));assert!(p.current().unwrap().0>second);
 let ordinary=p.current().unwrap().0;assert!(p.offer(FIRST.into()));let a=p.current().unwrap().0;assert!(p.offer(SECOND.into()));let b=p.current().unwrap().0;
 p.discard(a);assert_eq!(p.current().unwrap().1,SECOND);p.discard(b);assert_eq!(p.current().unwrap().0,ordinary);p.discard(ordinary);assert!(p.current().is_none());p.discard(a);assert!(p.current().is_none());
}
#[test]fn owner_gate_child(){
 if let Some(root)=std::env::var_os("MIXEL_GATE_CHILD_ROOT") {
  use server::mixel_gui_launch::reservation::Reservation;
  let r = if std::env::var_os("MIXEL_COLD_CHILD").is_some() {
   *MODE.lock().unwrap()="not-found";let outcome=server::mixel_gui_launch::cold_route(if std::env::var_os("MIXEL_EMPTY_CHILD").is_some(){String::new()}else{FIRST.into()});if std::env::var_os("MIXEL_CONTENDER_CHILD").is_some(){assert!(!outcome);std::fs::write(std::path::Path::new(&root).join("contender-done"),b"no-new-gui").unwrap();return;}assert!(outcome);None
  } else {Some(Reservation::acquire(std::path::Path::new(&root)).unwrap())};
  if let Some(r)=r.as_ref(){assert!(r.current().unwrap());}
  std::fs::write(std::path::Path::new(&root).join("child-ready"),b"ready").unwrap();
  std::thread::sleep(std::time::Duration::from_secs(10));drop(r);
 }
}
#[test]fn real_owned_flock_child_crash_releases_and_unsafe_sources_refuse(){
 use std::os::unix::fs::{PermissionsExt,MetadataExt};use server::mixel_gui_launch::reservation::Reservation;
 let base=std::path::PathBuf::from(std::env::var_os("MIXEL_TEST_ROOT").unwrap()).join("native-gate");std::fs::create_dir(&base).unwrap();
 let mut child=std::process::Command::new(std::env::current_exe().unwrap()).args(["--exact","owner_gate_child","--nocapture"]).env("MIXEL_GATE_CHILD_ROOT",&base).spawn().unwrap();
 let deadline=std::time::Instant::now()+std::time::Duration::from_secs(3);while !base.join("child-ready").exists(){assert!(std::time::Instant::now()<deadline);std::thread::sleep(std::time::Duration::from_millis(5));}
 assert_eq!(Reservation::acquire(&base).err().unwrap().kind(),std::io::ErrorKind::WouldBlock);child.kill().unwrap();child.wait().unwrap();
 let held=Reservation::acquire(&base).unwrap();assert!(held.current().unwrap());assert_eq!(Reservation::acquire(&base).err().unwrap().kind(),std::io::ErrorKind::WouldBlock);
 let owner=std::fs::read_dir(&base).unwrap().map(|x|x.unwrap().path()).find(|x|x.is_dir()).unwrap();let endpoint=owner.join("launch");
 std::fs::rename(&endpoint,owner.join("old")).unwrap();assert!(held.current().is_err());assert!(!endpoint.exists(),"validation must not create a missing endpoint");std::fs::write(&endpoint,[]).unwrap();std::fs::set_permissions(&endpoint,std::fs::Permissions::from_mode(0o600)).unwrap();assert!(!held.current().unwrap());drop(held);
 let r=Reservation::acquire(&base).unwrap();drop(r);
 std::fs::set_permissions(&endpoint,std::fs::Permissions::from_mode(0o644)).unwrap();assert!(Reservation::acquire(&base).is_err());std::fs::set_permissions(&endpoint,std::fs::Permissions::from_mode(0o600)).unwrap();
 std::fs::hard_link(&endpoint,owner.join("hard")).unwrap();assert!(Reservation::acquire(&base).is_err());std::fs::remove_file(owner.join("hard")).unwrap();
 std::fs::remove_file(&endpoint).unwrap();std::os::unix::fs::symlink(owner.join("old"),&endpoint).unwrap();assert!(Reservation::acquire(&base).is_err());std::fs::remove_file(&endpoint).unwrap();
 std::fs::set_permissions(&owner,std::fs::Permissions::from_mode(0o755)).unwrap();assert!(Reservation::acquire(&base).is_err());std::fs::set_permissions(&owner,std::fs::Permissions::from_mode(0o700)).unwrap();
 let r=Reservation::acquire(&base).unwrap();let uid=endpoint.metadata().unwrap().uid();assert!(uid>0);std::fs::rename(&owner,base.join("previous-owner")).unwrap();std::fs::create_dir(&owner).unwrap();std::fs::set_permissions(&owner,std::fs::Permissions::from_mode(0o700)).unwrap();assert!(!r.current().unwrap());drop(r);
}
'''
    # Tests exercise the actual native reservation without touching /private/tmp.
    launch=(p/'src/mixel_gui_launch.rs').read_text()
    old='fn acquire() -> std::io::Result<Reservation> { Reservation::acquire(std::path::Path::new("/private/tmp")) }'
    assert launch.count(old)==1
    instrumented=launch.replace('std::env::current_exe().map(std::process::Command::new)','crate::test_command()').replace('Reservation::adopt(std::path::Path::new("/private/tmp"), fd)','Reservation::adopt(std::path::Path::new(&std::env::var_os("MIXEL_TEST_ROOT").unwrap()), fd)').replace(old,'fn acquire() -> std::io::Result<Reservation> { Reservation::acquire(std::path::Path::new(&std::env::var_os("MIXEL_TEST_ROOT").unwrap())) }')
    # Only a bounded scheduler barrier replaces the worker's final poll sleep.
    poll_anchor='            // The owned child and fd stay alive after delivery/startup timeout.\n            // Pending storage remains one newest intent, retried while it starts.\n            std::thread::sleep(POLL);'
    assert instrumented.count(poll_anchor)==1
    instrumented=instrumented.replace(poll_anchor,poll_anchor.replace('std::thread::sleep(POLL);','crate::fixture_poll();'))
    (args.output/'launch-fixture.rs').write_bytes(instrumented.encode())
    fixture=fixture.replace('REAL_GUARD',str(guard_path)).replace('QUEUE',str(p/'src/mixel_support_url.rs')).replace('LAUNCH',str(args.output/'launch-fixture.rs')).replace('TRANSPORT',transport)
    # The compiled wrapper consumes exact production transport, queue, Pending,
    # spawn predicate and reservation bodies. Runtime mocks are explicit.
    fixture+='''
#[test]fn queue_contract_and_cold_accepted_route_preserved(){
 use server::mixel_support_url::*;
 assert_eq!((MAX_URL_BYTES,MAX_ACK_WORKERS,PENDING_TTL.as_millis()),(16384,8,1500));assert_eq!(ACK_REJECTED,"unavailable");assert_eq!(READY_OPTION,"mixel-support-url-callback-ready-v1");
 let mut q=Queue::new();let gen="11111111111111111111111111111111";assert!(q.begin_stream(gen));assert!(q.has_stream());assert!(!q.ordinary_ready());assert!(q.set_ready(&format!("Y:{gen}:1")));assert_eq!(stream_generation(&format!("main,{STREAM_PREFIX}{gen}")),Some(gen));
 assert!(q.ordinary_ready());let now=std::time::Instant::now();let (id,r)=q.offer(FIRST.into(),now).unwrap();assert_eq!(q.flush(now,|_|Some(false)),Some(Phase::RejectedSink));q.cancel(id);assert_eq!(r.try_recv().unwrap(),Delivery::Expired);
 let (_,r)=q.offer(SECOND.into(),now).unwrap();assert_eq!(q.flush(now,|_|Some(true)),Some(Phase::Accepted));assert_eq!(r.try_recv().unwrap(),Delivery::Accepted);assert!(q.stop_if_generation(gen));assert!(!q.has_stream());global_queue().lock().unwrap().stop();
 *MODE.lock().unwrap()="accepted";assert!(!server::mixel_gui_launch::cold_route(FIRST.into()));assert!(!server::mixel_gui_launch::cold_route("invalid".into()));password_security::actual_parent_absent();
}
\n#[test]fn actual_worker_rapid_urls_and_empty_open_single_child_until_exit(){
 use std::os::unix::fs::{DirBuilderExt,MetadataExt};
 let root=std::path::PathBuf::from(std::env::var_os("MIXEL_TEST_ROOT").unwrap());let uid=std::fs::symlink_metadata(&root).unwrap().uid();let gates=root.join("worker-child-gates");std::fs::DirBuilder::new().mode(0o700).create(&gates).unwrap();
 struct OwnedChildGate{path:Option<std::path::PathBuf>}
 impl Drop for OwnedChildGate{fn drop(&mut self){std::env::remove_var("MIXEL_WORKER_CHILD_GATE");if let Some(path)=self.path.as_ref(){let _=std::fs::DirBuilder::new().mode(0o700).create(path.join("release"));}}}
 let mut gate=OwnedChildGate{path:Some(gates.clone())};std::env::set_var("MIXEL_WORKER_CHILD_GATE","1");
 *MODE.lock().unwrap()="not-found";*CHILD_READY.lock().unwrap()=false;
 server::mixel_gui_launch::submit(FIRST.into());server::mixel_gui_launch::submit(String::new());server::mixel_gui_launch::submit(SECOND.into());
 let deadline=std::time::Instant::now()+std::time::Duration::from_secs(2);
 while *SPAWNS.lock().unwrap()==0 {assert!(std::time::Instant::now()<deadline);std::thread::sleep(std::time::Duration::from_millis(10));}
 while !worker_gate_marker(&gates.join("adopted"),uid).unwrap(){assert!(std::time::Instant::now()<deadline,"owned worker child did not adopt within original case budget");std::thread::sleep(std::time::Duration::from_millis(5));}
 assert!(std::time::Instant::now()<deadline,"owned worker child adopted after original case budget");
 std::thread::sleep(std::time::Duration::from_millis(100));assert_eq!(*SPAWNS.lock().unwrap(),1);
 *MODE.lock().unwrap()="panic-after-spawn";
 while !PANICKED.load(std::sync::atomic::Ordering::SeqCst){assert!(std::time::Instant::now()<deadline);std::thread::sleep(std::time::Duration::from_millis(5));}
 std::thread::sleep(std::time::Duration::from_millis(50));
 assert_eq!(server::mixel_gui_launch::reservation::Reservation::acquire(std::path::Path::new(&std::env::var_os("MIXEL_TEST_ROOT").unwrap())).err().unwrap().kind(),std::io::ErrorKind::WouldBlock);
 *MODE.lock().unwrap()="accepted";*CHILD_READY.lock().unwrap()=true;server::mixel_gui_launch::submit(SECOND.into());std::thread::sleep(std::time::Duration::from_millis(100));
 assert_eq!(*SPAWNS.lock().unwrap(),1);assert_eq!(DELIVERED.lock().unwrap().as_slice(),[SECOND.to_owned()]);password_security::actual_parent_not_owner();assert_eq!(server::mixel_gui_launch::reservation::Reservation::acquire(std::path::Path::new(&std::env::var_os("MIXEL_TEST_ROOT").unwrap())).err().unwrap().kind(),std::io::ErrorKind::WouldBlock);
 std::fs::DirBuilder::new().mode(0o700).create(gates.join("release")).unwrap();std::env::remove_var("MIXEL_WORKER_CHILD_GATE");
 let r=loop{
  assert!(std::time::Instant::now()<deadline,"owned worker child reservation did not release within original case budget");
  match server::mixel_gui_launch::reservation::Reservation::acquire(&root){
   Ok(owned)=>{assert!(std::time::Instant::now()<deadline,"owned worker child reservation released after original case budget");break owned;},
   Err(error) if error.kind()==std::io::ErrorKind::WouldBlock=>std::thread::sleep(std::time::Duration::from_millis(5)),
   Err(error)=>panic!("owned worker child reservation acquisition failed: {error}"),
  }
 };assert!(r.current().unwrap());drop(r);password_security::actual_parent_absent();
 for marker in ["adopted","release"]{std::fs::remove_dir(gates.join(marker)).unwrap();}std::fs::remove_dir(&gates).unwrap();gate.path=None;
 EXPECT_ORDINARY.store(true,std::sync::atomic::Ordering::SeqCst);*MODE.lock().unwrap()="not-found";*CHILD_READY.lock().unwrap()=false;let leases=LEASE_CALLS.load(std::sync::atomic::Ordering::SeqCst);
 server::mixel_gui_launch::submit(String::new());let deadline=std::time::Instant::now()+std::time::Duration::from_secs(2);while *SPAWNS.lock().unwrap()<2{assert!(std::time::Instant::now()<deadline);std::thread::sleep(std::time::Duration::from_millis(10));}
 assert_eq!(LEASE_CALLS.load(std::sync::atomic::Ordering::SeqCst),leases);*MODE.lock().unwrap()="accepted";*CHILD_READY.lock().unwrap()=true;std::thread::sleep(std::time::Duration::from_millis(900));EXPECT_ORDINARY.store(false,std::sync::atomic::Ordering::SeqCst);

}
'''
    fixture+='\n#[test]fn real_direct_cold_process_retains_reservation_until_process_exit(){\n use server::mixel_gui_launch::reservation::Reservation;\n let base=std::path::PathBuf::from(std::env::var_os("MIXEL_TEST_ROOT").unwrap()).join("direct-cold-gate");std::fs::create_dir(&base).unwrap();\n let mut child=std::process::Command::new(std::env::current_exe().unwrap()).args(["--exact","owner_gate_child","--nocapture"]).env("MIXEL_GATE_CHILD_ROOT",&base).env("MIXEL_TEST_ROOT",&base).env("MIXEL_COLD_CHILD","1").spawn().unwrap();\n let deadline=std::time::Instant::now()+std::time::Duration::from_secs(3);while !base.join("child-ready").exists(){assert!(std::time::Instant::now()<deadline);std::thread::sleep(std::time::Duration::from_millis(5));}\n assert_eq!(Reservation::acquire(&base).err().unwrap().kind(),std::io::ErrorKind::WouldBlock);child.kill().unwrap();child.wait().unwrap();let r=Reservation::acquire(&base).unwrap();assert!(r.current().unwrap());drop(r);\n}\n#[test]fn zz_attended_lease_failure_does_not_send_spawn_or_start_direct_gui(){\n LEASE_OK.store(false,std::sync::atomic::Ordering::SeqCst);let old=*SPAWNS.lock().unwrap();server::mixel_gui_launch::submit(SECOND.into());assert!(!server::mixel_gui_launch::cold_route(FIRST.into()));assert_eq!(*SPAWNS.lock().unwrap(),old);std::thread::sleep(std::time::Duration::from_millis(250));assert_eq!(*SPAWNS.lock().unwrap(),old);LEASE_OK.store(true,std::sync::atomic::Ordering::SeqCst);\n}\n'

    fixture+=r"""
#[test]fn actual_same_image_unix_peer_verifies_before_data_write(){
 use std::os::unix::io::AsRawFd;
 let (a,b)=std::os::unix::net::UnixStream::pair().unwrap();
 server::mixel_gui_launch::peer::verify(a.as_raw_fd()).unwrap();
 server::mixel_gui_launch::peer::verify(b.as_raw_fd()).unwrap();
 assert!(server::mixel_gui_launch::peer::verify(-1).is_err());
}
#[test]fn generation_ttl_discards_only_same_stale_intent(){
 let mut p=Pending::new();p.offer(FIRST.into());let (first,_)=p.current().unwrap();
 p.offer(SECOND.into());let (second,_)=p.current().unwrap();
 assert!(!p.expire(first,std::time::Instant::now()+std::time::Duration::from_secs(20)));
 assert_eq!(p.current().unwrap().1,SECOND);
 assert!(p.expire(second,std::time::Instant::now()+std::time::Duration::from_secs(20)));assert!(p.current().is_none());
 p.offer(String::new());let id=p.current().unwrap().0;p.offer(String::new());assert_ne!(p.current().unwrap().0,id);
 assert!(!p.expire(id,std::time::Instant::now()+std::time::Duration::from_secs(20)));
}
#[test]fn inherited_child_gate(){
 if let Some(base)=std::env::var_os("MIXEL_INHERITED_CHILD_ROOT") {
  use std::os::unix::fs::MetadataExt;
  let root=std::path::Path::new(&base);let uid=std::fs::symlink_metadata(root).unwrap().uid();assert!(worker_gate_marker(root,uid).unwrap());
  let initial=std::env::var_os(server::mixel_gui_launch::reservation::INHERITED_FD).unwrap();
  let attended=std::env::var_os(server::mixel_gui_launch::reservation::ATTENDED_START).is_some();
  let old=LEASE_CALLS.load(std::sync::atomic::Ordering::SeqCst);
  let barrier=std::sync::Arc::new(std::sync::Barrier::new(8));
  let threads:Vec<_>=(0..8).map(|_|{let barrier=barrier.clone();std::thread::spawn(move||{barrier.wait();server::mixel_gui_launch::adopt_inherited()})}).collect();
  let results:Vec<_>=threads.into_iter().map(|thread|thread.join().unwrap().unwrap()).collect();
  assert_eq!(results.iter().filter(|value|**value).count(),1);
  assert_eq!(LEASE_CALLS.load(std::sync::atomic::Ordering::SeqCst)-old,usize::from(attended));
  assert!(std::env::var_os(server::mixel_gui_launch::reservation::ATTENDED_START).is_none());
  assert!(std::env::var_os(server::mixel_gui_launch::reservation::INHERITED_FD).is_none());
  std::env::set_var(server::mixel_gui_launch::reservation::INHERITED_FD,initial);assert!(server::mixel_gui_launch::adopt_inherited().is_err());
  assert_eq!(server::mixel_gui_launch::reservation::Reservation::acquire(std::path::Path::new(&base)).err().unwrap().kind(),std::io::ErrorKind::WouldBlock);
  std::fs::write(std::path::Path::new(&base).join("adopted"),b"ready").unwrap();
  let deadline=std::time::Instant::now()+std::time::Duration::from_secs(3);
  while !worker_gate_marker(&root.join("release"),uid).unwrap(){assert!(std::time::Instant::now()<deadline,"owned inherited child release marker timeout");std::thread::sleep(std::time::Duration::from_millis(5));}
  assert!(std::time::Instant::now()<deadline,"owned inherited child release marker arrived after deadline");
 }
}
#[test]fn spawning_parent_gate(){
 if let Some(base)=std::env::var_os("MIXEL_SPAWNING_PARENT_ROOT") {
  let owned=server::mixel_gui_launch::reservation::Reservation::acquire(std::path::Path::new(&base)).unwrap();
  let mut command=std::process::Command::new(std::env::current_exe().unwrap());
  command.args(["--exact","inherited_child_gate","--nocapture"]).env("MIXEL_INHERITED_CHILD_ROOT",&base).env("MIXEL_TEST_ROOT",&base).env_remove("MIXEL_SPAWNING_PARENT_ROOT");
  let child=owned.spawn(&mut command,true).unwrap();std::fs::write(std::path::Path::new(&base).join("child-pid"),child.id().to_string()).unwrap();
  // No normal drop: prove continuity when spawning parent crashes/exits abruptly.
  std::process::exit(0);
 }
}
#[test]fn inherited_description_survives_parent_exit_and_is_released_after_child_exit(){
 use server::mixel_gui_launch::reservation::Reservation;
 use std::os::unix::fs::DirBuilderExt;
 let base=std::path::PathBuf::from(std::env::var_os("MIXEL_TEST_ROOT").unwrap()).join("inherited-gate");std::fs::DirBuilder::new().mode(0o700).create(&base).unwrap();
 struct OwnedInheritedGate{path:Option<std::path::PathBuf>}
 impl Drop for OwnedInheritedGate{fn drop(&mut self){if let Some(path)=self.path.as_ref(){let _=std::fs::DirBuilder::new().mode(0o700).create(path.join("release"));}}}
 let mut gate=OwnedInheritedGate{path:Some(base.clone())};
 let mut parent=std::process::Command::new(std::env::current_exe().unwrap()).args(["--exact","spawning_parent_gate","--nocapture"]).env("MIXEL_SPAWNING_PARENT_ROOT",&base).spawn().unwrap();
 let deadline=std::time::Instant::now()+std::time::Duration::from_secs(3);
 while !base.join("adopted").exists(){assert!(std::time::Instant::now()<deadline);std::thread::sleep(std::time::Duration::from_millis(5));}
 assert!(parent.wait().unwrap().success());assert_eq!(Reservation::acquire(&base).err().unwrap().kind(),std::io::ErrorKind::WouldBlock);
 assert!(std::time::Instant::now()<deadline,"inherited child held proof completed after original case budget");
 std::fs::DirBuilder::new().mode(0o700).create(base.join("release")).unwrap();
 let owned=loop{
  assert!(std::time::Instant::now()<deadline,"inherited child reservation did not release within original case budget");
  match Reservation::acquire(&base){
   Ok(owned)=>{assert!(std::time::Instant::now()<deadline,"inherited child reservation released after original case budget");break owned;},
   Err(error) if error.kind()==std::io::ErrorKind::WouldBlock=>std::thread::sleep(std::time::Duration::from_millis(5)),
   Err(error)=>panic!("inherited child reservation acquisition failed: {error}"),
  }
 };
 assert!(owned.current().unwrap());drop(owned);
 std::fs::remove_dir(base.join("release")).unwrap();gate.path=None;
}
#[test]fn malformed_inherited_markers_fail_closed_without_closing_foreign_fd(){
 use server::mixel_gui_launch::reservation::{INHERITED_FD,ATTENDED_START};
 for invalid in ["", "0", "true", "2"] {std::env::set_var(ATTENDED_START,invalid);assert!(server::mixel_gui_launch::adopt_inherited().is_err());assert!(std::env::var_os(ATTENDED_START).is_none());}
 std::env::set_var(ATTENDED_START,"1");assert!(server::mixel_gui_launch::adopt_inherited().is_err());
 for invalid in ["","-1","0","2","2147483648","3x","9999999999"] {
  std::env::set_var(INHERITED_FD,invalid);assert!(server::mixel_gui_launch::adopt_inherited().is_err());assert!(std::env::var_os(INHERITED_FD).is_none());
 }
 use std::os::unix::io::AsRawFd;
 let file=std::fs::File::open(std::env::current_exe().unwrap()).unwrap();std::env::set_var(INHERITED_FD,file.as_raw_fd().to_string());
 assert!(server::mixel_gui_launch::adopt_inherited().is_err());assert!(file.metadata().is_ok());
}
"""

    fixture+=r"""
#[test]fn two_actual_cold_routes_empty_and_support_share_startup_then_ready_ack(){
 use server::mixel_gui_launch::reservation::Reservation;
 let base=std::path::PathBuf::from(std::env::var_os("MIXEL_TEST_ROOT").unwrap()).join("two-direct-routes");std::fs::create_dir(&base).unwrap();
 let mut first=std::process::Command::new(std::env::current_exe().unwrap()).args(["--exact","owner_gate_child","--nocapture"]).env("MIXEL_GATE_CHILD_ROOT",&base).env("MIXEL_TEST_ROOT",&base).env("MIXEL_COLD_CHILD","1").env("MIXEL_EMPTY_CHILD","1").spawn().unwrap();
 let deadline=std::time::Instant::now()+std::time::Duration::from_secs(4);while !base.join("child-ready").exists(){assert!(std::time::Instant::now()<deadline);std::thread::sleep(std::time::Duration::from_millis(5));}
 let mut second=std::process::Command::new(std::env::current_exe().unwrap()).args(["--exact","owner_gate_child","--nocapture"]).env("MIXEL_GATE_CHILD_ROOT",&base).env("MIXEL_TEST_ROOT",&base).env("MIXEL_COLD_CHILD","1").env("MIXEL_CONTENDER_CHILD","1").spawn().unwrap();
 std::thread::sleep(std::time::Duration::from_millis(150));assert!(!base.join("contender-done").exists());assert_eq!(Reservation::acquire(&base).err().unwrap().kind(),std::io::ErrorKind::WouldBlock);
 std::fs::write(base.join("listener-ready"),b"actual-mocked-ack-ready").unwrap();assert!(second.wait().unwrap().success());assert!(base.join("contender-done").exists());
 assert_eq!(Reservation::acquire(&base).err().unwrap().kind(),std::io::ErrorKind::WouldBlock);first.kill().unwrap();first.wait().unwrap();drop(Reservation::acquire(&base).unwrap());
}
#[test]fn actual_foreign_executable_unix_peer_is_refused_before_bearer_write(){
 use std::os::unix::io::AsRawFd;
 let base=std::path::PathBuf::from(std::env::var_os("MIXEL_TEST_ROOT").unwrap());let socket=std::path::PathBuf::from("foreign-peer");let ready=base.join("foreign-peer-ready");let count=base.join("foreign-peer-count");
 let code="import socket,sys,pathlib\ns=socket.socket(socket.AF_UNIX);s.settimeout(3.0);s.bind(sys.argv[1]);s.listen(1);pathlib.Path(sys.argv[2]).write_bytes(b'ready');c,_=s.accept();c.settimeout(0.4)\ntry: data=c.recv(16384)\nexcept TimeoutError: data=b''\npathlib.Path(sys.argv[3]).write_text(str(len(data)));c.close();s.close()";
 struct OwnedPeer<'a>(&'a mut std::process::Child);
 impl Drop for OwnedPeer<'_>{fn drop(&mut self){let _=self.0.kill();let _=self.0.wait();}}
 let mut child=std::process::Command::new("/usr/bin/python3").args(["-c",code]).arg(&socket).arg(&ready).arg(&count).current_dir(&base).spawn().unwrap();
 {
  let owned=OwnedPeer(&mut child);let deadline=std::time::Instant::now()+std::time::Duration::from_secs(3);
  while !ready.exists(){assert!(std::time::Instant::now()<deadline);std::thread::sleep(std::time::Duration::from_millis(5));}
  struct OwnedCwd(std::path::PathBuf);impl Drop for OwnedCwd{fn drop(&mut self){std::env::set_current_dir(&self.0).unwrap();}}
  let cwd=OwnedCwd(std::env::current_dir().unwrap());std::env::set_current_dir(&base).unwrap();let stream=std::os::unix::net::UnixStream::connect(&socket).unwrap();drop(cwd);assert!(server::mixel_gui_launch::peer::verify(stream.as_raw_fd()).is_err());drop(stream);
  assert!(owned.0.wait().unwrap().success());assert_eq!(std::fs::read_to_string(&count).unwrap(),"0");
 }
 assert!(child.try_wait().unwrap().is_some());
 // Exercise the same ownership guard on an assertion failure before accept.
 let failed_ready=base.join("foreign-peer-failure-ready");
 let mut failed=std::process::Command::new("/usr/bin/python3").args(["-c",code]).arg("foreign-peer-failure").arg(&failed_ready).arg(base.join("foreign-peer-failure-count")).current_dir(&base).spawn().unwrap();
 let refusal=std::panic::catch_unwind(std::panic::AssertUnwindSafe(||{let _owned=OwnedPeer(&mut failed);let deadline=std::time::Instant::now()+std::time::Duration::from_secs(3);while !failed_ready.exists(){assert!(std::time::Instant::now()<deadline);std::thread::sleep(std::time::Duration::from_millis(5));}panic!("synthetic-owned-listener-readiness-refusal");}));
 assert!(refusal.is_err());assert!(failed.try_wait().unwrap().is_some());
}
"""
    native=sys.platform=='darwin' and not args.source_only
    if native:real_guard_controls(layer,args.output)
    fixture+=r"""
#[test]fn actual_scoped_bridge_retries_and_replacements_keep_protection_until_current_terminal(){
 use std::sync::atomic::Ordering;
 // Pause a real worker at retry poll: its exact pending support remains guarded.
 *MODE.lock().unwrap()="busy";BRIDGE_PHASE.store(1,Ordering::SeqCst);server::mixel_gui_launch::submit(FIRST.into());wait_phase(1);password_security::actual_parent_not_owner();assert!(password_security::actual_kernel_active());
 server::mixel_gui_launch::submit(SECOND.into());assert!(password_security::actual_kernel_active());BRIDGE_PHASE.store(0,Ordering::SeqCst);*MODE.lock().unwrap()="accepted";*CHILD_READY.lock().unwrap()=true;release_phase();std::thread::sleep(std::time::Duration::from_millis(250));password_security::actual_parent_absent();
 // A's accepted reply cannot drop B, which arrived during A's blocked send.
 *PHASE_RELEASE.lock().unwrap()=0;PHASE_READY.store(0,Ordering::SeqCst);*CHILD_READY.lock().unwrap()=false;BRIDGE_PHASE.store(2,Ordering::SeqCst);server::mixel_gui_launch::submit(FIRST.into());wait_phase(2);server::mixel_gui_launch::submit(SECOND.into());BRIDGE_PHASE.store(3,Ordering::SeqCst);release_phase();wait_phase(3);assert!(password_security::actual_kernel_active());password_security::actual_parent_not_owner();BRIDGE_PHASE.store(0,Ordering::SeqCst);release_phase();std::thread::sleep(std::time::Duration::from_millis(250));password_security::actual_parent_absent();
 // A fails spawn after B replaces it; only A is refused, B stays guarded.
 *PHASE_RELEASE.lock().unwrap()=0;PHASE_READY.store(0,Ordering::SeqCst);*MODE.lock().unwrap()="not-found";BRIDGE_PHASE.store(4,Ordering::SeqCst);server::mixel_gui_launch::submit(FIRST.into());wait_phase(4);server::mixel_gui_launch::submit(SECOND.into());BRIDGE_PHASE.store(1,Ordering::SeqCst);release_phase();wait_phase(1);assert!(password_security::actual_kernel_active());password_security::actual_parent_not_owner();BRIDGE_PHASE.store(0,Ordering::SeqCst);*MODE.lock().unwrap()="accepted";release_phase();std::thread::sleep(std::time::Duration::from_millis(250));password_security::actual_parent_absent();
}
"""
    if not native:fixture=portable_fixture(fixture,launch,args.output)
    if native:
        fixture+=r"""
#[test]fn cold_budget_includes_blocking_lease_and_refuses_late_success(){
 *MODE.lock().unwrap()="not-found";let old=*SPAWNS.lock().unwrap();LEASE_DELAY.store(15100,std::sync::atomic::Ordering::SeqCst);
 assert!(!server::mixel_gui_launch::cold_route(FIRST.into()));LEASE_DELAY.store(0,std::sync::atomic::Ordering::SeqCst);assert_eq!(*SPAWNS.lock().unwrap(),old);password_security::actual_parent_absent();
}
#[test]fn blocked_lease_worker_gate(){
 if std::env::var_os("MIXEL_BLOCKED_LEASE_GATE").is_none(){return;}
 *MODE.lock().unwrap()="not-found";*LEASE_BLOCK.lock().unwrap()=true;server::mixel_gui_launch::submit(FIRST.into());
 let deadline=std::time::Instant::now()+std::time::Duration::from_secs(3);while LEASE_CALLS.load(std::sync::atomic::Ordering::SeqCst)==0{assert!(std::time::Instant::now()<deadline);std::thread::sleep(std::time::Duration::from_millis(5));}
 let offered=std::time::Instant::now();for _ in 0..64 {server::mixel_gui_launch::submit(SECOND.into());server::mixel_gui_launch::submit(String::new());}assert!(offered.elapsed()<std::time::Duration::from_millis(250));assert_eq!(*SPAWNS.lock().unwrap(),0);
 *LEASE_BLOCK.lock().unwrap()=false;LEASE_EVENT.notify_all();while *SPAWNS.lock().unwrap()==0{assert!(std::time::Instant::now()<deadline);std::thread::sleep(std::time::Duration::from_millis(5));}
 *CHILD_READY.lock().unwrap()=true;
 // Observed spawn reservation precedes actual exec completion. Wait for the
 // exact delivery inside the original three-second case budget.
 while DELIVERED.lock().unwrap().is_empty(){assert!(std::time::Instant::now()<deadline,"latest delivery missing within original case budget");password_security::actual_parent_not_owner();std::thread::sleep(std::time::Duration::from_millis(5));}
 assert!(std::time::Instant::now()<deadline,"latest delivery observed after original case budget");
 assert_eq!(*SPAWNS.lock().unwrap(),1);assert_eq!(DELIVERED.lock().unwrap().as_slice(),[SECOND.to_owned()]);password_security::actual_parent_not_owner();
 // Require actual kernel release after the owned child exits; every sample
 // still forbids parent OWNER/memory state, and the case budget is unchanged.
 while password_security::actual_kernel_active(){assert!(std::time::Instant::now()<deadline,"owned consent guard did not release within original case budget");password_security::actual_parent_not_owner();std::thread::sleep(std::time::Duration::from_millis(5));}
 assert!(std::time::Instant::now()<deadline,"kernel release observed after original case budget");
 password_security::actual_parent_absent();
}
#[test]fn newest_ingress_survives_64_callbacks_while_lease_worker_is_blocked(){
 let base=std::path::PathBuf::from(std::env::var_os("MIXEL_TEST_ROOT").unwrap()).join("blocked-lease-gate");std::fs::create_dir(&base).unwrap();
 let status=std::process::Command::new(std::env::current_exe().unwrap()).args(["--exact","blocked_lease_worker_gate","--nocapture"]).env("MIXEL_BLOCKED_LEASE_GATE","1").env("MIXEL_TEST_ROOT",&base).status().unwrap();assert!(status.success());
 std::thread::sleep(std::time::Duration::from_millis(800));drop(server::mixel_gui_launch::reservation::Reservation::acquire(&base).unwrap());
}
"""
    target=args.output/'source-controls.rs';target.write_bytes(fixture.encode());binary=args.output/('source-controls.exe' if os.name=='nt' else 'source-controls')
    run(rustc_command([os.environ.get('RUSTC_BIN','rustc'),'--edition=2021','--deny=warnings','--test','--cfg','feature="flutter"',str(target),'-o',str(binary)]))
    if native:
        listing=run([str(binary),'--list']).stdout.decode()
        tests=[line.removesuffix(': test') for line in listing.splitlines() if line.endswith(': test')]
        for index,name in enumerate(tests):
            root=args.output/('native-case-'+str(index));root.mkdir()
            run([str(binary),'--exact',name,'--nocapture'],env={**os.environ,'MIXEL_TEST_ROOT':str(root)})
        print('PASS '+str(len(tests))+' isolated actual runtime cases with literal real guard; no static OWNER pollution between cases')
    else:
        run([str(binary),'--test-threads=1','--nocapture'],env={**os.environ,'MIXEL_TEST_ROOT':str(args.output)})
    (args.output/'generated-hashes.json').write_text(json.dumps(first,indent=2)+'\n')
    print('PASS exact support/ordinary typed sender and bounded latest storage; '+('native owned Mac reservation/child/peer controls' if native else 'portable compiled source controls; native Mac ownership not executed'))
if __name__=='__main__':main()
