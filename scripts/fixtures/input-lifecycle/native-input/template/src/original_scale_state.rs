//! Per-connection ownership at the common native/Flutter outgoing wire boundary.
//!
//! Revoking keyboard permission terminates this connection's key holds. Exact
//! releases wait until the peer grants permission again; they precede publication
//! of the renewed grant. No server permission check or global key state is changed.
use hbb_common::message_proto::{key_event, message, KeyEvent, Message, MouseEvent, ControlKey, pointer_device_event, touch_event};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KeyboardOrigin {
    Ui,
    Native,
}

#[derive(Clone)]
pub struct KeyboardInput {
    pub event: KeyEvent,
    pub origin: KeyboardOrigin,
    pub epoch: usize,
    pub permitted: bool,
    // Native physical modifiers, converted and swapped by the same sender as
    // the associated event. None means no native physical snapshot was taken.
    pub modifiers: Option<Vec<KeyEvent>>,
}

struct HeldKey {
    event: KeyEvent,
    owner: KeyboardOrigin,
}

pub struct KeyboardState {
    enabled: bool,
    epoch: usize,
    held: Vec<HeldKey>,
    native_needs_restore: bool,
    buttons: Vec<MouseEvent>,
    resumed_buttons: Vec<MouseEvent>,
}

impl KeyboardState {
    pub fn new(enabled: bool, epoch: usize) -> Self {
        Self { enabled, epoch, held: Vec::new(), native_needs_restore: false, buttons: Vec::new(), resumed_buttons: Vec::new() }
    }

    pub fn epoch(&self) -> usize { self.epoch }

    pub fn adopt_epoch(&mut self, epoch: usize) { self.epoch = epoch; }

    /// Called only for a still-current shared source epoch, queued before the
    /// native grab transition. A denied peer cannot accept cleanup yet.
    pub fn change_source(&mut self, epoch: usize) -> Vec<KeyEvent> {
        self.epoch = epoch;
        self.native_needs_restore = true;
        if !self.enabled { return Vec::new(); }
        self.held.drain(..).rev().map(|held| release(&held.event)).collect()
    }


    /// The caller serializes these releases to its exact connection before it
    /// publishes `enabled=true`. A failed write must end that connection.
    pub fn set_permission(&mut self, enabled: bool) -> Vec<KeyEvent> {
        if self.enabled == enabled { return Vec::new(); }
        self.enabled = enabled;
        if !enabled {
            self.epoch = self.epoch.wrapping_add(1);
            self.native_needs_restore = true;
            return Vec::new();
        }
        self.resumed_buttons = self.buttons.drain(..).rev().map(|mut event| {
            event.mask = (event.mask & !0x7) | 2; // Exact owned button, MOUSE_TYPE_UP.
            // The production server UP decoder only calls mouse_up; it does
            // not move to x/y. Zero avoids retaining a stale down position.
            event.x = 0;
            event.y = 0;
            event.modifiers.clear();
            event
        }).collect();
        self.held.drain(..).rev().map(|held| release(&held.event)).collect()
    }

    /// Drain only the releases made by the immediately preceding renewed grant.
    /// The caller sends these before key releases and before grant publication.
    pub fn take_button_releases(&mut self) -> Vec<MouseEvent> {
        std::mem::take(&mut self.resumed_buttons)
    }

    pub fn prepare_pointer(&mut self, message: Message, epoch: usize, permitted: bool) -> Option<Message> {
        if !self.permits(epoch, permitted) { return None; }
        if let Some(message::Union::MouseEvent(event)) = &message.union {
            let button = event.mask >> 3;
            if matches!(button, 1 | 2 | 4 | 8 | 16) {
                let index = self.buttons.iter().position(|held| held.mask >> 3 == button);
                match event.mask & 0x7 {
                    1 => if index.is_none() { self.buttons.push(event.clone()); },
                    2 => {
                        let index = index?;
                        self.buttons.remove(index);
                    }
                    _ => {}
                }
            }
        }
        Some(message)
    }

    pub fn permits(&self, epoch: usize, permitted: bool) -> bool {
        self.enabled && permitted && epoch == self.epoch
    }

    pub fn prepare_input(&mut self, input: KeyboardInput) -> Vec<KeyEvent> {
        if !self.permits(input.epoch, input.permitted) {
            return Vec::new();
        }
        let mut events = Vec::new();
        if input.origin == KeyboardOrigin::Native && self.native_needs_restore
            && (input.event.down || input.event.press || matches!(input.event.union,
                Some(key_event::Union::Seq(_)) | Some(key_event::Union::Unicode(_))))
        {
            if let Some(modifiers) = input.modifiers {
                // Restore only physically held modifier templates. This does not
                // replay other held keys, Unicode, text, or events queued before
                // revocation. The real event itself follows these owned downs.
                for event in modifiers {
                    if event.down && !event.press && persistent_key(&event) {
                        if self.accept(&event, KeyboardOrigin::Native) {
                            events.push(event);
                        }
                    }
                }
                self.native_needs_restore = false;
            }
        }
        if self.accept(&input.event, input.origin) { events.push(input.event); }
        events
    }

    fn accept(&mut self, event: &KeyEvent, origin: KeyboardOrigin) -> bool {
        if event.press || !persistent_key(event) { return true; }
        let found = self.held.iter().position(|held| same_key(&held.event, event));
        if event.down {
            if let Some(index) = found {
                // These are exclusive sources of the same local keyboard.
                // Transfer ownership on handoff; retaining the former source
                // would require an Up from a source that is no longer active.
                self.held[index].owner = origin;
            } else {
                self.held.push(HeldKey { event: event.clone(), owner: origin });
            }
            // Real repeats retain their existing delivery and never increase
            // ownership depth.
            return true;
        }
        let Some(index) = found else { return false; };
        if self.held[index].owner != origin { return false; }
        self.held.remove(index);
        true
    }
}

pub fn scoped_pointer_input(message: &Message) -> bool {
    match &message.union {
        Some(message::Union::MouseEvent(_)) => true,
        Some(message::Union::PointerDeviceEvent(event)) => matches!(&event.union,
            Some(pointer_device_event::Union::TouchEvent(touch)) if matches!(&touch.union,
                Some(touch_event::Union::ScaleUpdate(_)))),
        _ => false,
    }
}

fn persistent_key(event: &KeyEvent) -> bool {
    match &event.union {
        Some(key_event::Union::ControlKey(key)) => !matches!(key.enum_value_or_default(),
            ControlKey::CtrlAltDel | ControlKey::LockScreen),
        Some(key_event::Union::Chr(_)) | Some(key_event::Union::Win2winHotkey(_)) => true,
        _ => false,
    }
}

fn same_key(left: &KeyEvent, right: &KeyEvent) -> bool {
    if left.mode != right.mode { return false; }
    match (&left.union, &right.union) {
        (Some(key_event::Union::Win2winHotkey(a)), Some(key_event::Union::Win2winHotkey(b))) =>
            a >> 16 == b >> 16,
        (a, b) => a == b,
    }
}

fn release(event: &KeyEvent) -> KeyEvent {
    let mut event = event.clone();
    event.down = false;
    event.press = false;
    event.modifiers.clear();
    // The low word represents text on down, not part of the physical VK on up.
    if let Some(key_event::Union::Win2winHotkey(code)) = event.union {
        event.set_win2win_hotkey(code & 0xffff0000);
    }
    event
}
