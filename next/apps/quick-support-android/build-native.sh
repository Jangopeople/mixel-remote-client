#!/bin/bash
# Builds the host core (crates/quick_host) for Android and puts it where the app expects it.
#   ./build-native.sh            release, all phone processor types
#   ./build-native.sh debug      debug, arm64 only (emulator on Apple silicon, most phones)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
ndk="${NDK_HOME:-$HOME/Library/Android/sdk/ndk/28.2.13676358}/toolchains/llvm/prebuilt/darwin-x86_64/bin"
mode="${1:-release}"
if [ "$mode" = debug ]; then targets="aarch64-linux-android:arm64-v8a:aarch64-linux-android26"; flag=""; else
  targets="aarch64-linux-android:arm64-v8a:aarch64-linux-android26 armv7-linux-androideabi:armeabi-v7a:armv7a-linux-androideabi26 x86_64-linux-android:x86_64:x86_64-linux-android26"; flag="--release"; fi
cd "$here/../.."
for entry in $targets; do
  IFS=: read -r target abi clang <<< "$entry"
  upper="$(echo "$target" | tr 'a-z-' 'A-Z_')"; lower="$(echo "$target" | tr '-' '_')"
  env "CC_$lower=$ndk/$clang-clang" "AR_$lower=$ndk/llvm-ar" "CARGO_TARGET_${upper}_LINKER=$ndk/$clang-clang" \
    cargo build -p quick_host --lib --target "$target" $flag
  mkdir -p "$here/app/src/main/jniLibs/$abi"
  cp "target/$target/$mode/libquick_host.so" "$here/app/src/main/jniLibs/$abi/libquick_host.so"
  "$ndk/llvm-strip" "$here/app/src/main/jniLibs/$abi/libquick_host.so"
  ls -la "$here/app/src/main/jniLibs/$abi/libquick_host.so" | awk '{print $5, $9}'
done
