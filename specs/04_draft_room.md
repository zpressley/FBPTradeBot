# Spec 04: Draft Room

**Status:** Draft for review
**Build target:** January 15, 2027 (ready to test). Pool check and a league mock draft in early February.
**Live:** 2027 Prospect Draft (about Feb 14) and Keeper Draft (about Mar 7). Final dates come from the 2027 league calendar.
**Depends on:** Spec 01 (schema), Spec 03 (rosters written by the database). Spec 02 (auction) shows the same patterns and should be built first.
**Read first:** `WARP.md`, `specs/01_neon_foundation.md` (Design rules), `specs/04_draft_findings_2026.md`

## Goal

A draft room on the website that works every time for a live sitting. Anyone can find any player fast, the clock is right for everyone, a restart costs nothing, messages always arrive, and adding a player mid-draft just works.

## Decisions (9/28, updated 10/27)

1. **The website is the draft room.** Discord is for notifications only: a DM when you're on the clock with a link back to the draft room (link only, no pick buttons; decided 10/27), plus pick announcements in the draft channel. Picking by typing a name in Discord goes away.
2. **Live sitting.** Each draft runs in one session. Slow drafts (days long, overnight pause) are a future feature, but the design shouldn't block them.
3. **Autopick uses a ranking Zach uploads before each draft.** No ranking, no autopick from the pool (see Autopick).
4. **No pick trading during the draft.** Good idea, parked as a future feature.

## Out of scope

- Pick trading during a draft (future)
- Slow draft mode (future)
- The Division Draft (captains picking teams). Small enough to run by hand for now.
- Draft order generation (buy-ins, tax brackets, traded picks). That belongs to the KAP/PAD spec. This spec reads the order it's given.

## Rules to preserve (from 2026)

- **Order:** comes from the order table. Slots have an original owner and a current owner (traded picks). Rounds can be different sizes. The Keeper Draft skips slots that are keeper, taxed out, or an unbought buy-in.
- **Prospect Draft:** rounds 1-2 are FYPD-only and give a Blue Chip Contract. Round 3+ is any unowned prospect and gives a Development Contract.
- **Keeper Draft:** any unowned MLB player. Rounds 1-3 give VC-1, round 4+ gives TC-1.
- **Clock:** 4 minutes for the Prospect Draft, **3 minutes for the Keeper Draft** (changed 10/27). 60-second warning. Forklift teams get 10 seconds and board-only autopick. Forklift turns itself off when that team's board runs dry.
- **Boards:** each manager keeps an ordered list, up to 50 players.
- **Admin:** pause, resume, undo. Every pick writes a player event and a roster assignment.

## Schema (migration `0004_draft.sql`)

Spec 01 imported the 2026 order into a table called `draft_picks`. This migration renames it to `draft_slots` and adds the columns below, so the name `draft_picks` means actual picks from here on.

