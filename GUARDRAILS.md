
## Ghost directory rule

NEVER audit, scan, or modify files under:
- .claude/worktrees/
- .codex/worktrees/
- .antigravity/worktrees/
- .worktrees/
- .git/worktrees/
- Any directory matching */worktrees/*

These are agent scratch directories or local Git worktree checkouts with stale copies of the repo.
Always use `git ls-files --cached --others --exclude-standard` as the source of truth for "what files exist in this project."

If you find yourself reading `legacy_backend/`, `old_supabase/`, `archived/`, or anything that looks like a frozen snapshot, STOP. Ask Michael whether that directory is current or a ghost.
