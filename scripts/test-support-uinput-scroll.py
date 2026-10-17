#!/usr/bin/env python3
"""Compile literal uinput routing/emission and exercise exact whole-source guards."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("uinput_patch", ROOT / "scripts/patch-support-uinput-scroll.py")
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def block(source, marker):
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 1
    for end in range(opening + 1, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[start:end + 1]
    raise RuntimeError("Incomplete pinned Rust block: " + marker)


def run(arguments, out, name, expected=0):
    result = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding="utf-8", timeout=60)
    (out / (name + ".stdout")).write_text(result.stdout, encoding="utf-8")
    (out / (name + ".stderr")).write_text(result.stderr, encoding="utf-8")
    (out / (name + ".json")).write_text(json.dumps({"arguments": list(map(str, arguments)),
        "returncode": result.returncode}, indent=2) + "\n", encoding="utf-8")
    assert result.returncode == expected, result.stdout + result.stderr
    return result


def originals(source, out):
    upstream = subprocess.check_output(["git", "-C", str(source), "show", P.PIN + ":" + P.TARGET])
    assert sha(upstream) == P.CANONICAL_HASHES[0]
    branded = upstream.replace(b'RustDesk UInput Keyboard', b'Mixel Remote UInput Keyboard')
    assert sha(branded) == P.CANONICAL_HASHES[1]
    library_pin = subprocess.check_output(["git", "-C", str(source), "ls-tree", P.PIN,
                                          "libs/hbb_common"]).decode().split()[2]
    macro_source = subprocess.check_output(["git", "-C", str(source / "libs/hbb_common"),
                                          "show", library_pin + ":src/lib.rs"]).decode("utf-8")
    macro = block(macro_source, "macro_rules! allow_err")
    bindings = {}
    for name in (P.TARGET, "src/ipc.rs", "src/server/input_service.rs", "libs/enigo/src/lib.rs",
                 "libs/enigo/src/linux/nix_impl.rs", "libs/enigo/src/linux/xdo.rs"):
        data = subprocess.check_output(["git", "-C", str(source), "show", P.PIN + ":" + name])
        path = out / "pinned-source" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        bindings[name] = {"bytes": len(data), "sha256": sha(data)}
    (out / "pinned-allow-err.rs").write_text(macro + "\n", encoding="utf-8")
    (out / "source-bindings.json").write_text(json.dumps({"upstream": P.PIN, "hbb_common": library_pin,
        "files": bindings, "allow_err_sha256": sha(macro.encode())}, indent=2) + "\n", encoding="utf-8")
    return upstream, branded, macro


def snapshot(root):
    result = {}
    for parent, directories, names in os.walk(root):
        for name in directories + names:
            path = Path(parent) / name
            record = path.lstat()
            key = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[key] = ("symlink", os.readlink(path))
            elif path.is_file():
                result[key] = ("file", sha(path.read_bytes()), record.st_nlink)
            else:
                result[key] = ("directory",)
    return result


def guards(out, source_variants):
    count = {"roundtrips": 0, "refusals": 0}
    with tempfile.TemporaryDirectory(prefix="mixel-uinput-guards-", dir=out) as temporary:
        base = Path(temporary)
        for original in source_variants:
            for newline in (b"\n", b"\r\n"):
                data = original.replace(b"\n", newline)
                repo = base / (sha(original)[:8] + ("-lf" if newline == b"\n" else "-crlf"))
                target = repo / P.TARGET
                target.parent.mkdir(parents=True)
                target.write_bytes(data)
                with contextlib.redirect_stdout(io.StringIO()):
                    before = snapshot(repo)
                    P.apply(repo, check=True)
                    P.apply(repo, restore=True, check=True)
                    assert snapshot(repo) == before
                    P.apply(repo)
                    corrected = target.read_bytes()
                    assert corrected == P.transform(data)
                    before = snapshot(repo)
                    P.apply(repo)
                    P.apply(repo, check=True)
                    assert snapshot(repo) == before
                    P.apply(repo, restore=True)
                    assert target.read_bytes() == data
                    P.apply(repo, restore=True)
                    assert target.read_bytes() == data
                count["roundtrips"] += 1
                negatives = [data + b"// unrelated\n", data.replace(b"REL_HWHEEL: c_uint = 0x06", b"REL_HWHEEL: c_uint = 0x07"),
                    data.replace(b"ScrollY(length)", b"ScrollY(other)"), corrected + data, corrected.replace(P.HORIZONTAL.replace("\n",newline.decode()).encode(),b""), corrected.replace(P.NEW.replace("\n",newline.decode()).encode(),P.OLD.replace("\n",newline.decode()).encode()),
                    corrected.replace(P.VERTICAL.replace("\n",newline.decode()).encode(),b""), corrected.replace(P.NEW_VERTICAL.replace("\n",newline.decode()).encode(),P.OLD_VERTICAL.replace("\n",newline.decode()).encode()),
                    corrected.replace(b"REL_HWHEEL as c_int, detents", b"REL_HWHEEL as c_int, -detents"), b"\xff" + data,
                    data.replace(newline, b"\r", 1), data.replace(newline, b"\r\n" if newline == b"\n" else b"\n", 1)]
                for negative_index, candidate in enumerate(negatives):
                    assert candidate not in (data,corrected), f"No-op negative: {negative_index}"
                    target.write_bytes(candidate)
                    before = snapshot(repo)
                    for restore, check in ((False, False), (False, True), (True, False), (True, True)):
                        try:
                            with contextlib.redirect_stdout(io.StringIO()):
                                P.apply(repo, restore, check)
                        except (RuntimeError, UnicodeError):
                            pass
                        else:
                            raise AssertionError(f"Foreign uinput source accepted: {negative_index} {restore} {check}")
                        assert snapshot(repo) == before
                        count["refusals"] += 1
                target.write_bytes(data)
                foreign = repo / "foreign-owned-source.rs"
                foreign.write_bytes(data)
                target.unlink()
                for link in ("symlink", "hardlink"):
                    if link == "symlink":
                        target.symlink_to(foreign)
                    else:
                        os.link(foreign, target)
                    before = snapshot(repo)
                    for restore, check in ((False, False), (False, True), (True, False), (True, True)):
                        try:
                            with contextlib.redirect_stdout(io.StringIO()):
                                P.apply(repo, restore, check)
                        except RuntimeError:
                            pass
                        else:
                            raise AssertionError("Foreign linked uinput source accepted")
                        assert snapshot(repo) == before
                        count["refusals"] += 1
                    target.unlink()
                target.mkdir()
                before = snapshot(repo)
                try:
                    P.apply(repo)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("Directory accepted as uinput source")
                assert snapshot(repo) == before
                count["refusals"] += 1
                target.rmdir()
                before = snapshot(repo)
                try:
                    P.apply(repo)
                except FileNotFoundError:
                    pass
                else:
                    raise AssertionError("Missing source silently accepted")
                assert snapshot(repo) == before
                count["refusals"] += 1
                target.write_bytes(data)
                # A symlinked parent is refused even when its target contains pinned bytes.
                actual = repo / "actual-server"
                target.parent.rename(actual)
                target.parent.symlink_to(actual, target_is_directory=True)
                before = snapshot(repo)
                try:
                    P.apply(repo)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("Linked source parent accepted")
                assert snapshot(repo) == before
                count["refusals"] += 1
    return count


PREFIX = r'''
use std::sync::Mutex;
#[derive(Default)]
pub struct State {
    packets: Vec<(u16, u16, i32)>, sleeps: Vec<u64>, others: Vec<String>, errors: usize,
    fail_at: usize, fail_result: isize,
}
pub static STATE: Mutex<State> = Mutex::new(State {
    packets: Vec::new(), sleeps: Vec::new(), others: Vec::new(), errors: 0,
    fail_at: 0, fail_result: 0,
});
#[macro_export]
macro_rules! trace { ($($arg:tt)*) => { let _ = format_args!($($arg)*); }; }
#[macro_export]
macro_rules! debug { ($($arg:tt)*) => {{
    let _ = format_args!($($arg)*);
    crate::STATE.lock().unwrap().errors += 1;
}}; }
mod log { pub(crate) use crate::{trace, debug}; }
pub mod enigo { #[derive(Debug, Copy, Clone)] pub enum MouseButton { Left, Middle, Right, Side } }
#[derive(Debug)]
'''
MOUSE_STUBS = r'''
    pub struct MockFile;
    impl AsRawFd for MockFile { fn as_raw_fd(&self) -> i32 { 77 } }
    pub struct UInputMouseManager { uinput_file: MockFile }
    impl Default for UInputMouseManager { fn default() -> Self { Self { uinput_file: MockFile } } }
    // Only native write and timing are mocked; event construction and wheel/SYN methods below are literal source.
    unsafe extern "C" fn write(fd: c_int, pointer: *mut InputEvent, count: usize) -> c_long {
        assert_eq!(fd, 77); assert_eq!(count, size_of::<InputEvent>());
        let event = &*pointer;
        assert_eq!(event.time.tv_sec, 0); assert_eq!(event.time.tv_usec, 0);
        let mut state = crate::STATE.lock().unwrap();
        state.packets.push((event.r#type, event.code, event.value));
        if state.fail_at == state.packets.len() { state.fail_result as c_long } else { count as c_long }
    }
    mod thread {
        pub fn sleep(duration: std::time::Duration) {
            crate::STATE.lock().unwrap().sleeps.push(duration.as_millis() as u64);
        }
    }
    impl UInputMouseManager {
        pub fn move_to(&self, x: usize, y: usize) -> Result<()> { self.other(format!("to:{x}:{y}")) }
        pub fn move_relative(&self, x: i32, y: i32) -> Result<()> { self.other(format!("relative:{x}:{y}")) }
        pub fn press_button(&self, button: &MouseButton) -> Result<()> { self.other(format!("press:{button:?}")) }
        pub fn release_button(&self, button: &MouseButton) -> Result<()> { self.other(format!("release:{button:?}")) }
        pub fn click_button(&self, button: &MouseButton) -> Result<()> { self.other(format!("click:{button:?}")) }
        fn other(&self, text: String) -> Result<()> { crate::STATE.lock().unwrap().others.push(text); Ok(()) }
'''
TESTS = r'''
#[cfg(test)]
mod tests {
    use super::*;
    fn reset() { *STATE.lock().unwrap() = State::default(); }
    fn call(data: DataMouse) { handle_mouse(&mut mouce::UInputMouseManager::default(), &data); }
    fn packet(code: u16, value: i32) -> Vec<(u16,u16,i32)> { vec![(2,code,value),(0,0,0)] }
    fn horizontal(delta:i32) -> Vec<(u16,u16,i32)> { if delta==0 {vec![]} else {packet(6,delta)} }
    fn vertical(delta:i32) -> Vec<(u16,u16,i32)> {
        if delta==0 {vec![]} else if delta==i32::MIN {
            let mut events=packet(8,i32::MAX);events.extend(packet(8,1));events
        } else { packet(8,-delta) }
    }
    #[test]
    fn horizontal_positive_negative_zero() {
        for delta in [1,3,-1,-4,0,i32::MIN,i32::MAX] {
            reset(); call(DataMouse::ScrollX(delta)); let state=STATE.lock().unwrap();
            assert_eq!(state.packets,horizontal(delta));
            assert!(state.packets.len()<=2);assert_eq!(state.sleeps,vec![1;usize::from(delta!=0)]); assert_eq!(state.errors,0);
            assert_eq!(state.packets.iter().filter(|e| e.0==2).map(|e| i64::from(e.2)).sum::<i64>(),i64::from(delta));
        }
    }
    #[test]
    fn vertical_positive_negative_zero_and_extremes_finish() {
        for delta in [1,3,-1,-4,0,i32::MIN,i32::MAX] {
            reset(); call(DataMouse::ScrollY(delta)); let state=STATE.lock().unwrap();
            assert_eq!(state.packets,vertical(delta));
            assert!(state.packets.len()<=4);assert_eq!(state.sleeps,vec![1;state.packets.len()/2]); assert_eq!(state.errors,0);
            assert_eq!(state.packets.iter().filter(|e| e.0==2).map(|e| i64::from(e.2)).sum::<i64>(),-i64::from(delta));
        }
    }
    #[test]
    fn signed_totals_are_equivalent_to_original_detent_contract() {
        for delta in -100..=100 {
            reset();call(DataMouse::ScrollX(delta));
            assert_eq!(STATE.lock().unwrap().packets.iter().filter(|e| e.0==2).map(|e|i64::from(e.2)).sum::<i64>(),i64::from(delta));
            reset();call(DataMouse::ScrollY(delta));
            assert_eq!(STATE.lock().unwrap().packets.iter().filter(|e| e.0==2).map(|e|i64::from(e.2)).sum::<i64>(),-i64::from(delta));
        }
    }
    #[test]
    fn mixed_axis_sequence_preserves_signed_detent_totals() {
        reset();call(DataMouse::ScrollX(2));call(DataMouse::ScrollY(-3));call(DataMouse::ScrollX(-1));
        let mut events=packet(6,2);events.extend(packet(8,3));events.extend(packet(6,-1));
        let state=STATE.lock().unwrap();assert_eq!(state.packets,events);assert_eq!(state.sleeps,vec![1;3]);
    }
    #[test]
    fn native_rel_and_syn_errors_stop_only_current_request() {
        for (x,delta) in [(true,3),(true,i32::MIN),(false,3),(false,i32::MIN)] {
            let events=if x {horizontal(delta)} else {vertical(delta)};
            for failed_at in 1..=events.len() {
                for result in [-1,0,23,25] {
                    reset();{let mut state=STATE.lock().unwrap();state.fail_at=failed_at;state.fail_result=result;}
                    call(if x {DataMouse::ScrollX(delta)}else{DataMouse::ScrollY(delta)});
                    let state=STATE.lock().unwrap();assert_eq!(state.errors,1);assert_eq!(state.packets,events[..failed_at]);
                    assert_eq!(state.sleeps,vec![1;(failed_at-1)/2]);
                    drop(state);call(DataMouse::ScrollX(1));
                    let state=STATE.lock().unwrap();assert_eq!(state.errors,1);assert_eq!(state.sleeps.len(),(failed_at-1)/2+1);
                }
            }
        }
    }
    #[test]
    fn representative_all_i32_regions_bound_writes() {
        let mut value=0x9182_ace1u32;
        for _ in 0..4096 {
            value=value.wrapping_mul(1664525).wrapping_add(1013904223);let delta=value as i32;
            for x in [true,false] {
                reset();call(if x {DataMouse::ScrollX(delta)} else {DataMouse::ScrollY(delta)});
                let state=STATE.lock().unwrap();assert!(state.packets.len()<=if x {2}else{4});
                assert_eq!(state.packets.iter().filter(|e|e.0==2).map(|e|i64::from(e.2)).sum::<i64>(),if x {i64::from(delta)}else{-i64::from(delta)});
            }
        }
    }
    #[test]
    fn nonwheel_and_refresh_paths_are_unchanged() {
        reset();call(DataMouse::MoveTo(2,3));call(DataMouse::MoveRelative(-2,4));
        for button in [enigo::MouseButton::Left,enigo::MouseButton::Middle,enigo::MouseButton::Right] {
            call(DataMouse::Down(button));call(DataMouse::Up(button));call(DataMouse::Click(button));
        }
        for button in [enigo::MouseButton::Side] {call(DataMouse::Down(button));call(DataMouse::Up(button));call(DataMouse::Click(button));}
        call(DataMouse::Refresh);let state=STATE.lock().unwrap();
        assert_eq!(state.others,vec!["to:2:3","relative:-2:4","press:Left","release:Left","click:Left","press:Middle","release:Middle","click:Middle","press:Right","release:Right","click:Right"]);
        assert!(state.packets.is_empty());assert!(state.sleeps.is_empty());assert_eq!(state.errors,0);
    }
    #[test]
    fn literal_event_layout_and_unit_packets_are_unchanged() {
        assert_eq!(std::mem::size_of::<mouce::InputEvent>(),24);assert_eq!(std::mem::align_of::<mouce::InputEvent>(),8);
        for (direction,code,value) in [(mouce::ScrollDirection::Left,6,-1),(mouce::ScrollDirection::Right,6,1),
            (mouce::ScrollDirection::Up,8,1),(mouce::ScrollDirection::Down,8,-1)] {
            reset();mouce::UInputMouseManager::default().scroll_wheel(&direction).unwrap();
            assert_eq!(STATE.lock().unwrap().packets,packet(code,value));
        }
    }
}
'''


BASELINE_TESTS = r'''
#[cfg(test)]
mod tests {
    use super::*;
    fn reset(){*STATE.lock().unwrap()=State::default();}
    fn call(data:DataMouse){handle_mouse(&mut mouce::UInputMouseManager::default(),&data);}
    #[test]
    fn horizontal_positive_negative_zero(){
        reset();call(DataMouse::ScrollX(3));assert_eq!(STATE.lock().unwrap().packets,vec![(2,6,3),(0,0,0)]);
    }
    #[test]
    fn original_vertical_unit_packets_and_totals(){
        for delta in -100i32..=100 {
            reset();call(DataMouse::ScrollY(delta));let state=STATE.lock().unwrap();
            let sign=if delta<0 {1}else{-1};
            let events:Vec<_>=(0..delta.unsigned_abs()).flat_map(|_|[(2,8,sign),(0,0,0)]).collect();
            assert_eq!(state.packets,events);assert_eq!(state.sleeps,vec![1;delta.unsigned_abs() as usize]);
            assert_eq!(state.packets.iter().filter(|e|e.0==2).map(|e|i64::from(e.2)).sum::<i64>(),-i64::from(delta));
        }
    }
    #[test]
    fn original_vertical_min_gap_recorded(){
        reset();let result=std::panic::catch_unwind(||call(DataMouse::ScrollY(i32::MIN)));
        assert_eq!(result.is_err(),cfg!(debug_assertions));assert!(STATE.lock().unwrap().packets.is_empty());
    }
    #[test]
    fn vertical_min_requires_bounded_exact_total(){
        reset();call(DataMouse::ScrollY(i32::MIN));
        assert_eq!(STATE.lock().unwrap().packets,vec![(2,8,i32::MAX),(0,0,0),(2,8,1),(0,0,0)]);
    }
}
'''


def harness(source, ipc, macro):
    handler = block(source, "fn handle_mouse(")
    types = "#[repr(C)]\n" + block(source, "pub struct InputEvent") + "\n#[repr(C)]\n" + block(source, "pub struct TimeVal")
    constants = "\n".join(line.strip() for line in source.splitlines()
        if any(line.strip().startswith(name) for name in ("pub const EV_REL:", "pub const REL_WHEEL:",
            "pub const REL_HWHEEL:", "const SYN_REPORT:", "const EV_SYN:")))
    assert len(constants.splitlines()) == 5
    mouse_types = block(source, "pub enum MouseButton") + "\n" + block(source, "pub enum ScrollDirection")
    mouse_types = mouse_types.replace("pub enum", "#[derive(Debug)]\npub enum")
    event_data = block(ipc, "pub enum DataMouse")
    result = PREFIX + event_data + "\n" + macro + "\npub mod mouce {\n" + r'''
    use std::io::{Error, ErrorKind, Result};
    use std::os::fd::AsRawFd;
    use std::os::raw::{c_int,c_long,c_uint,c_ulong,c_ushort};
    use std::mem::size_of;
    use std::time::Duration;
''' + constants + "\n" + types + "\n" + mouse_types + MOUSE_STUBS
    result += "\n".join(block(source, marker) for marker in ("fn emit(&self", "fn syncronize(&self", "pub fn scroll_wheel(&self"))
    # The original harness must compile unchanged, so only the fixed source adds its new helper.
    if "pub fn scroll_wheel_horizontal(&self" in source:
        result += "\n" + block(source, "pub fn scroll_wheel_horizontal(&self")
        result += "\n" + block(source, "pub fn scroll_wheel_vertical(&self")
    return result + "\n}\n}\n" + handler + "\n" + TESTS


def compiled(out, source, upstream, branded, macro, rustc, sysroot):
    version = run([rustc, "--version"], out, "rust-version").stdout
    assert version.startswith("rustc 1.75.0 "), version
    ipc = (out / "pinned-source/src/ipc.rs").read_text(encoding="utf-8")
    original_harness = harness(upstream.decode(), ipc, macro).replace(TESTS,BASELINE_TESTS,1)
    corrected_harness = harness(P.transform(branded).decode(), ipc, macro)
    old = out / "original-wheel-control.rs"; old.write_text(original_harness, encoding="utf-8")
    new = out / "corrected-wheel-control.rs"; new.write_text(corrected_harness, encoding="utf-8")
    for label, path in (("original",old),("corrected",new)):
        binary = out / (label + "-wheel-control")
        run([rustc,"--edition=2021","-Dwarnings","--test",str(path),"-o",str(binary)],out,label+"-compile")
        if label == "original":
            result=run([str(binary),"--exact","tests::horizontal_positive_negative_zero","--test-threads=1"],out,"original-required-horizontal-failure",101)
            assert "left: []" in result.stdout, result.stdout + result.stderr
            run([str(binary),"--exact","tests::vertical_min_requires_bounded_exact_total","--test-threads=1"],out,"original-required-vertical-count-failure",101)
            run([str(binary),"--exact","tests::original_vertical_unit_packets_and_totals","--test-threads=1"],out,"original-vertical-unit-equivalence")
            run([str(binary),"--exact","tests::original_vertical_min_gap_recorded","--test-threads=1"],out,"original-vertical-debug-gap")
        else:
            run([str(binary),"--test-threads=1"],out,"corrected-eight-controls")
    negative_variants = {
        "wrong-horizontal-sign": corrected_harness.replace("REL_HWHEEL as c_int, detents", "REL_HWHEEL as c_int, -detents", 1),
        "lost-detent-count": corrected_harness.replace("REL_HWHEEL as c_int, detents", "REL_HWHEEL as c_int, detents.signum()", 1),
        "zero-emitted": corrected_harness.replace("0 => Ok(()),", "42 => Ok(()),", 1),
        "wrong-vertical-sign": corrected_harness.replace("REL_WHEEL as c_int, -detents", "REL_WHEEL as c_int, detents", 1),
        "lost-min-tail": corrected_harness.replace("self.emit(EV_REL, REL_WHEEL as c_int, 1)?", "self.emit(EV_REL, REL_WHEEL as c_int, 0)?", 1),
        "wrong-horizontal-axis": corrected_harness.replace("REL_HWHEEL as c_int, detents", "REL_WHEEL as c_int, detents", 1),
    }
    for label, candidate in negative_variants.items():
        assert candidate != corrected_harness
        path=out/(label+".rs"); path.write_text(candidate,encoding="utf-8")
        binary=out/label
        run([rustc,"--edition=2021","-Dwarnings","--test",str(path),"-o",str(binary)],out,label+"-compile")
        run([str(binary),"--test-threads=1"],out,label+"-required-failure",101)
    for label,path in (("original",old),("corrected",new)):
        release=out/(label+"-release-control")
        run([rustc,"--edition=2021","-Dwarnings","--test","-C","opt-level=3",str(path),"-o",str(release)],out,label+"-release-compile")
        if label=="corrected":
            run([str(release),"--test-threads=1"],out,"corrected-release-eight-controls")
        else:
            run([str(release),"--exact","tests::original_vertical_min_gap_recorded","--test-threads=1"],out,"original-vertical-release-gap")
            run([str(release),"--exact","tests::vertical_min_requires_bounded_exact_total","--test-threads=1"],out,"original-required-release-vertical-count-failure",101)
        run([rustc,"--edition=2021","-Dwarnings","--test","--emit=metadata","--target","x86_64-unknown-linux-gnu",
            "--sysroot",str(sysroot),str(path),"-o",str(out/(label+"-linux.rmeta"))],out,label+"-linux-metadata")
    corrected=P.transform(branded)
    assert P.transform(corrected,True)==branded
    # Exact branch reversal reconstructs the entire original, not just nonwheel snippets.
    assert corrected.replace(P.NEW.encode(),P.OLD.encode(),1).replace(P.NEW_VERTICAL.encode(),P.OLD_VERTICAL.encode(),1).replace(P.HORIZONTAL.encode(),b"",1).replace(P.VERTICAL.encode(),b"",1)==branded
    for marker in ("fn emit(&self", "fn syncronize(&self", "pub fn scroll_wheel(&self",
                   "pub fn new(rng_x:", "impl Drop for UInputMouseManager"):
        assert block(corrected.decode(),marker)==block(branded.decode(),marker),marker
    (out/"conservation.json").write_text(json.dumps({"original":sha(upstream),"branded":sha(branded),
        "corrected":sha(corrected),"whole_file_two_handlers_and_two_helpers_reversal":True,
        "device_creation_rights_ioctl_write_drop_unit_emitter_unchanged":True,
        "host_execution":"literal Rust handler/emitter with native write and delay mocks",
        "original_vertical_equivalence":"actual literal baseline +/-100 unit packets and signed total; new counts preserve signed total and +/-1 packets; multi-detent grouping intentionally aggregated",
        "old_vertical_MIN":"original debug overflow and release empty range reproduced; corrected fully emits MAX+1 in two packets",
        "linux_target":"actual Rust1.75 metadata only",
        "native_uinput_wayland_execution":False,
        "inherited_vertical_MIN":"pre-existing debug overflow and release empty range fixed at uinput handler; complete compiled MIN/MAX controls pass in both profiles; native device execution unrun","i32_MIN_MAX_complete_call_execution_both_axes":True,
        "bounded_wheel_work":"zero emits none; X at most2 writes; Y at most4 writes for MIN (MAX+1) and at most2 otherwise; exact signed totals",
        "public_contract":"https://docs.kernel.org/input/event-codes.html#ev-rel"},indent=2)+"\n",encoding="utf-8")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--evidence-dir",type=Path,required=True)
    parser.add_argument("--linux-sysroot",type=Path,required=True)
    args=parser.parse_args()
    out=args.evidence_dir.absolute(); out.mkdir(parents=True,exist_ok=False)
    upstream,branded,macro=originals(args.source.absolute(),out)
    counts=guards(out,(upstream,branded))
    compiled(out,args.source,upstream,branded,macro,os.environ.get("RUSTC_BIN","rustc"),args.linux_sysroot.absolute())
    (out/"guard-results.json").write_text(json.dumps(counts,indent=2)+"\n",encoding="utf-8")
    print(f"PASS uinput wheel source: 8 compiled controls in debug/release; 6 bad variants fail101; original horizontal failure101; original vertical required-failure101; Linux metadata original/corrected -Dwarnings; {counts['roundtrips']} LF/CRLF roundtrips; {counts['refusals']} zero-write refusals; native Wayland unrun")


if __name__=="__main__":
    main()
