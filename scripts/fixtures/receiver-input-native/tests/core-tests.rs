fn pump_until(mut ready: impl FnMut()->bool) {
    let deadline=std::time::Instant::now()+Duration::from_secs(5);
    while !ready() {
        assert!(std::time::Instant::now()<deadline,"component callback timeout");
        #[cfg(target_os="macos")] unsafe {
            #[link(name="CoreFoundation",kind="framework")] extern "C" {
                static kCFRunLoopDefaultMode:*const std::ffi::c_void;
                fn CFRunLoopRunInMode(mode:*const std::ffi::c_void,seconds:f64,return_after_source:bool)->i32;
            }
            CFRunLoopRunInMode(kCFRunLoopDefaultMode,0.001,true);
        }
        std::thread::sleep(Duration::from_millis(1));
    }
}
fn key_action(down:bool)->Action {
    let mut key=KeyEvent{down,mode:hbb_common::message_proto::KeyboardMode::Map.into(),..Default::default()};key.set_chr(30);Action::Key(key,false)
}
fn captured(receiver:&Receiver,action:Action)->Work {
    let generation=receiver.inner.state.lock().unwrap().generation.as_ref().unwrap().clone();
    let bytes=action.size();receiver.inner.pending.fetch_add(1,Ordering::SeqCst);receiver.inner.pending_bytes.fetch_add(bytes,Ordering::SeqCst);
    Work{inner:receiver.inner.clone(),generation,action,bytes}
}
fn run_actual(work: Work) {
    #[cfg(windows)] assert!(windows::native_effect(false,Box::new(move || {let _=apply_local_part(&work,&work.action);true})));
    #[cfg(not(windows))] apply_local(work);
}
fn clear_observed(){super::super::observed().lock().unwrap().clear();super::super::ENTERED.store(false,Ordering::SeqCst);}
fn stale_revoke_regrant_close() {
    clear_observed();let receiver=Receiver::new(7);assert!(receiver.authorize(true));
    let stale=captured(&receiver,key_action(true));let old=stale.generation.token.clone();
    assert!(receiver.set_permission(false));assert!(receiver.set_permission(true));
    let fresh=captured(&receiver,key_action(true));assert_ne!(old,fresh.generation.token);
    run_actual(stale);run_actual(fresh);
    assert_eq!(super::super::observed().lock().unwrap().iter().filter(|e|e.down).count(),1);
    let closing=captured(&receiver,key_action(true));assert!(receiver.close());run_actual(closing);
    assert!(!receiver.authorize(true));assert!(!receiver.set_permission(true));
    assert_eq!(super::super::observed().lock().unwrap().iter().filter(|e|e.down).count(),1);
}
fn count_and_byte_overflow_fail_closed() {
    let receiver=Receiver::new(8);assert!(receiver.authorize(true));
    let held=(0..MAX_PENDING).map(|_|captured(&receiver,key_action(true))).collect::<Vec<_>>();
    receiver.enqueue(key_action(false));assert!(receiver.failed());
    for work in held {assert!(!work.generation.active.load(Ordering::SeqCst));drop(work);}
    assert_eq!(receiver.inner.pending.load(Ordering::SeqCst),0);
    let receiver=Receiver::new(9);assert!(receiver.authorize(true));
    let mut key=KeyEvent::new();key.set_seq("x".repeat(MAX_BYTES+1));receiver.enqueue(Action::Key(key,false));assert!(receiver.failed());
    assert_eq!(receiver.inner.pending.load(Ordering::SeqCst),0);assert_eq!(receiver.inner.pending_bytes.load(Ordering::SeqCst),0);
}
fn independent_parallel_owner_and_cursor_real_peer() {
    clear_observed();let first=Receiver::new(11);let second=Receiver::new(12);assert!(first.authorize(true));assert!(second.authorize(true));
    let old=captured(&first,key_action(true));let second_work=captured(&second,key_action(true));assert!(first.close());run_actual(old);run_actual(second_work);
    assert_eq!(super::super::observed().lock().unwrap().iter().filter(|e|e.down).count(),1);assert!(second.close());
    let cursor=Receiver::new(99);assert!(cursor.authorize(false));cursor.enqueue(Action::Mouse(MouseEvent::new(),"synthetic".to_owned(),0,false,true));
    pump_until(||super::super::observed().lock().unwrap().iter().any(|e|e.peer==99));assert!(cursor.close());
}
fn bounded_backlog_revocation_at_real_dispatch() {
    clear_observed();let receiver=Receiver::new(13);assert!(receiver.authorize(true));
    super::super::PAUSE.store(true,Ordering::SeqCst);receiver.enqueue(key_action(true));
    #[cfg(not(target_os="macos"))] pump_until(||super::super::ENTERED.load(Ordering::SeqCst));
    for _ in 0..64 { receiver.enqueue(key_action(true)); }
    let start=std::time::Instant::now();assert!(receiver.set_permission(false));assert!(start.elapsed()<Duration::from_secs(1));
    super::super::PAUSE.store(false,Ordering::SeqCst);
    pump_until(||receiver.inner.pending.load(Ordering::SeqCst)==0);
    assert!(!super::super::observed().lock().unwrap().iter().any(|e|e.down));assert!(receiver.close());
}
fn long_text_is_admitted_and_interruptible() {
    clear_observed();let receiver=Receiver::new(14);assert!(receiver.authorize(true));
    let mut key=KeyEvent::new();key.set_seq("a🙂".repeat(10_000));
    let work=captured(&receiver,Action::Key(key,false));let generation=work.generation.clone();
    let count=Arc::new(AtomicUsize::new(0));let observed=count.clone();let active=generation.clone();
    apply(&generation.token,generation.effect_id,generation.conn,&work.action,Arc::new(move||observed.fetch_add(1,Ordering::SeqCst)<200&&active.active.load(Ordering::SeqCst)),local_runner(generation.clone()));
    assert!(count.load(Ordering::SeqCst)>=200);assert!(super::super::observed().lock().unwrap().iter().filter(|e|!e.text.is_empty()).count()<20_000);
    drop(work);assert!(!receiver.failed());assert!(receiver.close());
}
fn block_control_has_independent_native_thread_and_failed_unblock_retry() {
    let first=Receiver::new(21);let second=Receiver::new(22);assert!(first.authorize(true));assert!(second.authorize(true));
    assert!(first.block(true,false));assert!(second.block(true,true));
    first.enqueue(key_action(true));pump_until(||super::super::observed().lock().unwrap().iter().any(|event|event.down));
    #[cfg(windows)] assert_eq!(*super::super::input_threads().lock().unwrap().last().unwrap(),super::super::block_calls().lock().unwrap()[0].1,"SendInput and BlockInput must share the exact native thread");
    assert!(first.close());
    super::super::UNBLOCK_FAILURES.store(3,Ordering::SeqCst);assert!(second.close());
    let calls=super::super::block_calls().lock().unwrap();assert!(calls.iter().any(|(enabled,_)|*enabled));assert!(calls.iter().any(|(enabled,_)|!*enabled));
    assert!(calls.iter().all(|(_,thread)|*thread==calls[0].1));
}
fn cleanup_must_complete_before_ack_and_deferred_is_fenced() {
    clear_observed();let receiver=Receiver::new(31);assert!(receiver.authorize(true));
    let work=captured(&receiver,key_action(true));let generation=work.generation.clone();run_actual(work);
    super::super::RELEASE_FAILURES.store(3,Ordering::SeqCst);assert!(receiver.set_permission(false));
    assert!(generation.cleanup_done.load(Ordering::SeqCst));assert_eq!(super::super::RELEASE_FAILURES.load(Ordering::SeqCst),0);
    assert!(receiver.set_permission(true));let generation=receiver.inner.state.lock().unwrap().generation.clone().unwrap();
    let guard=generation.serial.lock().unwrap();let executed=Arc::new(AtomicBool::new(false));let seen=executed.clone();
    let start=std::time::Instant::now();assert!(local_runner(generation.clone())(Box::new(move||{seen.store(true,Ordering::SeqCst);})));assert!(start.elapsed()<Duration::from_millis(100));
    generation.active.store(false,Ordering::SeqCst);drop(guard);assert!(receiver.close());
    pump_until(||DEFERRED_PENDING.load(Ordering::SeqCst)==0);assert!(!executed.load(Ordering::SeqCst));
}
#[cfg(windows)] fn helper_transition_with_held_down_fails_closed() {
    clear_observed();let receiver=Receiver::new(32);assert!(receiver.authorize(true));run_actual(captured(&receiver,key_action(true)));
    super::portable_service::client::RUNNING.store(true,Ordering::SeqCst);assert!(!receiver.block(true,false));assert!(!receiver.block(true,true));receiver.enqueue(key_action(false));pump_until(||receiver.failed());
    super::portable_service::client::RUNNING.store(false,Ordering::SeqCst);assert!(receiver.close());
    assert!(super::super::observed().lock().unwrap().iter().any(|event|event.text=="cleanup"));
}
pub(crate) fn run_component_tests() {
    let cases:[(&str,fn());7]=[
        ("stale revoke/regrant/close",stale_revoke_regrant_close),
        ("count and byte overflow",count_and_byte_overflow_fail_closed),
        ("parallel owner and real cursor peer",independent_parallel_owner_and_cursor_real_peer),
        ("bounded backlog at actual dispatch",bounded_backlog_revocation_at_real_dispatch),
        ("long text per-scalar cancellation",long_text_is_admitted_and_interruptible),
        ("owned BlockInput actual-thread affinity and retry",block_control_has_independent_native_thread_and_failed_unblock_retry),
        ("cleanup acknowledgment and nonblocking deferred revoke",cleanup_must_complete_before_ack_and_deferred_is_fenced),
    ];
    for(name,case)in cases {case();println!("PASS: {name}");}
    #[cfg(target_os="macos")] { main_thread_can_complete_observer_queued_cleanup();println!("PASS: main-thread close completes already-queued observer cleanup"); }
    #[cfg(windows)] { startup_barrier_retires_input_and_both_blocks();println!("PASS: startup admission barrier retires keys and both BlockInput owners"); helper_transition_with_held_down_fails_closed(); println!("PASS: helper transition held modifier fails closed"); windows::run_native_tests(); }
}
#[cfg(windows)] pub(crate) fn windows_child(){windows::publisher_child();}

