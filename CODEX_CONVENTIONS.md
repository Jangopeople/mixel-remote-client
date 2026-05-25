# MIXEL Codex Conventions

Detailed reference. `AGENTS.md` summarizes; this file is where Codex looks for the *how* on each topic.

---

## Branches & commits

- **Branch from `main`**: `feat/<slug>`, `fix/<slug>`, `fix/r-XXX-<slug>`, `chore/<slug>`
- **Conventional Commits**:
  - `feat(orders): add order list view`
  - `fix(auth): handle 401 on token refresh`
  - `fix(r-001): add RLS to invoices`
  - `chore: bump dependencies`
  - `test(orders): cover empty + error states`
  - `docs: update ARCHITECTURE.md §10`
- **Atomic commits**: one logical change per commit. Message explains *why*, not *what*.
- **Squash on merge** unless commits are intentionally split (e.g. data migration + code change).

## TypeScript

- **Strict mode on**. No `any` without an inline comment justifying.
- **Types near use**. Feature-specific types live in `features/<name>/types.ts`. Shared types in `src/lib/types/` or `supabase/functions/_shared/types.ts`.
- **Zod schemas** for every Edge Function input and every form. Infer TS types from schemas — single source of truth.
- **Prefer `type` over `interface`** unless you specifically need declaration merging.
- **No magic numbers**. Constants in a named export.

## React

- **Function components only**. No class components.
- **Hooks**:
  - Custom hooks in `features/<name>/hooks/` (feature-local) or `src/hooks/` (shared)
  - One hook per file; export name matches filename
- **No `useEffect` for data fetching** — use TanStack Query exclusively
- **No prop drilling beyond 2 levels** — extract context or store
- **Component file naming**: `PascalCase.tsx`. Hook naming: `useThing.ts`. Util naming: `kebab-case.ts`.

## Styling — tokens, always

- All colors / sizes / radii / shadows / motion come from CSS variables in `tokens.css`
- Use Tailwind utility classes that consume tokens (e.g. `bg-[--bg-surface]` or configured Tailwind theme tokens)
- **Never** hardcoded values like `bg-[#FF0000]` or `p-[17px]`
- If a value isn't a token, **add the token** to `tokens.css` rather than hardcoding inline. Note the addition in your PR description.

## Class ordering in JSX

```
[layout] [spacing] [sizing] [typography] [color] [border] [state]
```

Example:
```tsx
<button className="flex items-center gap-2 px-4 py-2 h-10 text-sm font-medium text-white bg-brand hover:bg-brand-dark focus-visible:ring-2 disabled:opacity-50">
```

## Supabase usage

### Frontend (browser)
- Use the **authenticated Supabase client** (`createBrowserClient` or equivalent)
- RLS does the security — never bypass with `service_role`
- Pass through `Authorization: Bearer <session.access_token>` when calling Edge Functions

### Edge Functions
- Always start with auth verification using `_shared/verifyJwt.ts`:
  ```ts
  const { user, tenantId, role } = await verifyJwt(req.headers.get('Authorization'));
  ```
- Use `service_role` client only when needed (cross-tenant operations, admin actions)
- Return errors in the canonical envelope (see `ARCHITECTURE_PATTERNS.md` §12)
- Include `request_id` (UUID) in every response

### Migrations
- One logical change per file
- Forward-only — no `down` migrations
- RLS enabled in the same migration as table creation:
  ```sql
  create table public.orders (...);
  alter table public.orders enable row level security;
  create policy "orders_select" on public.orders for select ...;
  create policy "orders_insert" on public.orders for insert ...;
  create policy "orders_update" on public.orders for update ...;
  create policy "orders_delete" on public.orders for delete ...;
  ```
- Indexes in the same migration as the column they index
- Audit triggers wired immediately for financial / contractual data

## Testing

### Edge Functions (Deno test)
- At minimum: happy path + 2 unhappy paths (401, validation failure)
- Mock external APIs — never hit real third-party services in tests
- Test the error envelope shape

### React (Vitest + React Testing Library)
- One test per primary user interaction
- Test states: empty, loading, error, success, edge cases
- Use Testing Library queries (`getByRole`, `getByLabelText`) — avoid `getByTestId` unless necessary

### E2E (Playwright)
- One happy-path E2E per feature
- Run against the staging Cloudflare preview deploy (or local dev)
- Use `@axe-core/playwright` for accessibility checks

### Coverage targets
- Aim for >80% on `features/` and `supabase/functions/`
- Not enforced in CI — but tracked. PRs that drop coverage significantly need a comment explaining why.

