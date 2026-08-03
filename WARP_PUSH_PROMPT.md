Push pending commits in fbp-trade-bot AND fbp-hub. Both repos have already
been reconciled with their origins (see below) -- this task is ONLY to push
the results. Do not re-run, re-generate, or "fix" anything; do not run any
data pipeline, backfill, sync, or graduation scripts. Do not touch
token.json.

## fbp-trade-bot

Local main is a real merge commit (`2aa2862`) with two parents: your last
push and origin/main's current tip (`e598ad0`), plus six more commits on
top (`64dc74c`, `5510c93`, `1ac570f`, `8f9e9f0`, `435d9ea`, `d7d6715` --
docs, data-cleanse fixes, the new Team Planner API, and the PTDA WizBucks
allotment, see items 3-5 below). Local HEAD already contains every commit
that's on origin/main -- this should push as a plain, non-force
fast-forward-compatible push with no conflicts.

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -3` -- confirm it's still at `e598ad0`
   (if it has moved further forward, e.g. another daily update or live
   admin action landed since this was written, stop and let me know rather
   than merging/rebasing again yourself -- ping Zach or come back to me).
3. If unchanged: `git push origin main`. No merge/rebase needed -- it's
   already done, locally, and verified.
4. Verify: `git log --oneline origin/main -1` should show `d7d6715`.

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

**3. Data cleanse + two follow-up fixes (commits `64dc74c`, `5510c93`,
`1ac570f`, `8f9e9f0`).** `scripts/data_cleanse_combined_players.py` scans
`combined_players.json` for the failure patterns this session kept hitting
(ID collisions, missing FBP_Team, duplicate rows, etc.);
`DATA_CLEANSE_COMBINED_PLAYERS_2026_08_02.md` is the writeup, now updated
to reflect what's fixed vs. still open.

(`5510c93` corrects an initial mistake in the same report: a "27 owned
players stuck in Farm status" finding that assumed graduation is driven by
having debuted. Zach corrected this -- graduation is rule-based (350 PA /
100 IP-30 G / age 26+, per the FBP Constitution), not debut-based. Verified
against `data/graduation_eligible.json`'s properly-computed eligibility
snapshot: zero actual backlog. That finding is retracted in the doc and the
script's check was rewritten to use the real rule.)

`1ac570f` fixes two of the cleanse's findings, both scoped and confirmed
with Zach before applying:
- **Duplicate array rows** (upid 5996, 3825): each had two objects sharing
  one upid -- a full record and a sparse one carrying only
  bbref_id/fangraphs_id/fangraphs_name under an accented name spelling.
  Merged into one row each.
- **6 no-UPID stub records** (Ivan Herrera, Josh Smith, Luis Robert Jr.,
  Michael Harris II, Bobby Witt Jr. -- all owned; Jake Odorizzi --
  unowned): traced to a single bulk-drop event on 2026-03-13 (all 5 owned
  players dropped at the identical timestamp) followed by each being
  re-added by the *same* manager who'd dropped them, where the re-add's
  name-match failed and created a disconnected ownership stub instead of
  re-linking to the original UPID. Fixed by transplanting FBP_Team/manager
  onto the original rich record and deleting the stub -- contract terms
  deliberately left unchanged (Zach's call: same-manager reclaim right
  after a forced drop reads as a sync glitch, not a new pickup).

Verified before committing: player count 6,820 -> 6,812 (exactly the 2
merged duplicates + 6 removed stubs), JSON valid, zero unexpected diffs
among the other 6,804 players.

Still open, not fixed, no action needed from you: a couple more dormant
shadow-duplicate players (Jonathon Long, Abimelec Ortiz) and some low
priority cosmetic items -- all listed in the doc, none blocking.

**4. Team Planner save/load API** (`api_team_planner.py`, `health.py`,
commit `435d9ea` -- companion to the fbp-hub Team Planner push below, same
feature, other half of it). Two endpoints:
- `GET /api/team-planner/{team}` -- fetch a team's saved plan(s)
- `POST /api/team-planner/save` -- upsert one mode ('kap' or 'pad') of a
  team's plan

Gated only by the shared `X-API-Key` (the same key the Cloudflare Worker
injects site-wide) -- no per-manager ownership check, by design (Team
Planner has no login wall, matching Team Builder's existing access
pattern; documented in fbp-hub's `docs/TRADE_PLANNER_PLAN.md` section 3.2).
Any caller can save/load any team's plan. This is intentional, not a bug.

**Flagging, not fixed:** the save endpoint calls its commit function
directly without `wait=True`, and its failure handler only prints a
warning rather than raising -- so a failed commit still returns
`{"success": true}` to the caller. This is the same fire-and-forget defect
class this session spent a lot of effort finding and fixing elsewhere
(`trade_store.py`, `api_admin_bulk.py`, `commands/auction.py`): a saved
plan could report success and then vanish if the container redeploys
before the queued commit lands. Lower stakes than the other cases (a lost
planning draft, not a lost trade or roster move), but worth Zach knowing
before this ships. Not changed here -- his call on timing.

`data/team_planner_plans.json` seeded as `{}`. `health.py` diff is
additive only (new import, new router include, new commit-fn wiring in a
try/except matching the existing pattern for every other router) --
nothing existing was touched.

**5. 2026 Post-Trade Deadline Allotment (PTDA)** (`data/wizbucks.json`,
`data/wizbucks_transactions.json`, `scripts/apply_ptda_2026_08_02.py`,
commit `d7d6715`). Per FBP Constitution Article I Sec 02 (Installment 4 --
PTDA, based on current bracket), credited all 12 teams per bracket tier
Zach gave: Championship ($15) LFB/SAD/DMN/HAM, Consolation ($25)
WIZ/TBB/JEP/B2J, Elimination ($35) CFL/WAR/RV/DRO. Recorded as
`transaction_type: "admin_adjustment"` ledger entries (Zach's instruction),
matching the existing admin_adjustment schema exactly.

Script is guarded (validates the 12 bracket assignments exactly match
wizbucks.json's team set before writing anything) and idempotent (skips
any team that already has a PTDA-marked admin_adjustment entry, so a
re-run is safe). Verified before committing: both files valid JSON, all 12
new balances = old + tier amount, diff on the transactions file is a pure
append (12 new entries, zero existing entries altered) -- confirmed the
file's real on-disk convention is escaped-unicode
(`ensure_ascii=True`) empirically against HEAD before writing, so no
spurious reformatting of the other 254 entries.

## fbp-hub

Local main is one commit (`99f10fd`) ahead of origin/main (`5fea0c2`) --
plain fast-forward, no merge needed, no conflicts possible.

Steps:
1. `cd` into fbp-hub, `git fetch origin`.
2. `git log --oneline origin/main -3` -- confirm it's still at `5fea0c2`.
   If it's moved forward (auto-sync commits land here regularly), same
   rule as fbp-trade-bot: plain fast-forward should still work since
   nothing else touched these files, but if `git push` is rejected, stop
   and flag it rather than force-pushing or improvising a resolution.
3. `git push origin main`.
4. Verify: `git log --oneline origin/main -1` should show `99f10fd`.

### What's in this push

**Team Planner, replacing Team Builder in navigation** (commit `99f10fd`).
New page (`team-planner.html`, `css/team-planner.css`, `js/team-planner.js`)
plus a design doc (`docs/TRADE_PLANNER_PLAN.md`). Two modes (KAP/PAD), no
login wall by design, Save Plan persists cross-device via the new
fbp-trade-bot endpoints above.

Wired into all 4 places Team Builder previously appeared: the Front Office
dropdown (20 pages + team-builder.html itself -- identical 2-line swap in
each, verified via `git diff --numstat`: every one of the 20 plain pages is
exactly 2 insertions/2 deletions), `js/main.js`'s nav-highlighting array +
a new `initializePage()` case, `js/dashboard-tabs.js`'s quick-action tile,
and a brand-new entry in `js/auth.js`'s post-login dropdown (Team Planner
wasn't in that menu before -- new addition, not a swap). `team-builder.html`
itself is untouched otherwise (title/header still say Team Builder) -- the
page still exists and works, just isn't linked from anywhere anymore.

Diff verified before committing: 24 files changed, 53 insertions / 44
deletions total, matching exactly what was reviewed before commit -- no
stray changes.

## After pushing

**fbp-trade-bot:** Railway will redeploy from the new main. This changes
runtime behavior (the auction fix, the new Team Planner endpoints), not
just data -- worth a quick look at the first few log lines after restart
to confirm the bot comes up clean, and particularly worth watching the
next Sunday auction resolve to confirm no more persistence-warning
messages.

**fbp-hub:** this is a static site (GitHub Pages / Cloudflare) -- confirm
the deploy picks up cleanly and `team-planner.html` actually loads once
live, since it depends on the fbp-trade-bot endpoints above being deployed
too. Push fbp-trade-bot first if doing these one at a time, so the API
exists before the page that calls it goes live.
