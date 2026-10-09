#!/usr/bin/env python3
"""Submit an artifact once; retry bounded waits for its exact Apple notary ID."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ID_PATTERN = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
MAX_WAIT_ATTEMPTS = 3
WAIT_SECONDS = 600


class NotaryError(RuntimeError):
    pass


def submission_id(value):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise NotaryError("Apple returned an invalid notarization submission ID")
    return value.lower()


def authentication(profile=None, environment=None):
    environment = os.environ if environment is None else environment
    if profile:
        return ["--keychain-profile", profile]
    fields = ("APPLE_NOTARY_USER", "APPLE_NOTARY_PASSWORD", "APPLE_NOTARY_TEAM_ID")
    if any(not environment.get(field) for field in fields):
        raise NotaryError("Apple notarization requires a keychain profile or all three notary credentials")
    return ["--apple-id", environment[fields[0]], "--password", environment[fields[1]],
            "--team-id", environment[fields[2]]]


def notarize(artifact, log_dir, auth, *, sleep=time.sleep):
    artifact, log_dir = Path(artifact).resolve(), Path(log_dir).resolve()
    if not artifact.is_file() or artifact.stat().st_size == 0:
        raise NotaryError("Notarization artifact is missing or empty")
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {"artifact": artifact.name, "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
              "accepted": False, "submit_attempts": 0, "wait_attempts": 0}

    def redact(value):
        for secret in auth[1::2]:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value

    def invoke(operation, target, label):
        command = ["xcrun", "notarytool", operation, str(target), *auth, "--output-format", "json"]
        if operation == "wait":
            command += ["--timeout", f"{WAIT_SECONDS}s"]
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                    timeout=WAIT_SECONDS + 60)
            stdout, stderr, code = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or ""
            stderr = error.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8")
            stderr += "\nNotary process timed out; submission was not repeated.\n"
            code = 124
        (log_dir / f"{label}.stdout.log").write_text(redact(stdout), encoding="utf-8")
        (log_dir / f"{label}.stderr.log").write_text(redact(stderr), encoding="utf-8")
        if stdout.strip():
            print(redact(stdout).strip(), flush=True)
        if stderr.strip():
            print(redact(stderr).strip(), file=sys.stderr, flush=True)
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            data = None
        if data is not None and not isinstance(data, dict):
            raise NotaryError("Apple notarization response is not a JSON object")
        return code, data, stderr

    try:
        report["submit_attempts"] = 1
        code, data, _stderr = invoke("submit", artifact, "submit")
        if code != 0 or data is None:
            raise NotaryError("Apple notarization submission failed; it was not repeated")
        identifier = submission_id(data.get("id"))
        report["submission_id"] = identifier
        if str(data.get("status", "")).casefold() in {"invalid", "rejected"}:
            raise NotaryError("Apple rejected the notarization submission")
        print(f"Notary submission ID: {identifier}", flush=True)
        for attempt in range(1, MAX_WAIT_ATTEMPTS + 1):
            report["wait_attempts"] = attempt
            code, data, stderr = invoke("wait", identifier, f"wait-{attempt}")
            if data is not None and "id" in data and submission_id(data["id"]) != identifier:
                raise NotaryError("Apple wait response belongs to a different notarization submission")
            status = data.get("status") if data else None
            report["status"] = status
            if isinstance(status, str) and status.casefold() in {"invalid", "rejected"}:
                raise NotaryError("Apple rejected the notarization submission; stapling is forbidden")
            if code == 0 and status == "Accepted" and data is not None:
                if submission_id(data.get("id")) != identifier:
                    raise NotaryError("Accepted notarization response lacks the exact submission ID")
                report["accepted"] = True
                print(f"PASS: Apple notarization Accepted for {artifact.name}; submission {identifier}; wait attempt {attempt}", flush=True)
                return report
            transient = status == "In Progress" or bool(re.search(
                r"timed out|timeout|connection reset|connection was lost|NSURLErrorDomain Code=-(?:1001|1003|1004|1005|1009)\b|\b(?:408|429|500|502|503|504)\b",
                stderr, re.IGNORECASE))
            if not transient:
                raise NotaryError("Apple notarization wait failed without a retryable transport or pending status")
            if attempt == MAX_WAIT_ATTEMPTS:
                raise NotaryError("Apple notarization was not Accepted after three bounded waits")
            delay = attempt * 10
            print(f"Notary wait attempt {attempt} incomplete; retrying the same submission after {delay}s", flush=True)
            sleep(delay)
    except Exception as error:
        report["error"] = redact(str(error))
        raise
    finally:
        (log_dir / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--keychain-profile")
    args = parser.parse_args()
    try:
        notarize(args.artifact, args.log_dir, authentication(args.keychain_profile))
    except (NotaryError, OSError, UnicodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
