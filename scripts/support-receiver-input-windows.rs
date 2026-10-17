//! Windows portable receiver lease. Names carry no bearer data and are never logged.
use super::*;
use std::{ffi::c_void, ptr};
type Handle = *mut c_void;
const SYNC: u32 = 0x0010_0000;
const MUTEX_ACCESS: u32 = SYNC;
const EVENT_ACCESS: u32 = SYNC | 2;
const OBJECT: u32 = 0;
const TIMEOUT: u32 = 258;
const ABANDONED: u32 = 128;
#[repr(C)] struct Attributes { length: u32, descriptor: Handle, inherit: i32 }
#[repr(C)] #[derive(Default)] struct FileTime { low: u32, high: u32 }
#[link(name="kernel32")]
extern "system" {
    fn CreateEventExW(a: *const Attributes, name: *const u16, flags: u32, access: u32) -> Handle;
    fn CreateMutexExW(a: *const Attributes, name: *const u16, flags: u32, access: u32) -> Handle;
    fn GetNamedPipeServerProcessId(pipe: Handle, pid: *mut u32) -> i32;
    fn GetNamedPipeClientProcessId(pipe: Handle, pid: *mut u32) -> i32;
    fn OpenEventW(access: u32, inherit: i32, name: *const u16) -> Handle;
    fn OpenMutexW(access: u32, inherit: i32, name: *const u16) -> Handle;
    fn SetEvent(handle: Handle) -> i32;
    fn ReleaseMutex(handle: Handle) -> i32;
    fn WaitForSingleObject(handle: Handle, millis: u32) -> u32;
    fn GetLastError() -> u32;
    fn CloseHandle(handle: Handle) -> i32;
    fn LocalFree(handle: Handle) -> Handle;
    fn GetCurrentProcess() -> Handle;
    fn GetCurrentProcessId() -> u32;
    fn GetProcessId(process: Handle) -> u32;
    fn DuplicateHandle(source_process: Handle, source: Handle, target_process: Handle, target: *mut Handle, access: u32, inherit: i32, options: u32) -> i32;
    fn ResumeThread(thread: Handle) -> u32;
    fn TerminateProcess(process: Handle, code: u32) -> i32;
    fn OpenProcess(access: u32, inherit: i32, pid: u32) -> Handle;
    fn GetProcessTimes(process: Handle, born: *mut FileTime, exit: *mut FileTime, kernel: *mut FileTime, user: *mut FileTime) -> i32;
    fn QueryFullProcessImageNameW(process: Handle, flags: u32, name: *mut u16, count: *mut u32) -> i32;
    fn ProcessIdToSessionId(pid: u32, session: *mut u32) -> i32;
}
#[link(name="advapi32")]
extern "system" {
    fn ConvertStringSecurityDescriptorToSecurityDescriptorW(s: *const u16, revision: u32, descriptor: *mut Handle, size: *mut u32) -> i32;
    fn LogonUserW(user: *const u16, domain: *const u16, password: *const u16, kind: u32, provider: u32, token: *mut Handle) -> i32;
    fn CreateProcessWithLogonW(user: *const u16, domain: *const u16, password: *const u16, flags: u32, application: *const u16, command: *mut u16, creation: u32, environment: Handle, directory: *const u16, startup: *mut StartupInfo, info: *mut ProcessInfo) -> i32;
    fn OpenProcessToken(process: Handle, access: u32, token: *mut Handle) -> i32;
    fn GetTokenInformation(token: Handle, class: u32, data: Handle, size: u32, needed: *mut u32) -> i32;
    fn ConvertSidToStringSidW(sid: Handle, text: *mut *mut u16) -> i32;
    fn SetKernelObjectSecurity(handle: Handle, information: u32, descriptor: Handle) -> i32;
}
struct Owned(usize);
impl Owned {
    fn raw(&self) -> Handle { self.0 as Handle }
    fn from(handle: Handle) -> Option<Self> { (!handle.is_null()).then_some(Self(handle as usize)) }
}
impl Drop for Owned { fn drop(&mut self) { unsafe { CloseHandle(self.raw()); } } }
fn wide(value: &str) -> Vec<u16> { value.encode_utf16().chain(Some(0)).collect() }
fn birth(process: Handle) -> Option<u64> {
    let (mut born,mut exit,mut kernel,mut user) = (FileTime::default(),FileTime::default(),FileTime::default(),FileTime::default());
    (unsafe { GetProcessTimes(process,&mut born,&mut exit,&mut kernel,&mut user) } != 0)
        .then_some((born.high as u64) << 32 | born.low as u64)
}
fn image(process: Handle) -> Option<String> {
    let mut buffer = [0u16; 32768]; let mut size = buffer.len() as u32;
    if unsafe { QueryFullProcessImageNameW(process,0,buffer.as_mut_ptr(),&mut size) } == 0 || size == 0 || size as usize >= buffer.len() { return None; }
    String::from_utf16(&buffer[..size as usize]).ok().map(|v| v.to_lowercase())
}
fn process_sid(process: Handle) -> Option<String> {
    let mut token = ptr::null_mut();
    if unsafe { OpenProcessToken(process,8,&mut token) } == 0 { return None; }
    let token = Owned::from(token)?;
    token_sid(token.raw())
}
fn token_sid(token: Handle) -> Option<String> {
    let mut needed = 0;
    unsafe { GetTokenInformation(token,1,ptr::null_mut(),0,&mut needed); }
    if !(std::mem::size_of::<usize>() as u32..=65536).contains(&needed) { return None; }
    // usize alignment is required by TOKEN_USER's SID_AND_ATTRIBUTES pointer.
    let mut buffer = vec![0usize; (needed as usize + std::mem::size_of::<usize>() - 1) / std::mem::size_of::<usize>()];
    if unsafe { GetTokenInformation(token,1,buffer.as_mut_ptr().cast(),needed,&mut needed) } == 0 { return None; }
    let sid = unsafe { *(buffer.as_ptr() as *const Handle) }; let mut text = ptr::null_mut();
    if unsafe { ConvertSidToStringSidW(sid,&mut text) } == 0 { return None; }
    let mut length = 0; while length < 256 && unsafe { *text.add(length) } != 0 { length += 1; }
    let result = if length < 256 { String::from_utf16(unsafe { std::slice::from_raw_parts(text,length) }).ok() } else { None };
    unsafe { LocalFree(text.cast()); } result
}
fn descriptor(helper: Option<&str>, helper_access: u32) -> Option<OwnedDescriptor> {
    let sid = process_sid(unsafe { GetCurrentProcess() })?;
    let extra = helper.filter(|helper| *helper != sid).map(|helper| format!("(A;;{:#010x};;;{})", helper_access, helper)).unwrap_or_default();
    let sddl = wide(&format!("D:P(A;;GA;;;{sid}){extra}S:(ML;;NW;;;ME)"));
    let mut descriptor = ptr::null_mut();
    if unsafe { ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.as_ptr(),1,&mut descriptor,ptr::null_mut()) } == 0 { return None; }
    Some(OwnedDescriptor(descriptor as usize))
}
struct OwnedDescriptor(usize);
impl Drop for OwnedDescriptor { fn drop(&mut self) { unsafe { LocalFree(self.0 as Handle); } } }
fn create_object(name: &str, event: bool) -> Option<Owned> {
    let descriptor = descriptor(None,SYNC)?;
    let attributes = Attributes { length: std::mem::size_of::<Attributes>() as u32, descriptor: descriptor.0 as Handle, inherit: 0 }; let name = wide(name);
    let handle = unsafe { if event { CreateEventExW(&attributes,name.as_ptr(),1,EVENT_ACCESS | 0x0004_0000) } else { CreateMutexExW(&attributes,name.as_ptr(),0,MUTEX_ACCESS | 0x0004_0000) } };
    let collision = unsafe { GetLastError() } == 183;
    let owned = Owned::from(handle)?;
    if collision { None } else { Some(owned) }
}
fn names(token: &str) -> Option<(String,String,String,u32,u64)> {
    let mut parts = token.split('-'); let pid = parts.next()?.parse::<u32>().ok()?;
    let born = parts.next()?.parse::<u64>().ok()?; let nonce = parts.next()?;
    if parts.next().is_some() || pid == 0 || born == 0 || nonce.len()!=32 || !nonce.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) { return None; }
    let prefix = format!("Local\\Mixel-Remote-Input-v1-{token}");
    Some((format!("{prefix}-revoked"),format!("{prefix}-effect"),format!("{prefix}-cleaned"),pid,born))
}
pub(super) struct Lease { pub(super) token: String, event: Owned, mutex: Owned, cleaned: Owned, process: Option<Arc<Owned>>, poisoned: AtomicBool }
pub(super) struct Serial<'a>(&'a Owned, std::marker::PhantomData<std::rc::Rc<()>>);
impl Drop for Serial<'_> { fn drop(&mut self) { unsafe { ReleaseMutex(self.0.raw()); } } }
impl Lease {
    pub(super) fn create(nonce: &str) -> Option<Self> {
        let pid = unsafe { GetCurrentProcessId() }; let born = birth(unsafe { GetCurrentProcess() })?;
        let token = format!("{pid}-{born}-{nonce}"); let (event,mutex,cleaned,_,_) = names(&token)?;
        Some(Self { token, event: create_object(&event,true)?, mutex: create_object(&mutex,false)?, cleaned: create_object(&cleaned,true)?, process: None, poisoned: AtomicBool::new(false) })
    }
    fn open(token: &str, peer: &Peer) -> Option<Self> {
        let (event,mutex,cleaned,pid,born) = names(token)?;
        if pid != peer.pid || born != peer.born || !peer.alive() { return None; }
        let event = Owned::from(unsafe { OpenEventW(SYNC,0,wide(&event).as_ptr()) })?;
        let mutex = Owned::from(unsafe { OpenMutexW(MUTEX_ACCESS,0,wide(&mutex).as_ptr()) })?;
        let cleaned = Owned::from(unsafe { OpenEventW(EVENT_ACCESS,0,wide(&cleaned).as_ptr()) })?;
        Some(Self { token: token.to_owned(), event, mutex, cleaned, process: Some(peer.process.clone()), poisoned: AtomicBool::new(false) })
    }
    fn prepare_helper(&self) -> Option<String> {
        let helper = helper().lock().ok()?.clone()?;
        if !helper.alive() { return None; }
        let event = descriptor(Some(&helper.sid),SYNC)?;
        let mutex = descriptor(Some(&helper.sid),MUTEX_ACCESS)?;
        let cleaned = descriptor(Some(&helper.sid),EVENT_ACCESS)?;
        // The creator alone publishes the explicit intended helper SID, using a
        // peer obtained from the kernel pipe, never from packet-supplied names.
        if unsafe { SetKernelObjectSecurity(self.event.raw(),4,event.0 as Handle) } == 0 || unsafe { SetKernelObjectSecurity(self.mutex.raw(),4,mutex.0 as Handle) } == 0 || unsafe { SetKernelObjectSecurity(self.cleaned.raw(),4,cleaned.0 as Handle) } == 0 { return None; }
        Some(format!("{}-{}",helper.pid,helper.born))
    }