## Accessibility (WCAG 2.1 AA minimum)

- Every interactive element keyboard-reachable
- Focus rings visible (`focus-visible:ring-2 ring-[--brand] ring-offset-2`)
- `aria-label` on icon-only buttons
- `aria-live="polite"` for toasts; `aria-live="assertive"` for errors
- Color contrast ≥ 4.5:1 for body text, ≥ 3:1 for large text and UI components
- Respect `prefers-reduced-motion: reduce`
- Modal focus traps with focus return to trigger on close
- Skip-links on pages with heavy nav

## i18n

- All user-facing strings in translation files (`src/i18n/<lang>.json`)
- Key pattern: `feature.element.state` — e.g. `pos.order.action.confirm`
- Default locale: DE (formal `Sie`). Then FR, IT, EN unless PRD says otherwise.
- Format with `Intl`:
  - Dates: `Intl.DateTimeFormat('de-CH').format(date)` → DD.MM.YYYY
  - Numbers: `Intl.NumberFormat('de-CH').format(n)` → 1'000.00
  - Currency: `Intl.NumberFormat('de-CH', { style: 'currency', currency: 'CHF' })`
- Design must handle German strings 30% longer than English (button paddings, table column widths)

## Performance

- **Code-split per route** — lazy-load page components
- **Bundle budget**: < 200KB gzipped JS per route, < 100KB CSS
- **Images**: AVIF / WebP, lazy below the fold, max 200KB for hero
- **TanStack Query**: 5min stale time default; per-query overrides documented in `api.ts`
- **Skeleton loaders within 100ms** for any operation > 200ms
- **No render-blocking third-party scripts**

## Security

- Never echo user input into HTML without escaping (React handles this by default — don't bypass with `dangerouslySetInnerHTML`)
- Never log emails, names, financial values, or full JWTs — only tenant_id + user_id UUIDs
- Use signed URLs for any private file access
- Rate limit Edge Functions per tenant (helper in `_shared/rateLimit.ts`)
- CSP headers in `public/_headers` (Cloudflare Pages format) — no `unsafe-inline`
- Secrets via env vars only — never in code, never in client bundle

## File / image uploads

- Upload to Supabase Storage with signed URLs
- Bucket path: `{bucket}/{tenant_id}/{entity_type}/{entity_id}/{filename}`
- MIME + extension whitelist enforced server-side
- Size limits enforced server-side (avatars 2MB, attachments 25MB, imports 100MB)

## Webhooks (outbound)

- Stored in `public.webhooks`
- Deliveries queued in `public.webhook_deliveries`
- HMAC-SHA256 signing using webhook secret, header `X-MIXEL-Signature`
- Retry backoff: 1m / 5m / 30m / 2h / 12h, max 5 attempts → dead-letter
- Event catalog single source of truth: `supabase/functions/_shared/webhook-events.ts`

## Error handling

Canonical envelope from every Edge Function:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "Tenant access required",
    "details": { },
    "request_id": "uuid"
  }
}
```

Standard codes: `UNAUTHENTICATED`, `FORBIDDEN`, `NOT_FOUND`, `VALIDATION_FAILED`, `CONFLICT`, `RATE_LIMITED`, `INTERNAL_ERROR`, `UPSTREAM_FAILURE`, `IDEMPOTENCY_CONFLICT`.

Frontend maps codes → user-facing copy from i18n + recovery action from `UI_SPEC.md` §7 states.

## When you're stuck

In order:
1. Check this `CODEX_CONVENTIONS.md`
2. Check `docs/architecture/decisions/` for an ADR
3. Check `ARCHITECTURE_PATTERNS.md` if it's in the repo or uploaded
4. **Stop and ask Michael in the PR description** — don't silently pick a side
5. Conflict between PRD / UX / UI / ARCHITECTURE → always stop and ask

Never invent answers. The system is designed for honest "I'm blocked" responses — they're cheaper than rework.

## PR description template

```
## What
<one paragraph>

## Linked
- FRs: <list>
- Screens: <list with preview-deploy links>
- Linear: <issue URL>

## Edge Functions touched
<list>

## Tables touched
<list>

## States covered
- [x] Empty
- [x] Loading
- [x] Error
- [x] Success
- [x] Edge: <description>

## Tests
- [x] Edge Function: N tests
- [x] React: N tests
- [x] E2E: 1 happy path
- [x] Accessibility scan passes

## Manual QA
<Cloudflare preview link + 30s recording>

## Notes for Antigravity
<anything non-obvious — patterns, tradeoffs, anything to look at>
```

That's the full reference. AGENTS.md gives the headlines; this file is the depth.
