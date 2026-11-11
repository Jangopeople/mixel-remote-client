# Mixel Remote: controller wire protocol (as implemented)

Source of truth: the code at commit `5d66eaf` of this worktree. Paths are relative to the
repo root. `SS` = `crates/signaling_server/src`, `CP` = `crates/core_protocol/src`,
`DA` = `crates/desktop_app/src`, `WC` = `crates/web_client/src`. Every statement carries a
`file:line` reference. Items marked **(derived)** follow from library behaviour, not from a
line in this repo, and need a test vector before being relied on.

JSON conventions (serde defaults, used everywhere below):
`[u8; N]` and `Vec<u8>` are JSON arrays of numbers (not base64, not hex); `Uuid` is a
hyphenated string; `Option` is `null` or the value; `u64` is a JSON number.

Production public configuration: `deployment/public-service-config.json`
(`identity_url` = `authority_url` = `https://ism-api.mixel.ch`, `signaling_url` =
`wss://rs.mixel.ch:9444/v1/ws`, one `authority_keys` entry, `oauth_provider` = `keycloak`,
`relay_only` = `true`). Schema and validation: `DA/service_config.rs:8-25`, `138-234`.

---------------------------------------------------------------------------------------

## 1. Sign-in and tokens

Identity service is Supabase-style (GoTrue + PostgREST) at `identity_url`. Every request
carries header `apikey: <public_key from config>`; authenticated requests add
`Authorization: Bearer <access_token>` (`DA/account_auth.rs:199-217`, `SS/auth.rs:892-906`).

### 1.1 OAuth / PKCE (native flow, `DA/account_auth.rs:430-525`)
1. `verifier` = base64url-no-pad of 32 random bytes; `state` likewise (`296-302`).
   `challenge` = base64url-no-pad(SHA-256(ASCII bytes of `verifier`)) (`440`).
2. Redirect URI: `http://127.0.0.1:<ephemeral port>/auth/callback?state=<state>` (`432-441`).
   Native app listens on loopback; no custom URL scheme is used.
3. Authorize URL opened in the system browser (`475-484`):
   `GET {identity_url}/auth/v1/authorize?provider=<oauth_provider>&scopes=openid profile email&redirect_to=<redirect>&code_challenge=<challenge>&code_challenge_method=s256`
   If `sso_domain` is configured instead: `POST /auth/v1/sso` with
   `{"domain","redirect_to","skip_http_redirect":true,"code_challenge","code_challenge_method":"s256"}` -> `{"url"}` (`468-473`).
4. Callback must be `GET /auth/callback?...` with exactly one `state` (equal) and one `code`
   (`1057-1106`). 180 s wait (`1036`).
5. Token exchange (`514`): `POST /auth/v1/token?grant_type=pkce` body
   `{"auth_code":<code>,"code_verifier":<verifier>}` (no bearer) ->
   `{"access_token","refresh_token","expires_in"}` (`35-40`; `expires_in` must be 1..=31 days, `41-48`).
6. Refresh (`806-812`, `732-737`): `POST /auth/v1/token?grant_type=refresh_token` body
   `{"refresh_token":<token>}`. Native refreshes at `expires_at - clamp(remaining/5, 1, 60)` s (`627`).
7. Logout: `POST /auth/v1/logout?scope=local` with bearer (`159-171`).

GoTrue's redirect allow-list was extended only for `127.0.0.1` decimal-port callbacks
(`deployment/QA-DEPLOYMENT.md:34-35`). A browser origin redirect is **not** allow-listed by
anything in this repo.

### 1.2 The access token and "staff" / MFA
The access token is the GoTrue JWT. It is used (a) as bearer for PostgREST/RPC, (b) as bearer to
the identity authority (section 2.4). It is **never** sent to the signaling server in production.

`IsmAuth::authenticate_scope(token, prefer_personal)` (`SS/auth.rs:995-1076`), run by the client
(`DA/account_auth.rs:248`) and by the identity authority (`SS/identity_authority.rs:120-123` via
`authenticate_device`):
- token: <= 16000 chars, 3 dot-separated non-empty parts, charset `[A-Za-z0-9-_.]` (`1000-1011`);
  claims `role == "authenticated"`, `exp > now` (`1021`); claims are only trusted after
  `GET /auth/v1/user` succeeds and `user.id == claims.sub` (`1024-1027`).
- personal scope (when `prefer_personal`): `GET /rest/v1/rpc/remote_personal_account` returns
  `[{"profile_id","display_name"}]`; a non-null `profile_id` gives
  `Principal{user: id, tenant: Some(id), staff: false}` (`1028-1040`, `558-591`).
  `is_personal()` = `!staff && tenant == Some(user)` (`18-20`).
- otherwise `GET /rest/v1/users?auth_user_id=eq.<uid>&is_active=eq.true&select=id,auth_user_id,organization_id,is_active,role&limit=2`,
  exactly one row, role in `admin|agent|user` (`1041-1050`).
- `staff = organization_id IS NULL && role in (admin, agent)` (`1051-1052`); no organization and
  not staff -> rejected (`1053-1055`).
- **staff requires JWT claim `aal == "aal2"`** (`1056-1058`).
- organization members: `GET /rest/v1/organizations?id=eq.<tenant>&select=id,is_active&limit=2`
  must be active (`1059-1069`).
- Result `Principal{user: profile.id, tenant: organization_id, staff, expires_at: claims.exp}` (`1070-1075`).

MFA step-up (`DA/account_auth.rs:941-997`): `POST /auth/v1/factors/<factor>/challenge` body `{}`
-> `{"id"}`; `POST /auth/v1/factors/<factor>/verify` body `{"challenge_id","code"}` -> new
`Tokens` (aal2). Factors come from `/auth/v1/user` `.factors[]` with `status=="verified"`,
`factor_type=="totp"` (`596-601`). Code is 6 or 8 digits, max 5 attempts (`946`, `956`).

### 1.3 Calls after login (native)
- `GET /auth/v1/user` (`DA/account_auth.rs:244-246`), then `authenticate` (above).
- `POST /rest/v1/rpc/remote_my_company_administration` body `{}` (`151-158`, optional).
- Device directory: personal -> `GET /rest/v1/rpc/remote_personal_devices`; company ->
  `GET /rest/v1/remote_device_enrollments?select=device_id,connection_id,generation,devices!inner(id,name,os)&revoked_at=is.null&order=connection_id.asc&limit=51[&connection_id=gt.<after>][&devices.name=ilike.*<q>*]`
  (`SS/auth.rs:593-612`, `666-767`).
