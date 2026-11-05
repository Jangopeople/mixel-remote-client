// Appended after the entire final receiver core; deterministic stale-Work edge.
#[cfg(test)] pub(crate) fn custody_stale_work_case(){
    let receiver=Receiver::new(7101);assert!(receiver.authorize(true));
    let generation=receiver.inner.state.lock().unwrap().generation.clone().unwrap();
    let mut key=KeyEvent::new();key.set_chr(31);key.down=true;
    receiver.inner.pending.fetch_add(1,Ordering::SeqCst);
    let work=Work{inner:receiver.inner.clone(),generation:generation.clone(),action:Action::Key(key,false),bytes:0};
    let(entered,ready)=mpsc::sync_channel(0);let(resume,paused)=mpsc::sync_channel(0);
    let worker=std::thread::spawn(move||{
        assert!(work.generation.admitted());entered.send(()).unwrap();paused.recv().unwrap();
        // The actual final Local enqueue rechecks under the same serial that
        // completed retirement, retaining the real generation objects meanwhile.
        windows::enqueue_local(work);
    });
    ready.recv_timeout(Duration::from_secs(5)).unwrap();assert!(receiver.close());
    assert_eq!(generation.portable_used.load(Ordering::SeqCst),0);resume.send(()).unwrap();worker.join().unwrap();
    assert_eq!(generation.portable_used.load(Ordering::SeqCst),0);assert!(windows::custodian_live());
    // An unrelated actual generation must still be usable after the stale item.
    let other=Receiver::new(7102);assert!(other.authorize(true));assert!(other.close());
    assert!(windows::custodian_disconnect());
    println!("PASS: actual stale Work paused after early admission, revoke/cleanup completes, final serial refuses it without shared custody teardown");
}
