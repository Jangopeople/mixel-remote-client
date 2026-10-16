#!/usr/bin/env python3
"""Exercise strict QA text views of exact Git workflow and branding bytes.

These tests execute the actual source consumers up to their first unrelated
source/runtime action. Native tools, app processes and source owners never run.
"""
import ast
import copy
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
NAME = '.github/workflows/build.yml'
MACRO = 'scripts/apply-branding.sh'
SPEC = importlib.util.spec_from_file_location(
    'mixel_logical_views_projection', ROOT / 'scripts/input_lifecycle_integration_projection.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def function(name, selected, *, stop_before=None, result=None):
    """Compile the actual function body, with an explicit side-effect boundary."""
    path = ROOT / 'scripts' / name
    tree = ast.parse(path.read_text(encoding='utf-8'))
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == selected]
    assert len(matches) == 1, 'Actual QA consumer must be unique'
    node = copy.deepcopy(matches[0])
    if stop_before is not None:
        stops = [index for index, statement in enumerate(node.body)
                 if isinstance(statement, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == stop_before
                         for target in statement.targets)]
        assert len(stops) == 1, 'The explicit source/runtime boundary changed'
        node.body = node.body[:stops[0]]
        if result is not None:
            node.body.append(ast.Return(ast.parse(result, mode='eval').body))
    namespace = {'Path': Path, 'ROOT': ROOT, 'input_lifecycle': P,
                 'subprocess': subprocess, 're': re, 'textwrap': textwrap,
                 'shutil': shutil, '__file__': str(path)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 str(path), 'exec'), namespace)
    return namespace


def texture_namespace(root=ROOT, committed=None):
    namespace = function('test-support-macos-texture.py', 'workflow_controls',
                         stop_before='window_spec',
                         result='(workflow, committed, script, native_commands)')
    guard = function('test-support-macos-texture.py', 'exact_bridge_native_close_commands')
    mutations = function('test-support-macos-texture.py', 'bridge_native_close_mutation_controls')
    mutations['exact_bridge_native_close_commands'] = guard['exact_bridge_native_close_commands']
    namespace.update(ROOT=root, exact_bridge_native_close_commands=guard['exact_bridge_native_close_commands'])
    if committed is not None:
        def exact_git(arguments):
            assert arguments == ['git', '-C', str(root), 'show', 'HEAD:' + NAME]
            return committed
        namespace['subprocess'] = SimpleNamespace(check_output=exact_git)
    return namespace, guard['exact_bridge_native_close_commands'], mutations['bridge_native_close_mutation_controls']


def materialize(root, name, raw):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def profile(raw, newline):
    return P.canonical(raw)[0].encode().replace(b'\n', newline)


def macro_namespaces(root):
    branding = function('test-branding.py', 'historical_footer_branding_command')
    tree = ast.parse((ROOT / 'scripts/test-branding.py').read_text(encoding='utf-8'))
    definitions = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'TERMINAL_FOOTER_LAYERS'
                           for target in node.targets)]
    assert len(definitions) == 1
    branding.update(ROOT=root, SCRIPT=root / MACRO, BASH='bash',
                    TERMINAL_FOOTER_LAYERS=ast.literal_eval(definitions[0].value))
    compiler = function('test-support-compiler-cleanup.py', 'composed_controls',
                        stop_before='repo', result='macro_script')
    uri = function('test-support-flutter-analysis.py', 'without_uri_owner_commands')
    compiler.update(__file__=str(root / 'scripts/test-support-compiler-cleanup.py'),
                    historical_fixture=SimpleNamespace(
                        without_uri_owner_commands=uri['without_uri_owner_commands']))
    return branding, compiler


