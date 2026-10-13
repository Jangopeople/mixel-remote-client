#!/usr/bin/env python3
"""Exact compiler-only cleanup, guarded as the final composed branding layer."""
import argparse
import hashlib
from pathlib import Path
import stat
import os
import shutil
import subprocess
import sys
import tempfile

ORIGINAL = {'src/server.rs': '83392347f807fc65d82d40b09e19911793903936221d0775e4c5477d023e437a', 'src/server/audio_service.rs': 'fc457ae565a10cda97ff396979850ccc59af6f763306984e88458e2f5ef63c8f', 'src/server/rdp_input.rs': '5a62c64c1bf1f755bba61a8df52379d5efa055ed55e915560d53c012374cb7cd', 'src/platform/linux.rs': 'c45151860adecdf10672f534e340ee27391c39326014d3d2d08ccc387b88724a', 'src/server/input_service.rs': '623a248c86d115ac383b9dd419c3073ad70584850e7a21084592714be32684d0', 'src/server/connection.rs': '39950c3d3d3990bb5a7f651f6e440a462a704174959c9a6c80fd2c3152b36a6a', '.mixel-deps/clipboard-master/src/lib.rs': 'bc385cb4cbfe35b1aacc471123c49f4077c92df849d521a410233f934c101afe', 'src/hbbs_http/downloader.rs': 'f4343c56d847276ab8001b6609af9097f1cdc218c9c3fa181c68fc79a1ba5a52', 'src/hbbs_http.rs': 'e802ed59918af397f62072c693adf43db852e13356c7e308f5aa4cba477e5638', 'src/hbbs_http/http_client.rs': '049db5587db85fba3a64be44502e0644e5f86395538e9836abc42d13e2eab19b'}
CORRECTED = {'src/server.rs': '52baa48bc96fb3dff75db89a0feea79d765a1974b3695891eafd0bd52ce9e129', 'src/server/audio_service.rs': '547f59cce317a64d2e7a6b446e716f90523ee21586c782933440d7df3f771938', 'src/server/rdp_input.rs': '5bcebddb68497a6dedce0824d9c110948bea45ed8cf9a251f818803f23c08824', 'src/platform/linux.rs': 'd0bee8959f61bb2ac2d1fddcdec827fc7a5c4c6b3fd2fe590db6f8c74cda0801', 'src/server/input_service.rs': '6010ee1f04f20259db4d2772797fb43ebbda731cdc02ca3733e8ac261f94b474', 'src/server/connection.rs': 'e3360af9a4a16558762a95ad269064db80cf453d37a658c51620f8502b10e0ce', '.mixel-deps/clipboard-master/src/lib.rs': '71c82f00b19bdda4919e538764fe06365d66c98b622826893d715a6a0ba45a63', 'src/hbbs_http/downloader.rs': 'db71ea3d67f43274ea03916abc203f030497fcc6d6b5614da2c6c49302903933', 'src/hbbs_http.rs': 'e24731f84ef5b64b7d7eb311740e8b2e2edc27013ecdf036ec9c6526beb180b8', 'src/hbbs_http/http_client.rs': '505aff248f656cf6b19d0bcd07dacbd50667335ca9dcd9f7afebe00fa9ab9c5e'}
PRIOR_HASHES = {'patch-macos-startup.py': '303c9adaf752dfe1592b5ac641f25f3384a62c281bc627a130ad9f287ba47842', 'macos-startup-failure.dart': 'cfa9ba6bd0375129e1986bd02bde332774db1ec6f7388c88fbe1f0ee1db17b57', 'patch-support-macos-tray-url.py': '929b9b20788aaee6efe5bbd9ae14f85aa910fae2a8da453bddcb5e6fa0c96c06', 'patch-macos-update.py': 'c38bec4c693ac23446fec3cb06c41bf02270b3a6940a392776c1693961e552fa', 'patch-support-fatal-crash.py': 'c8d733c450ca8a67be3b91b3e5c5e51cf2da0da899e18f081086d26465592164', 'patch-support-native-warnings.py': 'acd3a5296d36ffd3e647f4481fc86cc0b91ffcd17f415e601691c869f03e0481', 'patch-support-fingerprint.py': 'a535692eb54010592f7c48689ab4f3ea5e072c532b761acdf8df91cabe016b10', 'patch-support-uinput-device.py': 'd55e5a8bfe65cd4312de239df6590a41a5d8091a6c85e77846724a4b036960c9', 'patch-support-file-transfer-window.py': '3ccd69e634ab93b51f1d9d3f19c2254cd5c9ab7a31ccee2a95fbc8075e7b8908', 'patch-support-keyboard-default.py': '58a41edd346ac6bfc73b799812fa9a66fe4e299a35e72608c408f7f851454d72', 'patch-support-macos-rdev.py': '3630901662d808366e3f7aa767326127c2c7d0f72b3765d2a71013112c322f97', 'patch-support-clipboard.py': 'ece3790684bd4806044c37cf9cf68512a569f1cf8027330bdcb50ef488c75cbf'}
FRESH = {'src/server.rs': 'd6460bc6dba17253e6e4ba7947db1dfee9285294a3e3e3bdd71a646dd160663f', 'src/server/audio_service.rs': 'fc457ae565a10cda97ff396979850ccc59af6f763306984e88458e2f5ef63c8f', 'src/server/rdp_input.rs': '5a62c64c1bf1f755bba61a8df52379d5efa055ed55e915560d53c012374cb7cd', 'src/platform/linux.rs': 'd4ac15c351fe3f7cac3058177910a024e0bc93e3e1b9d640a0a4a273d119764c', 'src/server/input_service.rs': '623a248c86d115ac383b9dd419c3073ad70584850e7a21084592714be32684d0', 'src/server/connection.rs': 'acfa1742dee063af8bf580474615fc33ab40fa9ff541ee34b75ece58ee54a527', 'src/hbbs_http/downloader.rs': 'f4343c56d847276ab8001b6609af9097f1cdc218c9c3fa181c68fc79a1ba5a52', 'src/hbbs_http.rs': 'e802ed59918af397f62072c693adf43db852e13356c7e308f5aa4cba477e5638', 'src/hbbs_http/http_client.rs': '049db5587db85fba3a64be44502e0644e5f86395538e9836abc42d13e2eab19b'}
CHANGES = {'src/server.rs': [['    let mut cfg0 = (Config::get(), Config2::get());\n    let mut synced = false;\n', '    let mut cfg0 = (Config::get(), Config2::get());\n'], ['                if !synced {\n                    if conn.send(&Data::SyncConfig(None)).await.is_ok() {\n                        if let Ok(Some(data)) = conn.next_timeout(1000).await {\n                            match data {\n                                Data::SyncConfig(Some(configs)) => {\n                                    let (config, config2) = *configs;\n                                    let _chk = crate::ipc::CheckIfRestart::new();\n                                    #[cfg(target_os = "macos")]\n                                    let _chk_pk = crate::CheckIfResendPk::new();\n                                    if !config.is_empty() {\n                                        if cfg0.0 != config {\n                                            cfg0.0 = config.clone();\n                                            Config::set(config);\n                                            log::info!("sync config from root");\n                                        }\n                                        if cfg0.1 != config2 {\n                                            cfg0.1 = config2.clone();\n                                            Config2::set(config2);\n                                            log::info!("sync config2 from root");\n                                        }\n                                    } else {\n                                        // only on macos, because this issue was only reproduced on macos\n                                        #[cfg(target_os = "macos")]\n                                        {\n                                            // root config is empty, mark for sync in watch loop\n                                            // to prevent root from generating a new config on login screen\n                                            is_root_config_empty = true;\n                                        }\n                                    }\n                                    synced = true;\n                                    // Notify startup waiter once initial sync phase finishes successfully.\n                                    if let Some(tx) = sync_done_tx.take() {\n                                        let _ = tx.send(());\n                                    }\n                                }\n                                _ => {}\n                            };\n                        };\n                    }\n                }', '                if conn.send(&Data::SyncConfig(None)).await.is_ok() {\n                    if let Ok(Some(data)) = conn.next_timeout(1000).await {\n                        match data {\n                            Data::SyncConfig(Some(configs)) => {\n                                let (config, config2) = *configs;\n                                let _chk = crate::ipc::CheckIfRestart::new();\n                                #[cfg(target_os = "macos")]\n                                let _chk_pk = crate::CheckIfResendPk::new();\n                                if !config.is_empty() {\n                                    if cfg0.0 != config {\n                                        cfg0.0 = config.clone();\n                                        Config::set(config);\n                                        log::info!("sync config from root");\n                                    }\n                                    if cfg0.1 != config2 {\n                                        cfg0.1 = config2.clone();\n                                        Config2::set(config2);\n                                        log::info!("sync config2 from root");\n                                    }\n                                } else {\n                                    // only on macos, because this issue was only reproduced on macos\n                                    #[cfg(target_os = "macos")]\n                                    {\n                                        // root config is empty, mark for sync in watch loop\n                                        // to prevent root from generating a new config on login screen\n                                        is_root_config_empty = true;\n                                    }\n                                }\n                                // Notify startup waiter once initial sync phase finishes successfully.\n                                if let Some(tx) = sync_done_tx.take() {\n                                    let _ = tx.send(());\n                                }\n                            }\n                            _ => {}\n                        };\n                    };\n                }']], 'src/server/audio_service.rs': [['        let mut buf = vec![];\n        buf = unsafe { hbb_common::mem::aligned_u8_vec(data.len(), 4) };', '        let mut buf = unsafe { hbb_common::mem::aligned_u8_vec(data.len(), 4) };']], 'src/server/rdp_input.rs': [['        resolution: (usize, usize),\n        scale: Option<f64>,', '        scale: Option<f64>,'], ['                stream,\n                resolution,\n                scale,', '                stream,\n                scale,']], 'src/platform/linux.rs': [['fn is_opensuse() -> bool {\n    if let Ok(res) = run_cmds("cat /etc/os-release | grep opensuse") {\n        if !res.is_empty() {\n            return true;\n        }\n    }\n    false\n}\n\npub fn run_as_user', '\npub fn run_as_user'], ['/*\npub fn elevate(args: Vec<&str>)', '/*\nfn is_opensuse() -> bool {\n    if let Ok(res) = run_cmds("cat /etc/os-release | grep opensuse") {\n        if !res.is_empty() {\n            return true;\n        }\n    }\n    false\n}\npub fn elevate(args: Vec<&str>)']], 'src/server/input_service.rs': [['static EXITING: AtomicBool = AtomicBool::new(false);\n\nconst MOUSE_MOVE_PROTECTION_TIMEOUT: Duration = Duration::from_millis(1_000);\n// Actual diff of (x,y) is (1,1) here. But 5 may be tolerant.\nconst MOUSE_ACTIVE_DISTANCE: i32 = 5;\n', 'static EXITING: AtomicBool = AtomicBool::new(false);\n'], ['    // out of time protection\n', '    const MOUSE_MOVE_PROTECTION_TIMEOUT: Duration = Duration::from_millis(1_000);\n    // Actual diff of (x,y) is (1,1) here. But 5 may be tolerant.\n    const MOUSE_ACTIVE_DISTANCE: i32 = 5;\n\n    // out of time protection\n']], 'src/server/connection.rs': [['    #[cfg(any(target_os = "windows", target_os = "linux", target_os = "macos"))]\n    enable_file_transfer: bool,', '    #[cfg(all(\n        any(target_os = "windows", target_os = "linux", target_os = "macos"),\n        any(target_os = "windows", feature = "unix-file-copy-paste")\n    ))]\n    enable_file_transfer: bool,'], ['            #[cfg(any(target_os = "windows", target_os = "linux", target_os = "macos"))]\n            enable_file_transfer: false,', '            #[cfg(all(\n                any(target_os = "windows", target_os = "linux", target_os = "macos"),\n                any(target_os = "windows", feature = "unix-file-copy-paste")\n            ))]\n            enable_file_transfer: false,']], '.mixel-deps/clipboard-master/src/lib.rs': [['#![cfg_attr(feature = "cargo-clippy", allow(clippy::style))]\n#![cfg_attr(rustfmt, rustfmt_skip)]', '#![cfg_attr(rustfmt, rustfmt_skip)]']], 'src/hbbs_http/downloader.rs': [['use super::create_http_client_async_with_url;\nuse hbb_common::{\n    bail,\n    lazy_static::lazy_static,\n    log,\n    tokio::{\n        self,\n        fs::File,\n        io::AsyncWriteExt,\n        sync::mpsc::{unbounded_channel, UnboundedReceiver, UnboundedSender},\n    },\n    ResultType,\n};\nuse serde_derive::Serialize;\nuse std::{collections::HashMap, path::PathBuf, sync::Mutex, time::Duration};', '#[cfg(any(target_os = "windows", target_os = "macos"))]\nuse super::create_http_client_async_with_url;\nuse hbb_common::{\n    bail, lazy_static::lazy_static, tokio::sync::mpsc::UnboundedSender, ResultType,\n};\n#[cfg(any(target_os = "windows", target_os = "macos"))]\nuse hbb_common::{\n    log,\n    tokio::{self, fs::File, io::AsyncWriteExt, sync::mpsc::{unbounded_channel, UnboundedReceiver}},\n};\nuse serde_derive::Serialize;\nuse std::{collections::HashMap, path::PathBuf, sync::Mutex};\n#[cfg(any(target_os = "windows", target_os = "macos"))]\nuse std::time::Duration;'], ['    finished: bool,', '    #[cfg(any(target_os = "windows", target_os = "macos"))]\n    finished: bool,'], ['pub fn download_file(', '#[cfg(any(target_os = "windows", target_os = "macos"))]\npub fn download_file('], ['#[tokio::main(flavor = "current_thread")]\nasync fn do_download(', '#[cfg(any(target_os = "windows", target_os = "macos"))]\n#[tokio::main(flavor = "current_thread")]\nasync fn do_download(']], 'src/hbbs_http.rs': [['pub use http_client::{\n    create_http_client_async, create_http_client_async_with_url, create_http_client_with_url,\n    get_url_for_tls,\n};', 'pub use http_client::{create_http_client_async, create_http_client_with_url, get_url_for_tls};\n#[cfg(any(target_os = "windows", target_os = "macos"))]\npub use http_client::create_http_client_async_with_url;']], 'src/hbbs_http/http_client.rs': [['use hbb_common::{\n    async_recursion::async_recursion,', '#[cfg(any(target_os = "windows", target_os = "macos"))]\nuse hbb_common::async_recursion::async_recursion;\nuse hbb_common::{'], ['pub async fn create_http_client_async_with_url(', '#[cfg(any(target_os = "windows", target_os = "macos"))]\npub async fn create_http_client_async_with_url('], ['#[async_recursion]\nasync fn create_http_client_async_with_url_(', '#[cfg(any(target_os = "windows", target_os = "macos"))]\n#[async_recursion]\nasync fn create_http_client_async_with_url_(']]}

