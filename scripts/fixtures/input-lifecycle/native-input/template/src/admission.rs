//! Executes the exact six production entrypoints via pinned FRB 1.80.1.
//! Only session lookup, event enqueue, native marker effects and Dart reply
//! delivery are mocked. No OS input, native grabs or network call occurs.
use flutter_rust_bridge::{SyncReturn,Handler,WrapInfo,FfiCallMode};
use flutter_rust_bridge::handler::DefaultHandler;
use std::{collections::HashMap,sync::{Arc,Mutex,OnceLock,mpsc},time::Duration};
type SessionID=u32;
#[derive(Clone,Debug,PartialEq,Eq)]
enum Effect { Key(bool), Raw(bool), Legacy(bool), Text(String), Mouse(i32), Pointer }
#[derive(Default)]
struct Session { effects: Mutex<Vec<Effect>> }
impl Session {
    fn get_keyboard_mode(&self)->String {"map".into()}
    fn handle_flutter_key_event(&self,_mode:&str,_character:&str,_usb:i32,_locks:i32,down:bool) {
        self.effects.lock().unwrap().push(Effect::Key(down));
    }
    fn handle_flutter_raw_key_event(&self,_mode:&str,_name:&str,_platform:i32,_position:i32,_locks:i32,down:bool) {
        self.effects.lock().unwrap().push(Effect::Raw(down));
    }
    #[allow(clippy::too_many_arguments)]
    fn input_key(&self,_name:&str,down:bool,_press:bool,_alt:bool,_ctrl:bool,_shift:bool,_command:bool) {
        self.effects.lock().unwrap().push(Effect::Legacy(down));
    }
    fn input_string(&self,value:&str) {self.effects.lock().unwrap().push(Effect::Text(value.into()));}
    #[allow(clippy::too_many_arguments)]
    fn send_mouse(&self,mask:i32,_x:i32,_y:i32,_alt:bool,_ctrl:bool,_shift:bool,_command:bool) {
        self.effects.lock().unwrap().push(Effect::Mouse(mask));
    }
}
mod sessions {
    use super::*;
    static SESSIONS:OnceLock<Mutex<HashMap<SessionID,Arc<Session>>>>=OnceLock::new();
    pub(super) fn install(id:SessionID)->Arc<Session> {
        let session=Arc::new(Session::default());
        SESSIONS.get_or_init(Default::default).lock().unwrap().insert(id,session.clone());session
    }
    pub(super) fn get_session_by_session_id(id:&SessionID)->Option<Arc<Session>> {
        SESSIONS.get_or_init(Default::default).lock().unwrap().get(id).cloned()
    }
}
#[allow(dead_code)]
mod input {
    pub const MOUSE_TYPE_DOWN:i32=1;pub const MOUSE_TYPE_UP:i32=2;pub const MOUSE_TYPE_WHEEL:i32=3;
    pub const MOUSE_TYPE_TRACKPAD:i32=4;pub const MOUSE_TYPE_MOVE_RELATIVE:i32=5;
    pub const MOUSE_BUTTON_LEFT:i32=1;pub const MOUSE_BUTTON_RIGHT:i32=2;pub const MOUSE_BUTTON_WHEEL:i32=4;
    pub const MOUSE_BUTTON_BACK:i32=8;pub const MOUSE_BUTTON_FORWARD:i32=16;
}
mod flutter {
    pub(super) fn session_send_pointer(id:super::SessionID,_msg:String) {
        if let Some(session)=super::sessions::get_session_by_session_id(&id) {
            session.effects.lock().unwrap().push(super::Effect::Pointer);
        }
    }
}
#[allow(dead_code)]
pub(crate) mod keyboard {pub fn set_relative_mouse_mode_state(_active:bool) {}}
#[allow(dead_code)]
mod original {include!("entrypoints_original.rs");}
#[allow(dead_code)]
mod candidate {include!("entrypoints_candidate.rs");}

static SELECTED_SOURCE:Mutex<String>=Mutex::new(String::new());
fn change_input_source(_id:SessionID,value:String) {*SELECTED_SOURCE.lock().unwrap()=value;}
fn try_sync_peer_option(_session:&Session,_id:&SessionID,_key:&str,_value:Option<serde_json::Value>) {}

