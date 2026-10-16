//! Actual connection input loop/cleanup, ENIGO owner helpers and portable helper
//! exit branches. Only transport/OS effects are replaced with FIFO recorders.
use crate::message_proto::*;
use std::{collections::{HashMap,HashSet,BTreeSet},sync::{Mutex,OnceLock,mpsc as std_mpsc,Arc,atomic::{AtomicBool,Ordering}},future::Future,task::{Wake,Waker,Context,Poll}};
#[allow(dead_code)]mod constants {include!("mouse_constants_selected.rs");}
use constants::input::*;
use permission_info::Permission;
use option_message::BoolOption;
#[derive(Clone,Copy,Debug,PartialEq,Eq,PartialOrd,Ord)]enum MouseButton {Left,Right,Middle,Back,Forward}
struct Enigo {held:BTreeSet<MouseButton>,effects:Vec<(bool,MouseButton)>,fail:bool}
impl Enigo {fn mouse_down(&mut self,button:MouseButton)->Result<(),()> {if self.fail{return Err(());}self.held.insert(button);self.effects.push((true,button));Ok(())}fn mouse_up(&mut self,button:MouseButton){self.held.remove(&button);self.effects.push((false,button));TRACE.lock().unwrap().push("OS-Up");}}
static ENIGO:Mutex<Enigo>=Mutex::new(Enigo{held:BTreeSet::new(),effects:Vec::new(),fail:false});
struct Owners(OnceLock<Mutex<HashMap<i32,HashSet<i32>>>>);
impl Owners {fn lock(&self)->std::sync::LockResult<std::sync::MutexGuard<'_,HashMap<i32,HashSet<i32>>>> {self.0.get_or_init(Default::default).lock()}}
static MOUSE_BUTTON_OWNERS:Owners=Owners(OnceLock::new());
macro_rules! allow_err {($e:expr)=>{let _=$e;}}
include!("button_sink_selected.rs");
#[derive(Clone)]struct Packet {event:MouseEvent,conn:i32,simulate:bool,show:bool}
static ROUTE:Mutex<Vec<Packet>>=Mutex::new(Vec::new());static DEFER:AtomicBool=AtomicBool::new(false);
static TRACE:Mutex<Vec<&'static str>>=Mutex::new(Vec::new());static PUBLISHED:Mutex<Vec<&'static str>>=Mutex::new(Vec::new());
fn sink(packet:Packet) {if packet.simulate {let mut en=ENIGO.lock().unwrap();match packet.event.mask&MOUSE_TYPE_MASK {MOUSE_TYPE_DOWN=>owned_mouse_down(&mut en,packet.conn,packet.event.mask>>3),MOUSE_TYPE_UP=>owned_mouse_up(&mut en,packet.conn,packet.event.mask>>3),_=>{}}}}
fn handle_mouse(event:&MouseEvent,conn:i32,_user:String,_argb:u32,simulate:bool,show:bool){let packet=Packet{event:event.clone(),conn,simulate,show};if DEFER.load(Ordering::SeqCst) {ROUTE.lock().unwrap().push(packet);}else{sink(packet);}}
fn flush(){let packets=std::mem::take(&mut *ROUTE.lock().unwrap());for packet in packets{sink(packet);}}
fn handle_key(_event:&KeyEvent){TRACE.lock().unwrap().push("key");}
fn handle_pointer(_event:&PointerDeviceEvent,_conn:i32){TRACE.lock().unwrap().push("pointer");}
#[cfg(target_os="linux")]fn clear_remapped_keycode(){}
#[cfg(target_os="macos")]fn reset_input_ondisconn(){}
#[cfg(any(target_os="windows",target_os="macos"))]mod rdev {pub fn set_mouse_extra_info(_value:usize){}pub fn set_keyboard_extra_info(_value:usize){}}
#[cfg(any(target_os="windows",target_os="macos"))]mod enigo {pub const ENIGO_INPUT_EXTRA_VALUE:usize=0;}
include!("button_message_selected.rs");
type Sender=();
struct Connection{tx_input:std_mpsc::Sender<MessageInput>,keyboard:bool,disable_keyboard:bool,closed:bool}
impl Connection {
     fn track_input_mouse(input: &InputMouse, held: &mut HashSet<(i32, i32)>) {
        let button = input.msg.mask >> 3;
        if !input.simulate || !matches!(button, MOUSE_BUTTON_LEFT | MOUSE_BUTTON_RIGHT | MOUSE_BUTTON_WHEEL | MOUSE_BUTTON_BACK | MOUSE_BUTTON_FORWARD) { return; }
        match input.msg.mask & MOUSE_TYPE_MASK {
            MOUSE_TYPE_DOWN => { held.insert((input.conn_id, button)); }
            MOUSE_TYPE_UP => { held.remove(&(input.conn_id, button)); }
            _ => {}
        }
    }