#[cfg(windows)] pub(crate) fn windows_query_child(){windows::query_child();}

#[cfg(windows)] fn startup_barrier_retires_input_and_both_blocks() {
    clear_observed();let receiver=Receiver::new(55);assert!(receiver.authorize(true));assert!(receiver.block(true,false));assert!(receiver.block(true,true));run_actual(captured(&receiver,key_action(true)));
    assert!(quiesce_helper_transition());assert!(receiver.failed());assert!(!receiver.inner.alive.load(Ordering::SeqCst));assert!(!receiver.block(true,true));assert!(!receiver.set_permission(true));
    let fresh=Receiver::new(56);assert!(!fresh.authorize(true));assert!(receiver.close());assert!(fresh.close());
    assert!(!blocks().unwrap().state.lock().unwrap().actual);TRANSITION.store(false,Ordering::SeqCst);
}

#[cfg(target_os="macos")] fn main_thread_can_complete_observer_queued_cleanup() {
    clear_observed();let receiver=Receiver::new(61);assert!(receiver.authorize(true));run_actual(captured(&receiver,key_action(true)));
    let generation=receiver.inner.state.lock().unwrap().generation.clone().unwrap();generation.active.store(false,Ordering::SeqCst);
    let observer=generation.clone();std::thread::spawn(move||schedule_cleanup(observer)).join().unwrap();
    assert!(generation.cleanup_requested.load(Ordering::SeqCst));assert!(!generation.cleanup_done.load(Ordering::SeqCst));
    assert!(receiver.close());assert!(generation.cleanup_done.load(Ordering::SeqCst));
    let done=Arc::new(AtomicBool::new(false));let seen=done.clone();dispatch::Queue::main().exec_async(move||{seen.store(true,Ordering::SeqCst);});pump_until(||done.load(Ordering::SeqCst));
    assert_eq!(super::super::observed().lock().unwrap().iter().filter(|e|e.owner==generation.token&&e.text=="cleanup").count(),1,"observer duplicate must not perform second cleanup");
}
