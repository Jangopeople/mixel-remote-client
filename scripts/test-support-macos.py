#!/usr/bin/env python3
"""Qualify pinned Mac availability, native failure boundaries and minimums."""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from sdk_discovery import find_dart

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-only', action='store_true',
                    help='Run exact Python source/upgrade controls without any SDK compilation or native query.')
test_options = parser.parse_args()

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
for old, new in ((patcher.OLD_MODE_HELPER, patcher.NEW_MODE_HELPER),
                 (patcher.OLD_CONFIG_HELPER, patcher.NEW_CONFIG_HELPER),
                 (patcher.OLD_AUTHORIZATION, patcher.NEW_AUTHORIZATION)):
    normalized = normalized.replace(new, old, 1)
normalized = normalized.replace(patcher.NEW_MODE_FILTER, patcher.OLD_MODE_FILTER, 2)
assert normalized == original
reject(lambda: patcher.patch_permissions(original.replace('return false;', 'return true;', 1)))
reject(lambda: patcher.patch_permissions(original + '\n'))
reject(lambda: patcher.patch_permissions(fixed.replace('macOS 10.15', 'macOS 10.14', 1)))
for old, new in ((patcher.OLD_MODE_HELPER, patcher.NEW_MODE_HELPER),
                 (patcher.OLD_CONFIG_HELPER, patcher.NEW_CONFIG_HELPER),
                 (patcher.OLD_AUTHORIZATION, patcher.NEW_AUTHORIZATION)):
    reject(lambda new=new: patcher.patch_permissions(fixed.replace(new, new + '\n', 1)))
    reject(lambda new=new: patcher.patch_permissions(fixed.replace(new, new * 2, 1)))
    # An existing availability-only patched checkout upgrades strictly through
    # the same full original pin, without requiring a pristine working tree.
    partial = fixed.replace(new, old, 1)
    assert patcher.patch_permissions(partial) == fixed
legacy = original.replace(patcher.OLD_CAPTURE, patcher.NEW_CAPTURE, 1).replace(patcher.OLD_INPUT, patcher.NEW_INPUT, 1)
legacy_changes = ((patcher.OLD_PIXEL_ENCODING, patcher.NEW_PIXEL_ENCODING),
                  (patcher.OLD_AUTH_FAILURE, patcher.NEW_AUTH_FAILURE),
                  (patcher.OLD_AUTH_EXECUTE, patcher.NEW_AUTH_EXECUTE))
# Preserve every exact previously admitted combination, not a broad legacy
# exception: each is normalized back to the same complete pristine SHA.
for profile in range(8):
    previous = legacy
    for bit, (old, new) in enumerate(legacy_changes):
        if profile & (1 << bit):
            previous = previous.replace(old, new, 1)
    assert patcher.patch_permissions(previous) == fixed
    reject(lambda previous=previous: patcher.patch_permissions(previous + '\n'))
previous_authorization_only = legacy.replace(patcher.OLD_PIXEL_ENCODING, patcher.NEW_PIXEL_ENCODING, 1).replace(patcher.OLD_AUTHORIZATION, patcher.NEW_AUTHORIZATION, 1)
assert patcher.patch_permissions(previous_authorization_only) == fixed
assert 'CGDisplayModeCopyPixelEncoding' not in fixed and 'bitDepth(' not in fixed
assert fixed.count(patcher.NEW_MODE_FILTER) == 2
for token in (patcher.NEW_MODE_FILTER, 'CGDisplayModeIsUsableForDesktopGUI(mode)'):
    reject(lambda token=token: patcher.patch_permissions(fixed.replace(token, token + ' /* drift */', 1)))
legacy_authorization = patcher.OLD_AUTHORIZATION.replace(patcher.OLD_AUTH_FAILURE, patcher.NEW_AUTH_FAILURE, 1)
old_helper = native_function(legacy_authorization, 'extern "C" bool Elevate(char* process, char** args)')
retired_branch = native_function(old_helper, '    if (process != NULL)')
expected_helper = old_helper.replace(retired_branch + '\n\n', '', 1).replace(
    'extern "C" bool Elevate(char* process, char** args)', 'static bool CheckAdminAuthorization()', 1)
