#!/usr/bin/env python3
"""Mixel Remote health monitor (mixel-vps, /usr/local/sbin/mixel-remote-health).

Run every 2 minutes by mixel-remote-health.timer. Checks every part of Mixel Remote as a SaaS,
writes the internal result (/var/lib/mixel-remote-health/latest.json), the public status file
(/var/www/mixel-remote-status/status.json, served as https://remote.mixel.ch/api/status) and decides
whether to alert.

Alerting (de-duplicated here, delivered by the existing ops alarm):
  * a check counts as DOWN after FAIL_AFTER consecutive failures (2 = about 4 minutes; the
    synthetic connection runs every 5 minutes, so it is DOWN after one failed run plus one retry);
  * the run exits 1 (-> OnFailure=notify-failure@mixel-remote-health.service -> e-mail to Michael
    with this run's journal) when a check newly went DOWN, or as a reminder every REMIND_SECONDS
    while something stays DOWN; otherwise it exits 0, also while a known outage continues;
  * when everything is back, one RECOVERED e-mail goes out through the same Resend sender.

Simulating a failure: put an override into /etc/mixel-remote-status/health.env, for example
  MRH_BROKER_LOCAL=http://127.0.0.1:8099/healthz
plus MRH_FORCE_ALL=1 (so checks with a long interval run now), run `systemctl start
mixel-remote-health` twice, then remove the file and run it once more (see OPERATIONS.md).
Usage: mixel-remote-health [--dry-run] [--force-all] [--only name,name]   (--only runs those now)
"""
from __future__ import annotations

import base64
import concurrent.futures
import datetime as dt
import json
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

STATE_DIR = os.environ.get("MRH_STATE_DIR", "/var/lib/mixel-remote-health")
PUBLIC_FILE = os.environ.get("MRH_PUBLIC_FILE", "/var/www/mixel-remote-status/status.json")
OVERRIDES_FILE = "/etc/mixel-remote-status/health.env"
PROBE_ENV = "/etc/mixel-remote-status/probe.env"
PROBE_ARCHIVE = "/var/lib/mixel-remote-status/probe-image.tar"
FAIL_AFTER = 2
REMIND_SECONDS = 3600
CERT_MIN_DAYS = 14
PRESENCE_MAX_AGE = 180
USER_AGENT = "MixelRemoteHealth/1.0"
UPDATE_KEY = bytes([
    0x54, 0x7e, 0x06, 0xc5, 0x2b, 0xf6, 0xa8, 0xcb, 0x75, 0xb2, 0xd8, 0xd6, 0xeb, 0x2d, 0xab, 0x8a,
    0x95, 0x06, 0x8d, 0xa3, 0x66, 0xab, 0xa4, 0xf7, 0x9a, 0x86, 0x3f, 0x70, 0x5a, 0x93, 0x65, 0x49,
])
UPDATE_BASE = "https://store-api.mixel.ch/storage/v1/object/public/remote-updates"
UPDATE_TARGETS = [("windows", "x64"), ("windows", "x64-program"), ("macos", "arm64"), ("linux", "x64")]
STRIPE_WEBHOOK_ID = "we_1UbE0hFRPXnJX22hlR5RA24a"
STRIPE_WEBHOOK_URL = "https://remote.mixel.ch/api/billing/webhook"

# Public components (what customers see on the status page) and their order.
COMPONENTS = [
    ("connections", "Remote connections"),
    ("relay", "Relay (TURN)"),
    ("signin", "Sign-in and accounts"),
    ("portal", "Website and portal"),
    ("viewer", "Web viewer"),
    ("billing", "Billing and invitations"),
    ("updates", "Downloads and updates"),
    ("meetings", "Meetings"),
]


def load_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


OVERRIDES = load_env(OVERRIDES_FILE)


def cfg(name: str, default: str) -> str:
    """A check target; /etc/mixel-remote-status/health.env or the environment may override it."""
    return os.environ.get(name) or OVERRIDES.get(name) or default


class CheckError(Exception):
    pass


