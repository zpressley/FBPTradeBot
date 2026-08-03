# Push Prompt — fbp-trade-bot & fbp-hub

**Last updated: 2026-08-03 19:44 UTC.** Rewritten after every new batch of
local commits -- the "ahead of origin" counts/hashes below are accurate as
of this timestamp. If it's more than a day or two old, don't trust the
hashes -- re-run `git log --oneline origin/main -3` yourself first. Local
history in both repos has been rebased onto origin more than once this
week as pushes landed out-of-band (from Zach directly, or another agent
with real push access), so old commit hashes mentioned in chat/docs may no
longer exist even though the content is already live -- always check
origin/main's actual tip, not a remembered hash.

Push pending commits in fbp-trade-bot AND fbp-hub. Both repos have already
been reconciled with their origins (see below) -- this task is ONLY to push
the results. Do not re-run, re-generate, or "fix" anything; do not run any
data pipeline, backfill, sync, or graduation scripts. Do not touch
token.json.

## fbp-trade-bot

Local main is **1 commit ahead** of origin/main (`ce73895`): `bb47107` --
plain fast-forward, no merge needed, no conflicts possible.

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -3` -- confirm it's still at `ce73895`
   (if it's moved forward since this was written, stop and flag it rather
   than merging/rebasing yourself).
3. If unchanged: `git push origin main`.
4. Verify: `git log --oneline origin/main -1` should match
   `git log --oneline HEAD -1` run right before the push.

**Do not force-push. Do not resolve any conflicts yourself** -- if
`git push` reports anything other than a clean push, stop and flag it.

### What's in this push

**Add ID-first player lookup/enrichment (MLB ID / Yahoo ID)** (`bb47107`).
Prompted by Ramon Marquez being added via the admin tool with the old
name-only flow. New shared module `mlb_lookup.py`:
`fetch_player_by_mlb_id()` is an exact `GET /people/{id}` call against the
MLB Stats API -- unambiguous, unlike a name search, which can match the
wrong same-named player or miss on an accent/suffix mismatch (the same
failure mode behind the Luis Garcia Jr. duplicate mess earlier this
session). `fetch_player_by_name()` is the pre-existing name-search
fallback, moved here so it's no longer duplicated field-for-field across
`api_admin_bulk.py` and `api_manager_players.py`. `enrich_player_data()`
is the single entry point both files now call: prefers `mlb_id` when
given, falls back to name search.

No `yahoo_id` lookup: there's no proven single-ID JSON bio endpoint for
Yahoo's Fantasy API in this codebase, and building one would mean invoking
the OAuth token flow (which can rewrite `token.json`) just to test it. A
`yahoo_id` is accepted and stored as a plain identifier, not
auto-enriched -- worth revisiting later if wanted.

Companion to two fbp-hub commits below (admin tool + players-page Add
Player, same feature, other half of it):
- `POST /api/admin/enrich-player` now accepts an optional `mlb_id` and
  prefers it over the name search.
- `api_manager_players.py`'s manager add-player-request flow: enrichment
  now passes `mlb_id` through; added `_find_duplicate_by_ids()` so a
  request also gets checked against `combined_players.json` by
  `mlb_id`/`yahoo_id`, not just by name (surfaced on the Discord review
  card); **Proof URL is no longer unconditionally required** -- an MLB ID
  or Yahoo ID now counts as proof on its own (Zach's call), Proof URL
  still accepted/validated if given.

Verified: `py_compile` clean on all 3 files. Live-testing against the real
MLB Stats API isn't possible from this sandbox (outbound HTTPS is
proxy-blocked here, same restriction that blocks git push/fetch) --
instead unit-tested with a mocked response matching the exact schema
already relied on elsewhere in this codebase: correct field mapping,
correct short-circuit on a non-numeric ID (no HTTP call attempted),
correct fallback to name search when an ID doesn't resolve.

**Everything else** (PTDA WizBucks allotment, Discord headshot
thumbnails, draft_manager.py fix, division field, IP min reminder task,
auction fix, trade backfills, data cleanse, Team Planner API) is already
on origin/main as of this push prompt -- nothing else pending from those.

## fbp-hub

Local main is **2 commits ahead** of origin/main (`6c995ac`): `bc4056f`,
`48ce3df` -- plain fast-forward, no merge needed, no conflicts possible.

Steps:
1. `cd` into fbp-hub, `git fetch origin`.
2. `git log --oneline origin/main -3` -- confirm it's still at `6c995ac`.
   If it's moved forward (another agent/Zach has been pushing directly to
   this repo too -- see the standings.html commit already at that tip),
   same rule as fbp-trade-bot: should still fast-forward cleanly, but if
   `git push` is rejected, stop and flag it rather than improvising a fix.
3. `git push origin main`.
4. Verify: `git log --oneline origin/main -1` should match
   `git log --oneline HEAD -1` run right before the push.

### What's in this push

**1. Admin add-player tool: ask for MLB/Yahoo ID first** (`bc4056f`).
New "Identify the Player" section at the top of the Add Player modal
(MLB ID + Yahoo ID). The existing "Auto-Fill from APIs" button (relabeled
"Look Up Player") now prefers the MLB ID field when filled in, and also
auto-fills the Name field itself from the result if it's empty (an add
can now start from just an ID, with no name typed yet). Extended the
existing name-only duplicate-check with a new ID-based one, wired to the
MLB ID / Yahoo ID fields directly. The MLB ID/Yahoo ID inputs were moved
up from Advanced Fields rather than duplicated -- same element IDs, so
the submit handler's existing field reads are unaffected.

**2. Simplify players-page Add Player to name + MLB/Yahoo ID** (`48ce3df`).
Per Zach: managers should just identify the player and let the system
find/fill in the rest -- the add-player-request modal is trimmed to
Name*, MLB ID, Yahoo ID, and Proof URL (no longer marked required in the
UI -- an MLB/Yahoo ID counts on its own now, matching the backend change
above). Team/position/age/bio fields are no longer asked for up front;
the backend's enrichment step already fills those in automatically, and
the existing edit-player flow covers anything left over.

Verified: `node --check` clean on both changed JS files. Grepped for
every removed field's old element ID and confirmed zero remaining
references. Confirmed the shared module's edit-player modal and
player-profile.html (which only uses the edit half) are unaffected.

**Everything else** (headshot avatars, Team Planner base + draft-picks
fix + BC Top 100/allotment preview, standings.html's division/GB/IP-color/
Leaders-toggle/two-color-category-leaders work) is already on
origin/main as of this push prompt -- nothing else pending from those.

## After pushing

**fbp-trade-bot:** Railway will redeploy. Data-only + one new module, no
existing runtime behavior changed -- low risk. Worth a quick look at
`/api/admin/enrich-player` (or just the admin add-player modal end to
end) with a real MLB ID to confirm the live lookup actually resolves --
this couldn't be tested from the sandbox that built it.

**fbp-hub:** confirm the admin Add Player modal shows the new "Identify
the Player" section, and that the players-page Add Player button now
shows the trimmed form (Name/MLB ID/Yahoo ID/Proof URL only).
