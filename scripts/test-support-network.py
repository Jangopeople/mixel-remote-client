#!/usr/bin/env python3
"""Execute actual generated HTTPS mapping, certificate policy and TCP retry paths."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from rust_toolchain import rustc_command

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get("RDREPO", ROOT / "rustdesk"))
TARGETS = ["libs/hbb_common/src/lib.rs", "libs/hbb_common/src/config.rs",
           "libs/hbb_common/src/websocket.rs", "libs/hbb_common/src/socket_client.rs",
           "src/rendezvous_mediator.rs", "src/client.rs", "src/ui_session_interface.rs"]


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
    mediator_tcp = function(mediator, "pub async fn start_tcp(server: ServerPtr, host: String)")
    invalidate_registration = function(mediator, "fn invalidate_mixel_registration(")
    freshness_start = mediator.index("        let mixel_registration = rz.mixel_relay_only")
    freshness_init = mediator[freshness_start:mediator.index("        // Keep all fallible", freshness_start)]
    receive_timeout = function(mediator, "if last_recv_msg.elapsed().as_millis() as u64\n")
    inner = function(mediator, "async fn start_tcp_inner(")
    freshness_finish = inner[inner.index("        rz.invalidate_mixel_registration", inner.index("}.await;")):inner.rindex("\n    }")]
    assert "let result: ResultType<()> = async {" in inner and "}.await;" in inner
    assert inner.index("let result: ResultType<()> = async {") < inner.index("res = conn.next()") < inner.index("}.await;")
    assert inner.index("rz.handle_resp(") < inner.index("}.await;")
    assert freshness_finish.strip().endswith("result")
    unsupported_registration = function(mediator, "if self.mixel_relay_only\n")
    registration_success_start = mediator.index("                        hbb_common::mixel_support_network::registration_succeeded")
    registration_success = mediator[registration_success_start:mediator.index("                        *SOLVING_PK_MISMATCH", registration_success_start)]
    registration_socket_start = mediator.index("            mixel_relay_only: hbb_common::mixel_support_network")
    registration_socket = mediator[registration_socket_start:mediator.index("            host: host.clone(),", registration_socket_start)].strip().removeprefix("mixel_relay_only: ").removesuffix(",")
    intranet_relay_start = mediator.index("        let relay = self.mixel_relay_only || Config::is_proxy();") if "        let relay = self.mixel_relay_only || Config::is_proxy();" in mediator else mediator.index("        let relay = self.mixel_relay_only || Config::is_proxy()\n")
    intranet_relay = mediator[intranet_relay_start:mediator.index(";", intranet_relay_start)+1]
    punch_relay_start = mediator.index("        let relay = self.mixel_relay_only || Config::is_proxy() || ph.force_relay")
    punch_relay = mediator[punch_relay_start:mediator.index(";", punch_relay_start)+1]
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
#[derive(Default)] struct State { server:String, ws:bool, proxy:bool, native_fail:bool, ws_fail:bool, tcp_fail:bool, reject_registration:bool, key_confirmed:bool, host_key_confirmed:bool, online:i64, cached_insecure:Option<bool>, requests:Vec<String> }
thread_local! { static STATE:RefCell<State> = RefCell::new(State { server:"rs.mixel.ch".into(), ..State::default() }); }
struct Config;
const OPTION_RELAY_SERVER:&str="relay-server";
const RENDEZVOUS_PORT:i32=21116;
const RELAY_PORT:i32=21117;
impl Config {
 fn is_proxy()->bool { STATE.with(|s|s.borrow().proxy) }
 fn get_rendezvous_server()->String { STATE.with(|s|s.borrow().server.clone()) }
 fn get_option(key:&str)->String { if key==OPTION_RELAY_SERVER {"rs.mixel.ch".into()} else if key=="allow-websocket" && STATE.with(|s|s.borrow().ws) {"Y".into()} else {String::new()} }
 fn reset_online() {STATE.with(|s|s.borrow_mut().requests.push("reset-online".into()));}
 fn set_key_confirmed(value:bool) {STATE.with(|s|s.borrow_mut().key_confirmed=value);}
 fn set_host_key_confirmed(_host:&str,value:bool) {STATE.with(|s|s.borrow_mut().host_key_confirmed=value);}
 fn update_latency(host:&str,value:i64) {STATE.with(|s|{let mut s=s.borrow_mut();s.online=value;s.requests.push(format!("latency:{host}:{value}"));});}
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
   STATE.with(|s| {let mut s=s.borrow_mut();s.requests.push(url.clone());if s.ws_fail {Err("gateway offline")} else {Ok(url)}})
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
 pub fn reset_for_test() { HTTPS_FALLBACK.store(false, Ordering::SeqCst);REGISTRATION_FAILURES.store(0,Ordering::SeqCst); }
}
type ServerPtr=Arc<String>;
fn is_udp_disabled()->bool { false }
mod hbb_common {pub(crate) use crate::mixel_support_network;}
mod log { pub(crate) use crate::info; }
#[macro_export] macro_rules! info { ($($arg:tt)*) => {{let _=format!($($arg)*);}} }
#[macro_export] macro_rules! bail {($($arg:tt)*)=>{{let _=format!($($arg)*);return Err("unsupported-registration");}}}
async fn sleep(seconds:f32) {STATE.with(|s|s.borrow_mut().requests.push(format!("retry-delay:{seconds}")));}
#[allow(non_camel_case_types)]
#[derive(Clone,Copy,PartialEq)] enum RegisterResult {OK,NOT_SUPPORT}
mod register_pk_response {pub(crate) use crate::RegisterResult as Result;}
struct EnumResult(RegisterResult);impl EnumResult {fn enum_value(&self)->Result<RegisterResult,()> {Ok(self.0)}}
struct RegisterResponse {result:EnumResult}
struct Punch {force_relay:bool}
struct RendezvousMediator {host:String,host_prefix:String,mixel_relay_only:bool}
impl RendezvousMediator {
 async fn start_udp(_server:ServerPtr,host:String)->ResultType<()> {
  STATE.with(|s| {let mut s=s.borrow_mut();s.requests.push(format!("udp:{host}"));if s.native_fail {Err("UDP blocked")} else {Ok(())}})
 }
 async fn start_tcp_inner(_server:ServerPtr,host:String)->ResultType<()> {
  STATE.with(|s|s.borrow_mut().requests.push(format!("tcp:{host}")));
  if STATE.with(|s|s.borrow().tcp_fail) {return Err("gateway disconnected");}
  if STATE.with(|s|s.borrow().reject_registration) {
   let rz=Self{host:host.clone(),host_prefix:host,mixel_relay_only:true};
   return rz.check_registration(RegisterResult::NOT_SUPPORT);
  }
  Ok(())
 }
 fn check_registration(&self,result:RegisterResult)->ResultType<()> {
  let rpr=RegisterResponse{result:EnumResult(result)};
  __UNSUPPORTED_REGISTRATION__
  if result==RegisterResult::OK {__REGISTRATION_SUCCESS__}
  Ok(())
 }
 fn intranet_relay(&self)->bool {__INTRANET_RELAY__ relay}
 fn punch_relay(&self,force_relay:bool)->bool {let ph=Punch{force_relay};__PUNCH_RELAY__ relay}
 __INVALIDATE_REGISTRATION__
 fn begin_registration(&self)->bool {
  let rz=self;
  __FRESHNESS_INIT__
  mixel_registration
 }
 fn finish_registration(&self,mixel_registration:bool,result:ResultType<()>)->ResultType<()> {
  let rz=self;
  __FRESHNESS_FINISH__
 }
 __MEDIATOR_TCP__
 __MEDIATOR_START__
}
__CHECK_WS__
__CONNECT_TCP__
struct Noop; impl Wake for Noop { fn wake(self:Arc<Self>) {} }
fn run<F:Future>(future:F)->F::Output {
 let waker=Waker::from(Arc::new(Noop));let mut context=Context::from_waker(&waker);let mut future=Box::pin(future);
 loop { if let Poll::Ready(result)=future.as_mut().poll(&mut context) {return result;} }
}
fn registered_socket(host:&str,conn:Stream)->RendezvousMediator {
 RendezvousMediator{host:host.into(),host_prefix:host.into(),mixel_relay_only:__REGISTRATION_SOCKET__}
}
fn receive_timer(mixel_registration:bool,keep_alive:i32,elapsed:u64)->ResultType<()> {
 struct KeepAlive {keep_alive:i32}
 struct LastReceived(u64);
 impl LastReceived {fn elapsed(&self)->std::time::Duration {std::time::Duration::from_millis(self.0)}}
 let rz=KeepAlive{keep_alive};let last_recv_msg=LastReceived(elapsed);
 __RECEIVE_TIMEOUT__
 Ok(())
}
#[test] fn actual_incoming_mixel_websocket_deadline_expires_before_session_timeout() {
 let rz=registered_socket("rs.mixel.ch:21116",Stream::WebSocket("Mixel".into()));
 assert!(rz.begin_registration());
 assert_eq!(receive_timer(true,60_000,20_000),Ok(()));
 assert!(receive_timer(true,60_000,20_001).is_err());
 assert!(receive_timer(true,600_000,20_001).is_err());
 // Model receive ages at the bridge's 10s cadence; real receipt waves are
 // exercised separately by the stock-server gateway integration fixture.
 for _ in 0..100 {assert_eq!(receive_timer(true,60_000,10_000),Ok(()));}
 assert_eq!(receive_timer(false,60_000,89_999),Ok(()));
 assert_eq!(receive_timer(false,60_000,90_000),Ok(()));
 assert!(receive_timer(false,60_000,90_001).is_err());
}
#[test] fn actual_registration_start_and_every_scoped_exit_clear_cached_online_and_confirmation() {
 let rz=registered_socket("rs.mixel.ch:21116",Stream::WebSocket("Mixel".into()));
 STATE.with(|s|{let mut s=s.borrow_mut();s.online=20_000_000;s.key_confirmed=true;s.host_key_confirmed=true;});
 let active=rz.begin_registration();assert!(active);
 assert!(STATE.with(|s|{let s=s.borrow();s.online==0&&!s.key_confirmed&&!s.host_key_confirmed}));
 assert_eq!(rz.check_registration(RegisterResult::OK),Ok(()));
 Config::update_latency("rs.mixel.ch:21116",15);
 assert!(STATE.with(|s|{let s=s.borrow();s.online>0&&s.key_confirmed&&s.host_key_confirmed}));
 for result in [Err("EPIPE"),Err("EOF"),Err("invalid-frame"),receive_timer(true,60_000,21_000),Ok(())] {
  STATE.with(|s|{let mut s=s.borrow_mut();s.online=15;s.key_confirmed=true;s.host_key_confirmed=true;});
  assert_eq!(rz.finish_registration(active,result),result);
  assert!(STATE.with(|s|{let s=s.borrow();s.online==0&&!s.key_confirmed&&!s.host_key_confirmed}));
 }
}
#[test] fn actual_native_custom_and_proxy_registration_keep_upstream_readiness_and_timing() {
 for (host,stream,proxy) in [("rs.mixel.ch:21116",Stream::Tcp,false),
  ("other.example:21116",Stream::WebSocket("foreign".into()),false),
  ("rs.mixel.ch:21116",Stream::WebSocket("Mixel".into()),true)] {
  STATE.with(|s|{let mut s=s.borrow_mut();s.proxy=proxy;s.online=42;s.key_confirmed=true;s.host_key_confirmed=true;s.requests.clear();});
  let rz=registered_socket(host,stream);let scoped=rz.begin_registration();assert!(!scoped);
  assert_eq!(rz.finish_registration(scoped,Err("upstream-error")),Err("upstream-error"));
  assert_eq!(receive_timer(scoped,60_000,60_000),Ok(()));
  assert!(STATE.with(|s|{let s=s.borrow();s.online==42&&s.key_confirmed&&s.host_key_confirmed&&s.requests.is_empty()}));
 }
}
#[test] fn actual_expired_registration_is_offline_before_bounded_native_retry() {
 mixel_support_network::reset_for_test();mixel_support_network::enable_https_fallback("rs.mixel.ch");
 let rz=registered_socket("rs.mixel.ch:21116",Stream::WebSocket("Mixel".into()));
 let scoped=rz.begin_registration();rz.check_registration(RegisterResult::OK).unwrap();
 Config::update_latency("rs.mixel.ch:21116",15);
 STATE.with(|s|s.borrow_mut().requests.clear());
 assert!(rz.finish_registration(scoped,receive_timer(scoped,60_000,21_000)).is_err());
 STATE.with(|s|s.borrow_mut().tcp_fail=true);
 assert!(run(RendezvousMediator::start_tcp(Arc::new(String::new()),"rs.mixel.ch:21116".into())).is_err());
 assert_eq!(STATE.with(|s|s.borrow().requests.clone()),vec!["latency:rs.mixel.ch:21116:0","tcp:rs.mixel.ch:21116","reset-online","retry-delay:1"]);
 assert!(!STATE.with(|s|s.borrow().key_confirmed||s.borrow().host_key_confirmed));
}
#[test] fn actual_expired_lane_cannot_supply_cached_confirmation_to_new_registration() {
 let old=registered_socket("rs.mixel.ch:21116",Stream::WebSocket("Mixel".into()));
 let active=old.begin_registration();old.check_registration(RegisterResult::OK).unwrap();
 Config::update_latency("rs.mixel.ch:21116",15);
 assert!(old.finish_registration(active,receive_timer(active,60_000,20_001)).is_err());
 assert!(STATE.with(|s|{let s=s.borrow();s.online==0&&!s.key_confirmed&&!s.host_key_confirmed}));
 let fresh=registered_socket("rs.mixel.ch:21116",Stream::WebSocket("Mixel".into()));
 let fresh_active=fresh.begin_registration();
 assert!(STATE.with(|s|{let s=s.borrow();s.online==0&&!s.key_confirmed&&!s.host_key_confirmed}));
 // Reconfirm only through the generated successful response branch on the
 // new lane, then clear it again if that lane's echo/send subsequently fails.
 fresh.check_registration(RegisterResult::OK).unwrap();
 Config::update_latency("rs.mixel.ch:21116",17);
 assert!(STATE.with(|s|{let s=s.borrow();s.online==17&&s.key_confirmed&&s.host_key_confirmed}));
 assert_eq!(fresh.finish_registration(fresh_active,Err("EPIPE")),Err("EPIPE"));
 assert!(STATE.with(|s|{let s=s.borrow();s.online==0&&!s.key_confirmed&&!s.host_key_confirmed}));
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
#[test] fn actual_failed_https_connect_returns_to_native_after_network_restore() {
 mixel_support_network::reset_for_test();
 STATE.with(|s|{let mut s=s.borrow_mut();s.native_fail=true;s.ws_fail=true;});
 assert_eq!(run(connect_tcp("rs.mixel.ch:21116",100)),Err("gateway offline"));
 assert!(!mixel_support_network::https_fallback_active("rs.mixel.ch"));
 STATE.with(|s|{let mut s=s.borrow_mut();s.native_fail=false;s.requests.clear();});
 assert_eq!(run(connect_tcp("rs.mixel.ch:21116",100)),Ok(Stream::Tcp));
 assert_eq!(STATE.with(|s|s.borrow().requests.clone()),vec!["native:rs.mixel.ch:21116"]);
 // The path when fallback was already active must reset on failure too.
 mixel_support_network::enable_https_fallback("rs.mixel.ch");
 assert_eq!(run(connect_tcp("rs.mixel.ch:21116",100)),Err("gateway offline"));
 assert!(!mixel_support_network::https_fallback_active("rs.mixel.ch"));
 assert_eq!(run(connect_tcp("rs.mixel.ch:21116",100)),Ok(Stream::Tcp));
}
#[test] fn actual_registration_failure_resets_gateway_and_restarts_native_selection() {
 mixel_support_network::reset_for_test();
 let server=Arc::new(String::new());
 for rejected in [false,true] {
  mixel_support_network::enable_https_fallback("rs.mixel.ch");
  STATE.with(|s|{let mut s=s.borrow_mut();s.tcp_fail=!rejected;s.reject_registration=rejected;s.requests.clear();});
  assert!(run(RendezvousMediator::start(server.clone(),"rs.mixel.ch".into())).is_err());
  assert!(!mixel_support_network::https_fallback_active("rs.mixel.ch"));
  assert_eq!(STATE.with(|s|s.borrow().requests.clone()),vec!["tcp:rs.mixel.ch","reset-online",if rejected {"retry-delay:2"} else {"retry-delay:1"}]);
  STATE.with(|s|{let mut s=s.borrow_mut();s.tcp_fail=false;s.reject_registration=false;s.requests.clear();});
  assert_eq!(run(RendezvousMediator::start(server.clone(),"rs.mixel.ch".into())),Ok(()));
  assert_eq!(STATE.with(|s|s.borrow().requests.clone()),vec!["udp:rs.mixel.ch"]);
 }
}
#[test] fn actual_registration_unsupported_is_socket_owned_and_never_confirms_key() {
 mixel_support_network::reset_for_test();
 STATE.with(|s|{let mut s=s.borrow_mut();s.key_confirmed=true;s.host_key_confirmed=true;});
 let rz=RendezvousMediator{host:"rs.mixel.ch".into(),host_prefix:"rs".into(),mixel_relay_only:true};
 assert!(rz.check_registration(RegisterResult::NOT_SUPPORT).is_err());
 assert!(!STATE.with(|s|s.borrow().key_confirmed||s.borrow().host_key_confirmed));
 assert_eq!(rz.check_registration(RegisterResult::OK),Ok(()));
 assert!(STATE.with(|s|s.borrow().key_confirmed&&s.borrow().host_key_confirmed));
 let native=RendezvousMediator{host:"other.example".into(),host_prefix:"other".into(),mixel_relay_only:false};
 assert_eq!(native.check_registration(RegisterResult::NOT_SUPPORT),Ok(()));
}
#[test] fn actual_explicit_websocket_proxy_and_foreign_failures_do_not_change_automatic_policy() {
 mixel_support_network::reset_for_test();let server=Arc::new(String::new());
 for (host,explicit,proxy) in [("rs.mixel.ch",true,false),("rs.mixel.ch",false,true),("other.example",true,false)] {
  mixel_support_network::enable_https_fallback("rs.mixel.ch");
  STATE.with(|s|{let mut s=s.borrow_mut();s.ws=explicit;s.proxy=proxy;s.tcp_fail=true;s.requests.clear();});
  assert!(run(RendezvousMediator::start(server.clone(),host.into())).is_err());
  assert!(mixel_support_network::https_fallback_active("rs.mixel.ch"));
  assert_eq!(STATE.with(|s|s.borrow().requests.clone()),vec![format!("tcp:{host}")]);
 }
 STATE.with(|s|{let mut s=s.borrow_mut();s.ws=false;s.proxy=false;s.requests.clear();});
 assert_eq!(run(RendezvousMediator::start(server,"other.example".into())),Ok(()));
 assert_eq!(STATE.with(|s|s.borrow().requests.clone()),vec!["udp:other.example"]);
 STATE.with(|s|{let mut s=s.borrow_mut();s.ws=true;s.ws_fail=true;});
 assert_eq!(run(connect_tcp("rs.mixel.ch:21116",100)),Err("gateway offline"));
 assert!(mixel_support_network::https_fallback_active("rs.mixel.ch"));
}
#[test] fn actual_active_registration_transport_survives_global_reset_without_direct_peer_probe() {
 mixel_support_network::reset_for_test();
 let ws=registered_socket("rs.mixel.ch:21116",Stream::WebSocket("Mixel".into()));
 assert!(mixel_support_network::reset_https_fallback("rs.mixel.ch"));
 assert!(ws.intranet_relay());assert!(ws.punch_relay(false));
 mixel_support_network::enable_https_fallback("rs.mixel.ch");
 for (endpoint,stream) in [("rs.mixel.ch:21116",Stream::Tcp),("other.example:21116",Stream::WebSocket("foreign".into()))] {
  let native=registered_socket(endpoint,stream);
  assert!(!native.intranet_relay());assert!(!native.punch_relay(false));assert!(native.punch_relay(true));
 }
 STATE.with(|s|s.borrow_mut().ws=true);
 let native=registered_socket("other.example:21116",Stream::Tcp);
 assert!(native.intranet_relay()&&native.punch_relay(false));
}
'''.replace("__TLS_CONNECT__", tls_connect).replace("__HELPER__", (ROOT / "scripts/support-network.rs").read_text(encoding="utf-8")).replace("__CHECK_WS__", function(websocket, "pub fn check_ws(")).replace("__CONNECT_TCP__", connect).replace("__MEDIATOR_START__", mediator_start).replace("__MEDIATOR_TCP__", mediator_tcp).replace("__UNSUPPORTED_REGISTRATION__", unsupported_registration).replace("__REGISTRATION_SUCCESS__", registration_success).replace("__USE_WS__", use_websocket).replace("__INTRANET_RELAY__", intranet_relay).replace("__PUNCH_RELAY__", punch_relay).replace("__REGISTRATION_SOCKET__", registration_socket).replace("__INVALIDATE_REGISTRATION__", invalidate_registration).replace("__FRESHNESS_INIT__", freshness_init).replace("__FRESHNESS_FINISH__", freshness_finish).replace("__RECEIVE_TIMEOUT__", receive_timeout)
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
    subprocess.run(rustc_command([rustc, "--edition=2021", "--deny", "warnings", "--test", str(harness), "-o", str(binary)]), check=True)
    subprocess.run([str(binary), "--test-threads=1"], check=True)
    print("PASS: actual generated HTTPS transport, strict TLS policy, native-first/proxy/custom-server behavior and patch idempotence")

    client = (repo / "src/client.rs").read_text(encoding="utf-8")
    activation_start = client.index("        // Mixel HTTPS relay mode follows this socket")
    activation = client[activation_start:client.index("        let my_addr = socket.local_addr();", activation_start)]
    nat_start = client.index("        let my_nat_type = if interface.is_force_relay()")
    nat = client[nat_start:client.index("        let mut is_local", nat_start)]
    ipv6_start = client.index("        let mut ipv6 = if crate::get_ipv6_punch_enabled()")
    ipv6 = client[ipv6_start:client.index("        let udp_nat_port", ipv6_start)]
    reply_start = client.index("                            let s = udp.0.take();")
    reply = client[reply_start:client.index('                            log::info!("{} Hole Punched', reply_start)]
    relay_ipv6 = function(client, "if let Some(s) = ipv6.0.filter(|_| !interface.is_force_relay())")
    ipv6_test = function(client, "if crate::get_ipv6_punch_enabled() && !interface.is_force_relay()")
    actual_connect = function(client, "    async fn connect(\n        local_addr:")
    init_start = client.index("        // The implicit process-wide fallback belongs")
    initialize = client[init_start:client.index("        if let Some((real_id, server, key))", init_start)]
    save = function(client, "if self.force_relay && !self.mixel_runtime_force_relay")
    reconnect = function((repo / "src/ui_session_interface.rs").read_text(encoding="utf-8"), "if true == force_relay")
    client_harness = r'''
use std::cell::RefCell;
use std::collections::HashMap;
use std::future::Future;
use std::net::SocketAddr;
use std::pin::Pin;
use std::sync::{Arc,RwLock};
use std::task::{Context,Poll,Wake,Waker};
type ResultType<T> = Result<T,&'static str>;
type KcpStream = ();
type ConnectionFuture<'a> = Pin<Box<dyn Future<Output=ResultType<(Stream,Option<KcpStream>,&'static str)>>+'a>>;
const CONNECT_TIMEOUT:u64=1000;
#[derive(Debug,PartialEq)] enum Stream { Tcp,WebSocket(String) }
#[allow(non_camel_case_types)]
#[derive(Clone,Copy,Debug,PartialEq)] enum NatType { SYMMETRIC=1,ASYMMETRIC=2,UNKNOWN_NAT=0 }
#[derive(Clone,Copy)] struct ConnType;
#[derive(Default)] struct Lch { force_relay:bool,mixel_runtime_force_relay:bool,direct_failures:i32,options:HashMap<String,String> }
#[derive(Default)] struct Preferences {websocket:bool,proxy:bool}
thread_local! {static PREFERENCES:RefCell<Preferences> = RefCell::new(Preferences::default());}
struct Config;
impl Config {
 fn get_option(_key:&str)->String {if PREFERENCES.with(|s|s.borrow().websocket) {"Y".into()} else {String::new()}}
 fn is_proxy()->bool {PREFERENCES.with(|s|s.borrow().proxy)}
}
mod config {pub fn option2bool(_key:&str,value:&str)->bool {value=="Y"}}
#[derive(Default)] struct SavedConfig {options:HashMap<String,String>}
impl Lch {
 fn get_option(&self,key:&str)->String {self.options.get(key).cloned().unwrap_or_default()}
 fn initialize(&mut self,force_relay:bool) {__INITIALIZE__}
 fn save_relay(&self,config:&mut SavedConfig) {__SAVE__}
}
trait Interface {
 fn get_lch(&self)->Arc<RwLock<Lch>>;
 fn is_force_relay(&self)->bool {self.get_lch().read().unwrap().force_relay}
 fn update_direct(&self,value:Option<bool>) {STATE.with(|s|s.borrow_mut().push(format!("direct-state:{value:?}")));}
}
struct TestInterface(Arc<RwLock<Lch>>);
impl Interface for TestInterface {fn get_lch(&self)->Arc<RwLock<Lch>> {self.0.clone()}}
struct TestUi {lc:Arc<RwLock<Lch>>}
impl TestUi {fn reconnect(&self,force_relay:bool) {__RECONNECT__}}
fn interface(forced:bool)->TestInterface {TestInterface(Arc::new(RwLock::new(Lch{force_relay:forced,..Lch::default()})))}
thread_local! {static STATE:RefCell<Vec<String>>=RefCell::new(Vec::new());}
fn calls()->Vec<String> {STATE.with(|s|s.borrow().clone())}
fn record(value:impl Into<String>) {STATE.with(|s|s.borrow_mut().push(value.into()));}
#[macro_export] macro_rules! info {($($arg:tt)*)=>{{let _=format!($($arg)*);}}}
#[macro_export] macro_rules! bail {
 ($error:expr)=>{{let _=&$error;return Err("connection-error");}};
 ($format:literal,$($arg:tt)*)=>{{let _=format!($format,$($arg)*);return Err("connection-error");}};
}
#[macro_export] macro_rules! anyhow {($($arg:tt)*)=>{{let _=format!($($arg)*);"forced-relay-disabled"}}}
#[macro_export] macro_rules! allow_err {($value:expr)=>{{let _=$value;}}}
mod log {pub(crate) use crate::info;pub(crate) use crate::info as debug;}
mod hbb_common {pub mod mixel_support_network {__HELPER__}}
fn get_ipv6_punch_enabled()->bool {true}
async fn test_ipv6() {record("ipv6-test");}
async fn get_nat_type(_timeout:u64)->i32 {record("nat-test");NatType::ASYMMETRIC as i32}
struct UdpSocket;
impl UdpSocket {async fn connect(&self,addr:SocketAddr)->ResultType<()> {record(format!("udp-bind:{addr}"));Ok(())}}
async fn get_ipv6_socket()->Option<(Arc<UdpSocket>,Vec<u8>)> {record("ipv6-socket");Some((Arc::new(UdpSocket),vec![1]))}
struct AddrMangle;
impl AddrMangle {fn decode(_bytes:&[u8])->SocketAddr {"127.0.0.1:4444".parse().unwrap()}}
struct Punch {is_udp:bool,socket_addr_v6:Vec<u8>}
struct Relay {socket_addr_v6:Vec<u8>}
trait FutureExt:Future+Sized {
 fn boxed<'a>(self)->Pin<Box<dyn Future<Output=Self::Output>+'a>> where Self:'a {Box::pin(self)}
}
impl<F:Future> FutureExt for F {}
fn connect_tcp_local(peer:SocketAddr,_local:Option<SocketAddr>,_timeout:u64)->impl Future<Output=ResultType<Stream>> {
 record(format!("tcp:{peer}"));async {Ok(Stream::Tcp)}
}
fn udp_nat_connect(_socket:Arc<UdpSocket>,kind:&'static str,_timeout:u64)->impl Future<Output=ResultType<(Stream,Option<KcpStream>,&'static str)>> {
 record(format!("udp-future:{kind}"));async move {Ok((Stream::Tcp,None,kind))}
}
async fn select_ok<'a>(futures:Vec<ConnectionFuture<'a>>)->ResultType<((Stream,Option<KcpStream>,&'static str),Vec<ConnectionFuture<'a>>)> {
 for future in futures {if let Ok(value)=future.await {return Ok((value,Vec::new()));}}
 Err("no-direct-connection")
}
struct Client;
impl Client {
 async fn request_relay(_id:&str,_relay:String,_server:&str,_signed:bool,_key:&str,_token:&str,_kind:ConnType)->ResultType<Stream> {
  record("relay-request");Ok(Stream::WebSocket("authenticated-relay".into()))
 }
 async fn secure_connection(_id:&str,_pk:Vec<u8>,_key:&str,_connection:&mut Stream)->ResultType<Option<Vec<u8>>> {
  record("authenticated-encryption");Ok(Some(vec![7]))
 }
 __ACTUAL_CONNECT__
}
struct Noop;impl Wake for Noop {fn wake(self:Arc<Self>) {}}
fn run<F:Future>(future:F)->F::Output {
 let waker=Waker::from(Arc::new(Noop));let mut context=Context::from_waker(&waker);let mut future=Box::pin(future);
 loop {if let Poll::Ready(result)=future.as_mut().poll(&mut context) {return result;}}
}
fn activate(rendezvous_server:&str,socket:Stream,interface:&TestInterface,mut udp:(Option<u8>,Option<u8>))->(bool,(Option<u8>,Option<u8>)) {
 struct StopUdp;
 impl StopUdp {fn send(self,_value:())->Result<(),()> {record("udp-stop");Ok(())}}
 let mut stop_udp_tx=Some(StopUdp);
 __ACTIVATION__
 (interface.is_force_relay(),udp)
}
#[test] fn actual_socket_detection_stops_background_nat_before_ipv6_setup() {
 let ui=interface(false);
 activate("rs.mixel.ch:21116",Stream::WebSocket("Mixel".into()),&ui,(Some(1),Some(2)));
 run(early_ipv6(&ui));assert_eq!(calls(),vec!["udp-stop"]);
 STATE.with(|s|s.borrow_mut().clear());let ui=interface(false);
 activate("other.example:21116",Stream::Tcp,&ui,(Some(1),Some(2)));
 run(early_ipv6(&ui));assert_eq!(calls(),vec!["ipv6-test"]);
}
async fn nat(interface:&TestInterface)->i32 {__NAT__ my_nat_type}
async fn early_ipv6(interface:&TestInterface) {__IPV6_TEST__}
async fn collect_ipv6(interface:&TestInterface)->(Option<Arc<UdpSocket>>,Option<Vec<u8>>) {
 #[allow(unused_mut)]
 __IPV6__
 ipv6
}
async fn reply_probe(interface:&TestInterface,mut udp:(Option<Arc<UdpSocket>>,Option<u8>),mut ipv6:(Option<Arc<UdpSocket>>,Option<Vec<u8>>)) {
 let ph=Punch{is_udp:true,socket_addr_v6:vec![1]};let peer_addr="127.0.0.1:3333".parse().unwrap();
 __REPLY__
 let _=(udp,ipv6);
}
async fn relay_ipv6(interface:&TestInterface)->usize {
 let ipv6=(Some(Arc::new(UdpSocket)),None::<Vec<u8>>);let rr=Relay{socket_addr_v6:vec![1]};let mut connect_futures=Vec::new();
 __RELAY_IPV6__
 connect_futures.len()
}
#[test] fn actual_socket_mode_never_uses_global_fallback_to_force_native_or_foreign_transport() {
 hbb_common::mixel_support_network::enable_https_fallback("rs.mixel.ch");
 for (endpoint,socket,forced) in [
  ("rs.mixel.ch:21116",Stream::Tcp,false),
  ("other.example:21116",Stream::WebSocket("foreign".into()),false),
  ("rs.mixel.ch.attacker.example:21116",Stream::WebSocket("foreign".into()),false),
  ("rs.mixel.ch:21116",Stream::WebSocket("Mixel".into()),true)] {
  let ui=interface(false);let (actual,udp)=activate(endpoint,socket,&ui,(Some(1),Some(2)));
  assert_eq!(actual,forced);assert_eq!(udp,if forced {(None,None)} else {(Some(1),Some(2))});
 }
}
#[test] fn actual_initialize_preserves_explicit_choices_without_using_process_fallback() {
 hbb_common::mixel_support_network::enable_https_fallback("rs.mixel.ch");
 let mut lch=Lch::default();
 lch.mixel_runtime_force_relay=true;lch.force_relay=true;
 lch.initialize(false);
 assert!(!lch.force_relay&&!lch.mixel_runtime_force_relay);
 lch.initialize(true);assert!(lch.force_relay&&!lch.mixel_runtime_force_relay);
 lch.options.insert("force-always-relay".into(),"Y".into());
 lch.initialize(false);assert!(lch.force_relay&&!lch.mixel_runtime_force_relay);
 lch.options.clear();PREFERENCES.with(|p|p.borrow_mut().websocket=true);
 lch.initialize(false);assert!(lch.force_relay&&!lch.mixel_runtime_force_relay);
 PREFERENCES.with(|p|{let mut p=p.borrow_mut();p.websocket=false;p.proxy=true;});
 lch.initialize(false);assert!(lch.force_relay&&!lch.mixel_runtime_force_relay);
}
#[test] fn actual_runtime_transport_choice_is_not_saved_but_explicit_choices_are() {
 let ui=interface(false);
 activate("rs.mixel.ch:21116",Stream::WebSocket("Mixel".into()),&ui,(Some(1),Some(2)));
 let lch=ui.get_lch();let lch=lch.read().unwrap();
 assert!(lch.force_relay&&lch.mixel_runtime_force_relay);
 let mut saved=SavedConfig::default();lch.save_relay(&mut saved);
 assert!(!saved.options.contains_key("force-always-relay"));
 for (websocket,proxy,command,saved_preference) in [(true,false,false,false),(false,true,false,false),(false,false,true,false),(false,false,false,true)] {
  PREFERENCES.with(|p|{let mut p=p.borrow_mut();p.websocket=websocket;p.proxy=proxy;});
  let ui=interface(false);let lch=ui.get_lch();
  {let mut lch=lch.write().unwrap();if saved_preference {lch.options.insert("force-always-relay".into(),"Y".into());}lch.initialize(command);}
  activate("rs.mixel.ch:21116",Stream::WebSocket("Mixel".into()),&ui,(Some(1),Some(2)));
  let lch=lch.read().unwrap();assert!(lch.force_relay&&!lch.mixel_runtime_force_relay);
  let mut saved=SavedConfig::default();lch.save_relay(&mut saved);
  assert_eq!(saved.options.get("force-always-relay"),Some(&"Y".to_owned()));
 }
}
#[test] fn actual_explicit_reconnect_after_runtime_fallback_preserves_original_saved_choice() {
 for explicit in [false,true] {
  let ui=interface(false);
  activate("rs.mixel.ch:21116",Stream::WebSocket("Mixel".into()),&ui,(Some(1),Some(2)));
  let lch=ui.get_lch();TestUi{lc:lch.clone()}.reconnect(explicit);
  let lch=lch.read().unwrap();
  assert!(lch.force_relay);assert_eq!(lch.mixel_runtime_force_relay,!explicit);
  let mut saved=SavedConfig::default();lch.save_relay(&mut saved);
  assert_eq!(saved.options.contains_key("force-always-relay"),explicit);
 }
}
#[test] fn actual_forced_connect_constructs_no_tcp_udp_ipv6_and_keeps_relay_encryption_path() {
 let ui=interface(true);let peer="127.0.0.1:21118".parse().unwrap();
 let result=run(Client::connect(peer,peer,"peer",vec![1],"rs.mixel.ch","rs.mixel.ch",0,NatType::ASYMMETRIC,0,false,"pin","token",ConnType,ui,Some(Arc::new(UdpSocket)),Some(Arc::new(UdpSocket)),"TCP")).unwrap();
 assert_eq!(calls(),vec!["relay-request","authenticated-encryption"]);
 assert!(!result.1);assert_eq!(result.4,"Relay");assert_eq!(result.2,Some(vec![7]));
}
#[test] fn actual_native_connect_preserves_direct_tcp_udp_ipv6_and_encryption() {
 let ui=interface(false);let peer="127.0.0.1:21118".parse().unwrap();
 let result=run(Client::connect(peer,peer,"peer",vec![1],"foreign.example","foreign.example",0,NatType::ASYMMETRIC,NatType::ASYMMETRIC as i32,false,"pin","token",ConnType,ui,Some(Arc::new(UdpSocket)),Some(Arc::new(UdpSocket)),"TCP")).unwrap();
 assert_eq!(calls(),vec!["tcp:127.0.0.1:21118","udp-future:UDP","udp-future:IPv6","authenticated-encryption"]);
 assert!(result.1);assert_eq!(result.4,"TCP");
}
#[test] fn actual_forced_relay_without_server_fails_instead_of_touching_direct_peer() {
 let ui=interface(true);let peer="127.0.0.1:21118".parse().unwrap();
 assert!(run(Client::connect(peer,peer,"peer",vec![1],"","rs.mixel.ch",0,NatType::SYMMETRIC,0,false,"pin","token",ConnType,ui,None,None,"TCP")).is_err());
 assert!(calls().is_empty());
}
#[test] fn actual_forced_nat_and_ipv6_setup_are_skipped() {
 let ui=interface(true);assert_eq!(run(nat(&ui)),NatType::SYMMETRIC as i32);
 run(early_ipv6(&ui));let sockets=run(collect_ipv6(&ui));assert!(sockets.0.is_none()&&sockets.1.is_none());
 assert!(calls().is_empty());
}
#[test] fn actual_native_nat_and_ipv6_setup_remain_enabled() {
 let ui=interface(false);assert_eq!(run(nat(&ui)),NatType::ASYMMETRIC as i32);
 run(early_ipv6(&ui));let sockets=run(collect_ipv6(&ui));assert!(sockets.0.is_some()&&sockets.1.is_some());
 assert_eq!(calls(),vec!["nat-test","ipv6-test","ipv6-socket"]);
}
#[test] fn actual_forced_punch_and_relay_response_cannot_reactivate_udp_or_ipv6() {
 let ui=interface(true);run(reply_probe(&ui,(Some(Arc::new(UdpSocket)),Some(1)),(Some(Arc::new(UdpSocket)),Some(vec![1]))));
 assert_eq!(run(relay_ipv6(&ui)),0);assert!(calls().is_empty());
}
#[test] fn actual_native_punch_and_relay_response_preserve_udp_and_ipv6() {
 let ui=interface(false);run(reply_probe(&ui,(Some(Arc::new(UdpSocket)),Some(1)),(Some(Arc::new(UdpSocket)),Some(vec![1]))));
 assert_eq!(run(relay_ipv6(&ui)),1);
 assert_eq!(calls(),vec!["udp-bind:127.0.0.1:3333","udp-bind:127.0.0.1:4444","udp-bind:127.0.0.1:4444","udp-future:IPv6"]);
}
'''.replace("__HELPER__", (ROOT / "scripts/support-network.rs").read_text(encoding="utf-8"))
    for marker, generated in {"__ACTUAL_CONNECT__": actual_connect, "__ACTIVATION__": activation,
                              "__NAT__": nat, "__IPV6_TEST__": ipv6_test, "__IPV6__": ipv6,
                              "__REPLY__": reply, "__RELAY_IPV6__": relay_ipv6,
                              "__INITIALIZE__": initialize, "__SAVE__": save,
                              "__RECONNECT__": reconnect}.items():
        client_harness = client_harness.replace(marker, generated)
    harness.write_text(client_harness, encoding="utf-8")
    subprocess.run(rustc_command([rustc, "--edition=2021", "--deny", "warnings", "--test", str(harness), "-o", str(binary)]), check=True)
    subprocess.run([str(binary), "--test-threads=1"], check=True)
    print("PASS: actual generated socket-owned relay mode skips every forced direct TCP/UDP/IPv6 probe and retains native/custom plus relay encryption behavior")

    # Compile both complete generated incoming host paths. Registration stays
    # native while the later ID socket independently changes to WebSocket.
    # Observable mocks record constructing a peer attempt, not just polling it.
    host_harness = r'''
#![allow(dead_code)]
use std::cell::RefCell;
use std::future::Future;
use std::net::SocketAddr;
use std::sync::{Arc,Mutex,MutexGuard,OnceLock};
use std::task::{Context,Poll,Wake,Waker};
use std::time::{Duration,Instant};
type ResultType<T> = Result<T,Box<dyn std::error::Error+Send+Sync>>;
type ServerPtr = Arc<()>;
const CONNECT_TIMEOUT:u64=1000;
#[allow(non_camel_case_types)]
#[derive(Clone,Copy,PartialEq)] enum NatType { SYMMETRIC=1,ASYMMETRIC=2,UNKNOWN_NAT=0 }
impl NatType {fn from_i32(value:i32)->Option<Self> {match value {1=>Some(Self::SYMMETRIC),2=>Some(Self::ASYMMETRIC),_=>None}}}
struct EnumValue(NatType);
mod bytes {pub type Bytes=Vec<u8>;}
mod hbb_common {
 pub mod mixel_support_network {__HELPER__}
 pub mod protobuf {pub trait Enum {fn enum_value(&self)->Result<crate::NatType,()>;}}
 pub(crate) use crate::AddrMangle;
}
impl hbb_common::protobuf::Enum for EnumValue {fn enum_value(&self)->Result<NatType,()> {Ok(self.0)}}
#[derive(Default)] struct State {websocket:bool,disabled:bool,calls:Vec<String>}
thread_local! {static STATE:RefCell<State>=RefCell::new(State::default());}
fn record(value:impl Into<String>) {STATE.with(|s|s.borrow_mut().calls.push(value.into()));}
fn calls()->Vec<String> {STATE.with(|s|s.borrow().calls.clone())}
struct Config;
impl Config {
 fn is_proxy()->bool {false}
 fn get_option(_key:&str)->String {String::new()}
 fn get_nat_type()->i32 {NatType::ASYMMETRIC as i32}
 fn get_id()->String {"123456789".into()}
}
mod config {
 pub fn option2bool(_key:&str,value:&str)->bool {value=="Y"}
 pub fn is_disable_tcp_listen()->bool {crate::STATE.with(|s|s.borrow().disabled)}
}
#[derive(Clone,Default)] struct Permissions;
impl Permissions {fn into_option(self)->Option<u8> {Some(7)}}
#[derive(Clone)] struct FetchLocalAddr {socket_addr:Vec<u8>,socket_addr_v6:Vec<u8>,relay_server:String,control_permissions:Permissions}
struct PunchHole {socket_addr:Vec<u8>,socket_addr_v6:Vec<u8>,relay_server:String,control_permissions:Permissions,nat_type:EnumValue,udp_port:i32,force_relay:bool}
#[derive(Default)] struct PunchHoleSent {socket_addr:Vec<u8>,id:String,relay_server:String,nat_type:EnumValue,version:String,socket_addr_v6:Vec<u8>}
impl Default for EnumValue {fn default()->Self {Self(NatType::ASYMMETRIC)}}
impl From<NatType> for EnumValue {fn from(value:NatType)->Self {Self(value)}}
#[derive(Default)] struct LocalAddr {id:String,socket_addr:Vec<u8>,local_addr:Vec<u8>,relay_server:String,version:String,socket_addr_v6:Vec<u8>}
const VERSION:&str="test";
struct AddrMangle;
impl AddrMangle {
 fn decode(bytes:&[u8])->SocketAddr {if bytes.is_empty() {"127.0.0.1:0"} else {"127.0.0.1:4444"}.parse().unwrap()}
 fn encode(_address:SocketAddr)->Vec<u8> {vec![1]}
}
struct Message {kind:&'static str}
impl Message {
 fn new()->Self {Self{kind:"unset"}}
 fn set_local_addr(&mut self,_value:LocalAddr) {self.kind="local-frame";}
 fn set_punch_hole_sent(&mut self,_value:PunchHoleSent) {self.kind="punch-frame";}
 fn write_to_bytes(&self)->ResultType<Vec<u8>> {Ok(self.kind.as_bytes().to_vec())}
}
enum Stream {Tcp,WebSocket(())}
impl Stream {
 fn local_addr(&self)->SocketAddr {record("address-read");"127.0.0.1:30001".parse().unwrap()}
 async fn send_raw(&mut self,bytes:Vec<u8>)->ResultType<()> {record(String::from_utf8(bytes).unwrap());Ok(())}
}
async fn connect_tcp(_endpoint:&str,_timeout:u64)->ResultType<Stream> {
 if STATE.with(|s|s.borrow().websocket) {record("id-socket:ws");Ok(Stream::WebSocket(()))}
 else {record("id-socket:tcp");Ok(Stream::Tcp)}
}
mod socket_client {
 pub fn connect_tcp_local(_peer:std::net::SocketAddr,_local:Option<std::net::SocketAddr>,_timeout:u64)->impl std::future::Future<Output=crate::ResultType<crate::Stream>> {
  crate::record("tcp-peer-constructed");async {Ok(crate::Stream::Tcp)}
 }
}
async fn start_ipv6(_ipv6:SocketAddr,_peer:SocketAddr,_server:ServerPtr,_permissions:Option<u8>)->Vec<u8> {record("ipv6-peer");vec![6]}
async fn accept_connection(_server:ServerPtr,_socket:Stream,_peer:SocketAddr,secure:bool,_permissions:Option<u8>) {record(format!("accept-secure={secure}"));}
fn is_ipv4(address:&SocketAddr)->bool {address.is_ipv4()}
struct Uuid;
impl Uuid {fn new_v4()->Self {Self}}
impl std::fmt::Display for Uuid {fn fmt(&self,out:&mut std::fmt::Formatter<'_>)->std::fmt::Result {out.write_str("synthetic-relay-uuid")}}
struct Last;
static LAST_MSG:Last=Last;
impl Last {
 async fn lock(&self)->MutexGuard<'static,(SocketAddr,Instant)> {
  static VALUE:OnceLock<Mutex<(SocketAddr,Instant)>>=OnceLock::new();
  VALUE.get_or_init(||Mutex::new(("127.0.0.1:0".parse().unwrap(),Instant::now()))).lock().unwrap()
 }
}
#[macro_export] macro_rules! debug {($($arg:tt)*)=>{{let _=format!($($arg)*);}}}
#[macro_export] macro_rules! allow_err {($value:expr)=>{{let _=$value;}}}
mod log {pub(crate) use crate::debug;}
struct RendezvousMediator {host:String,addr:SocketAddr,mixel_relay_only:bool}
impl RendezvousMediator {
 fn get_relay_server(&self,server:String)->String {server}
 async fn create_relay(&self,_address:Vec<u8>,_relay:String,_uuid:String,_server:ServerPtr,secure:bool,initiate:bool,_ipv6:Vec<u8>,permissions:Option<u8>)->ResultType<()> {
  assert_eq!(permissions,Some(7));record(format!("relay-secure={secure}-initiate={initiate}"));Ok(())
 }
 async fn punch_udp_hole(&self,_peer:SocketAddr,_server:ServerPtr,_message:PunchHoleSent,_permissions:Option<u8>)->ResultType<()> {record("udp-peer");Ok(())}
 __INTRANET__
 __INTRANET_INNER__
 __PUNCH__
}
struct Noop;impl Wake for Noop {fn wake(self:Arc<Self>) {}}
fn run<F:Future>(future:F)->F::Output {
 let waker=Waker::from(Arc::new(Noop));let mut context=Context::from_waker(&waker);let mut future=Box::pin(future);
 loop {if let Poll::Ready(result)=future.as_mut().poll(&mut context) {return result;}}
}
fn setup(host:&str,websocket:bool)->RendezvousMediator {
 *run(LAST_MSG.lock())=("127.0.0.1:0".parse().unwrap(),Instant::now()-Duration::from_secs(1));
 STATE.with(|s|*s.borrow_mut()=State{websocket,..State::default()});
 hbb_common::mixel_support_network::reset_https_fallback("rs.mixel.ch");
 RendezvousMediator{host:host.into(),addr:"127.0.0.1:21116".parse().unwrap(),mixel_relay_only:false}
}
fn intranet()->FetchLocalAddr {FetchLocalAddr{socket_addr:vec![1],socket_addr_v6:vec![6],relay_server:"rs.mixel.ch".into(),control_permissions:Permissions}}
fn punch(udp_port:i32)->PunchHole {PunchHole{socket_addr:vec![1],socket_addr_v6:vec![6],relay_server:"rs.mixel.ch".into(),control_permissions:Permissions,nat_type:EnumValue(NatType::ASYMMETRIC),udp_port,force_relay:false}}
#[test] fn actual_native_registration_then_later_https_intranet_constructs_no_tcp_or_ipv6_peer() {
 let rz=setup("rs.mixel.ch:21116",true);
 run(rz.handle_intranet(intranet(),Arc::new(()))).unwrap();
 assert_eq!(calls(),vec!["id-socket:ws","relay-secure=true-initiate=true"]);
}
#[test] fn actual_native_registration_then_later_https_punch_constructs_no_tcp_or_ipv6_peer() {
 let rz=setup("rs.mixel.ch:21116",true);
 run(rz.handle_punch_hole(punch(0),Arc::new(()))).unwrap();
 assert_eq!(calls(),vec!["id-socket:ws","relay-secure=true-initiate=true"]);
}
#[test] fn actual_native_intranet_retains_ipv6_and_local_tcp_bridge() {
 let rz=setup("rs.mixel.ch:21116",false);
 run(rz.handle_intranet(intranet(),Arc::new(()))).unwrap();
 assert_eq!(calls(),vec!["id-socket:tcp","ipv6-peer","address-read","local-frame","accept-secure=true"]);
}
#[test] fn actual_native_punch_retains_ipv6_and_direct_tcp() {
 let rz=setup("rs.mixel.ch:21116",false);
 run(rz.handle_punch_hole(punch(0),Arc::new(()))).unwrap();
 assert_eq!(calls(),vec!["id-socket:tcp","ipv6-peer","address-read","tcp-peer-constructed","punch-frame","accept-secure=true"]);
}
#[test] fn actual_foreign_websocket_preserves_upstream_custom_server_behavior() {
 for intranet_case in [true,false] {
  let rz=setup("other.example:21116",true);
  if intranet_case {run(rz.handle_intranet(intranet(),Arc::new(()))).unwrap();}
  else {run(rz.handle_punch_hole(punch(0),Arc::new(()))).unwrap();}
  assert!(!calls().iter().any(|value|value.starts_with("relay-secure=")));
  assert!(calls().contains(&"ipv6-peer".to_string()));
  assert!(calls().contains(&"accept-secure=true".to_string()));
 }
}
#[test] fn actual_native_udp_punch_retains_its_original_ipv6_and_udp_path() {
 let rz=setup("rs.mixel.ch:21116",true);
 run(rz.handle_punch_hole(punch(5555),Arc::new(()))).unwrap();
 assert_eq!(calls(),vec!["ipv6-peer","udp-peer"]);
}
'''.replace("__HELPER__", (ROOT / "scripts/support-network.rs").read_text(encoding="utf-8"))
    for marker, signature in {
        "__INTRANET__": "    async fn handle_intranet(&self,",
        "__INTRANET_INNER__": "    async fn handle_intranet_(",
        "__PUNCH__": "    async fn handle_punch_hole(&self,",
    }.items():
        host_harness = host_harness.replace(marker, function(mediator, signature))
    harness.write_text(host_harness, encoding="utf-8")
    subprocess.run(rustc_command([rustc, "--edition=2021", "--deny", "warnings", "--test", str(harness), "-o", str(binary)]), check=True)
    subprocess.run([str(binary), "--test-threads=1"], check=True)
    print("PASS: complete generated host intranet/punch paths route a later HTTPS socket through authenticated relay before constructing direct TCP/IPv6; native/UDP/custom paths retained")
