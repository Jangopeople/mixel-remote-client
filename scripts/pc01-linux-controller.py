#!/usr/bin/env python3
"""Private hosted Linux controller for one independently accepted PC01 session.

Root supplies the exact package/PC01 admission.  This driver never authorizes
Windows, supplies a password/invite, or claims independent Windows observations.
The separate NAS operator performs Accept, checks the actual host, and prepares
only synthetic Notepad/Calculator/file/clipboard observations.
"""
import argparse
import ast
import csv
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import time
import uuid
import zipfile

REPOSITORY = "Jangopeople/mixel-remote-client"
APP_SOURCE = "070da25001f97fabf67d08401e2cb15bde37a33b"
APP_TREE = "4411c8027836673ded7f1b0b22f7bdac47c32242"
PRODUCER = 39022433327
EXE = "/usr/local/mixel-test/usr/share/mixel-remote/mixel-remote"
FILE = "mixel-proof.bin"
PIN = "OogSlDx9l+fgs0t6ihF3uTg9emyCv01m8cr4ullarRo="
QA_PATHS = {"scripts/pc01-linux-controller.py", "scripts/test-pc01-linux-controller.py",
            ".github/workflows/pc01-linux-controller.yml"}
SOURCE_PINS = {
    "scripts/test-support-session-linux.py": "1b27d953947b73fec295ec709f2bb1f421cbc2843bab275a0bc702594caf6ebc",
    "scripts/test-support-launch-linux.py": "90e343f7cddffb3f830c54ee129e7ae192c0d4b269bc336faab93fb5d95abe18",
    "scripts/collect-linux-runtime-artifact.py": "56ce340dedd4030ec1145b103a09171c2fff7f872ae8c21142239853deb95271",
    "scripts/e2e/Dockerfile.linux": "8c5034fbdd43c29479c0f5224b1a1a33b6553c6d6bff6f785fdb5ec769a5edee",
    "scripts/e2e/start-linux-desktop.sh": "f9d0098bf8b3d86bc364f8f8f179fedeeacf533b5763ff0ca20691ac7e887dc4",
    "scripts/e2e/linux-fixture.py": "696e38b24ac2eb5d9aa4fb5a28a839f700caba365aac14df3370f2ff4dd36fd9",
}
# This closed exception is QA source only; application inputs remain app070.
TEXTURE_QA_OVERLAY = ('scripts/test-support-macos-texture.py',
                      ['100644', 'f22ff22f5e72708cbba15e7e2ab6ce64013fc122'],
                      ['100644', '82ecce3477ca12ec92fa8c362d95014e099c6772'])
PRIVATE_LINUX_JOB = 117367740409
PRIVATE_COLLECTOR_SHA256 = '57d1245346bf7d5ba4af7c645d36a96b685fea71197e55146acc163f82e6cdde'
PRIVATE_LINUX_STEPS = [(1, 'Set up job'),
 (2, 'Run actions/checkout@v5'),
 (3, 'Maximize build space'),
 (4, 'Install build dependencies'),
 (5, 'Install Rust'),
 (6, 'Install Flutter'),
 (7, 'Restore compiled codec cache'),
 (8, 'Setup pinned vcpkg'),
 (9, 'Install vcpkg deps'),
 (10, 'Fetch upstream RustDesk'),
 (11, 'Run Swatinem/rust-cache@v2'),
 (12, 'Apply Mixel-Remote branding'),
 (13, 'Restore bridge files'),
 (14, 'Preflight generated support source and reporter'),
 (15, 'Preserve SDK compatibility diagnostics'),
 (16, 'Preserve Linux file-dialog lifecycle evidence'),
 (17, 'Build (cargo + flutter via build.py)'),
 (18, 'Require the complete generated Flutter app to analyze without errors or warnings'),
 (19, 'Test compiled attended support guard'),
 (20, 'Verify compiled Flutter UI branding'),
 (21, 'Exercise actual Linux cold and warm support launches'),
 (22, 'Locate .deb'),
 (23, 'Run actions/upload-artifact@v6'),
 (24, 'Prove actual relay support session on two native Linux desktops'),
 (25, 'Prove automatic HTTPS support against an isolated owned relay'),
 (26, 'Prove incoming recovery when native TCP and UDP fail independently'),
 (27, 'Prove production HTTPS support with fresh owned native desktops'),
 (28, 'Run actions/upload-artifact@v6'),
 (53, 'Post Run Swatinem/rust-cache@v2'),
 (54, 'Post Restore compiled codec cache'),
 (55, 'Post Install Flutter'),
 (56, 'Post Run actions/checkout@v5'),
 (57, 'Complete job')]
PRIVATE_LINUX_ARTIFACTS = {'linux': {'digest': 'sha256:51944fc004743d9a56cddbb6c86266d02f69f2fa83a5a53dd5e7b374a95c1a06',
           'id': 12098846059,
           'name': 'linux',
           'size_in_bytes': 23831101},
 'linux-support-session-proof': {'digest': 'sha256:9972d82dfd2f0dc9f669f0fe273b6e19253881c85975fb6d41068f7b10352c4b',
                                 'id': 12098259686,
                                 'name': 'linux-support-session-proof',
                                 'size_in_bytes': 6368898}}
PRIVATE_CACHE_INDEX = {'path': '/Users/michaellascar/.codex/artifacts/mixel-remote-reliability-20261016/070da25-metadata-watch-e4aaqd45/ORIGINAL-CACHE-INDEX-4.json', 'bytes':27937, 'sha256':'944260c4b32407115d21aaa434f6c0a75ebb132ff3d5ed73fc58df858081b048'}
PRIVATE_ARTIFACT_SELECTION = {'path':'/Users/michaellascar/.codex/artifacts/mixel-remote-reliability-20261016/070da25-metadata-watch-e4aaqd45/terminal-artifact-inventory-1/SELECTION.json','bytes':2366,'sha256':'29301dafac275002aa0628dc039e47cc2a2508fedfc5aac53c8772cf32f4b3d1'}
PRIVATE_CONFIG_RECEIPT = {'path':'/Users/michaellascar/.codex/artifacts/mixel-remote-reliability-20261016/070da25-metadata-watch-e4aaqd45/common-terminal-originals/117366747077/READBACK.json','bytes':5057,'sha256':'024c54cf6e86877497ab74f9f1e58b7781c2544e98added7777ef1092b35f8be'}
PRIVATE_FLAGS = {key:False for key in ('wholeThreeSuccess','wholeProducerQualified','normalReleaseQualified','nativeQualified','taskComplete','publication','appLaunched','appInstalled')}
METHODS = ("run", "gui", "launch", "screenshot", "windows", "activate", "geometry", "click", "path", "rendered_row")
FLAGS = dict(wholeThreeSuccess=False, releaseQualified=False, nativeQualified=False,
             taskComplete=False, publication=False, PC01Qualified=False)


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as target:
        os.chmod(path, 0o600)
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def canonical_sha(value, size=64):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{" + str(size) + "}", value) is not None