fn info(mode:FfiCallMode)->WrapInfo {WrapInfo {port:Some(123),debug_name:"controlled_production_input",mode}}
fn normal(task:impl FnOnce()+Send+std::panic::UnwindSafe+'static)->mpsc::Receiver<()> {
    let (tx,rx)=mpsc::channel();
    DefaultHandler::default().wrap(info(FfiCallMode::Normal),move||move|_| {
        task();tx.send(()).unwrap();Ok(())
    });rx
}
fn sync(task:impl FnOnce()->SyncReturn<()>+std::panic::UnwindSafe) {
    let value=DefaultHandler::default().wrap_sync(info(FfiCallMode::Sync),move||Ok(task()));
    assert!(!value.is_null());
    // Matches generated free_WireSyncReturn ownership after its value is read.
    unsafe {drop(Box::from_raw(value));}
}
fn wait(rx:mpsc::Receiver<()>) {rx.recv_timeout(Duration::from_secs(5)).unwrap();}
fn normal_reordering(id:u32)->Vec<Effect> {
    let session=sessions::install(id);let (started_tx,started)=mpsc::channel();let (release,blocked)=mpsc::channel();
    let done_down=normal(move|| {
        started_tx.send(()).unwrap();wait(blocked);
        original::session_handle_flutter_key_event(id,"".into(),0xe1,0,true);
    });wait(started);
    wait(normal(move||original::session_handle_flutter_key_event(id,"".into(),0xe1,0,false)));
    release.send(()).unwrap();wait(done_down);
    let result=session.effects.lock().unwrap().clone();result
}
fn keys_only_sync_still_reorders_mouse(id:u32)->Vec<Effect> {
    let session=sessions::install(id);
    sync(||candidate::session_handle_flutter_key_event(id,"".into(),0xe1,0,true));
    let (started_tx,started)=mpsc::channel();let (release,blocked)=mpsc::channel();
    let done_mouse=normal(move|| {started_tx.send(()).unwrap();wait(blocked);
        original::session_send_mouse(id,r#"{"type":"down","buttons":"left","x":"3","y":"4"}"#.into());
    });wait(started);
    sync(||candidate::session_handle_flutter_key_event(id,"".into(),0xe1,0,false));
    release.send(()).unwrap();wait(done_mouse);
    let result=session.effects.lock().unwrap().clone();result
}
#[test] fn actual_frb_normal_workers_reorder_production_down_up() {
    assert_eq!(normal_reordering(1),[Effect::Key(false),Effect::Key(true)]);
}
#[test] fn actual_frb_keys_only_sync_still_reorders_modified_mouse() {
    assert_eq!(keys_only_sync_still_reorders_mouse(2),[Effect::Key(true),Effect::Key(false),Effect::Mouse(9)]);
}
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_key_down_must_precede_up() {
    assert_eq!(normal_reordering(11),[Effect::Key(true),Effect::Key(false)]);
}
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_modified_click_between_key_down_and_up() {
    assert_eq!(keys_only_sync_still_reorders_mouse(12),[Effect::Key(true),Effect::Mouse(9),Effect::Key(false)]);
}
#[test] fn all_six_sync_production_entrypoints_preserve_per_session_input_order() {
    let a=sessions::install(3);let b=sessions::install(4);
    sync(||candidate::session_handle_flutter_key_event(3,"".into(),0xe1,0,true));
    sync(||candidate::session_handle_flutter_raw_key_event(4,"".into(),56,56,0,true));
    sync(||candidate::session_send_mouse(3,r#"{"type":"down","buttons":"left","x":"3","y":"4"}"#.into()));
    sync(||candidate::session_send_mouse(3,r#"{"type":"wheel","x":"0","y":"1"}"#.into()));
    sync(||candidate::session_send_pointer(3,r#"{"k":"touch"}"#.into()));
    sync(||candidate::session_input_key(3,"VK_CONTROL".into(),true,false,false,false,false,false));
    sync(||candidate::session_input_string(3,"synthetic".into()));
    sync(||candidate::session_handle_flutter_key_event(3,"".into(),0xe1,0,false));
    sync(||candidate::session_handle_flutter_raw_key_event(4,"".into(),56,56,0,false));
    assert_eq!(*a.effects.lock().unwrap(),[Effect::Key(true),Effect::Mouse(9),Effect::Mouse(3),Effect::Pointer,Effect::Legacy(true),Effect::Text("synthetic".into()),Effect::Key(false)]);
    assert_eq!(*b.effects.lock().unwrap(),[Effect::Raw(true),Effect::Raw(false)]);
}
#[test] fn missing_session_and_invalid_mouse_remain_noops_without_late_worker() {
    sync(||candidate::session_handle_flutter_key_event(999,"".into(),0xe1,0,true));
    let session=sessions::install(5);
    sync(||candidate::session_send_mouse(5,"invalid".into()));
    assert!(session.effects.lock().unwrap().is_empty());
}
#[test] fn invalid_relative_activation_remains_dropped_and_sync_returns_normally() {
    let session=sessions::install(6);
    for json in [r#"{"relative_mouse_mode":"1","type":"move","x":"0","y":"0"}"#,
                 r#"{"relative_mouse_mode":"1","type":"move_relative","x":"1","y":"0"}"#,
                 r#"{"relative_mouse_mode":"0"}"#] {
        sync(||candidate::session_send_mouse(6,json.into()));
    }
    assert!(session.effects.lock().unwrap().is_empty());
}

#[test] fn actual_frb_original_source_workers_overwrite_newer_caller_intent() {
    sessions::install(21);let (started_tx,started)=mpsc::channel();let (release,blocked)=mpsc::channel();
    let done_first=normal(move|| {started_tx.send(()).unwrap();wait(blocked);
        original::main_set_input_source(21,"source1".into());});wait(started);
    wait(normal(||original::main_set_input_source(21,"source2".into())));
    release.send(()).unwrap();wait(done_first);
    assert_eq!(*SELECTED_SOURCE.lock().unwrap(),"source1");
}
#[test] fn sync_source_admission_preserves_global_caller_intent_across_sessions() {
    sessions::install(22);sessions::install(23);
    sync(||candidate::main_set_input_source(22,"source1".into()));
    sync(||candidate::main_set_input_source(23,"source2".into()));
    let selected=SELECTED_SOURCE.lock().unwrap().clone();assert_eq!(selected,"source2");
}
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_latest_source_setter_must_win() {
    sessions::install(24);let (started_tx,started)=mpsc::channel();let (release,blocked)=mpsc::channel();
    let first=normal(move|| {started_tx.send(()).unwrap();wait(blocked);original::main_set_input_source(24,"source1".into());});
    wait(started);wait(normal(||original::main_set_input_source(24,"source2".into())));
    release.send(()).unwrap();wait(first);let selected=SELECTED_SOURCE.lock().unwrap().clone();assert_eq!(selected,"source2");
}
