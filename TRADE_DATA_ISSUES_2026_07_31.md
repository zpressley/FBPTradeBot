# Trade & Player Data Issues — Diagnosis + Fix Plan
*Investigated 2026-07-31. No fixes applied yet — diagnosis only, per Zach's request.*

## TL;DR

| # | Issue | Root cause | Fix needed? |
|---|---|---|---|
| A | McGreevy (CFL→B2J), Luis García Jr.+Hall+Cunningham (WAR↔WIZ), Joe Ryan+Pratt+Roupp (B2J↔SAD) — none of these 8 players switched teams | **These 3 trades were never submitted through the trade portal.** They don't exist anywhere in `data/trades.json` or `data/pending_trades.json`. No portal record = `admin_approve()` never ran = ownership fields never touched. | Yes — manual apply, same pattern as `scripts/apply_manual_trades.py` |
| B | Cam Caminiti (LFB→HAM) still "unresolved" | Not a bug. HAM never accepted the trade; it's now correctly `status: "expired"`. Caminiti correctly still belongs to LFB. | No |
| C | Luis García Jr. won't create / Yahoo sync grabs the LAA reliever instead | Real data bug: **yahoo_id `9455` is duplicated across 3 different real players** (UPIDs 2898, 2899, 3767) in `combined_players.json`. The new Luis García Jr. record (UPID 8696) is also missing its `FBP_Team` key entirely and has no yahoo_id/mlb_id of its own. | Yes — dedupe yahoo_id, backfill UPID 8696's fields, add a uniqueness guard |
| D | McGreevy "no contract/ownership status" | Same root event as row A (trade never applied) layered on top of a pre-existing gap: he's been unowned/contract-less all season and never went through prospect graduation despite debuting in 2024. | Yes — folds into A's fix, plus a graduation-pipeline follow-up |

Also see **"Bonus finds"** at the bottom — one latent bug and two of Zach's own trades sitting in his queue unrelated to this investigation.

---

## Issue A: Three trades never entered the system

**Evidence.** I checked `data/trades.json` for every UPID involved in the three trades from Zach's table (McGreevy=2979, Luis García Jr.=8696, Steele Hall=7951, Kayson Cunningham=7953, Joe Ryan=4016, Landen Roupp=4567, Cooper Pratt=6583). None of them appear in any trade record dated 7/29 or 7/30. `data/pending_trades.json` is `[]`. I did find 6 *other* trades from the same two days (`TRADE-290726_1925-065` through `TRADE-300726_1705-070`) that went through the portal normally with clean `data_applied_summary` blocks — so the portal itself was working that day for other managers. These three specific trades just never became portal records at all.

Cross-checking current live data confirms every player is still sitting with their **pre-trade** owner:

| Player | UPID | Currently owned by | Should be owned by |
|---|---|---|---|
| Michael McGreevy | 2979 | *unowned* (`FBP_Team: ""`, `manager: ""`) | B2J / Btwn2Jackies |
| Luis García Jr. | 8696 | WAR (`manager` set, but `FBP_Team` key is **missing entirely**) | WIZ / Whiz Kids |
| Steele Hall | 7951 | WIZ / Whiz Kids | WAR / Weekend Warriors |
| Kayson Cunningham | 7953 | WIZ / Whiz Kids | WAR / Weekend Warriors |
| Joe Ryan | 4016 | B2J / Btwn2Jackies | SAD / not much of a donkey |
| Landen Roupp* | 4567 | SAD / not much of a donkey | B2J / Btwn2Jackies |
| Cooper Pratt | 6583 | SAD / not much of a donkey | B2J / Btwn2Jackies |

*\*Spelled "Landon" in your message; the DB has him as "Landen Roupp" (`bbref_id: rouppla01`) — same player, upid 4567.*

**Why they likely bypassed the portal:** `config/season_dates.json` sets `"trade_deadline": "2026-07-30"`. The portal's window check (`trade_store.load_trade_window_status`) closes at `trade_deadline + 1 day` at **UTC midnight**, which is 8:00 PM Eastern on 7/30. Several trades did successfully submit through the portal that evening (as late as 5:05 PM ET), so the window wasn't closed all day — but if any of these three were being finalized right at or after that 8 PM ET cutoff, `create_trade()` would have hard-rejected the submission (HTTP 400 "Trade window is closed") with no trade_id ever generated, which matches the total absence of records perfectly. This lines up with your hunch #2. The other plausible/complementary explanation is these were simply negotiated over Discord and reported to you directly rather than run through the manager-submit → manager-accept → admin-approve portal flow — either way, the result and the fix are the same.

**Note on Joe Ryan / the "RP Luis Garcia":** correctly no data issue on either, per your own note — Joe Ryan is an established VC2 keeper (not a prospect), still sitting at B2J because the trade never applied, nothing else wrong with his record.

### Fix plan for Issue A

