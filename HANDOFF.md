# Mixel-Remote — Agent Handoff / Current State

> **Purpose:** if Michael hits his Claude weekly limit, another agent (Codex,
> etc.) can pick up Mixel-Remote work from this file alone. Read this top to
> bottom before touching anything. Last updated: **2026-07-23**.
> Obey `AGENTS.md`, `GUARDRAILS.md`, `CODEX_CONVENTIONS.md` in this repo —
> especially the marketplace-release-safety rule (never cancel/replace a
> live or in-review store submission; updates go through the normal update
> path).

## What Mixel-Remote is

A rebranded fork of **RustDesk 1.4.6** used by Mixel IT to give SME customers
remote support. Not sold as software — used internally to deliver support.
Connects only to Mixel's own relay. Two repos are involved:

- **`Jangopeople/mixel-remote-client`** (this repo, private) — the fork +
  build pipeline that produces the branded, server-baked, signed clients.
- **`Jangopeople/mixel-ism`** — the web app (`apps/web`, Next.js) that hosts
  the `/remote-support` download page, the Microsoft Store MSIX workflow, and
  the KB. Deployed to Cloudflare Pages (`ism.mixel.ch` + `ism.mixel.mu`).

## Server (the relay) — do not confuse the two hosts

- **`rs.mixel.ch`** → the actual RustDesk relay. Resolves **directly** to the
  VPS `178.104.17.23` (NOT Cloudflare-proxied). Ports 21115-21119 open.
  Containers `hbbs` (rendezvous) + `hbbr` (relay) run via docker-compose in
  `/opt/rustdesk/` on the VPS. SSH: `ssh mixel-vps` (alias, **port 2222**).
- **`remote.mixel.ch`** → a DIFFERENT product (MeshCentral). Never target it
  for RustDesk work.
- **Canonical relay pub key** (verified live on VPS
  `/opt/rustdesk/data/id_ed25519.pub`): `OogSlDx9l+fgs0t6ihF3uTg9emyCv01m8cr4ullarRo=`
  This is what the client bakes and the page shows. An old note called
  `MtLWP8YyUX…` canonical — that key is DEAD. Always verify against the VPS file.

## Build pipeline (this repo)

- `.github/workflows/build.yml` clones upstream RustDesk (pinned 1.4.6,
  `branding/branding.env::UPSTREAM_VERSION`), runs `scripts/apply-branding.sh`,
  then upstream `build.py --flutter`, packages per platform, signs Windows via
  Azure Trusted Signing, signs+notarizes macOS, uploads all artifacts to the
  R2 bucket `mixel-remote-binaries` (custom domain `download.mixel.ch`).
- **Partial rebuilds:** `gh workflow run build.yml --ref main -f only=windows`
  (or `linux`, `macos`, or `windows,linux`). Saves CI minutes.
- **`scripts/apply-branding.sh`** is where ALL branding lives. Key patches:
  - Bakes `rs.mixel.ch` + pub key into `libs/hbb_common/src/config.rs`
    (`RENDEZVOUS_SERVERS`, `RS_PUB_KEY`) — the server is compile-time default.
  - Seeds `DEFAULT_SETTINGS` so the Network settings UI *visibly shows*
    `rs.mixel.ch` + key (blank fields read as "unconfigured" to users).
  - Deep rebrand (Windows+Linux): process `mixel-remote.exe`, extract dir
    `%LOCALAPPDATA%\mixel-remote`, core lib `libmixel-remote.{dll,so}`,
    `RuntimeBroker_mixel-remote.exe`, file-props, About slogan.
  - `patch_string` helper escapes sed metachars (`&` in replacement) — do NOT
    revert that; unescaped `&` silently corrupts patches.

## Current channel state (all live unless noted)

