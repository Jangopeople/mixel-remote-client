# Mixel Remote — operations runbook

How Mixel Remote runs as a SaaS: what lives where, how it is watched, how each part is deployed and
rolled back, how secrets are rotated, what to do in an incident and how to restore a backup.
Plan: `mixel-craft/docs/mixel-remote-plan.md` (Phase 8). Ops sources: `next/deployment/ops/`.

Hard rules that apply to everything below
- GitHub is source backup only: build, test and deploy from the Mac (wrangler) or on mixel-vps.
- mixel-vps is shared by many projects: **never** `docker system|builder|image prune`; remove only
  the containers, images and folders you created, by name.
- Never remove an approved store release; app updates are new versions (AGENTS.md).
- nginx: always `nginx -t` before `systemctl reload nginx`; never restart nginx.

## 1. Map

| Part | Where | Public | Source |
|---|---|---|---|
| Connection broker | container `mixel-remote-next-qa-broker-device-access` (host network, 127.0.0.1:8094, image `mixel-remote-next-backend:<tag>`, env `/opt/mixel-remote-next-qa/private/broker-*.env`) | `wss://rs.mixel.ch:9444/v1/ws`, `/healthz` (nginx site `mixel-remote-next-qa.conf`) | `next/crates/signaling_server`, `deployment/backend.Dockerfile` |
| Identity authority | compose project `mixel-remote-next-qa` service `authority` (127.0.0.1:8093, signer `private/identity.key`) | `https://ism-api.mixel.ch/v1/device-ticket`, `/v1/startup-host-ticket` | same crate, bin `identity_authority` |
| TURN relay | compose service `turn` (coturn, 178.104.17.23 UDP/TCP 3479, TLS 5350, relay UDP 60000–60099) | `turn:rs.mixel.ch:3479`, `turns:rs.mixel.ch:5350` | `deployment/coturn.Dockerfile`, `private/turnserver.conf` |
| Database | `ism-postgres` (schemas `remote_saas`, `remote_private`, public device tables) | via PostgREST `ism-postgrest` | `next/sql/0NN_*.sql` |
| Accounts | `ism-gotrue` (ism-api.mixel.ch/auth/v1) | ism-api.mixel.ch | ISM stack `/opt/supabase-ism` |
| Billing + invitation e-mails | container `mixel-remote-billing` (`/opt/mixel-remote-billing`, 127.0.0.1:8096), daily `mixel-remote-billing-daily.timer` | `https://remote.mixel.ch/api/billing/*` | `next/services/billing` |
| Portal + status page | Cloudflare Worker `mixel-remote-portal`, route `remote.mixel.ch/*` | `https://remote.mixel.ch`, `/status` | `next/frontends/portal` |
| Web viewer | static folder in the ISM web app | `https://ism.mixel.ch/remote-next/` | `next/frontends/web/viewer` → `mixel-ism/apps/web/public/remote-next/` |
| Meetings | `mixel-meet.service` (`/opt/mixel-meet`, 127.0.0.1:8097) | `https://rs.mixel.ch/meet/` | `next/meet` |
| Update channel | store-api bucket `remote-updates` (public, signed `latest.json`) | `store-api.mixel.ch/storage/v1/object/public/remote-updates/stable/<platform>-<arch>/latest.json` | `mixel-craft/releases/remote-updates/publish-remote-update.sh` |
| Store downloads | store.mixel.ch catalogue (`store_app_releases`, private bucket `native-builds`) | `https://store.mixel.ch/app/mixel-remote` | `mixel-store` `scripts/publish-native-release.sh` |
| Monitoring | `mixel-remote-health.timer` (every 2 min) | status JSON `https://remote.mixel.ch/api/status` | `deployment/ops/` |

## 2. Monitoring and alerts

`/usr/local/sbin/mixel-remote-health` (source `ops/mixel-remote-health.py`) runs every 2 minutes
(`mixel-remote-health.timer`). Checks (component → check, interval):

