#!/usr/bin/env python3
"""Fix managed-app sign-in for accounts that need to raise their assurance level.

A verified Mixel ID with a verified authenticator but no personal Remote account
(for example a staff/company admin) was sent to the personal "set up Remote" name
form and never offered the verification code step that reaches aal2. Offer the
code step (mfa_required) whenever the account has a verified TOTP factor; keep
personal setup only for accounts that cannot verify further.

Usage: patch-remote-signin.py [--check] <next-root>   (idempotent, exact-match)
"""
from __future__ import annotations
import sys
from pathlib import Path

FILE = "crates/desktop_app/src/account_auth.rs"
OLD = (
    "                setup = self\n"
    "                    .ism\n"
    "                    .personal_account(&tokens.access_token.0)\n"
    "                    .await\n"
    "                    .map_err(|_| \"Remote account service could not be reached\")?\n"
    "                    .is_some_and(|account| account.profile_id.is_none());\n"
)
NEW = (
    "                // A verified authenticator means the account can still raise\n"
    "                // its assurance level (for example a staff member reaching\n"
    "                // aal2), so offer the verification code step rather than\n"
    "                // personal setup. Personal setup is only for an account that\n"
    "                // cannot verify any further and has no Remote profile yet.\n"
    "                let has_verified_totp = user.factors.iter().any(|factor| {\n"
    "                    factor.status == \"verified\" && factor.factor_type == \"totp\"\n"
    "                });\n"
    "                setup = !has_verified_totp\n"
    "                    && self\n"
    "                        .ism\n"
    "                        .personal_account(&tokens.access_token.0)\n"
    "                        .await\n"
    "                        .map_err(|_| \"Remote account service could not be reached\")?\n"
    "                        .is_some_and(|account| account.profile_id.is_none());\n"
)

def main() -> int:
    args = sys.argv[1:]
    check = "--check" in args
    roots = [a for a in args if not a.startswith("--")]
    if len(roots) != 1:
        print(__doc__); return 2
    path = Path(roots[0]) / FILE
    raw = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    if NEW in text:
        print("already applied"); return 0
    if text.count(OLD) != 1:
        print(f"REFUSED: expected exactly one match, found {text.count(OLD)}"); return 1
    text = text.replace(OLD, NEW)
    if check:
        print("check ok: would patch 1 file"); return 0
    out = text.replace("\n", "\r\n") if crlf else text
    path.write_bytes(out.encode("utf-8"))
    print(f"patched {FILE}"); return 0

if __name__ == "__main__":
    sys.exit(main())
