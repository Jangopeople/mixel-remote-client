// Mac attended URI forwarding. Runtime only; no saved setting or bearer logging.
use std::collections::HashMap;
use std::sync::{mpsc, Mutex, OnceLock};
use std::time::{Duration, Instant};

pub(crate) const ACK_NAME: &str = "mixel-support-url-ack-v1";
pub(crate) const ACK_ACCEPTED: &str = "accepted";
pub(crate) const ACK_REJECTED: &str = "unavailable";
pub(crate) const READY_OPTION: &str = "mixel-support-url-callback-ready-v1";
pub(crate) const STREAM_PREFIX: &str = "mixel-support-url:";
pub(crate) const MAX_URL_BYTES: usize = 16_384;
pub(crate) const PENDING_TTL: Duration = Duration::from_millis(1_500);
pub(crate) const MAX_ACK_WORKERS: usize = 8;

fn decode_query(value: &str) -> Option<String> {
    let bytes = value.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => decoded.push(b' '),
            b'%' => {
                let high = (*bytes.get(index + 1)? as char).to_digit(16)?;
                let low = (*bytes.get(index + 2)? as char).to_digit(16)?;
                decoded.push(((high << 4) | low) as u8);
                index += 2;
            }
            byte => decoded.push(byte),
        }
        index += 1;
    }
    String::from_utf8(decoded).ok()
}

// Match Dart/Swift exact-key insertion order, replacement on a duplicate exact
// key, then case-normalization. Unknown parameters do not change the intent.
pub(crate) fn is_valid_support_url(raw: &str) -> bool {
    if raw.len() > MAX_URL_BYTES || raw.contains('#') || raw.chars().any(char::is_control) {
        return false;
    }
    let Some((scheme, rest)) = raw.split_once("://") else { return false; };
    let Some((authority, query)) = rest.split_once('?') else { return false; };
    if !scheme.eq_ignore_ascii_case("mixel-remote")
        || !(authority.eq_ignore_ascii_case("support") || authority.eq_ignore_ascii_case("support/"))
    {
        return false;
    }
    let mut exact = HashMap::new();
    let mut order = Vec::new();
    for pair in query.split('&') {
        let (name, value) = pair.split_once('=').unwrap_or((pair, ""));
        if name.is_empty() { continue; }
        let Some(name) = decode_query(name) else { return false; };
        let Some(value) = decode_query(value) else { return false; };
        if !exact.contains_key(&name) { order.push(name.clone()); }
        exact.insert(name, value);
    }
    let mut normalized = HashMap::new();
    for name in order { normalized.insert(name.to_lowercase(), exact.remove(&name).unwrap()); }
    let Some(token) = normalized.get("invite") else { return false; };
    let Some(key) = normalized.get("apikey") else { return false; };
    let bytes = token.as_bytes();
    bytes.len() == 40
        && bytes[..4].eq_ignore_ascii_case(b"inv_")
        && bytes[4..].iter().enumerate().all(|(index, byte)| {
            if matches!(index, 8 | 13 | 18 | 23) { *byte == b'-' } else { byte.is_ascii_hexdigit() }
        })
        && (20..=4_096).contains(&key.len())
        && key.bytes().all(|byte| (33..=126).contains(&byte))
}

pub(crate) fn accepted_ack(name: &str, value: Option<&str>) -> bool {
    name == ACK_NAME && value == Some(ACK_ACCEPTED)
}

