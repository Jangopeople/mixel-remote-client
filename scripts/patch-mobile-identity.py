#!/usr/bin/env python3
"""Complete the mobile identity chain after the desktop branding projection.

The JNI ffi package and exported Java_ffi_FFI symbols intentionally stay fixed.
Only application classes move namespaces. Kotlin and Dart must load the same
renamed native library. Preflight every mandatory anchor before writing files.
"""
import argparse
import pathlib
import re
import json
import subprocess
import tempfile


def project(root):
    root = pathlib.Path(root)
    changes = {}

    def read(relative):
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing or linked mobile source: {relative}")
        return path, path.read_bytes()

    def replace(relative, old, new, count):
        path, original = read(relative)
        data = changes.get(path, original)
        if data.count(new) == count and data.replace(new, b"").count(old) == 0:
            changes[path] = data
        elif data.count(old) == count:
            changes[path] = data.replace(old, new)
        else:
            raise ValueError(f"Unexpected mobile anchor: {relative}")

    gradle = "flutter/android/app/build.gradle"
    replace(gradle, b'applicationId "com.carriez.flutter_hbb"',
            b'applicationId "ch.mixel.remote"', 1)
    # The pinned Android codec triplet uses API 28. Do not advertise an older
    # install floor that its native libraries have not been built to support.
    replace(gradle, b"minSdkVersion 22", b"minSdkVersion 28", 1)
    # Keep the compiled core byte-exact. Compression handles ZIP alignment on
    # pinned AGP; symbol retention prevents Gradle silently rewriting the core.
    gradle_path, gradle_data = read(gradle)
    gradle_data = changes.get(gradle_path, gradle_data)
    previous_android = (b"    compileSdkVersion 34\n"
        b'    ndkVersion "28.2.13676358"\n'
        b"    packagingOptions { jniLibs { useLegacyPackaging true } }")
    complete_android = previous_android.replace(
        b"useLegacyPackaging true", b"useLegacyPackaging true; "
        b"keepDebugSymbols += ['**/libmixel-remote.so']")
    if gradle_data.count(complete_android) == 1:
        changes[gradle_path] = gradle_data
    elif gradle_data.count(previous_android) == 1:
        changes[gradle_path] = gradle_data.replace(previous_android, complete_android)
    elif gradle_data.count(b"    compileSdkVersion 34") == 1 and b"ndkVersion" not in gradle_data:
        changes[gradle_path] = gradle_data.replace(b"    compileSdkVersion 34", complete_android)
    else:
        raise ValueError("Unexpected Android native packaging profile")
    replace("flutter/android/gradle.properties", b"android.enableJetifier=true",
            b"android.enableJetifier=true\nandroid.bundle.enableUncompressedNativeLibs=false", 1)
    replace("flutter/android/app/src/main/kotlin/ffi.kt",
            b'System.loadLibrary("rustdesk")',
            b'System.loadLibrary("mixel-remote")', 1)
    _, native = read("flutter/lib/models/native_model.dart")
    if not re.search(rb"isAndroid\s*\? DynamicLibrary.open\('libmixel-remote.so'\)", native):
        raise ValueError("Apply desktop branding first: Android Dart loader is not branded")
    base = root / "flutter/android/app/src"
    for path in sorted(base.rglob("*")):
        if path.suffix not in {".kt", ".xml"} or not path.is_file():
            continue
        if path.is_symlink():
            raise ValueError("Linked Android source refused")
        data = changes.get(path, path.read_bytes())
        data = data.replace(b"com.carriez.flutter_hbb", b"ch.mixel.remote")
        if path.suffix == ".xml":
            data = data.replace(b"RustDesk", b"Mixel Remote")
            data = data.replace(b'android:scheme="rustdesk"', b'android:scheme="mixel-remote"')
        else:
            data = data.replace(b'"Show RustDesk"', b'"Show Mixel-Remote"')
        changes[path] = data
    info = "flutter/ios/Runner/Info.plist"
    replace(info, b"<string>RustDesk</string>", b"<string>Mixel Remote</string>", 2)
    replace(info, b"<string>com.carriez.rustdesk</string>", b"<string>ch.mixel.remote</string>", 1)
    replace(info, b"<string>rustdesk</string>", b"<string>mixel-remote</string>", 1)
    project_file = "flutter/ios/Runner.xcodeproj/project.pbxproj"
    project_path, project_data = read(project_file)
    previous_block = (b'MIXEL_RUST_TARGET = aarch64-apple-ios;\n'
        b'\t\t\t\t"MIXEL_RUST_TARGET[sdk=iphonesimulator*]" = aarch64-apple-ios-sim;\n'
        b'\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = ch.mixel.remote;')
    complete_block = previous_block.replace(
        b'MIXEL_RUST_TARGET = aarch64-apple-ios;\n',
        b'MIXEL_RUST_TARGET = aarch64-apple-ios;\n'
        b'\t\t\t\t"ARCHS[sdk=iphonesimulator*]" = arm64;\n')
    if project_data.count(complete_block) == 3:
        changes[project_path] = project_data
    elif project_data.count(previous_block) == 3:
        changes[project_path] = project_data.replace(previous_block, complete_block)
    elif project_data.count(b"PRODUCT_BUNDLE_IDENTIFIER = com.carriez.flutterHbb;") == 3:
        changes[project_path] = project_data.replace(
            b"PRODUCT_BUNDLE_IDENTIFIER = com.carriez.flutterHbb;", complete_block)
    else:
        raise ValueError("Unexpected iOS mobile build-settings profile")
    replace(project_file, b"DEVELOPMENT_TEAM = HZF9JMC8YN;",
            b'DEVELOPMENT_TEAM = "";', 3)
    replace(project_file, b"target/aarch64-apple-ios/", b"target/$(MIXEL_RUST_TARGET)/", 7)
    replace(project_file, b"IPHONEOS_DEPLOYMENT_TARGET = 13.0;",
            b"IPHONEOS_DEPLOYMENT_TARGET = 15.0;", 3)
    replace("flutter/ios/Podfile", b"platform :ios, '13.0'", b"platform :ios, '15.0'", 1)
    replace("flutter/ios/Podfile", b"config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '11.0'",
            b"config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '15.0'", 1)
    # This local target builds an Apple Silicon simulator core. Keep every
    # consuming target on that architecture rather than requesting Intel too.
    replace("flutter/ios/Podfile", b"config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '15.0'",
            b"config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '15.0'\n"
            b"      config.build_settings['ARCHS[sdk=iphonesimulator*]'] = 'arm64'", 1)
    return changes


