import ctypes
from dataclasses import replace
import errno
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
import unittest

import darwin_exit_observer as subject

IDENTITY = subject.Identity(123456, os.geteuid(), 1700000000, 123456,
                            '/private/Owned.app/Contents/MacOS/Owned',
                            '/private/Owned.app', 'ch.example.owned', '77')


class FakeQueue:
    def __init__(self, batches=(), register_error=None, poll_error=None):
        self.batches = list(batches)
        self.registration = None
        self.closed = False
        self.register_error = register_error
        self.poll_error = poll_error

    def control(self, changes, limit, timeout):
        assert timeout == 0
        if changes:
            if self.register_error:
                raise self.register_error
            self.registration = changes[0]
            assert limit == 0
            return []
        assert limit == 8
        if self.poll_error:
            raise self.poll_error
        batch = self.batches.pop(0) if self.batches else []
        return [event(self.registration) if callable(event) else event for event in batch]

    def close(self):
        self.closed = True


def event(reg, *, fflags=subject.NOTE_EXIT | subject.NOTE_EXITSTATUS,
          data=0, flags=0, ident=None, filt=None, cookie=None):
    return select.kevent(reg.ident if ident is None else ident,
                         filter=reg.filter if filt is None else filt, flags=flags,
                         fflags=fflags, data=data,
                         udata=reg.udata if cookie is None else cookie)


class Controls(unittest.TestCase):
    def observe(self, queue=None, identities=None):
        queue = queue or FakeQueue()
        reads = iter(identities or [IDENTITY, IDENTITY])
        return subject.ExitObserver(IDENTITY, lambda pid: next(reads),
                                    queue_factory=lambda: queue), queue

    def test_no_event_is_pending_not_exit(self):
        observer, queue = self.observe()
        with observer:
            self.assertEqual(observer.poll()['state'], 'pending')
            self.assertFalse(observer.receipt()['qualifies_application'])
        self.assertTrue(queue.closed)
        self.assertEqual(queue.registration.fflags, subject.REQUESTED_NOTES)

    def test_pid_birth_executable_and_bundle_mismatches_before_registration(self):
        variants = [replace(IDENTITY, pid=123457),
                    replace(IDENTITY, birth_microseconds=123457),
                    replace(IDENTITY, executable=IDENTITY.executable+'2'),
                    replace(IDENTITY, bundle_identifier='ch.example.other'),
                    replace(IDENTITY, bundle_version='78')]
        for variant in variants:
            with self.subTest(variant=variant):
                queue = FakeQueue()
                with self.assertRaises(subject.ObservationError):
                    self.observe(queue, [variant])
                self.assertIsNone(queue.registration)

    def test_mismatch_after_registration_closes_queue(self):
        queue = FakeQueue()
        with self.assertRaises(subject.ObservationError):
            self.observe(queue, [IDENTITY, replace(IDENTITY, birth_seconds=1700000001)])
        self.assertTrue(queue.closed)

    def test_missing_reader_identity_is_error(self):
        with self.assertRaises(subject.ObservationError):
            self.observe(identities=[None])

    def test_invalid_identity_rejected(self):
        for value in [replace(IDENTITY, pid=os.getpid()), replace(IDENTITY, uid=-1),
                      replace(IDENTITY, birth_microseconds=-1),
                      replace(IDENTITY, bundle_path=None),
                      replace(IDENTITY, executable='/tmp/other')]:
            with self.subTest(value=value), self.assertRaises(subject.ObservationError):
                subject.ExitObserver(value, lambda pid: value,
                                     queue_factory=lambda: self.fail('queue must not open'))

    def test_register_error_is_not_exit(self):
        queue = FakeQueue(register_error=OSError(errno.ESRCH, 'fixture missing PID'))
        with self.assertRaises(subject.ObservationError):
            self.observe(queue)
        self.assertTrue(queue.closed)

    def test_poll_error_is_sticky_and_not_exit(self):
        observer, _ = self.observe(FakeQueue(poll_error=OSError(errno.EBADF, 'fixture')))
        with observer, self.assertRaises(subject.ObservationError):
            observer.poll()
        self.assertEqual(observer.receipt()['state'], 'invalid')
        self.assertIsNone(observer.receipt()['decoded_exit'])

    def test_wrong_pid_filter_cookie_and_kernel_error_retained(self):
        variants = [{'ident': 99999}, {'filt': select.KQ_FILTER_READ}, {'cookie': 99999},
                    {'flags': select.KQ_EV_ERROR, 'data': errno.EPERM},
                    {'fflags': subject.NOTE_EXITSTATUS}, {'data': -1}]
        for values in variants:
            with self.subTest(values=values):
                observer, _ = self.observe(FakeQueue([[lambda reg: event(reg, **values)]]))
                with observer, self.assertRaises(subject.ObservationError):
                    observer.poll()
                self.assertEqual(observer.receipt()['state'], 'invalid')
                self.assertEqual(len(observer.receipt()['raw_events']), 1)

    def test_duplicate_event_rejected(self):
        observer, _ = self.observe(FakeQueue([[event], [event]]))
        with observer:
            self.assertEqual(observer.poll()['decoded_exit']['exit_code'], 0)
            with self.assertRaises(subject.ObservationError):
                observer.poll()
        self.assertEqual(observer.receipt()['state'], 'invalid')
        self.assertEqual(len(observer.receipt()['raw_events']), 2)

    def test_no_status_flag_never_interprets_data(self):
        observer, _ = self.observe(FakeQueue([[lambda reg: event(
            reg, fflags=subject.NOTE_EXIT | subject.NOTE_EXIT_MEMORY, data=1792)]]))
        with observer:
            decoded = observer.poll()['decoded_exit']
        self.assertFalse(decoded['exit_status_available'])
        self.assertFalse(decoded['exit_detail_available'])
        self.assertNotIn('exit_code', decoded)
        self.assertNotIn('exit_details', decoded)

    def test_detail_bits_only_when_kernel_flag_present(self):
        observer, _ = self.observe(FakeQueue([[lambda reg: event(
            reg, fflags=subject.REQUESTED_NOTES | subject.NOTE_EXIT_MEMORY |
            subject.NOTE_EXIT_CSERROR, data=7 << 8)]]))
        with observer:
            decoded = observer.poll()['decoded_exit']
        self.assertEqual(decoded['exit_code'], 7)
        self.assertEqual(decoded['exit_details'],
                         {'decrypt_failure': False, 'memory': True, 'code_signing_error': True})


