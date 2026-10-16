#!/usr/bin/env python3
"""Verify exact SDK overlay, previous-layer conservation and real desktop APIs."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


S = load('patch-support-flutter-sdk-compatibility.py')
T = load('patch-support-macos-termination.py')
P = load('patch-support-macos-cleanup.py')
A = load('test-support-flutter-analysis.py')
F = load('patch-support-file-transfer-window.py')


def snapshot(root):
    result = {}
    for path in root.rglob('*'):
        mode = path.lstat().st_mode
        name = path.relative_to(root).as_posix()
        if stat.S_ISLNK(mode): result[name] = ('symlink', os.readlink(path))
        elif stat.S_ISREG(mode): result[name] = ('file', path.read_bytes(), path.stat().st_nlink)
        elif not stat.S_ISDIR(mode): result[name] = ('nonregular', mode)
    return result


def source_snapshot(source):
    result = {name: S.digest(S.regular(source, name))
              for name in {'Cargo.toml', *S.FILES, *T.FILES, *P.FILES, T.GENERATED, P.GENERATED}}
    for layer in ('flutter/.mixel-dependencies/macos_application_exit', P.OWNED, S.OWNED):
        path = source / layer
        if path.exists() or path.is_symlink():
            files, directories = S.inventory(source, layer)
            result[layer] = {'files': {name: S.digest(data) for name, data in files.items()},
                             'directories': sorted(directories)}
    if (source/F.GENERATED).exists() or (source/F.GENERATED).is_symlink():
        result[F.GENERATED]=S.digest(F.safe_path(source,F.GENERATED))
    return result


def capture(source, target, newline=b'\n', _input_checked=False):
    with A.startup_baseline(source, _input_checked=_input_checked) as plain:
        if plain != source.resolve():
            return capture(plain, target, newline, _input_checked=True)
    sdk_owned = (source / S.OWNED).exists() or (source / S.OWNED).is_symlink()
    if sdk_owned:
        F.verify_previous_layers(source) if (source/F.GENERATED).exists() else S.apply(source, check=True)
    target.mkdir()
    for name in {'Cargo.toml', *S.FILES, *T.FILES, *P.FILES, T.GENERATED, P.GENERATED}:
        data = S.regular(source, name)
        if name in F.COMPOSED and (source/F.GENERATED).exists(): data=F.transform(name,data,restore=True)
        normalized, _ = S.normalize(data)
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(normalized.replace(b'\n', newline))
    # Earlier utilities are deliberately emitted LF by their owning helpers.
    (target / P.GENERATED).write_bytes(P.UTILITY.encode())
    (target / T.GENERATED).write_bytes(T.UTILITY.encode())
    layers = ('flutter/.mixel-dependencies/macos_application_exit', P.OWNED)
    if sdk_owned: layers += (S.OWNED,)
    for relative in layers:
        members, directories = S.inventory(source, relative)
        for name in directories: (target / relative / name).mkdir(parents=True, exist_ok=True)
        for name, data in members.items():
            path = target / relative / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative in (P.OWNED, S.OWNED) and name == 'proof.json':
                proof = json.loads(data)
                proof['line_endings'] = {name: 'crlf' if newline == b'\r\n' else 'lf'
                                         for name in (P.FILES if relative == P.OWNED else S.FILES)}
                data = (json.dumps(proof, indent=2) + '\n').encode()
            path.write_bytes(data)
    if sdk_owned:
        # Preserve every actual ownership byte in the copied fixture, then use
        # the authoritative guarded reversal only there. The supplied app is
        # read-only; the fixture begins the same canonical unowned-overlay case.
        S.apply(target, restore=True)
        shutil.rmtree(target / S.OWNED)


def rejected(operation, root):
    before = snapshot(root)
    try:
        with contextlib.redirect_stdout(io.StringIO()): operation()
    except (RuntimeError, ValueError, OSError): pass
    else: raise AssertionError('Invalid SDK overlay ownership accepted')
    assert snapshot(root) == before, 'Invalid overlay changed actual source before refusing'


def pinned_archive(repository, revision, names, env=None):
    # text=auto also follows core.eol when autocrlf is false. Native Windows
    # archive conversion must never replace the exact LF Git blob hash pins.
    return subprocess.check_output(['git', '-C', str(repository), '-c', 'core.autocrlf=false',
                                    '-c', 'core.eol=lf', 'archive', '--format=tar', revision, *names], env=env)


def fresh_members(data):
    result = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            if member.isfile():
                assert member.name in S.FRESH and member.name not in result, 'Changed fresh SDK source inventory'
                original = archive.extractfile(member).read()
                assert S.digest(original) == S.FRESH[member.name], 'Changed fresh SDK source: ' + member.name
                result[member.name] = original
    assert set(result) == set(S.FRESH), 'Incomplete fresh SDK source inventory'
    return result


def archive_eol_controls(out, upstream):
    inherited = dict(os.environ, GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='core.autocrlf',
                     GIT_CONFIG_VALUE_0='false', GIT_CONFIG_KEY_1='core.eol', GIT_CONFIG_VALUE_1='crlf')
    # Reproduce the exact previous command on real pinned Git objects, without
    # changing the upstream checkout/configuration or normalizing accepted bytes.
    original_args = ['git', '-C', str(upstream), '-c', 'core.autocrlf=false',
                     'archive', '--format=tar', S.PIN, *S.FILES]
    previous = subprocess.check_output(original_args, env=inherited)
    corrected = pinned_archive(upstream, S.PIN, S.FILES, inherited)
    files = fresh_members(corrected)
    try: fresh_members(previous)
    except AssertionError: pass
    else: raise AssertionError('Original Windows-like archive unexpectedly retained the LF pins')
    old = {}
    with tarfile.open(fileobj=io.BytesIO(previous)) as archive:
        for member in archive:
            if member.isfile(): old[member.name] = archive.extractfile(member).read()
    assert set(old) == set(files)
    for name, data in files.items():
        blob = subprocess.check_output(['git', '-C', str(upstream), 'show', S.PIN + ':' + name])
        assert data == blob and S.digest(data) == S.FRESH[name]
        assert old[name] == blob.replace(b'\n', b'\r\n') and S.digest(old[name]) != S.FRESH[name]
    for autocrlf in ('false', 'true', 'input'):
        for eol in ('lf', 'crlf', 'native'):
            env = dict(inherited, GIT_CONFIG_VALUE_0=autocrlf, GIT_CONFIG_VALUE_1=eol)
            assert pinned_archive(upstream, S.PIN, S.FILES, env) == corrected
    refusals = 0
    for change in ('tampered', 'mixed', 'missing', 'duplicate'):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as archive:
            for index, (name, data) in enumerate(files.items()):
                if index == 0 and change == 'missing': continue
                if index == 0 and change == 'tampered': data += b'// foreign\n'
                if index == 0 and change == 'mixed': data = data.replace(b'\n', b'\r\n', 1)
                member = tarfile.TarInfo(name); member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
                if index == 0 and change == 'duplicate': archive.addfile(member, io.BytesIO(data))
        try: fresh_members(buffer.getvalue())
        except AssertionError: refusals += 1
        else: raise AssertionError('Invalid raw fresh archive accepted: ' + change)
    (out / 'real-git-archive-eol-proof.json').write_text(json.dumps({
        'original_raw_hash_refusal': True, 'original_command': original_args,
        'source': S.PIN, 'exact_six_git_blob_hashes': {name: S.digest(data) for name, data in files.items()},
        'original_crlf_member_hashes': {name: S.digest(data) for name, data in old.items()},
        'inherited_eol_autocrlf_cases': 9, 'raw_tampered_mixed_inventory_refusals': refusals,
        'runner_temporary_archive_bytes_available': False}, indent=2) + '\n', encoding='utf-8')
    print('PASS: original real Git CRLF archive fails all six raw pins; explicit LF preserves exact Git blobs under nine inherited settings; four tampered/mixed/inventory archives refuse.')


def materialization(out, source, upstream):
    controls = 0
    for label, newline in (('lf', b'\n'), ('crlf', b'\r\n')):
        fixture = out / label
        capture(source, fixture, newline)
        baseline = snapshot(fixture)
        S.apply(fixture)
        corrected = snapshot(fixture)
        S.apply(fixture); S.apply(fixture, check=True)
        assert snapshot(fixture) == corrected
        for name in S.FILES:
            original = S.regular(fixture / S.OWNED, name)
            assert S.digest(original) == S.FILES[name]
            assert (fixture / name).read_bytes() == S.transform(name, original).replace(b'\n', newline)
        # No native/FFI/lifecycle source outside the six targets changes.
        for name, value in baseline.items():
            if name not in S.FILES: assert corrected[name] == value, name
        S.apply(fixture, restore=True)
        for name, value in baseline.items(): assert snapshot(fixture)[name] == value, name
        S.apply(fixture, restore=True); S.apply(fixture)
        assert snapshot(fixture) == corrected
        controls += 5
        mutations = {}
        for name in {*S.FILES, *T.FILES, *P.FILES, T.GENERATED, P.GENERATED}:
            mutations['target-' + name.replace('/', '_')] = lambda root, name=name: (root / name).write_bytes((root / name).read_bytes() + b'// drift\n')
        for name in S.FILES:
            mutations['original-' + name.replace('/', '_')] = lambda root, name=name: (root / S.OWNED / name).write_bytes((root / S.OWNED / name).read_bytes() + b'// drift\n')
        mutations.update({
            'mixed-targets': lambda root: (root / next(iter(S.FILES))).write_bytes(S.regular(root / S.OWNED, next(iter(S.FILES))).replace(b'\n', newline)),
            'mixed-newline': lambda root: (root / next(iter(S.FILES))).write_bytes((root / next(iter(S.FILES))).read_bytes() + b'\r\n\n'),
            'missing-owned': lambda root: shutil.rmtree(root / S.OWNED),
            'missing-previous-utility': lambda root: (root / T.GENERATED).unlink(),
            'missing-cleanup-utility': lambda root: (root / P.GENERATED).unlink(),
            'missing-previous-owned': lambda root: shutil.rmtree(root / 'flutter/.mixel-dependencies/macos_application_exit'),
            'missing-previous-both': lambda root: ((root / T.GENERATED).unlink(), shutil.rmtree(root / 'flutter/.mixel-dependencies/macos_application_exit')),
            'missing-cleanup-owned': lambda root: shutil.rmtree(root / P.OWNED),
            'missing-cleanup-both': lambda root: ((root / P.GENERATED).unlink(), shutil.rmtree(root / P.OWNED)),
            'extra-owned-file': lambda root: (root / S.OWNED / 'extra').write_bytes(b'foreign'),
            'extra-owned-dir': lambda root: (root / S.OWNED / 'extra').mkdir(),
            'bad-proof': lambda root: (root / S.OWNED / 'proof.json').write_text('{}'),
            'bad-cleanup-proof': lambda root: (root / P.OWNED / 'proof.json').write_text('{}'),
            'duplicate-proof': lambda root: (root / S.OWNED / 'proof.json').write_text((root / S.OWNED / 'proof.json').read_text().replace('{', '{"schema":1,', 1)),
            'hardlink': lambda root: os.link(root / next(iter(S.FILES)), root / 'linked'),
            'symlink-original': lambda root: ((root / S.OWNED / next(iter(S.FILES))).unlink(), (root / S.OWNED / next(iter(S.FILES))).symlink_to(root / next(iter(S.FILES)))),
        })
        if hasattr(os, 'mkfifo'):
            mutations['fifo-owned'] = lambda root: os.mkfifo(root / S.OWNED / 'fifo')
            mutations['fifo-prior'] = lambda root: os.mkfifo(root / P.OWNED / 'fifo')
        for mutation, change in mutations.items():
            bad = out / (label + '-' + mutation)
            shutil.copytree(fixture, bad)
            change(bad)
            for mode in ('apply', 'check', 'restore'):
                rejected(lambda: S.apply(bad, restore=mode == 'restore', check=mode == 'check'), bad)
                controls += 1
        bad = out / (label + '-owned-parent-symlink')
        shutil.copytree(fixture, bad)
        parent = bad / 'flutter/.mixel-dependencies'
        parent.rename(bad / 'retained-dependencies')
        parent.symlink_to('../retained-dependencies', target_is_directory=True)
        for mode in ('apply', 'check', 'restore'):
            rejected(lambda: S.apply(bad, restore=mode == 'restore', check=mode == 'check'), bad)
            controls += 1
    data = pinned_archive(upstream, S.PIN, S.FILES)
    inherited = dict(os.environ, GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='core.autocrlf',
                     GIT_CONFIG_VALUE_0='true', GIT_CONFIG_KEY_1='core.eol', GIT_CONFIG_VALUE_1='crlf')
    assert pinned_archive(upstream, S.PIN, S.FILES, inherited) == data
    originals = fresh_members(data)
    for label, newline in (('lf', b'\n'), ('crlf', b'\r\n')):
        fresh = out / ('fresh-' + label); fresh.mkdir()
        (fresh / 'Cargo.toml').write_bytes(b'[package]\nversion = "1.4.6"\n'.replace(b'\n', newline))
        for name, original in originals.items():
            path = fresh / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(original.replace(b'\n', newline))
        before = snapshot(fresh); S.apply(fresh, restore=True); assert snapshot(fresh) == before
        rejected(lambda: S.apply(fresh), fresh)
        controls += 2
    print(f'PASS: {controls} exact SDK overlay, newline, prior-layer and zero-mutation ownership controls.')


def previous_helper_controls(out, source):
    count = 0
    for helper in ('patch-support-macos-termination.py', 'patch-support-macos-cleanup.py'):
        for label in ('lf', 'crlf', 'mixed', 'bare-cr', 'tampered', 'missing-final-newline'):
            copied = out / (helper + '-' + label); copied.mkdir()
            for name in ('patch-support-flutter-sdk-compatibility.py', 'patch-support-macos-termination.py', 'patch-support-macos-cleanup.py'):
                (copied / name).write_bytes((ROOT / 'scripts' / name).read_bytes())
            path = copied / helper
            original, _ = S.normalize(path.read_bytes())
            data = original if label == 'lf' else original.replace(b'\n', b'\r\n') if label == 'crlf' else original
            if label == 'mixed': data = original.replace(b'\n', b'\r\n', 1)
            elif label == 'bare-cr': data = original + b'\r'
            elif label == 'tampered': data = original + b'# foreign\n'
            elif label == 'missing-final-newline': data = original.rstrip(b'\n')
            path.write_bytes(data)
            spec = importlib.util.spec_from_file_location('copied_sdk_overlay', copied / 'patch-support-flutter-sdk-compatibility.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            root = copied / 'source'; capture(source, root)
            if label in ('lf', 'crlf'):
                module.apply(root); before = snapshot(root)
                module.apply(root, check=True); assert snapshot(root) == before
                module.apply(root, restore=True)
                assert all(S.digest(S.regular(root, name)) == S.FILES[name] for name in S.FILES)
                count += 3
            else:
                for mode in ('apply', 'check', 'restore'):
                    rejected(lambda: module.apply(root, restore=mode == 'restore', check=mode == 'check'), root)
                    count += 1
    print(f'PASS: {count} actual LF/CRLF prior helper execution and malformed-source zero-mutation controls.')


DART = r'''
import 'package:flutter_test/flutter_test.dart';
import 'package:file_picker/file_picker.dart';
import 'package:file_picker/src/file_picker_macos.dart';
import 'package:file_picker/src/linux/file_picker_linux.dart';
import 'package:file_picker/src/windows/file_picker_windows.dart';
void main() {
 TestWidgetsFlutterBinding.ensureInitialized();
 test('official desktop registration selects each exact Dart implementation', () {
  FilePickerMacOS.registerWith(); expect(FilePicker.platform,isA<FilePickerMacOS>());
  FilePickerLinux.registerWith(); expect(FilePicker.platform,isA<FilePickerLinux>());
  FilePickerWindows.registerWith(); expect(FilePicker.platform,isA<FilePickerWindows>());
 });
 test('Mac directory dialog retains initial directory and prompt', () {
  final picker=FilePickerMacOS();
  expect(picker.generateCommandLineArguments('Change',pickDirectory:true,initialDirectory:'/tmp'),
    ['-e','choose folder default location "/tmp" with prompt "Change"']);
 });
 test('Mac screenshot save retains filename and initial directory', () {
  final picker=FilePickerMacOS();
  expect(picker.generateCommandLineArguments('Save as...',saveFile:true,fileName:'screenshot_1.png',initialDirectory:'/tmp'),
    ['-e','choose file name default name "screenshot_1.png" default location "/tmp" with prompt "Save as..."']);
  expect(picker.fileTypeToFileFilter(FileType.custom,['png']),'"", "png"');
  expect(() => picker.fileTypeToFileFilter(FileType.any,['png']),throwsArgumentError);
 });
 test('Mac actual parser preserves selected directory and saved file paths', () {
  final picker=FilePickerMacOS();
  expect(picker.resultStringToFilePaths('alias Macintosh HD:Users:QA:Pictures:'),['/Volumes/Macintosh HD/Users/QA/Pictures']);
  expect(picker.resultStringToFilePaths('file Macintosh HD:Users:QA:screenshot_1.png'),['/Volumes/Macintosh HD/Users/QA/screenshot_1.png']);
  expect(picker.resultStringToFilePaths('  '),isEmpty);
 });
 test('public file result bytes/path metadata contract remains available', () {
  final file=PlatformFile(name:'capture.png',path:'/tmp/capture.png',size:4);
  final result=FilePickerResult([file]);
  expect(result.files.single.name,'capture.png'); expect(result.paths.single,'/tmp/capture.png');
 });
}
'''


def native_command(args, windows=False):
    args = list(map(str, args))
    if windows and Path(args[0]).name.lower() in ('flutter', 'flutter.bat'):
        launcher = Path(args[0]).with_name('flutter.bat')
        if not launcher.is_file():
            raise RuntimeError('Missing native Windows Flutter launcher')
        args[0] = str(launcher)
        if any(any(character in arg for character in '\r\n"%!^&|<>') for arg in args):
            raise RuntimeError('Unsafe native Flutter batch fixture argument')
        return 'cmd.exe /d /s /c "' + ' '.join('"' + arg + '"' for arg in args) + '"'
    return args


def run(args, cwd, out, label, env=None, timeout=300):
    actual_env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1', CI='true', FLUTTER_SUPPRESS_ANALYTICS='true', DART_SUPPRESS_ANALYTICS='true')
    if env: actual_env.update(env)
    # Git Bash resolves Flutter's extensionless shell entry point on Windows.
    # Prefer the SDK's actual batch launcher and the current host command shell.
    args = native_command(args, os.name == 'nt')
    result = subprocess.run(args, cwd=cwd, env=actual_env, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    (out / (label + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
    assert result.returncode == 0, label + ' failed; see retained log'
    return result.stdout


def host_portability_controls(out):
    sdk = out / 'SDK with spaces'; sdk.mkdir()
    launcher = sdk / 'flutter.bat'; launcher.write_text('fixture only', encoding='utf-8')
    actual = native_command([sdk / 'flutter', 'analyze', '--no-fatal-infos'], windows=True)
    assert actual == 'cmd.exe /d /s /c ""' + str(launcher) + '" "analyze" "--no-fatal-infos""'
    assert native_command([sdk / 'flutter', 'analyze']) == [str(sdk / 'flutter'), 'analyze']
    count = 2
    for character in '\r\n"%!^&|<>':
        try: native_command([launcher, 'foreign' + character], windows=True)
        except RuntimeError: count += 1
        else: raise AssertionError('Unsafe Windows launcher argument accepted')
    launcher.unlink()
    try: native_command([sdk / 'flutter', 'analyze'], windows=True)
    except RuntimeError: count += 1
    else: raise AssertionError('Missing Windows launcher accepted')
    output = run([sys.executable, '-c', "import os; assert os.environ['PYTHONIOENCODING']=='utf-8'; print('\\u2192 complete')"], out, out, 'real-captured-unicode-child')
    assert output == '\u2192 complete\n'
    print(f'PASS: {count} Windows batch command/refusal controls and real UTF8 child capture.')


def flutter_controls(out, source, flutter, _input_checked=False):
    with A.startup_baseline(source, _input_checked=_input_checked) as plain:
        if plain != source.resolve():
            return flutter_controls(out, plain, flutter, _input_checked=True)
    app = out / 'actual-full-app'
    shutil.copytree(source, app, ignore=shutil.ignore_patterns('.git', '.dart_tool', 'build', '.mixel-dependencies'))
    # Bring forward the exact locally owned plugins/provenance without caches.
    shutil.copytree(source / 'flutter/.mixel-dependencies', app / 'flutter/.mixel-dependencies')
    file_transfer=(app/F.GENERATED).exists() or (app/F.GENERATED).is_symlink()
    if file_transfer: F.apply(app,restore=True)
    S.apply(app)
    if file_transfer: F.apply(app)
    run([flutter, '--version', '--machine'], app, out, 'actual-sdk-version')
    version = json.loads((out / 'actual-sdk-version.log').read_text())
    assert version['frameworkVersion'] == S.SDK and version['engineRevision'] == S.ENGINE
    run([flutter, 'pub', 'get'], app / 'flutter', out, 'actual-pub-get')
    log = (out / 'actual-pub-get.log').read_text()
    assert 'does not provide an inline implementation' not in log
    lock = (app / 'flutter/pubspec.lock').read_text()
    picker = re.search(r'(?ms)^  file_picker:\n(.*?)(?=^  [^ ]|\Z)', lock)
    assert picker is not None and 'version: "8.1.2"' in picker[1]
    assert '167bb619cdddaa10ef2907609feb8a79c16dfa479d3afaf960f8e223f754bf12' in picker[1]
    dependency = out / 'actual-desktop-dependency'
    (dependency / 'test').mkdir(parents=True)
    (dependency / 'pubspec.yaml').write_text('name: mixel_sdk_dependency_controls\n'
        'environment:\n  sdk: ">=3.6.0 <4.0.0"\n'
        'dependencies:\n  flutter:\n    sdk: flutter\n  file_picker: 8.1.2\n'
        'dev_dependencies:\n  flutter_test:\n    sdk: flutter\n', encoding='utf-8')
    run([flutter, 'pub', 'get'], dependency, out, 'actual-desktop-dependency-pub-get')
    tests = dependency / 'test/mixel_sdk_compatibility_test.dart'
    tests.write_text(DART, encoding='utf-8')
    run([flutter, 'test', '--reporter', 'expanded', str(tests.relative_to(dependency))], dependency, out, 'actual-desktop-file-picker-tests')
    run([flutter, 'analyze', '--no-pub', '--no-fatal-infos'], app / 'flutter', out, 'actual-full-app-analysis')
    print('PASS: actual Flutter 3.27.4 full app has zero errors/warnings and five real desktop dependency tests pass.')


def full_branding(out, upstream, bash):
    copied = out / 'pipeline-source'; copied.mkdir()
    archive = pinned_archive(ROOT, 'HEAD', ('scripts', 'branding'))
    inherited = dict(os.environ, GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='core.autocrlf',
                     GIT_CONFIG_VALUE_0='true', GIT_CONFIG_KEY_1='core.eol', GIT_CONFIG_VALUE_1='crlf')
    assert pinned_archive(ROOT, 'HEAD', ('scripts', 'branding'), inherited) == archive
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for member in stream:
            if member.isfile():
                path = copied / member.name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(stream.extractfile(member).read())
    for name in ('apply-branding.sh','patch-support-url-log-privacy.py','test-support-url-log-privacy.py', 'patch-support-flutter-sdk-compatibility.py',
                 'test-support-flutter-sdk-compatibility.py', 'patch-support-macos-termination.py',
                 'patch-support-file-transfer-window.py', 'test-support-file-transfer-window.py',
                 'patch-support-file-dialog-lifecycle.py', 'test-support-file-dialog-lifecycle.py',
                 "patch-support-input-lifecycle.py","support-input-lifecycle-engine.py","support-input-lifecycle-plan.json",
                 "input_lifecycle_integration_projection.py",
                 'patch-support-keyboard-default.py',
                 'patch-support-uinput-scroll.py',
                 'patch-support-uinput-device.py','test-support-uinput-device.py',
                 'patch-support-native-warnings.py','test-support-native-warnings.py',
                 'patch-support-compiler-cleanup.py','test-support-compiler-cleanup.py',
                 'patch-support-uri-routing.py','test-support-uri-routing.py',
                 'patch-support-macos-gui-launch.py', 'test-support-macos-gui-launch.py', 'support-macos-gui-launch.rs',
                 'patch-support-fingerprint.py','test-support-fingerprint.py',
                 'patch-support-fatal-crash.py','test-support-fatal-crash.py',
                 'patch-macos-update.py','test-macos-update.py','macos-update.scpt','macos-update-bundle.sh'):
        (copied / 'scripts' / name).write_bytes((ROOT / 'scripts' / name).read_bytes())
    A.copy_current_pipeline_inputs(copied)
    script = copied / 'scripts/apply-branding.sh'
    text = script.read_text(encoding='utf-8')
    prefix = 'python3 "$(dirname "${BASH_SOURCE[0]}")/'
    early = prefix + 'patch-support-flutter-sdk-compatibility.py" --restore "$RDREPO"\n'
    late = prefix + 'patch-support-flutter-sdk-compatibility.py" "$RDREPO"\n'
    restore = prefix + 'patch-support-macos-termination.py" --restore "$RDREPO"\n'
    apply_exit = prefix + 'patch-support-macos-termination.py" "$RDREPO"\n'
    assert text.count(restore) == 1 and text.count(apply_exit) == 1
    if early not in text: text = text.replace(restore, early + restore)
    if late not in text: text = text.replace(apply_exit, apply_exit + late)
    assert text.count(early) == 1 and text.count(late) == 1
    script.write_bytes(text.encode())
    for name, expected in (('patch-support-macos-termination.py', S.PRIOR_SHA),
                           ('patch-support-macos-cleanup.py', S.CLEANUP_SHA)):
        helper = copied / 'scripts' / name
        data, _ = S.normalize(helper.read_bytes()); assert S.digest(data) == expected
        helper.write_bytes(data.replace(b'\n', b'\r\n'))
    report = {}
    for label, newline in (('lf', b'\n'), ('crlf', b'\r\n')):
        repo = out / ('pipeline-' + label)
        run(['git', '-c', 'core.autocrlf=false', 'clone', '--quiet', '--shared', upstream, repo], copied, out, 'clone-' + label)
        run(['git', '-C', repo, '-c', 'core.autocrlf=false', 'checkout', '--quiet', S.PIN], copied, out, 'checkout-' + label)
        library = upstream / 'libs/hbb_common'
        run(['git', '-c', 'core.autocrlf=false', 'clone', '--quiet', '--shared', library, repo / 'libs/hbb_common'], copied, out, 'hbb-clone-' + label)
        pin = subprocess.check_output(['git', '-C', str(repo), 'ls-tree', 'HEAD', 'libs/hbb_common'], text=True).split()[2]
        run(['git', '-C', repo / 'libs/hbb_common', '-c', 'core.autocrlf=false', 'checkout', '--quiet', pin], copied, out, 'hbb-checkout-' + label)
        for name in {'Cargo.toml', *T.FRESH, *S.FRESH, *P.FILES}:
            original = subprocess.check_output(['git', '-C', str(repo), 'show', S.PIN + ':' + name])
            (repo / name).write_bytes(original.replace(b'\n', newline))
        env = dict(RDREPO=str(repo), BRANDING=str(copied / 'branding'))
        for attempt in (1, 2):
            run([bash, script], copied, out, f'pipeline-{label}-{attempt}', env)
            with A.startup_baseline(repo) as checked_repo:
                F.verify_previous_layers(checked_repo) if (checked_repo/F.GENERATED).exists() else S.apply(checked_repo, check=True)
                for name in S.FILES:
                    original = S.regular(checked_repo / S.OWNED, name)
                    assert S.digest(original) == S.FILES[name]
                    actual=S.normalize(S.regular(checked_repo, name))[0]
                    if name in F.COMPOSED and (checked_repo/F.GENERATED).exists(): actual=F.transform(name,actual,restore=True)
                    assert actual == S.transform(name, original)
                for name in T.FILES:
                    original = T.regular(checked_repo / 'flutter/.mixel-dependencies/macos_application_exit', name)
                    assert T.digest(original) == T.FILES[name]
                    actual = S.regular(checked_repo / S.OWNED, name) if name in S.FILES else S.normalize(S.regular(checked_repo, name))[0]
                    if name in F.COMPOSED and name not in S.FILES and (checked_repo/F.GENERATED).exists(): actual=F.transform(name,actual,restore=True)
                    assert actual == T.transform(name, original), name
                assert S.regular(checked_repo, T.GENERATED) == T.UTILITY.encode()
                assert S.regular(checked_repo, P.GENERATED) == P.UTILITY.encode()
            current = {name: value for name, value in snapshot(repo).items() if not name.startswith('.git/') and '/.git/' not in name}
            if attempt == 1: first = current
            else: assert current == first, 'Composed full branding changed on the second pass'
        for name in ('flutter/lib/common.dart', 'flutter/lib/desktop/pages/file_manager_page.dart', 'flutter/lib/web/bridge.dart'):
            target = repo / name; saved = target.read_bytes(); target.write_bytes(saved + b'// drift\n')
            before = snapshot(repo)
            actual_env = dict(os.environ, **env, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
            failed = subprocess.run([str(bash), str(script)], cwd=copied, env=actual_env, capture_output=True, text=True, encoding='utf-8', timeout=300)
            (out / ('pipeline-' + label + '-drift-' + Path(name).stem + '.log')).write_text(failed.stdout + failed.stderr, encoding='utf-8')
            assert failed.returncode != 0 and snapshot(repo) == before, 'Composed pipeline wrote before drift refusal'
            target.write_bytes(saved)
        report[label] = {'whole_pipeline_passes': 2, 'all_six_outputs_exact': True,
                         'all_prior_runtime_bodies_conserved': True, 'zero_mutation_drift_cases': 3}
    (out / 'full-pipeline-proof.json').write_text(json.dumps({'results': report,
        'prior_helper_execution_newline': 'crlf', 'early_restore_before_termination': True,
        'final_overlay_after_termination': True}, indent=2) + '\n', encoding='utf-8')
    print('PASS: full LF/CRLF branding twice, exact six-file final overlay, prior runtime conservation and zero-mutation composed drift.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=ROOT / 'rustdesk')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--flutter', type=Path)
    parser.add_argument('--full-branding', action='store_true')
    parser.add_argument('--bash', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='mixel-sdk-compatibility-') as directory:
        out = (args.output or Path(directory)).resolve()
        if out.is_relative_to(args.app.resolve()) or out.is_relative_to(args.source.resolve()):
            raise RuntimeError('SDK verification output must be outside its read-only input checkout')
        out.mkdir(parents=True, exist_ok=True)
        before = source_snapshot(args.app.resolve())
        archive_eol_controls(out, args.source.resolve())
        materialization(out, args.app.resolve(), args.source.resolve())
        previous_helper_controls(out, args.app.resolve())
        host_portability_controls(out)
        if args.full_branding: full_branding(out, args.source.resolve(), Path(A.branding_bash(args.bash, os.name == 'nt')))
        if args.flutter: flutter_controls(out, args.app.resolve(), args.flutter.resolve())
        assert source_snapshot(args.app.resolve()) == before, 'Supplied SDK/prior-layer source changed'
        (out / 'read-only-input-proof.json').write_text(json.dumps(before, sort_keys=True, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__': main()
