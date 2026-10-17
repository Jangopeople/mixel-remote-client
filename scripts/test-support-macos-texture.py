#!/usr/bin/env python3
"""Run pinned renderer lifecycle, frame-lock, async Dart and materialization controls."""
import argparse

import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
from unittest.mock import patch

from sdk_discovery import find_dart

# Load the exact sibling helper for direct, runpy and file-spec entry points.
_input_lifecycle_spec = importlib.util.spec_from_file_location(
    "mixel_input_lifecycle_projection",
    Path(__file__).with_name("input_lifecycle_integration_projection.py"),
)
input_lifecycle = importlib.util.module_from_spec(_input_lifecycle_spec)
_input_lifecycle_spec.loader.exec_module(input_lifecycle)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mixel_texture_patch", ROOT / "scripts/patch-support-macos-texture.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
FILES = ("Cargo.toml", "src/flutter.rs", "src/flutter_ffi.rs", "flutter/lib/web/bridge.dart", "flutter/lib/models/desktop_render_texture.dart", "flutter/pubspec.yaml")


def run(args, output, name):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    (output / name).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"{name} failed ({result.returncode}): {result.stdout}{result.stderr}")
    return result.stdout


# Exact public headers mapped into FlutterMacOS by engine framework_common.gni.
# The tiny module wrapper used below is a fixture, not a complete Flutter SDK.
FLUTTER_ENGINE = "82bd5b7209295a5b7ff8cae0df96e7870171e3a5"
FLUTTER_HEADERS = {
    'FlutterTexture.h': ('25ca4de1af6cbfac729e50f7462eb5d4de72f87a2ea545a9da1cab21707f23c6', r"""// Copyright 2013 The Flutter Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef FLUTTER_SHELL_PLATFORM_DARWIN_COMMON_FRAMEWORK_HEADERS_FLUTTERTEXTURE_H_
#define FLUTTER_SHELL_PLATFORM_DARWIN_COMMON_FRAMEWORK_HEADERS_FLUTTERTEXTURE_H_

#import <CoreMedia/CoreMedia.h>
#import <Foundation/Foundation.h>

#import "FlutterMacros.h"

NS_ASSUME_NONNULL_BEGIN

FLUTTER_DARWIN_EXPORT
/**
 * Represents a texture that can be shared with Flutter.
 *
 * See also: https://github.com/flutter/plugins/tree/master/packages/camera
 */
@protocol FlutterTexture <NSObject>
/**
 * Copy the contents of the texture into a `CVPixelBuffer`.
 *
 * The type of the pixel buffer is one of the following:
 * - `kCVPixelFormatType_32BGRA`
 * - `kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange`
 * - `kCVPixelFormatType_420YpCbCr8BiPlanarFullRange`
 */
- (CVPixelBufferRef _Nullable)copyPixelBuffer;

/**
 * Called when the texture is unregistered.
 *
 * Called on the raster thread.
 */
@optional
- (void)onTextureUnregistered:(NSObject<FlutterTexture>*)texture;
@end

FLUTTER_DARWIN_EXPORT
/**
 * A collection of registered `FlutterTexture`'s.
 */
@protocol FlutterTextureRegistry <NSObject>
/**
 * Registers a `FlutterTexture` for usage in Flutter and returns an id that can be used to reference
 * that texture when calling into Flutter with channels. Textures must be registered on the
 * platform thread. On success returns the pointer to the registered texture, else returns 0.
 */
- (int64_t)registerTexture:(NSObject<FlutterTexture>*)texture;
/**
 * Notifies Flutter that the content of the previously registered texture has been updated.
 *
 * This will trigger a call to `-[FlutterTexture copyPixelBuffer]` on the raster thread.
 */
- (void)textureFrameAvailable:(int64_t)textureId;
/**
 * Unregisters a `FlutterTexture` that has previously regeistered with `registerTexture:`. Textures
 * must be unregistered on the platform thread.
 *
 * @param textureId The result that was previously returned from `registerTexture:`.
 */
- (void)unregisterTexture:(int64_t)textureId;
@end

NS_ASSUME_NONNULL_END

#endif  // FLUTTER_SHELL_PLATFORM_DARWIN_COMMON_FRAMEWORK_HEADERS_FLUTTERTEXTURE_H_
"""),
    'FlutterMacros.h': ('79b0551d265c52701b378851718ab7b6db7afbecf561b73c2954988554e80f99', r"""// Copyright 2013 The Flutter Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef FLUTTER_SHELL_PLATFORM_DARWIN_COMMON_FRAMEWORK_HEADERS_FLUTTERMACROS_H_
#define FLUTTER_SHELL_PLATFORM_DARWIN_COMMON_FRAMEWORK_HEADERS_FLUTTERMACROS_H_

#if defined(FLUTTER_FRAMEWORK)

#define FLUTTER_DARWIN_EXPORT __attribute__((visibility("default")))

#else  // defined(FLUTTER_SDK)

#define FLUTTER_DARWIN_EXPORT

#endif  // defined(FLUTTER_SDK)

#ifndef NS_ASSUME_NONNULL_BEGIN
#define NS_ASSUME_NONNULL_BEGIN _Pragma("clang assume_nonnull begin")
#define NS_ASSUME_NONNULL_END _Pragma("clang assume_nonnull end")
#endif  // defined(NS_ASSUME_NONNULL_BEGIN)

/**
 * Indicates that the API has been deprecated for the specified reason. Code
 * that uses the deprecated API will continue to work as before. However, the
 * API will soon become unavailable and users are encouraged to immediately take
 * the appropriate action mentioned in the deprecation message and the BREAKING
 * CHANGES section present in the Flutter.h umbrella header.
 */
#define FLUTTER_DEPRECATED(msg) __attribute__((__deprecated__(msg)))

/**
 * Indicates that the previously deprecated API is now unavailable. Code that
 * uses the API will not work and the declaration of the API is only a stub
 * meant to display the given message detailing the actions for the user to take
 * immediately.
 */
#define FLUTTER_UNAVAILABLE(msg) __attribute__((__unavailable__(msg)))

#if __has_feature(objc_arc)
#define FLUTTER_ASSERT_ARC
#define FLUTTER_ASSERT_NOT_ARC #error ARC must be disabled !
#else
#define FLUTTER_ASSERT_ARC #error ARC must be enabled !
#define FLUTTER_ASSERT_NOT_ARC
#endif

#endif  // FLUTTER_SHELL_PLATFORM_DARWIN_COMMON_FRAMEWORK_HEADERS_FLUTTERMACROS_H_
"""),
}


def original_source(repo):
    return {name: subprocess.check_output(["git", "-C", str(repo), "show", "HEAD:" + name]) for name in FILES}


def write_repo(repo, sources):
    for name, data in sources.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def snapshot(repo):
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob("*") if p.is_file()}


def materialization_controls(output, originals, files):
    root = output / "materialization"
    root.mkdir()
    write_repo(root, originals)
    with patch.object(P, "fetch_source", return_value=dict(files)) as fetch:
        P.apply(root)
        assert fetch.call_count == 1
        first = snapshot(root)
        P.apply(root)
        assert fetch.call_count == 1 and snapshot(root) == first
    target = root / "flutter" / P.DEPENDENCY
    assert len(P.existing_files(target)) == 98 + len(P.OWNED)
    checks = 0
    for name in ("src/flutter.rs", "src/flutter_ffi.rs", "flutter/lib/web/bridge.dart", "flutter/lib/models/desktop_render_texture.dart", "flutter/pubspec.yaml"):
        owned = output / f"drift-{checks}"
        write_repo(owned, originals)
        text = (owned / name).read_text()
        if name.endswith("pubspec.yaml"):
            text = text.replace(P.PIN, "0" * 40)
        elif name.endswith("desktop_render_texture.dart"):
            text = text.replace("_textureKey = -1", "_textureKey = -2", 1)
        else:
            text = text.replace("ptr", "different_ptr", 1 if name.endswith("bridge.dart") else 0)
            # Mutate the actual registration block, rather than unrelated source.
            start = "  void sessionRegisterPixelbufferTexture(" if name.endswith("bridge.dart") else ("    fn register_pixelbuffer_texture(" if name.endswith("flutter.rs") else "pub fn session_register_pixelbuffer_texture(")
            a = text.index(start)
            text = text[:a] + text[a:].replace("ptr", "different_ptr", 1)
        (owned / name).write_text(text)
        before = snapshot(owned)
        with patch.object(P, "fetch_source", side_effect=AssertionError("drift fetched")):
            try:
                P.apply(owned)
            except RuntimeError:
                pass
            else:
                raise AssertionError("drift accepted")
        assert snapshot(owned) == before
        checks += 1
    for label, mutation in (
        ("extra-file", lambda d: (d / "unexpected").write_text("unexpected")),
        ("extra-dir", lambda d: (d / "extra").mkdir()),
        ("symlink", lambda d: (d / "linked").symlink_to(d / "pubspec.yaml")),
        ("wrong-content", lambda d: (d / "pubspec.yaml").write_text("wrong")),
        ("frame-drift", lambda d: (d / P.TEXT).write_text((d / P.TEXT).read_text().replace("memcpy(", "changed_copy(", 1))),
        ("old-frame-copy", lambda d: (d / P.TEXT).write_text(P.unpatch_frame((d / P.TEXT).read_text()))),
    ):
        owned = output / label
        shutil.copytree(root, owned)
        mutation(owned / "flutter" / P.DEPENDENCY)
        with patch.object(P, "fetch_source", side_effect=AssertionError("existing content fetched")):
            try:
                P.apply(owned)
            except RuntimeError:
                pass
            else:
                raise AssertionError(label + " accepted")
        checks += 1
    for label in ("destination-symlink", "parent-symlink"):
        owned = output / label
        write_repo(owned, originals)
        parent = owned / "flutter/.mixel-dependencies"
        if label == "parent-symlink":
            parent.symlink_to(target.parent, target_is_directory=True)
        else:
            parent.mkdir()
            (parent / "texture_rgba_renderer").symlink_to(target, target_is_directory=True)
        with patch.object(P, "fetch_source", side_effect=AssertionError("symlink fetched")):
            try:
                P.apply(owned)
            except RuntimeError:
                pass
            else:
                raise AssertionError(label + " accepted")
        checks += 1
    for label in ("callback-drift", "source-symlink", "source-hardlink"):
        owned = output / label
        write_repo(owned, originals)
        source = owned / "src/flutter.rs"
        if label == "callback-drift":
            text = source.read_text()
            a = text.index(P.CALLBACK_START)
            source.write_text(text[:a] + text[a:].replace("write().unwrap()", "read().unwrap()", 1))
        else:
            retained = owned / "retained-source"
            source.rename(retained)
            if label == "source-symlink":
                source.symlink_to(retained)
            else:
                os.link(retained, source)
        before = snapshot(owned)
        with patch.object(P, "fetch_source", side_effect=AssertionError("unsafe source fetched")):
            try:
                P.apply(owned)
            except RuntimeError:
                pass
            else:
                raise AssertionError(label + " accepted")
        assert snapshot(owned) == before
        checks += 1
    owned = output / "fetch-failure"
    write_repo(owned, originals)
    with patch.object(P, "fetch_source", side_effect=RuntimeError("actual bounded failure")):
        try:
            P.apply(owned)
        except RuntimeError:
            pass
        else:
            raise AssertionError("fetch failure accepted")
    assert snapshot(owned) == originals and not (owned / "flutter/.mixel-dependencies").exists()
    with patch.object(P.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "git")) as call, patch.object(P.time, "sleep"):
        try:
            P.fetch_source()
        except RuntimeError:
            pass
        else:
            raise AssertionError("failed network silently accepted")
        assert call.call_count == 3
    print(f"PASS: pinned98 source inventory plus exact three-file public header/base/owned101 materialization/idempotence; {checks + 2} drift/path/fetch refusal controls before source writes")
    return root


