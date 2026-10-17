#!/usr/bin/env python3
"""Unpublished fixed-070 normal-DEB QA on a fresh real Ubuntu systemd runner.

The hosted transfer is a new, explicitly counted normal-artifact GET, separate
from retained local archives. This does not qualify a release or restrict the
runner's network like QEMU restrict=on. The repository and CI artifacts may be
public; no application secrets or customer data are used. No GUI is tested.
"""
import argparse
import ast
import contextlib
import io
import tarfile
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import selectors
import shutil
import stat
import subprocess
import tempfile
import time

REPO = "Jangopeople/mixel-remote-client"
REPO_ID = 1229987840
APP = "070da25001f97fabf67d08401e2cb15bde37a33b"
TREE = "4411c8027836673ded7f1b0b22f7bdac47c32242"
RUN, ATTEMPT, JOB, ARTIFACT = 39022433327, 1, 117367740409, 12098846059
ARCHIVE_BYTES = 23831101
ARCHIVE_SHA = "51944fc004743d9a56cddbb6c86266d02f69f2fa83a5a53dd5e7b374a95c1a06"
DEB_SHA = "993f516763f85cd749faff8752e52ad5ac7be40b21e78d5878ec3a40422abc47"
MAIN_SHA = "ec180ea8503f85167949d74498baa1f08f908dd614a5a953ecd0e84cc082a437"
GUEST_SHA = "70008ff22ffd6aa79d46efc24258c998cfb41c0092a27b562b15f71f8d42edb4"
COLLECTOR_SHA = "56ce340dedd4030ec1145b103a09171c2fff7f872ae8c21142239853deb95271"
PACKAGED_UNIT = "d5f9c6da03e3a87b77687d8282b6615c209aea3ea5bca6fbf9fe3df8074c2173"
INSTALLED_UNIT = "5f9370df93f15c416ed7c1cd2803387a28c3baaa90027ed8fbe447056aba2410"
QA_PATHS = frozenset({
    ".github/workflows/linux-installer-systemd.yml", "scripts/linux-installer-systemd.py",
    "scripts/linux-installer-systemd-guest.py", "scripts/test-linux-installer-systemd.py",
    ".github/workflows/preflight.yml", "HANDOFF.md",
    "scripts/invoke-hosted-standard-qa-windows.ps1", "scripts/test-session-directory-access.py",
    "scripts/test-standard-valid-qa.py", "scripts/test-support-directory-security.py",
    "scripts/test-support-lease-standard-windows.ps1", "scripts/test-support-lease.py",
    "scripts/test-windows-lease-observation.py", "scripts/test-support-lease-ack.py",
    "scripts/test-support-launch-macos-concurrency-controls.py",
    "scripts/test-diagnose-macos-concurrency-failed-intel.py", "scripts/test-release-pipeline.py",
})
DEPS = ("libgtk-3-0", "libegl1", "libgl1", "libgles2", "libgl1-mesa-dri",
        "libxcb-randr0", "libxdo3", "libxfixes3", "libxcb-shape0", "libxcb-xfixes0",
        "libasound2", "libsystemd0", "curl", "libva2", "libva-drm2", "libva-x11-2",
        "libgstreamer-plugins-base1.0-0", "libpam0g", "gstreamer1.0-pipewire",
        "libayatana-appindicator3-1")
BASE = Path(__file__).resolve().parents[1]


def require(ok, why):
    if not ok:
        raise ValueError(why)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with path.open("x", encoding="utf-8") as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(value, f, indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def root_path(value):
    require(not any(c in value for c in "\n\r\x00"), "invalid-output-path")
    p = Path(value)
    require(p.is_absolute() and ".." not in p.parts and "worktrees" not in p.parts
            and str(p.resolve()) == value, "noncanonical-output-path")
    run, attempt = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", "")
    require(re.fullmatch(r"[1-9][0-9]*", run) and re.fullmatch(r"[1-9][0-9]*", attempt), "invalid-QA-run")
    require(p.parent == Path(os.environ["RUNNER_TEMP"]).resolve()
            and p.name == f"mixel-linux-installer-systemd-{run}-{attempt}", "unowned-output-path")
    return p


def command(root, label, argv, timeout=120, env=None, allowed=(0,)):
    require(re.fullmatch(r"[a-z0-9-]+", label), "unsafe-command-label")
    folder = root / "evidence" / label
    folder.mkdir(mode=0o700)
    save(folder / "COMMAND.json", {"argv": argv, "timeoutSeconds": timeout,
         "expectedExitCodes": list(allowed), "automaticRetry": False})
    status, error = None, None
    with (folder / "stdout").open("xb") as out, (folder / "stderr").open("xb") as err:
        os.fchmod(out.fileno(), 0o600); os.fchmod(err.fileno(), 0o600)
        try:
            status = subprocess.run(argv, cwd=BASE, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=err, timeout=timeout, env=env).returncode
        except subprocess.TimeoutExpired:
            error = "timeout"
        out.flush(); err.flush(); os.fsync(out.fileno()); os.fsync(err.fileno())
    save(folder / "RESULT.json", {"exit": status, "error": error,
         "stdoutBytes": (folder / "stdout").stat().st_size,
         "stdoutSHA256": sha(folder / "stdout"), "stderrSHA256": sha(folder / "stderr")})
    require(status in allowed and error is None, "command-failed-" + label)
    return (folder / "stdout").read_bytes()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=BASE, timeout=60)


def parse_tree(raw):
    rows = {}
    for row in raw.split(b"\0"):
        if not row:
            continue
        meta, name = row.split(b"\t", 1)
        path = name.decode("utf-8")
        require(path not in rows and not any(c in path for c in "\r\n\x00"), "invalid-tree-path")
        mode, kind, blob = meta.decode().split()
        require(kind == "blob", "unexpected-tracked-object")
        rows[path] = (mode, blob)
    return rows


def compare_trees(original, current):
    changed = sorted(p for p in original.keys() | current.keys() if original.get(p) != current.get(p))
    require(not set(changed) - QA_PATHS, "application-input-drift")
    require(all(p in current for p in original), "tracked-file-deletion")
    return changed


def validate_event(event, name, actual):
    require(re.fullmatch(r"[0-9a-f]{40}", actual or ""), "invalid-QA-SHA")
    require(event.get("repository", {}).get("full_name") == REPO
            and event["repository"].get("id") == REPO_ID, "wrong-QA-repository")
    if name == "pull_request":
        pr = event.get("pull_request", {})
        require(pr.get("head", {}).get("sha") == actual, "wrong-QA-PR-head")
        for side in ("head", "base"):
            repo = pr.get(side, {}).get("repo", {})
            require(repo.get("full_name") == REPO and repo.get("id") == REPO_ID, "fork-QA-refused")
    else:
        require(name == "workflow_dispatch", "unexpected-QA-event")