| Check | What it proves | Every |
|---|---|---|
| `broker_local`, `broker_public` | broker `/healthz` on 127.0.0.1:8094 and through nginx on rs.mixel.ch:9444 | 2 min |
| `authority`, `device_ticket` | authority `/healthz`; the public ticket endpoint answers (empty call refused, 401/422) | 2 min |
| `synthetic_connect` | **real session**: probe account signs in (GoTrue), two probe computers get device tickets, log in to `wss://rs.mixel.ch:9444/v1/ws`, request + accept a session, get TURN credentials, open a relay-only WebRTC data channel through the TURN server, exchange a ping/pong, close | 5 min |
| `presence_fresh` | broker presence report in `remote_private.presence_reports` < 180 s old | 2 min |
| `containers` | broker, authority, TURN, billing, GoTrue, PostgREST, Postgres running | 2 min |
| `turn_ports` | TURN TCP 3479 and TLS 5350 accept connections | 2 min |
| `gotrue`, `ism_api` | `/auth/v1/health`; PostgREST RPC `mixel_remote_prices` | 2 min |
| `portal`, `viewer`, `meetings` | 6 portal pages + app bundle; web viewer; meet page | 2 min |
| `billing`, `invite_mail` | billing `/health`; invitation sender ran < 10 min ago without error, no invitation overdue > 15 min | 2 min |
| `stripe_webhook` | Stripe webhook endpoint `we_1UbE0hFRPXnJX22hlR5RA24a` enabled and pointing to remote.mixel.ch | 15 min |
| `update_manifests` | 4 stable channel files: Ed25519 signature with the app's update key, target fields, installer downloadable with the announced size | 10 min |
| `store_downloads` | every current Mixel Remote store file: signed download link answers with the catalogue size; store page | 15 min |
| `tls` | certificates > 14 days: rs.mixel.ch 443/9444/5350, ism-api, remote, ism, store-api, store, plus origin certs | 60 min |
| `backups` (internal) | newest ISM DB archive and config archive < 26 h; `mixel-backup.service` last result | 60 min |

Probe identity: tenant **"Mixel Status Probe"** (`0ccb0b6c-263b-4c30-9f95-c086fef2e41a`, plan
enterprise, active, no billing, limits 2 devices / 2 sessions), account `status-probe@remote.mixel.ch`,
two computers "Status probe controller/host". Credentials and device keys:
`/etc/mixel-remote-status/probe.env` (root 0600, in the config backup). Image
`mixel-remote-status-probe:current` (`ops/status-probe.Dockerfile`). No customer data is touched.
The probe tenant must never be cleaned up by QA scripts; it is excluded from nothing else (it shows
up in internal counts as one tenant).

Output: `/var/lib/mixel-remote-health/latest.json` (internal detail, root), `state.json`,
`history.json` (90 days), public `/var/www/mixel-remote-status/status.json` → nginx
`location = /api/status` on the remote.mixel.ch origin → Worker `/api/status` (passes only known
fields; says "monitor unreachable" if the origin does not answer) → page `/status` (footer link
"Service status"). The page shows "Status not available" when the report is older than 10 minutes.

Alert path (existing ops alarm): a check is DOWN after 2 failed runs in a row. The run exits 1 only
when a check newly goes DOWN or once an hour while something stays DOWN →
`OnFailure=notify-failure@mixel-remote-health.service` → `/opt/ops-alarm/notify-failure.sh` →
Resend e-mail "[ops-alarm] mixel-remote-health.service FAILED" with the run's journal (the
`[ALERT]` and `[DOWN]` lines say what). The ops alarm's own per-unit de-dup is set to 60 s for this
unit (`notify-failure@mixel-remote-health.service.service.d/dedup.conf`) so it never swallows a new
failure. When everything passes again one "[ops-alarm] mixel-remote-health RECOVERED" e-mail goes
out through the same sender. A crash of the monitor itself alerts at most hourly.
`mixel-backup.service` (all databases) now also has `OnFailure=notify-failure@` (drop-in
`mixel-backup.service.d/alert.conf`).

Commands
```bash
systemctl list-timers mixel-remote-health.timer
journalctl -u mixel-remote-health -n 40 -o cat            # last runs
sudo /usr/local/sbin/mixel-remote-health --dry-run --only synthetic_connect   # run checks now, write nothing
sudo cat /var/lib/mixel-remote-health/latest.json
curl -s https://remote.mixel.ch/api/status | jq .status
```

