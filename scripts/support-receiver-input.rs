//! Receiver-owned input capabilities. Remote FIFO work never owns permission.
//! Revocation publishes deny before it waits for an in-flight synchronous effect.
use std::{collections::HashMap, sync::{Arc, Mutex, OnceLock, atomic::{AtomicBool, AtomicI32, AtomicUsize, Ordering}, mpsc}, time::Duration};
use hbb_common::{message_proto::{KeyEvent, MouseEvent, PointerDeviceEvent}, protobuf::Message as _};
use super::input_service;

const MAX_PENDING: usize = 128;
const MAX_BYTES: usize = 262_144;
static NEXT_EFFECT: AtomicI32 = AtomicI32::new(-1);

#[derive(Clone)]
pub(crate) enum Action {
    Mouse(MouseEvent, String, u32, bool, bool),
    Key(KeyEvent, bool),
    Pointer(PointerDeviceEvent),
}
impl Action {
    fn size(&self) -> usize { match self {
        Self::Mouse(v, name, ..) => v.compute_size() as usize + name.len(),
        Self::Key(v, _) => v.compute_size() as usize,
        Self::Pointer(v) => v.compute_size() as usize,
    }}
}
struct Generation {
    token: String,
    effect_id: i32,
    conn: i32,
    failure: Arc<AtomicBool>,
    active: AtomicBool,
    serial: Mutex<()>,
    cleanup_done: AtomicBool,
    cleanup_requested: AtomicBool,
    #[cfg(windows)]
    backend: windows::Backend,
    #[cfg(windows)]
    portable_used: AtomicUsize,
    #[cfg(windows)]
    kernel: windows::Lease,
}
impl Generation {
    fn new(conn: i32, failure: Arc<AtomicBool>) -> Option<Arc<Self>> {
        let registry = generations()?;
        let mut slots = registry.lock().ok()?;
        slots.retain(|_, slot| slot.get().is_some());
        if slots.len() >= 512 { return None; }
        let effect_id = NEXT_EFFECT.fetch_update(Ordering::SeqCst, Ordering::SeqCst, |v| v.checked_sub(1)).ok()?;
        let nonce = uuid::Uuid::new_v4().simple().to_string();
        #[cfg(windows)]
        let kernel = windows::Lease::create(&nonce)?;
        #[cfg(windows)]
        let backend = windows::Backend::current(&kernel)?;
        let generation = Arc::new(Self {
            #[cfg(windows)] token: kernel.token.clone(),
            #[cfg(not(windows))] token: nonce,
            effect_id, conn, failure, active: AtomicBool::new(true), serial: Mutex::new(()),
            cleanup_done: AtomicBool::new(false), cleanup_requested: AtomicBool::new(false),
            #[cfg(windows)] backend,
            #[cfg(windows)] portable_used: AtomicUsize::new(0),
            #[cfg(windows)] kernel,
        });
        slots.insert(generation.token.clone(), Slot::Active(Arc::downgrade(&generation)));
        Some(generation)
    }
    fn admitted(&self) -> bool {
        if !self.active.load(Ordering::SeqCst) { return false; }
        #[cfg(windows)] if !self.backend.matches_current() {
            self.active.store(false,Ordering::SeqCst); self.failure.store(true,Ordering::SeqCst); let _ = self.kernel.revoke(); return false;
        }
        true
    }
    fn try_retired(self: &Arc<Self>) -> bool {
        if self.active.load(Ordering::SeqCst) { return false; }
        schedule_cleanup(self.clone());
        if !self.cleanup_done.load(Ordering::SeqCst) { return false; }
        #[cfg(windows)] {
            if !self.kernel.try_retired() { return false; }
            if self.portable_used.load(Ordering::SeqCst) != 0 && !self.kernel.cleaned() { return false; }
        }
        true
    }
    fn deny(&self) -> bool {
        self.active.store(false,Ordering::SeqCst);
        #[cfg(windows)] { self.kernel.revoke() }
        #[cfg(not(windows))] { true }
    }
    fn revoke(self: &Arc<Self>) -> bool {
        // Deny never waits behind data, main dispatch, IPC, or cleanup queues.
        let denied = self.deny();
        if let Some(registry) = generations() {
            registry.lock().unwrap_or_else(|e| e.into_inner()).insert(self.token.clone(), Slot::Retiring(self.clone()));
        }
        if !denied { return false; }
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        loop {
            if self.try_retired() { return true; }
            if std::time::Instant::now() >= deadline { return false; }
            std::thread::sleep(Duration::from_millis(1));
        }
    }

}
enum Slot { Active(std::sync::Weak<Generation>), Retiring(Arc<Generation>) }
impl Slot { fn get(&self) -> Option<Arc<Generation>> { match self { Self::Active(v) => v.upgrade(), Self::Retiring(v) => Some(v.clone()) } } }
fn generations() -> Option<&'static Arc<Mutex<HashMap<String, Slot>>>> {
    static GENERATIONS: OnceLock<Option<Arc<Mutex<HashMap<String,Slot>>>>> = OnceLock::new();
    GENERATIONS.get_or_init(|| {
        let registry = Arc::new(Mutex::new(HashMap::<String,Slot>::new())); let worker = registry.clone();
        std::thread::Builder::new().name("mixel-input-retirement".to_owned()).spawn(move || loop {
            std::thread::sleep(Duration::from_millis(10));
            let candidates: Vec<_> = worker.lock().unwrap_or_else(|e| e.into_inner()).values().filter_map(Slot::get).collect();
            let retired: Vec<_> = candidates.into_iter().filter(|g| !g.active.load(Ordering::SeqCst) && g.try_retired()).map(|g| g.token.clone()).collect();
            let mut slots = worker.lock().unwrap_or_else(|e| e.into_inner());
            for token in retired { slots.remove(&token); }
            slots.retain(|_, slot| slot.get().is_some());
        }).ok().map(|_| registry)
    }).as_ref()
}
struct State { alive: bool, authorized: bool, generation: Option<Arc<Generation>>, cursor: Option<Arc<Generation>>, retiring: Vec<Arc<Generation>> }
struct Inner { state: Mutex<State>, pending: AtomicUsize, pending_bytes: AtomicUsize, control: String, conn: i32, failed: Arc<AtomicBool>, alive: AtomicBool, owners: AtomicUsize }
struct Work { inner: Arc<Inner>, generation: Arc<Generation>, action: Action, bytes: usize }
impl Drop for Work { fn drop(&mut self) { self.inner.pending.fetch_sub(1, Ordering::SeqCst); self.inner.pending_bytes.fetch_sub(self.bytes, Ordering::SeqCst); } }
pub(crate) struct Receiver { inner: Arc<Inner>, sender: mpsc::SyncSender<Work> }
impl Clone for Receiver {
    fn clone(&self) -> Self { self.inner.owners.fetch_add(1, Ordering::SeqCst); Self { inner: self.inner.clone(), sender: self.sender.clone() } }
}
impl Drop for Receiver {
    fn drop(&mut self) { if self.inner.owners.fetch_sub(1, Ordering::SeqCst) == 1 { let _ = self.close(); } }
}
impl Receiver {
    pub(crate) fn new(conn: i32) -> Self {
        let inner = Arc::new(Inner { state: Mutex::new(State { alive: true, authorized: false, generation: None, cursor: None, retiring: Vec::new() }), pending: AtomicUsize::new(0), pending_bytes: AtomicUsize::new(0), control: uuid::Uuid::new_v4().simple().to_string(), conn, failed: Arc::new(AtomicBool::new(false)), alive: AtomicBool::new(true), owners: AtomicUsize::new(1) });
        let (sender, receiver) = mpsc::sync_channel::<Work>(MAX_PENDING);
        if std::thread::Builder::new().name("mixel-receiver-input".to_owned()).spawn(move || while let Ok(work) = receiver.recv() { dispatch(work); }).is_err() {
            inner.failed.store(true, Ordering::SeqCst); inner.alive.store(false, Ordering::SeqCst);
            inner.state.lock().unwrap_or_else(|e| e.into_inner()).alive = false;
        }
        Self { inner, sender }
    }
    pub(crate) fn authorize(&self, enabled: bool) -> bool {
        { let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
          if !state.alive { return false; }
          state.authorized = true;
          if state.cursor.is_none() { state.cursor = Generation::new(self.inner.conn, self.inner.failed.clone()); }
          if state.cursor.is_none() { return false; }
        }
        self.set_permission(enabled)
    }
    pub(crate) fn failed(&self) -> bool { self.inner.failed.load(Ordering::SeqCst) }
    pub(crate) fn fail(&self) { self.inner.failed.store(true, Ordering::SeqCst); let _ = self.close(); }
    pub(crate) fn block(&self, enabled: bool, privacy: bool) -> bool {
        let state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        if !state.alive || !state.authorized { return false; }
        drop(state);
        set_block(&self.inner, enabled, privacy)
    }
    pub(crate) fn set_permission(&self, enabled: bool) -> bool {
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        if !state.alive || !state.authorized { return false; }
        if enabled == state.generation.is_some() { return true; }
        if let Some(previous) = state.generation.take() { previous.deny(); state.retiring.push(previous); }
        if !retire(&mut state) { state.alive = false; self.inner.alive.store(false,Ordering::SeqCst); return false; }
        if enabled { state.generation = Generation::new(self.inner.conn, self.inner.failed.clone()); state.generation.is_some() }
        else { set_block(&self.inner, false, false) }
    }
    pub(crate) fn close(&self) -> bool {
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        state.alive = false; self.inner.alive.store(false, Ordering::SeqCst);
        if let Some(cursor) = state.cursor.take() { state.retiring.push(cursor); }
        if let Some(previous) = state.generation.take() { state.retiring.push(previous); }
        // Publish every scope's deny before the first OS-unblock/cleanup wait.
        for generation in &state.retiring { generation.deny(); }
        let ordinary = set_block(&self.inner, false, false);
        let privacy = set_block(&self.inner, false, true);
        retire(&mut state) && ordinary && privacy
    }
    pub(crate) fn enqueue(&self, action: Action) {
        let state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        let cursor_only = matches!(&action, Action::Mouse(_, _, _, false, true));
        let scope = if cursor_only { &state.cursor } else { &state.generation };
        let Some(generation) = scope.as_ref().filter(|g| state.alive && state.authorized && g.active.load(Ordering::SeqCst)) else { return; };
        let bytes = action.size();
        let counted = self.inner.pending.fetch_update(Ordering::SeqCst, Ordering::SeqCst,
            |v| (v < MAX_PENDING).then_some(v + 1)).is_ok();
        if !counted { drop(state); self.fail(); return; }
        let budgeted = self.inner.pending_bytes.fetch_update(Ordering::SeqCst, Ordering::SeqCst,
            |v| v.checked_add(bytes).filter(|v| *v <= MAX_BYTES)).is_ok();
        if !budgeted { self.inner.pending.fetch_sub(1, Ordering::SeqCst); drop(state); self.fail(); return; }
        let work = Work { inner: self.inner.clone(), generation: generation.clone(), action, bytes };
        let sent = self.sender.try_send(work).is_ok();
        drop(state);
        // Losing an Up cannot leave a Down live: overflow retires the whole owner.
        if !sent { self.fail(); }
    }
}
fn retire(state: &mut State) -> bool {
    state.retiring.retain(|generation| {
        !generation.revoke()
    });
    state.retiring.is_empty()
}
fn dispatch(work: Work) {
    if !work.generation.active.load(Ordering::SeqCst) { return; }
    #[cfg(windows)] {
        // A generation owns exactly one actual native ledger. A helper start,
        // stop, or restart is an epoch boundary, never a direct fallback.
        if !work.generation.backend.matches_current() {
            work.inner.failed.store(true, Ordering::SeqCst); let _ = work.generation.revoke(); return;
        }
        if let windows::Backend::Portable(target) = &work.generation.backend {
            work.generation.portable_used.fetch_add(1, Ordering::SeqCst);
            if !super::portable_service::client::send_receiver_input(&work.generation.token, target, work.generation.conn, &work.action) {
                work.generation.portable_used.fetch_sub(1, Ordering::SeqCst);
                work.inner.failed.store(true, Ordering::SeqCst); let _ = work.generation.revoke();
            }
            return;
        }
        let generation = work.generation.clone(); let inner = work.inner.clone();
        if !windows::native_effect(false, Box::new(move || { apply_local(work); true })) {
            inner.failed.store(true, Ordering::SeqCst); let _ = generation.revoke();
        }
        return;
    }
    #[cfg(target_os = "macos")]
    { dispatch::Queue::main().exec_async(move || apply_local(work)); }
    #[cfg(not(any(windows, target_os = "macos")))]
    apply_local(work);
}
fn apply_local(work: Work) {
    let generation = &work.generation;
    let Ok(_serial) = generation.serial.lock() else { work.inner.failed.store(true, Ordering::SeqCst); return; };
    if !generation.active.load(Ordering::SeqCst) { return; }
    #[cfg(windows)] if !generation.backend.matches_current() {
        generation.active.store(false,Ordering::SeqCst); let _ = generation.kernel.revoke(); work.inner.failed.store(true,Ordering::SeqCst); return;
    }
    #[cfg(windows)]
    let Some(_kernel) = generation.kernel.effect() else { work.inner.failed.store(true, Ordering::SeqCst); return; };
    let active = generation.clone();
    apply(&generation.token, generation.effect_id, generation.conn, &work.action,
        Arc::new(move || active.admitted()), local_runner(generation.clone()));
}
type Deferred = Arc<dyn Fn(Box<dyn FnOnce() + Send>) -> bool + Send + Sync>;
static DEFERRED_PENDING: AtomicUsize = AtomicUsize::new(0);
struct DeferredPermit;
impl Drop for DeferredPermit { fn drop(&mut self) { DEFERRED_PENDING.fetch_sub(1,Ordering::SeqCst); } }
fn local_runner(generation: Arc<Generation>) -> Deferred {
    Arc::new(move |effect| {
        if !generation.admitted() || DEFERRED_PENDING.fetch_update(Ordering::SeqCst,Ordering::SeqCst,|v| (v < MAX_PENDING).then_some(v+1)).is_err() { return false; }
        let permit = DeferredPermit; let generation = generation.clone();
        let run = move || {
            let _permit = permit;
            let Ok(_serial) = generation.serial.lock() else { return false; };
            if !generation.admitted() { return false; }
            #[cfg(windows)] let Some(_kernel) = generation.kernel.effect() else { return false; };
            effect(); true
        };
        // Scheduling never waits for the current scope's serial/kernel guard.
        #[cfg(windows)] { windows::native_enqueue(false,Box::new(run)) }
        #[cfg(target_os = "macos")] { dispatch::Queue::main().exec_async(move || { run(); }); true }
        #[cfg(not(any(windows,target_os = "macos")))] { std::thread::Builder::new().name("mixel-deferred-input".to_owned()).spawn(run).is_ok() }
    })
}
#[cfg(target_os = "macos")]
fn on_main_thread() -> bool { extern "C" { fn pthread_main_np() -> i32; } unsafe { pthread_main_np() != 0 } }
fn schedule_cleanup(generation: Arc<Generation>) {
    if generation.cleanup_done.load(Ordering::SeqCst) || generation.cleanup_requested.swap(true, Ordering::SeqCst) { return; }
    let cleanup_generation = generation.clone();
    let run = move || {
        let retired = if let Ok(_serial) = cleanup_generation.serial.try_lock() { cleanup(&cleanup_generation.token) } else { false };
        cleanup_generation.cleanup_done.store(retired, Ordering::SeqCst);
        cleanup_generation.cleanup_requested.store(false, Ordering::SeqCst);
        retired
    };
    #[cfg(windows)] {
        if !windows::native_effect(true, Box::new(run)) { generation.cleanup_requested.store(false, Ordering::SeqCst); }
    }
    #[cfg(target_os = "macos")] {
        if on_main_thread() { run(); } else { dispatch::Queue::main().exec_async(move || { run(); }); }
    }
    #[cfg(not(any(windows,target_os = "macos")))] { run(); }
}
// BlockInput has its own authorization/lifetime, separate from keyboard data.
// Privacy/plugin calls retain their existing caller checks; only connection close
// invalidates both categories. No BlockOn is allowed to sit behind data work.
struct BlockRequest { desired: bool, owner: std::sync::Weak<Inner>, status: AtomicUsize }
struct BlockState { actual: bool, inflight: bool, requests: HashMap<(String, bool), Arc<BlockRequest>> }
struct Blocks { state: Mutex<BlockState>, changed: std::sync::Condvar }
fn blocks() -> Option<&'static Arc<Blocks>> {
    static BLOCKS: OnceLock<Option<Arc<Blocks>>> = OnceLock::new();
    BLOCKS.get_or_init(|| {
        let shared = Arc::new(Blocks { state: Mutex::new(BlockState { actual: false, inflight: false, requests: HashMap::new() }), changed: std::sync::Condvar::new() });
        let worker = shared.clone();
        std::thread::Builder::new().name("mixel-owned-block-input".to_owned()).spawn(move || block_worker(worker)).ok().map(|_| shared)
    }).as_ref()
}
fn block_worker(shared: Arc<Blocks>) {
    // Windows requires BlockInput(false) on the same thread that blocked. This
    // dedicated control worker is independent of every remote input FIFO.
    loop {
        let mut state = shared.state.lock().unwrap_or_else(|e| e.into_inner());
        for request in state.requests.values() {
            if request.desired && !request.owner.upgrade().map(|v| v.alive.load(Ordering::SeqCst)).unwrap_or(false) {
                request.status.store(2, Ordering::SeqCst);
            }
        }
        let wanted = state.requests.values().any(|r| r.desired && r.status.load(Ordering::SeqCst) != 2);
        if wanted != state.actual && !state.inflight {
            // Completion owns the actual-state update. A caller timeout must
            // never erase an OS call that could still finish on the native thread.
            state.inflight = true; drop(state);
            let checked = shared.clone();
            let effect = move || {
                let state = checked.state.lock().unwrap_or_else(|e| e.into_inner());
                let current = state.requests.values().any(|r| r.desired && r.status.load(Ordering::SeqCst) != 2 && r.owner.upgrade().map(|owner| owner.alive.load(Ordering::SeqCst)).unwrap_or(false));
                drop(state);
                let success = current == wanted && crate::platform::block_input(wanted).0;
                let mut state = checked.state.lock().unwrap_or_else(|e| e.into_inner());
                if success { state.actual = wanted; }
                state.inflight = false; checked.changed.notify_all(); success
            };
            #[cfg(windows)] if !windows::native_enqueue(true,Box::new(effect)) {
                shared.state.lock().unwrap_or_else(|e| e.into_inner()).inflight = false;
            }
            #[cfg(not(windows))] { effect(); }
            state = shared.state.lock().unwrap_or_else(|e| e.into_inner());
        }
        let wanted_now = state.requests.values().any(|r| r.desired && r.status.load(Ordering::SeqCst) != 2);
        if !state.inflight && wanted_now == state.actual {
            state.requests.retain(|_, request| {
                if request.status.load(Ordering::SeqCst) == 2 { return false; }
                request.status.store(1, Ordering::SeqCst);
                request.desired
            });
        }
        shared.changed.notify_all();
        let _wait = shared.changed.wait_timeout(state, Duration::from_millis(5)).unwrap_or_else(|e| e.into_inner());
    }
}
fn set_block(inner: &Arc<Inner>, enabled: bool, privacy: bool) -> bool {
    let Some(shared) = blocks() else { return false; };
    let key = (inner.control.clone(), privacy);
    let request = Arc::new(BlockRequest { desired: enabled, owner: Arc::downgrade(inner), status: AtomicUsize::new(0) });
    let mut state = shared.state.lock().unwrap_or_else(|e| e.into_inner());
    if !enabled && !state.requests.contains_key(&key) && !state.inflight { return true; }
    if !state.requests.contains_key(&key) && state.requests.len() >= 512 { return false; }
    if let Some(old) = state.requests.insert(key.clone(), request.clone()) { old.status.store(2, Ordering::SeqCst); }
    shared.changed.notify_all();
    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    loop {
        match request.status.load(Ordering::SeqCst) { 1 => return true, 2 => return false, _ => () }
        let now = std::time::Instant::now();
        if now >= deadline {
            // A timed-out On cannot silently activate later. Keep an Off request
            // until the same native thread actually releases the last OS block.
            if enabled && state.requests.get(&key).map(|v| Arc::ptr_eq(v, &request)).unwrap_or(false) {
                request.status.store(2, Ordering::SeqCst);
                state.requests.insert(key, Arc::new(BlockRequest { desired: false, owner: Arc::downgrade(inner), status: AtomicUsize::new(0) }));
                shared.changed.notify_all();
            }
            return false;
        }
        let waited = shared.changed.wait_timeout(state, deadline.saturating_duration_since(now)).unwrap_or_else(|e| e.into_inner());
        state = waited.0;
    }
}
fn apply(token: &str, effect_id: i32, conn: i32, action: &Action, allowed: Arc<dyn Fn() -> bool + Send + Sync>, runner: Deferred) {
    // Actual successful receipt ownership is maintained only by the native adapter.
    match action {
        Action::Key(original, press) => input_service::with_receiver_owner_scope_checked(token, effect_id, allowed.clone(), runner.clone(), || {
            let mut event = original.clone(); event.press = false;
            if *press { event.down = true; }
            if let Some(hbb_common::message_proto::key_event::Union::Seq(text)) = event.union.take() {
                // Text is one bounded work item. Each scalar reaches the actual
                // sink under a fresh deny check, avoiding one long native batch.
                let mut scalar = event.clone();
                for character in text.chars() {
                    if !allowed() { break; }
                    scalar.set_seq(character.to_string()); input_service::handle_key_(&scalar);
                }
            } else {
                let mut event = original.clone(); event.press = false;
                if *press { event.down = true; }
                if allowed() { input_service::handle_key_(&event); }
                if *press && allowed() { event.down = false; event.modifiers.clear(); input_service::handle_key_(&event); }
            }
        }),
        Action::Mouse(event, name, argb, simulate, cursor) => {
            input_service::with_receiver_owner_scope_checked(token, effect_id, allowed, runner, || input_service::handle_mouse_(event, conn, name.clone(), *argb, *simulate, *cursor));
        }
        Action::Pointer(event) => input_service::with_receiver_owner_scope_checked(token, effect_id, allowed, runner, || input_service::handle_pointer_(event, conn)),
    }
}
fn cleanup(token: &str) -> bool {
    // The native adapter owns canonical keys, buttons, and generation-relative
    // state. Clearing a real peer here could erase a fresh parallel generation.
    input_service::release_receiver_owner(token)
}

