# Mac internet acceptance deployment

This is an additive, isolated service deployment on `mixel-vps-01`
(`178.104.17.23`). The bundle is prepared locally; preparation does not deploy it.
Apply only after Michael approves these exact production actions. No existing
RustDesk service, relay identity, customer download, DNS record, live app, or
marketplace submission is replaced.

## Prepared files and boundaries

- `compose.json`: three separate unprivileged containers, all capabilities
  dropped, read-only root filesystems, bounded resources/logs, immutable local
  image IDs, no image pulls. Authority and broker listen only on host loopback
  8093/8092. TURN listens on the server's public IPv4 only.
- `private/identity.key`: fresh 32-byte identity signer; install mode 0600 with
  owner 10002. Only the authority gets this bind mount. Neither broker, TURN nor
  client receives it. Preserve this signer across service restarts.
- `private/authority.env`: existing public ISM anonymous key and trusted API
  origin, no database/service-role key or user bearer.
- `private/broker.env`: public identity root and a new independent TURN REST
  secret; no ISM bearer, identity seed or TLS private key.
- `private/turnserver.conf`: same independent relay secret, private/loopback,
  multicast and IPv6 peers denied, TCP peer relay disabled. The only explicit
  allowed peer is the public IPv4 relay address, so the two allocations can
  exchange media even when coturn matches IPv4 against the IPv6 deny range. Temporary REST
  credentials are issued only after consent. TURN relay range 60000–60099/UDP.
- `service-config.json`: public client configuration only; force relay for the
  first internet acceptance so a successful session actually proves the relay.
- `ism-api.nginx.conf`: hash-guarded original plus only an exact POST
  `/v1/device-ticket` upstream. Full account bearers stay on the already trusted
  `ism-api.mixel.ch` origin. The authority independently denies browser Origins.
- `broker.nginx.conf`: new rs.mixel.ch TLS listener 9444, only `/v1/ws` and
  `/healthz`. Existing rs.mixel.ch 443 and native ports remain untouched.
- `gotrue.remote-qa.json`: preserves the six existing redirects and adds only
  decimal-port 127.0.0.1 callback patterns (4/5 digits, exact path, PKCE state).
- `enrollment.sql`: transactional new enrollment/audit tables and MFA-staff
  enrollment/revoke/rotation RPCs. Existing devices/users/organizations remain
  the source of truth. This does not enroll any device or change an existing row.

## Apply sequence after exact approval

1. Recheck bundle `sha256.json`, server hostname/public IP, free ports, absent
   `remote_private` namespace/enrollment table, and the reviewed nginx SHA256.
   Refuse any mismatch. Existing database is PostgreSQL 15; use its own tools.
2. Make a private backup of the two existing nginx site files, current GoTrue
   environment/config-file list and schema metadata. Keep backups inside the
   fresh `/opt/mixel-remote-next-qa` root (0700). Refuse a pre-existing deployment
   root or Compose project; never overwrite another installation.
3. Transfer the prepared bundle privately to that root. Load the locally built
   image archive and require both loaded image IDs to match `plan.json`.
   Install signer owner10002/mode0600, TURN config owner10003/mode0600, and
   env files root/mode0600. Private directories are never served by nginx.
4. Privately copy only the existing rs.mixel.ch certificate and key into
   `private/turn-tls` (directory root:10003/0750, files root:10003/0640).
   Do not change the original Let's Encrypt file permissions. Add an rs-only
   renewal hook that atomically refreshes these two copies and restarts only
   this Compose project's TURN container. Broker/authority get no key mount.
5. Validate the complete prospective nginx configuration before reload.
   Preserve rs.mixel.ch's existing configuration byte-for-byte. Install the
   new 9444 vhost as a new site; replace only the hash-matched ISM site with
   the prepared additive version. Gracefully reload nginx, never restart it.
6. Apply `enrollment.sql` through `ism-postgres` with `psql -X -v ON_ERROR_STOP=1`,
   lock timeout5s and statement timeout30s. The SQL has its own transaction.
   Then send `NOTIFY pgrst, 'reload schema'`. Do not run fixtures/seed SQL.