def collector():
    path = BASE / "scripts/collect-linux-runtime-artifact.py"
    require(sha(path) == COLLECTOR_SHA, "collector-source-drift")
    spec = importlib.util.spec_from_file_location("mixel_linux_pure", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def validate_metadata(run, job, artifact, commit):
    pure = collector()
    require(pure.validate_run(run, RUN) == APP, "wrong-application-source")
    require(run.get("run_attempt") == ATTEMPT and type(run["run_attempt"]) is int
            and run.get("workflow_id") == 271445168
            and run.get("repository", {}).get("id") == REPO_ID
            and run.get("head_commit", {}).get("id") == APP
            and run["head_commit"].get("tree_id") == TREE, "wrong-build-identity")
    require(commit.get("sha") == APP and commit.get("tree", {}).get("sha") == TREE, "wrong-app-Git-object")
    require(all(type(job.get(k)) is int for k in ("id", "run_id", "run_attempt"))
            and job.get("id") == JOB and job.get("run_id") == RUN and job.get("run_attempt") == ATTEMPT
            and job.get("name") == "linux" and job.get("head_sha") == APP
            and job.get("status") == "completed" and job.get("conclusion") == "success", "Linux-job-not-exact-success")
    steps = job.get("steps", [])
    require(len(steps) == 33 and all(s.get("status") == "completed" and s.get("conclusion") == "success" for s in steps), "Linux-step-failure")
    require(pure.select_linux_artifact([artifact], RUN, APP) == artifact, "wrong-artifact")
    require(type(artifact.get("id")) is int and artifact["id"] == ARTIFACT
            and artifact.get("size_in_bytes") == ARCHIVE_BYTES
            and artifact.get("digest") == "sha256:" + ARCHIVE_SHA, "wrong-normal-archive")


def prepare(root):
    require(os.geteuid() != 0, "prepare-must-record-nonroot-runner-owner")
    actual = git("rev-parse", "HEAD").decode().strip()
    require(actual == os.environ.get("EXPECTED_QA_SOURCE"), "executed-QA-checkout-mismatch")
    validate_event(json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()), os.environ["GITHUB_EVENT_NAME"], actual)
    require(git("rev-parse", APP + "^{tree}").decode().strip() == TREE, "local-app-tree-mismatch")
    original = parse_tree(git("ls-tree", "-r", "-z", APP))
    current = parse_tree(git("ls-tree", "-r", "-z", actual))
    changed = compare_trees(original, current)
    require(not git("diff", "--name-only", "HEAD"), "dirty-QA-checkout")
    require(sha(BASE / "scripts/linux-installer-systemd-guest.py") == GUEST_SHA, "guest-source-drift")
    root.mkdir(mode=0o700); (root / "evidence").mkdir(mode=0o700)
    save(root / "evidence/SOURCE.json", {"applicationSource": APP, "applicationTree": TREE,
         "qaSource": actual, "qaTree": git("rev-parse", "HEAD^{tree}").decode().strip(),
         "executedCheckoutCommand": ["git", "rev-parse", "HEAD"], "executedCheckoutStdout": actual,
         "QAOnlyChangedPaths": changed, "allOtherTrackedBlobsAndModesEqual": True,
         "outputOwner": {"uid": os.geteuid(), "gid": os.getegid()},
         "guestSHA256": GUEST_SHA, "runnerImageOS": os.environ.get("ImageOS"),
         "runnerImageVersion": os.environ.get("ImageVersion"), "publication": False,
         "githubActions": os.environ.get("GITHUB_ACTIONS") == "true",
         "runnerEnvironment": os.environ.get("RUNNER_ENVIRONMENT"),
         "nativeQualified": False, "normalReleaseQualified": False, "taskComplete": False,
         "runnerNetworkQEMUIsolated": False})


def host(root):
    require(os.geteuid() == 0 and os.uname().machine == "x86_64", "real-root-amd64-required")
    release = dict(row.split("=", 1) for row in Path("/etc/os-release").read_text().splitlines() if "=" in row)
    require(release["ID"].strip('"') == "ubuntu" and release["VERSION_ID"].strip('"') == "22.04", "wrong-host-OS")
    require(Path("/proc/1/exe").resolve().name == "systemd" and Path("/sys/fs/cgroup/cgroup.controllers").is_file(), "real-PID1-systemd-cgroup2-required")
    require(not Path("/usr/sbin/policy-rc.d").exists(), "policy-rc.d-refused")
    for path in ("/usr/bin/mixel-remote", "/usr/share/mixel-remote", "/usr/lib/systemd/system/mixel-remote.service",
                 "/etc/systemd/system/mixel-remote.service", "/run/systemd/system/mixel-remote.service",
                 "/etc/systemd/system/mixel-remote.service.d", "/run/systemd/system/mixel-remote.service.d",
                 "/usr/lib/systemd/system/mixel-remote.service.d",
                 "/etc/systemd/system/multi-user.target.wants/mixel-remote.service",
                 "/root/.config/mixel-remote", "/var/tmp/mixel-systemd-qa", "/mnt/mixel-candidate",
                 "/var/lib/mixel-systemd-qa-unrelated-sentinel"):
        require(not os.path.lexists(path), "host-not-fresh-" + path)
    r = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Status}", "mixel-remote"], capture_output=True, timeout=30)
    require(r.returncode == 1 and not r.stdout, "existing-Mixel-package")
    save(root / "evidence/HOST-FRESH-PRECONDITIONS.json", {"fresh": True, "actualPID1Systemd": True,
         "root": True, "release": release, "productPathsAbsent": True, "packageAbsent": True})
    require_healthy_systemd(root)
    save(root / "evidence/HOST.json", {"fresh": True, "actualPID1Systemd": True, "root": True, "release": release})


def bounded_diagnostic(root, label, argv, timeout=8, bound=65536):
    """Read-only boot diagnostics; retain bounded raw bytes, never grant health."""
    folder = root / "evidence" / label; folder.mkdir(mode=0o700)
    save(folder / "COMMAND.json", {"argv": argv, "timeoutSeconds": timeout,
         "maxBytesPerStream": bound, "automaticRetry": False, "diagnosticOnly": True})
    data = {"stdout": bytearray(), "stderr": bytearray()}; child = None; error = None
    try:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, cwd=BASE)
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            for stream, name in ((child.stdout, "stdout"), (child.stderr, "stderr")):
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                require(time.monotonic() < deadline, "diagnostic-timeout")
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj); continue
                    output = data[key.data]; remaining = bound - len(output)
                    output.extend(block[:remaining])
                    require(len(block) <= remaining, "diagnostic-output-bound")
        child.wait(timeout=1)
    except Exception as exc:
        error = str(exc)
    finally:
        if child is not None:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=1)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=1)
            child.stdout.close(); child.stderr.close()
    for name, content in data.items():
        with (folder / name).open("xb") as f:
            os.fchmod(f.fileno(), 0o600); f.write(content); f.flush(); os.fsync(f.fileno())
    save(folder / "RESULT.json", {"exit": child.returncode if child else None, "error": error,
         "stdoutBytes": len(data["stdout"]), "stderrBytes": len(data["stderr"]),
         "stdoutSHA256": hashlib.sha256(data["stdout"]).hexdigest(),
         "stderrSHA256": hashlib.sha256(data["stderr"]).hexdigest(), "healthAccepted": False})
    return bytes(data["stdout"])


def failed_unit_names(raw):
    names = []
    pattern = r"(?:[A-Za-z0-9_.@:-]|\\x[0-9A-Fa-f]{2})+\.(?:service|socket|mount|automount|path|timer|target|scope|slice|swap|device)"
    for row in raw.decode("utf-8", errors="replace").splitlines():
        fields = row.split()
        if fields and len(fields[0]) <= 255 and re.fullmatch(pattern, fields[0]) and fields[0] not in names:
            names.append(fields[0])
            if len(names) == 16: break
    return names


def diagnose_host(root):
    failed = bounded_diagnostic(root, "host-failed-units", ["systemctl", "--failed", "--no-pager", "--plain", "--no-legend"])
    bounded_diagnostic(root, "host-pending-jobs", ["systemctl", "list-jobs", "--no-pager", "--no-legend"])
    names = failed_unit_names(failed)
    properties = "Id,ActiveState,SubState,Result,FragmentPath,ExecMainCode,ExecMainStatus"
    for index, name in enumerate(names):
        bounded_diagnostic(root, f"host-failed-unit-{index:02d}", ["systemctl", "show", "--no-pager",
                           "--property=" + properties, "--", name])
    if "mnt.mount" in names:
        bounded_diagnostic(root, "host-mnt-properties", ["systemctl", "show", "--all",
                           "--property=Id,What,Where,Type,SourcePath,Result", "mnt.mount"])
        bounded_diagnostic(root, "host-mnt-journal", ["journalctl", "-b", "-u", "mnt.mount",
                           "--no-pager", "--output=short-iso", "-n", "60"])
        bounded_diagnostic(root, "host-mnt-findmnt", ["findmnt", "--mountpoint", "/mnt",
                           "--json", "--output", "SOURCE,TARGET,FSTYPE"])
    save(root / "evidence/HOST-DIAGNOSTICS.json", {"failedUnits": names, "unitLimit": 16,
         "maxBytesPerCommandStream": 65536, "timeoutSecondsPerCommand": 8,
         "targetedMntDiagnostics": "mnt.mount" in names,
         "readOnly": True, "healthAccepted": False, "candidateExecuted": False})


