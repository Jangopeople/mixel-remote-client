#!/usr/bin/env python3
"""Run native Mac WebRTC against an owned loopback-only real coturn fixture.

The self-signed CA is used only by cfg(test) native transports. No system trust
store, existing service, production identity or customer session is modified.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
VENDOR_IMAGE="coturn/coturn@sha256:59af4221a2c45c1195a1a4a865542b3ea744be1dd8e228d11bde87c6631081cf"
IMAGE="mixel-remote-next-coturn:qa"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workspace", action="store_true", help="Run all workspace tests with the real TURN fixture available")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    scratch = (ROOT / "_scratch").resolve()
    if not output.is_relative_to(scratch) or output == scratch or output.exists() or "," in str(output):
        parser.error("Use a new folder inside the repository's _scratch")
    output.mkdir(mode=0o700, parents=True)
    name = "mixel-coturn-local-" + uuid.uuid4().hex
    started = False
    qualified = False
    cleanup_errors: list[str] = []
    previous_mask = os.umask(0o077)
    sensitive = ["root.key", "server.key", "fixture-turnserver.conf", "fixture.json"]

    def run(arguments: list[str], log: str, timeout: int = 60) -> None:
        with (output / log).open("ab") as stream:
            result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                    check=False, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"Fixture operation failed; see {log}")

    def free_port(udp: bool = False) -> int:
        for _ in range(10):
            with socket.socket() as tcp:
                tcp.bind(("127.0.0.1", 0))
                port = tcp.getsockname()[1]
                if not udp:
                    return port
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
                        datagram.bind(("127.0.0.1", port))
                        return port
                except OSError:
                    pass
        raise RuntimeError("No private fixture port is available")

    try:
        inspected=subprocess.run(["docker","image","inspect","--format","{{.Id}}",IMAGE],capture_output=True,text=True,timeout=10,check=False)
        if inspected.returncode:raise RuntimeError("Build the isolated coturn image before running this fixture")
        runtime_image=inspected.stdout.strip()
        openssl = shutil.which("openssl")
        if not openssl:
            raise RuntimeError("OpenSSL is required for the isolated fixture CA")
        run([openssl, "req", "-new", "-newkey", "rsa:2048", "-nodes", "-x509", "-days", "1", "-subj", "/CN=Mixel local TURN fixture CA",
             "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
             "-addext", "subjectKeyIdentifier=hash", "-addext", "authorityKeyIdentifier=keyid:always",
             "-keyout", str(output / "root.key"), "-out", str(output / "root.pem")], "certificate.log")
        run([openssl, "req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1",
             "-keyout", str(output / "server.key"), "-out", str(output / "server.csr")], "certificate.log")
        (output / "server.ext").write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always,issuer\n")
        run([openssl, "x509", "-req", "-in", str(output / "server.csr"), "-CA", str(output / "root.pem"), "-CAkey", str(output / "root.key"),
             "-CAcreateserial", "-days", "1", "-extfile", str(output / "server.ext"), "-out", str(output / "server.pem")], "certificate.log")
        run([openssl, "x509", "-in", str(output / "root.pem"), "-outform", "DER", "-out", str(output / "root.der")], "certificate.log")
        secret = secrets.token_hex(32)
        username = f"{int(time.time()) + 600}:mixel-local-coturn"
        password = base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
        port = free_port(udp=True)
        tls_port = free_port()
        configuration = "\n".join([
            "listening-port=3479", "tls-listening-port=5350", "listening-ip=0.0.0.0", "relay-ip=127.0.0.1",
            "external-ip=127.0.0.1", "realm=mixel-local-coturn", "use-auth-secret", f"static-auth-secret={secret}",
            "cert=/qa/server.pem", "pkey=/qa/server.key", "userdb=/tmp/fixture.sqlite", "pidfile=/tmp/coturn.pid",
            "no-tcp-relay", "no-multicast-peers", "fingerprint",
            # Loopback peers are required only inside this disposable fixture.
            # Deny all other IPv4 peers; never deploy this local-only policy.
            "allow-loopback-peers", "denied-peer-ip=0.0.0.0-255.255.255.255", "allowed-peer-ip=127.0.0.1",
            "min-port=60000", "max-port=60099", "user-quota=8", "total-quota=16", "relay-threads=2",
            "log-file=stdout", "simple-log", "no-software-attribute", "",
        ])
        (output / "fixture-turnserver.conf").write_text(configuration)
        (output / "fixture.json").write_text(json.dumps({"port": port, "tls_port": tls_port, "username": username,
                                                       "password": password, "certificate": str(output / "root.der")}) + "\n")
        started = True
        run(["docker", "run", "--detach", "--name", name, "--platform", "linux/amd64", "--read-only", "--cap-drop", "ALL",
             "--security-opt", "no-new-privileges=true", "--pids-limit", "64", "--memory", "256m", "--user", "10003:10003",
             "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777", "--mount", f"type=bind,src={output},dst=/qa,readonly",
             "--publish", f"127.0.0.1:{port}:3479/udp", "--publish", f"127.0.0.1:{port}:3479/tcp",
             "--publish", f"127.0.0.1:{tls_port}:5350/tcp", runtime_image, "-c", "/qa/fixture-turnserver.conf"], "start.log")
        trust = ssl.create_default_context(cafile=str(output / "root.pem"))
        deadline = time.monotonic() + 15
        while True:
            try:
                with socket.create_connection(("127.0.0.1", tls_port), timeout=1) as connection:
                    with trust.wrap_socket(connection, server_hostname="127.0.0.1"):
                        break
            except (OSError, ssl.SSLError) as error:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"The private coturn TLS listener did not become ready: {error}")
                time.sleep(0.1)
        environment = dict(os.environ)
        environment["MIXEL_LOCAL_COTURN_FIXTURE"] = str(output / "fixture.json")
        command = ["cargo", "test", "--manifest-path", str(ROOT / "next/Cargo.toml"), "--locked"]
        if args.workspace:
            command += ["--workspace", "--all-features"]
        else:
            command += ["-p", "core_protocol", "--lib", "--features", "external-turn-qa", "--offline", "coturn_fixture_tests::"]
        command += ["--", "--test-threads=1", "--nocapture"]
        with (output / "native.log").open("wb") as stream:
            result = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, timeout=420, check=False)
        if result.returncode:
            raise RuntimeError("Workspace tests or native coturn interoperability failed; see native.log")
        qualified = True
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}")
    finally:
        if started:
            try:
                run(["docker", "logs", name], "coturn.log", 10)
                run(["docker", "stop", "--time", "10", name], "stop.log", 20)
                run(["docker", "rm", name], "remove.log", 10)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                result = subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=15, check=False)
                if result.returncode:
                    cleanup_errors.append(name)
        if not cleanup_errors:
            for filename in sensitive:
                (output / filename).unlink(missing_ok=True)
        os.umask(previous_mask)
        receipt = {"qualified": qualified and not cleanup_errors, "coturnImage": IMAGE,"vendorImage":VENDOR_IMAGE, "onlyLoopbackPublished": True,
                   "productionTouched": False, "systemTrustStoreChanged": False, "cleanupErrors": cleanup_errors,
                   "fixtureCredentialsRemoved": all(not (output / filename).exists() for filename in sensitive)}
        (output / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if qualified and not cleanup_errors:
        print("PASS: native Mac WebRTC over real coturn UDP/TCP/TLS; fixture cleaned; no production or system-trust changes")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
