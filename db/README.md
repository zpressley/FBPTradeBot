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
