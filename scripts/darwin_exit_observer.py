"""Read-only, diagnostic-only Darwin exit observation; never a qualification result.

Caller supplies its existing exact-owned PID/birth/bundle identity reader. Attach to a
fresh CI-owned process before the action being diagnosed. This module starts no
process, sends no signal, and performs no fallback or retry. There is no CLI.
"""
from dataclasses import asdict, dataclass
import os
import secrets
import select

NOTE_EXIT = 0x80000000
NOTE_EXITSTATUS = 0x04000000
NOTE_EXIT_DETAIL = 0x02000000
NOTE_EXIT_DECRYPTFAIL = 0x00010000
NOTE_EXIT_MEMORY = 0x00020000
NOTE_EXIT_CSERROR = 0x00040000
REQUESTED_NOTES = NOTE_EXIT | NOTE_EXITSTATUS | NOTE_EXIT_DETAIL


@dataclass(frozen=True)
class Identity:
    pid: int
    uid: int
    birth_seconds: int
    birth_microseconds: int
    executable: str
    bundle_path: str | None
    bundle_identifier: str | None
    bundle_version: str | None


class ObservationError(RuntimeError):
    """Observation failed; absence of valid evidence is not an application exit."""


def validate_identity(value, *, disposable_child=False):
    if not isinstance(value, Identity):
        raise ObservationError('identity reader must return an immutable Identity')
    if type(value.pid) is not int or value.pid <= 1 or value.pid == os.getpid():
        raise ObservationError('refusing invalid or observer PID')
    if type(value.uid) is not int or value.uid != os.geteuid():
        raise ObservationError('target UID must equal observer effective UID')
    if (type(value.birth_seconds) is not int or value.birth_seconds <= 0
            or type(value.birth_microseconds) is not int
            or not 0 <= value.birth_microseconds < 1_000_000):
        raise ObservationError('precise process birth is required')
    if (not isinstance(value.executable, str)
            or not os.path.isabs(value.executable)
            or os.path.normpath(value.executable) != value.executable):
        raise ObservationError('canonical absolute executable path is required')
    bundle_values = (value.bundle_path, value.bundle_identifier, value.bundle_version)
    if disposable_child and bundle_values == (None, None, None):
        return
    if not all(isinstance(item, str) and item for item in bundle_values):
        raise ObservationError('exact native bundle identity is required')
    if (not os.path.isabs(value.bundle_path) or not value.bundle_path.endswith('.app')
            or os.path.normpath(value.bundle_path) != value.bundle_path
            or not value.executable.startswith(value.bundle_path + '/Contents/MacOS/')):
        raise ObservationError('executable does not belong to the expected app bundle')


class ExitObserver:
    def __init__(self, expected, identity_reader, *, disposable_child=False,
                 queue_factory=None):
        validate_identity(expected, disposable_child=disposable_child)
        self.expected = expected
        self._reader = identity_reader
        self._disposable_child = disposable_child
        self._queue = None
        self._cookie = secrets.randbits(31) or 1
        self._raw = []
        self._decoded = None
        self._failure = None
        self._closed = False
        # Complete identity checks occur on both sides of the registration.
        self._check_identity('before registration')
        try:
            self._queue = (queue_factory or select.kqueue)()
            event = select.kevent(
                expected.pid, filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_ONESHOT,
                fflags=REQUESTED_NOTES, udata=self._cookie)
            # Register only; no blocking wait and no silently consumed output event.
            self._queue.control([event], 0, 0)
            self._check_identity('after registration')
        except Exception as exc:
            self.close()
            raise ObservationError(f'registration failed: {type(exc).__name__}: {exc}') from exc

    def _check_identity(self, stage):
        actual = self._reader(self.expected.pid)
        validate_identity(actual, disposable_child=self._disposable_child)
        if actual != self.expected:
            raise ObservationError(f'PID/birth/executable/bundle identity mismatch {stage}')

    def poll(self):
        """Nonblocking; raw kernel fields retained even for invalid/error events."""
        if self._failure:
            raise ObservationError(self._failure)
        if self._closed:
            raise ObservationError('observer is closed')
        try:
            events = self._queue.control(None, 8, 0)
            for event in events:
                raw = {key: int(getattr(event, key))
                       for key in ('ident', 'filter', 'flags', 'fflags', 'data', 'udata')}
                self._raw.append(raw)
                if (raw['ident'] != self.expected.pid
                        or raw['filter'] != select.KQ_FILTER_PROC
                        or raw['udata'] != self._cookie):
                    raise ObservationError('event identity/filter/cookie mismatch')
                if raw['flags'] & select.KQ_EV_ERROR:
                    raise ObservationError(f'kernel event error errno={raw["data"]}')
                if not raw['fflags'] & NOTE_EXIT:
                    raise ObservationError('event lacks NOTE_EXIT')
                if self._decoded is not None:
                    raise ObservationError('duplicate exit event')
                decoded = {'exit_status_available': bool(raw['fflags'] & NOTE_EXITSTATUS),
                           'exit_detail_available': bool(raw['fflags'] & NOTE_EXIT_DETAIL)}
                if decoded['exit_status_available']:
                    status = raw['data']
                    if status < 0 or status > 0xFFFF:
                        raise ObservationError('invalid raw wait status')
                    if os.WIFEXITED(status):
                        decoded.update(kind='exited', exit_code=os.WEXITSTATUS(status))
                    elif os.WIFSIGNALED(status):
                        decoded.update(kind='signaled', signal=os.WTERMSIG(status))
                    else:
                        raise ObservationError('status is neither exited nor signaled')
                if decoded['exit_detail_available']:
                    decoded['exit_details'] = {
                        'decrypt_failure': bool(raw['fflags'] & NOTE_EXIT_DECRYPTFAIL),
                        'memory': bool(raw['fflags'] & NOTE_EXIT_MEMORY),
                        'code_signing_error': bool(raw['fflags'] & NOTE_EXIT_CSERROR)}
                self._decoded = decoded
        except Exception as exc:
            self._failure = f'{type(exc).__name__}: {exc}'
            raise ObservationError(self._failure) from exc
        return self.receipt()

    def receipt(self):
        return {'diagnostic_only': True, 'qualifies_application': False,
                'state': ('invalid' if self._failure else
                          'exit_observed' if self._decoded is not None else 'pending'),
                'identity': asdict(self.expected), 'requested_notes': REQUESTED_NOTES,
                'raw_events': list(self._raw), 'decoded_exit': self._decoded,
                'error': self._failure}

    def close(self):
        if self._queue is not None:
            self._queue.close()
            self._queue = None
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()
