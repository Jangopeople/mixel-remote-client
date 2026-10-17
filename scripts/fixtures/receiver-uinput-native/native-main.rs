//! Native component fixture: real Linux kernel capabilities; counted mock evdev sink.
#![allow(dead_code, non_camel_case_types, non_upper_case_globals)]
extern crate self as libc;
use std::{collections::{HashMap,HashSet},io::{BufRead,BufReader,Read,Write},os::unix::{fs::FileExt,io::AsRawFd,net::{UnixListener,UnixStream},process::CommandExt},path::PathBuf,sync::{Arc,Barrier,atomic::{AtomicUsize,Ordering}},time::{Duration,Instant}};

// Thin Linux ABI declarations only. All capability logic is the exact production
// module, with real syscalls, mmap, memfd seals, SO_PEERCRED and pidfd polling.
pub type socklen_t=u32;
#[repr(C)] pub struct ucred {pub pid:i32,pub uid:u32,pub gid:u32}
#[repr(C)] pub struct pollfd {pub fd:i32,pub events:i16,pub revents:i16}
pub const F_SEAL_WRITE:i32=8;pub const F_SEAL_SEAL:i32=1;pub const F_SEAL_SHRINK:i32=2;pub const F_SEAL_GROW:i32=4;
pub const F_ADD_SEALS:i32=1033;pub const F_GET_SEALS:i32=1034;
pub const PROT_READ:i32=1;pub const PROT_WRITE:i32=2;pub const MAP_SHARED:i32=1;
pub const MAP_FAILED:*mut std::ffi::c_void=(-1isize) as *mut std::ffi::c_void;
pub const MFD_CLOEXEC:u32=1;pub const MFD_ALLOW_SEALING:u32=2;pub const O_CLOEXEC:i32=0x80000;
pub const SOL_SOCKET:i32=1;pub const SO_PEERCRED:i32=17;pub const POLLIN:i16=1;
pub const SYS_pidfd_open:i64=434;
extern "C" {
    pub fn getsockopt(fd:i32,level:i32,name:i32,value:*mut std::ffi::c_void,length:*mut socklen_t)->i32;
    pub fn geteuid()->u32;
    pub fn syscall(number:i64,...)->i64;
    pub fn poll(fds:*mut pollfd,count:usize,timeout:i32)->i32;
    pub fn mmap(address:*mut std::ffi::c_void,length:usize,protection:i32,flags:i32,fd:i32,offset:i64)->*mut std::ffi::c_void;
    pub fn munmap(address:*mut std::ffi::c_void,length:usize)->i32;
    pub fn memfd_create(name:*const i8,flags:u32)->i32;
    pub fn fcntl(fd:i32,command:i32,...)->i32;
}
const PAGE:usize=4096;const WORD:usize=64;const USED:usize=72;
const SEALS:i32=F_SEAL_SEAL|F_SEAL_SHRINK|F_SEAL_GROW;
#[derive(Clone,Debug,PartialEq,Eq)]
pub(crate) struct WireLease {nonce:String,revoke_fd:i32,ack_fd:i32}
#[path="mixel_receiver_uinput_kernel.rs"] mod kernel;
#[path="mixel_receiver_uinput_core.rs"] mod core;
fn nonce(index:u64)->String {format!("{:032x}",index)}
fn pair()->(UnixStream,UnixStream,Arc<kernel::Peer>){let (a,b)=UnixStream::pair().unwrap();let peer=kernel::Peer::capture(a.as_raw_fd(),true).expect("real SO_PEERCRED/pidfd peer");(a,b,Arc::new(peer))}
fn passed(name:&str){println!("PASS native-kernel {}",name);}
fn wait_until(mut condition:impl FnMut()->bool){let end=Instant::now()+Duration::from_secs(3);while !condition(){assert!(Instant::now()<end,"bounded native condition timeout");std::thread::sleep(Duration::from_millis(1));}}
fn child(mode:&str,path:&str){
    let mut stream=UnixStream::connect(path).unwrap();
    if mode=="publisher" || mode=="reexec"{
        let publisher=kernel::Publisher::create(&nonce(800)).unwrap();
        writeln!(stream,"{} {} {}",publisher.wire.nonce,publisher.wire.revoke_fd,publisher.wire.ack_fd).unwrap();
        let mut command=[0];stream.read_exact(&mut command).unwrap();
        if mode=="reexec" {assert_eq!(command,[3]);let error=std::process::Command::new(std::env::current_exe().unwrap()).arg("--inert-child").exec();panic!("same executable reexec failed: {}",error);}
        assert_eq!(command,[1]);drop(publisher);return;
    }
    stream.write_all(b"ready\n").unwrap();let mut command=[0];stream.read_exact(&mut command).unwrap();assert_eq!(command,[2]);
    let error=std::process::Command::new("/bin/sleep").arg("1").exec();panic!("child exec failed: {}",error);
}
fn spawn(mode:&str,index:u64)->(std::process::Child,UnixStream,Arc<kernel::Peer>,PathBuf){
    let directory=std::env::temp_dir().join(format!("mixel-uinput-native-{}-{}",std::process::id(),index));std::fs::create_dir(&directory).unwrap();
    let path=directory.join("peer.sock");let listener=UnixListener::bind(&path).unwrap();
    let child=std::process::Command::new(std::env::current_exe().unwrap()).arg("--child").arg(mode).arg(path).spawn().unwrap();
    let (stream,_)=listener.accept().unwrap();stream.set_read_timeout(Some(Duration::from_secs(3))).unwrap();stream.set_write_timeout(Some(Duration::from_secs(3))).unwrap();
    let peer=Arc::new(kernel::Peer::capture(stream.as_raw_fd(),true).expect("kernel child peer"));(child,stream,peer,directory)
}
fn main(){
    let args:Vec<_>=std::env::args().collect();if args.get(1).map(String::as_str)==Some("--inert-child"){std::thread::sleep(Duration::from_secs(1));return;}if args.get(1).map(String::as_str)==Some("--child"){child(&args[2],&args[3]);return;}
    let (_a,_b,peer)=pair();assert!(peer.alive());passed("authenticated-same-executable-socket-peer");
    let publisher=kernel::Publisher::create(&nonce(1)).unwrap();let consumer=kernel::Consumer::open(&peer,&publisher.wire).unwrap();assert!(consumer.allowed(&peer));
    publisher.mark_used(1);consumer.ack.set(WORD,1);assert!(!publisher.denied());assert!(!publisher.retired());
    publisher.revoke.revoke();assert!(publisher.retired());passed("separate-readonly-revoke-and-ack-pages");
    let descriptor=std::fs::OpenOptions::new().write(true).open(format!("/proc/self/fd/{}",publisher.wire.revoke_fd)).unwrap();assert!(descriptor.set_len(8192).is_err());assert!(descriptor.set_len(0).is_err());passed("kernel-size-seals-refuse-resize");
    let publisher=kernel::Publisher::create(&nonce(2)).unwrap();let mut bad=publisher.wire.clone();bad.nonce=nonce(3);assert!(kernel::Consumer::open(&peer,&bad).is_none());
    bad=publisher.wire.clone();bad.ack_fd=bad.revoke_fd;assert!(kernel::Consumer::open(&peer,&bad).is_none());bad=publisher.wire.clone();bad.revoke_fd=-1;assert!(kernel::Consumer::open(&peer,&bad).is_none());passed("nonce-fd-collision-and-invalid-fd-refusals");
    let consumer=kernel::Consumer::open(&peer,&publisher.wire).unwrap();publisher.revoke.revoke();assert!(!consumer.allowed(&peer));
    // Negative control: even an erroneous publisher reset cannot revive a captured consumer.
    let descriptor=std::fs::OpenOptions::new().write(true).open(format!("/proc/self/fd/{}",publisher.wire.revoke_fd)).unwrap();assert!(descriptor.write_all_at(&0u64.to_ne_bytes(),WORD as u64).is_err());assert!(!consumer.allowed(&peer));publisher.revoke.revoke();passed("consumer-deny-is-monotonic");
    let publisher=kernel::Publisher::create(&nonce(4)).unwrap();let consumer=Arc::new(kernel::Consumer::open(&peer,&publisher.wire).unwrap());
    let barrier=Arc::new(Barrier::new(2));let actual=Arc::new(AtomicUsize::new(0));let fence=barrier.clone();let calls=actual.clone();let worker_peer=peer.clone();
    let worker=std::thread::spawn(move||{let mut ledger=core::Ledger::default();fence.wait();assert!(ledger.down(core::Claim{owner:nonce(4),temporary:0},"queued",29,true,||consumer.allowed(&worker_peer),|_,_|{calls.fetch_add(1,Ordering::SeqCst);Ok(())}).is_err());});
    publisher.revoke.revoke();barrier.wait();worker.join().unwrap();assert_eq!(actual.load(Ordering::SeqCst),0);passed("queued-down-after-revoke-barrier-refused");
    let pub_a=kernel::Publisher::create(&nonce(5)).unwrap();let pub_b=kernel::Publisher::create(&nonce(6)).unwrap();let cap_a=kernel::Consumer::open(&peer,&pub_a.wire).unwrap();let cap_b=kernel::Consumer::open(&peer,&pub_b.wire).unwrap();
    let mut ledger=core::Ledger::default();let mut effects=Vec::new();
    for (owner,cap) in [(nonce(5),&cap_a),(nonce(6),&cap_b)]{ledger.down(core::Claim{owner,temporary:0},"alias",29,true,||cap.allowed(&peer),|key,down|{effects.push((key,down));Ok(())}).unwrap();}
    pub_a.revoke.revoke();assert!(ledger.retire(|owner,_|owner.owner==nonce(5)&&!cap_a.allowed(&peer),|_,_|panic!("A released B")));assert!(ledger.held(&nonce(6)));
    pub_b.revoke.revoke();assert!(ledger.retire(|owner,_|owner.owner==nonce(6)&&!cap_b.allowed(&peer),|key,down|{effects.push((key,down));Ok(())}));assert_eq!(effects,[(29,true),(29,false)]);passed("authoritative-cross-owner-aggregate-receipts");
    let mut caps=HashMap::new();let mut bound=HashSet::new();let mut claims=core::Ledger::default();
    for index in 1000..5100 {
        let publisher=kernel::Publisher::create(&nonce(index)).unwrap();let cap=kernel::Consumer::open(&peer,&publisher.wire).unwrap();
        claims.down(core::Claim{owner:nonce(index),temporary:0},"held",29,true,||cap.allowed(&peer),|_,_|Ok(())).unwrap();
        publisher.mark_used(1);publisher.revoke.revoke();assert!(!cap.allowed(&peer));
        assert!(claims.retire(|owner,_|owner.owner==nonce(index),|_,_|Ok(())));cap.ack.set(WORD,1);assert!(publisher.retired());
        caps.insert(nonce(index),cap);core::reap_retired(&mut caps,&claims,|cap|cap.permanently_revoked() && cap.denied.load(Ordering::SeqCst) && cap.ack.load(WORD)&1!=0);assert!(caps.is_empty());
        assert!(!core::binding_known(&mut bound,&nonce(index)));bound.insert(nonce(index));assert!(bound.len()<=512);
        // A queued old Bind still opens only the original sealed deny page.
        let old=kernel::Consumer::open(&peer,&publisher.wire).unwrap();assert!(!old.allowed(&peer));
    }
    passed("4100-completed-generations-reaped-old-bind-stays-denied");
    let (mut process,mut stream,child_peer,directory)=spawn("publisher",7);let mut line=String::new();BufReader::new(stream.try_clone().unwrap()).read_line(&mut line).unwrap();let fields:Vec<_>=line.split_whitespace().collect();
    let wire=WireLease{nonce:fields[0].into(),revoke_fd:fields[1].parse().unwrap(),ack_fd:fields[2].parse().unwrap()};let consumer=kernel::Consumer::open(&child_peer,&wire).unwrap();assert!(consumer.allowed(&child_peer));
    stream.write_all(&[1]).unwrap();assert!(process.wait().unwrap().success());wait_until(||!child_peer.alive());assert!(!consumer.allowed(&child_peer));std::fs::remove_dir_all(directory).unwrap();passed("publisher-normal-exit-denies-retained-mapping-via-pidfd");
    let (mut process,mut stream,child_peer,directory)=spawn("foreign",8);let mut line=String::new();BufReader::new(stream.try_clone().unwrap()).read_line(&mut line).unwrap();assert_eq!(line,"ready\n");assert!(child_peer.alive());
    stream.write_all(&[2]).unwrap();wait_until(||!child_peer.alive());assert!(process.wait().unwrap().success());std::fs::remove_dir_all(directory).unwrap();passed("same-pid-executable-change-denied");
    let (mut process,mut stream,child_peer,directory)=spawn("reexec",9);let mut line=String::new();BufReader::new(stream.try_clone().unwrap()).read_line(&mut line).unwrap();let fields:Vec<_>=line.split_whitespace().collect();
    let wire=WireLease{nonce:fields[0].into(),revoke_fd:fields[1].parse().unwrap(),ack_fd:fields[2].parse().unwrap()};let consumer=kernel::Consumer::open(&child_peer,&wire).unwrap();assert!(consumer.allowed(&child_peer));
    stream.write_all(&[3]).unwrap();wait_until(||!consumer.allowed(&child_peer));assert!(process.wait().unwrap().success());std::fs::remove_dir_all(directory).unwrap();passed("same-executable-reexec-closed-capability-denied");
    let (mut sender,_receiver)=UnixStream::pair().unwrap();sender.set_nonblocking(true).unwrap();let bytes=[0u8;65536];
    loop {match sender.write(&bytes){Ok(0)=>panic!("closed test socket"),Ok(_)=>{},Err(error) if error.kind()==std::io::ErrorKind::WouldBlock=>break,Err(error)=>panic!("saturating test socket: {}",error)}}
    sender.set_nonblocking(false).unwrap();sender.set_write_timeout(Some(Duration::from_millis(10))).unwrap();let started=Instant::now();assert!(sender.write(&bytes).is_err());assert!(started.elapsed()<Duration::from_secs(1));passed("saturated-kernel-send-has-finite-timeout");
    println!("12 native kernel scenarios passed; evdev callbacks are counted component sinks, not desktop input proof");
}
