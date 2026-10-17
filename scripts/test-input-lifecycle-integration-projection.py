#!/usr/bin/env python3
"""Exact new integration brackets and complete-tree historical admissions."""
import ast
import hashlib
import importlib.util
import os
import re
from pathlib import Path
import tempfile
import unittest
from unittest import mock


# Load the exact sibling helper for direct, runpy and file-spec entry points.
_input_lifecycle_spec = importlib.util.spec_from_file_location(
    "mixel_input_lifecycle_projection",
    Path(__file__).with_name("input_lifecycle_integration_projection.py"),
)
P = importlib.util.module_from_spec(_input_lifecycle_spec)
_input_lifecycle_spec.loader.exec_module(P)

ROOT = Path(__file__).absolute().parents[1]


def actual_functions(name, functions):
    tree = ast.parse((ROOT / name).read_text(encoding='utf-8'))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in functions]
    assert len(selected) == len(functions)
    namespace = {"Path": Path, "re": re}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROOT / name), 'exec'), namespace)
    return namespace


def cold_workflow_view(raw, restore):
    """Unit-only independent runtime layers; shared terminal projection stays exact."""
    text, newline = P.canonical(raw)
    owner = actual_functions('scripts/test-support-launch-macos-concurrency-controls.py',
        {'cold_windows_workflow_projection', 'inverse_cold_windows_workflow',
         'macos_nasm_workflow_projection', 'restore_current_macos_runtime_workflow'})
    if restore:
        try:
            text = owner['restore_current_macos_runtime_workflow'](text)
            text = owner['inverse_cold_windows_workflow'](text)
        except AssertionError as error:
            raise ValueError('Changed exact cold workflow unit view') from error
    else:
        before, addition, after, log_before, old_log, new_log, log_after = owner['cold_windows_workflow_projection']()
        if (text.count(before + after) != 1 or addition in text or
                text.count(log_before + old_log + log_after) != 1 or new_log in text):
            raise ValueError('Changed exact cold workflow unit composition')
        text = text.replace(before + after, before + addition + after, 1)
        text = text.replace(log_before + old_log + log_after, log_before + new_log + log_after, 1)
        old_runtime, current_runtime = owner['macos_nasm_workflow_projection']()
        if text.count(old_runtime) != 1 or current_runtime in text:
            raise ValueError('Changed exact Mac runtime unit composition')
        previous = text
        text = text.replace(old_runtime, current_runtime, 1)
        try:
            if owner['restore_current_macos_runtime_workflow'](text) != previous:
                raise ValueError('Mac runtime composition inverse changed other bytes')
        except AssertionError as error:
            raise ValueError('Changed exact Mac runtime job context') from error
    return text.encode().replace(b'\n', newline)


def current_to_canonical(raw, name):
    return previous_receiver_to_canonical(P.receiver_projection().inverse(raw, name), name)


def previous_receiver_to_canonical(raw, name):
    """Historical controls retain their exact bytes after strict new-hunk inverse."""
    if name == '.github/workflows/build.yml':
        raw = cold_workflow_view(raw, restore=True)
    return P.project_to_canonical(P.receiver_projection().compose(raw, name), name)


def compose_current(raw, name):
    current = P.compose(raw, name)
    return cold_workflow_view(current, restore=False) if name == '.github/workflows/build.yml' else current


