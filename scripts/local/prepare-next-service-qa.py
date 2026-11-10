#!/usr/bin/env python3
"""Prepare a private, reviewable Mac internet-service bundle, without deploying.

All output stays in a new repository _scratch directory. The only network-capable
services produced here remain stopped. Private signer/relay secrets are kept out
of the app's public configuration and out of command-line arguments.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_ROOT = "/opt/mixel-remote-next-qa"
ORIGINAL_ISM_SHA = "4219cb5fb98035b9646ffd1de60a40e8da7a5cc0a847e6fd66886be63f19aa30"
PUBLIC_IP = "178.104.17.23"
REDIRECTS = [
    "https://ism.mixel.ch", "https://www.ism.mixel.ch",
    "https://ism.mixel.mu", "https://www.ism.mixel.mu",
    "http://localhost:3000", "http://localhost:5173",
]
NATIVE_REDIRECTS = ["http://127.0.0.1:" + "[0-9]" * size + "/auth/callback[?]state=*" for size in (4, 5)]
AUTHORITY_ROUTE = """    # Mixel Remote native delegation; full bearer remains on its trusted ISM origin.
    location = /v1/device-ticket {
        limit_except POST { deny all; }
        client_max_body_size 8k;
        access_log off;
        proxy_pass http://127.0.0.1:8093;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_connect_timeout 3s;
        proxy_read_timeout 25s;
        proxy_send_timeout 10s;
        proxy_buffering off;
        add_header Cache-Control "no-store" always;
        add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    }

