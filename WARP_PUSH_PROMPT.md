# Push Prompt — fbp-trade-bot & fbp-hub

**Last updated: 2026-08-03 17:47 UTC.** Rewritten after every new batch of
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

Local main is **6 commits ahead** of origin/main (`106e017`): `9ab7a0d`,
`e7e4470`, `e3be5da`, `b441d4b`, `77e66b6`, `00d2e1d` -- plain
fast-forward, no merge needed, no conflicts possible.

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -3` -- confirm it's still at `106e017`
   (if it's moved forward since this was written, stop and flag it rather
   than merging/rebasing yourself).
3. If unchanged: `git push origin main`.
4. Verify: `git log --oneline origin/main -1` should match
   `git log --oneline HEAD -1` run right before the push.

**Do not force-push. Do not resolve any conflicts yourself** -- if
`git push` reports anything other than a clean push, stop and flag it.

### What's in this push

**1. Fix blank years_simple on 8 round-4+ keeper-draft picks + fix the
gap at the source** (`9ab7a0d`, doc follow-up `e7e4470`, source fix
`e3be5da`, doc follow-up `b441d4b`). `draft/draft_manager.py`'s
keeper-draft pick handler only ever set `years_simple` for rounds 1-3
(hard-coded "VC 1"); round 4+ had no branch, so it stayed blank on 8
players from the 2026-03-08 draft, which made fbp-hub's rosters page and
Discord's `/trade`/`/lookup`/`/roster` fall back to showing the literal
`contract_type` string ("Keeper Contract") instead of a real code --
Zach caught this from a rosters-page screenshot. Backfilled the 8 records
to `years_simple="TC 1"` / `status="[5] TC1"` (Zach's ruling: a
keeper-draft pick is a TC 1, full stop, regardless of pre-draft tier),
then patched `draft_manager.py` itself (added the missing round 4+
branch, plus now sets `status` in both branches, plus both undo paths
clear `status` for symmetry) so future keeper drafts can't reproduce it.
Verified via `py_compile` and a functional test against an isolated copy
of the data (not the real files).

**2. Add `division` field to `config/managers.json`** (`77e66b6`).
Stanky's Grave (DMN/B2J/WAR/RV), Charlie Hustle (LFB/HAM/TBB/DRO),
Colossus of Clout (SAD/WIZ/JEP/CFL) -- per Constitution Article 7,
matched from Zach's standings screenshot. Companion to the fbp-hub
standings commit below, which is what actually consumes this. Verified
identical division values across both repos' copies for all 12 teams.

**3. Add Friday/Saturday IP min reminder DM task** (`00d2e1d`). New
scheduled task DMs managers at risk of missing the 35 IP/week minimum:
Friday 9 AM ET projects full-week pace off IP through Thursday; Saturday
9 AM ET flags anyone still 6+ IP short with only Sunday left. State
persisted to `data/ip_min_reminder_state.json` (seeded `{}`) so a restart
same-day doesn't double-send. Verified `py_compile` clean, confirmed all
12 teams have `discord_id` set in managers.json.

**Still flagged, not fixed, no action needed from you:**
- `api_team_planner.py`'s save endpoint still has the fire-and-forget
  commit issue noted when it shipped (no `wait=True`, swallows commit
  failures with only a print).
- `ip_min_reminder_tick`'s state-file commit (item 3 above) has the same
  gap -- doesn't pass `wait=True`, so a failed/delayed push wouldn't be
  surfaced. Lower stakes than the other cases: the local write happens
  before the commit is queued, so the only real exposure is a duplicate
  DM if the container restarts same-day before the commit lands.

**Everything else** (PTDA WizBucks allotment, Discord headshot
thumbnails, auction fix, trade backfills, data cleanse, Team Planner API)
is already on origin/main as of this push prompt -- nothing else pending
from those.

## fbp-hub

Local main is **2 commits ahead** of origin/main (`7986690`): `9df945d`,
`04f012b` -- plain fast-forward, no merge needed, no conflicts possible.

Steps:
1. `cd` into fbp-hub, `git fetch origin`.
2. `git log --oneline origin/main -3` -- confirm it's still at `7986690`.
3. `git push origin main`.
4. Verify: `git log --oneline origin/main -1` should match
   `git log --oneline HEAD -1` run right before the push.

### What's in this push

**1. Standings: divisions, IP min color scale, GB, Leaders toggle**
(`9df945d`). Adds the same `division` field to this repo's
`config/managers.json` copy (companion to the fbp-trade-bot commit
above -- standings.html reads its own local copy) and wires up
`standings.html`:
- League/Divisional toggle, grouped by the real division field instead
  of a placeholder.
- Games Back column off the existing live win-pct/record data.
- IP Min color scale: red <25, yellow 25-34, green 35+.
- Leaders toggle (star button) bolds the league-wide best per category,
  with ERA/ER/P_HR/H-per-9/BB-per-9/P_TB correctly treated as
  lower-is-better -- confirmed these match `data/standings.json`'s actual
  `display_name` strings (including the "/" in "H/9"/"BB/9") before
  hardcoding, so the highlight can't silently no-op on a naming mismatch.

Verified: JSON valid, division values match fbp-trade-bot's copy exactly
for all 12 teams, all 3 inline `<script>` blocks in standings.html pass
`node --check`.

**2. Team Planner: BC Top 100 free-keep + 2027 allotment preview**
(`04f012b`). A BC-tier prospect flagged "Top 100 on Nov 1" (T100 toggle,
table + mobile card view) retains Blue-Chip for free at the next PAD per
Constitution Article 4 Sec 04.4 -- zeroes its cost, shows a green
FREE/FREE KEEP badge, drops out of the PAD spend total, persists with the
plan. Separately, a "Potential 2027 allotment" block in the KAP-mode
WizBucks section previews PAD+KAP by projected finish bracket
(Championship/Consolation/Elimination) straight from the constitution's
numbers (PAD base $100/$120/$140, KAP flat $375, Consolation +KAP bonus,
Elimination +PAD bonus, Championship no WB bonus) -- informational only,
same non-binding treatment as the existing draft-picks projection, also
persists with the plan.

Verified: `js/team-planner.js` passes `node --check`, bonus figures
checked against the constitution directly.

## After pushing

**fbp-trade-bot:** Railway will redeploy. This batch changes runtime
behavior (draft pick logic, new scheduled DM task) as well as data --
worth confirming the bot comes up clean, and a `/lookup` on one of the 8
backfilled players (e.g. Lars Nootbaar) shows "TC 1" now.

**fbp-hub:** confirm standings.html's new toggles/columns render, and
Team Planner's T100 toggle + allotment preview show up in both the
Prospect Plan and WizBucks sections (desktop + mobile).
