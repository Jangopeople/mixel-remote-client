#!/usr/bin/env python3
"""Fixed failed-producer Intel/Rosetta diagnostic; no customer qualification."""
import argparse
import errno
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import pwd
import re
import stat
import subprocess
import sys
import threading
import types
import zipfile

sys.dont_write_bytecode = True
PROFILE_HASH = '163af79cc3480f41de56ed59a6b2531c0738dcc014c4c49b2edc454d1c8d84c6'
EXPECTED_PROFILE = {'artifactBytes': 30894055, 'artifactId': 11862454209, 'artifactSha256': '40d28fac26ad928ccfc6723cb3dbdec7134507258901a769f3d9daac221e0d1b', 'artifactTimes': {'created_at': '2026-10-14T01:22:52Z', 'expires_at': '2027-01-12T00:36:44Z', 'updated_at': '2026-10-14T01:22:52Z'}, 'failedJob': 115617863123, 'frozenHelpers': {'scripts/test-support-launch-linux.py': '90e343f7cddffb3f830c54ee129e7ae192c0d4b269bc336faab93fb5d95abe18', 'scripts/test-support-launch-macos-tray-controls.py': 'b06623ec893cdcf43b3b27670eadbe12f49d4491d012d8b88debe88fb4e6184d', 'scripts/test-support-launch-macos-tray.py': '80ebc3255a3898c9feb23c68e06fee28efb74109e5d30460b3ddb9e651ade9b4', 'scripts/test-support-launch-macos.py': '854bd6a7e34041d566c810cdd0fe3df238187198ea456381a725a18286968d65', 'scripts/verify-macos-bundle-minimum.py': 'fbd7652bdfaf26c86de97111668bd9726623880811367b99fb59a8190886dcdf', 'scripts/verify-macos-native-minimum.py': '7ecd3c71f2afc000f0d5d90dc470be03fbdf4c69e02ee7f1bdaa835464710c92'}, 'producerAttempt': 1, 'producerRun': 38491157973, 'producerSource': 'ca184ca219ceb1b087258e8f25d218eeba901abc', 'producerTree': '825974868c0ae0c632f8f739cf6dc729140c5400', 'reviewedSources': {'.github/workflows/diagnose-macos-failed-app.yml': '398579cbf8d089b9e193a78d7d867f94f9786dc219d0c1616ba263868bd100a0', 'scripts/diagnose-macos-failed-app.py': 'aad5c0590b8a9f8e16401c005419aebba0603676e735ce7cf45a9f77e5da6015', 'scripts/file_dialog_workflow_projection.py': '07d61633acee3a09fd505843664c3b560812276e41ba0fe02206278ae2b80bc8', 'scripts/observe-macos-incoming-cleanup.py': '7b0397d0630d1b61d81dd922f27f84eed66213b406a4c22fa63406d80e9dddbf', 'scripts/test-observe-macos-incoming-cleanup.py': '3299ad55dcfd570f34b76cf35c9d463a1a652d4a8eb0e7df70d9dea76089e4ae', 'scripts/test-support-launch-macos-concurrency-controls.py': '1f3fb2ec96e237a243fe36cf5d63263cbff6255014d3ef0aeed4745da108f8b0', 'scripts/test-support-launch-macos-concurrency.py': '9fb58a9c5333df460cc4a676dc1499ee08af4af78edca762d0e20c59f0d03fc0', 'scripts/standard_foreground95_workflow_projection.py': '5c82b35d8266dbccef120b6a535faefc581cad42edbbdbde1d0c6782c67f541e'}, 'schema': 1, 'scope': 'fixed-failed-intel-rosetta-concurrency-diagnostic-only'}
REPOSITORY = "Jangopeople/mixel-remote-client"
REPOSITORY_ID = 1229987840
SOURCE = "ca184ca219ceb1b087258e8f25d218eeba901abc"
SOURCE_TREE = "825974868c0ae0c632f8f739cf6dc729140c5400"
BRANCH = "fix/remote-support-reliability"
RUN, ATTEMPT, JOB = 38491157973, 1, 115617863123
WORKFLOW_ID = 271445168
WORKFLOW = ".github/workflows/build.yml"
DIAGNOSTIC_WORKFLOW = ".github/workflows/diagnose-macos-concurrency-failed-intel.yml"
JOB_NAME = "macos (x86_64, x86_64-apple-darwin)"
PACKAGE_STEP = "Package + sign + notarize (.app then .dmg)"
UPLOAD_STEP = "Retain failed macOS launch bundle and bounded diagnostics"
ARTIFACT_ID = 11862454209
ARTIFACT_NAME = "macos-launch-failure-diagnostic-x86_64"
ARTIFACT_BYTES = 30894055
ARTIFACT_SHA256 = "40d28fac26ad928ccfc6723cb3dbdec7134507258901a769f3d9daac221e0d1b"
LOG_BYTES = 713079
LOG_SHA256 = "62f470475ad04eb2566e5cf798f849786a634fa0bb68361fc2b2b872393cfbe7"
APP = "Mixel-Remote.app"
OUTER_APP = "x86_64/signed-stapled-diagnostic-app.zip"
OUTER_RUNTIME = "x86_64/runtime/runtime.json"
LEGACY_HASH = "aad5c0590b8a9f8e16401c005419aebba0603676e735ce7cf45a9f77e5da6015"
MANIFEST_HASH = "c7352ef1d6628035b4c275bed5f16def67fffb13b3002668bf81f837c4f71a45"
CALLER_HASH = '9fb58a9c5333df460cc4a676dc1499ee08af4af78edca762d0e20c59f0d03fc0'
CONTROLS_HASH = '1f3fb2ec96e237a243fe36cf5d63263cbff6255014d3ef0aeed4745da108f8b0'
CRITICAL_HASHES = {
    "Contents/MacOS/Mixel-Remote": "25042c78c8abf8d4d4e965769f5f3d03ebdef359334936ef2f3dcf477de627df",
    "Contents/MacOS/service": "9834f1e22bb92cfb663200650027d7f78318b1392c1bd034ddd66106a49b31f4",
    "Contents/Frameworks/liblibrustdesk.dylib": "c524ed736383b819abb7d023810e5c76bbe03ab7d8ae499c6043b94d716964af",
    "Contents/Frameworks/FlutterMacOS.framework/FlutterMacOS": "dfa1964c63413eb58ebbce1f7a42caab6b67c6918c7aa48e2866caba45ab15e4",
}
SCENARIOS = ("ordinary-and-support", "two-support", "batch-two-support")
PHASES = {"tray-launch", "unarmed-tray-readiness", "same-turn-activation",
          "tray-dispatch", "initial-gui-owner", "initial-gui-birth",
          "visible-v2-owned-gui", "bounded-stability"}