Test an alert (safe, not visible on the public page): `printf 'MRH_BACKUP_MAX_HOURS=0\nMRH_FORCE_ALL=1\n' |
sudo tee /etc/mixel-remote-status/health.env`, `sudo systemctl start mixel-remote-health` twice (the
second exits 1 and mails), then `sudo rm /etc/mixel-remote-status/health.env` and start once more
(RECOVERED mail). Every check target can be overridden the same way (`MRH_BROKER_LOCAL=http://127.0.0.1:8099/healthz`,
`MRH_PROBE_WS=wss://rs.mixel.ch:9445/v1/ws`, ...); overrides of public checks show on the status page,
so for those use a transient unit with its own state:
`systemd-run --wait --collect --unit=mixel-remote-health-sim --setenv=MRH_STATE_DIR=/var/lib/mixel-remote-health-sim --setenv=MRH_PUBLIC_FILE=/var/lib/mixel-remote-health-sim/status.json --setenv=MRH_BROKER_LOCAL=http://127.0.0.1:8099/healthz /usr/local/sbin/mixel-remote-health --force-all`
(delete `/var/lib/mixel-remote-health-sim` afterwards).

Install / update the ops pieces: `scp -r next/deployment/ops mixel-vps:~/mixel-remote-ops && ssh mixel-vps sudo bash ~/mixel-remote-ops/install.sh`
(idempotent, backs up replaced files to `/root/mixel-remote-ops-backup/`). Probe image:
```bash
tar -czf - Cargo.toml Cargo.lock crates vendor deployment/ops/status-probe.Dockerfile --exclude 'crates/mobile_app/gen' \
  | ssh mixel-vps 'rm -rf ~/mixel-status-probe-src && mkdir ~/mixel-status-probe-src && tar -xzf - -C ~/mixel-status-probe-src'
ssh mixel-vps 'cd ~/mixel-status-probe-src && sudo docker build -f deployment/ops/status-probe.Dockerfile -t mixel-remote-status-probe:<commit> . \
  && sudo docker run --rm --network host --env-file /etc/mixel-remote-status/probe.env mixel-remote-status-probe:<commit> run \
  && sudo docker tag mixel-remote-status-probe:<commit> mixel-remote-status-probe:current'
```
Recreate the probe identity (only if it was deleted): create the GoTrue user with the admin API
(password into `probe.env`), `docker run ... mixel-remote-status-probe:current setup >> probe.env`
(adds the `MIXEL_PROBE_A_*`/`B_*` lines), then set the tenant to `plan='enterprise', status='active',
trial_ends_at=null, max_devices_override=2, max_sessions_override=2`.

## 3. Rate limits and abuse protection

nginx (`/etc/nginx/conf.d/mixel-remote-limits.conf`, snippet `snippets/mixel-remote-limits-ism-api.conf`
included in the ism-api.mixel.ch server, `limit_req` in the rs.mixel.ch:9444 `/v1/ws` location);
per client address, beyond the burst → HTTP 429:

| Zone | Endpoints | Rate / burst |
|---|---|---|
| `mixel_remote_signin` | `POST /auth/v1/token` (password, pkce, id_token), `/auth/v1/verify`, MFA challenge/verify | 20/min, 30 |
| `mixel_remote_mail` | `POST /auth/v1/signup`, `otp`, `recover`, `magiclink`, `resend` | 5/min, 10 |
| `mixel_remote_rpc` | RPCs sign-up, invite, resend, accept invite, support codes, sign-in options, deploy links, domains, personal account, phone registration | 30/min, 30 |
| `mixel_remote_enroll` | RPCs enrol with deploy link, add this computer, personal device, self-removal | 120/min, 600 |
| `mixel_remote_ticket` | `POST /v1/device-ticket`, `/v1/startup-host-ticket` | 120/min, 600 |
| `mixel_remote_ws` | new broker WebSockets | 120/min, 1000 |

Token refresh, PostgREST reads and the presence report are not limited. Large bursts are deliberate
(a company rolls out hundreds of computers behind one address). Per company (in SQL): sign-ups 50/h
in total, invitations 100/day, deploy links 50/day plus uses per link and the plan's device limit,
support codes 30/h per user; billing service: per company and account per minute. The broker caps
1000 open connections in total and 60 messages/s per connection.
Change a limit: edit the conf in `ops/nginx/`, run `install.sh` (or copy + `nginx -t` + reload).
Block an address quickly: `deny <ip>;` in the ism-api server block or `ufw insert 1 deny from <ip>`
(note it in the incident log; remove later).

## 4. Deploy and roll back