    fn release_input_mouse(held: &mut HashSet<(i32, i32)>) {
        for (conn, button) in held.drain() {
            let event = MouseEvent { mask: (button << 3) | MOUSE_TYPE_UP, ..Default::default() };
            // Follow the same Mac main queue / Windows portable-service route as
            // the Down. The actual effect sink rejects unowned/foreign Ups.
            handle_mouse(&event, conn, String::new(), 0, true, false);
        }
    }

    fn release_mouse_input(&self) {
        self.tx_input.send(MessageInput::ReleaseMouse).ok();
    }

    fn handle_input(receiver: std_mpsc::Receiver<MessageInput>, tx: Sender) {
        let mut block_input_mode = false;
        let mut held_mouse_buttons = HashSet::new();
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        {
            rdev::set_mouse_extra_info(enigo::ENIGO_INPUT_EXTRA_VALUE);
            rdev::set_keyboard_extra_info(enigo::ENIGO_INPUT_EXTRA_VALUE);
        }
        #[cfg(target_os = "macos")]
        reset_input_ondisconn();
        loop {
            match receiver.recv_timeout(std::time::Duration::from_millis(500)) {
                Ok(v) => match v {
                    MessageInput::Mouse(mouse_input) => {
                        Self::track_input_mouse(&mouse_input, &mut held_mouse_buttons);
                        handle_mouse(
                            &mouse_input.msg,
                            mouse_input.conn_id,
                            mouse_input.username,
                            mouse_input.argb,
                            mouse_input.simulate,
                            mouse_input.show_cursor,
                        );
                    }
                    MessageInput::ReleaseMouse => Self::release_input_mouse(&mut held_mouse_buttons),
                    MessageInput::Key((mut msg, press)) => {
                        // Set the press state to false, use `down` only in `handle_key()`.
                        msg.press = false;
                        if press {
                            msg.down = true;
                        }
                        handle_key(&msg);
                        if press {
                            msg.down = false;
                            handle_key(&msg);
                        }
                    }
                    MessageInput::Pointer((msg, id)) => {
                        handle_pointer(&msg, id);
                    }
                    MessageInput::BlockOn => {
                        let (ok, msg) = crate::platform::block_input(true);
                        if ok {
                            block_input_mode = true;
                        } else {
                            Self::send_block_input_error(
                                &tx,
                                back_notification::BlockInputState::BlkOnFailed,
                                msg,
                            );
                        }
                    }
                    MessageInput::BlockOff => {
                        let (ok, msg) = crate::platform::block_input(false);
                        if ok {
                            block_input_mode = false;
                        } else {
                            Self::send_block_input_error(
                                &tx,
                                back_notification::BlockInputState::BlkOffFailed,
                                msg,
                            );
                        }
                    }
                    #[cfg(all(feature = "flutter", feature = "plugin_framework"))]
                    #[cfg(not(any(target_os = "android", target_os = "ios")))]
                    MessageInput::BlockOnPlugin(_peer) => {
                        let (ok, _msg) = crate::platform::block_input(true);
                        if ok {
                            block_input_mode = true;
                        }
                        let _r = PLUGIN_BLOCK_INPUT_TX_RX
                            .0
                            .lock()
                            .unwrap()
                            .send(block_input_mode);
                    }
                    #[cfg(all(feature = "flutter", feature = "plugin_framework"))]
                    #[cfg(not(any(target_os = "android", target_os = "ios")))]
                    MessageInput::BlockOffPlugin(_peer) => {
                        let (ok, _msg) = crate::platform::block_input(false);
                        if ok {
                            block_input_mode = false;
                        }
                        let _r = PLUGIN_BLOCK_INPUT_TX_RX
                            .0
                            .lock()
                            .unwrap()
                            .send(block_input_mode);
                    }
                },
                Err(err) => {
                    #[cfg(not(any(target_os = "android", target_os = "ios")))]
                    if block_input_mode {
                        let _ = crate::platform::block_input(true);
                    }
                    if std_mpsc::RecvTimeoutError::Disconnected == err {
                        break;
                    }
                }
            }
        }
        #[cfg(target_os = "linux")]
        clear_remapped_keycode();
        Self::release_input_mouse(&mut held_mouse_buttons);
        log::debug!("Input thread exited");
    }