def http(url: str, *, method: str = "GET", headers: dict | None = None, body: bytes | None = None,
         timeout: float = 15, max_bytes: int = 2_000_000) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, method=method, data=body,
                                     headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read(max_bytes)
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers or {}), error.read(max_bytes) if error.fp else b""
    except (urllib.error.URLError, socket.timeout, ConnectionError, ssl.SSLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise CheckError(f"{urllib.parse.urlsplit(url).netloc}: {reason}") from None


def expect_status(url: str, expected: int | tuple[int, ...], **kwargs) -> bytes:
    code, _, body = http(url, **kwargs)
    allowed = expected if isinstance(expected, tuple) else (expected,)
    if code not in allowed:
        raise CheckError(f"HTTP {code} (expected {'/'.join(map(str, allowed))})")
    return body


def psql(query: str) -> str:
    result = subprocess.run(
        ["docker", "exec", "-i", "ism-postgres", "psql", "-X", "-tA", "-v", "ON_ERROR_STOP=1",
         "-U", "postgres", "-d", "ism", "-c", query],
        capture_output=True, text=True, timeout=20, check=False)
    if result.returncode != 0:
        raise CheckError("database query failed: " + result.stderr.strip()[:160])
    return result.stdout.strip()


def anon_key() -> str:
    key = load_env("/opt/supabase-ism/.env").get("ANON_KEY", "")
    if not key:
        raise CheckError("ISM anon key not readable")
    return key


# --- checks ---------------------------------------------------------------------------------------
# Each check returns a short detail string or raises CheckError. Metadata: (component, interval s,
# severity). severity "outage" = the component does not work; "degraded" = works, needs attention.

def check_broker_local() -> str:
    body = expect_status(cfg("MRH_BROKER_LOCAL", "http://127.0.0.1:8094/healthz"), 200, timeout=5)
    if body.strip() != b"ok":
        raise CheckError("unexpected answer")
    return "connection broker answers on 127.0.0.1:8094"


def check_broker_public() -> str:
    body = expect_status(cfg("MRH_BROKER_PUBLIC", "https://rs.mixel.ch:9444/healthz"), 200, timeout=10)
    if body.strip() != b"ok":
        raise CheckError("unexpected answer")
    return "wss endpoint rs.mixel.ch:9444 answers"


def check_authority_local() -> str:
    expect_status(cfg("MRH_AUTHORITY_LOCAL", "http://127.0.0.1:8093/healthz"), 200, timeout=5)
    return "identity authority answers on 127.0.0.1:8093"


def check_device_ticket_endpoint() -> str:
    # An empty proof without a bearer must be refused by the authority itself (422 invalid proof or
    # 401 no bearer): proves the nginx -> authority path. 502/504 means the authority is unreachable.
    url = cfg("MRH_DEVICE_TICKET", "https://ism-api.mixel.ch/v1/device-ticket")
    expect_status(url, (401, 422), method="POST", body=b"{}", headers={"Content-Type": "application/json"}, timeout=10)
    return "device ticket endpoint answers and refuses an empty call"


def check_containers() -> str:
    names = ["mixel-remote-next-qa-broker-device-access", "mixel-remote-next-qa-authority-1",
             "mixel-remote-next-qa-turn-1", "mixel-remote-billing", "ism-gotrue", "ism-postgrest", "ism-postgres",
             *[n for n in cfg("MRH_CONTAINERS_EXTRA", "").split(",") if n]]
    result = subprocess.run(["docker", "inspect", "--format", "{{.Name}} {{.State.Status}} {{.RestartCount}}", *names],
                            capture_output=True, text=True, timeout=20, check=False)
    seen = {}
    for line in result.stdout.splitlines():
        name, status, restarts = line.lstrip("/").split()
        seen[name] = (status, restarts)
    bad = [f"{n}={seen.get(n, ('missing', '0'))[0]}" for n in names if seen.get(n, ("missing",))[0] != "running"]
    if bad:
        raise CheckError("not running: " + ", ".join(bad))
    return f"{len(names)} containers running"


def ensure_probe_image(image: str, archive: str, run=subprocess.run, exists=os.path.exists) -> str:
    """The probe's image is used by no standing container, so `docker image prune -a` removes it
    (14 Nov 2026: the check then failed for hours and the status page showed an outage that was
    none). A copy of the image is kept next to the probe's settings; a missing image is loaded
    from it. Returns a note for the check's result ("" when the image was there)."""
    if run(["docker", "image", "inspect", image], capture_output=True, text=True, timeout=30, check=False).returncode == 0:
        return ""
    if not exists(archive):
        raise CheckError(f"probe image {image} is missing and there is no copy at {archive} (rebuild it, see OPERATIONS.md)")
    loaded = run(["docker", "load", "-i", archive], capture_output=True, text=True, timeout=120, check=False)
    if loaded.returncode != 0 or run(["docker", "image", "inspect", image], capture_output=True, text=True, timeout=30, check=False).returncode != 0:
        raise CheckError(f"probe image {image} is missing and its copy could not be loaded: " + (loaded.stderr.strip() or loaded.stdout.strip())[:160])
    return "; the probe image had been removed and was loaded from its copy"


def check_synthetic_connect() -> str:
    image = cfg("MRH_PROBE_IMAGE", "mixel-remote-status-probe:current")
    restored = ensure_probe_image(image, cfg("MRH_PROBE_ARCHIVE", PROBE_ARCHIVE))
    command = ["docker", "run", "--rm", "--network", "host", "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--memory", "256m", "--cpus", "1",
               "--name", f"mixel-remote-status-probe-run-{os.getpid()}", "--env-file", PROBE_ENV]
    ws = cfg("MRH_PROBE_WS", "")
    if ws:
        command += ["-e", f"MIXEL_PROBE_WS={ws}"]
    result = subprocess.run([*command, image, "run"], capture_output=True, text=True, timeout=130, check=False)
    line = (result.stdout.strip().splitlines() or [""])[-1]
    try:
        outcome = json.loads(line)
    except json.JSONDecodeError:
        raise CheckError("probe did not run: " + (result.stderr.strip() or line)[:200]) from None
    steps = ",".join(s["step"] for s in outcome.get("steps", []))
    if not outcome.get("ok"):
        raise CheckError(f"{outcome.get('error')} (after: {steps or 'nothing'})")
    return f"sign-in, tickets, broker, consent, TURN relay data and close in {outcome.get('ms')} ms{restored}"


def check_presence_fresh() -> str:
    age = psql("select coalesce(extract(epoch from now() - max(reported_at))::int, -1) from remote_private.presence_reports")
    seconds = int(age or -1)
    if seconds < 0:
        raise CheckError("no presence report at all")
    if seconds > int(cfg("MRH_PRESENCE_MAX_AGE", str(PRESENCE_MAX_AGE))):
        raise CheckError(f"last presence report {seconds} s ago (limit {PRESENCE_MAX_AGE} s)")
    return f"last presence report {seconds} s ago"


def check_gotrue() -> str:
    body = expect_status(cfg("MRH_GOTRUE", "https://ism-api.mixel.ch/auth/v1/health"), 200,
                         headers={"apikey": anon_key()}, timeout=10)
    name = json.loads(body).get("name", "")
    return f"GoTrue healthy ({name or 'ok'})"


def check_ism_api() -> str:
    body = expect_status(cfg("MRH_ISM_API", "https://ism-api.mixel.ch/rest/v1/rpc/mixel_remote_prices"), 200,
                         method="POST", body=b"{}", timeout=10,
                         headers={"apikey": anon_key(), "Authorization": f"Bearer {anon_key()}",
                                  "Content-Type": "application/json"})
    prices = json.loads(body)
    if not isinstance(prices, list) or len(prices) < 4:
        raise CheckError("price list empty")
    return f"PostgREST answers ({len(prices)} prices)"


def check_portal() -> str:
    base = cfg("MRH_PORTAL", "https://remote.mixel.ch")
    pages = ["/", "/pricing", "/download", "/signin", "/signup", "/status"]
    script = ""
    for page in pages:
        body = expect_status(base + page, 200, timeout=15).decode("utf-8", "replace")
        if 'id="root"' not in body:
            raise CheckError(f"{page}: not the portal page")
        if not script and 'src="/assets/' in body:
            script = body.split('src="/assets/', 1)[1].split('"', 1)[0]
    if not script:
        raise CheckError("portal page without script")
    bundle = expect_status(f"{base}/assets/{script}", 200, timeout=15, max_bytes=10_000_000)
    # A bundle built without .env.local throws at start and shows a blank page (2026-11-13).
    if b"eyJhbGciOi" not in bundle:
        raise CheckError("app bundle built without the ISM sign-in key (blank page)")
    # Mass-rollout endpoints the deploy dialogs hand out.
    for script_path in ("/install/linux.sh", "/install/macos.sh"):
        if not expect_status(base + script_path, 200, timeout=15).startswith(b"#!/bin/sh"):
            raise CheckError(f"{script_path}: not the install script")
    installer = json.loads(expect_status(f"{base}/api/windows-installer", 200, timeout=15))
    if not str(installer.get("url", "")).startswith("https://store-api.mixel.ch/"):
        raise CheckError("/api/windows-installer: no installer address")
    return f"{len(pages)} pages, app bundle, rollout scripts and Windows installer link load"


def check_viewer() -> str:
    body = expect_status(cfg("MRH_VIEWER", "https://ism.mixel.ch/remote-next/"), 200, timeout=15).decode("utf-8", "replace")
    if "<html" not in body.lower():
        raise CheckError("not a page")
    return "ism.mixel.ch/remote-next/ loads"


def check_meetings() -> str:
    expect_status(cfg("MRH_MEET", "https://rs.mixel.ch/meet/"), 200, timeout=10)
    return "rs.mixel.ch/meet/ loads"


def check_billing() -> str:
    body = expect_status(cfg("MRH_BILLING", "https://remote.mixel.ch/api/billing/health"), 200, timeout=10)
    health = json.loads(body)
    if health.get("ok") is not True:
        raise CheckError("billing service not ok")
    return "billing service healthy"


def check_invite_mail() -> str:
    body = expect_status(cfg("MRH_BILLING", "https://remote.mixel.ch/api/billing/health"), 200, timeout=10)
    mail = json.loads(body).get("invite_mail") or {}
    if not mail.get("on"):
        raise CheckError("invitation e-mail sender is off")
    if not mail.get("healthy"):
        raise CheckError("invitation e-mail sender reports an error on its last run")
    last = mail.get("last_run")
    if not last:
        raise CheckError("invitation e-mail sender never ran")
    age = time.time() - dt.datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
    if age > 600:
        raise CheckError(f"invitation e-mail sender last ran {int(age)} s ago")
    stuck = int(psql("select count(*) from remote_saas.invites where mail_state in ('pending','sending') "
                     "and accepted_at is null and revoked_at is null and mail_next_at < now() - interval '15 minutes'"))
    if stuck:
        raise CheckError(f"{stuck} invitation e-mail(s) overdue by more than 15 minutes")
    return f"sender ran {int(age)} s ago, {mail.get('sent', 0)} sent since start, no backlog"


def check_stripe_webhook() -> str:
    key = load_env("/opt/mixel-remote-billing/.env").get("STRIPE_SECRET_KEY", "")
    if not key:
        raise CheckError("Stripe key not readable")
    auth = base64.b64encode(f"{key}:".encode()).decode()
    webhook = cfg("MRH_STRIPE_WEBHOOK", STRIPE_WEBHOOK_ID)
    code, _, body = http(f"https://api.stripe.com/v1/webhook_endpoints/{webhook}",
                         headers={"Authorization": f"Basic {auth}"}, timeout=15)
    if code != 200:
        raise CheckError(f"Stripe API HTTP {code}")
    endpoint = json.loads(body)
    if endpoint.get("status") != "enabled":
        raise CheckError(f"webhook endpoint is {endpoint.get('status')}")
    if endpoint.get("url") != STRIPE_WEBHOOK_URL:
        raise CheckError(f"webhook points to {endpoint.get('url')}")
    return f"webhook {webhook} enabled, {len(endpoint.get('enabled_events', []))} events"


def check_update_manifests() -> str:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key = Ed25519PublicKey.from_public_bytes(UPDATE_KEY)
    base = cfg("MRH_UPDATE_BASE", UPDATE_BASE)
    seen = []
    for platform, arch in UPDATE_TARGETS:
        label = f"{platform}-{arch}"
        body = expect_status(f"{base}/stable/{label}/latest.json", 200, timeout=15, max_bytes=8192)
        try:
            envelope = json.loads(body)
            manifest_bytes = base64.b64decode(envelope["manifest"], validate=True)
            signature = base64.b64decode(envelope["signature"], validate=True)
            key.verify(signature, manifest_bytes)
            manifest = json.loads(manifest_bytes)
        except InvalidSignature:
            raise CheckError(f"{label}: signature does not verify with the Mixel update key") from None
        except (ValueError, KeyError, TypeError):
            raise CheckError(f"{label}: invalid channel file") from None
        if (manifest.get("product"), manifest.get("channel"), manifest.get("platform"), manifest.get("arch")) != \
                ("mixel-remote", "stable", platform, arch):
            raise CheckError(f"{label}: manifest for another target")
        file = str(manifest.get("file", ""))
        if not file.startswith("files/") or ".." in file:
            raise CheckError(f"{label}: unsafe file path")
        code, headers, _ = http(f"{base}/{file}", method="HEAD", timeout=20, max_bytes=0)
        size = int({k.lower(): v for k, v in headers.items()}.get("content-length", "-1"))
        if code != 200 or size != int(manifest.get("size", -2)):
            raise CheckError(f"{label}: installer HTTP {code}, size {size} (manifest {manifest.get('size')})")
        seen.append(f"{label} {manifest.get('version')}")
    return "signed and downloadable: " + ", ".join(seen)


def check_store_downloads() -> str:
    store_env = load_env("/opt/supabase-store/.env")
    service = store_env.get("SERVICE_ROLE_KEY", "")
    if not service:
        raise CheckError("store service key not readable")
    result = subprocess.run(
        ["docker", "exec", "-i", "store-postgres", "psql", "-X", "-tA", "-U", "postgres", "-c",
         "select platform||'-'||arch||coalesce('-'||nullif(variant,''),''), file_path, size_bytes "
         "from store_app_releases where app_slug = 'mixel-remote' and is_current "
         "and coalesce(variant,'') not like 'previous%' order by 1"],
        capture_output=True, text=True, timeout=20, check=False)
    rows = [line.split("|") for line in result.stdout.strip().splitlines() if line.count("|") == 2]
    if not rows:
        raise CheckError("no current Mixel Remote downloads in the store")
    api = cfg("MRH_STORE_API", "https://store-api.mixel.ch/storage/v1")
    bad, good = [], 0
    for label, path, size in rows:
        quoted = urllib.parse.quote(path)
        code, _, body = http(f"{api}/object/sign/native-builds/{quoted}", method="POST", timeout=15,
                             body=json.dumps({"expiresIn": 60}).encode(),
                             headers={"Authorization": f"Bearer {service}", "apikey": service,
                                      "Content-Type": "application/json"})
        if code != 200:
            bad.append(f"{label}: sign HTTP {code}")
            continue
        signed = json.loads(body).get("signedURL", "")
        code, headers, _ = http(f"{api}{signed}", headers={"Range": "bytes=0-0"}, timeout=20, max_bytes=16)
        total = {k.lower(): v for k, v in headers.items()}.get("content-range", "").rsplit("/", 1)[-1]
        if code not in (200, 206) or (total.isdigit() and int(total) != int(size)):
            bad.append(f"{label}: HTTP {code}, size {total} (catalogue {size})")
        else:
            good += 1
    store_page = expect_status(cfg("MRH_STORE_PAGE", "https://store.mixel.ch/app/mixel-remote"), 200, timeout=15)
    if bad:
        raise CheckError("; ".join(bad))
    return f"{good} current download links answer, store page {len(store_page)} bytes"


def cert_days(host: str, port: int, *, server_name: str | None = None) -> int:
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=10) as raw:
        with context.wrap_socket(raw, server_hostname=server_name or host) as tls:
            not_after = tls.getpeercert()["notAfter"]
            try:
                tls.unwrap()  # clean close, no "connection reset" noise in the server logs
            except (OSError, ssl.SSLError):
                pass
    return int((ssl.cert_time_to_seconds(not_after) - time.time()) // 86400)


def origin_cert_days(name: str) -> int:
    result = subprocess.run(["openssl", "x509", "-noout", "-enddate", "-in", f"/etc/letsencrypt/live/{name}/fullchain.pem"],
                            capture_output=True, text=True, timeout=10, check=False)
    if result.returncode != 0:
        raise CheckError(f"{name}: origin certificate not readable")
    end = result.stdout.strip().split("=", 1)[1]
    return int((ssl.cert_time_to_seconds(end) - time.time()) // 86400)


def check_tls() -> str:
    targets = [("rs.mixel.ch", 443), ("rs.mixel.ch", 9444), ("rs.mixel.ch", 5350), ("ism-api.mixel.ch", 443),
               ("remote.mixel.ch", 443), ("ism.mixel.ch", 443), ("store-api.mixel.ch", 443), ("store.mixel.ch", 443)]
    minimum = int(cfg("MRH_CERT_MIN_DAYS", str(CERT_MIN_DAYS)))
    low, report = [], []
    for host, port in targets:
        try:
            days = cert_days(host, port)
        except (OSError, ssl.SSLError, KeyError) as error:
            low.append(f"{host}:{port} {error}")
            continue
        report.append(f"{host}:{port} {days}d")
        if days < minimum:
            low.append(f"{host}:{port} expires in {days} days")
    # Origin certificates behind Cloudflare (Full strict needs them valid too).
    for name in ("remote.mixel.ch", "store-api.mixel.ch"):
        try:
            days = origin_cert_days(name)
        except CheckError as error:
            low.append(str(error))
            continue
        report.append(f"{name} origin {days}d")
        if days < minimum:
            low.append(f"{name} origin expires in {days} days")
    if low:
        raise CheckError("; ".join(low))
    return "all > %d days: %s" % (minimum, ", ".join(report))


def check_turn_ports() -> str:
    """STUN binding over UDP 3479 (what clients use first) and a clean TLS handshake on 5350.
    (A bare TCP connect makes coturn log "Connection reset by peer" on every run.)"""
    host = cfg("MRH_TURN_HOST", "178.104.17.23")
    udp_port = int(cfg("MRH_TURN_PORT_3479", "3479"))
    tls_port = int(cfg("MRH_TURN_PORT_5350", "5350"))
    transaction = os.urandom(12)
    request = b"\x00\x01\x00\x00\x21\x12\xa4\x42" + transaction
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.settimeout(3)
        answer = b""
        for _ in range(3):
            try:
                udp.sendto(request, (host, udp_port))
                answer, _ = udp.recvfrom(2048)
                break
            except OSError:
                continue
    if len(answer) < 20 or answer[:2] != b"\x01\x01" or answer[8:20] != transaction:
        raise CheckError(f"TURN {host}:{udp_port}/udp: no STUN binding answer")
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, tls_port), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname="rs.mixel.ch") as tls:
                tls.unwrap()
    except (OSError, ssl.SSLError) as error:
        if not isinstance(error, ssl.SSLZeroReturnError):
            raise CheckError(f"TURN {host}:{tls_port}/tls: {error}") from None
    return "TURN answers STUN on UDP 3479 and TLS on 5350"


