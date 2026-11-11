#!/bin/bash
# Copies the meeting service to the server and restarts it. Run from a clean checkout on the Mac.
# First install only: create /etc/mixel-meet.env (see README) and add nginx-meet.conf to the rs.mixel.ch site.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
host="${1:-mixel-vps}"
(cd "$here" && npm test)
COPYFILE_DISABLE=1 tar --no-xattrs -C "$here" -czf - server.mjs package.json package-lock.json public | ssh "$host" 'set -e; sudo mkdir -p /opt/mixel-meet.new && sudo tar -C /opt/mixel-meet.new -xzf - && cd /opt/mixel-meet.new && sudo npm ci --omit=dev --ignore-scripts --no-audit --no-fund >/dev/null && sudo rm -rf /opt/mixel-meet.old && { [ -d /opt/mixel-meet ] && sudo mv /opt/mixel-meet /opt/mixel-meet.old || true; } && sudo mv /opt/mixel-meet.new /opt/mixel-meet && sudo chown -R root:root /opt/mixel-meet && sudo systemctl restart mixel-meet && sleep 1 && curl -fsS http://127.0.0.1:8097/meet/healthz && echo && sudo rm -rf /opt/mixel-meet.old'
scp -q "$here/deploy/mixel-meet.service" "$host:/tmp/mixel-meet.service.new" && ssh "$host" 'cmp -s /tmp/mixel-meet.service.new /etc/systemd/system/mixel-meet.service || { sudo cp /tmp/mixel-meet.service.new /etc/systemd/system/mixel-meet.service && sudo systemctl daemon-reload && sudo systemctl restart mixel-meet; }; rm -f /tmp/mixel-meet.service.new'