 async fn close(&mut self) {
        if self.closed {
            return;
        }
        self.closed = true;
        #[cfg(not(any(target_os = "android", target_os = "ios")))]
        self.release_mouse_input();

PUBLISHED.lock().unwrap().push("close");}

 async fn permission(&mut self,enabled:bool) {let conn=self;                                conn.keyboard = enabled;
                                #[cfg(not(any(target_os = "android", target_os = "ios")))]
                                if !enabled { conn.release_mouse_input(); }
                                conn.send_permission(Permission::Keyboard, enabled).await;
}

 fn disable(&mut self,q:BoolOption) {                self.disable_keyboard = q == BoolOption::Yes;
                #[cfg(not(any(target_os = "android", target_os = "ios")))]
                if self.disable_keyboard { self.release_mouse_input(); }
}

     fn peer_keyboard_enabled(&self) -> bool {
        self.keyboard && !self.disable_keyboard
    }
    fn input_mouse(
        &self,
        msg: MouseEvent,
        conn_id: i32,
        username: String,
        argb: u32,
        simulate: bool,
        show_cursor: bool,
    ) {
        self.tx_input
            .send(MessageInput::Mouse(InputMouse {
                msg,
                conn_id,
                username,
                argb,
                simulate,
                show_cursor,
            }))
            .ok();
    }

 #[cfg(feature="original-requirements")]    fn handle_input_original(receiver: std_mpsc::Receiver<MessageInput>, tx: Sender) {
        let mut block_input_mode = false;
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        {
            rdev::set_mouse_extra_info(enigo::ENIGO_INPUT_EXTRA_VALUE);
            rdev::set_keyboard_extra_info(enigo::ENIGO_INPUT_EXTRA_VALUE);
        }
        #[cfg(target_os = "macos")]
        reset_input_ondisconn();
        loop {
            match receiver.recv_timeout(std::time::Duration::from_millis(500)) {
                Ok(v) => match v {
                    MessageInput::ReleaseMouse => {},
                    MessageInput::Mouse(mouse_input) => {
                        handle_mouse(
                            &mouse_input.msg,
                            mouse_input.conn_id,
                            mouse_input.username,
                            mouse_input.argb,
                            mouse_input.simulate,
                            mouse_input.show_cursor,
                        );
                    }
                    MessageInput::Key((mut msg, press)) => {
                        // Set the press state to false, use `down` only in `handle_key()`.
                        msg.press = false;
                        if press {
                            msg.down = true;
                        }
                        handle_key(&msg);
                        if press {
                            msg.down = false;
                            handle_key(&msg);
                        }
                    }
                    MessageInput::Pointer((msg, id)) => {
                        handle_pointer(&msg, id);
                    }
                    MessageInput::BlockOn => {
                        let (ok, msg) = crate::platform::block_input(true);
                        if ok {
                            block_input_mode = true;
                        } else {
                            Self::send_block_input_error(
                                &tx,
                                back_notification::BlockInputState::BlkOnFailed,
                                msg,
                            );
                        }
                    }
                    MessageInput::BlockOff => {
                        let (ok, msg) = crate::platform::block_input(false);
                        if ok {
                            block_input_mode = false;
                        } else {
                            Self::send_block_input_error(
                                &tx,
                                back_notification::BlockInputState::BlkOffFailed,
                                msg,
                            );
                        }
                    }
                    #[cfg(all(feature = "flutter", feature = "plugin_framework"))]
                    #[cfg(not(any(target_os = "android", target_os = "ios")))]
                    MessageInput::BlockOnPlugin(_peer) => {
                        let (ok, _msg) = crate::platform::block_input(true);
                        if ok {
                            block_input_mode = true;
                        }
                        let _r = PLUGIN_BLOCK_INPUT_TX_RX
                            .0
                            .lock()
                            .unwrap()
                            .send(block_input_mode);
                    }
                    #[cfg(all(feature = "flutter", feature = "plugin_framework"))]
                    #[cfg(not(any(target_os = "android", target_os = "ios")))]
                    MessageInput::BlockOffPlugin(_peer) => {
                        let (ok, _msg) = crate::platform::block_input(false);
                        if ok {
                            block_input_mode = false;
                        }
                        let _r = PLUGIN_BLOCK_INPUT_TX_RX
                            .0
                            .lock()
                            .unwrap()
                            .send(block_input_mode);
                    }
                },
                Err(err) => {
                    #[cfg(not(any(target_os = "android", target_os = "ios")))]
                    if block_input_mode {
                        let _ = crate::platform::block_input(true);
                    }
                    if std_mpsc::RecvTimeoutError::Disconnected == err {
                        break;
                    }
                }
            }
        }
        #[cfg(target_os = "linux")]
        clear_remapped_keycode();
        log::debug!("Input thread exited");
    }

