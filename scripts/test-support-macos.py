#!/usr/bin/env python3
"""Qualify pinned Mac availability guards and architecture-specific minimums."""
import hashlib
import importlib.util
import os
from pathlib import Path
import platform
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get('RDREPO', ROOT / 'rustdesk'))
spec = importlib.util.spec_from_file_location('mixel_macos_patch', ROOT / 'scripts/patch-support-macos.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def source(path):
    return subprocess.check_output(['git', '-C', str(UPSTREAM), 'show', 'HEAD:' + path], text=True, encoding='utf-8')


def reject(operation):
    try:
        operation()
    except RuntimeError:
        return
    raise AssertionError('Pinned Mac source drift accepted')


original = source(patcher.SOURCE)
assert hashlib.sha256(original.encode()).hexdigest() == patcher.ORIGINAL_SHA256
fixed = patcher.patch_permissions(original)
assert patcher.patch_permissions(fixed) == fixed
assert fixed.replace(patcher.NEW_CAPTURE, patcher.OLD_CAPTURE, 1).replace(patcher.NEW_INPUT, patcher.OLD_INPUT, 1) == original
reject(lambda: patcher.patch_permissions(original.replace('return false;', 'return true;', 1)))
reject(lambda: patcher.patch_permissions(original + '\n'))
reject(lambda: patcher.patch_permissions(fixed.replace('macOS 10.15', 'macOS 10.14', 1)))
original_build = source(patcher.BUILD_SOURCE)
fixed_build = patcher.patch_runtime(original_build)
assert patcher.patch_runtime(fixed_build) == fixed_build
assert fixed_build.replace(patcher.NEW_RUNTIME, patcher.OLD_RUNTIME, 1) == original_build
reject(lambda: patcher.patch_runtime(original_build + '\n'))
reject(lambda: patcher.patch_runtime(original_build.replace('compile("macos")', 'compile("other")', 1)))
reject(lambda: patcher.patch_runtime(fixed_build.replace('libclang_rt.osx.a', 'libother.a', 1)))
reject(lambda: patcher.patch_runtime(fixed_build.replace(patcher.NEW_RUNTIME, patcher.NEW_RUNTIME * 2, 1)))
build, project, pods, info = [source(p) for p in ['build.py', 'flutter/macos/Runner.xcodeproj/project.pbxproj', 'flutter/macos/Podfile', 'flutter/macos/Runner/Info.plist']]
arm = patcher.configure_target(build, project, pods, info, 'aarch64')
assert arm[0].count('MACOSX_DEPLOYMENT_TARGET=12.3') == 1
assert arm[1].count('MACOSX_DEPLOYMENT_TARGET = 12.3;') == 6
assert "platform :osx, '12.3'" in arm[2]
assert patcher.configure_target(*arm, info, 'aarch64') == arm
intel = patcher.configure_target(*arm, info, 'x86_64')
assert intel == (build, project, pods)
assert patcher.configure_target(*intel, info, 'x86_64') == intel
reject(lambda: patcher.configure_target(build, project, pods, info, 'arm64'))
reject(lambda: patcher.configure_target(build.replace('MACOSX_DEPLOYMENT_TARGET=10.14', 'MACOSX_DEPLOYMENT_TARGET=11.0'), project, pods, info, 'aarch64'))
reject(lambda: patcher.configure_target(build, project.replace('MACOSX_DEPLOYMENT_TARGET = 10.14;', 'MACOSX_DEPLOYMENT_TARGET = 11.0;', 1), pods, info, 'aarch64'))
reject(lambda: patcher.configure_target(build, project.replace('MACOSX_DEPLOYMENT_TARGET = 10.14;', '', 1), pods, info, 'aarch64'))
reject(lambda: patcher.configure_target(build, project, pods.replace("platform :osx, '10.14'", "platform :osx, '11.0'"), info, 'aarch64'))
reject(lambda: patcher.configure_target(build, project, pods, info.replace('$(MACOSX_DEPLOYMENT_TARGET)', '10.14'), 'aarch64'))
with tempfile.TemporaryDirectory(prefix='mixel-macos-target-') as directory:
    repo = Path(directory)
    for path in ['Cargo.toml', patcher.SOURCE, patcher.BUILD_SOURCE, 'build.py', 'flutter/macos/Runner.xcodeproj/project.pbxproj', 'flutter/macos/Podfile', 'flutter/macos/Runner/Info.plist']:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source(path))
    before = {p: p.read_bytes() for p in repo.rglob('*') if p.is_file()}
    reject(lambda: patcher.apply(repo, 'unknown'))
    assert all(p.read_bytes() == data for p, data in before.items())
    (repo / patcher.BUILD_SOURCE).write_text(original_build + '\n')
    corrupt = {p: p.read_bytes() for p in repo.rglob('*') if p.is_file()}
    reject(lambda: patcher.apply(repo, 'aarch64'))
    assert all(p.read_bytes() == data for p, data in corrupt.items())
    (repo / patcher.BUILD_SOURCE).write_text(original_build)
    patcher.apply(repo, 'aarch64')
    snapshot = {p: p.read_bytes() for p in repo.rglob('*') if p.is_file()}
    patcher.apply(repo, 'aarch64')
    assert all(p.read_bytes() == data for p, data in snapshot.items())
    (repo / 'Cargo.toml').write_text(source('Cargo.toml').replace('version = "1.4.6"', 'version = "1.4.7"', 1))
    reject(lambda: patcher.apply(repo, 'aarch64'))
print('PASS: pinned Mac permission/runtime correction/idempotence, ARM12.3 and Intel10.14 targets, architecture switching, atomic validation and 16 drift/upgrade rejections')