def safe_admission(data, expected_sha, now=None):
    need(sha(data) == expected_sha and len(data) <= 32768, "admission-byte-pin")
    a = json.loads(data)
    need(a.get("schema") == "mixel-current070-PC01-controller-admission-v1" and a.get("decidedBy") == "Root"
         and a.get("decision") == "admit-one-private-hosted-controller-PC01", "Root-admission-required")
    def no_credentials(value):
        if isinstance(value, dict):
            for key, child in value.items():
                need(not re.search(r"password|secret|invite|apikey|credential|token", key, re.I), "credential-field-refused")
                no_credentials(child)
        elif isinstance(value, list):
            for child in value:
                no_credentials(child)
    no_credentials(a)
    need(a.get("applicationSource") == APP_SOURCE and a.get("applicationTree") == APP_TREE
         and a.get("producerRun") == PRODUCER and type(a.get("producerAttempt")) is int
         and a["producerAttempt"] == 1, "application-source")
    need(canonical_sha(a.get("qaSource"), 40) and canonical_sha(a.get("qaTree"), 40), "QA-source-required")
    need(a.get("flags") == FLAGS and all(type(value) is bool for value in a["flags"].values()) and a.get("syntheticDesktopOnly") is True
         and a.get("bothClipboardPreflightsPassed") is True and a.get("independentNASConsoleReady") is True,
         "operator-prerequisites")
    need(re.fullmatch(r"[0-9a-f]{24}", a.get("nonce", "")) is not None, "fresh-synthetic-nonce")
    for key in ("windowsQualificationSHA256", "windowsCold95ReceiptSHA256", "linuxQualificationSHA256", "linuxTerminalOriginalSHA256"):
        need(canonical_sha(a.get(key)), "actual-Root-input-receipt-required")
    host, artifact = a.get("host", {}), a.get("artifact", {})
    need(re.fullmatch(r"[0-9]{6,16}", host.get("id", "")) is not None, "actual-observed-PC01-ID")
    need(all(type(host.get(k)) is int and host[k] > 0 for k in ("incomingPID", "creationFileTime", "session"))
         and type(host.get("integrity")) is int and host["integrity"] == 8192 and host.get("elevated") is False, "actual-Medium-host-identity")
    need(all(canonical_sha(host.get(k)) for k in ("outerSHA256", "mainSHA256", "coreSHA256")), "actual-host-binary-pins")
    need(host.get("attendedV2") is True and host.get("kernelLease") is True
         and type(host.get("authenticatedRemoteCount")) is int and host["authenticatedRemoteCount"] == 0,
         "fresh-host-consent-baseline")
    folder = host.get("syntheticFolder", "")
    need(re.fullmatch(r"[A-Z]:\\Users\\[^\\/:*?\"<>|.][^\\/:*?\"<>|]*\\AppData\\Local\\Temp\\Mixel-QA-" + a["nonce"], folder) is not None,
         "owned-Windows-TEMP-folder")
    observed = dt.datetime.fromisoformat(host.get("observedAt", "").replace("Z", "+00:00"))
    current = now or dt.datetime.now(dt.timezone.utc)
    need(observed.tzinfo is not None and 0 <= (current - observed).total_seconds() <= 1800, "fresh-host-observation")
    need(type(a.get("linuxShippingJob")) is int and a["linuxShippingJob"] > 0, "actual-Linux-job-required")
    need(set(artifact) == {"id", "bytes", "sha256", "debSHA256", "mainSHA256"}
         and type(artifact["id"]) is int and artifact["id"] > 0
         and type(artifact["bytes"]) is int and 0 < artifact["bytes"] <= 128 * 1024 * 1024
         and all(canonical_sha(artifact[k]) for k in ("sha256", "debSHA256", "mainSHA256")), "actual-Linux-artifact-required")
    linux_eligibility(a)
    return a


