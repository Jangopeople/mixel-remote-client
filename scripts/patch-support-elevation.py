#!/usr/bin/env python3
"""Make Quick Support elevation work: the approved session survives the helper
switch, the helper that exists after Windows consent is accepted, and the time a
person needs to answer the consent prompt no longer counts against the helper.

Applies to the fully patched 1.4.6 support tree (after patch-support-input-lifecycle.py).
Usage: patch-support-elevation.py <rustdesk-root>
"""
from __future__ import annotations

import sys
from pathlib import Path

INPUT = "src/server/mixel_receiver_input.rs"
WINDOWS = "src/server/mixel_receiver_input_windows.rs"
IPC = "src/ipc.rs"
PORTABLE = "src/server/portable_service.rs"
CONNECTION = "src/server/connection.rs"
CORE = "src/core_main.rs"

CHANGES: list[tuple[str, str, str]] = [
    # --- the session's approval outlives a backend change -------------------
    (INPUT,
     "struct State { alive: bool, authorized: bool, permission_epoch:u64,",
     "struct State { alive: bool, authorized: bool, input_wanted: bool, rebind_tries: u32, permission_epoch:u64,"),
    (INPUT,
     "failed: Arc<AtomicBool>, alive: AtomicBool, owners: AtomicUsize }\nstruct Work",
     "failed: Arc<AtomicBool>, alive: AtomicBool, owners: AtomicUsize, rebind: AtomicBool }\nstruct Work"),
    (INPUT,
     "State { alive: true, authorized: false, permission_epoch:0,",
     "State { alive: true, authorized: false, input_wanted: false, rebind_tries: 0, permission_epoch:0,"),
    (INPUT,
     "alive: AtomicBool::new(true), owners: AtomicUsize::new(1) });",
     "alive: AtomicBool::new(true), owners: AtomicUsize::new(1), rebind: AtomicBool::new(false) });"),
    (INPUT,
     "self.active.store(false,Ordering::SeqCst); self.failure.store(true,Ordering::SeqCst); let _ = self.kernel.revoke(); return false;",
     "self.active.store(false,Ordering::SeqCst); self.request_rebind(); let _ = self.kernel.revoke(); return false;"),
    (INPUT,
     "    fn try_retired(self: &Arc<Self>) -> bool {\n",
     "    // A helper start or stop ends this scope, never the person's approval.\n"
     "    // The owner opens a fresh scope on whichever backend is current then;\n"
     "    // this scope keeps its own ledger and is retired through it.\n"
     "    #[cfg(windows)]\n"
     "    fn request_rebind(&self) {\n"
     "        match self.owner.upgrade() { Some(owner) => owner.rebind.store(true,Ordering::SeqCst), None => self.failure.store(true,Ordering::SeqCst) }\n"
     "    }\n"
     "    fn retire_nowait(self: &Arc<Self>) -> bool {\n"
     "        self.deny();\n"
     "        if let Some(registry) = generations() {\n"
     "            registry.lock().unwrap_or_else(|e| e.into_inner()).insert(self.token.clone(), Slot::Retiring(self.clone()));\n"
     "        }\n"
     "        self.try_retired()\n"
     "    }\n"
     "    fn try_retired(self: &Arc<Self>) -> bool {\n"),
    (INPUT,
     "          state.authorized = true;\n"
     "          if state.cursor.is_none() { state.cursor = Generation::new(&self.inner); }\n"
     "          if state.cursor.is_none() { return false; }\n",
     "          state.authorized = true;\n"
     "          // Approved while the helper is changing: scopes open when it is settled.\n"
     "          if self.inner.rebind.load(Ordering::SeqCst) || helper_transition_active() {\n"
     "              state.input_wanted = enabled; self.inner.rebind.store(true, Ordering::SeqCst); return true;\n"
     "          }\n"
     "          if state.cursor.is_none() { state.cursor = Generation::new(&self.inner); }\n"
     "          if state.cursor.is_none() {\n"
     "              if helper_transition_active() { state.input_wanted = enabled; self.inner.rebind.store(true, Ordering::SeqCst); return true; }\n"
     "              return false;\n"
     "          }\n"),
    (INPUT,
     "            if enabled && state.generation.is_some() { return true; }\n"
     "            let Some(epoch)=state.permission_epoch.checked_add(1)else{return false;};state.permission_epoch=epoch;\n",
     "            state.input_wanted = enabled;\n"
     "            if self.inner.rebind.load(Ordering::SeqCst) {\n"
     "                // Every scope is already denied; the pending rebind applies this choice.\n"
     "                let Some(epoch)=state.permission_epoch.checked_add(1)else{return false;};state.permission_epoch=epoch;\n"
     "                return true;\n"
     "            }\n"
     "            if enabled && state.generation.is_some() { return true; }\n"
     "            let Some(epoch)=state.permission_epoch.checked_add(1)else{return false;};state.permission_epoch=epoch;\n"),
    (INPUT,
     "        if state.generation.is_none(){state.generation=Generation::new(&self.inner);}\n"
     "        state.generation.is_some()\n"
     "    }\n",
     "        if state.generation.is_none(){state.generation=Generation::new(&self.inner);}\n"
     "        if state.generation.is_none() && helper_transition_active() { self.inner.rebind.store(true, Ordering::SeqCst); return true; }\n"
     "        state.generation.is_some()\n"
     "    }\n"
     "    /// Once a second from the connection: after a helper start, stop or refusal,\n"
     "    /// retire the scopes of the previous backend and open fresh ones on the\n"
     "    /// current backend. Input is simply not delivered in between. `false` means\n"
     "    /// the input owner could not be restored and the connection must end.\n"
     "    pub(crate) fn rebind_tick(&self) -> bool {\n"
     "        if !self.inner.rebind.load(Ordering::SeqCst) { return true; }\n"
     "        if self.failed() { return false; }\n"
     "        let waiting = helper_transition_active();\n"
     "        let pending = {\n"
     "            let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());\n"
     "            if !state.alive { return true; }\n"
     "            if let Some(scope) = state.cursor.take() { scope.deny(); state.retiring.push(scope); }\n"
     "            if let Some(scope) = state.generation.take() { scope.deny(); state.retiring.push(scope); }\n"
     "            #[cfg(windows)] for index in 0..2 { if let Some(scope) = state.portable_blocks[index].take() { scope.deny(); state.retiring.push(scope); } }\n"
     "            let Some(epoch) = state.permission_epoch.checked_add(1) else { drop(state); self.fail(); return false; };\n"
     "            state.permission_epoch = epoch;\n"
     "            // The consent prompt is the person's time. Only settling counts.\n"
     "            if !waiting { state.rebind_tries += 1; if state.rebind_tries > 60 { drop(state); self.fail(); return false; } }\n"
     "            state.retiring.clone()\n"
     "        };\n"
     "        if waiting { return true; }\n"
     "        let retired: Vec<_> = pending.into_iter().filter(|scope| scope.retire_nowait()).collect();\n"
     "        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());\n"
     "        state.retiring.retain(|scope| !retired.iter().any(|done| Arc::ptr_eq(scope, done)));\n"
     "        // A fresh grant never rearms a previous lease: wait for every old receipt.\n"
     "        if !state.retiring.is_empty() || !state.alive { return true; }\n"
     "        // Cleared first: a backend change from here on asks for the next rebind.\n"
     "        self.inner.rebind.store(false, Ordering::SeqCst);\n"
     "        if !state.authorized { state.rebind_tries = 0; return true; }\n"
     "        let Some(cursor) = Generation::new(&self.inner) else { self.inner.rebind.store(true, Ordering::SeqCst); return true; };\n"
     "        let generation = if state.input_wanted {\n"
     "            let Some(generation) = Generation::new(&self.inner) else {\n"
     "                cursor.deny(); state.retiring.push(cursor); self.inner.rebind.store(true, Ordering::SeqCst); return true;\n"
     "            };\n"
     "            Some(generation)\n"
     "        } else { None };\n"
     "        state.cursor = Some(cursor); state.generation = generation; state.rebind_tries = 0;\n"
     "        true\n"
     "    }\n"),
    (INPUT,
     "            work.inner.failed.store(true, Ordering::SeqCst); let _ = work.generation.revoke(); return;\n"
     "        }\n"
     "        // SAS retains",
     "            work.inner.rebind.store(true, Ordering::SeqCst); let _ = work.generation.retire_nowait(); return;\n"
     "        }\n"
     "        // SAS retains"),
    (INPUT,
     "        generation.failure.store(true,Ordering::SeqCst); generation.deny();\n"
     "        if let Some(owner) = generation.owner.upgrade() { owner.alive.store(false,Ordering::SeqCst); owners.insert(owner.control.clone(),owner); }\n",
     "        // Every scope of the old backend ends here. The approval does not: its\n"
     "        // owner rebinds once the helper is confirmed, refused or timed out.\n"
     "        generation.deny();\n"
     "        if let Some(owner) = generation.owner.upgrade() { owner.rebind.store(true,Ordering::SeqCst); owners.insert(owner.control.clone(),owner); }\n"),
    (INPUT,
     "#[cfg(windows)]\nstatic TRANSITION: AtomicBool = AtomicBool::new(false);\n",
     "#[cfg(windows)]\nstatic TRANSITION: AtomicBool = AtomicBool::new(false);\n"
     "/// True from the moment a helper start was requested until that helper is\n"
     "/// confirmed, refused, or timed out and the previous backend is restored.\n"
     "pub(crate) fn helper_transition_active() -> bool {\n"
     "    #[cfg(windows)] { TRANSITION.load(Ordering::SeqCst) }\n"
     "    #[cfg(not(windows))] { false }\n"
     "}\n"),
    (INPUT,
     "pub(crate) use windows::{block_reply, PortableReply, pipe_peer, pipe_publisher,",
     "pub(crate) use windows::{block_reply, PortableReply, pipe_peer, pipe_publisher, pipe_helper, lend_helper,"),

    # --- the helper after Windows consent is a successor process -------------
    (WINDOWS,
     "    fn TerminateProcess(process: Handle, code: u32) -> i32;\n",
     "    fn TerminateProcess(process: Handle, code: u32) -> i32;\n"
     "    fn GetExitCodeProcess(process: Handle, code: *mut u32) -> i32;\n"),
    (WINDOWS,
     "struct Started { peer: Peer, publisher: Option<u64>, confirmed: Arc<AtomicUsize> }",
     "struct Started { peer: Peer, publisher: Option<u64>, confirmed: Arc<AtomicUsize>, successor: Option<Peer> }\n"
     "const SYSTEM_SID: &str = \"S-1-5-18\";\n"
     "/// Helper side. The consented helper runs in another security context, so the\n"
     "/// broker cannot open it. It lends the broker a limited (synchronize, limited\n"
     "/// query) handle to itself; nothing else is granted by this.\n"
     "pub(crate) fn lend_helper(pipe: Handle) -> Option<u64> {\n"
     "    let pid = pipe_pid(pipe,true)?;\n"
     "    let broker = Owned::from(unsafe { OpenProcess(0x40 | 0x1000,0,pid) })?;\n"
     "    if image(broker.raw())? != image(unsafe { GetCurrentProcess() })? { return None; }\n"
     "    let mut lent = ptr::null_mut();\n"
     "    if unsafe { DuplicateHandle(GetCurrentProcess(),GetCurrentProcess(),broker.raw(),&mut lent,SYNC|0x1000,0,0) } == 0 { return None; }\n"
     "    Some(lent as usize as u64)\n"
     "}\n"
     "/// Broker side. Accept the one successor of a consented start that is still\n"
     "/// open: the kernel pipe binds its PID and session, the lent handle must name\n"
     "/// that PID and this same image. It is only ever granted the SYSTEM identity\n"
     "/// on the leases, so anything that is not the SYSTEM helper fails closed.\n"
     "pub(crate) fn pipe_helper(pipe: Handle, lent: u64) -> Option<Peer> {\n"
     "    let pid = pipe_pid(pipe,false)?;\n"
     "    let session = pipe_session(pipe,false)?;\n"
     "    let raw = usize::try_from(lent).ok()? as Handle;\n"
     "    if raw.is_null() || unsafe { GetProcessId(raw) } != pid { return None; }\n"
     "    // Inspect borrowed first. Never close an unvalidated packet-supplied handle.\n"
     "    if birth(raw).is_none() || image(raw)? != image(unsafe { GetCurrentProcess() })? { return None; }\n"
     "    let mut startup = started().lock().ok()?;\n"
     "    let attempt = startup.as_mut()?;\n"
     "    if !TRANSITION.load(Ordering::SeqCst) || attempt.confirmed.load(Ordering::SeqCst) != 0 || attempt.successor.is_some() { return None; }\n"
     "    let peer = verified_peer_session(pid,Arc::new(Owned::from(raw)?),SYSTEM_SID.to_owned(),session)?;\n"
     "    attempt.successor = Some(peer.clone());\n"
     "    *helper().lock().ok()? = Some(peer.clone());\n"
     "    Some(peer)\n"
     "}"),
    (WINDOWS,
     "    let approved={let startup=started().lock().unwrap_or_else(|e|e.into_inner());startup.as_ref().filter(|s|s.peer.pid==peer.pid&&s.peer.born==peer.born).map(|s|s.confirmed.clone())};\n"
     "    let Some(confirmed)=approved else{return false;};\n"
     "    let state=attempt().lock().unwrap_or_else(|e|e.into_inner());\n"
     "    if state.retired.contains_key(&identity(peer))||state.recover{return false;}\n"
     "    if state.candidate.as_ref().map(|p|unsafe{GetProcessId(p.raw())}!=peer.pid||birth(p.raw())!=Some(peer.born)).unwrap_or(true){return false;}\n",
     "    let approved={let startup=started().lock().unwrap_or_else(|e|e.into_inner());startup.as_ref().and_then(|s|{\n"
     "        let direct=s.peer.pid==peer.pid&&s.peer.born==peer.born;\n"
     "        let successor=s.successor.as_ref().map(|v|v.pid==peer.pid&&v.born==peer.born).unwrap_or(false);\n"
     "        (direct||successor).then(||(s.confirmed.clone(),direct))})};\n"
     "    let Some((confirmed,direct))=approved else{return false;};\n"
     "    let state=attempt().lock().unwrap_or_else(|e|e.into_inner());\n"
     "    if state.retired.contains_key(&identity(peer))||state.recover{return false;}\n"
     "    // The directly launched process must be the candidate itself. Its consented\n"
     "    // successor was bound to this attempt when its pipe was accepted.\n"
     "    if direct&&state.candidate.as_ref().map(|p|unsafe{GetProcessId(p.raw())}!=peer.pid||birth(p.raw())!=Some(peer.born)).unwrap_or(true){return false;}\n"
     "    if !direct&&state.candidate.is_none(){return false;}\n"),
    (WINDOWS,
     "    if startup.as_ref().map(|s|s.peer.alive()).unwrap_or(false){return None;}\n",
     "    if startup.as_ref().map(|s|s.peer.alive()||s.successor.as_ref().map(Peer::alive).unwrap_or(false)).unwrap_or(false){return None;}\n"),
    (WINDOWS,
     "        let deadline=std::time::Instant::now()+Duration::from_secs(10);\n"
     "        while watch.load(Ordering::SeqCst)==0&&std::time::Instant::now()<deadline&&unsafe{WaitForSingleObject(owned.raw(),0)}==TIMEOUT{std::thread::sleep(Duration::from_millis(10));}\n",
     "        // The launched process only asks Windows for consent, then exits; the\n"
     "        // helper is its successor. Answering the prompt is the person's time\n"
     "        // (Windows itself gives up after two minutes), so the helper's own\n"
     "        // deadline starts when the launched process has finished successfully.\n"
     "        let consent=std::time::Instant::now()+Duration::from_secs(150);\n"
     "        while watch.load(Ordering::SeqCst)==0&&std::time::Instant::now()<consent&&unsafe{WaitForSingleObject(owned.raw(),0)}==TIMEOUT{std::thread::sleep(Duration::from_millis(10));}\n"
     "        let mut code=1u32;\n"
     "        let granted=unsafe{WaitForSingleObject(owned.raw(),0)}!=TIMEOUT&&unsafe{GetExitCodeProcess(owned.raw(),&mut code)}!=0&&code==0;\n"
     "        let deadline=std::time::Instant::now()+Duration::from_secs(if granted{20}else{0});\n"
     "        while watch.load(Ordering::SeqCst)==0&&std::time::Instant::now()<deadline{std::thread::sleep(Duration::from_millis(10));}\n"),
    (WINDOWS,
     "    *startup=Some(Started{peer,publisher:Some(publisher as usize as u64),confirmed});",
     "    *startup=Some(Started{peer,publisher:Some(publisher as usize as u64),confirmed,successor:None});"),

    (IPC,
     "    ReceiverPublisherHandle(Option<u64>),\n",
     "    ReceiverPublisherHandle(Option<u64>),\n    ReceiverHelperHandle(Option<u64>),\n"),
    (IPC,
     "    pub(crate) fn receiver_pipe_publisher(&self, transferred: Option<u64>)",
     "    pub(crate) fn receiver_pipe_helper(&self, lent: u64) -> Option<crate::server::mixel_receiver_input::Peer> {\n"
     "        crate::server::mixel_receiver_input::pipe_helper(self.inner.get_ref().as_raw_handle().cast(), lent)\n"
     "    }\n"
     "    pub(crate) fn receiver_lend_helper(&self) -> Option<u64> {\n"
     "        crate::server::mixel_receiver_input::lend_helper(self.inner.get_ref().as_raw_handle().cast())\n"
     "    }\n"
     "    pub(crate) fn receiver_pipe_publisher(&self, transferred: Option<u64>)"),

    (PORTABLE,
     "            Ok(mut stream) => {\n"
     "                let transferred = match stream.next_timeout(1000).await {\n",
     "            Ok(mut stream) => {\n"
     "                // Identify to the broker first; it cannot open an elevated helper.\n"
     "                let lent = stream.receiver_lend_helper();\n"
     "                if stream.send(&Data::DataPortableService(ReceiverHelperHandle(lent))).await.is_err() { return; }\n"
     "                let transferred = match stream.next_timeout(3000).await {\n"),
    (PORTABLE,
     "                                        let Some(receiver_peer) = stream.receiver_pipe_peer(false) else { return; };\n",
     "                                        let lent = match stream.next_timeout(3000).await {\n"
     "                                            Ok(Some(Data::DataPortableService(ReceiverHelperHandle(lent)))) => lent,\n"
     "                                            _ => return,\n"
     "                                        };\n"
     "                                        let receiver_peer = match lent { Some(lent) => stream.receiver_pipe_helper(lent), None => stream.receiver_pipe_peer(false) };\n"
     "                                        let Some(receiver_peer) = receiver_peer else { log::error!(\"Portable helper identity refused\"); return; };\n"),
    (PORTABLE,
     "        if RUNNING.lock().unwrap().clone() {\n"
     "            bail!(\"already running\");\n"
     "        }\n"
     "        if SHMEM.lock().unwrap().is_none() {\n",
     "        if RUNNING.lock().unwrap().clone() {\n"
     "            bail!(\"already running\");\n"
     "        }\n"
     "        // A second request must not reset the shared memory of the first.\n"
     "        if crate::server::mixel_receiver_input::helper_transition_active() {\n"
     "            bail!(\"Elevation is already in progress\");\n"
     "        }\n"
     "        if SHMEM.lock().unwrap().is_none() {\n"),

    (CONNECTION,
     "                    if conn.receiver_input.failed() { conn.on_close(\"Input queue capacity or helper trust failed\", false).await; break; }\n",
     "                    if conn.receiver_input.failed() { conn.on_close(\"Input queue capacity or helper trust failed\", false).await; break; }\n"
     "                    #[cfg(not(any(target_os = \"android\", target_os = \"ios\")))]\n"
     "                    if !conn.receiver_input.rebind_tick() { conn.on_close(\"Input could not be restored after the helper changed\", false).await; break; }\n"),

    (CORE,
     "                crate::platform::elevate_or_run_as_system(\n"
     "                    click_setup,\n"
     "                    _is_elevate,\n"
     "                    _is_run_as_system,\n"
     "                );\n"
     "                return None;\n",
     "                crate::platform::elevate_or_run_as_system(\n"
     "                    click_setup,\n"
     "                    _is_elevate,\n"
     "                    _is_run_as_system,\n"
     "                );\n"
     "                // Still here: Windows consent was refused or failed. Say so to the\n"
     "                // waiting broker at once (a granted start has exited with 0 above).\n"
     "                std::process::exit(1);\n"),
]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    texts: dict[str, str] = {}
    crlf: dict[str, bool] = {}
    changed: set[str] = set()
    for name, old, new in CHANGES:
        if name not in texts:
            raw = (root / name).read_bytes().decode("utf-8")
            crlf[name] = "\r\n" in raw
            texts[name] = raw.replace("\r\n", "\n")
        text = texts[name]
        if new in text:
            continue  # already applied
        if text.count(old) != 1:
            print(f"REFUSED {name}: expected exactly one match, found {text.count(old)}: {old[:70]!r}")
            return 1
        texts[name] = text.replace(old, new)
        changed.add(name)
    for name in sorted(changed):
        text = texts[name].replace("\n", "\r\n") if crlf[name] else texts[name]
        (root / name).write_bytes(text.encode("utf-8"))
        print(f"patched {name}")
    if not changed:
        print("already applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
