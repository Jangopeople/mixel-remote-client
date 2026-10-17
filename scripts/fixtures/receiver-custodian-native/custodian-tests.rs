// Appended only after the entire production custodian leaf.
#[cfg(test)] pub(super) fn qa_namespace(value:&str)->bool{epoch::set_qa_namespace(value)}
#[cfg(test)] pub(super) fn qa_remove_owned_namespace(){epoch::qa_remove_owned_namespace();}
#[cfg(test)] pub(super) fn custody_component_decisions(){
    let link=AtomicBool::new(true);assert!(retain_authority(&link,true,true));assert!(!retain_authority(&link,true,false));assert!(!retain_authority(&link,true,true));
    let postfix=format!("_mixel-input-custodian-{}",uuid::Uuid::new_v4().simple());
    let child=suspended(&postfix).expect("actual suspended same-token owned child");let process=child.process.clone();assert!(resume(child,|_|u32::MAX).is_none());
    assert_eq!(unsafe{WaitForSingleObject(process.raw(),2000)},OBJECT,"injected Resume failure terminates only its never-resumed owned child");
    let name=format!("Local\\Mixel-Custody-QA-Completion-{}",uuid::Uuid::new_v4().simple());let creator=create_object(&name,true).unwrap();
    let retained=Owned::from(unsafe{OpenEventW(SYNC,0,wide(&name).as_ptr())}).unwrap();assert_ne!(unsafe{SetEvent(creator.raw())},0);drop(creator);
    assert_eq!(unsafe{WaitForSingleObject(retained.raw(),0)},OBJECT);assert_eq!(unsafe{SetEvent(retained.raw())},0,"read-only completion cannot be forged");assert_eq!(unsafe{GetLastError()},5);
    let runtime=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();runtime.block_on(async{
        let(tx,mut rx)=tokio::sync::mpsc::channel::<usize>(1);let paused=tx.clone();rx.close();assert!(rx.try_recv().is_err());assert!(paused.try_send(1).is_err(),"receiver close fences a sender paused after state check");
        let postfix=format!("qa-{}",uuid::Uuid::new_v4().simple());let mut listener=crate::ipc::new_listener(&postfix).await.unwrap();
        let client=crate::ipc::connect(5000,&postfix).await.unwrap();let server=listener.next().await.unwrap().unwrap();let mut stream=crate::ipc::Connection::new(server);
        let mut known=HashMap::new();for index in 0..256{let name=format!("Local\\Mixel-Custody-QA-Batch-{}-{index}",uuid::Uuid::new_v4().simple());let event=create_object(&name,true).unwrap();assert_ne!(unsafe{SetEvent(event.raw())},0);known.insert(format!("scope-{index}"),event);}
        let sender=async{while !known.is_empty(){assert!(flush_completions(&mut stream,&mut known,8).await.unwrap()<=8);}};
        let reader=async{let mut client=client;let mut tokens=std::collections::HashSet::new();for _ in 0..256{match decode::<Response>(client.next_timeout(5000).await.unwrap()).unwrap(){Response::Cleaned(token)=>assert!(tokens.insert(token)),_=>panic!("exact bounded completion")}}assert_eq!(tokens.len(),256);};
        futures_util::future::join(sender,reader).await;
    });
    let peer=own_peer().unwrap();
    for role in [0,1,2]{let lease=Lease::create(&uuid::Uuid::new_v4().simple().to_string()).unwrap();assert!(lease.revoke());assert!(retire_denied_scope(&lease.token,role,&peer));
        let until=std::time::Instant::now()+Duration::from_secs(5);while !lease.cleaned(){sweep();assert!(std::time::Instant::now()<until);std::thread::sleep(Duration::from_millis(10));}
    }
    let(tx,_rx)=tokio::sync::mpsc::channel(MAX_PENDING);let(control,_control_rx)=tokio::sync::mpsc::channel(MAX_PENDING);
    let broker=Broker{state:AtomicUsize::new(1),epoch:Mutex::new(None),sender:tx,control};let mut routes=HashMap::new();
    for index in 0..256{routes.insert(format!("scope-{index}"),RouteReply{peer:peer.clone(),role:0,reply:Arc::new(|_|true)});}
    deliver(Response::Retiring,&mut routes,None,&broker).unwrap();assert!(deliver(Response::Finished,&mut routes,None,&broker).is_err());
    for index in 0..256{deliver(Response::Cleaned(format!("scope-{index}")),&mut routes,None,&broker).unwrap();}
    assert!(deliver(Response::Finished,&mut routes,None,&broker).unwrap());assert_eq!(broker.state.load(Ordering::SeqCst),4);
    epoch::custody_epoch_decisions();
    println!("PASS: actual suspended-child failure, read-only retained completion, revoked roles, 256 completion obligations and authenticated retirement decisions");
}