def linux_eligibility(a):
    """A Root collection policy is eligibility only, never runtime authority."""
    route = a.get("linuxEligibility", "whole-producer-success")
    if route == "whole-producer-success":
        need("linuxPolicy" not in a, "private-policy-requires-explicit-route")
        return None
    need(route == "private-successful-linux-job", "unknown-Linux-eligibility-route")
    carrier = a.get("linuxPolicy", {})
    need(type(carrier) is dict and set(carrier) == {"json", "sha256"}
         and type(carrier["json"]) is str and canonical_sha(carrier["sha256"]), "Root-private-policy-required")
    raw = carrier["json"].encode("utf-8")
    need(0 < len(raw) <= 32768 and sha(raw) == carrier["sha256"], "Root-private-policy-byte-pin")
    def unique(pairs):
        value = {}
        for key, child in pairs:
            need(key not in value, "duplicate-private-policy-key")
            value[key] = child
        return value
    p = json.loads(raw, object_pairs_hook=unique,
                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite-private-policy")))
    keys = {"scope", "schema", "decidedBy", "decision", "applicationSource", "applicationTree", "producerRun", "attempt",
            "linuxJob", "collectorSHA256", "cacheIndex", "artifactSelection", "configReceipt", "producerObservation",
            "artifacts", "allowProducerNonSuccessForPrivateQA", "flags"}
    need(set(p) == keys and p["scope"] == "private-successful-linux-job"
         and p["schema"] == "mixel-current070-private-linux-per-job-policy-v1" and p["decidedBy"] == "Root"
         and p["decision"] == "admit-one-private-linux-normal-and-QA-collection", "shared-Root-private-collection-policy")
    need(p["applicationSource"] == APP_SOURCE and p["applicationTree"] == APP_TREE and p["producerRun"] == PRODUCER
         and type(p["attempt"]) is int and p["attempt"] == 1 and p["linuxJob"] == PRIVATE_LINUX_JOB
         and a["linuxShippingJob"] == PRIVATE_LINUX_JOB and p["collectorSHA256"] == PRIVATE_COLLECTOR_SHA256,
         "exact-reviewed-private-Linux-source-job")
    need(p["cacheIndex"] == PRIVATE_CACHE_INDEX and p["artifactSelection"] == PRIVATE_ARTIFACT_SELECTION
         and p["configReceipt"] == PRIVATE_CONFIG_RECEIPT, "exact-current-selected-config-and-originals")
    observed = p["producerObservation"]
    need(type(observed) is dict and set(observed) == {"status", "conclusion"}
         and (observed["status"], observed["conclusion"]) in (("in_progress", None), ("completed", "failure"), ("completed", "success")),
         "Root-observed-private-producer-state")
    need(p["artifacts"] == PRIVATE_LINUX_ARTIFACTS and p["allowProducerNonSuccessForPrivateQA"] is True
         and p["flags"] == PRIVATE_FLAGS and all(type(value) is bool for value in p["flags"].values()), "strict-private-scope-no-global-credit")
    artifact = p["artifacts"]["linux"]
    need(a["artifact"]["id"] == artifact["id"] and a["artifact"]["bytes"] == artifact["size_in_bytes"]
         and "sha256:" + a["artifact"]["sha256"] == artifact["digest"], "controller-package-matches-shared-policy")
    need(canonical_sha(a.get("linuxCollectionSHA256")), "actual-private-Linux-collection-receipt-required")
    # The enclosing admission still requires actual Linux qualification, current
    # Windows/cold95 qualification and NAS/Medium-host/clipboard prerequisites.
    return p


def hosted_guard(a, env, system):
    need(system == "Linux" and env.get("GITHUB_ACTIONS") == "true" and env.get("RUNNER_ENVIRONMENT") == "github-hosted"
         and env.get("RUNNER_ARCH") == "X64" and env.get("GITHUB_REPOSITORY") == REPOSITORY
         and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and env.get("GITHUB_JOB") == "pc01-controller"
         and env.get("GITHUB_SHA") == a["qaSource"] and env.get("GITHUB_RUN_ATTEMPT") == "1", "fresh-hosted-runner-only")


def compare_inventory(application, qa):
    changed = {path for path in set(application) | set(qa) if application.get(path) != qa.get(path)}
    texture, original, corrected = TEXTURE_QA_OVERLAY
    need(changed <= QA_PATHS | {texture} and QA_PATHS - {"HANDOFF.md"} <= changed, "exact-QA-only-source-delta")
    if texture in changed:
        need(application.get(texture) == original and qa.get(texture) == corrected, "exact-closed-texture-QA-overlay")
    need(not any(path not in application and path not in QA_PATHS for path in qa), "unexpected-new-source")
    need(all(qa[path][0] == value[0] for path, value in application.items() if path in qa), "all-existing-source-modes-exact")
    need(all(value[0] in ("100644", "100755") for path, value in qa.items() if path not in application), "new-QA-files-regular")
    return sorted(changed)


def source_equivalence(root, a):
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=20).stdout
    need(git("rev-parse", "HEAD").decode().strip() == a["qaSource"]
         and git("rev-parse", "HEAD^{tree}").decode().strip() == a["qaTree"]
         and git("rev-parse", APP_SOURCE + "^{tree}").decode().strip() == APP_TREE, "actual-Git-source-tree")
    need(not git("status", "--porcelain", "--untracked-files=all"), "clean-QA-checkout-required")
    def inventory(ref):
        result = {}
        for row in git("ls-tree", "-r", "-z", ref).split(b"\0"):
            if row:
                metadata, name = row.split(b"\t", 1)
                mode, kind, blob = metadata.decode().split()
                need(kind == "blob", "tracked-nonblob-refused")
                result[name.decode()] = [mode, blob]
        return result
    delta = compare_inventory(inventory(APP_SOURCE), inventory("HEAD"))
    for name, expected in SOURCE_PINS.items():
        path = root / name
        need(path.is_file() and not path.is_symlink() and sha(path.read_bytes()) == expected, "owned-source-pin")
    return {"applicationSource": APP_SOURCE, "applicationTree": APP_TREE, "qaSource": a["qaSource"],
            "qaTree": a["qaTree"], "QAOnlyChangedPaths": delta, "sourcePins": SOURCE_PINS,
            "closedTextureQAOverlayApplied": TEXTURE_QA_OVERLAY[0] in delta}


