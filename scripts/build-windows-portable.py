#!/usr/bin/env python3
"""Package the signed desktop payload; fail if the portable Cargo build fails."""
import argparse
import importlib.util
import struct
import xml.etree.ElementTree as ET
import os
import shutil
import subprocess
from pathlib import Path


PORTABLE_MANIFEST_ORIGINAL = '<?xml version=\'1.0\' encoding=\'UTF-8\' standalone=\'yes\'?>\n<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">\n  <asmv3:application xmlns:asmv3="urn:schemas-microsoft-com:asm.v3">\n    <asmv3:windowsSettings\n         xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">\n      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/PM</dpiAware>\n      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2, PerMonitor</dpiAwareness>\n    </asmv3:windowsSettings>\n  </asmv3:application>\n  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1"> \n        <application> \n            <!-- Windows 10 --> \n            <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>\n            <!-- Windows 8.1 -->\n            <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}"/>\n            <!-- Windows Vista -->\n            <supportedOS Id="{e2011457-1546-43c5-a5fe-008deee3d3f0}"/> \n            <!-- Windows 7 -->\n            <supportedOS Id="{35138b9a-5d96-4fbd-8e2d-a2440225f93a}"/>\n            <!-- Windows 8 -->\n            <supportedOS Id="{4a2f28e3-53b9-4441-ba9c-d69d4a4a6e38}"/>\n        </application> \n  </compatibility>\n  <dependency>\n      <dependentAssembly>\n          <assemblyIdentity\n              type="win32"\n              name="Microsoft.Windows.Common-Controls"\n              version="6.0.0.0"\n              processorArchitecture="*"\n              publicKeyToken="6595b64144ccf1df"\n              language="*"\n          />\n      </dependentAssembly>\n  </dependency>\n</assembly>\n'
PORTABLE_AS_INVOKER = '  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">\n    <security><requestedPrivileges>\n      <requestedExecutionLevel level="asInvoker" uiAccess="false" />\n    </requestedPrivileges></security>\n  </trustInfo>\n'


def validate_manifest_xml(raw: bytes) -> None:
    if not raw or len(raw) > 1024 * 1024:
        raise ValueError("Missing or oversized portable execution manifest")
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Portable manifest must not contain entity declarations")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError("Malformed portable execution manifest") from error
    namespace = "{urn:schemas-microsoft-com:asm.v3}"
    levels = [node for node in root.iter() if node.tag.split('}')[-1] == 'requestedExecutionLevel']
    expected = root.findall(namespace + 'trustInfo/' + namespace + 'security/' +
                            namespace + 'requestedPrivileges/' + namespace + 'requestedExecutionLevel')
    if root.tag != '{urn:schemas-microsoft-com:asm.v1}assembly' or len(levels) != 1 or expected != levels:
        raise ValueError("Portable manifest requires one explicit execution policy")
    if levels[0].attrib != {'level': 'asInvoker', 'uiAccess': 'false'}:
        raise ValueError("Portable launcher must be asInvoker with uiAccess=false")


def prepare_portable_manifest(repo: Path) -> None:
    path = repo / 'res/manifest.xml'
    raw = path.read_bytes()
    text = raw.decode('utf-8')
    ending = '\r\n' if '\r\n' in text else '\n'
    logical = text.replace('\r\n', '\n')
    if '\r' in logical or (ending == '\r\n' and '\n' in text.replace('\r\n', '')):
        raise ValueError("Mixed portable manifest newlines")
    before = logical.replace(PORTABLE_AS_INVOKER, '', 1) if logical.count(PORTABLE_AS_INVOKER) == 1 else logical
    if before != PORTABLE_MANIFEST_ORIGINAL:
        raise ValueError("Pinned portable manifest changed; refusing all writes")
    anchor = '<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">\n'
    if before.count(anchor) != 1:
        raise ValueError("Pinned portable manifest assembly changed")
    fixed = before.replace(anchor, anchor + PORTABLE_AS_INVOKER, 1)
    validate_manifest_xml(fixed.encode('utf-8'))
    path.write_bytes(fixed.replace('\n', ending).encode('utf-8'))


