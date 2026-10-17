#!/usr/bin/env python3
"""Real private child/file controls; API stand-ins do not qualify Windows tokens."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / 'scripts'
spec = importlib.util.spec_from_file_location('lease_ack_source', S / 'test-support-lease.py')
lease = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lease)
from rust_toolchain import rustc_command

STUBS = r'''
fn lease_main() {}
fn hold_support_invite_attended_lease()->bool { true }
fn support_invite_lease_status()->&'static str { "ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false" }
fn support_invite_attestation()->&'static str { "" }
fn support_invite_requires_click()->bool { false }
fn support_invite_owner_lease_failed()->bool { false }
const MIXEL_STANDARD_FIXTURE_EVENT:&str="private-ack-control";
mod standard_namespace_readonly { pub fn observe(_: &str)->Result<String,std::io::Error> { Ok("{}".to_owned()) } }
mod mixel_support_lease {
 pub fn fixture_observe_directory_access(_: &std::path::Path) {}
 pub fn fixture_observe_job_limits(_: &std::path::Path) {}
 pub fn fixture_enable_thread_tokens(_: &std::path::Path) {}
 pub fn fixture_flush_thread_tokens(_: &std::path::Path,_:bool) {}
 pub fn fixture_raw_open_error()->u32 { 2 }
 pub fn fixture_unnamed_create()->(&'static str,bool,u32,bool,u32) { ("close",true,0,true,0) }
}
'''


def run(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', timeout=30, **kwargs)
    if result.returncode:
        raise AssertionError(f'Actual control exit{result.returncode}: {argv}\n{result.stdout}\n{result.stderr}')
    return result


def wait_path(path, child=None):
    deadline = time.monotonic() + 10
    while not path.exists() and time.monotonic() < deadline:
        if child is not None and child.poll() is not None:
            raise AssertionError(f'Child exited{child.returncode} before {path.name}')
        time.sleep(.01)
    if not path.exists():
        raise AssertionError('Bounded owned file not published: ' + path.name)


class AckControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='mixel-actual-ack-')
        cls.root = Path(cls.temp.name)
        cls.pwsh = shutil.which('pwsh')
        if not cls.pwsh or not shutil.which('rustc'):
            raise RuntimeError('Actual PowerShell and Rust compiler are required; no skipped controls')
        body = lease.STANDARD_WINDOWS_HARNESS
        # Only OS guards are projected: all literal fixture lifecycle/ack code runs.
        body = body.replace('    #[cfg(windows)] {', '    {', 1).replace('    #[cfg(not(windows))]\n    panic!("This standard-account functional control requires actual Windows");', '')
        cls.child_source = STUBS + body
        source = cls.root / 'ack.rs'
        source.write_text(cls.child_source, encoding='utf-8')
        cls.binary = cls.root / ('child.exe' if os.name == 'nt' else 'child')
        run(rustc_command(['rustc', '--edition=2021', '--deny=warnings', str(source), '-o', str(cls.binary)]))
        cls.profiles = {}
        python_raw = (S / 'test-support-lease.py').read_bytes().replace(b'\r\n', b'\n')
        raw = (S / 'test-support-lease-standard-windows.ps1').read_bytes().replace(b'\r\n', b'\n')
        for profile in ('lf', 'crlf'):
            path = cls.root / (profile + '.ps1')
            path.write_bytes(raw if profile == 'lf' else raw.replace(b'\n', b'\r\n'))
            cls.profiles[profile] = path
            python_path = cls.root / (profile + '-lease.py')
            python_path.write_bytes(python_raw if profile == 'lf' else python_raw.replace(b'\n', b'\r\n'))
            profile_spec = importlib.util.spec_from_file_location('ack_' + profile, python_path)
            profile_module = importlib.util.module_from_spec(profile_spec)
            profile_spec.loader.exec_module(profile_module)
            if profile_module.STANDARD_WINDOWS_HARNESS != lease.STANDARD_WINDOWS_HARNESS:
                raise AssertionError('Physical Python LF/CRLF checkout changed the actual generated child body')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def powershell(self, body, profile='lf', timeout=30):
        script = self.root / ('control-' + str(time.monotonic_ns()) + '.ps1')
        prefix = r'''$ErrorActionPreference='Stop'
$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile('__SOURCE__',[ref]$t,[ref]$e)
if($e.Count){throw 'Actual source parser refused'}
foreach($name in @('Publish-StandardLeaseRelease','Write-StandardLeaseExitObservation')){
 $fn=@($a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name},$true))
 if($fn.Count -ne 1){throw 'Exact source function changed'}
 . ([scriptblock]::Create($fn[0].Extent.Text))
}
'''.replace('__SOURCE__', str(self.profiles[profile]).replace("'", "''"))
        script.write_text(prefix + body, encoding='utf-8-sig')
        return subprocess.Popen([self.pwsh, '-NoProfile', '-File', str(script)], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding='utf-8')

    def child(self, root):
        root.mkdir()
        binary = root / self.binary.name
        shutil.copyfile(self.binary, binary)
        binary.chmod(0o700)
        (root / 'fixture-mode.txt').write_text('probe')
        child = subprocess.Popen([str(binary)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        self.addCleanup(self.stop, child)
        wait_path(root / 'fixture-result.json', child)
        return child

    @staticmethod
    def stop(child):
        if child.poll() is None:
            child.terminate()
        child.communicate(timeout=10)

    def test_actual_closed_atomic_publication_parent_owned_ack_lf_crlf(self):
        for profile in self.profiles:
            root = self.root / ('atomic-' + profile)
            child = self.child(root)
            ready = root / 'publisher-ready'
            gate = root / 'publish-now'
            # Pause only immediately before the literal File.Move; real file APIs
            # and the source function's close/flush/order remain unchanged.
            body = r'''
$fixtureRoot='__ROOT__';$fn=(Get-Item Function:Publish-StandardLeaseRelease).Definition
$move='  [IO.File]::Move($partial,$releasePath)'
if(-not $fn.Contains($move)){throw 'Actual publish boundary changed'}
$pause="  [IO.File]::WriteAllText((Join-Path `$fixtureRoot 'publisher-ready'),'closed')`n  `$limit=[DateTime]::UtcNow.AddSeconds(10)`n  while(-not (Test-Path (Join-Path `$fixtureRoot 'publish-now')) -and [DateTime]::UtcNow -lt `$limit){Start-Sleep -Milliseconds 10}`n"
. ([scriptblock]::Create('function Publish-StandardLeaseRelease {'+$fn.Replace($move,$pause+$move)+'}'))
Publish-StandardLeaseRelease __PID__
'''.replace('__ROOT__', str(root).replace("'", "''")).replace('__PID__', str(child.pid))
            publisher = self.powershell(body, profile)
            self.addCleanup(self.stop, publisher)
            wait_path(ready, publisher)
            final = root / f'fixture-release-{child.pid}'
            self.assertFalse(final.exists())
            self.assertEqual(Path(str(final) + '.new').read_bytes(), b'verified')
            self.assertIsNone(child.poll())
            gate.write_text('go')
            stdout, stderr = publisher.communicate(timeout=15)
            self.assertEqual(publisher.returncode, 0, stdout + stderr)
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stdout + stderr)
            self.assertEqual(final.read_bytes(), b'verified')  # Child never deletes it.
            lifecycle = json.loads((root / f'fixture-lifecycle-{child.pid}.json').read_text())
            self.assertEqual(lifecycle, {'schema': 1, 'pid': child.pid, 'phase': 7, 'message': ''})
            final.unlink()  # Parent cleanup only after actual child exit.
        print('PASS: actual source publisher flushes/closes before final-name visibility; real child waits then exits0; parent retains/cleans acknowledgment in physical LF+CRLF.')

    def test_actual_child_wrong_ack_retains_panic_phase_and_nonzero(self):
        root = self.root / 'wrong-ack'
        child = self.child(root)
        path = root / f'fixture-release-{child.pid}'
        path.write_text('wrong-ack')
        stdout, stderr = child.communicate(timeout=10)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn('Owned acknowledgment bytes differ', stderr)
        record = json.loads((root / f'fixture-lifecycle-{child.pid}.json').read_text())
        self.assertEqual(record['pid'], child.pid)
        self.assertEqual(record['phase'], 5)
        self.assertIn('Owned acknowledgment bytes differ', record['message'])
        self.assertLessEqual(len(record['message']), 1024)
        self.assertTrue(path.exists())
        print('PASS: real child rejects wrong acknowledgment with nonzero exit and retained exact PID/phase/panic; no failure-as-success.')


    @unittest.skipUnless(os.name == 'nt', 'Actual Windows open-writer sharing requires hosted Windows')
    def test_real_windows_original_delete_race_is_reproduced_and_closed_publication_passes(self):
        root = self.root / 'windows-original'
        root.mkdir()
        # Restore only the original unsafe acknowledgment consumption statement.
        # All source lifecycle instrumentation remains to retain the actual error.
        start = self.child_source.index('            PHASE.store(5,')
        end = self.child_source.index('            lifecycle(&directory, 7, "");', start)
        end += len('            lifecycle(&directory, 7, "");')
        original = self.child_source[:start] + '            std::fs::remove_file(release).unwrap();' + self.child_source[end:]
        source = root / 'original-ack.rs'
        source.write_text(original, encoding='utf-8')
        binary = root / 'original-child.exe'
        run(rustc_command(['rustc', '--edition=2021', '--deny=warnings', str(source), '-o', str(binary)]))
        (root / 'fixture-mode.txt').write_text('probe')
        child = subprocess.Popen([str(binary)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        self.addCleanup(self.stop, child)
        wait_path(root / 'fixture-result.json', child)
        body = r"""
