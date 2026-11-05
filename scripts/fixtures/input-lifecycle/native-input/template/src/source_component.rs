//! Exact source-change production function with only registry/config/native
//! grab/monitoring effects mocked. Invalidation itself calls the actual Session
//! production method included by session_component, not an epoch reimplementation.
use crate::{client::Data,session_component};
use std::sync::{Arc,Mutex,OnceLock,atomic::{AtomicBool,Ordering},mpsc};
type SessionID=usize;
use crate::flutter;
#[derive(Clone)] pub(crate) struct Event;
static NATIVE_INPUT_GATE:Mutex<()>=Mutex::new(());
static NATIVE_INPUT_SUSPENDED:AtomicBool=AtomicBool::new(false);
static LATE_NATIVE:std::sync::atomic::AtomicU8=std::sync::atomic::AtomicU8::new(0);
static CALLBACK_BLOCK:Mutex<Option<(mpsc::Sender<()>,mpsc::Receiver<()>)>>=Mutex::new(None);
static LEAVE_BLOCK:Mutex<Option<(mpsc::Sender<()>,mpsc::Receiver<()>)>>=Mutex::new(None);
fn wait(rx:mpsc::Receiver<()>) {rx.recv_timeout(std::time::Duration::from_secs(10)).unwrap();}