def check_backups() -> str:
    """The nightly encrypted ISM database backup (mixel-backup.service) and the config backup ran."""
    import glob
    now = time.time()
    max_hours = float(cfg("MRH_BACKUP_MAX_HOURS", "26"))
    problems, report = [], []
    for label, pattern, max_age, min_size in (
            ("ism-postgres", "/opt/pg-backups-encrypted/ism-postgres-*.dump.gz.age", max_hours * 3600, 50_000_000),
            ("remote-config", "/opt/pg-backups-encrypted/remote-config/mixel-remote-config-*.age", max_hours * 3600, 1000)):
        files = sorted(glob.glob(pattern), key=os.path.getmtime)
        if not files:
            problems.append(f"{label}: no archive")
            continue
        newest = files[-1]
        age, size = now - os.path.getmtime(newest), os.path.getsize(newest)
        report.append(f"{label} {int(age / 3600)}h old, {size // 1_000_000} MB")
        if age > max_age:
            problems.append(f"{label}: newest archive {int(age / 3600)} h old")
        if size < min_size:
            problems.append(f"{label}: newest archive only {size} bytes")
    result = subprocess.run(["systemctl", "show", "mixel-backup.service", "-p", "Result", "--value"],
                            capture_output=True, text=True, timeout=10, check=False)
    if result.stdout.strip() not in ("success", ""):
        problems.append(f"mixel-backup.service last result {result.stdout.strip()}")
    if problems:
        raise CheckError("; ".join(problems))
    return ", ".join(report)


