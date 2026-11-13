//! Privacy screen, input block and pointer ring for a Linux computer in an X11 session.
//! - privacy screen: the display outputs get a black colour table (RandR gamma). The picture the
//!   technician receives is read from the screen contents, which do not change, so they keep
//!   seeing the desktop while the monitors are dark.
//! - block input: the computer's own keyboards and mice are switched off (XInput 2 "Device
//!   Enabled"); the technician's input arrives through the XTEST devices and keeps working.
//! - pointer: a small red ring (a shaped window that lets clicks through).
//!
//! A helper process watches this app: if it ends while the screen is dark or the input blocked
//! (a crash, a kill), the helper gives the person at the computer screen and input back.
//! Wayland allows none of this for other programs (the host announces `wayland`).
use std::{
    path::PathBuf,
    sync::{Mutex, mpsc},
};
use x11rb::{
    connection::Connection,
    protocol::{
        randr::ConnectionExt as _,
        shape::{self, ConnectionExt as _},
        xinput::{self, ConnectionExt as _},
        xproto::{self, ConnectionExt as _},
    },
    rust_connection::RustConnection,
};

/// X11 desktop (not Wayland): the protections can be offered.
pub fn x11_available() -> bool {
    !crate::wayland_portal::available() && std::env::var_os("DISPLAY").is_some_and(|value| !value.is_empty())
}

#[derive(Default)]
struct State {
    /// Colour tables before the screen went dark, per display output.
    gamma: Option<Vec<(u32, Vec<u16>, Vec<u16>, Vec<u16>)>>,
    /// Input devices switched off.
    blocked: Vec<u16>,
    watchdog: Option<std::process::Child>,
}
static STATE: Mutex<Option<State>> = Mutex::new(None);

fn connect() -> Result<(RustConnection, usize), String> {
    x11rb::connect(None).map_err(|_| "The X11 display is not available".to_owned())
}

fn fail<E>(_: E) -> String {
    "The X11 display refused the change".into()
}

/// The file the helper reads after a crash: which devices to switch on, whether to light the screen.
fn rescue_file() -> PathBuf {
    let folder = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).unwrap_or_else(std::env::temp_dir);
    folder.join("mixel-remote-x11-guard")
}

/// Writes what must be undone after a crash, and makes sure the helper watches this app.
fn arm(state: &mut State) {
    let dark = state.gamma.is_some();
    if !dark && state.blocked.is_empty() {
        let _ = std::fs::remove_file(rescue_file());
        if let Some(mut helper) = state.watchdog.take() {
            let _ = helper.kill();
            let _ = helper.wait();
        }
        return;
    }
    let devices: Vec<String> = state.blocked.iter().map(u16::to_string).collect();
    let _ = std::fs::write(rescue_file(), format!("dark={}\ndevices={}\n", u8::from(dark), devices.join(",")));
    let running = state.watchdog.as_mut().is_some_and(|helper| helper.try_wait().is_ok_and(|done| done.is_none()));
    if !running && let Ok(program) = std::env::current_exe() {
        state.watchdog = std::process::Command::new(program)
            .args([RESTORE_ARGUMENT, &std::process::id().to_string()])
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .ok();
    }
}

/// Command line of the helper: `--x11-restore <pid of the app>`.
pub const RESTORE_ARGUMENT: &str = "--x11-restore";

#[derive(Debug, PartialEq, Eq, Default)]
struct Rescue {
    dark: bool,
    devices: Vec<u16>,
}

fn parse_rescue(text: &str) -> Rescue {
    let mut rescue = Rescue::default();
    for line in text.lines() {
        if let Some(value) = line.strip_prefix("dark=") {
            rescue.dark = value.trim() == "1";
        } else if let Some(value) = line.strip_prefix("devices=") {
            rescue.devices = value.split(',').filter_map(|id| id.trim().parse().ok()).take(64).collect();
        }
    }
    rescue
}

