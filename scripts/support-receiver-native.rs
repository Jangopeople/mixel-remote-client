//! Successful native input receipts shared by scoped and compatibility callers.
//! API acceptance is not proof of target-application consumption.
use super::*;
#[path = "mixel_receiver_receipts.rs"]
mod receipt_core;
use receipt_core::{Claim, Ledger};
use std::{cell::RefCell, collections::HashMap, sync::{Arc, Mutex, OnceLock, atomic::{AtomicU64, Ordering}}, time::Instant};

pub(crate) type Admit = Arc<dyn Fn() -> bool + Send + Sync>;
/// Enqueue on the receiver-owned effect executor; must not wait for completion.
pub(crate) type ScopedRunner = Arc<dyn Fn(Box<dyn FnOnce() + Send>) -> bool + Send + Sync>;
#[derive(Clone)]
struct Context { owner: String, mouse_owner: i32, operation: u64, pulse: bool, admit: Admit, runner: Option<ScopedRunner> }
thread_local! { static CONTEXT: RefCell<Option<Context>> = const { RefCell::new(None) }; }
static NEXT_OPERATION: AtomicU64 = AtomicU64::new(1);
fn serial() -> &'static Mutex<()> { static VALUE: OnceLock<Mutex<()>> = OnceLock::new(); VALUE.get_or_init(|| Mutex::new(())) }
fn context() -> Option<Context> { CONTEXT.with(|value| value.borrow().clone()) }
fn admitted() -> bool { context().map(|value| (value.admit)()).unwrap_or(false) }
fn saved_scopes() -> &'static Mutex<HashMap<String, Context>> {
    static VALUE: OnceLock<Mutex<HashMap<String, Context>>> = OnceLock::new();
    VALUE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn claim(temporary: bool) -> Option<Claim> {
    context().map(|value| Claim { owner: value.owner, temporary: if temporary || value.pulse { value.operation } else { 0 } })
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct Physical(u32);
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
enum Alias { Enigo(Key), Raw(RawKey), #[cfg(target_os="windows")] Unicode(u16) }
#[derive(Clone, Copy)]
enum Route { Raw(RawKey), Enigo(Key), #[cfg(target_os="windows")] Unicode(u16), #[cfg(target_os="linux")] Unicode(char) }
#[derive(Clone, Copy)]
struct Prepared { id: Physical, route: Route, modifiers: u8 }
type Receipts = Ledger<Physical, Alias, Route>;
fn receipts() -> &'static Mutex<Receipts> { static VALUE: OnceLock<Mutex<Receipts>> = OnceLock::new(); VALUE.get_or_init(|| Mutex::new(Receipts::default())) }
fn native_error(error: impl std::fmt::Display) { log::error!("Owned native input was not accepted: {}", error); }

fn emit_raw(route: Route, down: bool) -> Result<(), String> {
    match route {
        Route::Raw(raw) => super::simulate_native_result(&if down { EventType::KeyPress(RdevKey::RawKey(raw)) } else { EventType::KeyRelease(RdevKey::RawKey(raw)) }),
        Route::Enigo(_) => Err("native Enigo lock is required for this backend".into()),
        #[cfg(target_os="windows")]
        Route::Unicode(_) => Err("native Unicode lock is required".into()),
        #[cfg(target_os="linux")]
        Route::Unicode(character) => rdev::simulate_char(character, down).map_err(|_| "X11 Unicode injection was not accepted".to_owned()),
    }
}
fn emit(en: &mut Enigo, route: Route, down: bool) -> Result<(), String> {
    match route {
        Route::Enigo(key) => (if down { en.key_down(key) } else { en.key_up_checked(key) }).map_err(|error| error.to_string()),
        #[cfg(target_os="windows")]
        Route::Unicode(unit) => en.receiver_unicode_event(unit, down).map_err(|error| error.to_string()),
        _ => emit_raw(route, down),
    }
}

fn acquire(en: &mut Enigo, prepared: Prepared, alias: Alias, temporary: bool) -> Result<(), String> {
    let owner = claim(temporary).ok_or("missing native owner")?;
    receipts().lock().map_err(|_| "native receipt ledger was poisoned")?
        .down(owner, alias, prepared.id, prepared.route, !temporary, Instant::now(), admitted, |route, down| emit(en, route, down))
}
fn release(en: &mut Enigo, alias: Alias, temporary: bool) -> Result<(), String> {
    let owner = claim(temporary).ok_or("missing native owner")?;
    let fallback = match alias {
        Alias::Enigo(key) => prepare_enigo(en, key), Alias::Raw(raw) => prepare_raw(raw),
        #[cfg(target_os="windows")]
        Alias::Unicode(unit) => Some(Prepared { id: Physical(0x1_0000 + unit as u32), route: Route::Unicode(unit), modifiers: 0 }),
    }.map(|plan| plan.id);
    receipts().lock().unwrap_or_else(|error| error.into_inner())
        .up(&owner, alias, fallback, |route, down| emit(en, route, down))
}
fn retire_matching(en: &mut Enigo, selected: impl Fn(&Claim, &Instant) -> bool) -> bool {
    let errors = receipts().lock().unwrap_or_else(|error| error.into_inner())
        .retire(selected, |route, down| emit(en, route, down));
    let complete = errors.is_empty();
    for error in errors { native_error(error); }
    complete
}

struct ScopeReset { prior: Option<Context>, owner: String, operation: u64 }
impl Drop for ScopeReset {
    fn drop(&mut self) {
        let mut en = ENIGO.lock().unwrap_or_else(|error| error.into_inner());
        retire_matching(&mut en, |claim, _| claim.owner == self.owner && claim.temporary == self.operation);
        CONTEXT.with(|value| { value.replace(self.prior.take()); });
    }
}

pub(crate) fn with_receiver_owner(owner: &str, mouse_owner: i32, effect: impl FnOnce()) {
    with_receiver_owner_checked(owner, mouse_owner, Arc::new(|| true), effect);
}
pub(crate) fn with_receiver_owner_checked(owner: &str, mouse_owner: i32, admit: Admit, effect: impl FnOnce()) {
    with_scope(owner, mouse_owner, admit, None, effect);
}
pub(crate) fn with_receiver_owner_scope_checked(owner: &str, mouse_owner: i32, admit: Admit, runner: ScopedRunner, effect: impl FnOnce()) {
    with_scope(owner, mouse_owner, admit, Some(runner), effect);
}
fn with_scope(owner: &str, mouse_owner: i32, admit: Admit, runner: Option<ScopedRunner>, effect: impl FnOnce()) {
    if context().is_some() { native_error("nested receiver owner admission"); return; }
    let Ok(_serial) = serial().lock() else { native_error("native serialization was poisoned"); return; };
    if !(admit)() { return; }
    let Ok(operation) = NEXT_OPERATION.fetch_update(Ordering::SeqCst, Ordering::SeqCst, |value| value.checked_add(1)) else { native_error("native operation identity exhausted"); return; };
    if !register_mouse_owner(owner, mouse_owner) { native_error("native owner identity changed"); return; }
    let scope = Context { owner: owner.to_owned(), mouse_owner, operation, pulse: false, admit, runner };
    if scope.runner.is_some() { saved_scopes().lock().unwrap().insert(owner.to_owned(), scope.clone()); }
    let prior = CONTEXT.with(|value| value.replace(Some(scope)));
    let _reset = ScopeReset { prior, owner: owner.to_owned(), operation };
    effect();
}
pub(super) fn with_default_scope(effect: impl FnOnce()) {
    if context().is_some() { effect(); }
    else { with_receiver_owner("@trusted-native-input", 0, effect); }
}
pub(super) fn with_key_pulse(effect: impl FnOnce()) {
    let Some(mut scope) = context() else { return; };
    scope.pulse = true;
    let prior = CONTEXT.with(|value| value.replace(Some(scope)));
    struct Reset(Option<Context>);
    impl Drop for Reset { fn drop(&mut self) { CONTEXT.with(|value| { value.replace(self.0.take()); }); } }
    let _reset = Reset(prior);
    effect();
}

#[cfg(target_os="linux")]
mod x11 {
    use std::ffi::c_void;
    #[link(name="X11")]
    extern "C" {
        fn XOpenDisplay(name: *const i8) -> *mut c_void;
        fn XCloseDisplay(display: *mut c_void) -> i32;
        fn XKeysymToKeycode(display: *mut c_void, symbol: std::os::raw::c_ulong) -> u8;
        fn XKeycodeToKeysym(display: *mut c_void, code: u8, index: i32) -> std::os::raw::c_ulong;
    }
    pub(super) fn character(character: char) -> Option<(u32, u8)> {
        unsafe {
            let display = XOpenDisplay(std::ptr::null());
            if display.is_null() { return None; }
            let symbol = if (character as u32) < 0x100 { character as u32 } else { 0x0100_0000 | character as u32 };
            let code = XKeysymToKeycode(display, symbol as _);
            let value = if code == 0 { None }
                else if XKeycodeToKeysym(display, code, 0) == symbol as _ { Some((code as u32, 0)) }
                else if XKeycodeToKeysym(display, code, 1) == symbol as _ { Some((code as u32, 1)) }
                else { None };
            XCloseDisplay(display);
            value
        }
    }
}

fn prepare_enigo(en: &mut Enigo, key: Key) -> Option<Prepared> {
    #[cfg(target_os="windows")]
    {
        let (vk, modifiers) = en.receiver_key_plan(key)?;
        let code = rdev::vk_to_scancode(vk);
        if code == 0 || code > u16::MAX as u32 { return None; }
        return Some(Prepared { id: Physical(code), route: Route::Raw(RawKey::ScanCode(code)), modifiers });
    }
    #[cfg(target_os="macos")]
    {
        let (code, modifiers) = en.receiver_key_plan(key)?;
        return Some(Prepared { id: Physical(code), route: Route::Raw(RawKey::MacVirtualKeycode(code as _)), modifiers });
    }
    #[cfg(target_os="linux")]
    {
        let x11 = crate::platform::linux::is_x11();
        let (code, modifiers) = match key {
            Key::Raw(code) => (code as u32, 0),
            Key::Layout(character) if x11 => x11::character(character)?,
            _ => { let (code, shift) = super::super::uinput::service::map_key(&key).ok()?; (code.code() as u32 + 8, u8::from(shift)) },
        };
        if code < 8 || code > u16::MAX as u32 { return None; }
        let route = if x11 { Route::Raw(RawKey::LinuxXorgKeycode(code as _)) } else { Route::Enigo(Key::Raw(code as _)) };
        return Some(Prepared { id: Physical(code), route, modifiers });
    }
    #[allow(unreachable_code)]
    None
}

fn prepare_raw(raw: RawKey) -> Option<Prepared> {
    #[cfg(target_os="windows")]
    let code = match raw { RawKey::ScanCode(code) => code as u32, RawKey::WinVirtualKeycode(vk) => rdev::vk_to_scancode(vk), _ => return None };
    #[cfg(target_os="macos")]
    let code = match raw { RawKey::MacVirtualKeycode(code) => code as u32, _ => return None };
    #[cfg(target_os="linux")]
    let code = match raw { RawKey::LinuxXorgKeycode(code) => code as u32, _ => return None };
    if code > u16::MAX as u32 { return None; }
    #[cfg(target_os="windows")]
    if code == 0 { return None; }
    #[cfg(target_os="windows")]
    let route = Route::Raw(RawKey::ScanCode(code));
    #[cfg(target_os="macos")]
    let route = Route::Raw(RawKey::MacVirtualKeycode(code as _));
    #[cfg(target_os="linux")]
    let route = if crate::platform::linux::is_x11() { Route::Raw(RawKey::LinuxXorgKeycode(code as _)) } else { Route::Enigo(Key::Raw(code as _)) };
    Some(Prepared { id: Physical(code), route, modifiers: 0 })
}

fn raw_key(key: RdevKey) -> Option<RawKey> {
    if let RdevKey::RawKey(raw) = key { return Some(raw); }
    #[cfg(target_os="windows")]
    { return rdev::win_scancode_from_key(key).map(RawKey::ScanCode); }
    #[cfg(target_os="macos")]
    { return rdev::macos_keycode_from_key(key).map(|code| RawKey::MacVirtualKeycode(code as _)); }
    #[cfg(target_os="linux")]
    { return rdev::linux_keycode_from_key(key).map(|code| RawKey::LinuxXorgKeycode(code as _)); }
}

pub(super) fn key_down(en: &mut Enigo, key: Key) -> enigo::ResultType { key_down_kind(en, key, false).map_err(Into::into) }
pub(super) fn key_down_temporary(en: &mut Enigo, key: Key) -> enigo::ResultType { key_down_kind(en, key, true).map_err(Into::into) }
fn key_down_kind(en: &mut Enigo, key: Key, temporary: bool) -> Result<(), String> {
    let prepared = prepare_enigo(en, key).ok_or("native key preparation failed")?;
    let modifiers = [Key::Shift, Key::Control, Key::Alt];
    let mut acquired = Vec::new();
    for (index, modifier) in modifiers.into_iter().enumerate() {
        if prepared.modifiers & (1 << index) != 0 {
            let plan = prepare_enigo(en, modifier).ok_or("native modifier preparation failed")?;
            if let Err(error) = acquire(en, plan, Alias::Enigo(modifier), true) {
                for modifier in acquired.into_iter().rev() { if let Err(error) = release(en, Alias::Enigo(modifier), true) { native_error(error); } }
                return Err(error);
            }
            acquired.push(modifier);
        }
    }
    let applied = acquire(en, prepared, Alias::Enigo(key), temporary);
    let mut release_error = None;
    for modifier in acquired.into_iter().rev() { if let Err(error) = release(en, Alias::Enigo(modifier), true) { release_error = Some(error); } }
    applied?;
    if let Some(error) = release_error { return Err(error); }
    Ok(())
}
pub(super) fn key_up(en: &mut Enigo, key: Key) { if let Err(error) = release(en, Alias::Enigo(key), false) { native_error(error); } }
pub(super) fn key_up_temporary(en: &mut Enigo, key: Key) { if let Err(error) = release(en, Alias::Enigo(key), true) { native_error(error); } }
fn key_click_checked(en: &mut Enigo, key: Key) -> Result<(), String> {
    let mut result = Err("missing native owner".into());
    with_key_pulse(|| { result = key_down_kind(en, key, false).and_then(|_| release(en, Alias::Enigo(key), false)); });
    result
}
pub(super) fn key_click(en: &mut Enigo, key: Key) {
    if let Err(error) = key_click_checked(en, key) { native_error(error); }
}
pub(super) fn simulate(event: &EventType) {
    let (key, down) = match event { EventType::KeyPress(key) => (*key, true), EventType::KeyRelease(key) => (*key, false), _ => { if admitted() { if let Err(error) = super::simulate_native_result(event) { native_error(error); } } return; } };
    let Some(raw) = raw_key(key) else { native_error("native raw key preparation failed"); return; };
    // Raw callers can already hold ENIGO. The native raw route is independent.
    let result = (|| -> Result<(), String> {
        let owner = claim(false).ok_or("missing native owner")?;
        let plan = prepare_raw(raw).ok_or("invalid native raw key")?;
        let mut state = receipts().lock().map_err(|_| "native receipt ledger was poisoned")?;
        if down { state.down(owner, Alias::Raw(raw), plan.id, plan.route, true, Instant::now(), admitted, emit_raw) }
        else { state.up(&owner, Alias::Raw(raw), Some(plan.id), emit_raw) }
    })();
    if let Err(error) = result { native_error(error); }
}

pub(super) fn key_sequence(en: &mut Enigo, text: &str) {
    for character in text.chars() {
        if !admitted() { return; }
        #[cfg(target_os="windows")]
        {
            let mut units = [0; 2];
            for unit in character.encode_utf16(&mut units) {
                if !admitted() { return; }
                let mut result = Ok(());
                with_key_pulse(|| {
                    let plan = Prepared { id: Physical(0x1_0000 + *unit as u32), route: Route::Unicode(*unit), modifiers: 0 };
                    result = acquire(en, plan, Alias::Unicode(*unit), false)
                        .and_then(|_| release(en, Alias::Unicode(*unit), false));
                });
                if let Err(error) = result { native_error(error); return; }
            }
        }
        #[cfg(target_os="macos")]
        if let Err(error) = en.receiver_text_event(character) { native_error(error); return; }
        #[cfg(target_os="linux")]
        {
            if crate::platform::linux::is_x11() && x11::character(character).is_none() {
                // rdev's existing Unicode fallback uses keycode194. Never borrow
                // that native key while another owned persistent hold uses it.
                if receipts().lock().unwrap_or_else(|error| error.into_inner()).keys.contains_key(&Physical(194)) { native_error("Unicode scratch key is already owned"); return; }
                let mut result = Ok(());
                with_key_pulse(|| {
                    let plan = Prepared { id: Physical(194), route: Route::Unicode(character), modifiers: 0 };
                    result = acquire(en, plan, Alias::Enigo(Key::Layout(character)), false)
                        .and_then(|_| release(en, Alias::Enigo(Key::Layout(character)), false));
                });
                if let Err(error) = result { native_error(error); return; }
            } else if let Err(error) = key_click_checked(en, Key::Layout(character)) { native_error(error); return; }
        }
    }
}

pub(super) fn mouse_owner(peer: i32) -> i32 { context().filter(|scope| scope.mouse_owner != 0).map(|scope| scope.mouse_owner).unwrap_or(peer) }
fn relative() -> &'static Mutex<HashMap<i32, i32>> { static VALUE: OnceLock<Mutex<HashMap<i32,i32>>> = OnceLock::new(); VALUE.get_or_init(|| Mutex::new(HashMap::new())) }
pub(super) fn set_relative(peer: i32, active: bool) {
    let owner = mouse_owner(peer);
    let mut scopes = relative().lock().unwrap_or_else(|error| error.into_inner());
    if active { scopes.insert(owner, peer); } else { scopes.remove(&owner); }
    let active = scopes.values().any(|value| *value == peer);
    let mut peers = RELATIVE_MOUSE_CONNS.lock().unwrap();
    if active { peers.insert(peer); } else { peers.remove(&peer); }
}
pub(super) fn clear_peer_relative(peer: i32) {
    relative().lock().unwrap_or_else(|error| error.into_inner()).retain(|_, value| *value != peer);
    RELATIVE_MOUSE_CONNS.lock().unwrap().remove(&peer);
}
fn clear_mouse_owner(en: &mut Enigo, owner: i32) -> bool {
    let mut complete = true;
    let mut buttons = MOUSE_BUTTON_OWNERS.lock().unwrap();
    let ids: Vec<i32> = buttons.keys().copied().collect();
    for button in ids {
        let scopes = buttons.get_mut(&button).unwrap();
        if !scopes.contains(&owner) { continue; }
        if scopes.len() != 1 { scopes.remove(&owner); continue; }
        let Some(button_id) = mouse_button_from_mask(button) else { complete = false; continue; };
        if let Err(error) = en.mouse_up_checked(button_id) { native_error(error); complete = false; continue; }
        buttons.remove(&button);
    }
    let mut scopes = relative().lock().unwrap_or_else(|error| error.into_inner());
    if let Some(peer) = scopes.remove(&owner) {
        if !scopes.values().any(|value| *value == peer) { RELATIVE_MOUSE_CONNS.lock().unwrap().remove(&peer); }
    }
    complete
}
pub(crate) fn release_receiver_owner(owner: &str) -> bool {
    let mut complete = false;
    with_default_scope(|| {
        let mut en = ENIGO.lock().unwrap_or_else(|error| error.into_inner());
        complete = retire_matching(&mut en, |claim, _| claim.owner == owner);
        // The owner→mouse association survives scope exit and failed releases.
        let mouse = owners_mouse().lock().unwrap().get(owner).copied();
        if let Some(mouse) = mouse {
            if !clear_mouse_owner(&mut en, mouse) { complete = false; }
            if complete { owners_mouse().lock().unwrap().remove(owner); }
        }
    });
    if complete {
        saved_scopes().lock().unwrap().remove(owner);
        #[cfg(target_os="windows")]
        win_pulses().lock().unwrap().retain(|(held, _)| held != owner);
    }
    complete
}
fn owners_mouse() -> &'static Mutex<HashMap<String,i32>> { static VALUE: OnceLock<Mutex<HashMap<String,i32>>> = OnceLock::new(); VALUE.get_or_init(|| Mutex::new(HashMap::new())) }
fn register_mouse_owner(owner: &str, mouse: i32) -> bool {
    if mouse == 0 { return true; }
    let mut owners = owners_mouse().lock().unwrap();
    if let Some(previous) = owners.get(owner) { return *previous == mouse; }
    owners.insert(owner.to_owned(), mouse); true
}
pub(super) fn expire(force: bool) {
    // Remote cleanup must run on its captured effect executor (BlockInput is
    // thread-affine). A denied runner is handled by receiver revocation cleanup.
    let owners: Vec<String> = {
        let state = receipts().lock().unwrap_or_else(|error| error.into_inner());
        state.keys.values().flat_map(|held| held.claims.iter())
            .filter(|(_, time)| force || time.elapsed().as_millis() >= 360_000)
            .map(|(owner, _)| owner.owner.clone()).collect()
    };
    let mut visited = std::collections::HashSet::new();
    for owner in owners {
        if !visited.insert(owner.clone()) { continue; }
        let scope = saved_scopes().lock().unwrap().get(&owner).cloned();
        if let Some(scope) = scope {
            let Some(runner) = scope.runner.clone() else { continue; };
            let next = runner.clone();
            runner(Box::new(move || with_scope(&scope.owner.clone(), scope.mouse_owner, scope.admit, Some(next), || {
                let mut en = ENIGO.lock().unwrap_or_else(|error| error.into_inner());
                retire_matching(&mut en, |claim, time| claim.owner == owner && (force || time.elapsed().as_millis() >= 360_000));
            })));
        } else if owner == "@trusted-native-input" {
            with_default_scope(|| {
                let mut en = ENIGO.lock().unwrap_or_else(|error| error.into_inner());
                retire_matching(&mut en, |claim, time| claim.owner == owner && (force || time.elapsed().as_millis() >= 360_000));
            });
        }
    }
}

pub(super) fn has_key(en: &mut Enigo, key: Key) -> bool {
    prepare_enigo(en, key).map(|plan| receipts().lock().unwrap_or_else(|error| error.into_inner()).keys.contains_key(&plan.id)).unwrap_or(false)
}

/// Preserve admission across queue/thread hops. The receiver runner queues work;
/// it must not wait for this closure while the current effect owns serialization.
pub(super) fn deferred(effect: impl FnOnce() + Send + 'static) {
    if let Some(scope) = context() {
        let Some(runner) = scope.runner.clone() else { native_error("no scoped deferred effect executor"); return; };
        let next = runner.clone();
        if !runner(Box::new(move || with_scope(&scope.owner, scope.mouse_owner, scope.admit, Some(next), effect))) {
            native_error("scoped deferred effect was not scheduled");
        }
    } else {
        // Compatibility callers without a remote scope retain the original thread.
        std::thread::spawn(move || with_default_scope(effect));
    }
}
pub(super) fn captured(effect: impl FnOnce() + Send + 'static) -> impl FnOnce() + Send {
    let scope = context();
    move || match scope {
        Some(scope) => with_scope(&scope.owner, scope.mouse_owner, scope.admit, scope.runner, effect),
        None => with_default_scope(effect),
    }
}
pub(super) fn effect_admitted() -> bool { admitted() }
pub(super) fn bounded_wheel(value: i32) -> bool { (-1024..=1024).contains(&value) }
pub(super) fn wheel(en: &mut Enigo, mut value: i32, horizontal: bool, trackpad: bool) {
    if !bounded_wheel(value) { native_error("wheel action exceeds native work bound"); return; }
    while value != 0 {
        if !admitted() { return; }
        let chunk = value.clamp(-16, 16);
        #[cfg(target_os="macos")]
        if horizontal { en.mouse_scroll_x(chunk, trackpad); } else { en.mouse_scroll_y(chunk, trackpad); }
        #[cfg(target_os="windows")]
        { let native = if trackpad { chunk } else { chunk * 120 }; if horizontal { en.mouse_scroll_x(native); } else { en.mouse_scroll_y(native); } }
        #[cfg(not(any(target_os="macos", target_os="windows")))]
        { let _ = trackpad; if horizontal { en.mouse_scroll_x(chunk); } else { en.mouse_scroll_y(chunk); } }
        value -= chunk;
    }
}

pub(super) fn valid_scale(value: f64) -> bool { value.is_finite() && (-1024.0..=1024.0).contains(&value) }
#[cfg(target_os="windows")]
fn win_pulses() -> &'static Mutex<std::collections::HashSet<(String,u32)>> {
    static VALUE: OnceLock<Mutex<std::collections::HashSet<(String,u32)>>> = OnceLock::new();
    VALUE.get_or_init(|| Mutex::new(std::collections::HashSet::new()))
}
#[cfg(target_os="windows")]
pub(super) fn win2win(en: &mut Enigo, code: u32, down: bool) {
    let Some(scope) = context() else { return; };
    let pulse = (scope.owner, code);
    if !down && win_pulses().lock().unwrap().remove(&pulse) { return; }
    if down {
        if let Some(character) = char::from_u32((code & 0xFFFF) as u32) {
            let key = Key::Layout(character);
            if prepare_enigo(en, key).is_some() {
                // A prepared one-shot never falls back to another Down after a
                // partial failure. Pending accepted holds stay in the ledger.
                win_pulses().lock().unwrap().insert(pulse);
                if let Err(error) = key_click_checked(en, key) { native_error(error); }
                return;
            }
        }
    }
    let raw = RawKey::WinVirtualKeycode((code >> 16) & 0xFFFF);
    simulate(&if down { EventType::KeyPress(RdevKey::RawKey(raw)) } else { EventType::KeyRelease(RdevKey::RawKey(raw)) });
}

pub(super) fn remote_scope() -> bool { context().map(|scope| scope.owner != "@trusted-native-input").unwrap_or(false) }