assert native_function(fixed, 'static bool CheckAdminAuthorization()') == expected_helper
old_wrapper = native_function(original, 'extern "C" bool MacCheckAdminAuthorization()')
assert native_function(fixed, 'extern "C" bool MacCheckAdminAuthorization()') == old_wrapper.replace(
    'return Elevate(NULL, NULL);', 'return CheckAdminAuthorization();', 1)
assert 'AuthorizationExecuteWithPrivileges' not in fixed and 'Elevate(' not in fixed
for drift in ('CheckAdminAuthorization()', 'kAuthorizationRightExecute',
              'kAuthorizationFlagInteractionAllowed', 'kAuthorizationFlagPreAuthorize',
              'kAuthorizationFlagExtendRights', 'AuthorizationFree(authRef, kAuthorizationFlagDefaults);'):
    reject(lambda drift=drift: patcher.patch_permissions(fixed.replace(drift, drift + ' /* drift */', 1)))
print('PASS: internal authorization-only helper exactly preserves the prior owned NULL-path create/rights flags, returns and cleanup; extern wrapper ABI remains; eight exact legacy profiles upgrade and six authorization drifts refuse')
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
assert intel == (build, patcher.patch_assemble_phase(project), pods)
assert patcher.configure_target(*intel, info, 'x86_64') == intel
# The target, complete phase, inputs and outputs remain exact apart from the
# explicit existing every-build policy. Other shell phases are untouched.
for ending in ('\n', '\r\n'):
    before_phase = project.replace('\n', ending)
    phase_fixed = patcher.patch_assemble_phase(before_phase)
    assert patcher.patch_assemble_phase(phase_fixed) == phase_fixed
    assert phase_fixed.replace(patcher.NEW_ASSEMBLE_PHASE.replace('\n', ending), patcher.OLD_ASSEMBLE_PHASE.replace('\n', ending), 1) == before_phase
for changed in (
    project.replace('33CC111E2044C6BF0003C045 /* ShellScript */,', '33CC111E2044C6BF0003C046 /* ShellScript */,', 1),
    project.replace(patcher.ASSEMBLE_TARGET, patcher.ASSEMBLE_TARGET * 2, 1),
    project.replace(patcher.OLD_ASSEMBLE_PHASE, patcher.OLD_ASSEMBLE_PHASE * 2, 1),
    project.replace('macos_assemble.sh && touch', 'macos_assemble.sh embed && touch', 1),
    project.replace('Flutter/ephemeral/tripwire,', 'Flutter/ephemeral/other,', 1),
    project.replace('/* Begin PBXShellScriptBuildPhase section */', '/* Begin Other section */', 1),
    project.replace(patcher.OLD_ASSEMBLE_PHASE, '', 1) + patcher.OLD_ASSEMBLE_PHASE,
    patcher.patch_assemble_phase(project).replace('alwaysOutOfDate = 1;', 'alwaysOutOfDate = 0;'),
    project.replace('\n', '\r\n', 1),
):
    reject(lambda changed=changed: patcher.patch_assemble_phase(changed))
print('PASS: exact Flutter Assemble target/phase LF and CRLF inverse/idempotence; nine malformed/binding/duplicate/moved/newline controls refuse; script and file lists unchanged')
for ending in ('\n', '\r\n', 'mixed'):
    with tempfile.TemporaryDirectory(prefix='mixel-macos-phase-entry-') as directory:
        repo = Path(directory)
        paths = ['Cargo.toml', patcher.SOURCE, patcher.BUILD_SOURCE, 'build.py', 'flutter/macos/Runner.xcodeproj/project.pbxproj', 'flutter/macos/Podfile', 'flutter/macos/Runner/Info.plist']
        for name in paths:
            file = repo / name
            file.parent.mkdir(parents=True, exist_ok=True)
            data = source(name).encode()
            if name.endswith('project.pbxproj'):
                data = data.replace(b'\n', b'\r\n', 1) if ending == 'mixed' else data.replace(b'\n', ending.encode())
            file.write_bytes(data)
        before = {p: p.read_bytes() for p in repo.rglob('*') if p.is_file()}
        if ending == 'mixed':
            reject(lambda: patcher.apply(repo, 'x86_64'))
            assert all(p.read_bytes() == raw for p, raw in before.items())
        else:
            patcher.apply(repo, 'x86_64')
            file = repo / 'flutter/macos/Runner.xcodeproj/project.pbxproj'
            fixed_project = file.read_bytes()
            assert fixed_project.replace(patcher.NEW_ASSEMBLE_PHASE.replace('\n', ending).encode(), patcher.OLD_ASSEMBLE_PHASE.replace('\n', ending).encode(), 1) == before[file]
            snapshot = {p: p.read_bytes() for p in repo.rglob('*') if p.is_file()}
            patcher.apply(repo, 'x86_64')
            assert all(p.read_bytes() == raw for p, raw in snapshot.items())
