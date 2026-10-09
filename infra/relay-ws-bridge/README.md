# Mixel HTTPS host registration gateway

This component repairs the confirmed incompatibility between the branded
client's HTTPS host-registration path and the deployed RustDesk OSS 1.1.15
server. It has not been deployed to production.

On 2026-10-09, an empty `RegisterPk` over certificate-verified
`wss://rs.mixel.ch/ws/id` returned `8201020806`, which decodes to
`RegisterPkResponse.result = NOT_SUPPORT (6)`, then the server disconnected.
No ID, UUID or public key was sent by that probe, so it could not register or
change a device. The live server's image digest matches the isolated fixture.
The server's [TCP handler](https://github.com/rustdesk/rustdesk-server/blob/1.1.15/src/rendezvous_server.rs)
implements this rejection explicitly; a WebSocket reverse proxy cannot repair
it by changing timeout settings.

The deployed native servers otherwise passed the read-only health checks:
no container restarts or OOM kills since August 25, small relay CPU/memory use,
public pin equal to the baked identity, valid TLS certificate through
December 24 and an active successful renewal timer. Both existing WebSocket
proxy layers use a 3600-second read timeout. Native remote sessions exchange
application heartbeats, so this timeout does not impose a one-hour session
limit. These checks did not justify changing the native server or its key.

## Transport behavior

The first binary rendezvous frame selects the mode. Host `RegisterPk` frames
go to the existing hbbs UDP port through a dedicated connected socket. Each
WebSocket session gets a separate source address in Linux's existing 127/8
loopback range and a separate UDP port. This prevents the stock upstream
per-IP registration bucket from merging the desktops behind one corporate
NAT. The gateway independently enforces trusted real-client limits: 256
connections and 1024 registration attempts per minute per source address,
with a global limit of 512 connections and 8192 registration attempts per
minute. Registration history is bounded globally as well as per source.

Only a genuine hbbs success enables ten-second `RegisterPeer` heartbeats.
Actual hbbs responses and incoming support requests travel back unchanged.
The native client echoes the gateway's empty application heartbeat. Missing
client or upstream activity closes the session within 30 seconds and stops
registration; the gateway never fabricates an online response.

Other rendezvous sessions retain the existing hbbs WebSocket path. Their
binary frames, signed peer identities and permissions remain unchanged.
The existing `/ws/relay` route continues directly to hbbr. Session encryption
remains between the two clients; the gateway neither reads relay keys nor
opens relay ciphertext. Automatic HTTPS clients must choose the relay path
from the actual rendezvous transport, including when fallback happens after
their original session preference was initialized.

The service binds only `127.0.0.1:8090` and requires one valid `X-Real-IP`
header from the loopback reverse proxy. The exact nginx location replaces
this header with its real TCP peer, rather than trusting a supplied header
or an appended forwarding chain. Malformed frames, message sizes and queues
are bounded. Logs omit identities, tokens, payloads and proxy source values.
`/healthz` is liveness only; local `/status` returns connection counts without
customer identifiers and supports draining during rollback.

## Run the isolated verification

```sh
python3 scripts/test-relay-ws-bridge-docker.py
```

The runner builds the reviewed gateway image, executes its actual baked
module through local WebSocket/UDP socket tests, then starts disposable
hbbs/hbbr 1.1.15 containers using the exact production image digest. A separate
nginx terminates TLS with a new one-day fixture CA and a certificate for
`rs.mixel.ch`. Every protocol-test socket is forced to the fixture's loopback
namespace; no test message can resolve or contact the public relay.

The fixture generates its own server identity. Only its public pin is copied
to the test runner. The production private key is never read or copied.
After verification, only task-created containers, network and data volume
are removed. The proof covers:

- Untrusted CA and incorrect certificate hostname are rejected.
- Stock WebSocket registration returns `NOT_SUPPORT`.
- 100 simultaneous hosts behind one trusted NAT receive genuine hbbs OK and
  the same 100 identities reconnect in a second wave within the same minute.
- Incoming support requests reach the corresponding host.
- The real hbbs signature binds the registered host ID and signing key.
- Actual hbbr WebSocket pairing carries the signed ephemeral identity and
  authenticated boxed session key used by the native protocol.
- Bidirectional XSalsa20-Poly1305 ciphertext survives 35 seconds unchanged;
  each endpoint authenticates/decrypts it and rejects tampering.
- Genuine host registration remains online beyond hbbs's 30-second expiry
  after a single `RegisterPk`.

This runner is protocol proof. On 2026-10-09, the actual built Linux customer
application also completed the desktop E2E suite through the isolated router
with native ports 21115–21119 blocked: real consent, RGB video, native keyboard
and mouse, clipboard in both directions, file upload/download with SHA
verification, service restart requiring fresh consent while GUI heartbeat was
paused, network loss/reconnect requiring fresh consent, and disconnect. The
compiled application was unchanged; only dedicated test HOME options, fixture
DNS and the public fixture CA/pin differed. That run used an amd64 Docker guest
on an arm64 development host and explicitly recorded the emulation and separate
relay identity.

Retain a task fixture for the desktop suite with:

```sh
python3 scripts/test-relay-ws-bridge-docker.py --keep-fixture /tmp/mixel-isolated-relay
```

The output directory must be new. Its manifest names only the task network,
containers, private fixture IP, public CA and public fixture pin. Desktop peers
join that network, map `rs.mixel.ch` to its fixture IP, trust only that public
test CA and store only the public fixture pin in their dedicated test HOME.
Their compiled encryption validation stays unchanged. The manifest records
that this is a different identity from the live baked pin. No host ports are
published. Clean up only the exact task resources listed in the manifest
after its peers finish.

## Exact deployment scope — requires explicit production approval

Deploy only after the isolated actual desktop HTTPS test and independent
review pass. The proposed changes are:

1. Add this component under `/opt/mixel-remote-registration-bridge/` and start
   its separate container with the supplied Compose file. It runs without
   capabilities as UID 65532, with a read-only filesystem, a 128 MiB memory
   bound, half a CPU, 32 PIDs and bounded logs. Port 8090 was unused during the
   read-only audit; recheck before starting.
2. Back up `/etc/nginx/sites-available/rs.mixel.ch.conf` and add exactly the
   supplied `nginx-location.conf` inside its existing TLS server block. The
   enabled file is a symlink to this file. The exact `/ws/id` route goes to
   the new loopback service and replaces `X-Real-IP` from `$remote_addr`.
3. Validate nginx configuration and gracefully reload nginx. Its certificate,
   renewal configuration, browser pages, presence API and `/ws/relay` location
   stay as configured. hbbs/hbbr containers, image, identity, database, native
   ports, global server limits and DNS do not change.
4. Verify local liveness, strict public TLS and a dedicated synthetic customer
   registration/session through port 443. Verify the existing native path
   using synthetic peers. These ordinary test clients use the live public pin
   and dedicated test data; they never access a customer's device.

Rollback restores only the backed-up nginx site, validates it and gracefully
reloads nginx. Existing HTTPS connections may still use the earlier worker
and gateway; keep the new container running until local `/status` reports
zero active connections so an ongoing support session is not interrupted.
Then stop/remove only this new component. Do not restart or replace hbbs/hbbr,
change the relay key, remove device records, touch DNS, alter certificates or
modify marketplace submissions as part of deployment or rollback.
