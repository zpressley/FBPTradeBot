# Spec 07: Team Page, Lineups and Position Eligibility

**Status:** Ready to build
**Build target:** February 28, 2027 (Roadmap: "Lineups, matchups, standings and free agency on the site")
**Live:** Spring training test week (about March 15, 2027), then Opening Day, March 25, 2027
**Depends on:** Spec 03 (contracts, roster status, roster limits). Feeds Spec 08 (scoring engine), which only counts stats for players in starting slots.
**Read first:** `WARP.md`, `specs/01_neon_foundation.md` (Design rules), `specs/03_rosters_contracts_wizbucks.md`

## Goal

Every manager has one team page that shows everything about their team and lets them set a daily lineup in a few taps on a phone. Multi-position players just work: the page knows where each player can play and only offers valid moves.

## Decisions (10/4, updated 10/10)

- **Roster:** 26 active spots, split into 9 batting slots (C, 1B, 2B, SS, 3B, CF, OF, OF, UTIL), 6 pitching slots (SP, RP, P, P, P, P) and 11 bench (BN). Plus **5 IL** and **2 NA** spots that don't count toward the 26. CF is its own slot and only CF-eligible players can fill it. A CF-eligible player can also fill either OF slot, but an OF-only player can't fill CF. (Confirmed 10/10.)
- **Position eligibility:** 5 games at a position, the way Yahoo runs it. Same number for pitchers: 5 starts for SP, 5 relief appearances for RP. Seeded from last season, then tracked through the year. (Confirmed 10/10.)
- **Lineups are daily** and each player locks at the first pitch of his own game. (Confirmed 10/10.)

## Two separate records

1. **Roster status (Spec 03):** who the team owns and where they sit on the roster: `active`, `il`, `na` or `farm`. Changes rarely, through actions (graduate, move to IL, release).
2. **Lineup (this spec):** for each team, each day, which player fills each slot. Bench is a slot (`BN`). Only players with roster status `active` can fill lineup slots. IL and NA players never appear in the lineup.

A player's stats count toward the team only on days he's in a starting slot (anything but `BN`).

## Schema (migration `0007_lineups.sql`)

```sql
create table lineup_slot_types (                -- the league's slot setup, by season
  season    int,
  code      text,                               -- 'C','1B','2B','SS','3B','CF','OF','UTIL','SP','RP','P','BN'
  count     int not null,                       -- OF 2, P 4, BN 11, others 1
  accepts   text[] not null,                    -- positions allowed: OF accepts {OF,CF}; UTIL accepts all batter positions; P accepts {SP,RP}; BN accepts everything
  is_starting boolean not null,                 -- false only for BN
  sort_order int not null,
  primary key (season, code)
);

create table position_eligibility (
  upid      text references players,
  season    int,
  position  text,                               -- 'C','1B','2B','3B','SS','CF','OF','SP','RP'
  source    text not null check (source in ('last_season','this_season','primary','admin')),
  games     int,                                -- games at the position that earned it
  earned_on date,
  primary key (upid, season, position)
);

create table lineups (
  team_abbr  text references teams,
  game_date  date,                              -- ET calendar day
  slot_code  text,
  slot_index int,                               -- OF 1-2, P 1-4, BN 1-11
  upid       text references players,           -- null = empty slot
  locked_at  timestamptz,                       -- set when this player's game starts
  set_by     text,                              -- manager, 'carryover', 'optimize', 'admin'
  primary key (team_abbr, game_date, slot_code, slot_index),
  unique (team_abbr, game_date, upid)
);

create table mlb_games (                        -- daily schedule from the MLB Stats API
  game_pk    bigint primary key,
  game_date  date not null,
  start_time timestamptz not null,
  home_team  text, away_team text,
  status     text,                              -- scheduled, live, final, postponed
  probable_home_sp int, probable_away_sp int    -- MLB IDs
);
```

## Position eligibility

- **Preseason:** each player is eligible at his primary position plus every position where he played **5 or more games** last MLB season.
- **In season:** a player gains a position the day after his 5th game there this season. Eligibility is never lost mid-season.
- **Pitchers:** SP after 5 starts, RP after 5 relief appearances, same windows.
- **CF counts for OF, not the other way around.** The OF slots accept CF or OF players. The CF slot only accepts CF players.
- **Rookies with no MLB games** get their primary position from the MLB Stats API.
- **Source:** MLB Stats API fielding stats by position, refreshed nightly. Admins can add a position by hand with a reason (`source = 'admin'`).
- **Roster limits (Spec 03) read this same table,** so "8 SP, or 9 with an SP/RP-eligible player" always matches what the lineup allows.
- **Thresholds live in a settings row** (`eligibility_games = 5`), not in code.

