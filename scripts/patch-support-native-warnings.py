#!/usr/bin/env python3
"""Strict reversible warning correction for pinned pristine and branded sources.

Accept only exact whole pristine/current/final source profiles and validate
prior branded layer ownership in a read-only virtual reversal before writes;
foreign, partially transformed, mixed-newline or unowned paths are fatal before
any writes. No dependency, SDK, native permission or behavior policy is changed.
"""
import argparse
import hashlib
import contextlib
import io
import tempfile
from pathlib import Path
import stat

UPSTREAM = '1abc897c451c8b5bbff3792509a7fef9d12f2ce3'
FRESH = {'libs/libxdo-sys-stub/src/lib.rs': '33f1f8a8afd6b46e6272369010f5a825a2a0438cde912a95eee3b1bfab2bf53d', 'libs/scrap/src/wayland/pipewire.rs': '553124f1826ddc7dd95404efc6e4635b98cc63ef96c581ea9a7cf519a5c5ea15', 'src/server/wayland.rs': '411e090fb780186deb58dcf47318c9c2b6798d165c39e33b9305c0cfac0c5381', 'src/server/connection.rs': 'acfa1742dee063af8bf580474615fc33ab40fa9ff541ee34b75ece58ee54a527', 'src/flutter_ffi.rs': 'de2fab2f98c87ff9e0611ec084ca2fd3a750d1fedf4990e8e10fa05963bbbdf7', 'src/platform/linux.rs': 'd4ac15c351fe3f7cac3058177910a024e0bc93e3e1b9d640a0a4a273d119764c', 'src/hbbs_http/sync.rs': '0503ffbfcaffd75b81a79ce9ccdbcb469276f14428202e03233ab7d852e667b6', 'src/tray.rs': '0f9410ed53f142e4dca83756bd29310f8cb9b019ab74e0f453df83592d597295', 'src/privacy_mode.rs': 'fd861b002dc058243288229bb275c91662116f9ad4cbe54a9a74dcd6fb0fcf7c', 'libs/scrap/build.rs': '55b2774bfe7d068280fc6b99eeb82fa4cd77b57ce9993c8846edfb1397206786'}
CORRECTED = {'libs/libxdo-sys-stub/src/lib.rs': '2fecabe41a37d96cb2fe2e449299c91ed671f5894ba7fd7fce19e076ccbab66b', 'libs/scrap/src/wayland/pipewire.rs': '8bbab38146fdb3e6c72bcee4e6817701c15e5296182bd6a8ea8b78bf6bb3f932', 'src/server/wayland.rs': '89a8edc825e974d6464a42f54210753e39342e0425dc1589003a0482028d5be6', 'src/server/connection.rs': '68a1a27ddc8a4d996151cbd8c381a09a1f82088fd214749ee291b3523a7757e2', 'src/flutter_ffi.rs': '703063828fca7ca4c484b04190d291ed0ababf66e3994002f42a6f275173bc0d', 'src/platform/linux.rs': 'b2e25fdde07b218c71ee76f68aab38e13256d42b09686c73779d2fa6b9ba5912', 'src/hbbs_http/sync.rs': '376dc7c096fb16bc09e6f2dcb5d972f64bd58d6550458664a2e75cedc244f6b9', 'src/tray.rs': '8e7e5b375d4b3e4c4588f4dd6bc7544a7fa2eb0b9cf16c684a96169f0e7aef3d', 'src/privacy_mode.rs': 'b88237fd685617e648adf745e3111f50cb299d2bf758c6d5d4ecda301270d114', 'libs/scrap/build.rs': '2d3c917a1cd831d20a37d256ff731acf083f77846e1ef070c5b68d6f401dc1c7'}
CHANGES = {'libs/libxdo-sys-stub/src/lib.rs': [['pub type useconds_t = c_uint;', 'pub use hbb_common::libc::useconds_t;']], 'libs/scrap/src/wayland/pipewire.rs': [['serde_json, tokio, ResultType', 'serde_json, ResultType']], 'src/server/wayland.rs': [['use hbb_common::{allow_err, anyhow, platform::linux::DISTRO};', 'use hbb_common::{allow_err, platform::linux::DISTRO};']], 'src/server/connection.rs': [['#[cfg(any(target_os = "windows", target_os = "linux", target_os = "macos"))]\nuse crate::clipboard_file::*;', '#[cfg(all(\n    any(target_os = "windows", target_os = "linux", target_os = "macos"),\n    any(target_os = "windows", feature = "unix-file-copy-paste")\n))]\nuse crate::clipboard_file::*;']], 'src/flutter_ffi.rs': [['    collections::HashMap,\n    path::PathBuf,\n    sync::{', '    collections::HashMap,\n    sync::{'], ['    time::{Duration, SystemTime},', '    time::SystemTime,'], ['pub type SessionID = uuid::Uuid;', '#[cfg(any(target_os = "windows", target_os = "macos"))]\nuse std::{path::PathBuf, time::Duration};\n\npub type SessionID = uuid::Uuid;']], 'src/platform/linux.rs': [['.replace(&hbb_common::whoami::hostname(), "")', '.replace(\n                    &hbb_common::whoami::fallible::hostname()\n                        .unwrap_or_else(|_| "localhost".to_owned())\n                        .to_ascii_lowercase(),\n                    "",\n                )']], 'src/hbbs_http/sync.rs': [['                        hash = hbb_common::base64::encode(&res[..]);', '                        use hbb_common::base64::Engine;\n                        hash = hbb_common::base64::engine::general_purpose::STANDARD.encode(&res[..]);']], 'src/tray.rs': [['    let mut event_loop = EventLoopBuilder::new().build();', '    #[cfg(target_os = "macos")]\n    let mut event_loop = EventLoopBuilder::new().build();\n    #[cfg(not(target_os = "macos"))]\n    let event_loop = EventLoopBuilder::new().build();']], 'src/privacy_mode.rs': [["        #[cfg(not(windows))]\n        let mut map: HashMap<&'static str, PrivacyModeCreator> = HashMap::new();", '        #[cfg(target_os = "macos")]\n        let mut map: HashMap<&\'static str, PrivacyModeCreator> = HashMap::new();\n        #[cfg(not(any(windows, target_os = "macos")))]\n        let map: HashMap<&\'static str, PrivacyModeCreator> = HashMap::new();']], 'libs/scrap/build.rs': [['fn main() {\n    // in this crate, these are also valid configurations\n    println!("cargo:rustc-check-cfg=cfg(dxgi,quartz,x11)");', 'fn mixel_supports_check_cfg() -> bool {\n    fn supports(environment: &str, expected_label: &str) -> bool {\n        let program = std::env::var_os(environment).expect("Cargo must supply tool executable");\n        let output = std::process::Command::new(program)\n            .arg("--version")\n            .output()\n            .expect("Cannot query the Cargo-selected tool");\n        assert!(output.status.success(), "Cargo tool version query failed");\n        let version = std::str::from_utf8(&output.stdout).expect("Non-UTF8 Cargo tool version");\n        let mut fields = version.split_whitespace();\n        assert_eq!(fields.next(), Some(expected_label), "Unexpected Cargo tool version");\n        let release = fields.next().expect("Missing Cargo tool version");\n        let stable = release.split(\'-\').next().unwrap();\n        let components: Vec<&str> = stable.split(\'.\').collect();\n        assert_eq!(components.len(), 3, "Invalid Cargo tool version components");\n        let major: u32 = components[0].parse().expect("Invalid Cargo tool major version");\n        let minor: u32 = components[1].parse().expect("Invalid Cargo tool minor version");\n        let _: u32 = components[2].parse().expect("Invalid Cargo tool patch version");\n        major > 1 || (major == 1 && minor >= 80)\n    }\n    // Cargo parses the directive; the selected compiler must also support it.\n    let rustc_supports = supports("RUSTC", "rustc");\n    let cargo_supports = supports("CARGO", "cargo");\n    rustc_supports && cargo_supports\n}\n\nfn main() {\n    // Stable check-cfg support starts at Rust/Cargo1.80; target cfg behavior stays unchanged.\n    if mixel_supports_check_cfg() {\n        println!("cargo:rustc-check-cfg=cfg(dxgi,quartz,x11)");\n    }']]}

