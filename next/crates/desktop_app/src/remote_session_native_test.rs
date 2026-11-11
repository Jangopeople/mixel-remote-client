use super::*;
#[cfg(target_os = "linux")]
use x11rb::{
    connection::Connection,
    protocol::xproto::{ChangeWindowAttributesAux, ConnectionExt},
};

struct FixturePeer {
    session: RemoteSession,
    identity: Arc<IdentityState>,
    video: Arc<VideoMailbox>,
    cancellation: Arc<AtomicBool>,
    consent_id: Uuid,
    consent_cancel: Arc<AtomicBool>,
    account_cancel: Arc<AtomicBool>,
    outgoing: mpsc::Sender<Command>,
}

impl FixturePeer {
    async fn new(id: Uuid, hosting: bool) -> (Self, mpsc::Receiver<Command>) {
        let session = RemoteSession::default();
        let identity = Arc::new(IdentityState::test_identity(
            ed25519_dalek::SigningKey::generate(&mut rand_core::OsRng),
        ));
        let video = Arc::new(VideoMailbox::default());
        let cancellation = Arc::new(AtomicBool::new(false));
        let consent_id = Uuid::new_v4();
        let consent_cancel = Arc::new(AtomicBool::new(false));
        let (outgoing, commands) = mpsc::channel(32);
        let account_cancel = {
            let mut inner = session.0.lock().await;
            inner.video = Some(video.clone());
            inner.status.phase = if hosting { "incoming" } else { "outgoing" }.into();
            inner.status.sharing = hosting;
            inner.commands = Some(outgoing.clone());
            inner.cancel.clone()
        };
        session
            .track(
                0,
                id,
                cancellation.clone(),
                consent_id,
                consent_cancel.clone(),
            )
            .await
            .unwrap();
        (
            Self {
                session,
                identity,
                video,
                cancellation,
                consent_id,
                consent_cancel,
                account_cancel,
                outgoing,
            },
            commands,
        )
    }

    fn start(
        &self,
        id: Uuid,
        remote: &Self,
        source: String,
        host: bool,
        signals: mpsc::Receiver<Signal>,
        #[cfg(target_os = "linux")] portal: Option<Arc<crate::wayland_portal::Grant>>,
    ) {
        let expires = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs()
            + 90;
        remote_peer::start(remote_peer::Config {
            state: self.session.clone(),
            generation: 0,
            session: id,
            nonce: [123; 32],
            host,
            login_window: false,
            remote_key: remote.identity.public_key().unwrap(),
            identity: self.identity.clone(),
            source,
            #[cfg(target_os = "linux")]
            portal,
            confirm: host,
            control: host,
            clipboard: false,
            files: host,
            chat: false,
            recording: host,
            consent_id: self.consent_id,
            consent_cancel: self.consent_cancel.clone(),
            consent_started: std::time::Instant::now(),
            session_cancel: self.cancellation.clone(),
            account_cancel: self.account_cancel.clone(),
            signals,
            commands: self.outgoing.clone(),
            video: self.video.clone(),
            relay_only: false,
            ice_servers: Vec::new(),
            authorization: Arc::new(std::sync::Mutex::new((
                expires,
                std::time::Instant::now() + Duration::from_secs(60),
            ))),
            peer_expires: Arc::new(AtomicU64::new(expires)),
        })
        .unwrap();
    }
}

async fn relay(
    mut outgoing: mpsc::Receiver<Command>,
    incoming: mpsc::Sender<Signal>,
) -> Result<(), String> {
    while let Some(command) = outgoing.recv().await {
        match command {
            Command::Signal { signal, .. } => {
                let _ = incoming.send(signal).await;
            }
            Command::Finished { result, .. } => return result,
            _ => return Err("Unexpected fixture command".into()),
        }
    }
    Err("Fixture peer disconnected without cleanup".into())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "Run only on an isolated signed-in test desktop"]
