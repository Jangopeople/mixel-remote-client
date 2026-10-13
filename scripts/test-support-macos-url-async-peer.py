#!/usr/bin/env python3
"""Run exact Mac receiver/queue/codec/Connection code over anonymous Tokio peers."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get('RDREPO', ROOT / 'rustdesk'))


def original(path):
    repo = UPSTREAM
    if path.startswith('libs/hbb_common/'):
        repo = repo / 'libs/hbb_common'
        path = path.removeprefix('libs/hbb_common/')
    return subprocess.check_output(['git', '-C', str(repo), 'show', 'HEAD:' + path]).decode()


def between(text, start, end):
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


def prepare(directory):
    tree = ast.parse((ROOT / 'scripts/test-support-invite.py').read_text())
    targets = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'targets' for t in node.targets))
    generated = directory / 'generated'
    for name in targets:
        path = generated / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(original(name), encoding='utf-8')
    subprocess.run([sys.executable, str(ROOT / 'scripts/patch-support-invite.py')], env={**os.environ, 'RDREPO': str(generated)}, check=True)
    server = (generated / 'src/server.rs').read_text()
    # End precisely at the helper's closing brace; keep its entire body.
    begin = server.index('#[cfg(all(target_os = "macos", feature = "flutter"))]\n#[path = "mixel_support_url.rs"]')
    ack = server.index('async fn send_mixel_support_url_ack(', begin)
    end = server.index('\n}\n', ack) + 3
    original_helpers = server[begin:end]
    helpers = original_helpers
    # Mac keeps the exact product cfg attributes. Linux CI relaxes only these
    # fixture attributes to compile the owned Mac branch; product files stay exact.
    if sys.platform == 'linux':
        helpers = helpers.replace('#[cfg(all(target_os = "macos", feature = "flutter"))]', '#[cfg(feature = "flutter")]')
    receiver = between(server, 'pub async fn start_ipc_url_server() {', '#[cfg(any(target_os = "macos", target_os = "linux"))]\nasync fn wait_initial_config_sync')
    # Its production entry wrapper creates a current-thread runtime. The tests
    # provide that same runtime, so only the attribute outside this body differs.
    assert 'tokio::spawn(async move {' in receiver and 'try_acquire_owned()' in receiver
    assert 'Duration::from_millis(25)' in receiver and '.cancel(id)' in receiver
    ipc = (generated / 'src/ipc.rs').read_text()
    # This fixture narrows Data to the only two variants used by the receiver
    # and sender. Refuse drift in the original representation or payloads.
    data_attributes = '#[derive(Debug, Serialize, Deserialize, Clone)]\n#[serde(tag = "t", content = "c")]\npub enum Data {'
    assert data_attributes in ipc, 'production Data serde attributes changed'
    data = between(ipc, 'pub enum Data {', '\n}\n')
    for declaration in ('    Config((String, Option<String>)),', '    UrlLink(String),'):
        assert data.count(declaration + '\n') == 1, 'production retained Data payload changed'
        previous = data[:data.index(declaration)].rstrip().rsplit('\n', 1)[-1].strip()
        assert not previous.startswith('#['), 'retained Data variant acquired attributes'
    variants = re.findall(r'^    ([A-Za-z_]\w*)\s*(?:\(|\{|,)', data, flags=re.MULTILINE)
    connection = between(ipc, 'pub struct ConnectionTmpl<T> {', '    async fn send_config(')
    connection += between(ipc, '    pub async fn next_timeout(', '    pub async fn next_timeout2(')
    connection += between(ipc, '    pub async fn next(&mut self)', '    pub async fn send_raw(') + '}\n'
    original_sender = between(ipc, 'pub async fn send_url_scheme(', '// Emit `close` events')
    sender = original_sender
    if sys.platform == 'linux':
        sender = sender.replace('#[cfg(all(target_os = "macos", feature = "flutter"))]', '#[cfg(feature = "flutter")]', 1)
    codec = original('libs/hbb_common/src/bytes_codec.rs')
    clock = between(original('libs/hbb_common/src/lib.rs'), 'pub fn timeout<T:', 'pub type ResultType')
    fixture = directory / 'peer-crate'
    source = fixture / 'src'
    source.mkdir(parents=True, exist_ok=True)
    (fixture / 'Cargo.toml').write_text('''[package]
name = "mixel-macos-url-async-peer-fixture"
version = "0.0.0"
edition = "2021"
rust-version = "1.75"

[features]
default = ["flutter"]
flutter = []

[dependencies]
anyhow = "=1.0.98"
bytes = "=1.10.1"
futures-util = { version = "=0.3.31", features = ["sink"] }
log = "=0.4.27"
serde = { version = "=1.0.228", features = ["derive"] }
serde_json = "=1.0.118"
tokio = { version = "=1.44.2", features = ["rt", "macros", "net", "sync", "time", "io-util"] }
tokio-util = { version = "=0.7.15", features = ["codec"] }
''')
    (fixture / 'Cargo.lock').write_bytes((ROOT / 'scripts/macos-url-async-peer.Cargo.lock').read_bytes())
    (source / 'bytes_codec.rs').write_text(codec)
    (source / 'mixel_support_url.rs').write_bytes((generated / 'src/mixel_support_url.rs').read_bytes())
    (source / 'server.rs').write_text('''use std::{collections::HashMap, sync::Arc, time::Duration};
use futures_util::StreamExt;
use crate::{ipc::Data, timeout};
''' + helpers + '\n' + receiver)
    (source / 'ipc.rs').write_text('''use anyhow::bail;
use futures_util::{SinkExt, StreamExt};
use tokio::{io::{AsyncRead, AsyncWrite}, net::UnixStream as Conn};
use tokio_util::codec::Framed;
use crate::{bytes_codec::BytesCodec, timeout, ResultType};
pub use crate::Data;
''' + connection + sender + '''
pub struct Incoming { pub receiver: tokio::sync::mpsc::UnboundedReceiver<ResultType<Conn>> }
impl futures_util::Stream for Incoming {
    type Item = ResultType<Conn>;
    fn poll_next(mut self: std::pin::Pin<&mut Self>, context: &mut std::task::Context<'_>) -> std::task::Poll<Option<Self::Item>> {
        self.receiver.poll_recv(context)
    }
}
pub async fn new_listener(postfix: &str) -> ResultType<Incoming> {
    assert_eq!(postfix, "_url");
    Ok(Incoming { receiver: crate::incoming_slot().lock().unwrap().take().ok_or_else(|| anyhow::anyhow!("private receiver missing"))? })
}
pub async fn connect(deadline: u64, postfix: &str) -> ResultType<Connection> {
    assert_eq!((deadline, postfix), (1_000, "_url"));
    Ok(Connection::new(crate::connect_slot().lock().unwrap().take().ok_or_else(|| anyhow::anyhow!("private peer missing"))?))
}
''')
    library = FIXTURE.replace('EXACT_TIMEOUT', clock)
    (source / 'lib.rs').write_text(library)
    source_files = [fixture / 'Cargo.toml', fixture / 'Cargo.lock', *generated.rglob('*'), *source.rglob('*')]
    hashes = {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(source_files) if path.is_file()}
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    provenance = {
        'upstreamCommit': subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip(),
        'hbbCommonCommit': subprocess.check_output(['git', '-C', str(UPSTREAM / 'libs/hbb_common'), 'rev-parse', 'HEAD'], text=True).strip(),
        'hostPlatform': sys.platform,
        'cfgRelaxation': 'Linux fixture only: Mac+flutter cfg becomes flutter' if sys.platform == 'linux' else 'none: exact original Mac cfg attributes',
        'productionAndFixtureBodyHashes': {
            'originalHelpers': digest(original_helpers), 'fixtureHelpers': digest(helpers),
            'originalSender': digest(original_sender), 'fixtureSender': digest(sender),
            'receiver': digest(receiver), 'connection': digest(connection),
            'codec': digest(codec), 'timeout': digest(clock),
            'originalFullDataEnum': digest(data),
        },
        'dataEnumSubset': {
            'originalSerdeAttributes': '#[serde(tag = "t", content = "c")]',
            'retainedExactPayloads': ['Config((String, Option<String>))', 'UrlLink(String)'],
            'omittedUnrelatedVariants': [variant for variant in variants if variant not in ('Config', 'UrlLink')],
            'fullDataTypeIsNotClaimedExact': True,
        },
        'generatedAndFixtureFiles': hashes,
        'seams': ['anonymous UnixStream pairs', 'private mpsc listener adapter', 'controlled attended-lease result', 'in-memory MAIN sink', 'Data enum retains only exact Config and UrlLink variants with exact serde representation', 'entry wrapper omitted: tests supply same current-thread Tokio runtime', 'Linux-only fixture cfg relaxation; Mac cfg attributes unchanged'],
        'exactProductionBodies': ['complete receiver including spawned worker', 'complete queue', 'complete BytesCodec', 'Connection constructor/send/next/next_timeout', 'sender send_url_scheme', 'hbb_common timeout'],
    }
    (directory / 'source-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    return fixture


FIXTURE = r'''
#![allow(dead_code)]
extern crate self as hbb_common;
pub use log;
use std::sync::{Mutex, OnceLock};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use tokio::net::UnixStream;
use tokio::sync::mpsc;
use serde::{Deserialize, Serialize};
mod bytes_codec;
mod ipc;
mod server;
pub type ResultType<T> = anyhow::Result<T>;
EXACT_TIMEOUT
#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(tag = "t", content = "c")]
pub enum Data { Config((String, Option<String>)), UrlLink(String) }
static LEASE_ALLOWED: AtomicBool = AtomicBool::new(true);
static LEASE_CALLS: AtomicUsize = AtomicUsize::new(0);
static SINK_STATE: AtomicUsize = AtomicUsize::new(0); // 0 absent, 1 rejects, 2 accepts
fn deliveries() -> &'static Mutex<Vec<u64>> { static DATA: OnceLock<Mutex<Vec<u64>>> = OnceLock::new(); DATA.get_or_init(|| Mutex::new(Vec::new())) }
fn incoming_slot() -> &'static Mutex<Option<mpsc::UnboundedReceiver<ResultType<UnixStream>>>> { static DATA: OnceLock<Mutex<Option<mpsc::UnboundedReceiver<ResultType<UnixStream>>>>> = OnceLock::new(); DATA.get_or_init(|| Mutex::new(None)) }
fn connect_slot() -> &'static Mutex<Option<UnixStream>> { static DATA: OnceLock<Mutex<Option<UnixStream>>> = OnceLock::new(); DATA.get_or_init(|| Mutex::new(None)) }
pub mod password_security {
    pub fn hold_support_invite_attended_lease() -> bool { super::LEASE_CALLS.fetch_add(1, super::Ordering::SeqCst); super::LEASE_ALLOWED.load(super::Ordering::SeqCst) }
}
mod flutter {
    pub const APP_TYPE_MAIN: &str = "main";
    pub fn push_global_event(channel: &str, event: String) -> Option<bool> {
        assert_eq!(channel, APP_TYPE_MAIN);
        match super::SINK_STATE.load(super::Ordering::SeqCst) {
            0 => None,
            1 => Some(false),
            _ => {
                let event: serde_json::Value = serde_json::from_str(&event).unwrap();
                let uri = event["url"].as_str().unwrap();
                let intent = uri.split("&").next().unwrap().rsplit('-').next().unwrap().parse::<u64>().unwrap_or(0);
                super::deliveries().lock().unwrap().push(intent);
                Some(true)
            }
        }
    }
}
#[cfg(test)] mod tests {
    use super::*;
    use bytes::{Bytes, BytesMut};
    use std::time::{Duration, Instant};
    use tokio_util::codec::Encoder;
    use server::mixel_support_url::*;
    const GEN: &str = "11111111111111111111111111111111";
    fn url(index: u64) -> String { format!("mixel-remote://support/?invite=inv_00000000-0000-0000-0000-{index:012}&apikey=synthetic-async-peer-key-000000000000") }
    fn ready() { server::set_mixel_support_url_ready(&format!("Y:{GEN}:1")); }
    fn reset() {
        let mut queue = global_queue().lock().unwrap(); queue.stop(); assert!(queue.begin_stream(GEN));
        LEASE_ALLOWED.store(true, Ordering::SeqCst); LEASE_CALLS.store(0, Ordering::SeqCst); SINK_STATE.store(0, Ordering::SeqCst); deliveries().lock().unwrap().clear();
        assert!(connect_slot().lock().unwrap().take().is_none()); assert!(incoming_slot().lock().unwrap().take().is_none());
    }
    struct Harness { input: mpsc::UnboundedSender<ResultType<UnixStream>>, task: tokio::task::JoinHandle<()> }
    impl Harness {
        fn start() -> Self {
            reset(); let (input, receiver) = mpsc::unbounded_channel(); *incoming_slot().lock().unwrap() = Some(receiver);
            Self { input, task: tokio::spawn(server::start_ipc_url_server()) }
        }
        async fn submit(&self, index: u64) -> ipc::Connection {
            let (server, client) = UnixStream::pair().unwrap(); self.input.send(Ok(server)).unwrap();
            let mut connection = ipc::Connection::new(client); connection.send(&Data::UrlLink(url(index))).await.unwrap(); connection
        }
        async fn shutdown(self) {
            drop(self.input); timeout(500, self.task).await.unwrap().unwrap();
            // Worker send timeout is 250ms. Drain every remaining receiver task
            // before this scoped current-thread runtime and its peers disappear.
            tokio::time::sleep(Duration::from_millis(300)).await;
            assert_eq!(tokio::runtime::Handle::current().metrics().num_alive_tasks(), 0);
        }
    }
    fn is_ack(data: Option<Data>, accepted: bool) -> bool {
        matches!(data, Some(Data::Config((name, Some(value)))) if name == ACK_NAME && value == if accepted { ACK_ACCEPTED } else { ACK_REJECTED })
    }
    async fn wait_lease_calls(count: usize) {
        timeout(500, async { while LEASE_CALLS.load(Ordering::SeqCst) < count { tokio::time::sleep(Duration::from_millis(1)).await; } }).await.unwrap();
    }
    #[tokio::test(flavor = "current_thread")]
    async fn real_receiver_ack_waits_for_callback_and_accepting_sink() {
        let harness = Harness::start(); let mut client = harness.submit(1).await; wait_lease_calls(1).await;
        assert!(client.next_timeout(75).await.is_err()); assert!(deliveries().lock().unwrap().is_empty());
        ready(); SINK_STATE.store(1, Ordering::SeqCst); server::flush_mixel_support_url();
        assert!(client.next_timeout(75).await.is_err()); assert!(deliveries().lock().unwrap().is_empty());
        SINK_STATE.store(2, Ordering::SeqCst); server::flush_mixel_support_url();
        assert!(is_ack(client.next_timeout(500).await.unwrap(), true)); assert_eq!(*deliveries().lock().unwrap(), vec![1]);
        assert!(client.next_timeout(50).await.is_err());
        harness.shutdown().await;
    }
    #[tokio::test(flavor = "current_thread")]
    async fn real_receiver_unavailable_lease_never_queues_or_delivers() {
        let harness = Harness::start(); ready(); SINK_STATE.store(2, Ordering::SeqCst); LEASE_ALLOWED.store(false, Ordering::SeqCst);
        let mut client = harness.submit(1).await; assert!(is_ack(client.next_timeout(500).await.unwrap(), false));
        assert_eq!(LEASE_CALLS.load(Ordering::SeqCst), 1); assert!(deliveries().lock().unwrap().is_empty());
        assert!(global_queue().lock().unwrap().flush(Instant::now(), |_| panic!("lease failure queued a URI")).is_none());
        harness.shutdown().await;
    }
    #[tokio::test(flavor = "current_thread")]
    async fn real_receiver_expiry_clears_pending_and_sends_unavailable() {
        let harness = Harness::start(); let started = Instant::now(); let mut client = harness.submit(1).await;
        assert!(is_ack(client.next_timeout(2500).await.unwrap(), false)); assert!(started.elapsed() >= PENDING_TTL);
        SINK_STATE.store(2, Ordering::SeqCst); ready(); assert!(deliveries().lock().unwrap().is_empty());
        assert!(global_queue().lock().unwrap().flush(Instant::now(), |_| panic!("expired URI survived")).is_none());
        harness.shutdown().await;
    }
    #[tokio::test(flavor = "current_thread")]
    async fn real_receiver_superseded_peer_cannot_ack_or_cancel_new_intent() {
        let harness = Harness::start(); let mut old = harness.submit(1).await; wait_lease_calls(1).await;
        let mut new = harness.submit(2).await; assert!(is_ack(old.next_timeout(500).await.unwrap(), false));
        assert!(new.next_timeout(75).await.is_err()); SINK_STATE.store(2, Ordering::SeqCst); ready();
        assert!(is_ack(new.next_timeout(500).await.unwrap(), true)); assert_eq!(*deliveries().lock().unwrap(), vec![2]);
        harness.shutdown().await;
    }
    fn fill_outbound(stream: &UnixStream) {
        // A real stalled peer can hold a worker's ACK send. Fill only this
        // anonymous pair with encoded synthetic padding frames before enqueue.
        let mut codec = bytes_codec::BytesCodec::new(); let mut encoded = BytesMut::new();
        codec.encode(Bytes::from(serde_json::to_vec(&Data::Config(("synthetic-padding".into(), Some("p".repeat(4096))))).unwrap()), &mut encoded).unwrap();
        let mut offset = 0; let mut written = 0;
        loop {
            match stream.try_write(&encoded[offset..]) {
                Ok(count) if count > 0 => { written += count; offset = (offset + count) % encoded.len(); assert!(written < 16 * 1024 * 1024, "unbounded private socket fill"); }
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => break,
                _ => panic!("private fill failed"),
            }
        }
        assert!(written > 0);
    }
    #[tokio::test(flavor = "current_thread")]
    async fn real_receiver_eight_worker_limit_rejects_ninth_and_recovers() {
        let harness = Harness::start(); let mut stalled = Vec::new();
        for index in 1..=MAX_ACK_WORKERS {
            let (server, client) = UnixStream::pair().unwrap(); server.writable().await.unwrap(); fill_outbound(&server);
            harness.input.send(Ok(server)).unwrap(); let mut peer = ipc::Connection::new(client);
            peer.send(&Data::UrlLink(url(index as u64))).await.unwrap(); stalled.push(peer);
        }
        let mut ninth = harness.submit(9).await;
        assert!(is_ack(ninth.next_timeout(150).await.unwrap(), false)); assert_eq!(LEASE_CALLS.load(Ordering::SeqCst), 9);
        assert!(deliveries().lock().unwrap().is_empty());
        tokio::time::sleep(Duration::from_millis(350)).await;
        let mut tenth = harness.submit(10).await; wait_lease_calls(10).await; SINK_STATE.store(2, Ordering::SeqCst); ready();
        assert!(is_ack(tenth.next_timeout(500).await.unwrap(), true)); assert_eq!(*deliveries().lock().unwrap(), vec![10]);
        drop(stalled); harness.shutdown().await;
    }
    #[tokio::test(flavor = "current_thread")]
    async fn actual_framed_sender_rejects_wrong_ack_and_ordinary_path_stays_send_only() {
        reset();
        for value in [0, 1, 2, 3] {
            let (server, client) = UnixStream::pair().unwrap(); *connect_slot().lock().unwrap() = Some(client);
            let peer = tokio::spawn(async move {
                let mut peer = ipc::Connection::new(server); assert!(matches!(peer.next_timeout(500).await.unwrap(), Some(Data::UrlLink(_))));
                let frame = match value { 0 => Data::Config(("wrong-name".into(), Some(ACK_ACCEPTED.into()))), 1 => Data::Config((ACK_NAME.into(), Some("wrong-value".into()))), 2 => Data::Config((ACK_NAME.into(), None)), _ => Data::UrlLink(String::new()) };
                peer.send(&frame).await.unwrap();
            });
            assert!(ipc::send_url_scheme(url(1)).await.is_err()); peer.await.unwrap();
        }
        for ordinary in ["close", "mixel-remote://123456", "mixel-remote://support/?invite=invalid&apikey=invalid"] {
            let (server, client) = UnixStream::pair().unwrap(); *connect_slot().lock().unwrap() = Some(client);
            let peer = tokio::spawn(async move {
                let mut peer = ipc::Connection::new(server); assert!(matches!(peer.next_timeout(500).await.unwrap(), Some(Data::UrlLink(_))));
                let _ = peer.send(&Data::Config(("wrong-name".into(), Some(ACK_REJECTED.into())))).await;
            });
            assert!(ipc::send_url_scheme(ordinary.into()).await.is_ok()); peer.await.unwrap();
        }
        assert_eq!(LEASE_CALLS.load(Ordering::SeqCst), 0);
    }
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--toolchains', default='', help='Optional comma-separated installed Rust toolchains')
    args = parser.parse_args()
    if sys.platform not in ('darwin', 'linux'):
        raise SystemExit('Mac receiver source fixture requires a Unix CI host; it never opens installed IPC')
    output = os.environ.get('MIXEL_PEER_PROOF_DIR')
    if output:
        directory = Path(output).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory(prefix='mixel-private-mac-async-peer-')
        directory = Path(cleanup.name)
    try:
        fixture = prepare(directory)
        toolchains = args.toolchains.split(',') if args.toolchains else ['']
        results = []
        for toolchain in toolchains:
            cargo = ['cargo'] + (['+' + toolchain] if toolchain else [])
            rustc = ['rustc'] + (['+' + toolchain] if toolchain else [])
            command = cargo + ['test', '--locked', '--manifest-path', str(fixture / 'Cargo.toml'), '--', '--test-threads=1']
            label = toolchain or 'configured-default'
            cargo_version = subprocess.check_output(cargo + ['--version'], text=True).strip()
            rustc_version = subprocess.check_output(rustc + ['--version'], text=True).strip()
            started = datetime.now(timezone.utc).isoformat()
            clock = time.monotonic()
            completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ, 'RUSTFLAGS': '-Dwarnings'})
            output_text = cargo_version + '\n' + rustc_version + '\n' + completed.stdout
            log_file = directory / ('cargo-' + label + '.log')
            log_file.write_text(output_text)
            print(output_text, end='', flush=True)
            results.append({'toolchain': label, 'cargoVersion': cargo_version, 'rustcVersion': rustc_version, 'startedUtc': started, 'finishedUtc': datetime.now(timezone.utc).isoformat(), 'elapsedSeconds': round(time.monotonic() - clock, 3), 'exitCode': completed.returncode, 'command': command, 'outputFile': log_file.name, 'outputSha256': hashlib.sha256(log_file.read_bytes()).hexdigest()})
            (directory / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
            completed.check_returncode()
        print('PASS real Tokio anonymous Unix peers: actual receiver ACK/sink/lease/expiry/supersession/eight-worker limit/recovery and wrong-ACK/ordinary sender; exact codec and Connection bodies; toolchains=' + ','.join(result['toolchain'] for result in results))
    finally:
        if cleanup:
            cleanup.cleanup()


if __name__ == '__main__':
    main()
