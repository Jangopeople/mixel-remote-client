#!/usr/bin/env python3
"""Inert POSIX-controller controls; Windows observer compilation is separate.

No Docker, process, compiler, Win32 or network calls are made by this suite.
"""
import ast
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).absolute().parents[1]
SPEC = importlib.util.spec_from_file_location('held', ROOT/'scripts/pc01-held-input-controller.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
TEST_TEMP_ROOT = '/private/tmp' if sys.platform == 'darwin' else '/tmp'


def binding():
    # Explicitly synthetic inert values; never emitted as a runtime admission.
    return {
        'target': dict(source='a'*40, tree='b'*40, producerRun=112233, runAttempt=2,
                       compositionReceiptSHA256='1'*64, qualificationReceiptSHA256='2'*64,
                       cold95ReceiptSHA256='3'*64),
        'controller': dict(source=M.LINUX_SOURCE, tree=M.LINUX_TREE, producerRun=M.LINUX_RUN,
                           runAttempt=1, archive=dict(id=998877, bytes=1024, sha256='4'*64),
                           debSHA256='5'*64, mainSHA256='6'*64, qualificationReceiptSHA256='7'*64),
        'qa': dict(source='c'*40, tree='d'*40, driverSHA256='8'*64, observerSHA256='9'*64),
        'host': dict(MainPid=1234, Birth=987654321, Sid='S-1-5-21-1-2-3-1001', Session=1,
                     registeredId='123456789', OuterPath=r'C:\Users\QA\Downloads\Mixel-Remote-QS.exe',
                     OuterSHA256='a'*64, MainPath=r'C:\Users\QA\AppData\Local\Temp\Mixel\Mixel-Remote.exe',
                     MainSHA256='b'*64, CorePath=r'C:\Users\QA\AppData\Local\Temp\Mixel\libmixel-remote.dll',
                     CoreSHA256='c'*64)}


BASE = dt.datetime(2026, 10, 17, 20, 0, tzinfo=dt.timezone.utc)


def utc(seconds):
    return (BASE+dt.timedelta(seconds=seconds)).isoformat()


def sample(b, case, seconds, count, bits=None):
    h, t = b['host'], b['target']
    return dict(applicationSource=t['source'], producerRun=t['producerRun'], runAttempt=t['runAttempt'],
                RootAdmissionSha256='e'*64, case=case, observedAtUTC=utc(seconds),
                monotonicMilliseconds=100000+int(seconds*1000), pid=h['MainPid'], birth=h['Birth'],
                sid=h['Sid'], session=h['Session'], path=h['MainPath'], registeredId=h['registeredId'],
                pipeOwnerPid=h['MainPid'], outerSha256=h['OuterSHA256'], mainSha256=h['MainSHA256'],
                coreSha256=h['CoreSHA256'], integrity=8192, elevated=False, guard='attended-runtime-v2',
                kernelLease='active', keyConfirmed=True, ipcQueries=6, remoteAuthenticated=count,
                inputDesktop='Default', observerDesktop='Default', foregroundPid=6543,
                foregroundBirth=777777,foregroundSid=h['Sid'],foregroundSession=h['Session'],
                foregroundIntegrity=8192,foregroundElevated=False,
                desktopAccess='existing-token-dacl-hook-and-journal',
                highBits=bits or dict.fromkeys(M.BITS, False))


def evidence(case):
    b = binding()
    bits = dict.fromkeys(M.BITS, False)
    if case.endswith('-shift'):
        bits.update(shift=True, leftShift=True)
    else:
        bits['leftButton'] = True
    rows = [sample(b, case, s, n, v) for s, n, v in
            [(0, 0, None), (3, 1, None), (12, 1, bits), (22, 0, None), (24, 0, None)]]
    event = dict(case=case, input='shift' if case.endswith('-shift') else 'leftButton',
                 controllerSource=M.LINUX_SOURCE, targetSource=b['target']['source'],
                 RootAdmissionSHA256='e'*64, heldAtUTC=utc(10), sourceReleaseAtUTC=utc(50),
                 hostFactsInferredFromOCR=False,
                 cleanup=['own-container-stopped-and-removed', 'own-image-removed'])
    ui = dict(RootAdmissionSHA256='e'*64,decidedBy='Root', case=case, actualCustomerAccept=True, sameHostPid=b['host']['MainPid'],
              sameHostBirth=b['host']['Birth'], consoleProofSHA256='d'*64,
              controllerToHostClockOffsetMilliseconds=0, clockUncertaintyMilliseconds=100,
              clockObservationReceiptSHA256='f'*64)
    if case.startswith('normal-disconnect-'):
        event.update(action='operator-host-disconnect', operatorWindowStartsUTC=utc(20))
        ui.update(actualHostDisconnect=True, disconnectAtUTC=utc(21),
                  disconnectMethod='keyboard-preFocused-normal-Disconnect', noHostMouseEventsWhileHeld=True,
                  noVNCpointerEventsWhileHeld=True,noHostMouseButtonEventsWhileHeld=True,
                  noHostShiftEventsWhileHeld=True,noOperatorClearModifiers=True)
    else:
        event.update(action='owned-viewer-pidfd-loss', actionAtUTC=utc(21), pidfdCapturedBeforeHold=True,
                     pidfdExitObserved=True, ownedViewer=dict(pid=88, startTicks=998,
                     sha256=b['controller']['mainSHA256'], exe='/usr/local/mixel-test/usr/share/mixel-remote/mixel-remote'))
        ui['noHostInputWhileHeld'] = True
    return b, rows, event, ui


def admission_fixture(d):
    b=binding()
    def pin(name, raw):
        p=d/name;p.write_bytes(raw);return dict(path=str(p),bytes=len(raw),sha256=M.sha(raw))
    receipts={name:pin(name+'.json',json.dumps({'inertFixtureOnly':True,'role':name}).encode()) for name in M.RECEIPTS}
    b['target'].update(compositionReceiptSHA256=receipts['composition']['sha256'],
        qualificationReceiptSHA256=receipts['windowsQualification']['sha256'],cold95ReceiptSHA256=receipts['cold95']['sha256'])
    b['controller']['qualificationReceiptSHA256']=receipts['linuxQualification']['sha256']
    baseline=sample(b,'normal-disconnect-shift',0,0)
    receipts['hostBaseline']=pin('baseline.json',json.dumps(baseline).encode())
    files={str(p.relative_to(ROOT)):dict(path=str(p),bytes=p.stat().st_size,sha256=M.sha(p.read_bytes())) for p in
           [ROOT/'scripts/pc01-held-input-controller.py',ROOT/'scripts/pc01-held-input-observer.ps1',
            ROOT/M.FIXTURE/'ReadOnlyIdentity.cs',ROOT/M.FIXTURE/'KeyState.cs',ROOT/M.FIXTURE/'viewer-monitor.py']}
    b['qa'].update(driverSHA256=files['scripts/pc01-held-input-controller.py']['sha256'],
                   observerSHA256=files['scripts/pc01-held-input-observer.ps1']['sha256'])
    frozen={n:dict(path=str(ROOT/n),bytes=(ROOT/n).stat().st_size,sha256=M.sha((ROOT/n).read_bytes())) for n in M.FROZEN}
    a=dict(schema='mixel-six-owner-held-input-admission-v1',decidedBy='Root',
        decision='admit-one-private-held-input-controller',nonce='a'*24,expiresAtUTC=utc(300),binding=b,
        receipts=receipts,prerequisites=dict(syntheticClipboardBothEnds=True,independentNASConsole=True,
        freshAdobeExclusive=True,noOtherRemoteControllers=True),flags=M.FLAGS,actorSources=frozen,driverFiles=files)
    return a


class BindingControls(unittest.TestCase):
    def test_separate_six_target_and_frozen070_controller(self):
        b = binding()
        self.assertEqual(M.validate_binding(b), b)
        self.assertNotEqual(b['target']['source'], b['controller']['source'])
        b['controller']['source'] = b['target']['source']
        with self.assertRaisesRegex(ValueError, 'independent-qualified070'):
            M.validate_binding(b)

    def test_positive_attempts_not_fixed_to_one_or_boolean(self):
        b = binding()
        for n in (2, 7):
            b['target']['runAttempt'] = n
            M.validate_binding(b)
        for n in (0, -1, True, None, '2'):
            b['target']['runAttempt'] = n
            with self.assertRaises(ValueError): M.validate_binding(b)

    def test_unknown_missing_values_never_placeholders(self):
        for group, key in [('target','source'), ('target','cold95ReceiptSHA256'),
                           ('controller','debSHA256'), ('host','CoreSHA256'), ('qa','driverSHA256')]:
            b = binding(); b[group][key] = None
            with self.assertRaises((ValueError, TypeError)): M.validate_binding(b)

    def test_concrete_windows_roles_no_fictitious_service(self):
        b = binding(); b['host']['ServiceSHA256'] = 'a'*64
        with self.assertRaisesRegex(ValueError, 'closed-host'): M.validate_binding(b)
        b = binding(); b['host']['CorePath'] = r'C:\Elsewhere\libmixel-remote.dll'
        with self.assertRaisesRegex(ValueError, 'concrete-main-core'): M.validate_binding(b)

    def test_held_name_rejection_before_any_filesystem_call(self):
        for name in ('worktrees', 'WorkTrees', 'WORKTREES'):
            with mock.patch.object(Path, 'lstat', side_effect=AssertionError('must not inspect')):
                with self.assertRaisesRegex(ValueError, 'name-first'):
                    M.ordinary('/private/tmp/'+name+'/opaque-unread')
        # No held-name directories or descendants are created by this control.

    def test_windows_held_names_refuse_before_hash_or_native(self):
        for name in ('worktrees', 'WorkTrees'):
            b = binding(); b['host']['OuterPath'] = 'C:\\'+name+'\\opaque.exe'
            with self.assertRaisesRegex(ValueError, 'ordinary-Windows'): M.validate_binding(b)

    def test_symlink_and_hardlink_pin_refused(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT, prefix='mixel-held-inert-') as tmp:
            d=Path(tmp); p=d/'source'; p.write_bytes(b'inert')
            link=d/'link'; link.symlink_to(p)
            pin=dict(path=str(link), bytes=5, sha256=M.sha(b'inert'))
            with self.assertRaisesRegex(ValueError, 'no-link'): M.read_pin(pin)
            link.unlink(); os.link(p, link)
            pin['path']=str(p)
            with self.assertRaisesRegex(ValueError, 'single-link'): M.read_pin(pin)

    def test_nonsecret_guest_projection_modes_and_drift(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT, prefix='mixel-held-inert-') as tmp:
            root=Path(tmp); payload=root/'payload'; payload.mkdir(mode=0o700)
            names=['client.deb','mixel-proof.bin','ipc_probe.py','controller-primitives.py','viewer-monitor.py']
            expected={}
            for name in names:
                (payload/name).write_bytes(b'inert-'+name.encode()); expected[name]=M.sha((payload/name).read_bytes())
            result=M.project(payload,expected)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode),0o700)
            self.assertEqual(stat.S_IMODE(payload.stat().st_mode),0o755)
            self.assertTrue(all(stat.S_IMODE((payload/n).stat().st_mode)==0o644 for n in names))
            self.assertEqual(len(result['members']),5)
            (payload/'extra').write_text('inert')
            with self.assertRaisesRegex(ValueError,'extra'): M.project(payload,expected)

    def test_admission_requires_real_pinned_receipts_before_mutation(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT, prefix='mixel-held-admission-') as tmp:
            d=Path(tmp);a=admission_fixture(d)
            raw=json.dumps(a).encode()
            self.assertEqual(M.admission(raw,M.sha(raw),now=BASE),a)
            del a['receipts']['cold95'];raw=json.dumps(a).encode()
            with self.assertRaisesRegex(ValueError,'receipts-required'):
                M.admission(raw,M.sha(raw),now=BASE)
            self.assertEqual(set(p.name for p in d.iterdir()),{name+'.json' for name in M.RECEIPTS}|{'baseline.json'})


