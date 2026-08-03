# Push Prompt — fbp-trade-bot & fbp-hub

**Last updated: 2026-08-03 17:20 UTC.** Rewritten after every new batch of
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

Local main is **3 commits ahead** of origin/main (`106e017`): `9ab7a0d`,
`e7e4470`, `e3be5da` -- plain fast-forward, no merge needed, no conflicts
possible.

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -3` -- confirm it's still at `106e017`
   (if it's moved forward since this was written, stop and flag it rather
   than merging/rebasing yourself).
3. If unchanged: `git push origin main`.
4. Verify: `git log --oneline origin/main -1` should match
   `git log --oneline HEAD -1` run right before the push (i.e. local and
   origin end up identical) -- don't rely on a hardcoded hash here, this
   doc's own update commit lands on top of everything below it.

**Do not force-push. Do not resolve any conflicts yourself** -- if
`git push` reports anything other than a clean push, stop and flag it.

### What's in this push

**1. Fix blank years_simple on 8 round-4+ keeper-draft picks** (commit
`9ab7a0d`, doc-only follow-up `e7e4470`). `draft/draft_manager.py`'s
keeper-draft pick handler only set `years_simple` for rounds 1-3
(hard-coded "VC 1"); rounds 4+ had no equivalent branch, so it was left
blank on 8 players from the 2026-03-08 keeper draft. With `years_simple`
blank, fbp-hub's rosters page and Discord's `/trade`, `/lookup`, `/roster`
commands all fell back to displaying the literal `contract_type` string
("Keeper Contract") instead of a real contract code -- Zach spotted this
from a rosters-page screenshot (Lars Nootbaar showing "Keeper Contract"
instead of "TC 1").

Per Zach's ruling: a keeper-draft pick is a TC 1, full stop, regardless of
pre-draft tier (resolves two cases -- Hunter Greene, Luke Weaver -- that
had an intervening drop/re-add making their prior tier ambiguous). Sets
`years_simple="TC 1"` + `status="[5] TC1"` on upids 3170, 3265, 3840, 4022,
2916, 3457, 2872, 3726 via `scripts/fix_blank_years_simple_2026_08_03.py`
(guarded -- only touches a record still in the expected broken state).
Verified: JSON valid, player count unchanged (6,812), diff touches exactly
these 8 records' `years_simple`/`status` fields, zero unexpected diffs
elsewhere.

**2. Fix the `draft_manager.py` gap at the source** (commit `e3be5da`).
Added the missing round-4+ branch (`years_simple = "TC 1"`) so future
keeper drafts can't reproduce the bug above. Also now sets `status`
alongside `years_simple` in both the round<=3 and round 4+ branches
("VC 1" -> "[3] VC1", "TC 1" -> "[5] TC1", matching the dominant real
pairing for each tier) -- previously `status` was left blank here
regardless of round, which only didn't matter because `years_simple` wins
the frontend's display fallback; closed both so the same class of gap
can't resurface elsewhere. For symmetry, the two undo paths
(`_clear_pick_from_rosters`, `reset_to_pick_one`) now also clear `status`
alongside `years_simple`/`contract_type`, so undoing a pick doesn't leave
a stale status behind.

Verified: `py_compile` clean. Functionally tested against an isolated
temp copy of the data (not the real files) by calling
`_apply_pick_to_rosters` directly for a synthetic round-5 pick (correctly
produced `years_simple="TC 1"`, `status="[5] TC1"`) and a round-2 pick
(unchanged behavior: `years_simple="VC 1"`, `status="[3] VC1"`, confirming
no regression), then ran `_clear_pick_from_rosters` on the round-5 pick to
confirm the rollback clears `status` too. This only touches
`draft/draft_manager.py` -- no data files changed in this commit.

**Still flagged, not fixed, no action needed from you:**
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