Always first: `git fetch && git log origin/feat/managed-app` — several agents merge there. Deploy
from a clean worktree on the current tip, run the part's checks, then watch `journalctl -u
mixel-remote-health -f` for one or two runs (the synthetic connection covers broker, authority,
TURN, GoTrue and PostgREST together).

**Broker** (≈ 6 min build): tar `Cargo.toml Cargo.lock crates vendor deployment/backend.Dockerfile` →
VPS → `docker build -f deployment/backend.Dockerfile -t mixel-remote-next-backend:<feature>-<date>-<commit> .`
→ save the current container config (`docker inspect ... > private/broker-container-before-<x>.json`) →
`docker stop` + `docker rename` the old one to `...-prev-<date>` (`docker update --restart no`) → `docker run -d --name
mixel-remote-next-qa-broker-device-access --network host --restart unless-stopped --user 10001:10001
--env-file private/broker-<x>.env --log-opt max-size=50m --log-opt max-file=5 <image>` with the other
flags exactly as in the saved inspect JSON.
Running sessions drop and reconnect (apps retry). Rollback: stop + remove the new container, rename
`...-prev-<date>` back, `docker start` it. Remove the `-prev` container once the new one is good.

**Authority / TURN** (compose project `mixel-remote-next-qa`, `/opt/mixel-remote-next-qa/compose.json`,
images pinned by ID): change the image ID in compose.json (keep a copy), `docker compose -p
mixel-remote-next-qa -f compose.json up -d --no-deps <authority|turn>`. Rollback: restore compose.json,
same command. TURN TLS is refreshed by the Let's Encrypt hook `refresh-turn-tls.py`.

**Database migrations**: `next/sql/0NN_*.sql`, next free number at apply time (security/legal agents
also add migrations). Apply with `docker exec -i ism-postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d
ism < file` (each file has its own transaction), then `NOTIFY pgrst, 'reload schema'`. Run the
rolled-back tests in `next/sql/tests/` first. Rollback: a migration that failed rolled itself back; a
committed one is reverted by a new forward migration — never drop customer tables.

**Billing service**: see `next/services/billing/README.md` (build on the VPS from the folder,
tag `mixel-remote-billing:<commit>` + `:current`, `docker compose up -d` in `/opt/mixel-remote-billing`).
Rollback: `docker tag mixel-remote-billing:<previous> mixel-remote-billing:current && docker compose up -d`
(Stripe retries webhooks for 3 days, so a short stop loses nothing).

**Portal** (`next/frontends/portal`): `npm ci && npm run typecheck && npm run lint && npm test &&
npm run build && npx wrangler deploy --message "<commit>"`. Deploy only from the current
`feat/managed-app` tip (a deploy from an older checkout silently removes other people's changes;
this happened on 2026-11-13). Rollback: `npx wrangler deployments list` →
`npx wrangler rollback <version-id>`.

**Web viewer**: build in `next/frontends/web/viewer` (README there), copy into
`mixel-ism/apps/web/public/remote-next/`, ISM PR → merge → `pnpm build && pnpm deploy:cloudflare` in
`apps/web` from clean main. Rollback: revert the ISM commit and deploy again (or Pages rollback).

**Meetings**: `next/meet/deploy/deploy.sh`; rollback by redeploying the previous commit.

**App releases / update channel**: `publish-remote-update.sh <channel> <platform> <arch> <version>
<file>` from the Mac (signs with `~/.mixel-secrets/remote-update-ed25519.pem`); store files with
`mixel-store/scripts/publish-native-release.sh`. Rollback of a bad update: apps install only newer
versions, so stop the spread by publishing the previous installer again under the channel (re-run the
script with the old file; computers that already updated stay), then ship a fixed higher version.
Store: make the previous row current again (`is_current`), never delete approved releases.

## 5. Rotate secrets

All secret files are root 0600 on mixel-vps and part of the daily config backup. After any rotation
run `sudo systemctl start mixel-remote-health` and confirm `synthetic_connect` passes.

| Secret | Where | Rotate |
|---|---|---|
| Presence token (`MIXEL_PRESENCE_TOKEN`) | `private/broker-*.env` (the env the broker container runs with) | mint a new JWT (role `mixel_remote_presence`, HS256 with the ISM JWT secret, new `iat`/`exp`), replace the line, recreate the broker (section 4). Revoke all at once: `revoke execute on function public.remote_presence_report(integer[]) from mixel_remote_presence`. Details: `QA-DEPLOYMENT.md` "Presence reporting". |
| TURN shared secret | `MIXEL_TURN_SHARED_SECRET` in the broker env and `static-auth-secret` in `private/turnserver.conf` | generate 48 random bytes (`openssl rand -hex 48`), put it into both files, restart `turn` (`docker compose -p mixel-remote-next-qa -f compose.json restart turn`) and recreate the broker right after each other, in a quiet hour. Relay credentials live 10 min; relayed sessions running at that moment may drop and reconnect. |
| Support policy token / startup host token | `MIXEL_SUPPORT_POLICY_TOKEN` (broker), `MIXEL_STARTUP_HOST_POLICY_TOKEN` (authority) | set the new value in the admin console env first (`/opt/mixel-admin-api`, accepts it), then broker/authority, recreate, remove the old one in the console. |
| Identity key (device ticket signer) | `private/identity.key` (32 bytes, owner 10002, 0600), public half in `MIXEL_PEER_AUTHORITY_PUBLIC_KEYS` (broker, comma-separated list) | create a new 32-byte key (`head -c 32 /dev/urandom`), add its public key to `MIXEL_PEER_AUTHORITY_PUBLIC_KEYS` **next to** the old one, recreate the broker, swap `identity.key`, recreate the authority; after the ticket lifetime (900 s) remove the old public key and recreate the broker. Enrolments are not affected; apps fetch a new ticket on renewal. |
| Stripe secret key | `STRIPE_SECRET_KEY` in `/opt/mixel-remote-billing/.env` (shared Mixel Shop account) | create a restricted or rolled key in the Stripe dashboard (Developers → API keys), replace the line, `docker compose up -d` in `/opt/mixel-remote-billing`, check `/api/billing/health` and the `stripe_webhook` check; other apps on the account (Shop, PBX, Marketing) use their own copies — coordinate before rolling the shared key. |
| Stripe webhook secret | `STRIPE_WEBHOOK_SECRET` (same file), endpoint `we_1UbE0hFRPXnJX22hlR5RA24a` | Stripe dashboard → Webhooks → endpoint → "Roll secret" with a delay (e.g. 1 h, both secrets valid), put the new `whsec_` in `.env`, `docker compose up -d`; Stripe retries anything refused in between. |
| Billing DB login | `DATABASE_URL` (role `mixel_remote_billing_service`) | `alter role mixel_remote_billing_service password '<new>'`, update `.env`, `docker compose up -d`. |
| Resend key (invitation e-mails) | `RESEND_API_KEY` in the billing `.env` | new key in Resend, replace, `docker compose up -d`, check `invite_mail`. |
| Probe password | `MIXEL_PROBE_PASSWORD` in `/etc/mixel-remote-status/probe.env` | GoTrue admin API `PUT /admin/users/<id>` with a new password, update the file. |
| Update signing key | `~/.mixel-secrets/remote-update-ed25519.pem` (Mac only) | needs an app release with the new public key first (`UPDATE_KEY` in `crates/desktop_app/src/self_update.rs` and in `ops/mixel-remote-health.py`); keep signing with the old key until most installs updated. |

## 6. Incident handling

1. **Confirm**: alert mail → `journalctl -u mixel-remote-health -n 60 -o cat` (the `[DOWN]` lines),
   `https://remote.mixel.ch/status`. Check the shared server first: `uptime`, `df -h /`, `free -g`,
   `docker ps | grep -E 'remote|ism-'`.
