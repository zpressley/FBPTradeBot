# Database (Neon Postgres, shadow mode)

Nothing reads from this database yet. `data/*.json` is still the source of truth; the database is a verified mirror that the daily workflow refreshes (`specs/01_neon_foundation.md`).

## The Neon project

Project **HUB 3.0** (`nameless-tooth-55559042`, org `org-plain-credit-91312585`, `aws-us-east-1`, Postgres 18). The default branch is **`production`** (the spec calls it `main`; same role). `.neon` (git-ignored) links this checkout to it, and `neon.ts` declares the services: Postgres only, with new non-default branches expiring after 7 days.

The CLI is `neon` (`npm i -g neon@latest`; the spec's `neonctl` is its old name). Sign in with `neon login`, or set `NEON_API_KEY` in CI.

## Connection strings

`neon deploy` (or `neon env pull`) writes the linked branch's strings into `.env.local` (`.env` if that file exists); both are git-ignored. The scripts load `.env` then `.env.local` themselves (real environment variables win), and each also takes `--database-url`.

| Variable | Neon string | Used by |
|---|---|---|
| `DATABASE_URL` | pooled (host contains `-pooler`) | the app (later), `import_json.py`, `reconcile.py` |
| `DATABASE_URL_UNPOOLED` (what Neon writes) or `DATABASE_URL_DIRECT` | direct | `migrate.py` (falls back to `DATABASE_URL` if both are unset) |

The same two values go in the GitHub repo secrets `DATABASE_URL` and `DATABASE_URL_DIRECT` for the daily workflow and `db-mirror.yml` (run it from the Actions tab to migrate + import + reconcile on demand).

Driver: the bot and API are one long-running Python process on Railway, so plain `psycopg` 3 over the **pooled** string is the right fit (no serverless HTTP driver needed); migrations use the direct string.

## Everyday commands

```bash
pip install -r requirements.txt            # psycopg[binary]

python scripts/db/migrate.py               # apply db/migrations/*.sql; re-running is a no-op
python scripts/db/import_json.py           # full reload of data/ + config/ in ONE transaction
python scripts/db/import_json.py --dry-run # parse and report, write nothing
python scripts/db/reconcile.py             # 7 checks + known data issues; exit 1 on any FAIL
python scripts/db/reconcile.py --seed 1 --sample 50 --verbose
```

`reconcile.py` is read-only. Known data issues (players without a UPID, manager/FBP_Team disagreements, `"None"` teams, duplicate MLB IDs) are listed in its report, not fixed.

## Worktree branch flow

Every agent worktree gets its own Neon branch, a copy of `main`'s data:

```bash
# new worktree + matching database branch with a copy of main's data
git worktree add ../fbp-<name> -b <name>
neon branches create --name wt-<name> --parent production
neon connection-string wt-<name> --pooled   # put in the worktree's .env.local as DATABASE_URL
neon connection-string wt-<name>            # direct string -> DATABASE_URL_UNPOOLED
# or simply, from inside the worktree:  neon checkout wt-<name> --create   (creates the branch and writes .env.local)

# done
neon branches delete wt-<name>
git worktree remove ../fbp-<name>
```

`.env` is listed in `.worktreeinclude`, so each worktree gets its own copy; replace the connection strings with the branch's.

**Never run migrations or imports against `production` from a worktree.** Only the scheduled workflow (`.github/workflows/daily-update.yml`), `db-mirror.yml` and deliberate manual runs by Zach touch `production`. Check `echo $DATABASE_URL` points at your `wt-<name>` branch before you run anything.

## Local Postgres (tests)

```bash
export DATABASE_URL=postgresql://user@127.0.0.1:5432/fbp   # any empty local database
python scripts/db/migrate.py && python scripts/db/import_json.py && python scripts/db/reconcile.py
python -m pytest tests/db -q
```

`tests/db` skip when `DATABASE_URL` is unset, and refuse to run against a non-local host (the import truncates tables) unless `FBP_TEST_ALLOW_REMOTE=1`.

## Daily workflow

After the data commit, `daily-update.yml` runs `migrate.py && import_json.py && reconcile.py --seed 1` when the `DATABASE_URL` secret is set. A reconcile FAIL fails the run; the JSON commit has already happened.

## Spec 03: rosters, contracts and WizBucks (`0003_rosters_contracts_wb.sql`)

`specs/03_rosters_contracts_wizbucks.md`. Schema and action functions only; nothing in the website, bot, API or scheduler calls them yet. (Migration 0002 is reserved for the auction spec.)

What 0003 adds:

- **Rename:** Spec 01's mirror `wb_ledger` / `wb_balances` become `wb_ledger_history` / `wb_balances_history` (2026 history, read-only). `import_json.py` and `reconcile.py` check 2 use the new names.
- **Live ledger:** new `wb_ledger` (the balance is `sum(amount)`, view `wb_balances`), plus SQL functions `wb_spend(...)` (advisory lock, balance check, refuses to go below zero except `penalty`) and `wb_credit(...)`.
- **Rules tables:** `contract_tiers` gets `next_tier`, `lower_tier`, `il_discount`; new `wb_prices`, `wb_installments`, `wb_rollover_caps`, `roster_limits`, `prospect_limits` with the 2027 seed rows. Additions beyond the spec are `wb_finish_rewards`, `contract_entry_tiers` and a few extra rows/columns; see the build report.
- **Contracts:** `contracts` (one open contract per player), `players.carried_tier`, `roster_slots`, `prospect_draft_slots`, `prospect_status`.
- **Brackets:** `team_seasons.bracket`, with the 2026 final standings seeded from `scripts/db/seed_2026_results.py` (applied by every import, so it survives reloads).
- **Actions:** `league/actions.py`, one function per action. The caller owns the transaction (`with conn.transaction():`); errors are `league.actions.RuleError`. Tests: `python -m pytest tests/league -q` (each test rolls back; test 12 uses two real connections and cleans up).

**`contracts`, `wb_ledger`, `roster_slots`, `prospect_draft_slots` and `prospect_status` are EMPTY until cutover.** `roster_assignments` keeps being reloaded from the JSON by the daily import until then. The import upserts `teams`/`players` (instead of truncating them) and never touches the live tables or `player_events` rows written by actions; if a live row points at a player that has vanished from the JSON, the import fails and rolls back.

Cutover commands (Zach only, December 15, after the final import; both are one transaction):

```bash
python scripts/db/cutover_contracts.py --dry-run --opening-balances      # list every conflict, the 12 balances (team-abbr order) and the --confirm-balances string; writes nothing
python scripts/db/cutover_contracts.py --resolutions zach.json --opening-balances --confirm-balances "80,55,..."
#   one transaction: contracts + roster_slots (keeper -> active, prospect -> farm) + one opening_balance per team
#   refuses while any conflict has no entry in zach.json ({upid: {"kind", "tier", "prospect_type"}}); IL/NA slots are set by hand afterwards
```

## Spec 02: auction (`0002_auction.sql`)

`specs/02_auction_portal.md`. Adds exactly the spec's four tables (`auction_weeks`, `auction_bids`, `auction_decisions`, `auction_results`) and the two partial unique indexes (one OB per team per week, one OB per prospect per week). It depends only on `teams` and `players` from 0001, so it runs before 0003 on a fresh database. Nothing is ever deleted from these tables; admin removal sets `removed_at`.

`auction/service.py` is the only code that may touch them (`place_bid`, `record_decision`, `get_board`, `resolve_week`, `mark_announced`, `admin_*`). **It is wired up behind a feature flag that defaults to off.** With `FBP_AUCTION_DB=1` the auction routes in `health.py` and the bot's `auction_tick` call the service through `fbp_db.py` (a small psycopg pool over the pooled `DATABASE_URL`, plus the calendar and priority feeds); with the flag unset every one of those routes takes the 2026 JSON path, unchanged. No Discord command or website code calls the service yet, and `js/auction.js` still polls the JSON file. It reads WizBucks from the live `wb_ledger`/`wb_balances` and opens prospect contracts through `league.actions`, so it is only meaningful once Spec 03's tables are live. Tests: `python -m pytest auction -q` (the service tests commit real rows on separate connections and delete them again in `finally`).

Hardening (review fixes):
- `auction_bids.seq` (bigserial) orders bids that share a `placed_at`; a check keeps `removed_at` and `removed_by` paired; a trigger refuses inserts into `auction_bids`/`auction_decisions` for a week that is not `open`.
- `0006_auction_ledger_guard.sql` (needs 0003) adds `wb_ledger_auction_win_once`, a unique index on `(upid, ref_id)` for `ref_type = 'auction' and kind = 'auction_win'`, so a week's win can't be charged twice.
- `place_bid` reads `auction_min_ob` / `auction_min_raise` from `wb_prices` for the week's season (a missing row is a `BidError`). Admin functions, `place_bid(source='admin', actor=...)` and `resolve_week` check `teams.role = 'admin'` (`NotAdmin`); `resolve_week` accepts `'scheduler'` or an admin and refuses before Sun 10:00am ET unless an admin passes `force=True`. It debits through `league.actions.wb_spend` after locking every bidding team, so a shortfall is a `RuleError` and nothing is written. `get_board` is one read-only repeatable-read snapshot.
- **Go-live setting:** `calendar["start"]` must be the Monday of the pre-season auction week, not Opening Day (read from `config/season_dates.json` by `fbp_db.auction_calendar`). **Needs Zach:** the affordability drop order (see `auction/REPLAY_NOTES.md`), and the 2027 bid-priority source -- Spec 02 wants FBP standings, or division draft seeding until those exist, and neither is in the data, so `fbp_db.priority_order` is a stopgap reading `data/standings.json` worst-rank-first.
- **No edit-amount action:** Spec 02's service list has no way to change a bid's amount, and the route cannot compose remove + add without using two transactions, so `POST /api/admin/auction/bids/update-amount` returns 501 while the flag is on.

## Ledger rules in the database (0003)

`wb_ledger` is append-only (a trigger raises on UPDATE, DELETE and TRUNCATE; fix a mistake with `admin_adjust`, kind `admin_adjustment` or `reversal`). The only bypass is a session that runs `set fbp.ledger_maintenance = 'on'` (test cleanup, a repair by the database owner); application code must never set it. A check constraint fixes the sign of `amount` by `kind`, and a deferred constraint trigger rejects any non-penalty debit that leaves a team below zero, even from a plain INSERT.

`league_windows (season, window_name, opens_at, closes_at, opened_by)` decides what is allowed: KAP actions (`keep_player`, `il_tag`, `reduce_a_tier`) need an open `KAP` window, `buy_draft_slot` and PAD-priced `assign_prospect_contract` need `PAD`, upgrades to BC need `BC_FINALIZATION`. Admins use `open_window` / `close_window` in `league/actions.py`. (The column is `window_name` because `window` is a reserved word in SQL.)

## Spec 07: lineups (`0007_lineups.sql`)

`specs/07_team_page_lineups.md`. Database layer and service only; no website, bot job or API route calls it yet.

- **Tables:** `lineup_slot_types` (the 2027 slot setup), `position_eligibility`, `lineups`, `lineup_versions` (one row per team per day, bumped by every lineup write; a save must carry the version it loaded), `mlb_games`, `mlb_roster_status` (where MLB lists each owned player: `active`, `il`, `minors`, `other`; this is what "MLB lists him on the IL" reads) and `league_settings` (`eligibility_games` 5, `lineup_future_days` 7) and `mlb_team_aliases` (OAK->ATH etc., read through the SQL function `mlb_abbr`). The IL and NA limits are `roster_limits.il_max` 5 / `na_max` 2 (Spec 03). `lineups.season` is generated from `game_date`, so the slot code is a real foreign key, and a trigger checks `slot_index` against the slot's count.
- **Service:** `lineups/service.py` is the only code that writes these tables. `validate` is the one validation function; `save_lineup` runs under the same `team:<abbr>` advisory lock as `league.actions`. Errors are `LineupError` (a `RuleError`) and `LineupConflict` (carries `.latest`).
- **Jobs it expects (none scheduled yet):** `refresh_games` (daily plus every few minutes on game days), `lock_started_games` (every minute on game days; `save_lineup` also runs it), `carryover` (12:00 AM ET), and nightly `refresh_mlb_status` then `refresh_eligibility` fed by `lineups/mlb_api.py`. The API client is never called by tests; read the rate-limit note at the top of it before scheduling anything.
- **Preseason seed:** `refresh_eligibility(conn, 2027, fetch_fielding(2026), now, source="last_season")`, then nightly in season with `source="this_season"`.
- **DH** is a position in this table (and in UTIL's `accepts`) for batters with no fielding position, such as the batter half of a two-way player.
- Tests: `python -m pytest tests/lineups -q` (each test rolls back).

## Spec 04: draft (`0004_draft.sql`)

`specs/04_draft_room.md`. Database layer and service only; no website, Discord or clock-runner process calls it yet.

- **Tables:** `drafts`, `draft_slots`, `draft_picks_live`, `draft_clock`, `draft_forklift`, `draft_boards`, `draft_rankings`, `draft_pool_overrides`, `notifications` (the outbox), `draft_events`, `draft_add_requests`, plus `draft_award_amounts` (speed-award prize money as data). Also enables `unaccent` and `pg_trgm`.
- **Name note:** the spec renames Spec 01's `draft_picks` to `draft_slots` and calls the new picks table `draft_picks`. The daily import still truncates and reloads `draft_picks` from `draft_order_2026.json` until cutover, so that mirror is left alone and the spec's picks table is **`draft_picks_live`**. At cutover: drop or rename the mirror, then rename `draft_picks_live` to `draft_picks`.
- **One eligibility rule:** SQL function `draft_pool(draft_id, for_round)` returns every player with `eligible` and a plain-text `reason`. Ownership is read from `contracts` and, **until the Dec 15 cutover, also from the open `roster_assignments` rows** (marked in the function; remove that join at cutover).
- **Service:** `draft/service.py` (`create_draft`, `slots_from_mirror`, `start`/`pause`/`resume`, `make_pick`/`make_picks`, `clock_tick`, `undo_last_pick`/`swap_player`/`void_pick`, `set_forklift`, `set_board`, `upload_rankings`, `request_add_player`/`decide_add_request`, `search`, `state`, `pay_speed_awards`). Contracts and `player_events` go through `league/actions.py`. Tests: `python -m pytest tests/draft -q` (they commit real rows on a few connections and delete them again in `finally`).
- **Simulator:** `python scripts/draft/simulate.py --draft prospect-2027 --clock 2 --setup --database-url URL` runs a whole draft with 12 fake managers, a clock runner that is killed and restarted, and admin pauses, then checks nothing stalled or was double-picked and contracts match picks. It refuses unless the draft is `is_test`; use a Neon test branch or a scratch database. `--cleanup` removes its rows (it sets `fbp.ledger_maintenance`, so local databases only).
- **Cutover rename (Zach only, once, with the Dec 15 cutover):** `psql "$DATABASE_URL_DIRECT" --single-transaction -v ON_ERROR_STOP=1 -f scripts/db/cutover_draft_rename.sql` renames the Spec 01 mirror `draft_picks` to `draft_picks_mirror_2026`, renames `draft_picks_live` to `draft_picks` (indexes, constraints, sequence too) and recreates `draft_pool()` for the new name (it is the only function that names the table; a test keeps its copy identical to the migration's). In the same change: set `PICKS_TABLE = "draft_picks"` in `draft/service.py` (the one place the service names the table), change the same table name in `trades/service.py` (one query), and **remove `draft_picks` from `scripts/db/import_json.py`** (its `build_draft_picks` step and truncate list), or the next daily import truncates the live picks. A test runs the rename SQL in a rolled-back transaction, then starts a draft, makes a pick and reads `draft_pool()` and `state()` on the renamed table.
- **Test-draft branch guard:** `create_draft(is_test=True)` and `simulate.py --setup/--cleanup` refuse unless the database host is local (127.0.0.1, localhost, a unix socket) or the `league_settings` row `branch` is `"test"`. On the Neon test branch set it once: `insert into league_settings (key, value) values ('branch', '"test"') on conflict (key) do update set value = excluded.value;`. `main` and the worktree branches never get that row. (`league_settings` is created by `0007_lineups.sql`; if it is missing the guard treats the branch as not-test.)
- **Defaults and rules in the schema:** `draft_defaults` holds the clock per draft type (Prospect 240 s, Keeper 180 s, forklift 10 s); a trigger fills `drafts.clock_seconds` when it is null, so the numbers live nowhere else. `draft_picks_live` refuses a pick in a skipped slot and a `contract_tier` that is not BC/DC (prospect) or a `contract_tiers` row (keeper). `draft_clock` can't hold a deadline and a paused remainder at once. One auto-pause per team per draft is a unique index. `notifications.next_attempt_at` is the retry-backoff time the sender will read.
- **Pool check and boards:** `draft_pool(draft_id)` with no round also returns `eligible_rounds` (`all`, `open_rounds_only` = not FYPD eligible, so out of rounds 1-2, or `none`) and `round_note` ("Not FYPD eligible (rounds 1-2)") for the Excluded tab. `import_boards_from_app_state(conn, 2026, target_season=2027)` seeds the 2027 big boards from the mirror's `manager_boards_2026` as "Any pick" lists and reports unknown players and teams.
- **Known gap until cutover:** a player created by "add a player" lives only in `players`; the daily import deletes `players` rows that are not in `combined_players.json` (and fails if a contract points at one). Add such a player to the JSON too, or run this after the cutover.

## Spec 05: trades (`0005_trades.sql`)

`specs/05_trade_portal.md`. Database layer and service only; no website, Discord command or API route calls it yet.

- **Name note:** the daily import still truncates and reloads the mirror `trades` / `trade_transfers` until cutover, so the spec's tables are **`trades_live`**, **`trade_parties`**, **`trade_items`** and **`trade_comments`**. At cutover: run `cutover_trades.py`, then drop the mirror tables and rename `trades_live` to `trades`. Ids are `T27-0001` (`trade_next_id(season)` and `trade_sequences`). Constraints beyond the spec's: a status agrees with its `close_kind` and approval fields, 2 or 3 parties, both teams of an item are parties, no duplicate items, a manual trade carries a `note` (a real column) and a `client_token`, a pick points at a real `draft_slots` row, `approved_by` is a team abbreviation or `'ADMIN'`, one imported trade per `raw->>'legacy_trade_id'`. Two deferred/row triggers: `accepted` and `approved` need every party's `accepted_at`, and `trade_items` / `trade_parties` only change while the trade is `pending` (imports are exempt from both; a session with `fbp.trade_maintenance = 'on'` skips the second, for test cleanup only). `trade_items.raw` is an addition (the imported 2026 picks).
- **Service:** `trades/service.py` (the old 2026 code stays in `trade/`). `validate(conn, trade, stage)` is the one rule function for `build`, `send`, `accept` and `approve`; `build_preview` is the impact panel. Then `send`, `accept`, `reject`, `withdraw`, `counter`, `comment`, `thread`, `expire_pending` (the hourly sweep: pending offers after 7 days, accepted never), `approve`, `admin_manual_entry`, `void`, `admin_queue`. Players move through `league.actions.transfer`, WizBucks through `wb_spend` / `wb_credit` (`trade_out` / `trade_in`) and, for a void, `admin_adjust` reversals. Readers take a `viewer`: `get_trade(conn, id, viewer)`, `thread(conn, id, viewer)`, `build_preview(conn, trade, viewer)` and `admin_queue(conn, viewer)` only serve a party or an admin (`NotAParty`, a `NotAdmin`, API 403). `thread()` returns `team_abbr = None` for `kind = 'system'` comments (the column keeps the sender's team only because it is a foreign key); the UI shows those as the system, not as a team. Errors are `TradeError` (a `RuleError`), `NotAdmin` (API: 403) and `TradeConflict` (409; the trade was auto-withdrawn and that is committed).
- **What decides what:** the `TRADE` row in `league_windows` is the trade window; picks also need the `KAP` window open. **`closes_at` is the exact cut-off instant** (open from `opens_at` up to, not including, `closes_at`): a window meant to allow 11:59 PM ET must close at **00:00 ET the next day**, and an offer at 11:59:30 PM then goes through. Ownership is read from `contracts`. Until the Dec 15 cutover the open `roster_assignments` row is used only to word the "is on X, not Y" message (marked in the code; remove at cutover); a player with no contract row is a block (`no_contract`) at build, send, accept and approve, so an offer cannot be accepted and then die at approve. A pick in a slot with a `skip_reason` can't be traded. **Bought in** for round N = the team has a slot in round N (`original_team`) whose `skip_reason` is not `'buyin_unpaid'`, so keeper and taxed slots still count. Buy-in for R1-3 is read from the keeper draft's `draft_slots`. Admins are `teams.role = 'admin'`.
- **Manual entry and money:** `admin_manual_entry(conn, trade, admin, note, client_token)` validates at stage `approve` plus the structure checks (no promised-WB subtraction, no 12-offer cap); a repeated `client_token` returns the existing trade and changes nothing. WizBucks inside one trade are netted per team (a chain A to B to C needs nothing from B), credits are written before debits, and an A to B plus B to A pair is blocked (`wb_pair`). Pick moves in approve, void and manual entry take `pg_advisory_xact_lock(hashtext(draft_id))` after the team locks. `counter()` must keep every team of the original offer and leaves the original out of the promised-WB sum. `void()` refuses imported 2026 trades (their money is in `wb_ledger_history`, their picks have no slots).
- **For Zach:** at build and send a team's WizBucks are reduced by what it has already promised in its other accepted-by-it offers (`committed_wb`). That is kept as a product decision: it stops one pile of WB being promised twice, but it also blocks a legal offer the team could only pay once. Say if you want it to warn instead of block.
- **Cutover (Zach only, once):** `python scripts/db/cutover_trades.py --dry-run` reshapes the mirror trades into `T26-0001..` with source `import` inside one transaction and rolls it back; without `--dry-run` it commits. It refuses if `trades_live` already has rows. Rows that map to `pending` / `accepted` (mirror `pending`, `admin_review`) are live work: they are listed as `NOT IMPORTED` and left for you to decide (there are none in the current data). A missing party on an approved trade gets a synthesized `accepted_at`.
- Tests: `python -m pytest tests/trades -q` (the service tests commit real rows and delete them again in `finally`; the cutover test builds a scratch database).
