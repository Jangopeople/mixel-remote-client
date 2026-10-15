#!/usr/bin/env python3
"""Run the three unchanged current77 signed-app gates privately, without packaging."""
import argparse
import contextlib
import hashlib
import json
import math
import re
import socket
import stat
import os
from pathlib import Path
import subprocess
import sys
import types
import zipfile

sys.dont_write_bytecode = True
SELF = "scripts/diagnose-current77-arm-remaining-gates.py"
CONTROLS = "scripts/test-current77-arm-remaining-gates.py"
PROJECTION = "scripts/current77_arm_remaining_workflow_projection.py"
BASE = "scripts/diagnose-current77-arm-warm-exit.py"
BASE_SHA = "30271d30fb3130d60e0aa790cbbb7724715b7b73de62b189e66d6bccc40d64cc"
PARSER = "scripts/diagnose-macos-concurrency-failed-intel.py"
PARSER_SHA = "45ff48039eafb005475340c68b9bb49a4596a577d5384c800a9da788aae98958"
BRANCH = "codex/current77-arm-remaining-gates-r2"
GATE = "verification/current77-arm-remaining-gates-r2-private-gate.json"
SCOPE = "current77-failed-arm-remaining-gates-r2-private-only"
CURRENT_FILES = {
    ".github/workflows/diagnose-macos-failed-app.yml": "561a113179fa036895ee6906e99e5b9a95241fb5e3c983ed77347103ac51c504",
    "scripts/current77-arm-warmexit-observed-records.json": "603f029be552a651ac1cabf9686ffbe8bbc874bb50bd5db439bf782a8cc7e795",
    "scripts/current77_arm_workflow_projection.py": "27d4f206d8820803e2b9b24e772fd44161ae2f52a3fb9967b435375ee8f86613",
    "scripts/darwin_exit_adapter.py": "71f6297ab8566e3de0d1fcedc59836412910270f4709028458250e9202bb65fd",
    "scripts/darwin_exit_observer.py": "f19af1bf38260edb33125fea2e446561f7048e39b6c81e5aa4efde378837eadf",
    BASE: BASE_SHA,
    "scripts/test-diagnose-current77-arm-warm-exit.py": "c78f942aceb59956deff72e389a594b3247522232fc6093956d4cbd9f1eeb4d8",
    "scripts/test-diagnose-macos-failed-app.py": "b74bf7ead76088656a8ced7f8910908fbe79c39cd9cae879091d146b70d7d25a",
    "scripts/test_darwin_exit_adapter.py": "86a9332af15c11d6fe7b5539094b2c251aa21a5924a2f817a0ca1a5864a6abfc",
    "scripts/test_darwin_exit_observer.py": "a330be925b268d5a26d9863e4d3605b284fc972b65b1b5cfc84ae544e1f1f05c",
}


def load_base(root):
    path = root / BASE
    raw = path.read_bytes()
    if path.is_symlink() or hashlib.sha256(raw).hexdigest() != BASE_SHA:
        raise RuntimeError("Frozen current77 ARM carrier changed")
    loaded = types.ModuleType("remaining_gates_frozen_arm_carrier")
    loaded.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), loaded.__dict__)
    return loaded


