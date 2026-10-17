#!/usr/bin/env python3
"""Separate private six-owner Windows held-input acceptance controller.

Default validation and evidence joins are inert. --run is hosted-Linux only,
requires a hash-bound Root admission and a pre-staged qualified070 controller
DEB, and never fetches artifacts or authorizes the Windows peer. Independent
Root console observations, not controller pixels, decide host input release.
"""
import argparse
import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shlex
import signal
import stat
import subprocess
import sys
import time
import uuid

FROZEN_SOURCE = '03ec1e70f98c08ed8bb9f63dd28886b754a8f2d2'
FROZEN = {
    'scripts/pc01-linux-controller.py': '8df417fabfaf368216e2e74aceccedde5b68cb3f94ee664d42a2d728bd39aa50',
    '.github/workflows/pc01-linux-controller.yml': '2007f8deca8f2a60a0acde3a1f9fb8d816a85942891c6231c57f3a8202304980',
    'scripts/test-pc01-linux-controller.py': '33add732600dc7d292c39177933ab8d1ba612f74ab4b69144489508a4122d31b',
}
LINUX_SOURCE = '070da25001f97fabf67d08401e2cb15bde37a33b'
LINUX_TREE = '4411c8027836673ded7f1b0b22f7bdac47c32242'
LINUX_RUN = 39022433327
CASES = ('normal-disconnect-shift', 'normal-disconnect-leftButton',
         'viewer-loss-shift', 'viewer-loss-leftButton')
BITS = ('shift', 'leftShift', 'rightShift', 'leftButton', 'rightButton')
FLAGS = {k: False for k in ('releaseQualified', 'nativeQualified', 'taskComplete',
                          'publication', 'PC01Qualified', 'wholeProducerQualified')}
RECEIPTS = {'composition', 'windowsQualification', 'cold95', 'signature',
            'payloadManifest', 'linuxQualification', 'hostBaseline', 'sourceControls'}
FIXTURE = Path('scripts/fixtures/pc01-held-input')


def need(ok, why):
    if not ok:
        raise ValueError(why)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(v, size=64):
    return type(v) is str and re.fullmatch('[0-9a-f]{' + str(size) + '}', v) is not None


def positive(v):
    return type(v) is int and v > 0


def raw_safe(path):
    p = Path(path)
    need(p.is_absolute() and not any(x.casefold() == 'worktrees' or x in ('.', '..') for x in p.parts),
         'ordinary-absolute-name-first-path')
    return p


def ordinary(path, *, file=False, absent=False):
    """No resolve/read/mkdir before the raw-name rejection and ancestor lstat."""
    p = raw_safe(path)
    for q in [*reversed(p.parents), p]:
        if absent and q == p:
            need(not q.exists() and not q.is_symlink(), 'fresh-output-required')
            continue
        s = q.lstat()
        need(not stat.S_ISLNK(s.st_mode) and not (getattr(s, 'st_file_attributes', 0) & 1024),
             'no-link-or-reparse-ancestry')
        if q == p and file:
            need(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, 'ordinary-single-link-file')
        else:
            need(stat.S_ISDIR(s.st_mode), 'ordinary-directory-ancestry')
    return p


def read_pin(pin, *, limit=8*1024*1024):
    need(type(pin) is dict and set(pin) == {'path', 'bytes', 'sha256'}
         and positive(pin['bytes']) and pin['bytes'] <= limit and digest(pin['sha256']), 'closed-file-pin')
    path = ordinary(pin['path'], file=True)
    before = path.stat()
    need(before.st_size == pin['bytes'], 'bounded-actual-file-size-before-read')
    raw = path.read_bytes()
    after = path.stat()
    need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
         and len(raw) == pin['bytes'] and sha(raw) == pin['sha256'], 'actual-file-pin')
    return raw


def parse_time(value):
    need(type(value) is str, 'UTC-time-required')
    value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(value.tzinfo is not None and value.utcoffset() == dt.timedelta(0), 'UTC-required')
    return value


def windows_path(value):
    need(type(value) is str and re.fullmatch(r'[A-Za-z]:\\[^:*?"<>|]+', value) is not None,
         'absolute-Windows-payload-path')
    p = PureWindowsPath(value)
    need(not any(x.casefold() == 'worktrees' or x in ('.', '..') for x in p.parts),
         'ordinary-Windows-payload-name')
    return p