2. **Triage by check**
   - `broker_*`/`synthetic_connect` "broker connect failed": `docker logs --tail 100 mixel-remote-next-qa-broker-device-access`; restart it (`docker restart ...`); bad deploy → roll back (section 4).
   - `synthetic_connect` "device ticket refused": authority logs (`docker logs mixel-remote-next-qa-authority-1`), GoTrue/PostgREST; "relay channel did not open": TURN logs (`docker logs mixel-remote-next-qa-turn-1`), UFW rules for 3479/5350/60000-60099, certificate copy in `private/turn-tls`.
   - `presence_fresh`: broker presence env/token (401 in broker logs → token rotated or revoked).
   - `gotrue`/`ism_api`: the ISM stack (`/opt/supabase-ism`, `docker compose ps`); this also affects ISM itself.
   - `billing`/`invite_mail`/`stripe_webhook`: `docker logs mixel-remote-billing`; a disabled webhook endpoint must be re-enabled in Stripe (events are retried 3 days).
   - `update_manifests`/`store_downloads`: store stack (`store-storage`, `store-api` nginx); a signature failure means the channel file was replaced — republish with the script, never hand-edit.
   - `tls`: `certbot certificates`; `certbot renew --cert-name <name>` and `nginx -t && systemctl reload nginx`.
   - `backups`: `journalctl -u mixel-backup -e`, disk space, Storage Box (`rclone lsd hetzner-storage:backups`).
