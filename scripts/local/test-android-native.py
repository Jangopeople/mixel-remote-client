#!/usr/bin/env python3
"""Execute packaged Rust JNI in a dedicated ARM Android emulator, without UI actions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["root", "output", "adb", "jdk-home", "gradle-home"]:
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--page-size", type=int, choices=[4096, 16384], required=True)
    parser.add_argument("--application-apk", required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"emulator-[0-9]+", args.serial):
        parser.error("This runner accepts only a dedicated emulator serial")
    root = args.root.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = root / "flutter/android"
    build = project / "app/build.gradle"
    original = build.read_text()
    runner = 'testInstrumentationRunner "androidx.test.runner.AndroidJUnitRunner"'
    if runner not in original:
        anchor = "        minSdkVersion 28"
        if original.count(anchor) != 1 or original.count("dependencies {") != 1:
            raise ValueError("Unexpected Android QA fixture anchors")
        original = original.replace(anchor, anchor + "\n        " + runner)
        original = original.replace("dependencies {", "dependencies {\n"
            '    androidTestImplementation "androidx.test:runner:1.5.2"\n'
            '    androidTestImplementation "androidx.test.ext:junit:1.1.5"')
        build.write_text(original)
    fixture = Path(__file__).resolve().parents[1] / "fixtures/mobile/AndroidNativeCoreTest.java"
    target = project / "app/src/androidTest/java/ch/mixel/remote/qa/AndroidNativeCoreTest.java"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture, target)
    env = os.environ.copy()
    env.update({"JAVA_HOME": str(args.jdk_home.resolve(strict=True)),
                "GRADLE_USER_HOME": str(args.gradle_home.resolve(strict=True))})
    commands = []

    def run(label, argv):
        entry = {"label": label, "argv": [str(x) for x in argv]}
        commands.append(entry)
        result = subprocess.run(entry["argv"], cwd=project, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800)
        (output / f"{label}.log").write_text(result.stdout)
        entry["exitCode"] = result.returncode
        if result.returncode:
            raise RuntimeError(f"{label} failed; see its retained log")
        return result.stdout

    qualified = False
    app = args.application_apk.resolve(strict=True)
    app_sha256 = hashlib.sha256(app.read_bytes()).hexdigest()
    with zipfile.ZipFile(app) as archive:
        core_sha256 = hashlib.sha256(archive.read("lib/arm64-v8a/libmixel-remote.so")).hexdigest()
    try:
        run("assemble-tests", [project / "gradlew", "--no-daemon", "--max-workers=2",
            ":app:assembleDebugAndroidTest", "-Ptarget-platform=android-arm64"])
        test = root / "flutter/build/app/outputs/apk/androidTest/debug/app-debug-androidTest.apk"
        adb = [args.adb.resolve(strict=True), "-s", args.serial]
        run("install-application", adb + ["install", "-r", app])
        installed = run("installed-package-path", adb + ["shell", "pm", "path", "ch.mixel.remote"]).strip()
        paths = [line.removeprefix("package:") for line in installed.splitlines() if line.startswith("package:")]
        if len(paths) != 1 or not paths[0].startswith("/data/app/") or not paths[0].endswith("/base.apk"):
            raise RuntimeError("Unexpected installed QA package path")
        readback = output / "installed-base.apk"
        run("installed-package-readback", adb + ["pull", paths[0], readback])
        if hashlib.sha256(readback.read_bytes()).hexdigest() != app_sha256:
            raise RuntimeError("Installed application differs from the supplied APK")
        run("install-test", adb + ["install", "-r", test])
        result = run("native-jni-tests", adb + ["shell", "am", "instrument", "-w", "-r",
            "-e", "mixelExpectedPageSize", str(args.page_size),
            "ch.mixel.remote.test/androidx.test.runner.AndroidJUnitRunner"])
        qualified = "OK (4 tests)" in result and "FAILURES" not in result and "INSTRUMENTATION_FAILED" not in result
        if not qualified:
            raise RuntimeError("Native instrumentation did not pass all four tests")
        print("PASS: 4 real ARM emulator tests; kernel page size, identity, native Rust JNI, brand and option read")
    finally:
        (output / "result.json").write_text(json.dumps({
            "nativeRuntimeQualified": qualified, "uiFlowQualified": False,
            "physicalDeviceQualified": False, "serial": args.serial,
            "kernelPageSize": args.page_size,
            "applicationApkSha256": app_sha256, "nativeCoreSha256": core_sha256,
            "fixtureSha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
            "commands": commands}, indent=2) + "\n")


if __name__ == "__main__":
    main()
