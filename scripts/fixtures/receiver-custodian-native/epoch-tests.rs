// Appended only after the entire production epoch leaf.
#[cfg(test)] pub(super) fn custody_epoch_decisions(){
    for mask in [0x40,0x40000,0x80000,0x10000000]{assert!(!safe_parent_ace(0,0,mask,"S-1-5-11"));assert!(safe_parent_ace(0,0,mask,"S-1-5-18"));}
    assert!(!safe_parent_ace(0,0,0x40000,"S-1-5-21-1000"));assert!(!safe_parent_ace(7,0,0,"S-1-5-18"));
    let peer=own_peer().unwrap();let mut epoch=Epoch::acquire(&peer).expect("real physical logon, secure ancestor/namespace and durable marker");
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
