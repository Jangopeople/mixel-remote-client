# Phase 1 patches for the managed app (2026-11-10)

`next/` is still untracked on this branch line (the source lives in the `remote-reliability` worktree), so
fixes are kept as idempotent exact-match patch scripts. Apply both before every build:

    python3 next/patches/patch-remote-signin.py <next-root>
    python3 next/patches/patch-remote-phase1-ui.py <next-root>     # --check to verify only

`patch-remote-phase1-ui.py`: next step after sign-in for team accounts, macOS-only panel and wording hidden on
Windows/Linux, consent hint, clearer text when a connection is refused. Shipped as 0.1.4 release candidates.

## Why a signed-in team account could not connect
The broker asks the admin console (`/api/internal/remote/authorize`). A session is allowed only if the target
device's ISM company has `organizations.mixel_org_id` set (link to an admin company) and the controller's
Mixel ID is owner, or an assigned technician, of a provider that has that company as customer
(`admin.remote_provider_memberships`, `admin.remote_customers`, `admin.remote_customer_assignments`).
A refusal reaches the app as "unavailable". Devices under an unlinked ISM company can enrol but never connect.

## lab/ (never ship)
- `patch-qa-session.py`: cargo feature `qa-session`; with `MIXEL_QA_SESSION_FILE` set, "Sign in" loads a
  session file instead of opening the browser. Build lab binaries with `--features qa-session`; release builds
  must not contain the string `MIXEL_QA_SESSION_FILE`.
- `lab-session.py` (run on the VPS with sudo): creates the dedicated staff test login and mints sessions;
  `remove` deletes the login again. The connect test also needs a temporary technician assignment for the
  test subject in the admin DB; delete it afterwards.
- `e2e-enrol-test.py`: API-level enrolment test (15 checks).
