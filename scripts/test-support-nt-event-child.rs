// Test-only read-only child probe for the identity-bound existing World event.
use std::ffi::c_void;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::Path;

type Handle = *mut c_void;
const SYNCHRONIZE: u32 = 0x0010_0000;
const ARMED: &str = "fixture-nt-event-armed";
const REQUEST: &str = "fixture-nt-event.request";
const RESULT: &str = "fixture-nt-event-result.json";
const TEMP: &str = "fixture-nt-event-result.new";
const MAGIC: &[u8; 8] = b"MXLNTE01";

#[repr(C)]
pub struct UnicodeString { pub length: u16, pub maximum_length: u16, pub buffer: *const u16 }
#[repr(C)]
pub struct ObjectAttributes {
    pub length: u32, pub root_directory: Handle, pub object_name: *const UnicodeString,
    pub attributes: u32, pub security_descriptor: Handle, pub security_quality_of_service: Handle,
}
pub type NtOpenEvent = unsafe extern "system" fn(*mut Handle, u32, *const ObjectAttributes) -> i32;

pub trait Api {
    fn pid(&mut self) -> u32;
    fn system_directory(&mut self, buffer: &mut [u16]) -> (u32, u32);
    fn load(&mut self, path: &[u16]) -> (Handle, u32);
    fn resolve(&mut self, module: Handle) -> (Handle, u32);
    unsafe fn open(&mut self, entry: Handle, output: *mut Handle, access: u32, attributes: *const ObjectAttributes) -> i32;
    fn close(&mut self, handle: Handle) -> (bool, u32);
    fn free(&mut self, module: Handle) -> (bool, u32);
}

#[cfg(windows)]
pub struct Native;
#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn GetCurrentProcessId() -> u32;
    fn GetSystemDirectoryW(buffer: *mut u16, size: u32) -> u32;
    fn LoadLibraryExW(path: *const u16, file: Handle, flags: u32) -> Handle;
    fn GetProcAddress(module: Handle, name: *const u8) -> Handle;
    fn GetLastError() -> u32;
    fn CloseHandle(handle: Handle) -> i32;
    fn FreeLibrary(module: Handle) -> i32;
    fn MoveFileW(existing: *const u16, destination: *const u16) -> i32;
}
#[cfg(windows)]
impl Api for Native {
    fn pid(&mut self) -> u32 { unsafe { GetCurrentProcessId() } }
    fn system_directory(&mut self, buffer: &mut [u16]) -> (u32, u32) {
        let size = unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) };
        let error = if size == 0 { unsafe { GetLastError() } } else { 0 }; (size, error)
    }
    fn load(&mut self, path: &[u16]) -> (Handle, u32) {
        let module = unsafe { LoadLibraryExW(path.as_ptr(), std::ptr::null_mut(), 0x800) };
        let error = if module.is_null() { unsafe { GetLastError() } } else { 0 }; (module, error)
    }
    fn resolve(&mut self, module: Handle) -> (Handle, u32) {
        let entry = unsafe { GetProcAddress(module, b"NtOpenEvent\0".as_ptr()) };
        let error = if entry.is_null() { unsafe { GetLastError() } } else { 0 }; (entry, error)
    }
    unsafe fn open(&mut self, entry: Handle, output: *mut Handle, access: u32, attributes: *const ObjectAttributes) -> i32 {
        let function: NtOpenEvent = std::mem::transmute(entry);
        function(output, access, attributes)
    }
    fn close(&mut self, handle: Handle) -> (bool, u32) {
        let success = unsafe { CloseHandle(handle) } != 0;
        let error = if success { 0 } else { unsafe { GetLastError() } }; (success, error)
    }
    fn free(&mut self, module: Handle) -> (bool, u32) {
        let success = unsafe { FreeLibrary(module) } != 0;
        let error = if success { 0 } else { unsafe { GetLastError() } }; (success, error)
    }
}

fn valid(handle: Handle) -> bool { !handle.is_null() && handle as isize != -1 }
fn hex(bytes: &[u8]) -> String { bytes.iter().map(|value| format!("{value:02x}")).collect() }