def validate_binding(b):
    """The reviewer target/controller/qa/host subset, with no Mac dependency."""
    need(type(b) is dict and set(b) == {'target', 'controller', 'qa', 'host'}, 'closed-four-scope-binding')
    t, c, q, h = (b[k] for k in ('target', 'controller', 'qa', 'host'))
    need(type(t) is dict and set(t) == {'source', 'tree', 'producerRun', 'runAttempt',
         'compositionReceiptSHA256', 'qualificationReceiptSHA256', 'cold95ReceiptSHA256'}, 'closed-target-binding')
    for group in (t, c, q):
        need(type(group) is dict and digest(group.get('source'), 40) and digest(group.get('tree'), 40), 'actual-source-tree')
    need(all(positive(t.get(k)) for k in ('producerRun', 'runAttempt'))
         and all(digest(t[k]) for k in ('compositionReceiptSHA256', 'qualificationReceiptSHA256', 'cold95ReceiptSHA256')),
         'actual-six-owner-receipts-required')
    need(type(c) is dict and set(c) == {'source', 'tree', 'producerRun', 'runAttempt', 'archive',
         'debSHA256', 'mainSHA256', 'qualificationReceiptSHA256'}, 'closed-controller-binding')
    need((c['source'], c['tree'], c['producerRun']) == (LINUX_SOURCE, LINUX_TREE, LINUX_RUN)
         and positive(c['runAttempt']), 'independent-qualified070-controller-not-six-owner')
    need(all(digest(c[k]) for k in ('debSHA256', 'mainSHA256', 'qualificationReceiptSHA256')),
         'actual-controller-package-receipts')
    need(set(c['archive']) == {'id', 'bytes', 'sha256'} and positive(c['archive']['id'])
         and positive(c['archive']['bytes']) and c['archive']['bytes'] <= 128*1024*1024
         and digest(c['archive']['sha256']), 'actual-controller-original-archive')
    need(set(q) == {'source', 'tree', 'driverSHA256', 'observerSHA256'}
         and digest(q['driverSHA256']) and digest(q['observerSHA256']), 'actual-current-QA-files')
    need(type(h) is dict and set(h) == {'MainPid', 'Birth', 'Sid', 'Session', 'registeredId',
         'OuterPath', 'OuterSHA256', 'MainPath', 'MainSHA256', 'CorePath', 'CoreSHA256'}, 'closed-host-binding')
    need(all(positive(h[k]) for k in ('MainPid', 'Birth', 'Session'))
         and re.fullmatch(r'S-1-5-21-(?:[0-9]+-){3}[0-9]+', h.get('Sid', '')) is not None
         and re.fullmatch(r'[0-9]{6,16}', h.get('registeredId', '')) is not None, 'real-host-kernel-identity')
    for role in ('Outer', 'Main', 'Core'):
        windows_path(h[role + 'Path'])
        need(digest(h[role + 'SHA256']), 'qualified-Windows-binary-digest')
    need(windows_path(h['MainPath']).name.casefold() == 'mixel-remote.exe'
         and windows_path(h['CorePath']).name.casefold() == 'libmixel-remote.dll'
         and windows_path(h['MainPath']).parent == windows_path(h['CorePath']).parent,
         'concrete-main-core-payload-roles')
    return b


