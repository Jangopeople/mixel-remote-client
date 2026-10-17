// Appended only after the exact final production Windows leaf.
#[cfg(test)] mod custody_qa {
use super::*;
use hbb_common::futures_util::StreamExt;
use std::{os::windows::io::{AsRawHandle,FromRawHandle},time::Instant};
#[link(name="kernel32")]extern "system"{
    fn CreateNamedPipeW(name:*const u16,access:u32,mode:u32,instances:u32,out_size:u32,in_size:u32,timeout:u32,security:Handle)->Handle;
    fn ConnectNamedPipe(pipe:Handle,overlapped:Handle)->i32;
    fn CreateFileW(name:*const u16,access:u32,share:u32,security:Handle,disposition:u32,flags:u32,template:Handle)->Handle;
    fn SetNamedPipeHandleState(pipe:Handle,mode:*const u32,maximum:*const u32,timeout:*const u32)->i32;
}
// PREPARE inserts the exact existing memory-only QA account helpers here.
// @EXACT_ACCOUNT_HELPERS@
struct OwnedChild(std::process::Child);
impl std::ops::Deref for OwnedChild{type Target=std::process::Child;fn deref(&self)->&Self::Target{&self.0}}
impl std::ops::DerefMut for OwnedChild{fn deref_mut(&mut self)->&mut Self::Target{&mut self.0}}
impl Drop for OwnedChild{fn drop(&mut self){if matches!(self.0.try_wait(),Ok(None)){let _=self.0.kill();let _=self.0.wait();}}}
fn until(mut test:impl FnMut()->bool){let end=Instant::now()+Duration::from_secs(12);while !test(){assert!(Instant::now()<end,"bounded actual custody observation");std::thread::sleep(Duration::from_millis(10));}}
fn runtime()->tokio::runtime::Runtime{tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap()}
fn effects()->Vec<serde_json::Value>{
    let root=std::path::PathBuf::from(std::env::var_os("MIXEL_CUSTODY_QA_OUTPUT").unwrap());
    let mut values=Vec::new();for entry in std::fs::read_dir(root).unwrap(){let entry=entry.unwrap();let name=entry.file_name();let name=name.to_str().unwrap();
        if !name.starts_with("effects-")||!name.ends_with(".jsonl"){continue;}
        assert!(entry.file_type().unwrap().is_file());for line in std::fs::read_to_string(entry.path()).unwrap().lines(){values.push(serde_json::from_str(line).unwrap());}
    }values
}
fn count(case:&str,down:bool)->usize{effects().iter().filter(|v|v["case"]==case&&v["kind"]=="mockKey"&&v["down"]==down).count()}
fn key()->KeyEvent{let mut value=KeyEvent::new();value.set_chr(30);value.down=true;value}
fn current()->String{custodian::current_identity().unwrap()}
fn completion(token:&str)->Owned{let(_,_,name,_,_)=names(token).unwrap();Owned::from(unsafe{OpenEventW(SYNC,0,wide(&name).as_ptr())}).unwrap()}
async fn text_send(stream:&mut crate::ipc::Connection,value:&str){stream.send(&crate::ipc::Data::Config(("qa".into(),Some(value.into())))).await.unwrap();}
async fn text_read(stream:&mut crate::ipc::Connection)->String{match stream.next_timeout(15000).await.unwrap(){Some(crate::ipc::Data::Config((key,Some(value))))if key=="qa"=>value,_=>panic!("actual owned QA control message")}}
fn assert_child(peer:&Peer,child:&OwnedChild){assert_eq!(peer.pid,child.id());assert_eq!(image(peer.process.raw()),image(unsafe{GetCurrentProcess()}));assert!(peer.alive());}
fn local_actor(postfix:&str){runtime().block_on(async{
    let mut stream=crate::ipc::connect(5000,postfix).await.unwrap();assert!(stream.receiver_pipe_peer(true).is_some());
    let receiver=super::super::Receiver::new(7001);assert!(receiver.authorize(true));
    let token=receiver.inner.state.lock().unwrap().generation.as_ref().unwrap().token.clone();
    receiver.enqueue(Action::Key(key(),false));
    let case=std::env::var("MIXEL_CUSTODY_QA_CASE").unwrap();until(||count(&case,true)==1);
    text_send(&mut stream,&token).await;text_send(&mut stream,&custodian::epoch().unwrap()).await;
    let command=text_read(&mut stream).await;
    if command=="unknown"{
        assert!(!custodian::ready(),"dead effect custodian cannot silently restart");
        let fresh=super::super::Receiver::new(7002);assert!(!fresh.authorize(true),"unknown old effects refuse a fresh grant");
        text_send(&mut stream,"refused-fresh-grant").await;
        loop{std::thread::sleep(Duration::from_millis(10));}
    }
    assert_eq!(command,"revoke");assert!(receiver.close());assert!(custodian::disconnect());text_send(&mut stream,"closed").await;
});}
fn local_death_case(){runtime().block_on(async{
    let case="local-broker-death";let postfix=format!("qa-{}",uuid::Uuid::new_v4().simple());let mut listener=crate::ipc::new_listener(&postfix).await.unwrap();
    let mut actor=OwnedChild(std::process::Command::new(std::env::current_exe().unwrap()).args(["--qa-local-actor",&postfix]).env("MIXEL_CUSTODY_QA_CASE",case).spawn().unwrap());
    let raw=timeout(5000,listener.next()).await.unwrap().unwrap().unwrap();let mut stream=crate::ipc::Connection::new(raw);
    let peer=stream.receiver_pipe_peer(false).unwrap();assert_child(&peer,&actor);
    let token=text_read(&mut stream).await;assert_eq!(names(&token).unwrap().3,actor.id());assert_eq!(names(&token).unwrap().4,peer.born);
    let _epoch=text_read(&mut stream).await;let cleaned=completion(&token);assert_eq!(count(case,true),1);assert_eq!(count(case,false),0);
    actor.kill().unwrap();actor.wait().unwrap();until(||unsafe{WaitForSingleObject(cleaned.raw(),0)}==OBJECT);until(||count(case,false)==1);
    assert_eq!(count(case,true),1);assert_eq!(count(case,false),1);assert!(unsafe{WaitForSingleObject(GetCurrentProcess(),0)}==TIMEOUT);
    println!("PASS: exact local publisher death leaves actual custodian and truthful mock Up/CLEANED");
});}
fn local_revoke_case(){runtime().block_on(async{
    let case="local-revoke";let postfix=format!("qa-{}",uuid::Uuid::new_v4().simple());let mut listener=crate::ipc::new_listener(&postfix).await.unwrap();
    let mut actor=OwnedChild(std::process::Command::new(std::env::current_exe().unwrap()).args(["--qa-local-actor",&postfix]).env("MIXEL_CUSTODY_QA_CASE",case).spawn().unwrap());
    let mut stream=crate::ipc::Connection::new(timeout(5000,listener.next()).await.unwrap().unwrap().unwrap());assert_child(&stream.receiver_pipe_peer(false).unwrap(),&actor);
    let token=text_read(&mut stream).await;let _epoch=text_read(&mut stream).await;let cleaned=completion(&token);
    text_send(&mut stream,"revoke").await;assert_eq!(text_read(&mut stream).await,"closed");assert!(actor.wait().unwrap().success());
    assert_eq!(unsafe{WaitForSingleObject(cleaned.raw(),0)},OBJECT);assert_eq!((count(case,true),count(case,false)),(1,1));
    println!("PASS: real local capture/ACK, production receipt ledger and actual cleanup-thread completion");
});}
fn unknown_custodian_case(){runtime().block_on(async{
    let case="unknown-custodian-death";let postfix=format!("qa-{}",uuid::Uuid::new_v4().simple());let mut listener=crate::ipc::new_listener(&postfix).await.unwrap();
    let mut actor=OwnedChild(std::process::Command::new(std::env::current_exe().unwrap()).args(["--qa-local-actor",&postfix]).env("MIXEL_CUSTODY_QA_CASE",case).spawn().unwrap());
    let mut stream=crate::ipc::Connection::new(timeout(5000,listener.next()).await.unwrap().unwrap().unwrap());assert_child(&stream.receiver_pipe_peer(false).unwrap(),&actor);
    let token=text_read(&mut stream).await;let identity=text_read(&mut stream).await;let cleaned=completion(&token);
    let(pid,born)=identity.split(':').next().unwrap().split_once('-').unwrap();let pid=pid.parse::<u32>().unwrap();let born=born.parse::<u64>().unwrap();
    let child=Owned::from(unsafe{OpenProcess(SYNC|0x1000|1,0,pid)}).unwrap();assert_eq!(birth(child.raw()),Some(born));assert_eq!(image(child.raw()),image(unsafe{GetCurrentProcess()}));
    assert_ne!(unsafe{TerminateProcess(child.raw(),78)},0);assert_eq!(unsafe{WaitForSingleObject(child.raw(),5000)},OBJECT);
    text_send(&mut stream,"unknown").await;assert_eq!(text_read(&mut stream).await,"refused-fresh-grant");
    assert_eq!(unsafe{WaitForSingleObject(cleaned.raw(),0)},TIMEOUT,"effect-child death is never CLEANED");assert_eq!((count(case,true),count(case,false)),(1,0));
    actor.kill().unwrap();actor.wait().unwrap();
    let mut fresh=OwnedChild(std::process::Command::new(std::env::current_exe().unwrap()).arg("--qa-expect-dirty").env("MIXEL_CUSTODY_QA_CASE",case).spawn().unwrap());
    until(||fresh.try_wait().unwrap().is_some());assert!(fresh.wait().unwrap().success());assert_eq!((count(case,true),count(case,false)),(1,0));
    println!("PASS: lost effect child leaves real dirty custody marker, no false Up/CLEANED and no automatic regrant; mock effect boundary only");
});}
fn qa_journal_acl(path:&std::path::Path,sid:&str){
    let caller=process_sid(unsafe{GetCurrentProcess()}).unwrap();let text=wide(&format!("O:{caller}D:P(A;OICI;FA;;;{caller})(A;OICI;FA;;;SY)(A;OICI;FA;;;{sid})"));
    let mut descriptor=ptr::null_mut();assert_ne!(unsafe{ConvertStringSecurityDescriptorToSecurityDescriptorW(text.as_ptr(),1,&mut descriptor,ptr::null_mut())},0);let descriptor=OwnedDescriptor(descriptor as usize);let object=qa_file(path,0xe0000);assert_ne!(unsafe{SetKernelObjectSecurity(object.raw(),1|4|0x80000000,descriptor.0 as Handle)},0);
}
fn qa_cross_parent(){
    assert!(hosted_receiver_qa());let root=qa_root();let executable=std::env::current_exe().unwrap();let mut account=QaAccount::create();
    let before_root=qa_dacl(&root);let before_file=qa_dacl(&executable);let marker=root.join("owner.marker");let before_marker=qa_dacl(&marker);
    struct Restore{root:std::path::PathBuf,exe:std::path::PathBuf,marker:std::path::PathBuf,before:[Vec<u8>;3]}
    impl Drop for Restore{fn drop(&mut self){let good=std::panic::catch_unwind(std::panic::AssertUnwindSafe(||{qa_set_acl(&self.exe,None,false);qa_set_acl(&self.marker,None,false);qa_set_acl(&self.root,None,true);assert_eq!(qa_dacl(&self.root),self.before[0]);assert_eq!(qa_dacl(&self.exe),self.before[1]);assert_eq!(qa_dacl(&self.marker),self.before[2]);}));if good.is_err(){QA_CLEANUP_FAILED.store(true,Ordering::SeqCst);}}}
    let _restore=Restore{root:root.clone(),exe:executable.clone(),marker:marker.clone(),before:[before_root,before_file,before_marker]};
    qa_set_acl(&root,Some(&account.sid),true);qa_set_acl(&executable,Some(&account.sid),false);qa_set_acl(&marker,Some(&account.sid),false);
    let journal=root.join("custody-journal");std::fs::create_dir(&journal).unwrap();qa_journal_acl(&journal,&account.sid);
    std::env::set_var("MIXEL_CUSTODY_QA_OUTPUT",&journal);std::env::set_var("MIXEL_CUSTODY_QA_CASE","different-sid-helper-death");
    let own_sid=process_sid(unsafe{GetCurrentProcess()}).unwrap();assert_ne!(own_sid,account.sid);
    let sddl=wide(&format!("D:P(A;;GA;;;{own_sid})(A;;GRGW;;;{})",account.sid));let mut descriptor=ptr::null_mut();assert_ne!(unsafe{ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.as_ptr(),1,&mut descriptor,ptr::null_mut())},0);let descriptor=OwnedDescriptor(descriptor as usize);let attributes=Attributes{length:std::mem::size_of::<Attributes>()as u32,descriptor:descriptor.0 as Handle,inherit:0};
    let raw=unsafe{CreateNamedPipeW(wide(&qa_pipe_path()).as_ptr(),3,1,1,4096,4096,5000,(&attributes as *const Attributes)as Handle)};assert!(!raw.is_null()&&raw as isize!=-1);let pipe=Owned::from(raw).unwrap();account.profile_attempted=true;
    assert!(launch_with_logon(&account.name,account.password.as_str(),executable.to_str().unwrap(),"--portable-service").is_some());let process=started().lock().unwrap().as_ref().unwrap().peer.process.clone();let _child=QaChild{process:process.clone()};
    let end=Instant::now()+Duration::from_secs(5);loop{if unsafe{ConnectNamedPipe(pipe.raw(),ptr::null_mut())}!=0||unsafe{GetLastError()}==535{break;}assert!(Instant::now()<end);std::thread::sleep(Duration::from_millis(1));}
    let peer=pipe_peer(pipe.raw(),false).unwrap();assert_eq!(peer.sid,account.sid);let transferred=publisher_handle(&peer).unwrap().unwrap();assert!(publisher_handle(&peer).is_none());
    let lease=Lease::create(&uuid::Uuid::new_v4().simple().to_string()).unwrap();assert!(lease.prepare_helper().is_some());let raw=pipe.raw();std::mem::forget(pipe);let mut file=unsafe{std::fs::File::from_raw_handle(raw)};
    qa_pipe_send(&mut file,&transferred.to_le_bytes());qa_send_text(&mut file,&account.sid);qa_send_text(&mut file,&lease.token);
    let mut ready=[0];qa_pipe_wait(&mut file,&mut ready);assert_eq!(ready,[1]);assert!(startup_confirmed(&peer,||{}));
    let child_identity=qa_read_text(&mut file);let(pid,born)=child_identity.split_once('-').unwrap();let pid=pid.parse::<u32>().unwrap();let born=born.parse::<u64>().unwrap();
    let custodian=Owned::from(unsafe{OpenProcess(SYNC|0x1000,0,pid)}).unwrap();assert_eq!(birth(custodian.raw()),Some(born));assert_eq!(image(custodian.raw()),image(unsafe{GetCurrentProcess()}));
    assert_eq!(count("different-sid-helper-death",true),1);assert!(!lease.cleaned());assert!(peer.alive());
    // Only the exact freshly-created expendable actor is terminated. The actual
    // effect-owning custodian is retained and must clean without actor/publisher.
    assert_ne!(unsafe{TerminateProcess(process.raw(),77)},0);assert_eq!(unsafe{WaitForSingleObject(process.raw(),5000)},OBJECT);
    until(||lease.cleaned());until(||count("different-sid-helper-death",false)==1);assert_eq!(unsafe{WaitForSingleObject(lease.event.raw(),0)},TIMEOUT,"original publisher is alive and has not revoked");
    assert_eq!(unsafe{WaitForSingleObject(custodian.raw(),10000)},OBJECT);assert!(lease.revoke());
    drop(file);drop(peer);drop(lease);started().lock().unwrap().take();helper().lock().unwrap().take();attempt().lock().unwrap().candidate.take();account.verify();
    println!("PASS: actual different-SID Medium helper and same-token custodian; original publisher lives; one mock Up/CLEANED after helper death");
}
fn qa_cross_child(){
    let path=qa_pipe_path();let end=Instant::now()+Duration::from_secs(5);let raw=loop{let raw=unsafe{CreateFileW(wide(&path).as_ptr(),0xc0000000,0,ptr::null_mut(),3,0,ptr::null_mut())};if !raw.is_null()&&raw as isize!=-1{break raw;}assert!(Instant::now()<end);std::thread::sleep(Duration::from_millis(1));};let mode=1;assert_ne!(unsafe{SetNamedPipeHandleState(raw,&mode,ptr::null(),ptr::null())},0);let mut pipe=unsafe{std::fs::File::from_raw_handle(raw)};
    let mut handle=[0;8];qa_pipe_wait(&mut pipe,&mut handle);let publisher=pipe_publisher(pipe.as_raw_handle(),Some(u64::from_le_bytes(handle))).unwrap();let sid=qa_read_text(&mut pipe);assert_eq!(process_sid(unsafe{GetCurrentProcess()}).as_deref(),Some(sid.as_str()));
    let mut token=ptr::null_mut();assert_ne!(unsafe{OpenProcessToken(GetCurrentProcess(),8,&mut token)},0);let token=Owned::from(token).unwrap();qa_standard_token(token.raw());
    let nonce=qa_read_text(&mut pipe);let lease=Lease::open(&nonce,&publisher).unwrap();assert!(lease.active());assert_eq!(unsafe{SetEvent(lease.event.raw())},0);assert_eq!(unsafe{GetLastError()},5);
    assert!(custodian::ready());let packet=(nonce,current(),88,1,key().write_to_bytes().unwrap(),String::new(),0,false,false);
    assert!(custodian::input(packet,&publisher,Arc::new(|_|true)));until(||count("different-sid-helper-death",true)==1);
    qa_pipe_send(&mut pipe,&[1]);let epoch=custodian::epoch().unwrap();qa_send_text(&mut pipe,epoch.split(':').next().unwrap());
    loop{std::thread::sleep(Duration::from_millis(10));}
}
fn cross_sid_case(){
    assert!(hosted_receiver_qa(),"different-SID case is mandatory hosted Windows only");
    use std::os::windows::fs::MetadataExt;let base=std::path::PathBuf::from(std::env::var_os("ProgramData").unwrap());assert_eq!(std::fs::symlink_metadata(&base).unwrap().file_attributes()&0x400,0);
    let nonce=uuid::Uuid::new_v4().simple().to_string();let root=base.join(format!("MixelReceiverLogon-{nonce}"));std::fs::create_dir(&root).unwrap();qa_set_acl(&root,None,true);std::fs::write(root.join("owner.marker"),&nonce).unwrap();qa_set_acl(&root.join("owner.marker"),None,false);
    let executable=root.join("custody-cross-sid.exe");std::fs::copy(std::env::current_exe().unwrap(),&executable).unwrap();qa_set_acl(&executable,None,false);
    let mut child=OwnedChild(std::process::Command::new(&executable).arg("--qa-cross-parent").spawn().unwrap());let end=Instant::now()+Duration::from_secs(90);while child.try_wait().unwrap().is_none(){assert!(Instant::now()<end,"bounded owned different-SID child; cleanup unqualified on timeout");std::thread::sleep(Duration::from_millis(20));}assert!(child.wait().unwrap().success(),"different-SID custody case");
    let output=std::path::PathBuf::from(std::env::var_os("MIXEL_CUSTODY_QA_OUTPUT").unwrap());for entry in std::fs::read_dir(root.join("custody-journal")).unwrap(){let entry=entry.unwrap();assert!(entry.file_type().unwrap().is_file());std::fs::copy(entry.path(),output.join(entry.file_name())).unwrap();}
    assert_eq!(std::fs::read(root.join("owner.marker")).unwrap(),nonce.as_bytes());assert_eq!(std::fs::symlink_metadata(&root).unwrap().file_attributes()&0x400,0);std::fs::remove_dir_all(root).unwrap();
}
pub(super) fn main(){
    let nonce=std::env::var("MIXEL_CUSTODY_QA_NAMESPACE").expect("fresh owned test namespace");assert!(custodian::qa_namespace(&nonce));
    if custodian::entry(){return;}
    let args:Vec<String>=std::env::args().collect();match args.get(1).map(String::as_str){
        Some("--qa-local-actor")=>{assert_eq!(args.len(),3);local_actor(&args[2]);return;},
        Some("--portable-service")=>{qa_cross_child();return;},
        Some("--qa-cross-parent")=>{let result=std::panic::catch_unwind(qa_cross_parent);assert!(!QA_CLEANUP_FAILED.load(Ordering::SeqCst));if let Err(error)=result{std::panic::resume_unwind(error);}return;},
        Some("--qa-expect-dirty")=>{let fresh=super::super::Receiver::new(7003);assert!(!fresh.authorize(true),"new broker process cannot hide old dirty custody epoch");return;},
        None=>{},_=>panic!("unknown private QA mode"),
    }
    custodian::custody_component_decisions();super::super::custody_stale_work_case();local_revoke_case();local_death_case();cross_sid_case();unknown_custodian_case();
    custodian::qa_remove_owned_namespace();
    println!("PASS: whole final receiver/custodian/epoch prefixes; real kernel identities/processes; mock effects only; application/OS custody-death acceptance remains separate");
}
}
#[cfg(test)] pub(super) fn custody_fixture_main(){custody_qa::main();}