def ownership_source_controls(output, files):
    original = files[P.TEXT].decode()
    fixed = P.patch_frame(original)
    assert P.patch_frame(fixed) == fixed and P.unpatch_frame(fixed) == original
    previous = fixed.replace(P.NEW_TEXTURE_BASE, P.OLD_TEXTURE_BASE, 1).replace(P.NEW_PIXEL_COPY, P.OLD_PIXEL_COPY, 1)
    assert P.patch_frame(previous) == fixed and P.unpatch_frame(previous) == original
    inverse = fixed.replace(P.NEW_TEXTURE_BASE, P.OLD_TEXTURE_BASE, 1).replace(P.NEW_PIXEL_COPY, P.OLD_PIXEL_COPY, 1)
    assert inverse == original.replace(P.OLD_FRAME, P.NEW_FRAME, 1).replace(P.OLD_DATA, P.NEW_DATA, 1)
    assert 'Unmanaged.passRetained(data!).toOpaque()' in P.NEW_PIXEL_COPY
    assert P.NEW_PIXEL_COPY.replace('    public override func retainedPixelBufferPointer() -> UnsafeMutableRawPointer? {', '    public func copyPixelBuffer() -> Unmanaged<CVPixelBuffer>? {').replace('.toOpaque()', '') == P.OLD_PIXEL_COPY
    for name, (digest, body) in FLUTTER_HEADERS.items():
        assert P.sha(body.encode()) == digest, 'Exact engine public header changed: ' + name
    assert '- (CVPixelBufferRef _Nullable)copyPixelBuffer;' in FLUTTER_HEADERS['FlutterTexture.h'][1]
    header = P.OWNED['macos/Classes/MixelTextureBase.h']
    implementation = P.OWNED['macos/Classes/MixelTextureBase.m']
    assert '<FlutterMacOS/FlutterTexture.h>' in header and ': NSObject <FlutterTexture>' in header
    assert '- (CVPixelBufferRef)copyPixelBuffer {' in implementation
    assert 'return (CVPixelBufferRef)[self retainedPixelBufferPointer];' in implementation
    assert not any(word in implementation for word in ('CFRetain', 'CFRelease', '@property', '__strong', '__weak'))
    mutations = [original + '\n', fixed.replace('passRetained', 'passUnretained', 1),
                 fixed.replace('queue.sync {', 'queue.async {', 1),
                 fixed.replace(P.NEW_TEXTURE_BASE, P.OLD_TEXTURE_BASE, 1),
                 fixed.replace(P.NEW_PIXEL_COPY, P.OLD_PIXEL_COPY, 1),
                 fixed.replace(P.NEW_PIXEL_COPY, P.NEW_PIXEL_COPY * 2, 1),
                 fixed.replace(P.NEW_TEXTURE_BASE, P.NEW_TEXTURE_BASE * 2, 1),
                 fixed.replace('.toOpaque()', '.toOpaque()!', 1)]
    for changed in mutations:
        try:
            P.patch_frame(changed)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Partial/changed retained-return source accepted')
    api = P.patch_api(files[P.API].decode())
    assert api.count(P.NEW_API_IMPORT) == 1
    assert api.index('#import "MixelTextureBase.h"') < api.index(P.OLD_API_IMPORT)
    assert P.unpatch_api(api) == files[P.API].decode()
    root = output / 'materialization'
    cases = (
        ('missing-base-header', lambda d: (d / 'macos/Classes/MixelTextureBase.h').unlink()),
        ('missing-base-implementation', lambda d: (d / 'macos/Classes/MixelTextureBase.m').unlink()),
        ('base-extra-retain', lambda d: (d / 'macos/Classes/MixelTextureBase.m').write_text(implementation.replace('return (CVPixelBufferRef)', 'return (CVPixelBufferRef)CFRetain', 1))),
        ('base-extra-release', lambda d: (d / 'macos/Classes/MixelTextureBase.m').write_text(implementation + '\nCFRelease(NULL);\n')),
        ('base-changed-header', lambda d: (d / 'macos/Classes/MixelTextureBase.h').write_text(header + '\n')),
        ('base-symlink', lambda d: ((d / 'macos/Classes/MixelTextureBase.m').unlink(), (d / 'macos/Classes/MixelTextureBase.m').symlink_to(root / 'flutter' / P.DEPENDENCY / 'macos/Classes/MixelTextureBase.m'))),
        ('base-hardlink', lambda d: ((d / 'macos/Classes/MixelTextureBase.m').unlink(), os.link(root / 'flutter' / P.DEPENDENCY / 'macos/Classes/MixelTextureBase.m', d / 'macos/Classes/MixelTextureBase.m'))),
        ('base-import-missing', lambda d: (d / P.API).write_text((d / P.API).read_text().replace(P.NEW_API_IMPORT, P.OLD_API_IMPORT, 1))),
        ('base-partial-class', lambda d: (d / P.TEXT).write_text((d / P.TEXT).read_text().replace(P.NEW_TEXTURE_BASE, P.OLD_TEXTURE_BASE, 1))),
        ('base-partial-hook', lambda d: (d / P.TEXT).write_text((d / P.TEXT).read_text().replace(P.NEW_PIXEL_COPY, P.OLD_PIXEL_COPY, 1))),
    )
    for label, mutate in cases:
        case = output / label
        shutil.copytree(root, case)
        target = case / 'flutter' / P.DEPENDENCY
        mutate(target)
        before = snapshot(case)
        modes = {x.relative_to(case).as_posix(): x.stat().st_mode for x in case.rglob('*')}
        with patch.object(P, 'fetch_source', side_effect=AssertionError('Changed bridge fetched')):
            try:
                P.apply(case)
            except RuntimeError:
                pass
            else:
                raise AssertionError(label + ' accepted')
        assert snapshot(case) == before
        assert {x.relative_to(case).as_posix(): x.stat().st_mode for x in case.rglob('*')} == modes
        if label == 'base-hardlink':
            (target / 'macos/Classes/MixelTextureBase.m').unlink()
    old_case = output / 'old-owned98-refuses-bridge-migration'
    shutil.copytree(root, old_case)
    old_target = old_case / 'flutter' / P.DEPENDENCY
    for name in P.OWNED:
        (old_target / name).unlink()
    (old_target / P.TEXT).write_text(previous)
    (old_target / P.API).write_text(api.replace(P.NEW_API_IMPORT, P.OLD_API_IMPORT, 1))
    before = snapshot(old_case)
    modes = {x.relative_to(old_case).as_posix(): x.stat().st_mode for x in old_case.rglob('*')}
    with patch.object(P, 'fetch_source', side_effect=AssertionError('Old bridge profile fetched')):
        try:
            P.apply(old_case)
        except RuntimeError as error:
            assert str(error) == 'Owned renderer retained-return bridge missing or changed; reprovision fresh source from pinned upstream'
        else:
            raise AssertionError('Old owned98 migrated in place')
    assert snapshot(old_case) == before
    assert {x.relative_to(old_case).as_posix(): x.stat().st_mode for x in old_case.rglob('*')} == modes
    (output / 'ownership-source-proof.json').write_text(json.dumps({
        'result': 'passed', 'exact_original98_inventory': P.INVENTORY_SHA,
        'new_owned_base_files': {name: P.sha(body.encode()) for name, body in P.OWNED.items()},
        'original_Swift': P.sha(original.encode()), 'corrected_Swift': P.sha(fixed.encode()),
        'source_drift_refusals': len(mutations), 'owned_path_content_refusals': len(cases),
        'old_owned98_refused_without_writes': True, 'whole_inverse': True,
        'exact_public_headers': {name: digest for name, (digest, _) in FLUTTER_HEADERS.items()},
        'scope': 'Python-only source/materialization checks; no compiler/native/app qualification'
    }, indent=2) + '\n')
    print('PASS: retained-return full inverse/idempotence; eight source and ten owned path/content refusals; old owned98 refuses before writes; exact Flutter protocol/macros pins; serial queue/passRetained/BOOL/row and owner-retirement source conserved')


def public_header_controls(output, files):
    # Swift's real generated Objective-C header imports the module-named public
    # header. CocoaPods' source glob must export it alongside our base header.
    name = 'macos/Classes/texture_rgba_renderer.h'
    assert P.OWNED[name] == '#import "MixelTextureBase.h"\n'
    root = output / 'materialization'
    target = root / 'flutter' / P.DEPENDENCY
    podspec = (target / 'macos/texture_rgba_renderer.podspec').read_text()
    source_globs = re.findall(r"^\s*s\.source_files\s*=\s*'([^']+)'\s*$", podspec, re.M)
    assert source_globs == ['Classes/**/*']
    exported = {p.relative_to(target / 'macos').as_posix()
                for p in (target / 'macos').glob(source_globs[0]) if p.is_file()}
    assert 'Classes/texture_rgba_renderer.h' in exported
    assert 'Classes/MixelTextureBase.h' in exported
    assert (target / name).read_bytes() == P.OWNED[name].encode()
    current = P.existing_files(target)
    original = dict(current)
    for owned_name in P.OWNED:
        original.pop(owned_name)
    original[P.PLUGIN] = P.unpatch_plugin(original[P.PLUGIN].decode()).encode()
    original[P.TEXT] = P.unpatch_frame(original[P.TEXT].decode()).encode()
    original[P.API] = P.unpatch_api(original[P.API].decode()).encode()
    assert len(current) == 101 and len(original) == 98 and original == files
    cases = (
        ('previous-owned100-missing-module-header', lambda p: p.unlink()),
        ('module-header-wrong-import', lambda p: p.write_text('#import "ForeignBase.h"\n')),
        ('module-header-extra-declaration', lambda p: p.write_text(P.OWNED[name] + '@interface Foreign : NSObject @end\n')),
        ('module-header-symlink', lambda p: (p.unlink(), p.symlink_to(target / name))),
        ('module-header-hardlink', lambda p: (p.unlink(), os.link(target / name, p))),
    )
    for label, mutate in cases:
        case = output / label
        shutil.copytree(root, case)
        member = case / 'flutter' / P.DEPENDENCY / name
        mutate(member)
        before = snapshot(case)
        modes = {p.relative_to(case).as_posix(): p.lstat().st_mode for p in case.rglob('*')}
        with patch.object(P, 'fetch_source', side_effect=AssertionError('Incomplete public header fetched')):
            try:
                P.apply(case)
            except RuntimeError:
                pass
            else:
                raise AssertionError(label + ' accepted')
        assert snapshot(case) == before
        assert {p.relative_to(case).as_posix(): p.lstat().st_mode for p in case.rglob('*')} == modes
        if label == 'module-header-hardlink':
            member.unlink()
    (output / 'public-header-source-proof.json').write_text(json.dumps({
        'result': 'passed', 'owned_product_members': len(current),
        'upstream_members_after_exact_inverse': len(original),
        'public_header': name, 'public_header_sha256': P.sha(P.OWNED[name].encode()),
        'actual_podspec_source_glob': source_globs[0],
        'previous_owned100_refused_without_writes': True,
        'header_path_content_refusals': len(cases),
        'scope': 'Python-only exact source/materialization and CocoaPods public-header discovery; actual module compilation remains separate'
    }, indent=2) + '\n')
    print('PASS: module-named public header is exported by the actual podspec glob; whole owned101 inverse recovers exact upstream98; previous owned100 and four changed/unsafe header cases refuse before writes')


