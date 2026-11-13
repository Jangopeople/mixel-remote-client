#!/usr/bin/env bash
# Restore test of the ISM database backup for Mixel Remote (run on mixel-vps as root).
#   mixel-remote-restore-test.sh <age identity file> [archive name]
# Takes the newest ism-postgres archive from the Storage Box (offsite copy, not the local one),
# decrypts it with the given age identity (the ISM recovery key, kept off the server: pass it in a
# root-only file under /dev/shm, it is shredded at the end), restores it into a throwaway container
# "mixel-remote-restore-test" (no network, data on the big volume), compares the row counts of every
# remote_saas / remote_private table and the related public tables with the live database, prints
# the timings, and removes the container, its data and the decrypted dump.
set -euo pipefail
umask 077
KEY="${1:?usage: $0 <age identity file> [archive]}"
source /etc/mixel-backup.env
RCLONE_CONFIG_PATH="${RCLONE_CONFIG_PATH:-/root/.config/rclone/rclone.conf}"
WORK=/mnt/HC_Volume_106234333/mixel-remote-restore-test
NAME=mixel-remote-restore-test
IMAGE=$(docker inspect --format '{{.Config.Image}}' ism-postgres)
cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -rf "$WORK"
  shred -u "$KEY" 2>/dev/null || rm -f "$KEY"
}
trap cleanup EXIT
[[ ! -e "$WORK" ]] || { echo "refusing: $WORK exists (another restore test?)" >&2; exit 1; }
docker inspect "$NAME" >/dev/null 2>&1 && { echo "refusing: container $NAME exists" >&2; exit 1; }
mkdir -p "$WORK/pgdata"
chmod 700 "$WORK"

ARCHIVE="${2:-$(rclone --config "$RCLONE_CONFIG_PATH" lsf "$RCLONE_REMOTE/daily" --include 'ism-postgres-*.dump.gz.age' | sort | tail -1)}"
[[ -n "$ARCHIVE" ]] || { echo "no ism-postgres archive on the Storage Box" >&2; exit 1; }
t0=$(date +%s)
rclone --config "$RCLONE_CONFIG_PATH" copyto "$RCLONE_REMOTE/daily/$ARCHIVE" "$WORK/$ARCHIVE" --retries 3 --log-level ERROR
t1=$(date +%s)
echo "[restore-test] fetched $ARCHIVE ($(stat -c %s "$WORK/$ARCHIVE") bytes) from the Storage Box in $((t1 - t0)) s"

docker run -d --name "$NAME" --network none --cpus 1.5 --memory 3g \
  -e POSTGRES_PASSWORD="$(openssl rand -hex 24)" -v "$WORK/pgdata:/var/lib/postgresql/data" "$IMAGE" >/dev/null
for _ in $(seq 1 90); do
  docker exec "$NAME" pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1 && break
  sleep 2
done
sleep 5
docker exec "$NAME" pg_isready -U postgres -h 127.0.0.1 >/dev/null
t2=$(date +%s)
echo "[restore-test] throwaway Postgres ($IMAGE) ready in $((t2 - t1)) s"

# pg_dumpall --clean output: role and "already exists" errors of the image's own roles are expected.
age -d -i "$KEY" "$WORK/$ARCHIVE" | gunzip | nice -n 10 docker exec -i "$NAME" psql -X -q -U postgres -d postgres \
  >"$WORK/restore.log" 2>&1 || true
t3=$(date +%s)
errors=$(grep -c "ERROR" "$WORK/restore.log" || true)
echo "[restore-test] restored in $((t3 - t2)) s ($errors psql errors; first ones:)"
grep "ERROR" "$WORK/restore.log" | sort | uniq -c | sort -rn | head -5 | cut -c1-160

TABLES=$(docker exec ism-postgres psql -X -tA -U postgres -d ism -c "select string_agg(format('%I.%I', n.nspname, c.relname), ' ' order by 1)
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where c.relkind = 'r' and (n.nspname in ('remote_saas', 'remote_private')
     or (n.nspname = 'public' and c.relname in ('devices', 'remote_device_enrollments', 'users', 'organizations', 'clients'))
     or (n.nspname = 'auth' and c.relname = 'users'))")
count_sql=""
for t in $TABLES; do count_sql+="select '$t', count(*) from $t union all "; done
count_sql="${count_sql% union all }"
docker exec ism-postgres psql -X -tA -F' ' -U postgres -d ism -c "$count_sql" | sort >"$WORK/live.txt"
docker exec "$NAME" psql -X -tA -F' ' -U postgres -d ism -c "$count_sql" 2>&1 | sort >"$WORK/restored.txt"
printf '%-45s %10s %10s\n' table live restored
join -a1 -e missing -o 0,1.2,2.2 "$WORK/live.txt" "$WORK/restored.txt" | while read -r t live restored; do
  printf '%-45s %10s %10s\n' "$t" "$live" "$restored"
done
missing=$(join -v1 "$WORK/live.txt" "$WORK/restored.txt" | wc -l)
dumped_at=$(echo "$ARCHIVE" | sed -E 's/ism-postgres-([0-9]{8})-([0-9]{4}).*/\1 \2 UTC/')
echo "[restore-test] archive taken $dumped_at; tables missing in the restore: $missing; total $((t3 - t0)) s"
[[ "$missing" -eq 0 ]]