def admission(raw, expected, now=None):
    need(digest(expected) and len(raw) <= 65536 and sha(raw) == expected, 'Root-admission-byte-pin')
    a = json.loads(raw)
    need(type(a) is dict and set(a) == {'schema', 'decidedBy', 'decision', 'nonce', 'expiresAtUTC',
         'binding', 'receipts', 'prerequisites', 'flags', 'actorSources', 'driverFiles'}, 'closed-Root-admission')
    need((a['schema'], a['decidedBy'], a['decision']) == ('mixel-six-owner-held-input-admission-v1',
         'Root', 'admit-one-private-held-input-controller'), 'explicit-Root-runtime-decision')
    need(type(a['nonce']) is str and re.fullmatch('[0-9a-f]{24}', a['nonce']), 'fresh-owned-nonce')
    until = parse_time(a['expiresAtUTC'])
    current = now or dt.datetime.now(dt.timezone.utc)
    need(dt.timedelta(0) < until-current <= dt.timedelta(hours=2), 'bounded-current-admission')
    b = validate_binding(a['binding'])
    need(a['flags'] == FLAGS and all(type(v) is bool for v in a['flags'].values()), 'all-global-claims-false')
    need(a['prerequisites'] == {'syntheticClipboardBothEnds': True, 'independentNASConsole': True,
         'freshAdobeExclusive': True, 'noOtherRemoteControllers': True}
         and all(type(v) is bool for v in a['prerequisites'].values()),
         'explicit-independent-operator-prerequisites')
    need(set(a['receipts']) == RECEIPTS, 'all-qualified-receipts-required')
    matches = {'composition': b['target']['compositionReceiptSHA256'],
               'windowsQualification': b['target']['qualificationReceiptSHA256'],
               'cold95': b['target']['cold95ReceiptSHA256'],
               'linuxQualification': b['controller']['qualificationReceiptSHA256']}
    for role, pin in a['receipts'].items():
        raw_receipt = read_pin(pin)
        need(type(json.loads(raw_receipt)) is dict, 'actual-JSON-receipt')
        if role in matches:
            need(pin['sha256'] == matches[role], 'Root-bound-qualified-receipt')
    need(type(a['driverFiles']) is dict and set(a['driverFiles']) == {
         'scripts/pc01-held-input-controller.py', 'scripts/pc01-held-input-observer.ps1',
         'scripts/fixtures/pc01-held-input/ReadOnlyIdentity.cs',
         'scripts/fixtures/pc01-held-input/KeyState.cs',
         'scripts/fixtures/pc01-held-input/viewer-monitor.py'}, 'finite-current-driver-files')
    for name, pin in a['driverFiles'].items():
        read_pin(pin, limit=128*1024)
    need(a['driverFiles']['scripts/pc01-held-input-controller.py']['sha256'] == b['qa']['driverSHA256']
         and a['driverFiles']['scripts/pc01-held-input-observer.ps1']['sha256'] == b['qa']['observerSHA256'],
         'current-driver-qa-binding')
    need(type(a['actorSources']) is dict and set(a['actorSources']) == set(FROZEN), 'exact-frozen-actor-source-pins')
    for name, pin in a['actorSources'].items():
        need(pin['sha256'] == FROZEN[name] and sha(read_pin(pin, limit=128*1024)) == FROZEN[name],
             'frozen03-source-bytes-only')
    # The Root host baseline receipt is bound, recent, and carries the same actual
    # identity and package hashes; it cannot be a source-only readiness template.
    baseline = json.loads(read_pin(a['receipts']['hostBaseline']))
    verify_observation(b, baseline, expected_count=0)
    need(released(baseline['highBits']), 'baseline-all-relevant-input-up')
    age = current - parse_time(baseline['observedAtUTC'])
    need(dt.timedelta(0) <= age <= dt.timedelta(minutes=30), 'fresh-host-baseline-required')
    return a


def verify_observation(binding, r, expected_count=None):
    h, t = binding['host'], binding['target']
    need(type(r) is dict and r.get('applicationSource') == t['source']
         and r.get('producerRun') == t['producerRun'] and r.get('runAttempt') == t['runAttempt'], 'host-observation-current-target')
    exact = {'pid': h['MainPid'], 'birth': h['Birth'], 'sid': h['Sid'], 'session': h['Session'],
             'registeredId': h['registeredId'], 'pipeOwnerPid': h['MainPid'],
             'outerSha256': h['OuterSHA256'], 'mainSha256': h['MainSHA256'], 'coreSha256': h['CoreSHA256']}
    need(all(type(r.get(k)) is type(v) and r[k] == v for k, v in exact.items()), 'same-host-kernel-binary-identity')
    need(type(r.get('path')) is str and r['path'].casefold() == h['MainPath'].casefold(), 'same-host-main-path')
    need(r.get('integrity') == 8192 and type(r['integrity']) is int and r.get('elevated') is False
         and r.get('guard') == 'attended-runtime-v2' and r.get('kernelLease') == 'active'
         and r.get('keyConfirmed') is True and r.get('ipcQueries') == 6, 'actual-Medium-guard-lease')
    need(type(r.get('remoteAuthenticated')) is int and r['remoteAuthenticated'] in (0, 1), 'typed-actual-auth-count')
    if expected_count is not None:
        need(r['remoteAuthenticated'] == expected_count, 'required-auth-phase')
    need(type(r.get('monotonicMilliseconds')) is int and r['monotonicMilliseconds'] > 0,
         'actual-host-monotonic-clock')
    parse_time(r.get('observedAtUTC'))
    need(r.get('inputDesktop') == 'Default' and r.get('observerDesktop') == 'Default'
         and positive(r.get('foregroundPid')) and positive(r.get('foregroundBirth'))
         and r.get('foregroundSid') == h['Sid'] and type(r.get('foregroundSession')) is int
         and r['foregroundSession'] == h['Session']
         and type(r.get('foregroundIntegrity')) is int and r['foregroundIntegrity'] == 8192
         and r.get('foregroundElevated') is False
         and r.get('desktopAccess') == 'existing-token-dacl-hook-and-journal',
         'accessible-actual-input-desktop-and-foreground')
    bits = r.get('highBits')
    need(type(bits) is dict and set(bits) == set(BITS) and all(type(v) is bool for v in bits.values()),
         'native-typed-key-button-high-bits')
    return r