/// A colour table that shows the picture normally.
fn linear(size: usize) -> Vec<u16> {
    (0..size).map(|index| if size <= 1 { 65535 } else { (index * 65535 / (size - 1)) as u16 }).collect()
}

/// Gives the screen and the input devices back (helper after a crash, or at the next start).
fn rescue_now(rescue: &Rescue) -> Result<(), String> {
    let (connection, screen) = connect()?;
    if rescue.dark {
        let root = connection.setup().roots[screen].root;
        let crtcs = connection.randr_get_screen_resources_current(root).map_err(fail)?.reply().map_err(fail)?.crtcs;
        for crtc in crtcs {
            let size = connection.randr_get_crtc_gamma_size(crtc).map_err(fail)?.reply().map_err(fail)?.size;
            if size > 0 {
                let ramp = linear(usize::from(size));
                let _ = connection.randr_set_crtc_gamma(crtc, &ramp, &ramp, &ramp);
            }
        }
    }
    if !rescue.devices.is_empty() {
        let enabled = enabled_atom(&connection)?;
        for device in &rescue.devices {
            let _ = set_enabled(&connection, enabled, *device, true);
        }
    }
    connection.flush().map_err(fail)?;
    Ok(())
}

/// The helper: waits for the app to end; if it left the screen dark or input blocked, undoes it.
pub fn watch_and_restore(app: u32) -> Result<(), String> {
    loop {
        // SAFETY: signal 0 only checks that the process exists.
        #[allow(unsafe_code)]
        let alive = unsafe { libc::kill(app as libc::pid_t, 0) } == 0;
        let Ok(text) = std::fs::read_to_string(rescue_file()) else { return Ok(()) };
        if !alive {
            let result = rescue_now(&parse_rescue(&text));
            let _ = std::fs::remove_file(rescue_file());
            return result;
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
}

/// At start: a previous run that crashed without its helper left something switched off.
pub fn recover_stale() {
    if !x11_available() {
        return;
    }
    if let Ok(text) = std::fs::read_to_string(rescue_file()) {
        let _ = rescue_now(&parse_rescue(&text));
        let _ = std::fs::remove_file(rescue_file());
    }
}

pub fn privacy(on: bool, _text: &str) -> Result<(), String> {
    let mut guard = STATE.lock().map_err(|_| "Protection state unavailable")?;
    let state = guard.get_or_insert_with(State::default);
    if on == state.gamma.is_some() {
        return Ok(());
    }
    let (connection, screen) = connect()?;
    if on {
        let root = connection.setup().roots[screen].root;
        let crtcs = connection.randr_get_screen_resources_current(root).map_err(fail)?.reply().map_err(fail)?.crtcs;
        let mut saved = Vec::new();
        for crtc in crtcs {
            let size = connection.randr_get_crtc_gamma_size(crtc).map_err(fail)?.reply().map_err(fail)?.size;
            if size == 0 {
                continue;
            }
            let gamma = connection.randr_get_crtc_gamma(crtc).map_err(fail)?.reply().map_err(fail)?;
            saved.push((crtc, gamma.red, gamma.green, gamma.blue));
        }
        if saved.is_empty() {
            return Err("The screen of this computer cannot be darkened (its display driver has no colour table)".into());
        }
        // The helper must be ready before the screen goes dark.
        state.gamma = Some(saved.clone());
        arm(state);
        for (crtc, red, _, _) in &saved {
            let black = vec![0u16; red.len()];
            connection.randr_set_crtc_gamma(*crtc, &black, &black, &black).map_err(fail)?.check().map_err(fail)?;
        }
    } else if let Some(saved) = state.gamma.take() {
        for (crtc, red, green, blue) in &saved {
            let _ = connection.randr_set_crtc_gamma(*crtc, red, green, blue);
        }
        connection.flush().map_err(fail)?;
        arm(state);
    }
    Ok(())
}

fn enabled_atom(connection: &RustConnection) -> Result<u32, String> {
    connection.xinput_xi_query_version(2, 2).map_err(fail)?.reply().map_err(|_| "XInput 2 is not available")?;
    Ok(connection.intern_atom(false, b"Device Enabled").map_err(fail)?.reply().map_err(fail)?.atom)
}

fn set_enabled(connection: &RustConnection, atom: u32, device: u16, enabled: bool) -> Result<(), String> {
    connection
        .xinput_xi_change_property(device, xproto::PropMode::REPLACE, atom, u32::from(xproto::AtomEnum::INTEGER), 1, &xinput::XIChangePropertyAux::Data8(vec![u8::from(enabled)]))
        .map_err(fail)?
        .check()
        .map_err(fail)
}

/// Keyboards and mice of the computer itself. XTEST devices carry the technician's input; the
/// virtual core devices and power/sleep buttons stay.
fn local_device(name: &str, kind: xinput::DeviceType) -> bool {
    matches!(kind, xinput::DeviceType::SLAVE_KEYBOARD | xinput::DeviceType::SLAVE_POINTER)
        && !name.contains("XTEST")
        && !name.starts_with("Virtual core")
        && !["Power Button", "Sleep Button", "Video Bus"].iter().any(|kept| name.contains(kept))
}

pub fn block(on: bool) -> Result<(), String> {
    let mut guard = STATE.lock().map_err(|_| "Protection state unavailable")?;
    let state = guard.get_or_insert_with(State::default);
    if on == !state.blocked.is_empty() {
        return Ok(());
    }
    let (connection, _) = connect()?;
    let atom = enabled_atom(&connection)?;
    if on {
        let devices: Vec<u16> = connection
            .xinput_xi_query_device(xinput::Device::ALL)
            .map_err(fail)?
            .reply()
            .map_err(fail)?
            .infos
            .into_iter()
            .filter(|device| device.enabled && local_device(&String::from_utf8_lossy(&device.name), device.type_))
            .map(|device| device.deviceid)
            .collect();
        if devices.is_empty() {
            return Err("No keyboard or mouse of this computer was found to block".into());
        }
        // The helper knows what to switch on again before anything is switched off.
        state.blocked = devices.clone();
        arm(state);
        for device in devices {
            if let Err(error) = set_enabled(&connection, atom, device, false) {
                for undo in &state.blocked {
                    let _ = set_enabled(&connection, atom, *undo, true);
                }
                state.blocked.clear();
                arm(state);
                return Err(error);
            }
        }
    } else {
        for device in std::mem::take(&mut state.blocked) {
            let _ = set_enabled(&connection, atom, device, true);
        }
        connection.flush().map_err(fail)?;
        arm(state);
    }
    Ok(())
}

/// Pointer ring commands: show at (screen, point) or hide.
type RingCommand = Option<(usize, (u16, u16))>;
static RING: Mutex<Option<mpsc::Sender<RingCommand>>> = Mutex::new(None);
const RING_SIZE: u16 = 34;

/// Shows the technician's pointer at `point` (0..65535 of the shared X11 screen) or hides it.
pub fn pointer(point: Option<(u16, u16)>, source: &str) {
    let screen = source
        .strip_prefix("x11:")
        .and_then(|rest| rest.split(':').next())
        .and_then(|screen| screen.parse::<usize>().ok())
        .unwrap_or(0);
    let Ok(mut ring) = RING.lock() else { return };
    if point.is_none() && ring.is_none() {
        return;
    }
    let command = point.map(|point| (screen, point));
    if ring.as_ref().is_none_or(|sender| sender.send(command).is_err()) {
        let (sender, receiver) = mpsc::channel();
        if std::thread::Builder::new().name("mixel-remote-pointer".into()).spawn(move || ring_thread(receiver)).is_ok() {
            let _ = sender.send(command);
            *ring = Some(sender);
        }
    }
}

fn ring_window(connection: &RustConnection, screen: &xproto::Screen) -> Result<u32, String> {
    let window = connection.generate_id().map_err(fail)?;
    connection
        .create_window(
            screen.root_depth,
            window,
            screen.root,
            0,
            0,
            RING_SIZE,
            RING_SIZE,
            0,
            xproto::WindowClass::INPUT_OUTPUT,
            screen.root_visual,
            &xproto::CreateWindowAux::new().override_redirect(1).background_pixel(0x00BA_2034),
        )
        .map_err(fail)?;
    // A ring: the window's shape is a filled circle with a hole; no input region, so clicks pass.
    let mask = connection.generate_id().map_err(fail)?;
    connection.create_pixmap(1, mask, window, RING_SIZE, RING_SIZE).map_err(fail)?;
    let pen = connection.generate_id().map_err(fail)?;
    connection.create_gc(pen, mask, &xproto::CreateGCAux::new().foreground(0)).map_err(fail)?;
    connection.poly_fill_rectangle(mask, pen, &[xproto::Rectangle { x: 0, y: 0, width: RING_SIZE, height: RING_SIZE }]).map_err(fail)?;
    connection.change_gc(pen, &xproto::ChangeGCAux::new().foreground(1)).map_err(fail)?;
    connection.poly_fill_arc(mask, pen, &[xproto::Arc { x: 1, y: 1, width: RING_SIZE - 2, height: RING_SIZE - 2, angle1: 0, angle2: 360 * 64 }]).map_err(fail)?;
    connection.change_gc(pen, &xproto::ChangeGCAux::new().foreground(0)).map_err(fail)?;
    connection.poly_fill_arc(mask, pen, &[xproto::Arc { x: 10, y: 10, width: RING_SIZE - 20, height: RING_SIZE - 20, angle1: 0, angle2: 360 * 64 }]).map_err(fail)?;
    connection.shape_mask(shape::SO::SET, shape::SK::BOUNDING, window, 0, 0, mask).map_err(fail)?;
    connection.shape_rectangles(shape::SO::SET, shape::SK::INPUT, xproto::ClipOrdering::UNSORTED, window, 0, 0, &[]).map_err(fail)?;
    connection.free_gc(pen).map_err(fail)?;
    connection.free_pixmap(mask).map_err(fail)?;
    Ok(window)
}

fn ring_thread(commands: mpsc::Receiver<RingCommand>) {
    let Ok((connection, _)) = connect() else { return };
    let mut shown: Option<(u32, usize)> = None;
    while let Ok(command) = commands.recv() {
        match command {
            None => {
                if let Some((window, _)) = shown {
                    let _ = connection.unmap_window(window);
                    let _ = connection.flush();
                }
            }
            Some((screen, point)) => {
                let roots = &connection.setup().roots;
                let Some(info) = roots.get(screen).or(roots.first()) else { continue };
                if shown.is_none_or(|(_, on)| on != screen) {
                    if let Some((window, _)) = shown.take() {
                        let _ = connection.destroy_window(window);
                    }
                    let Ok(window) = ring_window(&connection, info) else { continue };
                    shown = Some((window, screen));
                }
                let Some((window, _)) = shown else { continue };
                let (x, y) = crate::session_guard::place(point, 0.0, 0.0, f64::from(info.width_in_pixels), f64::from(info.height_in_pixels));
                let half = f64::from(RING_SIZE / 2);
                let _ = connection.configure_window(
                    window,
                    &xproto::ConfigureWindowAux::new().x((x - half) as i32).y((y - half) as i32).stack_mode(xproto::StackMode::ABOVE),
                );
                let _ = connection.map_window(window);
                let _ = connection.flush();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_computers_own_keyboards_and_mice_are_blocked() {
        assert!(local_device("AT Translated Set 2 keyboard", xinput::DeviceType::SLAVE_KEYBOARD));
        assert!(local_device("ImExPS/2 Generic Explorer Mouse", xinput::DeviceType::SLAVE_POINTER));
        assert!(!local_device("Virtual core XTEST keyboard", xinput::DeviceType::SLAVE_KEYBOARD));
        assert!(!local_device("Virtual core XTEST pointer", xinput::DeviceType::SLAVE_POINTER));
        assert!(!local_device("Virtual core keyboard", xinput::DeviceType::MASTER_KEYBOARD));
        assert!(!local_device("Power Button", xinput::DeviceType::SLAVE_KEYBOARD));
    }

    #[test]
    fn the_rescue_file_round_trips_and_ignores_garbage() {
        assert_eq!(parse_rescue("dark=1\ndevices=8,9,x,12\n"), Rescue { dark: true, devices: vec![8, 9, 12] });
        assert_eq!(parse_rescue("devices=\n"), Rescue::default());
        assert_eq!(parse_rescue("rm -rf /\n"), Rescue::default());
    }

    #[test]
    fn a_linear_colour_table_shows_the_picture_normally() {
        assert_eq!(linear(4), vec![0, 21845, 43690, 65535]);
        assert_eq!(linear(1), vec![65535]);
        assert!(linear(0).is_empty());
    }

    /// Lab check in a real X11 session (Xvfb works): darkening, blocking and the ring, then undone.
    /// `DISPLAY=:99 cargo test -- --ignored x11_protections_switch_on_and_off`
    #[test]
    #[ignore = "needs an X11 display"]
    fn x11_protections_switch_on_and_off() {
        assert!(x11_available(), "DISPLAY must point to an X11 server");
        let (connection, screen) = connect().unwrap();
        let root = connection.setup().roots[screen].root;
        let crtcs = connection.randr_get_screen_resources_current(root).unwrap().reply().unwrap().crtcs;
        let before: Vec<_> = crtcs.iter().map(|crtc| connection.randr_get_crtc_gamma(*crtc).unwrap().reply().ok().map(|gamma| gamma.red)).collect();
        match privacy(true, "test") {
            Ok(()) => {
                let dark: Vec<_> = crtcs.iter().filter_map(|crtc| connection.randr_get_crtc_gamma(*crtc).unwrap().reply().ok()).collect();
                assert!(dark.iter().all(|gamma| gamma.red.iter().all(|value| *value == 0)), "outputs are black");
                assert!(std::fs::read_to_string(rescue_file()).unwrap().contains("dark=1"));
                privacy(false, "").unwrap();
                let after: Vec<_> = crtcs.iter().map(|crtc| connection.randr_get_crtc_gamma(*crtc).unwrap().reply().ok().map(|gamma| gamma.red)).collect();
                assert_eq!(before, after, "colour tables restored");
                println!("privacy screen: darkened and restored {} outputs", crtcs.len());
            }
            Err(error) => println!("privacy screen not possible here: {error}"),
        }
        match block(true) {
            Ok(()) => {
                let atom = enabled_atom(&connection).unwrap();
                let blocked = STATE.lock().unwrap().as_ref().unwrap().blocked.clone();
                for device in &blocked {
                    let value = connection.xinput_xi_get_property(*device, false, atom, 0u32, 0, 1).unwrap().reply().unwrap();
                    assert!(matches!(value.items, xinput::XIGetPropertyItems::Data8(ref items) if items == &[0]), "device {device} off");
                }
                block(false).unwrap();
                for device in &blocked {
                    let value = connection.xinput_xi_get_property(*device, false, atom, 0u32, 0, 1).unwrap().reply().unwrap();
                    assert!(matches!(value.items, xinput::XIGetPropertyItems::Data8(ref items) if items == &[1]), "device {device} on again");
                }
                println!("block input: switched {} devices off and on", blocked.len());
            }
            Err(error) => println!("block input not possible here: {error}"),
        }
        assert!(!rescue_file().exists(), "nothing left to rescue");
        pointer(Some((32768, 32768)), "x11:0:1");
        std::thread::sleep(std::time::Duration::from_millis(300));
        pointer(None, "x11:0:1");
    }
}
