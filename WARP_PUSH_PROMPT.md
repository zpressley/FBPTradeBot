# Push Prompt — fbp-trade-bot & fbp-hub

**Last updated: 2026-08-03 17:05 UTC.** Rewritten after every new batch of
local commits -- the "ahead of origin" counts/hashes below are accurate as
of this timestamp. If it's more than a day or two old, don't trust the
hashes -- re-run `git log --oneline origin/main -3` yourself first. (Note
for whoever's pushing: local history in this repo has been rebased onto
origin more than once this week as pushes landed out-of-band, so old
commit hashes mentioned in chat/docs may no longer exist even though the
content is already live -- always check origin/main's actual tip, not a
remembered hash.)

Push pending commits in fbp-trade-bot AND fbp-hub. Both repos have already
been reconciled with their origins (see below) -- this task is ONLY to push
the results. Do not re-run, re-generate, or "fix" anything; do not run any
data pipeline, backfill, sync, or graduation scripts. Do not touch
token.json.

## fbp-trade-bot

Local main is **1 commit ahead** of origin/main (`106e017`): `9ab7a0d` --
plain fast-forward, no merge needed, no conflicts possible.

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -3` -- confirm it's still at `106e017`
   (if it's moved forward since this was written, stop and flag it rather
   than merging/rebasing yourself).
3. If unchanged: `git push origin main`.
4. Verify: `git log --oneline origin/main -1` should show `9ab7a0d`.

**Do not force-push. Do not resolve any conflicts yourself** -- if
`git push` reports anything other than a clean push, stop and flag it.

### What's in this push

**Fix blank years_simple on 8 round-4+ keeper-draft picks** (commit
`9ab7a0d`). `draft/draft_manager.py`'s keeper-draft pick handler only sets
`years_simple` for rounds 1-3 (hard-coded "VC 1"); rounds 4+ have no
equivalent branch, so it was left blank on 8 players from the 2026-03-08
keeper draft. With `years_simple` blank, fbp-hub's rosters page and
Discord's `/trade`, `/lookup`, `/roster` commands all fell back to
displaying the literal `contract_type` string ("Keeper Contract") instead
of a real contract code -- Zach spotted this from a rosters-page
screenshot (Lars Nootbaar showing "Keeper Contract" instead of "TC 1").

Per Zach's ruling: a keeper-draft pick is a TC 1, full stop, regardless of
pre-draft tier (resolves two cases -- Hunter Greene, Luke Weaver -- that
had an intervening drop/re-add making their prior tier ambiguous). Sets
`years_simple="TC 1"` + `status="[5] TC1"` on upids 3170, 3265, 3840, 4022,
2916, 3457, 2872, 3726 via `scripts/fix_blank_years_simple_2026_08_03.py`
(guarded -- only touches a record still in the expected broken state).
Verified: JSON valid, player count unchanged (6,812), diff touches exactly
these 8 records' `years_simple`/`status` fields, zero unexpected diffs
elsewhere.

**Still flagged, not fixed, no action needed from you:**
- `draft_manager.py`'s round-4+ gap itself is NOT patched -- the next
  keeper draft will reproduce this same bug on any round-4+ pick unless
  someone adds the missing `else: years_simple = "TC 1"` branch. Zach
  hasn't given the go-ahead yet.
- `api_team_planner.py`'s save endpoint still has the fire-and-forget
  commit issue noted when it shipped (no `wait=True`, swallows commit
  failures with only a print).

**Everything else** (PTDA WizBucks allotment, Discord headshot
thumbnails, auction fix, trade backfills, data cleanse, Team Planner API)
is already on origin/main as of this push prompt -- nothing else pending.

## fbp-hub

**Nothing pending.** Local main and origin/main are identical
(`7986690`) -- no push needed right now. (Headshot avatars and the Team
Planner draft-picks/mobile-layout fix both already shipped.)

## After pushing

**fbp-trade-bot:** Railway will redeploy from the new main. This is a
data-only change (no code/runtime behavior touched) -- worth a quick look
at the rosters page or a `/lookup` on one of the 8 players above (e.g.
Lars Nootbaar) to confirm "TC 1" now shows instead of "Keeper Contract".