def released(bits):
    return not any(bits.values())


def held(bits, case):
    if case.endswith('-shift'):
        return bits == dict(shift=True, leftShift=True, rightShift=False, leftButton=False, rightButton=False)
    return bits == dict(shift=False, leftShift=False, rightShift=False, leftButton=True, rightButton=False)


def stable_release(rows):
    fields=('foregroundPid','foregroundBirth','foregroundSid','foregroundSession',
            'foregroundIntegrity','foregroundElevated','inputDesktop','observerDesktop','desktopAccess')
    first=None;prior=None
    for row in rows:
        context=tuple(row[k] for k in fields)
        if context!=prior:
            first=row;prior=context
        elif ((parse_time(row['observedAtUTC'])-parse_time(first['observedAtUTC'])).total_seconds()>=1
              and row['monotonicMilliseconds']-first['monotonicMilliseconds']>=1000):
            return True
    return False


def join_case(binding, case, rows, event, ui, expected_admission):
    """Pure independent evidence join; never substitutes controller OCR facts."""
    validate_binding(binding)
    need(case in CASES and type(rows) is list and 5 <= len(rows) <= 450, 'bounded-case-native-rows')
    need(digest(expected_admission) and event.get('RootAdmissionSHA256') == expected_admission
         and ui.get('RootAdmissionSHA256') == expected_admission, 'exact-current-case-and-UI-admission-pin')
    previous = None
    for row in rows:
        verify_observation(binding, row)
        need(row.get('case') == case and row.get('RootAdmissionSha256') == event['RootAdmissionSHA256'],
             'same-held-case-and-admission')
        if previous:
            need(row['monotonicMilliseconds'] > previous['monotonicMilliseconds']
                 and parse_time(row['observedAtUTC']) > parse_time(previous['observedAtUTC']), 'ordered-independent-host-samples')
        previous = row
    need(event.get('targetSource') == binding['target']['source']
         and event.get('cleanup') == ['own-container-stopped-and-removed', 'own-image-removed'],
         'actual-target-case-owned-cleanup')
    need(event.get('case') == case and event.get('input') == ('shift' if case.endswith('-shift') else 'leftButton')
         and event.get('hostFactsInferredFromOCR') is False and event.get('controllerSource') == LINUX_SOURCE,
         'actual-controller-case-scope')
    need(type(ui.get('controllerToHostClockOffsetMilliseconds')) is int
         and abs(ui['controllerToHostClockOffsetMilliseconds']) <= 1000
         and type(ui.get('clockUncertaintyMilliseconds')) is int
         and 0 <= ui['clockUncertaintyMilliseconds'] <= 250
         and digest(ui.get('clockObservationReceiptSHA256')), 'independent-bounded-clock-join')
    offset = dt.timedelta(milliseconds=ui['controllerToHostClockOffsetMilliseconds'])
    uncertainty = dt.timedelta(milliseconds=ui['clockUncertaintyMilliseconds'])
    host_time = lambda r: parse_time(r['observedAtUTC']) - offset
    start, release = (parse_time(event[k]) for k in ('heldAtUTC', 'sourceReleaseAtUTC'))
    effect = (parse_time(ui['disconnectAtUTC']) - offset if case.startswith('normal-disconnect-')
              else parse_time(event['actionAtUTC']))
    need(start < effect < release and (release-effect).total_seconds() >= 15, 'controller-remains-held-across-action')
    before = [r for r in rows if host_time(r) + uncertainty < start and r['remoteAuthenticated'] == 0 and released(r['highBits'])]
    accepted = [r for r in rows if host_time(r) + uncertainty < start and r['remoteAuthenticated'] == 1 and released(r['highBits'])]
    down = [r for r in rows if start + uncertainty <= host_time(r) and host_time(r) + uncertainty < effect
            and r['remoteAuthenticated'] == 1 and held(r['highBits'], case)]
    up = [r for r in rows if effect + uncertainty < host_time(r) and host_time(r) + uncertainty < release
          and r['remoteAuthenticated'] == 0 and released(r['highBits'])]
    need(before and accepted and down and len(up) >= 2 and stable_release(up), 'independent-auth0-accepted-held-released-sequence')
    need(all(r['remoteAuthenticated'] == 0 and released(r['highBits']) for r in rows
             if host_time(up[0]) <= host_time(r) and host_time(r) + uncertainty < release),
         'no-late-input-or-auth-revival-before-source-release')
    need(parse_time(before[-1]['observedAtUTC']) < parse_time(accepted[-1]['observedAtUTC'])
         < parse_time(down[0]['observedAtUTC']) < parse_time(up[0]['observedAtUTC']), 'genuine-phase-order')
    need(ui.get('decidedBy') == 'Root' and ui.get('case') == case and ui.get('actualCustomerAccept') is True
         and ui.get('sameHostPid') == binding['host']['MainPid'] and ui.get('sameHostBirth') == binding['host']['Birth']
         and digest(ui.get('consoleProofSHA256')), 'independent-actual-console-proof-required')
    if case.startswith('normal-disconnect-'):
        need(event.get('action') == 'operator-host-disconnect' and ui.get('actualHostDisconnect') is True,
             'actual-host-Disconnect-not-controller-close')
        need(parse_time(event['operatorWindowStartsUTC']) <= effect < release - dt.timedelta(seconds=15),
             'actual-Disconnect-inside-held-operator-window')
        if case.endswith('-leftButton'):
            need(ui.get('disconnectMethod') in ('keyboard-preFocused-normal-Disconnect',
                 'normalGUI-Invoke-without-input-injection')
                 and ui.get('noHostMouseEventsWhileHeld') is True
                 and ui.get('noVNCpointerEventsWhileHeld') is True
                 and ui.get('noHostMouseButtonEventsWhileHeld') is True, 'no-physical-mouse-up-substitute')
        else:
            need(ui.get('noHostShiftEventsWhileHeld') is True
                 and ui.get('noOperatorClearModifiers') is True, 'no-physical-Shift-up-substitute')
    else:
        v = event.get('ownedViewer', {})
        need(event.get('action') == 'owned-viewer-pidfd-loss' and event.get('pidfdCapturedBeforeHold') is True
             and event.get('pidfdExitObserved') is True and positive(v.get('pid')) and positive(v.get('startTicks'))
             and v.get('sha256') == binding['controller']['mainSHA256'] and v.get('exe') ==
             '/usr/local/mixel-test/usr/share/mixel-remote/mixel-remote', 'exact-owned-viewer-loss-kernel-proof')
        need(ui.get('noHostInputWhileHeld') is True, 'no-physical-key-or-button-up-substitute')
    return {'case': case, 'independentHostHeldAndReleased': True, 'sameHostPid': binding['host']['MainPid'],
            'sameHostBirth': binding['host']['Birth'], 'releaseMillisecondsAfterAction':
            (host_time(up[0])-effect).total_seconds()*1000, **FLAGS}


