#!/usr/bin/env bash
# Mixel Remote daily billing job (/usr/local/sbin/mixel-remote-billing-daily.sh on mixel-vps):
# trials that ended become read-only, read-only tenants get their 30-day countdown, and devices of
# tenants read-only for 30 days are removed. Usage: mixel-remote-billing-daily.sh [dry-run|live]
# Default from MIXEL_REMOTE_BILLING_DAILY_MODE (/etc/default/mixel-remote-billing-daily), else live.
# Log: journalctl -u mixel-remote-billing-daily
set -euo pipefail
mode="${1:-${MIXEL_REMOTE_BILLING_DAILY_MODE:-live}}"
case "$mode" in
  dry-run) dry=true ;;
  live) dry=false ;;
  *) echo "usage: $0 [dry-run|live]" >&2; exit 2 ;;
esac
docker exec -i ism-postgres psql -X -tA -v ON_ERROR_STOP=1 -U postgres -d ism \
  -c "select remote_saas.billing_daily(${dry})"
