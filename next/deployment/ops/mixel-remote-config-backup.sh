#!/usr/bin/env bash
# Mixel Remote configuration and key backup (mixel-vps, /usr/local/sbin/mixel-remote-config-backup).
# Daily by mixel-remote-config-backup.timer. The database is in the nightly mixel-backup
# (ism-postgres pg_dumpall); this adds what that job does not cover:
#   /opt/mixel-remote-next-qa   broker/authority/TURN config, compose.json, identity.key (signer),
#                               broker env files (presence token, TURN secret), TURN TLS copies
#   /opt/mixel-remote-billing   compose.yml and .env (Stripe key, webhook secret, DB login)
#   /etc/mixel-remote-status    status probe account and probe computer keys
#   /etc/mixel-meet.env         Mixel Meet settings
#   units and scripts           mixel-remote-* units, /usr/local/sbin/mixel-remote-*
# Encrypted with age to the backup recipients of /etc/mixel-backup.env (AGE_RECIPIENT and the ISM
# recovery recipient), uploaded to $RCLONE_REMOTE/remote-config/, kept 90 days there and 3 days
# locally in /opt/pg-backups-encrypted/remote-config (root 0700). Plain files never leave tmpfs.
set -euo pipefail
umask 077
source /etc/mixel-backup.env
: "${AGE_RECIPIENT:?}" "${RCLONE_REMOTE:?}"
RCLONE_CONFIG_PATH="${RCLONE_CONFIG_PATH:-/root/.config/rclone/rclone.conf}"
LOCAL=/opt/pg-backups-encrypted/remote-config
mkdir -p "$LOCAL"
chmod 700 "$LOCAL"
STAMP=$(date -u +%Y%m%d-%H%M)
NAME="mixel-remote-config-$STAMP.tar.gz.age"
recipients=(--recipient "$AGE_RECIPIENT")
if [[ -n ${ISM_AGE_RECIPIENT:-} && "$ISM_AGE_RECIPIENT" != "$AGE_RECIPIENT" ]]; then
  recipients+=(--recipient "$ISM_AGE_RECIPIENT")
fi

shopt -s nullglob
paths=(/opt/mixel-remote-next-qa /opt/mixel-remote-billing/compose.yml /opt/mixel-remote-billing/.env
       /etc/mixel-remote-status /etc/mixel-meet.env /etc/systemd/system/mixel-remote-* /etc/systemd/system/mixel-meet.service
       /usr/local/sbin/mixel-remote-* /etc/nginx/conf.d/mixel-remote-limits.conf /etc/nginx/snippets/mixel-remote-limits-ism-api.conf)
shopt -u nullglob
[[ -s /opt/mixel-remote-next-qa/private/identity.key ]] || { echo "[remote-config-backup] identity.key missing" >&2; exit 1; }
existing=()
for path in "${paths[@]}"; do [[ -e "$path" ]] && existing+=("$path"); done

# images.tar (rebuildable image archive) and old backup folders are left out to keep it small.
tar --exclude='opt/mixel-remote-next-qa/images.tar' --exclude='opt/mixel-remote-next-qa/backup*' --exclude='*/src-build' \
    -czf - "${existing[@]}" 2> >(grep -v "Removing leading" >&2) | age "${recipients[@]}" -o "$LOCAL/$NAME"
size=$(stat -c %s "$LOCAL/$NAME")
[[ "$size" -gt 1000 ]] || { echo "[remote-config-backup] archive too small ($size bytes)" >&2; exit 1; }
rclone --config "$RCLONE_CONFIG_PATH" copyto "$LOCAL/$NAME" "$RCLONE_REMOTE/remote-config/$NAME" --retries 3 --log-level ERROR
rclone --config "$RCLONE_CONFIG_PATH" delete "$RCLONE_REMOTE/remote-config" --min-age 90d --include 'mixel-remote-config-*.age' --log-level ERROR
find "$LOCAL" -name 'mixel-remote-config-*.age' -mtime +3 -delete
echo "[remote-config-backup] $NAME ($size bytes, ${#existing[@]} paths) uploaded to $RCLONE_REMOTE/remote-config/"
