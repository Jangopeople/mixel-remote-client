//! TCP/TLS TURN adapter for a native stack whose built-in relayer accepts UDP.
//! Private endpoint aliases never leave this process as actual UDP traffic.
//! The relay allocation remains UDP; its first hop is an authenticated stream.
use crate::turn_framing;
use futures_util::{FutureExt, task::AtomicWaker};
use std::{
    collections::{HashMap, VecDeque},
    fmt,
    future::Future,
    io::{self, IoSliceMut},
    net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr},
    pin::Pin,
    sync::{
        Arc, Mutex, Weak,
        atomic::{AtomicU64, Ordering},
    },
    task::{Context, Poll},
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncRead, AsyncWrite, AsyncWriteExt},
    sync::{Notify, mpsc, watch},
};
use tokio_rustls::{
    TlsConnector,
    rustls::{self, ClientConfig, RootCertStore, pki_types::ServerName},
};
use webrtc::{
    peer_connection::RTCIceServer,
    runtime::{
        self, AsyncInterval, AsyncTcpListener, AsyncTcpStream, AsyncUdpSocket, JoinHandle,
        RecvMeta, Runtime, Transmit,
    },
};

const MAX_ROUTES: usize = 8;
const OUT_PACKETS: usize = 16;
const IN_PACKETS: usize = 64;
type IoFuture<'a, T> = Pin<Box<dyn Future<Output = io::Result<T>> + Send + 'a>>;
trait Stream: AsyncRead + AsyncWrite + Unpin + Send {}
impl<T: AsyncRead + AsyncWrite + Unpin + Send> Stream for T {}
type BoxStream = Box<dyn Stream>;
#[derive(Clone)]
struct Route {
    alias: SocketAddr,
    endpoint: String,
    name: String,
    tls: Option<Arc<ClientConfig>>,
}
struct Outbox {
    packets: VecDeque<Vec<u8>>,
    error: Option<String>,
    closed: bool,
}
struct Pipe {
    route: Route,
    out: Mutex<Outbox>,
    ready: Notify,
    writable: AtomicWaker,
    stop: watch::Sender<bool>,
    finished: watch::Sender<bool>,
}
impl Pipe {
    fn enqueue(&self, cx: &mut Context<'_>, packet: &[u8]) -> Poll<io::Result<usize>> {
        self.writable.register(cx.waker());
        let mut out = match self.out.lock() {
            Ok(out) => out,
            Err(_) => return Poll::Ready(Err(io::Error::other("TURN stream state failed"))),
        };
        if let Some(error) = &out.error {
            return Poll::Ready(Err(io::Error::new(
                io::ErrorKind::ConnectionRefused,
                error.clone(),
            )));
        }
        if out.closed {
            return Poll::Ready(Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "TURN stream closed",
            )));
        }
        if out.packets.len() >= OUT_PACKETS {
            return Poll::Pending;
        }
        let framed = match turn_framing::stream_packet(packet) {
            Ok(packet) => packet,
            Err(error) => return Poll::Ready(Err(error)),
        };
        out.packets.push_back(framed);
        drop(out);
        self.ready.notify_one();
        Poll::Ready(Ok(packet.len()))
    }
    fn pop(&self) -> io::Result<Option<Vec<u8>>> {
        let packet = self
            .out
            .lock()
            .map_err(|_| io::Error::other("TURN stream state failed"))?
            .packets
            .pop_front();
        if packet.is_some() {
            self.writable.wake();
        }
        Ok(packet)
    }
    fn finish(&self, error: Option<String>) {
        if let Ok(mut out) = self.out.lock() {
            out.error = error;
            out.closed = true;
            out.packets.clear();
        }
        self.writable.wake();
        self.finished.send_replace(true);
    }
}
struct Received {
    packet: Vec<u8>,
    source: SocketAddr,
}
struct StreamSocket {
    udp: Arc<dyn AsyncUdpSocket>,
    pipes: HashMap<SocketAddr, Arc<Pipe>>,
    incoming: Mutex<mpsc::Receiver<Received>>,
    udp_writes: Arc<AtomicU64>,
}
impl fmt::Debug for StreamSocket {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("MixelStreamTurnSocket")
            .field("routes", &self.pipes.len())
            .finish()
    }
}
impl AsyncUdpSocket for StreamSocket {
    fn local_addr(&self) -> io::Result<SocketAddr> {
        self.udp.local_addr()
    }
    fn max_gro_segments(&self) -> usize {
        // Direct UDP still uses the wrapped socket when TURN streams coexist.
        // Preserve its receive capacity so the driver can split GRO batches.
        self.udp.max_gro_segments()
    }
    fn poll_send(&self, cx: &mut Context<'_>, transmit: &Transmit<'_>) -> Poll<io::Result<usize>> {
        if let Some(pipe) = self.pipes.get(&transmit.destination) {
            if transmit
                .segment_size
                .is_some_and(|size| size < transmit.contents.len())
            {
                return Poll::Ready(Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "TURN streams do not accept UDP GSO batches",
                )));
            }
            // There is deliberately no UDP fallback from this branch, including
            // connection, DNS, TLS-chain and hostname failures.
            return pipe.enqueue(cx, transmit.contents);
        }
        let result = self.udp.poll_send(cx, transmit);
        if matches!(result, Poll::Ready(Ok(_))) {
            self.udp_writes.fetch_add(1, Ordering::Relaxed);
        }
        result
    }
    fn poll_recv(
        &self,
        cx: &mut Context<'_>,
        bufs: &mut [IoSliceMut<'_>],
        meta: &mut [RecvMeta],
    ) -> Poll<io::Result<usize>> {
        if bufs.is_empty() || meta.is_empty() {
            return Poll::Ready(Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "No receive storage",
            )));
        }
        let mut incoming = match self.incoming.lock() {
            Ok(incoming) => incoming,
            Err(_) => return Poll::Ready(Err(io::Error::other("TURN receive state failed"))),
        };
        if let Poll::Ready(Some(received)) = incoming.poll_recv(cx) {
            if received.packet.len() > bufs[0].len() {
                return Poll::Ready(Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "TURN receive storage is too small",
                )));
            }
            bufs[0][..received.packet.len()].copy_from_slice(&received.packet);
            meta[0] = RecvMeta::default();
            meta[0].addr = received.source;
            meta[0].len = received.packet.len();
            meta[0].stride = received.packet.len().max(1);
            meta[0].dst_ip = Some(self.local_addr()?.ip());
            return Poll::Ready(Ok(1));
        }
        drop(incoming);
        self.udp.poll_recv(cx, bufs, meta)
    }
}
impl Drop for StreamSocket {
    fn drop(&mut self) {
        for pipe in self.pipes.values() {
            pipe.stop.send_replace(true);
        }
    }
}

