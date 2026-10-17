//! Real Win32 custody authority and process lifecycle, mock input primitives.
//! The four production receiver/custody files are copied whole, unchanged.
#![allow(dead_code, unused_imports)]
#![deny(unused_mut)]
extern crate self as hbb_common;
pub use {anyhow, bytes, futures_util, protobuf, tokio};
pub use anyhow::bail;
pub type ResultType<T> = anyhow::Result<T>;
pub async fn timeout<T>(ms:u64,value:impl std::future::Future<Output=T>)->Result<T,tokio::time::error::Elapsed>{
    tokio::time::timeout(std::time::Duration::from_millis(ms),value).await
}
include!(concat!(env!("OUT_DIR"), "/protos/mod.rs"));
pub use message as message_proto;
use std::sync::{Arc,Mutex,OnceLock,atomic::{AtomicBool,AtomicUsize,Ordering}};

fn journal(value:serde_json::Value){
    use std::io::Write;
    let root=std::path::PathBuf::from(std::env::var_os("MIXEL_CUSTODY_QA_OUTPUT").expect("owned QA output"));
    assert!(root.is_absolute());
    let path=root.join(format!("effects-{}.jsonl",std::process::id()));
    let mut file=std::fs::OpenOptions::new().create(true).append(true).open(path).unwrap();
    let mut value=value;value["pid"]=std::process::id().into();
    value["thread"]=format!("{:?}",std::thread::current().id()).into();
    value["case"]=std::env::var("MIXEL_CUSTODY_QA_CASE").unwrap_or_default().into();
    serde_json::to_writer(&mut file,&value).unwrap();file.write_all(b"\n").unwrap();file.sync_data().unwrap();
}
mod platform {
    pub fn block_input(enabled:bool)->(bool,String){
        super::journal(serde_json::json!({"kind":"mockBlock","enabled":enabled}));(true,String::new())
    }
}
mod server {
    pub(crate) mod input_service {
        use super::super::*;
        #[path="../mixel_receiver_receipts.rs"] mod receipts;
        thread_local!{static OWNER:std::cell::RefCell<Option<(String,Arc<dyn Fn()->bool+Send+Sync>)>>=const{std::cell::RefCell::new(None)};}
        fn ledger()->&'static Mutex<receipts::Ledger<u32,u32,u32>>{
            static VALUE:OnceLock<Mutex<receipts::Ledger<u32,u32,u32>>>=OnceLock::new();VALUE.get_or_init(||Mutex::new(Default::default()))
        }
        pub(crate) fn with_receiver_owner_checked(owner:&str,_effect:i32,allowed:Arc<dyn Fn()->bool+Send+Sync>,effect:impl FnOnce()){
            let old=OWNER.with(|v|v.replace(Some((owner.to_owned(),allowed))));effect();OWNER.with(|v|v.replace(old));
        }
        pub(crate) fn with_receiver_owner_scope_checked(owner:&str,effect_id:i32,allowed:Arc<dyn Fn()->bool+Send+Sync>,_runner:Arc<dyn Fn(Box<dyn FnOnce()+Send>)->bool+Send+Sync>,effect:impl FnOnce()){
            with_receiver_owner_checked(owner,effect_id,allowed,effect)
        }
        fn owner()->Option<(String,Arc<dyn Fn()->bool+Send+Sync>)>{OWNER.with(|v|v.borrow().clone())}
        fn emit(route:u32,down:bool)->Result<(),String>{journal(serde_json::json!({"kind":"mockKey","route":route,"down":down}));Ok(())}
        pub(crate) fn handle_key_(event:&message_proto::KeyEvent){
            let Some((owner,allowed))=owner()else{return;};
            let claim=receipts::Claim{owner,temporary:0};let key=event.chr();
            let mut state=ledger().lock().unwrap();
            if event.down{let _=state.down(claim,key,key,key,false,std::time::Instant::now(),move||allowed(),emit);}
            else{let _=state.up(&claim,key,Some(key),emit);}
        }
        pub(crate) fn handle_mouse_(_event:&message_proto::MouseEvent,_conn:i32,_name:String,_argb:u32,_simulate:bool,_cursor:bool){}
        pub(crate) fn handle_pointer_(_event:&message_proto::PointerDeviceEvent,_conn:i32){}
        pub(crate) fn release_receiver_owner(owner:&str)->bool{
            ledger().lock().unwrap().retire(|claim,_|claim.owner==owner,emit).is_empty()
        }
        pub(crate) fn clear_relative_mouse_active(_conn:i32){}
    }
    pub(crate) mod portable_service {pub(crate) mod client{
        pub(crate) fn running()->bool{false}
        pub(crate) fn send_receiver_input(_token:&str,_target:&str,_conn:i32,_action:&super::super::mixel_receiver_input::Action)->bool{false}
        pub(crate) fn send_receiver_block(_token:&str,_target:&str,_conn:i32,_role:u8,_request:&str)->bool{false}
        pub(crate) fn send_receiver_retire(_token:&str,_target:&str,_role:u8)->bool{false}
    }}
    #[path="../mixel_receiver_input.rs"] pub(crate) mod mixel_receiver_input;
}