print('PASS: actual apply entry preserves LF/CRLF Runner bytes except exact phase insertion, repeats idempotently and refuses mixed-EOL before all writes')
for ending in ('\n', '\r\n', 'mixed'):
    with tempfile.TemporaryDirectory(prefix='mixel-macos-phase-cli-') as directory:
        repo = Path(directory)
        for name in ['Cargo.toml', patcher.SOURCE, patcher.BUILD_SOURCE, 'build.py', 'flutter/macos/Runner.xcodeproj/project.pbxproj', 'flutter/macos/Podfile', 'flutter/macos/Runner/Info.plist']:
            file = repo / name
            file.parent.mkdir(parents=True, exist_ok=True)
            data = source(name).encode()
            if name.endswith('project.pbxproj'):
                data = data.replace(b'\n', b'\r\n', 1) if ending == 'mixed' else data.replace(b'\n', ending.encode())
            file.write_bytes(data)
        before = {p: p.read_bytes() for p in repo.rglob('*') if p.is_file()}
        arguments = [sys.executable, str(ROOT / 'scripts/patch-support-macos.py')]
        environment = dict(os.environ, RDREPO=str(repo), MIXEL_MACOS_ARCH='x86_64')
        result = subprocess.run(arguments, env=environment, text=True, capture_output=True, timeout=30)
        if ending == 'mixed':
            assert result.returncode != 0 and 'Mixed Runner project newlines' in result.stderr
            assert all(p.read_bytes() == raw for p, raw in before.items())
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            file = repo / 'flutter/macos/Runner.xcodeproj/project.pbxproj'
            assert file.read_bytes().replace(patcher.NEW_ASSEMBLE_PHASE.replace('\n', ending).encode(), patcher.OLD_ASSEMBLE_PHASE.replace('\n', ending).encode(), 1) == before[file]
            snapshot = {p: p.read_bytes() for p in repo.rglob('*') if p.is_file()}
            again = subprocess.run(arguments, env=environment, text=True, capture_output=True, timeout=30)
            assert again.returncode == 0, again.stdout + again.stderr
            assert all(p.read_bytes() == raw for p, raw in snapshot.items())
print('PASS: actual subprocess CLI LF/CRLF exits0 and preserves exact project inverse/idempotence; mixed-EOL exits nonzero with every input byte unchanged')

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
print('PASS: pinned Mac permission/native ownership/runtime correction/idempotence, ARM12.3 and Intel10.14 targets, architecture switching, atomic validation and exact drift/upgrade rejections')