- Personal account creation: `POST /rest/v1/rpc/remote_create_personal_account`
  `{"requested_name"}` -> uuid (`DA/account_auth.rs:849-855`).

---------------------------------------------------------------------------------------

## 2. Device identity and enrollment

### 2.1 Keys
- One Ed25519 device key per device/scope (`DA/device_identity.rs:45`; stored as
  `MIXEL-IDENTITY\x01` + 32-byte seed in the OS key store, `35-60`). It signs: enrollment login
  proof (`331-345`), delegation proof (`266-284`), session `Hello` (`303-322`).
- Authority Ed25519 root(s): `authority_keys` (hex, 1..=4, `DA/service_config.rs:225-234`,
  `SS/delegated_auth.rs:39-63`). Only the identity authority holds the private key
  (`SS/identity_authority.rs:1-2`).
- Per session: ephemeral X25519 (section 4.5).

### 2.2 Enrollment (binds public key -> device row -> 9-digit `connection_id`)
All are PostgREST RPCs on `identity_url` with `apikey` + bearer; key is sent as text
`"\\x" + 64 lowercase hex` (a Postgres bytea literal).
- Personal account (requires `is_personal()`), `SS/auth.rs:614-651`:
  `POST /rest/v1/rpc/remote_register_personal_device`
  `{"device_name":<1..80 chars>,"device_os":"macos"|"windows"|"linux","key_material":"\\x<hex>"}`
  -> `u32` connection id; then verified against `GET /rest/v1/rpc/remote_personal_devices`
  (rows `{device_id,connection_id,generation,public_key,revoked_at,name,os}`, `59-74`, `593-612`).
- Company device, **MFA staff only** (`188`): optional
  `POST /rest/v1/rpc/remote_add_device` `{"target_client":<uuid>,"device_name","device_os"}` -> device uuid (`255-290`);
  then `POST /rest/v1/rpc/remote_enroll_device` with header
  `accept: application/vnd.pgrst.object+json`, body `{"target":<device uuid>,"key_material":"\\x<hex>"}`
  -> `{device_id,connection_id,generation,public_key,revoked_at}` (`177-228`). Add-only: an
  existing different key is never replaced (`174-176`).
- Rollout token (no user sign-in; bearer = the public key): `POST /rest/v1/rpc/remote_enroll_with_token`
  `{"token":"mrt_...","device_name","device_os","key_material"}` ->
  `{device_id,connection_id,generation,profile,tenant}` (`389-427`).
- `device_os` accepts only `macos|windows|linux` in every path (`267`, `402`, `41-57`); there is
  no "web"/"browser" value.
- An organization member with role `user` cannot enroll a device itself: `enroll` requires
  `staff` (`188`), `register_personal_device` requires a personal principal (`624`).

### 2.3 Resolving the own enrollment before connecting (`DA/remote_session.rs:181-209`, `1706-1711`)
1. `device_for_key(token, pubkey)` (`SS/auth.rs:768-808`): personal -> match in
   `remote_personal_devices`; else
   `GET /rest/v1/remote_device_enrollments?public_key=eq.%5Cx<hex>&select=device_id,connection_id,generation,public_key,revoked_at&limit=2` (exactly one, not revoked).
2. `authenticate_device(token, device)` (`924-985`): personal row, or
   `GET /rest/v1/remote_device_enrollments?device_id=eq.<uuid>&select=...&limit=2`
   -> `EnrolledPrincipal{principal, device, connection_id, generation, public_key}` (`26-34`).
3. Stored key must equal the enrolled key (`DA/remote_session.rs:1709-1711`).

### 2.4 DeviceTicket (`CP/delegation.rs`)
```
DeviceLoginChallenge { nonce:[u8;32], device:[u8;16], profile:[u8;16],
                       tenant:Option<[u8;16]>, connection_id:u32, generation:u64 }   CP/device_login.rs:9-16
DelegationProof      { challenge: DeviceLoginChallenge, issued_at:u64, signature:Vec<u8> }   delegation.rs:14-18
DeviceTicket         { version:u16, identity: DeviceLoginChallenge, public_key:[u8;32],
                       staff:bool, issued_at:u64, expires_at:u64, signature:Vec<u8> }        delegation.rs:88-96
```
All three are `deny_unknown_fields`.
- `challenge.bytes()` = `"Mixel Remote enrolled device login v1\0"` || nonce(32) || device(16) ||
  profile(16) || (tenant present ? 1 : 0) || tenant or 16 zero bytes || connection_id BE u32 ||
  generation BE u64 (`CP/device_login.rs:18-28`). Valid only if nonce/device/profile non-zero,
  tenant != zero, connection_id in 100000000..=999999999, generation > 0 (`29-36`).
- Delegation proof signature (device key, Ed25519) over
  `"Mixel Remote ISM device delegation request v1\0"` || issued_at BE u64 || `challenge.bytes()`
  (`CP/delegation.rs:20-30`, `47-63`). Accepted if `issued_at` in [now-30, now+5] (`72-79`).
- Ticket signature (authority key) over `"Mixel Remote delegated broker and peer identity v1\0"` ||
  version BE u16 || `identity.bytes()` || public_key(32) || staff(1) || issued_at BE u64 ||
  expires_at BE u64 (`108-117`).
- Ticket validity (`177-198`): version 1 (interactive, lifetime <= `MAX_TICKET_LIFETIME` = 900 s,
  `8`, `10`) or version 2 (unattended host only, <= 120 s, never staff, `9`, `11`);
  `staff == identity.tenant.is_none()`; `issued_at <= now+5`; `issued_at < expires_at`;
  `expires_at > now`; non-weak key. `verify(roots, now)` = validity + `verify_strict` against any
  of 1..=4 roots (`199-220`).
- `fingerprint()` = SHA-256(ticket signing bytes || signature) (`102-107`).
- Authorization rule: `can_request(target)` = not unattended-host-only and (staff, or same
  non-null tenant) (`248-253`; same rule in `SS/auth.rs:22-24`, `SS/registry.rs:39-43`).

### 2.5 Obtaining and renewing a ticket (`DA/device_tickets.rs:32-131`)
`POST {authority_url}/v1/device-ticket`, header `Authorization: Bearer <access_token>`, JSON body =
`DelegationProof` with a fresh random 32-byte `nonce` and `device/profile/tenant/connection_id/generation`
from the enrollment (`39-50`, `79-83`). Response body = `DeviceTicket` JSON (<= 8192 bytes, `112`).
Server side (`SS/identity_authority.rs:95-153`):
- **rejects any request that has an `Origin` header with 403** (`100-104`; deployment doc
  confirms intent, `deployment/QA-DEPLOYMENT.md:29-31`, `67`);