ERROR_KINDS = {"filesystem": "OSError", "validation": "ValueError",
               "subprocess": "SubprocessError", "runtime": "RuntimeError"}
CLEANUP_CODES = {
    "ipc-path-unsafe": "endpoint-inactivity-check",
    "ipc-endpoint-active": "endpoint-inactivity-check",
    "pid-marker-unsafe": "pid-marker-read",
    "pid-marker-oversized": "pid-marker-read",
    "pid-marker-open-race": "pid-marker-read",
    "pid-marker-read-race": "pid-marker-read",
    "pid-marker-identity-changed": "pid-marker-identity-check",
    "socket-identity-changed": "socket-identity-check",
    "unclassified-cleanup-error": "endpoint-cleanup",
}
SUPPORT = None
OBSERVER = None
MAX_LOG_STDERR = 16 * 1024
TERMINAL_ESCAPE_REFUSAL = (b"the response contains terminal escape sequences; "
                           b"pass --allow-escape-sequences to output it anyway")


class Refusal(RuntimeError):
    """Only fixed categories may enter output."""


def require(value, category):
    if not value:
        raise Refusal(category)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def frozen_support(root):
    global SUPPORT
    path = root / "scripts/diagnose-macos-failed-app.py"
    require(path.resolve(strict=True) == path, "frozen-archive-helper-canonical-source")
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
            and before.st_nlink == 1 and 0 < before.st_size <= 1024 * 1024,
            "frozen-archive-helper-owned-source")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    require(identity(before) == identity(opened) == identity(after)
            and len(raw) == before.st_size and sha(raw) == LEGACY_HASH,
            "frozen-archive-helper-source")
    loaded = types.ModuleType("frozen_failed_mac_archive")
    loaded.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), loaded.__dict__)
    SUPPORT = loaded
    return loaded


def source_contract(root):
    global OBSERVER
    support = frozen_support(root)
    raw = support.bounded_read(root / "scripts/failed-intel-concurrency-replay-profile.json", 65536)
    require(sha(raw) == PROFILE_HASH, "fixed-intel-profile-source")
    profile = support.unique_json(raw)
    require(profile == EXPECTED_PROFILE, "fixed-intel-profile")
    manifest = support.bounded_read(root / "scripts/failed-macos-diagnostic-helper-hashes.json", 65536)
    require(sha(manifest) == MANIFEST_HASH, "frozen-helper-manifest-source")
    require(support.unique_json(manifest) == profile["frozenHelpers"], "frozen-helper-manifest-shape")
    for name, expected in {**profile["frozenHelpers"], **profile["reviewedSources"]}.items():
        require(sha(support.bounded_read(root / name, 1024 * 1024)) == expected,
                "reviewed-replay-source-drift")
    name = "scripts/observe-macos-incoming-cleanup.py"
    raw_observer = support.bounded_read(root / name, 1024 * 1024)
    require(sha(raw_observer) == profile["reviewedSources"][name], "reviewed-incoming-observer-source")
    OBSERVER = types.ModuleType("reviewed_macos_incoming_cleanup_observer")
    OBSERVER.__file__ = str(root / name)
    exec(compile(raw_observer, str(root / name), "exec"), OBSERVER.__dict__)
    return profile


def capture(command, category, maximum=4 * 1024 * 1024, timeout=120):
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    require(len(result.stdout) + len(result.stderr) <= maximum, "command-output-bound")
    require(result.returncode == 0, category)
    return result.stdout, result.stderr


def api(suffix):
    stdout, _ = capture(["gh", "api", "--method", "GET", "repos/" + REPOSITORY + suffix],
                        "metadata-read-failed")
    return SUPPORT.unique_json(stdout)


def validate_run(record):
    require(isinstance(record, dict) and type(record.get("id")) is int and record["id"] == RUN,
            "producer-run-id")
    require(record.get("head_sha") == SOURCE and record.get("head_branch") == BRANCH
            and record.get("event") == "workflow_dispatch" and record.get("path") == WORKFLOW
            and type(record.get("workflow_id")) is int and record["workflow_id"] == WORKFLOW_ID
            and type(record.get("run_attempt")) is int and record["run_attempt"] == ATTEMPT,
            "producer-source-event-attempt")
    for field in ("repository", "head_repository"):
        identity = record.get(field, {})
        require(isinstance(identity, dict) and identity.get("full_name") == REPOSITORY
                and type(identity.get("id")) is int and identity["id"] == REPOSITORY_ID,
                "producer-repository")
    require((record.get("status"), record.get("conclusion")) == ("completed", "failure"),
            "whole-original-producer-not-completed-failure")


