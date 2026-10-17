#!/usr/bin/env python3
"""Run literal first-use selection, strict source guards and branding composition."""
import argparse
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PIN = "1abc897c451c8b5bbff3792509a7fef9d12f2ce3"
SOURCE_PINS = {
    "src/client.rs": "7ae3387a825a721c1f8a6fbe236c1810b2ff6e4aca7c09083f9e7f68c7283c47",
    "src/common.rs": "1c133828b7fac67c90e9f1293a58f73204a78d2fd25425f2758a1ae217575ac2",
    "src/keyboard.rs": "c87345ce10578f750340b0c69300e2acba46320d2c5869e5d473774f18a54eef",
    "src/platform/linux.rs": "d4ac15c351fe3f7cac3058177910a024e0bc93e3e1b9d640a0a4a273d119764c",
}
LIBRARY_PINS = {
    "src/keyboard.rs": "cb8a18861cb83d91b4f97af0a0d4fbbfab198ca5b2896227bc917f80d1becca9",
    "src/lib.rs": "71fe3406431ff01d8b2c2282028da99936d321787e2147db6fa999663c9f4b49",
    "protos/message.proto": "1f5bd3293fe78f60b078589d724e1428eef0415494a44032557c8e22e8bdbc72",
}


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


P = module(ROOT / "scripts/patch-support-keyboard-default.py", "first_use_patcher")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def block(source, marker):
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 1
    for end in range(opening + 1, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[start:end + 1]
    raise RuntimeError("Incomplete pinned Rust block: " + marker)


def run(arguments, out, label, *, success=True, **kwargs):
    result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", timeout=240,
                            env=dict(os.environ, PYTHONIOENCODING="utf-8"), **kwargs)
    (out / (label + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
    if success:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def source_files(source, out):
    library = source / "libs/hbb_common"
    library_pin = subprocess.check_output(["git", "-C", str(source), "ls-tree", PIN,
                                          "libs/hbb_common"], timeout=60).decode().split()[2]
    result = {}
    for prefix, repo, revision, pins in (("", source, PIN, SOURCE_PINS),
                                        ("libs/hbb_common/", library, library_pin, LIBRARY_PINS)):
        for name, expected in pins.items():
            data = subprocess.check_output(["git", "-C", str(repo), "show", revision + ":" + name], timeout=60)
            if expected:
                assert sha(data) == expected, name
            result[prefix + name] = data.decode("utf-8")
            target = out / "source-bindings" / (prefix + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    (out / "source-bindings.json").write_text(json.dumps({
        "upstream": PIN, "hbb_common": library_pin,
        "files": {name: sha(value.encode()) for name, value in result.items()},
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


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
            elif path.is_dir():
                result[key] = ("directory",)
            else:
                result[key] = ("other", record.st_mode)
    return result


def materialization(out, originals):
    counts = {"roundtrips": 0, "refusals": 0}
    for original in originals:
        assert sha(original) in P.CANONICAL_CLIENT_HASHES
        for newline in (b"\n", b"\r\n"):
            data = original.replace(b"\n", newline)
            corrected = P.transform(data)
            assert corrected == data.replace(P.OLD.replace("\n", newline.decode()).encode(),
                                             P.NEW.replace("\n", newline.decode()).encode(), 1)
            assert P.transform(corrected) == corrected
            assert P.transform(corrected, True) == data
            assert P.transform(data, True) == data
            counts["roundtrips"] += 1
            for malformed in (data + b"// foreign drift" + newline,
                              data.replace(b"config.keyboard_mode.is_empty()", b"config.keyboard_mode.len() == 0", 1),
                              corrected.replace(b"first_use_translate", b"first_use_translation", 1),
                              corrected + P.OLD.replace("\n", newline.decode()).encode(),
                              data[:-1], data.replace(newline, b"\r", 1),
                              data.replace(newline, b"\r\n" if newline == b"\n" else b"\n", 1)):
                for restore in (False, True):
                    try:
                        P.transform(malformed, restore)
                    except (RuntimeError, UnicodeError):
                        counts["refusals"] += 1
                    else:
                        raise AssertionError("Malformed keyboard default source accepted")
    # Every refusal is tested on the real materializer, with the entire owned
    # fixture snapshot stable, including an external symlink target.
    for mode in ("drift", "missing", "symlink-file", "symlink-src", "symlink-root",
                 "symlink-ancestor", "hardlink", "fifo", "partial"):
        with tempfile.TemporaryDirectory(prefix="mixel-keyboard-default-") as temporary:
            container = Path(temporary).resolve()
            repo = container / "checkout"
            target = repo / "src/client.rs"
            target.parent.mkdir(parents=True)
            target.write_bytes(originals[0])
            (repo / "unrelated").write_bytes(b"preserve unrelated bytes\n")
            external = container / "external"
            external.mkdir()
            (external / "client.rs").write_bytes(originals[0])
            called_repo = repo
            if mode == "drift":
                target.write_bytes(target.read_bytes() + b"// drift\n")
            elif mode == "missing":
                target.unlink()
            elif mode == "symlink-file":
                target.unlink()
                target.symlink_to(external / "client.rs")
            elif mode == "symlink-src":
                target.unlink()
                target.parent.rmdir()
                target.parent.symlink_to(external, target_is_directory=True)
            elif mode == "symlink-root":
                alias = container / "alias"
                alias.symlink_to(repo, target_is_directory=True)
                called_repo = alias
            elif mode == "symlink-ancestor":
                alias = container / "alias"
                alias.symlink_to(container, target_is_directory=True)
                called_repo = alias / "checkout"
            elif mode == "hardlink":
                os.link(target, repo / "linked")
            elif mode == "fifo":
                target.unlink()
                os.mkfifo(target)
            elif mode == "partial":
                target.write_bytes(P.transform(target.read_bytes()).replace(b"first_use_translate", b"first_use_bad", 1))
            before = snapshot(container)
            for restore, check in ((False, False), (True, False), (False, True)):
                try:
                    P.apply(called_repo, restore, check)
                except (RuntimeError, OSError, UnicodeError):
                    counts["refusals"] += 1
                else:
                    raise AssertionError("Materializer accepted " + mode)
                assert snapshot(container) == before, mode
    with tempfile.TemporaryDirectory(prefix="mixel-keyboard-default-clean-") as temporary:
        repo = Path(temporary).resolve()
        (repo / "src").mkdir()
        target = repo / "src/client.rs"
        for original in originals:
            target.write_bytes(original)
            before = snapshot(repo)
            with contextlib.redirect_stdout(io.StringIO()):
                P.apply(repo, check=True)
            assert snapshot(repo) == before
            with contextlib.redirect_stdout(io.StringIO()):
                P.apply(repo)
                P.apply(repo)
                P.apply(repo, True)
            assert snapshot(repo) == before
    (out / "materialization.json").write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")
    print("PASS: four pristine/composed LF/CRLF roundtrips; " + str(counts["refusals"]) +
          " strict source/ownership refusals preserve every fixture byte")


def composed_client(original, source_root, out):
    # Execute only the actual source-bound pure client replacements from the
    # earlier network helper. No ignored fixture or historical Git is needed.
    helper = (source_root / "scripts/patch-support-network.py").read_bytes()
    tree = ast.parse(helper)
    nodes = []
    calls = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and
                target.id in ("direct_attempts", "direct_guard") for target in node.targets):
            nodes.append(node)
        elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and
              isinstance(node.value.func, ast.Name) and node.value.func.id == "patch" and
              isinstance(node.value.args[0], ast.Constant) and node.value.args[0].value == "src/client.rs"):
            nodes.append(node)
            calls.append(node)
    assert len(calls) == 13
    secure = module(source_root / "scripts/patch-secure-support.py", "first_use_secure")
    state = [secure.patch_client(original.decode("utf-8"))]

    def patch(path, before, after):
        assert path == "src/client.rs" and state[0].count(before) == 1
        assert after not in state[0]
        state[0] = state[0].replace(before, after, 1)

    exec(compile(ast.Module(body=nodes, type_ignores=[]), "literal-network-client-transforms", "exec"), {"patch": patch})
    result = state[0].encode("utf-8")
    assert sha(result) == P.COMPOSED_CLIENT_SHA
    (out / "canonical-composed-client.rs").write_bytes(result)
    (out / "composed-client-binding.json").write_text(json.dumps({
        "network_helper_sha256": sha(helper),
        "secure_helper_sha256": sha((source_root / "scripts/patch-secure-support.py").read_bytes()), "actual_client_transform_calls": len(calls),
        "canonical_client_sha256": sha(result), "side_effects": False,
    }, indent=2) + "\n", encoding="utf-8")
    return result


RUST_BOUNDARY = r'''
extern crate self as hbb_common;
pub const PLATFORM_ANDROID: &str = "Android";
pub mod protos { pub mod message {
    // Generated protobuf enum dependency stand-in; declaration is checked
    // against the exact pinned .proto below. Display/parse/iter are literal.
    #[derive(Debug, Copy, Clone, PartialEq, Eq)]
    pub enum KeyboardMode { Legacy=0, Map=1, Translate=2, Auto=3 }
    impl Default for KeyboardMode { fn default()->Self {Self::Legacy} }
} }
pub use protos::message::KeyboardMode;
use std::str::FromStr;
use std::sync::{atomic::{AtomicBool, Ordering}, Mutex};
static X11: AtomicBool = AtomicBool::new(true);
static LOCAL_INPUT: Mutex<String> = Mutex::new(String::new());
pub fn get_local_option(_name:String)->String {LOCAL_INPUT.lock().unwrap().clone()}
pub mod platform { pub mod linux {
    struct TestX11;
    impl std::ops::Deref for TestX11 {
        type Target=bool;
        fn deref(&self)->&bool { if crate::X11.load(crate::Ordering::SeqCst) { &true } else { &false } }
    }
    static IS_X11:TestX11=TestX11;
    __X11__
} }
pub mod keyboard { pub mod input_source {
    use crate::get_local_option;
    pub const CONFIG_OPTION_INPUT_SOURCE:&str="input-source";
    pub const CONFIG_INPUT_SOURCE_1:&str="Input source 1";
    pub const CONFIG_INPUT_SOURCE_2:&str="Input source 2";
    pub const CONFIG_INPUT_SOURCE_DEFAULT:&str=CONFIG_INPUT_SOURCE_1;
    __INPUT__
} }
mod actual_mode { __MODE__ }
__VERSION__
__CAPABILITY__
__MODES__
pub struct Peer {pub platform:String,pub version:String}
pub struct Config {pub keyboard_mode:String}
pub struct Session {pub saved:Vec<String>}
impl Session {
    fn save_config(&mut self,config:Config) {self.saved.push(config.keyboard_mode)}
    pub fn select(&mut self,mut config:Config,pi:&Peer) {
__BRANCH__
        // no matter if change, for update file time
        self.save_config(config);
    }
}
pub fn select(mode:&str,platform:&str,version:&str,x11:bool,input:&str)->(String,usize) {
    X11.store(x11,Ordering::SeqCst);
    *LOCAL_INPUT.lock().unwrap()=input.to_owned();
    let mut session=Session {saved:vec![]};
    session.select(Config {keyboard_mode:mode.to_owned()},&Peer {platform:platform.to_owned(),version:version.to_owned()});
    (session.saved[0].clone(),session.saved.len())
}
'''


RUST_CONTROL = r'''
fn main() {
    let eligible = cfg!(fixture_linux_flutter);
    let changed = cfg!(fixture_corrected);
    if std::env::args().nth(1).as_deref()==Some("--require-first-use-translate") {
        assert_eq!(select("","Windows","1.4.6",true,"Input source 1").0,"translate",
                   "eligible first-use must follow the qualified operator layout");
    }
    let expected=if eligible && changed {"translate"} else {"map"};
    assert_eq!(select("","Windows","1.4.6",true,"Input source 1"),(expected.to_owned(),1));
    assert_eq!(select("","Windows","1.4.6",true,""),(expected.to_owned(),1));
    assert_eq!(select("","Windows","1.2.0",true,"Input source 1").0,expected);
    for platform in ["Linux","Mac OS","Android","iOS","windows",""] {
        let old=if platform.to_lowercase()=="android" {"legacy"} else {"map"};
        assert_eq!(select("",platform,"1.4.6",true,"Input source 1").0,old);
    }
    for version in ["1.1.9","1.1.10-1","1.0.0",""] {
        assert_eq!(select("","Windows",version,true,"Input source 1").0,"legacy");
    }
    for input in ["Input source 2","foreign source"," Input source 1"] {
        assert_eq!(select("","Windows","1.4.6",true,input).0,"map");
    }
    assert_eq!(select("","Windows","1.4.6",false,"Input source 1").0,"map");
    for explicit in ["map","translate","legacy","auto"] {
        for x11 in [true,false] {
            for input in ["Input source 1","Input source 2"] {
                assert_eq!(select(explicit,"Windows","1.4.6",x11,input),(explicit.to_owned(),1));
            }
        }
    }
    assert_eq!(select("map","Android","1.4.6",true,"Input source 1").0,"legacy");
    assert_eq!(select("translate","Windows","1.1.9",true,"Input source 1").0,"legacy");
    // The existing parser retains unsupported strings if its default Legacy
    // is supported. Preserve that exact old behavior; do not repair it here.
    assert_eq!(select("foreign-mode","Windows","1.4.6",true,"Input source 1").0,"foreign-mode");
    let saved=select("","Windows","1.4.6",true,"Input source 1").0;
    assert_eq!(select(&saved,"Windows","1.4.6",true,"Input source 1").0,saved);
    println!("PASS: literal first-use source eligible={} corrected={} selected={}; explicit choices, source2/Wayland/platform/version exclusions and per-peer save/reconnect conserved",eligible,changed,expected);
}
'''


def rust_source(files, corrected, fixture_cfg=False):
    client = files["src/client.rs"]
    start = client.index("        if config.keyboard_mode.is_empty() {")
    end = client.index("        // no matter if change, for update file time", start)
    branch = client[start:end]
    assert branch.count(P.OLD) == 1
    if corrected:
        branch = branch.replace(P.OLD, P.NEW, 1)
    input_body = block(files["src/keyboard.rs"], "pub fn get_cur_session_input_source()")
    if fixture_cfg:
        # Explicit fixture-only cfg projection permits host execution of both
        # real Linux branches; metadata below compiles original production cfg.
        branch = branch.replace('all(target_os = "linux", feature = "flutter")', "fixture_linux_flutter")
        input_body = input_body.replace('#[cfg(target_os = "linux")]', "#[cfg(fixture_linux_flutter)]")
    proto = block(files["libs/hbb_common/protos/message.proto"], "enum KeyboardMode{")
    assert proto == "enum KeyboardMode{\n  Legacy = 0;\n  Map = 1;\n  Translate = 2;\n  Auto = 3;\n}"
    source = RUST_BOUNDARY
    for marker, value in {
        "__X11__": block(files["src/platform/linux.rs"], "pub fn is_x11()"),
        "__INPUT__": input_body,
        "__MODE__": files["libs/hbb_common/src/keyboard.rs"],
        "__VERSION__": block(files["libs/hbb_common/src/lib.rs"], "pub fn get_version_number("),
        "__CAPABILITY__": block(files["src/common.rs"], "pub fn is_keyboard_mode_supported("),
        "__MODES__": block(files["src/common.rs"], "pub fn get_supported_keyboard_modes("),
        "__BRANCH__": branch,
    }.items():
        assert source.count(marker) == 1
        source = source.replace(marker, value)
    return source


def rust_controls(out, files, rustc, linux_sysroot):
    version = run([rustc, "--version"], out, "rust-version").stdout
    assert version.startswith("rustc 1.75.0 "), version
    for corrected in (False, True):
        source = out / ("corrected-literal.rs" if corrected else "original-literal.rs")
        source.write_text(rust_source(files, corrected, True) + RUST_CONTROL, encoding="utf-8")
        for eligible in (False, True):
            label = ("corrected" if corrected else "original") + ("-linux-flutter" if eligible else "-other-controller")
            binary = out / label
            arguments = [rustc, "--edition=2021", "-Dwarnings", str(source), "-o", str(binary)]
            if eligible:
                arguments += ["--cfg", "fixture_linux_flutter"]
            if corrected:
                arguments += ["--cfg", "fixture_corrected"]
            run(arguments, out, label + "-compile")
            run([str(binary)], out, label + "-run")
            if eligible:
                result = run([str(binary), "--require-first-use-translate"], out,
                             label + "-first-use-acceptance", success=corrected)
                if not corrected:
                    assert result.returncode != 0 and "eligible first-use must follow" in result.stderr
    source = out / "corrected-production-cfg.rs"
    source.write_text(rust_source(files, True), encoding="utf-8")
    targets = (("linux-flutter", "x86_64-unknown-linux-gnu", True, linux_sysroot),
               ("linux-no-flutter", "x86_64-unknown-linux-gnu", False, linux_sysroot),
               ("windows-flutter", "x86_64-pc-windows-msvc", True, None),
               ("mac-flutter", "aarch64-apple-darwin", True, None))
    for label, target, flutter, sysroot in targets:
        arguments = [rustc, "--edition=2021", "-Dwarnings", "--crate-type=lib", "--emit=metadata",
                     "--target", target, str(source), "-o", str(out / (label + ".rmeta"))]
        if flutter:
            arguments += ["--cfg", 'feature="flutter"']
        if sysroot:
            arguments += ["--sysroot", str(sysroot)]
        run(arguments, out, label + "-metadata")
    print("PASS: literal original/corrected eligibility and saved-mode regression; actual Rust1.75 production cfg metadata for Linux Flutter/non-Flutter, Windows and Mac, warnings denied")


def full_branding(out, source, source_root, helper):
    # Reuse the exact current four-profile producer and all its fatal drift
    # controls. Only this private shell gets the candidate check/apply hooks.
    A = module(source_root / "scripts/test-support-flutter-analysis.py", "first_use_branding")
    assert A.ROOT == source_root
    out.mkdir()
    original_run = A.run
    early = 'python3 "$(dirname "${BASH_SOURCE[0]}")/patch-support-keyboard-default.py" --check\n'
    late = 'python3 "$(dirname "${BASH_SOURCE[0]}")/patch-support-keyboard-default.py"\n'
    original_script = (source_root / "scripts/apply-branding.sh").read_bytes()
    armed = set()
    initialized = set()

    def candidate_run(arguments, *args, **kwargs):
        if len(arguments) >= 2 and Path(str(arguments[1])).name == "apply-branding.sh":
            script = Path(arguments[1])
            if script not in armed:
                current = script.read_text(encoding="utf-8")
                marker = '# Restore exact owned compatibility and Quit overlays in reverse order before\n'
                assert current.count(marker) == 1
                if early not in current:
                    current = current.replace(marker, early + "\n" + marker, 1)
                else:
                    assert current.count(early) == 1
                marker = 'echo "✓ Branding applied — product identity is $APP_NAME only."'
                assert current.count(marker) == 1
                if late not in current:
                    current = current.replace(marker, late + "\n" + marker, 1)
                else:
                    assert current.count(late) == 1
                script.write_bytes(current.encode("utf-8"))
                shutil.copyfile(helper, script.parent / helper.name)
                armed.add(script)
            repo = Path(kwargs["env"]["RDREPO"])
            if repo not in initialized:
                target = repo / "src/client.rs"
                original = target.read_bytes()
                assert sha(original.replace(b"\r\n", b"\n")) == P.CLIENT_SHA
                newline = b"\r\n" if repo.name.startswith("pipeline-crlf") else b"\n"
                target.write_bytes(original.replace(b"\r\n", b"\n").replace(b"\n", newline))
                initialized.add(repo)
        return original_run(arguments, *args, **kwargs)

    A.run = candidate_run
    A.pipeline(out, source, "bash")
    assert (source_root / "scripts/apply-branding.sh").read_bytes() == original_script
    for repo in sorted(out.glob("pipeline-*")):
        target = repo / "src/client.rs"
        if not target.is_file():
            continue
        data = target.read_bytes()
        assert sha(P.transform(data, True).replace(b"\r\n", b"\n")) == P.COMPOSED_CLIENT_SHA
        assert P.transform(data) == data
        before = A.snapshot(repo)
        target.write_bytes(data + (b"// unrelated keyboard default drift\r\n" if b"\r\n" in data else b"// unrelated keyboard default drift\n"))
        drift = A.snapshot(repo)
        failed = A.run(["bash", str(out / "pipeline-source/scripts/apply-branding.sh")], out,
                       repo.name + "-keyboard-drift-refusal", env=dict(os.environ, RDREPO=str(repo),
                       BRANDING=str(out / "pipeline-source/branding")), success=False)
        assert failed.returncode != 0 and "Pinned keyboard first-use source changed" in failed.stderr
        assert A.snapshot(repo) == drift, "Early keyboard guard changed prior ownership layers"
        target.write_bytes(data)
        assert A.snapshot(repo) == before
    print("PASS: candidate early read-only check + late apply across all four complete LF/CRLF branding profiles, twice; exact canonical network source and all earlier drift gates conserved")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--linux-sysroot", type=Path, required=True)
    parser.add_argument("--full-branding", action="store_true")
    arguments = parser.parse_args()
    out = arguments.evidence_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = arguments.source.resolve()
    source_root = arguments.source_root.resolve()
    files = source_files(source, out)
    composed = composed_client(files["src/client.rs"].encode(), source_root, out)
    materialization(out, (files["src/client.rs"].encode(), composed))
    rust_controls(out, files, os.environ.get("RUSTC_BIN", "rustc"), arguments.linux_sysroot.resolve())
    if arguments.full_branding:
        full_branding(out / "branding", source, source_root, ROOT / "scripts/patch-support-keyboard-default.py")
    print("PASS: bounded native Linux X11/source1 first-use Windows Translate candidate; full native application build and fresh-profile live acceptance remain required")


if __name__ == "__main__":
    main()