- 401 without bearer (`105-110`); runs `authenticate_device(bearer, device)` (`120-126`); requires the
  proof's identity fields to equal the enrollment and the JWT to have >= 30 s left (`129-136`);
  verifies the proof with the enrolled key (`137-139`); replay cache on `(device, nonce)` for 40 s ->
  409 (`49-65`, `140`); body limit 8192 (`72`);
- issues `DeviceTicket::issue(..., staff, now, min(jwt exp, now+900))` (`141-152`).
Client must check (`DA/device_tickets.rs:119-129`): signature against `roots`, `key == own key`,
`ticket.identity == sent challenge`, `!unattended_host_only()`, `staff` matches,
`expires_at <= JWT exp`.
Renewal = the same call again with a fresh nonce (`DA/remote_session.rs:1811-1820`), see 3.6.
`/v1/startup-host-ticket` (`SS/identity_authority.rs:162-214`) is for unattended hosts only.

### 2.6 `controller_only`
Compile-time: `cfg!(feature = "testflight-controller")` (`DA/service_config.rs:26-28`,
`DA/Cargo.toml:11`). Effects: cannot host (`30-35`), every incoming `Consent` is answered with
`Decide{accept:false}` (`DA/remote_session.rs:1941`), no capture targets (`DA/main.rs:339-341`),
separate key-store scope (`DA/device_identity.rs:25-32`, `84`, `217`). It does **not** remove the
enrollment requirement: the controller build still runs `auth.enrollment` + `registration_ticket`
+ `AuthenticateDevice` (`DA/remote_session.rs:1706-1746`).

### 2.7 Can a controller connect without an enrolled device? No.
- `ClientMessage::Authenticate{token}` exists (`SS/protocol.rs:65`) and `server.rs:159` accepts it
  syntactically, but the production binary uses `TicketAuth`, whose `authenticate` always returns
  `Err(AuthError)` (`SS/main.rs:18-20`, `SS/delegated_auth.rs:76-82`) -> the socket is dropped
  without a reply (`SS/server.rs:179-181`).
- Even with an authenticator that accepts it: `Ice` returns `Forbidden` unless `enrolled.is_some()`
  (`SS/server.rs:451-452`); `Presence` is `Forbidden` without identity (`SS/registry.rs:580-583`);
  with a policy configured, a request needs both identities (`SS/registry.rs:134-138`,
  `SS/server.rs:405-441`) -> `Unavailable`; the host rejects a `Consent` without
  `requester_identity` (`DA/remote_session.rs:1942`) and an `Accepted` without
  `controller_identity` (`1975`).
- Attended vs unattended is orthogonal: both need an enrolled controller. Attended:
  `Request` / `RequestWithCode` -> host user sees the incoming request and picks Accept/Reject
  (`DA/remote_session.rs:1960`). Unattended: `RequestUnattended` -> only if the host enabled
  it (`SS/registry.rs:611-616`), auto-accepted on the host with `clipboard/files/chat/recording = false`
  (`DA/remote_session.rs:1949-1967`).
- One live connection per `connection_id`: `register_with_id` returns `Busy` when the id is
  already registered and the new socket is closed (`SS/registry.rs:319-324`, `SS/server.rs:247-249`).
  A browser cannot reuse the enrollment of a desktop app that is online.

---------------------------------------------------------------------------------------

## 3. Signaling WebSocket

### 3.1 Transport
- URL `wss://rs.mixel.ch:9444/v1/ws` (route `/v1/ws`, `SS/server.rs:106-111`). No subprotocol, no
  `Authorization` header, no cookies: native connects with a bare URL
  (`DA/remote_session.rs:1721-1724`).
- **Origin check** (`SS/server.rs:117-121`): if the upgrade has an `Origin` header it must be an
  exact member of `MIXEL_WEB_ORIGINS`, else HTTP 403. No `Origin` header (native) passes.
  `MIXEL_WEB_ORIGINS` is mandatory, comma-separated, each an exact `https://` origin
  (`SS/main.rs:21-35`). Its deployed value is not in this repo. The binary binds loopback only;
  TLS is terminated by nginx (`SS/main.rs:13-17`), whose config is also not in this repo.
- Frames: UTF-8 **text** JSON only. A binary frame closes the connection (`SS/server.rs:462`).
  Max message/frame 65536 bytes (`SS/protocol.rs:61`, `SS/server.rs:125-126`).
- Limits: > 60 inbound frames per second closes (`306-308`); 75 s without any inbound frame
  closes (`267`); server sends WS Ping every 20 s (`269-272`) and any inbound frame including
  Pong counts as activity (`300-305`, `311`). Native also pings every 20 s and gives up after 65 s
  of silence (`DA/remote_session.rs:1787`, `1806`). First message must arrive within 5 s
  (`SS/server.rs:154`). 1000 connections, 32 concurrent authentications (`98-99`).
- Unparseable or unknown message -> `{"type":"error","code":"invalid"}` (`457`);
  `ClientMessage` is `deny_unknown_fields` (`SS/protocol.rs:63`).

### 3.2 Message shapes
Client -> server (`SS/protocol.rs:62-90`), tag `type`, snake_case:
```
{"type":"authenticate_device","token":"<base64url-no-pad(JSON(DeviceTicket))>","device":"<uuid>"}
{"type":"prove_device","signature":[64 numbers]}
{"type":"renew","token":"<encoded new ticket>"}
{"type":"presence","hosts":[123456789,...]}                 max 64 (registry.rs:21,577)
{"type":"request","host":123456789}
{"type":"request_with_code","host":123456789,"code":"<12 hex chars>"}   protocol.rs:21-23
{"type":"request_unattended","host":123456789}
{"type":"ice","session":"<uuid>"}
{"type":"signal","session":"<uuid>","payload":{"kind":"identity","value":<Hello>}}
{"type":"signal","session":"<uuid>","payload":{"kind":"offer","value":"<sdp>"}}
{"type":"signal","session":"<uuid>","payload":{"kind":"ice","value":"<JSON text of candidate>"}}
{"type":"restart","session":"<uuid>"}
{"type":"close","session":"<uuid>"}
```
Host-only messages (not for a controller): `configure_unattended`, `generate_support_code`,
`clear_support_code`, `check_pairing`, `confirm_pairing`, `decide`, `agent`.
Ticket encoding: `serde_json` bytes (<= 4096) -> base64url no padding (`SS/delegated_auth.rs:17-23`;
verify requires canonical re-encoding, `29-37`).
`Signal` (`SS/protocol.rs:110-131`): tag `kind`, content `value`; offer/answer 1..=60000 bytes, ice
1..=4096 bytes.

