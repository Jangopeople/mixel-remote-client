use crate::{client::{Data,keyboard_state::KeyboardState},message_proto::*};
use std::{sync::{Arc,RwLock,atomic::{AtomicUsize,Ordering},mpsc},future::Future,task::{Wake,Waker,Context,Poll}};
struct NoopWake;
impl Wake for NoopWake {fn wake(self:Arc<Self>) {}}
fn run<F:Future>(future:F)->F::Output {
    let mut future=Box::pin(future);let waker=Waker::from(Arc::new(NoopWake));let mut cx=Context::from_waker(&waker);
    match future.as_mut().poll(&mut cx) {Poll::Ready(value)=>value,Poll::Pending=>panic!("mock effects unexpectedly awaited")}
}
#[derive(Clone)]
pub(crate) struct Session {
    server_keyboard_enabled:Arc<RwLock<bool>>,
    keyboard_epoch:Arc<AtomicUsize>,
    sender:Arc<RwLock<Option<mpsc::Sender<Data>>>>,
}
impl Session {
    fn peer_platform(&self)->String {"Windows".into()}
    // Pure stand-in for the existing outbound mapping seam. The production
    // send_native method must apply it to both the event and held templates.
    fn swap_modifier_key(&self,event:&mut KeyEvent) {
        if event.chr()==42 {event.set_chr(125);}
    }
    include!("session_methods.rs");
}
struct Peer {
    messages:Vec<Message>,
    permission_at_send:Vec<bool>,
    flag:Arc<RwLock<bool>>,
    fail_at:Option<usize>,
}
impl Peer {
    async fn send(&mut self,message:&Message)->Result<(),()> {
        if self.fail_at==Some(self.messages.len()) {return Err(());}
        self.permission_at_send.push(*self.flag.read().unwrap());self.messages.push(message.clone());Ok(())
    }
}
struct Remote {handler:Session,keyboard_state:KeyboardState}
impl Remote {include!("remote_methods.rs");}
fn setup()->(Session,mpsc::Receiver<Data>,Remote,Peer) {
    let (tx,rx)=mpsc::channel();let s=Session {server_keyboard_enabled:Arc::new(RwLock::new(true)),keyboard_epoch:Arc::new(AtomicUsize::new(0)),sender:Arc::new(RwLock::new(Some(tx)))};
    let remote=Remote {handler:s.clone(),keyboard_state:KeyboardState::new(true,0)};
    let peer=Peer {messages:vec![],permission_at_send:vec![],flag:s.server_keyboard_enabled.clone(),fail_at:None};
    (s,rx,remote,peer)
}
fn key(code:u32,down:bool)->KeyEvent {let mut e=KeyEvent {down,mode:KeyboardMode::Map.into(),..Default::default()};e.set_chr(code);e}
fn send_mouse(s:&Session,kind:i32) {let mut m=Message::new();m.set_mouse_event(MouseEvent {mask:8|kind,x:731,y:514,..Default::default()});s.send(Data::Message(m));}
fn signature(messages:&[Message])->Vec<String> {
    messages.iter().map(|m|match &m.union {
        Some(message::Union::KeyEvent(e))=>format!("key:{}:{}",e.chr(),e.down),
        Some(message::Union::MouseEvent(e))=>format!("mouse:{}:{}:{}",e.mask,e.x,e.y),
        _=>"other".into()
    }).collect()
}
#[test] fn actual_session_remote_denial_source_handoff_and_fresh_native_input_order() {
    let (s,rx,mut r,mut p)=setup();
    s.send_key_event(&key(42,true));assert!(r.process(rx.recv().unwrap(),&mut p));
    assert!(r.permission(false,&mut p));assert!(!*s.server_keyboard_enabled.read().unwrap());
    s.send_key_event(&key(42,false));assert!(r.process(rx.recv().unwrap(),&mut p));
    s.invalidate_keyboard_input_source();assert!(r.process(rx.recv().unwrap(),&mut p));
    assert!(r.permission(true,&mut p));assert!(*s.server_keyboard_enabled.read().unwrap());
    // Actual Session applies outbound mapping to both physical template and input.
    s.send_native_key_event(&key(30,true),Some(vec![key(42,true)]));assert!(r.process(rx.recv().unwrap(),&mut p));
    s.send_key_event(&key(42,false));assert!(r.process(rx.recv().unwrap(),&mut p));
    s.send_native_key_event(&key(42,false),None);assert!(r.process(rx.recv().unwrap(),&mut p));
    assert_eq!(signature(&p.messages),["key:125:true","key:125:false","key:125:true","key:30:true","key:125:false"]);
    assert!(!p.permission_at_send[1],"cleanup must precede renewed permission publication");
}
#[test] fn actual_session_envelopes_keep_input_fifo_and_denied_mouse_cleanup() {
    let (s,rx,mut r,mut p)=setup();s.send_key_event(&key(42,true));send_mouse(&s,1);s.send_key_event(&key(42,false));
    for _ in 0..3 {assert!(r.process(rx.recv().unwrap(),&mut p));}
    assert_eq!(signature(&p.messages),["key:125:true","mouse:9:731:514","key:125:false"]);
    assert!(r.permission(false,&mut p));send_mouse(&s,2);let old_up=rx.recv().unwrap();
    assert!(r.permission(true,&mut p));assert!(r.process(old_up,&mut p));
    assert_eq!(signature(&p.messages)[3],"mouse:10:0:0");assert!(!p.permission_at_send[3]);
}
#[test] fn actual_session_denied_and_old_enqueues_cannot_become_regained_input() {
    let (s,rx,mut r,mut p)=setup();s.send_key_event(&key(30,true));let before=rx.recv().unwrap();
    assert!(r.permission(false,&mut p));s.send_key_event(&key(31,true));let denied=rx.recv().unwrap();send_mouse(&s,1);let denied_mouse=rx.recv().unwrap();
    assert!(r.permission(true,&mut p));for event in [before,denied,denied_mouse] {assert!(r.process(event,&mut p));}
    assert!(p.messages.is_empty());
}
#[test] fn actual_session_rapid_source_barriers_do_not_roll_back_or_release_twice() {
    let (s,rx,mut r,mut p)=setup();s.send_key_event(&key(42,true));r.process(rx.recv().unwrap(),&mut p);
    for _ in 0..20 {s.invalidate_keyboard_input_source();}
    for _ in 0..20 {assert!(r.process(rx.recv().unwrap(),&mut p));}
    assert_eq!(s.keyboard_epoch.load(Ordering::SeqCst),20);assert_eq!(r.keyboard_state.epoch(),20);
    assert_eq!(signature(&p.messages),["key:125:true","key:125:false"]);
    s.send_native_key_event(&key(30,true),Some(vec![key(42,true)]));r.process(rx.recv().unwrap(),&mut p);
    assert_eq!(signature(&p.messages)[2..],["key:125:true","key:30:true"]);
}
#[test] fn actual_permission_revocation_supersedes_queued_source_barrier() {
    let (s,rx,mut r,mut p)=setup();s.send_key_event(&key(42,true));r.process(rx.recv().unwrap(),&mut p);
    s.invalidate_keyboard_input_source();let source=rx.recv().unwrap();assert!(r.permission(false,&mut p));
    assert_eq!(s.keyboard_epoch.load(Ordering::SeqCst),2);assert!(r.process(source,&mut p));assert_eq!(r.keyboard_state.epoch(),2);
    assert!(r.permission(true,&mut p));assert_eq!(signature(&p.messages),["key:125:true","key:125:false"]);
}
#[test] fn actual_failed_cleanup_write_never_publishes_renewed_grant() {
    let (s,rx,mut r,mut p)=setup();s.send_key_event(&key(42,true));r.process(rx.recv().unwrap(),&mut p);r.permission(false,&mut p);
    p.fail_at=Some(1);assert!(!r.permission(true,&mut p));assert!(!*s.server_keyboard_enabled.read().unwrap());
}
#[test] fn actual_disconnect_drop_does_not_reuse_foreign_session_or_old_sender() {
    let (s,rx,mut r,mut p)=setup();s.send_key_event(&key(42,true));r.process(rx.recv().unwrap(),&mut p);drop(r);drop(rx);drop(s);
    let (fresh,rx,mut r,mut p)=setup();fresh.invalidate_keyboard_input_source();r.process(rx.recv().unwrap(),&mut p);assert!(p.messages.is_empty());
}