# Actual production enumeration/selection bodies with injected metadata/configuration
# boundaries. Fixture generation is Python-only; full default Mac CI compiles/runs it.
MODE_BOUNDARY_STUBS = r'''
#include <CoreFoundation/CoreFoundation.h>
#include <CoreGraphics/CoreGraphics.h>
#include <IOKit/graphics/IOGraphicsTypes.h>
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
struct TestMode { size_t w, h, pw, ph; double rate; bool gui; CFStringRef encoding; };
static TestMode current_mode, candidate[8];
static TestMode *listed[8];
static size_t listed_count;
static bool missing_current, missing_modes, configure_success = true;
static int current_copies, current_releases, array_releases, encoding_copies, gui_queries, configurations;
static CGDisplayModeRef selected;
static CGError begin_status, complete_status, cancel_status;
static int begins, completes, cancels, active_config;
static const CGDisplayConfigRef config_marker = (CGDisplayConfigRef)(uintptr_t)0x3456;
static const CFArrayRef array_marker = (CFArrayRef)(uintptr_t)0x2345;
static TestMode *data(CGDisplayModeRef mode) { assert(mode); return (TestMode *)mode; }
static CGDisplayModeRef test_CopyCurrent(CGDirectDisplayID display) {
    assert(display == 7); current_copies++; return missing_current ? NULL : (CGDisplayModeRef)&current_mode;
}
static CFArrayRef getAllModes(CGDirectDisplayID display) {
    assert(display == 7); return missing_modes ? NULL : array_marker;
}
static CFIndex test_Count(CFArrayRef array) { assert(array == array_marker); return (CFIndex)listed_count; }
static const void *test_Item(CFArrayRef array, CFIndex index) {
    assert(array == array_marker && index >= 0 && (size_t)index < listed_count); return listed[index];
}
static void test_ReleaseMode(CGDisplayModeRef mode) {
    assert(mode == (CGDisplayModeRef)&current_mode); current_releases++;
}
static void test_Release(CFTypeRef value) {
    if (value == array_marker) array_releases++;
    else { assert(value); CFRelease(value); }
}
static size_t test_Width(CGDisplayModeRef mode) { return data(mode)->w; }
static size_t test_Height(CGDisplayModeRef mode) { return data(mode)->h; }
static size_t test_PixelWidth(CGDisplayModeRef mode) { return data(mode)->pw; }
static double test_Rate(CGDisplayModeRef mode) { return data(mode)->rate; }
[[maybe_unused]] static bool test_GUI(CGDisplayModeRef mode) { gui_queries++; return data(mode)->gui; }
[[maybe_unused]] static CFStringRef test_Encoding(CGDisplayModeRef mode) {
    encoding_copies++; CFStringRef value = data(mode)->encoding;
    return value ? (CFStringRef)CFRetain(value) : NULL;
}
static CGError test_Begin(CGDisplayConfigRef *result) {
    begins++; assert(!active_config);
    if (begin_status != kCGErrorSuccess) return begin_status;
    active_config=1; *result=config_marker; return kCGErrorSuccess;
}
static CGError test_Configure(CGDisplayConfigRef config, CGDirectDisplayID display, CGDisplayModeRef mode, CFDictionaryRef options) {
    assert(config==config_marker && active_config==1 && display==7 && !options);
    configurations++; selected=mode; return configure_success ? kCGErrorSuccess : kCGErrorFailure;
}
static CGError test_Complete(CGDisplayConfigRef config, CGConfigureOption option) {
    assert(config==config_marker && active_config==1 && option==kCGConfigureForSession);
    completes++; active_config=0; return complete_status;
}
[[maybe_unused]] static CGError test_Cancel(CGDisplayConfigRef config) {
    assert(config==config_marker && active_config==1); cancels++; active_config=0; return cancel_status;
}
static void reset(void) {
    current_mode = {1920,1080,1920,1080,60,true,CFSTR(IO32BitDirectPixels)};
    listed_count = 0; missing_current = missing_modes = false; configure_success = true;
    current_copies = current_releases = array_releases = encoding_copies = gui_queries = configurations = 0; selected = NULL;
    begin_status = complete_status = cancel_status = kCGErrorSuccess; begins = completes = cancels = active_config = 0;
}
static void add(size_t w, size_t h, size_t pw, double rate, bool gui, CFStringRef encoding) {
    assert(listed_count < 8); size_t i=listed_count++;
    candidate[i] = {w,h,pw,h,rate,gui,encoding}; listed[i] = &candidate[i];
}
#define CGBeginDisplayConfiguration test_Begin
#define CGConfigureDisplayWithDisplayMode test_Configure
#define CGCompleteDisplayConfiguration test_Complete
#define CGCancelDisplayConfiguration test_Cancel
#define CGDisplayCopyDisplayMode test_CopyCurrent
#define CGDisplayModeRelease test_ReleaseMode
#define CFArrayGetCount test_Count
#define CFArrayGetValueAtIndex test_Item
#define CFRelease test_Release
#define CGDisplayModeGetWidth test_Width
#define CGDisplayModeGetHeight test_Height
#define CGDisplayModeGetPixelWidth test_PixelWidth
#define CGDisplayModeGetRefreshRate test_Rate
#define CGDisplayModeIsUsableForDesktopGUI test_GUI
#define CGDisplayModeCopyPixelEncoding test_Encoding
'''