"""
BROKER_NGINX = """server {
    listen 178.104.17.23:9444 ssl;
    server_name rs.mixel.ch;
    ssl_certificate /etc/letsencrypt/live/rs.mixel.ch/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/rs.mixel.ch/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    if ($host != rs.mixel.ch) { return 444; }
    location = /healthz {
        proxy_pass http://127.0.0.1:8092;
        proxy_connect_timeout 3s;
        proxy_read_timeout 5s;
    }
    location = /v1/ws {
        proxy_pass http://127.0.0.1:8092;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 90s;
        proxy_send_timeout 15s;
        proxy_buffering off;
        client_max_body_size 8k;
    }
    location / { return 404; }
}
"""


def patch_ism(source: str) -> str:
    if hashlib.sha256(source.encode()).hexdigest() != ORIGINAL_ISM_SHA:
        raise ValueError("ISM nginx changed since review; refresh the snapshot and review the patch")
    anchor = "    location /rest/v1/"
    if source.count(anchor) != 1 or "/v1/device-ticket" in source:
        raise ValueError("Unexpected existing ISM route layout")
    return source.replace(anchor, AUTHORITY_ROUTE + anchor, 1)


def public_api_key(path: Path) -> str:
    values = [line.split("=", 1)[1].strip().strip("\"'") for line in path.read_text().splitlines()
              if line.startswith("NEXT_PUBLIC_SUPABASE_ANON_KEY=")]
    if len(values) != 1:
        raise ValueError("Exactly one existing public anonymous API key is required")
    value = values[0]
    parts = value.split(".")
    if len(parts) != 3 or not all(parts) or json.loads(base64.urlsafe_b64decode(parts[1] + "===" )).get("role") != "anon":
        raise ValueError("Only an anonymous public API key is allowed")
    if not value.isascii() or any(ord(char) <= 32 or ord(char) >= 127 for char in value):
        raise ValueError("Malformed anonymous API key")
    return value


def compose(backend: str, turn: str) -> dict:
    common = {"network_mode": "host", "read_only": True, "cap_drop": ["ALL"],
              "security_opt": ["no-new-privileges:true"], "restart": "unless-stopped",
              "pids_limit": 128, "mem_limit": "256m", "cpus": 2,
              "pull_policy": "never", "stop_grace_period": "15s",
              "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}}
    return {"name": "mixel-remote-next-qa", "services": {
        "authority": {**common, "image": backend, "user": "10002:10002", "stop_signal": "SIGINT",
                      "entrypoint": ["/usr/local/bin/mixel-identity-authority"], "env_file": ["private/authority.env"],
                      "volumes": [{"type": "bind", "source": "./private/identity.key", "target": "/run/identity.key", "read_only": True}]},
        "broker": {**common, "image": backend, "user": "10001:10001", "stop_signal": "SIGINT",
                   "entrypoint": ["/usr/local/bin/mixel-signaling"], "env_file": ["private/broker.env"]},
        "turn": {**common, "image": turn, "user": "10003:10003", "pids_limit": 64,
                 "command": ["-c", "/run/turnserver.conf"],
                 "tmpfs": ["/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777"],
                 "volumes": [
                     {"type": "bind", "source": "./private/turnserver.conf", "target": "/run/turnserver.conf", "read_only": True},
                     {"type": "bind", "source": "./private/turn-tls", "target": "/run/turn-tls", "read_only": True}]},
    }}


def turn_configuration(secret: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", secret):
        raise ValueError("A fresh 256-bit relay secret is required")
    return "\n".join([
        "listening-port=3479", "tls-listening-port=5350", f"listening-ip={PUBLIC_IP}", f"relay-ip={PUBLIC_IP}",
        "realm=mixel-remote-next-qa", "use-auth-secret", f"static-auth-secret={secret}", "fingerprint",
        "cert=/run/turn-tls/fullchain.pem", "pkey=/run/turn-tls/privkey.pem", "no-tcp-relay", "no-multicast-peers",
        "min-port=60000", "max-port=60099", "user-quota=8", "total-quota=48", "relay-threads=2",
        "userdb=/tmp/turn.sqlite", "pidfile=/tmp/coturn.pid", "log-file=stdout", "simple-log", "no-software-attribute",
        # The two allocations exchange packets on this single public relay IP.
        # Coturn's catch-all IPv6 range also matched the IPv4 peer in live QA.
        f"allowed-peer-ip={PUBLIC_IP}",
        "denied-peer-ip=0.0.0.0-0.255.255.255", "denied-peer-ip=10.0.0.0-10.255.255.255",
        "denied-peer-ip=100.64.0.0-100.127.255.255", "denied-peer-ip=127.0.0.0-127.255.255.255",
        "denied-peer-ip=169.254.0.0-169.254.255.255", "denied-peer-ip=172.16.0.0-172.31.255.255",
        "denied-peer-ip=192.168.0.0-192.168.255.255", "denied-peer-ip=224.0.0.0-255.255.255.255",
        "denied-peer-ip=::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff", "",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ism-nginx-snapshot", type=Path, required=True)
    parser.add_argument("--public-api-key-env", type=Path, required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--turn-image", default="mixel-remote-next-coturn:qa")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to((ROOT / "_scratch").resolve()) or output == (ROOT / "_scratch").resolve():
        parser.error("Use a new private directory inside this repository's _scratch")
    nginx = patch_ism(args.ism_nginx_snapshot.read_text())
    api = public_api_key(args.public_api_key_env)
    ids = []
    for tag in (args.backend_image, args.turn_image):
        value = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", tag], text=True).strip()
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("Build and inspect both immutable service images first")
        ids.append(value)
    previous_mask = os.umask(0o077)
    try:
        output.mkdir(mode=0o700, parents=True)
        private = output / "private"
        private.mkdir(mode=0o700)
        seed = private / "identity.key"
        seed.write_bytes(secrets.token_bytes(32))
        seed.chmod(0o600)
        public = subprocess.check_output([
            "docker", "run", "--rm", "--platform", "linux/amd64", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--user", "10002:10002", "--entrypoint", "/usr/local/bin/mixel-identity-authority",
            "--mount", f"type=bind,src={seed},dst=/run/identity.key,readonly", "--env", "MIXEL_PEER_AUTHORITY_KEY_FILE=/run/identity.key",
            ids[0], "--public-key"], text=True, stderr=subprocess.PIPE).strip()
        if not re.fullmatch(r"[a-f0-9]{64}", public):
            raise ValueError("Native authority did not verify the prepared signer")
        secret = secrets.token_hex(32)
        (private / "authority.env").write_text(f"MIXEL_IDENTITY_BIND=127.0.0.1:8093\nMIXEL_PEER_AUTHORITY_KEY_FILE=/run/identity.key\nMIXEL_ISM_URL=https://ism-api.mixel.ch\nMIXEL_ISM_PUBLIC_KEY={api}\nTOKIO_WORKER_THREADS=2\n")
        (private / "broker.env").write_text(f"MIXEL_SIGNAL_BIND=127.0.0.1:8092\nMIXEL_PEER_AUTHORITY_PUBLIC_KEYS={public}\nMIXEL_WEB_ORIGINS=https://ism.mixel.ch\nMIXEL_TURN_URLS=turn:rs.mixel.ch:3479?transport=udp,turn:rs.mixel.ch:3479?transport=tcp,turns:rs.mixel.ch:5350?transport=tcp\nMIXEL_TURN_SHARED_SECRET={secret}\nMIXEL_TURN_CREDENTIAL_TTL=600\nTOKIO_WORKER_THREADS=2\n")
        (private / "turnserver.conf").write_text(turn_configuration(secret))
        (private / "turn-tls").mkdir(mode=0o700)
        (output / "compose.json").write_text(json.dumps(compose(*ids), indent=2) + "\n")
        (output / "broker.nginx.conf").write_text(BROKER_NGINX)
        (output / "ism-api.nginx.conf").write_text(nginx)
        (output / "ism-api.original.conf").write_bytes(args.ism_nginx_snapshot.read_bytes())
        (output / "gotrue.remote-qa.json").write_text(json.dumps({"services": {"gotrue": {"environment": {
            "GOTRUE_URI_ALLOW_LIST": ",".join(REDIRECTS + NATIVE_REDIRECTS)}}}}, indent=2) + "\n")
        (output / "service-config.json").write_text(json.dumps({"schema": 2, "identity_url": "https://ism-api.mixel.ch", "public_key": api,
            "signaling_url": "wss://rs.mixel.ch:9444/v1/ws", "authority_url": "https://ism-api.mixel.ch", "authority_keys": [public],
            "oauth_provider": "keycloak", "relay_only": True}, indent=2) + "\n")
        shutil.copy2(ROOT / "next/sql/001_device_enrollment.sql", output / "enrollment.sql")
        shutil.copy2(ROOT / "next/deployment/QA-DEPLOYMENT.md", output / "QA-DEPLOYMENT.md")
        metadata = {"productionTouched": False, "deployed": False, "hostname": "mixel-vps-01", "publicIp": PUBLIC_IP,
            "deployRoot": DEPLOY_ROOT, "backendImage": ids[0], "turnImage": ids[1], "publicAuthorityKey": public,
            "originalIsmNginxSha256": ORIGINAL_ISM_SHA, "originalRedirects": REDIRECTS,
            "newRedirects": NATIVE_REDIRECTS, "requiresActionTimeProductionApproval": True,
            "privateFiles": [str(p.relative_to(output)) for p in private.rglob("*") if p.is_file()]}
        (output / "plan.json").write_text(json.dumps(metadata, indent=2) + "\n")
        manifest = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.rglob("*")) if p.is_file()}
        (output / "sha256.json").write_text(json.dumps(manifest, indent=2) + "\n")
    finally:
        os.umask(previous_mask)
    print("PASS: private service bundle prepared; independent public authority root verified by actual binary; no deployment performed")


if __name__ == "__main__":
    main()