pub(crate) fn source_fixture()->(Session,mpsc::Receiver<Data>) {
    let (session,receiver,_remote,_peer)=setup();(session,receiver)
}
pub(crate) fn source_epoch(session:&Session)->usize {session.keyboard_epoch.load(Ordering::SeqCst)}
#[test] fn actual_pan_messages_keep_original_unintercepted_admission_and_no_synthetic_end() {
    let (s,rx,mut r,mut p)=setup();r.permission(false,&mut p);
    for kind in [0,1,2] {
        let mut touch=TouchEvent::new();
        match kind {
            0=>touch.set_pan_start(TouchPanStart{x:20,y:30,..Default::default()}),
            1=>touch.set_pan_update(TouchPanUpdate{x:1,y:2,..Default::default()}),
            _=>touch.set_pan_end(TouchPanEnd{x:20,y:30,..Default::default()}),
        }
        let mut pointer=PointerDeviceEvent::new();pointer.set_touch_event(touch);
        let mut message=Message::new();message.set_pointer_device_event(pointer);
        assert!(!crate::keyboard_state::scoped_pointer_input(&message));
        s.send(Data::Message(message.clone()));
        let data=rx.recv().unwrap();assert!(matches!(data,Data::Message(_)),"Pan path must remain original Message routing");
        assert!(r.process(data,&mut p));assert_eq!(p.messages.last(),Some(&message));
    }
    assert!(r.permission(true,&mut p));assert_eq!(p.messages.len(),3);
}
#[test] fn actual_desktop_scale_update_still_uses_permission_epoch_and_no_owned_contact() {
    let (s,rx,mut r,mut p)=setup();r.permission(false,&mut p);
    let mut touch=TouchEvent::new();touch.set_scale_update(TouchScaleUpdate{scale:12,..Default::default()});
    let mut pointer=PointerDeviceEvent::new();pointer.set_touch_event(touch);
    let mut message=Message::new();message.set_pointer_device_event(pointer);
    assert!(crate::keyboard_state::scoped_pointer_input(&message));s.send(Data::Message(message));
    let queued=rx.recv().unwrap();assert!(matches!(queued,Data::PointerInput(_)));r.permission(true,&mut p);
    assert!(r.process(queued,&mut p));assert!(p.messages.is_empty());
}

