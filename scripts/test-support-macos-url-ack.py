#!/usr/bin/env python3
"""Execute original/generated forwarding and readiness; no OS URI or HTTP."""
import ast
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from sdk_discovery import find_dart
from rust_toolchain import rustc_command

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get("RDREPO", ROOT / "rustdesk"))


def original(path):
    repo = UPSTREAM
    if path.startswith("libs/hbb_common/"):
        repo /= "libs/hbb_common"
        path = path.removeprefix("libs/hbb_common/")
    return subprocess.check_output(["git", "-C", str(repo), "show", "HEAD:" + path]).decode()


def between(text, start, end):
    return text[text.index(start):text.index(end, text.index(start))]


def rust_fixture(directory, generated):
    sender = between(generated["src/ipc.rs"], "pub async fn send_url_scheme(", "// Emit `close` events")
    # Execute the owned Mac branch on every CI host, without pretending to be
    # the host OS or delivering a URI. Its exact condition/body is preserved.
    sender = sender.replace('    #[cfg(all(target_os = "macos", feature = "flutter"))]\n', '', 1)
    baseline = between(original("src/ipc.rs"), "pub async fn send_url_scheme(", "// Emit `close` events")
    baseline = baseline.replace("pub async fn send_url_scheme(", "pub async fn original_send_url_scheme(", 1)
    receiver = between(original("src/server.rs"), "                            let mut m = HashMap::new();", "                        }\n                        _ =>")
    # The occurrence is inside the actual pinned Mac UrlLink receiver.
    assert 'm.insert("name", "on_url_scheme_received")' in receiver
    fixture = r'''
use std::collections::HashMap;
use std::future::Future;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Wake, Waker};
use std::time::Instant;
type ResultType<T> = Result<T, &'static str>;
macro_rules! bail { ($message:expr) => { return Err($message) }; }
#[macro_export] macro_rules! fixed_warn { ($($value:tt)*) => {{ () }}; }
mod log { pub(crate) use super::fixed_warn as warn; }
mod server { #[path = "QUEUE_PATH"] pub(crate) mod mixel_support_url; }
use server::mixel_support_url::*;
#[derive(Clone)] enum Data { UrlLink(String), Config((String, Option<String>)), Other }
struct Trace { mode: &'static str, sent: usize, delivered: usize, ack_reads: usize, deadline: u64 }
static TRACE: Mutex<Trace> = Mutex::new(Trace { mode: "ok", sent: 0, delivered: 0, ack_reads: 0, deadline: 0 });
mod serde_json { pub fn to_string(_: &std::collections::HashMap<&str, &str>) -> Result<String, ()> { Ok(String::new()) } }
mod flutter {
    pub const APP_TYPE_MAIN: &str = "main";
    pub fn push_global_event(_: &str, _: String) -> Option<bool> {
        let mut trace = super::TRACE.lock().unwrap();
        match trace.mode {
            "missing" => None,
            "rejected" => Some(false),
            "callback-null" => Some(true), // sink consumes, absent Dart callback drops it
            _ => { trace.delivered += 1; Some(true) }
        }
    }
}
fn original_receive(url: String) {
    ORIGINAL_RECEIVER
}
struct Connection;
async fn connect(deadline: u64, postfix: &str) -> ResultType<Connection> {
    assert_eq!((deadline, postfix), (1_000, "_url"));
    if TRACE.lock().unwrap().mode == "connect-failed" { Err("connect-failed") } else { Ok(Connection) }
}
impl Connection {
    async fn send(&mut self, frame: &Data) -> ResultType<()> {
        let Data::UrlLink(url) = frame else { return Err("unexpected-frame"); };
        let mut trace = TRACE.lock().unwrap();
        if trace.mode == "send-failed" { return Err("send-failed"); }
        trace.sent += 1;
        drop(trace);
        original_receive(url.clone());
        Ok(())
    }
    async fn next_timeout(&mut self, deadline: u64) -> ResultType<Option<Data>> {
        assert_eq!(deadline, 2_000);
        let mut trace = TRACE.lock().unwrap(); trace.ack_reads += 1;
        Ok(match trace.mode {
            "missing" | "callback-null" => None,
            "timeout" => return Err("read-timeout"),
            "rejected" | "superseded" | "expired" | "busy" => Some(Data::Config((ACK_NAME.into(), Some(ACK_REJECTED.into())))),
            "wrong-name" => Some(Data::Config(("wrong".into(), Some(ACK_ACCEPTED.into())))),
            "wrong-value" => Some(Data::Config((ACK_NAME.into(), Some("stale".into())))),
            "missing-value" => Some(Data::Config((ACK_NAME.into(), None))),
            "wrong-type" => Some(Data::Other),
            _ => Some(Data::Config((ACK_NAME.into(), Some(ACK_ACCEPTED.into())))),
        })
    }
}
async fn timeout<T>(deadline: u64, future: impl Future<Output = T>) -> Result<T, &'static str> {
    let mut trace = TRACE.lock().unwrap(); trace.deadline = deadline;
    if trace.mode == "total-timeout" { return Err("total-timeout"); }
    drop(trace); Ok(future.await)
}
BASELINE_SENDER
GENERATED_SENDER
struct Noop;
impl Wake for Noop { fn wake(self: Arc<Self>) {} }
fn run<T>(future: impl Future<Output = T>) -> T {
    let waker = Waker::from(Arc::new(Noop)); let mut context = Context::from_waker(&waker);
    let mut future = Box::pin(future);
    loop { if let Poll::Ready(value) = future.as_mut().poll(&mut context) { return value; } }
}
const GEN1: &str = "11111111111111111111111111111111";
const GEN2: &str = "22222222222222222222222222222222";
fn queue() -> Queue { let mut q = Queue::new(); assert!(q.begin_stream(GEN1)); q }
fn ready(q: &mut Queue, value: &str) -> bool { let (state, epoch) = value.split_once(':').unwrap_or((value, "")); q.set_ready(&format!("{state}:{GEN1}:{epoch}")) }
const FIRST: &str = "mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000001&apikey=synthetic-first-key-000000000000";
const SECOND: &str = "mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000002&apikey=synthetic-second-key-000000000000";
fn reset(mode: &'static str) { *TRACE.lock().unwrap() = Trace { mode, sent: 0, delivered: 0, ack_reads: 0, deadline: 0 }; }
#[test]
fn original_native_sender_claims_success_for_missing_rejected_and_callback_null() {
    for mode in ["missing", "rejected", "callback-null"] {
        reset(mode); assert!(run(original_send_url_scheme(FIRST.into())).is_ok());
        let trace = TRACE.lock().unwrap(); assert_eq!((trace.sent, trace.delivered, trace.ack_reads), (1, 0, 0));
    }
}
#[test]
fn corrected_actual_sender_requires_exact_ack_and_bounded_deadlines() {
    for mode in ["missing", "rejected", "callback-null", "timeout", "total-timeout", "wrong-name", "wrong-value", "missing-value", "wrong-type", "superseded", "expired", "busy", "connect-failed", "send-failed"] {
        reset(mode); assert!(run(send_url_scheme(FIRST.into())).is_err(), "non-success ACK must retain fallback");
        assert_eq!(TRACE.lock().unwrap().deadline, 4_000);
    }
    for mode in ["ok", "duplicate-ack"] {
        reset(mode); assert!(run(send_url_scheme(FIRST.into())).is_ok());
        // A per-request connection consumes one ACK, then is dropped. An extra
        // peer frame cannot acknowledge another intent or deliver it twice.
        assert_eq!(TRACE.lock().unwrap().ack_reads, 1);
    }
}
#[test]
fn unrelated_close_and_invalid_support_keep_actual_original_send_only_path() {
    for uri in ["close", "mixel-remote://123456", "mixel-remote://support/?invite=invalid&apikey=invalid"] {
        for mode in ["missing", "rejected", "wrong-name"] {
            reset(mode); let old = run(original_send_url_scheme(uri.into()));
            let old_counts = { let t = TRACE.lock().unwrap(); (t.sent, t.delivered, t.ack_reads, t.deadline) };
            reset(mode); let new = run(send_url_scheme(uri.into()));
            let new_counts = { let t = TRACE.lock().unwrap(); (t.sent, t.delivered, t.ack_reads, t.deadline) };
            assert_eq!(old, new); assert_eq!(old_counts, new_counts);
        }
    }
}
#[test]
fn queue_waits_for_callback_and_sink_in_both_startup_orders() {
    let now = Instant::now();
    for sink_first in [false, true] {
        let mut queue = queue(); let (_, reply) = queue.offer(FIRST.into(), now).unwrap();
        let mut sent = 0;
        if !sink_first { assert!(ready(&mut queue, "Y:1")); }
        let phase = queue.flush(now, |_| { sent += 1; if sink_first { Some(true) } else { None } });
        assert_eq!(phase, Some(if sink_first { Phase::WaitingCallback } else { Phase::WaitingSink }));
        assert!(reply.try_recv().is_err()); assert_eq!(sent, if sink_first { 0 } else { 1 });
        if sink_first { assert!(ready(&mut queue, "Y:1")); }
        assert_eq!(queue.flush(now, |_| Some(true)), Some(Phase::Accepted));
        assert_eq!(reply.try_recv().unwrap(), Delivery::Accepted);
        assert_eq!(queue.flush(now, |_| panic!("must not duplicate")), None);
    }
}
#[test]
fn rejected_sink_retries_latest_once_and_superseded_timeout_cannot_cancel_newer() {
    let now = Instant::now(); let mut queue = queue(); assert!(ready(&mut queue, "Y:1"));
    let (old_id, old) = queue.offer(FIRST.into(), now).unwrap();
    assert_eq!(queue.flush(now, |_| Some(false)), Some(Phase::RejectedSink));
    let (_, new) = queue.offer(SECOND.into(), now).unwrap();
    assert_eq!(old.try_recv().unwrap(), Delivery::Superseded); queue.cancel(old_id);
    let mut calls = 0;
    assert_eq!(queue.flush(now, |uri| { assert!(uri == SECOND); calls += 1; Some(true) }), Some(Phase::Accepted));
    assert_eq!(new.try_recv().unwrap(), Delivery::Accepted); assert_eq!(calls, 1);
}
#[test]
fn expiry_stop_and_late_ready_epochs_cannot_revive_a_stale_intent() {
    let now = Instant::now(); let mut queue = queue();
    let (_, expired) = queue.offer(FIRST.into(), now).unwrap();
    assert_eq!(queue.flush(now + PENDING_TTL, |_| panic!("expired URI must not dispatch")), Some(Phase::Expired));
    assert_eq!(expired.try_recv().unwrap(), Delivery::Expired);
    assert!(ready(&mut queue, "N:3")); assert!(!ready(&mut queue, "Y:2")); assert!(!ready(&mut queue, "Y:3"));
    for raw in ["Y", "Y:0", "X:4", "Y:-1", "Y:18446744073709551616"] { assert!(!ready(&mut queue, raw)); }
    let (_, pending) = queue.offer(FIRST.into(), now).unwrap(); queue.stop();
    assert_eq!(pending.try_recv().unwrap(), Delivery::Unavailable);
    assert_eq!(queue.flush(now, |_| panic!("stopped URI must not dispatch")), None);
    assert!(!ready(&mut queue, "Y:4"));
    assert!(queue.begin_stream(GEN2));
    assert!(!ready(&mut queue, "Y:99"));
    assert!(queue.set_ready(&format!("Y:{GEN2}:1")));
    assert!(queue.stop_if_generation(GEN2));
    assert!(!queue.has_stream());
}
#[test]
fn queue_is_latest_only_bounded_and_validator_matches_form_duplicates_and_metadata() {
    assert_eq!((MAX_URL_BYTES, MAX_ACK_WORKERS, PENDING_TTL.as_millis()), (16_384, 8, 1_500));
    assert_eq!(READY_OPTION, "mixel-support-url-callback-ready-v1");
    let mut queue = queue(); let now = Instant::now();
    for _ in 0..1_000 { assert!(queue.offer(FIRST.into(), now).is_some()); }
    assert_eq!(queue.flush(now, |_| panic!("unready callback must not dispatch")), Some(Phase::WaitingCallback));
    assert!(is_valid_support_url(FIRST)); assert!(is_valid_support_url(&FIRST.to_uppercase()));
    assert!(is_valid_support_url(&FIRST.replace("apikey", "apiKey")));
    assert!(!is_valid_support_url(&(FIRST.to_owned() + "&APIKEY=too-short&apikey=synthetic-warm-key-000000000000")));
    assert!(is_valid_support_url(&(FIRST.to_owned() + "&APIKEY=synthetic-warm-key-000000000000&apikey=too-short")));
    let encoded = FIRST.replace("synthetic-first-key-000000000000", "synthetic%2Bvalid-key-000000000000"); assert!(is_valid_support_url(&encoded));
    for suffix in ["#fragment", "&apikey=too-short", "&invite=invalid", "&unused=%ZZ", "&unused=%FF"] { assert!(!is_valid_support_url(&(FIRST.to_owned() + suffix))); }
    for control in 0x7fu32..=0x9f { assert!(!is_valid_support_url(&format!("{FIRST}&unused={}", char::from_u32(control).unwrap()))); }
    for authority in ["support:123", "user@support", "support.attacker.example", "support/path"] { assert!(!is_valid_support_url(&FIRST.replace("support/", authority))); }
    assert!(!is_valid_support_url(&FIRST.replace("synthetic-first-key-000000000000", "synthetic+bad-key-000000000000")));
    assert!(is_valid_support_url(&FIRST.replace("synthetic-first-key-000000000000", &"%21".repeat(4_096))));
    assert!(!is_valid_support_url(&FIRST.replace("synthetic-first-key-000000000000", &"a".repeat(4_097))));
    assert!(!is_valid_support_url(&(FIRST.to_owned() + "&unused=" + &"a".repeat(16_384))));
    assert!(!accepted_ack(ACK_NAME, None)); assert!(!accepted_ack("wrong", Some(ACK_ACCEPTED))); assert!(!accepted_ack(ACK_NAME, Some(ACK_REJECTED)));
    assert!(stream_generation(&format!("main,{STREAM_PREFIX}{GEN1}")) == Some(GEN1));
    for raw in ["main", "cm,mixel-support-url:11111111111111111111111111111111", "main,mixel-support-url:bad", "main,mixel-support-url:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"] { assert!(stream_generation(raw).is_none()); }
    assert!(queue.has_stream()); assert!(!queue.stop_if_generation(GEN2));
    // Exercise the real singleton without an app, service or socket.
    let mut singleton = global_queue().lock().unwrap(); singleton.stop(); assert!(singleton.begin_stream(GEN1)); assert!(ready(&mut singleton, "N:1"));
}
'''
    queue_source = (directory / "src/mixel_support_url.rs").read_text() + r"""
#[cfg(test)] mod exhaustion {
    use super::*;
    #[test] fn maximum_counter_never_wraps_or_overwrites_pending() {
        let mut queue = Queue::new(); let now = Instant::now();
        let url = "mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000001&apikey=synthetic-first-key-000000000000".to_owned();
        queue.next_id = u64::MAX - 1;
        let (id, reply) = queue.offer(url.clone(), now).unwrap(); assert_eq!(id, u64::MAX);
        assert!(queue.offer(url, now).is_none()); assert_eq!(queue.pending.as_ref().unwrap().id, u64::MAX);
        assert!(reply.try_recv().is_err()); queue.cancel(id); assert_eq!(reply.try_recv().unwrap(), Delivery::Expired);
    }
}
"""
    queue_target = directory / "queue_fixture.rs"; queue_target.write_text(queue_source)
    fixture = fixture.replace("QUEUE_PATH", str(queue_target).replace("\\", "\\\\"))
    fixture = fixture.replace("ORIGINAL_RECEIVER", receiver).replace("BASELINE_SENDER", baseline).replace("GENERATED_SENDER", sender)
    target = directory / "native_mac_url_ack.rs"
    target.write_text(fixture, encoding="utf-8")
    binary = directory / ("native_mac_url_ack.exe" if os.name == "nt" else "native_mac_url_ack")
    subprocess.run(rustc_command([os.environ.get("RUSTC_BIN", "rustc"), "--edition=2021", "--deny=warnings", "--test", str(target), "-o", str(binary)]), check=True)
    subprocess.run([str(binary), "--test-threads=1"], check=True)