def source_inputs(root):
    root = ordinary(root)
    for name, expected in FROZEN.items():
        need(sha(ordinary(root/name, file=True).read_bytes()) == expected, 'frozen03-input-unchanged')
    # Importing declarations performs no process/API calls; old main is excluded.
    path = root/'scripts/pc01-linux-controller.py'
    namespace = {'__name__': 'mixel_frozen_controller', '__file__': str(path)}
    exec(compile(path.read_bytes(), str(path), 'exec'), namespace)
    for name, expected in namespace['SOURCE_PINS'].items():
        need(sha(ordinary(root/name, file=True).read_bytes()) == expected, 'exact-controller-primitives-input')
    return namespace


def save(path, value):
    with path.open('x') as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


def project(payload, expected):
    need(set(expected) == {'client.deb', 'mixel-proof.bin', 'ipc_probe.py', 'controller-primitives.py', 'viewer-monitor.py'},
         'finite-nonsecret-projection')
    need({p.name for p in payload.iterdir()} == set(expected), 'no-extra-payload-members')
    result = []
    for name in sorted(expected):
        p = ordinary(payload/name, file=True)
        raw = p.read_bytes()
        need(sha(raw) == expected[name], 'exact-nonsecret-payload')
        result.append(dict(name=name, bytes=len(raw), sha256=expected[name]))
    payload.chmod(0o755)
    for name in expected:
        (payload/name).chmod(0o644)
    return dict(directoryMode=0o755, fileMode=0o644, members=result, readonlyBindRequired=True)


