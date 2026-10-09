#!/usr/bin/env python3
"""Prepare a reviewable guarded deployment bundle; never contacts production."""
import argparse
import hashlib
from pathlib import Path
import shutil

COMPONENT = Path(__file__).resolve().parent
EXPECTED_BEFORE = "1ebb21d7412fd843350891e46ccc1e6f692123588b2157c3b544b9fdc4613470"
PUBLIC_PIN = "OogSlDx9l+fgs0t6ihF3uTg9emyCv01m8cr4ullarRo="
ANCHOR = "    location / {\n        proxy_pass http://127.0.0.1:8088;"
FILES = ("bridge.py", "requirements.txt", "Dockerfile", "compose.yml", "nginx-location.conf")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def stage_site(source, location):
    if sha256(source) != EXPECTED_BEFORE:
        raise ValueError("Live nginx site differs from the reviewed public snapshot; no change staged")
    text = source.decode()
    if text.count(ANCHOR) != 1 or "location = /ws/id" in text:
        raise ValueError("Expected exact TLS route anchor missing or already changed")
    if location.count("location = /ws/id") != 1 or "proxy_set_header X-Real-IP $remote_addr;" not in location:
        raise ValueError("Unreviewed registration proxy route")
    inserted = "\n".join("    " + line if line else "" for line in location.rstrip().splitlines()) + "\n\n"
    return text.replace(ANCHOR, inserted + ANCHOR, 1).encode()


COMMON = r'''#!/usr/bin/env bash
set -euo pipefail
BASE=/opt/mixel-remote-registration-bridge
SITE=/etc/nginx/sites-available/rs.mixel.ch.conf
BEFORE=@BEFORE@
AFTER=@AFTER@
BACKUP="$BASE/rollback/$BEFORE/site.before.conf"
DOCKER=(docker --host unix:///var/run/docker.sock)
COMPOSE=("${DOCKER[@]}" compose --project-name mixel-remote-registration-bridge --file "$BASE/compose.yml")
hash_file() { sha256sum -- "$1" | cut -d ' ' -f 1; }
guard_bundle() {
    [[ $# == 1 && "$1" =~ ^[0-9a-f]{64}$ ]] || { echo "Supply the reviewed source.sha256 digest" >&2; exit 2; }
    [[ $EUID == 0 ]] || { echo "Run as root only after explicit production approval" >&2; exit 2; }
    [[ "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" == "$BASE" ]] || { echo "Unexpected component directory" >&2; exit 2; }
    [[ "$(hash_file "$BASE/source.sha256")" == "$1" ]] || { echo "Approved source digest mismatch" >&2; exit 2; }
    [[ ! -e "$BASE/.env" ]] || { echo "Unexpected Compose environment file" >&2; exit 2; }
    (cd -- "$BASE" && sha256sum --check --strict source.sha256)
    [[ -f "$SITE" && ! -L "$SITE" ]] || { echo "Unexpected site target" >&2; exit 2; }
    [[ "$(tr -d '\r\n' < /opt/rustdesk/data/id_ed25519.pub)" == '@PIN@' ]] || { echo "Existing public relay identity changed; stopped" >&2; exit 2; }
}
restore_site() {
    [[ "$(hash_file "$SITE")" == "$AFTER" ]] || { echo "Concurrent nginx site change; automatic restore refused" >&2; return 1; }
    [[ "$(hash_file "$BACKUP")" == "$BEFORE" ]] || { echo "Backup digest mismatch" >&2; return 1; }
    local temporary
    temporary=$(mktemp "${SITE}.mixel-XXXXXX")
    cp --preserve=mode,ownership -- "$BACKUP" "$temporary"
    mv -T -- "$temporary" "$SITE"
    nginx -t
    systemctl reload nginx
}
'''

