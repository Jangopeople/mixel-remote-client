#!/usr/bin/env python3
"""Execute the generated DBus receiver and native lease across real processes.

Only DBus/Flutter transport dependencies are mocked. The exact generated native
callback, sender bootstrap, authorization guard and process-owned lease run.
An isolated endpoint prevents the fixture from affecting an installed app.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", required=True, type=Path)
parser.add_argument("--upstream", required=True, type=Path)
arguments = parser.parse_args()
rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
assert rustc, "Rust compiler required"
dbus = (arguments.source / "src/server/dbus.rs").read_text(encoding="utf-8")
original = subprocess.check_output(
    ["git", "-C", str(arguments.upstream), "show", "HEAD:src/server/dbus.rs"], text=True, encoding="utf-8")
core = (arguments.source / "src/core_main.rs").read_text(encoding="utf-8")
connection = (arguments.source / "src/server/connection.rs").read_text(encoding="utf-8")
callback = dbus[dbus.index("fn handle_client_message("):]
old_callback = original[original.index("fn handle_client_message("):]
handoff_start = callback.index("                // DBus acknowledges the queued event")
handoff_end = callback.index("                use crate::flutter;", handoff_start)
normalized_callback = callback[:handoff_start] + callback[handoff_end:]
delivery_start = normalized_callback.index("                if hbb_common::password_security::is_support_invite_arg(&_uni_links)")
delivery_end = normalized_callback.index("                }", delivery_start) + len("                }\n")
normalized_callback = normalized_callback[:delivery_start] + normalized_callback[delivery_end:]
normalized_callback = normalized_callback.replace(
    "                let delivery = crate::flutter::push_global_event(flutter::APP_TYPE_MAIN, event);\n                match delivery {",
    "                match crate::flutter::push_global_event(flutter::APP_TYPE_MAIN, event) {")
assert normalized_callback == old_callback, \
    "Only attended ownership and delivery acknowledgment may change the pinned callback"
sender_start = core.index("fn try_send_by_dbus(")
sender = core[sender_start:core.index("\n#[cfg(not(any(target_os", sender_start)]
bootstrap_start = core.index("    let _is_mixel_support_invite = args.first()")
bootstrap = core[bootstrap_start:core.index('    #[cfg(any(target_os = "linux", target_os = "windows"))]', bootstrap_start)]
gate_start = connection.index("    async fn send_logon_response_and_keep_alive(&mut self) -> bool {")
gate = connection[gate_start:connection.index("        if self.require_2fa.is_some()", gate_start)] + "        false\n    }\n"
assert "self.support_invite_attended |= password::support_invite_requires_click();" in gate
for declaration in ['const DBUS_METHOD_NEW_CONNECTION: &str = "NewConnection";',
                    'const DBUS_METHOD_NEW_CONNECTION_ID: &str = "id";',
                    'const DBUS_METHOD_RETURN: &str = "ret";',
                    'const DBUS_METHOD_RETURN_SUCCESS: &str = "ok";']:
    assert declaration in original and declaration in dbus, "DBus/CLI transport contract changed"
namespace = re.search(r'const DBUS_NAME: &str = "([^"]+)";', dbus).group(1)
assert namespace in ("org.rustdesk.rustdesk", "ch.mixel.remote"), "Unexpected pinned/branded namespace"
print("Source SHA256: DBus=" + hashlib.sha256(dbus.encode()).hexdigest()
      + " core=" + hashlib.sha256(core.encode()).hexdigest()
      + " connection=" + hashlib.sha256(connection.encode()).hexdigest(), flush=True)

SCAFFOLD = r'''#![allow(dead_code,unused_imports)]
use std::collections::HashMap;
use std::io::{BufRead,Write};
use std::sync::Mutex;
const DBUS_METHOD_NEW_CONNECTION:&str="NewConnection";
const DBUS_METHOD_NEW_CONNECTION_ID:&str="id";
const DBUS_METHOD_RETURN:&str="ret";
const DBUS_METHOD_RETURN_SUCCESS:&str="ok";
struct IfaceBuilder<T>{uri:String,result:Option<String>,state:std::marker::PhantomData<T>}
impl IfaceBuilder<()> {
    fn method<F>(&mut self,name:&str,input:(&str,),output:(&str,),mut callback:F)
    where F:FnMut(&mut (),&mut (),(String,))->Result<(String,), &'static str> {
        assert_eq!((name,input,output),("NewConnection",("id",),("ret",)));
        self.result=Some(callback(&mut (),&mut (),(self.uri.clone(),)).unwrap().0);
    }
}
mod flutter {
    pub const APP_TYPE_MAIN:&str="main";
    pub static QUEUE:super::Mutex<Vec<String>>=super::Mutex::new(Vec::new());
    pub fn push_global_event(channel:&str,event:String)->Option<bool>{
        assert_eq!(channel,APP_TYPE_MAIN);
        match std::env::var("QUEUE_FAILURE").as_deref(){
            Ok("missing")=>None,Ok("closed")=>Some(false),
            _=>{QUEUE.lock().unwrap().push(event);Some(true)}
        }
    }
}
mod serde_json {pub mod ser {
    pub fn to_string(data:&std::collections::HashMap<&str,&str>)->Result<String, &'static str>{
        assert_eq!(data["name"],"on_url_scheme_received");Ok(data["url"].to_owned())
    }
}}
mod log {macro_rules! error{($($arg:tt)*)=>{eprintln!($($arg)*)};}pub(crate) use error;}
mod password {
    pub use crate::guard::{is_support_invite_arg,support_invite_requires_click,support_invite_must_wait};
    pub fn hold_support_invite_attended_lease()->bool {
        if std::env::var("OWNER_FAILURE").is_ok(){return false;}
        crate::guard::hold_support_invite_attended_lease()
    }
}
mod hbb_common {pub mod password_security {pub use crate::password::*;}}
mod ipc {
    pub static CALLS:std::sync::atomic::AtomicU32=std::sync::atomic::AtomicU32::new(0);
    pub fn set_config(name:&str,value:String)->Result<(), &'static str>{
        assert_eq!(name,"mixel-support-invite-attended");assert_eq!(value,"Y");
        CALLS.fetch_add(1,std::sync::atomic::Ordering::SeqCst);
        if std::env::var("IPC_FAILURE").is_ok(){Err("unavailable")}
        else {crate::guard::renew_support_invite_attended();Ok(())}
    }
}
mod dbus {
    pub fn invoke_new_connection(uri:String)->Result<(),Box<dyn std::error::Error>>{
        use std::io::{BufRead,Write};
        let mut socket=std::net::TcpStream::connect(std::env::var("OWNED_ADDRESS")?)?;
        writeln!(socket,"uri {}",uri)?;
        let mut response=String::new();std::io::BufReader::new(socket).read_line(&mut response)?;
        if response.trim()!="ok"{return Err("native receiver did not accept attended handoff".into());}Ok(())
    }
}
mod client {pub const LOGIN_MSG_NO_PASSWORD_ACCESS:&str="no-password-access";}
struct Login{my_id:String,my_name:String}
struct Pending{authorized:bool,support_invite_attended:bool,support_invite_accepted:bool,lr:Login}
impl Pending {
    fn try_start_cm(&mut self,_id:String,_name:String,authorized:bool){assert!(!authorized);}
    async fn send_login_error(&mut self,message:&str){assert_eq!(message,client::LOGIN_MSG_NO_PASSWORD_ACCESS);}
    // AUTHORIZATION_GATE
}
struct Noop;
impl std::task::Wake for Noop{fn wake(self:std::sync::Arc<Self>){}}
fn blocked_new_login()->bool{
    let mut pending=Pending{authorized:false,support_invite_attended:false,support_invite_accepted:false,lr:Login{my_id:"synthetic-valid-peer".into(),my_name:"Synthetic".into()}};
    let waker=std::task::Waker::from(std::sync::Arc::new(Noop));let mut context=std::task::Context::from_waker(&waker);
    let mut future=Box::pin(pending.send_logon_response_and_keep_alive());
    match std::future::Future::poll(future.as_mut(),&mut context){std::task::Poll::Ready(blocked)=>blocked,_=>panic!("fixture unexpectedly pending")}
}
mod guard {
// GUARD_IMPLEMENTATION
}
// DBUS_CALLBACK
// DBUS_SENDER
fn main(){
    match std::env::args().nth(1).as_deref(){
        Some("receiver")=>{
            let listener=std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            println!("receiver-ready {}",listener.local_addr().unwrap());std::io::stdout().flush().unwrap();
            for connection in listener.incoming(){
                let mut socket=connection.unwrap();let mut command=String::new();
                std::io::BufReader::new(socket.try_clone().unwrap()).read_line(&mut command).unwrap();let command=command.trim();
                if let Some(uri)=command.strip_prefix("uri "){
                    let mut builder=IfaceBuilder{uri:uri.to_owned(),result:None,state:std::marker::PhantomData};
                    handle_client_message(&mut builder);writeln!(socket,"{}",builder.result.unwrap()).unwrap();
                }else if command=="probe"{
                    writeln!(socket,"guard={} blocked={} queue={} ipc={}",guard::support_invite_requires_click(),blocked_new_login(),flutter::QUEUE.lock().unwrap().len(),ipc::CALLS.load(std::sync::atomic::Ordering::SeqCst)).unwrap();
                }else if command=="process-ui"{
                    assert_eq!(flutter::QUEUE.lock().unwrap().len(),1);assert!(guard::hold_support_invite_attended_lease());flutter::QUEUE.lock().unwrap().clear();writeln!(socket,"processed").unwrap();
                }else if command=="exit"{writeln!(socket,"bye").unwrap();break;}
                else{panic!("unknown fixture operation")}
            }
        }
        Some("sender")=>{
            let args=vec![std::env::args().nth(2).unwrap()];
// SENDER_BOOTSTRAP
            if _is_mixel_support_invite{assert!(!guard::support_invite_owner_lease_failed());assert!(guard::support_invite_requires_click());}
            println!("sender-ready");std::io::stdout().flush().unwrap();
            let mut input=String::new();std::io::stdin().read_line(&mut input).unwrap();
            if try_send_by_dbus(args[0].clone()).is_none(){println!("sender-exit-after-queue-ack");}
            else{println!("sender-keeps-cold-owner");std::io::stdout().flush().unwrap();input.clear();std::io::stdin().read_line(&mut input).unwrap();}
        }
        Some("probe")=>println!("{}",guard::support_invite_requires_click()),
        _=>panic!("role required")
    }
}
'''

with tempfile.TemporaryDirectory(prefix="mixel-native-dbus-") as temporary:
    work = Path(temporary)
    guard = (ROOT / "scripts/support-invite-guard.rs").read_text(encoding="utf-8").split("\n#[cfg(test)]", 1)[0]
    # Exact ownership/probe implementation; isolate only the fixed endpoints.
    endpoint = work / "lease-root"
    endpoint.mkdir()
    quoted = json.dumps(str(endpoint), ensure_ascii=False)
    guard = guard.replace('const ROOT: &str = "/private/tmp";', 'const ROOT: &str = ' + quoted + ';').replace('const ROOT: &str = "/tmp";', 'const ROOT: &str = ' + quoted + ';')
    guard = guard.replace('Global\\\\Mixel-Remote-Attended-Runtime-v2', 'Global\\\\Mixel-Remote-Dbus-Test-' + uuid.uuid4().hex)
    binaries = {}
    for name, body in [("original", old_callback), ("patched", callback)]:
        code = SCAFFOLD.replace("// GUARD_IMPLEMENTATION", guard).replace("// DBUS_CALLBACK", body).replace("// DBUS_SENDER", sender).replace("// SENDER_BOOTSTRAP", bootstrap).replace("// AUTHORIZATION_GATE", gate)
        source = work / (name + ".rs")
        source.write_text(code, encoding="utf-8")
        binary = work / (name + (".exe" if os.name == "nt" else ""))
        subprocess.run([rustc, "--edition=2021", "--deny=warnings", "--cfg", 'feature="flutter"', str(source), "-o", str(binary)], check=True)
        binaries[name] = binary

    def scenario(name, uri, *, support=True, owner_failure=False, ipc_failure=False,
                 queue_failure=None):
        binary = binaries[name]
        env = os.environ.copy()
        if owner_failure:
            env["OWNER_FAILURE"] = "1"
        if ipc_failure:
            env["IPC_FAILURE"] = "1"
        if queue_failure:
            env["QUEUE_FAILURE"] = queue_failure
        receiver = subprocess.Popen([str(binary), "receiver"], stdout=subprocess.PIPE, text=True, env=env)
        sender_process = None
        def query(command):
            host, port = address.rsplit(":", 1)
            with socket.create_connection((host, int(port)), timeout=3) as stream:
                stream.sendall((command + "\n").encode())
                response = b""
                while not response.endswith(b"\n"):
                    chunk = stream.recv(4096)
                    assert chunk, "truncated native fixture response"
                    response += chunk
                return response.decode().strip()
        try:
            ready = receiver.stdout.readline().strip()
            assert ready.startswith("receiver-ready ")
            address = ready.split(" ", 1)[1]
            assert query("probe") == "guard=false blocked=false queue=0 ipc=0"
            sender_env = {**os.environ, "OWNED_ADDRESS": address}
            sender_process = subprocess.Popen([str(binary), "sender", uri], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=sender_env)
            assert sender_process.stdout.readline().strip() == "sender-ready"
            assert query("probe") == ("guard=true blocked=true queue=0 ipc=0" if support else "guard=false blocked=false queue=0 ipc=0")
            sender_process.stdin.write("dispatch\n")
            sender_process.stdin.flush()
            result = sender_process.stdout.readline().strip()
            keeps_sender = support and name == "patched" and (
                owner_failure and ipc_failure or queue_failure)
            if keeps_sender:
                assert result == "sender-keeps-cold-owner"
                assert sender_process.poll() is None
                assert query("probe") == f"guard=true blocked=true queue=0 ipc={int(owner_failure)}"
            else:
                assert result == "sender-exit-after-queue-ack"
                sender_process.wait(timeout=5)
                assert sender_process.returncode == 0
                required = support and name == "patched"
                expected = f"guard={str(required).lower()} blocked={str(required).lower()} queue={int(not queue_failure)} ipc={int(owner_failure and support)}"
                assert query("probe") == expected, (name, query("probe"), expected)
                if support and not owner_failure and not queue_failure:
                    assert query("process-ui") == "processed"
                    assert query("probe") == "guard=true blocked=true queue=0 ipc=0"
        finally:
            if sender_process and sender_process.poll() is None:
                sender_process.kill()
                sender_process.wait(timeout=5)
            if receiver.poll() is None:
                try:
                    query("exit")
                    receiver.wait(timeout=5)
                except Exception:
                    receiver.kill()
                    receiver.wait(timeout=5)
        assert subprocess.check_output([str(binary), "probe"], text=True).strip() == "false", "Final owner exit must restore saved unattended behavior"

    uri = "mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000002&apikey=synthetic-invalid-public-key-000000000000"
    scenario("original", uri)
    print("PASS: original native DBus callback reproduces consent/authentication gate reopening after sender exit while Dart event remains queued", flush=True)
    scenario("patched", uri)
    scenario("patched", uri.upper())
    print("PASS: actual generated receiver owns the kernel lease before enqueue/ack; original ordinary false, sender-held true, receiver-owned true after sender exit before Dart; central authorization blocks login; final exit restores saved behavior", flush=True)
    for nonsupport in [
        "mixel-remote://connect/123456789?relay=true",
        "mixel-remote://support-extra/?invite=synthetic",
        "mixel-remote://support/extra?invite=synthetic",
        "mixel-remote://support@host/?invite=synthetic",
        "mixel-remote://support:443/?invite=synthetic",
        "mixel-remote://support/",
        "rustdesk://support/?invite=synthetic",
        "--connect",
    ]:
        scenario("patched", nonsupport, support=False)
    scenario("patched", "mixel-remote://connect/123456789?relay=true", support=False,
             owner_failure=True, ipc_failure=True)
    print("PASS: nine unrelated/malformed URI and CLI cases preserve ordinary DBus dispatch without acquiring consent or writing settings, even with guard/IPC failures; namespace " + namespace + " preserved", flush=True)
    scenario("patched", uri, ipc_failure=True)
    scenario("patched", uri, owner_failure=True)
    scenario("patched", uri, owner_failure=True, ipc_failure=True)
    print("PASS: failed owner acquisition sends only runtime memory renewal; failed renewal returns non-success without enqueue and keeps sender's cold owner", flush=True)
    for failure in ("missing", "closed"):
        scenario("original", uri, queue_failure=failure)
        scenario("patched", uri, queue_failure=failure)
        scenario("patched", "mixel-remote://connect/123456789?relay=true",
                 support=False, queue_failure=failure)
    print("PASS: original missing/closed Flutter streams lose support intent and release sender; generated receiver returns non-success for each failed support delivery, keeping cold sender alive/owned; ordinary dispatch stays unchanged; final owner exit restores saved behavior", flush=True)
print("Result: generated native DBus consent ownership regression checks passed", flush=True)