# libproc reads are confined to disposable children created by the tests below.
class BSDInfo(ctypes.Structure):
    _fields_ = ([(name, ctypes.c_uint32) for name in (
        'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid', 'rgid',
        'svuid', 'svgid', 'reserved')] + [('comm', ctypes.c_char * 16),
        ('name', ctypes.c_char * 32)] + [(name, ctypes.c_uint32) for name in (
        'nfiles', 'pgid', 'pjobc', 'tdev', 'tpgid')] + [('nice', ctypes.c_int32),
        ('start_seconds', ctypes.c_uint64), ('start_microseconds', ctypes.c_uint64)])


def child_identity(pid):
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidinfo.restype = ctypes.c_int
    lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    lib.proc_pidpath.restype = ctypes.c_int
    info = BSDInfo()
    if lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
        raise subject.ObservationError('owned child identity unavailable')
    path = ctypes.create_string_buffer(4096)
    if lib.proc_pidpath(pid, path, len(path)) <= 0:
        raise subject.ObservationError('owned child executable unavailable')
    return subject.Identity(info.pid, info.uid, info.start_seconds, info.start_microseconds,
                            os.fsdecode(path.value), None, None, None)


ACTUAL_RESULTS = []


class ActualChildren(unittest.TestCase):
    def run_child(self, action, expected_kind, expected_value):
        # No parent kill/terminate fallback. Child self-exits after a bounded stdin wait.
        code = ('import os,select,signal,sys; print("READY",flush=True); '
                'ready=select.select([sys.stdin],[],[],12)[0]; '
                'command=sys.stdin.readline().strip() if ready else ""; '
                'sys.exit(93) if command != "GO" else None; ' + action)
        process = subprocess.Popen([sys.executable, '-u', '-c', code], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'READY')
            expected = child_identity(process.pid)
            with subject.ExitObserver(expected, child_identity, disposable_child=True) as observer:
                self.assertEqual(observer.poll()['state'], 'pending')
                process.stdin.write('GO\n')
                process.stdin.flush()
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    receipt = observer.poll()
                    if receipt['state'] == 'exit_observed':
                        break
                    time.sleep(0.01)
                self.assertEqual(receipt['state'], 'exit_observed')
                # Read kernel result BEFORE Popen.wait/reap.
                process_status = process.wait(timeout=3)
                decoded = receipt['decoded_exit']
                self.assertTrue(decoded['exit_status_available'])
                self.assertEqual(decoded['kind'], expected_kind)
                value_key = 'exit_code' if expected_kind == 'exited' else 'signal'
                self.assertEqual(decoded[value_key], expected_value)
                self.assertEqual(process_status, expected_value if expected_kind == 'exited'
                                 else -expected_value)
                ACTUAL_RESULTS.append({'action': action, 'popen_returncode': process_status,
                                       'receipt': receipt})
                print('ACTUAL_CHILD ' + json.dumps(ACTUAL_RESULTS[-1], sort_keys=True), flush=True)
        finally:
            process.stdin.close()
            process.wait(timeout=15)
            process.stdout.close()
            process.stderr.close()

    def test_exit_zero(self):
        self.run_child('sys.exit(0)', 'exited', 0)

    def test_exit_seven(self):
        self.run_child('sys.exit(7)', 'exited', 7)

    def test_self_sigterm(self):
        self.run_child('os.kill(os.getpid(), signal.SIGTERM)', 'signaled', signal.SIGTERM)

    def test_self_sigkill(self):
        self.run_child('os.kill(os.getpid(), signal.SIGKILL)', 'signaled', signal.SIGKILL)


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        Path(__file__).with_name('actual-child-results.json').write_text(
            json.dumps({'diagnostic_only': True, 'results': ACTUAL_RESULTS}, indent=2)+'\n')