    fn authority_live(&self) -> bool {
        (unsafe { WaitForSingleObject(self.event.raw(),0) }) == TIMEOUT && self.process.as_ref().map(|p| (unsafe { WaitForSingleObject(p.raw(),0) }) == TIMEOUT).unwrap_or(true)
    }
    fn active(&self) -> bool {
        !self.poisoned.load(Ordering::SeqCst) && (unsafe { WaitForSingleObject(self.event.raw(),0) }) == TIMEOUT && self.process.as_ref().map(|p| (unsafe { WaitForSingleObject(p.raw(),0) }) == TIMEOUT).unwrap_or(true)
    }
    fn lock(&self) -> Option<Serial<'_>> {
        match unsafe { WaitForSingleObject(self.mutex.raw(),5_000) } {
            OBJECT => Some(Serial(&self.mutex, std::marker::PhantomData)),
            ABANDONED => {
                self.poisoned.store(true, Ordering::SeqCst);
                let serial = Serial(&self.mutex, std::marker::PhantomData); if cleanup(&self.token) { self.mark_cleaned(); } drop(serial); None
            },
            _ => None,
        }
    }
    pub(super) fn cleaned(&self) -> bool { unsafe { WaitForSingleObject(self.cleaned.raw(),0) == OBJECT } }
    fn mark_cleaned(&self) -> bool { unsafe { SetEvent(self.cleaned.raw()) != 0 } }
    pub(super) fn try_retired(&self) -> bool {
        if self.poisoned.load(Ordering::SeqCst) { return false; }
        match unsafe { WaitForSingleObject(self.mutex.raw(),0) } {
            OBJECT => { unsafe { ReleaseMutex(self.mutex.raw()); } true },
            ABANDONED => { self.poisoned.store(true, Ordering::SeqCst); unsafe { ReleaseMutex(self.mutex.raw()); } false },
            _ => false,
        }
    }
    pub(super) fn effect(&self) -> Option<Serial<'_>> { if !self.active() { return None; } let guard = self.lock()?; self.active().then_some(guard) }
    pub(super) fn revoke(&self) -> bool {
        // Publish deny first: waiting data cannot acquire an effective grant.
        // Only the creator's handle has EVENT_MODIFY_STATE.
        if unsafe { SetEvent(self.event.raw()) } == 0 {
            // An owned event refusing deny is a broken kernel trust boundary.
            // Exiting only this publisher makes every consumer's retained process
            // lease fail closed; it does not signal any unrelated process.
            std::process::exit(1);
        }
        // Retirement and owned cleanup are observed separately; publish does not
        // wait for data/dispatch/IPC. The source retains this scope until CLEANED.
        true
    }
}
#[derive(Clone)]
pub(crate) struct Peer { pid: u32, born: u64, process: Arc<Owned>, sid: String }
impl Peer { fn alive(&self) -> bool { (unsafe { WaitForSingleObject(self.process.raw(),0) }) == TIMEOUT } }
fn helper() -> &'static Mutex<Option<Peer>> {
    static HELPER: OnceLock<Mutex<Option<Peer>>> = OnceLock::new(); HELPER.get_or_init(|| Mutex::new(None))
}
fn pipe_pid(pipe: Handle, server_peer: bool) -> Option<u32> {
    let mut pid = 0;
    let ok = unsafe { if server_peer { GetNamedPipeServerProcessId(pipe,&mut pid) } else { GetNamedPipeClientProcessId(pipe,&mut pid) } };
    (ok != 0 && pid != 0).then_some(pid)
}
fn verified_peer(pid: u32, process: Arc<Owned>, sid: String) -> Option<Peer> {
    if unsafe { GetProcessId(process.raw()) } != pid { return None; }
    let born = birth(process.raw())?;
    if image(process.raw())? != image(unsafe { GetCurrentProcess() })? { return None; }
    let (mut source,mut target) = (u32::MAX,u32::MAX);
    if unsafe { ProcessIdToSessionId(pid,&mut source) } == 0 || unsafe { ProcessIdToSessionId(GetCurrentProcessId(),&mut target) } == 0 || source == 0 || source != target { return None; }
    let peer = Peer { pid, born, sid, process };
    peer.alive().then_some(peer)
}
pub(crate) fn pipe_peer(pipe: Handle, server_peer: bool) -> Option<Peer> {
    let pid = pipe_pid(pipe,server_peer)?;
    // An explicitly credentialed child has a retained real startup process
    // handle and exact logon-token SID, so no different-account token query.
    let retained = if !server_peer {
        let mut startup = started().lock().ok()?;
        if startup.as_ref().map(|s| !s.peer.alive()).unwrap_or(false) { startup.take(); }
        startup.as_ref().filter(|s| s.peer.pid == pid).map(|s| s.peer.clone())
    } else { None };
    let peer = if let Some(peer) = retained {
        let verified = verified_peer(pid,peer.process.clone(),peer.sid.clone())?;
        if verified.born != peer.born { return None; } verified
    } else {
        let process = Owned::from(unsafe { OpenProcess(SYNC | 0x1000,0,pid) })?;
        let sid = if server_peer { String::new() } else { process_sid(process.raw())? };
        verified_peer(pid,Arc::new(process),sid)?
    };
    if !server_peer { *helper().lock().ok()? = Some(peer.clone()); }
    Some(peer)
}
pub(crate) fn pipe_publisher(pipe: Handle, transferred: Option<u64>) -> Option<Peer> {
    let Some(raw) = transferred else { return pipe_peer(pipe,true); };
    let pid = pipe_pid(pipe,true)?;
    let raw = usize::try_from(raw).ok()? as Handle;
    if raw.is_null() || unsafe { GetProcessId(raw) } != pid { return None; }
    // Inspect borrowed first. Never close an unvalidated packet-supplied handle
    // (it might name an unrelated child object); a failed child then exits.
    if birth(raw).is_none() || image(raw)? != image(unsafe { GetCurrentProcess() })? { return None; }
    let (mut a,mut b) = (0,0);
    if unsafe { ProcessIdToSessionId(pid,&mut a) } == 0 || unsafe { ProcessIdToSessionId(GetCurrentProcessId(),&mut b) } == 0 || a == 0 || a != b || unsafe { WaitForSingleObject(raw,0) } != TIMEOUT { return None; }
    verified_peer(pid,Arc::new(Owned::from(raw)?),String::new())
}
struct Started { peer: Peer, publisher: Option<u64>, confirmed: Arc<AtomicBool> }
fn started() -> &'static Mutex<Option<Started>> { static STARTED: OnceLock<Mutex<Option<Started>>> = OnceLock::new(); STARTED.get_or_init(|| Mutex::new(None)) }
pub(crate) fn publisher_handle(peer: &Peer) -> Option<Option<u64>> {
    let mut startup = started().lock().ok()?;
    if let Some(startup) = startup.as_mut().filter(|s| s.peer.pid == peer.pid && s.peer.born == peer.born) {
        return startup.publisher.take().map(Some);
    }
    Some(None)
}
pub(crate) fn startup_confirmed(peer: &Peer) {
    // Caller publishes RUNNING first, so a fresh grant can never select Local.
    TRANSITION.store(false,Ordering::SeqCst);
    if let Ok(startup) = started().lock() { if let Some(startup) = startup.as_ref().filter(|s| s.peer.pid == peer.pid && s.peer.born == peer.born) { startup.confirmed.store(true,Ordering::SeqCst); } }
}
#[repr(C)] struct StartupInfo {
    size: u32, reserved: *mut u16, desktop: *mut u16, title: *mut u16,
    x: u32, y: u32, xsize: u32, ysize: u32, columns: u32, rows: u32, fill: u32, flags: u32,
    show: u16, reserved_size: u16, reserved_bytes: *mut u8, stdin: Handle, stdout: Handle, stderr: Handle,
}
#[repr(C)] struct ProcessInfo { process: Handle, thread: Handle, pid: u32, tid: u32 }
struct Secret(Vec<u16>);
impl Drop for Secret { fn drop(&mut self) { for unit in &mut self.0 { unsafe { ptr::write_volatile(unit,0); } } std::sync::atomic::compiler_fence(Ordering::SeqCst); } }
pub(crate) struct SecretCredential(String);
impl SecretCredential { pub(crate) fn new(value:String)->Self { Self(value) } pub(crate) fn as_str(&self)->&str { &self.0 } }
impl Drop for SecretCredential { fn drop(&mut self) { let mut bytes = std::mem::take(&mut self.0).into_bytes(); for byte in &mut bytes { unsafe { ptr::write_volatile(byte,0); } } std::sync::atomic::compiler_fence(Ordering::SeqCst); } }
struct Suspended { process: Arc<Owned>, thread: Option<Owned>, resumed: bool }
impl Drop for Suspended { fn drop(&mut self) { if !self.resumed { unsafe {
    if TerminateProcess(self.process.raw(),1) == 0 && WaitForSingleObject(self.process.raw(),0) != OBJECT { if let Some(thread) = &self.thread { ResumeThread(thread.raw()); } }
    WaitForSingleObject(self.process.raw(),5_000);
} } } }
pub(crate) fn launch_with_logon(user: &str, password: &str, exe: &str, arg: &str) -> Option<()> {
    if arg != "--portable-service" || user.is_empty() || password.is_empty() || [user,password,exe,arg].iter().any(|s| s.contains('\0')) { return None; }
    let mut startup = started().lock().ok()?;
    if startup.as_ref().map(|s| s.peer.alive()).unwrap_or(false) { return None; }
    let (domain,user) = user.rsplit_once('\\').map(|(d,u)| (Some(d),u)).unwrap_or_else(|| (if user.contains('@') { None } else { Some(".") },user));
    let domain = domain.map(wide); let domain = domain.as_ref().map(|v|v.as_ptr()).unwrap_or(ptr::null());
    let user = wide(user); let password = Secret(wide(password)); let mut token = ptr::null_mut();
    if unsafe { LogonUserW(user.as_ptr(),domain,password.0.as_ptr(),2,0,&mut token) } == 0 { return None; }
    let token = Owned::from(token)?; let sid = token_sid(token.raw())?;
    if !quiesce_helper_transition() { return None; }
    let mut command = wide(&format!("\"{}\" {}",exe,arg)); let exe = wide(exe);
    let mut info: StartupInfo = unsafe { std::mem::zeroed() }; info.size = std::mem::size_of::<StartupInfo>() as u32; info.flags = 1;
    let mut process: ProcessInfo = unsafe { std::mem::zeroed() };
    if unsafe { CreateProcessWithLogonW(user.as_ptr(),domain,password.0.as_ptr(),1,exe.as_ptr(),command.as_mut_ptr(),0x400 | 4,ptr::null_mut(),ptr::null(),&mut info,&mut process) } == 0 { return None; }
    let thread = Owned::from(process.thread);
    let mut suspended = Suspended { process: Arc::new(Owned::from(process.process)?), thread, resumed: false };
    if suspended.thread.is_none() { return None; }
    let peer = verified_peer(process.pid,suspended.process.clone(),sid)?;
    let mut publisher = ptr::null_mut();
    // Duplicate directly into this exact still-suspended owned child; minimal
    // read/wait rights, never inherited and never a process ACL/privilege change.
    if unsafe { DuplicateHandle(GetCurrentProcess(),GetCurrentProcess(),suspended.process.raw(),&mut publisher,SYNC | 0x1000,0,0) } == 0 { return None; }
    let confirmed = Arc::new(AtomicBool::new(false));
    let watch = confirmed.clone(); let owned = suspended.process.clone();
    std::thread::Builder::new().name("mixel-helper-startup".to_owned()).spawn(move || {
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while !watch.load(Ordering::SeqCst) && std::time::Instant::now() < deadline && unsafe { WaitForSingleObject(owned.raw(),0) } == TIMEOUT { std::thread::sleep(Duration::from_millis(10)); }
        if !watch.load(Ordering::SeqCst) && unsafe { WaitForSingleObject(owned.raw(),0) } == TIMEOUT { unsafe { TerminateProcess(owned.raw(),1); } }
    }).ok()?;
    *startup = Some(Started { peer, publisher: Some(publisher as usize as u64), confirmed });
    if unsafe { ResumeThread(suspended.thread.as_ref()?.raw()) } == u32::MAX { startup.take(); return None; }
    suspended.resumed = true; Some(())
}
#[derive(Clone)]
pub(super) enum Backend { Local, Portable(String) }
impl Backend {
    pub(super) fn current(lease: &Lease) -> Option<Self> {
        if super::super::portable_service::client::running() { Some(Self::Portable(lease.prepare_helper()?)) } else { Some(Self::Local) }
    }
    pub(super) fn matches_current(&self) -> bool {
        match self {
            Self::Local => !super::super::portable_service::client::running(),
            Self::Portable(target) => super::super::portable_service::client::running() && helper().lock().ok().and_then(|p| p.clone()).filter(Peer::alive).map(|p| format!("{}-{}",p.pid,p.born)) == Some(target.clone()),
        }
    }
}
#[derive(Clone)]
struct Remote { lease: Arc<Lease>, effect_id: i32 }
fn remotes() -> &'static Mutex<HashMap<String,Remote>> {
    static REMOTES: OnceLock<Mutex<HashMap<String,Remote>>> = OnceLock::new();
    REMOTES.get_or_init(|| Mutex::new(HashMap::new()))
}
fn retire_remote(remote: Remote) -> bool {
    native_effect(true, Box::new(move || {
        if remote.lease.active() { return false; }
        let Some(_serial) = remote.lease.lock() else { return false; };
        cleanup(&remote.lease.token) && remote.lease.mark_cleaned()
    }))
}
fn sweep() {
    let candidates: Vec<_> = remotes().lock().unwrap_or_else(|e| e.into_inner()).iter().filter(|(_,r)| !r.lease.active()).map(|(token,r)| (token.clone(),r.clone())).collect();
    for (token,remote) in candidates {
        let remove = retire_remote(remote.clone()) && !(remote.lease.poisoned.load(Ordering::SeqCst) && remote.lease.authority_live());
        if remove { remotes().lock().unwrap_or_else(|e| e.into_inner()).remove(&token); }
    }
}
fn start_sweeper() -> bool {
    static STARTED: OnceLock<bool> = OnceLock::new();
    *STARTED.get_or_init(|| std::thread::Builder::new().name("mixel-portable-input-cleanup".to_owned()).spawn(|| loop {
        std::thread::sleep(Duration::from_millis(10)); sweep();
    }).is_ok())
}
pub(crate) fn receive(packet: (String,String,i32,u8,Vec<u8>,String,u32,bool,bool), peer: &Peer) -> bool {
    let (token,target,conn,kind,bytes,name,argb,a,b) = packet;
    let current = unsafe { GetCurrentProcessId() };
    if !peer.alive() || Some(target) != birth(unsafe { GetCurrentProcess() }).map(|born| format!("{current}-{born}")) { return false; }
    if names(&token).map(|(_,_,_,pid,born)| (pid,born)) != Some((peer.pid,peer.born)) { return false; }
    if bytes.len() + name.len() > MAX_BYTES { return false; }
    let action = match kind {
        0 => MouseEvent::parse_from_bytes(&bytes).ok().map(|v| Action::Mouse(v,name,argb,a,b)),
        1 if name.is_empty() && argb == 0 && !b => KeyEvent::parse_from_bytes(&bytes).ok().map(|v| Action::Key(v,a)),
        2 if name.is_empty() && argb == 0 && !a && !b => PointerDeviceEvent::parse_from_bytes(&bytes).ok().map(Action::Pointer),
        _ => None,
    };
    let Some(action) = action else { return false; };
    if !start_sweeper() { return false; }
    sweep();
    let mut entries = remotes().lock().unwrap_or_else(|e| e.into_inner());
    if !entries.contains_key(&token) {
        if entries.len() >= 256 { return false; }
        let Some(lease) = Lease::open(&token,peer) else { return false; };
        let Ok(effect_id) = NEXT_EFFECT.fetch_update(Ordering::SeqCst,Ordering::SeqCst,|v| v.checked_sub(1)) else { return false; };
        entries.insert(token.clone(), Remote { lease: Arc::new(lease), effect_id });
    }
    let remote = entries.get(&token).unwrap().clone(); drop(entries);
    native_effect(false, Box::new(move || {
        let Some(_serial) = remote.lease.effect() else {
            if !remote.lease.active() { if let Some(_retire) = remote.lease.lock() { if cleanup(&token) { remote.lease.mark_cleaned(); } } }
            return false;
        };
        let lease = remote.lease.clone(); let deferred = remote.clone();
        let runner: Deferred = Arc::new(move |effect| {
            let deferred = deferred.clone();
            native_enqueue(false, Box::new(move || {
                let Some(_serial) = deferred.lease.effect() else { return false; };
                effect(); true
            }))
        });
        apply(&token,remote.effect_id,conn,&action,Arc::new(move || lease.active()),runner);
        true
    }))
}
pub(crate) fn shutdown() {
    // A stream dying invalidates every actual owner before owned cleanup is
    // scheduled. Do not remove responsibility when native cleanup fails.
    let entries: Vec<_> = remotes().lock().unwrap_or_else(|e| e.into_inner()).values().cloned().collect();
    for remote in entries { remote.lease.poisoned.store(true,Ordering::SeqCst); let _ = retire_remote(remote); }
    sweep();
}

