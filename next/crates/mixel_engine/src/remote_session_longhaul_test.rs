//! LAB ONLY: a real session (host + technician, the production engine, real WebRTC, real encoder
//! and decoder) across an emulated long-distance line, measured end to end. Each side runs as its
//! own process (its own network namespace, joined only through the line emulator), so the run is:
//!
//! ```text
//! Xvfb :77 -screen 0 1920x1080x24 &
//! LONGHAUL_ROLE=host   LONGHAUL_SOCK=/tmp/lh.sock DISPLAY=:77 ip netns exec hostns <test bin> --ignored --exact remote_session::remote_session_longhaul_test::longhaul &
//! LONGHAUL_ROLE=viewer LONGHAUL_SOCK=/tmp/lh.sock LONGHAUL_SECS=60 ip netns exec techns <test bin> --ignored --exact remote_session::remote_session_longhaul_test::longhaul
//! ```
//!
//! The host draws a moving "document" (a scrolling page of text-like blocks, 1920x1080) and
//! stamps every drawn frame with the wall clock in a row of black and white squares. The
//! technician reads the stamp out of every decoded picture: latency is "now - stamp" (both
//! processes share one clock), freshness is how far the stamps advance. Input: the technician
//! moves the pointer every 250 ms to a position that encodes a counter; the host paints the
//! counter it saw into a second row of squares, so the technician also measures pointer-to-picture
//! latency (the round trip a person feels when typing or moving the mouse).
use super::*;
use std::io::{BufRead, BufReader, Write as _};
use std::time::Instant;
use std::os::unix::net::{UnixListener, UnixStream};
use x11rb::{
    connection::Connection,
    protocol::xproto::{ConnectionExt, CreateGCAux, CreateWindowAux, EventMask, Rectangle, WindowClass},
};

const SQUARE: u16 = 24;
const BITS: u16 = 32;

fn now_ms() -> u32 {
    (SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() % (1u128 << 32)) as u32
}

/// Host: a full screen window with a scrolling page and the two stamp rows.
fn draw_loop(stop: Arc<AtomicBool>) {
    let (conn, screen) = x11rb::connect(None).unwrap();
    let root = conn.setup().roots[screen].clone();
    let (w, h) = (root.width_in_pixels, root.height_in_pixels);
    let win = conn.generate_id().unwrap();
    conn.create_window(
        root.root_depth,
        win,
        root.root,
        0,
        0,
        w,
        h,
        0,
        WindowClass::INPUT_OUTPUT,
        0,
        &CreateWindowAux::new()
            .background_pixel(0xffffff)
            .override_redirect(1)
            .event_mask(EventMask::POINTER_MOTION),
    )
    .unwrap();
    conn.map_window(win).unwrap();
    let gc = conn.generate_id().unwrap();
    conn.create_gc(gc, win, &CreateGCAux::new().foreground(0)).unwrap();
    conn.flush().unwrap();
    let speed: i32 = std::env::var("LONGHAUL_SCROLL").ok().and_then(|v| v.parse().ok()).unwrap_or(6);
    let mut offset: i32 = 0;
    let heavy: u32 = std::env::var("LONGHAUL_HEAVY").ok().and_then(|v| v.parse().ok()).unwrap_or(0);
    let mut seen_counter: u32 = 0;
    let started = Instant::now();
    let mut frame: u64 = 0;
    while !stop.load(Ordering::Acquire) {
        // The pointer position the technician sent encodes its counter (x + 1000 * y, both from 100).
        let pointer = conn.query_pointer(root.root).unwrap().reply().unwrap();
        let (px, py) = (i32::from(pointer.root_x), i32::from(pointer.root_y));
        if px >= 100 && py >= 100 {
            seen_counter = (((px - 100) + 5) / 10 + 100 * (((py - 100) + 5) / 10)) as u32;
        }
        offset += speed;
        // Page: white background, rows of grey "words" that scroll up.
        conn.change_gc(gc, &x11rb::protocol::xproto::ChangeGCAux::new().foreground(0xffffff)).unwrap();
        conn.poly_fill_rectangle(win, gc, &[Rectangle { x: 0, y: 100, width: w, height: h - 100 }]).unwrap();
        let mut words = Vec::new();
        let line_h = 22;
        let first = offset / line_h;
        for line in first..first + (i32::from(h) - 100) / line_h + 2 {
            let y = 100 + line * line_h - offset;
            if y < 100 || y > i32::from(h) - 20 {
                continue;
            }
            let mut x = 40i32;
            let mut seed = (line as u32).wrapping_mul(2654435761);
            while x < i32::from(w) - 200 {
                seed ^= seed << 13;
                seed ^= seed >> 17;
                seed ^= seed << 5;
                let len = 20 + (seed % 90) as i32;
                words.push(Rectangle { x: x as i16, y: y as i16, width: len as u16, height: 12 });
                x += len + 10;
            }
        }
        conn.change_gc(gc, &x11rb::protocol::xproto::ChangeGCAux::new().foreground(0x303840)).unwrap();
        conn.poly_fill_rectangle(win, gc, &words).unwrap();
        // Heavy mode: a "photo" area that changes every frame (a video, a slideshow, a busy page).
        if heavy > 0 {
            let mut seed = frame as u32 ^ 0x9e3779b9;
            for _ in 0..heavy {
                seed ^= seed << 13;
                seed ^= seed >> 17;
                seed ^= seed << 5;
                let color = seed & 0xffffff;
                let x = 1100 + (seed % 700) as i16;
                let y = 200 + ((seed >> 10) % 500) as i16;
                conn.change_gc(gc, &x11rb::protocol::xproto::ChangeGCAux::new().foreground(color)).unwrap();
                conn.poly_fill_rectangle(win, gc, &[Rectangle { x, y, width: 20 + (seed % 60) as u16, height: 20 + ((seed >> 8) % 60) as u16 }]).unwrap();
            }
        }
        // Stamp rows: row 0 the clock, row 1 the technician's pointer counter.
        for (row, value) in [(0u16, now_ms()), (1, seen_counter)] {
            let mut black = Vec::new();
            let mut white = Vec::new();
            for bit in 0..BITS {
                let r = Rectangle { x: (bit * SQUARE) as i16, y: (row * SQUARE) as i16, width: SQUARE, height: SQUARE };
                if value >> (BITS - 1 - bit) & 1 == 1 { black.push(r) } else { white.push(r) }
            }
            conn.change_gc(gc, &x11rb::protocol::xproto::ChangeGCAux::new().foreground(0)).unwrap();
            conn.poly_fill_rectangle(win, gc, &black).unwrap();
            conn.change_gc(gc, &x11rb::protocol::xproto::ChangeGCAux::new().foreground(0xffffff)).unwrap();
            conn.poly_fill_rectangle(win, gc, &white).unwrap();
        }
        conn.flush().unwrap();
        let _ = conn.get_input_focus().unwrap().reply();
        frame += 1;
        let next = started + Duration::from_micros(frame * 33_333);
        if let Some(wait) = next.checked_duration_since(Instant::now()) {
            std::thread::sleep(wait);
        }
    }
}

