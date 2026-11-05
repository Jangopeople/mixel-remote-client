#!/usr/bin/env python3
"""Guest-only exact fresh viewer pidfd capture; never a host process controller."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import time


def fact(pid):
    root = Path('/proc') / str(pid)
    raw = (root / 'stat').read_text()
    return dict(pid=pid, startTicks=int(raw[raw.rfind(')')+2:].split()[19]),
                exe=os.readlink(root / 'exe'), sha256=hashlib.sha256((root / 'exe').read_bytes()).hexdigest())


def monitor(pid, start, exe, digest, nonce, action, lifetime):
    if pid <= 0 or start <= 0 or action not in ('kill', 'release') or not 10 <= lifetime <= 180:
        raise ValueError('closed owned viewer monitor inputs')
    expected = dict(pid=pid, startTicks=start, exe=exe, sha256=digest)
    fd = os.pidfd_open(pid, 0)
    try:
        if fact(pid) != expected or select.select([fd], [], [], 0)[0]:
            raise ValueError('captured viewer identity changed')
        print(json.dumps(dict(event='captured', nonce=nonce, identity=expected)), flush=True)
        # A FIFO is not needed: the parent writes one exact fresh owned request
        # file, after capture. No PID lookup occurs when the signal is sent.
        request = Path('/proofs') / ('viewer-' + nonce + '.request')
        end = time.monotonic() + lifetime
        while time.monotonic() < end:
            if request.exists():
                raw = request.read_bytes()
                if raw != (action + ':' + nonce).encode():
                    raise ValueError('exact owned request required')
                if action == 'kill':
                    sent = dt.datetime.now(dt.timezone.utc).isoformat()
                    signal.pidfd_send_signal(fd, signal.SIGKILL, None, 0)
                    if not select.select([fd], [], [], 10)[0]:
                        raise TimeoutError('owned viewer did not exit')
                print(json.dumps(dict(event='completed', nonce=nonce, action=action,
                                      identity=expected, monotonic=time.monotonic(),
                                      sentAtUTC=sent if action == 'kill' else None,
                                      completedAtUTC=dt.datetime.now(dt.timezone.utc).isoformat())), flush=True)
                return
            if select.select([fd], [], [], .1)[0]:
                raise RuntimeError('viewer exited before exact owned request')
        raise TimeoutError('owned monitor deadline')
    finally:
        os.close(fd)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--pid', type=int, required=True)
    p.add_argument('--start', type=int, required=True)
    p.add_argument('--exe', required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--nonce', required=True)
    p.add_argument('--action', choices=('kill', 'release'), required=True)
    p.add_argument('--lifetime', type=int, default=180)
    a = p.parse_args()
    if len(a.nonce) != 32 or any(c not in '0123456789abcdef' for c in a.nonce):
        raise ValueError('fresh monitor nonce')
    monitor(a.pid, a.start, a.exe, a.sha256, a.nonce, a.action, a.lifetime)
