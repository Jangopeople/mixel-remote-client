# MIXEL Defaults — Baseline Audit Rules

Applies to every audit. Always loaded. Other rule files extend this for specific concerns.

---

## Stack adherence

🔴 **Convex anywhere** → flag. Convex is retired. PRs introducing or expanding Convex use are blocked.

🔴 **Vercel / AWS / Firebase / Supabase Cloud references** → flag. Stack is Hetzner Supabase + Cloudflare only.

🔴 **Material UI / Chakra / Ant Design / Redux / MobX** → flag. Stack is shadcn/ui + Tailwind + Zustand + TanStack Query.

🟡 **Stack drift in package.json** — new dependencies that overlap with the locked stack (e.g. adding `swr` when TanStack Query is the standard) require justification in the PR description.

## Conventional Commits

🟡 PR title doesn't match Conventional Commits (`feat(scope): ...`, `fix(scope): ...`, `chore: ...`, `fix(r-XXX): ...`).

🟡 Commit messages explain *what* changed but not *why*.

## Branches

🔴 PR targets a branch other than `main` without explanation. (Hotfix branches, release branches require a PR note.)

🟢 Branch name doesn't match `feat/<slug>` / `fix/<slug>` / `fix/r-XXX-<slug>` / `chore/<slug>`.

## TypeScript

🟡 `any` used without a justifying comment.

🟡 Type assertions (`as`) where a proper type narrow would work.

🟢 Magic numbers (`if (x > 7)` with no constant or comment).

🟢 Types declared far from use when a feature-local placement would be cleaner.

## React

🔴 `dangerouslySetInnerHTML` without an inline justification comment and a sanitization step.

🟡 `useEffect` used for data fetching (TanStack Query should be used).

🟡 Class components.

🟡 Prop drilling beyond 2 levels — should be extracted to context or store.

🟢 File naming inconsistent: component files should be `PascalCase.tsx`; hooks `useThing.ts`; utils `kebab-case.ts`.

## Imports and structure

🟡 Cross-feature imports (`features/orders/` importing from `features/invoices/` directly) — should go via a shared module or be re-evaluated for feature boundaries.

🟡 New shared components added when an existing UI Spec component would have worked.

🟢 Barrel files (`index.ts`) re-exporting things from nested folders unnecessarily.

## Testing

🔴 No new tests for new business logic.

🟡 Tests exist but only cover happy paths (states catalog requires empty / loading / error / success / edge coverage).

🟡 E2E test missing for a user-facing feature.

🟡 Tests assert against implementation details rather than user-facing behavior.

🟢 Test naming unclear.

## i18n

🔴 Hardcoded user-facing strings (`"Confirm"`, `"Save"`, etc.) without i18n keys, even if MVP only ships one language.

🟡 i18n key naming inconsistent (`pos.order.confirm` vs `pos.orders.confirmation` for same concept).

🟢 Default-locale translations missing for new keys.

## Dates / numbers / currency

🟡 Hardcoded date format `MM/DD/YYYY` (Swiss is DD.MM.YYYY).

🟡 Hardcoded number format with commas (Swiss is apostrophes: `1'000.00`).

🔴 Money stored as `float` / `numeric` instead of `bigint` cents — precision pitfall.

## Documentation

🟢 PR description missing fields from the template (Linear link, screens, states covered, tests).

🟢 New Edge Function without a header comment explaining its purpose, inputs, outputs.

🟢 New table without column comments for non-obvious fields.

## Environment / secrets

🔴 Any string matching common secret patterns (`sk_`, `mxl_`, `eyJ`, etc.) in code or `.env.example`.

🔴 `service_role` key referenced in frontend code or in env vars prefixed `VITE_` / `PUBLIC_`.

🔴 Hardcoded API keys, even for "test" services.

🟡 `.env.example` not updated when new vars are introduced.

🟡 Secrets logged in error messages or response bodies.

## Performance

🟡 Synchronous loops over large arrays in render path.

🟡 Missing keys on `.map()` rendered components or using array index as key.

🟡 Heavy components without code splitting on a non-MVP route.

🟢 Bundle size growth > 20KB gzipped on a single PR without justification.

## Logging / observability

🔴 Logging full JWTs, email addresses, names, or financial values.

🟡 Edge Function without structured logging (should include `request_id`, `tenant_id`, `user_id`, `event`, `duration_ms`).

🟢 `console.log` left in production code.

## Migrations

🔴 New table without RLS enabled in the same migration.

🔴 RLS policy with `using (true)` on a write operation (insert/update/delete) on a tenant table.

🟡 Index missing on a column the queries clearly filter by.

🟡 Migration named without timestamp or with non-descriptive subject (`update.sql`, `fix.sql`).

🟡 Multiple logical changes bundled in one migration file.

🟢 Down migration included (we use forward-only).

## What this rules file does NOT cover

Specialized concerns live in dedicated rule files:

- **Tenant isolation** → `multi-tenant.md`
- **Security beyond secrets** → `security-baseline.md`
- **Compliance (GeBüV, FADP, VAT)** → `swiss-compliance.md`
- **Design tokens + accessibility** → `ui-tokens.md`

Load those alongside this file when their topic is in scope for the PR.
