# Mixel Remote billing service (and invitation e-mails)

Stage 2 of "Mixel Remote as SaaS" (plan: `mixel-craft/docs/mixel-remote-plan.md`, Phase 8). Stripe
Checkout, the Stripe customer portal and the Stripe webhook, mapped to the tenant plan state in
`remote_saas` (SQL `next/sql/023_remote_saas_billing.sql`, prices `024_remote_saas_billing_prices.sql`).

## Where it runs

- Container `mixel-remote-billing` on mixel-vps (`/opt/mixel-remote-billing/compose.yml`, image
  `mixel-remote-billing:current`, built on the VPS from this folder), host port `127.0.0.1:8096`,
  network `supabase-ism_default` (reaches `ism-postgres` and `ism-postgrest`).
- Public: `https://remote.mixel.ch/api/billing/…` (location block in
  `/etc/nginx/sites-available/remote.mixel.ch.conf`, see `deploy/nginx-remote.mixel.ch.location.conf`).
- Why not an ISM edge function: the ISM edge runtime is one shared Deno router with the ISM
  service-role key and JWT secret. This service holds only the Stripe key, its webhook secret and a
  database login (`mixel_remote_billing_service`) that can execute nothing but the billing functions.
- Secrets: `/opt/mixel-remote-billing/.env` (root, 0600): `STRIPE_SECRET_KEY` (the Mixel Shop live
  account key, copied server to server), `STRIPE_WEBHOOK_SECRET`, `STRIPE_PORTAL_CONFIGURATION`,
  `DATABASE_URL`. Nothing secret is in git or on the Mac.
- Daily job: `mixel-remote-billing-daily.timer` (03:20 Europe/Zurich) runs
  `/usr/local/sbin/mixel-remote-billing-daily.sh` → `remote_saas.billing_daily(false)`.
  `mixel-remote-billing-daily.sh dry-run` shows what it would do. Log: `journalctl -u mixel-remote-billing-daily`.

## Invitation e-mails (SQL 026)

The same service sends the invitation e-mails of Mixel Remote companies (`src/invite-mail.js`,
templates `src/invite-templates.js` in English, German, French and Italian, `src/resend.js`).

- `remote_saas_invite_member` / `remote_saas_resend_invite` store a pending invitation **without a secret** and
  `NOTIFY remote_saas_invite_mail`. The service LISTENs on a dedicated connection and also sweeps every 30 s.
- Per invitation: `remote_saas.mail_claim_invites` (claim, `for update skip locked`) → a fresh secret
  `mri_<32 random bytes base64url>` → `remote_saas.mail_invite_secret` (stores only the SHA-256) → Resend
  `POST /emails` (idempotency key = claim id, reply-to the inviter, tags `app=mixel-remote`, `kind=invite`) →
  `remote_saas.mail_invite_result`. The secret is only in memory and in the e-mail; it is never logged or stored.
- Failures: 429/5xx/network are retried after 1, 5, 15, 60 minutes (5 attempts); a refused address gives up at
  once. Reserved test domains (`*.test`, `*.example`, `*.invalid`, `example.com/net/org`, `localhost`) are never
  mailed. A claim older than 5 minutes (crashed service) is taken over.
- Role: the login `mixel_remote_billing_service` is also a member of `mixel_remote_mail`, which may execute only
  the three `remote_saas.mail_*` functions.
- Settings in `/opt/mixel-remote-billing/.env`: `RESEND_API_KEY` (the Mixel Store's Resend key, copied server to
  server from `/opt/supabase-store/.env`; sender domain `mixel.ch` is verified in Resend), optional `MAIL_FROM`
  (default `Mixel Remote <remote@mixel.ch>`) and `INVITE_BASE_URL` (default `https://remote.mixel.ch/invite#`).
  Without a key the service runs and invitations stay queued.
- `GET /api/billing/health` shows `invite_mail: {on, sent, failed, last_run, healthy}`; logs: `docker logs
  mixel-remote-billing | grep "invite mail"` (invitation id, outcome, Resend message id, never the link).

## API (for the portal and the in-app management)

All POST bodies are JSON. `checkout`, `portal` and `change` need `Authorization: Bearer <GoTrue
access token>` of an **owner or admin** of the tenant. The service forwards that token to PostgREST
and calls `public.remote_saas_billing(tenant)`; PostgREST verifies the token and the RPC checks the
membership, role, sign-in method and 2-step rules. CORS: `https://remote.mixel.ch`, `tauri://localhost`,
`http://tauri.localhost`.

