//! Lab stand-in for a phone: registers with a support code, goes online and shares a generated
//! upright picture. Used to prove the host core against the live service without a phone.
//!   cargo run -p quick_host --example lab_host -- --state /tmp/qs.json [--code XXXX-XXXX-XXXX] [--auto-accept] [--view-only]
use openh264::{
    OpenH264API,
    encoder::{BitRate, Encoder, EncoderConfig, FrameRate, Level, Profile, RateControlMode, UsageType},
    formats::{RgbSliceU8, YUVBuffer},
};
use quick_host::{Config, Enrollment, Event, Host, InputEvent, Phase};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};

const WIDTH: usize = 432;
const HEIGHT: usize = 768;

#[derive(serde::Serialize, serde::Deserialize)]
struct State {
    seed: [u8; 32],
    enrollment: Option<Enrollment>,
}

fn main() -> Result<(), String> {
    let arguments: Vec<String> = std::env::args().collect();
    let value = |name: &str| arguments.iter().position(|argument| argument == name).and_then(|index| arguments.get(index + 1)).cloned();
    let flag = |name: &str| arguments.iter().any(|argument| argument == name);
    let path = value("--state").ok_or("--state <file> is required")?;
    let mut state: Option<State> = std::fs::read(&path).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok());

    let keyframe = Arc::new(AtomicBool::new(true));
    let pointer = Arc::new(Mutex::new((0u16, 0u16, false)));
    let pending = Arc::new(AtomicBool::new(false));
    let host = {
        let (keyframe, pointer, pending) = (keyframe.clone(), pointer.clone(), pending.clone());
        Host::new(Config::production(), state.as_ref().map(|state| state.seed), move |event| match event {
            Event::KeyframeWanted => keyframe.store(true, Ordering::Release),
            Event::Request { connection_id, staff } => {
                println!("REQUEST from {connection_id} (team member: {staff})");
                pending.store(true, Ordering::Release);
            }
            Event::Input(InputEvent::Pointer { x, y }) => {
                if let Ok(mut pointer) = pointer.lock() {
                    (pointer.0, pointer.1) = (x, y);
                }
            }
            Event::Input(InputEvent::Button { button, down }) => {
                println!("INPUT button {button} {}", if down { "down" } else { "up" });
                if let Ok(mut pointer) = pointer.lock() {
                    pointer.2 = down;
                    println!("INPUT at {:.3} x {:.3}", f64::from(pointer.0) / 65535.0, f64::from(pointer.1) / 65535.0);
                }
            }
            Event::Input(other) => println!("INPUT {other:?}"),
            Event::Status(phase, message) => println!("STATUS {phase:?} {message}"),
            other => println!("EVENT {other:?}"),
        })?
    };
    if state.is_none() {
        state = Some(State { seed: host.seed(), enrollment: None });
    }
    let state = state.as_mut().ok_or("no state")?;
    if state.enrollment.is_none() {
        let code = value("--code").ok_or("--code is required for the first start")?;
        state.enrollment = Some(host.enroll(&code, "Lab phone (Quick Support test)")?);
        std::fs::write(&path, serde_json::to_vec(state).map_err(|error| error.to_string())?).map_err(|error| error.to_string())?;
    }
    let enrollment = state.enrollment.clone().ok_or("no registration")?;
    println!("REMOTE ID {}", enrollment.connection_id);
    host.start(enrollment, WIDTH as u32, HEIGHT as u32, !flag("--view-only"))?;

    let config = EncoderConfig::new()
        .usage_type(UsageType::CameraVideoRealTime)
        .bitrate(BitRate::from_bps(800_000))
        .max_frame_rate(FrameRate::from_hz(10.0))
        .profile(Profile::Baseline)
        .level(Level::Level_3_1)
        .rate_control_mode(RateControlMode::Bitrate)
        .skip_frames(true);
    let mut encoder = Encoder::with_api_config(OpenH264API::from_source(), config).map_err(|error| error.to_string())?;
    let mut rgb = vec![0u8; WIDTH * HEIGHT * 3];
    let mut number = 0usize;
    let auto = flag("--auto-accept");
    loop {
        if pending.swap(false, Ordering::AcqRel) {
            if auto {
                println!("ACCEPTING");
                host.decide(true);
            } else {
                println!("DECLINING (start with --auto-accept to accept)");
                host.decide(false);
            }
        }
        // A plain upright picture: coloured bands, a square that moves down, and a white mark where the technician presses.
        let (px, py, down) = pointer.lock().map(|value| *value).unwrap_or((0, 0, false));
        let (px, py) = (usize::from(px) * WIDTH / 65536, usize::from(py) * HEIGHT / 65536);
        let square = (number * 6) % (HEIGHT - 60);
        for y in 0..HEIGHT {
            for x in 0..WIDTH {
                let band = y * 4 / HEIGHT;
                let mut colour = [[30u8, 60, 120], [20, 110, 70], [120, 70, 20], [90, 30, 100]][band];
                if (180..240).contains(&x) && (square..square + 60).contains(&y) {
                    colour = [240, 200, 40];
                }
                if down && x.abs_diff(px) < 14 && y.abs_diff(py) < 14 {
                    colour = [255, 255, 255];
                }
                rgb[(y * WIDTH + x) * 3..(y * WIDTH + x) * 3 + 3].copy_from_slice(&colour);
            }
        }
        if keyframe.swap(false, Ordering::AcqRel) {
            encoder.force_intra_frame();
        }
        let yuv = YUVBuffer::from_rgb_source(RgbSliceU8::new(&rgb, (WIDTH, HEIGHT)));
        let bits = encoder.encode(&yuv).map_err(|error| error.to_string())?.to_vec();
        if !bits.is_empty() {
            host.push_frame(&bits);
        }
        number += 1;
        std::thread::sleep(std::time::Duration::from_millis(100));
        let _ = Phase::Ready;
    }
}
