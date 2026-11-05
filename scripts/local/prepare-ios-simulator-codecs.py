#!/usr/bin/env python3
"""Project a pinned libvpx port that actually builds Apple simulator objects."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

PORT_SHA256 = "9b855096603a04b6bb55a36c5158d361255685b1d1dd07e22e0304365d9d85d3"
INSERT = r'''
# Mixel: libvpx's arm64-darwin configure path overrides the CMake SDK
# with iphoneos. Keep all of its optimized ARM code, but select the actual
# simulator SDK and simulator minimum-version flag before configure runs.
if(VCPKG_TARGET_IS_IOS AND VCPKG_OSX_SYSROOT STREQUAL "iphonesimulator")
    set(ENV{IOS_VERSION_MIN} "15.0")
    set(MIXEL_VPX_CONFIGURE "${SOURCE_PATH}/build/make/configure.sh")
    file(READ "${MIXEL_VPX_CONFIGURE}" MIXEL_VPX_CONFIGURE_TEXT)
    string(FIND "${MIXEL_VPX_CONFIGURE_TEXT}" "xcrun --sdk iphoneos --find" MIXEL_VPX_ANCHOR)
    if(MIXEL_VPX_ANCHOR EQUAL -1)
        message(FATAL_ERROR "Unexpected pinned libvpx Apple SDK selector")
    endif()
    string(REPLACE "iphoneos" "iphonesimulator" MIXEL_VPX_CONFIGURE_TEXT "${MIXEL_VPX_CONFIGURE_TEXT}")
    string(REPLACE "-miphonesimulator-version-min" "-mios-simulator-version-min" MIXEL_VPX_CONFIGURE_TEXT "${MIXEL_VPX_CONFIGURE_TEXT}")
    file(WRITE "${MIXEL_VPX_CONFIGURE}" "${MIXEL_VPX_CONFIGURE_TEXT}")
endif()

'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vcpkg-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = args.vcpkg_root.resolve(strict=True) / "ports/libvpx"
    original = (source / "portfile.cmake").read_bytes()
    if hashlib.sha256(original).hexdigest() != PORT_SHA256:
        raise ValueError("Unrecognized libvpx port; no overlay written")
    anchor = b"\nif(CMAKE_HOST_WIN32)\n"
    if original.count(anchor) != 1:
        raise ValueError("Unexpected pinned port insertion anchor")
    args.output.mkdir(parents=True, exist_ok=False)
    target = args.output / "libvpx"
    shutil.copytree(source, target)
    projected = original.replace(anchor, INSERT.encode() + anchor)
    (target / "portfile.cmake").write_bytes(projected)
    (args.output / "source.json").write_text(json.dumps({
        "upstreamPortSha256": PORT_SHA256,
        "overlayPortSha256": hashlib.sha256(projected).hexdigest(),
        "scope": "libvpx simulator SDK selection only; optimized codec sources retained"}, indent=2) + "\n")
    print("PASS: pinned libvpx simulator overlay prepared")


if __name__ == "__main__":
    main()
