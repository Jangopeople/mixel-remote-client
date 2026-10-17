//! Same-privilege receiver effect custodian. The expendable portable broker never
//! injects scoped input. Local receivers use the identical custody path.
//! A lost custodian is failure, never a cleanup receipt.
use super::*;
use hbb_common::{bail, futures_util::StreamExt, timeout, ResultType};
use serde::{Deserialize, Serialize};

const FLAG: &str = "--mixel-input-custodian";
const KEY: &str = "mixel-receiver-custodian-v1";
const MAX_WIRE: usize = MAX_BYTES * 6 + 4096;
const QUEUE_BYTES: usize = MAX_BYTES * 4;
const READY_MS: u64 = 5_000;
static BROKER: OnceLock<Mutex<Option<Arc<Broker>>>> = OnceLock::new();
static CHILD: OnceLock<ChildAuthority> = OnceLock::new();
static QUEUED_BYTES: AtomicUsize = AtomicUsize::new(0);

pub(crate) type InputPacket = (String,String,i32,u8,Vec<u8>,String,u32,bool,bool);
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
enum Operation {
    Input(InputPacket),
    Block { token:String,target:String,conn:i32,role:u8,request:String },
    Retire { token:String,target:String,role:u8 },
    Close,
}
impl Operation {
    fn token(&self)->&str { match self { Self::Input(v)=>&v.0,Self::Block{token,..}|Self::Retire{token,..}=>token,Self::Close=>"" } }
    fn target(&self)->&str { match self { Self::Input(v)=>&v.1,Self::Block{target,..}|Self::Retire{target,..}=>target,Self::Close=>"" } }
    fn role(&self)->u8 { match self { Self::Input(_)=>0,Self::Block{role,..}|Self::Retire{role,..}=>*role,Self::Close=>0 } }
    fn bytes(&self)->usize { self.token().len()+self.target().len()+match self { Self::Input(v)=>v.4.len()+v.5.len(),Self::Block{request,..}=>request.len()+64,Self::Retire{..}=>64,Self::Close=>0 } }
    fn set_target(&mut self,target:String) { match self { Self::Input(v)=>v.1=target,Self::Block{target:value,..}|Self::Retire{target:value,..}=>*value=target,Self::Close=>{} } }
}
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Request { sequence:u64,publisher_handle:u64,publisher_session:u32,operation:Operation }
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
enum Response { Ready, Queued(u64), Refused(String), Block(String,String,bool), Cleaned(String), Retiring, Finished }
struct ChildAuthority { helper:Peer, session:epoch::SessionEpoch, link:AtomicBool }
/// The original publisher event/process remains mandatory. This adds the
/// expendable broker's lifetime only in the actual custodian process.
fn retain_authority(link:&AtomicBool,helper_alive:bool,same_session:bool)->bool{
    if !helper_alive||!same_session{link.store(false,Ordering::SeqCst);}
    link.load(Ordering::SeqCst)
}
pub(super) fn authority_live()->bool {
    CHILD.get().map(|v|{
        if !v.link.load(Ordering::SeqCst){return false;}
        // Any observer (including the independent cleanup sweeper) publishes a
        // permanent deny. A reconnect cannot revive an already CLEANED token.
        retain_authority(&v.link,v.helper.alive(),v.session.live())
    }).unwrap_or(true)
}
fn deny_child() { if let Some(v)=CHILD.get(){v.link.store(false,Ordering::SeqCst);} }
pub(super) fn current_identity()->Option<String> { let pid=unsafe{GetCurrentProcessId()};Some(format!("{}-{}",pid,birth(unsafe{GetCurrentProcess()})?)) }
fn valid_postfix(value:&str)->bool {
    value.strip_prefix("_mixel-input-custodian-").map(|v|v.len()==32&&v.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b))).unwrap_or(false)
}
fn current_target(value:&str)->bool { current_identity().as_deref()==Some(value) }
fn source_owned(operation:&Operation,peer:&Peer)->bool {
    peer.alive()&&operation.token().len()<=100&&operation.target().len()<=100&&operation.bytes()<=MAX_BYTES&&operation.role()<=2
        &&names(operation.token()).map(|(_,_,_,p,b)|(p,b))==Some((peer.pid,peer.born))
}
fn borrowed_process(raw:u64,source:u32)->Option<Peer> {
    let raw=usize::try_from(raw).ok()? as Handle;
    // Validate borrowed first. Never close an unvalidated numeric handle.
    if raw.is_null()||(raw as isize)<0{return None;}
    let pid=unsafe{GetProcessId(raw)};
    if pid==0||birth(raw).is_none()||image(raw)?!=image(unsafe{GetCurrentProcess()})?||unsafe{WaitForSingleObject(raw,0)}!=TIMEOUT{return None;}
    // Session comes only from the authenticated same-session broker. It was
    // established from the actual original pipe/kernel peer before transfer.
    // Never reopen a different-account publisher PID for a session lookup.
    let mut target=0;
    if unsafe{ProcessIdToSessionId(GetCurrentProcessId(),&mut target)}==0||source==0||source!=target{return None;}
    verified_peer_session(pid,Arc::new(Owned::from(raw)?),String::new(),source)
}
fn integrity(token:Handle)->Option<String> {
    let mut needed=0;unsafe{GetTokenInformation(token,25,ptr::null_mut(),0,&mut needed);}
    if !(std::mem::size_of::<usize>()as u32..=65536).contains(&needed){return None;}
    let mut bytes=vec![0usize;(needed as usize+std::mem::size_of::<usize>()-1)/std::mem::size_of::<usize>()];
    if unsafe{GetTokenInformation(token,25,bytes.as_mut_ptr().cast(),needed,&mut needed)}==0{return None;}
    let sid=unsafe{*(bytes.as_ptr()as *const Handle)};let mut text=ptr::null_mut();
    if unsafe{ConvertSidToStringSidW(sid,&mut text)}==0{return None;}
    let mut count=0;while count<256&&unsafe{*text.add(count)}!=0{count+=1;}
    let value=if count<256{String::from_utf16(unsafe{std::slice::from_raw_parts(text,count)}).ok()}else{None};
    unsafe{LocalFree(text.cast());}value
}
fn token_context(process:Handle)->Option<(String,String,u32)> {
    let mut raw=ptr::null_mut();if unsafe{OpenProcessToken(process,8,&mut raw)}==0{return None;}
    let token=Owned::from(raw)?;let sid=token_sid(token.raw())?;let label=integrity(token.raw())?;
    let(mut elevated,mut needed)=(0u32,0u32);
    if unsafe{GetTokenInformation(token.raw(),20,(&mut elevated as *mut u32).cast(),4,&mut needed)}==0||needed!=4||elevated>1{return None;}
    Some((sid,label,elevated))
}
fn same_context(peer:&Peer)->bool {
    token_context(peer.process.raw()).zip(token_context(unsafe{GetCurrentProcess()})).map(|(a,b)|a==b).unwrap_or(false)
}
async fn send<T:Serialize>(stream:&mut crate::ipc::Connection,value:&T)->ResultType<()> {
    let text=serde_json::to_string(value)?;if text.len()>MAX_WIRE{bail!("custodian frame bound");}
    timeout(1000,stream.send(&crate::ipc::Data::Config((KEY.to_owned(),Some(text))))).await??;Ok(())
}
fn decode<T:serde::de::DeserializeOwned>(data:Option<crate::ipc::Data>)->ResultType<T> {
    let Some(crate::ipc::Data::Config((key,Some(text))))=data else{bail!("custodian frame kind");};
    if key!=KEY||text.len()>MAX_WIRE{bail!("custodian frame identity/bound");}
    Ok(serde_json::from_str(&text)?)
}
struct Launch { process:Arc<Owned> }
struct Suspended { process:Arc<Owned>,thread:Option<Owned>,resumed:bool }
impl Drop for Suspended{
    fn drop(&mut self){
        // This guard is disarmed ONLY after successful ResumeThread. It cannot
        // terminate an executing/effect-owning custodian or any foreign process.
        if !self.resumed&&unsafe{WaitForSingleObject(self.process.raw(),0)}==TIMEOUT{
            unsafe{TerminateProcess(self.process.raw(),1);WaitForSingleObject(self.process.raw(),1000);}
        }
    }
}
fn suspended(postfix:&str)->Option<Suspended> {
    if !valid_postfix(postfix){return None;}
    let executable=std::env::current_exe().ok()?.to_str()?.to_owned();
    if executable.chars().any(|v|v=='\0'||v=='"'){return None;}
    let application=wide(&executable);let mut command=wide(&format!("\"{}\" {} {}",executable,FLAG,postfix));
    let mut startup:StartupInfo=unsafe{std::mem::zeroed()};startup.size=std::mem::size_of::<StartupInfo>()as u32;
    let mut process:ProcessInfo=unsafe{std::mem::zeroed()};
    // Direct child, same already-authorized token/session. No runas, credential,
    // service install, inherited handles or automatic privilege transition.
    if unsafe{CreateProcessW(application.as_ptr(),command.as_mut_ptr(),ptr::null_mut(),ptr::null_mut(),0,0x400|4,ptr::null_mut(),ptr::null(),&mut startup,&mut process)}==0{return None;}
    Some(Suspended{process:Arc::new(Owned::from(process.process)?),thread:Owned::from(process.thread),resumed:false})
}
fn resume(mut child:Suspended,execute:impl FnOnce(Handle)->u32)->Option<Launch>{
    if execute(child.thread.as_ref()?.raw())==u32::MAX{return None;}
    child.resumed=true;Some(Launch{process:child.process.clone()})
}
fn launch(postfix:&str)->Option<Launch>{resume(suspended(postfix)?,|thread|unsafe{ResumeThread(thread)})}
struct BrokerWork { operation:Option<Operation>,peer:Option<Peer>,reply:Option<Reply>,bytes:usize,_capture:Option<Arc<Generation>> }
impl Drop for BrokerWork {fn drop(&mut self){QUEUED_BYTES.fetch_sub(self.bytes,Ordering::SeqCst);}}
struct Broker { state:AtomicUsize, epoch:Mutex<Option<String>>, sender:tokio::sync::mpsc::Sender<BrokerWork>,control:tokio::sync::mpsc::Sender<BrokerWork> }
fn stored()->&'static Mutex<Option<Arc<Broker>>>{BROKER.get_or_init(||Mutex::new(None))}
fn broker()->Option<Arc<Broker>> {
    if CHILD.get().is_some(){return None;}
    let mut slot=stored().lock().ok()?;
    if let Some(old)=slot.as_ref(){match old.state.load(Ordering::SeqCst){0|1=>return Some(old.clone()),4=>{},_=>return None,}}
    let(tx,rx)=tokio::sync::mpsc::channel(MAX_PENDING);let(control,control_rx)=tokio::sync::mpsc::channel(MAX_PENDING);
    let value=Arc::new(Broker{state:AtomicUsize::new(0),epoch:Mutex::new(None),sender:tx,control});let worker=value.clone();
    std::thread::Builder::new().name("mixel-input-custodian-broker".to_owned()).spawn(move||{
        let result=tokio::runtime::Builder::new_current_thread().enable_all().build().map_err(hbb_common::anyhow::Error::from)
            .and_then(|runtime|runtime.block_on(broker_loop(worker.clone(),rx,control_rx)));
        if worker.state.load(Ordering::SeqCst)!=4{worker.state.store(3,Ordering::SeqCst);}
        if let Err(error)=result{log::error!("Scoped input custodian failed; cleanup is not acknowledged: {}",error);}
    }).ok()?;
    *slot=Some(value.clone());Some(value)
}
pub(crate) fn ready()->bool {
    let Some(value)=broker()else{return false;};let deadline=std::time::Instant::now()+Duration::from_millis(READY_MS);
    while value.state.load(Ordering::SeqCst)==0&&std::time::Instant::now()<deadline{std::thread::sleep(Duration::from_millis(1));}
    if value.state.load(Ordering::SeqCst)==0{value.state.store(3,Ordering::SeqCst);}
    value.state.load(Ordering::SeqCst)==1
}
pub(crate) fn live()->bool {stored().lock().ok().and_then(|v|v.as_ref().cloned()).map(|v|v.state.load(Ordering::SeqCst)==1).unwrap_or(false)}
pub(super) fn epoch()->Option<String>{let value=stored().lock().ok()?.as_ref()?.clone();if value.state.load(Ordering::SeqCst)!=1{return None;}let identity=value.epoch.lock().ok()?.clone();identity}
fn enqueue(operation:Operation,peer:&Peer,reply:Reply,capture:Option<Arc<Generation>>)->bool {
    if !source_owned(&operation,peer)||!current_target(operation.target()){return false;}
    let Some(value)=broker().filter(|v|v.state.load(Ordering::SeqCst)==1)else{return false;};
    let bytes=operation.bytes();if QUEUED_BYTES.fetch_update(Ordering::SeqCst,Ordering::SeqCst,|n|n.checked_add(bytes).filter(|sum|*sum<=QUEUE_BYTES)).is_err(){return false;}
    let control=!matches!(&operation,Operation::Input(_));
    let work=BrokerWork{operation:Some(operation),peer:Some(peer.clone()),reply:Some(reply),bytes,_capture:capture};
    if control{value.control.try_send(work).is_ok()}else{value.sender.try_send(work).is_ok()}
}
pub(crate) fn input(packet:InputPacket,peer:&Peer,reply:Reply)->bool {enqueue(Operation::Input(packet),peer,reply,None)}
pub(crate) fn block(token:String,target:String,conn:i32,role:u8,request:String,peer:&Peer,reply:Reply)->bool {
    if !(1..=2).contains(&role)||request.len()!=32||!request.bytes().all(|b|b.is_ascii_hexdigit()){return false;}
    enqueue(Operation::Block{token,target,conn,role,request},peer,reply,None)
}
pub(crate) fn retire(token:String,target:String,role:u8,peer:&Peer)->bool {
    enqueue(Operation::Retire{token,target,role},peer,Arc::new(|_|true),None)
}
pub(crate) fn disconnect()->bool {
    let Some(value)=stored().lock().ok().and_then(|v|v.as_ref().cloned())else{return true;};
    if value.state.compare_exchange(1,2,Ordering::SeqCst,Ordering::SeqCst).is_err(){return value.state.load(Ordering::SeqCst)==4;}
    let until=std::time::Instant::now()+Duration::from_secs(5);
    while value.state.load(Ordering::SeqCst)==2&&std::time::Instant::now()<until{std::thread::sleep(Duration::from_millis(1));}
    // Timeout cannot manufacture CLOSED or admit another custodian epoch.
    value.state.load(Ordering::SeqCst)==4
}
struct RouteReply { peer:Peer,role:u8,reply:Reply }
fn deliver(response:Response,routes:&mut HashMap<String,RouteReply>,awaited:Option<u64>,broker:&Broker)->ResultType<bool> {
    match response {
        Response::Queued(sequence)=>{if Some(sequence)!=awaited{bail!("custodian acknowledgment identity");}Ok(true)},
        Response::Refused(token)=>{let route=routes.get(&token).ok_or_else(||hbb_common::anyhow::anyhow!("custodian unknown refusal"))?;if !(route.reply)(PortableReply::Refused(token)){bail!("custodian refusal delivery failed");}Ok(false)},
        Response::Block(token,request,ok)=>{let route=routes.get(&token).filter(|r|r.role!=0).ok_or_else(||hbb_common::anyhow::anyhow!("custodian unknown Block reply"))?;if !(route.reply)(PortableReply::Block(token,request,ok)){bail!("custodian Block delivery failed");}Ok(false)},
        Response::Cleaned(token)=>{if routes.remove(&token).is_none(){bail!("custodian unknown cleanup receipt");}Ok(false)},
        Response::Retiring=>{
            // Authenticated child has permanently latched deny. Stop admission,
            // but drain already-owned frames before the final Close barrier.
            let state=broker.state.load(Ordering::SeqCst);
            if !(1..=2).contains(&state){bail!("custodian retirement phase mismatch");}
            broker.state.store(2,Ordering::SeqCst);Ok(false)
        },
        Response::Finished=>{
            if broker.state.load(Ordering::SeqCst)!=2||!routes.is_empty(){bail!("custodian final cleanup obligations incomplete");}
            broker.state.store(4,Ordering::SeqCst);Ok(true)
        },
        Response::Ready=>bail!("custodian repeated startup acknowledgment"),
    }
}
async fn broker_loop(broker:Arc<Broker>,mut input:tokio::sync::mpsc::Receiver<BrokerWork>,mut control:tokio::sync::mpsc::Receiver<BrokerWork>)->ResultType<()> {
    let postfix=format!("_mixel-input-custodian-{}",uuid::Uuid::new_v4().simple());
    let mut listener=crate::ipc::new_listener(&postfix).await?;
    let child=launch(&postfix).ok_or_else(||hbb_common::anyhow::anyhow!("same-privilege custodian launch failed"))?;
    let raw=timeout(READY_MS,listener.next()).await?.ok_or_else(||hbb_common::anyhow::anyhow!("custodian startup listener ended"))??;
    let mut stream=crate::ipc::Connection::new(raw);
    // Authenticate against the retained direct-child handle, never a PID packet.
    let pid=unsafe{GetProcessId(child.process.raw())};
    if pipe_pid(stream.sas_pipe_handle(),false)!=Some(pid){bail!("custodian was not the directly launched child");}
    let peer=verified_peer(pid,child.process.clone(),process_sid(unsafe{GetCurrentProcess()}).ok_or_else(||hbb_common::anyhow::anyhow!("broker token identity unavailable"))?)
        .filter(same_context).ok_or_else(||hbb_common::anyhow::anyhow!("custodian token/session/image mismatch"))?;
    if !matches!(decode::<Response>(stream.next_timeout(READY_MS).await?)?,Response::Ready){bail!("custodian startup acknowledgment missing");}
    *broker.epoch.lock().map_err(|_|hbb_common::anyhow::anyhow!("custodian epoch identity poisoned"))?=Some(format!("{}:{}",identity(&peer),postfix));
    if broker.state.compare_exchange(0,1,Ordering::SeqCst,Ordering::SeqCst).is_err(){bail!("custodian startup retired before readiness");}
    let mut publisher_session=0;
    if unsafe{ProcessIdToSessionId(GetCurrentProcessId(),&mut publisher_session)}==0||publisher_session==0{bail!("Broker actual session unavailable");}
    let mut routes=HashMap::<String,RouteReply>::new();let mut next=1u64;
    let mut timer=tokio::time::interval(Duration::from_millis(10));
    let mut control_burst=0usize;
    let result:ResultType<()>=async {
        loop {
            let state=broker.state.load(Ordering::SeqCst);
            if state==4{break;}
            let item=if state==2{
                // New enqueue is closed. Every bounded already-owned frame is
                // captured denied before Close; dropping it would strand the
                // source's external cleanup responsibility.
                control.close();input.close();
                control.try_recv().ok().or_else(||input.try_recv().ok())
            }else{tokio::select! {
                biased;
                _=timer.tick()=>{if !peer.alive(){bail!("custodian process lost");}continue;},
                data=stream.next()=>{let response:Response=decode(data?)?;deliver(response,&mut routes,None,&broker)?;continue;},
                item=control.recv(),if control_burst<8||input.is_empty()=>{control_burst+=1;item},
                item=input.recv()=>{control_burst=0;item},
            }};
                    let Some(mut work)=item else{break;};
                    let mut operation=work.operation.take().ok_or_else(||hbb_common::anyhow::anyhow!("custodian empty work"))?;
                    let source=work.peer.take().ok_or_else(||hbb_common::anyhow::anyhow!("custodian missing publisher capability"))?;
                    if !source_owned(&operation,&source)||!current_target(operation.target()){bail!("custodian stale broker scope");}
                    let token=operation.token().to_owned();let role=operation.role();
                    if let Some(old)=routes.get(&token){if old.peer.pid!=source.pid||old.peer.born!=source.born||old.role!=role{bail!("custodian scope identity changed");}}
                    if routes.len()>=256&&!routes.contains_key(&token){
                        if broker.state.load(Ordering::SeqCst)==2{
                            // During retirement retain the queued capability
                            // while genuine completed owners free route slots.
                            let until=std::time::Instant::now()+Duration::from_secs(5);
                            while routes.len()>=256{
                                let remaining=until.saturating_duration_since(std::time::Instant::now()).as_millis().min(1000)as u64;
                                if remaining==0{bail!("custodian retirement capacity remains pending");}
                                let response=decode(stream.next_timeout(remaining).await?)?;
                                deliver(response,&mut routes,None,&broker)?;
                            }
                        }else{
                            if let Some(reply)=work.reply.take(){let _=reply(PortableReply::Refused(token));}
                            continue;
                        }
                    }
                    let reply=work.reply.take().ok_or_else(||hbb_common::anyhow::anyhow!("custodian reply route missing"))?;
                    // Retirement cannot overwrite a live native refusal/Block callback.
                    if !matches!(&operation,Operation::Retire{..})||!routes.contains_key(&token){routes.insert(token.clone(),RouteReply{peer:source.clone(),role,reply});}
                    let mut transferred=ptr::null_mut();
                    if unsafe{DuplicateHandle(GetCurrentProcess(),source.process.raw(),child.process.raw(),&mut transferred,SYNC|0x1000,0,0)}==0{bail!("custodian readonly publisher transfer failed");}
                    operation.set_target(identity(&peer));
                    let sequence=next;next=next.checked_add(1).ok_or_else(||hbb_common::anyhow::anyhow!("custodian request identity exhausted"))?;
                    send(&mut stream,&Request{sequence,publisher_handle:transferred as usize as u64,publisher_session,operation}).await?;
                    let until=std::time::Instant::now()+Duration::from_secs(1);
                    loop {
                        if !peer.alive()||broker.state.load(Ordering::SeqCst)>2{bail!("custodian lost during queue admission; outcome unknown");}
                        let remaining=until.saturating_duration_since(std::time::Instant::now()).as_millis().min(1000)as u64;
                        if remaining==0{bail!("custodian queue admission unknown; no retry");}
                        let response=decode(stream.next_timeout(remaining).await?)?;
                        if deliver(response,&mut routes,Some(sequence),&broker)?{break;}
                    }
        }Ok(())
    }.await;
    let result=if result.is_ok()&&broker.state.load(Ordering::SeqCst)==2{
        let closed:ResultType<()>=async{
            send(&mut stream,&Request{sequence:next,publisher_handle:0,publisher_session:0,operation:Operation::Close}).await?;
            let until=std::time::Instant::now()+Duration::from_secs(5);
            loop{
                let remaining=until.saturating_duration_since(std::time::Instant::now()).as_millis().min(1000)as u64;
                if remaining==0{bail!("Custodian final cleanup remains unacknowledged; no new epoch");}
                let response=decode(stream.next_timeout(remaining).await?)?;
                let finished=matches!(&response,Response::Finished);
                deliver(response,&mut routes,None,&broker)?;
                if finished{break;}
            }Ok(())
        }.await;closed
    }else{result};
    if broker.state.load(Ordering::SeqCst)!=4{broker.state.store(3,Ordering::SeqCst);}
    // Refusal is not cleanup. Original publisher leases stay pending until the
    // surviving custodian actually accepts every owned Up and signals CLEANED.
    for (token,route) in routes{let _=(route.reply)(PortableReply::Refused(token));}
    drop(stream);drop(child);result
}
/// Called only by the private child CLI before GUI initialization.
pub(crate) fn entry()->bool {
    let arguments:Vec<String>=std::env::args().collect();
    if arguments.get(1).map(String::as_str)!=Some(FLAG){return false;}
    if arguments.len()!=3||!valid_postfix(&arguments[2]){log::error!("Invalid private input custodian entry");return true;}
    let result=tokio::runtime::Builder::new_current_thread().enable_all().build().map_err(hbb_common::anyhow::Error::from)
        .and_then(|runtime|runtime.block_on(child_loop(&arguments[2])));
    if let Err(error)=result{log::error!("Input custodian stopped without a cleanup acknowledgment: {}",error);}
    true
}
async fn child_loop(postfix:&str)->ResultType<()> {
    let mut stream=crate::ipc::connect(1000,postfix).await?;
    let peer=stream.receiver_pipe_peer(true).filter(same_context).ok_or_else(||hbb_common::anyhow::anyhow!("custodian broker kernel identity/token unavailable"))?;
    if peer.pid==unsafe{GetCurrentProcessId()}{bail!("custodian cannot be its own broker");}
    let mut epoch=epoch::Epoch::acquire(&peer)?;
    CHILD.set(ChildAuthority{helper:peer,session:epoch.session(),link:AtomicBool::new(true)}).map_err(|_|hbb_common::anyhow::anyhow!("custodian authority already bound"))?;
    let(tx,mut replies)=tokio::sync::mpsc::channel::<Response>(MAX_PENDING);
    let reply:Reply=Arc::new(move|value|{
        let response=match value{PortableReply::Refused(token)=>Response::Refused(token),PortableReply::Block(token,request,ok)=>Response::Block(token,request,ok)};
        tx.try_send(response).is_ok()
    });
    let mut known=HashMap::<String,Owned>::new();
    let mut graceful=false;let mut retiring=false;let mut last=0u64;let mut timer=tokio::time::interval(Duration::from_millis(10));
    let result:ResultType<()>=async {
        send(&mut stream,&Response::Ready).await?;
        loop {
            tokio::select! {
                _=timer.tick()=>{
                    if !authority_live()&&!retiring{
                        retiring=true;deny_child();
                        // Publish retirement before any cleanup wait. The IPC
                        // control plane must finish capture/Close negotiation
                        // even when an actual native release is slow.
                        send(&mut stream,&Response::Retiring).await?;
                        sweep();
                    }
                    // Native callbacks precede their CLEANED event. Drain them
                    // first; completion cannot overtake a Block/refusal reply.
                    if replies.is_empty(){flush_completions(&mut stream,&mut known,8).await?;}
                },
                response=replies.recv()=>{if let Some(response)=response{send(&mut stream,&response).await?;}else{bail!("custodian reply path closed");}},
                data=stream.next()=>{
                    let request:Request=decode(data?)?;
                    if request.sequence<=last||request.sequence==0{bail!("custodian replay refused");}last=request.sequence;
                    if matches!(&request.operation,Operation::Close){if request.publisher_handle!=0||request.publisher_session!=0{bail!("Custodian Close capability mismatch");}graceful=true;break;}
                    let publisher=borrowed_process(request.publisher_handle,request.publisher_session).ok_or_else(||hbb_common::anyhow::anyhow!("custodian publisher capability invalid"))?;
                    if !source_owned(&request.operation,&publisher)||!current_target(request.operation.target()){bail!("custodian scope/authority mismatch");}
                    let token=request.operation.token().to_owned();let refused_token=token.clone();
                    let captured=if known.contains_key(&token){true}else if known.len()>=256{false}else{
                        // Retain read-only completion identity BEFORE queue ACK.
                        // SetEvent plus every other handle closing cannot erase
                        // the completion observation or leak this route forever.
                        let event=names(&token).and_then(|(_,_,name,_,_)|Owned::from(unsafe{OpenEventW(SYNC,0,wide(&name).as_ptr())}));
                        if let Some(event)=event{known.insert(token.clone(),event);true}else{false}
                    };
                    let accepted=captured&&match request.operation{
                        Operation::Input(packet)=>{
                            let token=packet.0.clone();
                            if receive_async(packet,&publisher,reply.clone()){true}
                            else{retire_denied_scope(&token,0,&publisher)&&reply(PortableReply::Refused(token))}
                        },
                        Operation::Block{token,target,conn,role,request}=>{
                            if receive_block(token.clone(),target,conn,role,request.clone(),&publisher,reply.clone()){true}
                            else{retire_denied_scope(&token,role,&publisher)&&reply(PortableReply::Block(token,request,false))}
                        },
                        Operation::Retire{token,target,role}=>receive_retire(token,target,role,&publisher),
                        Operation::Close=>false,
                    };
                    if !accepted{
                        // A revoked frame can arrive after its named scope was
                        // retired. Refuse that owner, never tear down unrelated
                        // live scopes merely because old objects are absent.
                        send(&mut stream,&Response::Refused(refused_token)).await?;
                    }
                    // Queued only, never a native Down/Up acceptance assertion.
                    send(&mut stream,&Response::Queued(request.sequence)).await?;
                }
            }
        }Ok(())
    }.await;
    deny_child();super::shutdown();
    // Custody survives broker process/pipe death. Cleanup runs on the original
    // effect OS thread; this process is never killed to manufacture success.
    loop {
        sweep();
        let empty=remotes().lock().unwrap_or_else(|e|e.into_inner()).is_empty();
        if empty{
            // All actual cleanup ran under the same per-owner effect mutex.
            // A final native-thread barrier also drains any denied continuations.
            if native_effect(true,Box::new(||true)){break;}
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    if !epoch.finish(){bail!("Actual cleanup completed but dirty custody marker/fence could not be retired");}
    if graceful{
        // No native work remains after the barrier. Preserve callback order,
        // then all retained completion handles, before authenticated Finished.
        while let Ok(response)=replies.try_recv(){send(&mut stream,&response).await?;}
        while !known.is_empty(){
            if flush_completions(&mut stream,&mut known,8).await?==0{bail!("custodian missing actual cleanup receipt");}
        }
        send(&mut stream,&Response::Finished).await?;
    }
    drop(stream);result
}

#[path="mixel_receiver_custodian_epoch_windows.rs"]
mod epoch;
fn own_peer()->Option<Peer> {
    let pid=unsafe{GetCurrentProcessId()};let process=Arc::new(Owned::from(unsafe{OpenProcess(SYNC|0x1000,0,pid)})?);
    verified_peer(pid,process,process_sid(unsafe{GetCurrentProcess()})?)
}
fn local_reply(peer:Peer)->Reply {Arc::new(move|response|{match response{
    PortableReply::Refused(token)=>super::super::portable_refused(&token),
    PortableReply::Block(token,request,success)=>block_reply(&token,&request,success,&peer),
}true})}
pub(super) fn local_input(work:super::super::Work) {
    let generation=&work.generation;
    let sent=own_peer().and_then(|peer|{
        let target=current_identity()?;let packet=super::super::encode(&generation.token,&target,generation.conn,&work.action)?;
        // Scope cleanup takes the SAME serial. The final deny check and external
        // responsibility publication cannot be overtaken by cleanup_done.
        let Ok(_serial)=generation.serial.lock()else{return Some(false);};
        if !generation.admitted(){return Some(false);}
        generation.portable_used.fetch_add(1,Ordering::SeqCst);
        // Retain actual named objects through authenticated capture/refusal ACK.
        if enqueue(Operation::Input(packet),&peer,local_reply(peer.clone()),Some(generation.clone())){Some(true)}else{generation.portable_used.fetch_sub(1,Ordering::SeqCst);Some(false)}
    }).unwrap_or(false);
    // The serial is dropped before deny/retirement can wait for cleanup.
    if !sent{generation.failure.store(true,Ordering::SeqCst);generation.deny();}
}
pub(super) fn local_block(scope:&Arc<Generation>,request:&str)->bool {
    let Some(peer)=own_peer()else{return false;};let Some(target)=current_identity()else{return false;};
    enqueue(Operation::Block{token:scope.token.clone(),target,conn:scope.conn,role:scope.role,request:request.to_owned()},&peer,local_reply(peer.clone()),Some(scope.clone()))
}
pub(super) fn local_retire(scope:&Arc<Generation>)->bool {
    let Some(peer)=own_peer()else{return false;};let Some(target)=current_identity()else{return false;};
    enqueue(Operation::Retire{token:scope.token.clone(),target,role:scope.role},&peer,Arc::new(|_|true),Some(scope.clone()))
}

fn retire_denied_scope(token:&str,role:u8,publisher:&Peer)->bool{
    if role>2{return false;}
    // A queue item may race a legitimate revoke before its first capture. This
    // is not malformed authority and must not kill unrelated custody scopes.
    let Some(lease)=Lease::open(token,publisher)else{return false;};
    if lease.active(){return false;}
    {
        let mut entries=remotes().lock().unwrap_or_else(|e|e.into_inner());
        if let Some(remote)=entries.get(token){if remote.role!=role{return false;}}
        else{
            if entries.len()>=256{return false;}
            let Ok(effect_id)=NEXT_EFFECT.fetch_update(Ordering::SeqCst,Ordering::SeqCst,|v|v.checked_sub(1))else{return false;};
            entries.insert(token.to_owned(),Remote{lease:Arc::new(lease),effect_id,role,
                pending:Arc::new(Mutex::new(std::collections::VecDeque::new())),scheduled:Arc::new(AtomicBool::new(false)),retiring:Arc::new(AtomicBool::new(false))});
        }
    }
    // Missing custody state never means cleanup success. Register the denied
    // scope and use the SAME retrying actual-thread cleanup/CLEANED routine.
    sweep();true
}

/// A bounded direct drain cannot overflow the native callback channel. Retain
/// each exact event and route until its authenticated notification was sent.
async fn flush_completions(stream:&mut crate::ipc::Connection,known:&mut HashMap<String,Owned>,bound:usize)->ResultType<usize>{
    let finished:Vec<_>=known.iter().filter(|(_,event)|unsafe{WaitForSingleObject(event.raw(),0)}==OBJECT)
        .take(bound.min(8)).map(|(token,_)|token.clone()).collect();
    let count=finished.len();
    for token in finished{send(stream,&Response::Cleaned(token.clone())).await?;known.remove(&token);}
    Ok(count)
}
