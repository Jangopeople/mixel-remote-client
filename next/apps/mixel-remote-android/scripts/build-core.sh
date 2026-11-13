#!/bin/bash
# build-core.sh [release|debug] [abi…]: builds the Rust engine (crates/mixel_core_ffi) as Android
# libraries with the NDK and generates its Kotlin interface (UniFFI library mode).
#   release (default): arm64-v8a, armeabi-v7a and x86_64 (phones, old phones, emulators on PCs)
#   debug:             arm64-v8a only (the emulator on Apple silicon and most phones)
# One build at a time and 4 compile jobs (JOBS), so the Mac stays usable. Version: MIXEL_ENGINE_APP_VERSION.
# The public service configuration is embedded in the library (deployment/public-service-config.json).
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
NEXT=$(cd "$HERE/../.." && pwd)
MODE=${1:-release}; shift || true
ABIS=("$@")
if [ ${#ABIS[@]} -eq 0 ]; then
  if [ "$MODE" = debug ]; then ABIS=(arm64-v8a); else ABIS=(arm64-v8a armeabi-v7a x86_64); fi
fi
JOBS=${JOBS:-4}
NDK_HOME=${NDK_HOME:-$HOME/Library/Android/sdk/ndk/28.2.13676358}
BIN="$NDK_HOME/toolchains/llvm/prebuilt/darwin-x86_64/bin"
export MIXEL_ENGINE_APP_VERSION=${MIXEL_ENGINE_APP_VERSION:-$(sed -n 's/^ *versionName = "\(.*\)"/\1/p' "$HERE/app/build.gradle.kts" | head -1)}
export MIXEL_DESKTOP_SERVICE_CONFIG=${MIXEL_DESKTOP_SERVICE_CONFIG:-$NEXT/deployment/public-service-config.json}
[ -f "$MIXEL_DESKTOP_SERVICE_CONFIG" ] || { echo "missing $MIXEL_DESKTOP_SERVICE_CONFIG" >&2; exit 2; }
PROFILE=release; [ "$MODE" = debug ] && PROFILE=dev
DIR=release; [ "$MODE" = debug ] && DIR=debug
cd "$NEXT"
FIRST=""
for abi in "${ABIS[@]}"; do
  case $abi in
    arm64-v8a) target=aarch64-linux-android; clang=aarch64-linux-android26 ;;
    armeabi-v7a) target=armv7-linux-androideabi; clang=armv7a-linux-androideabi26 ;;
    x86_64) target=x86_64-linux-android; clang=x86_64-linux-android26 ;;
    *) echo "unknown ABI $abi" >&2; exit 2 ;;
  esac
  upper=$(echo "$target" | tr 'a-z-' 'A-Z_'); lower=$(echo "$target" | tr '-' '_')
  env "CC_$lower=$BIN/$clang-clang" "CXX_$lower=$BIN/$clang-clang++" "AR_$lower=$BIN/llvm-ar" \
      "CARGO_TARGET_${upper}_LINKER=$BIN/$clang-clang" \
      cargo rustc -j "$JOBS" --profile "$PROFILE" -p mixel_core_ffi --lib --crate-type cdylib --target "$target" ${FEATURES:+--features "$FEATURES"}
  out="$HERE/app/src/main/jniLibs/$abi"; mkdir -p "$out"
  cp "target/$target/$DIR/libmixel_core_ffi.so" "$out/libmixel_core_ffi.so"
  # libc++_shared: openh264 is C++ (the NDK's shared C++ runtime ships next to the engine).
  triple=$target; [ "$target" = armv7-linux-androideabi ] && triple=arm-linux-androideabi
  cp "$NDK_HOME/toolchains/llvm/prebuilt/darwin-x86_64/sysroot/usr/lib/$triple/libc++_shared.so" "$out/"
  [ -z "$FIRST" ] && FIRST="target/$target/$DIR/libmixel_core_ffi.so"
  "$BIN/llvm-strip" "$out/libmixel_core_ffi.so" "$out/libc++_shared.so"
  echo "$abi: $(du -h "$out/libmixel_core_ffi.so" | cut -f1)"
done

# Kotlin interface, generated from the library itself.
cargo build -j "$JOBS" -p mixel_core_ffi --features bindgen --bin uniffi-bindgen
GEN="$HERE/app/src/main/java"
rm -rf "$GEN/uniffi"
"$NEXT/target/debug/uniffi-bindgen" generate --library "$FIRST" --language kotlin --no-format --out-dir "$GEN"
echo "engine $MIXEL_ENGINE_APP_VERSION for ${ABIS[*]} ($MODE)"