def require_healthy_systemd(root):
    try:
        require(command(root, "host-systemd", ["systemctl", "is-system-running", "--wait"], 60).strip() == b"running", "unhealthy-systemd")
    except Exception:
        try:
            diagnose_host(root)
            repair_stale_mnt(root)
        except Exception as error:
            # Retain only fixed guard codes, never exception text or fstab/options.
            try:
                allowed_codes = {
                    'mnt-repair-current-unit-identity-mismatch',
                    'mnt-repair-device-or-mounted-target-present',
                    'mnt-repair-diagnostic-bytes-mismatch',
                    'mnt-repair-diagnostic-outcome-mismatch',
                    'mnt-repair-different-mount-failure',
                    'mnt-repair-failed-units-remain',
                    'mnt-repair-foreign-mount',
                    'mnt-repair-foreign-or-invalid-fstab-entry',
                    'mnt-repair-foreign-unit-or-override',
                    'mnt-repair-fstab-after-mismatch',
                    'mnt-repair-fstab-changed-during-read',
                    'mnt-repair-fstab-entry-not-unique',
                    'mnt-repair-fstab-not-owned-regular',
                    'mnt-repair-fstab-recheck-mismatch',
                    'mnt-repair-global-health-not-running',
                    'mnt-repair-host-not-fresh',
                    'mnt-repair-invalid-fstab',
                    'mnt-repair-not-source-bound-hosted-QA',
                    'mnt-repair-other-failed-unit',
                    'mnt-repair-pending-jobs',
                    'mnt-repair-root-required',
                }
                code = error.args[0] if type(error) is ValueError and len(error.args) == 1 else None
                code = code if type(code) is str and code in allowed_codes else "redacted"
                known_types = (ValueError, TypeError, OSError, PermissionError, FileNotFoundError,
                               UnicodeDecodeError, json.JSONDecodeError, subprocess.TimeoutExpired)
                save(root / "evidence/HOST-MNT-REPAIR-FAILED.json", {
                    "exceptionType": type(error).__name__ if type(error) in known_types else "OtherException",
                    "refusalCode": code, "repairCompleted": False, "candidateExecuted": False,
                    "originalHealthFailurePreserved": True})
            except Exception:
                pass  # Even failure to retain this receipt cannot replace original health failure.
        else:
            return  # Repair itself requires global running and zero failed units.
        raise


def diagnostic_evidence(root, label, argv, exitcode):
    folder = root / "evidence" / label
    receipt = json.loads((folder / "RESULT.json").read_text())
    require(json.loads((folder / "COMMAND.json").read_text())["argv"] == argv
            and type(receipt.get("exit")) is int and receipt["exit"] == exitcode and receipt.get("error") is None,
            "mnt-repair-diagnostic-outcome-mismatch")
    output, error = (folder / "stdout").read_bytes(), (folder / "stderr").read_bytes()
    require(len(output) <= 65536 and not error
            and receipt.get("stdoutBytes") == len(output)
            and receipt.get("stdoutSHA256") == hashlib.sha256(output).hexdigest()
            and receipt.get("stderrSHA256") == hashlib.sha256(error).hexdigest(),
            "mnt-repair-diagnostic-bytes-mismatch")
    return output


def stale_mnt_proof(root):
    require(os.geteuid() == 0, "mnt-repair-root-required")
    source = json.loads((root / "evidence/SOURCE.json").read_text())
    require(source.get("githubActions") is True and source.get("runnerEnvironment") == "github-hosted"
            and source.get("runnerImageOS") == "ubuntu22" and source.get("applicationSource") == APP
            and source.get("applicationTree") == TREE and source.get("allOtherTrackedBlobsAndModesEqual") is True
            and re.fullmatch(r"[0-9a-f]{40}", source.get("qaSource", ""))
            and re.fullmatch(r"[0-9a-f]{40}", source.get("qaTree", ""))
            and source.get("executedCheckoutStdout") == source["qaSource"]
            and source.get("guestSHA256") == GUEST_SHA, "mnt-repair-not-source-bound-hosted-QA")
    fresh = json.loads((root / "evidence/HOST-FRESH-PRECONDITIONS.json").read_text())
    require(all(fresh.get(k) is True for k in ("fresh", "actualPID1Systemd", "root", "productPathsAbsent", "packageAbsent"))
            and fresh.get("release", {}).get("ID", "").strip('"') == "ubuntu"
            and fresh["release"].get("VERSION_ID", "").strip('"') == "22.04", "mnt-repair-host-not-fresh")
    failed = diagnostic_evidence(root, "host-failed-units", ["systemctl", "--failed", "--no-pager", "--plain", "--no-legend"], 0)
    rows = [row.split() for row in failed.splitlines() if row.strip()]
    require(len(rows) == 1 and rows[0][:4] == [b"mnt.mount", b"loaded", b"failed", b"failed"], "mnt-repair-other-failed-unit")
    pending = diagnostic_evidence(root, "host-pending-jobs", ["systemctl", "list-jobs", "--no-pager", "--no-legend"], 0)
    require(not pending.strip(), "mnt-repair-pending-jobs")
    raw = diagnostic_evidence(root, "host-mnt-properties", ["systemctl", "show", "--all",
                              "--property=Id,What,Where,Type,SourcePath,Result", "mnt.mount"], 0)
    properties = dict(row.split("=", 1) for row in raw.decode().splitlines())
    require(len(raw.splitlines()) == 6 and properties == {"Id": "mnt.mount", "What": "ephemeral0",
            "Where": "/mnt", "Type": "", "SourcePath": "/etc/fstab", "Result": "exit-code"}, "mnt-repair-foreign-mount")
    journal = diagnostic_evidence(root, "host-mnt-journal", ["journalctl", "-b", "-u", "mnt.mount",
                                 "--no-pager", "--output=short-iso", "-n", "60"], 0)
    require(b"special device ephemeral0 does not exist" in journal
            and b"mnt.mount: Mount process exited, code=exited, status=32" in journal,
            "mnt-repair-different-mount-failure")
    mounted = diagnostic_evidence(root, "host-mnt-findmnt", ["findmnt", "--mountpoint", "/mnt", "--json",
                                 "--output", "SOURCE,TARGET,FSTYPE"], 1)
    require(not mounted and not os.path.lexists("/dev/ephemeral0"), "mnt-repair-device-or-mounted-target-present")
    return {"qaSource": source["qaSource"], "qaTree": source["qaTree"], "unit": "mnt.mount",
            "device": "ephemeral0", "target": "/mnt", "initialFailure": "missing-device-exit32"}


def fstab_snapshot(path):
    identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink, s.st_size, s.st_mtime_ns)
    def owned_regular(info):
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                and 0 < info.st_size <= 65536 and info.st_mode & 0o200
                and not info.st_mode & (0o022 | 0o7111), "mnt-repair-fstab-not-owned-regular")
    preopen = path.lstat()
    owned_regular(preopen)  # Never open a known FIFO/device/socket/symlink.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        owned_regular(before)
        require(identity(before) == identity(preopen), "mnt-repair-fstab-recheck-mismatch")
        data = os.read(fd, 65537)
        after = os.fstat(fd)
        require(len(data) == before.st_size and identity(before) == identity(after), "mnt-repair-fstab-changed-during-read")
        return data, before
    finally:
        os.close(fd)


def commented_stale_entry(data):
    require(b"\x00" not in data, "mnt-repair-invalid-fstab")
    lines = data.splitlines(keepends=True); matches = []
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip(b" \t").startswith(b"#"): continue
        fields = line.split()
        if len(fields) > 1 and fields[1] == b"/mnt":
            require(len(fields) == 6 and fields[:2] == [b"ephemeral0", b"/mnt"]
                    and re.fullmatch(rb"[A-Za-z0-9_.+-]+", fields[2])
                    and re.fullmatch(rb"[0-9]+", fields[4]) and re.fullmatch(rb"[0-9]+", fields[5]),
                    "mnt-repair-foreign-or-invalid-fstab-entry")
            matches.append(index)
    require(len(matches) == 1, "mnt-repair-fstab-entry-not-unique")
    index = matches[0]; original = lines[index]
    lines[index] = b"# Mixel disposable QA stale missing-device entry: " + original
    return b"".join(lines), hashlib.sha256(original).hexdigest()