def apply(root, check=False):
    changes = project(root)
    dirty = [p for p, data in changes.items() if p.read_bytes() != data]
    if not check:
        for path in dirty:
            path.write_bytes(changes[path])
    return len(dirty)


def install_icons(root, icon):
    root, icon = pathlib.Path(root), pathlib.Path(icon).resolve(strict=True)
    catalog = root / "flutter/ios/Runner/Assets.xcassets/AppIcon.appiconset"
    images = json.loads((catalog / "Contents.json").read_text())["images"]
    outputs = {}
    for item in images:
        name = item.get("filename")
        if not name:
            continue
        if pathlib.Path(name).name != name:
            raise ValueError("Nonlocal app icon filename refused")
        size = int(float(item["size"].split("x")[0]) * float(item["scale"].removesuffix("x")))
        outputs[catalog / name] = size
    densities = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    for density, size in densities.items():
        target = root / f"flutter/android/app/src/main/res/mipmap-{density}/ic_launcher.png"
        if not target.is_file() or target.is_symlink():
            raise ValueError("Missing or linked Android launcher icon")
        outputs[target] = size
    # Render all resources successfully before replacing any existing icon.
    with tempfile.TemporaryDirectory() as temporary:
        staged = {}
        for size in sorted(set(outputs.values())):
            output = pathlib.Path(temporary) / f"icon-{size}.png"
            subprocess.run(["sips", "-z", str(size), str(size), str(icon), "--out", str(output)],
                           check=True, capture_output=True)
            staged[size] = output.read_bytes()
        for target, size in outputs.items():
            target.write_bytes(staged[size])
    return len(outputs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--icon", help="Render the Mixel 1024px source into every Android/iOS app icon (macOS)")
    args = parser.parse_args()
    try:
        count = apply(args.root, args.check)
        if args.icon and not args.check:
            print(f"PASS: {install_icons(args.root, args.icon)} mobile launcher icons rendered")
    except ValueError as error:
        parser.exit(1, f"REFUSED: {error}\n")
    print(f"PASS: mobile identity preflight; {count} source files {'require changes' if args.check else 'updated'}")