1. Confirm with the managers/Discord thread that the 3 trades match Zach's table exactly (no additional legs, no WizBucks, no draft picks — none were mentioned).
2. Write a one-off script following the exact pattern already established in `scripts/apply_manual_trades.py` (and `scripts/restore_farm_trade_reversion_2026_07.py`): for each of the 8 player legs, directly set `FBP_Team` + `manager` to the target values in the table above, append a matching `player_log.json` "Trade" entry for each (see `_apply_approved_trade_to_data_files` in `trade/trade_store.py` for the exact log entry shape to mirror), save, commit, push.
3. For Luis García Jr. (UPID 8696) specifically, add the currently-missing `"FBP_Team"` key (see Issue C) as part of this same pass — don't just flip it, it doesn't exist yet.
4. For McGreevy specifically, also set the contract fields (see Issue D).
5. Optionally: backfill each trade into `data/trades.json` as a `status: "approved"` record with `data_applied_by: "MANUAL_FIX"` so history/`list_history()` and the roster-sync trade-owner-lock guard (`_load_trade_owner_lock_map` in `roster_sync.py`) recognize these as recent trades and don't let the next Yahoo sync fight the ownership change.

---

## Issue B: Cam Caminiti — not a bug

`TRADE-250426_2025-024` (LFB→HAM, Caminiti for Bryce Miller): `acceptances: ["LFB"]` only — HAM never accepted. Status is now `"expired"` (`expired_from_status: "pending"`), correctly swept by the hourly `trade_expiry_sweep_tick` job (confirmed running in `health.py`, starts at bot boot, `@tasks.loop(minutes=60)`). Caminiti correctly still belongs to LFB. Nothing to fix here — the trade was simply never a real, mutually-accepted deal, and the system now reflects that correctly.

---

## Issue C: Luis García Jr. — the yahoo_id collision

**The collision.** `yahoo_id "9455"` is currently assigned to three different real people in `data/combined_players.json`:

| UPID | Name | MLB team | player_type | Notes |
|---|---|---|---|---|
| 2899 | Luis García | LAA | MLB | Owned by **WAR** (your team), Keeper Contract, TC1 — this looks like the real record you actually roster |
| 2898 | Luis García | LAA | MLB | Unowned duplicate of the same real person, `mlb_id: null` |
| 3767 | Luis García | DET | Farm | A **different, unrelated real person** (Tigers farm SS) who shouldn't share a yahoo_id with the LAA guys at all |

`data/upid_database.json`'s `name_index` compounds this — `"luis garcia"`, `"luis garcía"`, `"luis garcia jr."`, and `"luis garcía jr."` all resolve to overlapping subsets of `{2897, 2898, 2899, 3767, 8696}` (2897 is yet another distinct real person, a HOU pitcher).

**Why this breaks the Yahoo sync.** `data_pipeline/roster_sync.py`'s `_build_combined_indexes()` builds `by_yahoo_id` as `by_yahoo_id[yahoo_id] = p` while looping the player list — last one in the list wins, no collision check. So whichever of 2898/2899/3767 happens to sit later in the JSON array "wins" the `by_yahoo_id["9455"]` slot, and Yahoo-ID-based matching (`_match_yahoo_player`, priority #1) silently attributes your real roster's Luis García activity to whichever of those three won, not necessarily the correct one (2899). That's the mechanical explanation for "adding the wrong Luis Garcia from LAA."

**Likely origin of the duplicate assignment:** `scripts/backfill_yahoo_ids_from_index.py` only refuses to write a yahoo_id when it finds *more than one* Yahoo candidate for a given row's own name/alt-name set — it has no check for whether that yahoo_id is *already used by a different row*. Run across multiple passes (as alt_names lists changed), it could easily have assigned "9455" independently to more than one row.

**Why the new Luis García Jr. (UPID 8696) is fragile.** The `recover_lost_add_player_20260730.py` script that restored him after the original add-player commit got lost (see below) intentionally left `yahoo_id`, `mlb_id`, `birth_date`, `bats`, `throws`, `age` blank — and it also **never included an `"FBP_Team"` key at all** in its `NEW_PLAYER` template (only `"manager"`). Live data confirms this: UPID 8696 has `manager: "WAR"` but no `FBP_Team` key whatsoever. Since `trade_store.py` and `roster_sync.py` both read ownership via `p.get("FBP_Team")`, this player currently reads as **unowned** for any ownership check even though `manager` says WAR — which would itself block re-submitting his trade through the portal today ("Player Luis García Jr. is owned by UNOWNED, not WAR").

Separately: the original reason his 7/30 add "kept not creating" is already fixed — `api_admin_bulk.py`'s `add_player()` used to fire-and-forget its git commit (`wait=False` default), so a container redeploy could silently wipe an add that had already reported success to Discord. That's now patched (`_enqueue_commit(..., wait: bool = True)`, confirmed at 4 call sites in `api_admin_bulk.py`), and the specific lost add was recovered via `scripts/recover_lost_add_player_20260730.py`. No further action needed on that half.

### Fix plan for Issue C