/// Fixture transport is length bounded actual named-pipe I/O. Authentication,
/// source/session/birth checks and handle transfers are the production bodies.
mod ipc {
    use super::*;
    use std::{os::windows::io::AsRawHandle,pin::Pin,task::{Context,Poll}};
    use tokio::{io::{AsyncReadExt,AsyncWriteExt},net::windows::named_pipe::{NamedPipeServer,NamedPipeClient,ServerOptions,ClientOptions}};
    #[derive(serde::Serialize,serde::Deserialize)] pub(crate) enum Data{Config((String,Option<String>))}
    enum Pipe{Server(NamedPipeServer),Client(NamedPipeClient)}
    pub(crate) struct Connection(Pipe);
    impl Connection{
        pub(crate) fn new(raw:NamedPipeServer)->Self{Self(Pipe::Server(raw))}
        pub(crate) fn sas_pipe_handle(&self)->*mut std::ffi::c_void{match &self.0{Pipe::Server(v)=>v.as_raw_handle(),Pipe::Client(v)=>v.as_raw_handle()}}
        pub(crate) fn receiver_pipe_peer(&self,server_peer:bool)->Option<server::mixel_receiver_input::Peer>{server::mixel_receiver_input::pipe_peer(self.sas_pipe_handle(),server_peer)}
        pub(crate) async fn send(&mut self,value:&Data)->ResultType<()>{
            let data=serde_json::to_vec(value)?;if data.len()>2*1024*1024{bail!("fixture frame bound");}
            macro_rules! write{($v:expr)=>{{ $v.write_u32_le(data.len()as u32).await?;$v.write_all(&data).await?;$v.flush().await?; }}}
            match &mut self.0{Pipe::Server(v)=>write!(v),Pipe::Client(v)=>write!(v)};Ok(())
        }
        pub(crate) async fn next(&mut self)->ResultType<Option<Data>>{
            macro_rules! read{($v:expr)=>{{
                let size=match $v.read_u32_le().await{Ok(v)=>v,Err(error)if error.kind()==std::io::ErrorKind::UnexpectedEof=>return Ok(None),Err(error)=>return Err(error.into())};
                if size>2*1024*1024{bail!("fixture read bound");}let mut data=vec![0;size as usize];$v.read_exact(&mut data).await?;serde_json::from_slice(&data)?
            }}}
            Ok(Some(match &mut self.0{Pipe::Server(v)=>read!(v),Pipe::Client(v)=>read!(v)}))
        }
        pub(crate) async fn next_timeout(&mut self,ms:u64)->ResultType<Option<Data>>{timeout(ms,self.next()).await?}
    }
    pub(crate) struct Listener{rx:tokio::sync::mpsc::Receiver<ResultType<NamedPipeServer>>}
    impl futures_util::Stream for Listener{type Item=ResultType<NamedPipeServer>;fn poll_next(mut self:Pin<&mut Self>,cx:&mut Context<'_>)->Poll<Option<Self::Item>>{self.rx.poll_recv(cx)}}
    fn pipe_name(postfix:&str)->String{assert!(postfix.len()<150&&postfix.bytes().all(|b|b.is_ascii_alphanumeric()||b"_-".contains(&b)));format!("\\\\.\\pipe\\Mixel-owned-custody-QA-{postfix}")}
    pub(crate) async fn new_listener(postfix:&str)->ResultType<Listener>{
        let name=pipe_name(postfix);let first=ServerOptions::new().first_pipe_instance(true).create(&name)?;
        let(tx,rx)=tokio::sync::mpsc::channel(8);
        tokio::spawn(async move{
            let mut next=first;
            loop{if let Err(error)=next.connect().await{let _=tx.send(Err(error.into())).await;break;}
                let replacement=match ServerOptions::new().create(&name){Ok(v)=>v,Err(error)=>{let _=tx.send(Err(error.into())).await;break;}};
                if tx.send(Ok(next)).await.is_err(){break;}next=replacement;
            }
        });Ok(Listener{rx})
    }
    pub(crate) async fn connect(ms:u64,postfix:&str)->ResultType<Connection>{
        let name=pipe_name(postfix);let until=std::time::Instant::now()+std::time::Duration::from_millis(ms);
        loop{match ClientOptions::new().open(&name){Ok(v)=>return Ok(Connection(Pipe::Client(v))),Err(error)=>{if std::time::Instant::now()>=until{return Err(error.into());}}}tokio::time::sleep(std::time::Duration::from_millis(5)).await;}
    }
    pub(crate) mod receiver_sas{pub(crate) async fn request(_scope:&str)->super::ResultType<()>{super::bail!("fixture never sends SAS")}}
}
fn main(){server::mixel_receiver_input::custody_fixture_main();}
