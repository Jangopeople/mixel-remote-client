# Security Baseline — Audit Rules

Load when the PR touches Edge Functions, auth, env vars, or any code handling user input.

---

## Secrets in code or env

🔴 Anything resembling a secret committed to git: `sk_live_`, `sk_test_`, `mxl_`, `eyJ` (JWT prefix), `xoxb-`, `AKIA`, full Stripe / Supabase / Cloudflare keys.

🔴 Secret in `.env.example` with real values instead of placeholders.

🔴 `service_role` Supabase key referenced in frontend code (any file under `apps/frontend/src/`).

🔴 Any env var prefixed `VITE_` / `PUBLIC_` / `NEXT_PUBLIC_` containing a secret. These are exposed to the browser by build tools.

🟡 Long-lived API keys without a documented rotation schedule.

🟡 Secrets logged anywhere — error messages, response bodies, audit_log, structured logs.

---

## Service-role usage in Edge Functions

🔴 Edge Function uses `service_role` for an operation that could be done by the authenticated user's client (i.e. bypasses RLS unnecessarily).

🟡 Edge Function uses `service_role` without a code comment explaining why bypass is necessary.

🟡 Edge Function mixes `service_role` and authenticated operations in the same function — risk of using the wrong client for a query. Split into two functions when reasonable.

---

## JWT verification

🔴 Edge Function that handles user data but doesn't call `verifyJwt` (or equivalent) before reading the request.

🔴 `verifyJwt` called but result not checked — function proceeds even when verification fails.

🟡 JWT verification reimplemented inline instead of using `_shared/verifyJwt.ts`.

🟡 No role check in Edge Functions that perform role-restricted actions.

---

## Input validation

🔴 Edge Function reads `await req.json()` and uses fields directly without Zod validation. Untyped + unvalidated input is an attack vector.

🟡 Zod schema present but `strict()` not used — extra fields pass through.

🟡 Type assertions (`as Whatever`) used to bypass validation.

🟢 Validation error messages not user-friendly (this affects UX, but flag here too).

---

## SQL injection / dynamic queries

🔴 Edge Function builds SQL via string concatenation with user input.

🔴 Frontend builds Supabase filter expressions from user input without sanitization (rare but happens with custom query builders).

🟡 Use of `.rpc()` to call a function that takes a string parameter used in dynamic SQL inside the function — verify the function uses `format()` with `%I` / `%L` properly.

---

## Cross-origin / CORS

🟡 Edge Function returns `Access-Control-Allow-Origin: *` — overly permissive. Should be locked to the product's domain(s).

🟡 Allow-credentials with wildcard origin — invalid combination, browser will reject.

🟢 CORS handled per-function instead of via shared middleware.

---

## Rate limiting

🟡 Public Edge Function (no auth required, e.g. inbound webhooks) without rate limiting at Cloudflare WAF or in the function.

🟡 Authenticated Edge Function performing expensive operations (LLM calls, file generation) without per-tenant rate limiting.

🟢 Rate limit headers not set in responses (`X-RateLimit-Limit`, `X-RateLimit-Remaining`).

---

## File uploads

🔴 Upload endpoint without MIME validation server-side. Frontend extension check alone is bypassable.

🔴 Upload endpoint without size limit enforced server-side.

🟡 Uploaded file served via a public bucket / public URL when the file should be tenant-scoped.

🟡 Signed URL generated with overly long expiry (default should be < 1 hour for sensitive content).

---

## Authentication flows

🔴 Login form posts credentials to a non-HTTPS endpoint (even in dev — wrong habit).

🔴 Magic link / password reset link generated without expiry.

🔴 Session refresh endpoint doesn't verify the refresh token.

🟡 No protection against credential stuffing (rate limit on login attempts per IP / per email).

🟡 OAuth flow without state parameter (CSRF risk).

---

## CSP and security headers

🔴 `public/_headers` (Cloudflare Pages) missing CSP header entirely.

🔴 CSP includes `unsafe-inline` for scripts.

🟡 CSP includes `unsafe-eval` without a code comment explaining the dependency that requires it (some libraries do).

🟡 Missing `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`.

---

## 2FA / sensitive operations

🟡 Sensitive operation (delete account, change billing, export all data) doesn't require re-authentication or 2FA.

🟡 2FA available but not enforced for owners / admins of regulated tenants.

🟢 2FA setup flow doesn't show recovery codes.

---

## Webhook signature verification

🔴 Inbound webhook (TWINT, Stripe, GitHub, etc.) doesn't verify the provider's signature before any DB write.

🔴 Outbound webhook delivered without HMAC signature in headers.

🟡 Inbound webhook signature verified but timing-attack-vulnerable comparison (`===` instead of constant-time compare).

🟡 Outbound webhook secret stored as plain text without per-webhook rotation capability.

---

## Sensitive data exposure

🔴 API response includes sensitive fields the caller shouldn't see (e.g. password hashes, internal flags, other users' emails).

🔴 Bulk export endpoint without tenant + role check (could export another tenant's data).

🟡 Error messages leak internal info (file paths, stack traces, SQL syntax).

🟡 `console.error` of full request object in production code.

---

## XSS

🔴 `dangerouslySetInnerHTML` with user-supplied content not sanitized via DOMPurify or equivalent.

🟡 User-supplied URLs in `<a href>` without `javascript:` / `data:` scheme rejection.

🟡 User-supplied content rendered in `<style>` tag or `style` attribute.

---

## Dependencies

🟡 New dependency from a publisher with low download count or new package.

🟡 New dependency added without checking license compatibility (no GPL/AGPL in MIXEL products unless explicitly intentional).

🟡 Lock file (`pnpm-lock.yaml` / `package-lock.json`) not committed alongside dependency changes.

🟢 Dependency pinned to a major version that's significantly behind current stable.

---

## Reverse-engineering finding patterns (App Doctor pairing)

When auditing an existing app, common security debt:

- `service_role` key was added to `VITE_SUPABASE_KEY` "to make things work" → 🔴 leaked to browser
- Edge Functions written before `_shared/verifyJwt.ts` existed → ad-hoc auth → inconsistent
- Stripe / TWINT webhooks without signature verification "because we trust the IP" → 🔴 spoof risk
- `dangerouslySetInnerHTML` used for "trusted admin content" → 🔴 still XSS-exploitable

These are common patterns in older MIXEL apps that pre-date the patterns doc. Flag them all.
