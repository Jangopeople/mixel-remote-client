#!/usr/bin/env python3
"""Qualify pinned Mac availability, native failure boundaries and minimums."""
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


def native_function(text, signature):
    start = text.index(signature)
    brace = text.index('{', start)
    depth = 1
    for index in range(brace + 1, len(text)):
        depth += (text[index] == '{') - (text[index] == '}')
        if depth == 0:
            return text[start:index + 1]
    raise AssertionError('Incomplete pinned native function')


original = source(patcher.SOURCE)
assert hashlib.sha256(original.encode()).hexdigest() == patcher.ORIGINAL_SHA256
fixed = patcher.patch_permissions(original)
assert patcher.patch_permissions(fixed) == fixed
normalized = fixed.replace(patcher.NEW_CAPTURE, patcher.OLD_CAPTURE, 1).replace(patcher.NEW_INPUT, patcher.OLD_INPUT, 1)
for old, new in ((patcher.OLD_PIXEL_ENCODING, patcher.NEW_PIXEL_ENCODING),
                 (patcher.OLD_AUTH_FAILURE, patcher.NEW_AUTH_FAILURE),
                 (patcher.OLD_AUTH_EXECUTE, patcher.NEW_AUTH_EXECUTE)):
    normalized = normalized.replace(new, old, 1)
assert normalized == original
reject(lambda: patcher.patch_permissions(original.replace('return false;', 'return true;', 1)))
reject(lambda: patcher.patch_permissions(original + '\n'))
reject(lambda: patcher.patch_permissions(fixed.replace('macOS 10.15', 'macOS 10.14', 1)))
for old, new in ((patcher.OLD_PIXEL_ENCODING, patcher.NEW_PIXEL_ENCODING),
                 (patcher.OLD_AUTH_FAILURE, patcher.NEW_AUTH_FAILURE),
                 (patcher.OLD_AUTH_EXECUTE, patcher.NEW_AUTH_EXECUTE)):
    reject(lambda new=new: patcher.patch_permissions(fixed.replace(new, new + '\n', 1)))
    reject(lambda new=new: patcher.patch_permissions(fixed.replace(new, new * 2, 1)))
    # An existing availability-only patched checkout upgrades strictly through
    # the same full original pin, without requiring a pristine working tree.
    partial = fixed.replace(new, old, 1)
    assert patcher.patch_permissions(partial) == fixed
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
print('PASS: pinned Mac permission/native ownership/runtime correction/idempotence, ARM12.3 and Intel10.14 targets, architecture switching, atomic validation and 22 drift/upgrade rejections')


