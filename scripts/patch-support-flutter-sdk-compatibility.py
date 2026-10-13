#!/usr/bin/env python3
"""Apply the exact source compatibility overlay for official Flutter 3.27.4."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import tempfile

PIN = "1abc897c451c8b5bbff3792509a7fef9d12f2ce3"
SDK = "3.27.4"
ENGINE = "82bd5b7209295a5b7ff8cae0df96e7870171e3a5"
OWNED = "flutter/.mixel-dependencies/flutter_sdk_compatibility"
PRIOR_SHA = "d065176a68d9eaeb5543f4b61000e0d9e646b0ffee19ef928ecb50bf183d1c4f"
CLEANUP_SHA = "197bc021abf3cb8563b92dfca30d5ba7f1c93328db4318cf56386baa9cf6a0fb"
HISTORICAL_FILES = {'flutter/lib/desktop/pages/desktop_setting_page.dart': '21633c93e76a9f1a57801855c8146366691549f8d3d1f20cedd779f9fed0a292', 'flutter/lib/desktop/pages/view_camera_page.dart': 'aa8c1b3fda2e12cb292dd73cdcd229c7b8c955f0a39fbfe74e03388db0507939', 'flutter/lib/desktop/widgets/material_mod_popup_menu.dart': 'b6f9d99567272fcb5a7642d8930e522be3ae6876322228ab3e46370ab710a354', 'flutter/lib/desktop/widgets/tabbar_widget.dart': 'd05d6fd84b6c2f51775dfb7e2eb928367a81addbba0dbef56adc2494935685f6', 'flutter/lib/web/bridge.dart': 'd8a81b4d7fee5186430d7624390db2b61a3978481b757e5d9fb791d74c26fbc6', 'flutter/pubspec.yaml': '7884f460420fba8aec82fa434fddcadc564c86ad54dce298705555e4e51b7d7b'}
FILES = {'flutter/lib/desktop/pages/desktop_setting_page.dart': '21633c93e76a9f1a57801855c8146366691549f8d3d1f20cedd779f9fed0a292', 'flutter/lib/desktop/pages/view_camera_page.dart': 'aa8c1b3fda2e12cb292dd73cdcd229c7b8c955f0a39fbfe74e03388db0507939', 'flutter/lib/desktop/widgets/material_mod_popup_menu.dart': 'b6f9d99567272fcb5a7642d8930e522be3ae6876322228ab3e46370ab710a354', 'flutter/lib/desktop/widgets/tabbar_widget.dart': 'd05d6fd84b6c2f51775dfb7e2eb928367a81addbba0dbef56adc2494935685f6', 'flutter/lib/web/bridge.dart': 'd8a81b4d7fee5186430d7624390db2b61a3978481b757e5d9fb791d74c26fbc6', 'flutter/pubspec.yaml': 'e31830d4fe6473201a4b39d45c6f2370b31d665ecd9164f9a2903ed5206dc917'}
HISTORICAL_CORRECTED = {'flutter/lib/desktop/pages/desktop_setting_page.dart': '1e959f2600b854f9da9381909c01c188ed6b8ae03f9d1d26a4df26ea27e1cd9e', 'flutter/lib/desktop/pages/view_camera_page.dart': '52df8c3040764c2165406e8bff7b9a294154c02d5abb6aebad9c37763c773d4f', 'flutter/lib/desktop/widgets/material_mod_popup_menu.dart': '8628314244d77ab1eab59f2e7dad560936bd8e54174fcf347dffc3b22a94aca1', 'flutter/lib/desktop/widgets/tabbar_widget.dart': '41719ae626738fa169509768aca435f3ee51f05497f8ab185dd2d439bd27aa43', 'flutter/lib/web/bridge.dart': 'd4a2352d72b93ff32ebfd21c33a30f676a1c03f4b7a1382fc34d514a96432249', 'flutter/pubspec.yaml': '3c07d93a8a7368393d020be9965605f2dd79c17979334bff3816657b82e1f038'}
CORRECTED = {'flutter/lib/desktop/pages/desktop_setting_page.dart': '1e959f2600b854f9da9381909c01c188ed6b8ae03f9d1d26a4df26ea27e1cd9e', 'flutter/lib/desktop/pages/view_camera_page.dart': '52df8c3040764c2165406e8bff7b9a294154c02d5abb6aebad9c37763c773d4f', 'flutter/lib/desktop/widgets/material_mod_popup_menu.dart': '8628314244d77ab1eab59f2e7dad560936bd8e54174fcf347dffc3b22a94aca1', 'flutter/lib/desktop/widgets/tabbar_widget.dart': '41719ae626738fa169509768aca435f3ee51f05497f8ab185dd2d439bd27aa43', 'flutter/lib/web/bridge.dart': 'd4a2352d72b93ff32ebfd21c33a30f676a1c03f4b7a1382fc34d514a96432249', 'flutter/pubspec.yaml': 'cfa3c757fdd04e8a293b0f5bfcea286ec31e904786a4467d66867dbdd2ef5358'}
FRESH = {'flutter/lib/desktop/pages/desktop_setting_page.dart': '175e5180774b6ebf115f1ed6f6af2a70463599496bc0e8ef3aec0068a9a1e291', 'flutter/lib/desktop/pages/view_camera_page.dart': 'e858a52ad5ab545f69526d9b6175d63c6eea7330ba3b7e46c8460ebf4fa4eaec', 'flutter/lib/desktop/widgets/material_mod_popup_menu.dart': 'b6f9d99567272fcb5a7642d8930e522be3ae6876322228ab3e46370ab710a354', 'flutter/lib/desktop/widgets/tabbar_widget.dart': 'd37441c9917f79895985cddc3dbd6862866712548aa37915a1459e7d23863cf5', 'flutter/lib/web/bridge.dart': '967688252dbbdce312c3c11e253da1d728d4d7a3acb6426d1a5fb896cd9bc050', 'flutter/pubspec.yaml': '5780140c6d70e49549d3cf57465fcce2f6a9ed2a9e3c904664fa728979ab69bc'}
REPLACEMENTS = {'flutter/lib/desktop/pages/desktop_setting_page.dart': [['  const _Printer({super.key});', '  const _Printer();'], ["_LabeledTextField(\n    BuildContext context,\n    String label,\n    TextEditingController controller,\n    String errorText,\n    bool enabled,\n    bool secure) {\n  return Table(\n    columnWidths: const {\n      0: FixedColumnWidth(150),\n      1: FlexColumnWidth(),\n    },\n    defaultVerticalAlignment: TableCellVerticalAlignment.middle,\n    children: [\n      TableRow(\n        children: [\n          Padding(\n            padding: const EdgeInsets.only(right: 10),\n            child: Text(\n              '${translate(label)}:',\n              textAlign: TextAlign.right,\n              style: TextStyle(\n                fontSize: 16,\n                color: disabledTextColor(context, enabled),\n              ),\n            ),\n          ),\n          TextField(\n            controller: controller,\n            enabled: enabled,\n            obscureText: secure,\n            autocorrect: false,\n            decoration: InputDecoration(\n              errorText: errorText.isNotEmpty ? errorText : null,\n            ),\n            style: TextStyle(\n              color: disabledTextColor(context, enabled),\n            ),\n          ).workaroundFreezeLinuxMint(),\n        ],\n      ),\n    ],\n  ).marginOnly(bottom: 8);\n}\n", '']], 'flutter/lib/desktop/pages/view_camera_page.dart': [['  MouseCursor _buildCustomCursor(BuildContext context, double scale) {\n    final cursor = Provider.of<CursorModel>(context);\n    final cache = cursor.cache ?? preDefaultCursor.cache;\n    return buildCursorOfCache(cursor, scale, cache);\n  }\n', ''], ['  MouseCursor _buildDisabledCursor(BuildContext context, double scale) {\n    final cursor = Provider.of<CursorModel>(context);\n    final cache = preForbiddenCursor.cache;\n    return buildCursorOfCache(cursor, scale, cache);\n  }\n', ''], ["import 'package:flutter_hbb/native/custom_cursor.dart'\n    if (dart.library.html) 'package:flutter_hbb/web/custom_cursor.dart';\n", '']], 'flutter/lib/desktop/widgets/material_mod_popup_menu.dart': [['const double _kDefaultIconSize = 24.0;\n', '']], 'flutter/lib/desktop/widgets/tabbar_widget.dart': [["  _DesktopTabState() : super();\n\n  static RxString tablabelGetter(String peerId) {\n    final alias = bind.mainGetPeerOptionSync(id: peerId, key: 'alias');\n    return RxString(getDesktopTabLabel(peerId, alias));\n  }\n", '  _DesktopTabState() : super();\n\n']], 'flutter/lib/web/bridge.dart': [["final _privateConstructorUsedError = UnsupportedError(\n    'It seems like you constructed your class using `MyClass._()`. This constructor is only meant to be used by freezed and you are not supposed to need it nor use it.\\nPlease check the documentation here for more information: https://github.com/rrousselGit/freezed#adding-getters-and-methods-to-our-models');\n\nmixin _$EventToUI {\n  Object get field0 => throw _privateConstructorUsedError;\n}\n\n", '']], 'flutter/pubspec.yaml': [['  file_picker: ^5.1.0\n', '  file_picker: 8.1.2\n']]}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_parents(repo, name):
    parent = repo
    for part in Path(name).parts[:-1]:
        parent /= part
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise RuntimeError('Unsafe SDK compatibility parent: ' + name)


def regular(repo, name):
    safe_parents(repo, name)
    path = repo / name
    attrs = path.lstat()
    if not stat.S_ISREG(attrs.st_mode) or attrs.st_nlink != 1 or attrs.st_size > 500000:
        raise RuntimeError('Nonregular SDK compatibility source: ' + name)
    return path.read_bytes()


def normalize(data):
    normalized = data.replace(b'\r\n', b'\n')
    newline = b'\r\n' if b'\r\n' in data else b'\n'
    if b'\r' in normalized or data != normalized.replace(b'\n', newline):
        raise RuntimeError('Mixed SDK compatibility line endings')
    return normalized, newline


def transform(name, data):
    if name not in FILES or digest(data) not in (FILES[name], HISTORICAL_FILES[name]):
        raise RuntimeError('Changed SDK compatibility original: ' + name)
    expected_corrected = (CORRECTED[name] if digest(data) == FILES[name]
                          else HISTORICAL_CORRECTED[name])
    for before, after in REPLACEMENTS[name]:
        before, after = before.encode(), after.encode()
        if data.count(before) != 1:
            raise RuntimeError('Ambiguous SDK compatibility replacement: ' + name)
        data = data.replace(before, after)
    if digest(data) != expected_corrected:
        raise RuntimeError('Changed SDK compatibility transformation: ' + name)
    return data


def inventory(repo, relative):
    safe_parents(repo, relative + '/proof.json')
    owned = repo / relative
    if owned.is_symlink() or not owned.is_dir():
        raise RuntimeError('Missing SDK compatibility ownership: ' + relative)
    files, directories = {}, set()
    for path in owned.rglob('*'):
        name = path.relative_to(owned).as_posix()
        if path.is_symlink():
            raise RuntimeError('Symlink in SDK compatibility ownership')
        attrs = path.lstat()
        if stat.S_ISDIR(attrs.st_mode):
            directories.add(name)
        elif stat.S_ISREG(attrs.st_mode):
            files[name] = regular(owned, name)
        else:
            raise RuntimeError('Nonregular SDK compatibility ownership')
    return files, directories


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate SDK compatibility provenance key')
        result[key] = value
    return result


def load_prior(name, sha):
    path = Path(__file__).resolve().parent / name
    data, _ = normalize(regular(path.parent, path.name))
    if digest(data) != sha:
        raise RuntimeError('Changed prior SDK compatibility verifier: ' + name)
    namespace = {'__name__': 'mixel_sdk_prior_verifier', '__file__': str(path)}
    exec(compile(data, str(path), 'exec'), namespace)
    return namespace


def validate_previous_layers(repo, originals):
    """Read the complete prior layers before any actual overlay restore writes."""
    termination = load_prior('patch-support-macos-termination.py', PRIOR_SHA)
    cleanup = load_prior('patch-support-macos-cleanup.py', CLEANUP_SHA)
    names = {'Cargo.toml', *termination['FILES'], *cleanup['FILES'],
             termination['GENERATED'], cleanup['GENERATED']}
    # Even non-overlapping pages are mandatory: deleting both ownership layers
    # cannot turn a real composed checkout into a minimal fixture.
    captured = {}
    for name in names:
        data = regular(repo, name)
        if name in originals:
            _, newline = normalize(data)
            data = originals[name].replace(b'\n', newline)
        captured[name] = data
    owned_layers = {}
    for relative in ('flutter/.mixel-dependencies/macos_application_exit', cleanup['OWNED']):
        owned_layers[relative] = inventory(repo, relative)
    with tempfile.TemporaryDirectory(prefix='mixel-sdk-previous-layers-') as directory:
        stage = Path(directory)
        for name, data in captured.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for relative, (files, directories) in owned_layers.items():
            for name in sorted(directories):
                (stage / relative / name).mkdir(parents=True, exist_ok=True)
            for name, data in files.items():
                path = stage / relative / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        with contextlib.redirect_stdout(io.StringIO()):
            termination['apply'](stage, check=True)


def apply(repo, restore=False, check=False):
    repo = Path(repo)
    cargo, _ = normalize(regular(repo, 'Cargo.toml'))
    packages = re.findall(rb'(?ms)^\[package\]\s*\n(.*?)(?=^\[|\Z)', cargo)
    versions = re.findall(rb'(?m)^version\s*=\s*"([^\"]+)"\s*$', packages[0]) if len(packages) == 1 else []
    if versions != [b'1.4.6']:
        raise RuntimeError('SDK compatibility requires upstream 1.4.6')
    safe_parents(repo, OWNED + '/proof.json')
    owned = repo / OWNED
    current, newlines = {}, {}
    for name in FILES:
        current[name], newlines[name] = normalize(regular(repo, name))
    identities = {name: digest(data) for name, data in current.items()}
    if restore and not owned.exists() and not owned.is_symlink():
        if identities == FRESH:
            return
        if identities not in (FILES, HISTORICAL_FILES):
            raise RuntimeError('Missing or mixed SDK compatibility restore ownership')
        validate_previous_layers(repo, current)
        return
    originals = {}
    if owned.exists() or owned.is_symlink():
        members, directories = inventory(repo, OWNED)
        expected_dirs = {parent.as_posix() for name in FILES for parent in Path(name).parents if str(parent) != '.'}
        if set(members) != {*FILES, 'proof.json'} or directories != expected_dirs:
            raise RuntimeError('Changed SDK compatibility ownership inventory')
        try:
            proof = json.loads(members['proof.json'], object_pairs_hook=strict_object)
        except (ValueError, UnicodeError) as error:
            raise RuntimeError('Invalid SDK compatibility provenance') from error
        expected = {'schema': 1, 'pin': PIN, 'sdk': SDK, 'engine': ENGINE,
                    'line_endings': {name: 'crlf' if newline == b'\r\n' else 'lf' for name, newline in newlines.items()}}
        if proof != expected or type(proof.get('schema')) is not int:
            raise RuntimeError('Changed SDK compatibility provenance')
        originals = {name: members[name] for name in FILES}
    else:
        if restore:
            raise RuntimeError('Missing SDK compatibility ownership')
        originals = dict(current)
    states = set()
    original_identities = {}
    outputs = {}
    for name, original in originals.items():
        if digest(original) not in (FILES[name], HISTORICAL_FILES[name]):
            raise RuntimeError('Changed SDK compatibility original: ' + name)
        original_identities[name] = digest(original)
        corrected = transform(name, original)
        if current[name] not in (original, corrected):
            raise RuntimeError('Changed SDK compatibility current source: ' + name)
        states.add('original' if current[name] == original else 'corrected')
        outputs[name] = (original if restore else corrected).replace(b'\n', newlines[name])
    if original_identities not in (FILES, HISTORICAL_FILES):
        raise RuntimeError('Mixed SDK compatibility input profiles')
    if len(states) != 1:
        raise RuntimeError('Mixed SDK compatibility pipeline targets')
    validate_previous_layers(repo, originals)
    if check:
        return
    if not owned.exists():
        for name, original in originals.items():
            path = owned / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(original)
        proof = {'schema': 1, 'pin': PIN, 'sdk': SDK, 'engine': ENGINE,
                 'line_endings': {name: 'crlf' if newline == b'\r\n' else 'lf' for name, newline in newlines.items()}}
        (owned / 'proof.json').write_text(json.dumps(proof, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    for name, output in outputs.items():
        if regular(repo, name) != output:
            (repo / name).write_bytes(output)
    print('   restored exact SDK compatibility pipeline inputs' if restore else
          '   applied exact official Flutter 3.27.4 compatibility overlay')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('checkout', type=Path)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    apply(args.checkout.resolve(), args.restore, args.check)
