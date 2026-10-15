#!/usr/bin/env python3
"""Local source/mock controls: no artifact fetch, native command or app launch."""
import ast
import copy
from datetime import datetime, timezone
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import stat
import struct
import subprocess
import tempfile
import types
import unittest
import warnings
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scripts/diagnose-macos-failed-app.py"
v = types.ModuleType("failed_app_diagnostic")
v.__file__ = str(SOURCE)
exec(compile(SOURCE.read_bytes(), str(SOURCE), "exec"), v.__dict__)
DRIVER_SOURCE = ROOT / "scripts/run-failed-macos-tray-diagnostic.py"
d = types.ModuleType("privacy_driver")
d.__file__ = str(DRIVER_SOURCE)
exec(compile(DRIVER_SOURCE.read_bytes(), str(DRIVER_SOURCE), "exec"), d.__dict__)


def run():
    return {"id": v.RUN, "head_sha": v.SOURCE, "head_branch": v.BRANCH,
            "event": "workflow_dispatch", "path": v.WORKFLOW, "workflow_id": v.WORKFLOW_ID,
            "run_attempt": 1, "status": "completed", "conclusion": "failure",
            "repository": {"id": v.REPOSITORY_ID, "full_name": v.REPOSITORY},
            "head_repository": {"id": v.REPOSITORY_ID, "full_name": v.REPOSITORY}}


def jobs():
    return {"total_count": 1, "jobs": [{"id": v.JOB, "run_id": v.RUN, "run_attempt": 1,
             "name": v.JOB_NAME, "status": "completed", "conclusion": "failure",
             "head_sha": v.SOURCE, "head_branch": v.BRANCH,
             "started_at": "2026-10-13T17:45:22Z", "completed_at": "2026-10-13T18:32:29Z",
             "steps": [{"name": v.PACKAGE_STEP, "status": "completed", "conclusion": "failure",
                        "started_at": "2026-10-13T18:31:00Z", "completed_at": "2026-10-13T18:32:02Z"},
                       {"name": v.UPLOAD_STEP, "status": "completed", "conclusion": "success",
                        "started_at": "2026-10-13T18:32:05Z", "completed_at": "2026-10-13T18:32:08Z"}]}]}


def artifact():
    return {"id": v.ARTIFACT_ID, "name": v.ARTIFACT_NAME, "size_in_bytes": v.ARTIFACT_BYTES,
            "digest": "sha256:" + v.ARTIFACT_SHA256, "created_at": v.ARTIFACT_CREATED,
            "updated_at": v.ARTIFACT_CREATED, "expires_at": v.ARTIFACT_EXPIRES, "expired": False,
            "workflow_run": {"id": v.RUN, "head_sha": v.SOURCE, "head_branch": v.BRANCH,
                             "repository_id": v.REPOSITORY_ID, "head_repository_id": v.REPOSITORY_ID}}


def facts():
    return {"observedUtc": "2026-10-13T14:07:33+00:00", "syntheticInvite": True,
            "customerAcceptTested": False, "remoteDesktopConnected": False,
            "actualTrayDispatchObserved": False, "visibleGuiAndAttendedV2": False,
            "ownedProcessesStopped": True, "passed": False, "trayPid": 101,
            "runtimeFailure": {"phase": "os-url-activation",
                               "reason": "OS URL activation bypassed the existing tray process"},
            "failure": "A Mixel IPC endpoint is already active; refusing tray fixture"}


def empty_kernel_facts():
    tray = types.SimpleNamespace(gui_owner=lambda *args: 102, desktop=types.SimpleNamespace())
    return d.observe_kernel_identity(tray)


