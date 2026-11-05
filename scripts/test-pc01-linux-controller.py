#!/usr/bin/env python3
"""Inert controls only: synthetic metadata/ZIPs/pipes, no processes or network."""
import ast
import copy
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

SOURCE = Path(__file__).with_name("pc01-linux-controller.py")
spec = importlib.util.spec_from_file_location("pc01_controller", SOURCE)
M = importlib.util.module_from_spec(spec)
exec(compile(SOURCE.read_bytes(), str(SOURCE), "exec"), M.__dict__)
NOW = dt.datetime(2026, 10, 17, 12, 0, 0, tzinfo=dt.timezone.utc)


def admission():
    """Every ID/digest here is explicitly synthetic, never a future witness."""
    return {"schema": "mixel-current070-PC01-controller-admission-v1", "decidedBy": "Root",
        "decision": "admit-one-private-hosted-controller-PC01", "applicationSource": M.APP_SOURCE,
        "applicationTree": M.APP_TREE, "producerRun": M.PRODUCER, "producerAttempt": 1,
        "qaSource": "1" * 40, "qaTree": "2" * 40, "nonce": "0123456789abcdef01234567",
        "flags": M.FLAGS, "syntheticDesktopOnly": True, "bothClipboardPreflightsPassed": True,
        "independentNASConsoleReady": True, "windowsQualificationSHA256": "3" * 64,
        "windowsCold95ReceiptSHA256": "4" * 64, "linuxQualificationSHA256": "5" * 64,
        "linuxTerminalOriginalSHA256": "6" * 64, "linuxShippingJob": 987654321,
        "host": {"id": "123456789", "incomingPID": 1001, "creationFileTime": 12345678901234,
                 "session": 1, "integrity": 8192, "elevated": False,
                 "outerSHA256": "7" * 64, "mainSHA256": "8" * 64, "coreSHA256": "9" * 64,
                 "attendedV2": True, "kernelLease": True, "authenticatedRemoteCount": 0,
                 "observedAt": NOW.isoformat(),
                 "syntheticFolder": "C:\\Users\\Synthetic-QA\\AppData\\Local\\Temp\\Mixel-QA-0123456789abcdef01234567"},
        "artifact": {"id": 123456789, "bytes": 321, "sha256": "a" * 64, "debSHA256": "b" * 64, "mainSHA256": "c" * 64}}


def decode(a):
    b = json.dumps(a).encode()
    return M.safe_admission(b, M.sha(b), NOW)


def zip_bytes(name="mixel-remote-1.4.6-x86_64.deb", data=b"explicitly-inert-not-a-real-DEB", mode=stat.S_IFREG | 0o644):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        entry = zipfile.ZipInfo(name)
        entry.external_attr = mode << 16
        z.writestr(entry, data)
    return out.getvalue()


def metadata(a):
    run = {"id": M.PRODUCER, "run_attempt": 1, "head_sha": M.APP_SOURCE,
           "repository": {"id": 1229987840, "full_name": M.REPOSITORY},
           "workflow_id": 271445168, "head_branch": "fix/remote-support-reliability",
           "event": "workflow_dispatch", "path": ".github/workflows/build.yml", "status": "completed", "conclusion": "success"}
    job = {"id": a["linuxShippingJob"], "run_id": M.PRODUCER, "name": "linux", "status": "completed", "conclusion": "success",
           "started_at": "2026-10-17T10:00:00Z", "completed_at": "2026-10-17T11:00:00Z",
           "steps": [{"status": "completed", "conclusion": "success"}]}
    artifact = {"id": a["artifact"]["id"], "name": "linux", "expired": False, "size_in_bytes": a["artifact"]["bytes"],
                "digest": "sha256:" + a["artifact"]["sha256"], "created_at": "2026-10-17T10:59:00Z",
                "workflow_run": {"id": M.PRODUCER, "head_sha": M.APP_SOURCE}}
    return run, job, artifact