def rust_lifecycle_fixture(directory, generated):
    flutter = generated["src/flutter.rs"]
    push = between(flutter, "pub fn push_global_event(", "#[inline]\npub fn get_global_event_channels")
    lifecycle = between(flutter, "pub fn start_global_event_stream(", "#[inline]\nfn session_send_touch_scale")
    lifecycle = lifecycle.replace('    #[cfg(target_os = "macos")]\n', '')
    flush = between(generated["src/server.rs"], "pub(crate) fn flush_mixel_support_url()", '#[cfg(all(target_os = "macos", feature = "flutter"))]\npub(crate) fn set_mixel_support_url_ready')
    source = r"""
use std::collections::HashMap;
use std::sync::{Arc, OnceLock, RwLock};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;
type ResultType<T> = Result<T, &'static str>;
#[macro_export] macro_rules! fixed_warn { ($($value:tt)*) => {{ () }}; }
mod log { pub(crate) use super::fixed_warn as warn; }
mod serde_json { pub fn to_string(_: &std::collections::HashMap<&str, &str>) -> Result<String, ()> { Ok(String::new()) } }
mod server {
    use super::{HashMap, serde_json};
    #[allow(dead_code)] #[path = "QUEUE_PATH"] pub(crate) mod mixel_support_url;
    FLUSH
}
pub(crate) struct StreamSink<T> { id: usize, accepts: bool, count: Arc<AtomicUsize>, _type: std::marker::PhantomData<T> }
impl<T> StreamSink<T> {
    fn add(&self, _: T) -> bool { if self.accepts { self.count.fetch_add(1, Ordering::SeqCst); } self.accepts }
}
mod flutter {
    use super::*;
    pub const APP_TYPE_MAIN: &str = "main";
    // Match the production lazy static's dereference semantics using the
    // oldest CI compiler's stable OnceLock; generated lifecycle bodies stay exact.
    pub(crate) struct GlobalStreams;
    impl std::ops::Deref for GlobalStreams {
        type Target = RwLock<HashMap<String, StreamSink<String>>>;
        fn deref(&self) -> &Self::Target {
            static STREAMS: OnceLock<RwLock<HashMap<String, StreamSink<String>>>> = OnceLock::new();
            STREAMS.get_or_init(|| RwLock::new(HashMap::new()))
        }
    }
    pub(crate) static GLOBAL_EVENT_STREAM: GlobalStreams = GlobalStreams;
    PUSH
    LIFECYCLE
}
use server::mixel_support_url::*;
const GEN1: &str = "11111111111111111111111111111111";
const GEN2: &str = "22222222222222222222222222222222";
const FIRST: &str = "mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000001&apikey=synthetic-first-key-000000000000";
fn kind(gen: &str) -> String { format!("main,{STREAM_PREFIX}{gen}") }
fn sink(id: usize, accepts: bool) -> (StreamSink<String>, Arc<AtomicUsize>) {
    let count = Arc::new(AtomicUsize::new(0));
    (StreamSink { id, accepts, count: count.clone(), _type: std::marker::PhantomData }, count)
}
fn reset() { let mut queue = global_queue().lock().unwrap(); queue.stop(); flutter::GLOBAL_EVENT_STREAM.write().unwrap().clear(); }
fn ready(gen: &str, epoch: u64) { assert!(global_queue().lock().unwrap().set_ready(&format!("Y:{gen}:{epoch}"))); }
#[test]
fn actual_primary_replacement_rejects_old_generation_updates_and_stops() {
    reset(); let (first, old_count) = sink(1, true);
    flutter::start_global_event_stream(first, kind(GEN1)).unwrap(); ready(GEN1, 99);
    let (second, count) = sink(2, true); flutter::start_global_event_stream(second, kind(GEN2)).unwrap();
    assert!(!global_queue().lock().unwrap().set_ready(&format!("Y:{GEN1}:100")));
    let now = Instant::now(); let (_, reply) = global_queue().lock().unwrap().offer(FIRST.into(), now).unwrap();
    server::flush_mixel_support_url(); assert!(reply.try_recv().is_err());
    flutter::stop_global_event_stream(kind(GEN1));
    flutter::stop_global_event_stream("main".into()); // identityless legacy stop cannot pause a qualified replacement
    assert_eq!(flutter::GLOBAL_EVENT_STREAM.read().unwrap().get("main").unwrap().id, 2);
    ready(GEN2, 1); server::flush_mixel_support_url();
    assert_eq!(reply.try_recv().unwrap(), Delivery::Accepted);
    assert_eq!((old_count.load(Ordering::SeqCst), count.load(Ordering::SeqCst)), (0, 1));
    let (_, pending) = global_queue().lock().unwrap().offer(FIRST.into(), now).unwrap();
    flutter::stop_global_event_stream(kind(GEN2)); assert_eq!(pending.try_recv().unwrap(), Delivery::Unavailable);
    assert!(flutter::GLOBAL_EVENT_STREAM.read().unwrap().get("main").is_none());
    assert!(!global_queue().lock().unwrap().set_ready(&format!("Y:{GEN2}:2")));
    let (fresh, _) = sink(3, true); flutter::start_global_event_stream(fresh, kind(GEN1)).unwrap(); ready(GEN1, 1);
}
#[test]
fn actual_queue_stream_lock_order_serializes_late_stop_with_new_registration() {
    for _ in 0..100 {
        reset(); let (old, _) = sink(1, true); flutter::start_global_event_stream(old, kind(GEN1)).unwrap(); ready(GEN1, 1);
        let barrier = Arc::new(std::sync::Barrier::new(2));
        let first = barrier.clone(); let second = barrier.clone();
        let start = std::thread::spawn(move || { first.wait(); let (new, _) = sink(2, true); flutter::start_global_event_stream(new, kind(GEN2)).unwrap(); });
        let stop = std::thread::spawn(move || { second.wait(); flutter::stop_global_event_stream(kind(GEN1)); });
        start.join().unwrap(); stop.join().unwrap();
        assert_eq!(flutter::GLOBAL_EVENT_STREAM.read().unwrap().get("main").unwrap().id, 2); ready(GEN2, 1);
    }
}
#[test]
fn actual_rejected_sink_retains_until_poll_recovers_or_ttl_expires() {
    reset(); let (bad, _) = sink(1, false); flutter::start_global_event_stream(bad, kind(GEN1)).unwrap(); ready(GEN1, 1);
    let now = Instant::now(); let (_, reply) = global_queue().lock().unwrap().offer(FIRST.into(), now).unwrap();
    server::flush_mixel_support_url(); assert!(reply.try_recv().is_err());
    let (good, count) = sink(2, true); flutter::start_global_event_stream(good, kind(GEN2)).unwrap();
    // The generated existing one-second UI callback emits this new-generation
    // ready refresh. Execute its queue effect before the 1.5-second TTL.
    ready(GEN2, 1);
    assert_eq!(global_queue().lock().unwrap().flush(now + std::time::Duration::from_secs(1), |event| flutter::push_global_event("main", event.into())), Some(Phase::Accepted));
    assert_eq!(reply.try_recv().unwrap(), Delivery::Accepted); assert_eq!(count.load(Ordering::SeqCst), 1);
    let (_, expired) = global_queue().lock().unwrap().offer(FIRST.into(), now).unwrap();
    assert_eq!(global_queue().lock().unwrap().flush(now + PENDING_TTL, |event| flutter::push_global_event("main", event.into())), Some(Phase::Expired));
    assert_eq!(expired.try_recv().unwrap(), Delivery::Expired); assert_eq!(count.load(Ordering::SeqCst), 1);
}
#[test]
fn actual_unqualified_legacy_and_nonmain_stream_stops_preserve_original_semantics() {
    reset(); let (legacy, count) = sink(1, true); flutter::start_global_event_stream(legacy, "main".into()).unwrap();
    assert_eq!(flutter::push_global_event("main", String::new()), Some(true)); assert_eq!(count.load(Ordering::SeqCst), 1);
    flutter::stop_global_event_stream("main".into()); assert_eq!(flutter::push_global_event("main", String::new()), None);
    for name in ["cm", "remote,9"] { let (other, _) = sink(2, true); flutter::start_global_event_stream(other, name.into()).unwrap(); flutter::stop_global_event_stream(name.into()); assert!(flutter::GLOBAL_EVENT_STREAM.read().unwrap().get(name).is_none()); }
}
"""
    source = source.replace("QUEUE_PATH", str(directory / "src/mixel_support_url.rs").replace("\\", "\\\\"))
    source = source.replace("FLUSH", flush).replace("PUSH", push).replace("LIFECYCLE", lifecycle)
    target = directory / "mac_actual_stream_lifecycle.rs"; target.write_text(source)
    binary = directory / ("mac_actual_stream_lifecycle.exe" if os.name == "nt" else "mac_actual_stream_lifecycle")
    subprocess.run(rustc_command([os.environ.get("RUSTC_BIN", "rustc"), "--edition=2021", "--deny=warnings", "--test", str(target), "-o", str(binary)]), check=True)
    subprocess.run([str(binary), "--test-threads=1"], check=True)