def adapter_controls(output, files, *, native=True):
    output = Path(output).resolve(strict=True)
    original = files[P.API].decode()
    corrected = P.patch_api(original)
    assert P.sha(original.encode()) == P.API_SHA
    assert P.patch_api(corrected) == corrected and P.unpatch_api(corrected) == original
    assert corrected.replace(P.NEW_API_CALL, P.OLD_API_CALL, 1).replace(P.NEW_API_IMPORT, P.OLD_API_IMPORT, 1) == original
    for mutation in (original + "\n", original.replace("int len", "long len", 1),
                     original.replace("\n", "\r\n"), corrected.replace("return;", "return 0;", 1),
                     corrected.replace(P.NEW_API_CALL, P.NEW_API_CALL * 2, 1)):
        try:
            P.patch_api(mutation)
        except RuntimeError:
            pass
        else:
            raise AssertionError("C adapter drift accepted")
    # A previous owned profile is never migrated in place. Refusal must precede
    # every source write on both normal and unavailable-dir-fd environments.
    owned = output / "adapter-old-profile-refusal"
    shutil.copytree(output / "materialization", owned)
    target = owned / "flutter" / P.DEPENDENCY
    (target / P.API).write_bytes(files[P.API])
    before = snapshot(owned)
    modes = {p.relative_to(owned).as_posix(): p.stat().st_mode for p in owned.rglob("*")}
    actual_open = Path.open
    def read_only_open(path, mode="r", *args, **kwargs):
        if any(marker in mode for marker in ("w", "a", "x", "+")):
            raise AssertionError("Oldowned98 refusal attempted a source write")
        return actual_open(path, mode, *args, **kwargs)
    for unavailable in (False, True):
        with patch.object(P.os, "supports_dir_fd", set() if unavailable else P.os.supports_dir_fd), patch.object(P.os, "O_NOFOLLOW", None if unavailable else getattr(P.os, "O_NOFOLLOW", None), create=True), patch.object(Path, "open", read_only_open), patch.object(P, "fetch_source", side_effect=AssertionError("old profile fetched")), patch.object(P.os, "replace", side_effect=AssertionError("old profile replaced a file")):
            try:
                P.existing_files(target)
            except RuntimeError as error:
                assert str(error) == "Owned renderer C adapter patch missing; reprovision fresh source from pinned upstream"
            else:
                raise AssertionError("Uncorrected adapter accepted as final")
            try:
                P.apply(owned)
            except RuntimeError as error:
                assert str(error) == "Owned renderer C adapter patch missing; reprovision fresh source from pinned upstream"
            else:
                raise AssertionError("Oldowned98 migrated in place")
        assert snapshot(owned) == before
        assert {p.relative_to(owned).as_posix(): p.stat().st_mode for p in owned.rglob("*")} == modes
        portable = output / ("adapter-fresh-primitives-" + str(unavailable))
        write_repo(portable, {name: (output / "materialization" / name).read_bytes() for name in FILES})
        with patch.object(P.os, "supports_dir_fd", set() if unavailable else P.os.supports_dir_fd), patch.object(P.os, "O_NOFOLLOW", None if unavailable else getattr(P.os, "O_NOFOLLOW", None), create=True), patch.object(P, "fetch_source", return_value=dict(files)) as fetch:
            P.apply(portable)
            portable_before = snapshot(portable)
            P.apply(portable)
            assert fetch.call_count == 1 and snapshot(portable) == portable_before
            assert len(P.existing_files(portable / "flutter" / P.DEPENDENCY)) == 98 + len(P.OWNED)
    for label, mutate in (
        ("adapter-drift", lambda p: p.write_text(p.read_text().replace("int len", "long len", 1))),
        ("adapter-symlink", lambda p: (p.unlink(), p.symlink_to(target / P.API))),
        ("adapter-hardlink", lambda p: (p.unlink(), os.link(target / P.API, p))),
    ):
        case = output / label
        shutil.copytree(output / "materialization", case)
        member = case / "flutter" / P.DEPENDENCY / P.API
        mutate(member)
        before_case = snapshot(case)
        with patch.object(P, "fetch_source", side_effect=AssertionError("unsafe adapter fetched")):
            try:
                P.apply(case)
            except RuntimeError:
                pass
            else:
                raise AssertionError(label + " accepted")
        assert snapshot(case) == before_case
        if label == "adapter-hardlink":
            member.unlink()
    print("PASS: exact C adapter inverse/idempotence/five drift refusals; previous adapter refuses before any write with all bytes/modes preserved under present/absent dir-fd primitives; fresh owned101 materialization/corrected idempotence pass both; adapter drift/symlink/hardlink refuse")
    if sys.platform != "darwin" or not native:
        print("SOURCE SCOPE: actual SDK Objective-C adapter controls require macOS and explicit compiler admission")
        return
    folder = output / "objc-adapter"
    include = folder / "texture_rgba_renderer"
    include.mkdir(parents=True)
    (folder / "MixelTextureBase.h").write_text("// Adapter-only fixture; real base/public headers are exercised below.\n")
    header = include / "texture_rgba_renderer-Swift.h"
    header.write_text(r"""#import <Foundation/Foundation.h>
@interface TextRgba : NSObject
@property(nonatomic) BOOL accepted;
@property(nonatomic) NSInteger calls;
@property(nonatomic) const uint8_t *seenBuffer;
@property(nonatomic) NSInteger seenLen, seenWidth, seenHeight, seenAlignment;
- (BOOL)markFrameAvaliableRawWithBuffer:(const uint8_t *)buffer len:(NSInteger)len width:(NSInteger)width height:(NSInteger)height stride_align:(NSInteger)stride_align __attribute__((warn_unused_result));
@end
""")
    harness = folder / "main.m"
    harness.write_text(r"""#import "texture_rgba_renderer/texture_rgba_renderer-Swift.h"
#include <assert.h>
#include <stdio.h>
@implementation TextRgba
- (BOOL)markFrameAvaliableRawWithBuffer:(const uint8_t *)buffer len:(NSInteger)len width:(NSInteger)width height:(NSInteger)height stride_align:(NSInteger)alignment {
    self.calls++; self.seenBuffer=buffer; self.seenLen=len; self.seenWidth=width;
    self.seenHeight=height; self.seenAlignment=alignment; return self.accepted;
}
@end
extern void FlutterRgbaRendererPluginOnRgba(void *, const uint8_t *, int, int, int, int);
static void (*const originalCAbi)(void *, const uint8_t *, int, int, int, int) = &FlutterRgbaRendererPluginOnRgba;
int main(void) { @autoreleasepool {
    uint8_t bytes[8]={1,2,3,4,5,6,7,8};
    __weak TextRgba *weak;
    @autoreleasepool {
        TextRgba *texture=[TextRgba new]; weak=texture;
        for (int accepted=0; accepted<=1; accepted++) {
            texture.accepted=accepted;
            originalCAbi((__bridge void *)texture, bytes, 8, 2, 1, 1);
            assert(texture.calls==accepted+1 && texture.seenBuffer==bytes);
            assert(texture.seenLen==8 && texture.seenWidth==2 && texture.seenHeight==1 && texture.seenAlignment==1);
        }
        originalCAbi((__bridge void *)texture, bytes, -1, -2, -3, -4);
        assert(texture.calls==3 && texture.seenLen==-1 && texture.seenWidth==-2 && texture.seenHeight==-3 && texture.seenAlignment==-4);
        originalCAbi(NULL, bytes, 8, 2, 1, 1);
    }
    assert(weak==nil);
    puts("PASS: actual Objective-C adapter accepts/rejects once, forwards exact signed C arguments, tolerates nil, and does not retain the renderer; void C ABI links");
} return 0; }
""")
    old = folder / "original.m"; old.write_text(original)
    fixed = folder / "corrected.m"; fixed.write_text(corrected)
    flags = ["xcrun", "clang", "-fobjc-arc", "-Werror=unused-result", "-I", str(folder)]
    negative = subprocess.run(flags + ["-c", str(old), "-o", str(folder / "old.o")], text=True, capture_output=True, timeout=120)
    (folder / "original-unused-result.log").write_text(negative.stdout + negative.stderr)
    assert negative.returncode != 0 and "ignoring return value" in negative.stderr and "warn_unused_result" in negative.stderr
    for architecture, minimum in (("arm64", "12.3"), ("x86_64", "10.14")):
        run(flags + ["-arch", architecture, "-mmacosx-version-min=" + minimum, "-c", str(fixed), "-o", str(folder / (architecture + ".o"))], folder, architecture + "-compile.log")
        run(["xcrun", "clang++", "-x", "objective-c++", "-fobjc-arc", "-Werror=unused-result", "-I", str(folder), "-arch", architecture, "-mmacosx-version-min=" + minimum, "-c", str(fixed), "-o", str(folder / (architecture + "-cpp.o"))], folder, architecture + "-cpp-compile.log")
    for object_path in sorted(folder.glob("*.o")):
        symbols = run(["nm", "-g", str(object_path)], folder, object_path.stem + "-symbols.log")
        assert symbols.count(" _FlutterRgbaRendererPluginOnRgba") == 1
        assert "__Z" not in symbols
    executable = folder / "adapter-control"
    run(flags + [str(fixed), str(harness), "-framework", "Foundation", "-o", str(executable)], folder, "link.log")
    print(run([str(executable)], folder, "runtime.log").strip())
    print("PASS: original adapter reproduces unused-result error; corrected adapter compiles against Apple SDK in Objective-C and Objective-C++ for arm64/macOS12.3 and x86_64/macOS10.14")
    # The actual pinned public protocol and macro bytes, with a tiny module map,
    # exercise the real ObjC base. This is not a full Flutter framework build.
    real = folder / "actual-swift"
    (real / "texture_rgba_renderer").mkdir(parents=True)
    public = real / "FlutterMacOS"
    public.mkdir()
    for name, (digest, body) in FLUTTER_HEADERS.items():
        assert P.sha(body.encode()) == digest
        (public / name).write_text(body)
    (public / "module.modulemap").write_text('module FlutterMacOS { header "FlutterTexture.h" export * }\n')
    for name, body in P.OWNED.items():
        (real / Path(name).name).write_text(body)
    registry_header = real / "ActualRegistry.h"
    registry_header.write_text(r"""#import <FlutterMacOS/FlutterTexture.h>
@interface MixelTestTextureRegistry : NSObject <FlutterTextureRegistry>
@property(nonatomic) NSInteger frames;
@end
""")
    registry_impl = real / "ActualRegistry.m"
    registry_impl.write_text(r"""#import "ActualRegistry.h"
#include <assert.h>
@implementation MixelTestTextureRegistry
- (int64_t)registerTexture:(NSObject<FlutterTexture>*)texture { assert(texture); return 1; }
- (void)textureFrameAvailable:(int64_t)textureId { assert(textureId == 1); self.frames++; }
- (void)unregisterTexture:(int64_t)textureId { assert(textureId == 1); }
@end
""")
    bridge = real / "adapter.h"
    bridge.write_text('#import "MixelTextureBase.h"\n#import "ActualRegistry.h"\n#include <stdint.h>\nvoid FlutterRgbaRendererPluginOnRgba(void *, const uint8_t *, int, int, int, int);\n')
    real_flags = ["xcrun", "clang", "-fobjc-arc", "-fmodules", "-Werror=unused-result", "-Werror=ignored-attributes", "-I", str(real)]
    base_object = real / "base.o"
    registry_object = real / "registry.o"
    run(real_flags + ["-c", str(real / "MixelTextureBase.m"), "-o", str(base_object)], real, "actual-base-compile.log")
    run(real_flags + ["-c", str(registry_impl), "-o", str(registry_object)], real, "actual-registry-compile.log")
    text = P.patch_frame(files[P.TEXT].decode())
    swift = real / "TextRgba.swift"
    swift.write_text(text)
    generated_header = real / "texture_rgba_renderer/texture_rgba_renderer-Swift.h"
    run(["xcrun", "swiftc", "-I", str(real), "-import-objc-header", str(bridge), "-emit-library", "-emit-module", "-module-name", "texture_rgba_renderer",
         "-emit-objc-header", "-emit-objc-header-path", str(generated_header), str(swift), str(base_object), str(registry_object),
         "-o", str(real / "libtexture_rgba_renderer.dylib")], real, "swift-header.log")
    generated = generated_header.read_text()
    assert "markFrameAvaliableRawWithBuffer:" in generated and "SWIFT_WARN_UNUSED_RESULT" in generated
    assert "retainedPixelBufferPointer" in generated and "CVPixelBufferRef _Nullable __unsafe_unretained" not in generated
    # Keep the negative adapter gate independent of generated-header imports.
    negative = subprocess.run(real_flags + ["-include", str(real / "MixelTextureBase.h"), "-c", str(old), "-o", str(real / "old.o")], text=True, capture_output=True, timeout=120)
    (real / "original-unused-result.log").write_text(negative.stdout + negative.stderr)
    assert negative.returncode != 0 and "ignoring return value" in negative.stderr
    object_path = real / "adapter.o"
    run(real_flags + ["-c", str(fixed), "-o", str(object_path)], real, "actual-generated-header-compile.log")
    # Original current Swift ABI reproduces the ownership warning; candidate
    # generated headers must compile for both supported targets in ObjC/ObjC++.
    original_swift = real / "OriginalTextRgba.swift"
    original_swift.write_text(text.replace(P.NEW_TEXTURE_BASE, P.OLD_TEXTURE_BASE, 1).replace(P.NEW_PIXEL_COPY, P.OLD_PIXEL_COPY, 1))
    for architecture, minimum in (("arm64", "12.3"), ("x86_64", "10.14")):
        target = architecture + "-apple-macosx" + minimum
        run(real_flags + ["-target", target, "-c", str(real / "MixelTextureBase.m"), "-o", str(real / (architecture + "-base.o"))], real, architecture + "-actual-base-compile.log")
        for label, source in (("original", original_swift), ("corrected", swift)):
            emitted = real / (architecture + "-" + label + ".h")
            run(["xcrun", "swiftc", "-I", str(real), "-import-objc-header", str(bridge), "-target", target,
                 "-parse-as-library", "-emit-module", "-emit-module-path", str(real / (architecture + "-" + label + ".swiftmodule")),
                 "-emit-objc-header", "-emit-objc-header-path", str(emitted), str(source)], real, architecture + "-" + label + "-emit.log")
            include = real / (architecture + "-" + label + "-include.m")
            include.write_text('#import "MixelTextureBase.h"\n#import "' + emitted.name + '"\n')
            for language in ("objective-c", "objective-c++"):
                command = real_flags + ["-target", target, "-x", language, "-fsyntax-only", str(include)]
                result = subprocess.run(command, capture_output=True, text=True, timeout=120)
                log = architecture + "-" + label + "-" + language + "-header.log"
                (real / log).write_text(result.stdout + result.stderr)
                if label == "original":
                    assert result.returncode != 0 and "objc_ownership" in result.stderr and "ignored-attributes" in result.stderr
                else:
                    assert result.returncode == 0, result.stdout + result.stderr
    main = real / "main.swift"
    main.write_text(r"""import Foundation
import CoreVideo
let registry=MixelTestTextureRegistry()
weak var weakTexture: TextRgba?
let bytes:[UInt8]=[17,29,41,53]
// Scope every temporary/ObjC receiver inside the drained pool; only the
// independently retained pixel buffer crosses the owner-retirement boundary.
let next: CVPixelBuffer=autoreleasepool {
    let texture=TextRgba.new(registry:registry)
    weakTexture=texture
    let ptr=Unmanaged.passUnretained(texture).toOpaque()
    func submit(_ length: Int32) {
        bytes.withUnsafeBufferPointer { FlutterRgbaRendererPluginOnRgba(ptr,$0.baseAddress!,length,1,1,1) }
    }
    submit(4)
    precondition(registry.frames==1)
    let first=texture.copyPixelBuffer()!.takeRetainedValue()
    precondition(CVPixelBufferLockBaseAddress(first,[])==kCVReturnSuccess)
    let pixel=CVPixelBufferGetBaseAddress(first)!.assumingMemoryBound(to:UInt8.self)
    precondition(Array(UnsafeBufferPointer(start:pixel,count:4))==bytes)
    precondition(CVPixelBufferUnlockBaseAddress(first,[])==kCVReturnSuccess)
    submit(3)
    let afterRejected=texture.copyPixelBuffer()!.takeRetainedValue()
    precondition(first===afterRejected && registry.frames==1)
    submit(4)
    precondition(registry.frames==2)
    let next=texture.copyPixelBuffer()!.takeRetainedValue()
    precondition(first !== next)
    return next
}
precondition(weakTexture==nil)
precondition(CVPixelBufferLockBaseAddress(next,[])==kCVReturnSuccess)
precondition(CVPixelBufferGetBaseAddress(next)!.load(as:UInt8.self)==17)
precondition(CVPixelBufferUnlockBaseAddress(next,[])==kCVReturnSuccess)
// Deliberately retain a different owner: the same weak-nil retirement check
// must detect that persistent reference, then pass only after its release.
var retainedOwner: TextRgba?
weak var weakRetainedOwner: TextRgba?
autoreleasepool {
    let owner=TextRgba.new(registry:nil)
    retainedOwner=owner
    weakRetainedOwner=owner
}
let retainedOwnerLooksRetired=autoreleasepool { weakRetainedOwner==nil }
precondition(!retainedOwnerLooksRetired)
withExtendedLifetime(retainedOwner) {}
autoreleasepool { retainedOwner=nil }
precondition(weakRetainedOwner==nil)
print("PASS: actual public Flutter protocol + ObjC retained-return base + C adapter/generated Swift/CoreVideo accepts valid frame, preserves retained buffer on rejection, survives drained owner retirement, detects a deliberately retained owner and consumes each returned +1 once")
""")
    executable = real / "actual-adapter-swift-control"
    run(["xcrun", "swiftc", "-I", str(real), "-import-objc-header", str(bridge), str(swift), str(main), str(object_path), str(base_object), str(registry_object),
         "-o", str(executable)], real, "actual-swift-link.log")
    print(run([str(executable)], real, "actual-swift-runtime.log").strip())
    print("PASS: exact original Swift-generated ownership diagnostic reproduced; corrected public-header bridge compiles ObjC/ObjC++ for both targets (other retained SDK warnings remain separate)")


