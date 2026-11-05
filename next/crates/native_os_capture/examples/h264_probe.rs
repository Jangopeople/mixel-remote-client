#[cfg(target_os = "macos")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use native_os_capture::{macos_encoder::HardwareH264Encoder, stream::CapturedFrame};
    use std::{fs, io::Write, path::PathBuf};
    let output = PathBuf::from(
        std::env::args()
            .nth(1)
            .ok_or("new output directory is required")?,
    );
    fs::create_dir(&output)?;
    let mut file = fs::File::create(output.join("hardware.h264"))?;
    let mut encoder = HardwareH264Encoder::new(640, 420, 30, 2_000_000)?;
    let hardware = encoder.hardware_active()?;
    let mut keyframes = 0;
    let start = std::time::Instant::now();
    for index in 0..60u64 {
        let mut pixels = vec![0u8; 640 * 420 * 4];
        for y in 0..420 {
            for x in 0..640 {
                let p = (y * 640 + x) * 4;
                let (r, g, b) = if x < 320 {
                    (16, 208, 80)
                } else {
                    (32, 64, 220)
                };
                pixels[p] = b;
                pixels[p + 1] = g;
                pixels[p + 2] = r;
                pixels[p + 3] = 255;
                if y > 180 && y < 220 && x > index as usize * 8 && x < index as usize * 8 + 25 {
                    pixels[p..p + 3].fill(255);
                }
            }
        }
        let frame = CapturedFrame::bgra(640, 420, 2560, pixels)?;
        let encoded = encoder.encode(&frame, index * 33333, index == 30)?;
        if encoded.keyframe {
            keyframes += 1;
        }
        file.write_all(&encoded.annex_b)?;
    }
    file.flush()?;
    fs::write(
        output.join("result.txt"),
        format!(
            "hardware={hardware}\nframes=60\nkeyframes={keyframes}\nwidth=640\nheight=420\nelapsed_ms={}\n",
            start.elapsed().as_millis()
        ),
    )?;
    println!("PASS: hardware={hardware}; 60 encoded H264 frames; {keyframes} keyframes");
    Ok(())
}
#[cfg(not(target_os = "macos"))]
fn main() {
    eprintln!("This hardware probe requires macOS");
    std::process::exit(1);
}
