Push pending commits in fbp-trade-bot. These commits already exist locally and are fully tested/verified — this task is ONLY to get them onto origin/main safely. Do not re-run, re-generate, or "fix" anything; do not run any data pipeline, backfill, sync, or graduation scripts. Do not touch token.json.

fbp-hub has nothing pending right now.

## fbp-trade-bot

Local main is 2 commits ahead of the last-known origin/main (`26044a4`):
- `ff06dab` — Fix fire-and-forget commit bug in admin bulk endpoints
- `d26961c` — Recover Luis Garcia Jr. (UPID 8696), lost to the add-player commit bug

**Both real code + data changes, and time-sensitive** — the bug these fix is actively affecting admin actions right now (it just ate a real player-add), so the sooner this is live the sooner it stops happening again.

Context: Zach added a player (Luis Garcia Jr.) through the site's admin portal. The bot posted a Discord confirmation with a UPID, but the player never actually showed up — no git commit for it ever happened. Root cause: `api_admin_bulk.py`'s admin endpoints (add-player, bulk-graduate, bulk-update-contracts, bulk-release) called health.py's `_commit_and_push()` in fire-and-forget mode, same defect class already fixed for trades a week ago. The write hits local disk fine, but the git commit just gets queued and reports success immediately, with no confirmation the push actually happened — if the container redeploys before that queued push lands, the change vanishes even though Discord and the browser both already said it succeeded.

- `api_admin_bulk.py` — `_enqueue_commit()` now defaults to blocking until the push is confirmed and raises on failure/timeout (mirrors `trade/trade_store.py:_maybe_commit`). The four affected endpoints now commit *after* releasing `DATA_LOCK` instead of while holding it (so a slow git push doesn't stall other admin requests), and raise a real HTTP 500 instead of silently continuing to the Discord "success" notification if the commit fails.
- `data/combined_players.json`, `data/upid_database.json`, `data/player_log.json` — recreates Luis Garcia Jr. as UPID 8696 (WSH, 1B), the exact UPID Discord already announced. Bio fields (mlb_id, yahoo_id, birth_date, bats, throws, age) were deliberately left blank rather than guessed — flag this to Zach so he can re-run the admin portal's enrichment lookup once this is live, or fill them in by hand.

Verification already done locally: `python3 -m py_compile` on the changed `.py` files (clean), JSON re-validated as parseable, UPID 8696 confirmed present with the right team/position, and the diff double-checked to touch only the intended ~20 lines in `combined_players.json` (an earlier pass briefly reformatted ~300 unrelated players' name-encoding as a side effect of a Python json.dump quirk — caught and fixed before this commit was finalized, so the diff you'll see is clean).

**Known pre-existing gap, not fixed here (flagged for Zach to decide on scope):** the same fire-and-forget pattern also exists in `api_buyin.py`, `api_manager_players.py`, `api_settings.py`, `api_notes.py`, `api_upid.py`, `draft/board_manager.py`, `draft/draft_manager.py`, and two commit paths in `commands/auction.py` (routine bid placement + weekly phase sync — the auction *resolve/payout* path was already hardened in earlier work). All of them are wired to the same shared `_commit_and_push` and could lose data the same way add-player just did. Not in scope for this push.

Steps:
1. `cd` into fbp-trade-bot, `git fetch origin`.
2. `git log --oneline origin/main -5` — check whether origin/main is still at `26044a4`, or has moved forward (e.g. more standings/daily-update commits from Railway, which happen automatically).
3. If origin/main is unchanged: `git push origin main` (plain fast-forward, no force needed).
4. If origin/main has moved forward: do a normal `git merge origin/main` or `git rebase origin/main` — **not** a rebase with `-X ours`/`-X theirs`, and no `git push --force`. If `data/combined_players.json`, `data/upid_database.json`, or `data/player_log.json` show a real conflict, stop and flag Zach — these are live roster records. `api_admin_bulk.py` conflicting is unlikely (no one else should be editing it right now) but treat the same way if it happens.
5. After pushing, verify: `git log --oneline origin/main -1` matches local HEAD (`d26961c`, unless you had to merge).
6. Spot check after Railway redeploys: `python3 -c "import json; d=json.load(open('data/combined_players.json')); p=[x for x in d if x.get('upid')=='8696']; print(p)"` should show Luis Garcia Jr., WSH, 1B. Then, if you can, actually exercise the add-player form once (or watch Railway logs for the next real admin bulk action) to confirm it now waits for a real commit confirmation before Discord posts — look for `✅ Admin change committed to git` in the logs.

## After pushing

Railway will redeploy fbp-trade-bot from the new main. This one changes runtime behavior (not just docs or a data-only fix) — worth a quick look at the first few log lines after restart to confirm the bot comes up clean.