def hosted(env):
    need(sys.platform.startswith('linux') and env.get('GITHUB_ACTIONS') == 'true'
         and env.get('RUNNER_ENVIRONMENT') == 'github-hosted' and env.get('RUNNER_OS') == 'Linux'
         and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'explicit-hosted-only-runtime')


def exit_allowed(row, lost):
    need(type(row) is dict and type(row.get('pid')) is int and type(row.get('wait_status')) is int,
         'typed-controller-exit-record')
    return row['wait_status'] == 0 or (lost is not None and row['pid'] == lost['pid']
                                      and row['wait_status'] == 128 + signal.SIGKILL)


def held_actor_type(base):
    class HeldActor(base):
        owned_loss = None

        def connect(self, phase):
            pid = self.p.launch('controller', ['--connect', self.a['host']['id'], '--relay'])
            window = self.viewer()
            self.p.activate('controller', window)
            self.gui(['wmctrl', '-ir', window, '-b', 'add,fullscreen'])
            viewer_pid = int(self.gui(['xdotool', 'getwindowpid', window]).stdout.strip())
            self.phases.append(dict(phase=phase+'-viewer', senderPid=pid,
                                    window=window, identity=self.identity(viewer_pid)))
            print('OPERATOR: start independent host sampler for this case NOW (180s). '
                  'Verify same admitted PID/birth/three hashes/Medium/auth0 and controller ID. '
                  'Do not Accept for the next pending interval. No password or invite.', flush=True)
            time.sleep(15)
            for index in range(3):
                self.screen(phase+'-pending-'+str(index))
                time.sleep(4)
            print('OPERATOR: pending12s finished; use actual NAS blue Accept, verify auth1/up, '
                  'and show only the requested synthetic Notepad marker within45s.', flush=True)
            self.marker('mixelready'+self.a['nonce']+phase, phase+'-accepted', timeout=45)
            self.p.activate('controller', window)
            return window

        def alive(self, timeout=30):
            if self.owned_loss is None:
                return super().alive(timeout=timeout)
            # Only the exact pidfd-confirmed intentional viewer loss is expected.
            # Server and every other retained actor identity must remain alive.
            for value in self.identities.values():
                if value != self.owned_loss:
                    need(self.identity(value['pid'], timeout=timeout) == value, 'same-controller-kernel-process')
            raw = self.run(['python3', '-c', "import json;from pathlib import Path;print(json.dumps([json.loads(p.read_text()) for p in Path('/proofs').glob('launch-*-exit.json')]))"], timeout=timeout).stdout
            need(all(exit_allowed(x, self.owned_loss) for x in json.loads(raw)), 'no-unexpected-controller-exit')
    return HeldActor