def comment_stale_fstab(path, backup_root):
    """Same-owner utility; production caller separately requires real root QA."""
    original, before = fstab_snapshot(path)
    replacement, removed_hash = commented_stale_entry(original)
    backup_root.mkdir(mode=0o700)
    backup = backup_root / "fstab.original"
    with backup.open("xb") as file:
        os.fchmod(file.fileno(), 0o600); file.write(original); file.flush(); os.fsync(file.fileno())
    for directory in (backup_root, backup_root.parent):
        fd = os.open(directory, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    current, info = fstab_snapshot(path)
    identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_size, s.st_mtime_ns)
    require(current == original and identity(info) == identity(before), "mnt-repair-fstab-recheck-mismatch")
    fd, temporary = tempfile.mkstemp(prefix=".mixel-qa-fstab-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            os.fchown(file.fileno(), before.st_uid, before.st_gid)
            os.fchmod(file.fileno(), stat.S_IMODE(before.st_mode))
            file.write(replacement); file.flush(); os.fsync(file.fileno())
        # Recheck immediately before replacing this exact original inode/bytes.
        current, info = fstab_snapshot(path)
        require(current == original and identity(info) == identity(before), "mnt-repair-fstab-recheck-mismatch")
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.lexists(temporary): os.unlink(temporary)
    after, after_info = fstab_snapshot(path)
    require(after == replacement and (after_info.st_mode, after_info.st_uid, after_info.st_gid)
            == (before.st_mode, before.st_uid, before.st_gid), "mnt-repair-fstab-after-mismatch")
    return {"beforeSHA256": hashlib.sha256(original).hexdigest(), "afterSHA256": hashlib.sha256(after).hexdigest(),
            "removedLineSHA256": removed_hash, "privateBackup": "private-host-backup/fstab.original",
            "mode": stat.S_IMODE(before.st_mode), "uid": before.st_uid, "gid": before.st_gid,
            "allOtherBytesPreserved": True}


def confirm_mnt_identity(root):
    for prefix in ("/etc", "/run", "/usr/lib"):
        unit = prefix + "/systemd/system/mnt.mount"
        require(not os.path.lexists(unit) and not os.path.lexists(unit + ".d"), "mnt-repair-foreign-unit-or-override")
    argv = ["systemctl", "show", "--all",
            "--property=Id,LoadState,FragmentPath,DropInPaths,ActiveState,SubState,What,Where,SourcePath,Result", "mnt.mount"]
    bounded_diagnostic(root, "host-mnt-repair-identity", argv)
    raw = diagnostic_evidence(root, "host-mnt-repair-identity", argv, 0)
    properties = dict(row.split("=", 1) for row in raw.decode().splitlines())
    require(len(raw.splitlines()) == 10 and properties == {"Id": "mnt.mount", "LoadState": "loaded",
            "FragmentPath": "/run/systemd/generator/mnt.mount", "DropInPaths": "", "ActiveState": "failed",
            "SubState": "failed", "What": "ephemeral0", "Where": "/mnt", "SourcePath": "/etc/fstab",
            "Result": "exit-code"}, "mnt-repair-current-unit-identity-mismatch")


def repair_stale_mnt(root):
    proof = stale_mnt_proof(root)
    save(root / "evidence/HOST-MNT-REPAIR-INTENT.json", proof)
    confirm_mnt_identity(root)  # Last bounded observation before the fstab CAS/write.
    changed = comment_stale_fstab(Path("/etc/fstab"), root / "private-host-backup")
    save(root / "evidence/HOST-MNT-FSTAB-CHANGE.json", changed)  # Hashes only; never fstab/options.
    command(root, "host-mnt-repair-reload", ["systemctl", "daemon-reload"], 30)
    command(root, "host-mnt-repair-reset", ["systemctl", "reset-failed", "mnt.mount"], 30)
    require(command(root, "host-mnt-repair-health", ["systemctl", "is-system-running", "--wait"], 60).strip() == b"running", "mnt-repair-global-health-not-running")
    require(not command(root, "host-mnt-repair-failed-units", ["systemctl", "--failed", "--no-pager", "--plain", "--no-legend"], 30).strip(), "mnt-repair-failed-units-remain")
    save(root / "evidence/HOST-MNT-REPAIR.json", {"underlyingStaleEntryCommented": True,
         "actualGlobalRunning": True, "actualFailedUnitsEmpty": True, "mountOperations": 0,
         "genericResetFailed": False, "candidateExecuted": False, "serviceAccepted": False})


def api(root, label, endpoint):
    raw = command(root, label, ["gh", "api", "--hostname", "github.com", endpoint], 60)
    require(len(raw) <= 2 * 1024 * 1024, "metadata-size-bound")
    return json.loads(raw)


def archive_once(root):
    intent = root / "evidence/NORMAL-ARCHIVE-GET-INTENT.json"
    save(intent, {"artifactId": ARTIFACT, "endpoint": f"repos/{REPO}/actions/artifacts/{ARTIFACT}/zip",
         "normalArtifactGETCount": 1, "localCacheGETCount": 0, "automaticRetry": False})
    output = root / "original.zip"; deadline = time.monotonic() + 120; count = 0
    with output.open("xb") as out, (root / "evidence/archive.stderr").open("xb") as err:
        os.fchmod(out.fileno(), 0o600); os.fchmod(err.fileno(), 0o600)
        child = subprocess.Popen(["gh", "api", "--hostname", "github.com", intent_endpoint()], stdout=subprocess.PIPE, stderr=err)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    require(time.monotonic() < deadline, "archive-timeout")
                    if not selector.select(0.2):
                        continue
                    chunk = os.read(child.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    count += len(chunk); require(count <= ARCHIVE_BYTES, "archive-exceeds-exact-size")
                    out.write(chunk)
            require(child.wait(timeout=5) == 0, "archive-provider-failure")
        finally:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
            child.stdout.close()
            out.flush(); os.fsync(out.fileno())
    require(output.stat().st_size == ARCHIVE_BYTES and sha(output) == ARCHIVE_SHA, "archive-byte-mismatch")
    return output


def intent_endpoint():
    return f"repos/{REPO}/actions/artifacts/{ARTIFACT}/zip"


def collect(root):
    require(json.loads((root / "evidence/HOST.json").read_text()).get("fresh") is True, "missing-host-preflight")
    run = api(root, "build-run", f"repos/{REPO}/actions/runs/{RUN}/attempts/{ATTEMPT}")
    job = api(root, "linux-job", f"repos/{REPO}/actions/jobs/{JOB}")
    artifact = api(root, "normal-artifact", f"repos/{REPO}/actions/artifacts/{ARTIFACT}")
    commit = api(root, "application-commit", f"repos/{REPO}/git/commits/{APP}")
    validate_metadata(run, job, artifact, commit)
    archive = archive_once(root)
    name, package, digest = collector().read_linux_package(archive, "sha256:" + ARCHIVE_SHA)
    require(hashlib.sha256(package).hexdigest() == DEB_SHA, "wrong-inner-DEB")
    with (root / "candidate.deb").open("xb") as f: f.write(package)
    (root / "candidate.deb").chmod(0o400)
    save(root / "evidence/COLLECTION.json", {"applicationSource": APP, "applicationTree": TREE,
         "run": RUN, "attempt": ATTEMPT, "job": JOB, "artifactId": ARTIFACT, "archiveSHA256": ARCHIVE_SHA,
         "archiveBytes": ARCHIVE_BYTES, "DEBSHA256": DEB_SHA, "originalName": name,
         "actualArchiveDigest": digest, "hostedNormalArtifactGETCount": 1, "localNormalArchiveGETs": 0,
         "publication": False, "normalReleaseQualified": False, "taskComplete": False})


def validate_apt_plan(text):
    require(not re.search(r"(?m)^(Remv|Purg) ", text), "dependency-removal-refused")
    rows = [row for row in text.splitlines() if row.startswith("Inst ")]
    for row in rows:
        match = re.match(r"^Inst (\S+)(?: \[([^\]]+)\])? \((\S+)", row)
        require(match is not None, "unparsed-dependency-change")
        name, old, new = match.groups()
        if old:
            r = subprocess.run(["dpkg", "--compare-versions", new, "lt", old], timeout=10)
            require(r.returncode == 1, "dependency-downgrade-or-invalid-version-" + name)


def official_apt_context(root, parent=Path("/var/tmp")):
    context = parent / (root.name + "-official-apt")
    context.mkdir(mode=0o755); context.chmod(0o755)
    sources = context / "ubuntu.list"
    signed = "deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] "
    sources.write_text(signed + "https://archive.ubuntu.com/ubuntu jammy main restricted universe multiverse\n" +
                       signed + "https://archive.ubuntu.com/ubuntu jammy-updates main restricted universe multiverse\n" +
                       signed + "https://security.ubuntu.com/ubuntu jammy-security main restricted universe multiverse\n")
    sources.chmod(0o644)  # Public signed repository configuration, readable by _apt.
    (context / "lists/partial").mkdir(parents=True)
    (context / "lists").chmod(0o755)
    sandbox = pwd.getpwnam("_apt")
    os.chown(context / "lists/partial", sandbox.pw_uid, sandbox.pw_gid)
    (context / "lists/partial").chmod(0o700)
    return context, sources


def dependencies(root):
    require(os.geteuid() == 0 and (root / "evidence/COLLECTION.json").is_file(), "uncollected-dependencies-request")
    context, sources = official_apt_context(root)
    apt = ["apt-get", "-o", "Dir::Etc::sourcelist=" + str(sources), "-o", "Dir::Etc::sourceparts=-",
           "-o", "Dir::State::lists=" + str(context / "lists"), "-o", "Acquire::AllowInsecureRepositories=false",
           "-o", "APT::Get::AllowUnauthenticated=false", "-o", "Debug::Acquire::gpgv=true",
           "-o", "APT::Update::Error-Mode=any"]
    env = dict(os.environ, LC_ALL="C", DEBIAN_FRONTEND="noninteractive")
    command(root, "official-apt-update", apt + ["update"], 300, env)
    auth = root / "evidence/official-apt-authentication"; auth.mkdir()
    shutil.copyfile(sources, auth / "ubuntu.list")
    releases = list((context / "lists").glob("*InRelease"))
    require(len(releases) == 3 and all(p.is_file() and not p.is_symlink() for p in releases), "missing-official-signed-releases")
    for path in releases:
        shutil.copyfile(path, auth / path.name)
    plan = command(root, "official-dependency-plan", apt + ["--simulate", "--no-remove", "--no-install-recommends", "install", *DEPS], 120, env).decode()
    validate_apt_plan(plan)
    command(root, "official-dependency-download", apt + ["--yes", "--download-only", "--no-remove", "--no-install-recommends", "install", *DEPS], 300, env)
    command(root, "official-dependency-install", apt + ["--yes", "--no-download", "--no-remove", "--no-install-recommends", "install", *DEPS], 300, env)
    command(root, "official-dependency-versions", ["dpkg-query", "-W", "-f=${Package} ${Version} ${Status}\n", *DEPS])
    require(command(root, "dependency-audit", ["dpkg", "--audit"]).strip() == b"", "dependency-audit-findings")
    save(root / "evidence/DEPENDENCIES.json", {"officialUbuntuOnly": True, "packages": DEPS, "noForceDepends": True, "noAuthenticationBypass": True})


def run_guest(root):
    require(os.geteuid() == 0 and (root / "evidence/DEPENDENCIES.json").is_file(), "dependencies-not-ready")
    require(sha(root / "candidate.deb") == DEB_SHA and sha(BASE / "scripts/linux-installer-systemd-guest.py") == GUEST_SHA, "execution-bytes-differ")
    stage = Path("/mnt/mixel-candidate"); stage.mkdir(mode=0o700)
    shutil.copyfile(root / "candidate.deb", stage / "candidate.deb"); (stage / "candidate.deb").chmod(0o400)
    save(stage / "expected.json", {"DEBSHA256": DEB_SHA, "mainSHA256": MAIN_SHA,
         "packagedUnitSHA256": PACKAGED_UNIT, "installedUnitSHA256": INSTALLED_UNIT, "instance": root.name})
    save(root / "evidence/CANDIDATE-EXECUTION-INTENT.json", execution_intent(root))
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive"}
    command(root, "real-systemd-lifecycle", ["timeout", "--signal=TERM", "--kill-after=30s", "15m", "python3", "-u", str(BASE / "scripts/linux-installer-systemd-guest.py")], 960, env)
    result = json.loads(Path("/var/tmp/mixel-systemd-qa/RESULT.json").read_text())
    require(result.get("status") == "PASS-real-systemd-installer-service-lifecycle" and result.get("DEBSHA256") == DEB_SHA, "missing-real-service-PASS")


def evidence_owner(root):
    evidence = evidence_directory(root)
    source = root / "evidence/SOURCE.json"
    require(not source.is_symlink() and source.is_file() and source.stat().st_nlink == 1, "unowned-evidence-source")
    owner = json.loads(source.read_text())["outputOwner"]
    require(type(owner.get("uid")) is int and owner["uid"] > 0
            and type(owner.get("gid")) is int and owner["gid"] >= 0, "invalid-evidence-owner")
    directory, receipt, evidence_stat = root.stat(), source.stat(), evidence.stat()
    require((directory.st_uid, directory.st_gid) == (owner["uid"], owner["gid"])
            and (receipt.st_uid, receipt.st_gid) == (owner["uid"], owner["gid"])
            and (evidence_stat.st_uid, evidence_stat.st_gid) == (owner["uid"], owner["gid"])
            and directory.st_mode & 0o777 == evidence_stat.st_mode & 0o777 == 0o700
            and receipt.st_mode & 0o777 == 0o600,
            "evidence-owner-mode-binding-mismatch")
    return owner


def evidence_directory(root):
    evidence = root / "evidence"
    require(not root.is_symlink() and root.is_dir() and root.resolve() == root
            and not evidence.is_symlink() and evidence.is_dir()
            and evidence.resolve() == root / "evidence", "unowned-evidence-directory")
    return evidence


def publish_evidence(root, owner):
    # Only the newly prepared evidence tree; no package, parent or shared path.
    evidence = evidence_directory(root)
    require(evidence_owner(root) == owner, "evidence-owner-changed-during-phase")
    entries = []
    for parent, dirs, files in os.walk(evidence, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(parent) / name
            require(not path.is_symlink(), "unexpected-evidence-symlink")
            mode = path.stat()
            require(stat.S_ISDIR(mode.st_mode) or (stat.S_ISREG(mode.st_mode) and mode.st_nlink == 1), "nonexclusive-evidence-file")
            entries.append((path, stat.S_ISDIR(mode.st_mode)))
    for path, directory in entries:
        os.chown(path, owner["uid"], owner["gid"])
        path.chmod(0o700 if directory else 0o600)
    os.chown(evidence, owner["uid"], owner["gid"]); evidence.chmod(0o700)


def retain_guest_evidence(root):
    source = Path("/var/tmp/mixel-systemd-qa")
    if source.is_dir():
        shutil.copytree(source, root / "evidence/service-lifecycle")
    command(root, "final-service-journal", ["journalctl", "-b", "-u", "mixel-remote.service", "--no-pager"], 60)


def execution_intent(root):
    return {"DEBSHA256": DEB_SHA, "guestSHA256": GUEST_SHA, "networkQEMUIsolated": False,
            "instance": root.name, "applicationSource": APP}


def cleanup_binding(root):
    host_data = json.loads((root / "evidence/HOST.json").read_text())
    require(all(host_data.get(k) is True for k in ("fresh", "actualPID1Systemd", "root")), "cleanup-requires-fresh-host-proof")
    require(json.loads((root / "evidence/CANDIDATE-EXECUTION-INTENT.json").read_text()) == execution_intent(root), "cleanup-intent-not-owned")
    stage = Path("/mnt/mixel-candidate")
    require(not stage.is_symlink() and stage.is_dir() and stage.resolve() == stage
            and stage.stat().st_uid == 0, "cleanup-staging-not-owned")
    for name in ("candidate.deb", "expected.json"):
        path = stage / name; info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 0, "cleanup-staged-file-not-exclusive")
    require(sha(stage / "candidate.deb") == DEB_SHA, "cleanup-candidate-hash-mismatch")
    require(json.loads((stage / "expected.json").read_text()) == {
        "DEBSHA256": DEB_SHA, "mainSHA256": MAIN_SHA, "packagedUnitSHA256": PACKAGED_UNIT,
        "installedUnitSHA256": INSTALLED_UNIT, "instance": root.name}, "cleanup-staged-instance-mismatch")


def package_state(root, label):
    raw = command(root, label, ["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${Architecture}\t${db:Status-Status}\n", "mixel-remote"], 30, allowed=(0, 1))
    outcome = json.loads((root / "evidence" / label / "RESULT.json").read_text())
    require(len(raw) <= 4096, "cleanup-package-query-bound")
    if outcome["exit"] == 1:
        require(not raw, "cleanup-ambiguous-package-absence")
        return None
    rows = raw.decode().splitlines()
    require(len(rows) == 1 and len(rows[0].split("\t")) == 4, "cleanup-invalid-package-identity")
    name, version, architecture, state = rows[0].split("\t")
    require(name in ("mixel-remote", "mixel-remote:amd64") and version == "1.4.6"
            and architecture == "amd64" and state in ("installed", "unpacked", "config-files"), "cleanup-foreign-package-or-state")
    return state


def process_birth(pid):
    try: return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
    except FileNotFoundError: return None


def service_cgroup_empty():
    path = Path("/sys/fs/cgroup/system.slice/mixel-remote.service/cgroup.procs")
    return not path.exists() or not path.read_text().strip()


def inspect_service_process(pid):
    process = Path(f"/proc/{pid}"); executable = process / "exe"
    require(process.stat().st_uid == 0 and os.readlink(executable) in (
            "/usr/share/mixel-remote/mixel-remote", "/usr/share/mixel-remote/mixel-remote (deleted)")
            and sha(executable) == MAIN_SHA, "cleanup-foreign-service-process")
    members = Path("/sys/fs/cgroup/system.slice/mixel-remote.service/cgroup.procs").read_text().split()
    require(str(pid) in members, "cleanup-main-not-in-service-cgroup")
    birth = process_birth(pid); require(birth is not None, "cleanup-owner-exited-during-inspection")
    return {"pid": pid, "birth": birth}


def inspect_cleanup_service(root, label):
    fields = "Id,LoadState,FragmentPath,MainPID,ControlGroup,ActiveState,SubState,DropInPaths"
    raw = command(root, label, ["systemctl", "show", "--all", "mixel-remote.service", "--property=" + fields], 30, allowed=(0, 1, 4))
    rows = raw.decode().splitlines(); require(len(rows) == 8 and all("=" in r for r in rows), "cleanup-unknown-unit")
    unit = dict(row.split("=", 1) for row in rows)
    require(set(unit) == set(fields.split(",")) and unit["Id"] == "mixel-remote.service"
            and unit["MainPID"].isdigit() and unit["DropInPaths"] == "", "cleanup-invalid-unit-identity-or-dropin")
    pid = int(unit["MainPID"])
    if unit["LoadState"] == "not-found":
        require(pid == 0 and not unit["FragmentPath"] and not unit["ControlGroup"]
                and unit["ActiveState"] == "inactive" and unit["SubState"] == "dead"
                and service_cgroup_empty(), "cleanup-unknown-residual-service")
        return None
    fragment = Path(unit["FragmentPath"])
    require(unit["LoadState"] == "loaded" and os.path.realpath(fragment) == os.path.realpath("/usr/lib/systemd/system/mixel-remote.service")
            and not fragment.is_symlink() and fragment.is_file() and sha(fragment) == INSTALLED_UNIT,
            "cleanup-foreign-unit")
    if pid:
        require(pid > 1 and unit["ControlGroup"] == "/system.slice/mixel-remote.service", "cleanup-foreign-service-cgroup")
        return inspect_service_process(pid)
    require(service_cgroup_empty(), "cleanup-orphan-service-children")
    return None


def require_cleanup_absence(root):
    require(inspect_cleanup_service(root, "cleanup-final-unit") is None and service_cgroup_empty(), "cleanup-service-still-owned")
    for path in ("/usr/bin/mixel-remote", "/usr/share/mixel-remote", "/usr/lib/systemd/system/mixel-remote.service",
                 "/etc/systemd/system/mixel-remote.service", "/etc/systemd/system/multi-user.target.wants/mixel-remote.service",
                 "/root/.config/mixel-remote"):
        require(not os.path.lexists(path), "cleanup-residual-owned-path-" + path)


def validate_purge_plan(raw):
    actions = []
    for row in raw.decode().splitlines():
        if row.startswith(("Inst ", "Conf ", "Remv ", "Purg ")):
            fields = row.split()
            require(fields[0] in ("Remv", "Purg") and len(fields) > 1
                    and fields[1] in ("mixel-remote", "mixel-remote:amd64"), "cleanup-purge-would-change-other-package")
            actions.append(fields[1])
    require(actions, "cleanup-purge-plan-missing-owned-package")


def verify_cleanup_payload_if_present():
    main = Path("/usr/share/mixel-remote/mixel-remote")
    if os.path.lexists(main):
        require(not main.is_symlink() and main.is_file() and main.stat().st_uid == 0
                and sha(main) == MAIN_SHA, "cleanup-refuses-other-payload")


def cleanup_package(root):
    cleanup_binding(root)
    state = package_state(root, "cleanup-package-before")
    owner = inspect_cleanup_service(root, "cleanup-unit-before")
    if state is None:
        require(owner is None, "cleanup-unregistered-active-package")
        require_cleanup_absence(root)
        return {"packageInitially": "absent", "packageAndOwnedPathsAbsent": True}
    verify_cleanup_payload_if_present()
    if owner is not None:
        command(root, "cleanup-owned-service", ["systemctl", "stop", "mixel-remote.service"], 60)
        require(process_birth(owner["pid"]) != owner["birth"] and service_cgroup_empty(), "cleanup-owned-process-not-settled")
    plan = command(root, "cleanup-purge-plan", ["apt-get", "--simulate", "--no-download", "--no-auto-remove", "purge", "mixel-remote"], 60)
    validate_purge_plan(plan)
    command(root, "cleanup-owned-package", ["apt-get", "--yes", "--no-download", "--no-auto-remove", "purge", "mixel-remote"], 120)
    command(root, "cleanup-unit-reload", ["systemctl", "daemon-reload"], 30)
    require(package_state(root, "cleanup-package-after") is None, "cleanup-package-still-present")
    require_cleanup_absence(root)
    return {"packageInitially": state, "packageAndOwnedPathsAbsent": True}


def cleanup(root):
    evidence = root / "evidence"
    if (evidence / "CANDIDATE-EXECUTION-INTENT.json").is_file():
        first_error = None
        try: retain_guest_evidence(root)
        except Exception as error: first_error = error
        try: outcome = cleanup_package(root)
        except Exception as error:
            save(evidence / "CLEANUP-FAILED.json", {"retentionError": str(first_error) if first_error else None,
                 "cleanupError": str(error), "cleanupVerified": False, "taskComplete": False})
            if first_error is not None: raise first_error
            raise
        if first_error is not None: raise first_error
        save(evidence / "CLEANUP.json", {"scope": "Only fresh-run Mixel package; original failure stays failure",
             "outcome": outcome, "cleanupVerified": True, "taskComplete": False})



BASELINE_DATA_INVENTORY_SHA = '758f8ef55bdcfcf3959c42e79f57c4e9b13f0b6eb5d8797a3b23f38d0ed4ce26'
BASELINE_CONTROL_INVENTORY_SHA = 'ef1f9b0c32083d70d2751b16e695da2b1970a8f9b3fc8a1e8cef2db085950e3d'
DERIVED_CONTROL_INVENTORY_SHA = 'e892efb62c6afe7582de74a954e407ed0552bbd4807ad982d1bb23a23f1f2b44'
# This private route has three separate identities: 070 binaries, H packaging,
# and the exact isolated QA checkout. It never changes the normal collector gate.
PACKAGING_SOURCE = "73e7c3decf800ba5db5867c135bff5ef42080b83"
PACKAGING_TREE = "010d963e96f84377ba573a249e74124e666529f6"
PACKAGING_FILES = {
    "scripts/apply-branding.sh": ("100755", "37c9b9762cebc0771488f3d7330e78a87f683c128b476989b97b569e076761fa"),
    "scripts/patch-debian-service-lifecycle.py": ("100644", "d28823e63c3070a0d92f4bd1a9c37f38d4c24ba3f7cfa5e0d7c2f3ff2428d196"),
    "scripts/test-debian-service-lifecycle.py": ("100644", "3b562d5674ef5d9b3f0ed28cb30301b43cb1ba82ade89019250183a69d0a50f2"),
}
PRIVATE_QA_PATHS = frozenset((".github/workflows/linux-installer-systemd.yml",
    "scripts/linux-installer-systemd.py", "scripts/linux-installer-systemd-guest.py",
    "scripts/test-linux-installer-systemd.py"))
DERIVED_SHA = "0666ca6f87008654c8e06d17a9555ea6e207d4fec3491b10abee076308a9bd58"
DERIVED_BYTES = 24576176
NEW_CONTROLS = {
    "preinst": "b4464d9b45e73e2fb29706007e9fe4c9eb7b78a70fdc23f561017539ebb4cba2",
    "prerm": "afddb4b05e9c6008ed8a6a16c603cf6ba9ffa557f6492a16da76ab2c3abe854e",
    "postinst": "b0e203772a832ec4cfcbd0e45259f2e873661350737acb29c2d8a60279d147c0",
}


def packaging_blobs():
    require(git("rev-parse", PACKAGING_SOURCE + "^{tree}").decode().strip() == PACKAGING_TREE,
            "packaging-tree-mismatch")
    rows = parse_tree(git("ls-tree", "-r", "-z", PACKAGING_SOURCE, "--", *PACKAGING_FILES))
    require(set(rows) == set(PACKAGING_FILES), "packaging-file-inventory")
    blobs = {}
    for path, (mode, digest) in PACKAGING_FILES.items():
        require(rows[path][0] == mode, "packaging-file-mode")
        raw = git("show", PACKAGING_SOURCE + ":" + path)
        require(0 < len(raw) <= 131072 and hashlib.sha256(raw).hexdigest() == digest,
                "packaging-file-source")
        blobs[path] = raw
    return blobs


def packaging_transform(blobs):
    raw = blobs["scripts/patch-debian-service-lifecycle.py"]
    require(hashlib.sha256(raw).hexdigest() == PACKAGING_FILES["scripts/patch-debian-service-lifecycle.py"][1],
            "packaging-transform-source")
    tree = ast.parse(raw)
    names = {"ORIGINAL_SHA256", "CURRENT_SHA256", "UNIT_SHA256", "REPLACEMENTS", "logical", "transform"}
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in names)
             or (isinstance(n, ast.Assign) and len(n.targets) == 1
                 and isinstance(n.targets[0], ast.Name) and n.targets[0].id in names)]
    require(len(nodes) == len(names), "packaging-transform-AST-inventory")
    namespace = {"hashlib": hashlib}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "pinned-H-packaging-transform", "exec"), namespace)
    require(namespace["CURRENT_SHA256"] == NEW_CONTROLS and namespace["UNIT_SHA256"] == PACKAGED_UNIT,
            "packaging-transform-profile")
    return namespace["transform"]


