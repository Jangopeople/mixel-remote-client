---
description: Multi-session 100% coverage audit. Resumable via repo-local state files. Reports only; fixes require explicit approval.
---

# Full Audit Workflow

You are running a multi-session audit of the currently-open MIXEL repo. Each time this workflow runs, resume from where the previous session left off.

Your primary job is to read every file and report every bug. Fixes happen only on explicit Michael approval.

## Pre-flight

- Antigravity mode: Local Mode, or a named persistent worktree. Default New Worktree Mode is not safe for this audit because state files may not persist across sessions. If you detect a fresh ephemeral worktree, stop and tell Michael to switch.
- Read any of these files that exist:
  - `.agents/rules/full-audit-lenses.md`
  - `.agent/rules/full-audit-lenses.md`
  - `.agents/rules/*.md`
  - `.agent/rules/*.md`
  - `AGENTS.md`
  - `GUARDRAILS.md`
  - `PROJECT.md`
  - `README.md`

## State Files

All state files live at repo root:

- `AUDIT-PROGRESS.md`: state machine and batch pointer. Rewrite each session.
- `AUDIT-INVENTORY.md`: every file in scope with status checkbox. Update rows in place.
- `AUDIT-REPORT.md`: findings are append-only. Summary counters at top may be updated.
- `AUDIT-DECISIONS.md`: ambiguities and items needing Michael's judgment. Append only.

First action: check whether `AUDIT-PROGRESS.md` exists.

- Missing: run Session 1 Bootstrap.
- Present: run Session N Audit Batch.

## Session 1: Bootstrap

Hard stop: do not audit source files during Session 1. Do not open source files to preview them. Bootstrap creates inventory and state files only, then stops.

1. Generate file list using Git so `.gitignore` is respected:

```bash
git ls-files --cached --others --exclude-standard \
  | grep -Ev '^(node_modules/|\.git/|\.claude/|\.codex/|\.antigravity/|\.worktrees/|dist/|build/|\.next/|\.turbo/|coverage/|out/|\.cache/|ios/App/Pods/|android/\.gradle/)' \
  | grep -Ev '/(node_modules|dist|build|\.next|\.turbo|coverage|out|\.cache|Pods|\.gradle|worktrees|\.worktrees)/' \
  | grep -E '(\.(ts|tsx|js|jsx|mjs|cjs|sql|prisma|graphql|py|go|rs|lua|swift|kt|java|gradle|plist|xcconfig|json|jsonc|toml|ya?ml|xml|sh|bash|zsh|css|scss|html|md|mdx)$|(^|/)\.env[^/]*\.example$|(^|/)(Dockerfile[^/]*|Podfile|Makefile|Gemfile)$)' \
  | sort > /tmp/audit-files.txt
```

2. Bucket every file by risk priority:

P0 Critical Path:

- `supabase/migrations/**`, `supabase/functions/**`
- `**/auth/**`, `**/security/**`, `**/rbac/**`, `**/tenancy/**`
- `**/payments/**`, `**/invoice/**`, `**/billing/**`, `**/vat/**`, `**/twint/**`
- `services/*/src/server*`, `services/*/src/index*`
- root `package.json`, root `*.config.{ts,js,toml,yaml}`, `supabase/config.toml`
- `.env.example`, `capacitor.config.*`

P1 Business Logic:

- `src/lib/**`, `services/*/src/**`, `scripts/**`
- `**/api/**`, `**/routes/**`, `**/handlers/**`
- `supabase/seed*.sql`

P2 UI and Application:

- `src/components/**`, `src/app/**`, `src/pages/**`, `src/views/**`
- `src/hooks/**`, `src/contexts/**`, `src/stores/**`
- `ios/App/App/**`, `android/app/src/**`

P3 Tests and Types:

- `**/*.test.*`, `**/*.spec.*`, `**/__tests__/**`
- `**/types/**`, `**/*.d.ts`

P4 Docs, CSS, Assets, Generated:

- `docs/**`, `*.md`, `README*`
- `**/*.{css,scss}`
- auto-generated files and anything not matched above

3. Identify huge or auto-generated files and mark them with `[HUGE]`:

- files over 1000 lines
- lockfiles
- `*.gen.*`, `*db.types.ts`, generated GraphQL types

HUGE files get risk-lens audit only, not line-by-line prose audit.

4. Create `AUDIT-INVENTORY.md`:

```markdown
# Audit Inventory

Generated: <YYYY-MM-DD HH:MM>
Total files in scope: <N>

## P0 - Critical Path (<count>)

- [ ] path/to/file.ts
- [ ] path/to/file2.sql [HUGE]

## P1 - Business Logic (<count>)

## P2 - UI & Application (<count>)

## P3 - Tests & Types (<count>)

## P4 - Docs, CSS, Generated (<count>)
```

5. Create `AUDIT-PROGRESS.md`:

```markdown
# Audit Progress

## Status

- State: BOOTSTRAPPED
- Total files in scope: <N>
- Files audited: 0
- Coverage: 0%
- Current bucket: P0
- Next file: <first P0 path>
- Sessions completed: 1
- Last session: <YYYY-MM-DD>

## Batch Policy

- Target 15 files per session
- Reduce to 8 if files average over 500 lines
- Increase to 25 if files average under 100 lines
- HUGE files count as 5 normal files
- Stop reading new files at 60% context used; finish writing findings

## Finding ID Format

`AUDIT-S<session_number>-<sequence>`, for example `AUDIT-S3-07`.

## Sessions Log

| # | Date | Bucket | Files read | Findings added | Notes |
|---|------|--------|------------|----------------|-------|
| 1 | <date> | bootstrap | 0 | 0 | inventory created |
```