7. Start only project `mixel-remote-next-qa` using its explicit `compose.json`.
   Verify both loopback health endpoints, independent process IDs and secret
   mounts. Verify unauthenticated authority POST401 and browser Origin403.
8. Open only destination178.104.17.23 TCP9444, TCP/UDP3479, TCP5350 and
   UDP60000:60099 in UFW, each tagged `mixel-remote-next-qa`. Do not expose
   loopback8092/8093. Record precisely which rules were newly added.
9. Check the current GoTrue container image and every explicitly configured
   environment value against the base Compose result. Refuse unrelated drift.
   Add the prepared override alongside the two existing Compose files and
   recreate only `gotrue` with `--no-deps --pull never`. This briefly restarts
   sign-in; preserve all other auth settings, signing keys, users and tokens.
10. From the Mac, verify trusted HTTPS/WSS certificate chains, authority denials,
    public relay UDP/TCP/TLS allocation and forbidden private-peer policy.
    Build/sign the Mac app with the exact prepared public configuration.
    Perform real browser sign-in, MFA staff enrollment of explicitly selected
    QA device records, and two independently enrolled Mac endpoints. Verify
    no capture before Accept, Reject, actual selected-display video/input,
    Stop, fresh consent on reconnect and relay evidence. Save observed UI
    screenshots plus native session receipts. Do not call health checks or
    synthetic media a completed customer acceptance test.

## Rollback without data loss

Stop only the new Compose project. Remove only the newly added tagged firewall
rules. Remove only the owned 9444 site and renewal hook after verifying they
still match this deployment. Restore the ISM site from backup only if its current
hash equals the installed version; otherwise remove only this exact location
after reviewing concurrent changes. Validate nginx and gracefully reload it.

Remove the native redirect override by recreating only GoTrue from its original
two Compose files, after checking for unrelated changes. Preserve current
base settings and all existing sessions/keys. A failed additive database
migration rolls back its own transaction. If migration committed, **retain**
enrollment/audit tables, rows and signer; rollback never drops customer data.
Keep the private stopped deployment for recovery. Do not rotate existing
enrollments, delete images used elsewhere, change DNS, or touch marketplace apps.


## Presence reporting (online / last seen, SQL 025) — 2026-11-13

The broker reports the connection IDs of the enrolled devices connected right now every 30 s
(`crates/signaling_server/src/presence.rs`) to `public.remote_presence_report` through PostgREST. Optional; enabled
with two variables in the broker environment (`/opt/mixel-remote-next-qa/private/broker-presence.env`, root 0600):

- `MIXEL_PRESENCE_URL=https://ism-api.mixel.ch/rest/v1/rpc/remote_presence_report`
- `MIXEL_PRESENCE_TOKEN=<JWT, role mixel_remote_presence, HS256 with the ISM JWT secret, 5 years>`

Role `mixel_remote_presence` can execute only that function (and PostgREST's `enforce_staff_mfa_request`
pre-request check). Rotate: mint a new JWT (same claims, new `iat`/`exp`), replace the line, recreate the broker; to
revoke all tokens at once, `revoke execute on function public.remote_presence_report(integer[]) from
mixel_remote_presence`. A report less than 10 s after the previous one is ignored. Tenant members read the state
through `remote_saas_device_presence` (status `online`, `offline` + `last_seen`, `never`, `unknown` when the broker has
been silent for 3 minutes, `not_enrolled`).

Broker container `mixel-remote-next-qa-broker-device-access` (host network, `--restart unless-stopped`, json-file logs
50m×5) runs image `mixel-remote-next-backend:presence-20261113` with `--env-file private/broker-presence-full.env`
(the previous environment plus the two lines). The previous container config is in
`private/broker-container-before-presence-20261113.json`. Rollback: recreate it with image
`mixel-remote-next-backend:saas-20261113-f3aade2` and without the two variables; presence then shows "Status unknown".
