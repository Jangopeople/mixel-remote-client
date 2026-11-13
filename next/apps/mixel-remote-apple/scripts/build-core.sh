#!/bin/bash
# build-core.sh [macos] [ios]: builds the Rust engine (crates/mixel_core_ffi) as static libraries,
# generates its Swift interface and packs Core/MixelCoreFFI.xcframework.
#   macos: Apple silicon + Intel (one universal library)
#   ios:   iPhone/iPad devices + Apple-silicon simulator (the controller engine)
# One build at a time and 4 compile jobs, so the Mac stays usable. Version: MIXEL_ENGINE_APP_VERSION.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
NEXT=$(cd "$HERE/../.." && pwd)
PLATFORMS=("$@"); [ ${#PLATFORMS[@]} -eq 0 ] && PLATFORMS=(macos ios)
export MIXEL_ENGINE_APP_VERSION=${MIXEL_ENGINE_APP_VERSION:-1.1.0}
export MACOSX_DEPLOYMENT_TARGET=13.0 IPHONEOS_DEPLOYMENT_TARGET=16.0
PROFILE=${PROFILE:-release}
JOBS=${JOBS:-4}
cd "$NEXT"
build() { cargo build -j "$JOBS" --profile "$PROFILE" -p mixel_core_ffi --lib --target "$1" ${FEATURES:+--features "$FEATURES"}; }
lib() { echo "$NEXT/target/$1/$PROFILE/libmixel_core_ffi.a"; }

OUT="$HERE/Core/build"; rm -rf "$OUT"; mkdir -p "$OUT/headers"
ARGS=()
for platform in "${PLATFORMS[@]}"; do
  case $platform in
    macos)
      build aarch64-apple-darwin; build x86_64-apple-darwin
      mkdir -p "$OUT/macos"
      lipo -create "$(lib aarch64-apple-darwin)" "$(lib x86_64-apple-darwin)" -output "$OUT/macos/libmixel_core_ffi.a"
      ARGS+=(-library "$OUT/macos/libmixel_core_ffi.a" -headers "$OUT/headers")
      BINDINGS_FROM=$(lib aarch64-apple-darwin)
      ;;
    ios)
      build aarch64-apple-ios; build aarch64-apple-ios-sim
      ARGS+=(-library "$(lib aarch64-apple-ios)" -headers "$OUT/headers" -library "$(lib aarch64-apple-ios-sim)" -headers "$OUT/headers")
      BINDINGS_FROM=${BINDINGS_FROM:-$(lib aarch64-apple-ios-sim)}
      ;;
    *) echo "unknown platform $platform" >&2; exit 2 ;;
  esac
done

# Swift interface, generated from the library itself (UniFFI library mode).
cargo build -j "$JOBS" -p mixel_core_ffi --features bindgen --bin uniffi-bindgen
"$NEXT/target/debug/uniffi-bindgen" generate --library "$BINDINGS_FROM" --language swift --out-dir "$OUT/bindings"
cp "$OUT/bindings/mixel_core_ffiFFI.h" "$OUT/headers/"
cp "$OUT/bindings/mixel_core_ffiFFI.modulemap" "$OUT/headers/module.modulemap"
cp "$OUT/bindings/mixel_core_ffi.swift" "$HERE/Sources/MixelCore/mixel_core_ffi.swift"

rm -rf "$HERE/Core/MixelCoreFFI.xcframework"
xcodebuild -create-xcframework "${ARGS[@]}" -output "$HERE/Core/MixelCoreFFI.xcframework" >/dev/null
rm -rf "$OUT"
echo "Core/MixelCoreFFI.xcframework ($(du -sh "$HERE/Core/MixelCoreFFI.xcframework" | cut -f1)) for ${PLATFORMS[*]}, engine $MIXEL_ENGINE_APP_VERSION"
