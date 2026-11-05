#!/usr/bin/env python3
"""Verify real APK ARM64 ELF/RELRO and ZIP alignment; never infer runtime success."""
import argparse
import json
from pathlib import Path
import struct
import zipfile


def inspect(apk):
    results = []
    with zipfile.ZipFile(apk) as archive, apk.open('rb') as stream:
        for entry in archive.infolist():
            if not entry.filename.startswith('lib/arm64-v8a/') or not entry.filename.endswith('.so'):
                continue
            data = archive.read(entry)
            if data[:6] != b'\x7fELF\x02\x01' or len(data) < 64:
                raise ValueError(f'Not a little-endian ELF64 library: {entry.filename}')
            if struct.unpack_from('<H', data, 18)[0] != 183:
                raise ValueError(f'Not ARM64: {entry.filename}')
            offset = struct.unpack_from('<Q', data, 32)[0]
            size, count = struct.unpack_from('<HH', data, 54)
            if size != 56 or not count or offset + count * size > len(data):
                raise ValueError('Invalid ELF program header table')
            headers = [struct.unpack_from('<IIQQQQQQ', data, offset + i * size) for i in range(count)]
            loads = [h for h in headers if h[0] == 1]
            aligned = bool(loads) and all(h[7] >= 16384 and (h[3] - h[2]) % 16384 == 0 for h in loads)
            relro_ok = True
            for header in headers:
                if header[0] != 0x6474e552:
                    continue
                end = header[3] + header[6]
                if end % 16384:
                    containing = [h for h in loads if h[1] & 2 and h[3] <= header[3] < h[3] + h[6]]
                    if not containing or any(h[3] + h[6] != end for h in containing):
                        relro_ok = False
            stream.seek(entry.header_offset)
            local = stream.read(30)
            if len(local) != 30 or local[:4] != b'PK\x03\x04':
                raise ValueError('Invalid ZIP local header')
            name_length, extra_length = struct.unpack_from('<HH', local, 26)
            content_offset = entry.header_offset + 30 + name_length + extra_length
            compressed = entry.compress_type != zipfile.ZIP_STORED
            packaged = compressed or content_offset % 16384 == 0
            results.append({'library': entry.filename, 'elf16k': aligned,
                            'relro16k': relro_ok, 'compressed': compressed,
                            'zip16kOrExtracted': packaged,
                            'qualified': aligned and relro_ok and packaged})
    if not results:
        raise ValueError('APK contains no ARM64 native libraries')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    results = inspect(args.apk)
    report = {'binaryAlignmentQualified': all(r['qualified'] for r in results),
              'runtimeQualified': False, 'libraries': results}
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    for result in results:
        print(('PASS' if result['qualified'] else 'FAIL') + ': ' + result['library'] +
              f" ELF={result['elf16k']} RELRO={result['relro16k']} ZIP/extracted={result['zip16kOrExtracted']}")
    raise SystemExit(0 if report['binaryAlignmentQualified'] else 1)
