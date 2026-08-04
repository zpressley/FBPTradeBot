# Push Prompt — fbp-trade-bot & fbp-hub

**Last updated: 2026-08-04 21:16 UTC.** Rewritten after every new batch of
commits -- treat the hashes below as accurate only as of this timestamp.
Both repos get edited concurrently by more than one agent/session, so
always re-verify with `git log --oneline origin/main -3` before trusting
anything written here, including this file itself.

Push pending commits in both repos. Do not touch `token.json`. Do not
run any data pipeline, backfill, sync, or graduation script -- this task
is only to push what's already committed.

## fbp-trade-bot

Local main is **1 commit ahead** of origin/main (`19de9d5`): `9012bbd`.
Plain fast-forward, no conflicts possible.

**What's in it:** `POST /api/settings/site-theme` -- saves the user's
site-wide color theme choice. Mirrors the existing Team Colors save
route exactly (same auth, validation, git-commit-or-rollback pattern).
Writes to `data/site_theme.json`. Companion to the fbp-hub commit below
-- these two only make sense deployed together.

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -3` -- confirm it's still at `19de9d5`.
3. If unchanged: `git push origin main`.
4. Verify: `git log --oneline origin/main -1` matches `git log --oneline
   HEAD -1` from just before the push.

**Do not force-push. Do not resolve conflicts yourself** -- if `git
push` reports anything other than a clean push, stop and flag it.

## fbp-hub

Local main is 1 commit ahead (`58b0561`) but origin/main has **also**
moved -- 5 commits, all automated (cache-busting + the scheduled
data-sync bot), current tip `abfe63c`. This is a real divergence, not
the usual plain fast-forward, so the steps below differ from
fbp-trade-bot's.

Already checked so you don't have to guess: those 5 commits only touch
`data/*.json` and `?v=` cache-bust query strings on script/link tags.
Ran `git merge-tree` against `58b0561` -- **zero conflicts**, in every
file, including `settings.html` (both sides touch it, but on disjoint
line ranges: the cache-bust commit only rewrites the `?v=` strings near
the top/bottom, `58b0561`'s changes are new markup in the middle). A
plain rebase should go through with nothing to resolve.

**What's in it:** the Website Theme picker -- 5 color palettes replacing
the "Dark Mode Variants" placeholder in Settings. New CSS variables in
both `styles.css` (the main site) and `team-planner.css`/`team-builder.css`
(their own separate token system, kept in sync) so switching palettes
repaints everywhere. `applyTheme()`/`loadSiteTheme()` in `main.js`;
swatch-picker UI in `settings.js`/`settings.html`/`settings.css`. Saving
requires Discord login (same gate as Team Colors) and hits the
fbp-trade-bot endpoint above -- localStorage is just an instant-paint
cache, never the source of truth.

Steps:
1. `cd` into fbp-hub, `git fetch origin`.
2. `git rebase origin/main` -- verified clean above. If it reports an
   actual conflict anyway, stop and flag it rather than resolving it
   yourself.
3. `git push origin main`.
4. Verify: `git log --oneline origin/main -1` matches `git log --oneline
   HEAD -1` from just before the push.

**Do not force-push.**

## After pushing

**fbp-trade-bot:** Railway redeploys automatically.

**fbp-hub:** confirm Settings shows the 5-palette picker, and that
switching one repaints the whole site (check Team Planner specifically,
since it has its own CSS token system).

## For context, not action needed

The two live UPID collisions from Aug 3-4 (Ramon Marquez/Luis Garcia
Jr., then Ramon Marquez/Boston Smith) are confirmed already on
origin/main -- both resolved, root cause fixed, nothing pending from
either. The BC Top-100/PAD-KAP allotment preview and the ID-first
add-player lookup are also both already live. No action needed on any
of that.