SWIFT_STUBS = r'''
import Foundation
import CoreVideo
public protocol FlutterTexture { func copyPixelBuffer() -> Unmanaged<CVPixelBuffer>? }
// Swift-only lifetime fixture; the real Objective-C bridge/public headers are
// tested separately. Preserve its exact one-retain transfer semantics here.
public class MixelTextureBase: NSObject, FlutterTexture {
    public func retainedPixelBufferPointer() -> UnsafeMutableRawPointer? { nil }
    public func copyPixelBuffer() -> Unmanaged<CVPixelBuffer>? {
        guard let pointer = retainedPixelBufferPointer() else { return nil }
        return Unmanaged<CVPixelBuffer>.fromOpaque(pointer)
    }
}
public protocol FlutterTextureRegistry {
    func register(_ texture: FlutterTexture) -> Int64
    func unregisterTexture(_ id: Int64)
    func textureFrameAvailable(_ id: Int64)
}
public protocol FlutterPlugin {}
public typealias FlutterResult = (Any?) -> Void
public class FlutterMethodCall {
    let method: String; let arguments: Any?
    init(_ method: String, _ arguments: Any?) { self.method = method; self.arguments = arguments }
}
public class FlutterStandardTypedData { let data: Data; init(_ data: Data) { self.data = data } }
public class FlutterMethodChannel { init(name: String, binaryMessenger: Any) {} }
public class FlutterPluginRegistrar {
    public var messenger: Any { return 0 }
    public var textures: FlutterTextureRegistry { fatalError("not used") }
    public func addMethodCallDelegate(_ instance: FlutterPlugin, channel: FlutterMethodChannel) {}
}
let FlutterMethodNotImplemented = "unused"
final class WeakTexture { weak var value: TextRgba?; init(_ value: TextRgba) { self.value = value } }
final class Registry: FlutterTextureRegistry {
    var refs: [WeakTexture] = []; var unregistered: [Int64] = []; var frames = 0; var failRegister = false
    func register(_ texture: FlutterTexture) -> Int64 {
        refs.append(WeakTexture(texture as! TextRgba)); return failRegister ? 0 : Int64(refs.count)
    }
    func unregisterTexture(_ id: Int64) {
        precondition(refs[Int(id) - 1].value != nil, "local close owner must survive unregister")
        unregistered.append(id)
    }
    func textureFrameAvailable(_ id: Int64) { frames += 1 }
}
func call(_ plugin: TextureRgbaRendererPlugin, _ method: String, _ key: Int64) -> Any? {
    var value: Any?; plugin.handle(FlutterMethodCall(method, ["key": key])) { value = $0 }; return value
}
func exercise(_ plugin: TextureRgbaRendererPlugin, _ key: Int64) {
    precondition((call(plugin, "createTexture", key) as? Int64) != -1)
    let ptr = call(plugin, "getTexturePtr", key) as! UInt
    let texture = Unmanaged<TextRgba>.fromOpaque(UnsafeRawPointer(bitPattern: ptr)!).takeUnretainedValue()
    let bytes = [UInt8](repeating: 17, count: 64 * 16)
    let accepted = bytes.withUnsafeBufferPointer {
        texture.markFrameAvaliableRaw(buffer: $0.baseAddress!, len: $0.count, width: 16, height: 16, stride_align: 64)
    }
    precondition(accepted, "real CoreVideo frame must be accepted")
    precondition((call(plugin, "closeTexture", key) as? Bool) == true)
}
func verifyRows(_ texture: TextRgba, _ width: Int, _ height: Int, _ alignment: Int) -> Bool {
    let stride = (width * 4 + alignment - 1) & ~(alignment - 1)
    var bytes = [UInt8](repeating: 0, count: stride * height)
    for row in 0..<height { for column in 0..<(width * 4) { bytes[row * stride + column] = UInt8((row * 29 + column) % 251) } }
    let accepted = bytes.withUnsafeBufferPointer { texture.markFrameAvaliableRaw(buffer: $0.baseAddress!, len: $0.count, width: width, height: height, stride_align: alignment) }
    precondition(accepted)
    let pixel = texture.copyPixelBuffer()!.takeRetainedValue()
    precondition(CVPixelBufferLockBaseAddress(pixel, []) == kCVReturnSuccess)
    defer { CVPixelBufferUnlockBaseAddress(pixel, []) }
    let destination = CVPixelBufferGetBaseAddress(pixel)!.assumingMemoryBound(to: UInt8.self)
    let destinationStride = CVPixelBufferGetBytesPerRow(pixel)
    for row in 0..<height { for column in 0..<(width * 4) {
        if destination[row * destinationStride + column] != bytes[row * stride + column] { return false }
    } }
    for row in 0..<height { for column in (width * 4)..<destinationStride {
        if destination[row * destinationStride + column] != 0 { return false }
    } }
    return true
}
'''


def swift_controls(output, files):
    if sys.platform != "darwin":
        print("SOURCE SCOPE: actual CoreVideo controls require macOS; portable source/Rust/Dart controls continue")
        return
    text = files["macos/Classes/TextRgba.swift"].decode()
    original = files[P.PLUGIN].decode()
    assert text.count("import FlutterMacOS\n") == original.count("import FlutterMacOS\n") == 1
    old_tail = r'''
let registry = Registry(); var plugin: TextureRgbaRendererPlugin? = TextureRgbaRendererPlugin()
plugin!.setTextureRegistry(registry: registry)
for key in Int64(1)...Int64(3) { exercise(plugin!, key) }
precondition(registry.refs.filter { $0.value != nil }.count == 3)
var buffers = 0
for ref in registry.refs { if ref.value?.copyPixelBuffer()?.takeRetainedValue() != nil { buffers += 1 } }
precondition(buffers == 3 && registry.frames == 3 && registry.unregistered.count == 3)
for key in Int64(1)...Int64(3) { precondition((call(plugin!, "getTexturePtr", key) as? UInt) != 0) }
registry.failRegister = true
precondition((call(plugin!, "createTexture", 9) as? Int64) == 0)
precondition((call(plugin!, "getTexturePtr", 9) as? UInt) != 0)
plugin = nil; precondition(registry.refs.allSatisfy { $0.value == nil })
print("PASS: original pinned close retains3 objects and3 real CoreVideo buffers until plugin teardown")
print("PASS: original pinned registration failure0 still publishes a native pointer")
let rowTexture = TextRgba.new(registry: Registry())
precondition(!verifyRows(rowTexture, 17, 3, 1))
precondition(verifyRows(rowTexture, 17, 3, 64))
print("PASS: exact original packed align1 multi-display rows are corrupted; align64 control preserves every pixel")
'''
    new_tail = r'''
let registry = Registry(); let plugin = TextureRgbaRendererPlugin(); plugin.setTextureRegistry(registry: registry)
for key in Int64(1)...Int64(3) { exercise(plugin, key) }
precondition(registry.refs.allSatisfy { $0.value == nil })
precondition(registry.frames == 3 && registry.unregistered.count == 3)
for key in Int64(1)...Int64(3) {
    precondition((call(plugin, "getTexturePtr", key) as? Int) == 0)
    precondition((call(plugin, "closeTexture", key) as? Bool) == false)
}
exercise(plugin, 1)
precondition(registry.refs.allSatisfy { $0.value == nil } && registry.unregistered.count == 4)
precondition((call(plugin, "closeTexture", 999) as? Bool) == false)
registry.failRegister = true
precondition((call(plugin, "createTexture", 5) as? Int64) == 0)
precondition((call(plugin, "closeTexture", 5) as? Bool) == false)
precondition(registry.refs.allSatisfy { $0.value == nil } && registry.unregistered.count == 4)
precondition((call(plugin, "getTexturePtr", 5) as? Int) == 0)
let noRegistry = TextureRgbaRendererPlugin()
precondition((call(noRegistry, "createTexture", 6) as? Int64) == -1)
precondition((call(noRegistry, "closeTexture", 6) as? Bool) == false)
precondition((call(noRegistry, "getTexturePtr", 6) as? Int) == 0)
print("PASS: corrected pinned CoreVideo lifetime: distinct keys release immediately; closed pointers absent; repeat close, same-key recreate, unknown and failed registry safe; strong local owner survives unregister")
let rowTexture = TextRgba.new(registry: Registry())
var rows = 0
for width in [1,2,15,16,17,31,32,63,64,65,127,128,129,1919,1920,1921,3840] {
    for height in [1,2,7] { for alignment in [1,64] {
        precondition(verifyRows(rowTexture, width, height, alignment)); rows += 1
    } }
}
precondition(!rowTexture.markFrameAvaliable(data: Data(), width: 1, height: 1, stride_align: 1))
precondition(verifyRows(rowTexture, 17, 3, 1))
print("PASS: corrected real CoreVideo \(rows) packed/padded row cases preserve every visible pixel and zero destination padding; empty Data refused then valid frame recovers")
'''
    for name, source, tail in (("original", original, old_tail), ("fixed", P.patch_plugin(original), new_tail)):
        frame_source = P.patch_frame(text) if name == "fixed" else text
        prefix = SWIFT_STUBS + frame_source.replace("import FlutterMacOS\n", "", 1)
        fixture = output / (name + "-texture-lifetime.swift")
        fixture.write_text(prefix + source.replace("import FlutterMacOS\n", "", 1) + tail)
        for architecture, target, launcher in (("arm64", "arm64-apple-macosx12.3", []),
                                                ("x86_64", "x86_64-apple-macosx10.14", ["arch", "-x86_64"])):
            binary = output / (name + "-texture-lifetime-" + architecture)
            run(["swiftc", "-O", "-target", target, str(fixture), "-o", str(binary)], output, name + "-" + architecture + "-swift-compile.log")
            print(run([*launcher, str(binary)], output, name + "-" + architecture + "-swift-output.log").strip())
    swift_failure_controls(output, text, original)