 fn send_block_input_error(_tx:&Sender,_state:back_notification::BlockInputState,_msg:String){}
 async fn send_permission(&mut self,_permission:Permission,enabled:bool){PUBLISHED.lock().unwrap().push(if enabled{"grant"}else{"deny"});}
 fn incoming(&self,event:MouseEvent,conn:i32,simulate:bool){if self.peer_keyboard_enabled(){self.input_mouse(event,conn,String::new(),0,simulate,false);}}
}
struct Noop;impl Wake for Noop {fn wake(self:Arc<Self>){}}
fn run<F:Future>(future:F)->F::Output {let waker=Waker::from(Arc::new(Noop));let mut future=Box::pin(future);let mut context=Context::from_waker(&waker);match future.as_mut().poll(&mut context){Poll::Ready(value)=>value,Poll::Pending=>panic!("mock publication unexpectedly awaited")}}
fn reset(){let mut en=ENIGO.lock().unwrap();en.held.clear();en.effects.clear();en.fail=false;MOUSE_BUTTON_OWNERS.lock().unwrap().clear();ROUTE.lock().unwrap().clear();TRACE.lock().unwrap().clear();PUBLISHED.lock().unwrap().clear();DEFER.store(false,Ordering::SeqCst);}
fn event(button:i32,kind:i32)->MouseEvent {MouseEvent{mask:(button<<3)|kind,x:812,y:663,modifiers:vec![ControlKey::Shift.into()],..Default::default()}}
fn setup()->(Connection,std_mpsc::Receiver<MessageInput>){let(tx,rx)=std_mpsc::channel();(Connection{tx_input:tx,keyboard:true,disable_keyboard:false,closed:false},rx)}
fn packet(conn:i32,button:i32,kind:i32)->Packet {Packet{event:event(button,kind),conn,simulate:true,show:false}}
fn queued_close(connid:i32,deferred:bool){reset();DEFER.store(deferred,Ordering::SeqCst);let(conn,rx)=setup();conn.incoming(event(1,MOUSE_TYPE_DOWN),connid,true);drop(conn);Connection::handle_input(rx,());if deferred {assert_eq!(ROUTE.lock().unwrap().len(),2);let packets=ROUTE.lock().unwrap();let up=&packets[1];assert_eq!(up.event.mask,10);assert_eq!((up.event.x,up.event.y),(0,0));assert!(up.event.modifiers.is_empty());assert!(up.simulate&&!up.show);drop(packets);flush();}assert!(ENIGO.lock().unwrap().held.is_empty());assert_eq!(ENIGO.lock().unwrap().effects.len(),2);assert!(MOUSE_BUTTON_OWNERS.lock().unwrap().is_empty());}
#[test]fn actual_queue_drains_accepted_down_then_closes_via_same_direct_or_deferred_route(){queued_close(11,false);queued_close(12,true);}
#[test]fn actual_revoke_marker_clears_denied_up_before_fresh_grant_down(){reset();let(mut conn,rx)=setup();conn.incoming(event(1,MOUSE_TYPE_DOWN),7,true);run(conn.permission(false));conn.incoming(event(1,MOUSE_TYPE_UP),7,true);run(conn.permission(true));conn.incoming(event(1,MOUSE_TYPE_DOWN),7,true);conn.incoming(event(1,MOUSE_TYPE_UP),7,true);drop(conn);Connection::handle_input(rx,());assert_eq!(ENIGO.lock().unwrap().effects,[(true,MouseButton::Left),(false,MouseButton::Left),(true,MouseButton::Left),(false,MouseButton::Left)]);assert!(ENIGO.lock().unwrap().held.is_empty());assert_eq!(*PUBLISHED.lock().unwrap(),["deny","grant"]);}
#[test]fn actual_peer_disable_and_close_markers_release_with_retained_sender_clone_and_are_idempotent(){for close in [false,true]{reset();let(mut conn,rx)=setup();conn.incoming(event(4,MOUSE_TYPE_DOWN),8,true);let retained=conn.tx_input.clone();if close{run(conn.close());run(conn.close());}else{conn.disable(BoolOption::Yes);conn.disable(BoolOption::Yes);}conn.release_mouse_input();drop(conn);drop(retained);Connection::handle_input(rx,());assert_eq!(ENIGO.lock().unwrap().effects,[(true,MouseButton::Middle),(false,MouseButton::Middle)]);assert!(MOUSE_BUTTON_OWNERS.lock().unwrap().is_empty());}}
#[test]fn actual_effect_sink_preserves_foreign_same_button_owners_until_last_owned_up(){for reverse in [false,true]{for button in [1,2,4,8,16]{reset();sink(packet(1,button,1));sink(packet(2,button,1));sink(packet(if reverse{2}else{1},button,2));assert_eq!(ENIGO.lock().unwrap().held.len(),1);assert_eq!(ENIGO.lock().unwrap().effects.len(),2);sink(packet(if reverse{1}else{2},button,2));assert!(ENIGO.lock().unwrap().held.is_empty());assert_eq!(ENIGO.lock().unwrap().effects.len(),3);assert!(MOUSE_BUTTON_OWNERS.lock().unwrap().is_empty());}}}
#[test]fn actual_success_only_deduplicated_owner_and_orphan_up_do_not_release_foreign_buttons(){reset();sink(packet(1,1,1));sink(packet(1,1,1));ENIGO.lock().unwrap().fail=true;sink(packet(1,1,1));sink(packet(2,1,1));sink(packet(2,1,2));assert_eq!(MOUSE_BUTTON_OWNERS.lock().unwrap()[&1],HashSet::from([1]));sink(packet(1,1,2));sink(packet(1,1,2));assert!(ENIGO.lock().unwrap().held.is_empty());assert_eq!(ENIGO.lock().unwrap().effects.len(),3);reset();ENIGO.lock().unwrap().fail=true;sink(packet(3,2,1));sink(packet(3,2,2));assert!(ENIGO.lock().unwrap().effects.is_empty());assert!(MOUSE_BUTTON_OWNERS.lock().unwrap().is_empty());}
#[test]fn actual_cursor_only_unknown_buttons_moves_wheels_never_gain_cleanup_ownership(){reset();let(conn,rx)=setup();conn.incoming(event(1,1),1,false);for (button,kind)in[(0,0),(0,3),(32,1)]{conn.incoming(event(button,kind),1,true);}drop(conn);Connection::handle_input(rx,());assert!(ENIGO.lock().unwrap().effects.is_empty());assert!(MOUSE_BUTTON_OWNERS.lock().unwrap().is_empty());assert!(TRACE.lock().unwrap().is_empty());}
fn publish_will_close(){TRACE.lock().unwrap().push("WillClose");}fn publish_exit(){TRACE.lock().unwrap().push("EXIT");}
include!("button_helper_zero_selected.rs");include!("button_helper_tail_selected.rs");
#[test]fn actual_portable_zero_count_cleans_effect_owners_before_will_close_and_tail_is_inert(){reset();sink(packet(1,1,1));sink(packet(2,1,1));helper_zero(2);assert_eq!(ENIGO.lock().unwrap().held.len(),1);helper_zero(0);helper_tail();assert!(ENIGO.lock().unwrap().held.is_empty());assert_eq!(*TRACE.lock().unwrap(),["OS-Up","WillClose","EXIT"]);assert!(MOUSE_BUTTON_OWNERS.lock().unwrap().is_empty());}
#[test]fn actual_portable_stream_error_and_ping_timeout_tail_release_only_recorded_buttons(){for _exit in["streamErr","ping timeout"]{reset();for button in[1,2,4,8,16]{sink(packet(4,button,1));}helper_tail();helper_tail();assert!(ENIGO.lock().unwrap().held.is_empty());assert_eq!(ENIGO.lock().unwrap().effects.iter().filter(|e|!e.0).count(),5);assert_eq!(TRACE.lock().unwrap().last(),Some(&"EXIT"));}}
#[test]#[cfg(feature="original-requirements")]fn original_requirement_receiver_queue_disconnect_must_release_owned_button(){reset();let(conn,rx)=setup();conn.incoming(event(1,1),1,true);drop(conn);Connection::handle_input_original(rx,());let released=ENIGO.lock().unwrap().held.is_empty();assert!(released,"original queue exits after accepted Down with no owned Up");}

