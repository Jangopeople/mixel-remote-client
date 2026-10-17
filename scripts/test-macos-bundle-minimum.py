#!/usr/bin/env python3
"""Qualify complete target-slice bundle validation and stale-proof rejection."""
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import platform
import plistlib
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).with_name('verify-macos-bundle-minimum.py')
SPEC = importlib.util.spec_from_file_location('mixel_bundle_minimum', PATH)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def macho(arch='x86_64', minimum=(10, 14, 0), kind=6, endian='<', subtype=0, encoded=True):
    command = struct.pack(endian + '6I', 0x32, 24, 1,
                          minimum[0] << 16 | minimum[1] << 8 | minimum[2], 0, 0) if encoded else b''
    return struct.pack(endian + '8I', 0xFEEDFACF, gate.native.CPUS[arch], subtype,
                       kind, int(encoded), len(command), 0, 0) + command


def universal(payloads, *, endian='>', wide=False):
    entry_size, cursor = (32 if wide else 20), 0
    cursor = 8 + len(payloads) * entry_size
    content = bytearray(cursor)
    struct.pack_into(endian + '2I', content, 0, 0xCAFEBABF if wide else 0xCAFEBABE, len(payloads))
    for index, payload in enumerate(payloads):
        cpu, subtype = gate.thin_header(payload)
        position = (cursor + 7) & ~7
        content.extend(b'\0' * (position - len(content)))
        content.extend(payload)
        if wide:
            struct.pack_into(endian + 'IIQQII', content, 8 + index * entry_size, cpu, subtype, position, len(payload), 3, 0)
        else:
            struct.pack_into(endian + '5I', content, 8 + index * entry_size, cpu, subtype, position, len(payload), 3)
        cursor = position + len(payload)
    return bytes(content)


def bundle(folder, arch='x86_64', payloads=None):
    root = folder / 'Mixel-Remote.app'
    info = {'CFBundleIdentifier': 'ch.mixel.remote', 'CFBundleExecutable': 'Mixel-Remote',
            'LSMinimumSystemVersion': gate.MINIMUMS[arch]}
    (root / 'Contents').mkdir(parents=True)
    (root / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    minimum = gate.native.version(info['LSMinimumSystemVersion'])
    for path in gate.REQUIRED:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payloads[path] if payloads else macho(arch, minimum, 6 if path.endswith('.dylib') else 2))
    return root