NATIVE_BOUNDARY_STUBS = r'''
#include <CoreFoundation/CoreFoundation.h>
#include <CoreGraphics/CoreGraphics.h>
#include <Security/Authorization.h>
#include <Security/AuthorizationTags.h>
#include <IOKit/graphics/IOGraphicsTypes.h>
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>
static CFStringRef encoding;
static int encoding_copies, encoding_releases;
static CFStringRef test_CopyPixelEncoding(CGDisplayModeRef mode) {
    assert(mode == NULL);
    encoding_copies++;
    return encoding ? (CFStringRef)CFRetain(encoding) : NULL;
}
static CFComparisonResult test_CFStringCompare(CFStringRef first, CFStringRef second, CFStringCompareFlags options) {
    if (!first) {
        puts("NATIVE_CONTROL: original source enters actual CoreFoundation NULL comparison");
        fflush(stdout);
    }
    CFComparisonResult result = CFStringCompare(first, second, options);
    if (!first) {
        puts("NATIVE_CONTROL: actual CoreFoundation NULL comparison returned");
        fflush(stdout);
    }
    return result;
}
static void test_CFRelease(CFTypeRef value) {
    assert(value && value == encoding);
    encoding_releases++;
    CFRelease(value);
}
static OSStatus create_status, rights_status, execute_status;
static int auth_creates, auth_rights, auth_executes, auth_frees, auth_owned;
static int pipe_requests;
static FILE *owned_pipe;
static const AuthorizationRef owned_auth = (AuthorizationRef)(uintptr_t)0x1234;
static OSStatus test_AuthorizationCreate(const AuthorizationRights *rights,
    const AuthorizationEnvironment *environment, AuthorizationFlags flags, AuthorizationRef *result) {
    assert(!rights && environment == kAuthorizationEmptyEnvironment && flags == kAuthorizationFlagDefaults);
    auth_creates++;
    if (create_status == errAuthorizationSuccess) { *result = owned_auth; auth_owned++; }
    return create_status;
}
static OSStatus test_AuthorizationCopyRights(AuthorizationRef authorization,
    const AuthorizationRights *rights, const AuthorizationEnvironment *environment,
    AuthorizationFlags flags, AuthorizationRights **authorized) {
    assert(authorization == owned_auth && auth_owned == 1 && !auth_frees);
    assert(rights && rights->count == 1 && !strcmp(rights->items[0].name, kAuthorizationRightExecute));
    assert(environment == kAuthorizationEmptyEnvironment && !authorized);
    assert(flags == (kAuthorizationFlagDefaults | kAuthorizationFlagInteractionAllowed |
        kAuthorizationFlagPreAuthorize | kAuthorizationFlagExtendRights));
    auth_rights++;
    return rights_status;
}
static OSStatus test_AuthorizationFree(AuthorizationRef authorization, AuthorizationFlags flags) {
    assert(authorization == owned_auth && auth_owned == 1 && !auth_frees && flags == kAuthorizationFlagDefaults);
    auth_frees++; auth_owned--;
    return errAuthorizationSuccess;
}
static OSStatus test_AuthorizationExecuteWithPrivileges(AuthorizationRef authorization,
    const char *process, AuthorizationFlags flags, char *const *arguments, FILE **pipe) {
    assert(authorization == owned_auth && auth_owned == 1 && !auth_frees);
    assert(process && !strcmp(process, "synthetic-never-executed-tool"));
    assert(flags == kAuthorizationFlagDefaults && arguments && !strcmp(arguments[0], "synthetic") && !arguments[1]);
    auth_executes++;
    if (pipe) {
        pipe_requests++;
        if (execute_status == errAuthorizationSuccess) {
            owned_pipe = tmpfile(); assert(owned_pipe); *pipe = owned_pipe;
        }
    }
    return execute_status;
}
#define CGDisplayModeCopyPixelEncoding test_CopyPixelEncoding
#define CFStringCompare test_CFStringCompare
#define CFRelease test_CFRelease
#define AuthorizationCreate test_AuthorizationCreate
#define AuthorizationCopyRights test_AuthorizationCopyRights
#define AuthorizationFree test_AuthorizationFree
#define AuthorizationExecuteWithPrivileges test_AuthorizationExecuteWithPrivileges
'''