# name: (function, component, interval seconds, severity). Component "internal" is alerted on but
# not shown on the public status page.
CHECKS = {
    "broker_local": (check_broker_local, "connections", 0, "outage"),
    "broker_public": (check_broker_public, "connections", 0, "outage"),
    "authority": (check_authority_local, "connections", 0, "outage"),
    "device_ticket": (check_device_ticket_endpoint, "connections", 0, "outage"),
    "synthetic_connect": (check_synthetic_connect, "connections", 270, "outage"),
    "presence_fresh": (check_presence_fresh, "connections", 0, "degraded"),
    "containers": (check_containers, "connections", 0, "outage"),
    "turn_ports": (check_turn_ports, "relay", 0, "outage"),
    "gotrue": (check_gotrue, "signin", 0, "outage"),
    "ism_api": (check_ism_api, "signin", 0, "outage"),
    "portal": (check_portal, "portal", 0, "outage"),
    "viewer": (check_viewer, "viewer", 0, "outage"),
    "meetings": (check_meetings, "meetings", 0, "outage"),
    "billing": (check_billing, "billing", 0, "outage"),
    "invite_mail": (check_invite_mail, "billing", 0, "degraded"),
    "stripe_webhook": (check_stripe_webhook, "billing", 900, "degraded"),
    "update_manifests": (check_update_manifests, "updates", 600, "outage"),
    "store_downloads": (check_store_downloads, "updates", 900, "outage"),
    "tls": (check_tls, "portal", 3600, "degraded"),
    "backups": (check_backups, "internal", 3600, "degraded"),
}
# The synthetic connection proves the relay too.
ALSO_COUNTS_FOR = {"synthetic_connect": ["relay"]}


