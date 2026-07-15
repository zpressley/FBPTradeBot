Push pending commits in fbp-trade-bot and fbp-hub. These commits already exist locally and are fully tested/verified — this task is ONLY to get them onto origin/main safely. Do not re-run, re-generate, or "fix" anything; do not run any data pipeline, backfill, sync, or graduation scripts. Do not touch token.json.

## fbp-trade-bot

Local main is 2 commits ahead of the last-known origin/main (11ff27c):
- b53607c — Fix bulk_update_contracts status drift, archive dead legacy scripts
- f7dd6a7 — Fix PC/BC contract corruption from July 13 stale-snapshot incident (data/combined_players.json + data/player_log.json + new scripts/restore_pc_bc_corruption_2026_07.py)

f7dd6a7 is the important one — it's a live-incident data fix for several managers' paid PC/BC contract upgrades that got silently reverted. It needs to go out.

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -5` — check whether origin/main is still at `11ff27c`, or has moved forward (e.g. more standings/daily-update commits from Railway).
3. If origin/main is unchanged: `git push origin main` (plain fast-forward, no force needed).
4. If origin/main has moved forward: do a normal `git merge origin/main` or `git rebase origin/main` — **not** a rebase with `-X ours`/`-X theirs`, and no `git push --force`. If there's a real conflict in `data/combined_players.json` or `data/player_log.json`, resolve it by keeping BOTH the incoming remote changes AND our specific fixes — do not blindly discard either side. If you can't confidently reconcile a conflict, stop and flag Zach rather than guess (this file has already been corrupted once by a stale-copy overwrite — don't repeat that pattern).
5. After pushing, verify: `git log --oneline origin/main -1` matches local HEAD, and confirm `data/combined_players.json` is still valid JSON (`python3 -m json.tool data/combined_players.json > /dev/null`).
6. Spot check one fixed field survived: `python3 -c "import json; p=[x for x in json.load(open('data/combined_players.json')) if str(x.get('upid'))=='7284'][0]; print(p['name'], p['contract_type'])"` — should print `A.J. Ewing Blue Chip Contract`.

## fbp-hub

Local main is 1 commit ahead of the last-known origin/main (2a7b7f2):
- 37c276d — Close sync-data.yml coverage gap for upid_database.json, mlb_team_map.json, transactions_history.json

Same steps: fetch, check if origin/main moved, fast-forward push if not, normal merge/rebase (no force, no blind conflict auto-resolution) if it has.

## After both are pushed

Railway should auto-redeploy fbp-trade-bot from the new main. No further action needed on fbp-hub beyond the push — its own sync-data.yml workflow will pick up the corrected combined_players.json/player_log.json from fbp-trade-bot within 15 minutes (or immediately if repository_dispatch fires).
