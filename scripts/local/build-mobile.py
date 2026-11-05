#!/usr/bin/env python3
"""Build private mobile QA packages from an already desktop-branded checkout.

No marketplace submission, production configuration or physical device required.
The supplied vcpkg root must contain codecs for the selected target. For the
simulator use the arm64-ios triplet with iphonesimulator as its OSX sysroot.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=["android", "ios-simulator"], required=True)
    for key in ["root", "output", "flutter-sdk", "vcpkg-root"]:
        parser.add_argument(f"--{key}", required=True, type=Path)
    parser.add_argument("--android-sdk", type=Path)
    parser.add_argument("--jdk-home", type=Path)
    parser.add_argument("--cargo-home", type=Path)
    parser.add_argument("--gradle-home", type=Path,
                        help="Optional dedicated warm dependency cache; defaults inside output")
    args = parser.parse_args()
    root, output = args.root.resolve(strict=True), args.output.resolve()
    sdk, vcpkg = args.flutter_sdk.resolve(strict=True), args.vcpkg_root.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=False)
    pipeline = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update({"VCPKG_ROOT": str(vcpkg), "IPHONEOS_DEPLOYMENT_TARGET": "15.0"})
    if args.cargo_home:
        env["CARGO_HOME"] = str(args.cargo_home.resolve(strict=True))
    lock = root / "Cargo.lock"
    original_lock = sha(lock)
    commands = []

    def run(label, argv, cwd=root):
        record = {"label": label, "argv": [str(x) for x in argv], "cwd": str(cwd)}
        commands.append(record)
        (output / f"{label}.command.json").write_text(json.dumps(record, indent=2) + "\n")
        with (output / f"{label}.log").open("w") as stream:
            result = subprocess.run(record["argv"], cwd=cwd, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=7200)
        record["exitCode"] = result.returncode
        if result.returncode:
            raise RuntimeError(f"{label} failed; inspect {output / (label + '.log')}")

    qualified = False
    try:
        run("mobile-identity", [sys.executable, pipeline / "scripts/patch-mobile-identity.py",
                               "--root", root, "--icon", pipeline / "branding/icon-1024.png"])
        flutter = sdk / "bin/flutter"
        target = "aarch64-apple-ios-sim"
        triplet = "arm64-android" if args.platform == "android" else "arm64-ios"
        sodium = vcpkg / "installed" / triplet / "lib/libsodium.a"
        if not sodium.is_file() or sodium.stat().st_size < 1024:
            raise ValueError("A nonempty target-native libsodium archive is required")
        # The crate is also a HOST dependency; select each target separately.
        # The Android bundled builder produced an empty archive in live QA.
        env.pop("SODIUM_LIB_DIR", None)
        env.pop("SODIUM_SHARED", None)
        env["SODIUM_USE_PKG_CONFIG"] = "1"
        env["LIBSODIUM_STATIC"] = "1"
        suffix = "aarch64_linux_android" if args.platform == "android" else "aarch64_apple_ios_sim"
        env[f"PKG_CONFIG_PATH_{suffix}"] = str(sodium.parent / "pkgconfig")
        env[f"PKG_CONFIG_ALLOW_CROSS_{suffix}"] = "1"
        if args.platform == "ios-simulator":
            simulator_sdk = subprocess.check_output(
                ["xcrun", "--sdk", "iphonesimulator", "--show-sdk-path"], text=True).strip()
            env["SDKROOT"] = simulator_sdk
            env["BINDGEN_EXTRA_CLANG_ARGS_aarch64_apple_ios_sim"] = (
                "--target=arm64-apple-ios15.0-simulator")
        if args.platform == "android":
            if not args.android_sdk or not args.jdk_home:
                raise ValueError("Android requires --android-sdk and --jdk-home")
            android = args.android_sdk.resolve(strict=True)
            jdk = args.jdk_home.resolve(strict=True)
            # Flutter's legacy user file takes precedence over XDG. Refuse to
            # mutate or silently use it instead of the isolated configuration.
            if (Path.home() / ".flutter_settings").exists():
                raise ValueError("Legacy global Flutter configuration prevents XDG isolation")
            private_config = output / "flutter-config"
            private_config.mkdir()
            (private_config / "settings").write_text(json.dumps({
                "jdk-dir": str(jdk), "android-sdk": str(android), "analytics": False}))
            env.update({"XDG_CONFIG_HOME": str(private_config), "JAVA_HOME": str(jdk),
                        "ANDROID_HOME": str(android), "ANDROID_SDK_ROOT": str(android),
                        "GRADLE_USER_HOME": str(args.gradle_home.resolve(strict=True)
                                                if args.gradle_home else output / "gradle-home")})
            ndk = android / "ndk/28.2.13676358"
            toolchain = ndk / "toolchains/llvm/prebuilt/darwin-x86_64"
            clang = toolchain / "bin/aarch64-linux-android28-clang"
            if not clang.is_file():
                raise ValueError("Android NDK 28.2.13676358 is required")
            env.update({"ANDROID_NDK_HOME": str(ndk),
                        "CC_aarch64_linux_android": str(clang),
                        "CXX_aarch64_linux_android": str(clang) + "++",
                        "AR_aarch64_linux_android": str(toolchain / "bin/llvm-ar"),
                        "RANLIB": str(toolchain / "bin/llvm-ranlib"),
                        "RANLIB_aarch64_linux_android": str(toolchain / "bin/llvm-ranlib"),
                        "BINDGEN_EXTRA_CLANG_ARGS_aarch64_linux_android": (
                            f'--target=aarch64-linux-android28 --sysroot="{toolchain / "sysroot"}" '
                            f'-isystem "{toolchain / "sysroot/usr/include/aarch64-linux-android"}"'),
                        "CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER": str(clang)})
            target = "aarch64-linux-android"
        codec_dir = vcpkg / "installed" / triplet / "lib"
        libraries = ["libvpx.a", "libyuv.a", "libopus.a", "libaom.a", "libsodium.a"]
        codec_hashes = {}
        for name in libraries:
            archive = codec_dir / name
            codec_hashes[name] = sha(archive)
            if args.platform == "ios-simulator":
                inspection = subprocess.check_output(["xcrun", "otool", "-l", str(archive)], text=True)
                tags = set(re.findall(r"^\s*platform\s+(\d+)", inspection, re.MULTILINE))
                if tags != {"7"} or "LC_VERSION_MIN_IPHONEOS" in inspection:
                    raise ValueError(f"{name} contains non-simulator Apple objects")
        (output / "codec-inputs.json").write_text(json.dumps(codec_hashes, indent=2) + "\n")
        cache_receipt = root / "target" / f"mixel-codecs-{target}.json"
        old_receipt = json.loads(cache_receipt.read_text()) if cache_receipt.is_file() else None
        receipt = {"format": 2, "libraries": codec_hashes}
        if old_receipt != receipt:
            # scrap's rlib embeds vcpkg objects. Cargo cannot notice an archive
            # being replaced outside its source graph; invalidate that exact
            # target crate rather than reusing rejected device objects.
            run("invalidate-codec-cache", ["cargo", "+1.75.0", "clean", "-p", "scrap", "--release", "--target", target])
            cache_receipt.parent.mkdir(parents=True, exist_ok=True)
            cache_receipt.write_text(json.dumps(receipt, indent=2) + "\n")
        cargo_command = "rustc" if args.platform == "android" else "build"
        native_command = ["cargo", "+1.75.0", cargo_command, "--locked", "--lib", "--features",
                          "flutter", "--target", target, "--release", "-j", "2"]
        if args.platform == "android":
            native_command.extend(["--", "-C", "link-arg=-Wl,--no-undefined",
                                   "-C", "link-arg=-Wl,-z,max-page-size=16384",
                                   "-C", "link-arg=-Wl,-z,common-page-size=16384"])
        run("native-core", native_command)
        if args.platform == "android":
            native = root / f"target/{target}/release/liblibrustdesk.so"
            libs = root / "flutter/android/app/src/main/jniLibs/arm64-v8a"
            libs.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(native, libs / "libmixel-remote.so")
            runtime = toolchain / "sysroot/usr/lib/aarch64-linux-android/libc++_shared.so"
            shutil.copyfile(runtime, libs / runtime.name)
            run("android-package", [flutter, "build", "apk", "--debug", "--target-platform",
                                    "android-arm64"], root / "flutter")
            artifact = root / "flutter/build/app/outputs/flutter-apk/app-debug.apk"
            with zipfile.ZipFile(artifact) as archive:
                bundled = archive.read("lib/arm64-v8a/libmixel-remote.so")
                if hashlib.sha256(bundled).hexdigest() != sha(native):
                    raise ValueError("APK native core differs from the compiled core")
            delivered = output / "Mixel-Remote-Android-QA.apk"
            shutil.copyfile(artifact, delivered)
            run("android-page-alignment", [sys.executable,
                pipeline / "scripts/local/check-android-pages.py", delivered,
                "--output", output / "android-page-alignment.json"])
        else:
            native = root / f"target/{target}/release/liblibrustdesk.a"
            run("ios-package", [flutter, "build", "ios", "--simulator", "--debug",
                                "--no-codesign"], root / "flutter")
            artifact = root / "flutter/build/ios/iphonesimulator/Runner.app"
            delivered = output / "Mixel-Remote-iOS-Simulator.app"
            shutil.copytree(artifact, delivered, symlinks=True)
        if sha(lock) != original_lock:
            raise ValueError("Mobile build modified the pinned Cargo.lock")
        qualified = True
        print(f"PASS: private {args.platform} package built: {delivered}")
    finally:
        (output / "build.result.json").write_text(json.dumps({
            "packageBuildQualified": qualified, "runtimeQualified": False,
            "platform": args.platform, "cargoLockUnchanged": sha(lock) == original_lock,
            "commands": commands, "scope": "Private QA package; no store or customer publication"}, indent=2) + "\n")


if __name__ == "__main__":
    main()
