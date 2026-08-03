# Push Prompt — fbp-trade-bot & fbp-hub

**Last updated: 2026-08-03 16:35 UTC.** This gets rewritten after every new
batch of local commits, so the "ahead of origin" commit lists below should
always be current as of this timestamp. If it's more than a day or two old
when you're reading it, treat the hashes as suspect and re-check
`git log --oneline origin/main -3` yourself before pushing.

Push pending commits in fbp-trade-bot AND fbp-hub. Both repos have already
been reconciled with their origins (see below) -- this task is ONLY to push
the results. Do not re-run, re-generate, or "fix" anything; do not run any
data pipeline, backfill, sync, or graduation scripts. Do not touch
token.json.

## fbp-trade-bot

Local main is **3 commits ahead** of origin/main (`c59de9c`): `d7d6715`,
`e52ae43`, `6140dd8` -- plain fast-forward, no merge needed, no conflicts
possible.

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -3` -- confirm it's still at `c59de9c`
   (if it's moved forward since this was written, stop and flag it rather
   than merging/rebasing yourself -- ping Zach or come back to me).
3. If unchanged: `git push origin main`.
4. Verify: `git log --oneline origin/main -1` should show `6140dd8`.

**Do not force-push. Do not resolve any conflicts yourself** -- if
`git push` reports anything other than a clean push, stop and flag it.

### What's in this push

**1. 2026 Post-Trade Deadline Allotment (PTDA)** (`data/wizbucks.json`,
`data/wizbucks_transactions.json`, `scripts/apply_ptda_2026_08_02.py`,
commit `d7d6715`; doc-only follow-up `e52ae43`). Per FBP Constitution
Article I Sec 02 (Installment 4 -- PTDA, based on current bracket),
credited all 12 teams per the bracket tiers Zach gave: Championship ($15)
LFB/SAD/DMN/HAM, Consolation ($25) WIZ/TBB/JEP/B2J, Elimination ($35)
CFL/WAR/RV/DRO. Recorded as `transaction_type: "admin_adjustment"` ledger
entries per Zach's instruction, matching the existing admin_adjustment
schema exactly. Script is guarded (validates the 12 bracket assignments
match wizbucks.json's team set) and idempotent (skips any team with an
existing PTDA-marked entry, safe to re-run). Verified before committing:
both files valid JSON, all 12 new balances = old + tier amount, diff on
the transactions file is a pure append (12 new entries, zero existing
entries altered -- matched the file's real on-disk convention,
`ensure_ascii=True`/escaped-unicode, confirmed empirically against HEAD
rather than assumed).

**2. MLB headshot thumbnails in Discord `/player`** (`commands/lookup.py`,
`commands/player.py`, `commands/utils.py`, commit `6140dd8`) -- companion
to the fbp-hub avatar feature below, same CDN pattern. New
`mlb_headshot_url()` helper in `commands/utils.py` builds an
img.mlbstatic.com URL keyed off `mlb_id`. `/player` lookup replies switched
from plain text to a `discord.Embed` with the headshot as a thumbnail, for
both the single-match and closest-match cases; falls back to no thumbnail
when a player has no `mlb_id`. Verified: `python3 -m py_compile` clean on
all 3 changed files, diff reviewed against the commit.

**Previously shipped, already on origin (for reference only, not part of
this push):** auction persistence deadlock fix (`fe8096a`), 4 backfilled
trades + Luis Garcia Jr. identity fix (`e2d1deb`, `6640b44`), data cleanse
+ duplicate-row/stub fixes (`64dc74c`, `5510c93`, `1ac570f`, `8f9e9f0`),
Team Planner save/load API (`435d9ea`). Full writeups for these live in
git history if ever needed again -- trimmed from this doc since they're
no longer actionable.

**Still flagged, not fixed, no action needed from you:** `api_team_planner.py`'s
save endpoint still has the fire-and-forget commit issue noted when it
shipped (calls its commit function without `wait=True`, swallows failures
with only a print) -- Zach hasn't given the go-ahead to harden it yet.

## fbp-hub

Local main is **2 commits ahead** of origin/main (`08eb149`): `f069b69`,
`29a083e` -- plain fast-forward, no merge needed, no conflicts possible.

Steps:
1. `cd` into fbp-hub, `git fetch origin`.
2. `git log --oneline origin/main -3` -- confirm it's still at `08eb149`.
   If it's moved forward (auto-sync commits land here regularly), same
   rule as fbp-trade-bot: should still fast-forward cleanly, but if
   `git push` is rejected, stop and flag it rather than improvising a fix.
3. `git push origin main`.
4. Verify: `git log --oneline origin/main -1` should show `29a083e`.

### What's in this push

**1. Player headshot avatars** (commit `f069b69`) -- companion to the
fbp-trade-bot Discord embed above; sourced from MLB's public headshot CDN
using the `mlb_id` already in `combined_players.json`, no new data pipeline
needed. Core: one reusable helper pair in `js/main.js`
(`getPlayerPhotoUrl()` + `createPlayerAvatarHTML()`, plus
`handlePlayerPhotoError()`) that builds the image URL and falls back to a
local SVG silhouette if a player has no `mlb_id` or the photo fails to
load; everything else calls this. Wired into: player profile page (full
photo -- UI already existed, just wasn't connected to a real image
source), player database (thumbnail per row + bigger photo in the
slide-out detail panel), rosters (avatar next to every name, keeper and
prospect tables both), trade builder (avatar on players added to a trade
and in the player-picker modal). Coverage: 99.6% of MLB keepers have a
usable ID, ~52% of prospects do -- everyone else shows the silhouette
until MLB issues them one. Deliberately deferred: the dashboard's "My
Roster" widget runs through a separate `lineup-builder.js` module,
different enough to leave out for now -- helper's already built, quick
add later if wanted. Verified: `node --check` clean on all 5 changed JS
files (`main.js`, `player-profile.js`, `players.js`, `rosters.js`,
`trade.js`).

**2. Team Planner draft-picks fix + mobile-compact layout** (commit
`29a083e`). The picks grid was reading `data/draft_order_2026.json`, which
is the already-executed 2026 keeper draft built from 2025's final
standings -- not usable for projecting 2027 picks, since next year's order
is bracket-routed (championship/consolation/elimination), not
reverse-standings, and can't be fully known until playoffs resolve. Now
generates one placeholder pick per round (29 rounds), no order/pick-number
shown, plus a "model trading this away" drop toggle and a simplified
hypothetical add-pick picker (round + from-team). Also adds a mobile-only
accordion/card-list view (Roster Plan, Draft Picks, WizBucks Adjustment,
Prospect Plan, Draft Slots, plus a sticky summary bar reusing kap.js's
existing sticky-bar/debounce pattern) so the page needs less scrolling on
phones -- desktop keeps the original table layout, both share the same
`TP_STATE` and calculation functions, split purely by CSS media query.
Verified: `node --check` clean on `js/team-planner.js`.

**Previously shipped, already on origin (for reference only):** Team
Planner base build, replacing Team Builder in navigation (`08eb149`).

## After pushing

**fbp-trade-bot:** Railway will redeploy from the new main. This changes
Discord bot behavior (the `/player` embed), not just data -- worth a quick
look at the first `/player` lookup after restart to confirm the thumbnail
renders.

**fbp-hub:** static site (GitHub Pages / Cloudflare) -- confirm the deploy
picks up cleanly and headshots actually render on the players page, a
roster page, and the trade builder once live. Team Planner's draft-picks
tab and the mobile layout (resize below ~768px, or check on an actual
phone) are also worth a quick look.
