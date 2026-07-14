---
description: Bug-hunt lenses for the full-audit workflow. Loaded when /full-audit runs.
---

# Full Audit Lenses

Use these lenses when reading files during the full-audit workflow. They are reminders of what to not miss, not a complete list. If something feels off and does not fit a category, report it anyway.

## Comprehension

- Does code do what its name or comments claim?
- Is there a mismatch between intent and implementation?

## Correctness

- Every branch reachable and correct in both directions?
- Edge cases: null, empty, zero, negative, max, unicode, concurrent.
- Loop termination, off-by-one, mutation under iteration.
- Async: awaited, error path handled, no race condition, no promise that never resolves.
- Comparisons right: `>=` vs `>`, `==` vs `===`.
- Stale closures and `useEffect` dependencies.
- State mutated directly instead of via setter.

## Wiring

- Function signature matches caller expectations.
- DB query matches actual schema; cross-reference migrations.
- API call matches endpoint contract.
- React props match what is passed.
- Env var exists in `.env.example` and deployment config.
- i18n key exists in every translation file: DE, FR, IT, EN where applicable.
- Import path resolves to a file that still exists.
- Orphaned files or unused exports.
- DB columns in schema that nothing reads or writes, or code references missing columns.
- Route defined but no consumer, or consumer references missing route.
- Webhook URL duplicated and only one copy updated.
- Feature flag checked in one file but not another.
- Components reading the same data with different cache keys.

## Consistency

- Similar problems solved differently elsewhere.
- Two sources of truth for the same data.
- Conflicting client and server types.
- Conflicting client and server validation rules.

## Safety

- Hardcoded secrets, API keys, tokens in config, comments, tests, or logs.
- SQL string concatenation.
- `dangerouslySetInnerHTML`, `eval`, or `Function()`.
- Open CORS, missing auth checks, missing rate limits.
- File uploads without size or type limits.
- PII or tokens logged or sent to error reporters.
- Service role key used outside server-only code.

## Multi-Tenant Safety

- Supabase queries missing `tenant_id` or organization scoping.
- RLS policies too permissive, especially `using (true)`.
- `tenant_id` taken from request body instead of JWT claims.
- Background jobs or webhooks not tenant scoped.
- Cross-tenant joins or dashboards.

## Reliability

- Null or undefined dereferences.
- Array bounds issues.
- Division by zero or integer overflow.
- Float used for money.
- Date parsing without timezone awareness.
- Network calls without timeout, retry, or backoff.
- Streams, listeners, or subscriptions not cleaned up.
- Resources opened but not closed.

## Performance

- N+1 query patterns.
- Missing indexes on filtered or joined columns.
- Unbounded list rendering without pagination or virtualization.
- Heavy work on the main thread.
- High-frequency polling without backoff.
- Large objects held in React state where refs would do.
- New object references on every render causing re-renders.

## API Contracts

- Inconsistent error response shapes.
- Wrong HTTP status codes.
- Missing required fields in responses.
- Pagination edge cases: empty page, last page, cursor reuse.

## State And Caching

- Multiple sources of truth.
- Mutations that do not invalidate caches.
- Optimistic updates without rollback.
- Race conditions between state updates.

## MIXEL Compliance

- Accounting or POS writes not append-only: GeBueV risk.
- Receipts editable after issuance: GeBueV risk.
- Swiss VAT: 8.1%, 2.6%, 3.8%; CHF 0.05 rounding.
- Call recording without consent flow in `mixel-pbx`.
- PII in plain logs: GDPR risk.
- Missing audit log entries for tenant-sensitive actions.
- Retention period not enforced.

## MIXEL Stack Rules

- Convex references anywhere: retired, must be removed.
- Email or SMS for system events: use push and in-app instead.
- Direct `ReadableStream` use: breaks Capacitor WKWebView; use SSE fallback via `res.text()`.
- Supabase service role key in client code.
- Migrations not append-only safe.
- For `mixel-pbx`: CDR writes not append-only.

## Dead References

- Imports pointing to files that no longer exist.
- Routes referenced but not defined, or defined but never used.
- Env vars used but missing from `.env.example`.
- DB columns referenced in code but not current schema.
- i18n keys used but missing from translation files.
- TypeScript types imported but not exported.

## Huge Or Auto-Generated Files

For files over 1000 lines, lockfiles, `*.gen.*`, `db.types.ts`, and generated GraphQL types:

- Do not do line-by-line prose audit.
- Check hardcoded secrets.
- Check schema drift against code that consumes it.
- Check license or attribution if vendored.
- Check freshness: when regenerated, and whether source remains in repo.
- Mark inventory as `[HUGE - risk-lens only]`.

## Anything Else

Report all of these:

- "This looks fragile" with what could break it.
- "I do not understand why this works" because it might not.
- Confusing naming that could cause future bugs.
- Dead code.
- TODO, FIXME, or HACK comments.
- Test coverage gaps for critical paths.
