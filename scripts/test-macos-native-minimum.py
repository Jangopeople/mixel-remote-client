#!/usr/bin/env python3
"""Exercise the codec gate, including actual Apple SDK archive regressions."""
import importlib.util
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile

SPEC = importlib.util.spec_from_file_location('native_gate', Path(__file__).with_name('verify-macos-native-minimum.py'))
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def reject(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError('Invalid native archive accepted')


def native(arch='x86_64', minimum=(10, 14, 0), legacy=False, platform_id=1):
    packed = (minimum[0] << 16) | (minimum[1] << 8) | minimum[2] if minimum else 0
    command = (struct.pack('<4I', 0x24, 16, packed, 0) if legacy else struct.pack('<6I', 0x32, 24, platform_id, packed, 0, 0)) if minimum else b''
    return struct.pack('<8I', 0xFEEDFACF, gate.CPUS[arch], 3 if arch == 'x86_64' else 0, 1, int(bool(command)), len(command), 0, 0) + command


def member(name, payload):
    header = f'{name:<16}{0:<12}{0:<6}{0:<6}{0:<8}{len(payload):<10}`\n'.encode()
    assert len(header) == 60
    return header + payload + (b'\n' if len(payload) & 1 else b'')


with tempfile.TemporaryDirectory(prefix='mixel-codec-minimum-') as directory:
    root = Path(directory)
    archive = root / 'libtest.a'
    obj = native()
    for data in (b'!<arch>\n' + member('test.o/', obj),
                 b'!<arch>\n' + member('#1/12', b'test-long.o\0' + obj),
                 b'!<arch>\n' + member('//', b'test-long.o/\n') + member('/0', obj)):
        archive.write_bytes(data)
        proof = gate.verify_archive(archive, 'x86_64', (10, 14, 0))
        assert proof['member_count'] == proof['versioned_member_count'] == 1
        assert proof['maximum_encoded_minimum'] == '10.14.0'
    assert gate.object_metadata(native(legacy=True), 'x86_64', (10, 14, 0))['minimum'] == '10.14.0'
    assert gate.object_metadata(native('arm64', (12, 3, 0)), 'arm64', (12, 3, 0))['minimum'] == '12.3.0'
    archive.write_bytes(b'!<arch>\n' + member('test.o/', obj) + member('assembly.o/', native(minimum=None)))
    proof = gate.verify_archive(archive, 'x86_64', (10, 14, 0))
    assert proof['versioned_member_count'] == proof['unversioned_member_count'] == 1
    assert proof['members'][1]['minimum'] is None and proof['members'][1]['minimum_status'] == 'not-encoded'
    for data in (native(minimum=(14, 0, 0)), native('arm64', (12, 3, 0)), native(platform_id=2),
                 native(minimum=(0, 0, 0)), b'BC\xc0\xde', b'\x7fELF', obj[:31],
                 obj[:48], obj[:32] + struct.pack('<2I', 0x32, 4) + obj[40:],
                 obj[:52] + struct.pack('<I', 1)):
        reject(lambda data=data: gate.object_metadata(data, 'x86_64', (10, 14, 0)))
    for data in (b'', b'!<thin>\n', b'!<arch>\n', b'!<arch>\n' + member('/', b''),
                 b'!<arch>\n' + member('test.o/', obj)[:-1],
                 b'!<arch>\n' + member('/0', obj),
                 b'!<arch>\n' + member('#1/999', obj),
                 b'!<arch>\n' + member('assembly.o/', native(minimum=None))):
        archive.write_bytes(data)
        reject(lambda: gate.verify_archive(archive, 'x86_64', (10, 14, 0)))
    reject(lambda: gate.object_metadata(obj[:12] + struct.pack('<I', 2) + obj[16:], 'x86_64', (10, 14, 0)))
    reject(lambda: gate.object_metadata(obj[:20] + struct.pack('<I', 20) + obj[24:], 'x86_64', (10, 14, 0)))
    reject(lambda: gate.object_metadata(obj[:16] + struct.pack('<2I', 2, 48) + obj[24:] + obj[32:], 'x86_64', (10, 14, 0)))
    reject(lambda: gate.object_metadata(b'\xce\xfa\xed\xfe' + obj[4:28] + obj[32:], 'x86_64', (10, 14, 0)))
    for text in ('14', '10.14.bad', '-1.0', '10.14.0.0'):
        reject(lambda text=text: gate.version(text))
    print('PASS: BSD/GNU archives, legacy/current Mach-O versions, ARM/Intel and explicit unencoded-member records; malformed/newer/wrong-architecture/OS/archive controls rejected')
    if platform.system() == 'Darwin':
        source = root / 'codec.c'
        source.write_text('int mixel_codec_probe(void) { return 42; }\n')
        for arch, minimum in (('x86_64', '10.14'), ('arm64', '12.3')):
            object_path = root / (arch + '.o')
            for target in (minimum, '14.0'):
                subprocess.run(['xcrun', 'clang', '-arch', arch, '-mmacosx-version-min=' + target, '-c', str(source), '-o', str(object_path)], check=True, capture_output=True)
                archive.unlink(missing_ok=True)
                subprocess.run(['xcrun', 'ar', 'rcs', str(archive), str(object_path)], check=True, capture_output=True)
                if target == minimum:
                    proof = gate.verify_archive(archive, arch, gate.version(minimum))
                    assert proof['maximum_encoded_minimum'] == minimum + '.0'
                    reject(lambda: gate.verify_archive(archive, 'arm64' if arch == 'x86_64' else 'x86_64', gate.version(minimum)))
                    positive = archive.read_bytes()
                else:
                    reject(lambda: gate.verify_archive(archive, arch, gate.version(minimum)))
            print(f'PASS: actual Apple SDK {arch} archive minimum {minimum} accepted; actual newer macOS14.0 and opposite architecture rejected')
            library_dir = root / arch
            library_dir.mkdir()
            for name in gate.LIBRARIES:
                (library_dir / name).write_bytes(positive)
            command = [sys.executable, str(Path(__file__).with_name('verify-macos-native-minimum.py')),
                       '--lib-dir', str(library_dir), '--arch', arch, '--minimum', minimum]
            subprocess.run(command + ['--output', str(root / (arch + '.json'))], check=True)
            missing = library_dir / gate.LIBRARIES[0]
            missing.write_bytes(archive.read_bytes())  # actual newer14.0 object
            output = root / (arch + '.json')
            result = subprocess.run(command + ['--output', str(output)], capture_output=True)
            assert result.returncode != 0 and not output.exists()
            protected = root / (arch + '-protected.a')
            protected.write_bytes(positive)
            result = subprocess.run(command + ['--output', str(protected)], capture_output=True)
            assert result.returncode != 0 and protected.read_bytes() == positive
            missing.unlink()
            output = root / (arch + '-missing.json')
            result = subprocess.run(command + ['--output', str(output)], capture_output=True)
            assert result.returncode != 0 and not output.exists()
            missing.write_bytes(positive)
            (library_dir / 'libunexpected.a').write_bytes(positive)
            output = root / (arch + '-extra.json')
            result = subprocess.run(command + ['--output', str(output)], capture_output=True)
            assert result.returncode != 0 and not output.exists()
            print(f'PASS: actual {arch} six-archive CLI fixture writes complete proof; newer-OS same-output rerun removes prior success, protects non-JSON input, and missing/extra library creates no successful proof')
