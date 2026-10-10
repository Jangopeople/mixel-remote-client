#!/usr/bin/env python3
"""Test staged source/config guards and optionally nginx syntax offline."""
import argparse
from contextlib import redirect_stdout
import importlib.util
import io
from itertools import product
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "infra/relay-ws-bridge"
SNAPSHOT = COMPONENT / "fixtures/nginx-before-2026-10-09.conf"
NGINX = "nginx:1.30.4@sha256:d5792f71a9496b833bc08ea834a758c46e2b6a6306c10f4be926f38a656cdc1c"
spec = importlib.util.spec_from_file_location("mixel_gateway_deployment", COMPONENT / "prepare-deployment.py")
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)
DOCKER = False


def prepare_bundle(destination):
    with redirect_stdout(io.StringIO()):
        return deployment.prepare(SNAPSHOT, destination)


class DeploymentTests(unittest.TestCase):
    def test_reviewed_full_site_has_only_the_exact_new_route(self):
        before = SNAPSHOT.read_bytes()
        location = (COMPONENT / "nginx-location.conf").read_text()
        after = deployment.stage_site(before, location)
        inserted = "\n".join("    " + line if line else "" for line in location.rstrip().splitlines()) + "\n\n"
        self.assertEqual(after.replace(inserted.encode(), b"", 1), before)
        self.assertEqual(after.count(b"location = /ws/id"), 1)
        self.assertEqual(after.count(b"proxy_set_header X-Real-IP $remote_addr;"), 1)
        self.assertIn(b"limit_except POST OPTIONS { deny all; }", after)
        self.assertEqual(deployment.sha256(before), deployment.EXPECTED_BEFORE)

    def test_changed_snapshot_and_unreviewed_route_fail_closed(self):
        source = SNAPSHOT.read_bytes()
        location = (COMPONENT / "nginx-location.conf").read_text()
        for changed in (source + b"\n", source.replace(b"8088", b"8087")):
            with self.assertRaises(ValueError):
                deployment.stage_site(changed, location)
        with self.assertRaises(ValueError):
            deployment.stage_site(source, location.replace("$remote_addr", "$http_x_real_ip"))

    def test_complete_bundle_scripts_parse_and_preserve_hashes(self):
        with tempfile.TemporaryDirectory(prefix="mixel-deploy-guard-") as temporary:
            bundle = Path(temporary) / "bundle"
            digest = prepare_bundle(bundle)
            self.assertEqual(deployment.sha256((bundle / "source.sha256").read_bytes()), digest)
            for line in (bundle / "source.sha256").read_text().splitlines():
                expected, name = line.split("  ", 1)
                self.assertEqual(deployment.sha256((bundle / name).read_bytes()), expected)
            subprocess.run(["bash", "-n", str(bundle / "deploy.sh"), str(bundle / "rollback.sh")], check=True)
            before = (bundle / "nginx-site.before.conf").read_bytes()
            self.assertEqual(before, SNAPSHOT.read_bytes())

    def test_actual_shell_guards_reject_drift_before_any_infra_command(self):
        if not DOCKER:
            self.skipTest("enable --docker for real offline nginx and shell guards")
        for script, scenario in product(("deploy.sh", "rollback.sh"),
                                        ("approved-digest", "source", "site", "pin", "environment", "backup")):
            if scenario == "backup" and script == "deploy.sh":
                continue
            with self.subTest(script=script, scenario=scenario), tempfile.TemporaryDirectory(prefix="mixel-deploy-guard-") as temporary:
                folder = Path(temporary)
                bundle = folder / "bundle"
                digest = prepare_bundle(bundle)
                site = folder / "site.conf"
                shutil.copyfile(bundle / ("nginx-site.after.conf" if script == "rollback.sh" else "nginx-site.before.conf"), site)
                backup = bundle / "rollback" / deployment.EXPECTED_BEFORE / "site.before.conf"
                backup.parent.mkdir(parents=True)
                shutil.copyfile(SNAPSHOT, backup)
                pin = folder / "public-pin.txt"
                pin.write_text(deployment.PUBLIC_PIN)
                proof = folder / "proof"
                proof.mkdir()
                commands = folder / "commands"
                commands.mkdir()
                for name in ("nginx", "docker", "systemctl"):
                    (commands / name).write_text("#!/bin/sh\ntouch /proof/infra-command-ran\nexit 85\n")
                    (commands / name).chmod(0o755)
                if scenario == "approved-digest":
                    digest = "0" * 64
                elif scenario == "source":
                    with (bundle / "bridge.py").open("a") as file:
                        file.write("\n# unintended source drift\n")
                elif scenario == "site":
                    with site.open("a") as file:
                        file.write("\n# concurrent site edit\n")
                elif scenario == "pin":
                    pin.write_text("different-public-identity")
                elif scenario == "environment":
                    (bundle / ".env").write_text("COMPOSE_PROFILES=unexpected\n")
                else:
                    backup.write_text("unreviewed original site")
                command = ["docker", "run", "--rm", "--network", "none", "--read-only",
                           "--entrypoint", "bash", "-v", f"{bundle}:/opt/mixel-remote-registration-bridge:ro",
                           "-v", f"{site}:/etc/nginx/sites-available/rs.mixel.ch.conf:ro",
                           "-v", f"{pin}:/opt/rustdesk/data/id_ed25519.pub:ro", "-v", f"{proof}:/proof",
                           "-v", f"{commands}:/commands:ro", "-e", "PATH=/commands:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                           NGINX, "/opt/mixel-remote-registration-bridge/" + script, digest]
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((proof / "infra-command-ran").exists(), result.stdout + result.stderr)

    def test_actual_rollback_drains_active_session_and_retries_after_restoration(self):
        if not DOCKER:
            self.skipTest("enable --docker for actual offline rollback commands")
        with tempfile.TemporaryDirectory(prefix="mixel-rollback-drain-") as temporary:
            folder = Path(temporary)
            bundle = folder / "bundle"
            digest = prepare_bundle(bundle)
            site_directory = folder / "sites"
            site_directory.mkdir()
            site = site_directory / "rs.mixel.ch.conf"
            shutil.copyfile(bundle / "nginx-site.after.conf", site)
            backup = bundle / "rollback" / deployment.EXPECTED_BEFORE / "site.before.conf"
            backup.parent.mkdir(parents=True)
            shutil.copyfile(SNAPSHOT, backup)
            pin = folder / "public-pin.txt"
            pin.write_text(deployment.PUBLIC_PIN)
            proof = folder / "proof"
            proof.mkdir()
            commands = folder / "commands"
            commands.mkdir()
            # Stubs only at infrastructure boundaries. Actual generated shell,
            # filesystem/hash restoration and HTTP/Python drain check execute.
            for name in ("nginx", "systemctl"):
                (commands / name).write_text("#!/bin/sh\nprintf '%s\\n' '" + name + "' >> /proof/reloads\n")
                (commands / name).chmod(0o755)
            (commands / "docker").write_text(r'''#!/usr/bin/env python3
import subprocess, sys
from pathlib import Path
arguments = sys.argv[1:]
if 'exec' in arguments:
    sys.exit(subprocess.call([sys.executable, '-']))
if 'down' in arguments:
    with Path('/proof/component-stops').open('a') as file:
        file.write('only reviewed new component stopped\n')
    sys.exit(0)
raise SystemExit('Unexpected infrastructure command: ' + repr(arguments))
''')
            (commands / "docker").chmod(0o755)
            (commands / "status.py").write_text(r'''
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == '/status'
        body=json.dumps({'active_connections': int(Path('/proof/count').read_text()), 'connection_limit':512}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_):
        pass
HTTPServer(('127.0.0.1',8090),Handler).serve_forever()
''')
            (commands / "run.sh").write_text(r'''#!/usr/bin/env bash
set -euo pipefail
python3 /commands/status.py &
server=$!
trap 'kill "$server"' EXIT
python3 - <<'PY'
import socket,time
for _ in range(100):
    try:
        with socket.create_connection(('127.0.0.1',8090), timeout=0.1): break
    except OSError: time.sleep(0.02)
else: raise SystemExit('Task-only status fixture failed to start')
PY
echo 1 > /proof/count
set +e
/opt/mixel-remote-registration-bridge/rollback.sh "$1"
first=$?
set -e
[[ $first == 10 && ! -e /proof/component-stops ]]
cmp /opt/mixel-remote-registration-bridge/nginx-site.before.conf /etc/nginx/sites-available/rs.mixel.ch.conf
[[ $(wc -l < /proof/reloads) == 2 ]]
echo 0 > /proof/count
/opt/mixel-remote-registration-bridge/rollback.sh "$1"
[[ $(wc -l < /proof/component-stops) == 1 && $(wc -l < /proof/reloads) == 2 ]]
cmp /opt/mixel-remote-registration-bridge/nginx-site.before.conf /etc/nginx/sites-available/rs.mixel.ch.conf
echo 'PASS: actual rollback restores original route, retains active session, and second run stops only drained gateway'
''')
            result = subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only",
                "--entrypoint", "bash", "-v", f"{bundle}:/opt/mixel-remote-registration-bridge",
                "-v", f"{site_directory}:/etc/nginx/sites-available", "-v", f"{pin}:/opt/rustdesk/data/id_ed25519.pub:ro",
                "-v", f"{proof}:/proof", "-v", f"{commands}:/commands:ro", "-e",
                "PATH=/commands:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "mixel-registration-bridge:fixture", "/commands/run.sh", digest],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(site.read_bytes(), SNAPSHOT.read_bytes())
            self.assertEqual((proof / "component-stops").read_text().count("stopped"), 1)
            print("PASS: actual rollback first retains an active session; second verified-before run drains/removes only new component", flush=True)

    def test_exact_before_and_after_sites_pass_actual_nginx_1304_offline(self):
        if not DOCKER:
            self.skipTest("enable --docker for actual same-version nginx syntax")
        with tempfile.TemporaryDirectory(prefix="mixel-nginx-offline-") as temporary:
            folder = Path(temporary)
            bundle = folder / "bundle"
            prepare_bundle(bundle)
            certs = folder / "certificates"
            certs.mkdir()
            # Generated only in this task namespace. No production private key
            # is read, copied or included in the configuration test.
            subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "openssl",
                            "-v", f"{certs}:/certificates", "mixel-registration-bridge:fixture", "req", "-x509",
                            "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=rs.mixel.ch",
                            "-keyout", "/certificates/privkey.pem", "-out", "/certificates/fullchain.pem"],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            for kind in ("before", "after"):
                wrapper = bundle / f"nginx-{kind}.conf"
                wrapper.write_text(f"events {{}}\nhttp {{ include /work/nginx-site.{kind}.conf; }}\n")
                result = subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only",
                    "--tmpfs", "/var/log/nginx", "--tmpfs", "/var/cache/nginx", "--tmpfs", "/var/run",
                    "--entrypoint", "nginx", "-v", f"{bundle}:/work:ro",
                    "-v", f"{certs}:/etc/letsencrypt/live/rs.mixel.ch:ro", NGINX,
                    "-t", "-c", f"/work/nginx-{kind}.conf"], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("test is successful", result.stderr)
                print(f"PASS: exact reviewed nginx {kind} site validates offline with nginx 1.30.4 and generated fixture TLS", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", action="store_true")
    args, remaining = parser.parse_known_args()
    DOCKER = args.docker
    unittest.main(argv=[__file__, *remaining], verbosity=2)
