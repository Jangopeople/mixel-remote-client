#!/usr/bin/env python3
"""Pure/file/mock controls. No app/native/helper launch or provider operation.

Gate fixtures exist only in disposable unit-test directories. They are never
written at the staged future committed gate path and cannot authorize a replay.
The actual Darwin child controls are a separate hosted ARM workflow command.
"""
import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
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
SCRIPTS = ROOT / "scripts"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


v = load("current77_arm_replay", SCRIPTS / "diagnose-current77-arm-warm-exit.py")
projection = load("current77_arm_projection", SCRIPTS / "current77_arm_workflow_projection.py")
PROFILE_SHA = "603f029be552a651ac1cabf9686ffbe8bbc874bb50bd5db439bf782a8cc7e795"


def observed():
    raw = (SCRIPTS / v.OBSERVED_PROFILE.split("/")[-1]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PROFILE_SHA
    return json.loads(raw)


class CarrierControls(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="current77-arm-pure-control-")
        self.root = Path(self.directory.name).resolve()
        self.block = patch.object(v.subprocess, "run", side_effect=AssertionError("No provider/native/helper process in pure controls"))
        self.block.start()

    def tearDown(self):
        self.block.stop()
        self.directory.cleanup()

    def test_authentic_observed_pending_states_are_admitted_without_green_relabeling(self):
        facts = v.validate_records(observed())
        self.assertEqual(facts["producer"], {"id": v.RUN, "status": "in_progress", "conclusion": None})
        self.assertEqual(facts["preflight"], {"id": v.PREFLIGHT, "status": "in_progress", "conclusion": None})
        self.assertEqual(facts["comparison"]["conclusion"], "success")
        self.assertFalse(facts["wholeProducerGreen"])
        self.assertFalse(facts["normalReleaseQualified"])
        # Synthetic forward-state control only, never a future original/gate.
        completed = observed()
        completed["producer"].update(status="completed", conclusion="failure")
        completed["preflight"].update(status="completed", conclusion="success")
        for label, job in (("producerJobs", 116562140563), ("preflightJobs", 116557334151)):
            next(j for j in completed[label]["jobs"] if j["id"] == job).update(status="completed", conclusion="success")
        self.assertFalse(v.validate_records(completed)["wholeProducerGreen"])

    def test_source_repository_event_attempt_and_job_mutations_refuse(self):
        for name, key, value in (
            ("producer", "id", v.RUN + 1), ("producer", "head_sha", "b" * 40),
            ("producer", "event", "push"), ("producer", "run_attempt", 2),
            ("producer", "conclusion", "success"), ("preflight", "status", "invalid-state"),
            ("armJob", "conclusion", "success"),
            ("armJob", "status", "in_progress"), ("armJob", "labels", ["self-hosted"]),
            ("armJob", "id", True), ("artifact", "expired", True),
            ("artifact", "digest", "sha256:" + "b" * 64),
            ("artifact", "size_in_bytes", v.ARTIFACT["size_in_bytes"] + 1),
        ):
            bad = observed(); bad[name][key] = value
            with self.subTest(name=name, key=key), self.assertRaises(v.Refusal):
                v.validate_records(bad)
        bad = observed(); bad["producer"]["repository"]["id"] = 1
        with self.assertRaises(v.Refusal): v.validate_records(bad)
        bad = observed(); bad["armJob"]["steps"][0]["number"] = True
        with self.assertRaises(v.Refusal): v.validate_records(bad)

    def test_failed_step25_and_successful_upload27_remain_exact(self):
        for number, field, value in ((25, "conclusion", "success"), (25, "number", 24),
                                     (27, "conclusion", "failure"), (27, "completed_at", "2026-10-15T18:31:24Z")):
            bad = observed()
            next(s for s in bad["armJob"]["steps"] if s["number"] == number)[field] = value
            with self.subTest(number=number, field=field), self.assertRaises(v.Refusal): v.validate_records(bad)
        bad = observed(); bad["producerJobs"]["jobs"].append(copy.deepcopy(bad["producerJobs"]["jobs"][0]))
        bad["producerJobs"]["total_count"] += 1
        with self.assertRaises(v.Refusal): v.validate_records(bad)

    def test_required_successes_and_actual_two_comparison_lanes_cannot_be_skipped(self):
        for collection, job in (("producerJobs", 116562140309), ("producerJobs", 116562140491),
                                ("preflightJobs", 116557334238), ("preflightJobs", 116557333724)):
            bad = observed(); next(j for j in bad[collection]["jobs"] if j["id"] == job)["conclusion"] = "skipped"
            with self.subTest(job=job), self.assertRaises(v.Refusal): v.validate_records(bad)
        bad = observed(); bad["comparisonJobs"]["jobs"].pop(); bad["comparisonJobs"]["total_count"] -= 1
        with self.assertRaises(v.Refusal): v.validate_records(bad)

    def test_unrelated_windows_and_comparison_failure_cancel_timeout_are_honest_private_facts(self):
        for conclusion in ("failure", "timed_out", "cancelled"):
            records = observed()
            for name in ("preflight", "comparison"):
                records[name].update(status="completed", conclusion=conclusion)
            for name, job in (("producerJobs", 116562140563), ("preflightJobs", 116557334151),
                              ("comparisonJobs", 116557333543), ("comparisonJobs", 116557334369)):
                next(j for j in records[name]["jobs"] if j["id"] == job).update(status="completed", conclusion=conclusion)
            facts = v.validate_records(records)
            self.assertEqual(facts["windowsProducer"]["conclusion"], conclusion)
            self.assertEqual(facts["windowsPreflight"]["conclusion"], conclusion)
            self.assertEqual(facts["comparison"]["conclusion"], conclusion)
            self.assertEqual([j["conclusion"] for j in facts["comparisonLanes"]], [conclusion, conclusion])
            self.assertFalse(facts["wholeProducerGreen"]); self.assertFalse(facts["normalReleaseQualified"])

    def test_actual_post_state_summary_survives_separate_source_identity_refusal(self):
        records = observed(); records["producer"]["head_sha"] = "b" * 40
        summary = v.actual_state_summary(records)
        self.assertEqual(summary["producer"]["headSha"], "b" * 40)
        self.assertEqual(summary["producer"]["status"], "in_progress")
        self.assertFalse(summary["normalReleaseQualified"])
        with self.assertRaises(v.Refusal): v.validate_records(records)

    def gate_fixture(self):
        # Explicit synthetic unit fixture: no Git commit or genuine gate exists.
        files = {v.SELF, v.CONTROLS, v.PROJECTION, v.WORKFLOW, v.OBSERVED_PROFILE, v.HISTORICAL_CONTROLS, *v.OBSERVER}
        for name in files:
            destination = self.root / name; destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        gate = {"schema": 1, "scope": v.SCOPE, "producerSource": v.SOURCE, "producerTree": v.TREE,
                "producerRun": v.RUN, "producerAttempt": 1, "failedJob": v.JOB,
                "privateReplayAllowed": True, "customerAppQualified": False, "releaseQualified": False,
                "publication": False, "artifact": v.ARTIFACT, "nestedAppSha256": v.NESTED_SHA,
                "observedOriginals": observed(), "observedAdmission": v.validate_records(observed()),
                "reviewedHarnessFiles": {n: hashlib.sha256((self.root / n).read_bytes()).hexdigest() for n in files},
                "unitControlFixtureOnly": True}
        path = self.root / v.GATE_PATH; path.parent.mkdir()
        path.write_text(json.dumps(gate))
        return path, gate

    def git_blob(self, argv, *unused, **kwargs):
        self.assertEqual(argv[:3], ["git", "-C", str(self.root)])
        self.assertEqual(argv[3], "show")
        self.assertTrue(argv[4].startswith("HEAD:"))
        return (self.root / argv[4][5:]).read_bytes()

    def test_missing_gate_refuses_actual_cli_path_before_any_operation_or_output(self):
        existing_path, existing_gate = self.gate_fixture()
        existing_bytes = existing_path.read_bytes()
        with patch.object(v, "capture", side_effect=self.git_blob):
            self.assertEqual(v.verify_gate(self.root, existing_path), existing_gate)
        empty_checkout = self.root / "empty-diagnostic-checkout"
        empty_checkout.mkdir()
        missing_gate = empty_checkout / v.GATE_PATH
        with patch.object(v, "hosted_context", side_effect=AssertionError("Gate must refuse first")), \
             patch.object(v, "fresh_records", side_effect=AssertionError("No provider call")), \
             patch.object(v, "new_directory", side_effect=AssertionError("No output creation")):
            with self.assertRaisesRegex(v.Refusal, "committed-root-private-gate-required"):
                v.main(["--diagnostic-checkout", str(empty_checkout), "--producer-checkout", str(ROOT),
                        "--gate", str(missing_gate), "--destination", str(self.root / "private"),
                        "--safe-output", str(self.root / "safe")])
        self.assertFalse(missing_gate.exists())
        self.assertEqual(existing_path.read_bytes(), existing_bytes)

    def test_gate_requires_all_exact_reviewed_committed_blobs_and_false_qualification(self):
        path, gate = self.gate_fixture()
        with patch.object(v, "capture", side_effect=self.git_blob) as git:
            self.assertEqual(v.verify_gate(self.root, path), gate)
            self.assertEqual(git.call_count, len(gate["reviewedHarnessFiles"]) + 1)
        for key, value in (("publication", True), ("releaseQualified", True), ("customerAppQualified", True),
                           ("producerSource", "b" * 40), ("producerAttempt", 2), ("privateReplayAllowed", False)):
            changed = dict(gate, **{key: value}); path.write_text(json.dumps(changed))
            with self.subTest(key=key), self.assertRaises(v.Refusal): v.verify_gate(self.root, path)
        path.write_text(json.dumps(gate))
        with patch.object(v, "capture", return_value=b"different committed blob"), self.assertRaises(v.Refusal):
            v.verify_gate(self.root, path)
        path.write_text(json.dumps(gate)); (self.root / v.SELF).write_text("# drift\n")
        with patch.object(v, "capture", side_effect=self.git_blob), self.assertRaises(v.Refusal):
            v.verify_gate(self.root, path)

    def test_observer_helper_or_test_pin_drift_refuses_before_provider(self):
        path, gate = self.gate_fixture()
        for name in v.OBSERVER:
            bad = copy.deepcopy(gate); bad["reviewedHarnessFiles"][name] = "b" * 64
            path.write_text(json.dumps(bad))
            with self.subTest(file=name), self.assertRaisesRegex(v.Refusal, "reviewed-exit-observer-pins"):
                v.verify_gate(self.root, path)

    def test_read_refuses_symlinks_hardlinks_empty_and_oversize(self):
        path = self.root / "owned"; path.write_bytes(b"source")
        self.assertEqual(v.read(path), b"source")
        link = self.root / "link"; link.symlink_to(path)
        with self.assertRaises(v.Refusal): v.read(link)
        hard = self.root / "hard"; os.link(path, hard)
        with self.assertRaises(v.Refusal): v.read(path)
        hard.unlink(); path.write_bytes(b"")
        with self.assertRaises(v.Refusal): v.read(path)
        path.write_bytes(b"12345")
        with self.assertRaises(v.Refusal): v.read(path, 4)

    def test_frozen_producer_sources_and_actual_locked_wrapper_are_unchanged(self):
        for name, expected in v.FROZEN.items():
            with self.subTest(source=name): self.assertEqual(v.sha((ROOT / name).read_bytes()), expected)
        original = v.module(SCRIPTS / "test-support-launch-macos-concurrency.py", v.FROZEN["scripts/test-support-launch-macos-concurrency.py"])
        self.assertEqual(original.inverse_thread_safe_swift(original.thread_safe_swift(original.desktop.SWIFT)), original.desktop.SWIFT)
        self.assertIn("MixelCompletionStore(count: 1)", original.thread_safe_swift(original.desktop.SWIFT))

    def test_actual_original_desktop_wrapper_runs_once_with_compiler_projection_and_restores(self):
        original = v.module(SCRIPTS / "test-support-launch-macos-concurrency.py", v.FROZEN["scripts/test-support-launch-macos-concurrency.py"])
        desktop = original.desktop
        compiler, swift, argv = desktop.compile_helper, desktop.SWIFT, sys.argv
        calls = []; installer_calls = []
        @contextlib.contextmanager
        def install(actual_desktop, output):
            installer_calls.append((actual_desktop, output)); yield
        def main():
            calls.append(tuple(sys.argv))
            self.assertEqual(desktop.compile_helper(self.root), self.root / "inert-helper")
            return 1
        with patch.object(original.tray, "isolated_ci") as isolated, \
             patch.object(original, "compile_source", return_value=self.root / "inert-helper") as compile_source, \
             patch.object(desktop, "main", side_effect=main) as entry:
            code, output = v.run_unchanged_smoke(original, types.SimpleNamespace(install=install), self.root / "Mixel-Remote.app", self.root)
        self.assertEqual(code, 1); isolated.assert_called_once_with(); entry.assert_called_once_with()
        compile_source.assert_called_once_with(self.root, original.thread_safe_swift(swift))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:], ("--app", str(self.root / "Mixel-Remote.app"), "--diagnostics", str(self.root / "runtime")))
        self.assertEqual(installer_calls, [(desktop, self.root / "exit-observer-private.json")])
        self.assertIs(desktop.compile_helper, compiler); self.assertEqual(desktop.SWIFT, swift); self.assertIs(sys.argv, argv)
        self.assertEqual(output["stdout"]["sha256"], hashlib.sha256(b"").hexdigest())

    def test_original_exception_is_not_converted_or_replaced(self):
        original = v.module(SCRIPTS / "test-support-launch-macos-concurrency.py", v.FROZEN["scripts/test-support-launch-macos-concurrency.py"])
        error = RuntimeError("controlled original failure")
        @contextlib.contextmanager
        def install(*args): yield
        compiler = original.desktop.compile_helper
        with patch.object(original.tray, "isolated_ci"), patch.object(original.desktop, "main", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                v.run_unchanged_smoke(original, types.SimpleNamespace(install=install), self.root / "Mixel-Remote.app", self.root)
        self.assertIs(raised.exception, error)
        self.assertIs(original.desktop.compile_helper, compiler)

    def session(self, phase="fresh-warm-url-launch", code=0):
        app = self.root / "Mixel-Remote.app"; pid = 321
        receipt = {"diagnostic_only": True, "qualifies_application": False, "original_methods_restored": True,
                   "errors": [], "observers": [{"diagnostic_only": True, "qualifies_application": False,
                    "state": "exit_observed", "identity": {"pid": pid, "uid": os.geteuid(), "birth_seconds": 100,
                    "birth_microseconds": 2, "executable": str(app / "Contents/MacOS/Mixel-Remote"),
                    "bundle_path": str(app), "bundle_identifier": "ch.mixel.remote", "bundle_version": "64"},
                    "requested_notes": 0x86000000,
                    "raw_events": [{"ident": pid, "filter": -5, "flags": 0, "fflags": 0x84000000, "data": code << 8, "udata": 42}],
                    "decoded_exit": {"exit_status_available": True, "exit_detail_available": False, "kind": "exited", "exit_code": code},
                    "error": None, "first_exit_observation": {"sampling_stage": "after-request", "current_mark": None,
                     "phase": phase, "utc": "2026-10-15T18:31:16.361201+00:00", "elapsed_seconds": 1.92,
                     "meaning": "first observed exit; phase is not proof of the cause"}}]}
        runtime = {"schema": 1, "scope": "macos-launch-smoke-diagnostic-not-customer-release", "privacy_check_passed": True,
                   "customer_app_qualified": False, "screen_control_qualified": False, "publication": False,
                   "outcome": "failure", "events": [{"event": "owned-ordinary-application-launched", "pid": pid}]}
        return receipt, runtime, app

    def test_warm_exit_and_owned_cleanup_exit_keep_distinct_sampling_phases(self):
        for phase in ("fresh-warm-url-launch", "ordinary-to-warm-owned-cleanup", "owned-cleanup"):
            raw, runtime, app = self.session(phase)
            safe = v.safe_observer_facts(raw, runtime, app, 1)
            self.assertEqual(safe["observers"][0]["firstExitObservation"]["phase"], phase)
            self.assertFalse(safe["qualifiesApplication"])
            self.assertEqual(safe["originalSmokeExitCode"], 1)
            self.assertNotIn(str(app), json.dumps(safe))
            self.assertEqual(safe["observers"][0]["decodedExit"]["exit_code"], 0)

    def test_no_status_or_detail_flag_never_invents_a_code_or_cause(self):
        raw, runtime, app = self.session()
        raw["observers"][0]["decoded_exit"] = {"exit_status_available": False, "exit_detail_available": False}
        raw["observers"][0]["raw_events"][0]["fflags"] = 0x80000000
        safe = v.safe_observer_facts(raw, runtime, app, 1)
        self.assertNotIn("exit_code", safe["observers"][0]["decodedExit"])
        raw["observers"][0]["decoded_exit"]["exit_code"] = 0
        with self.assertRaises(v.Refusal): v.safe_observer_facts(raw, runtime, app, 1)

    def test_green_smoke_with_missing_invalid_or_statusless_observer_is_fatal(self):
        raw, runtime, app = self.session(); runtime["outcome"] = "success"
        good = v.safe_observer_facts(raw, runtime, app, 0)
        v.require_complete_observation(good)
        for mutation in ("missing", "error", "invalid", "pending", "statusless"):
            bad = copy.deepcopy(good)
            if mutation == "missing": bad["observers"] = []
            if mutation == "error": bad["errors"] = [{"stage": "register-owned-ordinary-PID", "error_type": "ObservationError"}]
            if mutation == "invalid": bad["observers"][0]["state"] = "invalid"
            if mutation == "pending": bad["observers"][0].update(state="pending", decodedExit=None)
            if mutation == "statusless": bad["observers"][0]["decodedExit"]["exit_status_available"] = False
            with self.subTest(mutation=mutation), self.assertRaises(v.Refusal): v.require_complete_observation(bad)

    def test_registration_error_and_original_green_result_are_retained_before_fatal_gate(self):
        raw, runtime, app = self.session(); runtime["outcome"] = "success"
        raw["observers"] = []
        raw["errors"] = [{"stage": "register-owned-ordinary-PID", "error_type": "ObservationError"}]
        private = self.root / "private"; private.mkdir(); (private / "runtime").mkdir()
        safe = self.root / "safe"; safe.mkdir()
        (private / "exit-observer-private.json").write_text(json.dumps(raw))
        (private / "runtime/runtime.json").write_text(json.dumps(runtime))
        receipt = {}
        with self.assertRaisesRegex(v.Refusal, "exit-observer-evidence-incomplete"):
            v.retain_observation(private, safe, app, receipt, 0, {"stdout": {}, "stderr": {}})
        self.assertEqual(receipt["originalSmokeExitCode"], 0)
        retained = json.loads((safe / "warm-exit-observer-safe-facts.json").read_text())
        self.assertEqual(retained["errors"], raw["errors"])
        self.assertFalse(retained["qualifiesApplication"])

    def test_safe_original_failure_phase_and_reason_hash_survive_pre_warm_failure(self):
        raw, runtime, app = self.session()
        reason = "private unrecognized failure with synthetic credentials"
        runtime["events"].append({"event": "test-failure", "phase": "ordinary-unarmed-health", "reason": reason})
        safe = v.safe_observer_facts(raw, runtime, app, 1)
        self.assertEqual(safe["originalRuntimeFailure"]["phase"], "ordinary-unarmed-health")
        self.assertEqual(safe["originalRuntimeFailure"]["reasonSha256"], hashlib.sha256(reason.encode()).hexdigest())
        self.assertEqual(safe["originalRuntimeFailure"]["reviewedReasonCategory"], "unrecognized-reason-redacted")
        self.assertNotIn(reason, json.dumps(safe))
        runtime["events"][-1].update(phase="fresh-warm-url-launch", reason="Warm support URL customer window: customer app exited")
        self.assertEqual(v.safe_observer_facts(raw, runtime, app, 1)["originalRuntimeFailure"]["reviewedReasonCategory"], "warm-owned-pid-exited")

    def test_native_core_controls_write_only_in_new_runner_temp_copy(self):
        job = projection.ADDED_JOB
        self.assertIn('OBSERVER_CONTROLS="$RUNNER_TEMP/current77-exit-observer-controls"', job)
        self.assertIn('mkdir "$OBSERVER_CONTROLS"', job)
        self.assertNotIn('mkdir -p "$OBSERVER_CONTROLS"', job)
        for name in v.OBSERVER:
            self.assertIn(name, job)
        self.assertIn('python3 -B "$OBSERVER_CONTROLS/test_darwin_exit_observer.py"', job)
        self.assertNotIn('python3 -B scripts/test_darwin_exit_observer.py', job)
        self.assertFalse((SCRIPTS / "actual-child-results.json").exists())

    def test_linux_shaped_select_absence_never_loads_native_core_controls(self):
        absent = types.ModuleType("select")
        with patch.object(sys, "platform", "linux"), patch.dict(sys.modules, {"select": absent}):
            tests = suite()
        groups = list(tests)
        self.assertEqual(len(groups), 2)
        self.assertIn("CarrierControls", list(groups[0])[0].id())
        self.assertIn("AdapterControls", list(groups[1])[0].id())

    def test_bad_observer_identity_extra_raw_fields_and_false_scope_refuse(self):
        for field, value in (("pid", 322), ("uid", os.geteuid() + 1), ("bundle_version", "65"),
                             ("executable", "/other/app"), ("birth_microseconds", 1000000)):
            raw, runtime, app = self.session(); raw["observers"][0]["identity"][field] = value
            with self.subTest(field=field), self.assertRaises(v.Refusal): v.safe_observer_facts(raw, runtime, app, 1)
        raw, runtime, app = self.session(); raw["rawLog"] = "must not upload"
        with self.assertRaises(v.Refusal): v.safe_observer_facts(raw, runtime, app, 1)
        raw, runtime, app = self.session(); raw["qualifies_application"] = True
        with self.assertRaises(v.Refusal): v.safe_observer_facts(raw, runtime, app, 1)
        raw, runtime, app = self.session(); raw["observers"][0]["raw_events"][0]["uri"] = "must not upload"
        with self.assertRaises(v.Refusal): v.safe_observer_facts(raw, runtime, app, 1)

    def test_two_hunk_workflow_inverse_preserves_all_original_bytes_and_rejects_drift(self):
        current = (ROOT / v.WORKFLOW).read_text()
        old = projection.historical_workflow(current)
        self.assertEqual(hashlib.sha256(old.encode()).hexdigest(), projection.HISTORICAL_SHA)
        self.assertEqual(projection.historical_workflow(old), old)
        for bad in (current + "# unrelated drift\n", current.replace("codex/current77-arm-warmexit", "main"),
                    current.replace("runs-on: macos-14", "runs-on: self-hosted", 1),
                    current + projection.ADDED_JOB, current.replace(projection.ADDED_CONTROL, "", 1)):
            with self.subTest(drift=hashlib.sha256(bad.encode()).hexdigest()), self.assertRaises(ValueError):
                projection.historical_workflow(bad)
        self.assertNotIn(": write", current)
        self.assertNotIn("secrets.", current)
        self.assertIn("refs/heads/" + v.QA_BRANCH, projection.ADDED_JOB)
        upload = projection.ADDED_JOB.split("uses: actions/upload-artifact@v6", 1)[1]
        self.assertNotIn("*.log", upload); self.assertNotIn("*.zip", upload)
        self.assertIn("current77-arm-safe-facts/warm-exit-observer-safe-facts.json", upload)

    def test_all_304_historical_arm_assertions_remain_byte_identical_ast(self):
        current = (SCRIPTS / "test-diagnose-macos-failed-app.py").read_text()
        added = ('        from current77_arm_workflow_projection import historical_workflow\n'
                 '        text = historical_workflow((ROOT / v.DIAGNOSTIC_WORKFLOW).read_text())')
        original = '        text = (ROOT / v.DIAGNOSTIC_WORKFLOW).read_text()'
        self.assertEqual(current.count(added), 1)
        restored = current.replace(added, original, 1)
        self.assertEqual(hashlib.sha256(restored.encode()).hexdigest(),
                         "761e89d1c381ba95aebb00c302dec9883c926c1b7936914b489e7e1385abe3f0")
        def assertions(text):
            return [ast.dump(n, include_attributes=False) for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "self" and n.func.attr.startswith("assert")]
        self.assertEqual(len(assertions(current)), 304)
        self.assertEqual(assertions(current), assertions(restored))

    def test_historical_intel_profile_is_not_rebound_to_this_distinct_carrier(self):
        legacy = v.module(SCRIPTS / "diagnose-macos-concurrency-failed-intel.py",
                          "45ff48039eafb005475340c68b9bb49a4596a577d5384c800a9da788aae98958")
        with self.assertRaisesRegex(legacy.Refusal, "reviewed-replay-source-drift"):
            legacy.source_contract(ROOT)
        carrier = self.root / "historical-intel"
        names = set(legacy.EXPECTED_PROFILE["frozenHelpers"]) | set(legacy.EXPECTED_PROFILE["reviewedSources"])
        names |= {"scripts/failed-intel-concurrency-replay-profile.json", "scripts/failed-macos-diagnostic-helper-hashes.json"}
        for name in names:
            destination = carrier / name; destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        (carrier / v.WORKFLOW).write_text(projection.historical_workflow((ROOT / v.WORKFLOW).read_text()))
        self.assertEqual(legacy.source_contract(carrier), legacy.EXPECTED_PROFILE)


def suite():
    # The core's ActualChildren class is deliberately excluded from local pure
    # controls. The hosted native job invokes its unchanged complete file.
    adapter = load("current77_exit_adapter_controls", SCRIPTS / "test_darwin_exit_adapter.py")
    loader = unittest.TestLoader()
    groups = [loader.loadTestsFromTestCase(CarrierControls)]
    if sys.platform == "darwin":
        core = load("current77_exit_core_controls", SCRIPTS / "test_darwin_exit_observer.py")
        groups.append(loader.loadTestsFromTestCase(core.Controls))
    groups.append(loader.loadTestsFromTestCase(adapter.AdapterControls))
    return unittest.TestSuite(groups)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(suite())
    raise SystemExit(0 if result.wasSuccessful() else 1)
