#!/usr/bin/env python3
"""Preserve native fatal-crash handling on the exact pinned desktop sources.

The application no longer allocates, locks input or mutates GPU options from
SIGSEGV. Normal shutdown and manually chosen GPU settings remain unchanged.
Both whole-file profiles are validated before either target can be written.
"""
import argparse
import hashlib
from pathlib import Path
import stat

UPSTREAM = '1abc897c451c8b5bbff3792509a7fef9d12f2ce3'
HBB = '48c37de3e6c4e399af6f51ca20e8e3e1fd037976'
FILES = ['src/core_main.rs', 'libs/hbb_common/src/platform/mod.rs']
PROFILES = [{'name': 'pristine', 'coreOriginal': '3bc241c59d547352e77bfa1b44d6ea0e1200b3dd703ed4a63fbb720b07187512', 'coreCorrected': 'a729fd5ec47d598ba4f2307fd1365485184d22fc9ebbdfb3b565c0d4aa70978d', 'platformOriginal': 'e378372d663af6d7b944f8807d904bacf2c32e66a3f48d1d76d804ef62a33fd1'}, {'name': 'composed', 'coreOriginal': 'c2c7909b4c7c4ed7ca173ac614bb017266e3ff220fb19bedc0b8dbbf0f2ca00d', 'coreCorrected': 'da22c16e10d6dcc8816dae300336cf5f3897834c65221a5a5f5344ec69901bd3', 'platformOriginal': '85a0f3d9bb68de2effe148c4f675c99a19d3e1492d647d7a239e07deeacd9f62'}]
CHANGES = [('#[cfg(not(debug_assertions))]\n#[cfg(not(any(target_os = "android", target_os = "ios")))]\nuse crate::platform::breakdown_callback;\n#[cfg(not(debug_assertions))]\n#[cfg(not(any(target_os = "android", target_os = "ios")))]\nuse hbb_common::platform::register_breakdown_handler;\nuse hbb_common::{config, log};\n', 'use hbb_common::{config, log};\n'), ('    #[cfg(not(debug_assertions))]\n    #[cfg(not(any(target_os = "android", target_os = "ios")))]\n    register_breakdown_handler(breakdown_callback);\n    #[cfg(target_os = "linux")]\n    #[cfg(feature = "flutter")]\n    {\n        let (k, v) = ("LIBGL_ALWAYS_SOFTWARE", "1");', '    #[cfg(target_os = "linux")]\n    #[cfg(feature = "flutter")]\n    {\n        let (k, v) = ("LIBGL_ALWAYS_SOFTWARE", "1");')]
ORIGINAL_PLATFORM = b'#[cfg(target_os = "linux")]\npub mod linux;\n\n#[cfg(target_os = "macos")]\npub mod macos;\n\n#[cfg(target_os = "windows")]\npub mod windows;\n\n#[cfg(not(debug_assertions))]\nuse crate::{config::Config, log};\n#[cfg(not(debug_assertions))]\nuse std::process::exit;\n\n#[cfg(not(debug_assertions))]\nstatic mut GLOBAL_CALLBACK: Option<Box<dyn Fn()>> = None;\n\n#[cfg(not(debug_assertions))]\nextern "C" fn breakdown_signal_handler(sig: i32) {\n    let mut stack = vec![];\n    backtrace::trace(|frame| {\n        backtrace::resolve_frame(frame, |symbol| {\n            if let Some(name) = symbol.name() {\n                stack.push(name.to_string());\n            }\n        });\n        true // keep going to the next frame\n    });\n    let mut info = String::default();\n    if stack.iter().any(|s| {\n        s.contains(&"nouveau_pushbuf_kick")\n            || s.to_lowercase().contains("nvidia")\n            || s.contains("gdk_window_end_draw_frame")\n            || s.contains("glGetString")\n    }) {\n        Config::set_option("allow-always-software-render".to_string(), "Y".to_string());\n        info = "Always use software rendering will be set.".to_string();\n        log::info!("{}", info);\n    }\n    if stack.iter().any(|s| {\n        s.to_lowercase().contains("nvidia")\n            || s.to_lowercase().contains("amf")\n            || s.to_lowercase().contains("mfx")\n            || s.contains("cuProfilerStop")\n    }) {\n        Config::set_option("enable-hwcodec".to_string(), "N".to_string());\n        info = "Perhaps hwcodec causing the crash, disable it first".to_string();\n        log::info!("{}", info);\n    }\n    log::error!(\n        "Got signal {} and exit. stack:\\n{}",\n        sig,\n        stack.join("\\n").to_string()\n    );\n    if !info.is_empty() {\n        #[cfg(target_os = "linux")]\n        linux::system_message(\n            "RustDesk",\n            &format!("Got signal {} and exit.{}", sig, info),\n            true,\n        )\n        .ok();\n    }\n    unsafe {\n        #[allow(static_mut_refs)]\n        if let Some(callback) = &GLOBAL_CALLBACK {\n            callback()\n        }\n    }\n    exit(0);\n}\n\n#[cfg(not(debug_assertions))]\npub fn register_breakdown_handler<T>(callback: T)\nwhere\n    T: Fn() + \'static,\n{\n    unsafe {\n        GLOBAL_CALLBACK = Some(Box::new(callback));\n        libc::signal(libc::SIGSEGV, breakdown_signal_handler as _);\n    }\n}\n'
CORRECTED_PLATFORM = b'#[cfg(target_os = "linux")]\npub mod linux;\n\n#[cfg(target_os = "macos")]\npub mod macos;\n\n#[cfg(target_os = "windows")]\npub mod windows;\n\n// Keep native fatal-crash handling; do not lock, allocate or change settings in a signal handler.\n'


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(raw):
    if b'\r' in raw:
        remainder = raw.replace(b'\r\n', b'')
        if b'\r' in remainder or b'\n' in remainder:
            raise RuntimeError('Mixed fatal-crash source newlines')
        return raw.replace(b'\r\n', b'\n'), b'\r\n'
    return raw, b'\n'