CURRENT = {'libs/libxdo-sys-stub/src/lib.rs': '33f1f8a8afd6b46e6272369010f5a825a2a0438cde912a95eee3b1bfab2bf53d', 'libs/scrap/build.rs': '55b2774bfe7d068280fc6b99eeb82fa4cd77b57ce9993c8846edfb1397206786', 'libs/scrap/src/wayland/pipewire.rs': '553124f1826ddc7dd95404efc6e4635b98cc63ef96c581ea9a7cf519a5c5ea15', 'src/flutter_ffi.rs': 'aa8e999f1e64a86b294c69033dcc7aafb5376bd35a51eac11e8d592c6e7d8484', 'src/hbbs_http/sync.rs': '0503ffbfcaffd75b81a79ce9ccdbcb469276f14428202e03233ab7d852e667b6', 'src/platform/linux.rs': 'e88091c00c4d5040adaf3e4fafd459ae0896157a59b35c9ecc124de1e1d675d6', 'src/privacy_mode.rs': 'fd861b002dc058243288229bb275c91662116f9ad4cbe54a9a74dcd6fb0fcf7c', 'src/server/connection.rs': 'bd99eeb6abf8b10a1a57bbb5eeca68498b1dffd03c3132b19dc336e6e3a5df54', 'src/server/wayland.rs': '411e090fb780186deb58dcf47318c9c2b6798d165c39e33b9305c0cfac0c5381', 'src/tray.rs': '0f9410ed53f142e4dca83756bd29310f8cb9b019ab74e0f453df83592d597295'}
FINAL = {'libs/libxdo-sys-stub/src/lib.rs': '2fecabe41a37d96cb2fe2e449299c91ed671f5894ba7fd7fce19e076ccbab66b', 'libs/scrap/build.rs': '2d3c917a1cd831d20a37d256ff731acf083f77846e1ef070c5b68d6f401dc1c7', 'libs/scrap/src/wayland/pipewire.rs': '8bbab38146fdb3e6c72bcee4e6817701c15e5296182bd6a8ea8b78bf6bb3f932', 'src/flutter_ffi.rs': '97e32fde66f3d2a283f5f4a37bae88a2539ac3ebc12fb8d4a06efcf109f068ab', 'src/hbbs_http/sync.rs': '376dc7c096fb16bc09e6f2dcb5d972f64bd58d6550458664a2e75cedc244f6b9', 'src/platform/linux.rs': 'c45151860adecdf10672f534e340ee27391c39326014d3d2d08ccc387b88724a', 'src/privacy_mode.rs': 'b88237fd685617e648adf745e3111f50cb299d2bf758c6d5d4ecda301270d114', 'src/server/connection.rs': '39950c3d3d3990bb5a7f651f6e440a462a704174959c9a6c80fd2c3152b36a6a', 'src/server/wayland.rs': '89a8edc825e974d6464a42f54210753e39342e0425dc1589003a0482028d5be6', 'src/tray.rs': '8e7e5b375d4b3e4c4588f4dd6bc7544a7fa2eb0b9cf16c684a96169f0e7aef3d'}
PRIOR_FILE_TRANSFER_SHA = 'e484d69d6135133776db616fd6d56dc0b699004e6b38bf318019ec905bff27a8'