CV_FAILURE_PROVIDER = r'''
enum Fault: CaseIterable { case none, createError, createNil, lockError, baseNil, shortRow, shortCapacity, strideOverflow, unlockError }
var fault = Fault.none
var allocations = 0
func testedCreate(_ allocator: CFAllocator?, _ width: Int, _ height: Int, _ format: OSType, _ attributes: CFDictionary?, _ output: UnsafeMutablePointer<CVPixelBuffer?>) -> CVReturn {
    allocations += 1
    if fault == .createError { output.pointee = nil; return kCVReturnError }
    if fault == .createNil { output.pointee = nil; return kCVReturnSuccess }
    return CVPixelBufferCreate(allocator, width, height, format, attributes, output)
}
func testedLock(_ buffer: CVPixelBuffer, _ flags: CVPixelBufferLockFlags) -> CVReturn {
    if fault == .lockError { return kCVReturnError }
    return CVPixelBufferLockBaseAddress(buffer, flags)
}
func testedUnlock(_ buffer: CVPixelBuffer, _ flags: CVPixelBufferLockFlags) -> CVReturn {
    let result = CVPixelBufferUnlockBaseAddress(buffer, flags)
    return fault == .unlockError ? kCVReturnError : result
}
func testedBase(_ buffer: CVPixelBuffer) -> UnsafeMutableRawPointer? { return fault == .baseNil ? nil : CVPixelBufferGetBaseAddress(buffer) }
func testedRow(_ buffer: CVPixelBuffer) -> Int { return fault == .shortRow ? 1 : (fault == .strideOverflow ? Int.max : CVPixelBufferGetBytesPerRow(buffer)) }
func testedCapacity(_ buffer: CVPixelBuffer) -> Int { return fault == .shortCapacity ? 0 : CVPixelBufferGetDataSize(buffer) }
'''
CV_FAILURE_TEST = r'''
let registry=Registry();let texture=TextRgba.new(registry:registry)
precondition(verifyRows(texture,17,3,1))
let bytes=[UInt8](repeating:41,count:204)
for failure in Fault.allCases where failure != .none {
    let previous=texture.copyPixelBuffer()!.takeRetainedValue();let frames=registry.frames
    fault=failure
    let accepted=bytes.withUnsafeBufferPointer { texture.markFrameAvaliableRaw(buffer:$0.baseAddress!,len:$0.count,width:17,height:3,stride_align:1) }
    precondition(!accepted && registry.frames==frames)
    precondition(previous === texture.copyPixelBuffer()!.takeRetainedValue())
    fault = .none;precondition(verifyRows(texture,17,3,1))
}
let shapes:[(Int,Int,Int,Int)]=[(0,3,1,204),(-1,3,1,204),(17,0,1,204),(17,-1,1,204),(17,3,0,204),(17,3,-1,204),(17,3,3,204),(Int.max,3,1,204),(Int.max/4,1,8,204),(1,1,Int.max,204),(17,Int.max,1,204),(17,3,1,203),(17,3,1,205),(17,3,1,-1),(1,1,1<<62,4)]
for (width,height,alignment,length) in shapes {
    let before=allocations;let previous=texture.copyPixelBuffer()!.takeRetainedValue();let frames=registry.frames
    let accepted=bytes.withUnsafeBufferPointer { texture.markFrameAvaliableRaw(buffer:$0.baseAddress!,len:length,width:width,height:height,stride_align:alignment) }
    precondition(!accepted && allocations==before && registry.frames==frames)
    precondition(previous === texture.copyPixelBuffer()!.takeRetainedValue())
}
precondition(!texture.markFrameAvaliable(data:Data(),width:1,height:1,stride_align:1))
precondition(verifyRows(texture,17,3,1))
print("PASS: actual fixed frame body: eight controlled API failure cases and15 malformed/overflow input cases preserve prior frame/no notification; empty Data refused; every valid retry succeeds (API failures injected, not observed host OS failures)")
'''


def swift_failure_controls(output, text, plugin):
    frame = P.patch_frame(text)
    for original, controlled in (("CVPixelBufferCreate", "testedCreate"),
                                  ("CVPixelBufferLockBaseAddress", "testedLock"),
                                  ("CVPixelBufferUnlockBaseAddress", "testedUnlock"),
                                  ("CVPixelBufferGetBaseAddress", "testedBase"),
                                  ("CVPixelBufferGetBytesPerRow", "testedRow"),
                                  ("CVPixelBufferGetDataSize", "testedCapacity")):
        frame = frame.replace(original, controlled)
    fixture = output / "actual-frame-failure-controls.swift"
    fixture.write_text(SWIFT_STUBS + CV_FAILURE_PROVIDER + frame.replace("import FlutterMacOS\n", "", 1)
                       + P.patch_plugin(plugin).replace("import FlutterMacOS\n", "", 1) + CV_FAILURE_TEST)
    for architecture, target, launcher in (("arm64", "arm64-apple-macosx12.3", []),
                                          ("x86_64", "x86_64-apple-macosx10.14", ["arch", "-x86_64"])):
        binary = output / ("actual-frame-failures-" + architecture)
        run(["swiftc", "-O", "-target", target, str(fixture), "-o", str(binary)], output, architecture + "-frame-failures-compile.log")
        print(run([*launcher, str(binary)], output, architecture + "-frame-failures-output.log").strip())


RUST_PREFIX = r'''
extern crate self as log;
#[macro_export] macro_rules! error { ($($x:tt)*) => {{}} }
use std::{collections::HashMap,sync::{Arc,RwLock,Mutex,Condvar,atomic::{AtomicUsize,Ordering}},thread,time::Duration};
type TextureRgbaPtr = usize;
#[derive(PartialEq)] enum RenderType { PixelBuffer }
struct DisplaySessionInfo { texture_rgba_ptr: usize, size: (usize,usize), notify_render_type: Option<RenderType>, #[cfg(feature="vram")] gpu_output_ptr: usize }
mod scrap { pub struct ImageRgb { pub raw: Vec<u8>, pub w: usize, pub h: usize } impl ImageRgb { pub fn align(&self)->usize {64} } }
struct VideoRenderer { map_display_sessions: RwLock<HashMap<usize,DisplaySessionInfo>>, is_support_multi_ui_session: bool, on_rgba_func: Option<unsafe extern "C" fn(usize,*const u8,i32,i32,i32,i32)> }
static CALLS: AtomicUsize = AtomicUsize::new(0);
static GATE: (Mutex<(bool,bool)>,Condvar) = (Mutex::new((false,false)),Condvar::new());
unsafe extern "C" fn frame(_:usize,_:*const u8,_:i32,_:i32,_:i32,_:i32) {
 CALLS.fetch_add(1,Ordering::SeqCst); let mut g=GATE.0.lock().unwrap();g.0=true;GATE.1.notify_all();while !g.1 {g=GATE.1.wait(g).unwrap();}
}
fn info(ptr:usize)->DisplaySessionInfo { DisplaySessionInfo { texture_rgba_ptr:ptr,size:(1,1),notify_render_type:None,#[cfg(feature="vram")] gpu_output_ptr:0 } }
'''
RUST_TEST = r'''
fn main() {
 let renderer=Arc::new(VideoRenderer {map_display_sessions:RwLock::new(HashMap::from([(0,info(111))])),is_support_multi_ui_session:true,on_rgba_func:Some(frame)});
 assert!(!renderer.unregister_pixelbuffer_texture_if_current(0,0));
 assert!(!renderer.unregister_pixelbuffer_texture_if_current(9,111));
 assert!(!renderer.unregister_pixelbuffer_texture_if_current(0,222));
 assert_eq!(renderer.map_display_sessions.read().unwrap()[&0].texture_rgba_ptr,111);
 let r=renderer.clone();let callback=thread::spawn(move||r.on_rgba(0,&scrap::ImageRgb{raw:vec![0;64],w:1,h:1}));
 {let mut g=GATE.0.lock().unwrap();while !g.0 {g=GATE.1.wait(g).unwrap();}}
 let (tx,rx)=std::sync::mpsc::channel();let r=renderer.clone();let removal=thread::spawn(move||tx.send(r.unregister_pixelbuffer_texture_if_current(0,111)).unwrap());
 assert!(matches!(rx.recv_timeout(Duration::from_millis(100)),Err(std::sync::mpsc::RecvTimeoutError::Timeout)));
 {let mut g=GATE.0.lock().unwrap();g.1=true;GATE.1.notify_all();}
 assert!(callback.join().unwrap());assert!(rx.recv_timeout(Duration::from_secs(2)).unwrap());removal.join().unwrap();
 assert!(!renderer.on_rgba(0,&scrap::ImageRgb{raw:vec![0;64],w:1,h:1}));assert_eq!(CALLS.load(Ordering::SeqCst),1);
 assert!(!renderer.unregister_pixelbuffer_texture_if_current(0,111));
 renderer.map_display_sessions.write().unwrap().insert(0,info(222));
 assert!(!renderer.unregister_pixelbuffer_texture_if_current(0,111));assert_eq!(renderer.map_display_sessions.read().unwrap()[&0].texture_rgba_ptr,222);
 assert!(renderer.unregister_pixelbuffer_texture_if_current(0,222));
 #[cfg(feature="vram")] {let mut x=info(333);x.gpu_output_ptr=444;renderer.map_display_sessions.write().unwrap().insert(0,x);assert!(renderer.unregister_pixelbuffer_texture_if_current(0,333));let r=renderer.map_display_sessions.read().unwrap();assert_eq!(r[&0].texture_rgba_ptr,0);assert_eq!(r[&0].gpu_output_ptr,444);}
 println!("PASS: actual source frame callback lock excludes retirement; empty/zero/wrong-pointer/repeat/handoff replacement and GPU-preservation controls");
}
'''


def rust_controls(output, generated):
    text = (generated / "src/flutter.rs").read_text()
    start = text.index(P.CALLBACK_START)
    end = text.index(P.CALLBACK_END, start)
    callback = text[start:end]
    method = P.METHOD.replace('    #[cfg(target_os = "macos")]\n', "")
    fixture = output / "actual-frame-lock.rs"
    fixture.write_text(RUST_PREFIX + "impl VideoRenderer {" + method + callback + "}\n" + RUST_TEST)
    rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
    if not rustc:
        raise RuntimeError("Rust compiler required for actual frame-lock controls")
    for mode, flags in (("normal", []), ("vram", ["--cfg", 'feature="vram"'])):
        binary = output / ("actual-frame-lock-" + mode + (".exe" if os.name == "nt" else ""))
        run([rustc, "--edition=2021", *flags, str(fixture), "-o", str(binary)], output, mode + "-rust-compile.log")
        print(run([str(binary)], output, mode + "-rust-output.log").strip())


