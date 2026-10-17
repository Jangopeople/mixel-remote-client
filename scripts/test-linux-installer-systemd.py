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
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
