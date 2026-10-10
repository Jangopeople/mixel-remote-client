#!/usr/bin/env python3
"""Compile and execute the actual generated peer/rendezvous handshake branches.

The socket and cryptographic primitives are deterministic test doubles. The
generated handshake branches are extracted unchanged, so tests execute their
real validation, downgrade decisions, sends and encryption state checks.
Real codec/crypto/desktop interoperability is verified separately by built peers.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from rust_toolchain import rustc_command


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get("RDREPO", ROOT / "rustdesk")).resolve()


def source(relative: str) -> str:
    return subprocess.run(["git", "-C", str(UPSTREAM), "show", "HEAD:" + relative],
        check=True, capture_output=True, text=True, encoding="utf-8").stdout


def extract(text: str, signature: str, next_signature: str) -> str:
    start = text.index(signature)
    return text[start:text.index(next_signature, start)]


HARNESS = r'''
use std::cell::Cell;
use std::collections::VecDeque;
use std::future::Future;
use std::sync::{Arc, RwLock};
use std::task::{Context, Poll, Wake, Waker};
type ResultType<T> = Result<T, String>;
const READ_TIMEOUT: u64 = 20_000;
const CONNECT_TIMEOUT: u64 = 10_000;
thread_local! {
    static CUSTOM: Cell<bool> = const { Cell::new(false) };
    static WEBSOCKET: Cell<bool> = const { Cell::new(false) };
    static TIMEOUT_AT: Cell<usize> = const { Cell::new(0) };
    static TIMEOUT_CALLS: Cell<usize> = const { Cell::new(0) };
    static HOST_KEY_MISSING: Cell<bool> = const { Cell::new(false) };
    static HOST_STARTED: Cell<Option<bool>> = const { Cell::new(None) };
}
fn is_custom_client() -> bool { CUSTOM.with(Cell::get) }
fn use_ws() -> bool { WEBSOCKET.with(Cell::get) }
fn reset(custom: bool) {
    CUSTOM.with(|value| value.set(custom));
    WEBSOCKET.with(|value| value.set(false));
    TIMEOUT_AT.with(|value| value.set(0));
    TIMEOUT_CALLS.with(|value| value.set(0));
    HOST_KEY_MISSING.with(|value| value.set(false));
    HOST_STARTED.with(|value| value.set(None));
}
#[macro_export]
macro_rules! bail { ($($value:tt)*) => { return Err(format!($($value)*)) }; }
#[macro_export]
macro_rules! anyhow { ($($value:tt)*) => { format!($($value)*) }; }
#[macro_export]
macro_rules! error { ($($value:tt)*) => {{ let _ = format!($($value)*); }}; }
#[macro_export]
macro_rules! info { ($($value:tt)*) => {{ let _ = format!($($value)*); }}; }
extern crate self as log;
mod config { pub const RS_PUB_KEY: &str = "valid-pin"; }
mod sign {
    pub const PUBLICKEYBYTES: usize = 32;
    pub const SECRETKEYBYTES: usize = 64;
    #[derive(Clone, Copy)] pub struct PublicKey(pub [u8; 32]);
    pub struct SecretKey(pub [u8;64]);
    pub fn sign(_bytes: &[u8], key: &SecretKey) -> Vec<u8> {
        let _ = key.0; b"signed-host-identity".to_vec()
    }
    pub fn verify(bytes: &[u8], key: &PublicKey) -> Result<Vec<u8>, ()> {
        if key.0 != [9;32] { return Err(()); }
        match bytes {
            b"signed-key" => Ok(vec![8;32]),
            b"signed-short-key" => Ok(vec![8;31]),
            _ => Err(()),
        }
    }
}
mod box_ {
    pub const PUBLICKEYBYTES: usize = 32;
    pub struct PublicKey(pub [u8;32]);
    pub struct SecretKey(pub [u8;32]);
    pub fn gen_keypair() -> (PublicKey, SecretKey) { (PublicKey([8;32]), SecretKey([6;32])) }
}
mod tcp {
    pub struct Encrypt;
    impl Encrypt {
        pub fn decode(symmetric: &[u8], asymmetric: &[u8], private: &super::box_::SecretKey) -> super::ResultType<Vec<u8>> {
            let _ = private.0;
            if symmetric == [2;48] && asymmetric == [1;32] { Ok(vec![3;32]) }
            else { Err("invalid symmetric key ciphertext".to_owned()) }
        }
    }
}
type Bytes = Vec<u8>;
#[derive(Default)] struct IdPk { id: String, pk: Vec<u8> }
impl IdPk {
    fn write_to_bytes(&self) -> ResultType<Vec<u8>> { let _ = (&self.id, &self.pk); Ok(vec![4;32]) }
}
struct Config;
impl Config {
    fn get_key_pair() -> (Vec<u8>, Vec<u8>) {
        if HOST_KEY_MISSING.with(Cell::get) { (vec![], vec![]) } else { (vec![5;64], vec![7;32]) }
    }
    fn get_id() -> String { "fixture-host".to_owned() }
    fn set_key_confirmed(_confirmed: bool) {}
}
pub struct MockServer;
impl MockServer { fn get_new_id(&mut self) -> i32 { 1 } }
type ServerPtr = Arc<RwLock<MockServer>>;
type SocketAddr = std::net::SocketAddr;
type ControlPermissions = ();
struct Connection;
impl Connection {
    async fn start(_addr: SocketAddr, stream: Stream, _id: i32,
        _server: std::sync::Weak<RwLock<MockServer>>, _permissions: Option<ControlPermissions>) {
        HOST_STARTED.with(|value| value.set(Some(stream.is_secured())));
    }
}
fn get_rs_pk(key: &str) -> Option<sign::PublicKey> {
    (key == "valid-pin").then_some(sign::PublicKey([9;32]))
}
fn decode_id_pk(bytes: &[u8], key: &sign::PublicKey) -> ResultType<(String, [u8;32])> {
    match bytes {
        b"identity-valid" if key.0 == [9;32] => Ok(("test-peer".to_owned(), [7;32])),
        b"identity-wrong-id" if key.0 == [9;32] => Ok(("wrong-peer".to_owned(), [7;32])),
        b"ephemeral-valid" if key.0 == [7;32] => Ok(("test-peer".to_owned(), [8;32])),
        b"ephemeral-wrong-id" if key.0 == [7;32] => Ok(("wrong-peer".to_owned(), [8;32])),
        _ => Err("signature verification failed".to_owned()),
    }
}
fn get_pk(bytes: &[u8]) -> Option<[u8;32]> { bytes.try_into().ok() }
fn create_symmetric_key_msg(_public_key: [u8;32]) -> (Vec<u8>, Vec<u8>, Vec<u8>) {
    (vec![1;32], vec![2;48], vec![3;32])
}
trait ContextOption<T> { fn context(self, message: &str) -> ResultType<T>; }
impl<T> ContextOption<T> for Option<T> {
    fn context(self, message: &str) -> ResultType<T> { self.ok_or_else(|| message.to_owned()) }
}
#[derive(Default)] struct PublicKey { asymmetric_value: Vec<u8>, symmetric_value: Vec<u8> }
impl PublicKey { fn new() -> Self { Self::default() } }
#[derive(Default)] struct SignedId { id: Vec<u8> }
mod message { pub enum Union { SignedId(super::SignedId), PublicKey(super::PublicKey), Other } }
struct Message { union: Option<message::Union> }
impl Message {
    fn new() -> Self { Self { union: None } }
    fn set_public_key(&mut self, value: PublicKey) { self.union = Some(message::Union::PublicKey(value)); }
    fn set_signed_id(&mut self, value: SignedId) { self.union = Some(message::Union::SignedId(value)); }
    fn parse_from_bytes(bytes: &[u8]) -> ResultType<Self> {
        let union = match bytes {
            b"ephemeral-good" => Some(message::Union::SignedId(SignedId { id: b"ephemeral-valid".to_vec() })),
            b"ephemeral-wrong-id" => Some(message::Union::SignedId(SignedId { id: b"ephemeral-wrong-id".to_vec() })),
            b"ephemeral-bad-signature" => Some(message::Union::SignedId(SignedId { id: b"ephemeral-invalid".to_vec() })),
            b"wrong-type" => Some(message::Union::Other),
            b"empty" => None,
            b"host-peer-key-good" => Some(message::Union::PublicKey(PublicKey { asymmetric_value: vec![1;32], symmetric_value: vec![2;48] })),
            b"host-peer-key-empty" => Some(message::Union::PublicKey(PublicKey::new())),
            b"host-peer-key-short" => Some(message::Union::PublicKey(PublicKey { asymmetric_value: vec![1;31], symmetric_value: vec![2;48] })),
            b"host-peer-key-ciphertext-bad" => Some(message::Union::PublicKey(PublicKey { asymmetric_value: vec![1;32], symmetric_value: vec![2;47] })),
            _ => return Err("malformed peer protobuf".to_owned()),
        };
        Ok(Self { union })
    }
}
#[derive(Default)] struct KeyExchange { keys: Vec<Vec<u8>> }
mod rendezvous_message { pub enum Union { KeyExchange(super::KeyExchange), Other } }
struct RendezvousMessage { union: Option<rendezvous_message::Union> }
impl RendezvousMessage {
    fn new() -> Self { Self { union: None } }
    fn set_key_exchange(&mut self, value: KeyExchange) { self.union = Some(rendezvous_message::Union::KeyExchange(value)); }
    fn parse_from_bytes(bytes: &[u8]) -> ResultType<Self> {
        let union = match bytes {
            b"rendezvous-good" => Some(rendezvous_message::Union::KeyExchange(KeyExchange { keys: vec![b"signed-key".to_vec()] })),
            b"rendezvous-bad-signature" => Some(rendezvous_message::Union::KeyExchange(KeyExchange { keys: vec![b"bad-key".to_vec()] })),
            b"rendezvous-short-key" => Some(rendezvous_message::Union::KeyExchange(KeyExchange { keys: vec![b"signed-short-key".to_vec()] })),
            b"rendezvous-no-keys" => Some(rendezvous_message::Union::KeyExchange(KeyExchange { keys: vec![] })),
            b"rendezvous-extra-keys" => Some(rendezvous_message::Union::KeyExchange(KeyExchange { keys: vec![vec![], vec![]] })),
            b"wrong-type" => Some(rendezvous_message::Union::Other),
            b"empty" => None,
            _ => return Err("malformed rendezvous protobuf".to_owned()),
        };
        Ok(Self { union })
    }
}
trait Packet { fn label(&self) -> &'static str; }
impl Packet for Message {
    fn label(&self) -> &'static str {
        match &self.union {
            Some(message::Union::PublicKey(value)) if !value.asymmetric_value.is_empty() && !value.symmetric_value.is_empty() => "peer-encryption-key",
            Some(message::Union::PublicKey(_)) => "insecure-peer-key",
            _ => "empty-peer-message",
        }
    }
}
impl Packet for RendezvousMessage {
    fn label(&self) -> &'static str {
        match &self.union { Some(rendezvous_message::Union::KeyExchange(value)) if value.keys.len() == 2 => "rendezvous-encryption-key", _ => "empty-rendezvous-message" }
    }
}
pub struct MockStream {
    received: VecDeque<ResultType<Vec<u8>>>, sent: Vec<&'static str>, secured: bool,
    fail_send: bool, refuse_key: bool,
}
pub enum Stream { Tcp(MockStream), WebSocket(MockStream) }
impl std::ops::Deref for Stream {
    type Target = MockStream;
    fn deref(&self) -> &MockStream { match self { Self::Tcp(value) | Self::WebSocket(value) => value } }
}
impl std::ops::DerefMut for Stream {
    fn deref_mut(&mut self) -> &mut MockStream { match self { Self::Tcp(value) | Self::WebSocket(value) => value } }
}
impl Stream {
    fn with(bytes: Option<&[u8]>) -> Self {
        Self::Tcp(MockStream { received: bytes.map(|value| Ok(value.to_vec())).into_iter().collect(),
               sent: vec![], secured: false, fail_send: false, refuse_key: false })
    }
    async fn next(&mut self) -> Option<ResultType<Vec<u8>>> { self.received.pop_front() }
    async fn send<T: Packet>(&mut self, packet: &T) -> ResultType<()> {
        if self.fail_send { return Err("socket send failed".to_owned()); }
        self.sent.push(packet.label()); Ok(())
    }
    fn set_key(&mut self, _key: Vec<u8>) { self.secured = !self.refuse_key; }
    fn is_secured(&self) -> bool { self.secured }
}
async fn timeout<F: Future>(_milliseconds: u64, future: F) -> ResultType<F::Output> {
    let call = TIMEOUT_CALLS.with(|value| { value.set(value.get()+1); value.get() });
    if TIMEOUT_AT.with(Cell::get) == call { Err("network timeout".to_owned()) }
    else { Ok(future.await) }
}
struct NoopWake;
impl Wake for NoopWake { fn wake(self: Arc<Self>) {} }
fn run<F: Future>(future: F) -> F::Output {
    let waker = Waker::from(Arc::new(NoopWake));
    let mut context = Context::from_waker(&waker);
    let mut future = Box::pin(future);
    loop { match future.as_mut().poll(&mut context) {
        Poll::Ready(value) => return value,
        Poll::Pending => std::thread::yield_now(),
    } }
}
struct Client;
impl Client {
GENERATED_PEER_HANDSHAKE
}
GENERATED_RENDEZVOUS_HANDSHAKE
GENERATED_HOST_HANDSHAKE

#[test]
fn pinned_peer_rejects_absent_invalid_and_wrong_signed_identity_without_fallback_writes() {
    for identity in [b"".as_slice(), b"invalid-signature", b"identity-wrong-id"] {
        reset(true);
        let mut stream = Stream::with(Some(b"ephemeral-good"));
        assert!(run(Client::secure_connection("test-peer", identity.to_vec(), "valid-pin", &mut stream)).is_err());
        assert!(stream.sent.is_empty()); assert!(!stream.secured);
    }
    reset(true);
    let mut stream = Stream::with(Some(b"ephemeral-good"));
    assert!(run(Client::secure_connection("test-peer", b"identity-valid".to_vec(), "invalid-pin", &mut stream)).is_err());
    assert!(stream.sent.is_empty()); assert!(!stream.secured);
}
#[test]
fn pinned_peer_rejects_wrong_id_signature_type_and_malformed_ephemeral_handshake() {
    for received in [b"ephemeral-wrong-id".as_slice(), b"ephemeral-bad-signature", b"wrong-type", b"empty", b"malformed"] {
        reset(true);
        let mut stream = Stream::with(Some(received));
        assert!(run(Client::secure_connection("test-peer", b"identity-valid".to_vec(), "valid-pin", &mut stream)).is_err());
        assert!(stream.sent.is_empty()); assert!(!stream.secured);
    }
}
#[test]
fn pinned_peer_rejects_eof_socket_errors_and_read_or_send_timeouts() {
    for mode in 0..5 {
        reset(true);
        let mut stream = Stream::with(Some(b"ephemeral-good"));
        match mode {
            0 => stream.received.clear(),
            1 => stream.received = [Err("receive failed".to_owned())].into_iter().collect(),
            2 => TIMEOUT_AT.with(|value| value.set(1)),
            3 => TIMEOUT_AT.with(|value| value.set(2)),
            _ => stream.fail_send = true,
        }
        assert!(run(Client::secure_connection("test-peer", b"identity-valid".to_vec(), "valid-pin", &mut stream)).is_err());
        assert!(!stream.secured);
    }
}
#[test]
fn pinned_peer_success_requires_actual_stream_encryption_state() {
    for configured in ["valid-pin", ""] {
        reset(true);
        let mut stream = Stream::with(Some(b"ephemeral-good"));
        assert_eq!(run(Client::secure_connection("test-peer", b"identity-valid".to_vec(), configured, &mut stream)).unwrap(), Some(vec![7;32]));
        assert_eq!(stream.sent, ["peer-encryption-key"]); assert!(stream.secured);
    }
    reset(true);
    let mut stream = Stream::with(Some(b"ephemeral-good")); stream.refuse_key = true;
    assert!(run(Client::secure_connection("test-peer", b"identity-valid".to_vec(), "valid-pin", &mut stream)).is_err());
}
#[test]
fn stock_peer_legacy_fallback_conventions_are_preserved() {
    reset(false);
    let mut stream = Stream::with(None);
    assert_eq!(run(Client::secure_connection("test-peer", vec![], "valid-pin", &mut stream)).unwrap(), None);
    assert_eq!(stream.sent, ["empty-peer-message"]); assert!(!stream.secured);
    for received in [b"ephemeral-wrong-id".as_slice(), b"ephemeral-bad-signature", b"wrong-type", b"malformed"] {
        reset(false);
        let mut stream = Stream::with(Some(received));
        assert!(run(Client::secure_connection("test-peer", b"identity-valid".to_vec(), "valid-pin", &mut stream)).is_ok());
        assert_eq!(stream.sent.len(), 1); assert!(!stream.secured);
    }
}
#[test]
fn pinned_rendezvous_rejects_wrong_type_format_eof_and_socket_errors() {
    for received in [Some(b"wrong-type".as_slice()), Some(b"empty".as_slice()), Some(b"malformed".as_slice()), None] {
        reset(true);
        let mut stream = Stream::with(received);
        assert!(run(secure_tcp(&mut stream, "valid-pin")).is_err());
        assert!(stream.sent.is_empty()); assert!(!stream.secured);
    }
    reset(true);
    let mut stream = Stream::with(None);
    stream.received.push_back(Err("receive failed".to_owned()));
    assert!(run(secure_tcp(&mut stream, "valid-pin")).is_err());
}
#[test]
fn pinned_rendezvous_rejects_bad_signature_key_length_and_key_count() {
    for received in [b"rendezvous-bad-signature".as_slice(), b"rendezvous-short-key", b"rendezvous-no-keys", b"rendezvous-extra-keys"] {
        reset(true);
        let mut stream = Stream::with(Some(received));
        assert!(run(secure_tcp(&mut stream, "valid-pin")).is_err());
        assert!(stream.sent.is_empty()); assert!(!stream.secured);
    }
    reset(true);
    let mut stream = Stream::with(Some(b"rendezvous-good"));
    assert!(run(secure_tcp(&mut stream, "invalid-pin")).is_err());
    assert!(stream.sent.is_empty()); assert!(!stream.secured);
}
#[test]
fn pinned_rendezvous_rejects_read_and_send_errors_and_unsecured_final_state() {
    for mode in 0..4 {
        reset(true);
        let mut stream = Stream::with(Some(b"rendezvous-good"));
        match mode {
            0 => TIMEOUT_AT.with(|value| value.set(1)),
            1 => TIMEOUT_AT.with(|value| value.set(2)),
            2 => stream.fail_send = true,
            _ => stream.refuse_key = true,
        }
        assert!(run(secure_tcp(&mut stream, "valid-pin")).is_err());
        assert!(!stream.secured);
    }
}
#[test]
fn pinned_rendezvous_valid_exchange_establishes_authenticated_encryption() {
    reset(true);
    let mut stream = Stream::with(Some(b"rendezvous-good"));
    run(secure_tcp(&mut stream, "valid-pin")).unwrap();
    assert_eq!(stream.sent, ["rendezvous-encryption-key"]); assert!(stream.secured);
}
#[test]
fn stock_rendezvous_and_secure_websocket_conventions_are_preserved() {
    for received in [Some(b"wrong-type".as_slice()), Some(b"malformed".as_slice()), None] {
        reset(false);
        let mut stream = Stream::with(received);
        run(secure_tcp(&mut stream, "valid-pin")).unwrap();
        assert!(stream.sent.is_empty()); assert!(!stream.secured);
    }
    reset(true);
    let mut stream = match Stream::with(None) { Stream::Tcp(value) => Stream::WebSocket(value), _ => unreachable!() };
    run(secure_tcp(&mut stream, "valid-pin")).unwrap();
    assert!(stream.sent.is_empty());
}
#[test]
fn concurrent_https_fallback_cannot_skip_authentication_of_existing_native_socket() {
    reset(true); WEBSOCKET.with(|value| value.set(true));
    let mut stream = Stream::with(None);
    assert!(run(secure_tcp(&mut stream, "valid-pin")).is_err());
    let mut stream = Stream::with(Some(b"rendezvous-good"));
    run(secure_tcp(&mut stream, "valid-pin")).unwrap();
    assert_eq!(stream.sent, ["rendezvous-encryption-key"]); assert!(stream.secured);
}
fn host_server() -> ServerPtr { Arc::new(RwLock::new(MockServer)) }
fn host_addr() -> SocketAddr { "127.0.0.1:21118".parse().unwrap() }
#[test]
fn pinned_host_rejects_plaintext_relay_and_missing_local_signing_identity() {
    reset(true);
    assert!(run(create_tcp_connection(host_server(), Stream::with(None), host_addr(), false, None)).is_err());
    assert_eq!(HOST_STARTED.with(Cell::get), None);
    HOST_KEY_MISSING.with(|value| value.set(true));
    assert!(run(create_tcp_connection(host_server(), Stream::with(Some(b"host-peer-key-good")), host_addr(), true, None)).is_err());
    assert_eq!(HOST_STARTED.with(Cell::get), None);
}
#[test]
fn pinned_host_rejects_empty_short_corrupt_and_wrong_type_peer_encryption_keys() {
    for received in [Some(b"host-peer-key-empty".as_slice()), Some(b"host-peer-key-short".as_slice()),
        Some(b"host-peer-key-ciphertext-bad".as_slice()), Some(b"wrong-type".as_slice()),
        Some(b"empty".as_slice()), Some(b"malformed".as_slice()), None] {
        reset(true);
        assert!(run(create_tcp_connection(host_server(), Stream::with(received), host_addr(), true, None)).is_err());
        assert_eq!(HOST_STARTED.with(Cell::get), None);
    }
}
#[test]
fn pinned_host_rejects_receive_send_timeouts_socket_errors_and_failed_encryption_state() {
    for mode in 0..5 {
        reset(true);
        let mut stream = Stream::with(Some(b"host-peer-key-good"));
        match mode {
            0 => TIMEOUT_AT.with(|value| value.set(1)),
            1 => TIMEOUT_AT.with(|value| value.set(2)),
            2 => stream.fail_send = true,
            3 => stream.received = [Err("receive failed".to_owned())].into_iter().collect(),
            _ => stream.refuse_key = true,
        }
        assert!(run(create_tcp_connection(host_server(), stream, host_addr(), true, None)).is_err());
        assert_eq!(HOST_STARTED.with(Cell::get), None);
    }
}
#[test]
fn pinned_host_starts_only_after_successful_authenticated_encryption() {
    reset(true);
    run(create_tcp_connection(host_server(), Stream::with(Some(b"host-peer-key-good")), host_addr(), true, None)).unwrap();
    assert_eq!(HOST_STARTED.with(Cell::get), Some(true));
}
#[test]
fn explicit_direct_listener_and_stock_host_keep_plaintext_compatibility() {
    reset(true);
    run(create_direct_tcp_connection(host_server(), Stream::with(None), host_addr(), false, None)).unwrap();
    assert_eq!(HOST_STARTED.with(Cell::get), Some(false));
    reset(false);
    run(create_tcp_connection(host_server(), Stream::with(None), host_addr(), false, None)).unwrap();
    assert_eq!(HOST_STARTED.with(Cell::get), Some(false));
    run(create_tcp_connection(host_server(), Stream::with(Some(b"host-peer-key-empty")), host_addr(), true, None)).unwrap();
    assert_eq!(HOST_STARTED.with(Cell::get), Some(false));
}
'''


def main() -> None:
    rustc = shutil.which("rustc")
    if not rustc:
        raise RuntimeError("rustc is required to execute actual handshake branches")
    with tempfile.TemporaryDirectory(prefix="mixel-secure-support-test-") as temporary:
        repository = Path(temporary)
        (repository / "src").mkdir()
        originals = {relative: source(relative) for relative in ("src/client.rs", "src/common.rs", "src/server.rs", "src/rendezvous_mediator.rs")}
        for relative, content in originals.items():
            (repository / relative).write_text(content, encoding="utf-8")
        env = dict(os.environ, RDREPO=str(repository))
        command = ["python3", str(ROOT / "scripts/patch-secure-support.py")]
        subprocess.run(command, env=env, check=True)
        once = {relative: (repository / relative).read_bytes() for relative in originals}
        subprocess.run(command, env=env, check=True)
        assert once == {relative: (repository / relative).read_bytes() for relative in originals}
        print("PASS: pinned handshake patch is byte-for-byte idempotent", flush=True)
        client = (repository / "src/client.rs").read_text(encoding="utf-8")
        common = (repository / "src/common.rs").read_text(encoding="utf-8")
        signature = "    async fn secure_connection("
        peer = extract(client, signature, "    /// Request a relay connection")
        rendezvous = extract(common, "pub async fn secure_tcp(", "\n#[inline]\nfn get_pk")
        host = extract((repository / "src/server.rs").read_text(encoding="utf-8"), "pub async fn create_tcp_connection(", "\npub async fn accept_connection(")
        # The Mac wake-up utility is an unrelated platform side effect. The
        # complete host policy/crypto branches and actual Connection::start call
        # remain unchanged and are executed against the observable test server.
        host = host[:host.index('    #[cfg(target_os = "macos")]')] + host[host.index("    Connection::start("):]
        mediator = (repository / "src/rendezvous_mediator.rs").read_text(encoding="utf-8")
        direct_listener = extract(mediator, "async fn direct_server(", "\nenum Sink")
        assert mediator.count("crate::server::create_direct_tcp_connection(") == 1
        assert "crate::server::create_direct_tcp_connection(" in direct_listener
        assert "crate::server::create_tcp_connection(" not in direct_listener
        # Verify only these handshakes change; the intentional direct IP/LAN
        # path and all unrelated session behavior remain byte-identical.
        assert extract(originals["src/client.rs"], "    pub async fn start(", "    /// Establish secure connection") == extract(client, "    pub async fn start(", "    /// Establish secure connection")
        generated = HARNESS.replace("GENERATED_PEER_HANDSHAKE", peer).replace("GENERATED_RENDEZVOUS_HANDSHAKE", rendezvous).replace("GENERATED_HOST_HANDSHAKE", host)
        rust_source = repository / "secure-handshake-tests.rs"
        rust_source.write_text(generated, encoding="utf-8")
        binary = repository / ("secure-handshake-tests.exe" if os.name == "nt" else "secure-handshake-tests")
        subprocess.run(rustc_command([rustc, "--edition=2021", "--deny", "warnings", "--test", str(rust_source), "-o", str(binary)]), check=True)
        subprocess.run([str(binary), "--test-threads=1"], check=True)
        for relative, broken in (
            ("src/client.rs", originals["src/client.rs"].replace("        Ok(option_pk)\n", "        Ok(None)\n", 1)),
            ("src/common.rs", originals["src/common.rs"].replace("pub async fn secure_tcp", "pub async fn renamed_secure_tcp", 1)),
        ):
            for path, content in originals.items():
                (repository / path).write_text(broken if path == relative else content, encoding="utf-8")
            rejected = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8")
            assert rejected.returncode != 0, f"Unexpected changed upstream handshake accepted: {relative}"
        print("PASS: changed upstream handshakes fail closed; only explicit direct IP/LAN listener retains its bypass", flush=True)
        print("PASS: actual generated peer/rendezvous handshake regression checks", flush=True)


if __name__ == "__main__":
    main()