def now_iso(at: float | None = None) -> str:
    return dt.datetime.fromtimestamp(at or time.time(), dt.timezone.utc).isoformat(timespec="seconds")


def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: str, value, mode: int = 0o600) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=1, sort_keys=False)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def run_check(name: str) -> tuple[str, bool, str, float]:
    started = time.time()
    try:
        detail = CHECKS[name][0]()
        return name, True, detail, time.time() - started
    except CheckError as error:
        return name, False, str(error), time.time() - started
    except Exception as error:  # noqa: BLE001 - a crashing check is a failed check, never a dead monitor
        return name, False, f"check crashed: {type(error).__name__}: {error}"[:300], time.time() - started


def send_recovery_mail(down_before: list[str], outage_seconds: int) -> None:
    key = load_env("/etc/newsletter-send/.env").get("RESEND_API_KEY", "")
    if not key:
        print("[mixel-remote-health] recovery: no Resend key, mail skipped", file=sys.stderr)
        return
    host = socket.gethostname()
    body = json.dumps({
        "from": "MIXEL Ops Alarm <bestellungen@mixel.ch>",
        "to": ["michael@mixel.ch"],
        "subject": f"[ops-alarm] mixel-remote-health RECOVERED on {host}",
        "text": (f"All Mixel Remote checks pass again at {now_iso()} after about {outage_seconds // 60} minutes.\n"
                 f"Were down: {', '.join(down_before)}\n\nStatus: https://remote.mixel.ch/status\n"
                 "Details: journalctl -u mixel-remote-health -e"),
    }).encode()
    try:
        code, _, _ = http("https://api.resend.com/emails", method="POST", body=body, timeout=15,
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        print(f"[mixel-remote-health] recovery mail HTTP {code}")
    except CheckError as error:
        print(f"[mixel-remote-health] recovery mail failed: {error}", file=sys.stderr)


def main() -> int:
    args = sys.argv[1:]
    dry = "--dry-run" in args
    force = "--force-all" in args or cfg("MRH_FORCE_ALL", "") == "1"
    only = None
    if "--only" in args:
        only = set(args[args.index("--only") + 1].split(","))
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    state = load_json(f"{STATE_DIR}/state.json", {"checks": {}, "alert": {}})
    checks_state = state.setdefault("checks", {})
    now = time.time()

    due = []
    for name, (_, _, interval, _) in CHECKS.items():
        if only and name not in only:
            continue
        previous = checks_state.get(name, {})
        # Failed checks are retried at the next run even if their interval is long.
        if force or only or not interval or not previous or not previous.get("ok") or now - previous.get("at", 0) >= interval:
            due.append(name)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run_check, due))

    for name, ok, detail, seconds in results:
        previous = checks_state.get(name, {})
        failures = 0 if ok else previous.get("failures", 0) + 1
        since = previous.get("since", now) if previous.get("ok") == ok else now
        checks_state[name] = {"ok": ok, "detail": detail, "at": now, "seconds": round(seconds, 2),
                              "failures": failures, "since": since}
        print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail} ({seconds:.1f}s)")
    for name in list(checks_state):
        if name not in CHECKS:
            del checks_state[name]

    down = sorted(n for n, c in checks_state.items() if not c["ok"] and c["failures"] >= FAIL_AFTER)
    failing = sorted(n for n, c in checks_state.items() if not c["ok"])

    # Components and their public state.
    components = []
    for component_id, title in COMPONENTS:
        members = [n for n, meta in CHECKS.items() if meta[1] == component_id or component_id in ALSO_COUNTS_FOR.get(n, [])]
        status = "operational"
        for name in members:
            if name in down:
                status = "outage" if CHECKS[name][3] == "outage" else ("degraded" if status == "operational" else status)
        since_values = [checks_state[n]["since"] for n in members if n in checks_state and n in down]
        components.append({"id": component_id, "name": title, "status": status,
                           "since": now_iso(min(since_values)) if since_values else None})
    worst = "outage" if any(c["status"] == "outage" for c in components) else (
        "degraded" if any(c["status"] == "degraded" for c in components) else "operational")

    # 90-day daily history per component (share of runs without a DOWN check).
    history = load_json(f"{STATE_DIR}/history.json", {})
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    for component in components:
        days = history.setdefault(component["id"], {})
        day = days.setdefault(today, {"runs": 0, "ok": 0, "worst": "operational"})
        day["runs"] += 1
        day["ok"] += int(component["status"] == "operational")
        rank = {"operational": 0, "degraded": 1, "outage": 2}
        if rank[component["status"]] > rank[day["worst"]]:
            day["worst"] = component["status"]
        for old in sorted(days)[:-90]:
            del days[old]

    public = {
        "updated_at": now_iso(now),
        "status": worst,
        "components": [
            {**c, "history": [{"date": d, "uptime": round(100 * v["ok"] / v["runs"], 2) if v["runs"] else None,
                               "worst": v["worst"]} for d, v in sorted(history.get(c["id"], {}).items())]}
            for c in components
        ],
    }
    internal = {"timestamp": now_iso(now), "ok": not failing, "failures": len(failing), "down": down,
                "checks": [{"name": n, "status": "ok" if c["ok"] else "fail", "detail": c["detail"],
                            "failures": c["failures"], "at": now_iso(c["at"])} for n, c in sorted(checks_state.items())]}

    # Alert decision (de-duplication lives here).
    alert = state.setdefault("alert", {})
    previously_down = alert.get("down", [])
    newly_down = [n for n in down if n not in previously_down]
    exit_code = 0
    if newly_down:
        print(f"[ALERT] newly down: {', '.join(newly_down)}; down now: {', '.join(down)}", file=sys.stderr)
        alert.update(last_sent=now, outage_started=alert.get("outage_started") or now)
        exit_code = 1
    elif down and now - alert.get("last_sent", 0) >= REMIND_SECONDS:
        print(f"[ALERT] still down after {int((now - alert.get('outage_started', now)) / 60)} min: {', '.join(down)}", file=sys.stderr)
        alert["last_sent"] = now
        exit_code = 1
    elif down:
        print(f"[known] still down (alert sent {int(now - alert.get('last_sent', now))} s ago): {', '.join(down)}")
    if not down and previously_down:
        print(f"[RECOVERED] {', '.join(previously_down)}")
        if not dry:
            send_recovery_mail(previously_down, int(now - alert.get("outage_started", now)))
        alert.pop("outage_started", None)
    alert["down"] = down
    for name in down:
        detail = checks_state[name]["detail"]
        print(f"[DOWN] {name} ({checks_state[name]['failures']} runs): {detail}", file=sys.stderr if exit_code else sys.stdout)

    if not dry:
        write_json(f"{STATE_DIR}/state.json", state)
        write_json(f"{STATE_DIR}/history.json", history)
        write_json(f"{STATE_DIR}/latest.json", internal)
        write_json(PUBLIC_FILE, public, mode=0o644)
    print(f"RESULT={worst.upper()} checks={len(checks_state)} failing={len(failing)} down={len(down)} ran={len(due)}")
    return exit_code


def guarded_main() -> int:
    """A crash of the monitor itself alerts too, but at most once per REMIND_SECONDS."""
    try:
        return main()
    except Exception as error:  # noqa: BLE001
        print(f"[mixel-remote-health] monitor crashed: {type(error).__name__}: {error}", file=sys.stderr)
        stamp = "/run/mixel-remote-health.crash"
        try:
            if time.time() - os.path.getmtime(stamp) < REMIND_SECONDS:
                return 0
        except OSError:
            pass
        try:
            with open(stamp, "w", encoding="utf-8") as handle:
                handle.write(now_iso())
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(guarded_main())