fn read_row(data: &[u8], width: usize, height: usize, row: usize) -> u32 {
    let scale = width as f64 / 1920.0;
    let mut value = 0u32;
    for bit in 0..BITS as usize {
        let x = (((bit as f64 + 0.5) * f64::from(SQUARE)) * scale) as usize;
        let y = (((row as f64 + 0.5) * f64::from(SQUARE)) * (height as f64 / 1080.0)) as usize;
        let i = (y * width + x) * 4;
        let lum = (u32::from(data[i]) + u32::from(data[i + 1]) + u32::from(data[i + 2])) / 3;
        value = value << 1 | u32::from(lum < 128);
    }
    value
}

fn pct(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return f64::NAN;
    }
    sorted[((sorted.len() - 1) as f64 * p).round() as usize]
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "LAB: run with the long-haul line emulator (see the module documentation)"]
async fn longhaul() {
    let role = std::env::var("LONGHAUL_ROLE").expect("LONGHAUL_ROLE=host|viewer");
    let sock = std::env::var("LONGHAUL_SOCK").unwrap_or_else(|_| "/tmp/longhaul.sock".into());
    let hosting = role == "host";
    let key = ed25519_dalek::SigningKey::generate(&mut rand_core::OsRng);
    let public = key.verifying_key().to_bytes();
    // Rendezvous: the host listens, the technician connects; first line: id and key.
    let stream = if hosting {
        let _ = std::fs::remove_file(&sock);
        let listener = UnixListener::bind(&sock).unwrap();
        listener.accept().unwrap().0
    } else {
        loop {
            match UnixStream::connect(&sock) {
                Ok(stream) => break stream,
                Err(_) => std::thread::sleep(Duration::from_millis(100)),
            }
        }
    };
    let mut writer = stream.try_clone().unwrap();
    let mut reader = BufReader::new(stream);
    let id = if hosting { Uuid::new_v4() } else { Uuid::nil() };
    writeln!(writer, "{} {}", id, public.iter().map(|b| format!("{b:02x}")).collect::<String>()).unwrap();
    let mut line = String::new();
    reader.read_line(&mut line).unwrap();
    let mut parts = line.split_whitespace();
    let their_id: Uuid = parts.next().unwrap().parse().unwrap();
    let remote_key = ed25519_dalek::VerifyingKey::from_bytes(&&{ let t = parts.next().unwrap(); let v: Vec<u8> = (0..t.len()).step_by(2).map(|i| u8::from_str_radix(&t[i..i + 2], 16).unwrap()).collect(); <[u8; 32]>::try_from(v).unwrap() }).unwrap();
    let id = if hosting { id } else { their_id };

    let session = RemoteSession::default();
    let identity = Arc::new(IdentityState::test_identity(key));
    let video = Arc::new(VideoMailbox::default());
    let cancellation = Arc::new(AtomicBool::new(false));
    let consent_id = Uuid::new_v4();
    let consent_cancel = Arc::new(AtomicBool::new(false));
    let (outgoing, mut commands) = mpsc::channel(32);
    let account_cancel = {
        let mut inner = session.0.lock().await;
        inner.video = Some(video.clone());
        inner.status.phase = if hosting { "incoming" } else { "outgoing" }.into();
        inner.status.sharing = hosting;
        inner.commands = Some(outgoing.clone());
        inner.cancel.clone()
    };
    session.track(0, id, cancellation.clone(), consent_id, consent_cancel.clone(), false, false).await.unwrap();
    let source = {
        let (desktop, screen) = x11rb::connect(None).unwrap();
        let root = desktop.setup().roots[screen].root;
        format!("x11:{screen}:{root}")
    };
    let stop = Arc::new(AtomicBool::new(false));
    if hosting {
        let stop = stop.clone();
        std::thread::spawn(move || draw_loop(stop));
        session
            .decide(Decision {
                session: id,
                accept: true,
                source: source.clone(),
                confirm: true,
                control: true,
                clipboard: false,
                files: false,
                chat: false,
                recording: false,
            })
            .await
            .unwrap();
        assert!(matches!(commands.recv().await, Some(Command::Decide { accept: true, .. })));
    }
    // Signals: engine -> socket, socket -> engine.
    let (to_engine, signals) = mpsc::channel(64);
    std::thread::spawn(move || {
        for line in reader.lines() {
            let Ok(line) = line else { break };
            let signal: Signal = serde_json::from_str(&line).unwrap();
            if to_engine.blocking_send(signal).is_err() {
                break;
            }
        }
    });
    let finished = tokio::spawn(async move {
        while let Some(command) = commands.recv().await {
            match command {
                Command::Signal { signal, .. } => {
                    writeln!(writer, "{}", serde_json::to_string(&signal).unwrap()).unwrap();
                }
                Command::Finished { result, .. } => return result,
                _ => {}
            }
        }
        Err::<(), String>("disconnected".into())
    });
    let expires = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() + 3600;
    remote_peer::start(remote_peer::Config {
        state: session.clone(),
        generation: 0,
        session: id,
        slot: id,
        transport: id,
        link: Default::default(),
        previous: None,
        nonce: [123; 32],
        host: hosting,
        login_window: false,
        remote_key,
        identity: identity.clone(),
        source: if hosting { source } else { String::new() },
        portal: None,
        confirm: hosting,
        control: hosting,
        clipboard: false,
        files: false,
        chat: false,
        recording: false,
        consent_id,
        consent_cancel: consent_cancel.clone(),
        consent_started: std::time::Instant::now(),
        session_cancel: cancellation.clone(),
        account_cancel,
        signals,
        commands: outgoing.clone(),
        video: video.clone(),
        relay_only: false,
        ice_servers: Vec::new(),
        authorization: Arc::new(std::sync::Mutex::new((expires, std::time::Instant::now() + Duration::from_secs(3000)))),
        peer_expires: Arc::new(AtomicU64::new(expires)),
        mirror: false,
        prewarmed: None,
    })
    .unwrap();

    if hosting {
        let _ = finished.await;
        stop.store(true, Ordering::Release);
        return;
    }
    // Technician: measure.
    let secs: u64 = std::env::var("LONGHAUL_SECS").ok().and_then(|v| v.parse().ok()).unwrap_or(60);
    let started = Instant::now();
    let mut first: Option<Duration> = None;
    let mut latencies = Vec::new();
    let mut input_lat = Vec::new();
    let mut frames = 0u32;
    let mut last_frame = Instant::now();
    let mut gaps = Vec::new();
    let mut sizes = std::collections::BTreeMap::<(usize, usize), u32>::new();
    let mut sec_frames = 0u32;
    let mut sec_lat = Vec::new();
    let mut sec_start = Instant::now();
    let mut counter: u32 = 0;
    let mut sent_at = std::collections::HashMap::<u32, Instant>::new();
    let mut last_move = Instant::now();
    let mut last_seen_counter = 0u32;
    let mut stale_secs = 0u32;
    let mut last_stamp = 0u32;
    while started.elapsed() < Duration::from_secs(secs) {
        if finished.is_finished() {
            println!("SESSION ENDED EARLY");
            break;
        }
        if first.is_some() && last_move.elapsed() >= Duration::from_millis(250) {
            counter += 1;
            let (x, y) = (100 + (counter % 100) * 10, 100 + (counter / 100) * 10);
            let input_session = session.clone();
            tokio::spawn(async move {
                let _ = input_session
                    .input(id, InputEvent::Pointer { x: ((x as f64 + 0.5) * 65535.0 / 1920.0) as u16, y: ((y as f64 + 0.5) * 65535.0 / 1080.0) as u16 })
                    .await;
            });
            sent_at.insert(counter, Instant::now());
            last_move = Instant::now();
        }
        let picture = session.next_video(id, false).await.unwrap_or_default();
        if picture.len() > 32 && &picture[..4] == b"MRF2" {
            let width = u32::from_le_bytes(picture[4..8].try_into().unwrap()) as usize;
            let height = u32::from_le_bytes(picture[8..12].try_into().unwrap()) as usize;
            let data = &picture[32..];
            let now = Instant::now();
            if first.is_none() {
                first = Some(started.elapsed());
                println!("first picture after {:?} ({width}x{height})", started.elapsed());
            } else {
                gaps.push(now.duration_since(last_frame).as_secs_f64() * 1000.0);
            }
            last_frame = now;
            frames += 1;
            sec_frames += 1;
            *sizes.entry((width, height)).or_default() += 1;
            let stamp = read_row(data, width, height, 0);
            let lat = f64::from(now_ms().wrapping_sub(stamp));
            if lat < 20_000.0 {
                latencies.push(lat);
                sec_lat.push(lat);
            }
            if stamp == last_stamp {
                stale_secs += 0;
            }
            last_stamp = stamp;
            let seen = read_row(data, width, height, 1);
            if seen > last_seen_counter && seen <= counter {
                for c in last_seen_counter + 1..=seen {
                    if let Some(at) = sent_at.remove(&c) {
                        if c == seen {
                            input_lat.push(now.duration_since(at).as_secs_f64() * 1000.0);
                        }
                    }
                }
                last_seen_counter = seen;
            }
        } else {
            tokio::time::sleep(Duration::from_millis(3)).await;
        }
        if sec_start.elapsed() >= Duration::from_secs(1) {
            sec_lat.sort_by(f64::total_cmp);
            println!(
                "t={:>3}s fps={:>2} lat_p50={:>5.0} lat_max={:>5.0}",
                started.elapsed().as_secs(),
                sec_frames,
                pct(&sec_lat, 0.5),
                pct(&sec_lat, 1.0)
            );
            sec_frames = 0;
            sec_lat.clear();
            sec_start = Instant::now();
        }
    }
    let _ = stale_secs;
    latencies.sort_by(f64::total_cmp);
    input_lat.sort_by(f64::total_cmp);
    let freezes = gaps.iter().filter(|g| **g > 500.0).count();
    let frozen_ms: f64 = gaps.iter().filter(|g| **g > 200.0).sum();
    let measured = started.elapsed().as_secs_f64() - first.map_or(0.0, |f| f.as_secs_f64());
    println!(
        "RESULT first_picture_s={:.2} fps={:.1} lat_p50={:.0} lat_p95={:.0} lat_max={:.0} input_p50={:.0} input_p95={:.0} input_n={} freezes_over_500ms={} frozen_ms_total={:.0} max_gap_ms={:.0} sizes={:?}",
        first.map_or(f64::NAN, |f| f.as_secs_f64()),
        f64::from(frames) / measured.max(0.1),
        pct(&latencies, 0.5),
        pct(&latencies, 0.95),
        pct(&latencies, 1.0),
        pct(&input_lat, 0.5),
        pct(&input_lat, 0.95),
        input_lat.len(),
        freezes,
        frozen_ms,
        gaps.iter().cloned().fold(0.0, f64::max),
        sizes
    );
    cancellation.store(true, Ordering::Release);
    let _ = tokio::time::timeout(Duration::from_secs(5), finished).await;
    std::process::exit(0);
}
