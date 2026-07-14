# mixel-remote-client

**Mixel Remote** is a branded remote-support client. This repo holds branding
overrides and the CI pipeline; it does **not** vendor upstream source. Each
build clones a pinned [RustDesk](https://github.com/rustdesk/rustdesk) tag,
applies Mixel identity, and publishes installers to
`https://download.mixel.ch/`.

## Layout

```
branding/
  custom.txt          Upstream build-time branding file (Mixel values)
  branding.env        App name, bundle id, relay host/key, UPSTREAM_VERSION
  icon-*.png          App icons

scripts/
  apply-branding.sh   Patches a fresh upstream checkout → Mixel Remote only
  local/              macOS notarize/publish + Windows PE icon swap

.github/workflows/
  build.yml           config → bridge → linux/mac/windows → R2
```

## Releasing

1. Set `UPSTREAM_VERSION` in `branding/branding.env` (canonical; CI reads it).
2. Tag: `git tag v1.4.6 && git push origin v1.4.6`  
   Or Actions → **build** → workflow_dispatch (optional version override).
3. Artifacts land on R2 as:
   - `mixel-remote-<version>-<arch>.{deb,dmg,exe,msi}`
   - Customer aliases:
     - `Mixel-Remote-Support-Apple-Silicon.dmg`
     - `Mixel-Remote-Support-Intel.dmg`
     - `Mixel-Remote-Support-Windows.exe`
4. Bump `?v=` on `mixel-ism` `/remote-support` so edge cache refreshes.

## Required GitHub Secrets

| Secret | What for |
|---|---|
| `APPLE_CERT_P12_BASE64` | macOS Developer ID Application (.p12, base64) |
| `APPLE_CERT_P12_PASSWORD` | .p12 password |
| `APPLE_NOTARY_USER` | Apple ID email |
| `APPLE_NOTARY_PASSWORD` | App-specific password |
| `APPLE_NOTARY_TEAM_ID` | 10-char Team ID |
| `CLOUDFLARE_API_TOKEN` | R2 edit |
| `CLOUDFLARE_ACCOUNT_ID` | CF account id |
| `WINDOWS_CERT_PFX_BASE64` | Optional Authenticode .pfx (base64) |
| `WINDOWS_CERT_PASSWORD` | Optional .pfx password |

Without Apple secrets, macOS DMGs are unsigned. Without Windows cert secrets,
the `.exe` is unsigned (SmartScreen warnings). Without Cloudflare secrets,
artifacts stay on the Actions run only.

## Branding rule

End users must never see **RustDesk**. `apply-branding.sh` rewrites UI strings,
PE/macOS/Linux package identity, relay defaults, and fails the build if key
surfaces still contain `RustDesk`. Upstream remains a **build-time** dependency
only (`librustdesk` / crate name kept for linking).

## Local helpers

- macOS sign + notarize + alias upload (when CI unsigned or for hotfix):  
  `scripts/local/sign-and-publish-macos.sh path/to.dmg`
- Windows PE icon swap:  
  `scripts/local/swap-windows-exe-icon.py in.exe icon.ico out.exe`

## Why not RustDesk Server Pro?

Decision recorded in the parent ITSM project history: no recurring license fee,
full control of the client binary and relay defaults (`rs.mixel.ch`).