DART_STUBS = r'''
import 'dart:async';
bool isMacOS = true;
void debugPrint(String s) {}
class SessionID { final String value; SessionID(this.value); }
class TextureModel { final List<int> ids=[];void setRgbaTextureId({required int display,required int id}) {ids.add(id);} }
class FFI { final id='test'; final textureModel=TextureModel(); }
class Binding {
 int key=0;int? native;int retire=0;int registrations=0;bool failRetire=false;
 int getNextTextureKey()=>++key;
 bool sessionUnregisterPixelbufferTextureIfCurrent({required SessionID sessionId,required int display,required int ptr}) {
  retire++;if(failRetire)throw StateError('unknown retirement');if(ptr==0||native!=ptr)return false;native=null;return true;
 }
}
final bind=Binding();
class Platform {
 void registerPixelbufferTexture(SessionID s,int d,int ptr) {bind.registrations++;bind.native=ptr==0?null:ptr;}
}
final platformFFI=Platform();
class TextureRgbaRenderer {
 final Map<int,int> pointers={}; final List<int> closed=[];
 Completer<int>? createPending;Completer<int>? ptrPending;Completer<bool>? closePending;
 int? failedId;int? fixedPtr;
 Future<int> createTexture(int key) async {
  if(createPending!=null) {await createPending!.future;}
  pointers[key]=fixedPtr??key+100;return failedId??key;
 }
 Future<int> getTexturePtr(int key) async {if(ptrPending!=null)return ptrPending!.future;return pointers[key]??0;}
 Future<bool> closeTexture(int key) async {closed.add(key);if(closePending!=null)await closePending!.future;return pointers.remove(key)!=null;}
}
void check(bool value,String message) {if(!value)throw StateError(message);}
Future<void> flush() async {for(var i=0;i<8;i++){await Future<void>.delayed(Duration.zero);}}
'''
DART_TEST = r'''
Future<void> main() async {
 final ffi=FFI();final session=SessionID('same-session');
 var texture=_PixelbufferTexture();texture.create(0,session,ffi);await flush();check(bind.native!=null,'created pointer registered');
 await texture.destroy(false,ffi);check(bind.native==null&&texture.textureRenderer.pointers.isEmpty,'migration retires original');
 final retire=bind.retire;await texture.destroy(false,ffi);check(bind.retire==retire,'repeat close no second retirement');
 texture=_PixelbufferTexture();texture.create(0,session,ffi);await flush();bind.native=999;
 await texture.destroy(false,ffi);check(bind.native==999,'same-session newer pointer survives');
 texture=_PixelbufferTexture();texture.textureRenderer.createPending=Completer<int>();texture.create(0,session,ffi);final lateKey=texture._textureKey;
 await texture.destroy(false,ffi);final registrations=bind.registrations;texture.textureRenderer.createPending!.complete(1);await flush();
 check(!texture.textureRenderer.pointers.containsKey(lateKey)&&bind.registrations==registrations,'late create closes exact old generation without registration');
 texture=_PixelbufferTexture();texture.textureRenderer.ptrPending=Completer<int>();texture.create(0,session,ffi);await flush();
 await texture.destroy(false,ffi);final before=bind.registrations;texture.textureRenderer.ptrPending!.complete(777);await flush();
 check(bind.registrations==before&&texture.textureRenderer.pointers.isEmpty,'late getPtr cannot re-register');
 texture=_PixelbufferTexture();texture.create(0,session,ffi);await flush();final first=texture._textureKey;
 texture.textureRenderer.closePending=Completer<bool>();final closing=texture.destroy(false,ffi);texture.create(0,session,ffi);await flush();final second=texture._textureKey;
 check(second!=first&&bind.native==second+100,'new generation registers independently');
 texture.textureRenderer.closePending!.complete(true);await closing;await flush();
 check(texture._textureKey==second&&bind.native==second+100,'old finally preserves new generation');texture.textureRenderer.closePending=null;await texture.destroy(false,ffi);
 check(bind.native==null&&texture.textureRenderer.pointers.isEmpty,'new generation can close');
 for(final failedId in [0,-1]) {
  texture=_PixelbufferTexture();texture.textureRenderer.failedId=failedId;final failed=bind.registrations;final published=ffi.textureModel.ids.length;texture.create(0,session,ffi);await flush();
  check(bind.registrations==failed&&ffi.textureModel.ids.length==published&&texture.textureRenderer.pointers.isEmpty,'failed registry must not publish UI or native texture');
  await texture.destroy(false,ffi);check(texture.textureRenderer.pointers.isEmpty,'failed registry disposal safe');
 }
 texture=_PixelbufferTexture();texture.create(0,session,ffi);await flush();final failedKey=texture._textureKey;final retained=bind.native;bind.failRetire=true;
 var refused=false;try{await texture.destroy(false,ffi);}on StateError{refused=true;}
 check(refused&&texture.textureRenderer.pointers.containsKey(failedKey)&&bind.native==retained,'unknown retirement cannot free native pointer');
 bind.failRetire=false;await texture.destroy(false,ffi);check(bind.native==null&&texture.textureRenderer.pointers.isEmpty,'known retry retires safely');
 texture=_PixelbufferTexture();texture.textureRenderer.fixedPtr=321;texture.create(0,session,ffi);await flush();await texture.destroy(false,ffi);
 final reused=_PixelbufferTexture();reused.textureRenderer.fixedPtr=321;reused.create(0,session,ffi);await flush();final reusedRetire=bind.retire;
 await texture.destroy(false,ffi);check(bind.native==321&&bind.retire==reusedRetire,'old repeated dispose cannot retire reused address');await reused.destroy(false,ffi);
 isMacOS=false;texture=_PixelbufferTexture();texture.create(0,session,ffi);await flush();final nonMac=bind.native;final nonMacRetire=bind.retire;
 await texture.destroy(false,ffi);check(bind.native==nonMac&&bind.retire==nonMacRetire,'non-Mac migration behavior preserved');
 texture=_PixelbufferTexture();texture.create(0,session,ffi);await flush();await texture.destroy(true,ffi);check(bind.native==null&&bind.retire==nonMacRetire,'non-Mac ordinary zero-pointer retirement preserved');
 print('PASS: actual generated Dart class: migration, matching/newer pointers, repeated disposal, late create/getPtr, same-session new generation, failed registry and unchanged non-Mac paths');
}
'''


def dart_controls(output, generated, originals):
    text = (generated / "flutter/lib/models/desktop_render_texture.dart").read_text()
    a = text.index("class _PixelbufferTexture {")
    b = text.index("\nclass _GpuTexture {", a)
    fixture = output / "actual-pixelbuffer-lifecycle.dart"
    fixture.write_text(DART_STUBS + text[a:b] + DART_TEST)
    dart = find_dart()
    if not dart:
        raise RuntimeError("Dart SDK required for async texture controls")
    print(run([dart, "analyze", str(fixture)], output, "dart-analyze.log").strip())
    print(run([dart, str(fixture)], output, "dart-lifecycle-output.log").strip())
    original = originals["flutter/lib/models/desktop_render_texture.dart"].decode()
    a = original.index("class _PixelbufferTexture {")
    b = original.index("\nclass _GpuTexture {", a)
    negative = output / "original-zero-registration.dart"
    negative.write_text(DART_STUBS + original[a:b] + r'''
Future<void> main() async {
 final ffi=FFI();final texture=_PixelbufferTexture();texture.textureRenderer.failedId=0;
 texture.create(0,SessionID('failed'),ffi);await flush();
 check(ffi.textureModel.ids.contains(0)&&bind.registrations==1&&bind.native!=null,'original exact0 failure publishes UI and registers pointer');
 print('PASS: exact original Dart registration failure0 publishes UI and native pointer; regression reproduced');
}
''')
    print(run([dart, str(negative)], output, "original-dart-zero-output.log").strip())


def git_archive_controls(output, files):
    # Reconstruct only the already verified98 source blobs in an owned Git
    # object database; no global settings, upstream refs or worktrees change.
    fixture = output / "actual-git-archive-source"
    fixture.mkdir()
    for name, data in files.items():
        file = fixture / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
    subprocess.run(["git", "init", "-q", str(fixture)], check=True, capture_output=True, timeout=30)
    subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-C", str(fixture), "add", "-f", "--", "."], check=True, capture_output=True, timeout=30)
    tree = subprocess.check_output(["git", "-C", str(fixture), "write-tree"], timeout=30).decode().strip()
    subprocess.run(["git", "-C", str(fixture), "config", "core.autocrlf", "true"], check=True, capture_output=True, timeout=30)
    subprocess.run(["git", "-C", str(fixture), "config", "core.eol", "crlf"], check=True, capture_output=True, timeout=30)
    helper = ast.parse((ROOT / "scripts/patch-support-macos-texture.py").read_text())
    fetch = next(node for node in helper.body if isinstance(node, ast.FunctionDef) and node.name == "fetch_source")
    calls = [node for node in ast.walk(fetch) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "check_output"
             and node.args and isinstance(node.args[0], ast.List)
             and any(isinstance(part, ast.Constant) and part.value == "archive" for part in node.args[0].elts)]
    assert len(calls) == 1
    call = calls[0]
    assert len(call.keywords) == 1 and call.keywords[0].arg == "timeout" and call.keywords[0].value.value == 30
    command = eval(compile(ast.Expression(call.args[0]), "actual-fetch-source-archive-command", "eval"),
                   {"__builtins__": {}}, {"directory": str(fixture), "PIN": tree})
    assert command[:5] == ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf"]
    original = [command[0]] + command[5:]
    uncorrected = subprocess.check_output(original, timeout=30)
    (output / "actual-original-autocrlf-archive.tar").write_bytes(uncorrected)
    try:
        P.source_files(uncorrected)
    except RuntimeError as error:
        assert str(error) == "Complete pinned renderer inventory changed"
    else:
        raise AssertionError("Original Git archive unexpectedly preserved pinned LF blobs under core.autocrlf=true")
    corrected = subprocess.check_output(command, timeout=30)
    (output / "actual-corrected-autocrlf-archive.tar").write_bytes(corrected)
    assert P.source_files(corrected) == files
    # Removing the current source's exact override recreates the real failure;
    # an implementation that merely relaxes inventory validation cannot pass.
    with tarfile.open(fileobj=io.BytesIO(uncorrected)) as archive:
        prior = {entry.name: archive.extractfile(entry).read()
                 for entry in archive.getmembers() if entry.isfile()}
    changed = sorted(name for name in files if prior[name] != files[name])
    assert changed
    (output / "git-archive-source-proof.json").write_text(json.dumps({
        "result": "passed", "pinned_inventory_sha256": P.INVENTORY_SHA,
        "original_changed_file_count": len(changed), "original_changed_files": changed,
        "original_archive_sha256": P.sha(uncorrected), "corrected_archive_sha256": P.sha(corrected),
        "source_extracted_command": command, "original_inventory_rejected": True,
        "corrected_all98_blobs_equal": True, "global_Git_settings_changed": False,
        "scope": "Real Git archive conversion on an isolated98-blob source fixture; revision is an owned fixture tree, not a fabricated upstream identity. Actual fetch retains its exact commit/tree checks."}, indent=2) + "\n")
    print("PASS: real Git core.autocrlf=true archive changes pinned source bytes and original inventory rejects; current exact-source archive command preserves all98 blobs without global setting changes")


def exact_bridge_native_close_commands(workflow):
    original = [
        ['../scripts/patch-support-macos-texture.py'],
        ['../scripts/patch-support-macos-window.py'],
    ]
    native = '../scripts/patch-support-linux-child-window-close.py'
    uri = '../scripts/patch-support-uri-routing.py'
    commands = [[uri, '--root', '.', '--restore'], [native, '--root', '.', '--restore'], *original, [native, '--root', '.', '--after-branding'], [uri, '--root', '.', '--after-branding']]
    lines = ['          ' + ('RDREPO=. ' if arguments in original else '') + 'python3 ' + ' '.join(arguments) + '\n' for arguments in commands]
    header = '      - name: Generate bridge\n        working-directory: rustdesk\n        run: |\n'
    following = "          pushd flutter && sed -i -e 's/extended_text: 14.0.0/extended_text: 13.0.0/g' pubspec.yaml && flutter pub get && popd\n"
    assert all(workflow.count(line) == 1 for line in lines), 'Exact native bracket/original hook count changed'
    assert workflow.count(header + ''.join(lines) + following) == 1, 'Exact native bracket/original hook order or placement changed'
    return commands