def canonical(data):
    text = data.decode('utf-8', errors='strict')
    normalized = text.replace('\r\n', '\n')
    if '\r' in normalized or ('\r\n' in text and '\n' in text.replace('\r\n', '')):
        raise RuntimeError('Mixed or unsupported compiler cleanup newlines')
    return normalized, '\r\n' if '\r\n' in text else '\n'


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def safe_source(root, relative):
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise RuntimeError('Unsafe compiler cleanup source name')
    root = Path(root).absolute()
    path = root / relative
    for parent in (root, *root.parents, *path.parents):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise RuntimeError('Unsafe compiler cleanup source parent')
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1000000
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise RuntimeError('Unowned or nonregular compiler cleanup source: ' + relative)
    return path


def transform(relative, source, restore=False):
    before = CORRECTED[relative] if restore else ORIGINAL[relative]
    after = ORIGINAL[relative] if restore else CORRECTED[relative]
    current = digest(source)
    if current == after:
        return source
    if current != before:
        raise RuntimeError('Pinned compiler cleanup source changed: ' + relative)
    pairs = list(reversed(CHANGES[relative])) if restore else CHANGES[relative]
    for old, new in pairs:
        old, new = (new, old) if restore else (old, new)
        if source.count(old) != 1:
            raise RuntimeError('Compiler cleanup literal changed: ' + relative)
        source = source.replace(old, new, 1)
    if digest(source) != after:
        raise RuntimeError('Compiler cleanup whole-source mismatch: ' + relative)
    return source



