# Mixel Remote portal (remote.mixel.ch)

The public website and the customer portal of Mixel Remote as SaaS (plan: `mixel-craft/docs/mixel-remote-plan.md`,
Phase 8, stage 3). React + TypeScript + Vite, served by a Cloudflare Worker with static assets.

## Pages
- `/` landing, `/pricing` plan table (CHF, yearly / monthly), `/download` (Mixel Store link).
- `/signup` company + account (email and password, email code, or Mixel ID) → `remote_saas_signup` → 14-day Business
  trial. `/signup/finish` names the company for an account that is signed in without one (Mixel ID sign-up).
- `/signin` email first; shows the methods the company allows (`remote_saas_sign_in_options` by verified domain, or
  `?company=<slug>`), then the authenticator code when the account has 2-step login.
- `/invite#<secret>` accepts an invitation (the link in the invitation e-mail); sign in or create an account first.
- `/auth/callback` OAuth (PKCE) return for Mixel ID.
- `/app` overview (plan, trial days, usage), `/app/devices` (online status with "online since" / "last seen" and the
  reason, refreshed every 30 s; rename, move, remove, add help, link to the web viewer),
  `/app/groups` (groups, technician access), `/app/deploy` (deploy links + the one-line command per system:
  Windows PowerShell, `curl … | sudo sh` for macOS and Linux), `/app/team` (members, roles, invitations with their
  e-mail state, send again, revoke), `/app/settings` (sign-in methods, required 2-step login, email domains, audit log),
  `/app/billing` (plan, limits, Stripe Checkout / customer portal / plan change via the billing service, success and
  cancel pages at `?billing=success|cancelled`), `/app/account` (2-step login).

Every action is an RPC from `next/sql/019`–`020` (billing state and prices: `023`/`024`, actions:
`next/services/billing`, `POST /api/billing/checkout|portal|change`); the database checks membership, role, plan and sign-in policy.
Online status comes from the connection broker: every 30 s it reports the connected enrolled devices to
`remote_presence_report` (SQL 025, see `next/deployment/QA-DEPLOYMENT.md` "Presence reporting"); the portal reads
`remote_saas_device_presence`, which applies the same access rules as the device list. "Status unknown" means the broker
has not reported for 3 minutes. Settings → Company: owners and admins rename the company (`remote_saas_rename_tenant`,
audited; the company ID/slug stays).

## Legal and data protection (SQL 027) — texts are DRAFTS pending Michael's review
- `/legal` (index), `/legal/terms`, `/legal/privacy`, `/legal/dpa`, `/legal/subprocessors` in EN/DE/FR/IT
  (`src/legal/*.ts`). Every page shows the draft banner, sets `<meta name="robots" content="noindex">`, and the
  worker sends `X-Robots-Tag: noindex, nofollow` for `/legal*` and `/deleted` (`run_worker_first` in `wrangler.toml`).
- Sign-up (`/signup`, `/signup/finish`) needs the checkbox "I accept the Terms … and the DPA"; the database stores
  version (`LEGAL_VERSION` = the current row in `remote_saas.legal_documents`) and time per tenant and person.
- Settings › Terms and data processing (accepted version; owners/admins accept a newer one), Settings › Data and
  deletion (owner: JSON export, CSV per table, delete the company), Account › Delete my account (also on the
  "no company" screen). Footer links on public pages and in the portal.
- Publishing the final texts: replace the texts, set `LEGAL_VERSION`/`LEGAL_DATE`, insert the new versions into
  `remote_saas.legal_documents` (current = true, old = false), remove the draft banner and the noindex rules.
  Existing companies then see "accept the updated terms" (owners/admins).

## Develop and check
```bash
npm ci
# Public anon key of the ISM API (not a secret, but kept out of git like the ISM web app does):
ssh mixel-vps 'sudo grep "^ANON_KEY=" /opt/supabase-ism/.env | cut -d= -f2-' | sed 's/^/VITE_ISM_ANON_KEY=/' > .env.local
npm run dev        # http://localhost:5174 (allowed by the ism-api CORS list)
npm run typecheck && npm run lint && npm test && npm run build
```

