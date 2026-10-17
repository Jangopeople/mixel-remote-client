//! Scoped SAS broker. SendSAS is VOID: this records invocation, never delivery.
//! Local generation objects stay in their session; read-only duplicates cross it.
use std::{ffi::c_void, ptr, sync::{atomic::{AtomicBool, AtomicU64, Ordering}, Mutex}};
use hbb_common::{bail, timeout, ResultType};
type Handle = *mut c_void;
const SYNC: u32 = 0x0010_0000;
const QUERY: u32 = 0x1000;
const DUP: u32 = 0x0040;
const OBJECT: u32 = 0;
const TIMEOUT: u32 = 258;
const ABANDONED: u32 = 128;
const WAIT_MS: u32 = 1000;
const SYSTEM: &str = "S-1-5-18";
static NEXT: AtomicU64 = AtomicU64::new(1);
static POISONED: AtomicBool = AtomicBool::new(false);
#[repr(C)] #[derive(Default)] struct FileTime { low: u32, high: u32 }
#[link(name="kernel32")]
extern "system" {
    fn GetCurrentProcess() -> Handle;
    fn GetCurrentProcessId() -> u32;
    fn GetProcessId(process: Handle) -> u32;
    fn GetProcessTimes(process: Handle, born: *mut FileTime, exit: *mut FileTime, kernel: *mut FileTime, user: *mut FileTime) -> i32;
    fn QueryFullProcessImageNameW(process: Handle, flags: u32, path: *mut u16, length: *mut u32) -> i32;
    fn ProcessIdToSessionId(pid: u32, session: *mut u32) -> i32;
    fn GetNamedPipeServerProcessId(pipe: Handle, pid: *mut u32) -> i32;
    fn GetNamedPipeClientProcessId(pipe: Handle, pid: *mut u32) -> i32;
    fn OpenProcess(access: u32, inherit: i32, pid: u32) -> Handle;
    fn OpenEventW(access: u32, inherit: i32, name: *const u16) -> Handle;
    fn OpenMutexW(access: u32, inherit: i32, name: *const u16) -> Handle;
    fn DuplicateHandle(source_process: Handle, source: Handle, target_process: Handle, target: *mut Handle, access: u32, inherit: i32, options: u32) -> i32;
    fn WaitForSingleObject(object: Handle, millis: u32) -> u32;
    fn ReleaseMutex(object: Handle) -> i32;
    fn CloseHandle(object: Handle) -> i32;
    fn LocalFree(object: Handle) -> Handle;
}
#[link(name="advapi32")]
extern "system" {
    fn OpenProcessToken(process: Handle, access: u32, token: *mut Handle) -> i32;
    fn GetTokenInformation(token: Handle, class: u32, data: Handle, length: u32, needed: *mut u32) -> i32;
    fn ConvertSidToStringSidW(sid: Handle, text: *mut *mut u16) -> i32;
    fn ImpersonateLoggedOnUser(token: Handle) -> i32;
    fn RevertToSelf() -> i32;
}
#[link(name="sas")]
extern "system" { fn SendSAS(as_user: i32); }
struct Owned(Handle);
impl Owned { fn new(raw: Handle) -> Option<Self> { (!raw.is_null() && raw as isize != -1).then_some(Self(raw)) } }
impl Drop for Owned { fn drop(&mut self) { unsafe { CloseHandle(self.0); } } }
fn wide(text: &str) -> Vec<u16> { text.encode_utf16().chain(Some(0)).collect() }
fn birth(process: Handle) -> Option<u64> {
    let (mut b,mut e,mut k,mut u)=(FileTime::default(),FileTime::default(),FileTime::default(),FileTime::default());
    (unsafe { GetProcessTimes(process,&mut b,&mut e,&mut k,&mut u) } != 0).then_some((b.high as u64)<<32 | b.low as u64)
}
fn image(process: Handle) -> Option<String> {
    let mut path=vec![0u16;32768];let mut count=path.len() as u32;
    if unsafe { QueryFullProcessImageNameW(process,0,path.as_mut_ptr(),&mut count) } == 0 || count==0 || count as usize>=path.len() { return None; }
    String::from_utf16(&path[..count as usize]).ok().map(|v|v.to_lowercase())
}
fn session(pid: u32) -> Option<u32> { let mut value=u32::MAX;(unsafe { ProcessIdToSessionId(pid,&mut value) } != 0 && value!=u32::MAX).then_some(value) }
fn token(process: Handle, access: u32) -> Option<Owned> { let mut raw=ptr::null_mut();(unsafe { OpenProcessToken(process,access,&mut raw) } != 0).then(||Owned::new(raw)).flatten() }
fn token_session(token: Handle) -> Option<u32> {
    let mut value=u32::MAX;let mut needed=0;
    (unsafe { GetTokenInformation(token,12,(&mut value as *mut u32).cast(),4,&mut needed) } != 0 && needed==4 && value!=u32::MAX).then_some(value)
}
fn token_sid(token: Handle) -> Option<String> {
    let mut needed=0;unsafe { GetTokenInformation(token,1,ptr::null_mut(),0,&mut needed); }
    if !(std::mem::size_of::<usize>() as u32..=65536).contains(&needed) { return None; }
    let mut data=vec![0usize;(needed as usize+std::mem::size_of::<usize>()-1)/std::mem::size_of::<usize>()];
    if unsafe { GetTokenInformation(token,1,data.as_mut_ptr().cast(),needed,&mut needed) } == 0 { return None; }
    let sid=unsafe { *(data.as_ptr() as *const Handle) };let mut text=ptr::null_mut();
    if unsafe { ConvertSidToStringSidW(sid,&mut text) } == 0 { return None; }
    let mut n=0;while n<256 && unsafe { *text.add(n) } != 0 { n+=1; }
    let result=if n<256 { String::from_utf16(unsafe { std::slice::from_raw_parts(text,n) }).ok() } else { None };
    unsafe { LocalFree(text.cast()); }result
}
fn alive(process: Handle) -> bool { unsafe { WaitForSingleObject(process,0)==TIMEOUT } }
fn duplicate(source_process: Handle, object: Handle, access: u32) -> Option<Owned> {
    if object.is_null() || (object as isize) < 0 { return None; }
    let mut output=ptr::null_mut();
    (unsafe { DuplicateHandle(source_process,object,GetCurrentProcess(),&mut output,access,0,0) } != 0).then(||Owned::new(output)).flatten()
}
fn pipe_pid(pipe: Handle, server: bool) -> Option<u32> {
    let mut pid=0;let ok=unsafe { if server { GetNamedPipeServerProcessId(pipe,&mut pid) } else { GetNamedPipeClientProcessId(pipe,&mut pid) } };
    (ok!=0 && pid!=0).then_some(pid)
}
fn identity(owner: &str) -> Option<(u32,u64)> {
    if owner.len()>96 { return None; }
    let mut parts=owner.split('-');let pid_text=parts.next()?;let born_text=parts.next()?;
    let pid=pid_text.parse::<u32>().ok()?;let born=born_text.parse::<u64>().ok()?;let nonce=parts.next()?;
    if pid.to_string()!=pid_text || born.to_string()!=born_text { return None; }
    if parts.next().is_some() || pid==0 || born==0 || nonce.len()!=32 || !nonce.bytes().all(|c|c.is_ascii_digit()||(b'a'..=b'f').contains(&c)) { return None; }
    Some((pid,born))
}
fn policy_eligible(value: Option<u32>) -> bool { matches!(value,Some(1)|Some(3)) }
// Pure decision core is compiled separately with the exact production bodies.
struct Authority {
    pipe_pid: Option<u32>, launched_pid: u32, born: u64,
    owner: Option<(u32,u64)>, publisher_session: Option<u32>,
    token_session: Option<u32>, target_session: u32,
    service_session: Option<u32>, service_system: bool,
    publisher_system: bool, image_match: bool, process_alive: bool,
}
fn authority_eligible(v: &Authority) -> bool {
    v.target_session!=0 && v.target_session!=u32::MAX && v.launched_pid!=0 && v.born!=0
        && v.pipe_pid==Some(v.launched_pid) && v.owner==Some((v.launched_pid,v.born))
        && v.publisher_session==Some(v.target_session) && v.token_session==Some(v.target_session)
        && v.service_session==Some(0) && v.service_system && v.publisher_system
        && v.image_match && v.process_alive
}
fn reserve(seen: &mut (u32,u64,u64), pid: u32, born: u64, sequence: u64) -> bool {
    if pid==0 || born==0 || sequence==0 { return false; }
    let previous=if (seen.0,seen.1)==(pid,born) { seen.2 } else { 0 };
    if sequence<=previous { return false; }
    *seen=(pid,born,sequence);true
}
fn service_policy() -> bool {
    use winreg::{RegKey,enums::{HKEY_LOCAL_MACHINE,KEY_READ}};
    RegKey::predef(HKEY_LOCAL_MACHINE).open_subkey_with_flags("Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\System",KEY_READ)
        .ok().and_then(|key|key.get_value::<u32,_>("SoftwareSASGeneration").ok()).map(|v|policy_eligible(Some(v))).unwrap_or(false)
}
struct SasPublisherLease { revoked: Owned, effect: Owned }
impl SasPublisherLease {
    fn open(owner: &str) -> Option<Self> {
        let (pid,born)=identity(owner)?;
        if pid!=unsafe { GetCurrentProcessId() } || birth(unsafe { GetCurrentProcess() })?!=born { return None; }
        let stem=format!("Local\\Mixel-Remote-Input-v1-{owner}");
        let revoked=Owned::new(unsafe { OpenEventW(SYNC,0,wide(&(stem.clone()+"-revoked")).as_ptr()) })?;
        let effect=Owned::new(unsafe { OpenMutexW(SYNC,0,wide(&(stem+"-effect")).as_ptr()) })?;
        Some(Self { revoked,effect })
    }
    fn active(&self) -> bool { unsafe { WaitForSingleObject(self.revoked.0,0)==TIMEOUT } }
}
fn service_peer(pipe: Handle) -> Option<Owned> {
    let pid=pipe_pid(pipe,true)?;let process=Owned::new(unsafe { OpenProcess(SYNC|QUERY,0,pid) })?;
    let token=token(process.0,8)?;
    if session(pid)?!=0 || token_sid(token.0)?!=SYSTEM || image(process.0)?!=image(unsafe { GetCurrentProcess() })? || !alive(process.0) || pipe_pid(pipe,true)?!=pid { return None; }
    Some(process)
}
/// Capture ACK imports handles before sender closes them. Commit precedes the
/// sink mutex wait; sender returns and releases its already-held generation guard.
pub(crate) async fn request(owner: &str) -> ResultType<()> {
    let Some(lease)=SasPublisherLease::open(owner) else { bail!("SAS requires the current installed-server receiver generation"); };
    let mut stream=crate::ipc::connect(1000,crate::POSTFIX_SERVICE).await
        .map_err(|error|hbb_common::anyhow::anyhow!("SAS requires the installed Mixel SYSTEM service; start/install it through an explicit administrator action (no automatic elevation): {}",error))?;
    let Some(service)=service_peer(stream.sas_pipe_handle()) else { bail!("SAS service identity could not be authenticated"); };
    if !lease.active() || !crate::server::input_service::sas_effect_admitted() { bail!("SAS receiver generation was revoked"); }
    let sequence=NEXT.fetch_update(Ordering::SeqCst,Ordering::SeqCst,|v|v.checked_add(1)).map_err(|_|hbb_common::anyhow::anyhow!("SAS request identity exhausted"))?;
    let request=crate::ipc::Data::SASScoped { owner:owner.to_owned(),sequence,revoked:lease.revoked.0 as usize as u64,effect:lease.effect.0 as usize as u64 };
    timeout(1000,stream.send(&request)).await??;
    match stream.next_timeout(1000).await? {
        Some(crate::ipc::Data::SASReady { sequence:seen,error }) if seen==sequence => if let Some(error)=error { bail!("Scoped SAS service refused: {}",error); },
        _ => bail!("SAS service did not capture this request; outcome unknown, do not retry"),
    }
    if !alive(service.0) || pipe_pid(stream.sas_pipe_handle(),true)!=Some(unsafe { GetProcessId(service.0) }) || !lease.active() || !crate::server::input_service::sas_effect_admitted() { bail!("SAS authority changed before commit"); }
    timeout(1000,stream.send(&crate::ipc::Data::SASCommit(sequence))).await??;
    Ok(()) // Queued only. SendSAS has no OS delivery result.
}
struct Serial<'a>(&'a Owned,std::marker::PhantomData<std::rc::Rc<()>>);
impl Drop for Serial<'_> { fn drop(&mut self) { if unsafe { ReleaseMutex(self.0.0) }==0 { POISONED.store(true,Ordering::SeqCst); } } }
struct Impersonation;
impl Impersonation {
    fn enter(token: Handle) -> Option<Self> { (unsafe { ImpersonateLoggedOnUser(token) }!=0).then_some(Self) }
}
impl Drop for Impersonation { fn drop(&mut self) {
    if unsafe { RevertToSelf() }==0 {
        // Microsoft requires shutdown if reverting fails. Exit only this broken
        // service process; continuing under a caller token is not permitted.
        std::process::exit(1);
    }
} }
fn history() -> &'static Mutex<(u32,u64,u64)> { static VALUE: std::sync::OnceLock<Mutex<(u32,u64,u64)>>=std::sync::OnceLock::new();VALUE.get_or_init(||Mutex::new((0,0,0))) }
pub(crate) struct Captured { process: Owned, revoked: Owned, effect: Owned, token: Owned, pid: u32, born: u64, session: u32, sequence: u64 }
impl Captured {
    pub(crate) fn capture(pipe: Handle, owner: &str, sequence: u64, revoked: u64, effect: u64, launched: Handle, target_session: u32) -> Result<Self,&'static str> {
        if POISONED.load(Ordering::SeqCst) || sequence==0 || target_session==0 || target_session==u32::MAX { return Err("SAS service authority unavailable"); }
        let process=duplicate(unsafe { GetCurrentProcess() },launched,SYNC|QUERY|DUP).ok_or("Retained service server handle unavailable")?;
        let pid=unsafe { GetProcessId(process.0) };let born=birth(process.0).ok_or("Server creation time unavailable")?;
        let expected_image=image(unsafe { GetCurrentProcess() }).ok_or("Service image identity unavailable")?;
        let own=token(unsafe { GetCurrentProcess() },8).ok_or("Service token unavailable")?;
        let caller=token(process.0,8|2).ok_or("Server impersonation token unavailable")?;
        let authority=Authority { pipe_pid:pipe_pid(pipe,false),launched_pid:pid,born,owner:identity(owner),
            publisher_session:session(pid),token_session:token_session(caller.0),target_session,
            service_session:session(unsafe { GetCurrentProcessId() }),service_system:token_sid(own.0).as_deref()==Some(SYSTEM),
            publisher_system:token_sid(caller.0).as_deref()==Some(SYSTEM),
            image_match:image(process.0).as_deref()==Some(expected_image.as_str()),process_alive:alive(process.0) };
        if !authority_eligible(&authority) { return Err("SAS caller is not the current SYSTEM service-launched server/session"); }
        if !service_policy() { return Err("Enable the installed service's Services/Both Software SAS policy through administrator policy"); }
        let revoked=usize::try_from(revoked).ok().and_then(|h|duplicate(process.0,h as Handle,SYNC)).ok_or("SAS revoke handle import failed")?;
        let effect=usize::try_from(effect).ok().and_then(|h|duplicate(process.0,h as Handle,SYNC)).ok_or("SAS serialization handle import failed")?;
        if pipe_pid(pipe,false)!=Some(pid) || !alive(process.0) { return Err("SAS publisher changed while importing handles"); }
        let mut seen=history().lock().map_err(|_|"SAS replay state poisoned")?;
        if !reserve(&mut seen,pid,born,sequence) { return Err("SAS request replay refused"); }
        Ok(Self { process,revoked,effect,token:caller,pid,born,session:target_session,sequence })
    }
    fn active(&self) -> bool { !POISONED.load(Ordering::SeqCst) && alive(self.process.0) && birth(self.process.0)==Some(self.born) && session(self.pid)==Some(self.session) && token_session(self.token.0)==Some(self.session) && unsafe { WaitForSingleObject(self.revoked.0,0)==TIMEOUT } }
    pub(crate) fn dispatch(self, sequence: u64) -> Result<(),&'static str> {
        if sequence!=self.sequence || !self.active() { return Err("SAS generation denied before dispatch"); }
        let _serial=match unsafe { WaitForSingleObject(self.effect.0,WAIT_MS) } {
            OBJECT => Serial(&self.effect,std::marker::PhantomData),
            ABANDONED => { unsafe { ReleaseMutex(self.effect.0); } POISONED.store(true,Ordering::SeqCst);return Err("Abandoned SAS generation serialization refused"); },
            _ => return Err("SAS serialization timeout/refusal"),
        };
        if !self.active() || !service_policy() { return Err("SAS generation or policy revoked under serialization"); }
        let _impersonation=Impersonation::enter(self.token.0).ok_or("SAS caller-token impersonation failed")?;
        if !self.active() { return Err("SAS generation denied at actual sink"); }
        unsafe { SendSAS(0); }
        Ok(())
    }
}
