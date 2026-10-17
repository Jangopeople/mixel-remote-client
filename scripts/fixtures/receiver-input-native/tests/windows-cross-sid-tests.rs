// Hosted-CI-only different-account qualification. No desktop input or product ACL
// fallback: only a fresh account and an exact private fixture directory are owned.
#[repr(C)] struct QaUser1 { name:*mut u16,password:*mut u16,age:u32,privilege:u32,home:*mut u16,comment:*mut u16,flags:u32,script:*mut u16 }
#[repr(C)] struct QaUser23 { name:*mut u16,full_name:*mut u16,comment:*mut u16,flags:u32,sid:Handle }
#[repr(C)] struct QaMember0 { sid:Handle }
#[link(name="netapi32")] extern "system" {
    fn NetUserAdd(server:*const u16,level:u32,data:*const u8,error:*mut u32)->u32;
    fn NetUserGetInfo(server:*const u16,user:*const u16,level:u32,data:*mut *mut u8)->u32;
    fn NetUserDel(server:*const u16,user:*const u16)->u32;
    fn NetApiBufferFree(buffer:Handle)->u32;
    fn NetLocalGroupAddMembers(server:*const u16,group:*const u16,level:u32,data:*const u8,count:u32)->u32;
}
#[link(name="advapi32")] extern "system" {
    fn ConvertStringSidToSidW(text:*const u16,sid:*mut Handle)->i32;
    fn LookupAccountSidW(system:*const u16,sid:Handle,name:*mut u16,name_len:*mut u32,domain:*mut u16,domain_len:*mut u32,kind:*mut u32)->i32;
    fn GetSecurityInfo(object:Handle,kind:u32,information:u32,owner:*mut Handle,group:*mut Handle,dacl:*mut Handle,sacl:*mut Handle,descriptor:*mut Handle)->u32;
    fn GetSecurityDescriptorDacl(descriptor:Handle,present:*mut i32,acl:*mut Handle,defaulted:*mut i32)->i32;
}
#[link(name="userenv")] extern "system" {fn DeleteProfileW(sid:*const u16,path:*const u16,computer:*const u16)->i32;}
#[link(name="kernel32")] extern "system" {fn GetExitCodeProcess(process:Handle,code:*mut u32)->i32;}
static QA_CLEANUP_FAILED:AtomicBool=AtomicBool::new(false);
fn hosted_receiver_qa()->bool{
    std::env::var("GITHUB_ACTIONS").as_deref()==Ok("true")&&std::env::var("RUNNER_ENVIRONMENT").as_deref()==Ok("github-hosted")&&std::env::var("RUNNER_OS").as_deref()==Ok("Windows")
}
fn qa_text(pointer:*const u16)->Option<String>{
    if pointer.is_null(){return None;}let mut length=0;while length<512&&unsafe{*pointer.add(length)}!=0{length+=1;}if length==512{return None;}String::from_utf16(unsafe{std::slice::from_raw_parts(pointer,length)}).ok()
}
fn qa_sid(sid:Handle)->Option<String>{let mut text=ptr::null_mut();if unsafe{ConvertSidToStringSidW(sid,&mut text)}==0{return None;}let result=qa_text(text);unsafe{LocalFree(text.cast());}result}
struct QaNet(usize);
impl Drop for QaNet{fn drop(&mut self){unsafe{NetApiBufferFree(self.0 as Handle);}}}
fn qa_account(name:&str)->Result<(QaNet,String,String,String),u32>{
    let mut buffer=ptr::null_mut();let status=unsafe{NetUserGetInfo(ptr::null(),wide(name).as_ptr(),23,&mut buffer)};if status!=0{return Err(status);}let owned=QaNet(buffer as usize);let info=unsafe{&*(buffer as *const QaUser23)};
    Ok((owned,qa_text(info.name).ok_or(13u32)?,qa_text(info.comment).ok_or(13u32)?,qa_sid(info.sid).ok_or(13u32)?))
}
struct QaAccount{name:String,marker:String,sid:String,password:SecretCredential,profile_attempted:bool}
impl QaAccount{
    fn create()->Self{
        assert!(hosted_receiver_qa(),"account mutation is restricted to isolated hosted CI");
        let nonce=uuid::Uuid::new_v4().simple().to_string();let name=format!("mxlrx{}",&nonce[..10]);let marker=format!("Mixel receiver CI {}",&nonce[..16]);assert!(marker.len()<=48);
        assert!(matches!(qa_account(&name),Err(2221)),"fresh exact account name must be absent");
        let password=SecretCredential::new(format!("aA!9{}{}",uuid::Uuid::new_v4().simple(),uuid::Uuid::new_v4().simple()));let mut password_wide=Secret(wide(password.as_str()));let mut user=wide(&name);let mut comment=wide(&marker);
        let info=QaUser1{name:user.as_mut_ptr(),password:password_wide.0.as_mut_ptr(),age:0,privilege:1,home:ptr::null_mut(),comment:comment.as_mut_ptr(),flags:0x1|0x200|0x10000,script:ptr::null_mut()};let mut parameter=0;
        assert_eq!(unsafe{NetUserAdd(ptr::null(),1,(&info as *const QaUser1).cast(),&mut parameter)},0,"exact temporary standard account creation");
        // Publish exact newly-created ownership before any fallible query/group operation.
        let mut account=Self{name,marker,sid:String::new(),password,profile_attempted:false};
        let (_buffer,actual,description,sid)=qa_account(&account.name).expect("query fresh exact account identity");assert_eq!(actual,account.name);assert_eq!(description,account.marker);assert!(sid.starts_with("S-1-5-21-"));assert!(sid.rsplit('-').next().unwrap().parse::<u32>().unwrap()>=1000);account.sid=sid;account.verify();
        let mut group_sid=ptr::null_mut();assert_ne!(unsafe{ConvertStringSidToSidW(wide("S-1-5-32-545").as_ptr(),&mut group_sid)},0);let group_sid=OwnedDescriptor(group_sid as usize);let(mut name_len,mut domain_len,mut kind)=(0,0,0);unsafe{LookupAccountSidW(ptr::null(),group_sid.0 as Handle,ptr::null_mut(),&mut name_len,ptr::null_mut(),&mut domain_len,&mut kind);}
        assert!(name_len>0&&name_len<256&&domain_len<256);let mut group=vec![0;name_len as usize];let mut domain=vec![0;domain_len as usize];assert_ne!(unsafe{LookupAccountSidW(ptr::null(),group_sid.0 as Handle,group.as_mut_ptr(),&mut name_len,domain.as_mut_ptr(),&mut domain_len,&mut kind)},0);assert_eq!(kind,4);
        let(buffer,_,_,_)=qa_account(&account.name).unwrap();let info=unsafe{&*(buffer.0 as *const QaUser23)};let member=QaMember0{sid:info.sid};let status=unsafe{NetLocalGroupAddMembers(ptr::null(),group.as_ptr(),0,(&member as *const QaMember0).cast(),1)};assert!(status==0||status==1378,"add only the exact fresh SID to builtin Users");
        account.verify_token();account
    }
    fn verify(&self){let(_,name,marker,sid)=qa_account(&self.name).expect("owned account remains queryable");assert_eq!(name,self.name);assert_eq!(marker,self.marker);assert_eq!(sid,self.sid);}
    fn verify_token(&self){
        let password=Secret(wide(self.password.as_str()));let mut token=ptr::null_mut();assert_ne!(unsafe{LogonUserW(wide(&self.name).as_ptr(),wide(".").as_ptr(),password.0.as_ptr(),2,0,&mut token)},0,"valid memory-only account credentials");let token=Owned::from(token).unwrap();assert_eq!(token_sid(token.raw()).as_deref(),Some(self.sid.as_str()));qa_standard_token(token.raw());
    }
}
impl Drop for QaAccount{fn drop(&mut self){
    if QA_CLEANUP_FAILED.load(Ordering::SeqCst){eprintln!("FAIL: unknown owned CI process/DACL state retains account/profile");return;}
    let candidate=attempt().lock().unwrap_or_else(|error|error.into_inner()).candidate.clone();
    if let Some(process)=candidate{if unsafe{WaitForSingleObject(process.raw(),5000)}!=OBJECT{QA_CLEANUP_FAILED.store(true,Ordering::SeqCst);eprintln!("FAIL: exact owned startup candidate remains alive; account/profile retained");return;}}
    let result=(||->Result<(),u32>{let(_,name,marker,sid)=qa_account(&self.name)?;
        if self.sid.is_empty()&&name==self.name&&marker==self.marker&&sid.starts_with("S-1-5-21-"){self.sid=sid.clone();}
        if name!=self.name||marker!=self.marker||sid!=self.sid{return Err(13);}
        if self.profile_attempted{let deadline=std::time::Instant::now()+Duration::from_secs(15);loop{if unsafe{DeleteProfileW(wide(&self.sid).as_ptr(),ptr::null(),ptr::null())}!=0{break;}let error=unsafe{GetLastError()};if error==2{break;}if std::time::Instant::now()>=deadline{return Err(error);}std::thread::sleep(Duration::from_millis(100));}}
        let status=unsafe{NetUserDel(ptr::null(),wide(&self.name).as_ptr())};if status!=0{return Err(status);}if !matches!(qa_account(&self.name),Err(2221)){return Err(13);}Ok(())})();
    if let Err(code)=result{QA_CLEANUP_FAILED.store(true,Ordering::SeqCst);eprintln!("FAIL: exact owned CI account/profile cleanup; native code={code}; identity retained for recovery");}
}}
fn qa_token_buffer(token:Handle,class:u32)->Vec<usize>{let mut needed=0;unsafe{GetTokenInformation(token,class,ptr::null_mut(),0,&mut needed);}assert!(needed>0&&needed<=65536);let mut buffer=vec![0usize;(needed as usize+std::mem::size_of::<usize>()-1)/std::mem::size_of::<usize>()];assert_ne!(unsafe{GetTokenInformation(token,class,buffer.as_mut_ptr().cast(),needed,&mut needed)},0);buffer}
fn qa_standard_token(token:Handle){
    let elevation=qa_token_buffer(token,20);assert_eq!(unsafe{*(elevation.as_ptr() as *const u32)},0,"fresh standard account must not be elevated");
    let integrity=qa_token_buffer(token,25);assert_eq!(qa_sid(unsafe{*(integrity.as_ptr()as *const Handle)}).as_deref(),Some("S-1-16-8192"),"actual standard token must have Medium integrity");
    let groups=qa_token_buffer(token,2);let bytes=groups.as_ptr() as *const u8;let count=unsafe{*(bytes as *const u32)}as usize;assert!(count<=1024);#[repr(C)]struct Group{sid:Handle,attributes:u32}let offset=(4+std::mem::align_of::<Group>()-1)&!(std::mem::align_of::<Group>()-1);assert!(offset+count*std::mem::size_of::<Group>()<=groups.len()*std::mem::size_of::<usize>());
    let mut users=false;for index in 0..count{let group=unsafe{&*(bytes.add(offset+index*std::mem::size_of::<Group>())as *const Group)};let sid=qa_sid(group.sid).unwrap();assert_ne!(sid,"S-1-5-32-544","fresh fixture must never join Administrators");if sid=="S-1-5-32-545"&&group.attributes&4!=0{users=true;}}assert!(users,"standard Users membership must be enabled");
}
fn qa_file(path:&std::path::Path,access:u32)->Owned{
    let path=wide(path.to_str().unwrap());let handle=unsafe{CreateFileW(path.as_ptr(),access,7,ptr::null_mut(),3,0x02000000,ptr::null_mut())};assert!(!handle.is_null()&&handle as isize != -1,"open exact owned fixture file/directory");Owned::from(handle).unwrap()
}
fn qa_dacl(path:&std::path::Path)->Vec<u8>{
    let object=qa_file(path,0x20000);let(mut owner,mut descriptor)=(ptr::null_mut(),ptr::null_mut());assert_eq!(unsafe{GetSecurityInfo(object.raw(),1,5,&mut owner,ptr::null_mut(),ptr::null_mut(),ptr::null_mut(),&mut descriptor)},0);let descriptor=OwnedDescriptor(descriptor as usize);assert_eq!(qa_sid(owner),process_sid(unsafe{GetCurrentProcess()}));let(mut present,mut defaulted,mut acl)=(0,0,ptr::null_mut());assert_ne!(unsafe{GetSecurityDescriptorDacl(descriptor.0 as Handle,&mut present,&mut acl,&mut defaulted)},0);assert_ne!(present,0);assert!(!acl.is_null());let size=unsafe{*((acl as *const u8).add(2)as *const u16)}as usize;assert!((8..=65535).contains(&size));unsafe{std::slice::from_raw_parts(acl as *const u8,size)}.to_vec()
}
fn qa_set_acl(path:&std::path::Path,helper:Option<&str>,directory:bool){
    use std::os::windows::fs::MetadataExt;let metadata=std::fs::symlink_metadata(path).unwrap();assert_eq!(metadata.file_attributes()&0x400,0,"no reparse fixture path");let sid=process_sid(unsafe{GetCurrentProcess()}).unwrap();let flags=if directory{"OICI"}else{""};let extra=helper.map(|sid|format!("(A;{flags};0x001200a9;;;{sid})")).unwrap_or_default();let sddl=wide(&format!("O:{sid}D:P(A;{flags};FA;;;{sid})(A;{flags};FA;;;SY){extra}"));let mut descriptor=ptr::null_mut();assert_ne!(unsafe{ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.as_ptr(),1,&mut descriptor,ptr::null_mut())},0);let descriptor=OwnedDescriptor(descriptor as usize);let object=qa_file(path,0xe0000);assert_ne!(unsafe{SetKernelObjectSecurity(object.raw(),1|4|0x80000000,descriptor.0 as Handle)},0);let _=qa_dacl(path);
}
fn qa_root()->std::path::PathBuf{
    let root=std::env::current_exe().unwrap().parent().unwrap().to_path_buf();let name=root.file_name().unwrap().to_str().unwrap();let nonce=name.strip_prefix("MixelReceiverLogon-").expect("exact owned CI root prefix");assert_eq!(nonce.len(),32);assert!(nonce.bytes().all(|c|c.is_ascii_hexdigit()&&!c.is_ascii_uppercase()));assert_eq!(std::fs::read(root.join("owner.marker")).unwrap(),nonce.as_bytes());root
}
fn qa_pipe_path()->String{format!("\\\\.\\pipe\\{}",qa_root().file_name().unwrap().to_str().unwrap())}
fn qa_pipe_wait(file:&mut std::fs::File,bytes:&mut [u8]){
    use std::io::Read;let deadline=std::time::Instant::now()+Duration::from_secs(5);let mut offset=0;while offset<bytes.len(){match file.read(&mut bytes[offset..]){Ok(0)=>panic!("owned fixture peer exited"),Ok(size)=>offset+=size,Err(error)if matches!(error.raw_os_error(),Some(232|536|231))=>{},Err(error)=>panic!("owned pipe read failed; native={:?}",error.raw_os_error())}assert!(std::time::Instant::now()<deadline,"bounded pipe read");std::thread::sleep(Duration::from_millis(1));}
}
fn qa_pipe_send(file:&mut std::fs::File,bytes:&[u8]){
    use std::io::Write;let deadline=std::time::Instant::now()+Duration::from_secs(5);let mut offset=0;while offset<bytes.len(){match file.write(&bytes[offset..]){Ok(size)=>offset+=size,Err(error)if matches!(error.raw_os_error(),Some(232|536|231))=>{},Err(error)=>panic!("owned pipe write failed; native={:?}",error.raw_os_error())}assert!(std::time::Instant::now()<deadline,"bounded pipe write");std::thread::sleep(Duration::from_millis(1));}
}
fn qa_send_text(file:&mut std::fs::File,text:&str){assert!(text.len()<=256);qa_pipe_send(file,&(text.len()as u32).to_le_bytes());qa_pipe_send(file,text.as_bytes());}
fn qa_read_text(file:&mut std::fs::File)->String{let mut bytes=[0;4];qa_pipe_wait(file,&mut bytes);let size=u32::from_le_bytes(bytes)as usize;assert!(size<=256);let mut text=vec![0;size];qa_pipe_wait(file,&mut text);String::from_utf8(text).unwrap()}
struct QaChild{process:Arc<Owned>}
impl Drop for QaChild{fn drop(&mut self){if unsafe{WaitForSingleObject(self.process.raw(),0)}==TIMEOUT{unsafe{TerminateProcess(self.process.raw(),1);}if unsafe{WaitForSingleObject(self.process.raw(),5000)}!=OBJECT{QA_CLEANUP_FAILED.store(true,Ordering::SeqCst);eprintln!("FAIL: exact owned CI child exit is unconfirmed");}}}}
fn qa_parent_body(){
    use std::os::windows::io::{AsRawHandle,FromRawHandle};assert!(hosted_receiver_qa());let mut caller_token=ptr::null_mut();assert_ne!(unsafe{OpenProcessToken(GetCurrentProcess(),8,&mut caller_token)},0);let caller_token=Owned::from(caller_token).unwrap();let elevation=qa_token_buffer(caller_token.raw(),20);assert_eq!(unsafe{*(elevation.as_ptr()as *const u32)},1,"isolated hosted caller must actually be elevated");let root=qa_root();let executable=std::env::current_exe().unwrap();let mut account=QaAccount::create();let original_root=qa_dacl(&root);let original_file=qa_dacl(&executable);let marker=root.join("owner.marker");let original_marker=qa_dacl(&marker);
    struct AclRestore{root:std::path::PathBuf,file:std::path::PathBuf,marker:std::path::PathBuf,before_root:Vec<u8>,before_file:Vec<u8>,before_marker:Vec<u8>}
    impl Drop for AclRestore{fn drop(&mut self){let result=std::panic::catch_unwind(std::panic::AssertUnwindSafe(||{qa_set_acl(&self.file,None,false);qa_set_acl(&self.marker,None,false);qa_set_acl(&self.root,None,true);assert_eq!(qa_dacl(&self.file),self.before_file);assert_eq!(qa_dacl(&self.marker),self.before_marker);assert_eq!(qa_dacl(&self.root),self.before_root);}));if result.is_err(){QA_CLEANUP_FAILED.store(true,Ordering::SeqCst);eprintln!("FAIL: exact private CI directory DACL restoration");}}}
    let _restore=AclRestore{root:root.clone(),file:executable.clone(),marker:marker.clone(),before_root:original_root,before_file:original_file,before_marker:original_marker};qa_set_acl(&root,Some(&account.sid),true);qa_set_acl(&executable,Some(&account.sid),false);qa_set_acl(&marker,Some(&account.sid),false);
    let own_sid=process_sid(unsafe{GetCurrentProcess()}).unwrap();assert_ne!(own_sid,account.sid);let sddl=wide(&format!("D:P(A;;GA;;;{own_sid})(A;;GRGW;;;{})",account.sid));let mut descriptor=ptr::null_mut();assert_ne!(unsafe{ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.as_ptr(),1,&mut descriptor,ptr::null_mut())},0);let descriptor=OwnedDescriptor(descriptor as usize);let attributes=Attributes{length:std::mem::size_of::<Attributes>()as u32,descriptor:descriptor.0 as Handle,inherit:0};
    let raw=unsafe{CreateNamedPipeW(wide(&qa_pipe_path()).as_ptr(),3,1,1,4096,4096,5000,(&attributes as *const Attributes)as Handle)};assert!(!raw.is_null()&&raw as isize != -1);let pipe=Owned::from(raw).unwrap();account.profile_attempted=true;
    assert!(launch_with_logon(&account.name,account.password.as_str(),executable.to_str().unwrap(),"--portable-service").is_some(),"actual valid different-SID suspended logon launch");let process=started().lock().unwrap().as_ref().unwrap().peer.process.clone();let _child=QaChild{process:process.clone()};
    let deadline=std::time::Instant::now()+Duration::from_secs(5);loop{if unsafe{ConnectNamedPipe(pipe.raw(),ptr::null_mut())}!=0||unsafe{GetLastError()}==535{break;}assert!(std::time::Instant::now()<deadline,"different-SID pipe connect");std::thread::sleep(Duration::from_millis(1));}
    let peer=pipe_peer(pipe.raw(),false).expect("actual retained different-SID startup identity");assert_eq!(peer.sid,account.sid);assert_eq!(peer.pid,unsafe{GetProcessId(process.raw())});let transferred=publisher_handle(&peer).unwrap().unwrap();assert!(publisher_handle(&peer).is_none());
    let lease=Lease::create(&uuid::Uuid::new_v4().simple().to_string()).unwrap();assert!(lease.prepare_helper().is_some());let raw=pipe.raw();std::mem::forget(pipe);let mut pipe=unsafe{std::fs::File::from_raw_handle(raw)};qa_pipe_send(&mut pipe,&transferred.to_le_bytes());qa_send_text(&mut pipe,&account.sid);qa_send_text(&mut pipe,&lease.token);let mut ready=[0];qa_pipe_wait(&mut pipe,&mut ready);assert_eq!(ready,[1]);assert!(startup_confirmed(&peer,||{}));assert!(!TRANSITION.load(Ordering::SeqCst));
    assert!(lease.revoke());let mut cleaned=[0];qa_pipe_wait(&mut pipe,&mut cleaned);assert_eq!(cleaned,[2]);assert!(lease.cleaned());qa_pipe_send(&mut pipe,&[3]);assert_eq!(unsafe{WaitForSingleObject(process.raw(),5000)},OBJECT);let mut code=1;assert_ne!(unsafe{GetExitCodeProcess(process.raw(),&mut code)},0);assert_eq!(code,0,"actual different-SID helper component exited successfully");
    drop(pipe);drop(peer);drop(lease);started().lock().unwrap().take();helper().lock().unwrap().take();attempt().lock().unwrap().candidate.take();account.verify();println!("PASS: actual different-SID standard logon, retained startup/pipe publisher identity, minimum intended kernel rights and revoke acknowledgment");
}
pub(crate) fn cross_sid_parent(){assert!(hosted_receiver_qa());let result=std::panic::catch_unwind(qa_parent_body);assert!(!QA_CLEANUP_FAILED.load(Ordering::SeqCst),"owned CI cleanup must complete");if let Err(error)=result{std::panic::resume_unwind(error);}println!("PASS: exact owned CI standard account/profile and private directory DACL cleanup");}
pub(crate) fn cross_sid_logon_child(){
    use std::os::windows::io::{AsRawHandle,FromRawHandle};let path=qa_pipe_path();let deadline=std::time::Instant::now()+Duration::from_secs(5);let raw=loop{let raw=unsafe{CreateFileW(wide(&path).as_ptr(),0xc0000000,0,ptr::null_mut(),3,0,ptr::null_mut())};if !raw.is_null()&&raw as isize != -1{break raw;}assert!(std::time::Instant::now()<deadline,"exact different-SID helper pipe");std::thread::sleep(Duration::from_millis(1));};let mode=1;assert_ne!(unsafe{SetNamedPipeHandleState(raw,&mode,ptr::null(),ptr::null())},0);let mut pipe=unsafe{std::fs::File::from_raw_handle(raw)};
    let mut transferred=[0;8];qa_pipe_wait(&mut pipe,&mut transferred);let publisher=pipe_publisher(pipe.as_raw_handle(),Some(u64::from_le_bytes(transferred))).expect("actual transferred publisher process identity");let expected=qa_read_text(&mut pipe);assert_eq!(process_sid(unsafe{GetCurrentProcess()}).as_deref(),Some(expected.as_str()));let mut token=ptr::null_mut();assert_ne!(unsafe{OpenProcessToken(GetCurrentProcess(),8,&mut token)},0);let token=Owned::from(token).unwrap();qa_standard_token(token.raw());
    let file=std::env::current_exe().unwrap();let denied=unsafe{CreateFileW(wide(file.to_str().unwrap()).as_ptr(),0x40000000,7,ptr::null_mut(),3,0,ptr::null_mut())};assert!(denied as isize == -1||denied.is_null(),"intended account must have Read/Execute only on fixture executable");assert_eq!(unsafe{GetLastError()},5);
    let nonce=qa_read_text(&mut pipe);let lease=Lease::open(&nonce,&publisher).expect("intended different-SID consumer opens exact minimum lease handles");assert!(lease.active());assert_eq!(unsafe{SetEvent(lease.event.raw())},0,"consumer cannot publish or reset REVOKED");assert_eq!(unsafe{GetLastError()},5);{let _effect=lease.effect().expect("consumer SYNCHRONIZE mutex effect ownership");}
    qa_pipe_send(&mut pipe,&[1]);let deadline=std::time::Instant::now()+Duration::from_secs(5);while lease.active(){assert!(std::time::Instant::now()<deadline);std::thread::sleep(Duration::from_millis(1));}assert!(lease.effect().is_none());assert!(lease.mark_cleaned(),"only intended helper may acknowledge CLEANED");qa_pipe_send(&mut pipe,&[2]);let mut exit=[0];qa_pipe_wait(&mut pipe,&mut exit);assert_eq!(exit,[3]);
}
fn native_valid_different_sid_ci_startup(){
    if !hosted_receiver_qa(){println!("SKIP: account-mutating different-SID fixture requires isolated hosted Windows CI");return;}
    use std::os::windows::fs::MetadataExt;let base=std::path::PathBuf::from(std::env::var_os("ProgramData").expect("hosted ProgramData"));assert_eq!(std::fs::symlink_metadata(&base).unwrap().file_attributes()&0x400,0);let nonce=uuid::Uuid::new_v4().simple().to_string();let root=base.join(format!("MixelReceiverLogon-{nonce}"));std::fs::create_dir(&root).unwrap();qa_set_acl(&root,None,true);std::fs::write(root.join("owner.marker"),nonce.as_bytes()).unwrap();qa_set_acl(&root.join("owner.marker"),None,false);let executable=root.join("receiver-cross-sid.exe");std::fs::copy(std::env::current_exe().unwrap(),&executable).unwrap();qa_set_acl(&executable,None,false);
    let mut child=OwnedChild(std::process::Command::new(&executable).arg("--mixel-cross-sid-parent").spawn().expect("launch exact private CI fixture copy"));let deadline=std::time::Instant::now()+Duration::from_secs(90);let status=loop{if let Some(status)=child.try_wait().unwrap(){break status;}assert!(std::time::Instant::now()<deadline,"bounded owned CI fixture; private root retained on failure");std::thread::sleep(Duration::from_millis(20));};assert!(status.success(),"different-SID qualification failed; private owned root retained for recovery");assert_eq!(std::fs::read(root.join("owner.marker")).unwrap(),nonce.as_bytes());assert_eq!(std::fs::symlink_metadata(&root).unwrap().file_attributes()&0x400,0);std::fs::remove_dir_all(root).unwrap();
}