Server -> client (`SS/protocol.rs:140-219`), tag `type`:
`device_challenge{challenge}`, `ready{host,protocol}`, `renewed{expires_at}`, `presence{online:[u32]}`,
`requested{session}`, `accepted{session,nonce:[32],controller_identity,host_identity}`,
`ice{session,lease}`, `signal{session,payload}`, `peer_renewed{session,identity}`,
`restarted{previous,session,nonce,controller_identity,host_identity}`,
`closed{session,reason}` with reason `rejected|disconnected|expired|requested` (`220-227`),
`error{code}` with code `forbidden|busy|unavailable|invalid` (`228-235`). Host-side only:
`consent`, `support_code`, `pairing_status`, `unattended_status`, `agent`.

### 3.3 Login sequence (`SS/server.rs:153-249`, `DA/remote_session.rs:1740-1747`)
1. C: `authenticate_device{token, device}`. Server verifies the ticket against its roots
   (`SS/delegated_auth.rs:83-111`), requires `ticket.identity.device == device` (`95-97`).
   Budget 17 s (`SS/server.rs:167`). Any failure: socket closed, no message.
2. S: `device_challenge{challenge}` with fresh `nonce`, `device`, `profile`, `tenant`,
   `connection_id`, `generation` (`190-211`). Client checks every field except the nonce against
   its enrollment (`DA/remote_session.rs:1743`).
3. C (within 5 s, `SS/server.rs:212`): `prove_device{signature}` = Ed25519 signature by the device
   key over `challenge.bytes()` (2.4) (`CP/device_login.rs:37-42`; verified `SS/server.rs:219`).
4. S: `ready{host: connection_id, protocol: 2}` (`SS/registry.rs:339-345`; `PROTOCOL_VERSION` = 2,
   `CP/lib.rs:13`). Client requires both to match (`DA/remote_session.rs:1746`).

### 3.4 Requesting a session (controller)
1. Before sending anything: resolve and pin the host key independently of signaling,
   `peer_key(token, host)` (`DA/remote_session.rs:1827`): personal row from
   `remote_personal_devices`, else
   `GET /rest/v1/remote_device_enrollments?connection_id=eq.<id>&select=device_id,connection_id,generation,public_key,revoked_at&limit=2`
   (`SS/auth.rs:811-853`).
2. C: `request` | `request_with_code` | `request_unattended` (`DA/remote_session.rs:1829-1834`).
   Server checks (`SS/registry.rs:601-690`): target online else `unavailable`; `can_request`;
   unattended needs `unattended_enabled`; max 4 sessions per peer (`20`, `617-627`) -> `busy`;
   code gate: if the host has a one-time code, only `request_with_code` with the right code works,
   5 attempts, 300 s lifetime (`45-46`, `630-659`); a host without a code gate rejects
   `request_with_code` (`658`). With policy configured the external policy must allow the pair
   (`SS/server.rs:405-441`, `SS/support_policy.rs:88-104`) else `unavailable`.
3. S: `requested{session}` to the controller, `consent{...}` to the host (`SS/registry.rs:675-689`).
   Consent phase deadline 90 s (`668`), then `closed{expired}` (`410-420`).
4. S (after host `decide{accept:true}`): `accepted{session, nonce, controller_identity, host_identity}`
   to both; negotiation deadline 30 s (`704-705`, `709-725`). Reject -> `closed{rejected}` (`700-703`).
5. Controller validation of `accepted` (`DA/remote_session.rs:1969-1993`):
   - session id is the one from `requested`; `nonce != 0` (`1970`, `1974`);
   - both tickets present (`1975-1976`); host ticket not unattended-only unless this was an
     unattended request (`1977-1979`);
   - own (`controller_identity`) ticket verifies against roots and equals the own ticket on
     device, version, profile, tenant, connection_id, generation, public_key, staff
     (`1982-1983`, `same_identity` `1452-1464`);
   - host ticket: `verify(roots, now)`, `identity.connection_id == requested host id`, and
     `own.staff || same non-null tenant` (`peer_identity`, `1465-1491`);
   - host ticket key **equals the key pinned in step 1** (`1985`);
   - remember `host.expires_at` as the peer expiry (`1986`).
6. Session id for the handshake:
   `session_binding = SHA-256("Mixel Remote identity-bound attended session v1\0" || session uuid (16 raw bytes) || nonce(32) || controller_ticket.fingerprint() || host_ticket.fingerprint())`
   (`CP/delegation.rs:222-235`, used at `DA/remote_session.rs:1990`). This 32-byte value, not the
   raw nonce, is `Context.session_id` (`DA/remote_session.rs:2009` -> `DA/remote_peer.rs:338-339`).
7. C: `ice{session}` (`1993`). S: `ice{session, lease}` (`SS/server.rs:451-454`):
   ```
   IceLease  { expires_at: u64, servers: [IceServer] }          SS/turn.rs:27-32
   IceServer { urls: [String], username: String, credential: String }   SS/turn.rs:12-18
   ```
   coturn REST credentials: `username = "<expires_at>:mixel-<session>-<connection_id>"`,
   `credential = base64(HMAC-SHA1(secret, username))` (`SS/turn.rs:101-115`); default TTL 600 s
   (`54-55`), capped by ticket expiry and the 900 s authorization window (`SS/server.rs:453`).
   URLs are `turn:`/`turns:` with optional `?transport=udp|tcp` (`SS/turn.rs:120-156`). Rate limit:
   one `ice` per 5 s per peer else `busy` (`SS/registry.rs:284-287`); refused while still in consent
   phase or with < 30 s authorization left (`275-283`).
   Controller checks (`DA/remote_session.rs:1999-2003`): `now < expires_at <= now+3600`,
   `expires_at <= own ticket expiry`, <= 8 servers, each 1..=8 urls <= 1024 chars,
   username <= 256, credential <= 1024.
   Signals that arrive between `accepted` and the lease are buffered (max 32, `2018`).
8. Signals. Server state machine (`SS/registry.rs:728-772`): `identity` once per side in any
   post-accept phase (`740-750`); `offer` only from the controller in phase Offer (`752-754`);
   `answer` only from the host (`755-761`); `ice` only after the offer was relayed (`762`);
   anything else `forbidden`. After the answer the session lives 8 h (`757-760`).
   Controller order: `identity` then `offer` (`DA/remote_peer.rs:415-420`), then trickle `ice`.