// BlockInput, SendInput and every owned release share one actual OS thread.
// The queues carry closures, never acquired mutex guards. Control has priority,
// with a finite burst so an unrelated control flood cannot starve data forever.
type NativeTask = Box<dyn FnOnce() -> bool + Send>;
struct NativeState { control: std::collections::VecDeque<NativeTask>, data: std::collections::VecDeque<NativeTask> }
struct Native { state: Mutex<NativeState>, changed: std::sync::Condvar }
thread_local! { static NATIVE_THREAD: std::cell::Cell<bool> = const { std::cell::Cell::new(false) }; }
fn native() -> Option<&'static Arc<Native>> {
    static NATIVE: OnceLock<Option<Arc<Native>>> = OnceLock::new();
    NATIVE.get_or_init(|| {
        let shared = Arc::new(Native { state: Mutex::new(NativeState { control: std::collections::VecDeque::new(), data: std::collections::VecDeque::new() }), changed: std::sync::Condvar::new() });
        let worker = shared.clone();
        std::thread::Builder::new().name("mixel-native-input".to_owned()).spawn(move || {
            NATIVE_THREAD.with(|v| v.set(true)); let mut control_burst = 0;
            loop {
                let mut state = worker.state.lock().unwrap_or_else(|e| e.into_inner());
                while state.control.is_empty() && state.data.is_empty() { state = worker.changed.wait(state).unwrap_or_else(|e| e.into_inner()); }
                let task = if !state.control.is_empty() && (control_burst < 8 || state.data.is_empty()) { control_burst += 1; state.control.pop_front() } else { control_burst = 0; state.data.pop_front() };
                drop(state); if let Some(task) = task { task(); }
            }
        }).ok().map(|_| shared)
    }).as_ref()
}
pub(super) fn native_enqueue(control: bool, task: NativeTask) -> bool {
    let Some(shared) = native() else { return false; };
    let mut state = shared.state.lock().unwrap_or_else(|e| e.into_inner());
    let queue = if control { &mut state.control } else { &mut state.data };
    if queue.len() >= MAX_PENDING { return false; }
    queue.push_back(task); shared.changed.notify_one(); true
}
pub(super) fn native_effect(control: bool, task: NativeTask) -> bool {
    if NATIVE_THREAD.with(std::cell::Cell::get) { return task(); }
    let (tx,rx) = mpsc::sync_channel(1);
    if !native_enqueue(control,Box::new(move || { let result = task(); let _ = tx.send(result); result })) { return false; }
    rx.recv_timeout(Duration::from_secs(5)).unwrap_or(false)
}
