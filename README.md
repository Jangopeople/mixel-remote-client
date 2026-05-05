# mixel-remote-client

Mixel-Remote is a rebranded build of [RustDesk](https://github.com/rustdesk/rustdesk).
This repo contains only the branding overrides and the build pipeline; it
does **not** vendor the RustDesk source. Each build job clones a pinned
upstream tag, applies the branding, and produces signed installers.

## What's in here

```
branding/
  custom.txt          RustDesk's own build-time branding override file
  branding.env        Build-script env vars (app name, bundle id, etc.)
  icon-{16,32,48,64,128,256,512,1024}.png   App icon, all required sizes

scripts/
  apply-branding.sh   Applies branding to a fresh RustDesk source checkout

.github/workflows/
  build.yml           Matrix build (Linux/macOS/Windows) → R2 upload
```

## Releasing a new build

1. Bump `UPSTREAM_VERSION` in `branding/branding.env` to the desired
   RustDesk tag (see https://github.com/rustdesk/rustdesk/releases).
2. Tag this repo: `git tag v1.4.6 && git push origin v1.4.6`.
3. The `build` workflow runs the matrix. Successful artifacts are
   uploaded to R2 and become available at
   `https://download.mixel.ch/mixel-remote-<version>-<arch>.<ext>`.
4. Update the frontend's `DOWNLOAD_LINKS` in `mixel-ism/apps/web/src/app/remote-support/page.tsx`
   if the URL pattern changes.

## Required GitHub Secrets

Set these once in this repo's Settings → Secrets and variables → Actions:

| Secret | What for | How to get |
|---|---|---|
| `APPLE_CERT_P12_BASE64` | macOS code signing | Export your "Developer ID Application" cert from Keychain Access as a `.p12`, then `base64 -i cert.p12 \| pbcopy` and paste |
| `APPLE_CERT_P12_PASSWORD` | Decrypts the .p12 | The password you set when exporting |
| `APPLE_NOTARY_USER` | Notarization | Your Apple ID email |
| `APPLE_NOTARY_PASSWORD` | Notarization | App-specific password from https://appleid.apple.com → Sign-in and Security |
| `APPLE_NOTARY_TEAM_ID` | Notarization | The 10-char Team ID from https://developer.apple.com/account |
| `CLOUDFLARE_API_TOKEN` | R2 upload | https://dash.cloudflare.com/profile/api-tokens — create token with R2:Edit + Account:Read scope |
| `CLOUDFLARE_ACCOUNT_ID` | R2 upload | https://dash.cloudflare.com → right sidebar |

Without `APPLE_CERT_*`, macOS builds still run but are unsigned (users
get the "unidentified developer" warning). Without `CLOUDFLARE_*`, the
upload step fails but artifacts are still attached to the workflow run.

## Status

🟡 **v0.1 — pipeline scaffolded, build steps are stubs.**

The matrix runs end-to-end (clone → apply branding → "build" → upload),
but the platform-specific build commands are placeholders. Replacing
them with real cargo/flutter invocations is the next milestone — see
`build.yml` lines marked `# TODO`.

## Why a fork instead of RustDesk Server Pro?

Decision recorded in the parent ITSM project history. Cost: time spent
maintaining the build pipeline. Benefit: zero recurring license fee and
full control over the client.