NATIVE_BOUNDARY_MAIN = r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    // The original NULL control uses the real CoreFoundation comparator, but
    // cannot leave a core file behind. No TCC or authorization APIs execute.
    struct rlimit no_core = {0, 0}; assert(setrlimit(RLIMIT_CORE, &no_core) == 0);
    if (!strcmp(argv[1], "pixel-null")) {
        assert(bitDepth(NULL) == 0 && encoding_copies == 1 && !encoding_releases);
        puts("PASS_NATIVE: NULL pixel encoding returns unknown depth0 without CoreFoundation compare/release");
        return 0;
    }
    if (!strcmp(argv[1], "pixel-known")) {
        const CFStringRef values[] = {CFSTR(kIO32BitFloatPixels), CFSTR(kIO64BitDirectPixels),
            CFSTR(kIO16BitFloatPixels), CFSTR(IO32BitDirectPixels), CFSTR(kIO30BitDirectPixels),
            CFSTR(IO16BitDirectPixels), CFSTR(IO8BitIndexedPixels), CFSTR("unrecognized synthetic encoding")};
        const size_t depths[] = {96, 64, 48, 32, 30, 16, 8, 0};
        for (size_t index = 0; index < sizeof(depths) / sizeof(depths[0]); index++) {
            encoding = values[index]; encoding_copies = encoding_releases = 0;
            assert(bitDepth(NULL) == depths[index]);
            assert(encoding_copies == 1 && encoding_releases == 1);
        }
        puts("PASS_NATIVE: seven known pixel encodings plus unknown preserve actual CoreFoundation matching and one release each");
        return 0;
    }
    bool execute = false;
    if (!strcmp(argv[1], "auth-create-error")) create_status = errAuthorizationInternal;
    else if (!strcmp(argv[1], "auth-cancel")) rights_status = errAuthorizationCanceled;
    else if (!strcmp(argv[1], "auth-denied")) rights_status = errAuthorizationDenied;
    else if (!strcmp(argv[1], "auth-rights-error")) rights_status = errAuthorizationInternal;
    else if (!strcmp(argv[1], "auth-success")) {}
    else if (!strcmp(argv[1], "auth-execute-error")) { execute = true; execute_status = errAuthorizationInternal; }
    else if (!strcmp(argv[1], "auth-execute-success")) execute = true;
    else return 64;
    char argument[] = "synthetic", process[] = "synthetic-never-executed-tool";
    char *arguments[] = {argument, NULL};
    bool result = Elevate(execute ? process : NULL, execute ? arguments : NULL);
    bool expected = !create_status && !rights_status && !execute_status;
    assert(result == expected && auth_creates == 1);
    assert(auth_rights == (create_status ? 0 : 1));
    assert(auth_executes == (execute && !create_status && !rights_status ? 1 : 0));
    bool leaked_auth = auth_owned != 0;
    bool unused_pipe = pipe_requests != 0;
    if (owned_pipe) { assert(fclose(owned_pipe) == 0); owned_pipe = NULL; }
    if (leaked_auth) {
        assert(!auth_frees && rights_status && !create_status);
        puts("REPRODUCED_NATIVE: failed/cancelled rights retain the successful-create authorization reference");
        return 42;
    }
    assert(auth_frees == (create_status ? 0 : 1));
    if (unused_pipe) {
        puts("REPRODUCED_NATIVE: process invocation requests an unused communications stream");
        return 43;
    }
    printf("PASS_NATIVE: %s preserves result and releases its successful-create authorization exactly once, with no pipe or OS permission request\n", argv[1]);
    return 0;
}
'''


def qualify_native_boundaries(original_text, fixed_text, folder, arch, minimum):
    functions = ['size_t bitDepth(CGDisplayModeRef mode)', 'extern "C" bool Elevate(char* process, char** args)']
    programs = {}
    for name, text in [('original', original_text), ('fixed', fixed_text)]:
        path = folder / ('boundaries-' + name + '.mm')
        path.write_text(NATIVE_BOUNDARY_STUBS + '\n'.join(native_function(text, signature) for signature in functions) + NATIVE_BOUNDARY_MAIN)
        binary = folder / ('boundaries-' + name)
        # These extracted functions use only C/CoreFoundation. Avoid loading
        # newer SDK libc++ headers or runtime for the Intel10.14 control.
        command = ['xcrun', 'clang++', '-std=c++17', '-arch', arch, '-mmacosx-version-min=' + minimum,
                   '-nostdinc++', '-nostdlib++', '-Wall', '-Wextra', '-Werror', str(path), '-framework', 'CoreFoundation', '-o', str(binary)]
        compiled = subprocess.run(command, text=True, capture_output=True, timeout=60)
        assert compiled.returncode == 0, compiled.stderr
        assert not compiled.stderr, compiled.stderr
        programs[name] = binary
    for name, binary in programs.items():
        cases = ['pixel-null', 'pixel-known', 'auth-create-error', 'auth-cancel', 'auth-denied',
                 'auth-rights-error', 'auth-success', 'auth-execute-error', 'auth-execute-success']
        for case in cases:
            result = subprocess.run([str(binary), case], text=True, capture_output=True, timeout=15)
            if name == 'original' and case == 'pixel-null':
                assert result.returncode in (-4, -6, -10, -11), (result.returncode, result.stdout, result.stderr)
                assert result.stdout.count('original source enters actual CoreFoundation NULL comparison') == 1, result.stdout
                assert 'actual CoreFoundation NULL comparison returned' not in result.stdout, result.stdout
                print('PASS: actual original ' + arch + ' NULL pixel metadata reaches real CoreFoundation failure; exit=' + str(result.returncode))
            elif name == 'original' and case in ('auth-cancel', 'auth-denied', 'auth-rights-error'):
                assert result.returncode == 42 and 'REPRODUCED_NATIVE: failed/cancelled rights' in result.stdout, (result.returncode, result.stdout, result.stderr)
            elif name == 'original' and case in ('auth-execute-error', 'auth-execute-success'):
                assert result.returncode == 43 and 'REPRODUCED_NATIVE: process invocation' in result.stdout, (result.returncode, result.stdout, result.stderr)
            else:
                assert result.returncode == 0 and 'PASS_NATIVE:' in result.stdout, (result.returncode, result.stdout, result.stderr)
    print('PASS: actual source-extracted ' + arch + ' macOS' + minimum + ' pixel NULL/known/unknown controls and create/cancel/denied/error/success/execution authorization ownership; original defects reproduced, corrected16 controls pass; no real Authorization/TCC/elevated execution')

if platform.system() == 'Darwin':
    with tempfile.TemporaryDirectory(prefix='mixel-macos-availability-') as directory:
        folder = Path(directory)
        target = os.environ.get('MACOS_RUNTIME_LINK_TARGET', 'x86_64-apple-darwin')
        assert target in ('x86_64-apple-darwin', 'aarch64-apple-darwin')
        boundary_arch, boundary_minimum = ('x86_64', '10.14') if target.startswith('x86_64') else ('arm64', '12.3')
        qualify_native_boundaries(original, fixed, folder, boundary_arch, boundary_minimum)
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