def prepare_derived(root):
    actual = git("rev-parse", "HEAD").decode().strip()
    original = parse_tree(git("ls-tree", "-r", "-z", APP))
    current = parse_tree(git("ls-tree", "-r", "-z", actual))
    changed = compare_trees(original, current)
    require(set(changed) == PRIVATE_QA_PATHS, "private-QA-four-file-fence")
    blobs = packaging_blobs()
    packaging_transform(blobs)
    prepare(root)
    save(root / "evidence/PACKAGING-SOURCE.json", {
        "scope": "private-derived-control-only-real-systemd-qa",
        "binarySource": APP, "binaryTree": TREE,
        "packagingSource": PACKAGING_SOURCE, "packagingTree": PACKAGING_TREE,
        "qaSource": actual, "qaTree": git("rev-parse", "HEAD^{tree}").decode().strip(),
        "exactQAChangedPaths": sorted(PRIVATE_QA_PATHS),
        "allOtherTrackedBlobsAndModesEqualBinary070": True,
        "packagingSourceApplicationInputsUnchanged": False,
        "packagingFiles": {p: {"mode": v[0], "sha256": v[1]} for p, v in PACKAGING_FILES.items()},
        "applicationCompilation": False, "wholeThreeQualified": False,
        "normalReleaseQualified": False, "nativeQualified": False, "taskComplete": False})


