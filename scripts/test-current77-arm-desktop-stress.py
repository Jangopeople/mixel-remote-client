#!/usr/bin/env python3
"""Pure/file/mock stress controls; never run a native app or provider request."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v = load("desktop_stress_carrier", ROOT / "scripts/diagnose-current77-arm-desktop-stress.py")
r = v.load_remaining(ROOT)
b = r.load_base(ROOT)
projection = load("desktop_stress_projection", ROOT / v.PROJECTION)


def observed():
    return json.loads((ROOT / b.OBSERVED_PROFILE).read_bytes())


class StressControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="current77-stress-pure-")
        self.root = Path(self.temp.name).resolve()
        self.no_run = patch.object(subprocess, "run", side_effect=AssertionError("No provider/native process in pure controls"))
        self.no_popen = patch.object(subprocess, "Popen", side_effect=AssertionError("No provider/native process in pure controls"))
        self.no_run.start(); self.no_popen.start()
    def tearDown(self):
        self.no_popen.stop(); self.no_run.stop(); self.temp.cleanup()
    def gate(self):
        files = set(r.CURRENT_FILES) | {v.REMAINING, r.CONTROLS, r.PROJECTION,
                                      v.SELF, v.CONTROLS, v.PROJECTION}
        for name in files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, path)
        gate = {"schema": 1, "scope": v.SCOPE, "producerSource": b.SOURCE,
            "producerTree": b.TREE, "producerRun": b.RUN, "producerAttempt": 1,
            "failedJob": b.JOB, "privateReplayAllowed": True, "iterationCount": 10,
            "stopOnFirstFailure": True, "customerAppQualified": False, "screenControlQualified": False,
            "releaseQualified": False, "publication": False, "privateDmgAllowed": False,
            "artifact": b.ARTIFACT, "nestedAppSha256": b.NESTED_SHA,
            "observedOriginals": observed(), "observedAdmission": b.validate_records(observed()),
            "reviewedHarnessFiles": {name: b.sha((self.root / name).read_bytes()) for name in files},
            "syntheticUnitFixtureOnly": True}
        path = self.root / v.GATE
        path.parent.mkdir(); path.write_text(json.dumps(gate))
        return path, gate
    def git(self, argv, *args, **kwargs):
        self.assertEqual(argv[:4], ["git", "-C", str(self.root), "show"])
        self.assertTrue(argv[4].startswith("HEAD:"))
        return (self.root / argv[4][5:]).read_bytes()
    def test_exact_frozen_carriers_and_workflow_inverse(self):
        self.assertEqual(b.sha((ROOT / v.REMAINING).read_bytes()), v.REMAINING_SHA)
        self.assertEqual(b.sha((ROOT / r.BASE).read_bytes()), r.BASE_SHA)
        current = (ROOT / b.WORKFLOW).read_text()
        parent = projection.current_workflow(current)
        self.assertEqual(b.sha(parent.encode()), v.PARENT_WORKFLOW_SHA)
        self.assertEqual(b.sha(load("stress_old_projection", ROOT / r.PROJECTION).current_workflow(parent).encode()),
                         r.CURRENT_FILES[b.WORKFLOW])
        with self.assertRaises(ValueError):
            projection.current_workflow(current + "\n# drift\n")
    def test_exact_synthetic_gate_admitted_and_native_claims_false(self):
        path, gate = self.gate()
        with patch.object(b, "capture", side_effect=self.git):
            self.assertEqual(v.verify_gate(b, r, self.root, path), gate)
        for key in ("customerAppQualified", "screenControlQualified", "releaseQualified", "publication"):
            self.assertIs(gate[key], False)
    def test_gate_count_scope_privilege_and_source_mutants_refused(self):
        path, gate = self.gate()
        for key, value in (("iterationCount", 9), ("iterationCount", True),
                           ("stopOnFirstFailure", False), ("scope", "normal-release"),
                           ("customerAppQualified", True), ("screenControlQualified", True),
                           ("publication", True), ("privateDmgAllowed", True), ("producerRun", 1979)):
            with self.subTest(key=key, value=value):
                bad = copy.deepcopy(gate); bad[key] = value; path.write_text(json.dumps(bad))
                with patch.object(b, "capture", side_effect=self.git), self.assertRaises(b.Refusal):
                    v.verify_gate(b, r, self.root, path)
        path.write_text(json.dumps(gate))
        (self.root / v.REMAINING).write_text("changed")
        with patch.object(b, "capture", side_effect=self.git), self.assertRaises(b.Refusal):
            v.verify_gate(b, r, self.root, path)
    def test_missing_gate_stops_before_git_provider_or_native(self):
        with patch.object(b, "capture", side_effect=AssertionError("No command allowed")), self.assertRaises(b.Refusal):
            v.verify_gate(b, r, self.root, self.root / v.GATE)
    def test_hosted_uses_only_new_branch_and_restores_base(self):
        before = b.QA_BRANCH
        def inspect(diagnostic, producer):
            self.assertEqual(b.QA_BRANCH, v.BRANCH)
            raise b.Refusal("synthetic host refusal")
        with patch.object(b, "hosted_context", side_effect=inspect), self.assertRaises(b.Refusal):
            v.hosted(b, self.root, self.root)
        self.assertEqual(b.QA_BRANCH, before)
    def test_credentials_absent_only_inside_native_run_even_on_failure(self):
        values = {"GH_TOKEN": "synthetic-gh", "GITHUB_TOKEN": "synthetic-github",
                  "APPLE_CERT": "synthetic-apple", "KEEP_VISIBLE": "fixture"}
        with patch.dict(os.environ, values):
            with self.assertRaises(RuntimeError):
                with v.native_environment():
                    self.assertFalse(any(key in os.environ for key in values if key != "KEEP_VISIBLE"))
                    self.assertEqual(os.environ["KEEP_VISIBLE"], "fixture")
                    raise RuntimeError("fixture")
            for key, value in values.items():
                self.assertEqual(os.environ[key], value)
    def session(self, code=0):
        app, pid = self.root / "Mixel-Remote.app", 321
        raw = {"diagnostic_only": True, "qualifies_application": False,
            "original_methods_restored": True, "errors": [], "observers": [{
            "diagnostic_only": True, "qualifies_application": False, "state": "exit_observed",
            "identity": {"pid": pid, "uid": os.geteuid(), "birth_seconds": 100,
                "birth_microseconds": 2, "executable": str(app / "Contents/MacOS/Mixel-Remote"),
                "bundle_path": str(app), "bundle_identifier": "ch.mixel.remote", "bundle_version": "64"},
            "requested_notes": 0x86000000, "raw_events": [{"ident": pid, "filter": -5,
                "flags": 0, "fflags": 0x84000000, "data": 15, "udata": 42}],
            "decoded_exit": {"exit_status_available": True, "exit_detail_available": False,
                "kind": "signaled", "signal": 15}, "error": None,
            "first_exit_observation": {"sampling_stage": "after-request", "current_mark": None,
                "phase": "ordinary-to-warm-owned-cleanup", "utc": "2026-10-15T18:31:16.361201+00:00",
                "elapsed_seconds": 1.92, "meaning": "first observed exit; phase is not proof of the cause"}}]}
        runtime = {"schema": 1, "scope": "macos-launch-smoke-diagnostic-not-customer-release",
            "privacy_check_passed": True, "customer_app_qualified": False, "screen_control_qualified": False,
            "publication": False, "outcome": "success" if code == 0 else "failure",
            "bundle_path": str(app), "executable_path": str(app / "Contents/MacOS/Mixel-Remote"),
            "executable_sha256": b.EXECUTABLE_SHA,
            "events": [{"event": "owned-ordinary-application-launched", "pid": pid}],
            "app_log_deltas": [], "reports": []}
        return raw, runtime, app
    def raw_fixture(self, code=0):
        private, safe = b.new_directory(self.root / "private"), b.new_directory(self.root / "safe")
        runtime_dir = b.new_directory(private / "runtime")
        observer, runtime, app = self.session(code)
        (runtime_dir / "runtime.json").write_text(json.dumps(runtime))
        (private / "exit-observer-private.json").write_text(json.dumps(observer))
        (private / "desktop.stdout").write_bytes(b"")
        (private / "desktop.stderr").write_bytes(b"synthetic stderr\n")
        return private, safe, runtime, app
    def desktop(self):
        return b.module(ROOT / "scripts/test-support-launch-macos.py", b.FROZEN["scripts/test-support-launch-macos.py"])
    def test_matching_originals_exact_empty_output_and_no_app_in_archive(self):
        private, safe, runtime, app = self.raw_fixture()
        log, crash = b"synthetic log\n", b'{"synthetic":"crash"}\n'
        (private / "runtime/owned-app-log-1.log").write_bytes(log)
        (private / "runtime/owned-crash-1.json").write_bytes(crash)
        runtime["app_log_deltas"] = [{"retained_file": "owned-app-log-1.log", "bytes": len(log), "sha256": b.sha(log)}]
        runtime["reports"] = [{"retained_file": "owned-crash-1.json", "retained_sha256": b.sha(crash)}]
        (private / "runtime/runtime.json").write_text(json.dumps(runtime))
        manifest = v.retain_originals(b, self.desktop(), private, safe, app, 0)
        self.assertTrue(manifest["contentRetained"]); self.assertFalse(manifest["signedAppIncluded"])
        self.assertFalse(manifest["rawCrashReportBytesIncluded"])
        self.assertEqual(manifest["crashPayloadKind"], "exact-frozen-privacy-filtered-diagnostic")
        with zipfile.ZipFile(safe / "diagnostic-originals.zip") as archive:
            self.assertEqual(set(archive.namelist()), set(manifest["files"]))
            self.assertEqual(archive.read("desktop.stdout"), b"")
            self.assertEqual(archive.read("runtime/owned-app-log-1.log"), log)
            self.assertEqual(archive.read("runtime/owned-crash-1.json"), crash)
            self.assertFalse(any(name.endswith(('.app', '.zip', '.dmg')) for name in archive.namelist()))
    def test_runtime_privacy_failure_withholds_content_preserves_hashes_and_code(self):
        private, safe, runtime, app = self.raw_fixture(1)
        runtime["privacy_check_passed"] = False
        (private / "runtime/runtime.json").write_text(json.dumps(runtime))
        with self.assertRaises(b.Refusal):
            v.retain_originals(b, self.desktop(), private, safe, app, 1)
        manifest = json.loads((safe / "diagnostic-originals-manifest.json").read_bytes())
        self.assertFalse(manifest["contentRetained"]); self.assertTrue(manifest["withheld"])
        self.assertEqual(manifest["originalExitCode"], 1)
        self.assertEqual(manifest["files"]["desktop.stderr"]["sha256"], b.sha(b"synthetic stderr\n"))
        self.assertFalse((safe / "diagnostic-originals.zip").exists())
    def test_content_privacy_pattern_is_withheld_even_when_runtime_flag_true(self):
        private, safe, runtime, app = self.raw_fixture(1)
        (private / "desktop.stderr").write_bytes(b"Authorization: Bearer secret-synthetic-token\n")
        with self.assertRaises(b.Refusal):
            v.retain_originals(b, self.desktop(), private, safe, app, 1)
        self.assertFalse((safe / "diagnostic-originals.zip").exists())
        self.assertNotIn('secret-synthetic', (safe / "diagnostic-originals-manifest.json").read_text())
    def test_unreferenced_log_or_app_archive_never_retained(self):
        private, safe, runtime, app = self.raw_fixture()
        (private / "runtime/owned-app-log-2.log").write_bytes(b"unreferenced")
        with self.assertRaises(b.Refusal):
            v.retain_originals(b, self.desktop(), private, safe, app, 0)
        self.assertFalse((safe / "diagnostic-originals.zip").exists())
    def test_symlink_hardlink_and_oversized_original_refused(self):
        for kind in ("symlink", "hardlink", "oversized"):
            with self.subTest(kind=kind):
                path = self.root / kind
                if kind == "symlink": path.symlink_to(ROOT / v.SELF)
                elif kind == "hardlink":
                    original = self.root / 'hardlink-original'; original.write_bytes(b'x'); os.link(original, path)
                else: path.write_bytes(b'xx')
                with self.assertRaises(b.Refusal): v.bounded_original(b, path, 1)
    def exercise(self, failed_at=None, observation_failure=False, retention_failure=False, admission_failure=False, invocation_error=None):
        private, safe = b.new_directory(self.root / "stress-private"), b.new_directory(self.root / "stress-safe")
        calls, receipt = [], {}
        concurrency = types.SimpleNamespace(desktop=object(), tray=object())
        def original(*args):
            self.assertNotIn("GH_TOKEN", os.environ); self.assertNotIn("APPLE_CERT", os.environ)
            scoped, iteration = args[-2], args[-1]
            self.assertEqual(scoped.parent, iteration)
            self.assertEqual(scoped.name, 'signed-stapled-diagnostic-app.zip')
            self.assertNotEqual(scoped, self.root/'nested')
            self.assertEqual(scoped.read_bytes(), b'synthetic nested archive')
            self.assertEqual(scoped.lstat().st_nlink, 1)
            number = len(calls) + 1; calls.append(number)
            if invocation_error is not None:
                (iteration/'desktop.stdout').write_bytes(b'partial original stdout')
                raise invocation_error
            return (1 if number == failed_at else 0), {"stdout": {"bytes": 0, "sha256": b.sha(b'')}}
        def retain(*args):
            if observation_failure: raise ValueError('observation fixture')
        def raw(*args):
            if retention_failure: raise OSError('retention fixture')
            return {"contentRetained": True}
        def admission(*args):
            if admission_failure: raise RuntimeError('admission fixture')
        remaining = types.SimpleNamespace(original_desktop=original, post_desktop_preconditions=admission)
        (self.root/'nested').write_bytes(b'synthetic nested archive')
        error = None
        with patch.object(b,'NESTED_BYTES',len(b'synthetic nested archive')), patch.object(b,'NESTED_SHA',b.sha(b'synthetic nested archive')), \
             patch.object(b, "retain_observation", side_effect=retain), patch.object(v, "retain_originals", side_effect=raw), \
             patch.dict(os.environ, {"GH_TOKEN": "synthetic", "APPLE_CERT": "synthetic"}):
            try:
                v.run_stress(b, remaining, object(), concurrency, object(), self.root/'app', self.root/'nested', private, safe, receipt)
            except Exception as caught: error = caught
            self.assertEqual(os.environ["GH_TOKEN"], "synthetic")
        return calls, receipt, error, safe
    def test_exactly_ten_sequential_original_calls_and_exclusive_iteration_outputs(self):
        calls, receipt, error, safe = self.exercise()
        self.assertIsNone(error); self.assertEqual(calls, list(range(1, 11)))
        self.assertTrue(receipt["allTenOriginalDesktopPassed"])
        self.assertEqual(receipt["completedIterations"], 10)
        self.assertEqual(sorted(path.name for path in safe.iterdir()), ['iteration-'+str(i).zfill(2) for i in range(1,11)])
        self.assertTrue(all(row["passed"] and not row["customerAppQualified"] for row in receipt["iterations"]))
    def test_original_failure_stops_first_and_preserves_actual_exit_code(self):
        calls, receipt, error, safe = self.exercise(failed_at=3)
        self.assertIsNotNone(error); self.assertEqual(calls, [1,2,3])
        self.assertEqual(receipt["firstFailedIteration"], 3); self.assertEqual(receipt["completedIterations"], 2)
        self.assertEqual(receipt["iterations"][-1]["originalExitCode"], 1)
        self.assertFalse(receipt["allTenOriginalDesktopPassed"])
    def test_observation_retention_and_admission_failure_never_retry(self):
        for field in ('observation_failure', 'retention_failure', 'admission_failure'):
            with self.subTest(field=field), tempfile.TemporaryDirectory(prefix='stress-stop-') as directory:
                old = self.root; self.root = Path(directory).resolve()
                try: calls, receipt, error, safe = self.exercise(**{field: True})
                finally: self.root = old
                self.assertIsNotNone(error); self.assertEqual(calls, [1])
                self.assertEqual(receipt['iterations'][0]['originalExitCode'], 0)
                self.assertEqual(receipt['firstFailedIteration'], 1)
    def test_original_failure_survives_both_diagnostic_failures(self):
        calls, receipt, error, safe = self.exercise(failed_at=1, observation_failure=True, retention_failure=True)
        self.assertEqual(calls, [1]); self.assertIsInstance(error, ValueError)
        row = receipt['iterations'][0]
        self.assertEqual(row['originalExitCode'], 1)
        self.assertEqual(row['observationFailureType'], 'ValueError')
        self.assertEqual(row['originalRetentionFailureType'], 'OSError')
    def test_final_metadata_rejection_preserves_actual_state_and_fails_private_receipt(self):
        support = types.SimpleNamespace(CRITICAL=(), verify_signatures=Mock())
        fake = types.SimpleNamespace(**{name: getattr(b, name) for name in dir(b) if not name.startswith('__')})
        records, bad = observed(), observed(); bad['producer']['head_sha'] = 'b'*40
        fake.module = Mock(return_value=support)
        fake.fresh_records = Mock(side_effect=[records, records, bad]); fake.fresh_fixture = Mock()
        frozen_r = types.SimpleNamespace(load_base=lambda root: fake, parser_for_app=Mock(return_value=object()))
        def prepare(*args): args[-1]['criticalHashes'] = {}; return self.root/'app', self.root/'nested'
        frozen_r.prepare_app = prepare
        gate_path = self.root/'synthetic-gate'
        def read(path, *args): return b'unit gate' if path == gate_path else b.read(path,*args)
        with patch.object(v,'load_remaining',return_value=frozen_r), patch.object(v,'verify_gate',return_value={'reviewedHarnessFiles':{}}), \
             patch.object(v,'hosted',return_value='b'*40), patch.object(v,'run_stress'), patch.object(fake,'read',side_effect=read), \
             patch.dict(os.environ,{'GITHUB_RUN_ID':'123'}):
            code = v.main(['--diagnostic-checkout',str(ROOT),'--producer-checkout',str(ROOT),'--gate',str(gate_path),
                           '--destination',str(self.root/'private'),'--safe-output',str(self.root/'safe')])
        receipt = json.loads((self.root/'safe/stress-validation-receipt.json').read_bytes())
        self.assertEqual(code,1); self.assertFalse(receipt['privateDiagnosticCompleted'])
        self.assertFalse(receipt['afterAdmissionValidationPassed'])
        self.assertEqual(receipt['observedAfter']['producer']['headSha'],'b'*40)
        self.assertFalse(receipt['releaseQualified'])


    def test_same_named_path_replacement_after_fd_read_is_refused(self):
        directory = self.root/'replace-parent'; directory.mkdir()
        path = directory/'file'; path.write_bytes(b'original')
        original_fstat = os.fstat
        calls = []
        def change(fd):
            info = original_fstat(fd); calls.append(fd)
            if len(calls) == 2:
                path.unlink(); path.write_bytes(b'original')
            return info
        with patch.object(v.os,'fstat',side_effect=change), self.assertRaises(b.Refusal):
            v.bounded_original(b,path,64)
    def test_parent_replacement_after_fd_read_is_refused(self):
        directory = self.root/'replace-parent'; directory.mkdir()
        path = directory/'file'; path.write_bytes(b'original')
        original_fstat = os.fstat
        calls = []
        def change(fd):
            info = original_fstat(fd); calls.append(fd)
            if len(calls) == 2:
                directory.rename(self.root/'previous-parent')
                directory.mkdir(); path.write_bytes(b'original')
            return info
        with patch.object(v.os,'fstat',side_effect=change), self.assertRaises(b.Refusal):
            v.bounded_original(b,path,64)
    def test_zip_write_failure_leaves_only_hidden_withheld_partial(self):
        private, safe, runtime, app = self.raw_fixture(1)
        with patch.object(zipfile.ZipFile,'writestr',side_effect=OSError('synthetic write failure')), self.assertRaises(OSError):
            v.retain_originals(b,self.desktop(),private,safe,app,1)
        self.assertFalse((safe/'diagnostic-originals.zip').exists())
        self.assertTrue((safe/'.diagnostic-originals.zip.partial').exists())
        manifest = json.loads((safe/'diagnostic-originals-manifest.json').read_bytes())
        self.assertTrue(manifest['withheld']); self.assertFalse(manifest['contentRetained'])
        self.assertTrue(manifest['partialArchiveWithheld'])
        self.assertIn('sha256',manifest['partialArchive'])
    def test_existing_final_archive_is_never_replaced_or_deleted(self):
        private, safe, runtime, app = self.raw_fixture()
        final = safe/'diagnostic-originals.zip'; final.write_bytes(b'foreign existing archive')
        with self.assertRaises(FileExistsError):
            v.retain_originals(b,self.desktop(),private,safe,app,0)
        self.assertEqual(final.read_bytes(),b'foreign existing archive')
        manifest = json.loads((safe/'diagnostic-originals-manifest.json').read_bytes())
        self.assertTrue(manifest['withheld']); self.assertFalse(manifest['contentRetained'])
    def test_scoped_archive_wrong_size_or_digest_refused_before_original(self):
        nested = self.root/'nested'; nested.write_bytes(b'synthetic nested archive')
        private = b.new_directory(self.root/'iteration')
        with self.assertRaises(b.Refusal): v.scoped_archive(b,nested,private)
        self.assertFalse((private/'signed-stapled-diagnostic-app.zip').exists())


    def test_raised_invocation_withholds_content_preserves_none_code_and_same_exception(self):
        original = RuntimeError('synthetic original invocation failure')
        calls, receipt, error, safe = self.exercise(invocation_error=original)
        self.assertEqual(calls,[1]); self.assertIs(error,original)
        row = receipt['iterations'][0]
        self.assertIsNone(row['originalExitCode']); self.assertIs(row['invocationReturned'],False)
        manifest = json.loads((safe/'iteration-01/diagnostic-originals-manifest.json').read_bytes())
        self.assertIsNone(manifest['originalExitCode']); self.assertFalse(manifest['invocationReturned'])
        self.assertFalse(manifest['contentRetained']); self.assertTrue(manifest['withheld'])
        self.assertEqual(manifest['invocationFailureType'],'RuntimeError')
        self.assertEqual(manifest['invocationFailureSha256'],b.sha(str(original).encode()))
        self.assertEqual(manifest['files']['desktop.stdout']['sha256'],b.sha(b'partial original stdout'))
        self.assertFalse((safe/'iteration-01/diagnostic-originals.zip').exists())
    def test_withholding_manifest_failure_cannot_mask_invocation_exception(self):
        original = ValueError('synthetic original')
        with patch.object(v,'invocation_withheld',side_effect=OSError('synthetic manifest failure')):
            calls, receipt, error, safe = self.exercise(invocation_error=original)
        self.assertIs(error,original); self.assertEqual(calls,[1])
        self.assertEqual(receipt['iterations'][0]['withholdingManifestFailureType'],'OSError')


    def test_late_final_validation_failure_withdraws_exact_owned_upload_name(self):
        private, safe, runtime, app = self.raw_fixture(1)
        original_read = v.bounded_original
        def fail_final(base, path, maximum):
            if path == safe/'diagnostic-originals.zip':
                raise b.Refusal('synthetic final validation refusal')
            return original_read(base,path,maximum)
        with patch.object(v,'bounded_original',side_effect=fail_final), self.assertRaises(b.Refusal):
            v.retain_originals(b,self.desktop(),private,safe,app,1)
        self.assertFalse((safe/'diagnostic-originals.zip').exists())
        manifest = json.loads((safe/'diagnostic-originals-manifest.json').read_bytes())
        self.assertFalse(manifest['contentRetained']); self.assertTrue(manifest['withheld'])
        self.assertTrue(manifest['invalidOwnedFinalRemoved']); self.assertNotIn('archive',manifest)
        self.assertEqual(manifest['originalExitCode'],1)
    def test_late_refusal_never_deletes_replacement_final_inode(self):
        private, safe, runtime, app = self.raw_fixture(1)
        original_read = v.bounded_original
        def replace_final(base,path,maximum):
            if path == safe/'diagnostic-originals.zip':
                path.rename(safe/'.owned-final-moved-aside')
                path.write_bytes(b'foreign replacement')
                raise b.Refusal('synthetic replaced final refusal')
            return original_read(base,path,maximum)
        with patch.object(v,'bounded_original',side_effect=replace_final), self.assertRaises(b.Refusal):
            v.retain_originals(b,self.desktop(),private,safe,app,1)
        self.assertEqual((safe/'diagnostic-originals.zip').read_bytes(),b'foreign replacement')
        manifest = json.loads((safe/'diagnostic-originals-manifest.json').read_bytes())
        self.assertFalse(manifest['contentRetained']); self.assertTrue(manifest['withheld'])
        self.assertTrue(manifest['finalReplacementNotTouched'])


def legacy_controls():
    """Project only the exact new workflow in an owned minimal source copy."""
    with tempfile.TemporaryDirectory(prefix="current77-stress-legacy-source-") as directory:
        source = Path(directory) / "source"
        shutil.copytree(ROOT / "scripts", source / "scripts", ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT / ".github/workflows", source / ".github/workflows")
        if (ROOT / 'verification').is_dir(): shutil.copytree(ROOT / 'verification', source / 'verification')
        workflow = source / b.WORKFLOW
        workflow.write_text(projection.current_workflow(workflow.read_text()))
        subprocess.run([sys.executable, '-B', str(source / r.CONTROLS), '--with-legacy-controls'],
                       cwd=source, check=True, timeout=600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-legacy-controls', action='store_true')
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(StressControls))
    if not result.wasSuccessful(): return 1
    if args.with_legacy_controls: legacy_controls()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
