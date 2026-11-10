#![cfg(target_os = "linux")]

use native_os_capture::x11_capture::{Display, X11Capture, displays};
use std::time::Duration;
use x11rb::{
    connection::Connection,
    protocol::xproto::{ConnectionExt, CreateGCAux, Rectangle},
};

#[test]
#[ignore = "requires a disposable Xvfb with two screens"]
fn captures_separate_xvfb_screens_and_converts_native_pixel_formats() {
    assert_eq!(
        std::env::var("MIXEL_X11_FIXTURE").as_deref(),
        Ok("isolated-xvfb")
    );
    let (paint, _) = x11rb::connect(None).unwrap();
    let outputs = displays().unwrap();
    assert_eq!(outputs.len(), 2);
    let mut expected = Vec::new();
    for (index, output) in outputs.iter().enumerate() {
        let root = &paint.setup().roots[index];
        assert_eq!(root.root, output.root);
        let visual = root
            .allowed_depths
            .iter()
            .flat_map(|depth| &depth.visuals)
            .find(|visual| visual.visual_id == root.root_visual)
            .unwrap();
        let colors = if index == 0 {
            (visual.red_mask, [0, 0, 255, 255])
        } else {
            (visual.green_mask, [0, 255, 0, 255])
        };
        expected.push(colors.1);
        let gc = paint.generate_id().unwrap();
        paint
            .create_gc(gc, output.root, &CreateGCAux::new().foreground(colors.0))
            .unwrap()
            .check()
            .unwrap();
        paint
            .poly_fill_rectangle(
                output.root,
                gc,
                &[Rectangle {
                    x: 0,
                    y: 0,
                    width: output.width,
                    height: output.height,
                }],
            )
            .unwrap()
            .check()
            .unwrap();
        paint.free_gc(gc).unwrap().check().unwrap();
    }
    for (output, color) in outputs.iter().zip(expected) {
        let mut capture = X11Capture::open(output).unwrap();
        let frame = capture.next_frame(Duration::from_secs(1)).unwrap().unwrap();
        assert_eq!(
            (frame.width(), frame.height()),
            (usize::from(output.width), usize::from(output.height))
        );
        assert!(frame.pixels().chunks_exact(4).all(|pixel| pixel == color));
        println!(
            "PASS: X11 screen {} captured at {}×{} and converted the painted color exactly",
            output.screen,
            frame.width(),
            frame.height()
        );
    }
    let first = &outputs[0];
    let stale = Display {
        screen: first.screen,
        root: outputs[1].root,
        width: first.width,
        height: first.height,
    };
    assert!(X11Capture::open(&stale).is_err());
}

#[test]
#[ignore = "run with Wayland and X11 environment variables together"]
fn refuses_xwayland_fallback() {
    assert_eq!(std::env::var("XDG_SESSION_TYPE").as_deref(), Ok("wayland"));
    assert!(
        displays()
            .err()
            .unwrap()
            .to_string()
            .contains("screen-sharing portal")
    );
    println!("PASS: the Wayland environment refuses the X11 capture fallback");
}