#[test] fn actual_scale_owned_end_precedes_key_cleanup_and_grant_publication() {
    let (s,rx,mut r,mut p)=setup();s.send_key_event(&key(42,true));r.process(rx.recv().unwrap(),&mut p);
    s.send(Data::Message(crate::scale_owned::scale_message(12)));r.process(rx.recv().unwrap(),&mut p);r.permission(false,&mut p);
    s.send(Data::Message(crate::scale_owned::scale_message(0)));r.process(rx.recv().unwrap(),&mut p);
    assert!(r.permission(true,&mut p));assert_eq!(p.messages.len(),4);assert_eq!(crate::scale_owned::scale_value(&p.messages[2]),Some(0));assert!(!p.permission_at_send[2]);assert!(!p.permission_at_send[3]);assert!(matches!(p.messages[3].union,Some(message::Union::KeyEvent(_))));
    assert!(*s.server_keyboard_enabled.read().unwrap());assert!(r.permission(true,&mut p));assert_eq!(p.messages.len(),4);
}
#[test] fn actual_scale_source_barrier_releases_only_owned_scale_before_keys() {
    let (s,rx,mut r,mut p)=setup();s.send_key_event(&key(42,true));r.process(rx.recv().unwrap(),&mut p);s.send(Data::Message(crate::scale_owned::scale_message(9)));r.process(rx.recv().unwrap(),&mut p);
    s.invalidate_keyboard_input_source();assert!(r.process(rx.recv().unwrap(),&mut p));assert_eq!(crate::scale_owned::scale_value(&p.messages[2]),Some(0));assert!(matches!(p.messages[3].union,Some(message::Union::KeyEvent(_))));
    s.send(Data::Message(crate::scale_owned::scale_message(0)));r.process(rx.recv().unwrap(),&mut p);assert_eq!(p.messages.len(),4);
}
#[test] fn actual_scale_end_replays_owned_control_immediately_after_end_before_next_ui_key() {
    let (s,rx,mut r,mut p)=setup();s.send_key_event(&key(29,true));r.process(rx.recv().unwrap(),&mut p);s.send(Data::Message(crate::scale_owned::scale_message(4)));r.process(rx.recv().unwrap(),&mut p);s.send(Data::Message(crate::scale_owned::scale_message(0)));r.process(rx.recv().unwrap(),&mut p);s.send_key_event(&key(30,true));r.process(rx.recv().unwrap(),&mut p);
    assert_eq!(crate::scale_owned::scale_value(&p.messages[2]),Some(0));assert_eq!(signature(&p.messages)[3..],["key:29:true","key:30:true"]);
}
#[test] fn actual_scale_cleanup_with_control_never_restores_old_owner_and_failed_end_never_grants() {
    let (s,rx,mut r,mut p)=setup();s.send_key_event(&key(29,true));r.process(rx.recv().unwrap(),&mut p);s.send(Data::Message(crate::scale_owned::scale_message(4)));r.process(rx.recv().unwrap(),&mut p);r.permission(false,&mut p);assert!(r.permission(true,&mut p));assert_eq!(crate::scale_owned::scale_value(&p.messages[2]),Some(0));assert_eq!(signature(&p.messages)[3],"key:29:false");assert_eq!(p.messages.len(),4);
    let (s,rx,mut r,mut p)=setup();s.send(Data::Message(crate::scale_owned::scale_message(4)));r.process(rx.recv().unwrap(),&mut p);r.permission(false,&mut p);p.fail_at=Some(1);assert!(!r.permission(true,&mut p));assert!(!*s.server_keyboard_enabled.read().unwrap());
}