def validate_jobs(record):
    jobs = record.get("jobs") if isinstance(record, dict) else None
    require(isinstance(jobs, list) and type(record.get("total_count")) is int
            and 0 < len(jobs) <= 100 and record["total_count"] == len(jobs),
            "complete-original-job-inventory")
    require(all(isinstance(job, dict) and type(job.get("id")) is int and job.get("status") == "completed"
                for job in jobs) and len({job["id"] for job in jobs}) == len(jobs),
            "original-job-inventory-identity")
    selected = [job for job in jobs if job["id"] == JOB]
    require(len(selected) == 1, "exact-original-failed-intel-job")
    job = selected[0]
    required = {"name": JOB_NAME, "status": "completed", "conclusion": "failure", "run_id": RUN,
                "run_attempt": ATTEMPT, "head_sha": SOURCE, "head_branch": BRANCH,
                "started_at": "2026-10-14T00:41:48Z", "completed_at": "2026-10-14T01:23:08Z",
                "labels": ["macos-14"], "runner_group_id": 0, "runner_group_name": "GitHub Actions"}
    require(all(job.get(key) == value and type(job.get(key)) is type(value)
                for key, value in required.items()), "failed-intel-job-provenance")
    require(isinstance(job.get("runner_name"), str)
            and re.fullmatch(r"GitHub Actions [0-9]{1,20}", job["runner_name"]),
            "original-job-hosted-runner")
    steps = job.get("steps")
    require(isinstance(steps, list) and 0 < len(steps) <= 100
            and all(isinstance(step, dict) and type(step.get("number")) is int for step in steps)
            and len({step["number"] for step in steps}) == len(steps), "original-job-steps")
    exact = (
        (PACKAGE_STEP, 24, "failure", "2026-10-14T01:21:15Z", "2026-10-14T01:22:47Z"),
        ("Retain macOS tray runtime proof", 25, "success", "2026-10-14T01:22:47Z", "2026-10-14T01:22:49Z"),
        (UPLOAD_STEP, 26, "success", "2026-10-14T01:22:49Z", "2026-10-14T01:22:52Z"),
    )
    for name, number, conclusion, start, end in exact:
        matches = [step for step in steps if step.get("name") == name]
        require(len(matches) == 1 and matches[0].get("number") == number
                and matches[0].get("status") == "completed" and matches[0].get("conclusion") == conclusion
                and matches[0].get("started_at") == start and matches[0].get("completed_at") == end,
                "original-failed-step-attribution")
    return job


def validate_artifact(item, now=None):
    require(isinstance(item, dict) and type(item.get("id")) is int and item["id"] == ARTIFACT_ID
            and item.get("name") == ARTIFACT_NAME and type(item.get("size_in_bytes")) is int
            and item["size_in_bytes"] == ARTIFACT_BYTES
            and item.get("digest") == "sha256:" + ARTIFACT_SHA256, "exact-failed-artifact-digest-size")
    identity = item.get("workflow_run", {})
    expected = {"id": RUN, "head_sha": SOURCE, "head_branch": BRANCH,
                "repository_id": REPOSITORY_ID, "head_repository_id": REPOSITORY_ID}
    require(isinstance(identity, dict) and all(identity.get(key) == value
            and type(identity.get(key)) is type(value) for key, value in expected.items()),
            "failed-artifact-producer-attribution")
    require(item.get("expired") is False and all(item.get(key) == value
            for key, value in EXPECTED_PROFILE["artifactTimes"].items()), "fixed-failed-artifact-times")
    require(SUPPORT.utc(item["expires_at"]) > (now or datetime.now(timezone.utc)),
            "failed-artifact-expired")


def classify_job_log_stderr(data, overflow=False):
    safe = {"httpStatus": None, "transportCategory": None}
    if (type(data) is not bytes or type(overflow) is not bool or overflow
            or len(data) > MAX_LOG_STDERR or not data.endswith(b"\n")
            or any(byte not in (9, 10, 13) and not 32 <= byte <= 126 for byte in data)):
        return safe
    if data in (TERMINAL_ESCAPE_REFUSAL + b"\n", TERMINAL_ESCAPE_REFUSAL + b"\r\n",
                b"gh: " + TERMINAL_ESCAPE_REFUSAL + b"\n", b"gh: " + TERMINAL_ESCAPE_REFUSAL + b"\r\n"):
        safe["transportCategory"] = "terminal-escape-sequences-refused"
        return safe
    status = re.fullmatch(rb"gh: [ -~]{0,2048} \(HTTP ([45][0-9]{2})\)\r?\n", data)
    if status is not None:
        safe["httpStatus"] = int(status[1])
        safe["transportCategory"] = "http-response-error"
    return safe


def capture_job_log_transport(output):
    command = ["gh", "api", "--allow-escape-sequences", "--method", "GET", "repos/" + REPOSITORY
               + "/actions/jobs/" + str(JOB) + "/logs"]
    retained = bytearray()
    state = {"overflow": False, "readFailed": False}
    def drain():
        try:
            while True:
                chunk = child.stderr.read(4096)
                if not chunk:
                    break
                if type(chunk) is not bytes:
                    state["readFailed"] = True
                    break
                available = MAX_LOG_STDERR - len(retained)
                retained.extend(chunk[:available])
                state["overflow"] |= len(chunk) > available
        except Exception:
            state["readFailed"] = True
        finally:
            try:
                child.stderr.close()
            except Exception:
                state["readFailed"] = True
    reader = None
    child = subprocess.Popen(command, stdout=output, stderr=subprocess.PIPE, bufsize=0)
    try:
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        code = child.wait(timeout=120)
        reader.join(timeout=5)
        require(not reader.is_alive() and not state["readFailed"], "original-job-log-stderr-capture-failed")
    except BaseException:
        # Every failed startup/wait/read path disposes only this owned gh child.
        # Secondary cleanup failures must not replace or stringify the primary.
        for cleanup in (child.kill, lambda: child.wait(timeout=5)):
            try:
                cleanup()
            except BaseException:
                pass
        if reader is not None:
            try:
                reader.join(timeout=5)
            except BaseException:
                pass
        # Popen's unbuffered pipe has no BufferedReader lock held by the drainer.
        try:
            child.stderr.close()
        except BaseException:
            pass
        raise
    return code, bytes(retained), state["overflow"]