MODE_BOUNDARY_MAIN = r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    uint32_t widths[8] = {}, heights[8] = {}, count = 99; bool hidpis[8] = {};
    if (!strcmp(argv[1], "metadata")) {
        const CFStringRef encodings[] = {NULL, CFSTR(IO8BitIndexedPixels), CFSTR(IO16BitDirectPixels),
            CFSTR(kIO30BitDirectPixels), CFSTR(IO32BitDirectPixels), CFSTR(kIO16BitFloatPixels),
            CFSTR(kIO64BitDirectPixels), CFSTR(kIO32BitFloatPixels)};
        for (size_t i=0; i<sizeof(encodings)/sizeof(encodings[0]); i++) {
            reset(); current_mode.encoding=encodings[i];
            add(1280,720,1280,60,true,encodings[(i+1)%8]);
            assert(MacGetModes(7,widths,heights,hidpis,8,&count));
#ifdef MIXEL_MODERN_MODES
            assert(count==1 && widths[0]==1280 && !hidpis[0]);
            assert(MacSetMode(7,1280,720,true) && selected==(CGDisplayModeRef)&candidate[0]);
            assert(!encoding_copies && current_copies==2 && current_releases==2 && array_releases==2);
#else
            assert(count==0 && !MacSetMode(7,1280,720,true) && !configurations);
            puts("REPRODUCED_MODE: usable mode excluded by legacy current/candidate encoding equality"); return 42;
#endif
        }
        puts("PASS_MODE: actual enumeration and selection accept GUI-usable modes across NULL/8/16/30/32/float/HDR legacy encodings without reading retired metadata");
    } else if (!strcmp(argv[1], "unusable")) {
        reset(); add(1280,720,1280,60,false,CFSTR(IO32BitDirectPixels));
        assert(MacGetModes(7,widths,heights,hidpis,8,&count));
#ifdef MIXEL_MODERN_MODES
        assert(count==0 && !MacSetMode(7,1280,720,true) && !configurations && !encoding_copies);
        puts("PASS_MODE: GUI-unusable modes never enumerate or reach the injected display setter");
#else
        assert(count==1 && MacSetMode(7,1280,720,true) && configurations==1);
        puts("REPRODUCED_MODE: legacy equal depth admits a GUI-unusable mode"); return 43;
#endif
    } else if (!strcmp(argv[1], "selection")) {
        reset(); add(1280,720,1280,59,true,NULL); add(1280,720,1280,60,false,NULL);
        add(1024,768,1024,60,true,NULL); add(1280,720,1280,60,true,NULL); add(1280,720,2560,60,true,NULL);
        assert(MacSetMode(7,1280,720,true) && selected==(CGDisplayModeRef)&candidate[4]);
        assert(MacSetMode(7,1280,720,false) && selected==(CGDisplayModeRef)&candidate[3]);
        listed_count=4;
        assert(MacSetMode(7,1280,720,true) && selected==(CGDisplayModeRef)&candidate[3]);
        int before=configurations; assert(!MacSetMode(7,800,600,true) && configurations==before);
        configure_success=false; assert(!MacSetMode(7,1280,720,true));
        assert(!encoding_copies && current_copies==5 && current_releases==5 && array_releases==5);
        puts("PASS_MODE: actual size/refresh filtering, HiDPI preference, first ordinary mode, fallback and setter failure behavior remain");
    } else if (!strcmp(argv[1], "capacity")) {
        reset(); add(1280,720,1280,60,true,NULL); add(1024,768,1024,60,true,NULL);
        widths[1]=12345; heights[1]=67890;
        assert(MacGetModes(7,widths,heights,hidpis,1,&count) && count==1 && widths[1]==12345 && heights[1]==67890);
        assert(MacGetModes(7,widths,heights,hidpis,0,&count) && count==0);
        assert(!encoding_copies && current_releases==2 && array_releases==2);
        puts("PASS_MODE: actual enumeration honors zero/one capacity without touching out-of-bound outputs");
    } else if (!strcmp(argv[1], "zero-refresh")) {
        reset(); current_mode.rate=0; add(1280,720,1280,60,true,NULL); add(1280,720,1280,0,true,NULL);
        assert(MacGetModes(7,widths,heights,hidpis,8,&count) && count==1);
        assert(MacSetMode(7,1280,720,true) && selected==(CGDisplayModeRef)&candidate[1]);
        assert(!encoding_copies); puts("PASS_MODE: existing exact zero-refresh matching is preserved");
    } else if (!strcmp(argv[1], "configuration")) {
        reset(); add(1280,720,1280,60,true,NULL);
        begin_status=kCGErrorFailure;
        assert(!setDisplayToMode(7,(CGDisplayModeRef)&candidate[0]) && begins==1 && !configurations && !cancels && !completes && !active_config);
        reset(); add(1280,720,1280,60,true,NULL); configure_success=false;
        assert(!setDisplayToMode(7,(CGDisplayModeRef)&candidate[0]));
#ifdef MIXEL_MODERN_MODES
        assert(begins==1 && configurations==1 && cancels==1 && !completes && !active_config);
#else
        assert(begins==1 && configurations==1 && !cancels && !completes && active_config==1);
        puts("REPRODUCED_MODE: failed Configure leaks the successfully begun display configuration"); return 44;
#endif
        reset(); add(1280,720,1280,60,true,NULL); configure_success=false; cancel_status=kCGErrorFailure;
        assert(!setDisplayToMode(7,(CGDisplayModeRef)&candidate[0]) && cancels==1 && !completes && !active_config);
        reset(); add(1280,720,1280,60,true,NULL); complete_status=kCGErrorFailure;
        assert(!setDisplayToMode(7,(CGDisplayModeRef)&candidate[0]) && !cancels && completes==1 && !active_config);
        reset(); add(1280,720,1280,60,true,NULL);
        assert(setDisplayToMode(7,(CGDisplayModeRef)&candidate[0]) && begins==1 && configurations==1 && completes==1 && !cancels && !active_config);
        puts("PASS_MODE: actual config helper cancels only failed Configure, preserves cancel-failure false, and never reuses invalidated Begin/Complete outcomes");
    } else if (!strcmp(argv[1], "missing")) {
        reset();
#ifdef MIXEL_MODERN_MODES
        assert(!isUsableDisplayMode(NULL) && !gui_queries);
#endif
        missing_current=true;
        assert(!MacGetModes(7,widths,heights,hidpis,8,&count) && !MacSetMode(7,1280,720,true));
        assert(!current_releases && !array_releases && !configurations);
        missing_current=false; missing_modes=true;
        assert(!MacGetModes(7,widths,heights,hidpis,8,&count) && !MacSetMode(7,1280,720,true));
        assert(current_releases==2 && !array_releases && !configurations && !encoding_copies);
        puts("PASS_MODE: NULL candidate/current/array paths preserve failure, release ownership and no display action");
    } else return 64;
    return 0;
}
'''


def mode_fixture(text, modern):
    helper = 'static bool isUsableDisplayMode(CGDisplayModeRef mode)' if modern else 'size_t bitDepth(CGDisplayModeRef mode)'
    signatures = [helper, 'static bool isHiDPIMode(CGDisplayModeRef mode)',
                  'static bool setDisplayToMode(CGDirectDisplayID display, CGDisplayModeRef mode)',
                  'extern "C" bool MacGetModes(', 'extern "C" bool MacSetMode(']
    bodies = '\n'.join(native_function(text, signature) for signature in signatures)
    if modern:
        assert 'CGDisplayModeCopyPixelEncoding' not in bodies and 'bitDepth(' not in bodies
        assert bodies.count(patcher.NEW_MODE_FILTER) == 2
    return ('#define MIXEL_MODERN_MODES 1\n' if modern else '') + MODE_BOUNDARY_STUBS + bodies + MODE_BOUNDARY_MAIN


def qualify_native_modes(original_text, fixed_text, folder, arch, minimum):
    # The negative control is the exact prior owned NULL-safe mode source, not
    # an invented selector. Authorization is not part of this isolated fixture.
    legacy_text = original_text.replace(patcher.OLD_PIXEL_ENCODING, patcher.NEW_PIXEL_ENCODING, 1)
    for name, text, modern in [('legacy', legacy_text, False), ('fixed', fixed_text, True)]:
        path=folder/('display-modes-'+name+'.mm'); path.write_text(mode_fixture(text, modern))
        binary=folder/('display-modes-'+name)
        result=subprocess.run(['xcrun','clang++','-std=c++17','-arch',arch,'-mmacosx-version-min='+minimum,
                               '-nostdinc++','-nostdlib++','-Wall','-Wextra','-Werror',str(path),
                               '-framework','CoreFoundation','-framework','CoreGraphics','-o',str(binary)],
                              text=True,capture_output=True,timeout=60)
        assert result.returncode==0 and not result.stderr, result.stderr
        cases=['metadata','unusable','configuration'] if not modern else ['metadata','unusable','selection','capacity','zero-refresh','missing','configuration']
        for case in cases:
            outcome=subprocess.run([str(binary),case],text=True,capture_output=True,timeout=15)
            expected=0 if modern else {'metadata':42,'unusable':43,'configuration':44}[case]
            assert outcome.returncode==expected and ('PASS_MODE:' if modern else 'REPRODUCED_MODE:') in outcome.stdout, (outcome.returncode,outcome.stdout,outcome.stderr)
    print('PASS: source-extracted '+arch+' mode controls exclude legacy mismatched-depth/unusable-mode defects and preserve public GUI usability, size/refresh/HiDPI/capacity/ownership without real display changes')


legacy_mode_text = original.replace(patcher.OLD_PIXEL_ENCODING, patcher.NEW_PIXEL_ENCODING, 1)
assert 'if (pixelEncoding == NULL)' in mode_fixture(legacy_mode_text, False)
assert native_function(fixed, 'static bool isUsableDisplayMode(CGDisplayModeRef mode)') in mode_fixture(fixed, True)
print('SOURCE SCOPE: actual enumeration/selection mode fixture bodies and exact prior owned NULL-safe negative projection prepared; compiler/runtime outcomes remain pending until full default Mac CI')

if test_options.source_only:
    print('SOURCE SCOPE: SDK compilation, extracted-C authorization/display-mode outcomes, native permission queries and texture suite were not executed (--source-only)')
    sys.exit(0)


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
#ifndef MIXEL_AUTHORIZATION_ONLY
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
#endif
static OSStatus create_status, rights_status;
static int auth_creates, auth_rights, auth_frees, auth_owned;
#ifndef MIXEL_AUTHORIZATION_ONLY
static OSStatus execute_status;
static int auth_executes;
static int pipe_requests;
static FILE *owned_pipe;
#endif
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
#ifndef MIXEL_AUTHORIZATION_ONLY
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
#endif
#ifndef MIXEL_AUTHORIZATION_ONLY
#define CGDisplayModeCopyPixelEncoding test_CopyPixelEncoding
#define CFStringCompare test_CFStringCompare
#define CFRelease test_CFRelease
#endif
#define AuthorizationCreate test_AuthorizationCreate
#define AuthorizationCopyRights test_AuthorizationCopyRights
#define AuthorizationFree test_AuthorizationFree
#ifndef MIXEL_AUTHORIZATION_ONLY
#define AuthorizationExecuteWithPrivileges test_AuthorizationExecuteWithPrivileges
#endif
'''

