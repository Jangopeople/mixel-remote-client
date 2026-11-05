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
        pub fn send_key_event(&self, evt: &KeyEvent) {
        // mode: legacy(0), map(1), translate(2), auto(3)

        let mut msg = evt.clone();
        self.swap_modifier_key(&mut msg);
        let mut msg_out = Message::new();
        msg_out.set_key_event(msg);
        self.send(Data::Message(msg_out));
    }
    pub fn send_native_key_event(&self, evt: &KeyEvent, modifiers: Option<Vec<KeyEvent>>) {
        use crate::client::keyboard_state::{KeyboardInput, KeyboardOrigin};
        let mut event = evt.clone();
        self.swap_modifier_key(&mut event);
        let modifiers = modifiers.map(|events| events.into_iter().map(|mut event| {
            self.swap_modifier_key(&mut event);
            event
        }).collect());
        let permitted = self.server_keyboard_enabled.read().unwrap();
        let input = KeyboardInput {
            event,
            origin: KeyboardOrigin::Native,
            epoch: self.keyboard_epoch.load(Ordering::SeqCst),
            permitted: *permitted,
            modifiers,
        };
        self.send(Data::KeyboardInput(input));
    }
    pub fn invalidate_keyboard_input_source(&self) {
        // Hold the same lock used by input enqueue until the barrier is queued.
        // This rotates ownership; it does not alter server permission.
        let _permission = self.server_keyboard_enabled.write().unwrap();
        let epoch = self.keyboard_epoch.fetch_add(1, Ordering::SeqCst).wrapping_add(1);
        self.send(Data::KeyboardSourceChanged(epoch));
    }
    fn send(&self, data: Data) {
        use crate::client::keyboard_state::{KeyboardInput, KeyboardOrigin};
        // Stamp every UI KeyEvent at the common Session enqueue boundary,
        // including legacy input_string/password paths. Denied and old queued
        // events cannot become new input when the grant is renewed.
        let data = match data {
            Data::Message(mut message) if matches!(message.union, Some(message::Union::KeyEvent(_))) => {
                let Some(message::Union::KeyEvent(event)) = message.union.take() else { unreachable!() };
                let permitted = self.server_keyboard_enabled.read().unwrap();
                Data::KeyboardInput(KeyboardInput {
                    event,
                    origin: KeyboardOrigin::Ui,
                    epoch: self.keyboard_epoch.load(Ordering::SeqCst),
                    permitted: *permitted,
                    modifiers: None,
                })
            }
            Data::Message(message) if crate::client::keyboard_state::scoped_pointer_input(&message) => {
                let permitted = self.server_keyboard_enabled.read().unwrap();
                Data::PointerInput((message, self.keyboard_epoch.load(Ordering::SeqCst), *permitted))
            }
            data => data,
        };
        if let Some(sender) = self.sender.read().unwrap().as_ref() {
            sender.send(data).ok();
        }
    }
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
impl Remote {    fn process(&mut self, data:Data, peer:&mut Peer)->bool {
        run(async {
            match data {
            Data::KeyboardSourceChanged(epoch) => {
                // A later source change or permission revoke supersedes this
                // queued barrier; only the exact current epoch can resume.
                if epoch == self.handler.keyboard_epoch.load(Ordering::SeqCst) {
                    let releases = self.keyboard_state.change_source(epoch);
                    if let Some(message) = self.keyboard_state.take_scale_release() {
                        if peer.send(&message).await.is_err() { return false; }
                    }
                    for event in releases {
                        let mut message = Message::new();
                        message.set_key_event(event);
                        if peer.send(&message).await.is_err() {
                            return false;
                        }
                    }
                }
            }
            Data::KeyboardInput(input) => {
                for event in self.keyboard_state.prepare_input(input) {
                    let mut message = Message::new();
                    message.set_key_event(event);
                    if peer.send(&message).await.is_err() {
                        return false;
                    }
                }
            }
            Data::PointerInput((message, epoch, permitted)) => {
                self.keyboard_state.set_windows_peer(self.handler.peer_platform().eq_ignore_ascii_case("Windows"));
                if let Some(message) = self.keyboard_state.prepare_pointer(message, epoch, permitted) {
                    if peer.send(&message).await.is_err() { return false; }
                    for event in self.keyboard_state.take_scale_controls() {
                        let mut message = Message::new();
                        message.set_key_event(event);
                        if peer.send(&message).await.is_err() { return false; }
                    }
                }
            }
                Data::Message(message) => { if peer.send(&message).await.is_err() { return false; } },
            }
            true
        })
    }
    fn permission(&mut self, enabled:bool, peer:&mut Peer)->bool {
        struct Permission {enabled:bool}
        let p=Permission {enabled};
        run(async {
                                // The IO loop is the sole owner of outgoing key
                                // order. Release this connection's old holds before
                                // publishing a renewed grant to either input source.
                                let releases = self.keyboard_state.set_permission(p.enabled);
                                if !p.enabled {
                                    let mut enabled = self.handler.server_keyboard_enabled.write().unwrap();
                                    if *enabled {
                                        self.handler.keyboard_epoch.fetch_add(1, Ordering::SeqCst);
                                    }
                                    self.keyboard_state.adopt_epoch(self.handler.keyboard_epoch.load(Ordering::SeqCst));
                                    *enabled = false;
                                }
                                if let Some(message) = self.keyboard_state.take_scale_release() {
                                    if peer.send(&message).await.is_err() { return false; }
                                }
                                for event in self.keyboard_state.take_button_releases() {
                                    let mut message = Message::new();
                                    message.set_mouse_event(event);
                                    if peer.send(&message).await.is_err() {
                                        return false;
                                    }
                                }
                                for event in releases {
                                    let mut message = Message::new();
                                    message.set_key_event(event);
                                    if peer.send(&message).await.is_err() {
                                        return false;
                                    }
                                }
                                if p.enabled {
                                    let mut enabled = self.handler.server_keyboard_enabled.write().unwrap();
                                    self.keyboard_state.adopt_epoch(self.handler.keyboard_epoch.load(Ordering::SeqCst));
                                    *enabled = true;
                                }
            true
        })
    }
}
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
