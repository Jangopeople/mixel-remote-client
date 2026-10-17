#!/usr/bin/env python3
"""Offline admission/refusal controls; no provider, app, service or apt runs."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys
import zipfile
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("systemd_qa", BASE / "scripts/linux-installer-systemd.py")
qa = importlib.util.module_from_spec(spec); spec.loader.exec_module(qa)


def metadata():
    run = {"id": qa.RUN, "repository": {"id": qa.REPO_ID, "full_name": qa.REPO},
           "event": "workflow_dispatch", "path": ".github/workflows/build.yml",
           "html_url": f"https://github.com/{qa.REPO}/actions/runs/{qa.RUN}",
           "head_sha": qa.APP, "run_attempt": 1, "workflow_id": 271445168,
           "status": "in_progress", "conclusion": None,
           "head_commit": {"id": qa.APP, "tree_id": qa.TREE}}
    job = {"id": qa.JOB, "run_id": qa.RUN, "run_attempt": 1, "name": "linux", "head_sha": qa.APP,
           "status": "completed", "conclusion": "success",
           "steps": [{"status": "completed", "conclusion": "success"} for _ in range(33)]}
    artifact = {"id": qa.ARTIFACT, "name": "linux", "expired": False,
                "size_in_bytes": qa.ARCHIVE_BYTES, "digest": "sha256:" + qa.ARCHIVE_SHA,
                "workflow_run": {"id": qa.RUN, "head_sha": qa.APP}}
    return [run, job, artifact, {"sha": qa.APP, "tree": {"sha": qa.TREE}}]


class Admission(unittest.TestCase):
    def test_exact_current_linux_success_accepts_without_whole_build_claim(self):
        qa.validate_metadata(*metadata())

    def test_mutated_run_source_attempt_repository_workflow_refuse(self):
        for field, value in [("id", 2), ("run_attempt", 2), ("run_attempt", True),
                             ("workflow_id", 2), ("head_sha", "a" * 40), ("path", "x.yml")]:
            with self.subTest(field=field, value=value):
                data = metadata(); data[0][field] = value
                with self.assertRaises(ValueError): qa.validate_metadata(*data)
        for key in ("id", "full_name"):
            data = metadata(); data[0]["repository"][key] = "wrong"
            with self.assertRaises(ValueError): qa.validate_metadata(*data)

    def test_failure_pending_skipped_wrong_job_or_step_refuse(self):
        for field, value in [("id", 3), ("run_id", 3), ("run_attempt", True), ("name", "windows"),
                             ("head_sha", "a" * 40), ("status", "in_progress"), ("conclusion", "failure"),
                             ("conclusion", "skipped"), ("conclusion", None)]:
            data = metadata(); data[1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): qa.validate_metadata(*data)
        for status, conclusion in [("pending", None), ("completed", "failure"), ("completed", "skipped")]:
            data = metadata(); data[1]["steps"][0] = {"status": status, "conclusion": conclusion}
            with self.assertRaises(ValueError): qa.validate_metadata(*data)
        data = metadata(); data[1]["steps"].pop()
        with self.assertRaises(ValueError): qa.validate_metadata(*data)

    def test_wrong_or_expired_artifact_size_digest_refuse(self):
        for field, value in [("id", 3), ("name", "linux-diagnostic"), ("expired", True),
                             ("size_in_bytes", qa.ARCHIVE_BYTES + 1), ("digest", "sha256:" + "0" * 64)]:
            data = metadata(); data[2][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): qa.validate_metadata(*data)
        data = metadata(); data[2]["workflow_run"]["head_sha"] = "a" * 40
        with self.assertRaises(ValueError): qa.validate_metadata(*data)

    def test_application_git_tree_mismatch_refuses(self):
        data = metadata(); data[3]["tree"]["sha"] = "0" * 40
        with self.assertRaises(ValueError): qa.validate_metadata(*data)

    def test_bad_metadata_collect_never_reaches_archive_get(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / "evidence").mkdir()
            (root / "evidence/HOST.json").write_text('{"fresh":true}')
            data = metadata(); data[1]["conclusion"] = "failure"
            with patch.object(qa, "api", side_effect=data), patch.object(qa, "archive_once") as download:
                with self.assertRaises(ValueError): qa.collect(root)
                download.assert_not_called()
            self.assertFalse((root / "original.zip").exists())
            self.assertFalse((root / "evidence/NORMAL-ARCHIVE-GET-INTENT.json").exists())

    def test_exact_product_tree_and_modes_with_explicit_qa_allowlist(self):
        original = {"branding/branding.env": ("100644", "a" * 40), "scripts/apply-branding.sh": ("100755", "b" * 40)}
        current = dict(original, **{"scripts/linux-installer-systemd.py": ("100644", "c" * 40)})
        self.assertEqual(qa.compare_trees(original, current), ["scripts/linux-installer-systemd.py"])
        for field in ("branding/branding.env", "scripts/apply-branding.sh", "unexpected-new-file"):
            bad = dict(current); bad[field] = ("100755", "d" * 40)
            with self.assertRaises(ValueError): qa.compare_trees(original, bad)
        bad = dict(current); del bad["scripts/apply-branding.sh"]
        with self.assertRaises(ValueError): qa.compare_trees(original, bad)

    def test_tree_parser_rejects_duplicate_newline_and_nonblob(self):
        valid = b"100644 blob " + b"a" * 40 + b"\tfile\0"
        self.assertEqual(qa.parse_tree(valid), {"file": ("100644", "a" * 40)})
        for raw in (valid + valid, valid.replace(b"file", b"file\nname"), valid.replace(b"blob", b"commit")):
            with self.assertRaises(ValueError): qa.parse_tree(raw)

    def test_same_repo_pr_only_and_exact_checkout(self):
        event = {"repository": {"id": qa.REPO_ID, "full_name": qa.REPO}, "pull_request": {
            "head": {"sha": "a" * 40, "repo": {"id": qa.REPO_ID, "full_name": qa.REPO}},
            "base": {"repo": {"id": qa.REPO_ID, "full_name": qa.REPO}}}}
        qa.validate_event(event, "pull_request", "a" * 40)
        for side in ("head", "base"):
            bad = copy.deepcopy(event); bad["pull_request"][side]["repo"]["id"] = 3
            with self.assertRaises(ValueError): qa.validate_event(bad, "pull_request", "a" * 40)
        for name, actual in [("push", "a" * 40), ("pull_request", "b" * 40), ("workflow_dispatch", "x\ny")]:
            with self.assertRaises(ValueError): qa.validate_event(event, name, actual)

    def test_owned_output_paths_newline_dotdot_symlink_refuse(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder).resolve(); good = parent / "mixel-linux-installer-systemd-123-1"
            env = {"RUNNER_TEMP": str(parent), "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}
            with patch.dict(os.environ, env):
                self.assertEqual(qa.root_path(str(good)), good)
                for value in [str(good) + "\n", str(parent / "other"), str(parent / ".." / good.name),
                              str(parent / "worktrees" / good.name), "relative"]:
                    with self.assertRaises(ValueError): qa.root_path(value)
                target = parent / "target"; target.mkdir(); good.symlink_to(target, target_is_directory=True)
                with self.assertRaises(ValueError): qa.root_path(str(good))

    def test_dependency_plan_refuses_remove_and_downgrade(self):
        for text in ("Remv package [1]\n", "Purg package [1]\n"):
            with self.assertRaises(ValueError): qa.validate_apt_plan(text)
        with self.assertRaises(ValueError): qa.validate_apt_plan("Inst malformed\n")
        with patch.object(qa.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            with self.assertRaises(ValueError): qa.validate_apt_plan("Inst package [2] (1 Ubuntu:22.04)\n")
        with patch.object(qa.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            qa.validate_apt_plan("Inst package [1] (2 Ubuntu:22.04)\n")

    def test_original_guest_is_unchanged_and_assertions_not_weakened(self):
        path = BASE / "scripts/linux-installer-systemd-guest.py"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), qa.GUEST_SHA)
        text = path.read_text()
        for text_required in ["actualPID1Systemd", "rootMainPIDExecutableProven", "stable30Seconds",
                              "restartStopStartDisableEnableRemovePurgeProven", "unrelatedSentinelPreserved",
                              "require(run(['dpkg','--audit'])=='',", "policy-rc.d-must-not-mask-real-service"]:
            self.assertIn(text_required, text)

    def test_workflow_is_private_dedicated_same_repo_no_secrets_or_container(self):
        text = (BASE / ".github/workflows/linux-installer-systemd.yml").read_text()
        for expected in ["workflow_dispatch:", "pull_request:", "contents: read", "actions: read",
                         "runs-on: ubuntu-22.04", "persist-credentials: false", "timeout-minutes: 30",
                         "github.event.pull_request.head.repo.full_name == github.repository", "if: always()"]:
            self.assertIn(expected, text)
        for banned in ["secrets.", "pull_request_target", "container:", "publish_r2", "continue-on-error", "write-all", "--force-depends", "allow-unauthenticated"]:
            self.assertNotIn(banned, text)
        self.assertEqual(text.count("GH_TOKEN:"), 1)
        self.assertEqual(text.count('sudo -n env RUNNER_TEMP="$RUNNER_TEMP"'), 4)

    def test_once_intent_and_durable_writes_refuse_reentry(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "intent.json"; qa.save(p, {"count": 1})
            with self.assertRaises(FileExistsError): qa.save(p, {"count": 2})
            self.assertEqual(json.loads(p.read_text()), {"count": 1})

    def test_real_private_evidence_owner_and_mode_handoff(self):
        # Real filesystem checks, without claiming an unavailable local sudo.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(); root.chmod(0o700)
            evidence = root / "evidence"; evidence.mkdir(mode=0o700)
            owner = {"uid": os.geteuid(), "gid": os.getegid()}
            qa.save(evidence / "SOURCE.json", {"outputOwner": owner})
            qa.save(evidence / "HOST.json", {"fresh": True})
            self.assertEqual(qa.evidence_owner(root), owner)
            qa.publish_evidence(root, owner)
            self.assertEqual((evidence / "HOST.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(evidence.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads((evidence / "HOST.json").read_text()), {"fresh": True})
            bad = dict(owner, uid=0)
            (evidence / "SOURCE.json").unlink(); qa.save(evidence / "SOURCE.json", {"outputOwner": bad})
            with self.assertRaises(ValueError): qa.evidence_owner(root)

    def test_cleanup_runs_if_evidence_retention_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); evidence = root / "evidence"; evidence.mkdir()
            qa.save(evidence / "CANDIDATE-EXECUTION-INTENT.json", {"synthetic": True})
            with patch.object(qa, "retain_guest_evidence", side_effect=OSError("synthetic evidence write failure")), \
                 patch.object(qa, "cleanup_package") as cleanup:
                with self.assertRaises(OSError): qa.cleanup(root)
                cleanup.assert_called_once_with(root)

    def test_symlink_evidence_parent_refuses_before_outside_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder).resolve(); root = base / "root"; root.mkdir(mode=0o700)
            other = base / "other"; other.mkdir(mode=0o700)
            owner = {"uid": os.geteuid(), "gid": os.getegid()}
            qa.save(other / "SOURCE.json", {"outputOwner": owner})
            (root / "evidence").symlink_to(other, target_is_directory=True)
            before = (other / "SOURCE.json").stat()
            with self.assertRaises(ValueError): qa.evidence_owner(root)
            with self.assertRaises(ValueError): qa.publish_evidence(root, owner)
            after = (other / "SOURCE.json").stat()
            self.assertEqual((before.st_uid, before.st_gid, before.st_mode), (after.st_uid, after.st_gid, after.st_mode))

    def test_hardlinked_evidence_file_refuses_before_outside_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder).resolve(); root = base / "root"; root.mkdir(mode=0o700)
            evidence = root / "evidence"; evidence.mkdir(mode=0o700)
            owner = {"uid": os.geteuid(), "gid": os.getegid()}
            qa.save(evidence / "SOURCE.json", {"outputOwner": owner})
            other = base / "unrelated-sentinel"; other.write_bytes(b"unchanged synthetic sentinel"); other.chmod(0o640)
            os.link(other, evidence / "hardlink")
            before = other.stat()
            with self.assertRaises(ValueError): qa.publish_evidence(root, owner)
            after = other.stat()
            self.assertEqual((before.st_uid, before.st_gid, before.st_mode), (after.st_uid, after.st_gid, after.st_mode))
            self.assertEqual(other.read_bytes(), b"unchanged synthetic sentinel")

    def test_full_collection_inert_zip_joins_inner_bytes(self):
        package = b"inert fixture bytes; not an executable or real DEB"
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); archive = base / "inert.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("mixel-remote-1.4.6-x86_64.deb", package)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with patch.object(qa, "ARCHIVE_BYTES", archive.stat().st_size), patch.object(qa, "ARCHIVE_SHA", digest):
                for accepted in (True, False):
                    root = base / str(accepted); (root / "evidence").mkdir(parents=True)
                    qa.save(root / "evidence/HOST.json", {"fresh": True})
                    expected = hashlib.sha256(package).hexdigest() if accepted else "0" * 64
                    with patch.object(qa, "DEB_SHA", expected), patch.object(qa, "api", side_effect=metadata()), \
                         patch.object(qa, "archive_once", return_value=archive):
                        if accepted:
                            qa.collect(root)
                            self.assertEqual((root / "candidate.deb").read_bytes(), package)
                            self.assertEqual((root / "candidate.deb").stat().st_mode & 0o777, 0o400)
                        else:
                            with self.assertRaises(ValueError): qa.collect(root)
                            self.assertFalse((root / "candidate.deb").exists())

    def test_real_inert_child_stream_exact_bound_and_once_intent(self):
        real_popen = subprocess.Popen
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for payload, exitcode, accepted in [(b"abc", 0, True), (b"abcd", 0, False), (b"", 42, False)]:
                root = base / str(len(list(base.iterdir()))); (root / "evidence").mkdir(parents=True)
                def inert(argv, **kwargs):
                    self.assertEqual(argv, ["gh", "api", "--hostname", "github.com", qa.intent_endpoint()])
                    return real_popen([sys.executable, "-c", f"import sys;sys.stdout.buffer.write({payload!r});sys.exit({exitcode})"], **kwargs)
                with patch.object(qa, "ARCHIVE_BYTES", 3), patch.object(qa, "ARCHIVE_SHA", hashlib.sha256(b"abc").hexdigest()), \
                     patch.object(qa.subprocess, "Popen", side_effect=inert) as child:
                    if accepted:
                        self.assertEqual(qa.archive_once(root).read_bytes(), b"abc")
                    else:
                        with self.assertRaises(ValueError): qa.archive_once(root)
                    with self.assertRaises(FileExistsError): qa.archive_once(root)
                    self.assertEqual(child.call_count, 1)

    def test_original_host_failure_has_priority_over_diagnostic_failure(self):
        original = ValueError("original health error")
        with patch.object(qa, "command", side_effect=original), \
             patch.object(qa, "diagnose_host", side_effect=OSError("diagnostic write failed")):
            with self.assertRaises(ValueError) as caught: qa.require_healthy_systemd(Path("unused"))
            self.assertIs(caught.exception, original)
        with patch.object(qa, "command", return_value=b"running\n"), patch.object(qa, "diagnose_host") as diagnostic:
            qa.require_healthy_systemd(Path("unused")); diagnostic.assert_not_called()
        with patch.object(qa, "command", return_value=b"degraded\n"), patch.object(qa, "diagnose_host"):
            with self.assertRaisesRegex(ValueError, "unhealthy-systemd"): qa.require_healthy_systemd(Path("unused"))

    def test_failed_unit_names_are_bounded_valid_and_unique(self):
        raw = b"valid.service loaded failed failed description\nvalid.service duplicate\n"
        raw += b"bad;name.service invalid\n../escape.service invalid\n--property=Environment.service invalid\n"
        raw += b"a" * 256 + b".service invalid\n"
        raw += b"escaped\\x2dname.socket loaded failed failed\n"
        raw += b"".join(f"test-{i}.service loaded failed failed\n".encode() for i in range(40))
        names = qa.failed_unit_names(raw)
        self.assertEqual(len(names), 16)
        self.assertEqual(names[:2], ["valid.service", "escaped\\x2dname.socket"])
        self.assertEqual(len(set(names)), 16)
        self.assertFalse(any(";" in name or "/" in name or "=" in name for name in names))

    def test_diagnostics_only_use_explicit_read_only_properties(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / "evidence").mkdir()
            failed = b"".join(f"unit-{i}.service loaded failed failed\n".encode() for i in range(30))
            calls = []
            def observe(root, label, argv):
                calls.append((label, argv)); return failed if label == "host-failed-units" else b""
            with patch.object(qa, "bounded_diagnostic", side_effect=observe): qa.diagnose_host(root)
            self.assertEqual(len(calls), 18)
            self.assertEqual(calls[0][1], ["systemctl", "--failed", "--no-pager", "--plain", "--no-legend"])
            self.assertEqual(calls[1][1], ["systemctl", "list-jobs", "--no-pager", "--no-legend"])
            for label, argv in calls[2:]:
                self.assertEqual(argv[:3], ["systemctl", "show", "--no-pager"])
                self.assertEqual(argv[3], "--property=Id,ActiveState,SubState,Result,FragmentPath,ExecMainCode,ExecMainStatus")
                self.assertEqual(argv[4], "--")
            self.assertNotIn("Environment", repr(calls))
            self.assertNotIn("ExecStart", repr(calls))
            self.assertNotIn("journalctl", repr(calls))

    def test_real_inert_diagnostic_child_output_and_time_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / "evidence").mkdir()
            qa.bounded_diagnostic(root, "large", [sys.executable, "-c", "import sys;sys.stdout.write('x'*1000);sys.stderr.write('y'*1000)"], bound=64)
            result = json.loads((root / "evidence/large/RESULT.json").read_text())
            self.assertEqual(result["error"], "diagnostic-output-bound")
            self.assertLessEqual(result["stdoutBytes"], 64); self.assertLessEqual(result["stderrBytes"], 64)
            self.assertFalse(result["healthAccepted"])
            qa.bounded_diagnostic(root, "timed", [sys.executable, "-c", "import time;time.sleep(3)"], timeout=0.03)
            result = json.loads((root / "evidence/timed/RESULT.json").read_text())
            self.assertEqual(result["error"], "diagnostic-timeout")
            self.assertNotEqual(result["exit"], 0)

    def test_package_identity_states_and_foreign_refusals(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / "evidence").mkdir()
            cases = [(1, b"", None)]
            cases += [(0, f"mixel-remote\t1.4.6\tamd64\t{s}\n".encode(), s) for s in ("installed", "unpacked", "config-files")]
            cases += [(0, row, "REFUSE") for row in (
                b"other\t1.4.6\tamd64\tinstalled\n", b"mixel-remote\t9\tamd64\tinstalled\n",
                b"mixel-remote\t1.4.6\tarm64\tinstalled\n", b"mixel-remote\t1.4.6\tamd64\thalf-installed\n")]
            cases += [(1, b"ambiguous", "REFUSE")]
            for i, (exitcode, raw, expected) in enumerate(cases):
                label = f"query-{i}"; p = root / "evidence" / label; p.mkdir(); qa.save(p / "RESULT.json", {"exit": exitcode})
                with patch.object(qa, "command", return_value=raw):
                    if expected == "REFUSE":
                        with self.assertRaises(ValueError): qa.package_state(root, label)
                    else: self.assertEqual(qa.package_state(root, label), expected)

    def cleanup_fixture(self, root, state, owner=None):
        """Call the actual cleanup function; replace only OS observations/actions."""
        actions = []
        def observed(root, label, argv, *args, **kwargs):
            actions.append((label, argv))
            return b"Purg mixel-remote [1.4.6]\n" if label == "cleanup-purge-plan" else b""
        return actions, [patch.object(qa, "cleanup_binding"),
                         patch.object(qa, "package_state", side_effect=[state, None] if state else [None]),
                         patch.object(qa, "inspect_cleanup_service", return_value=owner),
                         patch.object(qa, "verify_cleanup_payload_if_present"),
                         patch.object(qa, "require_cleanup_absence"),
                         patch.object(qa, "process_birth", return_value=None),
                         patch.object(qa, "service_cgroup_empty", return_value=True),
                         patch.object(qa, "command", side_effect=observed)]

    def test_actual_cleanup_handles_config_files_and_partial_unpack_without_main(self):
        from contextlib import ExitStack
        for state in ("config-files", "unpacked", "installed"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); actions, controls = self.cleanup_fixture(root, state)
                with ExitStack() as stack:
                    for control in controls: stack.enter_context(control)
                    result = qa.cleanup_package(root)
                self.assertEqual(result, {"packageInitially": state, "packageAndOwnedPathsAbsent": True})
                self.assertIn("cleanup-owned-package", [name for name, argv in actions])
                self.assertNotIn("cleanup-owned-service", [name for name, argv in actions])

    def test_remove_then_assertion_failure_keeps_failure_and_purges_residual_config(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / "evidence").mkdir()
            qa.save(root / "evidence/CANDIDATE-EXECUTION-INTENT.json", qa.execution_intent(root))
            failed = root / "evidence/guest-FAILED.json"
            failed.write_bytes(b'{"error":"assertion failed after apt remove","serviceAccepted":false}\n')
            before = failed.read_bytes(); actions, controls = self.cleanup_fixture(root, "config-files")
            with ExitStack() as stack:
                for control in controls: stack.enter_context(control)
                stack.enter_context(patch.object(qa, "retain_guest_evidence"))
                qa.cleanup(root)
            self.assertEqual(failed.read_bytes(), before)
            self.assertIn("cleanup-owned-package", [name for name, argv in actions])
            result = json.loads((root / "evidence/CLEANUP.json").read_text())
            self.assertTrue(result["cleanupVerified"])
            self.assertFalse(result["taskComplete"])

    def test_owned_service_is_proven_and_stopped_before_purge(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); actions, controls = self.cleanup_fixture(root, "installed", {"pid": 123, "birth": "owned-birth"})
            with ExitStack() as stack:
                for control in controls: stack.enter_context(control)
                qa.cleanup_package(root)
            labels = [label for label, argv in actions]
            self.assertLess(labels.index("cleanup-owned-service"), labels.index("cleanup-owned-package"))
            self.assertEqual(actions[0][1], ["systemctl", "stop", "mixel-remote.service"])

    def test_foreign_package_or_unit_refuses_before_stop_or_purge(self):
        root = Path("unused")
        with patch.object(qa, "cleanup_binding"), patch.object(qa, "package_state", side_effect=ValueError("foreign version")), \
             patch.object(qa, "command") as action:
            with self.assertRaises(ValueError): qa.cleanup_package(root)
            action.assert_not_called()
        with patch.object(qa, "cleanup_binding"), patch.object(qa, "package_state", return_value="installed"), \
             patch.object(qa, "inspect_cleanup_service", side_effect=ValueError("unknown unit")), patch.object(qa, "command") as action:
            with self.assertRaises(ValueError): qa.cleanup_package(root)
            action.assert_not_called()

    def test_absent_package_is_benign_only_after_unit_and_path_absence(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); actions, controls = self.cleanup_fixture(root, None)
            with ExitStack() as stack:
                for control in controls: stack.enter_context(control)
                result = qa.cleanup_package(root)
            self.assertEqual(result["packageInitially"], "absent"); self.assertEqual(actions, [])
        with patch.object(qa, "cleanup_binding"), patch.object(qa, "package_state", return_value=None), \
             patch.object(qa, "inspect_cleanup_service", return_value={"pid": 123, "birth": "foreign"}), patch.object(qa, "command") as action:
            with self.assertRaises(ValueError): qa.cleanup_package(Path("unused"))
            action.assert_not_called()

    def test_cleanup_requires_fresh_host_and_exact_intent_before_staged_access(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / "evidence").mkdir()
            qa.save(root / "evidence/HOST.json", {"fresh": False, "actualPID1Systemd": True, "root": True})
            with self.assertRaisesRegex(ValueError, "fresh-host"): qa.cleanup_binding(root)
            (root / "evidence/HOST.json").unlink()
            qa.save(root / "evidence/HOST.json", {"fresh": True, "actualPID1Systemd": True, "root": True})
            qa.save(root / "evidence/CANDIDATE-EXECUTION-INTENT.json", dict(qa.execution_intent(root), instance="foreign"))
            with self.assertRaisesRegex(ValueError, "intent-not-owned"): qa.cleanup_binding(root)

    def test_purge_plan_refuses_any_other_package_change(self):
        qa.validate_purge_plan(b"Purg mixel-remote [1.4.6]\n")
        for raw in (b"Remv unrelated [1]\n", b"Inst unrelated (1 Ubuntu)\n", b"Conf unrelated (1)\n", b"no action\n"):
            with self.assertRaises(ValueError): qa.validate_purge_plan(raw)

    def test_retention_error_survives_cleanup_error_and_both_are_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / "evidence").mkdir(); qa.save(root / "evidence/CANDIDATE-EXECUTION-INTENT.json", {})
            original = OSError("original retention failure")
            with patch.object(qa, "retain_guest_evidence", side_effect=original), patch.object(qa, "cleanup_package", side_effect=ValueError("cleanup refused foreign unit")):
                with self.assertRaises(OSError) as caught: qa.cleanup(root)
                self.assertIs(caught.exception, original)
            receipt = json.loads((root / "evidence/CLEANUP-FAILED.json").read_text())
            self.assertFalse(receipt["cleanupVerified"])
            self.assertEqual(receipt["cleanupError"], "cleanup refused foreign unit")

    def test_actual_apt_context_modes_under_private_umask(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "new-run"
            identity = SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid())
            before = os.umask(0o077)
            try:
                with patch.object(qa.pwd, "getpwnam", return_value=identity) as sandbox:
                    context, sources = qa.official_apt_context(root, Path(folder))
                    sandbox.assert_called_once_with("_apt")
            finally: os.umask(before)
            self.assertEqual(context.stat().st_mode & 0o777, 0o755)
            self.assertEqual(sources.stat().st_mode & 0o777, 0o644)
            self.assertEqual((context / "lists").stat().st_mode & 0o777, 0o755)
            self.assertEqual((context / "lists/partial").stat().st_mode & 0o777, 0o700)
            self.assertEqual((context / "lists/partial").stat().st_uid, identity.pw_uid)

    def test_actual_unit_inspection_refuses_unknown_foreign_or_residual_unit(self):
        props = {"Id": "mixel-remote.service", "LoadState": "not-found", "FragmentPath": "",
                 "MainPID": "0", "ControlGroup": "", "ActiveState": "inactive", "SubState": "dead", "DropInPaths": ""}
        def raw(properties): return "".join(f"{k}={v}\n" for k, v in properties.items()).encode()
        with patch.object(qa, "command", return_value=raw(props)) as query, patch.object(qa, "service_cgroup_empty", return_value=True):
            self.assertIsNone(qa.inspect_cleanup_service(Path("unused"), "query"))
            self.assertEqual(query.call_args.args[2], ["systemctl", "show", "--all", "mixel-remote.service",
                             "--property=Id,LoadState,FragmentPath,MainPID,ControlGroup,ActiveState,SubState,DropInPaths"])
        for field, value in [("Id", "other.service"), ("MainPID", "123"), ("ControlGroup", "/foreign"),
                             ("LoadState", "masked"), ("FragmentPath", "/foreign.service"), ("ActiveState", "active")]:
            changed = dict(props, **{field: value})
            with patch.object(qa, "command", return_value=raw(changed)), patch.object(qa, "service_cgroup_empty", return_value=True):
                with self.subTest(field=field), self.assertRaises(ValueError): qa.inspect_cleanup_service(Path("unused"), "query")

    def test_actual_process_inspection_requires_root_exe_hash_birth_and_cgroup(self):
        process = MagicMock(); process.stat.return_value = SimpleNamespace(st_uid=0)
        executable = MagicMock(); process.__truediv__.return_value = executable
        cgroup = MagicMock(); cgroup.read_text.return_value = "123\n"
        def path(value):
            return process if str(value) == "/proc/123" else cgroup
        with patch.object(qa, "Path", side_effect=path), patch.object(qa.os, "readlink", return_value="/usr/share/mixel-remote/mixel-remote"), \
             patch.object(qa, "sha", return_value=qa.MAIN_SHA), patch.object(qa, "process_birth", return_value="owned-birth"):
            self.assertEqual(qa.inspect_service_process(123), {"pid": 123, "birth": "owned-birth"})
            process.stat.return_value = SimpleNamespace(st_uid=1001)
            with self.assertRaises(ValueError): qa.inspect_service_process(123)
            process.stat.return_value = SimpleNamespace(st_uid=0); cgroup.read_text.return_value = "999\n"
            with self.assertRaises(ValueError): qa.inspect_service_process(123)
        with patch.object(qa, "Path", side_effect=path), patch.object(qa.os, "readlink", return_value="/foreign/executable"), \
             patch.object(qa, "sha", return_value=qa.MAIN_SHA):
            with self.assertRaises(ValueError): qa.inspect_service_process(123)

    def test_actual_parser_refuses_foreign_dropin_even_with_matching_fragment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); fragment = root / "mixel-remote.service"; fragment.write_bytes(b"inert exact unit fixture")
            props = {"Id": "mixel-remote.service", "LoadState": "loaded", "FragmentPath": str(fragment),
                     "MainPID": "123", "ControlGroup": "/system.slice/mixel-remote.service",
                     "ActiveState": "active", "SubState": "running", "DropInPaths": ""}
            def raw(): return "".join(f"{k}={v}\n" for k, v in props.items()).encode()
            def resolved(value):
                return "/expected-unit" if str(value) in (str(fragment), "/usr/lib/systemd/system/mixel-remote.service") else str(value)
            owner = {"pid": 123, "birth": "owned"}
            with patch.object(qa, "INSTALLED_UNIT", hashlib.sha256(fragment.read_bytes()).hexdigest()), \
                 patch.object(qa.os.path, "realpath", side_effect=resolved), \
                 patch.object(qa, "inspect_service_process", return_value=owner):
                with patch.object(qa, "command", return_value=raw()):
                    self.assertEqual(qa.inspect_cleanup_service(root, "positive"), owner)
                props["DropInPaths"] = "/run/systemd/system/mixel-remote.service.d/foreign.conf"
                calls = []
                def observe(root, label, argv, *args, **kwargs):
                    calls.append((label, argv)); return raw()
                with patch.object(qa, "cleanup_binding"), patch.object(qa, "package_state", return_value="installed"), \
                     patch.object(qa, "command", side_effect=observe):
                    with self.assertRaisesRegex(ValueError, "dropin"): qa.cleanup_package(root)
                self.assertEqual([label for label, argv in calls], ["cleanup-unit-before"])

    def test_fresh_host_override_roots_use_lexists_including_dangling_symlink(self):
        # Execute the actual host guard through its filesystem observation seam.
        roots = {"/etc/systemd/system/mixel-remote.service.d", "/run/systemd/system/mixel-remote.service.d",
                 "/usr/lib/systemd/system/mixel-remote.service.d"}
        original_path = Path
        original_lexists = os.path.lexists
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder).resolve(); (base / "evidence").mkdir()
            dangling = base / "dangling"; dangling.symlink_to(base / "absent")
            release = MagicMock(); release.read_text.return_value = 'ID=ubuntu\nVERSION_ID="22.04"\n'
            pid1 = MagicMock(); pid1.resolve.return_value.name = "systemd"
            controllers = MagicMock(); controllers.is_file.return_value = True
            def observed_path(value):
                return {"/etc/os-release": release, "/proc/1/exe": pid1,
                        "/sys/fs/cgroup/cgroup.controllers": controllers}.get(str(value), original_path(value))
            for override in roots:
                def exists(value): return original_lexists(dangling) if str(value) == override else False
                with patch.object(qa, "Path", side_effect=observed_path), patch.object(qa.os, "geteuid", return_value=0), \
                     patch.object(qa.os, "uname", return_value=SimpleNamespace(machine="x86_64")), \
                     patch.object(qa.os.path, "lexists", side_effect=exists), \
                     patch.object(qa.subprocess, "run", side_effect=AssertionError("must refuse before any host command")), \
                     patch.object(qa, "require_healthy_systemd") as health:
                    with self.assertRaisesRegex(ValueError, "host-not-fresh"): qa.host(base)
                    health.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
