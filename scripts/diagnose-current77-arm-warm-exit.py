#!/usr/bin/env python3
"""Private hosted replay of the exact current77 failed ARM signed application.

This carrier never builds, signs, publishes, replaces an installed application,
or turns a failed/pending producer into customer qualification.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import types
import zipfile

sys.dont_write_bytecode = True
REPOSITORY = "Jangopeople/mixel-remote-client"
REPOSITORY_ID = 1229987840
SOURCE = "77a0acf1b0af2070c7d5d2d1fb172a343882dfc0"
TREE = "fdd3807757b4c0e6085cf4def3f5eb338ba2b04f"
PRODUCER_BRANCH = "fix/remote-support-reliability"
QA_BRANCH = "codex/current77-arm-warmexit"
RUN, ATTEMPT, JOB = 38774820528, 1, 116562140099
PREFLIGHT, COMPARISON = 38774157443, 38774157532
WORKFLOW = ".github/workflows/diagnose-macos-failed-app.yml"
SELF = "scripts/diagnose-current77-arm-warm-exit.py"
CONTROLS = "scripts/test-diagnose-current77-arm-warm-exit.py"
PROJECTION = "scripts/current77_arm_workflow_projection.py"
OBSERVED_PROFILE = "scripts/current77-arm-warmexit-observed-records.json"
HISTORICAL_CONTROLS = "scripts/test-diagnose-macos-failed-app.py"
GATE_PATH = "verification/current77-arm-private-replay-gate.json"
MAX_GATE = 512 * 1024
SCOPE = "current77-failed-arm-warmexit-private-diagnostic-only"
ARTIFACT = {"id": 11998810499, "name": "macos-launch-failure-diagnostic-aarch64",
            "size_in_bytes": 24589609,
            "digest": "sha256:508043dfe1b75c3cba4bd0c40c0bf3e388911a70dccdcae573d560cd4cdd3f3b",
            "created_at": "2026-10-15T18:31:23Z", "updated_at": "2026-10-15T18:31:23Z",
            "expires_at": "2027-01-13T17:08:24Z"}
NESTED_BYTES = 24781636
NESTED_SHA = "8b2856100bcaca5c4a4e554301ecae0c4a9bf6819f8c107811930bc0aa6a3338"
EXECUTABLE_SHA = "b7d056a928f87174b40b97732805aefd2195bc503fc4cd29ce489c31b6a816a4"
FROZEN = {"scripts/diagnose-macos-failed-app.py": "aad5c0590b8a9f8e16401c005419aebba0603676e735ce7cf45a9f77e5da6015",
          "scripts/test-support-launch-macos.py": "854bd6a7e34041d566c810cdd0fe3df238187198ea456381a725a18286968d65",
          "scripts/test-support-launch-linux.py": "90e343f7cddffb3f830c54ee129e7ae192c0d4b269bc336faab93fb5d95abe18",
          "scripts/verify-macos-bundle-minimum.py": "fbd7652bdfaf26c86de97111668bd9726623880811367b99fb59a8190886dcdf",
          "scripts/verify-macos-native-minimum.py": "7ecd3c71f2afc000f0d5d90dc470be03fbdf4c69e02ee7f1bdaa835464710c92",
          'scripts/test-support-launch-macos-concurrency.py': '9fb58a9c5333df460cc4a676dc1499ee08af4af78edca762d0e20c59f0d03fc0',
          'scripts/test-support-launch-macos-tray.py': '80ebc3255a3898c9feb23c68e06fee28efb74109e5d30460b3ddb9e651ade9b4',
          'scripts/test-support-launch-macos-tray-controls.py': 'b06623ec893cdcf43b3b27670eadbe12f49d4491d012d8b88debe88fb4e6184d',
          'scripts/failed-macos-diagnostic-helper-hashes.json': 'c7352ef1d6628035b4c275bed5f16def67fffb13b3002668bf81f837c4f71a45',
          'scripts/observe-macos-incoming-cleanup.py': '7b0397d0630d1b61d81dd922f27f84eed66213b406a4c22fa63406d80e9dddbf'}
SHA = re.compile(r"[0-9a-f]{64}\Z")
TERMINAL_RESULTS = frozenset({"success", "failure", "cancelled", "timed_out", "neutral", "skipped",
                              "action_required", "startup_failure", "stale"})
UNRELATED_STATES = {("queued", None), ("in_progress", None)} | {("completed", r) for r in TERMINAL_RESULTS}
OBSERVER = {
    "scripts/darwin_exit_observer.py": "f19af1bf38260edb33125fea2e446561f7048e39b6c81e5aa4efde378837eadf",
    "scripts/darwin_exit_adapter.py": "71f6297ab8566e3de0d1fcedc59836412910270f4709028458250e9202bb65fd",
    "scripts/test_darwin_exit_observer.py": "a330be925b268d5a26d9863e4d3605b284fc972b65b1b5cfc84ae544e1f1f05c",
    "scripts/test_darwin_exit_adapter.py": "86a9332af15c11d6fe7b5539094b2c251aa21a5924a2f817a0ca1a5864a6abfc",
}


class Refusal(RuntimeError):
    """Only fixed reviewable categories are printed."""


def require(value, category):
    if not value:
        raise Refusal(category)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path, maximum=1024 * 1024):
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
            and before.st_nlink == 1 and 0 < before.st_size <= maximum, "owned-regular-file-required")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    require(identity(before) == identity(opened) == identity(after) and len(data) == before.st_size,
            "owned-file-changed")
    return data


def unique_json(data):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, "duplicate-json-key")
            out[key] = value
        return out
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Refusal("nonfinite-json-value")))


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def capture(argv, category, maximum=4 * 1024 * 1024, timeout=120):
    result = subprocess.run(argv, capture_output=True, timeout=timeout)
    require(len(result.stdout) + len(result.stderr) <= maximum, "command-output-bound")
    require(result.returncode == 0, category)
    return result.stdout


def module(path, expected):
    data = read(path)
    require(sha(data) == expected, "reviewed-helper-source-drift")
    loaded = types.ModuleType(path.stem.replace("-", "_"))
    loaded.__file__ = str(path)
    prior = sys.modules.get(loaded.__name__)
    sys.modules[loaded.__name__] = loaded
    try:
        exec(compile(data, str(path), "exec"), loaded.__dict__)
    except BaseException:
        if prior is None:
            sys.modules.pop(loaded.__name__, None)
        else:
            sys.modules[loaded.__name__] = prior
        raise
    return loaded


def require_identity(record, expected, category):
    require(isinstance(record, dict) and all(type(record.get(k)) is type(v) and record[k] == v
            for k, v in expected.items()), category)


def validate_run(record, run, workflow, workflow_id, event, states):
    require_identity(record, {"id": run, "head_sha": SOURCE, "head_branch": PRODUCER_BRANCH,
                     "run_attempt": ATTEMPT, "path": workflow, "workflow_id": workflow_id,
                     "event": event}, "original-run-source-event-attempt")
    require((record.get("status"), record.get("conclusion")) in states, "original-run-state-not-admitted")
    for field in ("repository", "head_repository"):
        require_identity(record.get(field), {"id": REPOSITORY_ID, "full_name": REPOSITORY}, "original-run-repository")


def inventory(record, run):
    jobs = record.get("jobs") if isinstance(record, dict) else None
    require(isinstance(jobs, list) and 0 < len(jobs) <= 100 and type(record.get("total_count")) is int
            and record["total_count"] == len(jobs), "complete-original-job-inventory-required")
    require(all(isinstance(j, dict) and type(j.get("id")) is int for j in jobs)
            and len({j["id"] for j in jobs}) == len(jobs), "original-job-identities")
    for j in jobs:
        require_identity(j, {"run_id": run, "run_attempt": ATTEMPT, "head_sha": SOURCE,
                         "head_branch": PRODUCER_BRANCH}, "original-job-run-source-attempt")
    return {j["id"]: j for j in jobs}


def state(job, name, result):
    require_identity(job, {"name": name, "status": "completed", "conclusion": result}, "required-original-job-result")


def validate_arm_job(job):
    require_identity(job, {"id": JOB, "run_id": RUN, "run_attempt": ATTEMPT, "head_sha": SOURCE,
                     "head_branch": PRODUCER_BRANCH, "name": "macos (aarch64, aarch64-apple-darwin)",
                     "status": "completed", "conclusion": "failure", "labels": ["macos-14"],
                     "runner_group_id": 0, "runner_group_name": "GitHub Actions",
                     "started_at": "2026-10-15T17:13:47Z", "completed_at": "2026-10-15T18:31:38Z"},
                     "exact-completed-failed-arm-job-required")
    require(isinstance(job.get("runner_name"), str)
            and re.fullmatch(r"GitHub Actions [0-9]{1,20}", job["runner_name"]), "original-hosted-arm-runner-required")
    steps = job.get("steps")
    require(isinstance(steps, list) and 0 < len(steps) <= 100
            and all(isinstance(s, dict) and type(s.get("number")) is int for s in steps)
            and len({s["number"] for s in steps}) == len(steps), "original-arm-step-inventory")
    for number, name, result, start, end in (
        (25, "Package + sign + notarize (.app then .dmg)", "failure", "2026-10-15T18:30:24Z", "2026-10-15T18:31:19Z"),
        (27, "Retain failed macOS launch bundle and bounded diagnostics", "success", "2026-10-15T18:31:20Z", "2026-10-15T18:31:23Z"),
    ):
        matches = [s for s in steps if s.get("number") == number or s.get("name") == name]
        require(len(matches) == 1, "exact-original-arm-step-required")
        require_identity(matches[0], {"number": number, "name": name, "status": "completed", "conclusion": result,
                         "started_at": start, "completed_at": end}, "original-arm-failure-upload-attribution")


def validate_artifact(record, now=None):
    require_identity(record, ARTIFACT, "exact-original-arm-artifact-required")
    require(record.get("expired") is False, "original-artifact-expired")
    require_identity(record.get("workflow_run"), {"id": RUN, "head_sha": SOURCE,
                     "head_branch": PRODUCER_BRANCH, "repository_id": REPOSITORY_ID,
                     "head_repository_id": REPOSITORY_ID}, "original-artifact-run-source-attempt")
    require(datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
            > (now or datetime.now(timezone.utc)), "original-artifact-expired")


def validate_records(records):
    pending_or_failed = {("in_progress", None), ("completed", "failure")}
    validate_run(records["producer"], RUN, ".github/workflows/build.yml", 271445168, "workflow_dispatch", pending_or_failed)
    validate_run(records["preflight"], PREFLIGHT, ".github/workflows/preflight.yml", 379498304, "pull_request", UNRELATED_STATES)
    validate_run(records["comparison"], COMPARISON, ".github/workflows/windows-branding-idempotence.yml", 383185287,
                 "pull_request", UNRELATED_STATES)
    producer = inventory(records["producerJobs"], RUN)
    require(JOB in producer, "failed-arm-job-missing")
    validate_arm_job(records["armJob"])
    require(producer[JOB] == records["armJob"], "failed-arm-job-records-disagree")
    for job, name in ((116559628730, "config"), (116559715360, "bridge"),
                      (116562140309, "linux"), (116562140491, "macos (x86_64, x86_64-apple-darwin)")):
        require(job in producer, "required-original-job-missing")
        state(producer[job], name, "success")
    require(116562140563 in producer and producer[116562140563].get("name") == "windows"
            and (producer[116562140563].get("status"), producer[116562140563].get("conclusion")) in UNRELATED_STATES,
            "original-windows-state-not-admitted")
    pref = inventory(records["preflightJobs"], PREFLIGHT)
    for job, name in ((116557333724, "https-registration-gateway"), (116557334166, "release-pipeline"),
                      (116557334238, "macos-runtime-harness"), (116557334406, "upstream-source")):
        require(job in pref, "required-preflight-job-missing")
        state(pref[job], name, "success")
    require(116557334151 in pref and pref[116557334151].get("name") == "windows-packaging"
            and (pref[116557334151].get("status"), pref[116557334151].get("conclusion")) in UNRELATED_STATES,
            "original-windows-preflight-state-not-admitted")
    comparison = inventory(records["comparisonJobs"], COMPARISON)
    require(set(comparison) == {116557333543, 116557334369}, "exact-two-comparison-lanes-required")
    for job, name in ((116557333543, "Complete branding byte identity (windows-2025)"),
                      (116557334369, "Complete branding byte identity (windows-2022)")):
        require(comparison[job].get("name") == name
                and (comparison[job].get("status"), comparison[job].get("conclusion")) in UNRELATED_STATES,
                "comparison-lane-identity-state")
    validate_artifact(records["artifact"])
    return {"producer": {k: records["producer"][k] for k in ("id", "status", "conclusion")},
            "preflight": {k: records["preflight"][k] for k in ("id", "status", "conclusion")},
            "comparison": {k: records["comparison"][k] for k in ("id", "status", "conclusion")},
            "windowsProducer": {k: producer[116562140563][k] for k in ("id", "status", "conclusion")},
            "windowsPreflight": {k: pref[116557334151][k] for k in ("id", "status", "conclusion")},
            "comparisonLanes": [{k: comparison[j][k] for k in ("id", "name", "status", "conclusion")}
                                for j in (116557333543, 116557334369)],
            "failedArmJob": JOB, "wholeProducerGreen": False, "normalReleaseQualified": False}


def api(suffix):
    return unique_json(capture(["gh", "api", "--method", "GET", "repos/" + REPOSITORY + suffix], "metadata-read-failed"))


def fresh_records():
    out = {}
    for label, run in (("producer", RUN), ("preflight", PREFLIGHT), ("comparison", COMPARISON)):
        out[label] = api("/actions/runs/" + str(run))
        out[label + "Jobs"] = api("/actions/runs/" + str(run) + "/attempts/1/jobs?per_page=100")
    out["armJob"] = api("/actions/jobs/" + str(JOB))
    out["artifact"] = api("/actions/artifacts/" + str(ARTIFACT["id"]))
    return out


def actual_state_summary(records):
    """Preserve actual bounded statuses even if later admission checks refuse."""
    def row(value):
        if not isinstance(value, dict):
            return {"recordUnavailable": True}
        result = {"id": value.get("id") if type(value.get("id")) is int else None,
                  "status": value.get("status") if value.get("status") in {"queued", "in_progress", "completed"} else "unrecognized-state",
                  "conclusion": value.get("conclusion") if value.get("conclusion") in TERMINAL_RESULTS | {None} else "unrecognized-result"}
        source = value.get("head_sha")
        result["headSha"] = source if isinstance(source, str) and re.fullmatch(r"[0-9a-f]{40}", source) else None
        result["runAttempt"] = value.get("run_attempt") if type(value.get("run_attempt")) is int else None
        return result
    result = {k: row(records.get(k)) for k in ("producer", "preflight", "comparison", "armJob")}
    for label, job in (("producerJobs", 116562140563), ("preflightJobs", 116557334151),
                       ("comparisonJobs", 116557333543), ("comparisonJobs", 116557334369)):
        collection = records.get(label, {})
        jobs = collection.get("jobs", []) if isinstance(collection, dict) else []
        selected = [j for j in jobs if isinstance(j, dict) and j.get("id") == job] if isinstance(jobs, list) else []
        result[str(job)] = row(selected[0] if len(selected) == 1 else None)
    result["normalReleaseQualified"] = False
    return result


def verify_gate(root, path):
    require(path.is_absolute() and path == root / GATE_PATH and path.is_file()
            and not path.is_symlink(), "committed-root-private-gate-required")
    gate = unique_json(read(path, MAX_GATE))
    require_identity(gate, {"schema": 1, "scope": SCOPE, "producerSource": SOURCE, "producerTree": TREE,
                     "producerRun": RUN, "producerAttempt": ATTEMPT, "failedJob": JOB,
                     "privateReplayAllowed": True, "customerAppQualified": False,
                     "releaseQualified": False, "publication": False}, "root-private-gate-identity-scope")
    require(gate.get("artifact") == ARTIFACT and gate.get("nestedAppSha256") == NESTED_SHA,
            "root-private-gate-artifact")
    require(isinstance(gate.get("observedOriginals"), dict), "honest-observed-original-records-required")
    observed = validate_records(gate["observedOriginals"])
    require(gate.get("observedAdmission") == observed, "observed-original-admission-mismatch")
    files = gate.get("reviewedHarnessFiles")
    required = {SELF, CONTROLS, PROJECTION, WORKFLOW, OBSERVED_PROFILE, HISTORICAL_CONTROLS, *OBSERVER}
    require(isinstance(files, dict) and set(files) == required
            and all(isinstance(h, str) and SHA.fullmatch(h) for h in files.values()), "root-private-gate-harness-pins")
    require(all(files[k] == v for k, v in OBSERVER.items()), "reviewed-exit-observer-pins")
    for name, expected in {GATE_PATH: sha(read(path, MAX_GATE)), **files}.items():
        raw = read(root / name)
        require(sha(raw) == expected, "reviewed-harness-source-drift")
        committed = capture(["git", "-C", str(root), "show", "HEAD:" + name], "reviewed-harness-must-be-tracked")
        require(committed == raw, "reviewed-harness-committed-blob-differs")
    return gate


def hosted_context(diagnostic, producer):
    require(sys.platform == "darwin" and platform.machine() == "arm64"
            and os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_OS") == "macOS"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
            and os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "fresh-hosted-arm-manual-only")
    own = os.environ.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", own) and own != SOURCE
            and os.environ.get("GITHUB_WORKFLOW_SHA") == own
            and os.environ.get("GITHUB_REF") == "refs/heads/" + QA_BRANCH
            and os.environ.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + WORKFLOW + "@refs/heads/" + QA_BRANCH
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
            and re.fullmatch(r"[1-9][0-9]{0,19}", os.environ.get("GITHUB_RUN_ID", ""))
            and os.environ["GITHUB_RUN_ID"] != str(RUN), "diagnostic-event-ref-attempt-binding")
    for checkout, expected in ((diagnostic, own), (producer, SOURCE)):
        require(checkout.is_dir() and not checkout.is_symlink() and checkout.resolve() == checkout, "canonical-clean-checkouts-required")
        require(capture(["git", "-C", str(checkout), "rev-parse", "HEAD"], "checkout-head").decode().strip() == expected,
                "exact-checkout-source-required")
        require(capture(["git", "-C", str(checkout), "status", "--porcelain"], "checkout-status") == b"", "checkout-dirty")
    require(capture(["git", "-C", str(producer), "rev-parse", "HEAD^{tree}"], "producer-tree").decode().strip() == TREE,
            "exact-producer-tree-required")
    return own


def fresh_fixture():
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    require(os.environ.get("HOME") == str(home) and home.resolve(strict=True) == home
            and home.stat().st_uid == os.getuid(), "runner-home-identity")
    paths = (Path("/tmp/Mixel-Remote"), Path("/private/tmp/mixel-remote-attended-v2-" + str(os.getuid())),
             Path("/private/tmp/mixel-remote-gui-launch-v1-" + str(os.getuid())),
             home / "Library/Logs/Mixel-Remote", home / "Library/Preferences/com.carriez.Mixel-Remote",
             home / "Library/Application Support/com.carriez.Mixel-Remote", home / "Library/Caches/com.carriez.Mixel-Remote",
             home / "Library/Preferences/ch.mixel.remote.plist", home / "Library/Saved Application State/ch.mixel.remote.savedState")
    require(not any(os.path.lexists(p) for p in paths), "pre-existing-runtime-state-refused")
    raw = capture(["ps", "-axo", "pid=,stat=,comm="], "process-inventory")
    for line in raw.decode("utf-8", errors="strict").splitlines():
        parts = line.strip().split(None, 2)
        require(len(parts) == 3 and re.fullmatch(r"[1-9][0-9]{0,9}", parts[0]), "process-inventory-shape")
        require("Z" in parts[1] or not (parts[2].endswith("/Mixel-Remote")
                or "/Mixel-Remote.app/" in parts[2] or parts[2].endswith("/RustDesk")), "pre-existing-application-refused")


def validate_embedded(record, nested):
    require_identity(record, {"schema": 1, "scope": "macos-launch-smoke-diagnostic-not-customer-release",
                     "runner_context": {"source_sha": SOURCE, "run_id": RUN}, "outcome": "failure",
                     "privacy_check_passed": True, "customer_app_qualified": False,
                     "screen_control_qualified": False, "publication": False,
                     "owned_pid": 18723, "owned_launch_time": 1792089074.53715,
                     "executable_sha256": EXECUTABLE_SHA}, "exact-original-warm-exit-runtime-required")
    require_identity(record.get("diagnostic_app_archive"), {"name": "signed-stapled-diagnostic-app.zip",
                     "bytes": NESTED_BYTES, "sha256": NESTED_SHA}, "exact-original-nested-app-required")
    require(nested.stat().st_size == NESTED_BYTES and sha(read(nested, 512 * 1024 * 1024)) == NESTED_SHA,
            "nested-app-original-digest-size")
    failures = [e for e in record.get("events", []) if e.get("event") == "test-failure"]
    require(len(failures) == 1 and failures[0].get("phase") == "fresh-warm-url-launch"
            and failures[0].get("reason") == "Warm support URL customer window: customer app exited"
            and failures[0].get("pid") == 18723, "original-warm-exit-failure-attribution")


def run_unchanged_smoke(concurrency, observer, app, private):
    # Replay the producer's actual --original-desktop wrapper exactly once.
    # Its isolated_ci check and locked NSWorkspace callback compiler remain.
    # All original runtime assertions, wait deadlines and cleanup are unchanged.
    out = private / "original-smoke.stdout"
    err = private / "original-smoke.stderr"
    arguments = ["--app", str(app), "--diagnostics", str(private / "runtime")]
    with out.open("x", encoding="utf-8") as stdout, err.open("x", encoding="utf-8") as stderr:
        os.chmod(out, 0o600); os.chmod(err, 0o600)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with observer.install(concurrency.desktop, private / "exit-observer-private.json"):
                code = concurrency.run_original("--original-desktop", arguments)
    require(type(code) is int and code in (0, 1), "original-smoke-return-contract")
    return code, {"stdout": {"bytes": out.stat().st_size, "sha256": sha(out.read_bytes())},
                  "stderr": {"bytes": err.stat().st_size, "sha256": sha(err.read_bytes())}}


def safe_observer_facts(record, runtime, app, original_code):
    """Strict content-free projection; raw errors and filesystem paths stay private."""
    require(isinstance(record, dict) and set(record) == {"diagnostic_only", "qualifies_application",
            "observers", "errors", "original_methods_restored"}, "exit-observer-receipt-schema")
    require_identity(record, {"diagnostic_only": True, "qualifies_application": False,
                     "original_methods_restored": True}, "exit-observer-diagnostic-scope")
    require_identity(runtime, {"schema": 1, "scope": "macos-launch-smoke-diagnostic-not-customer-release",
                     "privacy_check_passed": True, "customer_app_qualified": False,
                     "screen_control_qualified": False, "publication": False}, "replay-runtime-diagnostic-scope")
    require(runtime.get("outcome") == ("success" if original_code == 0 else "failure"), "replay-original-result-mismatch")
    events = runtime.get("events")
    require(isinstance(events, list) and len(events) <= 4096
            and all(isinstance(e, dict) for e in events), "replay-runtime-events-bound")
    ordinary = [e for e in events if e.get("event") == "owned-ordinary-application-launched"]
    require(len(ordinary) <= 1 and (ordinary or original_code == 1), "replay-owned-ordinary-pid-required")
    pid = ordinary[0].get("pid") if ordinary else None
    require(pid is None or type(pid) is int and pid > 1, "replay-owned-ordinary-pid-required")
    failures = [e for e in events if e.get("event") == "test-failure"]
    require(len(failures) <= 1, "replay-runtime-primary-failure-bound")
    original_failure = None
    if failures:
        failure = failures[0]
        reason = failure.get("reason")
        require(isinstance(reason, str) and 0 < len(reason.encode()) <= 4096, "replay-runtime-reason-bound")
        phases = {"initialization", "ordinary-gui-launch", "ordinary-unarmed-health", "fresh-warm-hide-setup",
                  "fresh-warm-url-launch", "ordinary-to-warm-owned-cleanup", "cold-url-launch",
                  "cold-visible-window", "cold-stable-window", "cold-incoming-health", "owned-cleanup"}
        phase = failure.get("phase")
        original_failure = {
            "phase": phase if isinstance(phase, str) and phase in phases else "unlisted-phase",
            "reasonSha256": sha(reason.encode()),
            "reviewedReasonCategory": ("warm-owned-pid-exited"
                if reason == "Warm support URL customer window: customer app exited" else "unrecognized-reason-redacted"),
        }
    require(isinstance(record["observers"], list) and len(record["observers"]) <= 1
            and isinstance(record["errors"], list) and len(record["errors"]) <= 64, "exit-observer-receipt-bound")
    safe = {"schema": 1, "scope": SCOPE, "diagnosticOnly": True, "qualifiesApplication": False,
            "originalMethodsRestored": True, "originalSmokeExitCode": original_code,
            "ordinaryPid": pid, "originalRuntimeOutcome": runtime["outcome"],
            "originalRuntimeFailure": original_failure, "observers": [], "errors": []}
    for row in record["errors"]:
        require(isinstance(row, dict) and set(row) == {"stage", "error_type"}
                and row["stage"] in {"duplicate-ordinary-mark", "register-owned-ordinary-PID", "mark",
                        "before-request", "after-request", "context-exit", "observer-receipt", "observer-close"}
                and isinstance(row["error_type"], str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", row["error_type"]),
                "exit-observer-error-schema")
        safe["errors"].append(dict(row))
    for row in record["observers"]:
        require(isinstance(row, dict) and set(row) == {"diagnostic_only", "qualifies_application", "state",
                "identity", "requested_notes", "raw_events", "decoded_exit", "error", "first_exit_observation"},
                "exit-observer-session-schema")
        require_identity(row, {"diagnostic_only": True, "qualifies_application": False,
                         "requested_notes": 0x86000000}, "exit-observer-session-scope-notes")
        require(row["state"] in {"pending", "exit_observed", "invalid"}, "exit-observer-state")
        identity = row["identity"]
        require(isinstance(identity, dict) and set(identity) == {"pid", "uid", "birth_seconds", "birth_microseconds",
                "executable", "bundle_path", "bundle_identifier", "bundle_version"}, "exit-observer-native-identity-schema")
        require_identity(identity, {"pid": pid, "uid": os.geteuid(), "executable": str(app / "Contents/MacOS/Mixel-Remote"),
                         "bundle_path": str(app), "bundle_identifier": "ch.mixel.remote", "bundle_version": "64"},
                         "exit-observer-owned-native-identity")
        require(type(identity["birth_seconds"]) is int and 0 < identity["birth_seconds"] < 2**63
                and type(identity["birth_microseconds"]) is int and 0 <= identity["birth_microseconds"] < 1000000,
                "exit-observer-native-birth")
        raw = row["raw_events"]
        require(isinstance(raw, list) and len(raw) <= 8, "exit-observer-kernel-event-bound")
        for event in raw:
            require(isinstance(event, dict) and set(event) == {"ident", "filter", "flags", "fflags", "data", "udata"}
                    and all(type(v) is int and -(2**63) <= v < 2**64 for v in event.values()), "exit-observer-raw-kernel-event")
        decoded = row["decoded_exit"]
        if decoded is not None:
            allowed = {"exit_status_available", "exit_detail_available", "kind", "exit_code", "signal", "exit_details"}
            require(isinstance(decoded, dict) and set(decoded) <= allowed
                    and all(type(decoded.get(k)) is bool for k in ("exit_status_available", "exit_detail_available")),
                    "exit-observer-decoded-status-schema")
            if decoded["exit_status_available"]:
                require(decoded.get("kind") in {"exited", "signaled"}, "exit-observer-decoded-kind")
                field = "exit_code" if decoded["kind"] == "exited" else "signal"
                require(type(decoded.get(field)) is int and 0 <= decoded[field] <= 255
                        and (field != "signal" or decoded[field] > 0)
                        and ("signal" if field == "exit_code" else "exit_code") not in decoded,
                        "exit-observer-decoded-exit-value")
            else:
                require(not set(decoded) & {"kind", "signal", "exit_code"}, "exit-observer-no-invented-status")
            if decoded["exit_detail_available"]:
                require(isinstance(decoded.get("exit_details"), dict)
                        and set(decoded["exit_details"]) == {"decrypt_failure", "memory", "code_signing_error"}
                        and all(type(v) is bool for v in decoded["exit_details"].values()), "exit-observer-decoded-details")
            else:
                require("exit_details" not in decoded, "exit-observer-no-invented-details")
        require((row["state"] == "pending") == (decoded is None and row["error"] is None), "exit-observer-state-consistency")
        require((row["state"] == "exit_observed") == (decoded is not None and row["error"] is None), "exit-observer-result-consistency")
        error = row["error"]
        require(error is None or isinstance(error, str) and 0 < len(error.encode()) <= 4096, "exit-observer-error-bound")
        first = row["first_exit_observation"]
        if first is not None:
            require(isinstance(first, dict) and set(first) == {"sampling_stage", "current_mark", "phase", "utc", "elapsed_seconds", "meaning"}
                    and first["sampling_stage"] in {"mark", "before-request", "after-request", "context-exit"}
                    and first["phase"] in {"initialization", "ordinary-gui-launch", "ordinary-unarmed-health",
                        "fresh-warm-hide-setup", "fresh-warm-url-launch", "ordinary-to-warm-owned-cleanup",
                        "cold-url-launch", "cold-visible-window", "cold-stable-window", "cold-incoming-health",
                        "owned-cleanup", "unlisted-phase"}
                    and first["meaning"] == "first observed exit; phase is not proof of the cause",
                    "exit-observer-first-sampling-schema")
            require(first["current_mark"] is None or first["current_mark"] in {
                "test-start", "owned-ordinary-application-launched", "owned-application-launched", "owned-pid-exited",
                "wait-observation-error", "wait-deadline", "incoming-health-response", "warm-cleanup-result",
                "test-failure", "cleanup-result", "launchservices-request", "launchservices-response", "launch-error",
                "ordinary-request", "ordinary-response", "ordinary-error", "hide-request", "hide-response", "hide-error",
                "probe-request", "probe-response", "probe-error", "unlisted-mark"}, "exit-observer-first-sampling-mark")
            elapsed = first["elapsed_seconds"]
            require(elapsed is None or type(elapsed) in (int, float) and 0 <= elapsed < 1800, "exit-observer-first-sampling-time")
            require(isinstance(first["utc"], str) and len(first["utc"]) <= 40,
                    "exit-observer-first-sampling-utc")
            stamp = datetime.fromisoformat(first["utc"])
            require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0,
                    "exit-observer-first-sampling-utc")
        require((first is not None) == (decoded is not None), "exit-observer-first-sampling-preservation")
        safe["observers"].append({"state": row["state"], "pid": pid, "uid": identity["uid"],
            "birthSeconds": identity["birth_seconds"], "birthMicroseconds": identity["birth_microseconds"],
            "executableSha256": EXECUTABLE_SHA, "bundleIdentifier": identity["bundle_identifier"],
            "bundleVersion": identity["bundle_version"], "requestedNotes": row["requested_notes"],
            "rawKernelEvents": raw, "decodedExit": decoded, "errorSha256": sha(error.encode()) if error else None,
            "firstExitObservation": first})
    return safe


def require_complete_observation(facts):
    require(facts["originalMethodsRestored"] and not facts["errors"] and len(facts["observers"]) == 1,
            "exit-observer-evidence-incomplete")
    session = facts["observers"][0]
    require(session["state"] != "invalid", "exit-observer-evidence-invalid")
    if facts["originalSmokeExitCode"] == 0:
        require(session["state"] == "exit_observed" and session["decodedExit"] is not None
                and session["decodedExit"]["exit_status_available"], "successful-replay-requires-actual-exit-status")


def retain_observation(private, safe, app, receipt, code, output):
    receipt["originalSmokeExitCode"] = code
    receipt["originalSmokeOutput"] = output
    facts = safe_observer_facts(unique_json(read(private / "exit-observer-private.json", 65536)),
            unique_json(read(private / "runtime/runtime.json", 12 * 1024 * 1024)), app, code)
    write(safe / "warm-exit-observer-safe-facts.json", facts)
    # Safe errors and the original result are retained before this fatal gate.
    # A green smoke cannot hide missing/invalid diagnostic observation.
    require_complete_observation(facts)
    return facts


def new_directory(path):
    require(path.is_absolute() and path.parent.resolve(strict=True) == path.parent
            and not os.path.lexists(path), "new-canonical-output-required")
    path.mkdir(mode=0o700)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic-checkout", type=Path, required=True)
    parser.add_argument("--producer-checkout", type=Path, required=True)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--safe-output", type=Path, required=True)
    args = parser.parse_args(argv)
    diagnostic = args.diagnostic_checkout.resolve(strict=True)
    producer = args.producer_checkout.resolve(strict=True)
    # Missing/uncommitted gate refuses before host commands, provider calls,
    # output creation, archive extraction or any application/native launch.
    gate = verify_gate(diagnostic, args.gate)
    own = hosted_context(diagnostic, producer)
    for name, expected in FROZEN.items():
        require(sha(read(producer / name)) == expected, "exact-producer-helper-source-drift")
    support = module(producer / "scripts/diagnose-macos-failed-app.py", FROZEN["scripts/diagnose-macos-failed-app.py"])
    for name, expected in OBSERVER.items():
        require(sha(read(diagnostic / name)) == expected, "reviewed-exit-observer-source-drift")
    module(diagnostic / "scripts/darwin_exit_observer.py", OBSERVER["scripts/darwin_exit_observer.py"])
    observer = module(diagnostic / "scripts/darwin_exit_adapter.py", OBSERVER["scripts/darwin_exit_adapter.py"])
    require(callable(getattr(observer, "install", None)), "reviewed-observer-interface-required")
    before = fresh_records()
    before_admission = validate_records(before)
    fresh_fixture()
    private = new_directory(args.destination)
    safe = new_directory(args.safe_output)
    receipt = {"schema": 1, "scope": SCOPE, "producerSource": SOURCE, "producerTree": TREE,
               "producerRun": RUN, "producerAttempt": ATTEMPT, "failedJob": JOB,
               "artifact": ARTIFACT, "harnessSource": own, "harnessRun": int(os.environ["GITHUB_RUN_ID"]),
               "harnessRunAttempt": 1, "rootGateSha256": sha(read(args.gate, MAX_GATE)),
               "reviewedHarnessFiles": gate["reviewedHarnessFiles"], "frozenProducerHelpers": FROZEN,
               "observedBefore": before_admission, "nativeExecuted": False,
               "customerAppQualified": False, "screenControlQualified": False,
               "releaseQualified": False, "publication": False, "rawLogsUploaded": False}
    code = 1
    try:
        archive = private / "original-failed-arm-artifact.zip"
        with archive.open("xb") as output:
            os.chmod(archive, 0o600)
            downloaded = subprocess.run(["gh", "api", "--method", "GET", "repos/" + REPOSITORY
                         + "/actions/artifacts/" + str(ARTIFACT["id"]) + "/zip"], stdout=output,
                         stderr=subprocess.DEVNULL, timeout=180)
        require(downloaded.returncode == 0 and archive.stat().st_size == ARTIFACT["size_in_bytes"]
                and support.file_digest(archive) == ARTIFACT["digest"][7:], "downloaded-original-artifact-digest-size")
        nested = private / "signed-stapled-diagnostic-app.zip"
        with zipfile.ZipFile(archive) as zipped:
            embedded = support.validate_outer(zipped)
            with nested.open("xb") as output:
                os.chmod(nested, 0o600)
                output.write(zipped.read(support.OUTER_APP))
        validate_embedded(embedded, nested)
        with zipfile.ZipFile(nested) as zipped:
            support.validate_nested(zipped)
        app_root = new_directory(private / "owned-app-copy")
        capture(["ditto", "-x", "-k", str(nested), str(app_root)], "validated-ditto-extraction")
        app = app_root / support.APP
        support.verify_tree(app)
        receipt["bundleIdentity"] = support.validate_app_identity(app, EXECUTABLE_SHA)
        support.verify_signatures(app)
        minimum = private / "bundle-minimum.json"
        capture([sys.executable, "-B", str(producer / "scripts/verify-macos-bundle-minimum.py"), "--bundle", str(app),
                 "--arch", "arm64", "--minimum", "12.3", "--output", str(minimum)], "complete-arm64-minimum")
        report = unique_json(read(minimum, 4 * 1024 * 1024))
        require(report.get("status") == "passed" and report.get("architecture") == "arm64"
                and report.get("advertised_minimum") == "12.3" and type(report.get("binary_count")) is int
                and report["binary_count"] >= 3, "complete-arm64-minimum-result")
        receipt["criticalHashes"] = {n: support.file_digest((app / n).resolve(strict=True)) for n in support.CRITICAL}
        receipt["signingTeam"] = support.TEAM
        validate_records(fresh_records())
        fresh_fixture()
        concurrency = module(producer / "scripts/test-support-launch-macos-concurrency.py", FROZEN["scripts/test-support-launch-macos-concurrency.py"])
        receipt["nativeExecuted"] = True
        code, output = run_unchanged_smoke(concurrency, observer, app, private)
        retain_observation(private, safe, app, receipt, code, output)
        after = fresh_records()
        receipt["observedAfter"] = actual_state_summary(after)
        receipt["afterAdmissionValidationPassed"] = False
        validate_records(after)
        receipt["afterAdmissionValidationPassed"] = True
        require(receipt["criticalHashes"] == {n: support.file_digest((app / n).resolve(strict=True)) for n in support.CRITICAL},
                "app-binaries-changed-during-replay")
        receipt["replayCompleted"] = True
        return code
    except Refusal as error:
        receipt["refusalCategory"] = str(error)
        raise
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile):
        receipt["refusalCategory"] = "private-diagnostic-operation-failed"
        raise Refusal("private-diagnostic-operation-failed") from None
    finally:
        if receipt["nativeExecuted"] and "observedAfter" not in receipt:
            # A missing/invalid observer must not discard post-native metadata.
            # Keep any primary failure and the original smoke result unchanged.
            try:
                after = fresh_records()
                receipt["observedAfter"] = actual_state_summary(after)
                receipt["afterAdmissionValidationPassed"] = False
                validate_records(after)
                receipt["afterAdmissionValidationPassed"] = True
            except (Refusal, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                receipt["postReplayMetadataFailure"] = "metadata-unavailable-or-not-admitted"
        write(safe / "validation-receipt.json", receipt)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Refusal, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile):
        print("FAIL: private current77 ARM diagnostic refused; qualification remains false", file=sys.stderr)
        raise SystemExit(1)
