# Spec 01: Neon Foundation (schema v1 + shadow import)

**Status:** Ready to build
**Target:** October 31, 2026 (Roadmap: "New database set up with all league data copied in")
**Owner:** Zach (planning in Cowork, code in Claude Code)
**Read first:** `WARP.md` (rules, especially Data Identity and External API Rate Limits)

## Goal

Stand up a Neon Postgres database, create the v1 schema, and build a repeatable import that copies the current `data/*.json` files into it. At the end of this spec the database is an exact, verified mirror of the JSON. Nothing in the bot, the API or fbp-hub reads from or writes to it yet.

This is "shadow mode." The JSON stays the source of truth through October. Specs 02+ move one area at a time (trades, WizBucks, auction, and so on) so the database becomes the source of truth for that area.

## Out of scope

- Changing any code that reads or writes JSON (about 99 Python files write to `data/` today)
- The fbp-hub website, the Discord bot, the Cloudflare Worker
- Scoring engine tables (lineups, matchups, game stats). That's a later spec.
- Removing `token.json` from git. Spec 02 covers moving tokens into the database.

## Design rules (why the data drifted, and how the database stops it)

Most of the mismatches in the JSON (two team fields that disagree, a stale `status` bracket, a trade applied by hand with no record) came from agents editing files directly and from writes that died partway through a git push. The database should make those mistakes impossible, not just detectable:

1. **One fact, one place.** A player's team lives only in `roster_assignments.team_abbr`. Display names, sort order and costs are looked up, never copied onto the player.
2. **All or nothing.** Every league action (trade, bid, keeper submission) runs in one database transaction. A dropped connection rolls the whole thing back instead of leaving half of it applied.
3. **Agents never write to `main`.** Worktrees get their own Neon branch (section 6). Only the scheduled import (now) and the live app (later) write to `main`.
4. **Constraints over conventions.** Foreign keys, unique indexes and check constraints reject bad data at write time. A rule that only lives in a markdown file or an agent's prompt will eventually get skipped.

## 1. Neon setup (Zach does this by hand, about 10 minutes)