```sql
create table drafts (
  draft_id        text primary key,            -- e.g. 'prospect-2027'
  season          int  not null,
  draft_type      text not null check (draft_type in ('prospect','keeper')),
  status          text not null default 'setup'
                  check (status in ('setup','pool_check','live','paused','complete')),
  clock_seconds   int  not null default 240,
  forklift_seconds int not null default 10,
  starts_at       timestamptz,
  is_test         boolean not null default false
);

create table draft_slots (
  draft_id     text references drafts,
  overall      int,
  round        int  not null,
  pick_in_round int not null,
  original_team text not null references teams,
  team_abbr    text not null references teams,  -- current owner
  round_type   text not null,                    -- 'fypd', 'open', 'vc', 'tc'
  skip_reason  text,                             -- 'keeper', 'taxed', 'buyin_unpaid', or null
  primary key (draft_id, overall)
);

create table draft_picks (
  draft_id   text,
  overall    int,
  upid       text not null references players,
  team_abbr  text not null references teams,
  source     text not null check (source in ('web','autopick','forklift','admin')),
  made_by    text not null,                      -- manager, 'system', or admin name
  made_at    timestamptz not null default now(),
  seconds_used int,                               -- clock time used: clock length minus time left at the pick (full clock for autopick and forklift). Feeds the speed awards.
  contract_tier text not null,
  primary key (draft_id, overall),
  foreign key (draft_id, overall) references draft_slots,
  unique (draft_id, upid)                        -- a player can only be drafted once
);

create table draft_clock (
  draft_id           text primary key references drafts,
  on_clock_overall   int,
  deadline_at        timestamptz,                -- null while paused
  paused_remaining_ms int,
  warned             boolean not null default false,
  runner_heartbeat_at timestamptz              -- clock runner writes this every second
);

create table draft_forklift (
  draft_id  text references drafts,
  team_abbr text references teams,
  enabled_at timestamptz not null default now(),
  primary key (draft_id, team_abbr)
);

create table draft_boards (                     -- each manager's big board
  season     int,
  draft_type text,
  team_abbr  text references teams,
  slot_key   text,                               -- a pick they own ('prospect-2027:27') or 'any'
  position   int,
  upid       text not null references players,
  primary key (season, draft_type, team_abbr, slot_key, position),
  unique (season, draft_type, team_abbr, slot_key, upid)
);

create table draft_rankings (                   -- uploaded by Zach before each draft
  draft_id text references drafts,
  upid     text references players,
  rank     int not null,
  source   text,
  primary key (draft_id, upid)
);

create table draft_pool_overrides (             -- admin fixes found during pool check
  draft_id text references drafts,
  upid     text references players,
  action   text not null check (action in ('include','exclude')),
  reason   text not null,
  set_by   text not null,
  set_at   timestamptz not null default now(),
  primary key (draft_id, upid)
);

create table notifications (                    -- outbox: nothing is sent inside a transaction
  id          bigserial primary key,
  kind        text not null,                    -- 'on_clock','warning','on_deck','pick_made','draft_paused',...
  team_abbr   text,
  payload     jsonb not null,
  created_at  timestamptz not null default now(),
  sent_at     timestamptz,
  attempts    int not null default 0,
  last_error  text
);

create table draft_events (                     -- admin actions, for the audit trail
  id        bigserial primary key,
  draft_id  text references drafts,
  kind      text not null,                      -- 'start','pause','resume','undo','swap','void','makeup_pick','forklift_on','player_added',...
  actor     text not null,
  detail    jsonb,
  at        timestamptz not null default now()
);
```

Also enable the `unaccent` and `pg_trgm` extensions for search.

## One eligibility rule

A single database function, `draft_pool(draft_id)`, returns **every** player it considered, with `eligible boolean` and `reason text`. The website, search, autopick, boards and the pick check all use it. Nothing else decides eligibility.

**Prospect Draft:**
- `player_type = 'Farm'`
- no open roster assignment (not owned)
- not already drafted in this draft
- for FYPD rounds: `fypd = true`
- then apply `draft_pool_overrides` (admin include or exclude, with a reason)

**Keeper Draft:**
- `player_type = 'MLB'`, no open roster assignment, not already drafted, then overrides.

Reasons are plain text a manager can read: "Owned by WAR", "Already drafted (pick 14)", "Not FYPD eligible (rounds 1-2)", "Excluded by admin: retired".

## Pool check (the fix for the player who got left out)

The draft moves to `pool_check` status 7 days before it starts.

- The draft room shows the full eligible pool **and** an "Excluded players" tab listing everyone who was left out and why.
- Any manager can hit "Missing someone?" to request a player be added or included. Requests notify Zach.
- Zach fixes issues with overrides or by adding the player. Every change shows up in `draft_events`.
- Before the draft opens, a check fails loudly if the ranking file references a player who isn't eligible, or if an eligible player is missing from the ranking file.

## 2 Time (back-to-back picks)

When one team owns consecutive draftable picks (usually from a trade, or because the teams between them have no pick that round), that run is a **2 Time**. Three in a row is the rare **3 Time**. Runs can cross a round boundary.

- **Everywhere:** the scoreboard tags it ("WAR 3.03 · 2 Time"), the your-next-pick pill says "You're up · 2 Time" or "You 3.03 · in 5 · 2 Time," Barrel wraps the run in a labeled box, Classic tags the first pick of the run, and Rainbow tags its first cell. The 3 Time gets its own louder styling.
- **Lock in together:** when a manager is on the clock with a run, the player card's button becomes "Add to 2 Time (1 of 2)." A tray above the draft view shows each pick in the run with the chosen player. "Lock in all 2" makes every pick in one transaction, in order. "Lock in 1 now" is also allowed, and the rest of the run stays on the clock.
- **Clock:** each pick in the run keeps its own normal clock. Locking in the whole run skips straight past it. The runner treats the run as individual picks for autopick, taking the tray first, then each pick's big board list, then "Any pick."
- **Announcements:** one channel message for the whole run ("WAR 2 Time: Player A and Player B"). The manager gets one on-clock DM for the run, not one per pick.
- `make_pick` gets a sibling, `make_picks(draft_id, team, [upid...])`, which runs the same checks for each pick inside one transaction and fails all or nothing.

