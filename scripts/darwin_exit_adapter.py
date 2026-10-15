"""Additive diagnostic hooks for the current macOS smoke's RuntimeEvidence.

No native launches, signals, helper calls, retries, deadline changes or success
conversion. Original mark/request each execute exactly once. Diagnostic errors
are retained separately; original application-test exceptions pass through.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import ctypes
import json
import math
import os
from pathlib import Path
import plistlib
import stat
import sys
import time
from types import FunctionType

from darwin_exit_observer import ExitObserver, Identity, ObservationError

# Only these source-defined labels may enter diagnostic sampling metadata.
_PHASES = frozenset({
    'initialization', 'ordinary-gui-launch', 'ordinary-unarmed-health',
    'fresh-warm-hide-setup', 'fresh-warm-url-launch', 'ordinary-to-warm-owned-cleanup',
    'cold-url-launch', 'cold-visible-window', 'cold-stable-window',
    'cold-incoming-health', 'owned-cleanup'})
_MARKS = frozenset({
    'test-start', 'owned-ordinary-application-launched', 'owned-application-launched',
    'owned-pid-exited', 'wait-observation-error', 'wait-deadline',
    'incoming-health-response', 'warm-cleanup-result', 'test-failure', 'cleanup-result',
    'launchservices-request', 'launchservices-response', 'launch-error',
    'ordinary-request', 'ordinary-response', 'ordinary-error',
    'hide-request', 'hide-response', 'hide-error',
    'probe-request', 'probe-response', 'probe-error'})


def _first_exit_metadata(evidence, stage, current_mark):
    now = time.monotonic()
    clock = getattr(evidence, 'clock', None)
    elapsed = (round(now - clock, 6) if type(clock) in (int, float)
               and math.isfinite(clock) and 0 <= clock <= now else None)
    phase = getattr(evidence, 'phase', None)
    return {'sampling_stage': stage, 'current_mark': current_mark,
            'phase': phase if isinstance(phase, str) and phase in _PHASES else 'unlisted-phase',
            'utc': datetime.now(timezone.utc).isoformat(),
            'elapsed_seconds': elapsed,
            'meaning': 'first observed exit; phase is not proof of the cause'}


class _BSDInfo(ctypes.Structure):
    # Darwin SDK sys/proc_info.h struct proc_bsdinfo (136 bytes on arm64/x86_64).
    _fields_ = ([('field_' + str(index), ctypes.c_uint32) for index in range(12)]
                + [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)]
                + [('tail_' + str(index), ctypes.c_uint32) for index in range(6)]
                + [('birth_seconds', ctypes.c_uint64),
                   ('birth_microseconds', ctypes.c_uint64)])


def libproc_identity(evidence, pid):
    """Only this exact owned PID is read; Cocoa launchDate is not a birth proxy."""
    library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                    ctypes.c_void_p, ctypes.c_int]
    library.proc_pidinfo.restype = ctypes.c_int
    library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    library.proc_pidpath.restype = ctypes.c_int
    before, after = _BSDInfo(), _BSDInfo()
    def read(info):
        count = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if count != ctypes.sizeof(info):
            raise ObservationError('exact owned PID libproc identity is unavailable')
        return (info.field_3, info.field_5, info.birth_seconds, info.birth_microseconds)
    birth = read(before)
    buffer = ctypes.create_string_buffer(4096)
    if library.proc_pidpath(pid, buffer, len(buffer)) <= 0:
        raise ObservationError('exact owned PID executable path is unavailable')
    executable = os.fsdecode(buffer.value)
    app = evidence.app
    if (not isinstance(app, Path) or app.resolve() != app
            or executable != str(evidence.executable_path)):
        raise ObservationError('exact PID does not run the owned diagnostic app executable')
    info_path = app / 'Contents/Info.plist'
    attributes = info_path.lstat()
    if not stat.S_ISREG(attributes.st_mode) or attributes.st_size > 1024 * 1024:
        raise ObservationError('owned app plist is not a bounded regular file')
    with info_path.open('rb') as stream:
        info = plistlib.load(stream)
    bundle, version = info.get('CFBundleIdentifier'), info.get('CFBundleVersion')
    if bundle != 'ch.mixel.remote' or not isinstance(version, str) or not version:
        raise ObservationError('owned app bundle identifier/build is invalid')
    if read(after) != birth:
        raise ObservationError('exact PID birth changed while reading its executable')
    return Identity(*birth, executable, str(app), bundle, version)


@contextmanager
def install(desktop, private_output, *, identity_reader=libproc_identity,
            observer_factory=ExitObserver):
    """Wrap ONLY mark/request and restore the identical originals on all exits.

    private_output must be a new JSON file within a canonical, owned, existing
    directory. The resulting receipt is diagnostic-only, including any errors.
    The optional callbacks exist solely for isolated adapter controls.
    """
    output = Path(private_output)
    parent = output.parent
    attributes = parent.lstat()
    if (not output.is_absolute() or parent.resolve() != parent
            or not stat.S_ISDIR(attributes.st_mode) or attributes.st_uid != os.geteuid()):
        raise ObservationError('diagnostic output must have a canonical owned parent')
    evidence_class = desktop.RuntimeEvidence
    original_mark, original_request = evidence_class.mark, evidence_class.request
    if not isinstance(original_mark, FunctionType) or not isinstance(original_request, FunctionType):
        raise ObservationError('RuntimeEvidence methods must be direct source functions')
    # Reserve an exclusive private file before any source hooks are changed.
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    sessions, errors = [], []
    def diagnostic_error(stage, exc):
        if len(errors) < 64:
            # Do not copy exception strings, URIs, helper arguments or tokens.
            errors.append({'stage': stage, 'error_type': type(exc).__name__})
    def sample(stage, current_mark=None):
        for session in sessions:
            if session['observer'] is not None and session['active']:
                try:
                    result = session['observer'].poll()
                    state = result.get('state')
                    if (session['last_state'] == 'pending' and state == 'exit_observed'
                            and session['first_exit_observation'] is None):
                        session['first_exit_observation'] = _first_exit_metadata(
                            session['evidence'], stage, current_mark)
                    session['last_state'] = state
                except Exception as exc:
                    session['active'] = False
                    diagnostic_error(stage, exc)
    def mark(self, event, **fields):
        result = original_mark(self, event, **fields)
        if event == 'owned-ordinary-application-launched':
            if any(item['evidence'] is self for item in sessions):
                diagnostic_error('duplicate-ordinary-mark', ObservationError())
            else:
                session = {'evidence': self, 'observer': None, 'active': False,
                           'last_state': 'pending', 'first_exit_observation': None}
                sessions.append(session)
                try:
                    expected = identity_reader(self, self.pid)
                    session['observer'] = observer_factory(
                        expected, lambda pid: identity_reader(self, pid))
                    session['active'] = True
                except Exception as exc:
                    diagnostic_error('register-owned-ordinary-PID', exc)
        sample('mark', event if isinstance(event, str) and event in _MARKS else 'unlisted-mark')
        return result
    def request(self, helper, action, *arguments):
        sample('before-request')
        try:
            return original_request(self, helper, action, *arguments)
        finally:
            sample('after-request')
    try:
        evidence_class.mark, evidence_class.request = mark, request
        yield
    finally:
        evidence_class.mark, evidence_class.request = original_mark, original_request
        sample('context-exit')
        receipts = []
        for session in sessions:
            observer = session['observer']
            if observer is None:
                continue
            try:
                collected = observer.receipt()
                collected['first_exit_observation'] = session['first_exit_observation']
                receipts.append(collected)
            except Exception as exc:
                diagnostic_error('observer-receipt', exc)
            try:
                observer.close()
            except Exception as exc:
                diagnostic_error('observer-close', exc)
        receipt = {'diagnostic_only': True, 'qualifies_application': False,
                   'observers': receipts, 'errors': errors,
                   'original_methods_restored': (evidence_class.mark is original_mark
                                                and evidence_class.request is original_request)}
        # Writing only the already-reserved owned descriptor cannot replace other files.
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(receipt, stream, indent=2)
                stream.write('\n')
        except Exception as exc:
            # A missing/invalid diagnostic receipt must fail the external facade's
            # diagnostic gate; it must never replace the smoke's original exception.
            print('EXIT_OBSERVER_DIAGNOSTIC_WRITE_FAILED ' + type(exc).__name__, file=sys.stderr)
