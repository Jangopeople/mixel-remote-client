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
    parser.add_argument('--tokio', action='store_true', help='also compile/run real Tokio observer and its original starvation control')
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
    source = templates['src/server/mixel_receiver_uinput.rs'].decode()
    start = source.index('async fn send_observed(')
    end = source.index('\nfn keyboard_state(', start)
    observer = source[start:end]+'\n'
    (output/'send-observed.rs').write_text(observer)
    current_order = '        _=&mut deadline=>return false,\n        result=&mut send=>return result.is_ok(),\n        _=timer.tick()=>{state.sweep(kind,&mut emit,&mut flush);timer.reset_after(std::time::Duration::from_millis(10));}'
    original_order = '        _=timer.tick()=>{state.sweep(kind,&mut emit,&mut flush);}\n        _=&mut deadline=>return false,\n        result=&mut send=>return result.is_ok(),'
    if observer.count(current_order) != 1:
        raise ValueError('production observer priority changed')
    (output/'send-observed-original.rs').write_text(observer.replace(current_order, original_order, 1))

    for name in ('tokio-main.rs', 'Cargo.toml'):
        (output/name).write_bytes((HERE/name).read_bytes())

    extension = '.exe' if platform.system() == 'Windows' else ''
    commands = [
        [args.rustc, '--edition=2021', '-D', 'warnings', '--test', '--crate-name', 'mixel_uinput_core', 'core-main.rs', '-o', 'core-tests'+extension],
        [str(output/('core-tests'+extension)), '--nocapture'],
        [args.rustc, '--edition=2021', '-D', 'warnings', '--crate-name', 'mixel_uinput_native', 'native-main.rs', '-o', 'native-tests'+extension],
        [str(output/('native-tests'+extension))],
    ]
    if args.tokio:
        commands.append(['cargo', 'run', '--manifest-path', str(output/'Cargo.toml'), '--bin', 'timer-component'])
        commands.append(['cargo', 'run', '--manifest-path', str(output/'Cargo.toml'), '--bin', 'timer-component', '--features', 'original-timer'])
    result = {'schema': 'mixel-uinput-native-component-v1', 'scope': 'real Linux kernel capabilities and actual production receipt core; counted evdev callbacks',
              'planSha256': owner.PLAN_SHA, 'prepared': True, 'run': args.run,
              'platform': platform.system(), 'commands': commands, 'commandResults': [],
              'inputs': {name: sha((output/name).read_bytes()) for name in ('mixel_receiver_uinput_kernel.rs', 'mixel_receiver_uinput_core.rs', 'native-main.rs', 'core-main.rs', 'send-observed.rs', 'send-observed-original.rs', 'tokio-main.rs', 'Cargo.toml')},
              'nativeComponentPassed': False, 'tokioObserverRequested': args.tokio, 'tokioOriginalStarvationReproduced': False, 'applicationBuildPassed': False, 'desktopInputPassed': False}
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
                if '--features' in command:
                    if completed.returncode != 101 or b'cleanup catch-up starved transport/deadline' not in completed.stderr:
                        raise ValueError('original timer starvation did not reproduce')
                    result['tokioOriginalStarvationReproduced'] = True
                elif completed.returncode:
                    raise ValueError('native component command failed')
            result['nativeComponentPassed'] = True
    finally:
        (output/'RESULT.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'prepared': True, 'nativeComponentPassed': result['nativeComponentPassed'], 'output': str(output)}))


if __name__ == '__main__':
    main()