NATIVE_BOUNDARY_MAIN = r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    // The original NULL control uses the real CoreFoundation comparator, but
    // cannot leave a core file behind. No TCC or authorization APIs execute.
    struct rlimit no_core = {0, 0}; assert(setrlimit(RLIMIT_CORE, &no_core) == 0);
#ifndef MIXEL_AUTHORIZATION_ONLY
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
#endif
#ifndef MIXEL_AUTHORIZATION_ONLY
    bool execute = false;
#endif
    if (!strcmp(argv[1], "auth-create-error")) create_status = errAuthorizationInternal;
    else if (!strcmp(argv[1], "auth-cancel")) rights_status = errAuthorizationCanceled;
    else if (!strcmp(argv[1], "auth-denied")) rights_status = errAuthorizationDenied;
    else if (!strcmp(argv[1], "auth-rights-error")) rights_status = errAuthorizationInternal;
    else if (!strcmp(argv[1], "auth-success")) {}
#ifndef MIXEL_AUTHORIZATION_ONLY
    else if (!strcmp(argv[1], "auth-execute-error")) { execute = true; execute_status = errAuthorizationInternal; }
    else if (!strcmp(argv[1], "auth-execute-success")) execute = true;
#endif
    else return 64;
#ifdef MIXEL_AUTHORIZATION_ONLY
    bool result = MacCheckAdminAuthorization();
    bool expected = !create_status && !rights_status;