def exact_original_log(private, receipt):
    path = private / "original-failed-intel-job.log"
    with path.open("x+b") as output:
        os.chmod(path, 0o600)
        code, stderr_data, stderr_overflow = capture_job_log_transport(output)
        require(type(code) is int and -(2**31) <= code < 2**31,
                "original-job-log-exit-code")
        receipt["originalJobLogTransport"] = classify_job_log_stderr(stderr_data, stderr_overflow)
        with (private / "original-failed-intel-job.stderr").open("xb") as stderr_output:
            os.chmod(stderr_output.name, 0o600)
            stderr_output.write(stderr_data)
        output.flush()
        before = os.fstat(output.fileno())
        observed = {"exitCode": code, "bytes": before.st_size, "sha256": None}
        receipt["originalJobLogObservation"] = observed
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                  value.st_mtime_ns, value.st_mode, value.st_uid, value.st_nlink)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_nlink == 1 and 0 <= before.st_size <= SUPPORT.MAX_API
                and identity(path.lstat()) == identity(before), "original-job-log-owned-byte-bound")
        output.seek(0)
        data = output.read(SUPPORT.MAX_API + 1)
        after = os.fstat(output.fileno())
        require(len(data) == before.st_size and identity(before) == identity(after)
                and identity(path.lstat()) == identity(after), "original-job-log-read-race")
        observed["sha256"] = sha(data)
    require(not stderr_overflow, "original-job-log-stderr-byte-bound")
    require(observed == {"exitCode": 0, "bytes": LOG_BYTES, "sha256": LOG_SHA256},
            "exact-original-job-log-attribution")


def validate_outer(archive):
    entries, _, _ = SUPPORT.inventory(archive, SUPPORT.MAX_OUTER, 128)
    files = [entry.filename for entry in entries if not entry.is_dir()]
    allowed = re.compile(r"x86_64/runtime/(?:runtime\.json|owned-app-log-[1-9][0-9]?\.log|owned-crash-[1-9][0-9]?\.json)\Z")
    require(files.count(OUTER_APP) == 1 and files.count(OUTER_RUNTIME) == 1
            and all(name == OUTER_APP or allowed.fullmatch(name) for name in files), "fixed-intel-outer-shape")
    require(0 < archive.getinfo(OUTER_APP).file_size <= SUPPORT.MAX_NESTED
            and 0 < archive.getinfo(OUTER_RUNTIME).file_size <= 12 * 1024 * 1024,
            "fixed-intel-outer-payload-bound")
    return SUPPORT.unique_json(archive.read(OUTER_RUNTIME))


def validate_embedded(record, nested):
    require(isinstance(record, dict) and type(record.get("schema")) is int and record["schema"] == 1
            and record.get("scope") == "macos-launch-smoke-diagnostic-not-customer-release"
            and record.get("runner_context") == {"source_sha": SOURCE, "run_id": RUN}
            and record.get("outcome") == "success" and record.get("privacy_check_passed") is True
            and all(record.get(key) is False for key in
                    ("customer_app_qualified", "screen_control_qualified", "publication")),
            "embedded-original-desktop-diagnostic-scope")
    archive = record.get("diagnostic_app_archive", {})
    require(isinstance(archive, dict) and archive.get("name") == "signed-stapled-diagnostic-app.zip"
            and type(archive.get("bytes")) is int and archive["bytes"] == nested.stat().st_size
            and archive.get("sha256") == SUPPORT.file_digest(nested)
            and record.get("executable_sha256") == CRITICAL_HASHES["Contents/MacOS/Mixel-Remote"],
            "embedded-intel-app-digest")


def verify_app(app):
    SUPPORT.verify_tree(app)
    required = {"CFBundleIdentifier": "ch.mixel.remote", "CFBundleExecutable": "Mixel-Remote",
                "CFBundleShortVersionString": "1.4.6", "CFBundleVersion": "64", "LSMinimumSystemVersion": "10.14"}
    info = plistlib.loads(SUPPORT.bounded_read(app / "Contents/Info.plist", 1024 * 1024))
    require(all(info.get(key) == value for key, value in required.items()), "exact-intel-bundle-identity-minimum")
    hashes = {name: SUPPORT.file_digest((app / name).resolve(strict=True)) for name in CRITICAL_HASHES}
    require(hashes == CRITICAL_HASHES, "four-exact-original-intel-binary-hashes")
    return hashes


def hosted_context(diagnostic, producer):
    require(sys.platform == "darwin" and platform.machine() == "arm64"
            and os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_OS") == "macOS"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
            and os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "hosted-arm64-rosetta-manual-only")
    own_source = os.environ.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", own_source) and own_source != SOURCE
            and os.environ.get("GITHUB_WORKFLOW_SHA") == own_source
            and re.fullmatch(r"[1-9][0-9]{0,19}", os.environ.get("GITHUB_RUN_ID", ""))
            and os.environ.get("GITHUB_RUN_ID") != str(RUN)
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
            and os.environ.get("GITHUB_REF") == "refs/heads/" + BRANCH
            and os.environ.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + DIAGNOSTIC_WORKFLOW
            + "@refs/heads/" + BRANCH, "diagnostic-event-source-binding")
    for checkout, expected in ((diagnostic, own_source), (producer, SOURCE)):
        require(checkout.is_dir() and not checkout.is_symlink(), "checkout-canonical-directory")
        stdout, _ = capture(["git", "-C", str(checkout), "rev-parse", "HEAD"], "checkout-head")
        require(stdout.decode().strip() == expected, "exact-checkout-source")
        stdout, _ = capture(["git", "-C", str(checkout), "status", "--porcelain"], "checkout-status")
        require(stdout == b"", "checkout-dirty")
    stdout, _ = capture(["git", "-C", str(producer), "rev-parse", "HEAD^{tree}"], "producer-tree")
    require(stdout.decode().strip() == SOURCE_TREE, "exact-producer-tree")
    old = (producer / WORKFLOW).read_text()
    ordered = ('python3 ../scripts/test-support-launch-macos-concurrency.py --original-desktop',
               'python3 ../scripts/test-support-launch-macos-concurrency.py --original-tray',
               'python3 ../scripts/test-support-launch-macos-concurrency.py --app')
    require(all(old.count(fragment) == 1 for fragment in ordered)
            and old.index(ordered[0]) < old.index(ordered[1]) < old.index(ordered[2])
            and 'DIAGNOSTIC_APP_ZIP="$DIAGNOSTICS/signed-stapled-diagnostic-app.zip"' in old
            and "if: ${{ always() && steps.macos_package.outcome == 'failure' }}" in old,
            "exact-producer-ordered-fixture-contract")
    return own_source