#[cfg(windows)]
pub(crate) fn portable_refused(token: &str) {
    let generation = generations().and_then(|registry| registry.lock().ok()?.get(token)?.get());
    if let Some(generation) = generation {
        if generation.active.load(Ordering::SeqCst) {
            generation.failure.store(true, Ordering::SeqCst); let _ = generation.revoke();
        }
    }
}
#[cfg(windows)]
pub(crate) fn encode(token: &str, target: &str, conn: i32, action: &Action) -> Option<(String, String, i32, u8, Vec<u8>, String, u32, bool, bool)> {
    let (kind, data, name, argb, a, b) = match action {
        Action::Mouse(v, name, argb, simulate, cursor) => (0, v.write_to_bytes().ok()?, name.clone(), *argb, *simulate, *cursor),
        Action::Key(v, press) => (1, v.write_to_bytes().ok()?, String::new(), 0, *press, false),
        Action::Pointer(v) => (2, v.write_to_bytes().ok()?, String::new(), 0, false, false),
    };
    (data.len() + name.len() <= MAX_BYTES).then_some((token.to_owned(), target.to_owned(), conn, kind, data, name, argb, a, b))
}
#[cfg(windows)]
#[path = "mixel_receiver_input_windows.rs"]
mod windows;
#[cfg(windows)]
pub(crate) use windows::{receive, pipe_peer, Peer, shutdown as portable_shutdown};
