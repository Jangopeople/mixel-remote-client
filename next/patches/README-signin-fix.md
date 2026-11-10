# Managed-app sign-in fix (shipped as 0.1.2 on 2026-11-10)

**Apply `patch-remote-signin.py` to `next/` before every managed-app build, or the sign-in bug
returns.** The managed app source (`next/`) is currently uncommitted (it lives only in the
`remote-reliability` worktree), so this fix cannot be committed into the app source yet — it is kept
here as an idempotent, exact-match patch and must be re-applied on top of the deployed
`crates/desktop_app/src/account_auth.rs` (SHA-256 of the shipped file: 4cf07e069efc5be7…).

    python3 next/patches/patch-remote-signin.py next      # idempotent; refuses on any mismatch

## The bug
A Mixel ID that is a staff / company admin (role admin/agent, no organisation) with a verified
authenticator but no personal Remote account could not sign in. After the browser login the gotrue
session is `aal1`; staff need `aal2`. The old `AccountAuth::inspect` called `personal_account()`
unconditionally in the `authenticate` error branch — but the server **blocks** staff account access
at aal1 ("Staff MFA required"), so that call errored and the whole sign-in failed (the user saw the
account screen stuck / a name-setup form, never the code step).

## The fix (one site, `crates/desktop_app/src/account_auth.rs::inspect`)
When the Remote check fails **and the account has a verified TOTP factor**, go to `mfa_required`
(the verification-code screen) instead of personal setup. Because Rust `&&` short-circuits, the
staff-blocked `personal_account()` call is skipped for these accounts. After the code the session
reaches aal2 and the staff account is authenticated. Personal setup is kept only for accounts with no
verified authenticator.

## Verified (2026-11-10)
- Built, signed and shipped as managed app 0.1.2 on Windows, macOS (Developer ID + notarized) and
  Linux; all current on store.mixel.ch. Per-platform: 18/27 cargo tests pass, clippy clean.
- Live end-to-end test against the production identity services (ism-api.mixel.ch) with a dedicated
  staff test account, a seeded verified authenticator and a real magic-link session (no password):
  12/12 assertions — /user returns the authenticator, staff access is blocked at aal1, the fixed
  decision is the code step, the real TOTP challenge+verify reaches aal2, and the staff account is
  then authenticated. Test account removed afterwards.

## macOS build note (critical — a 0.1.2 Mac build shipped broken without this)
`build.rs` embeds the service config only for `windows`/`linux`. **macOS loads it from a bundled
resource** (`read_packaged` reads `Contents/Resources/service-config.json`). So the macOS build MUST
pass the config as a tauri bundle resource, or the app shows
"This development build has no bundled Mixel service configuration" and sign-in can't start:

    // in the macOS tauri bundle config
    "bundle": { "resources": { "service-config.json": "service-config.json" } }

with `service-config.json` (the public config) placed next to `tauri.conf.json`. Windows/Linux instead
set `MIXEL_DESKTOP_SERVICE_CONFIG` at build time (build.rs embeds it). Shipped fix: macOS 0.1.3
(0.1.2 was missing the resource); Windows/Linux 0.1.2 are fine.