def private_tar_inventory(raw):
    require(0 < len(raw) <= 192 * 1024 * 1024, "bounded-package-tar")
    records = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for item in archive:
            path = PurePosixPath(item.name)
            require(not path.is_absolute() and ".." not in path.parts and "worktrees" not in path.parts
                    and item.name not in records and not any(c in item.name for c in "\x00\r\n\\"), "safe-package-tar-path")
            require(item.isfile() or item.isdir() or item.issym(), "package-tar-type")
            require(item.uid == item.gid == 0 and item.mode & 0o7000 == 0, "root-owned-package-tar")
            row = {"type": item.type.decode("ascii"), "mode": item.mode, "uid": item.uid,
                   "gid": item.gid, "size": item.size, "link": item.linkname}
            if item.issym():
                target = PurePosixPath(item.linkname)
                require(not target.is_absolute() and ".." not in target.parts and "worktrees" not in target.parts
                        and not any(c in item.linkname for c in "\x00\r\n\\"), "safe-package-link")
            if item.isfile():
                require(item.size <= 128 * 1024 * 1024, "bounded-package-file")
                row["sha256"] = hashlib.sha256(archive.extractfile(item).read()).hexdigest()
            records[item.name] = row
            require(len(records) <= 150, "bounded-package-member-count")
    return records