6. Create `AUDIT-REPORT.md`:

```markdown
# Audit Report

## Summary (may be updated each session)

- CRITICAL: 0
- HIGH: 0
- MEDIUM: 0
- LOW: 0
- Last updated: <YYYY-MM-DD>

## Findings (append-only)
```

7. Create `AUDIT-DECISIONS.md`:

```markdown
# Audit Decisions & Open Questions

## Architectural Ambiguities

## Missing Context Files

## Items Needing Michael's Judgment

## Areas Requiring Runtime Infrastructure To Audit
```

8. Report and stop:

```text
Bootstrap complete. <N> files in scope.
P0: <c>, P1: <c>, P2: <c>, P3: <c>, P4: <c>. <huge_count> marked [HUGE].
State files created. Paste /full-audit again to begin Session 2.
```

## Session N: Audit Batch

1. Resume:
   - Read `AUDIT-PROGRESS.md`.
   - Read `AUDIT-INVENTORY.md`.
   - Skim the last 5 findings in `AUDIT-REPORT.md`.
   - Read `AUDIT-DECISIONS.md`.

2. Update `AUDIT-PROGRESS.md` state to `IN_PROGRESS`.

3. Select batch:
   - Take next 15 unchecked `[ ]` files from current bucket.
   - If bucket is done, advance P0 to P1 to P2 to P3 to P4.
   - If all buckets are done, jump to Completion.
   - HUGE files count as 5 normal files toward the batch limit.

4. For each file:
   - Read the full file unless it is `[HUGE]`.
   - Trace purpose, inputs, outputs, dependencies, and consumers.
   - Apply lenses from `full-audit-lenses.md`.
   - Report anything off, not only items in the lens list.

5. For each finding, append immediately to `AUDIT-REPORT.md`:

```markdown
## [SEVERITY] <one-line title>

**ID:** AUDIT-S<N>-<seq>
**File:** path:line
**Bucket:** P<x>
**What:** plain English
**How I noticed:** what tipped me off
**Why it matters:** concrete impact
**Root cause hypothesis:** best guess
**Proposed fix:** specific change
**Risk of fix:** what could break
**Confidence:** high / medium / low
**Status:** REPORTED
```

Severity:

- CRITICAL: data loss, security holes, compliance violations, broken build, multi-tenant leaks, money math wrong
- HIGH: logic bugs affecting users, performance traps, broken contracts
- MEDIUM: dead code, refactor opportunities, fragile patterns
- LOW: style, naming, TODOs, confusion risks

6. For HUGE or auto-generated files:
   - Apply risk lens only.
   - Check hardcoded secrets, schema drift, license or attribution, and freshness.
   - Do not line-by-line prose audit.
   - Mark inventory as `[x] path [HUGE - risk-lens only] <- N findings`.

7. Update `AUDIT-INVENTORY.md` after each file:
   - Change `[ ]` to `[x]`.
   - Append `<- N findings`.

8. Context budget check after every 5 files:
   - If context feels over 60% used, stop reading new files.
   - Finish writing findings and wrap up.

9. Mandatory wrap-up:
   - Update `AUDIT-PROGRESS.md`: files audited, coverage, current bucket, next file.
   - Update summary counters in `AUDIT-REPORT.md`.
   - Add row to Sessions Log.
   - Set state to `IN_PROGRESS` or `COMPLETE`.

10. Drift self-check before reporting:
    - Inventory checkboxes match files claimed read.
    - Findings have IDs in `AUDIT-S<N>-<seq>` format.
    - `git diff` shows only `AUDIT-*.md` changes in audit mode.
    - Stayed in current bucket unless bucket completed.
    - Fix any mismatch before reporting.

11. Report:

```text
Session <N> complete.
Files this session: <count>. Total: X/Y (Z%).
Bucket: P<x> (<done>/<total>).
Findings added: <C>/<H>/<M>/<L>. Running total: <C>/<H>/<M>/<L>.
Decisions needing input: <yes/no, brief>.
Paste /full-audit for Session <N+1>.
Or reply fix CRITICAL / fix AUDIT-S2-03, AUDIT-S3-01 to enter fix mode.
```

## Completion

When every file in inventory is `[x]`:

- Set state to `COMPLETE`.
- Write final summary at top of `AUDIT-REPORT.md`.
- Report total findings, top 10 CRITICAL, and prompt for fix mode.

## Fix Mode

Only enter fix mode on explicit request such as `fix CRITICAL`, `fix CRITICAL+HIGH`, or `fix AUDIT-S<N>-<seq>`.

1. Create an Antigravity checkpoint.
2. Set state to `FIXING`.
3. For each approved finding, in severity order:
   - Restate finding and proposed fix.
   - Apply minimal diff.
   - Verify with the correct tool: targeted test, browser subagent, Supabase MCP, or curl.
   - Update finding status: `FIXED`, `PARTIAL`, or `COULD_NOT_FIX`.
4. Stop conditions:
   - same error 3 times: skip
   - fix touches more than 5 unrelated files: revert and skip
   - over 30 iterations: stop
   - over 200 tool calls: stop
5. End with `git diff --stat`. Do not commit. Do not push.

## Hard Rules

- Never run: `rm -rf`, `sudo`, `chmod 777`, `git push`, `git push --force`, `git reset --hard`, `mkfs`.
- Never commit. Never push.
- Never edit package versions just to silence warnings.
- Never edit deployed migrations; write new ones.
- Never disable RLS or remove tenant filters.
- Never reintroduce Convex.
- Never add email or SMS for system events.
- Never skip state-file updates at session end.
- Never restart from scratch if `AUDIT-PROGRESS.md` exists.
- If stuck on the same issue 3 times, stop and report.
