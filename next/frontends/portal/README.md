# Mixel Remote portal (remote.mixel.ch)

The public website and the customer portal of Mixel Remote as SaaS (plan: `mixel-craft/docs/mixel-remote-plan.md`,
Phase 8, stage 3). React + TypeScript + Vite, served by a Cloudflare Worker with static assets.

## Pages
- `/` landing, `/pricing` plan table (CHF, yearly / monthly), `/download` (Mixel Store link).
- `/signup` company + account (email and password, email code, or Mixel ID) → `remote_saas_signup` → 14-day Business
  trial. `/signup/finish` names the company for an account that is signed in without one (Mixel ID sign-up).
- `/signin` email first; shows the methods the company allows (`remote_saas_sign_in_options` by verified domain, or
  `?company=<slug>`), then the authenticator code when the account has 2-step login.
- `/invite#<secret>` accepts an invitation (same link format as the desktop app); sign in or create an account first.
- `/auth/callback` OAuth (PKCE) return for Mixel ID.
- `/app` overview (plan, trial days, usage), `/app/devices` (online status with "online since" / "last seen" and the
  reason, refreshed every 30 s; rename, move, remove, add help, link to the web viewer),
  `/app/groups` (groups, technician access), `/app/deploy` (deploy links + Windows command), `/app/team`
  (members, roles, invitation links), `/app/settings` (sign-in methods, required 2-step login, email domains, audit log),
  `/app/billing` (plan, limits, Stripe Checkout / customer portal / plan change via the billing service, success and
  cancel pages at `?billing=success|cancelled`), `/app/account` (2-step login).

Every action is an RPC from `next/sql/019`–`020` (billing state and prices: `023`/`024`, actions:
`next/services/billing`, `POST /api/billing/checkout|portal|change`); the database checks membership, role, plan and sign-in policy.
Online status comes from the connection broker: every 30 s it reports the connected enrolled devices to
`remote_presence_report` (SQL 025, see `next/deployment/QA-DEPLOYMENT.md` "Presence reporting"); the portal reads
`remote_saas_device_presence`, which applies the same access rules as the device list. "Status unknown" means the broker
has not reported for 3 minutes. Settings → Company: owners and admins rename the company (`remote_saas_rename_tenant`,
audited; the company ID/slug stays).

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
QA_RUN=r$(date +%H%M%S) QA_ADMIN_ENV=/path/qa-admin.env QA_SHOTS=/path/shots PORTAL_URL=https://remote.mixel.ch npx playwright test
# The web viewer with company accounts (after the portal run; uses its qa-run.json):
VIEWER_URL=https://ism.mixel.ch/remote-next/ QA_SHOTS=/path/shots QA_ADMIN_ENV=/path/qa-admin.env npx playwright test tests/e2e/viewer.spec.ts
# Clean-up of that run (database, then the accounts via the admin API):
ssh mixel-vps 'docker exec -i ism-postgres psql -U postgres -d ism -v ON_ERROR_STOP=1 -v run=<QA_RUN>' < tests/e2e/cleanup.sql
```
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

## Not yet
Invitation emails, Google / Microsoft / Apple / company SSO providers
(shown as "coming soon" until they are configured in GoTrue), automatic domain verification.

The portal's "Open the web viewer" goes to `https://ism.mixel.ch/login?next=/remote-next/`: the viewer runs on the ISM
origin (the connection service admits that one web origin), so company accounts sign in there with the same account.