def zip_bytes(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, data, mode in entries:
            item = zipfile.ZipInfo(name)
            item.create_system = 3
            item.external_attr = mode << 16
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Duplicate name:", category=UserWarning)
                zipped.writestr(item, data)
    return output.getvalue()


def entry(name, data=b"fixture"):
    return (name, data, stat.S_IFREG | 0o600)


def nested_entries():
    info = {"CFBundleIdentifier": "ch.mixel.remote", "CFBundleExecutable": "Mixel-Remote",
            "CFBundleShortVersionString": "1.4.6", "CFBundleVersion": "64", "LSMinimumSystemVersion": "12.3"}
    return [entry(v.APP + "/Contents/Info.plist", plistlib.dumps(info)),
            *[entry(v.APP + "/" + name, name.encode()) for name in v.CRITICAL]]


class DiagnosticControls(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mixel-failed-app-source-control-")
        self.root = Path(self.directory.name).resolve()
        self.native = patch.object(v.subprocess, "run", side_effect=AssertionError("Native/network operation forbidden in local controls"))
        self.native.start()

    def tearDown(self):
        self.native.stop()
        self.directory.cleanup()

    def test_exact_terminal_failed_producer_and_job_are_required(self):
        v.validate_run(run())
        with self.assertRaises(v.Refusal):
            v.validate_run(dict(run(), status="in_progress", conclusion=None))
        job, upload = v.validate_jobs(jobs())
        self.assertEqual(job["id"], v.JOB)
        v.validate_artifact(artifact(), upload, datetime(2026, 10, 13, tzinfo=timezone.utc))
        with self.assertRaises(v.Refusal):
            v.validate_run(dict(run(), status="completed", conclusion="success"))

    def test_wrong_run_source_event_repository_or_attempt_is_refused(self):
        mutations = (("id", True), ("id", v.RUN + 1), ("head_sha", "b" * 40),
                     ("head_branch", "main"), ("event", "push"), ("path", "other.yml"),
                     ("workflow_id", v.WORKFLOW_ID + 1), ("run_attempt", 2),
                     ("run_attempt", True), ("repository", {"id": 1, "full_name": v.REPOSITORY}),
                     ("head_repository", {"id": v.REPOSITORY_ID, "full_name": "other/repo"}))
        for key, value in mutations:
            with self.subTest(field=key, value=value), self.assertRaises(v.Refusal):
                v.validate_run(dict(run(), **{key: value}))
        for key, value in (("id", v.JOB + 1), ("run_attempt", 2), ("run_id", v.RUN + 1),
                           ("head_sha", "b" * 40), ("conclusion", "success"), ("status", "in_progress")):
            altered = jobs(); altered["jobs"][0][key] = value
            with self.subTest(job_field=key), self.assertRaises(v.Refusal):
                v.validate_jobs(altered)
        altered = jobs(); altered["jobs"][0]["steps"][0]["conclusion"] = "success"
        with self.assertRaises(v.Refusal):
            v.validate_jobs(altered)

    def test_wrong_artifact_identity_digest_size_source_expiry_and_job_window_are_refused(self):
        _, upload = v.validate_jobs(jobs())
        for key, value in (("id", v.ARTIFACT_ID + 1), ("id", True), ("name", "macos-aarch64"),
                           ("digest", "sha256:" + "b" * 64), ("size_in_bytes", v.ARTIFACT_BYTES + 1),
                           ("size_in_bytes", True), ("expired", True), ("expires_at", "2026-01-01T00:00:00Z"),
                           ("created_at", "2026-10-13T16:23:50Z"), ("updated_at", "2026-10-13T16:23:50Z")):
            with self.subTest(field=key), self.assertRaises(v.Refusal):
                v.validate_artifact(dict(artifact(), **{key: value}), upload)
        for key, value in (("id", v.RUN + 1), ("head_sha", "b" * 40), ("head_branch", "main"),
                           ("repository_id", 1), ("head_repository_id", 1)):
            altered = artifact(); altered["workflow_run"][key] = value
            with self.subTest(source_field=key), self.assertRaises(v.Refusal):
                v.validate_artifact(altered, upload)
        with self.assertRaises(v.Refusal):
            v.validate_artifact(artifact(), upload, datetime(2028, 1, 1, tzinfo=timezone.utc))
        late_upload = dict(upload, started_at="2026-10-13T16:23:47Z", completed_at="2026-10-13T16:23:48Z")
        with self.assertRaises(v.Refusal):
            v.validate_artifact(artifact(), late_upload)

    def test_duplicate_json_and_incomplete_or_ambiguous_job_inventory_are_refused(self):
        with self.assertRaises(v.Refusal):
            v.unique_json(b'{"source":1,"source":2}')
        for value in ({"total_count": 2, "jobs": jobs()["jobs"]},
                      {"total_count": 2, "jobs": jobs()["jobs"] * 2},
                      {"total_count": True, "jobs": jobs()["jobs"]}):
            with self.assertRaises(v.Refusal):
                v.validate_jobs(value)

    def test_outer_zip_rejects_traversal_duplicates_case_alias_and_symlink(self):
        base = [entry(v.OUTER_APP, b"zip"), entry(v.OUTER_RUNTIME, b"{}")]
        additions = [entry("../escape"), entry("aarch64/../escape"), entry("/absolute"),
                     entry("aarch64\\escape"), entry(v.OUTER_APP), entry(v.OUTER_APP.upper()),
                     ("aarch64/link", b"/tmp/escape", stat.S_IFLNK | 0o777)]
        for addition in additions:
            with self.subTest(name=addition[0]), zipfile.ZipFile(io.BytesIO(zip_bytes(base + [addition]))) as zipped:
                with self.assertRaises(v.Refusal):
                    v.validate_outer(zipped)
        with zipfile.ZipFile(io.BytesIO(zip_bytes(base))) as zipped:
            self.assertEqual(v.validate_outer(zipped), {})
        raw = zip_bytes(nested_entries() + [entry(v.APP + "/Contents/nulXname")])
        raw = raw.replace(b"nulXname", b"nul\0name")
        with zipfile.ZipFile(io.BytesIO(raw)) as zipped, self.assertRaises(v.Refusal):
            v.validate_nested(zipped)

    def test_embedded_source_archive_and_executable_bindings_are_required(self):
        nested = self.root / "owned.zip"; nested.write_bytes(b"owned archive")
        record = {"schema": 1, "scope": "macos-launch-smoke-diagnostic-not-customer-release",
                  "runner_context": {"source_sha": v.SOURCE, "run_id": v.RUN}, "outcome": "success",
                  "privacy_check_passed": True, "customer_app_qualified": False,
                  "screen_control_qualified": False, "publication": False, "executable_sha256": "a" * 64,
                  "diagnostic_app_archive": {"name": nested.name.replace("owned.zip", "signed-stapled-diagnostic-app.zip"),
                                             "bytes": nested.stat().st_size, "sha256": v.file_digest(nested)}}
        self.assertEqual(v.validate_embedded(record, nested), "a" * 64)
        for field, value in (("runner_context", {"source_sha": "b" * 40, "run_id": v.RUN}),
                             ("privacy_check_passed", False), ("publication", True), ("outcome", "failure"),
                             ("executable_sha256", "invalid")):
            with self.subTest(field=field), self.assertRaises(v.Refusal):
                v.validate_embedded(dict(record, **{field: value}), nested)
        for field, value in (("name", "other.zip"), ("bytes", 1), ("sha256", "b" * 64)):
            altered = copy.deepcopy(record); altered["diagnostic_app_archive"][field] = value
            with self.subTest(archive_field=field), self.assertRaises(v.Refusal):
                v.validate_embedded(altered, nested)

    def test_nested_framework_links_remain_internal_and_archive_bytes_unchanged(self):
        root = v.APP + "/Contents/Frameworks/Test.framework"
        entries = nested_entries() + [entry(root + "/Versions/A/Test", b"framework"),
                  (root + "/Versions/Current", b"A", stat.S_IFLNK | 0o777),
                  (root + "/Test", b"Versions/Current/Test", stat.S_IFLNK | 0o777)]
        data = zip_bytes(entries); before = hashlib.sha256(data).hexdigest()
        with zipfile.ZipFile(io.BytesIO(data)) as zipped:
            self.assertEqual(len(v.validate_nested(zipped)), len(entries))
        self.assertEqual(hashlib.sha256(data).hexdigest(), before)

    def test_ditto_appledouble_metadata_is_bounded_and_targets_actual_app_nodes(self):
        header = struct.pack(">II16sHIII", 0x00051607, 0x00020000, b"\0" * 16, 1, 9, 38, 4) + b"meta"
        valid = [entry("__MACOSX/._" + v.APP, header),
                 entry("__MACOSX/" + v.APP + "/Contents/._Info.plist", header)]
        with zipfile.ZipFile(io.BytesIO(zip_bytes(nested_entries() + valid))) as zipped:
            v.validate_nested(zipped)
        wrong = [entry("__MACOSX/" + v.APP + "/Contents/arbitrary", header),
                 entry("__MACOSX/" + v.APP + "/Contents/._missing", header),
                 entry("__MACOSX/._" + v.APP, b"not AppleDouble"),
                 entry("__MACOSX/._" + v.APP, header.replace(b"\x00\x05\x16\x07", b"\x00\x05\x16\x00", 1)),
                 entry("__MACOSX/._" + v.APP, header[:26] + struct.pack(">III", 9, 999, 4) + b"meta")]
        for extra in wrong:
            with self.subTest(name=extra[0]), zipfile.ZipFile(io.BytesIO(zip_bytes(nested_entries() + [extra]))) as zipped:
                with self.assertRaises(v.Refusal):
                    v.validate_nested(zipped)

    def test_nested_zip_refuses_link_escape_cycle_missing_target_and_child_below_link(self):
        root = v.APP + "/Contents/Frameworks/Test.framework"
        cases = [[(root + "/escape", b"../../../../outside", stat.S_IFLNK | 0o777)],
                 [(root + "/escape", b"/etc/passwd", stat.S_IFLNK | 0o777)],
                 [(root + "/missing", b"absent", stat.S_IFLNK | 0o777)],
                 [(root + "/a", b"b", stat.S_IFLNK | 0o777), (root + "/b", b"a", stat.S_IFLNK | 0o777)],
                 [(root + "/alias", b"target", stat.S_IFLNK | 0o777), entry(root + "/alias/file"), entry(root + "/target")],
                 [entry("other.app/Contents/MacOS/program")],
                 [("__MACOSX/" + v.APP + "/link", b"../../outside", stat.S_IFLNK | 0o777)]]
        for extra in cases:
            with self.subTest(extra=extra), zipfile.ZipFile(io.BytesIO(zip_bytes(nested_entries() + extra))) as zipped:
                with self.assertRaises(v.Refusal):
                    v.validate_nested(zipped)

    def test_signature_requires_all_actual_commands_team_and_runtime(self):
        valid = "TeamIdentifier=" + v.TEAM + "\nCodeDirectory v=20500 size=42 flags=0x10000(runtime) hashes=1\n"
        calls = []
        def mocked(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, b"", valid.encode() if "--display" in command else b"")
        with patch.object(v.subprocess, "run", side_effect=mocked):
            v.verify_signatures(self.root / v.APP)
        self.assertEqual([command[0] for command in calls], ["codesign", "xcrun", "spctl", "codesign"])
        for text in (valid.replace(v.TEAM, "AAAAAAAAAA"), valid + "TeamIdentifier=" + v.TEAM + "\n",
                     valid.replace("0x10000(runtime)", "0x0")):
            with self.assertRaises(v.Refusal):
                v.signature_identity(text)
        for failing in ("codesign", "xcrun", "spctl"):
            def refusal(command, **kwargs):
                return subprocess.CompletedProcess(command, 1 if command[0] == failing else 0, b"", valid.encode())
            with self.subTest(command=failing), patch.object(v.subprocess, "run", side_effect=refusal), self.assertRaises(v.Refusal):
                v.verify_signatures(self.root / v.APP)

    def test_original_masked_failure_phase_and_cleanup_refusal_are_separate_safe_facts(self):
        result = v.safe_runtime_facts(facts())
        self.assertEqual(result["primaryFailure"]["phase"], "os-url-activation")
        self.assertEqual(result["primaryFailure"]["reason"], facts()["runtimeFailure"]["reason"])
        self.assertEqual(result["cleanupFailure"]["reason"], facts()["failure"])
        self.assertFalse(result["passed"])
        self.assertFalse(result["customerAppQualified"])
        self.assertFalse(result["publication"])
        with self.assertRaises(v.Refusal):
            v.safe_runtime_facts(dict(facts(), remoteDesktopConnected=True))

    def test_privacy_guard_never_emits_uri_key_or_unrecognized_error_snippet(self):
        value = facts()
        value["runtimeFailure"] = {"phase": "tray-opened-dispatch", "reason": "Tray fixture log contains invitation credentials"}
        value["privateSnippet"] = "mixel-remote://support/?invite=inv_123 apikey=secret-value bearer abc"
        value["failure"] = "unexpected raw snippet " + value["privateSnippet"]
        safe = json.dumps(v.safe_runtime_facts(value))
        for needle in ("mixel-remote://", "inv_123", "secret-value", "bearer abc", "unexpected raw snippet"):
            self.assertNotIn(needle, safe)
        self.assertIn("Tray fixture log contains invitation credentials", safe)
        self.assertIn("unrecognized-error-redacted", safe)
        absent = dict(facts()); absent.pop("runtimeFailure")
        self.assertNotIn("primaryFailure", v.safe_runtime_facts(absent))
        self.assertNotIn("cleanupFailure", v.safe_runtime_facts(absent))

    def test_privacy_wrapper_preserves_original_results_and_records_only_categories_hash_and_length(self):
        spec = importlib.util.spec_from_file_location("source_only_privacy_desktop", ROOT / "scripts/test-support-launch-macos.py")
        desktop = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(desktop)
        original = desktop.privacy_issue
        controls = [b"password=", b"password=''", b'password=""', b'"password":""',
                    b"password=None", b"password=Some(empty)", b'"password":null',
                    b"authorization=false", b"Bearer authentication unavailable", b"empty password", b"ordinary log",
                    b"password = ''", b"password: \"\"", b"access_token=''", b"refresh_token=\"\"",
                    desktop.unix.TOKEN.encode(), desktop.WARM_TOKEN.encode(), desktop.unix.API_KEY.encode(),
                    b"mixel-remote://support/?invite=", b"inv_12345678-1234-1234-1234-123456789abc",
                    b"Bearer secret-test", b"api_key=private-test"]
        expected = [original(value) for value in controls]
        observed = d.observe_privacy(desktop)
        self.assertEqual([desktop.privacy_issue(value) for value in controls], expected)
        self.assertEqual(expected[:4], [False] * 4)
        self.assertEqual(expected[4:9], [True] * 5)
        self.assertTrue(all(expected[15:]))
        safe = v.safe_privacy_facts(observed)
        self.assertTrue(safe["originalResultPreserved"])
        self.assertEqual(len(safe["matchedObservations"]), sum(expected))
        for value, result in zip(controls, expected):
            matches = [item for item in safe["matchedObservations"] if item["freshBytesSha256"] == v.digest(value)]
            self.assertEqual(bool(matches), result)
            if result:
                self.assertEqual(matches[0]["freshBytes"], len(value))
                self.assertEqual(set(matches[0]), {"categories", "freshBytesSha256", "freshBytes"})
        serialized = json.dumps(safe)
        for needle in ("private-test", "secret-test", desktop.unix.TOKEN, desktop.unix.API_KEY,
                       "mixel-remote://", "Some(empty)", "authentication unavailable"):
            self.assertNotIn(needle, serialized)
        categories = {category for item in safe["matchedObservations"] for category in item["categories"]}
        self.assertEqual(categories, v.PRIVACY_CATEGORIES)
        for _ in range(80):
            self.assertTrue(desktop.privacy_issue(b"password=" + os.urandom(8).hex().encode()))
        self.assertEqual(len(observed["matchedObservations"]), 64)
        self.assertTrue(observed["observationLimitReached"])

    def test_privacy_safe_output_rejects_unknown_categories_raw_data_or_invalid_bounds(self):
        valid = {"schema": 1, "originalResultPreserved": True, "observationLimitReached": False,
                 "matchedObservations": [{"categories": ["bearer-pattern"], "freshBytes": 22, "freshBytesSha256": "a" * 64}]}
        for field, value in (("categories", ["raw bearer content"]), ("freshBytes", True),
                             ("freshBytes", 12 * 1024 * 1024 + 1), ("freshBytesSha256", "not-a-digest"),
                             ("rawSnippet", "never disclose")):
            altered = copy.deepcopy(valid); altered["matchedObservations"][0][field] = value
            with self.subTest(field=field), self.assertRaises(v.Refusal):
                v.safe_privacy_facts(altered)
        with self.assertRaises(v.Refusal):
            v.safe_privacy_facts(dict(valid, originalResultPreserved=False))

    def test_driver_calls_frozen_main_and_retains_additive_facts_without_changing_argv_or_result(self):
        desktop = types.SimpleNamespace(privacy_issue=lambda data: False, helper_request=lambda *args: {"pid": 101, "launchTime": 100.5}, probe=lambda *args: {})
        folder = self.root / "driver-proof"
        def frozen_main():
            self.assertEqual(d.sys.argv[1:], ["--app", str(self.root / v.APP), "--proofs", str(folder)])
            self.assertFalse(desktop.privacy_issue(b"harmless"))
            self.assertEqual(desktop.helper_request("helper", "tray"), {"pid": 101, "launchTime": 100.5})
            folder.mkdir()
            (folder / "tray-runtime-safe-facts.json").write_text("{}\n")
            return 1
        tray = types.SimpleNamespace(desktop=desktop, main=frozen_main, gui_owner=lambda *args: 102, launch_identity=lambda *args: 100.5)
        spec = types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda module: None))
        original_argv = d.sys.argv
        with patch.object(d.importlib.util, "spec_from_file_location", return_value=spec), \
             patch.object(d.importlib.util, "module_from_spec", return_value=tray):
            self.assertEqual(d.main(["--app", str(self.root / v.APP), "--proofs", str(folder)]), 1)
        self.assertIs(d.sys.argv, original_argv)
        output = json.loads((folder / "privacy-match-safe-facts.json").read_text())
        self.assertEqual(v.safe_privacy_facts(output)["matchedObservations"], [])
        launches = json.loads((folder / "launch-observation-safe-facts.json").read_text())
        self.assertEqual(v.safe_launch_facts(launches)["launchObservations"], [{"action": "tray", "pid": 101, "launchTime": 100.5}])
        probes = json.loads((folder / "gui-probe-safe-facts.json").read_text())
        self.assertEqual(v.safe_gui_facts(probes)["probeObservations"], [])
        self.assertFalse(probes["postFailureWindow"]["attempted"])

    def test_launch_wrapper_preserves_same_result_object_and_exception_and_records_only_four_numeric_observations(self):
        result = {"pid": 101, "launchTime": 100.5, "rawResponse": "mixel-remote://support/?invite=private-value"}
        calls = []
        failure = RuntimeError("synthetic failure")
        def original(helper, action, *args, **kwargs):
            calls.append((helper, action, args, kwargs))
            if action == "raise":
                raise failure
            return result
        desktop = types.SimpleNamespace(helper_request=original)
        facts = d.observe_launchservices(desktop)
        argument = object()
        self.assertIs(desktop.helper_request("helper", "tray", argument, mode="warm"), result)
        self.assertEqual(calls[-1], ("helper", "tray", (argument,), {"mode": "warm"}))
        self.assertEqual(set(result), {"pid", "launchTime", "rawResponse"})
        for invalid in (None, {"pid": True, "launchTime": 1}, {"pid": "101", "launchTime": 1},
                        {"pid": 101, "launchTime": True}, {"pid": 101, "launchTime": float("inf")},
                        {"pid": 101, "launchTime": float("nan")}, {"pid": 101, "launchTime": "100.5"},
                        {"pid": 101, "launchTime": 10**400},
                        {"pid": 101, "launchTime": -1}):
            result = invalid
            self.assertIs(desktop.helper_request("helper", "launch"), invalid)
        self.assertEqual(len(facts["launchObservations"]), 1)
        result = {"pid": 102, "launchTime": 102.5, "privateSnippet": "secret-test"}
        self.assertIs(desktop.helper_request("helper", "probe"), result)
        for _ in range(6):
            self.assertIs(desktop.helper_request("helper", "launch"), result)
        with self.assertRaises(RuntimeError) as raised:
            desktop.helper_request("helper", "raise")
        self.assertIs(raised.exception, failure)
        safe = v.safe_launch_facts(facts)
        self.assertEqual(len(safe["launchObservations"]), 4)
        self.assertTrue(safe["observationLimitReached"])
        self.assertNotIn("secret-test", json.dumps(safe))
        self.assertNotIn("private-value", json.dumps(safe))
        for field, value in (("action", "raw-response"), ("pid", True), ("launchTime", "100"),
                             ("launchTime", float("inf")), ("rawResponse", "never persist")):
            altered = copy.deepcopy(safe); altered["launchObservations"][0][field] = value
            with self.subTest(field=field), self.assertRaises(v.Refusal):
                v.safe_launch_facts(altered)

    def test_driver_refuses_existing_directory_file_symlink_and_alias_parent_before_import_or_native(self):
        existing = self.root / "existing"; existing.mkdir()
        sentinel = existing / "tray-runtime-safe-facts.json"; sentinel.write_bytes(b"existing owned bytes")
        regular = self.root / "existing-file"; regular.write_bytes(b"existing regular file")
        link = self.root / "existing-link"; link.symlink_to(existing, target_is_directory=True)
        alias_parent = self.root / "alias-parent"; alias_parent.symlink_to(existing, target_is_directory=True)
        before = {path: path.read_bytes() for path in (sentinel, regular)}
        for output in (existing, regular, link, alias_parent / "new"):
            with self.subTest(output=output.name), \
                 patch.object(d.importlib.util, "spec_from_file_location", side_effect=AssertionError("No helper import allowed")), \
                 self.assertRaises(RuntimeError):
                d.main(["--app", str(self.root / v.APP), "--proofs", str(output)])
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(set(path.name for path in existing.iterdir()), {sentinel.name})
        self.assertTrue(link.is_symlink())
        self.assertTrue(alias_parent.is_symlink())

    def test_hosted_and_checkout_guards_precede_any_metadata_or_native_operation(self):
        with patch.dict(v.os.environ, {}, clear=True), self.assertRaises(v.Refusal):
            v.hosted_context(self.root, self.root)
        environment = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "macOS", "GITHUB_REPOSITORY": v.REPOSITORY,
                       "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": "b" * 40,
                       "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REF": "refs/heads/" + v.BRANCH,
                       "GITHUB_WORKFLOW_REF": v.REPOSITORY + "/" + v.DIAGNOSTIC_WORKFLOW + "@refs/heads/" + v.BRANCH}
        with patch.dict(v.os.environ, environment, clear=True), patch.object(v.sys, "platform", "darwin"), patch.object(v.platform, "machine", return_value="x86_64"), self.assertRaises(v.Refusal):
            v.hosted_context(self.root, self.root)
        producer = self.root / "producer"; (producer / ".github/workflows").mkdir(parents=True)
        (producer / v.WORKFLOW).write_text('\n'.join((
            'DIAGNOSTIC_APP_ZIP="$DIAGNOSTICS/signed-stapled-diagnostic-app.zip"',
            'ditto -c -k --keepParent "$APP_PATH" "$DIAGNOSTIC_APP_ZIP"',
            '--diagnostic-app-zip "$DIAGNOSTIC_APP_ZIP"',
            "if: ${{ always() && steps.macos_package.outcome == 'failure' }}",
            "name: macos-launch-failure-diagnostic-${{ matrix.job.arch }}", "path: macos-launch-failure/")))
        calls = []
        def git_only(command, category):
            calls.append(command)
            self.assertEqual(command[0], "git")
            if command[-1] == "HEAD":
                return ((v.SOURCE if command[2] == str(producer) else "b" * 40) + "\n").encode(), b""
            if command[-1] == "HEAD^{tree}":
                return (v.SOURCE_TREE + "\n").encode(), b""
            return b"", b""
        with patch.dict(v.os.environ, environment, clear=True), patch.object(v.sys, "platform", "darwin"), \
             patch.object(v.platform, "machine", return_value="arm64"), patch.object(v, "capture", side_effect=git_only):
            self.assertEqual(v.hosted_context(self.root, producer), "b" * 40)
        self.assertEqual(len(calls), 5)
        for field, value in (("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_RUN_ATTEMPT", "2"),
                             ("GITHUB_REF", "refs/heads/main"), ("GITHUB_WORKFLOW_REF", "other/repo/file@refs/heads/main")):
            with patch.dict(v.os.environ, dict(environment, **{field: value}), clear=True), \
                 patch.object(v.sys, "platform", "darwin"), patch.object(v.platform, "machine", return_value="arm64"), self.assertRaises(v.Refusal):
                v.hosted_context(self.root, producer)

    def test_frozen_helper_contract_and_complete_source_parse(self):
        contract = v.helper_contract(ROOT)
        self.assertEqual(contract["scripts/test-support-launch-macos-tray.py"], "80ebc3255a3898c9feb23c68e06fee28efb74109e5d30460b3ddb9e651ade9b4")
        self.assertEqual(contract["scripts/test-support-launch-macos-tray-controls.py"], "b06623ec893cdcf43b3b27670eadbe12f49d4491d012d8b88debe88fb4e6184d")
        for source in (ROOT / "scripts").glob("*.py"):
            ast.parse(source.read_bytes(), filename=str(source))

    def test_workflow_is_manual_read_only_exact_source_and_safe_output_only(self):
        from current77_arm_workflow_projection import historical_workflow
        text = historical_workflow((ROOT / v.DIAGNOSTIC_WORKFLOW).read_text())
        self.assertIn("on:\n  workflow_dispatch:\n", text)
        self.assertNotIn("inputs:", text)
        self.assertNotIn("workflow_run:", text)
        self.assertNotIn("push:", text)
        self.assertIn("actions: read\n  contents: read", text)
        self.assertNotIn(": write", text)
        self.assertIn("runs-on: macos-14", text)
        self.assertIn("ref: ${{ github.sha }}", text)
        self.assertIn("ref: " + v.SOURCE, text)
        self.assertEqual(text.count("persist-credentials: false"), 3)
        self.assertIn("pull_request:\n    paths:", text)
        static, native = text.split("  failed-arm-diagnostic:\n", 1)
        self.assertIn("runs-on: ubuntu-latest", static)
        self.assertNotIn("gh api", static)
        self.assertNotIn("diagnose-macos-failed-app.py ", static)
        self.assertIn("github.event_name == 'workflow_dispatch'", native.split("steps:", 1)[0])
        self.assertIn("github.ref == 'refs/heads/" + v.BRANCH + "'", native.split("steps:", 1)[0])
        self.assertIn("needs: static-controls", native)
        self.assertEqual(text.count("uses: actions/upload-artifact@v6"), 1)
        upload = text.split("uses: actions/upload-artifact@v6", 1)[1]
        self.assertIn("failed-arm-safe-facts/validation-receipt.json", upload)
        self.assertIn("failed-arm-safe-facts/tray-diagnostic-safe-facts.json", upload)
        self.assertNotIn("*.log", upload)
        self.assertNotIn("*.zip", upload)
        self.assertNotIn("runtime-diagnostics/", upload)
        self.assertNotIn("secrets.", text)

    def test_complete_mock_diagnostic_retains_failure_without_native_or_raw_log_output(self):
        nested_data = zip_bytes(nested_entries())
        embedded = {"schema": 1, "scope": "macos-launch-smoke-diagnostic-not-customer-release",
                    "runner_context": {"source_sha": v.SOURCE, "run_id": v.RUN}, "outcome": "success",
                    "privacy_check_passed": True, "customer_app_qualified": False,
                    "screen_control_qualified": False, "publication": False,
                    "executable_sha256": v.digest(v.CRITICAL[0].encode()),
                    "diagnostic_app_archive": {"name": "signed-stapled-diagnostic-app.zip",
                                               "bytes": len(nested_data), "sha256": v.digest(nested_data)}}
        outer = self.root / "outer.zip"
        outer.write_bytes(zip_bytes([entry(v.OUTER_APP, nested_data), entry(v.OUTER_RUNTIME, json.dumps(embedded).encode()),
                                    entry("aarch64/runtime/owned-app-log-1.log", b"secret app log never copied")]))
        private, safe = self.root / "private", self.root / "safe"
        commands = []
        def metadata(suffix):
            if suffix.endswith("/attempts/1/jobs?per_page=100"):
                return jobs()
            return artifact() if "/artifacts/" in suffix else run()
        def mocked_capture(command, category, **kwargs):
            commands.append(command)
            if command[0] == "ditto":
                with zipfile.ZipFile(command[3]) as zipped:
                    zipped.extractall(command[4])
            elif "verify-macos-bundle-minimum.py" in command[2]:
                Path(command[-1]).write_text(json.dumps({"status": "passed", "architecture": "arm64", "advertised_minimum": "12.3", "binary_count": 4}))
            return b"", b""
        def mocked_runtime(command, **kwargs):
            self.assertIn("run-failed-macos-tray-diagnostic.py", command[2])
            folder = Path(command[-1]); folder.mkdir()
            record = facts(); record["criticalHashes"] = {name: v.digest(name.encode()) for name in v.CRITICAL}
            (folder / "tray-runtime-safe-facts.json").write_text(json.dumps(record))
            (folder / "privacy-match-safe-facts.json").write_text(json.dumps({"schema": 1,
                "originalResultPreserved": True, "matchedObservations": [], "observationLimitReached": False}))
            (folder / "launch-observation-safe-facts.json").write_text(json.dumps({"schema": 1,
                "originalResultPreserved": True, "launchObservations": [], "observationLimitReached": False}))
            (folder / "gui-probe-safe-facts.json").write_text(json.dumps({"schema": 1,
                "originalResultPreserved": True, "probeObservations": [], "serviceObservations": [], "observationLimitReached": False,
                "postFailureWindow": {"attempted": False, "deadlineSeconds": 14, "elapsedSeconds": 0, "refusalCategory": None}}))
            (folder / "kernel-owner-safe-facts.json").write_text(json.dumps(empty_kernel_facts()))
            return subprocess.CompletedProcess(command, 1)
        output = io.StringIO()
        with patch.object(v, "hosted_context", return_value="b" * 40), patch.object(v, "api", side_effect=metadata), \
             patch.object(v, "download_archive", return_value=outer), patch.object(v, "verify_signatures"), \
             patch.object(v, "capture", side_effect=mocked_capture), patch.object(v.subprocess, "run", side_effect=mocked_runtime), \
             patch("sys.stdout", output):
            result = v.main(["--diagnostic-checkout", str(ROOT), "--producer-checkout", str(ROOT),
                             "--destination", str(private), "--safe-output", str(safe)])
        self.assertEqual(result, 0)
        self.assertEqual(set(path.name for path in safe.iterdir()), {"validation-receipt.json", "tray-diagnostic-safe-facts.json"})
        receipt = json.loads((safe / "validation-receipt.json").read_text())
        self.assertTrue(receipt["validationPassed"])
        self.assertFalse(receipt["runtimePassed"])
        self.assertFalse(receipt["customerAppQualified"])
        serialized = b"".join(path.read_bytes() for path in safe.iterdir())
        self.assertNotIn(b"secret app log", serialized)
        self.assertFalse(any(path.suffix == ".log" for path in private.rglob("*")))
        self.assertEqual(json.loads((safe / "tray-diagnostic-safe-facts.json").read_text())["primaryFailure"]["phase"], "os-url-activation")



