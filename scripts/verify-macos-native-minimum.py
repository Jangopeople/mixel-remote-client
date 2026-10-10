#!/usr/bin/env python3
"""Verify every codec archive member before linking the customer macOS app."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import tempfile

LIBRARIES = ('libaom.a', 'libjpeg.a', 'libopus.a', 'libturbojpeg.a', 'libvpx.a', 'libyuv.a')
CPUS = {'x86_64': 0x01000007, 'arm64': 0x0100000C}
MAGICS = {b'\xcf\xfa\xed\xfe': ('<', 32), b'\xce\xfa\xed\xfe': ('<', 28),
          b'\xfe\xed\xfa\xcf': ('>', 32), b'\xfe\xed\xfa\xce': ('>', 28)}


def version(text):
    if not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?', text):
        raise ValueError('Invalid macOS version')
    parts = tuple(int(x) for x in text.split('.'))
    return parts + (0,) * (3 - len(parts))


def encoded_version(value):
    return (value >> 16, (value >> 8) & 255, value & 255)


def archive_members(data):
    if not data.startswith(b'!<arch>\n'):
        raise ValueError('Expected a complete ordinary static archive')
    offset, long_names, objects = 8, None, 0
    while offset < len(data):
        header = data[offset:offset + 60]
        if len(header) != 60 or header[58:] != b'`\n':
            raise ValueError('Invalid or truncated archive header')
        size_text = header[48:58].decode('ascii').strip()
        if not size_text.isdecimal():
            raise ValueError('Invalid archive member size')
        size = int(size_text)
        start, end = offset + 60, offset + 60 + size
        if end > len(data):
            raise ValueError('Truncated archive member')
        payload = data[start:end]
        name = header[:16].decode('ascii').rstrip()
        if name.startswith('#1/'):
            length_text = name[3:]
            if not length_text.isdecimal() or not 0 < int(length_text) <= len(payload):
                raise ValueError('Invalid BSD extended archive name')
            length = int(length_text)
            name = payload[:length].rstrip(b'\0').decode('utf-8')
            payload = payload[length:]
        elif name == '//':
            if long_names is not None:
                raise ValueError('Duplicate GNU archive name table')
            long_names = payload
        elif re.fullmatch(r'/[0-9]+', name):
            index = int(name[1:])
            if long_names is None or index >= len(long_names) or (index and long_names[index - 1:index] != b'\n'):
                raise ValueError('Unresolved GNU archive name')
            stop = long_names.find(b'/\n', index)
            if stop < 0:
                raise ValueError('Unterminated GNU archive name')
            name = long_names[index:stop].decode('utf-8')
        elif name not in ('/', '/SYM64/'):
            name = name.removesuffix('/')
        if name not in ('/', '//', '/SYM64/', '__.SYMDEF', '__.SYMDEF SORTED', '__.SYMDEF_64', '__.SYMDEF_64 SORTED'):
            if not name or '\0' in name or '\n' in name:
                raise ValueError('Invalid object member name')
            objects += 1
            yield objects, name, payload
        offset = end + (size & 1)
        if size & 1 and data[end:offset] != b'\n':
            raise ValueError('Missing archive member padding')
    if not objects:
        raise ValueError('Archive contains no native object members')


def object_metadata(data, arch, minimum, file_types=(1,)):
    try:
        endian, header_size = MAGICS[data[:4]]
    except KeyError as error:
        raise ValueError('Member is not a thin Mach-O native object') from error
    if len(data) < header_size:
        raise ValueError('Truncated Mach-O header')
    if header_size != 32:
        raise ValueError('Expected a 64-bit Mac native object')
    _, cpu, subtype, file_type, count, size, _ = struct.unpack_from(endian + '7I', data)
    if cpu != CPUS[arch] or file_type not in file_types:
        raise ValueError('Wrong native object architecture or file type')
    if count > 65536 or header_size + size > len(data):
        raise ValueError('Invalid native load-command extent')
    offset, end, minimums = header_size, header_size + size, []
    for _ in range(count):
        if offset + 8 > end:
            raise ValueError('Truncated native load command')
        command, length = struct.unpack_from(endian + '2I', data, offset)
        if length < 8 or length % 8 or offset + length > end:
            raise ValueError('Invalid native load command size')
        if command == 0x32:  # LC_BUILD_VERSION
            if length < 24:
                raise ValueError('Truncated native build version')
            platform, packed, _, tools = struct.unpack_from(endian + '4I', data, offset + 8)
            if platform != 1 or length != 24 + tools * 8:
                raise ValueError('Non-macOS or malformed native build version')
            minimums.append(encoded_version(packed))
        elif command == 0x24:  # LC_VERSION_MIN_MACOSX
            if length != 16:
                raise ValueError('Malformed legacy macOS minimum')
            minimums.append(encoded_version(struct.unpack_from(endian + 'I', data, offset + 8)[0]))
        elif command in (0x25, 0x2F, 0x30):
            raise ValueError('Non-macOS native minimum command')
        offset += length
    if offset != end or len(minimums) > 1:
        raise ValueError('Inconsistent native load-command coverage or minimums')
    if minimums and (minimums[0] < (10, 0, 0) or minimums[0] > minimum):
        raise ValueError('Native object requires a newer or invalid macOS version')
    return {'architecture': arch, 'cpu_subtype': subtype, 'file_type': file_type,
            'minimum': '.'.join(map(str, minimums[0])) if minimums else None,
            'minimum_status': 'versioned' if minimums else 'not-encoded',
            'sha256': hashlib.sha256(data).hexdigest()}


def verify_archive(path, arch, minimum):
    data = path.read_bytes()
    members = []
    for index, name, payload in archive_members(data):
        try:
            metadata = object_metadata(payload, arch, minimum)
        except ValueError as error:
            raise ValueError(f'{path.name} member {index} ({name}): {error}') from error
        members.append(dict(index=index, name=name, **metadata))
    versioned = [item for item in members if item['minimum'] is not None]
    if not versioned:
        raise ValueError(f'{path.name}: no versioned native members to establish its deployment target')
    return {'name': path.name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
            'member_count': len(members), 'versioned_member_count': len(versioned),
            'unversioned_member_count': len(members) - len(versioned),
            'maximum_encoded_minimum': max((item['minimum'] for item in versioned), key=version),
            'members': members}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lib-dir', type=Path, required=True)
    parser.add_argument('--arch', required=True)
    parser.add_argument('--minimum', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.suffix != '.json':
        raise ValueError('Proof output must be a JSON file, separate from native input archives')
    # A failed rerun must never upload an older successful invocation's proof.
    args.output.unlink(missing_ok=True)
    if args.arch not in CPUS:
        raise ValueError('Native codec architecture must be x86_64 or arm64')
    minimum = version(args.minimum)
    actual = tuple(sorted(path.name for path in args.lib_dir.glob('*.a')))
    if actual != LIBRARIES:
        raise ValueError(f'Expected exactly the six codec archives, found {actual}')
    archives = [verify_archive(args.lib_dir / name, args.arch, minimum) for name in LIBRARIES]
    proof = {'status': 'passed', 'architecture': args.arch, 'advertised_minimum': args.minimum,
             'scope': 'All native codec archive members; unencoded member minimums are recorded explicitly, not inferred.',
             'archives': archives}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.mixel-codec-proof-',
                                         dir=args.output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(proof, indent=2) + '\n')
        temporary.replace(args.output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f'PASS: all {len(archives)} codec archives/{sum(a["member_count"] for a in archives)} native members match {args.arch}; every encoded macOS minimum <= {args.minimum}; complete member hashes retained')


if __name__ == '__main__':
    main()
