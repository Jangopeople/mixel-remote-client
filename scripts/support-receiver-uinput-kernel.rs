//! Shared kernel capability bodies; compiled by the Linux native fixture too.
use super::{WireLease, PAGE, WORD, SEALS};
const BIND_READY:usize=80;
fn binding_offset(kind:u64)->Option<usize> {match kind {1=>Some(128),2=>Some(192),_=>None}}
use std::{fs::{File,OpenOptions},io::Write,os::unix::{fs::{MetadataExt,OpenOptionsExt,PermissionsExt},io::{AsRawFd,FromRawFd,RawFd}},path::PathBuf,sync::{Mutex,atomic::{AtomicBool,AtomicU64,Ordering}}};
#[derive(Clone, Debug, PartialEq, Eq)]
struct Identity { pid: i32, uid: u32, birth: u64, session: i32, executable: (u64,u64) }
fn identity(pid: i32, uid: u32, require_executable: bool) -> Option<Identity> {
    if pid <= 0 { return None; }
    let directory = PathBuf::from(format!("/proc/{}",pid));
    if std::fs::metadata(&directory).ok()?.uid() != uid { return None; }
    let stat = std::fs::read_to_string(directory.join("stat")).ok()?;
    let tail: Vec<_> = stat.get(stat.rfind(") ")?+2..)?.split_whitespace().collect();
    let birth = tail.get(19)?.parse().ok()?;
    let session = tail.get(3)?.parse().ok()?;
    let executable = if require_executable {
        let target = std::fs::metadata(directory.join("exe")).ok()?;
        let own = std::fs::metadata(std::env::current_exe().ok()?).ok()?;
        if (target.dev(),target.ino()) != (own.dev(),own.ino()) { return None; }
        (target.dev(),target.ino())
    } else { (0,0) };
    Some(Identity { pid, uid, birth, session, executable })
}
/// Retained pidfd prevents a recycled numerical PID from becoming this publisher.
pub(crate) struct Peer { identity: Identity, process: File, require_executable: bool }
impl Peer {
    pub(crate) fn capture(fd: RawFd, service: bool) -> Option<Self> {
        let mut credentials: libc::ucred = unsafe { std::mem::zeroed() };
        let mut length = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
        if unsafe { libc::getsockopt(fd,libc::SOL_SOCKET,libc::SO_PEERCRED,(&mut credentials as *mut libc::ucred).cast(),&mut length) } != 0
            || length as usize != std::mem::size_of::<libc::ucred>() { return None; }
        // The service authenticates the actual same-executable publisher. A user
        // client trusts a root service; /proc/root/exe may be intentionally hidden.
        let require_executable = service || credentials.uid != 0;
        if !service && credentials.uid != 0 && credentials.uid != unsafe { libc::geteuid() } { return None; }
        let before = identity(credentials.pid,credentials.uid,require_executable)?;
        let descriptor = unsafe { libc::syscall(libc::SYS_pidfd_open,credentials.pid,0) };
        if descriptor < 0 { return None; } // no PID-only fallback on older kernels
        let process = unsafe { File::from_raw_fd(descriptor as RawFd) };
        if identity(credentials.pid,credentials.uid,require_executable)? != before { return None; }
        let peer = Self { identity: before, process, require_executable };
        peer.alive().then_some(peer)
    }
    pub(crate) fn alive(&self) -> bool {
        let mut descriptor = libc::pollfd { fd: self.process.as_raw_fd(), events: libc::POLLIN, revents: 0 };
        (unsafe { libc::poll(&mut descriptor,1,0) == 0 })
            && identity(self.identity.pid,self.identity.uid,self.require_executable).as_ref() == Some(&self.identity)
    }
}
pub(super) struct Mapping { pointer: *mut u8, pub file: File, writable: bool }
// Only immutable header bytes and aligned AtomicU64 words are shared. Mapping
// size is sealed; the File outlives every atomic load and mapping reference.
unsafe impl Send for Mapping {}
unsafe impl Sync for Mapping {}
impl Drop for Mapping { fn drop(&mut self) { unsafe { libc::munmap(self.pointer.cast(),PAGE); } } }
impl Mapping {
    fn map(file: File, writable: bool) -> Option<Self> {
        let protection = libc::PROT_READ | if writable { libc::PROT_WRITE } else { 0 };
        let pointer = unsafe { libc::mmap(std::ptr::null_mut(),PAGE,protection,libc::MAP_SHARED,file.as_raw_fd(),0) };
        if pointer == libc::MAP_FAILED { return None; }
        Some(Self { pointer: pointer.cast(), file, writable })
    }
    fn into_file(self)->File {
        let mapping=std::mem::ManuallyDrop::new(self);
        unsafe {libc::munmap(mapping.pointer.cast(),PAGE);std::ptr::read(&mapping.file)}
    }
    fn word(&self, offset: usize) -> &AtomicU64 {
        assert!(matches!(offset,WORD|BIND_READY));
        unsafe { &*self.pointer.add(offset).cast::<AtomicU64>() }
    }
    pub(super) fn load(&self, offset: usize) -> u64 { self.word(offset).load(Ordering::SeqCst) }
    pub(super) fn set(&self, offset: usize, bits: u64) { assert!(self.writable); self.word(offset).fetch_or(bits,Ordering::SeqCst); }
    fn binding_matches(&self,kind:u64,instance:&str)->bool {
        let Some(offset)=binding_offset(kind) else {return false;};
        self.load(BIND_READY)&kind!=0 && instance.len()==32
            && unsafe {std::slice::from_raw_parts(self.pointer.add(offset),32)}==instance.as_bytes()
    }
    fn bind(&self,kind:u64,instance:&str)->bool {
        let Some(offset)=binding_offset(kind) else {return false;};
        if self.load(BIND_READY)&kind!=0 {return self.binding_matches(kind,instance);}
        if !self.writable || self.load(WORD)!=0 || !nonce_valid(instance) {return false;}
        // Revocation's publisher mutex serializes writers. The ready bit
        // publishes an immutable slot; readers never inspect a partial string.
        unsafe {std::ptr::copy_nonoverlapping(instance.as_ptr(),self.pointer.add(offset),32);}
        self.set(BIND_READY,kind);true
    }
    fn nonce_matches(&self, nonce: &str) -> bool {
        nonce.len() == 32 && unsafe { std::slice::from_raw_parts(self.pointer,32) } == nonce.as_bytes()
    }
    fn identity(&self) -> Option<(u64,u64)> { let stat=self.file.metadata().ok()?;Some((stat.dev(),stat.ino())) }
}
fn nonce_valid(nonce: &str) -> bool { nonce.len()==32 && nonce.bytes().all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase()) }
fn create_mapping(nonce: &str, purpose: &str, writable: bool) -> Option<Mapping> {
    let name = std::ffi::CString::new(format!("mixel-input-{}-{}",purpose,nonce)).ok()?;
    let descriptor = unsafe { libc::memfd_create(name.as_ptr(),libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING) };
    if descriptor < 0 { return None; }
    let mut file = unsafe { File::from_raw_fd(descriptor) };
    file.set_permissions(std::fs::Permissions::from_mode(0o600)).ok()?;
    file.set_len(PAGE as u64).ok()?;
    file.write_all(nonce.as_bytes()).ok()?;
    if unsafe { libc::fcntl(file.as_raw_fd(),libc::F_ADD_SEALS,if writable { SEALS & !libc::F_SEAL_SEAL } else { SEALS }) } != 0 { return None; }
    if !writable {
        // Publisher retains/maps only an O_RDONLY description of the ack page.
        file = OpenOptions::new().read(true).custom_flags(libc::O_CLOEXEC).open(format!("/proc/self/fd/{}",file.as_raw_fd())).ok()?;
    }
    Mapping::map(file,writable)
}
fn open_mapping(peer: &Peer, descriptor: i32, nonce: &str, writable: bool) -> Option<Mapping> {
    if descriptor < 0 || descriptor > 1_048_576 || !nonce_valid(nonce) || !peer.alive() { return None; }
    let path = format!("/proc/{}/fd/{}",peer.identity.pid,descriptor);
    // /proc/<authenticated PID>/fd is the kernel descriptor transfer mechanism;
    // O_NOFOLLOW would reject its required descriptor link. Never use a payload PID.
    let file = OpenOptions::new().read(true).write(writable).custom_flags(libc::O_CLOEXEC).open(path).ok()?;
    if !peer.alive() { return None; }
    let stat = file.metadata().ok()?;
    if !stat.is_file() || stat.len()!=PAGE as u64 || stat.uid()!=peer.identity.uid || stat.nlink()!=0
        || stat.mode() & 0o777 != 0o600 { return None; }
    let seals=unsafe {libc::fcntl(file.as_raw_fd(),libc::F_GET_SEALS)};
    if (writable && seals!=SEALS) || (!writable && seals!=(SEALS & !libc::F_SEAL_SEAL) && seals!=(SEALS | libc::F_SEAL_WRITE)) {return None;}
    let mapping = Mapping::map(file,writable)?;
    if !mapping.nonce_matches(nonce) || !peer.alive() { return None; }
    Some(mapping)
}
/// The deny word is published first, then permanently sealed against future
/// writes. All publisher accesses serialize remapping, so no raw atomic reference
/// survives unmap. The service retains its independent read-only mapping.
pub(super) struct Revocation { mapping:Mutex<Option<Mapping>> }
impl Revocation {
    fn new(mapping:Mapping)->Self {Self {mapping:Mutex::new(Some(mapping))}}
    pub(super) fn load(&self,_offset:usize)->u64 {
        self.mapping.lock().unwrap_or_else(|error|error.into_inner()).as_ref().map(|value|value.load(WORD)).unwrap_or(1)
    }
    pub(super) fn bind(&self,kind:u64,instance:&str)->bool {
        self.mapping.lock().unwrap_or_else(|error|error.into_inner()).as_ref().map(|mapping|mapping.bind(kind,instance)).unwrap_or(false)
    }
    pub(super) fn revoke(&self)->bool {
        let mut current=self.mapping.lock().unwrap_or_else(|error|error.into_inner());
        let Some(mapping)=current.as_ref() else {return false;};
        if !mapping.writable {
            let sealed=unsafe {libc::fcntl(mapping.file.as_raw_fd(),libc::F_ADD_SEALS,libc::F_SEAL_WRITE|libc::F_SEAL_SEAL)}==0;
            return sealed || unsafe {libc::fcntl(mapping.file.as_raw_fd(),libc::F_GET_SEALS)}==(SEALS|libc::F_SEAL_WRITE);
        }
        mapping.set(WORD,1); // monotonic deny is visible before any remap/seal work
        let file=current.take().unwrap().into_file();
        // No publisher writable mapping remains. Read-only consumer mappings do
        // not prevent F_SEAL_WRITE, so an old queued Bind can never regrant.
        let sealed=unsafe {libc::fcntl(file.as_raw_fd(),libc::F_ADD_SEALS,libc::F_SEAL_WRITE|libc::F_SEAL_SEAL)}==0;
        *current=Mapping::map(file,false);
        sealed && current.is_some()
    }

}
pub(super) struct Publisher { pub wire:WireLease, pub revoke:Revocation, pub ack:Mapping, pub failed:AtomicBool, used:AtomicU64 }
impl Publisher {
    pub(super) fn create(nonce:&str)->Option<Self> {
        if !nonce_valid(nonce) {return None;}
        let nonce=nonce.to_owned();let revoke=create_mapping(&nonce,"revoke",true)?;let ack=create_mapping(&nonce,"ack",false)?;
        Some(Self {wire:WireLease {nonce,revoke_fd:revoke.file.as_raw_fd(),ack_fd:ack.file.as_raw_fd()},revoke:Revocation::new(revoke),ack,failed:AtomicBool::new(false),used:AtomicU64::new(0)})
    }
    pub(super) fn bind(&self,kind:u64,instance:&str)->bool {
        if self.revoke.bind(kind,instance) {true}else{self.fail();false}
    }
    pub(super) fn denied(&self)->bool {self.revoke.load(WORD)!=0}
    pub(super) fn mark_used(&self,kind:u64) {self.used.fetch_or(kind,Ordering::SeqCst);}
    pub(super) fn fail(&self) {self.failed.store(true,Ordering::SeqCst);let _=self.revoke.revoke();}
    pub(super) fn retired(&self)->bool {self.denied() && self.ack.load(WORD)&self.used.load(Ordering::SeqCst)==self.used.load(Ordering::SeqCst)}
}
pub(super) struct Consumer { pub wire: WireLease, revoke: Mapping, pub ack: Mapping, pub denied: AtomicBool }
impl Consumer {
    pub(super) fn open(peer:&Peer,wire:&WireLease)->Option<Self> {
        if wire.revoke_fd==wire.ack_fd { return None; }
        let revoke=open_mapping(peer,wire.revoke_fd,&wire.nonce,false)?;
        let ack=open_mapping(peer,wire.ack_fd,&wire.nonce,true)?;
        if revoke.identity()==ack.identity() { return None; }
        Some(Self {wire:wire.clone(),revoke,ack,denied:AtomicBool::new(false)})
    }
    fn publisher_descriptors_match(&self,peer:&Peer)->bool {
        [(self.wire.revoke_fd,&self.revoke),(self.wire.ack_fd,&self.ack)].iter().all(|(descriptor,mapping)| {
            std::fs::metadata(format!("/proc/{}/fd/{}",peer.identity.pid,descriptor)).ok()
                .map(|stat|Some((stat.dev(),stat.ino()))==mapping.identity()).unwrap_or(false)
        })
    }
    pub(super) fn allowed(&self,peer:&Peer)->bool {
        if self.denied.load(Ordering::SeqCst) { return false; }
        if self.revoke.load(WORD)!=0 || !peer.alive() || !self.publisher_descriptors_match(peer) { self.denied.store(true,Ordering::SeqCst);return false; }
        true
    }
    pub(super) fn bound_to(&self,kind:u64,instance:&str)->bool {self.revoke.binding_matches(kind,instance)}
    pub(super) fn permanently_revoked(&self)->bool {
        self.revoke.load(WORD)!=0 && (unsafe {libc::fcntl(self.revoke.file.as_raw_fd(),libc::F_GET_SEALS)}) == (SEALS|libc::F_SEAL_WRITE)
    }
    pub(super) fn deny(&self) { self.denied.store(true,Ordering::SeqCst); }
}
