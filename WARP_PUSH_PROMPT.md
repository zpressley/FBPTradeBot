Push pending commits in fbp-trade-bot. These commits already exist locally and are fully tested/verified — this task is ONLY to get them onto origin/main safely. Do not re-run, re-generate, or "fix" anything; do not run any data pipeline, backfill, sync, or graduation scripts. Do not touch token.json.

fbp-hub has nothing pending right now — local main and origin/main are both at `c25fc75`. No action needed there.

## fbp-trade-bot

Local main is 2 commits ahead of the last-known origin/main (`0bf7d8e`):
- `2f0b884` — Fix trade-commit reliability bug + add expiry sweep
- `31a0b68` — Backfill 2 trades stuck by the fire-and-forget commit bug

**Unlike the last handoff, these are NOT docs-only.** They touch real application code and live production data:

- `trade/trade_store.py`, `health.py`, `commands/trade_logic.py` — fixes a real bug where trade git-commits could report success without confirming the push actually happened, causing a manager's trade acceptance to silently get lost. Also adds an hourly sweep that expires stale trades, and improves the Discord admin-approve/reject buttons to show a clear message instead of silently doing nothing when a trade's status has drifted from what the card implies.
- `data/trades.json`, `data/combined_players.json`, `data/player_log.json` — backfills two trades that got stuck by the above bug (`TRADE-060726_1243-050`, `TRADE-150526_2004-035`). Both were individually verified against current live data before being marked resolved (see `scripts/backfill_trade_060726_1243_050.py` and `scripts/backfill_trade_150526_2004_035.py` for the full reasoning — each is idempotent and guards every write with an exact-match check on current state).

Verification already done locally: `python3 -m py_compile` on all changed `.py` files (clean), all touched JSON files re-validated as parseable, and the specific player/trade records spot-checked against expected values.

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -5` — check whether origin/main is still at `0bf7d8e`, or has moved forward (e.g. more standings/daily-update commits from Railway, which happen automatically).
3. If origin/main is unchanged: `git push origin main` (plain fast-forward, no force needed).
4. If origin/main has moved forward: do a normal `git merge origin/main` or `git rebase origin/main` — **not** a rebase with `-X ours`/`-X theirs`, and no `git push --force`. If `data/trades.json`, `data/combined_players.json`, `data/player_log.json`, or `data/wizbucks_transactions.json` show a real conflict (as opposed to git auto-resolving a clean append), stop and flag Zach — these are live financial/roster records and a wrong auto-merge could double-apply or drop a transaction. Code file conflicts are unlikely (no one else should be editing `trade_store.py`/`health.py`/`trade_logic.py` right now) but treat the same way if they turn up.
5. After pushing, verify: `git log --oneline origin/main -1` matches local HEAD (`31a0b68`, unless you had to merge).
6. Spot check after Railway redeploys: open the two backfilled trades via the admin API/Discord and confirm `TRADE-060726_1243-050` and `TRADE-150526_2004-035` both show `status: approved`. Watch Railway logs briefly for `✅ Trade committed to git` / `🔥 COMMIT_WORKER_GIVE_UP` lines to confirm the new blocking-commit behavior is live and not erroring.

## After pushing

Railway will redeploy fbp-trade-bot from the new main. This time the deploy actually changes runtime behavior (not just docs) — worth a quick look at the first few log lines after restart to confirm the bot comes up clean and the new "Trade expiry sweep task started (hourly)" log line appears.