#[derive(Clone)]
pub(crate) struct SourceSession {
    inner:session_component::Session,
    receiver:Arc<Mutex<mpsc::Receiver<Data>>>,
    desktop:bool,
}
impl SourceSession {
    pub(crate) fn is_default(&self)->bool {self.desktop}
    pub(crate) fn invalidate_keyboard_input_source(&self) {self.inner.invalidate_keyboard_input_source();}
    fn epoch(&self)->usize {session_component::source_epoch(&self.inner)}
}
static SESSIONS:OnceLock<Mutex<Vec<SourceSession>>>=OnceLock::new();
static VALUE:Mutex<String>=Mutex::new(String::new());
static EFFECTS:Mutex<Vec<(String,Vec<usize>)>>=Mutex::new(Vec::new());
static IS_RDEV_ENABLED:AtomicBool=AtomicBool::new(false);
pub(crate) static MONITOR_ALLOWED:AtomicBool=AtomicBool::new(true);
fn clear_input_source_state() {
    #[cfg(target_os="macos")] crate::native_conversion::clear_source_fixture();
}
#[cfg(target_os="macos")]
pub(crate) fn perform_boundary_for_native_fixture() {setup();implementation::change_input_source(0,"Input source 1".into());}
fn sessions()->Vec<SourceSession> {SESSIONS.get_or_init(Default::default).lock().unwrap().clone()}
pub(crate) fn get_sessions()->Vec<SourceSession> {sessions()}
fn effect(label:String) {EFFECTS.lock().unwrap().push((label,sessions().iter().map(SourceSession::epoch).collect()));}
mod client {
    use super::*;
    pub(super) fn start_grab_loop() {super::effect("start_grab".into());}
    include!("native_admission_selected.rs");
    #[cfg(feature="original-requirements")]
    include!("native_admission_original.rs");
    fn process_native_event_with_session(_mode:&str,_event:&Event,_locks:Option<i32>,session:&SourceSession,_restore:bool) {
        let block=CALLBACK_BLOCK.lock().unwrap().take();if let Some((ready,blocked))=block {ready.send(()).unwrap();wait(blocked);}
        let mut event=crate::message_proto::KeyEvent{down:true,mode:crate::message_proto::KeyboardMode::Map.into(),..Default::default()};event.set_chr(30);
        session.inner.send_native_key_event(&event,None);super::effect("native_enqueue".into());
    }
}
pub(crate) fn enter_leave(id:usize,enter:bool) {
    if !enter {
        let block=LEAVE_BLOCK.lock().unwrap().take();if let Some((ready,blocked))=block {ready.send(()).unwrap();wait(blocked);}
        match LATE_NATIVE.load(Ordering::SeqCst) {
            1=>client::process_event("map",&Event,None),
            #[cfg(feature="original-requirements")] 2=>client::process_event_r5("map",&Event,None),
            _=>{}
        }
    }
    effect(format!("enter:{id}:{enter}"));
}
mod implementation {
    use super::SessionID;
    const CONFIG_INPUT_SOURCE_1:&str="Input source 1";
    const CONFIG_INPUT_SOURCE_2:&str="Input source 2";
    const CONFIG_OPTION_INPUT_SOURCE:&str="input-source";
    fn get_cur_session_input_source()->String {super::VALUE.lock().unwrap().clone()}
    fn set_local_option(_key:String,value:String) {*super::VALUE.lock().unwrap()=value;super::effect("set_option".into());}
    include!("source_change_selected.rs");
    #[cfg(feature="original-requirements")]
    include!("source_change_original.rs");
}
fn setup()->Vec<SourceSession> {
    let values=[true,true,false].map(|desktop| {
        let (inner,rx)=session_component::source_fixture();SourceSession{inner,receiver:Arc::new(Mutex::new(rx)),desktop}
    }).to_vec();
    *SESSIONS.get_or_init(Default::default).lock().unwrap()=values.clone();
    *VALUE.lock().unwrap()="Input source 2".into();EFFECTS.lock().unwrap().clear();
    IS_RDEV_ENABLED.store(false,Ordering::SeqCst);NATIVE_INPUT_SUSPENDED.store(false,Ordering::SeqCst);LATE_NATIVE.store(0,Ordering::SeqCst);MONITOR_ALLOWED.store(true,Ordering::SeqCst);values
}
#[test] fn actual_global_source_change_queues_owned_barriers_for_all_desktop_sessions_before_activation() {
    let values=setup();implementation::change_input_source(0,"Input source 1".into());
    assert_eq!(values.iter().map(SourceSession::epoch).collect::<Vec<_>>(),[1,1,0]);
    for value in &values[..2] {assert!(matches!(value.receiver.lock().unwrap().try_recv(),Ok(Data::KeyboardSourceChanged(1))));}
    assert!(values[2].receiver.lock().unwrap().try_recv().is_err());
    assert!(IS_RDEV_ENABLED.load(Ordering::SeqCst));
    for (label,epochs) in &*EFFECTS.lock().unwrap() {if label=="start_grab"||label.starts_with("enter:") {assert_eq!(epochs,&[1,1,0]);}}
}
#[test] fn actual_reverse_source_change_preserves_each_sender_barrier_before_leave() {
    let values=setup();implementation::change_input_source(0,"Input source 1".into());
    implementation::change_input_source(1,"Input source 2".into());
    assert_eq!(values.iter().map(SourceSession::epoch).collect::<Vec<_>>(),[2,2,0]);assert!(!IS_RDEV_ENABLED.load(Ordering::SeqCst));
    for value in &values[..2] {
        assert!(matches!(value.receiver.lock().unwrap().try_recv(),Ok(Data::KeyboardSourceChanged(1))));
        assert!(matches!(value.receiver.lock().unwrap().try_recv(),Ok(Data::KeyboardSourceChanged(2))));
    }
    assert!(EFFECTS.lock().unwrap().iter().any(|(label,epochs)|label=="enter:1:false"&&epochs==&[2,2,0]));
}
#[cfg(target_os="macos")]
#[test] fn actual_refused_native_monitoring_keeps_all_owned_sessions_and_source_unchanged() {
    let values=setup();MONITOR_ALLOWED.store(false,Ordering::SeqCst);
    implementation::change_input_source(0,"Input source 1".into());
    assert_eq!(values.iter().map(SourceSession::epoch).collect::<Vec<_>>(),[0,0,0]);
    assert_eq!(*VALUE.lock().unwrap(),"Input source 2");assert!(EFFECTS.lock().unwrap().is_empty());
}
#[test] fn actual_repeated_same_source_does_not_rotate_owned_epochs() {
    let values=setup();implementation::change_input_source(0,"Input source 2".into());
    assert_eq!(values.iter().map(SourceSession::epoch).collect::<Vec<_>>(),[0,0,0]);assert!(EFFECTS.lock().unwrap().is_empty());
}

