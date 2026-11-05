//! Verbatim original Session send methods and server permission predicate.
//! Channel delivery and OS key effects are mocked; this is a component
//! reproduction, not a live native session attribution.
use crate::{client::Data,message_proto::*};
use std::{collections::BTreeSet,sync::{Arc,RwLock,mpsc}};
struct Session {sender:Arc<RwLock<Option<mpsc::Sender<Data>>>>}
impl Session {
    fn swap_modifier_key(&self,_event:&mut KeyEvent) {}
    pub fn send_key_event(&self, evt: &KeyEvent) {
        // mode: legacy(0), map(1), translate(2), auto(3)

        let mut msg = evt.clone();
        self.swap_modifier_key(&mut msg);
        let mut msg_out = Message::new();
        msg_out.set_key_event(msg);
        self.send(Data::Message(msg_out));
    }

    fn send(&self, data: Data) {
        if let Some(sender) = self.sender.read().unwrap().as_ref() {
            sender.send(data).ok();
        }
    }
}
struct Server {keyboard:bool,disable_keyboard:bool,held:BTreeSet<u32>}
impl Server {
    fn peer_keyboard_enabled(&self) -> bool {
        self.keyboard && !self.disable_keyboard
    }
    fn consume(&mut self,data:Data) {
        let Data::Message(message)=data else {panic!("original sender must emit original Message");};
        let Some(message::Union::KeyEvent(event))=message.union else {panic!("key expected");};
        if self.peer_keyboard_enabled() {
            if event.down {self.held.insert(event.chr());} else {self.held.remove(&event.chr());}
        }
    }
}
fn event(code:u32,down:bool)->KeyEvent {let mut e=KeyEvent {down,mode:KeyboardMode::Map.into(),..Default::default()};e.set_chr(code);e}
#[test] fn normal_permitted_key_path_has_balanced_original_down_up() {
    let (tx,rx)=mpsc::channel();let s=Session{sender:Arc::new(RwLock::new(Some(tx)))};
    let mut server=Server{keyboard:true,disable_keyboard:false,held:BTreeSet::new()};
    for down in [true,false] {s.send_key_event(&event(42,down));server.consume(rx.recv().unwrap());}
    assert!(server.held.is_empty());
}
#[test] fn original_requirement_revoked_ui_up_then_native_resume_must_clear_old_hold() {
    let (tx,rx)=mpsc::channel();let s=Session{sender:Arc::new(RwLock::new(Some(tx)))};
    let mut server=Server{keyboard:true,disable_keyboard:false,held:BTreeSet::new()};
    s.send_key_event(&event(42,true));server.consume(rx.recv().unwrap());
    server.keyboard=false;s.send_key_event(&event(42,false));server.consume(rx.recv().unwrap());
    // Source1's first native key goes through the same original Session method;
    // no intervening Dart pointer/key boundary drains its private pending up.
    server.keyboard=true;s.send_key_event(&event(30,true));server.consume(rx.recv().unwrap());
    assert!(!server.held.contains(&42),"old remote Shift remains down after real permission regrant and first native key");
}
