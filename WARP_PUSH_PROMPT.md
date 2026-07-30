Push pending commits in fbp-trade-bot. This commit already exists locally and is fully tested/verified — this task is ONLY to get it onto origin/main safely. Do not re-run, re-generate, or "fix" anything; do not run any data pipeline, backfill, sync, or graduation scripts. Do not touch token.json.

fbp-hub has nothing pending right now.

The previous handoff (trade-commit reliability fix + 2-trade backfill) already made it to origin/main — last-known origin/main tip is `d54f64e`. Nothing to do for that batch.

## fbp-trade-bot

Local main is 1 commit ahead of the last-known origin/main (`d54f64e`):
- `bf67378` — Restore 8 farm players wiped by July 13 stale-snapshot incident

**Data-only change, but touches live rosters.** Zach cross-checked a personal trade-tracking list against the live site and found 8 farm/prospect players still showing their pre-trade owner, weeks after their trades were approved. Root cause: this is a second symptom of the same corruption already partly fixed by `e97bee3` back on 7/15 — commit `902c787` (2026-07-13) had substituted a stale ~June 10 snapshot of `combined_players.json`, silently reverting every player-ownership change applied between 2026-06-10 and 2026-07-13. The earlier fix only caught contract-type corruption for an enumerated list of players; it never caught this ownership-reversion angle because no one had flagged it yet.

- `data/combined_players.json` — restores `FBP_Team`/`manager` for 8 farm players (Andrew Fischer, Xavier Neyens, Kevin Defrank, Ronny Cruz, Dakota Jordan, Harry Ford, Jaxon Wiggins, Eduardo Tait) to their correct post-trade owner. Every write was guarded by an exact-match check against the known-corrupted value before touching anything — see `scripts/restore_farm_trade_reversion_2026_07.py` for the full reasoning and the dry-run output.
- `data/player_log.json` — re-adds the 8 original "Trade" log entries that were wiped by the same corrupting commit, pulled verbatim from commit `bda546b` (last-good, immediately before the corruption) rather than reconstructed.

Two MLB players from the same batch of trades (Jake Burger, Teoscar Hernández) were deliberately **not** touched — they show no current owner too, but that's confirmed to be a real, later Yahoo drop, unrelated to the corruption. Also not touched: `TRADE-250426_2025-024` (Cam Caminiti) — a separate, unrelated situation where the counterparty never accepted in the first place; it'll expire on its own via the new hourly sweep.

Verification already done locally: dry-run of the restore script reviewed before applying (zero unexpected values — every field matched the predicted corrupted value exactly), JSON re-validated as parseable after writing, and diff reviewed to confirm only the intended 16 lines in `combined_players.json` and 8 new entries in `player_log.json` changed (a first pass over-escaped unicode names and got amended out — final diff is clean).

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -5` — check whether origin/main is still at `d54f64e`, or has moved forward (e.g. more standings/daily-update commits from Railway, which happen automatically).
3. If origin/main is unchanged: `git push origin main` (plain fast-forward, no force needed).
4. If origin/main has moved forward: do a normal `git merge origin/main` or `git rebase origin/main` — **not** a rebase with `-X ours`/`-X theirs`, and no `git push --force`. If `data/combined_players.json` or `data/player_log.json` show a real conflict (as opposed to git auto-resolving a clean line-level change), stop and flag Zach — these are live roster records and a wrong auto-merge could re-corrupt the same 8 players or double-append a log entry.
5. After pushing, verify: `git log --oneline origin/main -1` matches local HEAD (`bf67378`, unless you had to merge).
6. Spot check after Railway redeploys: Xavier Neyens and Kevin Defrank should show as CFL; Andrew Fischer, Ronny Cruz, Dakota Jordan, Harry Ford, Jaxon Wiggins, and Eduardo Tait should all show as DRO. Easiest check: `python3 -c "import json; d=json.load(open('data/combined_players.json')); [print(p['name'], p['FBP_Team']) for p in d if p.get('upid') in ('7968','7964','7796','7532','7647','3499','6725','7146')]"`.

## After pushing

Railway will redeploy fbp-trade-bot from the new main. This is a data-only change (no code touched), so runtime behavior doesn't change — just confirm the bot comes up clean.