def private_inventory_digest(value):
    return hashlib.sha256((json.dumps(value, indent=2, sort_keys=True) + "\n").encode()).hexdigest()


def derive_tool(root, label, argv, bound=192 * 1024 * 1024, total_deadline=None):
    require(re.fullmatch(r"derive-[a-z-]+", label) and argv[0] == "dpkg-deb", "private-derive-command")
    require(type(bound) is int and 0 < bound <= 192 * 1024 * 1024, "private-derive-output-limit")
    require(total_deadline is None or time.monotonic() < total_deadline <= time.monotonic() + 300,
            "private-derive-total-deadline")
    folder = root / "evidence" / label; folder.mkdir(mode=0o700)
    save(folder / "COMMAND.json", {"argv": argv, "timeoutSeconds": 180,
         "stdoutLimit": bound, "stderrLimit": 1048576, "automaticRetry": False})
    env = {**os.environ, "SOURCE_DATE_EPOCH": "1792236317", "DPKG_DEB_THREADS_MAX": "1", "LC_ALL": "C"}
    status = None; failure = None; process = None
    with (folder / "stdout").open("xb") as out, (folder / "stderr").open("xb") as err:
        os.fchmod(out.fileno(), 0o600); os.fchmod(err.fileno(), 0o600)
        try:
            process = subprocess.Popen(argv, cwd=BASE, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            with selectors.DefaultSelector() as events:
                events.register(process.stdout, selectors.EVENT_READ, (out, bound))
                events.register(process.stderr, selectors.EVENT_READ, (err, 1048576))
                deadline = min(time.monotonic() + 180, total_deadline) if total_deadline is not None else time.monotonic() + 180
                while events.get_map():
                    require(time.monotonic() < deadline, "private-derive-timeout")
                    for key, _ in events.select(min(0.25, max(0, deadline - time.monotonic()))):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            events.unregister(key.fileobj); continue
                        destination, maximum = key.data
                        require(destination.tell() + len(chunk) <= maximum, "private-derive-stream-bound")
                        destination.write(chunk)
                status = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except BaseException as error:
            failure = type(error).__name__
            if process is not None and process.poll() is None:
                process.kill(); process.wait(timeout=5)
            raise
        finally:
            if process is not None:
                for stream in (process.stdout, process.stderr):
                    if stream is not None: stream.close()
            out.flush(); err.flush(); os.fsync(out.fileno()); os.fsync(err.fileno())
            save(folder / "RESULT.json", {"exit": status, "failure": failure,
                "stdoutSHA256": sha(folder / "stdout"), "stderrSHA256": sha(folder / "stderr")})
    require(status == 0, "private-derive-tool-failed-" + label)
    return (folder / "stdout").read_bytes()


def private_regular(path, digest, size, owner):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == owner
            and info.st_mode & 0o022 == 0 and info.st_size == size and 0 < size <= 33554432,
            "private-package-not-exclusive-regular")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        key = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(key(opened) == key(info), "private-package-preopen-replacement")
        result = hashlib.sha256(); total = 0
        while total < size:
            chunk = os.read(fd, min(1048576, size - total))
            require(bool(chunk), "private-package-short-read")
            result.update(chunk); total += len(chunk)
        require(key(os.fstat(fd)) == key(info) and key(path.lstat()) == key(info)
                and result.hexdigest() == digest, "private-package-bytes-changed")
    finally:
        os.close(fd)