fn valid_generation(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

pub(crate) fn stream_generation(app_type: &str) -> Option<&str> {
    let (kind, qualifier) = app_type.split_once(',')?;
    if kind != "main" { return None; }
    let generation = qualifier.strip_prefix(STREAM_PREFIX)?;
    valid_generation(generation).then_some(generation)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Delivery { Accepted, Unavailable, Superseded, Expired }

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Phase { WaitingCallback, WaitingSink, RejectedSink, Accepted, Expired }

fn record(phase: &'static str) {
    #[cfg(not(test))]
    hbb_common::log::info!("Mixel support URL handoff: {}", phase);
    #[cfg(test)]
    let _ = phase;
}

struct Pending {
    id: u64,
    url: String,
    expires: Instant,
    reply: mpsc::SyncSender<Delivery>,
    phase: Option<Phase>,
}

// The URI is deliberately not Debug: bearer contents must never enter logs.
pub(crate) struct Queue {
    next_id: u64,
    ready_epoch: u64,
    stream_generation: Option<String>,
    callback_ready: bool,
    pending: Option<Pending>,
}

impl Queue {
    pub(crate) fn new() -> Self {
        Self { next_id: 0, ready_epoch: 0, stream_generation: None, callback_ready: false, pending: None }
    }

    pub(crate) fn begin_stream(&mut self, generation: &str) -> bool {
        if !valid_generation(generation) || self.stream_generation.as_deref() == Some(generation) { return false; }
        self.stream_generation = Some(generation.to_owned());
        self.ready_epoch = 0;
        self.callback_ready = false;
        record("stream-started");
        true
    }

    pub(crate) fn has_stream(&self) -> bool { self.stream_generation.is_some() }

    pub(crate) fn stop_if_generation(&mut self, generation: &str) -> bool {
        if self.stream_generation.as_deref() != Some(generation) { return false; }
        self.stop();
        true
    }

    pub(crate) fn set_ready(&mut self, value: &str) -> bool {
        let Some((state, rest)) = value.split_once(':') else { return false; };
        let Some((generation, epoch)) = rest.split_once(':') else { return false; };
        if self.stream_generation.as_deref() != Some(generation) { return false; }
        if !matches!(state, "Y" | "N") || epoch.is_empty() || epoch.len() > 20
            || !epoch.bytes().all(|byte| byte.is_ascii_digit()) { return false; }
        let Ok(epoch) = epoch.parse::<u64>() else { return false; };
        // Late asynchronous FFI updates cannot reopen a disposed callback.
        if epoch == 0 || epoch <= self.ready_epoch { return false; }
        self.ready_epoch = epoch;
        let changed = self.callback_ready != (state == "Y");
        self.callback_ready = state == "Y";
        if changed { record(if self.callback_ready { "callback-ready" } else { "callback-paused" }); }
        true
    }

    pub(crate) fn offer(&mut self, url: String, now: Instant) -> Option<(u64, mpsc::Receiver<Delivery>)> {
        if !is_valid_support_url(&url) { return None; }
        // No wraparound can make a late timeout cancel a newer request.
        let next_id = self.next_id.checked_add(1)?;
        self.next_id = next_id;
        if let Some(previous) = self.pending.take() {
            let _ = previous.reply.try_send(Delivery::Superseded);
            record("superseded");
        }
        let (reply, receiver) = mpsc::sync_channel(1);
        self.pending = Some(Pending { id: next_id, url, expires: now + PENDING_TTL, reply, phase: None });
        record("pending");
        Some((next_id, receiver))
    }

    pub(crate) fn flush(&mut self, now: Instant, mut deliver: impl FnMut(&str) -> Option<bool>) -> Option<Phase> {
        let pending = self.pending.as_mut()?;
        let phase = if now >= pending.expires {
            Phase::Expired
        } else if !self.callback_ready {
            Phase::WaitingCallback
        } else {
            match deliver(&pending.url) {
                Some(true) => Phase::Accepted,
                Some(false) => Phase::RejectedSink,
                None => Phase::WaitingSink,
            }
        };
        if pending.phase != Some(phase) {
            pending.phase = Some(phase);
            record(match phase {
                Phase::WaitingCallback => "waiting-callback",
                Phase::WaitingSink => "waiting-sink",
                Phase::RejectedSink => "sink-rejected",
                Phase::Accepted => "delivered",
                Phase::Expired => "expired",
            });
        }
        if matches!(phase, Phase::Accepted | Phase::Expired) {
            let pending = self.pending.take().unwrap();
            let outcome = if phase == Phase::Accepted { Delivery::Accepted } else { Delivery::Expired };
            let _ = pending.reply.try_send(outcome);
        }
        Some(phase)
    }

    pub(crate) fn cancel(&mut self, id: u64) {
        if self.pending.as_ref().map(|pending| pending.id) == Some(id) {
            let pending = self.pending.take().unwrap();
            let _ = pending.reply.try_send(Delivery::Expired);
            record("expired");
        }
    }

    pub(crate) fn stop(&mut self) {
        self.callback_ready = false;
        self.stream_generation = None;
        self.ready_epoch = 0;
        if let Some(pending) = self.pending.take() { let _ = pending.reply.try_send(Delivery::Unavailable); }
        record("stream-stopped");
    }
}

pub(crate) fn global_queue() -> &'static Mutex<Queue> {
    static QUEUE: OnceLock<Mutex<Queue>> = OnceLock::new();
    QUEUE.get_or_init(|| Mutex::new(Queue::new()))
}