1. Confirm which of 2898/2899 is the real Angels reliever you actually roster (2899, with the Keeper Contract/TC1/WAR ownership, is almost certainly it) and which is the stray duplicate. Clear `yahoo_id` off the duplicate(s) — 2898 at minimum, and reassign 3767 (the Tigers farm SS, a genuinely different person) its own correct yahoo_id via a real MLB Stats API / Yahoo lookup, not "9455".
2. Look up the real `yahoo_id` and `mlb_id` for Luis García Jr. (WSH, 1B) and set them on UPID 8696.
3. Add the missing `"FBP_Team"` key to UPID 8696 (see Issue A step 3 — set directly to the post-trade value, WIZ, as part of the same fix pass).
4. Set UPID 8696's contract fields to match what your trade table already says he should be: `contract_type: "Keeper Contract"`, `years_simple: "TC 1"`, `status: "[5] TC1"` (mirrors the pattern used for other TC1 rookies like Dylan Crews in `restore_pc_bc_corruption_2026_07.py`).
5. Harden `scripts/backfill_yahoo_ids_from_index.py` (and ideally `_build_combined_indexes` in `roster_sync.py`) to treat a yahoo_id that's already claimed by a *different* UPID as a conflict to flag/skip, not silently overwrite. This is the guard that prevents this exact bug class from recurring for the next ambiguously-named player.

---

## Issue D: McGreevy contract/ownership

`player_log.json` shows McGreevy (UPID 2979) has been unowned all season — a real MLB reliever/spot-starter who's been repeatedly picked up and dropped by various managers on Yahoo waivers (`owner: ""`, contract toggling between `""` and `"Keeper Contract"/TC1` each time someone rosters/drops him), never formally purchased as an FBP asset. He debuted in MLB back on 2024-07-31 but is still `player_type: "Farm"` — he appears to have fallen through prospect graduation, plausibly because that pipeline only processes *owned* prospects and he never was one.

His current blank state (`contract_type: ""`, `status: "[9] P"`, no TC1) doesn't yet reflect the manual TC1 update you mentioned making — worth double-checking whether that edit actually persisted (this codebase has a history of edits/commits silently not landing; see Bonus finds below) or whether it just hasn't happened yet.

### Fix plan for Issue D

1. Apply as part of Issue A's fix pass: `FBP_Team: "B2J"`, `manager: "Btwn2Jackies"`, `contract_type: "Keeper Contract"`, `years_simple: "TC 1"`, `status: "[5] TC1"`.
2. Separately (lower priority, not blocking): take a quick look at the prospect-graduation pipeline (`data_pipeline/graduate_prospects_2025.py` / `scripts/graduate_37_players_2026_07.py`) to see whether it's scoped to owned-only players — if so, unowned prospects who debut can silently sit ungraduated indefinitely, and McGreevy probably isn't the only one.

---

## Bonus finds (not blocking, worth knowing about)

- **Latent bug, not the cause here but worth hardening:** in `trade/trade_store.py`, `admin_approve()` wraps `_apply_approved_trade_to_data_files()` in a try/except that swallows any exception, logs it into `data_applied_summary.warnings`, and still marks the trade `"approved"` regardless. Inside that function, `DATA_LOCK.acquire()` has no matching `try/finally` — if anything throws between acquiring the lock and the final release (e.g. during the WizBucks or draft-pick sections), the lock never releases *and* `combined_players.json` never gets saved, even though the trade shows as approved. I didn't find evidence this caused the current 3 trades (those never even reached this function), but it's a real risk for future ones and would be a cheap, high-value hardening pass: add a `finally: DATA_LOCK.release()`, and don't set `status = "approved"` until the data mutation actually succeeds.
- **Two of your own trades are sitting in your queue**, unrelated to anything above: `TRADE-300726_1607-069` (LFB↔WAR, Contreras/Greene for Langeliers/Contreras) is `"pending"` — WAR hasn't accepted yet. `TRADE-300726_1705-070` (LFB↔WAR, a bigger 7-piece deal) is in `"admin_review"` — both sides accepted, just needs the admin-approve click. Flagging since you're the manager on both.

---

## Files referenced

- `trade/trade_store.py`, `trade/trade_models.py` — trade portal engine
- `data/trades.json`, `data/pending_trades.json` — trade records (the 3 missing trades aren't in either)
- `data/combined_players.json`, `data/upid_database.json`, `data/player_log.json` — player data / identity / history
- `data_pipeline/roster_sync.py` — Yahoo roster sync + player matching (`_match_yahoo_player`, `_build_combined_indexes`)
- `scripts/backfill_yahoo_ids_from_index.py` — likely origin of the yahoo_id 9455 collision
- `scripts/recover_lost_add_player_20260730.py`, `api_admin_bulk.py` — the already-fixed lost-add bug
- `scripts/apply_manual_trades.py`, `scripts/restore_farm_trade_reversion_2026_07.py`, `scripts/restore_pc_bc_corruption_2026_07.py` — precedent/template for manually re-applying trades and field-level corrections
- `config/season_dates.json` — `trade_deadline: "2026-07-30"`
- `data_lock.py` — `DATA_LOCK` (global `threading.RLock`)