def require_packaging_receipt(root):
    data = json.loads((root / "evidence/PACKAGING-SOURCE.json").read_text())
    require(data.get("scope") == "private-derived-control-only-real-systemd-qa"
            and data.get("binarySource") == APP and data.get("binaryTree") == TREE
            and data.get("packagingSource") == PACKAGING_SOURCE and data.get("packagingTree") == PACKAGING_TREE
            and data.get("exactQAChangedPaths") == sorted(PRIVATE_QA_PATHS), "private-packaging-receipt")
    require(data.get("qaSource") == json.loads((root / "evidence/SOURCE.json").read_text()).get("qaSource"),
            "private-QA-source-join")
    return data


def derive_private(root):
    require(os.geteuid() != 0, "private-derive-must-be-unprivileged")
    require_packaging_receipt(root)
    deadline = time.monotonic() + 300
    collection = json.loads((root / "evidence/COLLECTION.json").read_text())
    require(collection.get("DEBSHA256") == DEB_SHA and collection.get("artifactId") == ARTIFACT,
            "private-baseline-collection")
    candidate = root / "candidate.deb"
    owner = json.loads((root / "evidence/SOURCE.json").read_text())["outputOwner"]["uid"]
    private_regular(candidate, DEB_SHA, 23858480, owner)
    work = root / "private-derive"; work.mkdir(mode=0o700)
    before_data = private_tar_inventory(derive_tool(root, "derive-before-data", ["dpkg-deb", "--fsys-tarfile", str(candidate)], total_deadline=deadline))
    before_control_raw = derive_tool(root, "derive-before-control", ["dpkg-deb", "--ctrl-tarfile", str(candidate)], total_deadline=deadline)
    before_control = private_tar_inventory(before_control_raw)
    require(private_inventory_digest(before_data) == BASELINE_DATA_INVENTORY_SHA
            and private_inventory_digest(before_control) == BASELINE_CONTROL_INVENTORY_SHA,
            "private-baseline-inventory")
    originals = {}
    with tarfile.open(fileobj=io.BytesIO(before_control_raw), mode="r:") as archive:
        for name in NEW_CONTROLS:
            member = archive.getmember("./" + name)
            require(member.isfile() and member.size <= 65536, "private-control-type")
            originals[name] = archive.extractfile(member).read()
    transformed = packaging_transform(packaging_blobs())(originals)
    require(set(transformed) == set(NEW_CONTROLS) and all(
        hashlib.sha256(transformed[n]).hexdigest() == NEW_CONTROLS[n] for n in NEW_CONTROLS), "private-control-source")
    stage = work / "stage"
    derive_tool(root, "derive-extract", ["dpkg-deb", "--raw-extract", str(candidate), str(stage)], 1048576, total_deadline=deadline)
    for name, raw in transformed.items():
        target = stage / "DEBIAN" / name; info = target.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and hashlib.sha256(target.read_bytes()).hexdigest() == before_control["./" + name]["sha256"],
                "private-extracted-control")
        target.write_bytes(raw); target.chmod(before_control["./" + name]["mode"])
    derived = work / "candidate.deb"
    derive_tool(root, "derive-build", ["dpkg-deb", "-Zxz", "-z1", "--root-owner-group", "--build", str(stage), str(derived)], 1048576, total_deadline=deadline)
    after_data = private_tar_inventory(derive_tool(root, "derive-after-data", ["dpkg-deb", "--fsys-tarfile", str(derived)], total_deadline=deadline))
    after_control = private_tar_inventory(derive_tool(root, "derive-after-control", ["dpkg-deb", "--ctrl-tarfile", str(derived)], total_deadline=deadline))
    require(after_data == before_data and len(after_data) == 130
            and private_inventory_digest(after_control) == DERIVED_CONTROL_INVENTORY_SHA,
            "private-derived-inventory")
    digest = sha(derived); size = derived.stat().st_size
    save(root / "evidence/DERIVATION-OBSERVED.json", {
        "baselineDEBSHA256": DEB_SHA, "observedDerivedSHA256": digest, "observedBytes": size,
        "binarySource": APP, "packagingSource": PACKAGING_SOURCE,
        "all130PayloadEntriesUnchanged": True, "controlEntries": 7,
        "newControlSHA256": NEW_CONTROLS, "sourceDateEpoch": 1792236317,
        "serializationMatchesQualified0666": digest == DERIVED_SHA and size == DERIVED_BYTES,
        "packageInstalled": False, "normalReleaseQualified": False, "taskComplete": False})
    require(digest == DERIVED_SHA and size == DERIVED_BYTES, "private-derived-serialization-refusal-before-install")
    baseline = root / "baseline.deb"
    require(not os.path.lexists(baseline), "private-baseline-already-retained")
    candidate.rename(baseline); derived.rename(candidate); candidate.chmod(0o400)
    save(root / "evidence/DERIVATION.json", {
        "scope": "private-derived-control-only-real-systemd-qa", "binarySource": APP,
        "packagingSource": PACKAGING_SOURCE, "baselineDEBSHA256": DEB_SHA,
        "derivedDEBSHA256": DERIVED_SHA, "derivedBytes": DERIVED_BYTES,
        "all130PayloadEntriesUnchanged": True, "controlEntries": 7,
        "newControlSHA256": NEW_CONTROLS, "applicationRecompiled": False,
        "normalReleaseQualified": False, "taskComplete": False})


@contextlib.contextmanager
def derived_context(root):
    global DEB_SHA
    require_packaging_receipt(root)
    data = json.loads((root / "evidence/DERIVATION.json").read_text())
    require(data.get("scope") == "private-derived-control-only-real-systemd-qa"
            and data.get("binarySource") == APP and data.get("packagingSource") == PACKAGING_SOURCE
            and data.get("baselineDEBSHA256") == DEB_SHA and data.get("derivedDEBSHA256") == DERIVED_SHA
            and data.get("derivedBytes") == DERIVED_BYTES and data.get("newControlSHA256") == NEW_CONTROLS
            and data.get("all130PayloadEntriesUnchanged") is True and data.get("controlEntries") == 7,
            "private-derived-receipt")
    owner = json.loads((root / "evidence/SOURCE.json").read_text())["outputOwner"]["uid"]
    private_regular(root / "baseline.deb", DEB_SHA, 23858480, owner)
    private_regular(root / "candidate.deb", DERIVED_SHA, DERIVED_BYTES, owner)
    original = DEB_SHA
    try:
        DEB_SHA = DERIVED_SHA
        yield
    finally:
        DEB_SHA = original


def host_derived(root):
    require(not os.path.lexists("/usr/sbin/policy-rc.d"), "private-host-policy-path-present")
    return host(root)


def run_derived(root):
    with derived_context(root):
        run_guest(root)


def cleanup_derived(root):
    if not (root / "evidence/CANDIDATE-EXECUTION-INTENT.json").exists():
        return cleanup(root)
    with derived_context(root):
        cleanup_binding(root)
        first = None
        try:
            command(root, "cleanup-owned-fixtures", ["timeout", "--signal=TERM", "--kill-after=5s", "45s",
                    "python3", "-u", str(BASE / "scripts/linux-installer-systemd-guest.py"), "--cleanup-owned-fixtures"], 55,
                    {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LC_ALL": "C"})
        except Exception as error:
            first = error
        try:
            cleanup(root)
        except Exception:
            if first is not None: raise first
            raise
        if first is not None: raise first


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "host", "collect", "derive", "dependencies", "run", "cleanup"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args(); root = root_path(args.output)
    if args.operation == "cleanup" and not (root / "evidence/SOURCE.json").is_file():
        return
    operation = {"prepare": prepare_derived, "host": host_derived, "collect": collect, "derive": derive_private,
                 "dependencies": dependencies, "run": run_derived, "cleanup": cleanup_derived}[args.operation]
    if args.operation in ("host", "dependencies", "run", "cleanup"):
        require(os.geteuid() == 0, "privileged-phase-requires-root")
        owner = evidence_owner(root)
        try:
            operation(root)
        finally:
            publish_evidence(root, owner)
    else:
        operation(root)


if __name__ == "__main__":
    main()