9. Ending: C `close{session}` -> both get `closed{requested}` (`SS/registry.rs:773-780`); peer
   socket loss -> `closed{disconnected}` (`391-409`). Messages for a session closed < 60 s ago
   are silently ignored (`437-449`).
   `error` handling in native (`DA/remote_session.rs:2020-2027`): after a pending request ->
   show reason, stay registered; after `accepted` (relay unavailable) -> send `close`; otherwise fatal.

### 3.5 Presence
`presence{hosts}` -> `presence{online}`: only ids the caller may request and that are connected
(`SS/registry.rs:576-600`). Requires an identity (ticket) on the connection.

### 3.6 Authorization lifetime and Renew
- Server closes the socket when `now >= ticket.expires_at` or 900 s after the last
  (re)authorization (`SS/server.rs:150-152`, `267`, `304`).
- C: `renew{token: <new encoded ticket>}`. Server: at most one per 30 s, else `error busy`
  (`318-321`); re-verifies; same device, connection_id, generation, key, ticket version
  (`330-338`), same user/tenant/staff (`SS/registry.rs:350-366`); failure closes the socket and
  its sessions (`343`). Success: `renewed{expires_at}` (`354`) and every active peer gets
  `peer_renewed{session, identity}` (`SS/registry.rs:237-263`).
- Native timing (`DA/remote_session.rs:1811-1820`): renew when the access token changed, or
  720 s since the last renewal, or <= 120 s left; never more often than every 31 s; skip if the new
  ticket does not extend the expiry (`1818`); `renewed` must arrive within 20 s and carry exactly
  the new ticket's `expires_at` (`1820`, `1903`, `1786`).
- The host stops the session when the controller's ticket expires
  (`DA/remote_peer.rs:61-83`, `peer_expires`), so renewing is required for sessions > 15 min.
- On `peer_renewed` the controller re-runs the host ticket checks and accepts only an identical
  identity with non-decreasing `issued_at`/`expires_at` (`DA/remote_session.rs:2011-2017`).

### 3.7 Restart (transport/relay rotation, controller-initiated)
Native sends `restart{session}` when the relay lease has <= 90 s left or 720 s after the last
rotation, never within 31 s (`DA/remote_session.rs:1801-1804`). Server: only the controller, phase
Connected, 30 s spacing (`SS/registry.rs:459-522`); both get
`restarted{previous, session, nonce, controller_identity, host_identity}`. Client treats it as a
fresh `accepted` for the new session id: new binding, new `ice` lease, a completely new peer
connection and handshake; host grants stay (`DA/remote_session.rs:1889-1901`). No `restarted`
within 20 s is fatal (`1800`).

---------------------------------------------------------------------------------------

## 4. WebRTC negotiation (controller = offerer)

Native reference: `DA/remote_peer.rs:297-715` (`negotiate`), `CP/transport.rs`.

### 4.1 Peer connection
- ICE servers from the lease; with `relay_only` the policy is `Relay` and mDNS is off
  (`CP/transport.rs:189-203`, `DA/remote_peer.rs:193-204`). Browser: `iceTransportPolicy: "relay"`.
- One video transceiver, direction `recvonly` (`CP/transport.rs:80-91`, `DA/remote_peer.rs:372-375`).
- One data channel created by the controller before the offer: label `mixel-control-v1`, default
  options (`DA/remote_peer.rs:381-387`; wasm `create_control_channel` sets `ordered: true`,
  `WC/wasm.rs:173-178`). The host rejects anything that is not ordered, fully reliable (no
  `maxPacketLifeTime`, no `maxRetransmits`), empty `protocol`, non-negotiated (`437-459`).
- Optional further channels created by the controller after the handshake: `mixel-files-v1`
  (`DA/remote_file_transport.rs:56`) and `mixel-chat-v1` (`DA/remote_chat.rs:17`, `173`); they use
  the separately derived bulk/chat keys (4.5). Not needed for view/control.

### 4.2 Offer
- `createOffer` -> `setLocalDescription`, wait up to 3 s for gathering to complete, then send the
  local description including the candidates gathered so far (`CP/transport.rs:236-285`). With
  relay-only, an offer with no candidate or any non-`typ relay` candidate is an error (`266-283`).
- Appended to the SDP text that is **sent** (after `trim_end`, each line `\r\n`-terminated,
  `DA/remote_peer.rs:416-419`):
  ```
  a=x-mixel-recording:1        controller understands the v4 permission record (301, 478)
  a=x-mixel-input-result:1     host may answer "unsupported" instead of "posted" (302, 479)
  a=x-mixel-displays:1         controller can switch host displays (304, 486)
  a=x-mixel-actions:1          controller understands host actions (311, 481)
  a=x-mixel-features:<csv>     optional features, native sends clip2,allmon,print,sound,hd
  ```
  Feature names: `clip2` (`CP/clipboard.rs:16`), `allmon`, `hd` (`DA/remote_control.rs:69-71`),
  `print` (`DA/host_print.rs:7`), `sound` (`CP/sound.rs:4`); list at `DA/remote_control.rs:59-67`.
  Parser keeps <= 16 names, each 1..=16 ASCII alphanumerics (`DA/remote_peer.rs:318-329`).
  Announce only what is implemented: `sound` makes the host send sound records by default
  (`DA/remote_control.rs:260`, `330-332`), `print` makes it send print jobs (`271-273`), `hd`
  switches the stream limit from 1280x720 to 1920x1080 (`DA/remote_peer.rs:708-713`, `257-259`).

### 4.3 Answer (host -> controller)
Extra lines the controller reads (`DA/remote_peer.rs:555-585`):
```
a=x-mixel-display-count:<count>:<current>   count 2..=16, current < count (305, 556-562)
a=x-mixel-display-all:1                     last index = all monitors as one picture; needs count >= 3 (307, 564)
a=x-mixel-host-actions:<csv>                subset of sas,restart,wol,sound (312, 565-569; DA/host_actions.rs:7-12)
a=x-mixel-macs:<csv>                        up to 4 MACs, 12 hex digits each, for wol (314, 570-580)
a=x-mixel-signin-id:<9 digits>              Remote ID of the host's sign-in-screen access (313, 581-585)
a=x-mixel-features:<csv>                    host features; only sent if the offer had features (549-551)
```
Displays and actions are only offered when the host granted control (`481`, `486`) and are
ignored by the controller if the permission record says no control (`668`, `678`).
Then `setRemoteDescription(answer)` (`586-594`).