// Pure SHA-256; the equality hash is not an authorization credential.
pub fn digest(input: &[u8]) -> String {
    const K: [u32; 64] = [
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
    let mut h: [u32; 8] = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    let mut padded=input.to_vec(); padded.push(0x80);
    while padded.len()%64 != 56 { padded.push(0); }
    padded.extend_from_slice(&((input.len() as u64)*8).to_be_bytes());
    for block in padded.chunks_exact(64) {
        let mut w=[0u32;64];
        for i in 0..16 { w[i]=u32::from_be_bytes(block[i*4..i*4+4].try_into().unwrap()); }
        for i in 16..64 {
            let a=w[i-15].rotate_right(7)^w[i-15].rotate_right(18)^(w[i-15]>>3);
            let b=w[i-2].rotate_right(17)^w[i-2].rotate_right(19)^(w[i-2]>>10);
            w[i]=w[i-16].wrapping_add(a).wrapping_add(w[i-7]).wrapping_add(b);
        }
        let [mut a,mut b,mut c,mut d,mut e,mut f,mut g,mut z]=h;
        for i in 0..64 {
            let s=e.rotate_right(6)^e.rotate_right(11)^e.rotate_right(25);
            let t=z.wrapping_add(s).wrapping_add((e&f)^(!e&g)).wrapping_add(K[i]).wrapping_add(w[i]);
            let q=(a.rotate_right(2)^a.rotate_right(13)^a.rotate_right(22)).wrapping_add((a&b)^(a&c)^(b&c));
            z=g;g=f;f=e;e=d.wrapping_add(t);d=c;c=b;b=a;a=t.wrapping_add(q);
        }
        for (old,value) in h.iter_mut().zip([a,b,c,d,e,f,g,z]) { *old=old.wrapping_add(value); }
    }
    hex(&h.iter().flat_map(|value|value.to_be_bytes()).collect::<Vec<_>>())
}

pub struct Request { pub nonce: String, pub target_hash: String, pub candidate: String }
fn lower_hex(value: &[u8]) -> bool { value.iter().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(b)) }
pub fn parse_request(bytes: &[u8], event: &str, expected_pid: u32) -> Result<Request, &'static str> {
    if bytes.len()<112 || bytes.len()>512 || &bytes[..8]!=MAGIC { return Err("request envelope"); }
    if u32::from_le_bytes(bytes[8..12].try_into().unwrap())!=expected_pid || expected_pid==0 || !lower_hex(&bytes[12..44]) || !lower_hex(&bytes[44..108]) { return Err("request tuple"); }
    let size=u32::from_le_bytes(bytes[108..112].try_into().unwrap()) as usize;
    if size==0 || size%2!=0 || bytes.len()!=112+size { return Err("request size"); }
    if !event.starts_with("Global\\Mixel-Remote-Standard-Lease-") || event.len()!=67 || !lower_hex(&event.as_bytes()[35..]) {
        return Err("compiled Global GUID");
    }
    let units:Vec<u16>=bytes[112..].chunks_exact(2).map(|pair|u16::from_le_bytes(pair.try_into().unwrap())).collect();
    let candidate=String::from_utf16(&units).map_err(|_|"request UTF16")?;
    let expected=format!("\\BaseNamedObjects\\{}", &event[7..]);
    if candidate!=expected { return Err("one explicit candidate"); }
    let target_hash=String::from_utf8(bytes[44..108].to_vec()).unwrap();
    if digest(&bytes[112..])!=target_hash { return Err("target hash"); }
    Ok(Request{nonce:String::from_utf8(bytes[12..44].to_vec()).unwrap(),target_hash,candidate})
}

pub struct Facts {
    pub pid: u32, pub system_status: &'static str, pub system_error: u32,
    pub load_status: &'static str, pub load_error: u32, pub resolve_status: &'static str, pub resolve_error: u32,
    pub open_status: &'static str, pub nt_status: Option<i32>, pub close_status: &'static str, pub close_error: u32,
    pub release_status: &'static str, pub release_error: u32, pub cleanup_okay: bool,
}
impl Facts {
    pub fn json(&self, request: &Request) -> String {
        format!(concat!("{{\"schema\":\"mixel-nt-event-child-v1\",\"phase\":\"ChildSelf\",\"pid\":{},",
            "\"nonce\":\"{}\",\"targetHash\":\"{}\",\"requestedAccess\":1048576,",
            "\"systemStatus\":\"{}\",\"systemError\":{},\"loadStatus\":\"{}\",\"loadError\":{},",
            "\"resolveStatus\":\"{}\",\"resolveError\":{},\"openStatus\":\"{}\",\"ntStatus\":{},",
            "\"closeStatus\":\"{}\",\"closeError\":{},\"releaseStatus\":\"{}\",\"releaseError\":{},\"cleanupOkay\":{}}}\n"),
            self.pid,request.nonce,request.target_hash,self.system_status,self.system_error,self.load_status,self.load_error,
            self.resolve_status,self.resolve_error,self.open_status,self.nt_status.map_or("null".into(),|value|value.to_string()),
            self.close_status,self.close_error,self.release_status,self.release_error,self.cleanup_okay)
    }
}

