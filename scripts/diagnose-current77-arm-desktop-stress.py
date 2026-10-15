#!/usr/bin/env python3
"""Ten sequential unchanged desktop smokes on the exact failed signed77 ARM app.

Private diagnosis only. Stops at the first failure; never retries, changes native
cleanup/admission, packages an app, or qualifies a customer/control/release flow.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import types
import zipfile

sys.dont_write_bytecode = True
SELF = "scripts/diagnose-current77-arm-desktop-stress.py"
CONTROLS = "scripts/test-current77-arm-desktop-stress.py"
PROJECTION = "scripts/current77_arm_desktop_stress_workflow_projection.py"
REMAINING = "scripts/diagnose-current77-arm-remaining-gates.py"
REMAINING_SHA = "89bf81192b1299df45d4202ff8b86891af3c5b165ac5107c9121afaf561cfbc0"
BRANCH = "codex/current77-arm-desktop-stress"
GATE = "verification/current77-arm-desktop-stress-private-gate.json"
SCOPE = "current77-failed-arm-desktop-stress-private-only"
COUNT = 10
PARENT_WORKFLOW_SHA = "931aec5cdecc8a3dcf9dc63cc7a693b4b9f31e7565d2b38f76da221a5c097796"
MAX_TOTAL = 28 * 1024 * 1024
RAW_FILE = re.compile(r"owned-(?:app-log-[1-9][0-9]{0,2}\.log|crash-[1-9][0-9]{0,2}\.json)\Z")
CREDENTIALS = frozenset({"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"})


def load_remaining(root):
    path = root / REMAINING
    raw = path.read_bytes()
    if path.is_symlink() or hashlib.sha256(raw).hexdigest() != REMAINING_SHA:
        raise RuntimeError("Frozen r2 diagnostic carrier changed")
    module = types.ModuleType("stress_frozen_remaining_carrier")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module


def verify_gate(base, remaining, root, path):
    base.require(path.is_absolute() and path == root / GATE and path.is_file()
                 and not path.is_symlink(), "committed-desktop-stress-gate-required")
    raw = base.read(path, base.MAX_GATE)
    gate = base.unique_json(raw)
    base.require_identity(gate, {"schema": 1, "scope": SCOPE, "producerSource": base.SOURCE,
        "producerTree": base.TREE, "producerRun": base.RUN, "producerAttempt": 1,
        "failedJob": base.JOB, "privateReplayAllowed": True, "iterationCount": COUNT,
        "stopOnFirstFailure": True, "customerAppQualified": False, "screenControlQualified": False,
        "releaseQualified": False, "publication": False, "privateDmgAllowed": False}, "desktop-stress-gate-scope")
    base.require(gate.get("artifact") == base.ARTIFACT and gate.get("nestedAppSha256") == base.NESTED_SHA,
                 "desktop-stress-exact-original-artifact")
    base.require(isinstance(gate.get("observedOriginals"), dict)
        and gate.get("observedAdmission") == base.validate_records(gate["observedOriginals"]),
        "desktop-stress-original-admission")
    required = set(remaining.CURRENT_FILES) | {REMAINING, remaining.CONTROLS,
                 remaining.PROJECTION, SELF, CONTROLS, PROJECTION}
    pins = gate.get("reviewedHarnessFiles")
    base.require(isinstance(pins, dict) and set(pins) == required
        and all(isinstance(h, str) and base.SHA.fullmatch(h) for h in pins.values()), "desktop-stress-source-pins")
    for name, digest in {GATE: base.sha(raw), **pins}.items():
        data = base.read(root / name)
        base.require(base.sha(data) == digest, "desktop-stress-source-drift")
        base.require(base.capture(["git", "-C", str(root), "show", "HEAD:" + name],
            "desktop-stress-committed-source-required") == data, "desktop-stress-uncommitted-source")
    projection = base.module(root / PROJECTION, pins[PROJECTION])
    old_projection = base.module(root / remaining.PROJECTION, pins[remaining.PROJECTION])
    parent_workflow = projection.current_workflow(base.read(root / base.WORKFLOW).decode())
    base.require(base.sha(parent_workflow.encode()) == PARENT_WORKFLOW_SHA, "desktop-stress-parent-workflow-drift")
    for name, digest in remaining.CURRENT_FILES.items():
        data = base.read(root / name)
        if name == base.WORKFLOW:
            data = old_projection.current_workflow(parent_workflow).encode()
        base.require(base.sha(data) == digest, "desktop-stress-frozen-caller-drift")
    base.require(pins[REMAINING] == REMAINING_SHA, "desktop-stress-r2-pin")
    return gate


def hosted(base, diagnostic, producer):
    old = base.QA_BRANCH
    try:
        base.QA_BRANCH = BRANCH
        return base.hosted_context(diagnostic, producer)
    finally:
        base.QA_BRANCH = old


@contextlib.contextmanager
def native_environment():
    """Native calls inherit no GH/Apple credentials; metadata retains its access."""
    removed = {key: value for key, value in os.environ.items()
               if key in CREDENTIALS or key.startswith("APPLE_")}
    for key in removed:
        del os.environ[key]
    try:
        yield
    finally:
        os.environ.update(removed)


def bounded_original(base, path, maximum):
    parent = path.parent.lstat()
    base.require(path.parent.resolve(strict=True) == path.parent and stat.S_ISDIR(parent.st_mode)
                 and parent.st_uid == os.getuid(), "desktop-stress-original-parent")
    before = path.lstat()
    base.require(stat.S_ISREG(before.st_mode)
        and before.st_uid == os.getuid() and before.st_nlink == 1 and 0 <= before.st_size <= maximum,
        "desktop-stress-original-file-type-size-owner")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    identity = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
                             value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    base.require(identity(before) == identity(opened) == identity(after) == identity(path.lstat())
                 and identity(parent) == identity(path.parent.lstat())
                 and path.parent.resolve(strict=True) == path.parent
                 and len(data) == before.st_size, "desktop-stress-original-file-changed")
    return data


def scoped_archive(base, nested, private):
    """Preserve the frozen RuntimeEvidence diagnostic archive parent guard."""
    data = bounded_original(base, nested, 32 * 1024 * 1024)
    base.require(len(data) == base.NESTED_BYTES and base.sha(data) == base.NESTED_SHA,
                 "desktop-stress-scoped-original-archive-identity")
    scoped = private / "signed-stapled-diagnostic-app.zip"
    with scoped.open("xb") as stream:
        os.chmod(scoped, 0o600)
        stream.write(data)
    reread = bounded_original(base, scoped, 32 * 1024 * 1024)
    base.require(reread == data and scoped.parent == private
                 and scoped != nested, "desktop-stress-scoped-original-archive-copy")
    return scoped


def publish_originals(base, safe, payloads, manifest):
    """No upload-matching final name exists until a complete ZIP is verified."""
    partial, archive = safe / ".diagnostic-originals.zip.partial", safe / "diagnostic-originals.zip"
    with partial.open("xb") as stream:
        os.chmod(partial, 0o600)
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for name, data in payloads.items():
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | 0o600) << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                zipped.writestr(entry, data)
    archive_bytes = bounded_original(base, partial, MAX_TOTAL + 1024 * 1024)
    with zipfile.ZipFile(partial) as zipped:
        names = zipped.namelist()
        base.require(len(names) == len(payloads) and set(names) == set(payloads)
                     and zipped.testzip() is None, "desktop-stress-originals-archive-members-crc")
        for name, data in payloads.items():
            actual = zipped.read(name)
            base.require(len(actual) == len(data) and base.sha(actual) == base.sha(data),
                         "desktop-stress-originals-archive-content")
    before = partial.lstat()
    created_identity = (before.st_dev, before.st_ino, before.st_uid, stat.S_IFMT(before.st_mode))
    published = False
    try:
        # link() cannot replace an existing name; expected identity comes from
        # OUR exclusive partial before creation, never from an unknown final.
        os.link(partial, archive, follow_symlinks=False)
        published = True
        linked = archive.lstat()
        base.require((linked.st_dev, linked.st_ino) == (before.st_dev, before.st_ino)
                     and linked.st_nlink == 2, "desktop-stress-originals-promotion-identity")
        current = partial.lstat()
        base.require((current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) ==
                     (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
                     "desktop-stress-originals-partial-changed")
        partial.unlink()
        base.require(bounded_original(base, archive, MAX_TOTAL + 1024 * 1024) == archive_bytes,
                     "desktop-stress-originals-final-changed")
        # Report retention only after EVERY validation and owned partial cleanup.
        manifest.update(contentRetained=True, withheld=False,
            archive={"bytes": len(archive_bytes), "sha256": base.sha(archive_bytes)})
    except Exception:
        manifest.update(contentRetained=False, withheld=True)
        manifest.pop("archive", None)
        if published:
            try:
                current = archive.lstat()
                actual = (current.st_dev, current.st_ino, current.st_uid, stat.S_IFMT(current.st_mode))
                if actual == created_identity:
                    # Remove only this invocation's exact owned creation. Never
                    # remove a pre-existing/replacement final or another inode.
                    archive.unlink()
                    manifest["invalidOwnedFinalRemoved"] = True
                else:
                    manifest["finalReplacementNotTouched"] = True
            except FileNotFoundError:
                manifest["invalidOwnedFinalAlreadyAbsent"] = True
            except Exception as cleanup_error:
                manifest["invalidOwnedFinalRemovalFailed"] = type(cleanup_error).__name__
        raise


def retain_originals(base, desktop, private, safe, app, code):
    """Retain exact allowed diagnostic bytes only after the frozen privacy gate."""
    manifest = {"schema": 1, "scope": SCOPE + "/matching-originals", "originalExitCode": code,
        "privacyAdmitted": False, "contentRetained": False, "signedAppIncluded": False,
        "customerAppQualified": False, "screenControlQualified": False, "publication": False,
        "files": {}, "withheld": True, "invocationReturned": True,
        "rawCrashReportBytesIncluded": False, "crashPayloadKind": "exact-frozen-privacy-filtered-diagnostic"}
    payloads = {}
    problem = None
    try:
        runtime_dir = private / "runtime"
        attributes = runtime_dir.lstat()
        base.require(runtime_dir.resolve() == runtime_dir and stat.S_ISDIR(attributes.st_mode)
                     and attributes.st_uid == os.getuid(), "desktop-stress-runtime-directory")
        names = sorted(path.name for path in runtime_dir.iterdir())
        base.require(len(names) <= 129 and "runtime.json" in names
            and all(name == "runtime.json" or RAW_FILE.fullmatch(name) for name in names),
            "desktop-stress-runtime-file-allowlist")
        candidates = {"runtime/" + name: (runtime_dir / name, 12 * 1024 * 1024
                       if name == "runtime.json" else 4 * 1024 * 1024) for name in names}
        candidates.update({name: (private / name, limit) for name, limit in (
            ("exit-observer-private.json", 65536), ("desktop.stdout", 1024 * 1024),
            ("desktop.stderr", 1024 * 1024))})
        total = 0
        for name, (path, maximum) in candidates.items():
            data = bounded_original(base, path, maximum)
            total += len(data)
            base.require(total <= MAX_TOTAL, "desktop-stress-original-total-byte-bound")
            manifest["files"][name] = {"bytes": len(data), "sha256": base.sha(data)}
            payloads[name] = data
        runtime = base.unique_json(payloads["runtime/runtime.json"])
        observer = base.unique_json(payloads["exit-observer-private.json"])
        # This exact frozen gate checks runtime privacy=true, outcome/code,
        # original ordinary PID, observer/native identity and diagnostic scope.
        base.safe_observer_facts(observer, runtime, app, code)
        base.require_identity(runtime, {"bundle_path": str(app),
            "executable_path": str(app / "Contents/MacOS/Mixel-Remote"),
            "executable_sha256": base.EXECUTABLE_SHA}, "desktop-stress-original-runtime-app")
        referenced = set()
        for category in ("app_log_deltas", "reports"):
            records = runtime.get(category)
            base.require(isinstance(records, list) and len(records) <= 64
                         and all(isinstance(row, dict) for row in records), "desktop-stress-original-runtime-references")
            for row in records:
                name = row.get("retained_file")
                if name is None:
                    continue
                prefix = "owned-app-log-" if category == "app_log_deltas" else "owned-crash-"
                base.require(isinstance(name, str) and RAW_FILE.fullmatch(name) and name.startswith(prefix)
                    and name not in referenced and "runtime/" + name in payloads, "desktop-stress-original-reference-name")
                referenced.add(name)
                data = payloads["runtime/" + name]
                digest = row.get("sha256") if category == "app_log_deltas" else row.get("retained_sha256")
                base.require(digest == base.sha(data), "desktop-stress-original-reference-digest")
                if category == "app_log_deltas":
                    base.require(type(row.get("bytes")) is int and row["bytes"] == len(data),
                                 "desktop-stress-original-log-byte-count")
        base.require(referenced == set(names) - {"runtime.json"}, "desktop-stress-unreferenced-original-content")
        base.require(all(not desktop.privacy_issue(data) for data in payloads.values()),
                     "desktop-stress-original-content-privacy")
        manifest["privacyAdmitted"] = True
        publish_originals(base, safe, payloads, manifest)
    except Exception as error:
        problem = error
        manifest["withheldReasonType"] = type(error).__name__
        manifest["withheldReasonSha256"] = base.sha(str(error).encode())
        partial = safe / ".diagnostic-originals.zip.partial"
        if os.path.lexists(partial):
            manifest["partialArchiveWithheld"] = True
            try:
                data = bounded_original(base, partial, MAX_TOTAL + 1024 * 1024)
                manifest["partialArchive"] = {"bytes": len(data), "sha256": base.sha(data)}
            except Exception:
                manifest["partialArchiveDigestUnavailable"] = True
    finally:
        base.write(safe / "diagnostic-originals-manifest.json", manifest)
    if problem is not None:
        raise problem
    return manifest


def invocation_withheld(base, private, safe, error):
    """Preserve a raised invocation without inventing a returned exit code."""
    manifest = {"schema": 1, "scope": SCOPE + "/matching-originals", "originalExitCode": None,
        "invocationReturned": False, "privacyAdmitted": False, "contentRetained": False,
        "withheld": True, "signedAppIncluded": False, "rawCrashReportBytesIncluded": False,
        "crashPayloadKind": "exact-frozen-privacy-filtered-diagnostic",
        "customerAppQualified": False, "screenControlQualified": False, "publication": False,
        "invocationFailureType": type(error).__name__,
        "invocationFailureSha256": base.sha(str(error).encode()), "files": {}}
    for name in ("desktop.stdout", "desktop.stderr"):
        try:
            data = bounded_original(base, private / name, 1024 * 1024)
            manifest["files"][name] = {"bytes": len(data), "sha256": base.sha(data)}
        except Exception:
            manifest["files"][name] = {"digestUnavailable": True}
    base.write(safe / "diagnostic-originals-manifest.json", manifest)
    return manifest

def run_stress(base, remaining, parser, concurrency, observer, app, nested, private, safe, receipt):
    receipt.update(iterations=[], completedIterations=0, firstFailedIteration=None,
                   allTenOriginalDesktopPassed=False)
    for number in range(1, COUNT + 1):
        name = "iteration-" + str(number).zfill(2)
        iteration_private = base.new_directory(private / name)
        iteration_safe = base.new_directory(safe / name)
        row = {"iteration": number, "originalExitCode": None, "passed": False,
               "customerAppQualified": False, "screenControlQualified": False,
               "releaseQualified": False, "publication": False}
        receipt["iterations"].append(row)
        receipt["phase"] = name + "/original-desktop"
        primary = None
        try:
            iteration_nested = scoped_archive(base, nested, iteration_private)
            row["scopedArchive"] = {"bytes": base.NESTED_BYTES, "sha256": base.NESTED_SHA}
            try:
                with native_environment():
                    code, output = remaining.original_desktop(base, concurrency, observer,
                        app, iteration_nested, iteration_private)
            except Exception as error:
                row["invocationReturned"] = False
                row["invocationFailureType"] = type(error).__name__
                row["invocationFailureSha256"] = base.sha(str(error).encode())
                try:
                    row["matchingOriginals"] = invocation_withheld(base, iteration_private, iteration_safe, error)
                except Exception as withholding_error:
                    row["withholdingManifestFailureType"] = type(withholding_error).__name__
                    row["withholdingManifestFailureSha256"] = base.sha(str(withholding_error).encode())
                raise
            # Original result is retained before any diagnostic/privacy/admission gate.
            row.update(invocationReturned=True, originalExitCode=code, originalOutput=output)
            try:
                base.retain_observation(iteration_private, iteration_safe, app, row, code, output)
            except Exception as error:
                primary = error
                row["observationFailureType"] = type(error).__name__
                row["observationFailureSha256"] = base.sha(str(error).encode())
            try:
                row["matchingOriginals"] = retain_originals(base, concurrency.desktop,
                    iteration_private, iteration_safe, app, code)
            except Exception as error:
                row["originalRetentionFailureType"] = type(error).__name__
                row["originalRetentionFailureSha256"] = base.sha(str(error).encode())
                if primary is None:
                    primary = error
            if primary is not None:
                raise primary
            base.require(code == 0, "desktop-stress-original-desktop-failed")
            receipt["phase"] = name + "/post-desktop-admission"
            remaining.post_desktop_preconditions(base, parser, concurrency.tray, app,
                iteration_private, iteration_safe, row)
            row["originalTrayPreconditionsVerified"] = True
            row["passed"] = True
            receipt["completedIterations"] = number
        except Exception as error:
            receipt["firstFailedIteration"] = number
            row["failureType"] = type(error).__name__
            row["failureReasonSha256"] = base.sha(str(error).encode())
            row["failedPhase"] = receipt["phase"]
            raise
    receipt["allTenOriginalDesktopPassed"] = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("diagnostic-checkout", "producer-checkout", "gate", "destination", "safe-output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    diagnostic = args.diagnostic_checkout.resolve(strict=True)
    producer = args.producer_checkout.resolve(strict=True)
    remaining = load_remaining(diagnostic)
    base = remaining.load_base(diagnostic)
    gate = verify_gate(base, remaining, diagnostic, args.gate)
    own = hosted(base, diagnostic, producer)
    for name, digest in base.FROZEN.items():
        base.require(base.sha(base.read(producer / name)) == digest, "desktop-stress-frozen-producer-drift")
    support = base.module(producer / "scripts/diagnose-macos-failed-app.py",
                          base.FROZEN["scripts/diagnose-macos-failed-app.py"])
    base.module(diagnostic / "scripts/darwin_exit_observer.py", base.OBSERVER["scripts/darwin_exit_observer.py"])
    observer = base.module(diagnostic / "scripts/darwin_exit_adapter.py", base.OBSERVER["scripts/darwin_exit_adapter.py"])
    before = base.validate_records(base.fresh_records())
    base.fresh_fixture()
    private, safe = base.new_directory(args.destination), base.new_directory(args.safe_output)
    receipt = {"schema": 1, "scope": SCOPE, "producerSource": base.SOURCE, "producerTree": base.TREE,
        "producerRun": base.RUN, "producerAttempt": 1, "failedJob": base.JOB, "artifact": base.ARTIFACT,
        "harnessSource": own, "harnessRun": int(os.environ["GITHUB_RUN_ID"]), "harnessRunAttempt": 1,
        "rootGateSha256": base.sha(base.read(args.gate, base.MAX_GATE)),
        "reviewedHarnessFiles": gate["reviewedHarnessFiles"], "observedBefore": before,
        "iterationCount": COUNT, "stopOnFirstFailure": True, "nativeExecuted": False,
        "customerAppQualified": False, "screenControlQualified": False, "releaseQualified": False,
        "publication": False, "privateDmgPrepared": False, "signedAppUploaded": False}
    app = None
    result = 1
    try:
        app, nested = remaining.prepare_app(base, support, diagnostic, producer, private, receipt)
        frozen_parser = remaining.parser_for_app(base, support, producer, receipt["criticalHashes"])
        concurrency = base.module(producer / "scripts/test-support-launch-macos-concurrency.py",
            base.FROZEN["scripts/test-support-launch-macos-concurrency.py"])
        base.validate_records(base.fresh_records())
        base.fresh_fixture()
        receipt["nativeExecuted"] = True
        run_stress(base, remaining, frozen_parser, concurrency, observer, app, nested,
                   private, safe, receipt)
        result = 0
    except Exception as error:
        receipt["failureType"] = type(error).__name__
        receipt["failureReasonSha256"] = base.sha(str(error).encode())
        receipt["failedPhase"] = receipt.get("phase", "input-validation")
    finally:
        if app is not None:
            try:
                base.require(receipt["criticalHashes"] == {name: support.file_digest((app / name).resolve(strict=True))
                    for name in support.CRITICAL}, "desktop-stress-original-app-critical-hashes-changed")
                support.verify_signatures(app)
                receipt["originalAppSignatureAndCriticalHashesPreserved"] = True
            except Exception:
                receipt["originalAppSignatureAndCriticalHashesPreserved"] = False
                result = 1
        try:
            after = base.fresh_records()
            receipt["observedAfter"] = base.actual_state_summary(after)
            receipt["afterAdmissionValidationPassed"] = False
            base.validate_records(after)
            receipt["afterAdmissionValidationPassed"] = True
        except Exception:
            receipt["postReplayMetadataFailure"] = "metadata-unavailable-or-not-admitted"
            result = 1
        receipt["privateDiagnosticCompleted"] = result == 0
        base.write(safe / "stress-validation-receipt.json", receipt)
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("FAIL: private current77 ARM desktop stress refused; qualification remains false", file=sys.stderr)
        raise SystemExit(1)
