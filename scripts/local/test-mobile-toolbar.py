#!/usr/bin/env python3
"""Run actual projected mobile toolbar layout and action tests in Flutter."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['root', 'output', 'flutter-sdk']:
        parser.add_argument(f'--{name}', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = root / 'flutter/lib/mobile/widgets/mixel_session_toolbar.dart'
    original = source.read_bytes()
    project = output / 'qa-project'
    (project / 'lib').mkdir(parents=True)
    (project / 'test').mkdir()
    (project / 'pubspec.yaml').write_text('''name: mixel_toolbar_qa
environment:
  sdk: '>=3.1.0 <4.0.0'
dependencies:
  flutter:
    sdk: flutter
dev_dependencies:
  flutter_test:
    sdk: flutter
''')
    shutil.copyfile(source, project / 'lib/mixel_session_toolbar.dart')
    fixture = Path(__file__).resolve().parents[1] / 'fixtures/mobile/mixel_session_toolbar_test.dart'
    shutil.copyfile(fixture, project / 'test/mixel_session_toolbar_test.dart')
    env = os.environ.copy()
    env.update({'FLUTTER_SUPPRESS_ANALYTICS': 'true', 'CI': 'true'})
    flutter = args.flutter_sdk.resolve(strict=True) / 'bin/flutter'
    commands = []
    qualified = False
    try:
        for label, arguments in [('dependencies', ['pub', 'get', '--offline']),
                                 ('analysis', ['analyze', '--no-pub']),
                                 ('widget-tests', ['test', '--reporter', 'expanded'])]:
            command = [str(flutter), *arguments]
            with (output / f'{label}.log').open('w') as log:
                result = subprocess.run(command, cwd=project, env=env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=600)
            commands.append({'label': label, 'argv': command, 'exitCode': result.returncode})
            if result.returncode:
                raise RuntimeError(f'{label} failed; see retained log')
        if source.read_bytes() != original:
            raise RuntimeError('Projected production toolbar changed during tests')
        qualified = True
        print('PASS: actual production toolbar; analysis and 3 narrow/wide layout/action tests')
    finally:
        (output / 'result.json').write_text(json.dumps({
            'widgetQualified': qualified, 'sourceUnchanged': source.read_bytes() == original,
            'sourceSha256': hashlib.sha256(original).hexdigest(),
            'fixtureSha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
            'commands': commands, 'nativeSessionQualified': False}, indent=2) + '\n')


if __name__ == '__main__':
    main()
