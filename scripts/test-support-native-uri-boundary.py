#!/usr/bin/env python3
"""Execute exact generated Mac URL boundaries with pure counters; no IPC or apps."""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import re
import subprocess
from pathlib import Path
from rust_toolchain import rustc_command

BASE = Path(__file__).absolute().parent
spec = importlib.util.spec_from_file_location('native_uri_terminal_owner', BASE / 'patch-support-uri-routing.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def function(text, name):
    match = re.search(r'(?m)^pub(?:\(crate\))? (?:async )?fn ' + re.escape(name) + r'\(', text)
    if not match:
        raise RuntimeError('Missing exact native boundary function')
    end = text.index('\n}\n', match.start()) + 3
    return text[match.start():end]


def mac_branch(text):
    # Only the actual Mac cfg attributes are removed for the portable fixture;
    # function statements and original payload bytes remain unchanged.
    return text.replace('    #[cfg(all(target_os = "macos", feature = "flutter"))]\n', '').replace('    #[cfg(not(target_os = "ios"))]\n', '')


HARNESS = r'''
#![allow(dead_code)]
use std::future::Future;
use std::sync::{Arc,Mutex};
use std::task::{Context,Poll,Wake,Waker};
type ResultType<T> = Result<T,&'static str>;
macro_rules! bail { ($value:expr) => {return Err($value)}; }
#[macro_export] macro_rules! fixed_debug { ($($value:tt)*) => {{ let _ = format_args!($($value)*); }}; }
mod log { pub(crate) use crate::fixed_debug as debug; }
#[derive(Clone,Copy,Debug,Default,PartialEq,Eq)]
struct Counts { legacy_connect:usize, legacy_write:usize, protected:usize, submit:usize, fallback:usize, receiver_ack:usize, receiver_legacy:usize, fail_legacy:bool }
static COUNTS:Mutex<Counts>=Mutex::new(Counts{legacy_connect:0,legacy_write:0,protected:0,submit:0,fallback:0,receiver_ack:0,receiver_legacy:0,fail_legacy:false});
static PAYLOAD:Mutex<Option<String>>=Mutex::new(None);
fn reset(fail_legacy:bool){*COUNTS.lock().unwrap()=Counts{fail_legacy,..Counts::default()};*PAYLOAD.lock().unwrap()=None;}
fn counts()->Counts{*COUNTS.lock().unwrap()}
mod server {
    #[path="QUEUE_PATH"] pub(crate) mod mixel_support_url;
    pub(crate) mod mixel_gui_launch {pub fn submit(url:String){super::super::COUNTS.lock().unwrap().submit+=1;*super::super::PAYLOAD.lock().unwrap()=Some(url);}}
}
#[derive(Clone)] enum Data { UrlLink(String) }
struct Connection;
async fn connect(deadline:u64,postfix:&str)->ResultType<Connection>{assert_eq!((deadline,postfix),(1_000,"_url"));let mut c=COUNTS.lock().unwrap();c.legacy_connect+=1;if c.fail_legacy{Err("synthetic legacy unavailable")}else{Ok(Connection)}}
impl Connection{async fn send(&mut self,data:&Data)->ResultType<()>{let Data::UrlLink(url)=data;COUNTS.lock().unwrap().legacy_write+=1;*PAYLOAD.lock().unwrap()=Some(url.clone());Ok(())}}
async fn send_mixel_gui_intent_(url:String)->server::mixel_support_url::Handoff{assert!(server::mixel_support_url::is_valid_support_url(&url));COUNTS.lock().unwrap().protected+=1;*PAYLOAD.lock().unwrap()=Some(url);server::mixel_support_url::Handoff::Accepted}
ACTUAL_SENDER
ORIGINAL_SENDER
mod ipc {
 pub fn send_url_scheme(url:String)->super::ResultType<()>{super::run(super::send_url_scheme(url))}
 pub fn original_send_url_scheme(url:String)->super::ResultType<()>{super::run(super::original_send_url_scheme(url))}
}
fn run_me(_:Vec<String>)->ResultType<()>{COUNTS.lock().unwrap().fallback+=1;Ok(())}
ACTUAL_COMMON
ORIGINAL_COMMON
fn core_first(args:Vec<String>)->Option<Vec<String>>{
 CORE_FIRST_GUARD
 Some(args)
}
fn core_cli(uni_links:String)->Option<Vec<String>>{
 CORE_CLI_GUARD
 Some(vec![uni_links])
}
async fn send_mixel_support_url_ack(_: &mut Connection,delivery:server::mixel_support_url::Delivery){assert_eq!(delivery,server::mixel_support_url::Delivery::Unavailable);COUNTS.lock().unwrap().receiver_ack+=1;}
async fn receiver_guard(url:String){
 use server::mixel_support_url;
 let mut conn=Connection;
 for _ in 0..1 {
 RECEIVER_GUARD
 COUNTS.lock().unwrap().receiver_legacy+=1;
 }
}
struct Noop;impl Wake for Noop{fn wake(self:Arc<Self>){}}
fn run<T>(future:impl Future<Output=T>)->T{let w=Waker::from(Arc::new(Noop));let mut c=Context::from_waker(&w);let mut f=Box::pin(future);loop{if let Poll::Ready(x)=f.as_mut().poll(&mut c){return x}}}
const VALID:&str="mixel-remote://support?invite=inv_00000000-0000-0000-0000-000000000001&apikey=synthetic-public-key-000000000000";
fn invalid_cases()->Vec<String>{vec![
 format!("{VALID}#help"),
 VALID.replace("://support?","://support/r?"),
 VALID.replace("://support?","://user@support?"),
 VALID.replace("://support?","://support:123?"),
 VALID.replace("://support?","://%73upport?"),
 format!("{}#help",VALID.replace("://support?","://SuPpOrT?")),
 VALID.replace("mixel-remote:","rustdesk:"),
 VALID.replace("mixel-remote:","https:"),
 VALID.replace("://support?","://123456789?"),
 format!("{}#help",VALID.replace("invite=inv_","INVITE=inv_")),
 VALID.replace("://support?","://123456789?").replace("apikey=synthetic","ap%69key=synthetic"),
 VALID.replace("://support?","://123456789?").replace("apikey=synthetic","apiKey=synthetic"),
 VALID.replace("support?","support\n?"),
 VALID.replace("support?","supp\u{0085}ort?"),
 VALID.replace("synthetic-public-key-000000000000","short"),
 VALID.replace("invite=inv_","invite=wrong_"),
 format!("{VALID}&padding={}","x".repeat(20_000)),
 "mixel-remote://support".into(),
 "mixel-remote://support#help".into(),
 "mixel-remote://123456789?%69NVITE=invalid".into(),
 "mixel-remote://123456789?APIKEY=invalid".into(),
 "mixel-remote://123456789?api%E2%84%AAey=invalid".into(),
 "mixel-remote://support?invite=%ZZ&apikey=%ZZ".into(),
 "mixel-remote://support?unknown=%ZZ".into(),
 "mixel-remote://support/?apikey=invalid".into(),
 "wrong://user@%73upport:123/r#help".into(),
 "mixel-remote://123456789?apikey#help".into(),
 "mixel-remote://123456789?apiKey#help".into(),
 "mixel-remote://123456789?api%E2%84%AAey#help".into(),
]}
#[test]fn original_fragment_defect_is_reproduced_without_any_transport(){
 let bad=format!("{VALID}#help");reset(false);assert!(run(original_send_url_scheme(bad.clone())).is_ok());assert_eq!((counts().legacy_connect,counts().legacy_write,counts().protected),(1,1,0));assert_eq!(PAYLOAD.lock().unwrap().as_deref(),Some(bad.as_str()));
 reset(true);original_handle_url_scheme(bad);assert_eq!((counts().legacy_connect,counts().fallback),(1,1));
}
#[test]fn every_malformed_candidate_is_refused_before_all_sender_common_core_and_receiver_side_effects(){
 for bad in invalid_cases(){assert!(server::mixel_support_url::is_reserved_support_url(&bad),"Candidate escaped reservation");assert!(!server::mixel_support_url::is_valid_support_url(&bad),"Malformed fixture is valid");
  for fail in [false,true]{reset(fail);assert!(run(send_url_scheme(bad.clone())).is_err());assert_eq!(counts(),Counts{fail_legacy:fail,..Counts::default()});assert!(PAYLOAD.lock().unwrap().is_none());
   reset(fail);handle_url_scheme(bad.clone());assert_eq!(counts(),Counts{fail_legacy:fail,..Counts::default()});assert!(PAYLOAD.lock().unwrap().is_none());}
  assert!(core_first(vec![bad.clone()]).is_none());assert!(core_cli(bad.clone()).is_none());reset(false);run(receiver_guard(bad));assert_eq!((counts().receiver_ack,counts().receiver_legacy),(1,0));
 }
}
#[test]fn valid_payload_is_unchanged_and_uses_only_existing_protected_handoff(){
 for uri in [VALID.to_owned(),VALID.replace("mixel-remote://support?","MiXeL-ReMoTe://SuPpOrT?"),VALID.replace("support?","support/?"),VALID.replace("apikey=","apiKey=")]{assert!(server::mixel_support_url::is_valid_support_url(&uri));assert!(server::mixel_support_url::is_reserved_support_url(&uri));reset(false);assert!(run(send_url_scheme(uri.clone())).is_ok());assert_eq!((counts().legacy_connect,counts().legacy_write,counts().protected),(0,0,1));assert_eq!(PAYLOAD.lock().unwrap().as_deref(),Some(uri.as_str()));reset(false);handle_url_scheme(uri.clone());assert_eq!((counts().submit,counts().legacy_connect,counts().fallback),(1,0,0));assert_eq!(PAYLOAD.lock().unwrap().as_deref(),Some(uri.as_str()));assert!(core_first(vec![uri.clone()]).is_some());assert!(core_cli(uri).is_some());}
}
#[test]fn ordinary_remote_close_and_legacy_parameters_preserve_the_complete_original_counts(){
 for uri in ["close","mixel-remote://123456789","rustdesk://123456789","https://123456789","mixel-remote://connect/123456789","mixel-remote://connection/new/123456789","mixel-remote://file-transfer/123456789","mixel-remote://support-other","mixel-remote://123456789?key=public&password=synthetic&relay=true&switch_uuid=other"]{
  assert!(!server::mixel_support_url::is_reserved_support_url(uri));for fail in [false,true]{reset(fail);let old=run(original_send_url_scheme(uri.into()));let old_counts=counts();reset(fail);let new=run(send_url_scheme(uri.into()));assert_eq!(old,new);assert_eq!(old_counts,counts());reset(fail);original_handle_url_scheme(uri.into());let old_counts=counts();reset(fail);handle_url_scheme(uri.into());assert_eq!(old_counts,counts());}
  assert!(core_first(vec![uri.into()]).is_some());assert!(core_cli(uri.into()).is_some());reset(false);run(receiver_guard(uri.into()));assert_eq!((counts().receiver_ack,counts().receiver_legacy),(0,1));
 }
 assert!(core_first(vec![]).is_some());assert!(core_first(vec!["--connect".into(),"support".into()]).is_some());
 reset(false);handle_url_scheme(String::new());assert_eq!((counts().submit,counts().legacy_connect,counts().fallback),(1,0,0));
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source = args.source.absolute()
    out = args.output.absolute()
    out.mkdir(parents=True, exist_ok=False)
    before = {name: patch.safe_source(source, name).read_bytes() for name in (*patch.ORIGINAL, *patch.NATIVE_ORIGINAL)}
    current = {name: patch.canonical(raw)[0] for name, raw in before.items()}
    original = {name: patch.transform_native(name, current[name], True) for name in patch.NATIVE_ORIGINAL}
    final = {name: patch.transform_native(name, original[name]) for name in patch.NATIVE_ORIGINAL}
    for name in patch.NATIVE_ORIGINAL:
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(final[name])
    sender = mac_branch(function(final['src/ipc.rs'], 'send_url_scheme'))
    baseline_sender = mac_branch(function(original['src/ipc.rs'], 'send_url_scheme')).replace('fn send_url_scheme(', 'fn original_send_url_scheme(', 1)
    common = mac_branch(function(final['src/common.rs'], 'handle_url_scheme'))
    baseline_common = mac_branch(function(original['src/common.rs'], 'handle_url_scheme')).replace('fn handle_url_scheme(', 'fn original_handle_url_scheme(', 1).replace('crate::ipc::send_url_scheme(', 'crate::ipc::original_send_url_scheme(', 1)
    # Exact newly owned guard statements, without surrounding unrelated startup
    # machinery. Full sender/common bodies above are independently executed.
    first = patch.NATIVE_CHANGES['src/core_main.rs'][0][1].removesuffix('    let _is_mixel_support_invite = args.first()\n')
    cli = patch.NATIVE_CHANGES['src/core_main.rs'][1][1].split('        if crate::server::mixel_support_url::is_valid_support_url(&uni_links)')[0]
    cli = cli.removeprefix('    #[cfg(target_os = "macos")]\n    {\n').removesuffix('        #[cfg(feature = "flutter")]\n')
    cli = cli.replace('        #[cfg(feature = "flutter")]\n', '')
    receiver = patch.NATIVE_CHANGES['src/server.rs'][0][1].removeprefix('                        Data::UrlLink(url) => {\n').removesuffix('                            if url.is_empty() {\n')
    harness = HARNESS
    for key, value in {'QUEUE_PATH': str(out / 'src/mixel_support_url.rs').replace('\\', '\\\\'), 'ACTUAL_SENDER': sender, 'ORIGINAL_SENDER': baseline_sender, 'ACTUAL_COMMON': common, 'ORIGINAL_COMMON': baseline_common, 'CORE_FIRST_GUARD': mac_branch(first), 'CORE_CLI_GUARD': cli, 'RECEIVER_GUARD': receiver}.items():
        assert harness.count(key) == 1
        harness = harness.replace(key, value)
    fixture = out / 'actual-native-boundaries.rs'
    fixture.write_text(harness)
    binary = out / ('actual-native-boundaries.exe' if __import__('os').name == 'nt' else 'actual-native-boundaries')
    rustc = os.environ.get('RUSTC_BIN') or shutil.which('rustc')
    if not rustc:
        raise RuntimeError('Installed Rust compiler is missing')
    commands = [rustc_command([rustc, '--edition=2021', '--test', '-Dwarnings', str(fixture), '-o', str(binary)]), [str(binary), '--test-threads=1', '--nocapture']]
    rows = []
    for index, command in enumerate(commands):
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (out / ('compile.log' if index == 0 else 'runtime.log')).write_text(result.stdout + result.stderr)
        rows.append({'command': command, 'exitCode': result.returncode})
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        if index:
            print(result.stdout.strip())
    # The security-critical peer verifier, protected payload sender and the
    # strict validator are conserved literally, beyond the five declared guards.
    protected = final['src/ipc.rs'].split('async fn send_mixel_gui_intent_(', 1)[1].split('// Emit `close` events', 1)[0]
    assert protected == original['src/ipc.rs'].split('async fn send_mixel_gui_intent_(', 1)[1].split('// Emit `close` events', 1)[0]
    validator = function(final['src/mixel_support_url.rs'], 'is_valid_support_url')
    assert validator == function(original['src/mixel_support_url.rs'], 'is_valid_support_url')
    assert all(patch.safe_source(source, name).read_bytes() == raw for name, raw in before.items())
    (out / 'result.json').write_text(json.dumps({'schema': 'mixel-pure-native-uri-boundaries-v1', 'commands': rows, 'nativeIPC': False, 'nativeApps': False, 'rawUrlOrCredentialsLogged': False, 'suppliedSourceUnchanged': True, 'invalidCases': 29, 'legacyCases': 9, 'protectedBodySHA256': hashlib.sha256(protected.encode()).hexdigest(), 'strictValidatorSHA256': hashlib.sha256(validator.encode()).hexdigest()}, indent=2) + '\n')
    print('PASS exact original fragment leak and fallback reproduced in pure counters; 29 malformed candidates block sender/common/core/receiver; valid protected payloads and nine ordinary legacy routes conserved; no IPC, listener, native app or URL activation.')


if __name__ == '__main__':
    main()
