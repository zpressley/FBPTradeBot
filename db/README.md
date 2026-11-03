# Database (Neon Postgres, shadow mode)

Nothing reads from this database yet. `data/*.json` is still the source of truth; the database is a verified mirror that the daily workflow refreshes (`specs/01_neon_foundation.md`).

## Connection strings

Both live in a local `.env` (never committed) and in GitHub repo secrets.

| Variable | Neon string | Used by |
|---|---|---|
| `DATABASE_URL` | pooled (host contains `-pooler`) | the app (later), `import_json.py`, `reconcile.py` |
| `DATABASE_URL_DIRECT` | direct | `migrate.py` (falls back to `DATABASE_URL` if unset) |

The scripts read the environment, not `.env` itself: `set -a; source .env; set +a`. Each script also takes `--database-url`.

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
neonctl branches create --name wt-<name> --parent main
neonctl connection-string wt-<name> --pooled   # put in the worktree's .env as DATABASE_URL
neonctl connection-string wt-<name>            # direct string -> DATABASE_URL_DIRECT

# done
neonctl branches delete wt-<name>
git worktree remove ../fbp-<name>
```

`.env` is listed in `.worktreeinclude`, so each worktree gets its own copy; replace the connection strings with the branch's.

**Never run migrations or imports against `main` from a worktree.** Only the scheduled workflow (`.github/workflows/daily-update.yml`) and deliberate manual runs by Zach touch `main`. Check `echo $DATABASE_URL` points at your `wt-<name>` branch before you run anything.

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

`auction/service.py` is the only code that may touch them (`place_bid`, `record_decision`, `get_board`, `resolve_week`, `mark_announced`, `admin_*`). **Nothing calls it yet**: no API route, Discord command, scheduler job or website code uses it. It reads WizBucks from the live `wb_ledger`/`wb_balances` and opens prospect contracts through `league.actions`, so it is only meaningful once Spec 03's tables are live. Tests: `python -m pytest auction -q` (the service tests commit real rows on separate connections and delete them again in `finally`).

## Ledger rules in the database (0003)

`wb_ledger` is append-only (a trigger raises on UPDATE, DELETE and TRUNCATE; fix a mistake with `admin_adjust`, kind `admin_adjustment` or `reversal`). The only bypass is a session that runs `set fbp.ledger_maintenance = 'on'` (test cleanup, a repair by the database owner); application code must never set it. A check constraint fixes the sign of `amount` by `kind`, and a deferred constraint trigger rejects any non-penalty debit that leaves a team below zero, even from a plain INSERT.

`league_windows (season, window_name, opens_at, closes_at, opened_by)` decides what is allowed: KAP actions (`keep_player`, `il_tag`, `reduce_a_tier`) need an open `KAP` window, `buy_draft_slot` and PAD-priced `assign_prospect_contract` need `PAD`, upgrades to BC need `BC_FINALIZATION`. Admins use `open_window` / `close_window` in `league/actions.py`. (The column is `window_name` because `window` is a reserved word in SQL.)

## Spec 07: lineups (`0007_lineups.sql`)

`specs/07_team_page_lineups.md`. Database layer and service only; no website, bot job or API route calls it yet.

- **Tables:** `lineup_slot_types` (the 2027 slot setup), `position_eligibility`, `lineups`, `lineup_versions` (one row per team per day, bumped by every lineup write; a save must carry the version it loaded), `mlb_games`, `mlb_roster_status` (where MLB lists each owned player: `active`, `il`, `minors`, `other`; this is what "MLB lists him on the IL" reads) and `league_settings` (`eligibility_games` 5, `il_slots` 5, `na_slots` 2). `lineups.season` is generated from `game_date`, so the slot code is a real foreign key, and a trigger checks `slot_index` against the slot's count.
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
- **Known gap until cutover:** a player created by "add a player" lives only in `players`; the daily import deletes `players` rows that are not in `combined_players.json` (and fails if a contract points at one). Add such a player to the JSON too, or run this after the cutover.