async fn native_accept_shares_real_pixels_and_injects_physical_input() {
    #[cfg(target_os = "linux")]
    let (desktop, root, source, center) = {
        let (desktop, screen) = x11rb::connect(None).unwrap();
        let root = desktop.setup().roots[screen].root;
        desktop
            .change_window_attributes(
                root,
                &ChangeWindowAttributesAux::new().background_pixel(0x00b33721),
            )
            .unwrap()
            .check()
            .unwrap();
        desktop
            .clear_area(false, root, 0, 0, 0, 0)
            .unwrap()
            .check()
            .unwrap();
        (desktop, root, format!("x11:{screen}:{root}"), (319, 239))
    };
    #[cfg(target_os = "windows")]
    let (source, center) = {
        let display = native_os_capture::windows_capture::displays()
            .unwrap()
            .into_iter()
            .next()
            .unwrap();
        {
            let mut raw =
                native_os_capture::windows_capture::WindowsCapture::open(&display).unwrap();
            let limit = std::time::Instant::now() + Duration::from_secs(4);
            loop {
                if let Some(frame) = raw.next_frame(Duration::from_millis(100)).unwrap()
                    && frame
                        .pixels()
                        .as_chunks::<4>()
                        .0
                        .iter()
                        .any(|pixel| pixel[..3] != frame.pixels()[..3])
                {
                    break;
                }
                assert!(
                    std::time::Instant::now() < limit,
                    "Windows capture returned no visible desktop pixels"
                );
            }
        }
        (
            format!(
                "dxgi:{}:{}:{}",
                display.adapter, display.output, display.name
            ),
            (
                display.left + (display.width as i32 - 1) / 2,
                display.top + (display.height as i32 - 1) / 2,
            ),
        )
    };

    let id = Uuid::new_v4();
    let (host, mut host_commands) = FixturePeer::new(id, true).await;
    let (viewer, viewer_commands) = FixturePeer::new(id, false).await;
    assert!(viewer.session.next_video(id, false).await.unwrap().is_empty());
    assert!(
        !viewer
            .session
            .input(
                id,
                InputEvent::Key {
                    usage: 4,
                    down: true
                }
            )
            .await
            .unwrap()
    );
    assert!(host_commands.try_recv().is_err());
    host.session
        .decide(Decision {
            session: id,
            accept: true,
            source: source.clone(),
            confirm: true,
            control: true,
            clipboard: false,
            files: true,
            chat: false,
            recording: true,
        })
        .await
        .unwrap();
    assert!(matches!(
        host_commands.recv().await,
        Some(Command::Decide {
            accept: true,
            control: true,
            ..
        })
    ));

    let (to_host, host_signals) = mpsc::channel(32);
    let (to_viewer, viewer_signals) = mpsc::channel(32);
    let host_finished = tokio::spawn(relay(host_commands, to_viewer));
    let viewer_finished = tokio::spawn(relay(viewer_commands, to_host));
    host.start(
        id,
        &viewer,
        source,
        true,
        host_signals,
        #[cfg(target_os = "linux")]
        None,
    );
    viewer.start(
        id,
        &host,
        String::new(),
        false,
        viewer_signals,
        #[cfg(target_os = "linux")]
        None,
    );

    let pixels = tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            let video = viewer.session.next_video(id, false).await.unwrap();
            if !video.is_empty() {
                break video;
            }
            assert!(
                !host_finished.is_finished(),
                "Host stopped before its first frame"
            );
            assert!(
                !viewer_finished.is_finished(),
                "Controller stopped before its first frame"
            );
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .expect("No desktop video arrived");
    assert_eq!(&pixels[..4], b"MRF2");
    let width = u32::from_le_bytes(pixels[4..8].try_into().unwrap()) as usize;
    let height = u32::from_le_bytes(pixels[8..12].try_into().unwrap()) as usize;
    assert!(width >= 320 && height >= 240 && width <= 1280 && height <= 720);
    assert_eq!(pixels.len(), 32 + width * height * 4);
    let data = &pixels[32..];
    #[cfg(target_os = "linux")]
    assert!(
        data[0].abs_diff(0x21) < 18 && data[1].abs_diff(0x37) < 18 && data[2].abs_diff(0xb3) < 18,
        "Received display pixel {:?}",
        &data[..4]
    );
    #[cfg(target_os = "windows")]
    assert!(
        data.as_chunks::<4>()
            .0
            .iter()
            .any(|pixel| pixel[..3] != data[..3])
    );
    assert!(
        viewer
            .session
            .input(id, InputEvent::Pointer { x: 32768, y: 32768 })
            .await
            .unwrap()
    );
    #[cfg(target_os = "linux")]
    let position = {
        let pointer = desktop.query_pointer(root).unwrap().reply().unwrap();
        (i32::from(pointer.root_x), i32::from(pointer.root_y))
    };
    #[cfg(target_os = "windows")]
    let position = windows_pointer();
    assert!(
        (position.0 - center.0).abs() <= 2 && (position.1 - center.1).abs() <= 2,
        "{position:?} vs {center:?}"
    );
    #[cfg(target_os = "linux")]
    assert!(
        !viewer
            .session
            .input(id, InputEvent::Text { text: "é".into() })
            .await
            .unwrap()
    );
    #[cfg(target_os = "linux")]
    let before = desktop.query_keymap().unwrap().reply().unwrap().keys;
    #[cfg(target_os = "windows")]
    let before = windows_a_pressed();
    assert!(
        viewer
            .session
            .input(
                id,
                InputEvent::Key {
                    usage: 4,
                    down: true
                }
            )
            .await
            .unwrap()
    );
    #[cfg(target_os = "linux")]
    let held = desktop.query_keymap().unwrap().reply().unwrap().keys;
    #[cfg(target_os = "windows")]
    let held = windows_a_pressed();
    assert_ne!(held, before);
    println!(
        "PASS: Accept unlocked real desktop pixels and acknowledged native pointer and key events"
    );
    let tmp = tempfile::tempdir().unwrap();
    let source = tmp.path().join("source.bin");
    let destination = tmp.path().join("received.bin");
    let contents = b"Mixel native file bytes\0\x01";
    std::fs::write(&source, contents).unwrap();
    let send = viewer.session.file_scope(viewer.consent_id).await.unwrap();
    let receive = host.session.file_scope(host.consent_id).await.unwrap();
    send.offer(source).await.unwrap();
    let offer = tokio::time::timeout(Duration::from_secs(8), async {
        loop {
            if let Some(offer) = receive.view().unwrap().incoming {
                break offer.id;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
    })
    .await
    .unwrap();
    receive.accept(offer, destination.clone()).await.unwrap();
    tokio::time::timeout(Duration::from_secs(8), async {
        while !destination.exists() {
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(std::fs::read(&destination).unwrap(), contents);
    println!("PASS: separately accepted file received byte-for-byte over the encrypted session");

    let recording = viewer.session.recording_hub();
    let movie = tmp.path().join("session.mp4");
    recording.start(id, movie.clone()).unwrap();
    tokio::time::timeout(Duration::from_secs(8), async {
        while !matches!(
            recording.view(id),
            Some(crate::remote_recording::View::Recording)
        ) {
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
    })
    .await
    .unwrap();
    #[cfg(target_os = "linux")]
    {
        desktop
            .change_window_attributes(
                root,
                &ChangeWindowAttributesAux::new().background_pixel(0x0036a087),
            )
            .unwrap()
            .check()
            .unwrap();
        desktop
            .clear_area(false, root, 0, 0, 0, 0)
            .unwrap()
            .check()
            .unwrap();
    }
    tokio::time::sleep(Duration::from_millis(200)).await;
    recording.stop(id).unwrap();
    tokio::time::timeout(Duration::from_secs(8), async {
        while !matches!(
            recording.view(id),
            Some(crate::remote_recording::View::Saved)
        ) {
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
    })
    .await
    .unwrap();
    let file = std::fs::File::open(movie).unwrap();
    let size = file.metadata().unwrap().len();
    let player = mp4::Mp4Reader::read_header(std::io::BufReader::new(file), size).unwrap();
    assert_eq!(player.tracks().len(), 1);
    println!("PASS: host-authorized recording produced a readable MP4");
    host.consent_cancel.store(true, Ordering::Release);
    viewer.cancellation.store(true, Ordering::Release);
    tokio::time::timeout(Duration::from_secs(12), host_finished)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    tokio::time::timeout(Duration::from_secs(12), viewer_finished)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    #[cfg(target_os = "linux")]
    assert_eq!(
        desktop.query_keymap().unwrap().reply().unwrap().keys,
        before
    );
    #[cfg(target_os = "windows")]
    assert_eq!(windows_a_pressed(), before);
    assert!(viewer.session.next_video(id, false).await.unwrap().is_empty());
    println!("PASS: revoke released held keys and cleared retained frames");
}

#[cfg(target_os = "windows")]
#[allow(unsafe_code)] // GetCursorPos fills a single initialized POINT.
fn windows_pointer() -> (i32, i32) {
    let mut point = windows::Win32::Foundation::POINT::default();
    unsafe { windows::Win32::UI::WindowsAndMessaging::GetCursorPos(&mut point) }.unwrap();
    (point.x, point.y)
}

#[cfg(target_os = "windows")]
#[allow(unsafe_code)] // GetAsyncKeyState is a read-only desktop state query.
fn windows_a_pressed() -> bool {
    unsafe { windows::Win32::UI::Input::KeyboardAndMouse::GetAsyncKeyState(0x41) < 0 }
}

#[cfg(target_os = "linux")]
#[tokio::test(flavor = "multi_thread", worker_threads = 3)]
#[ignore = "Requires a disposable signed-in Wayland desktop and local approval in its compositor dialog"]
async fn wayland_accept_shares_pipewire_pixels_and_posts_portal_input() {
    use crate::wayland_portal::SOURCE;
    use std::process::{Child, Command as Process, Stdio};

    const FIXTURE: &str = r#"
import gi, sys
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk
window = Gtk.Window(title='Mixel Remote Wayland QA')
window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.KEY_RELEASE_MASK)
window.connect('button-press-event', lambda _, event: print('pointer:{}'.format(event.button), flush=True) or False)
window.connect('key-press-event', lambda _, event: print('key:{}'.format(Gdk.keyval_name(event.keyval)), flush=True) or False)
window.connect('key-release-event', lambda _, event: print('release:{}'.format(Gdk.keyval_name(event.keyval)), flush=True) or False)
label = Gtk.Label(label='Mixel Remote Wayland input fixture')
window.add(label)
window.connect('map-event', lambda *_: print('ready', flush=True) or False)
window.connect('destroy', Gtk.main_quit)
window.fullscreen()
window.show_all()
Gtk.main()
"#;

    struct ChildGuard(Child);
    impl Drop for ChildGuard {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    let id = Uuid::new_v4();
    let (host, mut host_commands) = FixturePeer::new(id, true).await;
    let (viewer, viewer_commands) = FixturePeer::new(id, false).await;
    assert!(viewer.session.next_video(id, false).await.unwrap().is_empty());
    assert!(host_commands.try_recv().is_err());
    println!("Waiting for local Wayland compositor approval");
    host.session
        .decide(Decision {
            session: id,
            accept: true,
            source: SOURCE.into(),
            confirm: true,
            control: true,
            clipboard: false,
            files: true,
            chat: false,
            recording: true,
        })
        .await
        .unwrap();
    let Some(Command::Decide {
        accept: true,
        portal: Some(grant),
        ..
    }) = host_commands.recv().await
    else {
        panic!("Accept did not carry the compositor sharing grant");
    };

    let temp = tempfile::tempdir().unwrap();
    let observed = temp.path().join("observed.txt");
    let log = std::fs::File::create(&observed).unwrap();
    let _fixture = ChildGuard(
        Process::new("/usr/bin/python3")
            .args(["-c", FIXTURE])
            .env("GDK_BACKEND", "wayland")
            .env("NO_AT_BRIDGE", "1")
            .stdout(Stdio::from(log))
            .spawn()
            .unwrap(),
    );
    async fn observe(path: &std::path::Path, expected: &str) {
        tokio::time::timeout(Duration::from_secs(6), async {
            loop {
                if std::fs::read_to_string(path).unwrap().contains(expected) {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(30)).await;
            }
        })
        .await
        .unwrap_or_else(|_| {
            panic!(
                "Wayland fixture never received {expected:?}: {:?}",
                std::fs::read_to_string(path)
            )
        });
    }
    observe(&observed, "ready").await;
    let (to_host, host_signals) = mpsc::channel(32);
    let (to_viewer, viewer_signals) = mpsc::channel(32);
    let host_finished = tokio::spawn(relay(host_commands, to_viewer));
    let viewer_finished = tokio::spawn(relay(viewer_commands, to_host));
    host.start(
        id,
        &viewer,
        SOURCE.into(),
        true,
        host_signals,
        Some(grant.clone()),
    );
    viewer.start(id, &host, String::new(), false, viewer_signals, None);
    let frame = tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            let frame = viewer.session.next_video(id, false).await.unwrap();
            if !frame.is_empty() {
                break frame;
            }
            assert!(
                !host_finished.is_finished() && !viewer_finished.is_finished(),
                "Wayland session stopped before video arrived"
            );
            tokio::time::sleep(Duration::from_millis(30)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(&frame[..4], b"MRF2");
    assert!(
        frame[32..]
            .as_chunks::<4>()
            .0
            .iter()
            .any(|pixel| pixel[..3] != frame[32..35])
    );
    println!("PASS: approved Wayland monitor supplied real PipeWire video over encrypted WebRTC");
    assert!(
        viewer
            .session
            .input(id, InputEvent::Pointer { x: 32768, y: 32768 })
            .await
            .unwrap()
    );
    for down in [true, false] {
        assert!(
            viewer
                .session
                .input(
                    id,
                    InputEvent::ButtonClick {
                        button: 1,
                        down,
                        clicks: 1
                    }
                )
                .await
                .unwrap()
        );
    }
    observe(&observed, "pointer:1").await;
    for down in [true, false] {
        assert!(
            viewer
                .session
                .input(id, InputEvent::Key { usage: 4, down })
                .await
                .unwrap()
        );
    }
    observe(&observed, "key:a").await;
    observe(&observed, "release:a").await;
    println!(
        "PASS: independent Wayland GTK fixture received the actual pointer click and keyboard events"
    );
    host.consent_cancel.store(true, Ordering::Release);
    viewer.cancellation.store(true, Ordering::Release);
    let _ = tokio::time::timeout(Duration::from_secs(12), host_finished)
        .await
        .unwrap()
        .unwrap();
    let _ = tokio::time::timeout(Duration::from_secs(12), viewer_finished)
        .await
        .unwrap()
        .unwrap();
    assert!(!grant.live());
    assert!(viewer.session.next_video(id, false).await.unwrap().is_empty());
    println!("PASS: revocation stopped Wayland input and cleared retained frames");
}
