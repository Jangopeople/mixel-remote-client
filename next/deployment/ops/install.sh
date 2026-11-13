#!/usr/bin/env bash
# Installs / updates the Mixel Remote ops pieces on mixel-vps. Run from next/deployment/ops on the
# server as root (copy the folder there first, e.g. scp -r next/deployment/ops mixel-vps:~/mixel-remote-ops).
# Idempotent; backs up every file it replaces to /root/mixel-remote-ops-backup/.
# The probe image is built separately (status-probe.Dockerfile, see OPERATIONS.md).
set -euo pipefail
cd "$(dirname "$0")"
TS=$(date +%Y%m%d-%H%M%S)
BK=/root/mixel-remote-ops-backup
mkdir -p "$BK" && chmod 700 "$BK"
put() { # put <mode> <source> <target>
  if [[ -e "$3" ]] && ! cmp -s "$2" "$3"; then cp -a "$3" "$BK/$(basename "$3").$TS"; fi
  install -D -m "$1" -o root -g root "$2" "$3"
}
put 755 mixel-remote-health.py /usr/local/sbin/mixel-remote-health
put 700 mixel-remote-config-backup.sh /usr/local/sbin/mixel-remote-config-backup
put 700 mixel-remote-restore-test.sh /usr/local/sbin/mixel-remote-restore-test
put 644 systemd/mixel-remote-health.service /etc/systemd/system/mixel-remote-health.service
put 644 systemd/mixel-remote-health.timer /etc/systemd/system/mixel-remote-health.timer
put 644 systemd/mixel-remote-config-backup.service /etc/systemd/system/mixel-remote-config-backup.service
put 644 systemd/mixel-remote-config-backup.timer /etc/systemd/system/mixel-remote-config-backup.timer
put 644 systemd/notify-failure-mixel-remote-health.conf /etc/systemd/system/notify-failure@mixel-remote-health.service.service.d/dedup.conf
put 644 systemd/mixel-backup-alert.conf /etc/systemd/system/mixel-backup.service.d/alert.conf
put 644 nginx/mixel-remote-limits.conf /etc/nginx/conf.d/mixel-remote-limits.conf
put 644 nginx/mixel-remote-limits-ism-api.conf /etc/nginx/snippets/mixel-remote-limits-ism-api.conf
mkdir -p /var/www/mixel-remote-status /etc/mixel-remote-status /var/lib/mixel-remote-health
chmod 755 /var/www/mixel-remote-status; chmod 700 /etc/mixel-remote-status /var/lib/mixel-remote-health

edit() { # edit <file> <marker-that-must-be-unique> <text inserted before|after> <before|after> <guard>
  local file="$1" marker="$2" text="$3" where="$4" guard="$5"
  grep -qF "$guard" "$file" && return 0
  cp -a "$file" "$BK/$(basename "$file").$TS"
  python3 - "$file" "$marker" "$text" "$where" <<'PY'
import sys
path, marker, text, where = sys.argv[1:]
s = open(path).read()
assert s.count(marker) == 1, f"marker not unique in {path}: {marker!r}"
s = s.replace(marker, (text + marker) if where == "before" else (marker + text))
open(path, "w").write(s)
PY
}
edit /etc/nginx/sites-available/ism-api.mixel.lu.conf "    client_max_body_size 50M;"$'\n' \
  "    # Mixel Remote per-IP limits (sign-in, e-mail, account RPCs, enrolment, device tickets)."$'\n'"    include /etc/nginx/snippets/mixel-remote-limits-ism-api.conf;"$'\n' after mixel-remote-limits-ism-api
edit /etc/nginx/sites-available/mixel-remote-next-qa.conf "    location = /v1/ws {"$'\n' \
  "        limit_req zone=mixel_remote_ws burst=1000 nodelay;"$'\n'"        limit_req_status 429;"$'\n' after mixel_remote_ws
edit /etc/nginx/sites-available/remote.mixel.ch.conf "    # Mixel Remote billing service" \
  "$(cat nginx/remote.mixel.ch.status-location.conf)"$'\n\n' before "location = /api/status"
nginx -t
systemctl reload nginx
systemctl daemon-reload
systemctl enable --now mixel-remote-health.timer mixel-remote-config-backup.timer
echo "installed; first run: systemctl start mixel-remote-health && journalctl -u mixel-remote-health -n 40"
