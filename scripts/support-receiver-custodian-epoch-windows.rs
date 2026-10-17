//! Cross-broker fence for one actual interactive Windows logon session.
//! A dirty file survives process death. Only its retained creating handle may
//! delete it, after real receipt/Block cleanup; unknown custody never auto-resets.
use super::*;
use std::os::windows::ffi::OsStrExt;
use std::{marker::PhantomData,rc::Rc,path::{Component,Path,PathBuf}};
const ROOT_NAME:&str="Mixel-Remote-Input-Custody-v1";
#[cfg(test)] static QA_NAMESPACE:OnceLock<String>=OnceLock::new();
#[cfg(test)] pub(super) fn set_qa_namespace(nonce:&str)->bool{
    nonce.len()==32&&nonce.bytes().all(|v|v.is_ascii_digit()||(b'a'..=b'f').contains(&v))&&QA_NAMESPACE.set(nonce.to_owned()).is_ok()
}
fn root_name()->String{
    #[cfg(test)] if let Some(nonce)=QA_NAMESPACE.get(){return format!("{ROOT_NAME}-owned-QA-{nonce}");}
    ROOT_NAME.to_owned()
}
fn mutex_name(epoch:&SessionEpoch)->String{format!("Local\\{}-{}-{}",root_name(),epoch.session,epoch.logon)}
const ROOT_SD:&str="D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x001200a9;;;AU)(A;;0x00000002;;;AU)(A;;RC;;;OW)S:(ML;;NW;;;ME)";
const MUTEX_SD:&str="D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x00120000;;;AU)(A;;RC;;;OW)S:(ML;;NW;;;ME)";
#[repr(C)] struct Guid{_a:u32,_b:u16,_c:u16,_d:[u8;8]}
const PROGRAM_DATA:Guid=Guid{_a:0x62ab5d82,_b:0xfdc1,_c:0x4dc3,_d:[0xa9,0xdd,0x07,0x0d,0x1d,0x49,0x5d,0x97]};
#[repr(C)] struct WtsLevel1{session:u32,state:i32,_flags:i32,_station:[u16;33],_user:[u16;21],_domain:[u16;18],logon:i64,_connect:i64,_disconnect:i64,_last_input:i64,current:i64,_incoming:u32,_outgoing:u32,_in_frames:u32,_out_frames:u32,_in_compressed:u32,_out_compressed:u32}
#[repr(C)] struct WtsInfo{level:u32,data:WtsLevel1}
#[repr(C)] struct FileInfo{attributes:u32,_created:FileTime,_accessed:FileTime,_written:FileTime,_volume:u32,_size_high:u32,_size_low:u32,links:u32,_index_high:u32,_index_low:u32}
#[repr(C)] struct Acl{_revision:u8,_reserved:u8,_size:u16,count:u16,_reserved2:u16}
#[repr(C)] struct Ace{kind:u8,flags:u8,_size:u16,mask:u32,_sid:u32}
#[link(name="wtsapi32")] extern "system"{fn WTSQuerySessionInformationW(server:Handle,session:u32,class:u32,buffer:*mut Handle,bytes:*mut u32)->i32;fn WTSFreeMemory(buffer:Handle);}
#[link(name="shell32")] extern "system"{fn SHGetKnownFolderPath(id:*const Guid,_flags:u32,token:Handle,path:*mut *mut u16)->i32;}
#[link(name="ole32")] extern "system"{fn CoInitializeEx(reserved:Handle,model:u32)->i32;fn CoUninitialize();fn CoTaskMemFree(memory:Handle);}
#[link(name="kernel32")] extern "system"{
    fn CreateDirectoryW(path:*const u16,attributes:*const Attributes)->i32;
    fn CreateFileW(path:*const u16,access:u32,share:u32,attributes:*const Attributes,creation:u32,_flags:u32,template:Handle)->Handle;
    fn GetFileInformationByHandle(file:Handle,info:*mut FileInfo)->i32;
    fn WriteFile(file:Handle,data:*const u8,count:u32,_written:*mut u32,overlapped:Handle)->i32;
    fn FlushFileBuffers(file:Handle)->i32;
    fn SetFileInformationByHandle(file:Handle,class:u32,data:Handle,size:u32)->i32;
}
#[link(name="advapi32")] extern "system"{
    fn GetSecurityInfo(object:Handle,kind:u32,information:u32,owner:*mut Handle,group:*mut Handle,dacl:*mut Handle,sacl:*mut Handle,descriptor:*mut Handle)->u32;
    fn ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor:Handle,_revision:u32,information:u32,text:*mut *mut u16,length:*mut u32)->i32;
    fn GetAce(acl:Handle,index:u32,ace:*mut Handle)->i32;
}
struct Memory(Handle);impl Drop for Memory{fn drop(&mut self){unsafe{LocalFree(self.0);}}}
fn sd(text:&str)->Option<Memory>{let mut raw=ptr::null_mut();(unsafe{ConvertStringSecurityDescriptorToSecurityDescriptorW(wide(text).as_ptr(),1,&mut raw,ptr::null_mut())}!=0).then_some(Memory(raw))}
fn text(raw:*const u16,max:usize)->Option<String>{let mut n=0;while n<max&&unsafe{*raw.add(n)}!=0{n+=1;}if n==max{return None;}String::from_utf16(unsafe{std::slice::from_raw_parts(raw,n)}).ok()}
fn descriptor_text(raw:Handle)->Option<String>{let mut value=ptr::null_mut();if unsafe{ConvertSecurityDescriptorToStringSecurityDescriptorW(raw,1,4|16,&mut value,ptr::null_mut())}==0{return None;}let owned=Memory(value.cast());let result=text(value,16384);drop(owned);result}
fn exact_sd(handle:Handle,kind:u32,expected:&str)->bool{
    let mut raw=ptr::null_mut();if unsafe{GetSecurityInfo(handle,kind,4|16,ptr::null_mut(),ptr::null_mut(),ptr::null_mut(),ptr::null_mut(),&mut raw)}!=0{return false;}
    let actual=Memory(raw);let result=sd(expected).and_then(|value|descriptor_text(value.0)).zip(descriptor_text(actual.0)).map(|(a,b)|a==b).unwrap_or(false);result
}
fn sid_text(sid:Handle)->Option<String>{let mut raw=ptr::null_mut();if unsafe{ConvertSidToStringSidW(sid,&mut raw)}==0{return None;}let owned=Memory(raw.cast());let result=text(raw,256);drop(owned);result}
fn trusted_owner(sid:&str)->bool{matches!(sid,"S-1-5-18"|"S-1-5-32-544"|"S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464")}
fn safe_parent_ace(kind:u8,flags:u8,mask:u32,sid:&str)->bool{
    if flags&8!=0{return true;}
    if kind>1{return false;}
    // Explicit parent-delete, DACL/owner takeover and generic-all rights can
    // hide a dirty child after process death; they are trusted-admin only.
    kind!=0||mask&(0x40|0x40000|0x80000|0x10000000)==0||trusted_owner(sid)
}
fn parent_cannot_delete_children(handle:Handle)->bool{
    let(mut descriptor,mut dacl,mut owner)=(ptr::null_mut(),ptr::null_mut(),ptr::null_mut());
    if unsafe{GetSecurityInfo(handle,1,1|4,&mut owner,ptr::null_mut(),&mut dacl,ptr::null_mut(),&mut descriptor)}!=0||dacl.is_null(){return false;}
    let _owned=Memory(descriptor);if !sid_text(owner).as_deref().map(trusted_owner).unwrap_or(false){return false;}let count=unsafe{(*(dacl as *const Acl)).count}as u32;if count>256{return false;}
    for index in 0..count{
        let mut raw=ptr::null_mut();if unsafe{GetAce(dacl,index,&mut raw)}==0{return false;}
        let ace=unsafe{&*(raw as *const Ace)};if ace.flags&8!=0{continue;}
        let sid=unsafe{(raw as *const u8).add(8)} as Handle;
        let Some(sid)=sid_text(sid)else{return false;};
        if !safe_parent_ace(ace.kind,ace.flags,ace.mask,&sid){return false;}

    }true
}
fn directory(path:&Path)->Option<Owned>{let wide:Vec<u16>=path.as_os_str().encode_wide().chain(Some(0)).collect();let raw=unsafe{CreateFileW(wide.as_ptr(),0x00120081,3,ptr::null(),3,0x02200000,ptr::null_mut())};if raw as isize == -1{return None;}let owned=Owned::from(raw)?;let mut info:FileInfo=unsafe{std::mem::zeroed()};if unsafe{GetFileInformationByHandle(raw,&mut info)}==0||info.attributes&0x10==0||info.attributes&0x400!=0{return None;}Some(owned)}
fn program_data()->Option<PathBuf>{
    struct Com(bool);impl Drop for Com{fn drop(&mut self){if self.0{unsafe{CoUninitialize();}}}}
    let initialized=unsafe{CoInitializeEx(ptr::null_mut(),2)};if initialized<0&&initialized!=0x80010106u32 as i32{return None;}let _com=Com(initialized>=0);
    let mut raw=ptr::null_mut();if unsafe{SHGetKnownFolderPath(&PROGRAM_DATA,0,ptr::null_mut(),&mut raw)}<0||raw.is_null(){return None;}
    let path=text(raw,32768).map(PathBuf::from);unsafe{CoTaskMemFree(raw.cast());}path
}
#[derive(Clone,Debug,PartialEq,Eq)] pub(super) struct SessionEpoch{session:u32,logon:i64}
impl SessionEpoch{
    pub(super) fn current()->Option<Self>{
        let mut session=0;if unsafe{ProcessIdToSessionId(GetCurrentProcessId(),&mut session)}==0||session==0{return None;}
        let(mut raw,mut size)=(ptr::null_mut(),0);if unsafe{WTSQuerySessionInformationW(ptr::null_mut(),session,25,&mut raw,&mut size)}==0{return None;}
        struct Buffer(Handle);impl Drop for Buffer{fn drop(&mut self){unsafe{WTSFreeMemory(self.0);}}}let _buffer=Buffer(raw);
        if size as usize!=std::mem::size_of::<WtsInfo>()||raw.is_null(){return None;}let value=unsafe{&*(raw as *const WtsInfo)};
        if value.level!=1||value.data.session!=session||!matches!(value.data.state,0|1)||value.data.logon<=0||value.data.current<value.data.logon{return None;}
        Some(Self{session,logon:value.data.logon})
    }
    fn prefix(&self)->String{format!("session-{}-logon-{}-",self.session,self.logon)}
    pub(super) fn live(&self)->bool{Self::current().as_ref()==Some(self)}
}
pub(super) struct Epoch{epoch:SessionEpoch,mutex:Owned,held:bool,marker:Option<Owned>,_directories:Vec<Owned>,_same_thread:PhantomData<Rc<()>>}
impl Epoch{
    pub(super) fn acquire(broker:&Peer)->ResultType<Self>{
        let epoch=SessionEpoch::current().ok_or_else(||hbb_common::anyhow::anyhow!("Current physical interactive logon identity unavailable; input custody refused"))?;
        let base=program_data().ok_or_else(||hbb_common::anyhow::anyhow!("Secure machine custody location unavailable"))?;
        let mut path=PathBuf::new();let mut directories=Vec::new();
        for component in base.components(){match component{Component::Prefix(p)=>{let prefix=p.as_os_str().to_string_lossy();if !prefix.ends_with(':')||prefix.len()!=2{bail!("Local machine custody volume required");}path.push(p.as_os_str());},Component::RootDir=>path.push(component.as_os_str()),Component::Normal(p)=>path.push(p),_=>bail!("Unsafe custody ancestry")}
            if path.has_root(){let handle=directory(&path).ok_or_else(||hbb_common::anyhow::anyhow!("Custody parent is linked or unavailable"))?;if !parent_cannot_delete_children(handle.raw()){bail!("Custody parent permits unprivileged deletion; input refused");}directories.push(handle);}}
        path.push(root_name());let descriptor=sd(ROOT_SD).ok_or_else(||hbb_common::anyhow::anyhow!("Custody directory descriptor unavailable"))?;
        let attributes=Attributes{length:std::mem::size_of::<Attributes>()as u32,descriptor:descriptor.0,inherit:0};let root:Vec<u16>=path.as_os_str().encode_wide().chain(Some(0)).collect();
        if unsafe{CreateDirectoryW(root.as_ptr(),&attributes)}==0&&unsafe{GetLastError()}!=183{bail!("Private custody namespace cannot be created without changing privileges");}
        let handle=directory(&path).ok_or_else(||hbb_common::anyhow::anyhow!("Custody namespace is linked or unavailable"))?;
        if !exact_sd(handle.raw(),1,ROOT_SD){bail!("Custody namespace ACL changed; no repair or adoption");}directories.push(handle);
        let descriptor=sd(MUTEX_SD).ok_or_else(||hbb_common::anyhow::anyhow!("Custody mutex descriptor unavailable"))?;
        let attributes=Attributes{length:std::mem::size_of::<Attributes>()as u32,descriptor:descriptor.0,inherit:0};let mutex=Owned::from(unsafe{CreateMutexExW(&attributes,wide(&mutex_name(&epoch)).as_ptr(),0,SYNC|0x20000)}).ok_or_else(||hbb_common::anyhow::anyhow!("Custody cross-SID fence unavailable"))?;
        if !exact_sd(mutex.raw(),6,MUTEX_SD){bail!("Custody fence ACL mismatch");}
        let wait=unsafe{WaitForSingleObject(mutex.raw(),5000)};
        if wait==ABANDONED{unsafe{ReleaseMutex(mutex.raw());}bail!("Prior custodian terminated with unknown native state; session reset required");}
        if wait!=OBJECT{bail!("Prior session custodian still owns input cleanup; no fallback");}
        // The same OS thread holds this across the current-thread async runtime.
        struct Unlock<'a>(&'a Owned);impl Drop for Unlock<'_>{fn drop(&mut self){unsafe{ReleaseMutex(self.0.raw());}}}let unlock=Unlock(&mutex);
        let prefix=epoch.prefix();let mut count=0;
        for item in std::fs::read_dir(&path)?{let item=item?;count+=1;if count>4096{bail!("Bounded custody directory inventory exceeded");}let name=item.file_name();let Some(name)=name.to_str()else{bail!("Custody filename encoding changed");};if name.starts_with(&prefix){bail!("Prior dirty input epoch remains; no automatic restart or regrant");}}
        if !epoch.live()||!broker.alive(){bail!("Custody logon/broker changed before grant");}
        let name=format!("{}{}.dirty",prefix,uuid::Uuid::new_v4().simple());let marker_path=path.join(name);let file:Vec<u16>=marker_path.as_os_str().encode_wide().chain(Some(0)).collect();let sid=process_sid(unsafe{GetCurrentProcess()}).ok_or_else(||hbb_common::anyhow::anyhow!("Custodian SID unavailable"))?;
        let marker_sd=format!("D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x0013019f;;;{sid})(A;;RC;;;OW)S:(ML;;NW;;;ME)");let descriptor=sd(&marker_sd).ok_or_else(||hbb_common::anyhow::anyhow!("Custody marker descriptor unavailable"))?;let attributes=Attributes{length:std::mem::size_of::<Attributes>()as u32,descriptor:descriptor.0,inherit:0};
        let raw=unsafe{CreateFileW(file.as_ptr(),0x0013019f,1,&attributes,1,0x00200080,ptr::null_mut())};if raw as isize == -1{bail!("Exclusive custody marker creation failed");}let marker=Owned::from(raw).ok_or_else(||hbb_common::anyhow::anyhow!("Custody marker handle absent"))?;
        let mut info:FileInfo=unsafe{std::mem::zeroed()};if unsafe{GetFileInformationByHandle(marker.raw(),&mut info)}==0||info.attributes&(0x400|0x10)!=0||info.links!=1||!exact_sd(marker.raw(),1,&marker_sd){bail!("Unsafe custody marker identity/mode");}
        let body=format!("mixel-input-custody-v1\nsession={}\nlogon={}\nbroker={}-{}\ncustodian={}\n",epoch.session,epoch.logon,broker.pid,broker.born,current_identity().ok_or_else(||hbb_common::anyhow::anyhow!("Custodian identity unavailable"))?);let mut written=0;
        if unsafe{WriteFile(marker.raw(),body.as_ptr(),body.len()as u32,&mut written,ptr::null_mut())}==0||written as usize!=body.len()||unsafe{FlushFileBuffers(marker.raw())}==0{bail!("Durable dirty custody publication failed");}
        std::mem::forget(unlock);Ok(Self{epoch,mutex,held:true,marker:Some(marker),_directories:directories,_same_thread:PhantomData})
    }
    pub(super) fn session(&self)->SessionEpoch{self.epoch.clone()}
    pub(super) fn finish(&mut self)->bool{
        if !self.held{return false;}let mut remove=1u8;
        if unsafe{SetFileInformationByHandle(self.marker.as_ref().map(Owned::raw).unwrap_or(ptr::null_mut()),4,(&mut remove as *mut u8).cast(),1)}==0{return false;}
        // No path unlink and no foreign marker clear. Closing this exact retained
        // handle publishes deletion before the fence can be released.
        drop(self.marker.take());
        if unsafe{ReleaseMutex(self.mutex.raw())}==0{return false;}self.held=false;true
    }
}
impl Drop for Epoch{fn drop(&mut self){if self.held{unsafe{ReleaseMutex(self.mutex.raw());}}}}