class GuiIdentityControls(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mixel-gui-identity-source-control-")
        self.app = Path(self.directory.name).resolve()
        self.native = patch.object(d.subprocess, "run", side_effect=AssertionError("Native/network operation forbidden in local controls"))
        self.native.start()
        self.clock = 0.0
        self.timer = patch.object(d, "time", types.SimpleNamespace(
            monotonic=lambda: self.clock, time=lambda: 1791900000.0 + self.clock,
            sleep=lambda seconds: setattr(self, "clock", self.clock + seconds)))
        self.timer.start()

    def tearDown(self):
        self.timer.stop()
        self.native.stop()
        self.directory.cleanup()

    def tray(self, state=None):
        if state is None:
            state = {"running": True, "visible": False, "hidden": False, "launchTime": 0,
                     "bundlePath": str(self.app), "rawResponse": "mixel-remote://support/?invite=private"}
        self.state = state
        self.original_failure = RuntimeError("Tray fixture process has no retained launch identity")
        self.calls = []
        desktop = types.SimpleNamespace(probe=lambda *args: self.state)
        def identity(helper, app, pid):
            value = desktop.probe(helper, pid, app)
            if not value.get("running") or not value.get("launchTime"):
                raise self.original_failure
            return value["launchTime"]
        def owner(app, server_pid):
            self.calls.append(("kernel-owner", server_pid))
            return 102
        def connections(server_pid, app):
            self.calls.append(("connections", server_pid))
            return True
        tray = types.SimpleNamespace(desktop=desktop, gui_owner=owner, launch_identity=identity,
                                     isolated_ci=lambda: None, no_connections=connections)
        return tray

    def test_gui_probe_pass_through_preserves_same_object_arguments_and_private_values(self):
        tray = self.tray()
        calls = []
        result = self.state
        def original(*args, **kwargs):
            calls.append((args, kwargs))
            return result
        tray.desktop.probe = original
        facts = d.observe_gui_identity(tray)
        self.assertEqual(tray.gui_owner(self.app, 101), 102)
        argument = object()
        self.assertIs(tray.desktop.probe(argument, 102, self.app), result)
        self.assertEqual(calls, [((argument, 102, self.app), {})])
        self.assertIn("rawResponse", result)
        safe = v.safe_gui_facts(facts)
        self.assertEqual(safe["probeObservations"][0]["launchTime"], 0)
        self.assertTrue(safe["probeObservations"][0]["bundleMatch"])
        for value in ("mixel-remote://", "private", str(self.app), "rawResponse"):
            self.assertNotIn(value, json.dumps(safe))

    def test_original_probe_exception_identity_is_preserved_and_not_replaced(self):
        tray = self.tray()
        error = RuntimeError("raw secret never retained")
        def failed(*args): raise error
        tray.desktop.probe = failed
        facts = d.observe_gui_identity(tray)
        tray.gui_owner(self.app, 101)
        with self.assertRaises(RuntimeError) as raised:
            tray.desktop.probe("helper", 102, self.app)
        self.assertIs(raised.exception, error)
        self.assertEqual(v.safe_gui_facts(facts)["probeObservations"][0]["status"], "probe-unavailable")
        self.assertNotIn("raw secret", json.dumps(facts))
        self.assertFalse(facts["postFailureWindow"]["attempted"])

    def test_failed_identity_stays_failed_after_late_visible_registration_and_original_cleanup_refusal(self):
        tray = self.tray()
        facts = d.observe_gui_identity(tray)
        tray.gui_owner(self.app, 101)
        timeouts = []
        def later(helper, pid, app, timeout):
            timeouts.append(timeout)
            return {"running": True, "visible": self.clock >= 3, "hidden": False,
                    "launchTime": 1791900000 if self.clock >= 3 else 0, "bundlePath": str(app)}
        with patch.object(d, "bounded_gui_probe", side_effect=later), self.assertRaises(RuntimeError) as raised:
            tray.launch_identity("helper", self.app, 102)
        self.assertIs(raised.exception, self.original_failure)
        self.assertEqual(self.clock, 10.5)
        self.assertTrue(all(0 < value <= 1 for value in timeouts))
        observations = v.safe_gui_facts(facts)["probeObservations"]
        later = [row for row in observations if row["origin"] == "post-failure"]
        self.assertTrue(any(row["launchTime"] == 0 and not row["visible"] for row in later))
        self.assertTrue(any(row["launchTime"] > 0 and row["visible"] for row in later))
        # The original retained identity remains unavailable; observation does
        # not adopt the later launchTime or change the fixture/cleanup result.
        with self.assertRaises(RuntimeError) as cleanup:
            tray.launch_identity("helper", self.app, 102)
        self.assertIs(cleanup.exception, self.original_failure)
        self.assertEqual(self.clock, 10.5)
        self.assertEqual(len([x for x in self.calls if x[0] == "connections"]), len(later))

    def test_persistent_zero_launch_time_remains_distinct_from_late_registration(self):
        tray = self.tray()
        facts = d.observe_gui_identity(tray)
        tray.gui_owner(self.app, 101)
        with patch.object(d, "bounded_gui_probe", return_value=self.state), self.assertRaises(RuntimeError):
            tray.launch_identity("helper", self.app, 102)
        rows = v.safe_gui_facts(facts)["probeObservations"]
        self.assertTrue(rows)
        self.assertTrue(all(row["running"] and row["launchTime"] == 0 for row in rows))
        self.assertEqual(facts["postFailureWindow"]["elapsedSeconds"], 10.5)

    def test_missing_ns_running_application_is_safe_state_without_authorizing_its_empty_bundle(self):
        result = {"running": False, "visible": False, "hidden": False, "bundlePath": ""}
        response = subprocess.CompletedProcess(["fixture"], 0, json.dumps(result), "")
        with patch.object(d.subprocess, "run", return_value=response):
            self.assertEqual(d.bounded_gui_probe(Path("/owned/helper"), 102, self.app, 0.75), result)
        summary = d.probe_summary(result, self.app)
        self.assertFalse(summary["running"])
        self.assertFalse(summary["bundleMatch"])
        self.assertEqual(summary["launchTimeState"], "missing")
        self.assertIsNone(summary["launchTime"])

    def test_probe_only_command_has_bounded_timeout_and_refuses_malformed_or_excessive_response(self):
        calls = []
        valid = {"running": True, "bundlePath": str(self.app), "launchTime": 0}
        def controlled(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, json.dumps(valid), "")
        with patch.object(d.subprocess, "run", side_effect=controlled):
            self.assertEqual(d.bounded_gui_probe(Path("/owned/helper"), 102, self.app, 0.25), valid)
        self.assertEqual(calls[0][0], ["/owned/helper", "probe", "102"])
        self.assertEqual(calls[0][1]["timeout"], 0.25)
        for code, output in ((0, "not json"), (1, "{}"), (0, '{"error":"raw secret"}'), (0, "x" * 4097)):
            with patch.object(d.subprocess, "run", return_value=subprocess.CompletedProcess([], code, output, "")), self.assertRaises(RuntimeError):
                d.bounded_gui_probe(Path("/owned/helper"), 102, self.app, 0.25)

    def test_extra_sampling_refuses_wrong_kernel_owner_authentication_isolation_and_missing_owner(self):
        for field, replacement, category in (
                ("gui_owner", lambda *args: 103, "gui-owner-changed"),
                ("no_connections", lambda *args: False, "authenticated-connection"),
                ("isolated_ci", lambda: (_ for _ in ()).throw(RuntimeError("refused")), "isolated-context"),
                ("gui_owner", lambda *args: (_ for _ in ()).throw(RuntimeError("no owner")), "ownership-unavailable")):
            with self.subTest(field=field, category=category):
                tray = self.tray()
                original = d.observe_gui_identity(tray)
                # original_owner is captured before wrapping, so modify the
                # captured owner by making a fresh wrapper for each negative.
                self.assertEqual(tray.gui_owner(self.app, 101), 102)
                if field == "gui_owner":
                    # Use a stateful original to preserve the first ownership
                    # observation and refuse only the additional sample.
                    counter = [0]
                    fresh = self.tray()
                    def changed(*args):
                        counter[0] += 1
                        return 102 if counter[0] == 1 else replacement(*args)
                    fresh.gui_owner = changed
                    facts = d.observe_gui_identity(fresh)
                    fresh.gui_owner(self.app, 101)
                    tray = fresh
                else:
                    facts = original
                    setattr(tray, field, replacement)
                with patch.object(d, "bounded_gui_probe", side_effect=AssertionError("No extra probe after ownership refusal")), self.assertRaises(RuntimeError) as raised:
                    tray.launch_identity("helper", self.app, 102)
                self.assertIs(raised.exception, self.original_failure)
                self.assertEqual(facts["postFailureWindow"]["refusalCategory"], category)
                self.assertEqual([row for row in facts["probeObservations"] if row["origin"] == "post-failure"], [])
                v.safe_gui_facts(facts)

    def test_unknown_gui_or_other_identity_error_never_starts_extra_observation(self):
        for known, message in ((False, "Tray fixture process has no retained launch identity"), (True, "Different primary refusal")):
            tray = self.tray()
            error = RuntimeError(message)
            tray.launch_identity = lambda *args: (_ for _ in ()).throw(error)
            facts = d.observe_gui_identity(tray)
            if known: tray.gui_owner(self.app, 101)
            with patch.object(d, "bounded_gui_probe", side_effect=AssertionError("No observation permitted")), self.assertRaises(RuntimeError) as raised:
                tray.launch_identity("helper", self.app, 102)
            self.assertIs(raised.exception, error)
            self.assertFalse(facts["postFailureWindow"]["attempted"])

    def test_gui_schema_rejects_raw_paths_fields_invalid_pid_time_boolean_and_bounds(self):
        tray = self.tray()
        facts = d.observe_gui_identity(tray)
        tray.gui_owner(self.app, 101)
        tray.desktop.probe("helper", 102, self.app)
        v.safe_gui_facts(facts)
        for key, value in (("pid", True), ("pid", 0), ("origin", "native-launch"), ("status", "accepted"),
                           ("observedUnixTime", float("nan")), ("elapsedSeconds", 16), ("running", "yes"),
                           ("visible", 1), ("launchTime", -1), ("launchTimeState", "raw value"),
                           ("bundleMatch", str(self.app)), ("bundlePath", str(self.app)), ("arguments", "raw bearer secret")):
            changed = copy.deepcopy(facts);changed["probeObservations"][0][key] = value
            with self.subTest(key=key), self.assertRaises(v.Refusal): v.safe_gui_facts(changed)
        for key, value in (("deadlineSeconds", 15), ("elapsedSeconds", 16), ("refusalCategory", "raw exception"),
                           ("attempted", "true"), ("launchTime", 12)):
            changed = copy.deepcopy(facts);changed["postFailureWindow"][key] = value
            with self.subTest(window=key), self.assertRaises(v.Refusal): v.safe_gui_facts(changed)
        changed = copy.deepcopy(facts);changed["probeObservations"] *= 65
        with self.assertRaises(v.Refusal): v.safe_gui_facts(changed)
        for key, value in (("originalResultPreserved", False), ("schema", True), ("rawLogs", [])):
            with self.subTest(record=key), self.assertRaises(v.Refusal): v.safe_gui_facts(dict(facts, **{key:value}))

    def test_gui_summary_redacts_invalid_numeric_and_raw_boolean_fields(self):
        for launch in (True, "secret", float("nan"), float("inf"), -1, 10**400):
            result = d.probe_summary({"launchTime": launch, "running": "secret", "visible": 1,
                                      "hidden": {}, "bundlePath": "mixel-remote://private"}, self.app)
            self.assertIsNone(result["launchTime"])
            self.assertEqual(result["launchTimeState"], "invalid")
            self.assertTrue(all(result[field] is None for field in ("running", "visible", "hidden")))
            self.assertFalse(result["bundleMatch"])



    def test_timed_probe_failures_obey_deadline_preserve_primary_and_do_not_measure_after_owner_deadline(self):
        tray = self.tray()
        calls = [0]
        def slow_owner(*args):
            calls[0] += 1
            if calls[0] > 1: self.clock += 1
            return 102
        tray.gui_owner = slow_owner
        def slow_connections(*args):
            self.clock += 2
            return True
        tray.no_connections = slow_connections
        facts = d.observe_gui_identity(tray)
        tray.gui_owner(self.app, 101)
        deadlines = []
        def timeout(helper, pid, app, seconds):
            deadlines.append(seconds);self.clock += seconds
            raise subprocess.TimeoutExpired(["read-only-probe"], seconds)
        with patch.object(d, "bounded_gui_probe", side_effect=timeout), self.assertRaises(RuntimeError) as raised:
            tray.launch_identity("helper", self.app, 102)
        self.assertIs(raised.exception, self.original_failure)
        self.assertEqual(self.clock, 13.5)
        self.assertEqual(deadlines, [1, 1, 1])
        window = v.safe_gui_facts(facts)["postFailureWindow"]
        self.assertEqual(window["elapsedSeconds"], 13.5)
        self.assertTrue(all(row["status"] == "probe-unavailable" for row in facts["probeObservations"][1:]))

    def test_canonical_app_alias_is_refused_before_any_additive_probe(self):
        tray = self.tray()
        alias = self.app / "alias"
        alias.symlink_to(self.app, target_is_directory=True)
        facts = d.observe_gui_identity(tray)
        tray.gui_owner(alias, 101)
        with patch.object(d, "bounded_gui_probe", side_effect=AssertionError("No probe through app alias")), self.assertRaises(RuntimeError) as raised:
            tray.launch_identity("helper", alias, 102)
        self.assertIs(raised.exception, self.original_failure)
        self.assertEqual(v.safe_gui_facts(facts)["postFailureWindow"]["refusalCategory"], "noncanonical-app")
        self.assertEqual([row for row in facts["probeObservations"] if row["origin"] == "post-failure"], [])

    def test_exact_cc_producer_tree_mismatch_is_refused_before_native_diagnosis(self):
        producer = self.app / "producer"
        (producer / ".github/workflows").mkdir(parents=True)
        (producer / v.WORKFLOW).write_text((ROOT / v.WORKFLOW).read_text())
        environment = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "macOS", "GITHUB_REPOSITORY": v.REPOSITORY,
                       "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": "b" * 40,
                       "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REF": "refs/heads/" + v.BRANCH,
                       "GITHUB_WORKFLOW_REF": v.REPOSITORY + "/" + v.DIAGNOSTIC_WORKFLOW + "@refs/heads/" + v.BRANCH}
        def git_only(command, category):
            self.assertEqual(command[0], "git")
            if command[-1] == "HEAD":
                return ((v.SOURCE if command[2] == str(producer) else "b" * 40) + "\n").encode(), b""
            if command[-1] == "HEAD^{tree}": return ("c" * 40 + "\n").encode(), b""
            return b"", b""
        with patch.dict(v.os.environ, environment, clear=True), patch.object(v.sys, "platform", "darwin"), \
             patch.object(v.platform, "machine", return_value="arm64"), patch.object(v, "capture", side_effect=git_only), self.assertRaises(v.Refusal) as raised:
            v.hosted_context(self.app, producer)
        self.assertEqual(str(raised.exception), "producer-tree-source")



    def test_service_observation_distinguishes_late_gui_without_v2_and_late_v2_without_qualifying_either(self):
        for becomes_ready in (False, True):
            with self.subTest(becomes_ready=becomes_ready):
                self.clock = 0
                tray = self.tray()
                health_calls = []
                def health(server_pid, app, attended):
                    health_calls.append((server_pid, attended))
                    return becomes_ready and self.clock >= 3
                tray.health = health
                launches = {"launchObservations": [{"action": "tray", "pid": 101, "launchTime": 1791900000}]}
                facts = d.observe_gui_identity(tray, launches)
                tray.gui_owner(self.app, 101)
                def later(helper, pid, app, timeout):
                    return {"running": True, "visible": self.clock >= 3, "hidden": False,
                            "launchTime": 1791900000 if pid == 101 or self.clock >= 3 else 0, "bundlePath": str(app)}
                with patch.object(d, "bounded_gui_probe", side_effect=later), self.assertRaises(RuntimeError) as raised:
                    tray.launch_identity("helper", self.app, 102)
                self.assertIs(raised.exception, self.original_failure)
                safe = v.safe_gui_facts(facts)
                self.assertTrue(any(row["visible"] for row in safe["probeObservations"] if row["origin"] == "post-failure"))
                values = [row["attendedServiceReady"] for row in safe["serviceObservations"]]
                self.assertIn(False, values)
                self.assertEqual(True in values, becomes_ready)
                self.assertTrue(all(row["serverLaunchMatch"] for row in safe["serviceObservations"]))
                self.assertTrue(all(pid == 101 and attended for pid, attended in health_calls))
                self.assertNotIn("qualified", safe)
                self.assertLessEqual(facts["postFailureWindow"]["elapsedSeconds"], 14)

    def test_service_sampling_requires_retained_original_tray_identity_and_refuses_health_error(self):
        for retained, server_launch, health_error in ((None, 1791900000, False),
                                                     (1791900000, 1791900001, False),
                                                     (1791900000, 1791900000, True)):
            with self.subTest(retained=retained, server_launch=server_launch, health_error=health_error):
                self.clock = 0
                tray = self.tray()
                health_calls = []
                def health(*args):
                    health_calls.append(args)
                    raise RuntimeError("authenticated or unknown connection refused")
                tray.health = health
                launches = {"launchObservations": [{"action": "tray", "pid": 101, "launchTime": retained}]}
                facts = d.observe_gui_identity(tray, launches)
                tray.gui_owner(self.app, 101)
                def observed(helper, pid, app, timeout):
                    return {"running": True, "visible": True, "hidden": False,
                            "launchTime": server_launch if pid == 101 else 1791900000, "bundlePath": str(app)}
                with patch.object(d, "bounded_gui_probe", side_effect=observed), self.assertRaises(RuntimeError) as raised:
                    tray.launch_identity("helper", self.app, 102)
                self.assertIs(raised.exception, self.original_failure)
                self.assertEqual(bool(health_calls), health_error)
                self.assertTrue(all(row["attendedServiceReady"] is None for row in v.safe_gui_facts(facts)["serviceObservations"]))
                self.assertNotIn("authenticated or unknown", json.dumps(facts))

    def test_service_schema_refuses_raw_options_missing_identity_unknown_status_and_fake_readiness(self):
        tray = self.tray()
        tray.health = lambda *args: False
        launches = {"launchObservations": [{"action": "tray", "pid": 101, "launchTime": 1791900000}]}
        facts = d.observe_gui_identity(tray, launches)
        tray.gui_owner(self.app, 101)
        response = {"running": True, "visible": True, "hidden": False, "launchTime": 1791900000, "bundlePath": str(self.app)}
        with patch.object(d, "bounded_gui_probe", return_value=response), self.assertRaises(RuntimeError):
            tray.launch_identity("helper", self.app, 102)
        v.safe_gui_facts(facts)
        for key, value in (("pid", True), ("retainedServerLaunchTime", float("nan")), ("retainedServerLaunchTime", None), ("serverLaunchMatch", False),
                           ("attendedServiceReady", "yes"), ("status", "connected"), ("options", {"private":"value"})):
            changed = copy.deepcopy(facts);changed["serviceObservations"][0][key] = value
            with self.subTest(key=key), self.assertRaises(v.Refusal):v.safe_gui_facts(changed)
        changed = copy.deepcopy(facts);changed["serviceObservations"][0]["status"] = "health-unavailable"
        changed["serviceObservations"][0]["attendedServiceReady"] = True
        with self.assertRaises(v.Refusal):v.safe_gui_facts(changed)



    def test_fragmented_observation_reads_cannot_renew_the_absolute_deadline(self):
        timeouts = []
        class FragmentedSocket:
            def settimeout(inner, value): inner.timeout = value;timeouts.append(value)
            def recv(inner, size):
                if inner.timeout < 0.6:
                    self.clock += inner.timeout
                    raise TimeoutError("synthetic deadline")
                self.clock += 0.6
                return b"x"
        connection = d.DeadlineSocket(FragmentedSocket(), 2)
        connection.settimeout(2)
        with self.assertRaises(TimeoutError):
            for _ in range(20): connection.recv(1)
        self.assertEqual(self.clock, 2)
        self.assertGreater(timeouts[0], timeouts[-1])
        self.assertTrue(all(0 < timeout <= 2 for timeout in timeouts))

    def test_deadline_clones_keep_exact_original_health_query_guard_code_and_globals_unchanged(self):
        source = ast.parse((ROOT / "scripts/test-support-launch-macos-tray.py").read_bytes())
        selected = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in ("query", "no_connections", "health")]
        self.assertEqual(len(selected), 3)
        requests = []
        responses = [
            {"t":"VideoConnCount","c":0},
            {"t":"Config","c":["mixel-support-invite-attended","attended-runtime-v2"]},
            {"t":"OnlineStatus","c":[1,True]},
            {"t":"Options","c":{"custom-rendezvous-server":"rs.mixel.ch","relay-server":"rs.mixel.ch","key":"synthetic-public-key"}},
        ]
        class Socket:
            def __enter__(inner):return inner
            def __exit__(inner,*args):pass
            def settimeout(inner,value):pass
            def connect(inner,address):pass
            def sendall(inner,data):requests.append(json.loads(data))
        socket_module = types.SimpleNamespace(AF_UNIX=1,SOCK_STREAM=1,socket=lambda *args:Socket())
        unix = types.SimpleNamespace(IPC_PATH=Path("/synthetic/incoming"),PUBLIC_KEY="synthetic-public-key",
            frame=lambda data:data,read_frame=lambda connection:json.dumps(responses.pop(0)))
        namespace = {"socket":socket_module,"json":json,"unix":unix,
                     "peer_pid":lambda connection:101,
                     "process_path":lambda pid:self.app/"Contents/MacOS/Mixel-Remote"}
        exec(compile(ast.Module(body=selected,type_ignores=[]),"<exact-original-tray-read-only-guards>","exec"),namespace)
        original_query = namespace["query"]
        tray = types.SimpleNamespace(**{name:namespace[name] for name in ("query","no_connections","health")})
        owner = lambda *args:102
        bounded_owner, bounded_connections, bounded_health = d.bounded_read_only_guards(tray,owner,14)
        self.assertIs(bounded_owner.__code__,owner.__code__)
        self.assertIs(bounded_connections.__code__,tray.no_connections.__code__)
        self.assertIs(bounded_health.__code__,tray.health.__code__)
        self.assertIs(bounded_health.__globals__["query"].__code__,original_query.__code__)
        self.assertIs(namespace["query"],original_query)
        self.assertIs(namespace["socket"],socket_module)
        self.assertTrue(bounded_health(101,self.app,True))
        self.assertEqual([row["t"] for row in requests],["VideoConnCount","Config","OnlineStatus","Options"])
        self.assertEqual(responses,[])
        # The exact original guard refuses a different kernel owner before
        # any send, and neither its module nor original callables are patched.
        namespace["peer_pid"] = lambda connection:999
        _,_,refused = d.bounded_read_only_guards(tray,owner,14)
        before = list(requests)
        with self.assertRaises(RuntimeError):refused(101,self.app,True)
        self.assertEqual(requests,before)

    def test_service_kernel_owner_change_stops_extra_reads_without_overriding_original_failure(self):
        tray = self.tray()
        calls = [0]
        def owner(*args):
            calls[0]+=1
            return 102 if calls[0]<=2 else 103
        tray.gui_owner=owner
        tray.health=lambda *args:(_ for _ in ()).throw(AssertionError("No service query after GUI owner changed"))
        launches={"launchObservations":[{"action":"tray","pid":101,"launchTime":1791900000}]}
        facts=d.observe_gui_identity(tray,launches)
        tray.gui_owner(self.app,101)
        with patch.object(d,"bounded_gui_probe",return_value=self.state) as probe, self.assertRaises(RuntimeError) as raised:
            tray.launch_identity("helper",self.app,102)
        self.assertIs(raised.exception,self.original_failure)
        self.assertEqual(probe.call_count,1)
        safe=v.safe_gui_facts(facts)
        self.assertEqual(safe["postFailureWindow"]["refusalCategory"],"gui-owner-changed")
        self.assertEqual(safe["serviceObservations"][0]["status"],"ownership-unavailable")
        self.assertIsNone(safe["serviceObservations"][0]["attendedServiceReady"])


