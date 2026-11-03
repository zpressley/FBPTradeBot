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
python scripts/db/cutover_contracts.py --dry-run                 # build contracts from roster_assignments, print every conflict, write nothing
python scripts/db/cutover_contracts.py                           # writes only if there are no conflicts
python scripts/db/cutover_contracts.py --allow-conflicts         # writes the rest, skipping rows that can't be built without guessing
python scripts/db/cutover_contracts.py --opening-balances --dry-run   # show the 12 numbers from data/wizbucks.json
python scripts/db/cutover_contracts.py --opening-balances        # one opening_balance row per team (refuses if wb_ledger has rows)
```
