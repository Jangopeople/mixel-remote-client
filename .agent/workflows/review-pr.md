# Workflow — Review a PR

The day-to-day Antigravity audit. Run on every PR Codex opens.

---

## When to use this workflow

- Codex opens a PR → run this workflow
- App Doctor remediation lands as a PR → run this workflow (per-fix audit)
- Hotfix PR → run this workflow (briefer, focused on what changed)

For deep system-wide audits (App Doctor's diagnosis phase), use `audit-deep.md` instead.

---

## Step 1 — Load context

In order:

1. Read this workflow file (you're here)
2. Read `.agent/SKILL.md` if not already loaded
3. Read `.agent/rules/mixel-defaults.md` (always)
4. Read the PR description — extract the claimed FRs, screens, Edge Functions, tables, states covered
5. Read the linked product specs in this repo:
   - `ARCHITECTURE.md` — the schema and patterns contract
   - `docs/ux/UX_SPEC.md` — the UX contract (especially §7 states)
   - `docs/ui/UI_SPEC.md` — the UI contract
6. Based on the PR's touched layers, load the relevant rule files:
   - DB / RLS / Edge Functions touched → `rules/multi-tenant.md` + `rules/security-baseline.md`
   - Financial / personal data touched → also `rules/swiss-compliance.md`
   - UI touched → `rules/ui-tokens.md`
7. Read the PR diff

Don't load rules that don't apply. Keep the audit focused.

---

## Step 2 — Cross-check against the PR's claims

The PR description says it addresses certain FRs, screens, states. Verify:

- ✅ Every claimed FR has corresponding code change
- ✅ Every claimed screen has the file change (`docs/ui/screens/` or the actual page component)
- ✅ Every claimed state (empty / loading / error / success / edge) is actually implemented and tested
- ✅ Tests cover the changed functionality (not just structural placeholder tests)

If the PR claims something the diff doesn't show: **🔴 finding** — "PR description claims X but no corresponding change found."

---

## Step 3 — Pattern compliance

Run the rules against the diff. For each rule file you loaded:

- Scan the diff for matches against the patterns in that file
- For each match, decide severity (🔴 / 🟡 / 🟢)
- Note the file + line number
- Write the finding using the format below

### Finding format

```
🟡 **Missing error envelope in `create-invoice` Edge Function**

`supabase/functions/create-invoice/index.ts:43`

Returns `{ ok: false, message: "..." }` instead of the canonical
`{ error: { code, message, details, request_id } }` envelope.

Frontend error handler can't map this to user-facing copy from
the UX Spec §7 states. Breaks UX consistency across the product.

**Fix:**
```ts
return errorResponse('VALIDATION_FAILED', 'Customer ID required', { field: 'customer_id' });
```

`_shared/errors.ts` already has this helper.
```

### Severity calibration

- 🔴 = "I would not merge this" — security, data loss, compliance, broken core flow, secret leak
- 🟡 = "I'd merge after a fix" — pattern violations that compound, missing states, accessibility regressions
- 🟢 = "Note for next time" — polish, naming, minor inconsistencies

If you're unsure between severities, **err one level lower** — better to under-block than over-block.

---

## Step 4 — Verify the tests

- ✅ Edge Function changes have new / updated Deno tests
- ✅ React feature changes have Vitest + Testing Library tests
- ✅ User-facing flow has a Playwright happy-path E2E
- ✅ Tests cover the unhappy paths (validation failure, 401, 403) — not just happy

Tests missing for new business logic: **🔴**. Tests existing but covering only happy paths: **🟡**.

---

## Step 5 — Verify the PR description quality

Required fields (from `CODEX_CONVENTIONS.md` PR template):

- What (one paragraph)
- Linked FRs / Linear issue
- Edge Functions touched
- Tables touched
- States covered checklist
- Tests checklist
- Manual QA (Cloudflare preview link or recording)

Missing fields are 🟢 unless they make the audit impossible (e.g. no states checklist = can't verify state coverage). Then they're 🟡.

---

## Step 6 — Post the verdict

Single summary comment on the PR + line-level comments for each finding.

### Summary comment template

```
## Antigravity Audit

**Verdict**: 🔴 BLOCK | 🟡 NEEDS WORK | 🟢 APPROVED

**Findings**: X critical · Y major · Z minor

### 🔴 Critical (must fix before merge)
- [Title with link to line-level comment]
- ...

### 🟡 Major (should fix before merge)
- [Title with link]
- ...

### 🟢 Minor (flag only)
- [Title with link]
- ...

### What's good
- [Optional: 1–2 things done well — keeps the audit honest, not just negative]
```

If verdict is 🟢 APPROVED (no 🔴 or 🟡), summary can be one line: *"LGTM — Audit clean."*

### Verdict logic

- Any 🔴 finding → BLOCK
- 3+ 🟡 findings OR any 🟡 finding affecting compliance/security → NEEDS WORK
- Otherwise → APPROVED (even with 🟢 findings)

---

## Step 7 — File Linear issues for findings you flagged

For each 🔴 or 🟡 finding:

- Create / update a sub-issue under the PR's Linear epic
- Title: `[Antigravity] <finding title>` (so they're searchable)
- Body: copy of the finding (with code-block fix suggestion)
- Status: depending on workflow — usually "Backlog" if not blocking the current PR, "In Progress" if it's blocking

If Linear connector isn't available, skip — note in the PR comment that the findings should be tracked manually.

---

## Step 8 — Done

Wait for Codex to revise. When Codex pushes new commits, re-run the workflow on the updated diff.

If Codex's revision claims to address findings: verify each addressed finding in the new diff. Don't trust the PR description alone — verify the actual code.

When all 🔴 and 🟡 findings are addressed (or Michael explicitly waives them), update the verdict to 🟢 APPROVED.

Then Michael takes over for QA on the Cloudflare preview deploy.

---

## What you don't do in this workflow

- ❌ Merge the PR (Michael does)
- ❌ Push fixes yourself (Codex does)
- ❌ Approve a PR with 🔴 findings without explicit Michael waiver
- ❌ Audit code that's unrelated to this PR (out of scope)
- ❌ Propose new features (out of scope)
- ❌ Audit for taste — only audit against the loaded rules

Stay in lane. The chain is fast when everyone does their part.
