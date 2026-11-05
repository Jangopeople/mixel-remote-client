#!/usr/bin/env python3
"""Run the pinned native production-state controls without native UI effects."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time

NAMESPACE = 'mixel-native-relative-mouse-lease-swift-gate-v1'
SOURCE_RELATIVE = Path('flutter/macos/Runner/MainFlutterWindow.swift')
SOURCE_BYTES = 21261
SOURCE_SHA = '8e22dbfb4cdf7574eefc433c49a6ea819fcfa9d9168e1217bbee81a795ae9d03'
CORE_SHA = '1244dd5d0d0f071aa71dce7a25fac8b443d66aab907b03d7c0709c492bf4e8f8'
TEST_SHA = '88c6243d99f0af8de001a669ff476ee1bab63aa19d22019455dfd0b4a7789c9b'
BEGIN = b'// MIXEL_RELATIVE_MOUSE_LEASE_CORE_BEGIN\n'
END = b'// MIXEL_RELATIVE_MOUSE_LEASE_CORE_END\n'
SUMMARY = b'Ran 30 native production-state tests; 0 failures; all effects mocked\n'
MAX_OUTPUT = 2 * 1024 * 1024


class Refusal(RuntimeError):
    pass


def require(value, category):
    if not value:
        raise Refusal(category)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def read_regular(path, maximum, *, exact_size=None):
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
            and 0 <= before.st_size <= maximum
            and (exact_size is None or before.st_size == exact_size), 'regular-bounded-input-required')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        require(identity(os.fstat(stream.fileno())) == identity(before), 'opened-input-changed')
        raw = stream.read(maximum + 1)
        require(identity(os.fstat(stream.fileno())) == identity(before), 'read-input-changed')
    require(identity(path.lstat()) == identity(before) and len(raw) == before.st_size,
            'named-input-changed')
    return raw


def write_bytes(path, raw):
    with path.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)


def write_json(path, value):
    write_bytes(path, (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode())


def validate(source_argument, output_argument):
    # All admission runs before output creation OR compiler discovery/execution.
    source = source_argument.resolve(strict=True)
    require(source.is_dir(), 'full-application-source-directory-required')
    product = source / SOURCE_RELATIVE
    require(product.resolve(strict=True) == product, 'source-link-refused')
    original = read_regular(product, 128 * 1024, exact_size=SOURCE_BYTES)
    require(sha(original) == SOURCE_SHA, 'pinned-production-source-required')
    require(original.count(BEGIN) == original.count(END) == 1, 'unique-production-core-markers-required')
    start = original.index(BEGIN)
    finish = original.index(END) + len(END)
    require(start < finish, 'production-core-marker-order')
    core = original[start:finish]
    require(sha(core) == CORE_SHA, 'exact-production-core-required')
    asset = Path(__file__).resolve().with_name('native_lease_tests.swift')
    tests = read_regular(asset, 128 * 1024)
    require(sha(tests) == TEST_SHA, 'unchanged-native-tests-required')
    output = output_argument.absolute()
    require('..' not in output.parts and output.parent.resolve(strict=True) == output.parent
            and not output.is_relative_to(source) and not os.path.lexists(output),
            'fresh-output-outside-source-required')
    return source, product, original, core, tests, output


def command(output, label, arguments, *, seconds, cleanup_seconds, records):
    """Bounded subprocess group owned by this invocation; no pipes to drain."""
    started = time.monotonic()
    record = {'label': label, 'argv': arguments, 'startedUtc': datetime.now(timezone.utc).isoformat(),
              'wallBudgetSeconds': seconds, 'timeoutSeconds': seconds - cleanup_seconds,
              'returnCode': None, 'timedOut': False}
    stdout_path, stderr_path = output / (label + '.stdout'), output / (label + '.stderr')
    error = None
    with stdout_path.open('xb') as stdout, stderr_path.open('xb') as stderr:
        os.fchmod(stdout.fileno(), 0o600)
        os.fchmod(stderr.fileno(), 0o600)
        try:
            process = subprocess.Popen(arguments, cwd=output, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                record['returnCode'] = process.wait(timeout=seconds - cleanup_seconds)
            except subprocess.TimeoutExpired:
                record['timedOut'] = True
                # Only our newly-created compiler/test process group is targeted.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                remaining = max(0.001, seconds - (time.monotonic() - started))
                try:
                    record['returnCode'] = process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    record['terminationUnconfirmed'] = True
                raise Refusal(label + '-timeout')
        except Exception as caught:
            error = caught
            record['exceptionType'] = type(caught).__name__
    record['elapsedSeconds'] = round(time.monotonic() - started, 6)
    for name, path in [('stdout', stdout_path), ('stderr', stderr_path)]:
        raw = read_regular(path, MAX_OUTPUT)
        record[name] = {'file': path.name, 'bytes': len(raw), 'sha256': sha(raw)}
    write_json(output / (label + '.command.json'), record)
    records.append(record)
    if error is not None:
        raise error
    require(record['returnCode'] == 0, label + '-failed')
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path, help='Just-branded full application root')
    parser.add_argument('--output', required=True, type=Path, help='New output directory outside source')
    args = parser.parse_args(argv)
    source, product, original, core, tests, output = validate(args.source, args.output)
    compiler = shutil.which('swiftc')
    require(compiler is not None, 'swift-compiler-required')
    compiler = str(Path(compiler).resolve(strict=True))
    output.mkdir(mode=0o700)  # Exclusive; never replaces existing evidence.
    receipt = {'schema': 1, 'namespace': NAMESPACE, 'state': 'FAILED', 'sourceRoot': str(source),
        'sourceFile': str(product), 'sourceBeforeSha256': sha(original), 'extractedCoreSha256': sha(core),
        'testSha256': sha(tests), 'compiler': compiler, 'commands': [],
        'allEffectsMocked': True, 'nativeApplicationExecuted': False, 'actualAppKitOrCursorEffectsExecuted': False,
        'nativeRuntimeQualified': False, 'fullProductBuildQualified': False,
        'customerControlQualified': False, 'releaseQualified': False, 'publication': False}
    error = None
    try:
        write_bytes(output / 'MainFlutterWindow.scoped-source.swift', original)
        write_bytes(output / 'production-core.swift', core)
        write_bytes(output / 'native_lease_tests.swift', tests)
        combined = b'import Foundation\nimport CoreFoundation\n' + core + b'\n' + tests
        write_bytes(output / 'compiled-test-input.swift', combined)
        receipt['compiledInputSha256'] = sha(combined)
        command(output, 'compile',
            [compiler, '-module-cache-path', str(output / 'module-cache'),
             str(output / 'compiled-test-input.swift'), '-o', str(output / 'native-lease-tests')],
            seconds=45, cleanup_seconds=5, records=receipt['commands'])
        require(read_regular(output / 'compile.stderr', MAX_OUTPUT) == b'', 'unexpected-compiler-diagnostic')
        command(output, 'run', [str(output / 'native-lease-tests')],
            seconds=10, cleanup_seconds=2, records=receipt['commands'])
        stdout = read_regular(output / 'run.stdout', MAX_OUTPUT)
        require(read_regular(output / 'run.stderr', MAX_OUTPUT) == b'' and stdout.endswith(SUMMARY)
            and stdout.count(SUMMARY) == 1 and len([line for line in stdout.splitlines() if line.startswith(b'PASS: ')]) == 30
            and b'FAIL:' not in stdout and b'skip' not in stdout.lower(), 'exact-thirty-passing-controls-required')
        receipt.update(state='PASSED', testsRun=30, testsPassed=30, testsFailed=0, testsSkipped=0)
    except Exception as caught:
        error = caught
        receipt['failureType'] = type(caught).__name__
        receipt['failureReasonSha256'] = sha(str(caught).encode())
    finally:
        try:
            after = read_regular(product, 128 * 1024, exact_size=SOURCE_BYTES)
            receipt['sourceAfterSha256'] = sha(after)
            receipt['scopedSourceConserved'] = after == original
            require(after == original and sha(after) == SOURCE_SHA, 'production-source-changed')
        except Exception as caught:
            error = caught
            receipt.update(state='FAILED', scopedSourceConserved=False,
                conservationFailureType=type(caught).__name__, conservationFailureSha256=sha(str(caught).encode()))
        write_json(output / 'result.json', receipt)
    if error is not None:
        print('FAIL: native production-state gate; retained scoped evidence', file=sys.stderr)
        return 1
    print(SUMMARY.decode().strip())
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('REFUSED: ' + type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(1)