def verify_gate(base, root, path):
    base.require(path.is_absolute() and path == root / GATE and path.is_file()
                 and not path.is_symlink(), "committed-remaining-gates-root-gate-required")
    raw = base.read(path, base.MAX_GATE)
    gate = base.unique_json(raw)
    base.require_identity(gate, {"schema": 1, "scope": SCOPE, "producerSource": base.SOURCE,
        "producerTree": base.TREE, "producerRun": base.RUN, "producerAttempt": 1,
        "failedJob": base.JOB, "privateReplayAllowed": True, "customerAppQualified": False,
        "releaseQualified": False, "publication": False, "privateDmgAllowed": False}, "remaining-gates-scope-identity")
    base.require(gate.get("artifact") == base.ARTIFACT and gate.get("nestedAppSha256") == base.NESTED_SHA,
                 "remaining-gates-exact-artifact")
    base.require(isinstance(gate.get("observedOriginals"), dict)
                 and gate.get("observedAdmission") == base.validate_records(gate["observedOriginals"]),
                 "remaining-gates-honest-original-admission")
    pins = gate.get("reviewedHarnessFiles")
    base.require(isinstance(pins, dict) and set(pins) == set(CURRENT_FILES) | {SELF, CONTROLS, PROJECTION}
                 and all(isinstance(h, str) and base.SHA.fullmatch(h) for h in pins.values()), "remaining-gates-harness-pins")
    for name, expected in {GATE: base.sha(raw), **pins}.items():
        data = base.read(root / name)
        base.require(base.sha(data) == expected, "remaining-gates-source-drift")
        base.require(base.capture(["git", "-C", str(root), "show", "HEAD:" + name], "remaining-gates-tracked-source") == data,
                     "remaining-gates-committed-source-differs")
    # The workflow has an exact inverse. Every other current c747 caller/helper
    # remains byte-exact; there is no rebinding of the desktop/observer source.
    projection = base.module(root / PROJECTION, pins[PROJECTION])
    for name, expected in CURRENT_FILES.items():
        data = base.read(root / name)
        if name == base.WORKFLOW:
            data = projection.current_workflow(data.decode("utf-8")).encode("utf-8")
        base.require(base.sha(data) == expected, "frozen-c747-source-drift")
    return gate


def hosted(base, diagnostic, producer):
    original = base.QA_BRANCH
    try:
        base.QA_BRANCH = BRANCH
        return base.hosted_context(diagnostic, producer)
    finally:
        base.QA_BRANCH = original


def parser_for_app(base, support, producer, critical):
    parser = base.module(producer / PARSER, PARSER_SHA)
    parser.SUPPORT = support
    parser.OBSERVER = base.module(producer / "scripts/observe-macos-incoming-cleanup.py",
        base.FROZEN["scripts/observe-macos-incoming-cleanup.py"])
    # This isolated parser instance validates actual verified current77 ARM
    # hashes. Historical parser/profile source and its own dispatch stay intact.
    parser.CRITICAL_HASHES = dict(critical)
    return parser


def original_desktop(base, concurrency, observer, app, nested, private):
    stdout, stderr = private / "desktop.stdout", private / "desktop.stderr"
    with stdout.open("x", encoding="utf-8") as out, stderr.open("x", encoding="utf-8") as err:
        os.chmod(stdout, 0o600); os.chmod(stderr, 0o600)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with observer.install(concurrency.desktop, private / "exit-observer-private.json"):
                code = concurrency.run_original("--original-desktop", ["--app", str(app),
                    "--diagnostics", str(private / "runtime"), "--diagnostic-app-zip", str(nested)])
    base.require(type(code) is int and code in (0, 1), "original-desktop-result")
    return code, output_hashes(base, stdout, stderr)


def output_hashes(base, stdout, stderr):
    return {label: {"bytes": p.stat().st_size, "sha256": base.sha(p.read_bytes())}
            for label, p in (("stdout", stdout), ("stderr", stderr))}


