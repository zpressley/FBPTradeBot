Push pending commits in fbp-trade-bot. Local main has already been merged with
origin/main (see below) -- this task is ONLY to push the result. Do not
re-run, re-generate, or "fix" anything; do not run any data pipeline,
backfill, sync, or graduation scripts. Do not touch token.json.

fbp-hub has nothing pending right now.

## fbp-trade-bot

Local main is a real merge commit (`2aa2862`) with two parents: your last
push and origin/main's current tip (`e598ad0`). Local HEAD already contains
every commit that's on origin/main -- this should push as a plain,
non-force fast-forward-compatible push with no conflicts.

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -3` -- confirm it's still at `e598ad0`
   (if it has moved further forward, e.g. another daily update or live
   admin action landed since this was written, stop and let me know rather
   than merging/rebasing again yourself -- ping Zach or come back to me).
3. If unchanged: `git push origin main`. No merge/rebase needed -- it's
   already done, locally, and verified.
4. Verify: `git log --oneline origin/main -1` should show `2aa2862`.

**Do not force-push. Do not run `git rebase -X ours/-X theirs`. Do not
resolve any conflicts yourself** -- if `git push` reports anything other
than a clean push (e.g. origin moved forward again and it's rejected),
stop and flag it rather than improvising a resolution; this branch has
already had two rounds of careful manual reconciliation this session and a
third blind one is exactly what would cause real damage to live roster
data.

### What's in this push and why

**1. Auction persistence deadlock fix** (`commands/auction.py`, `health.py`,
commit `fe8096a`). Root cause of the recurring "auction bot hit a
persistence issue, then recovered" Discord messages: three places in the
Sunday auction-resolve tick called a blocking git-commit function
(`wait=True`) *while still holding* `DATA_LOCK`. When a push needs conflict
recovery, that recovery path also needs `DATA_LOCK` -- but the commit-worker
thread could never get it, because auction_tick (a different thread) was
sitting on the lock waiting for that same worker thread. Cross-thread
deadlock, not a flaky push -- it "resolves" only once the blocking wait
times out and releases the lock. Fixed by moving the blocking commit calls
to run *after* `DATA_LOCK` is released, matching the pattern already used
in `trade_store.py` and `api_admin_bulk.py`.

**2. Four backfilled trades + Luis Garcia Jr. identity fix** (commits
`e2d1deb`, `6640b44`, folded into merge `2aa2862`). Warp's own 7/31
investigation (`TRADE_DATA_ISSUES_2026_07_31.md` /
`TRADE_DATA_ISSUES_ROOT_CAUSE_FIXES_2026_07_31.md`) found 4 real trades that
never reached the portal (Caminiti/Miller, McGreevy, Garcia Jr./Hall/
Cunningham, Ryan/Roupp/Pratt) plus a yahoo_id collision (9455 shared across
3 unrelated players). That fix was correct but built against a stale local
base and, critically, didn't know about UPID 8697.

**UPID 8697 backstory:** Zach re-added Luis Garcia Jr. via the live admin
portal on 7/30 evening, not realizing UPID 8696 (my earlier recovery of his
*original* lost add) already existed -- 8696 had no `FBP_Team` key and
looked broken/unowned in the UI. 8697 has correct WAR ownership, a real
yahoo_id (10964), and was confirmed the next morning by the Yahoo roster
sync. The WAR->WIZ trade in Warp's fix had moved 8696 (the orphaned
duplicate) since its investigation never saw 8697.
`scripts/fix_garcia_jr_canonical_upid_2026_08_02.py` corrects this: retires
8696 (cleared ownership fields, flagged `approved_dupes: TRUE`), applies the
real trade to 8697, and repoints the trade record accordingly. Full
reasoning is in that script's docstring and in commit `6640b44`'s message.

**Two items from Warp's root-cause report are deliberately NOT included,
pending Zach's sign-off** (see `TRADE_DATA_ISSUES_ROOT_CAUSE_FIXES_2026_07_31.md`):
the time-of-day trade-deadline logic change, and the broader fix-priority
list (yahoo_id uniqueness guard, identity integrity checker, commissioner
manual-trade-entry UI, `trade_store.py` DATA_LOCK/exception hardening,
McGreevy-style graduation-gap sweep).

Verification done before merging: JSON-validity on all 5 touched data
files, all ~13 touched player records checked against expected values,
zero unexpected diffs among the other 6,807 players vs. origin/main's tip,
player count unchanged (6,820), and `python3 -m py_compile` clean on every
changed/new `.py` file.

## After pushing

Railway will redeploy fbp-trade-bot from the new main. This changes runtime
behavior (the auction fix), not just data -- worth a quick look at the
first few log lines after restart to confirm the bot comes up clean, and
particularly worth watching the next Sunday auction resolve to confirm no
more persistence-warning messages.
