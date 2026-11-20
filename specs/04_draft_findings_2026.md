# Draft System: 2026 Findings

Code audit of FBPTradeBot + fbp-hub, done 9/25/2026 to prepare the draft rebuild spec. File references are to the 2026 code.

## Root cause behind most issues

All live draft state (the draft manager, pick clock task, draft channel, confirm buttons) lives in the bot's memory and is set to None on startup (`commands/draft.py:206-233`). Anything that restarts the bot (a deploy, a crash, a git push that triggers a redeploy) stops the draft until an admin runs `/draft start`, which also resets the clock to a full 4 minutes. Every pick, clock start, pause and forklift toggle also queues a git push.

Note: Railway's current watch patterns ignore `data/**`, but root files like `token.json` still trigger redeploys. The watch rule may not have existed during the February 2026 drafts.

## Reported issues

**1. Hard to find players**
- Picks need an exact name (`draft/pick_validator.py:116-118`). Suggestions use difflib at 0.80/0.65, which is case-sensitive, has no accent folding and no last-name matching. "chourio" gets no suggestion for Jackson Chourio.
- The pool is a dict keyed by name (`draft/prospect_database.py:113`). 6 duplicate prospect names (Jose Ramos, José Rodríguez, Luis Baez, ...) mean one of each pair can't be picked.
- Web search is a lowercase substring match on static `combined_players.json` (`fbp-hub/js/draft.js:1720-1726`).
- Four places decide eligibility with four different rules (bot validator, website pool, boards, pool API). 10 prospects currently pass the bot but are hidden on the web.
- `api_draft_pool.py:66-70` crashes (treats the FYPD file as a list). Unused, but still proxied.

**2. Stopped working**
- Restarts wipe state (above). Confirm buttons are `View(timeout=None)` with no persistent registration, so they die on restart.
- The clock task only catches `CancelledError` (`commands/draft.py:651`). A failed Discord send or a race in `make_pick` kills it, so nothing autopicks. The keeper draft has 6 gaps over 4 minutes (longest 665 seconds).
- The commit worker is a daemon thread with fire-and-forget pushes (`health.py:166-205`), so a redeploy can drop the last picks.

**3. Delays from commits**
- The website polls `/api/draft/active` every 5 seconds from disk, so the push isn't the direct delay. The delays come from redeploys, the Pages resync chain (`sync-data.yml` has `cancel-in-progress`, so rapid picks keep cancelling it), and the pool loading once per page from static JSON.
- On a rejected push, the commit worker does `git reset --hard` and restores `data/*.json` under `DATA_LOCK` (`health.py:485-531`), but draft code never takes that lock, so a pick saved during that window can be overwritten.

**4. Timer always off**
- `timer_started_at` is naive `datetime.now()` in server time (UTC on Railway) (`commands/draft.py:595-601`). Discord shows "Autodraft at" in UTC. The API guesses UTC and converts (`health.py:1030-1056`).
- Pause clears the clock, and resume gives a fresh 4 minutes (`:1332`, `:1368`).
- After a restart the website counts down to 0:00 while nothing autopicks.
- The clock starts only after the announcement and DMs are sent, and web picks start it through a non-thread-safe `create_task`, so Discord and web drift apart.
- The website trusts the browser clock and hides skew (`fbp-hub/js/draft.js:546-549`).

**5. Messages that didn't work**
- FastAPI and Discord run on separate event loops. `pick-confirm` calls `_bot_ref.loop.create_task(...)` from the API thread (`api_draft_pick_request.py:298`), which isn't thread-safe. The announcement and next clock can be delayed or never run. The correct helper already exists at `health.py:1229`.
- `get_channel` returning None skips the announcement silently and never starts the next clock.
- The DM confirm `channel.send` (`commands/draft.py:526`) has no try/except. Managers with DMs off get a 403 and no feedback.
- Picks typed while paused, messages under 3 characters, and unmapped users are ignored silently.

**6. Players added mid-draft**
- `/admin/add-player` writes `combined_players.json`, but the draft's player database, validator and board manager are built once at `/draft start` and never reload. The new player shows on the web but the bot says "Player not found."
- New players default to `fypd: False` with no rank, so they're blocked in rounds 1-2 and ranked last.
- Draft side effects save `combined_players.json` without the lock, so a simultaneous pick and add can lose one write.

**7. Player left out during testing**
- Silent filters: `player_type` must equal "Farm", any non-blank `contract_type` excludes, rounds 1-2 need `fypd` (the FYPD build skips bad CSV rows with only a print), the web drops players with no ID, and duplicate names.
- Likely path: `scripts/reset_draft_2026.py` deletes the state file but doesn't roll back `combined_players.json`. Players drafted in a live-mode test keep their team and contract and fall out of the pool. It also nulls every `result` in the order file, including keeper-draft sentinels.
- `TEST_MODE` is a hard-coded constant (`commands/draft.py:220`).
- `/draft reset` rolls back by ID or name, so it can clear the wrong same-named player.

## Other issues found

- **Security:** the Worker proxies `pick-confirm`, `validate-pick` and board POST with the server API key and no manager check. The team comes from the request body, so anyone could pick for whoever is on the clock or rewrite any board.
- `/api/draft/active` builds a new manager on every 5-second poll and can write the state file.
- Three separate board manager instances. A Discord `/add` saves the whole file and overwrites board edits made on the web.
- Only 35 of 3,232 prospects have a `rank`, so autopick from the general pool is close to random after the board runs out.

## 2026 rules as implemented (to preserve)

- Order comes from `draft_order_2026.json` (`current_owner`, traded picks, rounds of different sizes). The keeper draft skips keeper, taxed-out and unbought buy-in slots.
- Prospect draft: 52 picks over 8 rounds. Rounds 1-2 are FYPD-only and give a Blue Chip Contract. Round 3+ is any unowned prospect and gives a Development Contract.
- Keeper draft: 348 slots (29 rounds x 12), 139 draftable. MLB players with no manager. Rounds 1-3 give VC-1, round 4+ gives TC-1.
- Clock 240 seconds, 60-second warning (channel + DM), on-clock DM with the top 10 from the manager's board.
- Autopick: the team's board in order, then the ranked pool, validated; a placeholder pick if nothing is eligible.
- Forklift mode: per-team 10-second clock with board-only autopick, turns itself off when the board runs dry.
- Undo rolls back the pick and its roster changes. Pause and resume. Every pick writes a player log entry and the pick result.
- Boards hold up to 50 players.
