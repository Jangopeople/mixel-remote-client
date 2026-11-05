# Local mobile QA builds

These targets extend the desktop-branded RustDesk 1.4.6 source to Android and
iOS. They produce private development packages, not customer or marketplace
releases. A successful package build does not establish runtime qualification.
No physical phone is needed to compile either package.

## Source preparation

Use a separate copy of the complete, generated desktop-branded checkout. Do not
modify a previously qualified desktop build tree or copy its desktop native
libraries into a mobile application. Preserve the pinned Cargo.lock and the
generated Dart/Rust bridge and iOS bridge header.

`scripts/patch-mobile-identity.py` validates mandatory anchors before changing
source. It completes the Android application ID, manifest, application-class
namespace, Kotlin native loader, mobile labels, iOS bundle identity and URL
scheme. The internal `ffi` JNI package and its exported Rust symbols stay fixed.
The Android native library must be packaged as `libmixel-remote.so` to agree with
both Kotlin and the branded Dart loader. The iOS project selects its Rust archive
by actual device/simulator SDK rather than linking a device archive on simulators.
The new local iOS target has a minimum deployment version of iOS 15.
The simulator app and Pods select ARM64 explicitly; Intel simulators are not qualified.

Run the source regressions against a complete desktop-branded source directory:

```sh
python3 scripts/test-mobile-identity.py --source /absolute/branded/source
```

## Native dependencies

Use the same pinned vcpkg revision as the desktop build, in an independent local
dependency workspace. Build `libvpx`, `libyuv`, `opus`, `aom`, and `libjpeg-turbo`
for the selected target, plus `libsodium`. Android additionally needs `cpu-features` and `oboe`.
Target-specific pkg-config selects actual Android/simulator sodium archives while
host helpers resolve a real Mac library. Do not set a global `SODIUM_LIB_DIR`.
The bundled Android sodium builder produced an empty archive in live testing;
the driver now requires target-native sodium and links with `--no-undefined`.
The corrected R6 APK passed four actual tests on each of the 4 KB Android 14
and 16 KB Android 15 ARM emulators, with exact installed APK readbacks.

For Apple Silicon simulators, the codec triplet is named `arm64-ios` (as required
by the pinned Rust build scripts) with `VCPKG_OSX_SYSROOT=iphonesimulator`, not
the device SDK. Build `libsodium` in that same simulator triplet. The pinned
libsodium-sys builder does not recognize `aarch64-apple-ios-sim`; the driver links
the actual simulator-native static library through target-specific pkg-config.
The crate is also a host build dependency: a global `SODIUM_LIB_DIR` would
incorrectly link simulator objects into Mac build executables. Host pkg-config
must resolve a real Mac libsodium, independently of the simulator path. Neither
Mac nor iPhone-device libraries are substitutes for simulator libraries.

Run `scripts/local/prepare-ios-simulator-codecs.py` against the pinned vcpkg
root to create a new overlay directory, then supply it as `--overlay-ports` when
building libvpx. Its upstream ARM configure path otherwise overrides the SDK
and emits device objects even when CMake selects the simulator. The build driver
checks every codec archive for simulator platform tags and invalidates the
release scrap cache when codec inputs change.

Android uses NDK `28.2.13676358`, an independent Android SDK directory, JDK 17,
and Rust target `aarch64-linux-android`. The initial private QA package has a
minimum of Android API 28, matching the pinned codec triplet's actual build
floor; support for earlier Android versions is not qualified. The initial
private target SDK is 33, compile SDK 34; this is not a store-ready release. Install SDK packages using existing
accepted SDK licenses. The driver uses private Flutter/Gradle configuration
directories. It refuses legacy global Flutter configuration that would override
that isolation rather than modifying the user's settings.

## Build driver

`scripts/local/build-mobile.py` accepts:

- `--platform android` or `--platform ios-simulator`
- `--root`: complete generated source checkout
- `--output`: a new directory for command logs, results and the package
- `--flutter-sdk`: the pinned, already installed Flutter SDK directory
- `--vcpkg-root`: dependency workspace with the correct target libraries
- Android: `--android-sdk` and `--jdk-home`
- Optional `--cargo-home`: existing dependency cache

The driver runs the mobile source projection and renders every mobile launcher
icon from the Mixel 1024px source. It compiles the real Rust core before building
the Flutter package. Android output is a debug APK; iOS output is an unsigned
simulator app. The result records package success separately from runtime proof
and verifies that Cargo.lock remains unchanged. Commands and failures are kept
in the supplied output directory. Failed output directories are not reused.

## Runtime acceptance

Use dedicated emulators/simulators for development acceptance. Verify branded
cold launch, native-core load, relay registration, ordinary consent and actual
input/clipboard/file flows against an independent test desktop. Do not infer
these results from compilation, source presence or mocked OS effects. Record
the target platform, package/core hashes and emulation limits with the result.

The broader user-selected scope also includes enterprise management, mobile
support, endpoint management and AI. These mobile build targets alone do not
implement or qualify that entire portfolio.

## Android native runtime runner

`scripts/local/test-android-native.py` takes `--root`, a new `--output`,
`--adb`, `--jdk-home`, a warm `--gradle-home`, an emulator-only `--serial`,
the exact `--application-apk`, and `--page-size 4096` or `--page-size 16384`. It builds only the app instrumentation target,
installs that supplied APK, reads the installed base APK back and verifies its
hash, then executes four actual tests, including the kernel page size and native JNI. It never claims UI/consent/physical
device qualification from these headless tests. Logs and the result retain
package, core and fixture hashes. No phone devices are required.

On this Xcode installation, iOS simulator UI is hosted by Device Hub
(`com.apple.dt.Devices`), rather than a standalone Simulator.app. The dedicated
iOS 26.5 simulator was exercised against PC01: cold launch, real relay connection,
ordinary blue Accept, live video and native keyboard input passed. An independent
Windows console confirmed `iosproof`; reconnect requested fresh consent and a
Windows rejection closed the mobile request. This is simulator viewer proof,
not iPhone screen-sharing or full portfolio acceptance.

The earlier R4 Android package was rejected for 16 KB page compatibility.
The current driver uses NDK r28, explicit maximum/common page-size flags and
`scripts/local/check-android-pages.py` to validate every packaged ARM64 library's
ELF LOAD segments, RELRO boundary and APK ZIP alignment/extraction. With pinned
AGP 7.3, the mobile projection uses Android's documented compressed-JNI path
and the corresponding bundle property. This consumes additional installed disk
space. It retains native-core symbols so Gradle cannot silently rewrite the
exact Rust library; the APK core must match the compiled file byte for byte.

The pinned Flutter engine itself already has 64 KB ELF alignment. An engine
upgrade is not inferred necessary from a ZIP alignment warning. These binary
checks still do not establish runtime compatibility: execute the native runner
on a dedicated emulator whose actual kernel reports 16384 bytes and retain the
exact installed APK readback and results. Android UI/consent acceptance remains
separate from headless JNI acceptance.

References: [Android's 16 KB implementation and testing guide](https://developer.android.com/guide/practices/page-sizes).