| Endpoint | Body | Answer |
|---|---|---|
| `POST /api/billing/checkout` | `tenant_id`, `plan` (`solo`/`team`/`business`), `interval` (`year` default, `month`), `extra_technicians` (0–1000), `return_path` (path on remote.mixel.ch, default `/billing`) | `{id, url, expires_at, trial_end}` – open `url` |
| `POST /api/billing/portal` | `tenant_id`, `return_path` | `{url}` |
| `POST /api/billing/change` | like checkout (no `return_path`) | `{subscription, status}`; the tenant updates when the webhook arrives |
| `POST /api/billing/webhook` | Stripe only | |
| `GET /api/billing/health` | | `{ok}` |

Errors: `{error, message}` with 400 (input), 401 `sign_in`, 403 `forbidden`, 409 `already_subscribed`
/ `plan_too_small` (message says why, e.g. too many technicians for Solo) / `no_customer` /
`no_subscription`, 402 `payment_failed` (change), 429, 502 `stripe_unavailable`.
After Checkout Stripe returns to `return_path?billing=success&session_id=…` or `?billing=cancelled`.

SQL for the UI (PostgREST, `rpc/…`):
- `remote_saas_billing(tenant)` – owners/admins: plan, status, trial end, interval, extra technicians,
  subscription status, period end, `cancel_at`, `cancel_at_period_end`, `has_subscription`,
  `devices_delete_after` (read-only countdown), usage.
- `mixel_remote_prices()` – anon and signed in: the price list (`amount_chf`, `per_month_chf`).
- Tenant limits keep coming from `remote_saas_tenant_overview` (plan + `extra_technicians`).

## Decisions

- **Prices and VAT.** CHF, Swiss VAT 8.1 % included, the same as Mixel Shop: the Shop puts the gross
  amount on Stripe and books VAT in Mixel ACC; the account has no Stripe Tax registration (head
  office missing) and no tax rates. Prices are `tax_behavior=inclusive`, so the amount stays gross
  if Stripe Tax is ever switched on. Invoices carry the footer "Preise inkl. 8.1 % MWST … CHE-184.355.981
  MWST". Yearly = list price × 12 (Solo 144, Team 468, Business 1068, extra technician 96 per year);
  monthly = +20 % (14.40, 46.80, 106.80, 9.60 per month). Enterprise: no Stripe price, "contact us".
- **Buying during the trial.** The rest of the trial stays free: the subscription's `trial_end` is
  the tenant's `trial_ends_at` and Checkout says "13 days free, then CHF 660.00 per year starting …".
  The chosen plan applies at once (status `active`). With less than 49 h left (Stripe needs 48 h)
  or after the trial, the first charge is immediate.
- **Status mapping.** Stripe `trialing`/`active` → `active`; `past_due` → `past_due` (still usable);
  `unpaid`/`canceled`/`paused` → `read_only`; `incomplete*` → unchanged. `invoice.paid` → `active`,
  `invoice.payment_failed` → `past_due`. Read-only starts a 30-day countdown
  (`billing.devices_delete_after`); paying again clears it; the daily job then removes the devices.
- **Event safety.** Signature checked with this endpoint's own secret (5 min tolerance); each event
  id applied once (`remote_saas.billing_events`); an event older than the last applied one is
  `stale`; objects without `metadata.app = mixel-remote` (Shop, PBX, Marketing share the account)
  are acknowledged and ignored; a customer/subscription of another tenant is never re-linked.
- **Plan changes** go through `/change` (prorated, charged at once; a failed payment rejects the
  change) because the Stripe portal cannot change subscriptions with several items. The portal
  does payment methods, invoices, billing details and "cancel at period end".
- **Customer portal default.** The account had no portal configuration, so the first one Stripe
  created became the account default (PBX opens portal sessions without a configuration). That
  default (`bpc_1UbE0hFRPXnJX22h1VUjEGB9`) is now neutral (MIXEL headline, payment methods,
  invoices, billing details; no cancel/update); Mixel Remote uses its own `bpc_1UbE1UFRPXnJX22hySg5T0Tr`.

## Stripe objects (live, account acct_1MHij8FRPXnJX22h, metadata `app=mixel-remote`)

Products `mixel_remote_solo`, `mixel_remote_team`, `mixel_remote_business`, `mixel_remote_technician`;
prices (lookup keys `mixel_remote_<plan|technician>_chf_<year|month>`) in `024_…sql`; portal
configuration `bpc_1UbE1UFRPXnJX22hySg5T0Tr`; webhook endpoint `we_1UbE0hFRPXnJX22hlR5RA24a`
(`https://remote.mixel.ch/api/billing/webhook`, API version 2024-06-20, the six events in
`src/events.js`). `scripts/catalogue.js` is idempotent and prints the ids (`--dry-run` first).

## Develop and test

```
npm ci && npm run check && npm test
psql … -f ../../sql/tests/remote-saas-billing.sql       # rolled back, synthetic accounts
psql … -f ../../sql/tests/remote-saas-invite-mail.sql   # rolled back, synthetic accounts
```

