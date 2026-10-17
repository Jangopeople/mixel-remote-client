#!/usr/bin/env python3
"""Compile exact receiver gate templates; native effects are controlled mocks.

Windows leases/pipe peers and macOS main dispatch are actual OS primitives. This
component does not qualify application integration or native input receipts.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).absolute().parent
FIXTURE = HERE / 'fixtures/receiver-input-native'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def prepare(output):
    output = Path(output).absolute()
    if output.exists():
        raise RuntimeError('Receiver evidence directory must be new')
    output.mkdir(parents=True)
    stage = output / 'component'
    shutil.copytree(FIXTURE, stage)
    (stage / 'tests/server').mkdir()
    manifest = {'scope': 'exact receiver gate; mocked native effects; actual native leases/dispatch', 'templates': {}}
    for template, tail, target in (
            ('support-receiver-input.rs', 'core-tests.rs', 'mixel_receiver_input.rs'),
            ('support-receiver-input-windows.rs', 'windows-tests.rs', 'mixel_receiver_input_windows.rs')):
        raw = (HERE / template).read_bytes().replace(b'\r\n', b'\n')
        test = (stage / 'tests' / tail).read_bytes().replace(b'\r\n', b'\n')
        generated = raw + b'\n// Exact component regression tail; product prefix above is unchanged.\n' + test
        (stage / 'tests' / target).write_bytes(generated)
        assert generated[:len(raw)] == raw
        manifest['templates'][template] = {'sha256': sha(raw), 'prefix_bytes': len(raw), 'compiled_sha256': sha(generated)}
    for name in ('Cargo.toml', 'Cargo.lock', 'build.rs', 'protos/message.proto', 'tests/receiver.rs'):
        manifest.setdefault('fixture', {})[name] = sha((stage / name).read_bytes())
    (output / 'source-binding.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return stage


def run(command, stage, output, name, expected_failure=False):
    environment = dict(os.environ, CARGO_TARGET_DIR=str(output / 'target'))
    with (output / (name + '.log')).open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=stage, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='strict')
        for line in process.stdout:
            sys.stdout.write(line); sys.stdout.flush(); log.write(line); log.flush()
        code = process.wait()
    (output / (name + '.command.json')).write_text(json.dumps({'command': command, 'exit_code': code, 'expected_failure': expected_failure}, indent=2) + '\n', encoding='utf-8')
    if expected_failure:
        text = (output / (name + '.log')).read_text(encoding='utf-8')
        if code == 0 or 'cannot be sent between threads safely' not in text or 'Rc<()>' not in text:
            raise RuntimeError('Acquired Windows mutex guard did not prove thread affinity')
        print('PASS: acquired Windows kernel mutex guard cannot migrate between threads', flush=True)
    elif code:
        raise RuntimeError('Receiver component command failed: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--cargo', default='cargo')
    args = parser.parse_args()
    output = args.output.absolute()
    stage = prepare(output)
    print('PASS: exact receiver templates bound to retained component sources', flush=True)
    if args.prepare_only:
        return
    run([args.cargo, 'test', '--locked', '--test', 'receiver', '--', '--nocapture'], stage, output, 'receiver')
    if os.name == 'nt':
        run([args.cargo, 'check', '--locked', '--test', 'receiver', '--features', 'guard-must-not-send'], stage, output, 'mutex-thread-affinity', expected_failure=True)
    print('PASS: receiver component native gates; application builds and live acceptance remain required', flush=True)


if __name__ == '__main__':
    main()
