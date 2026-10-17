//! Linux own-uinput final-sink capability and actual-device receipt owner.
//! A pipe write is never an input receipt. The separate revoke page bypasses FIFO.
use super::*;
use std::{collections::HashMap, os::unix::io::{AsRawFd, RawFd}, sync::{Arc, Mutex, OnceLock, atomic::{AtomicU64, Ordering}}};
#[path = "mixel_receiver_uinput_core.rs"]
mod core;
use core::{Claim, Ledger};

const PAGE: usize = 4096;
const WORD: usize = 64;
pub(super) const KEYBOARD: u64 = 1;
pub(super) const MOUSE: u64 = 2;
const SEALS: i32 = libc::F_SEAL_SEAL | libc::F_SEAL_SHRINK | libc::F_SEAL_GROW;
static NEXT_REQUEST: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub(crate) struct WireLease { nonce: String, revoke_fd: i32, ack_fd: i32 }
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub(crate) enum Operation {
    Bind,
    Key { alias: String, physical: Option<u32>, temporary: u64, down: bool, repeat: bool },
    RetireOperation(u64),
    Pressed(u32),
    Keyboard(DataKeyboard, u64),
    Mouse(DataMouse, u64),
}
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub(crate) struct Packet { lease: WireLease, sequence: u64, operation: Operation }
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub(crate) struct Reply { nonce: String, sequence: u64, accepted: bool, pressed: bool }

#[path = "mixel_receiver_uinput_kernel.rs"]
mod kernel;
pub(crate) use kernel::Peer;
use kernel::{Publisher, Consumer};

fn nonce_valid(nonce: &str) -> bool { nonce.len()==32 && nonce.bytes().all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase()) }
fn publishers() -> &'static Mutex<HashMap<String,Arc<Publisher>>> { static VALUE:OnceLock<Mutex<HashMap<String,Arc<Publisher>>>>=OnceLock::new();VALUE.get_or_init(||Mutex::new(HashMap::new())) }
pub(crate) fn register(owner: &str) -> bool {
    let mut registry=publishers().lock().unwrap_or_else(|error|error.into_inner());
    if registry.contains_key(owner) { return false; }
    if registry.len()>=1024 { return false; }
    let nonce=if nonce_valid(owner){owner.to_owned()}else{uuid::Uuid::new_v4().simple().to_string()};
    let Some(value)=Publisher::create(&nonce) else { return false; };
    registry.insert(owner.to_owned(),Arc::new(value));true
}
fn publisher(owner: &str) -> Option<Arc<Publisher>> {
    if owner=="@trusted-native-input" && !publishers().lock().ok()?.contains_key(owner) { let _=register(owner); }
    publishers().lock().ok()?.get(owner).cloned()
}
pub(crate) fn revoke(owner: &str) { if let Some(value)=publisher(owner) { if !value.revoke.revoke() {value.failed.store(true,Ordering::SeqCst);} } }
pub(crate) fn healthy(owner: &str) -> bool { publisher(owner).map(|value|!value.failed.load(Ordering::SeqCst) && !value.denied()).unwrap_or(false) }
pub(crate) fn retired(owner: &str) -> bool {
    let mut registry=publishers().lock().unwrap_or_else(|error|error.into_inner());
    let Some(value)=registry.get(owner) else { return true; };
    if !value.retired() { return false; }
    registry.remove(owner);true
}
fn scope() -> Result<(Arc<Publisher>,u64),String> {
    let (owner,operation)=super::super::input_service::receiver_uinput_context().ok_or("unscoped uinput mutation refused")?;
    let value=publisher(&owner).ok_or("unknown uinput receiver generation")?;
    Ok((value,operation))
}
fn next_sequence() -> Result<u64,String> { NEXT_REQUEST.fetch_update(Ordering::SeqCst,Ordering::SeqCst,|value|value.checked_add(1)).map_err(|_|"uinput request identity exhausted".into()) }
fn is_cleanup(operation: &Operation) -> bool { matches!(operation,Operation::RetireOperation(_) | Operation::Key { down:false,.. } | Operation::Keyboard(DataKeyboard::KeyUp(_),_) | Operation::Mouse(DataMouse::Up(_),_)) }
pub(super) fn request(stream: &mut Connection, runtime: &Runtime, bound: &mut std::collections::HashSet<String>, peer: &Peer, kind: u64, operation: Operation) -> Result<Reply,String> {
    let (publisher,temporary)=scope()?;
    let operation=match operation { Operation::Keyboard(value,_)=>Operation::Keyboard(value,temporary),Operation::Mouse(value,_)=>Operation::Mouse(value,temporary),other=>other };
    let exchange=|stream:&mut Connection,operation| -> Result<Reply,String> {
        if !peer.alive() { return Err("uinput service process changed".into()); }
        let sequence=next_sequence()?;
        let packet=Packet { lease:publisher.wire.clone(),sequence,operation };
        runtime.block_on(async {
            tokio::time::timeout(std::time::Duration::from_millis(IPC_REQUEST_TIMEOUT),stream.send(&Data::UinputReceiver(packet))).await
        }).map_err(|_|"uinput send deadline exceeded".to_owned())?.map_err(|error|error.to_string())?;
        match runtime.block_on(stream.next_timeout(IPC_REQUEST_TIMEOUT)).map_err(|error|error.to_string())? {
            Some(Data::UinputReceiverResponse(reply)) if reply.sequence==sequence && reply.nonce==publisher.wire.nonce && reply.accepted=>Ok(reply),
            _=>Err("uinput actual sink did not acknowledge acceptance".into()),
        }
    };
    let outcome=(|| {
        if !core::binding_known(bound,&publisher.wire.nonce) {
            exchange(stream,Operation::Bind)?;
            bound.insert(publisher.wire.nonce.clone());
        }
        if publisher.denied() && !is_cleanup(&operation) { return Err("uinput generation was revoked".into()); }
        // Bind acknowledgment guarantees the independent sink observer owns both
        // pages before this used bit can make retirement wait for an ack.
        publisher.mark_used(kind);
        exchange(stream,operation)
    })();
    if outcome.is_err() { publisher.fail(); }
    outcome
}