### 4.4 ICE candidates in `Signal::Ice`
`value` is a **string** containing the JSON of `RTCIceCandidateInit`
(`DA/remote_peer.rs:430-435`, `596-603`): camelCase
`{"candidate":"candidate:...","sdpMid":"0","sdpMLineIndex":0,"usernameFragment":"..."}`
(vendored `rtc` 0.21.1 `peer_connection/transport/ice/candidate.rs`, `#[serde(rename_all = "camelCase")]`,
unknown fields tolerated). `RTCIceCandidate.toJSON()` output matches. Max 4096 bytes
(`SS/protocol.rs:128`). The server only relays `ice` after the offer (3.4 step 8).

### 4.5 Identity hello and key agreement (`CP/crypto.rs`)
```
Hello { role: "controller"|"host", ephemeral:[u8;32], dtls_fingerprint:[u8;32], signature:[u8;64] }   38-46
Context { session_id:[u8;32], controller: Ed25519 pk, host: Ed25519 pk }                              59-64
```
- `Context.session_id` = `session_binding` (3.4 step 6). `controller` = own device key,
  `host` = pinned host key (`DA/remote_peer.rs:338-350`).
- `Handshake::new(context, Role::Controller, identity_key, local_fingerprint)` (`117-146`): fresh
  X25519 ephemeral; `signature` = Ed25519 (device key) over
  `"Mixel Remote control v1\0" || session_id(32) || controller_pk(32) || host_pk(32) || role byte (0 controller, 1 host) || ephemeral(32) || dtls_fingerprint(32)`
  (`66-79`, `33-35`). The key must equal `context.controller` (`123-132`).
- **Local fingerprint** (`CP/transport.rs:321-341`): the 32 bytes of the `a=fingerprint:sha-256 XX:..`
  line of the **own local SDP** (offer). Only `sha-256` is accepted; all `a=fingerprint` lines
  must be identical. This equals SHA-256 of the own DTLS certificate (DER).
- **Remote fingerprint** (`CP/transport.rs:297-305`): SHA-256 over the DER bytes of the first
  (leaf) certificate returned by `sctp.transport().get_remote_certificates()` of the established
  DTLS transport. It is not taken from the remote SDP or from the peer's `Hello`.
- `finish(peer_hello, observed_fingerprint)` (`152-220`), called after the control channel is
  open (`DA/remote_peer.rs:609-642`): peer role must differ; `peer.dtls_fingerprint == observed`;
  peer signature verifies (strict) with the pinned host key; X25519 must be contributory.
  `transcript = Context.bytes() || signing_bytes(controller hello) || signing_bytes(host hello)`;
  `binding = SHA-256(transcript)`; `HKDF-SHA256(salt = binding, ikm = shared secret)`;
  expand with info `"Mixel Remote directional AES256GCM v1"` to 64 bytes: bytes 0..32 =
  controller->host key, 32..64 = host->controller key (`182-199`, `240-268`).
  Bulk channel: info `"Mixel Remote bulk directional AES256GCM v1"`, binding
  `SHA-256(binding || "Mixel Remote bulk records v1")`; chat: `"Mixel Remote chat directional AES256GCM v1"`,
  `SHA-256(binding || "Mixel Remote chat records v1")` (`202-217`).
- The whole negotiation (offer to open channel) has 30 s (`DA/remote_peer.rs:331`, `427`, `613`).

---------------------------------------------------------------------------------------

## 5. Control channel (`mixel-control-v1`)

### 5.1 Record format (`CP/crypto.rs:289-347`)
Binary data-channel messages only; a text message or a message > 4120 bytes ends the session
(`DA/remote_control.rs:678-697`).
`record = sequence (u64 BE, 8 bytes) || AES-256-GCM ciphertext || tag (16 bytes)`;
nonce = 4 zero bytes || sequence BE (`234-238`); AAD = `binding(32) || sequence(8)` (`301-302`);
plaintext <= 4096 bytes (`19`, `290`). Sequence starts at 0 per direction and must arrive exactly
in order; any gap, replay or failed tag is an error (`326-328`). No rekey: 2^32 records max (`24`).

### 5.2 First record: host permissions (`DA/remote_control.rs:20-43`, `149-189`)
The controller waits <= 5 s after the handshake (`179-181`). Plaintext:
- `"mixel-permissions-v4"` + 4 bytes `[control, clipboard, files, recording]` (if the offer had
  `a=x-mixel-recording:1`), or
- `"mixel-permissions-v3"` + 3 bytes `[control, clipboard, files]`.
Each byte is 0 or 1; wrong length or value is an error (`35-37`). Test vectors: `707-713`.
`files == 1` -> take the bulk channel keys (`220-228`).

### 5.3 Records the controller sends
| Plaintext | Meaning | Reference |
|---|---|---|
| bincode `Input` | keyboard/mouse; only when control granted | `DA/remote_control.rs:475-493`, `CP/input.rs` |
| `mixel-video-keyframe-v1` | ask for a keyframe; throttled to 1/s on both sides | `421-445`, `632-641` |
| `mixel-display-select-v1` + 1 byte index | show host display N (< offered count); host then forces a keyframe | `23`, `400-417`, `622-631` |
| `mixel-action-v1:` + name | `sas`, `restart`, `wol:<12 hex>`, `sound:on`, `sound:off`; name <= 40 bytes, base name must have been offered | `25`, `387-399`, `604-621` |
| `MCB1` clipboard message | only when clipboard granted | `598-603`, `CP/clipboard.rs` |