if platform.system() == 'Darwin':
    with tempfile.TemporaryDirectory(prefix='mixel-macos-availability-') as directory:
        folder = Path(directory)
        header = original[:original.index('extern "C" bool CanUseNewApiForScreenCaptureCheck()')]
        def function(text, signature):
            start = text.index(signature)
            brace = text.index('{', start)
            depth = 1
            for i in range(brace + 1, len(text)):
                depth += (text[i] == '{') - (text[i] == '}')
                if depth == 0:
                    return text[start:i + 1]
            raise AssertionError('Incomplete native function')
        signatures = ['extern "C" bool IsCanScreenRecording(bool prompt)', 'extern "C" bool InputMonitoringAuthStatus(bool prompt)']
        bodies = {}
        for name, text in [('original', original), ('fixed', fixed)]:
            path = folder / (name + '.mm')
            path.write_text(header + '\n'.join(function(text, signature) for signature in signatures))
            result = subprocess.run(['xcrun', 'clang++', '-fsyntax-only', '-std=c++17', '-arch', 'x86_64', '-mmacosx-version-min=10.14', '-Werror=unguarded-availability', '-Werror=unguarded-availability-new', str(path)], text=True, capture_output=True)
            if name == 'original':
                assert result.returncode != 0 and all(api in result.stderr for api in ['CGPreflightScreenCaptureAccess', 'CGRequestScreenCaptureAccess', 'IOHIDCheckAccess', 'IOHIDRequestAccess']), result.stderr
            else:
                assert result.returncode == 0, result.stderr
            bodies[name] = path
        print('PASS: actual SDK compiler rejects four original unguarded permission calls at Intel10.14; corrected calls compile with availability warnings as errors')
        rustc = os.environ.get('MAC_RUSTC_BIN', 'rustc')
        version = subprocess.check_output([rustc, '--version'], text=True)
        assert version.startswith('rustc 1.81.'), 'Set MAC_RUSTC_BIN to the pinned Mac Rust1.81 compiler for the actual final-link regression'
        target = os.environ.get('MACOS_RUNTIME_LINK_TARGET', 'x86_64-apple-darwin')
        assert target in ('x86_64-apple-darwin', 'aarch64-apple-darwin')
        arch, minimum = ('x86_64', '10.14') if target.startswith('x86_64') else ('arm64', '12.3')
        runtime_path = Path(subprocess.check_output(['xcrun', 'clang', '-print-file-name=libclang_rt.osx.a'], text=True).strip())
        assert runtime_path.is_absolute() and runtime_path.is_file()
        runtime_symbols = subprocess.check_output(['xcrun', 'nm', '-arch', arch, str(runtime_path)], text=True, stderr=subprocess.DEVNULL)
        assert ' T ___isPlatformVersionAtLeast' in runtime_symbols
        object_path = folder / 'permissions.o'
        subprocess.run(['xcrun', 'clang++', '-c', '-std=c++17', '-arch', arch, '-mmacosx-version-min=' + minimum, '-Werror=unguarded-availability', '-Werror=unguarded-availability-new', str(bodies['fixed']), '-o', str(object_path)], check=True, capture_output=True)
        rust_source = folder / 'permission-link.rs'
        rust_source.write_text('extern "C" { fn IsCanScreenRecording(prompt: bool) -> bool; fn InputMonitoringAuthStatus(prompt: bool) -> bool; }\n#[no_mangle] pub unsafe extern "C" fn mixel_read_only_permissions() -> u32 { (IsCanScreenRecording(false) as u32) | ((InputMonitoringAuthStatus(false) as u32) << 1) }\n')
        environment = dict(os.environ, MACOSX_DEPLOYMENT_TARGET=minimum)
        arguments = [rustc, '--edition=2021', '--crate-type=cdylib', '--target', target, str(rust_source), '-C', 'link-arg=' + str(object_path)]
        for framework in ('AppKit', 'CoreGraphics', 'IOKit'):
            arguments += ['-l', 'framework=' + framework]
        if arch == 'x86_64':
            negative = subprocess.run(arguments + ['-o', str(folder / 'without-runtime.dylib')], env=environment, text=True, capture_output=True)
            assert negative.returncode != 0 and '___isPlatformVersionAtLeast' in negative.stderr, negative.stderr
            print('PASS: actual pinned Rust1.81 Intel10.14 final link reproduces the missing availability-runtime symbol without the correction')
        subprocess.run(arguments + ['-L', 'native=' + str(runtime_path.parent), '-l', 'static=clang_rt.osx', '-o', str(folder / 'with-runtime.dylib')], env=environment, check=True, capture_output=True)
        print('PASS: actual pinned Rust1.81 ' + arch + ' macOS' + minimum + ' final link resolves the compiler-selected availability runtime')
        path = bodies['fixed']
        path.write_text(path.read_text() + '\n#include <cstdio>\nint main() { bool screen=IsCanScreenRecording(false); bool input=InputMonitoringAuthStatus(false); printf("READ_ONLY_NATIVE_PERMISSIONS screen=%d input=%d\\n", screen, input); return 0; }\n')
        executable = folder / 'native-permission-query'
        subprocess.run(['xcrun', 'clang++', '-std=c++17', str(path), '-framework', 'AppKit', '-framework', 'CoreGraphics', '-framework', 'IOKit', '-o', str(executable)], check=True)
        actual = subprocess.check_output([str(executable)], text=True, timeout=30)
        assert 'READ_ONLY_NATIVE_PERMISSIONS screen=' in actual
        print(actual.strip())
        print('PASS: actual corrected native permission functions execute read-only with prompt=false; no TCC grant or product launch')
else:
    print('INFO: actual Apple SDK compilation/read-only permission query is exercised by Mac build preflight')
