# Root-Cause Report — Underlying Fixes
*Companion to TRADE_DATA_ISSUES_2026_07_31.md. That report's issues are now fixed in data/*.json (see "What was applied today" below). This report is about preventing recurrence.*

## What was applied today

Ran `scripts/apply_missing_trades_and_garcia_fix_2026_07_31.py` (dry-run reviewed first, diff-guarded like the existing `restore_*.py` scripts) against the live files in your local clone. **Not yet committed/pushed** — that's the one step left for you, same convention as `apply_manual_trades.py` uses:

```
git add data/ config/season_dates.json
git commit -m "Manual fix: 4 trades + Luis Garcia yahoo_id/bio dedup (2026-07-31)"
git push
```

Applied:
- **Cam Caminiti → HAM**, Bryce Miller leg was already satisfied (he's on LFB via an apparently unrelated separate transaction — no fabricated log entry was written for him, only for Caminiti).
- **McGreevy → B2J**, contract backfilled to Keeper Contract / TC1, NRI flag cleared.
- **Luis García Jr. (8696) → WIZ**, Steele Hall + Kayson Cunningham → WAR.
- **Joe Ryan → SAD**, Landen Roupp + Cooper Pratt → B2J.
- **Luis García yahoo_id dedup**: cleared the duplicate `yahoo_id: "9455"` off upids 2898 and 3767, corrected `team: "LAA"` → `"NYM"` on 2898/2899 (Yahoo's own data confirms NYM), and cleared `mlb_id`/`birth_date`/`mlb_primary_position` off 2899 — those belonged to the real Nationals infielder Luis García Jr., not the Mets reliever who actually owns yahoo_id 9455 (see below). That bio data now lives correctly on UPID 8696 instead, along with the real `mlb_id: 671277` confirmed via public MLB records (Baseball Savant / birth date match).
- `config/season_dates.json`: `trade_deadline` corrected `2026-07-30` → `2026-07-31`.
- Also added 4 backfilled records to `data/trades.json` (`MANUAL-20260731-001..004`) so trade history and the roster-sync trade-ownership guard both recognize these moves.

`git diff` reviewed — 30 insertions/28 deletions in `combined_players.json`, all in the expected 13 records, no incidental re-encoding of unrelated content.

One correction to my first report: I'd assumed UPID 2899 (the WAR-owned "Luis García") was simply the real Angels reliever you roster. It's actually more tangled — see below.

---

## A. Why 3 trades never reached the portal

**Root cause, confirmed:** `config/season_dates.json`'s `trade_deadline` was `"2026-07-30"` — one day before what you've now told me was the intended cutoff (noon Eastern, 7/31). I've corrected the date. But that only gets the system to *day-level* accuracy — `trade_store.load_trade_window_status()` has no concept of time-of-day at all:

```python
end = _parse_date_yyyy_mm_dd(end_s) + timedelta(days=1)
return start <= now < end
```

This always extends the window through 11:59:59 PM UTC-day-end, i.e. 8:00 PM Eastern, regardless of what time you actually want the cutoff to be. As of this fix, it's 12:44 PM ET on 7/31 — already past the "real" noon deadline you described, but the corrected config leaves the portal open until 8 PM ET tonight. I did not change that behavior without checking with you first, since it's a live, real-time-affecting change on the actual deadline day.

**Proposed fix (not yet made — needs your sign-off since it changes live trade-gating behavior today):**
1. Add a time component to `season_dates.json`, e.g. `"trade_deadline": "2026-07-31T12:00:00"`.
2. Update `load_trade_window_status()`'s `in_range()` to parse a full datetime (falling back to end-of-day if only a date is given, for backward compatibility with the other date fields in this file), and compare against `now` directly instead of `now < date+1day`.
3. Say the word and I'll make this change and close the window at noon ET precisely, effective immediately — or leave it at end-of-day for today and only ship the time-of-day support for next time. Your call.

**Secondary, process-level cause:** even a correct deadline doesn't explain why these 3 specific trades have zero record in `trades.json`/`pending_trades.json` while 6 others from the same 48 hours went through cleanly. The most likely explanation is they were negotiated off-portal (Discord/DM) rather than run through the manager-submit → manager-accept → admin-approve flow, possibly *because* the portal was rejecting them near the deadline boundary. Either way, this is the second time this season a trade has needed a bespoke manual-application script (`apply_manual_trades.py` did the same thing back in February for 3 different trades, and `restore_farm_trade_reversion_2026_07.py` for another 5 in July) — this is a recurring operational pattern, not a one-off.

**Proposed fix:** add a lightweight commissioner-only "manual trade entry" path to the admin portal/API — same `teams` + `transfers` shape as `create_trade()`, but skipping the window/acceptance checks and going straight to `approved` + applied (essentially what `admin_approve()` does, minus the manager-acceptance gate). That turns "write a one-off Python script and ask an AI to run it" into a form you fill out yourself in 30 seconds, and it stops silently missing `trades.json`/player_log/history/roster-sync-guard coverage the way today's 3 (and February's 3, and July's 5) did.

---

## B. The Luis García identity mess — deeper than reported first

**What I found on closer inspection.** `data/upid_database.json`'s own entry for UPID 2899 says `"team": "WSH", "pos": "2B,SS"` — that's the real Nationals infielder. But the actual player *record* in `combined_players.json` under UPID 2899 had `team: "LAA"`, `position: "RP"` — a pitcher. Yahoo's own live data (`data/yahoo_player_index.json`) settles it: `yahoo_id 9455` = "Luis García", team **NYM**, position RP — a Mets reliever, a real but different person from the Nationals infielder. So:

- UPID 2899's *identity slot* (per `upid_database.json`) was always meant to track the Nationals star.
- UPID 2899's *actual data row* (per `combined_players.json`) has apparently always held a different real person (a Mets/ex-Angels reliever), and at some point had that pitcher's `mlb_id`/`birth_date`/`mlb_primary_position` overwritten with the Nationals star's real bio data (671277 / 2000-05-16 / 2B) — almost certainly via a name-matching enrichment pass that matched one of 2899's `alt_names` (`upid_database.json` lists "Luis García Jr." as an alt_name on 2899) to the more prominent player without the team-disambiguation guard actually excluding the mismatch that time.
- UPID 2898 (unowned duplicate, already flagged `"approved_dupes": "TRUE"` in `upid_database.json` — someone already knew about a duplicate here) and UPID 3767 (a Tigers farm shortstop, a *third* distinct real person) both also carried the same `yahoo_id: "9455"`.

I checked `data_pipeline/enrich_combined_players_with_bio.py` and `scripts/update_mlb_ids_from_mlb_api.py` specifically — both are reasonably careful (the latter requires an exact, unique, team-checked MLB API match before writing an `mlb_id`). Neither is an obviously sloppy culprit on its own, which tells me this is likely an **older or manual** pass, or a case where the team field feeding the disambiguation check was itself already wrong at the time (e.g., resolved to blank via `mlb_team_map.json`'s alias table, silently disabling the team-match guard for just that one lookup). I don't have git-blame budget left in this session to pin the exact commit; I'm confident in the diagnosis, not in the exact historical culprit script.

**What this means going forward:** name collisions ("Luis Garcia" matches at least 4 real people already in this database: the Mets RP, the Nationals star, a Tigers farm SS, and a HOU pitcher at UPID 2897) are not consistently guarded against **globally** — `backfill_yahoo_ids_from_index.py` only checks for ambiguity *within a single row's own candidate names*, never "is this ID already claimed by a different UPID." `update_mlb_ids_from_mlb_api.py` and `roster_sync.py`'s `_match_yahoo_player` do attempt team disambiguation, but both depend on the `team` field being trustworthy at match time — which, per this exact incident, it wasn't.

**Proposed fixes:**
1. Add a global "is this yahoo_id/mlb_id already used by a *different* UPID" check to `backfill_yahoo_ids_from_index.py` before writing (skip + log instead of overwrite). This is the most surgical, lowest-risk fix and directly prevents a repeat of today's bug.
2. Write a small standalone integrity-checker (could run as part of `data_pipeline/update_all.py`) that flags: (a) any yahoo_id or mlb_id shared by more than one UPID in `combined_players.json`, (b) any UPID where `upid_database.json`'s `team` disagrees with `combined_players.json`'s `team` for the same UPID. Surface these as a Discord digest rather than silently living in the data for months, the way this one did.
3. Lower priority, not blocking: update the real UPID master sheet (Google Sheet, synced into `upid_database.json`) so row 2899 reflects the Mets reliever it now actually represents in `combined_players.json`, and drop "Luis García Jr." from its alt_names now that 8696 owns that identity cleanly. I didn't touch `upid_database.json` directly since it looked like it's meant to be regenerated from the external sheet rather than hand-edited.
4. yahoo_id for UPID 8696 is still blank — recommend running the admin portal's normal enrichment/lookup for him rather than guessing; I deliberately didn't invent one (see report 1 for why that's exactly how this mess started).

---

## C. Trade approval can silently mark itself "approved" without actually moving anyone

Carried over from report 1, still unfixed, still latent (not proven to be the cause of today's specific incidents, but a real risk worth closing while we're in here):

In `trade/trade_store.py`, `admin_approve()`:
```python
rec["status"] = "approved"
...
try:
    _apply_approved_trade_to_data_files(rec, admin_team)
except Exception as exc:
    rec.setdefault("data_applied_summary", {})
    rec["data_applied_summary"]["warnings"] = (...) + [f"Exception: {exc}"]
# status stays "approved" either way
```
And inside `_apply_approved_trade_to_data_files`, `DATA_LOCK.acquire()` has no `try/finally` — an exception between acquiring the lock and the final `DATA_LOCK.release()` (e.g. mid-WizBucks-transfer or mid-buy-in) leaves the lock held forever *and* skips saving `combined_players.json`, while the trade still shows `"approved"`.

**Proposed fix:** wrap the body in `try/finally: DATA_LOCK.release()`; don't set `rec["status"] = "approved"` until `_apply_approved_trade_to_data_files` returns cleanly with zero warnings (or make "approved-with-warnings" a distinct, visibly-flagged status rather than indistinguishable from a clean approval).

---

## D. McGreevy-style graduation gap

McGreevy debuted 2024-07-31 but stayed `player_type: "Farm"` with no contract for a full year, because (based on his `player_log.json` history) he was never *owned* — he just bounced on and off various managers' Yahoo rosters as a speculative pickup. My guess, not yet verified: the graduation sweep (`data_pipeline/graduate_prospects_2025.py` / `scripts/graduate_37_players_2026_07.py`) likely only processes *owned* prospects, so an unowned-but-debuted player can sit in limbo indefinitely with nothing ever flagging him.

**Proposed fix:** worth a quick read of that pipeline's filtering logic to confirm the owned-only scope, and either expand it to sweep all debuted Farm players regardless of ownership, or at minimum add unowned-but-debuted players to a review list so they don't require a trade dispute to surface, the way McGreevy did.

---

## Priority / effort summary

| Fix | Risk if left alone | Effort | Needs your sign-off before I do it? |
|---|---|---|---|
| Time-of-day trade deadline | Portal stays open ~8hrs later than intended today and every future deadline | Small | Yes — changes live trade acceptance today |
| Global yahoo_id/mlb_id uniqueness guard | Next ambiguously-named player repeats today's exact bug | Small | No — safe, additive, no behavior change for correctly-unique players |
| Identity integrity checker (upid_database vs combined_players) | Silent drift like UPID 2899 can recur undetected for months | Medium | No |
| Commissioner manual-trade-entry portal feature | Every off-portal trade keeps requiring a bespoke script + AI session | Medium-large | Recommend discussing scope first |
| trade_store.py exception/DATA_LOCK hardening | A future partial failure can silently mark a trade "approved" with no data moved and no clear signal | Small-medium | No — safe hardening, no behavior change for the success path |
| McGreevy-style graduation gap | Other unowned-but-debuted prospects likely sitting in the same limbo right now | Needs investigation first | No |

Let me know which of these you want built now vs. queued for a WARP handoff doc.
