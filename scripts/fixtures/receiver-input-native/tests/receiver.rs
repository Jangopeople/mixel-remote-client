#![allow(dead_code, unused_imports)]
extern crate self as hbb_common;
pub use protobuf;
include!(concat!(env!("OUT_DIR"), "/protos/mod.rs"));
pub use message as message_proto;
use std::sync::{Arc, Mutex, OnceLock, atomic::{AtomicBool, AtomicUsize, Ordering}};

#[derive(Clone, Debug)]
struct Observed { owner: String, peer: i32, down: bool, text: String }
fn observed() -> &'static Mutex<Vec<Observed>> { static V: OnceLock<Mutex<Vec<Observed>>> = OnceLock::new(); V.get_or_init(|| Mutex::new(Vec::new())) }
static PAUSE: AtomicBool = AtomicBool::new(false);
static ENTERED: AtomicBool = AtomicBool::new(false);
static RELEASE_FAILURES: AtomicUsize = AtomicUsize::new(0);
static UNBLOCK_FAILURES: AtomicUsize = AtomicUsize::new(0);
fn input_threads() -> &'static Mutex<Vec<std::thread::ThreadId>> { static V: OnceLock<Mutex<Vec<std::thread::ThreadId>>> = OnceLock::new(); V.get_or_init(|| Mutex::new(Vec::new())) }
fn block_calls() -> &'static Mutex<Vec<(bool,std::thread::ThreadId)>> { static V: OnceLock<Mutex<Vec<(bool,std::thread::ThreadId)>>> = OnceLock::new(); V.get_or_init(|| Mutex::new(Vec::new())) }
mod platform {
    pub fn block_input(enabled: bool) -> (bool,String) {
        if !enabled && super::UNBLOCK_FAILURES.fetch_update(super::Ordering::SeqCst,super::Ordering::SeqCst,|v| v.checked_sub(1)).is_ok() { return (false,"controlled failure".to_owned()); }
        super::block_calls().lock().unwrap().push((enabled,std::thread::current().id())); (true,String::new())
    }
}
mod server {
    pub(crate) mod input_service {
        use super::super::*;
        thread_local! { static OWNER: std::cell::RefCell<Option<(String,Arc<dyn Fn()->bool+Send+Sync>)>> = const { std::cell::RefCell::new(None) }; }
        pub(crate) fn with_receiver_owner_scope_checked(owner:&str,_mouse:i32,allowed:Arc<dyn Fn()->bool+Send+Sync>,_runner:Arc<dyn Fn(Box<dyn FnOnce()+Send>)->bool+Send+Sync>,effect:impl FnOnce()) {
            let old=OWNER.with(|v|v.replace(Some((owner.to_owned(),allowed)))); effect(); OWNER.with(|v|v.replace(old));
        }
        fn admitted() -> Option<String> { OWNER.with(|v|v.borrow().as_ref().filter(|(_,allowed)|allowed()).map(|(owner,_)|owner.clone())) }
        pub(crate) fn handle_key_(event:&message_proto::KeyEvent) {
            input_threads().lock().unwrap().push(std::thread::current().id());
            ENTERED.store(true,Ordering::SeqCst);
            while PAUSE.load(Ordering::SeqCst) && admitted().is_some() { std::thread::sleep(std::time::Duration::from_millis(1)); }
            if let Some(owner)=admitted() { observed().lock().unwrap().push(Observed{owner,peer:0,down:event.down,text:event.seq().to_owned()}); }
        }
        pub(crate) fn handle_mouse_(_event:&message_proto::MouseEvent,conn:i32,_name:String,_argb:u32,_simulate:bool,_cursor:bool) {
            if let Some(owner)=admitted() { observed().lock().unwrap().push(Observed{owner,peer:conn,down:false,text:String::new()}); }
        }
        pub(crate) fn handle_pointer_(_event:&message_proto::PointerDeviceEvent,conn:i32) {
            if let Some(owner)=admitted() { observed().lock().unwrap().push(Observed{owner,peer:conn,down:false,text:String::new()}); }
        }
        pub(crate) fn release_receiver_owner(owner:&str) -> bool { if RELEASE_FAILURES.fetch_update(Ordering::SeqCst,Ordering::SeqCst,|v|v.checked_sub(1)).is_ok() { return false; } observed().lock().unwrap().push(Observed{owner:owner.to_owned(),peer:0,down:false,text:"cleanup".to_owned()}); true }
        pub(crate) fn clear_relative_mouse_active(_conn:i32) {}
    }
    #[cfg(windows)] pub(crate) mod portable_service { pub(crate) mod client {
        pub(crate) static RUNNING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
        pub(crate) fn running()->bool {RUNNING.load(std::sync::atomic::Ordering::SeqCst)}
        pub(crate) fn send_receiver_input(_token:&str,_target:&str,_conn:i32,_action:&super::super::mixel_receiver_input::Action)->bool {false}
    }}
    #[path="../mixel_receiver_input.rs"]
    pub(crate) mod mixel_receiver_input;
}
fn main() {
    #[cfg(windows)] if std::env::args().any(|arg|arg=="--mixel-lease-publisher") { server::mixel_receiver_input::windows_child(); return; }
    server::mixel_receiver_input::run_component_tests();
    println!("PASS: exact receiver capability component; mocked native effects, actual kernel leases and actual Mac dispatch where applicable; application acceptance remains separate");
}