def runtime_endpoints():
    incoming = Path("/tmp/Mixel-Remote/ipc")
    url = Path(str(incoming) + "_url")
    return (incoming, incoming.with_suffix(".pid"), url, url.with_suffix(".pid"))


def process_inventory():
    raw, _ = capture(["ps", "-axo", "pid=,stat=,comm="], "process-inventory", maximum=4 * 1024 * 1024)
    rows = []
    for line in raw.decode("utf-8", errors="strict").splitlines():
        parts = line.strip().split(None, 2)
        require(len(parts) == 3 and re.fullmatch(r"[1-9][0-9]{0,9}", parts[0]), "process-inventory-shape")
        if "Z" not in parts[1] and (parts[2].endswith("/Mixel-Remote")
                                  or "/Mixel-Remote.app/" in parts[2]
                                  or parts[2].endswith("/RustDesk")):
            rows.append(int(parts[0]))
    return rows


def fresh_runtime_state():
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    require(os.environ.get("HOME") == str(home) and home == home.resolve(strict=True)
            and home.stat().st_uid == os.getuid(), "runner-home-identity")
    paths = (Path("/tmp/Mixel-Remote"),
             Path("/private/tmp/mixel-remote-attended-v2-" + str(os.getuid())),
             Path("/private/tmp/mixel-remote-gui-launch-v1-" + str(os.getuid())),
             home / "Library/Logs/Mixel-Remote",
             home / "Library/Preferences/com.carriez.Mixel-Remote",
             home / "Library/Application Support/com.carriez.Mixel-Remote",
             home / "Library/Caches/com.carriez.Mixel-Remote",
             home / "Library/Preferences/ch.mixel.remote.plist",
             home / "Library/Saved Application State/ch.mixel.remote.savedState")
    require(not any(os.path.lexists(path) for path in paths) and not process_inventory(),
            "pre-existing-runtime-state-refused")


def stopped_runtime():
    require(not any(os.path.lexists(path) for path in runtime_endpoints())
            and not process_inventory(), "runtime-cleanup-absence-not-proven")


def safe_error(record, endpoint=False):
    fields = {"reasonCategory", "errorType"} | ({"reasonCode", "subphase"} if endpoint else set())
    require(isinstance(record, dict) and set(record) == fields
            and record.get("reasonCategory") in ERROR_KINDS
            and record.get("errorType") == ERROR_KINDS[record["reasonCategory"]],
            "unsafe-error-fact")
    if endpoint:
        require(record.get("reasonCode") in CLEANUP_CODES
                and record.get("subphase") == CLEANUP_CODES[record["reasonCode"]],
                "unreviewed-endpoint-cleanup-code")
    return dict(record)


