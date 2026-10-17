#!/usr/bin/env python3
"""Verify the actual customer app and every bundled Mach-O target slice."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import struct
import tempfile

NATIVE_PATH = Path(__file__).with_name('verify-macos-native-minimum.py')
SPEC = importlib.util.spec_from_file_location('mixel_native_minimum', NATIVE_PATH)
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)
FAT_MAGICS = {b'\xca\xfe\xba\xbe': ('>', 20), b'\xbe\xba\xfe\xca': ('<', 20),
              b'\xca\xfe\xba\xbf': ('>', 32), b'\xbf\xba\xfe\xca': ('<', 32)}
MINIMUMS = {'x86_64': '10.14', 'arm64': '12.3'}
REQUIRED = ('Contents/MacOS/Mixel-Remote', 'Contents/MacOS/service',
            'Contents/Frameworks/liblibrustdesk.dylib')
FILE_TYPES = (2, 6, 8)  # MH_EXECUTE, MH_DYLIB, MH_BUNDLE


def thin_header(data):
    if data[:4] not in native.MAGICS:
        raise ValueError('Universal member is not a thin Mach-O')
    endian, length = native.MAGICS[data[:4]]
    if len(data) < length:
        raise ValueError('Truncated Mach-O header')
    _, cpu, subtype, file_type = struct.unpack_from(endian + '4I', data)
    if file_type not in FILE_TYPES:
        raise ValueError('Bundle contains an unlinked or unsupported Mach-O file type')
    return cpu, subtype


def slices(data):
    """Bound every fat directory entry before selecting the actual target CPU."""
    if data[:4] in native.MAGICS:
        cpu, subtype = thin_header(data)
        return [{'cpu': cpu, 'cpu_subtype': subtype, 'offset': 0, 'bytes': len(data)}]
    if data[:4] not in FAT_MAGICS or len(data) < 8:
        raise ValueError('Missing or truncated Mach-O universal header')
    endian, entry_size = FAT_MAGICS[data[:4]]
    count = struct.unpack_from(endian + 'I', data, 4)[0]
    table_end = 8 + count * entry_size
    if not 1 <= count <= 64 or table_end > len(data):
        raise ValueError('Invalid universal architecture table extent')
    result, identities = [], set()
    for index in range(count):
        position = 8 + index * entry_size
        if entry_size == 32:
            cpu, subtype, offset, length, alignment, reserved = struct.unpack_from(endian + 'IIQQII', data, position)
            if reserved:
                raise ValueError('Nonzero universal64 reserved field')
        else:
            cpu, subtype, offset, length, alignment = struct.unpack_from(endian + '5I', data, position)
        if (not length or offset < table_end or offset > len(data) or length > len(data) - offset
                or alignment > 31 or offset % (1 << alignment)):
            raise ValueError('Invalid universal slice offset, size or alignment')
        if (cpu, subtype) in identities:
            raise ValueError('Duplicate universal architecture identity')
        identities.add((cpu, subtype))
        payload = data[offset:offset + length]
        if thin_header(payload) != (cpu, subtype):
            raise ValueError('Universal directory CPU/subtype differs from actual member header')
        result.append({'cpu': cpu, 'cpu_subtype': subtype, 'offset': offset, 'bytes': length})
    ordered = sorted(result, key=lambda item: item['offset'])
    if any(a['offset'] + a['bytes'] > b['offset'] for a, b in zip(ordered, ordered[1:])):
        raise ValueError('Overlapping universal slices')
    return result


def binary_metadata(data, arch, minimum):
    entries = slices(data)
    matches = [entry for entry in entries if entry['cpu'] == native.CPUS[arch]]
    if len(matches) != 1:
        raise ValueError('Binary must contain exactly one actual requested target architecture')
    report = []
    for entry in entries:
        payload = data[entry['offset']:entry['offset'] + entry['bytes']]
        actual_arch = next((name for name, cpu in native.CPUS.items() if cpu == entry['cpu']), None)
        target = entry is matches[0]
        if actual_arch is not None:
            metadata = native.object_metadata(payload, actual_arch,
                minimum if target else (65535, 255, 255), file_types=FILE_TYPES)
        else:
            metadata = {'architecture': 'other-cpu-' + str(entry['cpu']),
                        'minimum': None, 'minimum_status': 'other-architecture-not-inspected',
                        'sha256': hashlib.sha256(payload).hexdigest()}
        if target and metadata['minimum'] is None:
            raise ValueError('Target Mach-O has no encoded macOS minimum')
        report.append(dict(entry, **metadata, compared_to_advertised_minimum=target))
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'slices': report,
            'target_slice': next(item for item in report if item['compared_to_advertised_minimum'])}


def inside(path, root):
    if not path.is_relative_to(root):
        raise ValueError('Bundle link escapes its actual app root')
    return path


def verify_bundle(bundle, arch, advertised):
    if arch not in MINIMUMS or advertised != MINIMUMS[arch]:
        raise ValueError('Expected the literal architecture-specific customer macOS minimum')
    minimum = native.version(advertised)
    root = bundle.resolve(strict=True)
    if not root.is_dir() or root.suffix != '.app':
        raise ValueError('Expected an actual app bundle directory')
    info_path = inside((root / 'Contents/Info.plist').resolve(strict=True), root)
    info_bytes = info_path.read_bytes()
    info = plistlib.loads(info_bytes)
    executable = info.get('CFBundleExecutable')
    if (not isinstance(executable, str) or not executable or executable in ('.', '..')
            or any(character in executable for character in ('/', '\\', '\0'))):
        raise ValueError('Info.plist executable must be a safe basename')
    if info.get('CFBundleIdentifier') != 'ch.mixel.remote' or executable != 'Mixel-Remote':
        raise ValueError('Unexpected customer bundle identity or executable')
    if info.get('LSMinimumSystemVersion') != advertised:
        raise ValueError('Info.plist minimum differs from the actual architecture contract')
    required = {}
    for name in REQUIRED:
        path = inside((root / name).resolve(strict=True), root)
        if not path.is_file():
            raise ValueError('Required runner/core/service is not a regular file: ' + name)
        required[name] = (path.stat().st_dev, path.stat().st_ino)
    binaries, links, duplicates, seen = [], [], [], {}
    def walk_error(error):
        raise error
    for directory, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in sorted(directories + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                target = inside(path.resolve(strict=True), root)
                links.append({'path': relative, 'target': target.relative_to(root).as_posix()})
                continue
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError('Bundle contains a non-regular file: ' + relative)
            state = path.stat()
            identity = (state.st_dev, state.st_ino)
            if identity in seen:
                duplicates.append({'path': relative, 'original': seen[identity]})
                continue
            seen[identity] = relative
            with path.open('rb') as stream:
                magic = stream.read(4)
                if magic not in native.MAGICS and magic not in FAT_MAGICS:
                    continue
                stream.seek(0)
                data = stream.read()
            try:
                metadata = binary_metadata(data, arch, minimum)
            except ValueError as error:
                raise ValueError(relative + ': ' + str(error)) from error
            binaries.append(dict(path=relative, inode_identity=identity, **metadata))
    actual = {tuple(item['inode_identity']): item['path'] for item in binaries}
    if any(identity not in actual for identity in required.values()):
        raise ValueError('Required runner/core/service is missing an actual target Mach-O')
    kinds = {tuple(item['inode_identity']): item['target_slice']['file_type'] for item in binaries}
    if any(kinds[identity] != (6 if name.endswith('.dylib') else 2) for name, identity in required.items()):
        raise ValueError('Required runner/service must be executable and core must be a dylib')
    return {'status': 'passed', 'architecture': arch, 'advertised_minimum': advertised,
            'bundle_identifier': info['CFBundleIdentifier'], 'bundle_executable': executable,
            'info_plist_sha256': hashlib.sha256(info_bytes).hexdigest(),
            'native_parser_sha256': hashlib.sha256(NATIVE_PATH.read_bytes()).hexdigest(),
            'bundle_parser_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'scope': 'All actual unique Mach-O files; only requested target slices compared to advertised minimum. No older-OS runtime or embedded-codec claim.',
            'required_binaries': {name: actual[identity] for name, identity in required.items()},
            'binary_count': len(binaries), 'binaries': binaries,
            'internal_links': links, 'duplicate_inodes': duplicates}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    # Validate the architecture after clearing the requested proof so a mistyped
    # architecture on a rerun cannot leave an earlier successful JSON behind.
    parser.add_argument('--arch', metavar='{x86_64,arm64}', required=True)
    parser.add_argument('--minimum', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.suffix != '.json':
        raise ValueError('Proof output must be a JSON file')
    if args.output.resolve().is_relative_to(args.bundle.resolve()):
        raise ValueError('Proof output must be separate from the input app bundle')
    args.output.unlink(missing_ok=True)
    proof = verify_bundle(args.bundle, args.arch, args.minimum)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.mixel-bundle-proof-',
                                         dir=args.output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(proof, indent=2) + '\n')
        temporary.replace(args.output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f'PASS: actual runner/core/service and all {proof["binary_count"]} bundled Mach-O files contain one {args.arch} target slice with encoded macOS minimum <= {args.minimum}; complete file/slice hashes retained')


if __name__ == '__main__':
    main()
