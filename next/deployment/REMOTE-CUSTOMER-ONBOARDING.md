# Remote customer onboarding rollout

Build 3 has a personal Remote workspace for a verified Mixel ID. It keeps an
account name, registers Macs by their protected public keys, and assigns a
stable nine-digit ID. The existing Admin Cockpit handles company access.
Personal device authorization permits attended sessions only between devices
owned by the same verified account.

1. Verify the current production database migration versions and take a
   recoverable backup. Apply `007_personal_remote_accounts.sql` in one
   transaction. It adds private account and device tables, authenticated RPCs
   scoped to the caller, a shared connection-ID reservation table, and a trigger
   that reserves IDs and keys for existing ISM enrollment writes. Backfill
   checks uniqueness before committing.
2. Stage the new identity authority image and the Admin Cockpit policy build.
   Preserve the authority's signing identity. Verify health and staff enrollment
   compatibility, then switch the isolated Remote identity authority and the
   Admin Cockpit policy endpoint. The existing broker still requires explicit
   consent, signed device tickets, and live authorization at the policy endpoint.
3. Before exposing the enrollment flow, run
   `authentik/redact-remote-enrollment-mail.py` against the pinned Authentik
   worker's `/authentik/stages/email/tasks.py`. Mount the result read-only at
   that path on the worker and confirm that the composed change affects only
   that mount. The worker retains delivery metadata for Remote enrollment and
   omits the message body containing the confirmation link. Verify that a real
   confirmation arrives and its audit event contains no link.
4. Import `authentik/remote-enrollment.yaml` to add the dedicated Mixel Remote
   enrollment flow. It creates inactive external users and activates them
   through a mailbox confirmation. On first sign-in, the Mixel ID OAuth
   provider independently verifies the current email address before the Remote
   profile RPC accepts the identity. The flow grants no ISM staff or company
   roles.
5. With the signed native QA host and a dedicated disposable Mixel ID, verify email confirmation, Remote
   display-name setup, a repeat device registration returning the same ID, a
   real attended request with Accept, a denied cross-account request, and the
   existing company role checks. Then upload the signed macOS build 3 to the
   existing internal TestFlight group and repeat onboarding from the installed beta.

If production verification fails, restore the prior service artifacts and
unpublish the dedicated enrollment flow. Preserve new data and ID reservations
for an audited recovery. Keep existing TestFlight builds and tester access.
