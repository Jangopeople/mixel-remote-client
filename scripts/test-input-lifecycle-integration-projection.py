#!/usr/bin/env python3
"""Exact new integration brackets and complete-tree historical admissions."""
import ast
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import input_lifecycle_integration_projection as P

ROOT = Path(__file__).absolute().parents[1]


def actual_functions(name, functions):
    tree = ast.parse((ROOT / name).read_text(encoding='utf-8'))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in functions]
    assert len(selected) == len(functions)
    namespace = {"Path": Path}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROOT / name), 'exec'), namespace)
    return namespace


def terminal():
    spec = importlib.util.spec_from_file_location('complete_input_control', ROOT / 'scripts' / P.WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = module.namespace()
    return module, engine, module.source_plan(engine)


class Controls(unittest.TestCase):
    def test_complete_lf_crlf_exact_inverse_preserves_every_old_byte(self):
        for name in ('scripts/apply-branding.sh', '.github/workflows/build.yml'):
            final = (ROOT / name).read_bytes()
            baseline = P.project_to_canonical(final, name)
            for ending in (b'\n', b'\r\n'):
                raw = baseline.replace(b'\r\n', b'\n').replace(b'\n', ending)
                composed = P.compose(raw, name)
                self.assertEqual(P.project_to_canonical(composed, name), raw)
                self.assertEqual(P.canonical(composed)[1], ending)
                self.assertEqual(P.canonical(composed)[0], P.canonical(final)[0])

    def test_missing_duplicate_moved_changed_and_reordered_hunks_refuse(self):
        for name in ('scripts/apply-branding.sh', '.github/workflows/build.yml'):
            final = (ROOT / name).read_text(encoding='utf-8')
            for before, addition, after in P.hunks(name):
                without = final.replace(addition, '', 1)
                mutants = [without, final.replace(addition, addition * 2, 1),
                    without + addition,
                    final.replace(addition, addition.replace('--root ', '--unknown-root ', 1) if '--root ' in addition else addition.replace('--source ', '--unknown-source ', 1), 1),
                    without.replace(before, addition + before, 1),
                    final.replace(addition, addition + '# foreign\n', 1)]
                for raw in mutants:
                    with self.subTest(name=name, block=addition[:60]):
                        self.assertNotEqual(raw, final)
                        with self.assertRaises(ValueError):
                            P.project_to_canonical(raw.encode(), name)

    def test_missing_working_layer_is_not_a_historical_head_fallback(self):
        for name in ('scripts/apply-branding.sh', '.github/workflows/build.yml'):
            old = P.project_to_canonical((ROOT / name).read_bytes(), name)
            with self.assertRaises(ValueError): P.inverse_blocks(old, name)
            self.assertEqual(P.committed_workflow(old, name), old)
            with self.assertRaises(ValueError): P.committed_workflow(old + b'# drift\n', name)

    def test_other_drift_stays_fatal_at_original_hash(self):
        for name in ('scripts/apply-branding.sh', '.github/workflows/build.yml'):
            final = (ROOT / name).read_bytes()
            with self.assertRaisesRegex(ValueError, 'changed other canonical bytes'):
                P.project_to_canonical(final + b'# unknown unrelated drift\n', name)
            self.assertTrue(P.inverse_blocks(final + b'# another independently reviewed layer\n', name).endswith(b'# another independently reviewed layer\n'))

    def test_actual_six_command_texture_guard_unchanged(self):
        guard = actual_functions('scripts/test-support-macos-texture.py', {'exact_bridge_native_close_commands'})['exact_bridge_native_close_commands']
        final = (ROOT / '.github/workflows/build.yml').read_bytes()
        with self.assertRaises(AssertionError): guard(final.decode())
        old = P.project_to_canonical(final, '.github/workflows/build.yml').decode()
        self.assertEqual(len(guard(old)), 6)

    def test_actual_uri_linux_bridge_historical_guards_unchanged(self):
        namespace = actual_functions('scripts/test-support-launch-macos-concurrency-controls.py',
            {'linux_close_bridge_projection', 'inverse_linux_close_bridge_checks', 'uri_bridge_projection', 'inverse_uri_bridge_checks'})
        final = (ROOT / '.github/workflows/build.yml').read_bytes()
        with self.assertRaises(AssertionError): namespace['inverse_uri_bridge_checks'](final.decode())
        old = P.project_to_canonical(final, '.github/workflows/build.yml').decode()
        value = namespace['inverse_linux_close_bridge_checks'](namespace['inverse_uri_bridge_checks'](old))
        expected = P.BRIDGE_HEADER + '          RDREPO=. python3 ../scripts/patch-support-macos-texture.py\n          RDREPO=. python3 ../scripts/patch-support-macos-window.py\n' + P.BRIDGE_AFTER
        self.assertEqual(value.count(expected), 1)

    def test_mac_regressions_are_exact_job_bound_and_fatal_if_missing(self):
        name = '.github/workflows/build.yml'
        final = (ROOT / name).read_text(encoding='utf-8')
        block = P.MAC_REGRESSIONS
        self.assertEqual(final.count(block), 1)
        before, addition, after = P.workflow_hunks()['macos'][0]
        without = final.replace(block, '', 1)
        moved = without.replace('  macos:\n', block + '  macos:\n', 1)
        first, second = block.split('      - name: Preserve desktop input lifetime regression evidence\n', 1)
        reversed_steps = '      - name: Preserve desktop input lifetime regression evidence\n' + second + first
        mutants = [without, final.replace(block, block + block, 1), moved,
            final.replace(block, reversed_steps, 1),
            final.replace('--expected-count 180', '--expected-count 179', 1),
            final.replace('        if: always()\n        uses: actions/upload-artifact@v6\n        with:\n          name: macos-input-lifecycle-', '        if: success()\n        uses: actions/upload-artifact@v6\n        with:\n          name: macos-input-lifecycle-', 1),
            final.replace(before + block + after, before + '# disjoint\n' + block + after, 1)]
        for changed in mutants:
            self.assertNotEqual(changed, final)
            with self.assertRaises(ValueError):
                P.project_to_canonical(changed.encode(), name)
        # Another job with an identical analyzer command cannot own this block.
        self.assertEqual(P.project_to_canonical(final.encode(), name), P.project_to_canonical(P.compose(P.project_to_canonical(final.encode(), name), name), name))

    def test_native_job_missing_duplicate_moved_mutated_or_reordered_refuses(self):
        name = '.github/workflows/build.yml'
        final = (ROOT / name).read_text(encoding='utf-8')
        block = P.NATIVE_REGRESSIONS
        self.assertEqual(final.count(block), 1)
        self.assertIn('native-input/native_input_gate.py', block)
        self.assertIn('--online\n', block)
        without = final.replace(block, '', 1)
        prepare = '      - name: Apply complete reviewed branding and input ownership\n'
        gate = '      - name: Prove actual input components and required original failures\n'
        prefix, remainder = block.split(prepare, 1)
        brand, remainder = remainder.split(gate, 1)
        gate_part, evidence = remainder.split('      - name: Preserve input regression evidence\n', 1)
        reordered = prefix + gate + gate_part + prepare + brand + '      - name: Preserve input regression evidence\n' + evidence
        mutants = [without, final.replace(block, block + block, 1), without + block,
            final.replace(block, block.replace("rust: '1.75'", "rust: '1.74'", 1), 1),
            final.replace(block, block.replace('--online\n', '\n', 1), 1),
            final.replace(block, block.replace('if: always()', 'if: success()', 1), 1),
            final.replace(block, reordered, 1)]
        for changed in mutants:
            self.assertNotEqual(changed, final)
            with self.assertRaises(ValueError): P.project_to_canonical(changed.encode(), name)

    def test_all_three_dependencies_are_exact_shipping_job_owned(self):
        name = '.github/workflows/build.yml'
        final = (ROOT / name).read_text(encoding='utf-8')
        original = '    needs: [config, bridge]\n'
        current = '    needs: [config, bridge, input-regressions]\n'
        for job in ('linux', 'macos', 'windows'):
            header = '  ' + job + ':\n'
            after = "    if: ${{ needs.config.outputs." + job + " == 'true' }}\n"
            exact = header + current + after
            self.assertEqual(final.count(exact), 1)
            missing = final.replace(exact, header + original + after, 1)
            mutants = [missing,
                final.replace(exact, header + current + current + after, 1),
                final.replace(exact, header + current.replace('input-regressions', 'foreign-gate') + after, 1),
                final.replace(exact, header + current.replace('config, bridge, input-regressions', 'input-regressions, config, bridge') + after, 1),
                final.replace(exact, header + current + '    # disjoint\n' + after, 1),
                missing.replace('  bridge:\n    needs: config\n', '  bridge:\n' + current, 1)]
            for changed in mutants:
                self.assertNotEqual(changed, final)
                with self.assertRaises(ValueError): P.project_to_canonical(changed.encode(), name)

    def test_actual_historical_footer_macro_inverts_terminal_first(self):
        import shutil
        namespace = actual_functions('scripts/test-branding.py', {'historical_footer_branding_command'})
        namespace.update(ROOT=ROOT, SCRIPT=ROOT/'scripts/apply-branding.sh', shutil=shutil,
                         input_lifecycle=P, BASH='bash', Path=Path)
        with tempfile.TemporaryDirectory(prefix='mixel footer macro source ') as temporary:
            for layers in (('uri',), ('uri', 'native', 'linux')):
                namespace['TERMINAL_FOOTER_LAYERS'] = {
                    'uri': ('patch-support-uri-routing.py', ''),
                    'native': ('patch-support-linux-child-window-close.py', 'flutter/.mixel-dependencies/desktop_multi_window/'),
                    'linux': ('patch-support-linux-window-close.py', '')}
                command = namespace['historical_footer_branding_command'](Path(temporary), layers)
                text = Path(command[1]).read_text(encoding='utf-8')
                self.assertNotIn(P.WRAPPER, text)
                for layer in layers:
                    self.assertNotIn(namespace['TERMINAL_FOOTER_LAYERS'][layer][0], text)
                self.assertIn('patch-support-file-dialog-lifecycle.py', text)
                self.assertEqual((ROOT/'scripts/apply-branding.sh').read_bytes(), P.compose(P.project_to_canonical((ROOT/'scripts/apply-branding.sh').read_bytes(), 'scripts/apply-branding.sh'), 'scripts/apply-branding.sh'))

    def test_complete_tree_entry_uses_strict_inspect_and_never_falls_back(self):
        _, engine, plan = terminal()
        with tempfile.TemporaryDirectory(prefix='mixel strict input entry ') as temporary:
            root = Path(temporary).resolve()
            # Real plan bytes are assembled from its reviewed complete chunks;
            # no app source implementation is rewritten by these controls.
            final = {}
            for row in plan['files']:
                if row['newOwned']:
                    final[row['name']] = row['changes'][0]['after'].encode()
            self.assertEqual(len(final), 1)
            owned = root / next(iter(final))
            owned.parent.mkdir(parents=True)
            owned.write_bytes(next(iter(final.values())))
            with mock.patch.object(engine['shutil'], 'copytree', side_effect=AssertionError('No partial source copy')):
                with self.assertRaises((RuntimeError, FileNotFoundError)):
                    with P.current_source(root): pass
            # Even removing the new module cannot turn this incomplete tree
            # into the optional/missing-layer path used by a heuristic selector.
            owned.unlink()
            with self.assertRaises((RuntimeError, FileNotFoundError)):
                with P.current_source(root): pass

    def test_internal_historical_recursion_is_explicit(self):
        tree = ast.parse((ROOT / 'scripts/test-support-flutter-analysis.py').read_text())
        for name in ('linux_close_baseline', 'startup_baseline'):
            node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
            self.assertEqual(node.args.args[-1].arg, '_input_checked')
            calls = [call for call in ast.walk(node) if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == name]
            self.assertGreaterEqual(len(calls), 4)
            for call in calls:
                self.assertTrue(any(keyword.arg == '_input_checked' and isinstance(keyword.value, ast.Constant) and keyword.value.value is True for keyword in call.keywords))
        # No older owner function gains an optional new-layer presence bypass.
        for name in ('test-support-uri-routing.py', 'test-support-linux-window-close.py', 'test-support-linux-child-window-close.py'):
            text = (ROOT / 'scripts' / name).read_text()
            self.assertIn('input_lifecycle.current_source(', text)
            self.assertNotIn('except RuntimeError:', text[text.index('def main('):])


if __name__ == '__main__':
    unittest.main(verbosity=2)
