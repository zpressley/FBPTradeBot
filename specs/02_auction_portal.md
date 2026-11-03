# Spec 02: Auction Portal on Neon

**Status:** Ready to build once Spec 01's schema exists on a Neon branch
**Build target:** December 20, 2026 (built and tested on a branch)
**Go-live:** Opening week of the 2027 auction (mid-March, date from the 2027 league calendar)
**Go-live depends on:** WizBucks and rosters being written by the database (Spec 03). The auction charges WizBucks and assigns players, so it can't go live while those still live in JSON.
**Read first:** `WARP.md`, `specs/01_neon_foundation.md` (Design rules), `AUCTION_RESOLUTION_RELIABILITY.md`

## Why this is first

The auction was the most visible failure of 2026. It also sits idle from the playoffs until mid-March, which means it can be rebuilt and tested against real data with zero league impact.

## What was actually wrong

Found by reading the 2026 code (`auction_manager.py`, `health.py`, `commands/auction.py`, `fbp-hub/js/auction.js`):

1. **Every write was a git push.** A bid saved `data/auction_current.json`, then `git commit && git push`. Two pushes close together meant one got rejected, which is the literal "persistence failed" message.
2. **Three schedulers raced on Sunday.** The bot's `auction_tick` loop and two GitHub Actions all tried to resolve the week and push. One was removed in July, but two triggers remain.
3. **The auction page read a stale copy.** `auction.js` polls `./data/auction_current.json` on GitHub Pages every 30 seconds. That file only updates after the bot pushes, the bot repo notifies fbp-hub, fbp-hub copies the file, and Pages redeploys. Bids showed up minutes late or not at all.
4. **Reading the auction wrote to it.** `GET /api/auction/current` saves state and does a git push whenever the phase changes. A page load could trigger a commit.
5. **Resolution wasn't all-or-nothing.** `resolve_week()` appends WizBucks ledger rows and rewrites the player files before it saves `resolved_at`. If it dies in between, a retry charges teams a second time.
6. **The affordability check can miss a team.** `_enforce_affordability()` loops over the teams that had wins at the start. A team that inherits a win after another team is dropped is never re-checked, so it can end up over budget.
7. **History was deleted every week.** Resolution sets `bids = []` and `matches = []`. The only record of 2026 auctions is git history and the ledger's `auction_winner` rows.
8. **Smaller issues.** Player log entries hardcode `2026`. Bid priority comes from Yahoo standings, which we're leaving.
9. **Uncontested OBs paid $10 no matter what was bid.** `_compute_tentative_winners()` hardcodes `"amount": 10` for the OB-only case. The new rule charges the OB amount.

## Rules (unchanged from 2026 unless noted)

All times Eastern. These match the 2026 code, which is what the league played under.

| Phase | When | Allowed |
|---|---|---|
| `off_week` | Before season start, All-Star break week(s), playoffs onward | Nothing |
| `ob_window` | Mon 3:00pm to Tue 11:59pm | Originating bids (OB) |
| `cb_window` | Wed 12:00am to Fri 9:00pm | Challenge bids (CB) |
| `ob_final` | Sat 12:00am to Sun 10:00am | OB manager records Match or Forfeit |
| `processing` | Sun 10:00am to Mon 3:00pm | Resolution runs, nothing else |

**Originating bids**
- Minimum $10. One OB per team per week. One OB per prospect per week.
- Prospect must be a Farm player with no current owner.

**Challenge bids**
- Only on prospects that have an OB. The OB team can't CB its own prospect.
- Must be at least $5 above the current high bid, **or** equal to the current high if the bidder has better priority (lower seed) than the current high bidder.
- One CB per team per prospect per ET calendar day. Exception: a team that still holds the high bid may raise again the same day.
- Friday: a team can only CB a prospect it already placed a CB on earlier in the week (no last-minute spoiler bids).

**WizBucks**
- Available = current WB balance minus committed. Committed = the sum of every prospect where this team is currently the high bidder. There are no ties: an equal bid belongs to the lower seed, so only that team has the amount held.
- When a team raises its own high CB, its old high amount is freed before checking.
- Bids can't be withdrawn.

**Resolution** (per prospect)
- OB and no CBs: OB team wins at **its OB amount** (minimum $10). **Changed from 2026**, when an uncontested OB always paid a flat $10. Decided 9/25: a manager who opens high to scare off challengers pays what they bid. There's no reason to open high anyway, since the OB team can match the top CB on Saturday.
- CBs and the OB team chose Match: OB team wins at the top CB amount.
- CBs and Forfeit or no decision: top CB wins. **There are no ties:** equal bids go to the lower seed. (Decided 10/27. This should be written into the Constitution.)
- Then affordability: any team whose wins exceed its balance loses its **latest** wins first, and each dropped prospect goes to the next eligible bidder. Repeat until every team is within budget (fixes bug 6).
- Winners get the player on a Purchased Contract (tier `P`), a WizBucks debit, and a player log event.