def runtime(a, admission_sha, root, deb, output, old):
    """Only the fresh Linux controller is controlled; PC01 receives no script."""
    hosted(os.environ)
    c, h = a['binding']['controller'], a['binding']['host']
    for name, pin in a['driverFiles'].items():
        need(ordinary(root/name, file=True).read_bytes() == read_pin(pin, limit=128*1024),
             'actual-checked-out-driver-file')
    need(0 < deb.stat().st_size <= 256*1024*1024, 'bounded-staged-DEB')
    need(sha(deb.read_bytes()) == c['debSHA256'], 'pre-staged-qualified-controller-DEB')
    need(old['sha'](Path(__file__).read_bytes()) == a['binding']['qa']['driverSHA256']
         and old['sha']((root/'scripts/pc01-held-input-observer.ps1').read_bytes()) == a['binding']['qa']['observerSHA256'],
         'exact-new-driver-and-observer')
    actual = lambda arg: subprocess.check_output(['git', '-C', str(root), 'rev-parse', arg], text=True).strip()
    need((actual('HEAD'), actual('HEAD^{tree}')) == (a['binding']['qa']['source'], a['binding']['qa']['tree']), 'exact-QA-checkout')
    ordinary(output, absent=True)  # All admission/input/source validation precedes mutation.
    output.mkdir(mode=0o700)
    save(output/'ADMISSION.json', a)
    events = []
    for index, case in enumerate(CASES):
        own = output/case; own.mkdir(mode=0o700)
        payload = own/'payload'; payload.mkdir(mode=0o700)
        (own/'controller-desktop').mkdir(mode=0o700)
        (payload/'client.deb').write_bytes(deb.read_bytes())
        (payload/'mixel-proof.bin').write_bytes(b'MIXEL_SYNTHETIC_HELD_INPUT_ONLY\n')
        primitives = old['extracted_primitives'](root, payload)
        (payload/'viewer-monitor.py').write_bytes((root/FIXTURE/'viewer-monitor.py').read_bytes())
        projection = project(payload, {p.name: sha(p.read_bytes()) for p in payload.iterdir()})
        legacy = dict(nonce=a['nonce'], artifact=dict(mainSHA256=c['mainSHA256']), host=dict(id=h['registeredId']))
        actor = held_actor_type(old['Controller'])(primitives, root, own, legacy, payload, projection)
        error = None
        cleanup = []
        selected = 'shift' if case.endswith('-shift') else 'leftButton'
        event = dict(case=case, input=selected, controllerSource=LINUX_SOURCE,
                     targetSource=a['binding']['target']['source'], hostFactsInferredFromOCR=False,
                     RootAdmissionSHA256=admission_sha)
        try:
            actor.setup()
            window = actor.connect('held' + str(index))
            viewer = actor.identity(int(actor.gui(['xdotool', 'getwindowpid', window]).stdout.strip()))
            need(viewer['pid'] != actor.server, 'fresh-viewer-distinct-from-owned-server')
            event['ownedViewer'] = viewer
            monitor = uuid.uuid4().hex
            action = 'kill' if case.startswith('viewer-loss-') else 'release'
            if action == 'kill':
                args = ['python3', '-B', '/payload/viewer-monitor.py', '--pid', str(viewer['pid']), '--start', str(viewer['startTicks']),
                        '--exe', viewer['exe'], '--sha256', viewer['sha256'], '--nonce', monitor, '--action', action]
                script = 'nohup ' + shlex.join(args) + ' > /proofs/monitor-' + monitor + '.jsonl 2>&1 < /dev/null &'
                actor.run(['bash', '-c', script])
                def captured():
                    raw = actor.run(['cat', '/proofs/monitor-' + monitor + '.jsonl'], check=False).stdout
                    return raw.strip() and json.loads(raw.splitlines()[0])
                cap = old['until']('actual owned pidfd capture', captured, timeout=10)
                need(cap == dict(event='captured', nonce=monitor, identity=viewer), 'exact-pre-hold-pidfd-capture')
                event['pidfdCapturedBeforeHold'] = True
            print('OPERATOR case ' + case + ': keep independent host sampler running. Verify actual auth1/up. '
                  'For button Disconnect, pre-focus normal host Disconnect and later use keyboard Enter or its normal GUI Invoke, '
                  'with NO noVNC pointer updates (including mask0) until release is independently seen. '
                  'For Shift, send NO operator Shift event/clearmodifiers until release. '
                  'For viewer loss, do not touch host input while held. The driver begins hold in20s.', flush=True)
            time.sleep(20)
            actor.p.activate('controller', window)
            actor.gui(['xdotool', 'keydown', 'Shift_L'] if selected == 'shift' else ['xdotool', 'mousedown', '1'])
            event['heldAtUTC'] = dt.datetime.now(dt.timezone.utc).isoformat()
            print('OPERATOR: input HELD. Confirm native held high-bit/auth1 independently. Action begins in10s.', flush=True)
            time.sleep(10)
            if action == 'kill':
                actor.run(['python3', '-c', "from pathlib import Path;p=Path('/proofs/viewer-" + monitor + ".request');p.write_bytes(b'kill:" + monitor + "')"])
                event['action'] = 'owned-viewer-pidfd-loss'
                def completed():
                    raw = actor.run(['cat', '/proofs/monitor-' + monitor + '.jsonl']).stdout
                    lines = raw.splitlines()
                    return json.loads(lines[-1]) if len(lines) == 2 else None
                done = old['until']('retained pidfd exact viewer exit', completed, timeout=12)
                need(done.get('event') == 'completed' and done.get('identity') == viewer and done.get('action') == 'kill', 'actual-owned-viewer-loss')
                event['actionAtUTC'] = done['sentAtUTC']
                event['pidfdExitAtUTC'] = done['completedAtUTC']
                event['pidfdExitObserved'] = True
                actor.owned_loss = viewer
            else:
                # Actual host action time comes ONLY from the independent Root
                # console proof in final join, not this controller prompt.
                event['action'] = 'operator-host-disconnect'
                event['operatorWindowStartsUTC'] = dt.datetime.now(dt.timezone.utc).isoformat()
                print('OPERATOR: NOW activate normal PC01 Disconnect; retain exact UTC and auth0/high-bit release. Source input remains held30s.', flush=True)
            time.sleep(30)
        except BaseException as exc:
            error = exc
        finally:
            try:
                if actor.created:
                    actor.ownership(timeout=3)
                    actor.gui(['xdotool', 'keyup', 'Shift_L'] if selected == 'shift' else ['xdotool', 'mouseup', '1'], check=False)
                    event['sourceReleaseAtUTC'] = dt.datetime.now(dt.timezone.utc).isoformat()
            except Exception as exc:
                if error is None:
                    error = exc
            cleanup = actor.cleanup()
            event['cleanup'] = cleanup
            event['independentHostEvidencePending'] = True
            save(own/'CONTROLLER-CASE.json', event)
        need(cleanup == ['own-container-stopped-and-removed', 'own-image-removed'], 'owned-cleanup-must-succeed')
        if error:
            raise error
        events.append(event)
    save(output/'CONTROLLER-RESULT.json', dict(events=events, independentHostJoinPending=True,
                                             hostedArtifactGETs=0, **FLAGS))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--admission', type=Path, required=True)
    p.add_argument('--admission-sha256', required=True)
    p.add_argument('--controller-deb', type=Path)
    p.add_argument('--output', type=Path)
    p.add_argument('--run', action='store_true')
    p.add_argument('--join', type=Path)
    p.add_argument('--join-sha256')
    args = p.parse_args()
    # No output creation, Git calls, imports of actor primitives or Docker calls
    # until all actual bindings and receipts validate.
    ap = ordinary(args.admission, file=True)
    a = admission(ap.read_bytes(), args.admission_sha256)
    need(not (args.run and args.join), 'separate-runtime-and-offline-join')
    if args.join:
        jp = ordinary(args.join, file=True)
        raw = jp.read_bytes()
        need(len(raw) <= 65536 and digest(args.join_sha256) and sha(raw) == args.join_sha256,
             'Root-pinned-independent-join-index')
        inputs = json.loads(raw)
        need(set(inputs) == {'schema', 'RootAdmissionSHA256', 'cases'}
             and inputs['schema'] == 'mixel-six-owner-held-input-join-v1'
             and inputs['RootAdmissionSHA256'] == args.admission_sha256
             and set(inputs['cases']) == set(CASES), 'all-four-cases-independent-inputs')
        results = []
        for case in CASES:
            pins = inputs['cases'][case]
            need(set(pins) == {'hostRows', 'controllerCase', 'RootConsoleProof'}, 'closed-case-evidence-pins')
            rows, event, ui = (json.loads(read_pin(pins[k])) for k in ('hostRows', 'controllerCase', 'RootConsoleProof'))
            results.append(join_case(a['binding'], case, rows, event, ui, args.admission_sha256))
        need(args.output is not None, 'fresh-join-output-required')
        output = ordinary(args.output, absent=True)
        output.mkdir(mode=0o700)
        save(output/'HELD-INPUT-JOIN.json', dict(cases=results, RootAdmissionSHA256=args.admission_sha256,
             inputIndexSHA256=args.join_sha256, scope='independent-held-input-evidence-only', **FLAGS))
        return
    if not args.run:
        print(json.dumps(dict(bindingsValidated=True, runtimeExecuted=False, **FLAGS)))
        return
    need(args.controller_deb is not None and args.output is not None, 'explicit-staged-DEB-and-fresh-output')
    deb = ordinary(args.controller_deb, file=True)
    output = raw_safe(args.output)
    root = ordinary(Path(__file__).absolute().parents[1])
    old = source_inputs(root)
    def deadline(signum, frame):
        raise TimeoutError('owned-held-controller-operator-deadline')
    signal.signal(signal.SIGTERM, deadline); signal.signal(signal.SIGINT, deadline)
    runtime(a, args.admission_sha256, root, deb, output, old)


if __name__ == '__main__':
    main()