pub(crate) struct StreamRuntime {
    inner: Arc<dyn Runtime>,
    routes: Vec<Route>,
    pipes: Mutex<Vec<Weak<Pipe>>>,
    udp_writes: Arc<AtomicU64>,
}
impl fmt::Debug for StreamRuntime {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("MixelStreamTurnRuntime")
            .field("routes", &self.routes.len())
            .finish()
    }
}
fn system_tls() -> io::Result<Arc<ClientConfig>> {
    let certificates = rustls_native_certs::load_native_certs();
    let mut roots = RootCertStore::empty();
    let (accepted, _) = roots.add_parsable_certificates(certificates.certs);
    if accepted == 0 {
        return Err(io::Error::other("No trusted system TLS roots available"));
    }
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .map_err(io::Error::other)?
        .with_root_certificates(roots)
        .with_no_client_auth();
    Ok(Arc::new(config))
}
impl StreamRuntime {
    /// TLS override is crate-private for isolated CA fixtures. Normal callers
    /// supply None and use platform trust; no permissive verifier exists.
    pub(crate) fn prepare(
        bind: &str,
        mut servers: Vec<RTCIceServer>,
        tls_override: Option<Arc<ClientConfig>>,
    ) -> io::Result<Option<(Arc<Self>, Vec<RTCIceServer>)>> {
        let mut routes = Vec::new();
        let mut tls = tls_override;
        for server in &mut servers {
            let parsed = server.urls().map_err(io::Error::other)?;
            let mut rewritten = Vec::new();
            for (original, url) in server.urls.iter().zip(parsed) {
                let secure = url.scheme.to_string() == "turns";
                if !secure && (url.scheme.to_string() != "turn" || url.proto.to_string() == "udp") {
                    rewritten.push(original.clone());
                    continue;
                }
                if url.proto.to_string() != "tcp" {
                    return Err(io::Error::new(
                        io::ErrorKind::Unsupported,
                        "TURN-over-DTLS is not a TLS-over-TCP endpoint",
                    ));
                }
                if routes.len() >= MAX_ROUTES {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "Too many stream TURN endpoints",
                    ));
                }
                let local: SocketAddr = bind.parse().map_err(|_| {
                    io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "Stream TURN requires a literal local bind address",
                    )
                })?;
                let alias = SocketAddr::new(
                    if local.is_ipv6() {
                        IpAddr::V6(Ipv6Addr::new(0x2001, 0xdb8, 0, 0, 0, 0, 0, 1))
                    } else {
                        IpAddr::V4(Ipv4Addr::new(192, 0, 2, 1))
                    },
                    40000 + routes.len() as u16,
                );
                let endpoint = if url.host.contains(':') {
                    format!("[{}]:{}", url.host, url.port)
                } else {
                    format!("{}:{}", url.host, url.port)
                };
                let config = if secure {
                    if tls.is_none() {
                        tls = Some(system_tls()?);
                    }
                    tls.clone()
                } else {
                    None
                };
                rewritten.push(format!("turn:{alias}?transport=udp"));
                routes.push(Route {
                    alias,
                    endpoint,
                    name: url.host,
                    tls: config,
                });
            }
            server.urls = rewritten;
        }
        if routes.is_empty() {
            return Ok(None);
        }
        let inner = runtime::default_runtime()
            .ok_or_else(|| io::Error::other("No native WebRTC runtime"))?;
        Ok(Some((
            Arc::new(Self {
                inner,
                routes,
                pipes: Mutex::new(vec![]),
                udp_writes: Arc::new(AtomicU64::new(0)),
            }),
            servers,
        )))
    }
    #[cfg(test)]
    pub(crate) fn physical_udp_writes(&self) -> u64 {
        self.udp_writes.load(Ordering::Relaxed)
    }
    pub(crate) async fn shutdown(&self) -> io::Result<()> {
        let pipes: Vec<_> = self
            .pipes
            .lock()
            .map_err(|_| io::Error::other("TURN state failed"))?
            .iter()
            .filter_map(Weak::upgrade)
            .collect();
        for pipe in &pipes {
            pipe.stop.send_replace(true);
        }
        tokio::time::timeout(Duration::from_secs(5), async {
            for pipe in pipes {
                let mut done = pipe.finished.subscribe();
                if !*done.borrow_and_update() {
                    done.wait_for(|value| *value)
                        .await
                        .map_err(io::Error::other)?;
                }
            }
            Ok(())
        })
        .await
        .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "TURN stream shutdown timed out"))?
    }
}
impl Runtime for StreamRuntime {
    fn spawn(&self, future: Pin<Box<dyn Future<Output = ()> + Send>>) -> Box<dyn JoinHandle> {
        self.inner.spawn(future)
    }
    fn spawn_reactor(
        &self,
        size: usize,
        future: Pin<Box<dyn Future<Output = ()> + Send>>,
    ) -> Box<dyn JoinHandle> {
        self.inner.spawn_reactor(size, future)
    }
    fn wrap_udp_socket(&self, socket: std::net::UdpSocket) -> io::Result<Arc<dyn AsyncUdpSocket>> {
        let udp = self.inner.wrap_udp_socket(socket)?;
        let (tx, rx) = mpsc::channel(IN_PACKETS);
        let mut pipes = HashMap::new();
        let mut registry = self
            .pipes
            .lock()
            .map_err(|_| io::Error::other("TURN state failed"))?;
        registry.retain(|pipe| pipe.strong_count() > 0);
        if registry.len() + self.routes.len() > 64 {
            return Err(io::Error::new(
                io::ErrorKind::OutOfMemory,
                "Too many live TURN streams",
            ));
        }
        for route in &self.routes {
            let (stop, stopped) = watch::channel(false);
            let (finished, _) = watch::channel(false);
            let pipe = Arc::new(Pipe {
                route: route.clone(),
                out: Mutex::new(Outbox {
                    packets: VecDeque::new(),
                    error: None,
                    closed: false,
                }),
                ready: Notify::new(),
                writable: AtomicWaker::new(),
                stop,
                finished,
            });
            registry.push(Arc::downgrade(&pipe));
            let owned = pipe.clone();
            let incoming = tx.clone();
            tokio::spawn(async move {
                let result = std::panic::AssertUnwindSafe(run_pipe(&owned, stopped, incoming))
                    .catch_unwind()
                    .await;
                let error = match result {
                    Ok(result) => result.err().map(|error| error.to_string()),
                    Err(_) => Some("TURN stream worker panicked".into()),
                };
                owned.finish(error);
            });
            pipes.insert(route.alias, pipe);
        }
        Ok(Arc::new(StreamSocket {
            udp,
            pipes,
            incoming: Mutex::new(rx),
            udp_writes: self.udp_writes.clone(),
        }))
    }
    fn wrap_tcp_listener(
        &self,
        listener: std::net::TcpListener,
    ) -> io::Result<Arc<dyn AsyncTcpListener>> {
        self.inner.wrap_tcp_listener(listener)
    }
    fn connect_tcp<'a>(&'a self, address: SocketAddr) -> IoFuture<'a, Arc<dyn AsyncTcpStream>> {
        self.inner.connect_tcp(address)
    }
    fn resolve_host<'a>(&'a self, host: &'a str) -> IoFuture<'a, Vec<SocketAddr>> {
        self.inner.resolve_host(host)
    }
    fn now(&self) -> Instant {
        self.inner.now()
    }
    fn sleep(&self, duration: Duration) -> Pin<Box<dyn Future<Output = ()> + Send + 'static>> {
        self.inner.sleep(duration)
    }
    fn interval(&self, duration: Duration) -> Box<dyn AsyncInterval> {
        self.inner.interval(duration)
    }
    fn block_on(&self, future: Pin<Box<dyn Future<Output = ()> + '_>>) {
        self.inner.block_on(future)
    }
    fn name(&self) -> &'static str {
        "mixel-stream-turn"
    }
}
async fn connect(route: &Route) -> io::Result<BoxStream> {
    tokio::time::timeout(Duration::from_secs(3), async {
        let stream = tokio::net::TcpStream::connect(&route.endpoint).await?;
        stream.set_nodelay(true)?;
        if let Some(config) = &route.tls {
            let name = ServerName::try_from(route.name.clone()).map_err(io::Error::other)?;
            let stream = TlsConnector::from(config.clone())
                .connect(name, stream)
                .await
                .map_err(io::Error::other)?;
            Ok(Box::new(stream) as BoxStream)
        } else {
            Ok(Box::new(stream) as BoxStream)
        }
    })
    .await
    .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "TURN stream connection timed out"))?
}
async fn run_pipe(
    pipe: &Arc<Pipe>,
    mut stopped: watch::Receiver<bool>,
    incoming: mpsc::Sender<Received>,
) -> io::Result<()> {
    let stream =
        tokio::select! {result=connect(&pipe.route)=>result?,_=stopped.changed()=>return Ok(())};
    let (reader, mut writer) = tokio::io::split(stream);
    let source = pipe.route.alias;
    let mut read_task = AbortReader(tokio::spawn(read_packets(reader, incoming, source)));
    let mut read_joined = false;
    let result = async {
        loop {
            if *stopped.borrow() {
                break;
            }
            if let Some(packet) = pipe.pop()? {
                tokio::time::timeout(Duration::from_secs(3), writer.write_all(&packet))
                    .await
                    .map_err(|_| {
                        io::Error::new(io::ErrorKind::TimedOut, "TURN stream write timed out")
                    })??;
                continue;
            }
            tokio::select! {
                result=&mut read_task.0=>{read_joined=true;return result.map_err(io::Error::other)?;},
                _=pipe.ready.notified()=>{},
                _=stopped.changed()=>break,
            }
        }
        // Flush queued Refresh(LIFETIME=0) before dropping the stream. The
        // entire drain is bounded, including a slow or unresponsive server.
        tokio::time::timeout(Duration::from_secs(1), async {
            while let Some(packet) = pipe.pop()? {
                writer.write_all(&packet).await?;
            }
            writer.shutdown().await
        })
        .await
        .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "TURN stream drain timed out"))?
    }
    .await;
    if !read_joined {
        read_task.0.abort();
        let _ = (&mut read_task.0).await;
    }
    result
}