def child_environment():
    return {k: v for k, v in os.environ.items() if not k.startswith("APPLE_") and k not in
            {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"}}


def original_child(base, producer, arguments, private, label, timeout):
    stdout, stderr = private / (label + ".stdout"), private / (label + ".stderr")
    with stdout.open("xb") as out, stderr.open("xb") as err:
        os.chmod(stdout, 0o600); os.chmod(stderr, 0o600)
        result = subprocess.run([sys.executable, "-B", str(producer / "scripts/test-support-launch-macos-concurrency.py"),
                                *arguments], stdout=out, stderr=err, env=child_environment(), timeout=timeout)
    base.require(type(result.returncode) is int, "original-child-result")
    return result.returncode, output_hashes(base, stdout, stderr)


def endpoint_snapshot(path, marker, expected_pid, base):
    row = {"exists": False, "kind": "absent", "uidMatchesCurrent": None,
           "stable": True, "markerPid": None, "markerMatchesOwnedColdPid": None}
    try:
        info = path.lstat()
    except FileNotFoundError:
        return row, None
    except OSError:
        row.update(kind="unavailable", stable=False)
        return row, None
    identity = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink, info.st_size, info.st_mtime_ns)
    kind = ("socket" if stat.S_ISSOCK(info.st_mode) else "regular" if stat.S_ISREG(info.st_mode)
            else "symlink" if stat.S_ISLNK(info.st_mode) else "directory" if stat.S_ISDIR(info.st_mode) else "other")
    row.update(exists=True, kind=kind, uidMatchesCurrent=info.st_uid == os.getuid())
    if marker and kind == "regular" and row["uidMatchesCurrent"] and info.st_nlink == 1:
        try:
            raw = base.read(path, 64)
            if re.fullmatch(rb"[1-9][0-9]{0,9}\n?", raw) and int(raw) < 2**31:
                row["markerPid"] = int(raw)
                row["markerMatchesOwnedColdPid"] = type(expected_pid) is int and int(raw) == expected_pid
        except (OSError, RuntimeError, ValueError):
            row["stable"] = False
    return row, identity


def socket_inactivity(path, row):
    if not row["exists"]:
        return "not-present" if row["kind"] == "absent" else "unavailable"
    if row["kind"] != "socket" or not row["uidMatchesCurrent"]:
        return "unsafe-not-connected"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(1)
            try:
                connection.connect(str(path))
            except FileNotFoundError:
                return "not-found"
            except ConnectionRefusedError:
                return "connection-refused"
        return "active"
    except OSError:
        return "other-connect-error"


def post_desktop_preconditions(base, parser, tray, app, private, safe, receipt):
    """Observe only; admit exactly the frozen tray socket contract, never clean."""
    facts = {"schema": 1, "scope": SCOPE + "/post-desktop-tray-admission", "admitted": False,
        "customerAppQualified": False, "publication": False, "cleanupPerformed": False,
        "ownedColdRuntimeVerified": False, "ownedColdPid": None, "ownedColdLaunchTime": None,
        "originalRuntimeSha256": None, "liveMixelRolePids": [], "processInventoryVerified": False,
        "endpoints": {}, "frozenTrayPreconditions": {"incoming": False, "url": False}}
    error = None
    try:
        try:
            raw = base.read(private / "runtime/runtime.json", 12 * 1024 * 1024)
            facts["originalRuntimeSha256"] = base.sha(raw)
            runtime = base.unique_json(raw)
            base.require_identity(runtime, {"schema": 1, "scope": "macos-launch-smoke-diagnostic-not-customer-release",
                "outcome": "success", "privacy_check_passed": True, "customer_app_qualified": False,
                "screen_control_qualified": False, "publication": False, "bundle_path": str(app),
                "executable_path": str(app / "Contents/MacOS/Mixel-Remote"), "executable_sha256": base.EXECUTABLE_SHA},
                "post-desktop-owned-runtime-identity")
            pid, launch = runtime.get("owned_pid"), runtime.get("owned_launch_time")
            base.require(type(pid) is int and 1 < pid < 2**31 and type(launch) in (int, float)
                and math.isfinite(launch) and launch > 0, "post-desktop-owned-cold-identity")
            events = runtime.get("events")
            base.require(isinstance(events, list) and len(events) <= 4096
                and all(isinstance(e, dict) for e in events), "post-desktop-owned-events")
            cold = [e for e in events if e.get("event") == "owned-application-launched"]
            base.require(len(cold) == 1 and cold[0].get("phase") == "cold-url-launch"
                and type(cold[0].get("pid")) is int and cold[0]["pid"] == pid, "post-desktop-cold-event-binding")
            cleanup = runtime.get("cleanup")
            base.require(isinstance(cleanup, dict) and cleanup.get("process") in
                ("already-exited", "already-terminated", "terminated-owned-application")
                and cleanup.get("endpoint") in ("absent", "removed-owned-endpoint")
                and not any(k.endswith("_error") for k in cleanup), "post-desktop-original-cleanup-failed")
            facts.update(ownedColdRuntimeVerified=True, ownedColdPid=pid, ownedColdLaunchTime=launch)
        except (OSError, RuntimeError, ValueError, TypeError):
            error = "post-desktop-owned-runtime-not-proven"
        try:
            pids = parser.process_inventory()
            valid = isinstance(pids, list) and len(pids) <= 256 and all(type(pid) is int and 0 < pid < 2**31 for pid in pids)
            facts["processInventoryVerified"] = valid
            facts["liveMixelRolePids"] = pids if valid else []
            if not valid or pids: error = error or "post-desktop-live-role-or-inventory-refused"
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            error = error or "post-desktop-process-inventory-unavailable"
        paths = {"incoming": tray.unix.IPC_PATH, "url": tray.URL_SOCKET}
        observed = {}
        for label, endpoint in paths.items():
            for marker, path, key in ((False, endpoint, label), (True, endpoint.with_suffix(".pid"), label + "Marker")):
                row, identity = endpoint_snapshot(path, marker, facts["ownedColdPid"], base)
                if not marker:
                    row["kernelConnectResult"] = socket_inactivity(path, row)
                facts["endpoints"][key] = row
                observed[key] = (path, marker, identity)
        # These are the exact next original fixture's preconditions. They only
        # inspect/connect; no deletion, signalling or replacement is introduced.
        for label, endpoint in paths.items():
            try:
                tray.endpoint_inactive(endpoint)
                facts["frozenTrayPreconditions"][label] = True
            except (OSError, RuntimeError, ValueError):
                error = error or "post-desktop-original-tray-precondition-refused"
        for key, (path, marker, identity) in observed.items():
            row = facts["endpoints"][key]
            after, after_identity = endpoint_snapshot(path, marker, facts["ownedColdPid"], base)
            row["stable"] = row["stable"] and after["stable"] and identity == after_identity
            if not row["stable"] or row["kind"] == "unavailable":
                error = error or "post-desktop-endpoint-changed-or-unavailable"
            if row["exists"]:
                if not row["uidMatchesCurrent"] or row["kind"] != ("regular" if marker else "socket"):
                    error = error or "post-desktop-endpoint-type-or-owner-refused"
                elif marker and row["markerMatchesOwnedColdPid"] is not True:
                    error = error or "post-desktop-marker-not-owned-cold-pid"
                elif not marker and row["kernelConnectResult"] != "connection-refused":
                    error = error or "post-desktop-socket-not-stably-inactive"
        facts["admitted"] = error is None
        if error: facts["refusalCategory"] = error
    finally:
        base.write(safe / "post-desktop-tray-admission-safe-facts.json", facts)
        receipt["postDesktopAdmission"] = {"admitted": facts["admitted"],
            "ownedColdPid": facts["ownedColdPid"], "originalRuntimeSha256": facts["originalRuntimeSha256"],
            "noLiveMixelRoleVerified": facts["processInventoryVerified"] and not facts["liveMixelRolePids"]}
    base.require(facts["admitted"], error or "post-desktop-admission-unavailable")
    return facts


def run_gates(base, support, parser, concurrency, observer, producer, app, nested, private, safe, receipt):
    receipt["phase"] = "original-desktop"
    code, hashes = original_desktop(base, concurrency, observer, app, nested, private)
    receipt["desktop"] = {"originalExitCode": code, "privateOutput": hashes}
    base.retain_observation(private, safe, app, receipt["desktop"], code, hashes)
    base.require(code == 0, "original-desktop-failed-later-gates-prohibited")
    post_desktop_preconditions(base, parser, concurrency.tray, app, private, safe, receipt["desktop"])
    receipt["desktop"]["originalTrayPreconditionsVerified"] = True

    receipt["phase"] = "original-tray"
    tray_root = private / "tray"
    code, hashes = original_child(base, producer, ["--original-tray", "--app", str(app), "--proofs", str(tray_root)],
                                   private, "tray", 420)
    receipt["tray"] = {"originalExitCode": code, "privateOutput": hashes, "kernelExitStatusObserved": False}
    raw = base.unique_json(base.read(tray_root / "tray-runtime-safe-facts.json", 65536))
    tray = support.safe_runtime_facts(raw)
    tray["scope"] = SCOPE + "/original-tray"
    base.write(safe / "original-tray-safe-facts.json", tray)
    base.require(code == 0 and tray["passed"] is True and tray["ownedProcessesStopped"] is True
        and tray["actualTrayDispatchObserved"] is True and tray["visibleGuiAndAttendedV2"] is True
        and tray.get("criticalHashes") == parser.CRITICAL_HASHES
        and not any(k in tray for k in ("primaryFailure", "outerFailure", "cleanupFailure"))
        and not any(k in raw for k in ("failure", "runtimeFailure", "endpointCleanupFailures")),
        "original-tray-failed-later-gates-prohibited")
    parser.stopped_runtime()
    receipt["tray"]["allRoleAndEndpointAbsenceVerified"] = True

    receipt["phase"] = "full-concurrency"
    concurrency_root = private / "concurrency"
    code, hashes = original_child(base, producer, ["--app", str(app), "--proofs", str(concurrency_root)],
                                   private, "concurrency", 660)
    receipt["concurrency"] = {"originalExitCode": code, "privateOutput": hashes, "kernelExitStatusObserved": False}
    raw = base.unique_json(base.read(concurrency_root / "concurrency-runtime-safe-facts.json", 1024 * 1024))
    facts = parser.safe_facts(raw)
    facts["scope"] = SCOPE + "/full-concurrency"
    base.write(safe / "original-concurrency-safe-facts.json", facts)
    parser.require_runtime_pass(code, facts)
    parser.stopped_runtime()
    receipt["concurrency"]["allRoleAndEndpointAbsenceVerified"] = True
    receipt["allOriginalGatesPassed"] = True


def prepare_app(base, support, diagnostic, producer, private, receipt):
    archive = private / "original-failed-arm-artifact.zip"
    with archive.open("xb") as out:
        os.chmod(archive, 0o600)
        result = subprocess.run(["gh", "api", "--method", "GET", "repos/" + base.REPOSITORY
            + "/actions/artifacts/" + str(base.ARTIFACT["id"]) + "/zip"], stdout=out,
            stderr=subprocess.DEVNULL, timeout=180)
    base.require(result.returncode == 0 and archive.stat().st_size == base.ARTIFACT["size_in_bytes"]
                 and support.file_digest(archive) == base.ARTIFACT["digest"][7:], "exact-original-archive-required")
    nested = private / "signed-stapled-diagnostic-app.zip"
    with zipfile.ZipFile(archive) as z:
        embedded = support.validate_outer(z)
        with nested.open("xb") as out:
            os.chmod(nested, 0o600); out.write(z.read(support.OUTER_APP))
    base.validate_embedded(embedded, nested)
    with zipfile.ZipFile(nested) as z: support.validate_nested(z)
    app_root = base.new_directory(private / "owned-app-copy")
    base.capture(["ditto", "-x", "-k", str(nested), str(app_root)], "validated-ditto-extraction")
    app = app_root / support.APP
    support.verify_tree(app)
    receipt["bundleIdentity"] = support.validate_app_identity(app, base.EXECUTABLE_SHA)
    support.verify_signatures(app)
    minimum = private / "bundle-minimum.json"
    base.capture([sys.executable, "-B", str(producer / "scripts/verify-macos-bundle-minimum.py"), "--bundle", str(app),
                  "--arch", "arm64", "--minimum", "12.3", "--output", str(minimum)], "complete-arm64-minimum")
    report = base.unique_json(base.read(minimum, 4 * 1024 * 1024))
    base.require(report.get("status") == "passed" and report.get("architecture") == "arm64"
                 and report.get("advertised_minimum") == "12.3" and type(report.get("binary_count")) is int
                 and report["binary_count"] >= 3, "complete-arm64-minimum-result")
    receipt["criticalHashes"] = {n: support.file_digest((app / n).resolve(strict=True)) for n in support.CRITICAL}
    receipt["signingTeam"] = support.TEAM
    return app, nested


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("diagnostic-checkout", "producer-checkout", "gate", "destination", "safe-output"):
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args(argv)
    diagnostic, producer = a.diagnostic_checkout.resolve(strict=True), a.producer_checkout.resolve(strict=True)
    base = load_base(diagnostic)
    gate = verify_gate(base, diagnostic, a.gate)
    own = hosted(base, diagnostic, producer)
    for name, expected in base.FROZEN.items():
        base.require(base.sha(base.read(producer / name)) == expected, "frozen-producer-helper-drift")
    support = base.module(producer / "scripts/diagnose-macos-failed-app.py", base.FROZEN["scripts/diagnose-macos-failed-app.py"])
    base.module(diagnostic / "scripts/darwin_exit_observer.py", base.OBSERVER["scripts/darwin_exit_observer.py"])
    observer = base.module(diagnostic / "scripts/darwin_exit_adapter.py", base.OBSERVER["scripts/darwin_exit_adapter.py"])
    before = base.validate_records(base.fresh_records())
    base.fresh_fixture()
    private, safe = base.new_directory(a.destination), base.new_directory(a.safe_output)
    receipt = {"schema": 1, "scope": SCOPE, "producerSource": base.SOURCE, "producerTree": base.TREE,
        "producerRun": base.RUN, "producerAttempt": 1, "failedJob": base.JOB, "artifact": base.ARTIFACT,
        "harnessSource": own, "harnessRun": int(os.environ["GITHUB_RUN_ID"]), "harnessRunAttempt": 1,
        "rootGateSha256": base.sha(base.read(a.gate, base.MAX_GATE)), "reviewedHarnessFiles": gate["reviewedHarnessFiles"],
        "observedBefore": before, "nativeExecuted": False, "allOriginalGatesPassed": False,
        "customerAppQualified": False, "releaseQualified": False, "publication": False,
        "privateDmgPrepared": False, "rawLogsUploaded": False}
    code = 1
    try:
        app, nested = prepare_app(base, support, diagnostic, producer, private, receipt)
        parser = parser_for_app(base, support, producer, receipt["criticalHashes"])
        concurrency = base.module(producer / "scripts/test-support-launch-macos-concurrency.py",
                                  base.FROZEN["scripts/test-support-launch-macos-concurrency.py"])
        base.validate_records(base.fresh_records()); base.fresh_fixture()
        receipt["nativeExecuted"] = True
        run_gates(base, support, parser, concurrency, observer, producer, app, nested, private, safe, receipt)
        base.require(receipt["criticalHashes"] == {n: support.file_digest((app / n).resolve(strict=True)) for n in support.CRITICAL},
                     "original-app-critical-hashes-changed")
        support.verify_signatures(app)
        receipt["originalAppSignatureAndCriticalHashesPreserved"] = True
        code = 0
    except Exception as error:
        receipt["failureType"] = type(error).__name__
        receipt["failureReasonSha256"] = base.sha(str(error).encode())
        # Only fixed categories/hash, never raw exception, URI, file path or log.
        receipt["failedPhase"] = receipt.get("phase", "input-validation")
        code = 1
    finally:
        try:
            after = base.fresh_records()
            receipt["observedAfter"] = base.actual_state_summary(after)
            receipt["afterAdmissionValidationPassed"] = False
            base.validate_records(after)
            receipt["afterAdmissionValidationPassed"] = True
        except Exception:
            code = 1
            receipt["postReplayMetadataFailure"] = "metadata-unavailable-or-not-admitted"
        receipt["privateDiagnosticCompleted"] = code == 0
        base.write(safe / "remaining-gates-validation-receipt.json", receipt)
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("FAIL: private current77 ARM remaining-gates admission refused", file=sys.stderr)
        raise SystemExit(1)
