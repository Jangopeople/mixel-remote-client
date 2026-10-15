#!/usr/bin/env python3
"""Pure/file/mock controls, with exact c747 projection for unchanged legacy tests.

These controls never launch an app, invoke a native API, contact a provider or
create a genuine admission gate. Synthetic gates live only in temporary roots.
"""
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v = load("remaining_arm_carrier", ROOT / "scripts/diagnose-current77-arm-remaining-gates.py")
projection = load("remaining_arm_projection", ROOT / v.PROJECTION)
b = v.load_base(ROOT)


def observed():
    return json.loads((ROOT / b.OBSERVED_PROFILE).read_bytes())


class RemainingGateControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="current77-remaining-pure-")
        self.root = Path(self.temp.name).resolve()
        self.no_run = patch.object(subprocess, "run", side_effect=AssertionError("No provider/native process in pure controls"))
        self.no_popen = patch.object(subprocess, "Popen", side_effect=AssertionError("No provider/native process in pure controls"))
        self.no_run.start(); self.no_popen.start()

    def tearDown(self):
        self.no_popen.stop(); self.no_run.stop(); self.temp.cleanup()

    def fixture(self):
        files = set(v.CURRENT_FILES) | {v.SELF, v.CONTROLS, v.PROJECTION}
        for name in files:
            p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, p)
        gate = {"schema": 1, "scope": v.SCOPE, "producerSource": b.SOURCE, "producerTree": b.TREE,
            "producerRun": b.RUN, "producerAttempt": 1, "failedJob": b.JOB, "privateReplayAllowed": True,
            "customerAppQualified": False, "releaseQualified": False, "publication": False,
            "privateDmgAllowed": False, "artifact": b.ARTIFACT, "nestedAppSha256": b.NESTED_SHA,
            "observedOriginals": observed(), "observedAdmission": b.validate_records(observed()),
            "reviewedHarnessFiles": {n: b.sha((self.root / n).read_bytes()) for n in files},
            "syntheticUnitFixtureOnly": True}
        p = self.root / v.GATE; p.parent.mkdir(); p.write_text(json.dumps(gate))
        return p, gate

    def git(self, argv, *unused, **kwargs):
        self.assertEqual(argv[:4], ["git", "-C", str(self.root), "show"])
        self.assertTrue(argv[4].startswith("HEAD:"))
        return (self.root / argv[4][5:]).read_bytes()

    def parser(self):
        support = b.module(ROOT / "scripts/diagnose-macos-failed-app.py", b.FROZEN["scripts/diagnose-macos-failed-app.py"])
        critical = {name: (str(i + 1) * 64) for i, name in enumerate(support.CRITICAL)}
        parser = v.parser_for_app(b, support, ROOT, critical)
        return support, parser

    def native_facts(self, parser):
        tray = {"observedUtc": "2026-10-15T20:00:00+00:00", "syntheticInvite": True,
            "customerAcceptTested": False, "remoteDesktopConnected": False,
            "actualTrayDispatchObserved": True, "visibleGuiAndAttendedV2": True,
            "ownedProcessesStopped": True, "passed": True, "criticalHashes": parser.CRITICAL_HASHES}
        facts = {"observedUtc": "2026-10-15T20:00:00+00:00", "syntheticInvite": True,
            "customerAcceptTested": False, "remoteDesktopConnected": False,
            "invitePayloadDeliveryTested": False, "coldDirectLaunchConcurrencyTested": False,
            "passed": True, "scenarios": [{"scenario": name, "passed": True,
                "ownedProcessesStopped": True, "criticalHashes": dict(parser.CRITICAL_HASHES),
                "trayPid": 101, "guiPid": 202, "inventorySamples": 15, "stabilitySamples": 13,
                "stabilitySecondsRequired": 5.0, "completionCount": 1 if name == "batch-two-support" else 2,
                "incomingEndpointSettlement": {"state": "not-required", "leaseCapturedBeforeStop": True,
                    "maximumMs": 5000, "attempts": 0, "elapsedMs": 0, "failureCode": "none"},
                "completionsMatchOriginalTray": True, "actualTrayDispatchObserved": True,
                "visibleGuiAndAttendedV2": True, "oneRetainedGuiDuringBoundedStability": True}
                for name in parser.SCENARIOS]}
        return tray, facts

    def test_c747_sources_and_all_product_helpers_remain_exact(self):
        for name, expected in v.CURRENT_FILES.items():
            raw = (ROOT / name).read_bytes()
            if name == b.WORKFLOW: raw = projection.current_workflow(raw.decode()).encode()
            self.assertEqual(b.sha(raw), expected, name)
        for name, expected in {**b.FROZEN, v.PARSER: v.PARSER_SHA}.items():
            self.assertEqual(b.sha((ROOT / name).read_bytes()), expected, name)

    def test_exact_workflow_inverse_refuses_any_other_drift(self):
        text = (ROOT / b.WORKFLOW).read_text()
        base = projection.current_workflow(text)
        self.assertEqual(b.sha(base.encode()), projection.BASE_SHA)
        self.assertEqual(projection.current_workflow(base), base)
        for bad in (text + "# drift\n", text.replace("macos-14", "macos-15", 1),
                    text.replace("--with-legacy-controls", "", 1), text + projection.NEW_JOB,
                    text.replace("retention-days: 7", "retention-days: 6", 1)):
            with self.subTest(sha=b.sha(bad.encode())), self.assertRaises(ValueError): projection.current_workflow(bad)

    def test_new_job_has_exact_branch_original_source_no_packaging_or_raw_upload(self):
        job = projection.NEW_JOB
        self.assertIn("refs/heads/" + v.BRANCH, job)
        self.assertIn("github.run_attempt == 1", job)
        self.assertIn("needs: static-controls", job)
        self.assertIn("runs-on: macos-14", job)
        self.assertIn("ref: " + b.SOURCE, job)
        self.assertEqual(job.count("persist-credentials: false"), 2)
        self.assertIn("/" + v.GATE + '"', job)
        self.assertIn('python3 -B "$OBSERVER_CONTROLS/test_darwin_exit_observer.py"', job)
        self.assertIn('cp scripts/darwin_exit_observer.py', job)
        self.assertNotIn("APPLE_", job)
        self.assertNotIn("secrets.", job)
        for forbidden in ("create-dmg", "notarize", "upload-r2", "*.json", "stdout", "stderr", "include-hidden-files: true"):
            self.assertNotIn(forbidden, job)
        paths = job.split("          path: |\n", 1)[1].split("          if-no-files-found:", 1)[0]
        self.assertEqual(len(paths.splitlines()), 4)
        self.assertIn("original-concurrency-safe-facts.json", paths)

    def test_missing_gate_actual_cli_precedes_host_provider_native_and_output(self):
        p, gate = self.fixture(); prior = p.read_bytes()
        empty = self.root / "empty"; (empty / "scripts").mkdir(parents=True)
        shutil.copyfile(ROOT / v.BASE, empty / v.BASE)
        with patch.object(v, "hosted", side_effect=AssertionError("must refuse first")), \
             patch.object(b, "fresh_records", side_effect=AssertionError("must refuse first")), \
             patch.object(v, "prepare_app", side_effect=AssertionError("must refuse first")):
            with self.assertRaisesRegex(RuntimeError, "committed-remaining-gates-root-gate-required"):
                v.main(["--diagnostic-checkout", str(empty), "--producer-checkout", str(ROOT),
                    "--gate", str(empty / v.GATE), "--destination", str(self.root / "private"),
                    "--safe-output", str(self.root / "safe")])
        self.assertEqual(p.read_bytes(), prior)
        self.assertFalse((empty / v.GATE).exists()); self.assertFalse((self.root / "private").exists())
        self.assertFalse((self.root / "safe").exists())

    def test_gate_requires_exact_13_committed_harness_blobs_and_independent_app_identity(self):
        p, gate = self.fixture()
        self.assertEqual(len(gate["reviewedHarnessFiles"]), 13)
        with patch.object(b, "capture", side_effect=self.git) as git:
            self.assertEqual(v.verify_gate(b, self.root, p), gate)
            self.assertEqual(git.call_count, 14)
        for key, val in (("producerSource", "b" * 40), ("producerAttempt", 2),
                         ("privateDmgAllowed", True), ("publication", True), ("customerAppQualified", True),
                         ("releaseQualified", True), ("privateReplayAllowed", False)):
            p.write_text(json.dumps(dict(gate, **{key: val})))
            with self.subTest(key=key), self.assertRaises(b.Refusal): v.verify_gate(b, self.root, p)
        p.write_text(json.dumps(gate))
        with patch.object(b, "capture", return_value=b"uncommitted"), self.assertRaises(b.Refusal):
            v.verify_gate(b, self.root, p)

    def test_gate_refuses_resealed_frozen_source_drift_and_old_scope(self):
        p, gate = self.fixture()
        name = "scripts/darwin_exit_adapter.py"
        (self.root / name).write_text("# drift\n")
        gate["reviewedHarnessFiles"][name] = b.sha((self.root / name).read_bytes())
        p.write_text(json.dumps(gate))
        with patch.object(b, "capture", side_effect=self.git), self.assertRaisesRegex(b.Refusal, "frozen-c747-source-drift"):
            v.verify_gate(b, self.root, p)
        gate["scope"] = b.SCOPE; p.write_text(json.dumps(gate))
        with self.assertRaises(b.Refusal): v.verify_gate(b, self.root, p)

    def test_hosted_branch_delegation_restores_frozen_module(self):
        original = b.QA_BRANCH
        def check(*args):
            self.assertEqual(b.QA_BRANCH, v.BRANCH)
            return "b" * 40
        with patch.object(b, "hosted_context", side_effect=check): self.assertEqual(v.hosted(b, ROOT, ROOT), "b" * 40)
        self.assertEqual(b.QA_BRANCH, original)
        with patch.object(b, "hosted_context", side_effect=RuntimeError("fixed")), self.assertRaises(RuntimeError):
            v.hosted(b, ROOT, ROOT)
        self.assertEqual(b.QA_BRANCH, original)

    def test_desktop_calls_frozen_wrapper_once_under_observer_exact_archive_args(self):
        app = self.root / "Mixel-Remote.app"; nested = self.root / "signed-stapled-diagnostic-app.zip"
        order = []
        @contextlib.contextmanager
        def install(desktop, output):
            self.assertIs(desktop, wrapper.desktop); self.assertEqual(output, self.root / "exit-observer-private.json")
            order.append("observer-enter"); yield; order.append("observer-exit")
        def invoke(mode, argv):
            order.append("wrapper")
            self.assertEqual(mode, "--original-desktop")
            self.assertEqual(argv, ["--app", str(app), "--diagnostics", str(self.root / "runtime"),
                                    "--diagnostic-app-zip", str(nested)])
            return 0
        wrapper = types.SimpleNamespace(desktop=object(), run_original=Mock(side_effect=invoke))
        code, hashes = v.original_desktop(b, wrapper, types.SimpleNamespace(install=install), app, nested, self.root)
        self.assertEqual(code, 0); wrapper.run_original.assert_called_once()
        self.assertEqual(order, ["observer-enter", "wrapper", "observer-exit"])
        self.assertEqual(set(hashes), {"stdout", "stderr"})

    def test_child_exact_original_argv_environment_and_deadline(self):
        env = dict(os.environ, GH_TOKEN="secret", GITHUB_TOKEN="secret", APPLE_NOTARY_PASSWORD="secret", RETAINED="yes")
        def run(argv, **kw):
            self.assertEqual(argv, [sys.executable, "-B", str(ROOT / "scripts/test-support-launch-macos-concurrency.py"), "--original-tray", "--app", "owned"])
            self.assertEqual(kw["timeout"], 420)
            self.assertEqual(kw["env"]["RETAINED"], "yes")
            self.assertFalse(any(k.startswith("APPLE_") or k in {"GH_TOKEN", "GITHUB_TOKEN"} for k in kw["env"]))
            return types.SimpleNamespace(returncode=1)
        with patch.dict(os.environ, env, clear=True), patch.object(subprocess, "run", side_effect=run) as child:
            code, hashes = v.original_child(b, ROOT, ["--original-tray", "--app", "owned"], self.root, "tray", 420)
        child.assert_called_once(); self.assertEqual(code, 1)
        self.assertTrue(all(row["bytes"] == 0 for row in hashes.values()))

    def exercise_gates(self, *, desktop_code=0, tray_code=0, concurrency_code=0, mutate_tray=None, mutate_concurrency=None, cleanup_error_at=None):
        support, parser = self.parser(); tray, full = self.native_facts(parser)
        if mutate_tray: mutate_tray(tray)
        if mutate_concurrency: mutate_concurrency(full)
        private = self.root / "private"; private.mkdir(); safe = self.root / "safe"; safe.mkdir()
        receipt = {"allOriginalGatesPassed": False}; calls = []
        def child(base, producer, argv, own, label, timeout):
            calls.append(label)
            directory = own / label; directory.mkdir()
            filename = "tray-runtime-safe-facts.json" if label == "tray" else "concurrency-runtime-safe-facts.json"
            (directory / filename).write_text(json.dumps(tray if label == "tray" else full))
            return (tray_code if label == "tray" else concurrency_code), {"stdout": {}, "stderr": {}}
        def desktop(*args): calls.append("desktop"); return desktop_code, {}
        cleanup_count = 0
        def stopped():
            nonlocal cleanup_count
            cleanup_count += 1
            if cleanup_count == cleanup_error_at: raise RuntimeError("runtime-cleanup-absence-not-proven")
        with patch.object(v, "original_desktop", side_effect=desktop), patch.object(v, "original_child", side_effect=child), \
             patch.object(b, "retain_observation", return_value={}), patch.object(parser, "stopped_runtime", side_effect=stopped):
            try:
                v.run_gates(b, support, parser, object(), object(), ROOT, self.root / "Mixel-Remote.app",
                            self.root / "signed-stapled-diagnostic-app.zip", private, safe, receipt)
                error = None
            except Exception as e: error = e
        return calls, receipt, safe, error, cleanup_count

    def test_original_three_gates_exact_order_success_and_all_role_checks(self):
        calls, receipt, safe, error, stopped = self.exercise_gates()
        self.assertIsNone(error); self.assertEqual(calls, ["desktop", "tray", "concurrency"]); self.assertEqual(stopped, 3)
        self.assertTrue(receipt["allOriginalGatesPassed"])
        for key in ("tray", "concurrency"): self.assertFalse(receipt[key]["kernelExitStatusObserved"])
        facts = json.loads((safe / "original-concurrency-safe-facts.json").read_bytes())
        self.assertEqual([row["completionCount"] for row in facts["scenarios"]], [2, 2, 1])
        self.assertEqual([row["stabilitySecondsRequired"] for row in facts["scenarios"]], [5.0] * 3)
        self.assertFalse(facts["customerAppQualified"]); self.assertFalse(facts["publication"])

    def test_desktop_failure_prohibits_tray_and_concurrency(self):
        calls, receipt, safe, error, stopped = self.exercise_gates(desktop_code=1)
        self.assertEqual(calls, ["desktop"]); self.assertIsNotNone(error); self.assertEqual(stopped, 0)
        self.assertFalse(receipt["allOriginalGatesPassed"]); self.assertEqual(receipt["desktop"]["originalExitCode"], 1)

    def test_tray_failure_preserves_phase_hash_and_cleanup_failure_before_stop(self):
        def mutate(t):
            t.update(passed=False, ownedProcessesStopped=False,
                     runtimeFailure={"phase": "attended-service-readiness", "reason": "private original reason"},
                     failure="different private cleanup reason")
        calls, receipt, safe, error, stopped = self.exercise_gates(tray_code=1, mutate_tray=mutate)
        self.assertEqual(calls, ["desktop", "tray"]); self.assertIsNotNone(error)
        facts = json.loads((safe / "original-tray-safe-facts.json").read_bytes())
        self.assertEqual(facts["primaryFailure"]["phase"], "attended-service-readiness")
        self.assertIn("cleanupFailure", facts)
        self.assertNotIn("private original reason", json.dumps(facts))
        self.assertEqual(facts["primaryFailure"]["reasonSha256"], b.sha(b"private original reason"))

    def test_tray_cleanup_field_even_exit_zero_blocks_concurrency(self):
        calls, receipt, safe, error, stopped = self.exercise_gates(mutate_tray=lambda t: t.update(endpointCleanupFailures=[{"private": "data"}]))
        self.assertEqual(calls, ["desktop", "tray"]); self.assertIsNotNone(error)
        self.assertFalse(receipt["allOriginalGatesPassed"])
        self.assertNotIn('"private": "data"', (safe / "original-tray-safe-facts.json").read_text())

    def test_full_concurrency_fatal_original_assertions_unweakened(self):
        for key, value in (("completionCount", 1), ("stabilitySecondsRequired", 4.0), ("stabilitySamples", 0),
                           ("ownedProcessesStopped", False), ("criticalHashes", {})):
            # A separate private output root per mutant avoids accidental reuse.
            with self.subTest(key=key), tempfile.TemporaryDirectory(prefix="remaining-mutant-") as d:
                old = self.root; self.root = Path(d).resolve()
                try:
                    calls, receipt, safe, error, stopped = self.exercise_gates(mutate_concurrency=lambda f: f["scenarios"][0].update({key: value}))
                    self.assertEqual(calls, ["desktop", "tray", "concurrency"]); self.assertIsNotNone(error)
                    self.assertFalse(receipt["allOriginalGatesPassed"])
                finally: self.root = old

    def test_full_concurrency_exit_nonzero_preserved(self):
        calls, receipt, safe, error, stopped = self.exercise_gates(concurrency_code=1)
        self.assertIsNotNone(error); self.assertEqual(receipt["concurrency"]["originalExitCode"], 1)
        self.assertTrue((safe / "original-concurrency-safe-facts.json").exists())
        self.assertFalse(receipt["allOriginalGatesPassed"])

    def test_role_or_endpoint_absence_failure_stops_later_gates(self):
        calls, receipt, safe, error, stopped = self.exercise_gates(cleanup_error_at=2)
        self.assertEqual(calls, ["desktop", "tray"]); self.assertIsNotNone(error)
        self.assertFalse(receipt["allOriginalGatesPassed"])

    def test_final_metadata_refusal_cannot_return_green_and_preserves_actual_state(self):
        support = types.SimpleNamespace(CRITICAL=(), verify_signatures=Mock())
        private, safe = self.root / "private", self.root / "safe"
        gate = {"reviewedHarnessFiles": {}}
        record = observed(); bad = observed(); bad["producer"]["head_sha"] = "b" * 40
        fake = types.SimpleNamespace(**{name: getattr(b, name) for name in dir(b) if not name.startswith("__")})
        fake.module = Mock(return_value=support)
        fake.fresh_records = Mock(side_effect=[record, record, bad]); fake.fresh_fixture = Mock()
        def prep(*args): args[-1]["criticalHashes"] = {}; return self.root / "app", self.root / "nested"
        def gates(*args): args[-1]["allOriginalGatesPassed"] = True
        with patch.object(v, "load_base", return_value=fake), patch.object(v, "verify_gate", return_value=gate), \
             patch.object(v, "hosted", return_value="b" * 40), patch.object(v, "prepare_app", side_effect=prep), \
             patch.object(v, "parser_for_app", return_value=object()), patch.object(v, "run_gates", side_effect=gates), \
             patch.dict(os.environ, {"GITHUB_RUN_ID": "123"}), patch.object(fake, "read", side_effect=lambda p, *args: b"unit gate" if p == self.root / "synthetic-gate" else b.read(p, *args)):
            code = v.main(["--diagnostic-checkout", str(ROOT), "--producer-checkout", str(ROOT),
                    "--gate", str(self.root / "synthetic-gate"), "--destination", str(private), "--safe-output", str(safe)])
        self.assertEqual(code, 1)
        facts = json.loads((safe / "remaining-gates-validation-receipt.json").read_bytes())
        self.assertFalse(facts["privateDiagnosticCompleted"]); self.assertFalse(facts["afterAdmissionValidationPassed"])
        self.assertEqual(facts["observedAfter"]["producer"]["headSha"], "b" * 40)
        self.assertFalse(facts["releaseQualified"])


def legacy_controls():
    """Run unchanged tests in owned copy with only exact workflow projections."""
    with tempfile.TemporaryDirectory(prefix="current77-remaining-legacy-source-") as d:
        source = Path(d) / "source"
        shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        workflow = source / b.WORKFLOW
        workflow.write_text(projection.current_workflow(workflow.read_text()))
        for name in ("test-diagnose-macos-failed-app.py", "test-support-launch-macos-tray-controls.py",
                     "test-diagnose-current77-arm-warm-exit.py"):
            subprocess.run([sys.executable, "-B", str(source / "scripts" / name)], cwd=source, check=True, timeout=180)
        old = load("remaining_legacy_projection", source / b.PROJECTION)
        workflow.write_text(old.historical_workflow(workflow.read_text()))
        subprocess.run([sys.executable, "-B", str(source / "scripts/test-diagnose-macos-concurrency-failed-intel.py")],
                       cwd=source, check=True, timeout=180)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-legacy-controls", action="store_true")
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RemainingGateControls))
    if not result.wasSuccessful(): return 1
    if args.with_legacy_controls: legacy_controls()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