## Two-way players

A two-way player (Ohtani) is two linked roster entries, a batter version and a pitcher version, under one contract. Each version uses its own roster spot (2 of the 26) and only fills slots of its own type. Traded, released and kept as a package (Constitution Art. V, Clause 1).

## How the daily lineup works

- **Carryover:** at 12:00 AM ET a job copies each team's lineup to the new day (`set_by = 'carryover'`), dropping anyone who moved to IL, NA or off the team. Empty starting slots stay empty and show a warning.
- **Future days:** managers can set lineups up to 7 days ahead. A future day is a copy that carryover won't overwrite once it's been edited.
- **Locks:** when a player's game starts (`mlb_games.start_time`), his slot locks for that day: he can't be moved and nobody can be moved into his slot. Players whose games haven't started can still move. Postponed games unlock.
- **Validation** (one function, `lineup_service.validate`): every player is on the team with `active` status, every player fits the slot's `accepts` list through his eligibility, no player appears twice, and nothing locked was changed.
- **Saving is one transaction** for the whole day's lineup, so a phone that drops mid-save can't leave half a lineup.
- **Two people editing:** the save carries the version it started from. If the lineup changed in between (another device, or a lock kicked in), the save is rejected with "Lineup changed, here's the latest" and the screen reloads.

## Moving players (phone first)

- **Tap a player.** Every slot he can legally fill lights up, plus the bench. Tap one: he moves there, and whoever was in it swaps into his old spot if that player fits there, or goes to the bench if not.
- **No drag and drop** (the 2026 dashboard notes say it never worked well on phones).
- **Optimize** fills the starting slots to start as many players with a game today as possible, placing multi-position players where they free up the most slots. It prefers higher-ranked players when there's a choice. The manager reviews and saves.
- **Badges on every row:** game time and opponent, "No game," "Postponed," "Probable SP," "Locked," and "IL" or "NA" alerts if MLB lists him injured while he's in a starting slot.

## The team page

URL: `/team/WAR`. Anyone can view any team. Only the owner (and admins) can change things.

**Header:** team name and logo, record and standings spot, WizBucks balance, and alerts (empty starting slot today, roster over a limit, debut fee due, prospect eligible to graduate).

**Tabs:**
1. **Lineup** (default): today's slots with badges and locks, a date picker for the next 7 days, Optimize, Save. Below it, IL (5) and NA (2) with move buttons.
2. **Roster:** every active, IL and NA player with positions, contract tier, cost to keep at the next KAP, IL tag and RaT history.
3. **Farm:** prospects with contract type (DC, PC, BC), rank, level, and progress bars toward each graduation limit. Actions: upgrade, pay debut fee, graduate.
4. **Money:** balance, money tied up in pending offers and auction bids, and the full WizBucks history.
5. **Picks:** draft picks owned, original owner, and buy-in status.

## Roster moves from the team page

- **Move to IL:** only when MLB lists him on the IL (from the Stats API). Opens a roster spot.
- **Move to NA:** only for players not on an MLB active roster (minors).
- **Activate:** back to active, blocked with a clear message if it would put the team over 26 or a pitcher or batter cap.
- **Release:** goes through Spec 03's `release` (tier stays with the player).
- Every move checks Spec 03's roster limits and is one transaction.

## Tests

1. A 2B/SS player can be placed at 2B, SS, UTIL or BN, and nowhere else.
2. A CF-eligible player can fill CF or either OF slot. An OF-only player can fill either OF slot but not CF.
3. A player reaches his 5th game at 3B on Tuesday and is 3B-eligible Wednesday.
4. Carryover copies Monday's lineup to Tuesday and drops a player moved to IL Monday night.
5. A player whose 1:05 PM game has started can't be moved at 1:10. A 7:05 PM player can.
6. A postponed game unlocks the slot.
7. Two devices save the same team's lineup a second apart. The second gets "Lineup changed" and nothing is lost.
8. Optimize with 12 batters (4 with no game today) fills all 9 batting slots with players who have games when it's possible.
9. Ohtani's batter version can't go in a P slot. Both versions count as roster spots.
10. Activating a player from IL when the team has 26 active is blocked with the reason.
11. Saving a lineup with an IL player in a starting slot is rejected.

## Acceptance criteria

- [ ] All tests pass
- [ ] Every lineup write goes through `lineup_service`, every eligibility check through `position_eligibility`
- [ ] Setting a full day's lineup on a phone takes under a minute, with Optimize under 10 seconds
- [ ] The team page works for every team, read-only for non-owners

## Open questions

None. Slot setup, pitcher eligibility and lineup locks were confirmed 10/10.