def bridge_native_close_mutation_controls(workflow):
    commands = exact_bridge_native_close_commands(workflow)
    lines = ['          ' + ('RDREPO=. ' if len(arguments) == 1 else '') + 'python3 ' + ' '.join(arguments) + '\n' for arguments in commands]
    original = ''.join(lines)
    mutations = []
    for index, line in enumerate(lines):
        mutations.extend((workflow.replace(line, '', 1), workflow.replace(line, line + line, 1),
                          workflow.replace(line, line.rstrip('\n') + ' --extra\n', 1)))
    for index in range(len(lines) - 1):
        swapped = lines[:]
        swapped[index:index + 2] = reversed(swapped[index:index + 2])
        mutations.append(workflow.replace(original, ''.join(swapped), 1))
    for order in (list(reversed(lines)), lines[1:] + lines[:1], lines[-1:] + lines[:-1]):
        mutations.append(workflow.replace(original, ''.join(order), 1))
    mutations.append(workflow.replace(original, lines[0] + '# disjoint\n' + ''.join(lines[1:]), 1))
    mutations.append(workflow.replace(original, '', 1) + original)
    for changed in mutations:
        try:
            exact_bridge_native_close_commands(changed)
        except AssertionError:
            pass
        else:
            raise AssertionError('Changed native bracket/original hook workflow accepted')
    return len(mutations)


def uri_bridge_fixture_sources(uri):
    """Bind all seven actual prior-owner URI sources, including fresh-source CI jobs."""
    explicit = os.environ.get('MIXEL_URI_FIXTURE_SOURCE')
    candidates = [Path(explicit).absolute()] if explicit else [Path(os.environ.get('RDREPO', ROOT / 'rustdesk')).absolute()]
    if not explicit and os.environ.get('RUNNER_TEMP'):
        candidates.append(Path(os.environ['RUNNER_TEMP']).absolute() / 'mixel-sdk-source/pipeline-lf')
    for source in candidates:
        if not source.exists():
            continue
        with input_lifecycle.current_source(source) as previous:
            result = uri_bridge_fixture_sources_from_previous(uri, previous, explicit)
        if result is not None:
            return result
    raise AssertionError('URI bridge fixture needs complete branded source via RDREPO, RUNNER_TEMP/mixel-sdk-source/pipeline-lf or MIXEL_URI_FIXTURE_SOURCE')


def uri_bridge_fixture_sources_from_previous(uri, source, explicit):
    files = {name: uri.canonical(uri.safe_source(source, name).read_bytes())[0] for name in uri.ORIGINAL}
    hashes = {name: uri.digest(value.encode()) for name, value in files.items()}
    if hashes == uri.FRESH and not explicit:
        return None
    if hashes == uri.FINAL:
        files = {name: uri.transform(name, value, restore=True) for name, value in files.items()}
    if {name: uri.digest(value.encode()) for name, value in files.items()} != uri.ORIGINAL:
        raise AssertionError('Changed URI bridge prior-owner fixture source')
    # Keep the real complete-profile guard: a final Dart-only fixture can no
    # longer stand in for the native sender/common/core/receiver/queue owner.
    uri.apply(source, check=True, restore=True, verify=False)
    native_files = {}
    for name in uri.NATIVE_ORIGINAL:
        value = uri.canonical(uri.safe_source(source, name).read_bytes())[0]
        native_files[name] = uri.transform_native(name, value, restore=True)
    if {name: uri.digest(value.encode()) for name, value in native_files.items()} != uri.NATIVE_ORIGINAL:
        raise AssertionError('Changed native URI bridge prior-owner fixture source')
    files.update(native_files)
    return {name: value.encode() for name, value in files.items()}