$fixtureRoot='__ROOT__'
$path=Join-Path $fixtureRoot 'fixture-release-__PID__'
$stream=[IO.File]::Open($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
try {
 $bytes=[Text.UTF8Encoding]::new($false).GetBytes('verified');$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)
 [IO.File]::WriteAllText((Join-Path $fixtureRoot 'writer-open'),'open')
 $limit=[DateTime]::UtcNow.AddSeconds(10)
 while(-not(Test-Path (Join-Path $fixtureRoot 'release-writer')) -and [DateTime]::UtcNow -lt $limit){Start-Sleep -Milliseconds 10}
} finally {$stream.Dispose()}
""".replace('__ROOT__', str(root).replace("'", "''")).replace('__PID__', str(child.pid))
        writer = self.powershell(body)
        self.addCleanup(self.stop, writer)
        wait_path(root / 'writer-open', writer)
        stdout, stderr = child.communicate(timeout=10)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn('code: 32', stderr)
        record = json.loads((root / f'fixture-lifecycle-{child.pid}.json').read_text())
        self.assertIn('code: 32', record['message'])
        (root / 'release-writer').write_text('go')
        stdout, stderr = writer.communicate(timeout=15)
        self.assertEqual(writer.returncode, 0, stdout + stderr)
        print('PASS: actual Windows open writer reproduces original child-delete sharing error32/nonzero; corrected closed atomic publication is exercised separately for both physical newline profiles.')

    def test_actual_publisher_refuses_existing_final_and_partial(self):
        for profile in self.profiles:
            for existing in ('final', 'partial'):
                root = self.root / (profile + '-' + existing)
                root.mkdir()
                final = root / 'fixture-release-4321'
                occupied = final if existing == 'final' else Path(str(final) + '.new')
                occupied.write_bytes(b'unrelated-owned-sentinel')
                p = self.powershell("$fixtureRoot='" + str(root).replace("'", "''") + "';Publish-StandardLeaseRelease 4321", profile)
                stdout, stderr = p.communicate(timeout=15)
                self.assertNotEqual(p.returncode, 0)
                self.assertEqual(occupied.read_bytes(), b'unrelated-owned-sentinel')
        print('PASS: actual CreateNew/Move refuse existing acknowledgment or partial bytes without overwrite in LF+CRLF.')

    def test_actual_observer_preserves_status_and_rejects_foreign_or_unbounded_phase(self):
        for profile in self.profiles:
            root = self.root / ('observer-' + profile)
            root.mkdir()
            body = r'''
$fixtureRoot='__ROOT__'
Add-Type 'public static class MixelOrdinaryTokenFixture {public static string Status="exited:0x00000065"; public static string StartedStatus(int pid){if(pid!=4321)throw new System.Exception("unowned");return Status;}}'
$path=Join-Path $fixtureRoot 'fixture-lifecycle-4321.json'
foreach($case in @('valid','foreign','oversize','malformed')){
 if($case -ceq 'valid'){$text='{"schema":1,"pid":4321,"phase":5,"message":"native failure code: 32"}'}
 elseif($case -ceq 'foreign'){$text='{"schema":1,"pid":4322,"phase":5,"message":"foreign"}'}
 elseif($case -ceq 'oversize'){$text='x'*4097}else{$text='{malformed'}
 [IO.File]::WriteAllText($path,$text)
 $status=Write-StandardLeaseExitObservation 4321 $case
 if($status -cne 'exited:0x00000065'){throw 'Original native nonzero status was replaced'}
}
'''.replace('__ROOT__', str(root).replace("'", "''"))
            p = self.powershell(body, profile)
            stdout, stderr = p.communicate(timeout=15)
            self.assertEqual(p.returncode, 0, stdout + stderr)
            rows = [json.loads(line.split('; ', 1)[1]) for line in stdout.splitlines() if line.startswith('OBSERVED: exact owned')]
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]['diagnostic'], 'available')
            self.assertEqual(rows[0]['lifecycle']['message'], 'native failure code: 32')
            self.assertTrue(all(row['nativeStatus'] == 'exited:0x00000065' for row in rows))
            self.assertTrue(all(row['diagnostic'] == 'invalid-or-unreadable' for row in rows[1:]))
        print('PASS: literal observer retains native nonzero plus exact child phase/error before cleanup; wrong PID/malformed/oversize never become a valid diagnostic (retained-handle API stand-in explicit).')


if __name__ == '__main__':
    unittest.main(verbosity=2)