1. Create a Neon project named `fbp`, region **AWS us-east-1** (closest to Railway's us-east4).
2. Postgres version: latest offered (17 or newer).
3. Branches:
   - `main`: production. Only the import job and, later, the live app write here.
   - `dev`: long-lived branch off `main` for manual poking.
   - Worktree branches are created on demand (see section 6).
4. Create a role `fbp_app` for the app. Keep the default owner role for migrations.
5. Save two connection strings in a local `.env` (never committed):
   - `DATABASE_URL`: the **pooled** string (host contains `-pooler`). Used by the app and the import.
   - `DATABASE_URL_DIRECT`: the **direct** string. Used by migrations.
6. Add both as GitHub repo secrets. Don't add them to Railway yet.
7. Install `neonctl` locally (`npm i -g neonctl`) and run `neonctl auth`.

## 2. Repo layout

```
db/
  migrations/
    0001_init.sql
  README.md            # how to migrate, import, and branch
scripts/db/
  migrate.py           # applies db/migrations/*.sql in order, tracks them in schema_migrations
  import_json.py       # full reload of data/*.json into the database
  reconcile.py         # compares database vs JSON, prints a report, exits non-zero on mismatch
```

Dependencies: add `psycopg[binary]>=3.2` to `requirements.txt`. No ORM, no migration framework. Plain SQL files and a small runner (WARP.md: simplicity first).

## 3. Schema v1

Conventions:
- `upid` is the player key everywhere. Store it as `text` (values are numeric strings today, but don't assume that holds).
- Team key is the 2 to 4 letter abbreviation (`WIZ`, `DMN`, ...). Full team names only live in `teams.name`.
- Timestamps are `timestamptz`. Treat naive timestamps in the JSON as `America/New_York`.
- Every table imported from JSON gets `imported_at timestamptz default now()`.
- Keep a `raw jsonb` column on tables where the JSON shape is messy, so nothing is lost while the columns settle.

### Reference

**`teams`**
`abbr text pk`, `name text unique not null`, `discord_id text`, `yahoo_team_id text`, `division text`, `role text`
Source: `config/managers.json` → `teams`.

**`team_name_aliases`**
`alias text pk`, `team_abbr text fk teams`
Seed with every full name and the abbreviation itself, plus historical names seen in the logs (for example `Jeppie Torrs` → `JEP`). The importer resolves every team reference through this table and reports anything it can't resolve.

**`seasons`**
`year int pk`, `dates jsonb`
Source: `config/season_dates.json` (store the whole file as `dates` for now).

**`team_seasons`**
`team_abbr`, `season`, `final_rank int`, `standing int`, `division text`, `kap_rollover int`, pk (`team_abbr`, `season`)
Source: `managers.json` (`final_rank_2025` → season 2025, `kap_rollover_2026` → season 2026).

### Players

**`players`**
`upid text pk`, `name text not null`, `mlb_id int unique`, `yahoo_id text`, `bbref_id text`, `fangraphs_id text`, `birth_date date`, `bats text`, `throws text`, `height text`, `weight int`, `mlb_team text`, `positions text` (as-is, for example `2B,SS`), `mlb_primary_position text`, `player_type text` (`MLB`, `Farm`, `RETIRED`, `JAIL`), `fypd bool`, `rank int`, `raw jsonb`
Source: `data/combined_players.json` (6,819 rows).
Rows with a blank `upid` (5 today): don't insert. List them in the reconcile report.

**`player_aliases`**
`alias_normalized text`, `upid text fk players`, pk (`alias_normalized`, `upid`)
Source: `data/upid_database.json` → `name_index`. Normalize with lowercase + accents stripped + punctuation removed. Replaces fuzzy matching against raw names.

### Ownership and contracts

**`contract_tiers`** (seeded by the migration, not imported)
`tier text pk`, `family text` (`FC`, `VC`, `TC`, `P`), `sort_order int`, `keeper_cost int`

| tier | family | sort_order | keeper_cost |
|---|---|---|---|
| FC-2 | FC | 0 | 125 |
| FC-1 | FC | 1 | 85 |
| VC-2 | VC | 2 | 55 |
| VC-1 | VC | 3 | 35 |
| TC-2 | TC | 4 | 25 |
| TC-1 | TC | 5 | 15 |
| TC-R | TC | 6 | 5 |
| TC-BC-1 | TC | 6 | 5 |
| TC-BC-2 | TC | 6 | 5 |
| P | P | 7 | null |

This replaces the `[n]` bracket in the old `status` field, which only existed to make Google Sheets sort tiers correctly. Costs come from the 2026 Constitution, Article 2. `roster_assignments.contract_tier` gets a foreign key to this table once the first import is clean.


**`roster_assignments`**
`id bigserial pk`, `upid fk players`, `team_abbr fk teams`, `contract_type text`, `contract_tier text`, `status_raw text`, `valid_from timestamptz`, `valid_to timestamptz null`, `source text`
One open row (`valid_to is null`) per owned player = the current roster. v1 imports only the current snapshot with `valid_from = import time` and `source = 'json_import'`. Later specs close and open rows on every move, which gives a full ownership history.

Add a partial unique index: one open assignment per `upid`.

Value mapping from `combined_players.json`:

| JSON field | JSON values seen | Column |
|---|---|---|
| `FBP_Team` and `manager` via aliases (when they disagree, see below) | `WIZ` / `Whiz Kids` | `team_abbr` |
| `contract_type` | `Keeper Contract`, `Purchased Contract`, `Blue Chip Contract`, `Development Cont.` | `contract_type`: store as `keeper`, `purchased`, `blue_chip`, `development` |
| `years_simple` | `P`, `TC R`, `TC BC-1`, `TC 1`, `TC 2`, `VC 1`, `VC 2`, `FC 1`, `FC 2` | `contract_tier`: store as `P`, `TC-R`, `TC-BC-1`, `TC-1`, `TC-2`, `VC-1`, `VC-2`, `FC-1`, `FC-2` |
| `status` | `[5] TC1`, `[7] P`, ... | `status_raw` as-is, for reference only. The bracket was a sort key for Google Sheets. Sort by `contract_tiers.sort_order` instead. |

Any value not in the table above: import it unchanged and flag it in the reconcile report. Never drop or guess.

**When `manager` and `FBP_Team` disagree** (decided 10/27): neither field wins automatically. The reconcile report lists each disagreement next to that player's latest `player_log` and `transactions_history` entries as evidence, and an admin picks the right team. The row imports with `team_abbr` null until it's resolved.

Known data issues to report (not fix) on first import:
- 3 players where `manager` and `FBP_Team` disagree or `manager` holds an old team name (for example Michael Arroyo, `Jeppie Torrs` vs `JEP`)
- 30 rows with `FBP_Team = "None"` as a string

Use a DB check constraint on `contract_tier` only after the first clean import. Until then, leave it as `text` so imports don't fail.

### Money

**`wb_ledger`**
`txn_id text pk`, `ts timestamptz not null`, `team_abbr fk teams`, `amount int not null` (signed), `balance_before int`, `balance_after int`, `txn_type text not null`, `description text`, `related_upid text`, `season int`, `metadata jsonb`
Source: `data/wizbucks_transactions.json` (298 rows, 16 types).

**`wb_balances` (view)**
Current balance per team = `balance_after` of the latest ledger row per team.

Verified 9/25: the latest `balance_after` per team matches `data/wizbucks.json` for all 12 teams. Reconcile must keep proving that.

Note for later: once WizBucks moves to the database (spec 03), the balance should come from the ledger, not a stored number. v1 just mirrors.

### Trades

**`trades`**
`trade_id text pk`, `status text`, `initiator_team text`, `teams text[]`, `created_at`, `expires_at`, `manager_approved_at`, `processed_at`, `data_applied_at timestamptz`, `admin_decision_by text`, `rejection_reason text`, `rejected_by text`, `raw jsonb`
Source: `data/trades.json` (95 trades) + `data/pending_trades.json`.

**`trade_transfers`**
`id bigserial pk`, `trade_id fk trades`, `type text` (`player`, `wizbucks`, `pick`, `buyin`, ...), `upid text`, `from_team text`, `to_team text`, `amount int`, `raw jsonb`
Source: each trade's `transfers` array.

### Event history

**`player_events`**
`id text pk`, `season int`, `ts timestamptz`, `update_type text`, `event text`, `source text`, `admin text`, `upid text` (nullable, not an FK: old history rows often have none), `player_name text`, `owner_raw text`, `owner_abbr text`, `contract text`, `status_raw text`, `raw jsonb`
Sources:
- `data/transactions_history.json` (11,194 rows, seasons 2012 to 2025)
- `data/player_log.json` (4,069 rows, 2026)

If `id` is missing, generate a deterministic one: sha1 of `season|timestamp|upid|player_name|update_type|event`. Dedupe on `id`. Report how many were generated.

### Drafts and prospects

**`draft_picks`**
`draft text`, `season int`, `round int`, `pick int`, `team_abbr text`, `round_type text`, `result jsonb`, pk (`draft`, `season`, `round`, `pick`)
Sources: `data/draft_order_2026.json` (403 rows), `data/historical/2025/draft_order_2025.json`.

**`top100_snapshots`**
`snapshot_date date`, `rank int`, `upid text`, `mlb_id int`, `name text`, `org text`, `position text`, `level text`, `eta text`, `raw jsonb`, pk (`snapshot_date`, `rank`)
Sources: `data/top100_prospects.json` (use the file's modified date, or today if unknown) and `data/historical/2025/Nov 1 2025 MLB Top 100 Prospects.csv` as `2025-11-01`.
This table is what the November 1, 2026 Blue Chip snapshot lands in.

**`player_season_stats`**
`upid`, `season`, `level`, `stat_type`, `source`, `mlb_id`, `mlb_team`, then the numeric stat columns as they appear, plus `raw jsonb`
Source: `data/player_stats.json` (23,197 rows). Pick the pk after looking at the data. `(upid, season, level, stat_type, source)` is the likely candidate, and the importer should check it for duplicates first.

### Catch-all app state

**`app_state`**
`key text pk`, `value jsonb not null`, `updated_at timestamptz`
One row per small state file: `auction_current`, `auction_resolved_state`, `trade_id_state`, `draft_state_keeper_2026`, `draft_state_prospect_2026`, `kap_submissions`, `pad_submissions_2026`, `manager_boards_2026`, `standings`, `standings_refresh_backoff_state`, `roster_sync_state`, `team_colors`, `site_theme`, `team_planner_plans`, `manager_notes`, `player_add_requests`, `forklift_mode_*`.
These get real tables when their area moves over. Until then they're preserved whole.

### History (decided 10/27: everything comes over)

Every file under `data/historical/` is imported in v1. Draft orders and results go into `draft_picks`, Top 100 lists into `top100_snapshots`, rosters into `roster_assignments` with closed date ranges, and anything that doesn't fit an existing table lands in `history_raw` (`source_file text`, `row_num int`, `row jsonb`, pk (`source_file`, `row_num`)) so nothing is lost. The reconcile report lists row counts per file.

### Skip for v1

Test files (`*_test.json`, `*_TEST.json`, `*_mock.json`), `sheet_players.json` (retired Sheets), `yahoo_*` raw pulls, `mlb_bio_dump.json`, CSV reports, `service_stats.json`, `flagged_for_review.json`, `graduation_eligible.json`, `prospect_stats.json` (regenerated by jobs), `backups/`.

## 4. Import behavior

`scripts/db/import_json.py`:
- Reads straight from `data/` and `config/`. Doesn't modify either.
- Runs in **one transaction**: truncate the imported tables, reload everything, commit. If anything fails, roll back and leave the database untouched.
- Load order: teams, aliases, seasons, players, player_aliases, roster_assignments, wb_ledger, trades + transfers, player_events, draft_picks, top100, stats, app_state.
- Prints a summary: rows per table, rows skipped and why, unmapped values.
- Flags: `--dry-run` (parse and report, no writes), `--database-url` (defaults to `DATABASE_URL`).

## 5. Reconcile

`scripts/db/reconcile.py` checks and prints PASS/FAIL for each:
1. Row counts per table vs source file (minus reported skips)
2. WizBucks: `wb_balances` equals `data/wizbucks.json` for all 12 teams
3. Rosters: owned player count per team equals `combined_players.json` per team
4. Every `roster_assignments.upid` exists in `players`
5. Every team reference resolved (no unknown aliases)
6. Every trade transfer with a `upid` points at a real player (report, don't fail, for historical trades)
7. Spot check: 20 random players, all fields equal between JSON and database

Exit code 1 on any FAIL.

## 6. Branch per worktree

Document in `db/README.md`:

```bash
# new worktree + matching database branch with a copy of main's data
git worktree add ../fbp-<name> -b <name>
neonctl branches create --name wt-<name> --parent main
neonctl connection-string wt-<name> --pooled   # put in the worktree's .env as DATABASE_URL

# done
neonctl branches delete wt-<name>
git worktree remove ../fbp-<name>
```

Add `.env` to `.worktreeinclude` so each worktree gets its own copy.

Rule for agents: **never run migrations or imports against `main` from a worktree.** Only the scheduled job (below) and deliberate manual runs touch `main`.

## 7. Keep the mirror fresh

Add a step to `.github/workflows/daily-update.yml`, after the existing data refresh:

```
python scripts/db/import_json.py && python scripts/db/reconcile.py
```

using the `DATABASE_URL` secret. If reconcile fails, the workflow fails and the JSON pipeline is unaffected (the import runs after the data commit).

## Acceptance criteria

- [ ] Neon project `fbp` exists with `main` and `dev` branches
- [ ] `python scripts/db/migrate.py` creates the full v1 schema on an empty branch, and re-running it is a no-op
- [ ] `python scripts/db/import_json.py` loads everything in under 2 minutes, and re-running it gives identical row counts
- [ ] `python scripts/db/reconcile.py` passes all 7 checks against `main`
- [ ] Reconcile report lists the known data issues (5 players without a UPID, 3 team mismatches, 30 `"None"` teams) instead of hiding them
- [ ] `db/README.md` covers migrate, import, reconcile, and the worktree branch flow
- [ ] No existing bot, API or pipeline behavior changed
- [ ] Daily workflow runs the import and reconcile, and they pass for 3 days in a row

## Open questions (answer before or during the build)

1. ~~What does the bracket number in `status` mean?~~ Answered 9/25: it was a Google Sheets sort key. Replaced by `contract_tiers.sort_order`. Note that `status` is also stale in places (60 rows say `[9] P`, one says `[7] TC1`, 3,014 have no bracket), so nothing should read it after cutover.
2. ~~**`manager` vs `FBP_Team`.**~~ Answered 10/27: it depends. Surface every disagreement with evidence from the player log, and an admin decides.
3. ~~**Historical CSVs.**~~ Answered 10/27: everything comes over in v1. See "History" above.
