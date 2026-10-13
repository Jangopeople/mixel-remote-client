#!/usr/bin/env python3
"""Hosted-only diagnosis of one failed ARM app; never package or qualify it."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import posixpath
import re
import stat
import struct
import subprocess
import sys
import unicodedata
import zipfile

REPOSITORY = "Jangopeople/mixel-remote-client"
REPOSITORY_ID = 1229987840
SOURCE = "cc95734a3980b93d63d9e8bf0cc7c3689bba846b"
SOURCE_TREE = "bcdf4e25e24e8fe5339faafc7d2bb230bb9e2f37"
BRANCH = "fix/remote-support-reliability"
RUN = 38424276372
ATTEMPT = 1
JOB = 115391416289
WORKFLOW_ID = 271445168
WORKFLOW = ".github/workflows/build.yml"
DIAGNOSTIC_WORKFLOW = ".github/workflows/diagnose-macos-failed-app.yml"
JOB_NAME = "macos (aarch64, aarch64-apple-darwin)"
PACKAGE_STEP = "Package + sign + notarize (.app then .dmg)"
UPLOAD_STEP = "Retain failed macOS launch bundle and bounded diagnostics"
ARTIFACT_ID = 11830478071
ARTIFACT_NAME = "macos-launch-failure-diagnostic-aarch64"
ARTIFACT_BYTES = 24568859
ARTIFACT_SHA256 = "316235b50b6dc9714646dcf543601c6e927e11a7abebb5e674f60a8fd8eed76c"
ARTIFACT_CREATED = "2026-10-13T16:23:46Z"
ARTIFACT_EXPIRES = "2027-01-11T15:41:53Z"
TEAM = "5277F8NDH4"
APP = "Mixel-Remote.app"
OUTER_APP = "aarch64/signed-stapled-diagnostic-app.zip"
OUTER_RUNTIME = "aarch64/runtime/runtime.json"
MAX_API = 4 * 1024 * 1024
MAX_OUTER = 128 * 1024 * 1024
MAX_NESTED = 512 * 1024 * 1024
MAX_APP = 2 * 1024 * 1024 * 1024
MAX_MEMBER = 512 * 1024 * 1024
MAX_FACTS = 64 * 1024
CRITICAL = ("Contents/MacOS/Mixel-Remote", "Contents/MacOS/service",
            "Contents/Frameworks/liblibrustdesk.dylib",
            "Contents/Frameworks/FlutterMacOS.framework/FlutterMacOS")
SHA = re.compile(r"[0-9a-f]{64}\Z")
PHASES = {"tray-launch", "tray-launch-identity", "unarmed-tray-readiness", "os-url-activation",
          "tray-opened-dispatch", "customer-gui-owner", "customer-gui-launch-identity",
          "customer-gui-visible", "attended-service-readiness"}
PRIVACY_CATEGORIES = {"synthetic-invite", "synthetic-api-key", "support-uri-prefix",
                      "invite-shape", "bearer-pattern", "credential-assignment"}
SAFE_REASONS = {
    "OS URL activation bypassed the existing tray process",
    "macOS application URL activation or window probe failed",
    "macOS window probe returned an invalid response",
    "Window probe targets a different application bundle",
    "Tray fixture log contains invitation credentials",
    "Tray launch returned a different executable",
    "Tray launch identity changed before verification",
    "Tray fixture process has no retained launch identity",
    "Fixture process identity is unavailable", "Fixture IPC has no kernel process owner",
    "Fixture incoming IPC belongs to another process", "Fixture IPC returned a different response",
    "Tray route did not create a distinct exact customer GUI",
    "Tray fixture connection count is invalid", "Tray fixture unexpectedly has an authenticated connection",
    "Tray fixture online response is invalid", "Tray fixture relay or runtime-only consent configuration is invalid",
    "Tray fixture cleanup process identity changed", "Tray fixture cleanup socket identity changed",
    "Tray fixture cleanup refused an authenticated connection",
    "Owned tray fixture did not exit after termination",
    "A Mixel IPC endpoint is already active; refusing tray fixture",
    "Pre-existing IPC path is not this runner's socket",
    "Diagnostic file is not an owned regular file", "Diagnostic file exceeds its byte bound",
    "Diagnostic file changed during open", "Diagnostic file changed during read",
    "Diagnostic directory is not an owned directory", "Diagnostic directory exceeds its entry bound",
    "Diagnostic directory contains a symbolic link", "Diagnostic directory exceeds its depth bound",
    "Diagnostic text is not valid UTF-8", "Diagnostic value failed validation",
    "Unarmed tray incoming service did not become ready", "Actual Tao tray Opened dispatch did not become ready",
    "Distinct customer GUI did not become ready", "Visible customer GUI did not become ready",
    "Tray service attended readiness did not become ready",
}


class Refusal(RuntimeError):
    """Only fixed categories may reach hosted output."""


def require(condition, category):
    if not condition:
        raise Refusal(category)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def unique_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate-json-key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Refusal("nonfinite-json-value")))


def utc(value):
    require(isinstance(value, str) and len(value) <= 40, "invalid-utc-time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0, "non-utc-time")
    return parsed


def bounded_read(path, maximum):
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
            and before.st_nlink == 1 and 0 < before.st_size <= maximum, "unsafe-owned-file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        require((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                == (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns), "file-open-race")
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    require(len(data) == before.st_size and len(data) <= maximum
            and (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns), "file-read-race")
    return data


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def capture(command, category, maximum=MAX_API, timeout=120):
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    require(len(result.stdout) + len(result.stderr) <= maximum, "command-output-bound")
    require(result.returncode == 0, category)
    return result.stdout, result.stderr


def api(suffix):
    stdout, _ = capture(["gh", "api", "--method", "GET", "repos/" + REPOSITORY + suffix], "metadata-read-failed")
    return unique_json(stdout)


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
        require(identity.get("full_name") == REPOSITORY and type(identity.get("id")) is int
                and identity["id"] == REPOSITORY_ID, "producer-repository")
    require((record.get("status"), record.get("conclusion")) ==
            ("completed", "failure"), "producer-diagnostic-state")


def validate_jobs(record):
    jobs = record.get("jobs") if isinstance(record, dict) else None
    require(isinstance(jobs, list) and type(record.get("total_count")) is int
            and 0 < len(jobs) <= 100 and record["total_count"] == len(jobs), "job-inventory-bound")
    require(all(isinstance(item, dict) and type(item.get("id")) is int for item in jobs)
            and len({item["id"] for item in jobs}) == len(jobs), "job-inventory-identity")
    selected = [item for item in jobs if item["id"] == JOB]
    require(len(selected) == 1, "failed-job-id")
    job = selected[0]
    require(job.get("name") == JOB_NAME and job.get("status") == "completed"
            and job.get("conclusion") == "failure" and type(job.get("run_id")) is int
            and job["run_id"] == RUN and type(job.get("run_attempt")) is int
            and job["run_attempt"] == ATTEMPT and job.get("head_sha") == SOURCE
            and job.get("head_branch") == BRANCH, "failed-job-provenance")
    start, end = utc(job.get("started_at")), utc(job.get("completed_at"))
    require(start <= end, "failed-job-time")
    required = {}
    steps = job.get("steps")
    require(isinstance(steps, list) and len(steps) <= 100, "failed-job-steps")
    for name, conclusion in ((PACKAGE_STEP, "failure"), (UPLOAD_STEP, "success")):
        matches = [step for step in steps if isinstance(step, dict) and step.get("name") == name]
        require(len(matches) == 1, "failed-job-step-identity")
        step = matches[0]
        require(step.get("status") == "completed" and step.get("conclusion") == conclusion
                and start <= utc(step.get("started_at")) <= utc(step.get("completed_at")) <= end,
                "failed-job-step-result")
        required[name] = step
    require(utc(required[PACKAGE_STEP]["completed_at"]) <= utc(required[UPLOAD_STEP]["started_at"]),
            "failed-job-upload-order")
    return job, required[UPLOAD_STEP]


def validate_artifact(item, upload, now=None):
    require(isinstance(item, dict) and type(item.get("id")) is int and item["id"] == ARTIFACT_ID
            and item.get("name") == ARTIFACT_NAME and type(item.get("size_in_bytes")) is int
            and item["size_in_bytes"] == ARTIFACT_BYTES and item.get("digest") == "sha256:" + ARTIFACT_SHA256,
            "failed-artifact-identity-digest-size")
    run = item.get("workflow_run", {})
    require(type(run.get("id")) is int and run["id"] == RUN and run.get("head_sha") == SOURCE
            and run.get("head_branch") == BRANCH
            and type(run.get("repository_id")) is int and run["repository_id"] == REPOSITORY_ID
            and type(run.get("head_repository_id")) is int and run["head_repository_id"] == REPOSITORY_ID,
            "failed-artifact-source")
    require(item.get("expired") is False and item.get("created_at") == ARTIFACT_CREATED
            and item.get("updated_at") == ARTIFACT_CREATED and item.get("expires_at") == ARTIFACT_EXPIRES
            and utc(item["expires_at"]) > (now or datetime.now(timezone.utc)), "failed-artifact-expiry")
    require(utc(upload["started_at"]) <= utc(item["created_at"]) <= utc(upload["completed_at"]),
            "failed-artifact-attempt-window")


def inventory(archive, expanded, count, allow_links=False):
    entries = archive.infolist()
    require(0 < len(entries) <= count and sum(item.file_size for item in entries) <= expanded,
            "zip-inventory-bound")
    canonical, nodes, links = set(), {}, {}
    for item in entries:
        name = item.filename
        path = PurePosixPath(name)
        key = name.rstrip("/")
        folded = unicodedata.normalize("NFC", key).casefold()
        mode = stat.S_IFMT(item.external_attr >> 16)
        require(name and len(name.encode()) <= 1024 and len(path.parts) <= 64
                and name == item.orig_filename
                and not path.is_absolute() and ".." not in path.parts and ":" not in name
                and "\\" not in name and not any(ord(c) < 32 or ord(c) == 127 for c in name)
                and key == path.as_posix() and folded not in canonical
                and not (item.flag_bits & 1) and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                and item.file_size <= MAX_MEMBER, "unsafe-zip-entry")
        canonical.add(folded)
        require(mode in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK), "unsafe-zip-type")
        if mode == stat.S_IFLNK:
            require(allow_links and not item.is_dir() and 0 < item.file_size <= 4096, "zip-symlink-refused")
            target = archive.read(item).decode("utf-8", errors="strict")
            require(target and not target.startswith("/") and ":" not in target and "\\" not in target
                    and not any(ord(c) < 32 or ord(c) == 127 for c in target), "unsafe-zip-link-target")
            links[key] = posixpath.normpath(posixpath.join(posixpath.dirname(key), target))
        else:
            require(item.is_dir() == (mode == stat.S_IFDIR) if mode in (stat.S_IFDIR, stat.S_IFREG) else True,
                    "zip-type-name-mismatch")
        require(not item.is_dir() or item.file_size == 0, "zip-directory-payload")
        nodes[key] = item
    for key in nodes:
        for parent in PurePosixPath(key).parents:
            if str(parent) == ".":
                continue
            if str(parent) in nodes:
                require(nodes[str(parent)].is_dir(), "zip-entry-below-file-or-link")
    require(archive.testzip() is None, "zip-integrity")
    return entries, nodes, links


def validate_outer(archive):
    entries, _, _ = inventory(archive, MAX_OUTER, 128)
    files = [item.filename for item in entries if not item.is_dir()]
    allowed = re.compile(r"aarch64/runtime/(?:runtime\.json|owned-app-log-[1-9][0-9]?\.log|owned-crash-[1-9][0-9]?\.json)\Z")
    require(files.count(OUTER_APP) == 1 and files.count(OUTER_RUNTIME) == 1
            and all(name == OUTER_APP or allowed.fullmatch(name) for name in files), "failed-outer-inventory")
    require(0 < archive.getinfo(OUTER_APP).file_size <= MAX_NESTED
            and 0 < archive.getinfo(OUTER_RUNTIME).file_size <= 12 * 1024 * 1024, "failed-outer-payload-bound")
    return unique_json(archive.read(OUTER_RUNTIME))


def validate_embedded(record, nested):
    require(isinstance(record, dict) and type(record.get("schema")) is int and record["schema"] == 1
            and record.get("scope") == "macos-launch-smoke-diagnostic-not-customer-release"
            and record.get("runner_context") == {"source_sha": SOURCE, "run_id": RUN}
            and record.get("outcome") == "success" and record.get("privacy_check_passed") is True
            and all(record.get(key) is False for key in ("customer_app_qualified", "screen_control_qualified", "publication")),
            "embedded-source-diagnostic-scope")
    archive = record.get("diagnostic_app_archive", {})
    require(archive.get("name") == "signed-stapled-diagnostic-app.zip"
            and type(archive.get("bytes")) is int and archive["bytes"] == nested.stat().st_size
            and archive.get("sha256") == file_digest(nested)
            and isinstance(record.get("executable_sha256"), str) and SHA.fullmatch(record["executable_sha256"]),
            "embedded-app-digest")
    return record["executable_sha256"]


def validate_nested(archive):
    entries, nodes, links = inventory(archive, MAX_APP, 20000, allow_links=True)
    require(all(key == APP or key.startswith(APP + "/") or key == "__MACOSX"
                or key == "__MACOSX/._" + APP or key == "__MACOSX/" + APP
                or key.startswith("__MACOSX/" + APP + "/") for key in nodes),
            "nested-app-root")
    require(APP + "/Contents/Info.plist" in nodes and APP + "/Contents/MacOS/Mixel-Remote" in nodes,
            "nested-app-required-entry")
    known = set(nodes)
    for key in nodes:
        known.update(str(parent) for parent in PurePosixPath(key).parents if str(parent) != ".")
    for key, item in nodes.items():
        if key.startswith("__MACOSX/") and not item.is_dir():
            require(key not in links and PurePosixPath(key).name.startswith("._")
                    and 26 <= item.file_size <= 8 * 1024 * 1024, "unsafe-appledouble-entry")
            target = PurePosixPath(key[len("__MACOSX/"):])
            target = str(target.with_name(target.name[2:]))
            require(target in known and (target == APP or target.startswith(APP + "/")),
                    "appledouble-missing-app-target")
            data = archive.read(item)
            magic, version = struct.unpack_from(">II", data)
            count = struct.unpack_from(">H", data, 24)[0]
            require(magic == 0x00051607 and version == 0x00020000 and 0 < count <= 16
                    and len(data) >= 26 + 12 * count, "unsafe-appledouble-header")
            spans, identities = [], set()
            for index in range(count):
                identity, offset, size = struct.unpack_from(">III", data, 26 + 12 * index)
                require(identity in (2, 9) and identity not in identities
                        and offset >= 26 + 12 * count and offset + size <= len(data),
                        "unsafe-appledouble-payload")
                identities.add(identity)
                spans.append((offset, offset + size))
            require(all(end <= following for (_, end), (following, _) in zip(sorted(spans), sorted(spans)[1:])),
                    "overlapping-appledouble-payload")
    def resolve(key, trail=()):
        require(len(trail) < 32 and key not in trail, "nested-link-cycle")
        parts = PurePosixPath(key).parts
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            if prefix in links:
                replacement = posixpath.join(links[prefix], *parts[index:])
                require(replacement.startswith(APP + "/") or replacement == APP, "nested-link-escape")
                return resolve(replacement, (*trail, key))
        require(key in known and (key == APP or key.startswith(APP + "/")), "nested-link-missing-target")
        return key
    for key in links:
        require(key.startswith(APP + "/"), "metadata-symlink-refused")
        resolve(key)
    return entries


def signature_identity(text):
    teams = re.findall(r"^TeamIdentifier=([A-Z0-9]{10})$", text, re.MULTILINE)
    flags = re.findall(r"^CodeDirectory [^\r\n]*\bflags=0x([0-9a-fA-F]+)(?:\([^\r\n)]*\))?(?: |$)", text, re.MULTILINE)
    require(teams == [TEAM] and len(flags) == 1 and int(flags[0], 16) & 0x10000,
            "signed-team-or-hardened-runtime")


def verify_signatures(app):
    commands = ((["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], "app-signature"),
                (["xcrun", "stapler", "validate", str(app)], "app-stapled-ticket"),
                (["spctl", "--assess", "--type", "execute", "--verbose=2", str(app)], "app-gatekeeper"))
    for command, category in commands:
        capture(command, category, maximum=512 * 1024)
    stdout, stderr = capture(["codesign", "--display", "--verbose=4", str(app)], "app-signing-identity")
    signature_identity((stdout + stderr).decode("utf-8", errors="strict"))


def verify_tree(app):
    require(app.is_dir() and not app.is_symlink(), "extracted-app-root")
    root = app.resolve(strict=True)
    count, total = 0, 0
    for directory, directories, files in os.walk(app, followlinks=False):
        for name in directories + files:
            path = Path(directory) / name
            item = path.lstat()
            count += 1
            require(count <= 25000 and item.st_uid == os.getuid(), "extracted-tree-bound-owner")
            if stat.S_ISLNK(item.st_mode):
                require(path.resolve(strict=True).is_relative_to(root), "extracted-link-escape")
            else:
                require(stat.S_ISREG(item.st_mode) or stat.S_ISDIR(item.st_mode), "extracted-special-file")
                if stat.S_ISREG(item.st_mode):
                    total += item.st_size
                    require(item.st_nlink == 1 and item.st_size <= MAX_MEMBER and total <= MAX_APP,
                            "extracted-file-bound")


def validate_app_identity(app, expected_executable):
    info = plistlib.loads(bounded_read(app / "Contents/Info.plist", 1024 * 1024))
    required = {"CFBundleIdentifier": "ch.mixel.remote", "CFBundleExecutable": "Mixel-Remote",
                "CFBundleShortVersionString": "1.4.6", "CFBundleVersion": "64", "LSMinimumSystemVersion": "12.3"}
    require(all(info.get(key) == value for key, value in required.items()), "signed-app-identity-minimum")
    require(file_digest(app / "Contents/MacOS/Mixel-Remote") == expected_executable, "embedded-executable-digest")
    return required


def hosted_context(diagnostic, producer):
    require(sys.platform == "darwin" and platform.machine() == "arm64"
            and os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_OS") == "macOS"
            and os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "hosted-arm-manual-only")
    own_source = os.environ.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", own_source) and own_source != SOURCE
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
            and os.environ.get("GITHUB_REF") == "refs/heads/" + BRANCH
            and os.environ.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + DIAGNOSTIC_WORKFLOW + "@refs/heads/" + BRANCH,
            "diagnostic-checkout-event-binding")
    for checkout, expected in ((diagnostic, own_source), (producer, SOURCE)):
        require(checkout.is_dir() and not checkout.is_symlink(), "checkout-directory")
        stdout, _ = capture(["git", "-C", str(checkout), "rev-parse", "HEAD"], "checkout-head")
        require(stdout.decode().strip() == expected, "checkout-source")
        stdout, _ = capture(["git", "-C", str(checkout), "status", "--porcelain"], "checkout-status")
        require(stdout == b"", "checkout-dirty")
    stdout, _ = capture(["git", "-C", str(producer), "rev-parse", "HEAD^{tree}"], "producer-tree")
    require(stdout.decode().strip() == SOURCE_TREE, "producer-tree-source")
    old_workflow = bounded_read(producer / WORKFLOW, 1024 * 1024).decode()
    contract = ('DIAGNOSTIC_APP_ZIP="$DIAGNOSTICS/signed-stapled-diagnostic-app.zip"',
                'ditto -c -k --keepParent "$APP_PATH" "$DIAGNOSTIC_APP_ZIP"',
                '--diagnostic-app-zip "$DIAGNOSTIC_APP_ZIP"',
                "if: ${{ always() && steps.macos_package.outcome == 'failure' }}",
                "name: macos-launch-failure-diagnostic-${{ matrix.job.arch }}", "path: macos-launch-failure/")
    require(all(fragment in old_workflow for fragment in contract), "exact-failed-upload-source-contract")
    return own_source


def helper_contract(diagnostic):
    contract_path = diagnostic / "scripts/failed-macos-diagnostic-helper-hashes.json"
    contract = unique_json(bounded_read(contract_path, MAX_FACTS))
    expected = {"scripts/test-support-launch-macos-tray.py", "scripts/test-support-launch-macos.py",
                "scripts/test-support-launch-linux.py", "scripts/verify-macos-bundle-minimum.py",
                "scripts/verify-macos-native-minimum.py", "scripts/test-support-launch-macos-tray-controls.py"}
    require(isinstance(contract, dict) and set(contract) == expected
            and all(isinstance(value, str) and SHA.fullmatch(value) for value in contract.values()), "diagnostic-helper-contract")
    for name, expected_hash in contract.items():
        require(digest(bounded_read(diagnostic / name, 1024 * 1024)) == expected_hash, "diagnostic-helper-source-drift")
    return contract


def safe_reason(value):
    require(isinstance(value, str) and len(value.encode()) <= 4096, "runtime-reason-bound")
    result = {"reasonSha256": digest(value.encode())}
    if value in SAFE_REASONS:
        result.update(reasonCategory="fixed-reviewed-reason", reason=value)
    elif re.fullmatch(r"Diagnostic filesystem operation failed \(errno=(?:None|-?[0-9]{1,10})\)", value):
        result.update(reasonCategory="filesystem-error", reason=value)
    else:
        result["reasonCategory"] = "unrecognized-error-redacted"
    return result


def safe_runtime_facts(record):
    require(isinstance(record, dict), "runtime-facts-object")
    safe = {"scope": "failed-producer-tray-diagnostic-only", "customerAppQualified": False,
            "publication": False, "rawLogsIncluded": False}
    for key in ("syntheticInvite", "customerAcceptTested", "remoteDesktopConnected", "actualTrayDispatchObserved",
                "visibleGuiAndAttendedV2", "ownedProcessesStopped", "passed"):
        require(type(record.get(key)) is bool, "runtime-facts-boolean")
        safe[key] = record[key]
    require(safe["syntheticInvite"] and not safe["customerAcceptTested"] and not safe["remoteDesktopConnected"],
            "runtime-facts-no-connection-scope")
    for key in ("trayPid", "guiPid"):
        if key in record:
            require(type(record[key]) is int and 0 < record[key] < 2**31, "runtime-facts-pid")
            safe[key] = record[key]
    if "criticalHashes" in record:
        hashes = record["criticalHashes"]
        require(isinstance(hashes, dict) and set(hashes) == set(CRITICAL)
                and all(isinstance(value, str) and SHA.fullmatch(value) for value in hashes.values()), "runtime-critical-hashes")
        safe["criticalHashes"] = hashes
    primary = record.get("runtimeFailure")
    if primary is not None:
        require(isinstance(primary, dict) and set(primary) == {"phase", "reason"}
                and primary.get("phase") in PHASES, "runtime-primary-failure-schema")
        safe["primaryFailure"] = {"phase": primary["phase"], **safe_reason(primary["reason"])}
    outer = record.get("failure")
    if outer is not None:
        safe["outerFailure"] = safe_reason(outer)
        if primary is not None and outer != primary["reason"]:
            safe["cleanupFailure"] = safe_reason(outer)
    # Unknown fields are retained only as hashes. Their values never enter the
    # uploaded artifact, including a privacy guard's original raw snippet.
    excluded = set(safe) | {"observedUtc", "criticalHashes", "runtimeFailure", "failure"}
    for key in sorted(set(record) - excluded):
        require(isinstance(key, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", key), "runtime-facts-field-name")
    safe["diagnosticFieldHashes"] = {key: digest(json.dumps(record[key], sort_keys=True, allow_nan=False).encode())
                                    for key in sorted(set(record) - excluded)}
    if "observedUtc" in record:
        safe["observedUtc"] = utc(record["observedUtc"]).isoformat()
    return safe


def safe_privacy_facts(record):
    require(isinstance(record, dict) and set(record) == {"schema", "originalResultPreserved",
            "matchedObservations", "observationLimitReached"} and type(record.get("schema")) is int
            and record["schema"] == 1 and record.get("originalResultPreserved") is True
            and type(record.get("observationLimitReached")) is bool, "privacy-observation-schema")
    observations = record.get("matchedObservations")
    require(isinstance(observations, list) and len(observations) <= 64, "privacy-observation-bound")
    for item in observations:
        require(isinstance(item, dict) and set(item) == {"categories", "freshBytesSha256", "freshBytes"}
                and isinstance(item.get("categories"), list) and 0 < len(item["categories"]) <= 6
                and all(isinstance(category, str) and category in PRIVACY_CATEGORIES for category in item["categories"])
                and len(set(item["categories"])) == len(item["categories"])
                and isinstance(item.get("freshBytesSha256"), str) and SHA.fullmatch(item["freshBytesSha256"])
                and type(item.get("freshBytes")) is int and 0 < item["freshBytes"] <= 12 * 1024 * 1024,
                "unsafe-privacy-observation")
    return record


def safe_launch_facts(record):
    require(isinstance(record, dict) and set(record) == {"schema", "originalResultPreserved",
            "launchObservations", "observationLimitReached"} and type(record.get("schema")) is int
            and record["schema"] == 1 and record.get("originalResultPreserved") is True
            and type(record.get("observationLimitReached")) is bool, "launch-observation-schema")
    observations = record.get("launchObservations")
    require(isinstance(observations, list) and len(observations) <= 4, "launch-observation-bound")
    for item in observations:
        launch = item.get("launchTime") if isinstance(item, dict) else None
        try:
            finite = type(launch) in (int, float) and launch > 0 and math.isfinite(launch)
        except OverflowError:
            finite = False
        require(isinstance(item, dict) and set(item) == {"action", "pid", "launchTime"}
                and item.get("action") in ("tray", "launch") and type(item.get("pid")) is int
                and 0 < item["pid"] < 2**31 and finite,
                "unsafe-launch-observation")
    return record



def safe_gui_facts(record):
    require(isinstance(record, dict) and set(record) == {"schema", "originalResultPreserved",
            "probeObservations", "serviceObservations", "observationLimitReached", "postFailureWindow"}
            and type(record.get("schema")) is int and record["schema"] == 1
            and record.get("originalResultPreserved") is True
            and type(record.get("observationLimitReached")) is bool, "gui-observation-schema")
    window = record.get("postFailureWindow")
    refusals = {"isolated-context", "noncanonical-app", "gui-owner-changed", "authenticated-connection",
                "ownership-unavailable", "observation-operation-failed", "server-identity-changed", "service-health-unavailable"}
    require(isinstance(window, dict) and set(window) == {"attempted", "deadlineSeconds", "elapsedSeconds", "refusalCategory"}
            and type(window.get("attempted")) is bool and type(window.get("deadlineSeconds")) is int
            and window["deadlineSeconds"] == 14 and window.get("refusalCategory") in refusals | {None},
            "gui-observation-window")
    def finite_range(value, low, high):
        try:
            return type(value) in (int, float) and low <= value <= high and math.isfinite(value)
        except OverflowError:
            return False
    require(finite_range(window.get("elapsedSeconds"), 0, 15), "gui-observation-window-bound")
    require(window["attempted"] or window["elapsedSeconds"] == 0 and window["refusalCategory"] is None,
            "gui-observation-window-state")
    observations = record.get("probeObservations")
    require(isinstance(observations, list) and len(observations) <= 64, "gui-observation-bound")
    fields = {"pid", "origin", "status", "observedUnixTime", "elapsedSeconds", "running", "visible", "hidden",
              "launchTime", "launchTimeState", "bundleMatch"}
    for item in observations:
        require(isinstance(item, dict) and set(item) == fields and type(item.get("pid")) is int
                and 0 < item["pid"] < 2**31 and item.get("origin") in ("fixture", "post-failure")
                and item.get("status") in ("response", "probe-unavailable")
                and finite_range(item.get("observedUnixTime"), 1, 2**40)
                and finite_range(item.get("elapsedSeconds"), 0, 15)
                and all(type(item.get(field)) is bool or item.get(field) is None for field in ("running", "visible", "hidden"))
                and type(item.get("bundleMatch")) is bool and item.get("launchTimeState") in ("finite", "missing", "invalid"),
                "unsafe-gui-observation")
        launch = item["launchTime"]
        require((item["launchTimeState"] == "finite" and finite_range(launch, 0, 2**40))
                or item["launchTimeState"] in ("missing", "invalid") and launch is None, "gui-launch-time-state")
        require(item["origin"] != "post-failure" or window["attempted"], "gui-post-failure-window-required")

    services = record.get("serviceObservations")
    require(isinstance(services, list) and len(services) <= 64, "service-observation-bound")
    service_fields = {"pid", "observedUnixTime", "elapsedSeconds", "retainedServerLaunchTime",
                      "serverLaunchMatch", "attendedServiceReady", "status"}
    for item in services:
        require(isinstance(item, dict) and set(item) == service_fields and type(item.get("pid")) is int
                and 0 < item["pid"] < 2**31 and finite_range(item.get("observedUnixTime"), 1, 2**40)
                and finite_range(item.get("elapsedSeconds"), 0, 15) and type(item.get("serverLaunchMatch")) is bool
                and (type(item.get("attendedServiceReady")) is bool or item.get("attendedServiceReady") is None)
                and item.get("status") in ("response", "server-identity-unavailable", "ownership-unavailable", "health-unavailable")
                and (item.get("retainedServerLaunchTime") is None or finite_range(item["retainedServerLaunchTime"], 1, 2**40)),
                "unsafe-service-observation")
        require(item["status"] != "response" or item["serverLaunchMatch"] and type(item["attendedServiceReady"]) is bool
                and finite_range(item["retainedServerLaunchTime"], 1, 2**40),
                "service-response-requires-retained-identity")
        require(item["status"] == "response" or item["attendedServiceReady"] is None, "service-refusal-unknown-only")
        require(window["attempted"], "service-post-failure-window-required")
    return record


def download_archive(destination):
    path = destination / "failed-arm-artifact.zip"
    with path.open("xb") as output:
        os.chmod(path, 0o600)
        result = subprocess.run(["gh", "api", "--method", "GET", "repos/" + REPOSITORY
                                 + "/actions/artifacts/" + str(ARTIFACT_ID) + "/zip"],
                                stdout=output, stderr=subprocess.DEVNULL, timeout=180)
    require(result.returncode == 0 and path.stat().st_size == ARTIFACT_BYTES
            and file_digest(path) == ARTIFACT_SHA256, "downloaded-failed-artifact-digest-size")
    return path


def new_directory(path):
    path = path.absolute()
    require(path.parent.resolve(strict=True) == path.parent and not os.path.lexists(path), "new-canonical-output")
    path.mkdir(mode=0o700)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic-checkout", type=Path, required=True)
    parser.add_argument("--producer-checkout", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--safe-output", type=Path, required=True)
    args = parser.parse_args(argv)
    diagnostic, producer = args.diagnostic_checkout.resolve(strict=True), args.producer_checkout.resolve(strict=True)
    own_source = hosted_context(diagnostic, producer)
    helper_hashes = helper_contract(diagnostic)
    run = api("/actions/runs/" + str(RUN)); validate_run(run)
    job, upload = validate_jobs(api("/actions/runs/" + str(RUN) + "/attempts/1/jobs?per_page=100"))
    item = api("/actions/artifacts/" + str(ARTIFACT_ID)); validate_artifact(item, upload)
    private = new_directory(args.destination)
    safe = new_directory(args.safe_output)
    receipt = {"scope": "failed-macos-arm-app-diagnostic-only", "repository": REPOSITORY,
               "producerSource": SOURCE, "producerRun": RUN, "producerAttempt": ATTEMPT, "failedJob": JOB,
               "artifactId": ARTIFACT_ID, "artifactSha256": ARTIFACT_SHA256, "artifactBytes": ARTIFACT_BYTES,
               "diagnosticSource": own_source, "helperHashes": helper_hashes,
               "customerAppQualified": False, "publication": False, "rawLogsIncluded": False,
               "remoteDesktopConnected": False, "nativeExecuted": False, "validationPassed": False}
    try:
        outer = download_archive(private)
        nested = private / "signed-stapled-diagnostic-app.zip"
        with zipfile.ZipFile(outer) as zipped:
            embedded = validate_outer(zipped)
            with nested.open("xb") as output:
                os.chmod(nested, 0o600)
                output.write(zipped.read(OUTER_APP))
        executable_hash = validate_embedded(embedded, nested)
        with zipfile.ZipFile(nested) as zipped:
            validate_nested(zipped)
        app_root = new_directory(private / "owned-app-copy")
        capture(["ditto", "-x", "-k", str(nested), str(app_root)], "validated-ditto-extraction")
        app = app_root / APP
        verify_tree(app)
        receipt["bundleIdentity"] = validate_app_identity(app, executable_hash)
        verify_signatures(app)
        minimum = private / "bundle-minimum.json"
        capture([sys.executable, "-B", str(diagnostic / "scripts/verify-macos-bundle-minimum.py"), "--bundle", str(app),
                 "--arch", "arm64", "--minimum", "12.3", "--output", str(minimum)], "complete-arm64-minimum")
        report = unique_json(bounded_read(minimum, MAX_API))
        require(report.get("status") == "passed" and report.get("architecture") == "arm64"
                and report.get("advertised_minimum") == "12.3" and type(report.get("binary_count")) is int
                and report["binary_count"] >= 3, "complete-arm64-minimum-result")
        receipt["criticalHashes"] = {name: file_digest((app / name).resolve(strict=True)) for name in CRITICAL}
        receipt["signingTeam"] = TEAM
        # Revalidate exact failed artifact/job/attempt immediately before native
        # diagnosis; a concurrent rerun must not substitute producer provenance.
        validate_run(api("/actions/runs/" + str(RUN)))
        _, final_upload = validate_jobs(api("/actions/runs/" + str(RUN) + "/attempts/1/jobs?per_page=100"))
        validate_artifact(api("/actions/artifacts/" + str(ARTIFACT_ID)), final_upload)
        receipt["validationPassed"] = True
        receipt["nativeExecuted"] = True
        result = subprocess.run([sys.executable, "-B", str(diagnostic / "scripts/run-failed-macos-tray-diagnostic.py"),
                                 "--app", str(app), "--proofs", str(private / "tray-runtime")],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=420)
        facts = unique_json(bounded_read(private / "tray-runtime/tray-runtime-safe-facts.json", MAX_FACTS))
        sanitized = safe_runtime_facts(facts)
        sanitized["privacyMatches"] = safe_privacy_facts(unique_json(bounded_read(
            private / "tray-runtime/privacy-match-safe-facts.json", MAX_FACTS)))
        sanitized["launchServicesObservations"] = safe_launch_facts(unique_json(bounded_read(
            private / "tray-runtime/launch-observation-safe-facts.json", MAX_FACTS)))
        sanitized["guiProbeObservations"] = safe_gui_facts(unique_json(bounded_read(
            private / "tray-runtime/gui-probe-safe-facts.json", MAX_FACTS)))
        if "criticalHashes" in sanitized:
            require(sanitized["criticalHashes"] == receipt["criticalHashes"], "runtime-exact-app-copy")
        else:
            receipt["runtimeCriticalHashesAbsent"] = True
        write_json(safe / "tray-diagnostic-safe-facts.json", sanitized)
        receipt["runtimeExitCode"] = result.returncode
        receipt["runtimePassed"] = sanitized["passed"]
        require((result.returncode == 0) == sanitized["passed"], "runtime-exit-facts-disagree")
        print("PASS: exact failed ARM app validated and diagnosed; safe facts only; customer qualification false")
        return 0
    except Refusal as error:
        receipt["refusalCategory"] = str(error)
        raise
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile):
        receipt["refusalCategory"] = "diagnostic-operation-failed"
        raise Refusal("diagnostic-operation-failed") from None
    finally:
        write_json(safe / "validation-receipt.json", receipt)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refusal as error:
        print("FAIL: diagnostic refusal: " + str(error), file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile):
        print("FAIL: diagnostic collection failed; no customer qualification", file=sys.stderr)
        raise SystemExit(1)
