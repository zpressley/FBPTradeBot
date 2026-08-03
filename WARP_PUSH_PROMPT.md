# Push Prompt — fbp-trade-bot & fbp-hub

**Last updated: 2026-08-03 21:45 UTC.** Rewritten after every new batch of
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

Local main is **2 commits ahead** of origin/main (`ae714f8`): `6e449c5`,
`5acf83b` -- plain fast-forward, no merge needed, no conflicts possible.

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -3` -- confirm it's still at `ae714f8`
   (if it's moved forward since this was written, stop and flag it rather
   than merging/rebasing yourself).
3. If unchanged: `git push origin main`.
4. Verify: `git log --oneline origin/main -1` should match
   `git log --oneline HEAD -1` run right before the push.

**Do not force-push. Do not resolve any conflicts yourself** -- if
`git push` reports anything other than a clean push, stop and flag it.

### What's in this push

**Fix a live UPID collision + its root cause (`6e449c5`, `5acf83b`).**
Zach added a new prospect, Ramon Marquez, through the admin Add Player
tool on 2026-08-03. He was assigned UPID 8697 -- already held by Luis
Garcia Jr. -- so `combined_players.json` ended up with two different
players sharing one UPID. Any lookup that builds a `{upid: player}` map
from that file silently keeps whichever row comes later and drops the
other, which is almost certainly why the live site announced the one
open auction bid on this UPID as being on Luis Garcia Jr. instead of the
prospect it was actually meant for.

Root cause (`6e449c5`): three separate places generated "the next free
UPID" by scanning only `upid_database.json`'s `by_upid` dict -- never
checking the UPIDs actually present in `combined_players.json`.
`upid_database.json` had drifted out of sync (missing a `by_upid` entry
for 8697 even though Luis Garcia Jr. already held it), so none of the
three saw a collision coming. Added one shared, correctly-guarded
`api_upid.get_next_free_upid()` that checks both files; `add_player()`
(`api_admin_bulk.py`), the manager add-player-request approval path
(`api_manager_players.py`), and `api_upid.py`'s own record-creation
endpoint all now call it instead of their own copy of the old logic.

Live-data fix (`5acf83b`, `scripts/fix_upid_8697_collision_2026_08_03.py`,
already run against this repo's data -- re-running it is a safe no-op,
it checks current state before touching anything): Ramon Marquez moved
to UPID 8698 (confirmed free); `upid_database.json` restored to describe
Luis Garcia Jr. under 8697 and Marquez under 8698; the one open auction
bid's `prospect_id` repointed from 8697 to 8698 (the auction is still in
the unresolved "ob_window" phase, so nothing had actually changed
ownership yet -- this was a clean fix); one new correction entry appended
to `player_log.json`. Luis Garcia Jr.'s own player record was never
altered, only the identity-index entry that had been overwritten out
from under him.

**Not fixed by this push, flagging for awareness:** the Discord message
the live bot already posted describing this bid as being on Luis Garcia
Jr. -- there's no stored log of bot messages to correct/retract from
here; if that needs cleaning up, it's a manual Discord action only
Zach/Warp can take.

**Everything else** (PTDA WizBucks allotment, Discord headshot
thumbnails, draft_manager.py fix, division field, IP min reminder task,
auction fix, trade backfills, data cleanse, Team Planner API, ID-first
player lookup/enrichment) is already on origin/main as of this push
prompt -- nothing else pending from those.

## fbp-hub

Local main is **2 commits ahead** of origin/main (`6c995ac`): `bc4056f`,
`48ce3df` -- plain fast-forward, no merge needed, no conflicts possible.
(Unchanged since the last push prompt -- no new fbp-hub work this round.)

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

**fbp-trade-bot:** Railway will redeploy. Data-only + a shared-helper
refactor of code already live in production behind the same endpoints --
low risk, but see the one-line spot-check below.

**fbp-hub:** confirm the admin Add Player modal shows the new "Identify
the Player" section, and that the players-page Add Player button now
shows the trimmed form (Name/MLB ID/Yahoo ID/Proof URL only).

## Spot-check after this deploy (UPID collision fix)

Add a brand-new player through the admin tool (a throwaway, e.g. any
random minor-leaguer by MLB ID) and confirm the UPID it's assigned
doesn't already belong to someone else -- check the new player's UPID
against `combined_players.json` (should appear exactly once) and against
`upid_database.json`'s `by_upid` (should now include that UPID). Delete
the throwaway afterward via the existing admin delete-player flow. This
isn't expected to fail -- `get_next_free_upid()` is unit-tested against
the exact collision scenario -- but this bug already happened once on
live data, so a real add-and-check is worth the 2 minutes.

## Verifying the ID lookup actually works (do this once, post-deploy)

The whole point of this push is a live MLB Stats API call, and it could
only be built against a **mocked** response in the sandbox that wrote it
-- outbound HTTPS is proxy-blocked there, the same restriction that
blocks git push/fetch from that sandbox. So the mapping/parsing logic is
unit-tested, but the real end-to-end call has never actually run. Please
run at least Test 1 and Test 2 below once this is live; 3 and 4 are
optional extra confidence.

Test subject throughout: **Mike Trout, MLB ID `545361`** -- already in
`combined_players.json` (owned by Whiz Kids), which makes him useful for
checking the new duplicate-detection too, not just the lookup itself.

**Test 1 -- raw MLB API sanity check (no auth needed, run from anywhere
with real internet access):**
```
curl -s "https://statsapi.mlb.com/api/v1/people/545361?hydrate=currentTeam" | python3 -m json.tool
```
Expect a `people` array with one entry: `fullName: "Mike Trout"`,
`currentTeam.abbreviation: "LAA"`, `primaryPosition.abbreviation: "CF"`,
plus `birthDate`/`mlbDebutDate`/`batSide.code`/`pitchHand.code`/
`currentAge`. If this fails, or any of those field names have changed,
`mlb_lookup.py`'s `_shape_person()` needs updating to match -- that
function's mapping was written from this exact shape as documented
elsewhere in the codebase, never confirmed live.

**Test 2 -- through the actual admin UI (no secrets needed, just be
logged in as admin):**
1. Admin portal → Add Player → type `545361` into the new MLB ID field
   → click "Look Up Player".
2. Expect: Name auto-fills to "Mike Trout", Team to LAA, Position to CF,
   Age/Bats/Throws/Birth Date/Debut Date all populate.
3. Expect ALSO: the duplicate warning banner should appear immediately,
   showing Mike Trout's existing UPID with "Matched on: MLB ID" -- this
   is the new ID-based duplicate check firing correctly (he's already in
   the database under this exact mlb_id).
4. Click Cancel, not Submit -- this is meant to be a read-only check, no
   need to leave behind a test record.

**Test 3 (optional) -- direct endpoint call, if `BOT_API_KEY`/the
Railway app URL are handy:**
```
curl -s -X POST "$BOT_API_URL/api/admin/enrich-player" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $BOT_API_KEY" \
  -d '{"mlb_id": "545361"}' | python3 -m json.tool
```
Expect the same data as Test 1, reshaped to this app's field names
(`team`, `position`, `age`, `bats`, `throws`, `birth_date`, `debut_date`,
`mlb_id`, `name`). Zero side effects either way -- this endpoint never
writes anything, it only reads and returns.

**Test 4 (optional, has a real side effect -- posts to Discord) --
manager add-player-request path, to check the Proof-URL-optional change
and ID-based dup surfacing on the review card:**
1. As any manager on the players page: Add Player → any name → MLB ID
   `545361` → leave Proof URL blank → submit.
2. Expect: submission succeeds (pre-this-push, it would've been blocked
   with "Proof URL is required").
3. Check the admin Discord review channel: the request card should show
   `Enrichment: MLB ID 545361` and a duplicate match on Mike Trout's UPID
   with `[matched: mlb_id]`.
4. **Reject the request afterward** to keep the review queue clean --
   this was just a test, not a real add.