#[test]fn actual_queue_preserves_normal_key_pointer_and_block_dispatch_without_mouse_ownership(){reset();let(conn,rx)=setup();conn.tx_input.send(MessageInput::Key((KeyEvent::new(),true))).unwrap();conn.tx_input.send(MessageInput::Pointer((PointerDeviceEvent::new(),3))).unwrap();conn.tx_input.send(MessageInput::BlockOn).unwrap();conn.tx_input.send(MessageInput::BlockOff).unwrap();drop(conn);Connection::handle_input(rx,());assert_eq!(*TRACE.lock().unwrap(),["key","key","pointer"]);assert!(ENIGO.lock().unwrap().effects.is_empty());}

#[test]
fn peer_actual_close_marker_releases_while_sender_clones_still_open() {
 for deferred in [false,true] {
  reset(); DEFER.store(deferred,Ordering::SeqCst);
  let (mut conn,rx)=setup(); let retained=conn.tx_input.clone();
  let worker=std::thread::spawn(move || Connection::handle_input(rx,()));
  conn.incoming(event(1,MOUSE_TYPE_DOWN),91,true);
  run(conn.close()); run(conn.close());
  retained.send(MessageInput::Key((KeyEvent::new(),false))).unwrap();
  let deadline=std::time::Instant::now()+std::time::Duration::from_secs(3);
  while !TRACE.lock().unwrap().contains(&"key") {
   assert!(std::time::Instant::now()<deadline,"queue failed to reach marker acknowledgement");
   std::thread::sleep(std::time::Duration::from_millis(1));
  }
  // Both senders are deliberately still alive here. EOF cannot explain cleanup.
  if deferred {
   assert_eq!(ROUTE.lock().unwrap().len(),2);
   flush();
  }
  assert!(ENIGO.lock().unwrap().held.is_empty());
  assert_eq!(ENIGO.lock().unwrap().effects,[(true,MouseButton::Left),(false,MouseButton::Left)]);
  assert!(MOUSE_BUTTON_OWNERS.lock().unwrap().is_empty());
  assert_eq!(*PUBLISHED.lock().unwrap(),["close"]);
  drop(conn); drop(retained); worker.join().unwrap();
  assert_eq!(ENIGO.lock().unwrap().effects.len(),2);
 }
}