DEPLOY = r'''
guard_bundle "$@"
[[ "$(hash_file "$SITE")" == "$BEFORE" ]] || { echo "Reviewed nginx before digest changed; stopped" >&2; exit 2; }
nginx -t
NATIVE_BEFORE=$("${DOCKER[@]}" inspect --format '{{.Id}} {{.State.StartedAt}} {{.RestartCount}}' hbbs hbbr)
python3 - <<'PY'
import socket
with socket.socket() as listener:
    listener.bind(('127.0.0.1', 8090))
print('Loopback port 8090 is unused')
PY
[[ -z "$("${DOCKER[@]}" ps -aq --filter label=com.docker.compose.project=mixel-remote-registration-bridge)" ]] || { echo "Existing component project; no container replaced" >&2; exit 2; }
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" build
"${COMPOSE[@]}" up -d --no-build --wait --wait-timeout 45
"${COMPOSE[@]}" exec -T registration-bridge python - <<'PY'
import json, urllib.request
assert urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2).read() == b'ok\n'
assert json.load(urllib.request.urlopen('http://127.0.0.1:8090/status', timeout=2))['active_connections'] == 0
print('New source-pinned gateway ready; no existing customer session')
PY
[[ "$(hash_file "$SITE")" == "$BEFORE" ]] || { echo "Concurrent nginx change during image build; site untouched" >&2; exit 2; }
install -d -m 0700 -- "$(dirname -- "$BACKUP")"
if [[ -e "$BACKUP" ]]; then
    [[ "$(hash_file "$BACKUP")" == "$BEFORE" ]] || { echo "Existing backup mismatch" >&2; exit 2; }
else
    cp --preserve=mode,ownership -- "$SITE" "$BACKUP"
fi
[[ "$(hash_file "$BACKUP")" == "$BEFORE" ]] || exit 2
temporary=$(mktemp "${SITE}.mixel-XXXXXX")
cp --preserve=mode,ownership -- "$BASE/nginx-site.after.conf" "$temporary"
chmod --reference="$SITE" "$temporary"
chown --reference="$SITE" "$temporary"
on_failure() {
    code=$?
    trap - ERR
    echo "Deployment failed; restoring only reviewed nginx site" >&2
    restore_site || echo "Site restore/reload failed; inspect exact site before further action" >&2
    exit "$code"
}
trap on_failure ERR
mv -T -- "$temporary" "$SITE"
nginx -t
systemctl reload nginx
[[ "$(hash_file "$SITE")" == "$AFTER" ]]
[[ "$("${DOCKER[@]}" inspect --format '{{.Id}} {{.State.StartedAt}} {{.RestartCount}}' hbbs hbbr)" == "$NATIVE_BEFORE" ]]
trap - ERR
echo "Only new gateway and exact nginx /ws/id route activated; native services and public identity preserved"
echo "Complete dedicated synthetic native and HTTPS desktop verification before reporting deployment complete"
'''

ROLLBACK = r'''
guard_bundle "$@"
[[ "$(hash_file "$BACKUP")" == "$BEFORE" ]] || { echo "Backup digest mismatch; rollback refused" >&2; exit 2; }
case "$(hash_file "$SITE")" in
    "$AFTER") restore_site ;;
    "$BEFORE") echo "Reviewed original route already restored; checking remaining gateway sessions" ;;
    *) echo "Current site differs from this deployment; rollback refused" >&2; exit 2 ;;
esac
"${COMPOSE[@]}" exec -T registration-bridge python - <<'PY'
import json, sys, urllib.request
count = json.load(urllib.request.urlopen('http://127.0.0.1:8090/status', timeout=2))['active_connections']
if count:
    print(f'Original route restored; gateway retained for {count} active connections. Stop only after these finish.')
    sys.exit(10)
print('Original route restored; no active gateway connections')
PY
# No native container, volume, identity, DNS or certificate is touched.
"${COMPOSE[@]}" down --timeout 30
echo "Only the new drained registration component stopped; reviewed original site restored"
'''


def prepare(snapshot, destination):
    before = snapshot.read_bytes()
    after = stage_site(before, (COMPONENT / "nginx-location.conf").read_text())
    destination.mkdir(parents=True, exist_ok=False)
    for name in FILES:
        shutil.copyfile(COMPONENT / name, destination / name)
    (destination / "nginx-site.before.conf").write_bytes(before)
    (destination / "nginx-site.after.conf").write_bytes(after)
    names = list(FILES) + ["nginx-site.before.conf", "nginx-site.after.conf", "deploy.sh", "rollback.sh"]
    common = COMMON.replace("@BEFORE@", EXPECTED_BEFORE).replace("@AFTER@", sha256(after)).replace("@PIN@", PUBLIC_PIN)
    for name, body in (("deploy.sh", DEPLOY), ("rollback.sh", ROLLBACK)):
        (destination / name).write_text(common + body)
        (destination / name).chmod(0o755)
    hashes = "".join(f"{sha256((destination / name).read_bytes())}  {name}\n" for name in names)
    (destination / "source.sha256").write_text(hashes)
    print("Reviewed existing nginx site SHA256:", EXPECTED_BEFORE)
    print("Prepared exact nginx site SHA256:", sha256(after))
    print("Approved source bundle digest (source.sha256):", sha256(hashes.encode()))
    print("Full rewritten site and guarded commands prepared:", destination)
    return sha256(hashes.encode())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.snapshot, args.output)
