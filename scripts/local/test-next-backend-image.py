#!/usr/bin/env python3
"""Exercise the two Mac connection services in isolated local Docker containers.

Publishes no ports, uses no production identity or private key, and removes only
its own named containers. A fixed public test-vector seed must never be deployed.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = "/usr/local/bin/mixel-identity-authority"
BROKER = "/usr/local/bin/mixel-signaling"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    scratch = (ROOT / "_scratch").resolve()
    if output == scratch or not output.is_relative_to(scratch) or output.exists():
        parser.error("Use a new output folder inside the repository's _scratch")
    output.mkdir(mode=0o700, parents=True)
    # Use an actual Linux-owned Docker volume. Docker Desktop's macOS bind
    # sharing may remap file ownership to each container's effective UID, which
    # cannot exercise a foreign-owner denial accurately.
    seed=Path("PUBLIC-TEST-VECTOR.seed");zero=Path("zero.seed");link=Path("linked.seed")
    volume="mixel-next-local-"+uuid.uuid4().hex
    volume_created=False
    qualified=False
    containers: list[str] = []
    checks: list[str] = []
    cleanup_errors: list[str] = []

    def run(arguments: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
        if arguments and arguments[0]=="run" and "--platform" not in arguments:
            arguments=["run","--platform","linux/amd64",*arguments[1:]]
        return subprocess.run(["docker", *arguments], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, check=False)

    def require(value: bool, message: str) -> None:
        if not value:
            raise RuntimeError(message)

    def common(image: str, entry: str, owner: str | None = None) -> list[str]:
        return ["--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
                "--pids-limit", "64", "--memory", "256m", "--user", owner or f"{os.getuid()}:{os.getgid()}",
                "--mount", f"type=volume,src={volume},dst=/qa,readonly",
                "--entrypoint", entry]

    def public_key(image: str, key: str = seed.name, owner: str | None = None) -> subprocess.CompletedProcess:
        return run(["run", "--rm", *common(image, AUTHORITY, owner), "--env", f"MIXEL_PEER_AUTHORITY_KEY_FILE=/qa/{key}", image, "--public-key"])

    def request(name: str, port: int, raw: str) -> bytes:
        # Static ports and a shell-quoted literal request; no user/remote command
        # text is interpolated. No bearer/private-key bytes enter this command.
        quoted = "'" + raw.replace("'", "'\\''") + "'"
        script = f"exec 3<>/dev/tcp/127.0.0.1/{port}; printf %s {quoted} >&3; cat <&3"
        result = run(["exec", name, "/bin/bash", "-c", script], timeout=5)
        require(result.returncode == 0, "Private health request failed")
        return result.stdout

    def status(response: bytes) -> int:
        first = response.split(b"\r\n", 1)[0].split()
        require(len(first) >= 2 and first[1].isdigit(), "Malformed service HTTP response")
        return int(first[1])

    def start(image: str, entry: str, environment: dict[str, str], port: int) -> str:
        name = "mixel-next-local-" + uuid.uuid4().hex
        arguments = ["run", "--detach", "--name", name, *common(image, entry)]
        for key, value in environment.items():
            arguments.extend(["--env", f"{key}={value}"])
        containers.append(name)
        result = run([*arguments, image])
        require(result.returncode == 0, "Cannot start the isolated service")
        deadline = time.monotonic() + 10
        while True:
            try:
                response = request(name, port, "GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                if status(response) == 200:
                    break
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
            require(time.monotonic() < deadline, "The isolated service did not become healthy")
            time.sleep(0.05)
        inspection = run(["inspect", name])
        require(inspection.returncode == 0, "Cannot inspect isolated network policy")
        container = json.loads(inspection.stdout)[0]
        require(container["HostConfig"]["NetworkMode"] == "none" and not container["NetworkSettings"]["Ports"], "Fixture unexpectedly exposes a network port")
        return name

    try:
        inspection = run(["image", "inspect", "--format", "{{.Id}}", args.image])
        require(inspection.returncode == 0, "Build the backend image before running verification")
        image = inspection.stdout.decode().strip()
        require(re.fullmatch(r"sha256:[a-f0-9]{64}", image) is not None, "Invalid immutable image reference")
        created=run(["volume","create","--label","ch.mixel.task=remote-backend-preflight",volume])
        require(created.returncode==0,"Cannot create the owned Linux fixture volume");volume_created=True
        setup=f"umask 077; printf %s '********************************' >/qa/{seed.name}; dd if=/dev/zero of=/qa/{zero.name} bs=32 count=1 status=none; ln -s {seed.name} /qa/{link.name}; chmod 700 /qa; chmod 600 /qa/{seed.name} /qa/{zero.name}; chown -hR {os.getuid()}:{os.getgid()} /qa"
        initialized=run(["run","--rm","--network","none","--read-only","--user","0:0","--mount",f"type=volume,src={volume},dst=/qa","--entrypoint","/bin/bash",image,"-euc",setup])
        require(initialized.returncode==0,"Cannot initialize private Linux-owned fixture files")
        valid = public_key(image)
        public = valid.stdout.decode().strip()
        require(valid.returncode == 0 and re.fullmatch(r"[a-f0-9]{64}", public) is not None, "The private owner could not load the test-vector signer")
        checks.append("Signer accepts only its private regular file; prints only the public key")
        changed=run(["run","--rm","--network","none","--read-only","--user",f"{os.getuid()}:{os.getgid()}","--mount",f"type=volume,src={volume},dst=/qa","--entrypoint","/bin/chmod",image,"644",f"/qa/{seed.name}"]);require(changed.returncode==0,"Cannot configure the world-readable negative fixture")
        denied = public_key(image)
        require(denied.returncode != 0 and not denied.stdout, "World-readable signer was accepted")
        changed=run(["run","--rm","--network","none","--read-only","--user",f"{os.getuid()}:{os.getgid()}","--mount",f"type=volume,src={volume},dst=/qa","--entrypoint","/bin/chmod",image,"600",f"/qa/{seed.name}"]);require(changed.returncode==0,"Cannot restore private fixture permissions")
        for key in (zero.name, link.name):
            denied = public_key(image, key)
            require(denied.returncode != 0 and not denied.stdout, "Zero or symlink signer was accepted")
        denied = public_key(image, owner="10001:10001")
        require(denied.returncode != 0 and not denied.stdout, "A different UID could load the signer")
        checks.append("Signer rejects world-readable, foreign-owned, all-zero and symlink files")
        authority = start(image, AUTHORITY, {"MIXEL_PEER_AUTHORITY_KEY_FILE": f"/qa/{seed.name}",
                          "MIXEL_ISM_URL": "https://isolated.mixel-remote.test", "MIXEL_ISM_PUBLIC_KEY": "public-fixture-only"}, 8093)
        proof=json.dumps({"challenge":{"nonce":[1]*32,"device":[1]*16,"profile":[2]*16,"tenant":None,"connection_id":123456789,"generation":1},"issued_at":int(time.time()),"signature":[0]*64},separators=(",",":"))
        headers=f"POST /v1/device-ticket HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {len(proof)}\r\nConnection: close\r\n"
        response=request(authority,8093,headers+"\r\n"+proof)
        require(status(response)==401,"A well-formed unauthenticated device-ticket request was not denied")
        response=request(authority,8093,headers+"Origin: https://untrusted.mixel-remote.test\r\n\r\n"+proof)
        require(status(response)==403,"A well-formed browser-origin request was not forbidden")
        checks.append("Authority starts privately, serves health, and refuses unauthenticated/browser requests")
        start(image, BROKER, {"MIXEL_PEER_AUTHORITY_PUBLIC_KEYS": public,
                             "MIXEL_WEB_ORIGINS": "https://isolated.mixel-remote.test", "MIXEL_ALLOW_LAN_ONLY": "1",
                             "MIXEL_SUPPORT_POLICY_URL": "http://127.0.0.1:8999/api/internal/remote/authorize",
                             "MIXEL_SUPPORT_POLICY_TOKEN": "11" * 32}, 8092)
        checks.append("Broker starts with the authority public key and policy configuration; no host ports are published")
        failed = run(["run", "--rm", *common(image, BROKER), image])
        require(failed.returncode != 0, "An unconfigured broker started")
        checks.append("Missing trust configuration refuses broker startup")
        for name in containers:
            stopped = run(["stop", "--time", "10", name])
            require(stopped.returncode == 0, "Isolated service stop failed")
            code = run(["inspect", "--format", "{{.State.ExitCode}}", name])
            require(code.returncode == 0 and code.stdout.strip() == b"0", "Service did not exit cleanly after its configured SIGINT")
        checks.append("Both service processes exit cleanly on the configured graceful stop signal")
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}")
        qualified = False
    else:
        qualified = True
    finally:
        for name in containers:
            try:
                result = run(["rm", "--force", name])
                if result.returncode:
                    cleanup_errors.append(name)
            except (OSError, subprocess.SubprocessError):
                cleanup_errors.append(name)
        volume_removed=not volume_created
        if volume_created:
            try:
                removed=run(["volume","rm",volume]);volume_removed=removed.returncode==0
                if not volume_removed:cleanup_errors.append(volume)
            except (OSError,subprocess.SubprocessError):cleanup_errors.append(volume)

        receipt = {"qualified": qualified and not cleanup_errors, "checks": checks,
                   "publishedPorts": False, "productionTouched": False, "testVectorRemoved": volume_removed,
                   "cleanupErrors": cleanup_errors}
        (output / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
    for check in checks:
        print(f"PASS: {check}")
    if cleanup_errors:
        print("FAIL: an owned local container could not be removed")
    return 0 if qualified and not cleanup_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
