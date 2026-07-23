> **Taking over / continuing Mixel-Remote work?** Read `HANDOFF.md` first —
> it has the full current state (all channels, server, signing, open items)
> as of 2026-07-23, written so another agent can continue without context.

## Marketplace Release Safety

This is a permanent hard rule for every current and future repository, all apps, all stores, and all platforms.

- Never remove, cancel, expire, withdraw, replace, or delete an approved, live, ready-for-sale, waiting-for-review, in-review, or previously approved marketplace submission unless Michael explicitly asks for that exact destructive action in writing.
- Never remove an approved app from sale.
- Never remove an approved or active review submission just to upload a newer build.
- App updates must be handled as updates: submit a new build/version through the store's normal update path while preserving the approved/live release.
- If a store workflow appears to require removing or cancelling an existing approved/live/reviewed release, stop and ask Michael first.
- Before any App Store Connect, Google Play Console, Microsoft Store, Microsoft Partner Center, Shopify app store, Chrome Web Store, Cloudflare marketplace, or other marketplace action that can affect public availability or review state, state the exact action and wait for explicit confirmation.


## App Review Demo Accounts

This is a permanent hard rule for every current and future repository, all apps, all stores, and all platforms.

- Never provide `mixel@mixel.ch` or Michael's personal email address as a demo, review, QA, support, or test account to Apple, Google, Microsoft, Shopify, Chrome Web Store, or any other marketplace reviewer.
- Always create and use a dedicated reviewer account for each product, e.g. `appreview-<product>@mixel.ch`, with only the access needed for review.
- Store reviewer passwords only in the approved password store/keychain and in the marketplace review credentials field. Do not paste them into chat, source files, tickets, PRs, docs, or screenshots.
- If a reviewer account stops working, rotate or recreate the dedicated reviewer account; never substitute Michael's personal email.

# AGENTS.md — Mixel Remote client (build pipeline)

> Briefing for agents working in `mixel-remote-client`. Read at session start.

---

## What this repo is

**Mixel Remote** — branded remote-support desktop client. This repo is a
**branding + CI build pipeline** on top of pinned [RustDesk](https://github.com/rustdesk/rustdesk)
upstream tags. It does **not** vendor app UI source permanently: CI clones
upstream, runs `scripts/apply-branding.sh`, builds installers, uploads to R2
(`download.mixel.ch`).

This is **not** a Vite/React/Supabase MIXEL web app. Ignore portfolio defaults
that assume PRD / UX Spec / RLS / tokens.css unless a task explicitly says
otherwise.

## Product identity (hard rule)

Shipped binaries must present as **Mixel Remote / Mixel-Remote** only.

- Display name: `Mixel Remote` (`APP_DISPLAY_NAME`)
- Technical / window name: `Mixel-Remote` (`APP_NAME`)
- Package / binary: `mixel-remote` (`APP_NAME_KEBAB`)
- Bundle ID: `ch.mixel.remote`
- Relay defaults: `rs.mixel.ch` + key in `branding/branding.env`

Do **not** leave user-visible `RustDesk` / Purslane / `com.carriez.*` strings in
installer metadata, desktop entries, About UI, PE resources, or 2FA issuer.
Internal link names (`librustdesk`, Cargo crate, `is_custom_client` check vs
literal `"RustDesk"`) stay on purpose so the build still links and custom-client
mode stays enabled.

## Layout

| Path | Role |
|---|---|
| `branding/` | Icons, `custom.txt`, `branding.env` |
| `scripts/apply-branding.sh` | Patches a fresh upstream checkout |
| `scripts/local/` | Local macOS notarize/publish + Windows icon swap |
| `.github/workflows/build.yml` | Matrix build → artifacts → R2 |
| `rustdesk/` | Local clone only — **gitignored**, never commit |

## How to work

1. Read `README.md` + `branding/branding.env`
2. For branding leaks: extend `apply-branding.sh`, then dry-run on a fresh
   `git clone --branch <UPSTREAM_VERSION> --recurse-submodules` of upstream
3. One PR per concern (`fix/…` or `feat/…` against `main`)
4. Conventional Commits: `fix(branding): …`, `ci(macos): …`
5. Do not push to `main`; open a PR for Antigravity / Michael

## Releasing

1. Bump `UPSTREAM_VERSION` in `branding/branding.env` (single source of truth)
2. Tag `vX.Y.Z` or run workflow_dispatch (optional version override)
3. CI builds Linux / macOS (arm64+x86_64 on `macos-14`) / Windows
4. Upload job writes versioned objects **and** customer aliases:
   - `Mixel-Remote-Support-Apple-Silicon.dmg`
   - `Mixel-Remote-Support-Intel.dmg`
   - `Mixel-Remote-Support-Windows.exe`
5. Bump `?v=` cache-buster on `mixel-ism` `/remote-support` when aliases change

## Secrets

See `README.md`. macOS needs Apple Developer ID + notary secrets for signed
builds. Windows Authenticode is optional via `WINDOWS_CERT_PFX_BASE64` +
`WINDOWS_CERT_PASSWORD`.

## What you don't do

- ❌ Commit the `rustdesk/` checkout
- ❌ Ship user-visible RustDesk branding
- ❌ Put `service_role` or unrelated MIXEL web-stack assumptions into this repo
- ❌ Push directly to `main`
- ❌ Suggest retired stacks (Convex, Vercel, Firebase, …) for this product

## Shared MIXEL Deploy Runbook

For Hetzner / Cloudflare deploy mechanics used elsewhere in the portfolio:

`/Users/michaellascar/Projects/VPS-Mixel/MIXEL_DEPLOY_RUNBOOK_2026-05-28.md`