class Provider:
    """Exactly three metadata reads, then one admitted archive; bounded pipes."""
    def __init__(self, output, admission):
        self.output, self.a, self.number = output, admission, 0

    def fetch(self, endpoint, archive=False):
        allowed = [f"repos/{REPOSITORY}/actions/runs/{PRODUCER}",
                   f"repos/{REPOSITORY}/actions/jobs/{self.a['linuxShippingJob']}",
                   f"repos/{REPOSITORY}/actions/artifacts/{self.a['artifact']['id']}",
                   f"repos/{REPOSITORY}/actions/artifacts/{self.a['artifact']['id']}/zip"]
        need(self.number < 4 and endpoint == allowed[self.number] and archive == (self.number == 3), "closed-once-provider-order")
        self.number += 1
        directory = self.output / ("request-" + str(self.number))
        directory.mkdir(mode=0o700)
        write_json(directory / "INTENT.json", {"endpoint": endpoint, "archive": archive, "attempt": 1})
        limit = self.a["artifact"]["bytes"] if archive else 1024 * 1024
        path, errors = directory / "original.stdout", directory / "original.stderr"
        deadline = time.monotonic() + (120 if archive else 30)
        proc = subprocess.Popen(["gh", "api", "--hostname", "github.com", endpoint], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        counts = {proc.stdout: 0, proc.stderr: 0}
        try:
            with path.open("xb") as out, errors.open("xb") as err:
                os.chmod(path, 0o600); os.chmod(errors, 0o600)
                destinations = {proc.stdout: out, proc.stderr: err}
                with selectors.DefaultSelector() as selected:
                    for stream in destinations:
                        selected.register(stream, selectors.EVENT_READ)
                    while selected.get_map():
                        need(time.monotonic() < deadline, "provider-deadline")
                        for key, _ in selected.select(.2):
                            block = os.read(key.fileobj.fileno(), 65536)
                            if not block:
                                selected.unregister(key.fileobj)
                                continue
                            counts[key.fileobj] += len(block)
                            need(counts[key.fileobj] <= (limit if key.fileobj is proc.stdout else 1024 * 1024), "provider-byte-bound")
                            destinations[key.fileobj].write(block)
                code = proc.wait(timeout=max(.1, deadline - time.monotonic()))
                for target in destinations.values():
                    target.flush(); os.fsync(target.fileno())
            write_json(directory / "RESULT.json", {"exit": code, "stdoutBytes": path.stat().st_size,
                       "stdoutSHA256": sha(path.read_bytes()), "stderrBytes": errors.stat().st_size,
                       "stderrSHA256": sha(errors.read_bytes()), "retry": False})
            need(code == 0, "provider-nonzero-no-retry")
            return path
        finally:
            if proc.poll() is None:
                proc.kill(); proc.wait(timeout=5)
            proc.stdout.close(); proc.stderr.close()


def package_metadata(a, run, job, artifact):
    need(run.get("id") == PRODUCER and type(run.get("run_attempt")) is int and run["run_attempt"] == 1 and run.get("head_sha") == APP_SOURCE
         and run.get("repository", {}).get("full_name") == REPOSITORY and run["repository"].get("id") == 1229987840
         and run.get("workflow_id") == 271445168 and run.get("head_branch") == "fix/remote-support-reliability"
         and run.get("event") == "workflow_dispatch"
         and run.get("path") == ".github/workflows/build.yml" and run.get("status") == "completed"
         and run.get("conclusion") == "success", "actual-current-producer-success")
    need(job.get("id") == a["linuxShippingJob"] and job.get("run_id") == PRODUCER
         and job.get("name") == "linux" and job.get("status") == "completed"
         and job.get("conclusion") == "success" and job.get("steps")
         and all(s.get("status") == "completed" and s.get("conclusion") in ("success", "skipped") for s in job["steps"]), "actual-Linux-shipping-success")
    expected = a["artifact"]
    need(artifact.get("id") == expected["id"] and artifact.get("name") == "linux" and artifact.get("expired") is False
         and artifact.get("size_in_bytes") == expected["bytes"] and artifact.get("digest") == "sha256:" + expected["sha256"]
         and artifact.get("workflow_run", {}).get("id") == PRODUCER
         and artifact["workflow_run"].get("head_sha") == APP_SOURCE, "actual-normal-Linux-artifact")
    parse = lambda x: dt.datetime.fromisoformat(x.replace("Z", "+00:00"))
    need(parse(job["started_at"]) <= parse(artifact["created_at"]) <= parse(job["completed_at"]), "Linux-upload-job-time-window")


def private_package_metadata(a, run, job, artifact, policy):
    """One successful exact Linux job; actual producer state stays recorded."""
    need(policy == linux_eligibility(a) and policy is not None, "Root-private-policy-before-metadata")
    need(run.get("id") == PRODUCER and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
         and run.get("head_sha") == APP_SOURCE and run.get("head_branch") == "fix/remote-support-reliability"
         and run.get("repository", {}).get("id") == 1229987840 and run["repository"].get("full_name") == REPOSITORY
         and run.get("head_repository", {}).get("id") == 1229987840 and run["head_repository"].get("full_name") == REPOSITORY
         and run.get("workflow_id") == 271445168 and run.get("path") == ".github/workflows/build.yml"
         and run.get("event") == "workflow_dispatch", "private-current-producer-identity")
    state = run.get("status"), run.get("conclusion")
    need(state in (("in_progress", None), ("completed", "failure"), ("completed", "success")), "private-observed-producer-state")
    observed = policy["producerObservation"]
    need(observed["status"] != "completed" or state == (observed["status"], observed["conclusion"]), "no-private-producer-regression")
    need(job.get("id") == PRIVATE_LINUX_JOB and job.get("run_id") == PRODUCER
         and type(job.get("run_attempt")) is int and job["run_attempt"] == 1
         and job.get("head_sha") == APP_SOURCE and job.get("head_branch") == "fix/remote-support-reliability"
         and job.get("name") == "linux" and job.get("status") == "completed" and job.get("conclusion") == "success",
         "private-exact-successful-Linux-job")
    steps = job.get("steps", [])
    need([(s.get("number"), s.get("name")) for s in steps] == PRIVATE_LINUX_STEPS
         and all(s.get("status") == "completed" and s.get("conclusion") == "success" for s in steps), "private-all33-required-steps-success")
    spec = PRIVATE_LINUX_ARTIFACTS["linux"]
    need(all(artifact.get(key) == value for key, value in spec.items()) and artifact.get("expired") is False, "private-exact-normal-artifact")
    origin = artifact.get("workflow_run", {})
    need(origin.get("id") == PRODUCER and origin.get("head_sha") == APP_SOURCE
         and origin.get("repository_id") == 1229987840 and origin.get("head_repository_id") == 1229987840
         and origin.get("head_branch") == "fix/remote-support-reliability", "private-artifact-source-origin")
    parse = lambda value: dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    need(parse(job["started_at"]) <= parse(artifact["created_at"]) <= parse(job["completed_at"]), "private-artifact-own-job-window")
    return {"scope": "private-successful-linux-job", "producerStatus": state[0], "producerConclusion": state[1],
            "LinuxSuccessfulSteps": 33, "RootPolicySHA256": a["linuxPolicy"]["sha256"],
            "configReceipt": policy["configReceipt"], "wholeProducerSuccessAsserted": False, **FLAGS}


def package_bytes(path, a):
    data = path.read_bytes()
    need(len(data) == a["artifact"]["bytes"] and sha(data) == a["artifact"]["sha256"], "actual-archive-provider-pin")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        need(len(entries) == 1, "one-normal-Linux-installer")
        entry = entries[0]
        need(entry.filename == entry.orig_filename and re.fullmatch(r"mixel-remote-[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?-x86_64\.deb", entry.filename)
             and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG) and not entry.flag_bits & 1
             and 0 < entry.file_size <= 256 * 1024 * 1024, "safe-canonical-DEB-member")
        deb = archive.read(entry)  # Actual decompression and CRC, without extracting arbitrary paths.
        need(sha(deb) == a["artifact"]["debSHA256"], "actual-DEB-pin")
        return entry.filename, deb


def extracted_primitives(root, destination):
    raw = (root / "scripts/test-support-session-linux.py").read_bytes()
    need(sha(raw) == SOURCE_PINS["scripts/test-support-session-linux.py"], "controller-primitives-source")
    tree = ast.parse(raw)
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Session")
    members = [n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name in METHODS]
    need({n.name for n in members} == set(METHODS), "exact-controller-methods")
    owner = ast.ClassDef(name="ControllerPrimitives", bases=[], keywords=[], body=members, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[owner], type_ignores=[]))
    namespace = dict(Path=Path, shlex=shlex, uuid=uuid, time=time, re=re, csv=csv, io=io, EXE=EXE, FILE=FILE,
                     command=command, until=until)
    exec(compile(module, str(root / "scripts/test-support-session-linux.py"), "exec"), namespace)
    text = ast.unparse(module) + "\n"
    (destination / "controller-primitives.py").write_text(text)
    probe = ast.parse((root / "scripts/test-support-launch-linux.py").read_bytes())
    bodies = [n for n in probe.body if isinstance(n, ast.FunctionDef) and n.name in ("frame", "read_exact", "read_frame", "query")]
    need(len(bodies) == 4, "four-read-only-IPC-functions")
    header = "import json,socket\nfrom pathlib import Path\nIPC_PATH=Path('/tmp/Mixel-Remote/ipc')\nMAX_IPC_REPLY=65536\n"
    (destination / "ipc_probe.py").write_text(header + ast.unparse(ast.Module(body=bodies, type_ignores=[])) + "\n")
    return namespace["ControllerPrimitives"]


