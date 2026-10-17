#!/usr/bin/env python3
"""Pure source/profile controls; no compiler, native API or application runs."""
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('receiver_native_enigo', ROOT / 'scripts/patch-support-receiver-native-enigo.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
FIXTURES = ROOT / 'scripts/fixtures/receiver-native-enigo'


def fixture_bytes(profile, name):
    return P.normalize((FIXTURES / profile / name).read_bytes())[0]


def snapshot(root):
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mode & 0o777)
            for path in root.rglob('*') if path.is_file()}


class NativeAcceptanceTests(unittest.TestCase):
    def test_all_seven_real_source_profiles_inverse_modes_and_idempotence(self):
        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for row in P.plan():
                    name = row['name']
                    original = fixture_bytes('original', name)
                    final = fixture_bytes('candidate', name)
                    self.assertEqual(P.digest(original), row['beforeSha256'])
                    self.assertEqual(P.digest(final), row['afterSha256'])
                    self.assertEqual(P.transform(original, row), final)
                    self.assertEqual(P.transform(final, row, restore=True), original)
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(original.replace(b'\n', newline))
                    path.chmod(0o640)
                before = snapshot(root)
                P.apply(root, restore=True, check=True)
                with self.assertRaises(ValueError): P.apply(root, check=True)
                P.apply(root)
                after = snapshot(root)
                self.assertTrue(all(mode == 0o640 for _, mode in after.values()))
                for row in P.plan():
                    self.assertEqual(after[row['name']][0], fixture_bytes('candidate', row['name']).replace(b'\n', newline))
                P.apply(root); P.apply(root, check=True)
                self.assertEqual(snapshot(root), after)
                P.apply(root, restore=True); P.apply(root, restore=True)
                self.assertEqual(snapshot(root), before)

    def test_missing_tampered_mixed_and_partial_profiles_refuse_before_any_write(self):
        for mutation in ('missing', 'tampered', 'mixed-newlines', 'partial'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'source'
                shutil.copytree(FIXTURES / 'original', root)
                row = P.plan()[0]
                target = root / row['name']
                if mutation == 'missing': target.unlink()
                elif mutation == 'tampered': target.write_bytes(target.read_bytes() + b'// drift\n')
                elif mutation == 'mixed-newlines': target.write_bytes(target.read_bytes().replace(b'\n', b'\r\n', 1))
                else: target.write_bytes(fixture_bytes('candidate', row['name']))
                before = snapshot(root)
                for restore in (False, True):
                    with self.assertRaises((ValueError, OSError)): P.apply(root, restore=restore)
                    self.assertEqual(snapshot(root), before)

    def test_symlink_hardlink_and_parent_symlink_refuse_without_mutation(self):
        for mutation in ('symlink', 'hardlink', 'parent'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                outer = Path(directory)
                root = outer / 'source'
                shutil.copytree(FIXTURES / 'original', root)
                target = root / P.plan()[0]['name']
                if mutation == 'parent':
                    parent = target.parent
                    retained = outer / 'retained'
                    parent.rename(retained)
                    parent.symlink_to(retained, target_is_directory=True)
                else:
                    retained = outer / 'retained.rs'
                    target.rename(retained)
                    if mutation == 'symlink': target.symlink_to(retained)
                    else: os.link(retained, target)
                original = retained.read_bytes() if retained.is_file() else None
                with self.assertRaises(ValueError): P.apply(root)
                if original is not None: self.assertEqual(retained.read_bytes(), original)

    def test_windows_zero_partial_counts_refuse_independently_of_last_error(self):
        source = (FIXTURES / 'candidate/libs/enigo/src/win/win_impl.rs').read_text()
        for variable in ('accepted', 'res'):
            guards = re.findall(r'if ' + variable + r' != (\d+) \{ return Err\(', source)
            self.assertTrue(guards, variable)
            self.assertEqual(set(guards), {'1'})
            # The actual guard's count is decisive, even with empty LastError.
            required = int(guards[0])
            self.assertEqual([count for count in (0, 1, 2) if count == required], [1])
        down = next(change['after'] for row in P.plan() if row['name'].endswith('win/win_impl.rs')
                    for change in row['changes'] if change['before'].startswith('    fn key_down'))
        self.assertLess(down.index('if let Err(error) = self.key_down'), down.index('applied.push'))
        self.assertIn('for modifier in applied.into_iter().rev()', down)
        self.assertNotIn('if !err.is_empty()', down)

    def test_macos_requires_source_and_event_before_four_dispatches(self):
        rows = [row for row in P.plan() if row['name'].endswith('macos/macos_impl.rs')]
        self.assertEqual(len(rows), 1)
        for name in ('key_down', 'key_up_checked', 'mouse_down', 'mouse_up_checked'):
            source = (FIXTURES / 'candidate' / rows[0]['name']).read_text()
            match = re.search(r'^    fn ' + name + r'\([^\n]*\).*?^    }\n', source, re.M | re.S)
            self.assertIsNotNone(match, name)
            body = match[0]
            self.assertIn('.ok_or("CGEvent source unavailable")?', body)
            self.assertIn('.map_err(', body)
            self.assertLess(body.index('.map_err('), body.index('self.post('))
            self.assertNotIn('if let Some(src)', body)

    def test_linux_preserves_backend_selection_but_checks_unavailable_and_native_status(self):
        source = (FIXTURES / 'candidate/libs/enigo/src/linux/xdo.rs').read_text()
        for name in ('key_down', 'key_up_checked', 'mouse_down', 'mouse_up_checked'):
            body = re.search(r'^    fn ' + name + r'\([^\n]*\).*?^    }\n', source, re.M | re.S)[0]
            self.assertIn('self.xdo.is_null() { return Err(', body)
            self.assertIn('if status != 0 { return Err(', body)
        source = (FIXTURES / 'candidate/libs/enigo/src/linux/nix_impl.rs').read_text()
        self.assertIn('if self.is_x11', source)
        self.assertIn('self.tfc_key_down_or_up(key, false, true)', source)
        self.assertIn('self.custom_keyboard.as_mut().ok_or(', source)
        self.assertIn('self.custom_mouse.as_mut().ok_or(', source)
        self.assertNotIn('log::warn!("Enigo::key_down: no custom_keyboard', source)

    def test_checked_defaults_fail_closed_and_wayland_send_failures_propagate(self):
        interface = (FIXTURES / 'candidate/libs/enigo/src/lib.rs').read_text()
        for name in ('key_up_checked', 'mouse_up_checked'):
            body = re.search(r'^    fn ' + name + r'\([^\n]*\).*?^    }\n', interface, re.M | re.S)[0]
            self.assertIn('Err(', body)
            self.assertNotIn('Ok(', body)
        source = (FIXTURES / 'candidate/src/server/uinput.rs').read_text()
        for name in ('key_down', 'key_up_checked', 'mouse_down', 'mouse_up_checked'):
            body = re.search(r'^        fn ' + name + r'\([^\n]*\).*?^        }\n', source, re.M | re.S)[0]
            self.assertIn('.map_err(', body)
            self.assertNotIn('allow_err!', body)
        source = (FIXTURES / 'candidate/src/server/rdp_input.rs').read_text()
        body = re.search(r'^        fn key_up_checked\([^\n]*\).*?^        }\n', source, re.M | re.S)[0]
        self.assertLess(body.index('handle_key(false'), body.index('self.modifier_state.update'))
        self.assertIn('state,\n        )?;\n        Ok(())', source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
