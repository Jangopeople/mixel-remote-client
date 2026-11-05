#!/usr/bin/env python3
"""Compiler-layer refusal/inverse controls; no application pass is inferred."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('windows_compiler_integration', Path(__file__).with_name('patch-windows-compiler-integration.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class Controls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.rows = []
        for i in range(6):
            before = f'prefix {i}\nold {i}\nsuffix\n'
            after = before.replace(f'old {i}', f'new {i}')
            row = {'path': f'f{i}.rs', 'beforeSha256': P.sha(before.encode()),
                   'afterSha256': P.sha(after.encode()),
                   'changes': [{'before': f'old {i}', 'after': f'new {i}', 'offset': before.index(f'old {i}')}]}
            self.rows.append(row)
            (self.root / row['path']).write_bytes(before.encode())

    def run_layer(self, **kwargs):
        with mock.patch.object(P, 'plan', return_value={'files': self.rows}):
            return P.apply(self.root, **kwargs)

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.root.iterdir()}

    def test_plan_is_whole_file_pinned_and_binds_real_pipe_generics(self):
        plan = P.plan()
        rows = {r['path']: r for r in plan['files']}
        row = rows['src/server/mixel_receiver_custodian_windows.rs']
        after = '\n'.join(p['after'] for p in row['changes'])
        self.assertIn('log, timeout, tokio', after)
        self.assertEqual(after.count('ConnectionTmpl<S>'), 2)
        self.assertEqual(after.count('tokio::io::AsyncRead+tokio::io::AsyncWrite+Unpin'), 2)
        self.assertNotIn('bail!(', after)
        key = rows['.mixel-deps/rdev/src/keycodes/chrome.rs']['changes']
        self.assertEqual(key[0]['before'], '                    $code => Key::$key,')
        self.assertEqual(key[0]['after'], '                    value if value == $code => Key::$key,')
        self.assertIsInstance(key[0]['offset'], int)

    def test_default_check_does_not_write(self):
        before = self.snapshot()
        self.assertEqual(self.run_layer()['writes'], 0)
        self.assertEqual(before, self.snapshot())

    def test_forward_idempotent_exact_inverse(self):
        before = self.snapshot()
        self.assertEqual(self.run_layer(write=True)['writes'], 6)
        self.assertEqual(self.run_layer(write=True)['writes'], 0)
        self.assertEqual(self.run_layer(write=True, restore=True)['writes'], 6)
        self.assertEqual(before, self.snapshot())

    def test_crlf_is_retained(self):
        for p in self.root.iterdir():
            p.write_bytes(p.read_bytes().replace(b'\n', b'\r\n'))
        before = self.snapshot()
        self.run_layer(write=True)
        self.assertTrue(all(b'\r\n' in raw and b'\n' not in raw.replace(b'\r\n', b'') for raw in self.snapshot().values()))
        self.run_layer(write=True, restore=True)
        self.assertEqual(before, self.snapshot())

    def test_removed_import_inverse_uses_closed_offset_not_empty_string_search(self):
        row = self.rows[-1]
        before = (self.root / row['path']).read_text()
        row['changes'][0]['after'] = ''
        row['afterSha256'] = P.sha(before.replace('old 5', '', 1).encode())
        snapshot = self.snapshot()
        self.run_layer(write=True)
        self.run_layer(write=True, restore=True)
        self.assertEqual(snapshot, self.snapshot())

    def test_changed_or_mixed_source_refuses_without_partial_writes(self):
        for change in ('drift', 'new 5'):
            p = self.root / 'f5.rs'
            p.write_text('prefix 5\n' + change + '\nsuffix\n')
            before = self.snapshot()
            with self.assertRaisesRegex(ValueError, 'mixed'):
                self.run_layer(write=True)
            self.assertEqual(before, self.snapshot())

    def test_hardlink_and_symlink_refuse(self):
        original = self.root / 'f5.rs'
        alias = self.root / 'alias'
        import os
        os.link(original, alias)
        with self.assertRaisesRegex(ValueError, 'independent'):
            self.run_layer(write=True)
        alias.unlink()
        raw = original.read_bytes()
        original.unlink()
        alias.write_bytes(raw)
        original.symlink_to(alias)
        with self.assertRaises(ValueError):
            self.run_layer(write=True)

    def test_mixed_newlines_refuse(self):
        (self.root / 'f5.rs').write_bytes(b'prefix 5\r\nold 5\nsuffix\n')
        with self.assertRaisesRegex(ValueError, 'newlines'):
            self.run_layer(write=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