`Input` (`CP/input.rs:8-38`), encoded with `bincode` 2.0.1 `config::standard()` (`68-79`);
limits: button 1..=5, clicks 1..=3, key usage 4..=0xe7 (USB HID keyboard usage), text <= 2048
bytes without NUL (`46-58`); non-canonical encodings are rejected (`93-104`).
Byte layout **(derived** from bincode's standard varint config; no test vector in the repo**)**:
variant index as varint, then fields; `u8`/`bool` one raw byte; `u16` varint (0..=250 one
byte, else `0xFB` + u16 LE); `i16` zigzag then varint; `&str` varint length + UTF-8.
```
0 Pointer{display:u16,x:u16,y:u16}     x,y normalised 0..=65535 over the shown picture
1 Button{button:u8,down:bool}
2 Wheel{horizontal:i16,vertical:i16}
3 Key{usage:u16,down:bool}
4 Text(&str)
5 ReleaseAll                            required on focus loss / disconnect (CP/input.rs:29)
6 ButtonClick{button:u8,down:bool,clicks:u8}
```
Native sets `Pointer.display` to the currently selected display index
(`DA/remote_control.rs:480-482`). Pointer normalisation: `round(clamp(x/width,0,1)*65535)`
(`WC/lib.rs:12-25`).

Host reactions that end the session: any input while control is not granted (`642-644`),
an undecodable input (`653`), an invalid display index (`629`), an unoffered action (`607-609`),
clipboard traffic without the clipboard grant (`DA/remote_clipboard.rs:64-66`).

### 5.4 Records the controller receives
| Plaintext | Meaning | Reference |
|---|---|---|
| `mixel-permissions-v3/v4...` | first record only | 5.2 |
| `mixel-input-posted-v1` | acknowledgment of one input record | `DA/remote_control.rs:655-675` |
| `mixel-input-unsupported-v1` | input could not be applied; only if `a=x-mixel-input-result:1` was offered | `657-668` |
| `mixel-video-idle-v1` | screen unchanged; resets the video timeout | `369-386`, `501-503`, `578-579` |
| `mixel-sound-v1:` + frame | one sound frame, if `sound` negotiated and control granted | `334-342`, `574-577` |
| `mixel-print-v1:` + `MCB1` message | a print job (PDF) in parts, if `print` negotiated | `284-328`, `572-573` |
| `MCB1` clipboard message | reply to a clipboard request | `DA/remote_clipboard.rs:213-253` |

Every input record gets exactly one acknowledgment, in order (`645-675`). Native sends one
input and waits <= 2 s for its acknowledgment (`475-514`); the host does not require that.
Any other plaintext is treated as fatal by native (`580-582`).

Clipboard (`CP/clipboard.rs:9-122`): `"MCB1" || kind(1) || id (u64 BE, non-zero)` then
kind 0 `Get`; 1 `Begin` + length u32 BE + SHA-256(32); 2 `Chunk` + offset u32 BE + 1..=3072
bytes; 3 `Commit`; 4 `Ack`; 5 `Unavailable`; 6 `GetRich`. Ids strictly increase per direction
(`DA/remote_clipboard.rs:159-165`, `177-181`).
- Fetch host clipboard: send `Get(id)` (or `GetRich(id)` if both sides have `clip2`); host
  answers `Begin, Chunk..., Commit` or `Unavailable(id)` (`70-85`, `224-253`).
- Push to host: send `Begin, Chunk..., Commit(id)`; host answers `Ack(id)` or `Unavailable(id)`
  (`86-121`, `215`).
- Limits: text 1 MiB / 15 s; rich 32 MiB / 120 s (`CP/clipboard.rs:10-15`). Rich payload: plain
  UTF-8 text, or `00 01` + PNG, or `00 02 count` + per file `name_len u16 BE, name, size u32 BE, data`
  (`223-305`). A print job is a rich payload with exactly one file (`DA/remote_clipboard.rs:147-153`).

Sound frame (`CP/sound.rs:1-12`, `91-102`): mono 24 kHz IMA ADPCM, 963 bytes = predictor i16 BE,
step index u8 (<= 88), 480 bytes of two 4-bit samples (low nibble first) = 960 samples (40 ms).
Silence is not sent. `RECORD_SWITCH` (`CP/sound.rs:8`) is defined but unused; switching is done
with the `sound:on` / `sound:off` action (`DA/remote_control.rs:610-613`).

### 5.5 Timing rules
- Permissions record within 5 s (`DA/remote_control.rs:179`).
- Video track must arrive within 8 s after the handshake; native requests a keyframe repeatedly
  meanwhile (`DA/remote_peer.rs:1052-1070`).
- Session ends when 12 s pass with neither a decoded frame nor `mixel-video-idle-v1`
  (`1247-1251`). The host sends idle at most every 2 s while nothing changes (`939-941`,
  `DA/remote_control.rs:370`).
- There is no controller->host keepalive on the control channel; liveness of the controller is
  enforced through signaling/ticket expiry (3.6) and the peer connection state.
- Viewing authorization object `Session` expires 15 min after activation (`CP/session.rs:7`, `88`);
  native replaces the transport with Restart (3.7) before that: after 720 s at the latest, earlier
  when the relay lease nears expiry.

---------------------------------------------------------------------------------------

## 6. Video

- Host sends one H.264 track: `video/H264`, 90 kHz, fmtp
  `level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f`, no rtcp-fb declared on
  the track codec (`CP/media.rs:63-70`). Track id `mixel-video-<ssrc hex>`, stream `mixel-screen` (`73-76`).
- The host picks the payload type from the negotiated codecs: first H264 entry whose fmtp contains
  `packetization-mode=1` (`137-150`). The browser offer must therefore contain H264 with
  packetization-mode 1; otherwise the host fails with `MediaError::Codec`.
- Encoder: constrained baseline, no B-frames (macOS `kVTProfileLevel_H264_ConstrainedBaseline_AutoLevel`,
  `crates/native_os_capture/src/macos_encoder.rs:111`; Windows/Linux `Profile::Baseline`,
  `crates/native_os_capture/src/desktop_h264.rs:63`). Size <= 1280x720, or <= 1920x1080 when both
  sides announce `hd`; even dimensions (`DA/remote_peer.rs:257-289`, `708-713`). <= 30 fps;
  3 Mbit/s, 6 Mbit/s above 720p (`761-762`).
- SPS/PPS are in-band: a forced keyframe is only accepted by the host encoder wrapper when the
  access unit carries SPS, PPS and an IDR (`desktop_h264.rs:135-137`; macOS prepends the
  parameter sets, `macos_encoder.rs:395-432`). Access units are Annex B, packetised by the WebRTC
  stack (single NAL, STAP-A 24, FU-A 28 are what the native receiver accepts, `CP/media.rs:331-368`).
- Keyframes: every 60th sent frame, after a display switch, and on `mixel-video-keyframe-v1`
  (`DA/remote_peer.rs:912`, `957`, `885-911`). Frames are sent only when the screen changes;
  otherwise `mixel-video-idle-v1` (`935-943`).
- The native controller requests keyframes over the control channel whenever its assembler lost
  data (`1108-1114`, `CP/media.rs:164-166`), never via RTCP. No code in this repo reads RTCP
  PLI/FIR on the host; only the stack's default interceptors are registered (`CP/transport.rs:182`).
- No audio RTP track exists. Sound is section 5.4.
- The sender checks before every frame that the DTLS fingerprints still equal the ones proven in
  the handshake (`CP/media.rs:119-136`).

---------------------------------------------------------------------------------------

## 7. What `crates/web_client` and `frontends/web/tests` provide

`WC/lib.rs`: `normalized_pointer` (`12-25`), `video_rect` (aspect-fit rectangle, `28-55`).
`WC/wasm.rs` (wasm-bindgen exports):
- `ControlIdentity` (`19-69`): `new()` generates a **random** Ed25519 key in wasm memory,
  `public_key()`, `begin(session_nonce, is_controller, peer_key, local_dtls_fingerprint)` ->
  `PendingControl`.
- `PendingControl` (`75-111`): `hello()` -> JSON text of `Hello`; `finish(peer_hello_json, observed_dtls_fingerprint)`
  -> `EncryptedControl`.
- `EncryptedControl` (`112-158`): `pointer`, `button`, `key`, `wheel`, `text`, `release_all`
  (each returns a sealed record), `decrypt_input(record)` (opens a record and decodes it **as Input**).
- `create_peer(configuration_json)` (`162-172`), `create_control_channel(peer)` (`173-178`),
  `draw_video(canvas, video)` (`179-194`).
`frontends/web/tests/browser-loopback.js`: two in-page `RTCPeerConnection`s, checks that
`peer.sctp.transport.getRemoteCertificates()[0]` hashed with SHA-256 equals the other side's SDP
fingerprint (`20-30`, `50-53`), runs the wasm handshake and an input round trip (`54-63`), decodes
a browser-encoded test video (`64-69`). It uses no signaling, no tickets, no host.

Missing for a real controller:
1. Persistent, enrollable identity: no import/export or storage of the key; a new
   `ControlIdentity` is a new unenrolled key every time.
2. `DeviceLoginChallenge` signing (`prove_device`) and `DelegationProof` creation: not exported.
3. `DeviceTicket` verification against `authority_keys`, `fingerprint()`, `session_binding`,
   `same_identity`/`peer_identity` checks: not exported (`begin` takes the already computed binding
   as `session_nonce`).
4. Generic `seal(bytes)` / `open(record) -> bytes`: absent. `decrypt_input` fails on the
   permissions record, idle, acknowledgments, clipboard, sound and print records; keyframe,
   display-select, action and clipboard records cannot be produced.
5. `Input::ButtonClick`: no export. Bulk/chat channel keys (`take_bulk_channel`,
   `take_chat_channel`): no export.
6. Everything outside crypto: OAuth/PKCE, REST/RPC calls, ticket fetch/renew, the signaling state
   machine (section 3), SDP attribute handling, Restart, clipboard/sound/print framing, UI.

---------------------------------------------------------------------------------------

## 8. Risks and unknowns for a browser implementation

1. **Ticket endpoint refuses browsers.** `POST /v1/device-ticket` answers 403 to any request with
   an `Origin` header (`SS/identity_authority.rs:100-104`) and has no CORS handling. A browser
   `fetch` POST always sends `Origin`. Needs a server change (exact-origin allow-list, as the code
   comment suggests) or a same-site backend that forwards without `Origin`.
2. **WebSocket Origin allow-list.** The page origin must be listed in `MIXEL_WEB_ORIGINS`
   (`SS/server.rs:117-121`); deployed value and the nginx rules in front are not in this repo.
3. **CORS and redirect allow-list on `identity_url`.** GoTrue/PostgREST CORS policy and the
   redirect allow-list for a web origin are deployment settings outside this repo
   (`deployment/QA-DEPLOYMENT.md:34-35` lists only loopback callbacks).
4. **Enrollment model.** A browser needs its own enrolled Ed25519 key with its own
   `connection_id` (2.7). `device_os` has no browser value (2.2); company enrollment needs aal2
   staff; the secret key would live in browser storage (wasm memory today, not persisted).
5. **Remote DTLS fingerprint.** Native hashes the certificate from the live DTLS transport.
   Browser equivalents: (a) `RTCDtlsTransport.getRemoteCertificates()` via
   `pc.sctp.transport` or `receiver.transport` -> SHA-256 of element 0; this is byte-for-byte the
   native computation and is what the loopback test does. It is not implemented in every browser
   (Firefox lacks it; check Safari). (b) Fallback: parse `a=fingerprint:sha-256` from the applied
   **remote** description once the connection is `connected`; the browser's DTLS stack has then
   verified the peer certificate against that value, so the 32 bytes are the same and
   `finish()` succeeds. The fallback relies on the browser's verification instead of an
   independent read; it still defeats a signaling MITM because the host's signed `Hello` commits to
   its own certificate fingerprint and the check `peer.dtls_fingerprint == observed`
   (`CP/crypto.rs:157-162`) fails if signaling swapped the SDP fingerprint.
   Local fingerprint from the local SDP is identical in both worlds. The host uses
   webrtc-rs certificates with a `sha-256` fingerprint line; a non-sha-256 line cannot be used.
6. **WebSocket ping/pong.** Browsers cannot send or observe ping frames. Server-side liveness is
   satisfied by the browser's automatic Pong (3.1). The native 65 s client-side silence check has
   no browser equivalent; use `presence` polling or socket close events instead.
7. **Keyframes.** A browser decoder asks for keyframes with RTCP PLI, which the host application
   does not act on (section 6). The page must send `mixel-video-keyframe-v1` itself at start and on
   stalls (e.g. from `getStats` freeze/`framesDecoded` counters). Worst case without it: wait up to
   60 changed frames.
8. **Idle screens.** No RTP flows while the host screen is static; browser-side "no video"
   heuristics must use `mixel-video-idle-v1`, not RTP inactivity.
9. **SDP handling.** Custom `a=x-mixel-*` lines must be appended to the string sent over
   signaling after `setLocalDescription` (they cannot be passed to `setLocalDescription`). Whether
   every browser accepts the host answer with trailing `a=x-mixel-*` lines unchanged is untested;
   strip them before `setRemoteDescription` after reading them.
10. **Interleaved records.** Sound, print and idle records share the control channel with input
    acknowledgments. Native's acknowledgment wait accepts only idle/posted/unsupported
    (`DA/remote_control.rs:494-510`); a browser should dispatch every record by prefix and match
    acknowledgments to inputs in FIFO order. Do not announce `sound`/`print` unless handled.
11. **Bincode layout** of `Input` is derived (5.3); confirm with a native-produced vector or keep
    using the wasm exports (and add `ButtonClick`).
12. **Relay only.** Production config forces TURN (`relay_only: true`); lease TTL about 600 s, so
    Restart (3.7) with a full renegotiation about every 8-12 min is part of normal operation. Whether
    a session survives without Restart past the TURN credential expiry is not established by this
    code.
13. **Concurrency limits.** 60 frames/s on the WebSocket (3.1): trickle ICE bursts plus other
    messages must stay below it. One `ice` request per 5 s; one `renew` per 30 s.