def prior_namespace(name):
    folder = Path(__file__).absolute().parent
    text, _ = canonical(safe_source(folder, name).read_bytes())
    if digest(text) != PRIOR_HASHES[name]:
        raise RuntimeError("Changed compiler cleanup prior verifier: " + name)
    namespace = {"__name__": "mixel_compiler_prior", "__file__": str(folder/name)}
    exec(compile(text, str(folder/name), "exec"), namespace)
    return namespace


def projection_ignored(root, directory, names):
    relative = Path(directory).relative_to(root).as_posix()
    ignored = set()
    # Only known application build caches are outside source ownership.
    # Never omit build/target/cache names inside a prior owned sidecar or
    # dependency: its original complete inventory must still reject them.
    if relative == '.':
        ignored.update(name for name in names if name in ('build', 'target', '.dart_tool', '__pycache__'))
    elif relative == 'flutter':
        ignored.update(name for name in names if name in ('build', '.dart_tool'))
    if relative in ('flutter/macos/Flutter', 'flutter/windows/flutter', 'flutter/linux/flutter'):
        ignored.update(name for name in names if name == 'ephemeral')
    return ignored


def validate_projection_sources(root):
    """Never hide unsafe source ownership by copying it to a regular file."""
    for directory, children, files in os.walk(root, followlinks=False):
        ignored = projection_ignored(root, directory, children) | {'.git'}
        children[:] = [name for name in children if name not in ignored]
        for name in children + [n for n in files if n != '.git']:
            path = Path(directory)/name
            info = path.lstat()
            if getattr(info, 'st_file_attributes', 0) & 0x400 or stat.S_ISLNK(info.st_mode):
                raise RuntimeError("Unsafe compiler prior source ownership: " + str(path.relative_to(root)))
            if stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise RuntimeError("Hardlinked compiler prior source: " + str(path.relative_to(root)))
            elif not stat.S_ISDIR(info.st_mode):
                raise RuntimeError("Nonregular compiler prior source: " + str(path.relative_to(root)))


