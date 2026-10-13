#!/usr/bin/env python3
"""Execute exact Tao callback prefix/helper/delegator using private offline seams.

No NSApplication, URI delivery, installed IPC, native app, services, preferences,
process signals, HTTP or external socket is used. The URL representation, IPC
outcome and thread-builder failure are explicit in-memory fixture adapters.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS=Path(__file__).resolve().parent
UPSTREAM=Path(os.environ.get("RDREPO",SCRIPTS.parent/"rustdesk"))
spec=importlib.util.spec_from_file_location("tray_url_patch",SCRIPTS/"patch-support-macos-tray-url.py")
patch=importlib.util.module_from_spec(spec);spec.loader.exec_module(patch)

def between(text,start,end):
    assert text.count(start)==1,start
    return text[text.index(start):text.index(end,text.index(start))]

def method(text,start):
    start=text.index(start);opening=text.index("{",start);depth=1;end=opening+1
    while depth:
        if text[end]=="{":depth+=1
        if text[end]=="}":depth-=1
        end+=1
    return text[start:end]

SEAMS=r"""
#![allow(dead_code,unused_imports,unused_mut)]
use std::sync::{Mutex,Condvar,OnceLock};
use std::sync::atomic::{AtomicBool,Ordering};
use std::time::{Duration,Instant};
use tao::event_loop::ControlFlow;
#[macro_export] macro_rules! safe_info {($($arg:tt)*)=>{{let text=format!($($arg)*); assert!(!text.contains("inv_") && !text.contains("apikey") && !text.contains("mixel-remote://"));}}}
mod log {pub use crate::safe_info as info;pub use crate::safe_info as debug;}
mod url {#[derive(Clone)] pub struct Url(pub String);impl Url {pub fn as_str(&self)->&str{&self.0}}}
mod tao {pub mod event {pub enum StartCause {Init} pub enum Event {Opened{urls:Vec<crate::url::Url>},NewEvents(StartCause),Other}} pub mod event_loop {pub enum ControlFlow {WaitUntil(std::time::Instant)}}}
#[derive(Clone,Copy)]enum Reply{Accept,Reject,WrongName,WrongValue,Missing,Timeout,Block}
struct State {reply:Reply,calls:Vec<String>,pids:Vec<u32>,threads:Vec<std::thread::ThreadId>,fallback:Vec<String>,released:bool}
fn state()->&'static (Mutex<State>,Condvar){static STATE:OnceLock<(Mutex<State>,Condvar)>=OnceLock::new();STATE.get_or_init(||(Mutex::new(State{reply:Reply::Accept,calls:vec![],pids:vec![],threads:vec![],fallback:vec![],released:false}),Condvar::new()))}
static FAIL_SPAWN:AtomicBool=AtomicBool::new(false);
mod fixture_thread {
 pub struct Builder(std::thread::Builder);
 impl Builder {pub fn new()->Self{Self(std::thread::Builder::new())} pub fn name(self,name:String)->Self{Self(self.0.name(name))} pub fn spawn<F>(self,f:F)->std::io::Result<std::thread::JoinHandle<()>>where F:FnOnce()+Send+'static {if crate::FAIL_SPAWN.load(std::sync::atomic::Ordering::Acquire){Err(std::io::Error::new(std::io::ErrorKind::Other,"synthetic-spawn-unavailable"))}else{self.0.spawn(f)}}}
}
mod ipc {
 pub fn send_url_scheme(intent:String)->Result<(),&'static str>{
  let mut s=crate::state().0.lock().unwrap();s.calls.push(intent);s.pids.push(std::process::id());s.threads.push(std::thread::current().id());let reply=s.reply;crate::state().1.notify_all();
  if matches!(reply,crate::Reply::Block){while !s.released{s=crate::state().1.wait(s).unwrap();}return Ok(());}
  let good=match reply{crate::Reply::Accept=>crate::server::mixel_support_url::accepted_ack("mixel-support-url-ack-v1",Some("accepted")),crate::Reply::WrongName=>crate::server::mixel_support_url::accepted_ack("wrong",Some("accepted")),crate::Reply::WrongValue=>crate::server::mixel_support_url::accepted_ack("mixel-support-url-ack-v1",Some("unavailable")),_=>false};
  if good{Ok(())}else{Err("synthetic-handoff-not-accepted")}
 }
}
fn run_me<T:AsRef<str>>(args:Vec<T>)->Result<(),&'static str>{state().0.lock().unwrap().fallback.extend(args.into_iter().map(|v|v.as_ref().to_owned()));Ok(())}
fn wait_idle(){let end=Instant::now()+Duration::from_secs(3);while MIXEL_TRAY_URL_WORKERS.load(Ordering::Acquire)!=0{assert!(Instant::now()<end,"worker failed to finish");std::thread::sleep(Duration::from_millis(2));}}
fn reset(reply:Reply){wait_idle();FAIL_SPAWN.store(false,Ordering::Release);let mut s=state().0.lock().unwrap();s.reply=reply;s.calls.clear();s.fallback.clear();s.pids.clear();s.threads.clear();s.released=false;}
fn wait_calls(n:usize){let end=Instant::now()+Duration::from_secs(3);loop{if state().0.lock().unwrap().calls.len()>=n{return;}assert!(Instant::now()<end,"expected worker never dispatched");std::thread::sleep(Duration::from_millis(2));}}
fn uri(n:u8)->String{format!("mixel-remote://support/?invite=inv_00000000-0000-0000-0000-{:012x}&apikey=synthetic-tray-key-000000000000",n)}
fn opened(values:Vec<String>)->tao::event::Event{tao::event::Event::Opened{urls:values.into_iter().map(url::Url).collect()}}
"""
TESTS=r"""
#[test]fn baseline_discards_actual_opened_event(){reset(Reply::Accept);original_callback(opened(vec![uri(1)]));std::thread::sleep(Duration::from_millis(15));assert!(state().0.lock().unwrap().calls.is_empty());}
#[test]fn corrected_dispatches_existing_gui_same_pid_off_event_loop(){reset(Reply::Accept);let caller=std::thread::current().id();corrected_callback(opened(vec![uri(1)]));wait_idle();let s=state().0.lock().unwrap();assert_eq!(s.calls,vec![uri(1)]);assert!(s.fallback.is_empty());assert_eq!(s.pids,vec![std::process::id()]);assert_ne!(s.threads[0],caller);}
#[test]fn missing_wrong_unavailable_or_timeout_keeps_actual_fallback(){for reply in [Reply::Reject,Reply::WrongName,Reply::WrongValue,Reply::Missing,Reply::Timeout]{reset(reply);corrected_callback(opened(vec![uri(2)]));wait_idle();let s=state().0.lock().unwrap();assert_eq!(s.calls,vec![uri(2)]);assert_eq!(s.fallback,vec![uri(2)]);}}
#[test]fn latest_valid_mixed_and_repeated_events(){reset(Reply::Accept);corrected_callback(opened(vec![uri(1),"mixel-remote://123456789".to_owned(),uri(3),"mixel-remote://support/?invite=invalid&apikey=synthetic-invalid-key".to_owned()]));wait_idle();assert_eq!(state().0.lock().unwrap().calls,vec![uri(3)]);}
#[test]fn ordinary_malformed_oversized_intents_never_forward(){reset(Reply::Accept);let base=uri(1);let invalid=vec!["mixel-remote://123456789".to_owned(),base.clone()+"#fragment",base.replace("support/","user@support/"),base.replace("support/","support:123/"),base.replace("support/","support/path/"),base.clone()+"&apikey=short",base.replace("synthetic-tray-key-000000000000",&"A".repeat(17000)),base.replace("synthetic-tray-key-000000000000","synthetic+invalid-key-00000000"),"https://example.invalid".to_owned()];corrected_callback(opened(invalid));std::thread::sleep(Duration::from_millis(15));assert!(state().0.lock().unwrap().calls.is_empty());}
#[test]fn bounded_tail_scan(){reset(Reply::Accept);let mut values=vec![uri(1)];values.extend((0..64).map(|_|"mixel-remote://ordinary".to_owned()));corrected_callback(opened(values));std::thread::sleep(Duration::from_millis(15));assert!(state().0.lock().unwrap().calls.is_empty());}
#[test]fn concurrent_limit_and_event_loop_nonblocking(){reset(Reply::Block);let started=Instant::now();for n in 1..=32{corrected_callback(opened(vec![uri(n)]));}assert!(started.elapsed()<Duration::from_millis(250));wait_calls(8);assert_eq!(MIXEL_TRAY_URL_WORKERS.load(Ordering::Acquire),8);assert_eq!(state().0.lock().unwrap().calls.len(),8);{let mut s=state().0.lock().unwrap();s.released=true;state().1.notify_all();}wait_idle();corrected_callback(opened(vec![uri(33)]));wait_idle();assert_eq!(state().0.lock().unwrap().calls.len(),9);}
#[test]fn failed_spawn_releases_worker_permit(){reset(Reply::Accept);FAIL_SPAWN.store(true,Ordering::Release);corrected_callback(opened(vec![uri(1)]));assert_eq!(MIXEL_TRAY_URL_WORKERS.load(Ordering::Acquire),0);assert!(state().0.lock().unwrap().calls.is_empty());FAIL_SPAWN.store(false,Ordering::Release);corrected_callback(opened(vec![uri(2)]));wait_idle();assert_eq!(state().0.lock().unwrap().calls,vec![uri(2)]);}
#[test]fn ordinary_new_events_prefix_remains_same(){reset(Reply::Accept);original_callback(tao::event::Event::NewEvents(tao::event::StartCause::Init));corrected_callback(tao::event::Event::NewEvents(tao::event::StartCause::Init));corrected_callback(tao::event::Event::Other);assert!(state().0.lock().unwrap().calls.is_empty());}
"""

def fixture(original,fixed,common,queue):
    marker="    event_loop.run(move |event, _, control_flow| {\n"
    anchor=patch.EVENT_ANCHOR
    old=between(original,marker,anchor).removeprefix(marker)
    new=between(fixed,marker,anchor).removeprefix(marker)
    helper=patch.HELPER
    # Only the native-platform cfg is relaxed for the offline test crate. The
    # original helper/queue/delegator bodies otherwise execute unchanged.
    helper=helper.replace('#[cfg(all(target_os = "macos", feature = "flutter"))]','#[cfg(test)]')
    new=new.replace('#[cfg(all(target_os = "macos", feature = "flutter"))]','#[cfg(test)]')
    assert helper.count("std::thread::Builder::new()")==1
    helper=helper.replace("std::thread::Builder::new()","fixture_thread::Builder::new()",1)
    def callback(name,body):return 'fn '+name+'(event:tao::event::Event){let _=&event;let mut flow=tao::event_loop::ControlFlow::WaitUntil(Instant::now());let control_flow=&mut flow;'+body+'}\n'
    delegate=method(common,"pub fn handle_url_scheme(url: String)")
    return SEAMS+"mod server {pub(crate) mod mixel_support_url {"+queue+"}}\n"+delegate+"\n"+helper+callback("original_callback",old)+callback("corrected_callback",new)+TESTS

class TrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        current=(UPSTREAM/"src/tray.rs").read_text()
        if patch.MARKER not in current and patch.digest(current.encode())==patch.PRISTINE_SHA256:
            # CI also invokes this gate before branding. Materialize the exact
            # full pipeline privately: never brand or relax pins on its input.
            with tempfile.TemporaryDirectory(prefix="mixel-tray-source-") as directory:
                root=Path(directory).resolve()/"rustdesk"
                def command(args,**kwargs):
                    result=subprocess.run(args,capture_output=True,text=True,timeout=90,**kwargs)
                    if result.returncode:raise RuntimeError(result.stdout[-3000:]+result.stderr[-3000:])
                    return result
                command(["git","clone","--quiet","--shared","--branch","1.4.6",str(UPSTREAM.resolve()),str(root)])
                assert command(["git","-C",str(root),"rev-parse","HEAD"]).stdout.strip()=="1abc897c451c8b5bbff3792509a7fef9d12f2ce3"
                command(["git","clone","--quiet","--shared",str((UPSTREAM/"libs/hbb_common").resolve()),str(root/"libs/hbb_common")])
                pin=command(["git","-C",str(root),"ls-tree","HEAD","libs/hbb_common"]).stdout.split()[2]
                command(["git","-C",str(root/"libs/hbb_common"),"checkout","--quiet",pin])
                command(["bash",str(SCRIPTS/"apply-branding.sh")],cwd=SCRIPTS.parent,
                        env={**os.environ,"RDREPO":str(root),"BRANDING":str(SCRIPTS.parent/"branding")})
                current=(root/"src/tray.rs").read_text()
                assert patch.MARKER in current, "Exact full pipeline did not apply the Mac tray overlay"
                cls.common=(root/"src/common.rs").read_text()
                cls.queue=(root/"src/mixel_support_url.rs").read_text()
        else:
            cls.common=(UPSTREAM/"src/common.rs").read_text()
            cls.queue=(UPSTREAM/"src/mixel_support_url.rs").read_text()
        cls.original=patch.transform(current,True) if patch.MARKER in current else current
        assert patch.digest(cls.original.encode())==patch.INPUT_SHA256
        cls.fixed=patch.transform(cls.original)
        if patch.MARKER in current:
            assert cls.fixed==current, "Exact final Mac tray overlay was not materialized"
        cls.rustc=os.environ.get("RUSTC_BIN","rustc")
        if not shutil.which(cls.rustc):raise SystemExit("Rust compiler required for actual Mac Tao callback gate")
    def test_actual_generated_callback_worker_validator_and_existing_delegator(self):
        with tempfile.TemporaryDirectory(prefix="mixel-tray-offline-")as temp:
            source=Path(temp)/"fixture.rs";source.write_text(fixture(self.original,self.fixed,self.common,self.queue));binary=Path(temp)/"fixture"
            for args in [[self.rustc,"--edition=2021","--test","-Dwarnings",str(source),"-o",str(binary)],[str(binary),"--test-threads=1"]]:
                result=subprocess.run(args,capture_output=True,text=True,timeout=45)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                print(result.stdout,end="")
    def test_exact_inverse_preserves_all_existing_tray_and_other_platform_source(self):
        self.assertEqual(patch.transform(self.fixed,True),self.original)
        self.assertEqual(self.fixed.replace(patch.HELPER,"",1).replace(patch.EVENT,"",1),self.original)
        self.assertEqual(self.fixed.count("crate::handle_url_scheme(intent);"),1)
        self.assertNotIn("mainSetOption",patch.HELPER);self.assertNotIn("mixel-support-invite-attended",patch.HELPER)
        # The route delegates to existing ACK/runtime-lease behavior. It keeps
        # existing sessions and the customer Accept requirement for new attended
        # requests; it does not prove foreground readiness during an old session.
        self.assertIn("crate::ipc::send_url_scheme(url.clone())",method(self.common,"pub fn handle_url_scheme(url: String)"))
        for line in patch.HELPER.splitlines():
            if "log::" in line:self.assertNotIn("{}",line)
    def test_lf_crlf_idempotency_and_restore(self):
        for ending in ("\n","\r\n"):
            with tempfile.TemporaryDirectory()as temp:
                root=Path(temp);(root/"src").mkdir();path=root/"src/tray.rs";raw=self.original.replace("\n",ending).encode();path.write_bytes(raw)
                patch.apply(root);first=path.read_bytes();patch.apply(root);self.assertEqual(first,path.read_bytes());patch.apply(root,True);self.assertEqual(raw,path.read_bytes())
    def test_pristine_early_restore_is_exact_noop_but_not_final_apply(self):
        pristine=subprocess.check_output(["git","-C",str(UPSTREAM),"show","1abc897c451c8b5bbff3792509a7fef9d12f2ce3:src/tray.rs"])
        self.assertEqual(patch.digest(pristine),patch.PRISTINE_SHA256)
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);(root/"src").mkdir();path=root/"src/tray.rs";path.write_bytes(pristine)
            patch.apply(root,True);self.assertEqual(path.read_bytes(),pristine)
            with self.assertRaises(SystemExit):patch.apply(root)
            self.assertEqual(path.read_bytes(),pristine)
            path.write_bytes(pristine+b"// drift\n")
            with self.assertRaises(SystemExit):patch.apply(root,True)
            self.assertEqual(path.read_bytes(),pristine+b"// drift\n")

    def test_drift_and_overlay_corruption_refuse_before_write(self):
        for body in [self.original+"// drift\n",self.fixed+"// drift\n",self.fixed.replace("runtime-only, support-only","changed",1)]:
            with tempfile.TemporaryDirectory()as temp:
                root=Path(temp);(root/"src").mkdir();path=root/"src/tray.rs";path.write_text(body)
                with self.assertRaises(SystemExit):patch.apply(root)
                self.assertEqual(path.read_text(),body)

if __name__=="__main__":
    if sys.platform not in {"darwin","linux"}:raise SystemExit("Mac tray offline fixture runs on macOS/Linux; no Windows gate")
    unittest.main(verbosity=2)