## Live end-to-end run (real backend, throwaway QA tenant)
```bash
# Service-role key for the GoTrue admin API, in a file outside the repo (chmod 600), deleted afterwards:
ssh mixel-vps 'sudo grep "^SERVICE_ROLE_KEY=" /opt/supabase-ism/.env | cut -d= -f2-' | sed 's/^/ISM_SERVICE_ROLE_KEY=/' > /path/qa-admin.env
QA_SQL='ssh mixel-vps docker exec -i ism-postgres psql -U postgres -d ism -At' \
QA_RUN=r$(date +%H%M%S) QA_ADMIN_ENV=/path/qa-admin.env QA_SHOTS=/path/shots PORTAL_URL=https://remote.mixel.ch npx playwright test
# The web viewer with company accounts (after the portal run; uses its qa-run.json):
VIEWER_URL=https://ism.mixel.ch/remote-next/ QA_SHOTS=/path/shots QA_ADMIN_ENV=/path/qa-admin.env npx playwright test tests/e2e/viewer.spec.ts
# Clean-up of that run (database, then the accounts via the admin API):
ssh mixel-vps 'docker exec -i ism-postgres psql -U postgres -d ism -v ON_ERROR_STOP=1 -v run=<QA_RUN>' < tests/e2e/cleanup.sql
```
Legal features (sign-up with terms, member account deletion, export, company deletion incl. the Stripe customer):
`QA_RUN=l$(date +%H%M%S) QA_ADMIN_ENV=… QA_SQL="ssh mixel-vps docker exec -i ism-postgres psql -U postgres -d ism -At" QA_STRIPE="ssh mixel-vps docker exec -i mixel-remote-billing node -" PORTAL_URL=https://remote.mixel.ch npx playwright test tests/e2e/legal.spec.ts`
(it deletes its own QA company; afterwards remove its `remote_saas.closures` row and the QA keys in `remote_private.connection_reservations`).
The billing test stops at the Stripe Checkout and customer-portal pages; nothing is paid. The owner signs up through the UI with Resend's test inbox (`delivered+…@resend.dev`, nothing is delivered); the
code is read with the admin `generate_link` call. Other QA users are created with the admin API. Never use real
accounts for QA.

## Deploy
```bash
npm run build && npx wrangler deploy
```
Worker `mixel-remote-portal`, route `remote.mixel.ch/*` on the `mixel.ch` zone (the DNS record stays proxied; the
route takes the traffic before the origin). `/api/billing/*` is passed through to the origin (nginx on mixel-vps →
billing service on 127.0.0.1:8096); keep that location in `/etc/nginx/sites-available/remote.mixel.ch.conf`. `/api/windows-installer` reads the signed stable Windows update
channel so the deploy dialog can show a ready-to-run command. Security headers: `public/_headers`.

Backend settings this site needs (done 2026-11-13 on mixel-vps):
- `ism-api.mixel.ch` CORS list includes `https://remote.mixel.ch` (`/etc/nginx/sites-available/ism-api.mixel.lu.conf`).
- GoTrue `GOTRUE_URI_ALLOW_LIST` includes `https://remote.mixel.ch/**` (`/opt/supabase-ism/docker-compose.yml` and the
  override `/opt/mixel-remote-next-qa/gotrue.remote-qa.json`, which is the effective value).

The previous remote.mixel.ch site (an archived Convex/RustDesk page, nginx → 127.0.0.1:8081, source in
`_archive/mixel-remote`) had only crawler traffic; its nginx vhost is left in place but receives no traffic while the
Worker route exists. Rollback: `npx wrangler delete` (or remove the route) and the old origin answers again.

## Invitation e-mails (SQL 026)
Inviting (portal, desktop app or the RPC) stores the invitation without a secret and signals the mail service
(`next/services/billing`, `src/invite-mail.js`). The service creates the secret, stores only its SHA-256, and sends the
e-mail through Resend (`Mixel Remote <remote@mixel.ch>`, reply-to the inviter) in the language the inviter's UI uses
(else the inviter's profile language; English, German, French or Italian). The secret exists only in the e-mail link
`https://remote.mixel.ch/invite#<secret>`. "Send again" creates a new link (the old one stops working), "Revoke" also
stops an e-mail that was not sent yet. The list shows "Being sent", "Sent <date>" or "Not delivered". QA addresses on
reserved test domains (`example.com`, `*.test`, …) are never mailed (they show "Not delivered"); the e2e run sets a
link for them with `QA_SQL` (see `tests/e2e/qa.ts` `invitationLink`).

## Mass rollout on Mac and Linux
`public/install/macos.sh` and `public/install/linux.sh` are served at `https://remote.mixel.ch/install/…`. The deploy
dialog shows `curl -fsSL https://remote.mixel.ch/install/<os>.sh | sudo sh -s -- '<token>'`:
- Linux (Ubuntu/Debian x64): checks the update channel's Ed25519 signature (Mixel update key) and the package
  SHA-256, `apt-get install`s the `.deb`, runs `desktop_app --signin-enroll <token>` as root → computer-wide key,
  enrolment, root service `mixel-remote-signin.service` with always-on access (sign-in screen and X11 sessions).
- macOS: downloads the release DMG (SHA-256 from the channel), requires Mixel's Developer ID signature (team
  5277F8NDH4), installs `/Applications/Mixel Remote.app` and runs `--signin-enroll <token>` as the signed-in person
  (`launchctl asuser`): key in the login Keychain, enrolment, per-user launch agent `<bundle id>.startup-host` running
  the headless host. Once per Mac the person allows Screen Recording and Accessibility (macOS rule).
Both refuse releases older than 0.1.22 (the first with `--signin-enroll` on Mac and Linux). Options for tests and
offline mirrors: `--deb FILE` (Linux), `--app PATH [--in-place]` (Mac), `--channel NAME`.

## Not yet
Google / Microsoft / Apple / company SSO providers
(shown as "coming soon" until they are configured in GoTrue), automatic domain verification.

The portal's "Open the web viewer" goes to `https://ism.mixel.ch/login?next=/remote-next/`: the viewer runs on the ISM
origin (the connection service admits that one web origin), so company accounts sign in there with the same account.