def project_payload(payload, expected):
    """Only this nonsecret finite payload crosses the readonly guest boundary."""
    names = {"client.deb", FILE, "ipc_probe.py", "controller-primitives.py"}
    need(set(expected) == names and payload.is_dir() and not payload.is_symlink(), "finite-nonsecret-payload")
    need({path.name for path in payload.iterdir()} == names, "no-extra-payload-members")
    members = []
    for name in sorted(names):
        path = payload / name
        metadata = path.lstat()
        need(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and sha(path.read_bytes()) == expected[name], "exact-regular-payload-member")
        members.append({"name": name, "bytes": metadata.st_size, "sha256": expected[name]})
    # All entries are validated before any permission change. The containing
    # evidence root stays0700; only these nonsecret bind-mounted bytes are readable.
    payload.chmod(0o755)
    for member in members:
        (payload / member["name"]).chmod(0o644)
    return {"directoryMode": 0o755, "fileMode": 0o644, "members": members, "readonlyBindRequired": True}


def command(arguments, *, check=True, timeout=30):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        # Do not publish app output, arbitrary clipboard values, or remote text.
        raise RuntimeError(Path(arguments[0]).name + " failed, exit=" + str(result.returncode))
    return result


def until(description, operation, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = operation()
        if value:
            return value
        time.sleep(.4)
    raise RuntimeError("Timed out: " + description)


def session_argv(kind, host_id):
    need(kind in ("--connect", "--file-transfer") and re.fullmatch(r"[0-9]{6,16}", host_id), "ordinary-ID-relay-only")
    return [kind, host_id, "--relay"]


def ocr_has(text, expected):
    # Exact synthetic token as a complete OCR word; no unexpected text is logged.
    return expected in re.findall(r"[A-Za-z0-9]+", text)


def marker_center(tsv, expected):
    matches = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        if row.get("text", "").strip() == expected:
            matches.append((int(row["left"]) + int(row["width"]) / 2,
                            int(row["top"]) + int(row["height"]) / 2))
    need(len(matches) <= 1, "ambiguous-synthetic-marker")
    return matches[0] if matches else None


class Controller:
    def __init__(self, primitives, root, output, admission, payload, projection):
        self.a, self.output, self.payload = admission, output, payload
        self.name = "mixel-pc01-" + uuid.uuid4().hex
        self.image = self.name + ":qa"
        self.image_id = None
        self.created = False
        self.p = primitives()
        self.p.names = {"controller": self.name}
        self.p.proofs = output
        self.identities, self.phases = {}, []
        self.root = root
        self.projection = projection

    def run(self, args, **kwargs):
        return self.p.run("controller", args, **kwargs)

    def gui(self, args, **kwargs):
        return self.p.gui("controller", args, **kwargs)

    def setup(self):
        context = self.output / "context"
        (context / "scripts/e2e").mkdir(parents=True)
        for name in ("Dockerfile.linux", "start-linux-desktop.sh", "linux-fixture.py"):
            shutil.copyfile(self.root / "scripts/e2e" / name, context / "scripts/e2e" / name)
        command(["docker", "pull", "--platform", "linux/amd64", "ubuntu:22.04"], timeout=300)
        base = json.loads(command(["docker", "image", "inspect", "ubuntu:22.04"]).stdout)
        need(len(base) == 1 and base[0]["Os"] == "linux" and base[0]["Architecture"] == "amd64", "official-base-platform")
        digests = [value for value in base[0]["RepoDigests"] if re.fullmatch(r"ubuntu@sha256:[0-9a-f]{64}", value)]
        need(len(digests) == 1, "resolved-official-base-digest")
        dockerfile = context / "scripts/e2e/Dockerfile.linux"
        original = dockerfile.read_text()
        need(original.startswith("FROM ubuntu:22.04\n") and original.count("FROM ") == 1, "exact-owned-base-projection")
        pinned = "FROM " + digests[0] + "\n" + original.split("\n", 1)[1]
        need(pinned.replace("FROM " + digests[0] + "\n", "FROM ubuntu:22.04\n", 1) == original, "base-whole-inverse")
        dockerfile.write_text(pinned)
        write_json(self.output / "DEPENDENCY-BASE.json", {"digest": digests[0], "imageId": base[0]["Id"],
                   "sourceDockerfileSHA256": sha(original.encode()), "ownedDockerfileSHA256": sha(pinned.encode()),
                   "onlyFirstFromLineChanged": True})
        result = command(["docker", "build", "--platform", "linux/amd64", "--label", "com.mixel.qa=" + self.name,
                          "-f", str(context / "scripts/e2e/Dockerfile.linux"), "-t", self.image, str(context)], timeout=600)
        (self.output / "docker-build.stdout").write_text(result.stdout)
        (self.output / "docker-build.stderr").write_text(result.stderr)
        self.image_id = command(["docker", "image", "inspect", "--format", "{{.Id}}", self.image]).stdout.strip()
        need(re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_id), "new-image-identity")
        self.created = True  # Enroll the exact fresh name before Docker may create it.
        command(["docker", "run", "-d", "--name", self.name, "--platform", "linux/amd64", "--label", "com.mixel.qa=" + self.name,
                 "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=256", "--memory=2g", "--cpus=2",
                 "--network=bridge", "--tmpfs", "/proofs:rw,nosuid,nodev,uid=1000,gid=1000,mode=0700,size=128m",
                 "--mount", "type=bind,src=" + str(self.payload) + ",dst=/payload,readonly", self.image_id])
        self.ownership()
        until("fresh own X11 desktop", lambda: self.run(["test", "-f", "/proofs/desktop-ready"], check=False).returncode == 0, timeout=30)
        probe = ("import os,json,hashlib;from pathlib import Path;"
                 "assert os.geteuid()==1000;root=Path('/payload');expected=" + repr(self.projection["members"]) + ";"
                 "assert {p.name for p in root.iterdir()}=={m['name'] for m in expected};"
                 "assert not os.access(root,os.W_OK) and all(not os.access(root/m['name'],os.W_OK) for m in expected);"
                 "assert all(hashlib.sha256((root/m['name']).read_bytes()).hexdigest()==m['sha256'] for m in expected);"
                 "import sys;sys.path.insert(0,'/payload');from ipc_probe import query;"
                 "print(json.dumps({'guestUID':os.geteuid(),'exactPayloadReadable':True,'readonlyPayload':True,'ipcDefinitionsImportable':True}))")
        readable = json.loads(self.run(["python3", "-B", "-c", probe]).stdout)
        need(readable == {"guestUID": 1000, "exactPayloadReadable": True, "readonlyPayload": True, "ipcDefinitionsImportable": True}, "actual-guest-readable-projection")
        write_json(self.output / "GUEST-PAYLOAD-READABILITY.json", readable)
        self.run(["bash", "-c", "mkdir -p /usr/local/mixel-test; dpkg-deb -x /payload/client.deb /usr/local/mixel-test"], user="root")
        need(self.run(["sha256sum", EXE]).stdout.split()[0] == self.a["artifact"]["mainSHA256"], "actual-extracted-controller-main")
        need(self.run(["id", "-u"]).stdout.strip() == "1000", "guest-only-controller")
        self.server = self.p.launch("controller", ["--server"])
        self.identities["server"] = self.identity(self.server)
        until("own registered controller ID", self.registration, timeout=90)
        self.main = self.p.launch("controller", [])
        self.identities["GUI"] = self.identity(self.main)
        until("ordinary controller GUI", lambda: self.p.windows("controller", "^Mixel-Remote$"), timeout=45)
        self.gui(["bash", "-c", 'xdotool search --name "Mixel isolated customer desktop" windowminimize'])
        write_json(self.output / "CONTROLLER-IDENTITY.json", {"container": self.name, "imageId": self.image_id, "id": self.controller_id,
                   "processes": self.identities, "source": APP_SOURCE, "mainSHA256": self.a["artifact"]["mainSHA256"]})
        print("OPERATOR: independent NAS Accept must show controller ID " + self.controller_id, flush=True)

    def ownership(self, timeout=30):
        result = json.loads(command(["docker", "inspect", self.name], timeout=timeout).stdout)
        need(len(result) == 1 and result[0]["Name"] == "/" + self.name and result[0]["Image"] == self.image_id
             and result[0]["Config"]["Labels"].get("com.mixel.qa") == self.name, "new-owned-controller-resource")

    def identity(self, pid, timeout=30):
        code = "import json,os,hashlib;from pathlib import Path;p=" + str(pid) + ";s=Path('/proc/'+str(p)+'/stat').read_text();print(json.dumps({'pid':p,'startTicks':int(s[s.rfind(')')+2:].split()[19]),'exe':os.readlink('/proc/'+str(p)+'/exe'),'sha256':hashlib.sha256(Path('/proc/'+str(p)+'/exe').read_bytes()).hexdigest()}))"
        fact = json.loads(self.run(["python3", "-c", code], timeout=timeout).stdout)
        need(fact["exe"] == EXE and fact["sha256"] == self.a["artifact"]["mainSHA256"], "actual-owned-process-image")
        return fact

    def alive(self, timeout=30):
        need(set(self.identities) <= {"server", "GUI"}, "bounded-owned-identity-set")
        for fact in self.identities.values():
            need(self.identity(fact["pid"], timeout=timeout) == fact, "same-controller-kernel-process")
        failures = self.run(["python3", "-c", "import json;from pathlib import Path;print(json.dumps([json.loads(p.read_text()) for p in Path('/proofs').glob('launch-*-exit.json')]))"], timeout=timeout).stdout
        need(not any(x["wait_status"] != 0 for x in json.loads(failures)), "controller-child-nonzero-is-failure")

    def registration(self):
        code = "import sys,json;sys.path.insert(0,'/payload');from ipc_probe import query;print(json.dumps([query('OnlineStatus'),query('Config',['id',None]),query('Options')]))"
        try:
            state, device, options = json.loads(self.run(["python3", "-c", code]).stdout)
            need(self.run(["cat", "/tmp/Mixel-Remote/ipc.pid"]).stdout.strip() == str(self.server), "own-controller-IPC-PID")
            if not (isinstance(state, list) and len(state) == 2 and type(state[0]) is int and state[0] > 0 and state[1] is True):
                return None
            need(isinstance(device, list) and len(device) == 2 and device[0] == "id" and re.fullmatch(r"[0-9]{6,16}", device[1]), "actual-controller-ID")
            need(all(options.get(k) == v for k, v in {"custom-rendezvous-server": "rs.mixel.ch", "relay-server": "rs.mixel.ch", "key": PIN}.items()), "branded-controller-relay")
            self.controller_id = device[1]
            return True
        except (RuntimeError, ValueError, json.JSONDecodeError):
            return None

    def screen(self, label):
        need(re.fullmatch(r"[A-Za-z0-9-]{1,80}", label) is not None, "owned-screenshot-label")
        self.alive()
        self.p.screenshot("controller", label)
        source = "/proofs/" + label + ".png"
        text = self.run(["tesseract", source, "stdout", "--psm", "11"]).stdout
        command(["docker", "cp", self.name + ":" + source, str(self.output / (label + ".png"))])
        return text

    def marker(self, marker, phase, timeout=180):
        print("OPERATOR checkpoint " + phase + ": show only synthetic Notepad marker " + marker + " after the required independent NAS checks/Accept.", flush=True)
        def observe():
            return ocr_has(self.screen(phase + "-candidate"), marker)
        until("operator synthetic marker " + phase, observe, timeout=timeout)
        tsv = self.run(["tesseract", "/proofs/" + phase + "-candidate.png", "stdout", "--psm", "11", "tsv"]).stdout
        center = marker_center(tsv, marker)
        need(center is not None, "exact-visible-marker-location")
        self.p.click("controller", *center)
        self.phases.append({"phase": phase, "marker": marker, "mouseClick": list(center), "observedMonotonic": time.monotonic(),
                            "independentWindowsEvidenceVerified": False})

    def viewer(self):
        return until("owned remote viewer", lambda: next(iter(self.p.windows("controller", "Remote Desktop.*Mixel-Remote$")), None), timeout=45)

    def connect(self, phase):
        pid = self.p.launch("controller", session_argv("--connect", self.a["host"]["id"]))
        self.phases.append({"phase": phase + "-sender", "pid": pid,
                            "note": "short-lived sender may forward to actual viewer; its exit JSON is independently retained"})
        window = self.viewer()
        self.p.activate("controller", window)
        self.gui(["wmctrl", "-ir", window, "-b", "add,fullscreen"])
        viewer_pid = int(self.gui(["xdotool", "getwindowpid", window]).stdout.strip())
        self.phases.append({"phase": phase + "-actual-viewer", "window": window, "identity": self.identity(viewer_pid)})
        print("OPERATOR: verify same PC01 incoming PID/birth/hash, typed auth0, blue Accept and this exact controller ID. Hold Accept until the pending interval below finishes. No password.", flush=True)
        marker = "mixelready" + self.a["nonce"] + phase
        start = time.monotonic()
        for index in range(3):
            text = self.screen(phase + "-before-Accept-" + str(index))
            need(not ocr_has(text, marker), "unexpected-pre-Accept-fresh-marker")
            time.sleep(4)
        self.phases.append({"phase": phase + "-pending-controller-observation", "seconds": time.monotonic() - start,
                            "hostAuth0OrBlueAcceptProvenByController": False})
        print("OPERATOR: pending12s interval finished; independently click actual PC01 Accept, then prepare its requested synthetic Notepad marker.", flush=True)
        self.marker(marker, phase + "-accepted")
        self.p.activate("controller", window)
        return window

    def input_and_clipboard(self, window):
        # Root leaves only this new Notepad document focused after its marker.
        self.gui(["xdotool", "key", "ctrl+a"])
        text = "mixelinput" + self.a["nonce"]
        self.gui(["xdotool", "type", "--clearmodifiers", text])
        self.gui(["xdotool", "key", "Return"])
        self.gui(["xdotool", "key", "a"])
        self.gui(["xdotool", "keydown", "Shift_L"])
        try:
            self.gui(["xdotool", "key", "a"])
        finally:
            self.gui(["xdotool", "keyup", "Shift_L"])
        self.gui(["xdotool", "key", "a"])
        # Root independently reads aAa; OCR normalization cannot prove case.
        until("changing actual Notepad input", lambda: ocr_has(self.screen("Notepad-input"), text), timeout=30)
        self.phases.append({"phase": "Notepad-input", "text": text, "expectedKeySequence": "aAa", "exactCaseIndependentNASReadPending": True})
        token = "mixellinuxclipboard" + self.a["nonce"]
        self.gui(["bash", "-c", "printf '%s' " + shlex.quote(token) + " | xclip -selection clipboard -in"])
        self.gui(["xdotool", "key", "Return", "ctrl+v"])
        until("Linux clipboard rendered on Windows", lambda: ocr_has(self.screen("clipboard-to-Windows"), token), timeout=30)
        self.marker("mixelwindowsclipboard" + self.a["nonce"], "Windows-clipboard-seeded")
        expected = "mixelwindowsclipboard" + self.a["nonce"]
        actual = self.gui(["xclip", "-selection", "clipboard", "-out"]).stdout
        need(actual == expected, "Windows-to-Linux-exact-synthetic-clipboard")
        self.phases.append({"phase": "synthetic-clipboard", "LinuxToWindowsTokenSHA256": sha(token.encode()),
                            "WindowsToLinuxTokenSHA256": sha(expected.encode()), "nativeWindowsClipboardAssertionPending": True})
        print("OPERATOR: focus normal Calculator, clear it, enter987654321 and leave it visible/focused. Driver will send Escape123+456=; independent NAS must read579.", flush=True)
        until("synthetic Calculator baseline", lambda: "987654321" in re.sub(r"[^0-9]", "", self.screen("Calculator-before")), timeout=180)
        self.gui(["xdotool", "key", "Escape"])
        self.gui(["xdotool", "type", "--clearmodifiers", "123+456"])
        self.gui(["xdotool", "key", "Return"])
        until("actual changing Calculator result", lambda: ocr_has(self.screen("Calculator-after"), "579"), timeout=30)
        self.phases.append({"phase": "Calculator", "baseline": 987654321, "expected": 579, "independentNASReadPending": True})

    def file_row(self, side, window, label):
        box = self.p.geometry("controller", window)
        offset = 500 if side == "remote" else 0
        bounds = (box["X"] + offset + 20, box["Y"] + 238, box["X"] + offset + 475, box["Y"] + 280)
        def observe():
            self.p.screenshot("controller", label)
            self.run(["python3", "-c", "from PIL import Image;i=Image.open('/proofs/" + label + ".png').crop(" + repr(bounds) + ");i.resize((i.width*3,i.height*3)).save('/proofs/" + label + "-row.png')"])
            text = self.run(["tesseract", "/proofs/" + label + "-row.png", "stdout", "--psm", "6", "tsv"]).stdout
            return self.p.rendered_row(text, bounds)
        return until("actual synthetic file row " + side, observe, timeout=60)

    def files(self):
        print("OPERATOR: create only the admitted user TEMP folder, accept the NEW file-transfer request independently and verify the uploaded exact hash. Leave the existing remote viewer available for Notepad acknowledgements.", flush=True)
        self.p.launch("controller", session_argv("--file-transfer", self.a["host"]["id"]))
        window = until("file-transfer window after independent Accept", lambda: next(iter(self.p.windows("controller", "File Transfer.*Mixel-Remote$")), None), timeout=180)
        self.p.activate("controller", window)
        self.gui(["xdotool", "windowsize", window, "1300", "740", "windowmove", window, "0", "40"])
        self.run(["mkdir", "-p", "/home/guest/s", "/home/guest/r"])
        self.run(["cp", "/payload/" + FILE, "/home/guest/s/" + FILE])
        self.p.path(130, "/home/guest/s")
        self.p.path(616, self.a["host"]["syntheticFolder"])
        self.p.click("controller", *self.file_row("source", window, "upload-source"))
        self.p.click("controller", 424, 232)
        self.file_row("remote", window, "upload-remote")
        self.p.path(130, "/home/guest/r")
        self.p.click("controller", *self.file_row("remote", window, "download-source"))
        self.p.click("controller", 575, 232)
        expected = sha((self.payload / FILE).read_bytes())
        until("downloaded actual synthetic bytes", lambda: self.run(["sha256sum", "/home/guest/r/" + FILE], check=False).stdout.split()[:1] == [expected], timeout=60)
        command(["docker", "cp", self.name + ":/home/guest/r/" + FILE, str(self.output / "roundtrip" / FILE)])
        need((self.output / "roundtrip" / FILE).read_bytes() == (self.payload / FILE).read_bytes(), "roundtrip-actual-byte-equality")
        self.screen("file-roundtrip")
        self.phases.append({"phase": "file-roundtrip", "bytes": (self.payload / FILE).stat().st_size, "sha256": expected,
                            "independentWindowsUploadedFileHashPending": True})
        self.gui(["xdotool", "windowclose", window])

    def cleanup(self):
        outcomes = []
        owned = False
        if self.created:
            try:
                self.ownership(timeout=5)
                owned = True
            except Exception as error:
                outcomes.append("FAILED-container-ownership:" + type(error).__name__)
            if owned:
                try:
                    self.alive(timeout=2)
                except Exception as error:
                    outcomes.append("FAILED-final-app-status:" + type(error).__name__)
                # A failed evidence copy must never skip bounded cleanup. Each
                # operation acts only on the exact already-verified fresh name.
                operations = [
                    ("proof-copy", ["docker", "cp", self.name + ":/proofs/.", str(self.output / "controller-desktop")], 10),
                    ("stop", ["docker", "stop", "--time", "8", self.name], 12),
                    ("remove", ["docker", "rm", self.name], 5),
                ]
                completed = {}
                for label, argv, timeout in operations:
                    try:
                        result = command(argv, check=False, timeout=timeout)
                        completed[label] = result.returncode == 0
                        if not completed[label]:
                            outcomes.append("FAILED-" + label + ":exit-" + str(result.returncode))
                    except Exception as error:
                        completed[label] = False
                        outcomes.append("FAILED-" + label + ":" + type(error).__name__)
                if completed["stop"] and completed["remove"]:
                    outcomes.append("own-container-stopped-and-removed")
        if self.image_id and (owned or not self.created):
            try:
                metadata = json.loads(command(["docker", "image", "inspect", self.image_id], timeout=3).stdout)
                need(len(metadata) == 1 and metadata[0]["Id"] == self.image_id
                     and metadata[0]["Config"]["Labels"].get("com.mixel.qa") == self.name, "own-image-identity")
                result = command(["docker", "image", "rm", self.image_id], check=False, timeout=5)
                outcomes.append("own-image-removed" if result.returncode == 0 else "FAILED-image-cleanup:exit-" + str(result.returncode))
            except Exception as error:
                outcomes.append("FAILED-image-cleanup:" + type(error).__name__)
        return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--admission-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    a = safe_admission(args.admission.read_bytes(), args.admission_sha256)
    private_policy = linux_eligibility(a)
    hosted_guard(a, os.environ, os.uname().sysname)
    root = Path(__file__).resolve().parents[1]
    binding = source_equivalence(root, a)
    need(args.output.is_absolute() and args.output.parent.resolve() == args.output.parent and not args.output.exists(), "fresh-canonical-output")
    args.output.mkdir(mode=0o700)
    parent = os.open(args.output.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    write_json(args.output / "ADMISSION.json", a)
    write_json(args.output / "SOURCE-EQUIVALENCE.json", binding)
    if private_policy is not None:
        raw_policy = a["linuxPolicy"]["json"].encode("utf-8")
        with (args.output / "ROOT-PRIVATE-LINUX-POLICY.json").open("xb") as target:
            os.chmod(target.name, 0o600)
            target.write(raw_policy); target.flush(); os.fsync(target.fileno())
    payload = args.output / "payload"; payload.mkdir(mode=0o700)
    (args.output / "roundtrip").mkdir(mode=0o700)
    (args.output / "controller-desktop").mkdir(mode=0o700)
    actor = None
    success, cleanup = False, []
    def deadline(signum, frame):
        raise TimeoutError("owned-controller-operator-deadline")
    signal.signal(signal.SIGTERM, deadline)
    signal.signal(signal.SIGINT, deadline)
    try:
        provider = Provider(args.output, a)
        prefix = "repos/" + REPOSITORY
        run = json.loads(provider.fetch(prefix + "/actions/runs/" + str(PRODUCER)).read_bytes())
        job = json.loads(provider.fetch(prefix + "/actions/jobs/" + str(a["linuxShippingJob"])).read_bytes())
        artifact = json.loads(provider.fetch(prefix + "/actions/artifacts/" + str(a["artifact"]["id"])).read_bytes())
        if private_policy is None:
            package_metadata(a, run, job, artifact)
        else:
            private_gate = private_package_metadata(a, run, job, artifact, private_policy)
            write_json(args.output / "PRIVATE-LINUX-PER-JOB-GATE.json", private_gate)
        archive = provider.fetch(prefix + "/actions/artifacts/" + str(a["artifact"]["id"]) + "/zip", archive=True)
        name, deb = package_bytes(archive, a)
        (payload / "client.deb").write_bytes(deb)
        data = b"MIXEL_PC01_SYNTHETIC_FILE_ONLY\n" + bytes(range(256)) * 256
        (payload / FILE).write_bytes(data)
        write_json(args.output / "LINUX-INPUT.json", {"artifact": artifact, "member": name, "debSHA256": sha(deb),
                   "hostedArchiveGETs": 1, "RootLocalArchiveGETsRepeated": False})
        primitives = extracted_primitives(root, payload)
        expected = {name: sha((payload / name).read_bytes()) for name in ("client.deb", FILE, "ipc_probe.py", "controller-primitives.py")}
        need(expected["client.deb"] == a["artifact"]["debSHA256"], "qualified-DEB-before-projection")
        projection = project_payload(payload, expected)
        write_json(args.output / "PAYLOAD-PROJECTION.json", projection)
        actor = Controller(primitives, root, args.output, a, payload, projection)
        actor.setup()
        print("OPERATOR synthetic file: bytes=" + str(len(data)) + " sha256=" + sha(data), flush=True)
        window = actor.connect("first")
        actor.input_and_clipboard(window)
        actor.files()
        actor.p.activate("controller", window)
        actor.marker("mixeldisconnectready" + a["nonce"], "ready-for-disconnect")
        print("OPERATOR: NOW click actual PC01 Disconnect and retain its same-host auth0 observation. In20s the driver normally closes only its own viewer. The separate Root receipt must verify your actual click and auth0; controller pixels do not prove them.", flush=True)
        time.sleep(20)
        actor.screen("after-operator-disconnect")
        actor.gui(["xdotool", "windowclose", window])
        actor.connect("reconnect")
        actor.gui(["xdotool", "key", "ctrl+a"])
        actor.gui(["xdotool", "type", "--clearmodifiers", "mixelreconnect" + a["nonce"]])
        until("new actual reconnect input frame", lambda: ocr_has(actor.screen("reconnect-input"), "mixelreconnect" + a["nonce"]), timeout=30)
        actor.marker("mixelfinishready" + a["nonce"], "ready-for-final-disconnect")
        print("OPERATOR: NOW click actual PC01 Disconnect/auth0 and retain native host identity/hash/clipboard/upload proof. Owned controller cleanup begins in20s; its screenshots do not establish the independent Windows facts.", flush=True)
        time.sleep(20)
        actor.screen("final-disconnect")
        success = True
    except Exception as error:
        write_json(args.output / "FAILED.json", {"type": type(error).__name__, "reason": str(error)[:300], **FLAGS})
        raise
    finally:
        if actor:
            cleanup = actor.cleanup()
        write_json(args.output / "RESULT.json", {"controllerAssertionsCompleted": success and cleanup == ["own-container-stopped-and-removed", "own-image-removed"],
                   "independentWindowsEvidenceJoinPending": True, "normalGUIQuitProven": False,
                   "operatorDisconnectAcknowledgementMachineVerified": False,
                   "phases": actor.phases if actor else [], "cleanup": cleanup,
                   "WindowsAuthOrAcceptInferredFromController": False, "source": APP_SOURCE, **FLAGS})
    need(success and cleanup == ["own-container-stopped-and-removed", "own-image-removed"], "owned-cleanup-must-complete")
    print("PASS: private controller observations retained; independent PC01 evidence join remains pending; no release/native/task qualification", flush=True)


if __name__ == "__main__":
    main()
