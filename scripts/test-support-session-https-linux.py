#!/usr/bin/env python3
"""Run the actual desktop HTTPS suite against a disposable owned relay.

The fixture uses new relay keys and its own TLS CA. It never changes the live
Mixel server. Peers request ordinary ID connections, so successful sessions
prove automatic HTTPS relay selection under a complete or partial port outage.
"""
import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LABEL = {"mixel.task": "registration-proof"}


def execute(arguments, *, check=True, timeout=30, capture=False, termination_grace=60, kill_grace=5):
    process = subprocess.Popen(arguments, text=True,
                               stdout=subprocess.PIPE if capture else None,
                               stderr=subprocess.PIPE if capture else None)
    def terminate():
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=termination_grace)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.communicate(timeout=kill_grace)
                except subprocess.TimeoutExpired:
                    for pipe in (process.stdout, process.stderr):
                        if pipe:
                            pipe.close()
                    process.wait(timeout=kill_grace)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        # Python fixture/desktop children handle SIGTERM with SystemExit,
        # allowing their own finally blocks to remove retained resources.
        try:
            terminate()
        except subprocess.SubprocessError as error:
            print("Timed out reaping the failed task process: " + str(error), file=sys.stderr, flush=True)
        raise
    result = subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result


def validate_manifest(manifest):
    if manifest.get("kind") != "mixel-isolated-registration-proof" or manifest.get("production_mutations") is not False:
        raise ValueError("Expected a deployment-free relay fixture manifest")
    network = manifest.get("network", "")
    match = re.fullmatch(r"(mixel-registration-proof-[0-9a-f]{12})-net", network)
    if not match:
        raise ValueError("Fixture network is not a uniquely owned test network")
    name = match.group(1)
    if manifest.get("volume") != name + "-data" or manifest.get("containers") != [name + "-hbbs", name + "-hbbr", name + "-router"]:
        raise ValueError("Fixture resources do not match its unique ownership name")
    return manifest


def owned(kind, name, **execution_limits):
    details = json.loads(execute(["docker", kind, "inspect", name], capture=True, **execution_limits).stdout)
    labels = details[0].get("Config", {}).get("Labels", {}) if kind == "container" else details[0].get("Labels", {})
    if any(labels.get(key) != value for key, value in LABEL.items()):
        raise ValueError("Refusing to operate on a resource without the fixture ownership label: " + name)


def cleanup(manifest, output, interrupted=False):
    """Every owned resource gets an independent cleanup attempt."""
    validate_manifest(manifest)
    errors = []
    limits = {"timeout": 2, "termination_grace": .25, "kill_grace": .25}
    for name in reversed(manifest["containers"]):
        try:
            owned("container", name, **limits)
            try:
                if interrupted:
                    result = subprocess.CompletedProcess([], 0, "", "")
                else:
                    result = execute(["docker", "logs", "--tail", "100", name], check=False, capture=True,
                                     timeout=1, termination_grace=.25, kill_grace=.25)
                # Keep service diagnostics, excluding connection identities,
                # relay payloads and all keys in the private server volume.
                lines = [line for line in (result.stdout + result.stderr).splitlines()
                         if re.search(r"authentication failed|Listening on|closed:|failure:|failed:", line, re.I)]
                (output / (name.rsplit("-", 1)[-1] + "-service.log")).write_text("\n".join(lines) + "\n")
            except Exception as error:
                errors.append("Fixture diagnostics: " + str(error))
            execute(["docker", "rm", "-f", name], capture=True, **limits)
        except Exception as error:
            errors.append("Fixture container cleanup: " + str(error))
    for kind, name in (("network", manifest["network"]), ("volume", manifest["volume"])):
        try:
            owned(kind, name, **limits)
            execute(["docker", kind, "rm", name], capture=True, **limits)
        except Exception as error:
            errors.append("Fixture " + kind + " cleanup: " + str(error))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deb", required=True, type=Path)
    parser.add_argument("--proofs", required=True, type=Path)
    parser.add_argument("--require-native", action="store_true")
    parser.add_argument("--artifact-run-id")
    parser.add_argument("--mixed-transports", action="store_true", help="Host retains native UDP registration; controller retains native TCP; ordinary ID/FT sessions must bridge the partial TCP outage")
    args = parser.parse_args()
    deb = args.deb.resolve(strict=True)
    output = args.proofs.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise SystemExit("Proof output directory must be empty")
    fixture_directory = output / "relay-fixture"
    fixture_manifest = None
    failure = None
    lifecycle = {"relay_environment": "isolated fixture (test CA and test public pin)",
                 "production_mutations": False, "mixed_transports": args.mixed_transports, "result": "failed"}
    try:
        execute([sys.executable, str(ROOT / "scripts/test-relay-ws-bridge-docker.py"),
                 "--keep-fixture", str(fixture_directory)], timeout=900)
        fixture_manifest = validate_manifest(json.loads((fixture_directory / "manifest.json").read_text()))
        for name in fixture_manifest["containers"]:
            owned("container", name)
        owned("network", fixture_manifest["network"])
        owned("volume", fixture_manifest["volume"])
        command = [sys.executable, str(ROOT / "scripts/test-support-session-linux.py"),
                   "--deb", str(deb), "--proofs", str(output / "session"),
                   "--mixed-transports" if args.mixed_transports else "--native-blocked",
                   "--isolated-relay-network", fixture_manifest["network"],
                   "--isolated-relay-address", fixture_manifest["fixture_address"],
                   "--isolated-relay-ca", fixture_manifest["public_test_ca_path"],
                   "--isolated-relay-pin", fixture_manifest["public_pin_path"]]
        if args.require_native:
            command += ["--require-native"]
        if args.artifact_run_id:
            command += ["--artifact-run-id", args.artifact_run_id]
        execute(command, timeout=1200)
        lifecycle["result"] = "passed"
    except BaseException as error:
        failure = error
        raise
    finally:
        errors = []
        try:
            if fixture_manifest is None and (fixture_directory / "manifest.json").is_file():
                fixture_manifest = validate_manifest(json.loads((fixture_directory / "manifest.json").read_text()))
            if fixture_manifest is not None:
                interrupted = isinstance(failure, (SystemExit, KeyboardInterrupt, subprocess.TimeoutExpired))
                errors = cleanup(fixture_manifest, fixture_directory, interrupted=interrupted)
        except Exception as error:
            errors.append("Fixture cleanup: " + str(error))
        lifecycle["owned_fixture_cleanup"] = "failed" if errors else ("completed" if fixture_manifest else "no retained fixture")
        if errors:
            lifecycle["cleanup_errors"] = errors
            lifecycle["result"] = "failed"
        (output / "fixture-lifecycle.json").write_text(json.dumps(lifecycle, indent=2) + "\n")
        if errors:
            message = "Owned fixture cleanup failed: " + "; ".join(errors)
            if failure is None:
                raise RuntimeError(message)
            print(message, file=sys.stderr, flush=True)
    print("Result: isolated HTTPS desktop session passed and all owned relay resources removed", flush=True)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    main()
