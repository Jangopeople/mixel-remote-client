# Workflow — Deep Audit

A full-system audit, not a PR review. Run when:

- The App Doctor (Claude.ai project) is producing a diagnosis and asks you to do a deeper sweep
- Michael wants a periodic (quarterly) audit of an in-production product
- Before a major version bump or new tenant onboarding

For per-PR review work, use `review-pr.md` instead. This workflow is heavier.

---

## When NOT to use this workflow

- ❌ Reviewing a single PR → use `review-pr.md`
- ❌ Investigating a specific bug → use a targeted approach
- ❌ Auditing a brand-new app with no production usage → premature

---

## Step 1 — Confirm scope with Michael

Before starting, confirm:

1. **Which product** — `mixel-acc`, `mixel-pbx`, etc.
2. **Scope** — full audit, or focused on (auth / payments / data model / UI consistency / etc.)
3. **Paired with App Doctor?** — if yes, the App Doctor will produce `DIAGNOSIS.md`; your job is to feed it findings. If no, you produce the findings yourself in a single report.
4. **Time budget** — full audits can be multi-session

If scope is unclear: stop, ask Michael.

---

## Step 2 — Load all relevant context

You're auditing the whole product, not a PR. Load:

1. `.agent/SKILL.md`
2. `.agent/rules/mixel-defaults.md`
3. `.agent/rules/multi-tenant.md`
4. `.agent/rules/security-baseline.md`
5. `.agent/rules/swiss-compliance.md` (if any financial/personal data)
6. `.agent/rules/ui-tokens.md` (if frontend in scope)
7. Product specs:
   - `PRD.md`
   - `ARCHITECTURE.md`
   - `docs/ux/UX_SPEC.md`
   - `docs/ui/UI_SPEC.md`
   - `docs/architecture/decisions/` (all ADRs)
   - `docs/audit/DIAGNOSIS.md` (if a previous audit exists)
8. The full codebase — directory tree first, then dive into:
   - `supabase/migrations/` — every migration, in order
   - `supabase/functions/` — every Edge Function
   - `apps/frontend/src/features/` — every feature folder
   - `apps/frontend/src/lib/` — shared utilities and types
   - `tokens.css` — to check token discipline
   - `package.json` — dependencies and scripts
   - `_headers` / Cloudflare config — for CSP, security headers
   - CI workflows in `.github/workflows/`

---

## Step 3 — Build a current state map

Before you find problems, document what exists. Short structured doc:

```
## Implicit PRD (what the app does)
- Primary users: ...
- Core flows: ...
- Major features: ...

## Implicit UX
- Navigation: ...
- Screen count: ...
- Multi-tenancy: yes / no / partial
- Real-time: yes / no / scoped

## Implicit UI
- Component library: ...
- Token discipline: token-driven / mixed / hardcoded
- States coverage: full / partial / minimal

## Implicit Architecture
- Backend: ...
- Frontend hosting: ...
- Auth: ...
- State management: ...
- Test coverage: ...
- CI: ...
```

If paired with the App Doctor, share this map back. The App Doctor will validate with Michael before you proceed.

---

## Step 4 — Run the audit, by theme

Don't run rules sequentially file-by-file — run them by **theme**, finding all related issues across the codebase before moving on:

### Theme A — Security & Tenant Isolation
- Apply `rules/security-baseline.md` + `rules/multi-tenant.md`
- Scan: every Edge Function, every RLS policy, every migration, every frontend Supabase client usage
- Output: findings list grouped under this theme

### Theme B — Compliance
- Apply `rules/swiss-compliance.md`
- Scan: `audit_log` setup, data retention, export / delete endpoints, VAT handling, currency storage, QR-bill (if invoicing)

### Theme C — Architecture Debt
- Apply `rules/mixel-defaults.md` + check against `ARCHITECTURE_PATTERNS.md` if available
- Scan: stack adherence, Edge Function structure, error envelope consistency, idempotency, webhook delivery, real-time scope

### Theme D — Data Model
- Scan: schema quality, indexes, money storage, timestamp handling, audit triggers

### Theme E — UX Critical Path
- Apply `rules/ui-tokens.md` (state coverage parts)
- Compare against `docs/ux/UX_SPEC.md` §7 states catalog
- Scan: every user-facing screen for empty / loading / error / success / edge coverage

### Theme F — UI Polish & Accessibility
- Apply `rules/ui-tokens.md` (visual parts)
- Compare against `docs/ui/UI_SPEC.md` tokens and components
- Scan: hardcoded values, contrast, focus rings, ARIA, motion handling

### Theme G — Extensibility
- Check whether settings / webhooks / integrations / roles use the canonical patterns
- Note any feature that's hardcoded where a pattern would have allowed growth

### Theme H — Tests & Observability
- Test coverage on `features/` and `supabase/functions/`
- Structured logging present in Edge Functions
- Frontend error capture present

### Theme I — Developer Experience
- README quality
- `.env.example` completeness
- Type generation from Supabase
- Migration discipline

Skip themes that don't apply.

---

## Step 5 — Categorize and prioritize

For every finding, assign:

- **Severity**: 🔴 critical / 🟡 major / 🟢 minor
- **Theme** (from above)
- **Location**: file path + line, or screen ID
- **Impact**: 1 sentence
- **Effort to fix**: S / M / L

Then sort:

1. 🔴 critical → top
2. 🔴 + theme = compliance → also top
3. 🟡 + low effort → quick wins
4. 🟡 + high effort → bigger projects
5. 🟢 → flag, don't block

---

## Step 6 — Output

Two modes:

### Mode A — Paired with App Doctor

Hand the findings list back to the App Doctor (Claude.ai project). It will:
- Integrate them into `DIAGNOSIS.md`
- Sequence them in `REMEDIATION_PLAN.md`
- Generate Codex fix prompts in `CODEX_FIX_PROMPTS.md`

Your job ends with the findings list.

### Mode B — Standalone audit

Produce a single report `docs/audit/ANTIGRAVITY_AUDIT_<date>.md` with:

```markdown
# Antigravity Audit — mixel-{slug} — YYYY-MM-DD

## Scope
- Full audit | Focused: <area>
- Triggered by: quarterly / pre-release / Michael request

## Summary
- Findings: X critical · Y major · Z minor
- Top 3 priorities (by impact):
  1. ...
  2. ...
  3. ...

## Findings by Theme
### Theme A — Security & Tenant Isolation
- F-A001 [🔴] ...
- F-A002 [🟡] ...
...

### Theme B — Compliance
- ...

(repeat for every theme)

## Recommended next steps
- Hand to App Doctor for remediation plan, OR
- Quick wins (🟡 + S effort) Michael can ask Codex to fix directly today

## Re-audit
Recommend next audit after: <event or date>
```

Commit to the repo. Archive previous audits in `docs/audit/archive/`.

---

## Step 7 — Linear hygiene

For each 🔴 and 🟡 finding:

- Create / update a sub-issue in Linear
- Title: `[Audit YYYY-MM-DD] <finding title>`
- Label: severity (`critical`, `major`)
- Theme as a sub-category

Skip 🟢 unless Michael wants them tracked.

If Linear connector unavailable, list the issue titles at the end of the audit report so Michael can create them manually.

---

## What you don't do in a deep audit

- ❌ Fix anything yourself (Codex applies fixes after Michael approves the remediation plan)
- ❌ Propose new features
- ❌ Refactor for taste — only flag against rules + specs
- ❌ Audit code that's out of scope (if scope is "auth flow", don't drift into reports)
- ❌ Inflate severity to make findings look more urgent
- ❌ Underflag to be polite

Be honest, be specific, be done.