def safe_facts(record):
    required = {"observedUtc", "scenarios", "syntheticInvite", "customerAcceptTested",
                "remoteDesktopConnected", "invitePayloadDeliveryTested",
                "coldDirectLaunchConcurrencyTested", "passed"}
    require(isinstance(record, dict) and required <= set(record)
            and set(record) <= required | {"failure"}, "concurrency-top-level-schema")
    safe = {"scope": "exact-failed-intel-rosetta-concurrency-diagnostic-only",
            "customerAppQualified": False, "publication": False, "rawLogsIncluded": False}
    for key in required - {"observedUtc", "scenarios"}:
        require(type(record[key]) is bool, "concurrency-boolean")
        safe[key] = record[key]
    require(safe["syntheticInvite"] and not any(safe[key] for key in
            ("customerAcceptTested", "remoteDesktopConnected", "invitePayloadDeliveryTested",
             "coldDirectLaunchConcurrencyTested")), "concurrency-diagnostic-only-scope")
    safe["observedUtc"] = SUPPORT.utc(record["observedUtc"]).isoformat()
    rows = record["scenarios"]
    require(isinstance(rows, list) and len(rows) <= 3, "scenario-bound")
    safe["scenarios"] = []
    boolean_fields = {"passed", "ownedProcessesStopped", "completionsMatchOriginalTray",
                      "actualTrayDispatchObserved", "visibleGuiAndAttendedV2",
                      "oneRetainedGuiDuringBoundedStability"}
    pid_fields = {"observedTrayCandidatePid", "trayPid", "observedGuiCandidatePid", "guiPid"}
    count_fields = {"inventorySamples", "completionCount", "stabilitySamples"}
    allowed = {"scenario", "criticalHashes", "stabilitySecondsRequired", "runtimeFailure",
               "processCleanupFailures", "endpointCleanupFailures", "unknownSameBundlePids",
               "unknownUrlOwnerPid", "incomingListenerObservations",
               "incomingListenerObservationFailure", "incomingEndpointSettlement",
               "incomingInitialIdentityFailure"} | boolean_fields | pid_fields | count_fields
    for index, row in enumerate(rows):
        require(isinstance(row, dict) and {"scenario", "passed", "ownedProcessesStopped"} <= set(row)
                and set(row) <= allowed and row["scenario"] == SCENARIOS[index], "scenario-schema-order")
        output = {"scenario": row["scenario"]}
        for key in row.keys() & boolean_fields:
            require(type(row[key]) is bool, "scenario-boolean")
            output[key] = row[key]
        for key in row.keys() & pid_fields:
            require(type(row[key]) is int and 0 < row[key] < 2**31, "scenario-pid")
            output[key] = row[key]
        for key in row.keys() & count_fields:
            require(type(row[key]) is int and 0 <= row[key] <= 100000, "scenario-count")
            output[key] = row[key]
        if "criticalHashes" in row:
            require(row["criticalHashes"] == CRITICAL_HASHES, "runtime-four-original-intel-hashes")
            output["criticalHashes"] = dict(CRITICAL_HASHES)
        if "stabilitySecondsRequired" in row:
            require(type(row["stabilitySecondsRequired"]) in (int, float)
                    and row["stabilitySecondsRequired"] == 5.0, "scenario-stability-bound")
            output["stabilitySecondsRequired"] = 5.0
        for key, label, endpoint in (("processCleanupFailures", "process", False),
                                     ("endpointCleanupFailures", "endpoint", True)):
            if key in row:
                failures = row[key]
                require(isinstance(failures, list) and 0 < len(failures) <= 2, "cleanup-failure-bound")
                output[key] = []
                for failure in failures:
                    require(isinstance(failure, dict) and failure.get(label) in
                            (("url", "incoming") if endpoint else ("gui", "tray")), "cleanup-failure-label")
                    output[key].append({label: failure[label],
                                        **safe_error({name: value for name, value in failure.items()
                                                      if name != label}, endpoint)})
        if "runtimeFailure" in row:
            failure = row["runtimeFailure"]
            require(isinstance(failure, dict) and failure.get("phase") in PHASES, "runtime-failure-phase")
            output["runtimeFailure"] = {"phase": failure["phase"],
                                       **safe_error({key: value for key, value in failure.items() if key != "phase"})}
        for key in ("unknownSameBundlePids", "unknownUrlOwnerPid"):
            if key in row:
                values = row[key] if key == "unknownSameBundlePids" else [row[key]]
                require(isinstance(values, list) and len(values) <= 32
                        and all(value is None or type(value) is int and 0 < value < 2**31 for value in values),
                        "unknown-process-observation-bound")
                output[key] = row[key]
        if "incomingEndpointSettlement" in row:
            settlement = row["incomingEndpointSettlement"]
            require(type(settlement) is dict and set(settlement) == {
                "state", "leaseCapturedBeforeStop", "maximumMs", "attempts", "elapsedMs", "failureCode"},
                "incoming-settlement-schema")
            require(type(settlement["state"]) is str and settlement["state"] in {
                "not-required", "settled", "failed"}
                and type(settlement["leaseCapturedBeforeStop"]) is bool
                and type(settlement["maximumMs"]) is int and settlement["maximumMs"] == 5000
                and type(settlement["attempts"]) is int and 0 <= settlement["attempts"] <= 50
                and type(settlement["elapsedMs"]) is int and 0 <= settlement["elapsedMs"] < 2**31
                and type(settlement["failureCode"]) is str and settlement["failureCode"] in {
                    "none", "lease-unavailable", "identity-refused", "probe-ambiguous",
                    "active-timeout", "cleanup-refused"}, "incoming-settlement-values")
            if settlement["state"] == "failed":
                require(row["passed"] is False and settlement["failureCode"] != "none"
                        and (settlement["leaseCapturedBeforeStop"] is True
                             or settlement["failureCode"] == "lease-unavailable"),
                        "incoming-settlement-failure-state")
            else:
                require(settlement["leaseCapturedBeforeStop"] is True
                        and settlement["failureCode"] == "none"
                        and settlement["elapsedMs"] <= settlement["maximumMs"]
                        and row["ownedProcessesStopped"] is True
                        and ((settlement["state"] == "not-required" and settlement["attempts"] == 0)
                             or (settlement["state"] == "settled" and settlement["attempts"] > 0)),
                        "incoming-settlement-success-state")
            output["incomingEndpointSettlement"] = dict(settlement)
        if "incomingInitialIdentityFailure" in row:
            identity = row["incomingInitialIdentityFailure"]
            require(type(identity) is dict and set(identity) == {
                "subphase", "reason", "errorType", "errno", "peerPidComparison", "peerUidComparison"},
                "initial-incoming-identity-schema")
            require(type(identity["subphase"]) is str and identity["subphase"] in {
                "post-connect-lease-guard", "peer-pid-first", "peer-uid-first", "peer-pid-comparison",
                "peer-uid-comparison", "peer-pid-recheck", "peer-uid-recheck", "post-peer-lease-guard",
                "post-close-lease-guard"}
                and type(identity["reason"]) is str and identity["reason"] in {
                    "unknown-error", "lease-guard-refused", "peer-pid-nonpositive", "peer-pid-unavailable",
                    "peer-uid-unavailable", "peer-pid-mismatch", "peer-uid-mismatch", "peer-pid-changed",
                    "peer-uid-changed", "deadline"}
                and type(identity["errorType"]) is str
                and identity["errorType"] in {"OSError", "RuntimeError", "ValueError", "SubprocessError"}
                and type(identity["peerPidComparison"]) is str and identity["peerPidComparison"] in {
                    "not-read", "expected", "foreign", "nonpositive", "unavailable"}
                and type(identity["peerUidComparison"]) is str and identity["peerUidComparison"] in {
                    "not-read", "expected", "foreign", "unavailable"}, "initial-incoming-identity-values")
            known_errno = {57} | {getattr(errno, name) for name in (
                "ENOTCONN", "ESRCH", "ENOENT", "EPERM", "EACCES", "EBADF", "EINVAL", "EINTR",
                "EAGAIN", "EIO", "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "ENOTSOCK", "ENOTSUP")}
            require(identity["errno"] is None or type(identity["errno"]) is int and identity["errno"] in known_errno,
                    "initial-incoming-identity-errno")
            settlement = row.get("incomingEndpointSettlement", {})
            require(row["passed"] is False and row["ownedProcessesStopped"] is True
                    and settlement.get("state") == "failed" and settlement.get("leaseCapturedBeforeStop") is True
                    and settlement.get("attempts") == 0 and settlement.get("failureCode") in {
                        "identity-refused", "active-timeout"}
                    and any(failure.get("endpoint") == "incoming" and failure.get("reasonCode") == "ipc-endpoint-active"
                            for failure in row.get("endpointCleanupFailures", [])),
                    "initial-incoming-identity-without-original-failure")
            output["incomingInitialIdentityFailure"] = dict(identity)
        require(not {"incomingListenerObservations", "incomingListenerObservationFailure"} <= set(row),
                "incoming-observation-conflicting-state")
        if "incomingListenerObservations" in row:
            require(OBSERVER is not None, "incoming-observer-contract-absent")
            try:
                require(OBSERVER.validate_facts(row["incomingListenerObservations"]) is True,
                        "unsafe-incoming-listener-observation")
            except ValueError:
                raise Refusal("unsafe-incoming-listener-observation") from None
            require(row.get("ownedProcessesStopped") is True
                    and any(failure.get("endpoint") == "incoming"
                            and failure.get("reasonCode") == "ipc-endpoint-active"
                            for failure in row.get("endpointCleanupFailures", [])),
                    "incoming-observation-without-original-failure")
            output["incomingListenerObservations"] = row["incomingListenerObservations"]
        if "incomingListenerObservationFailure" in row:
            require(row["incomingListenerObservationFailure"] == "observer-unavailable"
                    and row.get("ownedProcessesStopped") is True
                    and any(failure.get("endpoint") == "incoming"
                            and failure.get("reasonCode") == "ipc-endpoint-active"
                            for failure in row.get("endpointCleanupFailures", [])),
                    "unsafe-incoming-observation-failure")
            output["incomingListenerObservationFailure"] = "observer-unavailable"
        safe["scenarios"].append(output)
    if "failure" in record:
        safe["failure"] = safe_error(record["failure"])
    return safe


def require_runtime_pass(result, facts):
    require(type(result) is int and result == 0 and facts["passed"] is True
            and "failure" not in facts and len(facts["scenarios"]) == 3, "concurrency-runtime-failed")
    for row in facts["scenarios"]:
        require(all(row.get(key) is True for key in ("passed", "ownedProcessesStopped",
                "completionsMatchOriginalTray", "actualTrayDispatchObserved",
                "visibleGuiAndAttendedV2", "oneRetainedGuiDuringBoundedStability"))
                and row.get("criticalHashes") == CRITICAL_HASHES
                and row.get("incomingEndpointSettlement", {}).get("state") in ("not-required", "settled")
                and row.get("completionCount") == (1 if row["scenario"] == "batch-two-support" else 2)
                and row.get("stabilitySecondsRequired") == 5.0 and row.get("stabilitySamples", 0) > 0
                and not any(key in row for key in ("runtimeFailure", "processCleanupFailures",
                    "endpointCleanupFailures", "unknownSameBundlePids", "unknownUrlOwnerPid",
                    "incomingListenerObservations", "incomingListenerObservationFailure",
                    "incomingInitialIdentityFailure")),
                "concurrency-cleanup-or-observation-failed")


def child_environment():
    return {key: value for key, value in os.environ.items()
            if key not in {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"}}


def run_caller(diagnostic, arguments, category, timeout):
    result = subprocess.run([sys.executable, "-B", str(diagnostic / "scripts/test-support-launch-macos-concurrency.py"),
                             *arguments], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=timeout, env=child_environment())
    require(type(result.returncode) is int, category)
    return result.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("diagnostic-checkout", "producer-checkout", "destination", "safe-output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    diagnostic, producer = args.diagnostic_checkout.absolute(), args.producer_checkout.absolute()
    require(diagnostic == diagnostic.resolve(strict=True) and producer == producer.resolve(strict=True),
            "checkout-alias-refused")
    own_source = hosted_context(diagnostic, producer)
    profile = source_contract(diagnostic)
    validate_run(api("/actions/runs/" + str(RUN)))
    validate_jobs(api("/actions/runs/" + str(RUN) + "/attempts/1/jobs?per_page=100"))
    validate_artifact(api("/actions/artifacts/" + str(ARTIFACT_ID)))
    private = SUPPORT.new_directory(args.destination)
    safe = SUPPORT.new_directory(args.safe_output)
    receipt = {"scope": "failed-intel-rosetta-concurrency-diagnostic-only", "producerSource": SOURCE,
               "producerTree": SOURCE_TREE, "producerRun": RUN, "producerAttempt": ATTEMPT, "failedJob": JOB,
               "artifactId": ARTIFACT_ID, "artifactBytes": ARTIFACT_BYTES, "artifactSha256": ARTIFACT_SHA256,
               "originalJobLogSha256": LOG_SHA256, "diagnosticSource": own_source, "profileSha256": PROFILE_HASH,
               "reviewedCallerSha256": CALLER_HASH, "execution": "x86_64-app-on-arm64-Rosetta",
               "customerAppQualified": False, "publication": False, "remoteDesktopConnected": False,
               "rawLogsIncluded": False, "nativeExecuted": False, "validationPassed": False,
               "runtimePassed": False, "prerequisites": []}
    try:
        exact_original_log(private, receipt)
        outer = private / "exact-failed-intel-artifact.zip"
        with outer.open("xb") as stream:
            os.chmod(outer, 0o600)
            result = subprocess.run(["gh", "api", "--method", "GET", "repos/" + REPOSITORY
                                     + "/actions/artifacts/" + str(ARTIFACT_ID) + "/zip"],
                                    stdout=stream, stderr=subprocess.DEVNULL, timeout=180)
        require(result.returncode == 0 and outer.stat().st_size == ARTIFACT_BYTES
                and SUPPORT.file_digest(outer) == ARTIFACT_SHA256, "downloaded-exact-artifact-size-digest")
        nested = private / "signed-stapled-diagnostic-app.zip"
        with zipfile.ZipFile(outer) as archive:
            embedded = validate_outer(archive)
            with nested.open("xb") as stream:
                os.chmod(nested, 0o600)
                stream.write(archive.read(OUTER_APP))
        validate_embedded(embedded, nested)
        with zipfile.ZipFile(nested) as archive:
            SUPPORT.validate_nested(archive)
        app_root = SUPPORT.new_directory(private / "owned-app-copy")
        capture(["ditto", "-x", "-k", str(nested), str(app_root)], "validated-app-extraction")
        app = app_root / APP
        receipt["criticalHashes"] = verify_app(app)
        SUPPORT.verify_signatures(app)
        minimum = private / "bundle-minimum.json"
        capture([sys.executable, "-B", str(diagnostic / "scripts/verify-macos-bundle-minimum.py"),
                 "--bundle", str(app), "--arch", "x86_64", "--minimum", "10.14", "--output", str(minimum)],
                "complete-original-intel-minimum")
        report = SUPPORT.unique_json(SUPPORT.bounded_read(minimum, SUPPORT.MAX_API))
        require(report.get("status") == "passed" and report.get("architecture") == "x86_64"
                and report.get("advertised_minimum") == "10.14"
                and type(report.get("binary_count")) is int and report["binary_count"] >= 4,
                "complete-original-intel-minimum-result")
        receipt["bundleMinimum"] = "10.14"
        receipt["signingTeam"] = SUPPORT.TEAM
        validate_run(api("/actions/runs/" + str(RUN)))
        validate_jobs(api("/actions/runs/" + str(RUN) + "/attempts/1/jobs?per_page=100"))
        validate_artifact(api("/actions/artifacts/" + str(ARTIFACT_ID)))
        source_contract(diagnostic)
        fresh_runtime_state()
        capture(["arch", "-x86_64", "/usr/bin/true"], "existing-rosetta-required")
        receipt["validationPassed"] = True
        receipt["nativeExecuted"] = True
        desktop = private / "ordered-original-desktop"
        result = run_caller(diagnostic, ["--original-desktop", "--app", str(app), "--diagnostics", str(desktop),
                            "--diagnostic-app-zip", str(nested)], "original-desktop-exit", 420)
        receipt["prerequisites"].append({"fixture": "original-desktop", "exitCode": result})
        require(result == 0, "original-desktop-prerequisite-failed")
        facts = SUPPORT.unique_json(SUPPORT.bounded_read(desktop / "runtime.json", 12 * 1024 * 1024))
        require(facts.get("outcome") == "success" and facts.get("privacy_check_passed") is True
                and facts.get("executable_sha256") == CRITICAL_HASHES["Contents/MacOS/Mixel-Remote"]
                and all(facts.get(key) is False for key in
                        ("customer_app_qualified", "screen_control_qualified", "publication")),
                "original-desktop-safe-prerequisite-failed")
        tray = private / "ordered-original-tray"
        result = run_caller(diagnostic, ["--original-tray", "--app", str(app), "--proofs", str(tray)],
                            "original-tray-exit", 420)
        receipt["prerequisites"].append({"fixture": "original-tray", "exitCode": result})
        require(result == 0, "original-tray-prerequisite-failed")
        facts = SUPPORT.unique_json(SUPPORT.bounded_read(tray / "tray-runtime-safe-facts.json", 65536))
        require(all(facts.get(key) is True for key in ("passed", "ownedProcessesStopped",
                "actualTrayDispatchObserved", "visibleGuiAndAttendedV2", "syntheticInvite"))
                and all(facts.get(key) is False for key in ("customerAcceptTested", "remoteDesktopConnected"))
                and facts.get("criticalHashes") == CRITICAL_HASHES
                and not any(key in facts for key in ("failure", "runtimeFailure", "endpointCleanupFailures")),
                "original-tray-safe-prerequisite-failed")
        concurrency = private / "ordered-concurrency"
        result = run_caller(diagnostic, ["--app", str(app), "--proofs", str(concurrency)],
                            "concurrency-exit", 420)
        receipt["runtimeExitCode"] = result
        facts = safe_facts(SUPPORT.unique_json(SUPPORT.bounded_read(
            concurrency / "concurrency-runtime-safe-facts.json", 65536)))
        SUPPORT.write_json(safe / "concurrency-diagnostic-safe-facts.json", facts)
        verify_app(app)
        require_runtime_pass(result, facts)
        stopped_runtime()
        receipt["runtimePassed"] = True
        print("PASS: exact failed Intel/Rosetta app ordered diagnostic; safe facts only; customer qualification false")
        return 0
    except Refusal as error:
        receipt["refusalCategory"] = str(error)
        raise
    except SUPPORT.Refusal:
        receipt["refusalCategory"] = "frozen-validation-refused"
        raise Refusal("frozen-validation-refused") from None
    except subprocess.TimeoutExpired:
        receipt["refusalCategory"] = "native-child-timeout-cleanup-unverified"
        raise Refusal("native-child-timeout-cleanup-unverified") from None
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile):
        receipt["refusalCategory"] = "diagnostic-operation-failed"
        raise Refusal("diagnostic-operation-failed") from None
    finally:
        SUPPORT.write_json(safe / "validation-receipt.json", receipt)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refusal as error:
        print("FAIL: exact Intel concurrency diagnostic: " + str(error), file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile):
        print("FAIL: exact Intel concurrency diagnostic refused; no customer qualification", file=sys.stderr)
        raise SystemExit(1)

