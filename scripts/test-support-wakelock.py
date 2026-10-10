#!/usr/bin/env python3
"""Execute exact pinned and generated keep-awake managers with controlled futures."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from sdk_discovery import find_dart

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_PIN = "1abc897c451c8b5bbff3792509a7fef9d12f2ce3"
SCENARIOS = (
    "provider-error", "provider-retry", "acquire-release-race", "release-error",
    "synchronous-error", "desktop-reference-count", "duplicate-and-unknown-key",
    "preference-and-server", "mobile-last-request", "pending-release-new-session",
    "coalesced-before-start", "coalesced-during-acquisition",
    "bounded-request-backlog",
)
ORIGINAL_FAILURES = {
    "provider-error": "provider failure must not escape the session zone",
    "provider-retry": "failed acquisition must remain retryable",
    "acquire-release-race": "release must wait for acquisition",
    "release-error": "failed release must retain successful active state",
    "synchronous-error": "synchronous platform failure must be contained",
}


def load_patcher():
    spec = importlib.util.spec_from_file_location("mixel_wakelock_patch", ROOT / "scripts/patch-support-wakelock.py")
    patcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patcher)
    return patcher


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=30, **kwargs)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def main() -> None:
    repo = Path(os.environ.get("RDREPO", ROOT / "rustdesk")).resolve()
    dart = find_dart()
    require(bool(dart), "Dart SDK required: put dart on PATH or set DART_BIN")
    version = run([dart, "--version"])
    require(version.returncode == 0 and "Dart SDK version: 3.5.4 " in version.stdout + version.stderr,
            "Pinned Dart 3.5.4 required for these production-source controls")
    revision = run(["git", "-C", str(repo), "rev-parse", "HEAD"])
    require(revision.returncode == 0 and revision.stdout.strip() == UPSTREAM_PIN,
            "Source-extracted controls require the exact pinned RustDesk 1.4.6 checkout")
    original_result = run(["git", "-C", str(repo), "show", "HEAD:flutter/lib/common.dart"])
    require(original_result.returncode == 0, original_result.stderr)
    original = original_result.stdout
    patcher = load_patcher()
    manager = patcher.extract_manager(original)
    require(hashlib.sha256(manager.encode()).hexdigest() == patcher.ORIGINAL_SHA256,
            "Pinned original manager hash differs")
    generated = patcher.patch_common(original)
    require(patcher.patch_common(generated) == generated, "Generated class must be byte-idempotent")
    start, end = patcher.manager_range(original)
    new_start, new_end = patcher.manager_range(generated)
    require(original[:start] == generated[:new_start] and original[end:] == generated[new_end:],
            "Patch changed neighboring production source")
    drift_controls = (
        original.replace("static bool _enabled = false;", "static bool _enabled = true;", 1),
        original.replace(patcher.START, "class WakelockManagerChanged {", 1),
        original.replace(patcher.END, "\n/// changed reload marker", 1),
        original + "\n" + manager,
        generated.replace("await WakelockPlus.enable();", "WakelockPlus.enable();", 1),
        original.replace("WakelockPlus.disable();", "WakelockPlus.toggle(enable: false);", 1),
    )
    for index, drift in enumerate(drift_controls):
        try:
            patcher.patch_common(drift)
        except RuntimeError:
            pass
        else:
            raise RuntimeError(f"Unverified source drift {index} was accepted")
    fixture = (ROOT / "scripts/test-support-wakelock.dart").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="mixel-wakelock-source-") as directory:
        temporary = Path(directory)
        target = temporary / patcher.SOURCE
        target.parent.mkdir(parents=True)
        target.write_text(original, encoding="utf-8")
        env = {**os.environ, "RDREPO": str(temporary)}
        for invocation in range(2):
            result = run([sys.executable, str(ROOT / "scripts/patch-support-wakelock.py")], env=env)
            require(result.returncode == 0, result.stdout + result.stderr)
            require(target.read_bytes() == generated.encode(), f"CLI patch invocation {invocation} differed")
        # The CLI also fails before writing a drifted production file.
        target.write_text(drift_controls[0], encoding="utf-8")
        before = target.read_bytes()
        rejected = run([sys.executable, str(ROOT / "scripts/patch-support-wakelock.py")], env=env)
        require(rejected.returncode != 0 and "Pinned keep-awake manager changed" in rejected.stderr,
                "CLI upgrade drift did not fail closed")
        require(target.read_bytes() == before, "Rejected CLI drift modified its input")
        programs = {}
        for label, source in (("original", manager), ("generated", patcher.extract_manager(generated))):
            program = temporary / f"{label}_manager.dart"
            program.write_text("import 'dart:async';\nimport 'dart:io';\n\n" + source + "\n" + fixture,
                               encoding="utf-8")
            analysis = run([dart, "analyze", str(program)])
            require(analysis.returncode == 0, analysis.stdout + analysis.stderr)
            programs[label] = program
        for scenario, reason in ORIGINAL_FAILURES.items():
            result = run([dart, "run", str(programs["original"]), scenario])
            require(result.returncode == 1 and reason in result.stderr,
                    f"Original negative {scenario} failed for an unexpected reason:\n{result.stdout}{result.stderr}")
            print(f"PASS original negative {scenario}: {reason}")
        for scenario in SCENARIOS:
            result = run([dart, "run", str(programs["generated"]), scenario])
            require(result.returncode == 0 and result.stdout.strip() == f"PASS {scenario}",
                    f"Generated production manager failed:\n{result.stdout}{result.stderr}")
            if scenario == "bounded-request-backlog":
                require("OBSERVED 1 queued callback registrations for 10002 requests" in result.stderr,
                        "Queue growth control did not observe its single retained callback")
                print(result.stderr.strip())
            print(result.stdout.strip())
    print("PASS verified pinned source, Dart 3.5.4 analysis, CLI idempotence, neighboring bytes, and 7 fail-closed drift controls")
    print("PASS keep-awake source regression: 5 actual original failures rejected; 13 generated scenarios passed")


if __name__ == "__main__":
    main()