def canonical(data: bytes) -> tuple[str, str]:
    text = data.decode("utf-8", errors="strict")
    normalized = text.replace("\r\n", "\n")
    if "\r" in normalized or ("\r\n" in text and "\n" in text.replace("\r\n", "")):
        raise RuntimeError("Mixed or unsupported native warning source newlines")
    return normalized, "\r\n" if "\r\n" in text else "\n"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def transform(relative: str, source: str, restore: bool = False) -> str:
    current = digest(source)
    pairs = ((FRESH[relative], CORRECTED[relative]),
             (CURRENT[relative], FINAL[relative]))
    for original, corrected in pairs:
        if current == (original if restore else corrected):
            return source
        if current == (corrected if restore else original):
            target = original if restore else corrected
            break
    else:
        raise RuntimeError("Pinned native warning source changed: " + relative)
    for old, new in CHANGES[relative]:
        before, after = (new, old) if restore else (old, new)
        if source.count(before) != 1:
            raise RuntimeError("Native warning literal changed: " + relative)
        source = source.replace(before, after, 1)
    if digest(source) != target:
        raise RuntimeError("Native warning corrected whole-source mismatch: " + relative)
    return source


def safe_source(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise RuntimeError("Unsafe native warning source name")
    for part in [root, *list(path.parents)[:len(Path(relative).parts)-1]]:
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Unsafe native warning source parent: " + str(part))
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1000000 or getattr(info, "st_file_attributes", 0) & 0x400:
        raise RuntimeError("Unowned or nonregular native warning source: " + relative)
    return path


def prior_namespace(name: str, expected: str) -> dict:
    folder = Path(__file__).absolute().parent
    data = safe_source(folder, name).read_bytes()
    text, _ = canonical(data)
    if digest(text) != expected:
        raise RuntimeError("Changed native warning prior verifier: " + name)
    namespace = {"__name__": "mixel_native_warning_prior", "__file__": str(folder/name)}
    exec(compile(text, namespace["__file__"], "exec"), namespace)
    return namespace


def verify_previous_layers(root: Path, texts: dict) -> None:
    """Reverse only this layer in an owned projection, then run prior guards."""
    transfer = prior_namespace("patch-support-file-transfer-window.py", PRIOR_FILE_TRANSFER_SHA)
    # Reuse the already pinned SDK loader rather than duplicating its guards.
    sdk = prior_namespace("patch-support-flutter-sdk-compatibility.py",
                          "08255c1af27ea05c5e1eaf3c8d903070d26b4a1c8c3e02eb1c24107bbcf92010")
    termination = sdk["load_prior"]("patch-support-macos-termination.py", sdk["PRIOR_SHA"])
    cleanup = sdk["load_prior"]("patch-support-macos-cleanup.py", sdk["CLEANUP_SHA"])
    optional = {transfer["GENERATED"], termination["GENERATED"], cleanup["GENERATED"]}
    names = (set(FRESH) | set(transfer["COMPOSED"]) | set(sdk["FILES"]) |
             set(termination["FILES"]) | set(cleanup["FILES"]) | optional | {"Cargo.toml"})
    with tempfile.TemporaryDirectory(prefix="mixel-native-warning-prior-") as temporary:
        stage = Path(temporary).resolve()
        for name in sorted(names):
            data = transfer["safe_path"](root, name, optional=name in optional)
            if data is None:
                continue
            if name in texts:
                _, newline = canonical(data)
                data = transform(name, texts[name], restore=True).replace("\n", newline).encode("utf-8")
            target = stage/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        for layer in (sdk["OWNED"], "flutter/.mixel-dependencies/macos_application_exit", cleanup["OWNED"]):
            owned = root/layer
            if not owned.exists() and not owned.is_symlink():
                continue
            files, directories = sdk["inventory"](root, layer)
            (stage/layer).mkdir(parents=True, exist_ok=True)
            for name in directories:
                (stage/layer/name).mkdir(parents=True, exist_ok=True)
            for name, data in files.items():
                target = stage/layer/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        with contextlib.redirect_stdout(io.StringIO()):
            transfer["verify_previous_layers"](stage)


def apply(root: Path, restore: bool = False, check: bool = False,
          before_branding: bool = False, after_branding: bool = False) -> dict:
    root = root.absolute()
    for parent in [root, *root.parents]:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Unsafe native warning source root")
    pending, texts, hashes = [], {}, {}
    for relative in FRESH:
        path = safe_source(root, relative)
        original = path.read_bytes()
        source, newline = canonical(original)
        texts[relative] = source; hashes[relative] = digest(source)
        if hashes[relative] not in (FRESH[relative], CORRECTED[relative], CURRENT[relative], FINAL[relative]):
            raise RuntimeError("Pinned native warning source changed: " + relative)
        changed = transform(relative, source, restore).replace("\n", newline).encode("utf-8")
        pending.append((path, original, changed))
    profiles = (("pristine", "original", FRESH), ("pristine", "corrected", CORRECTED),
                ("branded", "original", CURRENT), ("branded", "corrected", FINAL))
    matches = [(profile, state) for profile, state, pins in profiles if hashes == pins]
    if len(matches) != 1:
        raise RuntimeError("Partially applied or mixed native warning correction")
    profile, state = matches[0]
    if after_branding and profile != "branded":
        raise RuntimeError("Native warning late layer requires exact current branding")
    if profile == "branded" or before_branding:
        verify_previous_layers(root, texts)
    if not check:
        for path, old, new in pending:
            if old != new:
                path.write_bytes(new)
    return {"source_state": state, "source_profile": profile, "checked_files": len(pending),
            "writes": 0 if check else sum(old != new for _, old, new in pending),
            "restore": restore, "check": check}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--before-branding", action="store_true")
    parser.add_argument("--after-branding", action="store_true")
    args = parser.parse_args()
    if args.before_branding and not (args.restore and args.check):
        parser.error("--before-branding requires --restore --check")
    if args.after_branding and args.restore:
        parser.error("--after-branding cannot restore")
    print(apply(args.source, args.restore, args.check, args.before_branding, args.after_branding))


if __name__ == "__main__":
    main()
