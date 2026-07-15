Push pending commits in fbp-trade-bot and fbp-hub. These commits already exist locally and are fully tested/verified — this task is ONLY to get them onto origin/main safely. Do not re-run, re-generate, or "fix" anything; do not run any data pipeline, backfill, sync, or graduation scripts. Do not touch token.json.

## fbp-trade-bot

Local main is 2 commits ahead of the last-known origin/main (e97bee3):
- 61945e6 — Add project README, archive point-in-time build/incident logs
- e035e4c — Documentation overhaul: fix Render->Railway drift, archive superseded build plans, merge duplicates

Both are documentation-only changes (new README.md, docs moved into docs/archive/, Render→Railway reference fixes, a couple of merged/deleted docs) — no data or application code touched. Nothing time-sensitive, but no reason to hold it back either.

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -5` — check whether origin/main is still at `e97bee3`, or has moved forward (e.g. more standings/daily-update commits from Railway).
3. If origin/main is unchanged: `git push origin main` (plain fast-forward, no force needed).
4. If origin/main has moved forward: do a normal `git merge origin/main` or `git rebase origin/main` — **not** a rebase with `-X ours`/`-X theirs`, and no `git push --force`. These commits only touch markdown files and a few `git mv` renames into `docs/archive/`, so a real conflict is unlikely, but if one does turn up, resolve it by keeping both sides' intent rather than blindly discarding either. If you can't confidently reconcile it, stop and flag Zach rather than guess.
5. After pushing, verify: `git log --oneline origin/main -1` matches local HEAD.
6. Spot check: `ls docs/archive/` should include `WHAT_YOU_GET.md`, `FILE_STRUCTURE_GUIDE.md`, `TRADE_TOOL_INTEGRATION.md`, `FOR_WARP.md`, `GIT_COMMIT_FIXES_2026-02-23.md`, `API_GIT_COMMIT_AUDIT.md`, `RANK_FIELD_SUMMARY.md`, `DRAFT_SYSTEM_HANDOFF.md`, `GRADUATION_PLAN_2026_MIDSEASON.md`; and `README.md` at repo root should be non-empty (`wc -l README.md`, expect ~80+ lines, not 0).

## fbp-hub

Local main is 2 commits ahead of the last-known origin/main (022fdac):
- 871fc4f — Add project README, archive superseded planning docs
- 15128a6 — Documentation overhaul: retire stale README, archive superseded build plan, fix Render->Railway drift

Also documentation-only — new root README.md, `data/README.md` deleted (superseded, was badly stale), `FBP_AUCTION_INTEGRATION_PLAN.md` moved to `docs/archive/`, WARP.md reference fixes, a couple of Render→Railway wording fixes in the buy-in docs.

Same steps: fetch, check if origin/main moved, fast-forward push if not, normal merge/rebase (no force, no blind conflict auto-resolution) if it has. Spot check: `ls docs/archive/` should include `FBP_AUCTION_INTEGRATION_PLAN.md`, `PHASE1_COMPLETE.md`, `OLD_AUCTION_APPSCRIPTS.md`, `WARP_FASTAPI_ANSWER.md`; `data/README.md` should no longer exist; `README.md` at repo root should exist and be non-empty.

## After both are pushed

Railway will redeploy fbp-trade-bot from the new main (docs-only change, so nothing functionally different — just confirms the deploy pipeline is still healthy). No further action needed on fbp-hub beyond the push; GitHub Pages redeploys automatically.