class EvidenceControls(unittest.TestCase):
    def test_all_four_genuine_held_sequences(self):
        for case in M.CASES:
            b,rows,event,ui=evidence(case)
            r=M.join_case(b,case,rows,event,ui,'e'*64)
            self.assertTrue(r['independentHostHeldAndReleased'])
            self.assertFalse(r['nativeQualified'])

    def test_old_releases_before_disconnect_is_negative(self):
        b,rows,event,ui=evidence(M.CASES[0]);event['sourceReleaseAtUTC']=utc(20)
        with self.assertRaisesRegex(ValueError,'remains-held'): M.join_case(b,M.CASES[0],rows,event,ui,'e'*64)

    def test_no_native_held_observation_is_negative(self):
        b,rows,event,ui=evidence(M.CASES[0]);rows[2]['highBits']=dict.fromkeys(M.BITS,False)
        with self.assertRaisesRegex(ValueError,'independent-auth0'): M.join_case(b,M.CASES[0],rows,event,ui,'e'*64)

    def test_stuck_key_or_button_after_disconnect_refused(self):
        for case in M.CASES:
            b,rows,event,ui=evidence(case)
            rows[3]['highBits']=rows[2]['highBits'];rows[4]['highBits']=rows[2]['highBits']
            with self.assertRaises(ValueError): M.join_case(b,case,rows,event,ui,'e'*64)

    def test_late_revival_after_up_refused(self):
        b,rows,event,ui=evidence(M.CASES[0]);rows.append(sample(b,M.CASES[0],26,1,rows[2]['highBits']))
        with self.assertRaisesRegex(ValueError,'late-input'): M.join_case(b,M.CASES[0],rows,event,ui,'e'*64)

    def test_auth_boolean_unknown_desktop_and_identity_drift_refused(self):
        changes=[('remoteAuthenticated',True),('inputDesktop','Winlogon'),('birth',111),
                 ('pipeOwnerPid',22),('mainSha256','f'*64),('elevated',True),('guard','absent')]
        for k,v in changes:
            b,rows,event,ui=evidence(M.CASES[0]);rows[3][k]=v
            with self.assertRaises(ValueError): M.join_case(b,M.CASES[0],rows,event,ui,'e'*64)

    def test_inaccessible_zeroes_foreground_elevation_reuse_and_desktop_switch_refused(self):
        case=M.CASES[0]
        for key,value in [('desktopAccess','unknown'),('foregroundIntegrity',12288),
                          ('foregroundElevated',True),('foregroundSid','S-1-5-21-9-8-7-1002'),
                          ('inputDesktop','Winlogon'),('observerDesktop','Default-inactive')]:
            b,rows,event,ui=evidence(case)
            # A zero from an inaccessible/changed context is never called Up.
            for r in rows[3:]: r[key]=value
            with self.assertRaisesRegex(ValueError,'desktop-and-foreground'):
                M.join_case(b,case,rows,event,ui,'e'*64)
        b,rows,event,ui=evidence(case);rows[4]['foregroundBirth']+=1
        with self.assertRaisesRegex(ValueError,'independent-auth0'):
            M.join_case(b,case,rows,event,ui,'e'*64)
        b,rows,event,ui=evidence(case);rows[4]['foregroundPid']+=1
        with self.assertRaisesRegex(ValueError,'independent-auth0'):
            M.join_case(b,case,rows,event,ui,'e'*64)

    def test_mouse_click_cannot_substitute_for_button_cleanup(self):
        case=M.CASES[1];b,rows,event,ui=evidence(case);ui['disconnectMethod']='mouseClick'
        with self.assertRaisesRegex(ValueError,'physical-mouse'): M.join_case(b,case,rows,event,ui,'e'*64)

    def test_vnc_pointer_mask0_or_shift_up_confound_refused(self):
        for field in ('noVNCpointerEventsWhileHeld','noHostMouseButtonEventsWhileHeld'):
            case=M.CASES[1];b,rows,event,ui=evidence(case);ui[field]=False
            with self.assertRaisesRegex(ValueError,'physical-mouse'): M.join_case(b,case,rows,event,ui,'e'*64)
        for field in ('noHostShiftEventsWhileHeld','noOperatorClearModifiers'):
            case=M.CASES[0];b,rows,event,ui=evidence(case);ui[field]=False
            with self.assertRaisesRegex(ValueError,'physical-Shift'): M.join_case(b,case,rows,event,ui,'e'*64)

    def test_viewer_loss_needs_retained_pidfd_and_no_host_input(self):
        case=M.CASES[2]
        for field in ('pidfdCapturedBeforeHold','pidfdExitObserved'):
            b,rows,event,ui=evidence(case);event[field]=False
            with self.assertRaisesRegex(ValueError,'kernel-proof'): M.join_case(b,case,rows,event,ui,'e'*64)
        b,rows,event,ui=evidence(case);ui['noHostInputWhileHeld']=False
        with self.assertRaisesRegex(ValueError,'physical-key'): M.join_case(b,case,rows,event,ui,'e'*64)

    def test_clock_uncertainty_and_unordered_samples_refused(self):
        b,rows,event,ui=evidence(M.CASES[0]);ui['clockUncertaintyMilliseconds']=10000
        with self.assertRaisesRegex(ValueError,'clock-join'): M.join_case(b,M.CASES[0],rows,event,ui,'e'*64)
        b,rows,event,ui=evidence(M.CASES[0]);rows[3]['monotonicMilliseconds']=rows[2]['monotonicMilliseconds']
        with self.assertRaisesRegex(ValueError,'ordered'): M.join_case(b,M.CASES[0],rows,event,ui,'e'*64)

    def test_coherent_old_case_rows_and_UI_refuse_current_admission(self):
        for case in M.CASES:
            b,rows,event,ui=evidence(case)
            event['RootAdmissionSHA256']='f'*64
            ui['RootAdmissionSHA256']='f'*64
            for r in rows: r['RootAdmissionSha256']='f'*64
            with self.assertRaisesRegex(ValueError,'exact-current-case'):
                M.join_case(b,case,rows,event,ui,'e'*64)

    def test_current_index_cannot_launder_old_coherent_case_before_output(self):
        import sys
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT,prefix='mixel-held-join-inert-') as tmp:
            root=Path(tmp);a=admission_fixture(root)
            raw=json.dumps(a).encode();ap=root/'admission';ap.write_bytes(raw);current=M.sha(raw)
            def pin(name,value):
                p=root/name;data=json.dumps(value).encode();p.write_bytes(data)
                return dict(path=str(p),bytes=len(data),sha256=M.sha(data))
            cases={}
            for index,case in enumerate(M.CASES):
                b,rows,event,ui=evidence(case)
                # The target and QA facts match the current validated binding;
                # only their coherent admission provenance is deliberately old.
                for r in rows:
                    r['applicationSource']=a['binding']['target']['source']
                    r['RootAdmissionSha256']='f'*64
                event['RootAdmissionSHA256']='f'*64;ui['RootAdmissionSHA256']='f'*64
                cases[case]=dict(hostRows=pin(str(index)+'-rows',rows),
                    controllerCase=pin(str(index)+'-event',event),RootConsoleProof=pin(str(index)+'-UI',ui))
            index=dict(schema='mixel-six-owner-held-input-join-v1',RootAdmissionSHA256=current,cases=cases)
            jp=root/'join-index';jraw=json.dumps(index).encode();jp.write_bytes(jraw);output=root/'not-created'
            original=M.admission
            with mock.patch.object(sys,'argv',['driver','--admission',str(ap),'--admission-sha256',current,
                 '--join',str(jp),'--join-sha256',M.sha(jraw),'--output',str(output)]),                 mock.patch.object(M,'admission',side_effect=lambda data,digest:original(data,digest,now=BASE)):
                with self.assertRaisesRegex(ValueError,'exact-current-case'):
                    M.main()
            self.assertFalse(output.exists())

    def test_wrong_admission_or_ocr_claim_refused(self):
        b,rows,event,ui=evidence(M.CASES[0]);rows[3]['RootAdmissionSha256']='f'*64
        with self.assertRaisesRegex(ValueError,'admission'): M.join_case(b,M.CASES[0],rows,event,ui,'e'*64)
        b,rows,event,ui=evidence(M.CASES[0]);event['hostFactsInferredFromOCR']=True
        with self.assertRaisesRegex(ValueError,'scope'): M.join_case(b,M.CASES[0],rows,event,ui,'e'*64)

    def test_expected_loss_does_not_hide_other_failures(self):
        lost=dict(pid=88)
        self.assertTrue(M.exit_allowed(dict(pid=88,wait_status=137),lost))
        self.assertFalse(M.exit_allowed(dict(pid=89,wait_status=137),lost))
        self.assertFalse(M.exit_allowed(dict(pid=88,wait_status=134),lost))
        self.assertFalse(M.exit_allowed(dict(pid=88,wait_status=137),None))