pub(crate) fn uses_keyboard(en: &mut enigo::Enigo) -> bool {
    en.get_custom_keyboard().as_mut().is_some_and(|value|value.as_mut_any().is::<client::UInputKeyboard>())
}
pub(crate) fn uses_mouse(en: &mut enigo::Enigo) -> bool {
    en.get_custom_mouse().as_mut().is_some_and(|value|value.as_mut_any().is::<client::UInputMouse>())
}
pub(crate) fn key(en:&mut enigo::Enigo,alias:String,physical:Option<u32>,temporary:u64,down:bool,repeat:bool)->Result<(),String> {
    let keyboard=en.get_custom_keyboard().as_mut().and_then(|value|value.as_mut_any().downcast_mut::<client::UInputKeyboard>()).ok_or("own uinput keyboard is unavailable")?;
    keyboard.receiver(Operation::Key { alias,physical,temporary,down,repeat }).map(|_|())
}
pub(crate) fn retire_operation(en:&mut enigo::Enigo,operation:u64)->Result<(),String> {
    let keyboard=en.get_custom_keyboard().as_mut().and_then(|value|value.as_mut_any().downcast_mut::<client::UInputKeyboard>()).ok_or("own uinput keyboard is unavailable")?;
    keyboard.receiver(Operation::RetireOperation(operation)).map(|_|())
}
pub(crate) fn pressed(en:&mut enigo::Enigo,physical:u32)->bool {
    en.get_custom_keyboard().as_mut().and_then(|value|value.as_mut_any().downcast_mut::<client::UInputKeyboard>())
        .and_then(|keyboard|keyboard.receiver(Operation::Pressed(physical)).ok()).map(|reply|reply.pressed).unwrap_or(false)
}
pub(crate) fn mouse_button(en:&mut enigo::Enigo,button:MouseButton,down:bool)->Result<(),String> {
    let mouse=en.get_custom_mouse().as_mut().and_then(|value|value.as_mut_any().downcast_mut::<client::UInputMouse>()).ok_or("own uinput mouse is unavailable")?;
    mouse.receiver(Operation::Mouse(if down {DataMouse::Down(button)}else{DataMouse::Up(button)},0)).map(|_|())
}

