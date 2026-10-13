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
            "run_attempt": 1, "status": "in_progress", "conclusion": None,
            "repository": {"id": v.REPOSITORY_ID, "full_name": v.REPOSITORY},
            "head_repository": {"id": v.REPOSITORY_ID, "full_name": v.REPOSITORY}}


def jobs():
    return {"total_count": 1, "jobs": [{"id": v.JOB, "run_id": v.RUN, "run_attempt": 1,
             "name": v.JOB_NAME, "status": "completed", "conclusion": "failure",
             "head_sha": v.SOURCE, "head_branch": v.BRANCH,
             "started_at": "2026-10-13T13:33:34Z", "completed_at": "2026-10-13T14:07:45Z",
             "steps": [{"name": v.PACKAGE_STEP, "status": "completed", "conclusion": "failure",
                        "started_at": "2026-10-13T14:06:18Z", "completed_at": "2026-10-13T14:07:34Z"},
                       {"name": v.UPLOAD_STEP, "status": "completed", "conclusion": "success",
                        "started_at": "2026-10-13T14:07:35Z", "completed_at": "2026-10-13T14:07:37Z"}]}]}


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

    def test_exact_failed_job_is_sufficient_while_overall_producer_is_running(self):
        v.validate_run(run())
        v.validate_run(dict(run(), status="completed", conclusion="failure"))
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
                           ("created_at", "2026-10-13T14:08:00Z"), ("updated_at", "2026-10-13T14:08:00Z")):
            with self.subTest(field=key), self.assertRaises(v.Refusal):
                v.validate_artifact(dict(artifact(), **{key: value}), upload)
        for key, value in (("id", v.RUN + 1), ("head_sha", "b" * 40), ("head_branch", "main"),
                           ("repository_id", 1), ("head_repository_id", 1)):
            altered = artifact(); altered["workflow_run"][key] = value
            with self.subTest(source_field=key), self.assertRaises(v.Refusal):
                v.validate_artifact(altered, upload)
        with self.assertRaises(v.Refusal):
            v.validate_artifact(artifact(), upload, datetime(2028, 1, 1, tzinfo=timezone.utc))
        late_upload = dict(upload, started_at="2026-10-13T14:07:38Z", completed_at="2026-10-13T14:07:39Z")
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
        desktop = types.SimpleNamespace(privacy_issue=lambda data: False, helper_request=lambda *args: {"pid": 101, "launchTime": 100.5})
        folder = self.root / "driver-proof"
        def frozen_main():
            self.assertEqual(d.sys.argv[1:], ["--app", str(self.root / v.APP), "--proofs", str(folder)])
            self.assertFalse(desktop.privacy_issue(b"harmless"))
            self.assertEqual(desktop.helper_request("helper", "tray"), {"pid": 101, "launchTime": 100.5})
            folder.mkdir()
            (folder / "tray-runtime-safe-facts.json").write_text("{}\n")
            return 1
        tray = types.SimpleNamespace(desktop=desktop, main=frozen_main)
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
            return b"", b""
        with patch.dict(v.os.environ, environment, clear=True), patch.object(v.sys, "platform", "darwin"), \
             patch.object(v.platform, "machine", return_value="arm64"), patch.object(v, "capture", side_effect=git_only):
            self.assertEqual(v.hosted_context(self.root, producer), "b" * 40)
        self.assertEqual(len(calls), 4)
        for field, value in (("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_RUN_ATTEMPT", "2"),
                             ("GITHUB_REF", "refs/heads/main"), ("GITHUB_WORKFLOW_REF", "other/repo/file@refs/heads/main")):
            with patch.dict(v.os.environ, dict(environment, **{field: value}), clear=True), \
                 patch.object(v.sys, "platform", "darwin"), patch.object(v.platform, "machine", return_value="arm64"), self.assertRaises(v.Refusal):
                v.hosted_context(self.root, producer)

    def test_frozen_helper_contract_and_complete_source_parse(self):
        contract = v.helper_contract(ROOT)
        self.assertEqual(contract["scripts/test-support-launch-macos-tray.py"], "f99e32292e639faec758ff2866317c83a480fbf56d5377e1b05cbb0fe0055f4b")
        self.assertEqual(contract["scripts/test-support-launch-macos-tray-controls.py"], "9558792bbea3d2d703e76496879dc666c5b1a178c15cbc542f7121304c924a87")
        for source in (ROOT / "scripts").glob("*.py"):
            ast.parse(source.read_bytes(), filename=str(source))

    def test_workflow_is_manual_read_only_exact_source_and_safe_output_only(self):
        text = (ROOT / v.DIAGNOSTIC_WORKFLOW).read_text()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
