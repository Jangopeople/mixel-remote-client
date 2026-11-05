// Appended after the exact frozen pre-custody receiver core: original negative.
#[cfg(test)] pub(crate) fn original_negative_main(){
    use std::io::Write;
    if std::env::args().nth(1).as_deref()==Some("--qa-original-publisher"){
        let receiver=Receiver::new(8001);assert!(receiver.authorize(true));let mut key=KeyEvent::new();key.set_chr(30);key.down=true;receiver.enqueue(Action::Key(key,false));
        let end=std::time::Instant::now()+Duration::from_secs(10);loop{
            let root=std::path::PathBuf::from(std::env::var_os("MIXEL_CUSTODY_QA_OUTPUT").unwrap());let path=root.join(format!("effects-{}.jsonl",std::process::id()));
            if std::fs::read_to_string(path).unwrap_or_default().contains("\"down\":true"){break;}
            assert!(std::time::Instant::now()<end);std::thread::sleep(Duration::from_millis(10));
        }
        // Exact old Local executor has a successful production-Ledger Down in
        // this expendable process. Abrupt exit bypasses every Drop/revoke path.
        std::process::exit(79);
    }
    let mut child=std::process::Command::new(std::env::current_exe().unwrap()).arg("--qa-original-publisher").env("MIXEL_CUSTODY_QA_CASE","original-negative").spawn().unwrap();let pid=child.id();let end=std::time::Instant::now()+Duration::from_secs(15);
    let status=loop{if let Some(status)=child.try_wait().unwrap(){break status;}if std::time::Instant::now()>=end{let _=child.kill();let _=child.wait();panic!("owned original-negative child timeout");}std::thread::sleep(Duration::from_millis(10));};assert_eq!(status.code(),Some(79));
    let root=std::path::PathBuf::from(std::env::var_os("MIXEL_CUSTODY_QA_OUTPUT").unwrap());let raw=std::fs::read_to_string(root.join(format!("effects-{pid}.jsonl"))).unwrap();let values:Vec<serde_json::Value>=raw.lines().map(|line|serde_json::from_str(line).unwrap()).collect();
    assert_eq!(values.iter().filter(|v|v["down"]==true).count(),1);assert_eq!(values.iter().filter(|v|v["down"]==false).count(),0);
    println!("PASS: original exact Local executor reversal reproduces successful mock Down lost with dead publisher and no Up; no desktop input");
}