def inventory(root):
    texts, pending, hashes = {}, [], {}
    for name in ORIGINAL:
        if name.startswith('.mixel-deps/') and not (root/name).exists() and not (root/name).is_symlink():
            continue
        path = safe_source(root, name)
        text, newline = canonical(path.read_bytes())
        texts[name] = text; hashes[name] = digest(text)
        pending.append((path, text, newline))
    profiles = [('pristine', 'original', FRESH), ('branded', 'original', ORIGINAL), ('branded', 'corrected', CORRECTED)]
    matches = [(profile, state) for profile, state, pins in profiles if hashes == pins]
    if len(matches) != 1:
        raise RuntimeError("Pinned or partially transformed compiler cleanup source profile changed")
    return texts, pending, matches[0]


def verify_previous_layers(root, texts):
    """Reverse this final layer only in a disposable exact prior-chain projection."""
    for name in PRIOR_HASHES:
        text, _ = canonical(safe_source(Path(__file__).absolute().parent, name).read_bytes())
        if digest(text) != PRIOR_HASHES[name]:
            raise RuntimeError("Changed compiler cleanup prior verifier: " + name)
    validate_projection_sources(root)
    with tempfile.TemporaryDirectory(prefix='mixel-compiler-prior-') as temporary:
        stage = Path(temporary).resolve()/'source'
        shutil.copytree(root, stage, symlinks=True,
                        ignore=lambda directory, names: projection_ignored(root, directory, names))
        for name, text in texts.items():
            if name not in ORIGINAL: continue
            if digest(text) == CORRECTED[name]:
                _, newline = canonical((root/name).read_bytes())
                (stage/name).write_bytes(transform(name, text, True).replace('\n', newline).encode('utf-8'))
        # Startup's private inverse includes the exact tray inverse and all
        # prior fatal/W/FP/U/F/keyboard/Rdev ownership guards.
        startup = prior_namespace('patch-macos-startup.py')
        try:
            startup['check_prior_overlays'](stage)
            startup['apply'](stage, restore=True)
            prior_namespace('patch-support-macos-tray-url.py')['apply'](stage, restore=True)
        except (RuntimeError, SystemExit) as error:
            raise RuntimeError('Compiler prior final startup/tray owner refused: ' + str(error)) from error
        operations = [
            ('patch-macos-update.py', ['--source', str(stage), '--restore', '--check']),
            ('patch-support-fatal-crash.py', ['--source', str(stage), '--restore', '--check']),
            ('patch-support-native-warnings.py', ['--source', str(stage), '--restore', '--check', '--before-branding']),
            ('patch-support-fingerprint.py', ['--restore', '--check', str(stage)]),
            ('patch-support-uinput-device.py', ['--source', str(stage), '--restore', '--check']),
            ('patch-support-file-transfer-window.py', ['--restore', '--check', '--before-branding', str(stage)]),
            ('patch-support-keyboard-default.py', ['--check']),
            ('patch-support-macos-rdev.py', ['--restore', '--check', str(stage)]),
        ]
        environment = dict(os.environ, RDREPO=str(stage), PYTHONIOENCODING='utf-8')
        for name, arguments in operations:
            result = subprocess.run([sys.executable, str(Path(__file__).absolute().parent/name), *arguments],
                                    env=environment, capture_output=True, text=True, encoding='utf-8', timeout=90)
            if result.returncode:
                raise RuntimeError("Compiler prior owner refused: " + name + "\n" + result.stderr[-4000:])
        try:
            prior_namespace('patch-support-clipboard.py')['check_existing'](stage)
        except RuntimeError as error:
            raise RuntimeError('Compiler prior clipboard owner refused: ' + str(error)) from error