def terminal():
    spec = importlib.util.spec_from_file_location('complete_input_control', ROOT / 'scripts' / P.WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = module.namespace()
    return module, engine, module.source_plan(engine)


class Controls(unittest.TestCase):
    def test_debian_ownership_hook_is_exactly_packaging_bound_and_original_bytes_recover(self):
        name = 'scripts/apply-branding.sh'
        final = P.canonical(P.receiver_projection().inverse((ROOT / name).read_bytes(), name))[0]
        before, addition, after = P.hunks(name)[-1]
        self.assertIn('patch-debian-archive-ownership.py', addition)
        self.assertEqual(final.count(before + addition + after), 1)
        self.assertEqual(final.count('patch-debian-archive-ownership.py'), 1)
        prior = P.debian_lifecycle_branding_projection(final.replace(addition, '', 1).encode(), name, restore=True)
        self.assertEqual(hashlib.sha256(prior).hexdigest(), 'b9df7b9cfc1d52e7d7a40fad64a67698190a360181862e3d02ea40328fe1988a')
        for mutated in (final.replace(addition, '', 1), final.replace(addition, addition * 2, 1),
                        final.replace(addition, addition.replace('$BUILDPY', '$OTHER'), 1),
                        final.replace(addition, '', 1) + addition,
                        final.replace(before + addition + after, before + '# disjoint\n' + addition + after, 1)):
            with self.assertRaises(ValueError):
                previous_receiver_to_canonical(mutated.encode(), name)

    def test_debian_lifecycle_hook_guard_whole_inverse_and_lf_crlf_refuse_drift(self):
        name = 'scripts/apply-branding.sh'
        final = P.canonical(P.receiver_projection().inverse((ROOT / name).read_bytes(), name))[0]
        before, addition, after, old_guard, new_guard = P.DEBIAN_LIFECYCLE_MACRO
        for ending in (b'\n', b'\r\n'):
            raw = final.encode().replace(b'\n', ending)
            prior = P.debian_lifecycle_branding_projection(raw, name, restore=True)
            self.assertEqual(P.digest(P.canonical(prior)[0].encode()), P.DEBIAN_PREVIOUS_BRANDING_SHA)
            self.assertEqual(P.debian_lifecycle_branding_projection(prior, name, restore=False), raw)
            self.assertEqual(P.canonical(prior)[1], ending)
            self.assertEqual(P.digest(P.canonical(previous_receiver_to_canonical(raw, name))[0].encode()), P.BASE_SHA[name])
            for changed in (final.replace(addition, '', 1), final.replace(addition, addition * 2, 1),
                            final.replace(addition, '', 1) + addition,
                            final.replace(before + addition + after, before + '# disjoint\n' + addition + after, 1),
                            final.replace(addition, addition.replace('--root', '--foreign-root'), 1),
                            final.replace(new_guard, old_guard, 1),
                            final.replace(new_guard, new_guard + new_guard, 1),
                            final.replace(new_guard, new_guard.replace('start', 'stop'), 1)):
                self.assertNotEqual(changed, final)
                with self.assertRaises(ValueError):
                    previous_receiver_to_canonical(changed.encode().replace(b'\n', ending), name)
            with self.assertRaises(ValueError):
                P.debian_lifecycle_branding_projection(prior, name, restore=True)
            self.assertEqual(P.committed_workflow(prior, name), P.inverse_previous_receiver_blocks(raw, name))

    def test_debian_private_source_validates_before_copy_and_only_changes_owned_stage(self):
        import contextlib
        import shutil
        events = []
        with tempfile.TemporaryDirectory(prefix='mixel-debian-prior-source-') as temporary:
            root = Path(temporary) / 'source'; root.mkdir()
            (root / 'synthetic').write_bytes(b'current')
            stage = Path(temporary) / 'stage'
            class Owner:
                def apply(self, path, restore=False, check=False):
                    self_outer.assertTrue(restore)
                    events.append(('check' if check else 'restore', Path(path).name))
                    if check:
                        self_outer.assertEqual(Path(path), root)
                    else:
                        self_outer.assertEqual(Path(path), stage)
                        (Path(path) / 'synthetic').write_bytes(b'prior')
            class Terminal:
                @contextlib.contextmanager
                def previous(self, path):
                    events.append(('copy', Path(path).name))
                    shutil.copytree(path, stage)
                    yield stage
            self_outer = self
            with mock.patch.object(P, 'debian_lifecycle_owner', return_value=Owner()):
                with P.previous_debian_lifecycle(Terminal(), root) as previous:
                    self.assertEqual(previous, stage)
                    self.assertEqual((stage / 'synthetic').read_bytes(), b'prior')
                    self.assertEqual((root / 'synthetic').read_bytes(), b'current')
            self.assertEqual(events, [('check', 'source'), ('copy', 'source'), ('restore', 'stage')])
            terminal = mock.Mock()
            with mock.patch.object(P, 'debian_lifecycle_owner') as factory:
                factory.return_value.apply.side_effect = RuntimeError('strict three-file profile refusal')
                with self.assertRaisesRegex(RuntimeError, 'strict three-file'):
                    P.previous_debian_lifecycle(terminal, root)
                terminal.previous.assert_not_called()
        with mock.patch.object(P, 'digest', return_value='0' * 64):
            with self.assertRaisesRegex(ValueError, 'Changed exact Debian lifecycle helper'):
                P.debian_lifecycle_owner()

    def test_cold_independent_view_preserves_both_profiles_and_rejects_malformed_layers(self):
        name = '.github/workflows/build.yml'
        raw = (ROOT / name).read_bytes()
        text = P.canonical(raw)[0]
        owner = actual_functions('scripts/test-support-launch-macos-concurrency-controls.py',
            {'cold_windows_workflow_projection', 'inverse_cold_windows_workflow'})
        before, addition, after, log_before, old_log, new_log, log_after = owner['cold_windows_workflow_projection']()
        without = text.replace(addition, '', 1)
        negatives = [without, text.replace(addition, addition * 2, 1), without + addition,
            text.replace(addition, addition.replace('timeout-minutes: 8', 'timeout-minutes: 9'), 1),
            text.replace(before + addition + after, before + '# disjoint\n' + addition + after, 1),
            text.replace(new_log, old_log, 1), text.replace(new_log, new_log * 2, 1),
            text.replace(new_log, new_log.replace('cold-support', 'foreign'), 1),
            text.replace(log_before + new_log + log_after, log_before + '# disjoint\n' + new_log + log_after, 1)]
        for ending in (b'\n', b'\r\n'):
            actual = text.encode().replace(b'\n', ending)
            projected = cold_workflow_view(actual, restore=True)
            self.assertEqual(cold_workflow_view(projected, restore=False), actual)
            self.assertEqual(P.canonical(projected)[1], ending)
            self.assertEqual(hashlib.sha256(P.canonical(current_to_canonical(actual, name))[0].encode()).hexdigest(), P.BASE_SHA[name])
            for bad in negatives:
                with self.subTest(ending=ending, malformed=bad[:40]):
                    with self.assertRaises(ValueError):
                        current_to_canonical(bad.encode().replace(b'\n', ending), name)
        self.assertEqual((ROOT / name).read_bytes(), raw)

    def test_nasm_independent_view_preserves_both_profiles_and_rejects_malformed_layers(self):
        name = '.github/workflows/build.yml'
        raw = (ROOT / name).read_bytes()
        text = P.canonical(raw)[0]
        owner = actual_functions('scripts/test-support-launch-macos-concurrency-controls.py',
            {'macos_nasm_workflow_projection', 'restore_current_macos_runtime_workflow'})
        old, new = owner['macos_nasm_workflow_projection']()
        without = text.replace(new, '', 1)
        marker = '\n  macos:\n'
        negatives = [text.replace(new, old, 1), text.replace(new, new * 2, 1),
            without + new, text.replace(new, new.replace(' nasm\n', '\n'), 1),
            text.replace(new, new.replace('nasm --version', 'nasm --help'), 1),
            text.replace(new, new.replace('      - name: Install Rust', '      # disjoint\n      - name: Install Rust'), 1),
            text.replace(marker, '\n  foreign:\n', 1), text + marker]
        for ending in (b'\n', b'\r\n'):
            actual = text.encode().replace(b'\n', ending)
            prior = current_to_canonical(actual, name)
            self.assertEqual(compose_current(prior, name), actual)
            self.assertEqual(P.canonical(prior)[1], ending)
            self.assertEqual(hashlib.sha256(P.canonical(prior)[0].encode()).hexdigest(), P.BASE_SHA[name])
            for bad in negatives:
                with self.subTest(ending=ending, malformed=bad[:40]):
                    self.assertNotEqual(bad, text)
                    with self.assertRaises(ValueError):
                        current_to_canonical(bad.encode().replace(b'\n', ending), name)
        self.assertEqual((ROOT / name).read_bytes(), raw)

    def test_complete_lf_crlf_exact_inverse_preserves_every_old_byte(self):
        for name in ('scripts/apply-branding.sh', '.github/workflows/build.yml'):
            final = (ROOT / name).read_bytes()
            baseline = current_to_canonical(final, name)
            for ending in (b'\n', b'\r\n'):
                raw = baseline.replace(b'\r\n', b'\n').replace(b'\n', ending)
                composed = compose_current(raw, name)
                self.assertEqual(current_to_canonical(composed, name), raw)
                self.assertEqual(P.canonical(composed)[1], ending)
                self.assertEqual(P.canonical(composed)[0], P.canonical(final)[0])

    def test_missing_duplicate_moved_changed_and_reordered_hunks_refuse(self):
        for name in ('scripts/apply-branding.sh', '.github/workflows/build.yml'):
            final = P.canonical(P.receiver_projection().inverse((ROOT / name).read_bytes(), name))[0]
            for before, addition, after in P.hunks(name):
                without = final.replace(addition, '', 1)
                mutants = [without, final.replace(addition, addition * 2, 1),
                    without + addition,
                    final.replace(addition, addition.replace('--root ', '--unknown-root ', 1) if '--root ' in addition else addition.replace('--source ', '--unknown-source ', 1) if '--source ' in addition else addition.replace('$BUILDPY', '$OTHER', 1), 1),
                    without.replace(before, addition + before, 1),
                    final.replace(addition, addition + '# foreign\n', 1)]
                for raw in mutants:
                    with self.subTest(name=name, block=addition[:60]):
                        self.assertNotEqual(raw, final)
                        with self.assertRaises(ValueError):
                            previous_receiver_to_canonical(raw.encode(), name)

    def test_missing_working_layer_is_not_a_historical_head_fallback(self):
        for name in ('scripts/apply-branding.sh', '.github/workflows/build.yml'):
            old = current_to_canonical((ROOT / name).read_bytes(), name)
            with self.assertRaises(ValueError): P.inverse_blocks(old, name)
            self.assertEqual(P.committed_workflow(old, name), old)
            with self.assertRaises(ValueError): P.committed_workflow(old + b'# drift\n', name)

    def test_other_drift_stays_fatal_at_original_hash(self):
        for name in ('scripts/apply-branding.sh', '.github/workflows/build.yml'):
            final = (ROOT / name).read_bytes()
            newline = P.canonical(final)[1]
            unrelated = b'# unknown unrelated drift\n'.replace(b'\n', newline)
            reviewed = b'# another independently reviewed layer\n'.replace(b'\n', newline)
            with self.assertRaisesRegex(ValueError, 'changed other canonical bytes'):
                current_to_canonical(final + unrelated, name)
            self.assertTrue(P.inverse_blocks(final + reviewed, name).endswith(reviewed))

    def test_actual_six_command_texture_guard_unchanged(self):
        guard = actual_functions('scripts/test-support-macos-texture.py', {'exact_bridge_native_close_commands'})['exact_bridge_native_close_commands']
        final = (ROOT / '.github/workflows/build.yml').read_bytes()
        with self.assertRaises(AssertionError): guard(final.decode())
        old = P.canonical(current_to_canonical(final, '.github/workflows/build.yml'))[0]
        self.assertEqual(len(guard(old)), 6)

    def test_actual_uri_linux_bridge_historical_guards_unchanged(self):
        namespace = actual_functions('scripts/test-support-launch-macos-concurrency-controls.py',
            {'linux_close_bridge_projection', 'inverse_linux_close_bridge_checks', 'uri_bridge_projection', 'inverse_uri_bridge_checks'})
        final = (ROOT / '.github/workflows/build.yml').read_bytes()
        with self.assertRaises(AssertionError): namespace['inverse_uri_bridge_checks'](final.decode())
        old = P.canonical(current_to_canonical(final, '.github/workflows/build.yml'))[0]
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
                current_to_canonical(changed.encode(), name)
        # Another job with an identical analyzer command cannot own this block.
        self.assertEqual(current_to_canonical(final.encode(), name), current_to_canonical(compose_current(current_to_canonical(final.encode(), name), name), name))

    def test_native_job_missing_duplicate_moved_mutated_or_reordered_refuses(self):
        name = '.github/workflows/build.yml'
        final = P.canonical(P.receiver_projection().inverse((ROOT / name).read_bytes(), name))[0]
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
            with self.assertRaises(ValueError): previous_receiver_to_canonical(changed.encode(), name)

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
                with self.assertRaises(ValueError): current_to_canonical(changed.encode(), name)

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
                self.assertEqual((ROOT/'scripts/apply-branding.sh').read_bytes(), compose_current(current_to_canonical((ROOT/'scripts/apply-branding.sh').read_bytes(), 'scripts/apply-branding.sh'), 'scripts/apply-branding.sh'))

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
                with self.assertRaises((RuntimeError, FileNotFoundError, ValueError)):
                    with P.current_source(root): pass
            # Even removing the new module cannot turn this incomplete tree
            # into the optional/missing-layer path used by a heuristic selector.
            owned.unlink()
            with self.assertRaises((RuntimeError, FileNotFoundError, ValueError)):
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


    def test_adjacent_projection_imports_and_actual_stdin_loader_boundary(self):
        import subprocess
        import sys
        names = (
            'test-branding.py', 'test-input-lifecycle-integration-projection.py',
            'test-support-compiler-cleanup.py', 'test-support-file-dialog-lifecycle.py',
            'test-support-flutter-analysis.py',
            'test-support-launch-macos-concurrency-controls.py',
            'test-support-linux-child-window-close.py',
            'test-support-linux-window-close.py', 'test-support-macos-texture.py',
            'test-support-uri-routing.py',
        )
        for name in names:
            path = ROOT / 'scripts' / name
            tree = ast.parse(path.read_text(encoding='utf-8'))
            nodes = [node for node in tree.body if (
                isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id in
                    ('_input_lifecycle_spec', 'input_lifecycle', 'P')
                    for target in node.targets) and
                isinstance(node.value, ast.Call) and
                isinstance(node.value.func, ast.Attribute) and
                (node.value.func.attr == 'spec_from_file_location' or
                 (node.value.func.attr == 'module_from_spec' and
                  len(node.value.args) == 1 and
                  isinstance(node.value.args[0], ast.Name) and
                  node.value.args[0].id == '_input_lifecycle_spec'))
            ) or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and
                  isinstance(node.value.func, ast.Attribute) and
                  node.value.func.attr == 'exec_module' and
                  isinstance(node.value.func.value, ast.Attribute) and
                  isinstance(node.value.func.value.value, ast.Name) and
                  node.value.func.value.value.id == '_input_lifecycle_spec')]
            self.assertEqual(len(nodes), 3, name)
            before = list(sys.path)
            namespace = {'__file__': str(path), 'Path': Path, 'importlib': importlib}
            exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
            module = namespace.get('input_lifecycle', namespace.get('P'))
            self.assertEqual(module.WRAPPER, 'patch-support-input-lifecycle.py')
            self.assertEqual(Path(namespace['_input_lifecycle_spec'].origin),
                             path.with_name('input_lifecycle_integration_projection.py'))
            self.assertEqual(sys.path, before)
        body = """import runpy,sys
from pathlib import Path
root=Path(sys.argv[1]);proof=Path(sys.argv[2])
controls=runpy.run_path(str(root/'scripts/test-support-native-warnings.py'),run_name='mixel_shell_boundary_controls')
controls['shell_path_controls'](proof)
print('PASS: actual isolated stdin/runpy/file-spec import and seven strict shell refusals')
"""
        with tempfile.TemporaryDirectory(prefix='mixel-import-boundary-') as temporary:
            result = subprocess.run([sys.executable, '-I', '-', str(ROOT), temporary],
                                    input=body, text=True, capture_output=True,
                                    cwd=temporary, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('seven strict shell refusals', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