def safe_target(root, relative):
    root = Path(root).absolute()
    target = root / relative
    for parent in reversed(target.parents):
        entry = parent.lstat()
        if (not stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode)
                or getattr(entry, 'st_file_attributes', 0) & 0x400):
            raise RuntimeError('Unsafe fatal-crash source parent')
    entry = target.lstat()
    if (not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1
            or entry.st_size > 1000000
            or getattr(entry, 'st_file_attributes', 0) & 0x400):
        raise RuntimeError('Unsafe fatal-crash source file: ' + relative)
    return target


def core_transform(data, restore=False):
    text = data.decode('utf-8')
    for before, after in reversed(CHANGES) if restore else CHANGES:
        old, new = (after, before) if restore else (before, after)
        if text.count(old) != 1:
            raise RuntimeError('Fatal-crash core anchor changed')
        text = text.replace(old, new, 1)
    return text.encode('utf-8')


def project(core, platform, restore=False):
    for profile in PROFILES:
        if digest(core) == profile['coreOriginal']:
            if digest(platform) != profile['platformOriginal']:
                raise RuntimeError('Fatal-crash source pair changed')
            corrected = core_transform(core)
            if digest(corrected) != profile['coreCorrected']:
                raise RuntimeError('Fatal-crash corrected core mismatch')
            return (core, platform) if restore else (corrected, CORRECTED_PLATFORM)
        if digest(core) == profile['coreCorrected']:
            if platform != CORRECTED_PLATFORM:
                raise RuntimeError('Fatal-crash corrected platform changed')
            original = core_transform(core, restore=True)
            platform_original = ORIGINAL_PLATFORM
            if profile['name'] == 'composed':
                platform_original = platform_original.replace(b'"RustDesk"', b'"Mixel Remote"')
            if (digest(original) != profile['coreOriginal']
                    or digest(platform_original) != profile['platformOriginal']):
                raise RuntimeError('Fatal-crash inverse source mismatch')
            return (original, platform_original) if restore else (core, platform)
    raise RuntimeError('Pinned fatal-crash core source changed')


def apply(root, restore=False, check=False):
    paths = [safe_target(root, relative) for relative in FILES]
    original = [path.read_bytes() for path in paths]
    normalized = [canonical(raw) for raw in original]
    desired = project(normalized[0][0], normalized[1][0], restore)
    outputs = [data.replace(b'\n', normalized[index][1]) for index, data in enumerate(desired)]
    if not check:
        for path, before, after in zip(paths, original, outputs):
            if before != after:
                path.write_bytes(after)
    return {relative: digest(data) for relative, data in zip(FILES, outputs)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    apply(args.source, restore=args.restore, check=args.check)
    print('PASS exact native fatal-crash handling source; normal cleanup and manual GPU options conserved')


if __name__ == '__main__':
    main()