def dart_fixture(directory, generated):
    helpers = between(generated["flutter/lib/common.dart"], "// Mixel Mac support forwarding readiness.", "// Mixel support invite handoff:")
    native = generated["flutter/lib/models/native_model.dart"]
    methods = between(native, "  void _clearMixelSupportUrlPending(", "  void setRgbaCallback(")
    baseline = between(original("flutter/lib/models/native_model.dart"), "  void _startListenEvent(", "  void setRgbaCallback(")
    common = generated["flutter/lib/common.dart"]
    parser_start = common.index("  if (uri.scheme.toLowerCase() == 'mixel-remote' && uri.host.toLowerCase() == 'support') {")
    parser = "List<String>? urlLinkToCmdArgs(Uri uri) {\n" + between(common, common[parser_start:parser_start + 100], "  } else if (uri.authority.isEmpty &&") + "  }\n  return null;\n}\n"
    valid = between((ROOT / "scripts/support-invite-reporter.dart").read_text(), "  static bool validInvite(", "  Future<void> run(")
    home = generated["flutter/lib/desktop/pages/desktop_home_page.dart"]
    poll_body = home.split("_updateTimer = periodic_immediate(const Duration(seconds: 1), () async {", 1)[1]
    poll = next(line for line in poll_body.splitlines() if line.strip() and not line.strip().startswith("//"))
    assert poll.strip() == "setMixelSupportUrlUiReady(true);"
    source = r'''
import 'dart:async';
import 'dart:convert';
import 'dart:math';
const isMacOS = true;
enum DesktopType { main, connectionManager, remote }
DesktopType desktopType = DesktopType.main;
const kAppTypeDesktopRemote = 'remote';
const kWindowId = 1;
typedef StreamEventHandler = Future<void> Function(Map<String, dynamic>);
final output = <String>[];
void debugPrint(String value) { output.add(value); }
class FakeBind {
  final values = <String>[];
  Future<void> mainSetOption({required String key, required String value}) async {
    if (key != 'mixel-support-url-callback-ready-v1') throw StateError('unrelated option');
    values.add(value);
  }
}
final bind = FakeBind();
class RustdeskImpl {
  final stream = StreamController<String>();
  String? actualAppType;
  Stream<String> startGlobalEventStream({required String appType}) { actualAppType = appType; return stream.stream; }
}
class MixelSupportInviteReporter { VALID }
PARSER
HELPERS
void actualExistingUiPoll() { POLL }
class ActualNative {
  static bool isMain = true;
  String _appType = 'main';
  StreamEventHandler? _eventCallback;
  Map<String, dynamic>? _mixelSupportUrlPendingEvent;
  Stopwatch? _mixelSupportUrlPendingAge;
  String? _mixelSupportUrlPendingGeneration;
  Completer<bool>? handlingBarrier;
  Future<bool> tryHandle(Map<String, dynamic> event) async => handlingBarrier?.future ?? false;
  METHODS
}
class OriginalNative {
  String _appType = 'main';
  StreamEventHandler? _eventCallback;
  Future<bool> tryHandle(Map<String, dynamic> event) async => false;
  BASELINE
}
Map<String, dynamic> event(int value) => {'name': 'on_url_scheme_received',
  'url': 'mixel-remote://support/?invite=inv_00000000-0000-0000-0000-${value.toString().padLeft(12, '0')}&apikey=synthetic-support-key-000000000000'};
Future<void> settle() async { await Future<void>.delayed(const Duration(milliseconds: 10)); }
void check(bool value, String label) { if (!value) throw StateError(label); }
Future<void> main() async {
  var originalDeliveries = 0;
  final original = OriginalNative(); final originalCore = RustdeskImpl(); original._startListenEvent(originalCore);
  originalCore.stream.add(jsonEncode(event(1))); await settle();
  original.setEventCallback((_) async { originalDeliveries++; }); await settle();
  check(originalDeliveries == 0, 'baseline negative callback-null drop not reproduced');
  await originalCore.stream.close();
  print('PASS negative actual original Dart dispatch drops support event while callback is null');
  final base = event(1)['url'] as String;
  var parityIndex = 0;
  for (final sample in <String,bool>{
    base: true,
    base.replaceFirst('apikey', 'apiKey'): true,
    '$base&APIKEY=too-short&apikey=synthetic-warm-key-000000000000': false,
    '$base&APIKEY=synthetic-warm-key-000000000000&apikey=too-short': true,
    '$base&unused=%ZZ': false,
    '$base&unused=%FF': false,
    '$base#fragment': false,
    base.replaceFirst('synthetic-support-key', 'synthetic+support-key'): false,
    base.replaceFirst('synthetic-support-key', 'synthetic%2Bsupport-key'): true,
  }.entries) {
    check(isMixelSupportUrlEvent({'name': 'on_url_scheme_received', 'url': sample.key}) == sample.value, 'actual Dart validation parity failed at ${parityIndex++}');
  }
  for (var control = 127; control <= 159; control++) {
    final raw = '$base&unused=${String.fromCharCode(control)}';
    check(!isMixelSupportUrlEvent({'name': 'on_url_scheme_received', 'url': raw}), 'actual Dart accepted raw DEL/C1 control $control');
  }
  final native = ActualNative(); final core = RustdeskImpl(); native._startListenEvent(core);
  await settle(); check(bind.values.every((v) => v.startsWith('N:')), 'null callback incorrectly ready');
  native.setEventCallback((_) async {}); await settle(); check(bind.values.last.startsWith('N:'), 'absent UI incorrectly ready');
  setMixelSupportUrlUiReady(true); await settle(); check(bind.values.last.startsWith('Y:'), 'attached callback+stream+UI not ready');
  native.setEventCallback(null); await settle(); check(bind.values.last.startsWith('N:'), 'cleared callback retained readiness');
  final delivered = <Map<String, dynamic>>[];
  core.stream.add(jsonEncode(event(1))); core.stream.add(jsonEncode(event(2))); await settle();
  check(native._mixelSupportUrlPendingEvent?['url'] == event(2)['url'], 'callback gap did not retain latest valid intent');
  for (final invalid in [<String,dynamic>{'name': 'ordinary', 'url': event(1)['url']},
      <String,dynamic>{'name': 'on_url_scheme_received', 'url': 'mixel-remote://support/?invite=invalid&apikey=too-short'}]) {
    core.stream.add(jsonEncode(invalid));
  }
  await settle(); check(native._mixelSupportUrlPendingEvent?['url'] == event(2)['url'], 'ordinary/invalid event changed retained support');
  check(native._mixelSupportUrlPendingEvent!.length == 2, 'retained unexpected metadata');
  native.setEventCallback((value) async { delivered.add(value); }); await settle();
  check(delivered.length == 1 && delivered.single['url'] == event(2)['url'], 'latest retained intent not delivered exactly once');
  actualExistingUiPoll(); await settle(); check(delivered.length == 1, 'poll duplicated delivered intent');
  native.setEventCallback(null); core.stream.add(jsonEncode(event(1))); await settle();
  await Future<void>.delayed(const Duration(milliseconds: 1500));
  actualExistingUiPoll(); await settle(); check(native._mixelSupportUrlPendingEvent == null, 'existing UI poll did not reap expired storage');
  native.setEventCallback((value) async { delivered.add(value); }); await settle();
  check(delivered.length == 1 && native._mixelSupportUrlPendingEvent == null, 'expired Dart intent revived');
  native.setEventCallback(null); core.stream.add(jsonEncode(event(1))); await settle();
  setMixelSupportUrlUiReady(false); await settle(); check(native._mixelSupportUrlPendingEvent == null, 'UI disposal retained Dart URI');
  core.stream.add(jsonEncode(event(1))); await settle(); check(native._mixelSupportUrlPendingEvent == null, 'disposed UI retained new frame');
  setMixelSupportUrlUiReady(true);
  native.setEventCallback((_) async { throw StateError('synthetic-sensitive-exception'); });
  core.stream.add(jsonEncode(event(1)));
  await settle(); check(output.contains('Mixel support URL dispatch failed.'), 'support failure not redacted');
  check(!output.any((v) => v.contains('synthetic-sensitive-exception')), 'support exception leaked');
  setMixelSupportUrlUiReady(false); await settle(); check(bind.values.last.startsWith('N:'), 'disposed UI retained readiness');
  actualExistingUiPoll(); await settle(); check(bind.values.last.startsWith('Y:'), 'existing UI poll did not resume callback');
  final firstGeneration = mixelSupportUrlGeneration;
  native.setEventCallback(null); core.stream.add(jsonEncode(event(1))); await settle();
  check(native._mixelSupportUrlPendingEvent != null, 'replacement fixture did not start with a pending event');
  final barrier = Completer<bool>(); native.handlingBarrier = barrier;
  core.stream.add(jsonEncode(event(4))); await settle();
  final replacement = RustdeskImpl(); native._startListenEvent(replacement); await settle();
  barrier.complete(false); native.handlingBarrier = null; await settle();
  check(native._mixelSupportUrlPendingEvent == null && delivered.length == 1, 'old frame awaiting handler lookup revived under new generation');
  check(native._mixelSupportUrlPendingEvent == null, 'new stream retained old intent');
  native.setEventCallback((value) async { delivered.add(value); }); await settle();
  final secondGeneration = mixelSupportUrlGeneration;
  check(firstGeneration != secondGeneration && secondGeneration.length == 32, 'same-isolate replacement did not change generation');
  check(replacement.actualAppType == 'main,mixel-support-url:$secondGeneration', 'replacement stream not qualified');
  actualExistingUiPoll(); await settle(); check(bind.values.last.startsWith('Y:$secondGeneration:'), 'new generation not ready');
  final beforeLate = bind.values.length;
  core.stream.add(jsonEncode(event(3))); await settle();
  check(delivered.length == 1 && native._mixelSupportUrlPendingEvent == null, 'old stream frame revived stale intent');
  core.stream.addError(StateError('old-stream-failure')); await settle(); await core.stream.close(); await settle();
  check(bind.values.length == beforeLate, 'late old onError/onDone paused replacement');
  native.setEventCallback(null); replacement.stream.add(jsonEncode(event(1))); await settle();
  replacement.stream.addError(StateError('synthetic-stream-failure')); await settle(); check(bind.values.last.startsWith('N:'), 'rejected stream retained readiness');
  check(native._mixelSupportUrlPendingEvent == null, 'rejected stream retained intent');
  await replacement.stream.close(); await settle(); check(bind.values.last.startsWith('N:$secondGeneration:'), 'closed replacement stream retained readiness');
  final count = bind.values.length;
  ActualNative.isMain = false; desktopType = DesktopType.connectionManager;
  final cm = ActualNative(); final cmCore = RustdeskImpl(); cm._startListenEvent(cmCore); cm.setEventCallback((_) async {}); setMixelSupportUrlUiReady(true);
  await settle(); await cmCore.stream.close(); await settle(); check(bind.values.length == count, 'CM published MAIN readiness');
  desktopType = DesktopType.remote; setMixelSupportUrlUiReady(true); await settle(); check(bind.values.length == count, 'secondary UI published MAIN readiness');
  final epochs = <String, int>{};
  for (final value in bind.values) {
    final parts = value.split(':'); check(parts.length == 3, 'invalid readiness frame');
    final epoch = int.parse(parts.last); check(epoch > (epochs[parts[1]] ?? 0), 'per-stream epoch not increasing');
    epochs[parts[1]] = epoch;
  }
  check(epochs.length == 2, 'replacement generation lifecycle not exercised');
  check(cmCore.actualAppType == 'main', 'CM stream unexpectedly qualified');
  final oldGeneration = mixelSupportUrlGeneration;
  check(mixelSupportUrlMainStreamType('remote') == 'remote' && mixelSupportUrlGeneration == oldGeneration, 'secondary created MAIN randomness');
  print('PASS actual generated MAIN callback-null/UI-gap/dispose/poll/same-isolate stream replacement, stale onError/onDone exclusion, per-generation epochs, CM/secondary exclusion and fixed support error redaction');
}
'''.replace("HELPERS", helpers).replace("POLL", poll).replace("METHODS", methods).replace("BASELINE", baseline).replace("VALID", valid).replace("PARSER", parser)
    target = directory / "mac_callback_readiness.dart"
    target.write_text(source, encoding="utf-8")
    dart = find_dart()
    if not dart: raise SystemExit("Dart SDK required")
    subprocess.run([dart, "analyze", str(target)], check=True)
    subprocess.run([dart, str(target)], check=True)
    # Execute the same generated native listener on an ordinary platform. No
    # Mac callback/stream state, random generation or onError interception runs.
    nonmac = source.replace('const isMacOS = true;', 'const isMacOS = false;', 1)
    nonmac = nonmac[:nonmac.index('Future<void> main() async {')] + r"""
Future<void> main() async {
  var actualErrors = 0, originalErrors = 0, actualEvents = 0, originalEvents = 0;
  await runZonedGuarded(() async {
    final native = ActualNative(); final core = RustdeskImpl();
    native._startListenEvent(core); native.setEventCallback((_) async { actualEvents++; });
    core.stream.add(jsonEncode(event(1))); core.stream.addError(StateError('synthetic-ordinary-error'));
    await settle(); await core.stream.close(); await settle();
    check(core.actualAppType == 'main', 'ordinary platform qualified stream');
  }, (Object _, StackTrace __) { actualErrors++; });
  await runZonedGuarded(() async {
    final native = OriginalNative(); final core = RustdeskImpl();
    native._startListenEvent(core); native.setEventCallback((_) async { originalEvents++; });
    core.stream.add(jsonEncode(event(1))); core.stream.addError(StateError('synthetic-ordinary-error'));
    await settle(); await core.stream.close(); await settle();
  }, (Object _, StackTrace __) { originalErrors++; });
  check(actualErrors == 1 && actualErrors == originalErrors && actualEvents == 1 && actualEvents == originalEvents, 'ordinary dispatch or zone errors changed');
  check(bind.values.isEmpty && mixelSupportUrlGeneration.isEmpty, 'ordinary platform published readiness or created random generation');
  print('PASS actual generated non-Mac stream event/zone-error behavior matches original; no readiness command or random generation');
}
"""
    target = directory / 'ordinary_callback_readiness.dart'; target.write_text(nonmac, encoding='utf-8')
    subprocess.run([dart, 'analyze', str(target)], check=True)
    subprocess.run([dart, str(target)], check=True)


