# CLAUDE.md

This repo's general agent instructions live in `WARP.md` (originally written for Warp, but it's the canonical rules/architecture doc for any AI agent working in this repo, Claude included). Read it before making changes.

In particular, see the "External API Rate Limits (Yahoo especially)" rule under `## Rules` — added 2026-08-08 after an 11-day Yahoo outage that a retry-forever loop with no backoff likely made worse. Any code touching Yahoo's Fantasy Sports API (or any third-party API) needs rate-limit/backoff handling considered from the start, not bolted on after an outage.

## The 2027 rebuild (Postgres on Neon)

FBP is moving off JSON files committed to git and onto Postgres hosted on Neon, one area at a time. The plan lives in `specs/`. Start with `specs/README.md`, which lists every spec, its status and the build order.

**Before writing any code for a rebuild task:**
1. Read the spec for that area, plus `specs/01_neon_foundation.md` ("Design rules").
2. Read the matching findings doc if there is one (`specs/*_findings_2026.md`). It lists what the 2026 code does wrong, with file and line references.
3. Enter plan mode and map the plan to the spec's numbered tests. Wait for approval.

**Rules for rebuild work.** Where these conflict with older guidance in `WARP.md` (git-backed persistence, `commit.sh`, `DATA_LOCK`), these win for anything that has moved to Postgres.
- **Never touch the Neon `main` branch.** Each worktree gets its own Neon branch. Only Zach promotes to main.
- **The spec is the source of truth.** If the data or code disagrees with the spec, stop and report it. Don't guess, and don't change the spec yourself.
- **One fact lives in one place.** No copies of the same value in two tables or a table plus a JSON file.
- **Every multi-step write is one transaction.** It all happens or none of it does. Serialize contested writes with `pg_advisory_xact_lock`.
- **No git in the data path.** Nothing that a manager does should cause a commit, a push or a redeploy.
- **Constraints over conventions.** If a rule can be a foreign key, check constraint or unique index, make it one.
- **Times are `timestamptz` in UTC.** Convert to ET only for display and for league deadlines that the spec defines in ET.
- **Team identity comes from the Worker's verified login,** never from the request body. Admin routes check admin.
- **One scheduler:** the bot's loop. Jobs are idempotent and safe to run twice.
- **Notifications go through the `notifications` outbox** with retries, never sent inline inside a transaction.
- **Tests come from the spec's test list.** A task is done when its acceptance criteria pass, not before.
- Rule questions (Constitution, how the league actually works) go to Zach. Don't infer rules from past data.