#[test] fn actual_native_admission_finishes_before_barrier_and_rejects_late_source2_callback() {
    let values=setup();implementation::change_input_source(0,"Input source 1".into());EFFECTS.lock().unwrap().clear();
    let (ready_tx,ready)=mpsc::channel();let (release,blocked)=mpsc::channel();*CALLBACK_BLOCK.lock().unwrap()=Some((ready_tx,blocked));
    let native=std::thread::spawn(||client::process_event("map",&Event,None));wait(ready);assert!(NATIVE_INPUT_GATE.try_lock().is_err());
    LATE_NATIVE.store(1,Ordering::SeqCst);let transition=std::thread::spawn(||implementation::change_input_source(0,"Input source 2".into()));
    release.send(()).unwrap();native.join().unwrap();transition.join().unwrap();
    let effects=EFFECTS.lock().unwrap().clone();let native_effects=effects.iter().filter(|(s,_)|s=="native_enqueue").collect::<Vec<_>>();assert_eq!(native_effects.len(),1);assert_eq!(native_effects[0].1,[1,1,0]);
    assert_eq!(values.iter().map(SourceSession::epoch).collect::<Vec<_>>(),[2,2,0]);assert!(NATIVE_INPUT_SUSPENDED.load(Ordering::SeqCst));
}
#[test] fn actual_cross_window_source_setters_serialize_complete_leave_and_config_publication() {
    setup();implementation::change_input_source(0,"Input source 1".into());
    let (ready_tx,ready)=mpsc::channel();let (release,blocked)=mpsc::channel();*LEAVE_BLOCK.lock().unwrap()=Some((ready_tx,blocked));
    let first=std::thread::spawn(||implementation::change_input_source(0,"Input source 2".into()));wait(ready);
    // The shorter native admission guard is already dropped before leave/OS effects.
    assert!(NATIVE_INPUT_GATE.try_lock().is_ok());
    let second=std::thread::spawn(||implementation::change_input_source(0,"Input source 1".into()));
    release.send(()).unwrap();first.join().unwrap();second.join().unwrap();
    assert_eq!(VALUE.lock().unwrap().as_str(),"Input source 1");assert!(!NATIVE_INPUT_SUSPENDED.load(Ordering::SeqCst));assert!(IS_RDEV_ENABLED.load(Ordering::SeqCst));
}
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_r5_late_native_callback_must_not_enter_after_source_barrier() {
    setup();implementation::change_input_source_r5(0,"Input source 1".into());EFFECTS.lock().unwrap().clear();LATE_NATIVE.store(2,Ordering::SeqCst);
    implementation::change_input_source_r5(0,"Input source 2".into());let effects=EFFECTS.lock().unwrap().clone();
    assert!(!effects.iter().any(|(s,_)|s=="native_enqueue"),"R5 admitted fresh native Down during source2 leave after barrier");
}
#[test] #[cfg(feature="original-requirements")]
fn original_requirement_r5_cross_window_source_setters_must_not_lose_final_intent() {
    setup();implementation::change_input_source_r5(0,"Input source 1".into());
    let (ready_tx,ready)=mpsc::channel();let (release,blocked)=mpsc::channel();*LEAVE_BLOCK.lock().unwrap()=Some((ready_tx,blocked));
    let first=std::thread::spawn(||implementation::change_input_source_r5(0,"Input source 2".into()));wait(ready);
    implementation::change_input_source_r5(0,"Input source 1".into());release.send(()).unwrap();first.join().unwrap();
    let selected=VALUE.lock().unwrap().clone();assert_eq!(selected,"Input source 1","R5 newer source1 request returns early before older source2 publishes");
}