class LogicalViews(unittest.TestCase):
    def test_actual_checkout_workflow_matches_real_committed_git_blob(self):
        namespace, _, _ = texture_namespace()
        with tempfile.TemporaryDirectory(prefix='mixel actual workflow view ') as temporary:
            working, committed, script, commands = namespace['workflow_controls'](Path(temporary), None, None)
        self.assertEqual(working, committed)
        self.assertNotIn('\r', working)
        self.assertEqual(len(commands), 6)
        self.assertIn('flutter_rust_bridge_codegen', script)

    def test_complete_working_and_head_lf_crlf_profiles(self):
        raw = (ROOT / NAME).read_bytes()
        with tempfile.TemporaryDirectory(prefix='mixel LF CRLF views ') as temporary:
            root = Path(temporary)
            for working_eol in (b'\n', b'\r\n'):
                for head_eol in (b'\n', b'\r\n'):
                    with self.subTest(working=working_eol, head=head_eol):
                        materialize(root, NAME, profile(raw, working_eol))
                        namespace, _, _ = texture_namespace(root, profile(raw, head_eol))
                        working, committed, _, commands = namespace['workflow_controls'](root, None, None)
                        self.assertEqual(working, committed)
                        self.assertEqual(len(commands), 6)

    def test_original_inverse_preserves_each_complete_byte_profile(self):
        for name in (NAME, MACRO):
            raw = (ROOT / name).read_bytes()
            for ending in (b'\n', b'\r\n'):
                current = profile(raw, ending)
                inverse = P.project_to_canonical(current, name)
                self.assertEqual(P.canonical(inverse)[1], ending)
                self.assertEqual(P.compose(inverse, name), current)

    def test_mixed_bare_cr_bom_invalid_utf8_working_views_refuse(self):
        raw = profile((ROOT / NAME).read_bytes(), b'\n')
        malformed = (raw.replace(b'\n', b'\r\n', 1), raw + b'\r',
                     b'\xef\xbb\xbf' + raw, raw + b'\xff')
        with tempfile.TemporaryDirectory(prefix='mixel invalid working ') as temporary:
            root = Path(temporary)
            for value in malformed:
                materialize(root, NAME, value)
                namespace, _, _ = texture_namespace(root, raw)
                with self.assertRaises((ValueError, UnicodeError, AssertionError)):
                    namespace['workflow_controls'](root, None, None)
                self.assertEqual((root / NAME).read_bytes(), value)

    def test_mixed_bare_cr_bom_invalid_utf8_head_views_refuse(self):
        raw = profile((ROOT / NAME).read_bytes(), b'\n')
        malformed = (raw.replace(b'\n', b'\r\n', 1), raw + b'\r',
                     b'\xef\xbb\xbf' + raw, raw + b'\xff')
        with tempfile.TemporaryDirectory(prefix='mixel invalid HEAD ') as temporary:
            root = Path(temporary)
            materialize(root, NAME, raw)
            for value in malformed:
                namespace, _, _ = texture_namespace(root, value)
                with self.assertRaises((ValueError, UnicodeError, AssertionError)):
                    namespace['workflow_controls'](root, None, None)
                self.assertEqual((root / NAME).read_bytes(), raw)

    def test_real_head_equality_preserves_unrelated_drift_refusal(self):
        raw = profile((ROOT / NAME).read_bytes(), b'\n')
        with tempfile.TemporaryDirectory(prefix='mixel HEAD drift ') as temporary:
            root = Path(temporary)
            for working, head in ((raw + b'# unexpected drift\n', raw),
                                  (raw, raw + b'# unexpected drift\n')):
                materialize(root, NAME, working)
                namespace, _, _ = texture_namespace(root, head)
                with self.assertRaisesRegex(AssertionError, 'No production build job'):
                    namespace['workflow_controls'](root, None, None)

    def test_all_original_28_hook_count_order_and_placement_mutations_refuse(self):
        raw = (ROOT / NAME).read_bytes()
        for ending in (b'\n', b'\r\n'):
            namespace, guard, mutations = texture_namespace()
            inverse = P.canonical(P.inverse_blocks(profile(raw, ending), NAME))[0]
            self.assertEqual(len(guard(inverse)), 6)
            self.assertEqual(mutations(inverse), 28)

    def test_missing_duplicate_and_changed_input_owner_hunks_still_refuse(self):
        raw = profile((ROOT / NAME).read_bytes(), b'\n')
        block = P.NATIVE_REGRESSIONS.encode()
        values = (raw.replace(block, b'', 1), raw.replace(block, block * 2, 1),
                  raw.replace(b'--expected-count 180', b'--expected-count 179', 1))
        with tempfile.TemporaryDirectory(prefix='mixel exact hunks ') as temporary:
            root = Path(temporary)
            for ending in (b'\n', b'\r\n'):
                for value in values:
                    materialize(root, NAME, value.replace(b'\n', ending))
                    namespace, _, _ = texture_namespace(root, raw)
                    with self.assertRaises(ValueError):
                        namespace['workflow_controls'](root, None, None)

    def test_actual_integration_historical_guards_in_both_profiles(self):
        path = ROOT / 'scripts/test-input-lifecycle-integration-projection.py'
        spec = importlib.util.spec_from_file_location('mixel_historical_guards', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        names = ('test_actual_six_command_texture_guard_unchanged',
                 'test_actual_uri_linux_bridge_historical_guards_unchanged',
                 'test_other_drift_stays_fatal_at_original_hash')
        raw = (ROOT / NAME).read_bytes()
        macro = (ROOT / MACRO).read_bytes()
        with tempfile.TemporaryDirectory(prefix='mixel actual historical guards ') as temporary:
            root = Path(temporary)
            for ending in (b'\n', b'\r\n'):
                materialize(root, NAME, profile(raw, ending))
                materialize(root, MACRO, profile(macro, ending))
                with mock.patch.object(module, 'ROOT', root), mock.patch.object(
                        module, 'actual_functions', self.actual_guard_functions):
                    for name in names:
                        with self.subTest(ending=ending, guard=name):
                            getattr(module.Controls(name), name)()

    @staticmethod
    def actual_guard_functions(name, functions):
        path = ROOT / name
        nodes = [node for node in ast.parse(path.read_text(encoding='utf-8')).body
                 if isinstance(node, ast.FunctionDef) and node.name in functions]
        assert len(nodes) == len(functions)
        namespace = {'Path': Path}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
        return namespace

    def test_actual_branding_and_compiler_macro_views_lf_crlf(self):
        raw = (ROOT / MACRO).read_bytes()
        expected = P.canonical(P.project_to_canonical(raw, MACRO))[0]
        with tempfile.TemporaryDirectory(prefix='mixel macro profiles ') as temporary:
            base = Path(temporary)
            for index, ending in enumerate((b'\n', b'\r\n')):
                root = base / str(index)
                materialize(root, MACRO, profile(raw, ending))
                materialize(root, 'scripts/unchanged-helper.py', b'# exact sibling\n')
                branding, compiler = macro_namespaces(root)
                for layers in (('uri',), ('uri', 'native', 'linux')):
                    with self.subTest(ending=ending, consumer='branding', layers=layers):
                        command = branding['historical_footer_branding_command'](root, layers)
                        value = Path(command[1]).read_bytes()
                        self.assertNotIn(b'\r', value)
                        self.assertNotIn(P.WRAPPER.encode(), value)
                with self.subTest(ending=ending, consumer='compiler'):
                    out = root / 'compiler-proof'; out.mkdir()
                    macro = compiler['composed_controls'](None, out, None)
                    uri = compiler['historical_fixture'].without_uri_owner_commands
                    self.assertEqual(macro.read_bytes(), uri(expected).encode())
                self.assertEqual((root / 'scripts/unchanged-helper.py').read_bytes(), b'# exact sibling\n')

    def test_malformed_macro_profiles_refuse_before_historical_write(self):
        raw = profile((ROOT / MACRO).read_bytes(), b'\n')
        malformed = (raw.replace(b'\n', b'\r\n', 1), raw + b'\r',
                     b'\xef\xbb\xbf' + raw, raw + b'\xff', raw + b'# unreviewed drift\n')
        with tempfile.TemporaryDirectory(prefix='mixel malformed macros ') as temporary:
            base = Path(temporary)
            for index, value in enumerate(malformed):
                root = base / str(index)
                materialize(root, MACRO, value)
                branding, compiler = macro_namespaces(root)
                with self.assertRaises((ValueError, UnicodeError)):
                    branding['historical_footer_branding_command'](root, ('uri',))
                out = root / 'compiler-proof'; out.mkdir()
                with self.assertRaises((ValueError, UnicodeError)):
                    compiler['composed_controls'](None, out, None)
                self.assertEqual((root / MACRO).read_bytes(), value)


if __name__ == '__main__':
    unittest.main(verbosity=2)