## Picking

`draft_service.make_pick(draft_id, team, upid, source, actor)` is the **only** way a pick happens. The website, DM buttons, autopick, forklift and admin tools all call it.

It runs in one transaction:
1. `pg_advisory_xact_lock(hashtext(draft_id))`, so picks take turns.
2. Check that the draft is `live`, the slot on the clock belongs to `team` (or the actor is an admin), and the player is eligible for that slot's round in `draft_pool`. In the Prospect Draft, the team must also have an open prepaid slot of the right type (BC for rounds 1-2, DC for 3+) from Spec 03's `prospect_draft_slots`. The pick fills that slot, and no WizBucks change hands. Slots are picks: every slot a team bought must be filled, and autopick fills any the manager doesn't.
3. Insert the `draft_picks` row. The unique index makes a double pick impossible.
4. Open a `roster_assignments` row with the contract for the round, and write a `player_events` row.
5. Move the clock to the next slot without a `skip_reason`: `deadline_at = now() + clock_seconds` (or `forklift_seconds`), `warned = false`. No slots left means `complete`.
6. Queue notifications: `pick_made` (channel), `on_clock` (next team's DM), `on_deck` (the team after that).
7. Commit. Notifications go out after the commit.

## The clock

- The clock is a **deadline** stored in UTC. Everyone counts down to the same moment.
- Every API response includes `server_now`, so the website corrects for a wrong browser clock instead of trusting it.
- **Pause** stores `paused_remaining_ms = deadline_at - now()` and clears `deadline_at`. **Resume** sets `deadline_at = now() + paused_remaining_ms`. No free minutes.
- **Admin pick corrections** work on **any** pick, not just the latest. Each one is its own transaction with a reason, logged in `draft_events`, and announced in the draft channel. Three tools:
  - **Undo last pick:** deletes the latest pick, closes its roster assignment, writes a reversal event, and puts that slot back on the clock with a full clock.
  - **Swap player** on any earlier pick (wrong player clicked, same-name mixup): replaces the player on that pick. The old player goes back to the pool, and the new one must be eligible for that pick's round. Later picks are untouched.
  - **Void** any earlier pick: removes the pick and returns the player to the pool. That slot becomes a **makeup pick** for its team, taken right after the current pick with a full clock. Later picks are untouched, so nobody else loses a pick they already made.
  - If a correction would make a later pick invalid (for example, swapping in a player someone else already took), it's blocked with an explanation instead of cascading.
- **The clock runner** is a loop in the bot process that checks every second. It reads the database and holds nothing in memory:
  - `deadline_at < now()`: autopick for the team on the clock.
  - Under 60 seconds and not `warned`: queue a warning, set `warned`.
  - It catches and logs every error, then keeps going. One bad pick attempt can't stop it.
- **After a restart,** the runner picks up where the database says. The website keeps showing the true deadline the whole time.
- **Nobody gets autopicked because of an outage.** The bot and the API run in the same Railway service, so if it went down, managers couldn't pick either. When the runner starts and its last heartbeat is more than 15 seconds old, it first extends the current deadline to at least `now() + 60 seconds`, posts "Draft resumed, 1:00 added to the clock" in the channel, and then carries on.
- If the process is up but the runner loop itself stops (heartbeat older than 15 seconds), the API pauses the draft, the website shows "Draft clock paused, commissioner notified," and Zach gets an alert.

## Autopick

In order:
1. The team's big board targets for **this pick**, top to bottom, then their "Any pick" list, skipping anyone no longer eligible.
2. For forklift teams, stop here. An empty board turns forklift off for that team and gives them a normal clock. They get a DM saying so.
3. `draft_rankings` for this draft, best rank first, eligible for this round.
4. If nothing qualifies (no ranking uploaded, or the ranking is exhausted), **pause the draft and alert Zach.** No random or placeholder picks.

Ranking upload: an admin page takes a CSV of `name, mlb_id or upid, rank`. It matches on ID first, then name, and shows unmatched rows for fixing before saving.

## Search

`GET /api/draft/{draft_id}/search?q=`:
- Matches as you type, starting at 2 characters.
- Ignores accents and case ("jose rodriguez" finds José Rodríguez).
- Matches first names, last names, partial names ("chourio", "j. wood") and aliases from `player_aliases`.
- Ranks results by match quality, then by draft ranking.
- Returns ineligible players too, grayed out with the reason, so nobody wonders whether a player is missing or just taken.
- Returns ID, name, position, MLB org, level, age, FYPD flag and rank. Same-named players are told apart by org and position.

## Website draft room (fbp-hub)

Built for phones first. A clickable mockup was reviewed 9/28 (Barrel, Classic, Rainbow views plus the big board).

**Always on screen: the scoreboard** (one compact row, kept to the essentials)
- **Left:** the team on the clock in large type, with the pick number beside it ("WAR 3.03", plus "via TBB" for a traded pick, plus a **2 Time** or **3 Time** tag when they pick back to back). It switches to your color when it's your turn.
- **Center:** the clock, a small ring that drains as time runs with the digits inside. Red under 60 seconds.
- **Right:** the next three teams ("CFL › JEP › B2J", yours highlighted) and a **your next pick** pill ("You 4.03 · in 7", or "You're up"). Tapping the pill jumps to that pick in whatever view you're in (and switches phones to the Draft tab), with a brief highlight.
- The last pick lives in the ticker, not the scoreboard.
- On phones: team and pick with the pill beneath on the left, the clock ring on the right.
- **Ticker:** the last 8 picks.
- **View switcher.** Each manager's choice is remembered on their device.
- On phones, the scoreboard compacts: team and pick details on the left, a smaller clock ring on the right, and the queue and "Your next pick" side by side underneath.

**Finding players (left pane, or the Players tab on phones)**
- Search as you type: accents ignored, last names, orgs ("SEA"), positions.
- Position chips: C, IF, OF, RHP, LHP. Toggles: hide drafted, on my big board.
- Best Available list sorted by the uploaded ranking, with a tier divider every 25 ranks.
- Drafted players stay in the list, grayed out with "WAR 2.07."
- Tap any player for a card with rank, level, age, ETA and org, plus "Draft" (only when you're on the clock) and "Slot on my big board."

**My big board**
- One ranked target list for **each pick the manager owns**, plus an "Any pick" list as the fallback. Slot a player from their card by choosing the pick (the card shows each of your picks with a status badge).
- Each target shows a status:
  - **Likely:** the projection has them available at your pick.
  - **Risky:** the projection has them going before your pick, and shows to whom.
  - **No chance:** they're projected to go before your pick **and** they sit in the top half of the available players compared to the number of picks ahead of you. In other words, it would take a lot of teams passing on them.
  - **Gone:** already drafted, and by whom.
- Reorder with up and down buttons. Drag and drop on desktop is a nice-to-have.
- Autopick uses this pick's list first, then "Any pick" (see Autopick).
- Replaces the single 50-player board from 2026. Existing boards import as "Any pick."

**Views (all read the same live state, and none can make a pick except through the player card)**
1. **Barrel** (default). One column per round, holding only the teams that actually pick that round (keeper, taxed and unbought slots are left out, so columns are different lengths). Made picks are solid. Open slots show the projection, faded. The manager's own open picks show their top live big board target with its status badge and the next two backups. Columns scroll sideways and snap on phones.
2. **Classic.** The pick list by round, with each round's pick count and upcoming slots.
3. **Rainbow.** The full grid (rounds by pick position) colored by position group, with no-pick slots shown dashed and labeled (kept, taxed, no buy-in).

**Projection rule (Barrel view and status badges):** walk the open picks in order. Other teams take the best-ranked eligible player left. The viewing manager's picks take their first available big board target for that pick, then "Any pick," then best available. This runs in the browser from the state response, so it costs the server nothing. Each manager only sees their own targets. The projection never uses other managers' boards.

**Admin panel** (admins only): pending add requests at the top, then start, pause, resume, pick corrections (undo, swap, void on any pick), forklift toggles, add player, override, upload ranking.

Live updates: poll `GET /api/draft/{draft_id}/state` every 2 seconds. It's a single small query, and 12 managers polling is nothing. (Server-sent events can come later.)

Stop reading draft data from GitHub Pages JSON entirely.

## Sound

Built into the draft room (not a separate phase). All sounds are generated in the browser with the Web Audio API, so there are no audio files to host or license. No real stadium music or TV draft themes.

- **Unlock:** browsers block sound until the page is tapped. The scoreboard gets a speaker button; one tap turns sound on for the session. Off by default. iPhone silent mode still mutes it, so visuals never depend on sound.
- **Sounds:**
  - **You're on the clock:** short organ-style riff. The most important one.
  - **60-second warning:** a chime. Soft ticks for the last 10 seconds.
  - **Pick made:** quiet click for other teams, a bigger sound when your pick locks in.
  - **2 Time / 3 Time:** a horn when one comes up. The 3 Time gets a bigger one.
  - **Sniped:** sad trombone when your top live target for your next pick gets taken (only that one, so it isn't constant).
  - **Announcer (toggle, off by default):** the browser's built-in voice (`speechSynthesis`) reads each pick: "With pick 3.03, the Weekend Warriors select Eduardo Quintero." A 2 Time reads both.
- **Settings per manager:** master on/off, volume, and a checkbox per sound, saved on their device alongside the view choice.
- **Background tab:** once unlocked, sounds still play when the tab isn't in front. The page can also ask permission for a browser notification ("You're on the clock, 3.03"), as a backup to the Discord DM.
- **Test:** a "Test sounds" button in settings plays each one.

## Discord (notifications only)

Sent by the bot from the `notifications` outbox. A row is marked `sent_at` only after Discord accepts it. Failures retry with backoff up to 5 times, then alert Zach.

- **On the clock (DM):** "You're on the clock, pick 2.07. 3:00 to pick." (the draft's clock length) with an **Open Draft Room** link button. Picks only happen on the website.
- **60-second warning (DM).**
- **On deck (DM).**
- **Pick made (draft channel):** one message per pick.
- **If a DM fails** (DMs turned off), post in the draft channel tagging that manager instead.
- **Remove:** typed-name picking, `on_message` draft handling, DM confirm views, `/draft` commands except an emergency `/draft pause` for admins.

## Adding a player mid-draft

Any manager can request a player. Any admin (teams with `role = 'admin'` in `teams`) approves or rejects. Admins can also add directly, skipping the request.

**Request (manager, from the draft room):**
1. "Add a player" searches the MLB Stats API by name and shows org, position, age and MLB ID, so the manager picks the right person. If the player already exists in FBP, it says so and shows why they aren't in the pool instead of creating a duplicate.
2. The manager submits with an optional note ("FYPD eligible, signed in January").
3. This creates a `draft_add_requests` row, logs a `draft_events` row, and notifies admins right away (DM plus a badge in the admin panel).

**Approve or reject (admin):**
1. The admin panel shows pending requests at the top, with the MLB profile.
2. Approve: the admin confirms player type, FYPD yes/no and rank (optional, defaults to after the last ranked player). This creates the player with a UPID. The player is searchable and draftable immediately, with nothing to reload.
3. Reject: the admin gives a reason.
4. Either way, the requesting manager gets a DM with the result, and an approval posts "Added to the pool: [name]" in the draft channel so every manager knows.

**If the requester is on the clock:**
The draft pauses automatically when the team on the clock submits a request, so they don't time out waiting on an admin. It resumes with the same time left once the request is decided. To keep this from becoming a timeout trick, each team gets one auto-pause per draft. After that, requests still go through, but the clock keeps running.

**Schema addition:**

```sql
create table draft_add_requests (
  id            bigserial primary key,
  draft_id      text not null references drafts,
  requested_by  text not null references teams,
  mlb_id        int  not null,
  player_name   text not null,
  note          text,
  status        text not null default 'pending' check (status in ('pending','approved','rejected')),
  paused_draft  boolean not null default false,
  decided_by    text,
  decided_at    timestamptz,
  decision_note text,
  created_upid  text references players,
  created_at    timestamptz not null default now()
);
create unique index one_pending_request_per_player on draft_add_requests (draft_id, mlb_id)
  where status = 'pending';
```

The same request flow runs during the pool check week, without the auto-pause.

## Testing

- **Test drafts never touch real data.** Test drafts run on a Neon branch (`draft-test`), created fresh from `main` for each test and deleted after. `is_test` drafts are rejected on the `main` branch.
- **Simulator:** `scripts/draft/simulate.py --draft prospect-2027 --clock 2` runs a full draft on a test branch with 12 fake managers picking at random intervals, some timing out, plus forced restarts of the clock runner mid-draft. It checks that nothing stalls, nothing is double-picked, every slot is filled, and roster assignments match picks.

**Required tests:**
1. Kill the bot mid-draft with 1:30 left. It comes back, and the website shows the same deadline the whole time.
2. The deadline passes while the bot is down. On restart, the clock is extended to 1:00 and a channel message explains why. No autopick for time lost to the outage.
3. Two managers try to pick the same player at the same instant. One succeeds, and the other gets "Already drafted."
4. Someone tries to pick out of turn from the website. Rejected with a 403.
5. Pause at 1:30 left, resume 10 minutes later. 1:30 left.
6. Search: "chourio", "jose rodriguez" (no accents), "j wood", "jose ramos" (two different players returned).
7. A manager with DMs off gets a channel tag instead.
8. Discord API is down for 2 minutes. Notifications queue and all arrive after.
9. Add a player mid-draft. Searchable and draftable in under 2 seconds.
9a. A manager on the clock requests a player. The draft pauses, an admin approves, the draft resumes with the same time left, and the manager can draft the new player. A second on-clock request from that team does not pause.
9b. Two managers request the same player at once. One pending request, not two players.
10. A big board with an ineligible or drafted player: autopick skips them and moves down the list, then to "Any pick."
10a. Big board privacy: manager A's targets never appear in manager B's projection or API responses.
11. No ranking uploaded and a manager times out with an empty board. The draft pauses and Zach gets alerted.
12. Full simulator run of both 2027 drafts with zero stalls.
13. Speed awards: a forklift team scores the full clock per pick, a 30-second pause doesn't count against the team on the clock, a tie for 6th splits $5, the leaderboard carries from the Prospect Draft into the Keeper Draft, nothing pays until the second draft completes, and a test draft pays nothing.
12a. A 2 Time locked in together: both picks land in one transaction, one channel message, and the clock moves past both. A 3 Time with only 1 of 3 locked: the next pick in the run is on the clock.
13. Swap the player on pick 5 during pick 20. Pick 5 shows the new player, the old player is back in the pool, and picks 6-19 are unchanged.
14. Void pick 5 during pick 20. Pick 5's team gets a makeup pick right after pick 20, with a full clock.

## Rollout

- **Mid-January:** build done, simulator passing.
- **Late January:** Zach runs a solo mock on a test branch.
- **Pool check** opens 7 days before the Prospect Draft.
- **League mock draft** (short, 2 or 3 rounds) about a week before the real draft, on a test branch. It's the dress rehearsal for everyone's phones and DMs.
- **Prospect Draft**, then fix anything, then the **Keeper Draft**.

## Acceptance criteria

- [ ] All 13 required tests pass
- [ ] Every pick path goes through `make_pick`, and every eligibility check goes through `draft_pool`
- [ ] No draft state in bot memory, and no git in the draft flow
- [ ] Web picks use the Worker-verified team, never the request body
- [ ] Pool check shows eligible and excluded players with reasons
- [ ] League mock draft completes on phones with no manual fixes

## Future features (not in this build)

- Trading picks during the draft
- Slow draft mode: longer clocks, overnight pause windows, queued picks
- Live push updates (server-sent events) instead of polling
- Arcade-themed view (mocked up 9/28, shelved)

## Fastest drafter awards (added 10/27)

The six teams with the fastest average pick time **across both drafts combined** (Prospect and Keeper) win WizBucks: **1st $30, 2nd $25, 3rd $20, 4th $15, 5th $10, 6th $5.** One set of awards per season, $105 total.

- **Running leaderboard:** a Speed tab in the draft room shows every team's average and pick count, updating after each pick and carrying over from the first draft into the second.

- **Pick time** is `draft_picks.seconds_used`, which `make_pick` records as the clock length minus the time left on `deadline_at`. Pauses and outage time added to the clock don't count against the team (never below 0).
- **Autopicks and forklift picks count as the full clock.** Otherwise forklift (10 seconds) would win every year.
- In a 2 Time, each pick is timed from when it went on the clock, so locking in both picks at once is rewarded.
- Admin corrections (undo, swap, void) don't count against anyone; a makeup pick is timed like any other.
- Ties split the combined prize for those places evenly.
- Paid when the **second** draft of the season is marked `complete`, as `wb_ledger` rows with `kind = 'draft_award'` (Spec 03), in one transaction. The draft channel gets a "Fastest drafters" post with the top 6 and their averages. After the first draft, the channel gets the standings so far.
- Test drafts (`is_test`) never pay.

## Open questions

1. ~~**Quick-pick buttons in the DM.**~~ Answered 10/27: link only.
2. ~~**Clock length.**~~ Answered 10/27: 3 minutes for the Keeper Draft. The Prospect Draft stays at 4.
4. ~~**Speed awards scope.**~~ Answered 10/27: combined across both drafts, with a running leaderboard.
3. ~~**Undo scope.**~~ Answered 9/28: admins can correct any pick. Spec now has undo, swap and void with makeup picks.