def main():
    tree = ast.parse((ROOT / "scripts/test-support-invite.py").read_text(encoding="utf-8"))
    targets = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "targets" for t in node.targets))
    with tempfile.TemporaryDirectory(prefix="mixel-private-mac-url-ack-") as temp:
        directory = Path(temp)
        for name in targets:
            path = directory / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(original(name), encoding="utf-8")
        subprocess.run([sys.executable, str(ROOT / "scripts/patch-support-invite.py")], env={**os.environ, "RDREPO": str(directory)}, check=True)
        generated = {name: (directory / name).read_text(encoding="utf-8") for name in targets}
        receiver = generated["src/server.rs"]
        owned = between(receiver, '                            if mixel_support_url::is_valid_support_url(&url)', '                            let mut m = HashMap::new();')
        assert owned.index("hold_support_invite_attended_lease()") < owned.index(".offer(url,") < owned.index("tokio::spawn(")
        assert "try_acquire_owned()" in owned and "PENDING_TTL" in owned and ".cancel(id)" in owned
        assert "Duration::from_millis(25)" in owned
        # Ordinary receiver statements and send-only tail are byte-for-byte.
        old_receiver = between(original("src/server.rs"), "                            let mut m = HashMap::new();", "                        }\n                        _ =>")
        assert old_receiver in receiver
        old_sender_tail = between(original("src/ipc.rs"), '    connect(1_000, "_url")', '// Emit `close` events')
        assert old_sender_tail in generated["src/ipc.rs"]
        setter = between(generated["src/ui_interface.rs"], '    #[cfg(all(target_os = "macos", feature = "flutter"))]\n    if key == crate::server::mixel_support_url::READY_OPTION', '    if &key == "stop-service"')
        assert "return;" in setter and all(word not in setter for word in ("OPTIONS.lock", "Config::set_option", "ipc::"))
        assert "drop(lock);" in generated["src/flutter.rs"] and "stop_if_generation(generation)" in generated["src/flutter.rs"]
        assert "Debug" not in between((directory / "src/mixel_support_url.rs").read_text(), "struct Pending {", "pub(crate) struct Queue") .split('// The URI', 1)[0]
        rust_fixture(directory, generated)
        rust_lifecycle_fixture(directory, generated)
        dart_fixture(directory, generated)
        hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in ("src/server.rs", "src/ipc.rs", "src/flutter.rs", "src/ui_interface.rs", "src/mixel_support_url.rs", "flutter/lib/common.dart", "flutter/lib/models/native_model.dart", "flutter/lib/desktop/pages/desktop_home_page.dart")}
        print("PASS source assertions: lease-before-queue, bounded8 workers/latest16KiB/1.5s expiry, cleanup, runtime-only interception, unchanged ordinary receiver/sender bytes")
        print("Generated source SHA256: " + str(hashes))


if __name__ == "__main__": main()