3. **Communicate**: the status page updates by itself within 2–4 minutes. For an outage over
   15 minutes e-mail the company owners (portal admins) with what is affected and the next update time.
4. **Recover and verify**: RECOVERED mail arrives; run `mixel-remote-health --dry-run --only synthetic_connect`.
5. **Write up** in `mixel-craft/docs/incidents/<date>-<topic>.md`: timeline, cause, fix, follow-ups.

## 7. Backups, RPO / RTO and restore

| What | How | Where | Retention |
|---|---|---|---|
| ISM database (incl. `remote_saas`, `remote_private`, billing tables, devices, users, auth) | `mixel-backup.service` daily 09:30 UTC: `pg_dumpall` of `ism-postgres`, gzip, `age` to the main recipient + ISM recovery recipient | local `/opt/pg-backups-encrypted/ism-postgres-*.age`, Storage Box `hetzner-storage:backups/{daily,weekly,monthly}` | 7 daily, 4 weekly, 12 monthly |
| Broker/authority/TURN config, identity key, broker env, billing `.env`, probe identity, meet env, units, scripts, nginx limits | `mixel-remote-config-backup.timer` daily 04:40 Zurich, `age` to the same recipients | `/opt/pg-backups-encrypted/remote-config/` (3 days), Storage Box `backups/remote-config/` | 90 days |
| nginx, letsencrypt, `/opt/supabase-*/.env` | part of `mixel-backup` (configs tarball) | Storage Box `backups/configs` | 30 days |

Decryption keys are not on the server: the main key on the Mac (`~/.config/age/mixel-vps-backup.txt`),
the ISM recovery key in `~/.mixel-secrets/ism-backup-recovery-20260930.agekey`. Both decrypt the ISM
archive and the Remote config archive.

**RPO** (data that can be lost): up to 24 h for the database (one dump per day at 09:30 UTC; no WAL
archiving). Subscriptions, invoices and payments stay in Stripe, so for paying customers the loss is
tenant, member and device changes since the last dump; compare `remote_saas.billing` with the Stripe
subscriptions after a restore. Lowering the RPO needs WAL archiving or more frequent dumps (not set up).
**RTO** (measured 2026-11-13, restore test below): fetch from the Storage Box ≈ 5 s, decrypt + restore
of the full ISM dump into a fresh Postgres ≈ RESTORE_MINUTES min, plus repointing the stack ≈ 15 min:
about RTO_TOTAL in total for a full database loss. Config/identity key restore ≈ 10 min.

Restore test (do it monthly; it changes nothing live):
```bash
ssh mixel-vps 'sudo install -m 600 /dev/stdin /dev/shm/mixel-restore-key' < ~/.mixel-secrets/ism-backup-recovery-20260930.agekey
ssh mixel-vps 'sudo /usr/local/sbin/mixel-remote-restore-test /dev/shm/mixel-restore-key'
```
It takes the newest Storage Box archive, restores it into the throwaway container
`mixel-remote-restore-test` (no network, data on `/mnt/HC_Volume_106234333/mixel-remote-restore-test`),
compares row counts of every `remote_saas`/`remote_private` table and the related public/auth tables
with live, prints timings, and removes the container, the data and the key.

Real restore (database lost or corrupted): stop writers (billing container, broker, authority), keep
the broken data directory (rename, do not delete), create a fresh `ism-postgres` from the ISM compose
stack, `age -d -i <key> ism-postgres-<stamp>.dump.gz.age | gunzip | docker exec -i ism-postgres psql
-U postgres -d postgres`, `NOTIFY pgrst, 'reload schema'`, start GoTrue/PostgREST, then billing,
authority, broker; run the health check; re-sync billing from Stripe (`/api/billing` events are
resend the gap's events from the Stripe dashboard if needed). Only one table: restore into the throwaway container (the
test script without the cleanup) and copy rows over with `\copy`.

Config restore: `age -d -i <key> mixel-remote-config-<stamp>.tar.gz.age | sudo tar -xzpf - -C /`
(restores `/opt/mixel-remote-next-qa`, billing `.env`, `/etc/mixel-remote-status`, units), then
`systemctl daemon-reload`, recreate the containers (section 4). The identity key keeps its owner
10002 / mode 0600 from the archive.
