#!/usr/bin/env python3
"""Prepare exact production Linux kernel/core fixtures; --run is Linux-only.

No /dev/uinput, privileges, application launch, provider API, or desktop input.
Kernel APIs are real; evdev callbacks are counted component sinks.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--rustc', default='rustc')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('uinput_fixture_owner', SCRIPTS/'patch-support-receiver-uinput.py')
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    _plan, templates = owner.load_plan()
    if args.output.exists():
        raise ValueError('native fixture output must be new')
    args.output.mkdir(parents=True)
    output = args.output.resolve(strict=True)
    for name in ('mixel_receiver_uinput_kernel.rs', 'mixel_receiver_uinput_core.rs'):
        raw = templates['src/server/'+name]
        (output/name).write_bytes(raw)
    (output/'native-main.rs').write_bytes((HERE/'native-main.rs').read_bytes())
    (output/'core-main.rs').write_text('#![allow(dead_code)]\n#[path="mixel_receiver_uinput_core.rs"] mod core;\n')
    extension = '.exe' if platform.system() == 'Windows' else ''
    commands = [
        [args.rustc, '--edition=2021', '-D', 'warnings', '--test', '--crate-name', 'mixel_uinput_core', 'core-main.rs', '-o', 'core-tests'+extension],
        [str(output/('core-tests'+extension)), '--nocapture'],
        [args.rustc, '--edition=2021', '-D', 'warnings', '--crate-name', 'mixel_uinput_native', 'native-main.rs', '-o', 'native-tests'+extension],
        [str(output/('native-tests'+extension))],
    ]
    result = {'schema': 'mixel-uinput-native-component-v1', 'scope': 'real Linux kernel capabilities and actual production receipt core; counted evdev callbacks',
              'planSha256': owner.PLAN_SHA, 'prepared': True, 'run': args.run,
              'platform': platform.system(), 'commands': commands, 'commandResults': [],
              'inputs': {name: sha((output/name).read_bytes()) for name in ('mixel_receiver_uinput_kernel.rs', 'mixel_receiver_uinput_core.rs', 'native-main.rs', 'core-main.rs')},
              'nativeComponentPassed': False, 'applicationBuildPassed': False, 'desktopInputPassed': False}
    try:
        if args.run:
            if platform.system() != 'Linux':
                raise ValueError('actual kernel fixture requires Linux')
            for index, command in enumerate(commands):
                completed = subprocess.run(command, cwd=output, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
                (output/f'command-{index}.stdout').write_bytes(completed.stdout)
                (output/f'command-{index}.stderr').write_bytes(completed.stderr)
                result['commandResults'].append({'command': command, 'returncode': completed.returncode,
                    'stdoutSha256': sha(completed.stdout), 'stderrSha256': sha(completed.stderr)})
                sys.stdout.buffer.write(completed.stdout)
                sys.stderr.buffer.write(completed.stderr)
                if completed.returncode:
                    raise ValueError('native component command failed')
            result['nativeComponentPassed'] = True
    finally:
        (output/'RESULT.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'prepared': True, 'nativeComponentPassed': result['nativeComponentPassed'], 'output': str(output)}))


if __name__ == '__main__':
    main()