**Priority order**
- Worst standings first. Snapshot it when the week opens and store it on the week, so it doesn't change mid-week.
- 2027 source: FBP's own standings. Before those exist (the auction week before the 2027 season starts), use the **division draft seeding**. Admin can override. (Decided 10/27.)

**Visibility**
- Every bid is public on the site: team, amount and time, for every prospect. (Decided 10/27.)

## Schema (migration `0002_auction.sql`)

```sql
create table auction_weeks (
  week_start      date primary key,          -- Monday, ET
  season          int not null,
  priority_order  text[] not null,           -- team abbrs, worst standings first
  status          text not null default 'open' check (status in ('open','resolved','skipped')),
  resolved_at     timestamptz,
  resolved_by     text,                      -- 'scheduler' or admin name
  announced_at    timestamptz,               -- Discord post sent
  summary         jsonb
);

create table auction_bids (
  bid_id        uuid primary key default gen_random_uuid(),
  week_start    date not null references auction_weeks,
  team_abbr     text not null references teams,
  upid          text not null references players,
  bid_type      text not null check (bid_type in ('OB','CB')),
  amount        int  not null check (amount > 0),
  placed_at     timestamptz not null default now(),
  placed_date_et date not null,
  source        text not null check (source in ('web','discord','admin')),
  removed_at    timestamptz,                 -- admin removal, never a hard delete
  removed_by    text,
  admin_note    text
);
create unique index one_ob_per_team_week on auction_bids (week_start, team_abbr)
  where bid_type = 'OB' and removed_at is null;
create unique index one_ob_per_prospect_week on auction_bids (week_start, upid)
  where bid_type = 'OB' and removed_at is null;

create table auction_decisions (
  week_start  date not null references auction_weeks,
  upid        text not null references players,
  team_abbr   text not null references teams,
  decision    text not null check (decision in ('match','forfeit')),
  decided_at  timestamptz not null default now(),
  source      text not null,
  primary key (week_start, upid)             -- one decision per prospect, final
);

create table auction_results (
  week_start  date not null references auction_weeks,
  upid        text not null references players,
  team_abbr   text not null references teams,
  amount      int  not null,
  outcome     text not null check (outcome in ('OB_ONLY','OB_MATCH','CB_WIN')),
  dropped     jsonb,                          -- teams dropped for affordability, in order
  primary key (week_start, upid)
);
```

Nothing is ever deleted. Past weeks stay queryable forever.

## Code layout

```
auction/
  rules.py      # pure functions, no I/O: phase_for(now, calendar), validate_bid(...), resolve(bids, decisions, balances, priority)
  service.py    # database transactions: place_bid, record_decision, resolve_week, get_board, admin_*
  tests/
    test_rules.py     # unit tests for every rule above
    test_service.py   # runs against a throwaway Neon branch
```

- `rules.py` ports the logic from `auction_manager.py`, keeping the same error messages so Discord and web users see familiar text. Fix bug 6 and apply the new OB-only price (item 9) while porting.
- `service.py` is the only code that touches auction tables. The FastAPI routes, the Discord cog and admin tools all call it. Nothing else reads or writes auction data.
- Once live, delete `auction_manager.py`, `scripts/resolve_auction.py`, `.github/workflows/resolve-auction.yml`, and every `_commit_and_push` call on auction files.

## How writes stay safe

**Placing a bid** is one transaction:
1. `select pg_advisory_xact_lock(hashtext('auction'))`. This makes auction writes take turns, one at a time. With 12 teams that costs nothing, and it rules out every race between bids.
2. Load the week (create it if this is the first action of an active week), the prospect's bids, and the team's balance from `wb_balances`.
3. `rules.validate_bid(...)`. On failure, return the error and roll back.
4. Insert the bid, commit.
5. After the commit, post the Discord log message. If Discord fails, the bid still stands, and the failure only gets logged.

**Match/Forfeit and admin edits** take the same lock and follow the same pattern.

**Resolution** is one transaction:
1. Take the same lock. `select ... from auction_weeks where week_start = $1 for update`.
2. If `status = 'resolved'`, return the stored summary. Running it twice is harmless.
3. `rules.resolve(...)`, then insert `auction_results`, `wb_ledger` debits, `roster_assignments` rows, and `player_events` rows.
4. Set `status = 'resolved'`, `resolved_at = now()`, commit.
5. After the commit, post the Discord announcement and set `announced_at`. If the post fails, the scheduler retries only the announcement (`announced_at is null`), never the resolution.

A crash anywhere before the commit leaves the database exactly as it was (fixes bug 5).

## One scheduler

