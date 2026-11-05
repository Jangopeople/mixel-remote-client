#!/usr/bin/env python3
"""Prepare exact whole-leaf SAS components; --run explicitly requires Windows."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import time

HERE = Path(__file__).resolve().parent
LEAF = HERE.parents[1] / 'support-receiver-sas-windows.rs'
LEAF_SHA256 = '3cff92bc3dc0ae0041084a36dc970f95a35240e77bf1844a97bde82dd90804ff'
FILES = {
    'source-Cargo.toml': 'harness/Cargo.toml',
    'source-Cargo.lock': 'harness/Cargo.lock',
    'hbb-Cargo.toml': 'harness/hbb-common-stub/Cargo.toml',
    'hbb-lib.rs': 'harness/hbb-common-stub/src/lib.rs',
    'native-main.rs': 'harness/src/lib.rs',
    'kernel-actor.rs': 'harness/src/bin/kernel_actor.rs',
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Regular single-link fixture source required: ' + str(path))
    return path.read_bytes()


def logical(raw):
    text = raw.decode('utf-8', errors='strict')
    value = text.replace('\r\n', '\n')
    newline = '\r\n' if '\r\n' in text else '\n'
    if '\r' in value or value.replace('\n', newline) != text:
        raise ValueError('Uniform fixture source newlines required')
    return value.encode()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def actor_compile_command(output, linker):
    # The actor is std + kernel32 only. Cargo --bin would also compile the
    # intentionally private harness library without cfg(test), where its
    # uncalled product APIs become artificial dead_code errors under -Dwarnings.
    return ['rustc', '+1.75.0', '--edition=2021', '--target',
            'x86_64-pc-windows-msvc', '-Dwarnings',
            '-C', 'linker=' + linker,
            '-o', str(output / 'kernel_actor.exe'),
            str(output / 'harness/src/bin/kernel_actor.rs')]


def report_compiler_failure(label, code, stderr):
    print(f'FAIL: {label}; compiler exit={code}; complete output retained', flush=True)
    print(stderr[-12000:].decode('utf-8', errors='replace'), flush=True)


def prepare(output):
    source = read(LEAF)
    if sha(logical(source)) != LEAF_SHA256:
        raise ValueError('Entire production SAS leaf pin drift')
    parts = {name: read(HERE / name) for name in (*FILES, 'pure-tests.rs', 'kernel-tests.rs')}
    output = Path(output).absolute()
    if ('worktrees' in output.parts or output.parent.resolve(strict=True) != output.parent
            or output.exists() or output.is_symlink()):
        raise ValueError('Fresh ordinary canonical output required')
    output.mkdir(mode=0o700)
    bindings = [{'path': str(LEAF), 'sha256': sha(source), 'logicalSHA256': LEAF_SHA256,
                 'bytes': len(source)}]
    for name, raw in parts.items():
        bindings.append({'path': str(HERE / name), 'sha256': sha(raw), 'bytes': len(raw)})
    for name, destination in FILES.items():
        path = output / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(parts[name])
    # The first len(source) bytes are the entire exact production module; no
    # function, FFI declaration, import, cfg or body is replaced or extracted.
    entire = source + b'\n' + parts['pure-tests.rs'] + b'\n' + parts['kernel-tests.rs']
    (output / 'harness/src/sas_leaf_under_test.rs').write_bytes(entire)
    value = {
        'scope': 'Entire exact SAS leaf compile and real Windows kernel negatives; no app/service positive',
        'sourceInputs': bindings,
        'entireLeafPrefixBytes': len(source),
        'entireLeafPhysicalSHA256': sha(source),
        'entireLeafLogicalSHA256': LEAF_SHA256,
        'generatedLeafWithAppendedTestsSHA256': sha(entire),
        'rustVersionRequired': '1.75.0',
        'lockVersion': 3,
        'preparedComponentCases': 14,
        'parentIPCStub': 'Always rejects transport; never executed by the component cases',
        'realDependenciesAndFFI': True,
        'compiled': False,
        'nativeExecuted': False,
        'SendSASInvoked': False,
        'realServicePositive': False,
        'secureScreenDelivered': False,
        'applicationQualified': False,
    }
    write(output / 'PREPARATION.json', value)
    return output, value


def run_windows(output, binding):
    if platform.system() != 'Windows':
        raise ValueError('--run requires actual hosted Windows; local native execution is not supported')
    deadline = time.monotonic() + 240
    commands = []
    env = dict(os.environ)
    env['CARGO_TARGET_DIR'] = str(output / 'target')
    env['RUSTFLAGS'] = '-Dwarnings'
    manifest = output / 'harness/Cargo.toml'
    try:
        def command(label, argv, bound):
            directory = output / label
            directory.mkdir(exist_ok=False)
            write(directory / 'COMMAND.json', {'argv': argv, 'cwd': str(manifest.parent),
                  'timeoutSeconds': bound, 'RUSTFLAGS': env['RUSTFLAGS']})
            started = time.monotonic()
            remaining = deadline - started
            if remaining <= 0:
                raise RuntimeError('Overall component deadline')
            try:
                result = subprocess.run(argv, cwd=manifest.parent, env=env, capture_output=True,
                                        timeout=min(bound, remaining))
            except subprocess.TimeoutExpired as error:
                (directory / 'stdout').write_bytes(error.stdout or b'')
                (directory / 'stderr').write_bytes(error.stderr or b'')
                write(directory / 'RESULT.json', {'outcome': 'timeout', 'qualified': False,
                      'seconds': time.monotonic() - started})
                # No rerun or claim all descendant closure. The hosted CI job's
                # own finite timeout/runner teardown remains the outer boundary.
                raise
            (directory / 'stdout').write_bytes(result.stdout)
            (directory / 'stderr').write_bytes(result.stderr)
            value = {'exit': result.returncode, 'seconds': time.monotonic() - started}
            write(directory / 'RESULT.json', value)
            commands.append({'label': label, **value})
            if result.returncode:
                if label in ('02-compile-entire-leaf', '03-build-owned-kernel-actor'):
                    report_compiler_failure(label, result.returncode, result.stderr)
                raise RuntimeError(label + ' failed; retained without retry')
            return result.stdout.decode('utf-8', errors='strict')

        version = command('01-rust-version', ['rustc', '+1.75.0', '--version'], 10)
        if not re.fullmatch(r'rustc 1\.75\.0 \([^\r\n]+\)\r?\n', version):
            raise ValueError('Exact Rust1.75.0 required')
        common = ['--locked', '--manifest-path', str(manifest), '--target', 'x86_64-pc-windows-msvc']
        command('02-compile-entire-leaf', ['cargo', '+1.75.0', 'test', *common, '--lib', '--no-run'], 100)
        linker = env.get('CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER', '')
        if not Path(linker).is_absolute() or not Path(linker).is_file() or Path(linker).name.lower() != 'link.exe':
            raise ValueError('Exact activated x64 MSVC linker required for the independent actor')
        command('03-build-owned-kernel-actor', actor_compile_command(output, linker), 60)
        actor = output / 'kernel_actor.exe'
        if not actor.is_file():
            raise ValueError('Newly compiled own component actor absent')
        env['SAS_COMPONENT_ACTOR'] = str(actor)
        stdout = command('04-real-kernel-negatives', ['cargo', '+1.75.0', 'test', *common,
                         '--lib', '--', '--test-threads=1', '--nocapture'], 40)
        if 'test result: ok. 14 passed; 0 failed; 0 ignored;' not in stdout:
            raise ValueError('Expected fourteen actual component cases with no ignored cases')
        for row in binding['sourceInputs']:
            if sha(read(Path(row['path']))) != row['sha256']:
                raise ValueError('Fixture source changed during execution')
        write(output / 'RESULT.json', {
            'scope': binding['scope'], 'commands': commands, 'componentCases': 14,
            'entireLeafLogicalSHA256': LEAF_SHA256, 'actorSHA256': sha(actor.read_bytes()),
            'SendSASInvoked': False, 'realServicePositive': False, 'secureScreenDelivered': False,
            'applicationQualified': False, 'nativeQualified': False,
            'normalReleaseQualified': False, 'publication': False,
        })
    except BaseException as error:
        write(output / 'STOPPED.json', {'scope': binding['scope'], 'commands': commands,
              'errorType': type(error).__name__, 'error': str(error), 'qualified': False})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--run', action='store_true', help='Explicit hosted Windows compile + kernel negatives')
    args = parser.parse_args()
    if args.run and platform.system() != 'Windows':
        raise ValueError('--run requires actual hosted Windows')
    output, binding = prepare(args.output)
    if args.run:
        run_windows(output, binding)
    else:
        print('PREPARED: entire exact SAS leaf; fourteen cases; compiler/native execution NOT run')


if __name__ == '__main__':
    main()