def apply(root, check=False, restore=False, before_branding=False, after_branding=False):
    root = Path(root).absolute()
    for parent in (root, *root.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError("Unsafe compiler cleanup source root")
    texts, pending, (profile, state) = inventory(root)
    if profile == 'pristine' and not restore:
        raise RuntimeError("Compiler cleanup application requires exact complete branding")
    if after_branding and profile != 'branded':
        raise RuntimeError("Compiler cleanup late layer requires exact complete branding")
    planned = []
    for path, text, newline in pending:
        changed = text if profile == 'pristine' else transform(path.relative_to(root).as_posix(), text, restore)
        planned.append((path, changed.replace('\n', newline).encode('utf-8')))
    if before_branding or after_branding:
        verify_previous_layers(root, texts)
    writes = 0
    for path, data in planned:
        if not check and path.read_bytes() != data:
            path.write_bytes(data); writes += 1
    return {'profile': profile, 'state': state, 'writes': writes, 'checked_files': len(pending)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--before-branding', action='store_true')
    parser.add_argument('--after-branding', action='store_true')
    args = parser.parse_args()
    if args.before_branding and not (args.restore and args.check):
        parser.error('--before-branding requires --restore --check')
    if args.after_branding and args.restore:
        parser.error('--after-branding cannot restore')
    print(apply(args.root, args.check, args.restore, args.before_branding, args.after_branding))


if __name__ == '__main__':
    main()