struct Sink { peer:Arc<Peer>, caps:HashMap<String,Arc<Consumer>>, ledger:Ledger, pending_sync:bool }
impl Sink {
    fn new(peer:Peer)->Self { Self {peer:Arc::new(peer),caps:HashMap::new(),ledger:Ledger::default(),pending_sync:false} }
    fn cap(&mut self,packet:&Packet)->Option<Arc<Consumer>> {
        if packet.sequence==0 || !nonce_valid(&packet.lease.nonce) { return None; }
        if let Some(cap)=self.caps.get(&packet.lease.nonce) {
            return (cap.wire==packet.lease).then(||cap.clone());
        }
        if (!matches!(packet.operation,Operation::Bind) && !is_cleanup(&packet.operation)) || self.caps.len()>=4096 { return None; }
        let cap=Arc::new(Consumer::open(&self.peer,&packet.lease)?);
        if !matches!(packet.operation,Operation::Bind) && !cap.permanently_revoked() {return None;}
        self.caps.insert(packet.lease.nonce.clone(),cap.clone());Some(cap)
    }
    fn deny_all(&self) { for cap in self.caps.values() {cap.deny();} }
    fn sweep(&mut self,kind:u64,mut emit:impl FnMut(u16,bool)->Result<(),String>,mut flush:impl FnMut()->Result<(),String>) -> bool {
        let caps=&self.caps;let peer=&self.peer;let mut attempted=false;
        self.ledger.retire(|claim,time|caps.get(&claim.owner).map(|cap|!cap.allowed(peer)).unwrap_or(true) || Ledger::expired(time),|key,down|{attempted=true;emit(key,down)});
        if self.pending_sync || attempted {self.pending_sync=flush().is_err();}
        for (owner,cap) in &self.caps {
            // Failed final Ups or SYN_REPORT retain responsibility and cannot acknowledge.
            if !cap.allowed(peer) && !self.ledger.held(owner) && !self.pending_sync {cap.ack.set(WORD,kind);}
        }
        // Retire only a permanently denied original capability. A queued old
        // Bind reopens that exact sealed deny page and still cannot admit input.
        if !self.pending_sync {
            core::reap_retired(&mut self.caps,&self.ledger,|cap|cap.denied.load(Ordering::SeqCst) && cap.permanently_revoked() && cap.ack.load(WORD)&kind!=0);
        }
        !self.pending_sync
    }
    fn drained(&self)->bool {!self.pending_sync && self.ledger.empty() && self.caps.values().all(|cap|cap.denied.load(Ordering::SeqCst))}
}
fn evdev_key(physical:u32)->Result<u16,String> {
    if !(9..=0x2ff+8).contains(&physical) {return Err("invalid own-uinput physical key".into());}
    let code=(physical-8) as u16;
    if format!("{:?}",evdev::Key::new(code)).contains("unknown key") {return Err("unsupported own-uinput physical key".into());}
    Ok(code)
}
fn keyboard_event(keyboard:&VirtualDevice,key:u16,down:bool)->Result<(),String> {
    // Write one EV_KEY separately: if the later SYN_REPORT fails, the accepted
    // key event must already belong to the authoritative receipt ledger.
    raw_event(keyboard.as_raw_fd(),1,key,if down {1}else{0})
}
fn raw_event(fd:RawFd,kind:u16,code:u16,value:i32)->Result<(),String> {
    let event=super::mouce::InputEvent {time:super::mouce::TimeVal {tv_sec:0,tv_usec:0},r#type:kind,code,value};
    let size=std::mem::size_of_val(&event);
    let written=unsafe {libc::write(fd,(&event as *const super::mouce::InputEvent).cast(),size)};
    if written!=size as isize {return Err(std::io::Error::last_os_error().to_string());}
    Ok(())
}
fn keyboard_flush(keyboard:&VirtualDevice)->Result<(),String> {raw_event(keyboard.as_raw_fd(),0,0,0)}
fn keyboard_emit(keyboard:&VirtualDevice,key:u16,down:bool)->Result<(),String> {keyboard_event(keyboard,key,down)}
fn button_code(button:MouseButton)->Result<u16,String> {match button {MouseButton::Left=>Ok(0x110),MouseButton::Right=>Ok(0x111),MouseButton::Middle=>Ok(0x112),MouseButton::Back=>Ok(0x113),MouseButton::Forward=>Ok(0x114),_=>Err("unsupported own-uinput mouse button".into())}}
fn mouse_emit(mouse:&super::mouce::UInputMouseManager,button:u16,down:bool)->Result<(),String> {
    let button=match button {0x110=>super::mouce::MouseButton::Left,0x111=>super::mouce::MouseButton::Right,0x112=>super::mouce::MouseButton::Middle,0x113=>super::mouce::MouseButton::Side,0x114=>super::mouce::MouseButton::Extra,_=>return Err("invalid retained uinput button".into())};
    with_owned_up(!down,||mouse.receiver_button_event(&button,down)).map_err(|error|error.to_string())
}
fn claim(cap:&Consumer,temporary:u64)->Claim {Claim {owner:cap.wire.nonce.clone(),temporary}}
fn actual_key(state:&mut Sink,cap:&Consumer,keyboard:&VirtualDevice,alias:&str,physical:Option<u32>,temporary:u64,down:bool,repeat:bool)->Result<(),String> {
    let key=physical.map(evdev_key).transpose()?;
    let who=claim(cap,temporary);
    if down {
        let key=key.ok_or("missing own-uinput Down key")?;
        state.ledger.down(who,alias,key,repeat,||cap.allowed(&state.peer),|key,down|keyboard_emit(keyboard,key,down))
    }else{state.ledger.up(&who,alias,key,|key,down|keyboard_emit(keyboard,key,down))}
}
fn proxy_key(state:&mut Sink,cap:&Consumer,keyboard:&VirtualDevice,key:Key,temporary:u64,down:bool)->Result<(),String> {
    let (physical,shift)=match key {Key::Raw(code)=>(code as u32,false),_=>{let (code,shift)=service::map_key(&key).map_err(|error|error.to_string())?;(code.code() as u32+8,shift)}};
    let alias=format!("proxy:{:?}",key);
    if shift && down {actual_key(state,cap,keyboard,&format!("shift:{}",alias),Some(50),temporary,true,false)?;}
    let result=actual_key(state,cap,keyboard,&alias,Some(physical),temporary,down,true);
    if shift && !down {actual_key(state,cap,keyboard,&format!("shift:{}",alias),Some(50),temporary,false,false)?;}
    result
}
fn keyboard_operation(state:&mut Sink,cap:&Consumer,keyboard:&VirtualDevice,packet:&Packet)->Result<bool,String> {
    if !matches!(packet.operation,Operation::Bind|Operation::Pressed(_)) {state.pending_sync=true;}
    match &packet.operation {
        Operation::Bind=>Ok(false),
        Operation::Key {alias,physical,temporary,down,repeat}=>actual_key(state,cap,keyboard,alias,*physical,*temporary,*down,*repeat).map(|_|false),
        Operation::RetireOperation(operation)=>{
            if state.ledger.retire(|owner,_|owner.owner==cap.wire.nonce && owner.temporary==*operation,|key,down|keyboard_emit(keyboard,key,down)) {Ok(false)}else{Err("uinput pulse cleanup remains pending".into())}
        }
        Operation::Pressed(physical)=>Ok(state.ledger.pressed_by(evdev_key(*physical)?,&cap.wire.nonce)),
        Operation::Keyboard(data,temporary)=>{
            match data {
                DataKeyboard::KeyDown(key)=>proxy_key(state,cap,keyboard,*key,*temporary,true)?,
                DataKeyboard::KeyUp(key)=>proxy_key(state,cap,keyboard,*key,*temporary,false)?,
                DataKeyboard::KeyClick(key)=>{
                    proxy_key(state,cap,keyboard,*key,packet.sequence,true)?;
                    proxy_key(state,cap,keyboard,*key,packet.sequence,false)?;
                }
                DataKeyboard::Sequence(text)=>{
                    if text.len()>262_144 {return Err("uinput text bound exceeded".into());}
                    for character in text.chars() {
                        state.sweep(KEYBOARD,|key,down|keyboard_emit(keyboard,key,down),||keyboard_flush(keyboard));
                        state.pending_sync=true;
                        if !cap.allowed(&state.peer) {return Err("uinput text generation revoked".into());}
                        proxy_key(state,cap,keyboard,Key::Layout(character),packet.sequence,true)?;
                        proxy_key(state,cap,keyboard,Key::Layout(character),packet.sequence,false)?;
                    }
                }
                DataKeyboard::GetKeyState(_)=>return Err("read-only key-state queries use their dedicated response".into()),
            }
            Ok(false)
        }
        Operation::Mouse(..)=>Err("mouse packet on uinput keyboard channel".into()),
    }
}
fn mouse_operation(state:&mut Sink,cap:&Consumer,mouse:&super::mouce::UInputMouseManager,packet:&Packet)->Result<bool,String> {
    if !matches!(packet.operation,Operation::Bind) {state.pending_sync=true;}
    match &packet.operation {
        Operation::Bind=>Ok(false),
        Operation::RetireOperation(operation)=>{
            if state.ledger.retire(|owner,_|owner.owner==cap.wire.nonce && owner.temporary==*operation,|key,down|mouse_emit(mouse,key,down)) {Ok(false)}else{Err("uinput mouse pulse cleanup remains pending".into())}
        }
        Operation::Mouse(data,temporary)=>{
            let who=claim(cap,*temporary);
            match data {
                DataMouse::Down(button)=>{let key=button_code(*button)?;state.ledger.down(who,&format!("button:{}",key),key,false,||cap.allowed(&state.peer),|key,down|mouse_emit(mouse,key,down))?;}
                DataMouse::Up(button)=>{let key=button_code(*button)?;state.ledger.up(&who,&format!("button:{}",key),Some(key),|key,down|mouse_emit(mouse,key,down))?;}
                DataMouse::Click(button)=>{
                    let who=claim(cap,packet.sequence);let key=button_code(*button)?;let alias=format!("button:{}",key);
                    state.ledger.down(who.clone(),&alias,key,false,||cap.allowed(&state.peer),|key,down|mouse_emit(mouse,key,down))?;
                    state.ledger.up(&who,&alias,Some(key),|key,down|mouse_emit(mouse,key,down))?;
                }
                DataMouse::MoveTo(x,y)=>{if !cap.allowed(&state.peer){return Err("uinput mouse generation revoked".into());}mouse.move_to(*x as _,*y as _).map_err(|error|error.to_string())?;}
                DataMouse::MoveRelative(x,y)=>{
                    if !(-10_000..=10_000).contains(x)||!(-10_000..=10_000).contains(y){return Err("uinput relative movement exceeds bound".into());}
                    if !cap.allowed(&state.peer){return Err("uinput mouse generation revoked".into());}mouse.move_relative(*x,*y).map_err(|error|error.to_string())?;
                }
                DataMouse::ScrollX(value)|DataMouse::ScrollY(value)=>{
                    if !(-1024..=1024).contains(value){return Err("uinput wheel exceeds bound".into());}
                    let mut remaining=*value;
                    while remaining!=0 {
                        state.sweep(MOUSE,|key,down|mouse_emit(mouse,key,down),||with_owned_up(true,||mouse.receiver_flush()).map_err(|error|error.to_string()));
                        state.pending_sync=true;
                        if !cap.allowed(&state.peer){return Err("uinput wheel generation revoked".into());}
                        let chunk=remaining.clamp(-16,16);
                        (if matches!(data,DataMouse::ScrollX(_)){mouse.scroll_wheel_horizontal(chunk)}else{mouse.scroll_wheel_vertical(chunk)}).map_err(|error|error.to_string())?;
                        remaining-=chunk;
                    }
                }
                DataMouse::Refresh=>return Err("resolution refresh must use setup channel".into()),
            }
            Ok(false)
        }
        _=>Err("keyboard packet on uinput mouse channel".into()),
    }
}
fn response(packet:&Packet,result:Result<bool,String>)->Reply {
    if let Err(error)=&result {log::error!("Owned uinput effect was not accepted: {}",error);}
    Reply {nonce:packet.lease.nonce.clone(),sequence:packet.sequence,accepted:result.is_ok(),pressed:result.unwrap_or(false)}
}

thread_local! {
    static DEVICE_SCOPE: std::cell::RefCell<Option<(Arc<Consumer>,Arc<Peer>)>>=const {std::cell::RefCell::new(None)};
    static OWNED_UP: std::cell::Cell<bool>=const {std::cell::Cell::new(false)};
}
fn with_device_scope<T>(cap:Arc<Consumer>,peer:Arc<Peer>,effect:impl FnOnce()->T)->T {
    let previous=DEVICE_SCOPE.with(|scope|scope.replace(Some((cap,peer))));
    struct Reset(Option<(Arc<Consumer>,Arc<Peer>)>);
    impl Drop for Reset {fn drop(&mut self){DEVICE_SCOPE.with(|scope|{scope.replace(self.0.take());});}}
    let _reset=Reset(previous);effect()
}
fn with_owned_up<T>(allowed:bool,effect:impl FnOnce()->T)->T {
    let previous=OWNED_UP.with(|value|value.replace(allowed));
    struct Reset(bool);impl Drop for Reset {fn drop(&mut self){OWNED_UP.with(|value|value.set(self.0));}}
    let _reset=Reset(previous);effect()
}
pub(super) fn device_admitted(kind:i32,value:i32)->bool {
    if OWNED_UP.with(|flag|flag.get()) && (kind==0 || (kind==1 && value==0)){return true;}
    DEVICE_SCOPE.with(|scope|scope.borrow().as_ref().map(|(cap,peer)|kind==0 || cap.allowed(peer)).unwrap_or(false))
}
async fn send_observed(stream:&mut Connection,data:&Data,state:&mut Sink,timer:&mut tokio::time::Interval,kind:u64,mut emit:impl FnMut(u16,bool)->Result<(),String>,mut flush:impl FnMut()->Result<(),String>)->bool {
    let send=stream.send(data);tokio::pin!(send);
    let deadline=tokio::time::sleep(std::time::Duration::from_millis(IPC_REQUEST_TIMEOUT));tokio::pin!(deadline);
    loop {tokio::select! {biased;
        _=timer.tick()=>{state.sweep(kind,&mut emit,&mut flush);}
        _=&mut deadline=>return false,
        result=&mut send=>return result.is_ok(),
    }}
}
fn keyboard_state(keyboard:&VirtualDevice,key:Key)->bool {
    if matches!(key,Key::CapsLock|Key::NumLock) {
        return keyboard.get_led_state().map(|leds|leds.contains(if key==Key::CapsLock {evdev::LedType::LED_CAPSL}else{evdev::LedType::LED_NUML})).unwrap_or(false);
    }
    keyboard.get_key_state().map(|keys|match key {
        Key::Shift=>keys.contains(evdev::Key::KEY_LEFTSHIFT)||keys.contains(evdev::Key::KEY_RIGHTSHIFT),
        Key::Control=>keys.contains(evdev::Key::KEY_LEFTCTRL)||keys.contains(evdev::Key::KEY_RIGHTCTRL),
        Key::Alt=>keys.contains(evdev::Key::KEY_LEFTALT)||keys.contains(evdev::Key::KEY_RIGHTALT),
        Key::Meta=>keys.contains(evdev::Key::KEY_LEFTMETA)||keys.contains(evdev::Key::KEY_RIGHTMETA),
        _=>false,
    }).unwrap_or(false)
}

pub(super) fn spawn_keyboard(mut stream:Connection) {
    let Some(peer)=stream.receiver_uinput_peer(true) else {log::error!("Unauthenticated uinput keyboard publisher refused");return;};
    tokio::spawn(async move {
        let keyboard=match service::create_uinput_keyboard(){Ok(value)=>value,Err(error)=>{log::error!("Failed to create uinput keyboard: {}",error);return;}};
        let mut state=Sink::new(peer);let mut connected=true;
        let mut timer=tokio::time::interval(std::time::Duration::from_millis(10));
        loop {
            tokio::select! { biased;
                _=timer.tick()=>{
                    // Independent priority cleanup does not queue behind data.
                    state.sweep(KEYBOARD,|key,down|keyboard_emit(&keyboard,key,down),||keyboard_flush(&keyboard));
                    if !connected && state.drained(){break;}
                }
                value=stream.next(),if connected=>{
                    match value {
                        Ok(Some(Data::UinputReceiver(packet)))=>{
                            let reply=if let Some(cap)=state.cap(&packet){
                                state.sweep(KEYBOARD,|key,down|keyboard_emit(&keyboard,key,down),||keyboard_flush(&keyboard));
                                let result=if !matches!(packet.operation,Operation::Bind) && !is_cleanup(&packet.operation) && !cap.allowed(&state.peer){Err("uinput generation revoked at sink".into())}else{keyboard_operation(&mut state,&cap,&keyboard,&packet)};
                                let result=if !state.sweep(KEYBOARD,|key,down|keyboard_emit(&keyboard,key,down),||keyboard_flush(&keyboard)){cap.deny();Err("uinput synchronization remains pending".into())}else{result};
                                response(&packet,result)
                            }else{response(&packet,Err("invalid uinput capability or channel bound".into()))};
                            state.sweep(KEYBOARD,|key,down|keyboard_emit(&keyboard,key,down),||keyboard_flush(&keyboard));
                            if !send_observed(&mut stream,&Data::UinputReceiverResponse(reply),&mut state,&mut timer,KEYBOARD,|key,down|keyboard_emit(&keyboard,key,down),||keyboard_flush(&keyboard)).await{connected=false;state.deny_all();}
                        }
                        Ok(Some(Data::Keyboard(DataKeyboard::GetKeyState(key))))=>{
                            let response=Data::KeyboardResponse(ipc::DataKeyboardResponse::GetKeyState(keyboard_state(&keyboard,key)));
                            if !state.peer.alive() || !send_observed(&mut stream,&response,&mut state,&mut timer,KEYBOARD,|key,down|keyboard_emit(&keyboard,key,down),||keyboard_flush(&keyboard)).await {connected=false;state.deny_all();}
                        }
                        // Unscoped mutation can never be a legacy remote fallback.
                        _=>{connected=false;state.deny_all();}
                    }
                }
            }
        }
    });
}
pub(super) fn spawn_mouse(mut stream:Connection,rng_x:(i32,i32),rng_y:(i32,i32)) {
    let Some(peer)=stream.receiver_uinput_peer(true) else {log::error!("Unauthenticated uinput mouse publisher refused");return;};
    tokio::spawn(async move {
        let mut mouse=match super::mouce::UInputMouseManager::new(rng_x,rng_y){Ok(value)=>value,Err(error)=>{log::error!("Failed to create uinput mouse: {}",error);return;}};
        let mut state=Sink::new(peer);let mut connected=true;let mut pending_refresh=None;
        let mut timer=tokio::time::interval(std::time::Duration::from_millis(10));
        loop {
            tokio::select! { biased;
                _=timer.tick()=>{
                    state.sweep(MOUSE,|key,down|mouse_emit(&mouse,key,down),||with_owned_up(true,||mouse.receiver_flush()).map_err(|error|error.to_string()));
                    if connected && state.ledger.empty() && !state.pending_sync {
                        if let Some((rng_x,rng_y))=pending_refresh.take() {
                            match super::mouce::UInputMouseManager::new(rng_x,rng_y){Ok(value)=>mouse=value,Err(error)=>{log::error!("uinput resolution refresh failed: {}",error);connected=false;state.deny_all();}}
                        }
                    }
                    if !connected && state.drained(){break;}
                }
                value=stream.next(),if connected=>{
                    match value {
                        Ok(Some(Data::UinputReceiver(packet)))=>{
                            let reply=if let Some(cap)=state.cap(&packet){
                                state.sweep(MOUSE,|key,down|mouse_emit(&mouse,key,down),||with_owned_up(true,||mouse.receiver_flush()).map_err(|error|error.to_string()));
                                let result=if !matches!(packet.operation,Operation::Bind) && !is_cleanup(&packet.operation) && !cap.allowed(&state.peer){Err("uinput generation revoked at sink".into())}else{with_device_scope(cap.clone(),state.peer.clone(),||mouse_operation(&mut state,&cap,&mouse,&packet))};
                                let result=if !state.sweep(MOUSE,|key,down|mouse_emit(&mouse,key,down),||with_owned_up(true,||mouse.receiver_flush()).map_err(|error|error.to_string())){cap.deny();Err("uinput synchronization remains pending".into())}else{result};
                                response(&packet,result)
                            }else{response(&packet,Err("invalid uinput capability or channel bound".into()))};
                            state.sweep(MOUSE,|key,down|mouse_emit(&mouse,key,down),||with_owned_up(true,||mouse.receiver_flush()).map_err(|error|error.to_string()));
                            if !send_observed(&mut stream,&Data::UinputReceiverResponse(reply),&mut state,&mut timer,MOUSE,|key,down|mouse_emit(&mouse,key,down),||with_owned_up(true,||mouse.receiver_flush()).map_err(|error|error.to_string())).await{connected=false;state.deny_all();}
                        }
                        Ok(Some(Data::Mouse(DataMouse::Refresh))) if state.peer.alive()=>{
                            let resolution=service::RESOLUTION.lock().unwrap();
                            // Keep the current device and all successful claims
                            // until every held button has its accepted final Up.
                            pending_refresh=Some((resolution.0,resolution.1));
                        }
                        _=>{connected=false;state.deny_all();}
                    }
                }
            }
        }
    });
}