pub fn observe(api: &mut impl Api, request: &Request) -> Facts {
    assert_eq!(std::mem::size_of::<usize>(),8,"x64 only");
    assert_eq!(std::mem::size_of::<UnicodeString>(),16);
    assert_eq!(std::mem::size_of::<ObjectAttributes>(),48);
    let mut facts=Facts{pid:api.pid(),system_status:"not-attempted",system_error:0,load_status:"not-attempted",load_error:0,
        resolve_status:"not-attempted",resolve_error:0,open_status:"not-attempted",nt_status:None,
        close_status:"not-owned",close_error:0,release_status:"not-acquired",release_error:0,cleanup_okay:true};
    assert_ne!(facts.pid,0);
    let mut directory=vec![0u16;32768];
    let (size,error)=api.system_directory(&mut directory);facts.system_error=error;
    if size==0 { facts.system_status="error";return facts; }
    let size=size as usize;
    if size>=directory.len() || directory[size]!=0 || directory[..size].contains(&0) {
        facts.system_status="invalid-output";return facts;
    }
    let text=match String::from_utf16(&directory[..size]) { Ok(text)=>text,Err(_)=>{facts.system_status="invalid-output";return facts;} };
    // A genuine Windows absolute drive path, not a caller search path or a relative DLL.
    if text.len()<3 || !text.as_bytes()[0].is_ascii_alphabetic() || &text.as_bytes()[1..3]!=b":\\" || text.contains('/') {
        facts.system_status="invalid-output";return facts;
    }
    facts.system_status="available";
    let path:Vec<u16>=format!("{}\\ntdll.dll",text.trim_end_matches('\\')).encode_utf16().chain(Some(0)).collect();
    let (module,error)=api.load(&path);facts.load_error=error;
    if module.is_null() { facts.load_status="error";return facts; }
    if !valid(module) { facts.load_status="invalid-output";return facts; }
    facts.load_status="acquired";
    let (entry,error)=api.resolve(module);facts.resolve_error=error;
    if entry.is_null() { facts.resolve_status="error"; }
    else if !valid(entry) { facts.resolve_status="invalid-output"; }
    else {
        facts.resolve_status="available";
        let target:Vec<u16>=request.candidate.encode_utf16().chain(Some(0)).collect();
        let bytes=(target.len()-1)*2;
        let name=UnicodeString{length:bytes as u16,maximum_length:(bytes+2) as u16,buffer:target.as_ptr()};
        let attributes=ObjectAttributes{length:48,root_directory:std::ptr::null_mut(),object_name:&name,attributes:0x40,
            security_descriptor:std::ptr::null_mut(),security_quality_of_service:std::ptr::null_mut()};
        let mut handle=std::ptr::null_mut();
        let status=unsafe { api.open(entry,&mut handle,SYNCHRONIZE,&attributes) };
        // Preserve the signed direct result before cleanup. Failure poison is never owned.
        facts.nt_status=Some(status);
        if status<0 { facts.open_status="nt-status"; }
        else if !valid(handle) { facts.open_status="invalid-output"; }
        else {
            facts.open_status="opened";
            let (success,error)=api.close(handle);facts.close_error=error;
            facts.close_status=if success { "closed" } else { "failed" };
            if !success { facts.cleanup_okay=false; }
        }
    }
    let (success,error)=api.free(module);facts.release_error=error;
    facts.release_status=if success { "released" } else { "failed" };
    if !success { facts.cleanup_okay=false; }
    facts
}