def private_admission():
    """In-memory policy fixture only; never a Root decision or runtime receipt."""
    a = admission()
    a["linuxEligibility"] = "private-successful-linux-job"
    a["linuxShippingJob"] = M.PRIVATE_LINUX_JOB
    a["linuxCollectionSHA256"] = "d" * 64  # Explicitly synthetic qualification dependency.
    spec = M.PRIVATE_LINUX_ARTIFACTS["linux"]
    a["artifact"].update(id=spec["id"], bytes=spec["size_in_bytes"], sha256=spec["digest"][7:])
    p = copy.deepcopy({"scope": "private-successful-linux-job", "schema": "mixel-current070-private-linux-per-job-policy-v1",
        "decidedBy": "Root", "decision": "admit-one-private-linux-normal-and-QA-collection",
        "applicationSource": M.APP_SOURCE, "applicationTree": M.APP_TREE, "producerRun": M.PRODUCER,
        "attempt": 1, "linuxJob": M.PRIVATE_LINUX_JOB, "collectorSHA256": M.PRIVATE_COLLECTOR_SHA256,
        "cacheIndex": M.PRIVATE_CACHE_INDEX, "artifactSelection": M.PRIVATE_ARTIFACT_SELECTION,
        "configReceipt": M.PRIVATE_CONFIG_RECEIPT, "producerObservation": {"status": "in_progress", "conclusion": None},
        "artifacts": M.PRIVATE_LINUX_ARTIFACTS, "allowProducerNonSuccessForPrivateQA": True, "flags": M.PRIVATE_FLAGS})
    attach_policy(a, p)
    return a, p


def attach_policy(a, policy):
    raw = json.dumps(policy, sort_keys=True) + "\n"
    a["linuxPolicy"] = {"json": raw, "sha256": M.sha(raw.encode())}


def private_metadata(a, state=("in_progress", None)):
    run, job, artifact = metadata(a)
    run.update(status=state[0], conclusion=state[1], head_repository={"id": 1229987840, "full_name": M.REPOSITORY})
    job.update(run_attempt=1, head_sha=M.APP_SOURCE, head_branch="fix/remote-support-reliability",
               steps=[{"number": number, "name": name, "status": "completed", "conclusion": "success"} for number, name in M.PRIVATE_LINUX_STEPS])
    artifact["workflow_run"].update(repository_id=1229987840, head_repository_id=1229987840, head_branch="fix/remote-support-reliability")
    return run, job, artifact


class PipeChild:
    """Real finite pipe bytes, no child process/CLI/API execution."""
    def __init__(self, out, err=b"", code=0):
        self.stdout, self.stderr = self.pipe(out), self.pipe(err)
        self.code = code
    @staticmethod
    def pipe(data):
        r, w = os.pipe()
        os.write(w, data)
        os.close(w)
        return os.fdopen(r, "rb")
    def poll(self):
        return self.code
    def wait(self, timeout=None):
        return self.code
    def kill(self):
        self.code = -9