The bot's existing `auction_tick` loop (every minute) is the only thing that runs auction jobs:
- Sunday 10:00am ET or later, and the week is `open`: resolve.
- Resolved and `announced_at` is null: retry the announcement.
- Monday 3:00pm: post "OB window is open."
- Sunday 11:00am and still not resolved: post an alert in the admin channel tagging Zach.

`POST /api/admin/auction/resolve-now` stays as a manual button that calls `service.resolve_week()`. Delete `resolve-auction.yml` (fixes bug 2).

## API (routes stay the same, so the Worker doesn't change)

| Route | Change |
|---|---|
| `GET /api/auction/current` | **Read only.** Never writes or commits (fixes bug 4). Returns week, phase, `phase_ends_at`, priority order, and a board: one row per prospect with OB team, high bid, high team, CB count, and the full bid history (team, amount, time). If the Worker passes the manager's team, also include that team's balance, committed and available. |
| `POST /api/auction/bid` | Calls `service.place_bid`. Uses the team from the Worker's verified user, **not** from the request body. |
| `POST /api/auction/match` | Calls `service.record_decision`. Same team rule. |
| `/api/admin/auction/*` | Same routes, now backed by `service.admin_*`. Remove sets `removed_at`, never deletes. |

The Worker needs one small change: forward the logged-in manager's team as a header (for example `X-FBP-Team`) on auction routes. The API trusts that header only when `X-API-Key` is valid.

## Website (fbp-hub)

- `js/auction.js`: replace `fetchJSON(`${DATA}auction_current.json`)` with `GET ${workerUrl}/api/auction/current` (fixes bug 3). Keep the 30-second poll and refresh right after the user's own bid succeeds.
- Show "phase ends in X" from `phase_ends_at`, and show the manager's available WB next to the bid box.
- Stop copying `auction_current.json` into fbp-hub.

## Discord

`/auction`, `/bid` and the Match/Forfeit buttons call the same `service` functions. The Discord cog keeps no auction state of its own.

## Data migration

- Import the last 2026 week's `resolved_winners` from `data/auction_current.json` into `auction_weeks` + `auction_results`.
- Optional backfill: every `auction_winner` row in `wb_ledger` (66 in 2026) becomes an `auction_results` row with `outcome` unknown (null). That gives a season history page something to show.
- Optional replay test: full git history of `data/auction_current.json` holds every 2026 bid. A script can replay each week through `rules.resolve()` and compare against the recorded winners. A mismatch means either the old code or the new code had a bug, which is worth knowing before March.

## Tests (must pass before go-live)

**Rules (unit):** one test per rule in the tables above, including every phase boundary (Mon 2:59pm vs 3:00pm, Fri 8:59pm vs 9:00pm, Sun 9:59am vs 10:00am), daylight saving weeks, the All-Star break, and the Friday spoiler rule.

**Service (against a Neon branch):**
1. 50 concurrent bids from 4 teams on 3 prospects: no team's committed ever exceeds its balance, and every accepted bid follows the raise rules.
2. Two concurrent OB attempts on the same prospect: exactly one succeeds.
3. `resolve_week` called twice at the same moment: one result set, and one ledger debit per winner.
4. Kill the process mid-resolution (raise an exception after the ledger insert): zero rows written, week still `open`.
5. OB-only price: an uncontested $25 OB resolves at $25, and an uncontested $10 OB at $10.
6. Affordability: team A wins 3 prospects it can't afford, and the dropped prospect lands on team B, which then goes over budget. B also gets trimmed (bug 6).
7. `GET /api/auction/current` 100 times: zero writes (check `pg_stat_user_tables` or row counts).

**Dry run:** in early March, run a full mock week on the `dev` branch with 2 or 3 league volunteers before the real opening week.

## Acceptance criteria

- [ ] Migration `0002_auction.sql` applies cleanly on top of Spec 01
- [ ] All rule and service tests pass
- [ ] Web, Discord and admin tools all go through `auction/service.py`, and nothing else touches auction tables
- [ ] No git commit happens anywhere in the auction flow
- [ ] `resolve-auction.yml` and `auction_manager.py` are deleted
- [ ] Auction page shows a new bid within one poll (30 seconds) without a site deploy
- [ ] Mock week completes with correct winners, ledger debits and roster moves

## Open questions

1. ~~**OB-only price.**~~ Answered 9/25: an uncontested OB pays its bid amount, not a flat $10. Announce this in the dev log before the 2027 auction opens.
2. ~~**Tie commitments.**~~ Answered 10/27: there is no tie. The lower seed wins an equal bid, so only that team has the amount held. Add this to the Constitution.
3. ~~**Priority before FBP standings exist.**~~ Answered 10/27: division draft seeding. It only affects the one auction week before the 2027 season.
4. ~~**Bid visibility.**~~ Answered 10/27: show every bid.
