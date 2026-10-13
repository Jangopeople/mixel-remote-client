#!/usr/bin/env python3
"""Executable source and behavior controls for the native warning correction and complete ownership composition.

Native app/Wayland/uinput/platform runtime qualification is outside this suite.
All materialization happens in exclusive copied fixtures; input sources and cached
crates are read-only. Original compiler warnings remain explicit negative controls.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import types
from unittest.mock import patch
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile

SPEC=importlib.util.spec_from_file_location('native_warning_patch',Path(__file__).with_name('patch-support-native-warnings.py'))
P=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(P)


def sha(data): return hashlib.sha256(data).hexdigest()


def snapshot(root):
    records={}
    for p in sorted(root.rglob('*')):
        s=p.lstat();n=str(p.relative_to(root))
        if stat.S_ISLNK(s.st_mode):records[n]=['symlink',os.readlink(p)]
        elif stat.S_ISREG(s.st_mode):records[n]=['file',s.st_nlink,sha(p.read_bytes())]
        elif stat.S_ISDIR(s.st_mode):records[n]=['dir']
        else:records[n]=['special',s.st_mode]
    return records


def run(args,out,label,env=None):
    r=subprocess.run([str(x) for x in args],capture_output=True,env=env,timeout=90)
    (out/(label+'.stdout')).write_bytes(r.stdout);(out/(label+'.stderr')).write_bytes(r.stderr)
    (out/(label+'.status.json')).write_text(json.dumps({'command':[str(x) for x in args],'returncode':r.returncode})+'\n')
    return r


def source_controls(original,out):
    for name in P.FRESH:
        raw=(original/name).read_bytes();text,eol=P.canonical(raw)
        assert P.digest(text)==P.FRESH[name]
        corrected=P.transform(name,text)
        assert P.digest(corrected)==P.CORRECTED[name] and P.transform(name,corrected,True)==text
    counts={'files':len(P.FRESH),'positive_profiles':0,'fatal_zero_write':0}
    for profile,eol in [('lf','\n'),('crlf','\r\n')]:
        repo=out/('materialization-'+profile);shutil.copytree(original,repo)
        for name in P.FRESH:
            p=repo/name;data,_=P.canonical(p.read_bytes());p.write_bytes(data.replace('\n',eol).encode())
        before=snapshot(repo)
        assert P.apply(repo,check=True)['writes']==0 and snapshot(repo)==before
        assert P.apply(repo)['writes']==10
        corrected=snapshot(repo)
        assert P.apply(repo)['writes']==0 and snapshot(repo)==corrected
        assert P.apply(repo,True,True)['writes']==0 and snapshot(repo)==corrected
        assert P.apply(repo,True)['writes']==10 and snapshot(repo)==before
        assert P.apply(repo,True)['writes']==0 and snapshot(repo)==before
        counts['positive_profiles']+=1
    for name in P.FRESH:
        for state in ('original','corrected'):
            repo=out/('drift-'+str(counts['fatal_zero_write']));shutil.copytree(original,repo)
            if state=='corrected':P.apply(repo)
            p=repo/name;p.write_bytes(p.read_bytes()+b'// foreign drift\n')
            before=snapshot(repo)
            for restore,check in [(False,False),(False,True),(True,False),(True,True)]:
                try:P.apply(repo,restore,check)
                except RuntimeError as error:assert 'Pinned native warning source changed: '+name==str(error)
                else:raise AssertionError('Changed source accepted')
                assert snapshot(repo)==before;counts['fatal_zero_write']+=1
    target=next(iter(P.FRESH))
    cases=['mixed','barecr','invalidutf8','bom','symlink','hardlink','missing','parent-symlink','partial']
    if hasattr(os,'mkfifo'):cases.append('fifo')
    else:counts['fifo_native_unavailable']=True
    for case in cases:
        repo=out/('unsafe-'+case);shutil.copytree(original,repo);p=repo/target
        if case=='mixed':p.write_bytes(p.read_bytes().replace(b'\n',b'\r\n',1))
        elif case=='barecr':p.write_bytes(p.read_bytes()+b'\r')
        elif case=='invalidutf8':p.write_bytes(p.read_bytes()+b'\xff')
        elif case=='bom':p.write_bytes(b'\xef\xbb\xbf'+p.read_bytes())
        elif case in ('symlink','hardlink'):
            elsewhere=out/('outside-'+case);elsewhere.write_bytes(p.read_bytes());p.unlink()
            if case=='symlink':
                try:p.symlink_to(elsewhere)
                except OSError:
                    if os.name!='nt':raise
                    p.write_bytes(elsewhere.read_bytes());counts['symlink_native_unavailable']=True;continue
            else:os.link(elsewhere,p)
        elif case=='fifo':p.unlink();os.mkfifo(p)
        elif case=='missing':p.unlink()
        elif case=='parent-symlink':
            parent=p.parent;elsewhere=out/'outside-parent';shutil.move(parent,elsewhere)
            try:parent.symlink_to(elsewhere,target_is_directory=True)
            except OSError:
                if os.name!='nt':raise
                shutil.move(elsewhere,parent);counts['symlink_native_unavailable']=True;continue
        else:p.write_bytes(P.transform(target,p.read_text()).encode())
        before=snapshot(repo)
        for restore,check in [(False,False),(False,True),(True,False),(True,True)]:
            try:P.apply(repo,restore,check)
            except (RuntimeError,UnicodeDecodeError,FileNotFoundError):pass
            else:raise AssertionError('Unsafe/partial source accepted: '+case)
            assert snapshot(repo)==before;counts['fatal_zero_write']+=1
    repo=out/'actual-attribute-model';shutil.copytree(original,repo)
    before=snapshot(repo);real_lstat=Path.lstat
    for forbidden in (repo,repo/Path(target).parent,repo/target):
        def reparse(path,*args,**kwargs):
            value=real_lstat(path,*args,**kwargs)
            if path==forbidden:
                return types.SimpleNamespace(st_mode=value.st_mode,st_nlink=value.st_nlink,st_size=value.st_size,st_file_attributes=0x400)
            return value
        for restore,check in [(False,False),(False,True),(True,False),(True,True)]:
            with patch.object(Path,'lstat',reparse):
                try:P.apply(repo,restore,check)
                except RuntimeError as error:assert 'Unsafe native warning source' in str(error) or 'Unowned or nonregular native warning source' in str(error)
                else:raise AssertionError('Windows reparse attribute accepted')
            assert snapshot(repo)==before;counts['fatal_zero_write']+=1
    (out/'materialization-results.json').write_text(json.dumps(counts,indent=2)+'\n')
    print('PASS: 10 exact whole-source reversals; LF/CRLF apply/restore/check/idempotence; '+str(counts['fatal_zero_write'])+' before-write refusal checks.')


def function(source,name):
    start=source.index('fn '+name+'(');brace=source.index('{',start);depth=1;i=brace+1
    while depth:
        if source[i]=='{':depth+=1
        elif source[i]=='}':depth-=1
        i+=1
    return source[start:i]


def compile_run(source,out,label,rustc,flags=(),expect=True):
    path=out/(label+'.rs');path.write_text(source);binary=out/(label+'.bin')
    result=run([rustc,'--edition=2021',path,'-o',binary,*flags],out,label+'-compile')
    assert (result.returncode==0)==expect,(label,result.stderr[-3000:])
    if expect:
        result=run([binary],out,label+'-run');assert result.returncode==0,(label,result.stderr)
    return result


def compiler_controls(original,out,rustc,newrustc,cargo,newcargo):
    old=(original/'libs/scrap/build.rs').read_text()
    corrected=P.transform('libs/scrap/build.rs',old)
    helper=corrected[corrected.index('fn mixel_supports_check_cfg'):corrected.index('fn main()')]
    literal='println!("cargo:rustc-check-cfg=cfg(dxgi,quartz,x11)");'
    assert old.count(literal)==1 and corrected.count(literal)==1
    tail=old[old.index(literal)+len(literal):]
    assert corrected[corrected.index(literal)+len(literal)+6:]==tail, 'Remaining target build logic changed'
    wrapper=helper+'\nfn main(){println!("{}",mixel_supports_check_cfg());}\n'
    compiled=out/'compiler-query.rs';compiled.write_text(wrapper)
    binary=out/'compiler-query';r=run([rustc,'--edition=2021','-D','warnings',compiled,'-o',binary],out,'compiler-query-compile');assert r.returncode==0,r.stderr
    env=os.environ.copy()
    for i,(compiler,driver,expect) in enumerate([(rustc,cargo,b'false\n'),(newrustc,newcargo,b'true\n'),(newrustc,cargo,b'false\n'),(rustc,newcargo,b'false\n')]):
        env['RUSTC']=str(compiler);env['CARGO']=str(driver);r=run([binary],out,'actual-query-'+str(i),env);assert r.returncode==0 and r.stdout==expect
    env['CARGO']=str(newcargo)
    versions=[('1.79.0',False),('1.80.0',True),('1.80.0-nightly',True),('1.81.1',True),('2.0.0',True)]
    bad=[b'rustc 1.80',b'rustc x.80.0',b'cargo 1.80.0',b'rustc 1.80.bad',b'\xff',b'']
    for i,(version,expected) in enumerate(versions):
        script=out/('compiler-ok-'+str(i));script.write_text('#!/bin/sh\nprintf "%s\\n" "rustc '+version+' (fixture 2026-01-01)"\n');script.chmod(0o700)
        env['RUSTC']=str(script);r=run([binary],out,'synthetic-version-'+str(i),env);assert r.returncode==0 and r.stdout==str(expected).lower().encode()+b'\n'
    for i,data in enumerate(bad):
        payload=out/('compiler-bad-payload-'+str(i));payload.write_bytes(data)
        script=out/('compiler-bad-'+str(i));script.write_text('#!/bin/sh\ncat '+repr(str(payload))+'\n');script.chmod(0o700)
        env['RUSTC']=str(script);r=run([binary],out,'invalid-version-'+str(i),env);assert r.returncode!=0
    script=out/'compiler-failed';script.write_text('#!/bin/sh\nexit 23\n');script.chmod(0o700);env['RUSTC']=str(script);assert run([binary],out,'failed-version-query',env).returncode!=0
    env.pop('RUSTC');assert run([binary],out,'missing-cargo-rustc',env).returncode!=0
    # Real Cargo-selected compiler behavior: original warning on 1.75, corrected
    # no unsupported directive on 1.75, recognized directive on 1.81.
    records=[]
    for version,r,c in [('175',rustc,cargo),('181',newrustc,newcargo)]:
        for state,build in [('old','fn main(){'+literal+'}\n'),('new',helper+'\nfn main(){if mixel_supports_check_cfg(){'+literal+'}}\n')]:
            repo=out/('cargo-check-cfg-'+version+'-'+state);(repo/'src').mkdir(parents=True)
            (repo/'Cargo.toml').write_text('[package]\nname="mixel_check_cfg_fixture"\nversion="0.1.0"\nedition="2021"\n')
            (repo/'src/lib.rs').write_text('pub fn unchanged()->u32{17}\n');(repo/'build.rs').write_text(build)
            env=os.environ.copy();env.update(RUSTC=str(r),CARGO_TARGET_DIR=str(repo/'target'))
            result=run([c,'check','--offline','--verbose','--manifest-path',repo/'Cargo.toml'],out,'cargo-'+version+'-'+state,env)
            assert result.returncode==0,result.stderr
            warning=b'cargo:rustc-check-cfg requires -Zcheck-cfg flag' in result.stderr
            assert warning==(version=='175' and state=='old'),result.stderr
            output=next((repo/'target/debug/build').glob('*/output')).read_bytes()
            assert (literal.split('"')[1].encode() in output)==(state=='old' or version=='181')
            records.append({'compiler':version,'source':state,'unsupportedWarning':warning,'emitted':literal.split('"')[1].encode() in output})
    repo=out/'cargo175-rustc181-corrected';(repo/'src').mkdir(parents=True)
    (repo/'Cargo.toml').write_text('[package]\nname="mixel_check_cfg_mixed_fixture"\nversion="0.1.0"\nedition="2021"\n')
    (repo/'src/lib.rs').write_text('pub fn unchanged()->u32{17}\n');(repo/'build.rs').write_text(helper+'\nfn main(){if mixel_supports_check_cfg(){'+literal+'}}\n')
    env=os.environ.copy();env.update(RUSTC=str(newrustc),CARGO_TARGET_DIR=str(repo/'target'))
    result=run([cargo,'check','--offline','--manifest-path',repo/'Cargo.toml'],out,'actual-cargo175-rustc181-corrected',env)
    assert result.returncode==0 and b'cargo:rustc-check-cfg requires' not in result.stderr,result.stderr
    assert next((repo/'target/debug/build').glob('*/output')).read_bytes()==b''
    records.append({'compiler':'181','cargo':'175','source':'new','unsupportedWarning':False,'emitted':False})
    (out/'compiler-version-results.json').write_text(json.dumps(records,indent=2)+'\n')
    print('PASS: actual Rust1.75/1.81 Cargo directive controls, four actual tool pairs, five version boundaries and eight fatal query/version controls; target build tail conserved.')


def cfg_controls(original,out,rustc):
    # Compile literal changed bindings under explicitly modeled platform cfgs.
    # No platform native libraries or whole app are executed by these controls.
    modes={'linux':[],'macos':['--cfg','mixel_macos'],'windows':['--cfg','mixel_windows']}
    old=(original/'src/privacy_mode.rs').read_text();new=P.transform('src/privacy_mode.rs',old)
    start=old.index(P.CHANGES['src/privacy_mode.rs'][0][0]);oldb=old[start:old.index('        Arc::new(Mutex::new(map))',start)]
    start=new.index(P.CHANGES['src/privacy_mode.rs'][0][1]);newb=new[start:new.index('        Arc::new(Mutex::new(map))',start)]
    # Keep exact bindings while stub implementations expose count/creator values.
    for state,block in [('old',oldb),('new',newb)]:
        block=re.sub(r'\bwindows\b','mixel_windows',block.replace('target_os = "macos"','mixel_macos'))
        stubs='''use std::collections::HashMap;
trait PrivacyMode{fn value(&self)->u32;}
type PrivacyModeCreator=fn(&str)->Box<dyn PrivacyMode>;
struct P(u32);impl PrivacyMode for P{fn value(&self)->u32{self.0}}
#[cfg(mixel_macos)] mod macos{use super::*;pub const PRIVACY_MODE_IMPL:&str="mac";pub struct PrivacyModeImpl;impl PrivacyModeImpl{pub fn new(_: &str)->P{P(1)}}}
#[cfg(mixel_windows)] mod win_exclude_from_capture{use super::*;pub const PRIVACY_MODE_IMPL:&str="exclude";pub fn is_supported()->bool{std::env::var("SUPPORTED").unwrap_or_default()=="yes"}pub struct PrivacyModeImpl;impl PrivacyModeImpl{pub fn new(_: &str)->P{P(2)}}}
#[cfg(mixel_windows)] mod win_mag{use super::*;pub const PRIVACY_MODE_IMPL:&str="mag";pub struct PrivacyModeImpl;impl PrivacyModeImpl{pub fn new(_: &str)->P{P(3)}}}
#[cfg(mixel_windows)] mod win_virtual_display{use super::*;pub const PRIVACY_MODE_IMPL:&str="virtual";pub struct PrivacyModeImpl;impl PrivacyModeImpl{pub fn new(_: &str)->P{P(4)}}}
fn main(){
'''
        end='''
#[cfg(not(any(mixel_macos,mixel_windows)))] assert!(map.is_empty());
#[cfg(mixel_macos)] {assert_eq!(map.len(),1);assert_eq!(map["mac"]("mac").value(),1);}
#[cfg(mixel_windows)] {assert_eq!(map.len(),2);assert_eq!(map["virtual"]("virtual").value(),4);if win_exclude_from_capture::is_supported(){assert_eq!(map["exclude"]("exclude").value(),2);}else{assert_eq!(map["mag"]("mag").value(),3);}}
}
'''
        for mode,flags in modes.items():
            # A Linux model has no creator invocation; ensure the trait method is
            # reached synthetically so the model itself doesn't add dead-code warnings.
            extra='assert_eq!(P(7).value(),7);\n'
            source=stubs+block+extra+end
            r=compile_run(source,out,'privacy-'+state+'-'+mode,rustc,flags=flags)
            warnings=(out/('privacy-'+state+'-'+mode+'-compile.stderr')).read_bytes()
            assert (b'does not need to be mutable' in warnings)==(state=='old' and mode=='linux')
            if state=='new':assert b'warning:' not in warnings,warnings
            if mode=='windows':
                env=os.environ.copy();env['SUPPORTED']='yes';r=run([out/('privacy-'+state+'-'+mode+'.bin')],out,'privacy-'+state+'-supported',env);assert r.returncode==0
    # Clipboard accesses are already protected by the corrected exact cfg.
    text=(original/'src/server/connection.rs').read_text();guard='#[cfg(any(target_os = "windows", feature = "unix-file-copy-paste"))]'
    assert text.count(guard)==4 and guard[6:-2] in P.CHANGES['src/server/connection.rs'][0][1]
    for window in (False,True):
        for feature in (False,True):
            import_text=P.CHANGES['src/server/connection.rs'][0][1].replace('target_os = "windows"','mixel_windows').replace('target_os = "linux"','mixel_linux').replace('target_os = "macos"','mixel_macos')
            body='mod clipboard_file{pub fn only_feature_api()->u32{3}}\n'+import_text+'\nfn main(){\n#[cfg(any(mixel_windows, feature="unix-file-copy-paste"))]assert_eq!(only_feature_api(),3);\n}\n'
            # Stub only exists when its real API exists, matching actual app cfg.
            body=body.replace('mod clipboard_file','#[cfg(any(mixel_windows, feature="unix-file-copy-paste"))]\nmod clipboard_file')
            flags=['-D','warnings','--cfg','mixel_linux']+(['--cfg','mixel_windows'] if window else [])+(['--cfg','feature="unix-file-copy-paste"'] if feature else [])
            compile_run(body,out,'clipboard-'+str(window)+'-'+str(feature),rustc,flags)
    print('PASS: exact privacy binding Linux/Mac/Windows models preserve both Windows creators; clipboard four cfg combinations compile; no runtime implementations removed.')


def import_binding_controls(original,out,rustc):
    # Exact moved imports model the real updater cfg; all updater bodies remain
    # whole-source byte conserved by the reversal controls.
    name='src/flutter_ffi.rs';text=(original/name).read_text();corrected=P.transform(name,text)
    updater=text[text.index('    #[cfg(any(target_os = "windows", target_os = "macos"))]\n    {\n        use crate::updater::get_download_file_from_url;'):]
    import re
    assert len(re.findall(r'(?<![\w:])PathBuf\b',text))==1 and len(re.findall(r'(?<![\w:])Duration\b',text))==2
    assert 'Some(PathBuf::from(download_file))' in updater and 'Some(Duration::from_secs(3))' in updater
    imports=P.CHANGES[name][2][1].split('\n\npub type')[0].replace('target_os = "windows"','mixel_windows').replace('target_os = "macos"','mixel_macos')
    for mode,flags in [('linux',[]),('mac',['--cfg','mixel_macos']),('windows',['--cfg','mixel_windows'])]:
        compile_run(imports+'\nfn main(){\n#[cfg(any(mixel_windows,mixel_macos))]{let p=PathBuf::from("unchanged");let d=Duration::from_secs(3);assert_eq!(p.to_str(),Some("unchanged"));assert_eq!(d.as_secs(),3);}}',out,'updater-import-'+mode,rustc,['-D','warnings',*flags])
    name='src/tray.rs';bindings=P.CHANGES[name][0]
    for state,binding in [('old',bindings[0]),('new',bindings[1])]:
        binding=binding.replace('target_os = "macos"','mixel_macos')
        source='struct EventLoop{sets:u32}struct EventLoopBuilder;\nimpl EventLoopBuilder{fn new()->Self{Self}fn build(self)->EventLoop{EventLoop{sets:0}}}\nimpl EventLoop{#[cfg(mixel_macos)]fn set_activation_policy(&mut self){self.sets+=1;}fn run(self)->u32{self.sets}}\nfn main(){\n' +binding+'\n#[cfg(mixel_macos)]event_loop.set_activation_policy();\nlet sets=event_loop.run();assert_eq!(sets,if cfg!(mixel_macos){1}else{0});}\n'
        for mode,flags in [('linux',[]),('mac',['--cfg','mixel_macos'])]:
            compile_run(source,out,'tray-'+state+'-'+mode,rustc,flags)
            err=(out/('tray-'+state+'-'+mode+'-compile.stderr')).read_bytes()
            assert (b'does not need to be mutable' in err)==(state=='old' and mode=='linux')
            if state=='new':assert b'warning:' not in err,err
    stubs='pub mod hbb_common{pub fn bail(){}pub fn allow_err(){}pub fn anyhow(){}pub mod config{pub fn touch(){}}pub mod platform{pub mod linux{pub const CMD_SH:&str="sh";pub const DISTRO:&str="linux";}}pub mod serde_json{pub fn touch(){}}pub mod tokio{}pub type ResultType=();}\n'
    for name,end in [('libs/scrap/src/wayland/pipewire.rs','fn main(){bail();config::touch();serde_json::touch();let _:ResultType=();assert_eq!(CMD_SH,"sh");}'),('src/server/wayland.rs','fn main(){allow_err();assert_eq!(DISTRO,"linux");}')]:
        old=(original/name).read_text();new=P.transform(name,old)
        oldline=next(l for l in old.splitlines() if l.startswith('use hbb_common::{'))
        newline=next(l for l in new.splitlines() if l.startswith('use hbb_common::{'))
        compile_run(stubs+oldline+'\n'+end,out,'unused-import-old-'+str(len(end)),rustc,['-D','warnings'],False)
        compile_run(stubs+newline+'\n'+end,out,'unused-import-new-'+str(len(end)),rustc,['-D','warnings'])
    print('PASS: exact updater imports all three platform models, tray Linux/Mac mutation preservation, and both literal unused-import old failures/new warning-free controls.')


def api_controls(original,out,rustc,cargo,registry,lock):
    # Exact locked crate sources are copied into an exclusive fixture before use.
    versions={'base64':'0.22.1','whoami':'1.6.0','libc':'0.2.171'}
    vendor=out/'api-vendor';vendor.mkdir()
    records={}
    for name,version in versions.items():
        src=registry/(name+'-'+version);target=vendor/(name+'-'+version);target.mkdir()
        checksum={'base64':'72b3254f16251a8381aa12e40e3c4d2f0199f8c6508fbecb9d91f575e0fbb8c6','whoami':'6994d13118ab492c3c80c1f81928718159254c53c472bf9ce36f8dae4add02a7','libc':'c19937216e9d3aa9956d9bb8dfc0b0c8beb6058fc4f7a4dc4d850edf86a237d6'}[name]
        archive=registry.parent.parent/'cache'/registry.name/(name+'-'+version+'.crate')
        data=archive.read_bytes();assert sha(data)==checksum
        retained=out/'api-original-crates'/archive.name;retained.parent.mkdir(exist_ok=True);retained.write_bytes(data)
        count=0
        with tarfile.open(archive) as stream:
            for member in stream.getmembers():
                relative=Path(member.name);assert relative.parts[0]==name+'-'+version and all(p not in ('..','.') for p in relative.parts)
                if member.isdir():continue
                assert member.isfile() and not member.islnk() and not member.issym() and member.size<10000000
                relative=Path(*relative.parts[1:]);value=stream.extractfile(member).read()
                assert len(value)==member.size and value==(src/relative).read_bytes()
                dest=target/relative;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(value);count+=1
        records[name]={'version':version,'registryChecksum':checksum,'archiveFiles':count,'sourceSHA256':sha((src/'src/lib.rs').read_bytes()),'eachArchiveMemberMatchesReadOnlyCache':True}
    assert 'pub type useconds_t = u32;' in (vendor/'libc-0.2.171/src/unix/linux_like/linux/mod.rs').read_text()
    repo=out/'actual-api-fixture';(repo/'src').mkdir(parents=True)
    lines=['[package]','name="mixel_native_warning_api_fixture"','version="0.1.0"','edition="2021"','[dependencies]']
    for name,version in versions.items():lines.append(name+'={path='+json.dumps(str(vendor/(name+'-'+version)))+', version='+json.dumps('='+version)+'}')
    (repo/'Cargo.toml').write_text('\n'.join(lines)+'\n')
    # Use locked upstream resolution as an input; the exclusive fixture's Cargo
    # retains exact existing package versions while pruning unrelated dependencies.
    shutil.copyfile(lock,repo/'Cargo.lock')
    original_hostname=function((registry/'whoami-1.6.0/src/api.rs').read_text(),'hostname').replace('fn hostname() -> String','fn legacy_hostname(input: Result<String,()>) -> String').replace('fallible::hostname()','input')
    assert 'DEFAULT_HOSTNAME.to_lowercase()' in original_hostname and 'make_ascii_lowercase' in original_hostname
    corrected_expr='input.unwrap_or_else(|_| "localhost".to_owned()).to_ascii_lowercase()'
    corrected_app=P.transform('src/platform/linux.rs',(original/'src/platform/linux.rs').read_text())
    assert ''.join(corrected_expr.split()).replace('input','hbb_common::whoami::fallible::hostname()') in ''.join(corrected_app.split())
    source='''use base64::Engine;
const DEFAULT_HOSTNAME:&str="LocalHost";
'''+original_hostname+'\nfn corrected_hostname(input: Result<String,()>) -> String{'+corrected_expr+'}\n'+'''
fn main(){
 for sample in ["HOST.EXAMPLE","LocalHost","MiXeD-Éßİ😀", "", "ÄBC"] {
  assert_eq!(legacy_hostname(Ok(sample.to_owned())),corrected_hostname(Ok(sample.to_owned())));
 }
 assert_eq!(legacy_hostname(Err(())),corrected_hostname(Err(())));assert_eq!(corrected_hostname(Err(())),"localhost");
 assert_eq!(whoami::hostname(),whoami::fallible::hostname().unwrap_or_else(|_|"localhost".to_owned()).to_ascii_lowercase());
 for length in 0..=1024 {
  let data:Vec<u8>=(0..length).map(|i|((i*73+length)%256) as u8).collect();
  assert_eq!(base64::encode(&data),base64::engine::general_purpose::STANDARD.encode(&data));
 }
 assert_eq!(base64::engine::general_purpose::STANDARD.encode([251,255,255]),"+///");
 assert_eq!(base64::engine::general_purpose::STANDARD.encode([255]),"/w==");
 assert_eq!(base64::engine::general_purpose::STANDARD.encode([255,254]),"//4=");
 assert_eq!(std::mem::size_of::<libc::useconds_t>(),std::mem::size_of::<libc::c_uint>());
 let value:libc::c_uint=u32::MAX;let delay:libc::useconds_t=value;let _:libc::c_uint=delay;
 println!("PASS: real locked base640.22.1 equivalence1025+alphabet/padding; exact legacy hostname success/failure/ASCII normalization; actual libc ABI4/max equivalence");
}
'''
    (repo/'src/main.rs').write_text(source)
    (out/'actual-api-old-source.rs').write_text(source)
    env=os.environ.copy();env.update(RUSTC=str(rustc),CARGO_TARGET_DIR=str(repo/'target'))
    result=run([cargo,'run','--offline','--manifest-path',repo/'Cargo.toml'],out,'actual-api-old-equivalence',env);assert result.returncode==0,result.stderr[-4000:]
    shutil.copyfile(repo/'target/debug/mixel_native_warning_api_fixture',out/'actual-api-old-executed.bin')
    assert b'use of deprecated function `base64::encode`' in result.stderr and b'use of deprecated function `whoami::hostname`' in result.stderr
    corrected=source.replace('assert_eq!(whoami::hostname(),whoami::fallible::hostname().unwrap_or_else(|_|"localhost".to_owned()).to_ascii_lowercase());','let _=whoami::fallible::hostname().unwrap_or_else(|_|"localhost".to_owned()).to_ascii_lowercase();').replace('assert_eq!(base64::encode(&data),base64::engine::general_purpose::STANDARD.encode(&data));','assert_eq!(base64::engine::general_purpose::STANDARD.decode(base64::engine::general_purpose::STANDARD.encode(&data)).unwrap(),data);')
    corrected=corrected.replace('real locked base640.22.1 equivalence1025+alphabet/padding','real locked base640.22.1 roundtrip1025+alphabet/padding')
    (repo/'src/main.rs').write_text(corrected)
    (out/'actual-api-corrected-source.rs').write_text(corrected)
    result=run([cargo,'rustc','--offline','--manifest-path',repo/'Cargo.toml','--','-D','warnings'],out,'actual-api-corrected-deny-warnings',env);assert result.returncode==0,result.stderr[-4000:]
    result=run([repo/'target/debug/mixel_native_warning_api_fixture'],out,'actual-api-corrected-run',env);assert result.returncode==0
    shutil.copyfile(repo/'target/debug/mixel_native_warning_api_fixture',out/'actual-api-corrected-executed.bin')
    # Exact alias declaration is the source of old warning; public libc re-export
    # uses the same real crate and function-pointer argument type.
    (repo/'src/lib.rs').write_text('pub use libc::useconds_t;\npub type Delay=unsafe extern "C" fn(useconds_t)->libc::c_int;\npub fn identity(x:libc::c_uint)->useconds_t{x}\n')
    result=run([cargo,'rustc','--offline','--lib','--manifest-path',repo/'Cargo.toml','--','-D','warnings'],out,'actual-libc-reexport-deny-warnings',env);assert result.returncode==0,result.stderr[-4000:]
    (repo/'src/lib.rs').write_text('pub type useconds_t=libc::c_uint;\npub type Delay=unsafe extern "C" fn(useconds_t)->libc::c_int;\n')
    # An exclusive target avoids a same-timestamp build-cache reuse in this negative.
    env['CARGO_TARGET_DIR']=str(repo/'target-original-alias-negative')
    result=run([cargo,'rustc','--offline','--lib','--manifest-path',repo/'Cargo.toml','--','-D','warnings'],out,'actual-libc-old-negative',env);assert result.returncode!=0 and b'non_camel_case_types' in result.stderr
    (out/'actual-api-bindings.json').write_text(json.dumps(records,indent=2)+'\n')
    print('PASS: actual locked crate API equivalence and warning-free corrected API/re-export; exact original deprecation/noncamel failures retained.')



def context_copy(source, target):
    """Copy only the exact source/ownership inputs required by the prior chain."""
    transfer=P.prior_namespace('patch-support-file-transfer-window.py',P.PRIOR_FILE_TRANSFER_SHA)
    sdk=P.prior_namespace('patch-support-flutter-sdk-compatibility.py','08255c1af27ea05c5e1eaf3c8d903070d26b4a1c8c3e02eb1c24107bbcf92010')
    term=sdk['load_prior']('patch-support-macos-termination.py',sdk['PRIOR_SHA'])
    cleanup=sdk['load_prior']('patch-support-macos-cleanup.py',sdk['CLEANUP_SHA'])
    optional={transfer['GENERATED'],term['GENERATED'],cleanup['GENERATED']}
    names=set(P.FRESH)|set(transfer['COMPOSED'])|set(sdk['FILES'])|set(term['FILES'])|set(cleanup['FILES'])|optional|{'Cargo.toml'}
    target.mkdir()
    for name in sorted(names):
        data=transfer['safe_path'](source,name,optional=name in optional)
        if data is not None:
            path=target/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    for relative in (sdk['OWNED'],'flutter/.mixel-dependencies/macos_application_exit',cleanup['OWNED']):
        owned=source/relative
        if not owned.exists() and not owned.is_symlink():continue
        files,dirs=sdk['inventory'](source,relative)
        (target/relative).mkdir(parents=True,exist_ok=True)
        for name in dirs:(target/relative/name).mkdir(parents=True,exist_ok=True)
        for name,data in files.items():
            path=target/relative/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    return target


def production_snapshot(root):
    return {name:record for name,record in snapshot(root).items() if '.git' not in Path(name).parts}


def composed_controls(source,out,full_branding=False,bash='bash'):
    source=source.absolute()
    spec=importlib.util.spec_from_file_location("mixel_warning_final_projection",Path(__file__).with_name("test-support-flutter-analysis.py"))
    analysis=importlib.util.module_from_spec(spec);spec.loader.exec_module(analysis)
    with analysis.startup_baseline(source) as baseline:
        canonical=context_copy(baseline,out/'composed-canonical')
    P.apply(canonical,restore=True)
    assert {name:P.digest(P.canonical((canonical/name).read_bytes())[0]) for name in P.FRESH}==P.CURRENT
    input_before=snapshot(canonical)
    counts={'newline_profiles':0,'fatal_zero_write':0,'whole_macro_profiles':0}
    modes=((False,False),(False,True),(True,False),(True,True))
    for profile,eol in (('lf','\n'),('crlf','\r\n')):
        repo=out/('composed-'+profile);shutil.copytree(canonical,repo)
        for name in P.FRESH:
            path=repo/name;text,_=P.canonical(path.read_bytes());path.write_bytes(text.replace('\n',eol).encode())
        old=snapshot(repo)
        assert P.apply(repo,True,True,True)['writes']==0 and snapshot(repo)==old
        assert P.apply(repo,after_branding=True)['writes']==10
        final=snapshot(repo)
        assert P.apply(repo,after_branding=True)['writes']==0 and snapshot(repo)==final
        assert P.apply(repo,True,True,True)['writes']==0 and snapshot(repo)==final
        assert P.apply(repo,True)['writes']==10 and snapshot(repo)==old
        counts['newline_profiles']+=1
    counts.update(hash_drift_zero_write=0, deliberate_mixed_zero_write=0)
    for profile,newline in (('lf','\n'),('crlf','\r\n')):
        for name in P.FRESH:
            repo=out/('composed-target-'+profile+'-'+str(counts['fatal_zero_write']));shutil.copytree(canonical,repo)
            P.apply(repo,after_branding=True)
            path=repo/name;text,_=P.canonical(path.read_bytes())
            path.write_bytes((text.replace('\n',newline)+'// unrelated drift'+newline).encode('utf-8'))
            assert P.canonical(path.read_bytes())[1]==newline
            before=snapshot(repo)
            for restore,check in modes:
                try:P.apply(repo,restore,check,before_branding=restore and check)
                except RuntimeError as error:assert str(error)=='Pinned native warning source changed: '+name
                else:raise AssertionError('Changed branded warning source accepted')
                assert snapshot(repo)==before;counts['fatal_zero_write']+=1;counts['hash_drift_zero_write']+=1
    target=next(iter(P.FRESH))
    for profile,newline,opposite in (('lf','\n','\r\n'),('crlf','\r\n','\n')):
        repo=out/('composed-deliberate-mixed-'+profile);shutil.copytree(canonical,repo)
        P.apply(repo,after_branding=True)
        path=repo/target;text,_=P.canonical(path.read_bytes())
        path.write_bytes((text.replace('\n',newline)+'// deliberate mixed drift'+opposite).encode('utf-8'))
        before=snapshot(repo)
        for restore,check in modes:
            try:P.apply(repo,restore,check,before_branding=restore and check)
            except RuntimeError as error:assert str(error)=='Mixed or unsupported native warning source newlines'
            else:raise AssertionError('Deliberately mixed branded warning source accepted')
            assert snapshot(repo)==before;counts['fatal_zero_write']+=1;counts['deliberate_mixed_zero_write']+=1
    assert counts['hash_drift_zero_write']==len(P.FRESH)*len(modes)*2
    assert counts['deliberate_mixed_zero_write']==len(modes)*2
    prior_cases=[('flutter/lib/common.dart','drift'),
                 ('flutter/lib/desktop/pages/file_manager_page.dart','drift'),
                 ('flutter/lib/mixel_support_cleanup.dart','drift'),
                 ('flutter/lib/mixel_support_app_exit.dart','missing'),
                 ('flutter/lib/utils/mixel_file_transfer_geometry.dart','drift'),
                 ('flutter/.mixel-dependencies/macos_cleanup','missing-dir'),
                 ('flutter/.mixel-dependencies/macos_application_exit','extra'),
                 ('flutter/.mixel-dependencies/flutter_sdk_compatibility','extra')]
    for name,kind in prior_cases:
        repo=out/('composed-prior-'+str(counts['fatal_zero_write']));shutil.copytree(canonical,repo);P.apply(repo,after_branding=True)
        path=repo/name
        if kind=='missing':path.unlink()
        elif kind=='missing-dir':shutil.rmtree(path)
        elif kind=='extra':(path/'foreign.txt').write_bytes(b'foreign\n')
        else:path.write_bytes(path.read_bytes()+b'// prior layer drift\n')
        before=snapshot(repo)
        for restore,check in modes:
            try:P.apply(repo,restore,check,before_branding=restore and check)
            except (RuntimeError,FileNotFoundError):pass
            else:raise AssertionError('Changed prior layer accepted: '+name)
            assert snapshot(repo)==before;counts['fatal_zero_write']+=1
    folder=out/'prior-helper-controls';folder.mkdir()
    original_file=P.__file__
    for path in Path(original_file).parent.glob('patch-support-*.py'):shutil.copyfile(path,folder/path.name)
    prior=folder/'patch-support-file-transfer-window.py';saved=prior.read_bytes()
    try:
        P.__file__=str(folder/Path(original_file).name)
        for kind in ('crlf','mixed','tamper'):
            text,_=P.canonical(saved)
            raw=text.replace('\n','\r\n').encode() if kind=='crlf' else (saved.replace(b'\n',b'\r\n',1) if kind=='mixed' else saved+b'# foreign verifier\n')
            prior.write_bytes(raw);before=snapshot(canonical)
            if kind=='crlf':assert P.apply(canonical,True,True,True)['writes']==0
            else:
                try:P.apply(canonical,True,True,True)
                except RuntimeError:pass
                else:raise AssertionError('Changed prior helper accepted: '+kind)
                counts['fatal_zero_write']+=1
            assert snapshot(canonical)==before
    finally:P.__file__=original_file;prior.write_bytes(saved)
    if full_branding:
        root=Path(__file__).resolve().parents[1]
        for profile,eol in (('lf','\n'),('crlf','\r\n')):
            repo=out/('macro-'+profile);shutil.copytree(source,repo,symlinks=True)
            P.apply(repo,restore=True)
            for name in P.FRESH:
                path=repo/name;text,_=P.canonical(path.read_bytes());path.write_bytes(text.replace('\n',eol).encode())
            before=production_snapshot(repo)
            env=dict(os.environ,RDREPO=str(repo.absolute()),BRANDING=str(root/'branding'))
            result=run([bash,root/'scripts/apply-branding.sh'],out,'macro-'+profile+'-first',env)
            assert result.returncode==0,result.stderr[-4000:]
            final=production_snapshot(repo)
            changed=[n for n in before if before[n]!=final[n]]
            assert sorted(changed)==sorted(P.FRESH) and set(before)==set(final),(changed,set(before)^set(final))
            assert {name:P.digest(P.canonical((repo/name).read_bytes())[0]) for name in P.FRESH}==P.FINAL
            result=run([bash,root/'scripts/apply-branding.sh'],out,'macro-'+profile+'-second',env)
            assert result.returncode==0,result.stderr[-4000:]
            assert production_snapshot(repo)==final,'Repeated macro changed production bytes'
            for name in ('src/server/connection.rs','flutter/lib/common.dart','flutter/lib/mixel_support_cleanup.dart'):
                path=repo/name;saved=path.read_bytes();newline=b'\r\n' if b'\r\n' in saved else b'\n'
                path.write_bytes(saved+b'// unrelated macro drift'+newline);drift=production_snapshot(repo)
                result=run([bash,root/'scripts/apply-branding.sh'],out,'macro-'+profile+'-drift-'+str(counts['fatal_zero_write']),env)
                assert result.returncode!=0,result.stdout[-4000:]
                expected={'src/server/connection.rs':b'Pinned native warning source changed: src/server/connection.rs','flutter/lib/common.dart':b'Pinned file-transfer window source changed: flutter/lib/common.dart','flutter/lib/mixel_support_cleanup.dart':b'Owned Mac cleanup helper changed'}[name]
                assert expected in result.stderr,result.stderr[-4000:]
                assert production_snapshot(repo)==drift,'Drift refusal restored an earlier layer'
                path.write_bytes(saved);counts['fatal_zero_write']+=1
            (out/('macro-'+profile+'-whole-source-conservation.json')).write_bytes((json.dumps({'only_changed_sources':changed,'all_other_production_bytes_conserved':True,'twice_exact':True,'final_warning_source_sha256':P.FINAL},indent=2,sort_keys=True)+'\n').encode())
            counts['whole_macro_profiles']+=1
    assert snapshot(canonical)==input_before
    (out/'composed-results.json').write_bytes((json.dumps(counts,indent=2,sort_keys=True)+'\n').encode())
    print('PASS: exact branded LF/CRLF warning reversal and prior ownership guards; '+str(counts['fatal_zero_write'])+' zero-write refusals; '+str(counts['whole_macro_profiles'])+' full macro profiles, every other production byte conserved.')



def qualified_bash(explicit,windows):
    # Reuse the mandatory analyzer's strict active Git Bash contract.
    spec=importlib.util.spec_from_file_location('warning_active_bash_contract',Path(__file__).with_name('test-support-flutter-analysis.py'))
    analysis=importlib.util.module_from_spec(spec);spec.loader.exec_module(analysis)
    return analysis.branding_bash(explicit,windows)


def shell_path_controls(out):
    folder=out/'active shell préflight 🚀';folder.mkdir()
    install=folder/'Git with spaces'
    for name in ('bin/bash.exe','usr/bin/bash.exe','cmd/git.exe','usr/bin/cygpath.exe'):
        target=install/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'layout-only; never executed')
    for name in ('bin/bash.exe','usr/bin/bash.exe'):
        assert qualified_bash(install/name,True)==str((install/name).resolve())
    wsl=folder/'Windows/System32/bash.exe';wsl.parent.mkdir(parents=True);wsl.write_bytes(b'layout-only')
    refused=0
    for supplied in (None,Path('bash'),wsl,install,folder/'missing.exe'):
        try:qualified_bash(supplied,True)
        except (RuntimeError,OSError):refused+=1
        else:raise AssertionError('Absent/bare/WSL/nonfile active shell accepted')
    (install/'cmd/git.exe').unlink()
    for name in ('bin/bash.exe','usr/bin/bash.exe'):
        try:qualified_bash(install/name,True)
        except RuntimeError:refused+=1
        else:raise AssertionError('Foreign non-Git active shell accepted')
    assert refused==7 and qualified_bash(None,False)=='bash'
    (out/'active-bash-path-controls.json').write_bytes((json.dumps({'legitimateLayoutStandIns':2,'missingBareWslForeignRefusals':7,'actualWindowsRuntimeExecuted':False,'unixDefaultPreserved':True},indent=2)+'\n').encode())


def wiring_controls(out,bash):
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('warning_pipeline_contract',root/'scripts/test-flutter-sdk-pipeline.py')
    workflow=importlib.util.module_from_spec(spec);spec.loader.exec_module(workflow)
    script=(root/'scripts/apply-branding.sh').read_text(encoding='utf-8')
    dry='patch-support-native-warnings.py" --source "$RDREPO" --restore --check --before-branding'
    reverse='patch-support-native-warnings.py" --source "$RDREPO" --restore\n'
    late='patch-support-native-warnings.py" --source "$RDREPO" --after-branding'
    assert script.count(dry)==script.count(reverse)==script.count(late)==1
    assert script.index(dry)<script.index('patch-support-file-transfer-window.py" --restore --check')<script.index(reverse)<script.index('patch-support-file-transfer-window.py" --restore "$RDREPO"\n')<script.index(late)
    assert script.index(late)>script.index('patch-support-uinput-scroll.py"\n')
    for name in ('test-support-flutter-analysis.py','test-support-flutter-sdk-compatibility.py','test-support-macos-cleanup.py','test-support-macos-termination.py'):
        tree=__import__('ast').parse((root/'scripts'/name).read_text(encoding='utf-8'))
        body=next(node for node in tree.body if isinstance(node,__import__('ast').FunctionDef) and node.name in ('pipeline','full_branding'))
        sets=[{x.value for x in node.elts if isinstance(x,__import__('ast').Constant)} for node in __import__('ast').walk(body) if isinstance(node,(__import__('ast').Tuple,__import__('ast').List))]
        assert any({'patch-support-native-warnings.py','test-support-native-warnings.py'}<=items for items in sets),name
    source_only='scripts/test-support-native-warnings.py'
    cases=(('build.yml','linux','Preflight generated support source and reporter'),('build.yml','macos','Preflight generated support source and reporter'),('preflight.yml','upstream-source','Exercise actual source patches and support reporter'),('preflight.yml','macos-runtime-harness','Prove exact SDK source compatibility and composed branding'))
    calls=[]
    for number,(file,job,step) in enumerate(cases):
        text=(root/'.github/workflows'/file).read_text(encoding='utf-8');body=workflow.run_text(workflow.step(workflow.job(text,job),step))
        assert body.count(source_only)==1 and body.index(source_only)>body.index('scripts/test-support-flutter-sdk-compatibility.py')
        folder=out/('workflow-gate-'+str(number));folder.mkdir()
        shim=folder/'shim.py';shim.write_bytes(b"import json,os,sys\nfrom pathlib import Path\nwith Path(os.environ['MIXEL_CALLS']).open('a',encoding='utf-8') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\nif sys.argv[1]=='scripts/test-support-native-warnings.py':sys.exit(27)\n")
        env=dict(os.environ,MIXEL_SHIM=str(shim),MIXEL_PYTHON=sys.executable,MIXEL_CALLS=str(folder/'calls.jsonl'),RUNNER_TEMP=str(folder/'temporary pr\u00e9flight \U0001f680'))
        prefix='python3() { "$MIXEL_PYTHON" "$MIXEL_SHIM" "$@"; }\n'
        result=run([bash,'-e','-o','pipefail','-c',prefix+body],out,'workflow-gate-'+str(number),env)
        assert result.returncode==27,result.stderr
        actual=[json.loads(line) for line in (folder/'calls.jsonl').read_text(encoding='utf-8').splitlines()]
        assert [row[0] for row in actual]==['scripts/test-support-flutter-analysis.py','scripts/test-macos-startup.py','scripts/test-support-macos-tray-url.py','scripts/test-support-launch-macos-tray-controls.py','scripts/test-support-macos-termination-support.py','scripts/test-support-flutter-sdk-compatibility.py',source_only],actual
        assert actual[-1][1:] == ['--source','rustdesk' if file=='build.yml' else env['RUNNER_TEMP']+'/mixel-sdk-source/pipeline-lf','--source-only','--output',env['RUNNER_TEMP']+'/mixel-native-warning-source']
        calls.append({'workflow':file,'job':job,'first_warning_gate_failure':27,'later_tests_unrun':True})
    dispatcher_source=(root/'scripts/test-windows-generated-source.sh').read_bytes()
    dispatcher_text=dispatcher_source.decode('utf-8')
    assert dispatcher_text.count('unset mixel_analyzer_bash')==1
    assert dispatcher_text.index(source_only)<dispatcher_text.index('unset mixel_analyzer_bash')<dispatcher_text.index('dart analyze')
    for profile in ('unix','windows'):
        folder=out/('windows-dispatch-gate-'+profile);(folder/'scripts').mkdir(parents=True)
        dispatcher=folder/'scripts/test-windows-generated-source.sh';dispatcher.write_bytes(dispatcher_source)
        shim_script=b'python() { "$MIXEL_PYTHON" "$MIXEL_SHIM" "$@"; }\ncygpath() { printf "%s\\n" "$MIXEL_ACTIVE_BASH"; }\n'
        (folder/'scripts/use-windows-msvc-bash.sh').write_bytes(shim_script)
        shim=folder/'shim.py';shim.write_bytes(b"import json,os,sys\nfrom pathlib import Path\nwith Path(os.environ['MIXEL_CALLS']).open('a',encoding='utf-8') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\nif sys.argv[1]=='scripts/test-support-native-warnings.py':sys.exit(27)\n")
        env=dict(os.environ,MIXEL_SHIM=str(shim.absolute()),MIXEL_PYTHON=sys.executable,MIXEL_CALLS=str((folder/'calls.jsonl').absolute()),RUNNER_TEMP=str((folder/'temporary préflight 🚀').absolute()),MIXEL_SDK_FULL_APP_PREFLIGHT='false',OS='Windows_NT' if profile=='windows' else 'Linux',MIXEL_ACTIVE_BASH=r'C:\Program Files\Git préflight 🚀\bin\bash.exe')
        result=run([bash,str(dispatcher.absolute())],out,'windows-dispatch-gate-'+profile,env)
        assert result.returncode==27,result.stderr
        actual=[json.loads(line) for line in (folder/'calls.jsonl').read_text(encoding='utf-8').splitlines()]
        assert actual[0].count('--bash')==1;active=actual[0][actual[0].index('--bash')+1]
        if profile=='windows':assert active==env['MIXEL_ACTIVE_BASH']
        else:assert Path(active).resolve()==Path(shutil.which(bash) if bash=='bash' else bash).resolve()
        assert actual[-1][0]==source_only and actual[-1][1:]==['--source',env['RUNNER_TEMP']+'/mixel-sdk-source/pipeline-lf','--source-only','--output',env['RUNNER_TEMP']+'/mixel-native-warning-source','--bash',active]
        assert 'scripts/test-support-lease.py' in [row[0] for row in actual] and 'dart' not in [row[0] for row in actual]
        calls.append({'dispatcher':'test-windows-generated-source.sh','profile':profile,'exactValidatedActiveBashForwarded':active,'warning_gate_failure':27,'original_source_tests_before_gate_conserved':True,'later_dart_unrun':True,'windowsPathConversionMocked':profile=='windows'})
    (out/'mandatory-wiring-results.json').write_bytes((json.dumps(calls,indent=2,sort_keys=True)+'\n').encode())
    print('PASS: exact early/restore/late order, four copied-helper inventories, four Unix workflow firstfatal27 controls and Windows post-native source gate; later commands unrun.')

def pinned_original(source,out):
    repo=out/'pinned-original';repo.mkdir()
    for name,expected in P.FRESH.items():
        raw=subprocess.check_output(['git','-C',str(source),'show',P.UPSTREAM+':'+name],timeout=60)
        assert sha(raw)==expected,name
        target=repo/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    return repo

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--rustc',type=Path);p.add_argument('--rustc-new',type=Path);p.add_argument('--cargo',type=Path);p.add_argument('--cargo-new',type=Path);p.add_argument('--registry',type=Path);p.add_argument('--lock',type=Path);p.add_argument('--source-only',action='store_true');p.add_argument('--branded-source',type=Path);p.add_argument('--full-branding',action='store_true');p.add_argument('--bash',type=Path)
    args=p.parse_args()
    args.bash=qualified_bash(args.bash,os.name=='nt')
    if not args.source_only and not all((args.rustc,args.rustc_new,args.cargo,args.cargo_new,args.registry,args.lock)):
        p.error('Full API controls require both tool pairs, the registry and pinned lock')
    if args.full_branding and not args.branded_source:p.error('--full-branding requires --branded-source')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    shell_path_controls(out)
    if args.source_only:
        original=pinned_original(args.source,out)
        source_controls(original,out)
        composed_controls(args.source,out,bash=args.bash)
        wiring_controls(out,args.bash)
        print('PASS: mandatory pinned source and composed ownership controls; no native app qualification.')
        return
    before=snapshot(args.source)
    source_controls(args.source,out);compiler_controls(args.source,out,args.rustc,args.rustc_new,args.cargo,args.cargo_new);cfg_controls(args.source,out,args.rustc);import_binding_controls(args.source,out,args.rustc);api_controls(args.source,out,args.rustc,args.cargo,args.registry,args.lock)
    if args.branded_source:
        composed_controls(args.branded_source,out,args.full_branding,args.bash)
        wiring_controls(out,args.bash)
    assert snapshot(args.source)==before
    (out/'scope.json').write_text(json.dumps({'inputUnchanged':True,'wholeAppOrNativeLinuxExecuted':False,'sourceFiles':10,'dependenciesUpgraded':False,'sdkUpgraded':False,'permissionChanges':False,'warningSuppressionAdded':False,'functionalWarningsDeferred':True},indent=2)+'\n')
    print('PASS: all preparatory native-warning controls; input unchanged; no native Linux/app qualification claimed.')

if __name__=='__main__':main()