class KernelIdentityControls(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mixel-kernel-observation-control-")
        self.app = Path(self.directory.name).resolve()
        self.native = patch.object(d.subprocess, "run", side_effect=AssertionError("Native/network operation forbidden"))
        self.native.start()
        self.clock = 0.0
        self.timer = patch.object(d, "time", types.SimpleNamespace(monotonic=lambda: self.clock,
            time=lambda: 1791900000.0 + self.clock, sleep=lambda seconds: setattr(self, "clock", self.clock + seconds)))
        self.timer.start()
        self.calls = []
        self.birth = ("darwin-gui-birth-v1", 102, 501, str(self.app / "Contents/MacOS/Mixel-Remote"), 100, 2)

    def tearDown(self):
        self.timer.stop(); self.native.stop(); self.directory.cleanup()

    def tray(self, owners=(102, 103, 104), births=None):
        owners = iter(owners); births = iter(births or (self.birth, self.birth))
        def owner(app, server_pid):
            self.calls.append(("owner", server_pid))
            return next(owners)
        def birth(app, pid, retained=None):
            self.calls.append(("birth", pid, retained))
            return next(births)
        def no_connections(server_pid, app):
            self.calls.append(("zero-auth", server_pid))
            return True
        tray = types.SimpleNamespace(gui_owner=owner, gui_birth=birth, no_connections=no_connections,
            isolated_ci=lambda: None, desktop=types.SimpleNamespace(safe_error=lambda error: str(error)))
        # The predicate and exception text are copied literally from the
        # hash-bound original helper. No success, retry, wait or cleanup gate
        # is substituted by the diagnostic observer.
        def runtime(app, helper, facts):
            server_pid = 101
            gui_pid = tray.gui_owner(app, server_pid)
            gui_launch = tray.gui_birth(app, gui_pid)
            phase = "customer-gui-visible"
            def visible_gui():
                if tray.gui_owner(app, server_pid) != gui_pid or tray.gui_birth(app, gui_pid) != gui_launch:
                    raise RuntimeError("Tray fixture customer GUI birth or kernel owner changed")
                return True
            try:
                return visible_gui()
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                facts["runtimeFailure"] = {"phase": phase, "reason": tray.desktop.safe_error(error)}
                raise
            finally:
                self.calls.append(("original-finally", gui_pid))
        tray.runtime = runtime
        return tray

    def launch(self):
        return {"launchObservations": [{"action": "tray", "pid": 101, "launchTime": 123.5}]}

    def server(self, helper, pid, app, timeout):
        self.calls.append(("server-probe", pid, timeout))
        self.assertEqual(pid, 101)
        self.assertTrue(0 < timeout <= 1)
        return {"running": True, "bundlePath": str(app), "launchTime": 123.5}

    def test_every_original_call_preserves_results_errors_arguments_and_immutable_initial_identity(self):
        tray = self.tray(owners=(102,103), births=(self.birth, tuple([*self.birth[:4],101,2]),None))
        facts = d.observe_kernel_identity(tray)
        self.assertEqual(tray.gui_owner(self.app,101),102)
        self.assertIs(tray.gui_birth(self.app,102),self.birth)
        self.assertEqual(tray.gui_owner(self.app,101),103)
        self.assertNotEqual(tray.gui_birth(self.app,102),self.birth)
        self.assertIsNone(tray.gui_birth(self.app,102,self.birth))
        safe = v.safe_kernel_facts(facts)
        self.assertEqual(safe["initialGuiPid"],102)
        self.assertEqual(safe["initialGuiBirth"]["startSeconds"],100)
        self.assertEqual([r["differsFromInitial"] for r in safe["observations"]], [False,False,True,True,None])
        self.assertTrue(safe["observations"][-1]["retainedArgument"])
        error = OSError("raw secret path")
        for operation in ("gui_owner","gui_birth"):
            other = self.tray()
            setattr(other,operation,lambda *args: (_ for _ in ()).throw(error))
            record = d.observe_kernel_identity(other)
            with self.assertRaises(OSError) as raised:
                getattr(other,operation)(self.app,102)
            self.assertIs(raised.exception,error)
            self.assertEqual(v.safe_kernel_facts(record)["observations"][0]["errorKind"],"os-error")
            self.assertNotIn("raw secret",json.dumps(record))

    def test_original_owner_short_circuit_and_post_primary_unknown_owner_never_followed_or_adopted(self):
        tray = self.tray()
        record = d.observe_kernel_identity(tray,self.launch())
        original = {}
        with patch.object(d,"bounded_gui_probe",side_effect=self.server), self.assertRaises(RuntimeError) as raised:
            tray.runtime(self.app,"exact-helper",original)
        self.assertEqual(str(raised.exception),d.KERNEL_FAILURE)
        safe = v.safe_kernel_facts(record)
        comparison = safe["primaryComparison"]
        self.assertTrue(comparison["ownerChanged"])
        self.assertFalse(comparison["birthEvaluated"])
        self.assertIsNone(comparison["birthChanged"])
        self.assertEqual(safe["postPrimary"]["ownerPid"],104)
        self.assertTrue(safe["postPrimary"]["ownerChanged"])
        self.assertEqual(safe["initialGuiPid"],102)
        self.assertEqual([c[1] for c in self.calls if c[0]=="birth"],[102])
        self.assertEqual(self.calls[-1],("original-finally",102))
        self.assertEqual(original["runtimeFailure"]["reason"],d.KERNEL_FAILURE)
        self.assertNotIn(str(self.app),json.dumps(safe))

    def test_original_birth_tuple_inequality_retains_exact_operand_discriminator(self):
        changed=(*self.birth[:4],101,2)
        tray=self.tray(owners=(102,102,102),births=(self.birth,changed))
        record=d.observe_kernel_identity(tray,self.launch())
        with patch.object(d,"bounded_gui_probe",side_effect=self.server),self.assertRaises(RuntimeError):
            tray.runtime(self.app,"helper",{})
        comparison=v.safe_kernel_facts(record)["primaryComparison"]
        self.assertFalse(comparison["ownerChanged"])
        self.assertTrue(comparison["birthEvaluated"])
        self.assertTrue(comparison["birthChanged"])
        self.assertEqual(record["initialGuiBirth"]["startSeconds"],100)
        self.assertEqual(record["observations"][3]["birth"]["startSeconds"],101)

    def test_positive_original_condition_remains_success_and_triggers_no_additive_query(self):
        tray=self.tray(owners=(102,102))
        record=d.observe_kernel_identity(tray,self.launch())
        with patch.object(d,"bounded_gui_probe",side_effect=AssertionError("No sampling on success")):
            self.assertTrue(tray.runtime(self.app,"helper",{}))
        self.assertIsNone(record["primaryComparison"])
        self.assertFalse(v.safe_kernel_facts(record)["postPrimary"]["attempted"])
        self.assertEqual(self.calls[-1],("original-finally",102))

    def test_post_primary_identity_noauth_timeout_and_isolation_refuse_without_querying_unknown_owner(self):
        for case in ("missing-launch","wrong-launch","auth","isolation","timeout"):
            with self.subTest(case=case):
                self.clock=0;self.calls=[]
                tray=self.tray()
                launch=self.launch() if case!="missing-launch" else {}
                if case=="auth":tray.no_connections=lambda *args:False
                if case=="isolation":tray.isolated_ci=lambda:(_ for _ in ()).throw(RuntimeError("private path"))
                record=d.observe_kernel_identity(tray,launch)
                def probe(*args):
                    result=self.server(*args)
                    if case=="wrong-launch":result["launchTime"]=124
                    if case=="timeout":self.clock+=4
                    return result
                with patch.object(d,"bounded_gui_probe",side_effect=probe),self.assertRaises(RuntimeError) as raised:
                    tray.runtime(self.app,"helper",{})
                self.assertEqual(str(raised.exception),d.KERNEL_FAILURE)
                safe=v.safe_kernel_facts(record)
                self.assertEqual(len([c for c in self.calls if c[0]=="owner"]),2)
                self.assertIsNone(safe["postPrimary"]["ownerPid"])
                self.assertEqual(self.calls[-1],("original-finally",102))
                self.assertNotIn("private path",json.dumps(safe))
                self.assertEqual(safe["postPrimary"]["status"],{"missing-launch":"server-identity-unavailable",
                    "wrong-launch":"server-identity-changed","auth":"authenticated-connection",
                    "isolation":"observation-refused","timeout":"deadline"}[case])

    def test_wrong_failure_other_frame_cleanup_failure_and_missing_context_never_authorize_observation(self):
        tray=self.tray();record=d.observe_kernel_identity(tray,self.launch())
        with patch.object(d,"bounded_gui_probe",side_effect=AssertionError("No unknown sampling")):
            self.assertEqual(tray.desktop.safe_error(RuntimeError(d.KERNEL_FAILURE)),d.KERNEL_FAILURE)
            self.assertEqual(tray.desktop.safe_error(RuntimeError("other failure")),"other failure")
        self.assertIsNone(record["primaryComparison"])
        self.assertFalse(v.safe_kernel_facts(record)["postPrimary"]["attempted"])

    def test_kernel_schema_privacy_invalid_tuples_order_and_bounds_fail_closed(self):
        tray=self.tray(owners=(102,));record=d.observe_kernel_identity(tray)
        tray.gui_owner(self.app,101);tray.gui_birth(self.app,102)
        for field,value in (("pid",True),("sequence",2),("origin","raw path"),("birth",{"path":"private"}),
                             ("elapsedSeconds",float("nan")),("errorKind","private"),("rawArguments",[])):
            altered=copy.deepcopy(record);altered["observations"][0][field]=value
            with self.subTest(field=field),self.assertRaises(v.Refusal):v.safe_kernel_facts(altered)
        for field,value in (("initialGuiPid",True),("totalCalls",True),("schema",True),("rawLogs",[])):
            with self.subTest(field=field),self.assertRaises(v.Refusal):v.safe_kernel_facts(dict(record,**{field:value}))
        for invalid in (("raw",),(*self.birth[:4],True,2),(*self.birth[:5],1000000)):
            self.assertIsNone(d.birth_summary(invalid,self.app))
        other=self.tray(owners=(102,));other.gui_owner=lambda *args:102
        limited=d.observe_kernel_identity(other)
        for _ in range(70):other.gui_owner(self.app,101)
        safe=v.safe_kernel_facts(limited)
        self.assertEqual(len(safe["observations"]),64)
        self.assertEqual(safe["totalCalls"],70)
        self.assertTrue(safe["observationLimitReached"])

    def test_old_gui_observer_keeps_first_owner_after_kernel_owner_change(self):
        tray=self.tray(owners=(102,103))
        tray.desktop.probe=lambda *args:{}
        tray.launch_identity=lambda *args:123
        kernel=d.observe_kernel_identity(tray)
        gui=d.observe_gui_identity(tray)
        tray.gui_owner(self.app,101);tray.gui_owner(self.app,101)
        tray.desktop.probe("helper",103,self.app)
        tray.desktop.probe("helper",102,self.app)
        self.assertEqual([r["pid"] for r in v.safe_gui_facts(gui)["probeObservations"]],[102])
        self.assertEqual(v.safe_kernel_facts(kernel)["initialGuiPid"],102)

    def test_exact_primary_reason_is_allowlisted_without_exporting_unknown_error_text(self):
        self.assertEqual(v.safe_reason(d.KERNEL_FAILURE)["reason"],d.KERNEL_FAILURE)
        self.assertEqual(v.safe_reason(d.KERNEL_FAILURE)["reasonCategory"],"fixed-reviewed-reason")
        self.assertNotIn("raw private",json.dumps(v.safe_reason("raw private")))

    def test_original_birth_argument_arity_keywords_and_exception_identity_are_unchanged(self):
        tray=self.tray(owners=(102,));seen=[]
        def spy(*args,**kwargs):
            seen.append((args,kwargs))
            return self.birth
        tray.gui_birth=spy
        facts=d.observe_kernel_identity(tray)
        tray.gui_owner(self.app,101)
        self.assertIs(tray.gui_birth(self.app,102),self.birth)
        self.assertIs(tray.gui_birth(self.app,102,self.birth),self.birth)
        self.assertIs(tray.gui_birth(self.app,102,retained=self.birth),self.birth)
        self.assertEqual(seen,[((self.app,102),{}),((self.app,102,self.birth),{}),
                               ((self.app,102),{"retained":self.birth})])
        v.safe_kernel_facts(facts)

    def test_original_cleanup_birth_queries_record_none_and_failure_without_new_cleanup_authority(self):
        tray=self.tray(owners=(102,),births=(self.birth,None))
        facts=d.observe_kernel_identity(tray)
        tray.gui_owner(self.app,101);tray.gui_birth(self.app,102)
        def terminate_owned():
            return tray.gui_birth(self.app,102,self.birth)
        self.assertIsNone(terminate_owned())
        safe=v.safe_kernel_facts(facts)
        self.assertEqual(safe["observations"][-1]["origin"],"terminate_owned")
        self.assertEqual(safe["observations"][-1]["status"],"returned-none")
        self.assertTrue(safe["observations"][-1]["retainedArgument"])
        self.assertFalse(safe["postPrimary"]["attempted"])

    def test_primary_schema_refuses_wrong_branch_operands_raw_tuple_and_authority_claims(self):
        tray=self.tray(owners=(102,102,102),births=(self.birth,(*self.birth[:4],101,2)))
        facts=d.observe_kernel_identity(tray,self.launch())
        with patch.object(d,"bounded_gui_probe",side_effect=self.server),self.assertRaises(RuntimeError):
            tray.runtime(self.app,"helper",{})
        v.safe_kernel_facts(facts)
        for field,value in (("birthSequence",2),("ownerSequence",1),("birthEvaluated",False),
                             ("ownerChanged",True),("reason","raw private error"),("authorization",True)):
            altered=copy.deepcopy(facts);altered["primaryComparison"][field]=value
            with self.subTest(field=field),self.assertRaises(v.Refusal):v.safe_kernel_facts(altered)
        impossible=copy.deepcopy(facts)
        owner_sequence=impossible["primaryComparison"]["ownerSequence"]
        impossible["observations"][owner_sequence-1].update(ownerPid=103,differsFromInitial=True)
        impossible["primaryComparison"]["ownerChanged"]=True
        with self.assertRaises(v.Refusal):v.safe_kernel_facts(impossible)
        impossible=copy.deepcopy(facts)
        birth_sequence=impossible["primaryComparison"]["birthSequence"]
        impossible["observations"][birth_sequence-1].update(birth=copy.deepcopy(facts["initialGuiBirth"]),differsFromInitial=False)
        impossible["primaryComparison"]["birthChanged"]=False
        with self.assertRaises(v.Refusal):v.safe_kernel_facts(impossible)
        impossible=copy.deepcopy(facts)
        impossible["observations"][birth_sequence-1]["birth"]=copy.deepcopy(facts["initialGuiBirth"])
        with self.assertRaises(v.Refusal):v.safe_kernel_facts(impossible)
        for field,value in (("ownerPid",True),("zeroAuth",False),("serverLaunchMatch",False),
                             ("deadlineSeconds",5),("rawPath",str(self.app)),("elapsedSeconds",6)):
            altered=copy.deepcopy(facts);altered["postPrimary"][field]=value
            with self.subTest(post=field),self.assertRaises(v.Refusal):v.safe_kernel_facts(altered)
        altered=copy.deepcopy(facts);altered["initialGuiBirth"]["executablePath"]=str(self.app)
        with self.assertRaises(v.Refusal):v.safe_kernel_facts(altered)

    def test_fixed_artifact_plus_one_second_only_and_completed_job_bounds(self):
        _,upload=v.validate_jobs(jobs())
        v.validate_artifact(artifact(),upload)
        for created in ("2026-10-13T18:32:08Z","2026-10-13T18:32:10Z","2099-10-13T18:32:09Z"):
            altered=artifact();altered.update(created_at=created,updated_at=created)
            with self.subTest(created=created),self.assertRaises(v.Refusal):v.validate_artifact(altered,upload)
        for start,end in (("2026-10-13T18:32:10Z","2026-10-13T18:32:29Z"),
                          ("2026-10-13T17:45:22Z","2026-10-13T18:32:08Z")):
            altered=jobs();altered["jobs"][0].update(started_at=start,completed_at=end)
            with self.assertRaises(v.Refusal):v.validate_jobs(altered)
        for field,value in (("completed_at","2026-10-13T18:32:07Z"),("started_at","2026-10-13T18:32:04Z")):
            with self.assertRaises(v.Refusal):v.validate_artifact(artifact(),dict(upload,**{field:value}))
        with self.assertRaises(v.Refusal):v.validate_run(dict(run(),run_attempt=2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