#else
    char argument[] = "synthetic", process[] = "synthetic-never-executed-tool";
    char *arguments[] = {argument, NULL};
    bool result = Elevate(execute ? process : NULL, execute ? arguments : NULL);
    bool expected = !create_status && !rights_status && !execute_status;
#endif
    assert(result == expected && auth_creates == 1);
    assert(auth_rights == (create_status ? 0 : 1));
    bool leaked_auth = auth_owned != 0;
#ifndef MIXEL_AUTHORIZATION_ONLY
    assert(auth_executes == (execute && !create_status && !rights_status ? 1 : 0));
    bool unused_pipe = pipe_requests != 0;
    if (owned_pipe) { assert(fclose(owned_pipe) == 0); owned_pipe = NULL; }
#endif
    if (leaked_auth) {
        assert(!auth_frees && rights_status && !create_status);
        puts("REPRODUCED_NATIVE: failed/cancelled rights retain the successful-create authorization reference");
        return 42;
    }
    assert(auth_frees == (create_status ? 0 : 1));
#ifndef MIXEL_AUTHORIZATION_ONLY
    if (unused_pipe) {
        puts("REPRODUCED_NATIVE: process invocation requests an unused communications stream");
        return 43;
    }
#endif
    printf("PASS_NATIVE: %s preserves result and releases its successful-create authorization exactly once, with no pipe or OS permission request\n", argv[1]);
    return 0;
}
'''


def qualify_native_boundaries(original_text, fixed_text, folder, arch, minimum):
    programs = {}
    for name, text in [('original', original_text), ('fixed', fixed_text)]:
        functions = ([] if name == 'fixed' else ['size_t bitDepth(CGDisplayModeRef mode)']) + [
                     ('extern "C" bool Elevate(char* process, char** args)' if name == 'original'
                      else 'static bool CheckAdminAuthorization()'),
                     'extern "C" bool MacCheckAdminAuthorization()']
        path = folder / ('boundaries-' + name + '.mm')
        mode = '#define MIXEL_AUTHORIZATION_ONLY 1\n' if name == 'fixed' else ''
        path.write_text(mode + NATIVE_BOUNDARY_STUBS + '\n'.join(native_function(text, signature) for signature in functions) + NATIVE_BOUNDARY_MAIN)
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
        cases = ['auth-create-error', 'auth-cancel', 'auth-denied', 'auth-rights-error', 'auth-success']
        if name == 'original':
            cases = ['pixel-null', 'pixel-known'] + cases
        if name == 'original':
            cases += ['auth-execute-error', 'auth-execute-success']
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
    print('PASS: actual source-extracted ' + arch + ' macOS' + minimum + ' corrected create/cancel/denied/error/success controls pass through the unchanged extern wrapper; all original pixel/execution controls remain; no real Authorization/TCC/elevated execution')

if platform.system() == 'Darwin':
    with tempfile.TemporaryDirectory(prefix='mixel-macos-availability-') as directory:
        folder = Path(directory)
        target = os.environ.get('MACOS_RUNTIME_LINK_TARGET', 'x86_64-apple-darwin')
        assert target in ('x86_64-apple-darwin', 'aarch64-apple-darwin')
        boundary_arch, boundary_minimum = ('x86_64', '10.14') if target.startswith('x86_64') else ('arm64', '12.3')
        qualify_native_boundaries(original, fixed, folder, boundary_arch, boundary_minimum)
        qualify_native_modes(original, fixed, folder, boundary_arch, boundary_minimum)
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

# The standalone Mac source job has no Flutter SDK; complete source jobs run
# the same lifecycle suite with actual Dart async controls as well.
texture_arguments = [sys.executable, str(ROOT / 'scripts/test-support-macos-texture.py')]
if platform.system() == 'Darwin' and not find_dart():
    texture_arguments.append('--native-only')
subprocess.run(texture_arguments, check=True)