## Deploy (on mixel-vps, from this folder)

```
tar -czf - --exclude node_modules . | ssh mixel-vps 'sudo sh -c "rm -rf /opt/mixel-remote-billing/src-build && mkdir -p /opt/mixel-remote-billing/src-build && tar --no-same-owner -xzf - -C /opt/mixel-remote-billing/src-build"'
ssh mixel-vps 'sudo sh -c "cd /opt/mixel-remote-billing/src-build && docker build -t mixel-remote-billing:<commit> . && docker tag mixel-remote-billing:<commit> mixel-remote-billing:current && cd .. && docker compose up -d"'
curl https://remote.mixel.ch/api/billing/health
```

## Rollback

- Service: `docker tag mixel-remote-billing:<previous> mixel-remote-billing:current && docker compose up -d`
  in `/opt/mixel-remote-billing`; or stop it (`docker compose down`) – Stripe retries webhooks for 3 days.
- Route: restore `/opt/mixel-remote-billing/remote.mixel.ch.conf.before-billing-*` to
  `/etc/nginx/sites-available/remote.mixel.ch.conf`, `nginx -t && systemctl reload nginx`.
- Daily job: `systemctl disable --now mixel-remote-billing-daily.timer`.
- Stripe: disable the webhook endpoint and archive the prices/products (do not delete; no other app
  uses them). SQL: the tables are additive; `drop table remote_saas.billing_events, remote_saas.billing,
  remote_saas.billing_prices cascade` plus the `billing*` functions removes stage 2 entirely.

## A real end-to-end payment test (not done yet – needs Michael's OK at the time)

There is no Stripe test-mode key for this account, so a real purchase is a real charge. A run was
prepared on 2026-11-13 (session created, then expired unpaid and cleaned up when Michael postponed).

Preconditions
- Michael's written OK for the exact run: one CHF 14.40 charge (Solo monthly, the smallest real
  price; a CHF 12 charge would need an extra temporary price) on his own card, refunded right away.
  Optional step 4 adds a second, prorated charge (Team monthly minus the unused Solo part).
- Michael enters the card himself in his browser. Agents never enter card data and never refund by
  script: the refund is done by a person in the Stripe dashboard (Payments → the charge → Refund).
  Stripe keeps its processing fee on a refunded charge.
- Never use mixel@mixel.ch or Michael's account as the QA owner.

Steps
1. QA owner and tenant: create a GoTrue user `qa-billing-pay-<random>@example.test` (admin API on
   127.0.0.1:6120 with the ISM service-role key, `email_confirm: true`), sign in (password grant),
   call `rpc/remote_saas_signup`. Move the trial into the past so the checkout charges now:
   `update remote_saas.tenants set trial_ends_at = now() - interval '1 minute' where id = '<tenant>'`.
2. `POST https://remote.mixel.ch/api/billing/checkout {"tenant_id": …, "plan": "solo", "interval": "month"}`
   with the QA owner's token. Check the session (`GET /v1/checkout/sessions/<id>?expand[]=line_items`):
   mode subscription, CHF, `amount_total` 1440, one line "Mixel Remote Solo". Give Michael the `url`
   (valid 1 hour).
3. After he paid, wait (up to 30 min) until these three are `applied` for the tenant and the tenant is
   `solo/active`:
   `select event_type, outcome from remote_saas.billing_events where tenant_id = '<tenant>'` –
   `checkout.session.completed`, `customer.subscription.created`, `invoice.paid`; also
   `remote_saas.billing` shows the subscription, `active`, `month` and the period end. The success
   page is `https://remote.mixel.ch/billing?billing=success&session_id=…`.
4. Optional upgrade: `POST /api/billing/change {"plan": "team", "interval": "month"}` → prorated
   invoice charged at once; `customer.subscription.updated` + `invoice.paid` → tenant `team/active`.
5. Portal: `POST /api/billing/portal`, open the URL, check the invoice(s) and the payment method.
6. Refund every charge in the Stripe dashboard (person, not script); confirm in Stripe the charges
   show "Refunded".
7. Cancel immediately without proration or final invoice:
   `DELETE /v1/subscriptions/<sub>?prorate=false&invoice_now=false` (after checking its metadata
   `app=mixel-remote` and `tenant_id`). Confirm `customer.subscription.deleted` is applied, the tenant
   is `read_only` and `remote_saas.billing.devices_delete_after` is 30 days ahead.
8. Clean up: `DELETE /v1/customers/<cus>`; delete the tenant's `remote_saas.billing_events`, the tenant,
   its `public.users` row, client and organization; delete the GoTrue user. Confirm 0 rows left and
   `GET /v1/customers/<cus>` says `deleted: true`.