def workflow_controls(output, originals, files, window_files=None):
    workflow = input_lifecycle.canonical(input_lifecycle.inverse_blocks((ROOT / ".github/workflows/build.yml").read_bytes(), ".github/workflows/build.yml"))[0]
    native_commands = exact_bridge_native_close_commands(workflow)
    output = Path(output).resolve(strict=True)
    helpers = ("patch-support-macos-texture.py", "patch-support-macos-window.py")
    hooks = tuple("          RDREPO=. python3 ../scripts/" + name + "\n" for name in helpers)
    assert all(workflow.count(hook) == 1 for hook in hooks)
    committed = input_lifecycle.canonical(input_lifecycle.committed_workflow(subprocess.check_output(["git", "-C", str(ROOT), "show", "HEAD:.github/workflows/build.yml"]), ".github/workflows/build.yml"))[0]
    assert workflow == committed, "No production build job, hook or guard may change for this fixture correction"
    match = re.search(r"      - name: Generate bridge\n        working-directory: rustdesk\n        run: \|\n((?:          .*\n)+)", workflow)
    assert match
    script = textwrap.dedent(match[1])
    assert script.index(helpers[0]) < script.index(helpers[1]) < script.index("flutter pub get") < script.index("flutter_rust_bridge_codegen")
    window_spec = importlib.util.spec_from_file_location("mixel_window_workflow", ROOT / "scripts" / helpers[1])
    window = importlib.util.module_from_spec(window_spec)
    window_spec.loader.exec_module(window)
    window_files = window.fetch_source() if window_files is None else dict(window_files)
    assert len(window_files) == 124 and window.inventory(window_files) == window.INVENTORY_SHA
    native_spec = importlib.util.spec_from_file_location("mixel_native_bridge", ROOT / "scripts/patch-support-linux-child-window-close.py")
    native = importlib.util.module_from_spec(native_spec)
    native_spec.loader.exec_module(native)
    uri_spec = importlib.util.spec_from_file_location("mixel_uri_bridge", ROOT / "scripts/patch-support-uri-routing.py")
    uri = importlib.util.module_from_spec(uri_spec)
    uri_spec.loader.exec_module(uri)
    uri_sources = uri_bridge_fixture_sources(uri)
    uri_assertions = "import hashlib\nuri_pins = " + repr({**uri.FINAL, **uri.NATIVE_FINAL}) + "\nuri_source = Path('..') if Path('pubspec.yaml').is_file() else Path('.')\nfor name, digest in uri_pins.items():\n    assert hashlib.sha256((uri_source / name).read_bytes()).hexdigest() == digest, 'Final URI routing layer missing before Flutter/codegen'\n"
    native_assertions = "import hashlib\n" + "native_pins = " + repr(native.FINAL) + "\n" + \
        "native_dependency = Path('.mixel-dependencies/desktop_multi_window') if Path('pubspec.yaml').is_file() else Path('flutter/.mixel-dependencies/desktop_multi_window')\nfor name, digest in native_pins.items():\n    assert hashlib.sha256((native_dependency / name).read_bytes()).hexdigest() == digest, 'Final native close layer missing before Flutter/codegen'\n"
    owner = output / "actual bridge étape"
    owner.mkdir()

    def prepare_case(case, native_final=False):
        repo = case / "rustdesk"
        write_repo(repo, originals)
        write_repo(repo, uri_sources)
        (repo / "flutter/macos/Runner").mkdir(parents=True)
        (repo / "flutter/ios/Runner").mkdir(parents=True)
        (case / "scripts").mkdir()
        for name in (*helpers, "patch-support-linux-child-window-close.py", "patch-support-uri-routing.py"):
            shutil.copyfile(ROOT / "scripts" / name, case / "scripts" / name)
        # Both actual child helpers validate their complete pinned archives;
        # only network retrieval is replaced with these exact source bytes.
        for name, contents in (("pinned-renderer.tar", files), ("pinned-window.tar", window_files)):
            with tarfile.open(case / name, "w") as tar:
                for member_name, data in contents.items():
                    info = tarfile.TarInfo(member_name); info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
        if native_final:
            with patch.object(P, "fetch_source", return_value=dict(files)):
                P.apply(repo)
            with patch.object(window, "fetch_source", return_value=dict(window_files)):
                window.apply(repo)
            # Only the complete prior app-composition validator is a seam in this
            # thirteen-file fixture. Provenance, manager and exact native bytes remain real.
            native.apply(repo, verify=False)
            uri.apply(repo, verify=False)
        return repo

    repo = prepare_case(owner / "with-both-hooks", native_final=True)
    before_unbracketed = snapshot(repo)
    try:
        window.apply(repo)
    except RuntimeError as error:
        assert str(error) == "Owned subwindow source or provenance changed"
    else:
        raise AssertionError("Original unbracketed window hook accepted the final native layer")
    assert snapshot(repo) == before_unbracketed
    binary = owner / "bin"
    binary.mkdir()
    python = binary / "python3"
    python.write_text('#!' + sys.executable + '\n' + r'''
import importlib.util,json,os,sys
from pathlib import Path
allowed={'../scripts/patch-support-macos-texture.py':'pinned-renderer.tar',
         '../scripts/patch-support-macos-window.py':'pinned-window.tar'}
native='../scripts/patch-support-linux-child-window-close.py'
uri='../scripts/patch-support-uri-routing.py'
args=sys.argv[1:]
assert ((len(args)==1 and args[0] in allowed) or
        args in ([native,'--root','.','--restore'],[native,'--root','.','--after-branding'],[uri,'--root','.','--restore'],[uri,'--root','.','--after-branding'])), 'Unexpected bridge helper invocation'
s=importlib.util.spec_from_file_location('actual_patch',args[0])
p=importlib.util.module_from_spec(s);s.loader.exec_module(p)
if args[0] in (native,uri):
    # This owned thirteen-file fixture has no whole application composition. Keep
    # native CLI parsing/provenance/manager/exact transforms; seam only that validator.
    p.validate_prior=lambda *arguments: None
    if args[0]==native and args[-1]=='--after-branding':
        assert Path('flutter/.mixel-dependencies/desktop_multi_window').is_dir(), 'Original window hook must materialize its complete source before native reapply'
    if args[0]==native:
        u=importlib.util.spec_from_file_location('fixture_uri_prior',uri)
        owner=importlib.util.module_from_spec(u);u.loader.exec_module(owner)
        assert {name:owner.digest(owner.canonical(Path(name).read_bytes())[0].encode()) for name in owner.ORIGINAL}==owner.ORIGINAL, 'URI layer must restore before native owner'
        assert {name:owner.digest(owner.canonical(Path(name).read_bytes())[0].encode()) for name in owner.NATIVE_ORIGINAL}==owner.NATIVE_ORIGINAL, 'Native URI layer must restore before native owner'
    sys.argv=[args[0], *args[1:]]
    p.main()
else:
    p.fetch_source=lambda:p.source_files((Path('..')/allowed[args[0]]).read_bytes())
    p.apply(Path(os.environ['RDREPO']))
    with Path('../actual-helper-invocations.jsonl').open('a') as log:
        log.write(json.dumps(args)+'\n')
with Path('../actual-bridge-invocations.jsonl').open('a') as log:
    log.write(json.dumps(args)+'\n')
''')
    flutter = binary / "flutter"
    flutter.write_text('#!' + sys.executable + '\n' + r'''
from pathlib import Path
assert 'session_unregister_pixelbuffer_texture_if_current' in Path('../src/flutter_ffi.rs').read_text()
assert 'path: .mixel-dependencies/texture_rgba_renderer' in Path('pubspec.yaml').read_text()
assert 'path: .mixel-dependencies/desktop_multi_window' in Path('pubspec.yaml').read_text()
assert 'guard flutterViewController.engine.run(withEntrypoint: nil) else {' in Path('.mixel-dependencies/desktop_multi_window/macos/Classes/FlutterWindow.swift').read_text()
NATIVE_FINAL_ASSERTIONS
URI_FINAL_ASSERTIONS
print('PASS: actual bridge step patched both pinned dependencies before Flutter resolution')
''')
    codegen = binary / "flutter_rust_bridge_codegen"
    codegen.write_text('#!' + sys.executable + '\n' + r'''
from pathlib import Path
assert 'session_unregister_pixelbuffer_texture_if_current' in Path('src/flutter_ffi.rs').read_text()
assert 'bool sessionUnregisterPixelbufferTextureIfCurrent(' in Path('flutter/lib/web/bridge.dart').read_text()
assert 'guard flutterViewController.engine.run(withEntrypoint: nil) else {' in Path('flutter/.mixel-dependencies/desktop_multi_window/macos/Classes/FlutterWindow.swift').read_text()
NATIVE_FINAL_ASSERTIONS
URI_FINAL_ASSERTIONS
Path('flutter/macos/Runner/bridge_generated.h').write_text('ordered bridge fixture')
print('PASS: actual bridge step reaches codegen with both source hooks applied')
''')
    for executable in (flutter, codegen):
        executable.write_text(executable.read_text().replace('NATIVE_FINAL_ASSERTIONS', native_assertions).replace('URI_FINAL_ASSERTIONS', uri_assertions))
    for executable in (python, flutter, codegen):
        executable.chmod(0o755)
    script = script.replace("~/.cargo/bin/flutter_rust_bridge_codegen", shlex.quote(codegen.as_posix()))
    bash = shutil.which("bash")
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.is_file():
            bash = str(candidate)
    assert bash
    environment = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"])
    result = subprocess.run([bash, "-e", "-o", "pipefail", "-c", script], cwd=repo, env=environment, capture_output=True, text=True, timeout=120)
    (output / "actual-bridge-step-output.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo.parent / "actual-helper-invocations.jsonl").read_text().splitlines() == [json.dumps(["../scripts/" + name]) for name in helpers]
    assert len(P.existing_files(repo / "flutter" / P.DEPENDENCY)) == 98 + len(P.OWNED)
    assert (repo.parent / "actual-bridge-invocations.jsonl").read_text().splitlines() == [json.dumps(arguments) for arguments in native_commands]
    final_before_inverse = snapshot(repo)
    uri.apply(repo, restore=True, verify=False)
    native.apply(repo, restore=True, verify=False)
    assert len(window.existing_files(repo / "flutter" / window.DEPENDENCY)) == 124
    native.apply(repo, verify=False)
    uri.apply(repo, verify=False)
    assert snapshot(repo) == final_before_inverse
    # Exact prior fixture bodies demonstrate why a second helper masked a
    # removed texture hook: the Python stand-in ignored its arguments and
    # always applied texture, while both downstream tools checked only texture.
    prior_binary = owner / "prior-bin"
    prior_binary.mkdir()
    prior_bodies = {
        "python3": r'''
import importlib.util,os
from pathlib import Path
s=importlib.util.spec_from_file_location('actual_patch','../scripts/patch-support-macos-texture.py')
p=importlib.util.module_from_spec(s);s.loader.exec_module(p)
p.fetch_source=lambda:p.source_files(Path('../pinned-renderer.tar').read_bytes())
p.apply(Path(os.environ['RDREPO']))
''',
        "flutter": r'''
from pathlib import Path
assert 'session_unregister_pixelbuffer_texture_if_current' in Path('../src/flutter_ffi.rs').read_text()
assert 'path: .mixel-dependencies/texture_rgba_renderer' in Path('pubspec.yaml').read_text()
print('PASS: actual bridge step patched source before Flutter resolution')
''',
        "flutter_rust_bridge_codegen": r'''
from pathlib import Path
assert 'session_unregister_pixelbuffer_texture_if_current' in Path('src/flutter_ffi.rs').read_text()
assert 'bool sessionUnregisterPixelbufferTextureIfCurrent(' in Path('flutter/lib/web/bridge.dart').read_text()
Path('flutter/macos/Runner/bridge_generated.h').write_text('ordered bridge fixture')
print('PASS: actual bridge step reaches codegen with new retirement API')
''',
    }
    for name, body in prior_bodies.items():
        path = prior_binary / name
        path.write_text('#!' + sys.executable + '\n' + body)
        path.chmod(0o755)
    prior_repo = prepare_case(owner / "prior-dispatch-without-texture")
    prior_script = script.replace("RDREPO=. python3 ../scripts/" + helpers[0] + "\n", "", 1)
    prior_script = prior_script.replace(shlex.quote(codegen.as_posix()), shlex.quote((prior_binary / "flutter_rust_bridge_codegen").as_posix()), 1)
    prior_result = subprocess.run([bash, "-e", "-o", "pipefail", "-c", prior_script], cwd=prior_repo,
                                  env={**environment, "RDREPO": ".", "PATH": str(prior_binary) + os.pathsep + os.environ["PATH"]},
                                  capture_output=True, text=True, timeout=30)
    (output / "bridge-prior-dispatch-without-texture-hook.log").write_text(prior_result.stdout + prior_result.stderr)
    assert prior_result.returncode == 0, prior_result.stdout + prior_result.stderr
    assert len(P.existing_files(prior_repo / "flutter" / P.DEPENDENCY)) == 98 + len(P.OWNED)
    assert not (prior_repo / "flutter" / window.DEPENDENCY).exists()
    print("PASS: reproduced prior argument-ignoring Python fixture falsely passing a removed texture hook through the window invocation")
    negatives = {}
    # Each negative gets the same complete source/helper fixtures. Failure must
    # arise from the missing dependency, not a missing helper/archive path.
    for name in helpers:
        negative = prepare_case(owner / ("without-" + name.removesuffix(".py")))
        changed = script.replace("RDREPO=. python3 ../scripts/" + name + "\n", "", 1)
        result = subprocess.run([bash, "-e", "-o", "pipefail", "-c", changed], cwd=negative, env=environment, capture_output=True, text=True, timeout=30)
        (output / ("bridge-without-" + name + "-negative.log")).write_text(result.stdout + result.stderr)
        assert result.returncode != 0 and "AssertionError" in result.stderr
        other = helpers[1] if name == helpers[0] else helpers[0]
        assert (negative.parent / "actual-helper-invocations.jsonl").read_text().splitlines() == [json.dumps(["../scripts/" + other])]
        negatives[name] = result.returncode
    native_negatives = {}
    bracket_lines = ["python3 " + " ".join(arguments) + "\n" for arguments in (native_commands[1], native_commands[-2])]
    for label, changed in (
            ('missing-restore', script.replace(bracket_lines[0], '', 1)),
            ('missing-reapply', script.replace(bracket_lines[1], '', 1)),
            ('reapply-before-window', script.replace(bracket_lines[1], '', 1).replace('RDREPO=. python3 ../scripts/' + helpers[1] + '\n', bracket_lines[1] + 'RDREPO=. python3 ../scripts/' + helpers[1] + '\n', 1))):
        negative = prepare_case(owner / label, native_final=True)
        result = subprocess.run([bash, "-e", "-o", "pipefail", "-c", changed], cwd=negative, env=environment, capture_output=True, text=True, timeout=30)
        (output / ('bridge-' + label + '-negative.log')).write_text(result.stdout + result.stderr)
        assert result.returncode != 0
        expected = 'Final native close layer missing' if label == 'missing-reapply' else 'Owned subwindow source or provenance changed'
        assert expected in result.stderr
        native_negatives[label] = result.returncode
    uri_negatives = {}
    uri_lines = ['python3 ' + ' '.join(arguments) + '\n' for arguments in (native_commands[0], native_commands[-1])]
    for label, changed in (
            ('missing-uri-restore', script.replace(uri_lines[0], '', 1)),
            ('missing-uri-reapply', script.replace(uri_lines[1], '', 1)),
            ('uri-reapply-before-native', script.replace(uri_lines[1], '', 1).replace(bracket_lines[1], uri_lines[1] + bracket_lines[1], 1))):
        negative = prepare_case(owner / label, native_final=True)
        result = subprocess.run([bash, '-e', '-o', 'pipefail', '-c', changed], cwd=negative, env=environment, capture_output=True, text=True, timeout=30)
        (output / ('bridge-' + label + '-negative.log')).write_text(result.stdout + result.stderr)
        assert result.returncode != 0
        expected = 'Final URI routing layer missing' if label == 'missing-uri-reapply' else 'URI layer must restore before native owner'
        assert expected in result.stderr
        uri_negatives[label] = result.returncode
    mutation_refusals = bridge_native_close_mutation_controls(workflow)
    before = snapshot(repo)
    before_invocations = (repo.parent / "actual-helper-invocations.jsonl").read_bytes()
    before_bridge_invocations = (repo.parent / "actual-bridge-invocations.jsonl").read_bytes()
    native_path = '../scripts/patch-support-linux-child-window-close.py'
    for args in ([], ["../scripts/unexpected.py"], ["../scripts/" + helpers[0], "--extra"], ["../scripts/" + helpers[1], "--extra"],
                 [native_path], [native_path, '--root', 'foreign', '--restore'],
                 [native_path, '--root', '.', '--restore', '--after-branding'],
                 [native_path, '--root', '.', '--after-branding', '--extra'],
                 [native_path, '--root', '.', '--wrong'], [native_path, '--restore', '--root', '.'],
                 ['../scripts/patch-support-uri-routing.py'], ['../scripts/patch-support-uri-routing.py', '--root', 'foreign', '--restore'],
                 ['../scripts/patch-support-uri-routing.py', '--root', '.', '--restore', '--after-branding'],
                 ['../scripts/patch-support-uri-routing.py', '--root', '.', '--after-branding', '--extra']):
        result = subprocess.run([sys.executable, str(python)] + args, cwd=repo, env={**environment, "RDREPO": "."}, capture_output=True, text=True, timeout=15)
        assert result.returncode != 0 and "Unexpected bridge helper invocation" in result.stderr
        assert snapshot(repo) == before and (repo.parent / "actual-helper-invocations.jsonl").read_bytes() == before_invocations
        assert (repo.parent / "actual-bridge-invocations.jsonl").read_bytes() == before_bridge_invocations
    (output / "workflow-source-proof.json").write_text(json.dumps({"result":"passed", "workflow_sha256":P.sha(workflow.encode()), "head_workflow_sha256":P.sha(committed.encode()), "only_change":"test-only faithful dispatch of exact URI/native brackets around both original bridge hooks", "all_other_build_jobs_and_guards_byte_identical":True, "actual_source_step_executed":True, "pinned_renderer_files":98, "pinned_window_files":124, "prior_argument_ignoring_dispatch_false_pass_reproduced":True, "independent_missing_hook_exit_codes":negatives, "unexpected_helper_invocations_rejected":14, "uri_bracket_runtime_negatives":uri_negatives, "native_bracket_runtime_negatives":native_negatives, "exact_order_count_mutations_refused":mutation_refusals, "native_prior_whole_app_composition":"explicit seam in this thirteen-file fixture; native/URI CLI and exact source profiles real", "original_unbracketed_native_final_inventory_failure_reproduced":True, "native_projection_whole_fixture_restored":True, "compiler_and_Flutter_tools":"ordering stand-ins; actual pinned bridge generation is a separate required build check"}, indent=2) + "\n")
    print("PASS: actual six-command bridge validates pinned98/124 and exact native2/URI7 before Flutter/codegen; original missing hooks plus native/URI bracket failures discriminate; bounded exact order/count mutations and fourteen unexpected invocations refuse; whole fixture inverse conserves bytes; production workflow unchanged")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--native-only", action="store_true", help="Mac source job without Dart; async Dart controls run in complete source jobs")
    args = parser.parse_args()
    repo = Path(os.environ.get("RDREPO", ROOT / "rustdesk")).resolve()
    originals = original_source(repo)
    target = repo / "flutter" / P.DEPENDENCY
    if target.exists():
        files = P.existing_files(target)
        files[P.PLUGIN] = P.unpatch_plugin(files[P.PLUGIN].decode()).encode()
        files[P.TEXT] = P.unpatch_frame(files[P.TEXT].decode()).encode()
        files[P.API] = P.unpatch_api(files[P.API].decode()).encode()
        for name in P.OWNED:
            files.pop(name)
    else:
        files = P.fetch_source()
    with tempfile.TemporaryDirectory(prefix="mixel-mac-texture-controls-") as temporary:
        output = args.output.resolve() if args.output else Path(temporary)
        if args.output:
            output.mkdir(parents=True, exist_ok=False)
        generated = materialization_controls(output, originals, files)
        ownership_source_controls(output, files)
        public_header_controls(output, files)
        adapter_controls(output, files)
        git_archive_controls(output, files)
        workflow_controls(output, originals, files)
        swift_controls(output, files)
        rust_controls(output, generated)
        if args.native_only:
            assert sys.platform == "darwin", "native-only mode requires actual macOS"
            print("SOURCE SCOPE: async Dart controls are required in complete source jobs; this standalone Mac job has native-only scope")
        else:
            dart_controls(output, generated, originals)
        (output / "source-proof.json").write_text(json.dumps({"result": "passed", "dependency_pin": P.PIN, "dependency_tree": P.TREE, "original_inventory_sha256": P.INVENTORY_SHA, "macOS_CoreVideo_executed": sys.platform == "darwin", "dart_async_executed": not args.native_only, "source_sha256": {name: hashlib.sha256((generated / name).read_bytes()).hexdigest() for name in FILES}, "scope": "source-extracted lifecycle/frame-lock/owned dependency" + (" and async Dart" if not args.native_only else "; async Dart skipped in native-only mode") + " controls; no application, UI, TCC, native authorization or remote session qualification"}, indent=2) + "\n")
        print("PASS: pinned Mac closed-texture correction " + ("native-only" if args.native_only else "source/native lifecycle and async handoff") + " controls; existing frame callback ABI unchanged")


if __name__ == "__main__":
    main()