pub fn run(directory: &Path, event: &str, api: &mut impl Api) -> Result<bool, String> {
    run_bounded(directory,event,api,std::time::Duration::from_secs(10))
}
pub fn run_bounded(directory: &Path, event: &str, api: &mut impl Api, timeout: std::time::Duration) -> Result<bool, String> {
    let arm=directory.join(ARMED);
    match fs::symlink_metadata(&arm) {
        Err(error) if error.kind()==std::io::ErrorKind::NotFound=>return Ok(false),
        Err(error)=>return Err(error.to_string()),
        Ok(metadata)=>if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len()!=8 { return Err("invalid armed request".into()); },
    }
    if fs::read(&arm).map_err(|error|error.to_string())? != b"observe\n" { return Err("invalid armed request".into()); }
    let path=directory.join(REQUEST);
    let deadline=std::time::Instant::now()+timeout;
    loop {
        match fs::symlink_metadata(&path) {
            Ok(_)=>break,
            Err(error) if error.kind()==std::io::ErrorKind::NotFound=>{},
            Err(error)=>return Err(error.to_string()),
        }
        if std::time::Instant::now()>=deadline { return Err("owned NT request not received".into()); }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    let metadata=match fs::symlink_metadata(&path) {
        Ok(metadata)=>metadata,Err(error)=>return Err(error.to_string()),
    };
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len()>512 { return Err("request regular/bounded file required".into()); }
    let file=OpenOptions::new().read(true).open(&path).map_err(|error|error.to_string())?;
    if !file.metadata().map_err(|error|error.to_string())?.is_file() { return Err("request changed type".into()); }
    let mut bytes=Vec::new();file.take(513).read_to_end(&mut bytes).map_err(|error|error.to_string())?;
    let request=parse_request(&bytes,event,std::process::id()).map_err(str::to_owned)?;
    // A result is not replaceable. Refuse old or unowned outputs before native calls.
    for output in [RESULT,TEMP] {
        match fs::symlink_metadata(directory.join(output)) {
            Ok(_)=>return Err("result already exists".into()),
            Err(error) if error.kind()==std::io::ErrorKind::NotFound=>{},
            Err(error)=>return Err(error.to_string()),
        }
    }
    let facts=observe(api,&request);
    let temporary=directory.join(TEMP);
    let mut output=OpenOptions::new().write(true).create_new(true).open(&temporary).map_err(|error|error.to_string())?;
    let written=(|| {
        output.write_all(facts.json(&request).as_bytes()).map_err(|error|error.to_string())?;
        output.sync_all().map_err(|error|error.to_string())
    })();
    drop(output);
    let publication=written.and_then(|()|publish_closed_new(&temporary,&directory.join(RESULT)));
    // This pathname was created by this call's successful create_new only.
    // Windows MoveFileW consumes it; portable link publication retains it until
    // unlink. Every remaining owned temporary is explicitly drained on failure.
    let cleanup=if matches!(publication,Ok(true)) { Ok(()) } else { match fs::remove_file(&temporary) {
        Ok(())=>Ok(()),Err(error) if error.kind()==std::io::ErrorKind::NotFound=>Ok(()),Err(error)=>Err(error.to_string()),
    } };
    match (publication,cleanup) {
        (Ok(_),Ok(()))=>{},
        (Err(error),Ok(()))=>return Err(error),
        (Ok(_),Err(error))=>return Err(format!("owned temporary cleanup failed: {error}")),
        (Err(error),Err(cleanup))=>return Err(format!("{error}; owned temporary cleanup failed: {cleanup}")),
    }
    Ok(true)
}

#[cfg(windows)]
pub fn observe_native(directory: &Path, event: &str) -> Result<bool, String> { run(directory,event,&mut Native) }

fn publish_closed_new(temporary: &Path, destination: &Path) -> Result<bool,String> {
    #[cfg(windows)] {
        use std::os::windows::ffi::OsStrExt;
        let old:Vec<u16>=temporary.as_os_str().encode_wide().chain(Some(0)).collect();
        let new:Vec<u16>=destination.as_os_str().encode_wide().chain(Some(0)).collect();
        // MoveFileW has no replace-existing flag and refuses an existing target.
        let success=unsafe { MoveFileW(old.as_ptr(),new.as_ptr()) } != 0;
        let error=if success {0} else {unsafe {GetLastError()}};
        if success { Ok(true) } else { Err(format!("non-replacing publication failed: {error}")) }
    }
    #[cfg(not(windows))] {
        // Same-directory hard-link publication atomically refuses replacement.
        fs::hard_link(temporary,destination).map(|()|false).map_err(|error|error.to_string())
    }
}
