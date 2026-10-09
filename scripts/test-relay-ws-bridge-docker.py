#!/usr/bin/env python3
"""Build/test gateway in disposable Docker namespaces, never the live relay."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import re
import signal
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
SERVER = "rustdesk/rustdesk-server:1.1.15@sha256:10818ec05b179039c6660f4d8e74b303f0db2858bbad2b18e24992ea22d54cd6"


def docker(*arguments, **kwargs):
    kwargs.setdefault("timeout", 180)
    return subprocess.run(["docker", *map(str, arguments)], check=True, **kwargs)


def cleanup(containers, network=None, volume=None):
    errors = []
    commands = [["docker", "rm", "-f", container] for container in reversed(containers)]
    if network:
        commands.append(["docker", "network", "rm", network])
    if volume:
        commands.append(["docker", "volume", "rm", volume])
    for command in commands:
        try:
            result = subprocess.run(command, timeout=10, capture_output=True, text=True)
            absent = re.search(r"no such (?:container|network|volume)|(?:network|volume) .+ not found",
                               result.stderr, re.I)
            if result.returncode and not absent:
                errors.append(" ".join(command[1:]))
        except (OSError, subprocess.TimeoutExpired):
            errors.append(" ".join(command[1:]))
    if errors:
        raise RuntimeError("Task fixture cleanup failed after attempting every resource: " + "; ".join(errors))


def main(skip_build=False, keep_fixture=None):
    if not skip_build:
        docker("build", "-t", "mixel-registration-bridge:test", ROOT / "infra/relay-ws-bridge", timeout=900)
        docker("build", "-f", ROOT / "infra/relay-ws-bridge/Dockerfile.test",
               "-t", "mixel-registration-bridge:fixture", ROOT / "infra/relay-ws-bridge", timeout=900)
    docker("run", "--rm", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           "--entrypoint", "python", "-e", "MIXEL_BRIDGE_MODULE=/app/bridge.py",
           "-v", f"{ROOT}:/work:ro", "mixel-registration-bridge:test",
           "/work/scripts/test-relay-ws-bridge.py")
    name = "mixel-registration-proof-" + uuid.uuid4().hex[:12]
    network, volume, hbbs, hbbr = name + "-net", name + "-data", name + "-hbbs", name + "-hbbr"
    created_containers, created_network, created_volume = [], False, False
    retained = False
    try:
        created_network = True
        docker("network", "create", "--label", "mixel.task=registration-proof", network,
               stdout=subprocess.DEVNULL)
        created_volume = True
        docker("volume", "create", "--label", "mixel.task=registration-proof", volume,
               stdout=subprocess.DEVNULL)
        created_containers.append(hbbs)
        docker("run", "-d", "--name", hbbs, "--label", "mixel.task=registration-proof",
               "--network", network, "--network-alias", "rs.mixel.ch", "-v", f"{volume}:/root",
               SERVER, "hbbs", "-r", "rs.mixel.ch", "-k", "_", stdout=subprocess.DEVNULL)
        if keep_fixture:
            keep_fixture.mkdir(parents=True, exist_ok=False)
        output_context = nullcontext(str(keep_fixture)) if keep_fixture else tempfile.TemporaryDirectory(prefix="mixel-relay-fixture-public-")
        with output_context as temporary:
            pin = Path(temporary) / "fixture-public-pin.txt"
            for attempt in range(50):
                result = subprocess.run(["docker", "cp", f"{hbbs}:/root/id_ed25519.pub", str(pin)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                if result.returncode == 0:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("isolated hbbs did not produce its public fixture identity")
            # Only the public identity leaves the server's private task volume.
            created_containers.append(hbbr)
            docker("run", "-d", "--name", hbbr, "--label", "mixel.task=registration-proof",
                   "--network", f"container:{hbbs}", "-v", f"{volume}:/root", SERVER,
                   "hbbr", "-k", "_", stdout=subprocess.DEVNULL)
            docker("exec", hbbs, "hbbs", "--version")
            docker("exec", hbbr, "hbbr", "--version")
            options = ["--label", "mixel.task=registration-proof", "--network", f"container:{hbbs}",
                       "-e", "MIXEL_BRIDGE_MODULE=/app/bridge.py", "-v", f"{ROOT}:/work:ro",
                       "-v", f"{pin}:/fixture-public-pin.txt:ro"]
            command = ["mixel-registration-bridge:fixture", "/work/scripts/test-relay-ws-bridge-server.py",
                       "--fixture-pin", "/fixture-public-pin.txt"]
            if keep_fixture:
                router = name + "-router"
                created_containers.append(router)
                docker("run", "-d", "--name", router, *options, "-v", f"{keep_fixture}:/fixture-output",
                       *command, "--serve-only", "--export-ca", "/fixture-output/ca.crt", stdout=subprocess.DEVNULL)
                for attempt in range(100):
                    if (keep_fixture / "ca.crt").is_file():
                        health = subprocess.run(["docker", "exec", router, "python", "-c",
                            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz',timeout=2).read()"],
                            capture_output=True, timeout=5)
                        if health.returncode == 0:
                            break
                    time.sleep(0.1)
                else:
                    raise RuntimeError("isolated desktop router did not become ready")
                details = docker("inspect", hbbs, capture_output=True, text=True)
                address = json.loads(details.stdout)[0]["NetworkSettings"]["Networks"][network]["IPAddress"]
                manifest = {"kind": "mixel-isolated-registration-proof", "network": network,
                            "containers": created_containers, "volume": volume, "tls_hostname": "rs.mixel.ch",
                            "fixture_address": address, "public_pin_path": str(pin),
                            "public_test_ca_path": str(keep_fixture / "ca.crt"), "production_mutations": False,
                            "relay_identity": "new fixture key; live baked identity never copied or altered",
                            "server_image": SERVER, "gateway_image": "mixel-registration-bridge:fixture"}
                (keep_fixture / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
                retained = True
                print(json.dumps(manifest, indent=2), flush=True)
            else:
                docker("run", "--rm", *options, *command)
    except BaseException as original:
        # Only task-owned fixture service metadata; keys/IDs/payloads omitted.
        for container in created_containers:
            if isinstance(original, (SystemExit, KeyboardInterrupt)):
                break  # Graceful timeout shutdown prioritizes bounded cleanup.
            try:
                result = subprocess.run(["docker", "logs", "--tail", "80", container],
                                        capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                continue
            output = result.stdout + result.stderr
            for line in output.splitlines():
                if re.search(r"authentication failed|Listening on|relay request|relayrequest|closed:|failure:|failed:", line, re.I):
                    print(re.sub(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", "<fixture-session>", line), flush=True)
        raise
    finally:
        if not retained:
            original_failure = sys.exc_info()[1]
            try:
                cleanup(created_containers, network if created_network else None,
                        volume if created_volume else None)
            except Exception as error:
                if original_failure:
                    if hasattr(original_failure, "add_note"):
                        original_failure.add_note(str(error))
                    else:
                        print(str(error), file=sys.stderr)
                else:
                    raise


if __name__ == "__main__":
    def terminate(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="reuse already built task images")
    parser.add_argument("--keep-fixture", type=Path, help="retain only new task containers and export public CA/pin/manifest for desktop E2E")
    args = parser.parse_args()
    main(args.skip_build, args.keep_fixture)
