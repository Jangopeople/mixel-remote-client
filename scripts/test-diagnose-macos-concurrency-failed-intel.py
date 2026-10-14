#!/usr/bin/env python3
"""Exact source/mock replay controls. No provider, native app or signal action."""
import ast
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings
import zipfile

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scripts/diagnose-macos-concurrency-failed-intel.py"
SPEC = importlib.util.spec_from_file_location("fixed_intel_replay", SOURCE)
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)
v.source_contract(ROOT)


def producer():
    return {"id": v.RUN, "head_sha": v.SOURCE, "head_branch": v.BRANCH,
            "event": "workflow_dispatch", "path": v.WORKFLOW, "workflow_id": v.WORKFLOW_ID,
            "run_attempt": 1, "status": "completed", "conclusion": "failure",
            "repository": {"id": v.REPOSITORY_ID, "full_name": v.REPOSITORY},
            "head_repository": {"id": v.REPOSITORY_ID, "full_name": v.REPOSITORY}}


def jobs():
    return {"total_count": 1, "jobs": [{"id": v.JOB, "run_id": v.RUN, "run_attempt": 1,
             "name": v.JOB_NAME, "status": "completed", "conclusion": "failure",
             "head_sha": v.SOURCE, "head_branch": v.BRANCH,
             "started_at": "2026-10-14T00:41:48Z", "completed_at": "2026-10-14T01:23:08Z",
             "labels": ["macos-14"], "runner_group_id": 0, "runner_group_name": "GitHub Actions",
             "runner_name": "GitHub Actions 1000078886", "steps": [
                 {"name": v.PACKAGE_STEP, "number": 24, "status": "completed", "conclusion": "failure",
                  "started_at": "2026-10-14T01:21:15Z", "completed_at": "2026-10-14T01:22:47Z"},
                 {"name": "Retain macOS tray runtime proof", "number": 25, "status": "completed", "conclusion": "success",
                  "started_at": "2026-10-14T01:22:47Z", "completed_at": "2026-10-14T01:22:49Z"},
                 {"name": v.UPLOAD_STEP, "number": 26, "status": "completed", "conclusion": "success",
                  "started_at": "2026-10-14T01:22:49Z", "completed_at": "2026-10-14T01:22:52Z"}]}]}


def artifact():
    return {"id": v.ARTIFACT_ID, "name": v.ARTIFACT_NAME, "size_in_bytes": v.ARTIFACT_BYTES,
            "digest": "sha256:" + v.ARTIFACT_SHA256, "expired": False,
            **v.EXPECTED_PROFILE["artifactTimes"],
            "workflow_run": {"id": v.RUN, "head_sha": v.SOURCE, "head_branch": v.BRANCH,
                             "repository_id": v.REPOSITORY_ID, "head_repository_id": v.REPOSITORY_ID}}


def facts():
    return {"observedUtc": "2026-10-14T02:30:00+00:00", "syntheticInvite": True,
            "customerAcceptTested": False, "remoteDesktopConnected": False,
            "invitePayloadDeliveryTested": False, "coldDirectLaunchConcurrencyTested": False,
            "passed": True, "scenarios": [
                {"scenario": name, "passed": True, "ownedProcessesStopped": True,
                 "criticalHashes": dict(v.CRITICAL_HASHES), "trayPid": 101, "guiPid": 202,
                 "inventorySamples": 15, "stabilitySamples": 13, "stabilitySecondsRequired": 5.0,
                 "completionCount": 1 if name == "batch-two-support" else 2,
                 "completionsMatchOriginalTray": True, "actualTrayDispatchObserved": True,
                 "visibleGuiAndAttendedV2": True, "oneRetainedGuiDuringBoundedStability": True}
                for name in v.SCENARIOS]}


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, data, mode in entries:
            item = zipfile.ZipInfo(name)
            item.create_system = 3
            item.external_attr = mode << 16
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                zipped.writestr(item, data)
    return output.getvalue()


def entry(name, data=b"fixture"):
    return name, data, stat.S_IFREG | 0o600


class StrictReplayControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mixel-intel-source-mock-")
        self.root = Path(self.temp.name).resolve()
        self.forbid = patch.object(subprocess, "run", side_effect=AssertionError("Provider/native operation forbidden"))
        self.forbid.start()
        self.forbid_popen = patch.object(subprocess, "Popen", side_effect=AssertionError("Provider/native child forbidden"))
        self.forbid_popen.start()

    def tearDown(self):
        self.forbid.stop()
        self.forbid_popen.stop()
        self.temp.cleanup()

    def test_exact_profile_and_frozen_helpers_bind_reviewed_caller_before_import(self):
        v.source_contract(ROOT)
        for relative in ("scripts/test-support-launch-macos-concurrency.py",
                         "scripts/failed-macos-diagnostic-helper-hashes.json",
                         "scripts/failed-intel-concurrency-replay-profile.json",
                         "scripts/diagnose-macos-failed-app.py"):
            carrier = self.root / Path(relative).name
            shutil.copytree(ROOT / "scripts", carrier / "scripts")
            (carrier / ".github/workflows").mkdir(parents=True)
            shutil.copy2(ROOT / ".github/workflows/diagnose-macos-failed-app.yml",
                         carrier / ".github/workflows/diagnose-macos-failed-app.yml")
            with (carrier / relative).open("ab") as stream:
                stream.write(b"\n# unreviewed source\n")
            with self.subTest(relative=relative), self.assertRaises((v.Refusal, v.SUPPORT.Refusal)):
                v.source_contract(carrier)

    def test_local_self_hosted_other_branch_or_workflow_source_refuses_before_command(self):
        env = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "macOS", "RUNNER_ENVIRONMENT": "github-hosted",
               "GITHUB_REPOSITORY": v.REPOSITORY, "GITHUB_EVENT_NAME": "workflow_dispatch",
               "GITHUB_SHA": "b" * 40, "GITHUB_WORKFLOW_SHA": "b" * 40, "GITHUB_RUN_ID": "99999999999",
               "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REF": "refs/heads/" + v.BRANCH,
               "GITHUB_WORKFLOW_REF": v.REPOSITORY + "/" + v.DIAGNOSTIC_WORKFLOW + "@refs/heads/" + v.BRANCH}
        for field, value in (("GITHUB_ACTIONS", "false"), ("RUNNER_ENVIRONMENT", "self-hosted"),
                             ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_REF", "refs/heads/main"),
                             ("GITHUB_WORKFLOW_SHA", "c" * 40), ("GITHUB_RUN_ID", str(v.RUN)),
                             ("GITHUB_RUN_ATTEMPT", "2")):
            with self.subTest(field=field), patch.dict(v.os.environ, dict(env, **{field: value}), clear=True), \
                 patch.object(v.sys, "platform", "darwin"), patch.object(v.platform, "machine", return_value="arm64"):
                with self.assertRaises(v.Refusal): v.hosted_context(ROOT, self.root)

    def test_whole_original_completed_failure_and_exact_source_required(self):
        v.validate_run(producer())
        for key, value in (("id", True), ("id", v.RUN + 1), ("head_sha", "b" * 40),
                           ("head_branch", "main"), ("event", "push"), ("run_attempt", 2),
                           ("run_attempt", True), ("workflow_id", 1), ("status", "in_progress"),
                           ("conclusion", "success"), ("conclusion", "cancelled"),
                           ("repository", {"id": 1, "full_name": v.REPOSITORY})):
            with self.subTest(key=key), self.assertRaises(v.Refusal):
                v.validate_run(dict(producer(), **{key: value}))

    def test_original_failed_job_step_identity_times_and_complete_inventory(self):
        v.validate_jobs(jobs())
        for key, value in (("id", v.JOB + 1), ("run_id", v.RUN + 1), ("run_attempt", True),
                           ("head_sha", "b" * 40), ("name", "macos (aarch64, aarch64-apple-darwin)"),
                           ("conclusion", "success"), ("completed_at", "2026-10-14T01:23:09Z"),
                           ("labels", ["self-hosted"]), ("runner_group_id", True)):
            record = jobs(); record["jobs"][0][key] = value
            with self.subTest(key=key), self.assertRaises(v.Refusal):
                v.validate_jobs(record)
        for field, value in (("number", 23), ("conclusion", "success"), ("completed_at", "2026-10-14T01:22:48Z")):
            record = jobs(); record["jobs"][0]["steps"][0][field] = value
            with self.subTest(step_field=field), self.assertRaises(v.Refusal):
                v.validate_jobs(record)
        record = jobs(); record["total_count"] = 2
        with self.assertRaises(v.Refusal): v.validate_jobs(record)
        record = jobs(); record["jobs"] *= 2; record["total_count"] = 2
        with self.assertRaises(v.Refusal): v.validate_jobs(record)

    def test_artifact_exact_size_digest_times_source_expiry_and_attempt_attribution(self):
        v.validate_artifact(artifact(), datetime(2026, 10, 14, tzinfo=timezone.utc))
        for key, value in (("id", True), ("id", v.ARTIFACT_ID + 1), ("name", "other"),
                           ("size_in_bytes", True), ("size_in_bytes", v.ARTIFACT_BYTES + 1),
                           ("digest", "sha256:" + "a" * 64), ("expired", True),
                           ("created_at", "2026-10-14T01:22:53Z"), ("updated_at", "2026-10-14T01:22:53Z"),
                           ("expires_at", "2027-01-12T00:36:45Z")):
            with self.subTest(key=key), self.assertRaises(v.Refusal):
                v.validate_artifact(dict(artifact(), **{key: value}))
        record = artifact(); record["workflow_run"]["head_sha"] = "b" * 40
        with self.assertRaises(v.Refusal): v.validate_artifact(record)
        with self.assertRaises(v.Refusal):
            v.validate_artifact(artifact(), datetime(2028, 1, 1, tzinfo=timezone.utc))

    def observe_log(self, name, payload, exit_code, expected_bytes=None, expected_sha=None, replacement=None):
        directory = self.root / name; directory.mkdir()
        receipt = {}
        def capture(output):
            output.write(payload)
            if replacement:
                path = directory / "original-failed-intel-job.log"
                if replacement == "hardlink":
                    os.link(path, directory / "extra-link")
                elif replacement == "replace":
                    path.unlink(); path.write_bytes(b"replacement-private-sentinel")
                elif replacement == "symlink":
                    path.unlink(); path.symlink_to(directory / "unowned-target")
            return exit_code, b"", False
        with patch.object(v, "capture_job_log_transport", side_effect=capture), \
             patch.object(v, "LOG_BYTES", len(payload) if expected_bytes is None else expected_bytes), \
             patch.object(v, "LOG_SHA256", hashlib.sha256(payload).hexdigest() if expected_sha is None else expected_sha):
            try:
                v.exact_original_log(directory, receipt)
            except v.Refusal as error:
                return receipt, str(error)
        return receipt, None

    def test_log_observation_exact_success_is_only_exit_bytes_and_opaque_hash(self):
        secret = b"private-log-sentinel support://never-export-this"
        receipt, refusal = self.observe_log("exact", secret, 0)
        self.assertIsNone(refusal)
        self.assertEqual(receipt, {"originalJobLogObservation": {"exitCode": 0, "bytes": len(secret),
                               "sha256": hashlib.sha256(secret).hexdigest()},
                               "originalJobLogTransport": {"httpStatus": None, "transportCategory": None}})
        self.assertNotIn("private-log-sentinel", json.dumps(receipt))
        self.assertNotIn("support://", json.dumps(receipt))

    def test_log_zero_partial_nonzero_and_wrong_size_or_digest_remain_fatal(self):
        for name, payload, exit_code, expected_bytes, expected_sha in (
                ("empty", b"", 0, 713079, v.LOG_SHA256),
                ("nonzero-empty", b"", 1, 713079, v.LOG_SHA256),
                ("nonzero-exact", b"private-partial-sentinel", 1, None, None),
                ("wrong-size", b"private-partial-sentinel", 0, 713079, v.LOG_SHA256),
                ("wrong-hash", b"private-partial-sentinel", 0, None, "a" * 64)):
            receipt, refusal = self.observe_log(name, payload, exit_code, expected_bytes, expected_sha)
            with self.subTest(case=name):
                self.assertEqual(refusal, "exact-original-job-log-attribution")
                self.assertEqual(receipt["originalJobLogObservation"], {"exitCode": exit_code,
                                 "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
                self.assertNotIn("private-partial-sentinel", json.dumps(receipt))

    def test_log_oversize_hardlink_replacement_or_symlink_never_hash_unstable_bytes(self):
        for name, payload, replacement in (("oversize", b"x" * (v.SUPPORT.MAX_API + 1), None),
                                            ("hardlink", b"private", "hardlink"),
                                            ("replacement", b"private", "replace"),
                                            ("symlink", b"private", "symlink")):
            with patch.object(v, "sha", side_effect=AssertionError("Unsafe log bytes must not be hashed")):
                receipt, refusal = self.observe_log(name, payload, 0, replacement=replacement)
            with self.subTest(case=name):
                self.assertEqual(refusal, "original-job-log-owned-byte-bound")
                self.assertIsNone(receipt["originalJobLogObservation"]["sha256"])
                self.assertEqual(set(receipt["originalJobLogObservation"]), {"exitCode", "bytes", "sha256"})

    def test_log_read_race_retains_no_digest(self):
        directory = self.root / "read-race"; directory.mkdir(); receipt = {}
        def capture(output):
            output.write(b"private-race-sentinel")
            return 0, b"", False
        original = v.os.fstat
        calls = []
        def raced(fd):
            value = original(fd); calls.append(value)
            if len(calls) == 2:
                fields = {key: getattr(value, key) for key in
                          ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode", "st_uid", "st_nlink")}
                fields["st_mtime_ns"] += 1
                return SimpleNamespace(**fields)
            return value
        with patch.object(v, "capture_job_log_transport", side_effect=capture), patch.object(v.os, "fstat", side_effect=raced), \
             patch.object(v, "sha", side_effect=AssertionError("Raced log must not be hashed")):
            with self.assertRaisesRegex(v.Refusal, "original-job-log-read-race"):
                v.exact_original_log(directory, receipt)
        self.assertIsNone(receipt["originalJobLogObservation"]["sha256"])

    def test_http_classifier_exports_only_fixed_numeric_status_and_category(self):
        for status in (403, 404, 500, 599):
            secret = b"gh: private-token https://private.invalid/path?secret=value (HTTP " + str(status).encode() + b")\n"
            safe = v.classify_job_log_stderr(secret)
            self.assertEqual(safe, {"httpStatus": status, "transportCategory": "http-response-error"})
            self.assertNotIn("private", json.dumps(safe)); self.assertNotIn("http://", json.dumps(safe))
        null = {"httpStatus": None, "transportCategory": None}
        for data in (b"", b"private-token", b"\xff\x00", b"gh: private (HTTP 403)",
                     b"not-gh: private (HTTP 403)\n", b"gh: private (HTTP 403) trailing\n",
                     b"gh: private (HTTP 40)\n", b"gh: private (HTTP 4030)\n",
                     b"gh: private (HTTP 000)\n", b"gh: private (HTTP 600)\n",
                     b"gh: private (HTTP 403)\ngh: private (HTTP 404)\n",
                     b"unknown private-token prefix\ngh: private (HTTP 403)\n",
                     b"gh: private (HTTP 403)\nunknown private-token suffix\n",
                     b"gh: private (HTTP 403)\n\x00", b"x" * (v.MAX_LOG_STDERR + 1)):
            with self.subTest(data=data[:40]): self.assertEqual(v.classify_job_log_stderr(data), null)
        self.assertEqual(v.classify_job_log_stderr(b"gh: private (HTTP 403)\n", True), null)
        class NeverString:
            def __str__(self): raise AssertionError("Never stringify raw objects")
        self.assertEqual(v.classify_job_log_stderr(NeverString()), null)

    def test_stderr_pipe_drainer_retains_at_most_bound_and_discards_overflow(self):
        for size in (0, v.MAX_LOG_STDERR, v.MAX_LOG_STDERR + 1, v.MAX_LOG_STDERR * 8):
            child = SimpleNamespace(stderr=io.BytesIO(b"x" * size), wait=lambda timeout: 1)
            with patch.object(v.subprocess, "Popen", return_value=child) as popen:
                code, data, overflow = v.capture_job_log_transport(io.BytesIO())
            self.assertEqual(code, 1); self.assertEqual(len(data), min(size, v.MAX_LOG_STDERR))
            self.assertEqual(overflow, size > v.MAX_LOG_STDERR)
            self.assertEqual(popen.call_args.args[0], ["gh", "api", "--method", "GET", "repos/" + v.REPOSITORY
                                                     + "/actions/jobs/" + str(v.JOB) + "/logs"])
            self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.PIPE)
            self.assertEqual(popen.call_args.kwargs["bufsize"], 0)
            self.assertTrue(child.stderr.closed)

    def test_source_grounded_terminal_escape_refusal_is_exact_fixed_category_only(self):
        safe = {"httpStatus": None, "transportCategory": "terminal-escape-sequences-refused"}
        null = {"httpStatus": None, "transportCategory": None}
        literal = v.TERMINAL_ESCAPE_REFUSAL
        for data in (literal + b"\n", literal + b"\r\n", b"gh: " + literal + b"\n"):
            self.assertEqual(v.classify_job_log_stderr(data), safe)
            self.assertNotIn("--allow-escape-sequences", json.dumps(v.classify_job_log_stderr(data)))
        for data in (literal, b"private-token " + literal + b"\n", literal + b" private-token\n",
                     b"private-token https://private.invalid\n" + literal + b"\n", literal[:-1] + b"\n"):
            self.assertEqual(v.classify_job_log_stderr(data), null)
        self.assertEqual(v.classify_job_log_stderr(literal + b"\n", True), null)
        directory = self.root / "terminal-escape-refusal"; directory.mkdir(); receipt = {}
        with patch.object(v, "capture_job_log_transport", return_value=(1, literal + b"\n", False)):
            with self.assertRaisesRegex(v.Refusal, "^exact-original-job-log-attribution$"):
                v.exact_original_log(directory, receipt)
        self.assertEqual(receipt["originalJobLogTransport"], safe)
        self.assertEqual(receipt["originalJobLogObservation"], {"exitCode": 1, "bytes": 0,
                         "sha256": hashlib.sha256(b"").hexdigest()})

    def test_private_stderr_is_bounded_owned_and_not_exported_on_http_refusal(self):
        for overflow in (False, True):
            directory = self.root / ("stderr-overflow" if overflow else "stderr-http"); directory.mkdir()
            data = b"gh: private-token https://private.invalid/path?secret=value (HTTP 403)\n"
            if overflow: data += b"x" * (v.MAX_LOG_STDERR - len(data))
            receipt = {}
            with patch.object(v, "capture_job_log_transport", return_value=(1, data, overflow)):
                with self.assertRaises(v.Refusal): v.exact_original_log(directory, receipt)
            path = directory / "original-failed-intel-job.stderr"
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_uid, os.getuid())
            self.assertLessEqual(path.stat().st_size, v.MAX_LOG_STDERR)
            self.assertEqual(receipt["originalJobLogTransport"], {"httpStatus": None if overflow else 403,
                             "transportCategory": None if overflow else "http-response-error"})
            self.assertNotIn("private-token", json.dumps(receipt)); self.assertNotIn("private.invalid", json.dumps(receipt))

    def test_stderr_read_error_is_fixed_refusal_without_exception_text(self):
        class BadPipe:
            def read(self, count): raise OSError("private-stderr-secret")
            def close(self): pass
        child = SimpleNamespace(stderr=BadPipe(), wait=lambda timeout: 1, kill=lambda: None)
        with patch.object(v.subprocess, "Popen", return_value=child):
            with self.assertRaisesRegex(v.Refusal, "^original-job-log-stderr-capture-failed$"):
                v.capture_job_log_transport(io.BytesIO())

    def test_owned_gh_startup_wait_and_timeout_failures_cleanup_and_preserve_primary(self):
        for stage in ("constructor", "startup", "wait", "timeout", "secondary-cleanup-fails"):
            primary = subprocess.TimeoutExpired("private-timeout-sentinel", 120) if stage == "timeout" else \
                      OSError("private-wait-sentinel") if stage in ("wait", "secondary-cleanup-fails") else \
                      RuntimeError("private-startup-sentinel")
            events = []
            class Pipe:
                def close(self):
                    events.append("pipe-close")
                    if stage == "secondary-cleanup-fails": raise OSError("private-secondary-pipe")
            def wait(timeout):
                events.append(("wait", timeout))
                if timeout == 120: raise primary
                if stage == "secondary-cleanup-fails": raise subprocess.TimeoutExpired("private-secondary-wait", 5)
                return -9
            def kill():
                events.append("kill")
                if stage == "secondary-cleanup-fails": raise OSError("private-secondary-kill")
            class Reader:
                def start(self):
                    events.append("reader-start")
                    if stage == "startup": raise primary
                def join(self, timeout): events.append(("join", timeout))
                def is_alive(self): return False
            def reader(*args, **kwargs):
                if stage == "constructor": raise primary
                return Reader()
            child = SimpleNamespace(stderr=Pipe(), wait=wait, kill=kill)
            with self.subTest(stage=stage), patch.object(v.subprocess, "Popen", return_value=child), \
                 patch.object(v.threading, "Thread", side_effect=reader):
                with self.assertRaises(type(primary)) as raised:
                    v.capture_job_log_transport(io.BytesIO())
                self.assertIs(raised.exception, primary)
                self.assertIn("kill", events)
                self.assertIn(("wait", 5), events)
                self.assertIn("pipe-close", events)
                if stage != "constructor": self.assertIn(("join", 5), events)

    def test_archive_intel_layout_and_frozen_traversal_duplicate_link_controls(self):
        base = [entry(v.OUTER_APP, b"nested"), entry(v.OUTER_RUNTIME, b"{}")]
        with zipfile.ZipFile(io.BytesIO(archive(base))) as zipped:
            self.assertEqual(v.validate_outer(zipped), {})
        for added in (entry("../escape"), entry("/absolute"), entry(v.OUTER_APP.upper()),
                      entry(v.OUTER_APP), entry("aarch64/runtime/runtime.json"),
                      ("x86_64/link", b"/tmp/escape", stat.S_IFLNK | 0o777)):
            with self.subTest(name=added[0]), zipfile.ZipFile(io.BytesIO(archive(base + [added]))) as zipped:
                with self.assertRaises((v.Refusal, v.SUPPORT.Refusal)): v.validate_outer(zipped)
        with self.assertRaises(v.SUPPORT.Refusal):
            v.SUPPORT.unique_json(b'{"id":1,"id":2}')

    def test_safe_cleanup_exact_reason_and_subphase_pairs_reject_raw_text(self):
        for code, phase in v.CLEANUP_CODES.items():
            record = {"reasonCategory": "runtime", "errorType": "RuntimeError", "reasonCode": code, "subphase": phase}
            self.assertEqual(v.safe_error(record, True), record)
            with self.assertRaises(v.Refusal): v.safe_error(dict(record, subphase="unknown"), True)
            with self.assertRaises(v.Refusal): v.safe_error(dict(record, reason="support://private-secret"), True)
        with self.assertRaises(v.Refusal):
            v.safe_error({"reasonCategory": "runtime", "errorType": "RuntimeError",
                          "reasonCode": "private-secret", "subphase": "endpoint-cleanup"}, True)

    def test_safe_schema_booleans_counts_unknown_fields_and_four_hashes_fail_closed(self):
        clean = v.safe_facts(facts()); v.require_runtime_pass(0, clean)
        for key, value in (("syntheticInvite", 1), ("remoteDesktopConnected", True),
                           ("customerAcceptTested", True), ("raw", "support://private")):
            with self.subTest(key=key), self.assertRaises(v.Refusal):
                v.safe_facts(dict(facts(), **{key: value}))
        for key, value in (("guiPid", True), ("inventorySamples", True), ("stabilitySamples", 100001),
                           ("scenario", "two-support"), ("unknown", "private"),
                           ("criticalHashes", {"Contents/MacOS/Mixel-Remote": "a" * 64})):
            record = facts(); record["scenarios"][0][key] = value
            with self.subTest(row_key=key), self.assertRaises(v.Refusal): v.safe_facts(record)

    def test_cleanup_failure_and_nonzero_or_inconsistent_exit_are_fatal(self):
        for result in (1, -9, True):
            with self.subTest(result=result), self.assertRaises(v.Refusal):
                v.require_runtime_pass(result, v.safe_facts(facts()))
        record = facts(); record["scenarios"][0]["endpointCleanupFailures"] = [{
            "endpoint": "incoming", "reasonCategory": "runtime", "errorType": "RuntimeError",
            "reasonCode": "pid-marker-unsafe", "subphase": "pid-marker-read"}]
        safe = v.safe_facts(record)
        self.assertEqual(safe["scenarios"][0]["endpointCleanupFailures"][0]["reasonCode"], "pid-marker-unsafe")
        with self.assertRaises(v.Refusal): v.require_runtime_pass(0, safe)
        for field in ("ownedProcessesStopped", "visibleGuiAndAttendedV2", "oneRetainedGuiDuringBoundedStability"):
            record = facts(); record["scenarios"][0][field] = False
            with self.subTest(field=field), self.assertRaises(v.Refusal):
                v.require_runtime_pass(0, v.safe_facts(record))

    def test_native_caller_receives_original_arguments_and_no_provider_tokens(self):
        with patch.dict(os.environ, {"GH_TOKEN": "private", "GITHUB_TOKEN": "private", "HOME": "/fixture"}):
            with patch.object(v.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                self.assertEqual(v.run_caller(ROOT, ["--original-desktop", "--app", "/fixture.app"], "exit", 420), 0)
                command = run.call_args.args[0]; options = run.call_args.kwargs
                self.assertEqual(command[3:], ["--original-desktop", "--app", "/fixture.app"])
                self.assertNotIn("GH_TOKEN", options["env"]); self.assertNotIn("GITHUB_TOKEN", options["env"])
                self.assertEqual(options["stdout"], subprocess.DEVNULL)
                self.assertEqual(options["stderr"], subprocess.DEVNULL)
                self.assertEqual(options["timeout"], 420)
        with patch.object(v.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 420)):
            with self.assertRaises(subprocess.TimeoutExpired): v.run_caller(ROOT, [], "exit", 420)

    def test_fresh_and_post_cleanup_never_delete_or_adopt_preexisting_state(self):
        with patch.object(v.os.path, "lexists", return_value=True), patch.object(v, "process_inventory", return_value=[]):
            with self.assertRaises(v.Refusal): v.stopped_runtime()
        with patch.object(v.os.path, "lexists", return_value=False), patch.object(v, "process_inventory", return_value=[123]):
            with self.assertRaises(v.Refusal): v.stopped_runtime()
        with patch.object(v.os.path, "lexists", return_value=False), patch.object(v, "process_inventory", return_value=[]):
            v.stopped_runtime()
        with patch.object(v, "capture", return_value=(b"123 S /fixture/Mixel-Remote.app/Contents/MacOS/Mixel-Remote\n", b"")):
            self.assertEqual(v.process_inventory(), [123])

    def test_initial_existing_production_namespace_is_refused_without_deletion(self):
        for name in ("mixel-remote-attended-v2-", "mixel-remote-gui-launch-v1-"):
            existing = Path("/private/tmp/" + name + str(os.getuid()))
            with self.subTest(namespace=name), patch.dict(v.os.environ, {"HOME": str(self.root)}), \
                 patch.object(v.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(self.root))), \
                 patch.object(v.os.path, "lexists", side_effect=lambda path: path == existing), \
                 patch.object(v, "process_inventory", return_value=[]), \
                 patch.object(v.Path, "unlink", side_effect=AssertionError("Existing state must never be deleted")):
                with self.assertRaises(v.Refusal): v.fresh_runtime_state()

    def test_exact_immutable_app_identity_minimum_and_critical_hashes_are_required(self):
        app = self.root / v.APP; (app / "Contents").mkdir(parents=True)
        info = {"CFBundleIdentifier": "ch.mixel.remote", "CFBundleExecutable": "Mixel-Remote",
                "CFBundleShortVersionString": "1.4.6", "CFBundleVersion": "64", "LSMinimumSystemVersion": "10.14"}
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        expected = {}
        for name in v.CRITICAL_HASHES:
            path = app / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(name.encode())
            expected[name] = hashlib.sha256(name.encode()).hexdigest()
        with patch.object(v, "CRITICAL_HASHES", expected):
            self.assertEqual(v.verify_app(app), expected)
            (app / "Contents/MacOS/service").write_bytes(b"different")
            with self.assertRaises(v.Refusal): v.verify_app(app)
            info["LSMinimumSystemVersion"] = "12.3"
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
            with self.assertRaises(v.Refusal): v.verify_app(app)

    def test_workflow_has_no_dispatch_inputs_publication_or_unsafe_output_paths(self):
        text = (ROOT / v.DIAGNOSTIC_WORKFLOW).read_text()
        self.assertNotIn("inputs:", text)
        self.assertIn("actions: read\n  contents: read", text)
        self.assertIn("ref: " + v.SOURCE, text)
        self.assertIn("github.event_name == 'workflow_dispatch'", text)
        self.assertIn("github.run_attempt == 1", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertEqual(text.count("persist-credentials: false"), 3)
        self.assertEqual(text.count("--evidence-dir"), 2)
        output = text.split("path: |", 1)[1].split("if-no-files-found:", 1)[0]
        self.assertEqual(output.split(), ["failed-intel-concurrency-safe-facts/validation-receipt.json",
                                         "failed-intel-concurrency-safe-facts/concurrency-diagnostic-safe-facts.json"])
        for forbidden in ("build.yml@", "notarize-macos", "sign-and-publish", "secrets.", "create-dmg"):
            self.assertNotIn(forbidden, text)

    def test_literal_main_requires_all_gates_and_preserves_desktop_tray_concurrency_order(self):
        tree = ast.parse(SOURCE.read_bytes()); main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        calls = [node for node in ast.walk(main) if isinstance(node, ast.Call)]
        caller = sorted([node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "run_caller"], key=lambda node: node.lineno)
        self.assertEqual(len(caller), 3)
        self.assertEqual(ast.literal_eval(caller[0].args[1].elts[0]), "--original-desktop")
        self.assertEqual(ast.literal_eval(caller[1].args[1].elts[0]), "--original-tray")
        self.assertEqual(ast.literal_eval(caller[2].args[1].elts[0]), "--app")
        for name in ("validate_run", "validate_jobs", "validate_artifact", "source_contract", "fresh_runtime_state", "verify_app"):
            lines = [node.lineno for node in calls if isinstance(node.func, ast.Name) and node.func.id == name]
            self.assertTrue(lines and min(lines) < caller[0].lineno, name)
        self.assertIn('except subprocess.TimeoutExpired:', SOURCE.read_text())
        self.assertIn('"native-child-timeout-cleanup-unverified"', SOURCE.read_text())
        self.assertIn('require_runtime_pass(result, facts)', SOURCE.read_text())
        self.assertIn('stopped_runtime()', SOURCE.read_text())

    def mock_replay(self, case):
        run_root = self.root / case; run_root.mkdir()
        producer_root = run_root / "producer"; producer_root.mkdir()
        private, safe = run_root / "private", run_root / "safe"
        payloads = {name: name.encode() for name in v.CRITICAL_HASHES}
        critical = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
        info = plistlib.dumps({"CFBundleIdentifier": "ch.mixel.remote", "CFBundleExecutable": "Mixel-Remote",
                              "CFBundleShortVersionString": "1.4.6", "CFBundleVersion": "64",
                              "LSMinimumSystemVersion": "10.14"})
        inner = archive([entry(v.APP + "/Contents/Info.plist", info),
                         *[entry(v.APP + "/" + name, data) for name, data in payloads.items()]])
        embedded = {"schema": 1, "scope": "macos-launch-smoke-diagnostic-not-customer-release",
                    "runner_context": {"source_sha": v.SOURCE, "run_id": v.RUN}, "outcome": "success",
                    "privacy_check_passed": True, "customer_app_qualified": False,
                    "screen_control_qualified": False, "publication": False,
                    "executable_sha256": critical["Contents/MacOS/Mixel-Remote"],
                    "diagnostic_app_archive": {"name": "signed-stapled-diagnostic-app.zip",
                                               "bytes": len(inner), "sha256": hashlib.sha256(inner).hexdigest()}}
        outer = archive([entry(v.OUTER_APP, inner), entry(v.OUTER_RUNTIME, json.dumps(embedded).encode())])
        events = []
        original_log = v.exact_original_log
        def job_log_transport(output):
            events.append("job-log-transport")
            return 1, b"gh: private-token https://private.invalid/path?secret=value (HTTP 403)\n", False

        def metadata(suffix):
            events.append("metadata")
            if "/attempts/1/jobs" in suffix: return jobs()
            if "/artifacts/" in suffix: return artifact()
            result = producer()
            if case == "producer-in-progress": result.update(status="in_progress", conclusion=None)
            return result

        def native(command, **kwargs):
            if command[0] == "gh":
                if "/actions/jobs/" in command[-1]:
                    events.append("job-log-transport")
                    self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                    return subprocess.CompletedProcess(command, 1)
                events.append("download")
                self.assertEqual(command[-1], "repos/" + v.REPOSITORY + "/actions/artifacts/" + str(v.ARTIFACT_ID) + "/zip")
                kwargs["stdout"].write(outer)
            elif command[0] == "ditto":
                events.append("extract")
                destination = Path(command[-1])
                with zipfile.ZipFile(io.BytesIO(inner)) as zipped:
                    for item in zipped.infolist():
                        path = destination / item.filename; path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(zipped.read(item))
            elif command[0] in ("codesign", "xcrun", "spctl"):
                events.append("signature")
                if command[0] == "codesign" and "--display" in command:
                    return subprocess.CompletedProcess(command, 0, b"", b"TeamIdentifier=5277F8NDH4\nCodeDirectory v=20500 flags=0x10000(runtime) hashes=1\n")
            elif command[0] == "arch":
                events.append("existing-rosetta")
            elif len(command) > 2 and command[2].endswith("verify-macos-bundle-minimum.py"):
                events.append("minimum")
                Path(command[command.index("--output") + 1]).write_text(json.dumps({"status": "passed",
                    "architecture": "x86_64", "advertised_minimum": "10.14", "binary_count": 4}))
            elif len(command) > 2 and command[2].endswith("test-support-launch-macos-concurrency.py"):
                arguments = command[3:]
                mode = "desktop" if "--original-desktop" in arguments else "tray" if "--original-tray" in arguments else "concurrency"
                events.append(mode)
                self.assertNotIn("GH_TOKEN", kwargs["env"])
                self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
                self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                if case == "desktop-fails" and mode == "desktop": return subprocess.CompletedProcess(command, 1)
                if case == "tray-fails" and mode == "tray": return subprocess.CompletedProcess(command, 1)
                if case == "caller-times-out" and mode == "concurrency": raise subprocess.TimeoutExpired(command, 420)
                output = Path(arguments[arguments.index("--diagnostics" if mode == "desktop" else "--proofs") + 1])
                output.mkdir()
                if mode == "desktop":
                    result = {"outcome": "success", "privacy_check_passed": True,
                              "executable_sha256": critical["Contents/MacOS/Mixel-Remote"],
                              "customer_app_qualified": False, "screen_control_qualified": False, "publication": False}
                    name = "runtime.json"
                elif mode == "tray":
                    result = {"passed": True, "ownedProcessesStopped": True, "actualTrayDispatchObserved": True,
                              "visibleGuiAndAttendedV2": True, "syntheticInvite": True,
                              "customerAcceptTested": False, "remoteDesktopConnected": False,
                              "criticalHashes": critical}
                    name = "tray-runtime-safe-facts.json"
                else:
                    result = facts(); name = "concurrency-runtime-safe-facts.json"
                    if case == "cleanup-fails":
                        result["passed"] = False; result["scenarios"] = result["scenarios"][:1]
                        result["scenarios"][0]["passed"] = False
                        result["failure"] = {"reasonCategory": "runtime", "errorType": "RuntimeError"}
                        result["scenarios"][0]["endpointCleanupFailures"] = [{"endpoint": "incoming",
                            "reasonCategory": "runtime", "errorType": "RuntimeError",
                            "reasonCode": "pid-marker-unsafe", "subphase": "pid-marker-read"}]
                (output / name).write_text(json.dumps(result))
                return subprocess.CompletedProcess(command, 1 if case == "cleanup-fails" and mode == "concurrency" else 0)
            else:
                raise AssertionError("Unexpected mocked command: " + repr(command))
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with patch.object(v, "CRITICAL_HASHES", critical), patch.object(v, "ARTIFACT_BYTES", len(outer)), \
             patch.object(v, "ARTIFACT_SHA256", hashlib.sha256(outer).hexdigest()), \
             patch.object(v, "hosted_context", return_value="b" * 40), patch.object(v, "api", side_effect=metadata), \
             patch.object(v, "exact_original_log", side_effect=lambda private, receipt:
                          original_log(private, receipt) if case == "job-log-transport-fails"
                          else events.append("exact-original-log")), \
             patch.object(v, "capture_job_log_transport", side_effect=job_log_transport), \
             patch.object(v, "fresh_runtime_state", side_effect=lambda: events.append("fresh-runtime")), \
             patch.object(v, "stopped_runtime", side_effect=lambda: events.append("stopped-runtime")), \
             patch.object(v.subprocess, "run", side_effect=native):
            arguments = ["--diagnostic-checkout", str(ROOT), "--producer-checkout", str(producer_root),
                         "--destination", str(private), "--safe-output", str(safe)]
            if case == "success":
                self.assertEqual(v.main(arguments), 0)
            else:
                with self.assertRaises(v.Refusal): v.main(arguments)
        return events, safe

    def test_complete_mock_replay_passes_only_with_exact_order_and_final_cleanup(self):
        events, safe = self.mock_replay("success")
        children = [event for event in events if event in {"desktop", "tray", "concurrency"}]
        self.assertEqual(children, ["desktop", "tray", "concurrency"])
        self.assertLess(events.index("exact-original-log"), events.index("download"))
        self.assertLess(events.index("minimum"), events.index("desktop"))
        self.assertLess(events.index("fresh-runtime"), events.index("desktop"))
        self.assertEqual(events[-1], "stopped-runtime")
        receipt = json.loads((safe / "validation-receipt.json").read_bytes())
        self.assertTrue(receipt["runtimePassed"])
        self.assertFalse(receipt["customerAppQualified"])
        self.assertFalse(receipt["publication"])
        self.assertEqual({path.name for path in safe.iterdir()},
                         {"validation-receipt.json", "concurrency-diagnostic-safe-facts.json"})

    def test_mock_incomplete_whole_producer_never_downloads_or_executes(self):
        events, safe = self.mock_replay("producer-in-progress")
        self.assertEqual(events, ["metadata"])
        self.assertFalse(safe.exists())

    def test_mock_log_transport_refusal_has_safe_observation_and_never_downloads_app(self):
        events, safe = self.mock_replay("job-log-transport-fails")
        self.assertEqual(events, ["metadata", "metadata", "metadata", "job-log-transport"])
        receipt = json.loads((safe / "validation-receipt.json").read_bytes())
        self.assertEqual(receipt["originalJobLogObservation"], {"exitCode": 1, "bytes": 0,
                         "sha256": hashlib.sha256(b"").hexdigest()})
        self.assertEqual(receipt["refusalCategory"], "exact-original-job-log-attribution")
        self.assertEqual(receipt["originalJobLogTransport"], {"httpStatus": 403, "transportCategory": "http-response-error"})
        self.assertNotIn("private-token", json.dumps(receipt)); self.assertNotIn("private.invalid", json.dumps(receipt))
        self.assertFalse(receipt["nativeExecuted"])
        self.assertFalse(receipt["validationPassed"])
        self.assertFalse(receipt["runtimePassed"])

    def test_mock_prerequisite_failure_timeout_and_cleanup_failure_are_fatal_and_preserved(self):
        for case, children in (("desktop-fails", ["desktop"]), ("tray-fails", ["desktop", "tray"]),
                               ("caller-times-out", ["desktop", "tray", "concurrency"]),
                               ("cleanup-fails", ["desktop", "tray", "concurrency"])):
            events, safe = self.mock_replay(case)
            with self.subTest(case=case):
                self.assertEqual([event for event in events if event in {"desktop", "tray", "concurrency"}], children)
                receipt = json.loads((safe / "validation-receipt.json").read_bytes())
                self.assertFalse(receipt["runtimePassed"])
                self.assertIn("refusalCategory", receipt)
                self.assertNotIn("stopped-runtime", events)
                if case == "cleanup-fails":
                    fact = json.loads((safe / "concurrency-diagnostic-safe-facts.json").read_bytes())
                    self.assertFalse(fact["passed"])
                    self.assertTrue(fact["scenarios"][0]["ownedProcessesStopped"])
                    self.assertEqual(fact["scenarios"][0]["endpointCleanupFailures"][0]["reasonCode"], "pid-marker-unsafe")
                if case == "caller-times-out":
                    self.assertEqual(receipt["refusalCategory"], "native-child-timeout-cleanup-unverified")


if __name__ == "__main__":
    unittest.main(verbosity=2)
