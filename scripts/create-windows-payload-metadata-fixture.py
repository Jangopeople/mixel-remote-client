#!/usr/bin/env python3
"""Write an inert x64 DLL-shaped PE with only a VERSIONINFO test resource.

The fixture has no code, imports or entry point and must never be executed. It
lets Windows' real FileVersionInfo exercise the production Store staging guard.
"""
import argparse
import struct
from pathlib import Path


def aligned(value, boundary=4):
    return (value + boundary - 1) // boundary * boundary


def version_block(key, value=b'', children=(), text=True):
    data = bytearray(struct.pack('<HHH', 0, len(value) // 2 if text else len(value), int(text)))
    data.extend((key + '\0').encode('utf-16le'))
    data.extend(b'\0' * (aligned(len(data)) - len(data)))
    data.extend(value)
    for child in children:
        data.extend(b'\0' * (aligned(len(data)) - len(data)))
        data.extend(child)
    struct.pack_into('<H', data, 0, len(data))
    return bytes(data)


def fixture(profile='valid'):
    fields = {
        'CompanyName': 'Mixel IT and Corporate Services GmbH',
        'FileDescription': 'Mixel Remote', 'FileVersion': '1.4.6',
        'InternalName': 'desktop_multi_window_plugin',
        'LegalCopyright': 'Copyright © 2026 Mixel IT and Corporate Services GmbH. All rights reserved.',
        'OriginalFilename': 'desktop_multi_window_plugin.dll',
        'ProductName': 'Mixel Remote', 'ProductVersion': '1.4.6',
    }
    if profile == 'retired':
        fields['CompanyName'] = 'com.carriez'
    elif profile not in ('valid', 'corrupt'):
        raise ValueError('Unknown resource fixture profile')
    strings = [version_block(key, (value + '\0').encode('utf-16le')) for key, value in fields.items()]
    table = version_block('StringFileInfo', children=[version_block('040904b0', children=strings)])
    translation = version_block('Translation', struct.pack('<HH', 0x409, 1200), text=False)
    variables = version_block('VarFileInfo', children=[translation])
    fixed = struct.pack('<13I', 0xFEEF04BD, 0x10000, 0x10004, 0x60000,
                        0x10004, 0x60000, 0x3F, 0, 0x40004, 2, 0, 0, 0)
    version = version_block('VS_VERSION_INFO', fixed, [table, variables], text=False)
    directory = b'\0' * 12 + struct.pack('<HH', 0, 1)
    resource = (directory + struct.pack('<II', 16, 0x80000018)
                + directory + struct.pack('<II', 1, 0x80000030)
                + directory + struct.pack('<II', 0x409, 72)
                + struct.pack('<IIII', 0x1000 + 88, len(version), 1200, 0) + version)
    if profile == 'corrupt':
        # Keep the PE/resource directory, but point its leaf outside the image.
        resource = bytearray(resource)
        struct.pack_into('<I', resource, 72, 0xFFFFF000)
        resource = bytes(resource)
    raw_size = aligned(len(resource), 0x200)
    dos = bytearray(128)
    dos[:2] = b'MZ'
    struct.pack_into('<I', dos, 0x3C, 128)
    optional = bytearray(240)
    for offset, fmt, value in [(0, 'H', 0x20B), (8, 'I', raw_size), (24, 'Q', 0x180000000),
                               (32, 'I', 0x1000), (36, 'I', 0x200), (40, 'H', 6),
                               (48, 'H', 6), (56, 'I', 0x2000), (60, 'I', 0x200),
                               (68, 'H', 2), (70, 'H', 0x160), (72, 'Q', 0x100000),
                               (80, 'Q', 0x1000), (88, 'Q', 0x100000),
                               (96, 'Q', 0x1000), (108, 'I', 16)]:
        struct.pack_into('<' + fmt, optional, offset, value)
    struct.pack_into('<II', optional, 112 + 2 * 8, 0x1000, len(resource))
    coff = struct.pack('<HHIIIHH', 0x8664, 1, 0, 0, 0, len(optional), 0x2022)
    section = struct.pack('<8sIIIIIIHHI', b'.rsrc\0\0\0', len(resource), 0x1000,
                          raw_size, 0x200, 0, 0, 0, 0, 0x40000040)
    headers = bytes(dos) + b'PE\0\0' + coff + optional + section
    assert len(headers) <= 0x200 and len(resource) <= 0x1000
    return headers.ljust(0x200, b'\0') + resource.ljust(raw_size, b'\0')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', choices=('valid', 'corrupt', 'retired'), default='valid')
    args = parser.parse_args()
    with args.output.open('xb') as stream:
        stream.write(fixture(args.profile))


if __name__ == '__main__':
    main()
