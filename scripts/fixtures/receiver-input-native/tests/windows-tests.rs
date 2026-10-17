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
        ("Windows create-new, readonly handles, kernel pipe peer and revoke",native_create_new_readonly_and_peer_binding as fn()),
        ("Windows owner death independent cleanup and restart target",native_owner_death_cleanup_without_next_data_and_old_restart_packets as fn()),
        ("Windows abandoned mutex permanently poisons scope",native_abandoned_mutex_poison_cannot_reopen_scope as fn()),
        ("Windows retained startup identity and once-only query-handle transfer",native_startup_identity_transfer_and_unrelated_handle_refusal as fn()),
        ("Windows exact owned suspended startup failure cleanup",native_partial_start_guard_terminates_only_owned_suspended_child as fn()),
        ("Windows late retiring-helper Pong cannot reopen admission",native_old_helper_pong_cannot_reopen_transition as fn()),
    ] {case();println!("PASS: {name}");}
}
#[cfg(feature="guard-must-not-send")]
fn acquired_mutex_guard_must_not_migrate(serial:Serial<'static>){std::thread::spawn(move||drop(serial));}

#[link(name="kernel32")]
extern "system" {
    fn SetNamedPipeHandleState(pipe:Handle,mode:*const u32,maximum:*const u32,timeout:*const u32)->i32;
    fn CreateProcessW(application:*const u16,command:*mut u16,process_attributes:Handle,thread_attributes:Handle,inherit:i32,creation:u32,environment:Handle,directory:*const u16,startup:*mut StartupInfo,info:*mut ProcessInfo)->i32;
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
    *started().lock().unwrap()=Some(Started{peer:peer.clone(),publisher:Some(transfer as usize as u64),confirmed:Arc::new(AtomicBool::new(false))});
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
    *helper().lock().unwrap()=Some(peer.clone());begin_transition();assert!(TRANSITION.load(Ordering::SeqCst));startup_confirmed(&peer);assert!(TRANSITION.load(Ordering::SeqCst),"late retiring-helper Pong cannot admit Local/old-backend scopes");
    *helper().lock().unwrap()=None;TRANSITION.store(false,Ordering::SeqCst);
}