| Channel | State |
|---|---|
| **Microsoft Store** ("Mixel ISM", product `9n2z1b4c5l9d`) | **1.5.6.0 LIVE** — baked, visible fields, deep-rebranded. Certifies same-day. Update workflow: `mixel-ism/.github/workflows/desktop-release-store.yml` builds the MSIX from an unsigned baked exe (Microsoft re-signs Store pkgs); submit via Partner Center as an **update** (bump `apps/desktop/msix/AppxManifest.xml` version each time). |
| **Windows .exe** (download.mixel.ch/Mixel-Remote-Support.exe) | Baked + visible fields + deep-rebranded + **Authenticode-signed** (Azure Trusted Signing, CN=Mixel International Services SARLS). |
| **Windows .msi** | Signed, but **stale** (still rustdesk-named internally; WiX only builds when present). For admin/Intune deploy. Rebrand not applied. |
| **macOS** Apple Silicon + Intel .dmg | Baked + visible fields + **signed & notarized** (Developer ID: Michael Lascar, 5277F8NDH4, bundle `ch.mixel.remote`). Fresh clean build `?v=1.4.6-8-clean`/`-6-clean`. About shows Mixel-Remote + Mixel copyright; RustDesk slogan removed. **NOT deep-rebranded**: core file still `liblibrustdesk.dylib` internally (Xcode/notarization risk; invisible inside signed .app). |
| **Linux .deb** | Baked + visible fields + deep-rebranded (`libmixel-remote.so`, `mixel-remote` binary, zero rustdesk-named files). |

## Signing (Azure Trusted Signing)

- Account `mixel-codesigning` (West Europe, Basic), cert profile `mixel-remote`
  (Public Trust, CN=Mixel International Services SARLS, identity validated to
  **2028-08-10**). Certs auto-rotate every 3 days — **no manual renewal**.
- CI auth: service principal `mixel-remote-signing-ci` (signer role only);
  secrets `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` in this
  repo's GitHub secrets. Wired in `build.yml` via `azure/trusted-signing-action@v2`.

## OPEN ITEMS (what a takeover agent should watch / do)

1. **SmartScreen reputation review (Windows direct .exe)** — submitted
   2026-07-22 to Microsoft's Security Intelligence portal
   (`microsoft.com/wdsi/filesubmission`, Software-developer persona, product
   "Microsoft Defender Smartscreen", "incorrectly detected"). The deep-rebrand
   reset the exe's download reputation, so fresh Windows machines see "Windows
   protected your PC". Decision expected 1-3 business days → email to
   michael@mixel.ch. When approved, verify the block is gone. **Lesson:** every
   new exe hash resets SmartScreen reputation — batch changes, don't reship
   the direct .exe often. Store + Intune bypass SmartScreen entirely.

2. **ToiToi (Store-blocked customer)** — their Windows machines block the MS
   Store and may enforce SmartScreen Block mode. Guaranteed fix = **Intune
   deploy of the signed .msi** (admin-deployed apps bypass SmartScreen). A
   German admin note was drafted but not yet sent. `.msi` is at
   download.mixel.ch/Mixel-Remote-Support.msi (signed).

3. **macOS customer freeze/"can't enter password"** — root cause was (a) an
   OLD download (About said RustDesk/Purslane) + (b) macOS TCC permissions not
   granted. Fix = re-download current build + grant **Screen Recording +
   Accessibility** + quit/reopen. The session/password flow itself is fine
   (verified: Linux controller connected to controlled with password, got
   video). Page now has a macOS permission block; KB article added (see below).
   If it still fails on the fresh build with permissions granted, get a screen
   recording — that would be a real bug.

4. **KB article** `supabase/seeds/src/kb_mixel_remote_macos.json` (in mixel-ism)
   — macOS freeze/permissions, trilingual, validated. SQL generated. **NOT yet
   applied to prod DB** — needs Michael's approval. Apply per
   `.agent/rules/kb-authoring-standard.md` pipeline (psql to `ism` DB on
   mixel-vps).

5. **macOS deep-dylib rename** — deliberately skipped (risk to notarization).
   Only do if Michael insists; test notarization carefully.

## How to deploy the web page (mixel-ism)

CF Pages git auto-deploy is BROKEN. From `mixel-ism/apps/web` on `main`:
`pnpm build && npx wrangler pages deploy out --project-name ism --branch main`.
Serves both `ism.mixel.ch` and `ism.mixel.mu` (the .mu CNAME must point at
`ism-8ha.pages.dev`). Bump `?v=` cache-busters in
`apps/web/src/app/remote-support/page.tsx` when an R2 binary is replaced.

## Hard rules (see AGENTS.md for full text)

- Confirm before any marketplace action affecting availability/review state.
- Never apply KB seeds to prod DB without explicit approval.
- Swiss Standard German in all customer text (no ß).
- New client builds are UPDATES, never remove/replace an approved store app.
