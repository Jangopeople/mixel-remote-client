// Appended only after the entire production epoch leaf.
#[cfg(test)] pub(super) fn custody_epoch_decisions(){
    for mask in [0x40,0x40000,0x80000,0x10000000]{assert!(!safe_parent_ace(0,0,mask,"S-1-5-11"));assert!(safe_parent_ace(0,0,mask,"S-1-5-18"));}
    assert!(!safe_parent_ace(0,0,0x40000,"S-1-5-21-1000"));assert!(!safe_parent_ace(7,0,0,"S-1-5-18"));
    let peer=own_peer().unwrap();let mut epoch=Epoch::acquire(&peer).expect("real physical logon, secure ancestor/namespace and durable marker");
    let namespace=epoch._directories.last().expect("retained exact namespace handle");
    assert!(exact_sd(namespace.raw(),1,ROOT_SD));
    for wrong in [ROOT_SD.replace("0x00000002;;;AU","0x00000006;;;AU"),
                  ROOT_SD.replace(";;;ME)",";;;HI)"),
                  ROOT_SD.replace("D:P(","D:("),
                  ROOT_SD.replace("S:AI(","S:(")] {
        assert!(!exact_sd(namespace.raw(),1,&wrong),"changed namespace permissions, label or protection must refuse");
    }
    println!("PASS: exact NTFS namespace metadata; widened rights, changed label and protection refuse without ACL writes");
    assert!(exact_sd(epoch.mutex.raw(),6,MUTEX_SD));
    let wrong_mutex=MUTEX_SD.replace("0x00120000;;;AU","0x00120001;;;AU");
    assert!(!exact_sd(epoch.mutex.raw(),6,&wrong_mutex),"additional unprivileged mutex rights must refuse");
    let sid=process_sid(unsafe{GetCurrentProcess()}).unwrap();
    let marker_sd=format!("D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x0013019f;;;{sid})(A;;RC;;;OW)S:AI(ML;;NW;;;ME)");
    let marker=epoch.marker.as_ref().expect("retained exact dirty-marker handle");
    assert!(exact_sd(marker.raw(),1,&marker_sd));
    assert!(!exact_sd(marker.raw(),1,&marker_sd.replace("0x0013019f","0x0017019f")),"marker DACL takeover rights must refuse");
    assert!(!exact_sd(marker.raw(),1,&marker_sd.replace(";;;ME)",";;;HI)")),"changed marker label must refuse");
    println!("PASS: exact materialized mutex and marker descriptors; wider rights and changed labels refuse");
    let other=peer.clone();let blocked=std::thread::spawn(move||Epoch::acquire(&other).is_err()).join().unwrap();assert!(blocked,"same physical local/portable session cannot overlap");
    assert!(epoch.finish());let mut next=Epoch::acquire(&peer).expect("true cleanup retirement permits a fresh physical epoch");assert!(next.finish());
    println!("PASS: real physical-session shared fence and exact ACL policy; no input or SAS effect");
}
#[cfg(test)] pub(super) fn qa_remove_owned_namespace(){
    use std::os::windows::fs::MetadataExt;
    let nonce=QA_NAMESPACE.get().expect("only owned QA namespace cleanup");assert_eq!(nonce.len(),32);
    let path=program_data().unwrap().join(root_name());let metadata=std::fs::symlink_metadata(&path).unwrap();assert_eq!(metadata.file_attributes()&0x400,0);
    let mut count=0;for entry in std::fs::read_dir(&path).unwrap(){let entry=entry.unwrap();count+=1;assert!(count<=4096);let name=entry.file_name();let name=name.to_str().unwrap();assert!(name.starts_with("session-")&&name.ends_with(".dirty"));let metadata=std::fs::symlink_metadata(entry.path()).unwrap();assert!(metadata.is_file());assert_eq!(metadata.file_attributes()&0x400,0);std::fs::remove_file(entry.path()).unwrap();}
    std::fs::remove_dir(&path).unwrap();
    println!("PASS: only fresh cfg(test) mock-effects namespace removed after retained fixture actors exited; production fixed namespace untouched");
}