def read_pe_manifests(raw: bytes) -> list[tuple[int, int, bytes]]:
    """Read RT_MANIFEST bytes without mapping or executing native code."""
    def unpack(fmt, offset):
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(raw):
            raise ValueError("Truncated PE structure")
        return struct.unpack_from(fmt, raw, offset)
    if len(raw) < 64 or raw[:2] != b'MZ':
        raise ValueError("Portable launcher is not a PE image")
    pe, = unpack('<I', 60)
    if raw[pe:pe + 4] != b'PE\0\0':
        raise ValueError("Portable PE signature is invalid")
    count, = unpack('<H', pe + 6)
    optional_size, = unpack('<H', pe + 20)
    optional = pe + 24
    magic, = unpack('<H', optional)
    directory_start, count_offset = {0x10b: (96, 92), 0x20b: (112, 108)}.get(magic, (0, 0))
    if not directory_start or optional_size < directory_start + 24 or not 1 <= count <= 96:
        raise ValueError("Unsupported portable PE headers")
    directory_count, = unpack('<I', optional + count_offset)
    if directory_count < 3:
        raise ValueError("Portable PE has no resource directory")
    resource_rva, resource_size = unpack('<II', optional + directory_start + 16)
    if not resource_rva or not 16 <= resource_size <= 16 * 1024 * 1024:
        raise ValueError("Portable PE resource bounds are invalid")
    sections = []
    for i in range(count):
        entry = optional + optional_size + i * 40
        virtual_size, rva, size, pointer = unpack('<IIII', entry + 8)
        if pointer + size > len(raw):
            raise ValueError("Truncated portable PE section")
        sections.append((rva, min(size, virtual_size or size), pointer))
    def at(rva, size):
        matches = [pointer + rva - start for start, span, pointer in sections
                   if size >= 0 and start <= rva and rva + size <= start + span]
        if len(matches) != 1:
            raise ValueError("Unmapped or ambiguous portable resource RVA")
        return raw[matches[0]:matches[0] + size]
    resource = at(resource_rva, resource_size)
    def directory(offset):
        if offset < 0 or offset + 16 > len(resource):
            raise ValueError("Truncated portable resource directory")
        named, ids = struct.unpack_from('<HH', resource, offset + 12)
        count = named + ids
        if count > 256 or offset + 16 + count * 8 > len(resource):
            raise ValueError("Invalid portable resource directory entries")
        return [struct.unpack_from('<II', resource, offset + 16 + i * 8) for i in range(count)]
    types = [value for name, value in directory(0) if name == 24]
    if len(types) != 1 or not types[0] & 0x80000000:
        raise ValueError("Portable PE requires one RT_MANIFEST type")
    manifests = []
    for name, names in directory(types[0] & 0x7fffffff):
        if name != 1 or not names & 0x80000000:
            raise ValueError("Portable PE manifest must use executable resource1")
        for language, leaf in directory(names & 0x7fffffff):
            if language > 65535 or leaf & 0x80000000 or leaf + 16 > len(resource):
                raise ValueError("Invalid portable manifest language/data entry")
            rva, size, _, reserved = struct.unpack_from('<IIII', resource, leaf)
            if not 1 <= size <= 1024 * 1024 or reserved:
                raise ValueError("Invalid portable manifest data bounds")
            manifests.append((name, language, at(rva, size)))
    if not manifests or len({(name, language) for name, language, _ in manifests}) != len(manifests):
        raise ValueError("Missing or duplicate portable execution manifest")
    return manifests


def verify_portable_manifest(binary: Path) -> None:
    manifests = read_pe_manifests(binary.read_bytes())
    for _, _, raw in manifests:
        validate_manifest_xml(raw)


def build_portable(repo: Path, payload: Path, output: Path, runner=subprocess.run) -> None:
    repo, payload, output = repo.resolve(), payload.resolve(), output.resolve()
    entry = payload / "Mixel-Remote.exe"
    if not entry.is_file():
        raise ValueError("Signed compiled Mixel-Remote.exe is required for portable packaging")
    prepare_portable_manifest(repo)
    portable = repo / "libs/portable"
    specification = importlib.util.spec_from_file_location("mixel_portable_generator", portable / "generate.py")
    generator = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(generator)
    original_directory = Path.cwd()
    try:
        table = generator.generate_md5_table(str(payload), 11)
        generator.write_package_metadata(table, str(portable), "./Mixel-Remote.exe")
        generator.write_app_metadata(str(portable))
        # Upstream generate.py ignores the exit status from os.system(cargo).
        # Execute Cargo ourselves and never reuse a stale launcher on failure.
        binary = repo / "target/release/rustdesk-portable-packer.exe"
        binary.unlink(missing_ok=True)
        runner(["cargo", "build", "--locked", "--release", "--manifest-path", str(portable / "Cargo.toml")],
               cwd=repo, check=True)
        if not binary.is_file():
            raise RuntimeError("Portable Cargo build did not produce its executable")
        verify_portable_manifest(binary)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(binary, output)
    finally:
        os.chdir(original_directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("rustdesk"))
    parser.add_argument("--payload", type=Path, default=Path("store-payload"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-manifest", type=Path, help="Read the final PE manifest without executing the image")
    args = parser.parse_args()
    if args.verify_manifest:
        if args.output:
            parser.error("--verify-manifest cannot package an output")
        verify_portable_manifest(args.verify_manifest)
        print("PASS: final portable PE manifest is explicitly asInvoker/uiAccess=false in every language")
    else:
        if not args.output:
            parser.error("--output is required for portable packaging")
        build_portable(args.repo, args.payload, args.output)
        print("PASS: portable launcher packages the signed compiled application with explicit asInvoker manifest")
