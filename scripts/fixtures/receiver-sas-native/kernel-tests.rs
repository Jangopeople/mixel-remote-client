// This module is appended after the complete byte-identical production leaf.
#[cfg(test)]
mod kernel_controls {
    use super::*;
    use std::{os::windows::io::AsRawHandle, process::{Child,Command}, time::{Duration,Instant,SystemTime,UNIX_EPOCH}};
    #[link(name="kernel32")]
    extern "system" {
        fn CreateEventW(attributes: Handle, manual: i32, initial: i32, name: *const u16) -> Handle;
        fn CreateMutexW(attributes: Handle, initial_owner: i32, name: *const u16) -> Handle;
        fn SetEvent(event: Handle) -> i32;
        fn GetLastError() -> u32;
        fn CreateNamedPipeW(name: *const u16, access: u32, mode: u32, instances: u32, out_size: u32, in_size: u32, timeout: u32, attributes: Handle) -> Handle;
        fn ConnectNamedPipe(pipe: Handle, overlapped: Handle) -> i32;
    }
    fn event(initial: bool) -> Owned {
        Owned::new(unsafe { CreateEventW(ptr::null_mut(),1,initial as i32,ptr::null()) }).expect("create own unnamed event")
    }
    fn mutex(owned: bool) -> Owned {
        Owned::new(unsafe { CreateMutexW(ptr::null_mut(),owned as i32,ptr::null()) }).expect("create own unnamed mutex")
    }
    struct ChildOwner(Child);
    impl Drop for ChildOwner {
        fn drop(&mut self) {
            if !matches!(self.0.try_wait(),Ok(Some(_))) { let _=self.0.kill(); }
            let _=self.0.wait();
        }
    }
    fn actor(mode: &str, extra: Option<&str>) -> ChildOwner {
        let exe=std::env::var_os("SAS_COMPONENT_ACTOR").expect("Root must bind owned component actor path");
        let mut command=Command::new(exe);command.arg(mode);
        if let Some(value)=extra { command.arg(value); }
        ChildOwner(command.spawn().expect("spawn only own component actor"))
    }
    fn finish(child: &mut ChildOwner) {
        assert_eq!(unsafe { WaitForSingleObject(child.0.as_raw_handle().cast(),5000) },OBJECT,"own component actor deadline");
        assert!(child.0.wait().expect("reap own component actor").success());
    }
    #[test]
    fn whole_leaf_api_is_linked_but_never_invoked() {
        let _request = request;
        let _capture = Captured::capture;
        let _dispatch = Captured::dispatch;
        // Deliberately no call: neither successful service authority nor SendSAS
        // delivery is simulated by this component harness.
    }
    #[test]
    fn readonly_duplicate_refuses_event_modification_and_retains_object() {
        let original=event(false);
        let copy=duplicate(unsafe { GetCurrentProcess() },original.0,SYNC).expect("read-only duplicate");
        assert_eq!(unsafe { SetEvent(copy.0) },0);
        assert_eq!(unsafe { GetLastError() },5,"read-only event duplicate cannot acquire modification rights");
        assert_eq!(unsafe { WaitForSingleObject(copy.0,0) },TIMEOUT);
        assert_ne!(unsafe { SetEvent(original.0) },0);
        drop(original);
        assert_eq!(unsafe { WaitForSingleObject(copy.0,0) },OBJECT,"duplicate retains exact original kernel object after source close");
    }
    #[test]
    fn process_handle_without_dup_rights_is_refused() {
        let original=event(false);
        let limited=Owned::new(unsafe { OpenProcess(SYNC|QUERY,0,GetCurrentProcessId()) }).expect("own query-only process handle");
        assert!(duplicate(limited.0,original.0,SYNC).is_none(),"no PROCESS_DUP_HANDLE means no import");
        assert_eq!(unsafe { GetLastError() },5);
        assert!(duplicate(unsafe { GetCurrentProcess() },ptr::null_mut(),SYNC).is_none());
        assert!(duplicate(unsafe { GetCurrentProcess() },(-1isize) as Handle,SYNC).is_none());
    }
    #[test]
    fn revoked_generation_is_real_signaled_event() {
        let source=event(false);
        let copy=duplicate(unsafe { GetCurrentProcess() },source.0,SYNC).expect("read-only generation event");
        let sender=Sender { revoked:copy,effect:mutex(false) };
        assert!(sender.active());
        assert_ne!(unsafe { SetEvent(source.0) },0);
        assert!(!sender.active(),"actual signaled revoke denies generation");
        // request/dispatch are never called with this constructed negative fixture.
    }
    #[test]
    fn owned_child_death_is_observed_through_retained_kernel_handle() {
        let stamp=SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let name=format!("Local\\Mixel-SAS-Component-Exit-{}-{}",unsafe { GetCurrentProcessId() },stamp);
        let release=Owned::new(unsafe { CreateEventW(ptr::null_mut(),1,0,wide(&name).as_ptr()) }).expect("own child release event");
        let mut child=actor("exit-event",Some(&name));
        let retained=duplicate(unsafe { GetCurrentProcess() },child.0.as_raw_handle().cast(),SYNC|QUERY).expect("retain own child process");
        assert!(alive(retained.0),"component child waits for parent before exiting");
        let born=birth(retained.0).expect("actual owned child creation time");assert_ne!(born,0);
        assert_ne!(unsafe { SetEvent(release.0) },0);
        finish(&mut child);
        assert!(!alive(retained.0),"dead publisher cannot satisfy production lifetime check");
        assert_eq!(birth(retained.0),Some(born));
    }
    #[test]
    fn bounded_mutex_wait_is_real_and_release_stays_on_owner_thread() {
        assert_eq!(WAIT_MS,1000);
        let original=mutex(true);
        struct Release<'a>(&'a Owned);
        impl Drop for Release<'_> { fn drop(&mut self) { assert_ne!(unsafe { ReleaseMutex(self.0.0) },0); } }
        let _release=Release(&original);
        let raw=original.0 as usize;
        let started=Instant::now();
        let outcome=std::thread::spawn(move || {
            let copy=duplicate(unsafe { GetCurrentProcess() },raw as Handle,SYNC).expect("thread read-only mutex duplicate");
            unsafe { WaitForSingleObject(copy.0,WAIT_MS) }
        }).join().expect("bounded owned thread finished");
        assert_eq!(outcome,TIMEOUT,"other thread cannot acquire generation serialization");
        assert!(started.elapsed()>=Duration::from_millis(900));
        assert!(started.elapsed()<Duration::from_secs(10),"finite1000ms wait must not hang");
    }
    #[test]
    fn real_foreign_pipe_pid_refuses_retained_server_authority() {
        let stamp=SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let name=format!("\\\\.\\pipe\\Mixel-SAS-Component-{}-{}",unsafe { GetCurrentProcessId() },stamp);
        // Byte pipe, nonblocking connect only; no public ports/global objects.
        let pipe=Owned::new(unsafe { CreateNamedPipeW(wide(&name).as_ptr(),3,1,1,1024,1024,0,ptr::null_mut()) }).expect("create own private component pipe");
        let release_name=format!("Local\\{}-release",name.strip_prefix("\\\\.\\pipe\\").unwrap());
        let release=Owned::new(unsafe { CreateEventW(ptr::null_mut(),1,0,wide(&release_name).as_ptr()) }).expect("own pipe actor release event");
        let connect_name=format!("Local\\{}-connect",name.strip_prefix("\\\\.\\pipe\\").unwrap());
        let connect=Owned::new(unsafe { CreateEventW(ptr::null_mut(),1,0,wide(&connect_name).as_ptr()) }).expect("own pipe actor connect event");
        let mut child=actor("pipe",Some(&name));
        // A successful first NOWAIT call merely makes the pipe available. The
        // actor is deliberately held before CreateFileW, so this cannot be a
        // connection acknowledgment on any scheduler.
        let initial=unsafe { ConnectNamedPipe(pipe.0,ptr::null_mut()) };
        let initial_error=if initial==0 { unsafe { GetLastError() } } else { 0 };
        assert!(initial!=0 || initial_error==536,"initial own pipe availability {}",initial_error);
        assert_eq!(pipe_pid(pipe.0,false),None,"initial pipe availability is not an actual connected peer");
        assert_ne!(unsafe { SetEvent(connect.0) },0);
        let deadline=Instant::now()+Duration::from_secs(3);
        loop {
            let ok=unsafe { ConnectNamedPipe(pipe.0,ptr::null_mut()) };
            let error=if ok==0 { unsafe { GetLastError() } } else { 0 };
            if error==535 && pipe_pid(pipe.0,false)==Some(child.0.id()) { break; }
            assert!(ok!=0 || error==535 || error==536 || error==232,"unexpected own component pipe error {}",error);
            assert!(child.0.try_wait().expect("query exact owned pipe actor").is_none(),"owned pipe actor exited before connection");
            assert!(Instant::now()<deadline,"own pipe actor connect deadline");
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(pipe_pid(pipe.0,false),Some(child.0.id()),"kernel reports actual foreign child, not request metadata");
        let own=unsafe { GetCurrentProcess() };
        let pid=unsafe { GetCurrentProcessId() };let born=birth(own).unwrap();
        let owner=format!("{}-{}-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",pid,born);
        let revoke=event(false);let effect=mutex(false);
        let target=session(pid).filter(|s|*s!=0).unwrap_or(1);
        assert!(Captured::capture(pipe.0,&owner,1,revoke.0 as usize as u64,effect.0 as usize as u64,own,target).is_err(),"foreign actual pipe client cannot impersonate retained launched server");
        assert_ne!(unsafe { SetEvent(release.0) },0);
        drop(pipe);finish(&mut child);
    }
}