class BundleMinimumTests(unittest.TestCase):
    def test_actual_thin_target_endianness_and_complete_hashes(self):
        for arch in gate.MINIMUMS:
            for endian in ('<', '>'):
                data = macho(arch, gate.native.version(gate.MINIMUMS[arch]), endian=endian)
                metadata = gate.binary_metadata(data, arch, gate.native.version(gate.MINIMUMS[arch]))
                self.assertEqual(metadata['target_slice']['minimum'], gate.MINIMUMS[arch] + '.0')
                self.assertEqual(metadata['sha256'], hashlib.sha256(data).hexdigest())
                self.assertEqual(metadata['target_slice']['sha256'], metadata['sha256'])

    def test_fat32_and64_both_endians_select_target_without_miscomparing_other_arch_minimum(self):
        x86, arm = macho(), macho('arm64', (12, 3, 0))
        for endian in ('<', '>'):
            for wide in (False, True):
                data = universal([arm, x86], endian=endian, wide=wide)
                report = gate.binary_metadata(data, 'x86_64', (10, 14, 0))
                self.assertEqual(report['target_slice']['sha256'], hashlib.sha256(x86).hexdigest())
                self.assertEqual(report['slices'][0]['minimum'], '12.3.0')
                self.assertFalse(report['slices'][0]['compared_to_advertised_minimum'])
                self.assertEqual(report['slices'][1]['bytes'], len(x86))

    def test_malformed_universal_boundaries_identity_and_overlap_are_rejected(self):
        good = universal([macho(), macho('arm64', (12, 3, 0))])
        changes = [(4, 0), (4, 65), (16, 0), (16, len(good) + 1), (20, 0), (20, len(good)),
                   (24, 32), (24, 6), (8, gate.native.CPUS['arm64']), (12, 4)]
        for offset, value in changes:
            broken = bytearray(good)
            struct.pack_into('>I', broken, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                gate.binary_metadata(bytes(broken), 'x86_64', (10, 14, 0))
        for wide in (False, True):
            for endian in ('<', '>'):
                data = universal([macho(), macho('arm64', (12, 3, 0))], wide=wide, endian=endian)
                for cut in (0, 4, 7, 12, len(data) - 1):
                    with self.subTest(wide=wide, endian=endian, cut=cut), self.assertRaises(ValueError):
                        gate.binary_metadata(data[:cut], 'x86_64', (10, 14, 0))
        duplicated = universal([macho(), macho()])
        with self.assertRaisesRegex(ValueError, 'Duplicate universal'):
            gate.binary_metadata(duplicated, 'x86_64', (10, 14, 0))
        two_targets = universal([macho(subtype=0), macho(subtype=3)])
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            gate.binary_metadata(two_targets, 'x86_64', (10, 14, 0))
        overlap = bytearray(good)
        # Both distinct member headers stay valid; only their file extents
        # overlap, so this exercises the overlap gate rather than duplicate CPU.
        struct.pack_into('>I', overlap, 20, len(good) - struct.unpack_from('>I', good, 16)[0])
        with self.assertRaisesRegex(ValueError, 'Overlapping universal'):
            gate.binary_metadata(bytes(overlap), 'x86_64', (10, 14, 0))
        reserved = bytearray(universal([macho()], wide=True))
        struct.pack_into('>I', reserved, 36, 1)
        with self.assertRaisesRegex(ValueError, 'reserved'):
            gate.binary_metadata(bytes(reserved), 'x86_64', (10, 14, 0))

    def test_target_slice_wrong_arch_newer_os_and_unversioned_are_rejected(self):
        for data in (macho('arm64', (12, 3, 0)), macho(minimum=(14, 0, 0)), macho(encoded=False),
                     universal([macho('arm64', (12, 3, 0))]), macho(kind=1)):
            with self.subTest(digest=hashlib.sha256(data).hexdigest()), self.assertRaises(ValueError):
                gate.binary_metadata(data, 'x86_64', (10, 14, 0))

    def test_bundle_real_framework_symlinks_and_hardlinks_deduplicate_without_hiding_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = bundle(Path(temporary))
            framework = root / 'Contents/Frameworks/example.framework'
            binary = framework / 'Versions/A/example'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(universal([macho(), macho('arm64', (12, 3, 0))]))
            (framework / 'Versions/Current').symlink_to('A', target_is_directory=True)
            (framework / 'example').symlink_to('Versions/Current/example')
            os.link(binary, root / 'Contents/Frameworks/example-duplicate')
            result = gate.verify_bundle(root, 'x86_64', '10.14')
            self.assertEqual(result['binary_count'], 4)
            self.assertEqual(len(result['internal_links']), 2)
            self.assertEqual(len(result['duplicate_inodes']), 1)
            self.assertEqual(set(result['required_binaries']), set(gate.REQUIRED))

    def test_newer_wrong_arch_or_unversioned_plugin_cannot_hide_beside_valid_required_files(self):
        for payload in (macho(minimum=(14, 0, 0)), macho('arm64', (12, 3, 0)), macho(encoded=False)):
            with tempfile.TemporaryDirectory() as temporary:
                root = bundle(Path(temporary))
                (root / 'Contents/Frameworks/extra-plugin').write_bytes(payload)
                with self.assertRaisesRegex(ValueError, 'extra-plugin'):
                    gate.verify_bundle(root, 'x86_64', '10.14')

    def test_info_identity_executable_literal_minimum_and_required_file_failures(self):
        for field, value in (('CFBundleIdentifier', 'other.app'), ('CFBundleExecutable', '../service'),
                             ('CFBundleExecutable', 'wrong-name'), ('CFBundleExecutable', ''),
                             ('LSMinimumSystemVersion', '10.14.0'), ('LSMinimumSystemVersion', '12.3')):
            with tempfile.TemporaryDirectory() as temporary:
                root = bundle(Path(temporary))
                info_path = root / 'Contents/Info.plist'
                info = plistlib.loads(info_path.read_bytes());info[field] = value
                info_path.write_bytes(plistlib.dumps(info))
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    gate.verify_bundle(root, 'x86_64', '10.14')
        for required in gate.REQUIRED:
            for replacement in (None, b'ordinary non-native resource', macho(kind=8)):
                with tempfile.TemporaryDirectory() as temporary:
                    root = bundle(Path(temporary))
                    path = root / required
                    path.unlink()
                    if replacement is not None:
                        path.write_bytes(replacement)
                    with self.subTest(required=required, replacement=replacement), self.assertRaises((OSError, ValueError)):
                        gate.verify_bundle(root, 'x86_64', '10.14')

    def test_escaping_broken_and_directory_links_and_unreadable_walk_cannot_hide_files(self):
        for target, directory in (('../../../external', False), ('missing', False), ('../../../outside', True)):
            with tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                root = bundle(folder)
                (folder / 'external').write_bytes(macho())
                (folder / 'outside').mkdir()
                (root / 'Contents/Frameworks/unsafe-link').symlink_to(target, target_is_directory=directory)
                with self.assertRaises((OSError, ValueError)):
                    gate.verify_bundle(root, 'x86_64', '10.14')
        with tempfile.TemporaryDirectory() as temporary:
            root = bundle(Path(temporary))
            def failed_walk(_root, **kwargs):
                kwargs['onerror'](PermissionError('Cannot inspect plugin directory'))
                return iter(())
            with patch.object(gate.os, 'walk', side_effect=failed_walk), self.assertRaises(PermissionError):
                gate.verify_bundle(root, 'x86_64', '10.14')

    def test_cli_clears_stale_json_on_failure_and_preserves_nonjson_and_input_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            root = bundle(folder)
            output = folder / 'proof.json'
            arguments = [str(PATH), '--bundle', str(root), '--arch', 'x86_64', '--minimum', '10.14', '--output', str(output)]
            output.write_text('old successful proof')
            with patch.object(sys, 'argv', arguments), contextlib.redirect_stdout(io.StringIO()):
                gate.main()
            self.assertIn('"status": "passed"', output.read_text())
            arguments[4] = 'invalid'
            with patch.object(sys, 'argv', arguments), self.assertRaises(ValueError):
                gate.main()
            self.assertFalse(output.exists())
            arguments[4] = 'x86_64'
            output.write_text('old successful proof')
            (root / gate.REQUIRED[0]).write_bytes(b'invalid')
            with patch.object(sys, 'argv', arguments), self.assertRaises(ValueError):
                gate.main()
            self.assertFalse(output.exists())
            for protected in (folder / 'protected.txt', root / 'Contents/inside.json'):
                protected.write_text('preserve me')
                arguments[-1] = str(protected)
                with patch.object(sys, 'argv', arguments), self.assertRaises(ValueError):
                    gate.main()
                self.assertEqual(protected.read_text(), 'preserve me')
            self.assertFalse(list(folder.glob('.mixel-bundle-proof-*')))

    @unittest.skipUnless(platform.system() == 'Darwin', 'Actual SDK executable/dylib controls run on macOS')
    def test_actual_sdk_executable_dylib_and_lipo_both_architectures(self):
        with tempfile.TemporaryDirectory(prefix='mixel-bundle-native-sdk-') as temporary:
            folder = Path(temporary)
            source = folder / 'native.c'
            source.write_text('int mixel_minimum_control(void) { return 7; }\nint main(void) { return 0; }\n')
            payloads = {}
            for arch, minimum in gate.MINIMUMS.items():
                payloads[arch] = {}
                for kind in ('executable', 'dylib'):
                    output = folder / (arch + '-' + kind)
                    arguments = ['xcrun', 'clang', '-arch', arch, '-mmacosx-version-min=' + minimum, str(source), '-o', str(output)]
                    if kind == 'dylib':
                        arguments += ['-dynamiclib']
                    result = subprocess.run(arguments, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    payloads[arch][kind] = output.read_bytes()
                app_folder = folder / arch;app_folder.mkdir()
                kinds = {name: payloads[arch]['dylib' if name.endswith('.dylib') else 'executable'] for name in gate.REQUIRED}
                app = bundle(app_folder, arch, kinds)
                self.assertEqual(gate.verify_bundle(app, arch, minimum)['binary_count'], 3)
                newer = app / 'Contents/Frameworks/newer-plugin'
                subprocess.run(['xcrun', 'clang', '-arch', arch, '-mmacosx-version-min=14.0', '-dynamiclib', str(source), '-o', str(newer)], check=True, capture_output=True)
                with self.assertRaisesRegex(ValueError, 'newer-plugin'):
                    gate.verify_bundle(app, arch, minimum)
            fat = folder / 'actual-lipo-dylib'
            subprocess.run(['xcrun', 'lipo', '-create', str(folder / 'x86_64-dylib'), str(folder / 'arm64-dylib'), '-output', str(fat)], check=True, capture_output=True)
            for arch, minimum in gate.MINIMUMS.items():
                self.assertEqual(gate.binary_metadata(fat.read_bytes(), arch, gate.native.version(minimum))['target_slice']['sha256'], hashlib.sha256(payloads[arch]['dylib']).hexdigest())


if __name__ == '__main__':
    unittest.main(verbosity=2)
