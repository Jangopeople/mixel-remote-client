#!/usr/bin/env python3
"""Execute actual generated HTTPS mapping, certificate policy and TCP retry paths."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get("RDREPO", ROOT / "rustdesk"))
TARGETS = ["libs/hbb_common/src/lib.rs", "libs/hbb_common/src/config.rs",
           "libs/hbb_common/src/websocket.rs", "libs/hbb_common/src/socket_client.rs",
           "src/rendezvous_mediator.rs"]


def function(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    for end in range(opening + 1, len(source)):
        depth += (source[end] == '{') - (source[end] == '}')
        if depth == 0:
            return source[start:end + 1]
    raise RuntimeError(f"Incomplete generated function: {signature}")


with tempfile.TemporaryDirectory(prefix="mixel-network-tests-") as temporary:
    repo = Path(temporary)
    for path in TARGETS:
        directory, name = UPSTREAM, path
        if path.startswith("libs/hbb_common/"):
            directory, name = UPSTREAM / "libs/hbb_common", path.removeprefix("libs/hbb_common/")
        original = subprocess.run(["git", "-C", str(directory), "show", f"HEAD:{name}"],
                                  check=True, capture_output=True).stdout
        file = repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(original)
    environment = {**os.environ, "RDREPO": str(repo)}
    subprocess.run(["python3", str(ROOT / "scripts/patch-support-network.py")], env=environment, check=True)
    before = {path: (repo / path).read_bytes() for path in TARGETS}
    subprocess.run(["python3", str(ROOT / "scripts/patch-support-network.py")], env=environment, check=True)
    assert before == {path: (repo / path).read_bytes() for path in TARGETS}
    websocket = (repo / TARGETS[2]).read_text(encoding="utf-8")
    socket = (repo / TARGETS[3]).read_text(encoding="utf-8")
    mediator = (repo / TARGETS[4]).read_text(encoding="utf-8")
    # Run generated transport code with observable network boundaries, rather
    # than copying its decisions into tests. Each fallback remains in source.
    connect = function(socket, "pub async fn connect_tcp<")
    connect = connect.replace("T: IntoTargetAddr<'t> + ToSocketAddrs + IsResolvedSocketAddr + std::fmt::Display", "T: std::fmt::Display")
    connect = connect.replace("pub async fn connect_tcp<\n    't,", "pub async fn connect_tcp<")
    tls_connect = function(websocket, "async fn connect(\n")
    mediator_start = function(mediator, "pub async fn start(server: ServerPtr, host: String)")
    use_websocket = function((repo / TARGETS[1]).read_text(encoding="utf-8"), "pub fn use_ws()")
    source = r'''
use std::cell::RefCell;
use std::future::Future;
use std::sync::Arc;
use std::task::{Context, Poll, Wake, Waker};
type ResultType<T> = Result<T, &'static str>;
type WebSocketStream<T> = T;
type MaybeTlsStream<T> = T;
type TcpStream = (TlsType, bool, Option<bool>, Option<bool>);
#[derive(Clone,Copy,Debug,PartialEq)] enum TlsType { Rustls }
#[derive(Debug,PartialEq)] pub enum Stream { Tcp, WebSocket(String) }
#[derive(Default)] struct State { server:String, ws:bool, proxy:bool, native_fail:bool, cached_insecure:Option<bool>, requests:Vec<String> }
thread_local! { static STATE:RefCell<State> = RefCell::new(State { server:"rs.mixel.ch".into(), ..State::default() }); }
struct Config;
const OPTION_RELAY_SERVER:&str="relay-server";
const RENDEZVOUS_PORT:i32=21116;
const RELAY_PORT:i32=21117;
impl Config {
 fn is_proxy()->bool { STATE.with(|s|s.borrow().proxy) }
 fn get_rendezvous_server()->String { STATE.with(|s|s.borrow().server.clone()) }
 fn get_option(key:&str)->String { if key==OPTION_RELAY_SERVER {"rs.mixel.ch".into()} else if key=="allow-websocket" && STATE.with(|s|s.borrow().ws) {"Y".into()} else {String::new()} }
}
mod keys {pub const OPTION_ALLOW_WEBSOCKET:&str="allow-websocket";}
fn option2bool(_option:&str,value:&str)->bool {value=="Y"}
mod config {pub(crate) use crate::option2bool;}
__USE_WS__
fn split_host_port(value:&str)->Option<(String,i32)> { let (h,p)=value.rsplit_once(':')?; Some((h.into(),p.parse().ok()?)) }
fn is_ip_str(value:&str)->bool { value.split(':').next().unwrap_or("").parse::<std::net::IpAddr>().is_ok() }
fn is_ws_endpoint(endpoint:&str)->bool { endpoint.starts_with("ws://")||endpoint.starts_with("wss://") }
fn get_cached_tls_type(_url:&str)->Option<TlsType> {None}
fn get_cached_tls_accept_invalid_cert(_url:&str)->Option<bool> { STATE.with(|s|s.borrow().cached_insecure) }
async fn connect_tcp_local<T:std::fmt::Display>(target:T,_local:Option<()>,_timeout:u64)->ResultType<Stream> {
 STATE.with(|s| { let mut s=s.borrow_mut();s.requests.push(format!("native:{target}"));if s.native_fail {Err("native blocked")} else {Ok(Stream::Tcp)} })
}
mod websocket {
 use super::*;
 pub struct WsFramedStream;
 impl WsFramedStream {
  pub async fn new(url:String,_local:Option<()>,_proxy:Option<()>,_timeout:u64)->ResultType<String> {
   STATE.with(|s|s.borrow_mut().requests.push(url.clone()));Ok(url)
  }
 }
}
struct TlsProbe;
impl TlsProbe {
 async fn try_connect(_url:&str,_timeout:u64,tls:TlsType,cached:bool,insecure:Option<bool>,original:Option<bool>)->ResultType<TcpStream> {
  Ok((tls,cached,insecure,original))
 }
__TLS_CONNECT__
}
mod mixel_support_network {
 __HELPER__
 pub fn reset_for_test() { HTTPS_FALLBACK.store(false, Ordering::SeqCst); }
}
type ServerPtr=Arc<String>;
fn is_udp_disabled()->bool { false }
mod hbb_common {pub(crate) use crate::mixel_support_network;}
mod log { pub(crate) use crate::info; }
#[macro_export] macro_rules! info { ($($arg:tt)*) => {{let _=format!($($arg)*);}} }
struct RendezvousMediator;
impl RendezvousMediator {
 async fn start_udp(_server:ServerPtr,host:String)->ResultType<()> {
  STATE.with(|s| {let mut s=s.borrow_mut();s.requests.push(format!("udp:{host}"));if s.native_fail {Err("UDP blocked")} else {Ok(())}})
 }
 async fn start_tcp(_server:ServerPtr,host:String)->ResultType<()> {
  STATE.with(|s|s.borrow_mut().requests.push(format!("tcp:{host}")));Ok(())
 }
 __MEDIATOR_START__
}
__CHECK_WS__
__CONNECT_TCP__
struct Noop; impl Wake for Noop { fn wake(self:Arc<Self>) {} }
fn run<F:Future>(future:F)->F::Output {
 let waker=Waker::from(Arc::new(Noop));let mut context=Context::from_waker(&waker);let mut future=Box::pin(future);
 loop { if let Poll::Ready(result)=future.as_mut().poll(&mut context) {return result;} }
}
#[test] fn actual_transport_policies() {
 mixel_support_network::reset_for_test();
 // The first healthy Mixel request must remain native.
 assert_eq!(run(connect_tcp("rs.mixel.ch:21116",100)).unwrap(),Stream::Tcp);
 STATE.with(|s| {let mut s=s.borrow_mut();s.native_fail=true;s.requests.clear();});
 assert_eq!(run(connect_tcp("other.example:21116",100)),Err("native blocked"));
 STATE.with(|s|s.borrow_mut().proxy=true);
 assert_eq!(run(connect_tcp("rs.mixel.ch:21116",100)),Err("native blocked"));
 STATE.with(|s| {let mut s=s.borrow_mut();s.proxy=false;s.requests.clear();});
 assert_eq!(run(connect_tcp("rs.mixel.ch:21116",100)).unwrap(),Stream::WebSocket("wss://rs.mixel.ch/ws/id".into()));
 assert_eq!(STATE.with(|s|s.borrow().requests.clone()),vec!["native:rs.mixel.ch:21116","wss://rs.mixel.ch/ws/id"]);
 assert_eq!(run(connect_tcp("rs.mixel.ch:21117",100)).unwrap(),Stream::WebSocket("wss://rs.mixel.ch/ws/relay".into()));
 STATE.with(|s|s.borrow_mut().server="other.example".into());
 assert_eq!(check_ws("other.example:21116"),"other.example:21116");
 STATE.with(|s|s.borrow_mut().ws=true);
 assert_eq!(check_ws("rs.mixel.ch:21116"),"wss://rs.mixel.ch/ws/id");
 assert_eq!(check_ws("rs.mixel.ch:21117"),"wss://rs.mixel.ch/ws/relay");
 assert_eq!(check_ws("other.example:21116"),"ws://other.example/ws/id");
}
#[test] fn actual_mixel_tls_ignores_insecure_cache() {
 STATE.with(|s|s.borrow_mut().cached_insecure=Some(true));
 let (_,_,allow,original)=run(TlsProbe::connect("wss://rs.mixel.ch/ws/id",100)).unwrap();
 assert_eq!(allow,Some(false));assert_eq!(original,Some(false));
 let (_,_,allow,original)=run(TlsProbe::connect("wss://other.example/ws/id",100)).unwrap();
 assert_eq!(allow,Some(true));assert_eq!(original,Some(true));
}
#[test] fn actual_udp_start_retries_mixel_but_preserves_other_servers() {
 mixel_support_network::reset_for_test();
 let server=Arc::new(String::new());
 assert_eq!(run(RendezvousMediator::start(server.clone(),"rs.mixel.ch".into())),Ok(()));
 assert_eq!(STATE.with(|s|s.borrow().requests.clone()),vec!["udp:rs.mixel.ch"]);
 assert!(!mixel_support_network::https_fallback_active("rs.mixel.ch"));
 STATE.with(|s| {let mut s=s.borrow_mut();s.native_fail=true;s.requests.clear();});
 assert_eq!(run(RendezvousMediator::start(server.clone(),"other.example".into())),Err("UDP blocked"));
 assert!(!mixel_support_network::https_fallback_active("rs.mixel.ch"));
 STATE.with(|s|s.borrow_mut().requests.clear());
 assert_eq!(run(RendezvousMediator::start(server,"rs.mixel.ch".into())),Ok(()));
 assert_eq!(STATE.with(|s|s.borrow().requests.clone()),vec!["udp:rs.mixel.ch","tcp:rs.mixel.ch"]);
 assert!(mixel_support_network::https_fallback_active("rs.mixel.ch"));
}
#[test] fn actual_active_fallback_keeps_custom_requests_native() {
 mixel_support_network::reset_for_test();
 assert!(mixel_support_network::enable_https_fallback("rs.mixel.ch"));
 STATE.with(|s|s.borrow_mut().native_fail=true);
 assert_eq!(check_ws("other.example:21116"),"other.example:21116");
 assert_eq!(run(connect_tcp("other.example:21116",100)),Err("native blocked"));
 assert_eq!(STATE.with(|s|s.borrow().requests.clone()),vec!["native:other.example:21116"]);
}
#[test] fn actual_later_proxy_setting_disables_implicit_fallback() {
 mixel_support_network::reset_for_test();
 assert!(mixel_support_network::enable_https_fallback("rs.mixel.ch"));
 STATE.with(|s| {let mut s=s.borrow_mut();s.native_fail=true;s.proxy=true;});
 assert!(!use_ws());
 assert_eq!(check_ws("rs.mixel.ch:21116"),"rs.mixel.ch:21116");
 assert_eq!(run(connect_tcp("rs.mixel.ch:21116",100)),Err("native blocked"));
 assert_eq!(STATE.with(|s|s.borrow().requests.clone()),vec!["native:rs.mixel.ch:21116"]);
}
#[test] fn actual_mixel_request_uses_its_own_ports_when_primary_server_differs() {
 mixel_support_network::reset_for_test();
 STATE.with(|s| {let mut s=s.borrow_mut();s.native_fail=true;s.server="other.example:22116".into();});
 assert_eq!(run(connect_tcp("rs.mixel.ch:21116",100)).unwrap(),Stream::WebSocket("wss://rs.mixel.ch/ws/id".into()));
 assert_eq!(check_ws("rs.mixel.ch:21115"),"wss://rs.mixel.ch/ws/id");
 assert_eq!(check_ws("rs.mixel.ch:21117"),"wss://rs.mixel.ch/ws/relay");
 assert_eq!(check_ws("other.example:22116"),"other.example:22116");
 assert!(!use_ws());
}
'''.replace("__TLS_CONNECT__", tls_connect).replace("__HELPER__", (ROOT / "scripts/support-network.rs").read_text(encoding="utf-8")).replace("__CHECK_WS__", function(websocket, "pub fn check_ws(")).replace("__CONNECT_TCP__", connect).replace("__MEDIATOR_START__", mediator_start).replace("__USE_WS__", use_websocket)
    # UDP registration must leave its retry loop and enter real TCP/WSS startup.
    assert 'if hbb_common::mixel_support_network::enable_https_fallback(&host)' in mediator
    assert 'match Self::start_udp(server.clone(), host.clone()).await' in mediator
    assert 'Self::start_tcp(server, host).await' in mediator
    harness = repo / "network.rs"
    harness.write_text(source, encoding="utf-8")
    rustc = shutil.which("rustc")
    if not rustc:
        raise RuntimeError("Rust is required for actual generated network path tests")
    binary = repo / ("network-tests.exe" if os.name == "nt" else "network-tests")
    subprocess.run([rustc, "--edition=2021", "--deny", "warnings", "--test", str(harness), "-o", str(binary)], check=True)
    subprocess.run([str(binary), "--test-threads=1"], check=True)
    print("PASS: actual generated HTTPS transport, strict TLS policy, native-first/proxy/custom-server behavior and patch idempotence")