class SourceControls(unittest.TestCase):
    def test_frozen_three_sources_exact(self):
        for name, expected in M.FROZEN.items():
            self.assertEqual(M.sha((ROOT/name).read_bytes()),expected)

    def test_actual_frozen_primitive_import_performs_no_process_calls(self):
        with mock.patch.object(M.subprocess,'run',side_effect=AssertionError('no runtime')) as run,             mock.patch.object(M.subprocess,'check_output',side_effect=AssertionError('no Git/provider')) as check:
            old=M.source_inputs(ROOT)
        self.assertEqual(old['APP_SOURCE'],M.LINUX_SOURCE)
        self.assertEqual(old['APP_TREE'],M.LINUX_TREE)
        self.assertIn('Controller',old)
        run.assert_not_called();check.assert_not_called()

    def test_identity_import_is_exact_readonly_R3(self):
        raw=(ROOT/M.FIXTURE/'ReadOnlyIdentity.cs').read_bytes()
        self.assertEqual(M.sha(raw),'1b0b4fd52a307f2076e3296722b8891fbe11c05b3361be3fce0f47ed5433efe8')
        text=raw.decode()
        for bad in ('TerminateProcess','SendInput','SetEvent','GetOptions','GetPassword'):
            self.assertNotIn(bad,text)
        self.assertIn('0x101000',text);self.assertIn('OpenProcessToken ( process , 8',text)

    def test_observer_source_has_no_top_level_native_or_policy_actions(self):
        text=(ROOT/'scripts/pc01-held-input-observer.ps1').read_text()
        self.assertTrue(text.startswith('# Function declarations only.'))
        self.assertIn('function Initialize-MixelHeldObserver',text)
        self.assertIn("-CompilerOptions '/warnaserror+'",text)
        for bad in ('Set-ExecutionPolicy','SendKeys','SendInput','Stop-Process','Start-Process','Set-ItemProperty','WriteAllText'):
            self.assertNotIn(bad,text)
        self.assertIn('Input changed during sample; retry required',text)
        self.assertIn('CloseObservations()',text)
        key=(ROOT/M.FIXTURE/'KeyState.cs').read_text()
        self.assertIn('GetAsyncKeyState(0x10) & 0x8000',key)
        self.assertIn('OpenInputDesktop(0, false, 1)',key)
        self.assertIn('own != before',key)
        self.assertIn('IntPtr after = OpenInputDesktop(0, false, 1)',key)
        self.assertIn('DesktopName(after) != before',key)
        self.assertIn('ExistingAccess(GetThreadDesktop(GetCurrentThreadId()))',key)
        self.assertIn('OpenProcessToken(GetCurrentProcess(),8',key)
        self.assertNotIn('SetUserObjectSecurity',key)
        self.assertNotIn('SetWindowsHook',key)
        self.assertIn('Foreground PID/birth changed during high-bit sample',text)
        self.assertIn(M.sha(key.encode()),text)

    def test_pidfd_not_pid_kill_and_capture_precedes_hold(self):
        tree=ast.parse((ROOT/M.FIXTURE/'viewer-monitor.py').read_bytes())
        calls=[ast.unparse(x.func) for x in ast.walk(tree) if isinstance(x,ast.Call)]
        self.assertIn('os.pidfd_open',calls);self.assertIn('signal.pidfd_send_signal',calls)
        self.assertNotIn('os.kill',calls)
        source=(ROOT/'scripts/pc01-held-input-controller.py').read_text()
        self.assertLess(source.index("event['pidfdCapturedBeforeHold'] = True"),source.index("event['heldAtUTC'] ="))
        self.assertIn("viewer['pid'] != actor.server",source)

    def test_monitor_uses_retained_mock_pidfd_and_refuses_changed_identity(self):
        import contextlib,io,types
        spec=importlib.util.spec_from_file_location('monitor',ROOT/M.FIXTURE/'viewer-monitor.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        expected=dict(pid=88,startTicks=99,exe='/owned/viewer',sha256='d'*64)
        request=mock.Mock();request.exists.return_value=True;request.read_bytes.return_value=b'kill:'+b'e'*32
        fake_path=mock.MagicMock();fake_path.__truediv__.return_value=request
        with mock.patch.object(module,'fact',return_value=expected),             mock.patch.object(module,'Path',return_value=fake_path),             mock.patch.object(module.os,'pidfd_open',return_value=71,create=True) as opened,             mock.patch.object(module.os,'close') as closed,             mock.patch.object(module.signal,'pidfd_send_signal',create=True) as sent,             mock.patch.object(module.select,'select',side_effect=[([],[],[]),([71],[],[])]),             contextlib.redirect_stdout(io.StringIO()) as output:
            module.monitor(88,99,'/owned/viewer','d'*64,'e'*32,'kill',30)
            opened.assert_called_once_with(88,0);sent.assert_called_once_with(71,module.signal.SIGKILL,None,0)
            closed.assert_called_once_with(71)
            facts=[json.loads(row) for row in output.getvalue().splitlines()]
            self.assertEqual([r['event'] for r in facts],['captured','completed'])
            self.assertEqual(facts[-1]['identity'],expected)
        with mock.patch.object(module,'fact',return_value=dict(expected,startTicks=100)),             mock.patch.object(module.os,'pidfd_open',return_value=71,create=True),             mock.patch.object(module.os,'close') as closed,             mock.patch.object(module.signal,'pidfd_send_signal',create=True) as sent:
            with self.assertRaisesRegex(ValueError,'identity changed'):
                module.monitor(88,99,'/owned/viewer','d'*64,'e'*32,'kill',30)
            sent.assert_not_called();closed.assert_called_once_with(71)

    def test_default_validation_does_not_invoke_runtime_or_provider(self):
        source=(ROOT/'scripts/pc01-held-input-controller.py').read_text()
        self.assertNotIn('gh api',source)
        self.assertNotIn('requests.get',source)
        self.assertNotIn('urllib',source)
        self.assertIn("if not args.run:",source)
        self.assertIn("hostedArtifactGETs=0",source)
        self.assertIn("ordinary(output, absent=True)",source)


if __name__=='__main__':
    unittest.main(verbosity=2)
