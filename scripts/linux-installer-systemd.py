#!/usr/bin/env python3
"""Unpublished fixed-6c0 normal-DEB QA on a fresh real Ubuntu systemd runner.

The hosted transfer is a new, explicitly counted normal-artifact GET, separate
from retained local archives. This does not qualify a release or restrict the
runner's network like QEMU restrict=on. The repository and CI artifacts may be
public; no application secrets or customer data are used. No GUI is tested.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import subprocess
import time

REPO = "Jangopeople/mixel-remote-client"
REPO_ID = 1229987840
APP = "6c0ab10cd2ef7b666615864fbc602e4097aacca9"
TREE = "a33f33a562a18ef0143069bf17db533835da8839"
RUN, ATTEMPT, JOB, ARTIFACT = 38999506750, 1, 117300405310, 12089394040
ARCHIVE_BYTES = 23831223
ARCHIVE_SHA = "11b0cf1261847e8625d9192ffcf4c041622d563f126520890390dea36e24b0a0"
DEB_SHA = "d63c5b624e55312bee8e52f5a2db16355fc3a06f5e12cee332f5567c57c391f1"
MAIN_SHA = "ec180ea8503f85167949d74498baa1f08f908dd614a5a953ecd0e84cc082a437"
GUEST_SHA = "91c005b9590de4a4d06d471a12e6c42fbbdae915fe94a6c6a41324d2acba2eb2"
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


def command(root, label, argv, timeout=120, env=None):
    require(re.fullmatch(r"[a-z0-9-]+", label), "unsafe-command-label")
    folder = root / "evidence" / label
    folder.mkdir(mode=0o700)
    save(folder / "COMMAND.json", {"argv": argv, "timeoutSeconds": timeout, "automaticRetry": False})
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
    require(status == 0 and error is None, "command-failed-" + label)
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
                 "/etc/systemd/system/multi-user.target.wants/mixel-remote.service",
                 "/root/.config/mixel-remote", "/var/tmp/mixel-systemd-qa", "/mnt/mixel-candidate",
                 "/var/lib/mixel-systemd-qa-unrelated-sentinel"):
        require(not os.path.lexists(path), "host-not-fresh-" + path)
    require(command(root, "host-systemd", ["systemctl", "is-system-running", "--wait"], 60).strip() == b"running", "unhealthy-systemd")
    r = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Status}", "mixel-remote"], capture_output=True, timeout=30)
    require(r.returncode == 1 and not r.stdout, "existing-Mixel-package")
    save(root / "evidence/HOST.json", {"fresh": True, "actualPID1Systemd": True, "root": True, "release": release})


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


def dependencies(root):
    require(os.geteuid() == 0 and (root / "evidence/COLLECTION.json").is_file(), "uncollected-dependencies-request")
    context = Path("/var/tmp") / (root.name + "-official-apt")
    context.mkdir(mode=0o755); context.chmod(0o755)
    sources = context / "ubuntu.list"
    signed = "deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] "
    sources.write_text(signed + "https://archive.ubuntu.com/ubuntu jammy main restricted universe multiverse\n" +
                       signed + "https://archive.ubuntu.com/ubuntu jammy-updates main restricted universe multiverse\n" +
                       signed + "https://security.ubuntu.com/ubuntu jammy-security main restricted universe multiverse\n")
    sources.chmod(0o644)  # Public signed repository configuration, readable by _apt.
    (context / "lists/partial").mkdir(parents=True)
    (context / "lists").chmod(0o755)
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
    save(root / "evidence/CANDIDATE-EXECUTION-INTENT.json", {"DEBSHA256": DEB_SHA, "guestSHA256": GUEST_SHA, "networkQEMUIsolated": False})
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


def cleanup_package(root):
    main = Path("/usr/share/mixel-remote/mixel-remote")
    if main.exists():
        require(sha(main) == MAIN_SHA, "cleanup-refuses-other-payload")
        command(root, "cleanup-owned-service", ["systemctl", "stop", "mixel-remote.service"], 60)
        command(root, "cleanup-owned-package", ["apt-get", "--yes", "--no-download", "--no-auto-remove", "purge", "mixel-remote"], 120)


def cleanup(root):
    evidence = root / "evidence"
    if (evidence / "CANDIDATE-EXECUTION-INTENT.json").is_file():
        try:
            retain_guest_evidence(root)
        finally:
            cleanup_package(root)
        save(evidence / "CLEANUP.json", {"scope": "Only fresh-run Mixel package; original failure stays failure", "taskComplete": False})


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "host", "collect", "dependencies", "run", "cleanup"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args(); root = root_path(args.output)
    if args.operation == "cleanup" and not (root / "evidence/SOURCE.json").is_file():
        return
    operation = {"prepare": prepare, "host": host, "collect": collect, "dependencies": dependencies,
                 "run": run_guest, "cleanup": cleanup}[args.operation]
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