struct AbortReader(tokio::task::JoinHandle<io::Result<()>>);
impl Drop for AbortReader {
    fn drop(&mut self) {
        self.0.abort();
    }
}
async fn read_packets(
    mut reader: tokio::io::ReadHalf<BoxStream>,
    incoming: mpsc::Sender<Received>,
    source: SocketAddr,
) -> io::Result<()> {
    loop {
        let packet = turn_framing::read_packet(&mut reader).await?;
        incoming
            .send(Received { packet, source })
            .await
            .map_err(|_| io::Error::new(io::ErrorKind::BrokenPipe, "TURN receiver closed"))?;
    }
}

#[cfg(test)]
#[path = "stream_turn_tests.rs"]
mod tests;

#[cfg(test)]
mod receive_tests {
    use super::*;

    #[tokio::test]
    async fn mixed_turn_socket_preserves_udp_receive_batch_capacity() {
        let runtime = webrtc::runtime::default_runtime().expect("native runtime");
        let udp = runtime
            .wrap_udp_socket(std::net::UdpSocket::bind("127.0.0.1:0").unwrap())
            .unwrap();
        let expected = udp.max_gro_segments();
        let (_, incoming) = mpsc::channel(1);
        let socket = StreamSocket {
            udp,
            pipes: HashMap::new(),
            incoming: Mutex::new(incoming),
            udp_writes: Arc::new(AtomicU64::new(0)),
        };

        assert_eq!(socket.max_gro_segments(), expected);
        println!("UDP receive batch capacity: {expected} datagrams");
    }
}
