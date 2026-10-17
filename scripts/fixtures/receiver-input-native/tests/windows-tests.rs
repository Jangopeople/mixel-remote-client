#[link(name="kernel32")]
extern "system" {
    fn CreateNamedPipeW(name:*const u16,access:u32,mode:u32,instances:u32,out_size:u32,in_size:u32,timeout:u32,security:Handle)->Handle;
    fn ConnectNamedPipe(pipe:Handle,overlapped:Handle)->i32;
    fn CreateFileW(name:*const u16,access:u32,share:u32,security:Handle,disposition:u32,flags:u32,template:Handle)->Handle;
}
fn read_packet(file:&mut std::fs::File)->String {
    use std::io::Read;let mut size=[0u8;4];file.read_exact(&mut size).unwrap();let size=u32::from_le_bytes(size)as usize;assert!(size<256);
    let mut bytes=vec![0;size];file.read_exact(&mut bytes).unwrap();String::from_utf8(bytes).unwrap()
}
pub(crate) fn publisher_child() {
    use std::{os::windows::io::FromRawHandle,io::{Read,Write}};
    let path=std::env::var("MIXEL_RECEIVER_TEST_PIPE").unwrap();
    let pipe=unsafe{CreateNamedPipeW(wide(&path).as_ptr(),3,0,1,4096,4096,5000,ptr::null_mut())};assert!(!pipe.is_null()&&pipe as isize != -1);
    let owned=Owned::from(pipe).unwrap();
    let connected=unsafe{ConnectNamedPipe(owned.raw(),ptr::null_mut())};assert!(connected!=0||unsafe{GetLastError()}==535);
    let _authenticated_helper=pipe_peer(owned.raw(),false).expect("actual intended helper pipe peer");
    let lease=Lease::create(&uuid::Uuid::new_v4().simple().to_string()).unwrap();assert!(lease.prepare_helper().is_some());
    let raw=owned.raw();std::mem::forget(owned);let mut file=unsafe{std::fs::File::from_raw_handle(raw)};
    file.write_all(&(lease.token.len()as u32).to_le_bytes()).unwrap();file.write_all(lease.token.as_bytes()).unwrap();file.flush().unwrap();
    loop {
        let mut command=[0u8;1];file.read_exact(&mut command).unwrap();
        match command[0] {
            0=>return,
            1=>{assert!(lease.revoke());file.write_all(&[1]).unwrap();file.flush().unwrap();},
            2=>{let handle=lease.mutex.0;std::thread::spawn(move||{assert_eq!(unsafe{WaitForSingleObject(handle as Handle,5000)},OBJECT);}).join().unwrap();file.write_all(&[2]).unwrap();file.flush().unwrap();},
            _=>panic!("unknown controlled lease command"),
        }
    }
}
struct OwnedChild(std::process::Child);
impl std::ops::Deref for OwnedChild { type Target=std::process::Child;fn deref(&self)->&Self::Target{&self.0} }
impl std::ops::DerefMut for OwnedChild { fn deref_mut(&mut self)->&mut Self::Target{&mut self.0} }
impl Drop for OwnedChild { fn drop(&mut self){if matches!(self.0.try_wait(),Ok(None)){let _=self.0.kill();let _=self.0.wait();}} }
struct Publisher { child:OwnedChild, file:std::fs::File, peer:Peer, token:String }
impl Publisher {
    fn start()->Self {
        use std::{os::windows::io::{FromRawHandle,AsRawHandle},time::Instant};
        let path=format!("\\\\.\\pipe\\Mixel-Input-Test-{}",uuid::Uuid::new_v4().simple());
        let child=OwnedChild(std::process::Command::new(std::env::current_exe().unwrap()).arg("--mixel-lease-publisher").env("MIXEL_RECEIVER_TEST_PIPE",&path).spawn().unwrap());
        let deadline=Instant::now()+Duration::from_secs(5);
        let handle=loop{let handle=unsafe{CreateFileW(wide(&path).as_ptr(),0xc0000000,0,ptr::null_mut(),3,0,ptr::null_mut())};if !handle.is_null()&&handle as isize != -1{break handle;}assert!(Instant::now()<deadline);std::thread::sleep(Duration::from_millis(5));};
        let mut file=unsafe{std::fs::File::from_raw_handle(handle)};
        let peer=pipe_peer(file.as_raw_handle(),true).expect("actual same-session executable pipe publisher required");
        let token=read_packet(&mut file);assert_eq!(names(&token).unwrap().3,child.id());Self{child,file,peer,token}
    }
    fn command(&mut self,command:u8){use std::io::{Read,Write};self.file.write_all(&[command]).unwrap();self.file.flush().unwrap();if command!=0{let mut reply=[0u8;1];self.file.read_exact(&mut reply).unwrap();assert_eq!(reply[0],command);}}
    fn packet(&self)->(String,String,i32,u8,Vec<u8>,String,u32,bool,bool){
        let mut key=KeyEvent::new();key.set_chr(30);key.down=true;
        let target=format!("{}-{}",unsafe{GetCurrentProcessId()},birth(unsafe{GetCurrentProcess()}).unwrap());
        (self.token.clone(),target,44,1,key.write_to_bytes().unwrap(),String::new(),0,false,false)
    }
}
// Exercise the exact live asynchronous admission and wait only in the fixture.
fn receive(packet:(String,String,i32,u8,Vec<u8>,String,u32,bool,bool),peer:&Peer)->bool{
    let token=packet.0.clone();if !receive_async(packet,peer,Arc::new(|_|true)){return false;}
    let remote=remotes().lock().unwrap().get(&token).cloned();let Some(remote)=remote else{return false;};
    wait_until(||!remote.scheduled.load(Ordering::SeqCst));remote.lease.active()
}
fn native_create_new_readonly_and_peer_binding() {
    let mut publisher=Publisher::start();let consumer=Lease::open(&publisher.token,&publisher.peer).unwrap();assert!(consumer.active());
    assert_eq!(unsafe{SetEvent(consumer.event.raw())},0,"consumer must not possess EVENT_MODIFY_STATE");
    let(event,mutex,_,_,_)=names(&publisher.token).unwrap();assert!(create_object(&event,true).is_none());assert!(create_object(&mutex,false).is_none());
    let wrong=format!("Local\\Mixel-Remote-Input-WrongType-{}",uuid::Uuid::new_v4().simple());let _mutex=create_object(&wrong,false).unwrap();assert!(create_object(&wrong,true).is_none());
    let mut forged=publisher.peer.clone();forged.pid=unsafe{GetCurrentProcessId()};assert!(Lease::open(&publisher.token,&forged).is_none());
    assert!(receive(publisher.packet(),&publisher.peer));publisher.command(1);assert!(!consumer.active());assert!(consumer.effect().is_none());assert!(!receive(publisher.packet(),&publisher.peer));let deadline=std::time::Instant::now()+Duration::from_secs(2);while !consumer.cleaned(){assert!(std::time::Instant::now()<deadline);std::thread::sleep(Duration::from_millis(5));}publisher.command(0);assert!(publisher.child.wait().unwrap().success());
}
fn native_owner_death_cleanup_without_next_data_and_old_restart_packets() {
    let mut publisher=Publisher::start();let consumer=Lease::open(&publisher.token,&publisher.peer).unwrap();assert!(receive(publisher.packet(),&publisher.peer));
    let stale=publisher.packet();publisher.command(0);assert!(publisher.child.wait().unwrap().success());assert!(!publisher.peer.alive());assert!(!consumer.active());
    let deadline=std::time::Instant::now()+Duration::from_secs(2);
    while !super::super::super::observed().lock().unwrap().iter().any(|event|event.owner==publisher.token&&event.text=="cleanup") {assert!(std::time::Instant::now()<deadline);std::thread::sleep(Duration::from_millis(5));}
    assert!(!receive(stale,&publisher.peer));
    let mut fresh=Publisher::start();let mut stale_target=fresh.packet();stale_target.1="1-1".to_owned();assert!(!receive(stale_target,&fresh.peer));assert!(receive(fresh.packet(),&fresh.peer));fresh.command(1);fresh.command(0);assert!(fresh.child.wait().unwrap().success());
}
fn native_abandoned_mutex_poison_cannot_reopen_scope() {
    let mut publisher=Publisher::start();assert!(receive(publisher.packet(),&publisher.peer));publisher.command(2);
    assert!(!receive(publisher.packet(),&publisher.peer));std::thread::sleep(Duration::from_millis(30));assert!(!receive(publisher.packet(),&publisher.peer));
    publisher.command(0);assert!(publisher.child.wait().unwrap().success());
}
pub(crate) fn run_native_tests() {
    for(name,case)in [
        ("Windows local text chunks preserve FIFO and allow Block controls",native_local_text_chunks_and_control as fn()),
        ("Windows helper Block ownership, same-thread input and failed Off retry",native_portable_block_owners_and_thread_affinity as fn()),
        ("Windows queued stale BlockOn denied before effect",native_portable_queued_block_on_is_denied as fn()),
        ("Windows portable text chunks preserve order and admit control",native_portable_text_chunks_and_control as fn()),
        ("Windows bounded portable packets overflow closes ownership",native_portable_packet_overflow as fn()),
        ("Windows correlated Block replies bind exact peer and first outcome",native_portable_block_reply_correlation as fn()),
        ("Windows keyboard revoke releases ordinary block and preserves privacy",native_portable_receiver_block_lifecycle as fn()),
        ("Windows create-new, readonly handles, kernel pipe peer and revoke",native_create_new_readonly_and_peer_binding as fn()),
        ("Windows owner death independent cleanup and restart target",native_owner_death_cleanup_without_next_data_and_old_restart_packets as fn()),
        ("Windows abandoned mutex permanently poisons scope",native_abandoned_mutex_poison_cannot_reopen_scope as fn()),
        ("Windows retained startup identity and once-only query-handle transfer",native_startup_identity_transfer_and_unrelated_handle_refusal as fn()),
        ("Windows exact owned suspended startup failure cleanup",native_partial_start_guard_terminates_only_owned_suspended_child as fn()),
        ("Windows late retiring-helper Pong cannot reopen admission",native_old_helper_pong_cannot_reopen_transition as fn()),
        ("Windows pending receipts and failed BlockOff retain recovery barrier",native_recovery_waits_for_receipts_and_block_off as fn()),
        ("Windows stale failure cannot disarm a fresh startup attempt",native_stale_failure_cannot_disarm_fresh_attempt as fn()),
        ("Windows invalid logon/create failure recover fresh admission",native_create_failure_and_invalid_logon_recover_fresh_admission as fn()),
        ("Windows owned handshake timeout recovers after exact child death",native_unconfirmed_owned_startup_times_out_then_recovers as fn()),
    ] {case();println!("PASS: {name}");}
}
#[cfg(feature="guard-must-not-send")]
fn acquired_mutex_guard_must_not_migrate(serial:Serial<'static>){std::thread::spawn(move||drop(serial));}

#[link(name="kernel32")]
extern "system" {
    fn SetNamedPipeHandleState(pipe:Handle,mode:*const u32,maximum:*const u32,timeout:*const u32)->i32;
}
pub(crate) fn query_child() {
    use std::{os::windows::io::{FromRawHandle,AsRawHandle},io::{Read,Write}};
    let path=std::env::var("MIXEL_RECEIVER_TEST_PIPE").unwrap();let deadline=std::time::Instant::now()+Duration::from_secs(5);
    let raw=loop{let h=unsafe{CreateFileW(wide(&path).as_ptr(),0xc0000000,0,ptr::null_mut(),3,0,ptr::null_mut())};if !h.is_null()&&h as isize != -1{break h;}assert!(std::time::Instant::now()<deadline);std::thread::sleep(Duration::from_millis(5));};
    let mut file=unsafe{std::fs::File::from_raw_handle(raw)};let mut bytes=[0u8;8];file.read_exact(&mut bytes).unwrap();let transferred=u64::from_le_bytes(bytes);
    let object=create_object(&format!("Local\\Mixel-Input-Untrusted-Handle-{}",uuid::Uuid::new_v4().simple()),false).unwrap();
    assert!(pipe_publisher(file.as_raw_handle(),Some(object.0 as u64)).is_none());
    assert_eq!(unsafe{WaitForSingleObject(object.raw(),0)},OBJECT,"rejected supplied handle must never close an unrelated object");unsafe{ReleaseMutex(object.raw());}
    let peer=pipe_publisher(file.as_raw_handle(),Some(transferred)).expect("kernel-bound transferred publisher query handle");assert_eq!(peer.pid,pipe_pid(file.as_raw_handle(),true).unwrap());assert!(peer.alive());
    drop(peer);assert_eq!(unsafe{WaitForSingleObject(transferred as usize as Handle,0)},u32::MAX,"accepted transferred handle is owned and closed exactly once");
    file.write_all(&[1]).unwrap();file.flush().unwrap();
}
fn native_startup_identity_transfer_and_unrelated_handle_refusal() {
    use std::{os::windows::io::{FromRawHandle,AsRawHandle},io::{Read,Write}};
    let path=format!("\\\\.\\pipe\\Mixel-Input-Query-{}",uuid::Uuid::new_v4().simple());
    let pipe=unsafe{CreateNamedPipeW(wide(&path).as_ptr(),3,1,1,4096,4096,5000,ptr::null_mut())};assert!(!pipe.is_null()&&pipe as isize != -1);let pipe=Owned::from(pipe).unwrap();
    let mut child=OwnedChild(std::process::Command::new(std::env::current_exe().unwrap()).arg("--mixel-receiver-query-child").env("MIXEL_RECEIVER_TEST_PIPE",&path).spawn().unwrap());
    let process=Owned::from(unsafe{OpenProcess(SYNC|0x1000|0x40,0,child.id())}).unwrap();let sid=process_sid(process.raw()).unwrap();let peer=verified_peer(child.id(),Arc::new(process),sid).unwrap();
    let mut transfer=ptr::null_mut();assert_ne!(unsafe{DuplicateHandle(GetCurrentProcess(),GetCurrentProcess(),peer.process.raw(),&mut transfer,SYNC|0x1000,0,0)},0);
    *started().lock().unwrap()=Some(Started{peer:peer.clone(),publisher:Some(transfer as usize as u64),confirmed:Arc::new(AtomicUsize::new(0))});
    let deadline=std::time::Instant::now()+Duration::from_secs(5);loop{if unsafe{ConnectNamedPipe(pipe.raw(),ptr::null_mut())}!=0||unsafe{GetLastError()}==535{break;}assert!(std::time::Instant::now()<deadline);std::thread::sleep(Duration::from_millis(5));}
    let mode=0;assert_ne!(unsafe{SetNamedPipeHandleState(pipe.raw(),&mode,ptr::null(),ptr::null())},0);
    let authenticated=pipe_peer(pipe.raw(),false).unwrap();assert_eq!(authenticated.pid,peer.pid);assert_eq!(authenticated.born,peer.born);assert_eq!(authenticated.sid,peer.sid);
    let transferred=publisher_handle(&authenticated).unwrap().unwrap();assert_eq!(transferred,transfer as usize as u64);assert!(publisher_handle(&authenticated).is_none(),"startup query handle transfers exactly once");
    let raw=pipe.raw();std::mem::forget(pipe);let mut file=unsafe{std::fs::File::from_raw_handle(raw)};file.write_all(&transferred.to_le_bytes()).unwrap();file.flush().unwrap();let mut reply=[0u8;1];file.read_exact(&mut reply).unwrap();assert_eq!(reply,[1]);assert!(child.wait().unwrap().success());
    started().lock().unwrap().take();*helper().lock().unwrap()=None;
}
fn native_partial_start_guard_terminates_only_owned_suspended_child() {
    let exe=std::env::current_exe().unwrap();let exe=wide(exe.to_str().unwrap());let mut command=wide("\"receiver-component\" --mixel-receiver-query-child");
    let mut info:StartupInfo=unsafe{std::mem::zeroed()};info.size=std::mem::size_of::<StartupInfo>()as u32;let mut process:ProcessInfo=unsafe{std::mem::zeroed()};
    assert_ne!(unsafe{CreateProcessW(exe.as_ptr(),command.as_mut_ptr(),ptr::null_mut(),ptr::null_mut(),0,4,ptr::null_mut(),ptr::null(),&mut info,&mut process)},0);
    let observer=Arc::new(Owned::from(process.process).unwrap());let guard=Suspended{process:observer.clone(),thread:Some(Owned::from(process.thread).unwrap()),resumed:false};drop(guard);
    assert_eq!(unsafe{WaitForSingleObject(observer.raw(),0)},OBJECT,"partial startup cannot leave its exact new child suspended");
}

fn native_old_helper_pong_cannot_reopen_transition() {
    let pid=unsafe{GetCurrentProcessId()};let process=Owned::from(unsafe{OpenProcess(SYNC|0x1000,0,pid)}).unwrap();let sid=process_sid(process.raw()).unwrap();let peer=verified_peer(pid,Arc::new(process),sid).unwrap();
    *helper().lock().unwrap()=Some(peer.clone());assert!(begin_transition().is_some());assert!(TRANSITION.load(Ordering::SeqCst));assert!(!startup_confirmed(&peer,||panic!("retired helper cannot publish RUNNING")));assert!(TRANSITION.load(Ordering::SeqCst),"late retiring-helper Pong cannot admit Local/old-backend scopes");
    *helper().lock().unwrap()=None;TRANSITION.store(false,Ordering::SeqCst);
}

fn native_create_failure_and_invalid_logon_recover_fresh_admission() {
    let exe=std::env::current_exe().unwrap();let exe=exe.to_str().unwrap();
    let absent=format!("MixelAbsent{}",&uuid::Uuid::new_v4().simple().to_string()[..8]);let secret=SecretCredential::new(uuid::Uuid::new_v4().simple().to_string());
    assert!(launch_with_logon(&absent,secret.as_str(),exe,"--portable-service").is_none());assert!(!TRANSITION.load(Ordering::SeqCst),"invalid credentials do not alter admission");
    let sid=process_sid(unsafe{GetCurrentProcess()}).unwrap();assert!(launch_owned(exe,"--portable-service",sid,|_,_|0).is_none());
    let deadline=std::time::Instant::now()+Duration::from_secs(3);while TRANSITION.load(Ordering::SeqCst){assert!(std::time::Instant::now()<deadline);std::thread::sleep(Duration::from_millis(5));}
    let receiver=Receiver::new(81);assert!(receiver.authorize(true));assert!(receiver.close());
}
fn native_unconfirmed_owned_startup_times_out_then_recovers() {
    // The owned child blocks waiting for a GUID-only fixture pipe, sends no
    // application Pong, and is killed only by its exact startup watchdog.
    let path=format!("\\\\.\\pipe\\Mixel-Input-Timeout-{}",uuid::Uuid::new_v4().simple());
    let exe=std::env::current_exe().unwrap();let application=wide(exe.to_str().unwrap());let mut command=wide(&format!("\"{}\" --mixel-lease-publisher",exe.display()));
    let old=std::env::var_os("MIXEL_RECEIVER_TEST_PIPE");std::env::set_var("MIXEL_RECEIVER_TEST_PIPE",path);
    let sid=process_sid(unsafe{GetCurrentProcess()}).unwrap();assert!(launch_owned(exe.to_str().unwrap(),"--portable-service",sid,|info,process|unsafe{CreateProcessW(application.as_ptr(),command.as_mut_ptr(),ptr::null_mut(),ptr::null_mut(),0,4,ptr::null_mut(),ptr::null(),info,process)}).is_some());
    if let Some(old)=old{std::env::set_var("MIXEL_RECEIVER_TEST_PIPE",old);}else{std::env::remove_var("MIXEL_RECEIVER_TEST_PIPE");}
    let owned=started().lock().unwrap().as_ref().unwrap().peer.process.clone();assert!(TRANSITION.load(Ordering::SeqCst));let fresh=Receiver::new(82);assert!(!fresh.authorize(true));assert!(fresh.close());
    let deadline=std::time::Instant::now()+Duration::from_secs(15);while TRANSITION.load(Ordering::SeqCst){assert!(std::time::Instant::now()<deadline);std::thread::sleep(Duration::from_millis(10));}
    assert_eq!(unsafe{WaitForSingleObject(owned.raw(),0)},OBJECT);let fresh=Receiver::new(83);assert!(fresh.authorize(true));assert!(fresh.close());started().lock().unwrap().take();*helper().lock().unwrap()=None;
}
fn native_recovery_waits_for_receipts_and_block_off() {
    let receiver=Receiver::new(84);assert!(receiver.authorize(true));super::run_actual(super::captured(&receiver,super::key_action(true)));assert!(receiver.block(true,false));
    super::super::super::RELEASE_FAILURES.store(100_000,Ordering::SeqCst);super::super::super::UNBLOCK_FAILURES.store(100_000,Ordering::SeqCst);
    assert!(!quiesce_helper_transition());assert!(TRANSITION.load(Ordering::SeqCst));let fresh=Receiver::new(85);assert!(!fresh.authorize(true));assert!(fresh.close());
    super::super::super::RELEASE_FAILURES.store(0,Ordering::SeqCst);
    let deadline=std::time::Instant::now()+Duration::from_secs(3);while !receiver.inner.state.lock().unwrap().generation.as_ref().unwrap().cleanup_done.load(Ordering::SeqCst){assert!(std::time::Instant::now()<deadline);std::thread::sleep(Duration::from_millis(5));}
    assert!(TRANSITION.load(Ordering::SeqCst),"failed OS unblock retains barrier after key receipts retire");
    super::super::super::UNBLOCK_FAILURES.store(0,Ordering::SeqCst);let deadline=std::time::Instant::now()+Duration::from_secs(3);while TRANSITION.load(Ordering::SeqCst){assert!(std::time::Instant::now()<deadline);std::thread::sleep(Duration::from_millis(5));}
    assert!(receiver.close());let fresh=Receiver::new(86);assert!(fresh.authorize(true));assert!(fresh.close());
}
fn native_stale_failure_cannot_disarm_fresh_attempt() {
    let old=begin_transition().unwrap();request_recovery(old);poll_transition_recovery();assert!(!TRANSITION.load(Ordering::SeqCst));
    let fresh=begin_transition().unwrap();assert_ne!(old,fresh);request_recovery(old);poll_transition_recovery();assert!(TRANSITION.load(Ordering::SeqCst),"old watchdog cannot recover a fresh pending attempt");
    request_recovery(fresh);poll_transition_recovery();assert!(!TRANSITION.load(Ordering::SeqCst));
}

fn own_target()->String{format!("{}-{}",unsafe{GetCurrentProcessId()},birth(unsafe{GetCurrentProcess()}).unwrap())}
fn wait_until(test:impl Fn()->bool){let deadline=std::time::Instant::now()+Duration::from_secs(4);while !test(){assert!(std::time::Instant::now()<deadline,"bounded actual-effect completion");std::thread::sleep(Duration::from_millis(1));}}
fn block_on(publisher:&Publisher,role:u8)->bool{
    let(tx,rx)=mpsc::sync_channel(1);let reply:Reply=Arc::new(move|value|{if let PortableReply::Block(_,_,ok)=value{tx.try_send(ok).is_ok()}else{false}});
    assert!(receive_block(publisher.token.clone(),own_target(),44,role,uuid::Uuid::new_v4().simple().to_string(),&publisher.peer,reply));rx.recv_timeout(Duration::from_secs(4)).unwrap()
}
fn native_portable_block_owners_and_thread_affinity(){
    let mut failed=Publisher::start();let failed_lease=Lease::open(&failed.token,&failed.peer).unwrap();super::super::super::BLOCK_FAILURES.store(1,Ordering::SeqCst);assert!(!block_on(&failed,1));assert!(!remote_blocks().lock().unwrap().contains(&failed.token));failed.command(1);wait_until(||failed_lease.cleaned());failed.command(0);assert!(failed.child.wait().unwrap().success());
    let begin=super::super::super::block_calls().lock().unwrap().len();let mut ordinary=Publisher::start();let mut privacy=Publisher::start();let mut data=Publisher::start();
    assert!(block_on(&ordinary,1));assert!(block_on(&ordinary,1));assert!(block_on(&privacy,2));assert!(receive(data.packet(),&data.peer));
    assert!(!receive_async(ordinary.packet(),&ordinary.peer,Arc::new(|_|true)),"a block lease cannot authorize keyboard input");
    assert!(!receive_block(data.token.clone(),own_target(),44,1,uuid::Uuid::new_v4().simple().to_string(),&data.peer,Arc::new(|_|true)),"an input lease cannot change role");
    let on=super::super::super::block_calls().lock().unwrap()[begin];assert!(on.0);assert_eq!(super::super::super::input_threads().lock().unwrap().last().copied(),Some(on.1));
    let ordinary_lease=Lease::open(&ordinary.token,&ordinary.peer).unwrap();ordinary.command(1);wait_until(||ordinary_lease.cleaned());assert_eq!(super::super::super::block_calls().lock().unwrap().len(),begin+1,"privacy owns remaining aggregate block");
    let privacy_lease=Lease::open(&privacy.token,&privacy.peer).unwrap();super::super::super::UNBLOCK_FAILURES.store(100_000,Ordering::SeqCst);privacy.command(1);std::thread::sleep(Duration::from_millis(40));assert!(!privacy_lease.cleaned(),"failed native Off must retain owner and refuse retirement ack");
    super::super::super::UNBLOCK_FAILURES.store(0,Ordering::SeqCst);wait_until(||privacy_lease.cleaned());let off=super::super::super::block_calls().lock().unwrap()[begin+1];assert!(!off.0);assert_eq!(off.1,on.1);
    ordinary.command(0);privacy.command(0);data.command(1);data.command(0);assert!(ordinary.child.wait().unwrap().success());assert!(privacy.child.wait().unwrap().success());assert!(data.child.wait().unwrap().success());
    let mut death=Publisher::start();let lease=Lease::open(&death.token,&death.peer).unwrap();assert!(block_on(&death,2));death.command(0);assert!(death.child.wait().unwrap().success());wait_until(||lease.cleaned());assert!(!remote_blocks().lock().unwrap().contains(&death.token),"publisher death retires block without next data");
}
fn native_portable_queued_block_on_is_denied(){
    let mut publisher=Publisher::start();let lease=Lease::open(&publisher.token,&publisher.peer).unwrap();let before=super::super::super::block_calls().lock().unwrap().len();
    let(entered,ready)=mpsc::sync_channel(1);let(release,wait)=mpsc::sync_channel(1);assert!(native_enqueue(true,Box::new(move||{entered.send(()).unwrap();wait.recv_timeout(Duration::from_secs(4)).unwrap();true})));ready.recv_timeout(Duration::from_secs(4)).unwrap();
    let(tx,rx)=mpsc::sync_channel(1);let reply:Reply=Arc::new(move|value|{if let PortableReply::Block(_,_,ok)=value{tx.try_send(ok).is_ok()}else{false}});
    assert!(receive_block(publisher.token.clone(),own_target(),44,1,uuid::Uuid::new_v4().simple().to_string(),&publisher.peer,reply));publisher.command(1);release.send(()).unwrap();assert!(!rx.recv_timeout(Duration::from_secs(4)).unwrap());wait_until(||lease.cleaned());assert_eq!(super::super::super::block_calls().lock().unwrap().len(),before);publisher.command(0);assert!(publisher.child.wait().unwrap().success());
}
fn native_portable_text_chunks_and_control(){
    wait_until(||remotes().lock().unwrap().is_empty());assert!(native_effect(true,Box::new(||true)));
    let mut data=Publisher::start();let mut block=Publisher::start();let mut packet=data.packet();let mut key=KeyEvent::new();key.set_seq("å".repeat(200));packet.4=key.write_to_bytes().unwrap();
    super::super::super::ENTERED.store(false,Ordering::SeqCst);super::super::super::PAUSE.store(true,Ordering::SeqCst);let reply:Reply=Arc::new(|_|true);assert!(receive_async(packet,&data.peer,reply.clone()));wait_until(||super::super::super::ENTERED.load(Ordering::SeqCst));
    let mut tail=data.packet();let mut key=KeyEvent::new();key.set_seq("TAIL".to_owned());tail.4=key.write_to_bytes().unwrap();assert!(receive_async(tail,&data.peer,reply));
    let(tx,rx)=mpsc::sync_channel(1);let token=data.token.clone();let reply:Reply=Arc::new(move|value|{if let PortableReply::Block(_,_,ok)=value{let count=super::super::super::observed().lock().unwrap().iter().filter(|v|v.owner==token&&v.text=="å").count();tx.try_send((ok,count)).is_ok()}else{false}});
    assert!(receive_block(block.token.clone(),own_target(),44,1,uuid::Uuid::new_v4().simple().to_string(),&block.peer,reply));super::super::super::PAUSE.store(false,Ordering::SeqCst);
    let(ok,count)=rx.recv_timeout(Duration::from_secs(4)).unwrap();assert!(ok);assert_eq!(count,64,"queued control executes exactly after first bounded scalar chunk");
    wait_until(||super::super::super::observed().lock().unwrap().iter().filter(|v|v.owner==data.token&&v.text!="cleanup").count()==204);
    let events:Vec<_>=super::super::super::observed().lock().unwrap().iter().filter(|v|v.owner==data.token&&v.text!="cleanup").map(|v|v.text.clone()).collect();assert_eq!(events.concat(),format!("{}TAIL","å".repeat(200)),"queued text remainder preserves scalar and same-owner FIFO order");
    data.command(1);block.command(1);data.command(0);block.command(0);assert!(data.child.wait().unwrap().success());assert!(block.child.wait().unwrap().success());
}
fn native_portable_packet_overflow(){
    wait_until(||PACKETS.load(Ordering::SeqCst)==0);
    let mut publisher=Publisher::start();let(entered,ready)=mpsc::sync_channel(1);let(release,wait)=mpsc::sync_channel(1);assert!(native_enqueue(true,Box::new(move||{entered.send(()).unwrap();wait.recv_timeout(Duration::from_secs(4)).unwrap();true})));ready.recv_timeout(Duration::from_secs(4)).unwrap();
    let reply:Reply=Arc::new(|_|true);for _ in 0..MAX_PENDING{assert!(receive_async(publisher.packet(),&publisher.peer,reply.clone()));}assert!(!receive_async(publisher.packet(),&publisher.peer,reply));assert!(!remotes().lock().unwrap().get(&publisher.token).unwrap().lease.active());release.send(()).unwrap();wait_until(||PACKETS.load(Ordering::SeqCst)==0);assert!(!super::super::super::observed().lock().unwrap().iter().any(|v|v.owner==publisher.token&&v.down));publisher.command(0);assert!(publisher.child.wait().unwrap().success());
}
fn native_portable_block_reply_correlation(){
    let mut publisher=Publisher::start();let request=uuid::Uuid::new_v4().simple().to_string();let pending=Arc::new(PendingBlock{token:publisher.token.clone(),target:identity(&publisher.peer),result:AtomicUsize::new(0)});pending_blocks().lock().unwrap().insert(request.clone(),pending.clone());
    block_reply("wrong",&request,true,&publisher.peer);block_reply(&publisher.token,"unknown",true,&publisher.peer);let mut forged=publisher.peer.clone();forged.born+=1;block_reply(&publisher.token,&request,true,&forged);assert_eq!(pending.result.load(Ordering::SeqCst),0);
    block_reply(&publisher.token,&request,true,&publisher.peer);block_reply(&publisher.token,&request,false,&publisher.peer);assert_eq!(pending.result.load(Ordering::SeqCst),1);pending_blocks().lock().unwrap().remove(&request);
    let cancelled=Arc::new(PendingBlock{token:publisher.token.clone(),target:identity(&publisher.peer),result:AtomicUsize::new(0)});pending_blocks().lock().unwrap().insert(request.clone(),cancelled.clone());cancel_block(&publisher.token);assert_eq!(cancelled.result.load(Ordering::SeqCst),2);pending_blocks().lock().unwrap().remove(&request);publisher.command(0);assert!(publisher.child.wait().unwrap().success());
}
fn native_portable_receiver_block_lifecycle(){
    use super::super::portable_service::client;
    let process=Arc::new(Owned::from(unsafe{OpenProcess(SYNC|0x1000,0,GetCurrentProcessId())}).unwrap());let sid=process_sid(process.raw()).unwrap();let peer=verified_peer(unsafe{GetCurrentProcessId()},process,sid).unwrap();*helper().lock().unwrap()=Some(peer.clone());client::RUNNING.store(true,Ordering::SeqCst);
    let source=peer.clone();*client::block_transport().lock().unwrap()=Some(Arc::new(move|token,target,conn,role,request|{let reply_source=source.clone();let reply:Reply=Arc::new(move|reply|{if let PortableReply::Block(token,request,ok)=reply{block_reply(&token,&request,ok,&reply_source);true}else{false}});receive_block(token.to_owned(),target.to_owned(),conn,role,request.to_owned(),&source,reply)}));
    let source=peer.clone();*client::retire_transport().lock().unwrap()=Some(Arc::new(move|token,target,role|receive_retire(token.to_owned(),target.to_owned(),role,&source)));
    let receiver=Receiver::new(96);assert!(receiver.authorize(true));assert!(receiver.block(true,false));assert!(receiver.block(true,true));let ordinary=receiver.inner.state.lock().unwrap().portable_blocks[0].clone().unwrap();let privacy=receiver.inner.state.lock().unwrap().portable_blocks[1].clone().unwrap();assert_ne!(ordinary.token,privacy.token);
    assert!(receiver.block(false,false));assert!(ordinary.kernel.cleaned());assert!(receiver.block(true,false));let fresh=receiver.inner.state.lock().unwrap().portable_blocks[0].clone().unwrap();assert_ne!(fresh.token,ordinary.token,"Off/On creates a new nonreusable block lease");
    assert!(receiver.set_permission(false));assert!(fresh.kernel.cleaned());assert!(ordinary.kernel.cleaned());assert!(privacy.active.load(Ordering::SeqCst));assert!(remote_blocks().lock().unwrap().contains(&privacy.token));assert!(!remote_blocks().lock().unwrap().contains(&ordinary.token));assert!(receiver.close());assert!(privacy.kernel.cleaned());assert!(!remote_blocks().lock().unwrap().contains(&privacy.token));
    client::RUNNING.store(false,Ordering::SeqCst);*helper().lock().unwrap()=None;client::block_transport().lock().unwrap().take();client::retire_transport().lock().unwrap().take();
}

fn native_local_text_chunks_and_control(){
    wait_until(||generations().unwrap().lock().unwrap().is_empty());assert!(native_effect(true,Box::new(||true)));
    super::clear_observed();let receiver=Receiver::new(97);assert!(receiver.authorize(true));let mut key=KeyEvent::new();key.set_seq("å".repeat(200));super::super::super::PAUSE.store(true,Ordering::SeqCst);receiver.enqueue(Action::Key(key,false));wait_until(||super::super::super::ENTERED.load(Ordering::SeqCst));
    let mut tail=KeyEvent::new();tail.set_seq("TAIL".to_owned());receiver.enqueue(Action::Key(tail,false));let copy=receiver.clone();let block=std::thread::spawn(move||copy.block(true,false));
    wait_until(||native().unwrap().state.lock().unwrap().control.len()>0);let begin=super::super::super::block_prefixes().lock().unwrap().len();super::super::super::PAUSE.store(false,Ordering::SeqCst);assert!(block.join().unwrap());
    assert_eq!(super::super::super::block_prefixes().lock().unwrap()[begin],64,"local Block executes between bounded text chunks");wait_until(||receiver.inner.pending.load(Ordering::SeqCst)==0);
    let text:String=super::super::super::observed().lock().unwrap().iter().filter(|event|event.text!="cleanup").map(|event|event.text.as_str()).collect();assert_eq!(text,format!("{}TAIL","å".repeat(200)));assert!(!receiver.failed());assert!(receiver.close());
}