class Controls(unittest.TestCase):
    def setUp(self):
        self.no_run = patch.object(M.subprocess, "run", side_effect=AssertionError("process execution forbidden in inert controls"))
        self.no_popen = patch.object(M.subprocess, "Popen", side_effect=AssertionError("provider/process execution forbidden"))
        self.no_run.start(); self.no_popen.start()
        self.addCleanup(self.no_run.stop); self.addCleanup(self.no_popen.stop)

    def test_actual_required_fields_and_no_unknown_defaults(self):
        self.assertEqual(decode(admission())["producerRun"], M.PRODUCER)
        for field in ("linuxShippingJob", "artifact", "windowsQualificationSHA256", "linuxTerminalOriginalSHA256"):
            with self.subTest(field=field):
                a = admission(); del a[field]
                with self.assertRaises((ValueError, KeyError)): decode(a)

    def test_source_attempt_flags_and_independent_prerequisites_refuse(self):
        cases = [("applicationSource", "0" * 40), ("producerAttempt", 2), ("producerAttempt", True), ("syntheticDesktopOnly", False),
                 ("bothClipboardPreflightsPassed", False), ("independentNASConsoleReady", False)]
        for key, value in cases:
            a = admission(); a[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): decode(a)
        a = admission(); a["flags"] = {**M.FLAGS, "nativeQualified": True}
        with self.assertRaises(ValueError): decode(a)
        a = admission(); a["flags"] = {key: 0 for key in M.FLAGS}
        with self.assertRaises(ValueError): decode(a)

    def test_credentials_URI_IP_and_wrong_host_token_refuse(self):
        for key in ("password", "inviteURI", "APIKEY", "secret", "approvalToken"):
            a = admission(); a[key] = "never-executed"
            with self.subTest(key=key), self.assertRaises(ValueError): decode(a)
        for value in ("1.2.3.4", "mixel-remote://support/", "123456 --password x"):
            a = admission(); a["host"]["id"] = value
            with self.assertRaises(ValueError): decode(a)
        for key, value in (("integrity", 12288), ("elevated", True), ("authenticatedRemoteCount", False), ("kernelLease", False)):
            a = admission(); a["host"][key] = value
            with self.assertRaises(ValueError): decode(a)

    def test_host_freshness_and_owned_TEMP_only(self):
        for value in ("2026-10-17T11:00:00Z", "2026-10-17T12:00:01Z"):
            a = admission(); a["host"]["observedAt"] = value
            with self.assertRaises(ValueError): decode(a)
        for folder in ("C:\\Users\\QA\\Documents", "\\\\server\\share", "C:\\Users\\QA\\AppData\\Local\\Temp\\Mixel-QA-..", "C:\\Users\\..\\AppData\\Local\\Temp\\Mixel-QA-0123456789abcdef01234567"):
            a = admission(); a["host"]["syntheticFolder"] = folder
            with self.assertRaises(ValueError): decode(a)

    def test_hosted_environment_is_mandatory(self):
        a = admission()
        env = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_ARCH": "X64", "GITHUB_REPOSITORY": M.REPOSITORY,
               "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_JOB": "pc01-controller", "GITHUB_SHA": a["qaSource"], "GITHUB_RUN_ATTEMPT": "1"}
        M.hosted_guard(a, env, "Linux")
        for key in env:
            bad = dict(env); bad[key] = "wrong"
            with self.subTest(key=key), self.assertRaises(ValueError): M.hosted_guard(a, bad, "Linux")
        with self.assertRaises(ValueError): M.hosted_guard(a, env, "Darwin")

    def test_all_other_product_blobs_modes_and_unknown_paths_are_exact(self):
        base = {"app.rs": ["100644", "a" * 40], "HANDOFF.md": ["100644", "b" * 40]}
        qa = {**base, **{path: ["100644", "c" * 40] for path in M.QA_PATHS - {"HANDOFF.md"}}}
        self.assertEqual(set(M.compare_inventory(base, qa)), M.QA_PATHS - {"HANDOFF.md"})
        for field in (0, 1):
            bad = copy.deepcopy(qa); bad["app.rs"][field] = "changed"
            with self.assertRaises(ValueError): M.compare_inventory(base, bad)
        bad = copy.deepcopy(qa); bad["HANDOFF.md"][0] = "100755"
        with self.assertRaises(ValueError): M.compare_inventory(base, bad)
        bad = copy.deepcopy(qa); bad["scripts/pc01-linux-controller.py"][0] = "120000"
        with self.assertRaises(ValueError): M.compare_inventory(base, bad)
        qa["unreviewed.py"] = ["100644", "f" * 40]
        with self.assertRaises(ValueError): M.compare_inventory(base, qa)

    def test_relay_only_actual_ID_argv(self):
        self.assertEqual(M.session_argv("--connect", "123456789"), ["--connect", "123456789", "--relay"])
        self.assertEqual(M.session_argv("--file-transfer", "123456789"), ["--file-transfer", "123456789", "--relay"])
        for kind, host in (("--password", "123456789"), ("--connect", "1.2.3.4"), ("--connect", "x;echo unsafe")):
            with self.assertRaises(ValueError): M.session_argv(kind, host)

    def test_exact_synthetic_marker_not_substring_and_no_ambiguous_mouse_target(self):
        self.assertTrue(M.ocr_has("word mixelready123456 other", "mixelready123456"))
        self.assertFalse(M.ocr_has("xmixelready123456", "mixelready123456"))
        tsv = "left\ttop\twidth\theight\ttext\n10\t20\t40\t12\tmixelready123456\n"
        self.assertEqual(M.marker_center(tsv, "mixelready123456"), (30, 26))
        with self.assertRaises(ValueError): M.marker_center(tsv + "50\t20\t40\t12\tmixelready123456\n", "mixelready123456")

    def test_fresh_successful_source_job_artifact_and_upload_window(self):
        a = admission(); run, job, artifact = metadata(a)
        M.package_metadata(a, run, job, artifact)
        cases = [(0, "head_sha", "0" * 40), (0, "conclusion", "failure"), (0, "run_attempt", 2),
                 (1, "id", 1), (1, "conclusion", "cancelled"), (2, "expired", True),
                 (2, "digest", "sha256:" + "0" * 64), (2, "created_at", "2026-10-17T11:00:01Z")]
        for index, key, value in cases:
            values = list(copy.deepcopy((run, job, artifact))); values[index][key] = value
            with self.subTest(index=index, key=key), self.assertRaises(ValueError): M.package_metadata(a, *values)

    def test_default_whole_producer_route_still_requires_success(self):
        a=admission()
        self.assertIsNone(M.linux_eligibility(a))
        for state in (("in_progress",None),("completed","failure")):
            run,job,artifact=metadata(a);run.update(status=state[0],conclusion=state[1])
            with self.subTest(state=state),self.assertRaises(ValueError):M.package_metadata(a,run,job,artifact)
        a["linuxEligibility"]="unknown-policy-route"
        with self.assertRaises(ValueError):decode(a)

    def test_private_route_requires_exact_policy_bytes_and_separate_runtime_admission(self):
        a,p=private_admission();self.assertEqual(decode(a)["linuxEligibility"],"private-successful-linux-job")
        self.assertEqual(M.linux_eligibility(a),p)
        b=copy.deepcopy(a);del b["linuxPolicy"]
        with self.assertRaises(ValueError):decode(b)
        b=copy.deepcopy(a);b["linuxPolicy"]["sha256"]="0"*64
        with self.assertRaises(ValueError):decode(b)
        b=copy.deepcopy(a);del b["linuxEligibility"]
        with self.assertRaises(ValueError):decode(b)
        raw=json.dumps(p).encode()
        with self.assertRaises(ValueError):M.safe_admission(raw,M.sha(raw),NOW)
        b=copy.deepcopy(a);del b["windowsQualificationSHA256"]
        with self.assertRaises(ValueError):decode(b)
        b=copy.deepcopy(a);del b["linuxCollectionSHA256"]
        with self.assertRaises(ValueError):decode(b)

    def test_private_policy_wrong_source_scope_config_flags_and_collector_refuse(self):
        for key,value in (("scope","whole-producer-success"),("applicationSource","0"*40),("linuxJob",1),
                          ("attempt",2),("collectorSHA256","0"*64),("allowProducerNonSuccessForPrivateQA",False)):
            a,p=private_admission();p[key]=value;attach_policy(a,p)
            with self.subTest(key=key),self.assertRaises(ValueError):decode(a)
        for key in ("cacheIndex","artifactSelection","configReceipt"):
            a,p=private_admission();p[key]["sha256"]="0"*64;attach_policy(a,p)
            with self.subTest(pin=key),self.assertRaises(ValueError):decode(a)
        a,p=private_admission();p["flags"]["wholeThreeSuccess"]=True;attach_policy(a,p)
        with self.assertRaises(ValueError):decode(a)
        a,p=private_admission();p["flags"]={key:0 for key in M.PRIVATE_FLAGS};attach_policy(a,p)
        with self.assertRaises(ValueError):decode(a)

    def test_private_policy_duplicate_fields_and_artifact_mismatch_refuse(self):
        a,p=private_admission();raw=a["linuxPolicy"]["json"].replace('{','{"scope":"private-successful-linux-job",',1)
        a["linuxPolicy"]={"json":raw,"sha256":M.sha(raw.encode())}
        with self.assertRaises(ValueError):decode(a)
        a,p=private_admission();a["artifact"]["id"]+=1
        with self.assertRaises(ValueError):decode(a)
        a,p=private_admission();p["artifacts"]["linux"]["digest"]="sha256:"+"0"*64;attach_policy(a,p)
        with self.assertRaises(ValueError):decode(a)

    def test_private_observed_producer_failure_is_never_whole_success_credit(self):
        a,p=private_admission()
        for state in (("in_progress",None),("completed","failure"),("completed","success")):
            gate=M.private_package_metadata(a,*private_metadata(a,state),p)
            self.assertEqual((gate["producerStatus"],gate["producerConclusion"]),state)
            self.assertFalse(gate["wholeProducerSuccessAsserted"])
            self.assertFalse(gate["wholeThreeSuccess"]);self.assertFalse(gate["nativeQualified"])
        for state in (("queued",None),("completed","cancelled"),("completed",None),("in_progress","failure")):
            with self.subTest(state=state),self.assertRaises(ValueError):M.private_package_metadata(a,*private_metadata(a,state),p)
        p["producerObservation"]={"status":"completed","conclusion":"failure"};attach_policy(a,p)
        for state in (("in_progress",None),("completed","success")):
            with self.assertRaises(ValueError):M.private_package_metadata(a,*private_metadata(a,state),p)

    def test_private_current_job_all33_named_steps_mandatory(self):
        a,p=private_admission();run,job,artifact=private_metadata(a)
        self.assertEqual(len(job["steps"]),33)
        M.private_package_metadata(a,run,job,artifact,p)
        for kind in ("missing","duplicate","moved","name","number","failure","skipped"):
            changed=copy.deepcopy(job)
            if kind=="missing":changed["steps"].pop()
            elif kind=="duplicate":changed["steps"].append(copy.deepcopy(changed["steps"][0]))
            elif kind=="moved":changed["steps"][0],changed["steps"][1]=changed["steps"][1],changed["steps"][0]
            elif kind in ("failure","skipped"):changed["steps"][16]["conclusion"]=kind
            else:changed["steps"][16][kind]="different"
            with self.subTest(kind=kind),self.assertRaises(ValueError):M.private_package_metadata(a,run,changed,artifact,p)

    def test_private_job_artifact_source_and_expiry_remain_strict(self):
        a,p=private_admission();run,job,artifact=private_metadata(a)
        for index,key,value in ((0,"head_sha","0"*40),(1,"run_attempt",2),(1,"conclusion","failure"),
                                (2,"expired",True),(2,"id",1),(2,"created_at","2026-10-17T11:00:01Z")):
            values=list(copy.deepcopy((run,job,artifact)));values[index][key]=value
            with self.subTest(index=index,key=key),self.assertRaises(ValueError):M.private_package_metadata(a,*values,p)

    def test_only_exact_closed_QA_texture_overlay_permitted(self):
        texture,original,corrected=M.TEXTURE_QA_OVERLAY
        base={"app.rs":["100644","a"*40],texture:list(original),"HANDOFF.md":["100644","e"*40]}
        qa={**base,**{path:["100644","c"*40] for path in M.QA_PATHS}}
        qa[texture]=list(corrected)
        self.assertEqual(set(M.compare_inventory(base,qa)),M.QA_PATHS|{texture})
        for bad in (["100644","0"*40],["100755",corrected[1]]):
            changed=copy.deepcopy(qa);changed[texture]=bad
            with self.assertRaises(ValueError):M.compare_inventory(base,changed)
        changed=copy.deepcopy(qa);changed["HANDOFF.md"][1]="f"*40
        with self.assertRaises(ValueError):M.compare_inventory(base,changed)
        changed=copy.deepcopy(base);changed[texture][1]="0"*40
        with self.assertRaises(ValueError):M.compare_inventory(changed,qa)

    def test_actual_inert_ZIP_CRC_digest_DEB_and_regular_member(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary) / "original.zip"
            content = b"explicitly-inert-not-a-real-DEB"
            data = zip_bytes(data=content); p.write_bytes(data)
            a = admission(); a["artifact"].update(bytes=len(data), sha256=M.sha(data), debSHA256=M.sha(content))
            self.assertEqual(M.package_bytes(p, a)[1], content)
            for bad in (zip_bytes("../unsafe.deb"), zip_bytes(mode=stat.S_IFLNK | 0o777)):
                p.write_bytes(bad); a["artifact"].update(bytes=len(bad), sha256=M.sha(bad))
                with self.assertRaises(ValueError): M.package_bytes(p, a)
            corrupt = bytearray(data); offset = corrupt.index(content); corrupt[offset] ^= 1
            p.write_bytes(corrupt); a["artifact"].update(bytes=len(corrupt), sha256=M.sha(corrupt))
            with self.assertRaises(zipfile.BadZipFile): M.package_bytes(p, a)

    def test_REAL_provider_metadata_then_exactly_one_archive_through_inert_pipes(self):
        a = admission(); data = zip_bytes(); a["artifact"].update(bytes=len(data), sha256=M.sha(data), debSHA256=M.sha(b"explicitly-inert-not-a-real-DEB"))
        values = [json.dumps(x).encode() for x in metadata(a)] + [data]
        calls = []
        def fake(argv, **kwargs):
            calls.append(argv)
            return PipeChild(values[len(calls)-1])
        with tempfile.TemporaryDirectory() as temporary, patch.object(M.subprocess, "Popen", side_effect=fake):
            provider = M.Provider(Path(temporary), a)
            prefix = "repos/" + M.REPOSITORY
            paths = [prefix + "/actions/runs/" + str(M.PRODUCER), prefix + "/actions/jobs/" + str(a["linuxShippingJob"]),
                     prefix + "/actions/artifacts/" + str(a["artifact"]["id"])]
            docs = [json.loads(provider.fetch(x).read_bytes()) for x in paths]
            M.package_metadata(a, *docs)
            archive = provider.fetch(paths[-1] + "/zip", archive=True)
            self.assertEqual(M.package_bytes(archive, a)[0], "mixel-remote-1.4.6-x86_64.deb")
            self.assertEqual(len(calls), 4)
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            for endpoint, flag in ((paths[-1]+"/zip", True), (paths[0], False)):
                with self.assertRaises(ValueError): provider.fetch(endpoint, archive=flag)
            self.assertEqual(len(calls), 4)

    def test_provider_refuses_early_or_unselected_GET_before_any_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = M.Provider(Path(temporary), admission())
            for endpoint in ("repos/other/actions/runs/1", "repos/"+M.REPOSITORY+"/actions/artifacts/123456789/zip"):
                with self.assertRaises(ValueError): p.fetch(endpoint, archive=True)

    def test_provider_nonzero_stops_without_retry(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(M.subprocess, "Popen", return_value=PipeChild(b"{}", b"synthetic-failure", 1)) as child:
            p = M.Provider(Path(temporary), admission())
            endpoint = "repos/"+M.REPOSITORY+"/actions/runs/"+str(M.PRODUCER)
            with self.assertRaises(ValueError): p.fetch(endpoint)
            with self.assertRaises(ValueError): p.fetch(endpoint)
            self.assertEqual(child.call_count, 1)
            self.assertTrue((Path(temporary)/"request-1/INTENT.json").is_file())
            self.assertEqual(json.loads((Path(temporary)/"request-1/RESULT.json").read_bytes())["exit"], 1)

    def test_provider_archive_hard_bound_precedes_write(self):
        a = admission(); a["artifact"]["bytes"] = 2
        with tempfile.TemporaryDirectory() as temporary, patch.object(M.subprocess, "Popen", return_value=PipeChild(b"123")):
            p = M.Provider(Path(temporary), a)
            p.number = 3  # Exercise only the actual bounded-reader branch, with no metadata claims.
            with self.assertRaisesRegex(ValueError, "provider-byte-bound"):
                p.fetch("repos/"+M.REPOSITORY+"/actions/artifacts/123456789/zip", archive=True)
            self.assertEqual((Path(temporary)/"request-4/original.stdout").stat().st_size, 0)
            self.assertTrue((Path(temporary)/"request-4/INTENT.json").is_file())

    def test_late_app_failure_is_retained_while_exact_owned_cleanup_continues(self):
        actor = object.__new__(M.Controller)
        actor.created = True; actor.name = "explicitly-inert-owned-name"; actor.image_id = "sha256:" + "a" * 64
        actor.output = Path("/explicitly-inert-output")
        actor.ownership = lambda **kwargs: None
        def failed(**kwargs):
            raise RuntimeError("synthetic-nonzero-child-status")
        actor.alive = failed
        calls = []
        def fake(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=json.dumps([{"Id":actor.image_id,"Config":{"Labels":{"com.mixel.qa":actor.name}}}]))
        with patch.object(M, "command", side_effect=fake):
            outcome = actor.cleanup()
        self.assertEqual(outcome, ["FAILED-final-app-status:RuntimeError", "own-container-stopped-and-removed", "own-image-removed"])
        self.assertEqual(calls[1], ["docker", "stop", "--time", "8", actor.name])
        self.assertEqual(calls[2], ["docker", "rm", actor.name])
        self.assertNotEqual(outcome, ["own-container-stopped-and-removed", "own-image-removed"])

    @staticmethod
    def actor_for_cleanup():
        actor = object.__new__(M.Controller)
        actor.created = True; actor.name = "explicitly-inert-owned-name"; actor.image_id = "sha256:" + "a" * 64
        actor.output = Path("/explicitly-inert-output")
        actor.ownership = lambda **kwargs: None
        actor.alive = lambda **kwargs: None
        return actor

    def test_copy_exception_cannot_skip_owned_stop_and_plain_remove(self):
        actor = self.actor_for_cleanup(); calls=[]
        def fake(args, **kwargs):
            calls.append((args,kwargs))
            if args[1] == "cp":
                raise M.subprocess.TimeoutExpired(args,kwargs['timeout'])
            return SimpleNamespace(returncode=0, stdout=json.dumps([{"Id":actor.image_id,"Config":{"Labels":{"com.mixel.qa":actor.name}}}]))
        with patch.object(M,"command",side_effect=fake): outcome=actor.cleanup()
        self.assertEqual(outcome,["FAILED-proof-copy:TimeoutExpired","own-container-stopped-and-removed","own-image-removed"])
        self.assertEqual([x[0][1] for x in calls],['cp','stop','rm','image','image'])
        self.assertTrue(all(0<x[1]['timeout']<=12 for x in calls))
        # ownership5 + at most2 identity2 + exit-status2 + copy10/stop12/rm5/image3+5 =46s < TERM→KILL60s.
        self.assertLess(5+2*2+2+sum(x[1]['timeout'] for x in calls),60)
        self.assertFalse(any('--force' in x[0] for x in calls))

    def test_stop_exception_still_attempts_owned_plain_remove(self):
        actor=self.actor_for_cleanup();calls=[]
        def fake(args,**kwargs):
            calls.append(args)
            if args[1]=='stop':raise OSError('explicitly-inert-stop-error')
            return SimpleNamespace(returncode=0,stdout=json.dumps([{"Id":actor.image_id,"Config":{"Labels":{"com.mixel.qa":actor.name}}}]))
        with patch.object(M,'command',side_effect=fake):outcome=actor.cleanup()
        self.assertIn('FAILED-stop:OSError',outcome)
        self.assertEqual(calls[2],['docker','rm',actor.name])
        self.assertNotIn('own-container-stopped-and-removed',outcome)

    def test_unknown_ownership_never_copies_stops_removes_or_removes_image(self):
        actor=self.actor_for_cleanup()
        def unknown(**kwargs):raise ValueError('explicitly-inert-unknown-owner')
        actor.ownership=unknown
        with patch.object(M,'command',side_effect=AssertionError('foreign-resource-action')) as command:
            outcome=actor.cleanup()
        self.assertEqual(outcome,['FAILED-container-ownership:ValueError'])
        self.assertEqual(command.call_count,0)

    def test_finite_nonsecret_projection_fixes_other_UID_readability_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            outer=Path(temporary);outer.chmod(0o700);payload=outer/'payload';payload.mkdir(mode=0o700)
            expected={}
            for name in ('client.deb',M.FILE,'ipc_probe.py','controller-primitives.py'):
                value=('explicitly-inert-nonsecret-'+name).encode();(payload/name).write_bytes(value);(payload/name).chmod(0o600);expected[name]=M.sha(value)
            result=M.project_payload(payload,expected)
            self.assertEqual(stat.S_IMODE(outer.stat().st_mode),0o700)
            self.assertEqual(stat.S_IMODE(payload.stat().st_mode),0o755)
            # These actual other-user bits cover guest1000 even when runnerUID1001.
            self.assertEqual(stat.S_IMODE(payload.stat().st_mode)&0o005,0o005)
            for name in expected:
                self.assertEqual(stat.S_IMODE((payload/name).stat().st_mode),0o644)
                self.assertTrue((payload/name).stat().st_mode&stat.S_IROTH)
            self.assertTrue(result['readonlyBindRequired'])

    def test_projection_refuses_extra_link_and_hardlink_before_any_chmod(self):
        for bad in ('extra','symlink','hardlink'):
            with self.subTest(bad=bad),tempfile.TemporaryDirectory() as temporary:
                outer=Path(temporary);payload=outer/'payload';payload.mkdir(mode=0o700)
                expected={}
                for name in ('client.deb',M.FILE,'ipc_probe.py','controller-primitives.py'):
                    (payload/name).write_bytes(b'explicitly-inert');(payload/name).chmod(0o600);expected[name]=M.sha(b'explicitly-inert')
                sentinel=outer/'outside-sentinel';sentinel.write_bytes(b'explicitly-inert-sentinel');sentinel.chmod(0o600)
                if bad=='extra':(payload/'private-admission.json').write_bytes(b'must-not-project')
                else:
                    (payload/'ipc_probe.py').unlink()
                    if bad=='symlink':(payload/'ipc_probe.py').symlink_to(sentinel)
                    else:os.link(sentinel,payload/'ipc_probe.py')
                with self.assertRaises(ValueError):M.project_payload(payload,expected)
                self.assertEqual(stat.S_IMODE(payload.stat().st_mode),0o700)
                self.assertEqual(stat.S_IMODE((payload/'client.deb').stat().st_mode),0o600)
                self.assertEqual(stat.S_IMODE(sentinel.stat().st_mode),0o600)
                self.assertEqual(sentinel.read_bytes(),b'explicitly-inert-sentinel')

    def test_actual_guest_payload_assertion_precedes_any_app_launch(self):
        source=SOURCE.read_text();tree=ast.parse(source)
        owner=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Controller')
        setup=next(n for n in owner.body if isinstance(n,ast.FunctionDef) and n.name=='setup')
        witness=next(n.lineno for n in ast.walk(setup) if isinstance(n,ast.Call) and 'GUEST-PAYLOAD-READABILITY.json' in ast.unparse(n))
        firstlaunch=min(n.lineno for n in ast.walk(setup) if isinstance(n,ast.Call) and ast.unparse(n.func)=='self.p.launch')
        self.assertLess(witness,firstlaunch)
        self.assertIn('assert os.geteuid()==1000',source)
        self.assertIn('assert not os.access(root,os.W_OK)',source)
        self.assertIn('"actual-guest-readable-projection"',source)

    def test_source_primitives_exact_readonly_subset_and_no_host_harness(self):
        root = Path(os.environ.get("MIXEL_CONTROLLER_SOURCE_ROOT", str(SOURCE.resolve().parents[1])))
        if not (root/"scripts/test-support-session-linux.py").is_file():
            self.fail("Use MIXEL_CONTROLLER_SOURCE_ROOT for artifact-only preparation; no source skips")
        with tempfile.TemporaryDirectory() as temporary:
            cls = M.extracted_primitives(root, Path(temporary))
            self.assertEqual({k for k in cls.__dict__ if not k.startswith("__")}, set(M.METHODS))
            text = (Path(temporary)/"ipc_probe.py").read_text()
            self.assertNotIn("TOKEN", text); self.assertNotIn("URI", text)
            self.assertEqual({n.name for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)}, {"frame","read_exact","read_frame","query"})

    def test_workflow_and_runtime_isolation_source_contract(self):
        text = SOURCE.read_text()
        workflow = SOURCE.parents[1]/".github/workflows/pc01-linux-controller.yml"
        value = workflow.read_text()
        self.assertIn("runs-on: ubuntu-22.04", value); self.assertIn("actions: read", value)
        self.assertIn("cancel-in-progress: false", value); self.assertIn("if: always()", value)
        self.assertNotIn("actions: write", value)
        self.assertIn("  pull_request:\n    paths:\n      - '.github/workflows/pc01-linux-controller.yml'\n      - 'scripts/pc01-linux-controller.py'\n      - 'scripts/test-pc01-linux-controller.py'",value)
        sourcejob=value.split('  inert-controls:\n',1)[1].split('  pc01-controller:\n',1)[0]
        runtimejob=value.split('  pc01-controller:\n',1)[1]
        self.assertIn("if: ${{ github.event_name == 'pull_request' }}",sourcejob)
        self.assertIn("if: ${{ github.event_name == 'workflow_dispatch' }}",runtimejob)
        self.assertIn('scope=source-only-inert-controller-controls',sourcejob)
        self.assertNotIn('GH_TOKEN:',sourcejob);self.assertNotIn('actions: read',sourcejob)
        self.assertNotIn('admission_json',sourcejob);self.assertNotIn('--run ',sourcejob)
        self.assertNotIn('docker ',sourcejob);self.assertNotIn('gh api',sourcejob)
        self.assertIn('run: python3 scripts/test-pc01-linux-controller.py',sourcejob)
        self.assertEqual(value.count("if: ${{ github.event_name == 'workflow_dispatch' }}"),1)
        self.assertIn('"--cap-drop=ALL"', text); self.assertIn('"--security-opt=no-new-privileges"', text)
        self.assertNotIn('"--privileged"', text); self.assertNotIn('"NET_ADMIN"', text)
        self.assertNotIn('/var/run/docker.sock', text); self.assertNotIn('"--publish"', text)
        self.assertIn('"WindowsAuthOrAcceptInferredFromController": False', text)
        self.assertIn('"independentWindowsEvidenceJoinPending": True', text)
        self.assertIn('signal.signal(signal.SIGTERM, deadline)', text)
        # Shift release uses a finally block, not a single ambiguous xdotool argv.
        tree = ast.parse(text)
        self.assertTrue(any(isinstance(n,ast.Try) and any(isinstance(x,ast.Expr) and 'keyup' in ast.unparse(x) for x in n.finalbody) for n in ast.walk(tree)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
